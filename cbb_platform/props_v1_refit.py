"""Production candidate v1: final development refit through 2024-25 and its serialized scoring artifact.

The FROZEN v1 specification is refit unchanged (no selection of any kind):
    stat / minutes   props_v0.fit(..., train_through=2025): every eligible appearance 2013-14..2024-25, same recency
                     weights (age from 2024-25), ridge penalty, priors, shrinkage, dispersion rules and supports
    participation    props_v0_participation.fit(..., train_seasons=(2024, 2025)): identifiable PLAYED / DNP_CONFIRMED
                     rows of 2023-24 and 2024-25; UNKNOWN_PARTICIPATION and NOT_ON_ROSTER stay excluded
    pricing          props_v1_pricing, unchanged
2025-26 is refused by every loader. 2024-25 is now training data: nothing here is out-of-sample evidence (the frozen
2024-25 validation results in modeling/player_props_v0/*metrics.json remain the promotion evidence).

The artifact (JSON) holds only what scoring needs: the v0 state (minutes preprocessing + coefficients, position prior
rates, shrinkage k, dispersion per target, minutes residual SDs, baseline population means), the participation state
(preprocessing + coefficients), the frozen config (recency, targets, supports), versions and the training range. Player
rates are not stored: by design they are computed at scoring time from that game's point-in-time features.

    .venv/bin/python -m cbb_platform.props_v1_refit    -> modeling/player_props_v1/refit_2024_25_artifact.json

FINAL PRE-LOCKBOX CANDIDATE (bottom of this module). The versioned build specification
modeling/player_props_v1/final_prelockbox_spec.json lists the accepted changes; the only one is STL Poisson -> the
existing props_v0 Negative Binomial with phi = 0.16. build_final_artifact() runs the frozen refit above unchanged
(asserting it byte-reproduces refit_2024_25_artifact.json), then overrides ONLY v0_state.dispersion[stl] from the spec.
At runtime the artifact is the single source of truth: Q.pregame_outputs / props_v1_pricing.score build every PMF from
v0_state.dispersion[t].phi via props_v0.pmf_matrix, so no phi is hard-coded in any runtime module.

    .venv/bin/python -m cbb_platform.props_v1_refit final      -> final_prelockbox_2024_25_artifact.json
    .venv/bin/python -m cbb_platform.props_v1_refit validate --suite S.json   -> final_prelockbox_validation.json
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_eval as E, props_v0_participation as Q
from cbb_platform import props_v1_pricing as V

TRAIN_THROUGH = 2025                                   # 2024-25
PARTICIPATION_SEASONS = (2024, 2025)                   # 2023-24, 2024-25 (the only seasons with confirmed DNPs)
ARTIFACT_PATH = P.CONFIG_PATH.parent.parent / "player_props_v1" / "refit_2024_25_artifact.json"
ARTIFACT_VERSION = "cbb-props-v1-refit-2024-25"


def spec_fingerprint(cfg: dict) -> str:
    """sha256 of every frozen specification element (config + participation inputs/penalty + pricing constants)."""
    spec = {"config": cfg, "participation": {"numeric": Q.NUMERIC, "log1p": sorted(Q.LOG1P), "flags": Q.FLAGS,
                                             "l2_alpha": Q.L2_ALPHA, "labels": Q.LABEL},
            "pricing": {"version": V.PRICING_VERSION, "ladder_tail": V.LADDER_TAIL, "quantiles": V.QUANTILES}}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def fit_artifact() -> dict:
    lockbox.check_season(TRAIN_THROUGH)
    if TRAIN_THROUGH != lockbox.LAST_DEVELOPMENT_SEASON:
        raise ValueError("the final development refit trains through the last development season only")
    cfg = P.load_config()
    frame, finals, _ = P.load_frames(cfg)
    v0 = P.fit(frame, finals, cfg, TRAIN_THROUGH)
    part_rows, counts = Q.load(seasons=PARTICIPATION_SEASONS)
    part = Q.fit(part_rows, train_seasons=PARTICIPATION_SEASONS)
    rows = {str(s): {"played": int(((part_rows["season"] == s) & (part_rows["y"] == 1)).sum()),
                     "dnp": int(((part_rows["season"] == s) & (part_rows["y"] == 0)).sum()),
                     "unknown_excluded": counts[str(s)].get("UNKNOWN_PARTICIPATION", 0),
                     "not_on_roster_excluded": counts[str(s)].get("NOT_ON_ROSTER", 0)} for s in PARTICIPATION_SEASONS}
    art = {"artifact_version": ARTIFACT_VERSION, "model_version": V.MODEL_VERSION,
           "dataset_version": cfg["upstream_dataset"], "training_through_season": TRAIN_THROUGH,
           "stat_training_seasons": [int(frame["season"].min()), TRAIN_THROUGH],
           "participation_training_seasons": list(PARTICIPATION_SEASONS), "participation_rows": rows,
           "spec_fingerprint": spec_fingerprint(cfg), "config": cfg,
           "v0_state": v0, "participation_state": part,
           # the frozen scorers read coefficients positionally (dict order = design-matrix column order)
           "coef_order": {"v0": list(v0["coef"]), "participation": list(part["coef"])}}
    art["artifact_fingerprint"] = fingerprint(art)
    return art


def fingerprint(art: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in art.items() if k != "artifact_fingerprint"},
                                     sort_keys=True).encode()).hexdigest()


def dumps(art: dict) -> str:
    # insertion order is preserved (NOT sort_keys): coefficient dicts are positional for the frozen scorers
    return json.dumps(art, indent=1) + "\n"


def save(art: dict, path: Path = ARTIFACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(art))


def load(path: Path = ARTIFACT_PATH) -> dict:
    art = json.loads(Path(path).read_text())
    if fingerprint(art) != art["artifact_fingerprint"]:
        raise ValueError("artifact fingerprint mismatch")
    if art["spec_fingerprint"] != spec_fingerprint(P.load_config()):
        raise ValueError("artifact was fit under a different specification")
    if list(art["v0_state"]["coef"]) != art["coef_order"]["v0"] or \
            list(art["participation_state"]["coef"]) != art["coef_order"]["participation"]:
        raise ValueError("coefficient order differs from the design-matrix order recorded at fit time")
    if art["training_through_season"] >= lockbox.LOCKBOX_SEASON:
        raise lockbox.LockboxViolation("artifact trained on 2025-26")
    return art


def scoring_model(art: dict) -> dict:
    """The props_v1_pricing.score() model dict from a loaded artifact. Nothing is fit: only the participation
    FEATURE rows are read from the Stage 3 store (labels are not used by scoring)."""
    part_rows, _ = Q.load(seasons=PARTICIPATION_SEASONS)
    return {"cfg": art["config"], "v0": art["v0_state"], "part": art["participation_state"], "part_rows": part_rows}


def score_demo(art: dict) -> "pd.DataFrame":
    """The fixed v1 demonstration games (2024-25; IN-SAMPLE for this refit: a functional check only)."""
    cand = V.load_candidates()
    return V.score(cand[cand["canonical_game_id"].isin(V.sample_games(cand))], scoring_model(art))


# ============================================================================ final pre-lockbox candidate
FINAL_SPEC_PATH = ARTIFACT_PATH.parent / "final_prelockbox_spec.json"
FINAL_ARTIFACT_PATH = ARTIFACT_PATH.parent / "final_prelockbox_2024_25_artifact.json"
FINAL_VALIDATION_PATH = ARTIFACT_PATH.parent / "final_prelockbox_validation.json"
FAMILY = {"negative_binomial": "NEGBIN"}                # spec vocabulary -> props_v0 dispersion vocabulary


def load_final_spec(path: Path = FINAL_SPEC_PATH) -> dict:
    return json.loads(Path(path).read_text())


def final_spec_fingerprint(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def apply_accepted_changes(v0_state: dict, spec: dict, cfg: dict) -> dict:
    """A copy of a fitted v0 state with the spec's accepted distribution overrides (family + phi). Means, rates,
    coefficients and every other target are untouched."""
    if spec["rejected_changes_activated"]:
        raise ValueError("rejected changes may not be activated")
    st = copy.deepcopy(v0_state)
    for name, ch in spec["accepted_changes"].items():
        t = cfg["count_targets"][name]
        if ch["mean_changed"] or not (isinstance(ch["phi"], float) and ch["phi"] > 0):
            raise ValueError(f"unsupported accepted change for {name}: {ch}")
        st["dispersion"][t] = {**st["dispersion"][t], "family": FAMILY[ch["family"]], "phi": ch["phi"]}
    return st


def build_final_artifact(spec: dict | None = None) -> dict:
    spec = spec or load_final_spec()
    if spec["training_through_season"] != TRAIN_THROUGH:
        raise ValueError("the final candidate trains through 2024-25 only")
    lockbox.check_season(spec["training_through_season"])
    base = fit_artifact()
    if hashlib.sha256(dumps(base).encode()).hexdigest() != spec["base_artifact_sha256"]:
        raise ValueError("the frozen refit no longer byte-reproduces the base artifact")
    if base["dataset_version"] != spec["dataset_version"]:
        raise ValueError("dataset version differs from the final specification")
    cfg = base["config"]
    overrides = {name: {"target": cfg["count_targets"][name], "family": ch["family"], "phi": ch["phi"],
                        "fitted_family": base["v0_state"]["dispersion"][cfg["count_targets"][name]]["family"],
                        "fitted_phi": base["v0_state"]["dispersion"][cfg["count_targets"][name]]["phi"]}
                 for name, ch in spec["accepted_changes"].items()}
    art = {"artifact_version": spec["candidate_name"],
           "model_version": V.MODEL_VERSION + "+" + "+".join(f"{n.lower()}-negbin-phi-{c['phi']}" for n, c in overrides.items()),
           **{k: base[k] for k in ("dataset_version", "training_through_season", "stat_training_seasons",
                                   "participation_training_seasons", "participation_rows", "spec_fingerprint")},
           "final_spec": spec, "final_spec_fingerprint": final_spec_fingerprint(spec),
           "base_artifact_sha256": spec["base_artifact_sha256"], "base_artifact_fingerprint": base["artifact_fingerprint"],
           "distribution_overrides": overrides,
           "v0_state_hash_note": "v0_state.state_hash is the hash of the fitted state before distribution_overrides",
           "config": cfg, "v0_state": apply_accepted_changes(base["v0_state"], spec, cfg),
           "participation_state": base["participation_state"], "coef_order": base["coef_order"]}
    art["artifact_fingerprint"] = fingerprint(art)
    return art


def load_final(path: Path = FINAL_ARTIFACT_PATH) -> dict:
    art = load(path)
    spec = art["final_spec"]
    if final_spec_fingerprint(spec) != art["final_spec_fingerprint"] or spec["rejected_changes_activated"]:
        raise ValueError("final specification fingerprint mismatch or rejected change activated")
    for name, o in art["distribution_overrides"].items():
        d = art["v0_state"]["dispersion"][o["target"]]
        if (d["family"], d["phi"]) != (FAMILY[spec["accepted_changes"][name]["family"]], spec["accepted_changes"][name]["phi"]):
            raise ValueError(f"{name} distribution differs from the final specification")
    return art


def score_final(cand: pd.DataFrame, art: dict) -> pd.DataFrame:
    """props_v1_pricing.score on the loaded final artifact, rows stamped with the artifact's model version."""
    out = V.score(cand, scoring_model(art))
    out["model_version"] = art["model_version"]
    return out


