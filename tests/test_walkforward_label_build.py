"""Walk-forward label build = the frozen stage3.labels.build minus its unread Stage 3.1 diagnostic relations.

No frozen store needed (CI): a small synthetic src / fx pair is labelled by the FULL frozen build and by
training_rows.label_statements(); the two consumed relations must be identical (values, dtypes, row order).
tests/test_walkforward_training_rows.py proves the label frames against the frozen loaders on the real stores."""
import duckdb
import pandas as pd
import pytest

from cbb_platform.stage3 import labels as LB
from cbb_platform.walkforward import training_rows as TR


def _db():
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone = 'UTC'")
    con.execute("ATTACH ':memory:' AS fx")
    con.execute("CREATE SCHEMA fx.ex; CREATE SCHEMA src")
    games = [("G1", "T1", "T2", 80, 70, "2026-11-05 00:00:00+00"), ("G2", "T2", "T3", 65, 66, "2026-11-07 00:00:00+00")]
    con.execute("""CREATE TABLE src.played_games (canonical_game_id VARCHAR, home VARCHAR, away VARCHAR, home_points INTEGER,
                   away_points INTEGER, scheduled_tip_utc TIMESTAMPTZ)""")
    con.executemany("INSERT INTO src.played_games VALUES (?, ?, ?, ?, ?, ?)", games)
    con.execute("""CREATE TABLE src.player_games (canonical_game_id VARCHAR, canonical_team_id VARCHAR, canonical_player_id VARCHAR,
                   season INTEGER, minutes DOUBLE, starter BOOLEAN, points DOUBLE, fga DOUBLE, fta DOUBLE, reb DOUBLE, ast DOUBLE,
                   stl DOUBLE, blk DOUBLE, tov DOUBLE, pf DOUBLE, orb DOUBLE, drb DOUBLE, ftm DOUBLE, two_pm DOUBLE, two_pa DOUBLE,
                   three_pm DOUBLE, three_pa DOUBLE, accounting_issues VARCHAR, identity_status VARCHAR)""")
    rows = []
    for g, t, ps in (("G1", "T1", ["P1", "P2", "P9"]), ("G1", "T2", ["P3"]), ("G2", "T2", ["P3", "P4"]), ("G2", "T3", ["P5", None])):
        for k, p in enumerate(ps):
            m = 0.0 if p == "P2" else 20.0 + k
            rows.append((g, t, p, 2027, m, k == 0, 2.0 * k + (0 if p == "P2" else 3), 5.0, 2.0, 4.0, 1.0, 0.0, 0.0, 1.0, 2.0,
                         1.0, 3.0, 1.0, 1.0, 3.0, float(k), 2.0, "BAD" if p == "P4" else None, "RESOLVED" if p else "UNRESOLVED"))
    con.executemany(f"INSERT INTO src.player_games VALUES ({', '.join('?' * 24)})", rows)
    con.execute("""CREATE TABLE src.substitutions (canonical_game_id VARCHAR, canonical_team_id VARCHAR, canonical_player_id VARCHAR,
                   sub_in_period INTEGER, sub_in_seconds_remaining INTEGER, sub_out_period INTEGER, sub_out_seconds_remaining INTEGER,
                   timing_complete BOOLEAN)""")
    con.execute("""INSERT INTO src.substitutions VALUES ('G1','T1','P1',1,1200,2,0,TRUE), ('G1','T1','P6',1,1200,1,600,TRUE),
                   ('G1','T1','P2',1,1200,2,0,TRUE), ('G1','T1','P1',1,1200,2,0,TRUE), ('G1','T1','P1',1,1200,2,0,TRUE),
                   ('G1','T1','P1',1,1200,2,0,TRUE)""")
    con.execute("CREATE TABLE src.team_games (canonical_game_id VARCHAR, canonical_team_id VARCHAR, game_minutes DOUBLE, possessions_source DOUBLE)")
    con.execute("INSERT INTO src.team_games VALUES ('G1','T1',40,70), ('G1','T2',40,71), ('G2','T2',45,77), ('G2','T3',45,76)")
    con.execute("CREATE TABLE src.rosters (canonical_player_id VARCHAR, canonical_team_id VARCHAR, season INTEGER)")
    con.execute("INSERT INTO src.rosters VALUES ('P1','T1',2027), ('P7','T3',2027), ('P8','T1',2027)")
    con.execute("CREATE TABLE src.identity_conflicts (canonical_player_id VARCHAR, season INTEGER)")
    con.execute("INSERT INTO src.identity_conflicts VALUES ('P5', 2027)")
    con.execute("CREATE TABLE src.recruits (canonical_player_id VARCHAR)")
    con.execute("INSERT INTO src.recruits VALUES ('P9')")
    con.execute("CREATE TABLE src.transfers (canonical_player_id VARCHAR, candidate_canonical_player_id VARCHAR)")
    con.execute("INSERT INTO src.transfers VALUES (NULL, 'P5')")
    # the pregame universe: P9 / P5 / P4 box rows fall outside it (diagnostic rows); P6 / P7 / P8 have no box row
    ex = [("E1", "TE1", "G1", "T1", "P1"), ("E2", "TE1", "G1", "T1", "P2"), ("E3", "TE1", "G1", "T1", "P6"),
          ("E4", "TE1", "G1", "T1", "P8"), ("E5", "TE2", "G1", "T2", "P3"), ("E6", "TE3", "G2", "T2", "P3"),
          ("E7", "TE4", "G2", "T3", "P7")]
    con.execute("""CREATE TABLE fx.ex.player_game_examples (player_game_example_id VARCHAR, game_example_id VARCHAR,
                   canonical_game_id VARCHAR, canonical_team_id VARCHAR, canonical_player_id VARCHAR, season INTEGER)""")
    con.executemany("INSERT INTO fx.ex.player_game_examples VALUES (?, ?, ?, ?, ?, 2027)", ex)
    con.execute("""CREATE TABLE fx.ex.team_game_examples (game_example_id VARCHAR, canonical_game_id VARCHAR, canonical_team_id VARCHAR,
                   season INTEGER, is_home BOOLEAN)""")
    con.execute("""INSERT INTO fx.ex.team_game_examples VALUES ('TE1','G1','T1',2027,TRUE), ('TE2','G1','T2',2027,FALSE),
                   ('TE3','G2','T2',2027,TRUE), ('TE4','G2','T3',2027,FALSE)""")
    con.execute("CREATE TABLE fx.ex.game_examples (canonical_game_id VARCHAR, prediction_cutoff_utc TIMESTAMPTZ, scheduled_tip_utc TIMESTAMPTZ)")
    con.execute("""INSERT INTO fx.ex.game_examples VALUES ('G1','2026-11-04 23:00:00+00','2026-11-05 00:00:00+00'),
                   ('G2','2026-11-06 23:00:00+00','2026-11-07 00:00:00+00')""")
    con.execute("""CREATE TABLE fx.ex.team_membership_evidence (canonical_player_id VARCHAR, canonical_team_id VARCHAR, season INTEGER,
                   available_from TIMESTAMPTZ)""")
    con.execute("INSERT INTO fx.ex.team_membership_evidence VALUES ('P4','T2',2027,'2026-12-01 00:00:00+00')")
    return con


