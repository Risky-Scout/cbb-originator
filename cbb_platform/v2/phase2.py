"""V2 Phase 2: marginal-preserving joint player/stat dependence. Stages (each refuses to run out of order):

    .venv/bin/python -m cbb_platform.v2.phase2 protocol   write the frozen protocol (commit it before `select`)
    .venv/bin/python -m cbb_platform.v2.phase2 select     fit on 2023-24 OOS pairs, evaluate J0/J1/J2 on 2024-25, select
    .venv/bin/python -m cbb_platform.v2.phase2 confirm    refit (frozen procedure) through 2024-25, evaluate 2025-26 ONCE
    .venv/bin/python -m cbb_platform.v2.phase2 final      fit the selected procedure on 2023-24..2025-26 OOS pairs

Inputs: the Phase 1B true-OOS forecast store only (never the final through-2025-26 marginal's in-sample residuals, never
2026-27). The Phase 1B marginal artifact and the v1 artifact are read only to verify their hashes.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.v2 import dependence as D, joint as J, latent as L, marginal as M, phase1b as B, provenance as PV

VERSION = "cbb-v2-phase2-dependence-1.0.0"
OUT = M.OUT_DIR
PROTOCOL_PATH = OUT / "phase2_protocol.json"
SEL_FIT_PATH = OUT / "phase2_selection_fit_2023_24.json"
SELECTION_PATH = OUT / "phase2_dependence_selection.json"
CONFIRM_FIT_PATH = OUT / "phase2_confirmation_fit_through_2024_25.json"
CONFIRM_PATH = OUT / "phase2_retrospective_2025_26.json"
VERIFY_PATH = OUT / "phase2_verification.json"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_phase2"          # gitignored; disposable per-game tables
STORE = B.OOS_PATH
STORE_SHA = "d0859e48763ed0ce68a0be72c82ec7f7f5d0989c88e90a4b0c214c6d7fa8e425"
MARGINAL_PATH = B.ARTIFACT_PATH
MARGINAL_SHA = "bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f"
V1_SHA = "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"
CODE = [C.PROJECT_ROOT / "cbb_platform" / "v2" / f for f in ("latent.py", "dependence.py", "joint.py", "phase2.py")]
SELECT_FIT, SELECT_EVAL = (2024,), 2025
CONFIRM_FIT, CONFIRM_EVAL = (2024, 2025), 2026
FINAL_FIT = (2024, 2025, 2026)
CANDIDATES = ("J0", "J1", "J2")
SENSITIVITY = {"J1_RAND": "J1", "J2_RAND": "J2"}                 # randomized-PIT refits; reported, never selectable
EVAL_BOOT = 2000
EVAL_SEED = 20264
MAJOR = ("ES", "VS_BAL", "COMBO_LOG", "EVENT_BRIER")
Z_PROBE = 3.29                                                    # two-sided 0.1%
PROBE_SHARE_MAX = 0.005
PROBE_MAX_ABS_Z = 6.5
DKW_ALPHA = 0.001
SINGLE_TOL = 0.002                                                # nats per player-stat
COMBO_DEGRADE_NATS = 0.002
COMBO_DEGRADE_CRPS_REL = 0.005
STABILITY_MIN_CORR = 0.7
STABILITY_MIN_SIGN_AGREEMENT = 0.9
MC_DECISION_RATIO = 3.0
MC_RERUN_DRAWS = 50_000


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rel(p) -> str:
    return str(p.relative_to(C.PROJECT_ROOT))


def _dump(path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=_json_default) + "\n")


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def guards() -> dict:
    out = {"V1_ARTIFACT_SHA256": _sha(PV.V1_ARTIFACT), "MARGINAL_ARTIFACT_SHA256": _sha(MARGINAL_PATH),
           "OOS_STORE_SHA256": _sha(STORE)}
    out["V1_ARTIFACT_UNCHANGED"] = out["V1_ARTIFACT_SHA256"] == V1_SHA
    out["V2_MARGINAL_ARTIFACT_UNCHANGED"] = out["MARGINAL_ARTIFACT_SHA256"] == MARGINAL_SHA
    out["OOS_STORE_UNCHANGED"] = out["OOS_STORE_SHA256"] == STORE_SHA
    if not (out["V1_ARTIFACT_UNCHANGED"] and out["V2_MARGINAL_ARTIFACT_UNCHANGED"] and out["OOS_STORE_UNCHANGED"]):
        raise RuntimeError(f"an input artifact changed: {out}")
    return out


def code_state() -> dict:
    return {_rel(p): _sha(p) for p in CODE}


def code_vs_protocol() -> dict:
    prot = json.loads(PROTOCOL_PATH.read_text())["code_sha256_at_freeze"]
    now = code_state()
    return {"code_sha256": now, "changed_since_protocol": sorted(k for k in now if now[k] != prot.get(k))}


# ============================================================================ protocol
def protocol() -> dict:
    return {
        "version": VERSION, "phase": "V2 PHASE 2: MARGINAL-PRESERVING JOINT PLAYER/STAT DEPENDENCE",
        "frozen_before_any_candidate_evaluation": True,
        "inputs": {"oos_store": _rel(STORE), "oos_store_sha256": STORE_SHA, "marginal_artifact": _rel(MARGINAL_PATH),
                   "MARGINAL_ARTIFACT_SHA256": MARGINAL_SHA, "v1_artifact_sha256": V1_SHA,
                   "rule": "dependence is estimated only from true-OOS forecast/outcome pairs of the Phase 1B store "
                           "(training_through_season < forecast_season); never from the final marginal's residuals; "
                           "no 2026-27 data; no API calls; marginals are never changed"},
        "population": {"rows": "participation_status == PLAYED with all 7 count labels; DNP / not on roster / unknown "
                               "participation are not playing observations and are excluded (never zeros); P(play) is "
                               "not used and never imputed",
                       "games": "both teams must have eligible rows", "stats": list(L.STATS)},
        "latent_residual": {"primary": "MID: z = Phi^-1(F(y-1) + p(y)/2)", "sensitivity": "RAND: randomized PIT, keyed "
                            "BLAKE2b v per (game, player, stat), seed %d" % D.FIT_SEED,
                            "centering": "z - E_model[z], then training mean per (role, stat)",
                            "scaling": "training ratio sqrt(E[(z - Ez - delta)^2] / E[Var_model z]) per (role, stat)",
                            "attenuation": "pooled moments divided by sum kappa_i kappa_j (first-order Hermite; "
                                           "kappa = E[z Z] from the marginal PMF)"},
        "structure": {"J0": "independence (identity latent correlation)",
                      "J1": "within-player 7x7 latent correlation; hierarchy global -> role (L5 minutes rank band) -> "
                            "role x position (G/F/C; U uses the role matrix); every level's deviation soft-thresholded at "
                            "%.2f game-clustered bootstrap SEs (%d draws); conditioning: a child keeps min eig >= %.1f x "
                            "its parent's (deviation scaled back, recorded)" % (D.SHRINK_Z, D.BOOTSTRAP, D.CONDITION_RATIO),
                      "J2": "J1 + same-team S and opposite-team X stat x stat latent correlations (distinct players), "
                            "soft-thresholded the same way; A = (M + X)/2, B = (M - X)/2, Q = M - S with M the dominating "
                            "additive Gram (NBA); Q admitted only by the pivotal-bootstrap competition gate (level %.3f); "
                            "per-player shrink c_ab (A + B) then c_q (Q) keeps every residual block PSD" % D.COMPETITION_GATE_LEVEL,
                      "no_named_player_or_pair_parameters": True, "tuning_grid": "none (all constants declared here)"},
        "psd": {"rule": "every game matrix is PSD by construction; validity floor min eig >= %g; residual blocks >= %g; "
                        "Cholesky jitter only for rounding (recorded), rows renormalized to unit norm" % (D.PSD_FLOOR, D.BLOCK_FLOOR),
                "INVALID_CORRELATION_MATRICES_required": 0},
        "simulation": {"draws_per_game": J.SIMS, "seed_base": J.SIM_SEED, "seed": "BLAKE2b(seed_base | game_id)",
                       "common_random_numbers": "one standard-normal bank per game shared by every candidate",
                       "inverse_cdf": "X = #{k : Phi^-1(F(k)) < z} (== F^-1(Phi(z)))", "combos": J.COMBOS,
                       "mc_error": "ES on draw halves, VS on two disjoint 2,000-draw replicates (reported VS = their "
                                   "mean); if, in the selection stage, any ES / VS_BAL interval bound used by the rule "
                                   "(J1-J0, J2-J0, J2-J1) lies within %.0f MC SE of zero, the selection evaluation is "
                                   "rerun with %d draws and that rerun decides" % (MC_DECISION_RATIO, MC_RERUN_DRAWS)},
        "chronology": {"selection": {"fit": list(SELECT_FIT), "evaluate": SELECT_EVAL},
                       "retrospective_confirmation": {"fit": list(CONFIRM_FIT), "evaluate": CONFIRM_EVAL,
                                                      "runs": "exactly once; selection unchanged by it"},
                       "final_artifact_fit": list(FINAL_FIT)},
        "candidates": list(CANDIDATES), "sensitivity_arms_not_selectable": SENSITIVITY,
        "metrics": {
            "scale": "each stat divided by its SD over the fit population's eligible rows (training only)",
            "ES": "energy score of the scaled full game vector (all eligible players x 7), mean over games [major]",
            "VS_BAL": "variogram score p = %.1f on the scaled game vector, mean of the per-pair means of the three pair "
                      "types (within player / same team / opposite team), mean over games [major]" % J.VS_P,
            "COMBO_LOG": "log score of PRA, PR, PA, RA, STOCKS from the same draws (MC PMF floored at %.1f/draws), mean "
                         "over player x combo [major]" % J.PROB_FLOOR_DRAWS,
            "EVENT_BRIER": "Brier score of the predefined joint threshold events, mean over events [major]",
            "secondary": ["ES_PLAYER", "VS components / uniform", "combo CRPS + Brier at J0-convolution quantile lines "
                          "0.25/0.5/0.75", "event log loss + per-family calibration", "joint tails", "count-space pair "
                          "correlation calibration (diagnostic; centered by evaluation-season means)",
                          "single-stat MC log score / CRPS"],
            "events": {k: v for k, v in J.EVENTS.items()}, "pair_diagnostics": J.PAIRS,
            "uncertainty": "game-level bootstrap, %d draws, seed %d; 95%% percentile intervals of paired differences" % (EVAL_BOOT, EVAL_SEED)},
        "marginal_preservation": {
            "probes": "per (player, stat): z of simulated mean, variance, P(X > q) at the analytic 0.25/0.5/0.75 "
                      "quantiles, P(X = mode); KS distance; single-stat MC log score vs J0",
            "PASS_if": {"per candidate x stat x probe": f"share |z| > {Z_PROBE} <= {PROBE_SHARE_MAX} and max |z| <= "
                                                        f"{PROBE_MAX_ABS_Z} (probes with >= 20 expected MC events)",
                        "KS": f"share of dims with KS > DKW({DKW_ALPHA}) <= {PROBE_SHARE_MAX}",
                        "single_stat_log_score": f"|J_k - J0| <= {SINGLE_TOL} nats or 99% CI contains 0",
                        "construction": "unit latent diagonal (|diag - 1| <= 1e-12) and 0 invalid matrices"}},
        "acceptance_rule": {
            "passes_vs_J0": ["MARGINAL_PRESERVATION PASS", "0 invalid matrices",
                             "ES or VS_BAL improves with the 95% CI upper bound < 0",
                             ">= 3 of the 4 major metrics improve (point) and none is significantly worse",
                             f"no combo materially degraded (log +{COMBO_DEGRADE_NATS} nats or CRPS +{COMBO_DEGRADE_CRPS_REL:.1%} "
                             "with the CI excluding 0)",
                             f"stability: added component's raw cells (F2024 fit vs a diagnostic F2025-only fit) correlate "
                             f">= {STABILITY_MIN_CORR} and agree in sign on >= {STABILITY_MIN_SIGN_AGREEMENT:.0%} of the cells "
                             "nonzero in both"],
            "J2_adds_value_vs_J1": ["ES or VS_BAL improves vs J1 with CI upper < 0",
                                    ">= 2 of ES, VS_BAL, EVENT_BRIER improve vs J1 (point)",
                                    "no major metric significantly worse than J1"],
            "selection": "J2 if it passes vs J0 and adds value vs J1; else J1 if it passes vs J0; else J0 (reported "
                         "honestly)"},
        "code_sha256_at_freeze": code_state(),
    }


def stage_protocol() -> dict:
    if PROTOCOL_PATH.exists():
        raise RuntimeError("the Phase 2 protocol is frozen; it is never rewritten")
    for p in (SEL_FIT_PATH, SELECTION_PATH, CONFIRM_PATH):
        if p.exists():
            raise RuntimeError(f"{p.name} exists before the protocol")
    g = guards()
    out = protocol()
    out["guards_at_freeze"] = g
    _dump(PROTOCOL_PATH, out)
    return out


# ============================================================================ fitting
def fit_record(pop: pd.DataFrame, acc: dict, label: str) -> dict:
    t = time.time()
    mid = D.fit(pop, "MID")
    rand = D.fit(pop, "RAND")
    scale = pop[[f"y_{s}" for s in L.STATS]].std(ddof=1).to_numpy()
    return {"label": label, "population": acc, "MID": mid, "RAND": rand, "scale": dict(zip(L.STATS, scale.tolist())),
            "pit_sensitivity": pit_compare(mid, rand), "seconds": round(time.time() - t, 1)}


def pit_compare(a: dict, b: dict) -> dict:
    def cells(f):
        R = np.asarray(f["within_player"]["global"]["R"])
        iu = np.triu_indices(D.NS, 1)
        ju = np.triu_indices(D.NS)
        return {"within_global": R[iu], "same_team": np.asarray(f["same_team"]["thresholded"])[ju],
                "cross_team": np.asarray(f["cross_team"]["thresholded"])[ju]}
    ca, cb = cells(a), cells(b)
    return {k: {"max_abs_diff": float(np.abs(ca[k] - cb[k]).max()),
                "corr": float(np.corrcoef(ca[k], cb[k])[0, 1]) if ca[k].std() > 0 and cb[k].std() > 0 else None,
                "sign_agreement_nonzero_both": _sign_agree(ca[k], cb[k])} for k in ca} | {
        "competition_gate": [a["shared"]["competition_family_active"], b["shared"]["competition_family_active"]]}


def _sign_agree(a, b):
    m = (a != 0) & (b != 0)
    return float((np.sign(a[m]) == np.sign(b[m])).mean()) if m.any() else None


def structures(fit: dict) -> dict:
    out = {c: D.structure(c, fit["MID"] if c != "J0" else None) for c in CANDIDATES}
    for arm, base in SENSITIVITY.items():
        out[arm] = D.structure(base, fit["RAND"])
    return out


def compact_fit(rec: dict) -> dict:
    """Fit record without per-group raw diagnostics duplicated for RAND (kept compact)."""
    out = dict(rec)
    out["RAND"] = {k: rec["RAND"][k] for k in ("pit", "within_player", "same_team", "cross_team", "shared",
                                               "psd_repairs_at_fit", "normalization")}
    return out


# ============================================================================ evaluation
def evaluate(pop: pd.DataFrame, sts: dict, scale: np.ndarray, tag: str, log=print, sims: int = J.SIMS) -> dict:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    games = list(pop.groupby("game_id", sort=True))
    names = list(sts)
    rows, events, pairs, rel = [], [], [], []
    probes = {c: {s: {k: [0, 0, 0.0] for k in ("mean", "var", "over_q25", "over_q50", "over_q75", "cell")} | {"ks": [0, 0, 0.0]}
                  for s in L.STATS} for c in CANDIDATES}
    obs_m, obs_lin, obs_pairs = [], [], []
    sim_m = {c: np.zeros((3, D.NS, D.NS)) for c in names}
    cons = {c: {"games": 0, "invalid": 0, "min_eig": np.inf, "max_diag_err": 0.0, "jitter_games": 0, "c_ab_min": 1.0,
                "c_q_mean_sum": 0.0, "c_q_min": 1.0} for c in names}
    e_sum, e_n = np.zeros(D.NS), 0
    dkw = float(np.sqrt(np.log(2 / DKW_ALPHA) / (2 * sims)))
    t0 = time.time()
    for gi, (gid, gp) in enumerate(games):
        r = J.score_game(gp, sts, scale, sims=sims, probe_arms=CANDIDATES)
        n = r["n_players"]
        row = {"game_id": gid, "n_players": n, "n_events": len(r["events"])}
        for c in names:
            x = r["cand"][c]
            row.update({f"{c}|ES": x["es"], f"{c}|ES_rep_a": x["es_rep"][0], f"{c}|ES_rep_b": x["es_rep"][1],
                        f"{c}|ES_PLAYER": x["es_player"], f"{c}|VS_BAL": x["vs"]["balanced"],
                        f"{c}|VS_WITHIN": x["vs"]["within_player"], f"{c}|VS_SAME": x["vs"]["same_team"],
                        f"{c}|VS_CROSS": x["vs"]["cross_team"], f"{c}|VS_UNIFORM": x["vs"]["uniform"],
                        f"{c}|VS_rep_a": x["vs_rep"][0], f"{c}|VS_rep_b": x["vs_rep"][1]})
            tot = 0.0
            for cb, v in x["combo"].items():
                row[f"{c}|{cb}_LOG"] = float(v["log"].sum())
                row[f"{c}|{cb}_CRPS"] = float(v["crps"].sum())
                row[f"{c}|{cb}_FLOORED"] = int(v["floored"])
                for li, q in enumerate(J.COMBO_LINE_Q):
                    row[f"{c}|{cb}_BRIER_q{int(q * 100)}"] = float(v["brier"][:, li].sum())
                tot += float(v["log"].sum())
            row[f"{c}|COMBO_LOG"] = tot
            for j, s in enumerate(L.STATS):
                row[f"{c}|SINGLE_{s}_LOG"] = float(x["single"]["log"][:, j].sum())
                row[f"{c}|SINGLE_{s}_CRPS"] = float(x["single"]["crps"][:, j].sum())
            row[f"{c}|EVENT_BRIER"] = float(sum((e[f"p_{c}"] - e["outcome"]) ** 2 for e in r["events"]))
            fl = 0.5 / sims
            row[f"{c}|EVENT_LOG"] = float(sum(-np.log(np.clip(e[f"p_{c}"], fl, 1 - fl)) if e["outcome"] else
                                              -np.log(np.clip(1 - e[f"p_{c}"], fl, 1 - fl)) for e in r["events"]))
            for k, m in enumerate(("within_player", "same_team", "cross_team")):
                sim_m[c][k] += x["sim_moments"][m]
            inf = r["info"][c]
            cc = cons[c]
            cc["games"] += 1
            cc["invalid"] += int(inf["invalid"])
            cc["min_eig"] = min(cc["min_eig"], inf["min_eig"])
            cc["max_diag_err"] = max(cc["max_diag_err"], inf["diag_err"])
            cc["jitter_games"] += int(inf["jitter"] > 0)
            cc["c_ab_min"] = min(cc["c_ab_min"], inf["c_ab_min"])
            cc["c_q_mean_sum"] += inf["c_q_mean"]
            cc["c_q_min"] = min(cc["c_q_min"], inf["c_q_min"])
            if c in CANDIDATES:
                _probe_update(probes[c], x["probes"], r["probe_expected"], dkw)
        rows.append(row)
        for e in r["events"]:
            events.append({"game_id": gid, **e})
        for p in r["pairs"]:
            pairs.append({"game_id": gid, **p})
        for cb in J.COMBOS:
            yv = r["y_combo"][cb]
            for p in range(n):
                rec = {"game_id": gid, "combo": cb, "outcome": int(yv[p] > r["lines"][cb][p][1])}
                for c in names:
                    rec[f"p_{c}"] = float(r["cand"][c]["combo"][cb]["p_over"][p, 1])
                rel.append(rec)
        # observed standardized count residuals for the correlation diagnostic
        e = r["e_obs"]
        team = (gp["team_id"] != gp["team_id"].min()).astype(int).to_numpy()
        mo = r["obs_moments"]
        obs_m.append(np.stack([mo["within_player"], mo["same_team"], mo["cross_team"]]))
        T = [e[team == t].sum(axis=0) for t in (0, 1)]
        nt = [(team == t).sum() for t in (0, 1)]
        obs_lin.append(np.stack([e.sum(axis=0), (nt[0] - 1) * T[0] + (nt[1] - 1) * T[1], nt[1] * T[0] + nt[0] * T[1]]))
        obs_pairs.append([n, nt[0] * (nt[0] - 1) + nt[1] * (nt[1] - 1), 2 * nt[0] * nt[1]])
        e_sum += e.sum(axis=0)
        e_n += n
        if (gi + 1) % 250 == 0 or gi + 1 == len(games):
            el = time.time() - t0
            log(f"[{tag}] {gi + 1}/{len(games)} games  {el / 60:.1f} min  eta {(len(games) - gi - 1) * el / (gi + 1) / 60:.1f} min")
    gt = pd.DataFrame(rows)
    ev = pd.DataFrame(events)
    pr = pd.DataFrame(pairs)
    rl = pd.DataFrame(rel)
    for name, df in (("games", gt), ("events", ev), ("pairs", pr), ("combo_reliability", rl)):
        df.to_parquet(SCRATCH / f"{tag}_{name}.parquet", index=False)
    om = {"obs": np.stack(obs_m), "lin": np.stack(obs_lin), "pairs": np.asarray(obs_pairs, dtype=float),
          "mean_e": e_sum / e_n}
    for c in names:
        cons[c]["c_q_mean"] = cons[c].pop("c_q_mean_sum") / max(cons[c]["games"], 1)
        cons[c]["INVALID_CORRELATION_MATRICES"] = cons[c].pop("invalid")
    out = summarize(gt, ev, pr, rl, probes, om, sim_m, cons, names, dkw, sims)
    out["draws_per_game"] = sims
    return out


def _probe_update(acc: dict, pz: dict, sup: dict, dkw: float) -> None:
    for j, s in enumerate(L.STATS):
        def upd(key, z, ok):
            z = np.abs(np.asarray(z)[ok])
            a = acc[s][key]
            a[0] += int(z.size)
            a[1] += int((z > Z_PROBE).sum())
            a[2] = max(a[2], float(z.max()) if z.size else 0.0)
        upd("mean", pz["z_mean"][:, j], sup["moment"][:, j])
        upd("var", pz["z_var"][:, j], sup["moment"][:, j])
        for qi, q in enumerate(("over_q25", "over_q50", "over_q75")):
            upd(q, pz["z_over"][:, j, qi], sup["over"][:, j, qi])
        upd("cell", pz["z_cell"][:, j], sup["cell"][:, j])
        ks = pz["ks"][:, j]
        a = acc[s]["ks"]
        a[0] += int(ks.size)
        a[1] += int((ks > dkw).sum())
        a[2] = max(a[2], float(ks.max()))


def _boot_weights(G: int) -> np.ndarray:
    return np.random.default_rng(EVAL_SEED).multinomial(G, np.full(G, 1.0 / G), size=EVAL_BOOT).astype(float)


def _ratio_ci(num: np.ndarray, den: np.ndarray, W: np.ndarray, q=(0.025, 0.975)) -> dict:
    pt = float(num.sum() / den.sum())
    bs = (W @ num) / (W @ den)
    lo, hi = np.quantile(bs, q)
    return {"value": pt, "lo": float(lo), "hi": float(hi)}


def metric_arrays(gt: pd.DataFrame, c: str) -> dict:
    """Per-game numerators / denominators of every summarized metric for candidate c."""
    one = np.ones(len(gt))
    npl = gt["n_players"].to_numpy(dtype=float)
    out = {"ES": (gt[f"{c}|ES"].to_numpy(), one), "ES_PLAYER": (gt[f"{c}|ES_PLAYER"].to_numpy(), one),
           "VS_BAL": (gt[f"{c}|VS_BAL"].to_numpy(), one), "VS_WITHIN": (gt[f"{c}|VS_WITHIN"].to_numpy(), one),
           "VS_SAME": (gt[f"{c}|VS_SAME"].to_numpy(), one), "VS_CROSS": (gt[f"{c}|VS_CROSS"].to_numpy(), one),
           "VS_UNIFORM": (gt[f"{c}|VS_UNIFORM"].to_numpy(), one),
           "COMBO_LOG": (gt[f"{c}|COMBO_LOG"].to_numpy(), npl * len(J.COMBOS)),
           "EVENT_BRIER": (gt[f"{c}|EVENT_BRIER"].to_numpy(), gt["n_events"].to_numpy(dtype=float)),
           "EVENT_LOG": (gt[f"{c}|EVENT_LOG"].to_numpy(), gt["n_events"].to_numpy(dtype=float))}
    for cb in J.COMBOS:
        out[f"{cb}_LOG"] = (gt[f"{c}|{cb}_LOG"].to_numpy(), npl)
        out[f"{cb}_CRPS"] = (gt[f"{c}|{cb}_CRPS"].to_numpy(), npl)
        for q in J.COMBO_LINE_Q:
            out[f"{cb}_BRIER_q{int(q * 100)}"] = (gt[f"{c}|{cb}_BRIER_q{int(q * 100)}"].to_numpy(), npl)
    for s in L.STATS:
        out[f"SINGLE_{s}_LOG"] = (gt[f"{c}|SINGLE_{s}_LOG"].to_numpy(), npl)
        out[f"SINGLE_{s}_CRPS"] = (gt[f"{c}|SINGLE_{s}_CRPS"].to_numpy(), npl)
    return out


def summarize(gt, ev, pr, rl, probes, om, sim_m, cons, names, dkw, sims=J.SIMS) -> dict:
    G = len(gt)
    W = _boot_weights(G)
    arrs = {c: metric_arrays(gt, c) for c in names}
    levels = {c: {m: _ratio_ci(*v, W) for m, v in arrs[c].items()} for c in names}
    deltas = {}
    for a, b in [(c, "J0") for c in names if c != "J0"] + [("J2", "J1"), ("J1_RAND", "J1"), ("J2_RAND", "J2")]:
        if a not in names or b not in names:
            continue
        d = {}
        for m in arrs[a]:
            num = arrs[a][m][0] - arrs[b][m][0]
            den = arrs[a][m][1]
            d[m] = _ratio_ci(num, den, W)
            d[m]["ci99"] = list(_ratio_ci(num, den, W, (0.005, 0.995)).values())[1:]
            d[m]["relative"] = d[m]["value"] / levels[b][m]["value"] if levels[b][m]["value"] else None
        deltas[f"{a}-{b}"] = d
    mc = {}
    for a in names:
        if a == "J0":
            continue
        for m, ka, kb in (("ES", "ES_rep_a", "ES_rep_b"), ("VS_BAL", "VS_rep_a", "VS_rep_b")):
            dg = (gt[f"{a}|{ka}"] - gt[f"J0|{ka}"]) - (gt[f"{a}|{kb}"] - gt[f"J0|{kb}"])
            mc[f"{a}-J0|{m}"] = float(dg.std(ddof=1) / np.sqrt(G) / 2)
        if a == "J2" and "J1" in names:
            for m, ka, kb in (("ES", "ES_rep_a", "ES_rep_b"), ("VS_BAL", "VS_rep_a", "VS_rep_b")):
                dg = (gt[f"J2|{ka}"] - gt[f"J1|{ka}"]) - (gt[f"J2|{kb}"] - gt[f"J1|{kb}"])
                mc[f"J2-J1|{m}"] = float(dg.std(ddof=1) / np.sqrt(G) / 2)
    return {"games": G, "player_games": int(gt["n_players"].sum()), "events": int(len(ev)),
            "levels": levels, "deltas": deltas, "mc_se": mc,
            "marginal_preservation": marginal_preservation(probes, deltas, cons, dkw),
            "construction": cons,
            "events_by_family": event_families(ev, names, W, gt, sims),
            "pair_diagnostics": pair_summary(pr, names),
            "correlation_calibration": correlation_calibration(om, sim_m, names),
            "combo_reliability": reliability(rl, names)}


def marginal_preservation(probes, deltas, cons, dkw) -> dict:
    out = {}
    for c in CANDIDATES:
        stats, ok = {}, True
        for s in L.STATS:
            rec = {}
            for k, (n, ex, mx) in probes[c][s].items():
                share = ex / n if n else 0.0
                if k == "ks":
                    passed = share <= PROBE_SHARE_MAX
                else:
                    passed = share <= PROBE_SHARE_MAX and mx <= PROBE_MAX_ABS_Z
                rec[k] = {"n": n, "exceed": ex, "share": share, "max": mx, "pass": passed}
                ok &= passed
            if c != "J0":
                d = deltas[f"{c}-J0"][f"SINGLE_{s}_LOG"]
                sp = abs(d["value"]) <= SINGLE_TOL or (d["ci99"][0] <= 0 <= d["ci99"][1])
                rec["single_log_vs_J0"] = {"delta": d["value"], "ci99": d["ci99"], "pass": sp}
                ok &= sp
            stats[s] = rec
        cons_ok = cons[c]["INVALID_CORRELATION_MATRICES"] == 0 and cons[c]["max_diag_err"] <= 1e-12
        out[c] = {"PASS": bool(ok and cons_ok), "construction_pass": bool(cons_ok), "dkw_threshold": dkw, "by_stat": stats}
    return out


def event_families(ev: pd.DataFrame, names, W, gt, sims=J.SIMS) -> dict:
    gidx = {g: i for i, g in enumerate(gt["game_id"])}
    ev = ev.assign(gi=ev["game_id"].map(gidx))
    G = len(gt)
    out = {}
    fl = 0.5 / sims
    for fam, e in ev.groupby("event", sort=True):
        rec = {"n": int(len(e)), "observed_rate": float(e["outcome"].mean())}
        cnt = np.bincount(e["gi"], minlength=G).astype(float)
        for c in names:
            p = e[f"p_{c}"].to_numpy()
            o = e["outcome"].to_numpy()
            rec[c] = {"mean_p": float(p.mean()), "brier": float(((p - o) ** 2).mean()),
                      "log": float(-(o * np.log(np.clip(p, fl, 1)) + (1 - o) * np.log(np.clip(1 - p, fl, 1))).mean()),
                      "obs_over_pred": float(o.mean() / p.mean()) if p.mean() > 0 else None}
            if c != "J0":
                dn = np.bincount(e["gi"], weights=(p - o) ** 2 - (e["p_J0"].to_numpy() - o) ** 2, minlength=G)
                rec[c]["brier_minus_J0"] = _ratio_ci(dn, cnt, W) if cnt.sum() else None
        ob = np.bincount(e["gi"], weights=e["outcome"].to_numpy(dtype=float), minlength=G)
        rec["observed_rate_ci"] = _ratio_ci(ob, cnt, W)
        out[fam] = rec
    return out


def pair_summary(pr: pd.DataFrame, names) -> dict:
    out = {}
    for name, p in pr.groupby("pair", sort=True):
        rec = {"n": int(len(p)), "observed_mean_product": float(p["obs"].mean()),
               "observed_se": float(p.groupby("game_id")["obs"].sum().std(ddof=1) * np.sqrt(p["game_id"].nunique()) / len(p))}
        for c in names:
            rec[f"sim_{c}"] = float(p[f"sim_{c}"].mean())
        out[name] = rec
    return out


def correlation_calibration(om: dict, sim_m: dict, names) -> dict:
    """Count-space pooled pair correlations, observed (centered by evaluation-season means, diagnostic only) vs the
    candidates' simulated expectation, per pair type and stat pair."""
    obs, lin, pairs, m = om["obs"].sum(axis=0), om["lin"].sum(axis=0), om["pairs"].sum(axis=0), om["mean_e"]
    out = {}
    types = ("within_player", "same_team", "cross_team")
    # realized variance of e (for correlation units) from the within-player diagonal
    var_e = np.diag(obs[0]) / pairs[0] - m ** 2
    sd = np.sqrt(np.maximum(var_e, 1e-12))
    for k, t in enumerate(types):
        cen = obs[k] - np.outer(m, lin[k]) - np.outer(lin[k], m) + pairs[k] * np.outer(m, m)
        oc = cen / pairs[k] / np.outer(sd, sd)
        cells = np.triu_indices(D.NS, 1) if t == "within_player" else np.triu_indices(D.NS)
        rec = {"pairs": float(pairs[k]), "observed": oc.tolist()}
        for c in names:
            sc = sim_m[c][k] / pairs[k]
            if t == "within_player":
                sd_s = np.sqrt(np.maximum(np.diag(sc), 1e-12))
                sc = sc / np.outer(sd_s, sd_s)
            rec[f"sim_{c}"] = sc.tolist()
            diff = (oc - sc)[cells]
            rec[f"rmse_{c}"] = float(np.sqrt((diff ** 2).mean()))
            x = sc[cells]
            rec[f"slope_{c}"] = float((x * oc[cells]).sum() / (x * x).sum()) if (x * x).sum() > 0 else None
        out[t] = rec
    out["realized_sd_of_standardized_residual"] = dict(zip(L.STATS, sd.tolist()))
    out["mean_standardized_residual"] = dict(zip(L.STATS, m.tolist()))
    return out


