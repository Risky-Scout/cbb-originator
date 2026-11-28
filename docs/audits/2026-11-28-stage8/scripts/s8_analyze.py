"""Stage 08 benchmark tables from the scored rows (s8_extract.py, s8_market.py). Nothing is fitted or tuned.

Conventions (pre-declared in README §3 before any table was produced):
  * signed bias = mean(forecast mean - actual);   paired loss difference = model - market (negative favours the model)
  * player stat metrics are on the PARTICIPATION-CONDITIONAL PMF and use played rows only (box line, minutes > 0);
    the unconditional mixture (1-p_play)*delta0 + p_play*PMF is scored separately on every forecast player-game
  * one row per (game, player, stat) per vintage; vintages are never pooled; the PRIMARY vintage LAST_PREGAME is the latest
    PMF with prediction_as_of < tip (no outcome-dependent choice)
  * uncertainty: cluster bootstrap over games (B = 1000, seed 20261128); sensitivity: block bootstrap over slate dates
  * multiplicity: Holm over the pre-declared family of DECISION-horizon market comparisons (one test per category)

    s8_analyze.py WORK OUT
"""
import json, os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.getcwd())
from cbb_platform.prod.engine import attribute  # pure time-table attribution (Stage 06 correction); no model code

WORK, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
B, SEED = 1000, 20261128
FROZEN = "cbb-v2-jm1-final-shadow-1.0.0"
STATS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "PRA", "PR", "PA", "RA", "STOCKS"]
FINAL_ENGINE = "JM1_FINAL+RA2V1[AST,FG3M,STL,REB]+PTSC1[0]+TOVT1+DEV1+CTX1+PI2+PS2"
MIN_N = 30
games = pd.read_parquet(f"{WORK}/games.parquet")
gidx = games.set_index("game_id")


def jdump(obj, name):
    json.dump(obj, open(f"{OUT}/{name}", "w"), indent=1, default=str)


# ----------------------------------------------------------------------------- cluster bootstrap
def boot(df, val_cols, cluster="game_id", b=B, ratio=None):
    """Bootstrap CI of column means (or of a ratio of sums) resampling clusters. Returns {col: (est, lo, hi, se, n_eff)}."""
    if not len(df):
        return {}
    cl = df[cluster].astype("category").cat.codes.to_numpy()
    G = cl.max() + 1
    rng = np.random.default_rng(SEED)
    W = rng.multinomial(G, np.full(G, 1 / G), size=b).astype(float)          # b x G cluster weights
    n_c = np.bincount(cl, minlength=G).astype(float)
    out = {}
    for c in val_cols:
        v = df[c].to_numpy(dtype=float)
        ok = np.isfinite(v)
        s_c = np.bincount(cl[ok], weights=v[ok], minlength=G)
        m_c = np.bincount(cl[ok], minlength=G).astype(float)
        est = s_c.sum() / m_c.sum()
        bs = (W @ s_c) / np.maximum(W @ m_c, 1e-12)
        se = bs.std(ddof=1)
        var_iid = v[ok].var(ddof=1) / ok.sum() if ok.sum() > 1 else np.nan
        n_eff = ok.sum() * var_iid / se ** 2 if se > 0 else np.nan
        out[c] = (float(est), *[round(float(x), 6) for x in np.percentile(bs, [2.5, 97.5])], float(se), float(n_eff))
    return out


def summarize_stat(d):
    r = {"n": int(len(d)), "games": int(d.game_id.nunique()), "players": int(d.player_id.nunique())}
    if not len(d):
        return r
    e = d.pmf_mean - d.y
    r.update({"mean_forecast": d.pmf_mean.mean(), "mean_actual": d.y.mean(), "bias": e.mean(), "mae": e.abs().mean(),
              "rmse": np.sqrt((e ** 2).mean()), "crps": d.crps.mean(), "log_score": d.logs.mean(),
              "log_floored": int(d.log_floored.sum()), "pit_mean": d.pit.mean(), "pit_var": d.pit.var(),
              "pit_lt05": (d.pit < .05).mean(), "pit_gt95": (d.pit > .95).mean(), "pit_lt10": (d.pit < .1).mean(),
              "pit_gt90": (d.pit > .9).mean(), "cov80": d.cov80.mean(), "cov90": d.cov90.mean(), "cov98": d.cov98.mean(),
              "w80": d.w80.mean(), "w90": d.w90.mean(), "above_q99": d.above_q99.mean(), "below_q01": d.below_q01.mean(),
              "sd_forecast": np.sqrt(d.pmf_var).mean(), "z_sd": (e / np.sqrt(d.pmf_var.clip(lower=1e-9))).std()})
    h = np.histogram(d.pit, bins=10, range=(0, 1))[0] / len(d)
    r["pit_deciles"] = [round(x, 4) for x in h]
    r["pit_chi2_iid"] = float(((h * len(d) - len(d) / 10) ** 2 / (len(d) / 10)).sum())
    return r


