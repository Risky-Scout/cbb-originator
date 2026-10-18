"""V2 Phase 3: structural interdependence (shared game environment + joint minutes + opportunity redistribution).

Stages (each refuses to run out of order):

    .venv/bin/python -m cbb_platform.v2.phase3 protocol   write the frozen protocol (commit it before `select`)
    .venv/bin/python -m cbb_platform.v2.phase3 select     structure through 2023-24, evaluate S0..S4 on 2024-25, select
    .venv/bin/python -m cbb_platform.v2.phase3 confirm    frozen procedure through 2024-25, evaluate 2025-26 ONCE
    .venv/bin/python -m cbb_platform.v2.phase3 final      final structural + residual-dependence artifacts through 2025-26

Inputs: the Phase 1B true-OOS forecast store (F2024..F2026) + its F2023 extension (cbb_platform/v2/phase3_prep.py), the
Stage 3 team features / team-game labels, and the Phase 2 J2 fits (S0). Outcomes of a forecast season are never used to
forecast it; 2026-27 is never read; no API calls. Model components: cbb_platform/v2/structural.py.
"""
from __future__ import annotations

import gc
import hashlib
import json
import sys
import time

import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P, props_v0_participation as Q
from cbb_platform import lockbox_runner as LR
from cbb_platform.v2 import dependence as D, joint as J, latent as L, marginal as M, phase1b as B, phase2 as P2
from cbb_platform.v2 import phase3_prep as PP, provenance as PV, structural as S

VERSION = "cbb-v2-phase3-structural-1.0.0"
OUT = M.OUT_DIR
PROTOCOL_PATH = OUT / "phase3_protocol.json"
SELECTION_PATH = OUT / "phase3_selection.json"
REBUILD_PATH = OUT / "phase3_selection_rebuild.json"
CONFIRM_PATH = OUT / "phase3_confirmation.json"
FINAL_PATH = OUT / "phase3_structural_final.json"
RESID_DEP_PATH = OUT / "phase3_residual_dependence.json"
VERIFY_PATH = OUT / "phase3_verification.json"
STORE_PATH = M.OOS_DIR / "phase3_structural_oos.parquet"
STORE_MANIFEST_PATH = OUT / "phase3_structural_oos_manifest.json"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_phase3"
MARGINAL_SHA = P2.MARGINAL_SHA
J2_PATH = OUT / "dependence_J2.json"
J2_SHA = "b41564a988f37a4c01a0dec9f8a9cb67aea69717294809b3eaa49acf4babb1ff"
V1_SHA = P2.V1_SHA
STORE_SHA = P2.STORE_SHA
F2023_SHA = "aeb1d11594cd0775f9c38e7d1cb89a7965e9d461774b499a388bc424d9df84cc"
PHASE2_FITS = {"select": OUT / "phase2_selection_fit_2023_24.json", "confirm": OUT / "phase2_confirmation_fit_through_2024_25.json"}
CODE = [C.PROJECT_ROOT / "cbb_platform" / "v2" / f for f in ("structural.py", "phase3.py", "phase3_prep.py")]
FIRST_TEAM_SEASON = 2014
OOS_FIRST = 2023
SELECT = {"T": 2024, "eval": 2025, "resid_seasons": (2024,)}
CONFIRM = {"T": 2025, "eval": 2026, "resid_seasons": (2024, 2025)}
FINAL = {"T": 2026, "resid_seasons": (2024, 2025, 2026)}
ARMS = ("S0", "S1", "S2", "S3", "S4_A05", "S4_A10")
ALPHAS = {"S4_A05": 0.5, "S4_A10": 1.0}
LAYER_OF = {"S1": "SHARED_GAME_ENVIRONMENT", "S2": "JOINT_MINUTES", "S3": "OPPORTUNITY_REDISTRIBUTION"}
SIMS = J.SIMS
EVAL_BOOT = 2000
EVAL_SEED = 20366
ROT_GROUPS = ("TOP5_MINUTES", "ROTATION_6_8", "BENCH_9PLUS")
SHARED_EVENTS = ("TM_SCORER_SCORER", "TM_CREATOR_SCORER", "TM_CREATOR_SHOOTER", "TM_REB_REB", "TM3_SCORERS_CREATOR",
                 "OP_SCORER_SCORER", "OP_REB_REB", "OP_TOV_STL", "TAIL_TM_SCORERS_HIGH", "TAIL_TM_SCORER_HIGH_OTHER_LOW",
                 "TAIL_OP_SCORERS_HIGH", "TAIL_OP_TOV_STL_HIGH")
STAT_DEGRADE_NATS = 0.002
AGG_TOLERANCE_NATS = 0.001
MC_DECISION_RATIO = 3.0
HALF = LR.HALF_LINES
MIN_KMAX = 60


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


_rel, _dump, _jd = P2._rel, P2._dump, P2._json_default


def guards() -> dict:
    out = {"V1_ARTIFACT_SHA256": _sha(PV.V1_ARTIFACT), "MARGINAL_ARTIFACT_SHA256": _sha(P2.MARGINAL_PATH),
           "PHASE2_DEPENDENCE_ARTIFACT_SHA256": _sha(J2_PATH), "OOS_STORE_SHA256": _sha(P2.STORE),
           "F2023_STORE_SHA256": _sha(PP.STORE_PATH)}
    out["V1_ARTIFACT_UNCHANGED"] = out["V1_ARTIFACT_SHA256"] == V1_SHA
    out["V2_PHASE1B_ARTIFACT_UNCHANGED"] = out["MARGINAL_ARTIFACT_SHA256"] == MARGINAL_SHA
    out["PHASE2_J2_ARTIFACT_UNCHANGED"] = out["PHASE2_DEPENDENCE_ARTIFACT_SHA256"] == J2_SHA
    out["OOS_STORE_UNCHANGED"] = out["OOS_STORE_SHA256"] == STORE_SHA
    out["F2023_STORE_UNCHANGED"] = out["F2023_STORE_SHA256"] == F2023_SHA
    if not all(v for k, v in out.items() if k.endswith("UNCHANGED")):
        raise RuntimeError(f"an input artifact changed: {out}")
    return out


def code_state() -> dict:
    return {_rel(p): _sha(p) for p in CODE}


def code_vs_protocol() -> dict:
    prot = json.loads(PROTOCOL_PATH.read_text())["code_sha256_at_freeze"]
    now = code_state()
    return {"code_sha256": now, "changed_since_protocol": sorted(k for k in now if now[k] != prot.get(k))}


# ============================================================================ data
STORE_COLS = ["forecast_season", "training_through_season", "game_id", "team_id", "player_id", "player_game_example_id",
              "prediction_cutoff_utc", "is_home", "position_group", "cold_start_class", "role_diagnostic", "p_play",
              "p_play_status", "participation_status", "min_mu", "min_sigma", "y_min", "pmf_support_max"] + \
             [f"{s}_{c}" for s in L.STATS for c in ("mu", "phi", "family")] + [f"y_{s}" for s in L.STATS]


def load_store(seasons) -> pd.DataFrame:
    """Phase 1B OOS forecast rows (every candidate) of the given forecast seasons (F2023 from the prep extension)."""
    seasons = S.check_seasons(seasons)
    parts = []
    main = [s for s in seasons if s in D.OOS_SEASONS]
    if main:
        parts.append(pd.read_parquet(P2.STORE, columns=STORE_COLS, filters=[("forecast_season", "in", main)]))
    if OOS_FIRST in seasons:
        parts.append(pd.read_parquet(PP.STORE_PATH, columns=STORE_COLS))
    if set(seasons) - set(main) - {OOS_FIRST}:
        raise S.V2Phase3Violation(f"no Phase 1B OOS forecasts for {seasons}")
    df = pd.concat(parts, ignore_index=True)
    if not (df["training_through_season"] < df["forecast_season"]).all():
        raise S.V2Phase3Violation("a forecast row was not out of sample")
    if not set(df["forecast_season"]) <= set(seasons):
        raise S.V2Phase3Violation("store filter leaked another season")
    df["role_diagnostic"] = df["role_diagnostic"].fillna("NO_ROLE_HISTORY")
    df["position_group"] = df["position_group"].fillna("U")
    return df


def scored_mask(df: pd.DataFrame) -> np.ndarray:
    """Phase 2 population: PLAYED with all 7 count labels, in a game where both teams have such rows."""
    ok = df["participation_status"].eq("PLAYED") & df[[f"y_{s}" for s in L.STATS]].notna().all(axis=1)
    key = df["game_id"].where(ok)
    nteam = df.assign(_k=key).dropna(subset=["_k"]).groupby("game_id")["team_id"].nunique()
    both = df["game_id"].map(nteam).fillna(0).eq(2)
    return (ok & both).to_numpy()


# ============================================================================ structural parameters
def fit_structure(T: int, terms=S.GLM_TERMS) -> dict:
    """Environment on team-games 2013-14..T, GLM on Phase 1B OOS rows 2022-23..T (both with labels <= T only)."""
    tf = S.load_team_frame(range(FIRST_TEAM_SEASON, T + 1), labels=True)
    env = S.env_fit(tf)
    seasons = list(range(OOS_FIRST, T + 1))
    rows, acc = S.glm_rows(load_store(seasons), tf[tf["season"].isin(seasons)], env)
    glm = S.glm_fit(rows) if tuple(terms) == S.GLM_TERMS else glm_fit_terms(rows, terms)
    return {"T": T, "env": env, "glm": glm, "glm_population": acc, "pit_audit": S.pit_audit(tf), "terms": list(terms)}


def glm_fit_terms(rows, terms) -> dict:
    out = {"terms": list(terms), "n_rows": int(len(rows)), "seasons": sorted(int(x) for x in rows["forecast_season"].unique()),
           "stats": {}}
    for s in L.STATS:
        X = S.glm_design(rows, s, terms)
        b, se = S.poisson_irls(X, rows[f"y_{s}"].to_numpy(dtype=float), np.log(rows[f"{s}_mu"].to_numpy(dtype=float)))
        coef = {t: 0.0 for t in S.GLM_TERMS} | dict(zip(terms, b.tolist()))
        out["stats"][s] = {"coef": coef, "se_robust": dict(zip(terms, se.tolist())), "family": S.FAMILY_OF[s]}
    return out


def residual_rows(F: int, struct_prev: dict) -> tuple[pd.DataFrame, dict]:
    """True-OOS structural residual basis of season F: structural parameters fitted through F-1 applied to the REALIZED
    state of F (realized minutes / lineup / environment are the conditioning labels of the residual)."""
    if struct_prev["T"] >= F:
        raise S.V2Phase3Violation("structural parameters must predate the residual season")
    tf = S.load_team_frame([F], labels=True)
    rows, acc = S.glm_rows(load_store([F]), tf, struct_prev["env"])
    terms = struct_prev["terms"]
    mu = S.realized_conditional_mu(rows, struct_prev["glm"], terms)
    for j, s in enumerate(L.STATS):
        rows[f"cond_{s}_mu"] = mu[:, j]
    rows["structure_T"] = struct_prev["T"]
    return rows, acc


