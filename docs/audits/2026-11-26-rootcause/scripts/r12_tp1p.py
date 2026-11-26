"""TP1P (ROOT_CAUSE_PROTOCOL.TP1P_production_feature_equivalence): TP1 rebuilt with the CBBD API value term; gates E1-E3
(team level) -> results/r12_tp1p_equivalence.json, priors -> data/qa/large/rootcause_20261126/tp1p_priors.parquet.
E4 = r9 walk + r10 score with TP_TAG=TP1P."""
import importlib.util, json, os
import numpy as np, pandas as pd
spearmanr = lambda a, b: type("r", (), {"correlation": pd.Series(np.asarray(a, float)).rank().corr(pd.Series(np.asarray(b, float)).rank())})
H = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("r9", os.path.join(H, "r9_team_prior.py")); R9 = importlib.util.module_from_spec(spec); spec.loader.exec_module(R9)
Fa = R9.features("api"); Pa = R9.fit_predict(Fa)
Pr = pd.read_parquet(f"{R9.O}/tp1_priors.parquet")
Fr = R9.features("pack")
Pa.drop(columns=["beta"]).to_parquet(f"{R9.O}/tp1p_priors.parquet", index=False)
out = {"E1": {}, "E2": {}, "E3": {}}
for s in range(2015, 2027):
    a, r = Fa[Fa.season == s].set_index("team"), Fr[Fr.season == s].set_index("team")
    ix = a.index.intersection(r.index)
    out["E1"][s] = {c: float(spearmanr(a.loc[ix, c], r.loc[ix, c]).correlation) for c in ("RV", "LV", "TV", "TVd")}
for s in range(2018, 2028):
    a, r = Pa[Pa.season == s].set_index("team"), Pr[Pr.season == s].set_index("team")
    ix = a.index.intersection(r.index)
    da, dr = a.loc[ix, "tp1"] - a.loc[ix, "x0"], r.loc[ix, "tp1"] - r.loc[ix, "x0"]
    out["E2"][s] = float((np.sign(da) == np.sign(dr)).mean())
    if s <= 2026:
        q, w = a.dropna(subset=["y"]), r.dropna(subset=["y"])
        rm = lambda v, y: float(np.sqrt(((y - v) ** 2).mean()))
        out["E3"][s] = {"gc3": rm(q.x0, q.y), "tp1p": rm(q.tp1, q.y), "tp1": rm(w.tp1, w.y)}
e3 = out["E3"]
imp_p = np.mean([v["gc3"] - v["tp1p"] for v in e3.values()]); imp_r = np.mean([v["gc3"] - v["tp1"] for v in e3.values()])
G = {"E1": all(min(v.values()) >= 0.95 for v in out["E1"].values()),
     "E2": all(v >= 0.95 for v in out["E2"].values()),
     "E3": all(v["tp1p"] < v["gc3"] for v in e3.values()) and imp_p >= 0.8 * imp_r,
     "E3_mean_improvement": {"tp1p": float(imp_p), "tp1": float(imp_r)}}
out["gates"] = G
json.dump(out, open(os.path.join(R9.RES, "r12_tp1p_equivalence.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
print("E1 min per season", {s: round(min(v.values()), 3) for s, v in out["E1"].items()})
print("E2", {s: round(v, 3) for s, v in out["E2"].items()})
print("E3", {s: (round(v["gc3"], 2), round(v["tp1p"], 2), round(v["tp1"], 2)) for s, v in e3.items()})
print(G)
