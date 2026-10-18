"""V2 Phase 2: same-draw joint generator over the Phase 1B marginals + joint scoring (research).

One game:
    1. every eligible player's 7 Phase 1B marginal PMFs (props_v0.pmf_matrix at the store's mu / phi, support 0..120);
    2. the candidate's latent correlation (cbb_platform/v2/dependence.game_correlation; J0 = identity);
    3. ONE latent normal bank E (sims x 7n) from a game-keyed seed, shared by every candidate (common random numbers);
       latent = E L' with L the unit-row Cholesky factor (diag(L L') == 1 exactly);
    4-5. latent -> uniform -> inverse marginal CDF. Implemented as the equivalent latent-threshold comparison
       X = min{k : F(k) >= Phi(z)} = #{k : Phi^-1(F(k)) < z}, which needs only Phi^-1 (the locked environment has no
       scipy); tests check the two routes give the same integers.
Every market of the game is read from that one bank: PRA = PTS + REB + AST, PR, PA, RA, STOCKS = STL + BLK per draw,
and cross-player joint events. Nothing is stored beyond per-game score summaries.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cbb_platform import props_v0 as P
from cbb_platform.v2 import dependence as D, latent as L

STATS = L.STATS
NS = len(STATS)
SI = {s: i for i, s in enumerate(STATS)}
SIMS = 20_000
VS_DRAWS = 2_000                                       # variogram expectation from the first 2,000 draws of the bank
VS_P = 0.5                                             # Scheuerer & Hamill (2015) order
SIM_SEED = 20263
COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"),
          "STOCKS": ("STL", "BLK")}
COMBO_LINE_Q = (0.25, 0.5, 0.75)                       # lines from the J0 (exact convolution) combo quantiles
PROB_FLOOR_DRAWS = 0.5                                 # MC probabilities floored at 0.5 / sims for log scores


# ============================================================================ marginals of one game
def game_marginals(rows: pd.DataFrame) -> dict:
    """PMF / CDF / latent thresholds for every (player, stat) of one game, player-major."""
    n = len(rows)
    pmf = np.empty((n, NS, L.KMAX + 1))
    for j, s in enumerate(STATS):
        pmf[:, j] = P.pmf_matrix(rows[f"{s}_mu"].to_numpy(dtype=float), float(rows[f"{s}_phi"].iloc[0]), L.KMAX)
    cdf = np.minimum(np.maximum.accumulate(np.cumsum(pmf, axis=2), axis=2), 1.0)
    cdf[..., -1] = 1.0
    with np.errstate(divide="ignore"):
        thr = np.where(cdf >= 1.0, np.inf, L.ndtri(np.clip(cdf, 1e-300, 1.0)))
    k = np.arange(L.KMAX + 1)
    mean = (pmf * k).sum(axis=2)
    var = (pmf * k * k).sum(axis=2) - mean ** 2
    return {"pmf": pmf, "cdf": cdf, "thr": thr, "mean": mean, "var": var}


def counts_from_latent(latent: np.ndarray, thr: np.ndarray) -> np.ndarray:
    """latent: sims x d (player-major); thr: n x 7 x (kmax+1). X = #{k : Phi^-1(F(k)) < z}."""
    t = thr.reshape(-1, thr.shape[-1])
    out = np.empty(latent.shape, dtype=np.int16)
    for c in range(latent.shape[1]):
        out[:, c] = np.searchsorted(t[c], latent[:, c], side="left")
    return out


def simulate(rows: pd.DataFrame, structures: dict, sims: int = SIMS, seed_base: int = SIM_SEED,
             marg: dict | None = None) -> tuple[dict, dict]:
    """One joint bank per candidate from ONE standard-normal bank. Returns ({name: draws sims x n x 7}, info)."""
    marg = marg or game_marginals(rows)
    n = len(rows)
    groups = list(D.group_of(rows))
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    seed = D.game_seed(seed_base, rows["game_id"].iloc[0])
    E = np.random.default_rng(seed).standard_normal((sims, n * NS))
    out, info = {}, {"seed": seed}
    for name, st in structures.items():
        gc = D.game_correlation(st, groups, team)
        if st.independent:
            latent, jitter = E, 0.0
        else:
            Lc, jitter = D.cholesky_unit(gc["corr"])
            latent = E @ Lc.T
        out[name] = counts_from_latent(latent, marg["thr"]).reshape(sims, n, NS)
        info[name] = {"min_eig": gc["min_eig"], "invalid": gc["invalid"], "diag_err": gc["diag_err"], "jitter": jitter,
                      "c_ab_min": float(gc["c_ab"].min()), "c_q_mean": float(gc["c_q"].mean()),
                      "c_q_min": float(gc["c_q"].min())}
    return out, info


