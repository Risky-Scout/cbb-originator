"""GAME_CHALLENGER_PROTOCOL amendment_GC3 gates (written before any GC3 metric). Usage: g6_gc3_eval.py GC3p GC3l [GC3pl]"""
import json, sys
import numpy as np, pandas as pd
from cbb_platform import config as C
D = C.PROJECT_ROOT / "data/qa/large/game_20261125"
rng = np.random.default_rng(20261125)
def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6); return -(y * np.log(p) + (1 - y) * np.log(1 - p))
def boot(v, dates):
    v = pd.Series(np.asarray(v, float)).groupby(np.asarray(dates)).agg(["sum", "count"])
    S, N = v["sum"].to_numpy(), v["count"].to_numpy(); idx = rng.integers(0, len(S), (2000, len(S)))
    r = S[idx].sum(1) / N[idx].sum(1); return float(S.sum() / N.sum()), *map(float, np.percentile(r, [2.5, 97.5]))
b = pd.read_parquet(D / "g1_base.parquet").set_index("game_id")
out = {}
for t in sys.argv[1:]:
    c = pd.read_parquet(D / f"g1_{t}.parquet").set_index("game_id"); ix = b.index.intersection(c.index); B, X = b.loc[ix], c.loc[ix]
    r = {}
    for season in sorted(B.season.unique()):
        m = (B.season == season).to_numpy(); bb, xx = B[m], X[m]; dt = pd.to_datetime(bb.game_date_et).dt.date; yh = (bb.y_m > 0).astype(float)
        big = (bb.close_spread.abs() >= 12).to_numpy()
        r[int(season)] = {"margin": boot((xx.y_m - xx.pm) ** 2 - (bb.y_m - bb.pm) ** 2, dt), "total": boot((xx.y_t - xx.pt) ** 2 - (bb.y_t - bb.pt) ** 2, dt),
                          "ml": boot(ll(xx.p_home, yh) - ll(bb.p_home, yh), dt),
                          "margin_12plus": float((((xx.y_m - xx.pm) ** 2 - (bb.y_m - bb.pm) ** 2)[big]).mean()) if big.any() else None,
                          "total_155plus": float((((xx.y_t - xx.pt) ** 2 - (bb.y_t - bb.pt) ** 2)[(bb.pt >= 155).to_numpy()]).mean())}
    g = {"T1": all(r[s]["total"][2] < 0 for s in (2024, 2025)), "T2": all(r[s]["margin"][2] <= 0.5 for s in (2024, 2025)),
         "T3": r[2027]["total"][0] <= 0 and r[2027]["margin"][0] <= 1.0, "T4": all(r[s]["ml"][2] <= 0.001 for s in (2024, 2025)),
         "T5": all(r[s]["margin_12plus"] <= 0.5 for s in (2024, 2025))}
    g["DECISION"] = "PASS" if all(g.values()) else "FAIL"
    out[t] = {"results": r, "gates": g}
    print(t, json.dumps(g)); [print("  ", s, {k: (tuple(round(x, 3) for x in v) if isinstance(v, tuple) else (round(v, 3) if v is not None else None)) for k, v in rr.items()}) for s, rr in r.items()]
json.dump(out, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-game/results/g6_gc3_eval.json", "w"), indent=1)
