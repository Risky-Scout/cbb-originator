"""DEFECT_PROTOCOL amendment_1: same gates as p2_eval for OC2 / ALLA / ALLB (p5_*_joint.parquet), plus G5 market gate
for every candidate on LIVE graded prop events (p5_LIVE_lines_lines.parquet x production graded rows with a de-vigged
fair probability). Output results/p3_joint_eval.json. Single process."""
import importlib.util
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
M = C.PROJECT_ROOT / "data/qa/large/defects_20261125"
spec = importlib.util.spec_from_file_location("p2", C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/scripts/p2_eval.py")
P2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(P2)
ARMS = {"PRODOC2": "OC2", "PRODALLA": "ALLA", "PRODALLB": "ALLB"}


def load(ds):
    d = pd.read_parquet(Q / f"p4/p5_{ds}_joint.parquet", columns=["arm", "game_id", "pgx", "stat", "y", "mean", "nll", "rps", "min_mu"])
    d = d[~d.stat.str.endswith("_IND")].merge(P2.B.frame(ds)[["pgx", "s_prev", "season_day"]], on="pgx", how="left")
    k = ["game_id", "pgx", "stat"]
    b = d[d.arm == "PRODXT1"].set_index(k)
    out = {}
    for arm, tag in ARMS.items():
        a = d[d.arm == arm].set_index(k)
        ix = a.index.intersection(b.index)
        x = a.loc[ix, ["y", "mean", "min_mu", "s_prev", "season_day"]].copy()
        x["dll"] = a.loc[ix, "nll"].to_numpy() - b.loc[ix, "nll"].to_numpy()
        x["drps"] = a.loc[ix, "rps"].to_numpy() - b.loc[ix, "rps"].to_numpy()
        x = x.reset_index().merge(pd.read_parquet(M / f"mult_{ds}_OC1.parquet")[["pgx", "G"]], on="pgx", how="left")
        out[arm] = x
    return out


def ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def market():
    L = pd.read_parquet(Q / "p4/p5_LIVE_lines_lines.parquet")
    g = pd.read_parquet(C.PROJECT_ROOT / "data/qa/large/market_20261125/perf_live_overlap.parquet")
    g = g[g.result.isin(["WIN", "LOSS"])].dropna(subset=["fair_probability"])
    x = g.merge(L, on=["game_id", "pgx", "stat", "line"])
    ps = np.where(x.side == "OVER", x.p_over, 1 - x.p_over - x.p_push) / np.clip(1 - x.p_push, 1e-9, None)
    x["p_arm"], x["y"] = ps, (x.result == "WIN").astype(float)
    out = {}
    base = x[x.arm == "PRODXT1"].set_index(["eval_key"])
    for arm, h in x.groupby("arm"):
        h = h.set_index("eval_key")
        ix = h.index.intersection(base.index)
        d = ll(h.loc[ix, "p_arm"], h.loc[ix, "y"]) - ll(base.loc[ix, "p_arm"], base.loc[ix, "y"])
        out[arm] = {"n": int(len(ix)), "logloss": float(ll(h.loc[ix, "p_arm"], h.loc[ix, "y"]).mean()),
                    "market_logloss": float(ll(h.loc[ix, "fair_probability"], h.loc[ix, "y"]).mean()),
                    "brier": float(((h.loc[ix, "p_arm"] - h.loc[ix, "y"]) ** 2).mean()),
                    "market_brier": float(((h.loc[ix, "fair_probability"] - h.loc[ix, "y"]) ** 2).mean()),
                    "d_vs_incumbent": float(d.mean())}
    return out


if __name__ == "__main__":
    R = {ds: {a: P2.summ(x) for a, x in load(ds).items()} for ds in ("F2026", "LIVE")}
    MK = market()
    g = {}
    for arm, key in ARMS.items():
        F, L = R["F2026"][arm], R["LIVE"][arm]
        gg = {"G1": F["stacked"][1] < 0 and L["stacked"][1] < 0,
              "G2": all(v[1] <= 0.0005 for ds in R for v in R[ds][arm]["per_stat"].values()),
              "G4": all(v[1] <= 0.0005 for ds in R for v in R[ds][arm]["combos"].values()),
              "PROTECTED": all(v[1] <= 0.002 for v in F["protected_cells"].values()),
              "G3": F["target_ctx"][1] < 0 and L["target_ctx"][0] <= 0,
              "G5": MK[arm]["d_vs_incumbent"] <= 0.005}
        g[key] = {k: bool(v) for k, v in gg.items()}
        g[key]["DECISION"] = "PASS" if all(g[key].values()) else "FAIL"
    g["G5_DV1"] = MK["PRODDV1"]["d_vs_incumbent"] <= 0.005
    g["G5_OT1"] = MK["PRODOT1"]["d_vs_incumbent"] <= 0.005
    for ds in R:
        for a, r in R[ds].items():
            print(f"\n== {ds} {a}: stacked {r['stacked'][0]:+.5f} ub {r['stacked'][1]:+.5f} | target_ctx ub {r['target_ctx'][1]:+.5f} | target_dv ub {r['target_dv'][1]:+.5f}")
            print("   per stat:", " ".join(f"{s} {v[0]:+.5f}({v[1]:+.5f})" for s, v in r["per_stat"].items()))
            print("   combos ub:", " ".join(f"{c} {v[1]:+.5f}" for c, v in r["combos"].items()))
            print("   protected ub > .002:", {k: round(v[1], 5) for k, v in r["protected_cells"].items() if v[1] > 0.002} or "none")
    print("\nMARKET (LIVE graded events)")
    for a, v in MK.items():
        print(f"  {a:9s} n {v['n']} logloss {v['logloss']:.4f} (market {v['market_logloss']:.4f}) brier {v['brier']:.4f} (market {v['market_brier']:.4f}) d_vs_inc {v['d_vs_incumbent']:+.4f}")
    print("\nGATES", json.dumps(g, indent=1, default=bool))
    json.dump({"gates": g, "results": R, "market": MK}, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results/p3_joint_eval.json", "w"), indent=1, default=float)