# ============================================================================= population A: vintages
A = pd.read_parquet(f"{WORK}/player_rows_all.parquet")
A["prediction_as_of"] = pd.to_datetime(A.prediction_as_of, utc=True)
A["tip"] = pd.to_datetime(A.tip, utc=True)
A["model_kind"] = np.where(A.model_version == FROZEN, "FROZEN_OPENING_BASELINE", "DAILY_WALK_FORWARD")
eng = {t: attribute(None, t) for t in A.prediction_as_of.unique()}
A["engine"] = np.where(A.engine_id.fillna("") != "", A.engine_id.fillna(""), A.prediction_as_of.map(eng)).astype(str)
A["engine_base"] = A.engine.str.split("#").str[0]
A["engine_attribution"] = np.where(A.engine_id.fillna("") != "", "RECORDED", "TIME_ATTRIBUTED")
A["vclass"] = np.select([A.window.isin(["INITIAL_0700", "REFIT_RETRY_0800"]), A.window.str.startswith("ROTOWIRE"),
                         A.window == "OFFICIAL_TICK"], ["INITIAL", "REFRESH", "CUTOFF"], "OTHER")
A["hours_before_tip"] = (A.tip - A.prediction_as_of).dt.total_seconds() / 3600
gd = gidx[["game_universe", "conference_game", "game_date_et"]]
A = A.join(gd, on="game_id")
A["slate"] = pd.to_datetime(A.game_date_et).dt.strftime("%Y-%m-%d")
s5 = pd.read_csv("docs/audits/2026-11-27-stage5/results/initial_gap_rows.csv")
s5k = set(zip(s5.snapshot_id, s5.game_id, s5.player_id))
A["snapshot_id"] = A.source.str.split("/").str[-1]
A["s5_initial_defect"] = [(s, g, p) in s5k for s, g, p in zip(A.snapshot_id, A.game_id, A.player_id)]
K = ["game_id", "player_id", "stat"]
A = A.sort_values(K + ["prediction_as_of"])
V = {"LAST_PREGAME": A.groupby(K).tail(1),
     "INITIAL": A[A.vclass == "INITIAL"].groupby(K).head(1),
     "REFRESH_LAST": A[A.vclass == "REFRESH"].groupby(K).tail(1),
     "CUTOFF_TICK": A[A.vclass == "CUTOFF"].groupby(K).tail(1)}

# ---------------------------------------------------------------- vintage reconciliation
recon = {"pmf_rows_complete_games": int(len(A)), "pmf_rows_by_window": A.window.value_counts().to_dict(),
         "pmf_rows_by_kind": A.kind.value_counts().to_dict(), "unique_player_game_stat": int(A[K].drop_duplicates().shape[0]),
         "unique_player_games": int(A[["game_id", "player_id"]].drop_duplicates().shape[0]),
         "unique_players": int(A.player_id.nunique()), "games": int(A.game_id.nunique()),
         "vintages_per_player_game_stat": A.groupby(K).size().value_counts().sort_index().to_dict(),
         "forecast_artifacts": int(A.source.nunique()),
         "frozen_model_rows": int((A.model_kind == "FROZEN_OPENING_BASELINE").sum()),
         "engine_attribution": A.engine_attribution.value_counts().to_dict(),
         "engine_rows": A.engine_base.value_counts().to_dict(),
         "s5_initial_defect_rows": int(A.s5_initial_defect.sum()),
         "s5_initial_defect_player_games": int(A.loc[A.s5_initial_defect, ["game_id", "player_id"]].drop_duplicates().shape[0]),
         "vintage_units": {k: {"rows": int(len(v)), "player_games": int(v[["game_id", "player_id"]].drop_duplicates().shape[0]),
                               "games": int(v.game_id.nunique()), "played_rows": int(v.played.sum()),
                               "frozen_model_rows": int((v.model_kind == "FROZEN_OPENING_BASELINE").sum()),
                               "hours_before_tip_median": float(v.hours_before_tip.median())} for k, v in V.items()}}
