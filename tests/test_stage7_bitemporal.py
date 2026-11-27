"""Stage 07: bitemporal information integrity and explicit model-source substitution.

Each test fixes a demonstrated failure mode. Where practical it shows that the old behaviour would have admitted
information or substituted a model silently, and that the corrected path refuses it or records it."""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import context_adj as CX, features as PF, model_source as MS, rotowire_feed as RW


def _state_dir(tmp_path: Path, source_cutoff: str) -> Path:
    d = tmp_path / "versions" / "S20261120T120000Z-abc"
    d.mkdir(parents=True)
    (d / "state_manifest.json").write_text(json.dumps({"state_version": d.name, "source_cutoff": source_cutoff,
                                                      "created_at": source_cutoff}))
    return d / "increment.duckdb"


# ------------------------------------------------------------------ replay over a later information state
def test_compact_canonical_refuses_a_later_information_state(tmp_path):
    inc = _state_dir(tmp_path, "2026-11-20T12:00:00+00:00")
    info = PF.information_state(inc, "2026-11-19T12:00:00+00:00")
    assert info["check"] == "LATER_INFORMATION_STATE"
    # Old path: no guard, so the rule mask alone ran over a state acquired a day after as_of. New path: refused before
    # anything is read.
    with pytest.raises(PF.FeatureBuildError, match="LATER_INFORMATION_STATE"):
        PF.compact_canonical(tmp_path / "out.duckdb", ["G-1"], "2026-11-19T12:00:00+00:00", 2027,
                             history_db=tmp_path / "missing.duckdb", increment_db=inc)


def test_information_state_ok_when_acquired_by_as_of(tmp_path):
    inc = _state_dir(tmp_path, "2026-11-20T12:00:00+00:00")
    assert PF.information_state(inc, "2026-11-20T12:00:00+00:00")["check"] == "OK"
    assert PF.information_state(inc, "2026-11-21T00:00:00+00:00")["check"] == "OK"
    assert PF.information_state(None, "2026-11-21")["check"] == "NO_INCREMENT"
    assert PF.information_state(tmp_path / "nomanifest" / "increment.duckdb", "2026-11-21")["check"] == \
        "INFORMATION_STATE_UNVERIFIED"


# ------------------------------------------------------------------ participation evidence uses the knowability rule
def test_participation_keys_use_the_rule_time_not_the_event_time(tmp_path):
    db = tmp_path / "inc.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA flagship")
    con.execute("""CREATE TABLE flagship.player_game_outcomes AS SELECT 'P-1' canonical_player_id, 'T-1' canonical_team_id,
                   'Some Player' source_player_name, 2027 season,
                   TIMESTAMPTZ '2026-11-20 00:00:00+00' event_time,
                   TIMESTAMPTZ '2026-11-20 04:00:00+00' historical_knowable_at""")
    con.close()
    between = "2026-11-20T02:00:00+00:00"                       # after tip, before tip + 4 h
    old = pd.Timestamp("2026-11-20 00:00:00+00") <= pd.Timestamp(between)
    assert old                                                  # the old event_time test admitted the line here
    assert ("C", "P-1") not in RW.participation_keys(db, between)
    assert ("C", "P-1") in RW.participation_keys(db, "2026-11-20T04:00:00+00:00")


# ------------------------------------------------------------------ F1: explicit DEGRADED frozen substitution
def test_scoring_model_fallback_is_explicit_degraded_and_logged(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    m = MS.scoring_model("2026-11-27T15:00:00+00:00", store)
    st = m.identity["model_source_status"]
    assert st["status"] == "DEGRADED" and st["fallback_code"] == "NO_ACTIVE_POINTER"
    assert m.identity["model_kind"] == MS.FROZEN_KIND                 # never labelled a daily refit
    assert st["actual"]["training_cutoff"] == MS.FROZEN_TRAINING_CUTOFF
    assert st["expected"]["expected_version"] == "cbb-jm1-2026-27-20261127"
    assert st["artifact_age_days"] > 200
    assert st["actionability_policy"] == "PRICED_BLOCKED_NON_ACTIONABLE" and st["recovery"]
    log = (store / "manifests" / MS.DEGRADED_LOG).read_text().strip().splitlines()
    assert len(log) == 1 and json.loads(log[0])["fallback_code"] == "NO_ACTIVE_POINTER"
    assert MS.is_degraded(m.identity)


def test_explicit_frozen_runs_are_labelled_not_degraded():
    m = MS.scoring_model("2026-11-27T15:00:00+00:00", None)
    assert m.identity["model_source_status"]["status"] == "EXPLICIT_FROZEN"
    assert not MS.is_degraded(m.identity)


def test_model_source_fallback_is_a_known_blocking_run_reason():
    from cbb_platform.prod import loader as LD, readiness as RD
    assert MS.FALLBACK_RUN_REASON in RD.RUN_REASONS
    assert "model_source_status" in LD.WALK_FORWARD_IDENTITY_FIELDS


def test_resolve_refuses_a_pointer_promoted_after_as_of(monkeypatch, tmp_path):
    from cbb_platform.walkforward import config as WC, store as SO

    class _M:
        identity = {"model_kind": WC.DAILY_KIND, "training_cutoff": "2026-11-27T11:00:00+00:00",
                    "model_version": "cbb-jm1-2026-27-20261127"}
    (tmp_path / "models" / "2026-11-27").mkdir(parents=True)
    monkeypatch.setattr(SO, "active", lambda root: {"ACTIVE_MODEL_VERSION": "cbb-jm1-2026-27-20261127",
                                                    "promoted_at": "2026-11-27T13:00:00+00:00"})
    monkeypatch.setattr(SO, "version_dir", lambda root, day: tmp_path / "models" / "2026-11-27")
    monkeypatch.setattr(SO, "paths", lambda root: {"models": tmp_path / "models"})
    monkeypatch.setattr(SO, "load_active", lambda root: _M())
    with pytest.raises(MS.ModelSourceUnavailable) as e:          # replay at 12:00Z: the 13:00Z promotion was not in force
        MS.resolve("ACTIVE_DAILY", "2026-11-27T12:00:00+00:00", tmp_path, replay=True)
    assert e.value.code == "FUTURE_PROMOTION"
    ok = MS.resolve("ACTIVE_DAILY", "2026-11-27T14:00:00+00:00", tmp_path, replay=True)
    assert ok.identity["model_source_status"]["status"] == "OK"


def test_health_reports_a_degraded_model_source_day(tmp_path):
    from datetime import date
    from cbb_platform.prod import lifecycle as LC
    store = tmp_path / "store"
    MS.scoring_model("2026-11-27T15:00:00+00:00", store)
    life = LC.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=store)
    s = LC.model_source_state(life, date(2026, 11, 27))
    assert s["PLAYER_MODEL_SOURCE"] == "DEGRADED" and s["DEGRADED_EVENTS"] == 1
    assert LC.model_source_state(life, date(2026, 11, 26))["PLAYER_MODEL_SOURCE"] == "OK"


