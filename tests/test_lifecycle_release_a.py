"""Release A (2026-11-18): the morning FEATURE_WARMUP stage, no stale INITIAL, the concise health contract, and the
official-tick player context in exports.

Incident 2026-11-17 / 11-18: the 01:30 ET warmup built over a ~00:43Z state that predated the late D-1 finals; the
07:00 walk-forward then deferred on the feature backlog (DEFERRED_FEATURE_BACKLOG), INITIAL ran on the D-2 model for
~70 min, and REFIT_RETRY repaired the slate after the 08:00 recovery promoted the fresh model.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import exports as EX, lifecycle as L, morning_stages as MS, ops_store as OS
from cbb_platform.walkforward import config as WC

D = date(2026, 11, 19)
T0610 = pd.Timestamp("2026-11-19T11:10:00Z")            # 06:10 ET (EST): 10 min after the training cutoff
T0130 = pd.Timestamp("2026-11-19T06:30:00Z")            # 01:30 ET: before the cutoff (old warmup time)
T0800 = pd.Timestamp("2026-11-19T13:00:00Z")


class Clock:
    def __init__(self, t):
        self.t = pd.Timestamp(t)

    def __call__(self):
        return self.t


@pytest.fixture
def life(tmp_path, monkeypatch):
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    return L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=tmp_path / "wf", clock=Clock(T0610))


def _warm(status="COMPLETE", state="S-frozen", todo=0, built=0):
    seen = []

    def run(day, root, state_root, budget_s):
        seen.append({"day": day, "budget_s": budget_s})
        return {"day": day.isoformat(), "state_version": state, "included_games": 700, "cached_before": 700 - todo,
                "cutoffs_todo": todo, "cutoffs_built": built, "games_built": built, "status": status, "elapsed_s": 1.0}
    return run, seen


def _prep_pass(life):
    with MS.locked(life.ops_root, D) as p:
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1, FINISHED_AT=T0610.isoformat())


# ============================================================================ schedule
def test_morning_starts_after_the_training_cutoff_and_no_standalone_warmup_timer():
    cut = pd.Timestamp(WC.refit_cutoff(D)).tz_convert(L.TZ)
    assert cut.strftime("%H:%M") < L.MORNING_TIME_ET            # 06:00 ET cutoff < 06:10 ET chain start
    sp = L.schedule_spec()
    assert sp["morning"]["at"] == "06:10" and sp["features_warmup"]["at"] == []
    assert sp["morning"]["stage_units"][:3] == ["cbb-originator-morning-prep", "cbb-originator-morning-warmup",
                                                "cbb-originator-walkforward"]
    assert MS.STAGES == ("PREP", "FEATURE_WARMUP", "WALK_FORWARD", "INITIAL_FORECAST", "EXPORTS", "HEALTH")
    assert pd.Timestamp(L.REFIT_RETRY_TIME_ET) < pd.Timestamp(MS.INITIAL_HOLD_UNTIL_ET)


def test_install_script_prunes_units_a_release_no_longer_ships():
    from cbb_platform import config as C
    s = (C.PROJECT_ROOT / "deploy" / "droplet" / "ops" / "cbb-install-units").read_text()
    assert 'systemctl disable --now "$n"' in s and "STALE BUT ACTIVE (kept)" in s and 'rm -f "$f"' in s
    assert s.index("removed stale") < s.index("systemctl daemon-reload")


# ============================================================================ warmup stage
def test_warmup_stage_uses_the_explicit_day_and_records_a_frozen_state(life):
    _prep_pass(life)
    run, seen = _warm(todo=29, built=29)
    code, out = MS.run_warmup(life, D, warm_runner=run)
    assert code == 0 and out["status"] == MS.PASS and out["state_version"] == "S-frozen"
    assert seen[0]["day"] == D                                    # never warmup_day(now) (06:10 >= MORNING_TIME_ET)
    st = MS.load(life.ops_root, D)["FEATURE_WARMUP"]
    assert st["STATE_FROZEN"] is True and st["STATE_VERSION"] == "S-frozen" and st["GAMES_BUILT"] == 29
    rec = OS.read_jsonl(Path(life.prod_root) / "walkforward_attempts" / f"warmup-{D.isoformat()}.jsonl")[-1]
    assert rec["serves_morning"] == D.isoformat() and rec["STATE_FROZEN"] is True


def test_warmup_before_the_cutoff_is_recorded_as_not_frozen(life):
    life.clock.t = T0130
    run, _ = _warm()
    rec = L.features_warmup(life, runner=run, day=D)
    assert rec["STATE_FROZEN"] is False and rec["status"] == "COMPLETE"


@pytest.mark.parametrize("ws,status,retry", [("BUDGET_EXHAUSTED", MS.DEFERRED, True), ("NO_STATE", MS.FAIL, True)])
def test_warmup_is_advisory_and_never_fails_the_chain(life, ws, status, retry):
    _prep_pass(life)
    run, _ = _warm(status=ws)
    code, out = MS.run_warmup(life, D, warm_runner=run)
    p = MS.load(life.ops_root, D)
    assert code == 0 and out["status"] == status and p["FEATURE_WARMUP"]["RETRYABLE"] is retry
    assert p["FIRST_HISTORICAL_FAILURE"] != "FEATURE_WARMUP" and p["FIRST_INCOMPLETE_STAGE"] != "FEATURE_WARMUP"


def test_warmup_skips_once_the_walk_forward_settled(life):
    _prep_pass(life)
    with MS.locked(life.ops_root, D) as p:
        p["WALK_FORWARD"].update(STATUS=MS.PASS, RETRYABLE=False)
    run, seen = _warm()
    assert MS.run_warmup(life, D, warm_runner=run)[1]["action"] == "SKIP_WALK_FORWARD_SETTLED" and seen == []


def test_walk_forward_records_that_it_fit_the_state_the_warmup_prepared(life):
    _prep_pass(life)
    MS.run_warmup(life, D, warm_runner=_warm(state="S-frozen")[0])
    rec = {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED", "ACTIVE_MODEL_VERSION": "v-new",
           "run_id": "WF", "attempt": "MORNING_0700", "state_freshness": {"state_version": "S-frozen"},
           "feature_catchup": {"cutoffs_todo": 0, "elapsed_s": 0.4}}
    import cbb_platform.prod.lifecycle as LL
    orig = LL.walk_forward
    LL.walk_forward = lambda *a, **k: rec
    try:
        assert MS.run_walkforward(life, D)[0] == 0
    finally:
        LL.walk_forward = orig
    lin = MS.load(life.ops_root, D)["WALK_FORWARD"]["WARMUP_LINEAGE"]
    assert lin == {"WARMUP_STATE_VERSION": "S-frozen", "FIT_STATE_VERSION": "S-frozen", "WARMUP_STATE_MATCHES_FIT": True,
                   "WARMUP_STATE_FROZEN": True, "CATCHUP_CUTOFFS_AFTER_WARMUP": 0, "CATCHUP_ELAPSED_S": 0.4}
    assert MS.load(life.ops_root, D)["WALK_FORWARD"]["PREV_STAGE"] == "FEATURE_WARMUP"


def test_warmup_state_mismatch_is_reported_not_hidden():
    p = {"FEATURE_WARMUP": {"STATE_VERSION": "S-early", "STATE_FROZEN": False}}
    lin = MS.warmup_lineage(p, {"state_freshness": {"state_version": "S-late"}})
    assert lin["WARMUP_STATE_MATCHES_FIT"] is False and lin["WARMUP_STATE_FROZEN"] is False


# ============================================================================ INITIAL hold
def _wf(**kw):
    return {"STATUS": MS.DEFERRED, "RETRYABLE": True, "MODEL_DECISION": "INCUMBENT_RETAINED", **kw}


@pytest.mark.parametrize("wf,recovery,now,held", [
    (_wf(), False, T0610, True),                                       # retryable deferral at 07:xx: hold
    (_wf(STATUS=MS.FAIL), False, T0610, True),                         # retryable resource failure: hold
    (_wf(RETRYABLE=False), False, T0610, False),                       # retries exhausted: incumbent, flagged
    (_wf(), True, T0800, False),                                       # inside the recovery pass: never hold
    (_wf(), False, pd.Timestamp("2026-11-19T15:00Z"), False),          # 10:00 ET: no recovery ahead
    ({"STATUS": MS.PASS, "RETRYABLE": False, "MODEL_DECISION": "PROMOTED"}, False, T0610, False),
])
def test_hold_initial_rules(wf, recovery, now, held):
    p = MS.new_progress(D)
    p["WALK_FORWARD"].update(wf)
    p["RECOVERY"]["ACTIVE"] = recovery
    assert MS.hold_initial(p, L.Life(clock=Clock(now))) is held


def test_a_held_initial_blocks_exports_and_is_resumed_by_recovery(life, monkeypatch):
    fc = []
    monkeypatch.setattr(L, "forecast", lambda lf, w=L.INITIAL, **k: fc.append(w) or {
        "status": "FORECAST", "window": w, "snapshot_id": "F1", "model_version": "v-new", "games_forecast": 3,
        "players_forecast": 30, "pmfs_persisted": 300})
    monkeypatch.setattr(L, "exports_for", lambda lf, d: {})
    _prep_pass(life)
    with MS.locked(life.ops_root, D) as p:
        p["WALK_FORWARD"].update(STATUS=MS.DEFERRED, RETRYABLE=True, MODEL_DECISION="INCUMBENT_RETAINED", ATTEMPTS=1,
                                 ERROR_TYPE=L.DEFERRED_FEATURE_BACKLOG)
    assert MS.run_forecast(life, D)[1]["action"] == "HELD_AWAITING_FRESH_MODEL" and fc == []
    assert MS.run_exports(life, D) == (1, {"stage": "EXPORTS", "action": "BLOCKED_FORECAST_INCOMPLETE",
                                           "forecast": MS.DEFERRED})
    p = MS.load(life.ops_root, D)
    assert MS.summary(p)["INITIAL_HELD_AWAITING_FRESH_MODEL"] is True and p["FIRST_INCOMPLETE_STAGE"] == "WALK_FORWARD"


# ============================================================================ health contract
def _h(**over):
    h = {"et_date": D.isoformat(), "code_sha": "x",
         "walk_forward": {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED",
                          "ACTIVE_MODEL_VERSION": "v-new", "attempt": "MORNING_0700"},
         "latest_forecast": {"snapshot_id": "F1", "window": "INITIAL", "model_version": "v-new", "pmfs_persisted": 9,
                             "games_forecast": 3},
         "forecast": {"status": "FORECAST", "snapshot_id": "F1", "window": "INITIAL", "model_version": "v-new",
                      "pmfs_persisted": 9},
         "exports": {"boards": {"status": "WRITTEN", "rows_with_valid_market": 4}, "distributions": {"status": "WRITTEN"},
                     "grades": {"status": "WRITTEN"}, "performance": {"status": "WRITTEN"}},
         "morning": {"MORNING_PREP_STATUS": "PASS", "MORNING_COMPLETE": True,
                     "FEATURE_WARMUP": {"STATUS": "PASS"}, "INITIAL_ON_SAME_DAY_MODEL": True, "REFIT_RETRY_USED": False,
                     "WARMUP_LINEAGE": {"WARMUP_STATE_MATCHES_FIT": True, "WARMUP_STATE_VERSION": "S",
                                        "FIT_STATE_VERSION": "S"}},
         "training_freshness": {"TRAINING_FRESHNESS": "PASS", "PREVIOUS_DAY_STATUS": "COMPLETE",
                                "PREVIOUS_DAY_CLASSIFICATION_RECORDED": True},
         "retention": {"RETENTION_STATE": "PASS"},
         "availability_1000": {"status": "FORECAST"}}
    for k, v in over.items():
        h[k] = v
    return h


def test_health_contract_normal_path():
    r = L.receipt(_h())
    c = r["HEALTH_CONTRACT"]
    assert set(c) == {"PREP_COMPLETE", "D_MINUS_1_COMPLETE", "TRAINING_FRESHNESS", "PLAYER_MODEL_ACTIVE",
                      "TEAM_RATINGS_FRESH", "PLAYER_FORECAST_COMPLETE", "GAME_FORECAST_COMPLETE", "PLAYER_MARKETS_STATE",
                      "GAME_MARKETS_STATE", "EXPORTS_COMPLETE", "GRADES_COMPLETE", "PERFORMANCE_COMPLETE",
                      "AVAILABILITY_STATE", "RETENTION_STATE", "NORMAL_MORNING_PATH", "DAY_OPERATIONAL", "ACTIVE_BLOCKER"}
    assert c["NORMAL_MORNING_PATH"] is True and c["DAY_OPERATIONAL"] is True and c["ACTIVE_BLOCKER"] is None
    assert c["AVAILABILITY_STATE"] == "PASS" and c["RETENTION_STATE"] == "PASS" and c["TRAINING_FRESHNESS"] == "PASS"
    assert c["GAME_FORECAST_COMPLETE"] == "NOT_DEPLOYED" and r["WARMUP_STATE_MATCHES_FIT"] is True


def test_health_contract_refit_retry_day_is_not_the_normal_path():
    m = _h()["morning"] | {"REFIT_RETRY_USED": True, "INITIAL_ON_SAME_DAY_MODEL": False}
    assert L.receipt(_h(morning=m))["HEALTH_CONTRACT"]["NORMAL_MORNING_PATH"] is False
    wf = _h()["walk_forward"] | {"attempt": "RECOVERY_2"}
    assert L.receipt(_h(walk_forward=wf))["NORMAL_MORNING_PATH"] is False


def test_health_contract_stale_training_blocks_the_day():
    tf = {"TRAINING_FRESHNESS": "FAIL", "ACTIVE_BLOCKER": "STALE_TRAINING_MODEL", "ACTIVE_MODEL_TRAINING_THROUGH": "2026-11-17",
          "EXPECTED_TRAINING_THROUGH": "2026-11-18"}
    c = L.receipt(_h(training_freshness=tf))["HEALTH_CONTRACT"]
    assert c["ACTIVE_BLOCKER"] == "STALE_TRAINING_MODEL" and c["DAY_OPERATIONAL"] is False


def test_availability_watchdog_abort_degrades_the_availability_state():
    av = L.availability_state(_h(availability_1600={"status": "PHASE_B_WATCHDOG_ABORT"}))
    assert av["AVAILABILITY_STATE"] == "DEGRADED" and av["FAILED_WINDOWS"] == {"1600": "PHASE_B_WATCHDOG_ABORT"}
    assert L.availability_state({})["AVAILABILITY_STATE"] == "NONE_YET"
    assert L.availability_state({"availability_poll_0830": {"forecast": None}})["AVAILABILITY_STATE"] == "PASS"


def test_retention_state_reads_the_latest_summary(life):
    p = Path(life.ops_root) / "retention" / "storage_retention.jsonl"
    assert L.retention_state(life)["RETENTION_STATE"] == "NOT_RECORDED"
    OS.append_jsonl(p, [{"at": (T0610 - pd.Timedelta(hours=40)).isoformat(), "summary": True, "changed": 1}])
    assert L.retention_state(life)["RETENTION_STATE"] == "STALE"
    OS.append_jsonl(p, [{"at": (T0610 - pd.Timedelta(hours=3)).isoformat(), "rule": "X"},
                        {"at": (T0610 - pd.Timedelta(hours=2)).isoformat(), "summary": True, "changed": 4,
                         "reclaimed_bytes": 10}])
    r = L.retention_state(life)
    assert r["RETENTION_STATE"] == "PASS" and r["CHANGED"] == 4 and r["AGE_H"] == 2.0


# ============================================================================ official-tick player context (Part XXIV)
def _recs(kind, fid):
    return [{"source_kind": kind, "forecast_id": fid, "player_id": "P1"}]


def test_official_tick_context_falls_back_to_its_own_projections(tmp_path):
    t = tmp_path / "ticks" / "K1"
    t.mkdir(parents=True)
    pd.DataFrame([{"game_id": "G", "player_id": "P1", "p_play": 0.97, "min_mu": 31.5, "proj_pts": 12.0}]).to_parquet(
        t / "projections.parquet", index=False)
    ctx = EX.player_context(tmp_path, "2026-11-17", _recs("OFFICIAL_TICK", "K1"))
    assert ctx[("K1", "P1")]["min_mu"] == 31.5 and ctx[("K1", "P1")]["p_play"] == 0.97
    assert EX.player_role(ctx[("K1", "P1")]["min_mu"]) == "CORE_30_PLUS"


def test_official_tick_prefers_forecast_players_and_snapshots_never_fall_back(tmp_path):
    t = tmp_path / "ticks" / "K1"
    t.mkdir(parents=True)
    pd.DataFrame([{"player_id": "P1", "min_mu": 9.0}]).to_parquet(t / "projections.parquet", index=False)
    pd.DataFrame([{"player_id": "P1", "min_mu": 22.0, "p_play": 0.9, "availability_status": "ACTIVE"}]).to_parquet(
        t / "forecast_players.parquet", index=False)
    assert EX.player_context(tmp_path, "2026-11-17", _recs("OFFICIAL_TICK", "K1"))[("K1", "P1")]["min_mu"] == 22.0
    s = tmp_path / "forecasts" / "2026-11-17" / "F1"
    s.mkdir(parents=True)
    pd.DataFrame([{"player_id": "P1", "min_mu": 9.0}]).to_parquet(s / "projections.parquet", index=False)
    assert EX.player_context(tmp_path, "2026-11-17", _recs("FORECAST_SNAPSHOT", "F1")) == {}   # blank, never estimated


def test_tick_scoring_evidence_writes_forecast_players():
    import inspect
    from cbb_platform.prod import shadow_tick as SH
    src = inspect.getsource(SH.scoring_evidence)
    assert 'fp.to_parquet(tdir / "forecast_players.parquet", index=False)' in src
    assert src.index('"projections.parquet"') < src.index('"forecast_players.parquet"')