jdump(recon, "vintage_reconciliation.json")

# ============================================================================= player distribution metrics
rows = []
for vn, v in V.items():
    pv = v[v.played]
    for mk in ["ALL", "DAILY_WALK_FORWARD", "FROZEN_OPENING_BASELINE"]:
        q = pv if mk == "ALL" else pv[pv.model_kind == mk]
        for st in STATS:
            d = q[q.stat == st]
            r = {"vintage": vn, "model_kind": mk, "stat": st, **summarize_stat(d)}
            if len(d) >= MIN_N:
                bt = boot(d.assign(err=d.pmf_mean - d.y, c90=d.cov90.astype(float)), ["err", "crps", "logs", "c90", "pit"])
                for c, (e, lo, hi, se, ne) in bt.items():
                    r[f"{c}_ci"] = [round(lo, 5), round(hi, 5)]
                    r[f"{c}_n_eff"] = round(ne, 1)
            rows.append(r)
PD = pd.DataFrame(rows)
PD.to_csv(f"{OUT}/player_distribution_metrics.csv", index=False)

# final-engine prospective subset (genuine forecasts of the final engine; the closest legitimate evidence on the corrected incumbent)
fe = []
for vn, v in V.items():
    q = v[v.played & (v.engine_base == FINAL_ENGINE)]
    for st in STATS:
        d = q[q.stat == st]
        r = {"vintage": vn, "stat": st, **summarize_stat(d)}
        if len(d) >= MIN_N:
            for c, (e, lo, hi, se, ne) in boot(d.assign(err=d.pmf_mean - d.y), ["err", "crps"]).items():
                r[f"{c}_ci"] = [round(lo, 5), round(hi, 5)]
        fe.append(r)
pd.DataFrame(fe).to_csv(f"{OUT}/final_engine_prospective_metrics.csv", index=False)

# by engine (LAST_PREGAME) - lineage-labelled, descriptive only (different dates, not a paired comparison)
eng_rows = []
for (e, mk), q in V["LAST_PREGAME"][V["LAST_PREGAME"].played].groupby(["engine_base", "model_kind"]):
    for st in STATS:
        d = q[q.stat == st]
        if len(d):
            eng_rows.append({"engine": e, "model_kind": mk, "stat": st, **{k: v for k, v in summarize_stat(d).items() if k != "pit_deciles"}})
pd.DataFrame(eng_rows).to_csv(f"{OUT}/player_metrics_by_engine.csv", index=False)

# unconditional (participation x conditional PMF), every forecast player-game incl. DNP / no box line
un = []
for vn, v in V.items():
    for st in STATS:
        d = v[(v.stat == st) & v.u_crps.notna()]
        y0 = np.where(d.played, d.y, 0)
        un.append({"vintage": vn, "stat": st, "n": len(d), "played_share": d.played.mean(),
                   "mean_forecast_uncond": (d.p_play * d.pmf_mean).mean(), "mean_actual_uncond": y0.mean(),
                   "bias_uncond": (d.p_play * d.pmf_mean - y0).mean(), "crps_uncond": d.u_crps.mean(),
                   "log_score_uncond": d.u_logs.mean(), "pit_mean_uncond": d.u_pit.mean(),
                   "pit_lt05": (d.u_pit < .05).mean(), "pit_gt95": (d.u_pit > .95).mean()})
pd.DataFrame(un).to_csv(f"{OUT}/player_unconditional_metrics.csv", index=False)

