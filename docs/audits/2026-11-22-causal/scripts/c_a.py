"""Challenger A (CHALLENGER_A_PROTOCOL.json): role-aware per-minute rate. Fit on training folds, evaluate chronologically.
    .venv/bin/python -B docs/audits/2026-11-22-causal/scripts/c_a.py dev|holdout
dev: fit F2024 -> eval F2025.  holdout: fit F2024+F2025 -> eval F2026 (once)."""
import json, sys
import numpy as np, pandas as pd
from cbb_platform import config as C, props_v0 as P
D = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
OUTD = C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results"; OUTD.mkdir(parents=True, exist_ok=True)
S7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
W = np.array([0.5, 0.25, 0.125])
FLOOR = 0.01
mode = sys.argv[1]
A2 = len(sys.argv) > 2 and sys.argv[2] == "A2"     # revision A2: position-only prior for rows with no minutes history
train_f, eval_f = ([2024], 2025) if mode == "dev" else ([2024, 2025], 2026)
load = lambda F: pd.read_parquet(D / f"d1_F{F}.parquet")
tr = pd.concat([load(F) for F in train_f], ignore_index=True); ev = load(eval_f)

def prep(d, S):
    ok = d[f"y_{S}"].notna().to_numpy()
    x = d[ok]
    h = np.column_stack([x[f"{S}_h{j}"].fillna(0).to_numpy(float) for j in (1, 2, 3)])
    hm = np.column_stack([x[f"h{j}_min"].fillna(0).to_numpy(float) for j in (1, 2, 3)])
    mpa = x.h1_min.to_numpy(float) / np.maximum(x.h1_n_app.fillna(0).to_numpy(float), 1)
    Dr = np.where(x.h1_n_app.fillna(0).to_numpy() >= 5,
                  np.clip(np.log(np.maximum(x.min_mu.to_numpy(float), 1) / np.maximum(mpa, 1)), -1.5, 1.5), 0.0)
    return {"x": x, "cur": x[f"{S}_cur"].fillna(0).to_numpy(float), "cm": x.cur_min.fillna(0).to_numpy(float),
            "h": h, "hm": hm, "k": float(x[f"{S}_k"].iloc[0]), "P": x[f"{S}_prior"].to_numpy(float),
            "z": np.where((x.H_min.to_numpy(float) > 0) | (not A2), np.log(np.maximum(x.min_mu.to_numpy(float), 1) / 20.0), 0.0), "D": Dr,
            "ymin": x.y_min.to_numpy(float), "y": x[f"y_{S}"].to_numpy(float), "mm": x.min_mu.to_numpy(float),
            "phi": float(x[f"{S}_phi"].iloc[0]), "r0": x[f"{S}_rate"].to_numpy(float)}

def rate(a, th):
    al, be, ga = th
    num = a["cur"] + np.exp(be * a["D"]) * (a["h"] * (W * np.exp(al * np.arange(1, 4)))).sum(1) + a["k"] * a["P"] * np.exp(ga * a["z"])
    den = a["cm"] + (a["hm"] * W).sum(1) + a["k"]
    return num / den

def nll(a, r):
    e = a["ymin"] * r
    L = a["y"].sum() / e.sum()
    mu = np.maximum(L * e, 1e-12)
    return float((mu - a["y"] * np.log(mu)).sum()), L

def nelder_mead(f, x0, step=0.1, iters=400, tol=1e-9):
    n = len(x0); pts = [np.array(x0, float)] + [np.array(x0, float) + step * np.eye(n)[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        o = np.argsort(vals); pts = [pts[i] for i in o]; vals = [vals[i] for i in o]
        if abs(vals[-1] - vals[0]) < tol * (1 + abs(vals[0])): break
        c = np.mean(pts[:-1], 0); xr = c + (c - pts[-1]); fr = f(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1]); fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c); fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]; vals = [vals[0]] + [f(p) for p in pts[1:]]
    return pts[int(np.argmin(vals))]

def score(a, mu, line):
    y = a["y"].astype(int); kmax = int(y.max()) + 40
    M = P.pmf_matrix(mu, a["phi"], kmax)
    ll = -np.log(np.maximum(M[np.arange(len(y)), np.minimum(y, kmax)], 1e-300))
    cdf = np.cumsum(M[:, :kmax], 1)
    rps = ((cdf - (np.arange(kmax)[None, :] >= y[:, None])) ** 2).sum(1)
    pov = 1 - cdf[np.arange(len(y)), np.floor(line).astype(int)]
    return ll, rps, pov

def cluster(diff, games):
    g = pd.Series(diff).groupby(games.to_numpy()).agg(["sum", "count"])
    m = diff.mean(); G = len(g)
    se = np.sqrt(G / (G - 1) * ((g["sum"] - g["count"] * m) ** 2).sum()) / len(diff)
    return m, se

