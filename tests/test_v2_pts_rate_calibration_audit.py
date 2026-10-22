"""Focused tests for the PTS rate calibration-geometry audit (quintiles, NB2 regression, decision logic, season
separation, no target leakage). Synthetic data only; no store is read."""
import copy
import inspect

import numpy as np
import pandas as pd
import pytest

from cbb_platform.v2 import pts_rate_calibration_audit as A
from cbb_platform.v2 import pts_rate_challenger as R1


def _synth(n=40000, a=-0.05, b=1.2, phi=0.15, seed=0):
    rng = np.random.default_rng(seed)
    r = np.exp(rng.normal(np.log(0.35), 0.35, n))
    m = rng.uniform(2, 38, n)
    mu = m * np.exp(a) * r ** b
    lam = rng.gamma(1 / phi, phi * mu)
    y = rng.poisson(lam).astype(float)
    return pd.DataFrame({"forecast_season": 2023, "game_id": rng.integers(0, 2000, n), "r_R0": r, "y_min": m, "y_PTS": y,
                         "min_mu": m + rng.normal(0, 3, n)})


def test_nb2_recovers_parameters_and_ci_covers():
    d = _synth()
    c = A.calib(d)
    assert c["converged"]
    assert abs(c["b"]["est"] - 1.2) < 0.03 and c["b"]["ci95"][0] < 1.2 < c["b"]["ci95"][1]
    assert abs(c["a"]["est"] + 0.05) < 0.05
    assert abs(c["phi"] - 0.15) < 0.02


def test_nb2_null_slope_one_and_poisson_limit():
    d = _synth(b=1.0, a=0.0, phi=1e-3, seed=1)
    c = A.calib(d)
    assert c["b"]["ci95"][0] < 1.0 < c["b"]["ci95"][1]
    assert c["phi"] < 0.02


def test_min_mu_extension_tiny_when_no_signal():
    d = _synth(seed=2)
    c = A.calib(d, (float(d["min_mu"].mean()), float(d["min_mu"].std(ddof=0))))
    assert abs(c["c"]["est"]) < 0.02


def test_quintiles_from_reference_only_and_fixed():
    ref = pd.DataFrame({"r_R0": np.linspace(0.1, 0.6, 1001)})
    b = A.boundaries(ref)
    assert np.allclose(b, np.quantile(ref["r_R0"], A.QS))
    q = A.quintile(np.array([0.0, b[0] - 1e-12, b[0], b[3], 5.0]), b)
    assert q.tolist() == [1, 1, 2, 5, 5]
    # the later season is binned with the reference boundaries, not its own
    later = np.linspace(0.3, 0.9, 1000)
    assert (A.quintile(later, b) == 5).mean() > 0.5


def test_season_roles_and_sources():
    assert A.REFERENCE == (2023, 2024)
    assert A.SAMPLES["2024-25"] == (2025,) and A.SAMPLES["2025-26"] == (2026,)
    assert set(A.SAMPLES["REFERENCE"]).isdisjoint({2025, 2026})
    src = inspect.getsource(A.run)
    assert "boundaries(ref)" in src and "boundaries(samp" not in src


def test_no_target_leakage_in_predicted_rate():
    """r_R0 is a function of PIT history/state only: permuting the target outcomes leaves it bit-identical."""
    rng = np.random.default_rng(3)
    n = 500
    h = pd.DataFrame({"pos": rng.choice(["G", "F", "C"], n), "cur_min": rng.uniform(0, 400, n),
                      "cur_pts": rng.uniform(0, 150, n), **{f"h{j}_{c}": rng.uniform(0, 900, n)
                                                              for j in (1, 2, 3) for c in ("min", "pts")}})
    st = {"prior_k_minutes": {"pts": 120.0}, "prior_rate": {"pts": {"G": 0.4, "F": 0.38, "C": 0.42}}}
    cfg = {"recency": {"half_life_seasons": 1.0, "prior_seasons_used": 3}, "mean_floor": 0.01}
    r0 = R1.rate_parts(h.assign(y_PTS=rng.integers(0, 30, n), y_min=rng.uniform(1, 40, n)), st, cfg)["r_R0"]
    r1 = R1.rate_parts(h.assign(y_PTS=rng.integers(0, 30, n), y_min=rng.uniform(1, 40, n)), st, cfg)["r_R0"]
    assert np.array_equal(r0, r1)
    assert np.array_equal(r0, R1.rate_parts(h, st, cfg)["r_R0"])          # identical with no outcome columns at all


