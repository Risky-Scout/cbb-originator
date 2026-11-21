"""2026-11-21 closing-quote forensics fixes (infrastructure / CLV reporting only; no predictive mathematics).

1. Game-line close capture must not be skipped because ANOTHER long CBB writer holds the global writer lock: the quote
   wake runs a close-only capture under the narrow game-quotes lock (cadence-gated: no extra provider calls), and every
   capture is serialised by that lock and written atomically.
2. Player CLV close index: market_exports.Observations.contracts applied the per-player-stat dedup of
   market_eval.eligible to a whole observation, keeping one player per (book, line, side)."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import game_quotes as GQ, lifecycle as L, market_exports as MX

DAY = date(2026, 11, 21)
TIP = pd.Timestamp("2026-11-21 23:30", tz="UTC")
CUT = pd.Timestamp("2026-11-21 22:30", tz="UTC")


def _sched(tip=TIP, cut=CUT):
    return pd.DataFrame([{"canonical_game_id": "G1", "game_date_et": DAY.isoformat(), "game_universe": "DI_VS_DI",
                          "not_played": False, "home_points": np.nan, "scheduled_tip_utc": tip, "cutoff": cut,
                          "home_team_id": "TH", "away_team_id": "TA"}])


@pytest.fixture
def life(monkeypatch, tmp_path):
    monkeypatch.setenv("CBB_GAME_MODEL", "SHADOW")
    monkeypatch.setenv("CBB_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(L.SH, "schedule", lambda st: _sched())
    monkeypatch.setattr(L.ST, "current", lambda root: {"increment_db": "x"})
    monkeypatch.setattr(L.SH, "team_names", lambda ctx, st: pd.DataFrame())
    from cbb_platform.game import forecast as GF
    from cbb_platform.prod import matching as MT
    monkeypatch.setattr(GF, "neutral_of", lambda db, ids: {})
    monkeypatch.setattr(MT, "team_index", lambda *a, **k: {})
    monkeypatch.setattr(MT, "odds_team_map", lambda: {})
    calls = []

    def fake_capture(sess, now, sl, tidx, out_root, extras=True, mode="QUOTE_WAKE"):
        calls.append({"now": now, "extras": extras, "mode": mode, "games": list(sl["canonical_game_id"])})
        rec = {"capture_id": f"GQ{now:%Y%m%dT%H%M%SZ}-t", "at": now.isoformat(), "status": "COMPLETE",
               "event_calls": 1, "mode": mode}
        d = Path(out_root) / DAY.isoformat()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['capture_id']}.json").write_text(json.dumps(rec))
        return rec
    monkeypatch.setattr(GQ, "capture", fake_capture)
    now = {"t": TIP - pd.Timedelta(minutes=20)}
    lf = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", history_db=tmp_path / "h.duckdb",
                clock=lambda: now["t"].to_pydatetime())
    return lf, calls, now


def test_close_only_capture_runs_inside_the_close_window_and_is_cadence_gated(life):
    lf, calls, now = life
    r = L.game_close_capture(lf)["game_quotes"]
    assert r["status"] == "COMPLETE" and r["mode"] == "CLOSE_ONLY_WRITER_BUSY"
    assert calls[-1]["games"] == ["G1"] and calls[-1]["extras"]           # team totals too (near-tip capture)
    # the same wake's normal capture after the writer lock: NOT_DUE (no second provider call within 10 min)
    now["t"] += pd.Timedelta(minutes=5)
    assert L.game_quotes_capture(lf, DAY)["status"] == "NOT_DUE" and len(calls) == 1
    now["t"] += pd.Timedelta(minutes=5)                                     # next 10-min wake: due again
    assert L.game_close_capture(lf)["game_quotes"]["status"] == "COMPLETE" and len(calls) == 2


def test_close_only_capture_never_runs_outside_the_close_window(life):
    lf, calls, now = life
    now["t"] = TIP - pd.Timedelta(hours=3)                                  # far from any tip: wait for the writer
    assert L.game_close_capture(lf)["game_quotes"]["status"] == "NOT_CLOSE_WINDOW" and not calls
    now["t"] = TIP + pd.Timedelta(minutes=1)                                # after tip: nothing (never post-tip)
    assert L.game_close_capture(lf)["game_quotes"]["status"] == "NOT_CLOSE_WINDOW" and not calls


def test_captures_are_serialised_by_the_narrow_game_quotes_lock(life):
    lf, calls, _ = life
    p = L.game_quotes_lock_path(lf)
    assert p == Path(os.environ["CBB_LOCK_DIR"]) / "cbb-originator.game-quotes.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = L.game_quotes_capture(lf, DAY, close_only=True, lock_wait_sec=0.2)
        assert r["status"] == "SKIPPED_GAME_QUOTES_LOCK_BUSY" and not calls
    assert L.game_quotes_capture(lf, DAY, close_only=True, lock_wait_sec=0.2)["status"] == "COMPLETE"


def test_close_capture_never_needs_the_writer_lock(life, tmp_path):
    lf, calls, _ = life
    w = tmp_path / "locks" / "cbb-originator.writer.lock"
    w.parent.mkdir(parents=True, exist_ok=True)
    with open(w, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)                                     # a long writer (Phase B / morning)
        assert L.game_close_capture(lf, wait_sec=0.2)["game_quotes"]["status"] == "COMPLETE"


def test_capture_files_are_written_atomically(tmp_path):
    p = tmp_path / "GQ1.json"
    GQ._atomic_write(p, lambda t: t.write_text("{}"))
    assert p.read_text() == "{}" and [x.name for x in tmp_path.iterdir()] == ["GQ1.json"]
    with pytest.raises(RuntimeError):
        GQ._atomic_write(tmp_path / "GQ2.json", lambda t: (t.write_text("partial"), (_ for _ in ()).throw(RuntimeError())))
    assert [x.name for x in tmp_path.iterdir()] == ["GQ1.json"]              # no partial file, no tmp left behind


def test_game_quotes_due_contract_unchanged(life):
    lf, _, now = life
    assert L.game_quotes_due(lf, DAY, now["t"]) == (True, True)
    assert L.game_quotes_plan(lf, DAY, now["t"])["near"]


# ------------------------------------------------------------------ player CLV close index
def _obs(tmp_path, rows):
    d = tmp_path / "quotes" / "2026-11-17"
    (d / "observations").mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(d / "observations" / "o1.parquet")
    (d / "observations.jsonl").write_text(json.dumps({"game_id": "G1", "received_at": "2026-11-18T00:43:00+00:00",
                                                      "source": "QUOTE_REFRESH", "file": "observations/o1.parquet"}) + "\n")
    return MX.Observations(tmp_path, "2026-11-17")


def test_close_contract_index_keeps_every_player_at_a_shared_book_line_side(tmp_path, monkeypatch):
    """2026-11-17 G-2fccdef14ce3b24f: Jervis AST UNDER 1.5 at betonlineag was the entry, yet the close index held only
    another player's (betonlineag, 1.5, *) rows -> NO_CLOSE_QUOTE."""
    from cbb_platform.prod import consensus as CN
    monkeypatch.setattr(CN, "row_exclusions", lambda q: pd.Series([""] * len(q), index=q.index))
    base = {"sportsbook": "betonlineag", "line": 1.5, "market_last_update": "2026-11-18T00:40:00Z",
            "canonical_game_id": "G1"}
    rows = [{**base, "canonical_player_id": p, "stat": st, "side": s, "decimal_odds": d}
            for p, st in (("P-A", "AST"), ("P-B", "AST"), ("P-B", "REB"))
            for s, d in (("OVER", 2.2), ("UNDER", 1.6))]
    m = _obs(tmp_path, rows).contracts({"file": "observations/o1.parquet"})
    assert len(m) == 6
    assert m[("P-A", "AST", "betonlineag", 1.5, "UNDER")] == 1.6 and m[("P-B", "REB", "betonlineag", 1.5, "OVER")] == 2.2
