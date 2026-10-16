"""CBBD box-score ingestion route (lockbox data plan §2 route (a); architect ruling 2026-10-13: CBBD is the historical
box-score provider for the lockbox dataset). Development validation only until LOCKBOX_OPEN.

The canonical build reads box scores only from the Starter Pack (`build_canonical.create_staging_views`:
staging.sp_*). This route fetches the same CBBD families through the single guarded fetch path and reshapes them into
those exact Starter Pack column layouts, so EVERY downstream rule (ids, identity resolution, de-mirroring, quarantine,
played status, PIT knowable_at, participation, accounting flags) runs unchanged:

    family (staging.sp_*)        CBBD endpoint                 request plan (per season)
    games                        games                         season + 7-day date windows
    team_game_stats              games/teams                   season + 7-day windows (API cap 3,000 games)
    player_game_stats            games/players                 season + [d-1, d+1] around every game date (cap 1,000)
    player_season_stats          stats/player/season           season
    team_season_stats            stats/team/season             season (D-I proxy: game_universe needs it)
    teams / conferences          teams / conferences           reference (guard-exempt): ids missing from the
                                                               Starter Pack files are appended, never replaced
    *_season_shooting_stats      (not fetched)                 empty for route seasons: no v1 input reads them

Route seasons REPLACE the Starter Pack rows of those seasons (development validation) or ADD a season the Starter Pack
does not have (the lockbox, only after the reviewed guard change). The guard is never bypassed: `plan` and `fetch`
call lockbox.check_season / check_request, so under the closed guard 2025-26 is refused before any credential read.

Before staging (route 1.2.0): completed games only (CBBD status 'final'; box rows of any other game dropped), and the
generic provider team-assignment rule (misfiled and mirrored player box lines; repair only under the team-box
reconciliation gate, else quarantine) -- see final_games_only / validate_team_assignment. Both are reported per row.

Known, declared representation differences from the Starter Pack (docs/lockbox/CBBD_BOX_ROUTE_RECONCILIATION_SPEC.md):
the per-record Starter Pack player-game `athleteId` is replaced by CBBD's persistent athleteId (so `player_game_key`
differs; canonical player identity still resolves by the frozen (season, team, exact name) rule), lineage columns
(source_file / source_record_key) name the raw payload, and venue_country is not supplied by the API.

    .venv/bin/python -m cbb_platform.cbbd_box_route fetch 2024 2025                # development validation (own raw root)
    .venv/bin/python -m cbb_platform.cbbd_box_route build 2024 2025 --out <dir>    # route build + successor identity
    ... fetch | fetch-context | build  [--raw-root R --log L]                      # opening: main raw store + request log
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from cbb_platform import build_canonical as B, config as C, lockbox

ROUTE_VERSION = "cbbd-box-route-1.2.0"
PROVIDER = "cbbd"
PURPOSE = "cbbd_box_route"
DEV_RAW_ROOT = C.RAW / "cbbd_box_route_dev"              # gitignored; NOT read by build_canonical.load_all_raw
DEV_LOG = DEV_RAW_ROOT / "request_log.jsonl"
FAMILY_ENDPOINT = {"games": "games", "team_game_stats": "games/teams", "player_game_stats": "games/players",
                   "player_season_stats": "stats/player/season", "team_season_stats": "stats/team/season"}
EMPTY_FAMILIES = ("player_season_shooting_stats", "team_season_shooting_stats")
REFERENCE = {"teams": "teams", "conferences": "conferences"}
ROUTE_ENDPOINTS = frozenset(FAMILY_ENDPOINT.values())
WEEK = 7


# ============================================================================ request plan
def season_dates(season: int) -> tuple[date, date]:
    """US/Eastern season boundary (LOCKBOX_POLICY.md): Aug 1 .. Jul 31."""
    return date(season - 1, 8, 1), date(season, 7, 31)


def _windows(lo: date, hi: date, days: int) -> list[tuple[str, str]]:
    """Chained date windows for the CBBD date filter, whose semantics are startDate in [start 00:00Z, end 00:00Z]
    (observed 2026-10-14: a game tipping on a window's end date after 00:00Z is NOT returned; only games stored at
    exactly 00:00:00Z on that date are). Each window ends where the next starts, so every instant in [lo, hi 00:00Z]
    is covered; a game stored exactly at a shared 00:00Z boundary is returned twice and collapsed by dedupe(). The last
    window ends at `hi`, not hi + 1: the guard refuses any range ending in the next season (for the last development
    season that is 2025-26), so [hi 00:00Z, hi + 1) is not requested (season-end Jul 31; no game in May-Oct in any
    Starter Pack season; staged_rows fails if a route game falls in June-July)."""
    out, d = [], lo
    while d < hi:
        e = min(d + timedelta(days=days), hi)
        out.append((d.isoformat(), e.isoformat()))
        d = e
    return out


def plan_base(season: int) -> list[tuple[str, dict]]:
    """Phase 1 requests (no dependency on fetched data). Refuses a season the CURRENT guard refuses."""
    lockbox.check_season(season)
    lo, hi = season_dates(season)
    reqs = [(ep, {"season": season, "startDateRange": a, "endDateRange": b})
            for ep in ("games", "games/teams") for a, b in _windows(lo, hi, WEEK)]
    reqs += [("stats/player/season", {"season": season}), ("stats/team/season", {"season": season})]
    for ep, p in reqs:
        lockbox.check_request(PROVIDER, ep, p)
    return reqs


def plan_players(season: int, game_dates: list[str]) -> list[tuple[str, dict]]:
    """Phase 2: one games/players request per UTC game date d, window [d - 1, d + 1], i.e. [d-1 00:00Z, d+1 00:00Z]
    under the end-exclusive filter (see _windows): the whole UTC day d is covered with a day of margin before it
    (windows overlap; identical rows are de-duplicated, conflicting rows fail closed)."""
    lockbox.check_season(season)
    lo, hi = season_dates(season)
    reqs = []
    for d in sorted(set(game_dates)):
        x = date.fromisoformat(d)
        a, b = max(x - timedelta(days=1), lo), min(x + timedelta(days=1), hi)
        reqs.append(("games/players", {"season": season, "startDateRange": a.isoformat(), "endDateRange": b.isoformat()}))
    for ep, p in reqs:
        lockbox.check_request(PROVIDER, ep, p)
    return reqs


CONTEXT_ENDPOINTS = frozenset({"substitutions/team", "teams/roster", "recruiting/players", "recruiting/portal"})


def plan_context(season: int, teams) -> list[tuple[str, dict]]:
    """The outcome-free context families the frozen Stage 3 / v1 features read (participation history from the
    substitution feed, roster returners, recruits, portal), in the exact request shapes of the Stage 2C backfill that
    built them for the development seasons: /substitutions/team per (season, school) of every team that played,
    /teams/roster per season, /recruiting/players and /recruiting/portal per class year season - 1. They are stored
    in the MAIN raw store and normalized by the existing adapter (event_build), not by this module."""
    lockbox.check_season(season)
    reqs = [("substitutions/team", {"season": season, "team": t}) for t in sorted(set(teams))]
    reqs += [("teams/roster", {"season": season}), ("recruiting/players", {"year": season - 1}),
             ("recruiting/portal", {"year": season - 1})]
    for ep, p in reqs:
        lockbox.check_request(PROVIDER, ep, p)
    return reqs


# ============================================================================ fetch (guarded; development seasons only)
def _client(raw_root: Path, log_path: Path):
    from cbb_platform import cbbd_client
    return cbbd_client.Client(raw_root=raw_root, log_path=log_path)


def fetch(season: int, raw_root: Path = DEV_RAW_ROOT, log_path: Path = DEV_LOG, workers: int = 2) -> dict:
    lockbox.check_season(season)
    reqs = plan_base(season)                           # every request passes the current guard before any client exists
    cl = _client(raw_root, log_path)
    kw = {"purpose": PURPOSE, "season": season, "scope": ROUTE_VERSION}
    base = cl.run_many([(ep, p, kw) for ep, p in reqs], workers=workers, label=f"route base {season}")
    for ref in REFERENCE.values():
        cl.fetch(ref, {}, purpose=PURPOSE, season=None, scope=ROUTE_VERSION)
    games = [g for e in base if e and e["endpoint"] == "games" for g in _read(raw_root, e)]
    dates = sorted({g["startDate"][:10] for g in games if g.get("startDate")})
    players = cl.run_many([(ep, p, kw) for ep, p in plan_players(season, dates)], workers=workers,
                          label=f"route players {season}")
    bad = [e for e in base + players if not e or e.get("status") not in ("STORED", "ALREADY_STORED")]
    if bad:
        raise RuntimeError(f"{len(bad)} route requests failed for season {season}")
    return {"season": season, "requests": len(base) + len(players) + len(REFERENCE), "game_dates": len(dates),
            "quota_remaining": cl.quota_remaining}


def _read(raw_root: Path, entry: dict):
    from cbb_platform import raw_store
    return json.loads(raw_store.read_payload(Path(raw_root) / entry["stored_file"]))


def payload_index(seasons, raw_root: Path = DEV_RAW_ROOT, log_path: Path = DEV_LOG) -> list[dict]:
    """The stored route payloads for `seasons` (+ reference), from the route request log; sorted, deterministic."""
    want = set(int(s) for s in seasons)
    plan = {json.dumps([ep, p], sort_keys=True) for s in want for ep, p in plan_base(s)}
    done = {}
    for line in Path(log_path).read_text().splitlines():
        e = json.loads(line)
        if e.get("status") in ("STORED", "ALREADY_STORED") and e.get("purpose") == PURPOSE:
            done[e["request_key"]] = e
    planned_eps = {json.loads(k)[0] for k in plan}      # date-windowed base endpoints: only the CURRENT plan's windows
    out = [e for e in done.values()
           if e["endpoint"] in REFERENCE.values() or (e.get("season") in want and e["endpoint"] in ROUTE_ENDPOINTS and (
               e["endpoint"] not in planned_eps or json.dumps([e["endpoint"], e["params"]], sort_keys=True) in plan))]
    missing = plan - {json.dumps([e["endpoint"], e["params"]], sort_keys=True) for e in out}
    if missing:
        raise RuntimeError(f"route payloads missing for {len(missing)} planned requests, e.g. {sorted(missing)[0]}")
    return sorted(out, key=lambda e: e["request_key"])


# ============================================================================ reshape -> Starter Pack layouts
def _txt(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "True" if v else "False"                     # build_canonical.as_bool reads exactly these
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(str(x) for x in v) + "]"     # e.g. home_period_points '[49, 41]'
    return str(v)


def _flat(o: dict, pre: str = "") -> dict:
    out = {}
    for k, v in o.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{pre}{k}_"))
        elif k == "byPeriod" and isinstance(v, list):        # Starter Pack: teamStats_points_byPeriod_0 .. _7
            out.update({f"{pre}{k}_{i}": x for i, x in enumerate(v)})
        else:
            out[pre + k] = v
    return out


def _utc_naive(s):
    """CBBD startDate (ISO, UTC) -> the Starter Pack games.csv form 'YYYY-MM-DD HH:MM:SS' (naive UTC)."""
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


GAMES_MAP = {"id": "id", "season": "season", "season_label": "seasonLabel", "season_type": "seasonType",
             "venue_id": "venueId", "venue": "venue", "venue_city": "city", "venue_state": "state",
             "neutral_site": "neutralSite", "conference_game": "conferenceGame", "game_type": "gameType",
             "notes": "gameNotes", "tournament": "tournament",
             **{f"{s}_{c}": f"{s}{t}" for s in ("home", "away") for c, t in
                (("team_id", "TeamId"), ("team", "Team"), ("conference_id", "ConferenceId"), ("conference", "Conference"),
                 ("seed", "Seed"), ("points", "Points"), ("period_points", "PeriodPoints"), ("winner", "Winner"))}}
PLAYER_GAME_MAP = {"game_id": "gameId", "season": "season", "season_type": "seasonType", "team_id": "teamId",
                   "team": "team", "conference": "conference", "team_seed": "teamSeed", "opponent_id": "opponentId",
                   "opponent": "opponent", "opponent_conference": "opponentConference", "opponent_seed": "opponentSeed",
                   "neutral_site": "neutralSite", "is_home": "isHome", "conference_game": "conferenceGame",
                   "game_type": "gameType", "game_minutes": "gameMinutes", "game_pace": "gamePace"}
TEAMS_MAP = {"id": "id", "team": "school", "mascot": "mascot", "nickname": "shortDisplayName",
             "abbreviation": "abbreviation", "display_name": "displayName", "primary_color": "primaryColor",
             "secondary_color": "secondaryColor"}
CONFERENCES_MAP = {"id": "id", "name": "name", "abbreviation": "abbreviation", "short_name": "shortName"}


def reshape(family: str, payload: list, lineage: str) -> list[dict]:
    """One CBBD payload -> Starter Pack-shaped rows (all VARCHAR, + `filename` lineage)."""
    rows = []
    for r in payload:
        if family == "games":
            row = {k: r.get(v) for k, v in GAMES_MAP.items()}
            row["start_date"] = _utc_naive(r.get("startDate"))
            row[STATUS_COL] = r.get("status")              # filtered and removed by staged_rows (not a Starter Pack column)
            rows.append(row)
        elif family == "player_game_stats":
            head = {k: r.get(v) for k, v in PLAYER_GAME_MAP.items()}
            head["start_date"] = r.get("startDate")
            for pl in r.get("players") or []:
                p = _flat(pl)
                p.pop("athleteSourceId", None)
                rows.append({**head, **p})
        elif family in ("team_game_stats", "player_season_stats", "team_season_stats"):
            rows.append(_flat(r))
        elif family == "teams":
            rows.append({k: r.get(v) for k, v in TEAMS_MAP.items()})
        elif family == "conferences":
            rows.append({k: r.get(v) for k, v in CONFERENCES_MAP.items()})
        else:
            raise ValueError(family)
    return [{**{k: _txt(v) for k, v in row.items()}, "filename": lineage} for row in rows]


KEYS = {"games": ("id",), "team_game_stats": ("gameId", "teamId"), "player_game_stats": ("game_id", "team_id", "athleteId"),
        "player_season_stats": ("season", "teamId", "athleteId"), "team_season_stats": ("season", "teamId"),
        "teams": ("id",), "conferences": ("id",)}
ENDPOINT_FAMILY = {**{v: k for k, v in FAMILY_ENDPOINT.items()}, **{v: k for k, v in REFERENCE.items()}}


def dedupe(family: str, rows: list[dict]) -> tuple[list[dict], int]:
    """Overlapping request windows return the same record more than once. Identical content (ignoring lineage) is
    collapsed; two DIFFERENT records under one key fail closed (never silently chosen)."""
    seen, out, dup = {}, [], 0
    for r in rows:
        k = tuple(r.get(c) for c in KEYS[family])
        body = {c: v for c, v in r.items() if c != "filename"}
        if k in seen:
            if seen[k] != body:
                raise ValueError(f"{family}: conflicting CBBD records for key {k}")
            dup += 1
            continue
        seen[k] = body
        out.append(r)
    return sorted(out, key=lambda r: tuple(str(r.get(c)) for c in KEYS[family])), dup


# D-I membership (accepted canonical contract, build_canonical: a team-season is D-I iff staging.sp_team_season_stats
# has a row for it). The Starter Pack season-stats families hold D-I rows only; CBBD stats/team/season and
# stats/player/season also return non-D-I schools, with `conference` NULL. On both development seasons the Starter Pack
# D-I set is exactly the CBBD rows with a non-NULL conference (team 726/726, player 10,207/10,207; 0 mismatches either
# way), so the route keeps exactly those rows and the unchanged canonical rule then reproduces the accepted D-I set.
DI_FAMILIES = ("team_season_stats", "player_season_stats")
OFF_SEASON_MONTHS = frozenset({"06", "07"})


def is_di_season_row(row: dict) -> bool:
    return row.get("conference") not in (None, "")


# Completed games only (the historical model contract; the Starter Pack holds final games only). CBBD `games.status`
# takes the values scheduled / in_progress / final / postponed / cancelled; the development payloads hold only final,
# scheduled and cancelled. A game is completed iff its status, normalized for case and surrounding whitespace only, is
# exactly "final". Every other value -- including NULL and any unrecognized value -- fails closed. Completion is never
# inferred from the date, a score, or the presence of team / player box rows. team_game_stats and player_game_stats
# carry no status: they are kept only for a game id the games family marks final.
STATUS_COL = "_cbbd_status"
FINAL_STATUS = "final"


def normalize_status(v) -> str | None:
    return v.strip().lower() if isinstance(v, str) else None


def is_final_status(v) -> bool:
    return normalize_status(v) == FINAL_STATUS


def final_games_only(out: dict) -> dict:
    """Drops every non-final game, and every team / player box row whose game is not a final game, before anything is
    staged. Returns the report (each excluded game listed)."""
    games, final, excluded = [], set(), []
    for r in out["games"]:
        status = r.pop(STATUS_COL, None)
        if is_final_status(status):
            final.add(r["id"])
            games.append(r)
        else:
            excluded.append({"game_id": r["id"], "season": r.get("season"), "status": status, "start_date": r.get("start_date"),
                             "home_team": r.get("home_team"), "away_team": r.get("away_team")})
    out["games"] = games
    rep = {"rule": "normalized_status == 'final' (case / surrounding whitespace only); all other values fail closed",
           "non_final_games_excluded": len(excluded),
           "non_final_by_status": {s: sum(e["status"] == s for e in excluded) for s in sorted({str(e["status"]) for e in excluded})},
           "non_final_games": sorted(excluded, key=lambda e: int(e["game_id"]))}
    nf = {e["game_id"] for e in excluded}
    for fam, col in (("team_game_stats", "gameId"), ("player_game_stats", "game_id")):
        keep = [r for r in out[fam] if r.get(col) in final]
        rep[f"{fam}_rows_of_non_final_games_dropped"] = sum(r.get(col) in nf for r in out[fam])
        rep[f"{fam}_rows_without_a_games_record_dropped"] = sum(r.get(col) not in final and r.get(col) not in nf for r in out[fam])
        out[fam] = keep
    return rep


# Provider team assignment (generic; never keyed on a game, team or player). In a few CBBD games a team's player box
# lines are filed under the OPPONENT only (unlike MIRRORED_BOX_LINES, where the same lines appear for both teams and
# team_attribution._demirror repairs them inside the canonical build). Evidence is the route's own already-approved CBBD
# facts of the same season and nothing else: the player's (persistent CBBD athleteId) box rows in OTHER games and the D-I
# season-stats rows (stats/player/season), with the thresholds of the accepted MIRRORED_BOX_LINES rule:
#   belongs(X):  >= 1 box row for X in other games AND a season-stats row on X with >= 2 games
#   denies(T):   0 box rows for T in other games AND at most a 1-game season-stats row on T
# A row filed under T is FLAGGED when T is not one of the game's two teams, or when denies(T) and belongs(the other team).
# Rows of a player listed for both teams in the game are left to _demirror. A game whose provider rows already reconcile
# PTS / FGM / 3PM / FTM with both team boxes is never touched. A flagged game is REPAIRED only if every flagged row has
# exactly one participating team that belongs() AND, after moving those rows, both teams' summed PTS / FGM / 3PM / FTM
# equal their team box exactly; otherwise every flagged row of the game is QUARANTINED (removed from staging, listed in
# the report). Raw payloads are never modified.
TEAM_CHECK = (("points", "teamStats_points_total"), ("fieldGoals_made", "teamStats_fieldGoals_made"),
              ("threePointFieldGoals_made", "teamStats_threePointFieldGoals_made"),
              ("freeThrows_made", "teamStats_freeThrows_made"))
SIDE_FIELDS = (("team_id", "opponent_id"), ("team", "opponent"), ("conference", "opponent_conference"),
               ("team_seed", "opponent_seed"))


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _sums(rows, team) -> tuple:
    return tuple(sum(_num(r.get(p)) for r in rows if r["team_id"] == team) for p, _ in TEAM_CHECK)


def _box(tbox, game, team):
    b = tbox.get((game, team))
    return None if b is None else tuple(_num(b.get(t)) for _, t in TEAM_CHECK)


def _moved(r: dict, target: str, g: dict) -> dict:
    """The row filed under its true team `target`: team / opponent fields swapped when the provider team played the game,
    otherwise taken from the games record."""
    m = dict(r)
    if r["team_id"] in (g["home_team_id"], g["away_team_id"]):
        for a, b in SIDE_FIELDS:
            m[a], m[b] = r.get(b), r.get(a)
    else:
        side, other = ("home", "away") if target == g["home_team_id"] else ("away", "home")
        for (a, b), c in zip(SIDE_FIELDS, ("team_id", "team", "conference", "seed")):
            m[a], m[b] = g.get(f"{side}_{c}"), g.get(f"{other}_{c}")
    m["is_home"] = _txt(target == g["home_team_id"])
    return m


def validate_team_assignment(out: dict) -> dict:
    games = {g["id"]: g for g in out["games"]}
    tbox = {(t["gameId"], t["teamId"]): t for t in out["team_game_stats"]}
    sf = {}
    for s in out["player_season_stats"]:
        k = (s["season"], s["athleteId"], s["teamId"])
        sf[k] = max(sf.get(k, 0), _num(s.get("games")))
    box, by_game = {}, {}
    for r in out["player_game_stats"]:
        box.setdefault((r["season"], r["athleteId"], r["team_id"]), set()).add(r["game_id"])
        by_game.setdefault(r["game_id"], []).append(r)

    def belongs(r, team):
        return (len(box.get((r["season"], r["athleteId"], team), set()) - {r["game_id"]}) >= 1
                and sf.get((r["season"], r["athleteId"], team), 0) >= 2)

    def denies(r, team):
        return (not (box.get((r["season"], r["athleteId"], team), set()) - {r["game_id"]})
                and sf.get((r["season"], r["athleteId"], team), 0) <= 1)

    def evidence(r, gid, teams):
        return {x: {"other_game_box_rows": len(box.get((r["season"], r["athleteId"], x), set()) - {gid}),
                    "season_stats_games": sf.get((r["season"], r["athleteId"], x), 0)} for x in sorted({*teams, r["team_id"]})}

    repairs, quarantines, validated, replace, drop = [], [], [], {}, set()
    for gid in sorted(by_game, key=int):
        g, rows = games[gid], by_game[gid]
        teams = (g["home_team_id"], g["away_team_id"])
        listed = {}
        for r in rows:
            listed.setdefault(r["athleteId"], []).append(r)
        flagged, mirrored = [], []
        for r in rows:
            if len(listed[r["athleteId"]]) > 1:
                mirrored.append(r)
                continue
            other = [x for x in teams if x != r["team_id"]]
            if r["team_id"] not in teams or (denies(r, r["team_id"]) and any(belongs(r, x) for x in other)):
                flagged.append(r)
        if not flagged and not mirrored:
            continue
        boxes = {t: _box(tbox, gid, t) for t in teams}
        if not mirrored and all(boxes[t] is not None and _sums(rows, t) == boxes[t] for t in teams):
            validated.append({"game_id": gid, "flagged_rows": len(flagged), "decision": "PROVIDER_ROWS_RECONCILE_KEPT"})
            continue
        # single-listed misfiled rows move to their evidenced team; for a mirrored player (one CBBD athleteId listed
        # under both teams) the copy under the team the player does NOT belong to is the misfiled one and is removed --
        # canonical DISTINCT ON (game_id, athleteId) would otherwise keep an arbitrary copy (persistent route ids).
        mirrored_ids = {id(r) for r in mirrored}
        targets = {id(r): [x for x in teams if x != r["team_id"] and belongs(r, x)] for r in flagged}
        copies = {}
        for a in {r["athleteId"] for r in mirrored}:
            true = [x for x in teams if belongs(listed[a][0], x) and all(denies(listed[a][0], y) for y in teams if y != x)]
            copies[a] = true[0] if len(true) == 1 and sorted(r["team_id"] for r in listed[a]) == sorted(teams) else None
        decided = all(len(v) == 1 for v in targets.values()) and all(copies.values())
        after = [] if not decided else [
            _moved(r, targets[id(r)][0], g) if id(r) in targets else r for r in rows
            if not (r["athleteId"] in copies and r["team_id"] != copies[r["athleteId"]])]
        ok = decided and all(boxes[t] is not None and _sums(after, t) == boxes[t] for t in teams)
        for r in flagged + mirrored:
            rec = {"game_id": gid, "season": r["season"], "athleteId": r["athleteId"], "name": r.get("name"),
                   "raw_provider_team_id": r["team_id"], "raw_provider_team": r.get("team"), "evidence": evidence(r, gid, teams)}
            if id(r) in mirrored_ids and ok:
                if r["team_id"] != copies[r["athleteId"]]:
                    drop.add(id(r))
                    keep = next(x for x in listed[r["athleteId"]] if x is not r)
                    repairs.append({**rec, "corrected_team_id": keep["team_id"], "corrected_team": keep.get("team"),
                                    "kind": "MIRRORED_COPY_REMOVED", "rule": "PROVIDER_TEAM_ASSIGNMENT_REPAIR (mirrored): "
                                    "belongs(true team) AND denies(copy team); the true-team line is kept, the copy removed; "
                                    "both team boxes reconcile PTS/FGM/3PM/FTM"})
            elif ok:
                m = _moved(r, targets[id(r)][0], g)
                replace[id(r)] = m
                repairs.append({**rec, "corrected_team_id": m["team_id"], "corrected_team": m["team"], "kind": "MISFILED_ROW_MOVED",
                                "rule": "PROVIDER_TEAM_ASSIGNMENT_REPAIR: belongs(corrected) AND denies(provider team); "
                                        "both team boxes reconcile PTS/FGM/3PM/FTM after the move"})
            else:
                drop.add(id(r))
                quarantines.append({**rec, "row": r, "kind": "MIRRORED" if id(r) in mirrored_ids else "MISFILED",
                                    "rule": "PROVIDER_TEAM_ASSIGNMENT_QUARANTINE: " + (
                                        "no unique evidenced team" if not decided else "team boxes do not reconcile after repair")})
    out["player_game_stats"] = sorted((replace.get(id(r), r) for r in out["player_game_stats"] if id(r) not in drop),
                                      key=lambda r: tuple(str(r.get(c)) for c in KEYS["player_game_stats"]))
    # Season-file echo of a repaired assignment (the accepted SPURIOUS_SEASON_MEMBERSHIP rule, applied here because the
    # canonical rule only sees games IT repaired): CBBD aggregates the misfiled line into a <= 1-game season row on the
    # wrong team, which would make the player a member of that team for participation. Removed from staging, listed.
    left = {(r["season"], r["athleteId"], r["team_id"]) for r in out["player_game_stats"]}
    wrong = {(x["season"], x["athleteId"], x["raw_provider_team_id"]) for x in repairs}
    echoes = [s for s in out["player_season_stats"] if (k := (s["season"], s["athleteId"], s["teamId"])) in wrong
              and k not in left and _num(s.get("games")) <= 1]
    eids = {id(s) for s in echoes}
    out["player_season_stats"] = [s for s in out["player_season_stats"] if id(s) not in eids]
    seen = {}
    for r in out["player_game_stats"]:                   # canonical collapses (game_id, athleteId): it must be unique now
        if seen.setdefault((r["game_id"], r["athleteId"]), r["team_id"]) != r["team_id"]:
            raise ValueError(f"player_game_stats: athlete {r['athleteId']} still listed for two teams in game {r['game_id']}")
    return {"rule": "generic belongs/denies evidence + PTS/FGM/3PM/FTM team-box reconciliation gate",
            "repaired": len(repairs), "quarantined": len(quarantines), "unresolved": 0,
            "games_repaired": sorted({r["game_id"] for r in repairs}, key=int),
            "games_quarantined": sorted({r["game_id"] for r in quarantines}, key=int),
            "flagged_but_provider_rows_reconcile": validated, "repairs": repairs, "quarantines": quarantines,
            "season_stats_echo_rows_removed": [{"season": s["season"], "athleteId": s["athleteId"], "name": s.get("name"),
                                                "team_id": s["teamId"], "team": s.get("team"), "games": s.get("games"),
                                                "rule": "SPURIOUS_SEASON_MEMBERSHIP (route): <= 1-game season row on the "
                                                        "provider team of a repaired row, no remaining box row for it"}
                                               for s in sorted(echoes, key=lambda s: (s["season"], s["athleteId"], s["teamId"]))]}


def same_name_records(out: dict) -> list[dict]:
    """(game, team, name) groups with more than one CBBD record. Existing canonical policy (build_canonical:
    same_name_in_game): two different source records with one name in a team-game cannot be told apart, so every such row
    is left UNRESOLVED_AMBIGUOUS (no player identity) and logged AMBIGUOUS_PLAYER_GAME_RECORD; nothing is merged."""
    g = {}
    for r in out["player_game_stats"]:
        g.setdefault((r["game_id"], r["team_id"], r.get("name")), []).append(r)
    stat = [c for c in ("minutes", "points", "fieldGoals_made", "fieldGoals_attempted", "rebounds_total", "assists")]
    return [{"game_id": k[0], "team_id": k[1], "name": k[2], "records": len(v), "athleteIds": sorted(r["athleteId"] for r in v),
             "identical_stat_lines": len({tuple(r.get(c) for c in stat) for r in v}) == 1}
            for k, v in sorted(g.items()) if len(v) > 1]


def staged_rows(seasons, raw_root: Path = DEV_RAW_ROOT, log_path: Path = DEV_LOG) -> tuple[dict, dict]:
    """family -> deduplicated Starter Pack-shaped rows for the route seasons; plus a payload/row report."""
    by: dict[str, list] = {f: [] for f in list(FAMILY_ENDPOINT) + list(REFERENCE)}
    report = {"payloads": 0, "payload_sha256": []}
    for e in payload_index(seasons, raw_root, log_path):
        fam = ENDPOINT_FAMILY[e["endpoint"]]
        by[fam] += reshape(fam, _read(raw_root, e), f"cbbd:{e['stored_file']}")
        report["payloads"] += 1
        report["payload_sha256"].append(e["payload_sha256"])
    report["payload_set_sha256"] = hashlib.sha256("\n".join(sorted(report.pop("payload_sha256"))).encode()).hexdigest()
    want = {str(s) for s in seasons}
    out = {}
    for fam, rows in by.items():
        if fam not in REFERENCE:
            stray = sorted({r.get("season") for r in rows} - want)
            if stray:
                raise lockbox.LockboxViolation(f"{fam}: route payload carries seasons {stray} outside {sorted(want)}")
        if fam in DI_FAMILIES:
            keep = [r for r in rows if is_di_season_row(r)]
            report[f"{fam}_non_di_rows_dropped"] = len(rows) - len(keep)
            rows = keep
        out[fam], report[f"{fam}_duplicates_collapsed"] = dedupe(fam, rows)   # a conflicting status under one id fails closed
    report["final_only"] = final_games_only(out)
    report["team_assignment"] = validate_team_assignment(out)
    report["same_name_records"] = same_name_records(out)
    for fam in out:
        report[f"{fam}_rows"] = len(out[fam])
    off =sorted({r["start_date"][:10] for r in out["games"] if (r.get("start_date") or "")[5:7] in OFF_SEASON_MONTHS})
    if off:   # the guard-capped last window leaves [Jul 31 00:00Z, Aug 1) unrequested; fail if the off-season is not empty
        raise ValueError(f"route games in June-July, the window plan assumes none: {off[:5]}")
    return out, report


# ============================================================================ hybrid canonical build
def _sp_view(con, family: str, pattern: str) -> str:
    glob = str(C.STARTER_PACK_DATA / pattern)
    return (f"SELECT * REPLACE (replace(filename, '{C.STARTER_PACK_ROOT}/', '') AS filename) "
            f"FROM read_csv('{glob}', all_varchar=true, union_by_name=true, filename=true)")


def install_staging(con, rows: dict, seasons) -> None:
    """staging.sp_<family> = Starter Pack rows of the NON-route seasons UNION ALL BY NAME the CBBD route rows."""
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    sl = ", ".join(f"'{int(s)}'" for s in seasons)
    for family, (pattern, _key) in C.STARTER_FAMILIES.items():
        sp = _sp_view(con, family, pattern)
        if family in REFERENCE:
            r = rows.get(family) or []
            if r:
                _register(con, f"cbbd_{family}", r)
                con.execute(f"""CREATE OR REPLACE VIEW staging.sp_{family} AS
                    WITH sp AS ({sp}) SELECT * FROM sp UNION ALL BY NAME
                    SELECT * FROM staging.cbbd_{family} WHERE id NOT IN (SELECT id FROM sp)""")
            else:
                con.execute(f"CREATE OR REPLACE VIEW staging.sp_{family} AS {sp}")
            continue
        keep = f"WITH sp AS ({sp}) SELECT * FROM sp WHERE season NOT IN ({sl})"
        if family in EMPTY_FAMILIES:
            con.execute(f"CREATE OR REPLACE VIEW staging.sp_{family} AS {keep}")
            continue
        _register(con, f"cbbd_{family}", rows[family])
        con.execute(f"CREATE OR REPLACE VIEW staging.sp_{family} AS {keep} UNION ALL BY NAME SELECT * FROM staging.cbbd_{family}")


def _register(con, name: str, rows: list[dict]) -> None:
    cols = sorted({c for r in rows for c in r})
    con.execute(f"CREATE OR REPLACE TABLE staging.{name} ({', '.join(f'\"{c}\" VARCHAR' for c in cols)})")
    if rows:
        con.executemany(f"INSERT INTO staging.{name} VALUES ({', '.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])


def build(seasons, db_path: Path, parquet_root: Path, raw_root: Path = DEV_RAW_ROOT, log_path: Path = DEV_LOG) -> dict:
    """The unchanged canonical build (build_canonical.build) with route seasons staged from CBBD. Writes ONLY db_path and
    parquet_root (never the development dataset). Under the closed guard only development seasons are accepted."""
    for s in seasons:
        lockbox.check_season(s)
    db_path, parquet_root = Path(db_path), Path(parquet_root)
    if db_path.resolve() == C.DUCKDB_PATH.resolve() or parquet_root.resolve() == C.CANONICAL.resolve():
        raise ValueError("the route build never overwrites the development canonical dataset")
    rows, report = staged_rows(seasons, raw_root, log_path)
    original = B.create_staging_views
    B.create_staging_views = lambda con: install_staging(con, rows, seasons)
    try:
        out = B.build(db_path=db_path, parquet_root=parquet_root)
    finally:
        B.create_staging_views = original
    return {"route_version": ROUTE_VERSION, "route_seasons": sorted(int(s) for s in seasons), "staging": report,
            "build_id": out["build_id"], "tables": out["tables"]}


def season_teams(season: int, raw_root: Path = DEV_RAW_ROOT, log_path: Path = DEV_LOG) -> list[str]:
    """School names of every team in the season's route `games` payloads (the /substitutions/team request set)."""
    rows, _ = staged_rows([season], raw_root, log_path)
    return sorted({r[c] for r in rows["games"] for c in ("home_team", "away_team") if r.get(c)})


