"""Focused tests of the 2026-27 NCAAB player-prop quote feed (The Odds API v4, documented event-odds endpoint).
CI-safe: labeled v4-format fixtures served by a fake HTTP layer; no network, no real credential, no model change."""
from __future__ import annotations

import json

import pandas as pd
import pytest
import requests

from cbb_platform.prod import (board as BO, config as PC, matching as MT, odds_acquire as OA, odds_feed as OF,
                               odds_fixtures as OX, quotes as QT, readiness as RD)

T = "2026-11-10T16:00:00Z"
TIP = "2026-11-11T00:30:00Z"
UPD = "2026-11-10T15:50:00Z"


def _session(tmp_path, fake, clock=T, **kw):
    return OA.Session("test", cache_root=tmp_path / "cache", ledger_path=tmp_path / "odds.jsonl", http_get=fake,
                      credential=lambda: OX.DUMMY_ODDS_CREDENTIAL, clock=lambda: pd.Timestamp(clock), sleep=lambda s: None,
                      **kw)


def _params(eid="evt-1", markets=QT.REQUEST_MARKETS, **over):
    return {"eventId": eid, "regions": "us", "markets": ",".join(markets), "oddsFormat": "american", "dateFormat": "iso",
            "includeLinks": "true", "includeSids": "true", **over}


# ============================================================================ documented market-key mapping
def test_market_mapping_is_the_documented_table():
    m = QT.market_mapping().set_index("MODEL_STAT")
    expect = {"PTS": "player_points", "REB": "player_rebounds", "AST": "player_assists", "FG3M": "player_threes",
              "STL": "player_steals", "BLK": "player_blocks", "TOV": "player_turnovers",
              "PRA": "player_points_rebounds_assists", "PR": "player_points_rebounds", "PA": "player_points_assists",
              "RA": "player_rebounds_assists", "STOCKS": "player_blocks_steals"}
    assert m["ODDS_API_MARKET_KEY"].to_dict() == expect
    assert set(m.index) == set(PC.MARKETS) and m["DOCUMENTED_SUPPORTED"].all()
    assert m.loc["STOCKS", "ALTERNATE_KEY"] == ""                   # no documented blocks+steals alternate
    assert not m["ALTERNATE_DOCUMENTED_FOR_NCAAB"].any()            # alternates documented under an NBA-only heading
    assert set(QT.REQUEST_MARKETS) == set(expect.values())          # alternates are never requested
    ev = OX.event_odds(OX.event("e", "H", "A", TIP), [OX.book("bk", "Bk", [
        OX.market("player_blocks_steals_alternate", OX.ou("Jon Doe", 2.5, 120, -150), UPD),
        OX.market("player_points_alternate", OX.ou("Jon Doe", 19.5, 190, -260), UPD)])])
    q = QT.normalize_event(ev, T)
    assert q["suppression_reason"].tolist() == ["UNSUPPORTED_MARKET"] * 2 + ["", ""]
    assert q["stat"].iloc[2:].eq("PTS").all() and q["is_alternate"].iloc[2:].all()   # a real returned quote is kept


def test_normalization_of_documented_event_odds_structure():
    ev = OX.event_odds(OX.event("evt-1", "Home U Hawks", "Away St Owls", TIP), [
        OX.book("DraftKings", "DraftKings", [OX.market("player_points", OX.ou("Jon Doe Jr.", 14.5, -115, -105, link=True),
                                                       UPD, link=True)], link=True),
        OX.book("fanduel", "FanDuel", [OX.market("player_threes", OX.ou("Jon Doe Jr.", 1.5, 120, -150), UPD)]),
        OX.book("bad", "Bad", [OX.market("player_rebounds", [{"name": "Over", "description": "", "price": -110, "point": 4.5},
                                                              {"name": "Yes", "description": "X", "price": -110, "point": 4.5},
                                                              {"name": "Over", "description": "X", "price": -110, "point": 4.3}],
                                         UPD)])])
    q = QT.normalize_event(ev, T)
    r = q.iloc[0]
    assert (r.provider_event_id, r.sportsbook, r.sportsbook_title, r.market_key, r.stat, r.side, r.line,
            r.american_odds) == ("evt-1", "draftkings", "DraftKings", "player_points", "PTS", "OVER", 14.5, -115)
    assert r.provider_player_name == "Jon Doe Jr." and r.decimal_odds == pytest.approx(1 + 100 / 115)
    assert r.market_last_update == pd.Timestamp(UPD) and pd.isna(r.bookmaker_last_update)
    assert r.quote_available_at == pd.Timestamp(UPD) and r.collector_received_at == pd.Timestamp(T)
    assert r.deep_link.startswith("https://sportsbook.example/betslip")                  # outcome link wins
    assert q.iloc[2].deep_link == "" and q.iloc[2].stat == "FG3M" and q.iloc[2].decimal_odds == pytest.approx(2.2)
    assert q.iloc[4:]["suppression_reason"].tolist() == ["INVALID_QUOTE"] * 3           # no player / bad side / bad line
    late = QT.normalize_event(ev, "2026-11-10T15:40:00Z")                               # receipt before market update
    assert set(late.iloc[:4]["suppression_reason"]) == {"INVALID_QUOTE"}


