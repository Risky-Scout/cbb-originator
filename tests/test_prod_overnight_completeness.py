"""Overnight state completeness before the morning refit (2026-11-12 incident: the last state before the 11:00Z training
cutoff was 05:22Z; 28 knowable D-I finals waited on the completed-stage request budget / the next 6 h refresh).

    A/B  PRE_REFIT_REFRESH: one sync inside the lead window before the cutoff; none when the state is already fresh
    C/D  COMPLETION_BACKLOG: a provider-FINAL game missing its result or a box re-triggers a sync (bounded spacing)
    E    a complete final is never re-requested; a cached box window whose game row has no players is re-fetched
    F    the retry that obtains the missing box writes a new state; an unchanged re-sync writes none
    G    a provider-final game without a result at the cutoff is FINAL_DATA_INCOMPLETE_AT_CUTOFF (+ prior-day status);
         a stale as-of-cutoff state defers the refit (DEFERRED_STATE_REFRESH_FAILED), the runner is never called
    H    an in-progress game is not polled by the completion loop and stays NOT_FINAL_AT_CUTOFF
    I    point in time: a version or a sync confirmation after the cutoff never counts
    J    the refit trains on (and is gated by) the freshest legal state version
Fixtures only (synthetic schedules / states, the feed_fixtures fake CBBD); no network, no credential."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from cbb_platform.prod import (cbbd_acquire as A, config as PC, feed_fixtures as FF, fixtures as FX, lifecycle as L,
                               shadow_tick as SH, sync as Y)
from cbb_platform.walkforward import eligibility as EL

DAY = date(2026, 11, 12)
CUT = pd.Timestamp("2026-11-12T11:00:00Z")                    # 06:00 ET (EST)
U = lambda s: pd.Timestamp(s)


# ============================================================================ plan(): sync triggers
def _sched(rows):
    df = pd.DataFrame(rows, columns=list(_game("G-0", "2026-11-11T00:00Z", "final", 1.0, True)))
    for c in ("scheduled_tip_utc", "cutoff", "knowable_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def _game(gid, tip, status, points, ingested, universe="DI_VS_DI"):
    t = U(tip)
    return {"canonical_game_id": gid, "scheduled_tip_utc": t, "cutoff": t - pd.Timedelta(hours=1),
            "knowable_at": t + pd.Timedelta(hours=4), "game_universe": universe, "home_points": points,
            "home_team_id": f"T-{gid}-h", "away_team_id": f"T-{gid}-a", "status": status, "ingested": ingested,
            "not_played": False}


def _plan(tmp_path, monkeypatch, rows, now, last_ok, last_try=None):
    ctx = SH.Ctx(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod")
    monkeypatch.setattr(SH, "schedule", lambda st: _sched(rows))
    ctx.sync_log.parent.mkdir(parents=True, exist_ok=True)
    if last_try is not None:
        ctx.sync_log.write_text(json.dumps({"finished_at": U(last_try).isoformat(), "status": "PROMOTED"}) + "\n")
    st = {"pointer": {"last_sync_source_cutoff": U(last_ok).isoformat()}}
    return SH.plan(ctx, st, U(now))


def test_A_stale_state_before_cutoff_triggers_pre_refit_refresh(tmp_path, monkeypatch):
    p = _plan(tmp_path, monkeypatch, [], "2026-11-12T10:20Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert p["sync_due"] and p["sync_why"] == ["PRE_REFIT_REFRESH"]
    early = _plan(tmp_path, monkeypatch, [], "2026-11-12T10:00Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert not early["sync_due"]                                           # outside the 45-min lead window
    assert ("sync", CUT - pd.Timedelta(minutes=PC.PRE_REFIT_REFRESH_LEAD_MINUTES)) in early["next"]


def test_B_fresh_state_no_redundant_refresh(tmp_path, monkeypatch):
    p = _plan(tmp_path, monkeypatch, [], "2026-11-12T10:40Z", last_ok="2026-11-12T10:20Z", last_try="2026-11-12T10:22Z")
    assert not p["sync_due"] and p["sync_why"] == []


def test_C_final_missing_result_enters_completion_loop(tmp_path, monkeypatch):
    rows = [_game("G-c", "2026-11-12T00:00Z", "final", None, False)]            # final, result withheld / not ingested
    p = _plan(tmp_path, monkeypatch, rows, "2026-11-12T05:50Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert p["sync_why"] == ["COMPLETION_BACKLOG"]
    soon = _plan(tmp_path, monkeypatch, rows, "2026-11-12T05:30Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert not soon["sync_due"]                                            # bounded: SYNC_RETRY_MINUTES spacing
    assert ("sync", U("2026-11-12T05:22Z") + pd.Timedelta(minutes=PC.SYNC_RETRY_MINUTES)) in soon["next"]


def test_D_final_with_result_missing_box_enters_completion_loop(tmp_path, monkeypatch):
    rows = [_game("G-d", "2026-11-12T00:00Z", "final", 70.0, False)]            # result but a box missing
    p = _plan(tmp_path, monkeypatch, rows, "2026-11-12T05:50Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert p["sync_why"] == ["COMPLETION_BACKLOG"]


def test_E_complete_final_is_not_retried(tmp_path, monkeypatch):
    rows = [_game("G-e", "2026-11-12T00:00Z", "final", 70.0, True),
            _game("G-x", "2026-11-12T00:00Z", "final", None, False, universe="NON_DI")]   # outside the training universe
    p = _plan(tmp_path, monkeypatch, rows, "2026-11-12T05:50Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert not p["sync_due"]


def test_H_in_progress_game_is_not_polled(tmp_path, monkeypatch):
    rows = [_game("G-h", "2026-11-12T00:00Z", "in_progress", None, False)]
    p = _plan(tmp_path, monkeypatch, rows, "2026-11-12T05:50Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert not p["sync_due"]
    pre = _plan(tmp_path, monkeypatch, rows, "2026-11-12T10:20Z", last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
    assert pre["sync_why"] == ["PRE_REFIT_REFRESH"]                        # ... re-checked by the pre-refit refresh,
    assert pre["refresh_windows"]                                          # its schedule window force-refreshed


def test_I_no_pre_refit_refresh_at_or_after_cutoff(tmp_path, monkeypatch):
    for now in ("2026-11-12T11:00Z", "2026-11-12T11:20Z"):
        p = _plan(tmp_path, monkeypatch, [], now, last_ok="2026-11-12T05:20Z", last_try="2026-11-12T05:22Z")
        assert "PRE_REFIT_REFRESH" not in p["sync_why"]


def test_B_last_sync_just_before_et_midnight_still_schedules_next_day_lead_window(tmp_path, monkeypatch):
    # 04:52Z Nov 13 = 23:52 ET Nov 12: the routine refresh (10:52Z) would land after the 10:15Z lead start
    p = _plan(tmp_path, monkeypatch, [], "2026-11-13T04:55Z", last_ok="2026-11-13T04:52Z", last_try="2026-11-13T04:53Z")
    syncs = [t for k, t in p["next"] if k == "sync"]
    assert min(syncs) == U("2026-11-13T10:15Z")
    pre = _plan(tmp_path, monkeypatch, [], "2026-11-13T10:20Z", last_ok="2026-11-13T04:52Z", last_try="2026-11-13T04:53Z")
    assert pre["sync_why"] == ["PRE_REFIT_REFRESH"]


# ============================================================================ sync: retry only what is incomplete
def _kw(w: Path, hist: Path) -> dict:
    return dict(history_db=hist, state_root=w / "state", cache_root=w / "cache", ledger_path=w / "ledger.jsonl",
                sync_log=w / "sync.jsonl", work_root=w / "work", credential=lambda: FF.DUMMY_CREDENTIAL,
                provider_kind="LIVE")


@pytest.fixture(scope="module")
def retry_world(tmp_path_factory):
    """D final + knowable; the provider's player box first has no players, then is posted; then nothing changes."""
    w = tmp_path_factory.mktemp("completion")
    hist = FX.synthetic_history(w / "history.duckdb")
    kw = _kw(w, hist)
    Y.run(clock=FF.clock_at("2026-11-01T12:00Z"), http_get=FF.FakeCBBD(FF.payloads("before")), **kw)
    full = FF.payloads("after")
    pk = A.request_key("games/players", {"season": 2027, "startDateRange": "2026-11-04", "endDateRange": "2026-11-06"})
    empty = {**full, pk: [{**r, "players": []} for r in full[pk]]}
    http = FF.FakeCBBD(empty)
    out = {"w": w, "http": http, "pk": pk}
    out["s_empty"] = Y.run(clock=FF.clock_at("2026-11-05T06:00Z"), http_get=http, **kw)
    http.bodies = dict(full)                                                 # the provider posts the box
    n = len(http.calls)
    out["s_posted"] = Y.run(clock=FF.clock_at("2026-11-05T06:20Z"), http_get=http, **kw)
    out["calls_posted"] = http.calls[n:]
    n = len(http.calls)
    out["s_again"] = Y.run(clock=FF.clock_at("2026-11-05T06:40Z"), http_get=http, **kw)
    out["calls_again"] = http.calls[n:]
    return out


