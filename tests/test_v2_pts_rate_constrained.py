import numpy as np
import pytest

from cbb_platform.v2 import pts_rate_challenger as R1, pts_rate_constrained as K, structural as S


def test_accounting_identity_synthetic():
    rng = np.random.default_rng(0)
    mu, r0, em, ym, y = (rng.uniform(0.1, 30, 50) for _ in range(5))
    A, B, C = mu - r0 * em, r0 * (em - ym), r0 * ym - y
    assert np.max(np.abs(A + B + C - (mu - y))) < 1e-12


def test_clipped_normal_mean_and_k_factor_vs_monte_carlo():
    m, s = np.array([4.0, 15.0, 33.0]), np.array([4.5, 6.0, 5.0])
    eps = np.random.default_rng(1).standard_normal((400_000, 1))
    raw = np.clip(m + s * eps, 0, K.MIN_CAP)
    assert np.allclose(K.clipped_normal_mean(m, s), raw.mean(0), atol=0.03)
    p, ed = [0.9, 0.1], 40.5
    D = np.where(np.random.default_rng(2).random((400_000, 1)) < 0.1, 45.0, 40.0)
    mc = ((np.maximum(raw * D / ed, S.MIN_FLOOR) / m) ** 1.3).mean(0)
    assert np.allclose(K.k_factor(m, s, 1.3, p, ed), mc, rtol=5e-3)


def test_jm1_mixture_averages_probabilities_before_log():
    lm = np.log(np.array([[2.0], [12.0]]))                       # two draws, one dim: very different means
    tab = S.mixture_tables(lm, np.full(S.NS, 0.1))
    pmf = S.mixture_pmf(tab, 2)
    mix = 0.5 * (tab["pmf"][tab["inv"][0, 0]] + tab["pmf"][tab["inv"][1, 0]])
    assert np.allclose(pmf[0], mix)
    y = 7
    assert -np.log(pmf[0, y]) < np.mean([-np.log(tab["pmf"][tab["inv"][b, 0], y]) for b in range(2)])


def test_candidate_surface_loading_dedupes_and_adds_nothing():
    rec = {"coarse_surface": [{"tau": 1.0, "rho": 0.0}, {"tau": 2.0, "rho": 0.5}],
           "refine_surface": [{"tau": 2.0 * (1 + 1e-12), "rho": 0.5}, {"tau": 3.0, "rho": 1.0}]}
    c, total = K.candidates(rec)
    assert total == 4 and len(c) == 3
    assert {(x["tau"], x["rho"]) for x in c} <= {(1.0, 0.0), (2.0, 0.5), (3.0, 1.0)}


def _summary(b30, b010=0.7, agg=0.164, low=0.571, f30=0.081, tg=-0.125, dlog=0.0, dcrps=0.0):
    keys = ("ALL", "EARLY_TEAM_GAMES_LE2")
    return {"bias": {"MIN_30PLUS": b30, "MIN_0_10": b010, "ALL": agg, "LOW_LT20": low, "EARLY_FIRST_30_DAYS": f30,
                     "EARLY_TEAM_GAMES_LE2": tg},
            "dlog": dict.fromkeys(keys, dlog), "dcrps_scaled": dict.fromkeys(keys, dcrps)}


def test_constraint_logic():
    r0 = _summary(-0.369)
    ok = K.constraint_check(_summary(-0.2, b010=0.6), r0)
    assert all(ok.values())
    assert not K.constraint_check(_summary(0.16, b010=0.6), r0)["B"]          # overshoot cap
    assert not K.constraint_check(_summary(-0.3, b010=0.6), r0)["A"]          # < 25% reduction
    assert not K.constraint_check(_summary(-0.2, b010=0.6, agg=0.22), r0)["D"]
    assert not K.constraint_check(_summary(-0.2, b010=0.6, f30=0.14), r0)["F"]
    assert not K.constraint_check(_summary(-0.2, b010=0.6, dlog=0.003), r0)["H"]


def test_shortlist_feasible_only_ranked_max_three():
    c = [{"tau": t, "rho": r, "log": lg, "crps": 1.0, "feasible": f}
         for t, r, lg, f in [(10, .5, 2.0, True), (20, .5, 1.0, False), (30, .5, 1.5, True), (40, .6, 1.7, True), (50, .7, 1.6, True)]]
    s = K.shortlist(c)
    assert [x["tau"] for x in s] == [30, 50, 40]
    assert K.shortlist([dict(x, feasible=False) for x in c]) == []


def test_no_2025_without_committed_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "LOCK_PATH", tmp_path / "missing_lock.json")
    with pytest.raises(R1.ChallengerViolation):
        K.require_lock()
