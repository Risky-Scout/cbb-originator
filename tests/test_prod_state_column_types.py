"""prod/state._upsert column typing (2026-11-10 Droplet: every sync after the first 'PTS_IDENTITY' box flag raised
STATE_UPDATE_FAILED 'Could not convert string PTS_IDENTITY to INT32 ... accounting_issues': the increment's
player_game_outcomes / team_game_outcomes tables were created from an all-NULL first batch, which DuckDB types INTEGER)."""
import duckdb
import pandas as pd
import pytest

from cbb_platform.prod import state as ST

REL = "flagship.player_game_outcomes"


def _rows(gid, pid, issue, points=6):
    return pd.DataFrame({"canonical_game_id": [gid], "canonical_team_id": ["T-1"], "canonical_player_id": [pid],
                         "player_game_key": [f"PG-{gid}-{pid}"], "points": [points],
                         "accounting_issues": pd.Series([issue], dtype=object)})


@pytest.fixture
def con():
    c = duckdb.connect()
    ST._empty_tables(c)
    yield c
    c.close()


def _type(con, col):
    return {r[0]: r[1] for r in con.execute(f"DESCRIBE {REL}").fetchall()}[col]


def test_all_null_first_batch_then_flagged_row_upserts(con):
    ST._upsert(con, REL, _rows("G-1", "P-a", None))
    assert _type(con, "accounting_issues") == "INTEGER"                 # the production failure precondition
    ST._upsert(con, REL, _rows("G-2", "P-b", "PTS_IDENTITY"))             # raised ConversionException before the fix
    assert _type(con, "accounting_issues") == "VARCHAR"
    got = con.execute(f"SELECT canonical_game_id, points, accounting_issues FROM {REL} ORDER BY 1").fetchall()
    assert got == [("G-1", 6, None), ("G-2", 6, "PTS_IDENTITY")]          # earlier rows kept, values unchanged


def test_a_column_holding_values_is_never_retyped(con):
    ST._upsert(con, REL, _rows("G-1", "P-a", "PTS_IDENTITY"))
    ST._upsert(con, REL, _rows("G-2", "P-b", None))                       # all-NULL batch: no flip back to INTEGER
    assert _type(con, "accounting_issues") == "VARCHAR"
    assert ST._adopt_types(con, REL, _rows("G-3", "P-c", None)) == []
    assert con.execute(f"SELECT count(*), count(accounting_issues) FROM {REL}").fetchone() == (2, 1)


def test_upsert_replaces_the_same_key_after_retyping(con):
    ST._upsert(con, REL, _rows("G-1", "P-a", None, points=5))
    ST._upsert(con, REL, _rows("G-1", "P-a", "PTS_IDENTITY", points=7))   # same key: corrected box replaces the row
    assert con.execute(f"SELECT points, accounting_issues FROM {REL}").fetchall() == [(7, "PTS_IDENTITY")]
