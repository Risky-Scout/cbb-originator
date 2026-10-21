"""HIGH-EXPOSURE PTS BIAS ROOT-CAUSE AUDIT (architect 2026-10-21). DIAGNOSIS ONLY -- no model change, no resimulation,
no API call. Reads the same stored true-OOS evidence as directional_audit plus the Phase 1B OOS forecast store.

FROZEN JM1_FINAL MEAN (jm1_final.eval_game; structural.log_mu_terms / anchor; props_v0._predict_means):
    Phase 1B (props_v0, conditional on PLAYING):  mu_1B,s = max(min_mu * rate_s, 0.01)
        min_mu  = clip(ridge(pregame features), 1, 45)               (trained on PLAYED rows, y_min)
        rate_s  = (H_s + k_s * prior_s[pos]) / (H_min + k_s)         (recency-weighted history, half-life 1 season)
    JM1_FINAL, per draw b:  M_b = clip(min_mu + min_sigma eps_b, 0, 60) * D_b / E[D]
        mu_bs = mu_1B,s * exp(c0_s + beta_s log(max(M_b, 1) / min_mu) + pace / own / opp environment shocks)
        anchor alpha = 1.0 -> no pull back to mu_1B; reported mean = mean of the NB mixture over draws
        = E_b[mu_bs] (up to the 0.5% log-grid quantization and count support truncation).
    P(play) does not enter: scored rows are PLAYED rows and the minutes draw is not multiplied by P(play).

EXACT DECOMPOSITION (per row, sums exactly to forecast - actual):
    forecast - y = (mu_1B - y)                    PHASE 1B
                 + (mean_JM1 - mu_1B)             STRUCTURAL LAYER
    PHASE 1B, rows with y_min > 0 (y = y_min * r):  mu_1B - y = m^ r^ - m r with r^ = mu_1B / min_mu
        MINUTES = (m^ - m)(r^ + r) / 2,  RATE = (r^ - r)(m^ + m) / 2   (two-factor Shapley; exact)
    rows with y_min = 0 (rate unobserved): all of mu_1B - y is MINUTES (reported separately as ZERO_MINUTE rows)
    STRUCTURAL LAYER, sequential (order stated, exact sum):
        INTERCEPT = mu_1B (e^c0 - 1)
        EXPOSURE_JENSEN = mu_1B e^c0 (J - 1),  J = E_eps[(max(clip(min_mu + min_sigma eps, 0, 60), 1) / min_mu)^beta]
                          (dense-grid normal quadrature of the frozen minutes draw, duration factor D/E[D] excluded)
        REMAINDER = mean_JM1 - mu_1B e^c0 J      (environment shocks, duration, quantization, truncation)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cbb_platform import config as C
from cbb_platform.prod import directional_audit as DA

FOCUS = ("PTS", "AST", "FG3M", "TOV")
PHASE1B_STORE = "modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet"
STRUCT_PARAMS = {"2024-25": "modeling/player_props_v2/joint_minutes_v2_dev_parameters.json",          # structure T=2024
                 "2025-26": "modeling/player_props_v2/jm1_final_confirmation_parameters.json"}       # structure T=2025
MIN_FLOOR, MIN_KMAX = 1.0, 60.0
BANDS = {"MIN_LT10": (0, 10), "MIN_10_20": (10, 20), "MIN_20_30": (20, 30), "MIN_30PLUS": (30, np.inf),
         "LOW_LT20": (0, 20), "HIGH_GE30": (30, np.inf), "ALL": (-np.inf, np.inf)}
FOLD_STATES = {"2024-25": "modeling/player_props_v2/states/F2025_ANNUAL_REFIT_V1REF.json",
               "2025-26": "modeling/player_props_v2/states/F2026_ANNUAL_REFIT_V1REF.json"}
TARGET = {"PTS": "pts", "AST": "ast", "FG3M": "three_pm", "TOV": "tov"}
OUT_JSON = C.PROJECT_ROOT / "modeling" / "player_props_v2" / "jm1_final_high_exposure_root_cause.json"
GH_X = np.linspace(-8.0, 8.0, 3201)                         # dense normal grid (the clip / floor make the integrand
GH_W = np.exp(-0.5 * GH_X ** 2)                              # kinked, so Gauss-Hermite is avoided)
GH_W = GH_W / GH_W.sum()


def jensen(min_mu: np.ndarray, min_sigma: np.ndarray, beta: float) -> np.ndarray:
    """E_eps[(max(clip(mu + sigma eps, 0, 60), 1) / mu)^beta] by dense-grid normal quadrature (frozen draw law)."""
    m = np.clip(min_mu[:, None] + min_sigma[:, None] * GH_X[None, :], 0.0, MIN_KMAX)
    return (np.maximum(m, MIN_FLOOR) / min_mu[:, None]) ** beta @ GH_W


def load(season: str, root: Path | None = None) -> tuple[pd.DataFrame, dict]:
    root = Path(root or C.PROJECT_ROOT)
    src = DA.SOURCES[season]
    a = src["arm"]
    want = ["game_id", "player_id", "player_game_example_id", "min_mu", "min_sigma", "p_play", "y_min"]
    want += [f"y_{s}" for s in FOCUS] + [f"{a}|{s}|mean" for s in FOCUS] + [f"S0|{s}|mean" for s in FOCUS]
    want += [f"{a}|min_{k}" for k in ("mean", "sd", "pit_lo", "pit_hi", "q05", "q10", "q25", "q75", "q90", "q95")]
    want += [f"S0|min_{k}" for k in ("mean", "sd", "pit_lo", "pit_hi", "q10", "q90")]
    df = pq.read_table(root / src["path"], columns=want).to_pandas()
    df = df.rename(columns={c: c.split("|", 1)[1] for c in df.columns if c.startswith(a + "|")})
    store = pd.read_parquet(root / PHASE1B_STORE, columns=["player_game_example_id", "forecast_season", "min_mu"]
                            + [f"{s}_mu" for s in FOCUS], filters=[("forecast_season", "=", src["forecast_season"])])
    df = df.merge(store.rename(columns={"min_mu": "store_min_mu"}), on="player_game_example_id", how="left",
                  validate="one_to_one")
    params = json.loads((root / STRUCT_PARAMS[season]).read_text())["structure"]["glm"]["stats"]
    chk = {"rows": int(len(df)), "store_join_missing": int(df["PTS_mu"].isna().sum()),
           "min_mu_max_abs_diff_vs_store": float((df["min_mu"] - df["store_min_mu"]).abs().max()),
           "rows_y_min_zero": int((df["y_min"] <= 0).sum())}
    for s in FOCUS:
        chk[f"{s}_mean_floor_binding"] = int((df[f"{s}_mu"] <= 0.0100001).sum())
        chk[f"{s}_S0_mean_vs_mu1B_max_abs"] = float((df[f"S0|{s}|mean"] - df[f"{s}_mu"]).abs().max())
    return df, {"checks": chk, "coef": {s: params[s]["coef"] for s in FOCUS}}


def decompose(df: pd.DataFrame, s: str, coef: dict) -> pd.DataFrame:
    m_hat, m = df["min_mu"].to_numpy(float), df["y_min"].to_numpy(float)
    mu1b, y, f = df[f"{s}_mu"].to_numpy(float), df[f"y_{s}"].to_numpy(float), df[f"{s}|mean"].to_numpy(float)
    r_hat = mu1b / m_hat
    pos = m > 0
    r = np.where(pos, y / np.where(pos, m, 1.0), r_hat)
    minutes = np.where(pos, (m_hat - m) * (r_hat + r) / 2, mu1b - y)
    rate = np.where(pos, (r_hat - r) * (m_hat + m) / 2, 0.0)
    c0, beta = coef["intercept"], coef["log_minutes_ratio"]
    J = jensen(m_hat, df["min_sigma"].to_numpy(float), beta)
    inter = mu1b * (np.exp(c0) - 1)
    jen = mu1b * np.exp(c0) * (J - 1)
    rem = f - mu1b * np.exp(c0) * J
    return pd.DataFrame({"total": f - y, "phase1b": mu1b - y, "minutes": minutes, "rate": rate,
                         "structural": f - mu1b, "intercept": inter, "exposure_jensen": jen, "struct_remainder": rem,
                         "r_hat": r_hat, "r": r, "pos": pos, "J": J,
                         "J_realized": (np.maximum(m, MIN_FLOOR) / m_hat) ** beta, "mu1b": mu1b, "f": f, "y": y})


def _ratio(bt: DA.Boot, num: np.ndarray, den: np.ndarray, mask: np.ndarray) -> dict:
    n = np.bincount(bt.codes[mask], weights=num[mask], minlength=bt.G)
    d = np.bincount(bt.codes[mask], weights=den[mask], minlength=bt.G)
    bs = (bt.W @ n) / np.maximum(bt.W @ d, 1e-12)
    lo, hi = np.quantile(bs, (0.025, 0.975))
    return {"value": float(n.sum() / d.sum()), "lo": float(lo), "hi": float(hi)}


def minutes_block(df: pd.DataFrame, bt: DA.Boot, m: np.ndarray) -> dict:
    mh, y = df["min_mu"].to_numpy(float), df["y_min"].to_numpy(float)
    e = mh - y
    out = {"N": int(m.sum()), "mean_pred_conditional_minutes": float(mh[m].mean()),
           "mean_actual_minutes_played_rows": float(y[m].mean()), "signed_error": bt.mean(e, m),
           "median_error": float(np.median(e[m])), "MAE": float(np.abs(e[m]).mean()), "RMSE": float(np.sqrt((e[m] ** 2).mean())),
           "P_actual_gt_pred": float((y[m] > mh[m]).mean()), "P_actual_lt_pred": float((y[m] < mh[m]).mean()),
           "jm1_minutes_dist_mean_minus_actual": bt.mean(df["min_mean"].to_numpy(float) - y, m)}
    # distribution calibration of the frozen JM1 minutes draw (stored per-row PIT and quantiles)
    lo, hi = df["min_pit_lo"].to_numpy(float), df["min_pit_hi"].to_numpy(float)
    mid = 0.5 * (lo + hi)
    q = {k: df[f"min_{k}"].to_numpy(float) for k in ("q05", "q10", "q25", "q75", "q90", "q95")}
    sd = df["min_sd"].to_numpy(float)
    resid = df["min_mean"].to_numpy(float) - y
    out["distribution"] = {
        "mean_mid_pit": bt.mean(mid, m), "P_pit_gt_0.9": float(DA._p_above(lo, hi, 0.9)[m].mean()),
        "P_pit_lt_0.1": float(1 - DA._p_above(lo, hi, 0.1)[m].mean()),
        "P_y_gt_q90": float((y[m] > q["q90"][m]).mean()), "P_y_lt_q10": float((y[m] < q["q10"][m]).mean()),
        "P_y_gt_q75": float((y[m] > q["q75"][m]).mean()), "P_y_lt_q25": float((y[m] < q["q25"][m]).mean()),
        "coverage_q10_q90": float(((y[m] >= q["q10"][m]) & (y[m] <= q["q90"][m])).mean()),
        "coverage_q05_q95": float(((y[m] >= q["q05"][m]) & (y[m] <= q["q95"][m])).mean()),
        "mean_pred_sd": float(sd[m].mean()), "rms_pred_sd": float(np.sqrt((sd[m] ** 2).mean())),
        "rmse_of_dist_mean": float(np.sqrt((resid[m] ** 2).mean())),
        "spread_ratio_rmse_over_rms_sd": float(np.sqrt((resid[m] ** 2).mean()) / np.sqrt((sd[m] ** 2).mean()))}
    return out


def stat_block(df: pd.DataFrame, s: str, dc: pd.DataFrame, bt: DA.Boot, m: np.ndarray) -> dict:
    mins = df["y_min"].to_numpy(float)
    p = m & dc["pos"].to_numpy()
    out = {"N": int(m.sum()), "N_played_minutes_gt0": int(p.sum())}
    for k in ("total", "phase1b", "minutes", "rate", "structural", "intercept", "exposure_jensen", "struct_remainder"):
        out[k] = bt.mean(dc[k].to_numpy(float), m)
    tot = out["total"]["value"]
    out["share_of_total"] = {k: (out[k]["value"] / tot if abs(tot) > 1e-9 else None)
                             for k in ("minutes", "rate", "structural")}
    out["sum_check"] = float(out["minutes"]["value"] + out["rate"]["value"] + out["structural"]["value"] - tot)
    out["struct_sum_check"] = float(out["intercept"]["value"] + out["exposure_jensen"]["value"]
                                    + out["struct_remainder"]["value"] - out["structural"]["value"])
    # minute-weighted rates (played rows with minutes > 0): predicted r^ vs realized sum(y) / sum(minutes)
    pr = _ratio(bt, dc["r_hat"].to_numpy(float) * mins, mins, p)
    ar = _ratio(bt, dc["y"].to_numpy(float), mins, p)
    d = _ratio(bt, (dc["r_hat"].to_numpy(float) - dc["r"].to_numpy(float)) * mins, mins, p)
    out["rate"] = out["rate"] | {}
    out["per_minute"] = {"pred_rate": pr, "actual_rate": ar, "signed_rate_error": d,
                         "relative_rate_error": d["value"] / ar["value"],
                         "relative_rate_error_ci": [d["lo"] / ar["value"], d["hi"] / ar["value"]]}
    out["mean_J"] = float(dc["J"].to_numpy(float)[m].mean())
    out["mean_J_realized_minutes"] = float(dc["J_realized"].to_numpy(float)[m].mean())   # same functional, realized y_min
    out["structural_ratio_mean_f_over_mu1b"] = float(dc["f"].to_numpy(float)[m].sum() / dc["mu1b"].to_numpy(float)[m].sum())
    return out


def history_inputs(season: str, root: Path | None = None) -> pd.DataFrame:
    """The frozen Phase 1B rate inputs of every candidate row of the season (marginal.load_frames, verbatim SQL;
    read-only). Labels are not used here."""
    from cbb_platform.v2 import marginal as MG
    root = Path(root or C.PROJECT_ROOT)
    cfg = json.loads((root / "modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json").read_text())["config"]
    F = DA.SOURCES[season]["forecast_season"]
    fr, _ = MG.load_frames(cfg, range(F - cfg["recency"]["prior_seasons_used"], F + 1), "candidates")
    fr = fr[fr["season"] == F]
    keep = ["player_game_example_id", "pos", "cur_min"] + [f"h{j}_min" for j in (1, 2, 3)]
    keep += [f"cur_{t}" for t in TARGET.values()] + [f"h{j}_{t}" for j in (1, 2, 3) for t in TARGET.values()]
    return fr[keep], cfg


def rate_trace(df: pd.DataFrame, dc: pd.DataFrame, hist: pd.DataFrame, cfg: dict, state: dict, s: str) -> pd.DataFrame:
    """Exact split of the per-minute rate error r^ - r (rows with minutes > 0), in POINTS at the Shapley rate weight
    (m^ + m) / 2, so the three parts sum to the RATE contribution:
        r^ = (1 - w) r_hist + w prior_pos,  w = k / (H_min + k)        (props_v0._predict_means, reproduced)
        PRIOR_SHRINKAGE = w (prior_pos - r_hist)
        PRIOR_SEASON_BLEND = r_hist - r_cur   (r_cur = current-season-to-date rate; = r_hist when no current minutes)
        CURRENT_RATE_VS_REALIZED = r_cur - r
    r_hist undefined (H_min = 0): r^ = prior_pos, all of r^ - r_cur... is PRIOR_SHRINKAGE with r_hist := r_cur := r^."""
    t = TARGET[s]
    h = df[["player_game_example_id"]].merge(hist, on="player_game_example_id", how="left", validate="one_to_one")
    hl = cfg["recency"]["half_life_seasons"]
    wj = [0.5 ** (j / hl) for j in (1, 2, 3)]
    Hm = h["cur_min"].fillna(0).to_numpy(float) + sum(w * h[f"h{j}_min"].fillna(0).to_numpy(float) for j, w in zip((1, 2, 3), wj))
    Hs = h[f"cur_{t}"].fillna(0).to_numpy(float) + sum(w * h[f"h{j}_{t}"].fillna(0).to_numpy(float) for j, w in zip((1, 2, 3), wj))
    k = state["prior_k_minutes"][t]
    prior = h["pos"].map(state["prior_rate"][t]).to_numpy(float)
    r_rep = (Hs + k * prior) / (Hm + k)
    r_hat = dc["r_hat"].to_numpy(float)
    has = Hm > 0
    r_hist = np.where(has, Hs / np.where(has, Hm, 1), r_rep)
    cm = h["cur_min"].fillna(0).to_numpy(float)
    r_cur = np.where(cm > 0, h[f"cur_{t}"].fillna(0).to_numpy(float) / np.where(cm > 0, cm, 1), r_hist)
    wgt = (df["min_mu"].to_numpy(float) + df["y_min"].to_numpy(float)) / 2
    pos = dc["pos"].to_numpy()
    r = dc["r"].to_numpy(float)
    out = pd.DataFrame({"prior_shrinkage": np.where(pos, (r_rep - r_hist) * wgt, 0.0),
                        "prior_season_blend": np.where(pos, (r_hist - r_cur) * wgt, 0.0),
                        "current_rate_vs_realized": np.where(pos, (r_cur - r) * wgt, 0.0),
                        "reproduction_gap": np.where(pos, (r_hat - r_rep) * wgt, 0.0),
                        "w_prior": k / (Hm + k), "cur_share_of_history_minutes": np.where(Hm > 0, cm / np.maximum(Hm, 1e-9), 0.0),
                        "no_history": ~has, "no_current": cm <= 0})
    out.attrs["max_abs_rate_reproduction_error"] = float(np.nanmax(np.abs(r_hat - r_rep)))
    return out


def run(root: Path | None = None, write: bool = True) -> dict:
    res = {"stats": {s: {} for s in FOCUS}, "minutes": {}, "checks": {}, "coef": {}}
    for season in DA.SOURCES:
        df, meta = load(season, root)
        res["checks"][season], res["coef"][season] = meta["checks"], meta["coef"]
        bt = DA.Boot(df["game_id"].to_numpy())
        hist, cfg = history_inputs(season, root)
        state = json.loads((Path(root or C.PROJECT_ROOT) / FOLD_STATES[season]).read_text())
        state = state.get("v0_state", state)
        mu = df["min_mu"].to_numpy(float)
        masks = {b: (mu >= lo) & (mu < hi) for b, (lo, hi) in BANDS.items()}
        res["minutes"][season] = {b: minutes_block(df, bt, mk) for b, mk in masks.items()}
        for s in FOCUS:
            dc = decompose(df, s, meta["coef"][s])
            res["stats"][s][season] = {b: stat_block(df, s, dc, bt, mk) for b, mk in masks.items()}
            tr = rate_trace(df, dc, hist, cfg, state, s)
            res["checks"][season][f"{s}_max_abs_rate_reproduction_error"] = tr.attrs["max_abs_rate_reproduction_error"]
            for b, mk in masks.items():
                blk = {k: bt.mean(tr[k].to_numpy(float), mk) for k in
                       ("prior_shrinkage", "prior_season_blend", "current_rate_vs_realized", "reproduction_gap")}
                blk["mean_w_prior"] = float(tr["w_prior"].to_numpy(float)[mk].mean())
                blk["mean_current_share_of_history_minutes"] = float(tr["cur_share_of_history_minutes"].to_numpy(float)[mk].mean())
                blk["share_no_history"] = float(tr["no_history"].to_numpy()[mk].mean())
                blk["share_no_current_season_minutes"] = float(tr["no_current"].to_numpy()[mk].mean())
                res["stats"][s][season][b]["rate_trace"] = blk
        del df
    out = {"audit": "JM1_FINAL high-exposure PTS bias root cause (diagnostic only; stored true-OOS; no model change)",
           "construction": __doc__, "sign_convention": "predicted - actual (> 0 OVER-prediction)",
           "bands": {k: list(v) for k, v in BANDS.items()}, "results": res,
           "historical_resimulation": False, "api_calls": 0, "model_changed": False}
    if write:
        OUT_JSON.write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    run()
