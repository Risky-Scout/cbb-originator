"""Morning data -> state -> refit reliability (2026-11-12 follow-up): pre-refit refresh retries and per-game request
failure isolation.

    1-8   PRE_REFIT_REFRESH: first attempt at the lead-window start; a failed attempt is retried at
          min(last attempt + 20 min, cutoff - 10 min) on its own (no completion backlog needed), carried in DUE.json and
          honoured by due_check; the chain stops on success, on the cutoff, or when no authorized attempt is left
    9-17  a completed-stage request failure (HTTP 500, timeout, non-retryable 400) is recorded per game and isolated:
          every other game is ingested and promoted, the failed game stays incomplete and re-enters COMPLETION_BACKLOG
          (retryable) or leaves it (non-retryable); nothing already complete is fetched again
    18-23 the morning gate: a refreshed (or retried-then-refreshed) state trains, an un-refreshable stale state defers
          explicitly, a genuinely incomplete final / an in-progress game is deferred without blocking the refit, and
          the completion loop stops once the backlog is clear
Fixtures only (synthetic schedules / states, the feed_fixtures fake CBBD); no network, no credential."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import requests

from cbb_platform.prod import (cbbd_acquire as A, due_check as DC, feed_fixtures as FF, fixtures as FX, lifecycle as L,
                               ops_store as OS, shadow_tick as SH, state as ST, sync as Y)
from cbb_platform.walkforward import eligibility as EL

DAY = date(2026, 11, 12)
CUT = pd.Timestamp("2026-11-12T11:00:00Z")                    # 06:00 ET (EST): the unchanged training cutoff
U = lambda s: pd.Timestamp(s)


# ============================================================================ plan(): pre-refit retry chain
def _sched(rows):
    cols = ["canonical_game_id", "scheduled_tip_utc", "cutoff", "knowable_at", "game_universe", "home_points",
            "home_team_id", "away_team_id", "status", "ingested", "not_played"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("scheduled_tip_utc", "cutoff", "knowable_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def _game(gid, tip, status, points, ingested):
    t = U(tip)
    return {"canonical_game_id": gid, "scheduled_tip_utc": t, "cutoff": t - pd.Timedelta(hours=1),
            "knowable_at": t + pd.Timedelta(hours=4), "game_universe": "DI_VS_DI", "home_points": points,
            "home_team_id": f"T-{gid}-h", "away_team_id": f"T-{gid}-a", "status": status, "ingested": ingested,
            "not_played": False}


def _ctx(tmp_path, attempts):
    """attempts: [(finished_at, status)] of the sync log (the last one is `last_try`)."""
    ctx = SH.Ctx(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod")
    ctx.sync_log.parent.mkdir(parents=True, exist_ok=True)
    ctx.sync_log.write_text("".join(json.dumps({"finished_at": U(f).isoformat(), "status": s}) + "\n" for f, s in attempts))
    return ctx


def _plan(tmp_path, monkeypatch, now, last_ok, attempts, rows=()):
    ctx = _ctx(tmp_path, attempts)
    monkeypatch.setattr(SH, "schedule", lambda st: _sched(list(rows)))
    st = {"pointer": {"last_sync_source_cutoff": U(last_ok).isoformat()}}
    return SH.plan(ctx, st, U(now)), ctx, st


def _syncs_before_cut(p):
    return sorted(t for k, t in p["next"] if k == "sync" and t < CUT)


OVERNIGHT = [("2026-11-12T05:22:53Z", "PROMOTED")]


def test_01_successful_first_attempt_schedules_no_retry(tmp_path, monkeypatch):
    p, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:30Z", "2026-11-12T10:20:30Z",
                    OVERNIGHT + [("2026-11-12T10:22:41Z", "PROMOTED")])
    assert p["pre_refit"]["status"] == "REFRESHED" and p["pre_refit"]["due_at"] is None
    assert "PRE_REFIT_REFRESH" not in p["sync_why"] and _syncs_before_cut(p) == []


def test_02_failed_attempt_retries_exactly_20_minutes_later(tmp_path, monkeypatch):
    p, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:20Z", "2026-11-12T05:20:26Z",
                    OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED")])
    assert p["pre_refit"]["status"] == "RETRY" and p["pre_refit"]["due_at"] == U("2026-11-12T10:37:10Z")
    assert "PRE_REFIT_REFRESH" not in p["sync_why"] and _syncs_before_cut(p) == [U("2026-11-12T10:37:10Z")]
    p2, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:40Z", "2026-11-12T05:20:26Z",
                     OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED")])
    assert p2["sync_why"] == ["PRE_REFIT_REFRESH"]                           # the retry fires at the next tick


def test_03_fail_then_success_stops_the_chain(tmp_path, monkeypatch):
    p, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:50Z", "2026-11-12T10:38:40Z",
                    OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED"), ("2026-11-12T10:41:05Z", "PROMOTED")])
    assert p["pre_refit"]["status"] == "REFRESHED" and p["pre_refit"]["due_at"] is None
    assert "PRE_REFIT_REFRESH" not in p["sync_why"] and _syncs_before_cut(p) == []


def test_04_second_failure_retries_only_while_safely_before_cutoff(tmp_path, monkeypatch):
    two = OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED"), ("2026-11-12T10:41:05Z", "FAILED")]
    p, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:42Z", "2026-11-12T05:20:26Z", two)
    assert p["pre_refit"]["due_at"] == U("2026-11-12T10:50Z")                # min(11:01:05, cutoff - 10 min)
    three = two + [("2026-11-12T10:52:30Z", "FAILED")]
    p3, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:53Z", "2026-11-12T05:20:26Z", three)
    assert p3["pre_refit"]["status"] == "RETRIES_EXHAUSTED" and p3["pre_refit"]["due_at"] is None
    assert "PRE_REFIT_REFRESH" not in p3["sync_why"] and _syncs_before_cut(p3) == []


def test_05_no_retry_after_the_cutoff_for_that_cutoff(tmp_path, monkeypatch):
    for now in ("2026-11-12T11:00Z", "2026-11-12T11:10Z"):
        p, _, _ = _plan(tmp_path, monkeypatch, now, "2026-11-12T05:20:26Z",
                        OVERNIGHT + [("2026-11-12T10:52:30Z", "FAILED")])
        assert "PRE_REFIT_REFRESH" not in p["sync_why"]
        assert p["pre_refit"]["cutoff"] == U("2026-11-13T11:00Z")            # only the NEXT cutoff's window
        assert p["pre_refit"]["due_at"] == U("2026-11-13T10:15Z")


def test_06_failed_pre_refit_retries_without_any_completion_backlog(tmp_path, monkeypatch):
    rows = [_game("G-done", "2026-11-12T00:00Z", "final", 70.0, True)]       # nothing incomplete: no backlog
    p, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:40Z", "2026-11-12T05:20:26Z",
                    OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED")], rows)
    assert p["sync_why"] == ["PRE_REFIT_REFRESH"]                            # PRE_REFIT alone is sufficient
    early, _, _ = _plan(tmp_path, monkeypatch, "2026-11-12T10:20Z", "2026-11-12T05:20:26Z",
                        OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED")], rows)
    assert _syncs_before_cut(early) == [U("2026-11-12T10:37:10Z")]           # and its wake-up exists without one


def test_07_due_json_carries_the_retry(tmp_path, monkeypatch):
    p, ctx, st = _plan(tmp_path, monkeypatch, "2026-11-12T10:20Z", "2026-11-12T05:20:26Z",
                       OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED")])
    d = SH.due_document(p, st, ctx)
    assert d["due_now"] is False and d["next_due_at"] == U("2026-11-12T10:37:10Z").isoformat()
    assert d["next_by_kind"]["sync"] == U("2026-11-12T10:37:10Z").isoformat()
    assert d["pre_refit_refresh"]["status"] == "RETRY" and d["pre_refit_refresh"]["due_at"] == d["next_due_at"]


def test_08_due_check_wakes_at_the_retry(tmp_path, monkeypatch):
    p, ctx, st = _plan(tmp_path, monkeypatch, "2026-11-12T10:20Z", "2026-11-12T05:20:26Z",
                       OVERNIGHT + [("2026-11-12T10:17:10Z", "FAILED")])
    OS.write_json(ctx.ops("DUE.json"), SH.due_document(p, st, ctx))
    assert DC.check(ctx.ops_root, U("2026-11-12T10:30Z").to_pydatetime())[0] is False
    assert DC.check(ctx.ops_root, U("2026-11-12T10:40Z").to_pydatetime())[0] is True
    # the pre-refit entry wakes on its own even when nothing else in DUE.json is due
    d = json.loads(ctx.ops("DUE.json").read_text())
    d.update(next_due_at="2026-11-12T23:00:00+00:00", next_by_kind={"report": "2026-11-13T12:00:00+00:00"})
    ctx.ops("DUE.json").write_text(json.dumps(d))
    due, why = DC.check(ctx.ops_root, U("2026-11-12T10:40Z").to_pydatetime())
    assert due and why.startswith("PRE_REFIT_REFRESH_DUE")
    assert DC.check(ctx.ops_root, U("2026-11-12T11:00Z").to_pydatetime())[1] != why     # never after its cutoff


# ============================================================================ per-game failure isolation
FOUR = ("D", "A", "B", "C")                                      # D 11-05; A, B, C 11-11 00:30Z (D-I vs D-I)


def four_final_payloads() -> dict:
    out = FF.payloads("before")
    bxs = {t: FF.box_payloads(t) for t in FOUR}
    games = [FF._game(t, "final", bxs[t]["hp"], bxs[t]["ap"]) if t in FOUR else FF._game(t) for t in FF.GAMES]
    for ep, p in Y.plan_schedule():
        out[A.request_key(ep, p)] = games if p["startDateRange"] == "2026-11-01" else []
    for tags, (a, b) in ((("D",), ("2026-11-04", "2026-11-06")), (("A", "B", "C"), ("2026-11-10", "2026-11-12"))):
        for ep, part in (("games/teams", "teams"), ("games/players", "players")):
            out[A.request_key(ep, {"season": 2027, "startDateRange": a, "endDateRange": b})] = \
                [r for t in tags for r in bxs[t][part]]
    season, seen = [], set()
    for t in FOUR:
        for r in bxs[t]["season"]:
            k = json.dumps(r, sort_keys=True)
            if k not in seen:
                seen.add(k)
                season.append(r)
    out[A.request_key("stats/player/season", {"season": 2027})] = season
    for t in FOUR:
        out[A.request_key("substitutions/game", {"gameId": FF.GAMES[t][0]})] = bxs[t]["subs"]
    return out


class FirstSubsFails(FF.FakeCBBD):
    """Fails every attempt of the FIRST substitutions/game request it sees (HTTP status, or a timeout); serves the rest."""

    def __init__(self, bodies, how):
        super().__init__(bodies)
        self.how, self.victim = how, None

    def __call__(self, url, params=None, headers=None, timeout=None):
        key = self._key(url, dict(params or {}))
        if key.startswith("substitutions/game|") and self.victim in (None, key):
            self.victim = key
            if self.how == "timeout":
                self.calls.append(key)
                raise requests.Timeout("fixture timeout")
            self.fail[key] = self.how
        return super().__call__(url, params=params, headers=headers, timeout=timeout)


def _world(root: Path, how) -> dict:
    w = root
    hist = FX.synthetic_history(w / "history.duckdb")
    ctx = SH.Ctx(ops_root=w / "ops", prod_root=w / "prod")
    kw = dict(history_db=hist, state_root=ctx.state_root, cache_root=ctx.cache_raw, ledger_path=ctx.cbbd_ledger,
              sync_log=ctx.sync_log, work_root=w / "work", credential=lambda: FF.DUMMY_CREDENTIAL,
              provider_kind="LIVE", sleep=lambda s: None)
    Y.run(clock=FF.clock_at("2026-11-01T12:00Z"), http_get=FF.FakeCBBD(FF.payloads("before")), **kw)
    http = FirstSubsFails(four_final_payloads(), how)
    out = {"ctx": ctx, "kw": kw, "http": http}
    out["s1"] = Y.run(clock=FF.clock_at("2026-11-11T06:00Z"), http_get=http, **kw)
    out["calls1"] = list(http.calls)
    out["victim"] = http.victim
    out["victim_game"] = FX.cid("G", "game", json.loads(http.victim.split("|", 1)[1])["gameId"])
    out["st1"] = ST.current(ctx.state_root)
    out["done1"] = Y._complete_in_state(out["st1"])
    out["plan1"] = SH.plan(ctx, out["st1"], U("2026-11-11T06:30Z"))
    return out


@pytest.fixture(scope="module")
def http500(tmp_path_factory):
    out = _world(tmp_path_factory.mktemp("fail500"), 500)
    http = out["http"]
    http.how, http.fail = None, {}                                           # the provider recovers
    http.victim = "--none--"
    n = len(http.calls)
    out["s2"] = Y.run(clock=FF.clock_at("2026-11-11T06:30Z"), http_get=http, **out["kw"])
    out["calls2"] = http.calls[n:]
    out["st2"] = ST.current(out["ctx"].state_root)
    out["plan2"] = SH.plan(out["ctx"], out["st2"], U("2026-11-11T07:00Z"))
    return out


def test_09_one_failed_request_others_persist(http500):
    s1 = http500["s1"]
    assert s1["status"] == "PROMOTED" and s1["outcome"] == "PARTIAL_SUCCESS"
    assert s1["completed_games_new"] == 4 and s1["completed_games_ingested"] == 3
    assert {FF.gid(t) for t in FOUR} - {http500["victim_game"]} <= http500["done1"]


def test_10_failure_is_recorded_per_game(http500):
    s1 = http500["s1"]
    (f,) = s1["completed_request_failures"]
    assert f["GAME_IDS"] == [http500["victim_game"]] and f["REQUEST_KIND"] == "substitutions/game"
    assert f["REQUEST_KEY"] == http500["victim"] and f["HTTP_STATUS_OR_EXCEPTION"] == "HTTP 500"
    assert f["RETRYABLE"] is True and f["ERROR_CLASSIFICATION"] == "PROVIDER_5XX"
    assert U(f["NEXT_RETRY_AT"]) - U(f["ATTEMPT_TIME"]) == pd.Timedelta(minutes=20)
    r = s1["completed_receipt"]
    assert (r["REQUESTS_FAILED"], r["GAMES_COMPLETED"], r["GAMES_STILL_INCOMPLETE"]) == (1, 3, 1)
    assert r["REQUESTS_ATTEMPTED"] == r["REQUESTS_SUCCEEDED"] + r["REQUESTS_FAILED"]
    logged = json.loads(http500["ctx"].sync_log.read_text().splitlines()[1])           # durable in the sync log
    assert logged["completed_request_failures"] == s1["completed_request_failures"]


def test_11_failed_game_stays_incomplete(http500):
    assert http500["victim_game"] not in http500["done1"]
    sch = SH.schedule(http500["st1"]).set_index("canonical_game_id")
    assert not sch.loc[http500["victim_game"], "ingested"] and pd.isna(sch.loc[http500["victim_game"], "home_points"])


def test_12_failed_final_game_enters_completion_backlog(http500):
    assert "COMPLETION_BACKLOG" in http500["plan1"]["sync_why"]


def test_13_failure_does_not_abort_later_games(http500):
    subs = [k for k in http500["calls1"] if k.startswith("substitutions/game|")]
    assert subs[0] == http500["victim"]                                      # the FIRST game request failed ...
    assert len(set(subs) - {http500["victim"]}) == 3                         # ... and all later ones were still made


def test_14_retry_refetches_only_the_failed_game(http500):
    assert [k for k in http500["calls2"] if k.split("|")[0] in ("games/teams", "games/players", "substitutions/game")] \
        == [http500["victim"]]
    assert http500["s2"]["completed_games_ingested"] == 1 and http500["s2"]["outcome"] == "SUCCESS"


def test_15_one_http_500_does_not_fail_the_sync(http500):
    assert http500["s1"]["status"] != "FAILED" and http500["s1"]["state_version"]


def test_16_one_timeout_does_not_fail_the_sync(tmp_path):
    out = _world(tmp_path, "timeout")
    (f,) = out["s1"]["completed_request_failures"]
    assert out["s1"]["status"] == "PROMOTED" and out["s1"]["completed_games_ingested"] == 3
    assert f["ERROR_CLASSIFICATION"] == "NETWORK" and f["RETRYABLE"] is True and "Timeout" in f["HTTP_STATUS_OR_EXCEPTION"]
    assert "COMPLETION_BACKLOG" in out["plan1"]["sync_why"]


def test_17_nonretryable_request_is_recorded_and_isolated(tmp_path):
    out = _world(tmp_path, 400)
    (f,) = out["s1"]["completed_request_failures"]
    assert out["s1"]["status"] == "PROMOTED" and out["s1"]["completed_games_ingested"] == 3
    assert f["RETRYABLE"] is False and f["ERROR_CLASSIFICATION"] == "INVALID_REQUEST" and f["NEXT_RETRY_AT"] is None
    assert "COMPLETION_BACKLOG" not in out["plan1"]["sync_why"]              # not retried every 20 minutes
    n = len(out["http"].calls)
    s2 = Y.run(clock=FF.clock_at("2026-11-11T07:00Z"), http_get=out["http"], **out["kw"])
    assert out["victim"] not in out["http"].calls[n:]                        # the exact request is not re-sent ...
    (g,) = s2["completed_request_failures"]
    assert g["ERROR_CLASSIFICATION"] == Y.SKIPPED_NONRETRYABLE and g["GAME_IDS"] == [out["victim_game"]]   # ... visibly
    assert out["victim_game"] not in Y._complete_in_state(ST.current(out["ctx"].state_root))


# ============================================================================ the morning gate end to end
def _version(root: Path, sv: str, created: str, source: str) -> None:
    d = root / "state" / "versions" / sv
    d.mkdir(parents=True)
    (d / "state_manifest.json").write_text(json.dumps({"state_version": sv, "created_at": created, "source_cutoff": source}))


def _log(root: Path, rows: list[tuple]) -> None:
    p = root / "sync" / "sync_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"status": s, "state_version": v, "source_cutoff": c, "finished_at": f}) + "\n"
                         for s, v, c, f in rows))


def _life(tmp_path) -> L.Life:
    return L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=tmp_path / "wf",
                  clock=lambda: U("2026-11-12T12:00Z"))


def _refit(life):
    calls = []
    r = L.walk_forward(life, DAY, warm_runner=lambda *a, **k: {"status": "COMPLETE"},
                       runner=lambda *a, **k: calls.append(1) or {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED"})
    return r, calls


STALE = ("PROMOTED", "S0522", "2026-11-12T05:20:26Z", "2026-11-12T05:22:53Z")


def _pre_refit_tick(tmp_path, now, rows):
    """What plan() decides at `now` given the sync log `rows` (pointer = latest PROMOTED/UNCHANGED source_cutoff)."""
    ok = [U(c) for s, _, c, _ in rows if s in ("PROMOTED", "UNCHANGED")]
    last_try = U(rows[-1][3])
    return SH.pre_refit_due(U(now), max(ok), last_try)


def test_18_stale_state_then_successful_pre_refit_refresh_trains(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    assert _pre_refit_tick(tmp_path, "2026-11-12T10:20Z", [STALE])["due_at"] == U("2026-11-12T10:15Z")
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S0522", "2026-11-12T05:22:51Z", "2026-11-12T05:20:26Z")
    _version(tmp_path / "prod", "S1022", "2026-11-12T10:22:40Z", "2026-11-12T10:20:30Z")
    _log(tmp_path / "prod", [STALE, ("PROMOTED", "S1022", "2026-11-12T10:20:30Z", "2026-11-12T10:22:41Z")])
    r, calls = _refit(life)
    assert calls == [1] and r["DAILY_REFIT_STATUS"] == "PASS" and r["state_freshness"]["STATE_AGE_MIN"] <= 60


def test_19_stale_state_failed_refresh_then_retry_success_trains(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    fail = ("FAILED", None, None, "2026-11-12T10:21:30Z")
    rows = [STALE, fail]
    assert _pre_refit_tick(tmp_path, "2026-11-12T10:42Z", rows)["due_at"] == U("2026-11-12T10:41:30Z")   # retry due
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S0522", "2026-11-12T05:22:51Z", "2026-11-12T05:20:26Z")
    _version(tmp_path / "prod", "S1042", "2026-11-12T10:42:40Z", "2026-11-12T10:40:30Z")
    _log(tmp_path / "prod", rows + [("PROMOTED", "S1042", "2026-11-12T10:40:30Z", "2026-11-12T10:42:41Z")])
    r, calls = _refit(life)
    assert calls == [1] and r["DAILY_REFIT_STATUS"] == "PASS" and r["state_freshness"]["state_version"] == "S1042"


def test_20_all_authorized_refresh_attempts_fail_defers_explicitly(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    rows = [STALE] + [("FAILED", None, None, t) for t in
                      ("2026-11-12T10:21:30Z", "2026-11-12T10:41:30Z", "2026-11-12T10:52:30Z")]
    assert _pre_refit_tick(tmp_path, "2026-11-12T10:53Z", rows)["status"] == "RETRIES_EXHAUSTED"
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S0522", "2026-11-12T05:22:51Z", "2026-11-12T05:20:26Z")
    _log(tmp_path / "prod", rows)
    r, calls = _refit(life)
    assert calls == [] and r["DAILY_REFIT_STATUS"] == L.DEFERRED_STATE_REFRESH_FAILED      # stale never trains
    assert r["DAILY_MODEL_PROMOTION"] == "NOT_ATTEMPTED" and "STATE_STALE_AT_CUTOFF" in r["reason"]


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


def _fresh_life(tmp_path):
    life = _life(tmp_path)
    _version(tmp_path / "prod", "S1022", "2026-11-12T10:22:40Z", "2026-11-12T10:20:30Z")
    _log(tmp_path / "prod", [("PROMOTED", "S1022", "2026-11-12T10:20:30Z", "2026-11-12T10:22:41Z")])
    return life


def test_21_fresh_state_with_one_incomplete_final_defers_that_game_and_trains(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    st = _state_db(tmp_path / "inc.duckdb", [{"id": "G-ok", "tip": "2026-11-12T00:00Z", "status": "final"},
                                             {"id": "G-inc", "tip": "2026-11-12T00:00Z", "status": "final", "result": False}])
    el = EL.evaluate(st, CUT, {"G-ok": {"available_at": "2026-11-12T10:22Z", "state_version": "S1022"}}).set_index("game_id")
    assert el.loc["G-ok", "decision"] == "INCLUDED"
    assert tuple(el.loc["G-inc", ["decision", "reason"]]) == ("DEFERRED", "FINAL_DATA_INCOMPLETE_AT_CUTOFF")
    pdc = EL.prior_day_completeness(el.reset_index(), DAY)
    assert (pdc["INCOMPLETE_FINAL"], pdc["INCLUDED"], pdc["STATUS"]) == (1, 1, EL.PRIOR_DAY_INCOMPLETE)
    r, calls = _refit(_fresh_life(tmp_path))
    assert calls == [1] and r["state_freshness"]["STATE_FRESH"]              # the incomplete final does not block


def test_22_in_progress_prior_day_game_is_deferred_and_does_not_block(tmp_path, monkeypatch):
    monkeypatch.delenv("CBB_HEAVY_LOCK", raising=False)
    st = _state_db(tmp_path / "live.duckdb", [{"id": "G-ok", "tip": "2026-11-12T00:00Z", "status": "final"},
                                              {"id": "G-live", "tip": "2026-11-12T01:00Z", "status": "in_progress"}])
    el = EL.evaluate(st, CUT, {"G-ok": {"available_at": "2026-11-12T10:22Z", "state_version": "S1022"}}).set_index("game_id")
    assert tuple(el.loc["G-live", ["decision", "reason"]]) == ("DEFERRED", "NOT_FINAL_AT_CUTOFF")
    pdc = EL.prior_day_completeness(el.reset_index(), DAY)
    assert (pdc["IN_PROGRESS"], pdc["INCOMPLETE_FINAL"], pdc["STATUS"]) == (1, 0, "COMPLETE")
    r, calls = _refit(_fresh_life(tmp_path))
    assert calls == [1] and r["DAILY_REFIT_STATUS"] == "PASS"


def test_23_completion_retries_stop_once_the_backlog_is_clear(http500):
    p = http500["plan2"]
    assert "COMPLETION_BACKLOG" not in p["sync_why"]
    assert not [t for k, t in p["next"] if k == "sync" and t < U("2026-11-11T08:00Z")]   # no 20-minute retry entry