def _rel(con, rel):
    return con.execute(f"SELECT * FROM {rel}").df()


def test_kept_statements_are_the_frozen_ones_minus_diagnostics():
    calls = []

    class Rec:
        def execute(self, s, *a, **k):
            calls.append(s)
    LB.build(Rec())
    frozen = "".join(calls)
    kept = TR.label_statements()
    assert all(s in frozen for s in kept)                       # verbatim
    text = "\n".join(kept)
    for d in TR.LABEL_DIAGNOSTIC_RELATIONS:
        assert d.split(".")[1] not in text
    for r in TR.LABEL_RELATIONS:
        assert f"TABLE {r} AS" in text
    assert len(kept) == 5                                       # schema, cand, part, the two consumed relations


def test_consumed_relations_identical_to_full_frozen_build():
    full, mini = _db(), _db()
    LB.build(full)
    for s in TR.label_statements():
        mini.execute(s)
    assert len(_rel(full, "lab.target_box_rows_outside_universe")) > 0      # the fixture exercises the dropped branch
    assert len(_rel(full, "lab.player_pool_misses")) > 0
    for r in TR.LABEL_RELATIONS:
        a, b = _rel(full, r), _rel(mini, r)
        assert len(a) > 0
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    have = {f"{s}.{t}" for s, t in mini.execute("SELECT schema_name, table_name FROM duckdb_tables() WHERE schema_name = 'lab'").fetchall()}
    assert have == set(TR.LABEL_RELATIONS)


def test_changed_frozen_relation_set_is_refused(monkeypatch):
    orig = LB.build

    def extra(con):
        orig(con)
        con.execute("CREATE OR REPLACE TABLE lab.something_new AS SELECT 1")
    monkeypatch.setattr(LB, "build", extra)
    # a NEW relation is kept verbatim only if it reads no diagnostic relation; a removed diagnostic must refuse
    assert "CREATE OR REPLACE TABLE lab.something_new AS SELECT 1" in TR.label_statements()

    def fewer(con):
        class Cut:
            def execute(self, s, *a, **k):
                con.execute(s.split("CREATE OR REPLACE TABLE lab.player_pool_misses")[0])
        orig(Cut())
    monkeypatch.setattr(LB, "build", fewer)
    with pytest.raises(TR.TrainingRowsError):
        TR.label_statements()
