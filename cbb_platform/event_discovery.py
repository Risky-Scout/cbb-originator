"""Stage 2A -- coverage-first discovery of CBBD event-level data, 2004-05 .. 2024-25 (lockbox excluded).

    .venv/bin/python -m cbb_platform.event_discovery sample     # write the fixed sample manifest (offline)
    .venv/bin/python -m cbb_platform.event_discovery fetch      # live: 3 calls per sampled game + escalation
    .venv/bin/python -m cbb_platform.event_discovery analyze    # offline: raw payloads -> QA artifacts

Sample (deterministic, no RNG): for every development season, candidate games are canonical D-I vs D-I
games with a team box score. Each is assigned a PHASE and a conference TIER:

    PHASE  EARLY  regular season, Nov-Dec          TIER  MAJOR  ACC, Big 12, Big East, Big Ten, Pac-12, SEC
           MID    regular season, Jan                    MID    A-10, American, MWC, WCC, MVC, CUSA, MAC,
           LATE   regular season, Feb-Apr                       Sun Belt, CAA, Horizon, WAC
           POST   season_type = postseason               SMALL  every other D-I conference
    (tier = the HOME team's season conference)

One game per (phase, tier) cell -- 12 cells -- plus one NEUTRAL-site game, each chosen as the candidate
with the smallest sha256(SEED | cbbd_game_id). The selection is persisted in
data/qa/cbbd_event_coverage_sample_manifest.json before any call is made.

Escalation: a season whose PBP sample shows NO coverage is probed again with /plays/date on
ESCALATION_DATES of its busiest dates (each date covers dozens of games), so absence is never declared
from a handful of games.

Classification per (season, family), from successful requests only:
    MEASURED_PRESENT                 every tested game returned rows
    MEASURED_PARTIAL                 some tested games returned rows
    NO_COVERAGE_OBSERVED_IN_SAMPLE   every request succeeded and none returned rows
    REQUEST_FAILED                   no request for this family succeeded
    NOT_TESTED                       no request was made
A sample finding is never a full-season coverage claim.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict

import duckdb

from cbb_platform import config as C, event_metrics as M, raw_store

SEED = "stage2-discovery-v1"
SEASONS = list(range(2005, 2026))           # end-year convention; 2026 (2025-26) is the lockbox
MAJOR = ("ACC", "Big 12", "Big East", "Big Ten", "Pac-12", "SEC")
MID = ("A-10", "American", "Mountain West", "WCC", "MVC", "CUSA", "MAC", "Sun Belt", "CAA", "Horizon", "WAC")
PHASES = ("EARLY", "MID", "LATE", "POST")
TIERS = ("MAJOR", "MID", "SMALL")
GAME_ENDPOINTS = ("plays/game", "substitutions/game", "lineups/game")
ESCALATION_DATES = 6

SAMPLE_MANIFEST = C.QA / "cbbd_event_coverage_sample_manifest.json"
OUT_CSV = C.QA / "cbbd_event_coverage_discovery.csv"
OUT_JSON = C.QA / "cbbd_event_coverage_discovery.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "CBBD_EVENT_COVERAGE_DISCOVERY.md"

CANDIDATES_SQL = f"""
WITH g AS (
    SELECT g.*, CAST(replace(g.source_record_key, 'id=', '') AS BIGINT) AS cbbd_game_id
    FROM core.games g
    WHERE g.season BETWEEN {SEASONS[0]} AND {SEASONS[-1]} AND g.game_universe = 'DI_VS_DI' AND g.has_team_box_score)
SELECT g.season, g.cbbd_game_id, g.canonical_game_id, g.game_date_et, g.season_type, g.neutral_site,
       c.abbreviation AS home_conf,
       CASE WHEN g.season_type = 'postseason' THEN 'POST'
            WHEN month(g.game_date_et) IN (11, 12, 10) THEN 'EARLY'
            WHEN month(g.game_date_et) = 1 THEN 'MID'
            ELSE 'LATE' END AS phase,
       CASE WHEN c.abbreviation IN {MAJOR} THEN 'MAJOR' WHEN c.abbreviation IN {MID} THEN 'MID' ELSE 'SMALL' END AS tier,
       sha256('{SEED}|' || g.cbbd_game_id) AS h