# ============================================================================= participation and minutes (one row per player-game)
part = []
pmin = []
for vn, v in V.items():
    u = v.drop_duplicates(["game_id", "player_id"]).copy()
    u["y_play"] = u.played.astype(float)
    u["brier"] = (u.p_play - u.y_play) ** 2
    pp = u.p_play.clip(1e-6, 1 - 1e-6)
    u["ll"] = -(u.y_play * np.log(pp) + (1 - u.y_play) * np.log(1 - pp))
    base = u.y_play.mean()
    bt = boot(u, ["brier", "ll", "y_play", "p_play"])
    r = {"vintage": vn, "group": "ALL", "n": len(u), "games": u.game_id.nunique(), "played_rate": base, "mean_p_play": u.p_play.mean(),
         "brier": u.brier.mean(), "brier_ci": bt["brier"][1:3], "brier_climatology": base * (1 - base),
         "log_loss": u.ll.mean(), "log_loss_ci": bt["ll"][1:3], "n_eff": bt["brier"][4]}
    part.append(r)
    for gcol in ["availability_status", "role_diagnostic", "cold_start_class", "model_kind"]:
        for gv, q in u.groupby(gcol, dropna=False):
            part.append({"vintage": vn, "group": f"{gcol}={gv}", "n": len(q), "games": q.game_id.nunique(), "played_rate": q.y_play.mean(),
                         "mean_p_play": q.p_play.mean(), "brier": q.brier.mean(), "log_loss": q.ll.mean(),
                         "evaluable": len(q) >= MIN_N})
    # reliability
    bins = [0, .02, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, .95, .98, 1.0001]
    u["pbin"] = pd.cut(u.p_play, bins, right=False)
    for b_, q in u.groupby("pbin", observed=True):
        lo_, hi_ = (np.nan, np.nan)
        if len(q) >= 5:
            bb = boot(q, ["y_play"])
            lo_, hi_ = bb["y_play"][1:3]
        part.append({"vintage": vn, "group": f"reliability {b_}", "n": len(q), "mean_p_play": q.p_play.mean(),
                     "played_rate": q.y_play.mean(), "played_rate_ci": [lo_, hi_]})
    # minutes, conditional on playing (only mean and sd are persisted: a normal reference interval is an approximation)
    m = u[u.played & u.min_mu.notna()].copy()
    m["err"] = m.min_mu - m.minutes
    m["z"] = (m.minutes - m.min_mu) / m.min_sigma
    for gcol in [None, "role_diagnostic", "cold_start_class", "starter"]:
        groups = [("ALL", m)] if gcol is None else list(m.groupby(gcol, dropna=False))
        for gv, q in groups:
            if not len(q):
                continue
            r = {"vintage": vn, "group": "ALL" if gcol is None else f"{gcol}={gv}", "n": len(q), "games": q.game_id.nunique(),
                 "mean_min_mu": q.min_mu.mean(), "mean_minutes": q.minutes.mean(), "bias": q.err.mean(), "mae": q.err.abs().mean(),
                 "rmse": np.sqrt((q.err ** 2).mean()), "mean_min_sigma": q.min_sigma.mean(), "z_mean": q.z.mean(), "z_sd": q.z.std(),
                 "normal_cov90": (q.z.abs() <= 1.645).mean(), "diagnostic_only": gcol == "starter"}
            if gcol is None:
                r["bias_ci"] = boot(q, ["err"])["err"][1:3]
            pmin.append(r)
    # minutes of the forecast players who did not play: the unconditional minutes error
pd.DataFrame(part).to_csv(f"{OUT}/participation_metrics.csv", index=False)
pd.DataFrame(pmin).to_csv(f"{OUT}/minutes_metrics.csv", index=False)

# ============================================================================= role x stat and other subgroups (PRIMARY vintage)
P0 = V["LAST_PREGAME"]
pv = P0[P0.played].copy()
pv["min_band"] = pd.cut(pv.min_mu, [0, 10, 20, 28, 99], right=False, labels=["<10", "10-20", "20-28", "28+"]).astype(str)
pv["pplay_band"] = pd.cut(pv.p_play, [0, .5, .8, .95, 1.01], right=False, labels=["<.5", ".5-.8", ".8-.95", ".95+"]).astype(str)
pv["horizon"] = pd.cut(pv.hours_before_tip, [0, 1.5, 4, 8, 99], right=False, labels=["<1.5h", "1.5-4h", "4-8h", "8h+"]).astype(str)
pv["season_phase"] = pd.cut(pd.to_datetime(pv.slate), pd.to_datetime(["2026-11-01", "2026-11-15", "2026-11-22", "2026-11-28"]),
                            right=False, labels=["11-08..11-14", "11-15..11-21", "11-22..11-27"]).astype(str)
pv["starter_actual"] = pv.starter.map({True: "STARTER", False: "BENCH"}).fillna("UNKNOWN")
GROUPS = {"role_diagnostic": "PREDICTION_TIME", "min_band": "PREDICTION_TIME", "pplay_band": "PREDICTION_TIME",
          "availability_status": "PREDICTION_TIME", "cold_start_class": "PREDICTION_TIME", "horizon": "PREDICTION_TIME",
          "season_phase": "PREDICTION_TIME", "game_universe": "PREDICTION_TIME", "conference_game": "PREDICTION_TIME",
          "model_kind": "LINEAGE", "starter_actual": "DIAGNOSTIC_REALIZED"}
