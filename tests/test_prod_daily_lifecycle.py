"""Focused tests of the live daily production lifecycle (directive 2026-11-04 LIVE DAILY PRODUCTION LIFECYCLE):

    durable PMF evidence      prod/pmf_evidence.py + shadow-tick persistence (ticks/<tick>/pmfs.parquet)
    forecast snapshots        prod/lifecycle.py (line-independent, immutable, pre-cutoff only, fixed ET schedule)
    Distributions export      reads durable evidence only: survives run-directory teardown; latest pre-cutoff snapshot
    odds discovery            curated explicit team map (The Odds API "St" / "CSU" / "(Chi)" spellings)
    completed-game ingestion  unresolved-player NULL keys; latest-receipt-per-game for overlapping box windows

CI-safe: synthetic history, labeled fixture payloads behind fake HTTP layers, temporary roots. No network (every socket
entry point refused where the lifecycle runs), no real credential, no model change.
"""
from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from cbb_platform import cbbd_box_route as R
from cbb_platform.prod import (config as PC, exports as EX, feed_fixtures as FF, fixtures as FX, ledger as LG,
                               lifecycle as L, loader as LD, matching as MT, odds_feed as OF, odds_fixtures as OX,
                               ops_store as OS, pmf_evidence as PE, shadow_tick as TK, state as ST, sync as Y)
from tests.test_prod_shadow_tick import Clock, _ctx, life  # noqa: F401  (module fixture: the shadow-tick lifecycle)

SIMS = 300


# ============================================================================ durable PMF evidence
def _run_dir(tmp: Path, pmfs: dict) -> Path:
    d = tmp / "run"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"game_id": g, "team_id": "T-1", "player_id": p, "market": m, "method": "BANK", "projection": float(np.dot(np.arange(len(v)), v)),
             "pmf": list(v), "game_label": "Away @ Home 2026-11-04", "player_label": p} for (g, p, m), v in pmfs.items()]
    pd.DataFrame(rows).to_parquet(d / "market_pmfs.parquet", index=False)
    return d


def test_pmf_evidence_keys_receipt_ids_and_full_support(tmp_path):
    v = np.array([0.1, 0.2, 0.3, 0.4])
    d = _run_dir(tmp_path, {("G-1", "P-1", "PTS"): v, ("G-1", "P-1", "REB"): np.array([0.5, 0.5])})
    pe = PE.from_run(d, "R1", "2026-11-04T18:00:00Z", "cbb-jm1-2026-27-20261101")
    assert list(pe.columns) == PE.COLUMNS
    assert {"prediction_receipt_id", "game_id", "player_id", "team_id", "stat", "prediction_as_of", "model_version",
            "outcome", "probability", "mean"} <= set(pe.columns)
    pts = pe[pe["stat"] == "PTS"]
    assert pts["outcome"].tolist() == [0, 1, 2, 3] and np.allclose(pts["probability"], v)
    assert pts["prediction_receipt_id"].nunique() == 1
    assert pts["prediction_receipt_id"].iat[0] == LG.prediction_id("R1", "G-1", "P-1", "PTS", None, None)
    assert (pe["model_version"] == "cbb-jm1-2026-27-20261101").all()
    assert pe["prediction_as_of"].iat[0] == "2026-11-04T18:00:00+00:00"
    back = PE.distributions(pe)
    assert len(back) == 2 and all(abs(x["pmf"].sum() - 1) <= PE.PMF_TOL for x in back)


def test_pmf_evidence_is_immutable_and_refuses_bad_pmfs(tmp_path):
    d = _run_dir(tmp_path, {("G-1", "P-1", "PTS"): np.array([0.25, 0.75])})
    pe = PE.from_run(d, "R1", "2026-11-04T18:00:00Z", "M1")
    p = tmp_path / "ev" / PE.FILE
    assert PE.write(p, pe)["written"] is True
    assert PE.write(p, pe)["written"] is False                     # identical content: no-op
    changed = pe.assign(probability=pe["probability"][::-1].to_numpy())
    with pytest.raises(PE.PMFEvidenceError):
        PE.write(p, changed)                                       # never overwritten
    for bad in (np.array([1.1, -0.1]), np.array([0.5, 0.4])):
        with pytest.raises(PE.PMFEvidenceError):
            PE.from_run(_run_dir(tmp_path / str(bad.sum()), {("G", "P", "PTS"): bad}), "R", "2026-11-04T18:00Z", "M")


