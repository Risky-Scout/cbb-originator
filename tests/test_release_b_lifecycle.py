"""Release B lifecycle integration: the game layer is SHADOW-gated, failure-isolated, lineage-checked, and its health
distinguishes MODEL_FAILURE from NO_MARKET_AVAILABLE; daily team ratings are one state per day (no intraday refit)."""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cbb_platform.game import forecast as GF, ratings as GR
from cbb_platform.prod import game_exports as GX, lifecycle as L


def test_game_forecast_is_off_unless_shadow(monkeypatch, tmp_path):
    monkeypatch.delenv("CBB_GAME_MODEL", raising=False)
    assert L.game_forecast(tmp_path, {}, pd.DataFrame(), {}, date(2026, 11, 19), None)["status"] == "NOT_ENABLED"


def test_game_layer_failure_never_breaks_the_player_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("CBB_GAME_MODEL", "SHADOW")
    monkeypatch.setattr(GF, "forecast", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = L.game_forecast(tmp_path, {"snapshot_id": "F"}, pd.DataFrame(), {"increment_db": "x"}, date(2026, 11, 19), None)
    assert r["status"] == "FAILED" and "boom" in r["reason"]


@pytest.mark.parametrize("gf,exports,gq,want", [
    (None, {}, {}, ("NOT_ENABLED", "NOT_ENABLED", "NOT_ENABLED")),
    ({"status": "COMPLETE", "ratings_fresh": {"TEAM_RATINGS_FRESH": True}}, {"game_lines": {"rows_with_market": 12}},
     {"status": "COMPLETE"}, (True, True, "VALID_MARKETS")),
    ({"status": "COMPLETE", "ratings_fresh": {"TEAM_RATINGS_FRESH": True}}, {"game_lines": {"rows_with_market": 0}},
     {"status": "COMPLETE"}, (True, True, "NO_MARKET_AVAILABLE")),
    ({"status": "PARTIAL", "ratings_fresh": {"TEAM_RATINGS_FRESH": False}}, {}, {"status": "FAILED"},
     (False, "MODEL_FAILURE: PARTIAL", "CAPTURE_FAILED")),
    ({"status": "COMPLETE", "ratings_fresh": {"TEAM_RATINGS_FRESH": True}}, {}, {}, (True, True, "NOT_CAPTURED"))])
def test_game_health_states(gf, exports, gq, want):
    lf = {"game_forecast": gf} if gf else {}
    s = L.game_state({"exports": exports, "game_quotes": gq}, lf, no_games=False)
    assert (s["TEAM_RATINGS_FRESH"], s["GAME_FORECAST_COMPLETE"], s["GAME_MARKETS_STATE"]) == want


def test_game_layer_is_never_part_of_day_operational():
    h = {"latest_forecast": {"game_forecast": {"status": "FAILED"}}, "et_date": "2026-11-19"}
    r = L.receipt(h)
    assert r["HEALTH_CONTRACT"]["GAME_FORECAST_COMPLETE"] == "MODEL_FAILURE: FAILED"
    assert "GAME" not in json.dumps([k for k in L.receipt({"et_date": "x"})["HEALTH_CONTRACT"]["ACTIVE_BLOCKER"] or ""])


def _hist():
    rows = []
    t0 = pd.Timestamp("2026-11-03 23:00", tz="UTC")
    for d in range(16):
        for i in range(3):
            tip = t0 + pd.Timedelta(days=d)
            rows.append(dict(game_id=f"G{d}_{i}", season=2027,
                             game_date_et=pd.Timestamp((tip - pd.Timedelta(hours=5)).date()), tip_utc=tip,
                             home=f"T{i}", away=f"T{i + 3}", neutral=False, conference_game=False, home_pts=75 + i,
                             away_pts=70, ot=0, home_conf="c", away_conf="c", poss=68.0, poss_game=68.0,
                             knowable_at=tip + pd.Timedelta(hours=3)))
    return pd.DataFrame(rows)


def test_daily_ratings_one_state_per_day_and_recovery_rebuild(tmp_path):
    hist = _hist()
    cut = pd.Timestamp("2026-11-19 11:00", tz="UTC")
    p = GR.RatingParams()
    r1, s1 = GF.daily_ratings(tmp_path, hist, cut, None, p, pending_d1=1)          # a D-1 final still missing
    assert s1["source"] == "BUILT" and not s1["fresh"]["TEAM_RATINGS_FRESH"]
    r2, s2 = GF.daily_ratings(tmp_path, hist, cut, None, p, pending_d1=0)          # recovery once it arrived
    assert s2["source"] == "REBUILT_RECOVERY_NOT_FRESH" and s2["fresh"]["TEAM_RATINGS_FRESH"]
    late = pd.concat([hist, hist.tail(1).assign(game_id="LATE", home_pts=120)])  # a later sync changes nothing today
    r3, s3 = GF.daily_ratings(tmp_path, late, cut, None, p, pending_d1=0)
    assert s3["source"] == "REUSED_DAILY_STATE" and r3.state_id() == r2.state_id()


def test_lineage_validation_detects_a_mismatched_player_snapshot(tmp_path):
    day = "2026-11-19"
    sd = tmp_path / "forecasts" / day / "F1"
    sd.mkdir(parents=True)
    cut = "2026-11-19T11:00:00+00:00"
    pd.DataFrame([{"player_run_id": "R1", "player_model_version": "M1", "state_version": "S1", "forecast_id": "F1",
                   "ratings_state_id": "TR-a", "training_cutoff_utc": cut}]).to_parquet(sd / GF.FILE)
    (sd / "snapshot.json").write_text(json.dumps({"run_id": "R1", "model_version": "M1", "state_version": "S1",
                                                  "snapshot_id": "F1"}))
    assert GX.lineage(tmp_path, day)["status"] == "PASS"
    (sd / "snapshot.json").write_text(json.dumps({"run_id": "R1", "model_version": "M2", "state_version": "S1",
                                                  "snapshot_id": "F1"}))
    bad = GX.lineage(tmp_path, day)
    assert bad["status"] == "FAIL" and bad["mismatches"][0]["field"] == "player_model_version"


def _sched(tip, cutoff):
    return pd.DataFrame([{"canonical_game_id": "G1", "game_date_et": "2026-11-19", "game_universe": "DI_VS_DI",
                          "not_played": False, "home_points": np.nan, "scheduled_tip_utc": tip, "cutoff": cutoff}])


def test_game_quote_slate_keeps_a_game_from_its_cutoff_until_tip(monkeypatch):
    """2026-11-19 production finding: capture used the player slate (cutoff > now), so no game had a quote inside the
    30-min close window (CLV = NO_CLOSE_QUOTE x 103) and the 10-min near-tip cadence never engaged."""
    tip, cut = pd.Timestamp("2026-11-19 19:30", tz="UTC"), pd.Timestamp("2026-11-19 18:30", tz="UTC")
    monkeypatch.setattr(L.SH, "schedule", lambda st: _sched(tip, cut))
    now = pd.Timestamp("2026-11-19 19:10", tz="UTC")                                  # after cutoff, before tip
    assert len(L.slate({}, date(2026, 11, 19), now)) == 0                             # player slate: closed
    assert list(L.quote_slate({}, date(2026, 11, 19), now)["canonical_game_id"]) == ["G1"]
    assert len(L.quote_slate({}, date(2026, 11, 19), tip)) == 0                       # never at / after tip


def test_game_quotes_near_tip_cadence_engages_after_the_cutoff(monkeypatch, tmp_path):
    tip, cut = pd.Timestamp("2026-11-19 19:30", tz="UTC"), pd.Timestamp("2026-11-19 18:30", tz="UTC")
    monkeypatch.setattr(L.SH, "schedule", lambda st: _sched(tip, cut))
    monkeypatch.setattr(L.ST, "current", lambda root: {"increment_db": "x"})
    root = tmp_path / "ops" / "game_quotes" / "2026-11-19"
    root.mkdir(parents=True)
    (root / "GQ20261119T185000Z-a.json").write_text(json.dumps({"status": "COMPLETE", "at": "2026-11-19T18:50:00+00:00",
                                                               "event_calls": 1}))

    class Life:
        ops_root, state_root = tmp_path / "ops", tmp_path / "state"
    due, extras = L.game_quotes_due(Life, date(2026, 11, 19), pd.Timestamp("2026-11-19 19:00", tz="UTC"))
    assert due and extras                                       # 10 min since the last capture, 30 min before tip


def test_closing_uses_the_last_pregame_quote_inside_the_window():
    from cbb_platform.prod import game_quotes as GQ
    q = pd.DataFrame([{"game_id": "G1", "book": "b", "market_type": "SPREAD", "side": "HOME", "point": -2.5,
                       "decimal": d, "collector_received_at": pd.Timestamp(t, tz="UTC")}
                      for t, d in (("2026-11-19 18:20", 1.90), ("2026-11-19 19:10", 1.95), ("2026-11-19 19:20", 1.87),
                                   ("2026-11-19 19:31", 1.50))])
    c = GQ.closing(q, {"G1": "2026-11-19T19:30:00+00:00"})
    assert len(c) == 1 and c["decimal"].iloc[0] == 1.87          # last strictly-pregame, in-window; post-tip ignored


def test_game_performance_splits_conference_games(monkeypatch, tmp_path):
    """2026-11-20 finding: the Conference Game sheet bucketed every evaluation as 'None' (flag never attached)."""
    from cbb_platform.prod import exports as EX, game_quotes as GQ
    (tmp_path / "evaluations" / "2026-11-19").mkdir(parents=True)
    (tmp_path / "evaluations" / "2026-11-19" / GX.EVAL_FILE).write_text("")
    ev = [{"game_id": g, "scheduled_tip_utc": "2026-11-19T23:00:00+00:00"} for g in ("G1", "G2", "G3")]
    monkeypatch.setattr(GX, "evaluations", lambda root, d: ev)
    monkeypatch.setattr(GQ, "load", lambda root, d: pd.DataFrame())
    monkeypatch.setattr(GQ, "closing", lambda q, tips: pd.DataFrame())
    monkeypatch.setattr(GX, "game_pmfs", lambda root, d: pd.DataFrame())
    monkeypatch.setattr(GX, "graded", lambda root, d, fin, cl, names: [
        {"GAME_ID": e["game_id"], "RESULT": "WIN", "MARKET_TYPE": "SPREAD", "SIDE": "HOME", "_slate": d} for e in ev])
    seen = {}
    monkeypatch.setattr(GX, "performance_tables", lambda rows: seen.setdefault("rows", rows) and {})
    monkeypatch.setattr(EX, "write_xlsx", lambda *a, **k: None)
    monkeypatch.setattr(GX, "PERF_SHEETS", ())
    GX.game_performance("2026-11-20", tmp_path, tmp_path, lambda ids: {}, {}, conf_of=lambda ids: {"G1": True, "G2": False})
    assert [r["_conf"] for r in seen["rows"]] == ["CONFERENCE", "NON_CONFERENCE", "UNKNOWN"]