rs = []
for gcol, basis in GROUPS.items():
    for (gv, st), d in pv.groupby([gcol, "stat"], dropna=False):
        s = {k: v for k, v in summarize_stat(d).items() if k != "pit_deciles"}
        r = {"group_var": gcol, "group": str(gv), "basis": basis, "stat": st, **s, "evaluable": len(d) >= MIN_N}
        if len(d) >= MIN_N:
            bt = boot(d.assign(err=d.pmf_mean - d.y), ["err", "crps"])
            r["bias_ci"] = [round(x, 4) for x in bt["err"][1:3]]
            r["crps_ci"] = [round(x, 4) for x in bt["crps"][1:3]]
            r["bias_significant"] = not (bt["err"][1] <= 0 <= bt["err"][2])
        else:
            r["status"] = "NOT_EVALUABLE"
        rs.append(r)
RS = pd.DataFrame(rs)
RS.to_csv(f"{OUT}/role_stat_metrics.csv", index=False)

# ============================================================================= calibration of exceedance probabilities on the full universe
# outcome-independent pseudo-line per row: floor(PMF median) + 0.5  ->  P(over) from the conditional PMF; played rows only
# requires the PMF: recomputed from persisted mean/var? no - use the PIT identity: P(Y > L) = 1 - F(L); F stored? we use the
# market contracts for listed lines and the decile PIT for the full universe (exact for randomized PIT).
cal = []
for st in STATS:
    d = pv[pv.stat == st]
    if len(d) >= MIN_N:
        h = np.histogram(d.pit, bins=10, range=(0, 1))[0]
        cal.append({"stat": st, "n": len(d), "pit_deciles_share": list(np.round(h / len(d), 4)),
                    "central80_cov": d.cov80.mean(), "central90_cov": d.cov90.mean(), "upper_tail_rate_gt95": (d.pit > .95).mean(),
                    "lower_tail_rate_lt05": (d.pit < .05).mean()})
jdump(cal, "pit_calibration.json")

# ============================================================================= game distribution metrics
GR = pd.concat([pd.read_parquet(os.path.join(WORK, "game_rows", f)) for f in sorted(os.listdir(f"{WORK}/game_rows"))], ignore_index=True)
GR["prediction_as_of"] = pd.to_datetime(GR.prediction_as_of, utc=True)
GR = GR.join(gidx[["game_universe", "game_date_et", "tip"]], on="game_id").sort_values(["game_id", "prediction_as_of"])
GR["slate"] = pd.to_datetime(GR.game_date_et).dt.strftime("%Y-%m-%d")
GV = {"LAST_PREGAME": GR.groupby("game_id").tail(1),
      "INITIAL": GR[GR.window.isin(["INITIAL_0700", "REFIT_RETRY_0800"])].groupby("game_id").head(1)}
grow, gcal = [], []
for vn, g in GV.items():
    g = g.copy()
    g["ml_y"] = (g.H > g.A).astype(float)
    g["ml_brier"] = (g.p_home_win - g.ml_y) ** 2
    pw = g.p_home_win.clip(1e-6, 1 - 1e-6)
    g["ml_ll"] = -(g.ml_y * np.log(pw) + (1 - g.ml_y) * np.log(1 - pw))
    for cls, q in [("ALL", g)] + list(g.groupby("game_universe")):
        r = {"vintage": vn, "class": cls, "n_games": len(q), "joint_versions": q.joint_version.value_counts().to_dict(),
             "model_versions": q.model_version.value_counts().to_dict()}
        for t in ["home", "away", "total", "margin"]:
            y = q[{"home": "H", "away": "A", "total": "T", "margin": "M"}[t]]
            e = q[f"{t}_mean"] - y
            r.update({f"{t}_bias": e.mean(), f"{t}_mae": e.abs().mean(), f"{t}_rmse": np.sqrt((e ** 2).mean()),
                      f"{t}_crps": q[f"{t}_crps"].mean(), f"{t}_log_score": q[f"{t}_logs"].mean(),
                      f"{t}_pit_mean": q[f"{t}_pit"].mean(), f"{t}_cov80": q[f"{t}_cov80"].mean(), f"{t}_cov90": q[f"{t}_cov90"].mean(),
                      f"{t}_cov98": q[f"{t}_cov98"].mean(), f"{t}_w90": q[f"{t}_w90"].mean(), f"{t}_sd_model": q[f"{t}_sd"].mean(),
                      f"{t}_resid_sd": e.std(), f"{t}_pit_tails_5": [(q[f"{t}_pit"] < .05).mean(), (q[f"{t}_pit"] > .95).mean()]})
            if len(q) >= MIN_N:
                bt = boot(q.assign(err=e), ["err", f"{t}_crps"], cluster="slate")
                r[f"{t}_bias_ci_dateblock"] = [round(x, 3) for x in bt["err"][1:3]]
        r.update({"ml_brier": q.ml_brier.mean(), "ml_log_loss": q.ml_ll.mean(), "ml_mean_p": q.p_home_win.mean(),
                  "ml_home_win_rate": q.ml_y.mean(),
                  "corr_home_away_model_mean": q.corr_ha_model.mean(),
                  "corr_home_away_realized_resid": np.corrcoef(q.home_mean - q.H, q.away_mean - q.A)[0, 1] if len(q) > 3 else np.nan,
                  "rho_margin_total_model_mean": q.rho_mt_model.mean(),
                  "rho_margin_total_realized_resid": np.corrcoef(q.margin_mean - q["M"], q.total_mean - q["T"])[0, 1] if len(q) > 3 else np.nan})
        grow.append(r)
    for b_, q in g.groupby(pd.cut(g.p_home_win, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001], right=False), observed=True):
        gcal.append({"vintage": vn, "bin": str(b_), "n": len(q), "mean_p": q.p_home_win.mean(), "home_win_rate": q.ml_y.mean()})
