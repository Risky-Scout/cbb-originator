"""Evaluator for PHASE4_PROTOCOL.json challenger_S1_D1_C1 (written before any F2026 / LIVE metric of C1, S1, D1).
Inputs: data/qa/large/causal_20261122/p4/p5_{F2026,LIVE}_c1s1d1.parquet (arms PRODXT1 / PRODC1 / PRODS1 / PRODD1 / PRODALL,
common random numbers). Gates exactly as declared; output results/c1s1d1_eval.json."""
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
COMBOS = ["PRA", "PR", "PA", "RA", "STOCKS"]
TARGET = ["COLD_EARLY_SEASON", "COLD_NO_HISTORY", "COLD_TRANSFER_IN"]
MB, ML = [0, 10, 20, 30, 99], ["<10", "10-20", "20-30", "30+"]


def cl(v, g):
    v = np.asarray(v, float); s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"]); m = float(v.mean()); G = len(s)
    if G < 2:
        return m, float("nan")
    return m, m + 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))


def roles(ds):
    if ds == "LIVE":
        f = pd.read_parquet(Q / "live/oos_2027.parquet")[["player_game_example_id", "role_diagnostic"]]
        return f.rename(columns={"player_game_example_id": "pgx", "role_diagnostic": "role"})
    f = pd.read_parquet(Q / "d1_F2026.parquet")[["player_game_example_id", "role_l5_minutes_rank"]]
    rk = f.role_l5_minutes_rank
    f["role"] = np.where(rk <= 5, "TOP5_MINUTES", np.where(rk <= 8, "ROTATION_6_8", "BENCH_9PLUS"))
    f.loc[rk.isna(), "role"] = "NO_ROLE_HISTORY"
    return f.rename(columns={"player_game_example_id": "pgx"})[["pgx", "role"]]


def load(ds):
    d = pd.read_parquet(Q / f"p4/p5_{ds}_c1s1d1.parquet")
    d = d[~d.stat.str.endswith("_IND")].merge(roles(ds), on="pgx", how="left")
    d["mband"] = pd.cut(d.min_mu, MB, labels=ML, right=False).astype(str)
    k = ["game_id", "pgx", "stat"]
    b = d[d.arm == "PRODXT1"].set_index(k)
    out = {}
    for arm in ("PRODC1", "PRODS1", "PRODD1", "PRODALL"):
        a = d[d.arm == arm].set_index(k)
        ix = a.index.intersection(b.index)
        x = a.loc[ix, ["y", "mean", "cold", "early", "role", "mband"]].copy()
        x["base_mean"] = b.loc[ix, "mean"].to_numpy()
        x["dll"] = a.loc[ix, "nll"].to_numpy() - b.loc[ix, "nll"].to_numpy()
        x["drps"] = a.loc[ix, "rps"].to_numpy() - b.loc[ix, "rps"].to_numpy()
        out[arm] = x.reset_index()
    return out


def summary(x):
    b7 = x[x.stat.isin(BASE7)]
    r = {"stacked": cl(b7.dll, b7.game_id), "per_stat": {s: cl(g.dll, g.game_id) for s, g in b7.groupby("stat")},
         "identical": {s: bool(np.abs(g.dll).max() < 1e-12) for s, g in b7.groupby("stat")},
         "cells": {}, "early": {}, "combos": {c: cl(g.drps, g.game_id) for c, g in x[x.stat.isin(COMBOS)].groupby("stat")}}
    for col in ("role", "mband", "cold"):
        for (s, v), h in b7.groupby(["stat", col]):
            if len(h) >= 400:
                r["cells"][f"{s}|{col}={v}"] = cl(h.dll, h.game_id)
    e = b7[b7.early.astype(bool)]
    r["early"] = {s: cl(g.dll, g.game_id) for s, g in e.groupby("stat")}
    r["early_stacked"] = cl(e.dll, e.game_id)
    t = b7[b7.cold.isin(TARGET)]
    r["target_stacked"] = cl(t.dll, t.game_id)
    r["role_err"] = {}
    for s, g in b7.groupby("stat"):
        f = lambda m: max(abs(h.y.sum() / h[m].sum() - 1) for _, h in g.groupby("role") if len(h) >= 400)
        r["role_err"][s] = (f("base_mean"), f("mean"))
    return r


