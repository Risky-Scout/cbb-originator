"""Challenger R1 (injury replacement), PHASE4_PROTOCOL.json challenger_R1_injury_replacement (declared before metrics)."""
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
TIERS = [0, 10, 20, 25, 45]
TL = ["<10", "10-20", "20-25", "25+"]


def cl(v, g):
    v = np.asarray(v, float); s = pd.Series(v).groupby(np.asarray(g)).agg(["sum", "count"]); m = float(v.mean()); G = len(s)
    return m, m + 1.96 * float(np.sqrt(G / (G - 1) * ((s["sum"] - s["count"] * m) ** 2).sum()) / len(v))


def hist(season):
    d = pd.read_parquet(Q / f"d1_{season}.parquet").rename(columns={"canonical_team_id": "team_id", "canonical_player_id": "player_id"})
    d = d[["game_id", "team_id", "player_id", "prediction_cutoff_utc", "min_mu", "y_min"]].sort_values("prediction_cutoff_utc")
    games = d[["team_id", "game_id", "prediction_cutoff_utc"]].drop_duplicates(["team_id", "game_id"]).sort_values(["team_id", "prediction_cutoff_utc"])
    games["k"] = games.groupby("team_id").cumcount()
    d = d.merge(games[["team_id", "game_id", "k"]], on=["team_id", "game_id"])
    d = d.sort_values(["team_id", "player_id", "k"])
    d["l3"] = d.groupby(["team_id", "player_id"]).y_min.transform(lambda s: s.rolling(3, min_periods=3).mean())
    last = d[["team_id", "player_id", "k", "l3", "min_mu"]]
    # candidate absentee for team game k+1: played game k with l3 >= 25 and has no row at k+1
    nxt = last[last.l3 >= 25].assign(k=lambda x: x.k + 1)
    have = d[["team_id", "player_id", "k"]].assign(here=True)
    m = nxt.merge(have, on=["team_id", "player_id", "k"], how="left")
    m = m[m.here.isna()].merge(games[["team_id", "k", "game_id"]], on=["team_id", "k"])   # the team did play game k+1
    lost = m.groupby(["game_id", "team_id"]).min_mu.sum().rename("L").reset_index()
    t = d.merge(lost, on=["game_id", "team_id"], how="left")
    t["L"] = t.L.fillna(0.0)
    return t[["game_id", "team_id", "player_id", "min_mu", "y_min", "L"]]


def live():
    d = pd.read_parquet(Q / "live/oos_2027.parquet")
    new = d[(d.min_mu >= 25) & (d.p_play >= 0.5) & ~(d.y_min > 0)]          # DNP rows carry y_min NaN
    lost = new.groupby(["game_id", "team_id"]).min_mu.sum().rename("L").reset_index()
    t = d[(d.y_min > 0) & ~d.index.isin(new.index)].merge(lost, on=["game_id", "team_id"], how="left")
    t["L"] = t.L.fillna(0.0)
    return t[["game_id", "team_id", "player_id", "min_mu", "y_min", "L"]]


def share(t, arm):
    w = t.min_mu if arm == "R1a" else t.min_mu * (40 - t.min_mu).clip(lower=0) / 40
    return t.L * w / w.groupby([t.game_id, t.team_id]).transform("sum")


def apply(t, arm, beta):
    return np.minimum(t.min_mu + beta * share(t, arm), np.maximum(40.0, t.min_mu))


def fit(t, arm):
    x = share(t, arm)[t.L > 0]; r = (t.y_min - t.min_mu)[t.L > 0]
    return float((x * r).sum() / (x * x).sum())


def evaluate(t, arm, beta, name):
    s = t[t.L > 0].copy()
    s["r1"] = apply(s, arm, beta)
    s["dse"] = (s.y_min - s.r1) ** 2 - (s.y_min - s.min_mu) ** 2
    s["tier"] = pd.cut(s.min_mu, TIERS, right=False, labels=TL)
    tiers = {str(k): {"n": len(h), "bias_inc": float((h.y_min - h.min_mu).mean()), "bias_r1": float((h.y_min - h.r1).mean())}
             for k, h in s.groupby("tier", observed=True)}
    m, ub = cl(s.dse, s.game_id.astype(str) + s.team_id.astype(str))
    red = sum(abs(v["bias_r1"]) < abs(v["bias_inc"]) for v in tiers.values())
    print(f"{name} {arm} beta {beta:.3f}: team-games {s[['game_id','team_id']].drop_duplicates().shape[0]} rows {len(s)} "
          f"MSE INC {((s.y_min-s.min_mu)**2).mean():.2f} R1 {((s.y_min-s.r1)**2).mean():.2f} dMSE {m:+.3f} ub {ub:+.3f} bias-reduced {red}/4")
    for k, v in tiers.items():
        print(f"    {k:6s} n {v['n']:5d} bias INC {v['bias_inc']:+.2f} R1 {v['bias_r1']:+.2f}")
    return {"dMSE": m, "ub": ub, "bias_reduced": red, "tiers": tiers}


if __name__ == "__main__":
    F25, F26, LV = hist("F2025"), hist("F2026"), live()
    res = {}
    for arm in ("R1a", "R1b"):
        b = fit(F25, arm)
        res[arm] = {"beta": b, "F2025": evaluate(F25, arm, b, "F2025")}
    arm = min(("R1a", "R1b"), key=lambda a: res[a]["F2025"]["dMSE"])
    b = res[arm]["beta"]
    print("selected on F2025:", arm, b)
    res["selected"] = arm
    res["F2026"] = evaluate(F26, arm, b, "F2026")
    res["LIVE"] = evaluate(LV, arm, b, "LIVE")
    ok = res["LIVE"]["ub"] < 0 and res["LIVE"]["bias_reduced"] >= 3 and res["F2026"]["ub"] < 0
    res["DECISION"] = "PROMOTE" if ok else "REJECT"
    print("DECISION:", res["DECISION"])
    json.dump(res, open(C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/r1_injury_eval.json", "w"), indent=1, default=float)
