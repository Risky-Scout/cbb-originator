"""PI1 (ROOT_CAUSE_PROTOCOL.json): TRAIN fit on F2025 PRODX3 only, then per-row multipliers for F2026 / LIVE:
data/qa/large/rootcause_20261126/mult_{DS}_PI1.parquet = ALLB multipliers x PI1 adjustment. Writes results/r4_pi1_fit.json."""
import json, os
import numpy as np, pandas as pd
from cbb_platform.prod import dev_trend as DV

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122")
D = os.path.join(ROOT, "data/qa/large/defects_20261125")
O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
STATS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
PANEL = pd.read_parquet(f"{O}/transition_panel.parquet").dropna(subset=["canonical_player_id"]).drop_duplicates(["canonical_player_id", "season"])
FIN = pd.read_parquet(f"{O}/team_finals.parquet")
STR = {(t, s): v for t, s, v in zip(FIN.team, FIN.season, FIN.strength)}


def frame(ds):
    cols = ["player_game_example_id", "canonical_player_id", "canonical_team_id", "season", "cur_min", "h1_min", "h2_min",
            "h3_min", "pps_same_team", "pps_min_per_app"]
    f = pd.read_parquet(f"{Q}/live/frame_2027.parquet" if ds == "LIVE" else f"{Q}/d1_{ds}.parquet", columns=cols)
    f = f.rename(columns={"player_game_example_id": "pgx"})
    h = 0.5 * f.h1_min.fillna(0) + 0.25 * f.h2_min.fillna(0) + 0.125 * f.h3_min.fillna(0)
    f["s_prev"] = h / (f.cur_min.fillna(0) + h + DV.K_MINUTES)
    f["w_nh"] = 31.0 / (f.cur_min.fillna(0) + 31.0)
    f["same"] = f.pps_same_team.fillna(0) == 1
    f = f.merge(PANEL[["canonical_player_id", "season", "cls", "origin_team"]], on=["canonical_player_id", "season"], how="left")
    f["cls"] = f["cls"].fillna("NO_PRIOR_HISTORY")
    dest = np.array([STR.get((t, s - 1), np.nan) for t, s in zip(f.canonical_team_id, f.season)])
    orig = np.array([STR.get((t, s - 1), np.nan) if isinstance(t, str) else np.nan for t, s in zip(f.origin_team, f.season)])
    f["z_d"] = np.clip(np.nan_to_num(dest - orig, nan=0.0), -25, 25) / 10.0
    f["z_m"] = (np.clip(f.pps_min_per_app.fillna(20.0), 0, 36) - 20.0) / 8.0
    return f


def fit():
    r = pd.read_parquet(f"{Q}/p4/p5_F2025_train3.parquet", columns=["arm", "pgx", "stat", "y", "mean", "min_mu"])
    r = r[(r.arm == "PRODX3") & r.y.notna() & (r["mean"] > 0)].merge(frame("F2025"), on="pgx")
    out, diag = {}, {}
    for s in STATS:
        q = r[r.stat == s]
        res = ((q.y - q["mean"]) / q["mean"]).to_numpy(); w = q["mean"].to_numpy()
        t = (q.cls == "TRANSFER").to_numpy()
        X = (q.s_prev.to_numpy()[:, None] * np.c_[np.ones(len(q)), q.z_d, q.z_m])[t]
        A = X.T @ (w[t, None] * X) + 4.0 * np.eye(3); b = X.T @ (w[t] * res[t])
        coef = np.linalg.solve(A, b)
        n = (q.cls == "NO_PRIOR_HISTORY").to_numpy()
        x = q.w_nh.to_numpy()[n]
        e = float((x * w[n] * res[n]).sum() / ((x * x * w[n]).sum() + 4.0))
        out[s] = {"a": float(coef[0]), "b": float(coef[1]), "c": float(coef[2]), "e": e}
        diag[s] = {"transfer_rows": int(t.sum()), "no_history_rows": int(n.sum())}
    return out, diag


def mults(ds, co):
    f = frame(ds)
    a = pd.read_parquet(f"{D}/mult_{ds}_ALLB.parquet").merge(f, on="pgx", how="left")
    rep = {}
    for s in STATS:
        dv = 1 + a.s_prev.fillna(0) * np.where(a.same, DV.DELTA[s][0], DV.DELTA[s][1])
        c = co[s]
        tr = (a.cls == "TRANSFER").to_numpy()
        nh = (a.cls == "NO_PRIOR_HISTORY").to_numpy()
        adj = np.ones(len(a))
        adj[tr] = ((1 + a.s_prev * (c["a"] + c["b"] * a.z_d + c["c"] * a.z_m)) / dv).to_numpy()[tr]
        adj[nh] = (1 + a.w_nh * c["e"]).to_numpy()[nh]
        adj = np.nan_to_num(adj, nan=1.0)
        m = np.clip(a[f"{s}_m"].to_numpy() * adj, 0.7, 1.3)
        a[f"{s}_m"] = m
        rep[s] = {"transfer_adj_mean": float(adj[tr].mean()) if tr.any() else None, "nh_adj_mean": float(adj[nh].mean()) if nh.any() else None,
                  "adj_range": [float(adj.min()), float(adj.max())]}
    a[["pgx"] + [f"{s}_m" for s in STATS]].to_parquet(f"{O}/mult_{ds}_PI1.parquet", index=False)
    rep["rows"] = int(len(a)); rep["cls"] = a.cls.value_counts().to_dict()
    return rep


co, diag = fit()
res = {"coef_TRAIN_F2025": co, "train_rows": diag}
for ds in ("F2026", "LIVE"):
    res[ds] = mults(ds, co)
json.dump(res, open(f"{RES}/r4_pi1_fit.json", "w"), indent=1, default=str)
print(json.dumps(res["coef_TRAIN_F2025"], indent=0))
print({ds: {s: res[ds][s] for s in ("PTS", "AST")} for ds in ("F2026", "LIVE")})
