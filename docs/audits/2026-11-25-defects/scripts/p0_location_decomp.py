"""P0 (2026-11-25): location decomposition of the CURRENT production engine (replay arm PRODXT1 = 4f0de48 config) on
LIVE (2026-27 through 11-21) and F2026 (2025-26 frozen-fit walk). Chronological OOS, played rows (props void DNP).

Per stat x role (min_mu: LIMITED_UNDER_20 / ROTATION_20_TO_30 / CORE_30_PLUS):
  incumbent       sum(mean)                       (model minutes x model rate)
  AM_MR           sum(mean * y_min / min_mu)      (actual minutes x model rate)
  MM_AR           sum(mean) * rate_ratio          (model minutes x actual rate)
  actual          sum(y)
  minutes_ratio = sum(y_min)/sum(min_mu) (minutes-weighted), rate_ratio = actual / AM_MR, full = actual / incumbent.
Plus: participation (p_play vs appearance; informational, props void DNP), role-change split (cold class, early),
opponent context (chi-square of per-opponent per-minute residuals vs Poisson-ish noise), distribution (variance ratio,
PIT80, P(over l0+.5) vs realized). Single process."""
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
OUT = C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results"
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
ROLE = lambda m: np.where(m >= 30, "CORE_30_PLUS", np.where(m >= 20, "ROTATION_20_TO_30", "LIMITED_UNDER_20"))


def load(ds):
    r = pd.read_parquet(Q / f"p4/p5_{ds}_t1.parquet", columns=["game_id", "pgx", "stat", "y", "mean", "var", "min_mu", "cold", "F_lo", "F_hi", "l0", "c_0", "early"])
    r = r[r.stat.isin(BASE7)]
    if ds == "LIVE":
        f = pd.read_parquet(Q / "live/oos_2027.parquet", columns=["player_game_example_id", "y_min", "team_id", "p_play"])
        f = f.rename(columns={"player_game_example_id": "pgx", "team_id": "team"})
    else:
        f = pd.read_parquet(Q / "d1_F2026.parquet", columns=["player_game_example_id", "y_min", "canonical_team_id", "p_play"])
        f = f.rename(columns={"player_game_example_id": "pgx", "canonical_team_id": "team"})
    r = r.merge(f, on="pgx", how="left")
    tg = r[["game_id", "team"]].drop_duplicates()
    opp = tg.merge(tg, on="game_id").query("team_x != team_y").rename(columns={"team_x": "team", "team_y": "opp"})
    r = r.merge(opp, on=["game_id", "team"], how="left")
    r["role"] = ROLE(r.min_mu.to_numpy())
    r["pit"] = r.F_lo + np.random.default_rng(0).random(len(r)) * (r.F_hi - r.F_lo)
    return r


def ci(y, m, g):
    y, m = np.asarray(y, float), np.asarray(m, float)
    R = y.sum() / m.sum()
    e = pd.Series(y - R * m).groupby(np.asarray(g)).sum().to_numpy()
    se = np.sqrt(len(e) / max(len(e) - 1, 1) * (e ** 2).sum()) / m.sum()
    return round(float(R), 4), round(float(R - 1.96 * se), 4), round(float(R + 1.96 * se), 4)


def decomp(h):
    p = h[h.y_min > 0]
    am_mr = p["mean"] * p.y_min / p.min_mu
    rate = p.y.sum() / am_mr.sum()
    out = {"n": int(len(h)), "n_played": int(len(p)),
           "incumbent": float(p["mean"].sum()), "AM_MR": float(am_mr.sum()), "MM_AR": float(p["mean"].sum() * rate), "actual": float(p.y.sum()),
           "full": ci(p.y, p["mean"], p.game_id), "minutes": ci(p.y_min, p.min_mu, p.game_id), "rate": ci(p.y, am_mr, p.game_id),
           "var_ratio": round(float(((h.y - h["mean"]) ** 2).sum() / h["var"].sum()), 3),
           "pit80": round(float(((h.pit > .1) & (h.pit < .9)).mean()), 3),
           "p_over": round(float((1 - h.c_0).mean()), 4), "y_over": round(float((h.y > h.l0 + .5).mean()), 4)}
    # share of the log location error explained by minutes vs rate
    lf = np.log(out["actual"] / out["incumbent"])
    out["share_minutes"] = round(float(np.log(out["minutes"][0]) / lf), 2) if abs(lf) > 1e-3 else None
    return out


def opp_context(h):
    """per-opponent per-minute residual: z = (sum y - sum am_mr) / sqrt(sum var * y_min/min_mu); chi2/df > 1 => context."""
    p = h[(h.y_min > 0) & h.opp.notna()].copy()
    p["cf"] = p["mean"] * p.y_min / p.min_mu
    p["v"] = p["var"] * p.y_min / p.min_mu
    g = p.groupby("opp").agg(y=("y", "sum"), cf=("cf", "sum"), v=("v", "sum"), n=("y", "size"))
    g = g[g.n >= 20]
    z = (g.y - g.cf) / np.sqrt(g.v)
    return {"opponents": int(len(g)), "chi2_per_df": round(float((z ** 2).mean()), 2)}


R = {}
for ds in ("LIVE", "F2026"):
    r = load(ds)
    res = {"participation": {"p_play_mean": float(r.drop_duplicates("pgx").p_play.mean()),
                             "note": "universe = appearing rows; props void DNP, so participation adds 0 to graded location bias"}}
    for s in BASE7:
        d = r[r.stat == s]
        e = {"ALL": decomp(d)}
        for role, h in d.groupby("role"):
            e[role] = decomp(h)
            e[role]["by_cold"] = {c: decomp(g)["full"] + (decomp(g)["minutes"][0], decomp(g)["rate"][0], len(g)) for c, g in h.groupby("cold") if len(g) >= 300}
            e[role]["early"] = {str(k): decomp(g)["rate"] for k, g in h.groupby("early") if len(g) >= 300}
        e["opp_context"] = opp_context(d)
        res[s] = e
    R[ds] = res
OUT.mkdir(parents=True, exist_ok=True)
json.dump(R, open(OUT / "p0_location_decomp.json", "w"), indent=1, default=float)
for ds in R:
    print(f"\n===== {ds}  (full | minutes | per-minute rate | share of log error from minutes | p_over vs y_over | var ratio)")
    for s in BASE7:
        for role in ("CORE_30_PLUS", "ROTATION_20_TO_30", "LIMITED_UNDER_20"):
            x = R[ds][s].get(role)
            if x:
                print(f"{s:5s} {role:18s} n {x['n']:6d} full {x['full'][0]:.3f} [{x['full'][1]:.3f},{x['full'][2]:.3f}] min {x['minutes'][0]:.3f} rate {x['rate'][0]:.3f} "
                      f"[{x['rate'][1]:.3f},{x['rate'][2]:.3f}] shareMin {x['share_minutes']} | over p {x['p_over']:.3f} y {x['y_over']:.3f} | vr {x['var_ratio']}")
        print(f"      opp context chi2/df {R[ds][s]['opp_context']}")
