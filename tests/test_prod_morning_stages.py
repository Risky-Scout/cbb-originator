"""The 07:00 ET morning as five resumable, memory-isolated stages (prod/morning_stages.py) and the 08:00 ET recovery.

Incident 2026-11-13: the monolithic morning process promoted cbb-jm1-2026-27-20261113 at 12:40Z, then was OOM-killed in
the INITIAL forecast at 13:08Z; the 08:00 retry returned NO_OP ("the 07:00 walk-forward was not deferred"). These tests
pin the replacement: durable stage receipts, same-day model evidence (never a refit after a promotion), resume at the
first incomplete stage, the original training cutoff on every retry, one process per stage, walk-forward-only heavy lock.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform.prod import lifecycle as L, morning_stages as MS, ops_store as OS
from cbb_platform.walkforward import config as WC, store as SO

D = date(2026, 11, 13)
T0700 = pd.Timestamp("2026-11-13T12:00:00Z")           # 07:00 ET (EST)
T0800 = pd.Timestamp("2026-11-13T13:00:00Z")
HARNESS = Path(__file__).with_name("morning_stage_harness.py")
SD = C.PROJECT_ROOT / "deploy" / "droplet" / "systemd"


class Clock:
    def __init__(self, t):
        self.t = pd.Timestamp(t)

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    clock = Clock(T0700)
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=tmp_path / "wf" / "cbb-originator",
                  clock=clock)
    calls = {"tick": 0, "exports": 0, "forecast": [], "fit": 0}

    def tick(ctx):
        calls["tick"] += 1
        return {"tick_id": "K1", "status": "OK", "did_work": True}

    def exports_for(lf, d):
        if d != D:                                       # the prep stage's prior-day (Grades D-1) export
            calls["prior_exports"] = calls.get("prior_exports", 0) + 1
            return {"date": d.isoformat(), "grades": {"status": "WRITTEN"}}
        calls["exports"] += 1
        if calls.get("fail_exports"):
            raise RuntimeError("synthetic export failure")
        return {"date": d.isoformat(), "boards": {"status": "WRITTEN", "rows": 10},
                "distributions": {"status": "WRITTEN", "rows": 20}, "grades": {"status": "WRITTEN", "rows": 3}}

    def forecast(lf, w=L.INITIAL, **k):
        calls["forecast"].append(w)
        if calls.get("fail_forecast"):
            raise MemoryError("synthetic")
        act = SO.active(lf.model_root) or {}
        return {"status": "FORECAST", "window": L.window_label(w), "snapshot_id": f"F{len(calls['forecast'])}",
                "model_version": act.get("ACTIVE_MODEL_VERSION", "cbb-jm1-incumbent"), "games_forecast": 22,
                "players_forecast": 622, "pmfs_persisted": 7464, "stat_predictions": 7464}

    monkeypatch.setattr(L.SH, "tick", tick)
    monkeypatch.setattr(L, "exports_for", exports_for)
    monkeypatch.setattr(L, "forecast", forecast)
    return {"life": life, "clock": clock, "calls": calls, "tmp": tmp_path}


def _promoting_runner(env, vid="cbb-jm1-2026-27-20261113"):
    def run(day, root, state_root, now):
        env["calls"]["fit"] += 1
        _store_version(Path(root), day, promote=True)
        return {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED", "ACTIVE_MODEL_VERSION": vid,
                "run_id": f"WF-{day:%Y%m%d}-x"}
    return run


def _store_version(root: Path, day: date, promote: bool, gates_ok: bool = True, cutoff=None) -> str:
    """A minimal valid version directory (hash-consistent manifest + sidecar, gates.json) and optionally its promotion."""
    SO.ensure(root)
    vid = WC.version_id(day)
    vdir = SO.version_dir(root, day)
    if not vdir.exists():
        cand = SO.new_candidate_dir(root, day)
        msha = SO.write_json(cand / SO.FILES["marginal"], {"m": 1})
        rsha = SO.write_json(cand / SO.FILES["residual_j2"], {"MARGINAL_ARTIFACT_SHA256": msha})
        jsha = SO.write_json(cand / SO.FILES["jm1_final"], {"MARGINAL_ARTIFACT_SHA256": msha,
                                                             "RESIDUAL_DEPENDENCE_ARTIFACT_SHA256": rsha})
        SO.write_manifest(cand, {"MODEL_VERSION": vid, "TRAINING_CUTOFF": (cutoff or WC.refit_cutoff(day)).isoformat(),
                                 "STATE_VERSION": "S-x", "INPUT_IDENTITY_SHA": "i", "deterministic_refit": {"BIT_IDENTICAL": True},
                                 "artifacts": {"marginal": {"sha256": msha}, "residual_j2": {"sha256": rsha},
                                               "jm1_final": {"sha256": jsha}}})
        SO.write_json(cand / "gates.json", {"ALL_PASS": gates_ok})
        SO.finalize(root, cand, day)
    if promote:
        SO.promote(root, vid, "DAILY_WALK_FORWARD gates PASS", f"WF-{day:%Y%m%d}-x")
    return vid


def _incumbent(env):
    _store_version(env["life"].model_root, date(2026, 11, 12), promote=True)


def _chain(env):
    out = []
    for st in MS.STAGES:
        out.append(MS.run_stage(env["life"], D, st))
    return out


def _p(env):
    return MS.load(env["life"].ops_root, D)


def _log(env):
    return OS.read_jsonl(MS.day_dir(env["life"].ops_root, D) / "stage_log.jsonl")


# ============================================================================ 25. normal morning
def test_25_normal_morning_completes_and_0800_recovery_is_already_complete(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    codes = [c for c, _ in _chain(env)]
    p = _p(env)
    assert codes == [0, 0, 0, 0, 0]
    assert [p[s]["STATUS"] for s in MS.STAGES] == [MS.PASS] * 5 and p["MORNING_COMPLETE"] is True
    assert p["WALK_FORWARD"]["RUN_ID"] == "WF-20261113-x" and p["WALK_FORWARD"]["ACTIVE_MODEL_VERSION"] == "cbb-jm1-2026-27-20261113"
    assert p["INITIAL_FORECAST"]["GAME_COUNT"] == 22 and p["INITIAL_FORECAST"]["PMF_COUNT"] == 7464
    for k in ("DATE", "CODE_COMMIT", "TRAINING_CUTOFF_UTC", "CREATED_AT", "UPDATED_AT"):
        assert k in p
    assert p["TRAINING_CUTOFF_UTC"] == "2026-11-13T11:00:00+00:00"
    before = dict(env["calls"], forecast=list(env["calls"]["forecast"]))
    env["clock"].t = T0800
    r = MS.recover(env["life"], D)
    assert r["status"] == "ALREADY_COMPLETE" and r["action"] == "ALREADY_COMPLETE"
    assert not MS.resume_request_path(env["life"].ops_root).exists()            # nothing re-started
    _chain(env)                                                                 # even a re-run chain does no work
    assert env["calls"] == before
    h = json.loads(L.health_path(env["life"], D).read_text())["receipt"]
    assert h["MORNING_COMPLETE"] is True and h["RECOVERY_REQUIRED"] is False and h["MORNING_WALK_FORWARD_STATUS"] == "PASS"
    assert h["RECOVERY_LAST_ACTION"] == "ALREADY_COMPLETE" and "MORNING_NOT_RECORDED" not in h["WARNINGS"]


def _wrap_wf(env):
    real = L.walk_forward

    def wf(life, day, attempt="MORNING_0700", runner=None, warm_runner=None):
        return real(life, day, attempt, runner=runner or _promoting_runner(env), warm_runner=warm_runner)
    return wf


# ============================================================================ 26. the actual incident
def test_26_oom_after_promotion_recovery_never_refits_and_completes(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    assert MS.run_prep(env["life"], D)[0] == 0
    assert MS.run_walkforward(env["life"], D)[0] == 0
    assert env["calls"]["fit"] == 1 and _p(env)["WALK_FORWARD"]["STATUS"] == MS.PASS     # receipt persisted
    # the INITIAL forecast process dies (OOM) before its receipt: begin, then the cgroup is killed
    with MS.locked(env["life"].ops_root, D) as p:
        MS._begin(p, "INITIAL_FORECAST", "WALK_FORWARD")
        p["INITIAL_FORECAST"]["PID"] = 2 ** 22 + 12345                               # a dead pid
    MS.finalize(env["life"], D, "INITIAL_FORECAST", env={"SERVICE_RESULT": "oom-kill", "EXIT_CODE": "killed", "EXIT_STATUS": "KILL"})
    p = _p(env)
    assert p["INITIAL_FORECAST"]["STATUS"] == MS.FAIL and p["INITIAL_FORECAST"]["ERROR_TYPE"] == "OOM_KILL"
    assert p["WALK_FORWARD"]["STATUS"] == MS.PASS and p["RECOVERY_REQUIRED"] and p["FIRST_INCOMPLETE_STAGE"] == "INITIAL_FORECAST"
    env["clock"].t = T0800
    r = MS.recover(env["life"], D)
    assert r["status"] == "RESUME_REQUESTED" and r["action"] == "RESUME_AT_INITIAL_FORECAST"
    assert MS.resume_request_path(env["life"].ops_root).exists()
    _chain(env)                                                                 # the idempotent chain, again
    p = _p(env)
    assert env["calls"]["fit"] == 1                                             # NOT refit
    assert env["calls"]["forecast"] == [L.INITIAL] and env["calls"]["exports"] == 1
    assert p["MORNING_COMPLETE"] is True and p["INITIAL_FORECAST"]["ATTEMPTS"] == 2


# ============================================================================ 27. promotion exists, receipt missing
@pytest.mark.parametrize("finalized", [True, False])
def test_27_killed_after_promotion_before_receipt_is_reconstructed_from_the_model_store(env, monkeypatch, finalized):
    _incumbent(env)
    with MS.locked(env["life"].ops_root, D) as p:
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1)
        MS._begin(p, "WALK_FORWARD", "PREP")
        p["WALK_FORWARD"]["PID"] = 2 ** 22 + 54321
    _store_version(env["life"].model_root, D, promote=True)           # promoted, then killed before progress.json
    if finalized:                                                      # ExecStopPost saw the oom-kill
        MS.finalize(env["life"], D, "WALK_FORWARD", env={"SERVICE_RESULT": "oom-kill", "EXIT_CODE": "killed", "EXIT_STATUS": "KILL"})
        wf = _p(env)["WALK_FORWARD"]
        assert wf["STATUS"] == MS.PASS and wf["KILLED_AFTER_PROMOTION"] == "OOM_KILL"
    env["clock"].t = T0800
    r = MS.recover(env["life"], D)
    assert r["walk_forward"]["STATUS"] == MS.PASS and r["action"] == "RESUME_AT_INITIAL_FORECAST"
    wf = _p(env)["WALK_FORWARD"]
    assert wf["RUN_ID"] == "WF-20261113-x" and wf["ACTIVE_MODEL_VERSION"] == "cbb-jm1-2026-27-20261113" and wf["REFIT_SKIPPED"]
    fits = []
    monkeypatch.setattr(L, "walk_forward", lambda *a, **k: fits.append(1) or {})
    _chain(env)
    assert fits == [] and _p(env)["MORNING_COMPLETE"] is True


def test_27b_model_evidence_requires_same_day_gated_promotion_at_the_authorized_cutoff(env):
    root = env["life"].model_root
    assert MS.model_evidence(root, D)["reason"] == "NO_SAME_DAY_VERSION"
    _store_version(root, D, promote=False)
    assert MS.model_evidence(root, D)["reason"] == "NEVER_PROMOTED"
    SO.promote(root, WC.version_id(D), "DAILY_WALK_FORWARD gates PASS", "WF-1")
    ev = MS.model_evidence(root, D)
    assert ev["VALID"] and ev["RUN_ID"] == "WF-1" and ev["TRAINING_CUTOFF_UTC"] == "2026-11-13T11:00:00+00:00"
    d2 = date(2026, 11, 14)
    _store_version(root, d2, promote=True, gates_ok=False)
    assert MS.model_evidence(root, d2)["reason"] == "GATES_NOT_ALL_PASS"
    d3 = date(2026, 11, 15)
    _store_version(root, d3, promote=True, cutoff=WC.refit_cutoff(d3) + pd.Timedelta(hours=2))
    assert MS.model_evidence(root, d3)["reason"].startswith("TRAINING_CUTOFF_MISMATCH")
    (SO.version_dir(root, D) / SO.FILES["marginal"]).chmod(0o644)
    (SO.version_dir(root, D) / SO.FILES["marginal"]).write_text('{"m": 2}')      # tampered artifact
    assert MS.model_evidence(root, D)["reason"].startswith("INTEGRITY")


# ============================================================================ 28. OOM before promotion + PIT cutoff
def test_28_oom_before_promotion_keeps_active_and_retries_with_the_original_cutoff(env, monkeypatch):
    _incumbent(env)
    root = env["life"].model_root
    with MS.locked(env["life"].ops_root, D) as p:
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1)
        MS._begin(p, "WALK_FORWARD", "PREP")
        p["WALK_FORWARD"]["PID"] = 2 ** 22 + 777
    MS.finalize(env["life"], D, "WALK_FORWARD", env={"SERVICE_RESULT": "oom-kill", "EXIT_CODE": "killed", "EXIT_STATUS": "KILL"})
    wf = _p(env)["WALK_FORWARD"]
    assert wf["STATUS"] == MS.FAIL and wf["ERROR_TYPE"] == "OOM_KILL" and wf["RETRYABLE"] and wf["MODEL_DECISION"] is None
    assert SO.active(root)["ACTIVE_MODEL_VERSION"] == "cbb-jm1-2026-27-20261112"             # previous ACTIVE remains
    assert MS.run_forecast(env["life"], D)[0] == 1 and env["calls"]["forecast"] == []        # no decision: no forecast
    env["clock"].t = pd.Timestamp("2026-11-13T18:00:00Z")                                    # a retry at 13:00 ET
    r = MS.recover(env["life"], D)
    assert r["action"] == "RESUME_AT_WALK_FORWARD"
    seen = {}
    from cbb_platform.walkforward import eligibility as EL
    real_asof = EL.state_asof
    monkeypatch.setattr(EL, "state_asof", lambda sr, cut: seen.setdefault("asof", cut) and real_asof(sr, cut))

    def runner(day, root, state_root, now):
        seen.update(day=day, now=now)
        return {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED", "ACTIVE_MODEL_VERSION": "v13",
                "run_id": "WF-retry"}
    code, out = MS.run_walkforward(env["life"], D, runner=runner)
    assert code == 0 and out["status"] == MS.PASS
    assert seen["day"] == D and WC.refit_cutoff(seen["day"]) == pd.Timestamp("2026-11-13T11:00:00Z")
    assert seen["asof"] == pd.Timestamp("2026-11-13T11:00:00Z")                  # state as-of D 06:00 ET, not 13:00
    assert OS.read_jsonl(L.wf_attempts_path(env["life"], D))[-1]["attempt"] == "RECOVERY_2"


def test_28b_point_in_time_retry_selects_only_the_state_legal_at_the_original_cutoff(tmp_path):
    """A state version promoted AFTER D 06:00 ET (here 08:30 ET) is never the training state of D, whenever the retry
    runs: the walk-forward resolves eligibility.state_asof(WC.refit_cutoff(D))."""
    from cbb_platform.prod import state as ST
    from cbb_platform.walkforward import eligibility as EL
    sr = tmp_path / "state"
    from cbb_platform.prod import fixtures as FX
    g = pd.DataFrame([FX._game("G-pit-1", 2027, pd.Timestamp("2026-11-13T00:00Z"), "T-h", "T-a")])
    ST.update({"flagship.games": g}, pd.Timestamp("2026-11-13T10:30Z"), "FIXTURE", None, sr, created_at=pd.Timestamp("2026-11-13T10:30Z"))
    early = ST.current(sr)["manifest"]["state_version"]
    g2 = pd.DataFrame([FX._game("G-pit-1", 2027, pd.Timestamp("2026-11-13T00:00Z"), "T-h", "T-a", hp=70, ap=65)])
    ST.update({"flagship.games": g2}, pd.Timestamp("2026-11-13T13:30Z"), "FIXTURE", None, sr, created_at=pd.Timestamp("2026-11-13T13:30Z"))
    assert ST.current(sr)["manifest"]["state_version"] != early
    assert EL.state_asof(sr, WC.refit_cutoff(D))["state_version"] == early


# ============================================================================ 29. forecast process OOM
def test_29_forecast_oom_recovery_runs_the_forecast_only(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    env["calls"]["fail_forecast"] = True
    assert [c for c, _ in _chain(env)][:3] == [0, 0, 1]
    p = _p(env)
    assert p["INITIAL_FORECAST"]["STATUS"] == MS.FAIL and p["INITIAL_FORECAST"]["RETRYABLE"] and not p["MORNING_COMPLETE"]
    assert p["EXPORTS"]["STATUS"] == MS.NOT_STARTED                               # no cascading false PASS
    env["calls"]["fail_forecast"] = False
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_INITIAL_FORECAST"
    _chain(env)
    assert env["calls"]["fit"] == 1 and env["calls"]["forecast"] == [L.INITIAL, L.INITIAL] and _p(env)["MORNING_COMPLETE"]


# ============================================================================ 30. export failure
def test_30_export_failure_recovery_runs_exports_only(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    env["calls"]["fail_exports"] = True
    assert [c for c, _ in _chain(env)] == [0, 0, 0, 1, 0]
    p = _p(env)
    assert p["EXPORTS"]["STATUS"] == MS.FAIL and p["HEALTH"]["STATUS"] == MS.PASS and not p["MORNING_COMPLETE"]
    h = json.loads(L.health_path(env["life"], D).read_text())["receipt"]
    assert h["MORNING_COMPLETE"] is False and h["RECOVERY_REQUIRED"] is True and h["MORNING_EXPORTS_STATUS"] == "FAIL"
    env["calls"]["fail_exports"] = False
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_EXPORTS"
    _chain(env)
    assert env["calls"]["fit"] == 1 and env["calls"]["forecast"] == [L.INITIAL] and env["calls"]["exports"] == 2
    assert _p(env)["MORNING_COMPLETE"] is True


def test_health_only_resume_and_gate_fail_continues_with_the_incumbent(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", lambda life, day, attempt="MORNING_0700", **k: {
        "DAILY_REFIT_STATUS": "FAIL", "DAILY_MODEL_PROMOTION": "REFUSED", "ACTIVE_MODEL_VERSION": "cbb-jm1-2026-27-20261112",
        "run_id": "WF-refused", "attempt": attempt})
    for st in MS.STAGES[:-1]:
        MS.run_stage(env["life"], D, st)
    p = _p(env)
    wf = p["WALK_FORWARD"]                                                       # CASE 7: deterministic gate failure
    assert wf["STATUS"] == MS.FAIL and wf["ERROR_TYPE"] == MS.MODEL_GATE_FAIL and wf["RETRYABLE"] is False
    assert wf["MODEL_DECISION"] == "INCUMBENT_RETAINED" and env["calls"]["forecast"] == [L.INITIAL]
    assert p["FIRST_INCOMPLETE_STAGE"] == "HEALTH" and not p["MORNING_COMPLETE"]  # CASE 5: health only
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_HEALTH"
    _chain(env)
    p = _p(env)
    assert p["MORNING_COMPLETE"] is True and env["calls"]["forecast"] == [L.INITIAL] and env["calls"]["exports"] == 1
    assert MS.recover(env["life"], D)["status"] == "ALREADY_COMPLETE"            # never re-tried endlessly


def test_shared_lock_deferral_is_retried_once_then_settles_on_the_incumbent(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", lambda life, day, attempt="MORNING_0700", **k: {
        "DAILY_REFIT_STATUS": L.DEFERRED_SHARED_LOCK, "DAILY_MODEL_PROMOTION": "NOT_ATTEMPTED", "attempt": attempt})
    _chain(env)
    p = _p(env)
    assert p["WALK_FORWARD"]["STATUS"] == MS.DEFERRED and p["WALK_FORWARD"]["RETRYABLE"] and env["calls"]["forecast"] == [L.INITIAL]
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_WALK_FORWARD"     # CASE 2
    _chain(env)
    p = _p(env)
    assert p["WALK_FORWARD"]["ATTEMPTS"] == 2 and p["WALK_FORWARD"]["RETRYABLE"] is False and p["MORNING_COMPLETE"]


def test_walk_forward_exception_blocks_the_normal_forecast_and_exhaustion_falls_back(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", lambda life, day, attempt="MORNING_0700", **k: {
        "DAILY_REFIT_STATUS": "FAIL", "DAILY_MODEL_PROMOTION": "NOT_PROMOTED", "reason": "ValueError: boom"})
    codes = [c for c, _ in _chain(env)]
    assert codes[1] == 1 and codes[2] == 1 and env["calls"]["forecast"] == []    # no usable decision: no forecast
    env["clock"].t = T0800
    MS.recover(env["life"], D)
    codes = [c for c, _ in _chain(env)]
    p = _p(env)
    assert codes[1] == 0 and p["WALK_FORWARD"]["MODEL_DECISION"] == "INCUMBENT_RETAINED" and not p["WALK_FORWARD"]["RETRYABLE"]
    assert env["calls"]["forecast"] == [L.INITIAL] and p["MORNING_COMPLETE"] is True


def test_double_walk_forward_oom_second_recovery_pass_forecasts_with_the_incumbent(env):
    _incumbent(env)
    with MS.locked(env["life"].ops_root, D) as p:
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1)
    for _ in range(2):                                                           # 07:00 attempt and the recovery attempt
        with MS.locked(env["life"].ops_root, D) as p:
            MS._begin(p, "WALK_FORWARD", "PREP")
            p["WALK_FORWARD"]["PID"] = 2 ** 22 + 99
        MS.finalize(env["life"], D, "WALK_FORWARD", env={"SERVICE_RESULT": "oom-kill", "EXIT_CODE": "killed", "EXIT_STATUS": "KILL"})
        if _ == 0:
            assert MS.recover(env["life"], D)["action"] == "RESUME_AT_WALK_FORWARD"
    wf = _p(env)["WALK_FORWARD"]
    assert wf["ATTEMPTS"] == 2 and not wf["RETRYABLE"] and wf["MODEL_DECISION"] == "INCUMBENT_RETAINED"
    MS.resume_request_path(env["life"].ops_root).unlink()
    MS.run_health(env["life"], D)                                                # pass 1 ends: health asks for pass 2
    req = json.loads(MS.resume_request_path(env["life"].ops_root).read_text())
    assert req["resume_at"] == "INITIAL_FORECAST" and req["pass"] == 2
    _chain(env)
    p = _p(env)
    assert env["calls"]["forecast"] == [L.INITIAL] and p["MORNING_COMPLETE"] and p["RECOVERY"]["ACTIVE"] is False


def test_reconciled_missed_forecast_is_never_fabricated(env):
    _incumbent(env)
    _store_version(env["life"].model_root, D, promote=True)
    OS.append_jsonl(L.wf_attempts_path(env["life"], D), [{"attempt": "MORNING_0700", "started_at": "2026-11-13T12:00:40+00:00",
                    "finished_at": "2026-11-13T12:40:14+00:00", "DAILY_REFIT_STATUS": "PASS",
                    "memory_mb": {"end": {"VmHWM": 3075.7, "VmRSS": 311.1}}}])
    env["clock"].t = pd.Timestamp("2026-11-13T19:30:00Z")
    r = MS.reconcile_missed_morning(env["life"], D, "MORNING_OOM_AFTER_PROMOTION", "wf attempt started in the 07:00 process")
    s = r["summary"]
    assert s["MORNING_WALK_FORWARD_STATUS"] == "PASS" and s["MORNING_ACTIVE_MODEL"] == "cbb-jm1-2026-27-20261113"
    assert s["MORNING_INITIAL_FORECAST_STATUS"] == "FAIL" and s["MORNING_INITIAL_FORECAST_ERROR"] == "MORNING_OOM_AFTER_PROMOTION"
    assert s["MORNING_INITIAL_FORECAST_HISTORICAL_STATUS"] == "MISSED_DUE_TO_MORNING_OOM"
    assert _p(env)["WALK_FORWARD"]["PEAK_RSS_MB"] == 3075.7
    rec = MS.recover(env["life"], D)
    assert rec["action"] == "RESUME_AT_EXPORTS"                                 # the first stage with work left
    _chain(env)
    p = _p(env)
    assert env["calls"]["forecast"] == [] and env["calls"]["fit"] == 0 and env["calls"]["tick"] == 0   # nothing fabricated
    assert p["EXPORTS"]["STATUS"] == MS.PASS and p["MORNING_COMPLETE"] is False and p["RECOVERY_REQUIRED"] is False
    assert MS.recover(env["life"], D)["status"] == "INCOMPLETE_NOT_RECOVERABLE"
    h = json.loads(L.health_path(env["life"], D).read_text())["receipt"]
    assert h["MORNING_WALK_FORWARD_STATUS"] == "PASS" and "MORNING_NOT_RECORDED" not in h["WARNINGS"]
    assert any("MEMORY_HIGH_EXCEEDED: WALK_FORWARD" in w for w in h["WARNINGS"])


def test_finalize_classifies_orchestration_failures():
    c = MS.classify_service_result
    assert c("success", "exited", "0") is None and c("oom-kill", "killed", "KILL") == "OOM_KILL"
    assert c("timeout", "killed", "TERM") == "TIMEOUT" and c("signal", "killed", "TERM") == "SIGTERM"
    assert c("signal", "killed", "KILL") == "SIGKILL" and c("exit-code", "exited", "1") == "UNEXPECTED_CHILD_EXIT"


# ============================================================================ subprocess: real process boundaries
def _h(tmp, now, *args, **popen):
    base = [sys.executable, str(HARNESS), "--ops-root", str(tmp / "ops"), "--prod-root", str(tmp / "prod"),
            "--model-root", str(tmp / "wf" / "cbb-originator"), "--now", str(pd.Timestamp(now).isoformat())]
    env = {**os.environ, "PYTHONPATH": str(C.PROJECT_ROOT)}
    env.pop(L.HEAVY_LOCK_ENV, None)
    env.pop("INVOCATION_ID", None)
    if popen.pop("popen", False):
        return subprocess.Popen(base + list(args), stdout=subprocess.PIPE, text=True, env=env, **popen)
    r = subprocess.run(base + list(args), capture_output=True, text=True, env=env, timeout=300)
    assert r.stdout.strip(), r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]) | {"stderr": r.stderr}


def test_33_34_each_stage_is_its_own_process_and_the_walk_forward_memory_is_gone_before_the_forecast(tmp_path):
    """Directive 33 + 34: distinct PIDs, the previous stage's process no longer exists when the next starts, and the
    walk-forward's resident memory (a 600 MB touched allocation here) is NOT carried into the forecast process -- OS-level
    reclamation by process exit, not gc.collect()."""
    pids = {}
    for st in ("prep", "walkforward", "forecast", "exports", "health"):
        args = ["stage", st, "--mode", "fake"] + (["--alloc-mb", "600"] if st == "walkforward" else [])
        r = _h(tmp_path, T0700, *args)
        assert r["code"] == 0, r["stderr"]
        pids[st] = r["pid"]
        assert not MS.pid_alive(r["pid"])                                       # exited before the next stage starts
    assert len(set(pids.values())) == 5
    p = MS.load(tmp_path / "ops", D)
    assert [p[s]["PID"] for s in MS.STAGES[:-1]] == [pids[s] for s in ("prep", "walkforward", "forecast", "exports")]
    assert p["WALK_FORWARD"]["PREV_STAGE_PID_EXITED"] and p["INITIAL_FORECAST"]["PREV_STAGE_PID_EXITED"]
    assert p["EXPORTS"]["PREV_STAGE_PID_EXITED"]
    wf_peak, fc_start = p["WALK_FORWARD"]["PEAK_RSS_MB"], p["INITIAL_FORECAST"]["START_RSS_MB"]
    assert wf_peak >= 600 and fc_start < wf_peak - 450, (wf_peak, fc_start)
    assert p["MORNING_COMPLETE"] is True
    for s in MS.STAGES[:-1]:
        assert all(k in p[s] for k in MS.TELEMETRY)


def test_31_0800_recovery_while_the_0700_stage_still_runs_waits_then_resumes_the_first_incomplete_stage(tmp_path):
    lock = tmp_path / "cbb-originator.writer.lock"
    rel = tmp_path / "release"
    with MS.locked(tmp_path / "ops", D) as p:
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1)
    holder = _h(tmp_path, T0700, "hold", "--writer-lock", str(lock), "--release-file", str(rel), popen=True)
    assert json.loads(holder.stdout.readline())["event"] == "HOLDING"           # 07:00 walk-forward RUNNING, lock held
    rec = _h(tmp_path, T0800, "recover", "--writer-lock", str(lock), popen=True)
    time.sleep(1.5)
    assert rec.poll() is None                                                   # blocked on the writer lock: no 2nd writer
    p = MS.load(tmp_path / "ops", D)
    assert p["WALK_FORWARD"]["STATUS"] == MS.RUNNING and p.get("RECOVERY_LAST_ACTION") is None
    rel.write_text("go")
    assert holder.wait(timeout=60) == 0
    out = json.loads(rec.communicate(timeout=120)[0].strip().splitlines()[-1])["out"]
    assert out["status"] == "RESUME_REQUESTED" and out["action"] == "RESUME_AT_INITIAL_FORECAST"   # not the old NO_OP
    assert out["walk_forward"]["STATUS"] == MS.PASS


def test_31b_a_stage_alive_without_the_lock_defers_the_recovery(env):
    with MS.locked(env["life"].ops_root, D) as p:
        MS._begin(p, "WALK_FORWARD", "PREP")                                    # our own (alive) pid
    assert MS.recover(env["life"], D)["status"] == "DEFERRED_STAGE_RUNNING"
    assert not MS.resume_request_path(env["life"].ops_root).exists()


# ============================================================================ 32. heavy lock: walk-forward only
def test_32_only_the_walk_forward_stage_takes_the_shared_heavy_lock(env, monkeypatch):
    _incumbent(env)
    lock = env["tmp"] / "heavy.lock"
    monkeypatch.setenv(L.HEAVY_LOCK_ENV, str(lock))
    monkeypatch.setenv(L.HEAVY_WAIT_ENV, "0.2")
    holder = os.open(lock, os.O_WRONLY | os.O_CREAT)
    fcntl.flock(holder, fcntl.LOCK_EX)                                          # NBA (or any) heavy job holds it
    try:
        t0 = time.monotonic()
        assert MS.run_prep(env["life"], D)[0] == 0
        code, wf = MS.run_walkforward(env["life"], D, runner=_promoting_runner(env))
        assert code == 0 and wf["status"] == MS.DEFERRED and wf["DAILY_REFIT_STATUS"] == L.DEFERRED_SHARED_LOCK
        assert env["calls"]["fit"] == 0
        for st in ("INITIAL_FORECAST", "EXPORTS", "HEALTH"):                    # never blocked by the heavy lock
            assert MS.run_stage(env["life"], D, st)[0] == 0
        assert time.monotonic() - t0 < 5
    finally:
        os.close(holder)
    run = (C.PROJECT_ROOT / "deploy" / "droplet" / "bin" / "cbb-originator-run").read_text()
    blk = run[run.index("morning-stage)     RUN="):run.index("morning-finalize)  RUN=")]
    assert '[ "$2" = "walkforward" ] || unset CBB_HEAVY_LOCK CBB_HEAVY_LOCK_WAIT_SEC' in blk
    for u in ("morning-prep", "morning-forecast", "morning-exports", "morning-health", "morning-recover"):
        body = "\n".join(l for l in (SD / f"cbb-originator-{u}.service").read_text().splitlines() if not l.startswith("#"))
        assert "heavy" not in body and "/run/lock" not in body, u
    assert "CBB_HEAVY_LOCK=/run/lock/sports-models/heavy.lock" in (SD / "cbb-originator-walkforward.service").read_text()


# ============================================================================ systemd wiring
def _directives(name: str) -> dict:
    out = {}
    for ln in (SD / name).read_text().splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


def test_systemd_chain_is_one_entrypoint_with_requires_after_and_separate_cgroups():
    P, W, F, E, H = ("cbb-originator-morning-prep.service", "cbb-originator-walkforward.service",
                     "cbb-originator-morning-forecast.service", "cbb-originator-morning-exports.service",
                     "cbb-originator-morning-health.service")
    assert _directives("cbb-originator-morning.timer")["Unit"] == ["cbb-originator-morning.target"]
    t = _directives("cbb-originator-morning.target")
    assert t["Wants"][0].split() == [P, W, F, E, H] and t["After"][0].split() == [P, W, F, E, H]
    assert t["StopWhenUnneeded"] == ["yes"]                                     # re-triggerable every morning
    for prev, nxt in ((P, W), (W, F), (F, E)):
        d = _directives(nxt)
        assert d["Requires"] == [prev] and d["After"] == [prev], nxt
    h = _directives(H)
    assert h["Wants"][0].split() == [P, W, F, E] and h["After"][0].split() == [P, W, F, E] and "Requires" not in h
    for u, st in ((P, "prep"), (W, "walkforward"), (F, "forecast"), (E, "exports"), (H, "health")):
        d = _directives(u)
        assert d["Type"] == ["oneshot"] and d["Restart"] == ["no"] and "RemainAfterExit" not in d
        assert d["ExecStart"] == [f"/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run morning-stage {st}"]
        assert d["ExecStopPost"] == [f"/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run morning-finalize {st}"]
        assert d["MemorySwapMax"] == ["0"]
    for u in (P, W, F, E):
        assert _directives(u)["MemoryHigh"] == ["2800M"] and _directives(u)["MemoryMax"] == ["3200M"]
    r = _directives("cbb-originator-morning-recover.service")
    assert set(" ".join(r["After"]).split()) >= {P, W, F, E, H, "cbb-originator-morning.target"}
    assert r["ExecStartPost"] == ["+/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-morning-resume"]
    assert _directives("cbb-originator-morning-recover.timer")["Unit"] == ["cbb-originator-morning-recover.service"]
    timers = [p.name for p in SD.glob("*.timer") if "07:00:00" in p.read_text()]
    assert timers == ["cbb-originator-morning.timer"]                           # exactly ONE 07:00 entrypoint
    assert not (SD / "cbb-originator-morning.service").exists() and not (SD / "cbb-originator-refit-retry.timer").exists()


def test_resume_script_restarts_the_chain_only_on_request_and_at_most_twice(tmp_path):
    script = C.PROJECT_ROOT / "deploy" / "droplet" / "bin" / "cbb-originator-morning-resume"
    log = tmp_path / "calls"
    fake = tmp_path / "systemctl"
    req = tmp_path / "prod" / "ops" / "morning" / "RESUME_REQUEST"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n[ -f {tmp_path}/again ] && rm {tmp_path}/again && touch {req}\nexit 0\n')
    fake.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "CBB_PROD_ROOT": str(tmp_path / "prod"), "CBB_SYSTEMCTL": str(fake)}
    assert subprocess.run(["bash", str(script)], env=env).returncode == 0 and not log.exists()   # no request: nothing
    req.parent.mkdir(parents=True)
    req.write_text("{}")
    (tmp_path / "again").write_text("")
    assert subprocess.run(["bash", str(script)], env=env).returncode == 0
    assert log.read_text().splitlines() == ["start cbb-originator-morning.target"] * 2 and not req.exists()
