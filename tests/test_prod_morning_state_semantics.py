"""Morning state machine + health semantics (2026-11-14): INITIAL_FORECAST FAIL is preserved, the day's operational
forecast state is reported separately, FIRST_INCOMPLETE_STAGE / FIRST_HISTORICAL_FAILURE are never both None for an
incomplete morning, and every health warning carries a class. TEST fixtures only."""
import itertools
from datetime import date

import pandas as pd
import pytest

from cbb_platform.prod import lifecycle as L, morning_stages as MS
from cbb_platform.walkforward import eligibility as EL

DAY = date(2026, 11, 14)


def _nov14():
    """progress.json of 2026-11-14 as production recorded it (reduced to the state-machine fields)."""
    p = MS.new_progress(DAY)
    p["PREP"].update(STATUS="PASS", FINISHED_AT="2026-11-14T12:01:14Z", RETRYABLE=False, ATTEMPTS=1)
    p["WALK_FORWARD"].update(STATUS="PASS", FINISHED_AT="2026-11-14T12:40:17Z", RETRYABLE=False, ATTEMPTS=1,
                             DAILY_REFIT_STATUS="PASS", DAILY_MODEL_PROMOTION="PROMOTED",
                             ACTIVE_MODEL_VERSION="cbb-jm1-2026-27-20261114", PEAK_RSS_MB=2844.4)
    p["INITIAL_FORECAST"].update(STATUS="FAIL", FINISHED_AT="2026-11-14T13:05:44Z", RETRYABLE=False, ATTEMPTS=2,
                                 ERROR_TYPE="EXCEPTION", ERROR_DETAIL="InvalidIndexError: Reindexing only valid ...")
    p["EXPORTS"].update(STATUS="PASS", FINISHED_AT="2026-11-14T13:06:08Z", RETRYABLE=False, ATTEMPTS=1,
                        BOARDS_STATUS="MISSING_EVIDENCE", DISTRIBUTIONS_STATUS="MISSING_EVIDENCE",
                        GRADES_STATUS="MISSING_EVIDENCE")
    p["HEALTH"].update(STATUS="PASS", ATTEMPTS=3)
    p["HEALTH"]["AGGREGATED_UPDATED_AT"] = MS._upstream_key(p)
    MS.derive(p)
    return p


def _health(p, latest=True):
    h = {"et_date": DAY.isoformat(), "morning": MS.summary(p),
         "walk_forward": {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED",
                          "ACTIVE_MODEL_VERSION": "cbb-jm1-2026-27-20261114",
                          "prior_day_completeness": {"STATUS": EL.PRIOR_DAY_UNRECONCILED, "FINAL_GAMES": 55,
                                                     "INCOMPLETE_FINAL": 0, "UNRECONCILED_FINAL": 1}},
         "forecast": {"status": "EXCEPTION", "reason": "InvalidIndexError"}}
    if latest:
        h["latest_forecast"] = {"snapshot_id": "F20261114T180028Z-c3d88a", "window": "ROTOWIRE_1300",
                                "model_version": "cbb-jm1-2026-27-20261114", "games_forecast": 16, "pmfs_persisted": 5208,
                                "executed_at": "2026-11-14T18:00:28+00:00"}
        h["exports"] = {"boards": {"status": "WRITTEN"}, "distributions": {"status": "WRITTEN"}, "grades": {"status": "WRITTEN"}}
    return L.receipt(h)


def test_nov14_progress_names_the_historical_failure():
    p = _nov14()
    assert p["MORNING_COMPLETE"] is False and p["MORNING_CHAIN_COMPLETE"] is False
    assert p["FIRST_INCOMPLETE_STAGE"] is None and p["RECOVERY_REQUIRED"] is False     # nothing retryable remains
    assert p["FIRST_HISTORICAL_FAILURE"] == "INITIAL_FORECAST"
    assert p["STAGE_TERMINAL_STATES"] == {"PREP": "PASS", "FEATURE_WARMUP": "NOT_REQUIRED", "WALK_FORWARD": "PASS",
                                          "INITIAL_FORECAST": "FAIL", "EXPORTS": "DEFERRED", "HEALTH": "PASS"}
    s = MS.summary(p)
    assert s["INITIAL_FORECAST"] == "FAIL" and s["FIRST_HISTORICAL_FAILURE"] == "INITIAL_FORECAST"


