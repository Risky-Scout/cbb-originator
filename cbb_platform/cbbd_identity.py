"""CBBD API identity verification + coverage smoke analysis (offline; reads data/raw/cbbd only).

    .venv/bin/python -m cbb_platform.cbbd_smoke analyze

Writes
    data/qa/cbbd_player_identity_verification.json   machine result (authoritative)
    docs/CBBD_PLAYER_IDENTITY_VERIFICATION.md        rendered ONLY from that JSON

Question: is `/games/players` `players[].athleteId` a PERSISTENT player id, or a per-record id the
way Starter Pack `player_game_stats.athleteId` is (measured, STARTER_PACK_PHYSICAL_AUDIT §6b)?

Classification (`classify`) — evidence, not assumption:
    PERSISTENT_PLAYER_ID      every player seen in >= 2 games keeps exactly one id, no id is shared
                              by two different players, and there is enough evidence (MIN_MULTI_GAME)
    NON_PERSISTENT_RECORD_ID  every multi-game player gets a new id per game (ids unique per record)
    MIXED_UNRESOLVED          anything else, including too little evidence or no data at all
Downstream use of the API id as a canonical anchor additionally requires season-file compatibility
(`anchor_compatible`); until then the CBBD adapter keeps API ids as provider-scoped tokens.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

import duckdb

from cbb_platform import config as C, raw_store

PERSISTENT = "PERSISTENT_PLAYER_ID"
NON_PERSISTENT = "NON_PERSISTENT_RECORD_ID"
UNRESOLVED = "MIXED_UNRESOLVED"
MIN_MULTI_GAME = 10          # multi-game players needed before any classification other than UNRESOLVED
MIN_ANCHOR_AGREEMENT = 0.99  # share of name-resolved rows whose API id equals the season-file anchor id

RESULT_JSON = C.QA / "cbbd_player_identity_verification.json"
RESULT_MD = C.PROJECT_ROOT / "docs" / "CBBD_PLAYER_IDENTITY_VERIFICATION.md"
SMOKE_MD = C.PROJECT_ROOT / "docs" / "CBBD_LIVE_SMOKE_RESULTS.md"


# ----------------------------------------------------------------------------- pure logic (unit tested)
def player_rows(box_payload: list[dict]) -> list[dict]:
    """Flatten a /games/players payload to one row per (game, team, player line)."""
    out = []
    for g in box_payload:
        for p in g.get("players") or []:
            out.append({"game_id": g["gameId"], "season": g["season"], "team_id": g["teamId"],
                        "name": p.get("name"), "athlete_id": p.get("athleteId"),
                        "athlete_source_id": p.get("athleteSourceId"), "minutes": p.get("minutes")})
    return out


def _stability(rows: list[dict], id_field: str, key_fields: tuple) -> dict:
    ids_by_player, names_by_id, games_by_player = defaultdict(set), defaultdict(set), defaultdict(set)
    for r in rows:
        if r.get(id_field) is None or not r.get("name"):
            continue
        key = tuple(r[k] for k in key_fields)
        ids_by_player[key].add(r[id_field])
        names_by_id[r[id_field]].add(r["name"])
        games_by_player[key].add(r["game_id"])
    multi = [k for k, g in games_by_player.items() if len(g) >= 2]
    stable = [k for k in multi if len(ids_by_player[k]) == 1]
    per_game = [k for k in multi if len(ids_by_player[k]) == len(games_by_player[k])]
    return {"players": len(ids_by_player), "multi_game_players": len(multi),
            "multi_game_players_single_id": len(stable), "multi_game_players_new_id_every_game": len(per_game),
            # one id attached to two different player names = id reuse (the same player in two seasons is not)
            "ids_shared_by_different_players": sum(1 for v in names_by_id.values() if len(v) > 1),
            "examples_unstable": [{"player": list(k), "ids": sorted(map(str, ids_by_player[k]))}
                                  for k in multi if len(ids_by_player[k]) > 1][:5]}


def classify(rows: list[dict], id_field: str = "athlete_id") -> tuple[str, dict]:
    """Within-season stability (team, season, name) plus cross-season stability (team, name)."""
    within = _stability(rows, id_field, ("team_id", "season", "name"))
    across = _stability(rows, id_field, ("team_id", "name"))
    seasons = defaultdict(set)
    for r in rows:
        seasons[(r["team_id"], r["name"])].add(r["season"])
    across["players_seen_in_2plus_seasons"] = sum(1 for s in seasons.values() if len(s) >= 2)
    ev = {"within_season": within, "across_seasons": across, "multi_team_contexts": multi_team_contexts(rows, id_field)}
    m = within["multi_game_players"]
    if m < MIN_MULTI_GAME:
        ev["reason"] = f"insufficient evidence: {m} multi-game players < {MIN_MULTI_GAME}"
        return UNRESOLVED, ev
    if within["multi_game_players_single_id"] == m and within["ids_shared_by_different_players"] == 0 \
            and across["multi_game_players_single_id"] == across["multi_game_players"] \
            and ev["multi_team_contexts"]["names_with_different_ids"] == 0:
        ev["reason"] = ("every multi-game player keeps one id within and across seasons and across team "
                        "contexts; no id reuse")
        return PERSISTENT, ev
    if within["multi_game_players_new_id_every_game"] == m:
        ev["reason"] = "every multi-game player receives a new id in every game"
        return NON_PERSISTENT, ev
    ev["reason"] = "ids are neither consistently stable nor consistently per-record"
    return UNRESOLVED, ev


def multi_team_contexts(rows: list[dict], id_field: str = "athlete_id") -> dict:
    """Same exact name seen under two or more teams (transfers, or a name collision): one id or several?"""
    teams, ids = defaultdict(set), defaultdict(set)
    for r in rows:
        if r.get(id_field) is not None and r.get("name"):
            teams[r["name"]].add((r["team_id"], r["season"]))
            ids[r["name"]].add(r[id_field])
    multi = sorted(n for n, t in teams.items() if len({tid for tid, _ in t}) >= 2)
    return {"names_in_2plus_teams": len(multi), "names_with_one_id": sum(len(ids[n]) == 1 for n in multi),
            "names_with_different_ids": sum(len(ids[n]) > 1 for n in multi),
            "detail": [{"name": n, "team_seasons": sorted(map(list, teams[n])), "ids": sorted(map(str, ids[n]))}
                       for n in multi][:20]}


def roster_agreement(rows: list[dict], rosters: list[dict]) -> dict:
    """Do box-score athleteIds equal /teams/roster player ids for the same (team, season, name)?"""
    roster_ids = {(t["teamId"], int(t["season"]), p["name"]): p["id"] for t in rosters for p in t.get("players") or []}
    keyed = {(r["team_id"], r["season"], r["name"]): r["athlete_id"] for r in rows}
    both = [k for k in keyed if k in roster_ids]
    in_scope = [k for k in keyed if (k[0], k[1]) in {(t["teamId"], int(t["season"])) for t in rosters}]
    return {"box_players": len(keyed), "box_players_on_rostered_team_seasons": len(in_scope),
            "matched_by_name_on_roster": len(both),
            "same_id_as_roster": sum(1 for k in both if keyed[k] == roster_ids[k]),
            "agreement": round(sum(1 for k in both if keyed[k] == roster_ids[k]) / len(both), 4) if both else None,
            "mismatches": [{"player": list(k), "box_id": keyed[k], "roster_id": roster_ids[k]}
                           for k in both if keyed[k] != roster_ids[k]],
            "box_names_not_on_roster": [list(k) for k in in_scope if k not in roster_ids]}


def anchor_agreement(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> dict:
    """Compare API athleteId with the Starter Pack persistent season-file id for the same
    (season, team, exact name). Uses canonical tables only (cid() of the season-file id)."""
    if not rows:
        return {"name_resolved_rows": 0, "api_id_equals_season_file_id": 0, "agreement": None}
    con.execute("CREATE OR REPLACE TEMP TABLE api_rows (season INTEGER, team_id VARCHAR, name VARCHAR, athlete_id VARCHAR)")
    con.executemany("INSERT INTO api_rows VALUES (?,?,?,?)",
                    [(r["season"], str(r["team_id"]), r["name"], str(r["athlete_id"])) for r in rows])
    detail = con.execute("""
        WITH a AS (SELECT DISTINCT * FROM api_rows),
        sf AS (SELECT season, canonical_team_id, source_player_name, count(DISTINCT canonical_player_id) n,
                      min(canonical_player_id) pid FROM fact.player_season_stats GROUP BY 1, 2, 3)
        SELECT a.season, a.team_id, a.name, a.athlete_id, sf.n, sf.pid = cid('P', 'athlete', a.athlete_id),
               p.source_player_name AS name_of_api_id_in_season_files
        FROM a LEFT JOIN sf ON sf.season = a.season AND sf.canonical_team_id = cid('T', 'team', a.team_id)
                           AND sf.source_player_name = a.name
        LEFT JOIN (SELECT canonical_player_id, arg_max(source_player_name, season) source_player_name
                   FROM fact.player_season_stats GROUP BY 1) p ON p.canonical_player_id = cid('P', 'athlete', a.athlete_id)
        ORDER BY ALL""").fetchall()
    resolved = [d for d in detail if d[4] == 1]
    same = sum(1 for d in resolved if d[5])

    def cause(d):
        if d[4] is None:
            return ("API_ID_IS_A_SEASON_FILE_ID_UNDER_A_DIFFERENT_NAME" if d[6] else
                    "NO_SEASON_FILE_ROW_FOR_THIS_NAME_AND_API_ID_UNKNOWN")
        if d[4] > 1:
            return "AMBIGUOUS_NAME_IN_SEASON_FILE"
        return None if d[5] else "ID_DIFFERS_FROM_SEASON_FILE"
    mism = [{"season": d[0], "team_id": d[1], "name": d[2], "api_id": d[3], "season_file_name_for_api_id": d[6],
             "cause": cause(d)} for d in detail if cause(d)]
    return {"distinct_api_players": len(detail), "name_resolved_rows": len(resolved), "api_id_equals_season_file_id": same,
            "agreement": round(same / len(resolved), 4) if resolved else None,
            "api_ids_known_as_persistent_season_file_ids": sum(1 for d in detail if d[6]),
            "mismatches": mism, "mismatch_causes": dict(Counter(m["cause"] for m in mism))}


def anchor_compatible(result: dict) -> bool:
    """True only when the API id may be used directly as the canonical player anchor."""
    a = result.get("season_file_compatibility") or {}
    return (result.get("classification") == PERSISTENT and (a.get("agreement") or 0) >= MIN_ANCHOR_AGREEMENT
            and (a.get("name_resolved_rows") or 0) >= MIN_MULTI_GAME)


def verified_anchor_compatible() -> bool:
    """Gate read by the CBBD adapter before it maps API athlete ids onto canonical player ids."""
    try:
        return anchor_compatible(json.loads(RESULT_JSON.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


# ----------------------------------------------------------------------------- coverage smoke
def _share(num, den):
    return {"n": num, "of": den, "share": round(num / den, 4) if den else None}


def coverage(payloads: dict, con: duckdb.DuckDBPyConnection | None) -> dict:
    games = [g for p in payloads.get("games", []) for g in p]
    boxes = [g for p in payloads.get("games/players", []) for g in p]
    teams = [t for p in payloads.get("games/teams", []) for t in p]
    gids = {g["id"] for g in games}
    out = {"games_returned": len(games), "game_ids": sorted(gids),
           "seasons": sorted({g["season"] for g in games}),
           "player_box_coverage": _share(len(gids & {b["gameId"] for b in boxes if b.get("players")}), len(gids)),
           "team_box_coverage": _share(sum(1 for gid in gids if sum(t["gameId"] == gid for t in teams) == 2), len(gids))}

    # Score reconciliation: /games final score vs Starter Pack canonical game and vs /games/teams points.
    score = {"vs_starter_pack": [0, 0], "team_box_vs_games": [0, 0]}
    for g in games:
        if con is not None:
            row = con.execute("""SELECT home_points, away_points FROM core.games JOIN metadata.game_id_map USING (canonical_game_id)
                                 WHERE provider = 'CBBD' AND provider_game_key = ?""", [str(g["id"])]).fetchone()
            if row:
                score["vs_starter_pack"][1] += 1
                score["vs_starter_pack"][0] += row == (g.get("homePoints"), g.get("awayPoints"))
        for t in teams:
            if t["gameId"] == g["id"]:
                pts = ((t.get("teamStats") or {}).get("points") or {}).get("total")
                expected = g.get("homePoints") if t.get("isHome") else g.get("awayPoints")
                score["team_box_vs_games"][1] += 1
                score["team_box_vs_games"][0] += pts == expected
    out["score_reconciliation"] = {k: _share(*v) for k, v in score.items()}

    # Minutes: Σ player minutes per team-game vs 5 x gameMinutes (whole-minute rounding tolerance 5).
    mins = [abs(sum(p.get("minutes") or 0 for p in b["players"]) - 5 * b["gameMinutes"]) <= 5
            for b in boxes if b.get("players") and b.get("gameMinutes")]
    out["minutes_reconciliation"] = _share(sum(mins), len(mins))

    events = {}
    for gid, plays in payloads.get("plays/game", {}).items():
        shooting = [p for p in plays if p.get("shootingPlay")]
        on = [p for p in plays if p.get("onFloor")]
        events[gid] = {
            "plays": len(plays),
            "onFloor_present": _share(len(on), len(plays)),
            "onFloor_complete_10": _share(sum(1 for p in on if len(p["onFloor"]) == 10), len(plays)),
            "shotInfo_on_shooting_plays": _share(sum(1 for p in shooting if p.get("shotInfo")), len(shooting)),
            "shotInfo_with_location": _share(sum(1 for p in shooting if ((p.get("shotInfo") or {}).get("location") or {}).get("x")
                                                 is not None), len(shooting)),
            "wallclock_present": _share(sum(1 for p in plays if p.get("wallclock")), len(plays)),
            "substitutions": len(payloads.get("substitutions/game", {}).get(gid, [])),
            "lineups": len(payloads.get("lineups/game", {}).get(gid, [])),
        }
    out["event_games"] = events
    rosters = [t for p in payloads.get("teams/roster", []) for t in p]
    out["rosters"] = [{"team": t.get("team"), "season": t.get("season"), "players": len(t.get("players") or [])}
                      for t in rosters]
    return out


# ----------------------------------------------------------------------------- driver
def load_payloads(root=None, with_params: bool = False):
    """{endpoint: [body, ...]} (per-game endpoints: {gameId: body}); optionally also {endpoint: [params, ...]}."""
    out: dict = defaultdict(list)
    params: dict = defaultdict(list)
    for path, meta in raw_store.records("cbbd", root=root):
        if "request" in meta:      # Stage 2 backfill payloads: not part of the Stage 1 smoke analysis
            continue
        body = json.loads(raw_store.read_payload(path))
        ep = meta["endpoint"]
        if ep in ("plays/game", "substitutions/game", "lineups/game"):
            out.setdefault(ep, {})
            out[ep][meta["params"]["gameId"]] = body
        else:
            out[ep].append(body)
            params[ep].append(meta["params"])
    for ep in ("plays/game", "substitutions/game", "lineups/game"):
        if isinstance(out.get(ep), list):
            out[ep] = {}
    return (dict(out), dict(params)) if with_params else dict(out)


def analyze(con: duckdb.DuckDBPyConnection | None, root=None, blocked_reason: str | None = None) -> dict:
    payloads, params = load_payloads(root, with_params=True)
    rows = [r for p in payloads.get("games/players", []) for r in player_rows(p)]
    result = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "field": "/games/players players[].athleteId", "raw_payload_count": sum(
                  len(v) for v in payloads.values()), "player_rows": len(rows)}
    if not rows:
        result.update({"status": "BLOCKED_CREDENTIAL" if blocked_reason else "NO_DATA",
                       "blocked_reason": blocked_reason, "classification": UNRESOLVED,
                       "evidence": {"reason": "no live /games/players payload has been received"},
                       "athleteSourceId": None, "roster_agreement": None, "season_file_compatibility": None,
                       "coverage": "NOT_MEASURED"})
    else:
        cls, ev = classify(rows)
        result.update({"status": "MEASURED", "classification": cls, "evidence": ev,
                       "athleteSourceId": dict(zip(("classification", "evidence"), classify(rows, "athlete_source_id"))),
                       "roster_agreement": roster_agreement(rows, [t for p in payloads.get("teams/roster", []) for t in p]),
                       "season_file_compatibility": anchor_agreement(con, rows) if con is not None else None,
                       "coverage": coverage(payloads, con)})
        if con is not None:
            from cbb_platform import cbbd_validate
            result["smoke"] = cbbd_validate.measure(payloads, con)
            result["date_bounded_pit"] = cbbd_validate.date_bounded_pit(payloads, params, con)
    result["anchor_compatible"] = anchor_compatible(result)
    result["mapping_implication"] = (
        "API athleteId may anchor canonical players directly (provider 'CBBD')." if result["anchor_compatible"] else
        "API athleteId must NOT be used as a canonical player anchor. CBBD event/box rows keep provider-scoped "
        "'CBBD_ATHLETE:<id>' tokens; Starter Pack (season, team, exact name) mapping remains the only player join.")
    return result


def render_md(r: dict) -> str:
    lines = ["# CBBD player identity verification", "",
             "Rendered from `data/qa/cbbd_player_identity_verification.json` by `cbb_platform/cbbd_identity.py`. "
             "Do not edit by hand.", "",
             f"- **Field tested:** `{r['field']}`",
             f"- **Status:** `{r['status']}`",
             f"- **Classification:** `{r['classification']}`",
             f"- **Anchor-compatible with Starter Pack season-file ids:** `{r['anchor_compatible']}`",
             f"- **Raw CBBD payloads analysed:** {r['raw_payload_count']} · player rows: {r['player_rows']}",
             f"- **Generated:** {r['generated_at_utc']}", ""]
    if r.get("blocked_reason"):
        lines += ["## Why this is blocked", "", r["blocked_reason"], ""]
    if r.get("status") == "MEASURED":
        w, a, mt = r["evidence"]["within_season"], r["evidence"]["across_seasons"], r["evidence"]["multi_team_contexts"]
        ra, sf = r["roster_agreement"] or {}, r["season_file_compatibility"] or {}
        lines += ["## Summary (SMOKE SAMPLE)", "",
                  f"- Sample: {r['player_rows']} player-game lines, {w['players']} distinct (team, season, player) keys.",
                  f"- Cross-game persistence: {w['multi_game_players_single_id']}/{w['multi_game_players']} multi-game players keep "
                  f"one id within a season; {a['multi_game_players_single_id']}/{a['multi_game_players']} keep it across seasons "
                  f"({a['players_seen_in_2plus_seasons']} players seen in 2 seasons). Ids shared by different players: "
                  f"{w['ids_shared_by_different_players']}.",
                  f"- Multi-team contexts (transfers): {mt['names_with_one_id']}/{mt['names_in_2plus_teams']} keep one id "
                  f"({', '.join(d['name'] for d in mt['detail'])}).",
                  f"- Roster agreement: {ra.get('same_id_as_roster')}/{ra.get('matched_by_name_on_roster')} box ids equal "
                  "`/teams/roster` ids.",
                  f"- Starter Pack persistent-id agreement: {sf.get('api_id_equals_season_file_id')}/{sf.get('name_resolved_rows')} "
                  f"name-resolved players ({sf.get('agreement')}); {sf.get('api_ids_known_as_persistent_season_file_ids')}/"
                  f"{sf.get('distinct_api_players')} API ids are known Starter Pack season-file ids.",
                  f"- Mismatches and causes: {sf.get('mismatch_causes')}: "
                  + "; ".join(f"{m['name']} (API) vs {m['season_file_name_for_api_id']} (season file), same id {m['api_id']}"
                              for m in sf.get("mismatches", [])), ""]
    lines += ["## Mapping implication", "", r["mapping_implication"], "",
              "## Decision rule", "",
              f"- `{PERSISTENT}`: every player seen in at least 2 games keeps one id within and across seasons, and no id "
              f"is shared by two players. Requires at least {MIN_MULTI_GAME} multi-game players.",
              f"- `{NON_PERSISTENT}`: every multi-game player receives a new id in every game.",
              f"- `{UNRESOLVED}`: anything else, too little evidence, or no data. **Stage 2 must not proceed on this value.**",
              f"- Direct anchoring also needs at least {MIN_ANCHOR_AGREEMENT:.0%} agreement between the API id and the "
              "Starter Pack season-file id for the same (season, team, exact name).", "",
              "## Evidence", "", "```json", json.dumps({k: r.get(k) for k in (
                  "evidence", "athleteSourceId", "roster_agreement", "season_file_compatibility")}, indent=1, default=str),
              "```", "", "The SMOKE SAMPLE coverage measurements and the date-bounded PIT experiment are rendered in "
              "`docs/CBBD_LIVE_SMOKE_RESULTS.md`.", ""]
    return "\n".join(lines)


def render_smoke_md(r: dict) -> str:
    lines = ["# CBBD live smoke results (SMOKE SAMPLE)", "",
             "Rendered from `data/qa/cbbd_player_identity_verification.json` (keys `smoke`, `date_bounded_pit`) by "
             "`cbb_platform/cbbd_identity.py`. Do not edit by hand.", "",
             "**Every number below describes the bounded smoke sample only** (see `cbb_platform/cbbd_smoke.py`). "
             "None of them may be generalized to whole seasons.", ""]
    for key, title in (("game_data", "Game data"), ("player_box", "Player box"), ("team_box", "Team box"),
                       ("pbp", "PBP (per game)"), ("onfloor", "onFloor (per game)"), ("shotinfo", "shotInfo (per game)"),
                       ("substitutions", "Substitutions (per game)"), ("lineups", "Lineups (per game)"), ("rosters", "Rosters")):
        lines += [f"## {title}", "", "```json", json.dumps((r.get("smoke") or {}).get(key), indent=1, default=str), "```", ""]
    pit = r.get("date_bounded_pit") or {}
    lines += ["## Date-bounded PIT experiment", "", f"**CBBD_DATE_BOUNDED_PIT = `{pit.get('classification')}`**", "",
              "```json", json.dumps(pit, indent=1, default=str), "```", ""]
    return "\n".join(lines)


def main(blocked_reason: str | None = None) -> dict:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS}) if C.DUCKDB_PATH.exists() else None
    if con is not None:
        con.execute("SET TimeZone = 'UTC'")      # cid() is persisted in the database by the build
    try:
        result = analyze(con, blocked_reason=blocked_reason)
    finally:
        if con is not None:
            con.close()
    RESULT_JSON.write_text(json.dumps(result, indent=1, default=str))
    RESULT_MD.write_text(render_md(result))
    if "smoke" in result:
        SMOKE_MD.write_text(render_smoke_md(result))
    print(json.dumps({k: result[k] for k in ("status", "classification", "anchor_compatible")}))
    return result
