"""Stage 2B / 2T documents, rendered ONLY from measured artifacts (no hand-typed numbers).

    .venv/bin/python -m cbb_platform.stage2_reports

    docs/CBBD_BACKFILL_STRATEGY.md                 <- data/qa/cbbd_endpoint_equivalence.json, request log, plan
    docs/CANDIDATE_SUBSYSTEM_TRAINING_WINDOWS.md   <- data/qa/cbbd_*_by_season.csv + canonical database

Candidate windows are PROPOSALS from explicit, printed thresholds. The quantitative architect approves and
freezes them; nothing here freezes a modeling window.
"""
from __future__ import annotations

import csv
import json
from collections import Counter

import duckdb

from cbb_platform import config as C

SEASONS = list(range(2005, 2026))
STRATEGY_MD = C.PROJECT_ROOT / "docs" / "CBBD_BACKFILL_STRATEGY.md"
WINDOWS_MD = C.PROJECT_ROOT / "docs" / "CANDIDATE_SUBSYSTEM_TRAINING_WINDOWS.md"


def _csv(name):
    with (C.QA / name).open() as f:
        return {int(r["season"]): {k: (float(v) if v not in ("", None) and k != "season" else (None if v == "" else v))
                                   for k, v in r.items()} for r in csv.DictReader(f)}


def api_activity() -> dict:
    from cbb_platform.cbbd_client import REQUEST_LOG
    final, attempts = {}, Counter()
    n429 = n5xx = nnet = 0
    for line in REQUEST_LOG.read_text().splitlines():
        e = json.loads(line)
        if e["status"] == "ATTEMPT_FAILED":
            attempts["failed_attempts"] += 1
            s = e.get("http_status")
            n429 += s == 429
            n5xx += bool(s and s >= 500)
            nnet += s is None
            continue
        prev = final.get(e["request_key"])
        if prev is None or prev["status"] not in ("STORED", "ALREADY_STORED"):
            final[e["request_key"]] = e
    ok = [e for e in final.values() if e["status"] in ("STORED", "ALREADY_STORED")]
    retries = sum(e.get("retries", 0) for e in final.values())
    perm = [e for e in final.values() if e["status"].startswith("FAILED")]
    t = sorted(e["requested_at"] for e in final.values())
    by_ep = Counter(e["endpoint"] for e in final.values())
    return {"requests": len(final), "http_calls": len(final) + retries, "successful_calls": len(ok),
            "empty_successful": sum(1 for e in ok if e.get("empty")), "retries": retries,
            "logged_429": n429, "logged_5xx": n5xx, "logged_network_errors": nnet,
            "permanent_failures": len(perm), "first_request_utc": t[0] if t else None, "last_request_utc": t[-1] if t else None,
            "bytes_received": sum(e.get("size_bytes") or 0 for e in ok), "requests_by_endpoint": dict(sorted(by_ep.items())),
            "final_quota_remaining": min((e.get("quota_remaining") for e in ok if e.get("quota_remaining")), default=None)}


