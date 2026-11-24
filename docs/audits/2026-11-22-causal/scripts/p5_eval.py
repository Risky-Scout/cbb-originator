"""Paired evaluation across replay files (common random numbers): p5_eval.py <ARM> <BASE> <parquet> [<parquet> ...]
Prints per-stat d log loss / d RPS with game-clustered 95% upper bounds, role / early cells, calibration by role,
and (where F_lo / F_hi exist) PIT 50% / 80% central coverage."""
import sys
import numpy as np, pandas as pd
arm, base, files = sys.argv[1], sys.argv[2], sys.argv[3:]
d = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
d = d[d.arm.isin([arm, base])].drop_duplicates(["arm", "game_id", "pgx", "stat"])
d["role"] = np.select([d.min_mu >= 30, d.min_mu >= 20, d.min_mu >= 10], ["S30", "R20", "B10"], "L<10")
key = ["game_id", "pgx", "stat"]
a = d[d.arm == arm].set_index(key); b = d[d.arm == base].set_index(key)
ix = a.index.intersection(b.index); a, b = a.loc[ix], b.loc[ix]
x = a[["y", "mean", "role", "early"]].copy(); x["dll"] = a.nll - b.nll; x["drps"] = a.rps - b.rps; x["mb"] = b["mean"]
x = x.reset_index()
def cl(v, g):
    s = pd.Series(v).groupby(g.to_numpy()).agg(["sum", "count"]); m = float(np.mean(v)); G = len(s)
    return m, float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))
tot = cl(x[x.stat.isin(["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"])].dll.to_numpy(), x[x.stat.isin(["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"])].game_id)
print(f"{arm} vs {base}: n {len(x)}  STACKED-7 dLL {tot[0]:+.5f} ub {tot[0] + 1.96 * tot[1]:+.5f}")
for S, g in x.groupby("stat"):
    m, se = cl(g.dll.to_numpy(), g.game_id); r, rse = cl(g.drps.to_numpy(), g.game_id)
    e = g[g.early]; em, ese = cl(e.dll.to_numpy(), e.game_id) if len(e) > 50 else (np.nan, np.nan)
    cells = " ".join(f"{k}:{h.y.sum() / h.mb.sum():.3f}>{h.y.sum() / h['mean'].sum():.3f}({cl(h.dll.to_numpy(), h.game_id)[0] + 1.96 * cl(h.dll.to_numpy(), h.game_id)[1]:+.4f})"
                     for k, h in g.groupby("role"))
    print(f"{S:6s} dLL {m:+.5f} ub {m + 1.96 * se:+.5f} | dRPS {r:+.5f} ub {r + 1.96 * rse:+.5f} | early ub {em + 1.96 * ese:+.5f} | {cells}")