def fit_s4(resid: pd.DataFrame, struct_seasons: list) -> dict:
    """S4 components from the true-OOS structural residual basis: conditional dispersion (method of moments) and the
    residual J2 (cbb_platform/v2/dependence.fit, MID, unchanged architecture) on the conditional PMFs."""
    fam = {s: resid[f"{s}_family"].iloc[0] for s in L.STATS}
    y = resid[[f"y_{s}" for s in L.STATS]].to_numpy(dtype=float)
    mu = resid[[f"cond_{s}_mu" for s in L.STATS]].to_numpy(dtype=float)
    disp = S.dispersion_refit(y, mu, fam)
    pop = resid[["forecast_season", "game_id", "team_id", "player_id", "role_diagnostic", "position_group"]].copy()
    for j, s in enumerate(L.STATS):
        pop[f"{s}_mu"] = np.maximum(mu[:, j], 1e-6)
        pop[f"{s}_phi"] = disp[s]["phi"]
        pop[f"y_{s}"] = y[:, j]
    pop = pop.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)
    dep = D.fit(pop, "MID")
    scale = y.std(axis=0, ddof=1)
    return {"dispersion": disp, "residual_dependence": dep, "fit_rows": int(len(pop)), "fit_games": int(pop["game_id"].nunique()),
            "fit_seasons": sorted(int(x) for x in pop["forecast_season"].unique()), "structures_used": struct_seasons,
            "scale": dict(zip(L.STATS, scale.tolist()))}


