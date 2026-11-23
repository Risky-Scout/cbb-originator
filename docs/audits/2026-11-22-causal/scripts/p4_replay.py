"""Phase 4 (PHASE4_PROTOCOL.json): the PRODUCTION scorer (v2.jm1_final_production.score_game, frozen opening artifacts)
on point-in-time OOS inputs, arms differing only in the Phase 1B means fed to it (common random numbers).

    .venv/bin/python -B docs/audits/2026-11-22-causal/scripts/p4_replay.py F2026|LIVE <arms json> <out tag> [max_games]

arms json: {"ARM": {"a2": [stats], "min_sd": null | {"class": scale}}}; INC = {} always included.
Writes per-cell rows (gitignored data dir) and an aggregate summary (results/)."""
import json, sys, time
import numpy as np, pandas as pd
from cbb_platform import config as C
from cbb_platform.prod import inputs as IN, loader as LD
from cbb_platform.v2 import jm1_final_production as JP, latent as L, phase3 as P3

R = C.PROJECT_ROOT
D = R / "data/qa/large/causal_20261122"
RES = R / "docs/audits/2026-11-22-causal/results"
SIMS = 2000
TGT = {"PTS": "pts", "REB": "reb", "AST": "ast", "FG3M": "three_pm", "STL": "stl", "BLK": "blk", "TOV": "tov"}
W = np.array([0.5, 0.25, 0.125])
ds, arms_arg, tag = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
max_games = int(sys.argv[4]) if len(sys.argv) > 4 else None
arms = {"INC": {}, **arms_arg}
fit = json.loads((RES / "challenger_a2_holdout.json").read_text())["stats"]
live = json.loads((RES / "challenger_a2_live_2026_27.json").read_text())["stats"]
A2P = {s: (fit[s]["fit"]["A_full"]["alpha"], fit[s]["fit"]["A_full"]["beta"], fit[s]["fit"]["A_full"]["gamma"],
           live[s]["LA"] / live[s]["L0"]) for s in TGT}
model = LD.load()
prod = model.jm1

# ------------------------------------------------------------------ inputs: candidate rows + environment + A2 components
if ds == "F2026":
    df, envp = P3.season_inputs(2026, prod["struct"])
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    comp = pd.read_parquet(D / "d1_F2026.parquet")
    comp = comp.rename(columns={**{f"{s}_{c}": f"{c}_{t}" for s, t in TGT.items() for c in ("cur", "h1", "h2", "h3")}})
    st = {"prior_k_minutes": {t: float(comp[f"{s}_k"].iloc[0]) for s, t in TGT.items()}}
    prior_col = {t: comp.set_index("player_game_example_id")[f"{s}_prior"] for s, t in TGT.items()}
    early_day = comp.set_index("player_game_example_id")["season_day"]
else:
    df = pd.read_parquet(D / "live/oos_2027.parquet")
    df["_scored"] = P3.scored_mask(df)
    df = df[df["game_id"].isin(df.loc[df["_scored"], "game_id"].unique())]
    df = df.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)
    envd = IN.environment(pd.read_parquet(D / "live/team_2027.parquet"), prod["struct"]["env"])
    comp = pd.read_parquet(D / "live/frame_2027.parquet")
    v0 = json.loads((D / "live/opening_v0_state.json").read_text())
    st = {"prior_k_minutes": v0["prior_k_minutes"]}
    prior_col = {t: comp.set_index("player_game_example_id")["pos"].map(v0["prior_rate"][t]) for t in TGT.values()}
    ts = pd.to_datetime(comp.prediction_cutoff_utc, utc=True)
    early_day = pd.Series((ts - ts.min()).dt.days.to_numpy(), index=comp.player_game_example_id)
comp = comp.set_index("player_game_example_id")


