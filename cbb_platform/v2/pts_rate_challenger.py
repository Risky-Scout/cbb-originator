"""PTS RATE CHALLENGER R1 (research; protocol modeling/player_props_v2/PTS_RATE_CHALLENGER_PROTOCOL.md, commit 3e9e52c,
frozen before any R1 quantity was computed). ONE global empirical-Bayes pseudo-minutes parameter tau for the PTS
per-minute rate; everything else is the frozen JM1_FINAL (e1353e5), called through its own library functions.

    r_prior = (Hp_pts + k p_pos) / (Hp_min + k)          frozen R0 estimator with the current season removed
    r_R1    = (cur_pts + tau r_prior) / (cur_min + tau)   (R0 = the special case tau_i = Hp_min,i + k)

Stages (in order; each writes a record):
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger fit                tau on 2022-23 + 2023-24 (Phase 1B NB)
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger evaluate 2024-25   development evaluation
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger evaluate 2025-26   retrospective confirmation
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger decide             gates A-J -> decision
No API call, no refit, no change to any frozen file. Not a production component.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P
from cbb_platform.prod import directional_audit as DA
from cbb_platform.v2 import dependence as D, joint as J, joint_minutes_v2 as JM, latent as L, marginal as MG
from cbb_platform.v2 import jm1_final as F, phase3 as P3, phase3_prep as PP, structural as S

PROTOCOL = C.PROJECT_ROOT / "modeling" / "player_props_v2" / "PTS_RATE_CHALLENGER_PROTOCOL.md"
PROTOCOL_COMMIT = "3e9e52c4e079f5c0c0f25023805c60a2d3f7b8b7"
OUT = C.PROJECT_ROOT / "modeling" / "player_props_v2"
FIT_PATH = OUT / "pts_rate_challenger_fit.json"
EVAL_PATH = {"2024-25": OUT / "pts_rate_challenger_eval_2024_25.json", "2025-26": OUT / "pts_rate_challenger_eval_2025_26.json"}
RESULT_PATH = OUT / "pts_rate_challenger_results.json"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_pts_rate"
CFG_PATH = OUT / "v2_marginal_final_v1ref_through_2025_26.json"

TAU_LO, TAU_HI, N_GRID = 1.0, 2000.0, 241
TAU_GRID = TAU_HI ** (np.arange(N_GRID) / (N_GRID - 1))           # tau_i = 2000^(i/240), tau_0 = 1
SUSPECT_IDX = {0, 1, N_GRID - 2, N_GRID - 1}
KMAX_1B = 120
FIT_FOLDS = {2023: PP.STORE_PATH, 2024: C.PROJECT_ROOT / "modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet"}
EVAL = {"2024-25": {"F": 2025, "params": OUT / "joint_minutes_v2_dev_parameters.json"},
        "2025-26": {"F": 2026, "params": OUT / "jm1_final_confirmation_parameters.json"}}
BANDS = {"MIN_0_10": (0, 10), "MIN_10_20": (10, 20), "MIN_20_30": (20, 30), "MIN_30PLUS": (30, np.inf),
         "LOW_LT20": (0, 20), "ALL": (-np.inf, np.inf)}
EVIDENCE = {"CUR_0": (-np.inf, 0.0), "CUR_0_100": (0.0, 100.0), "CUR_100_300": (100.0, 300.0), "CUR_300PLUS": (300.0, np.inf)}
COLD = ("WARM", "COLD_EARLY_SEASON", "COLD_TRANSFER_IN", "COLD_NO_HISTORY")
PTS_SCALE = 6.858576995112134                                     # phase3._s0_structure("select") PTS scale
REPRO_RTOL = F.REPRO_RTOL
COMBOS = ("PRA", "PR", "PA")
IDX = [L.STATS.index(s) for s in ("PTS", "REB", "AST")]
# gates (protocol section 8)
G_HIGH, G_COMP = 0.75, 0.75
G_AGG_LOG, G_AGG_CRPS = F.G_STAT_LOG, F.G_STAT_CRPS                # +0.002 / +0.002 (upper bounds)
G_GROUP = F.G_STAT_GROUP_LOG                                       # +0.005
G_BIAS_WORSE, G_CAL = 0.05, 0.01
G_COMBO_CRPS_REL, G_COMBO_BRIER = 0.0025, 0.002
TAILS = {"mid_pit": 0.5, "P_pit_lt_0.25": 0.25, "P_pit_lt_0.5": 0.5, "P_pit_lt_0.75": 0.75,
         "lower_miss_0.1": 0.1, "upper_miss_0.9": 0.1, "cov80": 0.8, "cov90": 0.9}


class ChallengerViolation(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=C.PROJECT_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def protocol_frozen() -> dict:
    """The protocol file must be byte-identical to its committed version at PROTOCOL_COMMIT."""
    committed = _git("show", f"{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(C.PROJECT_ROOT)}")
    if committed != PROTOCOL.read_text().strip():
        raise ChallengerViolation("the protocol changed after its freeze commit")
    return {"protocol_commit": PROTOCOL_COMMIT, "protocol_sha256": _sha(PROTOCOL)}


def _state(F_: int) -> dict:
    st = json.loads((OUT / "states" / f"F{F_}_ANNUAL_REFIT_V1REF.json").read_text())
    return st.get("v0_state", st)


def history(F_: int) -> tuple[pd.DataFrame, dict]:
    """Frozen PIT rate inputs (marginal.load_frames 'candidates', verbatim SQL) of every candidate row of season F_."""
    cfg = json.loads(CFG_PATH.read_text())["config"]
    fr, _ = MG.load_frames(cfg, range(F_ - cfg["recency"]["prior_seasons_used"], F_ + 1), "candidates")
    fr = fr[fr["season"] == F_]
    keep = ["player_game_example_id", "pos", "cur_min", "cur_pts"] + [f"h{j}_{c}" for j in (1, 2, 3) for c in ("min", "pts")]
    return fr[keep].reset_index(drop=True), cfg


def rate_parts(h: pd.DataFrame, st: dict, cfg: dict) -> dict:
    """R0 rate and the R1 ingredients (r_prior, cur_pts, cur_min), exactly the props_v0._predict_means algebra."""
    hl = cfg["recency"]["half_life_seasons"]
    wj = {j: P.recency_weights(j, hl) for j in (1, 2, 3)}
    hp_min = sum(wj[j] * h[f"h{j}_min"].fillna(0).to_numpy(float) for j in (1, 2, 3))
    hp_pts = sum(wj[j] * h[f"h{j}_pts"].fillna(0).to_numpy(float) for j in (1, 2, 3))
    cm, cp = h["cur_min"].fillna(0).to_numpy(float), h["cur_pts"].fillna(0).to_numpy(float)
    k = float(st["prior_k_minutes"]["pts"])
    pp = h["pos"].map(st["prior_rate"]["pts"]).to_numpy(float)
    r_prior = (hp_pts + k * pp) / (hp_min + k)
    # R0: the frozen props_v0._predict_means rate, verbatim (_hist(t) + k * prior) / (_hist(min) + k)
    r_r0 = (P._hist(h, cfg, "pts") + k * pp) / (P._hist(h, cfg, "min") + k)
    return {"r_R0": r_r0, "r_prior": r_prior, "cur_min": cm, "cur_pts": cp, "hp_min": hp_min, "k": k}


def r_r1(parts: dict, tau: float) -> np.ndarray:
    """The protocol formula (cur_pts + tau r_prior) / (cur_min + tau). Zero-evidence rows (cur_min = 0 AND cur_pts = 0)
    take its exact value r_prior directly, so they are bit-identical to R0 (no float round trip tau r / tau). Rows with
    cur_min = 0 but cur_pts > 0 (source box scores with 0 minutes and points; addendum) keep the formula verbatim."""
    cm, cp, rp = parts["cur_min"], parts["cur_pts"], parts["r_prior"]
    return np.where((cm <= 0) & (cp == 0), rp, (cp + tau * rp) / (cm + tau))


def numerics(parts: dict, tau: float, mu_r0: np.ndarray, min_mu: np.ndarray, floor: float) -> dict:
    """Gate J numerics on every candidate row."""
    r1 = r_r1(parts, tau)
    cm, cp, rp = parts["cur_min"], parts["cur_pts"], parts["r_prior"]
    pos = cm > 0
    rc = np.where(pos, cp / np.where(pos, cm, 1.0), rp)
    lo, hi = np.minimum(rc, rp), np.maximum(rc, rp)
    mu1 = np.maximum(min_mu * r1, floor)
    z = (cm <= 0) & (cp == 0)
    q = (cm <= 0) & (cp != 0)
    return {"rows": int(len(r1)), "nonfinite": int((~np.isfinite(r1)).sum()), "nonpositive": int((r1 <= 0).sum()),
            "outside_convex_hull": int((pos & ((r1 < lo - 1e-12) | (r1 > hi + 1e-12))).sum()),
            "zero_evidence_rows": int(z.sum()),
            "zero_evidence_mu_not_bit_identical": int((mu1[z] != mu_r0[z]).sum()),
            "cur_min_zero_with_points_rows": int(q.sum()),
            "cur_min_zero_with_points_max_r_R1_minus_r_prior": float(np.max(r1[q] - rp[q])) if q.any() else 0.0,
            "max_r_R1": float(np.nanmax(r1)), "min_r_R1": float(np.nanmin(r1))}


# ============================================================================ fit
def _nb_scores(mu: np.ndarray, phi: float, y: np.ndarray, chunk: int = 40000) -> tuple[np.ndarray, np.ndarray]:
    """Per-row log score and CRPS under the exact frozen Phase 1B PMF props_v0.pmf_matrix(mu, phi, 120)."""
    ls, cr = np.empty(len(mu)), np.empty(len(mu))
    yi = np.minimum(y.astype(np.int64), KMAX_1B)
    kk = np.arange(KMAX_1B + 1)
    for a in range(0, len(mu), chunk):
        b = min(a + chunk, len(mu))
        pm = P.pmf_matrix(mu[a:b], phi, KMAX_1B)
        r = np.arange(b - a)
        ls[a:b] = -np.log(np.maximum(pm[r, yi[a:b]], 1e-300))
        cdf = np.minimum(np.cumsum(pm, axis=1), 1.0)
        cdf[:, -1] = 1.0
        cr[a:b] = ((cdf - (kk[None] >= yi[a:b, None])) ** 2).sum(axis=1)
    return ls, cr


def fit(log=print) -> dict:
    if FIT_PATH.exists():
        raise ChallengerViolation("tau is fitted exactly once; the fit record already exists")
    pf = protocol_frozen()
    folds, checks = [], {}
    for F_, path in FIT_FOLDS.items():
        st = _state(F_)
        h, cfg = history(F_)
        s = pd.read_parquet(path, columns=["player_game_example_id", "forecast_season", "min_mu", "PTS_mu", "PTS_phi",
                                           "participation_status", "y_PTS", "game_id", "training_through_season"])
        s = s[s["forecast_season"] == F_].merge(h, on="player_game_example_id", how="left", validate="one_to_one")
        if s["pos"].isna().any() or (s["training_through_season"] >= F_).any():
            raise ChallengerViolation(f"F{F_}: history join incomplete or not out-of-sample")
        parts = rate_parts(s, st, cfg)
        mu_r0 = np.maximum(s["min_mu"].to_numpy(float) * parts["r_R0"], cfg["mean_floor"])
        err = float(np.max(np.abs(mu_r0 - s["PTS_mu"].to_numpy(float))))
        phi = float(st["dispersion"]["pts"]["phi"])
        checks[F_] = {"candidate_rows": int(len(s)), "R0_PTS_mu_max_abs_reproduction_error": err,
                      "phi": phi, "phi_store": float(s["PTS_phi"].iloc[0]), "k": parts["k"],
                      "state_train_through": st["train_through"]}
        if err > 1e-9 or abs(phi - checks[F_]["phi_store"]) > 1e-12:
            raise ChallengerViolation(f"F{F_}: R0 not reproduced ({err})")
        pl = (s["participation_status"].eq("PLAYED") & s["y_PTS"].notna()).to_numpy()
        checks[F_]["fit_rows"], checks[F_]["fit_games"] = int(pl.sum()), int(s.loc[pl, "game_id"].nunique())
        folds.append({"F": F_, "parts": {k: (v[pl] if isinstance(v, np.ndarray) else v) for k, v in parts.items()},
                      "min_mu": s["min_mu"].to_numpy(float)[pl], "y": s["y_PTS"].to_numpy(float)[pl], "phi": phi,
                      "floor": cfg["mean_floor"], "all_parts": parts, "all_mu_r0": mu_r0,
                      "all_min_mu": s["min_mu"].to_numpy(float)})
    n = sum(len(f["y"]) for f in folds)
    t0 = time.time()

    def score(mu_fn):
        ls = cr = 0.0
        for f in folds:
            l_, c_ = _nb_scores(mu_fn(f), f["phi"], f["y"])
            ls, cr = ls + l_.sum(), cr + c_.sum()
        return ls / n, cr / n

    r0_log, r0_crps = score(lambda f: np.maximum(f["min_mu"] * f["parts"]["r_R0"], f["floor"]))
    curve = []
    for i, tau in enumerate(TAU_GRID):
        lg, cr = score(lambda f, t=tau: np.maximum(f["min_mu"] * r_r1(f["parts"], t), f["floor"]))
        curve.append({"i": i, "tau": float(tau), "mean_log_score": float(lg), "mean_crps": float(cr)})
        if i % 40 == 0:
            log(f"[fit] grid {i}/{N_GRID - 1} tau {tau:.2f} log {lg:.6f}  {time.time() - t0:.0f}s")
    logs = np.array([c["mean_log_score"] for c in curve])
    i_star = int(np.flatnonzero(logs == logs.min())[0])
    tau = float(TAU_GRID[i_star])
    num = {F_: numerics(f["all_parts"], tau, f["all_mu_r0"], f["all_min_mu"], f["floor"]) for F_, f in zip(FIT_FOLDS, folds)}
    out = {"experiment": "PTS rate challenger R1 (research)", **pf,
           "objective": "mean PTS negative log score under props_v0.pmf_matrix(mu_1B'(tau), phi_fold, 120), pooled "
                        "PLAYED rows with y_PTS of F2023 + F2024 (true-OOS Phase 1B folds)",
           "grid": {"bounds": [TAU_LO, TAU_HI], "points": N_GRID, "rule": "tau_i = 2000^(i/240); argmin, tie -> smaller i"},
           "TAU": tau, "TAU_INDEX": i_star, "TAU_UNITS": "pseudo-minutes",
           "TAU_IDENTIFICATION_SUSPECT": i_star in SUSPECT_IDX,
           "fit_rows": n, "fit_checks": {str(k): v for k, v in checks.items()},
           "R0_on_fit_rows": {"mean_log_score": float(r0_log), "mean_crps": float(r0_crps)},
           "R1_at_tau_on_fit_rows": {"mean_log_score": curve[i_star]["mean_log_score"], "mean_crps": curve[i_star]["mean_crps"]},
           "secondary_crps_argmin_tau": float(TAU_GRID[int(np.argmin([c["mean_crps"] for c in curve]))]),
           "numerics_fit_seasons": {str(k): v for k, v in num.items()},
           "curve": curve, "seconds": round(time.time() - t0, 1), "api_calls": 0}
    FIT_PATH.write_text(json.dumps(out, indent=1))
    log(f"[fit] TAU = {tau:.3f} (i = {i_star}); R0 log {r0_log:.6f} vs R1 {curve[i_star]['mean_log_score']:.6f}")
    return out


def frozen_tau() -> float:
    rec = json.loads(FIT_PATH.read_text())
    if rec["protocol_commit"] != PROTOCOL_COMMIT:
        raise ChallengerViolation("fit record from another protocol")
    _git("ls-files", "--error-unmatch", str(FIT_PATH.relative_to(C.PROJECT_ROOT)))      # the fit must be committed
    if _git("status", "--porcelain", str(FIT_PATH.relative_to(C.PROJECT_ROOT))):
        raise ChallengerViolation("the fit record differs from its committed version")
    return float(rec["TAU"])


# ============================================================================ exact frozen JM1 PTS scorer
def _subsample(gid: str) -> bool:
    return int(hashlib.sha256(gid.encode()).hexdigest()[:8], 16) % 5 == 0


def _params(path: Path) -> tuple[dict, object]:
    a = json.loads(path.read_text())
    params = {"struct": {"env": a["structure"]["env"], "glm": a["structure"]["glm"]}, "s4": a["s4_components"],
              "e_dur": a["e_dur"], "T": a["T"]}
    return params, D.structure("J2", a["s4_components"]["residual_dependence"])


def score_game(gid, gp, gp1, envd, params, s4_struct, sims=F.SIMS, combos=False) -> dict:
    """R0 and R1 PTS (and, if combos, PRA / PR / PA) for one game from common random numbers. gp: frozen Phase 1B rows;
    gp1: identical except PTS_mu. Every call is the frozen jm1_final.eval_game path, PTS (+REB, AST) only."""
    gi = P3._game_input(gid, gp, envd)                 # exposure draws do not depend on the count means
    rows = gp[gp["_scored"]].reset_index(drop=True)
    rows1 = gp1[gp1["_scored"]].reset_index(drop=True)
    n = len(rows)
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    y = rows[[f"y_{s}" for s in L.STATS]].to_numpy(dtype=float)
    dur, x, Mx = F.simulate_exposure(gi, params["struct"]["env"], params["e_dur"], sims)
    sv = S.State(dur, x, Mx, np.zeros((sims, n, len(S.FAM))), np.zeros((sims, 0)), {}, np.zeros((n, len(S.FAM))), 0.0)
    min_mu = rows["min_mu"].to_numpy(dtype=float)
    phic = np.array([params["s4"]["dispersion"][s]["phi"] for s in L.STATS])
    lms = {}
    for arm, rr in (("R0", rows), ("R1", rows1)):
        base_lm = np.log(rr[[f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float))
        lm, _ = S.anchor(S.log_mu_terms(base_lm, min_mu, team, sv, params["struct"]["glm"], JM.JM_TERMS), base_lm, JM.JM1_ALPHA)
        lms[arm] = lm
    if not np.array_equal(lms["R0"][:, :, 1:], lms["R1"][:, :, 1:]):
        raise ChallengerViolation(f"{gid}: a non-PTS conditional mean changed")
    out = {"rows": rows, "single": {}, "combo": {}}
    tabs = {}
    for arm in ("R0", "R1"):
        tabs[arm] = S.mixture_tables(lms[arm][:, :, 0], np.full(J.NS, phic[0]))
        out["single"][arm] = P3.single_metrics(S.mixture_pmf(tabs[arm], sims), y[:, 0], np.zeros(n, dtype=int))
    if combos:
        E = np.random.default_rng(D.game_seed(J.SIM_SEED, gid)).standard_normal((sims, n * J.NS))
        L4, _ = D.cholesky_unit(D.game_correlation(s4_struct, list(D.group_of(rows)), team)["corr"])
        marg0 = J.game_marginals(rows)
        lines = J.combo_lines(marg0)
        xs = np.zeros((sims, n, J.NS), dtype=np.int16)
        for j in IDX[1:]:
            cj = np.arange(n) * J.NS + j
            xs[:, :, j] = S.counts(S.mixture_tables(lms["R0"][:, :, j], np.full(J.NS, phic[j])), E @ L4[cj].T)
        zp = E @ L4[np.arange(n) * J.NS].T
        for arm in ("R0", "R1"):
            xa = xs.copy()
            xa[:, :, 0] = S.counts(tabs[arm], zp)
            out["combo"][arm] = J.combo_scores(xa, y, lines, sims)
    return out


def evaluate(season: str, log=print, limit_games: int | None = None, write: bool = True) -> dict:
    pf = protocol_frozen()
    tau = frozen_tau()
    if write and EVAL_PATH[season].exists():
        raise ChallengerViolation(f"{season} is evaluated exactly once")
    if season == "2025-26" and write and not EVAL_PATH["2024-25"].exists():
        raise ChallengerViolation("development evaluation (2024-25) first")
    e = EVAL[season]
    F_ = e["F"]
    params, s4_struct = _params(e["params"])
    st = _state(F_)
    df, envp = P3.season_inputs(F_, params["struct"])
    h, cfg = history(F_)
    df = df.merge(h, on="player_game_example_id", how="left", validate="many_to_one")
    if df["pos"].isna().any():
        raise ChallengerViolation("history join incomplete")
    parts = rate_parts(df, st, cfg)
    mu_r0 = np.maximum(df["min_mu"].to_numpy(float) * parts["r_R0"], cfg["mean_floor"])
    repro_1b = float(np.max(np.abs(mu_r0 - df["PTS_mu"].to_numpy(float))))
    if repro_1b > 1e-9:
        raise ChallengerViolation(f"{season}: Phase 1B R0 PTS mean not reproduced ({repro_1b})")
    df1 = df.copy()
    df1["PTS_mu"] = np.maximum(df["min_mu"].to_numpy(float) * r_r1(parts, tau), cfg["mean_floor"])
    num = numerics(parts, tau, mu_r0, df["min_mu"].to_numpy(float), cfg["mean_floor"])
    num["non_pts_phase1b_means_bit_identical"] = bool(all(np.array_equal(df[f"{s}_mu"].to_numpy(), df1[f"{s}_mu"].to_numpy())
                                                          for s in L.STATS[1:]))
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    g0, g1 = dict(list(df.groupby("game_id", sort=True))), dict(list(df1.groupby("game_id", sort=True)))
    gids = sorted(g0)[:limit_games] if limit_games else sorted(g0)
    keys = ("log", "crps", "mean", "var", "pit_lo", "pit_hi", "pover0", "pover1", "pover2")
    parts_rows, combo_rows = [], []
    t0 = time.time()
    for gi_, gid in enumerate(gids):
        sub = _subsample(gid)
        r = score_game(gid, g0[gid], g1[gid], envd, params, s4_struct, combos=sub)
        blk = r["rows"][["game_id", "player_game_example_id"]].copy()
        for arm in ("R0", "R1"):
            for k in keys:
                blk[f"{arm}|{k}"] = r["single"][arm][k]
        parts_rows.append(blk)
        if sub:
            for p, pid in enumerate(r["rows"]["player_game_example_id"]):
                rec = {"game_id": gid, "player_game_example_id": pid}
                for arm in ("R0", "R1"):
                    for cb in COMBOS:
                        c = r["combo"][arm][cb]
                        rec[f"{arm}|{cb}|log"], rec[f"{arm}|{cb}|crps"] = float(c["log"][p]), float(c["crps"][p])
                        for li, q in enumerate((25, 50, 75)):
                            rec[f"{arm}|{cb}|brier_q{q}"] = float(c["brier"][p, li])
                combo_rows.append(rec)
        if (gi_ + 1) % 500 == 0 or gi_ + 1 == len(gids):
            el = time.time() - t0
            log(f"[{season}] {gi_ + 1}/{len(gids)} games {el / 60:.1f} min eta {(len(gids) - gi_ - 1) * el / (gi_ + 1) / 60:.1f} min")
    rows = pd.concat(parts_rows, ignore_index=True)
    combo = pd.DataFrame(combo_rows)
    pk = df[["player_game_example_id", "cur_min", "PTS_mu"]].rename(columns={"PTS_mu": "mu1B_R0"})
    pk["mu1B_R1"] = df1["PTS_mu"].to_numpy()
    pk["w_current"] = parts["cur_min"] / (parts["cur_min"] + tau)
    pk["r_prior"], pk["r_R0"], pk["r_R1"] = parts["r_prior"], parts["r_R0"], r_r1(parts, tau)
    rows = rows.merge(pk, on="player_game_example_id", how="left", validate="one_to_one")
    stored = DA.SOURCES[season]
    a = stored["arm"]
    sv = pd.read_parquet(C.PROJECT_ROOT / stored["path"],
                         columns=["player_game_example_id", "prediction_cutoff_utc", "team_games", "cold_start_class",
                                  "min_mu", "y_min", "y_PTS", "y_REB", "y_AST"] + [f"{a}|{s}|{k}" for s in ("PTS", "REB", "AST")
                                                                                  for k in ("log", "crps", "mean")])
    rows = rows.merge(sv, on="player_game_example_id", how="left", validate="one_to_one")
    repro = {k: float(np.max(np.abs(rows[f"R0|{k}"] - rows[f"{a}|PTS|{k}"]) / np.maximum(np.abs(rows[f"{a}|PTS|{k}"]), 1.0)))
             for k in ("log", "crps", "mean")}
    gt = pd.read_parquet(C.PROJECT_ROOT / stored["games"])
    cg = combo.groupby("game_id").sum(numeric_only=True).reset_index().merge(gt, on="game_id", how="left")
    for cb in COMBOS:
        for k, kk in (("log", "LOG"), ("crps", "CRPS"), ("brier_q25", "BRIER_q25"), ("brier_q50", "BRIER_q50"), ("brier_q75", "BRIER_q75")):
            repro[f"{cb}_{kk}"] = float(np.max(np.abs(cg[f"R0|{cb}|{k}"] - cg[f"{a}|{cb}_{kk}"]) /
                                               np.maximum(np.abs(cg[f"{a}|{cb}_{kk}"]), 1.0)))
    ok = bool(max(repro.values()) <= REPRO_RTOL and rows[f"{a}|PTS|log"].notna().all())
    if not limit_games:
        ok = ok and len(rows) == len(sv)
    rec = {"season": season, **pf, "TAU": tau, "rows": int(len(rows)), "games": len(gids),
           "combo_subsample_games": int(combo["game_id"].nunique()) if len(combo) else 0,
           "combo_subsample_rows": int(len(combo)), "phase1b_R0_reproduction_max_abs": repro_1b,
           "R0_reproduction_max_relative": repro, "R0_REPRODUCED": bool(ok), "numerics": num,
           "seconds": round(time.time() - t0, 1), "draws_per_game": F.SIMS, "api_calls": 0, "structure_T": params["T"]}
    rec["summary"] = summarize(rows, combo, season)
    if write:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        rows.to_parquet(SCRATCH / f"{season}_pts_rows.parquet", index=False)
        combo.to_parquet(SCRATCH / f"{season}_combo_rows.parquet", index=False)
        EVAL_PATH[season].write_text(json.dumps(rec, indent=1, default=float))
    return rec


# ============================================================================ metrics
def _arm(rows: pd.DataFrame, arm: str) -> dict:
    y = rows["y_PTS"].to_numpy(float)
    lo, hi = rows[f"{arm}|pit_lo"].to_numpy(float), rows[f"{arm}|pit_hi"].to_numpy(float)
    pa = lambda u: DA._p_above(lo, hi, u)
    br = np.mean([(rows[f"{arm}|pover{li}"].to_numpy(float) - (y > DA.HALF_LINES["PTS"][li])) ** 2 for li in range(3)], axis=0)
    m = rows[f"{arm}|mean"].to_numpy(float)
    return {"mean": m, "err": m - y, "abs": np.abs(m - y), "sq": (m - y) ** 2, "log": rows[f"{arm}|log"].to_numpy(float),
            "crps": rows[f"{arm}|crps"].to_numpy(float), "crps_scaled": rows[f"{arm}|crps"].to_numpy(float) / PTS_SCALE,
            "brier": br, "mid_pit": 0.5 * (lo + hi), "P_pit_lt_0.25": 1 - pa(0.25), "P_pit_lt_0.5": 1 - pa(0.5),
            "P_pit_lt_0.75": 1 - pa(0.75), "lower_miss_0.1": 1 - pa(0.1), "upper_miss_0.9": pa(0.9),
            "cov80": pa(0.1) - pa(0.9), "cov90": pa(0.05) - pa(0.95)}


def _block(rows, A, bt, m) -> dict:
    y = rows["y_PTS"].to_numpy(float)
    out = {"N": int(m.sum()), "mean_actual": float(y[m].mean()) if m.any() else None}
    if not m.any():
        return out
    for arm in ("R0", "R1"):
        a = A[arm]
        b = {"mean_forecast": float(a["mean"][m].mean()), "bias": bt.mean(a["err"], m),
             "MAE": float(a["abs"][m].mean()), "RMSE": float(np.sqrt(a["sq"][m].mean()))}
        b["relative_bias"] = b["bias"]["value"] / out["mean_actual"] if out["mean_actual"] else None
        for k in ("log", "crps", "crps_scaled", "brier", *TAILS):
            b[k] = float(a[k][m].mean())
        b["tail_asymmetry"] = b["upper_miss_0.9"] - b["lower_miss_0.1"]
        out[arm] = b
    out["delta"] = {k: bt.mean(A["R1"][k] - A["R0"][k], m) for k in ("log", "crps", "crps_scaled", "brier")}
    out["delta"]["relative_log_pct"] = 100 * out["delta"]["log"]["value"] / out["R0"]["log"]
    out["delta"]["relative_crps_pct"] = 100 * out["delta"]["crps"]["value"] / out["R0"]["crps"]
    return out


def summarize(rows: pd.DataFrame, combo: pd.DataFrame, season: str) -> dict:
    bt = DA.Boot(rows["game_id"].to_numpy())
    A = {arm: _arm(rows, arm) for arm in ("R0", "R1")}
    mu = rows["min_mu"].to_numpy(float)
    cm = rows["cur_min"].fillna(0).to_numpy(float)
    t = pd.to_datetime(rows["prediction_cutoff_utc"], utc=True).dt.tz_convert("America/New_York")
    day = (t.dt.normalize() - t.dt.normalize().min()).dt.days.to_numpy()
    cold = rows["cold_start_class"].fillna("UNKNOWN").to_numpy()
    masks = {b: (mu >= lo) & (mu < hi) for b, (lo, hi) in BANDS.items()}
    masks["EARLY_TEAM_GAMES_LE2"] = rows["team_games"].fillna(0).to_numpy(float) <= DA.EARLY_TEAM_GAMES
    masks["EARLY_FIRST_30_DAYS"] = day < DA.EARLY_DAYS
    masks.update({b: (cm > lo) & (cm <= hi) if np.isfinite(lo) else cm <= hi for b, (lo, hi) in EVIDENCE.items()})
    masks.update({f"STATE_{c}": cold == c for c in COLD})
    out = {"strata": {k: _block(rows, A, bt, m) for k, m in masks.items()}}
    for b in EVIDENCE:
        mk = masks[b]
        out["strata"][b]["mean_w_current"] = float(rows["w_current"].to_numpy(float)[mk].mean()) if mk.any() else None
    bands = ("MIN_0_10", "MIN_10_20", "MIN_20_30", "MIN_30PLUS")
    comp = {}
    for arm in ("R0", "R1"):
        bv = np.array([out["strata"][b][arm]["bias"]["value"] for b in bands])
        nv = np.array([out["strata"][b]["N"] for b in bands], float)
        xi = np.arange(4.0)
        xm = np.average(xi, weights=nv)
        slope = float(np.sum(nv * (xi - xm) * (bv - np.average(bv, weights=nv))) / np.sum(nv * (xi - xm) ** 2))
        comp[arm] = {"C_bias_0_10_minus_30plus": float(bv[0] - bv[3]), "weighted_slope_per_band": slope,
                     "band_bias": dict(zip(bands, bv.tolist()))}
    out["compression"] = comp
    # combos: mean bias on ALL rows (exact linearity: combo mean = sum of stat means; REB / AST = stored JM1 means)
    a = DA.SOURCES[season]["arm"]
    cmb = {}
    hi_m = masks["MIN_30PLUS"]
    for cb, pts_ in (("PRA", ("REB", "AST")), ("PR", ("REB",)), ("PA", ("AST",))):
        yc = rows["y_PTS"].to_numpy(float) + sum(rows[f"y_{s}"].to_numpy(float) for s in pts_)
        oth = sum(rows[f"{a}|{s}|mean"].to_numpy(float) for s in pts_)
        e0, e1 = A["R0"]["mean"] + oth - yc, A["R1"]["mean"] + oth - yc
        c = {"bias_all_rows": {"R0": bt.mean(e0), "R1": bt.mean(e1)},
             "bias_min_30plus": {"R0": bt.mean(e0, hi_m), "R1": bt.mean(e1, hi_m)},
             "bias_low_lt20": {"R0": bt.mean(e0, masks["LOW_LT20"]), "R1": bt.mean(e1, masks["LOW_LT20"])}}
        if len(combo):
            cbt = DA.Boot(combo["game_id"].to_numpy())
            d = {}
            for k in ("log", "crps"):
                d[k] = cbt.mean(combo[f"R1|{cb}|{k}"].to_numpy(float) - combo[f"R0|{cb}|{k}"].to_numpy(float))
                d[f"R0_mean_{k}"] = float(combo[f"R0|{cb}|{k}"].mean())
            br = {arm: np.mean([combo[f"{arm}|{cb}|brier_q{q}"].to_numpy(float) for q in (25, 50, 75)], axis=0) for arm in ("R0", "R1")}
            d["brier_mean_q25_q50_q75"] = cbt.mean(br["R1"] - br["R0"])
            d["R0_mean_brier"] = float(br["R0"].mean())
            c["subsample_scores_R1_minus_R0"] = d
        cmb[cb] = c
    out["combos"] = cmb
    return out


# ============================================================================ gates
def _hi(ci: dict) -> float:
    return float(ci["hi"])


def gates(ev: dict) -> dict:
    g = {}
    for season, rec in ev.items():
        s = rec["summary"]["strata"]
        cp = rec["summary"]["compression"]
        gg = {}
        b0, b1 = s["MIN_30PLUS"]["R0"]["bias"]["value"], s["MIN_30PLUS"]["R1"]["bias"]["value"]
        gg["A_high_exposure_bias"] = {"R0": b0, "R1": b1, "reduction_pct": 100 * (1 - abs(b1) / abs(b0)),
                                      "PASS": abs(b1) <= G_HIGH * abs(b0)}
        c0, c1 = cp["R0"]["C_bias_0_10_minus_30plus"], cp["R1"]["C_bias_0_10_minus_30plus"]
        gg["B_compression"] = {"R0": c0, "R1": c1, "reduction_pct": 100 * (1 - abs(c1) / abs(c0)), "PASS": abs(c1) <= G_COMP * abs(c0)}
        d = s["ALL"]["delta"]
        gg["C_aggregate_log"] = {"delta": d["log"], "relative_pct": d["relative_log_pct"], "margin": G_AGG_LOG,
                                 "PASS": _hi(d["log"]) <= G_AGG_LOG}
        gg["D_aggregate_crps"] = {"delta_scaled": d["crps_scaled"], "relative_pct": d["relative_crps_pct"], "margin": G_AGG_CRPS,
                                  "PASS": _hi(d["crps_scaled"]) <= G_AGG_CRPS}
        dh = s["MIN_30PLUS"]["delta"]
        gg["E_high_exposure_scores"] = {"log": dh["log"], "crps_scaled": dh["crps_scaled"],
                                        "PASS": _hi(dh["log"]) <= G_GROUP and _hi(dh["crps_scaled"]) <= G_GROUP}
        lo = s["LOW_LT20"]
        lw = abs(lo["R1"]["bias"]["value"]) - abs(lo["R0"]["bias"]["value"])
        gg["F_low_exposure"] = {"abs_bias_change": lw, "log": lo["delta"]["log"], "crps_scaled": lo["delta"]["crps_scaled"],
                                "PASS": lw <= G_BIAS_WORSE and _hi(lo["delta"]["log"]) <= G_GROUP
                                and _hi(lo["delta"]["crps_scaled"]) <= G_GROUP}
        early = {}
        for k in ("EARLY_TEAM_GAMES_LE2", "EARLY_FIRST_30_DAYS"):
            e = s[k]
            bw = abs(e["R1"]["bias"]["value"]) - abs(e["R0"]["bias"]["value"])
            pw = abs(e["R1"]["mid_pit"] - 0.5) - abs(e["R0"]["mid_pit"] - 0.5)
            cw = abs(e["R1"]["cov80"] - 0.8) - abs(e["R0"]["cov80"] - 0.8)
            early[k] = {"abs_bias_change": bw, "mid_pit_worsening": pw, "cov80_worsening": cw,
                        "log": e["delta"]["log"], "crps_scaled": e["delta"]["crps_scaled"],
                        "PASS": _hi(e["delta"]["log"]) <= G_GROUP and _hi(e["delta"]["crps_scaled"]) <= G_GROUP
                        and bw <= G_BIAS_WORSE and pw <= G_CAL and cw <= G_CAL}
        gg["G_early_season"] = {**early, "PASS": all(v["PASS"] for v in early.values())}
        tails = {}
        for k in ("ALL", "MIN_30PLUS"):
            tails[k] = {d_: abs(s[k]["R1"][d_] - tgt) - abs(s[k]["R0"][d_] - tgt) for d_, tgt in TAILS.items()}
        gg["H_tails"] = {"worsening": tails, "PASS": all(v <= G_CAL for t in tails.values() for v in t.values())}
        cb = {}
        for c, v in rec["summary"]["combos"].items():
            sc = v["subsample_scores_R1_minus_R0"]
            bw = abs(v["bias_all_rows"]["R1"]["value"]) - abs(v["bias_all_rows"]["R0"]["value"])
            cb[c] = {"log_hi": _hi(sc["log"]), "crps_hi_rel": _hi(sc["crps"]) / sc["R0_mean_crps"],
                     "brier_hi": _hi(sc["brier_mean_q25_q50_q75"]), "abs_bias_change_all_rows": bw}
            cb[c]["PASS"] = (cb[c]["log_hi"] <= G_GROUP and cb[c]["crps_hi_rel"] <= G_COMBO_CRPS_REL
                             and cb[c]["brier_hi"] <= G_COMBO_BRIER and bw <= G_BIAS_WORSE)
        gg["I_combos"] = {**cb, "PASS": all(v["PASS"] for v in cb.values())}
        n = rec["numerics"]
        cs = {c: _hi(s[f"STATE_{c}"]["delta"]["log"]) for c in ("COLD_TRANSFER_IN", "COLD_NO_HISTORY") if s[f"STATE_{c}"]["N"]}
        gg["J_numerics_cold_start"] = {"numerics": n, "cold_log_hi": cs,
                                       "PASS": n["nonfinite"] == 0 and n["nonpositive"] == 0 and n["outside_convex_hull"] == 0
                                       and n["zero_evidence_mu_not_bit_identical"] == 0 and n["non_pts_phase1b_means_bit_identical"]
                                       and all(v <= G_GROUP for v in cs.values())}
        gg["PRECONDITION_R0_REPRODUCED"] = rec["R0_REPRODUCED"]
        g[season] = gg
    return g


def decide(log=print) -> dict:
    pf = protocol_frozen()
    fitrec = json.loads(FIT_PATH.read_text())
    ev = {s: json.loads(p.read_text()) for s, p in EVAL_PATH.items()}
    g = gates(ev)
    letters = [k for k in g["2024-25"] if k[0] in "ABCDEFGHIJ" and k[1] == "_"]
    fails = [f"{s}:{k}" for s in g for k in letters if not g[s][k]["PASS"]]
    num_fit_ok = all(v["nonfinite"] == 0 and v["nonpositive"] == 0 and v["outside_convex_hull"] == 0
                     and v["zero_evidence_mu_not_bit_identical"] == 0 for v in fitrec["numerics_fit_seasons"].values())
    if not num_fit_ok:
        fails.append("fit_seasons:J_numerics")
    if not all(g[s]["PRECONDITION_R0_REPRODUCED"] for s in g):
        decision = "R1_INCONCLUSIVE"
    elif fails:
        decision = "R1_REJECT"
    else:
        decision = "R1_ACCEPT_PENDING_JOINT_CONFIRMATION"
    out = {**pf, "TAU": fitrec["TAU"], "TAU_IDENTIFICATION_SUSPECT": fitrec["TAU_IDENTIFICATION_SUSPECT"],
           "gates": g, "failed_gates": fails, "RATE_CHALLENGER_DECISION": decision,
           "PRODUCTION_MODEL_CHANGED": False, "PUBLIC_LAUNCH_STATUS": "HOLD"}
    RESULT_PATH.write_text(json.dumps(out, indent=1, default=float))
    log(f"DECISION {decision}; failed {fails}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fit":
        fit()
    elif cmd == "evaluate":
        evaluate(sys.argv[2])
    elif cmd == "decide":
        decide()
    else:
        raise SystemExit(__doc__)
