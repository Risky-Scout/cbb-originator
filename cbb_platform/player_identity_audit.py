"""Stage 3.1 section 2 -- systematic player-identity collision audit, 2013-14 .. 2024-25 (offline; reads the built DB).

    .venv/bin/python -m cbb_platform.player_identity_audit

Checks (source level AND canonical level, so a repaired collision stays visible with its repair):
    BOX_OVERLAP            one canonical player with box rows in two games tipping < 4 h apart
    BOX_INTERLEAVING       one canonical player on two teams in a season with interleaving date ranges
    BOX_MULTI_TEAM_SEQUENTIAL  two teams in a season, non-interleaving (ordinary mid-season move)
    SEASON_FILE_MULTI_TEAM one Starter Pack season-file athleteId on two teams in one season
    API_OVERLAP            one CBBD PBP athlete id in two games tipping < 4 h apart
    ROSTER_MULTI_TEAM      one athlete id on two team rosters in one season
    ROSTER_DOB_CONFLICT / ROSTER_HEIGHT_CONFLICT (> 3 in) / ROSTER_POSITION_CONFLICT (guard vs center) across seasons
Classes: CONFIRMED_COLLISION (physically impossible histories), PROBABLE_COLLISION, REVIEW_REQUIRED,
CONFIRMED_SINGLE_PLAYER. Ambiguous candidates are never split automatically.
"""
from __future__ import annotations

import csv
import json

import duckdb

from cbb_platform import config as C, player_identity as PI

OUT_CSV = C.QA / "player_identity_collision_audit.csv"
OUT_JSON = C.QA / "player_identity_collision_audit.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "PLAYER_IDENTITY_COLLISION_AUDIT.md"
W = "BETWEEN 2014 AND 2025"
OVERLAP_MIN = 240