pd.DataFrame(grow).to_json(f"{OUT}/game_distribution_metrics.json", orient="records", indent=1, default_handler=str)
pd.DataFrame(gcal).to_csv(f"{OUT}/game_moneyline_reliability.csv", index=False)

# ============================================================================= market comparison (population C)
def paired(df, pm, pk, y, cluster="game_id"):
    d = df[[pm, pk, y, cluster] + [c for c in ["slate", "slate_date"] if c in df]].dropna(subset=[pm, pk, y]).copy()
    if not len(d):
        return None
    eps = 1e-6
    a, b_, yy = d[pm].clip(eps, 1 - eps), d[pk].clip(eps, 1 - eps), d[y]
    d["bs_m"], d["bs_k"] = (a - yy) ** 2, (b_ - yy) ** 2
    d["ll_m"] = -(yy * np.log(a) + (1 - yy) * np.log(1 - a))
    d["ll_k"] = -(yy * np.log(b_) + (1 - yy) * np.log(1 - b_))
    d["d_bs"], d["d_ll"] = d.bs_m - d.bs_k, d.ll_m - d.ll_k
    d["cal_m"], d["cal_k"] = a - yy, b_ - yy
    bt = boot(d, ["d_bs", "d_ll", "cal_m", "cal_k"], cluster=cluster)
    se = bt["d_bs"][3]
    from math import erf, sqrt
    z = bt["d_bs"][0] / se if se > 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))
    zl = bt["d_ll"][0] / bt["d_ll"][3] if bt["d_ll"][3] > 0 else 0.0
    pl = 2 * (1 - 0.5 * (1 + erf(abs(zl) / sqrt(2))))
    return {"n": len(d), "clusters": int(d[cluster].nunique()), "base_rate": yy.mean(), "mean_model_p": a.mean(), "mean_market_p": b_.mean(),
            "brier_model": d.bs_m.mean(), "brier_market": d.bs_k.mean(), "d_brier": bt["d_bs"][0], "d_brier_ci": bt["d_bs"][1:3],
            "d_brier_p": p, "log_model": d.ll_m.mean(), "log_market": d.ll_k.mean(), "d_log": bt["d_ll"][0], "d_log_ci": bt["d_ll"][1:3],
            "d_log_p": pl, "n_eff": bt["d_bs"][4], "citl_model": bt["cal_m"][0], "citl_model_ci": bt["cal_m"][1:3],
            "citl_market": bt["cal_k"][0], "citl_market_ci": bt["cal_k"][1:3]}


PM = pd.read_parquet(f"{WORK}/player_market_contracts.parquet")
PM = PM[~PM.void_dnp & ~PM.push].copy()
PM["slate"] = PM.slate_date
prim = PM[(PM.book == PM.eval_book) & (PM.line == PM.eval_line)].copy()       # one contract per (game, player, stat)
mrows = []
for scope, base in [("PRIMARY_DECISION_CONTRACT", prim), ("ALL_BOOK_LINE_CONTRACTS", PM)]:
    for hz, col in [("OPEN", "open_fair"), ("DECISION", "dec_fair"), ("CLOSE", "close_fair")]:
        for st in ["ALL"] + STATS:
            d = base if st == "ALL" else base[base.stat == st]
            r = paired(d, "model_p_over", col, "over")
            mrows.append({"market": "PLAYER", "scope": scope, "horizon": hz, "category": st,
                          **(r or {"n": 0, "status": "NOT_EVALUABLE"})})
        for role, d in base.groupby("player_role"):
            r = paired(d, "model_p_over", col, "over")
            mrows.append({"market": "PLAYER", "scope": scope, "horizon": hz, "category": f"role={role}", **(r or {"n": 0})})
