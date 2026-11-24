"""Challenger M1 (cold-start minutes SD) evaluation, written before any M1 metric was read
(PHASE4_PROTOCOL.json challenger_M1_minutes_sd + addendum_production_arm).

    p9_m1_eval.py <F2026 m1 parquet> <F2026 p2final parquet> <LIVE m1 parquet> <LIVE p2final parquet>

PRODXM1 vs PRODX (= the P2 arm of the p2final runs: identical configuration and seeds). Also the declared record arms
M1 vs INC and A2M1 vs A2ALL when the p4 replay files are present. Minutes diagnostics from the replay inputs."""
import json
import sys

import numpy as np
import pandas as pd

from cbb_platform import config as C

BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
COMBOS = ["PRA", "PR", "PA", "RA", "STOCKS"]
TARGET = ["COLD_EARLY_SEASON", "COLD_NO_HISTORY", "COLD_TRANSFER_IN"]
S = {"COLD_EARLY_SEASON": 1.115, "COLD_NO_HISTORY": 1.257, "COLD_TRANSFER_IN": 1.09, "WARM": 0.972}


def cl(v, g):
    v = np.asarray(v, float); s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"]); m = float(v.mean()); G = len(s)
    if G < 2:
        return m, float("nan")
    return m, m + 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))


def pair(fa, arm, fb, base):
    k = ["game_id", "pgx", "stat"]
    a = pd.read_parquet(fa); a = a[a.arm == arm].set_index(k)
    b = pd.read_parquet(fb); b = b[b.arm == base].set_index(k)
    ix = a.index.intersection(b.index)
    x = a.loc[ix, ["y", "mean", "min_mu", "cold", "early"]].copy() if "early" in a else a.loc[ix, ["y", "mean", "min_mu", "cold"]].copy()
    x["dll"] = a.loc[ix, "nll"].to_numpy() - b.loc[ix, "nll"].to_numpy()
    x["drps"] = a.loc[ix, "rps"].to_numpy() - b.loc[ix, "rps"].to_numpy()
    return x.reset_index()


def report(name, x):
    r = {}
    b7 = x[x.stat.isin(BASE7)]
    r["stacked"] = cl(b7.dll, b7.game_id)
    r["per_stat"] = {s: cl(g.dll, g.game_id) for s, g in b7.groupby("stat")}
    r["cells"] = {f"{s}|{c}": cl(h.dll, h.game_id) for (s, c), h in b7.groupby(["stat", "cold"]) if len(h) > 200}
    t = b7[b7.cold.isin(TARGET)]
    r["target_stacked"] = cl(t.dll, t.game_id)
    if "early" in b7:
        e = b7[b7.early.astype(bool)]
        r["early_stacked"] = cl(e.dll, e.game_id)
    r["combos_rps"] = {c: cl(g.drps, g.game_id) for c, g in x[x.stat.isin(COMBOS)].groupby("stat")}
    print(f"\n== {name}: stacked dLL {r['stacked'][0]:+.5f} ub {r['stacked'][1]:+.5f} | target classes {r['target_stacked'][0]:+.5f} ub {r['target_stacked'][1]:+.5f}"
          + (f" | early {r['early_stacked'][0]:+.5f} ub {r['early_stacked'][1]:+.5f}" if "early_stacked" in r else ""))
    for s, v in r["per_stat"].items():
        cc = " ".join(f"{c.split('|')[1].replace('COLD_', '')}:{w[0]:+.4f}({w[1]:+.4f})" for c, w in r["cells"].items() if c.startswith(s + "|"))
        print(f"   {s:5s} {v[0]:+.5f} ub {v[1]:+.5f} | {cc}")
    print("   combos dRPS ub: " + " ".join(f"{c} {v[1]:+.5f}" for c, v in r["combos_rps"].items()))
    return r


def minutes_diag():
    """Forecast normal clip(N(min_mu, sigma), 0, 60) vs realized played minutes, by class, sigma before / after M1."""
    out = {}
    for ds, f in (("F2026", C.PROJECT_ROOT / "data/qa/large/causal_20261122/d1_F2026.parquet"),
                  ("LIVE", C.PROJECT_ROOT / "data/qa/large/causal_20261122/live/oos_2027.parquet")):
        d = pd.read_parquet(f)
        if "min_sigma" not in d or "cold_start_class" not in d:
            continue
        d = d[d.y_min > 0]
        z = np.random.default_rng(0).standard_normal((400, 1))
        res = {}
        for cls, h in d.groupby("cold_start_class"):
            mu, sg, y = h.min_mu.to_numpy(float), h.min_sigma.to_numpy(float), h.y_min.to_numpy(float)
            row = {"n": len(h), "bias": float((y - mu).mean()), "MAE": float(np.abs(y - mu).mean())}
            for lab, s in (("inc", 1.0), ("m1", S.get(cls, 1.0))):
                draws = np.clip(mu[None] + s * sg[None] * z, 0, 60)
                q = {p: np.quantile(draws, p, axis=0) for p in (.1, .25, .75, .9)}
                row[f"sd_ratio_{lab}"] = float(np.std(y - mu) / np.sqrt(np.mean(draws.var(0))))
                row[f"cov50_{lab}"] = float(((y >= q[.25]) & (y <= q[.75])).mean())
                row[f"cov80_{lab}"] = float(((y >= q[.1]) & (y <= q[.9])).mean())
            res[cls] = row
        out[ds] = res
        print(f"\n== minutes {ds}")
        print(pd.DataFrame(res).T.round(3).to_string())
    return out


if __name__ == "__main__":
    f26m, f26p, livm, livp = sys.argv[1:5]
    res = {"F2026": report("F2026 PRODXM1 vs PRODX", pair(f26m, "PRODXM1", f26p, "P2")),
           "LIVE": report("LIVE PRODXM1 vs PRODX", pair(livm, "PRODXM1", livp, "P2"))}
    p4 = C.PROJECT_ROOT / "data/qa/large/causal_20261122/p4"
    for ds, fm in (("F2026", f26m), ("LIVE", livm)):
        ref = p4 / f"replay_{ds}_a2.parquet"
        if ref.exists():
            res[f"{ds}_record_M1"] = report(f"{ds} record M1 vs INC", pair(fm, "M1", str(ref), "INC"))
            res[f"{ds}_record_A2M1"] = report(f"{ds} record A2M1 vs A2", pair(fm, "A2M1", str(ref), "A2"))
    res["minutes"] = minutes_diag()
    F, L = res["F2026"], res["LIVE"]
    G1 = F["stacked"][1] < 0 and L["stacked"][1] < 0 and all(v[1] <= 0.0005 for r in (F, L) for v in r["per_stat"].values())
    G2 = all(v[1] <= 0.002 for v in F["cells"].values())
    G3 = L.get("early_stacked", (0, 1))[1] <= 0.0005
    GT = F["target_stacked"][1] < 0 and L["target_stacked"][1] < 0
    GC = all(v[1] <= 0 for r in (F, L) for v in r["combos_rps"].values())
    g = {"G1": G1, "G2": G2, "G3": G3, "TARGET": GT, "COMBOS": GC}
    print("\nGATES", g, "\nDECISION:", "PROMOTE" if all(g.values()) else "REJECT")
    json.dump({"gates": g, **res}, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/m1_eval.json", "w"), indent=1, default=float)
