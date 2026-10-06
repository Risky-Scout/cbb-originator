"""Stage 2.2 reports, rendered from the built database (offline).

    .venv/bin/python -m cbb_platform.game_status_report

    data/qa/game_status_resolution.csv        every game excluded from played games (+ every 0-0 trusted game)
    data/qa/game_status_aggregate_impact.csv  team-seasons whose all-results aggregates changed (v2.1 -> v2.2)
    data/qa/game_status_resolution.json       summary
    docs/GAME_STATUS_RESOLUTION.md
"""
from __future__ import annotations

import csv
import hashlib
import json

import duckdb

from cbb_platform import config as C

RES_CSV = C.QA / "game_status_resolution.csv"
IMPACT_CSV = C.QA / "game_status_aggregate_impact.csv"
OUT_JSON = C.QA / "game_status_resolution.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "GAME_STATUS_RESOLUTION.md"
BEFORE = C.QA / "game_status_before_aggregates_v2.1.json"
MODEL_WINDOW = (2014, 2025)     # 2013-14 .. 2024-25 (candidate windows, Stage 2T)


def run(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    cur = con.execute("""SELECT s.*, th.school AS home_team, ta.school AS away_team, g.game_date_et, g.home_points, g.away_points, g.notes
        FROM metadata.game_status_resolution s JOIN flagship.games g USING (canonical_game_id)
        JOIN core.teams th ON th.canonical_team_id = g.home_team_id JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
        WHERE s.excluded_from_played_games OR (g.home_points = 0 AND g.away_points = 0)
        ORDER BY g.season, g.game_date_et, CAST(s.source_game_id AS BIGINT)""")
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    before = {(t, s): v for t, s, *v in json.loads(BEFORE.read_text())["team_season_game_derived"]}
    after = {(t, s): v for t, s, *v in con.execute("""SELECT canonical_team_id, season, games_with_result, wins, losses,
             points_all_results, opp_points_all_results FROM fact.team_season_game_derived""").fetchall()}
    names = dict(con.execute("SELECT canonical_team_id, school FROM core.teams").fetchall())
    excluded_by_ts = {}
    for r in rows:
        if r["excluded_from_played_games"]:
            for side in ("home", "away"):
                tid = con.execute(f"SELECT {side}_team_id FROM core.games WHERE canonical_game_id = ?", [r["canonical_game_id"]]).fetchone()[0]
                excluded_by_ts.setdefault((tid, r["season"]), []).append(r["source_game_id"])
    impact = []
    for k in sorted(set(before) | set(after), key=lambda k: (k[1], names.get(k[0], ""))):
        b, a = before.get(k), after.get(k)
        if b != a:
            impact.append({"team": names.get(k[0]), "canonical_team_id": k[0], "season": k[1],
                           "old_games": b[0] if b else None, "new_games": a[0] if a else None,
                           "old_wins": b[1] if b else None, "new_wins": a[1] if a else None,
                           "old_losses": b[2] if b else None, "new_losses": a[2] if a else None,
                           "old_points_for": b[3] if b else None, "new_points_for": a[3] if a else None,
                           "old_points_against": b[4] if b else None, "new_points_against": a[4] if a else None,
                           "removed_unplayed_contributions": len(excluded_by_ts.get(k, [])),
                           "source_game_ids_excluded": " ".join(sorted(excluded_by_ts.get(k, []), key=int))})
    zz = [r for r in rows if r["home_points"] == 0 and r["away_points"] == 0]
    lo, hi = MODEL_WINDOW
    summary = {
        "zero_zero_trusted_games": len(zz),
        "zero_zero_by_status": {s: sum(1 for r in zz if r["status"] == s) for s in
                                ("PLAYED_CONFIRMED", "UNPLAYED_OR_CANCELLED", "SCHEDULE_PLACEHOLDER", "SOURCE_STATUS_UNRESOLVED")},
        "zero_zero_seasons": sorted({r["season"] for r in zz}),
        "zero_zero_in_model_window": sum(1 for r in zz if lo <= r["season"] <= hi),
        "all_status_counts": dict(con.execute("SELECT status, count(*) FROM metadata.game_status_resolution GROUP BY 1 ORDER BY 1").fetchall()),
        "excluded_from_played_games": sum(1 for r in rows if r["excluded_from_played_games"]),
        "excluded_outside_zero_zero": [{k: r[k] for k in ("source_game_id", "season", "home_team", "away_team", "home_points",
                                                          "away_points", "status", "resolution_method")}
                                       for r in rows if r["excluded_from_played_games"] and not (r["home_points"] == 0 and r["away_points"] == 0)],
        "excluded_in_model_window": sum(1 for r in rows if r["excluded_from_played_games"] and lo <= r["season"] <= hi),
        "trusted_games": con.execute("SELECT count(*) FROM flagship.games").fetchone()[0],
        "played_games": con.execute("SELECT count(*) FROM flagship.played_games").fetchone()[0],
        "affected_team_seasons": len(impact),
        "affected_team_seasons_in_model_window": sum(1 for r in impact if lo <= r["season"] <= hi),
        "aggregate_totals_old_new": {k: [sum(r["old_" + k] or 0 for r in impact), sum(r["new_" + k] or 0 for r in impact)]
                                     for k in ("games", "wins", "losses", "points_for", "points_against")},
        "status_hash": hashlib.sha256(json.dumps(con.execute(
            "SELECT * EXCLUDE (created_by_version) FROM metadata.game_status_resolution ORDER BY canonical_game_id").fetchall(),
            default=str).encode()).hexdigest(),
        "pre_2005_policy": "OUT_OF_MODELING_SCOPE",
    }
    con.close()
    with RES_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with IMPACT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(impact[0]) if impact else ["team"])
        w.writeheader()
        w.writerows(impact)
    OUT_JSON.write_text(json.dumps(summary, indent=1, default=str))
    OUT_MD.write_text(render(summary, rows, impact))
    return summary


