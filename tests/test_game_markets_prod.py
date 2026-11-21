"""Release B production game-market path: quote guard (separate from the certified player guard), parsing, quote
state, settlement, CLV, Board Game Lines grain and the SHADOW policy."""
from __future__ import annotations

import pandas as pd
import pytest

from cbb_platform.game import forecast as GF, markets as GM, model as GMod, params as GP
from cbb_platform.prod import game_exports as GX, game_quotes as GQ, odds_acquire as OA

NOW = pd.Timestamp("2026-12-01 15:00", tz="UTC")
TIP = pd.Timestamp("2026-12-02 00:00", tz="UTC")


def _sess(tmp_path):
    s = GQ.GameSession("t", cache_root=tmp_path / "raw", ledger_path=tmp_path / "l.jsonl",
                       credential=lambda: "x", clock=lambda: NOW.to_pydatetime(), http_get=None)
    s.event_commence = lambda eid: TIP.isoformat() if eid == "e1" else None
    return s


def _core(**kw):
    p = {**GQ.books_param(), "markets": "h2h,spreads,totals", "oddsFormat": "american", "dateFormat": "iso",
         "commenceTimeFrom": "2026-12-01T15:00:00Z", "commenceTimeTo": "2026-12-03T03:00:00Z"}
    p.update(kw)
    return p


def test_player_guard_unchanged_no_featured_call_there():
    assert OA.PATHS["event_odds"].endswith("/events/{eventId}/odds")
    assert "/v4/sports/basketball_ncaab/odds" not in OA.PATHS.values()
    with pytest.raises(OA.OddsGuardViolation):
        OA.check_request("game_odds", _core())


def test_game_guard_allows_core_bulk_and_event_extras_only(tmp_path):
    s = _sess(tmp_path)
    s._check_request("game_odds", _core(), s.event_commence, NOW)
    s._check_request("game_event_odds", {"eventId": "e1", **GQ.books_param(), "markets": ",".join(GQ.EVENT_KEYS),
                                         "oddsFormat": "american", "dateFormat": "iso"}, s.event_commence, NOW)
    for bad in (_core(markets="player_points"), _core(markets="h2h,team_totals"), _core(oddsFormat="decimal"),
                _core(regions="us"), {**_core(), "apiKey": "x"}):
        with pytest.raises(OA.OddsGuardViolation):
            s._check_request("game_odds", bad, s.event_commence, NOW)
    with pytest.raises(OA.OddsGuardViolation):                         # unlisted event
        s._check_request("game_event_odds", {"eventId": "e9", **GQ.books_param(), "markets": "team_totals",
                                             "oddsFormat": "american", "dateFormat": "iso"}, s.event_commence, NOW)
    with pytest.raises(OA.OddsGuardViolation):                         # after commence
        s._check_request("game_event_odds", {"eventId": "e1", **GQ.books_param(), "markets": "team_totals",
                                             "oddsFormat": "american", "dateFormat": "iso"}, s.event_commence,
                         TIP + pd.Timedelta(minutes=1))
    assert s._estimated_cost("game_odds", _core()) == 3


def _event():
    o = lambda **k: k                                                            # noqa: E731
    return {"id": "e1", "sport_key": "basketball_ncaab", "home_team": "Home U", "away_team": "Away U",
            "commence_time": TIP.isoformat(), "bookmakers": [{"key": "draftkings", "markets": [
                {"key": "h2h", "last_update": "2026-12-01T14:59:00Z", "outcomes": [o(name="Home U", price=-200), o(name="Away U", price=170)]},
                {"key": "spreads", "outcomes": [o(name="Home U", price=-110, point=-5.5), o(name="Away U", price=-110, point=5.5)]},
                {"key": "totals", "outcomes": [o(name="Over", price=-105, point=141.5), o(name="Under", price=-115, point=141.5)]},
                {"key": "team_totals", "outcomes": [o(name="Over", description="Away U", price=-110, point=68.5),
                                                     o(name="Under", description="Away U", price=-110, point=68.5)]}]}]}


TIDX = {"explicit": {"home u": {"TH"}, "away u": {"TA"}}, "exact": {}}


def test_parse_maps_outcomes_to_canonical_sides():
    q = GQ.parse([_event()], {"e1": ("G1", "TH", "TA")}, TIDX, NOW, "GQ1")
    got = {(r.market_type, r.side, r.point) for r in q.itertuples()}
    assert ("MONEYLINE", "HOME", None) in {(a, b, None if pd.isna(c) else c) for a, b, c in got}
    assert ("SPREAD", "AWAY", 5.5) in got and ("GAME_TOTAL", "UNDER", 141.5) in got
    assert ("AWAY_TEAM_TOTAL", "OVER", 68.5) in got
    assert q.loc[q.market_type == "MONEYLINE", "decimal"].round(4).tolist() == [1.5, 2.7]
    # a neutral site listed with home/away swapped still prices the canonical side by team name
    q2 = GQ.parse([_event()], {"e1": ("G1", "TA", "TH")}, TIDX, NOW, "GQ1")
    assert set(q2.loc[(q2.market_type == "SPREAD") & (q2.point == -5.5), "side"]) == {"AWAY"}


