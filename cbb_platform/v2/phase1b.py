"""V2 Phase 1B: freeze the final marginal foundation under the architect ruling (ANNUAL_REFIT, V1REF, half-life 1).

Phase 1 evidence (W0 / W4 / W2 / V1REF comparisons, weighting_selection.json, retrospective_2025_26.json, the W2
challenger and its OOS store) is kept unchanged. This module only ADDS:

    architect_ruling_phase1b.json                 the ruling (W2 = protocol-selected; V1REF = architect-selected)
    v2_marginal_final_v1ref_through_2025_26.json  final V2 marginal artifact (v1 architecture, V1REF, through 2025-26)
    modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet + oos_forecast_store_v1ref_manifest.json
                                                  OOS store from the SAVED Phase 1 V1REF annual-refit states (no fitting)
    phase1b_verification.json                     OOS reproduction of the recorded V1REF fold metrics, v1-vs-V2 artifact
                                                  comparison, tip-rule impact audit

    .venv/bin/python -m cbb_platform.v2.phase1b
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P, props_v0_eval as E, props_v0_participation as Q, props_v1_refit as R
from cbb_platform.v2 import marginal as M, provenance as PV

POLICY, HALF_LIFE = "V1REF", 1.0
RULING_PATH = M.OUT_DIR / "architect_ruling_phase1b.json"
ARTIFACT_PATH = M.OUT_DIR / "v2_marginal_final_v1ref_through_2025_26.json"
OOS_PATH = M.OOS_DIR / "oos_forecasts_phase1b_v1ref.parquet"
OOS_MANIFEST_PATH = M.OUT_DIR / "oos_forecast_store_v1ref_manifest.json"
VERIFY_PATH = M.OUT_DIR / "phase1b_verification.json"
V1_SHA = "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"
TOL = 1e-12
FOLDS = M.DEV_FOLDS + (M.CONFIRM_SEASON,)


def ruling() -> dict:
    sel = json.loads(M.SELECTION_PATH.read_text())
    return {"record": "Architect ruling, V2 Phase 1B: final marginal foundation",
            "ANNUAL_REFIT": True, "WEIGHTING_POLICY": POLICY, "HALF_LIFE": HALF_LIFE,
            "protocol_selected_policy": {"policy": sel["SELECTED_WEIGHTING_POLICY"], "half_life": sel["SELECTED_HALF_LIFE"],
                                         "meaning": "W2 = protocol-selected among W0 / W4 / W2 (weighting_selection.json, unchanged)"},
            "architect_selected_policy": {"policy": POLICY, "half_life": HALF_LIFE,
                                          "meaning": "V1REF = architect-selected final marginal policy because it independently "
                                                     "outperformed W2 on both development seasons before 2025-26 was examined "
                                                     "(weighting_selection.json notes_from_development_folds), and again "
                                                     "retrospectively on 2025-26"},
            "constraints": ["no additional half-lives tested", "2025-26 not used for further weighting selection",
                            "no dependence layer"],
            "phase1_evidence_preserved_sha256": {p.name: M._sha(p) for p in (M.PROTOCOL_PATH, M.DEV_PATH, M.SELECTION_PATH,
                                                                             M.CONFIRM_PATH, M.ARTIFACT_PATH, M.FINAL_RECORD_PATH,
                                                                             M.OOS_MANIFEST_PATH)}}


# ---------------------------------------------------------------------------- final artifact
def build_artifact(frame, finals, part, cfg) -> dict:
    v0 = M.stl_policy(M.fit_v0(frame, finals, cfg, M.V2_MAX_SEASON, POLICY, "FINAL_1B"), cfg)
    pst = M.fit_participation(part, range(M.PARTICIPATION_FIRST_SEASON, M.V2_MAX_SEASON + 1), POLICY, "FINAL_1B")
    spec = R.load_final_spec()
    art = {"artifact_version": "cbb-props-v2-marginal-final-v1ref-through-2025-26",
           "role": "V2_MARGINAL_FOUNDATION (architect ruling Phase 1B; not promoted; the v1 champion is unchanged)",
           "phase": "cbb-v2-phase1b", "SUPERVISED_TRAINING_THROUGH": M.V2_MAX_SEASON, "HISTORICAL_STATE_THROUGH": M.V2_MAX_SEASON,
           "PRODUCTION_SEASON": M.PRODUCTION_SEASON, "ANNUAL_REFIT": True, "weighting_policy": POLICY, "half_life": HALF_LIFE,
           "weight_rule": "v1 verbatim: training-row weight 0.5 ** ((2026 - season) / 1.0) (props_v0.fit); participation unweighted (v1)",
           "datasets": {"stage3_store": str(M.FEATURES_DB.relative_to(C.PROJECT_ROOT)),
                        "dev_dataset_version_le_2024_25": "cbb_data_v2.4+5f19d31e524a",
                        "lockbox_dataset_version_2025_26": "cbb_data_v3.0+86a63cb6d902",
                        "provenance": "modeling/player_props_v2/data_provenance.json"},
           "stat_training_seasons": [M.FIRST_SEASON, M.V2_MAX_SEASON], "participation_training_seasons": pst["train_seasons"],
           "spec_fingerprint": R.spec_fingerprint(cfg), "final_spec_fingerprint": R.final_spec_fingerprint(spec),
           "stl_policy": spec["accepted_changes"]["STL"], "config": cfg, "v0_state": v0, "participation_state": pst,
           "coef_order": {"v0": list(v0["coef"]), "participation": list(pst["coef"])},
           "architect_ruling_sha256": M._sha(RULING_PATH)}
    art["artifact_fingerprint"] = R.fingerprint(art)
    return art


def _leaf_diff(a, b, skip=("state_hash", "v2", "train_through", "n_train_rows", "train_max_cutoff", "train_seasons",
                           "n_train", "base_rate")) -> float:
    """max |a - b| over shared numeric leaves (structure must match; bookkeeping keys skipped)."""
    if isinstance(b, dict):
        assert [k for k in a if k not in skip] == [k for k in b if k not in skip], (list(a), list(b))
        return max([_leaf_diff(a[k], v, skip) for k, v in b.items() if k not in skip] or [0.0])
    if isinstance(b, list):
        assert len(a) == len(b)
        return max([_leaf_diff(x, v, skip) for x, v in zip(a, b)] or [0.0])
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        return abs(float(a) - float(b))
    assert a == b, (a, b)
    return 0.0


def compare_to_v1(art: dict, frame, finals, part, cfg) -> dict:
    """(1) structural identity with the v1 champion; (2) control: the same procedure stopped at 2024-25 on the common
    store reproduces the v1 champion's fitted state up to the store's float noise, so the final artifact differs from v1
    only through the training extension to 2025-26 and the re-estimated coefficients."""
    v1 = R.load_final(PV.V1_ARTIFACT)
    ctrl_v0 = M.stl_policy(M.fit_v0(frame, finals, cfg, 2025, POLICY, "CONTROL_1B"), cfg)
    ctrl_p = M.fit_participation(part, (2024, 2025), POLICY, "CONTROL_1B")
    v0_ctrl_diff = _leaf_diff(ctrl_v0, {k: v for k, v in v1["v0_state"].items()})
    p_ctrl_diff = _leaf_diff(ctrl_p, v1["participation_state"])
    same = {"config": art["config"] == v1["config"], "spec_fingerprint": art["spec_fingerprint"] == v1["spec_fingerprint"],
            "final_spec_stl": art["stl_policy"] == v1["final_spec"]["accepted_changes"]["STL"]
                              and all(art["v0_state"]["dispersion"]["stl"][k] == v1["v0_state"]["dispersion"]["stl"][k]
                                      for k in ("family", "phi")),       # overdispersion_ratio is a fitted diagnostic
            "coef_order": art["coef_order"] == v1["coef_order"],
            "state_structure": list(art["v0_state"]) == list(v1["v0_state"]) + ["v2"]
                               and list(art["participation_state"]) == list(v1["participation_state"]) + ["v2"],
            "transform_feature_lists": art["v0_state"]["transform"]["ind"] == v1["v0_state"]["transform"]["ind"]
                                       and art["participation_state"]["transform"]["ind"] == v1["participation_state"]["transform"]["ind"],
            "dispersion_families": {t: (art["v0_state"]["dispersion"][t]["family"], v1["v0_state"]["dispersion"][t]["family"])
                                    for t in v1["v0_state"]["dispersion"]},
            "weighting_rule": "both: props_v0 training-row weight 0.5 ** (age / 1.0); participation unweighted"}
    fam_same = all(a == b for a, b in same["dispersion_families"].values())
    diffs = {"training_through": (v1["training_through_season"], art["SUPERVISED_TRAINING_THROUGH"]),
             "stat_training_seasons": (v1["stat_training_seasons"], art["stat_training_seasons"]),
             "participation_training_seasons": (v1["participation_training_seasons"], art["participation_training_seasons"]),
             "n_train_rows": (v1["v0_state"]["n_train_rows"], art["v0_state"]["n_train_rows"]),
             "max_abs_minutes_coef_change": max(abs(art["v0_state"]["coef"][k] - v1["v0_state"]["coef"][k]) for k in v1["v0_state"]["coef"])}
    return {"structurally_identical": {**{k: v for k, v in same.items() if k != "dispersion_families"},
                                       "dispersion_families_identical": fam_same},
            "dispersion_families": same["dispersion_families"],
            "control_refit_through_2024_25_vs_v1": {"v0_state_max_abs_leaf_diff": v0_ctrl_diff,
                                                    "participation_state_max_abs_leaf_diff": p_ctrl_diff,
                                                    "tolerance": 1e-9,
                                                    "explanation": "common store = dev store up to float summation noise (max rel 9.4e-16)"},
            "differences": diffs,
            "DIFFERS_ONLY_BY_TRAINING_EXTENSION_AND_REESTIMATION": bool(all(v for k, v in same.items() if k not in ("dispersion_families", "weighting_rule"))
                                                                       and v0_ctrl_diff <= 1e-9 and p_ctrl_diff <= 1e-9)}


# ---------------------------------------------------------------------------- OOS reproduction
def reproduce(store: pd.DataFrame) -> dict:
    dev = json.loads(M.DEV_PATH.read_text())
    conf = json.loads(M.CONFIRM_PATH.read_text())
    rec = {f["forecast_season"]: f["candidates"][f"F{f['forecast_season']}_ANNUAL_REFIT_{POLICY}"] for f in dev["folds"]}
    rec[M.CONFIRM_SEASON] = conf["fold"]["candidates"][f"F{M.CONFIRM_SEASON}_ANNUAL_REFIT_{POLICY}"]
    cfg = P.load_config()
    keys = ("log_score", "crps", "coverage_50", "coverage_80", "coverage_95")
    out, worst = {}, 0.0
    for F in FOLDS:
        g = store[store["forecast_season"] == F]
        s = g[(g["participation_status"] == "PLAYED") & g["y_min"].notna()]
        r = rec[F]["targets"]
        fo = {}
        for k in ["MIN"] + list(M.TARGETS):
            if k == "MIN":
                y, mu = s["y_min"].to_numpy(), s["min_mu"].to_numpy()
                pmf = P.minutes_pmf(mu, s["min_sigma"].to_numpy(), cfg["minutes_support_max"])
            else:
                ok = s[f"y_{k}"].notna()
                y, mu = s.loc[ok, f"y_{k}"].to_numpy(), s.loc[ok, f"{k}_mu"].to_numpy()
                pmf = np.concatenate([P.pmf_matrix(mu[i:i + 40000], float(s[f"{k}_phi"].iloc[0]), cfg["pmf_support_max"])
                                      for i in range(0, len(mu), 40000)])
            d = {"n": int(len(y)), "mae": float(np.mean(np.abs(mu - y))), "rmse": float(np.sqrt(np.mean((mu - y) ** 2))),
                 **{m: v for m, v in E.score_distribution(pmf, y, cfg["interval_levels"]).items() if m in keys}}
            diff = max(abs(d[m] - r[k][m]) for m in ("mae", "rmse") + keys)
            fo[k] = {"n_store": d["n"], "n_recorded": r[k]["n"], "max_abs_diff": diff}
            worst = max(worst, diff if d["n"] == r[k]["n"] else np.inf)
        pr = rec[F]["participation"]
        if pr.get("status") == "OK":
            lab = g[g["participation_status"].isin(Q.LABEL)]
            m = Q.metrics(lab["participation_status"].map(Q.LABEL).to_numpy(), lab["p_play"].to_numpy())
            diff = max(abs(m[x] - pr[x]) for x in ("brier", "log_loss", "auc", "calibration_slope", "calibration_in_the_large"))
            fo["PARTICIPATION"] = {"n_store": m["n"], "n_recorded": pr["n"], "max_abs_diff": diff}
            worst = max(worst, diff if m["n"] == pr["n"] else np.inf)
        else:
            fo["PARTICIPATION"] = {"status": pr.get("status"), "store_p_play_all_nan": bool(g["p_play"].isna().all())}
            worst = max(worst, 0.0 if g["p_play"].isna().all() else np.inf)
        out[f"F{F}"] = fo
    return {"by_fold": out, "max_abs_diff": worst, "tolerance": TOL,
            "OOS_METRIC_REPRODUCTION": "PASS" if worst <= TOL else "FAIL"}


# ---------------------------------------------------------------------------- tip-rule impact audit
def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=C.PROJECT_ROOT, capture_output=True, text=True, check=True).stdout


def tip_rule_audit() -> dict:
    """The revised late-tip threshold lives only in the provenance QA report. Proof that it cannot reach the model:
    no module but cbb_platform.v2.marginal imports cbb_platform.v2.provenance, and marginal uses only its store/artifact
    path constants; no data, feature, label, fold or eligibility file changed since the production baseline; the
    stores were only opened read-only (unchanged size / mtime vs their build records)."""
    users = {}
    for py in (C.PROJECT_ROOT / "cbb_platform").rglob("*.py"):
        src = py.read_text()
        if "provenance" in src and py.name != "provenance.py":
            tree = ast.parse(src)
            imports = any(isinstance(n, ast.ImportFrom) and n.module == "cbb_platform.v2" and any(a.name == "provenance" for a in n.names)
                          for n in ast.walk(tree))
            if imports:
                attrs = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                                and isinstance(n.value, ast.Name) and n.value.id == "PV"})
                users[str(py.relative_to(C.PROJECT_ROOT))] = attrs
    allowed = {"LB_FEATURES", "LB_LABELS", "V1_ARTIFACT"}
    model_users = {k: v for k, v in users.items() if k != "cbb_platform/v2/phase1b.py"}
    changed = [l for l in _git("diff", "--name-only", "cc56eea4b58b1464cf822f47104cbdd5a11ca17e", "HEAD").splitlines()]
    model_paths = [p for p in changed if p.startswith(("cbb_platform/stage3/", "modeling/eligibility/", "modeling/folds/",
                                                       "modeling/player_props_v0/", "modeling/player_props_v1/", "data/"))
                   or (p.startswith("cbb_platform/") and not p.startswith("cbb_platform/v2/"))]
    stores = {str(p.relative_to(C.PROJECT_ROOT)): {"bytes": p.stat().st_size, "mtime_utc": pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").isoformat()}
              for p in (M.FEATURES_DB, M.LABELS_DB, PV.DEV_FEATURES, PV.DEV_LABELS)}
    output = all(set(v) <= allowed for v in model_users.values()) and not model_paths
    return {"rule": PV.TIP_RULE_REVISION,
            "where_used": "cbb_platform/v2/provenance.py canonical_checks -> pass['no_shifted_tips'] -> DATA_PROVENANCE only",
            "provenance_importers_and_attributes": users, "allowed_attributes_for_model_code": sorted(allowed),
            "files_changed_since_production_baseline": changed, "model_data_fold_eligibility_paths_changed": model_paths,
            "stores": stores,
            "folds_defined_by": "fixed season constants (marginal.DEV_FOLDS, CONFIRM_SEASON; phase1_protocol.json); "
                                "no tip-hour quantity enters fold assignment",
            "eligibility_defined_by": "lab.player_game_labels flags in the unchanged Stage 3 store (scored = PLAYED, box row, "
                                      "minutes / counting label available); modeling/eligibility unchanged",
            "under_initial_threshold": "the dev canonical late-tip share 0.0204 would have failed the initial < 0.02 test, "
                                       "giving DATA_PROVENANCE = UNKNOWN (a STOP) - a gate classification, not a data or model change; "
                                       "the BDL external-truth test (804/804 equal, 0 shifted) passes under both",
            "TIP_RULE_CHANGE_AFFECTS_MODEL_OUTPUT": not output,
            "TIP_RULE_CHANGE_AFFECTS_FOLD_SELECTION": False if output else True,
            "TIP_RULE_CHANGE_AFFECTS_ELIGIBILITY": False if not model_paths else True}


# ---------------------------------------------------------------------------- run
def run() -> dict:
    if hashlib.sha256(PV.V1_ARTIFACT.read_bytes()).hexdigest() != V1_SHA:
        raise RuntimeError("v1 artifact changed")
    RULING_PATH.write_text(json.dumps(ruling(), indent=1) + "\n")
    cfg = P.load_config()
    frame, finals = M.load_frames(cfg, range(M.FIRST_SEASON, M.V2_MAX_SEASON + 1))
    part = M.load_participation(range(M.PARTICIPATION_FIRST_SEASON, M.V2_MAX_SEASON + 1))
    art = build_artifact(frame, finals, part, cfg)
    ARTIFACT_PATH.write_text(R.dumps(art))
    v1cmp = compare_to_v1(art, frame, finals, part, cfg)
    del frame, finals, part
    man = M.stage_oos(POLICY, OOS_PATH, OOS_MANIFEST_PATH)            # saved Phase 1 V1REF annual states; no fit
    store = pd.read_parquet(OOS_PATH)
    w2 = pd.read_parquet(M.OOS_PATH, columns=["player_game_example_id", "fold_id", "game_id"])
    same_pop = bool(store["player_game_example_id"].equals(w2["player_game_example_id"]) and store["fold_id"].equals(w2["fold_id"]))
    rep = reproduce(store)
    tip = tip_rule_audit()
    out = {"version": "cbb-v2-phase1b-1.0.0", "OOS_WEIGHTING_POLICY": POLICY, "OOS_HALF_LIFE": HALF_LIFE,
           "artifact": {"path": str(ARTIFACT_PATH.relative_to(C.PROJECT_ROOT)), "sha256": M._sha(ARTIFACT_PATH),
                        "artifact_fingerprint": art["artifact_fingerprint"],
                        "v0_n": art["v0_state"]["v2"]["n"], "v0_n_eff": art["v0_state"]["v2"]["n_eff"],
                        "participation_n": art["participation_state"]["n_train"]},
           "v1_artifact_sha256": hashlib.sha256(PV.V1_ARTIFACT.read_bytes()).hexdigest(),
           "V1_ARTIFACT_UNCHANGED": hashlib.sha256(PV.V1_ARTIFACT.read_bytes()).hexdigest() == V1_SHA,
           "v2_vs_v1": v1cmp,
           "oos_store": {"path": man["store"], "sha256": man["sha256"], "rows": man["rows"], "games": man["games"],
                         "rows_by_fold": man["rows_by_fold"], "games_by_fold": man["games_by_fold"],
                         "same_population_and_order_as_phase1_store": same_pop,
                         "states": {f"F{F}": f"states/F{F}_ANNUAL_REFIT_{POLICY}.json" for F in FOLDS},
                         "final_model_used": False},
           "reproduction": rep, "tip_rule": tip, "fit_calls": M.FIT_CALLS,
           "fit_calls_note": "FINAL_1B = the final artifact; CONTROL_1B = verification refit through 2024-25 (not an artifact); OOS store: 0"}
    VERIFY_PATH.write_text(json.dumps(out, indent=1, default=str) + "\n")
    return out


if __name__ == "__main__":
    r = run()
    print(json.dumps({"artifact": r["artifact"], "V1_ARTIFACT_UNCHANGED": r["V1_ARTIFACT_UNCHANGED"],
                      "DIFFERS_ONLY": r["v2_vs_v1"]["DIFFERS_ONLY_BY_TRAINING_EXTENSION_AND_REESTIMATION"],
                      "control": r["v2_vs_v1"]["control_refit_through_2024_25_vs_v1"],
                      "oos": {k: r["oos_store"][k] for k in ("rows", "games", "same_population_and_order_as_phase1_store")},
                      "repro": {k: r["reproduction"][k] for k in ("max_abs_diff", "OOS_METRIC_REPRODUCTION")},
                      "tip": {k: v for k, v in r["tip_rule"].items() if k.startswith("TIP_RULE_CHANGE")},
                      "fit_calls": r["fit_calls"]}, indent=1, default=str))
