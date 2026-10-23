"""Focused tests of the prospective 2026-27 shadow tick (prod/shadow_tick.py), its durable evidence store
(prod/ops_store.py), the cheap scheduler gate (prod/due_check.py) and the prospective monitors (prod/prospective_monitor.py).

CI-safe: synthetic history, labeled CBBD-format and Odds API v4-format fixtures behind fake HTTP layers, a dummy
encryption key, temporary roots. No network, no real credential, no model change. One module-scoped lifecycle:

    11-01 12:00  bootstrap tick (first sync, free events listing)      -> immediate re-tick: nothing at all happens
    11-05 06:00  game D final + knowable                               -> ingested; FIRST_LIVE_COMPLETED_GAME_INGESTION
    11-10 23:00  A/B/C inside [cutoff - 40 min, cutoff)                 -> ONE paid validation (PASS), ONE quote snapshot,
                                                                          ONE bank per game, registry PREDICTED x3
    11-10 23:05  re-tick                                               -> nothing (no duplicate prediction / quote)
    (restore)    fresh prod root from the encrypted working set        -> same state, still nothing to do
    11-11 05:00  game A final                                          -> ingested + settled (DNP = NO_ACTION)
    11-11 05:01  re-tick                                               -> no duplicate settlement
    11-12 04:30  game T due (no event listed)                          -> scored; no second validation, no paid call
    11-12 12:30  reports                                               -> daily + cumulative reports
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from cbb_platform import ops_persistence as OP
from cbb_platform.prod import (cbbd_acquire as A, config as PC, due_check as DC, feed_fixtures as FF, fixtures as FX,
                               odds_fixtures as OX, ops_store as OS, prospective_monitor as PM, run as RN,
                               shadow_tick as TK, state as ST, sync as Y)

KEY = "fixture-ops-key-not-a-real-secret"
SIMS = 1500


class Clock:
    def __init__(self, t):
        self.t = pd.Timestamp(t)

    def __call__(self):
        return self.t


def _payloads_final_a() -> dict:
    out = FF.payloads("after")
    bd, ba = FF.box_payloads("D"), FF.box_payloads("A")
    games = [FF._game(t) for t in FF.GAMES]
    games[0] = FF._game("D", "final", bd["hp"], bd["ap"])
    games[1] = FF._game("A", "final", ba["hp"], ba["ap"])
    for ep, p in Y.plan_schedule():
        out[A.request_key(ep, p)] = games if p["startDateRange"] == "2026-11-01" else []
    rng = {"season": 2027, "startDateRange": "2026-11-10", "endDateRange": "2026-11-12"}
    out[A.request_key("games/teams", rng)] = ba["teams"]
    out[A.request_key("games/players", rng)] = ba["players"]
    out[A.request_key("stats/player/season", {"season": 2027})] = list({r["athleteId"]: r for r in bd["season"] + ba["season"]}.values())
    out[A.request_key("substitutions/game", {"gameId": FF.GAMES["A"][0]})] = ba["subs"]
    return out


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _ctx(w: Path, hist: Path, clock, cbbd, odds, **kw) -> TK.Ctx:
    return TK.Ctx(ops_root=w / "ops", prod_root=w / "prod", history_db=hist, clock=clock, cbbd_http=cbbd, odds_http=odds,
                  cbbd_credential=lambda: FF.DUMMY_CREDENTIAL, odds_credential=lambda: OX.DUMMY_ODDS_CREDENTIAL,
                  sims=SIMS, **kw)


def _odds(names: dict, tn, sl, a: str, b: str, quoted: list, bench=None):
    tip = "2026-11-11T00:30:00Z"
    ev_a = OX.event("evt-A", tn[sl.loc[a, "home_team_id"]], tn[sl.loc[a, "away_team_id"]], tip)
    ev_b = OX.event("evt-B", tn[sl.loc[b, "home_team_id"]], "Unknown Visitors", tip)
    upd = "2026-11-10T22:55:00Z"
    p0, p1 = quoted
    pts = OX.ou(p0, 10.5, -110, -110, link=True) + OX.ou(p1, 6.5, 120, -150)
    if bench:
        pts += OX.ou(bench, 0.5, 300, -500)
    odds_a = OX.event_odds(ev_a, [
        OX.book("draftkings", "DraftKings", [OX.market("player_points", pts, upd, link=True),
                                             OX.market("player_rebounds", OX.ou(p0, 3.5, -120, 100), upd)], link=True),
        OX.book("fanduel", "FanDuel", [OX.market("player_points", OX.ou(p0, 10.5, 105, -135), upd)])])
    return OX.FakeOdds([ev_a, ev_b], {"evt-A": odds_a})


@pytest.fixture(scope="module")
def life(tmp_path_factory):
    old = os.environ.get(PC.OPS_KEY_ENV)
    os.environ[PC.OPS_KEY_ENV] = KEY
    w = tmp_path_factory.mktemp("tick")
    hist = FX.synthetic_history(w / "history.duckdb")
    (w / "ops").mkdir()
    subprocess.run(["git", "init", "-q", str(w / "ops")], check=True)
    subprocess.run(["git", "-C", str(w / "ops"), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(w / "ops"), "config", "user.name", "fixture"], check=True)
    clock = Clock("2026-11-01T12:00Z")
    cbbd = FF.FakeCBBD(FF.payloads("before"))
    odds = OX.FakeOdds([])
    assets = OP.encrypted_dir_assets(w / "assets")             # the same AES-256 path the private release uses
    ctx = _ctx(w, hist, clock, cbbd, odds, assets=assets, persist=True,
               commit=lambda root, msg: OP.git_commit_push(root, msg, push=False))
    out = {"w": w, "hist": hist, "ctx": ctx, "clock": clock, "cbbd": cbbd, "assets": assets}
    # ---------------------------------------------------------------- 11-01 bootstrap + idempotent re-tick
    out["t1"] = TK.tick(ctx)
    out["digest1"] = (_tree_digest(w / "ops"), _tree_digest(w / "prod" / "state"), len(cbbd.calls), len(odds.calls))
    out["t1b"] = TK.tick(ctx)
    out["digest1b"] = (_tree_digest(w / "ops"), _tree_digest(w / "prod" / "state"), len(cbbd.calls), len(odds.calls))
    out["git_after_idle"] = subprocess.run(["git", "-C", str(w / "ops"), "rev-list", "--count", "HEAD"],
                                           capture_output=True, text=True).stdout.strip()
    # ---------------------------------------------------------------- 11-05 D final -> first ingestion
    cbbd.bodies = FF.payloads("after")
    clock.t = pd.Timestamp("2026-11-05T06:00Z")
    out["t2"] = TK.tick(ctx)
    # ---------------------------------------------------------------- 11-10 23:00 scoring tick
    st = ST.current(w / "prod" / "state")
    from cbb_platform.prod import slate as SL
    sl = SL.build(st["increment_db"], "2026-11-10T23:00Z", st["manifest"]["schedule_as_of"], 36).set_index("canonical_game_id")
    pre = RN.daily("2026-11-10T23:00Z", state_root=w / "prod" / "state", history_db=hist, ledger_path=w / "pre" / "l.jsonl",
                   run_root=w / "pre", sims=200, sync_log=w / "pre" / "s.jsonl", odds_cache_root=w / "pre" / "nc",
                   odds_collections=None, odds_ledger=w / "pre" / "o.jsonl", odds_record=w / "pre" / "r.json")
    u = pd.read_csv(Path(pre["run_dir"]) / "player_universe.csv")
    a, b = FF.gid("A"), FF.gid("B")
    ok = u[(u["game_id"] == a) & u["in_frozen_pool"].astype(bool) & u["suppression_reason"].isna()].drop_duplicates("canonical_player_id")
    starters = ok[ok["canonical_player_id"].isin([FF.pid(0, j) for j in range(5)])]
    bench = ok[ok["canonical_player_id"].isin([FF.pid(0, j) for j in range(5, 11)])]
    teams, _ = RN._names(hist)
    tn = teams.set_index("canonical_team_id")["display_name"]
    q = [starters.iloc[0]["name"], starters.iloc[1]["name"]]
    out.update({"p0": starters.iloc[0]["canonical_player_id"], "p1": starters.iloc[1]["canonical_player_id"],
                "bench": None if bench.empty else bench.iloc[0]["canonical_player_id"]})
    odds2 = _odds({}, tn, sl, a, b, q, None if bench.empty else bench.iloc[0]["name"])
    ctx.odds_http = odds2
    out["odds"] = odds2
    clock.t = pd.Timestamp("2026-11-10T23:00Z")
    out["t3"] = TK.tick(ctx)
    out["paid_after_t3"] = [c for c in odds2.calls if "/odds" in c[0]]
    out["t3b"] = (TK.tick(ctx), len(cbbd.calls), len(odds2.calls))
    out["calls_after_t3"] = (len(cbbd.calls), len(odds2.calls))
    # ---------------------------------------------------------------- durable restore on a fresh root
    w2 = w / "fresh"
    w2.mkdir()
    shutil.copytree(w / "ops", w2 / "ops")
    c2 = _ctx(w2, hist, Clock("2026-11-10T23:05Z"), FF.FakeCBBD({}), OX.FakeOdds([]), assets=assets, restore=True)
    out["restored"] = TK.tick(c2)
    out["restored_ctx"] = c2
    # ---------------------------------------------------------------- 11-11 05:00 A final -> settle; re-tick
    cbbd.bodies = _payloads_final_a()
    clock.t = pd.Timestamp("2026-11-11T05:00Z")
    out["t4"] = TK.tick(ctx)
    clock.t = pd.Timestamp("2026-11-11T05:01Z")
    out["t4b"] = TK.tick(ctx)
    st4 = ST.current(w / "prod" / "state")
    out["plan_0510"] = TK.plan(ctx, st4, pd.Timestamp("2026-11-11T05:10Z"))
    out["plan_0530"] = TK.plan(ctx, st4, pd.Timestamp("2026-11-11T05:30Z"))
    out["sched_t4"] = TK.schedule(st4).set_index("canonical_game_id")
    # ---------------------------------------------------------------- 11-12 04:30 game T due; 12:30 reports
    clock.t = pd.Timestamp("2026-11-12T04:30Z")
    n_paid = len([c for c in odds2.calls if "/odds" in c[0]])
    out["t5"] = TK.tick(ctx)
    out["paid_delta_t5"] = len([c for c in odds2.calls if "/odds" in c[0]]) - n_paid
    clock.t = pd.Timestamp("2026-11-12T12:30Z")
    out["t6"] = TK.tick(ctx)
    yield out
    if old is None:
        os.environ.pop(PC.OPS_KEY_ENV, None)
    else:
        os.environ[PC.OPS_KEY_ENV] = old


def _reg(life) -> pd.DataFrame:
    return pd.DataFrame(OS.read_jsonl(life["w"] / "ops" / "registry" / "games.jsonl"))


# ============================================================================ gate, bootstrap, idempotence
def test_frozen_model_hash_gate_blocks_everything(life, tmp_path):
    bad = {k: PC.MODEL_DIR.parent.parent / v[0] for k, v in PC.ARTIFACTS.items()}
    t = tmp_path / "m.json"
    t.write_text(Path(bad["marginal"]).read_text() + " ")
    bad["marginal"] = t
    cbbd, odds = FF.FakeCBBD(FF.payloads("before")), OX.FakeOdds([])
    ctx = _ctx(tmp_path, life["hist"], Clock("2026-11-01T12:00Z"), cbbd, odds, artifact_paths=bad)
    r = TK.tick(ctx)
    assert r["frozen_model_hash_gate"] == "FAIL" and r["status"] == "BLOCKED" and r["reason"] == "ARTIFACT_HASH_MISMATCH"
    assert cbbd.calls == [] and odds.calls == [] and not (tmp_path / "prod" / "state" / "CURRENT.json").exists()
    assert life["t1"]["frozen_model_hash_gate"] == "PASS"


def test_bootstrap_then_idempotent_tick(life):
    t1, t1b = life["t1"], life["t1b"]
    assert t1["sync"]["status"] == "PROMOTED" and t1["sync"]["why"] == ["NO_STATE"] and t1["events"]["status"] == "FETCHED"
    assert t1b["did_work"] is False and "sync" not in t1b and "events" not in t1b
    assert life["digest1"] == life["digest1b"]                 # no network call, no file, no state version, no evidence
    assert life["git_after_idle"] == "1"                       # the idle tick made no commit
    assert DC.check(life["w"] / "ops", pd.Timestamp("2026-11-01T12:05Z").to_pydatetime())[0] is False
    due = json.loads((life["w"] / "ops" / "DUE.json").read_text())
    assert due["due_now"] is False and "generated_at" not in due


def test_due_check_gate(tmp_path):
    from datetime import datetime, timezone
    now = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
    assert DC.check(tmp_path, now) == (True, "NO_DUE_DOCUMENT (bootstrap)")
    (tmp_path / "DUE.json").write_text(json.dumps({"due_now": False, "next_due_at": "2026-11-10T22:50:00+00:00",
                                                   "next_by_kind": {"sync": "2026-11-11T03:00:00+00:00"}}))
    assert DC.check(tmp_path, now)[0] is False
    assert DC.check(tmp_path, datetime(2026, 11, 10, 22, 51, tzinfo=timezone.utc))[0] is True


# ============================================================================ completed games + first live ingestion
def test_first_live_completed_game_ingestion_marker(life):
    t2 = life["t2"]
    assert t2["sync"]["status"] == "PROMOTED" and t2["sync"]["completed_games_ingested"] == 1
    assert t2["sync"]["why"] == ["ROUTINE_REFRESH"] and t2["sync"]["refresh_windows"] == ["2026-11-01"]
    m = json.loads((life["w"] / "ops" / "markers" / "FIRST_LIVE_COMPLETED_GAME_INGESTION.json").read_text())
    assert m["status"] == "PASS" and list(m["games"]) == [FF.gid("D")]
    g = m["games"][FF.gid("D")]
    assert all(g[k] for k in ("game_identity", "final_status", "player_box_scores", "team_box_scores", "substitution_data",
                              "cache", "request_ledger", "atomic_state_update", "future_game_chronology"))
    assert t2["first_live_completed_game_ingestion"] == "PASS"


def test_completed_game_queue_and_cache_reuse(life):
    t4 = life["t4"]
    assert t4["sync"]["completed_games_ingested"] == 1 and t4["sync"]["status"] == "PROMOTED"
    ent = [e for e in OS.read_jsonl(life["w"] / "ops" / "ledgers" / "cbbd_production_requests.jsonl")
           if str(e.get("stage", "")).startswith(t4["sync"]["sync_id"] + ":completed")]
    eps = [e["endpoint"] for e in ent]
    assert eps[0] == "stats/player/season" and "substitutions/game" in eps
    assert all(e["status"] != "FAILED" for e in ent)
    # the season player file cached at the D sync predates A: it was re-fetched, not reused
    assert [e["status"] for e in ent if e["endpoint"] == "stats/player/season"] == ["FETCHED"]
    sch = life["sched_t4"]
    assert sch.loc[FF.gid("A"), "ingested"] and not sch.loc[FF.gid("B"), "ingested"]
    for p in (life["plan_0510"], life["plan_0530"]):              # B / C not final: no result-driven polling
        assert p["sync_due"] is False and p["refresh_windows"] == ["2026-11-01"]   # ... refreshed at the next sync


def test_completed_reuse_rule():
    games = pd.DataFrame({"canonical_game_id": ["G-1", "G-2"], "scheduled_tip_utc": pd.to_datetime(
        ["2026-11-10T19:00Z", "2026-11-11T00:30Z"], utc=True), "tip_time_quality": ["EXACT", "EXACT"],
        "game_date_et": pd.to_datetime(["2026-11-10", "2026-11-10"])})
    sched = pd.DataFrame({"canonical_game_id": ["G-1", "G-2"], "provider_game_id": ["1", "2"]})
    assert Y.completed_reuse_age("substitutions/game", {"gameId": 1}, games, sched, Path("/nonexistent"), "2026-11-11") is None


# ============================================================================ T-60 scoring, validation, quotes
def test_t60_one_prediction_per_game_with_timing(life):
    t3 = life["t3"]
    sc = t3["scoring"]
    assert sc["games_due"] == 3 and sc["games_scored"] == 3 and sc["one_joint_bank_per_game"] is True
    reg = _reg(life)
    pred = reg[reg["status"] == "PREDICTED"]
    assert set(pred["game_id"]) >= {FF.gid("A"), FF.gid("B"), FF.gid("C")} and pred["game_id"].is_unique
    r = pred.set_index("game_id").loc[FF.gid("A")]
    assert r["target_cutoff"].startswith("2026-11-10T23:30") and r["prediction_as_of"].startswith("2026-11-10T23:00")
    assert r["timing_deviation_seconds"] == 1800.0 and r["scheduled_tip"].startswith("2026-11-11T00:30")
    assert r["executed_at"] and pd.Timestamp(r["prediction_as_of"]) < pd.Timestamp(r["target_cutoff"])
    proj = pd.read_parquet(life["w"] / "ops" / "ticks" / t3["tick_id"] / "projections.parquet")
    assert proj.groupby("game_id")["bank_sha256"].nunique().eq(1).all()          # one joint bank shared by all markets
    assert {f"proj_{m}" for m in PC.MARKETS} <= set(proj.columns)
    assert {"scheduled_tip", "target_cutoff", "prediction_as_of", "executed_at", "timing_deviation_seconds"} <= set(proj.columns)


def test_first_real_event_validation_once(life):
    t3 = life["t3"]
    assert t3["quote_validation"]["status"] == "PASS" and t3["quote_validation"]["event_id"] == "evt-A"
    assert (life["w"] / "ops" / "markers" / "QUOTE_INTEGRATION_VALIDATED.json").exists()
    paid = life["paid_after_t3"]
    assert len(paid) == 1 and paid[0][0].endswith("/events/evt-A/odds")          # ONE paid request in total
    att = OS.read_jsonl(life["w"] / "ops" / "markers" / "quote_validation_attempts.jsonl")
    assert len(att) == 1 and att[0]["credits"] == 2
    assert "quote_validation" not in life["t5"] and life["paid_delta_t5"] == 0     # never repeated after PASS
    assert len(OS.read_jsonl(life["w"] / "ops" / "markers" / "quote_validation_attempts.jsonl")) == 1


def test_one_quote_snapshot_per_event_and_no_duplicates(life):
    snaps = OS.read_jsonl(life["w"] / "ops" / "registry" / "quote_snapshots.jsonl")
    assert [s["event_id"] for s in snaps] == ["evt-A"] and snaps[0]["status"] == "CACHE_HIT" and snaps[0]["credits"] == 0
    tick3b, cb, co = life["t3b"]
    assert tick3b["did_work"] is False and (cb, co) == life["calls_after_t3"]
    qs = PM.load(life["w"] / "ops")["quote_sides"]
    assert len(qs) and qs["record_id"].is_unique
    need = {"game_id", "player_id", "market", "min_mu" if "min_mu" in qs else "predicted_minutes", "p_play", "position_group",
            "evidence_bucket", "cold_start_class", "line", "p_over", "p_under", "p_push", "favored_side", "fair_decimal",
            "book_decimal_odds", "model_edge", "sportsbook", "collector_received_at", "market_last_update", "deep_link",
            "response_sha256", "target_cutoff"}
    assert need <= set(qs.columns)
    s = qs[(qs["player_id"] == life["p0"]) & (qs["market"] == "PTS") & (qs["sportsbook"] == "draftkings")].iloc[0]
    assert s["deep_link"].startswith("https://") and s["response_sha256"]


def test_durable_state_recovery_on_fresh_root(life):
    r = life["restored"]
    assert r["restore"]["restored"] is True and r["restore"]["fallback_used"] is False and r["restore"]["restored_files"] > 0
    assert r["did_work"] is False and "scoring" not in r and "sync" not in r
    c2 = life["restored_ctx"]
    assert c2.cbbd_http.calls == [] and c2.odds_http.calls == []
    a = ST.current(life["w"] / "fresh" / "prod" / "state")["manifest"]["state_version"]
    assert a == life["t3"].get("sync", {}).get("state_version") or a == ST.current(life["w"] / "prod" / "state")["manifest"]["state_version"]
    assert OS.verify_chain(life["w"] / "fresh" / "ops")["CHAIN_VALID"]


def test_working_set_fallback_and_refusal(life, tmp_path):
    ptr = json.loads((life["w"] / "ops" / "WORKING_SET.json").read_text())
    assert ptr["current"]["asset"] in life["assets"].names() and ptr["previous"]
    ops = tmp_path / "ops"
    shutil.copytree(life["w"] / "ops", ops)
    shutil.copytree(life["w"] / "assets", tmp_path / "assets", ignore=shutil.ignore_patterns(".scratch"))
    broken = OP.encrypted_dir_assets(tmp_path / "assets")
    (tmp_path / "assets" / (ptr["current"]["asset"] + ".enc")).write_bytes(b"corrupt")
    r = OS.restore_working_set(ops, broken, tmp_path / "prod")
    assert r["restored"] and r["fallback_used"] and r["errors"]
    for n in broken.names():
        (tmp_path / "assets" / (n + ".enc")).write_bytes(b"corrupt")
    with pytest.raises(OS.OpsStoreError):
        OS.restore_working_set(ops, broken, tmp_path / "prod2")
    os.environ[PC.OPS_KEY_ENV], good = "wrong-key", os.environ[PC.OPS_KEY_ENV]
    try:                                                            # a wrong key never yields a state
        with pytest.raises(OS.OpsStoreError):
            OS.restore_working_set(life["w"] / "ops", life["assets"], tmp_path / "prod3")
    finally:
        os.environ[PC.OPS_KEY_ENV] = good


# ============================================================================ settlement
def test_settlement_appended_once_dnp_no_action(life):
    t4, t4b = life["t4"], life["t4b"]
    assert t4["settlement"]["games"] == 1 and t4["settlement"]["quoted_predictions"] > 0
    assert "settlement" not in t4b and t4b["did_work"] is False
    ev = PM.load(life["w"] / "ops")
    st = ev["settlements"]
    assert st["prediction_id"].is_unique and set(st["outcome"]) <= {"OVER", "UNDER", "PUSH", "NO_ACTION"}
    pq = ev["predictions_quoted"].set_index("prediction_id")
    for r in st.itertuples(index=False):                         # the prediction itself was never altered
        assert pq.loc[r.prediction_id, "probability"] == pytest.approx(r.predicted_probability)
    played = st[st["participation"] == "PLAYED"]
    assert len(played) and played["actual"].notna().all() and played["brier"].notna().any()
    if life["bench"] is not None:
        b = st[st["player_id"] == life["bench"]]
        assert len(b) and (b["outcome"] == "NO_ACTION").all() and b["actual"].isna().all()   # DNP never a zero
    act = ev["actuals"]
    assert (act.loc[act["participation"] == "DNP", "act_PTS"].isna()).all()
    assert len(OS.read_jsonl(life["w"] / "ops" / "registry" / "settlements.jsonl")) == 1


# ============================================================================ missed cutoff
def test_missed_cutoff_is_recorded_never_backfilled(life, tmp_path, monkeypatch):
    w = tmp_path
    shutil.copytree(life["w"] / "prod" / "state", w / "prod" / "state")
    (w / "ops").mkdir()
    OS.write_json(w / "ops" / "OPERATION.json", {"operation_started_at": "2026-11-01T00:00:00+00:00"})
    called = []
    monkeypatch.setattr(RN, "daily", lambda *a, **k: called.append(1))
    ctx = _ctx(w, life["hist"], Clock("2026-11-11T00:00Z"), FF.FakeCBBD({}), OX.FakeOdds([]))
    st = ST.current(w / "prod" / "state")
    p = TK.plan(ctx, st, pd.Timestamp("2026-11-11T00:00Z"))
    tick = {"tick_id": "Ktest"}
    TK.step_missed(ctx, p, tick)
    reg = _reg({"w": w})
    assert {FF.gid("A"), FF.gid("B"), FF.gid("C")} <= set(reg.loc[reg["status"] == "MISSED_PREDICTION_CUTOFF", "game_id"])
    assert not called
    p2 = TK.plan(ctx, st, pd.Timestamp("2026-11-11T00:10Z"))
    assert not len(p2["score"]) and not (set(p2["missed"]["canonical_game_id"]) & set(reg["game_id"]))


def test_lifecycle_t_game_scored_without_paid_call(life):
    t5 = life["t5"]
    reg = _reg(life).set_index("game_id")
    assert reg.loc[FF.gid("T"), "status"] in ("PREDICTED", "NOT_SCORED_AT_CUTOFF") if FF.gid("T") in reg.index else True
    assert t5["events"]["status"] == "FETCHED"


# ============================================================================ reports + chain
def test_reports_and_chain(life):
    ops = life["w"] / "ops"
    assert OS.verify_chain(ops)["CHAIN_VALID"]
    cum = json.loads((ops / "reports" / "cumulative.json").read_text())
    for k in ("high_exposure_pts", "early_season", "evidence_buckets_pts", "transfer_cold_start", "center_pts",
              "over_under_sides", "market_benchmark", "operations", "warnings"):
        assert k in cum, k
    assert cum["operations"]["games_predicted"] >= 3 and cum["evidence"]["player_projections"] > 0
    assert (ops / "reports" / "cumulative.md").read_text().startswith("# CBB 2026-27 prospective shadow")
    daily = sorted((ops / "reports" / "daily").glob("*.json"))
    assert "2026-11-10.json" in {p.name for p in daily}
    d = json.loads((ops / "reports" / "daily" / "2026-11-10.json").read_text())
    assert d["date_et"] == "2026-11-10" and d["operations"]["games_predicted"] == 3
    assert "PUBLIC_READY" not in json.dumps(cum) and cum["policy"].startswith("diagnostic only")


# ============================================================================ monitors on synthetic evidence (exact values)
def _evidence(tmp_path) -> Path:
    ops = tmp_path / "ops"
    t = ops / "ticks" / "K1"
    t.mkdir(parents=True)
    rows, acts, sides = [], [], []
    k = 0
    for g in range(6):
        for j in range(4):
            mu = [34.0, 31.0, 12.0, 25.0][j]
            pid = f"P-{g}-{j}"
            proj = 20.0 if mu >= 30 else 8.0
            rows.append({"game_id": f"G-{g}", "player_id": pid, "team_id": "T-1", "proj_PTS": proj, "proj_REB": 4.0,
                         **{f"proj_{m}": 1.0 for m in PC.MARKETS if m not in ("PTS", "REB")},
                         "min_mu": mu, "p_play": 0.95, "position_group": "C" if j == 1 else "G",
                         "cur_min": [0.0, 50.0, 200.0, 400.0][j], "evidence_bucket": TK._evidence_bucket([0.0, 50.0, 200.0, 400.0][j]),
                         "cold_start_class": "COLD_TRANSFER_IN" if j == 2 else None, "transfer_in": j == 2,
                         "cold_start_new": False, "opening_season": g < 2, "first_30_days": True, "high_exposure": mu >= 30,
                         "center": j == 1, "clean": True, "priced": True, "game_date": "2026-11-10",
                         "bank_sha256": f"b{g}", "distribution_method": "BANK_MIXTURE_PMF"})
            actual = proj + 2.0 if mu >= 30 else proj - 1.0          # high exposure under-forecast by 2
            acts.append({"game_id": f"G-{g}", "player_id": pid, "participation": "PLAYED", "minutes": mu,
                         **{f"act_{m}": 1.0 for m in PC.MARKETS}, "act_PTS": actual, "act_REB": 4.0})
            if mu >= 30:
                for side, dec in (("OVER", 1.91), ("UNDER", 1.91)):
                    k += 1
                    sides.append({"record_id": f"r{k}", "opportunity_id": f"o{g}{j}", "run_id": "R", "model_version": "v",
                                  "data_label": "LIVE", "prediction_as_of": "2026-11-10T23:00:00+00:00", "game_id": f"G-{g}",
                                  "game": "x", "game_date": "2026-11-10", "week": "2026-W46", "player_id": pid, "player": pid,
                                  "team_id": "T-1", "market": "PTS", "market_kind": "STAT", "line": 19.5, "side": side,
                                  "sportsbook": "bk", "book_decimal_odds": dec, "book_american_odds": -110.0,
                                  "quote_available_at": None, "collector_received_at": None, "predicted_minutes": mu,
                                  "p_play": 0.95, "role_pregame": "TOP5_MINUTES", "cold_start_class": None,
                                  "team_games_pregame": 5.0, "exposure_band": "MIN_30_PLUS", "p_play_band": "P_GE_90",
                                  "high_exposure": True, "opening_season": g < 2, "transfer_in": False, "projection": proj,
                                  "p_over": 0.55, "p_under": 0.45, "p_push": 0.0, "p_side": 0.55 if side == "OVER" else 0.45,
                                  "p_side_nonpush": 0.55 if side == "OVER" else 0.45, "fair_decimal": 1.8,
                                  "fair_american": -125.0, "model_edge": 0.05 if side == "OVER" else -0.14,
                                  "favored_side": "OVER", "recommended_side": "OVER", "two_sided": True,
                                  "position_group": "C" if j == 1 else "G", "cur_min": 0.0, "evidence_bucket": "CUR_0",
                                  "first_30_days": True, "center": j == 1, "cold_start_new": False, "clean": True})
    pd.DataFrame(rows).to_parquet(t / "projections.parquet", index=False)
    pd.DataFrame(acts).to_parquet(t / "actuals.parquet", index=False)
    pd.DataFrame(sides).to_parquet(t / "quote_sides.parquet", index=False)
    ss = [{"record_id": s["record_id"], "settled_at": "2026-11-11T05:00:00+00:00", "result_source": "X",
           "participation": "PLAYED", "actual": 22.0, "signed_error": -2.0,
           "result": "WIN" if s["side"] == "OVER" else "LOSS", "side_win": 1.0 if s["side"] == "OVER" else 0.0,
           "brier": (0.55 - 1) ** 2 if s["side"] == "OVER" else 0.45 ** 2, "log_loss": 0.0,
           "closing_decimal_odds": None, "clv": None} for s in sides]
    pd.DataFrame(ss).to_parquet(t / "side_settlements.parquet", index=False)
    return ops


def test_high_exposure_pts_monitor(tmp_path):
    rep = PM.build(_evidence(tmp_path))
    he = rep["high_exposure_pts"]
    p = he["projection"]
    assert p["n"] == 12 and p["mean_model"] == pytest.approx(20.0) and p["mean_actual"] == pytest.approx(22.0)
    assert p["signed_error"]["value"] == pytest.approx(-2.0) and p["relative_error"] == pytest.approx(-2.0 / 22.0)
    ql = he["quoted_lines"]
    assert ql["quoted_lines"] == 12 and ql["settled_lines"] == 12
    assert ql["model_brier"]["value"] == pytest.approx(0.45 ** 2) and ql["mean_model_p_over"] == pytest.approx(0.55)
    c = he["sides"]["counters"]
    assert c["over_favored"] == 12 and c["under_favored"] == 0
    assert he["PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING"] is True     # the interval excludes 0 on the under side
    assert "PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING" in rep["warnings"] and rep["warning_action"].startswith("NONE")
    assert PM.AUTOMATIC_MODEL_CORRECTION is False


def test_market_no_vig_benchmark(tmp_path):
    rep = PM.build(_evidence(tmp_path))
    mb = rep["market_benchmark"]["all"]
    assert mb["lines"] == 12                                      # same lines, both sides from the same book
    assert mb["market_brier"]["value"] == pytest.approx(0.25)     # 1.91 / 1.91 -> no-vig 0.5
    assert mb["model_brier"]["value"] == pytest.approx(0.45 ** 2)
    assert mb["brier_model_minus_market"]["value"] == pytest.approx(0.45 ** 2 - 0.25)
    lt = PM.line_table(PM.load(tmp_path / "ops"), PM.player_table(PM.load(tmp_path / "ops")))
    assert lt["market_p_over"].dropna().eq(0.5).all() and (lt["market_books"].dropna() == 1).all()


def test_early_season_evidence_transfer_center_monitors(tmp_path):
    rep = PM.build(_evidence(tmp_path))
    es = rep["early_season"]["team_games_le_2"]["PTS"]["projection"]
    assert es["n"] == 8 and es["games"] == 2                       # games 0-1, 4 players each
    eb = rep["evidence_buckets_pts"]
    assert eb["CUR_0"]["projection"]["n"] == 6 and eb["CUR_300PLUS"]["projection"]["signed_error"]["value"] == pytest.approx(1.0)
    tr = rep["transfer_cold_start"]["COLD_TRANSFER_IN"]["PTS"]["projection"]
    assert tr["n"] == 6 and tr["signed_error"]["value"] == pytest.approx(1.0)
    c = rep["center_pts"]["C"]["projection"]
    assert c["n"] == 6 and c["signed_error"]["value"] == pytest.approx(-2.0)
    assert rep["center_pts"]["non_C"]["projection"]["n"] == 18


def test_side_monitor_settled_by_side(tmp_path):
    rep = PM.build(_evidence(tmp_path))
    sd = rep["over_under_sides"]["overall"]
    assert sd["over"]["settled"] == 12 and sd["over"]["wins"] == 12 and sd["under"]["losses"] == 12
    assert sd["over_share_of_favored"]["share"] == pytest.approx(1.0)
    assert isinstance(rep["over_under_sides"]["warnings"], list)


def test_daily_and_cumulative_reports(tmp_path):
    ops = _evidence(tmp_path)
    out = PM.write_reports(ops, pd.Timestamp("2026-11-11T13:00Z"), pd.Timestamp("2026-11-10T12:00Z"))
    assert out["daily"] == ["2026-11-10"]
    d = json.loads((ops / "reports" / "daily" / "2026-11-10.json").read_text())
    assert d["high_exposure_pts"]["projection"]["n"] == 12 and "operations" in d
    assert (ops / "reports" / "cumulative.md").exists() and (ops / "reports" / "daily" / "2026-11-10.md").exists()
    assert PM.daily_report_due(ops, pd.Timestamp("2026-11-11T13:00Z"), pd.Timestamp("2026-11-10T12:00Z")) is False
    assert PM.daily_report_due(ops, pd.Timestamp("2026-11-12T13:00Z"), pd.Timestamp("2026-11-10T12:00Z")) is True


def test_validation_fail_blocks_and_inconclusive_retries(tmp_path, monkeypatch):
    from cbb_platform.prod import odds_feed as OF
    ctx = _ctx(tmp_path, None, Clock("2026-11-10T23:00Z"), None, None)
    (tmp_path / "ops").mkdir()

    def fake(status, per):
        return lambda *a, **k: {"status": status, "requests": [{"endpoint": "event_odds", "cost": 0}],
                                "checks": {"expected_markets_returned": {"pass": status == "PASS", "per_market": per},
                                           "timestamps_usable": {"pass": status == "PASS"}},
                                "validated_at": "2026-11-10T23:00:00+00:00", "event_id": "e", "canonical_game_id": "g",
                                "feed_version": "v"}
    monkeypatch.setattr(OF, "validate", fake("FAIL", {"player_points": "MARKET_NOT_OFFERED_FOR_EVENT"}))
    assert TK.step_validate(ctx, None, "e1", "g1", {}) == "INCONCLUSIVE_NO_PLAYER_PROPS_POSTED"
    v = TK._validation_state(ctx, ctx.now())
    assert v["inconclusive_today"] == 1 and TK.validation_eligible(v, ctx)          # nothing posted yet: may retry
    monkeypatch.setattr(OF, "validate", fake("FAIL", {"player_points": "RETURNED"}))
    assert TK.step_validate(ctx, None, "e2", "g2", {}) == "FAIL"
    v = TK._validation_state(ctx, ctx.now())
    assert v["failed"] and not TK.validation_eligible(v, ctx)                        # FAIL: persisted, stays BLOCKED
    assert not (tmp_path / "ops" / "markers" / "QUOTE_INTEGRATION_VALIDATED.json").exists()
    ctx.allow_revalidation = True
    assert TK.validation_eligible(v, ctx)                                            # explicit architect action only
    monkeypatch.setattr(OF, "validate", fake("PASS", {"player_points": "RETURNED"}))
    assert TK.step_validate(ctx, None, "e3", "g3", {}) == "PASS"
    assert not TK.validation_eligible(TK._validation_state(ctx, ctx.now()), ctx)    # never repeated after PASS


# ============================================================================ evidence store primitives
def test_chain_detects_tampering(tmp_path):
    ops = tmp_path / "ops"
    (ops / "ticks" / "K1").mkdir(parents=True)
    (ops / "ticks" / "K1" / "a.json").write_text("{}")
    OS.chain_append(ops, "K1")
    with pytest.raises(OS.OpsStoreError):
        OS.chain_append(ops, "K1")                              # sealed ticks are immutable
    assert OS.verify_chain(ops)["ticks"] == 1
    (ops / "ticks" / "K1" / "a.json").write_text('{"x": 1}')
    with pytest.raises(OS.OpsStoreError):
        OS.verify_chain(ops)


def test_ledger_mirror_is_append_only(tmp_path):
    prod, ops = tmp_path / "prod", tmp_path / "ops"
    src = OS.ledger_mirror(prod)["ledgers/sync_log.jsonl"]
    src.parent.mkdir(parents=True)
    src.write_text('{"a": 1}\n')
    assert OS.mirror_out(ops, prod) == ["ledgers/sync_log.jsonl"]
    src.write_text('{"a": 1}\n{"c": 3}\n')
    OS.mirror_in(ops, prod)                                        # the longer local ledger is kept, never truncated
    assert src.read_text() == '{"a": 1}\n{"c": 3}\n'
    src.unlink()
    OS.mirror_in(ops, prod)                                        # fresh runner: restored from the evidence copy
    assert src.read_text() == '{"a": 1}\n'
    src.write_text('{"b": 2}\n')
    with pytest.raises(OS.OpsStoreError):
        OS.mirror_out(ops, prod)
    with pytest.raises(OS.OpsStoreError):
        OS.mirror_in(ops, prod)


def test_no_credential_or_key_in_evidence(life):
    blob = b"".join(p.read_bytes() for p in (life["w"] / "ops").rglob("*") if p.is_file() and ".git" not in p.parts)
    for secret in (FF.DUMMY_CREDENTIAL, OX.DUMMY_ODDS_CREDENTIAL, KEY):
        assert secret.encode() not in blob
    for p in (life["w"] / "assets").glob("*.enc"):
        b = p.read_bytes()
        assert KEY.encode() not in b and b.startswith(b"Salted__")                 # stored encrypted only
        assert b"increment.duckdb" not in b and b"CURRENT.json" not in b


def test_persistence_module_is_the_only_shell_code_and_is_narrow():
    """Shelling out is confined to cbb_platform/ops_persistence.py and to git / gh / openssl; no deploy host, no SSH."""
    src = Path(OP.__file__).read_text()
    assert OP.ALLOWED_TOOLS == ("git", "gh", "openssl")
    for bad in ("ssh", "scp", "rsync", "curl", "wget", "paramiko", "odds-scanner", "wizardofodds", "--force", "main"):
        assert f'"{bad}"' not in src, bad
    with pytest.raises(OS.OpsStoreError):
        OP._run(["bash", "-c", "true"])
    tick_src = Path(TK.__file__).read_text()
    assert "import subprocess" not in tick_src and "import subprocess" not in Path(OS.__file__).read_text()


def test_cbbd_ttl_counts_from_last_verification(tmp_path):
    """An unchanged re-fetch keeps the original payload file; the TTL then runs from that verification, so a routine
    sync does not re-fetch an unchanged roster / schedule window on every run after its first TTL lapses."""
    key = ("teams/roster", {"season": 2027})
    fake = FF.FakeCBBD({A.request_key(*key): FF.rosters()})

    def get(t):
        return A.Session("ttl", cache_root=tmp_path / "c", ledger_path=tmp_path / "l.jsonl", http_get=fake,
                         credential=lambda: FF.DUMMY_CREDENTIAL, clock=FF.clock_at(t), sleep=lambda s: None
                         ).get(*key, max_age_hours=24)
    assert get("2026-11-01T00:00Z")["status"] == "FETCHED"
    assert get("2026-11-02T01:00Z")["status"] == "FETCHED_UNCHANGED"          # 25 h after receipt: re-verified
    assert get("2026-11-02T12:00Z")["status"] == "CACHE_HIT"                  # 35 h after receipt, 11 h after verification
    assert len(fake.calls) == 2
    assert get("2026-11-03T02:00Z")["status"] == "FETCHED_UNCHANGED" and len(fake.calls) == 3


def test_result_needed_for_imminent_scoring_triggers_sync(life, tmp_path):
    """A knowable, not-yet-ingested game of a team whose next cutoff is imminent triggers a sync (else STALE_STATE)."""
    st = ST.current(life["w"] / "prod" / "state")
    sch = TK.schedule(st)
    ctx = _ctx(tmp_path, life["hist"], Clock("2026-11-12T03:40Z"), None, None)
    (tmp_path / "ops").mkdir()
    OS.write_json(tmp_path / "ops" / "OPERATION.json", {"operation_started_at": "2026-11-01T00:00:00+00:00"})
    p = TK.plan(ctx, st, pd.Timestamp("2026-11-12T03:40Z"))       # T (teams 6, 7) cutoff 05:00Z; N (team 7) unresolved
    n = sch.set_index("canonical_game_id").loc[FF.gid("N")]
    assert n["game_universe"] != "DI_VS_DI" and not n["ingested"]
    assert "RESULT_NEEDED_FOR_SCORING" in p["sync_why"]