def reliability(rl: pd.DataFrame, names) -> dict:
    edges = np.linspace(0, 1, 11)
    out = {}
    for cb, r in rl.groupby("combo", sort=True):
        rec = {}
        for c in names:
            b = np.clip(np.digitize(r[f"p_{c}"], edges[1:-1]), 0, 9)
            tab = r.assign(b=b).groupby("b").agg(n=("outcome", "size"), pred=(f"p_{c}", "mean"), obs=("outcome", "mean"))
            rec[c] = {"table": tab.reset_index().to_dict(orient="records"),
                      "ece": float((tab["n"] * (tab["pred"] - tab["obs"]).abs()).sum() / tab["n"].sum())}
        out[cb] = rec
    return out


# ============================================================================ decision
def stability(fit_a: dict, fit_b: dict) -> dict:
    def cells(f, comp):
        if comp == "within_player":
            R = np.asarray(f["within_player"]["global"]["raw"])
            Rt = np.asarray(f["within_player"]["global"]["R"])
            iu = np.triu_indices(D.NS, 1)
            return R[iu], Rt[iu]
        ju = np.triu_indices(D.NS)
        return np.asarray(f[comp]["raw"])[ju], np.asarray(f[comp]["thresholded"])[ju]
    out = {}
    for comp in ("within_player", "same_team", "cross_team"):
        ra, ta = cells(fit_a, comp)
        rb, tb = cells(fit_b, comp)
        corr = float(np.corrcoef(ra, rb)[0, 1])
        sa = _sign_agree(ta, tb)
        out[comp] = {"raw_corr": corr, "sign_agreement_nonzero_both": sa, "nonzero_a": int((ta != 0).sum()),
                     "nonzero_b": int((tb != 0).sum()),
                     "pass": bool(corr >= STABILITY_MIN_CORR and (sa is None or sa >= STABILITY_MIN_SIGN_AGREEMENT))}
    return out