# ============================================================================ per-game scores
def energy_score(x: np.ndarray, y: np.ndarray) -> float:
    """x: sims x d, y: d. E||X - y|| - 0.5 E||X - X'|| (X' = the next draw; unbiased pairing)."""
    t1 = np.sqrt(((x - y) ** 2).sum(axis=1)).mean()
    h = len(x) // 2
    t2 = np.sqrt(((x[:h] - x[h:2 * h]) ** 2).sum(axis=1)).mean()
    return float(t1 - 0.5 * t2)


def pair_masks(n: int, team: np.ndarray) -> dict:
    p = np.repeat(np.arange(n), NS)
    t = np.repeat(team, NS)
    iu = np.triu(np.ones((n * NS, n * NS), dtype=bool), 1)
    same_p = p[:, None] == p[None, :]
    same_t = t[:, None] == t[None, :]
    return {"within_player": iu & same_p, "same_team": iu & ~same_p & same_t, "cross_team": iu & ~same_t}


def variogram(x: np.ndarray, y: np.ndarray, masks: dict, chunk: int = 250) -> dict:
    """Scaled variogram score (order VS_P) per pair type: mean over pairs of (|y_a-y_b|^p - E|X_a-X_b|^p)^2."""
    d = x.shape[1]
    ev = np.zeros((d, d))
    for s in range(0, len(x), chunk):
        xc = x[s:s + chunk]
        ev += (np.abs(xc[:, :, None] - xc[:, None, :]) ** VS_P).sum(axis=0)
    ev /= len(x)
    vy = np.abs(y[:, None] - y[None, :]) ** VS_P
    sq = (vy - ev) ** 2
    out = {k: float(sq[m].mean()) for k, m in masks.items()}
    out["balanced"] = float(np.mean([out[k] for k in masks]))
    out["uniform"] = float(sq[np.triu(np.ones((d, d), dtype=bool), 1)].mean())
    return out


def _pmf_conv(pmfs: list[np.ndarray]) -> np.ndarray:
    out = pmfs[0]
    for p in pmfs[1:]:
        out = np.convolve(out, p)
    return out


def _quantile(pmf: np.ndarray, q: float) -> int:
    return int(np.searchsorted(np.cumsum(pmf), q - 1e-12))


def derived_markets(draws: np.ndarray) -> dict:
    """PRA / PR / PA / RA / STOCKS per draw and player, exactly from the same joint draws (sims x n x 7)."""
    return {name: draws[:, :, [SI[s] for s in parts]].astype(np.int64).sum(axis=2) for name, parts in COMBOS.items()}


def combo_scores(draws: np.ndarray, y: np.ndarray, lines: dict, sims: int) -> dict:
    """Per player and combo: log score, CRPS, Brier at the predefined lines (from the same draws)."""
    out = {}
    floor = PROB_FLOOR_DRAWS / sims
    n = draws.shape[1]
    der = derived_markets(draws)
    for name, parts in COMBOS.items():
        c = der[name]                                                                # sims x n
        yc = y[:, [SI[s] for s in parts]].sum(axis=1).astype(np.int64)
        ls, cr, floored = np.empty(n), np.empty(n), 0
        br = np.empty((n, len(COMBO_LINE_Q)))
        pov = np.empty((n, len(COMBO_LINE_Q)))
        for p in range(n):
            top = max(int(c[:, p].max()), int(yc[p])) + 1
            f = np.bincount(c[:, p], minlength=top)[:top] / sims
            pr = f[yc[p]]
            floored += int(pr < floor)
            ls[p] = -np.log(max(pr, floor))
            F = np.cumsum(f)
            cr[p] = float(((F - (np.arange(top) >= yc[p])) ** 2).sum())
            for li, line in enumerate(lines[name][p]):
                pov[p, li] = float((c[:, p] > line).mean())
                br[p, li] = (pov[p, li] - float(yc[p] > line)) ** 2
        out[name] = {"log": ls, "crps": cr, "brier": br, "p_over": pov, "floored": floored}
    return out