# ---------------------------------------------------------------------------- validation (evidence, never runtime)
LINES = (0.5, 1.5, 2.5)
CHUNK = 40000


def _dist(mu, t, st, cfg):
    if t == "min":
        return P.minutes_pmf(mu, P._band_sigma(mu, cfg["minutes_sigma_buckets"], st["minutes_sigma"]), cfg["minutes_support_max"])
    return P.pmf_matrix(mu, st["dispersion"][t]["phi"], cfg["pmf_support_max"])


def _thresholds(pmf, y) -> dict:
    over = np.column_stack([V.price(pmf, L)["over_probability"] for L in np.arange(pmf.shape[1] - 1) + 0.5])
    th = {}
    for L in LINES:
        p, o = over[:, int(L - 0.5)], (y > L).astype(float)
        pc, gap = np.clip(p, 1e-15, 1 - 1e-15), float(p.mean() - o.mean())
        th[str(L)] = {"n": int(len(o)), "pred": float(p.mean()), "observed": float(o.mean()), "gap": gap, "abs_gap": abs(gap),
                      "brier": float(np.mean((p - o) ** 2)),
                      "log_loss": float(-np.mean(o * np.log(pc) + (1 - o) * np.log(1 - pc)))}
    return {"p_over_monotonicity_violations": int(np.sum(np.diff(over, axis=1) > 0)), "thresholds": th,
            "mean_abs_threshold_gap": float(np.mean([r["abs_gap"] for r in th.values()])),
            "mean_threshold_brier": float(np.mean([r["brier"] for r in th.values()])),
            "mean_threshold_log_loss": float(np.mean([r["log_loss"] for r in th.values()]))}


