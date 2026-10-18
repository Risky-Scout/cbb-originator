"""V2 Phase 2: discrete PIT latent residuals for the Phase 1B count marginals (research; never alters a marginal).

For an observed count y with marginal CDF F (props_v0.pmf_matrix, support 0..kmax with the tail folded into kmax), the
observation is the latent interval  Z in [a_y, b_y] = [Phi^-1(F(y-1)), Phi^-1(F(y))]  of a standard normal Z.

Two transforms of that interval (adapted from NBA Prop Quant research/game_latent_state/pit.py):
    MID   z = Phi^-1(F(y-1) + p(y)/2)             (the NBA incumbent copula's transform; deterministic)
    RAND  z = Phi^-1(F(y-1) + v p(y)), v ~ U(0,1) (the NBA shadow layer's exact randomized PIT; v is a keyed BLAKE2b
                                                    digest of (game, player, stat), so it is reproducible and
                                                    order-free)

Neither z is the latent Z, so a raw correlation of z understates the latent copula correlation (most for sparse
STL/BLK/FG3M). CBB-specific change: the dependence estimators divide pooled cross-moments by their model-implied
first-order attenuation, using per-row quantities that are exact functions of the marginal PMF:
    E[z]          = sum_y p_y z(y)                  (MID; 0 for RAND)
    kappa = E[z Z] = sum_y p_y z(y) m_y   (MID),     sum_y p_y m_y^2 (RAND),     m_y = (phi(a_y) - phi(b_y)) / p_y
Under a Gaussian copula with latent correlation rho, E[(z_i - Ez_i)(z_j - Ez_j)] = rho kappa_i kappa_j + O(rho^2), so
rho_hat = sum (z - Ez)(z - Ez) / sum kappa kappa is a moment estimator of rho (first-order Hermite). It is a regularized
dependence estimator, not a likelihood; MID and RAND give two estimates of the same rho (the randomized-PIT
sensitivity).
"""
from __future__ import annotations

import hashlib

import numpy as np

from cbb_platform import props_v0 as P

STATS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV")
KMAX = 120                                   # the OOS store's pmf_support_max (Phase 1B marginal)
PIT_EPSILON = 1e-12                          # clip before Phi^-1 (NBA PIT_EPSILON); |z| <= 7.03
PIT_HASH_PERSON = b"cbbv2pit"
_SCALE64 = float(1 << 64)


# Wichura (1988) AS241 (the CPython statistics.NormalDist.inv_cdf coefficients), vectorized; the locked environment has
# numpy but no scipy, and no new dependency is allowed.
_A = [2.5090809287301226727e+3, 3.3430575583588128105e+4, 6.7265770927008700853e+4, 4.5921953931549871457e+4,
      1.3731693765509461125e+4, 1.9715909503065514427e+3, 1.3314166789178437745e+2, 3.3871328727963666080e+0]
_B = [5.2264952788528545610e+3, 2.8729085735721942674e+4, 3.9307895800092710610e+4, 2.1213794301586595867e+4,
      5.3941960214247511077e+3, 6.8718700749205790830e+2, 4.2313330701600911252e+1, 1.0]
_C = [7.7454501427834140764e-4, 2.2723844989269184583e-2, 2.4178072517745061177e-1, 1.2704582524523683826e+0,
      3.6478483247632046050e+0, 5.7694972214606914055e+0, 4.6303378461565452959e+0, 1.4234371107496835773e+0]
_D = [1.0507500716444168432e-9, 5.4759380849953449460e-4, 1.5198666563616457197e-2, 1.4810397642748007459e-1,
      6.8976733498510000455e-1, 1.6763848301838038494e+0, 2.0531916266377588219e+0, 1.0]
_E = [2.0103343992922881327e-7, 2.7115555687434875782e-5, 1.2426609473880784386e-3, 2.6532189526576123093e-2,
      2.9656057182850489123e-1, 1.7848265399172913358e+0, 5.4637849111641143699e+0, 6.6579046435011037772e+0]
_F = [2.0442631033899397856e-15, 1.4215117583164458887e-7, 1.8463183175100546818e-5, 7.8686913114561325910e-4,
      1.4875361290850614853e-2, 1.3692988092273580531e-1, 5.9983220655588793769e-1, 1.0]


