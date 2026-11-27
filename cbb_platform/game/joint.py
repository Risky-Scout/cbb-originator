"""GameJointPMF = P(Home final = h, Away final = a): ONE coherent discrete joint score distribution per game.

Construction (design v0, CBB_GAME_MODEL_DESIGN_v0 sections JOINT / ENDGAME / OVERTIME):

1. Pre-endgame latent scores at T* (regulation clock T* before the end of the 2nd half):
   a discretized bivariate normal in (Margin = H - A, Total = H + A) coordinates with INDEPENDENT control of the margin
   SD, the total SD and the margin-total correlation. Cells are integrated exactly (conditional decomposition + Gauss-
   Legendre over each home-score cell), never a density-at-midpoint approximation.
2. Endgame: each pre-endgame cell (h0, a0) moves by the frozen historical late-game increment kernel K_d(dh, da)
   conditional on the lead d = h0 - a0 (close-score foul behaviour, leader / trailer asymmetry come from PBP).
3. Overtime: every regulation tie (s, s) is replaced by s + the OT increment law, repeated while the period ties;
   after MAX_OT periods the residual tie mass (< 1e-6) is split by the OT home-win probability. P(H = A) == 0.
4. Moment matching: the latent means / SDs / correlation are solved so the PMF reproduces the target expected
   home / away scores, margin SD, total SD and margin-total correlation (the calibrated game-level quantities).
   MEAN BASIS (Stage 06 correction, 2026-11-27): the target expected scores are REGULATION (40-minute) expectations:
   game.ratings fits pace on per-40 possessions and efficiency on points per possession, so poss x eff / 100 is the
   expected score of 40 minutes. The means are therefore matched on the regulation PMF (after the endgame, before
   overtime), and overtime adds its own expected points: E[final] = E[regulation] + P(regulation tie) x E[OT points].
   Before the correction the OT-inclusive FINAL PMF was matched to the 40-minute target, which removed the OT
   expectation (~1.2 points of game total; measured 2023-24 / 2024-25 / 2026-27: final - target exceeds
   per40 - target by P(OT) x OT points). The SD / correlation targets stay matched on the FINAL PMF (the variance
   model is calibrated on final-score residuals). CBB_GAME_MEAN_BASIS=FINAL restores the previous construction.

No player PMF is summed and no minutes / pace are resampled: the player layer only shifts the target means upstream
(game.lineup_adjust); this module owns all game-level variance. Pure numpy; deterministic; float64 internally.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field

import numpy as np

MEAN_BASIS_ENV = "CBB_GAME_MEAN_BASIS"        # REGULATION (default, Stage 06) | FINAL (pre-Stage-06 in-place fallback)
JOINT_VERSIONS = {"REGULATION": "cbb-game-joint-1.1.0", "FINAL": "cbb-game-joint-1.0.0"}


def mean_basis() -> str:
    v = (os.environ.get(MEAN_BASIS_ENV) or "REGULATION").upper()
    return v if v in JOINT_VERSIONS else "REGULATION"


JOINT_VERSION = JOINT_VERSIONS[mean_basis()]       # read at import; joint_version() is the call-time value


def joint_version() -> str:
    return JOINT_VERSIONS[mean_basis()]
GL_NODES = 5                              # Gauss-Legendre nodes per home-score cell (exact to ~1e-10 for sd >= 4)
SUPPORT_SD = 6.0                          # latent support: mean +- 6 SD per axis; tails folded into the edge cells
MAX_OT = 8                                # repeated overtime periods before the residual-tie split
TIE_TOL = 1e-12
SUM_TOL = 1e-9
MATCH_ITERS = 10
MATCH_TOL = 2e-4                          # points / SD units: final moments within this of the targets

_erf = np.frompyfunc(math.erf, 1, 1)


def ncdf(x) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)).astype(float))


# ============================================================================ frozen kernels (parameter artifacts)
@dataclass(frozen=True)
class EndgameKernel:
    """K_d(dh, da): P(home adds dh, away adds da over the last T* of regulation | home lead d at T*).
    `table` has shape (2*D + 1, R + 1, R + 1) for leads -D..D (|d| > D uses the edge row)."""
    t_star_sec: int
    max_lead: int
    table: np.ndarray
    source: str = ""

    @property
    def reach(self) -> int:
        return self.table.shape[1] - 1

    def mean_increments(self) -> np.ndarray:
        r = np.arange(self.reach + 1)
        return np.stack([(self.table.sum(axis=2) * r).sum(axis=1), (self.table.sum(axis=1) * r).sum(axis=1)], axis=1)

    def sha256(self) -> str:
        return hashlib.sha256(np.ascontiguousarray(self.table, dtype=np.float64).tobytes()
                              + f"{self.t_star_sec}|{self.max_lead}".encode()).hexdigest()

    @staticmethod
    def identity() -> "EndgameKernel":
        """No endgame step (T* = end of regulation): the regulation PMF is the latent PMF."""
        return EndgameKernel(0, 0, np.ones((1, 1, 1)), "IDENTITY")


@dataclass(frozen=True)
class OvertimeParams:
    """One 5-minute OT period: per-team points ~ discretized bivariate normal with means kappa * (5/40) * mu_team,
    SD sd_team each and correlation rho (fit on OT period scores)."""
    kappa: float = 1.0
    sd_team: float = 3.9
    rho: float = 0.25
    reach: int = 30
    source: str = ""

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps([self.kappa, self.sd_team, self.rho, self.reach]).encode()).hexdigest()


# ============================================================================ discretized bivariate normal
def _cov_ha(sd_m: float, sd_t: float, rho_mt: float) -> tuple[float, float, float]:
    """(Var H, Var A, Cov(H, A)) from Var(H-A) = sd_m^2, Var(H+A) = sd_t^2, Corr(H-A, H+A) = rho_mt."""
    c = rho_mt * sd_m * sd_t
    vh = (sd_t ** 2 + sd_m ** 2 + 2 * c) / 4.0
    va = (sd_t ** 2 + sd_m ** 2 - 2 * c) / 4.0
    cha = (sd_t ** 2 - sd_m ** 2) / 4.0
    return vh, va, cha


def bvn_cells(m_h: float, m_a: float, sd_m: float, sd_t: float, rho_mt: float,
              h_lo: int, h_hi: int, a_lo: int, a_hi: int) -> np.ndarray:
    """P(H in [h-.5, h+.5), A in [a-.5, a+.5)) for integer h in [h_lo, h_hi], a in [a_lo, a_hi]; mass outside the box
    is folded into the boundary cells (none is lost)."""
    vh, va, cha = _cov_ha(sd_m, sd_t, rho_mt)
    if vh <= 0 or va <= 0 or cha ** 2 >= vh * va:
        raise ValueError(f"degenerate covariance: sd_m={sd_m} sd_t={sd_t} rho={rho_mt}")
    sh = math.sqrt(vh)
    slope = cha / vh
    s_cond = math.sqrt(va - cha ** 2 / vh)
    xg, wg = np.polynomial.legendre.leggauss(GL_NODES)
    hs = np.arange(h_lo, h_hi + 1, dtype=float)
    # home-axis cell edges; the first / last cells extend to -inf / +inf (tail folding)
    lo_e = hs - 0.5
    hi_e = hs + 0.5
    lo_e[0] = m_h - 12 * sh
    hi_e[-1] = m_h + 12 * sh
    half = (hi_e - lo_e) / 2.0
    mid = (hi_e + lo_e) / 2.0
    x = mid[:, None] + half[:, None] * xg[None, :]                              # (nh, G) quadrature abscissae
    dens = np.exp(-0.5 * ((x - m_h) / sh) ** 2) / (sh * math.sqrt(2 * math.pi))
    w = dens * wg[None, :] * half[:, None]                                       # (nh, G)
    edges = np.arange(a_lo, a_hi + 2, dtype=float) - 0.5                         # (na + 1,)
    m_cond = m_a + slope * (x - m_h)                                             # (nh, G)
    cdf = ncdf((edges[None, None, :] - m_cond[:, :, None]) / s_cond)             # (nh, G, na + 1)
    cdf[:, :, 0] = 0.0
    cdf[:, :, -1] = 1.0
    cell = np.diff(cdf, axis=2)                                                  # (nh, G, na)
    p = np.einsum("hg,hga->ha", w, cell)
    p = np.clip(p, 0.0, None)
    tot = p.sum()
    # exact home-axis tail mass outside [lo_e[0], hi_e[-1]] is ~1e-33: renormalize the rounding only
    return p / tot


# ============================================================================ the joint PMF object
@dataclass
class GameJointPMF:
    h_min: int
    a_min: int
    p: np.ndarray                                    # (n_h, n_a) float64, sums to 1, zero on h == a
    meta: dict = field(default_factory=dict)

    # ---- marginals / derived distributions (one engine: every market is a sum over the same cells)
    @property
    def h_vals(self) -> np.ndarray:
        return np.arange(self.h_min, self.h_min + self.p.shape[0])

    @property
    def a_vals(self) -> np.ndarray:
        return np.arange(self.a_min, self.a_min + self.p.shape[1])

    def home(self) -> tuple[np.ndarray, np.ndarray]:
        return self.h_vals, self.p.sum(axis=1)

    def away(self) -> tuple[np.ndarray, np.ndarray]:
        return self.a_vals, self.p.sum(axis=0)

    def _grid(self):
        return np.meshgrid(self.h_vals, self.a_vals, indexing="ij")

    def margin(self) -> tuple[np.ndarray, np.ndarray]:
        H, A = self._grid()
        m = (H - A).ravel()
        lo = int(m.min())
        return np.arange(lo, int(m.max()) + 1), np.bincount(m - lo, weights=self.p.ravel())

    def total(self) -> tuple[np.ndarray, np.ndarray]:
        H, A = self._grid()
        t = (H + A).ravel()
        lo = int(t.min())
        return np.arange(lo, int(t.max()) + 1), np.bincount(t - lo, weights=self.p.ravel())

    def moments(self) -> dict:
        H, A = self._grid()
        P = self.p
        eh, ea = float((P * H).sum()), float((P * A).sum())
        M, T = H - A, H + A
        em, et = eh - ea, eh + ea
        vm = float((P * (M - em) ** 2).sum())
        vt = float((P * (T - et) ** 2).sum())
        cmt = float((P * (M - em) * (T - et)).sum())
        return {"exp_home": eh, "exp_away": ea, "exp_margin": em, "exp_total": et, "sd_margin": math.sqrt(vm),
                "sd_total": math.sqrt(vt), "rho_mt": cmt / math.sqrt(vm * vt), "var_margin": vm, "var_total": vt}

    def tie_mass(self) -> float:
        H, A = self._grid()
        return float(self.p[H == A].sum())

    def validate(self) -> dict:
        s = float(self.p.sum())
        neg = int((self.p < 0).sum())
        tie = self.tie_mass()
        ok = abs(s - 1.0) <= SUM_TOL and neg == 0 and tie <= TIE_TOL and np.isfinite(self.p).all()
        return {"PMF_SUM": s, "NEGATIVE_CELLS": neg, "TIE_MASS": tie, "VALID": bool(ok)}

    def sha256(self) -> str:
        return pmf_sha256(self.h_min, self.a_min, self.p)


def pmf_sha256(h_min: int, a_min: int, p: np.ndarray) -> str:
    q = np.ascontiguousarray(p, dtype=np.float64)
    return hashlib.sha256(f"{h_min}|{a_min}|{q.shape[0]}|{q.shape[1]}|".encode() + q.tobytes()).hexdigest()


# ============================================================================ endgame + overtime transforms
def apply_endgame(p0: np.ndarray, h_min: int, a_min: int, k: EndgameKernel) -> tuple[np.ndarray, int, int]:
    """P_reg(h, a) = sum_{h0, a0} P0(h0, a0) * K_{h0 - a0}(h - h0, a - a0). Origin unchanged; grid grows by reach."""
    R = k.reach
    if R == 0:
        return p0.copy(), h_min, a_min
    nh, na = p0.shape
    H0 = np.arange(h_min, h_min + nh)[:, None]
    A0 = np.arange(a_min, a_min + na)[None, :]
    lead = np.clip(H0 - A0, -k.max_lead, k.max_lead) + k.max_lead                 # (nh, na) row index into the table
    live = k.table.reshape(k.table.shape[0], -1).max(axis=0) > 1e-12              # kernel cells with any mass
    out = np.zeros((nh + R, na + R))
    for c in np.flatnonzero(live):                                                # gather per kernel cell: no 4-D copy
        dh, da = divmod(int(c), R + 1)
        out[dh:dh + nh, da:da + na] += p0 * k.table[:, dh, da][lead]
    return out, h_min, a_min


def _bvn_small(m_x: float, m_y: float, sd: float, rho: float, reach: int) -> np.ndarray:
    """One OT period's (home, away) points on 0..reach, from per-team means / SD / correlation."""
    sd_m = math.sqrt(2 * sd * sd * (1 - rho))
    sd_t = math.sqrt(2 * sd * sd * (1 + rho))
    return bvn_cells(m_x, m_y, sd_m, sd_t, 0.0, 0, reach, 0, reach)