def _target_metrics(mu, y, t, st, cfg, stl: bool) -> tuple[dict, np.ndarray]:
    pmf = np.concatenate([_dist(mu[i:i + CHUNK], t, st, cfg) for i in range(0, len(mu), CHUNK)])
    m = {"family": "DISCRETIZED_NORMAL" if t == "min" else st["dispersion"][t]["family"],
         "phi": None if t == "min" else st["dispersion"][t]["phi"],
         "mean_pred": float(mu.mean()), "observed_mean": float(y.mean()), "mean_bias": float(mu.mean() - y.mean()),
         "mae": float(np.mean(np.abs(mu - y))), "rmse": float(np.sqrt(np.mean((mu - y) ** 2))),
         **E.score_distribution(pmf, y, cfg["interval_levels"]), "min_pmf_entry": float(pmf.min())}
    if stl:
        m.update(_thresholds(pmf, y))
    return m, pmf


def _stl_diagnostics(pb, pf, y, upper) -> list:
    cb, cf = np.cumsum(pb, axis=1), np.cumsum(pf, axis=1)
    out = [{"quantity": "P(STL=0)", "observed": float(np.mean(y == 0)), "baseline": float(cb[:, 0].mean()),
            "challenger": float(cf[:, 0].mean())}]
    for j in range(1, upper + 1):
        out.append({"quantity": f"P(STL>={j})", "observed": float(np.mean(y >= j)),
                    "baseline": float(np.mean(1 - cb[:, j - 1])), "challenger": float(np.mean(1 - cf[:, j - 1]))})
    return out


