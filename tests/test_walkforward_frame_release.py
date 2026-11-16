"""walkforward.fit.frame_released_after_copy + duckdb_limits.drop_resident (release 2026-11-15): after marginal.fit_v0
has copied its training rows, the spilled input frame's resident pages are dropped. Values never change: the frame still
reads back identically, and a real fit_v0 on a spilled frame equals the fit on the anonymous-memory frame bit for bit."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from cbb_platform import duckdb_limits as DLM, props_v0 as P
from cbb_platform.v2 import marginal as M
from cbb_platform.walkforward import fit as WF


def _spilled(monkeypatch, tmp_path, df):
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "spill"))
    monkeypatch.setattr(DLM, "SPILL_MIN_BYTES", 1)
    return DLM.spill_frame(df)


def _train_frame(n=6_000, seed=5):
    cfg = P.load_config()
    rng = np.random.default_rng(seed)
    d = {c: np.where(rng.random(n) < 0.08, np.nan, rng.gamma(2.0, 6.0, n)) for c in cfg["minutes_features"]}
    for t in ["min"] + list(cfg["count_targets"].values()):
        d[f"cur_{t}"] = np.where(rng.random(n) < 0.05, np.nan, rng.gamma(2.0, 40.0, n))
        for j in range(1, cfg["recency"]["prior_seasons_used"] + 1):
            d[f"h{j}_{t}"] = np.where(rng.random(n) < 0.3, np.nan, rng.gamma(2.0, 200.0, n))
    for j in ("cur", "h1"):
        d[f"{j}_n_app"] = rng.integers(0, 30, n).astype(float)
    d["y_min"] = rng.gamma(4.0, 6.0, n)
    for t in cfg["count_targets"].values():
        d[f"y_{t}"] = rng.poisson(3.0, n).astype(float)
    d["pos"] = rng.choice(list(P.POS_GROUPS), n)
    d["cold_start_class"] = rng.choice(list(P.COLD), n)
    d["is_home"] = rng.integers(0, 2, n).astype(float)
    d["season"] = rng.choice([2024, 2025, 2026], n)
    d["prediction_cutoff_utc"] = pd.Timestamp("2026-01-01", tz="UTC")
    return pd.DataFrame(d), cfg


def test_drop_resident_keeps_values(monkeypatch, tmp_path):
    df = pd.DataFrame({"a": np.arange(300_000, dtype=float), "b": np.arange(300_000, dtype=np.int64), "c": "x"})
    sp = _spilled(monkeypatch, tmp_path, df)
    assert DLM.drop_resident(sp) > 0
    pd.testing.assert_frame_equal(sp, df)                        # pages fault back in unchanged
    assert DLM.drop_resident(df) == 0                            # anonymous frame: nothing to advise


def test_hook_fires_once_and_is_restored(monkeypatch, tmp_path):
    frozen = M.season_weights
    calls = []
    monkeypatch.setattr(DLM, "drop_resident", lambda f: calls.append(f) or 7)
    frame = pd.DataFrame({"x": [1.0]})
    with WF.frame_released_after_copy(frame) as st:
        a = M.season_weights(np.array([2025, 2026]), 2026, "V1REF")
        b = M.season_weights(np.array([2025, 2026]), 2026, "V1REF")
    assert M.season_weights is frozen and len(calls) == 1 and st["dropped"] == 7
    assert a.tobytes() == b.tobytes() == frozen(np.array([2025, 2026]), 2026, "V1REF").tobytes()
    with pytest.raises(RuntimeError):
        with WF.frame_released_after_copy(frame):
            raise RuntimeError("fit failed")
    assert M.season_weights is frozen


def test_refuses_when_not_frozen(monkeypatch):
    monkeypatch.setattr(M, "season_weights", lambda *a: None)
    with pytest.raises(WF.WalkForwardFitError):
        with WF.frame_released_after_copy(pd.DataFrame()):
            pass


def test_fit_v0_on_spilled_frame_with_release_is_bit_identical(monkeypatch, tmp_path):
    df, cfg = _train_frame()
    finals = pd.DataFrame({"season": [2025] * 50, "min": np.linspace(10, 35, 50), "pos": ["G"] * 50,
                           **{t: np.linspace(1, 9, 50) for t in cfg["count_targets"].values()}})
    policy = next(iter(M.REFERENCE))
    ref = M.fit_v0(df.copy(), finals, cfg, 2026, policy, "TEST")
    sp = _spilled(monkeypatch, tmp_path, df.copy())
    with WF.bounded_predict_means(), WF.frame_released_after_copy(sp) as st:
        got = M.fit_v0(sp, finals, cfg, 2026, policy, "TEST")
    assert st["dropped"] and st["dropped"] > 0
    assert json.dumps(got, sort_keys=True, default=str) == json.dumps(ref, sort_keys=True, default=str)


def test_spill_in_place_same_object_same_values(monkeypatch, tmp_path):
    df = pd.DataFrame({"a": np.arange(300_000, dtype=float), "b": np.arange(300_000, dtype=np.int64), "c": "x"})
    df["d"] = df["a"] * 2.0                                      # unconsolidated float group, as tr after tr["_w"] = ...
    want = df.copy()
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "spill"))
    monkeypatch.setattr(DLM, "SPILL_MIN_BYTES", 1)
    monkeypatch.setattr(DLM, "SPILL_FLUSH_ROWS", 1)
    ident = id(df)
    maps = DLM.spill_in_place(df)
    assert id(df) == ident and maps and df._mgr.is_consolidated()
    pd.testing.assert_frame_equal(df, want)
    with DLM.kept_paged_out(maps) as entry:
        assert DLM._pageout_kept() > 0
    assert entry == [] and DLM._KEPT_OUT == []
    pd.testing.assert_frame_equal(df, want)                     # pages fault back in unchanged


def test_spill_in_place_noop_without_configuration(monkeypatch):
    monkeypatch.delenv(DLM.TMP_ENV, raising=False)
    df = pd.DataFrame({"a": np.arange(10, dtype=float)})
    mgr = df._mgr
    assert DLM.spill_in_place(df) == [] and df._mgr is mgr


def test_training_rows_spilled_restores_and_refuses(monkeypatch):
    frozen = P._design
    with pytest.raises(RuntimeError):
        with WF.training_rows_spilled():
            raise RuntimeError("fit failed")
    assert P._design is frozen
    monkeypatch.setattr(P, "_design", lambda *a, **k: None)
    with pytest.raises(WF.WalkForwardFitError):
        with WF.training_rows_spilled():
            pass


def test_fit_v0_with_training_rows_spilled_is_bit_identical(monkeypatch, tmp_path):
    df, cfg = _train_frame()
    finals = pd.DataFrame({"season": [2025] * 50, "min": np.linspace(10, 35, 50), "pos": ["G"] * 50,
                           **{t: np.linspace(1, 9, 50) for t in cfg["count_targets"].values()}})
    policy = next(iter(M.REFERENCE))
    ref = M.fit_v0(df.copy(), finals, cfg, 2026, policy, "TEST")
    sp = _spilled(monkeypatch, tmp_path, df.copy())
    monkeypatch.setattr(DLM, "SPILL_FLUSH_ROWS", 3)
    monkeypatch.setattr(WF, "PREDICT_CHUNK_ROWS", 1_024)
    with WF.bounded_predict_means(), WF.frame_released_after_copy(sp), WF.training_rows_spilled() as st:
        got = M.fit_v0(sp, finals, cfg, 2026, policy, "TEST")
    assert st["spilled"] and st["spilled"] > 0 and P._design is WF._FROZEN_DESIGN and DLM._KEPT_OUT == []
    assert json.dumps(got, sort_keys=True, default=str) == json.dumps(ref, sort_keys=True, default=str)