def test_E_empty_box_window_is_refetched_not_reused(retry_world):
    r = retry_world
    assert r["s_empty"]["completed_games_new"] == 1 and r["s_empty"]["completed_games_ingested"] == 0
    assert r["pk"] in r["calls_posted"]                                     # the stale empty window was re-requested


def test_F_retry_obtaining_missing_box_writes_new_state(retry_world):
    r = retry_world
    assert r["s_posted"]["status"] == "PROMOTED" and r["s_posted"]["completed_games_ingested"] == 1
    assert r["s_posted"]["state_version"] != r["s_empty"]["state_version"]


def test_E_complete_game_never_refetched_and_unchanged_state_not_rewritten(retry_world):
    r = retry_world
    assert r["s_again"]["completed_games_new"] == 0
    assert not [k for k in r["calls_again"] if k.split("|")[0] in ("games/players", "games/teams", "substitutions/game")]
    assert r["s_again"]["status"] == "UNCHANGED" and r["s_again"]["state_version"] == r["s_posted"]["state_version"]


# ============================================================================ eligibility + prior-day completeness
def _state_db(path: Path, games: list[dict]) -> dict:
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    con.execute("CREATE SCHEMA flagship; CREATE SCHEMA metadata")
    con.execute("""CREATE TABLE flagship.games (canonical_game_id VARCHAR, season INTEGER, scheduled_tip_utc TIMESTAMPTZ,
                   tip_time_quality VARCHAR, game_date_et DATE, home_team_id VARCHAR, away_team_id VARCHAR,
                   home_points INTEGER, away_points INTEGER, game_universe VARCHAR)""")
    con.execute("CREATE TABLE metadata.prod_schedule (canonical_game_id VARCHAR, status VARCHAR)")
    con.execute("CREATE TABLE flagship.team_game_outcomes (canonical_game_id VARCHAR, canonical_team_id VARCHAR, points INTEGER)")
    con.execute("""CREATE TABLE flagship.player_game_outcomes (canonical_game_id VARCHAR, canonical_team_id VARCHAR,
                   canonical_player_id VARCHAR, points INTEGER, identity_status VARCHAR)""")
    for g in games:
        tip = U(g["tip"])
        res = g["status"] == "final" and g.get("result", True)
        con.execute("INSERT INTO flagship.games VALUES (?, 2027, ?, 'EXACT', ?, 'H', 'A', ?, ?, 'DI_VS_DI')",
                    [g["id"], tip.to_pydatetime(), tip.tz_convert("America/New_York").date(), 60 if res else None, 50 if res else None])
        con.execute("INSERT INTO metadata.prod_schedule VALUES (?, ?)", [g["id"], g["status"]])
        if res:
            for t, p in (("H", 60), ("A", 50)):
                con.execute("INSERT INTO flagship.team_game_outcomes VALUES (?, ?, ?)", [g["id"], t, p])
                con.execute("INSERT INTO flagship.player_game_outcomes VALUES (?, ?, ?, ?, 'RESOLVED')", [g["id"], t, f"P-{t}", p])
    con.close()
    return {"increment_db": path}


