"""Player correction T1: TOV within-season trend (docs/audits/2026-11-22-causal/PHASE4_PROTOCOL.json,
tov_blk_reevaluation.challenger_T1_tov_season_trend). Promoted 2026-11-24.

Defect: turnover rates fall through every season while the frozen TOV mean is flat in time. Marginal realized /
forecast TOV, Nov -> Mar: 2023-24 .987 -> .929, 2024-25 1.008 -> .929, 2025-26 1.034 -> .927; live 2026-27 ISO weeks
45 / 46 / 47: 1.073 / 1.060 / 1.033 (WARM players too: not cold start, not shrinkage).

Fix (TOV mean only):  TOV_mu <- TOV_mu * exp(B * (u - UBAR)),  u = days from Nov 1 of the season to the row's prediction
cutoff (America/New_York) / 100. B = Poisson GLM slope (offset log TOV_mu) on 2023-24 + 2024-25 played rows; UBAR = the
mean u of those rows (season-average multiplier 1). Frozen; no 2025-26 or 2026-27 data used in the fit.

Single pre-declared evaluation on the production configuration (A2 + P2, common random numbers): TOV log loss
2025-26 -0.00076 (ub -0.00056), live 2026-27 -0.00368 (ub -0.00288); every other stat bit-identical.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ID = "CBB_PLAYER_CORRECTION_T1_TOV_SEASON_TREND_V1"
B = -0.07673824529357458
UBAR = 0.7213194232871556
ENABLED = True


def enabled() -> bool:
    return bool(ENABLED)


def season_u(cutoff_utc) -> np.ndarray:
    ts = pd.to_datetime(pd.Series(np.asarray(cutoff_utc)), utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    yr = ts.dt.year.where(ts.dt.month >= 7, ts.dt.year - 1)
    return (ts - pd.to_datetime(yr.astype(str) + "-11-01")).dt.days.to_numpy(float) / 100.0


def multiplier(cutoff_utc) -> np.ndarray:
    return np.exp(B * (season_u(cutoff_utc) - UBAR))


def apply(rows: pd.DataFrame, cand: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """rows = model input rows (after prod.rate_a2); returns rows with TOV_mu scaled and an audit receipt."""
    if not enabled() or rows.empty:
        return rows, {"id": ID, "enabled": enabled(), "rows": 0}
    c = cand.set_index("player_game_example_id").loc[rows["player_game_example_id"].to_numpy()]
    m = multiplier(c["prediction_cutoff_utc"].to_numpy())
    out = rows.copy()
    out["TOV_mu"] = out["TOV_mu"].to_numpy(dtype=float) * m
    return out, {"id": ID, "enabled": True, "rows": int(len(rows)), "b": B, "ubar": UBAR,
                 "multiplier_min": float(m.min()), "multiplier_max": float(m.max())}
