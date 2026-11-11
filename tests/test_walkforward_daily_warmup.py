"""Overnight pregame-feature warmup: scheduling only (no fit / promotion / manifest write / morning lock)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import lifecycle as L
from cbb_platform.walkforward import daily as WD, store as SO


@pytest.fixture
def fake(monkeypatch, tmp_path):
    root = tmp_path / "wf"
    gc = pd.DataFrame({"canonical_game_id": ["G1", "G2", "G3", "G4"],
                       "prediction_cutoff_utc": pd.to_datetime(["2026-11-02T23:00Z", "2026-11-02T23:00Z",
                                                                "2026-11-03T00:00Z", "2026-11-04T01:00Z"])})
    cache = SO.paths(root)["training"] / "features"
    (cache / "G1").mkdir(parents=True)                               # G1 already cached by an earlier run
    calls = []
    monkeypatch.setattr(WD.P, "load_config", lambda: {"cfg": 1})
    monkeypatch.setattr(WD.EL, "outcome_available_at", lambda sr, cut, known: {"G1": {}})
    monkeypatch.setattr(WD.EL, "state_asof", lambda sr, cut: {"state_version": "S1", "increment_db": "inc"})
    monkeypatch.setattr(WD.EL, "evaluate", lambda st, cut, av: pd.DataFrame(
        {"game_id": list(gc["canonical_game_id"]), "decision": ["INCLUDED"] * 4, "reason": [""] * 4}))
    monkeypatch.setattr(WD.TR, "game_cutoffs", lambda inc_db, ids: gc[gc["canonical_game_id"].isin(ids)])

    def fr(cache_root, state_root, games, history_db, cfg, log=print):
        calls.append(sorted(games["canonical_game_id"]))
        return {"meta": {g: {"status": "BUILT"} for g in games["canonical_game_id"]}}
    monkeypatch.setattr(WD.TR, "feature_rows", fr)
    return {"root": root, "calls": calls, "tmp": tmp_path}


def test_warmup_builds_only_uncached_games_oldest_cutoff_first_with_the_same_builder(fake):
    out = WD.warm_features(date(2026, 11, 10), root=fake["root"], state_root=fake["tmp"], history_db=Path("h"))
    assert fake["calls"] == [["G2"], ["G3"], ["G4"]]                  # one call per cutoff, cached G1 skipped
    assert out["status"] == "COMPLETE" and out["cached_before"] == 1 and out["cutoffs_built"] == 3
    assert not (SO.paths(fake["root"])["manifests"] / "outcome_available_at.json").exists()   # no manifest write
    assert not (SO.paths(fake["root"])["locks"] / "morning.lock").exists()                    # never the morning lock
    assert not SO.paths(fake["root"])["models"].exists() and not SO.paths(fake["root"])["active"].exists()


def test_warmup_stops_starting_cutoffs_after_its_budget(fake):
    t = iter([0.0, 0.0, 10.0, 99999.0, 99999.0, 99999.0])
    out = WD.warm_features(date(2026, 11, 10), root=fake["root"], state_root=fake["tmp"], history_db=Path("h"),
                           budget_s=60.0, clock=lambda: next(t))
    assert out["status"] == "BUDGET_EXHAUSTED" and fake["calls"] == [["G2"], ["G3"]]


def test_lifecycle_warmup_takes_the_heavy_lock_and_defers_when_busy(monkeypatch, tmp_path):
    import fcntl
    lock = tmp_path / "heavy.lock"
    monkeypatch.setenv(L.HEAVY_LOCK_ENV, str(lock))
    monkeypatch.setenv(L.WARMUP_WAIT_ENV, "0")
    now = pd.Timestamp("2026-11-10T06:30:00Z")                         # 01:30 ET -> serves the 11-10 morning
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", clock=lambda: now.to_pydatetime())
    seen = {}

    def runner(day, root, state_root, budget_s):
        seen.update(day=day, budget_s=budget_s)
        return {"status": "COMPLETE"}
    out = L.features_warmup(life, 150, runner=runner)
    assert out["status"] == "COMPLETE" and out["lock_result"] == "ACQUIRED" and seen["day"] == date(2026, 11, 10)
    assert 0 < seen["budget_s"] <= 9000
    with open(lock, "a") as f:                                         # NBA (or the morning) holds the lock
        fcntl.flock(f, fcntl.LOCK_EX)
        out = L.features_warmup(life, 150, runner=runner)
    assert out["status"] == "DEFERRED_SHARED_LOCK"
    log = (tmp_path / "prod" / "walkforward_attempts" / "warmup-2026-11-10.jsonl").read_text().splitlines()
    assert [json.loads(x)["status"] for x in log] == ["COMPLETE", "DEFERRED_SHARED_LOCK"]
    assert L.warmup_day(pd.Timestamp("2026-11-10T13:00:00Z")) == date(2026, 11, 11)   # 08:00 ET -> next morning
    assert L.schedule_spec()["features_warmup"]["at"] == ["01:30"]


def test_daily_receipt_summarizes_recorded_sections_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CBB_SCHEDULER_ROLE", "PRIMARY_WRITER")
    now = pd.Timestamp("2026-11-10T15:00:00Z")
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", clock=lambda: now.to_pydatetime())
    day = date(2026, 11, 10)
    L.health(life, day, walk_forward={"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED",
                                      "ACTIVE_MODEL_VERSION": "v1", "lock_result": "ACQUIRED"},
             forecast={"status": "FORECAST", "model_version": "v1", "games_forecast": 3, "players_forecast": 60,
                       "pmfs_persisted": 720, "games_not_scored": {"G9": ["AVAILABILITY_IDENTITY_UNRESOLVED"]}},
             exports={"distributions": {"status": "WRITTEN"}, "boards": {"status": "MISSING_EVIDENCE"},
                      "grades": {"status": "MISSING_EVIDENCE"}, "raw_quote_rows": 0})
    h = L.health(life, day, availability_1000={"rotowire_web_acquisition": {
        "auth": "PASS", "news_status": "PROCESSED", "snapshot_status": "PROCESSED", "news_continuity": "CONTINUOUS",
        "at": now.isoformat()}})
    r = h["receipt"]
    assert (r["MORNING_REFIT_STATUS"], r["MODEL_PROMOTION"], r["MODEL_VERSION"]) == ("PASS", "PROMOTED", "v1")
    assert (r["FORECAST_GAME_COUNT"], r["FORECAST_PLAYER_COUNT"], r["PMF_COUNT"]) == (3, 60, 720)
    assert (r["ROTOWIRE_AUTH"], r["ROTOWIRE_NEWS_OVERLAP"], r["ROTOWIRE_LAST_SUCCESS"]) == ("PASS", "CONTINUOUS", now.isoformat())
    assert r["BOARD_STATUS"] == "NONE_NO_REAL_QUOTES" and r["BOARD_EXPORT_STATUS"] == "MISSING_EVIDENCE"
    assert r["REAL_QUOTES"] is False and r["IDENTITY_BLOCKED_GAMES"] == 1 and r["MODEL_FALLBACK_USED"] is False
    assert r["WRITER_COUNT"] == 1 and r["FINAL_STATUS"] == "OK" and r["HEAVY_LOCK_STATUS"] == "ACQUIRED"
    h = L.health(life, day, walk_forward={"DAILY_REFIT_STATUS": "FAIL", "reason": "x"})
    assert h["receipt"]["FINAL_STATUS"] == "FAIL" and h["receipt"]["ERRORS"]
    h = L.health(life, day, quote_refresh={"status": "SKIPPED", "reason": "QUOTE_INTEGRATION_NOT_VALIDATED (x)"},
                 walk_forward={"DAILY_REFIT_STATUS": L.DEFERRED_FEATURE_BACKLOG, "DAILY_MODEL_PROMOTION": "NOT_ATTEMPTED",
                               "note": "backlog"})
    r = h["receipt"]
    assert r["BOARD_STATUS"] == "NONE_QUOTE_INTEGRATION_NOT_VALIDATED"          # explicit, never a silent absence
    assert r["MODEL_FALLBACK_USED"] is True and r["FALLBACK_REASON"] == "backlog"
    assert "WALK_FORWARD_DEFERRED_FEATURE_BACKLOG" in r["WARNINGS"] and r["FINAL_STATUS"] == "DEGRADED"
    assert L.board_status("WRITTEN", {}, True) == "WRITTEN"
    assert L.board_status("MISSING_EVIDENCE", {"status": "REPRICED"}, True) == "NONE_NO_VALID_OFFER"


def test_receipt_uses_the_refresh_forecast_and_flags_a_missing_morning(monkeypatch, tmp_path):
    """2026-11-09: the morning was killed (no walk_forward section) and the 18:00 refresh forecast the slate; the receipt
    must report that forecast and must not claim OK."""
    monkeypatch.setenv("CBB_SCHEDULER_ROLE", "PRIMARY_WRITER")
    now = pd.Timestamp("2026-11-09T23:56:00Z")
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", clock=lambda: now.to_pydatetime())
    day = date(2026, 11, 9)
    sec = {"rotowire_refresh": "ENABLED", "status": "FORECAST", "note": None, "snapshot_id": "F1", "window": "ROTOWIRE_1800",
           "model_version": "m1", "games_forecast": 7, "players_forecast": 313, "pmfs_persisted": 2100,
           "games_not_scored": {}, "rotowire_web_acquisition": {"auth": "PASS", "news_status": "PROCESSED",
                                                                "snapshot_status": "PROCESSED", "news_continuity": "CONTINUOUS",
                                                                "at": now.isoformat()}}
    r = L.health(life, day, availability_1800=sec)["receipt"]
    assert (r["FORECAST_STATUS"], r["FORECAST_WINDOW"], r["MODEL_VERSION"]) == ("FORECAST", "ROTOWIRE_1800", "m1")
    assert (r["FORECAST_GAME_COUNT"], r["FORECAST_PLAYER_COUNT"], r["PMF_COUNT"], r["IDENTITY_BLOCKED_GAMES"]) == (7, 313, 2100, 0)
    assert r["MORNING_REFIT_STATUS"] is None and "MORNING_NOT_RECORDED" in r["WARNINGS"] and r["FINAL_STATUS"] == "DEGRADED"
    blocked = {**sec, "snapshot_id": "F2", "games_not_scored": {"G1": ["AVAILABILITY_IDENTITY_UNRESOLVED"], "G2": []}}
    assert L.health(life, day, availability_1800=blocked)["receipt"]["IDENTITY_BLOCKED_GAMES"] == 1