FROM g
LEFT JOIN core.team_seasons ts ON ts.canonical_team_id = g.home_team_id AND ts.season = g.season
LEFT JOIN core.conferences c ON c.canonical_conference_id = ts.canonical_conference_id
"""


def build_sample(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(f"CREATE OR REPLACE TEMP TABLE disc_cand AS {CANDIDATES_SQL}")
    rows = con.execute("""
        SELECT season, phase, tier, arg_min(cbbd_game_id, h), arg_min(CAST(game_date_et AS VARCHAR), h),
               arg_min(home_conf, h), arg_min(neutral_site, h), count(*)
        FROM disc_cand GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall()
    neutral = con.execute("""SELECT season, arg_min(cbbd_game_id, h), arg_min(CAST(game_date_et AS VARCHAR), h),
                                    arg_min(home_conf, h), arg_min(phase, h), arg_min(tier, h), count(*)
                             FROM disc_cand WHERE neutral_site GROUP BY 1 ORDER BY 1""").fetchall()
    sample = defaultdict(list)
    for season, phase, tier, gid, d, conf, ns, n in rows:
        sample[season].append({"cell": f"{phase}/{tier}", "cbbd_game_id": gid, "date_et": d, "home_conf": conf,
                               "neutral_site": ns, "cell_candidates": n})
    for season, gid, d, conf, phase, tier, n in neutral:
        if gid not in {s["cbbd_game_id"] for s in sample[season]}:
            sample[season].append({"cell": f"NEUTRAL({phase}/{tier})", "cbbd_game_id": gid, "date_et": d,
                                   "home_conf": conf, "neutral_site": True, "cell_candidates": n})
    empty_cells = {s: sorted({f"{p}/{t}" for p in PHASES for t in TIERS} - {x["cell"] for x in sample[s]})
                   for s in SEASONS}
    # busiest ET dates per season (for escalation probes): deterministic order (games desc, date asc)
    busiest = defaultdict(list)
    for season, d, n in con.execute("""SELECT season, CAST(game_date_et AS VARCHAR), count(*) n FROM disc_cand
                                       GROUP BY 1, 2 ORDER BY 1, n DESC, 2""").fetchall():
        busiest[season].append([d, n])
    man = {"seed": SEED, "seasons": SEASONS, "tiers": {"MAJOR": MAJOR, "MID": MID, "SMALL": "all other D-I"},
           "phases": PHASES, "endpoints_per_game": GAME_ENDPOINTS,
           "sample": {str(s): sorted(sample[s], key=lambda x: x["cell"]) for s in SEASONS},
           "empty_cells": {str(s): v for s, v in empty_cells.items()},
           "escalation_dates": {str(s): spread(busiest[s], ESCALATION_DATES) for s in SEASONS}}
    return man


