"""Challenger P2 final evaluation (PHASE4_PROTOCOL.json challenger_P2_pts_compound_family.gate_P2_vs_PROD), written before
the final run was read.   p8_p2_eval.py <p5_F2026_p2final.parquet> <p5_LIVE_p2final.parquet>

Per stat / minutes bin (<5, 5-10, 10-15, 15-20, 20-30, 30+): log loss, RPS, Brier at the main line (floor(mean) + .5) and
the +-2 alternates, OVER calibration at the main line, P(0) and P(1) predicted vs realized, randomized-PIT mean and
50 % / 80 % central-interval coverage; paired game-clustered 95 % upper bounds for P2 - PROD."""
import json
import sys

import numpy as np
import pandas as pd

BINS = [0, 5, 10, 15, 20, 30, 99]
LAB = ["<5", "5-10", "10-15", "15-20", "20-30", "30+"]
ROLE = [0, 10, 20, 30, 99]


def cl(v, g):
    v = np.asarray(v, float)
    s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"])
    m = float(v.mean()); G = len(s)
    if G < 2:
        return m, float("nan")
    return m, m + 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))


def cover(lo, hi, a, b):
    w = np.maximum(hi - lo, 1e-12)
    return np.clip((np.minimum(hi, b) - np.maximum(lo, a)) / w, 0, 1)


def load(f):
    d = pd.read_parquet(f)
    k = ["game_id", "pgx", "stat"]
    a = d[d.arm == "P2"].set_index(k); b = d[d.arm == "PROD"].set_index(k)
    ix = a.index.intersection(b.index)
    return a.loc[ix].reset_index(), b.loc[ix].reset_index()


def metrics(x):
    y = x.y.to_numpy()
    out = {"n": len(x), "LL": x.nll.mean(), "RPS": x.rps.mean()}
    for o, c in (("m2", "c_m2"), ("0", "c_0"), ("p2", "c_p2")):
        po = 1 - x[c].to_numpy(); yo = (y > x.l0.to_numpy() + {"m2": -2, "0": 0, "p2": 2}[o] + 0.5).astype(float)
        out[f"Brier_{o}"] = float(np.mean((po - yo) ** 2))
        if o == "0":
            out["P_over"] = float(po.mean()); out["over_rate"] = float(yo.mean())
    out["P0"] = x.p0.mean(); out["real0"] = float((y == 0).mean()); out["P1"] = x.p1.mean(); out["real1"] = float((y == 1).mean())
    out["PIT"] = float(((x.F_lo + x.F_hi) / 2).mean())
    out["cov50"] = float(cover(x.F_lo.to_numpy(), x.F_hi.to_numpy(), .25, .75).mean())
    out["cov80"] = float(cover(x.F_lo.to_numpy(), x.F_hi.to_numpy(), .10, .90).mean())
    return out


def report(name, a, b, early=None):
    res = {"gates": {}, "cells": {}}
    print(f"\n===== {name}")
    for S in ["PTS", "PRA", "PR", "PA"]:
        A, B = a[a.stat == S], b[b.stat == S]
        dl = cl(A.nll.to_numpy() - B.nll.to_numpy(), A.game_id); dr = cl(A.rps.to_numpy() - B.rps.to_numpy(), A.game_id)
        db = cl((((1 - A.c_0) - (A.y > A.l0 + .5)) ** 2).to_numpy() - (((1 - B.c_0) - (B.y > B.l0 + .5)) ** 2).to_numpy(), A.game_id)
        res["gates"][S] = {"dLL": dl, "dRPS": dr, "dBrier_main": db}
        print(f"{S:4s} dLL {dl[0]:+.5f} ub {dl[1]:+.5f} | dRPS {dr[0]:+.5f} ub {dr[1]:+.5f} | dBrier(main) {db[0]:+.5f} ub {db[1]:+.5f}")
        A = A.assign(bin=pd.cut(A.min_mu, BINS, labels=LAB, right=False), role=pd.cut(A.min_mu, ROLE, right=False).astype(str))
        B = B.assign(bin=A.bin.to_numpy(), role=A.role.to_numpy())
        for lab in LAB:
            ma, mb = A.bin == lab, B.bin == lab
            if ma.sum() < 30:
                continue
            pa, pb = metrics(A[ma]), metrics(B[mb])
            d = cl(A[ma].nll.to_numpy() - B[mb].nll.to_numpy(), A[ma].game_id)
            res["cells"][f"{S}|{lab}"] = {"P2": pa, "PROD": pb, "dLL": d}
            print(f"   {lab:6s} n {pa['n']:6d} LL {pb['LL']:.4f}>{pa['LL']:.4f} (ub {d[1]:+.4f}) RPS {pb['RPS']:.4f}>{pa['RPS']:.4f} "
                  f"Brier {pb['Brier_0']:.4f}>{pa['Brier_0']:.4f} alt-2 {pb['Brier_m2']:.4f}>{pa['Brier_m2']:.4f} alt+2 {pb['Brier_p2']:.4f}>{pa['Brier_p2']:.4f} | "
                  f"Pover {pb['P_over']:.3f}/{pa['P_over']:.3f} real {pa['over_rate']:.3f} | P0 {pb['P0']:.3f}>{pa['P0']:.3f} real {pa['real0']:.3f} "
                  f"P1 {pb['P1']:.3f}>{pa['P1']:.3f} real {pa['real1']:.3f} | PIT {pb['PIT']:.3f}>{pa['PIT']:.3f} cov50 {pb['cov50']:.3f}>{pa['cov50']:.3f} cov80 {pb['cov80']:.3f}>{pa['cov80']:.3f}")
        if S == "PTS":
            res["role_ub"] = {}
            for r, h in A.groupby("role"):
                hb = B[B.role == r]
                res["role_ub"][r] = cl(h.nll.to_numpy() - hb.nll.to_numpy(), h.game_id)[1]
            if early is not None:
                e = A.pgx.map(early).fillna(False).astype(bool).to_numpy()
                res["early"] = cl(A.nll.to_numpy()[e] - B.nll.to_numpy()[e], A.game_id.to_numpy()[e])
    return res


if __name__ == "__main__":
    fa, fb = load(sys.argv[1]); la, lb = load(sys.argv[2])
    import pathlib
    early = pd.read_parquet(sys.argv[2]).drop_duplicates("pgx").set_index("pgx")["early"] if "early" in pd.read_parquet(sys.argv[2]).columns else None
    rf = report("F2026", fa, fb); rl = report("LIVE", la, lb, early)
    K1 = rf["gates"]["PTS"]["dLL"][1] < 0 and rl["gates"]["PTS"]["dLL"][1] < 0
    K2 = max(rf["role_ub"].values()) <= 0.002
    K3 = rl.get("early", (0, 1))[1] <= 0.0005
    K4 = all(r["gates"][c]["dRPS"][1] <= 0 for r in (rf, rl) for c in ("PRA", "PR", "PA"))
    g = {"K1": K1, "K2": K2, "K3": K3, "K4": K4, "F2026_role_ub": rf["role_ub"], "LIVE_early": rl.get("early")}
    print("\nGATES", json.dumps(g, default=float))
    print("DECISION:", "PROMOTE" if all((K1, K2, K3, K4)) else "REJECT")
    from cbb_platform import config as C
    out = C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results" / ("p2_final_eval.json" if "p2final" in sys.argv[2] else "p2_eval_check.json")
    json.dump({"gates": g, "F2026": rf, "LIVE": rl}, open(out, "w"), indent=1, default=float)
