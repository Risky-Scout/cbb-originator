"""PS2 (ROOT_CAUSE_PROTOCOL.json): TRAIN 2024-25 played rows -> per min_mu band empirical quantiles (99 knots) of
z = (y_min - min_mu) / min_sigma. -> data/qa/large/rootcause_20261126/ps2_qmap.json"""
import json, os
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
f = pd.read_parquet(os.path.join(ROOT, "data/qa/large/causal_20261122/d1_F2025.parquet"), columns=["min_mu", "min_sigma", "y_min"])
f = f[(f.y_min.fillna(0) > 0) & f.min_mu.notna() & (f.min_sigma > 0)]
z = (f.y_min - f.min_mu) / f.min_sigma
EDGES = [0, 10, 20, 28, 1e9]
U = np.linspace(0.01, 0.99, 99)
out = {"edges": EDGES[:-1], "u": U.round(4).tolist(), "bands": []}
for lo, hi in zip(EDGES[:-1], EDGES[1:]):
    zz = z[(f.min_mu >= lo) & (f.min_mu < hi)]
    out["bands"].append({"lo": lo, "n": int(len(zz)), "q": np.quantile(zz, U).round(5).tolist(),
                         "mean": float(zz.mean()), "sd": float(zz.std())})
json.dump(out, open(os.path.join(ROOT, "data/qa/large/rootcause_20261126/ps2_qmap.json"), "w"), indent=1)
for b in out["bands"]:
    print(b["lo"], b["n"], "mean %.3f sd %.3f" % (b["mean"], b["sd"]), "q05 %.2f q50 %.2f q95 %.2f" % (b["q"][4], b["q"][49], b["q"][94]))