def spread(dates_by_volume: list, k: int) -> list:
    """k busy dates spread across the season: the busiest date of each of k chronological slices."""
    if not dates_by_volume:
        return []
    chron = sorted(dates_by_volume)
    size = max(1, len(chron) // k)
    out = []
    for i in range(0, len(chron), size):
        sl = chron[i:i + size]
        out.append(max(sl, key=lambda x: (x[1], x[0])))
    return sorted(out)[:k]


def write_sample() -> dict:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    try:
        man = build_sample(con)
    finally:
        con.close()
    SAMPLE_MANIFEST.write_text(json.dumps(man, indent=1, default=str))
    return man


def plan(man: dict) -> list[tuple[str, dict, dict]]:
    jobs = []
    for s in SEASONS:
        for g in man["sample"][str(s)]:
            for ep in GAME_ENDPOINTS:
                jobs.append((ep, {"gameId": g["cbbd_game_id"]},
                             {"purpose": "stage2a_discovery", "season": s, "scope": f"game:{g['cell']}"}))
    # one roster call per season returns every team of that season
    jobs += [("teams/roster", {"season": s}, {"purpose": "stage2a_discovery", "season": s, "scope": "season:all_teams"})
             for s in SEASONS]
    return jobs


def utc_dates_for_et(d: str) -> list[str]:
    """/plays/date takes a UTC date. Evening ET games spill into the next UTC date, so an ET date is
    covered by its own UTC date plus the next one."""
    from datetime import date, timedelta
    x = date.fromisoformat(d)
    return [x.isoformat(), (x + timedelta(days=1)).isoformat()]


def fetch(workers: int = 2) -> None:
    from cbb_platform.cbbd_client import Client, compile_manifest
    man = json.loads(SAMPLE_MANIFEST.read_text())
    client = Client()
    client.run_many(plan(man), workers=workers, label="discovery")
    # escalation for seasons with no PBP in the game sample
    res = analyze(write=False)
    esc = []
    for s in SEASONS:
        if res["seasons"][str(s)]["families"]["pbp"]["classification"] == "NO_COVERAGE_OBSERVED_IN_SAMPLE":
            for d, _n in man["escalation_dates"][str(s)]:
                esc.append(("plays/date", {"date": d}, {"purpose": "stage2a_discovery_escalation", "season": s,
                                                         "scope": f"date:{d}"}))
    if esc:
        client.run_many(esc, workers=workers, label="discovery-escalation")
    compile_manifest()
    print(json.dumps({k: v for k, v in client.stats.items() if k != "started_at"}))


# ---------------------------------------------------------------------- analysis (offline)
def stored_payloads(purposes: tuple[str, ...]) -> dict:
    """(endpoint, request_key) -> (payload, meta) for stored requests with one of `purposes`."""
    from cbb_platform.cbbd_client import REQUEST_LOG
    out = {}
    if not REQUEST_LOG.exists():
        return out
    for line in REQUEST_LOG.read_text().splitlines():
        e = json.loads(line)
        if e.get("purpose") in purposes and e.get("status") in ("STORED", "ALREADY_STORED"):
            out[e["request_key"]] = e
    return out


def _load(entry) -> list:
    return json.loads(raw_store.read_payload(C.RAW / entry["stored_file"]))


def classify(tested: int, present: int, failed: int) -> str:
    if tested == 0:
        return "REQUEST_FAILED" if failed else "NOT_TESTED"
    if present == 0:
        return "NO_COVERAGE_OBSERVED_IN_SAMPLE"
    return "MEASURED_PRESENT" if present == tested else "MEASURED_PARTIAL"


def analyze(write: bool = True) -> dict:
    from cbb_platform.cbbd_client import REQUEST_LOG, request_key
    man = json.loads(SAMPLE_MANIFEST.read_text())
    log = defaultdict(list)
    if REQUEST_LOG.exists():
        for line in REQUEST_LOG.read_text().splitlines():
            e = json.loads(line)
            if e.get("purpose", "").startswith("stage2a_discovery"):
                log[e["request_key"]].append(e)

    def outcome(ep, params):
        es = log.get(request_key(ep, params), [])
        ok = [e for e in es if e["status"] in ("STORED", "ALREADY_STORED")]
        if ok:
            return "OK", ok[-1]
        return ("FAILED", es[-1]) if es else ("NOT_TESTED", None)

    rows, seasons = [], {}
    for s in SEASONS:
        fam = {f: {"tested": 0, "present": 0, "failed": 0} for f in ("pbp", "onfloor", "shotinfo", "substitutions", "lineups")}
        q = defaultdict(list)
        for g in man["sample"][str(s)]:
            gid = g["cbbd_game_id"]
            row = {"season": s, "cell": g["cell"], "cbbd_game_id": gid, "date_et": g["date_et"], "home_conf": g["home_conf"]}
            st, e = outcome("plays/game", {"gameId": gid})
            row["pbp_request"] = st
            if st == "OK":
                pm = M.play_metrics(_load(e))
                row.update({f"pbp_{k}": v for k, v in pm.items()})
                fam["pbp"]["tested"] += 1
                fam["pbp"]["present"] += int(pm["plays"] > 0)
                if pm["plays"] > 0:
                    fam["onfloor"]["tested"] += 1
                    fam["onfloor"]["present"] += int(pm["plays_with_onfloor"] > 0)
                    q["onfloor_share"].append(pm["plays_with_onfloor"] / pm["plays"])
                    q["valid_5v5_share"].append(pm["onfloor_valid_5v5"] / pm["plays"])
                    if pm["shooting_plays"]:
                        fam["shotinfo"]["tested"] += 1
                        fam["shotinfo"]["present"] += int(pm["shooting_plays_with_shotinfo"] > 0)
                        q["shotinfo_share"].append(pm["shooting_plays_with_shotinfo"] / pm["shooting_plays"])
                        if pm["field_goal_shots"]:
                            q["xy_share_fg"].append(pm["field_goal_shots_with_xy"] / pm["field_goal_shots"])
            elif st == "FAILED":
                fam["pbp"]["failed"] += 1
            for ep, f in (("substitutions/game", "substitutions"), ("lineups/game", "lineups")):
                st, e = outcome(ep, {"gameId": gid})
                row[f"{f}_request"] = st
                if st == "OK":
                    n = len(_load(e))
                    row[f"{f}_rows"] = n
                    fam[f]["tested"] += 1
                    fam[f]["present"] += int(n > 0)
                elif st == "FAILED":
                    fam[f]["failed"] += 1
            rows.append(row)
        for f in fam.values():
            f["classification"] = classify(f["tested"], f["present"], f["failed"])
        # escalation (/plays/date on busy dates) -- only for seasons whose game sample showed no PBP
        esc = []
        for d, n in man["escalation_dates"][str(s)]:
            st, e = outcome("plays/date", {"date": d})
            if st != "NOT_TESTED":
                pl = _load(e) if st == "OK" else None
                esc.append({"date": d, "et_games_that_date": n, "request": st,
                            "plays": len(pl) if pl is not None else None,
                            "games_with_plays": len({p["gameId"] for p in pl}) if pl else 0})
        seasons[str(s)] = {"games_sampled": len(man["sample"][str(s)]), "empty_cells": man["empty_cells"][str(s)],
                           "families": fam, "escalation_plays_date": esc,
                           "quality_in_sample": {k: {"min": round(min(v), 4), "mean": round(sum(v) / len(v), 4)}
                                                 for k, v in q.items() if v}}
        if esc and all(x["request"] == "OK" for x in esc):
            seasons[str(s)]["families"]["pbp"]["escalation_games_with_plays"] = sum(x["games_with_plays"] for x in esc)
    # rosters (one call per season returns every team) are part of discovery coverage
    for s in SEASONS:
        st, e = outcome("teams/roster", {"season": s})
        if st == "OK":
            r = _load(e)
            seasons[str(s)]["families"]["rosters"] = {
                "teams": len(r), "players": sum(len(t.get("players") or []) for t in r),
                "classification": "MEASURED_PRESENT" if r else "NO_COVERAGE_OBSERVED_IN_SAMPLE"}
        else:
            seasons[str(s)]["families"]["rosters"] = {"classification": "REQUEST_FAILED" if st == "FAILED" else "NOT_TESTED"}
    result = {"seed": SEED, "sample_manifest": str(SAMPLE_MANIFEST.relative_to(C.PROJECT_ROOT)), "seasons": seasons,
              "warning": "Sample findings. Never a claim of full-season coverage; see cbbd_event_coverage_by_season.csv "
                         "for measured full-backfill coverage."}
    if write:
        OUT_JSON.write_text(json.dumps(result, indent=1, default=str))
        cols = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("season", "cell", "cbbd_game_id", "date_et", "home_conf"), k))
        with OUT_CSV.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        OUT_MD.write_text(render_md(result))
    return result


