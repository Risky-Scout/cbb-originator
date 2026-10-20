"""Prospective monitoring of the three KNOWN frozen-model warnings (jm1_final_production_manifest_2026_27.json
known_monitoring_warnings). Diagnostics ONLY: this module reads the ledger and writes reports; it has no path that
changes a forecast, a probability or an artifact (architect ruling: recorded, not repaired).

  A  PTS mean bias            confirmation 2025-26: JM1_FINAL +0.212, S0 +0.033 (projection - actual, PTS)
  B  high PRA / PR / PA lines confirmation q75-threshold Brier slightly worse than S0 (+0.0009 / +0.0008 / +0.0006)
  C  same-team dependence     confirmation correlation-calibration RMSE: JM1_FINAL 0.0161, S0 0.0051 (slope 0.37)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cbb_platform.prod import ledger as LG

REFERENCE = {
    "A_PTS_MEAN_BIAS": {"jm1_final": 0.212, "s0": 0.033, "unit": "points (projection - actual), conditional on playing"},
    "B_HIGH_COMBO_LINES": {"markets": ("PRA", "PR", "PA"), "rule": "line >= the player's q75 of the predicted pmf",
                           "confirmation_brier_delta_vs_s0": {"PRA": 0.0009, "PR": 0.0008, "PA": 0.0006}},
    "C_SAME_TEAM_CORRELATION": {"jm1_final_rmse": 0.0161, "s0_rmse": 0.0051, "slope": 0.37},
    "policy": "monitoring only; never adjusts a forecast",
}


def _settled(path) -> pd.DataFrame:
    p = pd.DataFrame(LG.predictions(path))
    s = pd.DataFrame(LG.settlements(path))
    if p.empty or s.empty:
        return pd.DataFrame()
    return p.merge(s, on="prediction_id", how="inner")


def pts_bias(path=LG.PC.LEDGER_PATH) -> dict:
    d = _settled(path)
    if d.empty:
        return {"n": 0, "status": "NO_SETTLED_ROWS", "reference": REFERENCE["A_PTS_MEAN_BIAS"]}
    d = d[(d["market"] == "PTS") & d["actual"].notna()].drop_duplicates(["game_id", "player_id"])
    if d.empty:
        return {"n": 0, "status": "NO_SETTLED_PTS", "reference": REFERENCE["A_PTS_MEAN_BIAS"]}
    e = d["projection"].astype(float) - d["actual"].astype(float)
    return {"n": int(len(e)), "mean_bias": float(e.mean()), "se": float(e.std(ddof=1) / np.sqrt(len(e))) if len(e) > 1 else None,
            "reference": REFERENCE["A_PTS_MEAN_BIAS"], "status": "TRACKING"}


def high_combo_brier(path=LG.PC.LEDGER_PATH) -> dict:
    d = _settled(path)
    out = {"reference": REFERENCE["B_HIGH_COMBO_LINES"], "by_market": {}}
    if d.empty:
        out["status"] = "NO_SETTLED_ROWS"
        return out
    d = d[d["market"].isin(REFERENCE["B_HIGH_COMBO_LINES"]["markets"]) & d["brier"].notna()
          & (d["line"].astype(float) >= d["pmf_q75"].astype(float))]
    for m, g in d.groupby("market"):
        out["by_market"][m] = {"n": int(len(g)), "mean_brier": float(g["brier"].astype(float).mean())}
    out["status"] = "TRACKING" if out["by_market"] else "NO_HIGH_LINE_ROWS"
    return out


def same_team_dependence(game_diagnostics: list[dict], actuals: dict) -> dict:
    """game_diagnostics: simulate.same_team_correlation records; actuals: {(game_id, player_id): value}. Compares the
    bank's predicted pairwise correlations with realized standardized pair products (needs many settled games)."""
    pred, real = [], []
    for g in game_diagnostics:
        for key, c in g.get("pair_corr", {}).items():
            a, b = key.split("|")
            ya, yb = actuals.get((g["game_id"], a)), actuals.get((g["game_id"], b))
            ma, mb = g.get("means", {}).get(a), g.get("means", {}).get(b)
            sa, sb = g.get("sds", {}).get(a), g.get("sds", {}).get(b)
            if None in (ya, yb, ma, mb, sa, sb) or not sa or not sb:
                continue
            pred.append(c)
            real.append((ya - ma) / sa * (yb - mb) / sb)
    if len(pred) < 30:
        return {"n_pairs": len(pred), "status": "INSUFFICIENT_SETTLED_PAIRS", "reference": REFERENCE["C_SAME_TEAM_CORRELATION"]}
    pred, real = np.array(pred), np.array(real)
    return {"n_pairs": int(len(pred)), "mean_predicted": float(pred.mean()), "mean_realized_product": float(real.mean()),
            "status": "TRACKING", "reference": REFERENCE["C_SAME_TEAM_CORRELATION"]}


def report(path=LG.PC.LEDGER_PATH, game_diagnostics=(), actuals=None) -> dict:
    return {"A_PTS_MEAN_BIAS": pts_bias(path), "B_HIGH_COMBO_LINES": high_combo_brier(path),
            "C_SAME_TEAM_CORRELATION": same_team_dependence(list(game_diagnostics), actuals or {}),
            "policy": REFERENCE["policy"]}