# ============================================================================ evaluation
def season_inputs(F: int, struct: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All candidate rows of the evaluated games + the pregame environment (no label of season F is loaded)."""
    df = load_store([F])
    if df["p_play"].isna().any():
        raise S.V2Phase3Violation(f"F{F}: P(play) missing; the base scenario needs a pregame P(play)")
    sc = scored_mask(df)
    df["_scored"] = sc
    df = df[df["game_id"].isin(df.loc[sc, "game_id"].unique())]
    df = df.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)
    tf = S.load_team_frame([F], labels=False)
    envp = S.env_predict(tf, struct["env"])
    return df, envp.merge(tf[["game_id", "team_id", "prediction_cutoff_utc", "res_state_available_at",
                                "box_state_available_at"]], on=["game_id", "team_id"])


def _game_input(gid, gp: pd.DataFrame, envp: dict) -> S.GameInput:
    team = (gp["team_id"] != gp["team_id"].min()).astype(int).to_numpy()
    tids = [gp["team_id"].min(), gp["team_id"].max()]
    env = np.zeros((2, 3))
    for t, tid in enumerate(tids):
        r = envp.get((gid, tid))
        if r is not None:
            env[t] = r
    return S.GameInput(gid, team, gp["_scored"].to_numpy(), gp["p_play"].to_numpy(dtype=float),
                       gp["min_mu"].to_numpy(dtype=float), gp["min_sigma"].to_numpy(dtype=float), S.rates(gp), env)


def single_metrics(pmf: np.ndarray, y: np.ndarray, stat_idx: np.ndarray) -> dict:
    """Per (row, stat) cell from its predictive PMF: log score, CRPS, mean, var, P(X > half lines), PIT interval,
    mid-PIT latent z with its model mean / variance (Phase 2 latent scale diagnostic)."""
    k = np.arange(pmf.shape[1])
    yi = np.minimum(y.astype(np.int64), pmf.shape[1] - 1)
    r = np.arange(len(y))
    cdf = np.minimum(np.cumsum(pmf, axis=1), 1.0)
    cdf[:, -1] = 1.0
    lo = np.where(yi > 0, cdf[r, np.maximum(yi - 1, 0)], 0.0)
    hi = cdf[r, yi]
    mean = pmf @ k
    var = pmf @ (k * k) - mean ** 2
    out = {"log": -np.log(np.maximum(pmf[r, yi], 1e-300)), "crps": ((cdf - (k[None] >= yi[:, None])) ** 2).sum(axis=1),
           "mean": mean, "var": var, "pit_lo": lo, "pit_hi": hi}
    for li in range(3):
        line = np.array([HALF[L.STATS[j]][li] for j in stat_idx])
        out[f"pover{li}"] = 1.0 - cdf[r, np.floor(line).astype(int)]
    low = np.concatenate([np.zeros((len(y), 1)), cdf[:, :-1]], axis=1)
    p = np.maximum(cdf - low, 0.0)
    zt = L.gaussianize(low + 0.5 * p)
    z = L.gaussianize(lo + 0.5 * (hi - lo))
    ez = (p * zt).sum(axis=1)
    out["zc"] = z - ez
    out["vz"] = (p * zt * zt).sum(axis=1) - ez ** 2
    return out


def minutes_metrics(pmf: np.ndarray, y: np.ndarray) -> dict:
    k = np.arange(pmf.shape[1])
    yi = np.clip(np.round(y).astype(np.int64), 0, pmf.shape[1] - 1)
    r = np.arange(len(y))
    cdf = np.minimum(np.cumsum(pmf, axis=1), 1.0)
    mean = pmf @ k
    med = (cdf < 0.5).sum(axis=1)
    out = {"min_log": -np.log(np.maximum(pmf[r, yi], 1e-300)), "min_crps": ((cdf - (k[None] >= yi[:, None])) ** 2).sum(axis=1),
           "min_mean": mean, "min_sd": np.sqrt(np.maximum(pmf @ (k * k) - mean ** 2, 0.0)), "min_median": med.astype(float),
           "min_pit_lo": np.where(yi > 0, cdf[r, np.maximum(yi - 1, 0)], 0.0), "min_pit_hi": cdf[r, yi]}
    for q in (0.05, 0.1, 0.25, 0.75, 0.9, 0.95):
        out[f"min_q{int(q * 100):02d}"] = (cdf < q).sum(axis=1).astype(float)
    return out


def eval_game(gid, gp, envp, struct, s4, s0_struct, s4_structs, scale, sims=SIMS, arms=ARMS) -> dict:
    """Every arm of one game from shared random numbers. Single-player quantities (marginals, minutes, combos) are
    conditional on that player playing (his forced-active world); the joint vector couples those worlds through the
    common duration, environment, participation uniforms, minute noise and latent normals."""
    gi = _game_input(gid, gp, envp)
    rows = gp[gp["_scored"]].reset_index(drop=True)
    n = len(rows)
    y = rows[[f"y_{s}" for s in L.STATS]].to_numpy(dtype=float)
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    groups = list(D.group_of(rows))
    marg0 = J.game_marginals(rows)
    E = np.random.default_rng(D.game_seed(J.SIM_SEED, gid)).standard_normal((sims, n * J.NS))
    gc0 = D.game_correlation(s0_struct, groups, team)
    L0, jit0 = D.cholesky_unit(gc0["corr"])
    z0 = E @ L0.T
    s4_terms = tuple(struct["terms"])
    need_minutes = any(a in ("S2", "S3") for a in arms) or (any(a in ALPHAS for a in arms) and S.needs_minutes(s4_terms))
    st = S.simulate_state(gi, struct["env"], sims, minutes=need_minutes)
    base_lm = np.log(rows[[f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float))
    min_mu = rows["min_mu"].to_numpy(dtype=float)
    phi1b = np.array([float(rows[f"{s}_phi"].iloc[0]) for s in L.STATS])
    phic = np.array([s4["dispersion"][s]["phi"] for s in L.STATS]) if s4 else None
    stat_idx = np.tile(np.arange(J.NS), n)
    yv = y.ravel()
    draws, single, mins, info, anchors = {}, {}, {}, {}, {}
    pmf_min0 = P.minutes_pmf(min_mu, rows["min_sigma"].to_numpy(dtype=float), MIN_KMAX)
    s3_lm, z4, inf4 = None, None, None
    for a in arms:
        if a == "S0":
            draws[a] = J.counts_from_latent(z0, marg0["thr"]).reshape(sims, n, J.NS)
            single[a] = single_metrics(marg0["pmf"].reshape(n * J.NS, -1), yv, stat_idx)
            mins[a] = minutes_metrics(pmf_min0, rows["y_min"].to_numpy(dtype=float))
            info[a] = {"min_eig": gc0["min_eig"], "invalid": gc0["invalid"], "jitter": jit0}
            continue
        if a in ("S1", "S2", "S3"):
            lm = S.log_mu(base_lm, min_mu, team, st, struct["glm"], a)
            if a == "S3":
                s3_lm = lm
            phi, z, inf = phi1b, z0, info["S0"] if "S0" in info else {}
        else:
            full = s4_terms == tuple(S.GLM_TERMS)
            lm_s = s3_lm if (s3_lm is not None and full) else S.log_mu_terms(base_lm, min_mu, team, st, struct["glm"], s4_terms)
            lm, m_struct = S.anchor(lm_s, base_lm, ALPHAS[a])
            anchors[a] = m_struct
            if z4 is None:
                gc4 = D.game_correlation(s4_structs[a], groups, team)
                L4, jit4 = D.cholesky_unit(gc4["corr"])
                z4 = E @ L4.T
                inf4 = {"min_eig": gc4["min_eig"], "invalid": gc4["invalid"], "jitter": jit4}
            z, phi, inf = z4, phic, inf4
        tab = S.mixture_tables(lm.reshape(sims, n * J.NS), phi)
        draws[a] = S.counts(tab, z).reshape(sims, n, J.NS)
        single[a] = single_metrics(S.mixture_pmf(tab, sims), yv, stat_idx)
        info[a] = inf
        if a == "S1" or (a in ALPHAS and not S.needs_minutes(s4_terms)):
            mins[a] = mins["S0"] if "S0" in mins else minutes_metrics(pmf_min0, rows["y_min"].to_numpy(dtype=float))
        else:
            mr = np.clip(np.round(st.M), 0, MIN_KMAX).astype(np.int64)
            pm = np.stack([np.bincount(mr[:, i], minlength=MIN_KMAX + 1) for i in range(n)]) / sims
            mins[a] = minutes_metrics(pm, rows["y_min"].to_numpy(dtype=float))
        del tab
    # joint scores (Phase 2 definitions; lines / anchors from the S0 = Phase 1B marginals, pregame)
    masks = J.pair_masks(n, team)
    lines = J.combo_lines(marg0)
    ys = (y / scale).ravel()
    rl = J.roles(rows, team)
    h = sims // 2
    joint = {"game_id": gid, "n_players": n}
    mom = {}
    for a, x in draws.items():
        xs = (x.astype(float) / scale).reshape(sims, n * J.NS)
        va, vb = J.variogram(xs[:J.VS_DRAWS], ys, masks), J.variogram(xs[J.VS_DRAWS:2 * J.VS_DRAWS], ys, masks)
        joint.update({f"{a}|ES": J.energy_score(xs, ys), f"{a}|ES_rep_a": J.energy_score(xs[:h], ys),
                      f"{a}|ES_rep_b": J.energy_score(xs[h:], ys),
                      f"{a}|VS_BAL": 0.5 * (va["balanced"] + vb["balanced"]), f"{a}|VS_rep_a": va["balanced"],
                      f"{a}|VS_rep_b": vb["balanced"]})
        for k in ("within_player", "same_team", "cross_team", "uniform"):
            joint[f"{a}|VS_{k.upper()}"] = 0.5 * (va[k] + vb[k])
        cs = J.combo_scores(x, y, lines, sims)
        tot = 0.0
        for cb, v in cs.items():
            joint[f"{a}|{cb}_LOG"] = float(v["log"].sum())
            joint[f"{a}|{cb}_CRPS"] = float(v["crps"].sum())
            for li, q in enumerate(J.COMBO_LINE_Q):
                joint[f"{a}|{cb}_BRIER_q{int(q * 100)}"] = float(v["brier"][:, li].sum())
            tot += float(v["log"].sum())
        joint[f"{a}|COMBO_LOG"] = tot
        mean = single[a]["mean"].reshape(n, J.NS)
        sd = np.sqrt(np.maximum(single[a]["var"].reshape(n, J.NS), 1e-12))
        mom[a] = {"obs": J.moment_sums((y - mean) / sd, team), "sim": J.moment_sums((x - mean) / sd, team),
                  "e_obs": ((y - mean) / sd)}
    events = J.joint_events(draws, y, marg0, rl)
    joint["n_events"] = len(events)
    for a in draws:
        joint[f"{a}|EVENT_BRIER"] = float(sum((e[f"p_{a}"] - e["outcome"]) ** 2 for e in events))
        joint[f"{a}|EVENT_BRIER_SHARED"] = float(sum((e[f"p_{a}"] - e["outcome"]) ** 2 for e in events if e["event"] in SHARED_EVENTS))
        fl = 0.5 / sims
        joint[f"{a}|EVENT_LOG"] = float(sum(-np.log(np.clip(e[f"p_{a}"], fl, 1 - fl)) if e["outcome"] else
                                            -np.log(np.clip(1 - e[f"p_{a}"], fl, 1 - fl)) for e in events))
    joint["n_events_shared"] = sum(e["event"] in SHARED_EVENTS for e in events)
    # combo identities from the same draws (exact by construction; checked)
    for a, x in draws.items():
        der = J.derived_markets(x[:50])
        joint[f"{a}|COMBO_IDENTITY_VIOLATIONS"] = int(sum(
            int((der[c] != x[:50][:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2)).sum())
            for c, parts in J.COMBOS.items()))
        joint[f"{a}|INVALID_MATRIX"] = int(info[a].get("invalid", False))
    return {"joint": joint, "single": single, "mins": mins, "events": events, "moments": mom, "checks": st.checks,
            "state": st, "anchors": anchors, "rows": rows, "calib_err": st.calib_max_err}


def evaluate(F: int, struct: dict, s4: dict, s0_struct, scale: np.ndarray, tag: str, log=print, sims: int = SIMS,
             arms=ARMS, limit_games: int | None = None) -> dict:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    df, envp = season_inputs(F, struct)
    pit = S.pit_audit(envp)
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev])
            for r in envp.itertuples(index=False)}
    s4_structs = {a: D.structure("J2", s4["residual_dependence"]) for a in ALPHAS} if s4 else {}
    games = list(df.groupby("game_id", sort=True))
    if limit_games:
        games = games[:limit_games]
    keys = ("log", "crps", "mean", "var", "pover0", "pover1", "pover2", "pit_lo", "pit_hi", "zc", "vz")
    mkeys = ("min_log", "min_crps", "min_mean", "min_sd", "min_median", "min_pit_lo", "min_pit_hi",
             "min_q05", "min_q10", "min_q25", "min_q75", "min_q90", "min_q95")
    cell_parts = {a: {k: [] for k in keys} for a in arms}
    min_parts = {a: {k: [] for k in mkeys} for a in arms}
    row_parts, joint_rows, ev_rows, store_parts = [], [], [], []
    obs_m = {a: [] for a in arms}
    sim_m = {a: np.zeros((3, J.NS, J.NS)) for a in arms}
    pairs_n = []
    checks = {}
    calib_max = 0.0
    t0 = time.time()
    for gi_, (gid, gp) in enumerate(games):
        r = eval_game(gid, gp, envd, struct, s4, s0_struct, s4_structs, scale, sims, arms)
        rows = r["rows"]
        n = len(rows)
        meta = rows[["forecast_season", "game_id", "team_id", "player_id", "player_game_example_id", "role_diagnostic",
                     "position_group", "cold_start_class", "p_play", "min_mu", "y_min"] + [f"y_{s}" for s in L.STATS]].copy()
        row_parts.append(meta)
        for a in arms:
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
        for k, v in r["checks"].items():
            checks[k] = checks.get(k, 0) + v
        calib_max = max(calib_max, r["calib_err"])
        stt = r["state"]
        if stt.M.shape[1]:
            sp = pd.DataFrame({"player_game_example_id": rows["player_game_example_id"].to_numpy(),
                               "sim_min_mean": stt.M.mean(axis=0), "sim_min_sd": stt.M.std(axis=0),
                               "sim_min_q10": np.quantile(stt.M, 0.1, axis=0), "sim_min_q50": np.quantile(stt.M, 0.5, axis=0),
                               "sim_min_q90": np.quantile(stt.M, 0.9, axis=0),
                               "dur_mean": float(stt.dur.mean()), "p_overtime_sim": float((stt.dur > S.REGULATION).mean())})
            for j, f in enumerate(S.FAM):
                sp[f"share_{f}"] = stt.shares_mean[:, j]
                sp[f"opp_deficit_mean_{f}"] = stt.opp_deficit[:, :, j].mean(axis=0)
            for a, m in r["anchors"].items():
                for j, s in enumerate(L.STATS):
                    sp[f"{a}_struct_mean_{s}"] = m[:, j]
            store_parts.append(sp)
        if (gi_ + 1) % 100 == 0 or gi_ + 1 == len(games):
            el = time.time() - t0
            log(f"[{tag}] {gi_ + 1}/{len(games)} games  {el / 60:.1f} min  eta {(len(games) - gi_ - 1) * el / (gi_ + 1) / 60:.1f} min")
        del r
        if (gi_ + 1) % 500 == 0:
            gc.collect()
    meta = pd.concat(row_parts, ignore_index=True)
    cells = {a: {k: np.concatenate(v) for k, v in cell_parts[a].items()} for a in arms}
    mins = {a: {k: np.concatenate(v) for k, v in min_parts[a].items()} for a in arms}
    gt = pd.DataFrame(joint_rows)
    ev = pd.DataFrame(ev_rows)
    sim_store = pd.concat(store_parts, ignore_index=True) if store_parts else None
    # compact per-row evidence (scratch; deleted after freezing)
    cols = {}
    for a in arms:
        for k in ("log", "crps", "mean", "var", "pover0", "pover1", "pover2", "zc", "vz", "pit_lo", "pit_hi"):
            v = cells[a][k].reshape(len(meta), J.NS)
            for j, s in enumerate(L.STATS):
                cols[f"{a}|{s}|{k}"] = v[:, j]
        for k in mkeys:
            cols[f"{a}|{k}"] = mins[a][k]
    wide = pd.concat([meta, pd.DataFrame(cols)], axis=1)
    wide.to_parquet(SCRATCH / f"{tag}_rows.parquet", index=False)
    gt.to_parquet(SCRATCH / f"{tag}_games.parquet", index=False)
    ev.to_parquet(SCRATCH / f"{tag}_events.parquet", index=False)
    if sim_store is not None:
        sim_store.to_parquet(SCRATCH / f"{tag}_simstore.parquet", index=False)
    om = {a: np.stack(obs_m[a]) for a in arms}
    out = summarize(wide, gt, ev, om, sim_m, np.asarray(pairs_n, dtype=float), arms, df, sims, scale)
    out["accounting"] = checks | {"TEAM_MINUTES_CONSTRAINT_VIOLATIONS": checks.get("team_minutes_violations", 0)
                                  + checks.get("minutes_bound_violations", 0) + checks.get("inactive_minutes_nonzero", 0),
                                  "OPPORTUNITY_SHARE_VIOLATIONS": checks.get("negative_share", 0) + checks.get("share_sum_violations", 0),
                                  "COMBO_IDENTITY_VIOLATIONS": int(sum(gt[f"{a}|COMBO_IDENTITY_VIOLATIONS"].sum() for a in arms)),
                                  "INVALID_CORRELATION_MATRICES": {a: int(gt[f"{a}|INVALID_MATRIX"].sum()) for a in arms},
                                  "anchor_calibration_max_abs_minutes_error": calib_max,
                                  "team_expected_minutes_gap_mean_abs": checks.get("team_expected_minutes_gap_abs", 0.0)
                                  / max(checks.get("teams", 0), 1)}
    out["pit_audit_team_features"] = pit
    out["draws_per_game"] = sims
    out["population"] = {"games": int(len(gt)), "scored_player_games": int(len(meta)),
                         "candidate_rows_in_games": int(len(df)), "BASE_PPLAY_SCENARIO_GAMES": int(len(gt)),
                         "AVAILABILITY_OVERRIDE_EVALUATION_GAMES": 0,
                         "availability_note": "no historical pregame injury / status source exists (canonical "
                                              "availability.events has 0 rows); every game is a base-P(play) scenario"}
    return out


# ============================================================================ summaries
def _boot_weights(G: int) -> np.ndarray:
    return np.random.default_rng(EVAL_SEED).multinomial(G, np.full(G, 1.0 / G), size=EVAL_BOOT).astype(float)


def _ratio_ci(num, den, W, q=(0.025, 0.975)) -> dict:
    pt = float(num.sum() / den.sum())
    bs = (W @ num) / (W @ den)
    lo, hi = np.quantile(bs, q)
    return {"value": pt, "lo": float(lo), "hi": float(hi)}


def _game_sums(v: np.ndarray, gidx: np.ndarray, G: int, mask=None) -> tuple[np.ndarray, np.ndarray]:
    if mask is not None:
        v, gidx = v[mask], gidx[mask]
    return np.bincount(gidx, weights=v, minlength=G), np.bincount(gidx, minlength=G).astype(float)


def cell_arrays(wide: pd.DataFrame, a: str, gidx: np.ndarray, G: int, scale: dict, mask=None, stats=L.STATS) -> dict:
    """Per-game (numerator, denominator) of the cell-level metrics of arm a over the given rows and stats."""
    out = {}
    acc = {k: [np.zeros(G), np.zeros(G)] for k in ("MARG_LOG", "MARG_CRPS_SCALED", "MARG_BRIER", "MEAN_MSE_SCALED")}
    for s in stats:
        ok = np.ones(len(wide), bool) if mask is None else mask
        y = wide[f"y_{s}"].to_numpy(dtype=float)
        terms = {"MARG_LOG": wide[f"{a}|{s}|log"].to_numpy(dtype=float),
                 "MARG_CRPS_SCALED": wide[f"{a}|{s}|crps"].to_numpy(dtype=float) / scale[s],
                 "MARG_BRIER": np.mean([(wide[f"{a}|{s}|pover{li}"].to_numpy(dtype=float) - (y > HALF[s][li])) ** 2
                                        for li in range(3)], axis=0),
                 "MEAN_MSE_SCALED": ((wide[f"{a}|{s}|mean"].to_numpy(dtype=float) - y) / scale[s]) ** 2}
        for k, v in terms.items():
            nu, de = _game_sums(v, gidx, G, ok)
            acc[k][0] += nu
            acc[k][1] += de
            out[f"{k}|{s}"] = (nu, de)
    out.update({k: tuple(v) for k, v in acc.items()})
    return out


def minutes_arrays(wide, a, gidx, G, mask=None) -> dict:
    y = wide["y_min"].to_numpy(dtype=float)
    out = {}
    for k, v in (("MIN_CRPS", wide[f"{a}|min_crps"].to_numpy(dtype=float)),
                 ("MIN_LOG", wide[f"{a}|min_log"].to_numpy(dtype=float)),
                 ("MIN_AE_MEDIAN", np.abs(wide[f"{a}|min_median"].to_numpy(dtype=float) - y)),
                 ("MIN_AE_MEAN", np.abs(wide[f"{a}|min_mean"].to_numpy(dtype=float) - y)),
                 ("MIN_SE_MEAN", (wide[f"{a}|min_mean"].to_numpy(dtype=float) - y) ** 2),
                 ("MIN_BIAS", wide[f"{a}|min_mean"].to_numpy(dtype=float) - y)):
        out[k] = _game_sums(v, gidx, G, mask)
    return out


def joint_arrays(gt: pd.DataFrame, a: str) -> dict:
    one = np.ones(len(gt))
    npl = gt["n_players"].to_numpy(dtype=float)
    out = {k: (gt[f"{a}|{k}"].to_numpy(), one) for k in ("ES", "VS_BAL", "VS_WITHIN_PLAYER", "VS_SAME_TEAM", "VS_CROSS_TEAM",
                                                          "VS_UNIFORM")}
    out["VS_SHARED"] = (0.5 * (gt[f"{a}|VS_SAME_TEAM"] + gt[f"{a}|VS_CROSS_TEAM"]).to_numpy(), one)
    out["COMBO_LOG"] = (gt[f"{a}|COMBO_LOG"].to_numpy(), npl * len(J.COMBOS))
    out["EVENT_BRIER"] = (gt[f"{a}|EVENT_BRIER"].to_numpy(), gt["n_events"].to_numpy(dtype=float))
    out["EVENT_BRIER_SHARED"] = (gt[f"{a}|EVENT_BRIER_SHARED"].to_numpy(), gt["n_events_shared"].to_numpy(dtype=float))
    out["EVENT_LOG"] = (gt[f"{a}|EVENT_LOG"].to_numpy(), gt["n_events"].to_numpy(dtype=float))
    for cb in J.COMBOS:
        out[f"{cb}_LOG"] = (gt[f"{a}|{cb}_LOG"].to_numpy(), npl)
        out[f"{cb}_CRPS"] = (gt[f"{a}|{cb}_CRPS"].to_numpy(), npl)
        for q in J.COMBO_LINE_Q:
            out[f"{cb}_BRIER_q{int(q * 100)}"] = (gt[f"{a}|{cb}_BRIER_q{int(q * 100)}"].to_numpy(), npl)
    return out


def latent_scale(wide: pd.DataFrame, a: str, mask=None) -> dict:
    """Phase 2 diagnostic: realized / modeled latent spread lam = sqrt(E[(z - Ez - delta)^2] / E[Var z]) per stat."""
    out = {}
    for s in L.STATS:
        zc = wide[f"{a}|{s}|zc"].to_numpy(dtype=float)
        vz = wide[f"{a}|{s}|vz"].to_numpy(dtype=float)
        if mask is not None:
            zc, vz = zc[mask], vz[mask]
        d = zc.mean()
        out[s] = {"lam": float(np.sqrt(((zc - d) ** 2).mean() / vz.mean())), "delta": float(d), "n": int(len(zc))}
    return out


def pit_calibration(wide: pd.DataFrame, a: str, s: str) -> dict:
    """Randomized-PIT-free calibration: expected-coverage of central intervals from the PIT interval (mid-PIT
    histogram in deciles + coverage of the 50 / 80 / 95% central intervals)."""
    lo = wide[f"{a}|{s}|pit_lo"].to_numpy(dtype=float)
    hi = wide[f"{a}|{s}|pit_hi"].to_numpy(dtype=float)
    mid = 0.5 * (lo + hi)
    hist = np.histogram(mid, bins=np.linspace(0, 1, 11))[0] / len(mid)
    cov = {}
    for lev in (0.5, 0.8, 0.95):
        a_, b_ = (1 - lev) / 2, 1 - (1 - lev) / 2
        # probability mass of the observed cell inside [a, b] (uniform within the PIT interval)
        w = np.clip((np.minimum(hi, b_) - np.maximum(lo, a_)) / np.maximum(hi - lo, 1e-12), 0, 1)
        cov[str(lev)] = float(w.mean())
    return {"mid_pit_decile_shares": hist.tolist(), "central_coverage": cov}


def summarize(wide, gt, ev, om, sim_m, pairs_n, arms, df, sims, train_scale) -> dict:
    G = len(gt)
    gidx = pd.Index(gt["game_id"]).get_indexer(wide["game_id"])
    W = _boot_weights(G)
    scale = dict(zip(L.STATS, np.asarray(train_scale, dtype=float).tolist()))  # training scale (Phase 2 fit record)
    role = wide["role_diagnostic"].to_numpy()
    pos = wide["position_group"].to_numpy()
    cold = wide["cold_start_class"].fillna("UNKNOWN").to_numpy()
    rc = role_change_flags(wide, df)
    strata = {"ALL": None, **{f"ROLE:{r}": role == r for r in ROT_GROUPS + ("NO_ROLE_HISTORY",)},
              **{f"POS:{p}": pos == p for p in ("G", "F", "C", "U")},
              "COLD_START": np.isin(cold, ("COLD_NO_HISTORY", "COLD_EARLY_SEASON")),
              "TRANSFER": cold == "COLD_TRANSFER_IN", "RETURNER": cold == "WARM",
              "ROTATION_OR_BENCH": np.isin(role, ("ROTATION_6_8", "BENCH_9PLUS")),
              "PREGAME_EXPECTED_ROTATION_CHANGE": rc["pregame"], "REALIZED_TOP5_ABSENCE_DIAGNOSTIC": rc["realized"]}
    strata = {k: v for k, v in strata.items() if v is None or v.any()}          # empty strata are not reported
    cells = {st: {a: cell_arrays(wide, a, gidx, G, scale, m) for a in arms} for st, m in strata.items()}
    mins = {st: {a: minutes_arrays(wide, a, gidx, G, m) for a in arms} for st, m in strata.items()
            if st in ("ALL", "ROLE:TOP5_MINUTES", "ROLE:ROTATION_6_8", "ROLE:BENCH_9PLUS", "ROLE:NO_ROLE_HISTORY",
                      "POS:G", "POS:F", "POS:C", "POS:U", "PREGAME_EXPECTED_ROTATION_CHANGE",
                      "REALIZED_TOP5_ABSENCE_DIAGNOSTIC")}
    jo = {a: joint_arrays(gt, a) for a in arms}
    levels, deltas = {}, {}
    pairs = [(a, "S0") for a in arms if a != "S0"] + [("S2", "S1"), ("S3", "S2"), ("S4_A10", "S3"), ("S4_A05", "S3"),
                                                       ("S4_A05", "S4_A10")]
    pairs = [p for p in pairs if p[0] in arms and p[1] in arms]
    for st in cells:
        levels[st] = {a: {k: _ratio_ci(*v, W) for k, v in cells[st][a].items()} for a in arms}
        for a, b in pairs:
            deltas.setdefault(f"{a}-{b}", {})[st] = {k: _ratio_ci(cells[st][a][k][0] - cells[st][b][k][0], cells[st][a][k][1], W)
                                                     for k in cells[st][a]}
    min_levels, min_deltas = {}, {}
    for st in mins:
        min_levels[st] = {a: {k: _ratio_ci(*v, W) for k, v in mins[st][a].items()} | {"RMSE": None} for a in arms}
        for a in arms:
            min_levels[st][a]["RMSE"] = float(np.sqrt(min_levels[st][a]["MIN_SE_MEAN"]["value"]))
        for a, b in pairs:
            min_deltas.setdefault(f"{a}-{b}", {})[st] = {k: _ratio_ci(mins[st][a][k][0] - mins[st][b][k][0],
                                                                      mins[st][a][k][1], W) for k in mins[st][a]}
    jl = {a: {k: _ratio_ci(*v, W) for k, v in jo[a].items()} for a in arms}
    jd = {f"{a}-{b}": {k: _ratio_ci(jo[a][k][0] - jo[b][k][0], jo[a][k][1], W) for k in jo[a]} for a, b in pairs}
    mc = {}
    for a, b in pairs:
        for m, ka, kb in (("ES", "ES_rep_a", "ES_rep_b"), ("VS_BAL", "VS_rep_a", "VS_rep_b")):
            dg = (gt[f"{a}|{ka}"] - gt[f"{b}|{ka}"]) - (gt[f"{a}|{kb}"] - gt[f"{b}|{kb}"])
            mc[f"{a}-{b}|{m}"] = float(dg.std(ddof=1) / np.sqrt(G) / 2)
    lam = {st: {a: latent_scale(wide, a, m) for a in arms} for st, m in strata.items() if st in
           ("ALL", "ROLE:TOP5_MINUTES", "ROLE:ROTATION_6_8", "ROLE:BENCH_9PLUS", "ROLE:NO_ROLE_HISTORY")}
    calib = {a: {s: pit_calibration(wide, a, s) for s in L.STATS} for a in arms}
    min_cal = {a: minutes_calibration(wide, a) for a in arms}
    return {"games": G, "player_games": int(len(wide)), "levels": levels, "deltas": deltas,
            "minutes_levels": min_levels, "minutes_deltas": min_deltas, "joint_levels": jl, "joint_deltas": jd,
            "mc_se": mc, "latent_scale_lam": lam, "pit_calibration": calib, "minutes_calibration": min_cal,
            "events_by_family": event_families(ev, arms, W, gt, sims),
            "correlation_calibration": correlation_calibration(om, sim_m, pairs_n, arms),
            "strata_sizes": {st: int(len(wide) if m is None else m.sum()) for st, m in strata.items()},
            "descriptive_scale": scale, "participation": participation_metrics(df)}


def role_change_flags(wide: pd.DataFrame, df: pd.DataFrame) -> dict:
    """PREGAME: a TOP5_MINUTES teammate (pregame role) has P(play) < 0.5. REALIZED (diagnostic stratum only, defined
    by outcomes, never a model input): a TOP5_MINUTES teammate did not play."""
    t5 = df[df["role_diagnostic"].eq("TOP5_MINUTES")]
    pre = t5.assign(m=t5["p_play"] < 0.5).groupby(["game_id", "team_id"])["m"].any()
    rea = t5.assign(m=~t5["participation_status"].eq("PLAYED")).groupby(["game_id", "team_id"])["m"].any()
    k = pd.MultiIndex.from_frame(wide[["game_id", "team_id"]])
    return {"pregame": pre.reindex(k).fillna(False).to_numpy(bool), "realized": rea.reindex(k).fillna(False).to_numpy(bool)}


def minutes_calibration(wide: pd.DataFrame, a: str) -> dict:
    y = np.round(wide["y_min"].to_numpy(dtype=float))
    out = {}
    for lev, (ql, qh) in {"0.5": ("min_q25", "min_q75"), "0.8": ("min_q10", "min_q90"), "0.9": ("min_q05", "min_q95")}.items():
        out[f"coverage_{lev}"] = float(((y >= wide[f"{a}|{ql}"]) & (y <= wide[f"{a}|{qh}"])).mean())
    mid = 0.5 * (wide[f"{a}|min_pit_lo"].to_numpy(dtype=float) + wide[f"{a}|min_pit_hi"].to_numpy(dtype=float))
    out["mid_pit_decile_shares"] = (np.histogram(mid, bins=np.linspace(0, 1, 11))[0] / len(mid)).tolist()
    return out


def participation_metrics(df: pd.DataFrame) -> dict:
    """The unchanged Phase 1B P(play) on every identifiable candidate of the evaluated games (shared by all arms)."""
    lab = df[df["participation_status"].isin(("PLAYED", "DNP_CONFIRMED"))]
    y = lab["participation_status"].eq("PLAYED").to_numpy(dtype=float)
    p = np.clip(lab["p_play"].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    return {"n": int(len(y)), **Q.metrics(y, p), "note": "P(play) is the unchanged Phase 1B participation model; "
            "Phase 3 consumes it as the base availability scenario"}


def event_families(ev: pd.DataFrame, arms, W, gt, sims) -> dict:
    gidx = {g: i for i, g in enumerate(gt["game_id"])}
    ev = ev.assign(gi=ev["game_id"].map(gidx))
    G = len(gt)
    out = {}
    fl = 0.5 / sims
    for fam, e in ev.groupby("event", sort=True):
        o = e["outcome"].to_numpy(dtype=float)
        cnt = np.bincount(e["gi"], minlength=G).astype(float)
        rec = {"n": int(len(e)), "observed_rate": float(o.mean())}
        for a in arms:
            p = e[f"p_{a}"].to_numpy()
            rec[a] = {"mean_p": float(p.mean()), "brier": float(((p - o) ** 2).mean()),
                      "log": float(-(o * np.log(np.clip(p, fl, 1)) + (1 - o) * np.log(np.clip(1 - p, fl, 1))).mean()),
                      "calibration_gap_obs_minus_pred": float(o.mean() - p.mean())}
            if a != "S0":
                dn = np.bincount(e["gi"], weights=(p - o) ** 2 - (e["p_S0"].to_numpy() - o) ** 2, minlength=G)
                rec[a]["brier_minus_S0"] = _ratio_ci(dn, cnt, W)
        out[fam] = rec
    return out


def correlation_calibration(om, sim_m, pairs_n, arms) -> dict:
    """Pooled pair correlations of standardized count residuals (each arm standardized by its own predictive mean / SD),
    observed vs simulated, per pair type (RMSE over stat pairs, regression slope)."""
    pairs = pairs_n.sum(axis=0)
    out = {}
    for a in arms:
        obs = om[a].sum(axis=0)
        rec = {}
        for k, t in enumerate(("within_player", "same_team", "cross_team")):
            oc = obs[k] / pairs[k]
            sc = sim_m[a][k] / pairs[k]
            cells = np.triu_indices(J.NS, 1) if t == "within_player" else np.triu_indices(J.NS)
            diff = (oc - sc)[cells]
            x = sc[cells]
            rec[t] = {"rmse": float(np.sqrt((diff ** 2).mean())),
                      "slope": float((x * oc[cells]).sum() / (x * x).sum()) if (x * x).sum() > 0 else None,
                      "observed": oc.tolist(), "simulated": sc.tolist()}
        out[a] = rec
    return out


# ============================================================================ decision (frozen in the protocol)
def _sig_better(d) -> bool:
    return d["hi"] < 0


def _sig_worse(d) -> bool:
    return d["lo"] > 0


def layer_gates(summ: dict) -> dict:
    dl, jd, md = summ["deltas"], summ["joint_deltas"], summ["minutes_deltas"]
    acc = summ["accounting"]
    g1 = {"improves_shared_joint": {k: _sig_better(jd["S1-S0"][k]) for k in ("ES", "VS_SHARED", "EVENT_BRIER_SHARED")},
          "mean_mse_not_significantly_worse": not _sig_worse(dl["S1-S0"]["ALL"]["MEAN_MSE_SCALED"])}
    g1["ACCEPT"] = bool(any(g1["improves_shared_joint"].values()) and g1["mean_mse_not_significantly_worse"])
    g2 = {"team_minutes_constraint_violations": acc["TEAM_MINUTES_CONSTRAINT_VIOLATIONS"],
          "min_crps_not_significantly_worse_than_phase1b": not _sig_worse(md["S2-S1"]["ALL"]["MIN_CRPS"]),
          "improves": {"MIN_CRPS": _sig_better(md["S2-S1"]["ALL"]["MIN_CRPS"]),
                       "MARG_LOG_ROTATION_OR_BENCH": _sig_better(dl["S2-S1"]["ROTATION_OR_BENCH"]["MARG_LOG"]),
                       "ES": _sig_better(jd["S2-S1"]["ES"])}}
    g2["ACCEPT"] = bool(g2["team_minutes_constraint_violations"] == 0 and g2["min_crps_not_significantly_worse_than_phase1b"]
                        and any(g2["improves"].values()))
    g3 = {"improves": {k: _sig_better(dl["S3-S2"]["ALL"][k]) for k in ("MEAN_MSE_SCALED", "MARG_LOG")},
          "significantly_worse": {k: _sig_worse(dl["S3-S2"]["ALL"][k]) for k in ("MEAN_MSE_SCALED", "MARG_LOG")}}
    g3["ACCEPT"] = bool(any(g3["improves"].values()) and not any(g3["significantly_worse"].values()))
    return {"S1": g1, "S2": g2, "S3": g3}


def select_alpha(summ: dict) -> dict:
    v = {a: summ["levels"]["ALL"][a]["MARG_LOG"]["value"] for a in ALPHAS}
    best = min(sorted(v), key=lambda a: (v[a], -ALPHAS[a]))
    return {"MARG_LOG": v, "selected_arm": best, "alpha": ALPHAS[best]}


def acceptance(summ: dict, cand: str) -> dict:
    """The frozen Phase 3 acceptance rule for candidate `cand` against S0."""
    d = summ["deltas"][f"{cand}-S0"]
    jd = summ["joint_deltas"][f"{cand}-S0"]
    md = summ["minutes_deltas"][f"{cand}-S0"]
    lam = summ["latent_scale_lam"]
    ev = summ["events_by_family"].get("TM_SCORER_SCORER", {})
    acc = summ["accounting"]
    agg = d["ALL"]["MARG_LOG"]
    tgt = d["ROTATION_OR_BENCH"]["MARG_LOG"]
    out = {"A1_marginal": {"aggregate": agg, "targeted_rotation_or_bench": tgt,
                           "PASS": bool(_sig_better(agg) or (_sig_better(tgt) and agg["value"] <= AGG_TOLERANCE_NATS))},
           "A2_no_stat_materially_degraded": {s: d["ALL"][f"MARG_LOG|{s}"] for s in L.STATS}}
    out["A2_no_stat_materially_degraded"]["PASS"] = not any(
        v["value"] > STAT_DEGRADE_NATS and v["lo"] > 0 for k, v in out["A2_no_stat_materially_degraded"].items() if k in L.STATS)
    out["A3_minutes"] = {"TEAM_MINUTES_CONSTRAINT_VIOLATIONS": acc["TEAM_MINUTES_CONSTRAINT_VIOLATIONS"],
                         "MIN_CRPS_delta_vs_phase1b": md["ALL"]["MIN_CRPS"],
                         "PASS": bool(acc["TEAM_MINUTES_CONSTRAINT_VIOLATIONS"] == 0 and not _sig_worse(md["ALL"]["MIN_CRPS"]))}
    pts = {r: {"S0": lam[f"ROLE:{r}"]["S0"]["PTS"]["lam"], cand: lam[f"ROLE:{r}"][cand]["PTS"]["lam"]}
           for r in ("BENCH_9PLUS", "ROTATION_6_8", "TOP5_MINUTES")}
    out["A4_bench_rotation_uncertainty"] = {"PTS_lam": pts, "PASS": bool(all(
        abs(np.log(pts[r][cand])) < abs(np.log(pts[r]["S0"])) for r in ("BENCH_9PLUS", "ROTATION_6_8")))}
    out["A5_joint_scoring"] = {"ES": jd["ES"], "VS_BAL": jd["VS_BAL"],
                               "PASS": bool(not _sig_worse(jd["ES"]) and not _sig_worse(jd["VS_BAL"]))}
    if ev:
        gap0, gap1 = abs(ev["S0"]["calibration_gap_obs_minus_pred"]), abs(ev[cand]["calibration_gap_obs_minus_pred"])
        out["A6_both_top_scorers_over"] = {"observed": ev["observed_rate"], "S0_mean_p": ev["S0"]["mean_p"],
                                           f"{cand}_mean_p": ev[cand]["mean_p"], "brier_minus_S0": ev[cand]["brier_minus_S0"],
                                           "PASS": bool(ev[cand]["brier_minus_S0"]["value"] < 0 and gap1 < gap0)}
    else:
        out["A6_both_top_scorers_over"] = {"PASS": False, "note": "event family missing"}
    out["A7_pit"] = {"GENUINE_PIT_VIOLATIONS": summ["pit_audit_team_features"]["violations"],
                     "PASS": summ["pit_audit_team_features"]["violations"] == 0}
    out["A8_no_2026_27"] = {"PASS": True, "enforced_by": "structural.check_seasons / marginal.check_seasons guards"}
    out["A9_deterministic"] = {"PASS": bool(summ.get("determinism", {}).get("identical", False)),
                               "check": summ.get("determinism")}
    out["matrices"] = {"INVALID_CORRELATION_MATRICES": acc["INVALID_CORRELATION_MATRICES"].get(cand, 0)}
    out["ALL_PASS"] = bool(all(v["PASS"] for k, v in out.items() if k[:1] == "A" and k[1:2].isdigit())
                           and out["matrices"]["INVALID_CORRELATION_MATRICES"] == 0)
    return out


def mc_adequacy(summ: dict, pairs) -> dict:
    out = {}
    for a, b in pairs:
        for m in ("ES", "VS_BAL"):
            se = summ["mc_se"].get(f"{a}-{b}|{m}")
            v = summ["joint_deltas"][f"{a}-{b}"][m]
            bound = v["hi"] if v["value"] < 0 else v["lo"]
            out[f"{a}-{b}|{m}"] = {"delta": v["value"], "decisive_bound": bound, "mc_se": se,
                                   "bound_to_mc_se": abs(bound) / se if se else None}
    out["MC_ADEQUATE"] = bool(all(x["bound_to_mc_se"] is None or x["bound_to_mc_se"] >= MC_DECISION_RATIO
                                  for k, x in out.items() if isinstance(x, dict)))
    return out


def determinism(F, struct, s4, s0_struct, scale, n_games=2, arms=ARMS) -> dict:
    """Re-simulate the first games of the season twice: every arm's draws must be bit-identical."""
    df, envp = season_inputs(F, struct)
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    s4s = {a: D.structure("J2", s4["residual_dependence"]) for a in ALPHAS}
    hs = []
    for _ in range(2):
        h = hashlib.sha256()
        for gid, gp in list(df.groupby("game_id", sort=True))[:n_games]:
            r = eval_game(gid, gp, envd, struct, s4, s0_struct, s4s, scale, sims=5000, arms=arms)
            for a in arms:
                h.update(np.ascontiguousarray(r["single"][a]["log"]).tobytes())
                h.update(json.dumps(r["joint"], sort_keys=True, default=_jd).encode())
        hs.append(h.hexdigest())
    return {"games": n_games, "draws": 5000, "hashes": hs, "identical": hs[0] == hs[1]}


# ============================================================================ protocol
def protocol() -> dict:
    return {
        "version": VERSION, "phase": "V2 PHASE 3: STRUCTURAL INTERDEPENDENCE (SHARED GAME ENVIRONMENT + JOINT MINUTES + "
                                     "OPPORTUNITY REDISTRIBUTION)",
        "frozen_before_any_candidate_evaluation": True,
        "pre_freeze_development_disclosure": (
            "Design prototypes before this freeze used: team-game data 2013-14..2024-25 for the environment OLS shape "
            "(R2 / residual SDs); Phase 1B OOS rows of 2023-24 and 2024-25 for GLM coefficient magnitudes / stability; "
            "minutes of a 2,000-team-game subset of 2024-25 for the allocator form (kappa in {0, 0.75, 1}, anchored vs "
            "not). No Phase 3 candidate (S1..S4) was scored on any season; nothing of 2025-26 was inspected. kappa = 1 "
            "is declared as Phase 1B's own minutes SD, not tuned."),
        "inputs": {"phase1b_oos_store": _rel(P2.STORE), "phase1b_oos_store_sha256": STORE_SHA,
                   "phase1b_oos_extension_F2023": _rel(PP.STORE_PATH), "F2023_sha256": F2023_SHA,
                   "F2023_manifest": _rel(PP.MANIFEST_PATH),
                   "MARGINAL_ARTIFACT_SHA256": MARGINAL_SHA, "PHASE2_DEPENDENCE_ARTIFACT_SHA256": J2_SHA,
                   "v1_artifact_sha256": V1_SHA,
                   "team_data": "Stage 3 lockbox store feat.team_features (pregame) + lab.team_game_labels (training labels)",
                   "S0_dependence": {"selection": _rel(PHASE2_FITS["select"]), "confirmation": _rel(PHASE2_FITS["confirm"]),
                                     "final": _rel(J2_PATH)},
                   "HISTORICAL_API_CALLS": 0},
        "candidate_ladder": {
            "S0": "Phase 2 benchmark: Phase 1B marginals + J2 (the Phase 2 fit of the same chronology)",
            "S1": "S0 + shared game environment: log mu += gamma_s . (pace_dev, own_eff_dev, opp_eff_dev); pregame mean "
                  "deviations + simulated shared shocks; Phase 1B dispersion; Phase 2 J2 copula",
            "S2": "S1 + joint constrained minutes (anchored logit-shift allocation under the base P(play) scenario) + "
                  "c_s + beta_s log(max(M,1)/min_mu)",
            "S3": "S2 + opportunity redistribution: + theta_s log(U_ref,f / U_f) for the stat's family f",
            "S4": "S3 + refitted conditional dispersion + refitted residual J2 (same architecture) + mean anchor alpha",
            "alpha_grid": ALPHAS, "no_other_major_component": True},
        "chronology": {"selection": {"structure_through": SELECT["T"], "evaluate": SELECT["eval"],
                                     "S4_components_from_true_OOS_structural_residuals_of": list(SELECT["resid_seasons"])},
                       "retrospective_confirmation": {"structure_through": CONFIRM["T"], "evaluate": CONFIRM["eval"],
                                                      "S4_components_from": list(CONFIRM["resid_seasons"]),
                                                      "runs": "exactly once; the selection is not changed by it"},
                       "final": {"structure_through": FINAL["T"], "S4_components_from": list(FINAL["resid_seasons"])},
                       "true_OOS_structural_residuals": "season F residuals use structural parameters fitted through F-1 "
                                                        "(F2024 uses parameters from F2023 = 2022-23 Phase 1B OOS rows)"},
        "feature_whitelist": {
            "game_environment": ["te_pace_per40", "te_off_eff", "te_def_eff", "te_games_boxed", "tp_prev_off_eff",
                                 "tp_prev_def_eff", "is_home", "neutral_site (team + opponent rows of feat.team_features)"],
            "player": ["Phase 1B min_mu, min_sigma, {stat}_mu (per-minute family rates = mu / min_mu), {stat}_phi",
                       "Phase 1B P(play)", "role_diagnostic / position_group (J2 groups, diagnostics)"],
            "never_inputs": ["sportsbook spread / total / moneyline / prop lines", "target-game realized pace, "
                             "efficiency, minutes, participation (training labels only)"]},
        "shared_game_variables": {
            "expected_pace": "OLS of realized log(poss * 40 / game_minutes) on [1, own log pace, opp log pace, own log "
                             "off eff, opp log def eff, home, away]; team pace / efficiency = prior-game values shrunk "
                             "toward the prior season (efficiency) or the training league mean with %g / %g pseudo-games;"
                             " game-level expectation = mean of the two team predictions" % (S.SHRINK_GAMES_PACE, S.SHRINK_GAMES_EFF),
            "expected_efficiency": "same design for realized log(points / possession) per team",
            "shocks": "shared game pace eps_g ~ N(0, sd_pace) (game-level residual SD); team efficiency (eps_0, eps_1) "
                      "~ BVN(0, sd_eff, rho_eff) (training residuals)",
            "duration": "D = 40 + 5 K, K from the training overtime frequencies; one duration per draw for both teams"},
        "minutes_allocation": {
            "method": "anchored logit-shift: s_i = sigmoid(a_i + kappa sd_i xi_i + delta_d), sum_active s = 5, "
                      "M_i = 5 D s_i / sum s (exact team total, 0 <= M <= D, inactive 0); a_i = logit(min_mu/40) "
                      "calibrated (%d fixed-point steps on the first %d draws) so E[M_i | i active] = min_mu_i under "
                      "the base scenario; sd_i = min(min_sigma / (40 p (1 - p)), %g); kappa = %g (declared)"
                      % (S.CALIB_ITERS, S.CALIB_DRAWS, S.LOGIT_SD_MAX, S.KAPPA),
            "fewer_than_5_active": "activate the highest-P(play) inactive candidates (counted)",
            "players": "every rostered candidate of the team (bench included)"},
        "opportunity_families": {f: list(v) for f, v in S.FAMILIES.items()} | {
            "family_rate": S.FAMILY_RATE_STAT,
            "budget_share": "U_f = sum_active M_j rate_jf; share_i = M_i rate_if / U_f; team budget B_f = U_ref^theta "
                            "U_f^(1 - theta); U_ref = base-scenario expectation of U_f (training: team-season mean)",
            "no_named_player_or_pair_parameters": True},
        "conditional_mean_glm": {"terms": list(S.GLM_TERMS), "fit": "Poisson pseudo-likelihood with offset log mu_1B, "
                                 "ridge %g x n on non-intercept terms, per stat, on Phase 1B OOS rows 2022-23..T given "
                                 "the realized state" % S.GLM_RIDGE, "layer_terms": {k: list(v) for k, v in S.LAYER_TERMS.items()}},
        "availability_policy": {
            "evaluation_scenario": "BASE_PPLAY: every candidate other than the scored player is active with its "
                                   "pregame Phase 1B P(play); a scored player's single-player quantities are "
                                   "conditional on him playing (forced active in the same draw)",
            "historical_overrides": "none: no historical pregame status source exists (availability.events = 0 rows); "
                                    "zero minutes is never treated as a known OUT",
            "production_interface": "ACTIVE 1, OUT 0, EXPECTED_ACTIVE max(model, 0.9), QUESTIONABLE / UNCERTAIN "
                                    "supplied probability (default 0.5), none -> P(play); validated by invariants "
                                    "only (prospective validation in 2026-27)"},
        "dispersion_refit_policy": "S1-S3 keep the Phase 1B dispersion; S4: one phi per stat by method of moments on "
                                   "true-OOS structural residuals given the realized state; Phase 1B families kept (STL "
                                   "stays NB, TOV stays Poisson)",
        "residual_dependence_policy": "S1-S3 use the Phase 2 J2 of the same chronology; S4 refits the same J2 "
                                      "architecture (dependence.fit, MID) on the conditional PMFs of the true-OOS "
                                      "structural residual basis",
        "monte_carlo_policy": {"draws_per_game": SIMS, "rerun_draws": 50_000,
                               "rule": "if a decisive ES / VS_BAL interval bound of the selection lies within %.0f MC SE "
                                       "of zero, the affected comparison is rerun with 50,000 draws" % MC_DECISION_RATIO,
                               "marginals": "Rao-Blackwellized: the predictive PMF is the mixture of conditional PMFs "
                                            "over the draws (S0 exact)",
                               "seeds": "J2 latent bank BLAKE2b(20263|game) as Phase 2 (common across arms); structural "
                                        "streams BLAKE2b(%d|stream|game)" % S.SEED_BASE},
        "metrics": {"marginal": ["MARG_LOG", "MARG_CRPS_SCALED", "MARG_BRIER (half lines %s)" % HALF, "MEAN_MSE_SCALED",
                                 "bias", "PIT coverage / mid-PIT deciles"],
                    "minutes": ["MIN_CRPS", "MIN_LOG", "MAE (median, mean)", "RMSE", "bias", "coverage"],
                    "participation": "Brier / log loss / calibration of the unchanged P(play)",
                    "joint": ["ES", "VS_BAL (+ within / same-team / cross-team / VS_SHARED)", "COMBO_LOG + per combo",
                              "EVENT_BRIER (+ EVENT_BRIER_SHARED over %s)" % list(SHARED_EVENTS), "correlation calibration"],
                    "strata": ["stat", "role", "position", "role x stat", "position x stat", "cold start / transfer / "
                               "returner", "PREGAME_EXPECTED_ROTATION_CHANGE", "REALIZED_TOP5_ABSENCE_DIAGNOSTIC"],
                    "uncertainty": "game-clustered bootstrap, %d draws, seed %d" % (EVAL_BOOT, EVAL_SEED),
                    "diagnostic_not_objective": "latent spread ratio lam per role (Phase 2 1.39 / 1.28 / 1.05)"},
        "selection_rule": {
            "layer_gates": {
                "S1_SHARED_GAME_ENVIRONMENT": "ACCEPT iff one of ES, VS_SHARED, EVENT_BRIER_SHARED improves vs S0 "
                                              "(95% CI upper < 0) and MEAN_MSE_SCALED is not significantly worse",
                "S2_JOINT_MINUTES": "ACCEPT iff 0 team-minutes violations, MIN_CRPS not significantly worse than Phase 1B "
                                    "(S1), and one of MIN_CRPS, MARG_LOG on ROTATION_OR_BENCH cells, ES improves vs S1",
                "S3_OPPORTUNITY_REDISTRIBUTION": "ACCEPT iff MEAN_MSE_SCALED or MARG_LOG improves vs S2 and neither is "
                                                 "significantly worse"},
            "alpha": "alpha* = argmin MARG_LOG over S4(0.5), S4(1.0) on 2024-25 (tie -> 1.0)",
            "rejected_layer": "if any layer is rejected, the S4 candidate is rebuilt from the accepted layers only "
                              "(GLM refit on those terms, S4 components refit) and evaluated once more on 2024-25",
            "acceptance_vs_S0": {
                "A1": "MARG_LOG improves (CI upper < 0), or ROTATION_OR_BENCH MARG_LOG improves (CI upper < 0) with "
                      "aggregate delta <= +%g nats" % AGG_TOLERANCE_NATS,
                "A2": "no stat with MARG_LOG delta > +%g nats and CI lower > 0" % STAT_DEGRADE_NATS,
                "A3": "TEAM_MINUTES_CONSTRAINT_VIOLATIONS = 0 and MIN_CRPS not significantly worse than Phase 1B",
                "A4": "PTS lam for BENCH_9PLUS and ROTATION_6_8 both closer to 1 (|log lam|) than S0",
                "A5": "ES and VS_BAL not significantly worse than S0",
                "A6": "TM_SCORER_SCORER (both top scorers over): Brier below S0 and |observed - mean predicted| smaller",
                "A7": "GENUINE_PIT_VIOLATIONS = 0", "A8": "no 2026-27 outcome", "A9": "deterministic re-simulation",
                "matrices": "0 invalid correlation matrices"},
            "selected": "the S4 candidate (alpha*) if every acceptance condition holds; otherwise S0 (Phase 2 J2 kept; "
                        "Phase 3 not promoted)"},
        "accounting_tests": ["team minutes = 5 D (|diff| <= 1e-9) in every team draw", "0 <= M <= D", "inactive = 0",
                             "shares >= 0 and sum to 1 per family", "combo identities exact", "constraints are built "
                             "into generation (no post-hoc repair)"],
        "code_sha256_at_freeze": code_state(),
    }


def stage_protocol() -> dict:
    if PROTOCOL_PATH.exists():
        raise RuntimeError("the Phase 3 protocol is frozen; it is never rewritten")
    for p in (SELECTION_PATH, CONFIRM_PATH, FINAL_PATH):
        if p.exists():
            raise RuntimeError(f"{p.name} exists before the protocol")
    g = guards()
    out = protocol()
    out["guards_at_freeze"] = g
    _dump(PROTOCOL_PATH, out)
    return out


# ============================================================================ stages
def _s0_structure(stage: str):
    if stage == "final":
        return P2.load_artifact(J2_PATH)
    fit = json.loads(PHASE2_FITS[stage].read_text())
    return D.structure("J2", fit["MID"]), np.asarray([fit["scale"][s] for s in L.STATS])


def build_procedure(T: int, resid_seasons, log=print, terms=S.GLM_TERMS) -> tuple[dict, dict, pd.DataFrame, dict]:
    """The frozen procedure through T: structural parameters P_T, true-OOS structural residual basis of resid_seasons
    (season F with P_{F-1}), and the S4 components fitted on that basis."""
    struct = fit_structure(T, terms)
    parts, accs, structs_used = [], {}, {}
    for F in resid_seasons:
        prev = struct if F - 1 == T else fit_structure(F - 1, terms)
        log(f"structural residual basis F{F} from parameters through {F - 1}")
        r, a = residual_rows(F, prev)
        parts.append(r)
        accs[F] = a
        structs_used[F] = F - 1
    resid = pd.concat(parts, ignore_index=True)
    log(f"S4 components on {len(resid)} residual rows")
    s4 = fit_s4(resid, structs_used)
    s4["residual_basis_population"] = accs
    return struct, s4, resid, accs


def _compact_struct(struct: dict) -> dict:
    return {k: struct[k] for k in ("T", "env", "glm", "glm_population", "pit_audit", "terms")}


def _compact_s4(s4: dict) -> dict:
    out = dict(s4)
    dep = s4["residual_dependence"]
    out["residual_dependence"] = {k: dep[k] for k in ("pit", "fit_seasons", "fit_player_games", "fit_games", "normalization",
                                                       "within_player", "same_team", "cross_team", "shared", "psd_repairs_at_fit")}
    return out


def j2_comparison(old: dict, new: dict) -> dict:
    """Old Phase 2 J2 (relative to the Phase 1B marginals) vs the residual J2 after structure."""
    out = {}
    iu = np.triu_indices(J.NS, 1)
    ju = np.triu_indices(J.NS)
    Ro, Rn = np.asarray(old["within_player"]["global"]["R"]), np.asarray(new["within_player"]["global"]["R"])
    out["within_player_global"] = {"pairs": {f"{L.STATS[i]}-{L.STATS[j]}": [float(Ro[i, j]), float(Rn[i, j])] for i, j in zip(*iu)},
                                   "mean_abs_old": float(np.abs(Ro[iu]).mean()), "mean_abs_new": float(np.abs(Rn[iu]).mean())}
    for comp in ("same_team", "cross_team"):
        o, n_ = np.asarray(old[comp]["thresholded"]), np.asarray(new[comp]["thresholded"])
        out[comp] = {"pairs": {f"{L.STATS[i]}-{L.STATS[j]}": [float(o[i, j]), float(n_[i, j])] for i, j in zip(*ju)
                               if o[i, j] != 0 or n_[i, j] != 0},
                     "mean_abs_old": float(np.abs(o[ju]).mean()), "mean_abs_new": float(np.abs(n_[ju]).mean()),
                     "nonzero_old": int((o[ju] != 0).sum()), "nonzero_new": int((n_[ju] != 0).sum())}
    return out


def stage_select(log=print, limit_games=None) -> dict:
    if SELECTION_PATH.exists():
        raise RuntimeError("the selection record exists; the selection stage does not rerun")
    prot_commit = M.committed_unchanged(PROTOCOL_PATH)
    g = guards()
    T, F = SELECT["T"], SELECT["eval"]
    struct, s4, resid, accs = build_procedure(T, SELECT["resid_seasons"], log)
    if max(resid["forecast_season"]) >= F:
        raise RuntimeError("an evaluation-season outcome entered the S4 fit")
    s0, scale = _s0_structure("select")
    log(f"evaluating S0..S4 on F{F}")
    summ = evaluate(F, struct, s4, s0, scale, f"select_{F}", log, limit_games=limit_games)
    summ["determinism"] = determinism(F, struct, s4, s0, scale)
    gates = layer_gates(summ)
    alpha = select_alpha(summ)
    cand = alpha["selected_arm"]
    acc = acceptance(summ, cand)
    rejected = [LAYER_OF[k] for k, v in gates.items() if not v["ACCEPT"]]
    mc = mc_adequacy(summ, [(a, "S0") for a in ARMS if a != "S0"] + [("S2", "S1"), ("S3", "S2")])
    rebuild = None
    if rejected:
        rebuild = {"rejected_layers": rejected, "note": "frozen rule: rebuild S4 from the accepted layers and evaluate on "
                                                        "2024-25 (stage select_rebuild)"}
    sel_model = (cand if acc["ALL_PASS"] and not rejected else ("PENDING_REBUILD" if rejected else "S0"))
    out = {"version": VERSION, "stage": "SELECTION", "protocol_sha256": _sha(PROTOCOL_PATH), "protocol_commit": prot_commit,
           "structure_through": T, "evaluation_season": F, "season_2025_26_loaded": False, "guards": g,
           "code": code_vs_protocol(), "structure": _compact_struct(struct), "s4_components": _compact_s4(s4),
           "old_J2_vs_residual_J2": j2_comparison(json.loads(PHASE2_FITS["select"].read_text())["MID"], s4["residual_dependence"]),
           "summary": summ, "layer_gates": gates, "alpha_selection": alpha, "acceptance": acc, "mc_adequacy": mc,
           "rebuild": rebuild, "SELECTED_PHASE3_MODEL": sel_model,
           "SELECTED_ARCHITECTURE": {"layers": [LAYER_OF[k] for k in ("S1", "S2", "S3") if gates[k]["ACCEPT"]],
                                     "alpha": alpha["alpha"], "candidate_arm": cand},
           "PROCEDURE_FROZEN_BEFORE_2025_26": True}
    resid_path = SCRATCH / "select_residual_basis.parquet"
    resid.to_parquet(resid_path, index=False)
    _dump(SELECTION_PATH, out)
    return out


def accepted_terms(layers) -> tuple:
    """GLM terms of the accepted layers (opportunity redistribution requires the joint-minutes layer)."""
    t = set()
    if "SHARED_GAME_ENVIRONMENT" in layers:
        t |= {"pace_dev", "own_eff_dev", "opp_eff_dev"}
    if "JOINT_MINUTES" in layers:
        t |= {"intercept", "log_minutes_ratio"}
        if "OPPORTUNITY_REDISTRIBUTION" in layers:
            t.add("opportunity_deficit")
    return tuple(x for x in S.GLM_TERMS if x in t)


def _selected() -> dict:
    sel = json.loads(SELECTION_PATH.read_text())
    out = {"model": sel["SELECTED_PHASE3_MODEL"], "alpha": sel["alpha_selection"]["alpha"],
           "arm": sel["alpha_selection"]["selected_arm"], "layers": sel["SELECTED_ARCHITECTURE"]["layers"],
           "terms": list(S.GLM_TERMS), "rebuilt": False}
    if sel.get("rebuild"):
        if not REBUILD_PATH.exists():
            raise RuntimeError("the selection requires the frozen rebuild stage (select_rebuild) first")
        rb = json.loads(REBUILD_PATH.read_text())
        out.update({"model": rb["SELECTED_PHASE3_MODEL"], "alpha": rb["alpha_selection"]["alpha"] if rb.get("alpha_selection") else None,
                    "arm": rb["alpha_selection"]["selected_arm"] if rb.get("alpha_selection") else None,
                    "layers": rb["layers"], "terms": rb["terms"], "rebuilt": True})
    return out


def stage_select_rebuild(log=print) -> dict:
    """Frozen rule: when a layer is rejected, rebuild S4 from the accepted layers and evaluate it once on 2024-25."""
    if REBUILD_PATH.exists():
        raise RuntimeError("the rebuild record exists")
    sel_commit = M.committed_unchanged(SELECTION_PATH)
    sel = json.loads(SELECTION_PATH.read_text())
    if not sel.get("rebuild"):
        raise RuntimeError("no layer was rejected; no rebuild")
    layers = sel["SELECTED_ARCHITECTURE"]["layers"]
    terms = accepted_terms(layers)
    g = guards()
    out = {"version": VERSION, "stage": "SELECTION_REBUILD", "selection_record_commit": sel_commit, "layers": layers,
           "terms": list(terms), "guards": g, "code": code_vs_protocol(), "season_2025_26_loaded": False}
    if not terms:
        out.update({"SELECTED_PHASE3_MODEL": "S0", "alpha_selection": None, "note": "no structural layer accepted"})
        _dump(REBUILD_PATH, out)
        return out
    T, F = SELECT["T"], SELECT["eval"]
    struct, s4, resid, accs = build_procedure(T, SELECT["resid_seasons"], log, terms=terms)
    s0, scale = _s0_structure("select")
    arms = ("S0",) + tuple(ALPHAS)
    summ = evaluate(F, struct, s4, s0, scale, f"select_rebuild_{F}", log, arms=arms)
    summ["determinism"] = determinism(F, struct, s4, s0, scale, arms=arms)
    alpha = select_alpha(summ)
    acc = acceptance(summ, alpha["selected_arm"])
    out.update({"structure": _compact_struct(struct), "s4_components": _compact_s4(s4), "summary": summ,
                "alpha_selection": alpha, "acceptance": acc,
                "SELECTED_PHASE3_MODEL": alpha["selected_arm"] if acc["ALL_PASS"] else "S0"})
    _dump(REBUILD_PATH, out)
    return out


def stage_confirm(log=print) -> dict:
    if CONFIRM_PATH.exists():
        raise RuntimeError("the 2025-26 retrospective confirmation runs exactly once; its record already exists")
    sel_commit = M.committed_unchanged(REBUILD_PATH if REBUILD_PATH.exists() else SELECTION_PATH)
    sel = _selected()
    g = guards()
    T, F = CONFIRM["T"], CONFIRM["eval"]
    struct, s4, resid, accs = build_procedure(T, CONFIRM["resid_seasons"], log)
    if max(resid["forecast_season"]) >= F:
        raise RuntimeError("an evaluation-season outcome entered the S4 fit")
    s0, scale = _s0_structure("confirm")
    log(f"evaluating once on F{F}")
    summ = evaluate(F, struct, s4, s0, scale, f"confirm_{F}", log)
    summ["determinism"] = determinism(F, struct, s4, s0, scale)
    gates = layer_gates(summ)
    rb_summ, sel_summ, sel_struct, sel_s4 = None, summ, struct, s4
    if sel["rebuilt"] and sel["model"] != "S0":
        terms = tuple(sel["terms"])
        sel_struct, sel_s4, _, _ = build_procedure(T, CONFIRM["resid_seasons"], log, terms=terms)
        arms = ("S0",) + tuple(ALPHAS)
        rb_summ = evaluate(F, sel_struct, sel_s4, s0, scale, f"confirm_rebuild_{F}", log, arms=arms)
        rb_summ["determinism"] = determinism(F, sel_struct, sel_s4, s0, scale, arms=arms)
        sel_summ = rb_summ
    acc = acceptance(sel_summ, sel["arm"]) if sel["model"] != "S0" else acceptance(summ, select_alpha(summ)["selected_arm"])
    out = {"version": VERSION, "stage": "RETROSPECTIVE_CONFIRMATION_2025_26",
           "nature": "retrospective confirmation; the selected Phase 3 procedure is not changed by this result",
           "selected": sel, "selection_record_commit": sel_commit, "selection_record_sha256": _sha(SELECTION_PATH),
           "structure_through": T, "evaluation_season": F, "guards": g, "code": code_vs_protocol(),
           "structure": _compact_struct(struct), "s4_components": _compact_s4(s4),
           "old_J2_vs_residual_J2": j2_comparison(json.loads(PHASE2_FITS["confirm"].read_text())["MID"], s4["residual_dependence"]),
           "summary": summ, "rebuilt_configuration_summary": rb_summ, "layer_gates_for_information": gates,
           "acceptance_for_information": acc, "alpha_comparison_for_information": select_alpha(sel_summ),
           "selected_model_confirmed": bool(acc["ALL_PASS"]) if sel["model"] != "S0" else None}
    resid.to_parquet(SCRATCH / "confirm_residual_basis.parquet", index=False)
    _dump(CONFIRM_PATH, out)
    return out


def stage_final(log=print) -> dict:
    conf_commit = M.committed_unchanged(CONFIRM_PATH)
    sel = _selected()
    g = guards()
    terms = tuple(sel["terms"]) if sel["model"] != "S0" else S.GLM_TERMS
    struct, s4, resid, accs = build_procedure(FINAL["T"], FINAL["resid_seasons"], log, terms=terms)
    art = {"version": VERSION, "role": "V2_PHASE3_STRUCTURAL_LAYER (research; not deployed)",
           "SELECTED_PHASE3_MODEL": sel["model"], "selected_architecture": sel,
           "MARGINAL_ARTIFACT": _rel(P2.MARGINAL_PATH), "MARGINAL_ARTIFACT_SHA256": MARGINAL_SHA,
           "PHASE2_DEPENDENCE_ARTIFACT": _rel(J2_PATH), "PHASE2_DEPENDENCE_ARTIFACT_SHA256": J2_SHA,
           "structure": _compact_struct(struct), "alpha": sel["alpha"],
           "conditional_dispersion": s4["dispersion"], "kappa": S.KAPPA, "families": S.FAMILIES,
           "family_rate_stat": S.FAMILY_RATE_STAT, "availability_interface": {
               "ACTIVE": 1.0, "OUT": 0.0, "EXPECTED_ACTIVE": "max(P(play), 0.9)", "QUESTIONABLE/UNCERTAIN": "supplied (default 0.5)",
               "none": "Phase 1B P(play)"},
           "construction": "cbb_platform/v2/structural.py (simulate_state, log_mu, anchor, mixture_tables, counts)",
           "fit": {"structure_through": FINAL["T"], "residual_basis_seasons": list(FINAL["resid_seasons"]),
                   "residual_basis_rows": int(len(resid)), "residual_basis_population": accs},
           "protocol_sha256": _sha(PROTOCOL_PATH), "selection_record_sha256": _sha(SELECTION_PATH),
           "confirmation_record_sha256": _sha(CONFIRM_PATH), "confirmation_record_commit": conf_commit,
           "PRODUCTION_SEASON": 2027, "OUTCOME_DATA_THROUGH": 2026}
    _dump(FINAL_PATH, art)
    dep = {"version": VERSION, "role": "V2_PHASE3_RESIDUAL_DEPENDENCE", "SELECTED_DEPENDENCE_MODEL": "J2_RESIDUAL",
           "MARGINAL_ARTIFACT_SHA256": MARGINAL_SHA, "PHASE2_DEPENDENCE_ARTIFACT_SHA256": J2_SHA,
           "PHASE3_STRUCTURAL_ARTIFACT_SHA256": _sha(FINAL_PATH),
           "conditioning": "latent Gaussian copula over the S4 CONDITIONAL count distributions given the structural "
                           "state (environment, minutes, opportunity)",
           "fit": {"source": "true-OOS structural residual basis (season F with structural parameters through F-1)",
                   "seasons": list(FINAL["resid_seasons"]), "rows": s4["fit_rows"], "games": s4["fit_games"]},
           "pit": "MID", "stats": list(L.STATS),
           "parameters": {k: s4["residual_dependence"][k] for k in ("within_player", "same_team", "cross_team", "shared")},
           "normalization": s4["residual_dependence"]["normalization"],
           "psd_repairs_at_fit": s4["residual_dependence"]["psd_repairs_at_fit"],
           "old_J2_vs_residual_J2": j2_comparison(json.loads(J2_PATH.read_text())["parameters"], s4["residual_dependence"]),
           "PRODUCTION_SEASON": 2027, "OUTCOME_DATA_THROUGH": 2026}
    _dump(RESID_DEP_PATH, dep)
    man = build_store(resid, struct, s4)
    ver = {"version": VERSION, "final_artifact": _rel(FINAL_PATH), "final_artifact_sha256": _sha(FINAL_PATH),
           "residual_dependence_artifact": _rel(RESID_DEP_PATH), "residual_dependence_artifact_sha256": _sha(RESID_DEP_PATH),
           "structural_oos_store_manifest": _rel(STORE_MANIFEST_PATH), "guards": g, "code": code_vs_protocol(),
           "V1_ARTIFACT_UNCHANGED": g["V1_ARTIFACT_UNCHANGED"], "V2_PHASE1B_ARTIFACT_UNCHANGED": g["V2_PHASE1B_ARTIFACT_UNCHANGED"],
           "PHASE2_J2_ARTIFACT_UNCHANGED": g["PHASE2_J2_ARTIFACT_UNCHANGED"], "HISTORICAL_API_CALLS": 0,
           "2026_27_OUTCOME_DATA_TOUCHED": False, "store": man}
    _dump(VERIFY_PATH, ver)
    return ver


def build_store(resid_final: pd.DataFrame, struct: dict, s4: dict) -> dict:
    """True-OOS structural forecast store: per scored player-game, the realized-state residual basis (every season,
    structure through F-1) joined with the pregame simulation summaries of the evaluation runs (F2025: structure
    through 2023-24; F2026: through 2024-25)."""
    keep = ["forecast_season", "training_through_season", "game_id", "team_id", "player_id", "player_game_example_id",
            "prediction_cutoff_utc", "role_diagnostic", "position_group", "p_play", "p_play_status", "min_mu", "min_sigma",
            "structure_T", "x_pace_dev", "x_own_eff_dev", "x_opp_eff_dev"] + [f"opp_{f}" for f in S.FAM] + \
           [f"{s}_mu" for s in L.STATS] + [f"cond_{s}_mu" for s in L.STATS] + ["y_min"] + [f"y_{s}" for s in L.STATS]
    df = resid_final[keep].copy()
    sel = _selected()
    for s in L.STATS:
        df[f"phi_conditional_{s}"] = s4["dispersion"][s]["phi"]
    df["alpha"] = sel["alpha"]
    df["availability_scenario"] = np.where(df["forecast_season"] >= 2025, "BASE_PPLAY", "REALIZED_STATE_RESIDUAL_BASIS_ONLY")
    sims = []
    for tag in ("select_2025", "confirm_2026"):
        p = SCRATCH / f"{tag}_simstore.parquet"
        if p.exists():
            sims.append(pd.read_parquet(p))
    if sims:
        sm = pd.concat(sims, ignore_index=True)
        cols = [c for c in sm.columns if not c.startswith("S4_") or c.startswith(sel["arm"] + "_")]
        df = df.merge(sm[cols], on="player_game_example_id", how="left", validate="one_to_one")
    env = struct["env"]
    df["env_sd_pace"], df["env_sd_eff"], df["env_rho_eff"] = env["sd_pace"], env["sd_eff"], env["rho_eff"]
    df["model_version"] = VERSION
    df["data_version"] = "phase1b-oos(" + STORE_SHA[:12] + "; F2023 " + F2023_SHA[:12] + ") + stage3 team features"
    df.to_parquet(STORE_PATH, index=False)
    man = {"store": _rel(STORE_PATH), "sha256": _sha(STORE_PATH), "rows": int(len(df)),
           "rows_by_season": {int(k): int(v) for k, v in df["forecast_season"].value_counts().sort_index().items()},
           "games": int(df["game_id"].nunique()), "columns": list(df.columns),
           "chronology": "season F: structural parameters through F-1 (structure_T); pregame simulation summaries "
                         "(sim_*, share_*, S4 structural means) exist for 2024-25 (selection run) and 2025-26 "
                         "(confirmation run); 2023-24 has no pregame P(play), so only its realized-state residual basis",
           "use": "fit the final residual dependence (phase3_residual_dependence.json); never in-sample residuals"}
    _dump(STORE_MANIFEST_PATH, man)
    return man


if __name__ == "__main__":
    stage = sys.argv[1]
    fn = {"protocol": stage_protocol, "select": stage_select, "select_rebuild": stage_select_rebuild,
          "confirm": stage_confirm, "final": stage_final}[stage]
    r = fn()
    print(json.dumps({k: r[k] for k in ("SELECTED_PHASE3_MODEL", "selected_model_confirmed", "final_artifact",
                                        "final_artifact_sha256") if k in r}, indent=1, default=_jd))
