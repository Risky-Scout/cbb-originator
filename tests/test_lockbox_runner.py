"""Fit-free lockbox runner (cbb_platform/lockbox_runner.py). 2025-26 is refused by the closed guard before anything is
read; the gate semantics follow the frozen scorecard exactly (missing => FAIL); on 2024-25 the runner, with every fit
entry point disabled, reproduces the recorded evaluation-B metrics and reruns byte-identically."""
from __future__ import annotations

import ast
import json
import subprocess
import sys

import numpy as np
import pytest

from cbb_platform import config as C, lockbox, lockbox_runner as L, lockbox_runner_validation as LV, props_v1_refit as R
from cbb_platform.stage3 import spec


def test_lockbox_season_refused_before_anything_is_read(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("read attempted for a refused season")
    monkeypatch.setattr(R, "load_final", boom)
    monkeypatch.setattr(L, "_sha", boom)
    with pytest.raises(lockbox.LockboxViolation):
        L.run(R.FINAL_ARTIFACT_PATH, spec.FEATURES_DB, spec.LABELS_DB, lockbox.LOCKBOX_SEASON)


def test_future_lockbox_command_fails_while_guard_closed(tmp_path):
    out = tmp_path / "LOCKBOX_RESULT_2025_26.json"
    p = subprocess.run([sys.executable, "-m", "cbb_platform.lockbox_runner", "--artifact", str(R.FINAL_ARTIFACT_PATH),
                        "--features-db", str(tmp_path / "f.duckdb"), "--labels-db", str(tmp_path / "l.duckdb"),
                        "--season", "2026", "--out", str(out)], cwd=C.PROJECT_ROOT, capture_output=True, text=True)
    assert p.returncode != 0 and "LockboxViolation" in p.stderr and not out.exists()


def test_runner_contains_no_fit_path():
    src = (C.PROJECT_ROOT / "cbb_platform" / "lockbox_runner.py").read_text()
    calls = {n.func.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"fit", "fit_artifact", "build_final_artifact", "evaluate", "_fit_baseline", "_irls",
                        "apply_accepted_changes", "save"}


def _counts(c_log=1.0, b_log=1.1, c_crps=0.5, b_crps=0.6, n=10, mins=((4.0, 6.0), (5.0, 7.0))):
    t = {k: {"n": n, "candidate": {"log_score": c_log, "crps": c_crps}, "rolling_baseline": {"log_score": b_log, "crps": b_crps}}
         for k in L.TARGETS}
    t["MIN"] = {"n": n, "candidate": {"mae": mins[0][0], "rmse": mins[0][1]}, "rolling_baseline": {"mae": mins[1][0], "rmse": mins[1][1]}}
    return {"targets": t}


def _integ(ok=True):
    r = {"max_row_sum_error": 1e-15, "min_entry": 0.0, "p_over_monotonicity_violations": 0, "integer_push_max_error": 0.0,
         "half_line_max_push": 0.0, "invalid_fair_prices": 0 if ok else 1}
    return {"counts": {t: dict(r) for t in L.TARGETS}, "MIN": {"max_row_sum_error": 1e-15, "min_entry": 0.0}}


PART = {"candidate": {"brier": 0.05, "log_loss": 0.19, "calibration_slope": 1.0, "calibration_in_the_large": 0.0},
        "heuristic": {"brier": 0.07, "log_loss": 0.25}}


def test_gates_go_and_boundaries():
    g = L.gates(_counts(), PART, _integ())
    assert g["verdict"] == "GO" and all(g[k]["pass"] for k in g if k != "verdict")
    for slope, ok in ((0.80, True), (1.25, True), (0.7999, False), (1.2501, False)):
        p = {**PART, "candidate": {**PART["candidate"], "calibration_slope": slope}}
        assert L.gates(_counts(), p, _integ())["G5_participation_calibration"]["pass"] is ok
    for citl, ok in ((0.25, True), (-0.25, True), (0.2501, False)):
        p = {**PART, "candidate": {**PART["candidate"], "calibration_in_the_large": citl}}
        assert L.gates(_counts(), p, _integ())["G5_participation_calibration"]["pass"] is ok
    c = _counts()
    c["targets"]["PTS"]["candidate"]["crps"] = 0.7                       # 6 of 7 targets still win
    assert L.gates(c, PART, _integ())["G2_per_target"]["pass"]
    c["targets"]["REB"]["candidate"]["log_score"] = 1.2                  # 5 of 7
    assert not L.gates(c, PART, _integ())["G2_per_target"]["pass"]
    assert L.gates(_counts(), PART, _integ(ok=False))["verdict"] == "NO-GO"


def test_missing_or_nonfinite_gated_values_fail_closed():
    c = _counts()
    c["targets"]["STL"]["candidate"]["log_score"] = None
    g = L.gates(c, PART, _integ())
    assert not g["G1_aggregate"]["pass"] and g["verdict"] == "NO-GO"
    assert not L.gates(_counts(), {}, _integ())["G4_participation"]["pass"]
    c = _counts(mins=((float("nan"), 6.0), (5.0, 7.0)))
    assert not L.gates(c, PART, _integ())["G3_minutes"]["pass"]
    empty = {"counts": {}, "MIN": {"max_row_sum_error": 0.0, "min_entry": 0.0}}
    assert not L.gates(_counts(), PART, empty)["G6_pricing_integrity"]["pass"]


def test_cr1_interval_matches_hand_computation():
    d = np.array([1.0, 2.0, 3.0, 5.0])
    g = np.array(["a", "a", "b", "c"])
    r = L.cr1_interval(d, g)
    m = d.mean()
    s = np.array([(1 - m) + (2 - m), 3 - m, 5 - m])
    se = np.sqrt(3 / 2 * np.sum(s ** 2)) / 4
    assert r["mean_difference"] == m and abs(r["se"] - se) < 1e-15 and r["clusters"] == 3


@pytest.fixture(scope="module")
def validation():
    return LV.validate()


def test_development_reproduction_fit_free(validation):
    ev = validation["evaluation_B"]
    assert ev["fit_calls"] == [0, 0]
    assert ev["reproduction"]["reproduced"], ev["reproduction"]
    assert ev["deterministic_rerun_byte_identical"]
    assert ev["gates"]["verdict"] == "GO" and all(v for k, v in ev["gates"].items() if k != "verdict")
    fc = validation["final_artifact_in_sample_functional_check"]
    assert fc["fit_calls"] == [0, 0] and fc["deterministic_rerun_byte_identical"]
    assert fc["artifact_sha256"] == "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"


def test_recorded_runner_evidence_matches_rerun(validation):
    rec = json.loads(LV.OUT.read_text())
    assert rec["evaluation_B"]["result_sha256"] == validation["evaluation_B"]["result_sha256"]
    assert rec["final_artifact_in_sample_functional_check"]["result_sha256"] == \
        validation["final_artifact_in_sample_functional_check"]["result_sha256"]
    assert rec["lockbox_touched"] is False and rec["season"] == 2025
