"""walkforward.fit.chunked_predict_means / bounded_predict_means (footprint 2026-11-14): the frozen props_v0._predict_means
on 64-row-aligned row blocks returns byte-identical arrays, and the substitution is scoped to one marginal fit."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.walkforward import fit as WF


def _frame(n, seed=3):
    cfg = P.load_config()
    rng = np.random.default_rng(seed)
    d = {c: np.where(rng.random(n) < 0.08, np.nan, rng.gamma(2.0, 6.0, n)) for c in cfg["minutes_features"]}
    for t in ["min"] + list(cfg["count_targets"].values()):
        d[f"cur_{t}"] = np.where(rng.random(n) < 0.05, np.nan, rng.gamma(2.0, 40.0, n))
        for j in range(1, cfg["recency"]["prior_seasons_used"] + 1):
            d[f"h{j}_{t}"] = np.where(rng.random(n) < 0.3, np.nan, rng.gamma(2.0, 200.0, n))
    d["pos"] = rng.choice(list(P.POS_GROUPS), n)
    d["cold_start_class"] = rng.choice(list(P.COLD), n)
    d["is_home"] = np.where(rng.random(n) < 0.02, np.nan, rng.integers(0, 2, n).astype(float))
    d["_w"] = rng.random(n) + 0.1
    df = pd.DataFrame(d)
    X, names, tf = P._design(df, cfg)
    st = {"transform": tf, "coef": dict(zip(names, rng.normal(0, 3, len(names)).tolist())),
          "prior_rate": {t: {g: float(rng.random()) for g in P.POS_GROUPS} for t in cfg["count_targets"].values()},
          "prior_k_minutes": {t: float(rng.gamma(2, 50)) for t in cfg["count_targets"].values()}}
    return df, cfg, st


@pytest.mark.parametrize("n", [WF.PREDICT_CHUNK_ROWS * 3 + 4_097, WF.PREDICT_CHUNK_ROWS * 2 + 13, WF.PREDICT_CHUNK_ROWS * 2 + 1,
                               WF.PREDICT_CHUNK_ROWS * 4 - 7, WF.PREDICT_CHUNK_ROWS * 2, WF.PREDICT_CHUNK_ROWS * 21 + 16_373, 5_000])
def test_chunked_predict_means_is_byte_identical(n):
    df, cfg, st = _frame(n)
    ref = P._predict_means(df, cfg, st)
    got = WF.chunked_predict_means(df, cfg, st)
    assert list(got) == list(ref)
    for k in ref:
        assert got[k].dtype == ref[k].dtype and got[k].shape == ref[k].shape, k
        assert got[k].tobytes() == ref[k].tobytes(), k


def test_substitution_is_scoped_and_restored():
    frozen = P._predict_means
    with WF.bounded_predict_means():
        assert P._predict_means is WF.chunked_predict_means
    assert P._predict_means is frozen
    with pytest.raises(RuntimeError):
        with WF.bounded_predict_means():
            raise RuntimeError("fit failed")
    assert P._predict_means is frozen


def test_refuses_when_not_the_frozen_function(monkeypatch):
    monkeypatch.setattr(P, "_predict_means", lambda *a: None)
    with pytest.raises(WF.WalkForwardFitError):
        with WF.bounded_predict_means():
            pass