def a2_means(rows: pd.DataFrame, stats) -> dict:
    """stat -> A2 mean per row (NaN where the components are unavailable): research formula = prod.rate_a2."""
    c = comp.reindex(rows["player_game_example_id"].to_numpy())
    f = lambda k: c[k].fillna(0).to_numpy(float) if k in c else np.zeros(len(c))
    mm = rows["min_mu"].to_numpy(float)
    hm = np.column_stack([f(f"h{j}_min") for j in (1, 2, 3)])
    H = f("cur_min") + (hm * W).sum(1)
    n1 = f("h1_n_app")
    mpa = f("h1_min") / np.maximum(n1, 1)
    Dr = np.where(n1 >= 5, np.clip(np.log(np.maximum(mm, 1) / np.maximum(mpa, 1)), -1.5, 1.5), 0.0)
    z = np.where(H > 0, np.log(np.maximum(mm, 1) / 20.0), 0.0)
    have = c["cur_min"].notna().to_numpy() | c["h1_min"].notna().to_numpy() if "cur_min" in c else np.zeros(len(c), bool)
    out = {}
    for s in stats:
        t = TGT[s]; al, be, ga, sc = A2P[s]
        k = float(st["prior_k_minutes"][t])
        pr = prior_col[t].reindex(rows["player_game_example_id"].to_numpy()).to_numpy(float)
        h = np.column_stack([f(f"h{j}_{t}") for j in (1, 2, 3)])
        num = f(f"cur_{t}") + np.exp(be * Dr) * (h * (W * np.exp(al * np.arange(1, 4)))).sum(1) + k * pr * np.exp(ga * z)
        mu = np.maximum(mm * num / (H + k) * sc, 0.01)
        out[s] = np.where(np.isfinite(mu) & (have | ~np.isnan(pr)), mu, np.nan)
    return out


cells = []
games = sorted(df["game_id"].unique())[:max_games]
t0 = time.time()
for gi, gid in enumerate(games):
    gp0 = df[df["game_id"] == gid].reset_index(drop=True)
    for arm, spec in arms.items():
        gp = gp0.copy()
        if spec.get("a2"):
            for s, mu in a2_means(gp, spec["a2"]).items():
                gp[f"{s}_mu"] = np.where(np.isnan(mu), gp[f"{s}_mu"], mu)
        if spec.get("min_sd"):
            cls = gp["cold_start_class"].astype(str)
            gp["min_sigma"] = gp["min_sigma"] * cls.map(spec["min_sd"]).fillna(1.0).to_numpy(float)
        r = JP.score_game(prod, gid, gp, envd, SIMS)
        rows, pmf = r["rows"], r["pmf"]
        K = pmf.shape[2]
        for j, s in enumerate(L.STATS):
            y = rows[f"y_{s}"].to_numpy(float)
            P = pmf[:, j, :]
            yi = np.clip(np.round(y).astype(int), 0, K - 1)
            p_y = P[np.arange(len(y)), yi]
            cdf = np.cumsum(P, axis=1)
            rps = ((cdf[:, :-1] - (np.arange(K - 1)[None, :] >= yi[:, None])) ** 2).sum(1)
            k = np.arange(K)
            m = (P * k).sum(1)
            v = (P * (k[None, :] - m[:, None]) ** 2).sum(1)
            for i in range(len(y)):
                cells.append((arm, gid, rows.at[i, "player_game_example_id"], s, float(y[i]), float(m[i]), float(v[i]),
                              float(-np.log(max(p_y[i], 1e-300))), float(rps[i]), float(rows.at[i, "min_mu"]),
                              str(rows.at[i, "cold_start_class"])))
    if gi % 100 == 0:
        print(f"{gi}/{len(games)} {time.time() - t0:.0f}s", flush=True)
cdf_ = pd.DataFrame(cells, columns=["arm", "game_id", "pgx", "stat", "y", "mean", "var", "nll", "rps", "min_mu", "cold"])
cdf_["early"] = cdf_["pgx"].map(early_day).le(14)
(D / "p4").mkdir(exist_ok=True)
cdf_.to_parquet(D / f"p4/replay_{ds}_{tag}.parquet", index=False)
print("cells", len(cdf_), "games", len(games), f"{time.time() - t0:.0f}s")