def _conv2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    s = (a.shape[0] + b.shape[0] - 1, a.shape[1] + b.shape[1] - 1)
    out = np.fft.irfft2(np.fft.rfft2(a, s) * np.fft.rfft2(b, s), s)
    return np.clip(out, 0.0, None)


def overtime_increments(mu_home: float, mu_away: float, ot: OvertimeParams) -> tuple[np.ndarray, float]:
    """F(x, y): distribution of the TOTAL OT points added to a regulation tie, conditional on the game ending (x != y),
    after up to MAX_OT periods; returns (F, residual tie mass before the split)."""
    per = (5.0 / 40.0) * ot.kappa
    k1 = _bvn_small(per * mu_home, per * mu_away, ot.sd_team, ot.rho, ot.reach)
    n = k1.shape[0]
    diag = np.eye(n, dtype=bool)
    tie = np.where(diag, k1, 0.0)
    end = np.where(diag, 0.0, k1)
    F = end.copy()
    carry = tie.copy()                                                           # mass still tied after j periods
    for _ in range(MAX_OT - 1):
        step = _conv2(carry, end)
        F = _pad_add(F, step)
        carry = _conv2(carry, tie)
        carry = np.where(np.eye(*carry.shape, dtype=bool), carry, 0.0)           # still exactly tied
    resid = float(carry.sum())
    # split the residual tie mass by the one-period home-win probability (ties excluded)
    ph = float(np.tril(end, -1).sum()) / max(float(end.sum()), 1e-300)          # k1[x, y]: home wins iff x > y
    if resid > 0:
        idx = np.arange(carry.shape[0])
        hw = np.zeros((carry.shape[0] + 1, carry.shape[1] + 1))
        hw[idx + 1, idx] += carry[idx, idx] * ph
        hw[idx, idx + 1] += carry[idx, idx] * (1 - ph)
        F = _pad_add(F, hw)
    return F, resid


