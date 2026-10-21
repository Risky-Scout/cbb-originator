"""Focused tests of the 2026-27 production data feed (CBBD production-only acquisition, incremental atomic state, frozen
identity rules, pregame player universe). CI-safe: synthetic history + labeled CBBD-format fixtures served by a fake
HTTP layer; no network, no real credential, no local frozen store, no model change."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from cbb_platform import config as C, lockbox
from cbb_platform.prod import (cbbd_acquire as A, cbbd_feed as F, config as PC, feed_fixtures as FF, features as PF,
                               fixtures as FX, run as RN, state as ST, sync as Y, universe as UV)

ROOT = Path(C.PROJECT_ROOT)
# research acquisition path: byte-identical to the frozen commit e1353e5 (the production feed never edits it)
FROZEN_GUARD_SHA256 = {
    "cbb_platform/lockbox.py": "b810441e76631b2719422cf1ccadc5de712e1c1dcbdc0672f0b7a21083ea65c1",
    "cbb_platform/source_fetch.py": "6856520c218d974da7aa6d76ac263c4174389e05eefaa8138b96e4add492cef2",
    "cbb_platform/cbbd_client.py": "2cc4b14740ace42de1ed522067bf1b7c4238e945194a8930c337fd05bf4adb43",
    "cbb_platform/adapters/cbbd.py": "470a537c2ad056c66f77183bf1d560b11cbf89b4555a6dba4cee04e303da5dd8",
}
# odds paths that never exist: a live-format daily run in these tests never reads the real data/prod odds cache
NO_ODDS = dict(odds_cache_root=ROOT / "tests" / "_no_odds_cache", odds_collections=ROOT / "tests" / "_no_odds_collections",
               odds_ledger=ROOT / "tests" / "_no_odds_ledger.jsonl", odds_record=ROOT / "tests" / "_no_odds_record.json")
T_SYNC1, T_EARLY, T_SYNC2, T_SYNC3, T_RUN = ("2026-11-01T12:00Z", "2026-11-05T03:00Z", "2026-11-05T06:00Z",
                                            "2026-11-10T15:00Z", "2026-11-10T16:00Z")


def _kw(w: Path, hist: Path) -> dict:
    return dict(history_db=hist, state_root=w / "state", cache_root=w / "cache", ledger_path=w / "ledger.jsonl",
                sync_log=w / "sync.jsonl", work_root=w / "work", credential=lambda: FF.DUMMY_CREDENTIAL,
                provider_kind="LIVE")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """synthetic history -> preseason sync -> early sync (D final, not knowable) -> sync (D ingested) -> idempotent
    re-sync -> daily live-format run for the 2026-11-10 slate."""
    w = tmp_path_factory.mktemp("livefeed")
    hist = FX.synthetic_history(w / "history.duckdb")
    kw = _kw(w, hist)
    before, after = FF.FakeCBBD(FF.payloads("before")), FF.FakeCBBD(FF.payloads("after"))
    out = {"w": w, "hist": hist, "kw": kw, "before": before, "after": after}
    out["s1"] = Y.run(clock=FF.clock_at(T_SYNC1), http_get=before, **kw)
    out["v1"] = ST.current(w / "state")["manifest"]["state_version"]
    out["s_early"] = Y.run(clock=FF.clock_at(T_EARLY), http_get=after, **kw)
    out["s2"] = Y.run(clock=FF.clock_at(T_SYNC2), http_get=after, **kw)
    out["v2"] = ST.current(w / "state")["manifest"]["state_version"]
    out["calls_before_s3"] = len(after.calls)
    out["s3"] = Y.run(clock=FF.clock_at(T_SYNC3), http_get=after, **kw)
    out["daily"] = RN.daily(T_RUN, **NO_ODDS, state_root=w / "state", history_db=hist, ledger_path=w / "shadow.jsonl",
                            run_root=w / "runs", sims=2000, sync_log=w / "sync.jsonl")
    out["universe"] = pd.read_csv(Path(out["daily"]["run_dir"]) / "player_universe.csv")
    return out


def _u(world, **kv):
    u = world["universe"]
    for k, v in kv.items():
        u = u[u[k] == v]
    return u


# ============================================================================ separation + guards
def test_research_lockbox_guard_preserved():
    for rel, sha in FROZEN_GUARD_SHA256.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == sha, rel
    for ep, p in (("teams/roster", {"season": 2027}), ("games", {"season": 2027}),
                  ("recruiting/portal", {"year": 2026}), ("recruiting/players", {"year": 2026}),
                  ("games/players", {"season": 2027, "startDateRange": "2026-11-01", "endDateRange": "2026-11-02"})):
        with pytest.raises(lockbox.LockboxViolation):
            lockbox.check_request("cbbd", ep, p)


def test_production_acquisition_is_a_separate_path():
    import ast
    tree = ast.parse(Path(A.__file__).read_text())
    imported = {n.name for x in ast.walk(tree) if isinstance(x, ast.Import) for n in x.names} | \
        {f"{x.module}.{n.name}" for x in ast.walk(tree) if isinstance(x, ast.ImportFrom) for n in x.names}
    assert not {i for i in imported if i.split(".")[-1] in ("lockbox", "source_fetch", "cbbd_client", "os")}, imported
    names = {x.id for x in ast.walk(tree) if isinstance(x, ast.Name)} | {x.attr for x in ast.walk(tree) if isinstance(x, ast.Attribute)}
    assert not {"environ", "getenv", "opening_authorized", "check_request_lockbox"} & names     # no switch, no env toggle
    assert PC.CBBD_CACHE_RAW.resolve() != C.RAW.resolve() and not str(PC.CBBD_CACHE_RAW.resolve()).startswith(str(C.RAW.resolve()))
    assert str(PC.PROD_CACHE_ROOT.resolve()).startswith(str(PC.PROD_ROOT.resolve()))           # gitignored data/prod/


def test_production_guard_admits_only_2026_27():
    ok = [("teams/roster", {"season": 2027}), ("recruiting/players", {"year": 2026}), ("recruiting/portal", {"year": 2026}),
          ("games", {"season": 2027, "startDateRange": "2026-08-01", "endDateRange": "2026-11-01"}),
          ("stats/player/season", {"season": 2027})]
    for ep, p in ok:
        A.check_request(ep, p)
    A.check_request("substitutions/game", {"gameId": 7001}, lambda g: 2027 if g == 7001 else None)
    bad = [("teams/roster", {"season": 2026}), ("teams/roster", {"season": 2025}), ("teams/roster", {}),
           ("teams/roster", {"season": [2026, 2027]}), ("recruiting/portal", {"year": 2025}),
           ("recruiting/players", {"year": 2027}), ("games", {"season": 2027}),
           ("games", {"season": 2027, "startDateRange": "2026-07-30", "endDateRange": "2026-08-05"}),
           ("games", {"season": 2027, "startDateRange": "2027-07-01", "endDateRange": "2027-08-02"}),
           ("games", {"season": 2026, "startDateRange": "2026-11-01", "endDateRange": "2026-11-02"}),
           ("plays/date", {"date": "2026-11-05"}), ("teams/roster", {"season": 2027, "team": "x"}),
           ("substitutions/team", {"season": 2027, "team": "x"})]
    for ep, p in bad:
        with pytest.raises(A.ProductionGuardViolation):
            A.check_request(ep, p)
    with pytest.raises(A.ProductionGuardViolation):                   # unknown / historical game id
        A.check_request("substitutions/game", {"gameId": 12345}, lambda g: 2026)
    with pytest.raises(A.ProductionGuardViolation):                   # a response carrying another season is refused
        A.check_response("teams/roster", [{"teamId": 1, "season": 2026, "players": []}])
    with pytest.raises(A.ProductionGuardViolation):
        A.check_response("games", [{"id": 1, "season": 2027, "startDate": "2026-03-01T00:00:00Z"}])


# ============================================================================ acquisition: ledger, cache, budget, failures
def test_request_ledger_fields_and_no_credentials(world):
    led = A.ledger(world["w"] / "ledger.jsonl")
    assert led
    need = {"provider", "endpoint", "params", "requested_at", "received_at", "status", "season", "record_count",
            "response_sha256", "cache_path", "retry_count"}
    fetched = [e for e in led if e["status"] in ("FETCHED", "FETCHED_UNCHANGED", "CACHE_HIT")]
    assert fetched and all(need <= set(e) for e in fetched)
    assert {e["season"] for e in led} == {2027} and {e["provider"] for e in led} == {"cbbd"}
    blob = (world["w"] / "ledger.jsonl").read_text() + "".join(
        p.read_text() for p in (world["w"] / "cache").rglob("*.meta.json"))
    assert FF.DUMMY_CREDENTIAL not in blob and "Bearer" not in blob
    assert all(h.get("Authorization") == f"Bearer {FF.DUMMY_CREDENTIAL}" for h in world["before"].headers_seen)


def test_cache_reuse_within_ttl(tmp_path):
    http = FF.FakeCBBD(FF.payloads("before"))
    kw = dict(cache_root=tmp_path / "c", ledger_path=tmp_path / "l.jsonl", http_get=http,
              credential=lambda: FF.DUMMY_CREDENTIAL)
    s = A.Session("t1", clock=FF.clock_at("2026-11-01T12:00Z"), **kw)
    e1 = s.get("teams/roster", {"season": 2027}, max_age_hours=24)
    s2 = A.Session("t2", clock=FF.clock_at("2026-11-01T20:00Z"), **kw)
    e2 = s2.get("teams/roster", {"season": 2027}, max_age_hours=24)
    assert (e1["status"], e2["status"], len(http.calls)) == ("FETCHED", "CACHE_HIT", 1)
    assert e1["response_sha256"] == e2["response_sha256"]
    s3 = A.Session("t3", clock=FF.clock_at("2026-11-03T12:00Z"), **kw)     # older than the TTL: re-fetched
    e3 = s3.get("teams/roster", {"season": 2027}, max_age_hours=24)
    assert e3["status"] == "FETCHED_UNCHANGED" and len(http.calls) == 2 and e3["verified_at"].startswith("2026-11-03")


def test_stage_budget_stops_before_the_call(tmp_path):
    http = FF.FakeCBBD(FF.payloads("before"))
    s = A.Session("budget", max_live_requests=2, cache_root=tmp_path / "c", ledger_path=tmp_path / "l.jsonl",
                  http_get=http, credential=lambda: FF.DUMMY_CREDENTIAL, clock=FF.clock_at("2026-11-01T12:00Z"))
    for ep, p in Y.plan_schedule()[:2]:
        s.get(ep, p, max_age_hours=6)
    with pytest.raises(A.BudgetExceeded):
        s.get(*Y.plan_schedule()[2], max_age_hours=6)
    assert len(http.calls) == 2
    assert A.ledger(tmp_path / "l.jsonl")[-1]["status"] == "REFUSED_BUDGET"


def test_retry_then_permanent_failure(tmp_path):
    key = A.request_key("teams/roster", {"season": 2027})
    http = FF.FakeCBBD(FF.payloads("before"), fail={key: 500})
    s = A.Session("fail", cache_root=tmp_path / "c", ledger_path=tmp_path / "l.jsonl", http_get=http,
                  credential=lambda: FF.DUMMY_CREDENTIAL, sleep=lambda x: None, clock=FF.clock_at("2026-11-01T12:00Z"))
    with pytest.raises(A.ProviderFailure):
        s.get("teams/roster", {"season": 2027}, max_age_hours=24)
    assert len(http.calls) == A.MAX_RETRIES + 1
    st = [e["status"] for e in A.ledger(tmp_path / "l.jsonl")]
    assert st.count("ATTEMPT_FAILED") == A.MAX_RETRIES and st[-1] == "FAILED"
    assert not list((tmp_path / "c").rglob("*.json"))                   # a failure is never cached as data


# ============================================================================ incremental + atomic state
def test_incremental_sync_and_idempotence(world):
    s1, s2, s3 = world["s1"], world["s2"], world["s3"]
    assert s1["status"] == "PROMOTED" and s1["coverage"]["games"] == 7 and s1["coverage"]["completed_games"] == 0
    assert s2["status"] == "PROMOTED" and s2["completed_games_ingested"] == 1
    assert s2["coverage"]["completed_with_team_box"] == s2["coverage"]["completed_with_substitutions"] == 1
    assert s3["status"] == "UNCHANGED" and s3["state_version"] == world["v2"]          # same content: no new version
    assert len(world["after"].calls) > world["calls_before_s3"]                       # it did re-verify the provider
    ptr = ST.current(world["w"] / "state")["pointer"]
    assert ptr["state_version"] == world["v2"] and ptr["last_sync_source_cutoff"].startswith("2026-11-10T15:00")
    man = ST.current(world["w"] / "state")["manifest"]
    for k in ("state_version", "created_at", "source_cutoff", "last_successful_sync", "schedule_as_of", "roster_as_of",
              "last_completed_game_included"):
        assert man.get(k), k
    assert man["parent_state_version"] is not None and man["model_refit"] is False


def test_failed_validation_never_promotes(world, tmp_path, monkeypatch):
    w = tmp_path
    kw = _kw(w, world["hist"])
    ok = Y.run(clock=FF.clock_at(T_SYNC1), http_get=FF.FakeCBBD(FF.payloads("before")), **kw)
    before = (w / "state" / "CURRENT.json").read_text()
    real = F.preseason_identity

    def corrupt(*a, **k):
        rel, rep = real(*a, **k)
        rel["core.recruits"] = rel["core.recruits"].assign(recruit_year=2025)       # a class that does not feed 2026-27
        return rel, rep
    monkeypatch.setattr(F, "preseason_identity", corrupt)
    bad = Y.run(clock=FF.clock_at("2026-11-02T12:00Z"), http_get=FF.FakeCBBD(FF.payloads("before")), **kw)
    assert ok["status"] == "PROMOTED" and bad["status"] == "FAILED" and bad["reason"] == "STATE_UPDATE_FAILED"
    assert (w / "state" / "CURRENT.json").read_text() == before                        # last-known-good untouched
    assert not [p for p in (w / "state" / "versions").iterdir() if p.name.startswith(".building")]


def test_provider_failure_keeps_last_good_and_blocks(world, tmp_path):
    w = tmp_path
    kw = _kw(w, world["hist"])
    Y.run(clock=FF.clock_at(T_SYNC1), http_get=FF.FakeCBBD(FF.payloads("before")), **kw)
    before = (w / "state" / "CURRENT.json").read_text()
    key = A.request_key(*Y.plan_schedule()[1])
    down = FF.FakeCBBD(FF.payloads("before"), fail={key: 503})
    import cbb_platform.prod.cbbd_acquire as acq
    old = acq.BACKOFF
    acq.BACKOFF = 0.0
    try:
        r = Y.run(clock=FF.clock_at("2026-11-01T20:00Z"), http_get=down, **kw)
    finally:
        acq.BACKOFF = old
    assert r["status"] == "FAILED" and r["reason"] == "PROVIDER_FAILURE"
    assert (w / "state" / "CURRENT.json").read_text() == before
    s = RN.daily("2026-11-01T21:00Z", **NO_ODDS, state_root=w / "state", history_db=world["hist"], ledger_path=w / "l.jsonl",
                 run_root=w / "runs", sims=500, sync_log=w / "sync.jsonl", horizon_hours=1)
    assert "PROVIDER_FAILURE" in s["run_reasons"] and s["readiness_state"] == "BLOCKED"


# ============================================================================ schedule + roster normalization
def test_schedule_normalization(world):
    con = duckdb.connect(str(ST.current(world["w"] / "state")["increment_db"]), read_only=True)
    g = con.execute("SELECT * FROM flagship.games").df().set_index("canonical_game_id")
    s = con.execute("SELECT * FROM metadata.prod_schedule").df().set_index("canonical_game_id")
    con.close()
    assert len(g) == 7 and set(g["season"]) == {2027}
    d, t, n, x = FF.gid("D"), FF.gid("T"), FF.gid("N"), FF.gid("X")
    assert g.loc[t, "tip_time_quality"] == "TBD_FLAG" and g.loc[d, "tip_time_quality"] == "EXACT"
    assert str(pd.Timestamp(g.loc[d, "game_date_et"]).date()) == "2026-11-04"           # 19:00 ET on Nov 4
    assert g.loc[d, "home_team_id"] == FX.cid("T", "team", FF.team_id(0))
    assert g.loc[n, "game_universe"] == "DI_VS_NON_DI" and g.loc[d, "game_universe"] == "DI_VS_DI"
    assert s.loc[x, "status"] == "canceled" and s.loc[d, "provider_game_id"] == "7001"
    assert s["source_received_at"].notna().all() and s["schedule_as_of"].notna().all()
    assert g.loc[d, "home_points"] == FF.box_payloads()["hp"]
    gr = world["daily"]["game_reasons"]
    assert "MISSING_SCHEDULE" in gr[x] and "NON_DI_GAME" in gr[n]


def test_roster_normalization_and_division_one(world):
    con = duckdb.connect(str(ST.current(world["w"] / "state")["increment_db"]), read_only=True)
    r = con.execute("SELECT * FROM flagship.rosters").df()
    ts = con.execute("SELECT * FROM core.team_seasons").df().set_index("canonical_team_id")
    con.close()
    assert set(r["season"]) == {2027} and r["canonical_player_id"].str.startswith("P-").all()
    assert (r["canonical_player_id"] == r["source_athlete_id"].map(lambda a: FX.cid("P", "athlete", a))).all()
    assert bool(ts.loc[FX.cid("T", "team", FF.NONDI), "is_division_one"]) is False
    assert ts["is_division_one"].sum() == 8


# ============================================================================ identity classes
def test_returning_player_identity_continuity(world):
    p = FF.pid(0, 0)                                         # same CBBD athlete id in 2025-26 history and 2026-27 roster
    u = _u(world, canonical_player_id=p)
    assert len(u) and set(u["newcomer_class"]) == {"RETURNING"} and set(u["roster_status"]) == {"ON_CURRENT_ROSTER"}
    assert not u["cold_start"].any() and set(u["scoreability"]) == {"SCOREABLE"}
    con = duckdb.connect(str(world["hist"]), read_only=True)
    n = con.execute("SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_player_id = ?", [p]).fetchone()[0]
    con.close()
    assert n > 0                                             # his prior history is the history the frozen state reads


def test_transfer_moves_membership_and_keeps_prior_history(world):
    p = FF.pid(1, 11)                                        # team 1 -> team 0, same provider id (RESOLVED)
    u = _u(world, canonical_player_id=p)
    dest = FX.cid("T", "team", FF.team_id(0))
    origin = FX.cid("T", "team", FF.team_id(1))
    assert set(u["team_id"]) == {dest} and set(u["newcomer_class"]) == {"TRANSFER"}
    assert set(u["pool_evidence"]) == {"TRANSFER_PRESEASON_EVIDENCE"} and set(u["cold_start_class"]) == {"COLD_TRANSFER_IN"}
    assert set(u["scoreability"]) == {"SCOREABLE"}
    cdb = Path(world["daily"]["run_dir"]) / "features" / "canonical_asof.duckdb"
    con = duckdb.connect(str(cdb), read_only=True)
    teams = {r[0] for r in con.execute("""SELECT DISTINCT canonical_team_id FROM flagship.player_game_outcomes
                                          WHERE canonical_player_id = ? AND season <= 2026""", [p]).fetchall()}
    con.close()
    assert teams == {origin}                                 # prior games stay origin-team history (never relabeled)


def test_transfer_with_reissued_provider_id_is_not_duplicated(world):
    old = FF.pid(2, 11)
    new = FX.cid("P", "athlete", FF.NEW_IDS["TRANSFER_NEW_ID"])
    dest = FX.cid("T", "team", FF.team_id(3))
    pool = _u(world, canonical_player_id=old, in_frozen_pool=True)
    assert set(pool["team_id"]) == {dest} and set(pool["roster_status"]) == {"ON_CURRENT_ROSTER_PROVIDER_ID_CHANGED"}
    assert set(pool["scoreability"]) == {"SCOREABLE"}
    dup = _u(world, canonical_player_id=new)
    assert len(dup) and not dup["in_frozen_pool"].any() and set(dup["suppression_reason"]) == {"DUPLICATE_PROVIDER_IDENTITY"}
    stale = _u(world, canonical_player_id=old, in_frozen_pool=False)       # still listed by the origin team (stale)
    assert set(stale["suppression_reason"]) == {"TRANSFERRED_OUT"}
    u = world["universe"]
    per_game = u[u["in_frozen_pool"]].groupby(["game_id", "canonical_player_id"])["team_id"].nunique()
    assert (per_game == 1).all()                                           # no player in two pools of one game
    days = u[u["in_frozen_pool"]].merge(pd.DataFrame({"game_id": list(world["daily"]["game_reasons"])}), on="game_id")
    assert days.groupby("canonical_player_id")["team_id"].nunique().max() == 1


def test_unresolved_transfer_is_suppressed(world):
    u = _u(world, name="Nobody Known")
    assert len(u) and not u["in_frozen_pool"].any() and set(u["suppression_reason"]) == {"AMBIGUOUS_PLAYER"}


def test_ambiguous_transfer_identity_is_never_guessed(tmp_path):
    """Two same-name players on the origin roster: the frozen portal rule leaves the record UNRESOLVED_AMBIGUOUS_NAME."""
    hist = tmp_path / "mini.duckdb"
    con = duckdb.connect(str(hist))
    for s in ("core", "metadata", "flagship"):
        con.execute(f"CREATE SCHEMA {s}")
    a, b = FX.cid("P", "athlete", 1), FX.cid("P", "athlete", 2)
    con.execute("CREATE TABLE core.players (canonical_player_id VARCHAR, display_name VARCHAR)")
    con.execute(f"INSERT INTO core.players VALUES ('{a}', 'Sam Same'), ('{b}', 'Sam Same')")
    con.execute(f"""CREATE TABLE flagship.rosters AS SELECT * FROM (VALUES
        ('{a}', '{FX.cid("T", "team", 9001)}', 2026, '1', 'Sam Same', 'Sam', 'Same', 2025),
        ('{b}', '{FX.cid("T", "team", 9001)}', 2026, '2', 'Sam Same', 'Sam', 'Same', 2025))
        t(canonical_player_id, canonical_team_id, season, source_athlete_id, player_name, first_name, last_name, start_season)""")
    con.close()
    snap = tmp_path / "snap" / "cbbd"
    tr = [{"id": 1, "sourceId": "s", "year": 2026, "firstName": "Sam", "lastName": "Same", "position": "G",
           "origin": {"id": 9001, "name": "O", "conference": "C"}, "destination": {"id": 9002, "name": "D", "conference": "C"},
           "stars": None, "rating": None, "eligibility": None, "yearsRemaining": None}]
    for ep, body in (("teams/roster", [{"teamId": 9002, "teamSourceId": "x", "team": "D", "conference": "C", "season": 2027,
                                         "players": [FF._player(3, "Sam", "Same", start=2027)]}]),
                     ("recruiting/portal", tr), ("recruiting/players", [])):
        from cbb_platform import raw_store
        raw_store.store("cbbd", ep, json.dumps(body).encode(), params={}, root=tmp_path / "snap")
    rel, rep = F.preseason_identity(hist, tmp_path / "snap", tmp_path / "w.duckdb", "2026-10-20T00:00Z")
    t = rel["core.transfers"].iloc[0]
    assert t["identity_method"] == "UNRESOLVED_AMBIGUOUS_NAME"
    assert pd.isna(t["canonical_player_id"]) and pd.isna(t["candidate_canonical_player_id"])
    assert snap.exists()


def test_freshman_enters_pool_with_frozen_cold_start(world):
    fresh = FX.cid("P", "athlete", FF.NEW_IDS["FRESHMAN"])
    u = _u(world, canonical_player_id=fresh)
    assert set(u["newcomer_class"]) == {"FRESHMAN"} and set(u["pool_evidence"]) == {"RECRUIT_PRESEASON_EVIDENCE"}
    assert set(u["cold_start_class"]) == {"COLD_NO_HISTORY"} and u["cold_start"].all() and set(u["scoreability"]) == {"SCOREABLE"}
    con = duckdb.connect(str(ST.current(world["w"] / "state")["increment_db"]), read_only=True)
    r = con.execute("SELECT * FROM core.recruits WHERE canonical_player_id = ?", [fresh]).df()
    con.close()
    assert len(r) == 1 and r.iloc[0]["identity_method"] == F.P2 and pd.isna(r.iloc[0]["source_athlete_id"])
    fdb = Path(world["daily"]["run_dir"]) / "features" / "features_asof.duckdb"
    con = duckdb.connect(str(fdb), read_only=True)
    f = con.execute("SELECT * FROM feat.player_features WHERE canonical_player_id = ?", [fresh]).df()
    con.close()
    assert len(f) and (f["pstd_appearances"].fillna(0) == 0).all()              # no fabricated prior statistics


def test_other_new_players_are_roster_aware_but_suppressed(world):
    walk = _u(world, canonical_player_id=FX.cid("P", "athlete", FF.NEW_IDS["WALK_ON"]))
    assert len(walk) and not walk["in_frozen_pool"].any()
    assert set(walk["suppression_reason"]) == {"NO_FROZEN_MEMBERSHIP_EVIDENCE"} and set(walk["newcomer_class"]) == {"OTHER_NEWCOMER"}
    miss = _u(world, name="Missing Recruit")
    assert len(miss) and set(miss["suppression_reason"]) == {"RECRUIT_IDENTITY_UNAVAILABLE"} and miss["canonical_player_id"].isna().all()


def test_departed_returner_is_simulated_but_never_priced(world):
    gone = FF.pid(0, 11)
    u = _u(world, canonical_player_id=gone, in_frozen_pool=True)
    assert len(u) and set(u["suppression_reason"]) == {"NOT_ON_CURRENT_ROSTER"} and set(u["scoreability"]) == {"SUPPRESSED"}
    sup = UV.suppression_map(world["universe"])
    assert any(k[1] == gone for k in sup)


def test_state_rejects_two_recruits_on_one_identity(world, tmp_path):
    rows = {"flagship.games": pd.DataFrame([{**FX._game(FX._gid("x"), 2027, pd.Timestamp("2026-11-20T00:00Z"),
                                                       FX.cid("T", "team", 9001), FX.cid("T", "team", 9002))}]),
            "core.recruits": pd.DataFrame([{"source_recruit_id": "1", "recruit_year": 2026, "canonical_player_id": "P-x",
                                            "committed_team_id": "T-a", "stars": 3, "rating": 0.9, "ranking": 1,
                                            "height_inches": 80.0, "weight_lbs": 200.0},
                                           {"source_recruit_id": "2", "recruit_year": 2026, "canonical_player_id": "P-x",
                                            "committed_team_id": "T-b", "stars": 3, "rating": 0.9, "ranking": 2,
                                            "height_inches": 80.0, "weight_lbs": 200.0}])}
    with pytest.raises(ST.StateUpdateFailed, match="IDENTITY_CONFLICT"):
        ST.update(rows, "2026-11-01T00:00Z", {"kind": "FIXTURE"}, {}, tmp_path / "s", created_at="2026-11-01T00:00Z")


# ============================================================================ point-in-time chronology
def test_roster_cutoff_chronology(world, tmp_path):
    with pytest.raises(F.FeedUnavailable, match="MISSING_ROSTER"):            # no roster received by that instant
        F.snapshot([("teams/roster", {"season": 2027})], "2026-11-01T11:59Z", tmp_path / "s", world["w"] / "cache")
    prov = F.snapshot([("teams/roster", {"season": 2027})], T_RUN, tmp_path / "s2", world["w"] / "cache")
    assert all(pd.Timestamp(v["received_at"]) <= pd.Timestamp(T_RUN) for v in prov.values())
    s = RN.daily("2026-11-01T11:00Z", **NO_ODDS, state_root=world["w"] / "state", history_db=world["hist"],
                 ledger_path=tmp_path / "l.jsonl", run_root=tmp_path / "r", sims=500, sync_log=world["w"] / "sync.jsonl",
                 horizon_hours=1)
    assert "POST_CUTOFF_DATA" in s["run_reasons"] and s["readiness_state"] == "BLOCKED"


def test_completed_game_cutoff_chronology(world):
    early = world["s_early"]
    assert early["results_deferred_not_yet_knowable"] == 1 and early["completed_games_new"] == 0
    con = duckdb.connect(str(ST.current(world["w"] / "state")["increment_db"]), read_only=True)
    k = con.execute("SELECT max(historical_knowable_at) FROM flagship.player_game_outcomes").fetchone()[0]
    con.close()
    assert pd.Timestamp(k) <= pd.Timestamp(world["s2"]["source_cutoff"])
    assert pd.Timestamp(k) == pd.Timestamp("2026-11-05T04:00Z")                 # HR-1: tip + 4 h


def test_target_game_leakage_prevention(world, tmp_path):
    inc = ST.current(world["w"] / "state")["increment_db"]
    d = FF.gid("D")
    out = PF.compact_canonical(tmp_path / "c1.duckdb", [d], "2026-11-04T23:00Z", 2027, world["hist"], inc)
    assert out["target_outcome_rows"] == 0 and out["post_as_of_rows"] == 0
    con = duckdb.connect(str(tmp_path / "c1.duckdb"), read_only=True)
    assert con.execute(f"SELECT home_points FROM flagship.games WHERE canonical_game_id = '{d}'").fetchone()[0] is None
    assert con.execute(f"SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_game_id = '{d}'").fetchone()[0] == 0
    con.close()
    a = FF.gid("A")                       # a LATER game predicted before D was knowable: D's box is not in its input
    PF.compact_canonical(tmp_path / "c2.duckdb", [a], "2026-11-05T03:59Z", 2027, world["hist"], inc)
    con = duckdb.connect(str(tmp_path / "c2.duckdb"), read_only=True)
    assert con.execute(f"SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_game_id = '{d}'").fetchone()[0] == 0
    con.close()
    PF.compact_canonical(tmp_path / "c3.duckdb", [a], "2026-11-05T04:00Z", 2027, world["hist"], inc)
    con = duckdb.connect(str(tmp_path / "c3.duckdb"), read_only=True)
    assert con.execute(f"SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_game_id = '{d}'").fetchone()[0] == 22
    con.close()


# ============================================================================ Stage 3 compatibility + readiness
def test_stage3_schema_compatibility(world):
    from cbb_platform.stage3 import universe
    assert universe.MEMBERSHIP_SQL.count(PF.TS_ORIGINAL) == 1                   # frozen SQL text unchanged
    d = world["daily"]
    assert d["scored_games"] == 3 and d["data_label"] == "LIVE"
    fdb = Path(d["run_dir"]) / "features" / "features_asof.duckdb"
    con = duckdb.connect(str(fdb), read_only=True)
    n = con.execute("SELECT count(*) FROM feat.player_features").fetchone()[0]
    pe = dict(con.execute("SELECT pool_evidence, count(*) FROM ex.player_game_examples GROUP BY 1").fetchall())
    con.close()
    assert n > 0 and {"TRANSFER_PRESEASON_EVIDENCE", "RECRUIT_PRESEASON_EVIDENCE"} <= set(pe)
    a = FF.gid("A")                                       # team 0's earlier game D is in the state: no STALE_STATE
    assert "STALE_STATE" not in d["game_reasons"][a] and "MISSING_PLAYER_STATE" not in d["game_reasons"][a]


def test_live_readiness_blocks_on_quote_states_not_on_data(world):
    d = world["daily"]
    assert d["readiness_state"] == "BLOCKED"
    assert "QUOTE_INTEGRATION_UNVALIDATED" in d["run_reasons"] and "QUOTE_COVERAGE_UNVERIFIED" not in d["run_reasons"]
    assert all("ODDS_EVENT_NOT_YET_LISTED" in d["game_reasons"][g] for g in d["banks"])   # no listing acquired
    assert not {"LIVE_DATA_FEED_UNAVAILABLE", "STATE_UPDATE_FAILED", "MISSING_ROSTER", "STALE_ROSTER", "STALE_STATE",
                "PROVIDER_FAILURE"} & set(d["run_reasons"])
    for k in ("prediction_as_of", "state_as_of", "schedule_as_of", "roster_as_of"):
        assert d.get(k) is not None and pd.Timestamp(d[k]) <= pd.Timestamp(T_RUN), k
    assert d["validated_prediction_cutoff"] == "TIP_MINUS_1H"
    recs = [json.loads(l) for l in (world["w"] / "shadow.jsonl").read_text().splitlines() if l.strip()]
    rr = [r for r in recs if r["record_type"] == "RUN"][-1]["body"]
    assert rr["roster_as_of"] and rr["schedule_as_of"] and rr["state_as_of"] and rr["data_label"] == "LIVE"
    assert all(r["body"]["feature_cutoff"] for r in recs if r["record_type"] == "PREDICTION")


def test_stale_data_blocking(world, tmp_path):
    s = RN.daily("2026-11-12T16:00Z", **NO_ODDS, state_root=world["w"] / "state", history_db=world["hist"],
                 ledger_path=tmp_path / "l.jsonl", run_root=tmp_path / "r", sims=500, sync_log=world["w"] / "sync.jsonl",
                 horizon_hours=1)
    assert "STALE_STATE" in s["run_reasons"]
    s = RN.daily("2026-11-20T16:00Z", **NO_ODDS, state_root=world["w"] / "state", history_db=world["hist"],
                 ledger_path=tmp_path / "l2.jsonl", run_root=tmp_path / "r2", sims=500, sync_log=world["w"] / "sync.jsonl",
                 horizon_hours=1)
    assert {"STALE_STATE", "STALE_ROSTER"} <= set(s["run_reasons"])
    s = RN.daily("2026-11-12T16:00Z", **NO_ODDS, state_root=tmp_path / "nostate", history_db=world["hist"],
                 ledger_path=tmp_path / "l3.jsonl", run_root=tmp_path / "r3", sims=500)
    assert s["run_reasons"] == ["STATE_UPDATE_FAILED", "LIVE_DATA_FEED_UNAVAILABLE"]


# ============================================================================ quotes: first real-event validation (fixture)
def test_odds_validation_then_live_quote_states(world, tmp_path):
    """Implementation validation on a listed event inside the prop window (ONE event-odds request), then a live-format
    daily run: the generic quote blocker is gone; precise per-game / per-row states remain; best price is the maximum
    decimal price; nothing is priced without a sportsbook quote."""
    from cbb_platform.prod import matching as MT, odds_acquire as OA, odds_feed as OF, odds_fixtures as OX, slate as SL
    st = ST.current(world["w"] / "state")
    slate = SL.build(st["increment_db"], T_RUN, st["manifest"]["schedule_as_of"], 36).set_index("canonical_game_id")
    teams, _ = RN._names(world["hist"])
    tn = teams.set_index("canonical_team_id")["display_name"]
    u = world["universe"]
    a, b, c = FF.gid("A"), FF.gid("B"), FF.gid("C")
    ok = u[(u["game_id"] == a) & u["in_frozen_pool"].astype(bool) & u["suppression_reason"].isna()]
    p0, p1, p2 = ok.drop_duplicates("canonical_player_id").head(3).itertuples(index=False)
    tip = "2026-11-11T00:30:00Z"
    ev_a = OX.event("evt-A", tn[slate.loc[a, "home_team_id"]], tn[slate.loc[a, "away_team_id"]], tip)
    ev_b = OX.event("evt-B", tn[slate.loc[b, "home_team_id"]], "Unknown Visitors", tip)       # one team unresolved
    upd = "2026-11-10T15:50:00Z"
    odds_a = OX.event_odds(ev_a, [
        OX.book("draftkings", "DraftKings", [
            OX.market("player_points", OX.ou(p0.name, 10.5, -110, -110, link=True) + OX.ou("Not On This Team", 8.5, -110, -110),
                      upd, link=True),
            OX.market("player_rebounds", OX.ou(p0.name, 3.5, -120, 100), upd)], link=True),
        OX.book("fanduel", "FanDuel", [OX.market("player_points", OX.ou(p0.name, 10.5, 105, -135), upd)])])
    fake = OX.FakeOdds([ev_a, ev_b], {"evt-A": odds_a})
    clock = FF.clock_at(T_RUN)
    sess = OA.Session("odds-validate", cache_root=tmp_path / "oc", ledger_path=tmp_path / "odds.jsonl", http_get=fake,
                      credential=lambda: OX.DUMMY_ODDS_CREDENTIAL, clock=clock, sleep=lambda s: None)
    rec = tmp_path / "integration_validation.json"
    v = OF.validate("evt-A", now=T_RUN, session=sess, run_root=tmp_path / "vr", record=rec,
                    state_root=world["w"] / "state", history_db=world["hist"], collections_dir=tmp_path / "coll",
                    sims=2000, sync_log=world["w"] / "sync.jsonl")
    assert v["status"] == "PASS", v["checks"]
    assert v["canonical_game_id"] == a and rec.exists()
    paid = [e for e in v["requests"] if e["endpoint"] == "event_odds"]
    assert len(paid) == 1 and paid[0]["cost"] == 2 and paid[0]["quota_remaining"] is not None   # minimum: one request
    pm = v["checks"]["expected_markets_returned"]["per_market"]
    assert pm["player_points"] == "RETURNED" and pm["player_assists"] == "MARKET_NOT_OFFERED_FOR_EVENT"
    assert v["checks"]["player_identity"]["unmatched_names"] == ["Not On This Team"]
    s = RN.daily(T_RUN, state_root=world["w"] / "state", history_db=world["hist"], ledger_path=tmp_path / "l.jsonl",
                 run_root=tmp_path / "r", sims=2000, sync_log=world["w"] / "sync.jsonl", odds_cache_root=tmp_path / "oc",
                 odds_collections=tmp_path / "coll", odds_ledger=tmp_path / "odds.jsonl", odds_record=rec)
    assert "QUOTE_INTEGRATION_UNVALIDATED" not in s["run_reasons"] and "ODDS_API_FAILURE" not in s["run_reasons"]
    gr = s["game_reasons"]
    assert not {"ODDS_EVENT_NOT_YET_LISTED", "PLAYER_PROP_MARKET_NOT_YET_POSTED", "QUOTE_IDENTITY_UNRESOLVED"} & set(gr[a])
    assert "QUOTE_IDENTITY_UNRESOLVED" in gr[b] and "ODDS_EVENT_NOT_YET_LISTED" in gr[c]
    preds = [json.loads(l)["body"] for l in (tmp_path / "l.jsonl").read_text().splitlines()
             if json.loads(l)["record_type"] == "PREDICTION"]
    row = {(p["player_id"], p["market"], p["side"]): p for p in preds if p["game_id"] == a}
    over = row[(p0.canonical_player_id, "PTS", "OVER")]
    assert over["sportsbook"] == "fanduel" and over["best_decimal_odds"] == pytest.approx(2.05) and over["line"] == 10.5
    under = row[(p0.canonical_player_id, "PTS", "UNDER")]
    assert under["sportsbook"] == "draftkings" and under["best_decimal_odds"] == pytest.approx(1 + 100 / 110)
    assert "MARKET_NOT_OFFERED_FOR_EVENT" in row[(p0.canonical_player_id, "AST", None)]["suppression_reason"]
    assert "QUOTE_IDENTITY_UNRESOLVED" in row[(p1.canonical_player_id, "PTS", None)]["suppression_reason"]
    assert "NO_VALID_QUOTE" in row[(p1.canonical_player_id, "REB", None)]["suppression_reason"]
    quoted = {(p0.canonical_player_id, "PTS", 10.5), (p0.canonical_player_id, "REB", 3.5)}
    assert {(p["player_id"], p["market"], p["line"]) for p in preds if p["line"] is not None} == quoted   # nothing fabricated
    assert all(p["readiness_state"] == "BLOCKED" or p["eligibility_state"] == "SHADOW_ONLY_NO_WAGERING" for p in preds)
    later = RN.daily("2026-11-10T16:30Z", state_root=world["w"] / "state", history_db=world["hist"],
                     ledger_path=tmp_path / "l2.jsonl", run_root=tmp_path / "r2", sims=500, sync_log=world["w"] / "sync.jsonl",
                     odds_cache_root=tmp_path / "oc", odds_collections=tmp_path / "coll", odds_ledger=tmp_path / "odds.jsonl",
                     odds_record=rec, game_ids=[a])
    assert "QUOTE_STALE" in later["game_reasons"][a]                    # 40 min after the market update
    assert not [x for x in OA.ledger(tmp_path / "odds.jsonl") if OX.DUMMY_ODDS_CREDENTIAL in json.dumps(x)]