def test_shadow_tick_persists_the_pmfs_it_used_and_exports_survive_runner_teardown(life, tmp_path):
    reg = pd.DataFrame(OS.read_jsonl(life["w"] / "ops" / "registry" / "games.jsonl"))
    pred = reg[reg["status"] == "PREDICTED"].drop_duplicates("game_id")
    assert len(pred)
    for tid, g in pred.groupby("tick_id"):
        tdir = life["w"] / "ops" / "ticks" / tid
        pe = PE.read(tdir / PE.FILE)
        assert set(g["game_id"]) <= set(pe["game_id"])
        run = g["run_id"].iat[0]
        assert (pe["run_id"] == run).all() and (pe["prediction_as_of"] == pd.Timestamp(g["prediction_as_of"].iat[0]).isoformat()).all()
        # the persisted PMFs are exactly the run's PMFs (never recomputed)
        mp = pd.read_parquet(life["w"] / "prod" / "runs" / run / "market_pmfs.parquet")
        mp = mp[mp["game_id"].isin(g["game_id"])]
        got = {(x["game_id"], x["player_id"], x["stat"]): x["pmf"] for x in PE.distributions(pe)}
        assert len(got) == len(mp)
        for r in mp.itertuples(index=False):
            assert np.array_equal(got[(r.game_id, r.player_id, r.market)], np.asarray(r.pmf, dtype=float))
        tick = json.loads((tdir / "tick.json").read_text())
        assert tick["scoring"]["evidence"]["pmfs"] == pe["prediction_receipt_id"].nunique()
    # runner teardown: only the evidence tree survives; Distributions are still exported from it
    ops = tmp_path / "ops"
    shutil.copytree(life["w"] / "ops", ops, ignore=shutil.ignore_patterns(".git"))
    day = EX.slate_date_of(pred["scheduled_tip"].iat[0])
    rep = EX.daily(day, ops_root=ops, runs_root=tmp_path / "no_runs", out_root=tmp_path / "x",
                   history_db=life["hist"], state_root=life["w"] / "prod" / "state")
    assert rep["distributions"]["status"] == "WRITTEN" and rep["distributions"]["PMF_RECONCILIATION"] == "PASS"


# ============================================================================ forecast snapshots (lifecycle)
@pytest.fixture(scope="module")
def day(tmp_path_factory):
    w = tmp_path_factory.mktemp("lifecycle")
    hist = FX.synthetic_history(w / "history.duckdb")
    clock = Clock("2026-11-01T12:00Z")
    cbbd = FF.FakeCBBD(FF.payloads("before"))
    odds = OX.FakeOdds([])
    ctx = _ctx(w, hist, clock, cbbd, odds)
    TK.tick(ctx)
    cbbd.bodies = FF.payloads("after")
    clock.t = pd.Timestamp("2026-11-05T06:00Z")
    TK.tick(ctx)
    life_ = L.Life(ops_root=w / "ops", prod_root=w / "prod", history_db=hist, model_root=w / "no_wf_store",
                   clock=clock, sims=SIMS)
    return {"w": w, "hist": hist, "clock": clock, "odds": odds, "life": life_, "ctx": ctx}


def _refuse_sockets(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network refused in the lifecycle test")
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)


def test_schedule_is_exactly_0700_plus_four_rotowire_refreshes():
    s = L.schedule_spec()
    assert s["timezone"] == "America/New_York" and s["morning"]["at"] == "07:00"
    assert s["rotowire_refresh"]["at"] == ["10:00", "13:00", "16:00", "18:00"]
    assert L.ROTOWIRE_REFRESH_TIMES_ET == ("10:00", "13:00", "16:00", "18:00")
    assert s["other_fixed_rotowire_refresh_times"] == []
    with pytest.raises(ValueError):
        L.window_label("12:00")


