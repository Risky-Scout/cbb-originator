"""Lockbox readiness for the CBB pre-lockbox candidate (cbb_platform/lockbox_readiness.py, record content 3.0.0): the
r2 candidate is the original model plus tracked STL evidence only, the lockbox is still closed, the guard patches are
prepared but unapplied, the environment lock reproduces, G1-G6 are verbatim, approval is withheld and the records are
deterministic. Nothing here reads 2025-26."""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from cbb_platform import config as C, lockbox, props_v0 as P, lockbox_readiness as L

FROZEN = ["cbb_platform/props_v0.py", "cbb_platform/props_v0_eval.py", "cbb_platform/props_v0_participation.py",
          "cbb_platform/props_v1_pricing.py", "cbb_platform/props_v1_refit.py", "cbb_platform/lockbox.py",
          "cbb_platform/source_fetch.py", "cbb_platform/stage3/spec.py", "modeling/player_props_v0/config.json", L.ARTIFACT]
G1_G6_2_0_0 = {   # scorecard content 2.0.0 (sha256 8b2371a9...9013, preserved in the external archive)
    "G1_aggregate": "candidate aggregate log score < baseline AND candidate aggregate CRPS < baseline",
    "G2_per_target": "candidate beats baseline on BOTH log score and CRPS for >= 6 of 7 count targets",
    "G3_minutes": "candidate MAE < baseline MAE AND candidate RMSE < baseline RMSE",
    "G4_participation": "candidate Brier < heuristic Brier AND candidate log loss < heuristic log loss",
    "G5_participation_calibration": "calibration slope in [0.80, 1.25] AND |calibration-in-the-large| <= 0.25",
    "G6_pricing_integrity": "every PMF row sums to 1 within 1e-9, no negative entry, 0 P(over) monotonicity violations, "
                            "push semantics exact, every fair price finite where 0 < p < 1",
    "verdict": "GO only if G1-G6 all hold; any failure = NO-GO for this frozen candidate, never tuned on (protocol §3)"}


def test_r2_candidate_is_the_original_plus_evidence_only():
    for tag in (L.MODEL_TAG, L.ORIGINAL_TAG):
        assert subprocess.run(["git", "diff", "--quiet", tag, "--", *FROZEN], cwd=C.PROJECT_ROOT).returncode == 0
    ident = L.identity()
    assert ident["pass"] and ident["tag_commit"] == L.MODEL_COMMIT and ident["original_tag_preserved"]
    assert ident["files_changed_original_to_r2"] == sorted(L.STL_EVIDENCE)
    assert ident["artifact_sha256_at_tag"] == ident["artifact_sha256_at_original_tag"] == L.ARTIFACT_SHA256
    assert L.stl_evidence()["pass"]


def test_2025_26_still_inaccessible_and_approval_withheld():
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_season(lockbox.LOCKBOX_SEASON)
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_request("cbbd", "games", {"season": lockbox.LOCKBOX_SEASON})
    with pytest.raises(lockbox.LockboxViolation):
        P.load_frames(P.load_config(), seasons=[lockbox.LOCKBOX_SEASON])
    assert not hasattr(lockbox, "opening_authorized")                  # no guard patch is applied
    assert not (C.MANIFESTS / "LOCKBOX_OPEN_DECISION_2025_26.json").exists()
    rec = json.loads(L.LEDGER_PATH.read_text())
    assert rec["lockbox_open"] is False and rec["lockbox_touched"] is False and rec["touched_2025_26"] is False
    assert rec["approval_record"]["lockbox_open"] is False and rec["approval_record"]["approved_by"] == []
    assert rec["approval_record"]["status"] == "NOT_GRANTED" and rec["LOCKBOX_OPEN_APPROVAL"] == "WITHHELD"
    assert rec["gates"]["LOCKBOX_OPEN_APPROVAL"]["status"] == "WITHHELD"
    assert rec["LOCKBOX_READY"] in ("FAIL", "FAIL_APPROVAL_ONLY") and rec["guard"]["applied"] is False
    assert (rec["LOCKBOX_READY"] == "FAIL_APPROVAL_ONLY") == (rec["NON_APPROVAL_LOCKBOX_GATES"] == "PASS")


def test_guard_patches_prepared_not_applied():
    for patch in (L.GUARD_PATCH, L.GUARD_PATCH_V3):
        assert subprocess.run(["git", "apply", "--check", patch], cwd=C.PROJECT_ROOT).returncode == 0
        text = (C.PROJECT_ROOT / patch).read_text()
        assert "OPEN_DECISION_SHA256: str | None = None" in text                  # closed by default even once applied
        assert "s > LOCKBOX_SEASON" in text and "after {LOCKBOX_LABEL}" in text   # later seasons stay refused


def test_scorecard_g1_g6_verbatim_and_rulings_recorded():
    sc = L.scorecard()
    assert sc["pass_criteria"] == G1_G6_2_0_0 == L.PASS_CRITERIA
    na = sc["not_applicable_by_ruling"]
    assert set(na) == {"market_benchmark", "energy_variogram_scores", "five_pit_checkpoints"}
    assert sc["candidate"]["model_tag"] == L.MODEL_TAG and sc["candidate"]["artifact_sha256"] == L.ARTIFACT_SHA256
    assert "FAILS its gate" in sc["pass_fail_semantics"]["missing_data"]


