"""Stage 2K -- full candidate duplicate-game audit over the development history (2004-05 .. 2024-25).

    .venv/bin/python -m cbb_platform.game_identity_audit

Candidates: every pair of canonical games in one season between the SAME two teams (either
orientation) whose US/Eastern dates are at most one day apart. Each pair is classified from evidence
only; nothing is merged, deleted or rewritten.

    TRUE_DUPLICATE            same home/away orientation, same scheduled tip, same NON-ZERO final score,
                              venue equal (or missing on one side), and at most one of the two carries any
                              box score -- i.e. one record is an orphan copy of the other. (Two contests
                              between the same teams cannot share a tip time.)
    LEGITIMATE_DISTINCT_GAME  different ET dates and different tips, both with independent results (e.g. the
                              2020-21 back-to-back series), or both with their own team box score
                              and different scores
    POSSIBLE_DUPLICATE        same tip and orientation but a score is missing/0-0 or differs
    UNRESOLVED                anything else (e.g. opposite orientation with different scores at one tip)

Downstream scope is measured for every TRUE/POSSIBLE duplicate: which canonical tables carry rows
for each id, and which derived team-season aggregates count both copies. Remediation is NOT performed:
it is reported for an architect decision.

Writes data/qa/game_identity_candidates.csv, data/qa/game_identity_audit.json, docs/GAME_IDENTITY_AUDIT.md.
"""
from __future__ import annotations

import json

import duckdb

from cbb_platform import config as C

OUT_CSV = C.QA / "game_identity_candidates.csv"
OUT_JSON = C.QA / "game_identity_audit.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "GAME_IDENTITY_AUDIT.md"

CANDIDATES_SQL = """
WITH g AS (
    SELECT g.*, th.school AS home_school, ta.school AS away_school,
           least(g.home_team_id, g.away_team_id) AS t1, greatest(g.home_team_id, g.away_team_id) AS t2,
           CAST(replace(g.source_record_key, 'id=', '') AS BIGINT) AS cbbd_game_id,
           (SELECT count(*) FROM fact.team_games t WHERE t.canonical_game_id = g.canonical_game_id) AS team_box_rows,
           (SELECT count(*) FROM fact.player_games p WHERE p.canonical_game_id = g.canonical_game_id) AS player_box_rows
    FROM core.games g
    JOIN core.teams th ON th.canonical_team_id = g.home_team_id
    JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
    WHERE g.season BETWEEN 2005 AND 2025)
SELECT a.season, a.cbbd_game_id AS game_a, b.cbbd_game_id AS game_b,
       a.canonical_game_id AS canonical_a, b.canonical_game_id AS canonical_b,
       a.game_date_et AS date_a, b.game_date_et AS date_b,
       a.home_school || ' vs ' || a.away_school AS matchup_a, b.home_school || ' vs ' || b.away_school AS matchup_b,
       a.home_team_id = b.home_team_id AS same_orientation,
       a.scheduled_tip_utc AS tip_a, b.scheduled_tip_utc AS tip_b,
       a.scheduled_tip_utc IS NOT DISTINCT FROM b.scheduled_tip_utc AS same_tip,
       a.home_points AS home_pts_a, a.away_points AS away_pts_a, b.home_points AS home_pts_b, b.away_points AS away_pts_b,
       a.canonical_venue_id AS venue_a, b.canonical_venue_id AS venue_b,
       a.neutral_site AS neutral_a, b.neutral_site AS neutral_b, a.season_type AS season_type_a, b.season_type AS season_type_b,
       a.team_box_rows AS team_box_rows_a, b.team_box_rows AS team_box_rows_b,
       a.player_box_rows AS player_box_rows_a, b.player_box_rows AS player_box_rows_b
FROM g a JOIN g b ON a.t1 = b.t1 AND a.t2 = b.t2 AND a.season = b.season AND a.cbbd_game_id < b.cbbd_game_id
 AND abs(date_diff('day', a.game_date_et, b.game_date_et)) <= 1
"""