# ============================================================================ production acquisition guard
def test_guard_admits_only_documented_2026_27_ncaab_requests():
    known = {"evt-1": TIP, "old": "2026-03-01T00:00:00Z"}.get
    OA.check_request("events", OF.events_params(T, "2026-11-12T04:00Z"))
    OA.check_request("event_odds", _params(), known, T)
    bad = [("odds", {"regions": "us"}, None), ("scores", {}, None), ("historical_event_odds", {}, None),
           ("events", OF.events_params("2026-03-01T00:00Z", "2026-03-02T00:00Z"), None),          # 2025-26
           ("events", {"dateFormat": "iso"}, None),
           ("event_odds", _params(markets=("player_points", "player_double_double")), T),
           ("event_odds", _params(markets=("player_points_alternate",)), T),                     # not requested
           ("event_odds", _params(markets=("player_points", "player_points")), T),
           ("event_odds", _params(regions="us,us2"), T), ("event_odds", _params(oddsFormat="decimal"), T),
           ("event_odds", _params(sport="basketball_nba"), T), ("event_odds", _params(apiKey="x"), T),
           ("event_odds", _params(eid="not-listed"), T), ("event_odds", _params(eid="old"), T),
           ("event_odds", _params(), "2026-11-08T00:00:00Z"),                                     # > 36 h before tip
           ("event_odds", _params(), "2026-11-11T00:31:00Z")]                                     # after commence
    for ep, params, now in bad:
        with pytest.raises(OA.OddsGuardViolation):
            OA.check_request(ep, params, known, now or T)