def test_quote_state_and_close_never_use_post_tip_rows():
    q = GQ.parse([_event()], {"e1": ("G1", "TH", "TA")}, TIDX, TIP - pd.Timedelta(minutes=5), "GQ1")
    late = q.assign(collector_received_at=(TIP + pd.Timedelta(minutes=2)).isoformat(), decimal=3.0)
    allq = pd.concat([q, late]).assign(collector_received_at=lambda d: pd.to_datetime(d.collector_received_at, utc=True))
    assert (GQ.as_of(allq, TIP)["decimal"] < 3).all()
    c = GQ.closing(allq, {"G1": TIP})
    assert len(c) == len(q) and (c["decimal"] < 3).all()


@pytest.mark.parametrize("mt,side,pt,hp,ap,res", [
    ("SPREAD", "HOME", -5.5, 80, 70, "WIN"), ("SPREAD", "HOME", -10.0, 80, 70, "PUSH"),
    ("SPREAD", "AWAY", 5.5, 80, 70, "LOSS"), ("ALT_SPREAD", "AWAY", 12.5, 80, 70, "WIN"),
    ("GAME_TOTAL", "OVER", 150.0, 80, 70, "PUSH"), ("GAME_TOTAL", "UNDER", 151.5, 80, 70, "WIN"),
    ("MONEYLINE", "AWAY", None, 80, 70, "LOSS"), ("HOME_TEAM_TOTAL", "OVER", 79.5, 80, 70, "WIN"),
    ("AWAY_TEAM_TOTAL", "UNDER", 70.0, 80, 70, "PUSH"), ("SPREAD", "HOME", -1.5, None, None, "PENDING")])
def test_settlement(mt, side, pt, hp, ap, res):
    assert GX.settle({"market_type": mt, "side": side, "point": pt}, hp, ap) == res


def test_clv_statuses():
    e = {"game_id": "G1", "market_type": "SPREAD", "side": "HOME", "point": -5.5, "book": "draftkings", "fair_p": 0.5}
    q = GQ.parse([_event()], {"e1": ("G1", "TH", "TA")}, TIDX, TIP - pd.Timedelta(minutes=5), "GQ1")
    q["collector_received_at"] = pd.to_datetime(q["collector_received_at"], utc=True)
    assert GX.clv(e, q)["CLV_STATUS"] == "EXACT_LINE"
    assert GX.clv({**e, "book": "fanduel"}, q)["CLV_STATUS"] == "SAME_LINE_NEAREST_CLOSE"
    mv = GX.clv({**e, "point": -3.5}, q)
    assert mv["CLV_STATUS"] == "LINE_MOVED" and mv["LINE_MOVE"] == 2.0          # -3.5 beat a -5.5 close
    assert GX.clv({**e, "market_type": "MONEYLINE", "point": None}, q.iloc[0:0])["CLV_STATUS"] == "NO_CLOSE_QUOTE"
    assert GX.clv({**e, "fair_p": None}, q)["CLV_STATUS"] == "NO_DEVIG_PAIR"


def test_board_grain_and_shadow_policy():
    eot = GP.endgame_ot()
    v = GMod.VarianceParams(**GP.game_model()["variance"])
    g = GF.canonical(GMod.build(GMod.targets(76.0, 70.0, 8, v), eot))
    row = {"game_id": "G1", "home_team_id": "TH", "away_team_id": "TA", "scheduled_tip_utc": TIP.isoformat(),
           "cutoff_utc": (TIP - pd.Timedelta(hours=1)).isoformat(), "forecast_id": "F1", "model_version": "m",
           "ratings_state_id": "TR-x", "player_model_version": "p", "pmf_sha256": g.sha256(), "h_min": g.h_min,
           "a_min": g.a_min, "rows": g.p.shape[0], "cols": g.p.shape[1], "pmf_zlib": GF.encode(g),
           **{k.lower(): v for k, v in GM.summary(g).items()}}
    pm = pd.DataFrame([row, {**row, "game_id": "G2"}])
    q = GQ.parse([_event()], {"e1": ("G1", "TH", "TA")}, TIDX, NOW, "GQ1")
    q["collector_received_at"] = pd.to_datetime(q["collector_received_at"], utc=True)
    rows, st = GX.board_rows(pm, q, {"TH": "Home U", "TA": "Away U"}, NOW)
    df = pd.DataFrame(rows, columns=GX.LINE_COLS)
    g1 = df[df.GAME_ID == "G1"]
    assert not g1.duplicated(["GAME_ID", "MARKET_TYPE"]).any()
    assert set(g1.MARKET_TYPE) == {"SPREAD", "GAME_TOTAL", "MONEYLINE", "AWAY_TEAM_TOTAL"}
    assert (df.IS_ACTIONABLE == False).all() and (df.SELECTION_TIER == "RESEARCH").all()      # noqa: E712
    assert df[df.GAME_ID == "G2"].DECISION.tolist() == [GX.NO_MARKET]                       # model-only, not a failure
    assert st == {"games": 2, "games_with_market": 1, "rows_with_market": 4, "model_only_games": 1}