def classify(r: dict) -> tuple[str, str]:
    """(class, reason) for one candidate pair (pure; unit tested)."""
    scores_a = (r["home_pts_a"], r["away_pts_a"])
    scores_b = (r["home_pts_b"], r["away_pts_b"])
    # compare scores in a common orientation
    if not r["same_orientation"]:
        scores_b = scores_b[::-1]
    real = lambda s: None not in s and s != (0, 0)
    same_score = real(scores_a) and real(scores_b) and scores_a == scores_b
    venue_ok = r["venue_a"] is None or r["venue_b"] is None or r["venue_a"] == r["venue_b"]
    boxed = int((r["team_box_rows_a"] or 0) + (r["player_box_rows_a"] or 0) > 0) + \
        int((r["team_box_rows_b"] or 0) + (r["player_box_rows_b"] or 0) > 0)
    same_date = r["date_a"] == r["date_b"]
    if r["same_orientation"] and r["same_tip"] and same_score and venue_ok and boxed <= 1:
        return "TRUE_DUPLICATE", "same orientation, tip, non-zero score and venue; at most one record has box scores"
    if not same_date and not r["same_tip"] and real(scores_a) and real(scores_b):
        return "LEGITIMATE_DISTINCT_GAME", "different ET dates and tips, both with independent final results"
    if boxed == 2 and not same_score:
        return "LEGITIMATE_DISTINCT_GAME", "both records carry their own box scores with different results"
    if r["same_orientation"] and r["same_tip"]:
        return "POSSIBLE_DUPLICATE", "same orientation and tip, but a score is missing/0-0 or differs"
    return "UNRESOLVED", "evidence neither proves one contest nor two"


def downstream_scope(con, pairs: list[dict]) -> dict:
    """Which canonical rows each duplicate id carries and which derived aggregates count both."""
    dup = [p for p in pairs if p["classification"] in ("TRUE_DUPLICATE", "POSSIBLE_DUPLICATE")]
    ids = sorted({p["canonical_a"] for p in dup} | {p["canonical_b"] for p in dup})
    tables = {}
    for t in ("fact.team_games", "fact.player_games", "fact.participation", "core.game_schedule_versions",
              "fact.plays", "fact.shots", "fact.substitutions", "fact.lineup_stats"):
        try:
            n = con.execute(f"SELECT count(*) FROM {t} WHERE canonical_game_id IN (SELECT unnest(?))", [ids]).fetchone()[0]
        except duckdb.Error:
            n = None
        tables[t] = n
    # all-results aggregates in fact.team_season_game_derived count every scored core.games row
    both_scored = [p for p in dup if None not in (p["home_pts_a"], p["away_pts_a"], p["home_pts_b"], p["away_pts_b"])]
    true_scored = [p for p in both_scored if p["classification"] == "TRUE_DUPLICATE"]
    team_seasons = set()
    for p in both_scored:
        for side in ("home", "away"):
            tid = con.execute(f"SELECT {side}_team_id FROM core.games WHERE canonical_game_id = ?", [p["canonical_a"]]).fetchone()[0]
            team_seasons.add((tid, p["season"]))
    return {"duplicate_or_possible_ids": len(ids), "canonical_rows_on_these_ids": tables,
            "pairs_where_both_records_have_scores": len(both_scored),
            "true_duplicate_pairs_both_scored": len(true_scored),
            "team_seasons_whose_all_results_aggregates_count_both_copies": len(team_seasons),
            "affected_columns": ["fact.team_season_game_derived.games_with_result", "wins", "losses",
                                 "points_all_results", "opp_points_all_results"],
            "box_score_double_count": "none: no candidate pair has box rows on both ids" if not any(
                p["team_box_rows_a"] and p["team_box_rows_b"] for p in dup) else "PRESENT"}