def render(s, rows, impact) -> str:
    L = ["# Game status resolution (Stage 2.2)", "",
         "Rendered from the built database by `cbb_platform/game_status_report.py`. Rule (architect): a game enters the "
         "trusted PLAYED-game modeling universe (`flagship.played_games`) only with POSITIVE evidence that it was played. "
         "A 0-0 score alone is not evidence. Raw schedule records are preserved (`core.games`, `flagship.games`); no score "
         "is fabricated and no winner inferred. Rules: `cbb_platform/game_status.py`.", "",
         f"* 0-0 trusted games audited: **{s['zero_zero_trusted_games']}** (seasons {s['zero_zero_seasons']}); by status: {s['zero_zero_by_status']}",
         f"* 0-0 games inside the 2013-14..2024-25 modeling window: **{s['zero_zero_in_model_window']}**",
         f"* all trusted games by status: {s['all_status_counts']}",
         f"* trusted games {s['trusted_games']:,} -> played games {s['played_games']:,} ({s['excluded_from_played_games']} excluded)",
         f"* excluded games that are NOT 0-0: {s['excluded_outside_zero_zero']}",
         f"* excluded games inside the modeling window: {s['excluded_in_model_window']}",
         f"* affected team-seasons: {s['affected_team_seasons']} (inside the modeling window: {s['affected_team_seasons_in_model_window']}); "
         f"totals old -> new: {s['aggregate_totals_old_new']}",
         f"* status hash: `{s['status_hash']}`", "",
         "## Evidence searched for every game", "",
         "team box rows, player box rows, PBP plays, shots, substitutions, lineup units, per-period scores (both sides >= 2 "
         "periods), and an official final score that is neither 0-0 nor a forfeit signature (2-0/1-0). games.csv carries no "
         "status/cancellation field; its `home_winner`/`away_winner` flags are both False on every 0-0 record. No source "
         "carries an explicit cancellation indicator, so no game is labeled UNPLAYED_OR_CANCELLED: the 0-0 records are "
         "SCHEDULE_PLACEHOLDER when a team of the record played a different scored contest that day (or within a day in the "
         "same named event), otherwise SOURCE_STATUS_UNRESOLVED. Both are excluded from played games.", "",
         "## Pre-2005 data (architect ruling)", "",
         "All seasons before 2004-05 are **OUT_OF_MODELING_SCOPE**. They stay preserved in raw/core storage, are not "
         "identity-adjudicated, and never enter `flagship.games`, `flagship.played_games`, PIT features, training or validation. "
         "See `docs/DATA_SCOPE_RULINGS.md`.", "",
         "## Excluded games", "", "| source game | season | date | home | away | score | status | method | notes |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["excluded_from_played_games"]:
            L.append(f"| {r['source_game_id']} | {r['season']} | {r['game_date_et']} | {r['home_team']} | {r['away_team']} | "
                     f"{r['home_points']}-{r['away_points']} | {r['status']} | {r['resolution_method']} | {r['notes'] or ''} |")
    L += ["", "## Aggregate impact (v2.1 -> v2.2)", "", "Full table: `data/qa/game_status_aggregate_impact.csv`.", "",
          "| team | season | games | W | L | PF | PA | removed | source ids |", "|---|---|---|---|---|---|---|---|---|"]
    for r in impact:
        L.append(f"| {r['team']} | {r['season']} | {r['old_games']}->{r['new_games']} | {r['old_wins']}->{r['new_wins']} | "
                 f"{r['old_losses']}->{r['new_losses']} | {r['old_points_for']}->{r['new_points_for']} | "
                 f"{r['old_points_against']}->{r['new_points_against']} | {r['removed_unplayed_contributions']} | {r['source_game_ids_excluded']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
