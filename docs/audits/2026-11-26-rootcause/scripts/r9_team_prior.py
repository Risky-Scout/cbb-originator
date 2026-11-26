"""TP1 (ROOT_CAUSE_PROTOCOL.json): preseason team roster prior. Stage A (default): features + rolling team-level OOS
(results/r9_team_prior_teamlevel.json, data/qa/large/rootcause_20261126/tp1_priors.parquet). Stage B ("walk"): game-level
walk-forward 2024 / 2025 / 2027 with the TP1 strength prior vs GC3 (results/r9_team_prior_games.json)."""
import gzip, glob, json, os, sys
import duckdb, numpy as np, pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
SP = os.path.join(ROOT, "../CBB_MOV_GT_TT/CBB_Player_Input_Game_Level_Output/starter_pack_2026/data")
O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
LOCK = os.path.join(ROOT, "data/lockbox_2025_26/canonical/cbb.duckdb")
FEAT = ["x0", "x0ret", "RV", "LV", "TV", "TVd", "RC"]


def features():
    c = duckdb.connect(LOCK, read_only=True)
    pmap = dict(c.execute("select provider_player_key, canonical_player_id from metadata.player_id_map where provider='CBBD'").fetchall())
    tmap = dict(c.execute("select provider_team_key, canonical_team_id from metadata.team_id_map where provider='CBBD'").fetchall())
    ros = c.execute("select distinct canonical_player_id p, canonical_team_id t, season from core.rosters where season between 2015 and 2026").df()
    rc = c.execute("""select committed_source_team_id st, recruit_year y, coalesce(rating, 0) rating from core.recruits
                      where committed_source_team_id is not null""").df()
    c.close()
    b = json.load(gzip.open(glob.glob(f"{O}/cbbd_prod/20261112*.json.gz")[0]))
    r27 = pd.DataFrame([{"p": pmap.get(str(p["id"])), "t": tmap.get(str(t["teamId"])), "season": 2027} for t in b for p in t["players"]])
    ros = pd.concat([ros, r27.dropna(subset=["t"])], ignore_index=True)
    rec27 = json.load(gzip.open(glob.glob(f"{O}/cbbd_prod/20261020*.json.gz")[0]))
    rc = pd.concat([rc, pd.DataFrame([{"st": (x.get("committedTo") or {}).get("id"), "y": 2026, "rating": x.get("rating") or 0} for x in rec27])])
    rc["t"] = rc.st.map(lambda v: tmap.get(str(int(v))) if pd.notna(v) else None)
    RC = rc.dropna(subset=["t"]).groupby(["t", "y"]).rating.sum()
    fin = pd.read_parquet(f"{O}/team_finals.parquet")
    STR = {(t, s): v for t, s, v in zip(fin.team, fin.season, fin.strength)}
    ps = {}
    for y in range(2014, 2027):
        d = pd.read_csv(f"{SP}/player_season_stats/{y}.csv", usecols=["teamId", "athleteId", "games", "minutes", "PORPAG"])
        d = d.sort_values("minutes", ascending=False).drop_duplicates("athleteId")
        d["p"] = d.athleteId.astype(str).map(pmap); d["t"] = d.teamId.astype(str).map(tmap)
        d["v"] = d.PORPAG.fillna(0) * d.games.fillna(0) / 30.0
        ps[y] = d.dropna(subset=["p", "t"])
    rows = []
    for s in range(2015, 2028):
        prev = ps[s - 1]
        tmin = prev.groupby("t").minutes.sum()
        R = ros[ros.season == s].dropna(subset=["p"]).merge(prev[["p", "t", "minutes", "v"]].rename(columns={"t": "t_prev"}), on="p", how="left")
        R["ret"] = R.t == R.t_prev
        R["trn"] = R.t_prev.notna() & ~R.ret
        R["dstr"] = [np.clip(STR.get((a, s - 1), 0.0) - STR.get((b2, s - 1), 0.0), -25, 25) / 10 if b2 is not None and isinstance(b2, str) else 0.0
                     for a, b2 in zip(R.t, R.t_prev)]
        onr = set(zip(R.p, R.t))
        lost = prev[[(p, t) not in onr for p, t in zip(prev.p, prev.t)]].groupby("t").v.sum()
        g = R.groupby("t").apply(lambda q: pd.Series({
            "RETmin": q.loc[q.ret, "minutes"].sum(), "RV": q.loc[q.ret, "v"].sum(), "TV": q.loc[q.trn, "v"].sum(),
            "TVd": (q.loc[q.trn, "v"] * q.loc[q.trn, "dstr"]).sum(), "n_roster": len(q)}), include_groups=False).reset_index()
        g["season"] = s
        g["RET"] = g.RETmin / g.t.map(tmin).replace(0, np.nan)
        g["LV"] = g.t.map(lost).fillna(0)
        g["RC"] = [RC.get((t, s - 1), 0.0) for t in g.t]
        g["x0"] = [STR.get((t, s - 1), 0.0) for t in g.t]
        g["x0ret"] = g.x0 * (g.RET.fillna(0.5) - 0.5)
        g["y"] = [STR.get((t, s), np.nan) for t in g.t]
        rows.append(g)
    F = pd.concat(rows, ignore_index=True).rename(columns={"t": "team"})
    return F