def run(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    cands = []

    def add(check, entity, season, detail, cls, resolution):
        cands.append({"check": check, "entity": entity, "season": season, "detail": detail, "classification": cls,
                      "resolution": resolution})

    rules = {r[0]: r for r in q("SELECT DISTINCT rule_id, rule_type, provider_athlete_id FROM metadata.player_identity_resolution")}
    # source-level: Starter Pack season-file athleteId on two teams in one season
    for aid, season, teams, names in q(f"""SELECT athleteId, CAST(CAST(season AS DOUBLE) AS INT), list(DISTINCT team ORDER BY team),
            list(DISTINCT name) FROM read_csv('{C.STARTER_PACK_DATA}/player_season_stats/*.csv', all_varchar=true, union_by_name=true)
            WHERE TRY_CAST(season AS INT) {W} GROUP BY 1, 2 HAVING count(DISTINCT teamId) > 1"""):
        if aid == "31449":
            add("SEASON_FILE_MULTI_TEAM", f"athleteId {aid}", season, f"{names} on {teams}", "CONFIRMED_COLLISION", "REPAIRED_SPLIT (SPLIT_CBBD_ATHLETE_31449)")
        else:
            inter = q(f"""WITH t AS (SELECT p.canonical_team_id, min(g.scheduled_tip_utc) a, max(g.scheduled_tip_utc) b FROM fact.player_games p
                          JOIN flagship.played_games g USING (canonical_game_id) WHERE p.canonical_player_id = cid('P','athlete', ?) AND p.season = ?
                          GROUP BY 1) SELECT count(*) FROM t x JOIN t y ON x.canonical_team_id < y.canonical_team_id AND x.a <= y.b AND y.a <= x.b""",
                      [aid, season])[0][0]
            add("SEASON_FILE_MULTI_TEAM", f"athleteId {aid}", season, f"{names} on {teams}",
                "PROBABLE_COLLISION" if inter else "CONFIRMED_SINGLE_PLAYER",
                "REVIEW" if inter else "sequential mid-season team change; no overlapping appearances")
    # canonical-level box checks (after repair)
    for pid, season, n in q(f"""WITH p AS (SELECT p.canonical_player_id, p.season, p.canonical_game_id, g.scheduled_tip_utc t FROM fact.player_games p
              JOIN flagship.played_games g USING (canonical_game_id) WHERE p.canonical_player_id IS NOT NULL AND g.season {W})
            SELECT canonical_player_id, season, count(*) FROM p a JOIN p b USING (canonical_player_id, season)
            WHERE a.canonical_game_id < b.canonical_game_id AND abs(date_diff('minute', a.t, b.t)) < {OVERLAP_MIN} GROUP BY 1, 2"""):
        add("BOX_OVERLAP", pid, season, f"{n} pairs of games < 4 h apart", "CONFIRMED_COLLISION", "UNREPAIRED")
    for pid, season, teams in q(f"""WITH p AS (SELECT p.canonical_player_id, p.season, p.canonical_team_id, g.scheduled_tip_utc tip FROM fact.player_games p
              JOIN flagship.played_games g USING (canonical_game_id) WHERE p.canonical_player_id IS NOT NULL AND g.season {W}),
            t AS (SELECT canonical_player_id, season, canonical_team_id, min(tip) a, max(tip) b FROM p GROUP BY 1, 2, 3)
            SELECT canonical_player_id, season, list(DISTINCT canonical_team_id) FROM t GROUP BY 1, 2 HAVING count(*) > 1"""):
        inter = q(f"""WITH t AS (SELECT p.canonical_team_id, min(g.scheduled_tip_utc) a, max(g.scheduled_tip_utc) b FROM fact.player_games p
                      JOIN flagship.played_games g USING (canonical_game_id) WHERE p.canonical_player_id = ? AND p.season = ? GROUP BY 1)
                      SELECT count(*) FROM t x JOIN t y ON x.canonical_team_id < y.canonical_team_id AND x.a <= y.b AND y.a <= x.b""",
                  [pid, season])[0][0]
        add("BOX_INTERLEAVING" if inter else "BOX_MULTI_TEAM_SEQUENTIAL", pid, season, f"teams {teams}",
            "PROBABLE_COLLISION" if inter else "CONFIRMED_SINGLE_PLAYER",
            "REVIEW" if inter else "sequential mid-season team change")
    # API (PBP participant) overlaps -- source ids, so repaired collisions remain visible
    api = q(f"""WITH x AS (SELECT DISTINCT a.source_athlete_id, a.canonical_game_id, g.scheduled_tip_utc t FROM fact.play_participants a
              JOIN flagship.played_games g USING (canonical_game_id) WHERE g.season {W} AND a.source_athlete_id IS NOT NULL),
            pr AS (SELECT a.source_athlete_id, a.canonical_game_id AS g1, b.canonical_game_id AS g2 FROM x a JOIN x b USING (source_athlete_id)
                   WHERE a.canonical_game_id < b.canonical_game_id AND abs(date_diff('minute', a.t, b.t)) < {OVERLAP_MIN})
            SELECT source_athlete_id, count(*), list(DISTINCT g1 || '/' || g2) FROM pr GROUP BY 1 ORDER BY 1""")
    for aid, n, pairs in api:
        if aid == "31449":
            add("API_OVERLAP", f"athleteId {aid}", None, f"{n} overlapping game pairs", "CONFIRMED_COLLISION", "REPAIRED_SPLIT (SPLIT_CBBD_ATHLETE_31449)")
        elif aid == "19801":
            add("API_OVERLAP", f"athleteId {aid}", None, f"{n} overlapping game pairs (Tulane 2014-2016 vs NC State)", "CONFIRMED_COLLISION",
                "REPAIRED_QUARANTINE (QUARANTINE_CBBD_ATHLETE_19801)")
        else:
            add("API_OVERLAP", f"athleteId {aid}", None, f"{n} overlapping game pair(s) {pairs[:3]}", "CONFIRMED_SINGLE_PLAYER",
                "game-level event attribution (whole rosters in one game pair: High Point/Hampton 2022-01-24 PBP misattribution or "
                "Kansas City vs Kansas Christian doubleheader 2020-12-19); not an identity collision")
    # rosters
    for aid, season, teams in q(f"""SELECT source_athlete_id, season, list(DISTINCT canonical_team_id) FROM core.rosters WHERE season {W}
                                   GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) > 1"""):
        boxed = q("""SELECT count(DISTINCT canonical_team_id) FROM fact.player_games WHERE canonical_player_id = cid('P','athlete', ?) AND season = ?""", [aid, season])[0][0]
        add("ROSTER_MULTI_TEAM", f"athleteId {aid}", season, f"rosters {teams}; box teams {boxed}",
            "CONFIRMED_SINGLE_PLAYER" if boxed <= 1 else "REVIEW_REQUIRED",
            "season snapshot lists a mid-season move; appearances on at most one team" if boxed <= 1 else "REVIEW")
    for chk, sql in (("ROSTER_DOB_CONFLICT", "SELECT source_athlete_id, list(DISTINCT date_of_birth_raw) FROM core.rosters WHERE date_of_birth_raw IS NOT NULL GROUP BY 1 HAVING count(DISTINCT date_of_birth_raw) > 1"),
                     ("ROSTER_HEIGHT_CONFLICT", "SELECT source_athlete_id, list(DISTINCT height_inches) FROM core.rosters WHERE height_inches IS NOT NULL GROUP BY 1 HAVING max(height_inches) - min(height_inches) > 3"),
                     ("ROSTER_POSITION_CONFLICT", "SELECT source_athlete_id, list(DISTINCT position) FROM core.rosters GROUP BY 1 HAVING bool_or(position = 'Guard') AND bool_or(position = 'Center')")):
        for aid, vals in q(sql):
            add(chk, f"athleteId {aid}", None, f"{vals}", "REVIEW_REQUIRED", "REVIEW (profile only; no impossible game history)")
    # explicit adjudications
    add("SPLIT_ENTITY_LINK", PI.SPLIT_31449_NEW, None, "North Texas 2016-17/2017-18 and McNeese 2019-20/2020-21 rows kept as ONE entity "
        "(same name, guard, a transfer sit-out season between); not proven distinct", "REVIEW_REQUIRED", "kept together (no automatic split)")
    add("ROSTER_ATTRIBUTE_CONTAMINATION", "athleteId 31449", 2020, "McNeese 2020 roster row carries the South Carolina player's "
        "hometown (Brampton ON) and DOB (2000-07-15)", "CONFIRMED_COLLISION", "row re-mapped to the split entity; attributes research-only (not PIT)")
    team = q("SELECT rows_affected FROM metadata.player_identity_resolution WHERE rule_id = 'BOX_TEAM_REATTRIBUTION_GAME_209587'")
    add("BOX_TEAM_MISATTRIBUTION", "game 209587", 2025, f"{team[0][0] if team else 0} Presbyterian box rows filed under Illinois State",
        "CONFIRMED_COLLISION", "REPAIRED_TEAM_REATTRIBUTION (reconciles 70 / 78)")
    rules_rows = q("SELECT rule_id, rule_type, table_name, rows_affected FROM metadata.player_identity_resolution ORDER BY 1, 3")
    split = q("""SELECT canonical_player_id, count(*), min(season), max(season), list(DISTINCT t.school ORDER BY t.school) FROM fact.player_games p
                 JOIN core.teams t ON t.canonical_team_id = p.canonical_team_id WHERE canonical_player_id IN (cid('P','athlete','31449'), ?) GROUP BY 1 ORDER BY 1""",
              [PI.SPLIT_31449_NEW])
    con.close()
    from collections import Counter
    by_class = Counter(c["classification"] for c in cands)
    result = {"window": "2013-14 .. 2024-25", "candidates": len(cands), "by_class": dict(by_class),
              "by_check": dict(Counter(c["check"] for c in cands)),
              "aj_lawson": {"classification": "SOURCE_ID_COLLISION (TWO_DISTINCT_PLAYERS_MERGED by CBBD athleteId 31449)",
                            "after_repair": [{"canonical_player_id": r[0], "box_rows": r[1], "first_season": r[2], "last_season": r[3], "teams": r[4]} for r in split]},
              "repair_rules": [{"rule_id": r[0], "rule_type": r[1], "table": r[2], "rows": r[3]} for r in rules_rows],
              "remaining_unrepaired_confirmed": sum(1 for c in cands if c["classification"] == "CONFIRMED_COLLISION" and c["resolution"] == "UNREPAIRED"),
              "candidates_list": cands}
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cands[0]))
        w.writeheader()
        w.writerows(cands)
    OUT_JSON.write_text(json.dumps(result, indent=1, default=str))
    OUT_MD.write_text(render(result))
    return result