def _max_leaf_diff(a, b) -> float:
    """Largest absolute difference over the numeric leaves of b that a also has (structure must match)."""
    if isinstance(b, dict):
        return max([_max_leaf_diff(a[k], v) for k, v in b.items()] or [0.0])
    if isinstance(b, list):
        assert len(a) == len(b)
        return max([_max_leaf_diff(x, v) for x, v in zip(a, b)] or [0.0])
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        return abs(float(a) - float(b))
    assert a == b, (a, b)
    return 0.0


def _evaluations(cfg, spec) -> tuple[dict, dict]:
    recorded = json.loads((P.CONFIG_PATH.parent / "metrics.json").read_text())["evaluations"]
    frame, finals, _ = P.load_frames(cfg)
    if int(frame["season"].max()) != TRAIN_THROUGH:
        raise lockbox.LockboxViolation("validation frame must end at 2024-25")
    out, keep = {}, {}
    for ev in cfg["evaluations"]:
        st = P.fit(frame, finals, cfg, ev["train_through"])
        assert st["state_hash"] == recorded[ev["name"]]["state_hash"], ev["name"]
        fs = apply_accepted_changes(st, spec, cfg)
        va = frame[frame["season"] == ev["validate"]]
        assert va["prediction_cutoff_utc"].min() > pd.Timestamp(st["train_max_cutoff"]), "no future information"
        mb, mf = P.predict(va, cfg, st)["challenger"], P.predict(va, cfg, fs)["challenger"]
        res = {"train_through": ev["train_through"], "validate": ev["validate"], "state_hash": st["state_hash"],
               "row_identity_sha256": hashlib.sha256("\n".join(va["player_game_example_id"].astype(str)).encode()).hexdigest(),
               "targets": {}}
        names = {"min": "MIN", **{v: k for k, v in cfg["count_targets"].items()}}
        for t in ["min"] + list(cfg["count_targets"].values()):
            y = va["y_min" if t == "min" else f"y_{t}"].to_numpy(dtype=float); ok = ~np.isnan(y)
            stl = names[t] in spec["accepted_changes"]
            b, pb = _target_metrics(mb[t][ok], y[ok], t, st, cfg, stl)
            f, pf = _target_metrics(mf[t][ok], y[ok], t, fs, cfg, stl)
            rec = recorded[ev["name"]]["targets"][names[t]]["challenger"]
            r = {"n": int(ok.sum()), "baseline": b, "final": f,
                 "max_abs_mean_difference": float(np.max(np.abs(mf[t] - mb[t]))),
                 "baseline_vs_recorded_max_diff": max(abs(b[k] - rec[k]) for k in ("mae", "rmse", "log_score", "crps",
                                                                                  "coverage_50", "coverage_80", "coverage_95")),
                 "pmf_byte_identical": bool(np.array_equal(pb, pf))}
            if stl:
                r["final_pmf_equals_pmf_matrix_phi"] = bool(np.array_equal(pf, P.pmf_matrix(mb[t][ok], spec["accepted_changes"][names[t]]["phi"], cfg["pmf_support_max"])))
                keep[ev["name"]] = {"y": y[ok], "pb": pb, "pf": pf, "state": st, "final_state": fs}
            res["targets"][names[t]] = r
        out[ev["name"]] = res
    return out, keep


