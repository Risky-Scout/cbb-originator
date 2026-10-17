"""Post-lockbox production baseline (state POST_LOCKBOX_CLOSED_GO, cbb_platform/lockbox_state.py): the 2025-26 lockbox was
opened exactly once under the recorded authorization, exactly one fit-free scoring run produced GO, the guard is closed
again, 2025-26 is refused without an authorization and 2026-27 is refused, the model artifact and predictive surface are
unchanged, and genuine PIT violations are 0. The pre-opening state is certified at readiness commit d65aac9
(tests/test_lockbox_readiness.py, tests/test_guard_v2_review.py). Read-only: no fetch, build, scoring or fit."""
from __future__ import annotations

import json
import os

import pytest

from cbb_platform import config as C, lockbox, lockbox_state as S

pytestmark = pytest.mark.skipif(S.current() != S.POST_LOCKBOX_CLOSED_GO, reason="not the post-lockbox state")


@pytest.fixture(scope="module")
def baseline():
    return S.verify()


def test_state_is_post_lockbox_closed_go(baseline):
    assert S.current() == S.POST_LOCKBOX_CLOSED_GO == baseline["state"]
    assert baseline["pass"]


def test_opened_exactly_once_under_authorization(baseline):
    o = baseline["opening"]
    assert o["OPENED_EXACTLY_ONCE_UNDER_AUTHORIZATION"]
    assert o["commits_binding_the_guard"] == [S.OPENING_COMMIT] and o["binding_at_opening_commit"] == S.DECISION_SHA256
    assert o["decision_record_commits"] == [S.OPENING_COMMIT] and o["decision_lockbox_open"]
    assert o["opening_commit_parent_is_readiness"]


def test_exactly_one_scoring_run_produced_go(baseline):
    s = baseline["scoring"]
    assert s["EXACTLY_ONE_SCORING_RUN_GO"] and s["verdict"] == "GO" and all(s["gates_pass"].values())
    assert s["result_commits"] == s["scoring_run_commits"] == [S.CLOSURE_COMMIT] and s["go_tag_commit"] == S.CLOSURE_COMMIT
    assert s["result_file_sha256"] == S.RESULT_FILE_SHA256
    assert s["result_sha256"] == s["scoring_run_result_sha256"] == S.RESULT_SHA256
    assert s["fit_calls"] == 0 and s["fit_free"] is True and s["refit_recalibrated_or_selected"] is False


def test_guard_is_closed_now(baseline):
    assert lockbox.OPEN_DECISION_SHA256 is None and not lockbox.opening_authorized()
    g = baseline["guard"]
    assert g["GUARD_CURRENTLY_OPEN"] is False and g["guard_v3_and_scoring_enable_applied"]
    assert not any(b["authorized"] for b in g["behaviour"].values())       # the flag alone never opens it


def test_lockbox_access_without_authorization_refused(baseline):
    assert baseline["summary"]["CURRENT_LOCKBOX_ACCESS_WITHOUT_AUTHORIZATION"] == "REFUSED"
    for b in baseline["guard"]["behaviour"].values():
        assert b["season_2026"] and b["cbbd_2026"] and b["cbbd_2026_box"] and b["response_2026"] and b["stage3_2026"]
        assert b["model_seasons_max"] == 2025 and b["evaluation_seasons"] == [] and b["audited_seasons"] == [2005, 2025]
    if os.environ.get("LOCKBOX_OPEN_2025_26") != "1":
        assert C.AUDITED_SEASONS == (2005, 2025) and C.schema_sql() == C.SCHEMA_SQL.read_text()
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_season(lockbox.LOCKBOX_SEASON)


def test_2027_access_refused(baseline):
    assert baseline["summary"]["2027_ACCESS"] == "REFUSED"
    for b in baseline["guard"]["behaviour"].values():
        assert b["cbbd_2027"] and b["cbbd_2027_box"] and b["stage3_2027"]
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_request("cbbd", "games", {"season": lockbox.LOCKBOX_SEASON + 1})


def test_model_artifact_unchanged_no_refit_no_recalibration(baseline):
    m, s = baseline["model"], baseline["summary"]
    assert s["MODEL_ARTIFACT_SHA256"] == S.ARTIFACT_SHA256 and m["MODEL_ARTIFACT_UNCHANGED"]
    assert m["artifact_commits_after_r2"] == [] and m["scorecard_sha256"] == S.SCORECARD_SHA256
    assert s["PREDICTIVE_SPEC_CHANGED"] is False and s["REFIT_PERFORMED"] is False and s["RECALIBRATION_PERFORMED"] is False
    # the only predictive-surface changes since r2 are the reviewed scoring-enable season-window gating, byte for byte
    assert m["changed_files_equal_reviewed_patch_output"] and m["predictive_files_dirty"] == []
    assert set(m["predictive_files_changed_since_r2"]) <= {"cbb_platform/props_v0.py", "cbb_platform/props_v0_participation.py",
                                                         "cbb_platform/stage3/spec.py"}


def test_genuine_pit_violations_zero(baseline):
    p = baseline["pit"]
    assert p["GENUINE_PIT_VIOLATIONS"] == 0 and baseline["summary"]["GENUINE_PIT_VIOLATIONS"] == 0
    assert p["lockbox_audit"]["UNAUTHORIZED_FUTURE_SEASON_ROWS"] == 0 and p["lockbox_audit"]["PIT_VIOLATIONS"] == 0
    assert p["lockbox_audit"]["season_rule"]["mode"] == "LOCKBOX_OPEN_2025_26" and p["lockbox_audit_module_is_current"]
    assert p["development_audit_PIT_VIOLATIONS"] == 0 and p["development_audit_module_at_readiness_commit"]
    assert p["current_season_rule"]["mode"] == "CLOSED"                    # this tree audits closed: 2026+ unauthorized


def test_pre_lockbox_readiness_was_unopened_at_d65aac9(baseline):
    h = baseline["historical_pre_lockbox"]
    assert h["commit"] == S.READINESS_COMMIT and h["unopened"]
    assert h["decision_record_absent"] and h["result_absent"] and h["guard_patches_not_applied"]


def test_committed_baseline_record_is_current(baseline):
    rec = json.loads(S.OUT.read_text())
    assert rec == json.loads(json.dumps({k: v for k, v in baseline.items() if k != "head"}, sort_keys=True))
