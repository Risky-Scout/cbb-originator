"""Focused tests of the RotoWire CBB availability integration (prod/rotowire_acquire.py, prod/rotowire_feed.py and the
existing availability interface prod/inputs.apply_availability). CI-safe: SYNTHETIC records in the directive's field
layout served by a fake HTTP layer; no network, no real credential, no model change."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
import requests

from cbb_platform import raw_store
from cbb_platform.prod import (config as PC, fixtures as FX, inputs as IN, production_board as PB, readiness as RD,
                               rotowire_acquire as RA, rotowire_feed as RW, run as RN, smoke as SMK, state as ST)

KEY = "RW-DUMMY-KEY-0123456789abcdef"            # sentinel credential: must never appear anywhere on disk
T0 = pd.Timestamp("2026-11-10T15:00:00Z")
TIP = pd.Timestamp("2026-11-11T00:30:00Z")
CUT = TIP - pd.Timedelta(hours=1)


# ============================================================================ synthetic RotoWire-layout records
def news(uid, pid, first, last, status, dt, *, team=("1001", "Home U"), edited=None, priority=3, pos="G"):
    return {"Id": uid, "DateTime": dt, "LastEdited": edited, "Priority": priority,
            "Headline": "SYNTHETIC", "Notes": "LICENSED NOTE TEXT", "Analysis": "LICENSED ANALYSIS TEXT",
            "Player": {"Id": pid, "FirstName": first, "LastName": last, "Position": pos, "SportDataIOId": None},
            "Team": {"Id": team[0], "Name": team[1], "Code": "HU"},
            "Injury": {"Status": status, "Type": "Knee", "Location": "Knee", "Detail": "Sprain", "Side": "Left",
                       "ReturnDate": None}}


def snap(pid, first, last, status, *, team=("1001", "Home U"), pos="G"):
    return {"Id": pid, "FirstName": first, "LastName": last, "Position": pos, "Team": {"Id": team[0], "Name": team[1]},
            "Injury": {"Status": status, "Type": "Knee", "Location": "Knee", "Detail": "Sprain", "Side": "Left",
                       "ReturnDate": None}}


class Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()


class Fake:
    """Serves the queued body per endpoint path; records the request (the key travels only in params)."""

    def __init__(self, **bodies):
        self.bodies = {RA.PATHS[k]: v for k, v in bodies.items()}
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        b = self.bodies[url.replace(RA.BASE, "")]
        if isinstance(b, Exception):
            raise b
        return b if isinstance(b, Resp) else Resp(b)


@pytest.fixture
def ctx():
    teams = pd.DataFrame({"canonical_team_id": ["T1", "T2"], "school": ["Home U", "Away St"],
                          "display_name": ["Home U", "Away St"], "mascot": [None, None]})
    roster = pd.DataFrame([("P1", "T1", "Jon", "Doe", "Jon Doe", "Guard"), ("P2", "T1", "Ann", "Lee", "Ann Lee", "Forward"),
                           ("P3", "T2", "Sam", "Roe", "Sam Roe", "Guard"), ("P4", "T2", "Sam", "Roe", "Sam Roe", "Guard"),
                           ("P5", "T2", "Kai", "Ito", "Kai Ito", "Center")],
                          columns=["canonical_player_id", "canonical_team_id", "first_name", "last_name", "player_name",
                                   "position"])
    return RW.IdentityContext(teams, roster)


GAMES = pd.DataFrame({"canonical_game_id": ["G1"], "home_team_id": ["T1"], "away_team_id": ["T2"],
                      "feature_cutoff": [CUT], "start_time": [TIP]})


def _session(tmp_path, fake, clock):
    return RA.Session("test", cache_root=tmp_path / "cache", ledger_path=tmp_path / "rw.jsonl", http_get=fake,
                      credential=lambda: KEY, clock=lambda: clock[0], sleep=lambda s: None)


def _ingest(tmp_path, ctx, store, clock, endpoint, body):
    s = _session(tmp_path, Fake(**{endpoint: body}), clock)
    return RW.ingest(s.get(endpoint), endpoint, ctx, store, s)


def _state(store, at):
    return RW.player_state(store.frame(), at).set_index("rotowire_player_id")


# ============================================================================ status contract
def test_status_normalization_table():
    exp = {"ACT": "ACTIVE", "OUT": "OUT", "GTD": "GTD", "Q": "QUESTIONABLE", "D": "DOUBTFUL", "SUSP": "SUSPENDED",
           "NA": "UNAVAILABLE", "IR": "LONG_TERM_OUT", "PUP": "LONG_TERM_OUT", "IR-R": "RETURN_DESIGNATION",
           "DNP": "POSTGAME_ONLY"}
    assert {k: RW.normalize_status(k) for k in exp} == exp
    assert RW.normalize_status(" gtd ") == "GTD" and RW.normalize_status("ir-r") == "RETURN_DESIGNATION"


def test_null_and_unknown_status_are_never_coerced(tmp_path, ctx):
    for raw in (None, "", float("nan")):
        assert RW.normalize_status(raw) == "UNKNOWN"
    for raw in ("Questionable", "PROB", "Out For Season", "O"):
        assert RW.normalize_status(raw) == "UNKNOWN"
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u1", "r1", "Jon", "Doe", "Probable", "2026-11-10T09:00:00-05:00")]})
    e = store.load()[0]
    assert (e["raw_status"], e["normalized_status"]) == ("Probable", "UNKNOWN")      # raw kept verbatim
    st = _state(store, T0 + pd.Timedelta(minutes=1)).loc["r1"]
    assert st["effective_status"] == "UNKNOWN" and st["gate"] == RW.GATE_UNRECOGNIZED          # suppressed, not priced


def test_dnp_is_never_pregame_evidence(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    _ingest(tmp_path, ctx, store, clock, "news_injuries", {"Updates": [
        news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-09T09:00:00-05:00"),
        news("u2", "r1", "Jon", "Doe", "DNP", "2026-11-10T09:30:00-05:00"),          # postgame designation
        news("u3", "r2", "Ann", "Lee", "DNP", "2026-11-10T09:30:00-05:00", pos="F")]})
    assert {e["normalized_status"] for e in store.load()} == {"OUT", "POSTGAME_ONLY"}  # stored for audit
    fr, _ = RW.availability_frame(store.frame(), GAMES, T0 + pd.Timedelta(hours=1))
    assert "POSTGAME_ONLY" not in set(fr["status"])
    assert fr.set_index("player_id")["status"].to_dict() == {"P1": "OUT"}             # the DNP never superseded it
    assert "P2" not in set(fr["player_id"])                                           # DNP alone: no evidence at all
    assert RW.usable(store.frame(), T0 + pd.Timedelta(days=5))["normalized_status"].ne("POSTGAME_ONLY").all()
    with pytest.raises(RW.AvailabilityLeakage):
        RW.interface_status("POSTGAME_ONLY")


# ============================================================================ the availability interface
def _rows():
    return pd.DataFrame({"game_id": ["G1"] * 4, "team_id": ["T1", "T1", "T2", "T2"], "player_id": ["P1", "P2", "P3", "P5"],
                         "p_play": [0.9, 0.8, 0.7, 0.95], "min_mu": [30.0, 8.0, 12.0, 25.0], "min_sigma": [4.0] * 4,
                         "role_diagnostic": ["TOP5_MINUTES", "BENCH", "BENCH", "TOP5_MINUTES"],
                         **{f"{s}_mu": [1.0] * 4 for s in IN.L.STATS}})


def _frame(status_by_player, gate=None):
    return pd.DataFrame([{"game_id": "G1", "player_id": p, "status": s, "received_at": T0,
                          "availability_source": "ROTOWIRE", "availability_raw_status": s,
                          "availability_gate": (gate or {}).get(p, "")} for p, s in status_by_player.items()])


@pytest.mark.parametrize("status", RW.OUT_LIKE)
def test_out_like_states_are_unavailable(status):
    rows, flags = IN.apply_availability(_rows(), _frame({"P2": status}))
    r = rows.set_index("player_id")
    assert r.loc["P2", "player_suppression"] == "CONFIRMED_UNAVAILABLE" and not r.loc["P2", "_scored"]
    assert r.loc["P2", "p_play"] == 0.8                                  # P(play) itself is never rewritten
    assert flags["G1"]["reasons"] == []                                  # bench player: no rotation shock
    _, flags = IN.apply_availability(_rows(), _frame({"P1": status}))
    assert "UNSUPPORTED_ROTATION_SHOCK" in flags["G1"]["reasons"]


def test_active_is_available_and_ungated():
    rows, flags = IN.apply_availability(_rows(), _frame({"P1": "ACTIVE"}))
    r = rows.set_index("player_id").loc["P1"]
    assert r["availability_status"] == "ACTIVE" and r["player_suppression"] == "" and r["_scored"]
    assert r["availability_gate"] == "" and flags["G1"]["reasons"] == []


@pytest.mark.parametrize("status", ["GTD", "QUESTIONABLE", "DOUBTFUL", "RETURN_DESIGNATION", "UNKNOWN"])
def test_uncertain_states_preserved_with_base_p_play(status):
    base, _ = IN.apply_availability(_rows(), None)
    rows, flags = IN.apply_availability(_rows(), _frame({"P2": status, "P5": status},
                                                       {"P2": RW.GATE_UNCERTAIN, "P5": RW.GATE_UNCERTAIN}))
    r = rows.set_index("player_id")
    assert r.loc["P2", "availability_status"] == status and r.loc["P2", "availability_gate"] == RW.GATE_UNCERTAIN
    assert r.loc["P2", "_scored"] and r.loc["P2", "player_suppression"] == ""        # still forecast (shadow)
    np.testing.assert_array_equal(rows["p_play"].to_numpy(), base["p_play"].to_numpy())   # no invented probability
    assert not [c for c in rows.columns if "prob" in c.lower() and c != "p_play"]
    assert flags["G1"]["reasons"] == ["AVAILABILITY_UNCERTAIN_ROTATION"]               # P5 is a rotation player
    ev = RD.evaluate((), flags["G1"]["reasons"], [RW.GATE_UNCERTAIN])
    assert ev["readiness_state"] == "BLOCKED" and ev["wager_eligible"] is False and not ev["unknown_reasons"]


def test_availability_reason_codes_known_blocking_and_mapped():
    for c in RD.AVAILABILITY_GAME_REASONS + RD.AVAILABILITY_ROW_REASONS:
        assert c in RD.KNOWN and RD.evaluate((c,))["readiness_state"] == "BLOCKED"
        assert PB.condition_of(c)[2] is False                                       # never ACTIONABLE
    assert PB.condition_of("AVAILABILITY_STALE")[0] is False                        # policy §8: no publication
    # architect ruling 2026-10-31: unresolved availability is PRICED_BUT_NON_ACTIONABLE; the rest stays suppressed
    assert PB.FAIL_CLOSED_MATRIX["UNRESOLVED_AVAILABILITY"][:3] == (True, True, False)
    for c in ("AVAILABILITY_UNCERTAIN", "AVAILABILITY_UNCERTAIN_ROTATION"):
        assert PB.CODE_CONDITION[c] == "UNRESOLVED_AVAILABILITY" and c in PB.PRICED_BLOCKED_REASONS
    for c in ("AVAILABILITY_SOURCE_CONFLICT", "AVAILABILITY_STATUS_UNRECOGNIZED", "CONFIRMED_UNAVAILABLE"):
        assert PB.condition_of(c)[:3] == (False, False, False) and c not in PB.PRICED_BLOCKED_REASONS
    assert set(RW.UNRESOLVED) == {"GTD", "QUESTIONABLE", "DOUBTFUL", "RETURN_DESIGNATION"}
    assert not set(RW.OUT_LIKE) & set(RW.UNRESOLVED) and set(RW.OUT_LIKE) <= set(IN.UNAVAILABLE)
    assert PB.RIGHTS_REGISTER["RotoWire CBB API (availability)"] == "UNVERIFIED"
    assert PB.rights_gate()["COMMERCIAL_RIGHTS_GATE"] == "FAIL"
    assert PC.WAGERING_ENABLED is False


# ============================================================================ timestamps / versioning / append-only
def test_timestamp_parsing_with_utc_offset():
    assert RW.parse_ts("2026-11-10T14:05:00-05:00") == ("2026-11-10T19:05:00+00:00", "OK")
    assert RW.parse_ts("2026-11-10T19:05:00Z") == ("2026-11-10T19:05:00+00:00", "OK")
    assert RW.parse_ts("2026-11-10T14:05:00") == (None, "NAIVE_UNRESOLVED")          # never guessed into a zone
    assert RW.parse_ts("11/10/2026 2:05 PM") == (None, "UNPARSEABLE")
    assert RW.parse_ts(None) == (None, "MISSING")


def test_last_edited_creates_a_new_immutable_version(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    dt = "2026-11-10T09:00:00-05:00"
    _ingest(tmp_path, ctx, store, clock, "news_injuries", {"Updates": [news("u1", "r1", "Jon", "Doe", "GTD", dt)]})
    first = store.path.read_bytes()
    clock[0] = T0 + pd.Timedelta(minutes=10)
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", dt, edited="2026-11-10T10:05:00-05:00")]})
    ev = store.load()
    assert len(ev) == 2 and store.path.read_bytes().startswith(first)                 # prior line untouched
    assert [e["normalized_status"] for e in ev] == ["GTD", "OUT"] and ev[0]["rotowire_update_id"] == ev[1]["rotowire_update_id"]
    assert _state(store, clock[0] + pd.Timedelta(minutes=1)).loc["r1", "effective_status"] == "OUT"
    assert _state(store, clock[0] - pd.Timedelta(minutes=1)).loc["r1", "effective_status"] == "GTD"   # history kept


def test_append_only_status_transitions(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    seq = [("u1", "OUT", "2026-11-08T09:00:00-05:00"), ("u2", "GTD", "2026-11-09T09:00:00-05:00"),
           ("u3", "ACT", "2026-11-10T09:00:00-05:00")]
    snaps = []
    for k, (uid, s, dt) in enumerate(seq):
        clock[0] = T0 + pd.Timedelta(minutes=10 * k)
        _ingest(tmp_path, ctx, store, clock, "news_injuries", {"Updates": [news(uid, "r1", "Jon", "Doe", s, dt)]})
        snaps.append(store.path.read_bytes())
    assert all(snaps[i + 1].startswith(snaps[i]) for i in range(2))
    assert [e["normalized_status"] for e in store.load()] == ["OUT", "GTD", "ACTIVE"]
    assert [e["seq"] for e in store.load()] == [0, 1, 2]
    st = [_state(store, T0 + pd.Timedelta(minutes=10 * k + 1)).loc["r1", "effective_status"] for k in range(3)]
    assert st == ["OUT", "GTD", "ACTIVE"]


def test_duplicate_response_is_idempotent(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    body = {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T09:00:00-05:00", priority=5),
                        news("u2", "r2", "Ann", "Lee", "Q", "2026-11-10T09:01:00-05:00", priority=1, pos="F")]}
    a = _ingest(tmp_path, ctx, store, clock, "news_injuries", body)
    clock[0] = T0 + pd.Timedelta(minutes=10)
    b = _ingest(tmp_path, ctx, store, clock, "news_injuries", body)
    assert (a["events_new"], b["events_new"]) == (2, 0) and len(store.load()) == 2
    assert store.load()[0]["collected_at_utc"] == T0.isoformat()                      # earliest collection kept
    assert {e["priority"] for e in store.load()} == {1, 5}                            # priority never filters
    led = RA.ledger(tmp_path / "rw.jsonl")
    assert [e["status"] for e in led] == ["FETCHED", "PROCESSED", "FETCHED_UNCHANGED", "PROCESSED"]


# ============================================================================ point in time
def test_point_in_time_cutoff(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [CUT - pd.Timedelta(minutes=30)]
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u1", "r1", "Jon", "Doe", "ACT", "2026-11-10T12:00:00-05:00")]})
    clock[0] = CUT + pd.Timedelta(minutes=5)                                         # collected after the cutoff
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u2", "r1", "Jon", "Doe", "OUT", "2026-11-10T18:50:00-05:00")]})
    fr, _ = RW.availability_frame(store.frame(), GAMES, CUT + pd.Timedelta(hours=3))   # as_of after: game cutoff binds
    assert fr.set_index("player_id").loc["P1", "status"] == "ACTIVE"
    assert pd.Timestamp(fr["received_at"].max()) < CUT


def test_late_collected_update_cannot_backdate(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [pd.Timestamp("2026-11-10T17:00:00Z")]
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T10:00:00-05:00")]})   # source 15:00Z
    e = store.load()[0]
    assert e["source_datetime"] == "2026-11-10T15:00:00+00:00" and e["knowable_at"] == "2026-11-10T17:00:00+00:00"
    assert _state(store, pd.Timestamp("2026-11-10T16:00:00Z")).empty                 # not knowable at 16:00
    assert _state(store, pd.Timestamp("2026-11-10T17:00:01Z")).loc["r1", "effective_status"] == "OUT"


# ============================================================================ raw evidence / secrets
def test_raw_payload_hash_and_no_licensed_text_in_events(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    body = {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T09:00:00-05:00")]}
    _ingest(tmp_path, ctx, store, clock, "news_injuries", body)
    e = store.load()[0]
    raw = json.dumps(body).encode()
    assert e["raw_payload_sha256"] == hashlib.sha256(raw).hexdigest()
    path, meta = raw_store.records("rotowire", tmp_path / "cache")[0]
    assert raw_store.read_payload(path) == raw and meta["sha256"] == e["raw_payload_sha256"]
    assert e["source_endpoint"] == "/Basketball/CBB/News/Injuries.php" and e["record_sha256"]
    for k in RW.EVENT_FIELDS:
        assert k in e
    text = store.path.read_text()
    assert "LICENSED" not in text                                                     # notes / analysis never copied


def test_no_secret_is_logged_or_stored(tmp_path, ctx, monkeypatch):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    body = {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T09:00:00-05:00")]}
    s = _session(tmp_path, f := Fake(news_injuries=body,
                                     snapshot=requests.ConnectionError(f"GET {RA.BASE}?key={KEY} refused")), clock)
    out = RW.poll(s, ctx, store)
    assert f.calls[0][1] == {RA.API_KEY_PARAM: KEY} and out["snapshot"]["status"] == "PROVIDER_FAILURE"
    assert KEY not in json.dumps(out)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            data = raw_store.read_payload(p) if p.suffix == ".gz" else p.read_bytes()
            assert KEY.encode() not in data, p
    monkeypatch.setenv("ROTOWIRE_API_KEY", KEY)
    from cbb_platform import credentials
    assert KEY in credentials.present_values()                                       # registered for leak scans


def test_request_guard_refuses_unconfirmed_parameters_and_endpoints(tmp_path):
    s = _session(tmp_path, Fake(), [T0])
    for ep, params in (("news_injuries", {"date": "2026-11-10"}), ("snapshot", {"format": "json"}),
                       ("lineups", {}), ("news_updates", {})):
        with pytest.raises(RA.RotoWireGuardViolation):
            s.get(ep, params)
    assert s.live_requests == 0


# ============================================================================ outage / malformed
def test_provider_outage_fails_closed_through_freshness(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    s = _session(tmp_path, Fake(news_injuries=Resp(b"", 503), snapshot=requests.Timeout("t")), clock)
    out = RW.poll(s, ctx, store)
    assert {v["status"] for v in out.values()} == {"PROVIDER_FAILURE"} and store.load() == []
    assert RW.freshness(RA.ledger(tmp_path / "rw.jsonl"), GAMES, T0)["game_reasons"] == {"G1": ["AVAILABILITY_FEED_UNAVAILABLE"]}
    assert len([c for c in s.entries if c["status"] == "ATTEMPT_FAILED"]) == 2 * RA.MAX_RETRIES


def test_freshness_bounds_follow_policy(tmp_path, ctx):
    assert [RW.freshness_bound_minutes(m) for m in (2000, 24 * 60, 600, 6 * 60, 59, 0)] == [360, 360, 90, 90, 20, 20]
    store, clock = RW.EventStore(tmp_path / "ev"), [CUT - pd.Timedelta(minutes=40)]
    s = _session(tmp_path, Fake(news_injuries={"Updates": []}, snapshot={"Players": []}), clock)
    RW.poll(s, ctx, store)
    led = RA.ledger(tmp_path / "rw.jsonl")
    assert RW.freshness(led, GAMES, CUT - pd.Timedelta(minutes=25))["game_reasons"]["G1"] == []
    assert RW.freshness(led, GAMES, CUT - pd.Timedelta(minutes=10))["game_reasons"]["G1"] == ["AVAILABILITY_STALE"]
    assert RW.due(led, CUT - pd.Timedelta(minutes=34), [TIP]) == {"mode": "CRITICAL_PRE_TIP", "interval_minutes": 5, "due": True}
    assert RW.due(led, CUT - pd.Timedelta(minutes=38), [TIP])["due"] is False
    assert RW.poll_mode(T0 - pd.Timedelta(days=3), [TIP])[0] == "OFF_DAY"
    assert RW.poll_mode(T0, [TIP])[0] == "GAME_DAY"


def test_malformed_response_and_records(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    s = _session(tmp_path, Fake(news_injuries=Resp(b"<html>not json</html>")), clock)
    with pytest.raises(RA.RotoWireProviderFailure, match="MALFORMED_RESPONSE"):
        s.get("news_injuries")
    r = _ingest(tmp_path, ctx, store, clock, "news_injuries", {"Message": "unexpected"})
    assert r["status"] == "MALFORMED_RESPONSE" and store.load() == []
    bad = news("u9", "r9", "X", "Y", "OUT", "2026-11-10T09:00:00-05:00")
    del bad["Player"]
    r = _ingest(tmp_path, ctx, store, clock, "news_injuries",
                {"Updates": [bad, "garbage", news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T09:00:00-05:00")]})
    assert (r["records"], r["malformed_records"], r["events_new"]) == (3, 2, 1)
    led = RA.ledger(tmp_path / "rw.jsonl")
    assert RA.last_success([e for e in led if e["status"] != "PROCESSED"], "news_injuries", T0) is None


# ============================================================================ identity
def test_identity_resolution_states(ctx):
    f = lambda first, last, team="Home U", pos="G", tid="1001": {
        "rotowire_player_id": "x", "first_name": first, "last_name": last, "position": pos,
        "team_name": team, "rotowire_team_id": tid}
    assert ctx.resolve(f("Jon", "Doe"))["canonical_player_id"] == "P1"
    assert ctx.resolve(f("Jon", "Doe"))["identity_resolution_status"] == "RESOLVED"
    assert ctx.resolve(f("Sam", "Roe", "Away St"))["identity_resolution_status"] == "AMBIGUOUS"     # two on roster
    assert ctx.resolve(f("Jon", "Doe", pos="C"))["identity_resolution_status"] == "AMBIGUOUS"       # position conflict
    assert ctx.resolve(f("Jonathan", "Doe"))["identity_resolution_status"] == "UNMATCHED"           # never fuzzy
    assert ctx.resolve(f("Jon", "Doe", "Home University"))["identity_resolution_status"] == "UNMATCHED"
    m = RW.IdentityContext(ctx.teams, ctx.roster, player_map=pd.DataFrame({"rotowire_player_id": ["x"],
                                                                           "canonical_player_id": ["P4"]}))
    r = m.resolve(f("Sam", "Roe", "Away St"))
    assert (r["identity_resolution_status"], r["canonical_player_id"], r["identity_method"]) == ("RESOLVED", "P4", "EXPLICIT_MAP")


def test_ambiguous_and_unmatched_identities_fail_closed(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    _ingest(tmp_path, ctx, store, clock, "news_injuries", {"Updates": [
        news("u1", "r3", "Sam", "Roe", "OUT", "2026-11-10T09:00:00-05:00", team=("1002", "Away St"))]})
    fr, reasons = RW.availability_frame(store.frame(), GAMES, T0 + pd.Timedelta(minutes=5))
    assert fr.empty and reasons["G1"] == ["AVAILABILITY_IDENTITY_UNRESOLVED"]
    assert store.load()[0]["identity_resolution_status"] == "AMBIGUOUS"               # stored for QA
    store2 = RW.EventStore(tmp_path / "ev2")
    _ingest(tmp_path, ctx, store2, clock, "news_injuries", {"Updates": [
        news("u2", "r8", "Nobody", "Known", "GTD", "2026-11-10T09:00:00-05:00", team=("9999", "Unknown College"))]})
    fr, reasons = RW.availability_frame(store2.frame(), GAMES, T0 + pd.Timedelta(minutes=5))
    assert fr.empty and reasons["G1"] == ["AVAILABILITY_IDENTITY_UNRESOLVED"]          # team unknown: every game
    store3 = RW.EventStore(tmp_path / "ev3")
    _ingest(tmp_path, ctx, store3, clock, "news_injuries", {"Updates": [
        news("u3", "r8", "Nobody", "Known", "ACT", "2026-11-10T09:00:00-05:00", team=("9999", "Unknown College"))]})
    assert RW.availability_frame(store3.frame(), GAMES, T0 + pd.Timedelta(minutes=5))[1]["G1"] == []


# ============================================================================ snapshot reconciliation
def test_snapshot_reconciliation_never_rewrites_news(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T09:00:00-05:00")]})
    news_line = store.path.read_bytes()
    clock[0] = T0 + pd.Timedelta(minutes=1)
    _ingest(tmp_path, ctx, store, clock, "snapshot", {"Players": [snap("r1", "Jon", "Doe", "OUT")]})
    ev = store.load()
    assert store.path.read_bytes().startswith(news_line) and len(ev) == 2
    assert (ev[1]["event_kind"], ev[1]["reconciliation"], ev[1]["source_datetime"]) == (
        "SNAPSHOT_OBSERVATION", "MATCHES_LATEST_NEWS", None)
    clock[0] = T0 + pd.Timedelta(minutes=2)
    assert _ingest(tmp_path, ctx, store, clock, "snapshot", {"Players": [snap("r1", "Jon", "Doe", "OUT")]})["events_new"] == 0
    assert _state(store, clock[0] + pd.Timedelta(seconds=1)).loc["r1", "effective_status"] == "OUT"


def test_snapshot_change_without_matching_news_update(tmp_path, ctx):
    store, clock = RW.EventStore(tmp_path / "ev"), [T0]
    _ingest(tmp_path, ctx, store, clock, "news_injuries",
            {"Updates": [news("u1", "r1", "Jon", "Doe", "OUT", "2026-11-10T09:00:00-05:00")]})
    clock[0] = T0 + pd.Timedelta(minutes=10)
    _ingest(tmp_path, ctx, store, clock, "snapshot", {"Players": [snap("r1", "Jon", "Doe", "GTD"),
                                                                   snap("r5", "Kai", "Ito", "Q", team=("1002", "Away St"), pos="C")]})
    obs = {e["rotowire_player_id"]: e for e in store.load() if e["event_kind"] == "SNAPSHOT_OBSERVATION"}
    assert obs["r1"]["reconciliation"] == obs["r5"]["reconciliation"] == "NO_MATCHING_NEWS_UPDATE"
    assert obs["r1"]["knowable_at"] == obs["r1"]["collected_at_utc"] == clock[0].isoformat()
    st = _state(store, clock[0] + pd.Timedelta(seconds=1))
    assert (st.loc["r1", "effective_status"], st.loc["r1", "gate"]) == ("UNKNOWN", RW.GATE_CONFLICT)   # never picks one
    assert (st.loc["r5", "effective_status"], st.loc["r5", "gate"]) == ("QUESTIONABLE", RW.GATE_UNCERTAIN)
    assert [e["normalized_status"] for e in store.load() if e["event_kind"] == "NEWS_UPDATE"] == ["OUT"]
    clock[0] = T0 + pd.Timedelta(minutes=20)                                        # r5 leaves the current list
    _ingest(tmp_path, ctx, store, clock, "snapshot", {"Players": [snap("r1", "Jon", "Doe", "GTD")]})
    left = [e for e in store.load() if e["rotowire_player_id"] == "r5"][-1]
    assert (left["normalized_status"], left["reconciliation"]) == ("NOT_LISTED", "LEFT_CURRENT_LIST")
    fr, _ = RW.availability_frame(store.frame(), GAMES, clock[0] + pd.Timedelta(seconds=1))
    assert fr.set_index("player_id").loc["P5", "status"] == "NO_REPORT"


# ============================================================================ end to end through run.daily
@pytest.fixture(scope="module")
def smoke_state(tmp_path_factory):
    hist = FX.synthetic_history(tmp_path_factory.mktemp("rwhist") / "history.duckdb")
    work = tmp_path_factory.mktemp("rwsmoke") / "w"
    work.mkdir()
    rows, meta = FX.increment_rows(hist)
    ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], work / "state",
              created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    return hist, work, meta


def test_run_daily_conditions_on_rotowire_point_in_time(tmp_path, smoke_state):
    hist, work, meta = smoke_state
    con = duckdb.connect(str(hist), read_only=True)                 # the fixture feed has no roster table: membership
    teams = con.execute("SELECT DISTINCT canonical_team_id, school, display_name FROM core.teams").df()   # = meta
    names = con.execute("SELECT canonical_player_id, any_value(display_name) FROM core.players GROUP BY 1").df()
    con.close()
    nm = dict(names.itertuples(index=False))
    roster = pd.DataFrame([(p, tm, nm[p].split(" ", 1)[0], nm[p].split(" ", 1)[-1], nm[p], "Guard")
                           for tm, ps in meta["roster"].items() for p in ps],
                          columns=["canonical_player_id", "canonical_team_id", "first_name", "last_name", "player_name",
                                   "position"])
    ctx = RW.IdentityContext(teams.assign(mascot=None), roster)
    t = meta["teams"]
    bench, star = meta["roster"][t[0]][-1], meta["roster"][t[1]][0]
    who = roster.set_index("canonical_player_id")
    tname = teams.drop_duplicates("canonical_team_id").set_index("canonical_team_id")["school"]
    rec = lambda uid, p, s: news(uid, f"rw-{p}", who.loc[p, "first_name"], who.loc[p, "last_name"], s,
                                 (FX.AS_OF - pd.Timedelta(hours=2)).isoformat(),
                                 team=(f"t-{who.loc[p, 'canonical_team_id']}", tname[who.loc[p, "canonical_team_id"]]),
                                 pos=who.loc[p, "position"] or "G")
    store, clock = RW.EventStore(tmp_path / "rw"), [FX.AS_OF - pd.Timedelta(minutes=5)]
    s = _session(tmp_path, Fake(news_injuries={"Updates": [rec("u1", bench, "OUT"), rec("u2", star, "GTD")]},
                                snapshot={"Players": []}), clock)
    out = RW.poll(s, ctx, store)
    assert out["news_injuries"]["identity_counts"] == {"RESOLVED": 2}
    kw = dict(state_root=work / "state", history_db=hist, sims=2000)
    base = RN.daily(FX.AS_OF, (), ledger_path=tmp_path / "a.jsonl", run_root=tmp_path / "ra", **kw)
    rw = RN.daily(FX.AS_OF, (), ledger_path=tmp_path / "b.jsonl", run_root=tmp_path / "rb", rotowire_root=store.root,
                  rotowire_ledger=tmp_path / "rw.jsonl", **kw)
    mi = lambda o: pd.read_parquet(Path(o["run_dir"]) / "model_inputs.parquet").set_index(["game_id", "player_id"])
    a, b = mi(base), mi(rw)
    np.testing.assert_array_equal(a["p_play"].to_numpy(), b.loc[a.index, "p_play"].to_numpy())   # model untouched
    assert (b.xs(bench, level="player_id")["player_suppression"] == "CONFIRMED_UNAVAILABLE").all()
    sg = b.xs(star, level="player_id")
    assert (sg["availability_status"] == "GTD").all() and (sg["availability_gate"] == RW.GATE_UNCERTAIN).all()
    assert rw["availability"]["status_counts"] == {"OUT": int((b["availability_status"] == "OUT").sum()),
                                                   "GTD": int((b["availability_status"] == "GTD").sum())}
    star_games = set(sg.index)
    assert all("AVAILABILITY_UNCERTAIN_ROTATION" in rw["game_reasons"][g] for g in star_games if g in rw["game_reasons"])
    assert not any(r.startswith("AVAILABILITY_") for g in base["game_reasons"].values() for r in g)
    led = [json.loads(l)["body"] for l in (tmp_path / "b.jsonl").read_text().splitlines()]
    preds = [p for p in led if p.get("player_id") == star]
    assert preds and all("AVAILABILITY_UNCERTAIN" in p["suppression_reason"] for p in preds)
    assert all(p["readiness_state"] == "BLOCKED" for p in preds)
    fresh = rw["availability"]["freshness"]                         # ~8 h to tip: the policy's 90-min (T6H) bound
    assert fresh and all(d["bound_minutes"] == 90 and d["acquisition_age_minutes"] == 5.0 for d in fresh.values())
    assert not any("AVAILABILITY_STALE" in rw["game_reasons"][g] for g in fresh)
    late = RN.daily(FX.AS_OF + pd.Timedelta(minutes=100), (), ledger_path=tmp_path / "c.jsonl", run_root=tmp_path / "rc",
                    rotowire_root=store.root, rotowire_ledger=tmp_path / "rw.jsonl", **kw)
    lf = late["availability"]["freshness"]
    assert lf and all("AVAILABILITY_STALE" in late["game_reasons"][g] for g in lf)   # 105 min > 90: stale, blocked


# ============================================================================ board policy: PRICED_BUT_NON_ACTIONABLE
def test_gtd_player_is_priced_but_never_actionable(tmp_path, smoke_state):
    hist, work, meta = smoke_state
    t = meta["teams"]
    star, bench = meta["roster"][t[1]][0], meta["roster"][t[0]][-1]           # star: quoted (fixture event U2, PTS)
    con = duckdb.connect(str(hist), read_only=True)
    teams = con.execute("SELECT DISTINCT canonical_team_id, school, display_name FROM core.teams").df()
    names = dict(con.execute("SELECT canonical_player_id, any_value(display_name) FROM core.players GROUP BY 1").fetchall())
    con.close()
    roster = pd.DataFrame([(p, tm, names[p].split(" ", 1)[0], names[p].split(" ", 1)[-1], names[p], "Guard")
                           for tm, ps in meta["roster"].items() for p in ps],
                          columns=["canonical_player_id", "canonical_team_id", "first_name", "last_name", "player_name",
                                   "position"])
    ctx = RW.IdentityContext(teams.assign(mascot=None), roster)
    who = roster.set_index("canonical_player_id")
    tname = teams.drop_duplicates("canonical_team_id").set_index("canonical_team_id")["school"]
    rec = lambda uid, p, s: news(uid, f"rw-{p}", who.loc[p, "first_name"], who.loc[p, "last_name"], s,
                                 (FX.AS_OF - pd.Timedelta(hours=2)).isoformat(),
                                 team=(f"t-{who.loc[p, 'canonical_team_id']}", tname[who.loc[p, "canonical_team_id"]]))
    store, clock = RW.EventStore(tmp_path / "rw"), [FX.AS_OF - pd.Timedelta(minutes=5)]
    RW.poll(_session(tmp_path, Fake(news_injuries={"Updates": [rec("u1", bench, "OUT"), rec("u2", star, "GTD")]},
                                    snapshot={"Players": []}), clock), ctx, store)
    q = FX.quotes_files(hist, meta, tmp_path / "quotes")
    s = RN.daily(FX.AS_OF, q, state_root=work / "state", history_db=hist, ledger_path=tmp_path / "l.jsonl",
                 run_root=tmp_path / "runs", sims=2000, sync_log=tmp_path / "sync.jsonl", odds_cache_root=tmp_path / "oc",
                 odds_collections=None, odds_ledger=tmp_path / "odds.jsonl", odds_record=tmp_path / "odds_record.json",
                 rotowire_root=store.root, rotowire_ledger=tmp_path / "rw.jsonl")
    run_ctx = PB.load_run(s, tmp_path / "l.jsonl", work / "state")
    run_ctx.data_label = "LIVE"                     # test-only relabel to exercise the priced path (FIXTURE never publishes)
    sg = lambda: {"games": {g: {"SCHEDULE_FRESHNESS_GATE": "PASS", "reasons": [], "verified_at": FX.AS_OF.isoformat()}
                            for g in {r["game_id"] for r in run_ctx.records}}}
    b = PB.build_boards(run_ctx, FX.AS_OF, sg(), checkpoint_label="T-RW")
    pub = [r for r in b["public"]["rows"] if r["player_id"] == star]
    assert pub and all(r["status"] == "PUBLISHABLE" and r["projection"] is not None for r in pub)   # projection kept
    assert all(not PB.PRICED_BLOCKED_REASONS & set(r["suppression_reasons"]) and "availability_status" not in r for r in pub)
    assert all(r["status"] == "SUPPRESSED" for r in b["public"]["rows"] if r["player_id"] == bench)    # OUT: suppressed
    star_rows = [r for r in b["internal"]["rows"] if r["player_id"] == star]
    assert star_rows
    for r in star_rows:
        assert r["availability_status"] == "GTD" and r["QUOTE_QUALITY_GATE"] == "PASS"
        assert r["pricing_status"] == "PRICING_ALLOWED" and r["board_state"] == "PRICED_BLOCKED"
        p = r["p_over"] if r["side"] == "OVER" else r["p_under"]
        fair = r["fair_decimal_over"] if r["side"] == "OVER" else r["fair_decimal_under"]
        assert p is not None and fair is not None and r["raw_edge_ev_per_unit"] is not None   # fair price + diagnostic edge
        assert "UNRESOLVED_AVAILABILITY" in r["actionable_block_reasons"]
        assert r["actionable_bet_allowed"] is False and r["stake"] == 0.0
    assert all(r["actionable_bet_allowed"] is False and r["stake"] == 0.0 and r["board_state"] in PB.BOARD_STATES
               and "DISABLED_BY_AVAILABILITY_VALIDATION" in r["actionable_block_reasons"] for r in b["internal"]["rows"])
    assert b["internal"]["real_money_actionability"] == "DISABLED_BY_AVAILABILITY_VALIDATION"
    later = PB.build_boards(run_ctx, FX.AS_OF + pd.Timedelta(minutes=2), sg(), checkpoint_label="T-RW-2")
    assert all(r["board_state"] != "PRICED_ACTIONABLE" and "UNRESOLVED_AVAILABILITY" in r["actionable_block_reasons"]
               for r in later["internal"]["rows"] if r["player_id"] == star)       # the old price is never unlocked


def test_uncertain_to_active_requires_fresh_quote_and_reprice():
    """GTD at run 1; ACT knowable at T2. Neither the run-1 price nor a run-2 price on a pre-ACT quote is actionable; only
    a new run whose quote was posted after the ACT confirmation, with a fresh availability observation, clears the row
    gates (the production global blocks still apply)."""
    T1, T2 = TIP - pd.Timedelta(hours=3), TIP - pd.Timedelta(hours=2)
    T3 = T2 + pd.Timedelta(minutes=15)
    av = lambda at: {"last_success": {"news_injuries": at.isoformat(), "snapshot": at.isoformat()}}
    row = lambda st, recv, qa: {"availability_status": st, "availability_received_at": recv.isoformat(),
                                "quote_available_at": None if qa is None else qa.isoformat()}
    chk = lambda r, codes, cp, aso, obs, g=(): PB.actionable_block_reasons(
        r, priced=True, codes=codes, checkpoint_at=cp, tip=TIP, prediction_as_of=aso, availability=av(obs), global_blocks=g)
    old_quote = T1 - pd.Timedelta(minutes=10)
    run1 = row("GTD", T1 - pd.Timedelta(minutes=30), old_quote)
    assert "UNRESOLVED_AVAILABILITY" in chk(run1, {"AVAILABILITY_UNCERTAIN"}, T1, T1, T1 - pd.Timedelta(minutes=5))
    assert "UNRESOLVED_AVAILABILITY" in chk(run1, {"AVAILABILITY_UNCERTAIN"}, T3, T1, T3)      # same run, later: blocked
    assert chk(row("ACTIVE", T2, old_quote), set(), T3, T3, T3 - pd.Timedelta(minutes=2)) == \
        ["QUOTE_PREDATES_AVAILABILITY_STATE"]                                                # no fresh quote
    assert chk(row("ACTIVE", T2, None), set(), T3, T3, T3) == ["QUOTE_PREDATES_AVAILABILITY_STATE"]
    fresh = row("ACTIVE", T2, T2 + pd.Timedelta(minutes=5))
    assert chk(fresh, set(), T3, T3, T3 - pd.Timedelta(minutes=2)) == []                     # fresh quote + reprice
    assert chk(fresh, set(), T3, T3, T3 - pd.Timedelta(minutes=120)) == ["AVAILABILITY_STALE_AT_CHECKPOINT"]
    assert chk(row("ACTIVE", T3, T3), set(), T3, T3, T3) == ["REPRICE_REQUIRED"]              # state newer than the run
    assert PB.actionable_block_reasons(fresh, priced=False, codes=set(), checkpoint_at=T3, tip=TIP, prediction_as_of=T3,
                                       availability=None, global_blocks=()) == [
        "AVAILABILITY_SOURCE_NOT_CONFIGURED", "NOT_PRICED"]
    g = PB.global_actionable_blocks()
    assert {"AUTOMATIC_ACTIONABLE_WAGER_DISABLED", "BETTING_POLICY_MISSING", "DISABLED_BY_AVAILABILITY_VALIDATION",
            "WAGERING_DISABLED"} <= set(g)
    assert chk(fresh, set(), T3, T3, T3, g) == sorted(g)                                      # production: never actionable
    assert PC.AVAILABILITY_G4B_STATUS == "ACCUMULATING" and PC.REAL_MONEY_ACTIONABILITY == "DISABLED_BY_AVAILABILITY_VALIDATION"