def _stl_reproduction(ev: dict, keep: dict, cfg) -> dict:
    """Integrated candidate vs the accepted challenger evidence (full precision), and the original A-J rules rerun."""
    ch = json.loads((ARTIFACT_PATH.parent / "stl_nb_challenger.json").read_text())
    evidence = {"decision": ch["decision"], "selected_phi": ch["calibration_fit"]["selected_phi"],
                "lockbox_touched": ch["lockbox_touched"], "max_mean_difference": ch["forward_validation"]["max_mean_difference"],
                "acceptance": ch["acceptance"]}
    evidence["valid"] = (ch["decision"] == "ACCEPT" and ch["calibration_fit"]["selected_phi"] == 0.16
                         and ch["lockbox_touched"] is False and ch["forward_validation"]["max_mean_difference"] == 0.0
                         and all(ch["acceptance"].values()) and sorted(ch["acceptance"]) == list("ABCDEFGHIJ"))
    fv, B, A = ch["forward_validation"], ev["B"]["targets"]["STL"], ev["A"]["targets"]["STL"]
    upper = 4 if min(int(np.sum(keep["A"]["y"] >= 4)), int(np.sum(keep["B"]["y"] >= 4))) >= 100 else 3
    diag = {k: _stl_diagnostics(keep[k]["pb"], keep[k]["pf"], keep[k]["y"], upper) for k in ("A", "B")}
    diffs = {"B_final_vs_accepted_challenger": _max_leaf_diff(B["final"], fv["challenger"]),
             "B_baseline_vs_accepted_baseline": _max_leaf_diff(B["baseline"], fv["baseline"]),
             "B_distribution_diagnostics": _max_leaf_diff(diag["B"], fv["distribution_diagnostics"]),
             "A_distribution_diagnostics": _max_leaf_diff(diag["A"], ch["calibration_fit"]["distribution_diagnostics"]),
             "A_log_score": max(abs(A["final"]["log_score"] - ch["calibration_fit"]["challenger_log_score"]),
                                abs(A["baseline"]["log_score"] - ch["calibration_fit"]["baseline_log_score"])),
             "A_mae": abs(A["final"]["mae"] - ch["calibration_fit"]["challenger_mae"]),
             "n": float(abs(B["n"] - fv["n"]))}
    b, c = B["baseline"], B["final"]
    tb, tc = b["thresholds"], c["thresholds"]
    rules = {"A": B["max_abs_mean_difference"] <= 1e-12 and abs(c["mae"] - b["mae"]) <= 1e-12,
             "B": c["log_score"] < b["log_score"] - 0.0001,
             "C": c["crps"] < b["crps"] - 0.0001,
             "D": tc["0.5"]["abs_gap"] <= 0.75 * tb["0.5"]["abs_gap"],
             "E": tc["2.5"]["abs_gap"] <= 0.75 * tb["2.5"]["abs_gap"],
             "F": (tc["1.5"]["abs_gap"] <= tb["1.5"]["abs_gap"] + 0.002 and tc["1.5"]["brier"] <= tb["1.5"]["brier"] + 0.0002
                   and tc["1.5"]["log_loss"] <= tb["1.5"]["log_loss"] + 0.0002),
             "G": (c["mean_threshold_brier"] < b["mean_threshold_brier"] - 1e-6
                   and c["mean_threshold_log_loss"] < b["mean_threshold_log_loss"] - 1e-6),
             "H": c["mean_abs_threshold_gap"] < b["mean_abs_threshold_gap"]}
    return {"accepted_evidence": evidence, "max_abs_differences": diffs, "tolerance": 1e-10,
            "reproduced": evidence["valid"] and max(diffs.values()) <= 1e-10,
            "distribution_diagnostics": diag, "acceptance_rerun_rules_A_H": rules}


