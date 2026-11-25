"""Gate evaluator for DEFECT_PROTOCOL.json (written before any candidate metric). Inputs p5_{F2026,LIVE}_defects.parquet
(arms PRODXT1 / PRODDV1 / PRODOC1 / PRODOT1, common random numbers). Output results/p2_eval.json. Single process."""
import importlib.util
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
M = C.PROJECT_ROOT / "data/qa/large/defects_20261125"
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
COMBOS = ["PRA", "PR", "PA", "RA", "STOCKS"]
spec = importlib.util.spec_from_file_location("b", C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/scripts/p1_build_mults.py")
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)


def cl(v, g):
    v = np.asarray(v, float); s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"]); m = float(v.mean()); G = len(s)
    return (m, m + 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))) if G > 1 else (m, float("nan"))


def load(ds):
    d = pd.read_parquet(Q / f"p4/p5_{ds}_defects.parquet", columns=["arm", "game_id", "pgx", "stat", "y", "mean", "nll", "rps", "min_mu"])
    d = d[~d.stat.str.endswith("_IND")]
    f = B.frame(ds)[["pgx", "s_prev", "season_day"]]
    d = d.merge(f, on="pgx", how="left")
    k = ["game_id", "pgx", "stat"]
    b = d[d.arm == "PRODXT1"].set_index(k)
    out = {}
    for arm in ("PRODDV1", "PRODOC1", "PRODOT1"):
        a = d[d.arm == arm].set_index(k)
        ix = a.index.intersection(b.index)
        x = a.loc[ix, ["y", "mean", "min_mu", "s_prev", "season_day"]].copy()
        x["dll"] = a.loc[ix, "nll"].to_numpy() - b.loc[ix, "nll"].to_numpy()
        x["drps"] = a.loc[ix, "rps"].to_numpy() - b.loc[ix, "rps"].to_numpy()
        x = x.reset_index()
        mf = pd.read_parquet(M / f"mult_{ds}_{arm[4:]}.parquet")
        if "G" in mf:
            x = x.merge(mf[["pgx", "G"]], on="pgx", how="left")
        out[arm] = x
    return out


def summ(x):
    b7 = x[x.stat.isin(BASE7)]
    r = {"stacked": cl(b7.dll, b7.game_id), "per_stat": {s: cl(g.dll, g.game_id) for s, g in b7.groupby("stat")},
         "combos": {c: cl(g.drps, g.game_id) for c, g in x[x.stat.isin(COMBOS)].groupby("stat")}}
    t = b7[(b7.s_prev > 0) & (b7.min_mu >= 20) & (b7.season_day <= 30) & b7.stat.isin(["PTS", "AST", "FG3M", "TOV"])]
    r["target_dv"] = cl(t.dll, t.game_id) if len(t) else (float("nan"),) * 2
    if "G" in b7:
        t = b7[b7.G >= 5]
        r["target_ctx"] = cl(t.dll, t.game_id) if len(t) else (float("nan"),) * 2
    r["protected_cells"] = {}
    b7 = b7.assign(role=np.where(b7.min_mu >= 30, "CORE", np.where(b7.min_mu >= 20, "ROT", "LIM")))
    for (s, ro), h in b7[b7.role != "LIM"].groupby(["stat", "role"]):
        r["protected_cells"][f"{s}|{ro}"] = cl(h.dll, h.game_id)
    return r


if __name__ == "__main__":
    R = {ds: {a: summ(x) for a, x in load(ds).items()} for ds in ("F2026", "LIVE")}
    g = {}
    for a, key in (("PRODDV1", "DV1"), ("PRODOC1", "OC1"), ("PRODOT1", "OT1")):
        F, L = R["F2026"][a], R["LIVE"][a]
        gg = {"G1": F["stacked"][1] < 0 and L["stacked"][1] < 0,
              "G2": all(v[1] <= 0.0005 for ds in R for v in R[ds][a]["per_stat"].values()),
              "G4": all(v[1] <= 0.0005 for ds in R for v in R[ds][a]["combos"].values()),
              "PROTECTED": all(v[1] <= 0.002 for v in F["protected_cells"].values())}
        gg["G3"] = (F["target_dv"][1] < 0 and L["target_dv"][0] < 0) if key == "DV1" else (F["target_ctx"][1] < 0 and L["target_ctx"][0] <= 0)
        g[key] = {k: bool(v) for k, v in gg.items()}
        g[key]["DECISION_EXCL_G5"] = "PASS" if all(g[key].values()) else "FAIL"
    for ds in R:
        for a, r in R[ds].items():
            print(f"\n== {ds} {a}: stacked {r['stacked'][0]:+.5f} ub {r['stacked'][1]:+.5f} | target_dv {r['target_dv'][0]:+.5f} ub {r['target_dv'][1]:+.5f}"
                  + (f" | target_ctx {r['target_ctx'][0]:+.5f} ub {r['target_ctx'][1]:+.5f}" if "target_ctx" in r else ""))
            print("   per stat:", " ".join(f"{s} {v[0]:+.5f}({v[1]:+.5f})" for s, v in r["per_stat"].items()))
            print("   combos ub:", " ".join(f"{c} {v[1]:+.5f}" for c, v in r["combos"].items()))
            bad = {k: round(v[1], 5) for k, v in r["protected_cells"].items() if v[1] > 0.002}
            print("   protected cells ub > .002:", bad or "none")
    print("\nGATES", json.dumps(g, indent=1))
    json.dump({"gates": g, "results": R}, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results/p2_eval.json", "w"), indent=1, default=float)