def test_initial_forecast_is_line_independent_immutable_and_persists_pmfs(day, monkeypatch):
    _refuse_sockets(monkeypatch)
    day["clock"].t = pd.Timestamp("2026-11-10T12:00Z")             # 07:00 ET on 2026-11-10
    n_odds = len(day["odds"].calls)
    r = L.forecast(day["life"], L.INITIAL)
    assert r["status"] == "FORECAST" and r["window"] == "INITIAL_0700" and r["slate_date"] == "2026-11-10"
    assert r["quotes_used"] == 0 and r["paid_requests"] == 0 and len(day["odds"].calls) == n_odds
    assert r["games_forecast"] > 0 and r["players_forecast"] > 0 and r["stat_predictions"] == r["pmfs_persisted"] > 0
    sdir = day["life"].forecasts("2026-11-10", r["snapshot_id"])
    pe = PE.read(sdir / PE.FILE)
    assert pe["prediction_receipt_id"].nunique() == r["pmfs_persisted"]
    assert (pe["model_version"] == r["model_version"]).all()
    assert set(pe["game_id"]) <= set(r["games"]) and all(pd.Timestamp(c) > day["clock"].t for c in r["cutoffs"].values())
    fp = pd.read_parquet(sdir / "forecast_players.parquet")
    assert {"min_mu", "p_play", "availability_status", "model_version", "prediction_as_of"} <= set(fp.columns)
    assert r["model_version"] == LD.load().identity["model_version"]  # no walk-forward store: the frozen artifacts
    snap = json.loads((sdir / "snapshot.json").read_text())
    assert snap["snapshot_id"] == r["snapshot_id"] and not OS.write_once(sdir / "snapshot.json", {"x": 1})
    idx = L.snapshots(day["life"])
    assert idx[-1]["snapshot_id"] == r["snapshot_id"]
    day["initial"] = r


def test_refresh_without_rotowire_key_changes_nothing(day, monkeypatch):
    monkeypatch.delenv("ROTOWIRE_API_KEY", raising=False)
    day["clock"].t = pd.Timestamp("2026-11-10T15:00Z")             # 10:00 ET
    r = L.forecast(day["life"], "10:00")
    assert r["rotowire_refresh"] == "BLOCKED_NO_KEY" and r["status"] == "NO_FORECAST_DUE" and r["games"] == []
    monkeypatch.setenv("ROTOWIRE_API_KEY", "dummy-not-a-key")
    assert L.rotowire_state(day["life"]) == "DISABLED_SMOKE_NOT_PASSED"    # a key alone never enables the refresh


def test_no_forecast_after_a_games_cutoff_and_distributions_use_latest_pre_cutoff(day, monkeypatch, tmp_path):
    _refuse_sockets(monkeypatch)
    life_ = day["life"]
    first = day["initial"]
    cut = min(pd.Timestamp(c) for c in first["cutoffs"].values())
    day["clock"].t = cut + pd.Timedelta(minutes=1)                  # after the cutoff of the earliest game(s)
    late = L.forecast(life_, L.INITIAL)
    gone = [g for g, c in first["cutoffs"].items() if pd.Timestamp(c) <= day["clock"].t]
    assert gone and not set(gone) & set(late["games"])               # a post-cutoff game is never re-forecast
    # a doctored snapshot made AFTER the cutoff is never used for that game
    idx = life_.forecasts("index.jsonl")
    bogus = dict(OS.read_jsonl(idx)[0], snapshot_id="F-BOGUS", prediction_as_of=(cut + pd.Timedelta(hours=1)).isoformat())
    (life_.forecasts("2026-11-10", "F-BOGUS")).mkdir(parents=True)
    shutil.copy(life_.forecasts("2026-11-10", first["snapshot_id"], PE.FILE), life_.forecasts("2026-11-10", "F-BOGUS", PE.FILE))
    OS.append_jsonl(idx, [bogus])
    src = EX.forecast_sources(life_.ops_root, "2026-11-10")
    assert all(aso < c for v in src.values() for aso, c, _ in v)
    assert not any("F-BOGUS" in str(p) for v in src.values() for _, _, p in v)
    rep = EX.daily("2026-11-10", ops_root=life_.ops_root, runs_root=tmp_path / "no_runs", out_root=tmp_path / "x",
                   history_db=day["hist"], state_root=life_.state_root)
    d = rep["distributions"]
    assert d["status"] == "WRITTEN" and d["PMF_RECONCILIATION"] == "PASS" and d["negative"] == 0
    assert d["distributions"] == len(set(PE.read(life_.forecasts("2026-11-10", first["snapshot_id"], PE.FILE))["prediction_receipt_id"]))
    assert rep["boards"]["status"] == "MISSING_EVIDENCE" and rep["grades"]["status"] == "MISSING_EVIDENCE"  # no quote: no bet


