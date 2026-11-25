"""Gate evaluator for GAME_CHALLENGER_PROTOCOL.json (written before any challenger metric). Paired challenger - base
losses on identical games; date-block bootstrap 95% CI (2,000 reps). Usage: g3_paired.py GC1 GC2"""
import json
import sys

import numpy as np
import pandas as pd

from cbb_platform import config as C

D = C.PROJECT_ROOT / "data/qa/large/game_20261125"
rng = np.random.default_rng(20261125)


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def boot(v, dates):
    v = pd.Series(np.asarray(v, float)).groupby(np.asarray(dates)).agg(["sum", "count"])
    S, N = v["sum"].to_numpy(), v["count"].to_numpy()
    idx = rng.integers(0, len(S), (2000, len(S)))
    reps = S[idx].sum(1) / N[idx].sum(1)
    return float(S.sum() / N.sum()), *map(float, np.percentile(reps, [2.5, 97.5]))


b = pd.read_parquet(D / "g1_base.parquet").set_index("game_id")
out = {}
for t in sys.argv[1:]:
    c = pd.read_parquet(D / f"g1_{t}.parquet").set_index("game_id")
    ix = b.index.intersection(c.index)
    B, X = b.loc[ix], c.loc[ix]
    r = {}
    for season in sorted(B.season.unique()):
        m = (B.season == season).to_numpy()
        bb, xx, dt = B[m], X[m], pd.to_datetime(B[m].game_date_et).dt.date
        yh = (bb.y_m > 0).astype(float)
        r[int(season)] = {"n": int(m.sum()),
                          "margin": boot((xx.y_m - xx.pm) ** 2 - (bb.y_m - bb.pm) ** 2, dt),
                          "total": boot((xx.y_t - xx.pt) ** 2 - (bb.y_t - bb.pt) ** 2, dt),
                          "ml": boot(ll(xx.p_home, yh) - ll(bb.p_home, yh), dt)}
    G1 = all(r[s][k][2] < 0 for s in (2024, 2025) for k in ("margin", "total"))
    G2 = all(r[2027][k][0] <= 0 for k in ("margin", "total"))
    G3 = all(r[s]["ml"][2] <= 0 for s in (2024, 2025))
    out[t] = {"results": r, "G1": G1, "G2": G2, "G3": G3, "DECISION": "PASS" if G1 and G2 and G3 else "FAIL"}
    print(t, out[t]["DECISION"], {s: {k: tuple(round(x, 3) for x in v) for k, v in rr.items() if k != "n"} for s, rr in r.items()})
json.dump(out, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-game/results/g3_paired.json", "w"), indent=1)