def ub(t):
    return t[1]


if __name__ == "__main__":
    R = {ds: {arm: summary(x) for arm, x in load(ds).items()} for ds in ("F2026", "LIVE")}
    F, L = (lambda a: R["F2026"][a]), (lambda a: R["LIVE"][a])
    others = lambda a, keep: all(R[ds][a]["identical"][s] for ds in R for s in BASE7 if s not in keep)
    cells = lambda a, stats, cols: all(ub(v) <= 0.002 for k, v in F(a)["cells"].items() if k.split("|")[0] in stats and k.split("|")[1].split("=")[0] in cols)
    g = {}
    a = "PRODC1"
    g["C1"] = {"G1": ub(F(a)["per_stat"]["PTS"]) < 0 and ub(L(a)["per_stat"]["PTS"]) < 0,
               "G2": cells(a, ["PTS"], ["role", "mband"]), "G3": ub(L(a)["early"]["PTS"]) <= 0.0005,
               "G4": L(a)["role_err"]["PTS"][1] < L(a)["role_err"]["PTS"][0],
               "COMBOS": all(ub(R[ds][a]["combos"][c]) <= 0 for ds in R for c in ("PRA", "PR", "PA")), "OTHERS": others(a, ["PTS"])}
    a = "PRODS1"
    g["S1"] = {"STL": ub(F(a)["per_stat"]["STL"]) < 0 and ub(L(a)["per_stat"]["STL"]) < 0,
               "CELLS": cells(a, ["STL"], ["role", "mband", "cold"]),
               "STOCKS": all(ub(R[ds][a]["combos"]["STOCKS"]) <= 0 for ds in R), "OTHERS": others(a, ["STL"])}
    a = "PRODD1"
    g["D1"] = {"G1": all(ub(R[ds][a]["stacked"]) < 0 for ds in R) and all(ub(v) <= 0.0005 for ds in R for v in R[ds][a]["per_stat"].values()),
               "G2": cells(a, BASE7, ["cold"]), "G3": ub(L(a)["early_stacked"]) <= 0.0005,
               "TARGET": all(ub(R[ds][a]["target_stacked"]) < 0 for ds in R),
               "COMBOS": all(ub(v) <= 0 for ds in R for v in R[ds][a]["combos"].values())}
    for k, v in g.items():
        for kk in list(v):
            v[kk] = bool(v[kk])                       # numpy bool -> JSON (serialization only)
        v["DECISION"] = "PASS" if all(v.values()) else "FAIL"
    for ds in R:
        for arm, r in R[ds].items():
            print(f"\n== {ds} {arm}: stacked {r['stacked'][0]:+.5f} ub {r['stacked'][1]:+.5f} | early {r['early_stacked'][1]:+.5f} | target {r['target_stacked'][1]:+.5f}")
            print("   per stat: " + " ".join(f"{s} {v[0]:+.5f}({v[1]:+.5f})" for s, v in r["per_stat"].items() if not r["identical"][s]))
            print("   combos dRPS ub: " + " ".join(f"{c} {v[1]:+.5f}" for c, v in r["combos"].items()))
            bad = {k: round(v[1], 5) for k, v in r["cells"].items() if v[1] > 0.002}
            print("   cells ub > .002:", bad or "none", "| role err:", {s: (round(a_, 3), round(b_, 3)) for s, (a_, b_) in r["role_err"].items() if not r["identical"][s]})
    print("\nGATES", json.dumps(g, indent=1))
    json.dump({"gates": g, "results": R}, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/c1s1d1_eval.json", "w"), indent=1, default=float)
