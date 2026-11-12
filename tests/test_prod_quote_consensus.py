"""The Odds API NCAAB player-prop integration: 12 market keys, event endpoints, market discovery before event odds,
quota headers, the consensus Board (one row per GAME x PLAYER x STAT, no-quote rows kept blank), consensus rules
(median line, zero MAD, stale-move protection, exact-line pricing, existing de-vig, median fair probabilities), content
hashes (a quote-only change never rewrites Distributions) and the persistent certification gate.

Fixtures only (fake HTTP, temporary roots, dummy credential). No network, no model run, no PMF regeneration.
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import (config as PC, consensus as CN, exports as EX, odds_acquire as OA, odds_feed as OF,
                               odds_fixtures as OX, pmf_evidence as PE, quote_cert as QC, quote_obs as QO, quotes as QT)

TWELVE = ("player_points", "player_rebounds", "player_assists", "player_threes", "player_steals", "player_blocks",
          "player_turnovers", "player_points_rebounds_assists", "player_points_rebounds", "player_points_assists",
          "player_rebounds_assists", "player_blocks_steals")
T0 = pd.Timestamp("2026-11-12T20:00:00Z")
TIP = "2026-11-13T00:00:00Z"
DATE = "2026-11-12"


# ============================================================================ 1-5 acquisition
def test_1_twelve_exact_market_keys_and_sport_key():
    assert QT.SPORT_KEY == "basketball_ncaab" and OA.SPORT == "basketball_ncaab"
    assert set(QT.REQUEST_MARKETS) == set(TWELVE) and len(QT.REQUEST_MARKETS) == 12
    assert not any(k.endswith("_alternate") for k in QT.REQUEST_MARKETS)
    assert {QT.KEY_TO_STAT[k] for k in TWELVE} == set(PC.MARKETS)            # every forecast stat has its key


def test_2_event_specific_endpoints_only():
    assert OA.PATHS == {"events": "/v4/sports/basketball_ncaab/events",
                        "event_markets": "/v4/sports/basketball_ncaab/events/{eventId}/markets",
                        "event_odds": "/v4/sports/basketball_ncaab/events/{eventId}/odds"}
    assert "/v4/sports/basketball_ncaab/odds" not in OA.PATHS.values()       # no sport-level featured-market call
    with pytest.raises(OA.OddsGuardViolation):
        OA.check_request("sport_odds", {})


def _session(tmp_path, fake, t=T0):
    s = OA.Session("t", cache_root=tmp_path / "raw", ledger_path=tmp_path / "ledger.jsonl", http_get=fake,
                   credential=lambda: OX.DUMMY_ODDS_CREDENTIAL, clock=lambda: t.to_pydatetime(), sleep=lambda *_: None)
    OF.fetch_events(s, t, t + pd.Timedelta(hours=36))
    s.event_commence = OF.event_commence_resolver(s.cache_root, t)
    return s


def _fake(with_props=True):
    ev = OX.event("e1", "Home U", "Away U", TIP)
    ev2 = OX.event("e2", "Home V", "Away V", TIP)
    upd = "2026-11-12T19:55:00Z"
    odds = {"e1": OX.event_odds(ev, [
        OX.book("draftkings", "DraftKings", [OX.market("player_points", OX.ou("Ann A", 14.5, -110, -110), upd),
                                             OX.market("player_rebounds", OX.ou("Ann A", 5.5, -115, -105), upd)]),
        OX.book("fanduel", "FanDuel", [OX.market("player_points", OX.ou("Ann A", 14.5, -105, -115), upd)])])}
    return OX.FakeOdds([ev, ev2], odds if with_props else {})


def test_3_4_discovery_first_and_only_advertised_markets_requested(tmp_path):
    fake = _fake()
    s = _session(tmp_path, fake)
    p = OF.collect(s, ["e1"], tmp_path / "coll")[-1]
    paths = [u[len(OA.BASE):] for u, _ in fake.calls]
    assert paths[1:] == ["/v4/sports/basketball_ncaab/events/e1/markets", "/v4/sports/basketball_ncaab/events/e1/odds"]
    odds_params = fake.calls[-1][1]
    assert odds_params["markets"] == "player_points,player_rebounds"         # the advertised subset, production order
    assert odds_params["regions"] == "us" and "apiKey" in odds_params
    doc = json.loads(p.read_text())
    assert doc["market_discovery"]["requested_markets"] == ["player_points", "player_rebounds"]
    assert doc["market_discovery"]["bookmakers_advertising"] == 2


def test_4b_no_supported_market_means_no_event_odds_call(tmp_path):
    fake = _fake(with_props=False)
    s = _session(tmp_path, fake)
    p = OF.collect(s, ["e2"], tmp_path / "coll")[-1]
    assert not any(u.endswith("/odds") for u, _ in fake.calls)              # no useless prop request
    rc = OF.collection_receipt(p)
    assert rc["discovery_status"] == "NO_SUPPORTED_PLAYER_PROP_MARKETS" and rc["credits"] == 1 and rc["odds_status"] is None
    assert len(QT.load_collected(p)) == 0                                     # normalizes to zero quotes
    with pytest.raises(OA.OddsGuardViolation):                                # a market outside the 12 is refused
        OA.check_request("event_odds", {"eventId": "e1", "regions": "us", "markets": "player_points_alternate",
                                        "oddsFormat": "american", "dateFormat": "iso"}, s.event_commence, T0)


def test_5_quota_headers_recorded_for_every_live_request(tmp_path):
    fake = _fake()
    s = _session(tmp_path, fake)
    p = OF.collect(s, ["e1"], tmp_path / "coll")[-1]
    live = [e for e in OA.ledger(tmp_path / "ledger.jsonl") if e["status"] == "FETCHED"]
    assert [e["endpoint"] for e in live] == ["events", "event_markets", "event_odds"]
    for e in live:
        assert e["quota_remaining"] is not None and e["quota_used"] is not None and e["cost"] is not None
    assert [e["cost"] for e in live] == [0, 1, 2]                              # x-requests-last per request
    rc = OF.collection_receipt(p)
    assert rc["credits"] == 3 and rc["quota_remaining"] == fake.remaining and rc["quota_used"] == fake.used
    again = OF.collect(s, ["e1"], tmp_path / "coll")[-1]                     # inside 10 min: cache, never re-bought
    assert OF.collection_receipt(again)["markets_status"] == "CACHE_HIT" and OF.collection_receipt(again)["credits"] == 0


# ============================================================================ consensus rules
def _obs(book, line, over, under, *, player="P1", stat="PTS", upd="2026-11-12T19:55:00Z", rx="2026-11-12T20:00:00Z",
         game="G1", status="VALID", reason=""):
    out = []
    for side, a in (("OVER", over), ("UNDER", under)):
        if a is None:
            continue
        out.append({"provider_event_id": "e1", "sportsbook": book, "market_key": "player_points", "is_alternate": False,
                    "provider_player_name": player, "stat": stat, "line": line, "side": side, "american_odds": a,
                    "decimal_odds": QT.american_to_decimal(a), "market_last_update": pd.Timestamp(upd),
                    "bookmaker_last_update": pd.Timestamp(upd), "quote_available_at": pd.Timestamp(upd),
                    "collector_received_at": pd.Timestamp(rx), "canonical_game_id": game, "canonical_player_id": player,
                    "quote_status": status, "suppression_reason": reason})
    return out


def _cons(rows):
    return CN.consensus(pd.DataFrame(rows))


def test_9_median_line_ordinary_median():
    m, _ = _cons(_obs("a", 14.5, -110, -110) + _obs("b", 15.5, -110, -110) + _obs("c", 15.5, -105, -115))
    assert m["consensus_line"] == 15.5 and m["consensus_book_count"] == 2 and m["raw_book_count"] == 3
    m, _ = _cons(_obs("a", 14.5, -110, -110) + _obs("b", 15.5, -110, -110))
    assert m["consensus_line"] == 15.0 and m["market_status"] == "NO_EXACT_LINE_PRICE"   # never an invented price
    assert m["market_over_prob"] is None and m["consensus_confidence"] == "LOW"


def test_10_zero_mad_cluster_excludes_isolated_line():
    rows = sum((_obs(b, 20.5, -110, -110) for b in "abcd"), []) + _obs("e", 23.5, -110, -110)
    m, aud = _cons(rows)
    assert m["consensus_line"] == 20.5 and m["outlier_book_count"] == 1 and m["raw_book_count"] == 4
    assert {a["reason"] for a in aud if a["sportsbook"] == "e"} == {"LINE_OUTLIER"}
    small = sum((_obs(b, 20.5, -110, -110) for b in "abc"), []) + _obs("e", 23.5, -110, -110)
    m, aud = _cons(small)                                                     # 3-4 books: never excluded on distance
    assert m["outlier_book_count"] == 0 and m["raw_book_count"] == 4 and m["consensus_line"] == 20.5
    assert {a["status"] for a in aud if a["sportsbook"] == "e"} == {"INCLUDED_OTHER_LINE"}


def test_11_coordinated_newer_move_is_not_an_outlier():
    old = sum((_obs(b, 20.5, -110, -110, upd="2026-11-12T18:00:00Z") for b in "abc"), [])
    new = sum((_obs(b, 22.5, -110, -110, upd="2026-11-12T19:58:00Z") for b in "de"), [])
    m, aud = _cons(old + new)
    assert m["consensus_line"] == 22.5 and m["consensus_books"] == ["d", "e"]
    assert {a["reason"] for a in aud if a["sportsbook"] in "abc"} == {"SUPERSEDED_QUOTE"}
    for a in aud:
        assert a["quote_age_seconds"] is not None and a["collector_received_at"] is not None


def test_12_different_lines_never_probability_averaged():
    rows = _obs("a", 14.5, -110, -110) + _obs("b", 16.5, -110, -110) + _obs("c", 14.5, 100, -120)
    m, aud = _cons(rows)
    a = EX.no_vig("OVER", QT.american_to_decimal(-110), QT.american_to_decimal(-110))
    c = EX.no_vig("OVER", QT.american_to_decimal(100), QT.american_to_decimal(-120))
    assert m["consensus_line"] == 14.5 and m["consensus_books"] == ["a", "c"]
    assert m["market_over_prob"] == pytest.approx(float(np.median([a, c])))
    assert {x["status"] for x in aud if x["sportsbook"] == "b"} == {"INCLUDED_OTHER_LINE"}


def test_13_existing_devig_is_the_one_used(monkeypatch):
    seen = []
    real = EX.no_vig

    def spy(side, d1, d2):
        seen.append(side)
        return real(side, d1, d2)
    monkeypatch.setattr(EX, "no_vig", spy)
    _cons(_obs("a", 14.5, -120, 100))
    assert seen == ["OVER", "UNDER"]                                          # paired prices de-vigged before consensus


def test_14_consensus_is_median_of_fair_probabilities():
    books = [("a", -110, -110), ("b", -130, 110), ("c", -150, 125)]
    m, _ = _cons(sum((_obs(b, 14.5, o, u) for b, o, u in books), []))
    fair = [EX.no_vig("OVER", QT.american_to_decimal(o), QT.american_to_decimal(u)) for _, o, u in books]
    assert m["market_over_prob"] == pytest.approx(float(np.median(fair)))
    assert m["market_under_prob"] == pytest.approx(1 - float(np.median(fair)))
    raw_avg = float(np.mean([1 / QT.american_to_decimal(o) for _, o, _ in books]))
    assert m["market_over_prob"] != pytest.approx(raw_avg)                   # never vigged / raw averages
    assert m["consensus_over_odds"] == pytest.approx(CN.display_american(m["market_over_prob"]))
    assert m["consensus_confidence"] == "MEDIUM"


def test_audit_reasons_no_silent_exclusion():
    rows = (_obs("a", 14.5, -110, -110) + _obs("a", 14.5, -110, -110, upd="2026-11-12T19:59:00Z")   # duplicate
            + _obs("b", 14.5, -110, None)                                                          # unpaired
            + _obs("c", 14.5, -110, -110, status="SUPPRESSED", reason="STALE_QUOTE")
            + _obs("d", 14.5, -110, -110, status="INVALID", reason="INVALID_QUOTE"))
    m, aud = _cons(rows)
    reasons = sorted(a["reason"] for a in aud if a["status"] == "EXCLUDED")
    assert reasons == ["DUPLICATE_BOOK_OBSERVATION", "DUPLICATE_BOOK_OBSERVATION", "MALFORMED_MARKET", "MALFORMED_MARKET",
                       "STALE_QUOTE", "STALE_QUOTE", "UNPAIRED_PRICE"]
    assert len(aud) == len(rows) and m["consensus_book_count"] == 1 and m["consensus_confidence"] == "SINGLE_BOOK"


def test_confidence_labels_deterministic():
    assert CN._confidence(0, 0, None) == "NO_MARKET" and CN._confidence(1, 1, None) == "SINGLE_BOOK"
    assert CN._confidence(2, 2, None) == "LOW" and CN._confidence(3, 3, None) == "MEDIUM"
    assert CN._confidence(4, 4, None) == "HIGH" and CN._confidence(4, 4, 7200) == "MEDIUM"
    assert CN._confidence(6, 2, None) == "LOW"


# ============================================================================ 6-8 Board universe
def _pmf(lam, k=60):
    p = np.array([math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1)) for i in range(k)])
    p[-1] = max(0.0, 1 - p[:-1].sum())
    return p


def _pmfs():
    out = []
    for pid, team in (("P1", "T1"), ("P2", "T2")):
        for stat, lam in (("PTS", 15.0), ("REB", 5.0), ("PRA", 25.0)):
            out.append({"game_id": "G1", "player_id": pid, "team_id": team, "stat": stat, "mean": lam, "pmf": _pmf(lam),
                        "game_label": "Away @ Home", "player_label": pid, "tip": pd.Timestamp(TIP),
                        "cutoff": pd.Timestamp(TIP) - pd.Timedelta(hours=1), "source_kind": "FORECAST_SNAPSHOT",
                        "forecast_id": "F1", "model_version": "M"})
    return out


def test_6_7_8_one_row_per_key_no_quote_rows_kept_blank():
    q = pd.DataFrame(_obs("a", 14.5, -110, -110) + _obs("a", 14.5, -110, -110) + _obs("b", 14.5, -105, -115)
                     + _obs("c", 15.5, -110, -110))                           # duplicate sportsbook observations
    rows, aud, cnt = EX.board_rows(_pmfs(), {"G1": ({"received_at": "x"}, q)}, {"T1": "Home", "T2": "Away"})
    assert cnt["rows"] == 6 and cnt["unique_keys"] == 6 and cnt["duplicate_keys"] == 0
    keys = [(r[1], r[5], r[8]) for r in rows]
    assert len(set(keys)) == len(keys) == 6                                   # sportsbook rows never multiply a key
    col = {c: i for i, c in enumerate(EX.BOARD_COLS)}
    quoted = [r for r in rows if r[col["Canonical Player ID"]] == "P1" and r[col["Stat"]] == "PTS"][0]
    assert quoted[col["Market Status"]] == "VALID_MARKET" and quoted[col["Consensus Line"]] == 14.5
    assert quoted[col["Raw Book Count"]] == 3 and quoted[col["Consensus Book Count"]] == 2
    assert quoted[col["Model Over %"]] is not None and quoted[col["Over Edge"]] is not None
    assert quoted[col["Opponent"]] == "Away" and quoted[col["Team"]] == "Home"
    blank = ("Consensus Line", "Model Over %", "Market Over %", "Over Edge", "Consensus Over Odds", "Model Under %",
             "Market Under %", "Under Edge", "Consensus Under Odds", "Preferred Side", "Preferred EV")
    for r in rows:
        if r is quoted:
            continue
        assert r[col["Market Status"]] == "NO_VALID_MARKET" and r[col["Raw Book Count"]] == 0
        assert r[col["Consensus Book Count"]] == 0 and r[col["Consensus Confidence"]] == "NO_MARKET"
        assert all(r[col[c]] is None for c in blank) and r[col["Model Projection"]] is not None
    assert sum(1 for a in aud if a["reason"] == "DUPLICATE_BOOK_OBSERVATION") == 2


def test_board_duplicate_pmf_key_aborts_export(tmp_path):
    rows, _, cnt = EX.board_rows(_pmfs() + _pmfs()[:1], {}, {})
    assert cnt["duplicate_keys"] == 1


# ============================================================================ 15 content hashes through daily()
def _snapshot(ops: Path, snap="F20261112T120000Z-aaaaaa", aso="2026-11-12T12:00:00+00:00"):
    d = ops / "forecasts" / DATE / snap
    rows = []
    for r in _pmfs():
        rid = PE.receipt_id("R1", r["game_id"], r["player_id"], r["stat"])
        rows.append(pd.DataFrame({"prediction_receipt_id": rid, "game_id": r["game_id"], "player_id": r["player_id"],
                                  "team_id": r["team_id"], "stat": r["stat"], "prediction_as_of": aso, "model_version": "M",
                                  "outcome": np.arange(len(r["pmf"]), dtype=np.int64), "probability": r["pmf"],
                                  "mean": r["mean"], "run_id": "R1", "method": "BANK_MIXTURE_PMF",
                                  "game_label": "Away @ Home", "player_label": r["player_id"]}))
    PE.write(d / PE.FILE, pd.concat(rows, ignore_index=True))
    (d / "snapshot.json").write_text(json.dumps({"scheduled_tips": {"G1": TIP}}))
    idx = ops / "forecasts" / "index.jsonl"
    idx.parent.mkdir(parents=True, exist_ok=True)
    with idx.open("a") as f:
        f.write(json.dumps({"slate_date": DATE, "status": "FORECAST", "prediction_as_of": aso, "snapshot_id": snap,
                            "games": ["G1"], "cutoffs": {"G1": "2026-11-12T23:00:00+00:00"}}) + "\n")


def _daily(tmp_path, now):
    return EX.daily(DATE, ops_root=tmp_path / "ops", runs_root=tmp_path / "runs", out_root=tmp_path / "exports",
                    history_db=None, state_root=None, labels=("TEST",), now=now)


def test_15_quote_only_change_never_rewrites_distributions(tmp_path):
    _snapshot(tmp_path / "ops")
    r1 = _daily(tmp_path, "2026-11-12T19:00:00Z")
    assert r1["distributions"]["status"] == "WRITTEN" and r1["distributions"]["rewritten"] is True
    assert r1["boards"]["status"] == "WRITTEN" and r1["boards"]["rows"] == 6 and r1["boards"]["rows_with_valid_market"] == 0
    assert r1["grades"]["status"] == "WRITTEN" and r1["grades"]["GRADE_STATUS"] == "NOTHING_SETTLED_YET"
    wb = EX.read_xlsx(Path(r1["grades"]["path"]))
    assert wb["Status"]["rows"][1][0] == "NOTHING_SETTLED_YET"
    r2 = _daily(tmp_path, "2026-11-12T19:10:00Z")                            # nothing changed: nothing rewritten
    assert (r2["distributions"]["rewritten"], r2["boards"]["rewritten"], r2["grades"]["rewritten"]) == (False, False, False)
    QO.record(tmp_path / "ops", DATE, "G1", "e1", "2026-11-12T20:00:00Z",
              pd.DataFrame(_obs("a", 14.5, -110, -110) + _obs("b", 14.5, -105, -115)), "QUOTE_REFRESH")
    r3 = _daily(tmp_path, "2026-11-12T20:05:00Z")                            # quote-only change
    assert r3["distributions"]["rewritten"] is False and r3["boards"]["rewritten"] is True
    assert r3["boards"]["rows"] == 6 and r3["boards"]["rows_with_valid_market"] == 1 and r3["boards"]["duplicate_keys"] == 0
    full = EX.read_xlsx(Path(r3["boards"]["path"]))["Full Board"]["rows"]
    assert full[0] == list(EX.BOARD_COLS) and len(full) == 7
    with gzip.open(r3["boards"]["audit"], "rt") as f:
        aud = [json.loads(x) for x in f]
    assert {a["status"] for a in aud} == {"INCLUDED"} and len(aud) == 4
    r4 = _daily(tmp_path, "2026-11-12T20:15:00Z")
    assert r4["boards"]["rewritten"] is False and r4["distributions"]["rewritten"] is False
    QO.record(tmp_path / "ops", DATE, "G1", "e1", "2026-11-12T23:10:00Z",          # after the cutoff: never used
              pd.DataFrame(_obs("a", 18.5, -110, -110, rx="2026-11-12T23:10:00Z")), "QUOTE_REFRESH")
    r5 = _daily(tmp_path, "2026-11-12T23:20:00Z")
    assert r5["boards"]["rewritten"] is False


# ============================================================================ 16-17 certification gate
def _receipt(at="2026-11-12T20:00:00+00:00"):
    return {"status": "PASS", "validated_at": at, "event_id": "e1", "canonical_game_id": "G1"}


def test_16_certification_persists_after_pass(tmp_path):
    m = tmp_path / "markers"
    assert not QC.state(m)["certified"]
    QC.certify(m, _receipt())
    assert (m / QC.FIRST_PASS).exists()
    for later in ("2026-11-12T21:00:00Z", "2026-11-20T12:00:00Z", "2027-03-01T12:00:00Z"):
        assert QC.state(m, later)["certified"]                               # no daily re-certification, no expiry
    assert not QC.state(m, "2026-11-12T19:00:00Z")["certified"]              # not before it happened
    one_bad = [{"endpoint": "event_odds", "status": "FAILED", "http_status": 500},
               {"endpoint": "event_odds", "status": "ATTEMPT_FAILED", "http_status": 429}]
    assert QC.material_failure(one_bad) is None                              # transient: not material
    assert QC.check_session(m, one_bad, "2026-11-13T00:00:00Z") is None and QC.state(m)["certified"]


def test_17_material_invalidation_recloses_certification(tmp_path, monkeypatch):
    m = tmp_path / "markers"
    QC.certify(m, _receipt())
    inv = QC.check_session(m, [{"endpoint": "event_odds", "status": "REFUSED_RESPONSE_GUARD", "error": "schema"}],
                           "2026-11-13T00:00:00Z")
    assert inv["reason"] == "PROVIDER_SCHEMA_CHANGE"
    st = QC.state(m, "2026-11-13T01:00:00Z")
    assert not st["certified"] and st["reason"] == "INVALIDATED_PROVIDER_SCHEMA_CHANGE"
    QC.certify(m, _receipt("2026-11-13T02:00:00+00:00"))                     # a new real PASS re-opens it
    assert QC.state(m, "2026-11-13T03:00:00Z")["certified"]
    assert QC.material_failure([{"endpoint": "event_markets", "status": "FAILED", "http_status": 401}])[0] == "CREDENTIAL_FAILURE"
    monkeypatch.setattr(QC, "fingerprint", lambda: "changed-mapping-or-pricing")
    assert QC.state(m)["reason"] == "INVALIDATED_INTEGRATION_CHANGED" and not QC.state(m)["certified"]
    with pytest.raises(ValueError):
        QC.invalidate(m, "ONE_BAD_QUOTE", "x", "2026-11-13T04:00:00Z")      # not a material reason


def test_run_gate_record_follows_fingerprint(tmp_path, monkeypatch):
    rec = tmp_path / "integration_validation.json"
    rec.write_text(json.dumps({"status": "PASS", "validated_at": "2026-11-12T20:00:00+00:00",
                               "integration_fingerprint": QC.fingerprint()}))
    assert OF.integration_validated(rec, "2026-11-12T21:00:00Z") is not None
    monkeypatch.setattr(QC, "fingerprint", lambda: "other")
    assert OF.integration_validated(rec, "2026-11-12T21:00:00Z") is None


def test_certification_ranking_is_deterministic():
    from cbb_platform.prod import quote_refresh as QR
    d = [{"event_id": "a", "game_id": "G3", "tip": "2026-11-12T23:00Z", "supported_market_count": 3, "bookmakers_advertising": 2},
         {"event_id": "b", "game_id": "G2", "tip": "2026-11-12T22:00Z", "supported_market_count": 5, "bookmakers_advertising": 1},
         {"event_id": "c", "game_id": "G1", "tip": "2026-11-12T21:00Z", "supported_market_count": 5, "bookmakers_advertising": 4},
         {"event_id": "d", "game_id": "G0", "tip": "2026-11-12T20:30Z", "supported_market_count": 0, "bookmakers_advertising": 0},
         {"event_id": "e", "game_id": "G4", "tip": "2026-11-12T20:00Z", "supported_market_count": 5, "bookmakers_advertising": 4}]
    assert [x["event_id"] for x in QR.rank(d)] == ["e", "c", "b", "a"]
    assert [x["event_id"] for x in QR.rank(list(reversed(d)))] == ["e", "c", "b", "a"]
