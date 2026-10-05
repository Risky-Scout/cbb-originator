"""Raw immutability: re-storing a payload never mutates prior raw artifacts."""
import hashlib
import os

import pytest

from cbb_platform import raw_store


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_identical_payload_is_idempotent(tmp_path):
    a = raw_store.store("bdl", "games", b'{"data": [1]}', params={"x": 1}, root=tmp_path)
    before = (_digest(a.path), a.path.stat().st_mtime_ns)
    b = raw_store.store("bdl", "games", b'{"data": [1]}', params={"x": 1}, root=tmp_path)
    assert a.created and not b.created and a.path == b.path
    assert (_digest(a.path), a.path.stat().st_mtime_ns) == before
    payloads = [f for f in (tmp_path / "bdl" / "games").iterdir() if not f.name.endswith(".meta.json")]
    assert len(payloads) == 1


def test_changed_payload_appends_new_file(tmp_path):
    a = raw_store.store("bdl", "games", b'{"data": [1]}', root=tmp_path)
    b = raw_store.store("bdl", "games", b'{"data": [2]}', root=tmp_path)
    assert a.path != b.path and a.path.exists() and b.path.exists()
    assert [m["sha256"] for _, m in raw_store.records("bdl", root=tmp_path)] == [a.sha256, b.sha256]


def test_raw_files_are_read_only(tmp_path):
    a = raw_store.store("cbbd", "plays/game", b"[]", root=tmp_path)
    assert not os.access(a.path, os.W_OK)
    with pytest.raises(PermissionError):
        a.path.write_bytes(b"tampered")


def test_project_raw_store_is_read_only_and_hash_consistent():
    """Every raw payload actually stored in data/raw still matches its recorded sha256."""
    for path, meta in raw_store.records("bdl"):
        assert _digest(path) == meta["sha256"]
        assert not os.access(path, os.W_OK)


def test_identical_bytes_from_a_different_request_keep_their_own_provenance(tmp_path):
    """Stage 1 final unblock: two CBBD games that both return `[]` must not collapse into one record."""
    a = raw_store.store("cbbd", "plays/game", b"[]", params={"gameId": 14}, root=tmp_path)
    b = raw_store.store("cbbd", "plays/game", b"[]", params={"gameId": 35928}, root=tmp_path)
    assert a.created and b.created and a.path != b.path
    assert sorted(m["params"]["gameId"] for _, m in raw_store.records("cbbd", root=tmp_path)) == [14, 35928]