def test_G_final_without_result_is_explicit_and_prior_day_status(tmp_path):
    games = [{"id": "G-ok", "tip": "2026-11-12T00:00Z", "status": "final"},
             {"id": "G-inc", "tip": "2026-11-12T00:00Z", "status": "final", "result": False},
             {"id": "G-late", "tip": "2026-11-12T08:30Z", "status": "final", "result": False},   # knowable 12:30Z
             {"id": "G-live", "tip": "2026-11-12T01:00Z", "status": "in_progress"}]
    st = _state_db(tmp_path / "inc.duckdb", games)
    avail = {"G-ok": {"available_at": "2026-11-12T05:22Z", "state_version": "S1"}}
    el = EL.evaluate(st, CUT, avail).set_index("game_id")
    assert el.loc["G-ok", "decision"] == "INCLUDED"
    assert tuple(el.loc["G-inc", ["decision", "reason"]]) == ("DEFERRED", "FINAL_DATA_INCOMPLETE_AT_CUTOFF")
    assert el.loc["G-late", "reason"] == "NOT_KNOWABLE_AT_CUTOFF"           # HR-1 still withholds it: legitimate
    assert el.loc["G-live", "reason"] == "NOT_FINAL_AT_CUTOFF"              # H: genuinely in progress
    pdc = EL.prior_day_completeness(el.reset_index(), DAY)
    assert pdc["PRIOR_DAY"] == "2026-11-11" and pdc["PRIOR_DAY_GAMES"] == 3        # 19:00 / 20:00 ET tips; 03:30 ET is 11-12
    assert (pdc["FINAL_GAMES"], pdc["FINAL_WITH_RESULT"], pdc["FINAL_WITH_COMPLETE_BOX"]) == (2, 1, 1)
    assert (pdc["IN_PROGRESS"], pdc["INCOMPLETE_FINAL"], pdc["STATUS"]) == (1, 1, EL.PRIOR_DAY_INCOMPLETE)
    assert pdc["INCOMPLETE_FINAL_GAME_IDS"] == ["G-inc"]


