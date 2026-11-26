"""Paired gate evaluation of a candidate arm vs PRODALLB (ROOT_CAUSE_PROTOCOL.json gates G1-G4; G5 separately).
    r11_eval.py <tag> <ARM>     e.g. pi1 PRODPI1 | rd1 PRODRD1
Reads data/qa/large/causal_20261122/p4/p5_{F2026,LIVE}_<tag>.parquet. 95% game-cluster bootstrap. Single process."""
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122")
O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
tag, ARM = sys.argv[1], sys.argv[2]
BASEARM = os.environ.get("BASE", "PRODALLB")
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
COMBOS = ["PRA", "PR", "PA", "RA", "STOCKS"]
rng = np.random.default_rng(20261126)
PANEL = pd.read_parquet(f"{O}/transition_panel.parquet").dropna(subset=["canonical_player_id"]).drop_duplicates(["canonical_player_id", "season"])


def cls_map(ds):
    f = (pd.read_parquet(f"{Q}/live/frame_2027.parquet", columns=["player_game_example_id", "canonical_player_id", "season"]) if ds == "LIVE"
         else pd.read_parquet(f"{Q}/d1_{ds}.parquet", columns=["player_game_example_id", "canonical_player_id", "season"]))
    f = f.merge(PANEL[["canonical_player_id", "season", "cls"]], on=["canonical_player_id", "season"], how="left")
    return dict(zip(f.player_game_example_id, f.cls.fillna("NO_PRIOR_HISTORY")))


def boot(d, g):
    s = pd.DataFrame({"d": d, "g": g}).groupby("g").d.agg(["sum", "count"])
    if len(s) < 5:
        return [float(np.mean(d)) if len(d) else None, None, None]
    i = rng.integers(0, len(s), (1000, len(s)))
    r = s["sum"].to_numpy()[i].sum(1) / s["count"].to_numpy()[i].sum(1)
    return [float(np.mean(d)), float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))]


def evaluate(ds):
    d = pd.read_parquet(f"{Q}/p4/p5_{ds}_{tag}.parquet", columns=["arm", "game_id", "pgx", "stat", "nll", "rps"])
    d = d[~d.stat.str.endswith("_IND")]
    k = ["game_id", "pgx", "stat"]
    a, b = d[d.arm == ARM].set_index(k), d[d.arm == BASEARM].set_index(k)
    ix = a.index.intersection(b.index)
    x = pd.DataFrame({"dll": a.loc[ix, "nll"].to_numpy() - b.loc[ix, "nll"].to_numpy(),
                      "drps": a.loc[ix, "rps"].to_numpy() - b.loc[ix, "rps"].to_numpy()}, index=ix).reset_index()
    x["cls"] = x.pgx.map(cls_map(ds))
    rd = f"{O}/rd1_rows_{ds}.parquet"
    if os.path.exists(rd):
        r = pd.read_parquet(rd); x = x.merge(r, on="pgx", how="left")
    base = x[x.stat.isin(BASE7)]
    out = {"stacked": boot(base.dll.to_numpy(), base.game_id.to_numpy()), "per_stat": {}, "cells": {}, "class": {}, "combos_rps": {}}
    for s, h in base.groupby("stat"):
        out["per_stat"][s] = boot(h.dll.to_numpy(), h.game_id.to_numpy())
    for c, h in base.groupby("cls"):
        out["class"][c] = boot(h.dll.to_numpy(), h.game_id.to_numpy())
        for s, q in h.groupby("stat"):
            out["cells"][f"{s}|{c}"] = boot(q.dll.to_numpy(), q.game_id.to_numpy())
    t = base[base.cls.isin(["TRANSFER", "NO_PRIOR_HISTORY"])]
    out["target_PI1"] = boot(t.dll.to_numpy(), t.game_id.to_numpy())
    if "has_abs" in base:
        t = base[base.has_abs.fillna(False).astype(bool)]
        out["target_RD1"] = boot(t.dll.to_numpy(), t.game_id.to_numpy())
        for bnd, h in base.groupby("band"):
            for s, q in h.groupby("stat"):
                out["cells"][f"{s}|band={bnd}"] = boot(q.dll.to_numpy(), q.game_id.to_numpy())
    for c in COMBOS:
        h = x[x.stat == c]
        if len(h):
            out["combos_rps"][c] = boot(h.drps.to_numpy(), h.game_id.to_numpy())
    return out


