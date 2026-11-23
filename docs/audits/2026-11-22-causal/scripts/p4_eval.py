"""Gate evaluation of a p4_replay output against PHASE4_PROTOCOL.json.  p4_eval.py <replay parquet> <ARM> [BASE=INC]"""
import sys, json
import numpy as np, pandas as pd
f, arm = sys.argv[1], sys.argv[2]; base = sys.argv[3] if len(sys.argv) > 3 else "INC"
d = pd.read_parquet(f)
d["role"] = np.select([d.min_mu >= 30, d.min_mu >= 20, d.min_mu >= 10], ["S30", "R20", "B10"], "L<10")
key = ["game_id", "pgx", "stat"]
a = d[d.arm == arm].set_index(key); b = d[d.arm == base].set_index(key).loc[a.index]
x = a[["nll", "rps", "mean", "y", "role", "early", "cold"]].copy()
x["dll"] = a.nll - b.nll; x["drps"] = a.rps - b.rps; x["mean_b"] = b["mean"]
x = x.reset_index()
def cl(v, g):
    s = pd.Series(v).groupby(g.to_numpy()).agg(["sum", "count"]); m = float(np.mean(v)); G = len(s)
    return m, float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))
out = {}
for S, g in x.groupby("stat"):
    m, se = cl(g.dll.to_numpy(), g.game_id); mr, ser = cl(g.drps.to_numpy(), g.game_id)
    cells = {}
    for name in ("role", "early", "cold"):
        for v, h in g.groupby(name):
            dm, dse = cl(h.dll.to_numpy(), h.game_id)
            cells[f"{name}|{v}"] = {"n": len(h), "dll": dm, "ub": dm + 1.96 * dse,
                                   "ratio_base": h.y.sum() / h.mean_b.sum(), "ratio_arm": h.y.sum() / h["mean"].sum()}
    roles = [c for c in cells if c.startswith("role|")]
    out[S] = {"n": len(g), "nll_base": float(b.loc[b.index.get_level_values("stat") == S, "nll"].mean()), "dll": m, "ub": m + 1.96 * se,
              "drps": mr, "drps_ub": mr + 1.96 * ser,
              "max_role_err_base": max(abs(cells[c]["ratio_base"] - 1) for c in roles),
              "max_role_err_arm": max(abs(cells[c]["ratio_arm"] - 1) for c in roles), "cells": cells}
    o = out[S]
    print(f"{S:5s} n {o['n']:6d} dLL {m:+.5f} ub {o['ub']:+.5f} | dRPS {mr:+.5f} ub {o['drps_ub']:+.5f} | role err {o['max_role_err_base']:.3f} -> {o['max_role_err_arm']:.3f}")
    for c in sorted(cells):
        v = cells[c]
        if not c.startswith("cold|"):
            print(f"      {c:12s} n {v['n']:6d} ratio {v['ratio_base']:.3f} -> {v['ratio_arm']:.3f}  dLL {v['dll']:+.5f} ub {v['ub']:+.5f}")
json.dump(out, open(f.replace(".parquet", f".eval_{arm}_vs_{base}.json"), "w"), indent=1, default=float)
