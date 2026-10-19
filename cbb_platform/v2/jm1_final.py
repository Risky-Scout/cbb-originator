"""CBB V2 JM1_FINAL (experiment cbb-v2-jm1-final): random player-minutes exposure + conditional dispersion refit +
residual J2. The diagnostic JM1 arm of the JointMinutesV2 development run, frozen as-is and confirmed once.

This is NOT a joint team-minutes model. No team-minutes budget, no reservoir, no reconciliation, no forced teammate
conservation, no opportunity redistribution. Per draw, each scored player's minutes are ONE independent draw from his
Phase 1B conditional-minutes marginal (scaled by the shared game duration), and that draw feeds all seven stats.

Stages (each refuses to run out of order):

    .venv/bin/python -m cbb_platform.v2.jm1_final protocol   write the frozen protocol + development evidence (commit both)
    .venv/bin/python -m cbb_platform.v2.jm1_final confirm    procedure through 2024-25, evaluate 2025-26 ONCE vs S0

The JointMinutesV2 module (cbb_platform/v2/joint_minutes_v2.py) and the Phase 3 module are used read-only as a library;
their frozen records are never rewritten. The final through-2025-26 refit is NOT a stage of this module. No API calls.
"""
from __future__ import annotations

import gc
import hashlib
import json
import sys
import time

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P
from cbb_platform.v2 import dependence as D, joint as J, joint_minutes_v2 as JM, latent as L, marginal as M
from cbb_platform.v2 import phase2 as P2, phase3 as P3, provenance as PV, structural as S

VERSION = "cbb-v2-jm1-final-1.0.0"
EXPERIMENT = "cbb-v2-jm1-final"
OUT = M.OUT_DIR
PROTOCOL_PATH = OUT / "jm1_final_protocol.json"
DEV_EVIDENCE_PATH = OUT / "jm1_final_development_evidence.json"
CONFIRM_PATH = OUT / "jm1_final_confirmation.json"
CONFIRM_PARAMS_PATH = OUT / "jm1_final_confirmation_parameters.json"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_jm1"
CODE = [C.PROJECT_ROOT / "cbb_platform" / "v2" / f for f in
        ("jm1_final.py", "joint_minutes_v2.py", "structural.py", "phase3.py", "dependence.py", "joint.py")]
JM2_FROZEN = {"joint_minutes_v2_protocol.json": "80d9b002a0ba3c309580b61fc6a79ad4ab73b6474225007be6efb02ab36a47d0",
              "joint_minutes_v2_dev_parameters.json": "008b906fb634ac757f7bb92222120e991727bc1159a098db4f7046d0eb6741cd"}

CAND = "JM1_FINAL"
ARMS = ("S0", CAND)
CONFIRM = {"T": 2025, "eval": 2026, "resid_seasons": (2024, 2025)}   # = the frozen Phase 3 confirmation chronology
DEV = dict(JM.DEV)                                                   # {T 2024, eval 2025, resid (2024,)}: reproduction only
MAX_SEASON = 2026                                                    # 2025-26: last season with outcomes; never 2026-27
SIMS = JM.SIMS
TIERS = JM.TIERS
POSITIONS_GATED = ("G", "F", "C")                                    # U = unclassified position: reported, not gated
EARLY_TEAM_GAMES = JM.EARLY_TEAM_GAMES
REPRO_GAMES = 12
REPRO_RTOL = 2e-6
# confirmation gates (strict: a gate passes only if its 95% interval bound clears the margin; inconclusive = fail)
G_AGG_SUPERIORITY = 0.0          # aggregate MARG_LOG and MARG_CRPS_SCALED upper bound < 0
G_STAT_LOG = 0.002               # each stat MARG_LOG upper bound <= +0.002
G_STAT_CRPS = 0.002              # each stat MARG_CRPS_SCALED upper bound <= +0.002
G_STAT_GROUP_LOG = 0.005         # each stat x tier and stat x position MARG_LOG upper bound <= +0.005
G_MIN_CRPS_ALL = JM.G_MIN_CRPS_ALL
G_MIN_CRPS_TIER = JM.G_MIN_CRPS_TIER
G_ES = JM.G_ES
G_VS = JM.G_VS


class JM1Violation(RuntimeError):
    pass


_sha = JM._sha
_rel, _dump, _jd = P2._rel, P2._dump, P2._json_default


def check_seasons(seasons) -> list[int]:
    s = sorted({int(x) for x in seasons})
    if s and s[-1] > MAX_SEASON:
        raise JM1Violation(f"JM1_FINAL never reads 2026-27 (asked {s})")
    return s


def guards() -> dict:
    out = JM.guards()                       # Phase 1B / J2 / OOS stores / Phase 3 records / user QA file
    for name, sha in JM2_FROZEN.items():
        out[f"{name}_sha256"] = _sha(OUT / name)
        out[f"{name}_UNCHANGED"] = out[f"{name}_sha256"] == sha
    if not all(v for k, v in out.items() if k.endswith("UNCHANGED")):
        raise JM1Violation(f"a protected file changed: {out}")
    return out


def code_state() -> dict:
    return {_rel(p): _sha(p) for p in CODE}


# ============================================================================ the frozen JM1 mechanism
def fit_procedure(T: int, resid_seasons, log=print) -> dict:
    """Exactly the JM1 development procedure: structure (environment + GLM with JM_TERMS) through T, true-OOS structural
    residual basis of resid_seasons (season F with parameters through F-1), conditional dispersion (method of moments)
    and residual J2 (MID) on that basis; E[D] from the fitted overtime law."""
    check_seasons([T, *resid_seasons])
    struct, s4, resid, _ = P3.build_procedure(T, resid_seasons, log, terms=JM.JM_TERMS)
    if max(resid["forecast_season"]) > T or struct["T"] != T:
        raise JM1Violation("a season after T entered the procedure")
    return {"T": T, "struct": struct, "s4": s4, "resid_rows": int(len(resid)),
            "resid_seasons": sorted(int(x) for x in resid["forecast_season"].unique()),
            "e_dur": JM.expected_duration(struct["env"]["overtime"]["p"])}