def test_acquisition_budget_quota_headers_cache_and_no_credential_leak(tmp_path):
    ev = OX.event("evt-1", "H", "A", TIP)
    odds = OX.event_odds(ev, [OX.book("draftkings", "DraftKings", [
        OX.market("player_points", OX.ou("Jon Doe", 14.5, -110, -110), UPD),
        OX.market("player_rebounds", OX.ou("Jon Doe", 4.5, -110, -110), UPD)])])
    fake = OX.FakeOdds([ev], {"evt-1": odds}, remaining=1000)
    s = _session(tmp_path, fake)
    with pytest.raises(OA.OddsGuardViolation):                      # eventId must come from an acquired listing
        s.get("event_odds", _params(), max_age_seconds=600)
    s.event_commence = lambda e: None
    with pytest.raises(OA.OddsProviderFailure, match="quota"):      # quota unknown: no paid call before the free listing
        s.event_commence = {"evt-1": TIP}.get
        s.get("event_odds", _params(), max_age_seconds=600)
    assert fake.calls == []
    e = OF.fetch_events(s, T, "2026-11-12T04:00Z")
    assert e["status"] == "FETCHED" and e["cost"] == 0 and s.quota_remaining == 1000
    s.event_commence = OF.event_commence_resolver(s.cache_root, T)
    e = OF.collect(s, ["evt-1"], tmp_path / "coll")
    led = OA.ledger(tmp_path / "odds.jsonl")
    last = led[-1]
    assert last["status"] == "FETCHED" and last["cost"] == 2 and last["quota_remaining"] == 998
    assert last["estimated_cost"] == 12 and last["markets_returned"] == 2 and s.credits_charged == 2
    n = len(fake.calls)
    OF.collect(s, ["evt-1"], tmp_path / "coll")                     # within 10 min: cache hit, no call
    assert len(fake.calls) == n and OA.ledger(tmp_path / "odds.jsonl")[-1]["status"] == "CACHE_HIT"
    assert fake.calls[-1][1]["apiKey"] == OX.DUMMY_ODDS_CREDENTIAL  # the key travels only in the request query
    for f in [tmp_path / "odds.jsonl", *(tmp_path / "cache").rglob("*"), *(tmp_path / "coll").rglob("*")]:
        if f.is_file():
            assert OX.DUMMY_ODDS_CREDENTIAL.encode() not in f.read_bytes(), f
    q = QT.load_collected(sorted((tmp_path / "coll").glob("*.json"))[0])
    assert len(q) == 4 and (q["quote_status"] == "VALID").all()
    tight = _session(tmp_path, fake, clock="2026-11-10T16:11Z", max_credits=13)
    tight.quota_remaining, tight.credits_charged = 1000, 2
    tight.event_commence = {"evt-1": TIP}.get
    with pytest.raises(OA.OddsBudgetExceeded):                      # 2 + 12 > 13: refused before the call
        tight.get("event_odds", _params(), max_age_seconds=0)
    low = _session(tmp_path, fake, clock="2026-11-10T16:11Z", quota_reserve=995)
    low.quota_remaining = 1000
    low.event_commence = {"evt-1": TIP}.get
    with pytest.raises(OA.OddsProviderFailure, match="reserve"):    # 1000 - 12 < 995
        low.get("event_odds", _params(), max_age_seconds=0)


def test_provider_failure_is_ledgered_without_leaking_the_url(tmp_path):
    leak = requests.ConnectionError(f"https://api.the-odds-api.com/v4?apiKey={OX.DUMMY_ODDS_CREDENTIAL}")
    s = _session(tmp_path, OX.FakeOdds([], raise_exc=leak))
    with pytest.raises(OA.OddsProviderFailure) as e:
        OF.fetch_events(s, T, "2026-11-12T04:00Z")
    assert OX.DUMMY_ODDS_CREDENTIAL not in str(e.value)
    assert OX.DUMMY_ODDS_CREDENTIAL not in (tmp_path / "odds.jsonl").read_text()
    assert OF.last_odds_failure(tmp_path / "odds.jsonl", T)["status"] == "FAILED"
    assert OF.last_odds_failure(tmp_path / "odds.jsonl", "2026-11-10T15:00Z") is None     # not yet happened then
    s2 = _session(tmp_path, OX.FakeOdds([OX.event("e", "H", "A", TIP)]), clock="2026-11-10T16:05Z")
    OF.fetch_events(s2, "2026-11-10T16:05Z", "2026-11-12T04:00Z")
    assert OF.last_odds_failure(tmp_path / "odds.jsonl", "2026-11-10T16:10Z") is None     # a later success clears it


# ============================================================================ readiness coverage states
def _slate():
    return pd.DataFrame([{"canonical_game_id": g, "start_time": pd.Timestamp(TIP), "home_team_id": h, "away_team_id": a,
                          "neutral_site": False, "slate_status": "SCORE"}
                         for g, h, a in (("G1", "T1", "T2"), ("G2", "T3", "T4"), ("G3", "T5", "T6"), ("G4", "T7", "T8"),
                                         ("G5", "T9", "T10"))])


def _tidx():
    teams = pd.DataFrame([{"canonical_team_id": f"T{i}", "school": f"School {i}", "display_name": f"School {i}",
                           "mascot": "Cats"} for i in range(1, 11)])
    return MT.team_index(teams, None)