def test_receipt_reports_recovered_operational_forecast_and_keeps_initial_fail():
    r = _health(_nov14())
    assert r["INITIAL_FORECAST"] == "FAIL" and r["FORECAST_STATUS"] == "EXCEPTION"
    assert (r["FORECAST_OPERATIONAL_STATE"], r["FORECAST_OPERATIONAL_SNAPSHOT_ID"], r["FORECAST_OPERATIONAL_WINDOW"]) == (
        "RECOVERED", "F20261114T180028Z-c3d88a", "ROTOWIRE_1300")
    assert r["MORNING_CHAIN_COMPLETE"] is False and r["DAY_OPERATIONAL"] is True and r["ACTIVE_BLOCKERS"] == []
    assert r["FORECAST_OPERATIONAL_AT"] == "2026-11-14T18:00:28+00:00" and all(r["DAY_OPERATIONAL_CONDITIONS"].values())
    cls = {c["warning"].split(":")[0].split(" ")[0]: c["class"] for c in r["WARNING_CLASSES"]}
    assert cls == {"MORNING_INCOMPLETE": "RECOVERED_HISTORICAL", "MEMORY_HIGH_EXCEEDED": "INFORMATIONAL",
                   EL.PRIOR_DAY_UNRECONCILED: "INFORMATIONAL", "FORECAST_EXCEPTION": "RECOVERED_HISTORICAL"}
    assert len(r["WARNING_CLASSES"]) == len(r["WARNINGS"])                  # nothing suppressed
    assert r["FINAL_STATUS"] == "DEGRADED"                                  # wording never turns a failure into OK
    assert any("first historical failure INITIAL_FORECAST" in w for w in r["WARNINGS"])


def test_without_a_replacement_forecast_the_failure_is_an_active_blocker():
    r = _health(_nov14(), latest=False)
    assert r["FORECAST_OPERATIONAL_STATE"] == "FAIL" and r["DAY_OPERATIONAL"] is False
    assert {c["class"] for c in r["WARNING_CLASSES"] if c["warning"].startswith(("MORNING_INCOMPLETE", "FORECAST_"))} == {
        "ACTIVE_BLOCKER"}
    assert r["FINAL_STATUS"] == "DEGRADED" and len(r["ACTIVE_BLOCKERS"]) == 2


def test_incomplete_morning_always_names_a_stage():
    """Exhaustive over terminal-ish stage statuses: an incomplete morning has a first incomplete stage or a first
    historical failure -- never both None."""
    sts = ("NOT_STARTED", "PASS", "FAIL", "DEFERRED")
    for combo in itertools.product(sts, repeat=4):
        for retry in (False, True):
            p = MS.new_progress(DAY)
            for s, v in zip(MS.STAGES[:-1], combo):
                p[s].update(STATUS=v, RETRYABLE=retry if v in ("FAIL", "DEFERRED") else False, FINISHED_AT="t")
            p["HEALTH"].update(STATUS="PASS")
            p["HEALTH"]["AGGREGATED_UPDATED_AT"] = MS._upstream_key(p)
            MS.derive(p)
            if not p["MORNING_CHAIN_COMPLETE"]:
                assert p["FIRST_INCOMPLETE_STAGE"] or p["FIRST_HISTORICAL_FAILURE"], (combo, retry)
            assert set(p["STAGE_TERMINAL_STATES"].values()) <= set(MS.TERMINAL_STATES) | {"NOT_STARTED", "RUNNING"}


def _el(rows):
    base = {"game_universe": "DI_VS_DI", "provider_status": "final", "has_result": True, "box_complete": True,
            "box_reconciled": True, "decision": "INCLUDED", "reason": "", "scheduled_tip_utc": pd.Timestamp("2026-11-14T00:00Z")}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_prior_day_unreconciled_box_is_not_counted_incomplete():
    """G-450c5c43e2e730bb: complete box, players 86 vs team box 85 -> BOX_UNRECONCILED, not INCOMPLETE_FINAL."""
    el = _el([{"game_id": "G-ok"}, {"game_id": "G-450c5c43e2e730bb", "box_reconciled": False, "decision": "DEFERRED",
                                    "reason": "BOX_UNRECONCILED"}])
    pdc = EL.prior_day_completeness(el, DAY)
    assert (pdc["FINAL_GAMES"], pdc["FINAL_WITH_COMPLETE_BOX"], pdc["FINAL_WITH_RECONCILED_BOX"]) == (2, 2, 1)
    assert (pdc["INCOMPLETE_FINAL"], pdc["UNRECONCILED_FINAL"]) == (0, 1)
    assert pdc["UNRECONCILED_FINAL_GAME_IDS"] == ["G-450c5c43e2e730bb"] and pdc["STATUS"] == EL.PRIOR_DAY_UNRECONCILED


def test_prior_day_incomplete_still_wins_when_both_occur():
    el = _el([{"game_id": "G-u", "box_reconciled": False, "decision": "DEFERRED", "reason": "BOX_UNRECONCILED"},
              {"game_id": "G-i", "box_complete": False, "box_reconciled": False, "decision": "DEFERRED",
               "reason": "BOX_INCOMPLETE"}])
    pdc = EL.prior_day_completeness(el, DAY)
    assert (pdc["INCOMPLETE_FINAL"], pdc["UNRECONCILED_FINAL"], pdc["STATUS"]) == (1, 1, EL.PRIOR_DAY_INCOMPLETE)