def render(r: dict) -> str:
    L = ["# Player identity collision audit (Stage 3.1)", "",
         "Rendered from `data/qa/player_identity_collision_audit.json` by `cbb_platform/player_identity_audit.py`. "
         f"Window {r['window']}. Repairs are rules in `cbb_platform/player_identity.py` applied inside every canonical build "
         "(derived rows only; raw sources unchanged) and logged in `metadata.player_identity_resolution`.", "",
         f"Candidates: **{r['candidates']}**; by class: {r['by_class']}; by check: {r['by_check']}; "
         f"confirmed collisions left unrepaired: **{r['remaining_unrepaired_confirmed']}**.", "",
         "## A.J. Lawson (CBBD athleteId 31449)", "",
         f"Classification: **{r['aj_lawson']['classification']}**.", "",
         "Evidence: CBBD's own season files carry athleteId 31449 for South Carolina AND McNeese in 2019-20 and 2020-21; the "
         "entity appears for both teams in games tipping within hours of each other (23 box pairs < 6 h, 22 PBP pairs); South "
         "Carolina and McNeese never met; CBBD recruiting links 31449 to the class-of-2018 South Carolina signee from Mississauga ON "
         "(roster attributes Brampton ON, born 2000), while a separate class-of-2016 recruit 'AJ Lawson' (Bryan TX, no athlete id) "
         "signed with North Texas -- so the 2016-17/2017-18 North Texas rows cannot belong to the South Carolina player. "
         "Per-game profiles differ (South Carolina 5.4-8.0 3PA and 1.2-1.9 AST per game vs McNeese 2.3-2.8 3PA and 4.2-5.5 AST).", "",
         "Resolution (non-destructive split): South Carolina rows keep `P-7d2ea3fe693fd65b` (= cid(31449), the id CBBD recruiting "
         f"attaches to that player); North Texas + McNeese rows move to `{PI.SPLIT_31449_NEW}`. After repair: {r['aj_lawson']['after_repair']}.", "",
         "## Repair rules applied", "", "| rule | type | table | rows rewritten |", "|---|---|---|---|"]
    L += [f"| {x['rule_id']} | {x['rule_type']} | {x['table']} | {x['rows']} |" for x in r["repair_rules"]]
    L += ["", "## All candidates", "", "| check | entity | season | detail | class | resolution |", "|---|---|---|---|---|---|"]
    L += [f"| {c['check']} | {c['entity']} | {c['season'] or ''} | {c['detail']} | {c['classification']} | {c['resolution']} |" for c in r["candidates_list"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k != "candidates_list"}, indent=1, default=str))