def render_md(res: dict) -> str:
    fams = ("pbp", "onfloor", "shotinfo", "substitutions", "lineups", "rosters")
    ab = {"MEASURED_PRESENT": "PRESENT", "MEASURED_PARTIAL": "PARTIAL", "NO_COVERAGE_OBSERVED_IN_SAMPLE": "NONE_IN_SAMPLE",
          "REQUEST_FAILED": "REQ_FAILED", "NOT_TESTED": "NOT_TESTED"}
    out = ["# CBBD event coverage discovery (Stage 2A)", "",
           "Rendered from `data/qa/cbbd_event_coverage_discovery.json` by `cbb_platform/event_discovery.py`. "
           "Do not edit by hand.", "",
           "**These are SAMPLE findings** (a fixed, hash-ordered stratified sample of ~13 games per season; "
           "manifest `data/qa/cbbd_event_coverage_sample_manifest.json`). They decide *where to backfill*; they are "
           "never a claim of full-season coverage. Measured full-season coverage is in "
           "`data/qa/cbbd_event_coverage_by_season.csv` (Stage 2O).", "",
           "Cell = present/tested games (failed requests are counted separately and never as absence).", "",
           "| season | games | " + " | ".join(fams) + " | onFloor share (mean) | valid 5v5 share (mean) | shotInfo share (mean) |",
           "|---|---|" + "---|" * (len(fams) + 3)]
    for s, r in res["seasons"].items():
        cells = []
        for f in fams:
            x = r["families"][f]
            if f == "rosters":
                cells.append(f"{ab[x['classification']]} ({x.get('teams', 0)} teams)")
            else:
                fail = f", {x['failed']} failed" if x["failed"] else ""
                cells.append(f"{ab[x['classification']]} {x['present']}/{x['tested']}{fail}")
        qq = r["quality_in_sample"]
        g = lambda k: f"{qq[k]['mean']:.3f}" if k in qq else "-"
        out.append(f"| {s} | {r['games_sampled']} | " + " | ".join(cells) +
                   f" | {g('onfloor_share')} | {g('valid_5v5_share')} | {g('shotinfo_share')} |")
    out += ["", "## Escalation probes (/plays/date on busy dates)", "",
            "Run only for seasons whose game sample returned no PBP. Each date covers dozens of games.", ""]
    any_esc = False
    for s, r in res["seasons"].items():
        for e in r["escalation_plays_date"]:
            any_esc = True
            out.append(f"- {s} {e['date']} (ET games that date: {e['et_games_that_date']}): request {e['request']}, "
                       f"plays {e['plays']}, games with plays {e['games_with_plays']}")
    if not any_esc:
        out.append("- none needed")
    out += ["", "## Empty strata", ""]
    for s, r in res["seasons"].items():
        if r["empty_cells"]:
            out.append(f"- {s}: no candidate game in {', '.join(r['empty_cells'])}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["sample"]:
        m = write_sample()
        print(sum(len(v) for v in m["sample"].values()), "games sampled")
    elif cmd == ["fetch"]:
        fetch(int(sys.argv[2]) if len(sys.argv) > 2 else 2)
    elif cmd == ["analyze"]:
        r = analyze()
        for s, x in r["seasons"].items():
            print(s, {f: v["classification"] for f, v in x["families"].items()})
    else:
        print(__doc__)


# ---------------------------------------------------------------------- team-season escalation
# Substitutions/lineups that show no coverage in the game sample are re-probed with the team-SEASON
# endpoints (one call covers ~30 games of a team). Teams: the HOME teams of the EARLY/MAJOR, EARLY/MID,
# EARLY/SMALL and POST/MAJOR sample games (deterministic, from the persisted sample manifest).
TEAM_ESCALATION_CELLS = ("EARLY/MAJOR", "EARLY/MID", "EARLY/SMALL", "POST/MAJOR")


def escalation_teams(man: dict) -> dict:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    try:
        out = {}
        for s in SEASONS:
            out[s] = []
            for g in man["sample"][str(s)]:
                if g["cell"] in TEAM_ESCALATION_CELLS:
                    school = con.execute("""SELECT t.school FROM core.games g JOIN core.teams t ON t.canonical_team_id = g.home_team_id
                                            WHERE g.source_record_key = ?""", [f"id={g['cbbd_game_id']}"]).fetchone()[0]
                    out[s].append(school)
        return out
    finally:
        con.close()


def fetch_team_escalation(workers: int = 2) -> None:
    from cbb_platform.cbbd_client import Client, compile_manifest
    man = json.loads(SAMPLE_MANIFEST.read_text())
    res = analyze(write=False)
    teams = escalation_teams(man)
    jobs = []
    for s in SEASONS:
        fams = res["seasons"][str(s)]["families"]
        for fam, ep in (("substitutions", "substitutions/team"), ("lineups", "lineups/team")):
            if fams[fam]["classification"] != "MEASURED_PRESENT":
                jobs += [(ep, {"season": s, "team": t}, {"purpose": "stage2a_discovery_team_escalation", "season": s,
                                                         "scope": f"team_season:{t}"}) for t in teams[s]]
    Client().run_many(jobs, workers=workers, label="team-escalation")
    compile_manifest()


def team_escalation_results() -> dict:
    from cbb_platform.cbbd_client import REQUEST_LOG
    out = defaultdict(lambda: defaultdict(list))
    for line in REQUEST_LOG.read_text().splitlines():
        e = json.loads(line)
        if e.get("purpose") == "stage2a_discovery_team_escalation":
            ok = e["status"] in ("STORED", "ALREADY_STORED")
            rows = _load(e) if ok else None
            out[str(e["season"])][e["endpoint"]].append(
                {"team": e["params"]["team"], "request": "OK" if ok else "FAILED", "rows": len(rows) if ok else None,
                 "games": len({r.get("gameId") for r in rows}) if ok and rows and "gameId" in rows[0] else None})
    return {s: dict(v) for s, v in out.items()}
