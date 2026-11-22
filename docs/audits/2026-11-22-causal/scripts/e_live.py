"""A2 PRIMARY (prospective) holdout: the 2026-27 season through 2026-11-21 (production walk-forward training rows,
frame hash = the 11-22 production model's training manifest). Incumbent = the OPENING marginal (through 2025-26) that
produced these OOS forecasts; A2 = frozen (alpha, beta, gamma, L) from the F2024+F2025 fit. Never fitted on 2026-27."""
import json
import numpy as np, pandas as pd
from cbb_platform import config as C, props_v0 as P
R = C.PROJECT_ROOT
D = R / "data/qa/large/causal_20261122"
TGT = {"PTS": "pts", "REB": "reb", "AST": "ast", "FG3M": "three_pm", "STL": "stl", "BLK": "blk", "TOV": "tov"}
W = np.array([0.5, 0.25, 0.125])
fit = json.loads((R / "docs/audits/2026-11-22-causal/results/challenger_a2_holdout.json").read_text())
fr = pd.read_parquet(D / "live/frame_2027.parquet"); oos = pd.read_parquet(D / "live/oos_2027.parquet")
st = json.loads((D / "live/opening_v0_state.json").read_text())
d = fr.merge(oos.drop(columns=[c for c in oos.columns if c.startswith("y_")] + ["cold_start_class", "is_home", "prediction_cutoff_utc"]), on="player_game_example_id", how="inner",
             validate="one_to_one")
d = d[d.y_min > 0].reset_index(drop=True)
tr = pd.concat([pd.read_parquet(D / f"d1_F{F}.parquet") for F in (2024, 2025)], ignore_index=True)
ts = pd.to_datetime(d.prediction_cutoff_utc, utc=True); d["season_day"] = (ts - ts.min()).dt.days
Hm = d.cur_min.fillna(0).to_numpy(float) + sum(w * d[f"h{j}_min"].fillna(0).to_numpy(float) for j, w in zip((1, 2, 3), W))
mpa = d.h1_min.to_numpy(float) / np.maximum(d.h1_n_app.fillna(0).to_numpy(float), 1)
Dr = np.where(d.h1_n_app.fillna(0).to_numpy() >= 5, np.clip(np.log(np.maximum(d.min_mu.to_numpy(float), 1) / np.maximum(mpa, 1)), -1.5, 1.5), 0.0)
z = np.where(Hm > 0, np.log(np.maximum(d.min_mu.to_numpy(float), 1) / 20.0), 0.0)
role = np.select([d.min_mu >= 30, d.min_mu >= 20, d.min_mu >= 10], ["STARTER_30P", "ROTATION_20_30", "BENCH_10_20"], "LOW_LT10")
cm = d.cur_min.fillna(0).to_numpy(float)
evid = pd.cut(pd.Series(np.where(Hm > 0, cm / np.where(Hm > 0, Hm, 1), np.nan)), [-0.01, 0.1, 0.25, 0.5, 0.75, 1.01],
              labels=["cur<10%", "10-25%", "25-50%", "50-75%", "75%+"]).astype(object).fillna("NO_HISTORY").astype(str).to_numpy()
early = np.where(d.season_day.to_numpy() <= 14, "D0_14", "D15P")
out = {"rows": int(len(d)), "games": int(d.game_id.nunique()), "stats": {}}
def cl(diff, g):
    s = pd.Series(diff).groupby(g).agg(["sum", "count"]); m = diff.mean(); G = len(s)
    return m, np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(diff)