def decide(summary: dict, stab: dict) -> dict:
    d = summary["deltas"]
    mp = summary["marginal_preservation"]
    lv = summary["levels"]

    def vs_j0(c):
        x = d[f"{c}-J0"]
        ck = {"marginal_preservation": mp[c]["PASS"],
              "valid_matrices": summary["construction"][c]["INVALID_CORRELATION_MATRICES"] == 0,
              "improves_ES_or_VS": any(x[m]["hi"] < 0 for m in ("ES", "VS_BAL")),
              "majors_improving": [m for m in MAJOR if x[m]["value"] < 0],
              "majors_significantly_worse": [m for m in MAJOR if x[m]["lo"] > 0]}
        ck["directional"] = len(ck["majors_improving"]) >= 3 and not ck["majors_significantly_worse"]
        deg = []
        for cb in J.COMBOS:
            lg, cr = x[f"{cb}_LOG"], x[f"{cb}_CRPS"]
            if (lg["value"] > COMBO_DEGRADE_NATS and lg["lo"] > 0) or \
               (cr["value"] > COMBO_DEGRADE_CRPS_REL * lv["J0"][f"{cb}_CRPS"]["value"] and cr["lo"] > 0):
                deg.append(cb)
        ck["combos_materially_degraded"] = deg
        comps = ["within_player"] if c == "J1" else ["within_player", "same_team", "cross_team"]
        ck["stable"] = all(stab[k]["pass"] for k in comps)
        ck["PASS"] = bool(ck["marginal_preservation"] and ck["valid_matrices"] and ck["improves_ES_or_VS"]
                          and ck["directional"] and not deg and ck["stable"])
        return ck

    j1, j2 = vs_j0("J1"), vs_j0("J2")
    x = d["J2-J1"]
    add = {"improves_ES_or_VS_vs_J1": any(x[m]["hi"] < 0 for m in ("ES", "VS_BAL")),
           "improving_vs_J1": [m for m in ("ES", "VS_BAL", "EVENT_BRIER") if x[m]["value"] < 0],
           "majors_significantly_worse_vs_J1": [m for m in MAJOR if x[m]["lo"] > 0]}
    add["PASS"] = bool(add["improves_ES_or_VS_vs_J1"] and len(add["improving_vs_J1"]) >= 2
                       and not add["majors_significantly_worse_vs_J1"])
    sel = "J2" if (j2["PASS"] and add["PASS"]) else ("J1" if j1["PASS"] else "J0")
    # Monte Carlo adequacy: no decision-relevant interval bound (ES / VS_BAL, J1-J0, J2-J0, J2-J1) lies within
    # MC_DECISION_RATIO Monte Carlo SEs of zero
    margins = {}
    for k, se in summary["mc_se"].items():
        pair, m = k.split("|")
        if pair not in ("J1-J0", "J2-J0", "J2-J1"):
            continue
        v = d[pair][m]
        margins[k] = {"delta": v["value"], "ci_hi": v["hi"], "mc_se": se,
                      "hi_to_mc_se": abs(v["hi"]) / se if se > 0 else None}
    mc_ok = all(m["hi_to_mc_se"] is None or m["hi_to_mc_se"] >= MC_DECISION_RATIO for m in margins.values())
    return {"J1_vs_J0": j1, "J2_vs_J0": j2, "J2_adds_value_vs_J1": add, "SELECTED_DEPENDENCE_MODEL": sel,
            "mc_margins": margins, "MC_ADEQUATE": bool(mc_ok),
            "draws_per_game": summary.get("draws_per_game", J.SIMS)}