ARMS = {"A_dev": [True, False, False], "A_role": [True, True, False], "A_full": [True, True, True]}
out = {"mode": mode, "train": train_f, "eval": eval_f, "stats": {}}
for S in S7:
    at, ae = prep(tr, S), prep(ev, S)
    base_nll, L0 = nll(at, at["r0"])
    res = {"fit": {}, "eval": {}}
    mu0 = np.maximum(ae["mm"] * L0 * ae["r0"], FLOOR)
    line = np.floor(mu0) + 0.5
    ll0, rps0, pov0 = score(ae, mu0, line)
    over = (ae["y"] > line).astype(float)
    x = ae["x"]
    role = np.select([x.min_mu >= 30, x.min_mu >= 20, x.min_mu >= 10], ["STARTER_30P", "ROTATION_20_30", "BENCH_10_20"], "LOW_LT10")
    evid = pd.cut(x.cur_min.fillna(0) / x.H_min.replace(0, np.nan), [-0.01, 0.1, 0.25, 0.5, 0.75, 1.01],
                  labels=["cur<10%", "10-25%", "25-50%", "50-75%", "75%+"]).astype(object).fillna("NO_HISTORY").astype(str).to_numpy()
    early = np.where(x.season_day.to_numpy() <= 30, "D0_30", "D31P")
    transfer = np.where(x.prof_portal_transfer_in.fillna(0).to_numpy() > 0, "TRANSFER_IN", "NOT_TRANSFER")
    for arm, mask in ARMS.items():
        f = lambda th: nll(at, rate(at, np.where(mask, th, 0.0)))[0]
        th = np.where(mask, nelder_mead(f, [0.0, 0.0, 0.0]), 0.0)
        nl, L = nll(at, rate(at, th))
        mu = np.maximum(ae["mm"] * L * rate(ae, th), FLOOR)
        ll, rps, pov = score(ae, mu, line)
        m, se = cluster(ll - ll0, x.game_id)
        mr, ser = cluster(rps - rps0, x.game_id)
        cells = {}
        for name, lab in (("role", role), ("evidence", evid), ("early", early), ("transfer", transfer)):
            for v in np.unique(lab):
                i = lab == v
                dm, dse = cluster((ll - ll0)[i], x.game_id[i])
                cells[f"{name}|{v}"] = {"n": int(i.sum()), "d_logloss": dm, "d_logloss_ub": dm + 1.96 * dse,
                                        "ratio_incumbent": float(ae["y"][i].sum() / mu0[i].sum()),
                                        "ratio_challenger": float(ae["y"][i].sum() / mu[i].sum()),
                                        "mean_err_incumbent": float((mu0 - ae["y"])[i].mean()),
                                        "mean_err_challenger": float((mu - ae["y"])[i].mean())}
        res["fit"][arm] = {"alpha": th[0], "beta": th[1], "gamma": th[2], "L": L, "train_nll_gain": base_nll - nl}
        res["eval"][arm] = {"n": len(ll), "logloss_inc": ll0.mean(), "d_logloss": m, "d_logloss_se": se,
                            "d_rps": mr, "d_rps_se": ser, "brier_inc": float(((pov0 - over) ** 2).mean()),
                            "brier_ch": float(((pov - over) ** 2).mean()), "p_over_inc": float(pov0.mean()),
                            "p_over_ch": float(pov.mean()), "over_rate": float(over.mean()), "cells": cells}
    out["stats"][S] = res
    e = res["eval"]["A_full"]; ft = res["fit"]["A_full"]
    worst = max(e["cells"].items(), key=lambda kv: kv[1]["d_logloss_ub"])
    print(f"{S}: fit a={ft['alpha']:+.3f} b={ft['beta']:+.3f} g={ft['gamma']:+.3f} | dLL {e['d_logloss']:+.5f}+-{e['d_logloss_se']:.5f} "
          f"dRPS {e['d_rps']:+.5f}+-{e['d_rps_se']:.5f} | brier {e['brier_inc']:.4f}->{e['brier_ch']:.4f} | "
          f"worst cell {worst[0]} ub {worst[1]['d_logloss_ub']:+.5f} | ablation dLL dev {res['eval']['A_dev']['d_logloss']:+.5f} "
          f"role {res['eval']['A_role']['d_logloss']:+.5f}", flush=True)
    for c in ("role|STARTER_30P", "role|LOW_LT10", "role|BENCH_10_20", "evidence|cur<10%", "evidence|75%+", "early|D0_30"):
        v = e["cells"].get(c)
        if v: print(f"   {c:20s} n {v['n']:6d} ratio {v['ratio_incumbent']:.4f}->{v['ratio_challenger']:.4f} dLL {v['d_logloss']:+.5f}")
(OUTD / f"challenger_a{'2' if A2 else ''}_{mode}.json").write_text(json.dumps(out, indent=1, default=float))
