"""Shared test fixtures.

`db`         read-only connection to the BUILT canonical database (run the build first).
`starter_db` the same database (READ_ONLY), Starter Pack staging views bound to the verified current Starter Pack.
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


@pytest.fixture(scope="session")
def starter_db():
    """`db` for checks that read the Starter Pack staging views: the frozen database attached READ_ONLY, its staging
    views bound to the current Starter Pack only after its registered content identity is proven (relocation.py)."""
    from cbb_platform import relocation
    if not C.DUCKDB_PATH.exists():
        pytest.fail(f"{C.DUCKDB_PATH} missing — run `.venv/bin/python -m cbb_platform.build_canonical` first")
    con = relocation.connect_canonical_relocated()
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


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _game_model_off_by_default(monkeypatch):
    """Release B: the game layer is switched on by the production units (CBB_GAME_MODEL=SHADOW). Tests run with it
    OFF unless a test opts in with monkeypatch.setenv, so legacy player-contract tests stay deterministic."""
    monkeypatch.delenv("CBB_GAME_MODEL", raising=False)


def guards_allowing_removed_user_qa(guard_fn):
    """Part XXVI resolution (OBSOLETE_PRE_CLEANUP_CONTRACT): data/qa/game_identity_questionable_only.csv was a
    git-ignored user scratch file deleted in the user-approved 2026-11-17 workspace cleanup. Every frozen model artifact
    must still be unchanged; the QA file may only be ABSENT (sha None) -- a present-but-modified file still fails."""
    import ast
    from cbb_platform.v2 import joint_minutes_v2 as JM
    try:
        return guard_fn(), True
    except JM.JMViolation as e:
        out = ast.literal_eval(str(e).split(": ", 1)[1])
        bad = [k for k, v in out.items() if k.endswith("UNCHANGED") and not v and k != "USER_QA_FILE_UNCHANGED"]
        assert not bad, bad
        assert out["USER_QA_FILE_sha256"] is None and not JM.USER_QA_FILE.exists(), "user QA file MODIFIED"
        return out, False