# ============================================================================ stages
def stage_select(log=print) -> dict:
    if SELECTION_PATH.exists():
        raise RuntimeError("the selection record exists; the selection stage does not rerun")
    prot_commit = M.committed_unchanged(PROTOCOL_PATH)
    g = guards()
    pop_fit, acc_fit = D.load_population(STORE, SELECT_FIT)
    pop_eval, acc_eval = D.load_population(STORE, [SELECT_EVAL])
    seasons_loaded = sorted(set(pop_fit["forecast_season"]) | set(pop_eval["forecast_season"]))
    if CONFIRM_EVAL in seasons_loaded:
        raise RuntimeError("2025-26 loaded in the selection stage")
    log("fitting 2023-24 dependence (MID + RAND)")
    fr = fit_record(pop_fit, acc_fit, "SELECTION_FIT_2023_24")
    _dump(SEL_FIT_PATH, compact_fit(fr) | {"protocol_sha256": _sha(PROTOCOL_PATH), "protocol_commit": prot_commit})
    sts = structures(fr)
    scale = np.asarray([fr["scale"][s] for s in L.STATS])
    log("evaluating on 2024-25")
    summ = evaluate(pop_eval, sts, scale, "select_2024_25", log)
    log("stability diagnostic: 2024-25-only fit (never used to forecast)")
    diag_fit = D.fit(pop_eval, "MID")
    stab = stability(fr["MID"], diag_fit)
    dec = decide(summ, stab)
    summ_low = None
    if not dec["MC_ADEQUATE"]:                                    # protocol: rerun with 50,000 draws
        log("Monte Carlo margin inadequate at %d draws: rerunning with %d" % (J.SIMS, MC_RERUN_DRAWS))
        summ_low = summ
        summ = evaluate(pop_eval, sts, scale, "select_2024_25_50k", log, sims=MC_RERUN_DRAWS)
        dec = decide(summ, stab)
    out = {"version": VERSION, "stage": "SELECTION", "protocol_sha256": _sha(PROTOCOL_PATH), "protocol_commit": prot_commit,
           "selection_fit_record_sha256": _sha(SEL_FIT_PATH), "fit_seasons": list(SELECT_FIT),
           "evaluation_season": SELECT_EVAL, "seasons_loaded": seasons_loaded, "season_2025_26_loaded": False,
           "evaluation_population": acc_eval, "guards": g, "code": code_vs_protocol(),
           "summary": summ, "summary_at_%d_draws_superseded" % J.SIMS: summ_low, "stability": stab, "decision": dec,
           "SELECTED_DEPENDENCE_MODEL": dec["SELECTED_DEPENDENCE_MODEL"],
           "DEPENDENCE_SELECTED_BEFORE_2025_26": True}
    _dump(SELECTION_PATH, out)
    return out


