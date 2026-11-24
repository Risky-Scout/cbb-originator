"""prod.pts_compound (Challenger P2): PTS shape only. Invariants: every PTS conditional-table row keeps its mean exactly,
the other six stats' tables are bit-identical to the frozen function, thresholds are the frozen rule applied to the
compound CDF, and P2 off is the identity."""
from __future__ import annotations

import numpy as np
import pytest

from cbb_platform.prod import engine as ENG, pts_compound as PTC, simulate as SM
from cbb_platform.v2 import latent as L, structural as S


def _lm(n_players=3, sims=400, seed=3):
    rng = np.random.default_rng(seed)
    base = np.log(np.array([[12.0, 5.0, 3.0, 1.5, 1.0, 0.6, 1.8], [3.0, 1.5, 0.8, 0.3, 0.4, 0.2, 0.5],
                            [0.8, 0.5, 0.2, 0.1, 0.1, 0.1, 0.2]]))[:n_players]
    return (base[None] + 0.4 * rng.standard_normal((sims, n_players, S.NS))).reshape(sims, n_players * S.NS)


def _phi():
    return np.array([0.114, 0.022, 0.047, 0.053, 0.036, 0.129, 0.0])


def test_off_is_identity(monkeypatch):
    monkeypatch.setattr(PTC, "PHI_N", None)
    lm = _lm()
    a = S.mixture_tables(lm, _phi())
    b = SM.bounded_mixture_tables(lm, _phi())
    for k in ("pmf", "cdf", "thr", "inv", "starts"):
        assert np.array_equal(a[k], b[k])


@pytest.mark.parametrize("phi_n", [0.0, 0.06])
def test_pts_mean_preserved_and_other_stats_untouched(monkeypatch, phi_n):
    lm = _lm()
    frozen = S.mixture_tables(lm, _phi())
    monkeypatch.setattr(PTC, "PHI_N", phi_n)
    tab = SM.bounded_mixture_tables(lm, _phi())
    k = np.arange(L.KMAX + 1)
    q = np.round(lm / S.GRID_H).astype(np.int64)
    for c in range(lm.shape[1]):
        a, b = tab["starts"][c], tab["starts"][c + 1]
        if c % S.NS == PTC.PTS_DIM:
            mu = np.exp(np.unique(q[:, c]) * S.GRID_H)
            m = (tab["pmf"][a:b] * k).sum(1)
            assert np.allclose(m[mu <= 30], mu[mu <= 30], rtol=1e-8, atol=1e-9)   # mean exact (N truncated at NMAX)
            assert np.allclose(m, mu, rtol=1e-4)                     # beyond 30: only the shared kmax tail fold
            assert np.allclose((frozen["pmf"][a:b] * k).sum(1), mu, rtol=1e-4)    # same rows (frozen NB folds its tail at kmax)
            lo = mu <= 15.0
            assert (tab["pmf"][a:b, 0][lo] > frozen["pmf"][a:b, 0][lo]).all()                # more zero mass where mu <= 15
        else:
            for key in ("pmf", "cdf", "thr"):
                assert np.array_equal(tab[key][a:b], frozen[key][a:b])
    assert np.array_equal(tab["inv"], frozen["inv"]) and np.array_equal(tab["starts"], frozen["starts"])
    assert np.abs(tab["pmf"].sum(1) - 1).max() < 1e-12
    cdf = np.minimum(np.maximum.accumulate(np.cumsum(tab["pmf"], axis=1), axis=1), 1.0)
    cdf[:, -1] = 1.0
    assert np.allclose(tab["cdf"], cdf)


def test_lattice_shape():
    p = PTC.compound_matrix(np.array([4.0]), 0.0, 60)[0]
    assert p[1] < p[2] and p[1] < p[0]                                 # 1 point is rarer than 0 or 2 (events are 1/2/3)


def test_engine_id_names_p2_only_when_on(monkeypatch):
    assert PTC.PHI_N == 0.0                                           # promoted 2026-11-24
    assert "PTSC1[0]" in ENG.engine_id()
    monkeypatch.setattr(PTC, "PHI_N", None)
    off = ENG.engine_id()
    assert "PTSC1" not in off
    monkeypatch.setattr(PTC, "PHI_N", 0.06)
    assert "PTSC1[0.06]" in ENG.engine_id() and ENG.engine_id() != off