# model lean direction at decision (prediction-time): OVER lean if model_p > market fair
prim["lean"] = np.where(prim.model_p_over > prim.dec_fair, "MODEL_LEANS_OVER", "MODEL_LEANS_UNDER")
for ln, d in prim.groupby("lean"):
    r = paired(d, "model_p_over", "dec_fair", "over")
    mrows.append({"market": "PLAYER", "scope": "PRIMARY_DECISION_CONTRACT", "horizon": "DECISION", "category": ln, **(r or {"n": 0})})

GM = pd.read_parquet(f"{WORK}/game_market_contracts.parquet")
GM = GM[~GM.push].copy()
main = {"MONEYLINE", "SPREAD", "GAME_TOTAL", "HOME_TEAM_TOTAL", "AWAY_TEAM_TOTAL"}
# primary game unit: per game x main market, the decision consensus point (most books at decision; tie -> lower |pt - median|)
dm = GM[GM.mt.isin(main) & GM.dec_fair.notna()].copy()
cons = []
for (gid, mt), q in dm.groupby(["game_id", "mt"]):
    if mt == "MONEYLINE":
        pick = q
    else:
        cnt = q.groupby("pt").book.nunique()
        top = cnt[cnt == cnt.max()].index.to_numpy()
        med = q.pt.median()
        ptv = top[np.argmin(np.abs(top - med))]
        pick = q[q.pt == ptv]
    cons.append({"game_id": gid, "mt": mt, "pt": pick.pt.iloc[0], "books": pick.book.nunique(), "model_p": pick.model_p.mean(),
                 "open_fair": pick.open_fair.mean(), "dec_fair": pick.dec_fair.mean(), "close_fair": pick.close_fair.mean(),
                 "result": pick.result.iloc[0], "slate": pick.slate.iloc[0]})
CG = pd.DataFrame(cons)
CG.to_parquet(f"{WORK}/game_market_consensus.parquet", index=False)
for scope, base in [("PRIMARY_CONSENSUS_MAIN_LINE", CG), ("ALL_BOOK_LINE_CONTRACTS", GM)]:
    for hz, col in [("OPEN", "open_fair"), ("DECISION", "dec_fair"), ("CLOSE", "close_fair")]:
        cats = sorted(base.mt.unique())
        for mt in ["ALL"] + cats:
            d = base if mt == "ALL" else base[base.mt == mt]
            r = paired(d, "model_p", col, "result")
            mrows.append({"market": "GAME", "scope": scope, "horizon": hz, "category": mt, **(r or {"n": 0, "status": "NOT_EVALUABLE"})})
MR = pd.DataFrame(mrows)
# Holm over the pre-declared DECISION family: primary scope, one test per player stat and per main game market (n >= MIN_N)
fam = MR[(MR.horizon == "DECISION") & MR.scope.isin(["PRIMARY_DECISION_CONTRACT", "PRIMARY_CONSENSUS_MAIN_LINE"]) &
         ~MR.category.astype(str).str.startswith(("role=", "MODEL_LEANS", "ALL")) & (MR.n >= MIN_N)].copy()
for metric in ["d_brier_p", "d_log_p"]:
    o = fam[metric].sort_values()
    m = len(o)
    adj, run = {}, 0.0
    for i, (ix, pval) in enumerate(o.items()):
        run = max(run, min(1.0, (m - i) * pval))
        adj[ix] = run
    MR.loc[list(adj), metric.replace("_p", "_p_holm")] = pd.Series(adj)
MR["family_member"] = MR.index.isin(fam.index)


