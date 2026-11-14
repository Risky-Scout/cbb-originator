"""lifecycle.availability_changes on the production slate frame (2026-11-09 Droplet acceptance: the first RotoWire-ENABLED
refresh raised AttributeError 'feature_cutoff' -- lifecycle.slate carries the validated production `cutoff`, which
rotowire_feed.availability_frame reads as feature_cutoff)."""
from __future__ import annotations

import pandas as pd

from cbb_platform.prod import config as PC, lifecycle as L, rotowire_feed as RW

CUT = pd.Timestamp("2026-11-10T00:30:00Z")


def _slate():
    return pd.DataFrame({"canonical_game_id": ["G-a"], "home_team_id": ["T-h"], "away_team_id": ["T-a"],
                         "scheduled_tip_utc": [CUT + pd.Timedelta(minutes=60)], "cutoff": [CUT]})


def test_availability_changes_passes_the_slate_cutoff_as_the_feature_cutoff(monkeypatch):
    seen = {}

    def frame(ev, sg, now, **kw):
        seen["sg"] = sg
        return pd.DataFrame(columns=["game_id", "player_id", "status"]), {}

    monkeypatch.setattr(RW, "availability_frame", frame)
    monkeypatch.setattr(RW.EventStore, "frame", lambda self: pd.DataFrame())
    monkeypatch.setattr(L, "latest_players", lambda life, gid: None)
    affected, changed = L.availability_changes(None, _slate(), CUT - pd.Timedelta(hours=2))
    assert (affected, changed) == ([], {})
    assert seen["sg"]["feature_cutoff"].tolist() == [CUT]               # exactly the validated production cutoff
    assert "start_time" in seen["sg"]


def test_availability_changes_runs_the_real_frame_on_the_slate_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(PC, "ROTOWIRE_ROOT", tmp_path / "rotowire")        # empty event store
    monkeypatch.setattr(L, "latest_players", lambda life, gid: None)
    affected, changed = L.availability_changes(None, _slate(), CUT - pd.Timedelta(hours=2))
    assert (affected, changed) == ([], {})


def test_availability_changes_keeps_an_explicit_feature_cutoff(monkeypatch):
    seen = {}
    def frame(ev, sg, now, **kw):
        seen["sg"] = sg
        return pd.DataFrame(columns=["game_id", "player_id", "status"]), {}

    monkeypatch.setattr(RW, "availability_frame", frame)
    monkeypatch.setattr(RW.EventStore, "frame", lambda self: pd.DataFrame())
    monkeypatch.setattr(L, "latest_players", lambda life, gid: None)
    g = _slate().assign(feature_cutoff=[CUT - pd.Timedelta(minutes=5)])
    L.availability_changes(None, g, CUT - pd.Timedelta(hours=2))
    assert seen["sg"]["feature_cutoff"].tolist() == [CUT - pd.Timedelta(minutes=5)]


def _with(monkeypatch, cur_rows, prev):
    monkeypatch.setattr(RW, "availability_frame",
                        lambda ev, sg, now, **kw: (pd.DataFrame(cur_rows, columns=["game_id", "player_id", "status"]), {}))
    monkeypatch.setattr(RW.EventStore, "frame", lambda self: pd.DataFrame())
    monkeypatch.setattr(L, "latest_players", lambda life, gid: prev)
    return L.availability_changes(None, _slate(), CUT - pd.Timedelta(hours=2))


def test_a_stored_no_report_equals_an_absent_record(monkeypatch):
    """11-13 Droplet: 936 / 850 / 499 players were flagged NO_REPORT -> NO_REPORT and their games re-simulated."""
    prev = pd.DataFrame({"player_id": ["P1", "P2", "P3"], "availability_status": ["NO_REPORT", "NO_REPORT", "OUT"]})
    assert _with(monkeypatch, [("G-a", "P3", "OUT")], prev) == ([], {})
    assert _with(monkeypatch, [("G-a", "P1", "NO_REPORT"), ("G-a", "P3", "OUT")], prev) == ([], {})


def test_real_changes_are_still_detected(monkeypatch):
    prev = pd.DataFrame({"player_id": ["P1", "P2", "P3"], "availability_status": ["NO_REPORT", "NO_REPORT", "OUT"]})
    assert _with(monkeypatch, [("G-a", "P1", "OUT"), ("G-a", "P3", "OUT")], prev) == (["G-a"], {"G-a": ["P1"]})
    assert _with(monkeypatch, [], prev) == (["G-a"], {"G-a": ["P3"]})               # OUT record withdrawn
    assert _with(monkeypatch, [("G-a", "P3", "QUESTIONABLE")], prev) == (["G-a"], {"G-a": ["P3"]})
    assert _with(monkeypatch, [("G-a", "P3", "OUT"), ("G-a", "P9", "OUT")], prev) == (["G-a"], {"G-a": ["P9"]})


def test_a_never_priced_game_keeps_the_previous_rule(monkeypatch):
    assert _with(monkeypatch, [("G-a", "P1", "NO_REPORT")], None) == (["G-a"], {"G-a": ["P1"]})
    assert _with(monkeypatch, [], None) == ([], {})


def test_priced_status_matches_apply_availability():
    from cbb_platform.prod import inputs as IN
    rows = pd.DataFrame({"game_id": ["G"] * 4, "player_id": ["A", "B", "C", "D"]})
    av = pd.DataFrame({"game_id": ["G", "G"], "player_id": ["A", "B"], "status": ["OUT", "NO_REPORT"],
                       "received_at": [CUT, CUT]})
    merged = rows.merge(av.rename(columns={"status": "s"}), on=["game_id", "player_id"], how="left")["s"].fillna("NO_REPORT")
    assert [L.priced_status(v) for v in [*av["status"], None, float("nan")]] == merged.tolist()


def test_forecast_input_hash_is_exact_order_free_and_per_game():
    import numpy as np
    mi = pd.DataFrame({"game_id": ["G1"] * 3 + ["G2"] * 2, "team_id": ["A", "A", "B", "C", "D"],
                       "player_id": ["p1", "p2", "p3", "p4", "p5"], "min_mu": [30.1, 12.0, 25.5, 8.0, 33.3],
                       "availability_status": ["NO_REPORT"] * 5, "availability_received_at": [pd.NaT] * 5})
    ident = {"model_version": "m", "artifact_sha256": {"a": "b"}}
    a = L.forecast_input_hashes(mi, ["G1", "G2"], ident, 20000)
    assert a == L.forecast_input_hashes(mi.iloc[::-1], ["G1", "G2"], ident, 20000)
    m = mi.copy(); m.loc[0, "min_mu"] = np.nextafter(30.1, 31)           # one ulp is a different input
    b = L.forecast_input_hashes(m, ["G1", "G2"], ident, 20000)
    assert b["G1"] != a["G1"] and b["G2"] == a["G2"]
    m = mi.copy(); m.loc[4, "availability_status"] = "OUT"
    assert L.forecast_input_hashes(m, ["G2"], ident, 20000)["G2"] != a["G2"]
    m = mi.copy(); m["availability_received_at"] = pd.Timestamp("2026-11-13T15:00Z")
    assert L.forecast_input_hashes(m, ["G1", "G2"], ident, 20000) == a         # arrival time is not an input
    assert L.forecast_input_hashes(mi, ["G1"], {**ident, "model_version": "n"}, 20000)["G1"] != a["G1"]
    assert L.forecast_input_hashes(mi, ["G1"], ident, 10000)["G1"] != a["G1"]