for S, t in TGT.items():
    y = d[f"y_{t}"].to_numpy(float); ok = ~np.isnan(y)
    k = st["prior_k_minutes"][t]; prior = d["pos"].map(st["prior_rate"][t]).to_numpy(float)
    h = np.column_stack([d[f"h{j}_{t}"].fillna(0).to_numpy(float) for j in (1, 2, 3)])
    cur = d[f"cur_{t}"].fillna(0).to_numpy(float)
    r0 = (cur + (h * W).sum(1) + k * prior) / (Hm + k)
    mu_store = d[f"{S}_mu"].to_numpy(float)
    rep = float(np.nanmax(np.abs(np.maximum(d.min_mu.to_numpy(float) * r0, 0.01) - mu_store)))
    f = fit["stats"][S]["fit"]["A_full"]; al, be, ga, LA = f["alpha"], f["beta"], f["gamma"], f["L"]
    rA = (cur + np.exp(be * Dr) * (h * (W * np.exp(al * np.arange(1, 4)))).sum(1) + k * prior * np.exp(ga * z)) / (Hm + k)
    # incumbent training level factor on the same training rows (protocol level control)
    ytr = tr[f"y_{S}"].to_numpy(float); okt = ~np.isnan(ytr)
    L0 = ytr[okt].sum() / (tr.y_min.to_numpy(float)[okt] * tr[f"{S}_rate"].to_numpy(float)[okt]).sum()
    mu0 = np.maximum(d.min_mu.to_numpy(float) * L0 * r0, 0.01)[ok]; muA = np.maximum(d.min_mu.to_numpy(float) * LA * rA, 0.01)[ok]
    muraw = mu_store[ok]                                   # the production (opening) 1B mean, no level factor
    yy = y[ok].astype(int); kmax = int(yy.max()) + 40; phi = float(d[f"{S}_phi"].iloc[0])
    def sc(mu):
        M = P.pmf_matrix(mu, phi, kmax); ll = -np.log(np.maximum(M[np.arange(len(yy)), np.minimum(yy, kmax)], 1e-300))
        cdf = np.cumsum(M[:, :kmax], 1); rps = ((cdf - (np.arange(kmax)[None, :] >= yy[:, None])) ** 2).sum(1)
        return ll, rps, cdf
    ll0, rps0, c0 = sc(mu0); llA, rpsA, cA = sc(muA); llR, _, _ = sc(muraw)
    line = np.floor(mu0) + 0.5; idx = np.floor(line).astype(int); over = (y[ok] > line).astype(float)
    p0 = 1 - c0[np.arange(len(yy)), idx]; pA = 1 - cA[np.arange(len(yy)), idx]
    g = d.game_id.to_numpy()[ok]
    m, se = cl(llA - ll0, g); mr, ser = cl(rpsA - rps0, g)
    cells = {}
    for name, lab in (("role", role[ok]), ("evidence", evid[ok]), ("early", early[ok])):
        for v in np.unique(lab):
            i = lab == v; dm, dse = cl((llA - ll0)[i], g[i])
            cells[f"{name}|{v}"] = {"n": int(i.sum()), "d_logloss": dm, "d_logloss_ub": dm + 1.96 * dse,
                                    "ratio_incumbent": float(y[ok][i].sum() / mu0[i].sum()), "ratio_a2": float(y[ok][i].sum() / muA[i].sum()),
                                    "ratio_production_raw": float(y[ok][i].sum() / muraw[i].sum())}
    out["stats"][S] = {"n": int(ok.sum()), "rate_reproduction_max_abs_err": rep, "L0": L0, "LA": LA,
                       "logloss_inc": ll0.mean(), "logloss_prod_raw": llR.mean(), "d_logloss": m, "d_logloss_se": se,
                       "d_rps": mr, "d_rps_se": ser, "brier_inc": float(((p0 - over) ** 2).mean()), "brier_a2": float(((pA - over) ** 2).mean()),
                       "cells": cells}
    print(f"{S}: n {ok.sum()} rep_err {rep:.2e} | dLL {m:+.5f}+-{se:.5f} dRPS {mr:+.5f}+-{ser:.5f} | brier {out['stats'][S]['brier_inc']:.4f}->{out['stats'][S]['brier_a2']:.4f}")
    for c in sorted(cells):
        v = cells[c]
        print(f"   {c:22s} n {v['n']:5d} ratio prod {v['ratio_production_raw']:.3f} inc {v['ratio_incumbent']:.3f} -> A2 {v['ratio_a2']:.3f}  dLL {v['d_logloss']:+.5f} ub {v['d_logloss_ub']:+.5f}")
(R / "docs/audits/2026-11-22-causal/results/challenger_a2_live_2026_27.json").write_text(json.dumps(out, indent=1, default=float))