def stage_confirm(log=print) -> dict:
    if CONFIRM_PATH.exists():
        raise RuntimeError("the 2025-26 retrospective confirmation runs exactly once; its record already exists")
    sel_commit = M.committed_unchanged(SELECTION_PATH)
    sel = json.loads(SELECTION_PATH.read_text())["SELECTED_DEPENDENCE_MODEL"]
    g = guards()
    pop_fit, acc_fit = D.load_population(STORE, CONFIRM_FIT)
    pop_eval, acc_eval = D.load_population(STORE, [CONFIRM_EVAL])
    log("fitting the frozen procedure through 2024-25 (MID + RAND)")
    fr = fit_record(pop_fit, acc_fit, "CONFIRMATION_FIT_THROUGH_2024_25")
    _dump(CONFIRM_FIT_PATH, compact_fit(fr) | {"selection_record_sha256": _sha(SELECTION_PATH),
                                               "selection_record_commit": sel_commit})
    sts = structures(fr)
    scale = np.asarray([fr["scale"][s] for s in L.STATS])
    log("evaluating once on 2025-26")
    summ = evaluate(pop_eval, sts, scale, "confirm_2025_26", log)
    sel_fit = json.loads(SEL_FIT_PATH.read_text())
    stab = stability(sel_fit["MID"], fr["MID"])
    dec = decide(summ, stab)
    out = {"version": VERSION, "stage": "RETROSPECTIVE_CONFIRMATION_2025_26",
           "nature": "retrospective confirmation; the selected dependence model is not changed by this result",
           "selected_dependence_model": sel, "selection_record_commit": sel_commit,
           "selection_record_sha256": _sha(SELECTION_PATH), "confirmation_fit_record_sha256": _sha(CONFIRM_FIT_PATH),
           "fit_seasons": list(CONFIRM_FIT), "evaluation_season": CONFIRM_EVAL, "evaluation_population": acc_eval,
           "guards": g, "code": code_vs_protocol(), "summary": summ,
           "stability_selection_fit_vs_confirmation_fit": stab,
           "rule_applied_to_2025_26_for_information_only": dec,
           "selected_model_confirmed": dec["SELECTED_DEPENDENCE_MODEL"] == sel}
    _dump(CONFIRM_PATH, out)
    return out


