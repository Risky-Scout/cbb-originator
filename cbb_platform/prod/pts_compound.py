"""Player Challenger P2: PTS compound scoring family (docs/audits/2026-11-22-causal/PHASE4_PROTOCOL.json,
challenger_P2_pts_compound_family).

Defect: the frozen scorer draws PTS from a unit-step negative binomial. Points arrive in 1 / 2 / 3-point events, so
the unit-step NB gives about half the realized zero-point mass (live 2026-27, min_mu 10-15: PMF P(0) 0.146 vs realized
0.336) and ~5x the realized P(1), even when matched to the realized mean and variance within minutes bins.

Fix (PTS conditional table only): given the scorer's conditional mean mu (unchanged),

    PTS = sum_{k <= N} V_k,  V in {1, 2, 3} w.p. PV (frozen: 2023-24 + 2024-25 box scores, ML at bin-matched moments),
    N ~ NB(mean mu / VB, var m + PHI_N m^2)   (props_v0.pmf_matrix convention; PHI_N chosen on 2024-25 only)

E[PTS | draw] = mu exactly, so every PTS mean, the minutes draws, the environment, the residual dependence (latent
thresholds are recomputed from the compound CDF exactly as structural.mixture_tables does) and the other six stats'
tables are unchanged. Applied in prod.simulate.bounded_mixture_tables, i.e. the production scoring path only.
"""
from __future__ import annotations

import numpy as np

from cbb_platform import props_v0 as PZ
from cbb_platform.v2 import latent as L, structural as S

ID = "CBB_PLAYER_CHALLENGER_P2_PTS_COMPOUND_V1"
PV = (0.11, 0.65, 0.24)                  # P(V = 1, 2, 3)
PHI_N = 0.0                              # promoted 2026-11-24: selected on 2024-25 (declared grid {0,.03,.06,.12}); None = off
NMAX = 100
PTS_DIM = list(L.STATS).index("PTS")
_VCONV: dict = {}


def enabled() -> bool:
    return PHI_N is not None


def _vconv(kmax: int) -> np.ndarray:
    if kmax not in _VCONV:
        pv = np.asarray(PV, dtype=float)
        out = np.zeros((NMAX + 1, kmax + 1))
        cur = np.zeros(kmax + 1)
        cur[0] = 1.0
        step = np.zeros(kmax + 1)
        step[1:4] = pv
        for n in range(NMAX + 1):
            out[n] = cur
            out[n, -1] += max(1.0 - cur.sum(), 0.0)          # mass beyond kmax folded into the last support point
            cur = np.convolve(cur, step)[: kmax + 1]
        _VCONV[kmax] = out
    return _VCONV[kmax]


def compound_matrix(mu: np.ndarray, phi_n: float, kmax: int) -> np.ndarray:
    """Rows: P(PTS = 0..kmax-1), P(PTS >= kmax) for each conditional mean mu."""
    vb = float(np.dot(PV, (1.0, 2.0, 3.0)))
    out = PZ.pmf_matrix(np.asarray(mu, dtype=float) / vb, float(phi_n), NMAX) @ _vconv(kmax)
    out[:, -1] += np.maximum(1.0 - out.sum(axis=1), 0.0)
    return out


def apply(tab: dict, lm: np.ndarray, kmax: int) -> dict:
    """Replace the PTS rows of one structural.mixture_tables result (lm: sims x d, player-major, dim % NS = stat) by the
    compound family; pmf / cdf / thr of every other row untouched. Row order = the frozen function's (dim, used grid)."""
    if not enabled():
        return tab
    d = lm.shape[1]
    q = np.round(lm / S.GRID_H).astype(np.int64)
    for c in range(PTS_DIM, d, S.NS):
        a, b = tab["starts"][c], tab["starts"][c + 1]
        grid = np.unique(q[:, c])
        if len(grid) != b - a:
            raise ValueError(f"PTS dim {c}: {len(grid)} grid points vs {b - a} table rows")
        pmf = compound_matrix(np.exp(grid * S.GRID_H), PHI_N, kmax)
        if "pmf" in tab:
            tab["pmf"][a:b] = pmf
        cdf = np.minimum(np.maximum.accumulate(np.cumsum(pmf, axis=1), axis=1), 1.0)
        cdf[:, -1] = 1.0
        if "cdf" in tab:
            tab["cdf"][a:b] = cdf
        if "thr" in tab:
            thr = np.full(cdf.shape, S.THR_CAP)
            need = cdf < 1.0 - 1e-15
            with np.errstate(divide="ignore"):
                thr[need] = np.clip(L.ndtri(np.clip(cdf[need], 1e-300, 1.0)), -S.THR_CAP, S.THR_CAP)
            tab["thr"][a:b] = thr
    return tab
