"""Step 1B/1C (2026-11-25): residual defect map of the CURRENT production engine (PRODXT1 = A2 + P2 + T1; replay files
p5_{F2026,LIVE}_t1, common seeds, chronological OOS: F2026 = 2025-26 frozen-fit walk, LIVE = 2026-27 through 11-21).

Per stat x segment (role, minutes band, cold-start class, season phase): N, forecast mean, realized mean, ratio with a
game-clustered 95% CI, variance ratio E[(y-m)^2]/E[var], randomized-PIT 80% coverage, Brier at the main line l0+.5 and
OVER/UNDER calibration, and the per-minute decomposition (minutes error vs per-minute rate error) for played rows.
A cell is a CONFIRMED_DEFECT candidate only if N >= 400, |ratio - 1| CI excludes 0 on BOTH datasets with the same sign
(or LIVE only, for 2026-27-specific effects, flagged), and |ratio - 1| >= 3%."""
import json
import sys

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
COMBOS = ["PRA", "PR", "PA", "RA", "STOCKS"]
MB = [0, 10, 20, 30, 99]
ML = ["<10", "10-20", "20-30", "30+"]


def ratio_ci(y, m, g):
    """ratio sum(y)/sum(m) with a game-clustered delta-method 95% CI."""
    y, m, g = np.asarray(y, float), np.asarray(m, float), np.asarray(g)
    R = y.sum() / m.sum()
    e = pd.Series(y - R * m).groupby(g).sum().to_numpy()
    G = len(e)
    se = np.sqrt(G / max(G - 1, 1) * (e ** 2).sum()) / m.sum()
    return float(R), float(R - 1.96 * se), float(R + 1.96 * se)


def load(ds):
    r = pd.read_parquet(Q / f"p4/p5_{ds}_t1.parquet")
    r = r[r.stat.isin(BASE7 + COMBOS)]
    if ds == "LIVE":
        f = pd.read_parquet(Q / "live/oos_2027.parquet")[["player_game_example_id", "role_diagnostic", "y_min"]]
        f = f.rename(columns={"player_game_example_id": "pgx", "role_diagnostic": "role"})
    else:
        f = pd.read_parquet(Q / "d1_F2026.parquet")[["player_game_example_id", "role_l5_minutes_rank", "y_min"]]
        rk = f.role_l5_minutes_rank
        f["role"] = np.where(rk <= 5, "TOP5_MINUTES", np.where(rk <= 8, "ROTATION_6_8", "BENCH_9PLUS"))
        f.loc[rk.isna(), "role"] = "NO_ROLE_HISTORY"
        f = f.rename(columns={"player_game_example_id": "pgx"})[["pgx", "role", "y_min"]]
    r = r.merge(f, on="pgx", how="left")
    r["mband"] = pd.cut(r.min_mu, MB, labels=ML, right=False).astype(str)
    u = np.random.default_rng(0).random(len(r))
    r["pit"] = r.F_lo + u * (r.F_hi - r.F_lo)
    r["p_over"] = 1 - r.c_0                       # P(Y > l0 + .5)
    r["y_over"] = (r.y > r.l0 + 0.5).astype(float)
    return r


def cell(h):
    R, lo, hi = ratio_ci(h.y, h["mean"], h.game_id)
    vr = float(((h.y - h["mean"]) ** 2).sum() / h["var"].sum())
    return {"n": int(len(h)), "games": int(h.game_id.nunique()), "mean_fc": float(h["mean"].mean()), "mean_y": float(h.y.mean()),
            "ratio": R, "lo": lo, "hi": hi, "var_ratio": vr, "pit80": float(((h.pit > .1) & (h.pit < .9)).mean()),
            "brier_l0": float(((h.p_over - h.y_over) ** 2).mean()), "p_over": float(h.p_over.mean()), "y_over": float(h.y_over.mean())}


def decomp(h):
    """played rows: stat mean = minutes x per-minute rate. log ratio(y/m) = log(y_min/min_mu) part + per-minute part."""
    h = h[h.y_min > 0]
    if len(h) < 200:
        return None
    rm = float(h.y_min.sum() / h.min_mu.sum())
    rs = float(h.y.sum() / h["mean"].sum())
    return {"minutes_ratio": rm, "stat_ratio": rs, "per_minute_ratio": rs / rm}


def segments(r):
    out = {}
    for s in BASE7 + COMBOS:
        d = r[r.stat == s]
        rec = {"ALL": cell(d)}
        for col in ("role", "mband", "cold", "early"):
            for k, h in d.groupby(col):
                if len(h) >= 400:
                    c = cell(h)
                    if s in BASE7:
                        c["decomp"] = decomp(h)
                    rec[f"{col}={k}"] = c
        out[s] = rec
    return out


if __name__ == "__main__":
    res = {ds: segments(load(ds)) for ds in ("F2026", "LIVE")}
    flags = []
    for s in BASE7 + COMBOS:
        for k in res["LIVE"][s]:
            L = res["LIVE"][s][k]
            F = res["F2026"][s].get(k)
            def sig(c):
                return c and c["n"] >= 400 and (c["lo"] > 1 or c["hi"] < 1) and abs(c["ratio"] - 1) >= 0.03
            if sig(L):
                both = bool(sig(F) and np.sign(F["ratio"] - 1) == np.sign(L["ratio"] - 1))
                flags.append({"stat": s, "seg": k, "live_ratio": round(L["ratio"], 3), "live_ci": [round(L["lo"], 3), round(L["hi"], 3)],
                              "f26_ratio": round(F["ratio"], 3) if F else None, "both": both, "live_var_ratio": round(L["var_ratio"], 2),
                              "decomp": L.get("decomp")})
    for f in flags:
        print(f)
    print("\nvariance ratio / PIT80 (ALL):")
    for s in BASE7 + COMBOS:
        a, b = res["F2026"][s]["ALL"], res["LIVE"][s]["ALL"]
        print(f"  {s:6s} F26 ratio {a['ratio']:.3f} vr {a['var_ratio']:.2f} pit80 {a['pit80']:.3f} | LIVE ratio {b['ratio']:.3f} "
              f"[{b['lo']:.3f},{b['hi']:.3f}] vr {b['var_ratio']:.2f} pit80 {b['pit80']:.3f} over p {b['p_over']:.3f} y {b['y_over']:.3f}")
    json.dump({"segments": res, "flags": flags}, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/p15_defect_map.json", "w"),
              indent=1, default=float)