def fetch_context(season: int, raw_root: Path = C.RAW, log_path: Path = C.MANIFESTS / "cbbd_request_log.jsonl",
                  box_raw_root: Path | None = None, box_log: Path | None = None, workers: int = 2) -> dict:
    """Context families into the MAIN raw store (normalized by the existing adapter on the next canonical build)."""
    reqs = plan_context(season, season_teams(season, box_raw_root or raw_root, box_log or log_path))
    cl = _client(raw_root, log_path)
    out = cl.run_many([(ep, p, {"purpose": PURPOSE + "_context", "season": season, "scope": ROUTE_VERSION})
                       for ep, p in reqs], workers=workers, label=f"route context {season}")
    bad = [e for e in out if not e or e.get("status") not in ("STORED", "ALREADY_STORED")]
    if bad:
        raise RuntimeError(f"{len(bad)} context requests failed for season {season}")
    return {"season": season, "requests": len(reqs), "quota_remaining": cl.quota_remaining}


def successor_version(route_build: dict, prefix: str = "cbb_data_v3.0+") -> dict:
    """Deterministic dataset identity of a route build: the content digest over every canonical table fingerprint
    (the dataset_manifest formula) plus the route payload-set hash. Two builds from the same inputs must agree."""
    canon = hashlib.sha256(json.dumps({t: v["fingerprint"] for t, v in route_build["tables"].items()},
                                      sort_keys=True).encode()).hexdigest()
    digest = hashlib.sha256(f"{route_build['staging']['payload_set_sha256']}|{canon}".encode()).hexdigest()
    return {"dataset_version": prefix + digest[:12], "canonical_content_digest": canon,
            "route_payload_set_sha256": route_build["staging"]["payload_set_sha256"]}