R = {ds: evaluate(ds) for ds in ("F2026", "LIVE")}
ub = lambda v: v[2] if v[2] is not None else v[0]
G = {"G1": all(ub(R[ds]["stacked"]) < 0 for ds in R),
     "G3": all(ub(R[ds]["class"].get("SAME_TEAM_RETURNER", [0, 0, 0])) <= 0.0002 for ds in R) and
           all(ub(v) <= 0.002 for v in R["F2026"]["cells"].values()) and all(ub(v) <= 0.0005 for ds in R for v in R[ds]["per_stat"].values()),
     "G4": all(ub(v) <= 0.0002 for ds in R for v in R[ds]["combos_rps"].values())}
if ARM in ("PRODPI1", "PRODPI2"):
    G["G2"] = all(ub(R[ds]["target_PI1"]) < 0 for ds in R)
    G["G3_protected_returner_ub"] = {ds: ub(R[ds]["class"].get("SAME_TEAM_RETURNER", [0, 0, 0])) for ds in R}
else:
    G["G2"] = all(ub(R[ds]["target_RD1"]) < 0 for ds in R)
    G["G3"] = all(ub(v) <= 0.002 for v in R["F2026"]["cells"].values() if "band=" in str(v) or True) and \
              all(ub(v) <= 0.0005 for ds in R for v in R[ds]["per_stat"].values())
G["failures"] = {ds: {"per_stat_over": {k: v for k, v in R[ds]["per_stat"].items() if ub(v) > 0.0005},
                      "cells_over": {k: v for k, v in R[ds]["cells"].items() if ub(v) > 0.002},
                      "combos_over": {k: v for k, v in R[ds]["combos_rps"].items() if ub(v) > 0.0002}} for ds in R}
json.dump({"results": R, "gates": G}, open(f"{RES}/r11_{tag}_eval.json", "w"), indent=1, default=str)
for ds in R:
    r = R[ds]
    print(ds, "stacked", np.round(r["stacked"], 5), "target", np.round(r.get("target_PI1" if ARM in ("PRODPI1", "PRODPI2") else "target_RD1"), 5))
    print("  class", {k: np.round(v, 5).tolist() for k, v in r["class"].items()})
    print("  per_stat", {k: np.round(v, 5).tolist() for k, v in r["per_stat"].items()})
    print("  combos", {k: np.round(v, 5).tolist() for k, v in r["combos_rps"].items()})
print({k: v for k, v in G.items() if k != "failures"}); print(json.dumps(G["failures"], default=str)[:1500])


def g5():
    """G5: graded live prop events with a de-vigged fair probability; arm vs PRODALLB log loss (p3_joint_eval.market pattern)."""
    Lp = f"{Q}/p4/p5_LIVE_{tag}lines_lines.parquet"
    if not os.path.exists(Lp):
        return None
    L = pd.read_parquet(Lp)
    g = pd.read_parquet(os.path.join(ROOT, "data/qa/large/market_20261125/perf_live_overlap.parquet"))
    g = g[g.result.isin(["WIN", "LOSS"])].dropna(subset=["fair_probability"])
    x = g.merge(L, on=["game_id", "pgx", "stat", "line"])
    x["p"] = np.where(x.side == "OVER", x.p_over, 1 - x.p_over - x.p_push) / np.clip(1 - x.p_push, 1e-9, None)
    x["y"] = (x.result == "WIN").astype(float)
    llf = lambda p, y: -(y * np.log(np.clip(p, 1e-6, 1 - 1e-6)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))
    a, b = x[x.arm == ARM].set_index("eval_key"), x[x.arm == BASEARM].set_index("eval_key")
    ix = a.index.intersection(b.index)
    la, lb, lm = llf(a.loc[ix, "p"], a.loc[ix, "y"]).mean(), llf(b.loc[ix, "p"], b.loc[ix, "y"]).mean(), llf(a.loc[ix, "fair_probability"], a.loc[ix, "y"]).mean()
    return {"n": int(len(ix)), "logloss_arm": float(la), "logloss_ALLB": float(lb), "logloss_market": float(lm), "pass": bool(la <= lb + 0.002)}


G5 = g5()
if G5:
    G["G5"] = G5["pass"]
    json.dump({"results": R, "gates": G, "G5": G5}, open(f"{RES}/r11_{tag}_{ARM}_eval.json", "w"), indent=1, default=str)
    print("G5", G5)
