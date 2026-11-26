"""Feature warmup: one frozen Stage 3 build per IDENTICAL information state (training_rows.information_state_groups),
not one per distinct cutoff. 2026-11-26 root cause: ~220 s per build whatever the number of games, 27..39 builds per
morning (51 -> 135 min warmup, INITIAL 12:37Z -> 14:16Z). These tests prove, against the real compact_canonical mask,
that cutoffs with no as_of-mask instant between them see a byte-identical masked canonical DB, and that the grouping
splits exactly at every instant, every state-version change, and never merges across them."""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from cbb_platform.prod import config as PC, features as PF
from cbb_platform.walkforward import gates as GT, training_rows as TR

S = PC.PRODUCTION_SEASON
U = lambda s: pd.Timestamp(s, tz="UTC")
GAME_COLS = """season INTEGER, canonical_game_id VARCHAR, home_points INTEGER, away_points INTEGER,
    home_period_points INTEGER[], away_period_points INTEGER[], overtime_count INTEGER, has_team_box_score BOOLEAN,
    has_player_box_score BOOLEAN, tip_time_quality VARCHAR, scheduled_tip_utc TIMESTAMPTZ, game_date_et DATE"""


def _game(con, rel, season, gid, tip, pts):
    h = "NULL" if pts is None else pts
    con.execute(f"""INSERT INTO {rel} VALUES ({season}, '{gid}', {h}, {h}, NULL, NULL, 0, {pts is not None},
                    {pts is not None}, 'EXACT', TIMESTAMPTZ '{tip}', DATE '{pd.Timestamp(tip).date()}')""")


def _outcome_tables(con, rows):
    for rel in ("team_game_outcomes", "player_game_outcomes", "substitutions"):
        con.execute(f"CREATE TABLE flagship.{rel} (season INTEGER, canonical_game_id VARCHAR, historical_knowable_at TIMESTAMPTZ, v INTEGER)")
        for season, gid, t in rows:
            con.execute(f"INSERT INTO flagship.{rel} VALUES ({season}, '{gid}', TIMESTAMPTZ '{t}', 1)")


@pytest.fixture()
def dbs(tmp_path):
    """History (one 2025-26 game) + a 2026-27 increment: A tips 12:00Z (knowable 16:00Z), B tips 20:00Z (knowable
    2026-11-26 00:00Z), target T tips 2026-11-26 02:00Z (no result)."""
    h, i = tmp_path / "hist.duckdb", tmp_path / "inc.duckdb"
    con = duckdb.connect(str(h))
    con.execute("SET TimeZone = 'UTC'")
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA {s}")
    con.execute(f"CREATE TABLE flagship.played_games ({GAME_COLS})")
    _game(con, "flagship.played_games", 2026, "G-H", "2026-03-01 18:00:00+00", 70)
    _outcome_tables(con, [(2026, "G-H", "2026-03-01 22:00:00+00")])
    for rel in PF.UNION_RELATIONS:
        con.execute(f"CREATE TABLE {rel} (k VARCHAR)")
        con.execute(f"INSERT INTO {rel} VALUES ('x')")
    for rel in PF.EMPTY_RELATIONS:
        con.execute(f"CREATE TABLE {rel} (k VARCHAR)")
    con.close()
    con = duckdb.connect(str(i))
    con.execute("SET TimeZone = 'UTC'")
    con.execute("CREATE SCHEMA flagship")
    con.execute(f"CREATE TABLE flagship.games ({GAME_COLS})")
    _game(con, "flagship.games", S, "G-A", "2026-11-25 12:00:00+00", 60)
    _game(con, "flagship.games", S, "G-B", "2026-11-25 20:00:00+00", 61)
    _game(con, "flagship.games", S, "G-T", "2026-11-26 02:00:00+00", None)
    _outcome_tables(con, [(S, "G-A", "2026-11-25 16:00:00+00"), (S, "G-B", "2026-11-26 00:00:00+00")])
    con.close()
    return h, i


def _masked(tmp_path, h, i, as_of, name):
    out = tmp_path / f"{name}.duckdb"
    PF.compact_canonical(out, ["G-T"], as_of, S, h, i, True)
    con = duckdb.connect(str(out), read_only=True)
    rels = [f"{a}.{b}" for a, b in con.execute("SELECT schema_name, table_name FROM duckdb_tables() ORDER BY 1, 2").fetchall()]
    frames = {r: con.execute(f"SELECT * FROM {r} ORDER BY ALL").df() for r in rels}
    con.close()
    return frames


def _same(a, b):
    return a.keys() == b.keys() and all(a[k].equals(b[k]) for k in a)