def single_scores(draws: np.ndarray, y: np.ndarray, sims: int) -> dict:
    """Per player and stat: MC log score and CRPS (marginal preservation: equal across candidates up to MC noise)."""
    floor = PROB_FLOOR_DRAWS / sims
    n = draws.shape[1]
    ls, cr = np.empty((n, NS)), np.empty((n, NS))
    for p in range(n):
        for j in range(NS):
            x = draws[:, p, j].astype(np.int64)
            top = max(int(x.max()), int(y[p, j])) + 1
            f = np.bincount(x, minlength=top)[:top] / sims
            ls[p, j] = -np.log(max(f[int(y[p, j])], floor))
            cr[p, j] = float(((np.cumsum(f) - (np.arange(top) >= y[p, j])) ** 2).sum())
    return {"log": ls, "crps": cr}


def marginal_probes(draws: np.ndarray, marg: dict) -> dict:
    """Per (player, stat): z of simulated mean / variance / P(X > line) at 3 lines / P(X = mode) vs the analytic PMF,
    and the KS distance (NBA validation.marginal_preservation, extended with the PMF cell and KS)."""
    sims, n, _ = draws.shape
    x = draws.astype(float)
    m_sim = x.mean(axis=0)
    v_sim = x.var(axis=0, ddof=1)
    mu4 = ((x - m_sim) ** 4).mean(axis=0)
    z_mean = (m_sim - marg["mean"]) / np.sqrt(np.maximum(marg["var"], 1e-12) / sims)
    z_var = (v_sim - marg["var"]) / np.sqrt(np.maximum(mu4 - v_sim ** 2, 1e-24) / sims)
    z_over, z_cell, ks = [], np.empty((n, NS)), np.empty((n, NS))
    for q in (0.25, 0.5, 0.75):
        k = (marg["cdf"] < q).sum(axis=2)                                             # analytic quantile
        p_over = 1 - np.take_along_axis(marg["cdf"], k[..., None], axis=2)[..., 0]
        s_over = (draws > k[None]).mean(axis=0)
        z_over.append((s_over - p_over) / np.sqrt(np.maximum(p_over * (1 - p_over), 1e-12) / sims))
    mode = marg["pmf"].argmax(axis=2)
    for p in range(n):
        for j in range(NS):
            f = np.bincount(draws[:, p, j].astype(np.int64), minlength=L.KMAX + 1)[:L.KMAX + 1] / sims
            pm = marg["pmf"][p, j, mode[p, j]]
            z_cell[p, j] = (f[mode[p, j]] - pm) / np.sqrt(max(pm * (1 - pm), 1e-12) / sims)
            ks[p, j] = float(np.abs(np.cumsum(f) - marg["cdf"][p, j]).max())
    return {"z_mean": z_mean, "z_var": z_var, "z_over": np.stack(z_over, axis=-1), "z_cell": z_cell, "ks": ks,
            "mean_err": m_sim - marg["mean"], "var_rel_err": (v_sim - marg["var"]) / np.maximum(marg["var"], 1e-12)}


def moment_sums(e: np.ndarray, team: np.ndarray) -> dict:
    """e: (sims x) n x 7 standardized count residuals. Sums over within-player stat pairs, same-team distinct-player
    pairs and cross-team pairs (ordered), averaged over draws when e has a draw axis."""
    if e.ndim == 2:
        e = e[None]
    own = np.einsum("dps,dpt->st", e, e) / len(e)
    T = [e[:, team == t].sum(axis=1) for t in (0, 1)]
    tt = [np.einsum("ds,dt->st", T[t], T[t]) / len(e) for t in (0, 1)]
    same = tt[0] + tt[1] - own
    x01 = np.einsum("ds,dt->st", T[0], T[1]) / len(e)
    return {"within_player": own, "same_team": same, "cross_team": x01 + x01.T}


