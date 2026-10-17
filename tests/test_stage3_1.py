"""Stage 3.1 -- player identity repairs and historical-roster PIT integrity."""
from __future__ import annotations

import csv
import inspect
import json

import duckdb
import pytest

from cbb_platform import config as C, player_identity as PI
from cbb_platform.stage3 import build as B, labels, participation, player_state, sources, spec, universe
from tests.conftest import scalar

LEGAL = ("PRIOR_APPEARANCE_CONFIRMED", "PRIOR_SEASON_RETURNER", "PRIOR_SEASON_ROSTER_RETURNER",
         "TRANSFER_PRESEASON_EVIDENCE", "RECRUIT_PRESEASON_EVIDENCE")
SC_ID = "P-7d2ea3fe693fd65b"


@pytest.fixture(scope="module")
def fx():
    con = duckdb.connect(str(spec.FEATURES_DB), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{spec.LABELS_DB}' AS lb (READ_ONLY)")
    con.execute(f"ATTACH '{C.DUCKDB_PATH}' AS cdb (READ_ONLY)")
    yield con
    con.close()


def one(con, sql, p=None):
    return con.execute(sql, p or []).fetchone()[0]


# ------------------------------------------------------------------ A. A.J. Lawson / identity repairs (upstream v2.3)
def test_aj_lawson_is_split_into_two_people(db):
    sc = db.execute("""SELECT list(DISTINCT t.school), min(p.season), max(p.season), count(*) FROM fact.player_games p
                       JOIN core.teams t ON t.canonical_team_id = p.canonical_team_id WHERE p.canonical_player_id = ?""", [SC_ID]).fetchone()
    other = db.execute("""SELECT list(DISTINCT t.school ORDER BY t.school), min(p.season), max(p.season), count(*) FROM fact.player_games p
                          JOIN core.teams t ON t.canonical_team_id = p.canonical_team_id WHERE p.canonical_player_id = ?""", [PI.SPLIT_31449_NEW]).fetchone()
    assert sc[0] == ["South Carolina"] and (sc[1], sc[2]) == (2019, 2021)
    assert other[0] == ["McNeese", "North Texas"] and (other[1], other[2]) == (2017, 2021)
    assert sc[3] + other[3] == 81 + 122                                     # no valid box row dropped
    assert scalar(db, "SELECT count(*) FROM core.players WHERE canonical_player_id IN (?, ?)", [SC_ID, PI.SPLIT_31449_NEW]) == 2


def test_raw_sources_are_untouched_by_identity_repair():
    """The Starter Pack still carries athleteId 31449 on both teams: repairs live only in derived canonical rows."""
    n = duckdb.sql(f"""SELECT count(DISTINCT team) FROM read_csv('{C.STARTER_PACK_DATA}/player_season_stats/*.csv', all_varchar=true,
                       union_by_name=true) WHERE athleteId = '31449' AND season = '2020'""").fetchone()[0]
    assert n == 2


def test_no_canonical_player_appears_in_overlapping_games(db):
    assert scalar(db, """WITH p AS (SELECT p.canonical_player_id, p.canonical_game_id, g.scheduled_tip_utc t FROM fact.player_games p
        JOIN flagship.played_games g USING (canonical_game_id) WHERE p.canonical_player_id IS NOT NULL AND g.season BETWEEN 2014 AND 2025)
        SELECT count(*) FROM p a JOIN p b USING (canonical_player_id) WHERE a.canonical_game_id < b.canonical_game_id
        AND abs(date_diff('minute', a.t, b.t)) < 240""") == 0
    assert scalar(db, """WITH x AS (SELECT DISTINCT a.canonical_player_id, a.canonical_game_id, g.scheduled_tip_utc t FROM fact.play_participants a
        JOIN flagship.played_games g USING (canonical_game_id) WHERE a.canonical_player_id IS NOT NULL AND g.season BETWEEN 2014 AND 2025)
        SELECT count(*) FROM x a JOIN x b USING (canonical_player_id) WHERE a.canonical_game_id < b.canonical_game_id
        AND abs(date_diff('minute', a.t, b.t)) < 240""") <= 25    # only whole-roster game-level event attribution remains (audited)


def test_yurtseven_history_contains_only_his_team_seasons(db):
    yur = scalar(db, "SELECT cid('P','athlete','19801')")
    assert scalar(db, """SELECT count(*) FROM fact.play_participants x JOIN core.games g USING (canonical_game_id)
        JOIN core.teams th ON th.canonical_team_id = g.home_team_id JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
        WHERE x.canonical_player_id = ? AND NOT ((g.season IN (2017, 2018) AND 'NC State' IN (th.school, ta.school))
                                                 OR (g.season = 2020 AND 'Georgetown' IN (th.school, ta.school)))""", [yur]) == 0
    assert scalar(db, "SELECT count(*) FROM fact.play_participants WHERE source_athlete_id = '19801' AND canonical_player_id IS NULL") > 0


def test_game_209587_box_reconciles_after_reattribution(db):
    assert scalar(db, """SELECT count(*) FROM fact.team_games t JOIN (SELECT canonical_game_id, canonical_team_id, sum(points) pts
        FROM fact.player_games GROUP BY 1, 2) p USING (canonical_game_id, canonical_team_id)
        WHERE t.canonical_game_id = cid('G','game','209587') AND t.points = p.pts""") == 2


def test_identity_audit_is_complete():
    r = json.loads((C.QA / "player_identity_collision_audit.json").read_text())
    assert r["remaining_unrepaired_confirmed"] == 0
    assert set(r["by_class"]) <= {"CONFIRMED_SINGLE_PLAYER", "CONFIRMED_COLLISION", "PROBABLE_COLLISION", "REVIEW_REQUIRED"}
    rows = list(csv.DictReader((C.QA / "player_identity_collision_audit.csv").open()))
    assert any(x["entity"] == "athleteId 31449" and x["classification"] == "CONFIRMED_COLLISION" for x in rows)


# ------------------------------------------------------------------ B. roster PIT policy / player pool
def test_every_pool_member_has_legal_evidence_at_the_cutoff(fx):
    assert one(fx, f"SELECT count(*) FROM ex.player_game_examples WHERE pool_evidence NOT IN {LEGAL}") == 0
    assert one(fx, f"SELECT count(*) FROM ex.team_membership_evidence WHERE evidence_class NOT IN {LEGAL}") == 0
    assert one(fx, """SELECT count(*) FROM ex.player_game_examples x WHERE NOT EXISTS (SELECT 1 FROM ex.team_membership_evidence e
        WHERE e.canonical_player_id = x.canonical_player_id AND e.canonical_team_id = x.canonical_team_id AND e.season = x.season
          AND e.available_from <= x.prediction_cutoff_utc)""") == 0


def test_future_first_appearance_cannot_leak_backward(fx):
    """A player whose only evidence is an appearance must not be in any pool before that appearance was knowable."""
    assert one(fx, """SELECT count(*) FROM ex.player_game_examples x WHERE x.pool_evidence = 'PRIOR_APPEARANCE_CONFIRMED'
        AND NOT EXISTS (SELECT 1 FROM cdb.flagship.player_game_outcomes p JOIN cdb.flagship.played_games g USING (canonical_game_id)
            WHERE p.canonical_player_id = x.canonical_player_id AND p.canonical_team_id = x.canonical_team_id AND g.season = x.season
              AND p.historical_knowable_at <= x.prediction_cutoff_utc AND p.canonical_game_id <> x.canonical_game_id)""") == 0


def test_end_of_season_roster_never_creates_membership(fx):
    # current-season roster rows are not an evidence class, and roster-only players never enter a pool
    assert one(fx, """SELECT count(*) FROM ex.roster_snapshot_research r JOIN ex.player_game_examples x
        USING (game_example_id, canonical_team_id, canonical_player_id)""") == 0
    assert one(fx, "SELECT count(*) FROM ex.roster_snapshot_research") > 0
    src = inspect.getsource(universe)
    assert "r.season + 1" in src            # roster evidence = the PREVIOUS season's snapshot only
    for m in (player_state,):
        s = inspect.getsource(m)
        assert "FROM src.rosters GROUP BY" not in s and "ro.position" not in s and "ro.height" not in s


def test_feature_history_never_reads_the_season_roster_snapshot():
    s = inspect.getsource(player_state)
    assert "classify_sql('part_cand')" in s                     # default use_season_roster_snapshot=False
    assert "use_season_roster_snapshot=True" in inspect.getsource(labels)
    assert "def classify_sql(candidates: str, use_season_roster_snapshot: bool = False)" in inspect.getsource(participation)


def test_no_approved_feature_depends_on_the_roster_snapshot():
    reg = json.loads(spec.REGISTRY_JSON.read_text())
    for e in reg["entries"]:
        if e.get("approved_for_modeling"):
            assert "rosters" not in e.get("source_tables", ""), e["feature_name"]
            assert not e["feature_name"].startswith("research_"), e["feature_name"]
    research = [e for e in reg["entries"] if e["kind"] == "RESEARCH_NON_PIT"]
    assert research and all(not e["approved_for_modeling"] for e in research)


def test_target_box_cannot_create_target_membership(tmp_path):
    """Adversarial: inject a brand-new player ONLY into one target game's box. He must be absent from that game's pool
    and present from his team's NEXT game (evidence = that appearance)."""
    con = B.connect(tmp_path / "adv.duckdb")
    sources.bind(con)
    con.execute("CREATE SCHEMA s2")
    for (v,) in con.execute("SELECT view_name FROM duckdb_views() WHERE schema_name = 'src'").fetchall():
        cols = [c[0] for c in con.execute(f"DESCRIBE src.{v}").fetchall()]
        con.execute(f"CREATE TABLE s2.{v} AS SELECT * FROM src.{v}" + (" WHERE season = 2024" if "season" in cols else ""))
    sources.unbind(con)
    con.execute("CREATE SCHEMA src")
    for (t,) in con.execute("SELECT table_name FROM duckdb_tables() WHERE schema_name = 's2'").fetchall():
        con.execute(f"CREATE TABLE src.{t} AS SELECT * FROM s2.{t}")
    g, team, tip = con.execute("""SELECT canonical_game_id, canonical_team_id, historical_knowable_at FROM src.player_games
        WHERE canonical_player_id IS NOT NULL ORDER BY historical_knowable_at, canonical_game_id LIMIT 1 OFFSET 40000""").fetchone()
    con.execute(f"""INSERT INTO src.player_games SELECT * REPLACE ('P-ADVERSARIAL0001' AS canonical_player_id, 'PG-ADV' AS player_game_key)
                    FROM src.player_games WHERE canonical_game_id = '{g}' AND canonical_team_id = '{team}' LIMIT 1""")
    universe.build(con)
    assert one(con, "SELECT count(*) FROM ex.player_game_examples WHERE canonical_player_id = 'P-ADVERSARIAL0001' AND canonical_game_id = ?", [g]) == 0
    later = one(con, """SELECT count(*) FROM ex.player_game_examples WHERE canonical_player_id = 'P-ADVERSARIAL0001'
                        AND canonical_team_id = ? AND prediction_cutoff_utc >= ?""", [team, tip])
    nxt = one(con, "SELECT count(*) FROM ex.team_game_examples WHERE canonical_team_id = ? AND prediction_cutoff_utc >= ?", [team, tip])
    assert later == nxt and nxt > 0


# ------------------------------------------------------------------ C. misses
def test_pool_misses_are_classified_and_none_is_a_pipeline_bug(fx):
    assert one(fx, "SELECT count(*) FROM lb.lab.player_pool_misses WHERE miss_class = 'PIPELINE_BUG'") == 0
    assert one(fx, """SELECT count(*) FROM lb.lab.player_pool_misses WHERE miss_class NOT IN
                      ('IDENTITY_ERROR', 'HISTORICALLY_UNOBSERVABLE', 'VALID_COLD_START_MISS', 'OTHER')""") == 0
    assert one(fx, "SELECT count(*) FROM lb.lab.player_pool_misses") == one(fx, "SELECT count(*) FROM lb.lab.target_box_rows_outside_universe")
    rows = list(csv.DictReader((C.QA / "player_pool_misses.csv").open()))
    assert len(rows) == 20 and all(r["classification"] for r in rows)


def test_split_identities_do_not_mix_feature_histories(fx):
    assert one(fx, f"""SELECT count(*) FROM feat.player_box_state s WHERE s.canonical_player_id = '{SC_ID}'
                       AND s.last_team_id <> 'T-d65fe040e43569b4'""") == 0                 # South Carolina only
    assert one(fx, f"""SELECT count(*) FROM feat.player_box_state s WHERE s.canonical_player_id = '{PI.SPLIT_31449_NEW}'
                       AND s.last_team_id = 'T-d65fe040e43569b4'""") == 0


def test_stage3_rebuilt_from_v2_3():
    # v2.3 is preserved verbatim as an ancestor (Pre-Model Integrity Closure v1 created the successor v2.4); Stage 3 is
    # rebuilt from the CURRENT dataset, whose lineage must contain v2.3 (same pattern as test_stage2_2 for v2.2)
    v23 = json.loads((C.MANIFESTS / "DATASET_MANIFEST_cbb_data_v2.3+7bef640cccde.json").read_text())
    assert v23["dataset_version"] == "cbb_data_v2.3+7bef640cccde" and v23["parent_dataset_version"] == "cbb_data_v2.2+95dd9c2fa5b7"
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    assert "cbb_data_v2.3+7bef640cccde" in man["lineage"]
    assert json.loads(spec.STAGE3_MANIFEST.read_text())["upstream_dataset"] == man["dataset_version"] == spec.UPSTREAM_DATASET
