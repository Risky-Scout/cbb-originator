"""RESEARCH ONLY (Step 2/3, 2026-11-25): production game model vs CBBD market on identical games.
Input g1_<tag>.parquet. Market margin = -spread (home line), market total = overUnder, market team score home =
(T + M)/2, away = (T - M)/2; ML market = proportional de-vig. Differences are model - market loss (negative = model
better) with a 95% CI from a date-block bootstrap (2,000 reps). Class: MODEL BETTER / MARKET BETTER / STATISTICALLY
INDISTINGUISHABLE / INSUFFICIENT SAMPLE (n < 300). Encompassing: OLS y = a + b_model*model + b_mkt*market (diagnostic of
D = model - market, never used to blend). Single process."""
import json
import sys

import numpy as np
import pandas as pd

from cbb_platform import config as C

D = C.PROJECT_ROOT / "data/qa/large/game_20261125"
tag = sys.argv[1] if len(sys.argv) > 1 else "base"
s = pd.read_parquet(D / f"g1_{tag}.parquet")
s["date"] = pd.to_datetime(s.game_date_et).dt.date
s["phase"] = pd.cut(s.ngp, [-1, 3, 8, 15, 99], labels=["ngp0-3", "ngp4-8", "ngp9-15", "ngp16+"]).astype(str)
rng = np.random.default_rng(20261125)


def boot(df, f):
    pt = f(df)
    days = df.date.unique()
    g = {d: h for d, h in df.groupby("date")}
    reps = [f(pd.concat([g[d] for d in rng.choice(days, len(days))])) for _ in range(2000)]
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return float(pt), float(lo), float(hi)


def cls(n, lo, hi):
    if n < 300:
        return "INSUFFICIENT SAMPLE"
    return "MODEL BETTER" if hi < 0 else "MARKET BETTER" if lo > 0 else "STATISTICALLY INDISTINGUISHABLE"


def mse_diff(y, a, b):
    return lambda d: float(((d[y] - d[a]) ** 2).mean() - ((d[y] - d[b]) ** 2).mean())


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def compare(h, which):
    sp, tt = ("close_spread", "close_total") if which == "close" else ("spread_open", "total_open")
    o = h.dropna(subset=[sp, tt]).copy()
    o["mk_m"], o["mk_t"] = -o[sp], o[tt]
    o["mk_h"], o["mk_a"] = (o.mk_t + o.mk_m) / 2, (o.mk_t - o.mk_m) / 2
    o["y_h"], o["y_a"] = (o.y_t + o.y_m) / 2, (o.y_t - o.y_m) / 2
    out = {"n": int(len(o))}
    if len(o) < 30:
        return out
    for name, y, a, b in (("margin_mse", "y_m", "pm", "mk_m"), ("total_mse", "y_t", "pt", "mk_t"),
                          ("home_score_mse", "y_h", "exp_home", "mk_h"), ("away_score_mse", "y_a", "exp_away", "mk_a")):
        r = boot(o, mse_diff(y, a, b))
        out[name] = {"model": float(((o[y] - o[a]) ** 2).mean()), "market": float(((o[y] - o[b]) ** 2).mean()),
                     "diff": r, "class": cls(len(o), r[1], r[2])}
    for name, y, a, b in (("margin", "y_m", "pm", "mk_m"), ("total", "y_t", "pt", "mk_t")):
        X = np.c_[np.ones(len(o)), o[a], o[b]]
        beta = np.linalg.lstsq(X, o[y].to_numpy(float), rcond=None)[0]
        out[f"encompass_{name}"] = {"b_model": float(beta[1]), "b_market": float(beta[2]),
                                    "D_sd": float((o[a] - o[b]).std()), "corr_D_resid": float(np.corrcoef(o[a] - o[b], o[y] - o[b])[0, 1])}
    if which == "close":
        m = o.dropna(subset=["p_mkt_home"])
        m = m.assign(yh=(m.y_m > 0).astype(float))
        r = boot(m, lambda d: float((ll(d.p_home, d.yh) - ll(d.p_mkt_home, d.yh)).mean()))
        out["moneyline_logloss"] = {"n": int(len(m)), "model": float(ll(m.p_home, m.yh).mean()), "market": float(ll(m.p_mkt_home, m.yh).mean()),
                                    "diff": r, "class": cls(len(m), r[1], r[2])}
        for k, p, yv, push in (("spread_ats", "p_cover", "y_cover", "push_ats"), ("total_ou", "p_over", "y_over", "push_ou")):
            q = o[~o[push].astype(bool)].dropna(subset=[p, yv])
            r = boot(q, lambda d: float((ll(d[p], d[yv]) - ll(np.full(len(d), .5), d[yv])).mean()))
            out[k] = {"n": int(len(q)), "model_logloss": float(ll(q[p], q[yv]).mean()), "market_logloss": float(np.log(2)),
                      "diff": r, "class": cls(len(q), r[1], r[2]), "model_side_hit": float(((q[p] > .5) == (q[yv] > .5)).mean())}
    return out


R = {}
for season, h in s.groupby("season"):
    R[int(season)] = {"close": compare(h, "close"), "open": compare(h, "open"),
                      "by_phase_close": {ph: compare(g, "close") for ph, g in h.groupby("phase")}}
json.dump(R, open(C.PROJECT_ROOT / f"docs/audits/2026-11-25-game/results/g2_vs_market_{tag}.json", "w"), indent=1, default=float)
for season, r in R.items():
    for w in ("open", "close"):
        x = r[w]
        if "margin_mse" not in x:
            print(season, w, "n", x["n"]); continue
        print(season, w, "n", x["n"], " ".join(f"{k}:{x[k]['class'][:12]}({x[k]['diff'][0]:+.2f})" for k in x if isinstance(x[k], dict) and "class" in x[k]),
              "| enc margin b_model %.2f b_mkt %.2f" % (x["encompass_margin"]["b_model"], x["encompass_margin"]["b_market"]),
              "total b_model %.2f b_mkt %.2f" % (x["encompass_total"]["b_model"], x["encompass_total"]["b_market"]))
    for ph, x in r["by_phase_close"].items():
        if "margin_mse" in x:
            print("   ", ph, x["n"], "margin", f"{x['margin_mse']['diff'][0]:+.2f}", x["margin_mse"]["class"][:12], "total", f"{x['total_mse']['diff'][0]:+.2f}", x["total_mse"]["class"][:12],
                  "enc b_model", round(x["encompass_margin"]["b_model"], 2))
