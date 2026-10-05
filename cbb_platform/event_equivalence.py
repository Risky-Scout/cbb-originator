"""Stage 2B -- empirical endpoint-equivalence tests on a bounded sample (writes the evidence behind
docs/CBBD_BACKFILL_STRATEGY.md).

    .venv/bin/python -m cbb_platform.event_equivalence fetch
    .venv/bin/python -m cbb_platform.event_equivalence analyze

PBP   For three full US/Eastern dates (2007-08, 2015-16, 2024-25 eras) every game is fetched with
      /plays/game/{id} and the same dates with /plays/date (the UTC dates the games tip on). For each game
      the two event sets are compared on the stable event key (gameId, play id): missing / extra ids and
      field-by-field equality of every common event. A busy date checks /plays/date for truncation
      (spot-checked against /plays/game). /plays/team (one team-season) is compared on three of its games.
SUBS  /substitutions/team (one team-season) vs /substitutions/game for three of its games.
LINEUPS /lineups/team (date-bounded to one game day) vs /lineups/game: the team endpoint has no gameId.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta

import duckdb

from cbb_platform import config as C, raw_store

PURPOSE = "stage2b_equivalence"
EQUIV_DATES = ["2008-01-11", "2016-01-15", "2025-01-17"]     # low-volume ET dates (8, 8, 10 games)
BUSY = {"et_date": "2025-02-08", "spot_games": 5}          # 154 ET games
TEAM = {"season": 2025, "team": "Purdue"}
OUT = C.QA / "cbbd_endpoint_equivalence.json"


def _con():
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    return con


def games_on(con, et_date: str) -> list[tuple[int, str]]:
    """(cbbd game id, UTC tip date) of every canonical game on an ET date."""
    return [(int(k.replace("id=", "")), str(d)) for k, d in con.execute(
        """SELECT source_record_key, CAST(scheduled_tip_utc AS DATE) FROM core.games
           WHERE game_date_et = CAST(? AS DATE) ORDER BY 1""", [et_date]).fetchall()]


def team_games(con, season, team) -> list[tuple[int, str]]:
    return [(int(k.replace("id=", "")), str(d)) for k, d in con.execute(
        """SELECT g.source_record_key, CAST(g.game_date_et AS VARCHAR) FROM core.games g
           JOIN core.teams t ON t.canonical_team_id IN (g.home_team_id, g.away_team_id)
           WHERE g.season = ? AND t.school = ? ORDER BY g.scheduled_tip_utc""", [season, team]).fetchall()]


def plan() -> list:
    con = _con()
    jobs = []
    kw = lambda scope: {"purpose": PURPOSE, "scope": scope}
    for d in EQUIV_DATES:
        gs = games_on(con, d)
        for gid, _ in gs:
            jobs.append(("plays/game", {"gameId": gid}, kw(f"equiv_date:{d}")))
        for ud in sorted({u for _, u in gs}):
            jobs.append(("plays/date", {"date": ud}, kw(f"equiv_date:{d}")))
    busy = games_on(con, BUSY["et_date"])
    for ud in sorted({u for _, u in busy}):
        jobs.append(("plays/date", {"date": ud}, kw("busy_date")))
    for gid, _ in sorted(busy, key=lambda x: x[0])[:: max(1, len(busy) // BUSY["spot_games"])][:BUSY["spot_games"]]:
        jobs.append(("plays/game", {"gameId": gid}, kw("busy_date_spot")))
    tg = team_games(con, **TEAM)
    jobs.append(("plays/team", dict(TEAM), kw("team_season")))
    jobs.append(("substitutions/team", dict(TEAM), kw("team_season")))
    for gid, d in tg[:3]:
        jobs += [("plays/game", {"gameId": gid}, kw("team_season_game")),
                 ("substitutions/game", {"gameId": gid}, kw("team_season_game")),
                 ("lineups/game", {"gameId": gid}, kw("team_season_game")),
                 ("lineups/team", {**TEAM, "startDateRange": d, "endDateRange": d}, kw("team_game_day"))]
    jobs.append(("lineups/team", dict(TEAM), kw("team_season")))
    con.close()
    for j in jobs:
        j[2]["season"] = j[1].get("season")
    return jobs


def fetch(workers: int = 2):
    from cbb_platform.cbbd_client import Client, compile_manifest
    Client().run_many(plan(), workers=workers, label="equivalence")
    compile_manifest()


# ---------------------------------------------------------------------- comparison (pure; unit tested)
def compare_event_sets(per_game: list[dict], bulk: list[dict]) -> dict:
    """Compare one game's /plays/game events with the same game's events inside a bulk payload."""
    a = {p["id"]: p for p in per_game}
    b = {p["id"]: p for p in bulk}
    common = a.keys() & b.keys()
    diff_fields = defaultdict(int)
    for k in common:
        for f in set(a[k]) | set(b[k]):
            if a[k].get(f) != b[k].get(f):
                diff_fields[f] += 1
    return {"per_game_events": len(a), "bulk_events": len(b), "missing_in_bulk": len(a.keys() - b.keys()),
            "extra_in_bulk": len(b.keys() - a.keys()), "common": len(common),
            "events_identical": sum(1 for k in common if a[k] == b[k]), "field_differences": dict(diff_fields),
            "duplicate_ids_in_bulk": len(bulk) - len(b), "duplicate_ids_in_per_game": len(per_game) - len(a),
            "equivalent": len(a) == len(b) == len(common) and all(a[k] == b[k] for k in common)}


def analyze() -> dict:
    from cbb_platform.cbbd_client import REQUEST_LOG, request_key
    log = {}
    for line in REQUEST_LOG.read_text().splitlines():
        e = json.loads(line)
        if e.get("status") in ("STORED", "ALREADY_STORED"):
            log[e["request_key"]] = e
    load = lambda ep, p: json.loads(raw_store.read_payload(C.RAW / log[request_key(ep, p)]["stored_file"]))
    con = _con()
    res = {"pbp_date_vs_game": {}, "pbp_busy_date": {}, "pbp_team_vs_game": {}, "subs_team_vs_game": {},
           "lineups_team_vs_game": {}}
    for d in EQUIV_DATES:
        gs = games_on(con, d)
        bulk = [p for ud in sorted({u for _, u in gs}) for p in load("plays/date", {"date": ud})]
        by_game = defaultdict(list)
        for p in bulk:
            by_game[p["gameId"]].append(p)
        per = {gid: compare_event_sets(load("plays/game", {"gameId": gid}), by_game.get(gid, [])) for gid, _ in gs}
        res["pbp_date_vs_game"][d] = {
            "games": len(gs), "games_with_pbp_per_game": sum(1 for x in per.values() if x["per_game_events"]),
            "games_equivalent": sum(1 for x in per.values() if x["equivalent"]),
            "events_per_game_endpoint": sum(x["per_game_events"] for x in per.values()),
            "events_bulk_for_these_games": sum(x["bulk_events"] for x in per.values()),
            "bulk_events_total_incl_adjacent_et_dates": len(bulk),
            "bulk_duplicate_event_ids": len(bulk) - len({(p["gameId"], p["id"]) for p in bulk}),
            "bytes_per_game_calls": sum(log[request_key("plays/game", {"gameId": g})]["size_bytes"] for g, _ in gs),
            "bytes_date_calls": sum(log[request_key("plays/date", {"date": u})]["size_bytes"] for u in {u for _, u in gs}),
            "calls_per_game": len(gs), "calls_date": len({u for _, u in gs}),
            "per_game": {str(k): v for k, v in per.items()}}
    busy = games_on(con, BUSY["et_date"])
    bulk = [p for ud in sorted({u for _, u in busy}) for p in load("plays/date", {"date": ud})]
    by_game = defaultdict(list)
    for p in bulk:
        by_game[p["gameId"]].append(p)
    et_ids = {g for g, _ in busy}
    spots = [g for g, _ in sorted(busy, key=lambda x: x[0])[:: max(1, len(busy) // BUSY["spot_games"])][:BUSY["spot_games"]]]
    res["pbp_busy_date"] = {
        "et_date": BUSY["et_date"], "et_games": len(busy), "utc_dates": sorted({u for _, u in busy}),
        "bulk_events": len(bulk), "bulk_games": len(by_game), "bulk_games_on_et_date": len(et_ids & by_game.keys()),
        "bulk_bytes": sum(log[request_key("plays/date", {"date": u})]["size_bytes"] for u in {u for _, u in busy}),
        "payload_row_counts": {u: log[request_key("plays/date", {"date": u})]["row_count"] for u in sorted({u for _, u in busy})},
        "spot_checks": {str(g): compare_event_sets(load("plays/game", {"gameId": g}), by_game.get(g, [])) for g in spots}}
    tg = team_games(con, **TEAM)
    team_plays = load("plays/team", TEAM)
    tb = defaultdict(list)
    for p in team_plays:
        tb[p["gameId"]].append(p)
    res["pbp_team_vs_game"] = {
        "team_season": TEAM, "games_in_canonical": len(tg), "games_in_team_payload": len(tb), "events": len(team_plays),
        "bytes": log[request_key("plays/team", TEAM)]["size_bytes"],
        "compared": {str(g): compare_event_sets(load("plays/game", {"gameId": g}), tb.get(g, [])) for g, _ in tg[:3]}}
    subs = load("substitutions/team", TEAM)
    sb = defaultdict(list)
    for r in subs:
        sb[r["gameId"]].append(r)
    cmp = {}
    for g, _ in tg[:3]:
        game_rows = load("substitutions/game", {"gameId": g})
        team_id = {r["teamId"] for r in subs}
        mine = [r for r in game_rows if r["teamId"] in team_id]
        key = lambda r: json.dumps(r, sort_keys=True)
        cmp[str(g)] = {"game_endpoint_rows": len(game_rows), "game_endpoint_rows_for_team": len(mine),
                       "team_endpoint_rows": len(sb.get(g, [])),
                       "identical_multiset": sorted(map(key, mine)) == sorted(map(key, sb.get(g, []))),
                       "team_endpoint_has_opponent_rows": any(r["teamId"] not in team_id for r in sb.get(g, []))}
    res["subs_team_vs_game"] = {"team_season": TEAM, "rows": len(subs), "games": len(sb),
                                "bytes": log[request_key("substitutions/team", TEAM)]["size_bytes"], "compared": cmp}
    lt = load("lineups/team", TEAM)
    lcmp = {}
    for g, d in tg[:3]:
        lg = load("lineups/game", {"gameId": g})
        ld = load("lineups/team", {**TEAM, "startDateRange": d, "endDateRange": d})
        tid = {u["teamId"] for u in ld} or {u["teamId"] for u in lt}
        mine = {u["idHash"]: u for u in lg if u["teamId"] in tid}
        day = {u["idHash"]: u for u in ld}
        lcmp[str(g)] = {"game_units_for_team": len(mine), "team_day_units": len(day),
                        "same_units": mine.keys() == day.keys(),
                        "same_seconds": all(mine[k]["totalSeconds"] == day[k]["totalSeconds"] for k in mine.keys() & day.keys())}
    res["lineups_team_vs_game"] = {"team_season_units": len(lt), "team_payload_has_gameId": any("gameId" in u for u in lt),
                                   "day_bounded_vs_game": lcmp}
    con.close()
    OUT.write_text(json.dumps(res, indent=1, default=str))
    return res


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]:
        fetch()
    elif sys.argv[1:2] == ["analyze"]:
        r = analyze()
        for k, v in r["pbp_date_vs_game"].items():
            print(k, {x: v[x] for x in v if x != "per_game"})
        print(json.dumps({k: v for k, v in r.items() if k != "pbp_date_vs_game"}, indent=1, default=str)[:6000])
    else:
        print(__doc__)