def ndtri(p) -> np.ndarray:
    """Phi^-1(p) for p in (0, 1), elementwise (AS241, ~1e-16 relative)."""
    p = np.asarray(p, dtype=float)
    q = p - 0.5
    out = np.empty_like(p)
    c = np.abs(q) <= 0.425
    r = 0.180625 - q[c] * q[c]
    out[c] = q[c] * np.polyval(_A, r) / np.polyval(_B, r)
    t = ~c & (p > 0) & (p < 1)
    r = np.sqrt(-np.log(np.where(q[t] <= 0, p[t], 1.0 - p[t])))
    lo = r <= 5.0
    x = np.empty_like(r)
    x[lo] = np.polyval(_C, r[lo] - 1.6) / np.polyval(_D, r[lo] - 1.6)
    x[~lo] = np.polyval(_E, r[~lo] - 5.0) / np.polyval(_F, r[~lo] - 5.0)
    out[t] = np.where(q[t] < 0, -x, x)
    out[p <= 0], out[p >= 1] = -np.inf, np.inf
    return out


def cdf_table(mu: np.ndarray, phi: float, kmax: int = KMAX) -> np.ndarray:
    """F(0..kmax) per row from the exact Phase 1B marginal PMF (props_v0.pmf_matrix); F(kmax) = 1."""
    pmf = P.pmf_matrix(np.asarray(mu, dtype=float), float(phi), kmax)
    cdf = np.cumsum(pmf, axis=1)
    cdf = np.minimum(np.maximum.accumulate(cdf, axis=1), 1.0)
    cdf[:, -1] = 1.0
    return cdf


def interval(cdf: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(F(y-1), F(y)) for integer y (y > kmax maps to the folded tail point kmax)."""
    y = np.minimum(np.asarray(y, dtype=np.int64), cdf.shape[1] - 1)
    r = np.arange(len(y))
    hi = cdf[r, y]
    lo = np.where(y > 0, cdf[r, np.maximum(y - 1, 0)], 0.0)
    return lo, hi


def pit_uniform(seed: int, game: np.ndarray, player: np.ndarray, stat: str) -> np.ndarray:
    """Locked v in (0, 1) per (game, player, stat): keyed digest of the observation identity (NBA pit.py pattern)."""
    key = int(seed).to_bytes(8, "big", signed=True)
    out = np.empty(len(game))
    for i, (g, p) in enumerate(zip(game, player)):
        d = hashlib.blake2b(b"|".join((key, str(g).encode(), str(p).encode(), stat.encode())), digest_size=8,
                            person=PIT_HASH_PERSON).digest()
        out[i] = (int.from_bytes(d, "big") + 0.5) / _SCALE64
    return out


def gaussianize(u: np.ndarray) -> np.ndarray:
    return ndtri(np.clip(u, PIT_EPSILON, 1 - PIT_EPSILON))


def latent_residuals(mu: np.ndarray, phi: float, y: np.ndarray, kind: str, v: np.ndarray | None = None,
                     chunk: int = 20000) -> dict:
    """z, E[z], Var[z] and kappa per row for one stat under its own marginal. kind: 'MID' | 'RAND' (RAND needs v)."""
    n = len(mu)
    z, ez, vz, kap = np.empty(n), np.empty(n), np.empty(n), np.empty(n)
    for s in range(0, n, chunk):
        sl = slice(s, s + chunk)
        cdf = cdf_table(mu[sl], phi)
        lo, hi = interval(cdf, y[sl])
        low = np.concatenate([np.zeros((cdf.shape[0], 1)), cdf[:, :-1]], axis=1)       # F(k-1)
        p = cdf - low
        a, b = gaussianize(low), gaussianize(cdf)
        a[:, 0], b[:, -1] = -np.inf, np.inf                                               # exact end points
        dens = (np.exp(-0.5 * a * a) - np.exp(-0.5 * b * b)) / np.sqrt(2 * np.pi)       # phi(a) - phi(b) = p m
        if kind == "MID":
            zt = gaussianize(low + 0.5 * p)
            z[sl] = gaussianize(lo + 0.5 * (hi - lo))
            ez[sl] = (p * zt).sum(axis=1)
            vz[sl] = (p * zt * zt).sum(axis=1) - ez[sl] ** 2
            kap[sl] = (zt * dens).sum(axis=1)                                            # sum p z m = sum z (phi(a)-phi(b))
        elif kind == "RAND":
            z[sl] = gaussianize(lo + v[sl] * (hi - lo))
            ez[sl], vz[sl] = 0.0, 1.0
            with np.errstate(divide="ignore", invalid="ignore"):
                kap[sl] = np.where(p > 1e-300, dens * dens / p, 0.0).sum(axis=1)
        else:
            raise ValueError(kind)
    return {"z": z, "ez": ez, "vz": vz, "kappa": kap}

