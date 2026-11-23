"""prod.engine: every forecast / evaluation / grade / performance row carries the predictive engine that produced it."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from cbb_platform.prod import engine as ENG, pmf_evidence as PE, rate_a2 as RA2

A2_SWITCH = "2026-11-22T21:45:31.499302+00:00"           # Droplet `current` -> 01d4ab1 (symlink mtime)


def test_engine_id_is_deterministic_in_the_mathematics():
    eid = ENG.engine_id()
    assert eid == "JM1_FINAL+RA2V1[AST,FG3M,STL]#437c07dc928d" == ENG.engine_id()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(RA2, "PARAMS", {})
        assert ENG.engine_id() == "JM1_FINAL"
    with pytest.MonkeyPatch.context() as mp:                # one changed constant -> a different engine
        mp.setattr(RA2, "PARAMS", {**RA2.PARAMS, "STL": RA2.PARAMS["STL"][:4] + (1.0,)})
        assert ENG.engine_id() != eid


def test_history_is_frozen_and_the_legacy_boundary_is_exact():
    assert ENG.ENGINE_HISTORY == (("2026-10-01T00:00:00+00:00", "JM1_FINAL", None),
                                  (A2_SWITCH, "JM1_FINAL+RA2V1[AST,FG3M,STL]#437c07dc928d",
                                   "01d4ab1bdb34a055c52f3b5a692a2151f4c55eec"))
    t = pd.Timestamp(A2_SWITCH)
    assert ENG.legacy_engine((t - pd.Timedelta(microseconds=1)).isoformat()) == "JM1_FINAL"
    assert ENG.legacy_engine(A2_SWITCH) == ENG.ENGINE_HISTORY[1][1]
    assert ENG.legacy_engine("2026-11-22 21:11:04.105475+00:00") == "JM1_FINAL"        # last pre-A2 production run
    assert ENG.legacy_engine("2026-11-22T22:10:10.060092+00:00") == ENG.ENGINE_HISTORY[1][1]   # first A2 run
    assert ENG.legacy_engine(None) is None and ENG.attribute("X", A2_SWITCH) == "X"


def test_production_run_stamps_the_engine_on_every_record(tmp_path):
    from tests import test_prod_golden_equivalence as G
    G.capture(tmp_path)
    eid = ENG.engine_id()
    recs = [json.loads(l) for l in open(tmp_path / "ledger.jsonl")]
    run = next(r["body"] for r in recs if r["record_type"] == "RUN")
    preds = [r["body"] for r in recs if r["record_type"] == "PREDICTION"]
    assert run["model_identity"]["engine_id"] == eid and "code_commit" in run["model_identity"]
    assert preds and all(p["engine_id"] == eid and "code_commit" in p for p in preds)
    run_dir = next((tmp_path / "runs").iterdir())
    s = json.loads((run_dir / "run_summary.json").read_text())
    assert s["model_identity"]["engine_id"] == eid and s["player_rate_adjustment"]["id"] == RA2.ID
    pe = PE.from_run(run_dir, s["run_id"], s["prediction_as_of"], s["model_identity"]["model_version"])
    assert len(pe) and set(pe["engine_id"]) == {eid}                    # read from the run's own summary
    side = [json.loads(l)["body"] for l in open(run_dir.parent.parent / "side_monitor.jsonl")] \
        if (run_dir.parent.parent / "side_monitor.jsonl").exists() else []
    assert all(b.get("engine_id") == eid for b in side if "projection" in b)