# ============================================================================ predefined joint events
def _line(marg: dict, p: int, s: str, q: float) -> int:
    """Smallest k with F(k) >= q: 'over' = X > k (P <= 1 - q), 'low' = X <= k."""
    return int((marg["cdf"][p, SI[s]] < q).sum())


def roles(rows: pd.DataFrame, team: np.ndarray) -> dict:
    """Pregame per-team anchors by marginal mean rank (never by outcome)."""
    out = {}
    for t in (0, 1):
        idx = np.flatnonzero(team == t)
        r = rows.iloc[idx]

        def top(col, k=1, exclude=()):
            order = [idx[i] for i in np.argsort(-r[col].to_numpy(), kind="mergesort") if idx[i] not in exclude]
            return order[:k]
        t2 = top("PTS_mu", 2)
        a1 = top("AST_mu", 1)[0]
        out[t] = {"T1": t2[0], "T2": t2[1] if len(t2) > 1 else None, "A1": a1,
                  "SCORER_NOT_A1": top("PTS_mu", 1, exclude=(a1,))[0] if len(idx) > 1 else None,
                  "SHOOTER_NOT_A1": top("FG3M_mu", 1, exclude=(a1,))[0] if len(idx) > 1 else None,
                  "R1": top("REB_mu", 1)[0], "R2": (top("REB_mu", 2) + [None])[1], "S1": top("STL_mu", 1)[0],
                  "TOV1": top("TOV_mu", 1)[0]}
    return out


# event legs: (anchor, stat, kind) with kind in OVER50 (X > median), OVER80 (X > q80), LOW20 (X <= q20); anchors are
# per-team (own team = t, opponent = 1 - t) or ('a', ...) / ('b', ...) for the two teams of the game.
EVENTS = {
    # same player
    "SP_PTS_FG3M": [("T1", "PTS", "OVER50"), ("T1", "FG3M", "OVER50")],
    "SP_PTS_AST": [("T1", "PTS", "OVER50"), ("T1", "AST", "OVER50")],
    "SP_PTS_REB": [("R1", "PTS", "OVER50"), ("R1", "REB", "OVER50")],
    "SP_REB_BLK": [("R1", "REB", "OVER50"), ("R1", "BLK", "OVER50")],
    "SP_AST_TOV": [("A1", "AST", "OVER50"), ("A1", "TOV", "OVER50")],
    # teammates
    "TM_SCORER_SCORER": [("T1", "PTS", "OVER50"), ("T2", "PTS", "OVER50")],
    "TM_CREATOR_SCORER": [("A1", "AST", "OVER50"), ("SCORER_NOT_A1", "PTS", "OVER50")],
    "TM_CREATOR_SHOOTER": [("A1", "AST", "OVER50"), ("SHOOTER_NOT_A1", "FG3M", "OVER50")],
    "TM_REB_REB": [("R1", "REB", "OVER50"), ("R2", "REB", "OVER50")],
    "TM3_SCORERS_CREATOR": [("T1", "PTS", "OVER50"), ("T2", "PTS", "OVER50"), ("A1", "AST", "OVER50")],
    # opponents (own team t, opponent o)
    "OP_SCORER_SCORER": [("T1", "PTS", "OVER50"), ("o:T1", "PTS", "OVER50")],
    "OP_REB_REB": [("R1", "REB", "OVER50"), ("o:R1", "REB", "OVER50")],
    "OP_TOV_STL": [("TOV1", "TOV", "OVER50"), ("o:S1", "STL", "OVER50")],
    # joint tails
    "TAIL_SP_PTS_FG3M_HIGH": [("T1", "PTS", "OVER80"), ("T1", "FG3M", "OVER80")],
    "TAIL_SP_PTS_HIGH_FG3M_LOW": [("T1", "PTS", "OVER80"), ("T1", "FG3M", "LOW20")],
    "TAIL_TM_SCORERS_HIGH": [("T1", "PTS", "OVER80"), ("T2", "PTS", "OVER80")],
    "TAIL_TM_SCORER_HIGH_OTHER_LOW": [("T1", "PTS", "OVER80"), ("T2", "PTS", "LOW20")],
    "TAIL_OP_SCORERS_HIGH": [("T1", "PTS", "OVER80"), ("o:T1", "PTS", "OVER80")],
    "TAIL_OP_TOV_STL_HIGH": [("TOV1", "TOV", "OVER80"), ("o:S1", "STL", "OVER80")],
}
SYMMETRIC_OPPONENT = {"OP_SCORER_SCORER", "OP_REB_REB", "TAIL_OP_SCORERS_HIGH"}   # evaluated once per game (team 0)