# ------------------------------------------------------------------ CTX1 ledger: lineage, as-of replay, incumbent
def _ledger(root: Path, rows: pd.DataFrame, updated_at: str, incumbent: str = "TAG"):
    p = CX.ledger_path(root, 2027)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(p, index=False)
    p.with_suffix(".json").write_text(json.dumps({"incumbent": incumbent, "updated_at": updated_at}))


def _rows(added=None):
    d = pd.DataFrame({"game_id": ["G-1", "G-2"], "date": ["2026-11-20", "2026-11-25"], "team": "T-1", "opp": "T-2",
                      "stat": "pts", "y": [70.0, 72.0], "cf": [68.0, 70.0]})
    if added is not None:
        d["added_at"] = added
    return d


def test_ledger_replay_keeps_only_rows_added_by_as_of(tmp_path):
    _ledger(tmp_path, _rows(["2026-11-21T11:30:00+00:00", "2026-11-26T11:30:00+00:00"]), "2026-11-26T11:30:00+00:00")
    led, st = CX.load_ledger(tmp_path, 2027, "2026-11-27T00:00:00+00:00", replay=True)
    assert st == "OK" and len(led) == 2
    led, st = CX.load_ledger(tmp_path, 2027, "2026-11-22T00:00:00+00:00", replay=True)
    # the old path read the whole current ledger whatever as_of was; the replay now sees only the 11-21 row
    assert list(led["game_id"]) == ["G-1"]


def test_ledger_replay_refuses_legacy_rows_and_future_ledgers(tmp_path):
    _ledger(tmp_path, _rows(), "2026-11-26T11:30:00+00:00")
    assert CX.load_ledger(tmp_path, 2027, "2026-11-27T00:00:00+00:00", replay=True) == (None, "LEDGER_REPLAY_UNVERIFIABLE")
    assert CX.load_ledger(tmp_path, 2027, "2026-11-25T00:00:00+00:00")[1] == "LEDGER_FROM_FUTURE"


def test_ledger_incumbent_mismatch_is_visible(tmp_path):
    _ledger(tmp_path, _rows(), "2026-11-26T11:30:00+00:00", incumbent="OLD")
    led, st = CX.load_ledger(tmp_path, 2027, "2026-11-26T20:00:00+00:00", expected_incumbent="NEW")
    assert st == "INCUMBENT_MISMATCH" and led is not None
    assert CX.load_ledger(tmp_path, 2027, "2026-11-26T20:00:00+00:00", expected_incumbent="OLD")[1] == "OK"


# ------------------------------------------------------------------ F9 runtime flags are lineage
def test_runtime_flags_are_in_the_run_identity(monkeypatch):
    from cbb_platform.prod import engine as ENG
    monkeypatch.setenv("CBB_GAME_MEAN_BASIS", "FINAL")
    ident = ENG.identity()
    assert ident["runtime_flags"]["CBB_GAME_MEAN_BASIS"] == "FINAL"
    assert ident["runtime_flags"]["effective_game_joint_version"] == "cbb-game-joint-1.0.0"
    assert "runtime_flags" not in ENG.engine_id()


# ------------------------------------------------------------------ replay contract: curated statuses as deployed
def test_release_in_force_is_the_latest_deploy_by_the_instant():
    from cbb_platform.prod import replay_contract as RC
    dep = [("a", "2026-11-09T23:40:00+00:00"), ("b", "2026-11-26T23:03:40+00:00")]
    assert RC.release_in_force(dep, "2026-11-26T12:00:00+00:00") == "a"   # an 11-26 status was not deployed yet
    assert RC.release_in_force(dep, "2026-11-27T00:00:00+00:00") == "b"
    assert RC.release_in_force(dep, "2026-11-01T00:00:00+00:00") is None


def test_curated_csv_at_reads_the_deployed_content():
    from cbb_platform.prod import replay_contract as RC
    seen = {}

    def show(commit, rel):
        seen["args"] = (commit, rel)
        return "rotowire_player_id,verified_on\n123,2026-11-09\n" if commit == "R1" else None
    df = RC.curated_csv_at("R1", "rotowire_player_states", show)
    assert seen["args"] == ("R1", "cbb_platform/prod/rotowire_player_states.csv")
    assert list(df["verified_on"]) == ["2026-11-09"]
    assert RC.curated_csv_at("R0", "rotowire_player_states", show).empty     # not yet deployed: no rows
