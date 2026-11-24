"""Phase 4 / Challenger P1 (PHASE4_PROTOCOL.json challenger_P1_mean_anchor): p4_replay + per-arm mean-anchor alpha, PIT
(F(y-1), F(y)) and combos from the same joint draws. The PRODUCTION scorer (v2.jm1_final_production.score_game, frozen opening artifacts)
on point-in-time OOS inputs, arms differing only in the Phase 1B means fed to it (common random numbers).

    .venv/bin/python -B docs/audits/2026-11-22-causal/scripts/p5_replay.py F2026|LIVE <arms json> <out tag> [max_games]

arms json: {"ARM": {"a2": [stats], "min_sd": null | {"class": scale}, "anchor": "MNA"}}; INC = {} always included.
Writes per-cell rows (gitignored data dir) and an aggregate summary (results/)."""
import json, sys, time
import numpy as np, pandas as pd
from cbb_platform import config as C
from cbb_platform.prod import inputs as IN, loader as LD
from cbb_platform.v2 import jm1_final_production as JP, joint_minutes_v2 as JM, latent as L, phase3 as P3

R = C.PROJECT_ROOT
D = R / "data/qa/large/causal_20261122"
RES = R / "docs/audits/2026-11-22-causal/results"
SIMS = 2000
TGT = {"PTS": "pts", "REB": "reb", "AST": "ast", "FG3M": "three_pm", "STL": "stl", "BLK": "blk", "TOV": "tov"}
W = np.array([0.5, 0.25, 0.125])
COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"), "STOCKS": ("STL", "BLK")}
ds, arms_arg, tag = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
max_games = int(sys.argv[4]) if len(sys.argv) > 4 else None
arms = dict(arms_arg)
fit = json.loads((RES / "challenger_a2_holdout.json").read_text())["stats"]
live = json.loads((RES / "challenger_a2_live_2026_27.json").read_text())["stats"]
A2P = {s: (fit[s]["fit"]["A_full"]["alpha"], fit[s]["fit"]["A_full"]["beta"], fit[s]["fit"]["A_full"]["gamma"],
           live[s]["LA"] / live[s]["L0"]) for s in TGT}
model = LD.load()
prod = model.jm1

# ------------------------------------------------------------------ inputs: candidate rows + environment + A2 components
if ds in ("F2025", "F2026"):
    df, envp = P3.season_inputs(int(ds[1:]), prod["struct"])
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    comp = pd.read_parquet(D / f"d1_{ds}.parquet")
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
_gsrc = df if "prediction_cutoff_utc" in df else comp                                         # T1: game date
GAME_CUTOFF = _gsrc.drop_duplicates("game_id").set_index("game_id")["prediction_cutoff_utc"]
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


PV = np.array([0.11, 0.65, 0.24])             # challenger_P2: frozen TRAIN event shares (results/p2_pts_family_train_fit.json)
VB = float((PV * np.arange(1, 4)).sum())
NMAX = 100


def _vconv(kmax):
    out = np.zeros((NMAX + 1, kmax + 1)); cur = np.zeros(kmax + 1); cur[0] = 1.0
    step = np.zeros(kmax + 1); step[1:4] = PV
    for n in range(NMAX + 1):
        out[n] = cur; out[n, -1] += max(1.0 - cur.sum(), 0.0)
        cur = np.convolve(cur, step)[: kmax + 1]
    return out


VCONV = None


def compound_matrix(mu, phiN, kmax):
    """P2 PTS family: sum of N iid V in {1,2,3}, N ~ NB(mean mu / VB, var m + phiN m^2); tail folded into kmax."""
    global VCONV
    if VCONV is None or VCONV.shape[1] != kmax + 1:
        VCONV = _vconv(kmax)
    from cbb_platform import props_v0 as PZ
    pN = PZ.pmf_matrix(np.asarray(mu, float) / VB, float(phiN), NMAX)
    out = pN @ VCONV
    out[:, -1] += np.maximum(1.0 - out.sum(axis=1), 0.0)
    return out