# count-space pair diagnostics (Section 15): (anchor_a, stat_a, anchor_b, stat_b)
PAIRS = {
    "WP_T1_PTS_FG3M": ("T1", "PTS", "T1", "FG3M"), "WP_T1_PTS_AST": ("T1", "PTS", "T1", "AST"),
    "WP_R1_REB_BLK": ("R1", "REB", "R1", "BLK"), "WP_R1_REB_AST": ("R1", "REB", "R1", "AST"),
    "WP_A1_AST_TOV": ("A1", "AST", "A1", "TOV"), "WP_S1_STL_TOV": ("S1", "STL", "S1", "TOV"),
    "TM_SCORER_SCORER": ("T1", "PTS", "T2", "PTS"), "TM_CREATOR_SCORER": ("A1", "AST", "SCORER_NOT_A1", "PTS"),
    "TM_CREATOR_SHOOTER": ("A1", "AST", "SHOOTER_NOT_A1", "FG3M"), "TM_REB_REB": ("R1", "REB", "R2", "REB"),
    "OP_SCORER_SCORER": ("T1", "PTS", "o:T1", "PTS"), "OP_REB_REB": ("R1", "REB", "o:R1", "REB"),
    "OP_TOV_STL": ("TOV1", "TOV", "o:S1", "STL"), "OP_SCORER_REB": ("T1", "PTS", "o:R1", "REB"),
}


def _anchor(rl: dict, t: int, a: str):
    return rl[1 - t][a[2:]] if a.startswith("o:") else rl[t][a]


def joint_events(draws: dict, y: np.ndarray, marg: dict, rl: dict) -> list[dict]:
    q = {"OVER50": 0.5, "OVER80": 0.8, "LOW20": 0.2}
    out = []
    for ev, legs in EVENTS.items():
        for t in ((0,) if ev in SYMMETRIC_OPPONENT else (0, 1)):
            ps = [_anchor(rl, t, a) for a, _, _ in legs]
            keys = [(p, s) for p, (_, s, _) in zip(ps, legs)]
            if any(p is None for p in ps) or len(set(keys)) < len(keys):
                continue
            hit, ind = True, {}
            for (p, s), (_, _, kind) in zip(keys, legs):
                k = _line(marg, p, s, q[kind])
                hit &= (y[p, SI[s]] <= k) if kind == "LOW20" else (y[p, SI[s]] > k)
                for name, x in draws.items():
                    leg = (x[:, p, SI[s]] <= k) if kind == "LOW20" else (x[:, p, SI[s]] > k)
                    ind[name] = leg if name not in ind else ind[name] & leg
            out.append({"event": ev, "team": t, "outcome": int(hit), **{f"p_{k}": float(v.mean()) for k, v in ind.items()}})
    return out


def pair_products(draws: dict, y: np.ndarray, marg: dict, rl: dict) -> list[dict]:
    sd = np.sqrt(np.maximum(marg["var"], 1e-12))
    out = []
    for name, (aa, sa, ab, sb) in PAIRS.items():
        for t in (0, 1):
            pa, pb = _anchor(rl, t, aa), _anchor(rl, t, ab)
            if pa is None or pb is None or (pa == pb and sa == sb):
                continue
            ja, jb = SI[sa], SI[sb]
            ea = (y[pa, ja] - marg["mean"][pa, ja]) / sd[pa, ja]
            eb = (y[pb, jb] - marg["mean"][pb, jb]) / sd[pb, jb]
            rec = {"pair": name, "team": t, "obs": float(ea * eb)}
            for c, x in draws.items():
                xa = (x[:, pa, ja] - marg["mean"][pa, ja]) / sd[pa, ja]
                xb = (x[:, pb, jb] - marg["mean"][pb, jb]) / sd[pb, jb]
                rec[f"sim_{c}"] = float((xa * xb).mean())
            out.append(rec)
    return out