# ============================================================================ the walk-forward freshness gate
def _version(root: Path, sv: str, created: str, source: str) -> None:
    d = root / "state" / "versions" / sv
    d.mkdir(parents=True)
    (d / "state_manifest.json").write_text(json.dumps({"state_version": sv, "created_at": created, "source_cutoff": source}))


def _synclog(root: Path, rows: list[tuple]) -> None:
    p = root / "sync" / "sync_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"status": s, "state_version": v, "source_cutoff": c, "finished_at": f}) + "\n"
                         for s, v, c, f in rows))


def _life(tmp_path) -> L.Life:
    return L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=tmp_path / "wf",
                  clock=lambda: U("2026-11-12T12:00Z"))


def test_G_stale_state_defers_refit_and_never_calls_runner(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S0522", "2026-11-12T05:22:51Z", "2026-11-12T05:20:26Z")
    _synclog(tmp_path / "prod", [("PROMOTED", "S0522", "2026-11-12T05:20:26Z", "2026-11-12T05:22:53Z")])
    calls = []
    r = L.walk_forward(life, DAY, runner=lambda *a, **k: calls.append(1) or {}, warm_runner=lambda *a, **k: {})
    assert calls == [] and r["DAILY_REFIT_STATUS"] == L.DEFERRED_STATE_REFRESH_FAILED
    assert r["DAILY_MODEL_PROMOTION"] == "NOT_ATTEMPTED" and "STATE_STALE_AT_CUTOFF" in r["reason"]
    assert r["state_freshness"]["STATE_AGE_MIN"] == pytest.approx(339.6, abs=0.1)
    assert L.DEFERRED_STATE_REFRESH_FAILED not in L.RETRYABLE_DEFERRALS      # no later refresh can change the as-of state


def test_I_post_cutoff_version_or_confirmation_never_counts(tmp_path):
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S0522", "2026-11-12T05:22:51Z", "2026-11-12T05:20:26Z")
    _version(tmp_path / "prod", "S1133", "2026-11-12T11:33:29Z", "2026-11-12T11:30:34Z")    # after the cutoff
    _synclog(tmp_path / "prod", [("PROMOTED", "S0522", "2026-11-12T05:20:26Z", "2026-11-12T05:22:53Z"),
                                 ("UNCHANGED", "S0522", "2026-11-12T10:58:00Z", "2026-11-12T11:02:00Z"),  # finished late
                                 ("PROMOTED", "S1133", "2026-11-12T11:30:34Z", "2026-11-12T11:33:32Z")])
    f = L.state_freshness(life, DAY)
    assert f["state_version"] == "S0522" and f["STATE_FRESH"] is False


def test_J_refit_gets_the_refreshed_state(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S0522", "2026-11-12T05:22:51Z", "2026-11-12T05:20:26Z")
    _version(tmp_path / "prod", "S1022", "2026-11-12T10:22:40Z", "2026-11-12T10:20:30Z")    # pre-refit refresh
    _synclog(tmp_path / "prod", [("PROMOTED", "S0522", "2026-11-12T05:20:26Z", "2026-11-12T05:22:53Z"),
                                 ("PROMOTED", "S1022", "2026-11-12T10:20:30Z", "2026-11-12T10:22:41Z"),
                                 ("UNCHANGED", "S1022", "2026-11-12T10:40:30Z", "2026-11-12T10:42:41Z")])
    f = L.state_freshness(life, DAY)
    assert f["state_version"] == "S1022" and f["STATE_AGE_MIN"] == pytest.approx(19.5, abs=0.1) and f["STATE_FRESH"]
    assert EL.state_asof(life.state_root, CUT)["state_version"] == "S1022"           # what training_state reads
    calls = []
    r = L.walk_forward(life, DAY, warm_runner=lambda *a, **k: {"status": "COMPLETE"},
                       runner=lambda *a, **k: calls.append(1) or {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED"})
    assert calls == [1] and r["DAILY_REFIT_STATUS"] == "PASS" and r["state_freshness"]["STATE_FRESH"]
