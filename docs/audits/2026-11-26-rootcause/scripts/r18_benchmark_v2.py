"""CBB_ORIGINATOR_FINAL_BENCHMARK_V2. Final engine only (player: JM1_FINAL+...+CTX1+PI2+PS2 replayed engine-pure on the live
graded prop events at their captured lines; game: cbb-game-model-1.2.0 walk-forward). Market = de-vigged contemporaneous
fair probability (props) / no-vig closing moneyline, closing spread / total (games). Paired game-cluster bootstrap.
Class: MODEL BETTER / MARKET BETTER (95% interval of model - market excludes 0), INDISTINGUISHABLE, INSUFFICIENT SAMPLE (n < 100)."""
import json, os
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122"); O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
G = os.path.join(ROOT, "data/qa/large/game_20261125"); RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
rng = np.random.default_rng(2026)
ll = lambda p, y: -(y * np.log(np.clip(p, 1e-6, 1 - 1e-6)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))


def paired(d, g):
    s = pd.DataFrame({"d": d, "g": g}).groupby("g").d.agg(["sum", "count"])
    i = rng.integers(0, len(s), (2000, len(s)))
    r = s["sum"].to_numpy()[i].sum(1) / s["count"].to_numpy()[i].sum(1)
    return [float(np.mean(d)), float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))]


def cls(v, n):
    if n < 100:
        return "INSUFFICIENT SAMPLE"
    return "MODEL BETTER" if v[2] < 0 else ("MARKET BETTER" if v[1] > 0 else "INDISTINGUISHABLE")


def block(p_m, p_k, y, g):
    d = ll(p_m, y) - ll(p_k, y)
    v = paired(d.to_numpy() if hasattr(d, "to_numpy") else d, g)
    return {"n": int(len(y)), "logloss_model": float(ll(p_m, y).mean()), "logloss_market": float(ll(p_k, y).mean()),
            "brier_model": float(((p_m - y) ** 2).mean()), "brier_market": float(((p_k - y) ** 2).mean()),
            "model_minus_market_logloss": v, "class": cls(v, len(y))}


out = {"id": "CBB_ORIGINATOR_FINAL_BENCHMARK_V2", "generated": "2026-11-26",
       "engine": {"player": "JM1_FINAL+RA2V1[AST,FG3M,STL,REB]+PTSC1[0]+TOVT1+DEV1+CTX1+PI2+PS2#972018d0ebf9 (engine-pure replay)",
                  "game": "cbb-game-model-1.2.0 (TP1P prior)"},
       "labels": "research replay of the final engine on live 2026-27 events; predecessor-engine production outcomes are NOT mixed in"}
g = pd.read_parquet(f"{ROOT}/data/qa/large/market_20261125/perf_live_overlap.parquet")
g = g[g.result.isin(["WIN", "LOSS"])].dropna(subset=["fair_probability"])
L = pd.read_parquet(f"{Q}/p4/p5_LIVE_ps2lines_lines.parquet"); L = L[L.arm == "PRODPS2"]
x = g.merge(L, on=["game_id", "pgx", "stat", "line"])
x["p"] = np.where(x.side == "OVER", x.p_over, 1 - x.p_over - x.p_push) / np.clip(1 - x.p_push, 1e-9, None)
x["y"] = (x.result == "WIN").astype(float)
P = pd.read_parquet(f"{O}/transition_panel.parquet").dropna(subset=["canonical_player_id"]).drop_duplicates(["canonical_player_id", "season"])
f = pd.read_parquet(f"{Q}/live/frame_2027.parquet", columns=["player_game_example_id", "canonical_player_id", "season"]).merge(
    pd.read_parquet(f"{Q}/live/oos_2027.parquet", columns=["player_game_example_id", "min_mu"]), on="player_game_example_id", how="left")
f = f.merge(P[["canonical_player_id", "season", "cls"]], on=["canonical_player_id", "season"], how="left")
x = x.merge(f.rename(columns={"player_game_example_id": "pgx"})[["pgx", "cls", "min_mu"]], on="pgx", how="left")
x["cls"] = x.cls.fillna("NO_PRIOR_HISTORY")
x["role"] = pd.cut(x.min_mu, [-1, 20, 28, 99], labels=["BENCH/LIMITED", "ROTATION", "STARTER"]).astype(str)
x["pbucket"] = pd.cut(x.fair_probability, [0, .4, .5, .6, 1], labels=["<.40", ".40-.50", ".50-.60", ".60+"]).astype(str)
pl = {"ALL": block(x.p, x.fair_probability, x.y, x.game_id)}
for dim in ("stat", "cls", "role", "side", "pbucket"):
    pl[dim] = {str(k): block(h.p, h.fair_probability, h.y, h.game_id) for k, h in x.groupby(dim)}
out["player_vs_market"] = pl
s = pd.read_parquet(f"{G}/g1_TP1P.parquet")
gm = {}
for season, h in s.groupby("season"):
    r = {}
    m = h.dropna(subset=["p_mkt_home"])
    if len(m):
        r["moneyline"] = block(m.p_home, m.p_mkt_home, (m.y_m > 0).astype(float), m.game_id)
    c = h.dropna(subset=["close_spread", "p_cover"]); c = c[c.push_ats == 0] if "push_ats" in c else c
    if len(c):
        d = ((c.y_m - c.pm) ** 2 - (c.y_m + c.close_spread) ** 2)
        v = paired(d.to_numpy(), c.game_id); r["spread_margin_mse_model_minus_close"] = {"n": int(len(c)), "diff": v, "class": cls(v, len(c))}
    t = h.dropna(subset=["close_total"])
    if len(t):
        d = ((t.y_t - t.pt) ** 2 - (t.y_t - t.close_total) ** 2)
        v = paired(d.to_numpy(), t.game_id); r["total_mse_model_minus_close"] = {"n": int(len(t)), "diff": v, "class": cls(v, len(t))}
    gm[int(season)] = r
out["game_vs_market"] = gm
json.dump(out, open(f"{RES}/CBB_ORIGINATOR_FINAL_BENCHMARK_V2.json", "w"), indent=1)
a = pl["ALL"]; print("PLAYER ALL", a["n"], round(a["logloss_model"], 4), round(a["logloss_market"], 4), np.round(a["model_minus_market_logloss"], 4), a["class"])
for dim in ("stat", "cls", "role", "pbucket"):
    print(dim, {k: (v["n"], v["class"], round(v["model_minus_market_logloss"][0], 3)) for k, v in pl[dim].items()})
for s_, r in gm.items():
    print("GAME", s_, {k: (v["n"], v["class"], round((v.get("model_minus_market_logloss") or v["diff"])[0], 4)) for k, v in r.items()})
