"""Morning lifecycle / 08:00 recovery / day-operational semantics (2026-11-15 directive, section 2), driven through the
real stage chain (prod.morning_stages), the 08:00 recover() and a later availability refresh (lifecycle.refresh) with
the fit / forecast / exports faked (fixtures from test_prod_morning_stages). Every scenario checks the required health
fields: MORNING_CHAIN_COMPLETE, DAY_OPERATIONAL, FORECAST_OPERATIONAL_STATE, FIRST_HISTORICAL_FAILURE, RECOVERY_REQUIRED
(+ its reason); every stage is exactly one of PASS / FAIL / DEFERRED / RECOVERED / NOT_REQUIRED once the chain has run,
and "first incomplete stage None" is never emitted. TEST fixtures only."""
from __future__ import annotations

import json

import pandas as pd

from cbb_platform.prod import lifecycle as L, morning_stages as MS
from cbb_platform.walkforward import store as SO

from .test_prod_morning_stages import D, T0800, _chain, _incumbent, _p, _wrap_wf, env  # noqa: F401  (fixture)

T1000 = pd.Timestamp("2026-11-13T15:00:00Z")
REQUIRED = ("MORNING_CHAIN_COMPLETE", "DAY_OPERATIONAL", "FORECAST_OPERATIONAL_STATE", "FIRST_HISTORICAL_FAILURE",
            "RECOVERY_REQUIRED", "RECOVERY_REQUIRED_REASON", "INITIAL_FORECAST", "ACTIVE_BLOCKERS")


def _receipt(env) -> dict:
    r = json.loads(L.health_path(env["life"], D).read_text())["receipt"]
    for k in REQUIRED:
        assert k in r, k
    assert isinstance(r["RECOVERY_REQUIRED"], bool) and r["RECOVERY_REQUIRED_REASON"]
    assert set(r["STAGE_TERMINAL_STATES"].values()) <= set(MS.TERMINAL_STATES), r["STAGE_TERMINAL_STATES"]
    assert not any("first incomplete stage None" in w for w in r["WARNINGS"]), r["WARNINGS"]
    if r["MORNING_CHAIN_COMPLETE"] is False:
        assert r["FIRST_INCOMPLETE_STAGE"] or r["FIRST_HISTORICAL_FAILURE"]
    if r["DAY_OPERATIONAL"]:
        assert r["ACTIVE_BLOCKERS"] == [] and r["FORECAST_OPERATIONAL_STATE"] in ("PASS", "RECOVERED")
    return r


def _timed_forecast(env, monkeypatch):
    fake = L.forecast

    def fc(life, w=L.INITIAL, **k):
        return {**fake(life, w, **k), "executed_at": life.now().isoformat()}
    monkeypatch.setattr(L, "forecast", fc)


def _settle_initial_failure(env, monkeypatch):
    """The INITIAL forecast fails at 07:00 and again on the 08:00 recovery (attempts exhausted: settled FAIL)."""
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    env["calls"]["fail_forecast"] = True
    _chain(env)
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_INITIAL_FORECAST"
    _chain(env)
    fc = _p(env)["INITIAL_FORECAST"]
    assert fc["STATUS"] == MS.FAIL and fc["RETRYABLE"] is False and fc["ATTEMPTS"] == MS.MAX_ATTEMPTS
    env["calls"]["fail_forecast"] = False