def test_stored_pmf_round_trip_is_exact():
    eot = GP.endgame_ot()
    v = GMod.VarianceParams(**GP.game_model()["variance"])
    g = GF.canonical(GMod.build(GMod.targets(66.0, 71.0, 1, v), eot))
    d = GF.decode(GF.encode(g), g.h_min, g.a_min, g.p.shape)
    assert d.sha256() == g.sha256() and d.tie_mass() == 0 and abs(d.p.sum() - 1) < 1e-12


class _Resp:
    def __init__(self, body, last):
        import json as _j
        self.status_code, self.content = 200, _j.dumps(body).encode()
        self.headers = {"x-requests-remaining": "100000", "x-requests-used": "1", "x-requests-last": str(last)}


def test_capture_end_to_end_with_a_fake_provider(tmp_path):
    ev = _event()
    listing = [{k: ev[k] for k in ("id", "sport_key", "home_team", "away_team", "commence_time")}]
    extras = {"id": "e1", "sport_key": "basketball_ncaab", "commence_time": TIP.isoformat(), "bookmakers": [
        {"key": "fanduel", "markets": [{"key": "alternate_spreads", "outcomes": [
            {"name": "Home U", "price": 150, "point": -9.5}, {"name": "Away U", "price": -180, "point": 9.5}]}]}]}
    calls = []

    def http(url, params=None, timeout=None):
        calls.append(url)
        if url.endswith("/events"):
            return _Resp(listing, 0)
        if url.endswith("/basketball_ncaab/odds"):
            assert params["markets"] == "h2h,spreads,totals"
            return _Resp([ev], 3)
        assert params["markets"] == ",".join(GQ.EVENT_KEYS)
        return _Resp(extras, 1)

    s = GQ.GameSession("t", cache_root=tmp_path / "raw", ledger_path=tmp_path / "l.jsonl", credential=lambda: "k",
                       clock=lambda: NOW.to_pydatetime(), http_get=http)
    slate = pd.DataFrame([{"canonical_game_id": "G1", "home_team_id": "TH", "away_team_id": "TA",
                           "start_time": TIP, "neutral_site": False}])
    rec = GQ.capture(s, NOW, slate, TIDX, tmp_path / "gq")
    assert rec["status"] == "COMPLETE" and rec["events_matched"] == 1 and rec["event_calls"] == 1, rec
    assert rec["session"]["credits_charged"] == 4 and len(calls) == 3
    q = GQ.load(tmp_path / "gq", "2026-12-01")
    assert {"SPREAD", "GAME_TOTAL", "MONEYLINE", "AWAY_TEAM_TOTAL", "ALT_SPREAD"} <= set(q.market_type)
    assert not any("apiKey" in l for l in (tmp_path / "l.jsonl").read_text().splitlines())


def test_team_total_quote_ids_are_unique_per_team():
    """2026-11-21 audit: both teams' OVER/UNDER at the same point from one book in one capture shared a quote_id."""
    ev = _event()
    tt = ev["bookmakers"][0]["markets"][3]["outcomes"]
    tt += [{"name": "Over", "description": "Home U", "price": -110, "point": 68.5},
           {"name": "Under", "description": "Home U", "price": -110, "point": 68.5}]
    q = GQ.parse([ev], {"e1": ("G1", "TH", "TA")}, TIDX, NOW, "GQ1")
    t = q[q.market_type.str.endswith("TEAM_TOTAL")]
    assert len(t) == 4 and t.quote_id.is_unique
    assert set(t.loc[t.market_type == "HOME_TEAM_TOTAL", "team_id"]) == {"TH"}
    assert all("team_totals:HOME|" in x for x in t.loc[t.market_type == "HOME_TEAM_TOTAL", "quote_id"])
    other = q[~q.market_type.str.endswith("TEAM_TOTAL")]
    assert all(x.split("|")[2] in GQ.CORE_KEYS for x in other.quote_id)       # non-team-total ids unchanged