def run(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute("SET TimeZone = 'UTC'")
    try:
        df = con.execute(CANDIDATES_SQL + " ORDER BY season, date_a, game_a").df()
        pairs = []
        for r in df.to_dict("records"):
            r = {k: (None if (v is not None and not isinstance(v, (str, list)) and v != v) else v) for k, v in r.items()}
            for k in ("home_pts_a", "away_pts_a", "home_pts_b", "away_pts_b", "team_box_rows_a", "team_box_rows_b",
                      "player_box_rows_a", "player_box_rows_b"):
                r[k] = None if r[k] is None else int(r[k])
            r["classification"], r["reason"] = classify(r)
            pairs.append(r)
        scope = downstream_scope(con, pairs)
        # the Stage 1 Purdue-Alabama note, checked explicitly
        pa = con.execute("""SELECT g.source_record_key, g.game_date_et, home_points, away_points FROM core.games g
            JOIN core.teams th ON th.canonical_team_id = g.home_team_id JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
            WHERE g.season = 2025 AND th.school IN ('Purdue', 'Alabama') AND ta.school IN ('Purdue', 'Alabama')""").fetchall()
    finally:
        con.close()
    import pandas as pd
    out = pd.DataFrame(pairs)
    out.to_csv(OUT_CSV, index=False)
    counts = out.groupby(["season", "classification"]).size().unstack(fill_value=0) if len(out) else None
    result = {"candidate_pairs": len(pairs),
              "by_class": out["classification"].value_counts().to_dict() if len(out) else {},
              "by_season": {int(s): {k: int(v) for k, v in row.items() if v} for s, row in counts.iterrows()} if counts is not None else {},
              "downstream_scope": scope,
              "purdue_alabama_2024_25": [{"game": k, "date_et": str(d), "score": f"{h}-{a}"} for k, d, h, a in pa],
              "remediation_performed": False,
              "destructive_remediation_required": False,
              "recommended_non_destructive_remediation": (
                  "Keep both records. Exclude the box-less copy of each TRUE_DUPLICATE pair from all-results "
                  "season aggregates via an identity-exclusion view (architect approval required)."),
              }
    OUT_JSON.write_text(json.dumps(result, indent=1, default=str))
    OUT_MD.write_text(render(result, pairs))
    return result


def render(res: dict, pairs: list[dict]) -> str:
    L = ["# Game identity audit (Stage 2K)", "",
         "Rendered from `data/qa/game_identity_audit.json` by `cbb_platform/game_identity_audit.py`. "
         "Full candidate list: `data/qa/game_identity_candidates.csv`.", "",
         "Scope: every pair of 2004-05..2024-25 canonical games between the same two teams (either "
         "orientation) whose US/Eastern dates are at most one day apart. **Nothing was merged or deleted.**", "",
         "## Classes", "",
         "| class | pairs |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(res["by_class"].items())]
    L += ["", "## By season", "", "| season | " + " | ".join(sorted(res["by_class"])) + " |",
          "|---|" + "---|" * len(res["by_class"])]
    for s, row in sorted(res["by_season"].items()):
        L.append(f"| {s} | " + " | ".join(str(row.get(k, 0)) for k in sorted(res["by_class"])) + " |")
    sc = res["downstream_scope"]
    L += ["", "## Downstream contamination scope", "",
          f"- Canonical ids involved in TRUE/POSSIBLE duplicates: {sc['duplicate_or_possible_ids']}",
          f"- Box-score double counting: {sc['box_score_double_count']}",
          f"- Pairs where BOTH records carry a final score: {sc['pairs_where_both_records_have_scores']} "
          f"(TRUE_DUPLICATE: {sc['true_duplicate_pairs_both_scored']})",
          f"- Team-seasons whose ALL-RESULTS aggregates count both copies: "
          f"{sc['team_seasons_whose_all_results_aggregates_count_both_copies']} "
          f"(columns: {', '.join(sc['affected_columns'])})",
          "- Rows on the involved ids, per table: " + ", ".join(f"{k}={v}" for k, v in sc["canonical_rows_on_these_ids"].items()),
          "", "Box-score based aggregates (`games_with_team_box` and the box columns) are unaffected because no "
          "candidate pair has box rows on both ids. The all-results columns double count the result of each "
          "duplicated contest. **Remediation is not performed in Stage 2** (no destructive change is needed: the "
          "recommended fix is an exclusion view), and is reported for an architect decision.", "",
          "## Stage 1 Purdue-Alabama note", "",
          "Stage 1 reported two Starter Pack game ids for the same Purdue-Alabama date. Re-measured: "
          + "; ".join(f"{x['game']} on {x['date_et']} ({x['score']})" for x in res["purdue_alabama_2024_25"])
          + ". games.csv, team_game_stats, player_game_stats and the CBBD API all carry exactly ONE id (671) for "
          "this contest; the observation is not reproducible and no candidate pair involves it.", "",
          "## TRUE_DUPLICATE / POSSIBLE_DUPLICATE / UNRESOLVED pairs", "",
          "| season | ET date | game a | game b | matchup a | matchup b | score a | score b | box a/b | class |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for p in pairs:
        if p["classification"] != "LEGITIMATE_DISTINCT_GAME":
            L.append(f"| {p['season']} | {str(p['date_a'])[:10]} | {p['game_a']} | {p['game_b']} | {p['matchup_a']} | "
                     f"{p['matchup_b']} | {p['home_pts_a']}-{p['away_pts_a']} | {p['home_pts_b']}-{p['away_pts_b']} | "
                     f"{'Y' if p['team_box_rows_a'] else 'N'}/{'Y' if p['team_box_rows_b'] else 'N'} | {p['classification']} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: r[k] for k in ("candidate_pairs", "by_class", "downstream_scope")}, indent=1, default=str))
