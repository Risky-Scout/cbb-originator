"""Directive 20 + 21: the stage split changes WHERE the forecast / walk-forward run (a fresh process per systemd unit),
never WHAT they compute. Each test runs the production code path in-process (the pre-split monolith's call) and through
the morning stage in a separate subprocess (tests/morning_stage_harness.py --mode real) on identical inputs, then
requires identical outputs (byte-identical artifacts / bit-identical PMF probabilities)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform.prod import (config as PC, feed_fixtures as FF, fixtures as FX, lifecycle as L, morning_stages as MS,
                               odds_fixtures as OX, pmf_evidence as PE, shadow_tick as TK)
from tests.test_prod_shadow_tick import Clock, _ctx

HARNESS = Path(__file__).with_name("morning_stage_harness.py")
SIMS = 300


def _harness(ops, prod, model_root, now, *args, history_db=None, sims=None, max_state_age=None):
    cmd = [sys.executable, str(HARNESS), "--ops-root", str(ops), "--prod-root", str(prod), "--model-root", str(model_root),
           "--now", pd.Timestamp(now).isoformat()]
    if history_db:
        cmd += ["--history-db", str(history_db)]
    if sims:
        cmd += ["--sims", str(sims)]
    if max_state_age is not None:
        cmd += ["--max-state-age", str(max_state_age)]
    env = {**os.environ, "PYTHONPATH": str(C.PROJECT_ROOT)}
    for k in (L.HEAVY_LOCK_ENV, "INVOCATION_ID"):
        env.pop(k, None)
    r = subprocess.run(cmd + list(args), capture_output=True, text=True, env=env, timeout=3600)
    assert r.stdout.strip(), r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


# ============================================================================ 20. forecast equivalence
def test_20_forecast_stage_in_a_fresh_process_is_identical_to_the_in_process_forecast(tmp_path):
    w = tmp_path
    hist = FX.synthetic_history(w / "history.duckdb")
    clock = Clock("2026-11-01T12:00Z")
    cbbd = FF.FakeCBBD(FF.payloads("before"))
    ctx = _ctx(w, hist, clock, cbbd, OX.FakeOdds([]))
    TK.tick(ctx)
    cbbd.bodies = FF.payloads("after")
    clock.t = pd.Timestamp("2026-11-05T06:00Z")
    TK.tick(ctx)
    now = pd.Timestamp("2026-11-10T12:00Z")                                  # 07:00 ET
    clock.t = now
    life = L.Life(ops_root=w / "ops", prod_root=w / "prod", history_db=hist, model_root=w / "no_wf_store", clock=clock,
                  sims=SIMS)
    a = L.forecast(life, L.INITIAL)                                         # the pre-split in-process call
    day = L.et_date(now)
    with MS.locked(life.ops_root, day) as p:                                # a usable model decision (frozen fallback)
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1)
        p["WALK_FORWARD"].update(STATUS=MS.PASS, MODEL_DECISION="INCUMBENT_RETAINED", DAILY_REFIT_STATUS="NO_NEW_DATA")
    out = _harness(w / "ops", w / "prod", w / "no_wf_store", now, "stage", "forecast", "--mode", "real",
                   history_db=hist, sims=SIMS)
    assert out["code"] == 0 and out["out"]["status"] == "FORECAST"
    pr = MS.load(life.ops_root, day)["INITIAL_FORECAST"]
    assert pr["STATUS"] == MS.PASS and pr["PID"] == out["pid"] != os.getpid()
    b_id = pr["SNAPSHOT_ID"]
    sa, sb = life.forecasts(day.isoformat(), a["snapshot_id"]), life.forecasts(day.isoformat(), b_id)
    ja, jb = (json.loads((s / "snapshot.json").read_text()) for s in (sa, sb))
    for k in ("games", "cutoffs", "model_version", "artifact_sha256", "state_version", "games_forecast",
              "players_forecast", "stat_predictions", "pmfs_persisted", "prediction_as_of"):
        assert ja[k] == jb[k], k
    key = ["game_id", "player_id", "stat", "outcome"]
    pa = PE.read(sa / PE.FILE).sort_values(key).reset_index(drop=True)
    pb = PE.read(sb / PE.FILE).sort_values(key).reset_index(drop=True)
    assert len(pa) > 0 and len(pa) == len(pb)
    for c in ("game_id", "player_id", "team_id", "stat", "outcome", "model_version", "method", "prediction_as_of"):
        assert pa[c].tolist() == pb[c].tolist(), c
    assert pa["probability"].to_numpy().tobytes() == pb["probability"].to_numpy().tobytes()   # bit-identical
    assert pa["mean"].to_numpy().tobytes() == pb["mean"].to_numpy().tobytes()
    fa, fb = (pd.read_parquet(s / "forecast_players.parquet").sort_values(["game_id", "player_id"]).reset_index(drop=True)
              for s in (sa, sb))
    for c in ("game_id", "player_id", "min_mu", "min_sigma", "p_play", "availability_status"):
        assert fa[c].astype(str).tolist() == fb[c].astype(str).tolist(), c


# ============================================================================ 21. walk-forward equivalence (real fit)
HIST = PC.HISTORY_CANONICAL


@pytest.mark.skipif(not HIST.exists(), reason="frozen local stores not present")
def test_21_walk_forward_stage_in_a_fresh_process_produces_byte_identical_artifacts(tmp_path, monkeypatch):
    """The real walk-forward (feature catch-up, fit 1, fit 2, deterministic repeat gate, hard gates, promotion) on the
    labeled fixture season: lifecycle.walk_forward in-process vs the WALK_FORWARD stage in a fresh subprocess, two
    fresh stores, identical state -> identical marginal / structural / residual-dependence artifacts and manifest
    artifact hashes."""
    from cbb_platform.walkforward import config as WC, store as SO
    from tests.test_walkforward_daily import D4, _states
    state = tmp_path / "state"
    _states(state)
    now = WC.refit_cutoff(D4) + pd.Timedelta(minutes=30)
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    monkeypatch.setattr(PC, "MAX_STATE_AGE_AT_REFIT_CUTOFF_MINUTES", 10 ** 6)  # the fixture's state has no sync log
    roots = {}
    for k in ("inproc", "stage"):
        prod = tmp_path / k / "prod"
        prod.mkdir(parents=True)
        shutil.copytree(state, prod / "state", symlinks=True)
        roots[k] = {"prod": prod, "ops": prod / "ops", "wf": tmp_path / k / "wf" / "cbb-originator"}
    r = roots["inproc"]
    life = L.Life(ops_root=r["ops"], prod_root=r["prod"], model_root=r["wf"], clock=lambda: now)
    a = L.walk_forward(life, D4, "MORNING_0700")                            # the pre-split monolith's call
    assert a["DAILY_MODEL_PROMOTION"] == "PROMOTED", a
    s = roots["stage"]
    with MS.locked(s["ops"], D4) as p:
        p["PREP"].update(STATUS=MS.PASS, ATTEMPTS=1)
    out = _harness(s["ops"], s["prod"], s["wf"], now, "stage", "walkforward", "--mode", "real", max_state_age=10 ** 6)
    assert out["code"] == 0 and out["out"]["status"] == MS.PASS and out["out"]["DAILY_MODEL_PROMOTION"] == "PROMOTED", out
    for f in SO.FILES.values():
        assert (SO.version_dir(r["wf"], D4) / f).read_bytes() == (SO.version_dir(s["wf"], D4) / f).read_bytes(), f
    ma, mb = (json.loads((SO.version_dir(x["wf"], D4) / SO.MANIFEST).read_text()) for x in (r, s))
    assert ma["artifacts"] == mb["artifacts"] and ma["TRAINING_CUTOFF"] == mb["TRAINING_CUTOFF"]
    assert ma["deterministic_refit"]["BIT_IDENTICAL"] and mb["deterministic_refit"]["BIT_IDENTICAL"]
    assert ma["TRAINING_DATA_HASH"] == mb["TRAINING_DATA_HASH"] and ma["INPUT_IDENTITY_SHA"] == mb["INPUT_IDENTITY_SHA"]
    pr = MS.load(s["ops"], D4)["WALK_FORWARD"]
    assert pr["RUN_ID"] and pr["ACTIVE_MODEL_VERSION"] == WC.version_id(D4) and pr["PEAK_RSS_MB"] > 0
    # a second stage run the same day: model evidence -> no refit
    out2 = _harness(s["ops"], s["prod"], s["wf"], now + pd.Timedelta(hours=1), "stage", "walkforward", "--mode", "real")
    assert out2["out"]["action"] == "SKIP_ALREADY_PASS"
    with MS.locked(s["ops"], D4) as p:                                       # receipt lost: the store still proves it
        p["WALK_FORWARD"].update(STATUS=MS.FAIL, RETRYABLE=True, MODEL_DECISION=None)
    out3 = _harness(s["ops"], s["prod"], s["wf"], now + pd.Timedelta(hours=2), "stage", "walkforward", "--mode", "real")
    assert out3["out"]["action"] == "RECONSTRUCTED_PASS_FROM_MODEL_EVIDENCE_NO_REFIT"
    assert len(SO.read_log(s["wf"] / "manifests" / "runs.jsonl")) == 1                      # exactly one fit
