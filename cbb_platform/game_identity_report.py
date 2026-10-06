"""Stage 2.1 reports, rendered from the built database (offline).

    .venv/bin/python -m cbb_platform.game_identity_report

    data/qa/game_identity_resolution.csv        every metadata.game_alias_map row
    data/qa/game_identity_aggregate_impact.csv  every team-season whose all-results aggregates changed
    data/qa/game_identity_resolution.json       summary (+ fact preservation before/after)
    docs/GAME_IDENTITY_RESOLUTION.md

"old" = the all-results aggregate over EVERY core.games source record (the Stage 2.0 definition);
"new" = the same aggregate over the trusted (deduplicated) relation flagship.games -- the identity effect only.
"""
from __future__ import annotations

import hashlib
import json

import duckdb

from cbb_platform import config as C

RES_CSV = C.QA / "game_identity_resolution.csv"
IMPACT_CSV = C.QA / "game_identity_aggregate_impact.csv"
OUT_JSON = C.QA / "game_identity_resolution.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "GAME_IDENTITY_RESOLUTION.md"
BEFORE = C.QA / "game_identity_before_counts_v2.0.json"

OLD_SQL = """
WITH results AS (
    SELECT home_team_id AS team, season, home_points AS pts, away_points AS opp, canonical_game_id FROM core.games
    WHERE home_points IS NOT NULL AND away_points IS NOT NULL
    UNION ALL SELECT away_team_id, season, away_points, home_points, canonical_game_id FROM core.games
    WHERE home_points IS NOT NULL AND away_points IS NOT NULL)
SELECT team, results.season, count(*) AS n, count(*) FILTER (WHERE pts > opp) AS w, count(*) FILTER (WHERE pts < opp) AS l,
       sum(pts) AS pf, sum(opp) AS pa,
       count(*) FILTER (WHERE results.canonical_game_id IN (SELECT source_canonical_game_id FROM metadata.game_alias_map
                                                    WHERE excluded_from_trusted_games)) AS removed,
       list(DISTINCT m.source_game_id ORDER BY m.source_game_id) FILTER (WHERE m.source_game_id IS NOT NULL) AS involved
FROM results LEFT JOIN metadata.game_alias_map m ON m.source_canonical_game_id = results.canonical_game_id
     AND m.resolution_class <> 'LEGITIMATE_DISTINCT_GAME'
GROUP BY 1, 2
"""


NEW_SQL = """
WITH results AS (
    SELECT home_team_id AS team, season, home_points AS pts, away_points AS opp FROM flagship.games
    WHERE home_points IS NOT NULL AND away_points IS NOT NULL
    UNION ALL SELECT away_team_id, season, away_points, home_points FROM flagship.games
    WHERE home_points IS NOT NULL AND away_points IS NOT NULL)
SELECT team AS canonical_team_id, season, count(*) AS games_with_result, count(*) FILTER (WHERE pts > opp) AS wins,
       count(*) FILTER (WHERE pts < opp) AS losses, sum(pts) AS points_all_results, sum(opp) AS opp_points_all_results
FROM results GROUP BY 1, 2
"""


def impact_rows(con) -> list[dict]:
    # Stage 2.1 identity effect ONLY: all source records vs the trusted (deduplicated) relation flagship.games.
    # (fact.team_season_game_derived additionally excludes unplayed records since Stage 2.2.)
    cur = con.execute(f"""
        SELECT t.school AS team, o.team AS canonical_team_id, o.season,
               o.n AS old_games, d.games_with_result AS new_games, o.w AS old_wins, d.wins AS new_wins,
               o.l AS old_losses, d.losses AS new_losses, o.pf AS old_points_for, d.points_all_results AS new_points_for,
               o.pa AS old_points_against, d.opp_points_all_results AS new_points_against,
               o.removed AS removed_duplicate_contributions, array_to_string(o.involved, ' ') AS source_game_ids_involved
        FROM ({OLD_SQL}) o JOIN ({NEW_SQL}) d ON d.canonical_team_id = o.team AND d.season = o.season
        JOIN fact.team_season_game_derived x ON x.canonical_team_id = o.team AND x.season = o.season
        JOIN core.teams t ON t.canonical_team_id = o.team
        WHERE (o.n, o.w, o.l, o.pf, o.pa) IS DISTINCT FROM (d.games_with_result, d.wins, d.losses, d.points_all_results, d.opp_points_all_results)
        ORDER BY o.season, t.school""")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fact_preservation(con) -> dict:
    before = json.loads(BEFORE.read_text())["counts"] if BEFORE.exists() else {}
    out = {}
    for t, fv in (("fact.player_games", "flagship.player_game_outcomes"), ("fact.team_games", "flagship.team_game_outcomes"),
                  ("fact.participation", "flagship.participation"), ("fact.plays", None), ("fact.shots", None),
                  ("fact.substitutions", None), ("fact.lineup_stats", "flagship.lineup_stats")):
        raw = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        on_excluded = con.execute(f"""SELECT count(*) FROM {t} f JOIN metadata.game_alias_map m
            ON m.source_canonical_game_id = f.canonical_game_id AND m.excluded_from_trusted_games""").fetchone()[0]
        mapped = con.execute(f"""SELECT count(*) FROM {t} f JOIN metadata.trusted_game_map r
            ON r.source_canonical_game_id = f.canonical_game_id""").fetchone()[0]
        out[t] = {"v2_0_rows": before.get(t), "v2_1_rows": raw, "rows_on_alias_or_corrupt_records": on_excluded,
                  "rows_resolving_to_trusted_games": mapped,
                  "rows_without_canonical_game": raw - mapped - on_excluded,
                  "trusted_view": fv, "trusted_view_rows": con.execute(f"SELECT count(*) FROM {fv}").fetchone()[0] if fv else None}
    return out