def verdict(r):
    if not r.get("n") or r["n"] < MIN_N:
        return "NOT_EVALUABLE"
    lo, hi = r["d_brier_ci"]
    if hi < 0 and lo < 0:
        sig = (r.get("d_brier_p_holm", r["d_brier_p"]) if r["family_member"] else r["d_brier_p"]) < 0.05
        return "MODEL_BETTER" if sig else "MODEL_BETTER_UNADJUSTED_ONLY"
    if lo > 0:
        sig = (r.get("d_brier_p_holm", r["d_brier_p"]) if r["family_member"] else r["d_brier_p"]) < 0.05
        return "MODEL_INFERIOR" if sig else "MODEL_INFERIOR_UNADJUSTED_ONLY"
    return "INCONCLUSIVE"


MR["verdict_brier"] = [verdict(r) for r in MR.to_dict("records")]
MR.to_csv(f"{OUT}/market_comparison.csv", index=False)

# reliability tables (model and market) at decision, primary scope, with tiers
rel = []
for mk, d, pm_, pk_, y_ in [("PLAYER", prim, "model_p_over", "dec_fair", "over"), ("GAME", CG, "model_p", "dec_fair", "result")]:
    d = d.dropna(subset=[pk_]).copy()
    for who, col in [("MODEL", pm_), ("MARKET", pk_)]:
        side_p = np.maximum(d[col], 1 - d[col])
        side_hit = np.where(d[col] >= .5, d[y_], 1 - d[y_])
        for b_, q in pd.DataFrame({"p": side_p, "hit": side_hit, "game_id": d.game_id}).groupby(
                pd.cut(side_p, [.5, .55, .6, .65, .7, .8, 1.0001], right=False), observed=True):
            ci = boot(q, ["hit"])["hit"][1:3] if len(q) >= 5 else [np.nan, np.nan]
            rel.append({"market": mk, "forecaster": who, "side_prob_bin": str(b_), "n": len(q), "mean_p": q.p.mean(),
                        "hit_rate": q.hit.mean(), "hit_ci": ci})
        for tier in [.60, .65]:
            q = pd.DataFrame({"p": side_p, "hit": side_hit, "game_id": d.game_id})[side_p >= tier]
            ci = boot(q, ["hit"])["hit"][1:3] if len(q) >= 5 else [np.nan, np.nan]
            rel.append({"market": mk, "forecaster": who, "side_prob_bin": f"TIER>={tier}", "n": len(q),
                        "mean_p": q.p.mean() if len(q) else np.nan, "hit_rate": q.hit.mean() if len(q) else np.nan, "hit_ci": ci})
    # calibration slope (logistic recalibration of y on logit p), descriptive
    for who, col in [("MODEL", pm_), ("MARKET", pk_)]:
        p = d[col].clip(1e-4, 1 - 1e-4).to_numpy(); yv = d[y_].to_numpy(dtype=float)
        x = np.log(p / (1 - p)); X = np.c_[np.ones_like(x), x]; w = np.zeros(2)
        for _ in range(50):
            mu = 1 / (1 + np.exp(-X @ w)); H = X.T @ (X * (mu * (1 - mu))[:, None]); w += np.linalg.solve(H + 1e-9 * np.eye(2), X.T @ (yv - mu))
        rel.append({"market": mk, "forecaster": who, "side_prob_bin": "CALIBRATION_SLOPE", "n": len(d), "intercept": w[0], "slope": w[1]})
pd.DataFrame(rel).to_csv(f"{OUT}/market_reliability.csv", index=False)

# market coverage
mcov = {"player": {"evaluations_complete_games": int(pd.read_parquet(f"{WORK}/player_evals.parquet").game_id.isin(games.loc[games.complete, "game_id"]).sum()),
                   "contracts_all_book_lines": int(len(pd.read_parquet(f"{WORK}/player_market_contracts.parquet"))),
                   "contracts_settled": int(len(PM)), "primary_contracts_settled": int(len(prim)),
                   "primary_games": int(prim.game_id.nunique()), "primary_player_games": int(prim[["game_id", "player_id"]].drop_duplicates().shape[0]),
                   "primary_by_stat": prim.stat.value_counts().to_dict(), "void_dnp": int(pd.read_parquet(f"{WORK}/player_market_contracts.parquet").void_dnp.sum()),
                   "decision_source_kind": prim.source_kind.value_counts().to_dict(),
                   "decision_frozen_model": int((prim.model_version == FROZEN).sum())},
        "game": {"contracts_all": int(len(GM)), "games": int(GM.game_id.nunique()), "consensus_rows": int(len(CG)),
                 "consensus_by_market": CG.mt.value_counts().to_dict(), "pushes_excluded": int(pd.read_parquet(f"{WORK}/game_market_contracts.parquet").push.sum())}}
jdump(mcov, "market_coverage.json")
print("OK")