def _coherence(pmf: np.ndarray) -> dict:
    K = pmf.shape[1] - 1
    over = np.column_stack([V.price(pmf, L)["over_probability"] for L in np.arange(K) + 0.5])
    r = {"max_row_sum_error": float(np.max(np.abs(pmf.sum(axis=1) - 1))), "min_entry": float(pmf.min()),
         "p_over_monotonicity_violations": int(np.sum(np.diff(over, axis=1) > 0)),
         "integer_push_max_error": 0.0, "half_line_max_push": 0.0, "nonpush_max_error": 0.0, "invalid_fair_prices": 0,
         "tail_last_point_max_error": float(np.max(np.abs(over[:, -1] - pmf[:, K])))}
    for L in np.arange(1.0, 11.0):
        p = V.price(pmf, L)
        r["integer_push_max_error"] = max(r["integer_push_max_error"], float(np.max(np.abs(p["push_probability"] - pmf[:, int(L)]))),
                                          float(np.max(np.abs(p["over_probability"] + p["under_probability"] + p["push_probability"] - 1))))
    for L in (0.5, 1.5, 2.5, 5.5, 10.5, 20.5, 30.5):
        p = V.price(pmf, L)
        r["half_line_max_push"] = max(r["half_line_max_push"], float(np.max(np.abs(p["push_probability"]))))
        o, u = p["over_probability_nonpush"], p["under_probability_nonpush"]
        settled = (p["over_probability"] + p["under_probability"]) > 0
        r["nonpush_max_error"] = max(r["nonpush_max_error"], float(np.max(np.abs(o[settled] + u[settled] - 1))))
        fin = settled & (o > 0) & (o < 1)
        d_o, d_u = p["fair_over_decimal"][fin], p["fair_under_decimal"][fin]
        am = p["fair_over_american"][fin]
        r["invalid_fair_prices"] += int(np.sum(~np.isfinite(d_o)) + np.sum(~np.isfinite(d_u))
                                        + np.sum(np.abs(d_o * o[fin] - 1) > 1e-9)
                                        + np.sum(np.abs(V.american_to_decimal(am) / d_o - 1) > 1e-9))   # relative: d_o reaches 1e12
    try:
        V.price(pmf[:1], K + 0.5); r["beyond_support_refused"] = False
    except ValueError:
        r["beyond_support_refused"] = True
    r["pass"] = (r["max_row_sum_error"] <= 1e-12 and r["min_entry"] >= 0 and r["p_over_monotonicity_violations"] == 0
                 and r["integer_push_max_error"] <= 1e-12 and r["half_line_max_push"] == 0.0 and r["nonpush_max_error"] <= 1e-12
                 and r["invalid_fair_prices"] == 0 and r["tail_last_point_max_error"] <= 1e-12 and r["beyond_support_refused"])
    return r


def _merge(acc: dict | None, r: dict) -> dict:
    if acc is None:
        return r
    out = {}
    for k, v in r.items():
        if k in ("min_entry",):
            out[k] = min(acc[k], v)
        elif isinstance(v, bool):
            out[k] = acc[k] and v
        elif k in ("p_over_monotonicity_violations", "invalid_fair_prices"):
            out[k] = acc[k] + v
        else:
            out[k] = max(acc[k], v)
    return out


def _pregame_identity(cfg, spec, ev_b_state, base_art, final_art) -> dict:
    """2024-25 pregame candidates through the real pregame / pricing path: (1) evaluation-B state, baseline vs final;
    (2) committed base artifact vs final artifact. Every output except the STL PMF must be identical; the STL PMF must
    be pmf_matrix(identical STL mean, 0.16). Pricing / PMF integrity is checked on the final artifact's PMFs."""
    cand = V.load_candidates()
    part_rows, _ = Q.load(seasons=PARTICIPATION_SEASONS)
    p_eval = Q.fit(part_rows[part_rows["season"] == 2024])               # frozen evaluation participation (2023-24)
    phi = spec["accepted_changes"]["STL"]["phi"]
    pairs = {"evaluation_B": (cfg, ev_b_state, apply_accepted_changes(ev_b_state, spec, cfg), p_eval, p_eval),
             "final_artifact_vs_base_artifact": (final_art["config"], base_art["v0_state"], final_art["v0_state"],
                                                 base_art["participation_state"], final_art["participation_state"])}
    out = {k: {"candidates": int(len(cand)), "table_columns_identical": None, "non_stl_pmf_byte_identical": True,
               "stl_pmf_is_negbin_phi": True, "stl_pmf_differs_from_poisson": False} for k in pairs}
    coherence = {}
    for i in range(0, len(cand), CHUNK):
        chunk = cand.iloc[i:i + CHUNK]
        for k, (c, sb, sf, pb_, pf_) in pairs.items():
            rb = Q.pregame_outputs(chunk, part_rows, c, sb, pb_)
            rf = Q.pregame_outputs(chunk, part_rows, c, sf, pf_)
            same = {col: bool(rb["table"][col].equals(rf["table"][col])) for col in rb["table"].columns}
            o = out[k]
            o["table_columns_identical"] = same if o["table_columns_identical"] is None else \
                {col: o["table_columns_identical"][col] and same[col] for col in same}
            for name, pmf in rb["conditional_stat_distribution"].items():
                if name == "STL":
                    mu = rb["table"]["STL_mean_given_play"].to_numpy()
                    o["stl_pmf_is_negbin_phi"] &= bool(np.array_equal(rf["conditional_stat_distribution"][name],
                                                                      P.pmf_matrix(mu, phi, c["pmf_support_max"])))
                    o["stl_pmf_differs_from_poisson"] |= not np.array_equal(pmf, rf["conditional_stat_distribution"][name])
                else:
                    o["non_stl_pmf_byte_identical"] &= bool(np.array_equal(pmf, rf["conditional_stat_distribution"][name]))
            if k == "final_artifact_vs_base_artifact":
                for name, pmf in rf["conditional_stat_distribution"].items():
                    if name != "MIN":
                        coherence[name] = _merge(coherence.get(name), _coherence(pmf))
    for o in out.values():
        o["pass"] = (all(o["table_columns_identical"].values()) and o["non_stl_pmf_byte_identical"]
                     and o["stl_pmf_is_negbin_phi"] and o["stl_pmf_differs_from_poisson"])
    return {"identity": out, "pricing_coherence_final_artifact": coherence}