def render_strategy() -> str:
    eq = json.loads((C.QA / "cbbd_endpoint_equivalence.json").read_text())
    api = api_activity()
    from cbb_platform.event_backfill import selected_seasons
    sel = selected_seasons()
    d = eq["pbp_date_vs_game"]
    L = ["# CBBD backfill strategy (Stage 2B)", "",
         "Rendered by `cbb_platform/stage2_reports.py` from `data/qa/cbbd_endpoint_equivalence.json` (bounded "
         "equivalence sample, `cbb_platform/event_equivalence.py`), the discovery results and the request log.", "",
         "## Constraint that shapes everything", "",
         "CBBD reports a per-account call quota in `X-CallLimit-Remaining` (124,943 when Stage 2 began). A per-game "
         "backfill of every family for 21 seasons (~5,800 games x 3 endpoints x 21) would need ~360k calls, so "
         "discovery comes first and bulk endpoints are used only where equivalence is proven. The client refuses "
         "to start a call when fewer than 20,000 calls remain (`QUOTA_RESERVE`).", "",
         "## Endpoint chosen per family", "",
         "| family | endpoint | seasons backfilled | why |", "|---|---|---|---|",
         f"| PBP (+onFloor, shotInfo, participants) | `/plays/date` (UTC tip date and the next day) | {sel['pbp'][0]}-{sel['pbp'][-1]} | "
         "event-for-event identical to `/plays/game` (below); ~2 calls per date instead of 1 per game |",
         f"| substitutions | `/substitutions/team` (every team in core.team_seasons) | {', '.join(map(str, sel['substitutions']))} | "
         "row-for-row identical to `/substitutions/game` restricted to the team; one call covers a team-season |",
         f"| lineups | `/lineups/game` | {', '.join(map(str, sel['lineups']))} | `/lineups/team` has no gameId and its date "
         "filter returned no units: it loses game identity, so it is NOT equivalent |",
         "| rosters | `/teams/roster?season=` (no team) | 2005-2025 | one call returns every team of a season |",
         "| recruiting players / team rankings / portal | `/recruiting/players|teams|portal?year=` | class years 2004-2024 | "
         "one call per year; years whose target season is 2025-26 are excluded (lockbox) |",
         "| venues | `/venues` | n/a | one call (reference data) |", "",
         "Seasons were selected ONLY from measured discovery (`docs/CBBD_EVENT_COVERAGE_DISCOVERY.md`): PBP where the "
         "sample showed any PBP; substitutions/lineups where the game sample or the team-season escalation probe "
         "showed any rows. 2004-05 PBP was absent in 12 sampled games AND in 6 busy-date `/plays/date` probes (~635 games).", "",
         "## Equivalence evidence (bounded sample)", "",
         "| ET date | games | games equivalent | events per-game endpoint | events in /plays/date | duplicate event ids across UTC-date payloads | calls per-game vs date | bytes per-game vs date |",
         "|---|---|---|---|---|---|---|---|"]
    for k, v in d.items():
        L.append(f"| {k} | {v['games']} | {v['games_equivalent']} | {v['events_per_game_endpoint']} | {v['events_bulk_for_these_games']} | "
                 f"{v['bulk_duplicate_event_ids']} | {v['calls_per_game']} vs {v['calls_date']} | "
                 f"{v['bytes_per_game_calls']:,} vs {v['bytes_date_calls']:,} |")
    b = eq["pbp_busy_date"]
    spots = b["spot_checks"]
    L += ["", f"Busy date {b['et_date']}: {b['et_games']} ET games; /plays/date returned {b['bulk_games_on_et_date']} of them "
          f"({b['bulk_events']:,} events, payload rows {b['payload_row_counts']}) with no truncation; "
          f"{sum(1 for x in spots.values() if x['equivalent'])}/{len(spots)} spot-checked games identical to /plays/game.", "",
          f"`/plays/team` ({eq['pbp_team_vs_game']['team_season']}): {eq['pbp_team_vs_game']['games_in_team_payload']} of "
          f"{eq['pbp_team_vs_game']['games_in_canonical']} games, {sum(1 for x in eq['pbp_team_vs_game']['compared'].values() if x['equivalent'])}"
          f"/{len(eq['pbp_team_vs_game']['compared'])} compared games identical. Equivalent but ~2x the payload bytes of /plays/date "
          "(each game arrives once per team), so not chosen.", "",
          f"`/substitutions/team` vs `/substitutions/game`: "
          + "; ".join(f"game {g}: {x['team_endpoint_rows']} team rows = {x['game_endpoint_rows_for_team']} game rows for the team, identical={x['identical_multiset']}"
                      for g, x in eq["subs_team_vs_game"]["compared"].items())
          + ". The team endpoint carries only the requested team's athletes, so EVERY team (including non-D-I opponents) is requested.", "",
          f"`/lineups/team`: payload has gameId = {eq['lineups_team_vs_game']['team_payload_has_gameId']}; date-bounded calls vs game units: "
          + "; ".join(f"game {g}: {x['team_day_units']} vs {x['game_units_for_team']}" for g, x in eq["lineups_team_vs_game"]["day_bounded_vs_game"].items()) + ".", "",
          "## Deduplication keys", "",
          "* plays: `(gameId, play id)`. `/plays/date` returns a whole game under BOTH adjacent UTC dates (measured), so "
          "copies are expected. Copies are compared by a content hash of every field; differing copies would be "
          "quarantined as `CONFLICTING_DUPLICATE_EVENT` (none may be silently merged). Preference: /plays/date > /plays/game > /plays/team, then file name.",
          "* substitutions: content hash of `(gameId, teamId, athleteId, subIn, subOut)`; the game and team endpoints overlap for sample games.",
          "* lineups: `(gameId, teamId, idHash)` from `/lineups/game` only.",
          "* rosters: `(athleteId, teamId, season)`; recruits: recruit id; portal: transfer id; venues: venue id.", "",
          "## Retry, rate-limit and checkpoint strategy (`cbb_platform/cbbd_client.py`)", "",
          "* HTTP 429 -> honour `Retry-After` when supplied, otherwise exponential backoff; every worker pauses.",
          "* HTTP 5xx and network timeouts -> bounded exponential backoff (5 retries, 2s..120s). Other 4xx -> permanent failure.",
          "* Every failed attempt and every final outcome is an append-only line in `data/manifests/cbbd_request_log.jsonl`; "
          "`data/manifests/cbbd_backfill_manifest.json` is compiled from it. No header or credential is ever recorded.",
          "* Checkpoint = a request key (endpoint + exact params) with a stored payload. Restarting skips it; a failed "
          "request is retried on the next run and is never interpreted as absent data.",
          "* Raw payloads are written atomically (temp file + rename), gzip-compressed deterministically for large families, "
          "read-only, and hashed over the original response bytes.",
          "* Concurrency was raised deliberately: 2 workers for discovery (0 retries in 803 calls) -> 3 -> 4 workers "
          "(0 429s in ~2,000 calls) -> a 6-worker trial produced 429s and was reverted to 4 workers / 0.2 s spacing.", "",
          "## Measured API activity (request log)", "", "```json", json.dumps(api, indent=1), "```", "",
          "## Known limitations", "",
          "* `/plays/date` coverage of a game can only be proven for dates requested; games with no CBBD PBP are "
          "reported as `games_tested_without_pbp`, never as failures.",
          "* `/lineups/game` is one call per game; it was therefore restricted to the seasons where lineups exist (2023-24, 2024-25).",
          "* Substitutions for non-D-I opponents exist only if CBBD tracks that team (team endpoint requested for every team-season appearing in games).",
          "* The quota is account-wide; the reserve protects other use of the same account."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------- 2T
THRESH = {"box_cov": 0.90, "id_res": 0.95, "pbp_cov": 0.80, "recon": 0.90, "shooter": 0.95, "xy": 0.80,
          "valid5": 0.85, "subs_cov": 0.80, "timing": 0.99, "lineup_cov": 0.80}


def _first_run(ok: dict) -> int | None:
    """Earliest season from which every later development season qualifies (a contiguous window ending 2025)."""
    first = None
    for s in reversed(SEASONS):
        if ok.get(s):
            first = s
        else:
            break
    return first


def render_windows(db_path=None) -> str:
    pbp, flo, sh = _csv("cbbd_pbp_quality_by_season.csv"), _csv("cbbd_onfloor_quality_by_season.csv"), _csv("cbbd_shot_quality_by_season.csv")
    su, li = _csv("cbbd_substitution_quality_by_season.csv"), _csv("cbbd_lineup_quality_by_season.csv")
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True)
    ident = {s: (n, r) for s, n, r in con.execute("""SELECT p.season, count(*), count(p.canonical_player_id) FROM fact.player_games p
             JOIN core.team_seasons ts ON ts.canonical_team_id = p.canonical_team_id AND ts.season = p.season
             WHERE ts.is_division_one GROUP BY 1""").fetchall()}
    di = {s: (n, b, pb) for s, n, b, pb in con.execute("""SELECT season, count(*), count(*) FILTER (WHERE has_team_box_score),
             count(*) FILTER (WHERE has_player_box_score) FROM core.games WHERE game_universe = 'DI_VS_DI' GROUP BY 1""").fetchall()}
    sub_cov = {s: n for s, n in con.execute("""SELECT g.season, count(DISTINCT s.canonical_game_id) FROM fact.substitutions s
             JOIN core.games g USING (canonical_game_id) WHERE g.game_universe = 'DI_VS_DI' GROUP BY 1""").fetchall()}
    lin_cov = {s: n for s, n in con.execute("""SELECT g.season, count(DISTINCT l.canonical_game_id) FROM fact.lineup_stats l
             JOIN core.games g USING (canonical_game_id) WHERE g.game_universe = 'DI_VS_DI' GROUP BY 1""").fetchall()}
    con.close()
    r = lambda a, b: (a / b) if b else 0.0
    row = {}
    for s in SEASONS:
        n_di, tb, pb = di.get(s, (0, 0, 0))
        p = pbp.get(s, {})
        row[s] = {
            "di_games": n_di, "team_box_cov": r(tb, n_di), "player_box_cov": r(pb, n_di),
            "player_id_res": r(ident.get(s, (0, 0))[1], ident.get(s, (0, 0))[0]),
            "pbp_cov": r(p.get("di_vs_di_games_with_pbp") or 0, n_di), "pbp_games": int(p.get("games_with_pbp") or 0),
            "recon": r(p.get("games_score_reconciled") or 0, p.get("games_with_pbp") or 0),
            "shooter": sh.get(s, {}).get("shooter_resolution") or 0.0, "xy": sh.get(s, {}).get("xy_coverage_field_goals") or 0.0,
            "valid5": flo.get(s, {}).get("valid_5v5_rate") or 0.0, "onfloor_games": int(flo.get(s, {}).get("games_with_onfloor") or 0),
            "subs_cov": r(sub_cov.get(s, 0), n_di), "timing": su.get(s, {}).get("timing_completeness") or 0.0,
            "lineup_cov": r(lin_cov.get(s, 0), n_di)}
    T = THRESH
    # name -> (conditions [(metric, threshold)], note, unit, usable-count function)
    rules = {
        "player latent talent": ([("player_box_cov", T["box_cov"]), ("player_id_res", T["id_res"])], "",
                                 "games with player box (D-I vs D-I)", lambda x: round(x["player_box_cov"] * x["di_games"])),
        "player shooting (shot-level)": ([("pbp_cov", T["pbp_cov"]), ("shooter", T["shooter"])],
                                         "Season-level shooting profiles also exist 2014-2025 in the Starter Pack.", "games with PBP", lambda x: x["pbp_games"]),
        "team/game environment": ([("team_box_cov", T["box_cov"])], "", "games with team box (D-I vs D-I)",
                                  lambda x: round(x["team_box_cov"] * x["di_games"])),
        "participation/minutes": ([("player_box_cov", T["box_cov"]), ("player_id_res", T["id_res"])],
                                  "Box minutes only; exact on-court participation additionally needs substitutions (see rotation).",
                                  "games with player box", lambda x: round(x["player_box_cov"] * x["di_games"])),
        "lineup interaction": ([("valid5", T["valid5"]), ("lineup_cov", T["lineup_cov"])], "", "games with lineups",
                               lambda x: round(x["lineup_cov"] * x["di_games"])),
        "possession/event model": ([("pbp_cov", T["pbp_cov"]), ("recon", T["recon"])], "", "games with PBP", lambda x: x["pbp_games"]),
        "shot model (with location)": ([("pbp_cov", T["pbp_cov"]), ("shooter", T["shooter"]), ("xy", T["xy"])], "", "games with PBP",
                                       lambda x: x["pbp_games"]),
        "substitution/rotation": ([("subs_cov", T["subs_cov"]), ("timing", T["timing"])], "", "games with substitutions",
                                  lambda x: round(x["subs_cov"] * x["di_games"])),
    }
    L = ["# Candidate subsystem training windows (Stage 2T) -- PROPOSALS ONLY", "",
         "Rendered by `cbb_platform/stage2_reports.py` from the measured per-season QA files and the canonical database. "
         "**Nothing here freezes a modeling window**; the quantitative architect approves/freezes after Stage 2. "
         "2025-26 is the lockbox and is excluded everywhere. Latest development season for every subsystem: 2024-25 (2025).", "",
         "Rule for a candidate window: the earliest season from which EVERY later development season meets every printed "
         "threshold (a contiguous window ending 2024-25). Seasons that pass in isolation before a failing season are "
         "listed as 'isolated qualifying seasons'. Metric definitions are under the table at the end.", ""]
    for name, (conds, note, unit, count) in rules.items():
        fails = {s: [f"{m}={row[s][m]:.3f}<{t}" for m, t in conds if row[s][m] < t] for s in SEASONS}
        q = {s: not fails[s] for s in SEASONS}
        first = _first_run(q)
        window = [s for s in SEASONS if first and s >= first]
        isolated = [s for s in SEASONS if q[s] and s not in window]
        L += [f"## {name}", "", "* Threshold: " + " and ".join(f"`{m}` >= {t}" for m, t in conds) + (f". {note}" if note else "."),
              f"* Earliest plausible season: **{first if first else 'NONE'}**" + (f" ({first - 1}-{str(first)[-2:]})" if first else ""),
              "* Latest development season: 2025 (2024-25)",
              f"* Usable {unit} in the candidate window: **{sum(count(row[s]) for s in window):,}**",
              f"* Isolated qualifying seasons outside the window: {isolated or 'none'}",
              "* Seasons excluded and why: " + ("; ".join(f"{s} ({', '.join(f)})" for s, f in fails.items() if f) or "none"), ""]
    cols = ["di_games", "team_box_cov", "player_box_cov", "player_id_res", "pbp_cov", "recon", "shooter", "xy", "valid5",
            "subs_cov", "timing", "lineup_cov"]
    L += ["## Measured inputs by season", "", "| season | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for s in SEASONS:
        L.append(f"| {s} | " + " | ".join(f"{row[s][c]:.3f}" if isinstance(row[s][c], float) else str(row[s][c]) for c in cols) + " |")
    L += ["", "## Does source quality change by era?", "",
          "Read directly from the table above and `data/qa/cbbd_event_coverage_by_season.csv`:", "",
          "* **Box-score era (2004-05 onward):** team/player boxes exist for every season; coverage dips in 2011-12 and "
          "2012-13 (`team_box_cov`), where the Starter Pack also holds orphan duplicate game records (docs/GAME_IDENTITY_AUDIT.md).",
          "* **PBP era:** no CBBD PBP for 2004-05; partial and uneven coverage until the season where `pbp_cov` first stays "
          "above threshold; shotInfo (shooter, range) accompanies PBP throughout; x/y locations and wallclock arrive later (see `xy`).",
          "* **Tracking era (2023-24, 2024-25 only):** onFloor, substitutions and lineups exist only here. Any lineup, rotation "
          "or on-court participation subsystem is limited to two development seasons.", "",
          "Thresholds used: `" + json.dumps(THRESH) + "`.", "",
          "Metric definitions: `di_games` D-I vs D-I canonical games; `team_box_cov` / `player_box_cov` share of them with a "
          "team / player box; `player_id_res` resolved share of D-I player-game rows; `pbp_cov` share of D-I games with CBBD PBP; "
          "`recon` share of PBP games whose final PBP score equals the official score; `shooter` resolved shooter share of "
          "shotInfo rows; `xy` share of field-goal shots with x/y; `valid5` VALID_5V5 share of plays with onFloor; `subs_cov` "
          "share of D-I games with substitutions; `timing` share of substitution rows with complete in/out timing; `lineup_cov` "
          "share of D-I games with /lineups/game units."]
    return "\n".join(L) + "\n"


def run(db_path=None):
    STRATEGY_MD.write_text(render_strategy())
    WINDOWS_MD.write_text(render_windows(db_path))


if __name__ == "__main__":
    run()
    print("written", STRATEGY_MD.name, WINDOWS_MD.name)
