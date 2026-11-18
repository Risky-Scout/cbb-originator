"""RESEARCH ONLY: GameJointPMF validation over walk-forward predictions (backtest.walk_season output + `ngp`).

Per game: build the production PMF (model.targets -> model.build), then score
  margin / total: MAE, RMSE (PMF means), CRPS (discrete), PIT coverage 50/80/95
  ML: Brier, log loss, calibration deciles (P(home win))
  vs CBBD closing line (one provider per game): ATS P(home cover) and O/U P(over) Brier / log loss / hit rate,
      model-vs-close disagreement, and the market's own RMSE as the benchmark; ML vs the no-vig close.
  coherence: markets.coherence on every PMF (COHERENT must be 100%).
"""
from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from cbb_platform.game import markets as GM, model as GMod, params as GP


def _crps(vals, p, y):
    c = np.cumsum(p)
    return float(((c - (vals >= y)) ** 2).sum())


def _pit_interval(vals, p, y, q):
    c = np.cumsum(p)
    lo = vals[np.searchsorted(c, (1 - q) / 2)]
    hi = vals[min(np.searchsorted(c, 1 - (1 - q) / 2), len(vals) - 1)]
    return lo <= y <= hi


def score_rows(args):
    rows, vp = args
    eot = GP.endgame_ot()
    v = GMod.VarianceParams(**vp)
    out = []
    for r in rows:
        t = GMod.targets(r["exp_home"], r["exp_away"], r["ngp"], v)
        g = GMod.build(t, eot)
        d = GM.Dist(g)
        mv, mp = g.margin()
        tv, tp = g.total()
        y_m = r["home_pts"] - r["away_pts"]
        y_t = r["home_pts"] + r["away_pts"]
        coh = GM.coherence(g)
        rec = {"game_id": r["game_id"], "season": r["season"], "y_m": y_m, "y_t": y_t,
               "pm": float(np.dot(mv, mp)), "pt": float(np.dot(tv, tp)), "crps_m": _crps(mv, mp, y_m),
               "crps_t": _crps(tv, tp, y_t), "p_home": d.p_gt("M", 0), "coherent": coh["COHERENT"],
               **{f"in{int(q * 100)}_m": _pit_interval(mv, mp, y_m, q) for q in (0.5, 0.8, 0.95)},
               **{f"in{int(q * 100)}_t": _pit_interval(tv, tp, y_t, q) for q in (0.5, 0.8, 0.95)}}
        sp, tot = r.get("spread"), r.get("total")
        if sp is not None and not (isinstance(sp, float) and math.isnan(sp)):
            w, l, u = GM.side_probs(d, "SPREAD", "HOME", float(sp))
            rec.update(close_spread=float(sp), p_cover=GM.model_prob(w, u), push_ats=(y_m + sp == 0),
                       y_cover=float(y_m + sp > 0))
        if tot is not None and not (isinstance(tot, float) and math.isnan(tot)):
            w, l, u = GM.side_probs(d, "GAME_TOTAL", "OVER", float(tot))
            rec.update(close_total=float(tot), p_over=GM.model_prob(w, u), push_ou=(y_t == tot), y_over=float(y_t > tot))
        out.append(rec)
    return out


def american_to_decimal(a):
    if a is None or (isinstance(a, float) and math.isnan(a)) or a == 0:
        return None
    return 1 + (a / 100 if a > 0 else 100 / -a)