def _artifact_comparison(base: dict, final: dict) -> dict:
    stl = final["distribution_overrides"]["STL"]["target"]
    v0b, v0f = copy.deepcopy(base["v0_state"]), copy.deepcopy(final["v0_state"])
    db, df_ = v0b["dispersion"].pop(stl), v0f["dispersion"].pop(stl)
    keys = ("dataset_version", "training_through_season", "stat_training_seasons", "participation_training_seasons",
            "participation_rows", "spec_fingerprint", "config", "participation_state", "coef_order")
    return {"v0_state_identical_except_stl_dispersion": v0b == v0f
            and list(base["v0_state"]) == list(final["v0_state"]),
            "minutes_coefficients_identical": base["v0_state"]["coef"] == final["v0_state"]["coef"]
            and list(base["v0_state"]["coef"]) == list(final["v0_state"]["coef"]),
            "minutes_transform_identical": base["v0_state"]["transform"] == final["v0_state"]["transform"],
            "position_priors_identical": base["v0_state"]["prior_rate"] == final["v0_state"]["prior_rate"],
            "shrinkage_identical": base["v0_state"]["prior_k_minutes"] == final["v0_state"]["prior_k_minutes"],
            "non_stl_dispersion_identical": v0b["dispersion"] == v0f["dispersion"],
            **{f"{k}_identical": base[k] == final[k] for k in keys},
            "stl_dispersion_base": db, "stl_dispersion_final": df_}


def _fresh(code: str, *args) -> str:
    r = subprocess.run([sys.executable, "-c", code, *args], cwd=C.PROJECT_ROOT, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-3000:])
    return r.stdout.strip().splitlines()[-1]


_BUILD = """
import hashlib, sys
from pathlib import Path
from cbb_platform import props_v1_refit as R
R.save(R.build_final_artifact(), Path(sys.argv[1]))
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
"""

FRESH_SCORE = """
import hashlib, json
import numpy as np
from cbb_platform import props_v0 as P, props_v0_participation as Q, props_v1_refit as R, props_v1_pricing as V
calls = []
def _no_fit(*a, **k):
    calls.append(1); raise RuntimeError("fit attempted")
P.fit = Q.fit = R.fit_artifact = R.build_final_artifact = _no_fit
art = R.load_final()
cand = V.load_candidates()
cand = cand[cand["canonical_game_id"].isin(V.sample_games(cand))]
res = Q.pregame_outputs(cand, R.scoring_model(art)["part_rows"], art["config"], art["v0_state"], art["participation_state"])
out = R.score_final(cand, art)
csv = out.to_csv(index=False, lineterminator="\\n", float_format="%.6f")
print(json.dumps({"loaded": True, "fit_calls": len(calls), "rows": len(out),
                  "families": {t: d["family"] for t, d in art["v0_state"]["dispersion"].items()},
                  "stl_phi": art["v0_state"]["dispersion"]["stl"]["phi"],
                  "max_pmf_row_sum_error": max(float(np.max(np.abs(p.sum(axis=1) - 1))) for p in res["conditional_stat_distribution"].values()),
                  "settle_max_error": float(np.max(np.abs(out["over_probability"] + out["under_probability"] + out["push_probability"] - 1))),
                  "model_version": sorted(out["model_version"].unique()),
                  "sha256": hashlib.sha256(csv.encode()).hexdigest()}, sort_keys=True))
"""