def artifact_path(sel: str):
    return OUT / f"dependence_{sel}.json"


def stage_final(log=print) -> dict:
    conf_commit = M.committed_unchanged(CONFIRM_PATH)
    sel = json.loads(CONFIRM_PATH.read_text())["selected_dependence_model"]
    g = guards()
    pop, acc = D.load_population(STORE, FINAL_FIT)
    log(f"final fit of {sel} on 2023-24..2025-26 OOS pairs")
    f = D.fit(pop, "MID")
    keep = {"J0": (), "J1": ("within_player",), "J2": ("within_player", "same_team", "cross_team", "shared")}[sel]
    art = {"version": VERSION, "role": "V2_CHALLENGER_DEPENDENCE", "SELECTED_DEPENDENCE_MODEL": sel,
           "MARGINAL_ARTIFACT": _rel(MARGINAL_PATH), "MARGINAL_ARTIFACT_SHA256": MARGINAL_SHA,
           "marginal_preserving": "unit-diagonal latent Gaussian copula over the Phase 1B marginals; the marginal "
                                  "artifact is not modified and its PMFs are reproduced exactly",
           "conditioning": "joint distribution of the counts of players who play (P(play) stays separate)",
           "construction": "cbb_platform/v2/dependence.py: structure() + game_correlation() + cholesky_unit(); "
                           "cbb_platform/v2/joint.py: simulate()",
           "fit": {"seasons": list(FINAL_FIT), "source": _rel(STORE), "source_sha256": STORE_SHA,
                   "DEPENDENCE_FIT_ROWS": acc["DEPENDENCE_ELIGIBLE_PLAYER_GAMES"],
                   "DEPENDENCE_FIT_GAMES": acc["DEPENDENCE_ELIGIBLE_GAMES"],
                   "DEPENDENCE_FIT_PLAYERS": acc["DEPENDENCE_ELIGIBLE_PLAYERS"], "population": acc},
           "pit": "MID", "stats": list(L.STATS), "parameters": {k: f[k] for k in keep},
           "normalization": f["normalization"], "psd_repairs_at_fit": f["psd_repairs_at_fit"],
           "protocol_sha256": _sha(PROTOCOL_PATH), "selection_record_sha256": _sha(SELECTION_PATH),
           "confirmation_record_sha256": _sha(CONFIRM_PATH), "confirmation_record_commit": conf_commit,
           "PRODUCTION_SEASON": 2027, "OUTCOME_DATA_THROUGH": 2026}
    path = artifact_path(sel)
    _dump(path, art)
    # construction / preservation check of the final structure on the last 200 games of 2025-26 (no outcomes used)
    st = {"J0": D.structure("J0", None)}
    if sel != "J0":
        st[sel] = D.structure(sel, f)
    sample = pop[pop["forecast_season"] == 2026]
    sample = sample[sample["game_id"].isin(sorted(sample["game_id"].unique())[-200:])]
    chk = {"games": 0, "invalid": 0, "min_eig": np.inf, "max_diag_err": 0.0}
    for _, gp in sample.groupby("game_id", sort=True):
        team = (gp["team_id"] != gp["team_id"].min()).astype(int).to_numpy()
        for c, s in st.items():
            gc = D.game_correlation(s, list(D.group_of(gp)), team)
            chk["games"] += c == sel
            chk["invalid"] += int(gc["invalid"])
            chk["min_eig"] = min(chk["min_eig"], gc["min_eig"])
            chk["max_diag_err"] = max(chk["max_diag_err"], gc["diag_err"])
    ver = {"version": VERSION, "artifact": _rel(path), "artifact_sha256": _sha(path), "guards": g,
           "V1_ARTIFACT_UNCHANGED": g["V1_ARTIFACT_UNCHANGED"],
           "V2_MARGINAL_ARTIFACT_UNCHANGED": g["V2_MARGINAL_ARTIFACT_UNCHANGED"],
           "final_structure_check_last_200_games_2025_26": chk, "code": code_vs_protocol(),
           "HISTORICAL_API_CALLS": 0, "2026_27_OUTCOME_DATA_TOUCHED": False}
    _dump(VERIFY_PATH, ver)
    return ver


def load_artifact(path) -> D.Structure:
    a = json.loads(path.read_text())
    if a["MARGINAL_ARTIFACT_SHA256"] != MARGINAL_SHA:
        raise RuntimeError("dependence artifact references another marginal")
    sel = a["SELECTED_DEPENDENCE_MODEL"]
    return D.structure(sel, a["parameters"] if sel != "J0" else None)


if __name__ == "__main__":
    stage = sys.argv[1]
    fn = {"protocol": stage_protocol, "select": stage_select, "confirm": stage_confirm, "final": stage_final}[stage]
    r = fn()
    print(json.dumps({k: v for k, v in r.items() if k in ("SELECTED_DEPENDENCE_MODEL", "selected_dependence_model",
                                                          "selected_model_confirmed", "artifact", "artifact_sha256")},
                     indent=1, default=_json_default))