def test_model_ready_is_candidate_specific_and_a2_is_not_marked_complete():
    """Ruling R: CANDIDATE_MODEL_READY passes only when every other non-approval gate passes; the Blueprint dataset-level
    A2 program stays recorded with its true state and the dataset manifest is not promoted."""
    dm = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    assert dm["status"].startswith("RESEARCH_CANDIDATE")
    assert subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "data/manifests/DATASET_MANIFEST.json"], cwd=C.PROJECT_ROOT).returncode == 0
    rec = json.loads(L.LEDGER_PATH.read_text())
    a2 = rec["gates"]["DATASET_MODEL_READY_BLUEPRINT_A2"]
    assert a2["status"] == "OPEN" and not all(a2["evidence"]["phase_a2"].values())
    assert {k for k, v in a2["evidence"]["phase_a2"].items() if v is False} >= set(a2["evidence"]["a2_n_a_for_v1"])
    cm = rec["gates"]["CANDIDATE_MODEL_READY"]
    others = [k for k, v in rec["gates"].items() if k not in ("CANDIDATE_MODEL_READY", "LOCKBOX_OPEN_APPROVAL", *L.INFORMATIONAL)]
    assert set(cm["evidence"]["required_gates"]) == set(others)
    assert (cm["status"] == "PASS") == all(rec["gates"][k]["status"] in L.NON_APPROVAL_OK for k in others)
    assert rec["MODEL_READY"] == cm["status"] and rec["LOCKBOX_OPEN_APPROVAL"] == "WITHHELD"


def test_route_closure_recorded_with_measured_e8_and_its_classification():
    rec = json.loads(L.LEDGER_PATH.read_text())
    r = rec["cbbd_route"]
    assert r["module_sha256"] == L.sha(L.ROUTE_MODULE) and r["reconciler_sha256"] == L.sha(L.RECONCILER)
    assert r["reconciliation_report_sha256"] == L.sha(L.ROUTE_FULL) and r["pit_audit_sha256"] == L.sha(L.ROUTE_PIT)
    assert r["CBBD_INGESTION_ROUTE"] == "PASS" and r["ANTI_LEAKAGE_CONTRACT"] == "PASS" and r["PIT_VIOLATIONS"] == 0
    assert r["E8_FROZEN_REFERENCE_COMPARISON"] == "FAIL" and r["E8_MAX_GATED_METRIC_DIFFERENCE"] > 1e-3   # measured, not hidden
    assert r["E8_DIFFERENCE_CLASSIFICATION"] == "EXPLAINED_PROVIDER_DATA_REVISION" and r["E8_GATE_VERDICTS_IDENTICAL"] == "PASS"
    assert r["DETERMINISTIC_A_B"] == "PASS" and r["FIT_CALLS"] == 0 and r["PREDICTIVE_SPEC_CHANGED"] is False
    full = json.loads((C.PROJECT_ROOT / L.ROUTE_FULL).read_text())
    assert full["route_module_sha256"] == r["module_sha256"] and full["verdict"]["E8"] == "FAIL"
    assert rec["starter_pack"]["registered_data_files"] == 111
    assert rec["starter_pack"]["registered_data_digest"] == rec["starter_pack"]["current_data_digest"]
    assert rec["touched_2025_26"] is False and rec["used_2026_27_preseason_data"] is False


def test_guard_design_approved_by_exact_bytes_and_not_applied():
    g = L.guard()
    assert g["design_approved"] and g["applied"] is False and g["live_guard_closed"] and g["decision_record_absent"]
    assert g["v3"]["opens_stats_team_season"] and g["v3"]["closed_by_default"] and g["v3"]["later_seasons_refused"]
    assert g["scoring_enable"]["applies_after_v3"] and g["scoring_enable"]["enables_only_when_authorized"]
    assert all(L.sha(p) == h for p, h in L.APPROVED_DESIGN_SHA256.items())


def test_environment_lock_reproducible():
    rec = json.loads(L.ENV_PATH.read_text())
    assert L.environment()["environment_fingerprint"] == rec["environment_fingerprint"]
    lock = L.lock_matches_venv()
    assert lock["pass"] and lock["all_wheels_sha256_hashed"] and set(lock["locked_not_installed"]) <= {"colorama", "tzdata"}
    assert lock["requirements_lock_matches_uv_lock"] and lock["requirements_lock_all_hashed"]


def test_readiness_records_deterministic():
    env = L.environment()
    assert json.dumps(L.scorecard(), indent=1) + "\n" == L.SCORECARD_PATH.read_text()
    assert json.dumps(L.readiness(env), indent=1) + "\n" == L.LEDGER_PATH.read_text()      # regenerated byte-identically
    rec = json.loads(L.LEDGER_PATH.read_text())
    assert rec["model_artifact"]["artifact_sha256"] == L.ARTIFACT_SHA256 and rec["model_artifact"]["code_sha"] == L.MODEL_COMMIT
    assert rec["candidate"]["authoritative_tag"] == L.MODEL_TAG and rec["candidate"]["original_commit"] == L.ORIGINAL_COMMIT
    assert rec["scorecard"]["sha256"] == hashlib.sha256(L.SCORECARD_PATH.read_bytes()).hexdigest()
    assert rec["data_opening_plan"]["sha256"] == L.sha(L.PLAN_PATH)
    assert rec["model_artifact"]["environment_fingerprint"] == env["environment_fingerprint"]
    assert set(rec["model_artifact"]["eligibility_manifests"]) == {"ELIG_PARTICIPATION", "ELIG_MINUTES", "ELIG_BOX_COUNTS"}
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    assert hashlib.sha256(L._canon(body).encode()).hexdigest() == rec["record_sha256"]
