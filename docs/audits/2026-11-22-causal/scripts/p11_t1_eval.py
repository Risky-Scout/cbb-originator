"""Challenger T1 (TOV within-season trend) evaluation, written before any T1 metric was read
(PHASE4_PROTOCOL.json tov_blk_reevaluation.challenger_T1_tov_season_trend).

    p11_t1_eval.py <F2026 t1 parquet> <F2026 p2final parquet> <LIVE t1 parquet> <LIVE p2final parquet>

PRODXT1 vs PRODX (= the P2 arm of p2final: identical configuration and seeds, verified on the smoke run)."""
import json
import sys

import numpy as np
import pandas as pd

from cbb_platform import config as C

BINS, LAB = [0, 10, 20, 30, 99], ["<10", "10-20", "20-30", "30+"]


def cl(v, g):
    v = np.asarray(v, float); s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"]); m = float(v.mean()); G = len(s)
    if G < 2:
        return m, float("nan")
    return m, m + 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))


def pair(fa, fb):
    k = ["game_id", "pgx", "stat"]
    a = pd.read_parquet(fa); a = a[a.arm == "PRODXT1"].set_index(k)
    b = pd.read_parquet(fb); b = b[b.arm == "P2"].set_index(k)
    ix = a.index.intersection(b.index)
    return a.loc[ix].reset_index(), b.loc[ix].reset_index()


def report(name, a, b):
    other = a.stat != "TOV"
    same = bool(np.abs(a.nll[other].to_numpy() - b.nll[other].to_numpy()).max() < 1e-12)
    A, B = a[~other], b[~other]
    r = {"others_identical": same, "dLL": cl(A.nll.to_numpy() - B.nll.to_numpy(), A.game_id),
         "dRPS": cl(A.rps.to_numpy() - B.rps.to_numpy(), A.game_id), "cells": {}}
    bins = pd.cut(A.min_mu, BINS, labels=LAB, right=False)
    for lab in LAB:
        m = (bins == lab).to_numpy()
        r["cells"][lab] = {"n": int(m.sum()), "ratio_inc": float(B.y[m].mean() / B["mean"][m].mean()),
                           "ratio_t1": float(A.y[m].mean() / A["mean"][m].mean()),
                           "dLL": cl(A.nll.to_numpy()[m] - B.nll.to_numpy()[m], A.game_id.to_numpy()[m])}
    if "early" in A:
        e = A.early.astype(bool).to_numpy()
        r["early"] = cl(A.nll.to_numpy()[e] - B.nll.to_numpy()[e], A.game_id.to_numpy()[e])
    print(f"\n== {name}: others identical {same} | TOV dLL {r['dLL'][0]:+.5f} ub {r['dLL'][1]:+.5f} | dRPS {r['dRPS'][0]:+.5f} ub {r['dRPS'][1]:+.5f}"
          + (f" | early ub {r['early'][1]:+.5f}" if "early" in r else ""))
    for lab, c in r["cells"].items():
        print(f"   {lab:6s} n {c['n']:6d} y/mean INC {c['ratio_inc']:.3f} T1 {c['ratio_t1']:.3f} dLL {c['dLL'][0]:+.5f} ub {c['dLL'][1]:+.5f}")
    return r


if __name__ == "__main__":
    F = report("F2026", *pair(sys.argv[1], sys.argv[2]))
    L = report("LIVE", *pair(sys.argv[3], sys.argv[4]))
    g = {"others_identical": F["others_identical"] and L["others_identical"],
         "F2026_TOV": F["dLL"][1] < 0, "LIVE_TOV": L["dLL"][1] < 0,
         "F2026_cells": all(c["dLL"][1] <= 0.002 for c in F["cells"].values()),
         "LIVE_early": L.get("early", (0, 1))[1] <= 0.0005}
    print("\nGATES", g, "\nDECISION:", "PROMOTE" if all(g.values()) else "REJECT")
    json.dump({"gates": g, "F2026": F, "LIVE": L}, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/t1_eval.json", "w"), indent=1, default=float)