def _pad_add(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    s = (max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1]))
    out = np.zeros(s)
    out[:a.shape[0], :a.shape[1]] += a
    out[:b.shape[0], :b.shape[1]] += b
    return out


def apply_overtime(p_reg: np.ndarray, h_min: int, a_min: int, F: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Replace every regulation tie (s, s) by (s + x, s + y) ~ F; non-tied cells unchanged."""
    nh, na = p_reg.shape
    rh, ra = F.shape
    out = np.zeros((nh + rh - 1, na + ra - 1))
    out[:nh, :na] = p_reg
    for i in range(nh):
        s = h_min + i
        j = s - a_min
        if 0 <= j < na and p_reg[i, j] > 0:
            m = p_reg[i, j]
            out[i, j] -= m
            out[i:i + rh, j:j + ra] += m * F
    out[out < 0] = 0.0                                                           # exact zero at the removed ties
    return out, h_min, a_min


def _trim(p: np.ndarray, h_min: int, a_min: int, eps: float = 1e-13) -> tuple[np.ndarray, int, int]:
    rows = np.flatnonzero(p.sum(axis=1) > eps)
    cols = np.flatnonzero(p.sum(axis=0) > eps)
    p2 = p[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1].copy()
    return p2 / p2.sum(), h_min + int(rows[0]), a_min + int(cols[0])


# ============================================================================ build with moment matching
@dataclass(frozen=True)
class Targets:
    exp_home: float
    exp_away: float
    sd_margin: float
    sd_total: float
    rho_mt: float


def _build(m_h, m_a, sd_m, sd_t, rho, endgame: EndgameKernel, F: np.ndarray):
    vh, va, _ = _cov_ha(sd_m, sd_t, rho)
    sh, sa = math.sqrt(vh), math.sqrt(va)
    h_lo, h_hi = int(math.floor(m_h - SUPPORT_SD * sh)), int(math.ceil(m_h + SUPPORT_SD * sh))
    a_lo, a_hi = int(math.floor(m_a - SUPPORT_SD * sa)), int(math.ceil(m_a + SUPPORT_SD * sa))
    h_lo, a_lo = max(h_lo, 0), max(a_lo, 0)
    p0 = bvn_cells(m_h, m_a, sd_m, sd_t, rho, h_lo, h_hi, a_lo, a_hi)
    p1, hm, am = apply_endgame(p0, h_lo, a_lo, endgame)
    H1 = np.arange(hm, hm + p1.shape[0])[:, None]
    A1 = np.arange(am, am + p1.shape[1])[None, :]
    s1 = float(p1.sum())
    reg = (float((p1 * H1).sum()) / s1, float((p1 * A1).sum()) / s1, float(p1[H1 == A1].sum()) / s1)
    p2, hm, am = apply_overtime(p1, hm, am, F)
    p2, hm, am = _trim(p2, hm, am)
    H = np.arange(hm, hm + p2.shape[0])[:, None]
    A = np.arange(am, am + p2.shape[1])[None, :]
    p2[H == A] = 0.0
    g = GameJointPMF(hm, am, p2 / p2.sum())
    g._regulation = {"exp_home": reg[0], "exp_away": reg[1], "tie_mass": reg[2]}
    return g


def build(t: Targets, endgame: EndgameKernel, ot: OvertimeParams) -> GameJointPMF:
    """The final GameJointPMF whose moments match `t` (means exactly to MATCH_TOL; SDs / correlation likewise)."""
    for v, n in ((t.sd_margin, "sd_margin"), (t.sd_total, "sd_total")):
        if not (v > 0 and math.isfinite(v)):
            raise ValueError(f"{n}={v}")
    if not abs(t.rho_mt) < 0.95:
        raise ValueError(f"rho_mt={t.rho_mt}")
    F, resid = overtime_increments(t.exp_home, t.exp_away, ot)
    inc = endgame.mean_increments()
    lead0 = int(round(t.exp_home - t.exp_away))
    e_inc = inc[int(np.clip(lead0, -endgame.max_lead, endgame.max_lead)) + endgame.max_lead] if endgame.reach else np.zeros(2)
    m_h, m_a = t.exp_home - e_inc[0], t.exp_away - e_inc[1]
    sd_m, sd_t, rho = t.sd_margin, t.sd_total, t.rho_mt
    basis = mean_basis()
    hist = []
    for it in range(MATCH_ITERS):
        g = _build(m_h, m_a, sd_m, sd_t, rho, endgame, F)
        mo = g.moments()
        mm = g._regulation if basis == "REGULATION" else mo          # the moments the target means refer to
        err = {"exp_home": t.exp_home - mm["exp_home"], "exp_away": t.exp_away - mm["exp_away"],
               "sd_margin": t.sd_margin - mo["sd_margin"], "sd_total": t.sd_total - mo["sd_total"],
               "rho_mt": t.rho_mt - mo["rho_mt"]}
        hist.append(max(abs(err[k]) for k in ("exp_home", "exp_away", "sd_margin", "sd_total")))
        if hist[-1] <= MATCH_TOL and abs(err["rho_mt"]) <= MATCH_TOL:
            break
        m_h += err["exp_home"]
        m_a += err["exp_away"]
        sd_m = math.sqrt(max(0.25, sd_m ** 2 + t.sd_margin ** 2 - mo["var_margin"]))      # variance-additive step
        sd_t = math.sqrt(max(0.25, sd_t ** 2 + t.sd_total ** 2 - mo["var_total"]))
        rho = float(np.clip(rho + err["rho_mt"], -0.9, 0.9))
    g.meta = {"joint_version": JOINT_VERSIONS[basis], "mean_basis": basis, "regulation_moments": g._regulation,
              "latent": {"m_home": m_h, "m_away": m_a, "sd_margin": sd_m,
                                                         "sd_total": sd_t, "rho_mt": rho},
              "targets": t.__dict__, "match_iterations": it + 1, "match_max_abs_err": hist[-1],
              "ot_residual_tie_split": resid, "endgame_kernel_sha256": endgame.sha256(), "ot_params_sha256": ot.sha256(),
              "final_moments": g.moments()}
    return g
