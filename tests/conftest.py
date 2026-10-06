"""Shared test fixtures.

`db`         read-only connection to the BUILT canonical database (run the build first).
`empty_db`   in-memory database with the canonical schema + ID macros, for adapter tests that
             use the clearly labeled SYNTHETIC fixtures in tests/fixtures/ (never production data).
`raw_root`   temporary raw store; patches config.RAW so adapters never touch data/raw in tests.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from cbb_platform import config as C
from cbb_platform.build_canonical import ID_MACROS

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def db():
    if not C.DUCKDB_PATH.exists():
        pytest.fail(f"{C.DUCKDB_PATH} missing — run `.venv/bin/python -m cbb_platform.build_canonical` first")
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    yield con
    con.close()


@pytest.fixture
def empty_db():
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(C.SCHEMA_SQL.read_text())
    con.execute(ID_MACROS)
    con.execute("SET VARIABLE ver = 'test'")
    con.execute("SET VARIABLE proc = now()")
    yield con
    con.close()


@pytest.fixture
def raw_root(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    monkeypatch.setattr(C, "RAW", root)
    return root


def scalar(con, sql, params=None):
    return con.execute(sql, params or []).fetchone()[0]
