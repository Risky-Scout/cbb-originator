"""Certification accounting + discovery efficiency (prod.quote_refresh.certify, prod.shadow_tick validation state).

A discovery-only run (/markets, no event odds) never consumes one of the day's 3 certification attempts; an empty one
starts a 60-minute discovery cooldown. Discovery scans at most 8 candidates per region, `us` first, stopping at the
first strong candidate (>=3 markets on >=2 books); `us2` only when `us` showed nothing; event odds use the region the
selected candidate was discovered in, for exactly its advertised markets. No network: provider pieces are faked."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from cbb_platform.prod import odds_acquire as OA
from cbb_platform.prod import quote_refresh as QR
from cbb_platform.prod import shadow_tick as TK

T0 = pd.Timestamp("2026-11-12T17:00Z")


class Clock:
    def __init__(self, t):
        self.t = pd.Timestamp(t)

    def __call__(self):
        return self.t


class FakeSession:
    def __init__(self):
        self.entries, self.quota_remaining, self.event_commence = [], None, None

    def get(self, endpoint, params, **kw):
        self.entries.append({"endpoint": endpoint, "params": params, "status": "FETCHED", "cost": 0})
        return {"body": [], "received_at": T0.isoformat()}


def _cand(i: int) -> dict:
    tip = T0 + pd.Timedelta(hours=2, minutes=i)
    return {"game": {"canonical_game_id": f"G-{i:02d}", "scheduled_tip_utc": tip, "cutoff": tip - pd.Timedelta(hours=1)},
            "event_id": f"ev{i:02d}", "src": {"forecast_id": "F"}}


ALL = list(QR.QT.REQUEST_MARKETS)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake provider. adv[(region, event)] = (n markets, n books); props[event] = prop rows the odds call returns."""
    st = {"adv": {}, "props": {}, "n": 3, "sessions": []}

    def session(ctx, label):
        s = FakeSession()
        st["sessions"].append(s)
        return s

    def discover(session, event_id, region="us"):
        session.entries.append({"endpoint": "event_markets", "params": {"eventId": event_id, "regions": region},
                                "status": "FETCHED", "cost": 1})
        m, b = st["adv"].get((region, event_id), (0, 0))
        sup = ALL[:m]
        return {"supported_markets": sup, "supported_market_count": m, "bookmakers_advertising": b if m else 0,
                "status": "DISCOVERED" if m else "NO_SUPPORTED_PLAYER_PROP_MARKETS", "event_id": event_id,
                "region": region, "body": {}, "request": {"received_at": T0.isoformat()}}

    def collect(session, eids, out_dir, discovered=None, **kw):
        d = discovered[eids[0]]
        session.entries.append({"endpoint": "event_odds", "status": "FETCHED", "cost": d["supported_market_count"],
                                "params": {"eventId": eids[0], "regions": d["region"],
                                           "markets": ",".join(d["supported_markets"])}})
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{eids[0]}_{len(session.entries)}.json"
        p.write_text("{}")
        return [p]

    def price(game, src, path, sl, tidx, now, rid, req):
        n = st["props"].get(path.name.split("_")[0], 0)
        return {}, [{"side": "OVER"}] * n, pd.DataFrame({"stat": ["points"] * n})

    monkeypatch.setattr(QR.SH, "_session", session)
    monkeypatch.setattr(QR.SL, "build", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(QR.SH, "team_names", lambda *a, **k: {})
    monkeypatch.setattr(QR.MT, "team_index", lambda *a, **k: {})
    monkeypatch.setattr(QR.OF, "event_commence_resolver", lambda *a, **k: (lambda e: None))
    monkeypatch.setattr(QR, "certification_candidates", lambda *a, **k: [_cand(i) for i in range(st["n"])])
    monkeypatch.setattr(QR.OF, "discover", discover)
    monkeypatch.setattr(QR.OF, "collect", collect)
    monkeypatch.setattr(QR.OF, "collection_receipt", lambda p: {})
    monkeypatch.setattr(QR, "price_collection", price)
    monkeypatch.setattr(QR, "certification_checks",
                        lambda *a, **k: {c: {"pass": True} for c in QR.CERT_CHECKS})
    clk = Clock(T0)
    ctx = TK.Ctx(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", clock=clk)
    (tmp_path / "ops" / "markers").mkdir(parents=True)
    st["ctx"], st["clk"] = ctx, clk
    st["st"] = {"increment_db": None, "manifest": {"source_cutoff": T0.isoformat()}}
    return st


def _calls(env, endpoint, region=None):
    return [e for s in env["sessions"] for e in s.entries if e["endpoint"] == endpoint
            and (region is None or e["params"].get("regions") == region)]


def _attempts(env):
    return [json.loads(x) for x in (env["ctx"].ops("markers", "quote_validation_attempts.jsonl")).read_text().splitlines()]


def _state(env):
    return TK._validation_state(env["ctx"], env["ctx"].now())


def _eligible(env):
    return TK.validation_eligible(_state(env), env["ctx"])


def test_a_empty_us_and_us2_is_discovery_only_and_starts_cooldown(env):
    assert TK.step_validate(env["ctx"], env["st"], {}) == "INCONCLUSIVE_NO_SUPPORTED_PLAYER_PROP_MARKETS"
    assert len(_calls(env, "event_odds")) == 0
    assert len(_calls(env, "event_markets", "us")) == 3 and len(_calls(env, "event_markets", "us2")) == 3
    a = _attempts(env)[-1]
    assert a["counts_toward_certification_limit"] is False and a["discovery_only"] is True
    assert a["discovery_run"] is True and a["event_odds_request_count"] == 0
    assert a["us_market_discovery_calls"] == 3 and a["us2_market_discovery_calls"] == 3
    v = _state(env)
    assert v["certification_attempts_today"] == 0 and v["discovery_cooldown_active"]
    assert v["last_discovery_status"] == "INCONCLUSIVE_NO_SUPPORTED_PLAYER_PROP_MARKETS"
    assert v["last_discovery_event_odds_request_count"] == 0 and v["last_discovery_supported_markets_found"] == 0


def test_b_wake_10_minutes_later_is_blocked_by_cooldown(env):
    TK.step_validate(env["ctx"], env["st"], {})
    env["clk"].t += pd.Timedelta(minutes=10)
    assert not _eligible(env)
    env["clk"].t += pd.Timedelta(minutes=TK.MIN_MINUTES_BETWEEN_EMPTY_DISCOVERY - 11)
    assert not _eligible(env)                                       # 59 minutes: still cooling


def test_c_wake_60_minutes_later_may_discover_again(env):
    TK.step_validate(env["ctx"], env["st"], {})
    env["clk"].t += pd.Timedelta(minutes=TK.MIN_MINUTES_BETWEEN_EMPTY_DISCOVERY)
    assert _eligible(env)


def test_d_weak_first_candidate_does_not_stop_discovery(env):
    env["adv"] = {("us", "ev00"): (1, 1), ("us", "ev02"): (4, 3)}
    env["props"] = {"ev02": 4}
    out = QR.certify(env["ctx"], env["st"], T0)
    assert out["status"] == "PASS" and out["event_id"] == "ev02"
    assert [e["params"]["eventId"] for e in _calls(env, "event_markets")] == ["ev00", "ev01", "ev02"]
    assert out["discovery_early_stop"]["event_id"] == "ev02"


def test_e_strong_candidate_stops_discovery_early(env):
    env["n"] = 8
    env["adv"] = {("us", "ev01"): (3, 2), ("us", "ev05"): (12, 9)}
    env["props"] = {"ev01": 2}
    out = QR.certify(env["ctx"], env["st"], T0)
    assert [e["params"]["eventId"] for e in _calls(env, "event_markets")] == ["ev00", "ev01"]   # ev05 never scanned
    assert out["event_id"] == "ev01" and out["status"] == "PASS"
    odds = _calls(env, "event_odds")
    assert len(odds) == 1 and odds[0]["params"]["markets"] == ",".join(ALL[:3])                 # only advertised keys


def test_f_bounded_batch_without_strong_candidate_still_selects_best(env):
    env["n"] = 12
    env["adv"] = {("us", "ev03"): (1, 2), ("us", "ev06"): (2, 2), ("us", "ev07"): (2, 1)}
    env["props"] = {"ev06": 1}
    out = QR.certify(env["ctx"], env["st"], T0)
    assert len(_calls(env, "event_markets")) == QR.CERT_DISCOVERY_BATCH_SIZE == 8                  # never > 8
    assert out["discovery_truncated"] == 4 and "discovery_early_stop" not in out
    assert out["event_id"] == "ev06" and out["status"] == "PASS" and out["selected_region"] == "us"
    assert not _calls(env, "event_markets", "us2")


def test_g_us2_fallback_only_when_us_is_empty_and_odds_follow_region(env):
    env["adv"] = {("us2", "ev01"): (3, 2)}                    # strong in us2: the us2 pass stops after ev01
    env["props"] = {"ev01": 3}
    out = QR.certify(env["ctx"], env["st"], T0)
    assert len(_calls(env, "event_markets", "us")) == 3 and len(_calls(env, "event_markets", "us2")) == 2
    assert out["selected_region"] == "us2" and out["status"] == "PASS"
    odds = _calls(env, "event_odds")
    assert len(odds) == 1 and odds[0]["params"]["regions"] == "us2"
    assert out["us2_market_discovery_calls"] == 2 and out["event_odds_request_count"] == 1


def test_h_us_supported_candidate_never_queries_us2(env):
    env["adv"] = {("us", "ev02"): (1, 1), ("us2", "ev00"): (12, 9)}
    env["props"] = {"ev02": 1}
    out = QR.certify(env["ctx"], env["st"], T0)
    assert not _calls(env, "event_markets", "us2") and out["selected_region"] == "us"
    assert _calls(env, "event_odds")[0]["params"]["regions"] == "us"


def test_i_one_odds_request_counts_one_attempt(env):
    env["adv"] = {("us", "ev00"): (2, 1)}                      # advertised, but returns no prop rows
    assert TK.step_validate(env["ctx"], env["st"], {}) == "INCONCLUSIVE_NO_PLAYER_PROPS_RETURNED"
    a = _attempts(env)[-1]
    assert a["event_odds_request_count"] == 1 and a["counts_toward_certification_limit"] is True
    v = _state(env)
    assert v["certification_attempts_today"] == 1 and not v["discovery_cooldown_active"]


def test_j_second_ranked_fallback_still_counts_one_attempt(env):
    env["adv"] = {("us", "ev00"): (2, 1), ("us", "ev01"): (1, 1)}
    assert TK.step_validate(env["ctx"], env["st"], {}) == "INCONCLUSIVE_NO_PLAYER_PROPS_RETURNED"
    assert len(_calls(env, "event_odds")) == 2
    assert _attempts(env)[-1]["event_odds_request_count"] == 2
    assert _state(env)["certification_attempts_today"] == 1


def test_k_three_real_attempts_exhaust_the_day(env):
    env["adv"] = {("us", "ev00"): (2, 1)}
    for _ in range(3):
        assert _eligible(env)
        TK.step_validate(env["ctx"], env["st"], {})
        env["clk"].t += pd.Timedelta(minutes=TK.MIN_MINUTES_BETWEEN_INCONCLUSIVE)
    v = _state(env)
    assert v["certification_attempts_today"] == TK.MAX_CERTIFICATION_ATTEMPTS_PER_DAY == 3
    assert not TK.validation_eligible(v, env["ctx"])
    assert len(_calls(env, "event_odds")) == 3


def test_l_three_empty_discoveries_consume_no_attempts(env):
    for _ in range(3):
        assert _eligible(env)
        TK.step_validate(env["ctx"], env["st"], {})
        env["clk"].t += pd.Timedelta(minutes=TK.MIN_MINUTES_BETWEEN_EMPTY_DISCOVERY)
    v = _state(env)
    assert v["certification_attempts_today"] == 0 and v["discovery_only_runs_today"] == 3
    assert _eligible(env) and not _calls(env, "event_odds")


def test_crash_after_odds_started_still_counts(env, monkeypatch):
    env["adv"] = {("us", "ev00"): (2, 1)}
    monkeypatch.setattr(QR, "price_collection", lambda *a, **k: 1 / 0)
    assert TK.step_validate(env["ctx"], env["st"], {}) == "ERROR"
    assert _attempts(env)[-1]["counts_toward_certification_limit"] is True
    assert _state(env)["certification_attempts_today"] == 1


def test_legacy_records_judged_by_their_request_log(env):
    p = env["ctx"].ops("markers", "quote_validation_attempts.jsonl")
    rows = [{"attempted_at": T0.isoformat(), "status": "INCONCLUSIVE_NO_PLAYER_PROPS_RETURNED",
             "requests": [{"endpoint": "events"}, {"endpoint": "event_odds"}]},
            {"attempted_at": (T0 + pd.Timedelta(minutes=1)).isoformat(),
             "status": "INCONCLUSIVE_NO_SUPPORTED_PLAYER_PROP_MARKETS",
             "requests": [{"endpoint": "events"}, {"endpoint": "event_markets"}]}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    v = _state(env)
    assert v["certification_attempts_today"] == 1 and v["discovery_only_runs_today"] == 1
    assert v["discovery_cooldown_active"]


def test_guard_allows_one_sportsbook_region_only():
    ok = {"eventId": "e", "regions": "us2", "dateFormat": "iso"}
    kw = {"event_commence": lambda e: T0 + pd.Timedelta(hours=3), "now": T0}
    OA.check_request("event_markets", ok, **kw)
    OA.check_request("event_markets", {**ok, "regions": "us"}, **kw)
    for bad in ("us_dfs", "us,us2", "eu"):
        with pytest.raises(OA.OddsGuardViolation, match="regions"):
            OA.check_request("event_markets", {**ok, "regions": bad}, **kw)