def simulate_exposure(gi: S.GameInput, env: dict, e_dur: float, sims: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(dur, x, M): the shared game duration and environment shocks (structural.simulate_state streams, seed base
    20271), and per scored player ONE minutes draw clip(min_mu + min_sigma eps, 0, 60) x D / E[D] with eps the
    'minutes' stream drawn for every candidate of the game (bit-identical to the JM1 development arm)."""
    base = S.simulate_state(S.GameInput(gi.game_id, gi.team, gi.scored, gi.p_play, gi.min_mu, gi.min_sigma,
                                        np.ones((len(gi.team), len(S.FAM))), gi.env), env, sims, minutes=False,
                            seed_base=JM.SEED_BASE)
    eps = np.random.default_rng(JM.stream_seed(gi.game_id, "minutes")).standard_normal((sims, len(gi.team)))
    raw = JM.raw_minutes(gi.min_mu, gi.min_sigma, eps, base.dur, e_dur)
    return base.dur, base.x, raw[:, np.flatnonzero(gi.scored)]


def eval_game(gid, gp, envd, params, s0_struct, s4_struct, scale, sims=SIMS) -> dict:
    """S0 and JM1_FINAL for one game from common random numbers (Phase 2 / Phase 3 evaluation conventions)."""
    gi = P3._game_input(gid, gp, envd)
    rows = gp[gp["_scored"]].reset_index(drop=True)
    n = len(rows)
    y = rows[[f"y_{s}" for s in L.STATS]].to_numpy(dtype=float)
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    groups = list(D.group_of(rows))
    marg0 = J.game_marginals(rows)
    E = np.random.default_rng(D.game_seed(J.SIM_SEED, gid)).standard_normal((sims, n * J.NS))
    gc0 = D.game_correlation(s0_struct, groups, team)
    L0, jit0 = D.cholesky_unit(gc0["corr"])
    gc4 = D.game_correlation(s4_struct, groups, team)
    L4, jit4 = D.cholesky_unit(gc4["corr"])
    z0, z4 = E @ L0.T, E @ L4.T
    dur, x, Mx = simulate_exposure(gi, params["struct"]["env"], params["e_dur"], sims)
    base_lm = np.log(rows[[f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float))
    min_mu = rows["min_mu"].to_numpy(dtype=float)
    phic = np.array([params["s4"]["dispersion"][s]["phi"] for s in L.STATS])
    stat_idx = np.tile(np.arange(J.NS), n)
    yv = y.ravel()
    ymin = rows["y_min"].to_numpy(dtype=float)
    draws, single, mins, info = {}, {}, {}, {}
    draws["S0"] = J.counts_from_latent(z0, marg0["thr"]).reshape(sims, n, J.NS)
    single["S0"] = P3.single_metrics(marg0["pmf"].reshape(n * J.NS, -1), yv, stat_idx)
    mins["S0"] = P3.minutes_metrics(P.minutes_pmf(min_mu, rows["min_sigma"].to_numpy(dtype=float), JM.KMAX), ymin)
    info["S0"] = {"min_eig": gc0["min_eig"], "invalid": gc0["invalid"], "jitter": jit0}
    sv = S.State(dur, x, Mx, np.zeros((sims, n, len(S.FAM))), np.zeros((sims, 0)), {}, np.zeros((n, len(S.FAM))), 0.0)
    lm_s = S.log_mu_terms(base_lm, min_mu, team, sv, params["struct"]["glm"], JM.JM_TERMS)
    lm, _ = S.anchor(lm_s, base_lm, JM.JM1_ALPHA)
    tab = S.mixture_tables(lm.reshape(sims, n * J.NS), phic)
    draws[CAND] = S.counts(tab, z4).reshape(sims, n, J.NS)
    single[CAND] = P3.single_metrics(S.mixture_pmf(tab, sims), yv, stat_idx)
    info[CAND] = {"min_eig": gc4["min_eig"], "invalid": gc4["invalid"], "jitter": jit4}
    mr = np.clip(np.round(Mx), 0, JM.KMAX).astype(np.int64)
    mins[CAND] = P3.minutes_metrics(np.stack([np.bincount(mr[:, i], minlength=JM.KMAX + 1) for i in range(n)]) / sims, ymin)
    del tab
    joint = JM.joint_block(gid, n, team, rows, y, draws, single, info, marg0, sims, scale)
    return {"joint": joint["joint"], "single": single, "mins": mins, "events": joint["events"],
            "moments": joint["moments"], "rows": rows, "M": Mx}


# ============================================================================ evaluation
def team_games_frame(F: int) -> pd.DataFrame:
    """Pregame team games so far (max pp_team_games of feat.player_features), the EARLY_SEASON stratum feature."""
    check_seasons([F])
    con = duckdb.connect(str(PV.LB_FEATURES), read_only=True, config={"threads": C.DUCKDB_THREADS})
    df = con.execute("SELECT canonical_game_id AS game_id, canonical_team_id AS team_id, "
                     "coalesce(max(pp_team_games), 0)::DOUBLE AS team_games FROM feat.player_features "
                     f"WHERE season = {int(F)} GROUP BY 1, 2").df()
    con.close()
    return df


def evaluate(F: int, params: dict, s0_struct, scale, tag: str, log=print, sims: int = SIMS,
             limit_games: int | None = None, write: bool = True) -> dict:
    check_seasons([F])
    SCRATCH.mkdir(parents=True, exist_ok=True)
    df, envp = P3.season_inputs(F, params["struct"])
    pit = S.pit_audit(envp)
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    s4_struct = D.structure("J2", params["s4"]["residual_dependence"])
    games = list(df.groupby("game_id", sort=True))
    if limit_games:
        games = games[:limit_games]
    keys = ("log", "crps", "mean", "var", "pover0", "pover1", "pover2", "pit_lo", "pit_hi", "zc", "vz")
    mkeys = ("min_log", "min_crps", "min_mean", "min_sd", "min_median", "min_pit_lo", "min_pit_hi",
             "min_q05", "min_q10", "min_q25", "min_q75", "min_q90", "min_q95")
    cell_parts = {a: {k: [] for k in keys} for a in ARMS}
    min_parts = {a: {k: [] for k in mkeys} for a in ARMS}
    row_parts, joint_rows, ev_rows = [], [], []
    obs_m = {a: [] for a in ARMS}
    sim_m = {a: np.zeros((3, J.NS, J.NS)) for a in ARMS}
    pairs_n = []
    t0 = time.time()
    for gi_, (gid, gp) in enumerate(games):
        r = eval_game(gid, gp, envd, params, s0_struct, s4_struct, scale, sims)
        rows = r["rows"]
        n = len(rows)
        row_parts.append(rows[["forecast_season", "game_id", "team_id", "player_id", "player_game_example_id",
                               "role_diagnostic", "position_group", "cold_start_class", "p_play", "min_mu", "min_sigma",
                               "y_min", "prediction_cutoff_utc"] + [f"y_{s}" for s in L.STATS]].copy())
        for a in ARMS:
            for k in keys:
                cell_parts[a][k].append(r["single"][a][k].astype(np.float32))
            for k in mkeys:
                min_parts[a][k].append(r["mins"][a][k].astype(np.float32))
            mo = r["moments"][a]
            obs_m[a].append(np.stack([mo["obs"]["within_player"], mo["obs"]["same_team"], mo["obs"]["cross_team"]]))
            sim_m[a] += np.stack([mo["sim"]["within_player"], mo["sim"]["same_team"], mo["sim"]["cross_team"]])
        team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
        nt = [(team == t).sum() for t in (0, 1)]
        pairs_n.append([n, nt[0] * (nt[0] - 1) + nt[1] * (nt[1] - 1), 2 * nt[0] * nt[1]])
        joint_rows.append(r["joint"])
        for e in r["events"]:
            ev_rows.append({"game_id": gid, **e})
        if (gi_ + 1) % 100 == 0 or gi_ + 1 == len(games):
            el = time.time() - t0
            log(f"[{tag}] {gi_ + 1}/{len(games)} games  {el / 60:.1f} min  eta {(len(games) - gi_ - 1) * el / (gi_ + 1) / 60:.1f} min")
        del r
        if (gi_ + 1) % 500 == 0:
            gc.collect()
    meta = pd.concat(row_parts, ignore_index=True)
    cols = {}
    for a in ARMS:
        for k in keys:
            v = np.concatenate(cell_parts[a][k]).reshape(len(meta), J.NS)
            for j, s in enumerate(L.STATS):
                cols[f"{a}|{s}|{k}"] = v[:, j]
        for k in mkeys:
            cols[f"{a}|{k}"] = np.concatenate(min_parts[a][k])
    wide = pd.concat([meta, pd.DataFrame(cols)], axis=1)
    wide = wide.merge(team_games_frame(F), on=["game_id", "team_id"], how="left")
    gt, ev = pd.DataFrame(joint_rows), pd.DataFrame(ev_rows)
    if write:
        wide.to_parquet(SCRATCH / f"{tag}_rows.parquet", index=False)
        gt.to_parquet(SCRATCH / f"{tag}_games.parquet", index=False)
        ev.to_parquet(SCRATCH / f"{tag}_events.parquet", index=False)
    acc = {"COMBO_IDENTITY_VIOLATIONS": int(sum(gt[f"{a}|COMBO_IDENTITY_VIOLATIONS"].sum() for a in ARMS)),
           "INVALID_CORRELATION_MATRICES": {a: int(gt[f"{a}|INVALID_MATRIX"].sum()) for a in ARMS}}
    return {"wide": wide, "gt": gt, "ev": ev, "om": {a: np.stack(obs_m[a]) for a in ARMS}, "sim_m": sim_m,
            "pairs_n": np.asarray(pairs_n, dtype=float), "df": df, "accounting": acc, "pit_audit_team_features": pit,
            "draws_per_game": sims}


def strata_of(wide: pd.DataFrame) -> dict:
    role = wide["role_diagnostic"].to_numpy()
    pos = wide["position_group"].to_numpy()
    cold = wide["cold_start_class"].fillna("UNKNOWN").to_numpy()
    month = pd.to_datetime(wide["prediction_cutoff_utc"], utc=True).dt.tz_convert("America/New_York").dt.month.to_numpy()
    s = {"ALL": None, **{f"ROLE:{r}": role == r for r in JM.ROLES4},
         **{f"POS:{p}": pos == p for p in ("G", "F", "C", "U")},
         **{f"COLD:{c}": cold == c for c in ("WARM", "COLD_EARLY_SEASON", "COLD_TRANSFER_IN", "COLD_NO_HISTORY")},
         "COLD_START": np.isin(cold, ("COLD_NO_HISTORY", "COLD_EARLY_SEASON")), "TRANSFER": cold == "COLD_TRANSFER_IN",
         "RETURNER": cold == "WARM", "ROTATION_OR_BENCH": np.isin(role, ("ROTATION_6_8", "BENCH_9PLUS")),
         "EARLY_SEASON": wide["team_games"].fillna(0).to_numpy(dtype=float) <= EARLY_TEAM_GAMES, "MONTH:NOV": month == 11}
    return {k: v for k, v in s.items() if v is None or v.any()}


def summarize(ev_out: dict) -> dict:
    """The JointMinutesV2 summary (levels / deltas / joint / calibration / events), arms S0 and JM1_FINAL."""
    wide, gt, ev, df = ev_out["wide"], ev_out["gt"], ev_out["ev"], ev_out["df"]
    sims, scale = ev_out["draws_per_game"], ev_out["scale"]
    G = len(gt)
    gidx = pd.Index(gt["game_id"]).get_indexer(wide["game_id"])
    W = P3._boot_weights(G)
    sc = dict(zip(L.STATS, np.asarray(scale, dtype=float).tolist()))
    strata = strata_of(wide)
    cells = {st: {a: P3.cell_arrays(wide, a, gidx, G, sc, m) for a in ARMS} for st, m in strata.items()}
    mins = {st: {a: P3.minutes_arrays(wide, a, gidx, G, m) for a in ARMS} for st, m in strata.items()}
    jo = {a: P3.joint_arrays(gt, a) for a in ARMS}
    pair = f"{CAND}-S0"
    levels, min_levels, deltas, min_deltas = {}, {}, {pair: {}}, {pair: {}}
    for st in cells:
        levels[st] = {a: {k: P3._ratio_ci(*v, W) for k, v in cells[st][a].items()} for a in ARMS}
        min_levels[st] = {a: {k: P3._ratio_ci(*v, W) for k, v in mins[st][a].items()} for a in ARMS}
        for a in ARMS:
            min_levels[st][a]["RMSE"] = float(np.sqrt(min_levels[st][a]["MIN_SE_MEAN"]["value"]))
        deltas[pair][st] = {k: P3._ratio_ci(cells[st][CAND][k][0] - cells[st]["S0"][k][0], cells[st][CAND][k][1], W)
                            for k in cells[st][CAND]}
        min_deltas[pair][st] = {k: P3._ratio_ci(mins[st][CAND][k][0] - mins[st]["S0"][k][0], mins[st][CAND][k][1], W)
                                for k in mins[st][CAND]}
    jl = {a: {k: P3._ratio_ci(*v, W) for k, v in jo[a].items()} for a in ARMS}
    jd = {pair: {k: P3._ratio_ci(jo[CAND][k][0] - jo["S0"][k][0], jo[CAND][k][1], W) for k in jo[CAND]}}
    mc = {}
    for m_, ka, kb in (("ES", "ES_rep_a", "ES_rep_b"), ("VS_BAL", "VS_rep_a", "VS_rep_b")):
        dg = (gt[f"{CAND}|{ka}"] - gt[f"S0|{ka}"]) - (gt[f"{CAND}|{kb}"] - gt[f"S0|{kb}"])
        mc[f"{pair}|{m_}"] = float(dg.std(ddof=1) / np.sqrt(G) / 2)
    lam = {st: {a: P3.latent_scale(wide, a, m) for a in ARMS} for st, m in strata.items()
           if st.startswith("ROLE:") or st in ("ALL", "EARLY_SEASON")}
    return {"games": G, "player_games": int(len(wide)), "levels": levels, "deltas": deltas,
            "minutes_levels": min_levels, "minutes_deltas": min_deltas, "joint_levels": jl, "joint_deltas": jd,
            "mc_se": mc, "latent_scale_lam": lam,
            "pit_calibration": {a: {s: P3.pit_calibration(wide, a, s) for s in L.STATS} for a in ARMS},
            "minutes_calibration": {a: P3.minutes_calibration(wide, a) for a in ARMS},
            "minutes_calibration_strata": {st: {a: P3.minutes_calibration(wide[m] if m is not None else wide, a)
                                                for a in ARMS} for st, m in strata.items()
                                           if st.startswith(("ROLE:", "COLD:")) or st in ("ALL", "EARLY_SEASON")},
            "stat_bias_pred_minus_obs": {st: {a: {s: float((wide.loc[m if m is not None else slice(None), f"{a}|{s}|mean"]
                                                           - wide.loc[m if m is not None else slice(None), f"y_{s}"]).mean())
                                                  for s in L.STATS} for a in ARMS}
                                         for st, m in strata.items() if st == "ALL" or st.startswith(("ROLE:", "POS:"))},
            "events_by_family": P3.event_families(ev, ARMS, W, gt, sims),
            "correlation_calibration": P3.correlation_calibration(ev_out["om"], ev_out["sim_m"], ev_out["pairs_n"], ARMS),
            "strata_sizes": {st: int(len(wide) if m is None else m.sum()) for st, m in strata.items()},
            "descriptive_scale": sc, "participation": P3.participation_metrics(df)}


# ============================================================================ frozen confirmation gates
def _hi(d) -> float:
    return d["hi"]


def gates(summ: dict, cand: str, acc: dict, extra: dict) -> dict:
    """Frozen JM1_FINAL promotion gates vs S0 (strict 95% bound rule). `summ` may be the JointMinutesV2 development
    summary (cand = 'JM1', development preview) or the JM1_FINAL confirmation summary (cand = 'JM1_FINAL')."""
    d, jd, md = summ["deltas"][f"{cand}-S0"], summ["joint_deltas"][f"{cand}-S0"], summ["minutes_deltas"][f"{cand}-S0"]
    out = {}
    agg = {"MARG_LOG": d["ALL"]["MARG_LOG"], "MARG_CRPS_SCALED": d["ALL"]["MARG_CRPS_SCALED"]}
    out["C1_aggregate_superiority"] = {**agg, "rule": "both upper bounds < 0",
                                       "PASS": all(_hi(v) < G_AGG_SUPERIORITY for v in agg.values())}
    stat_log = {s: d["ALL"][f"MARG_LOG|{s}"] for s in L.STATS}
    stat_crps = {s: d["ALL"][f"MARG_CRPS_SCALED|{s}"] for s in L.STATS}
    fails = [f"LOG|{s}" for s, v in stat_log.items() if _hi(v) > G_STAT_LOG] + \
            [f"CRPS|{s}" for s, v in stat_crps.items() if _hi(v) > G_STAT_CRPS]
    out["C2_every_stat_non_inferior"] = {"MARG_LOG": stat_log, "MARG_CRPS_SCALED": stat_crps,
                                         "margins": {"MARG_LOG": G_STAT_LOG, "MARG_CRPS_SCALED": G_STAT_CRPS},
                                         "failures": fails, "PASS": not fails}
    grp = {f"{s}|{r}": d[f"ROLE:{r}"][f"MARG_LOG|{s}"] for s in L.STATS for r in TIERS if f"ROLE:{r}" in d}
    out["C3_stat_x_tier_non_inferior"] = {"MARG_LOG": grp, "margin": G_STAT_GROUP_LOG,
                                          "failures": [k for k, v in grp.items() if _hi(v) > G_STAT_GROUP_LOG],
                                          "missing_tiers": [r for r in TIERS if f"ROLE:{r}" not in d]}
    out["C3_stat_x_tier_non_inferior"]["PASS"] = not (out["C3_stat_x_tier_non_inferior"]["failures"]
                                                      or out["C3_stat_x_tier_non_inferior"]["missing_tiers"])
    gp_ = {f"{s}|{p}": d[f"POS:{p}"][f"MARG_LOG|{s}"] for s in L.STATS for p in POSITIONS_GATED if f"POS:{p}" in d}
    out["C4_stat_x_position_non_inferior"] = {"MARG_LOG": gp_, "margin": G_STAT_GROUP_LOG,
                                              "failures": [k for k, v in gp_.items() if _hi(v) > G_STAT_GROUP_LOG],
                                              "missing_positions": [p for p in POSITIONS_GATED if f"POS:{p}" not in d]}
    out["C4_stat_x_position_non_inferior"]["PASS"] = not (out["C4_stat_x_position_non_inferior"]["failures"]
                                                          or out["C4_stat_x_position_non_inferior"]["missing_positions"])
    tier = {r: md[f"ROLE:{r}"]["MIN_CRPS"] for r in TIERS if f"ROLE:{r}" in md}
    out["C5_exposure_not_degraded"] = {"MIN_CRPS_ALL": md["ALL"]["MIN_CRPS"], "MIN_CRPS_TIER": tier,
                                       "margins": {"ALL": G_MIN_CRPS_ALL, "TIER": G_MIN_CRPS_TIER},
                                       "PASS": _hi(md["ALL"]["MIN_CRPS"]) <= G_MIN_CRPS_ALL and len(tier) == 3
                                       and all(_hi(v) <= G_MIN_CRPS_TIER for v in tier.values())}
    lam = summ["latent_scale_lam"]
    pts = {r: {"S0": lam[f"ROLE:{r}"]["S0"]["PTS"]["lam"], cand: lam[f"ROLE:{r}"][cand]["PTS"]["lam"]}
           for r in ("BENCH_9PLUS", "ROTATION_6_8") if f"ROLE:{r}" in lam}
    out["C6_pts_spread_bench_rotation"] = {"PTS_lam": pts, "rule": "|log lam| closer to 1 than S0 for BENCH_9PLUS and "
                                                                    "ROTATION_6_8 (Phase 3 A4)",
                                           "PASS": len(pts) == 2 and all(abs(np.log(v[cand])) < abs(np.log(v["S0"]))
                                                                         for v in pts.values())}
    mc_es, mc_vs = summ["mc_se"].get(f"{cand}-S0|ES"), summ["mc_se"].get(f"{cand}-S0|VS_BAL")
    near = {k: (abs(_hi(v) - mg) / se if se else None) for k, v, mg, se in (("ES", jd["ES"], G_ES, mc_es),
                                                                             ("VS_BAL", jd["VS_BAL"], G_VS, mc_vs))}
    mc_ok = all(x is None or x >= P3.MC_DECISION_RATIO for x in near.values())
    out["C7_joint_non_inferior"] = {"ES": jd["ES"], "VS_BAL": jd["VS_BAL"], "margins": {"ES": G_ES, "VS_BAL": G_VS},
                                    "bound_to_margin_in_mc_se": near, "MC_ADEQUATE": mc_ok,
                                    "PASS": bool(_hi(jd["ES"]) <= G_ES and _hi(jd["VS_BAL"]) <= G_VS and mc_ok)}
    ev = summ["events_by_family"].get("TM_SCORER_SCORER", {})
    if ev:
        g0, g1 = abs(ev["S0"]["calibration_gap_obs_minus_pred"]), abs(ev[cand]["calibration_gap_obs_minus_pred"])
        out["C8_both_top_scorers_over"] = {"observed": ev["observed_rate"], "S0_mean_p": ev["S0"]["mean_p"],
                                           f"{cand}_mean_p": ev[cand]["mean_p"], "brier_minus_S0": ev[cand]["brier_minus_S0"],
                                           "abs_gap_S0": g0, f"abs_gap_{cand}": g1, "rule": "Phase 3 A6 (unchanged)",
                                           "PASS": bool(ev[cand]["brier_minus_S0"]["value"] < 0 and g1 < g0)}
    else:
        out["C8_both_top_scorers_over"] = {"PASS": False, "note": "event family missing"}
    inv = acc["INVALID_CORRELATION_MATRICES"].get(cand, 0)
    out["C9_integrity"] = {"COMBO_IDENTITY_VIOLATIONS": acc["COMBO_IDENTITY_VIOLATIONS"], "INVALID_CORRELATION_MATRICES": inv,
                           "GENUINE_PIT_VIOLATIONS": extra["env_pit"]["violations"], "chronology": extra["chronology"],
                           "PASS": acc["COMBO_IDENTITY_VIOLATIONS"] == 0 and inv == 0 and extra["env_pit"]["violations"] == 0
                           and bool(extra["chronology"]["OK"])}
    out["C10_deterministic"] = {"check": extra["determinism"], "PASS": bool(extra["determinism"]["identical"])}
    out["ALL_PASS"] = bool(all(v["PASS"] for k, v in out.items() if k[:1] == "C"))
    return out


def determinism(F, params, s0_struct, scale, n_games=2) -> dict:
    df, envp = P3.season_inputs(F, params["struct"])
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    s4s = D.structure("J2", params["s4"]["residual_dependence"])
    hs = []
    for _ in range(2):
        h = hashlib.sha256()
        for gid, gp in list(df.groupby("game_id", sort=True))[:n_games]:
            r = eval_game(gid, gp, envd, params, s0_struct, s4s, scale, sims=5000)
            for a in ARMS:
                h.update(np.ascontiguousarray(r["single"][a]["log"]).tobytes())
            h.update(np.ascontiguousarray(r["M"]).tobytes())
            h.update(json.dumps(r["joint"], sort_keys=True, default=_jd).encode())
        hs.append(h.hexdigest())
    return {"games": n_games, "draws": 5000, "hashes": hs, "identical": hs[0] == hs[1]}


# ============================================================================ development evidence (saved 2024-25 run)
def reproduce_development_arm(log=print, n_games: int = REPRO_GAMES) -> dict:
    """Mechanical equivalence check: this module, with the development procedure (parameters through 2023-24), re-scores
    the first n_games of 2024-25 and must reproduce the SAVED JM1 and S0 development values. No metric is selected on."""
    params = fit_procedure(DEV["T"], DEV["resid_seasons"], log)
    s0, scale = P3._s0_structure("select")
    ev_out = evaluate(DEV["eval"], params, s0, scale, "repro_2025", log, limit_games=n_games, write=False)
    saved = pd.read_parquet(JM.SCRATCH / f"develop_{DEV['eval']}_rows.parquet")
    saved_g = pd.read_parquet(JM.SCRATCH / f"develop_{DEV['eval']}_games.parquet")
    w = ev_out["wide"].merge(saved, on="player_game_example_id", suffixes=("", "__dev"), how="left")
    worst = {}
    for a_new, a_dev in ((CAND, "JM1"), ("S0", "S0")):
        cols = [(f"{a_new}|{s}|{k}", f"{a_dev}|{s}|{k}__dev" if a_dev == "S0" else f"{a_dev}|{s}|{k}")
                for s in L.STATS for k in ("log", "crps", "mean", "var")] + \
               [(f"{a_new}|{k}", f"{a_dev}|{k}__dev" if a_dev == "S0" else f"{a_dev}|{k}") for k in ("min_crps", "min_mean", "min_sd")]
        for cn, cd in cols:
            x, yv = w[cn].to_numpy(dtype=float), w[cd].to_numpy(dtype=float)
            worst[cn] = float(np.max(np.abs(x - yv) / np.maximum(np.abs(yv), 1.0)))
    g = ev_out["gt"].merge(saved_g, on="game_id", suffixes=("", "__dev"))
    for a_new, a_dev in ((CAND, "JM1"), ("S0", "S0")):
        for k in ("ES", "VS_BAL", "PRA_LOG", "PRA_CRPS", "EVENT_BRIER", "COMBO_LOG"):
            cd = f"{a_dev}|{k}__dev" if a_dev == "S0" else f"{a_dev}|{k}"
            worst[f"{a_new}|{k}"] = float(np.max(np.abs(g[f"{a_new}|{k}"] - g[cd]) / np.maximum(np.abs(g[cd]), 1.0)))
    mx = max(worst.values())
    return {"games": n_games, "player_games": int(len(w)), "rows_matched": int(w["JM1|PTS|log"].notna().sum()),
            "max_relative_difference": mx, "tolerance": REPRO_RTOL, "per_metric_max_relative_difference": worst,
            "IDENTICAL_WITHIN_TOLERANCE": bool(mx <= REPRO_RTOL and w["JM1|PTS|log"].notna().all()),
            "parameters": {"T": params["T"], "resid_seasons": params["resid_seasons"], "e_dur": params["e_dur"],
                           "phi": {s: params["s4"]["dispersion"][s]["phi"] for s in L.STATS}},
            "note": "saved values are float32; tolerance covers storage rounding only"}


def development_evidence(repro: dict) -> dict:
    """Compact JM1 development record from the SAVED JointMinutesV2 2024-25 run (descriptive / selection evidence)."""
    dev = json.loads(JM.DEV_PATH.read_text())
    s = dev["summary"]
    pick = lambda x: {k: x[k] for k in ("value", "lo", "hi")}
    d, jd, md = s["deltas"]["JM1-S0"], s["joint_deltas"]["JM1-S0"], s["minutes_deltas"]["JM1-S0"]
    regress = [{"stratum": st, "metric": k, **pick(v), "n": s["strata_sizes"].get(st)}
               for st, vv in d.items() for k, v in vv.items() if v["lo"] > 0]
    regress += [{"stratum": st, "metric": k, **pick(v), "n": s["strata_sizes"].get(st)}
                for st, vv in md.items() for k, v in vv.items() if v["lo"] > 0 and k in ("MIN_CRPS", "MIN_AE_MEAN", "MIN_SE_MEAN")]
    ev = s["events_by_family"]
    acc = {"COMBO_IDENTITY_VIOLATIONS": dev["accounting"]["COMBO_IDENTITY_VIOLATIONS"],
           "INVALID_CORRELATION_MATRICES": {"JM1": dev["accounting"]["INVALID_CORRELATION_MATRICES"]["JM1"]}}
    preview = gates(s, "JM1", acc, {"env_pit": dev["pit"]["team_features"], "determinism": dev["determinism"],
                                    "chronology": {"OK": True, "note": "development chronology (parameters through 2023-24)"}})
    return {
        "version": VERSION, "experiment": EXPERIMENT, "nature": "DESCRIPTIVE / SELECTION evidence from the saved 2024-25 "
        "JointMinutesV2 development run (JM1 arm). 2024-25 is development data. NOT confirmation.",
        "source": {"record": _rel(JM.DEV_PATH), "record_sha256": _sha(JM.DEV_PATH), "commit": "305b0f3",
                   "scratch": "data/qa/large/v2_jm2/develop_2025_*.parquet"},
        "games": s["games"], "player_games": s["player_games"],
        "marginal": {st: {"MARG_LOG": pick(d["ALL"][f"MARG_LOG|{st}"]), "MARG_CRPS_SCALED": pick(d["ALL"][f"MARG_CRPS_SCALED|{st}"]),
                          "MARG_BRIER": pick(d["ALL"][f"MARG_BRIER|{st}"]),
                          "bias_S0": s["stat_bias_pred_minus_obs"]["ALL"]["S0"][st],
                          "bias_JM1": s["stat_bias_pred_minus_obs"]["ALL"]["JM1"][st],
                          "pit_coverage_S0": s["pit_calibration"]["S0"][st]["central_coverage"],
                          "pit_coverage_JM1": s["pit_calibration"]["JM1"][st]["central_coverage"]} for st in L.STATS}
                    | {"AGGREGATE": {k: pick(d["ALL"][k]) for k in ("MARG_LOG", "MARG_CRPS_SCALED", "MARG_BRIER")}},
        "pts_spread_ratio": {r: {a: s["latent_scale_lam"][r][a]["PTS"]["lam"] for a in ("S0", "JM1")} for r in s["latent_scale_lam"]},
        "joint": {k: {"S0": s["joint_levels"]["S0"][k]["value"], "JM1": s["joint_levels"]["JM1"][k]["value"], "delta": pick(jd[k])}
                  for k in jd},
        "both_top_scorers_over": {"observed": ev["TM_SCORER_SCORER"]["observed_rate"],
                                  **{a: {kk: ev["TM_SCORER_SCORER"][a][kk] for kk in ("mean_p", "brier", "log", "calibration_gap_obs_minus_pred")}
                                     for a in ("S0", "JM1")}},
        "event_families": {k: {"observed": v["observed_rate"], "S0_gap": v["S0"]["calibration_gap_obs_minus_pred"],
                               "JM1_gap": v["JM1"]["calibration_gap_obs_minus_pred"], "JM1_brier_minus_S0": v["JM1"].get("brier_minus_S0")}
                           for k, v in ev.items()},
        "minutes": {st: {a: {k: s["minutes_levels"][st][a][k]["value"] for k in ("MIN_CRPS", "MIN_AE_MEAN", "MIN_BIAS")}
                         | {"RMSE": s["minutes_levels"][st][a]["RMSE"]} for a in ("S0", "JM1")}
                    | {"delta_MIN_CRPS": pick(md[st]["MIN_CRPS"])} for st in ("ALL", "ROLE:TOP5_MINUTES", "ROLE:ROTATION_6_8",
                                                                              "ROLE:BENCH_9PLUS", "EARLY_SEASON", "MONTH:NOV")},
        "minutes_coverage": {a: s["minutes_calibration"][a] for a in ("S0", "JM1")},
        "correlation_calibration": {a: {k: {kk: v[kk] for kk in ("rmse", "slope") if kk in v} for k, v in s["correlation_calibration"][a].items()}
                                    for a in ("S0", "JM1")},
        "residual_j2_vs_old_j2": {k: {kk: v[kk] for kk in v if kk != "pairs"} for k, v in dev["old_J2_vs_residual_J2"].items()},
        "residual_j2_note": "JM1 and JM2 share the residual J2 / dispersion refit of the development procedure",
        "protected_category_regressions_CI_excludes_0": regress,
        "confirmation_gates_applied_to_development_PREVIEW": preview,
        "reproduction_by_this_module": repro}


# ============================================================================ protocol
def protocol(dev_evidence_sha: str) -> dict:
    return {
        "version": VERSION, "experiment": EXPERIMENT,
        "candidate": "JM1_FINAL = random player-minutes exposure + conditional dispersion refit + residual J2",
        "NOT": "not a joint team-minutes model: no exact 200/225-minute team reconciliation is claimed or performed",
        "frozen_before_2025_26_evaluation": True,
        "architect_authorization": "2026-10-19 ruling after the JM2 postmortem (JM2_POSTMORTEM.md, jm2_postmortem.json): JM2 "
                                   "rejected, no JM3; exactly one candidate, JM1_FINAL, the existing JM1 development arm frozen",
        "pre_freeze_disclosure": (
            "JM1 was selected from 2024-25 DEVELOPMENT evidence (the diagnostic JM1 arm of the single frozen JointMinutesV2 "
            "run). The mechanism is the existing JM1 arm unchanged (verified by bit-level reproduction of saved development "
            "values). No parameter search. 2025-26 results of OTHER models were inspected earlier in the project (Phase 1B "
            "lockbox, Phase 2 J2 confirmation); no JM1 / JM1_FINAL 2025-26 result has been computed or seen. The run below "
            "is a LOCKED RETROSPECTIVE CONFIRMATION, not a pristine lockbox."),
        "current_champion": "S0 = Phase 1B marginals + Phase 2 J2",
        "architecture": {
            "participation": "Phase 1B P(play) unchanged; scored population = the Phase 2 population (PLAYED, eligible); all "
                             "single-player quantities are conditional on playing, as in S0",
            "exposure": "per draw b and scored player i: M_ib = clip(min_mu_i + min_sigma_i eps_ib, 0, %d) x D_b / E[D]; "
                        "eps_ib iid N(0,1) (independent across players: no team factor, no budget, no reservoir, no "
                        "reconciliation, no teammate conservation); this is the Phase 1B discretized-normal conditional "
                        "minutes marginal sampled directly" % JM.KMAX,
            "duration_environment": "one duration D_b per game shared by both teams (fitted overtime law, D = 40 + 5K) and "
                                    "the accepted S1 shared game environment (pace / efficiency shocks), structural."
                                    "simulate_state streams; pace never changes the clock",
            "stat_means": "log mu_ibs = log mu_1B,is + GLM(intercept, log(max(M_ib, 1) / min_mu_i), pace_dev, own_eff_dev, "
                          "opp_eff_dev); anchor alpha = 1.0 (structural mean); ONE M_ib feeds all seven stats",
            "no_opportunity_redistribution": True,
            "counts": "NB / Poisson mixture over draws with the refit conditional dispersion phi_s; count latents from the "
                      "residual J2 Gaussian copula (common count-latent bank across arms)",
            "combos": "PRA / PR / PA / RA / STOCKS are sums of the same simulated stat draws",
            "named_player_pair_parameters": False},
        "fitting_procedure": {
            "function": "jm1_final.fit_procedure(T, resid_seasons) = phase3.build_procedure(T, resid_seasons, terms=JM_TERMS) "
                        "+ E[D] from the fitted overtime law",
            "environment": "team-games 2013-14..T (labels <= T)",
            "glm": "Poisson pseudo-likelihood with offset log mu_1B on Phase 1B OOS rows 2022-23..T given the realized state",
            "dispersion_refit": "one phi per stat, method of moments on the true-OOS structural residual basis given realized "
                                "minutes + environment (families kept)",
            "residual_j2": "dependence.fit(MID) on the conditional PMFs of the true-OOS structural residual basis",
            "residual_basis": "season F with structural parameters fitted through F-1 (true OOS)",
            "hyperparameter_search": "none"},
        "chronology": {
            "confirmation": {"parameters_through": CONFIRM["T"], "evaluate": CONFIRM["eval"],
                             "residual_basis_true_OOS_seasons": list(CONFIRM["resid_seasons"]),
                             "rule": "the frozen Phase 3 confirmation chronology (phase3.CONFIRM)",
                             "phase1b_forecasts": "OOS store F2026 rows (training_through_season < forecast_season asserted)",
                             "S0_comparator": "Phase 2 J2 fit through 2024-25 (%s)" % _rel(P3.PHASE2_FITS["confirm"]),
                             "runs": "exactly once"},
            "development_reproduction": {"parameters_through": DEV["T"], "evaluate": DEV["eval"],
                                         "residual_basis_true_OOS_seasons": list(DEV["resid_seasons"])},
            "final_refit_through_2025_26": "NOT part of this protocol; only after a PASS and architect approval"},
        "comparison": {"population": "every eligible scored player-game of every 2025-26 game in the Phase 1B OOS store with a "
                                     "pregame P(play) (phase3.season_inputs), S0 and JM1_FINAL on the SAME games / rows",
                       "pairing": "same-game, common random numbers for count latents; game-clustered bootstrap",
                       "uncertainty": "game-clustered bootstrap, %d draws, seed %d; 95%% percentile intervals"
                                      % (P3.EVAL_BOOT, P3.EVAL_SEED),
                       "draws_per_game": SIMS},
        "metrics": {"player_stats": ["log score", "CRPS (scaled)", "threshold Brier", "PIT central coverage", "bias"],
                    "strata": ["ALL", "stat", "TOP5_MINUTES / ROTATION_6_8 / BENCH_9PLUS", "position G/F/C/U", "stat x role",
                               "stat x position", "cold-start classes", "EARLY_SEASON (pregame team games <= %d)"
                               % EARLY_TEAM_GAMES, "MONTH:NOV"],
                    "minutes": ["CRPS", "MAE", "RMSE", "bias", "80% / 90% coverage"],
                    "joint": ["ES", "VS_BAL (+ components)", "pairwise correlation calibration", "joint-event Brier (incl. "
                              "tail events)", "PRA / PR / PA / RA / STOCKS log / CRPS / threshold Brier"],
                    "diagnostics": ["PTS latent spread ratio by role", "both top scorers over (rate, Brier, log loss)",
                                    "old J2 vs residual J2"]},
        "confirmation_gates_vs_S0": {
            "rule": "strict: a gate passes only if its 95% bootstrap bound clears the margin; inconclusive = FAIL",
            "C1": "aggregate MARG_LOG and aggregate MARG_CRPS_SCALED: upper bound < 0 (superiority)",
            "C2": "every stat: MARG_LOG upper bound <= +%g and MARG_CRPS_SCALED upper bound <= +%g" % (G_STAT_LOG, G_STAT_CRPS),
            "C3": "every stat x tier (TOP5_MINUTES, ROTATION_6_8, BENCH_9PLUS): MARG_LOG upper bound <= +%g" % G_STAT_GROUP_LOG,
            "C4": "every stat x position (G, F, C): MARG_LOG upper bound <= +%g (U reported only)" % G_STAT_GROUP_LOG,
            "C5": "minutes exposure: MIN_CRPS upper bound <= +%g overall and <= +%g per tier vs Phase 1B" % (G_MIN_CRPS_ALL, G_MIN_CRPS_TIER),
            "C6": "PTS latent spread ratio |log lam| closer to 1 than S0 for BENCH_9PLUS and ROTATION_6_8 (Phase 3 A4)",
            "C7": "ES upper bound <= +%g and VS_BAL upper bound <= +%g, each bound >= %g MC SE from its margin"
                  % (G_ES, G_VS, P3.MC_DECISION_RATIO),
            "C8": "both top scorers over (TM_SCORER_SCORER): Brier below S0 AND |observed - mean predicted| smaller (Phase 3 A6)",
            "C9": "0 combo identity violations, 0 invalid correlation matrices, 0 genuine PIT violations, chronology asserted",
            "C10": "bit-identical re-simulation",
            "decision": "PASS iff every gate passes; otherwise FAIL (S0 stays champion; branch closed for 2026-27). No "
                        "post-hoc rule, no repair loop, no rerun on a design failure.",
            "gate_provenance": "margins inherited unchanged from the frozen JointMinutesV2 protocol (G1/G2 minutes, G5 stat "
                               "and stat x tier, G6 joint, G7 = Phase 3 A6) and Phase 3 A4; new: C1 superiority (a "
                               "challenger must beat the champion) and C4 stat x position at the stat x tier margin. The "
                               "gates were written after the 2024-25 development evidence was seen (development data); "
                               "their development preview is recorded in the development evidence file."},
        "seeds": {"structural_streams": "BLAKE2b(%d|stream|game): duration, environment, minutes" % JM.SEED_BASE,
                  "count_latents": "BLAKE2b(%d|game) (Phase 2 bank, common across arms)" % J.SIM_SEED,
                  "bootstrap": "seed %d" % P3.EVAL_SEED},
        "inputs": {"phase1b_oos_store": _rel(P2.STORE), "phase1b_oos_store_sha256": P3.STORE_SHA,
                   "phase1b_F2023_extension_sha256": P3.F2023_SHA, "MARGINAL_ARTIFACT_SHA256": P3.MARGINAL_SHA,
                   "PHASE2_DEPENDENCE_ARTIFACT_SHA256": P3.J2_SHA,
                   "S0_confirmation_fit": _rel(P3.PHASE2_FITS["confirm"]),
                   "S0_confirmation_fit_sha256": _sha(P3.PHASE2_FITS["confirm"]),
                   "jm2_frozen": JM2_FROZEN, "phase3_frozen": JM.PHASE3_FROZEN,
                   "development_evidence": _rel(DEV_EVIDENCE_PATH), "development_evidence_sha256": dev_evidence_sha,
                   "HISTORICAL_API_CALLS": 0},
        "code_sha256_at_freeze": code_state(),
    }


def stage_protocol(log=print) -> dict:
    if PROTOCOL_PATH.exists() or DEV_EVIDENCE_PATH.exists():
        raise RuntimeError("the JM1_FINAL protocol is frozen; it is never rewritten")
    if CONFIRM_PATH.exists():
        raise RuntimeError("confirmation record exists before the protocol")
    g = guards()
    repro = reproduce_development_arm(log)
    if not repro["IDENTICAL_WITHIN_TOLERANCE"]:
        raise JM1Violation(f"this module does not reproduce the saved JM1 development arm: {repro['max_relative_difference']}")
    _dump(DEV_EVIDENCE_PATH, development_evidence(repro))
    out = protocol(_sha(DEV_EVIDENCE_PATH))
    out["guards_at_freeze"] = g
    out["JM1_ARCHITECTURE_MATCHES_DEVELOPMENT_ARM"] = True
    _dump(PROTOCOL_PATH, out)
    return out


# ============================================================================ the one-shot confirmation
def compact_params(params: dict) -> dict:
    return {"version": VERSION, "T": params["T"], "e_dur": params["e_dur"], "resid_seasons": params["resid_seasons"],
            "structure": P3._compact_struct(params["struct"]), "s4_components": P3._compact_s4(params["s4"]),
            "resid_rows": params["resid_rows"]}


def stage_confirm(log=print) -> dict:
    if CONFIRM_PATH.exists():
        raise RuntimeError("the JM1_FINAL 2025-26 confirmation runs exactly once; its record already exists")
    prot_commit = M.committed_unchanged(PROTOCOL_PATH)
    dev_commit = M.committed_unchanged(DEV_EVIDENCE_PATH)
    prot = json.loads(PROTOCOL_PATH.read_text())
    now = code_state()
    changed = sorted(k for k in now if now[k] != prot["code_sha256_at_freeze"].get(k))
    if changed:
        raise JM1Violation(f"code changed since the protocol freeze: {changed}")
    g = guards()
    T, F = CONFIRM["T"], CONFIRM["eval"]
    params = fit_procedure(T, CONFIRM["resid_seasons"], log)
    _dump(CONFIRM_PARAMS_PATH, compact_params(params))
    s0, scale = P3._s0_structure("confirm")
    log(f"evaluating {ARMS} on F{F}")
    ev_out = evaluate(F, params, s0, scale, f"confirm_{F}", log)
    ev_out["scale"] = scale
    summ = summarize(ev_out)
    det = determinism(F, params, s0, scale)
    tt = ev_out["df"]["training_through_season"]
    chron = {"structure_T": params["struct"]["T"], "residual_seasons": params["resid_seasons"],
             "glm_seasons": params["struct"]["glm"]["seasons"], "phase1b_training_through_max": int(tt.max()),
             "evaluation_season": F, "S0_fit": P3.PHASE2_FITS["confirm"].name}
    chron["OK"] = bool(chron["structure_T"] == T and max(chron["residual_seasons"]) <= T and max(chron["glm_seasons"]) <= T
                       and chron["phase1b_training_through_max"] < F)
    extra = {"env_pit": ev_out["pit_audit_team_features"], "determinism": det, "chronology": chron}
    gt_ = gates(summ, CAND, ev_out["accounting"], extra)
    j2 = P3.j2_comparison(json.loads(P3.PHASE2_FITS["confirm"].read_text())["MID"], params["s4"]["residual_dependence"])
    out = {"version": VERSION, "experiment": EXPERIMENT, "stage": "LOCKED_RETROSPECTIVE_CONFIRMATION_2025_26",
           "nature": "locked retrospective confirmation (not a pristine lockbox: 2025-26 results of other models were "
                     "inspected earlier in the project); JM1_FINAL is not changed by this result",
           "protocol_commit": prot_commit, "protocol_sha256": _sha(PROTOCOL_PATH), "development_evidence_commit": dev_commit,
           "code_sha256": now, "code_changed_since_protocol": changed, "guards": g, "parameters_through": T,
           "evaluation_season": F, "parameters_record": _rel(CONFIRM_PARAMS_PATH), "parameters_sha256": _sha(CONFIRM_PARAMS_PATH),
           "summary": summ, "accounting": ev_out["accounting"], "pit": ev_out["pit_audit_team_features"],
           "chronology": chron, "old_J2_vs_residual_J2": j2, "gates": gt_, "determinism": det,
           "JM1_FINAL_CONFIRMATION_DECISION": "PASS" if gt_["ALL_PASS"] else "FAIL",
           "2025_26_USED_AS_CONFIRMATION": True, "2025_26_USED_TO_TUNE_JM1_BEFORE_CONFIRMATION": False,
           "HISTORICAL_API_CALLS": 0, "draws_per_game": SIMS}
    _dump(CONFIRM_PATH, out)
    return out


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    lg = lambda m: print(m, flush=True)
    if stage == "protocol":
        r = stage_protocol(lg)
        print(json.dumps({"protocol": _rel(PROTOCOL_PATH), "sha256": _sha(PROTOCOL_PATH),
                          "development_evidence": _rel(DEV_EVIDENCE_PATH)}, indent=1))
    elif stage == "confirm":
        r = stage_confirm(lg)
        print(json.dumps({"DECISION": r["JM1_FINAL_CONFIRMATION_DECISION"]}, indent=1))
    else:
        raise SystemExit("usage: python -m cbb_platform.v2.jm1_final protocol|confirm")