def combo_lines(marg: dict) -> dict:
    """Predefined half-integer-equivalent lines per player and combo: 'over' = X > k at the J0 convolution quantiles."""
    n = marg["pmf"].shape[0]
    out = {}
    for name, parts in COMBOS.items():
        out[name] = [[_quantile(_pmf_conv([marg["pmf"][p, SI[s]] for s in parts]), q) for q in COMBO_LINE_Q]
                     for p in range(n)]
    return out


def score_game(rows: pd.DataFrame, structures: dict, scale: np.ndarray, sims: int = SIMS, probe_arms=()) -> dict:
    """Simulate one game for every candidate from one shared bank and score every candidate on it.
    Monte Carlo replicates: ES on draw halves [0, sims/2) and [sims/2, sims); VS on draws [0, VS_DRAWS) and
    [VS_DRAWS, 2 VS_DRAWS). The reported ES uses all draws; the reported VS is the mean of its two replicates."""
    marg = game_marginals(rows)
    draws, info = simulate(rows, structures, sims, marg=marg)
    y = rows[[f"y_{s}" for s in STATS]].to_numpy(dtype=float)
    n = len(rows)
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    masks = pair_masks(n, team)
    lines = combo_lines(marg)
    ys = (y / scale).ravel()
    sd = np.sqrt(np.maximum(marg["var"], 1e-12))
    rl = roles(rows, team)
    h = sims // 2
    e_obs = (y - marg["mean"]) / sd
    res = {"game_id": rows["game_id"].iloc[0], "n_players": n, "info": info, "cand": {}, "e_obs": e_obs,
           "obs_moments": moment_sums(e_obs, team), "lines": lines}
    for name, x in draws.items():
        xs = (x.astype(float) / scale).reshape(sims, n * NS)
        vs_a, vs_b = variogram(xs[:VS_DRAWS], ys, masks), variogram(xs[VS_DRAWS:2 * VS_DRAWS], ys, masks)
        r = {"es": energy_score(xs, ys), "es_rep": (energy_score(xs[:h], ys), energy_score(xs[h:], ys)),
             "es_player": float(np.mean([energy_score(xs[:, p * NS:(p + 1) * NS], ys[p * NS:(p + 1) * NS])
                                         for p in range(n)])),
             "vs": {k: 0.5 * (vs_a[k] + vs_b[k]) for k in vs_a}, "vs_rep": (vs_a["balanced"], vs_b["balanced"]),
             "combo": combo_scores(x, y, lines, sims),
             "single": single_scores(x, y, sims),
             "sim_moments": moment_sums((x - marg["mean"]) / sd, team)}
        if name in probe_arms:
            r["probes"] = marginal_probes(x, marg)
        res["cand"][name] = r
    res["events"] = joint_events(draws, y, marg, rl)
    res["pairs"] = pair_products(draws, y, marg, rl)
    res["y_combo"] = {c: y[:, [SI[s] for s in parts]].sum(axis=1) for c, parts in COMBOS.items()}
    res["probe_expected"] = probe_support(marg, sims)
    return res


def probe_support(marg: dict, sims: int) -> dict:
    """Which probes have enough expected Monte Carlo events (>= 20) for a normal z (else excluded from z gates)."""
    p0 = marg["pmf"][..., 0]
    ok_moment = sims * np.minimum(1 - p0, p0 + 1e-300) >= 20
    over = []
    for q in (0.25, 0.5, 0.75):
        k = (marg["cdf"] < q).sum(axis=2)
        po = 1 - np.take_along_axis(marg["cdf"], k[..., None], axis=2)[..., 0]
        over.append(sims * np.minimum(po, 1 - po) >= 20)
    pm = marg["pmf"].max(axis=2)
    return {"moment": ok_moment, "over": np.stack(over, axis=-1), "cell": sims * np.minimum(pm, 1 - pm) >= 20}
