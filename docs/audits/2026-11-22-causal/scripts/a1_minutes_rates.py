"""A1: minutes (phase B) and per-minute rate (phase C) decomposition on D1 (deterministic; aggregates only).
Mean error convention: forecast - actual (positive = over-forecast).
Decomposition of a count error, exact per row:  mu - y = rate*(min_mu - y_min)  +  y_min*(rate - y/y_min)
                                                          [minutes part]          [rate part, at realized minutes]
"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from math import erf
class norm:
    @staticmethod
    def ppf(q): return {0.75: 0.6744897501960817, 0.90: 1.2815515655446004}[q]
    @staticmethod
    def cdf(x): return 0.5 * (1 + np.vectorize(erf)(np.asarray(x) / np.sqrt(2)))
from cbb_platform import config as C
D = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
d = pd.concat([pd.read_parquet(D / f"d1_F{F}.parquet") for F in (2024, 2025, 2026)], ignore_index=True)
S7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
h1_mpa = d.h1_min / d.h1_n_app
d["has_h1"] = d.h1_n_app.fillna(0) >= 5
d["role_change"] = np.where(d.has_h1, np.log(np.maximum(d.min_mu, 1) / np.maximum(h1_mpa, 1)), np.nan)
d["rc_bucket"] = pd.cut(d.role_change, [-9, -0.4, -0.15, 0.15, 0.4, 9],
                        labels=["DECREASE_BIG", "DECREASE", "STABLE", "INCREASE", "INCREASE_BIG"])
d["role"] = np.select([d.min_mu >= 30, d.min_mu >= 20, d.min_mu >= 10], ["STARTER_30P", "ROTATION_20_30", "BENCH_10_20"], "LOW_LT10")
d["evidence"] = pd.cut(d.cur_min.fillna(0) / d.H_min.replace(0, np.nan), [-0.01, 0.1, 0.25, 0.5, 0.75, 1.01],
                       labels=["cur<10%", "10-25%", "25-50%", "50-75%", "75%+"])
d["transfer"] = np.where(d.prof_portal_transfer_in.fillna(0) > 0, "TRANSFER_IN",
                         np.where(d.pps_same_team.fillna(1) == 0, "NEW_TEAM_OTHER", "SAME_TEAM"))
d["returning"] = np.where(d.pp_games_since_played.fillna(0) >= 3, "RETURN_FROM_ABSENCE", "CONTINUOUS")
d["early"] = np.where(d.season_day <= 30, "D0_30", "D31P")
d["cold"] = d.cold_start_class.astype(str)
# ---------------- B. minutes
z = (d.y_min - d.min_mu) / d.min_sigma
d["min_err"] = d.min_mu - d.y_min
d["cov50"] = (np.abs(z) <= norm.ppf(0.75)).astype(float)
d["cov80"] = (np.abs(z) <= norm.ppf(0.90)).astype(float)
d["pit"] = norm.cdf(z)

def mins(g):
    e = g.min_err
    return pd.Series({"n": len(g), "mean_err": e.mean(), "se": e.std() / np.sqrt(len(g)), "mae": e.abs().mean(),
                      "rmse": np.sqrt((e ** 2).mean()), "cov50": g.cov50.mean(), "cov80": g.cov80.mean(),
                      "sd_ratio": e.std() / np.sqrt((g.min_sigma ** 2).mean())})
res = {"B_minutes": {}}
for key in ("fold", "role", "rc_bucket", "transfer", "returning", "early", "cold", "evidence"):
    res["B_minutes"][key] = d.groupby(key, observed=True).apply(mins).round(4).to_dict(orient="index")
# ---------------- C. rate (at realized minutes) + minutes part, per stat
res["C_rate"] = {}
for S in S7:
    ok = d[f"y_{S}"].notna()
    x = d[ok]
    rate_part = x.y_min * x[f"{S}_rate"] - x[f"y_{S}"]
    min_part = x[f"{S}_rate"] * (x.min_mu - x.y_min)
    def cell(g, rp=rate_part, mp=min_part):
        i = g.index
        return pd.Series({"n": len(g), "mean_err": (x.loc[i, f"{S}_mu"] - x.loc[i, f"y_{S}"]).mean(),
                          "minutes_part": mp.loc[i].mean(), "rate_part": rp.loc[i].mean(),
                          "rate_part_se": rp.loc[i].std() / np.sqrt(len(i)),
                          "realized_over_model_rate": x.loc[i, f"y_{S}"].sum() / (x.loc[i, "y_min"] * x.loc[i, f"{S}_rate"]).sum()})
    res["C_rate"][S] = {key: x.groupby(key, observed=True).apply(cell).round(4).to_dict(orient="index")
                        for key in ("fold", "role", "rc_bucket", "evidence", "transfer", "returning", "early")}
    # key causal test: log realized/model rate (count-weighted Poisson pseudo-regression via grouped ratio) vs role change,
    # separately among HIGH-EVIDENCE rows (cur >= 50% of history: prior/blend weight small) -- if the role-change effect
    # survives where the history blend has little weight, the defect is not the blend.
    h = x[x.has_h1]
    for lab, m in (("all_h1", np.ones(len(h), bool)), ("cur_lt25pct", (h.cur_min.fillna(0) / h.H_min) < 0.25),
                   ("cur_ge50pct", (h.cur_min.fillna(0) / h.H_min) >= 0.5)):
        g = h[m]
        # Poisson GLM: y ~ exp(a + b*role_change) * y_min*rate ; IRLS 2 params
        X = np.column_stack([np.ones(len(g)), g.role_change.to_numpy()])
        off = np.log(np.maximum(g.y_min * g[f"{S}_rate"], 1e-9)).to_numpy()
        y = g[f"y_{S}"].to_numpy(); b = np.zeros(2)
        for _ in range(50):
            mu = np.exp(off + X @ b); H = (X * mu[:, None]).T @ X; st = np.linalg.solve(H, X.T @ (y - mu)); b += st
            if np.abs(st).max() < 1e-10: break
        mu = np.exp(off + X @ b); Hi = np.linalg.inv((X * mu[:, None]).T @ X); Sg = X * (y - mu)[:, None]
        se = np.sqrt(np.diag(Hi @ (Sg.T @ Sg) @ Hi))
        res["C_rate"][S].setdefault("role_change_slope", {})[lab] = {"n": int(len(g)), "intercept": round(b[0], 4),
                                                                     "slope": round(b[1], 4), "se": round(se[1], 4)}
(D / "a1_results.json").write_text(json.dumps(res, indent=1, default=str))
pd.set_option("display.width", 220)
for key in ("role", "rc_bucket", "transfer", "returning", "early", "cold"):
    print("MINUTES by", key); print(pd.DataFrame(res["B_minutes"][key]).T.to_string())
for S in ("PTS", "AST", "REB", "FG3M"):
    for key in ("role", "rc_bucket", "evidence"):
        print(S, "RATE by", key); print(pd.DataFrame(res["C_rate"][S][key]).T.to_string())
for S in S7:
    print(S, "role_change_slope", res["C_rate"][S]["role_change_slope"])