def _pre_window(con) -> dict:
    """Seasons before the audited window: measured, NOT adjudicated, and outside the trusted relation."""
    from collections import Counter
    from cbb_platform import game_identity_audit as GA
    lo = C.AUDITED_SEASONS[0]
    df = con.execute(GA.CANDIDATES_SQL.replace("WHERE g.season BETWEEN 2005 AND 2025", f"WHERE g.season < {lo}")).df()
    cls = Counter()
    for r in df.to_dict("records"):
        r = {k: (None if (v is not None and not isinstance(v, (str, list)) and v != v) else v) for k, v in r.items()}
        cls[GA.classify(r)[0]] += 1
    return {"seasons": f"< {lo}", "raw_records": con.execute(f"SELECT count(*) FROM core.games WHERE season < {lo}").fetchone()[0],
            "candidate_pairs": len(df), "by_class": dict(cls),
            "status": "raw-only in core.games; excluded from flagship.games (identity not audited/adjudicated)"}


def run(db_path=None) -> dict:
    import csv
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    cur = con.execute("SELECT * FROM metadata.game_alias_map ORDER BY CAST(source_game_id AS BIGINT)")
    cols = [c[0] for c in cur.description]
    amap = [dict(zip(cols, r)) for r in cur.fetchall()]
    impact = impact_rows(con)
    pres = fact_preservation(con)
    summary = {
        "resolution_hash": hashlib.sha256(json.dumps([[r[c] for c in cols if c != "created_by_version"] for r in amap],
                                                     default=str).encode()).hexdigest(),
        "map_rows": len(amap),
        "by_class": dict(con.execute("SELECT resolution_class, count(*) FROM metadata.game_alias_map GROUP BY 1 ORDER BY 1").fetchall()),
        "by_method": dict(con.execute("SELECT adjudication_method, count(*) FROM metadata.game_alias_map WHERE resolution_class <> 'LEGITIMATE_DISTINCT_GAME' GROUP BY 1 ORDER BY 1").fetchall()),
        "raw_source_games": con.execute("SELECT count(*) FROM core.games").fetchone()[0],
        "trusted_games": con.execute("SELECT count(*) FROM flagship.games").fetchone()[0],
        "trusted_games_dev_seasons": con.execute("SELECT count(*) FROM flagship.games WHERE season BETWEEN 2005 AND 2025").fetchone()[0],
        "raw_games_dev_seasons": con.execute("SELECT count(*) FROM core.games WHERE season BETWEEN 2005 AND 2025").fetchone()[0],
        "aliases": con.execute("SELECT count(*) FROM metadata.game_alias_map WHERE resolution_class = 'DUPLICATE_ALIAS'").fetchone()[0],
        "quarantined_corrupt": con.execute("SELECT count(*) FROM metadata.game_alias_map WHERE resolution_class = 'SOURCE_CORRUPT_MISATTRIBUTED'").fetchone()[0],
        "affected_team_seasons": len(impact),
        "removed_duplicate_contributions": sum(r["removed_duplicate_contributions"] for r in impact),
        "aggregate_totals_old_new": {k: [sum(r["old_" + k] for r in impact), sum(r["new_" + k] for r in impact)]
                                     for k in ("games", "wins", "losses", "points_for", "points_against")},
        "fact_preservation": pres,
        "unaudited_pre_window": _pre_window(con),
        "wallclock_policy": "UNTRUSTED_MIXED",
    }
    con.close()
    with RES_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(amap)
    with IMPACT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(impact[0]) if impact else ["team"])
        w.writeheader()
        w.writerows(impact)
    OUT_JSON.write_text(json.dumps(summary, indent=1, default=str))
    OUT_MD.write_text(render(summary, amap, impact))
    return summary


