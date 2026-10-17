"""Reproducibility and immutability (Step 12 integrity gate, also run as tests)."""
import json

import duckdb

from cbb_platform import config as C, lockbox_state as S
from cbb_platform.audit_starter_pack import starter_pack_manifest
from cbb_platform.build_canonical import build


def test_rebuild_from_inputs_is_deterministic(db, tmp_path, monkeypatch):
    """A fresh build from the same immutable inputs must reproduce every table fingerprint exactly
    (all columns except processed_at).

    Post-lockbox (cbb_platform/lockbox_state.py): the raw store also holds the one authorized 2025-26 fetch, which is not
    an input of the frozen development build. The inputs are then the raw store minus EXACTLY those payloads (the 987
    request-log lines added by the closure commit, sha256-verified); every other payload is read, none is ignored."""
    if S.current() != S.PRE_LOCKBOX_READINESS:
        assert S.historical_raw_root(tmp_path / "raw")["excluded_files"] == 2 * S.AUTHORIZED_REQUESTS
        monkeypatch.setattr(C, "RAW", tmp_path / "raw")
    fresh = build(db_path=tmp_path / "rebuild.duckdb", parquet_root=tmp_path / "parquet")
    current = dict(db.execute("""SELECT table_name, content_fingerprint FROM metadata.data_versions
                                 WHERE build_id = (SELECT max(build_id) FROM metadata.data_versions)""").fetchall())
    rebuilt = {t: r["fingerprint"] for t, r in fresh["tables"].items()}
    assert rebuilt == current


def test_starter_pack_bytes_unchanged_since_audit():
    recorded = json.loads((C.MANIFESTS / "starter_pack_sha256.json").read_text())["files"]
    now = starter_pack_manifest()
    data = lambda m: {k: v for k, v in m.items() if not k.endswith(".DS_Store")}
    assert data(now) == data(recorded)


def test_parquet_exports_match_database(db):
    """Every non-empty canonical table has a Parquet twin with identical row count."""
    for table, rows in db.execute("""SELECT table_name, row_count FROM metadata.data_versions
                                     WHERE build_id = (SELECT max(build_id) FROM metadata.data_versions)""").fetchall():
        schema, name = table.split(".")
        path = C.CANONICAL / schema / f"{name}.parquet"
        if rows:
            assert duckdb.sql(f"SELECT count(*) FROM '{path}'").fetchone()[0] == rows, table
        else:
            assert not path.exists(), table