def test_coverage_states_replace_the_generic_quote_blocker(tmp_path):
    evs = [OX.event("e1", "School 1", "School 2", TIP), OX.event("e2", "School 3", "Nowhere Tech", TIP),
           OX.event("e4", "School 7", "School 8", TIP), OX.event("e5", "School 9", "School 10", TIP)]
    fake = OX.FakeOdds(evs)
    s = _session(tmp_path, fake)
    OF.fetch_events(s, T, "2026-11-12T04:00Z")
    lst = OF.listing(T, s.cache_root)
    stale_upd = "2026-11-10T15:00:00Z"
    e1 = OX.event_odds(evs[0], [OX.book("draftkings", "DraftKings", [
        OX.market("player_points", OX.ou("Ann Able", 10.5, -110, -110) + OX.ou("Who Dat", 8.5, -110, -110), UPD),
        OX.market("player_rebounds", OX.ou("Ann Able", 4.5, -110, -110), stale_upd)])])
    e4 = OX.event_odds(evs[2], [])
    e5 = OX.event_odds(evs[3], [OX.book("draftkings", "DraftKings", [
        OX.market("player_points", OX.ou("Cy Cee", 10.5, -110, -110), stale_upd)])])
    q = QT.typed(pd.concat([QT.normalize_event(e, T) for e in (e1, e4, e5)], ignore_index=True))
    slate, tidx = _slate(), _tidx()
    q = MT.match_games(q, slate, tidx)
    cands = pd.DataFrame([{"game_id": "G1", "team_id": "T1", "player_id": "P1", "display_name": "Ann Able"},
                          {"game_id": "G1", "team_id": "T2", "player_id": "P2", "display_name": "Bo Bee"},
                          {"game_id": "G5", "team_id": "T9", "player_id": "P5", "display_name": "Cy Cee"}])
    q = MT.match_players(q, cands, None)
    q = BO.quote_freshness(q, pd.Timestamp(T), {g: pd.Timestamp(TIP) for g in slate["canonical_game_id"]})
    cov = OF.coverage(slate, q, lst, tidx)
    gr = cov["game_reasons"]
    assert gr == {"G1": [], "G2": ["QUOTE_IDENTITY_UNRESOLVED"], "G3": ["ODDS_EVENT_NOT_YET_LISTED"],
                  "G4": ["PLAYER_PROP_MARKET_NOT_YET_POSTED"], "G5": ["QUOTE_STALE"]}
    rr = cov["row_reason"]
    assert rr("G1", "P1", "AST") == "MARKET_NOT_OFFERED_FOR_EVENT"       # per event, never a global conclusion
    assert rr("G1", "P1", "REB") == "STALE_QUOTE"
    assert rr("G1", "P2", "PTS") == "QUOTE_IDENTITY_UNRESOLVED"          # "Who Dat" did not resolve
    assert rr("G1", "P2", "REB") == "NO_VALID_QUOTE"
    assert rr("G3", "P9", "PTS") == "NO_VALID_QUOTE"                    # game-level reason names the cause
    assert cov["diagnostics"]["G1"]["markets_posted"] == ["PTS", "REB"]
    none = OF.coverage(slate, q.iloc[0:0], None, tidx)                  # no listing acquired at all
    assert set(sum(none["game_reasons"].values(), [])) == {"ODDS_EVENT_NOT_YET_LISTED"}
    every = {"ODDS_EVENT_NOT_YET_LISTED", "PLAYER_PROP_MARKET_NOT_YET_POSTED", "MARKET_NOT_OFFERED_FOR_EVENT",
             "QUOTE_IDENTITY_UNRESOLVED", "QUOTE_STALE", "ODDS_API_FAILURE", "QUOTE_INTEGRATION_UNVALIDATED"}
    assert every <= RD.KNOWN and "QUOTE_COVERAGE_UNVERIFIED" not in RD.KNOWN
    assert RD.evaluate(["QUOTE_COVERAGE_UNVERIFIED"])["readiness_state"] == "BLOCKED"   # retired code still fails closed


def test_validation_record_gates_by_time(tmp_path):
    rec = tmp_path / "v.json"
    assert OF.integration_validated(rec, T) is None
    rec.write_text(json.dumps({"status": "PASS", "validated_at": T}))
    assert OF.integration_validated(rec, "2026-11-10T15:59Z") is None and OF.integration_validated(rec, T)
    rec.write_text(json.dumps({"status": "FAIL", "validated_at": T}))
    assert OF.integration_validated(rec, "2026-11-11T00:00Z") is None
