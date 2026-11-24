"""Context ablation (PHASE4_PROTOCOL.json context_ablation, declared before metrics). ABL - PRODX (= P2 arm of p2final),
per stat and stacked base-7 log loss with game-clustered 95 % CI.   p12_ablation_eval.py <abl parquet> <p2final parquet>"""
import json
import sys

import numpy as np
import pandas as pd

BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]


def ci(v, g):
    v = np.asarray(v, float); s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"]); m = float(v.mean()); G = len(s)
    h = 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))
    return m, m - h, m + h


if __name__ == "__main__":
    k = ["game_id", "pgx", "stat"]
    b = pd.read_parquet(sys.argv[2]); b = b[b.arm == "P2"].set_index(k)
    a0 = pd.read_parquet(sys.argv[1])
    out = {}
    for arm in ("ENV0", "OPP0"):
        a = a0[a0.arm == arm].set_index(k); ix = a.index.intersection(b.index)
        d = pd.DataFrame({"d": a.loc[ix, "nll"].to_numpy() - b.loc[ix, "nll"].to_numpy()}, index=ix).reset_index()
        r = {"stacked7": ci(d[d.stat.isin(BASE7)].d, d[d.stat.isin(BASE7)].game_id)}
        for s, h in d.groupby("stat"):
            r[s] = ci(h.d, h.game_id)
        out[arm] = r
        print(f"== {arm} - PRODX  (dLL > 0: the ablated context helps)")
        for s, (m, lo, hi) in r.items():
            print(f"   {s:9s} {m:+.5f} [{lo:+.5f}, {hi:+.5f}]  {'USEFUL' if lo > 0 else ('HARMFUL' if hi < 0 else 'UNRESOLVED')}")
    json.dump(out, open(sys.argv[1].replace(".parquet", "_eval.json"), "w"), indent=1, default=float)