def validate_final(suite: dict | None = None) -> dict:
    spec = load_final_spec()
    cfg = P.load_config()
    base_art, final_art = load(ARTIFACT_PATH), load_final(FINAL_ARTIFACT_PATH)
    base_sha = hashlib.sha256(ARTIFACT_PATH.read_bytes()).hexdigest()
    final_sha = hashlib.sha256(FINAL_ARTIFACT_PATH.read_bytes()).hexdigest()
    if base_sha != spec["base_artifact_sha256"]:
        raise ValueError("base artifact changed")
    ev, keep = _evaluations(cfg, spec)
    stl = _stl_reproduction(ev, keep, cfg)
    pre = _pregame_identity(cfg, spec, keep["B"]["state"], base_art, final_art)
    with tempfile.TemporaryDirectory() as d:
        builds = [_fresh(_BUILD, str(Path(d) / f"build{i}.json")) for i in (1, 2)]
    scores = [json.loads(_fresh(FRESH_SCORE)) for _ in (1, 2)]
    non_stl = {e: {t: r["pmf_byte_identical"] and r["max_abs_mean_difference"] == 0.0
                   and {k: v for k, v in r["baseline"].items()} == {k: v for k, v in r["final"].items()}
                   for t, r in ev[e]["targets"].items() if t != "STL"} for e in ev}
    integrity = {"lockbox_2025_26_blocked": _lockbox_blocked(), "frame_max_season": TRAIN_THROUGH,
                 "baseline_reproduces_recorded_metrics": all(r["baseline_vs_recorded_max_diff"] <= 1e-12
                                                             for e in ev.values() for r in e["targets"].values())}
    stl["acceptance_rerun"] = {**stl["acceptance_rerun_rules_A_H"],
                               "I": pre["identity"]["evaluation_B"]["pass"],
                               "J": integrity["lockbox_2025_26_blocked"] and all(c["pass"] for c in pre["pricing_coherence_final_artifact"].values())
                               and ev["B"]["targets"]["STL"]["final"]["max_pmf_row_sum_error"] <= 1e-12}
    del stl["acceptance_rerun_rules_A_H"]
    out = {"candidate_name": spec["candidate_name"], "baseline_commit": spec["base_commit"],
           "baseline_artifact_sha256": base_sha, "dataset_version": spec["dataset_version"],
           "candidate_specification_fingerprint": final_art["final_spec_fingerprint"],
           "candidate_artifact_sha256": final_sha, "candidate_artifact_fingerprint": final_art["artifact_fingerprint"],
           "evaluations": ev, "stl_accepted_evidence": stl,
           "non_stl_identity": {"by_evaluation": non_stl, "pass": all(v for e in non_stl.values() for v in e.values())},
           "stl_mean_identical": all(ev[e]["targets"]["STL"]["max_abs_mean_difference"] == 0.0 for e in ev),
           "pregame_identity": pre["identity"], "pricing_and_pmf_checks": pre["pricing_coherence_final_artifact"],
           "artifact_comparison": _artifact_comparison(base_art, final_art),
           "artifact_determinism": {"independent_build_sha256": builds,
                                    "byte_identical": builds[0] == builds[1] == final_sha},
           "fresh_process_load": {"runs": scores, "no_fit": all(s["fit_calls"] == 0 for s in scores),
                                  "deterministic": scores[0] == scores[1]},
           "full_suite": suite, "integrity": integrity, "lockbox_2025_26_touched": False}
    return out


def _lockbox_blocked() -> bool:
    try:
        lockbox.check_season(lockbox.LOCKBOX_SEASON)
    except lockbox.LockboxViolation:
        return True
    return False


if __name__ == "__main__" and len(sys.argv) > 1:
    if sys.argv[1] == "final":
        save(build_final_artifact(), FINAL_ARTIFACT_PATH)
        print(hashlib.sha256(FINAL_ARTIFACT_PATH.read_bytes()).hexdigest())
    elif sys.argv[1] == "validate":
        suite = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 and sys.argv[2] == "--suite" else None
        out_path = Path(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[4] == "--out" else FINAL_VALIDATION_PATH
        out_path.write_text(json.dumps(validate_final(suite), indent=1, default=str) + "\n")
        print(hashlib.sha256(out_path.read_bytes()).hexdigest())
    else:
        raise SystemExit(f"unknown command {sys.argv[1]}")
elif __name__ == "__main__":
    a = fit_artifact()
    save(a)
    print(json.dumps({k: a[k] for k in ("artifact_version", "training_through_season", "participation_training_seasons",
                                        "participation_rows", "spec_fingerprint", "artifact_fingerprint")}, indent=1))
    print("n_train v0", a["v0_state"]["n_train_rows"], "participation", a["participation_state"]["n_train"])
