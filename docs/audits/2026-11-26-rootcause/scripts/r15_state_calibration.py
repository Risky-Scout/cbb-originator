"""PS1 diagnostic (no fit): the production minutes model as an explicit state mixture. P(OUT) = 1 - p_play;
P(LIMITED <10 / BENCH 10-20 / ROTATION 20-28 / STARTER 28+) = p_play x P(clip(N(min_mu, min_sigma), 0, 48) in band)
(the bank's minutes draw, simulate.py). Realized state from actual minutes. By information class and season-day bucket.
-> results/r15_state_calibration.json"""
import json, math, os
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122")
O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
B = [0, 10, 20, 28, 1e9]; S = ["LIMITED", "BENCH", "ROTATION", "STARTER"]
Phi = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))
PANEL = pd.read_parquet(f"{O}/transition_panel.parquet").dropna(subset=["canonical_player_id"]).drop_duplicates(["canonical_player_id", "season"])


def probs(f):
    mu, sd = f.min_mu.to_numpy(float), np.maximum(f.min_sigma.to_numpy(float), 1e-6)
    cdf = lambda x: Phi((x - mu) / sd)
    pp = f.p_play.fillna(1).to_numpy(float)
    edges = [0.0, 10, 20, 28]
    # mass below 0 is clipped to 0 minutes (counted LIMITED); above 48 to STARTER
    P = {"OUT": 1 - pp}
    for i, s in enumerate(S):
        lo = cdf(edges[i]) if i else np.zeros(len(f))
        hi = cdf(edges[i + 1]) if i < 3 else np.ones(len(f))
        P[s] = pp * (hi - lo)
    return pd.DataFrame(P, index=f.index)


def realized(y):
    y = y.fillna(0).to_numpy(float)
    return np.select([y <= 0, y < 10, y < 20, y < 28], ["OUT"] + S[:3], "STARTER")


out = {}
for ds, path, pid in (("F2026", f"{Q}/d1_F2026.parquet", "canonical_player_id"), ("LIVE", f"{Q}/live/oos_2027.parquet", "player_id")):
    f = pd.read_parquet(path, columns=[pid, "player_game_example_id", "p_play", "min_mu", "min_sigma", "y_min"])
    f["season"] = 2026 if ds == "F2026" else 2027
    f = f.merge(PANEL[["canonical_player_id", "season", "cls"]].rename(columns={"canonical_player_id": pid}), on=[pid, "season"], how="left")
    f["cls"] = f.cls.fillna("NO_PRIOR_HISTORY")
    f = f.dropna(subset=["min_mu", "min_sigma"])
    P = probs(f); r = realized(f.y_min)
    res = {}
    for c, idx in [("ALL", f.index)] + [(c, f.index[f.cls == c]) for c in sorted(f.cls.unique())]:
        res[c] = {"n": int(len(idx)), **{s: {"pred": round(float(P.loc[idx, s].mean()), 4), "obs": round(float((r[f.index.get_indexer(idx)] == s).mean()), 4)}
                                          for s in ["OUT"] + S}}
    out[ds] = res
json.dump(out, open(os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results/r15_state_calibration.json"), "w"), indent=1)
for ds, res in out.items():
    for c, v in res.items():
        print(ds, c.ljust(18), v["n"], " ".join(f"{s[:4]} {v[s]['pred']:.3f}/{v[s]['obs']:.3f}" for s in ["OUT"] + S))
