"""TRAIN-only fits for challengers S1 (STL within-season trend) and D1 (per stat x cold-class dispersion), 2026-11-25.
Written and run before any F2026 / LIVE metric of S1 / D1 (PHASE4_PROTOCOL.json challenger_S1_D1_C1).

S1: Poisson GLM slope of y_STL on u (days since Nov 1 / 100) with offset log STL_mu, played rows of d1_F2024 + d1_F2025
    (exactly the T1 recipe); ubar = mean u.
D1: sigma_{s,c}^2 = log(1 + (r_{s,c} - 1) * sum var / sum mean^2) per base stat s and cold class c on the TRAIN 2024-25
    production-configuration replay (p5_F2025_train3, arm PRODX3 = A2 + P2 + T1); r = realized / forecast variance;
    sigma = 0 where r <= 1 and for WARM (target cold populations only)."""
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import tov_trend as TT

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]

d = pd.concat([pd.read_parquet(Q / f"d1_{s}.parquet", columns=["prediction_cutoff_utc", "y_min", "STL_mu", "y_STL"]) for s in ("F2024", "F2025")])
d = d[(d.y_min > 0) & np.isfinite(d.STL_mu) & np.isfinite(d.y_STL) & (d.STL_mu > 0)]
u = TT.season_u(d.prediction_cutoff_utc.to_numpy())
ubar = float(u.mean())
x, y, off = u - ubar, d.y_STL.to_numpy(float), np.log(d.STL_mu.to_numpy(float))
a, b = 0.0, 0.0
for _ in range(50):                                   # Newton, Poisson with offset: log E y = off + a + b x
    m = np.exp(off + a + b * x)
    g = np.array([(y - m).sum(), ((y - m) * x).sum()])
    H = np.array([[m.sum(), (m * x).sum()], [(m * x).sum(), (m * x * x).sum()]])
    step = np.linalg.solve(H, g)
    a, b = a + step[0], b + step[1]
    if np.abs(step).max() < 1e-12:
        break
se_b = float(np.sqrt(np.linalg.inv(H)[1, 1]))
s1 = {"b": float(b), "se_b": se_b, "a_level_not_used": float(a), "ubar": ubar, "n": int(len(d))}

r = pd.read_parquet(Q / "p4/p5_F2025_train3.parquet", columns=["stat", "y", "mean", "var", "cold"])
r = r[r.stat.isin(BASE7)]
sig, rat = {}, {}
for (s, c), h in r.groupby(["stat", "cold"]):
    rr = float(((h.y - h["mean"]) ** 2).sum() / h["var"].sum())
    rat.setdefault(c, {})[s] = {"r": rr, "n": int(len(h))}
    if c != "WARM" and rr > 1:
        sig.setdefault(c, {})[s] = float(np.sqrt(np.log(1 + (rr - 1) * h["var"].sum() / (h["mean"] ** 2).sum())))
    elif c != "WARM":
        sig.setdefault(c, {})[s] = 0.0
out = {"S1": s1, "D1_sigma": sig, "D1_train_var_ratio": rat}
print(json.dumps(out, indent=1))
json.dump(out, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/p16_s1_d1_train_fit.json", "w"), indent=1)
