"""Challenger P1R (MNA on PTS / FG3M) evaluation, written before any P1R metric was read
(PHASE4_PROTOCOL.json challenger_P1R_mna_high_elasticity).   p14_p1r_eval.py <F2026 p1r> <F2026 t1> <LIVE p1r> <LIVE t1>"""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location("p9", Path(__file__).with_name("p9_m1_eval.py"))
p9 = importlib.util.module_from_spec(spec); spec.loader.exec_module(p9)
BINS, LAB = [0, 5, 10, 15, 20, 30, 99], ["<5", "5-10", "10-15", "15-20", "20-30", "30+"]


def bins(x):
    out = {}
    for s in ("PTS", "FG3M"):
        h = x[x.stat == s].assign(bin=lambda t: pd.cut(t.min_mu, BINS, labels=LAB, right=False))
        for lab, g in h.groupby("bin", observed=True):
            out[f"{s}|{lab}"] = p9.cl(g.dll, g.game_id)
            print(f"   {s:4s} {lab:6s} n {len(g):6d} y {g.y.mean():.3f} mean {g['mean'].mean():.3f} dLL {out[f'{s}|{lab}'][0]:+.5f} ub {out[f'{s}|{lab}'][1]:+.5f}")
    return out


if __name__ == "__main__":
    xf = p9.pair(sys.argv[1], "PRODXP1R", sys.argv[2], "PRODXT1"); F = p9.report("F2026 PRODXP1R vs PRODXT1", xf); F["bins"] = bins(xf)
    xl = p9.pair(sys.argv[3], "PRODXP1R", sys.argv[4], "PRODXT1"); L = p9.report("LIVE PRODXP1R vs PRODXT1", xl); L["bins"] = bins(xl)
    g = {"G1": F["stacked"][1] < 0 and L["stacked"][1] < 0 and all(v[1] <= 0.0005 for r in (F, L) for v in r["per_stat"].values()),
         "G2": all(v[1] <= 0.002 for v in F["bins"].values()),
         "G3": L.get("early_stacked", (0, 1))[1] <= 0.0005,
         "COMBOS": all(r["combos_rps"][c][1] <= 0 for r in (F, L) for c in ("PRA", "PR", "PA"))}
    print("\nGATES", g, "\nDECISION:", "PROMOTE" if all(g.values()) else "REJECT")
    json.dump({"gates": g, "F2026": F, "LIVE": L}, open(p9.C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/p1r_eval.json", "w"), indent=1, default=float)
