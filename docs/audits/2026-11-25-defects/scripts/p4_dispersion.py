"""P7 (Defects 4 + 8): dispersion AFTER the promoted mean repairs. Player: arm PRODALLB vs PRODXT1 (p5_*_joint) variance
ratio E[(y-m)^2]/E[var], randomized-PIT 80% coverage, P(0) calibration, by stat x role. Game: GameJointPMF interval
coverage (50/80/95) for margin and total by |spread| band, total band and season phase on g1_GC3pl. Single process."""
import json
import numpy as np, pandas as pd
from cbb_platform import config as C
Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
B7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
R = {"player": {}, "game": {}}
for ds in ("F2026", "LIVE"):
    d = pd.read_parquet(Q / f"p4/p5_{ds}_joint.parquet", columns=["arm", "stat", "y", "mean", "var", "min_mu", "F_lo", "F_hi", "p0"])
    d = d[d.stat.isin(B7) & d.arm.isin(["PRODXT1", "PRODALLB"])]
    d["role"] = np.where(d.min_mu >= 30, "CORE", np.where(d.min_mu >= 20, "ROT", "LIM"))
    d["pit"] = d.F_lo + np.random.default_rng(0).random(len(d)) * (d.F_hi - d.F_lo)
    out = {}
    for (a, s, ro), h in d.groupby(["arm", "stat", "role"]):
        out[f"{a}|{s}|{ro}"] = {"n": len(h), "var_ratio": round(float(((h.y - h["mean"]) ** 2).sum() / h["var"].sum()), 3),
                                "pit80": round(float(((h.pit > .1) & (h.pit < .9)).mean()), 3), "p0": round(float(h.p0.mean()), 4), "y0": round(float((h.y == 0).mean()), 4)}
    R["player"][ds] = out
s = pd.read_parquet(C.PROJECT_ROOT / "data/qa/large/game_20261125/g1_GC3pl.parquet")
s["sb"] = pd.cut(s.pm.abs(), [-.1, 3, 7, 12, 99], labels=["<3", "3-7", "7-12", "12+"]).astype(str)
s["tb"] = pd.cut(s.pt, [0, 135, 145, 155, 300], labels=["<135", "135-145", "145-155", "155+"]).astype(str)
s["ph"] = pd.cut(s.ngp, [-1, 3, 8, 15, 99], labels=["ngp0-3", "ngp4-8", "ngp9-15", "ngp16+"]).astype(str)
for season, h in s.groupby("season"):
    o = {}
    for col in ("sb", "tb", "ph"):
        for k, g in h.groupby(col):
            o[f"{col}={k}"] = {"n": len(g), **{c: round(float(g[c].mean()), 3) for c in ("in50_m", "in80_m", "in95_m", "in50_t", "in80_t", "in95_t")}}
    m = h.dropna(subset=["p_mkt_home"]); yh = (m.y_m > 0).astype(float)
    m = m.assign(pb=pd.cut(m.p_home, [0, .2, .35, .5, .65, .8, 1]).astype(str))
    o["ml_calibration"] = {k: {"n": len(g), "p": round(float(g.p_home.mean()), 3), "y": round(float((g.y_m > 0).mean()), 3), "mkt": round(float(g.p_mkt_home.mean()), 3)} for k, g in m.groupby("pb")}
    R["game"][int(season)] = o
json.dump(R, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results/p4_dispersion.json", "w"), indent=1)
for ds, o in R["player"].items():
    print("==", ds)
    for s_ in B7:
        print("  ", s_, " | ".join(f"{ro} vr {o[f'PRODXT1|{s_}|{ro}']['var_ratio']}->{o[f'PRODALLB|{s_}|{ro}']['var_ratio']} pit80 {o[f'PRODALLB|{s_}|{ro}']['pit80']}" for ro in ("CORE", "ROT", "LIM")))
for season, o in R["game"].items():
    print("== game", season); [print("  ", k, v) for k, v in o.items()]
