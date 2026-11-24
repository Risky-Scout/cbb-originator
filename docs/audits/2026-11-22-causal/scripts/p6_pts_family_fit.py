"""Challenger P2 (PTS compound family) TRAIN fit: event-value shares (p1, p2, p3) of PTS = sum_k V_k, N ~ NB, fitted by
maximum likelihood on 2023-24 + 2024-25 box scores (minutes > 0) at bin-matched moments (realized-minutes bins), the
NB(N) dispersion solved so the compound has exactly the target mean and variance. No 2025-26 / 2026-27 data is read."""
import json, sys
from math import lgamma
import duckdb, numpy as np, pandas as pd
from cbb_platform import config as C
K = 90
LG = np.array([lgamma(i + 1) for i in range(K)])
def nb(m, v):
    k = np.arange(K)
    if v <= m * (1 + 1e-6):
        return np.exp(k * np.log(m) - m - LG)
    r = m * m / (v - m); p = r / (r + m)
    return np.exp(np.array([lgamma(i + r) for i in k]) - lgamma(r) - LG + r * np.log(p) + k * np.log1p(-p))
def compound(m, v, pv):
    vals = np.array([1, 2, 3]); vb = (pv * vals).sum(); e2 = (pv * vals ** 2).sum(); mN = m / vb
    varN = (v - mN * (e2 - vb ** 2)) / vb ** 2
    pN = nb(mN, max(varN, mN)); out = np.zeros(K); cur = np.zeros(K); cur[0] = 1.0; step = np.zeros(K); step[1:4] = pv
    for n in range(K):
        if pN[n] < 1e-15 and n > mN: break
        out += pN[n] * cur; cur = np.convolve(cur, step)[:K]
    return out
if __name__ == "__main__":
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    d = con.execute("select minutes, points from fact.player_games where minutes>0 and season in (2024,2025) and points is not null").fetchdf()
    d["mb"] = pd.cut(d.minutes, [1, 3, 5, 8, 11, 14, 17, 20, 24, 28, 32, 45], right=False)
    cells = [(h.points.to_numpy(), np.bincount(np.minimum(h.points.to_numpy(), K - 1), minlength=K)) for _, h in d.groupby("mb", observed=True)]
    def total_ll(pv):
        s = 0.0
        for y, cnt in cells:
            f = compound(y.mean(), y.var(), pv); s += float((cnt * np.log(np.maximum(f, 1e-15))).sum())
        return s
    best = None
    for p1 in np.arange(0.05, 0.40, 0.01):
        for p3 in np.arange(0.10, 0.36, 0.01):
            if p1 + p3 >= 0.95: continue
            pv = np.array([p1, 1 - p1 - p3, p3]); ll = total_ll(pv)
            if best is None or ll > best[0]: best = (ll, p1, p3)
    ll, p1, p3 = best
    n = sum(len(y) for y, _ in cells)
    nb_ll = sum(float((cnt * np.log(np.maximum(nb(y.mean(), y.var()), 1e-15))).sum()) for y, cnt in cells)
    box = total_ll(np.array([0.344, 0.464, 0.192]))
    out = {"train_seasons": [2024, 2025], "rows": n, "grid_step": 0.01, "p1": round(p1, 2), "p2": round(1 - p1 - p3, 2), "p3": round(p3, 2),
           "mean_ll": {"NB_matched": nb_ll / n, "compound_box_shares": box / n, "compound_fit": ll / n}}
    print(json.dumps(out, indent=1))
    json.dump(out, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/p2_pts_family_train_fit.json", "w"), indent=1)
