"""Player correction DV1: year-over-year development of returning players (docs/audits/2026-11-25-defects/DEFECT_PROTOCOL.json).
Promoted 2026-11-25.

Defect (current engine, chronological OOS): returning players' per-minute rates are under-forecast early in the season
and the gap decays as current-season minutes accumulate; players with no prior season are unbiased. 2025-26 days 0-30,
same team: PTS CORE 1.115 / ROTATION 1.092, TOV 1.104, AST 1.071, FG3M 1.057; REB / BLK / STL no pattern. Root cause:
prior-season rates enter the rate blend with no development (class-progression) term.

Fix (stat means):  mu_s <- mu_s * (1 + DELTA[s][same_team] * s_prev),
    s_prev = (.5 h1 + .25 h2 + .125 h3)_min / (cur + .5 h1 + .25 h2 + .125 h3 + 31)_min   (prior-season share of the blend)
DELTA = Poisson MLE on TRAIN 2024-25 only (actual-minutes counterfactual of the production configuration), all seven stats,
same team vs not; no stat selection. Frozen.

Single pre-declared evaluation (common random numbers vs the production configuration): stacked log loss 2025-26
-0.00055 (joint ALLB -0.00223), live 2026-27 -0.00055 (ub -0.00037); live graded prop events log loss -0.0071.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ID = "CBB_PLAYER_CORRECTION_DV1_RETURNING_DEVELOPMENT_V1"
W = (0.5, 0.25, 0.125)
K_MINUTES = 31.0
# stat -> (delta same team, delta otherwise)
DELTA = {
    "PTS": (0.062411744840117106, -0.03499763202010939),
    "REB": (-0.0020985946352850794, -0.008475137697019844),
    "AST": (0.050963266166055454, -0.010734819546429328),
    "FG3M": (0.04720156883113255, 0.005523752952692451),
    "STL": (0.026032927916131106, 0.03514637190418912),
    "BLK": (-0.016411572888479213, -0.011997529858833761),
    "TOV": (0.0265236084670283, -0.021242860037212378),
}
ENABLED = True


def enabled() -> bool:
    return bool(ENABLED)


def _col(df: pd.DataFrame, c: str) -> np.ndarray:
    return pd.to_numeric(df[c], errors="coerce").fillna(0).to_numpy(dtype=float) if c in df else np.zeros(len(df))


def prior_share(cand: pd.DataFrame) -> np.ndarray:
    h = sum(w * _col(cand, f"h{j}_min") for j, w in zip((1, 2, 3), W))
    return h / (_col(cand, "cur_min") + h + K_MINUTES)


def multipliers(cand: pd.DataFrame) -> dict:
    s = prior_share(cand)
    same = _col(cand, "pps_same_team") == 1
    return {st: 1.0 + np.where(same, a, b) * s for st, (a, b) in DELTA.items()}


def apply(rows: pd.DataFrame, cand: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """rows = model input rows (after prod.tov_trend); returns rows with the seven stat means scaled and a receipt."""
    if not enabled() or rows.empty:
        return rows, {"id": ID, "enabled": enabled(), "rows": 0}
    c = cand.set_index("player_game_example_id").loc[rows["player_game_example_id"].to_numpy()].reset_index()
    m = multipliers(c)
    out = rows.copy()
    for st, v in m.items():
        out[f"{st}_mu"] = out[f"{st}_mu"].to_numpy(dtype=float) * v
    return out, {"id": ID, "enabled": True, "rows": int(len(rows)),
                 "multiplier_range": {st: [float(v.min()), float(v.max())] for st, v in m.items()}}