# ============================================================================ odds discovery: curated team map
def test_curated_odds_team_map_is_unique_and_resolves_provider_spellings():
    m = MT.odds_team_map()
    assert len(m) >= 7 and m["canonical_team_id"].str.match(r"^T-[0-9a-f]{16}$").all()
    assert not m["provider_team_name"].map(MT.norm).duplicated().any()
    teams = pd.DataFrame([{"canonical_team_id": "T-3216cda83ee1dea6", "school": "Wichita State",
                           "display_name": "Wichita State Shockers", "mascot": "Shockers"},
                          {"canonical_team_id": "T-be38d13b6bcc187d", "school": "Cal State Northridge",
                           "display_name": "Cal State Northridge Matadors", "mascot": "Matadors"}])
    assert MT.resolve_team("Wichita St Shockers", MT.team_index(teams, None)) == (None, "UNMATCHED")   # the defect
    tidx = MT.team_index(teams, m)
    assert MT.resolve_team("Wichita St Shockers", tidx) == ("T-3216cda83ee1dea6", "EXPLICIT_MAP")
    assert MT.resolve_team("CSU Northridge Matadors", tidx) == ("T-be38d13b6bcc187d", "EXPLICIT_MAP")
    assert MT.resolve_team("Wichita State Shockers", tidx) == ("T-3216cda83ee1dea6", "EXACT_NORMALIZED")
    slate = pd.DataFrame({"canonical_game_id": ["G-x"], "home_team_id": ["T-3216cda83ee1dea6"],
                          "away_team_id": ["T-be38d13b6bcc187d"], "neutral_site": [False],
                          "start_time": [pd.Timestamp("2026-11-05T00:30Z")]})
    ev = pd.DataFrame({"provider_event_id": ["e1"], "provider_home_team": ["Wichita St Shockers"],
                       "provider_away_team": ["CSU Northridge Matadors"],
                       "provider_commence_time": [pd.Timestamp("2026-11-05T00:30Z")]})
    assert OF.match_events(ev, slate, tidx)["canonical_game_id"].tolist() == ["G-x"]


