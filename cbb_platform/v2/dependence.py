"""V2 Phase 2: marginal-preserving joint player/stat dependence, estimation and PSD game construction (research).

Population. The Phase 1B true-OOS forecast store only (modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet): every row
was forecast by a model trained on seasons before the forecast season. Dependence is conditional on playing, so only
participation_status == PLAYED rows with all 7 count labels enter; a DNP is never a zero, P(play) is not used (and is
not estimable for 2023-24 rows; nothing is imputed). A game enters only when both teams have eligible rows.

Latent residual (cbb_platform/v2/latent.py). z = Phi^-1(mid-PIT) (MID; RAND = randomized-PIT sensitivity), centered by
its model expectation, then by a training-population mean per (role, stat), and scaled by the training ratio of the
realized to the model-implied z variance per (role, stat). That step keeps marginal miscalibration (e.g. OOS PTS
forecasts that are too narrow) out of the dependence estimates; the marginals themselves are never changed.

Estimators (moment ratios, attenuation-corrected; no named-player or named-pair parameter anywhere):
    within player, group g, s != t:   R_g[s, t] = sum_i z_si z_ti / sum_i k_si k_ti        (i in group g)
    same team, distinct players:      S[s, t]   = sum_{i != j, same team} z_si z_tj / sum k_si k_tj
    opposite teams:                   X[s, t]   = sum_{i, j on opposite teams} z_si z_tj / sum k_si k_tj
Team sums make each O(n) per team (NBA factors.pair_moments). Game-clustered bootstrap SEs (whole games resampled);
every entry is soft-thresholded at SHRINK_Z SEs toward independence (NBA factors.soft_threshold). The groups are pregame
role (team L5-minutes rank band) x position group; a group's deviation from the pooled R is thresholded the same way,
so sparse groups fall back to the pooled matrix.

Game construction (adapted from NBA covariance.build_game_covariance): with the J2 shared Grams A (game), B (team
contrast) and Q (within-team zero sum) from S and X (NBA factors.dominating_additive_gram + competition_gate),
    Sigma = (C D)(C D)' + K_Q + blockdiag_i(R_i - c_i^2 G_i),   G_i = A + B + (n_team - 1) Q
pins every player's own block to exactly R_i, gives distinct teammates c_i c_j (A + B - Q) and opponents c_i c_j (A - B),
and is PSD by construction (c_i = largest value in (0, 1] keeping R_i - c_i^2 G_i PSD). Unit diagonal => every latent
coordinate is exactly N(0, 1) => every simulated marginal is exactly the Phase 1B marginal.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cbb_platform.v2 import latent as L

STATS = L.STATS
NS = len(STATS)
OOS_SEASONS = (2024, 2025, 2026)                       # the only seasons in the Phase 1B OOS store
ROLES = ("TOP5_MINUTES", "ROTATION_6_8", "BENCH_9PLUS", "NO_ROLE_HISTORY")
POSITIONS = ("G", "F", "C", "U")
SHRINK_Z = 1.96                                        # NBA DEFAULT_SHRINK_Z (declared, not tuned)
BOOTSTRAP = 200                                        # game-clustered bootstrap draws for estimation SEs
FIT_SEED = 20262                                       # bootstrap + RAND-PIT key
COMPETITION_GATE_LEVEL = 0.975                         # NBA COMPETITION_GATE_LEVEL
PSD_FLOOR = -1e-10                                     # validity floor for an assembled game matrix (NBA)
BLOCK_FLOOR = 1e-9                                     # residual player blocks keep min eig >= this (strictly PSD)
MIN_SHARED_SHRINK = 1e-6
STORE_COLUMNS = ["forecast_season", "training_through_season", "game_id", "team_id", "player_id", "player_game_example_id",
                 "is_home", "position_group", "role_diagnostic", "participation_status", "p_play"] + \
                [f"{s}_{c}" for s in STATS for c in ("mu", "phi", "family")] + [f"y_{s}" for s in STATS] + ["pmf_support_max"]


class V2Phase2DataViolation(RuntimeError):
    pass


# ============================================================================ population
def load_population(store_path, seasons) -> tuple[pd.DataFrame, dict]:
    """Eligible rows (PLAYED, 7 labels, both teams present) of the given OOS forecast seasons + exclusion accounting."""
    seasons = sorted(int(s) for s in seasons)
    if not set(seasons) <= set(OOS_SEASONS):
        raise V2Phase2DataViolation(f"Phase 2 reads OOS forecast seasons {OOS_SEASONS} only; refused {seasons}")
    df = pd.read_parquet(store_path, columns=STORE_COLUMNS, filters=[("forecast_season", "in", seasons)])
    if not (df["training_through_season"] < df["forecast_season"]).all():
        raise V2Phase2DataViolation("a forecast row was not out of sample")
    if not set(df["forecast_season"]) <= set(seasons):
        raise V2Phase2DataViolation("store filter leaked another season")
    if (df["pmf_support_max"] != L.KMAX).any():
        raise V2Phase2DataViolation("unexpected PMF support")
    acc = {"seasons": seasons, "candidate_rows": int(len(df)), "candidate_games": int(df["game_id"].nunique()),
           "excluded": {}}
    played = df["participation_status"].eq("PLAYED")
    for status, n in df.loc[~played, "participation_status"].value_counts().sort_index().items():
        acc["excluded"][f"NOT_A_PLAYING_OBSERVATION:{status}"] = int(n)
    d = df[played]
    ycols = [f"y_{s}" for s in STATS]
    miss = d[ycols].isna().any(axis=1)
    acc["excluded"]["PLAYED_BUT_COUNT_LABEL_MISSING"] = int(miss.sum())
    d = d[~miss]
    nteam = d.groupby("game_id")["team_id"].transform("nunique")
    acc["excluded"]["GAME_WITHOUT_BOTH_TEAMS_ELIGIBLE"] = int((nteam < 2).sum())
    acc["excluded_games_one_team"] = int(d.loc[nteam < 2, "game_id"].nunique())
    d = d[nteam == 2].copy()
    if d.groupby("game_id")["team_id"].nunique().max() > 2:
        raise V2Phase2DataViolation("a game with more than two teams")
    d["role_diagnostic"] = d["role_diagnostic"].fillna("NO_ROLE_HISTORY")
    d["position_group"] = d["position_group"].fillna("U")
    d = d.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)
    acc.update({"DEPENDENCE_ELIGIBLE_PLAYERS": int(d["player_id"].nunique()),
                "DEPENDENCE_ELIGIBLE_PLAYER_GAMES": int(len(d)),
                "DEPENDENCE_ELIGIBLE_GAMES": int(d["game_id"].nunique()),
                "player_games_by_season": {int(k): int(v) for k, v in d["forecast_season"].value_counts().sort_index().items()},
                "games_by_season": {int(k): int(v) for k, v in d.groupby("forecast_season")["game_id"].nunique().items()},
                "played_with_zero_minutes_retained": "PLAYED label rows are retained whatever the minutes (a recorded "
                                                     "appearance is a playing observation)",
                "p_play_used": False})
    return d, acc


def group_of(pop: pd.DataFrame) -> np.ndarray:
    return (pop["role_diagnostic"].astype(str) + "|" + pop["position_group"].astype(str)).to_numpy()


# ============================================================================ latent residuals
def residuals(pop: pd.DataFrame, kind: str = "MID", seed: int = FIT_SEED) -> dict:
    """Raw latent quantities (n x 7): z - E[z], Var[z] (model), kappa."""
    n = len(pop)
    zc, vz, kap = np.empty((n, NS)), np.empty((n, NS)), np.empty((n, NS))
    for j, s in enumerate(STATS):
        phi = pop[f"{s}_phi"].unique()
        if len(phi) != 1:
            raise V2Phase2DataViolation(f"{s}: more than one dispersion in a population")
        v = L.pit_uniform(seed, pop["game_id"].to_numpy(), pop["player_id"].to_numpy(), s) if kind == "RAND" else None
        r = L.latent_residuals(pop[f"{s}_mu"].to_numpy(dtype=float), float(phi[0]), pop[f"y_{s}"].to_numpy().astype(np.int64),
                               kind, v)
        zc[:, j], vz[:, j], kap[:, j] = r["z"] - r["ez"], r["vz"], r["kappa"]
    return {"kind": kind, "zc": zc, "vz": vz, "kappa": kap}


def normalization(pop: pd.DataFrame, res: dict) -> dict:
    """Training constants per (role, stat): mean offset delta and scale ratio lam = sqrt(E[(z-Ez-delta)^2] / E[Var z])."""
    out = {}
    role = pop["role_diagnostic"].to_numpy()
    for r in sorted(set(role)):
        m = role == r
        delta = res["zc"][m].mean(axis=0)
        lam = np.sqrt(((res["zc"][m] - delta) ** 2).mean(axis=0) / res["vz"][m].mean(axis=0))
        out[r] = {"n": int(m.sum()), "delta": delta.tolist(), "lam": lam.tolist()}
    return out


def standardized(pop: pd.DataFrame, res: dict, norm: dict) -> np.ndarray:
    role = pop["role_diagnostic"].to_numpy()
    out = np.empty_like(res["zc"])
    pooled = None
    for r in np.unique(role):
        m = role == r
        if r not in norm:                                 # role unseen in training: pooled constants
            if pooled is None:
                w = np.array([v["n"] for v in norm.values()], dtype=float)
                pooled = {k: np.average([v[k] for v in norm.values()], axis=0, weights=w) for k in ("delta", "lam")}
            c = pooled
        else:
            c = norm[r]
        out[m] = (res["zc"][m] - np.asarray(c["delta"])) / np.asarray(c["lam"])
    return out


# ============================================================================ pooled moments
def _outer(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a[:, :, None] * b[:, None, :]).reshape(len(a), NS * NS)


def game_moments(pop: pd.DataFrame, zt: np.ndarray, kap: np.ndarray, groups: list[str]) -> dict:
    """Per-game sufficient statistics (numerators from z, denominators from kappa), flattened 7x7."""
    gcode, gids = pd.factorize(pop["game_id"], sort=True)
    team = pop.groupby("game_id")["team_id"].transform(lambda t: (t != t.min()).astype(int)).to_numpy()
    G = len(gids)
    grp = group_of(pop)
    gix = {g: i for i, g in enumerate(groups)}
    gi = np.array([gix[g] for g in grp])
    out = {"games": np.asarray(gids), "G": G, "groups": groups}
    tn = np.zeros((G, 2))
    np.add.at(tn, (gcode, team), 1.0)
    out["same_pairs"] = float((tn * (tn - 1)).sum())
    out["cross_pairs"] = float((2 * tn[:, 0] * tn[:, 1]).sum())
    for key, x in (("z", zt), ("k", kap)):
        own = _outer(x, x)
        w = np.zeros((G, len(groups), NS * NS))
        np.add.at(w, (gcode, gi), own)
        out[f"within_{key}"] = w
        tsum = np.zeros((G, 2, NS))
        np.add.at(tsum, (gcode, team), x)
        own_g = w.sum(axis=1)
        same = _outer(tsum[:, 0], tsum[:, 0]) + _outer(tsum[:, 1], tsum[:, 1]) - own_g
        cross = _outer(tsum[:, 0], tsum[:, 1]) + _outer(tsum[:, 1], tsum[:, 0])
        out[f"same_{key}"], out[f"cross_{key}"] = same, cross
    return out


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(np.abs(den) > 0, num / den, 0.0)
    return r.reshape(r.shape[:-1] + (NS, NS))


def soft_threshold(est: np.ndarray, se: np.ndarray, z: float = SHRINK_Z) -> np.ndarray:
    """sign(r) max(0, |r| - z se) (NBA factors.soft_threshold)."""
    return np.sign(est) * np.clip(np.abs(est) - z * se, 0.0, None)


def _offdiag(m: np.ndarray) -> np.ndarray:
    m = 0.5 * (m + np.swapaxes(m, -1, -2))
    idx = np.arange(NS)
    m[..., idx, idx] = 1.0
    return m


def min_eig(m: np.ndarray) -> float:
    return float(np.linalg.eigvalsh(0.5 * (m + m.T)).min())


def psd_toward_identity(R: np.ndarray, floor: float = 1e-8) -> tuple[np.ndarray, float]:
    """Largest lam in [0, 1] with lam R + (1 - lam) I having min eigenvalue >= floor (recorded, never silent)."""
    if min_eig(R) >= floor:
        return R, 1.0
    e = min_eig(R)                                       # min eig of lam R + (1-lam) I = lam e + 1 - lam
    lam = (1 - floor) / (1 - e)
    return lam * R + (1 - lam) * np.eye(len(R)), float(lam)


CONDITION_RATIO = 0.5                                  # a child matrix keeps min eig >= this x its parent's


def conditioned(parent: np.ndarray, dev: np.ndarray, ratio: float = CONDITION_RATIO) -> tuple[np.ndarray, float]:
    """parent + t * dev with the largest t in [0, 1] keeping min eig >= ratio * min eig(parent): a sparse group's
    thresholded deviations are scaled back toward the pooled matrix rather than clipped (recorded when t < 1)."""
    floor = ratio * min_eig(parent)
    if min_eig(parent + dev) >= floor:
        return _offdiag(parent + dev), 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if min_eig(parent + mid * dev) >= floor:
            lo = mid
        else:
            hi = mid
    return _offdiag(parent + lo * dev), float(lo)


def project_psd_rank(m: np.ndarray, rank: int) -> tuple[np.ndarray, np.ndarray]:
    """Best rank-`rank` PSD approximation and loadings (NBA covariance.project_psd_rank)."""
    sym = 0.5 * (m + m.T)
    w, V = np.linalg.eigh(sym)
    o = np.argsort(w)[::-1]
    w, V = w[o], V[:, o]
    k = min(rank, len(w))
    load = V[:, :k] * np.sqrt(np.clip(w[:k], 0.0, None))
    return load @ load.T, load


def psd_part(m: np.ndarray) -> np.ndarray:
    return project_psd_rank(m, len(m))[0]


def dominating_additive_gram(S: np.ndarray, X: np.ndarray) -> np.ndarray:
    """M = A + B dominating S, X, -X in the Loewner order (NBA factors.dominating_additive_gram)."""
    base = psd_part(S)
    return base + psd_part(X - base) + psd_part(-X - base)


def competition_gate(S_point: np.ndarray, S_draws_min_eig: np.ndarray, level: float = COMPETITION_GATE_LEVEL) -> tuple[bool, dict]:
    """Admit the within-team zero-sum family only if the same-team block is significantly indefinite (basic/pivotal
    bootstrap bound on min eig(S) below 0; NBA factors.competition_gate)."""
    point = min_eig(S_point)
    lo = float(np.quantile(S_draws_min_eig, 1 - level))
    upper = 2 * point - lo
    return bool(upper < 0), {"min_eigenvalue_point": point, "bootstrap_lower_quantile": lo, "pivotal_upper_bound": upper,
                             "level": level, "draws": int(len(S_draws_min_eig))}


# ============================================================================ fit
def fit(pop: pd.DataFrame, kind: str = "MID", boot: int = BOOTSTRAP, seed: int = FIT_SEED) -> dict:
    """Estimate every dependence ingredient from one fit population (training OOS seasons only)."""
    res = residuals(pop, kind, seed)
    norm = normalization(pop, res)
    zt = standardized(pop, res, norm)
    groups = sorted(set(group_of(pop)))
    gm = game_moments(pop, zt, res["kappa"], groups)
    G = gm["G"]
    rng = np.random.default_rng(seed)
    W = rng.multinomial(G, np.full(G, 1.0 / G), size=boot).astype(float)       # B x G whole-game resampling weights

    def est(num, den):                                                          # point, bootstrap draws
        return _ratio(num.sum(axis=0), den.sum(axis=0)), _ratio(W @ num, W @ den)

    wz, wk = gm["within_z"], gm["within_k"]
    Rg_pt, Rg_bs = est(wz.sum(axis=1), wk.sum(axis=1))
    Rg_se = Rg_bs.std(axis=0, ddof=1)
    R_global = _offdiag(soft_threshold(Rg_pt, Rg_se))
    R_global, lam_global = psd_toward_identity(R_global)
    psd_events = []
    if lam_global < 1:
        psd_events.append({"matrix": "within_global", "lam": lam_global})
    gkeys = np.array(groups)
    roles_of = np.array([g.split("|")[0] for g in groups])
    pos_of = np.array([g.split("|")[1] for g in groups])
    grp_rows = group_of(pop)

    def child(mask, parent_pt, parent_bs, parent_R, label, n_rows):
        num, den = wz[:, mask].sum(axis=1), wk[:, mask].sum(axis=1)
        pt, bs = _ratio(num.sum(axis=0), den.sum(axis=0)), _ratio(W @ num, W @ den)
        dev_se = (bs - parent_bs).std(axis=0, ddof=1)
        dev = soft_threshold(pt - parent_pt, dev_se)
        np.fill_diagonal(dev, 0.0)
        dev = 0.5 * (dev + dev.T)
        R, lam = conditioned(parent_R, dev)
        if lam < 1:
            psd_events.append({"matrix": label, "deviation_scale": lam})
        return pt, bs, R, {"n_player_games": n_rows, "R": R.tolist(), "raw": pt.tolist(), "deviation_se": dev_se.tolist(),
                           "deviation_thresholded": dev.tolist(), "deviation_scale": lam,
                           "cells_deviating": int((np.triu(dev, 1) != 0).sum()), "min_eigenvalue": min_eig(R)}

    role_out, group_out = {}, {}
    for r in sorted(set(roles_of)):
        m = roles_of == r
        pt_r, bs_r, R_r, rec = child(m, Rg_pt, Rg_bs, R_global, f"within_role:{r}", int(np.isin(grp_rows, gkeys[m]).sum()))
        role_out[r] = rec
        for g in gkeys[m]:
            if g.endswith("|U"):                                       # position unknown: the role matrix
                group_out[g] = {"n_player_games": int((grp_rows == g).sum()), "R": R_r.tolist(), "uses": f"role:{r}"}
                continue
            _, _, _, rec_g = child(gkeys == g, pt_r, bs_r, R_r, f"within_group:{g}", int((grp_rows == g).sum()))
            group_out[g] = rec_g

    S_pt, S_bs = est(gm["same_z"], gm["same_k"])
    X_pt, X_bs = est(gm["cross_z"], gm["cross_k"])
    S_se, X_se = S_bs.std(axis=0, ddof=1), X_bs.std(axis=0, ddof=1)
    S_pt, X_pt = 0.5 * (S_pt + S_pt.T), 0.5 * (X_pt + X_pt.T)
    S_thr, X_thr = soft_threshold(S_pt, S_se), soft_threshold(X_pt, X_se)
    S_thr, X_thr = 0.5 * (S_thr + S_thr.T), 0.5 * (X_thr + X_thr.T)
    gate, gate_ev = competition_gate(S_pt, np.array([min_eig(b) for b in S_bs]))
    M = dominating_additive_gram(S_thr, X_thr)
    Q = (M - S_thr) if gate else np.zeros_like(S_thr)
    A, B = 0.5 * (M + X_thr), 0.5 * (M - X_thr)
    _, A_load = project_psd_rank(A, NS)
    _, B_load = project_psd_rank(B, NS)
    _, Q_load = project_psd_rank(Q, NS)
    A_load, B_load, Q_load = (_trim(x) for x in (A_load, B_load, Q_load))
    S_fit = A_load @ A_load.T + B_load @ B_load.T - Q_load @ Q_load.T
    X_fit = A_load @ A_load.T - B_load @ B_load.T

    return {
        "pit": kind, "stats": list(STATS), "fit_seasons": sorted(int(s) for s in pop["forecast_season"].unique()),
        "fit_player_games": int(len(pop)), "fit_games": int(G), "fit_players": int(pop["player_id"].nunique()),
        "bootstrap_draws": int(boot), "shrink_z": SHRINK_Z, "seed": int(seed),
        "normalization": norm,
        "within_player": {"global": {"R": R_global.tolist(), "raw": Rg_pt.tolist(), "se": Rg_se.tolist(),
                                     "min_eigenvalue": min_eig(R_global)},
                          "roles": role_out, "groups": group_out, "hierarchy": "global -> role -> role x position "
                          "(G/F/C; unknown position U uses its role matrix); each level's deviation from its parent is "
                          "soft-thresholded at SHRINK_Z game-clustered SEs"},
        "same_team": {"raw": S_pt.tolist(), "se": S_se.tolist(), "thresholded": S_thr.tolist(), "fitted": S_fit.tolist(),
                      "ordered_pairs": gm["same_pairs"]},
        "cross_team": {"raw": X_pt.tolist(), "se": X_se.tolist(), "thresholded": X_thr.tolist(), "fitted": X_fit.tolist(),
                       "ordered_pairs": gm["cross_pairs"]},
        "shared": {"A_loadings": A_load.tolist(), "B_loadings": B_load.tolist(), "Q_loadings": Q_load.tolist(),
                   "competition_family_active": gate, "competition_gate": gate_ev,
                   "A_eigenvalues": np.linalg.eigvalsh(A)[::-1].tolist(), "B_eigenvalues": np.linalg.eigvalsh(B)[::-1].tolist(),
                   "Q_eigenvalues": np.linalg.eigvalsh(Q)[::-1].tolist()},
        "psd_repairs_at_fit": psd_events,
    }


def _trim(load: np.ndarray, tol: float = 1e-12) -> np.ndarray:
    keep = np.sqrt((load ** 2).sum(axis=0)) > tol
    return load[:, keep] if keep.any() else np.zeros((NS, 0))


# ============================================================================ candidate structures
@dataclass
class Structure:
    """What the simulator needs for one candidate. J0: nothing; J1: within-player R by group; J2: + shared Grams."""
    name: str
    R_groups: dict = field(default_factory=dict)
    R_global: np.ndarray | None = None
    A: np.ndarray = field(default_factory=lambda: np.zeros((NS, 0)))
    B: np.ndarray = field(default_factory=lambda: np.zeros((NS, 0)))
    Q: np.ndarray = field(default_factory=lambda: np.zeros((NS, 0)))
    _c_cache: dict = field(default_factory=dict, repr=False)

    @property
    def independent(self) -> bool:
        return self.R_global is None

    @property
    def shared(self) -> bool:
        return self.A.shape[1] + self.B.shape[1] + self.Q.shape[1] > 0

    def R(self, group: str) -> np.ndarray:
        return self.R_groups.get(group, self.R_global)


def structure(candidate: str, fitted: dict | None) -> Structure:
    if candidate == "J0":
        return Structure("J0")
    wp = fitted["within_player"]
    s = Structure(candidate, {g: np.asarray(v["R"]) for g, v in wp["groups"].items()}, np.asarray(wp["global"]["R"]))
    if candidate == "J2":
        sh = fitted["shared"]
        s.A, s.B = np.asarray(sh["A_loadings"]).reshape(NS, -1), np.asarray(sh["B_loadings"]).reshape(NS, -1)
        s.Q = np.asarray(sh["Q_loadings"]).reshape(NS, -1)
    elif candidate != "J1":
        raise ValueError(candidate)
    return s


def _largest_feasible_shrink(R: np.ndarray, Gm: np.ndarray, floor: float = BLOCK_FLOOR) -> float:
    """Largest c in (0, 1] with R - c^2 Gm PSD (min eig concave, non-increasing in c^2: bisection; NBA)."""
    if min_eig(R - Gm) >= floor:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if min_eig(R - mid * Gm) >= floor:
            lo = mid
        else:
            hi = mid
    return float(np.sqrt(lo))


def player_shrinks(R: np.ndarray, AB: np.ndarray, QQ: np.ndarray, n_team: int) -> tuple[float, float]:
    """(c_ab, c_q) for one player block. CBB change to the NBA single shrink: the game/team-contrast Gram A + B is
    admitted first (largest c_ab with R - c_ab^2 (A + B) PSD), then the within-team zero-sum Gram (largest c_q with
    R - c_ab^2 (A + B) - c_q^2 (n - 1) Q PSD). With 10-11 played players per team, (n - 1) Q can exceed what a unit
    variance block can absorb; shrinking only Q then leaves the game/opponent structure intact."""
    c_ab = _largest_feasible_shrink(R, AB)
    c_q = _largest_feasible_shrink(R - c_ab ** 2 * AB, max(n_team - 1, 0) * QQ) if QQ.any() and n_team > 1 else 1.0
    return c_ab, c_q


def game_correlation(st: Structure, groups: list[str], team: np.ndarray) -> dict:
    """Latent correlation for one game; dims are player-major (player p, stat s) -> p * 7 + s. team: 0/1 per player.
        Sigma = (C_ab D)(C_ab D)' + C_q kron_team(n I - 11', Q) C_q + blockdiag_i(R_i - c_ab,i^2 (A+B) - c_q,i^2 (n-1) Q)
    a sum of a Gram, a congruence of a Kronecker product of PSD matrices and PSD blocks: PSD by construction."""
    n = len(groups)
    d = n * NS
    if st.independent:
        return {"corr": np.eye(d), "c_ab": np.ones(n), "c_q": np.ones(n), "min_eig": 1.0, "invalid": False, "diag_err": 0.0}
    team = np.asarray(team)
    nteam = np.array([(team == t).sum() for t in team])
    Rs = [st.R(g) for g in groups]
    corr = np.zeros((d, d))
    c_ab, c_q = np.ones(n), np.ones(n)
    if st.shared:
        AB, QQ = st.A @ st.A.T + st.B @ st.B.T, st.Q @ st.Q.T
        side = np.where(team == 0, 1.0, -1.0)
        D = np.zeros((d, st.A.shape[1] + st.B.shape[1]))
        for p in range(n):
            key = (groups[p], int(nteam[p]))
            if key not in st._c_cache:
                st._c_cache[key] = player_shrinks(Rs[p], AB, QQ, int(nteam[p]))
            c_ab[p], c_q[p] = st._c_cache[key]
            if c_ab[p] < MIN_SHARED_SHRINK:
                raise V2Phase2DataViolation(f"player block cannot absorb shared structure: {key}")
            sl = slice(p * NS, (p + 1) * NS)
            D[sl] = c_ab[p] * np.hstack([st.A, side[p] * st.B])
            corr[sl, sl] += Rs[p] - c_ab[p] ** 2 * AB - c_q[p] ** 2 * max(nteam[p] - 1, 0) * QQ
        corr += D @ D.T
        if QQ.any():
            for t in (0, 1):
                mem = np.flatnonzero(team == t)
                nt = len(mem)
                K = np.kron(nt * np.eye(nt) - np.ones((nt, nt)), QQ)
                cq = np.repeat(c_q[mem], NS)
                ix = (mem[:, None] * NS + np.arange(NS)).ravel()
                corr[np.ix_(ix, ix)] += cq[:, None] * K * cq[None, :]
    else:
        for p in range(n):
            corr[p * NS:(p + 1) * NS, p * NS:(p + 1) * NS] = Rs[p]
    corr = 0.5 * (corr + corr.T)
    diag_err = float(np.abs(np.diag(corr) - 1).max())
    np.fill_diagonal(corr, 1.0)
    me = min_eig(corr)
    return {"corr": corr, "c_ab": c_ab, "c_q": c_q, "min_eig": me, "invalid": bool(me < PSD_FLOOR), "diag_err": diag_err}


def cholesky_unit(corr: np.ndarray) -> tuple[np.ndarray, float]:
    """Cholesky factor with unit row norms (so diag(L L') == 1 exactly); jitter only for float rounding, recorded."""
    jitter = 0.0
    me = min_eig(corr)
    if me <= 1e-12:
        jitter = abs(me) + 1e-12
    Lc = np.linalg.cholesky(corr + jitter * np.eye(len(corr)))
    Lc /= np.sqrt((Lc ** 2).sum(axis=1))[:, None]
    return Lc, jitter


def game_seed(base: int, game_id: str) -> int:
    d = hashlib.blake2b(f"{base}|{game_id}".encode(), digest_size=8, person=b"cbbv2sim").digest()
    return int.from_bytes(d, "big") >> 1
