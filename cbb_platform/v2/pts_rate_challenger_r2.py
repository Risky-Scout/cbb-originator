"""PTS RATE CHALLENGER R2 (research; protocol modeling/player_props_v2/PTS_RATE_CHALLENGER_R2_PROTOCOL.md, commit 0126280,
frozen before any R2 quantity was computed). Damped adaptive empirical-Bayes PTS per-minute rate, TWO global parameters:

    r_R2 = r_prior + rho * n/(n + tau) * (cur_pts/n - r_prior)    (n = cur_min > 0);   r_R2 = r_prior when n = 0

The prior, the exact frozen JM1 PTS scorer and the metric / bootstrap helpers are the R1 module's, reused unchanged
(`pts_rate_challenger`). Internally those helpers label the challenger arm "R1"; every record written here relabels it "R2".

Stages (in order; each runs only if the previous one passed, each writes a record):
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger_r2 fit               tau, rho on 2022-23 + 2023-24 (Phase 1B NB)
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger_r2 screen            stage A, 2024-25, analytic
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger_r2 evaluate 2024-25  exact JM1 PTS
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger_r2 evaluate 2025-26  exact JM1 PTS
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger_r2 combos            PRA / PR / PA, both seasons
    .venv/bin/python -m cbb_platform.v2.pts_rate_challenger_r2 decide
No API call, no refit, no change to any frozen file. Not a production component.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import directional_audit as DA
from cbb_platform.v2 import jm1_final as F, latent as L, phase3 as P3
from cbb_platform.v2 import pts_rate_challenger as R1

PROTOCOL = R1.OUT / "PTS_RATE_CHALLENGER_R2_PROTOCOL.md"
PROTOCOL_COMMIT = "0126280eb56cab7fafe22f22154c412779b77dc5"
OUT = R1.OUT
FIT_PATH = OUT / "pts_rate_challenger_r2_fit.json"
SCREEN_PATH = OUT / "pts_rate_challenger_r2_screen_2024_25.json"
EVAL_PATH = {"2024-25": OUT / "pts_rate_challenger_r2_eval_2024_25.json", "2025-26": OUT / "pts_rate_challenger_r2_eval_2025_26.json"}
COMBO_PATH = OUT / "pts_rate_challenger_r2_combos.json"
RESULT_PATH = OUT / "pts_rate_challenger_r2_results.json"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_pts_rate_r2"
R1_ROWS = {s: R1.SCRATCH / f"{s}_pts_rows.parquet" for s in R1.EVAL}

TAU_LO, TAU_HI = 1.0, 2000.0
TAU_C = TAU_HI ** (np.arange(41) / 40)                           # coarse tau_i = 2000^(i/40)
RHO_C = np.arange(21) / 20                                       # coarse rho_j = j/20
N_REF = 21
G_OVERSHOOT = 0.15                                               # gate A: bias_R2(>=30) <= +0.15
G_AGG_BIAS = 0.05                                                # gate K
G_SCREEN_LOG = R1.G_AGG_LOG                                      # screen SC: point dLOG <= +0.002


def protocol_frozen() -> dict:
    committed = R1._git("show", f"{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(C.PROJECT_ROOT)}")
    if committed != PROTOCOL.read_text().strip():
        raise R1.ChallengerViolation("the R2 protocol changed after its freeze commit")
    return {"protocol_commit": PROTOCOL_COMMIT, "protocol_sha256": R1._sha(PROTOCOL)}


def r_r2(parts: dict, tau: float, rho: float) -> np.ndarray:
    """Protocol section 2. n = 0 rows (including n = 0 with points) take r_prior exactly."""
    cm, cp, rp = parts["cur_min"], parts["cur_pts"], parts["r_prior"]
    pos = cm > 0
    nn = np.where(pos, cm, 1.0)
    return np.where(pos, rp + rho * (nn / (nn + tau)) * (cp / nn - rp), rp)


def eff_weight(parts: dict, tau: float, rho: float) -> np.ndarray:
    cm = parts["cur_min"]
    return np.where(cm > 0, rho * cm / (np.where(cm > 0, cm, 1.0) + tau), 0.0)


def numerics(parts: dict, tau: float, rho: float, mu_r0, min_mu, floor) -> dict:
    r2 = r_r2(parts, tau, rho)
    cm, cp, rp = parts["cur_min"], parts["cur_pts"], parts["r_prior"]
    pos = cm > 0
    rc = np.where(pos, cp / np.where(pos, cm, 1.0), rp)
    lo, hi = np.minimum(rc, rp), np.maximum(rc, rp)
    mu2 = np.maximum(min_mu * r2, floor)
    z, q = (cm <= 0) & (cp == 0), (cm <= 0) & (cp != 0)
    w = eff_weight(parts, tau, rho)
    bm = [float(w[(cm <= 0)].mean()) if (cm <= 0).any() else 0.0] + \
         [float(w[(cm > a) & (cm <= b)].mean()) for a, b in ((0, 100), (100, 300), (300, np.inf)) if ((cm > a) & (cm <= b)).any()]
    return {"rows": int(len(r2)), "nonfinite": int((~np.isfinite(r2)).sum()), "nonpositive": int((r2 <= 0).sum()),
            "outside_convex_hull": int((pos & ((r2 < lo - 1e-12) | (r2 > hi + 1e-12))).sum()),
            "zero_evidence_rows": int(z.sum()),
            "zero_evidence_mu_not_bit_identical": int((mu2[z] != mu_r0[z]).sum()),
            "cur_min_zero_rows_not_r_prior": int((r2[cm <= 0] != rp[cm <= 0]).sum()),
            "cur_min_zero_with_points_rows": int(q.sum()),
            "eff_weight_min": float(w.min()), "eff_weight_max": float(w.max()),
            "eff_weight_in_0_rho": bool((w >= 0).all() and ((w < rho).all() if rho > 0 else (w == 0).all())),
            "eff_weight_bucket_means": bm, "eff_weight_bucket_monotone": bool(np.all(np.diff(bm) >= 0)),
            "max_r_R2": float(np.nanmax(r2)), "min_r_R2": float(np.nanmin(r2))}


def _relabel(o):
    """Challenger arm 'R1' (R1 helper naming) -> 'R2' in every key of a record."""
    if isinstance(o, dict):
        return {(k.replace("R1", "R2") if isinstance(k, str) else k): _relabel(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_relabel(v) for v in o]
    return o


# ============================================================================ fit
def _fit_folds() -> tuple[list, dict]:
    folds, checks = [], {}
    for F_, path in R1.FIT_FOLDS.items():
        st = R1._state(F_)
        h, cfg = R1.history(F_)
        s = pd.read_parquet(path, columns=["player_game_example_id", "forecast_season", "min_mu", "PTS_mu", "PTS_phi",
                                           "participation_status", "y_PTS", "game_id", "training_through_season"])
        s = s[s["forecast_season"] == F_].merge(h, on="player_game_example_id", how="left", validate="one_to_one")
        if s["pos"].isna().any() or (s["training_through_season"] >= F_).any():
            raise R1.ChallengerViolation(f"F{F_}: history join incomplete or not out-of-sample")
        parts = R1.rate_parts(s, st, cfg)
        mu_r0 = np.maximum(s["min_mu"].to_numpy(float) * parts["r_R0"], cfg["mean_floor"])
        err = float(np.max(np.abs(mu_r0 - s["PTS_mu"].to_numpy(float))))
        phi = float(st["dispersion"]["pts"]["phi"])
        if err > 1e-9 or abs(phi - float(s["PTS_phi"].iloc[0])) > 1e-12:
            raise R1.ChallengerViolation(f"F{F_}: R0 not reproduced ({err})")
        pl = (s["participation_status"].eq("PLAYED") & s["y_PTS"].notna()).to_numpy()
        checks[str(F_)] = {"candidate_rows": int(len(s)), "R0_PTS_mu_max_abs_reproduction_error": err, "phi": phi,
                           "k": parts["k"], "fit_rows": int(pl.sum()), "fit_games": int(s.loc[pl, "game_id"].nunique())}
        folds.append({"F": F_, "parts": {k: (v[pl] if isinstance(v, np.ndarray) else v) for k, v in parts.items()},
                      "min_mu": s["min_mu"].to_numpy(float)[pl], "y": s["y_PTS"].to_numpy(float)[pl], "phi": phi,
                      "floor": cfg["mean_floor"], "all_parts": parts, "all_mu_r0": mu_r0, "all_min_mu": s["min_mu"].to_numpy(float)})
    return folds, checks


def _best(cands: list) -> dict:
    """Lowest log; ties (exact equality) -> larger tau, then smaller rho."""
    return min(cands, key=lambda c: (c["log"], -c["tau"], c["rho"]))


def fit(log=print) -> dict:
    if FIT_PATH.exists():
        raise R1.ChallengerViolation("tau / rho are fitted exactly once; the fit record already exists")
    pf = protocol_frozen()
    folds, checks = _fit_folds()
    n = sum(len(f["y"]) for f in folds)
    t0 = time.time()

    def score(mu_fn):
        ls = cr = 0.0
        for f in folds:
            l_, c_ = R1._nb_scores(mu_fn(f), f["phi"], f["y"])
            ls, cr = ls + l_.sum(), cr + c_.sum()
        return float(ls / n), float(cr / n)

    def at(tau, rho):
        lg, cr = score(lambda f: np.maximum(f["min_mu"] * r_r2(f["parts"], tau, rho), f["floor"]))
        return {"tau": float(tau), "rho": float(rho), "log": lg, "crps": cr}

    r0_log, r0_crps = score(lambda f: np.maximum(f["min_mu"] * f["parts"]["r_R0"], f["floor"]))
    coarse = []
    for i, tau in enumerate(TAU_C):
        for j, rho in enumerate(RHO_C):
            coarse.append({"i": i, "j": j, **at(tau, rho)})
        if i % 10 == 0:
            log(f"[fit] coarse tau {i}/40  {time.time() - t0:.0f}s")
    cb = _best(coarse)
    i_, j_ = cb["i"], cb["j"]
    t_lo, t_hi = TAU_C[max(i_ - 1, 0)], TAU_C[min(i_ + 1, 40)]
    p_lo, p_hi = RHO_C[max(j_ - 1, 0)], RHO_C[min(j_ + 1, 20)]
    taus = np.geomspace(t_lo, t_hi, N_REF)
    rhos = np.linspace(p_lo, p_hi, N_REF)
    refine = [{"a": a, "b": b, **at(t, r)} for a, t in enumerate(taus) for b, r in enumerate(rhos)]
    best = _best(coarse + refine)
    tau, rho = best["tau"], best["rho"]
    boundary = bool(tau <= TAU_C[1] or tau >= TAU_C[39] or rho <= 0.05 or rho >= 0.95)
    num = {str(f["F"]): numerics(f["all_parts"], tau, rho, f["all_mu_r0"], f["all_min_mu"], f["floor"]) for f in folds}
    # sensitivity: R1 (rho = 1) at the R1 tau, and the best rho = 1 point on the coarse grid
    r1_pt = at(json.loads(R1.FIT_PATH.read_text())["TAU"], 1.0)
    out = {"experiment": "PTS rate challenger R2 (research)", **pf,
           "objective": "mean PTS negative log score under props_v0.pmf_matrix(mu_1B'(tau, rho), phi_fold, 120), pooled "
                        "PLAYED rows with y_PTS of F2023 + F2024 (true-OOS Phase 1B folds)",
           "search": {"coarse": "tau 2000^(i/40) i=0..40 x rho j/20 j=0..20 (861)",
                      "coarse_best": {k: cb[k] for k in ("i", "j", "tau", "rho", "log")},
                      "refine_rectangle": {"tau": [float(t_lo), float(t_hi)], "rho": [float(p_lo), float(p_hi)],
                                           "grid": "21 geometric tau x 21 linear rho (441)"},
                      "tie_rule": "lowest log; then larger tau; then smaller rho"},
           "TAU": tau, "RHO": rho, "EFFECTIVE_WEIGHT_ASYMPTOTE": rho, "BOUNDARY_SOLUTION": boundary,
           "fit_rows": n, "fit_checks": checks,
           "R0_on_fit_rows": {"mean_log_score": r0_log, "mean_crps": r0_crps},
           "R2_on_fit_rows": {"mean_log_score": best["log"], "mean_crps": best["crps"]},
           "R1_on_fit_rows_tau_74_23_rho_1": r1_pt,
           "secondary_crps_argmin_coarse": {k: min(coarse, key=lambda c: c["crps"])[k] for k in ("tau", "rho", "crps")},
           "numerics_fit_seasons": num, "coarse_surface": coarse, "refine_surface": refine,
           "seconds": round(time.time() - t0, 1), "api_calls": 0}
    FIT_PATH.write_text(json.dumps(out, indent=1))
    log(f"[fit] TAU {tau:.3f} RHO {rho:.4f} boundary {boundary}; log R0 {r0_log:.6f} R1 {r1_pt['log']:.6f} R2 {best['log']:.6f}"
        f"; {out['seconds']}s")
    return out


def frozen_params() -> tuple[float, float]:
    rec = json.loads(FIT_PATH.read_text())
    if rec["protocol_commit"] != PROTOCOL_COMMIT:
        raise R1.ChallengerViolation("fit record from another protocol")
    rel = str(FIT_PATH.relative_to(C.PROJECT_ROOT))
    R1._git("ls-files", "--error-unmatch", rel)
    if R1._git("status", "--porcelain", rel):
        raise R1.ChallengerViolation("the R2 fit record differs from its committed version")
    return float(rec["TAU"]), float(rec["RHO"])


# ============================================================================ strata
def masks(rows: pd.DataFrame) -> dict:
    """Protocol section 7 (= R1 summarize strata)."""
    mu = rows["min_mu"].to_numpy(float)
    cm = rows["cur_min"].fillna(0).to_numpy(float)
    t = pd.to_datetime(rows["prediction_cutoff_utc"], utc=True).dt.tz_convert("America/New_York")
    day = (t.dt.normalize() - t.dt.normalize().min()).dt.days.to_numpy()
    m = {b: (mu >= lo) & (mu < hi) for b, (lo, hi) in R1.BANDS.items()}
    m["EARLY_TEAM_GAMES_LE2"] = rows["team_games"].fillna(0).to_numpy(float) <= DA.EARLY_TEAM_GAMES
    m["EARLY_FIRST_30_DAYS"] = day < DA.EARLY_DAYS
    m.update({b: (cm > lo) & (cm <= hi) if np.isfinite(lo) else cm <= hi for b, (lo, hi) in R1.EVIDENCE.items()})
    return m


# ============================================================================ stage A screen
def screen(log=print) -> dict:
    if SCREEN_PATH.exists():
        raise R1.ChallengerViolation("the stage A screen runs exactly once")
    pf = protocol_frozen()
    tau, rho = frozen_params()
    t0 = time.time()
    rows = pd.read_parquet(R1_ROWS["2024-25"], columns=["game_id", "player_game_example_id", "R0|mean", "mu1B_R0", "cur_min",
                                                        "min_mu", "y_PTS", "team_games", "prediction_cutoff_utc"])
    st = R1._state(2025)
    h, cfg = R1.history(2025)
    rows = rows.merge(h.drop(columns=["cur_min"]), on="player_game_example_id", how="left", validate="one_to_one")
    parts = R1.rate_parts(rows, st, cfg)
    mm = rows["min_mu"].to_numpy(float)
    mu0 = np.maximum(mm * parts["r_R0"], cfg["mean_floor"])
    repro = float(np.max(np.abs(mu0 - rows["mu1B_R0"].to_numpy(float))))
    if repro > 1e-9:
        raise R1.ChallengerViolation(f"screen: Phase 1B R0 not reproduced ({repro})")
    mu2 = np.maximum(mm * r_r2(parts, tau, rho), cfg["mean_floor"])
    y = rows["y_PTS"].to_numpy(float)
    phi = float(st["dispersion"]["pts"]["phi"])
    l0, c0 = R1._nb_scores(mu0, phi, y)
    l2, c2 = R1._nb_scores(mu2, phi, y)
    m0 = rows["R0|mean"].to_numpy(float)
    m2 = m0 * mu2 / mu0
    e0, e2 = m0 - y, m2 - y
    w = eff_weight(parts, tau, rho)
    bt = DA.Boot(rows["game_id"].to_numpy())
    mk = masks(rows)
    strata = {}
    for k, m in mk.items():
        if not m.any():
            strata[k] = {"N": 0}
            continue
        strata[k] = {"N": int(m.sum()), "bias_R0": float(e0[m].mean()), "bias_R2": float(e2[m].mean()),
                     "abs_bias_change": float(abs(e2[m].mean()) - abs(e0[m].mean())),
                     "phase1b_log_R0": float(l0[m].mean()), "phase1b_dlog": bt.mean(l2 - l0, m),
                     "phase1b_crps_R0": float(c0[m].mean()), "phase1b_dcrps": bt.mean(c2 - c0, m),
                     "mean_eff_weight": float(w[m].mean())}
    b = lambda k, a: strata[k][f"bias_{a}"]
    C0, C2 = b("MIN_0_10", "R0") - b("MIN_30PLUS", "R0"), b("MIN_0_10", "R2") - b("MIN_30PLUS", "R2")
    sg = {"SA_high_exposure": {"R0": b("MIN_30PLUS", "R0"), "R2": b("MIN_30PLUS", "R2"),
                               "reduction_pct": 100 * (1 - abs(b("MIN_30PLUS", "R2")) / abs(b("MIN_30PLUS", "R0"))),
                               "PASS": abs(b("MIN_30PLUS", "R2")) <= R1.G_HIGH * abs(b("MIN_30PLUS", "R0"))},
          "SB_compression": {"R0": C0, "R2": C2, "reduction_pct": 100 * (1 - abs(C2) / abs(C0)),
                             "PASS": abs(C2) <= R1.G_COMP * abs(C0)},
          "SC_aggregate_log": {"dlog": strata["ALL"]["phase1b_dlog"]["value"],
                               "PASS": strata["ALL"]["phase1b_dlog"]["value"] <= G_SCREEN_LOG},
          "SD_low_exposure_bias": {"abs_bias_change": strata["LOW_LT20"]["abs_bias_change"],
                                   "PASS": strata["LOW_LT20"]["abs_bias_change"] <= R1.G_BIAS_WORSE},
          "SE_first_30_days_bias": {"abs_bias_change": strata["EARLY_FIRST_30_DAYS"]["abs_bias_change"],
                                    "PASS": strata["EARLY_FIRST_30_DAYS"]["abs_bias_change"] <= R1.G_BIAS_WORSE}}
    passed = all(v["PASS"] for v in sg.values())
    out = {"stage": "A (analytic screen, 2024-25 only)", **pf, "TAU": tau, "RHO": rho,
           "inputs": {"r1_rows": str(R1_ROWS["2024-25"].relative_to(C.PROJECT_ROOT)), "r1_rows_sha256": R1._sha(R1_ROWS["2024-25"]),
                      "phase1b_R0_reproduction_max_abs": repro, "phi_F2025": phi, "rows": int(len(rows))},
           "screen_mean": "R0 exact JM1 mean x mu_1B'_R2 / mu_1B_R0", "screen_scores": "Phase 1B NB (props_v0.pmf_matrix)",
           "strata": strata, "band_bias": {k: {"R0": b(k, "R0"), "R2": b(k, "R2")} for k in
                                           ("MIN_0_10", "MIN_10_20", "MIN_20_30", "MIN_30PLUS")},
           "screen_gates": sg, "STAGE_A_PASS": passed,
           "STAGE_A_DECISION": "PASS" if passed else "R2_EARLY_REJECT",
           "seconds": round(time.time() - t0, 1), "api_calls": 0}
    SCREEN_PATH.write_text(json.dumps(out, indent=1, default=float))
    log(f"[screen] {'PASS' if passed else 'R2_EARLY_REJECT'}: " +
        ", ".join(f"{k} {'ok' if v['PASS'] else 'FAIL'}" for k, v in sg.items()) + f"; {out['seconds']}s")
    return out


def _require_pass(path, key: str):
    if not path.exists() or not json.loads(path.read_text()).get(key):
        raise R1.ChallengerViolation(f"previous stage ({path.name}) did not pass; stop")


# ============================================================================ exact PTS
def _season_frames(season: str, tau: float, rho: float):
    e = R1.EVAL[season]
    F_ = e["F"]
    params, s4_struct = R1._params(e["params"])
    st = R1._state(F_)
    df, envp = P3.season_inputs(F_, params["struct"])
    h, cfg = R1.history(F_)
    df = df.merge(h, on="player_game_example_id", how="left", validate="many_to_one")
    if df["pos"].isna().any():
        raise R1.ChallengerViolation("history join incomplete")
    parts = R1.rate_parts(df, st, cfg)
    mu_r0 = np.maximum(df["min_mu"].to_numpy(float) * parts["r_R0"], cfg["mean_floor"])
    repro_1b = float(np.max(np.abs(mu_r0 - df["PTS_mu"].to_numpy(float))))
    if repro_1b > 1e-9:
        raise R1.ChallengerViolation(f"{season}: Phase 1B R0 PTS mean not reproduced ({repro_1b})")
    df1 = df.copy()
    df1["PTS_mu"] = np.maximum(df["min_mu"].to_numpy(float) * r_r2(parts, tau, rho), cfg["mean_floor"])
    num = numerics(parts, tau, rho, mu_r0, df["min_mu"].to_numpy(float), cfg["mean_floor"])
    num["non_pts_phase1b_means_bit_identical"] = bool(all(np.array_equal(df[f"{s}_mu"].to_numpy(), df1[f"{s}_mu"].to_numpy())
                                                          for s in L.STATS[1:]))
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    return df, df1, parts, envd, params, s4_struct, num, repro_1b


def evaluate(season: str, log=print, limit_games: int | None = None, write: bool = True) -> dict:
    pf = protocol_frozen()
    tau, rho = frozen_params()
    if write:
        if EVAL_PATH[season].exists():
            raise R1.ChallengerViolation(f"{season} is evaluated exactly once")
        _require_pass(SCREEN_PATH, "STAGE_A_PASS")
        if season == "2025-26":
            _require_pass(EVAL_PATH["2024-25"], "PRIMARY_GATES_PASS")
    df, df1, parts, envd, params, s4_struct, num, repro_1b = _season_frames(season, tau, rho)
    g0, g1 = dict(list(df.groupby("game_id", sort=True))), dict(list(df1.groupby("game_id", sort=True)))
    gids = sorted(g0)[:limit_games] if limit_games else sorted(g0)
    keys = ("log", "crps", "mean", "var", "pit_lo", "pit_hi", "pover0", "pover1", "pover2")
    blocks = []
    t0 = time.time()
    for gi_, gid in enumerate(gids):
        r = R1.score_game(gid, g0[gid], g1[gid], envd, params, s4_struct, combos=False)
        blk = r["rows"][["game_id", "player_game_example_id"]].copy()
        for arm in ("R0", "R1"):
            for k in keys:
                blk[f"{arm}|{k}"] = r["single"][arm][k]
        blocks.append(blk)
        if (gi_ + 1) % 1000 == 0 or gi_ + 1 == len(gids):
            el = time.time() - t0
            log(f"[{season}] {gi_ + 1}/{len(gids)} games {el / 60:.1f} min")
    rows = pd.concat(blocks, ignore_index=True)
    pk = df[["player_game_example_id", "cur_min", "PTS_mu"]].rename(columns={"PTS_mu": "mu1B_R0"})
    pk["mu1B_R1"] = df1["PTS_mu"].to_numpy()
    pk["w_current"] = eff_weight(parts, tau, rho)
    pk["r_prior"], pk["r_R0"], pk["r_R1"] = parts["r_prior"], parts["r_R0"], r_r2(parts, tau, rho)
    rows = rows.merge(pk, on="player_game_example_id", how="left", validate="one_to_one")
    a = DA.SOURCES[season]["arm"]
    sv = pd.read_parquet(C.PROJECT_ROOT / DA.SOURCES[season]["path"],
                         columns=["player_game_example_id", "prediction_cutoff_utc", "team_games", "cold_start_class",
                                  "min_mu", "y_min", "y_PTS", "y_REB", "y_AST"] + [f"{a}|{s}|{k}" for s in ("PTS", "REB", "AST")
                                                                                  for k in ("log", "crps", "mean")])
    rows = rows.merge(sv, on="player_game_example_id", how="left", validate="one_to_one")
    repro = {k: float(np.max(np.abs(rows[f"R0|{k}"] - rows[f"{a}|PTS|{k}"]) / np.maximum(np.abs(rows[f"{a}|PTS|{k}"]), 1.0)))
             for k in ("log", "crps", "mean")}
    ok = bool(max(repro.values()) <= R1.REPRO_RTOL and rows[f"{a}|PTS|log"].notna().all())
    if not limit_games:
        ok = ok and len(rows) == len(sv)
    summ = R1.summarize(rows, pd.DataFrame(), season)
    summ.pop("combos")                                       # combos only in the combo stage (protocol section 8)
    rec = {"season": season, **pf, "TAU": tau, "RHO": rho, "rows": int(len(rows)), "games": len(gids),
           "phase1b_R0_reproduction_max_abs": repro_1b, "R0_reproduction_max_relative": repro, "R0_REPRODUCED": ok,
           "numerics": num, "seconds": round(time.time() - t0, 1), "draws_per_game": F.SIMS, "api_calls": 0,
           "structure_T": params["T"], "summary": summ}
    rec = _relabel(rec)
    rec["gates"] = pts_gates(rec)
    rec["PRIMARY_GATES_PASS"] = bool(rec["R0_REPRODUCED"] and all(v["PASS"] for k, v in rec["gates"].items()))
    if write:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        rows.rename(columns=lambda c: c.replace("R1", "R2")).to_parquet(SCRATCH / f"{season}_pts_rows.parquet", index=False)
        EVAL_PATH[season].write_text(json.dumps(rec, indent=1, default=float))
    log(f"[{season}] repro {rec['R0_REPRODUCED']}; gates " +
        ", ".join(f"{k} {'ok' if v['PASS'] else 'FAIL'}" for k, v in rec["gates"].items()))
    return rec


# ============================================================================ gates (protocol section 9)
def pts_gates(rec: dict) -> dict:
    s, cp, n = rec["summary"]["strata"], rec["summary"]["compression"], rec["numerics"]
    hi, G, BW, CAL = R1._hi, R1.G_GROUP, R1.G_BIAS_WORSE, R1.G_CAL
    gg = {}
    b0, b2 = s["MIN_30PLUS"]["R0"]["bias"]["value"], s["MIN_30PLUS"]["R2"]["bias"]["value"]
    gg["A_high_exposure_bias"] = {"R0": b0, "R2": b2, "reduction_pct": 100 * (1 - abs(b2) / abs(b0)),
                                  "PASS": abs(b2) <= R1.G_HIGH * abs(b0) and b2 <= G_OVERSHOOT}
    c0, c2 = cp["R0"]["C_bias_0_10_minus_30plus"], cp["R2"]["C_bias_0_10_minus_30plus"]
    gg["B_compression"] = {"R0": c0, "R2": c2, "reduction_pct": 100 * (1 - abs(c2) / abs(c0)),
                           "band_bias": {"R0": cp["R0"]["band_bias"], "R2": cp["R2"]["band_bias"]},
                           "PASS": abs(c2) <= R1.G_COMP * abs(c0)}
    d = s["ALL"]["delta"]
    gg["C_aggregate_log"] = {"delta": d["log"], "PASS": hi(d["log"]) <= R1.G_AGG_LOG}
    gg["D_aggregate_crps"] = {"delta_scaled": d["crps_scaled"], "delta": d["crps"], "PASS": hi(d["crps_scaled"]) <= R1.G_AGG_CRPS}
    dh = s["MIN_30PLUS"]["delta"]
    gg["E_high_exposure_scores"] = {"log": dh["log"], "crps": dh["crps"], "crps_scaled": dh["crps_scaled"],
                                    "PASS": hi(dh["log"]) <= G and hi(dh["crps_scaled"]) <= G}
    lo = s["LOW_LT20"]
    lw = abs(lo["R2"]["bias"]["value"]) - abs(lo["R0"]["bias"]["value"])
    gg["F_low_exposure"] = {"abs_bias_change": lw, "log": lo["delta"]["log"], "crps_scaled": lo["delta"]["crps_scaled"],
                            "PASS": lw <= BW and hi(lo["delta"]["log"]) <= G and hi(lo["delta"]["crps_scaled"]) <= G}
    early = {}
    for k in ("EARLY_TEAM_GAMES_LE2", "EARLY_FIRST_30_DAYS"):
        e = s[k]
        bw = abs(e["R2"]["bias"]["value"]) - abs(e["R0"]["bias"]["value"])
        pw = abs(e["R2"]["mid_pit"] - 0.5) - abs(e["R0"]["mid_pit"] - 0.5)
        cw = abs(e["R2"]["cov80"] - 0.8) - abs(e["R0"]["cov80"] - 0.8)
        early[k] = {"R0_bias": e["R0"]["bias"]["value"], "R2_bias": e["R2"]["bias"]["value"], "abs_bias_change": bw,
                    "mid_pit_worsening": pw, "cov80_worsening": cw, "log": e["delta"]["log"], "crps_scaled": e["delta"]["crps_scaled"],
                    "PASS": hi(e["delta"]["log"]) <= G and hi(e["delta"]["crps_scaled"]) <= G and bw <= BW and pw <= CAL and cw <= CAL}
    gg["G_early_season"] = {**early, "PASS": all(v["PASS"] for v in early.values())}
    tails = {k: {d_: abs(s[k]["R2"][d_] - t) - abs(s[k]["R0"][d_] - t) for d_, t in R1.TAILS.items()} for k in ("ALL", "MIN_30PLUS")}
    gg["H_tails"] = {"worsening": tails, "PASS": all(v <= CAL for t in tails.values() for v in t.values())}
    cs = {c: hi(s[f"STATE_{c}"]["delta"]["log"]) for c in ("COLD_TRANSFER_IN", "COLD_NO_HISTORY") if s[f"STATE_{c}"]["N"]}
    gg["J_numerics_cold_start"] = {"cold_log_hi": cs,
                                   "PASS": n["nonfinite"] == 0 and n["nonpositive"] == 0 and n["outside_convex_hull"] == 0
                                   and n["zero_evidence_mu_not_bit_identical"] == 0 and n["cur_min_zero_rows_not_r_prior"] == 0
                                   and n["non_pts_phase1b_means_bit_identical"] and n["eff_weight_bucket_monotone"]
                                   and n["eff_weight_in_0_rho"] and all(v <= G for v in cs.values())}
    a0, a2 = s["ALL"]["R0"]["bias"]["value"], s["ALL"]["R2"]["bias"]["value"]
    gg["K_aggregate_bias"] = {"R0": a0, "R2": a2, "PASS": abs(a2) <= abs(a0) + G_AGG_BIAS}
    return gg


# ============================================================================ combos (step 3)
def combos(log=print) -> dict:
    pf = protocol_frozen()
    tau, rho = frozen_params()
    if COMBO_PATH.exists():
        raise R1.ChallengerViolation("combos are evaluated exactly once")
    for s in EVAL_PATH:
        _require_pass(EVAL_PATH[s], "PRIMARY_GATES_PASS")
    out = {**pf, "TAU": tau, "RHO": rho, "subsample": "int(sha256(game_id)[:8], 16) % 5 == 0 (R1 fixed 20%)", "seasons": {}}
    t0 = time.time()
    for season in EVAL_PATH:
        df, df1, parts, envd, params, s4_struct, _, _ = _season_frames(season, tau, rho)
        g0, g1 = dict(list(df.groupby("game_id", sort=True))), dict(list(df1.groupby("game_id", sort=True)))
        recs = []
        for gid in (g for g in sorted(g0) if R1._subsample(g)):
            r = R1.score_game(gid, g0[gid], g1[gid], envd, params, s4_struct, combos=True)
            for p, pid in enumerate(r["rows"]["player_game_example_id"]):
                rc = {"game_id": gid, "player_game_example_id": pid}
                for arm in ("R0", "R1"):
                    for cb in R1.COMBOS:
                        c = r["combo"][arm][cb]
                        rc[f"{arm}|{cb}|log"], rc[f"{arm}|{cb}|crps"] = float(c["log"][p]), float(c["crps"][p])
                        for li, q in enumerate((25, 50, 75)):
                            rc[f"{arm}|{cb}|brier_q{q}"] = float(c["brier"][p, li])
                recs.append(rc)
        combo = pd.DataFrame(recs)
        rows = pd.read_parquet(SCRATCH / f"{season}_pts_rows.parquet").rename(columns=lambda c: c.replace("R2", "R1"))
        summ = _relabel(R1.summarize(rows, combo, season)["combos"])
        cg = {}
        for cb, v in summ.items():
            sc = v["subsample_scores_R2_minus_R0"]
            bw = abs(v["bias_all_rows"]["R2"]["value"]) - abs(v["bias_all_rows"]["R0"]["value"])
            cg[cb] = {"log_hi": R1._hi(sc["log"]), "crps_hi_rel": R1._hi(sc["crps"]) / sc["R0_mean_crps"],
                      "brier_hi": R1._hi(sc["brier_mean_q25_q50_q75"]), "abs_bias_change_all_rows": bw,
                      "bias_all_rows": {"R0": v["bias_all_rows"]["R0"]["value"], "R2": v["bias_all_rows"]["R2"]["value"]}}
            cg[cb]["PASS"] = (cg[cb]["log_hi"] <= R1.G_GROUP and cg[cb]["crps_hi_rel"] <= R1.G_COMBO_CRPS_REL
                              and cg[cb]["brier_hi"] <= R1.G_COMBO_BRIER and bw <= R1.G_BIAS_WORSE)
        combo.rename(columns=lambda c: c.replace("R1", "R2")).to_parquet(SCRATCH / f"{season}_combo_rows.parquet", index=False)
        out["seasons"][season] = {"games": int(combo["game_id"].nunique()), "rows": int(len(combo)), "summary": summ,
                                  "I_combos": {**cg, "PASS": all(v["PASS"] for v in cg.values())}}
        log(f"[combos {season}] " + ", ".join(f"{k} {'ok' if v['PASS'] else 'FAIL'}" for k, v in cg.items()))
    out["seconds"] = round(time.time() - t0, 1)
    COMBO_PATH.write_text(json.dumps(out, indent=1, default=float))
    return out


# ============================================================================ decision
def decide(log=print) -> dict:
    pf = protocol_frozen()
    fr = json.loads(FIT_PATH.read_text())
    sc = json.loads(SCREEN_PATH.read_text()) if SCREEN_PATH.exists() else None
    ev = {s: json.loads(p.read_text()) for s, p in EVAL_PATH.items() if p.exists()}
    cb = json.loads(COMBO_PATH.read_text()) if COMBO_PATH.exists() else None
    fit_num_ok = all(v["nonfinite"] == 0 and v["nonpositive"] == 0 and v["outside_convex_hull"] == 0
                     and v["zero_evidence_mu_not_bit_identical"] == 0 and v["cur_min_zero_rows_not_r_prior"] == 0
                     for v in fr["numerics_fit_seasons"].values())
    fails = [] if fit_num_ok else ["fit_seasons:J_numerics"]
    if sc is None:
        raise R1.ChallengerViolation("no screen record")
    if not sc["STAGE_A_PASS"]:
        decision = "R2_EARLY_REJECT"
        fails += [f"screen:{k}" for k, v in sc["screen_gates"].items() if not v["PASS"]]
    elif any(not e["R0_REPRODUCED"] for e in ev.values()):
        decision = "R2_INCONCLUSIVE"
    else:
        fails += [f"{s}:{k}" for s, e in ev.items() for k, v in e["gates"].items() if not v["PASS"]]
        if cb:
            fails += [f"{s}:I_combos" for s, v in cb["seasons"].items() if not v["I_combos"]["PASS"]]
        complete = len(ev) == 2 and cb is not None
        decision = "R2_REJECT" if fails else ("R2_ACCEPT_PENDING_ARCHITECT_REVIEW" if complete else "R2_INCONCLUSIVE")
    out = {**pf, "TAU": fr["TAU"], "RHO": fr["RHO"], "BOUNDARY_SOLUTION": fr["BOUNDARY_SOLUTION"],
           "STAGE_A_PASS": sc["STAGE_A_PASS"], "exact_seasons_run": list(ev), "combos_run": cb is not None,
           "failed_gates": fails, "R2_DECISION": decision, "PRODUCTION_MODEL_CHANGED": False, "PUBLIC_LAUNCH_STATUS": "HOLD"}
    RESULT_PATH.write_text(json.dumps(out, indent=1, default=float))
    log(f"DECISION {decision}; failed {fails}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fit":
        fit()
    elif cmd == "screen":
        screen()
    elif cmd == "evaluate":
        evaluate(sys.argv[2])
    elif cmd == "combos":
        combos()
    elif cmd == "decide":
        decide()
    else:
        raise SystemExit(__doc__)
