"""Root-cause audit math (synthetic): the minutes / rate / structural decomposition is exact and the Gauss-Hermite
minutes functional matches the frozen clipped-normal draw law."""
import numpy as np
import pandas as pd

from cbb_platform.prod import root_cause_audit as R


def test_decomposition_sums_exactly():
    rng = np.random.default_rng(7)
    n = 4000
    m_hat = rng.uniform(2, 38, n)
    y_min = np.where(rng.random(n) < 0.05, 0.0, np.clip(m_hat + rng.normal(0, 6, n), 0, 45).round())
    mu1b = m_hat * rng.uniform(0.2, 0.5, n)
    y = rng.poisson(np.maximum(y_min, 0) * 0.35).astype(float)
    df = pd.DataFrame({"min_mu": m_hat, "min_sigma": np.full(n, 6.0), "y_min": y_min, "PTS_mu": mu1b, "y_PTS": y,
                       "PTS|mean": mu1b * rng.uniform(0.9, 1.3, n)})
    dc = R.decompose(df, "PTS", {"intercept": -0.01, "log_minutes_ratio": 1.3})
    np.testing.assert_allclose(dc["minutes"] + dc["rate"] + dc["structural"], dc["total"], atol=1e-12)
    np.testing.assert_allclose(dc["intercept"] + dc["exposure_jensen"] + dc["struct_remainder"], dc["structural"], atol=1e-12)
    z = dc["pos"].to_numpy()
    assert (dc.loc[~z, "rate"] == 0).all()                         # zero-minute rows: rate unobserved -> all minutes
    # Shapley rate term is zero when the rate is exact; minutes term is zero when minutes are exact
    df2 = df.assign(y_min=m_hat, y_PTS=mu1b)
    dc2 = R.decompose(df2, "PTS", {"intercept": 0.0, "log_minutes_ratio": 1.0})
    np.testing.assert_allclose(dc2[["minutes", "rate"]].to_numpy(), 0.0, atol=1e-12)


def test_minutes_functional_matches_monte_carlo():
    mu, sig, beta = np.array([6.0, 15.0, 33.0]), np.array([5.1, 6.9, 5.4]), 1.3
    eps = np.random.default_rng(0).standard_normal(2_000_000)
    mc = [np.mean((np.maximum(np.clip(m + s * eps, 0, 60), 1) / m) ** beta) for m, s in zip(mu, sig)]
    np.testing.assert_allclose(R.jensen(mu, sig, beta), mc, rtol=2e-3)
    np.testing.assert_allclose(R.jensen(np.array([20.0]), np.array([1e-9]), 1.3), [1.0], atol=1e-9)