def fit_predict(F):
    out = []
    for s in range(2018, 2028):
        tr = F[(F.season >= 2016) & (F.season < s)].dropna(subset=["y"])
        te = F[F.season == s].copy()
        mu, sd = tr[FEAT].mean(), tr[FEAT].std().replace(0, 1)
        X = np.c_[np.ones(len(tr)), ((tr[FEAT] - mu) / sd).to_numpy()]
        A = X.T @ X + np.diag([0.0] + [1.0] * len(FEAT))
        beta = np.linalg.solve(A, X.T @ tr.y.to_numpy())
        te["tp1"] = np.c_[np.ones(len(te)), ((te[FEAT] - mu) / sd).to_numpy()] @ beta
        te["beta"] = [dict(zip(["b0"] + FEAT, np.round(beta, 3)))] * len(te)
        out.append(te)
    return pd.concat(out, ignore_index=True)


if __name__ == "__main__" and (len(sys.argv) < 2 or sys.argv[1] != "walk"):
    F = features()
    P = fit_predict(F)
    P.drop(columns=["beta"]).to_parquet(f"{O}/tp1_priors.parquet", index=False)
    rep = {}
    for s, q in P.groupby("season"):
        q2 = q.dropna(subset=["y"])
        rep[int(s)] = {"teams": int(len(q)), "with_final": int(len(q2)),
                       "rmse_gc3_prior": float(np.sqrt(((q2.y - q2.x0) ** 2).mean())) if len(q2) else None,
                       "rmse_tp1_prior": float(np.sqrt(((q2.y - q2.tp1) ** 2).mean())) if len(q2) else None,
                       "beta_std": q.beta.iloc[0]}
    json.dump(rep, open(f"{RES}/r9_team_prior_teamlevel.json", "w"), indent=1, default=str)
    for s, v in rep.items():
        print(s, v["teams"], "RMSE GC3 %s TP1 %s" % (v["rmse_gc3_prior"] and round(v["rmse_gc3_prior"], 2), v["rmse_tp1_prior"] and round(v["rmse_tp1_prior"], 2)), v["beta_std"])


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "walk":
    os.environ.setdefault("CBB_EVAL_WORKERS", "1")
    sys.argv = ["g1_backtest.py", "TP1_import_only"]
    import importlib.util
    from cbb_platform.game import forecast as GF, params as GP, ratings as GR
    from cbb_platform.game.research import backtest as BT
    G1 = os.path.join(ROOT, "docs/audits/2026-11-25-game/scripts/g1_backtest.py")
    src = open(G1).read().split('\nif __name__ == "__main__":')[0]
    ns = {"__file__": G1, "__name__": "g1lib"}
    exec(compile(src, G1, "exec"), ns)
    gm = GP.game_model()
    rp = GR.RatingParams(**{k: gm["ratings"][k] for k in ("tau_days", "lam_off", "lam_def", "lam_pace", "rho_carry", "lam_league", "carry_level", "carry_pace")})
    ns["rp"] = rp                                                       # production 1.1.0 params (GC3 carry) for the walk
    TP = pd.read_parquet(f"{O}/tp1_priors.parquet")
    def inject(pri, s):
        m = dict(zip(TP[TP.season == s].team, TP[TP.season == s].tp1))
        st = np.array([m.get(t, o - d) for t, o, d in zip(pri.teams, pri.off, pri.dfn)])
        lv = pri.off + pri.dfn
        return GR.Ratings(list(pri.teams), (st + lv) / 2, (lv - st) / 2, pri.pace, pri.mu, pri.hca, pri.pi, {"prior_of": "TP1", "season": s})
    D = ns["D"]
    ck = D / "g1_TP1_walk.parquet"
    if ck.exists():
        P = pd.read_parquet(ck)
    else:
        con = duckdb.connect(LOCK, read_only=True); hist = GR.games_frame(con, (2013, 2026)); con.close()
        chain = BT.prior_chain(hist, gm["ratings"]["prior_chain_first_season"], 2026, rp)
        preds = [ns["walk"](hist[hist.season == s], inject(chain[s], s)) for s in (2024, 2025)]
        preds.append(ns["walk"](GF.season_games(D / "increment.duckdb", 2027), inject(GF.prior(2027), 2027)))
        P = pd.concat(preds, ignore_index=True); P.to_parquet(ck)
    print("walk rows", len(P), flush=True)