def test_duplicate_curated_map_entries_are_refused(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("provider_team_name,canonical_team_id\nWichita St Shockers,T-1\nwichita st. shockers,T-2\n")
    with pytest.raises(ValueError):
        MT.odds_team_map(p)


# ============================================================================ completed-game ingestion defects
def test_overlapping_box_windows_take_each_game_from_its_latest_receipt():
    t0, t1 = pd.Timestamp("2026-11-03T04:00Z"), pd.Timestamp("2026-11-03T12:00Z")
    old = [{"gameId": 372661, "teamId": 168, "points": 70}, {"gameId": 1, "teamId": 5, "points": 60}]
    new = [{"gameId": 372661, "teamId": 168, "points": 71}, {"gameId": 2, "teamId": 6, "points": 50}]
    recs, sup = Y.latest_receipt_per_game([(t0, 0, old), (t1, 1, new)])
    by = {(r["gameId"], r["teamId"]): r["points"] for r in recs}
    assert by == {(372661, 168): 71, (1, 5): 60, (2, 6): 50} and sup == 1
    rows = R.reshape("team_game_stats", old + new, "x")             # the pre-fix concatenation fails closed
    with pytest.raises(ValueError, match="conflicting CBBD records"):
        R.dedupe("team_game_stats", rows)
    R.dedupe("team_game_stats", R.reshape("team_game_stats", recs, "x"))
    same = [{"gameId": 9, "teamId": 1, "points": 1}, {"gameId": 9, "teamId": 1, "points": 2}]
    recs2, _ = Y.latest_receipt_per_game([(t1, 0, same)])            # a conflict inside ONE response still fails closed
    with pytest.raises(ValueError):
        R.dedupe("team_game_stats", R.reshape("team_game_stats", recs2, "x"))


def test_unresolved_players_are_distinct_rows_but_resolved_duplicates_still_fail(tmp_path):
    db = tmp_path / "inc.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA flagship")
    con.execute("""CREATE TABLE flagship.player_game_outcomes AS SELECT * FROM (VALUES
                   ('PG-1', 'G-1', 'T-1', NULL), ('PG-2', 'G-1', 'T-1', NULL), ('PG-3', 'G-1', 'T-1', 'P-1'))
                   t(player_game_key, canonical_game_id, canonical_team_id, canonical_player_id)""")
    k = ", ".join(ST.key_exprs("flagship.player_game_outcomes"))
    q = f"SELECT count(*) FROM (SELECT {k} FROM flagship.player_game_outcomes GROUP BY ALL HAVING count(*) > 1)"
    assert con.execute(q).fetchone()[0] == 0                        # two unresolved players: not a duplicate key
    con.execute("INSERT INTO flagship.player_game_outcomes VALUES ('PG-4', 'G-1', 'T-1', 'P-1')")
    assert con.execute(q).fetchone()[0] == 1                        # a resolved player twice: still a duplicate
    con.close()
    assert ST.key_exprs("flagship.games") == ["canonical_game_id"]
    assert ST.key_exprs("flagship.substitutions")[2] == "coalesce(canonical_player_id, 'UNRESOLVED:' || source_athlete_id)"


# ============================================================================ Droplet schedule (deploy/droplet/systemd)
def test_droplet_units_implement_exactly_the_fixed_schedule():
    import re
    import subprocess
    d = PC.C.PROJECT_ROOT / "deploy" / "droplet"
    sd = d / "systemd"
    timers = {p.name: [ln for ln in p.read_text().splitlines() if ln.startswith("OnCalendar=")] for p in sd.glob("*.timer")}
    assert timers["cbb-originator-morning.timer"] == [f"OnCalendar=*-*-* {L.MORNING_TIME_ET}:00 America/New_York"]
    assert timers["cbb-originator-refit-retry.timer"] == [f"OnCalendar=*-*-* {L.REFIT_RETRY_TIME_ET}:00 America/New_York"]
    for t in L.ROTOWIRE_REFRESH_TIMES_ET:
        assert timers[f"cbb-originator-availability-{t.replace(':', '')}.timer"] == [f"OnCalendar=*-*-* {t}:00 America/New_York"]
    for p in sd.glob("*"):
        assert not re.search(r"(?i)^\s*Environment=.*(API_KEY|ENCRYPTION_KEY|TOKEN)=", p.read_text(), re.M)
    run = d / "bin" / "cbb-originator-run"
    for bad in (["lifecycle-refresh", "12:00"], ["lifecycle-refresh"], ["lifecycle-noon"], ["morning"]):
        r = subprocess.run(["bash", str(run), *bad], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert r.returncode == 3, (bad, r.stderr)


# ============================================================================ shared heavy lock: walk-forward only
def _wf_life(tmp_path):
    clock = Clock(pd.Timestamp("2026-11-10T12:00Z"))
    return L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=tmp_path / "wf", clock=clock)


def test_busy_shared_lock_defers_the_refit_and_keeps_last_good(tmp_path, monkeypatch):
    import fcntl
    import os
    lock = tmp_path / "heavy.lock"
    monkeypatch.setenv(L.HEAVY_LOCK_ENV, str(lock))
    monkeypatch.setenv(L.HEAVY_WAIT_ENV, "0.2")
    life = _wf_life(tmp_path)
    calls = []
    holder = os.open(lock, os.O_WRONLY | os.O_CREAT)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        r = L.walk_forward(life, L.et_date(life.now()), runner=lambda *a, **k: calls.append(1) or {})
    finally:
        os.close(holder)
    assert r["DAILY_REFIT_STATUS"] == L.DEFERRED_SHARED_LOCK and r["lock_result"] == "NOT_ACQUIRED"
    assert calls == []                                   # nothing fitted; ACTIVE untouched
    assert lock.exists()                                 # never deleted
    recs = OS.read_jsonl(L.wf_attempts_path(life, L.et_date(life.now())))
    assert [x["DAILY_REFIT_STATUS"] for x in recs] == [L.DEFERRED_SHARED_LOCK]