def _opts(args: list[str]) -> tuple[list[str], dict]:
    o, rest, i = {}, [], 0
    while i < len(args):
        if args[i].startswith("--"):
            o[args[i][2:]] = args[i + 1]
            i += 2
        else:
            rest.append(args[i])
            i += 1
    return rest, o


if __name__ == "__main__":
    cmd, (args, o) = sys.argv[1], _opts(sys.argv[2:])
    roots = {"raw_root": Path(o.get("raw-root", DEV_RAW_ROOT)), "log_path": Path(o.get("log", DEV_LOG))}
    if cmd == "fetch":
        for s in args:
            print(json.dumps(fetch(int(s), **roots)))
    elif cmd == "fetch-context":
        for s in args:
            print(json.dumps(fetch_context(int(s), box_raw_root=roots["raw_root"], box_log=roots["log_path"])))
    elif cmd == "build":
        out = Path(o["out"])
        r = build([int(s) for s in args], out / "cbb.duckdb", out / "parquet", **roots)
        r["identity"] = successor_version(r)
        (out / "route_build.json").write_text(json.dumps(r, indent=1, sort_keys=True) + "\n")
        print(json.dumps({"staging": r["staging"], "identity": r["identity"]}, indent=1))
    else:
        raise SystemExit(f"unknown command {cmd}")
