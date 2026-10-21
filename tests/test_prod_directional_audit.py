"""Focused tests of the directional-bias audit conventions (synthetic Poisson forecasts with KNOWN direction; no store,
no model, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cbb_platform.prod import directional_audit as DA


def _pcdf(k, mu, K: int = 120):
    """Poisson CDF F(k; mu) for integer arrays k (k < 0 -> 0), numpy only."""
    j = np.arange(K)
    logp = j[None, :] * np.log(mu)[:, None] - mu[:, None] - np.cumsum(np.log(np.maximum(j, 1)))[None, :]
    cdf = np.cumsum(np.exp(logp), axis=1)
    kk = np.clip(np.asarray(k, int), -1, K - 1)
    return np.where(kk < 0, 0.0, cdf[np.arange(len(mu)), np.maximum(kk, 0)])


def _frame(scale: float, n_games: int = 1500, per: int = 10, seed: int = 7) -> pd.DataFrame:
    """Forecast Poisson(mu) for every stat; outcomes ~ Poisson(scale * mu). scale > 1: forecasts too LOW."""
    rng = np.random.default_rng(seed)
    n = n_games * per
    df = pd.DataFrame({"game_id": np.repeat([f"G{i}" for i in range(n_games)], per)})
    for s in DA.STATS:
        mu = rng.uniform(0.5, 12.0, n)
        y = rng.poisson(scale * mu)
        df[f"{s}|mean"] = mu
        df[f"y_{s}"] = y
        df[f"{s}|pit_lo"] = _pcdf(y - 1, mu)
        df[f"{s}|pit_hi"] = _pcdf(y, mu)
        for li, L in enumerate(DA.HALF_LINES[s]):
            df[f"{s}|pover{li}"] = 1 - _pcdf(np.full(n, int(np.floor(L))), mu)
    return df


def _season(scale, seed):
    df = _frame(scale, seed=seed)
    return df, DA.Boot(df["game_id"].to_numpy(), n_boot=300, seed=1)


def test_calibrated_forecasts_are_green_with_exact_pit_targets():
    by = {}
    for k, seed in (("a", 1), ("b", 2)):
        df, bt = _season(1.0, seed)
        r = DA.stat_metrics(df, "PTS", bt)
        assert abs(r["mean_mid_pit"]["value"] - 0.5) < 0.006               # E[mid-PIT] = 0.5 under calibration
        assert abs(r["P_pit_gt_half"]["value"] - 0.5) < 0.008
        assert abs(r["tails"]["q75"]["upper_tail_miss"] - 0.25) < 0.01 and abs(r["tails"]["q75"]["lower_tail_miss"] - 0.25) < 0.01
        assert r["signed_mean_error"]["lo"] < 0 < r["signed_mean_error"]["hi"]
        by[k] = r
    assert DA.classify(by, "stat")["level"] == "GREEN"


def test_sign_convention_under_prediction_is_negative_and_red_when_persistent():
    by = {}
    for k, seed in (("a", 3), ("b", 4)):
        df, bt = _season(1.25, seed)                                         # outcomes 25% above forecasts
        r = DA.stat_metrics(df, "PTS", bt)
        assert r["signed_mean_error"]["hi"] < 0                              # forecast - actual < 0: UNDER-PREDICTION
        assert r["mean_mid_pit"]["lo"] > 0.5                                 # outcomes above the forecast distribution
        assert r["tails"]["q75"]["asymmetry"]["lo"] > 0                      # too much mass below outcomes
        assert all(v["p_over_minus_observed"]["hi"] < 0 for v in r["fixed_line_over_calibration"].values())
        by[k] = r
    c = DA.classify(by, "stat")
    assert c["level"] == "RED" and any("UNDER-PREDICTION" in e for e in c["evidence"])


def test_over_prediction_direction_and_single_season_is_only_watch():
    df, bt = _season(0.8, 5)
    over = DA.stat_metrics(df, "PTS", bt)
    assert over["signed_mean_error"]["lo"] > 0 and over["mean_mid_pit"]["hi"] < 0.5
    df2, bt2 = _season(1.0, 6)
    ok = DA.stat_metrics(df2, "PTS", bt2)
    c = DA.classify({"a": over, "b": ok}, "stat")                            # one season only: not persistent
    assert c["level"] == "WATCH"


def test_combo_mean_is_the_sum_of_stat_means():
    df, bt = _season(1.0, 8)
    r = DA.combo_metrics(df, "PRA", bt)
    assert np.isclose(r["mean_projection"], sum(df[f"{s}|mean"].mean() for s in ("PTS", "REB", "AST")))
    assert r["quantile_calibration"].startswith("NOT_AVAILABLE")
    assert DA.subgroup_level({"a": {**r, "N": 10}}, "combo") == "NOT_CLASSIFIED_SMALL_N"
