"""Stage 2C -- resumable historical CBBD backfill, scoped by the measured discovery (Stage 2A) and the
endpoint-equivalence evidence (Stage 2B). See docs/CBBD_BACKFILL_STRATEGY.md.

    .venv/bin/python -m cbb_platform.event_backfill plan          # print the request plan (offline)
    .venv/bin/python -m cbb_platform.event_backfill run [workers] # fetch (resumes; never refetches a stored payload)

Selected scope (decided ONLY from data/qa/cbbd_event_coverage_discovery.json + team escalation):
    PBP (+onFloor, shotInfo)   /plays/date        every UTC date with a tip, and the day after, in seasons
                                                  whose discovery PBP is PRESENT or PARTIAL (2005-06 .. 2024-25)
    substitutions              /substitutions/team every team in core.team_seasons for seasons with any measured
                                                  substitution coverage (2012-13 pocket, 2023-24, 2024-25)
    lineups                    /lineups/game      every canonical game of seasons with measured lineup coverage
    rosters                    /teams/roster      one call per season (already complete from discovery)
    recruiting / portal        one call per class year whose target season is a development season (<= 2024-25)
    venues                     one call
Request failures are retried by re-running; a failed request is never treated as absent data.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import duckdb

from cbb_platform import config as C, event_discovery as D, lockbox

PURPOSE = "stage2c_backfill"
RECRUIT_YEARS = list(range(2004, 2025))   # class year Y enrolls for end-year season Y+1 <= 2025
PORTAL_YEARS = list(range(2004, 2025))    # portal year Y feeds end-year season Y+1 <= 2025


def selected_seasons() -> dict:
    """Family -> seasons to backfill, derived from the measured discovery JSON (never hard-coded)."""
    disc = json.loads(D.OUT_JSON.read_text())["seasons"]
    esc = D.team_escalation_results()
    has = lambda f, s: disc[str(s)]["families"][f]["classification"] in ("MEASURED_PRESENT", "MEASURED_PARTIAL")
    team_rows = lambda ep, s: any((x["rows"] or 0) > 0 for x in esc.get(str(s), {}).get(ep, []))
    return {
        "pbp": [s for s in D.SEASONS if has("pbp", s)],
        "substitutions": [s for s in D.SEASONS if has("substitutions", s) or team_rows("substitutions/team", s)],
        "lineups": [s for s in D.SEASONS if has("lineups", s) or team_rows("lineups/team", s)],
    }


def utc_dates(con, season: int) -> list[str]:
    tips = [r[0] for r in con.execute("""SELECT DISTINCT CAST(coalesce(scheduled_tip_utc, CAST(game_date_et AS TIMESTAMPTZ)) AS DATE)
                                          FROM core.games WHERE season = ? ORDER BY 1""", [season]).fetchall()]
    out = set()
    for d in tips:
        out |= {d, d + timedelta(days=1)}
    # never request a date in the lockbox window (the guard would refuse it anyway)
    return sorted(x.isoformat() for x in out if lockbox.season_of_date(x) == season)


def plan() -> list:
    sel = selected_seasons()
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    jobs = []
    kw = lambda s, scope: {"purpose": PURPOSE, "season": s, "scope": scope}
    for s in sel["pbp"]:
        jobs += [("plays/date", {"date": d}, kw(s, f"date:{d}")) for d in utc_dates(con, s)]
    for s in sel["substitutions"]:
        teams = [r[0] for r in con.execute("""SELECT DISTINCT t.school FROM core.team_seasons ts JOIN core.teams t USING (canonical_team_id)
                                              WHERE ts.season = ? ORDER BY 1""", [s]).fetchall()]
        jobs += [("substitutions/team", {"season": s, "team": t}, kw(s, f"team_season:{t}")) for t in teams]
    for s in sel["lineups"]:
        gids = [int(r[0].replace("id=", "")) for r in con.execute(
            "SELECT source_record_key FROM core.games WHERE season = ? ORDER BY scheduled_tip_utc, 1", [s]).fetchall()]
        jobs += [("lineups/game", {"gameId": g}, kw(s, "game")) for g in gids]
    jobs += [("teams/roster", {"season": s}, kw(s, "season:all_teams")) for s in D.SEASONS]
    jobs += [("recruiting/players", {"year": y}, kw(y + 1, f"class:{y}")) for y in RECRUIT_YEARS]
    jobs += [("recruiting/teams", {"year": y}, kw(y + 1, f"class:{y}")) for y in RECRUIT_YEARS]
    jobs += [("recruiting/portal", {"year": y}, kw(y + 1, f"portal:{y}")) for y in PORTAL_YEARS]
    jobs += [("venues", {}, kw(None, "all"))]
    con.close()
    return jobs


def run(workers: int = 3, min_interval: float | None = None) -> dict:
    from cbb_platform.cbbd_client import Client, compile_manifest
    import time
    t0 = time.time()
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    seasons = dict(con.execute("SELECT replace(source_record_key, 'id=', ''), season FROM core.games").fetchall())
    con.close()
    # same answer as lockbox.canonical_game_season, cached in memory for the worker threads
    client = Client(known_game_season=lambda gid: seasons.get(str(gid)),
                    **({"min_interval": min_interval} if min_interval else {}))
    jobs = plan()
    # Discovery/equivalence payloads already answer some of these requests (same endpoint + params):
    # the client checkpoint skips them, so nothing is fetched twice.
    small = [j for j in jobs if j[0] != "plays/date"]
    big = [j for j in jobs if j[0] == "plays/date"]
    try:
        client.run_many(small, workers=workers, label="backfill-small", progress_every=500)
        client.run_many(big, workers=workers, label="backfill-pbp-dates", progress_every=50)
    finally:
        compile_manifest()
        stats = {k: v for k, v in client.stats.items() if k != "started_at"}
        stats["runtime_s"] = round(time.time() - t0, 1)
        stats["quota_remaining"] = client.quota_remaining
        runs = C.MANIFESTS / "cbbd_backfill_runs.jsonl"
        with runs.open("a") as f:
            f.write(json.dumps(stats) + "\n")
        print(json.dumps(stats))
    return stats


if __name__ == "__main__":
    if sys.argv[1:2] == ["plan"]:
        from collections import Counter
        print(selected_seasons())
        print(Counter(j[0] for j in plan()))
    elif sys.argv[1:2] == ["run"]:
        run(int(sys.argv[2]) if len(sys.argv) > 2 else 3, float(sys.argv[3]) if len(sys.argv) > 3 else None)
    else:
        print(__doc__)