def test_mask_instants_are_exactly_the_admission_times_lo_exclusive_hi_inclusive(dbs):
    h, i = dbs
    assert PF.as_of_mask_instants(h, i, S, U("2026-11-25 16:00"), U("2026-11-25 23:59")) == []
    assert PF.as_of_mask_instants(h, i, S, U("2026-11-25 15:59"), U("2026-11-25 16:00")) == [U("2026-11-25 16:00").isoformat()]
    assert PF.as_of_mask_instants(h, i, S, U("2026-11-25 13:00"), U("2026-11-26 01:00")) == [
        U("2026-11-25 16:00").isoformat(), U("2026-11-26 00:00").isoformat()]
    with pytest.raises(PF.FeatureBuildError):
        PF.as_of_mask_instants(h, i, S, U("2026-11-26"), U("2026-11-25"))


def test_no_instant_in_the_window_means_a_byte_identical_masked_canonical_db(dbs, tmp_path):
    h, i = dbs
    lo, hi = U("2026-11-25 16:30"), U("2026-11-25 23:30")              # no instant in (lo, hi]
    assert PF.as_of_mask_instants(h, i, S, lo, hi) == []
    assert _same(_masked(tmp_path, h, i, lo, "lo"), _masked(tmp_path, h, i, hi, "hi"))
    later = U("2026-11-26 00:30")                                       # B's result became knowable at 00:00Z
    assert PF.as_of_mask_instants(h, i, S, lo, later) == [U("2026-11-26 00:00").isoformat()]
    assert not _same(_masked(tmp_path, h, i, lo, "lo2"), _masked(tmp_path, h, i, later, "later"))


def _groups(i, spec):
    st = {sv: {"state_version": sv, "increment_db": i, "created_at": U("2026-11-25 10:00")} for _, sv, _ in spec}
    g = {}
    for cut, sv, gids in spec:
        g.setdefault((U(cut), sv), (st[sv], []))[1].extend(gids)
    return g


def test_grouping_merges_only_identical_information_states(dbs):
    h, i = dbs
    spec = [("2026-11-25 13:00", "S1", ["g1"]), ("2026-11-25 14:00", "S1", ["g2", "g3"]),
            ("2026-11-25 16:00", "S1", ["g4"]),             # instant 16:00 in (13:00, 16:00]: new state
            ("2026-11-25 18:00", "S1", ["g5"]),
            ("2026-11-25 19:00", "S2", ["g6"]),             # new state version: never merged across
            ("2026-11-25 21:00", "S2", ["g7"])]
    out = TR.information_state_groups(_groups(i, spec), h, S)
    got = [(k[0], k[1], v[1], v[3]) for k, v in out]
    assert [(a, sv, gids) for a, sv, gids, _ in got] == [
        (U("2026-11-25 13:00"), "S1", ["g1", "g2", "g3"]), (U("2026-11-25 16:00"), "S1", ["g4", "g5"]),
        (U("2026-11-25 19:00"), "S2", ["g6", "g7"])]
    first = out[0][1]
    assert first[2] == {"g1": U("2026-11-25 13:00"), "g2": U("2026-11-25 14:00"), "g3": U("2026-11-25 14:00")}
    assert first[3]["equivalent_through"] == U("2026-11-25 14:00").isoformat() and first[3]["group_cutoffs"] == 2
    single = TR.information_state_groups(_groups(i, [("2026-11-25 13:00", "S1", ["g1", "g2"])]), h, S)
    assert single[0][1][3] is None                                   # one cutoff: the incumbent meta, unchanged


def _meta(as_of, cut, info=None, sv="S1"):
    b = {"as_of": as_of, "target_outcome_rows": 0, "post_as_of_rows": 0}
    if info:
        b["information_state"] = info
    return {"prediction_cutoff_utc": cut, "state_version": sv, "build": b}


def test_pit_gate_accepts_only_a_recorded_earlier_identical_state():
    info = {"rule": "IDENTICAL_AS_OF_MASKED_CANONICAL", "as_of": "2026-11-25T13:00:00+00:00", "state_version": "S1",
            "equivalent_through": "2026-11-25T14:00:00+00:00", "group_cutoffs": 2, "as_of_mask_instants_in_window": 0}
    assert GT.build_as_of_legal(_meta("2026-11-25 14:00:00+00", "2026-11-25T14:00:00+00:00"))       # incumbent
    assert GT.build_as_of_legal(_meta("2026-11-25 13:00:00.000000+00", "2026-11-25T14:00:00+00:00", info))
    assert not GT.build_as_of_legal(_meta("2026-11-25 13:00:00+00", "2026-11-25T14:00:00+00:00"))  # no proof
    assert not GT.build_as_of_legal(_meta("2026-11-25 13:00:00+00", "2026-11-25T15:00:00+00:00", info))  # beyond proof
    assert not GT.build_as_of_legal(_meta("2026-11-25 13:00:00+00", "2026-11-25T14:00:00+00:00", info, sv="S2"))
    assert not GT.build_as_of_legal(_meta("2026-11-25 15:00:00+00", "2026-11-25T14:00:00+00:00", info))  # later as_of
    assert not GT.build_as_of_legal(_meta("2026-11-25 13:00:00+00", "2026-11-25T14:00:00+00:00",
                                          {**info, "as_of_mask_instants_in_window": 1}))
