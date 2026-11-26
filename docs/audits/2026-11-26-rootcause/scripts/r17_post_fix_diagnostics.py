"""Steps 9 / 10 / 11 after the upstream fixes (no fit): player dispersion by information class for the PS2 engine
(replay p5_*_ps2), game interval coverage and spread bands for game model 1.2.0 (g1_TP1P) vs 1.1.0 (g1_GC3pl).
-> results/r17_post_fix_diagnostics.json"""
import json, os
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122"); O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
G = os.path.join(ROOT, "data/qa/large/game_20261125")
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
P = pd.read_parquet(f"{O}/transition_panel.parquet").dropna(subset=["canonical_player_id"]).drop_duplicates(["canonical_player_id", "season"])
out = {"dispersion": {}, "game": {}}
for ds in ("F2026", "LIVE"):
    d = pd.read_parquet(f"{Q}/p4/p5_{ds}_ps2.parquet", columns=["arm", "pgx", "stat", "y", "mean", "var", "min_mu", "early"])
    f = (pd.read_parquet(f"{Q}/live/frame_2027.parquet", columns=["player_game_example_id", "canonical_player_id", "season"]) if ds == "LIVE"
         else pd.read_parquet(f"{Q}/d1_{ds}.parquet", columns=["player_game_example_id", "canonical_player_id", "season"]))
    f = f.merge(P[["canonical_player_id", "season", "cls"]], on=["canonical_player_id", "season"], how="left")
    cm = dict(zip(f.player_game_example_id, f.cls.fillna("NO_PRIOR_HISTORY")))
    d = d[d.stat.isin(BASE7)].copy(); d["cls"] = d.pgx.map(cm)
    d["role"] = pd.cut(d.min_mu, [-1, 10, 20, 28, 99], labels=["LIMITED", "BENCH", "ROTATION", "STARTER"]).astype(str)
    r = {}
    for arm, h in d.groupby("arm"):
        h = h.assign(z2=(h.y - h["mean"]) ** 2)
        ratio = lambda q: float(q.z2.sum() / q["var"].sum())
        r[arm] = {"all": ratio(h), "by_class": {c: ratio(q) for c, q in h.groupby("cls")},
                  "by_role": {c: ratio(q) for c, q in h.groupby("role")}, "early": {str(e): ratio(q) for e, q in h.groupby("early")},
                  "by_stat": {s: ratio(q) for s, q in h.groupby("stat")}}
    out["dispersion"][ds] = r
for tag in ("GC3pl", "TP1P"):
    s = pd.read_parquet(f"{G}/g1_{tag}.parquet")
    res = {}
    for season, h in s.groupby("season"):
        e = {"n": int(len(h)), **{c: float(h[c].mean()) for c in ("in50_m", "in80_m", "in95_m", "in50_t", "in80_t", "in95_t")},
             "margin_rmse": float(np.sqrt(((h.y_m - h.pm) ** 2).mean()))}
        L = h.dropna(subset=["close_spread"])
        if len(L):
            band = pd.cut((-L.close_spread).abs(), [-0.1, 3, 7, 12, 99], labels=["<3", "3-7", "7-12", "12+"])
            e["spread_bands"] = {str(b): {"n": int(len(q)), "model_rmse": float(np.sqrt(((q.y_m - q.pm) ** 2).mean())),
                                          "market_rmse": float(np.sqrt(((q.y_m + q.close_spread) ** 2).mean()))}
                                 for b, q in L.groupby(band, observed=True)}
        res[int(season)] = e
    out["game"][tag] = res
json.dump(out, open(os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results/r17_post_fix_diagnostics.json"), "w"), indent=1)
for ds, r in out["dispersion"].items():
    for arm, v in r.items():
        print(ds, arm, "all %.3f" % v["all"], {k: round(x, 3) for k, x in v["by_class"].items()}, {k: round(x, 3) for k, x in v["by_role"].items()}, "early", {k: round(x, 3) for k, x in v["early"].items()})
for tag, r in out["game"].items():
    for s, v in r.items():
        print(tag, s, "cov m50/80/95 %.3f %.3f %.3f t %.3f %.3f %.3f" % tuple(v[c] for c in ("in50_m", "in80_m", "in95_m", "in50_t", "in80_t", "in95_t")),
              {b: (q["n"], round(q["model_rmse"], 2), round(q["market_rmse"], 2)) for b, q in v.get("spread_bands", {}).items()})
