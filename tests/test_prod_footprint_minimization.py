"""Compact production history (Method B): source selection, candidate verification, frozen-reader restoration.

Unit parts run everywhere (synthetic files, no stores). Store-backed parts SKIP visibly when the compact candidate or
the frozen stores are absent (GitHub-hosted CI holds neither)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from cbb_platform.prod import config as PC
from cbb_platform.v2 import phase3 as P3, provenance as PV
from cbb_platform.walkforward import daily as DL, history_source as HS

FROZEN_DV = json.loads(HS.FROZEN_MANIFEST.read_text())["data_versions"]


def _fake_candidate(tmp_path, sources=None):
    cand = tmp_path / "cand.duckdb"
    cand.write_bytes(b"compact-history-candidate")
    man = {"candidate_sha256": HS.HX.sha256_file(cand), "candidate_bytes": cand.stat().st_size,
           "sources": sources or {k: {"sha256": v["sha256"]} for k, v in FROZEN_DV.items()}}
    HS.manifest_path(cand).write_text(json.dumps(man))
    return cand


def test_default_is_control_and_unchanged(monkeypatch):
    monkeypatch.delenv(HS.ENV_SOURCE, raising=False)
    assert HS.selected() == "CONTROL"
    assert HS.history_db() == PC.HISTORY_CANONICAL
    assert HS.stage3_paths() == (PV.LB_FEATURES, PV.LB_LABELS)
    before = (PV.LB_FEATURES, PV.LB_LABELS, P3.load_store)
    with HS.active() as src:
        assert src == "CONTROL" and (PV.LB_FEATURES, PV.LB_LABELS, P3.load_store) == before


def test_unknown_source_refused(monkeypatch):
    monkeypatch.setenv(HS.ENV_SOURCE, "SOMETHING_ELSE")
    with pytest.raises(HS.HistorySourceError):
        HS.selected()


def test_candidate_selection_verifies_manifest_hash(tmp_path, monkeypatch):
    cand = _fake_candidate(tmp_path)
    monkeypatch.setenv(HS.ENV_SOURCE, "CANDIDATE")
    monkeypatch.setenv(HS.ENV_CANDIDATE, str(cand))
    assert DL.history_identity() == {k: v["sha256"] for k, v in FROZEN_DV.items()}     # same ids as CONTROL
    assert HS.history_db() == cand and HS.stage3_paths() == (cand, cand)
    cand.write_bytes(b"compact-history-candidatX")                                      # same size, other bytes
    with pytest.raises(HS.HistorySourceError, match="sha256"):
        DL.history_identity()
    cand.write_bytes(b"short")
    with pytest.raises(HS.HistorySourceError, match="bytes"):
        HS.identity()


def test_candidate_from_other_sources_refused(tmp_path, monkeypatch):
    cand = _fake_candidate(tmp_path, sources={k: {"sha256": "0" * 64} for k in FROZEN_DV})
    monkeypatch.setenv(HS.ENV_SOURCE, "CANDIDATE")
    monkeypatch.setenv(HS.ENV_CANDIDATE, str(cand))
    with pytest.raises(HS.HistorySourceError, match="data_versions"):
        HS.identity()


def test_active_patches_and_restores_frozen_readers(tmp_path, monkeypatch):
    cand = _fake_candidate(tmp_path)
    monkeypatch.setenv(HS.ENV_SOURCE, "CANDIDATE")
    monkeypatch.setenv(HS.ENV_CANDIDATE, str(cand))
    before = (PV.LB_FEATURES, PV.LB_LABELS, P3.load_store)
    with pytest.raises(RuntimeError, match="boom"):
        with HS.active():
            assert PV.LB_FEATURES == cand and PV.LB_LABELS == cand and P3.load_store is HS.load_store_candidate
            raise RuntimeError("boom")
    assert (PV.LB_FEATURES, PV.LB_LABELS, P3.load_store) == before


def test_no_network_import_in_history_source():
    src = HS.__file__
    text = open(src).read()
    assert "import socket" not in text and "requests" not in text


# ---------------------------------------------------------------- store-backed (skip visibly without the stores)
_needs = pytest.mark.skipif(not HS.DEFAULT_CANDIDATE.exists() or not PV.LB_FEATURES.exists(),
                            reason="compact candidate / frozen Stage 3 store absent (local stores only)")


@_needs
def test_candidate_verifies_and_holds_the_traced_subset():
    man = HS.verify_candidate(HS.DEFAULT_CANDIDATE)
    assert set(HS.HX.RELATIONS) <= set(man["relations"])
    assert len(man["columns"]["player_features"]) == 46 and len(man["columns"]["player_box_state"]) == 15
    assert "oos_forecasts_phase1.parquet" not in json.dumps(man["sources"])


@_needs
def test_candidate_oos_store_equals_parquet(monkeypatch):
    monkeypatch.setenv(HS.ENV_SOURCE, "CANDIDATE")
    monkeypatch.setenv(HS.ENV_CANDIDATE, str(HS.DEFAULT_CANDIDATE))
    a = P3.load_store([2023, 2024, 2025, 2026])
    b = HS.load_store_candidate([2023, 2024, 2025, 2026])
    pd.testing.assert_frame_equal(a, b, check_exact=True)