def tables(lm, phi, pts_phiN=None):
    """= structural.mixture_tables, with the PTS conditional table optionally the P2 compound family."""
    from cbb_platform import props_v0 as PZ
    from cbb_platform.v2 import structural as S
    kmax = L.KMAX
    sims, d = lm.shape
    q = np.round(lm / S.GRID_H).astype(np.int64)
    qmin = q.min(axis=0); width = q.max(axis=0) - qmin + 1
    base = np.concatenate([[0], np.cumsum(width)[:-1]])
    key = q - qmin[None, :] + base[None, :]
    used = np.bincount(key.ravel(), minlength=int(width.sum())) > 0
    rank = np.cumsum(used) - 1; inv = rank[key]
    dim_of = np.repeat(np.arange(d), width)[used]
    grid = (np.concatenate([np.arange(w) for w in width]) + np.repeat(qmin, width))[used]
    mu = np.exp(grid * S.GRID_H); so = dim_of % S.NS
    pmf = np.empty((len(mu), kmax + 1))
    for j in range(S.NS):
        m = so == j
        if m.any():
            pmf[m] = (compound_matrix(mu[m], pts_phiN, kmax) if (j == 0 and pts_phiN is not None)
                      else PZ.pmf_matrix(mu[m], float(phi[j]), kmax))
    cdf = np.minimum(np.maximum.accumulate(np.cumsum(pmf, axis=1), axis=1), 1.0); cdf[:, -1] = 1.0
    thr = np.full(cdf.shape, S.THR_CAP); need = (cdf < 1.0 - 1e-15)
    with np.errstate(divide="ignore"):
        thr[need] = np.clip(L.ndtri(np.clip(cdf[need], 1e-300, 1.0)), -S.THR_CAP, S.THR_CAP)
    starts = np.searchsorted(dim_of, np.arange(d + 1), side="left")
    return {"inv": inv, "starts": starts, "pmf": pmf, "cdf": cdf, "thr": thr}


