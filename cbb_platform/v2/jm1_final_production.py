"""CBB V2 JM1_FINAL production candidate: the FINAL authorized refit of the confirmed JM1_FINAL procedure through
2025-26, its frozen artifacts, a deterministic production scorer and the production-integrity checks.

The architecture is frozen by cbb_platform/v2/jm1_final.py (protocol 101dc64, confirmation 6898df9, PASS). This module
calls jm1_final.fit_procedure unchanged with the final chronology; it selects, tunes and searches nothing.

    .venv/bin/python -m cbb_platform.v2.jm1_final_production final   refit through 2025-26 (twice: determinism) + freeze

Rollback model: S0 = Phase 1B marginals + Phase 2 J2 (artifacts never written here). No deployment, no API calls, no
2026-27 outcome is read (structural / jm1_final season guards: max season 2026).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v1_pricing as PR
from cbb_platform.v2 import dependence as D, jm1_final as F, joint as J, joint_minutes_v2 as JM, latent as L
from cbb_platform.v2 import marginal as M, phase2 as P2, phase3 as P3, provenance as PV, structural as S

VERSION = "cbb-v2-jm1-final-production-1.0.0"
OUT = M.OUT_DIR
ARTIFACT_PATH = OUT / "jm1_final_through_2025_26.json"
RESID_DEP_PATH = OUT / "jm1_final_residual_dependence_through_2025_26.json"
MANIFEST_PATH = OUT / "jm1_final_production_manifest_2026_27.json"
REPORT_PATH = OUT / "JM1_FINAL_PRODUCTION_FREEZE_REPORT.md"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_jm1_final"
FINAL = {"T": 2026, "resid_seasons": (2024, 2025, 2026)}     # supervised + historical state through 2025-26
PRODUCTION_SEASON = 2027
ROLLBACK = {"model": "S0 = Phase 1B marginals + Phase 2 J2",
            "MARGINAL_ARTIFACT": "modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json",
            "MARGINAL_ARTIFACT_SHA256": "bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f",
            "PHASE2_DEPENDENCE_ARTIFACT": "modeling/player_props_v2/dependence_J2.json",
            "PHASE2_DEPENDENCE_ARTIFACT_SHA256": "b41564a988f37a4c01a0dec9f8a9cb67aea69717294809b3eaa49acf4babb1ff"}
INTEGRITY_GAMES = 3
INTEGRITY_SIMS = 4000
INTEGRITY_SEASON = 2026          # mechanical checks only (in-sample for the final fit; no metric is computed)

_sha, _rel, _dump, _jd = JM._sha, P2._rel, P2._dump, P2._json_default


class ProductionFreezeViolation(RuntimeError):
    pass


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, default=_jd, separators=(",", ":"))


DET_RTOL = 1e-12                 # reproduction tolerance on floats (non-float content must match exactly)


def numeric_diff(a, b) -> dict:
    """Structural equality of two JSON trees with floats compared at DET_RTOL (relative, abs floor 1e-15)."""
    out = {"floats_compared": 0, "floats_differing": 0, "max_rel_diff": 0.0, "mismatch": []}

    def walk(x, y, p):
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
        if not (num(x) and num(y)):
            if type(x) is not type(y):
                out["mismatch"].append(p)
            elif isinstance(x, dict):
                if set(x) != set(y):
                    out["mismatch"].append(p)
                for k in sorted(set(x) & set(y)):
                    walk(x[k], y[k], f"{p}/{k}")
            elif isinstance(x, list):
                if len(x) != len(y):
                    out["mismatch"].append(p)
                for i, (u, v) in enumerate(zip(x, y)):
                    walk(u, v, f"{p}[{i}]")
            elif x != y:
                out["mismatch"].append(p)
            return
        if isinstance(x, float) or isinstance(y, float):
            out["floats_compared"] += 1
            if x != y:
                out["floats_differing"] += 1
                r = abs(x - y) / max(abs(x), abs(y), 1e-15)
                out["max_rel_diff"] = max(out["max_rel_diff"], r)
                if r > DET_RTOL:
                    out["mismatch"].append(p)
        elif x != y:
            out["mismatch"].append(p)

    walk(a, b, "")
    out["NUMERICALLY_IDENTICAL"] = not out["mismatch"]
    out["mismatch"] = out["mismatch"][:20]
    return out


def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=C.PROJECT_ROOT, capture_output=True, text=True, check=True).stdout.strip()


# ============================================================================ artifacts
def build_artifacts(params: dict) -> tuple[dict, dict]:
    """(structural JM1_FINAL artifact, residual dependence artifact) from one fit_procedure result; deterministic content
    (no timestamps) so two fits can be compared byte for byte."""
    s4 = params["s4"]
    dep = {"version": VERSION, "role": "V2_JM1_FINAL_RESIDUAL_DEPENDENCE (production candidate; not deployed)",
           "SELECTED_DEPENDENCE_MODEL": "J2", "architecture": "JM1_FINAL residual J2 (dependence.fit MID, unchanged)",
           "MARGINAL_ARTIFACT_SHA256": P3.MARGINAL_SHA,
           "conditioning": "latent Gaussian copula over the JM1_FINAL CONDITIONAL count distributions given the structural "
                           "state (random player minutes x D / E[D], shared game environment)",
           "fit": {"source": "true-OOS structural residual basis: season F with structural parameters fitted through F-1",
                   "seasons": params["resid_seasons"], "rows": s4["fit_rows"], "games": s4["fit_games"],
                   "structures_used": s4["structures_used"], "population": s4["residual_basis_population"]},
           "pit": "MID", "stats": list(L.STATS), "parameters": s4["residual_dependence"],
           "PRODUCTION_SEASON": PRODUCTION_SEASON, "OUTCOME_DATA_THROUGH": FINAL["T"]}
    art = {"version": VERSION, "role": "V2_JM1_FINAL_STRUCTURAL (production candidate; not deployed)",
           "architecture": "JM1_FINAL", "jm1_final_version": F.VERSION,
           "MARGINAL_ARTIFACT": ROLLBACK["MARGINAL_ARTIFACT"], "MARGINAL_ARTIFACT_SHA256": P3.MARGINAL_SHA,
           "structure": P3._compact_struct(params["struct"]), "terms": list(JM.JM_TERMS), "alpha": JM.JM1_ALPHA,
           "e_dur": params["e_dur"], "conditional_dispersion": s4["dispersion"], "families": S.FAMILIES,
           "descriptive_scale": s4["scale"], "kmax_minutes": JM.KMAX, "kmax_counts": L.KMAX,
           "exposure": "M_ib = clip(min_mu_i + min_sigma_i eps_ib, 0, %d) x D_b / E[D]; eps iid N(0,1) from the 'minutes' "
                       "stream over every candidate of the game; no team budget / reservoir / reconciliation" % JM.KMAX,
           "seeds": {"structural_streams": "structural.stream_seed(%d, game_id, stream): duration, environment, minutes"
                                           % JM.SEED_BASE,
                     "count_latents": "dependence.game_seed(%d, game_id)" % J.SIM_SEED},
           "fit": {"structure_through": params["T"], "glm_seasons": params["struct"]["glm"]["seasons"],
                   "residual_basis_seasons": params["resid_seasons"], "residual_basis_rows": params["resid_rows"]},
           "PRODUCTION_SEASON": PRODUCTION_SEASON, "SUPERVISED_TRAINING_THROUGH": FINAL["T"],
           "HISTORICAL_STATE_THROUGH": FINAL["T"]}
    return art, dep


def load_production(art_path=ARTIFACT_PATH, dep_path=RESID_DEP_PATH) -> dict:
    """The frozen production state in the form jm1_final.eval_game / score_game consume."""
    a, d = json.loads(art_path.read_text()), json.loads(dep_path.read_text())
    if a["MARGINAL_ARTIFACT_SHA256"] != P3.MARGINAL_SHA or d["MARGINAL_ARTIFACT_SHA256"] != P3.MARGINAL_SHA:
        raise ProductionFreezeViolation("artifact references another Phase 1B marginal")
    return {"struct": {"env": a["structure"]["env"], "glm": a["structure"]["glm"], "T": a["structure"]["T"]},
            "s4": {"dispersion": a["conditional_dispersion"], "residual_dependence": d["parameters"]},
            "e_dur": a["e_dur"], "residual_structure": D.structure("J2", d["parameters"])}


# ============================================================================ production scorer
def score_game(prod: dict, gid: str, gp: pd.DataFrame, envd: dict, sims: int) -> dict:
    """Joint draws for the priced players of one game (gp["_scored"] = priced set), conditional on each priced player
    playing; P(play) is passed through beside the prices and never enters the conditional draws."""
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
    lm, _ = S.anchor(S.log_mu_terms(base_lm, rows["min_mu"].to_numpy(dtype=float), team, sv, prod["struct"]["glm"],
                                    JM.JM_TERMS), base_lm, JM.JM1_ALPHA)
    tab = S.mixture_tables(lm.reshape(sims, n * J.NS), np.array([prod["s4"]["dispersion"][s]["phi"] for s in L.STATS]))
    draws = S.counts(tab, z).reshape(sims, n, J.NS)
    return {"rows": rows, "draws": draws, "pmf": S.mixture_pmf(tab, sims).reshape(n, J.NS, -1), "M": Mx, "dur": dur,
            "lm": lm, "p_play": rows["p_play"].to_numpy(dtype=float), "corr_min_eig": gc["min_eig"],
            "corr_invalid": bool(gc["invalid"]), "jitter": jit}


# ============================================================================ integrity
def integrity_checks(prod: dict, log=print) -> dict:
    """Mechanical production-integrity checks of the frozen scorer on a few 2025-26 games (in-sample for the final fit:
    no predictive metric is computed or reported)."""
    df, envp = P3.season_inputs(INTEGRITY_SEASON, prod["struct"])
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    out = {"season": INTEGRITY_SEASON, "games": INTEGRITY_GAMES, "sims": INTEGRITY_SIMS, "checks": {}}
    ck = {k: True for k in ("pplay_separate", "one_minutes_draw_feeds_7_stats", "same_draw_combos", "psd_game_correlation",
                            "pmf_normalized", "pricing_push_exact", "deterministic", "matches_confirmed_eval_game")}
    detail = {}
    for gid, gp in list(df.groupby("game_id", sort=True))[:INTEGRITY_GAMES]:
        gp = gp.copy()
        gp["_scored"] = gp["p_play"] > 0                     # price every candidate (conditional on playing)
        r = score_game(prod, gid, gp, envd, INTEGRITY_SIMS)
        n = len(r["rows"])
        # P(play) separate: changing every P(play) leaves the conditional draws bit-identical
        gp2 = gp.copy()
        gp2["p_play"] = np.clip(1.0 - gp2["p_play"], 0.01, 0.99)
        r2 = score_game(prod, gid, gp2, envd, INTEGRITY_SIMS)
        ck["pplay_separate"] &= bool(np.array_equal(r["draws"], r2["draws"]) and np.array_equal(r["M"], r2["M"]))
        # one minutes draw per (draw, player) feeds all seven stats: perturb one (draw, player) minutes value
        gi = P3._game_input(gid, gp, envd)
        team = (r["rows"]["team_id"] != r["rows"]["team_id"].min()).astype(int).to_numpy()
        base_lm = np.log(r["rows"][[f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float))
        mm = r["rows"]["min_mu"].to_numpy(dtype=float)
        dur, x, Mx = F.simulate_exposure(gi, prod["struct"]["env"], prod["e_dur"], 50)
        st = S.State(dur, x, Mx.copy(), np.zeros((50, n, len(S.FAM))), np.zeros((50, 0)), {}, np.zeros((n, len(S.FAM))), 0.0)
        a = S.log_mu_terms(base_lm, mm, team, st, prod["struct"]["glm"], JM.JM_TERMS)
        st.M[3, 0] = st.M[3, 0] + 5.0
        dd = S.log_mu_terms(base_lm, mm, team, st, prod["struct"]["glm"], JM.JM_TERMS) - a
        ck["one_minutes_draw_feeds_7_stats"] &= bool(np.count_nonzero(dd) == J.NS and np.count_nonzero(dd[3, 0]) == J.NS
                                                     and Mx.shape == (50, n))
        # combos are sums of the same simulated stat draws
        der = J.derived_markets(r["draws"])
        ck["same_draw_combos"] &= all(np.array_equal(der[c], r["draws"][:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2))
                                      for c, parts in J.COMBOS.items())
        ck["psd_game_correlation"] &= bool((not r["corr_invalid"]) and r["corr_min_eig"] > -1e-9)
        pm = r["pmf"]
        ck["pmf_normalized"] &= bool((pm >= -1e-15).all() and np.abs(pm.sum(axis=2) - 1).max() < 1e-9)
        # exact pricing primitive (props_v1_pricing.price): integer line pushes, half line never pushes
        flat = pm.reshape(n * J.NS, -1)
        for line in (2.0, 2.5):
            p = PR.price(flat, line)
            tot = p["over_probability"] + p["under_probability"] + p["push_probability"]
            push_ok = np.allclose(p["push_probability"], flat[:, 2]) if line == 2.0 else np.all(p["push_probability"] == 0)
            ck["pricing_push_exact"] &= bool(np.abs(tot - 1).max() < 1e-9 and push_ok)
        # deterministic
        r3 = score_game(prod, gid, gp, envd, INTEGRITY_SIMS)
        ck["deterministic"] &= bool(np.array_equal(r["draws"], r3["draws"]))
        # the production scorer reproduces the confirmed evaluation code path (jm1_final.eval_game) on its scored rows
        gpe = gp.copy()
        gpe["_scored"] = P3.scored_mask(gpe)
        if gpe["_scored"].any():
            ev = F.eval_game(gid, gpe, envd, prod, D.structure("J0", None), prod["residual_structure"],
                             np.ones(J.NS), sims=INTEGRITY_SIMS)
            rs = score_game(prod, gid, gpe, envd, INTEGRITY_SIMS)
            mean_p = (rs["pmf"] * np.arange(rs["pmf"].shape[2])).sum(axis=2).ravel()
            ck["matches_confirmed_eval_game"] &= bool(np.allclose(mean_p, ev["single"][F.CAND]["mean"], rtol=1e-12, atol=1e-12)
                                                      and np.array_equal(rs["M"], ev["M"]))
        detail[gid] = {"priced_players": n, "corr_min_eig": float(r["corr_min_eig"]), "jitter": float(r["jitter"])}
        log(f"integrity {gid}: {n} priced players")
    out["checks"] = ck
    out["per_game"] = detail
    out["ALL_PASS"] = bool(all(ck.values()))
    return out


# ============================================================================ the final stage
def _file_id(p) -> dict:
    return {"path": _rel(p), "bytes": p.stat().st_size, "sha256": _sha(p)}


def stage_final(log=print) -> dict:
    for p in (ARTIFACT_PATH, RESID_DEP_PATH, MANIFEST_PATH):
        if p.exists():
            raise RuntimeError(f"{_rel(p)} exists; the final freeze runs once")
    conf = json.loads(F.CONFIRM_PATH.read_text())
    if conf["JM1_FINAL_CONFIRMATION_DECISION"] != "PASS":
        raise ProductionFreezeViolation("JM1_FINAL did not pass its confirmation")
    prot_commit = M.committed_unchanged(F.PROTOCOL_PATH)
    conf_commit = M.committed_unchanged(F.CONFIRM_PATH)
    prot = json.loads(F.PROTOCOL_PATH.read_text())
    code_now = F.code_state()
    changed = sorted(k for k in code_now if code_now[k] != prot["code_sha256_at_freeze"].get(k))
    if changed:
        raise ProductionFreezeViolation(f"the frozen JM1_FINAL code changed: {changed}")
    g = F.guards()
    # final fit, twice: deterministic reproduction of the artifact content
    fits = []
    for k in (1, 2):
        log(f"final fit {k}/2: structure through {FINAL['T']}, residual basis {FINAL['resid_seasons']}")
        params = F.fit_procedure(FINAL["T"], FINAL["resid_seasons"], log)
        art, dep = build_artifacts(params)
        fits.append((params, art, dep, hashlib.sha256(_canon(art).encode()).hexdigest(),
                     hashlib.sha256(_canon(dep).encode()).hexdigest()))
    params, art, dep, ha, hd = fits[0]
    det = {"fits": 2, "artifact_content_sha256": [f[3] for f in fits], "dependence_content_sha256": [f[4] for f in fits],
           "BIT_IDENTICAL": bool(fits[0][3] == fits[1][3] and fits[0][4] == fits[1][4]),
           "float_rtol": DET_RTOL, "frozen": "fit 1"}
    det["artifact_numeric"] = numeric_diff(json.loads(_canon(fits[0][1])), json.loads(_canon(fits[1][1])))
    det["dependence_numeric"] = numeric_diff(json.loads(_canon(fits[0][2])), json.loads(_canon(fits[1][2])))
    det["IDENTICAL"] = bool(det["artifact_numeric"]["NUMERICALLY_IDENTICAL"] and det["dependence_numeric"]["NUMERICALLY_IDENTICAL"])
    if not det["BIT_IDENTICAL"]:
        det["note"] = ("last-bit float noise only: structural.load_team_frame reads feat.team_features through a "
                       "multi-threaded DuckDB query without ORDER BY, so the env_fit lstsq row order (and its float "
                       "summation order) can vary run to run; the frozen JM1_FINAL code is not changed. The frozen "
                       "artifact is the source of truth and scoring from it is bit-deterministic.")
    if not det["IDENTICAL"]:
        raise ProductionFreezeViolation(f"final fit is not deterministic: {det}")
    if max(params["resid_seasons"]) > FINAL["T"] or params["struct"]["T"] != FINAL["T"]:
        raise ProductionFreezeViolation("chronology")
    _dump(RESID_DEP_PATH, dep)
    art["RESIDUAL_DEPENDENCE_ARTIFACT"] = _rel(RESID_DEP_PATH)
    art["RESIDUAL_DEPENDENCE_ARTIFACT_SHA256"] = _sha(RESID_DEP_PATH)
    _dump(ARTIFACT_PATH, art)
    prod = load_production()
    integ = integrity_checks(prod, log)
    if not integ["ALL_PASS"]:
        raise ProductionFreezeViolation(f"production integrity: {integ['checks']}")
    j2 = P3.j2_comparison(json.loads(P3.J2_PATH.read_text())["parameters"], params["s4"]["residual_dependence"])
    g_after = F.guards()
    man = {
        "version": VERSION, "architecture": "JM1_FINAL", "status": "FROZEN PRODUCTION CANDIDATE (not deployed)",
        "production_season": PRODUCTION_SEASON, "supervised_training_through": FINAL["T"],
        "historical_state_through": FINAL["T"],
        "artifacts": {"marginal": {"path": ROLLBACK["MARGINAL_ARTIFACT"], "sha256": _sha(C.PROJECT_ROOT / ROLLBACK["MARGINAL_ARTIFACT"])},
                      "jm1_final_structural": _file_id(ARTIFACT_PATH),
                      "conditional_dispersion": {"in": _rel(ARTIFACT_PATH), "key": "conditional_dispersion",
                                                 "content_sha256": hashlib.sha256(_canon(art["conditional_dispersion"]).encode()).hexdigest()},
                      "residual_dependence": _file_id(RESID_DEP_PATH),
                      "shared_environment": {"in": _rel(ARTIFACT_PATH), "key": "structure.env",
                                             "content_sha256": hashlib.sha256(_canon(art["structure"]["env"]).encode()).hexdigest()}},
        "provenance": {"protocol": _file_id(F.PROTOCOL_PATH) | {"commit": prot_commit},
                       "development_evidence": _file_id(F.DEV_EVIDENCE_PATH),
                       "confirmation_record": _file_id(F.CONFIRM_PATH) | {"commit": conf_commit, "decision": "PASS"},
                       "confirmation_parameters": _file_id(F.CONFIRM_PARAMS_PATH),
                       "jm2_postmortem": _file_id(OUT / "jm2_postmortem.json") | {"commit": "ac84ff4"},
                       "code_commit": _git("rev-parse", "HEAD"), "code_sha256": code_now | {
                           _rel(C.PROJECT_ROOT / "cbb_platform" / "v2" / "jm1_final_production.py"):
                               _sha(C.PROJECT_ROOT / "cbb_platform" / "v2" / "jm1_final_production.py")}},
        "data_versions": {"phase1b_oos_store": _file_id(P2.STORE), "phase1b_F2023_extension": _file_id(P3.PP.STORE_PATH),
                          "features_duckdb": _file_id(PV.LB_FEATURES), "labels_duckdb": _file_id(PV.LB_LABELS)},
        "oos_residual_basis": {"seasons": params["resid_seasons"], "rows": params["s4"]["fit_rows"],
                               "games": params["s4"]["fit_games"], "structures_used": params["s4"]["structures_used"],
                               "rule": "season F scored with structural parameters fitted through F-1 (true OOS)"},
        "random_seed_policy": art["seeds"] | {"draws_per_game": F.SIMS},
        "supported_stats": list(L.STATS), "supported_combos": {k: list(v) for k, v in J.COMBOS.items()},
        "pricing": "conditional on playing; P(play) reported beside the prices, never mixed into the PMF; exact push "
                   "handling via cbb_platform/props_v1_pricing.price",
        "known_monitoring_warnings": {
            "A_PTS_MEAN_BIAS": "2025-26 confirmation PTS mean bias S0 +0.033 vs JM1_FINAL +0.212: track PTS calibration / "
                               "mean bias from the first slate",
            "B_HIGH_COMBO_OVERS": "q75 threshold Brier slightly worse than S0 for PRA (+0.0009), PR (+0.0008), PA (+0.0006): "
                                  "monitor high combo lines separately",
            "C_SAME_TEAM_CORRELATION": "same-team correlation calibration RMSE S0 0.0051 vs JM1_FINAL 0.0161 (slope 0.37): "
                                       "residual same-team dependence is a known weakness",
            "policy": "recorded only; NOT repaired (architect ruling)"},
        "rollback_model": ROLLBACK | {"ROLLBACK_ARTIFACTS_UNCHANGED": bool(g_after["V2_PHASE1B_ARTIFACT_UNCHANGED"]
                                                                          and g_after["PHASE2_J2_ARTIFACT_UNCHANGED"])},
        "deterministic_final_fit": det, "production_integrity": integ,
        "old_J2_vs_final_residual_J2": j2, "guards_before": g, "guards_after": g_after,
        "HISTORICAL_API_CALLS": 0, "2026_27_OUTCOME_DATA_TOUCHED": False, "DEPLOYMENT_PERFORMED": False}
    _dump(MANIFEST_PATH, man)
    return man


if __name__ == "__main__":
    if (sys.argv[1] if len(sys.argv) > 1 else "") != "final":
        raise SystemExit("usage: python -m cbb_platform.v2.jm1_final_production final")
    m = stage_final(lambda s: print(s, flush=True))
    print(json.dumps({"manifest": _rel(MANIFEST_PATH), "sha256": _sha(MANIFEST_PATH),
                      "artifact_sha256": m["artifacts"]["jm1_final_structural"]["sha256"],
                      "residual_dependence_sha256": m["artifacts"]["residual_dependence"]["sha256"],
                      "deterministic": m["deterministic_final_fit"]["IDENTICAL"],
                      "integrity": m["production_integrity"]["ALL_PASS"]}, indent=1))
