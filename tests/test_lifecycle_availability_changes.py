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