# 1 ---------------------------------------------------------------------------------------------------------------
def test_1_all_pass(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    _timed_forecast(env, monkeypatch)
    _chain(env)
    r = _receipt(env)
    assert r["MORNING_CHAIN_COMPLETE"] is True and r["DAY_OPERATIONAL"] is True and r["INITIAL_FORECAST"] == "PASS"
    assert r["FORECAST_OPERATIONAL_STATE"] == "PASS" and r["FORECAST_OPERATIONAL_AT"] == "2026-11-13T12:00:00+00:00"
    assert r["FIRST_HISTORICAL_FAILURE"] is None and r["RECOVERY_REQUIRED"] is False
    assert r["RECOVERY_REQUIRED_REASON"] == "NOT_REQUIRED_MORNING_CHAIN_COMPLETE"
    assert set(r["STAGE_TERMINAL_STATES"].values()) == {MS.PASS}


# 2 ---------------------------------------------------------------------------------------------------------------
def test_2_deferred_walk_forward_resumes_only_the_walk_forward_with_the_original_cutoff(env, monkeypatch):
    _incumbent(env)
    seen = []
    promoting = _wrap_wf(env)                                                  # bound to the real walk_forward

    def wf(life, day, attempt="MORNING_0700", **k):
        seen.append((day, attempt))
        if len(seen) == 1:
            return {"DAILY_REFIT_STATUS": L.DEFERRED_SHARED_LOCK, "DAILY_MODEL_PROMOTION": "NOT_ATTEMPTED", "attempt": attempt}
        return promoting(life, day, attempt, **k)
    monkeypatch.setattr(L, "walk_forward", wf)
    _chain(env)
    p = _p(env)
    assert p["STAGE_TERMINAL_STATES"]["WALK_FORWARD"] == MS.DEFERRED and p["RECOVERY_REQUIRED"] is True
    assert p["RECOVERY_REQUIRED_REASON"].startswith("RESUME_AT_WALK_FORWARD")
    r = _receipt(env)
    assert r["DAY_OPERATIONAL"] is False and any("WALK_FORWARD_" in b for b in r["ACTIVE_BLOCKERS"])
    n_fc = len(env["calls"]["forecast"])
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_WALK_FORWARD"
    _chain(env)
    p = _p(env)
    assert seen[-1] == (D, "RECOVERY_2") and env["calls"]["fit"] == 1 and p["MORNING_COMPLETE"] is True
    assert p["TRAINING_CUTOFF_UTC"] == "2026-11-13T11:00:00+00:00"            # the 06:00 ET cutoff, not 08:00
    assert len(env["calls"]["forecast"]) == n_fc + 1                           # one REFIT_RETRY forecast on the new model
    r = _receipt(env)
    assert r["DAY_OPERATIONAL"] is True and r["RECOVERY_REQUIRED"] is False


# 3 ---------------------------------------------------------------------------------------------------------------
def test_3_failed_initial_with_nothing_replacing_it_is_not_operational(env, monkeypatch):
    _settle_initial_failure(env, monkeypatch)
    p = _p(env)
    assert p["FIRST_INCOMPLETE_STAGE"] is None and p["FIRST_HISTORICAL_FAILURE"] == "INITIAL_FORECAST"
    assert p["RECOVERY_REQUIRED"] is False and p["RECOVERY_REQUIRED_REASON"].startswith("NOT_REQUIRED_SETTLED: INITIAL_FORECAST")
    r = _receipt(env)
    assert r["INITIAL_FORECAST"] == "FAIL" and r["FORECAST_OPERATIONAL_STATE"] == "FAIL"
    assert r["MORNING_CHAIN_COMPLETE"] is False and r["DAY_OPERATIONAL"] is False and r["ACTIVE_BLOCKERS"]
    w = [x for x in r["WARNINGS"] if x.startswith("MORNING_INCOMPLETE")][0]
    assert "chain settled, nothing recoverable; first historical failure INITIAL_FORECAST" in w
    assert MS.recover(env["life"], D)["status"] == "INCOMPLETE_NOT_RECOVERABLE"   # never a fabricated forecast
    assert env["calls"]["forecast"] == [L.INITIAL, L.INITIAL] and env["calls"]["fit"] == 1


# 4 ---------------------------------------------------------------------------------------------------------------
def test_4_failed_initial_then_a_legitimate_later_forecast_recovers_the_operational_state(env, monkeypatch):
    _settle_initial_failure(env, monkeypatch)
    _timed_forecast(env, monkeypatch)
    env["clock"].t = T1000
    out = L.refresh(env["life"], "10:00")                                       # the scheduled availability refresh
    r = _receipt(env)
    assert r["INITIAL_FORECAST"] == "FAIL"                                     # history is never rewritten
    assert r["FORECAST_OPERATIONAL_STATE"] == "RECOVERED"
    assert r["FORECAST_OPERATIONAL_SNAPSHOT_ID"] == out["forecast"]["snapshot_id"]
    assert r["FORECAST_OPERATIONAL_WINDOW"] == out["forecast"]["window"]
    assert r["FORECAST_OPERATIONAL_AT"] == "2026-11-13T15:00:00+00:00"
    assert r["FORECAST_OPERATIONAL_MODEL_VERSION"] == "cbb-jm1-2026-27-20261113"
    assert r["DAY_OPERATIONAL"] is True and r["ACTIVE_BLOCKERS"] == []
    assert env["calls"]["fit"] == 1                                            # no refit


# 5 ---------------------------------------------------------------------------------------------------------------
def test_5_export_failure_blocks_the_day_until_the_exports_are_resumed(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    env["calls"]["fail_exports"] = True
    _chain(env)
    p = _p(env)
    assert p["STAGE_TERMINAL_STATES"]["EXPORTS"] == MS.FAIL and p["RECOVERY_REQUIRED"] is True
    assert p["RECOVERY_REQUIRED_REASON"].startswith("RESUME_AT_EXPORTS")
    r = _receipt(env)
    assert r["DAY_OPERATIONAL"] is False and r["DAY_OPERATIONAL_CONDITIONS"]["EXPORTS"] is False
    env["calls"]["fail_exports"] = False
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_EXPORTS"
    _chain(env)
    r = _receipt(env)
    assert r["DAY_OPERATIONAL"] is True and r["MORNING_CHAIN_COMPLETE"] is True
    assert env["calls"]["fit"] == 1 and env["calls"]["forecast"] == [L.INITIAL] and env["calls"]["exports"] == 2


# 6 ---------------------------------------------------------------------------------------------------------------
def test_6_restart_after_promotion_never_refits(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    assert MS.run_prep(env["life"], D)[0] == 0 and MS.run_walkforward(env["life"], D)[0] == 0
    act = SO.active(env["life"].model_root)["ACTIVE_MODEL_VERSION"]
    assert act == "cbb-jm1-2026-27-20261113"
    # the host restarts: the remaining stages and the 08:00 recovery run in fresh processes
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_INITIAL_FORECAST"
    _chain(env)
    _chain(env)                                                                # and a duplicate chain does nothing
    assert env["calls"]["fit"] == 1 and SO.active(env["life"].model_root)["ACTIVE_MODEL_VERSION"] == act
    p = _p(env)
    assert p["MORNING_COMPLETE"] and p["WALK_FORWARD"]["ATTEMPTS"] == 1 and p["TRAINING_CUTOFF_UTC"] == "2026-11-13T11:00:00+00:00"
    assert _receipt(env)["DAY_OPERATIONAL"] is True


# 7 ---------------------------------------------------------------------------------------------------------------
def test_7_recovery_after_sigterm_resumes_the_killed_stage_only(env, monkeypatch):
    _incumbent(env)
    monkeypatch.setattr(L, "walk_forward", _wrap_wf(env))
    assert MS.run_prep(env["life"], D)[0] == 0 and MS.run_walkforward(env["life"], D)[0] == 0
    with MS.locked(env["life"].ops_root, D) as p:                              # INITIAL forecast SIGTERMed (timeout)
        MS._begin(p, "INITIAL_FORECAST", "WALK_FORWARD")
        p["INITIAL_FORECAST"]["PID"] = 2 ** 22 + 4242
    MS.finalize(env["life"], D, "INITIAL_FORECAST", env={"SERVICE_RESULT": "signal", "EXIT_CODE": "killed", "EXIT_STATUS": "TERM"})
    p = _p(env)
    assert p["INITIAL_FORECAST"]["ERROR_TYPE"] == "SIGTERM" and p["INITIAL_FORECAST"]["RETRYABLE"] is True
    assert p["STAGE_TERMINAL_STATES"]["INITIAL_FORECAST"] == MS.FAIL and p["RECOVERY_REQUIRED"] is True
    env["clock"].t = T0800
    assert MS.recover(env["life"], D)["action"] == "RESUME_AT_INITIAL_FORECAST"
    _chain(env)
    p = _p(env)
    assert env["calls"]["fit"] == 1 and env["calls"]["forecast"] == [L.INITIAL] and p["INITIAL_FORECAST"]["ATTEMPTS"] == 2
    r = _receipt(env)
    assert r["MORNING_CHAIN_COMPLETE"] is True and r["DAY_OPERATIONAL"] is True and r["INITIAL_FORECAST"] == "PASS"


# 8 ---------------------------------------------------------------------------------------------------------------
def test_8_historical_failure_but_operational_day(env, monkeypatch):
    """2026-11-14 shape: INITIAL failed terminally, a later refresh forecast + exports exist -> DAY_OPERATIONAL true while
    MORNING_CHAIN_COMPLETE stays false and the failure is classed RECOVERED_HISTORICAL (not an active blocker)."""
    _settle_initial_failure(env, monkeypatch)
    _timed_forecast(env, monkeypatch)
    env["clock"].t = T1000
    L.refresh(env["life"], "10:00")
    r = _receipt(env)
    assert r["MORNING_CHAIN_COMPLETE"] is False and r["DAY_OPERATIONAL"] is True
    assert r["FIRST_HISTORICAL_FAILURE"] == "INITIAL_FORECAST" and r["INITIAL_FORECAST"] == "FAIL"
    assert r["RECOVERY_REQUIRED"] is False and r["RECOVERY_REQUIRED_REASON"].startswith("NOT_REQUIRED_SETTLED")
    cls = {c["warning"].split(":")[0]: c["class"] for c in r["WARNING_CLASSES"]}
    assert cls["MORNING_INCOMPLETE"] == L.RECOVERED_HISTORICAL and r["ACTIVE_BLOCKERS"] == []
    assert all(v for v in r["DAY_OPERATIONAL_CONDITIONS"].values())
