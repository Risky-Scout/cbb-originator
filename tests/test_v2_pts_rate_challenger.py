"""Focused tests of the PTS rate challenger R1 algebra (research; no data access)."""
import numpy as np
import pandas as pd

from cbb_platform import props_v0 as P
from cbb_platform.v2 import pts_rate_challenger as R

CFG = {"recency": {"half_life_seasons": 1.0, "prior_seasons_used": 3}}
ST = {"prior_k_minutes": {"pts": 30.0}, "prior_rate": {"pts": {"G": 0.35, "F": 0.34, "C": 0.37, "U": 0.36}}}


def _frame(n=400, seed=0):
    g = np.random.default_rng(seed)
    cm = np.where(g.random(n) < 0.3, 0.0, g.uniform(1, 900, n))
    df = pd.DataFrame({"pos": g.choice(list("GFCU"), n), "cur_min": cm,
                       "cur_pts": np.where(cm > 0, np.round(cm * g.uniform(0.1, 0.7, n)), 0.0)})
    for j in (1, 2, 3):
        m = np.where(g.random(n) < 0.4, np.nan, g.uniform(0, 1100, n))
        df[f"h{j}_min"], df[f"h{j}_pts"] = m, np.round(m * g.uniform(0.1, 0.7, n))
    df.loc[0, ["cur_min", "cur_pts"]] = [0.0, 2.0]                     # source quirk: 0 minutes with points
    return df


def test_r1_formula_zero_evidence_and_r0_special_case():
    df = _frame()
    p = R.rate_parts(df, ST, CFG)
    tau = 74.23
    r1 = R.r_r1(p, tau)
    closed = (p["cur_pts"] + tau * p["r_prior"]) / (p["cur_min"] + tau)
    assert np.allclose(r1, closed, rtol=0, atol=1e-15)                   # the protocol formula on every row
    z = (p["cur_min"] <= 0) & (p["cur_pts"] == 0)
    assert np.array_equal(r1[z], p["r_R0"][z])                           # zero evidence: R1 == R0 bit-identically
    assert r1[0] > p["r_prior"][0]                                       # 0-minute-with-points row keeps cur_pts
    # R0 is R1 with the player-specific pseudo-minutes tau_i = Hp_min + k
    r0_as_r1 = (p["cur_pts"] + (p["hp_min"] + p["k"]) * p["r_prior"]) / (p["cur_min"] + p["hp_min"] + p["k"])
    assert np.allclose(r0_as_r1, p["r_R0"], rtol=1e-12)
    # the R0 rate is the frozen props_v0 rate verbatim
    pr = df["pos"].map(ST["prior_rate"]["pts"]).to_numpy(float)
    assert np.array_equal(p["r_R0"], (P._hist(df, CFG, "pts") + 30.0 * pr) / (P._hist(df, CFG, "min") + 30.0))
    # convex combination of current rate and prior where current minutes exist; tau limits
    pos = p["cur_min"] > 0
    rc = p["cur_pts"][pos] / p["cur_min"][pos]
    lo, hi = np.minimum(rc, p["r_prior"][pos]), np.maximum(rc, p["r_prior"][pos])
    assert ((r1[pos] >= lo - 1e-12) & (r1[pos] <= hi + 1e-12)).all()
    assert np.allclose(R.r_r1(p, 1e12)[pos], p["r_prior"][pos], rtol=1e-6)
    num = R.numerics(p, tau, np.maximum(df["cur_min"].to_numpy() * 0 + 20 * p["r_R0"], 0.01), np.full(len(df), 20.0), 0.01)
    assert num["nonfinite"] == num["nonpositive"] == num["outside_convex_hull"] == 0
    assert num["zero_evidence_mu_not_bit_identical"] == 0 and num["cur_min_zero_with_points_rows"] == 1


def test_grid_and_frozen_protocol():
    assert R.TAU_GRID[0] == 1.0 and np.isclose(R.TAU_GRID[-1], 2000.0) and len(R.TAU_GRID) == 241
    assert np.all(np.diff(np.log(R.TAU_GRID)) > 0)
    assert R.protocol_frozen()["protocol_commit"] == R.PROTOCOL_COMMIT