def test_prior_day_counters_never_contradict():
    """No game is both 'final with complete box' and INCOMPLETE_FINAL; UNRECONCILED_FINAL is exactly the complete-box,
    non-reconciled finals; every combination of box flags / reasons keeps the counters consistent."""
    reasons = ("", "BOX_UNRECONCILED", "BOX_INCOMPLETE", "FINAL_DATA_INCOMPLETE_AT_CUTOFF")
    rows = []
    for i, (has, comp, rec) in enumerate(itertools.product((True, False), repeat=3)):
        if rec and not comp or comp and not has:
            continue
        reason = "" if rec else "BOX_UNRECONCILED" if comp else "BOX_INCOMPLETE" if has else "FINAL_DATA_INCOMPLETE_AT_CUTOFF"
        assert reason in reasons
        rows.append({"game_id": f"G-{i}", "has_result": has, "box_complete": comp, "box_reconciled": rec,
                     "decision": "INCLUDED" if rec else "DEFERRED", "reason": reason})
    pdc = EL.prior_day_completeness(_el(rows), DAY)
    assert not set(pdc["INCOMPLETE_FINAL_GAME_IDS"]) & set(pdc["UNRECONCILED_FINAL_GAME_IDS"])
    assert pdc["FINAL_WITH_COMPLETE_BOX"] + pdc["INCOMPLETE_FINAL"] == pdc["FINAL_GAMES"]
    assert pdc["FINAL_WITH_RECONCILED_BOX"] + pdc["UNRECONCILED_FINAL"] == pdc["FINAL_WITH_COMPLETE_BOX"]
    assert pdc["INCLUDED"] == pdc["FINAL_WITH_RECONCILED_BOX"]


@pytest.mark.parametrize("seed", range(25))
def test_prior_day_identities_hold_for_any_row_mix(seed):
    """FINAL_GAMES = COMPLETE_FINAL + INCOMPLETE_FINAL and COMPLETE_FINAL = RECONCILED_FINAL + UNRECONCILED_FINAL for
    ANY mix of statuses / flags / reasons -- including rows whose flags contradict their reason -- and UNRECONCILED is
    never counted as INCOMPLETE."""
    import random
    rnd = random.Random(seed)
    rows = [{"game_id": f"G-{i}", "provider_status": rnd.choice(["final", "final", "in_progress", "scheduled", "postponed"]),
             "has_result": rnd.random() < .8, "box_complete": rnd.random() < .7, "box_reconciled": rnd.random() < .6,
             "decision": rnd.choice(["INCLUDED", "DEFERRED"]),
             "reason": rnd.choice(["", "BOX_UNRECONCILED", "BOX_INCOMPLETE", "FINAL_DATA_INCOMPLETE_AT_CUTOFF",
                                   "IDENTITY_UNRESOLVED"])} for i in range(rnd.randint(0, 40))]
    pdc = EL.prior_day_completeness(_el(rows) if rows else _el([]).iloc[0:0], DAY)
    assert pdc["FINAL_GAMES"] == pdc["COMPLETE_FINAL"] + pdc["INCOMPLETE_FINAL"]
    assert pdc["COMPLETE_FINAL"] == pdc["RECONCILED_FINAL"] + pdc["UNRECONCILED_FINAL"]
    assert not set(pdc["INCOMPLETE_FINAL_GAME_IDS"]) & set(pdc["UNRECONCILED_FINAL_GAME_IDS"])
    fin = {r["game_id"] for r in rows if r["provider_status"] == "final"}
    assert set(pdc["INCOMPLETE_FINAL_GAME_IDS"]) | set(pdc["UNRECONCILED_FINAL_GAME_IDS"]) <= fin
    assert pdc["STATUS"] == (EL.PRIOR_DAY_INCOMPLETE if pdc["INCOMPLETE_FINAL"] else
                             EL.PRIOR_DAY_UNRECONCILED if pdc["UNRECONCILED_FINAL"] else "COMPLETE")


def test_health_never_says_stage_none_and_carries_recovery_reason():
    h = {"et_date": "2026-11-15", "morning": {"MORNING_COMPLETE": False, "FIRST_INCOMPLETE_STAGE": None,
                                             "FIRST_HISTORICAL_FAILURE": "INITIAL_FORECAST", "RECOVERY_REQUIRED": False,
                                             "RECOVERY_REQUIRED_REASON": "CHAIN_SETTLED"}}
    out = L.receipt(h)
    assert out["WARNINGS"] and not any("stage None" in w for w in out["WARNINGS"])
    assert out["RECOVERY_REASON"] == "CHAIN_SETTLED" and out["FIRST_HISTORICAL_FAILURE"] == "INITIAL_FORECAST"
    assert {"MORNING_CHAIN_COMPLETE", "DAY_OPERATIONAL", "FORECAST_OPERATIONAL_STATE", "FORECAST_OPERATIONAL_AT",
            "RECOVERY_REQUIRED"} <= set(out)
