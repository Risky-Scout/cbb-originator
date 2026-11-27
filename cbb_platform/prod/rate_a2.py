"""Player Challenger A2 (role-aware per-minute rate), promoted 2026-11-22 for AST, FG3M and STL; REB added 2026-11-24.

Defect (docs/audits/2026-11-22-causal): the Phase 1B rate blend carries prior-season history forward unprojected (no
season-to-season development, no role-change adjustment) and shrinks toward a position-only prior (no role). Realized /
model rate was 1.07-1.15 where the blend leans on last season and 0.86-0.95 where it leans on the current season, and
0.81-0.89 for low-minute players. Minutes are not the cause (A1: bias <= 0.4 min in every role, SD ratio ~1).

    rate_t = (cur_t + exp(beta_t * D) * sum_j w_j exp(alpha_t * j) h_jt + k_t prior_pos_t exp(gamma_t * z)) / (H_min + k_t)
    mu_t   = max(min_mu * rate_t * SCALE_t, mean_floor)

w_j = 0.5 ** j (the incumbent recency weights), k_t / prior_pos_t = the daily marginal state (unchanged), D = clipped log
role change vs last season's minutes per appearance (>= 5 appearances, else 0), z = log(min_mu / 20) for players with
any minutes history (0 = the incumbent position prior for a first appearance). SCALE_t = L_A2 / L_incumbent: the
training-level factors of both arms (F2024 + F2025), so the JM1 structural intercept / mean anchor keep the level they
were fitted at and only the geometry changes. Constants were fitted on F2024 + F2025 only
(CHALLENGER_A_PROTOCOL.json); evidence: F2026 holdout + the 2026-27 season through 2026-11-21 (never fitted on).

Applied in the SCORING path only (prod.run, after inputs.marginal_rows). The daily walk-forward fit, its dispersion and
the structural GLM training rows are unchanged (that is the configuration that was evaluated). PTS, REB, BLK and TOV are
NOT adjusted: they failed a pre-declared criterion (PTS live early-season log loss; REB / BLK aggregate upper bound;
TOV everywhere), so they keep the incumbent rate.

2026-11-24 (PHASE4_PROTOCOL.json, production-scorer re-test, common random numbers): REB passes G1-G4 on the
PRODUCTION scorer (F2026 d log loss -0.00031, ub -0.00019; LIVE -0.00091, ub -0.00047; early ub -0.00032; max role
error .057 -> .048) and is promoted with its frozen F2024 + F2025 constants. AST / FG3M / STL re-confirmed. PTS fails G3
(live early-season ub +0.00138), BLK fails G1 (F2026 ub +0.00012), TOV fails G1 (LIVE +0.00255): incumbent rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ID = "CBB_PLAYER_CHALLENGER_A2_ROLE_AWARE_RATE_V1"
W = np.array([0.5, 0.25, 0.125])
D_CLIP = 1.5
D_MIN_APPEARANCES = 5
Z_REF_MINUTES = 20.0
# stat -> (target key, alpha, beta, gamma, SCALE)   fitted F2024 + F2025 (docs/audits/2026-11-22-causal/results)
PARAMS = {
    "AST": ("ast", 0.06990694168849601, 0.10985553123445585, 0.306375232970297, 0.9629945862889774),
    "FG3M": ("three_pm", 0.07120316621231922, 0.03688378479751936, 0.24144762614925558, 0.9657138734373469),
    "STL": ("stl", 0.08097776987423, -0.007646288823820966, 0.021172416258187172, 0.9673960453383759),
    "REB": ("reb", 0.028409245183987564, 0.03249293210071952, 0.02648832272897604, 0.9853249554045149),
}
NOT_ADJUSTED = ("PTS", "BLK", "TOV")


# F4 (Stage 07): column -> rows defaulted to 0 (absent column = every row, recorded as ABSENT); reset per apply()
DEFAULTED: dict = {}


def _col(df: pd.DataFrame, c: str) -> np.ndarray:
    if c not in df:
        DEFAULTED[c] = "ABSENT"
        return np.zeros(len(df))
    x = df[c]
    n = int(x.isna().sum())
    if n and DEFAULTED.get(c) != "ABSENT":
        DEFAULTED[c] = DEFAULTED.get(c, 0) + n
    return x.fillna(0).to_numpy(dtype=float)


def rates(cand: pd.DataFrame, min_mu: np.ndarray, v0_state: dict) -> dict:
    """stat -> A2 per-minute rate (before SCALE), on the candidate frame (rows aligned with min_mu)."""
    mm = np.asarray(min_mu, dtype=float)
    hm = np.column_stack([_col(cand, f"h{j}_min") for j in (1, 2, 3)])
    H = _col(cand, "cur_min") + (hm * W).sum(1)
    n1 = _col(cand, "h1_n_app")
    mpa = _col(cand, "h1_min") / np.maximum(n1, 1)
    D = np.where(n1 >= D_MIN_APPEARANCES,
                 np.clip(np.log(np.maximum(mm, 1) / np.maximum(mpa, 1)), -D_CLIP, D_CLIP), 0.0)
    z = np.where(H > 0, np.log(np.maximum(mm, 1) / Z_REF_MINUTES), 0.0)
    out = {}
    for s, (t, al, be, ga, _) in PARAMS.items():
        k = float(v0_state["prior_k_minutes"][t])
        prior = cand["pos"].map(v0_state["prior_rate"][t]).to_numpy(dtype=float)
        h = np.column_stack([_col(cand, f"h{j}_{t}") for j in (1, 2, 3)])
        num = _col(cand, f"cur_{t}") + np.exp(be * D) * (h * (W * np.exp(al * np.arange(1, 4)))).sum(1) \
            + k * prior * np.exp(ga * z)
        out[s] = num / (H + k)
    return out


def apply(rows: pd.DataFrame, cand: pd.DataFrame, cfg: dict, v0_state: dict) -> tuple[pd.DataFrame, dict]:
    """rows = inputs.marginal_rows output; returns rows with the PARAMS stats' means replaced and an audit receipt."""
    if rows.empty:
        return rows, {"id": ID, "stats": list(PARAMS), "rows": 0}
    c = cand.set_index("player_game_example_id").loc[rows["player_game_example_id"].to_numpy()].reset_index()
    mm = rows["min_mu"].to_numpy(dtype=float)
    DEFAULTED.clear()
    r = rates(c, mm, v0_state)
    out = rows.copy()
    rec = {"id": ID, "stats": list(PARAMS), "not_adjusted": list(NOT_ADJUSTED), "rows": int(len(rows)), "mean_ratio": {},
           "defaulted_to_0": dict(DEFAULTED)}
    for s, (_, _, _, _, scale) in PARAMS.items():
        new = np.maximum(mm * r[s] * scale, cfg["mean_floor"])
        old = out[f"{s}_mu"].to_numpy(dtype=float)
        rec["mean_ratio"][s] = float(new.sum() / old.sum()) if old.sum() > 0 else None
        out[f"{s}_mu"] = new
    return out, rec