def render(s: dict, amap: list[dict], impact: list[dict]) -> str:
    L = ["# Canonical game identity resolution (Stage 2.1)", "",
         "Rendered from the built database by `cbb_platform/game_identity_report.py`. Rule: **one real basketball contest "
         "= one trusted canonical game.** `core.games` keeps every source record unchanged; `metadata.game_alias_map` "
         "states which records are the same contest; `flagship.games` (and `metadata.trusted_game_map`, through which every "
         "flagship fact view and every game-derived aggregate is routed) contains each real contest exactly once.", "",
         f"* raw source game records: {s['raw_source_games']:,} -> trusted games: {s['trusted_games']:,} "
         f"(development seasons: {s['raw_games_dev_seasons']:,} -> {s['trusted_games_dev_seasons']:,})",
         f"* map rows: {s['map_rows']} {s['by_class']}",
         f"* methods (non-legitimate rows): {s['by_method']}",
         f"* aliases: {s['aliases']}; quarantined corrupt records: {s['quarantined_corrupt']}",
         f"* affected team-seasons: {s['affected_team_seasons']}; removed duplicate contributions: {s['removed_duplicate_contributions']}",
         f"* aggregate totals over affected team-seasons, old -> new: {s['aggregate_totals_old_new']}",
         f"* resolution hash: `{s['resolution_hash']}`", "",
         "## Representative selection", "",
         "TRUE_DUPLICATE: (1) verified box attachment, (2) richer event data, (3) complete non-zero score, (4) smaller "
         "source id. Every pair was decided at (1). Event facts are built after resolution; the build then STOPS if any "
         "alias or corrupt record carries facts (none do), so (2) could not have changed an outcome.", "",
         "POSSIBLE_DUPLICATE and UNRESOLVED pairs follow the architect's Stage 2.1 adjudication. The build STOPS if the "
         "adjudicated result contradicts the box-attached record's score.", "",
         "## Fact preservation (v2.0 -> v2.1)", "", "| table | v2.0 rows | v2.1 rows | rows on alias/corrupt records | rows resolving to trusted games | trusted view rows |",
         "|---|---|---|---|---|---|"]
    for t, v in s["fact_preservation"].items():
        L.append(f"| {t} | {v['v2_0_rows']} | {v['v2_1_rows']} | {v['rows_on_alias_or_corrupt_records']} | "
                 f"{v['rows_resolving_to_trusted_games']} | {v['trusted_view_rows'] if v['trusted_view_rows'] is not None else '-'} |")
    L += ["", "`rows_without_canonical_game` (in the JSON) are event rows of the two CBBD games absent from games.csv, already "
          "quarantined in Stage 2 as GAME_NOT_IN_CANONICAL; they were never part of a trusted game.", "",
          "## Every non-legitimate pair", "", "| source game | class | resolves to | method | evidence |", "|---|---|---|---|---|"]
    for r in amap:
        if r["resolution_class"] != "LEGITIMATE_DISTINCT_GAME":
            L.append(f"| {r['source_game_id']} | {r['resolution_class']} | {r['resolved_canonical_game_id'] or 'EXCLUDED'} | "
                     f"{r['adjudication_method']} | {r['adjudication_evidence']} |")
    L += ["", "## Aggregate impact", "", "Full table: `data/qa/game_identity_aggregate_impact.csv`.", "",
          "| team | season | games old->new | W old->new | L old->new | PF old->new | PA old->new | removed | source ids |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in impact:
        L.append(f"| {r['team']} | {r['season']} | {r['old_games']}->{r['new_games']} | {r['old_wins']}->{r['new_wins']} | "
                 f"{r['old_losses']}->{r['new_losses']} | {r['old_points_for']}->{r['new_points_for']} | "
                 f"{r['old_points_against']}->{r['new_points_against']} | {r['removed_duplicate_contributions']} | {r['source_game_ids_involved']} |")
    u = s["unaudited_pre_window"]
    L += ["", "## Scope of the trusted relation", "",
          f"`flagship.games` covers the audited development seasons {C.AUDITED_SEASONS[0]}..{C.AUDITED_SEASONS[1]} "
          f"(end-year). Seasons {u['seasons']} ({u['raw_records']:,} raw records) remain raw-only in `core.games`: the same "
          f"duplicate pattern exists there ({u['candidate_pairs']} candidate pairs, {u['by_class']}), it has not been "
          "adjudicated, and no box or event data exist for those seasons."]
    L += ["", "## Wallclock (architect ruling, closed)", "",
          "CBBD raw wallclock is **UNTRUSTED_MIXED**: preserved verbatim, prohibited for PIT cutoffs, feature availability, "
          "event latency, tip reconstruction and live timing. No correction is attempted."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k != "fact_preservation"}, indent=1, default=str))