def _res(b_ref=1.10, ci=(1.06, 1.14), b25=1.08, b26=1.09, pattern=True, c=0.005, c_ci=(0.001, 0.009)):
    rel = {s: {"compression_pattern": pattern} for s in ("2022-23", "2023-24", "2024-25", "2025-26", "REFERENCE")}
    cal = {"REFERENCE": {"b": {"est": b_ref, "ci95": list(ci)}, "converged": True},
           "2024-25": {"b": {"est": b25, "ci95": [b25 - .03, b25 + .03]}, "converged": True},
           "2025-26": {"b": {"est": b26, "ci95": [b26 - .03, b26 + .03]}, "converged": True}}
    pos = {f"POSITION:{p}": {"N": 5000, "b": 1.1, "b_ci95": [1.0, 1.2]} for p in A.POSITIONS}
    early = {k: {"N": 5000, "b": 1.1, "b_ci95": [0.98, 1.2], "signed_err": -0.01, "rel_err": -0.02}
             for k in ("EARLY:TEAM_GAMES_LE2", "EARLY:FIRST_30_DAYS", "EARLY:REMAINDER")}
    st = {s: {**copy.deepcopy(pos), **copy.deepcopy(early)} for s in A.SAMPLES}
    mmu = {s: {"c": {"est": c, "ci95": list(c_ci)}, "converged": True} for s in A.SAMPLES}
    return {"reliability": rel, "calibration": cal, "strata": st, "min_mu_after_rate": mmu}


def test_decision_justified_when_all_hold():
    d = A.decide(_res())
    assert d["DECISION"] == "CALIBRATION_DECOMPRESSION_JUSTIFIED" and d["RATE_COMPRESSION_CONFIRMED"]


@pytest.mark.parametrize("kw", [dict(b_ref=1.04), dict(ci=(0.99, 1.14)), dict(b25=0.99), dict(b26=1.0), dict(pattern=False)])
def test_decision_not_justified(kw):
    d = A.decide(_res(**kw))
    assert d["DECISION"] == "CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED" and not d["RATE_COMPRESSION_CONFIRMED"]


def test_decision_inconclusive_on_material_min_mu():
    d = A.decide(_res(c=0.03, c_ci=(0.02, 0.04)))
    assert d["DECISION"] == "INCONCLUSIVE" and d["RATE_COMPRESSION_CONFIRMED"]


def test_decision_slope_drift_excused_only_by_curve():
    assert A.decide(_res(b_ref=1.30, ci=(1.25, 1.35), b25=1.10))["criteria"]["4_CONSISTENT"]   # curve pattern excuses
    r = _res(b_ref=1.30, ci=(1.25, 1.35), b25=1.10)
    r["reliability"]["2024-25"]["compression_pattern"] = False
    assert not A.decide(r)["criteria"]["4_CONSISTENT"]


def test_decision_position_and_early_reversal():
    r = _res()
    r["strata"]["2025-26"]["POSITION:C"]["b_ci95"] = [0.8, 0.95]
    d = A.decide(r)
    assert d["position_reversals"] == ["2025-26:C"] and d["DECISION"] == "CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED"
    r = _res()
    r["strata"]["REFERENCE"]["EARLY:FIRST_30_DAYS"].update(signed_err=0.01, rel_err=0.04)
    assert not A.decide(r)["criteria"]["8_EARLY"]


def test_reliability_pattern():
    rng = np.random.default_rng(4)
    n = 50000
    r = np.exp(rng.normal(np.log(0.35), 0.35, n))
    m = rng.uniform(5, 35, n)
    y = rng.poisson(m * 0.35 * (r / 0.35) ** 1.3).astype(float)          # compressed predictions
    d = pd.DataFrame({"r_R0": r, "y_min": m, "y_PTS": y, "r_act": y / m, "min_mu": m})
    rel = A.reliability(d, A.boundaries(d))
    assert rel["compression_pattern"] and rel["rel_err"][0] > 0 > rel["rel_err"][4]