def score_mna(prod, gid, gp, envd, sims, mna=True, pts_phiN=None):
    """= JP.score_game with the minutes-neutral anchor (challenger_P1_mean_anchor.fix) and / or the P2 PTS family."""
    from cbb_platform.v2 import dependence as D, jm1_final as F, joint as J, structural as S
    gi = P3._game_input(gid, gp, envd)
    rows = gp[gp["_scored"]].reset_index(drop=True)
    n = len(rows)
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    gc = D.game_correlation(prod["residual_structure"], list(D.group_of(rows)), team)
    Lc, jit = D.cholesky_unit(gc["corr"])
    z = np.random.default_rng(D.game_seed(J.SIM_SEED, gid)).standard_normal((sims, n * J.NS)) @ Lc.T
    dur, x, Mx = F.simulate_exposure(gi, prod["struct"]["env"], prod["e_dur"], sims)
    base_lm = np.log(rows[[f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float))
    sv = S.State(dur, x, Mx, np.zeros((sims, n, len(S.FAM))), np.zeros((sims, 0)), {}, np.zeros((n, len(S.FAM))), 0.0)
    mm = rows["min_mu"].to_numpy(dtype=float)
    full = S.log_mu_terms(base_lm, mm, team, sv, prod["struct"]["glm"], JM.JM_TERMS)
    nom = S.log_mu_terms(base_lm, mm, team, sv, prod["struct"]["glm"], tuple(t for t in JM.JM_TERMS if t != "log_minutes_ratio"))
    lm = full + (np.log(np.exp(nom).mean(axis=0)) - np.log(np.exp(full).mean(axis=0)))[None] if mna else S.anchor(full, base_lm, JM.JM1_ALPHA)[0]
    tab = tables(lm.reshape(sims, n * J.NS), np.array([prod["s4"]["dispersion"][s]["phi"] for s in L.STATS]), pts_phiN)
    draws = S.counts(tab, z).reshape(sims, n, J.NS)
    return {"rows": rows, "draws": draws, "pmf": S.mixture_pmf(tab, sims).reshape(n, J.NS, -1)}


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
        if spec.get("tov_trend"):                         # challenger T1 (PHASE4_PROTOCOL tov_blk_reevaluation)
            tt = spec["tov_trend"]
            ts = pd.to_datetime(gp["game_id"].map(GAME_CUTOFF), utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
            yr = ts.dt.year.where(ts.dt.month >= 7, ts.dt.year - 1)
            u = (ts - pd.to_datetime(yr.astype(str) + "-11-01")).dt.days.to_numpy(float) / 100.0
            gp["TOV_mu"] = gp["TOV_mu"].to_numpy(float) * np.exp(tt["b"] * (u - tt["ubar"]))
        if spec.get("min_sd"):
            cls = gp["cold_start_class"].astype(str)
            gp["min_sigma"] = gp["min_sigma"] * cls.map(spec["min_sd"]).fillna(1.0).to_numpy(float)
        if spec.get("anchor") == "MNA" or "pts_phiN" in spec:
            r = score_mna(prod, gid, gp, envd, SIMS, mna=spec.get("anchor") == "MNA", pts_phiN=spec.get("pts_phiN"))
        else:
            r = JP.score_game(prod, gid, gp, envd, SIMS)
        rows, pmf = r["rows"], r["pmf"]
        K = pmf.shape[2]
        base_mean = {}
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
            Fy = cdf[np.arange(len(y)), yi]
            base_mean[s] = m
            l0 = np.floor(m).astype(int)                            # main line l0 + 0.5; alternates +-2
            cl = {o: cdf[np.arange(len(y)), np.clip(l0 + o, 0, K - 1)] * (l0 + o >= 0) for o in (-2, 0, 2)}
            for i in range(len(y)):
                cells.append((arm, gid, rows.at[i, "player_game_example_id"], s, float(y[i]), float(m[i]), float(v[i]),
                              float(-np.log(max(p_y[i], 1e-300))), float(rps[i]), float(rows.at[i, "min_mu"]),
                              str(rows.at[i, "cold_start_class"]), float(Fy[i] - p_y[i]), float(Fy[i]),
                              float(P[i, 0]), float(P[i, 1]), int(l0[i]), float(cl[-2][i]), float(cl[0][i]), float(cl[2][i])))
        dr = r["draws"]                                             # sims x n x 7, the production combo source
        ix = {s: j for j, s in enumerate(L.STATS)}
        for cname, parts in COMBOS.items():
            cd = sum(dr[:, :, ix[p]] for p in parts).astype(np.int64)  # sims x n
            y = sum(rows[f"y_{p}"].to_numpy(float) for p in parts)
            Kc = int(max(cd.max(), y.max())) + 2
            l0c = np.floor(sum(base_mean[p] for p in parts)).astype(int)
            for i in range(len(y)):
                h = np.bincount(cd[:, i], minlength=Kc)[:Kc] / cd.shape[0]
                c_ = np.cumsum(h); yi = int(round(y[i]))
                rps = float(((c_[:-1] - (np.arange(Kc - 1) >= yi)) ** 2).sum())
                p_y = (h[yi] * cd.shape[0] + 0.5) / (cd.shape[0] + 0.5 * Kc)
                cg = lambda o: float(c_[min(l0c[i] + o, Kc - 1)]) if l0c[i] + o >= 0 else 0.0
                cells.append((arm, gid, rows.at[i, "player_game_example_id"], cname, float(y[i]), float(cd[:, i].mean()),
                              float(cd[:, i].var()), float(-np.log(p_y)), rps, float(rows.at[i, "min_mu"]),
                              str(rows.at[i, "cold_start_class"]), float(c_[yi] - h[yi]), float(c_[yi]),
                              float(h[0]), float(h[1]), int(l0c[i]), cg(-2), cg(0), cg(2)))
    if gi % 100 == 0:
        print(f"{gi}/{len(games)} {time.time() - t0:.0f}s", flush=True)
cdf_ = pd.DataFrame(cells, columns=["arm", "game_id", "pgx", "stat", "y", "mean", "var", "nll", "rps", "min_mu", "cold", "F_lo", "F_hi",
                                    "p0", "p1", "l0", "c_m2", "c_0", "c_p2"])
cdf_["early"] = cdf_["pgx"].map(early_day).le(14)
(D / "p4").mkdir(exist_ok=True)
cdf_.to_parquet(D / f"p4/p5_{ds}_{tag}.parquet", index=False)
print("cells", len(cdf_), "games", len(games), f"{time.time() - t0:.0f}s")