def test_lock_is_held_only_during_the_walk_forward_and_released(tmp_path, monkeypatch):
    import fcntl
    import os
    lock = tmp_path / "heavy.lock"
    monkeypatch.setenv(L.HEAVY_LOCK_ENV, str(lock))
    monkeypatch.setenv(L.HEAVY_WAIT_ENV, "1")
    life = _wf_life(tmp_path)
    seen = {}

    def runner(*a, **k):
        fd = os.open(lock, os.O_WRONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["held"] = False
        except BlockingIOError:
            seen["held"] = True
        finally:
            os.close(fd)
        return {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED", "ACTIVE_MODEL_VERSION": "v2"}
    r = L.walk_forward(life, L.et_date(life.now()), runner=runner)
    assert seen["held"] is True and r["lock_result"] == "ACQUIRED" and r["DAILY_MODEL_PROMOTION"] == "PROMOTED"
    fd = os.open(lock, os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)        # released after the step
    os.close(fd)


def test_walk_forward_exception_keeps_last_good_and_is_recorded(tmp_path, monkeypatch):
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    life = _wf_life(tmp_path)

    def boom(*a, **k):
        raise MemoryError("synthetic")
    r = L.walk_forward(life, L.et_date(life.now()), runner=boom)
    assert r["DAILY_REFIT_STATUS"] == "FAIL" and r["DAILY_MODEL_PROMOTION"] == "NOT_PROMOTED" and "MemoryError" in r["reason"]


def test_refit_retry_is_a_no_op_unless_the_morning_was_deferred(tmp_path, monkeypatch):
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    life = _wf_life(tmp_path)
    day = L.et_date(life.now())
    calls = []
    r = L.refit_retry(life, day, runner=lambda *a, **k: calls.append(1) or {})
    assert r["status"] == "NO_OP" and calls == []
    OS.append_jsonl(L.wf_attempts_path(life, day), [{"attempt": "MORNING_0700", "DAILY_REFIT_STATUS": "PASS"}])
    assert L.refit_retry(life, day, runner=lambda *a, **k: calls.append(1) or {})["status"] == "NO_OP" and calls == []
    h = json.loads(L.health_path(life, day).read_text())
    assert h["et_date"] == day.isoformat() and h["refit_retry"]["status"] == "NO_OP"


def test_refit_retry_runs_once_after_deferral_and_reforecasts_on_promotion(tmp_path, monkeypatch):
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    life = _wf_life(tmp_path)
    day = L.et_date(life.now())
    OS.append_jsonl(L.wf_attempts_path(life, day), [{"attempt": "MORNING_0700", "DAILY_REFIT_STATUS": L.DEFERRED_SHARED_LOCK}])
    fc = []
    monkeypatch.setattr(L, "forecast", lambda lf, w=L.INITIAL, **k: fc.append(w) or {"status": "FORECAST", "window": w})
    monkeypatch.setattr(L, "exports_for", lambda lf, d: {})
    r = L.refit_retry(life, day, runner=lambda *a, **k: {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED"})
    assert r["status"] == "RAN" and fc == [L.REFIT_RETRY]
    assert L.window_label(L.REFIT_RETRY) == "REFIT_RETRY_0800"
    again = L.refit_retry(life, day, runner=lambda *a, **k: {"DAILY_MODEL_PROMOTION": "PROMOTED"})
    assert again["status"] == "NO_OP" and fc == [L.REFIT_RETRY]          # exactly one retry per day
    h = json.loads(L.health_path(life, day).read_text())
    assert not any(k in json.dumps(h) for k in ("API_KEY=", "ENCRYPTION_KEY="))