def summarize(s: pd.DataFrame) -> dict:
    def bl(p, y):
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return {"n": int(len(y)), "brier": float(((p - y) ** 2).mean()),
                "log_loss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())}
    y_hw = (s.y_m > 0).astype(float)
    out = {"n": int(len(s)), "coherent_rate": float(s.coherent.mean()),
           "margin": {"mae": float((s.y_m - s.pm).abs().mean()), "rmse": float(np.sqrt(((s.y_m - s.pm) ** 2).mean())),
                      "crps": float(s.crps_m.mean()), "bias": float((s.y_m - s.pm).mean()),
                      **{k: float(s[f"{k}_m"].mean()) for k in ("in50", "in80", "in95")}},
           "total": {"mae": float((s.y_t - s.pt).abs().mean()), "rmse": float(np.sqrt(((s.y_t - s.pt) ** 2).mean())),
                     "crps": float(s.crps_t.mean()), "bias": float((s.y_t - s.pt).mean()),
                     **{k: float(s[f"{k}_t"].mean()) for k in ("in50", "in80", "in95")}},
           "moneyline": bl(s.p_home.to_numpy(), y_hw.to_numpy())}
    dec = pd.cut(s.p_home, np.linspace(0, 1, 11), include_lowest=True)
    out["moneyline"]["calibration"] = [{"bin": str(k), "n": int(len(g)), "p": float(g.p_home.mean()),
                                        "y": float((g.y_m > 0).mean())} for k, g in s.groupby(dec, observed=True)]
    if "close_spread" in s:
        a = s[s.close_spread.notna() & ~s.push_ats.fillna(False).astype(bool)]
        mk = -a.close_spread                                            # market expected margin
        out["ats"] = {**bl(a.p_cover.to_numpy(), a.y_cover.to_numpy()),
                      "hit_rate_model_side": float(((a.p_cover > 0.5) == (a.y_cover == 1)).mean()),
                      "hit_rate_when_model_disagrees_2pts": float(((a.p_cover > 0.5) == (a.y_cover == 1))[(a.pm - mk).abs() >= 2].mean()),
                      "n_disagree_2pts": int(((a.pm - mk).abs() >= 2).sum()),
                      "market_margin_rmse": float(np.sqrt(((a.y_m - mk) ** 2).mean())),
                      "model_margin_rmse_same_games": float(np.sqrt(((a.y_m - a.pm) ** 2).mean())),
                      "mean_abs_model_minus_close": float((a.pm - mk).abs().mean())}
        o = s[s.close_total.notna() & ~s.push_ou.fillna(False).astype(bool)]
        out["ou"] = {**bl(o.p_over.to_numpy(), o.y_over.to_numpy()),
                     "hit_rate_model_side": float(((o.p_over > 0.5) == (o.y_over == 1)).mean()),
                     "market_total_rmse": float(np.sqrt(((o.y_t - o.close_total) ** 2).mean())),
                     "model_total_rmse_same_games": float(np.sqrt(((o.y_t - o.pt) ** 2).mean())),
                     "mean_abs_model_minus_close": float((o.pt - o.close_total).abs().mean())}
    if "p_mkt_home" in s:
        m = s[s.p_mkt_home.notna()]
        y = (m.y_m > 0).astype(float).to_numpy()
        out["ml_vs_close"] = {"model": bl(m.p_home.to_numpy(), y), "market_novig": bl(m.p_mkt_home.to_numpy(), y)}
    return out


def run(preds: pd.DataFrame, vp: dict, lines: pd.DataFrame | None, workers: int = 8) -> pd.DataFrame:
    df = preds.copy()
    if lines is not None:
        ln = lines.dropna(subset=["game_id"]).drop_duplicates("game_id")[["game_id", "spread", "total", "hml", "aml"]]
        df = df.merge(ln, on="game_id", how="left")
    recs = df.to_dict(orient="records")
    chunks = [(recs[i::workers], vp) for i in range(workers)]
    with ProcessPoolExecutor(workers) as ex:
        res = [r for part in ex.map(score_rows, chunks) for r in part]
    s = pd.DataFrame(res)
    if lines is not None:
        s = s.merge(df[["game_id", "hml", "aml", "home", "away", "conference_game", "neutral", "ngp"]], on="game_id")
        dh = s.hml.map(american_to_decimal)
        da = s.aml.map(american_to_decimal)
        s["p_mkt_home"] = [GM.no_vig(a, b) if a and b else None for a, b in zip(dh, da)]
        s["p_mkt_home"] = s["p_mkt_home"].astype(float)
    return s


if __name__ == "__main__":
    preds_p, lines_p, out_p = sys.argv[1:4]
    gm = GP.game_model()
    preds = pd.read_parquet(preds_p)
    lines = pd.read_parquet(lines_p)
    res = {}
    allp = []
    for season in (2024, 2025, 2026):
        s = run(preds[preds.season == season], gm["variance"], lines)
        allp.append(s)
        res[str(season)] = summarize(s)
        print(season, json.dumps({k: v for k, v in res[str(season)].items() if k != "moneyline"}, default=str)[:1500],
              flush=True)
    pd.concat(allp).to_parquet(out_p.replace(".json", ".parquet"))
    json.dump(res, open(out_p, "w"), indent=1, default=str)
