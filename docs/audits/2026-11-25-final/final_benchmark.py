"""CBB_ORIGINATOR_FINAL_BENCHMARK (2026-11-25). Engine-pure: recorded live grades stratified by ENGINE_ID (never pooled
into a 'current' claim), current-engine counterfactual from the chronological replay (PRODALLB = deployed player engine),
game scorecard of the deployed game model (GC3pl = cbb-game-model-1.1.0) vs open / close. Classes: MODEL BETTER /
MARKET BETTER / STATISTICALLY INDISTINGUISHABLE / INSUFFICIENT SAMPLE (n < 300 events). 95% game-cluster bootstrap."""
import json, os
import numpy as np, pandas as pd
os.environ.setdefault("OMP_NUM_THREADS", "1")
R = "docs/audits/2026-11-25-final/results"; os.makedirs(R, exist_ok=True)
rng = np.random.default_rng(20261125)
def ll(p, y): p = np.clip(p, 1e-6, 1 - 1e-6); return -(y * np.log(p) + (1 - y) * np.log(1 - p))
def cls(n, lo, hi): return "INSUFFICIENT SAMPLE" if n < 300 else "MODEL BETTER" if hi < 0 else "MARKET BETTER" if lo > 0 else "STATISTICALLY INDISTINGUISHABLE"
def boot(d, gid):
    s = pd.Series(d).groupby(np.asarray(gid)).agg(["sum", "count"])
    if len(s) < 5: return float("nan"), float("nan")
    i = rng.integers(0, len(s), (2000, len(s))); r = s["sum"].to_numpy()[i].sum(1) / s["count"].to_numpy()[i].sum(1)
    return tuple(float(x) for x in np.percentile(r, [2.5, 97.5]))
def score(h):
    p = h.model_probability / np.clip(1 - h.push_probability.fillna(0), 1e-9, None); f = h.fair_probability; y = (h.result == "WIN").astype(float)
    d = ll(p, y) - ll(f, y); lo, hi = boot(d.to_numpy(), h.game_id); n = len(h)
    return {"n": n, "games": int(h.game_id.nunique()), "model_ll": float(ll(p, y).mean()), "market_ll": float(ll(f, y).mean()),
            "d_ll": float(d.mean()), "ci": [lo, hi], "brier_model": float(((p - y) ** 2).mean()), "brier_market": float(((f - y) ** 2).mean()),
            "hit_rate": float(y.mean()), "mean_model_p": float(p.mean()), "class": cls(n, lo, hi)}
g = pd.read_parquet("data/qa/large/market_20261125/player_perf.parquet")
g = g[g.result.isin(["WIN", "LOSS"])].dropna(subset=["fair_probability", "model_probability"])
g["pb"] = pd.cut(g.model_probability, [0, .5, .55, .6, .65, 1]).astype(str)
out = {"id": "CBB_ORIGINATOR_FINAL_BENCHMARK", "generated": "2026-11-25", "player": {"recorded_by_engine": {}, "recorded_all_history": score(g)}}
for e, h in g.groupby("engine_id"):
    rec = {"overall": score(h), "strata": {}}
    for keys in (["stat"], ["player_role"], ["side"], ["pb"], ["stat", "player_role", "side"]):
        for k, q in h.groupby(keys):
            if len(q) >= 30: rec["strata"]["|".join(keys) + "=" + "|".join(map(str, np.atleast_1d(k)))] = score(q)
    # paired OVER/UNDER reconstruction: each event seen from the OVER side (complement of recorded side, push removed)
    pw = h.model_probability / np.clip(1 - h.push_probability.fillna(0), 1e-9, None)
    po = np.where(h.side == "OVER", pw, 1 - pw); fo = np.where(h.side == "OVER", h.fair_probability, 1 - h.fair_probability)
    yo = np.where(h.side == "OVER", h.result == "WIN", h.result == "LOSS").astype(float)
    b = pd.cut(po, [0, .3, .4, .5, .6, .7, 1])
    rec["paired_over_calibration"] = {str(k): {"n": int(len(v)), "model": round(float(po[v.index].mean()), 3), "realized": round(float(yo[v.index].mean()), 3),
                                               "market": round(float(fo[v.index].mean()), 3)} for k, v in pd.Series(np.arange(len(h))).groupby(b, observed=True)}
    rec["paired_over_means"] = {"model": float(po.mean()), "realized": float(yo.mean()), "market": float(fo.mean())}
    out["player"]["recorded_by_engine"][e] = rec
out["player"]["current_engine_counterfactual"] = json.load(open("docs/audits/2026-11-25-defects/results/p5_player_market_paired.json"))
out["player"]["current_engine_recorded"] = {"engine_id": "JM1_FINAL+RA2V1[AST,FG3M,STL,REB]+PTSC1[0]+TOVT1+DEV1+CTX1#03e534d236e2",
                                            "n": 0, "class": "INSUFFICIENT SAMPLE", "note": "first graded slate 2026-11-25 (grades 11-26)"}
out["game"] = json.load(open("docs/audits/2026-11-25-game/results/g2_vs_market_GC3pl.json")) if os.path.exists("docs/audits/2026-11-25-game/results/g2_vs_market_GC3pl.json") else None
json.dump(out, open(f"{R}/CBB_ORIGINATOR_FINAL_BENCHMARK.json", "w"), indent=1, default=str)
for e, r in out["player"]["recorded_by_engine"].items():
    o = r["overall"]; print(f"{e[:60]:60s} n {o['n']:4d} LL {o['model_ll']:.4f} mkt {o['market_ll']:.4f} d {o['d_ll']:+.4f} [{o['ci'][0]:+.4f},{o['ci'][1]:+.4f}] {o['class']} | P(over) model {r['paired_over_means']['model']:.3f} real {r['paired_over_means']['realized']:.3f} mkt {r['paired_over_means']['market']:.3f}")
o = out["player"]["recorded_all_history"]; print("ALL", o["n"], round(o["d_ll"], 4), o["ci"], o["class"])
print("game present:", out["game"] is not None)
