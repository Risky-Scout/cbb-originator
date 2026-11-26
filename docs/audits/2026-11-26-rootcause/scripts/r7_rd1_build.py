"""RD1 (ROOT_CAUSE_PROTOCOL.json): absent-regular redistribution. TRAIN fit on F2025 rows only; multipliers for F2026 / LIVE:
data/qa/large/rootcause_20261126/mult_{DS}_RD1.parquet = ALLB multipliers x RD1. Results: results/r7_rd1_fit.json.
Grid built from the season's own played rows (strictly earlier appearances): regular = trailing-5-appearance minutes >= 24
and played the team's previous game; absent = no played row in this team game."""
import json, os
import numpy as np, pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122")
D = os.path.join(ROOT, "data/qa/large/defects_20261125")
O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
STATS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
BANDS = [0, 10, 20, 28, 99]
BL = ["<10", "10-20", "20-28", "28+"]


def rows(ds):
    yc = ["y_min"] + [f"y_{s}" for s in STATS]
    if ds == "LIVE":
        r = pd.read_parquet(f"{Q}/live/oos_2027.parquet", columns=["player_game_example_id", "game_id", "team_id", "player_id", "prediction_cutoff_utc"] + yc)
    else:
        r = pd.read_parquet(f"{Q}/d1_{ds}.parquet", columns=["player_game_example_id", "game_id", "canonical_team_id", "canonical_player_id", "prediction_cutoff_utc"] + yc)
        r = r.rename(columns={"canonical_team_id": "team_id", "canonical_player_id": "player_id"})
    r = r.rename(columns={"player_game_example_id": "pgx"})
    r["tip"] = pd.to_datetime(r.prediction_cutoff_utc, utc=True)
    return r[r.y_min.fillna(0) > 0]


def grid(r):
    tg = r[["game_id", "team_id", "tip"]].groupby(["game_id", "team_id"]).tip.min().reset_index().sort_values(["team_id", "tip"])
    tg["k"] = tg.groupby("team_id").cumcount()
    r = r.merge(tg[["game_id", "team_id", "k"]], on=["game_id", "team_id"])
    first = r.groupby(["player_id", "team_id"]).k.min().rename("k0").reset_index()
    G = tg.merge(first, on="team_id")
    G = G[G.k >= G.k0].merge(r[["game_id", "team_id", "player_id", "pgx", "y_min"] + [f"y_{s}" for s in STATS]],
                             on=["game_id", "team_id", "player_id"], how="left")
    G["played"] = G.y_min.fillna(0) > 0
    G = G.sort_values(["player_id", "team_id", "k"])
    key = [G.player_id, G.team_id]
    for v in ["y_min"] + [f"y_{s}" for s in STATS]:
        s = G[v].where(G.played)
        t = s.groupby(key).transform(lambda x: x.shift(1).rolling(5, min_periods=3).mean())
        G[v + "5"] = t.groupby(key).ffill() if False else t
        G[v + "5"] = G.groupby(["player_id", "team_id"])[v + "5"].ffill()
    G["prev_played"] = G.groupby(["player_id", "team_id"]).played.shift(1).fillna(False).astype(bool)
    G["absent_reg"] = (G.y_min5 >= 24) & G.prev_played & ~G.played
    lost = G[G.absent_reg].groupby(["game_id", "team_id"])[[f"y_{s}5" for s in STATS]].sum().add_prefix("lost_")
    P = G[G.played].copy()
    tot = P.groupby(["game_id", "team_id"])[[f"y_{s}5" for s in STATS]].sum().add_prefix("tot_")
    P = P.join(lost, on=["game_id", "team_id"]).join(tot, on=["game_id", "team_id"])
    for s in STATS:
        P[f"L_{s}"] = (P[f"lost_y_{s}5"].fillna(0) / P[f"tot_y_{s}5"].clip(lower=1e-6)).clip(0, 1.5)
    P["band"] = pd.cut(P.y_min5.fillna(0), BANDS, labels=BL, right=False).astype(str)
    P["has_abs"] = P[f"lost_y_PTS5"].fillna(0) > 0
    return P


def fit(P):
    co = {}
    for s in STATS:
        co[s] = {}
        for b in BL:
            q = P[(P.band == b) & P[f"y_{s}5"].gt(0) & P[f"y_{s}"].notna()]
            r = (q[f"y_{s}"] / q[f"y_{s}5"] - 1).to_numpy(); w = q[f"y_{s}5"].to_numpy(); L = q[f"L_{s}"].to_numpy()
            X = np.c_[np.ones(len(q)), L]
            A = X.T @ (w[:, None] * X) + np.diag([0.0, 1.0]); bb = X.T @ (w * r)
            c = np.linalg.solve(A, bb)
            co[s][b] = {"theta": float(c[1]), "intercept_not_applied": float(c[0]), "n": int(len(q)), "n_abs_rows": int((L > 0).sum())}
    return co


P25 = grid(rows("F2025"))
co = fit(P25)
res = {"coef_TRAIN_F2025": co}
for ds in ("F2026", "LIVE"):
    P = grid(rows(ds))
    a = pd.read_parquet(f"{D}/mult_{ds}_ALLB.parquet").merge(P[["pgx", "band", "has_abs"] + [f"L_{s}" for s in STATS]], on="pgx", how="left")
    a["band"] = a["band"].fillna("<10"); a["has_abs"] = a["has_abs"].fillna(False)
    for s in STATS:
        th = a.band.map({b: co[s][b]["theta"] for b in BL}).to_numpy(float)
        adj = 1 + th * a[f"L_{s}"].fillna(0).to_numpy()
        a[f"{s}_m"] = np.clip(a[f"{s}_m"].to_numpy() * adj, 0.7, 1.6)
    a[["pgx"] + [f"{s}_m" for s in STATS]].to_parquet(f"{O}/mult_{ds}_RD1.parquet", index=False)
    a[["pgx", "has_abs", "band"]].to_parquet(f"{O}/rd1_rows_{ds}.parquet", index=False)
    res[ds] = {"rows": int(len(a)), "rows_in_absent_team_games": int(a.has_abs.sum()),
               "team_games_with_absence": int(P[P.has_abs][["game_id", "team_id"]].drop_duplicates().shape[0])}
json.dump(res, open(f"{RES}/r7_rd1_fit.json", "w"), indent=1)
print(json.dumps({s: {b: round(v["theta"], 3) for b, v in co[s].items()} for s in STATS}))
print({ds: res[ds] for ds in ("F2026", "LIVE")})
