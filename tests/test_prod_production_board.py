"""Production boards + WizardOfOdds production contract v2 (directive 2026-10-26 §6-§13, §18-§20). FIXTURE data through
the real run.daily path; the board layer only READS the run (the ledger bytes are unchanged). Publication uses the
local staging transport only; the WizardOfOdds transport is PENDING_SITE_CONFIRMATION."""
import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import config as PC, fixtures as FX, production_board as PB, readiness as RD, run as RN, state as ST

REQUIRED_CONDITIONS = (
    "ARTIFACT_HASH_MISMATCH", "WRONG_PRODUCTION_SEASON", "STALE_SCHEDULE", "GAME_IDENTITY_AMBIGUITY",
    "PLAYER_IDENTITY_AMBIGUITY", "UNSUPPORTED_PLAYER_STATUS", "STALE_QUOTE", "ODDS_API_FAILURE", "CBBD_FAILURE",
    "MISSING_STATE", "FAILED_STATE_PROMOTION", "POINT_IN_TIME_VIOLATION", "GAME_ALREADY_STARTED", "SIMULATION_FAILURE",
    "NON_NORMALIZED_PMF", "PUBLICATION_FAILURE", "PUBLICATION_RECONCILIATION_MISMATCH",
    "DUPLICATE_PUBLICATION_CONFLICTING_PAYLOAD")
PRIVATE_ONLY = set(PB.PRIVATE_FIELDS) - set(PB.PUBLIC_FIELDS)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    w = tmp_path_factory.mktemp("board")
    hist = FX.synthetic_history(w / "history.duckdb")
    rows, meta = FX.increment_rows(hist)
    ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], w / "state",
              created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    q = FX.quotes_files(hist, meta, w / "quotes")
    s = RN.daily(FX.AS_OF, q, availability=FX.availability(meta), state_root=w / "state", history_db=hist,
                 ledger_path=w / "ledger.jsonl", run_root=w / "runs", sims=PC.SIMS, sync_log=w / "sync.jsonl",
                 odds_cache_root=w / "oc", odds_collections=None, odds_ledger=w / "odds.jsonl",
                 odds_record=w / "odds_record.json")
    ledger_sha = hashlib.sha256((w / "ledger.jsonl").read_bytes()).hexdigest()
    ctx = PB.load_run(s, w / "ledger.jsonl", w / "state")
    return {"w": w, "summary": s, "ctx": ctx, "ledger_sha": ledger_sha}


def _live(ctx):
    c = copy.copy(ctx)
    c.data_label = "LIVE"            # test-only relabel to exercise the publishable path; FIXTURE is never publishable
    return c


def _sg(ctx, status="PASS", reasons=()):
    return {"games": {g: {"SCHEDULE_FRESHNESS_GATE": status, "reasons": list(reasons), "verified_at": "2026-11-10T15:30:00+00:00"}
                      for g in {r["game_id"] for r in ctx.records}}}


def _boards(run, cp=FX.AS_OF, sg=None, ctx=None, **kw):
    ctx = ctx or _live(run["ctx"])
    return PB.build_boards(ctx, cp, sg if sg is not None else _sg(ctx), checkpoint_label="T-FIXTURE", **kw)


# ---------------------------------------------------------------- contract basics
def test_contract_constants():
    assert PB.DRAWS_PER_GAME == 20_000 == PC.SIMS
    assert PB.SUPPORTED_MARKETS == ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "PRA", "PR", "PA", "RA", "STOCKS")
    assert PB.PUBLISH_TRANSPORT == "PENDING_SITE_CONFIRMATION" and PB.AUTOMATIC_ACTIONABLE_WAGER == "DISABLED"
    assert not PB.PUBLISH_PRIVATE_FIELDS
    for f in ("TS%", "PF", "ORB", "DRB", "FT", "FT%", "2P", "2P%", "3P%"):
        assert f not in PB.SUPPORTED_MARKETS


def test_board_layer_never_alters_the_run(run):
    _boards(run)
    assert hashlib.sha256((run["w"] / "ledger.jsonl").read_bytes()).hexdigest() == run["ledger_sha"]


# ---------------------------------------------------------------- public projection board
def test_public_board_covers_every_player_market_without_lines(run):
    b = _boards(run)["public"]
    keys = {(r["canonical_game_id"], r["player_id"], r["market"]) for r in b["rows"]}
    assert keys == {(r["game_id"], r["player_id"], r["market"]) for r in run["ctx"].records}
    assert {r["market"] for r in b["rows"]} <= set(PB.SUPPORTED_MARKETS)
    quoted = {(r["game_id"], r["player_id"], r["market"]) for r in run["ctx"].records if r.get("sportsbook")}
    unquoted_pub = [r for r in b["rows"] if r["status"] == "PUBLISHABLE"
                    and (r["canonical_game_id"], r["player_id"], r["market"]) not in quoted]
    assert unquoted_pub, "projections must not wait for a sportsbook line"
    for r in b["rows"]:
        assert set(r) == set(PB.PUBLIC_FIELDS) and not set(r) & PRIVATE_ONLY
        if r["status"] == "PUBLISHABLE":
            assert r["projection"] is not None and not r["suppression_reasons"]
        else:
            assert r["projection"] is None and r["suppression_reasons"]
    proj = {(r["game_id"], r["player_id"], r["market"]): r["projection"] for r in run["ctx"].records}
    assert all(r["projection"] == proj[(r["canonical_game_id"], r["player_id"], r["market"])]
               for r in b["rows"] if r["status"] == "PUBLISHABLE")          # the model's number, untouched
    assert b["draws_per_game"] == 20_000 and b["contract_version"] == PB.CONTRACT_VERSION


def test_private_fields_only_when_separately_authorized(run):
    assert set(_boards(run, publish_private_fields=True)["public"]["rows"][0]) >= PRIVATE_ONLY


def test_fixture_data_is_never_publishable(run):
    b = _boards(run, ctx=run["ctx"])["public"]
    assert b["data_label"] != "LIVE" and all(r["status"] == "SUPPRESSED" for r in b["rows"])


# ---------------------------------------------------------------- internal pricing board: fair vs executable
def test_internal_board_separates_fair_and_executable(run):
    rows = _boards(run)["internal"]["rows"]
    assert rows
    for r in rows:
        assert r["price_kind"] == {"fair": "MODEL_FAIR_PRICE", "executable": "SPORTSBOOK_EXECUTABLE_PRICE"}
        fair = r["fair_decimal_over"] if r["side"] == "OVER" else r["fair_decimal_under"]
        if r["sportsbook"] is None:
            assert r["executable_decimal"] is None and r["raw_edge_ev_per_unit"] is None
            assert r["pricing_status"] == "PRICING_SUPPRESSED"
        if r["raw_edge_ev_per_unit"] is not None:                         # edge only vs a real, gated book quote
            assert r["QUOTE_QUALITY_GATE"] == "PASS" and r["executable_decimal"] is not None and fair is not None
        assert r["actionable_bet_allowed"] is False
    assert any(r["pricing_status"] == "PRICING_ALLOWED" and r["raw_edge_ev_per_unit"] is not None for r in rows)


def test_stale_quote_suppresses_pricing_not_projection(run):
    b = _boards(run, cp=FX.AS_OF + pd.Timedelta(hours=3))
    assert all(r["pricing_status"] == "PRICING_SUPPRESSED" and r["raw_edge_ev_per_unit"] is None
               for r in b["internal"]["rows"] if r["sportsbook"])
    assert any("QUOTE_FRESH_AT_CHECKPOINT" in r["failed_quote_gates"] for r in b["internal"]["rows"] if r["sportsbook"])
    assert any(r["status"] == "PUBLISHABLE" for r in b["public"]["rows"])


def test_quote_gates_each_fail_closed():
    tip = pd.Timestamp("2026-11-11T00:30Z")
    good = {"market": "PTS", "line": 17.5, "side": "OVER", "best_decimal_odds": 1.91, "sportsbook": "dk",
            "quote_available_at": "2026-11-10T15:55Z", "collector_received_at": "2026-11-10T15:56Z"}
    assert PB.quote_gates(good, FX.AS_OF, tip, True, True)["QUOTE_QUALITY_GATE"] == "PASS"
    bad = [({"market": "TS%"}, "SUPPORTED_MARKET"), ({"line": 17.3}, "VALID_LINE"), ({"side": "X"}, "VALID_SIDE"),
           ({"best_decimal_odds": 1.0}, "VALID_PRICE"), ({"quote_available_at": "2026-11-10T15:00Z"}, "QUOTE_FRESH_AT_CHECKPOINT"),
           ({"sportsbook": None}, "EXACT_EVENT_MATCH"), ({"collector_received_at": None}, "PROVIDER_RETURNED_QUOTE")]
    for patch, gate in bad:
        g = PB.quote_gates({**good, **patch}, FX.AS_OF, tip, True, True)
        assert g["QUOTE_QUALITY_GATE"] == "FAIL" and gate in g["failed"], gate
    assert "GAME_NOT_STARTED" in PB.quote_gates(good, tip, tip, True, True)["failed"]
    assert "MODEL_HASH_GATE" in PB.quote_gates(good, FX.AS_OF, tip, False, True)["failed"]
    assert "POINT_IN_TIME_GATE" in PB.quote_gates(good, FX.AS_OF, tip, True, False)["failed"]


# ---------------------------------------------------------------- point in time / leakage
def test_point_in_time_pass_on_clean_run(run):
    for g in {r["game_id"] for r in run["ctx"].records}:
        assert PB.point_in_time(run["ctx"], g) == {"POINT_IN_TIME_LEGALITY": "PASS",
                                                   "TARGET_GAME_FEATURE_LEAKAGE_DETECTED": False,
                                                   "TARGET_GAME_OUTCOME_LEAKAGE_DETECTED": False,
                                                   "FUTURE_DATA_LEAKAGE_DETECTED": False}


@pytest.mark.parametrize("kind", ["feature", "outcome", "future"])
def test_any_leakage_flag_blocks_publication_and_pricing(run, monkeypatch, kind):
    ctx = _live(run["ctx"])
    g = sorted({r["game_id"] for r in ctx.records})[0]
    if kind == "feature":
        ctx.summary = {**ctx.summary, "game_reasons": {**ctx.summary["game_reasons"], g: ["FUTURE_LEAKAGE"]}}
        flag = "TARGET_GAME_FEATURE_LEAKAGE_DETECTED"
    elif kind == "outcome":
        monkeypatch.setattr(PB, "outcome_rows_for", lambda db, gid: 1 if gid == g else 0)
        flag = "TARGET_GAME_OUTCOME_LEAKAGE_DETECTED"
    else:
        ctx.summary = {**ctx.summary, "run_reasons": ["POST_CUTOFF_DATA"]}
        flag = "FUTURE_DATA_LEAKAGE_DETECTED"
    b = PB.build_boards(ctx, FX.AS_OF, _sg(ctx), checkpoint_label="T")
    st = b["game_status"][g]
    assert st[flag] is True and st["POINT_IN_TIME_LEGALITY"] == "FAIL"
    assert not st["publication_allowed"] and not st["pricing_allowed"] and not st["actionable_bet_allowed"]
    assert all(r["status"] == "SUPPRESSED" for r in b["public"]["rows"] if r["canonical_game_id"] == g)
    assert all(r["pricing_status"] == "PRICING_SUPPRESSED" for r in b["internal"]["rows"] if r["canonical_game_id"] == g)


# ---------------------------------------------------------------- schedule freshness + post-tip
def test_schedule_freshness_fail_suppresses_everything(run):
    ctx = _live(run["ctx"])
    b = PB.build_boards(ctx, FX.AS_OF, _sg(ctx, "FAIL", ["SCHEDULE_STALE_AFTER_REFRESH"]), checkpoint_label="T")
    assert all(r["status"] == "SUPPRESSED" and "SCHEDULE_STALE_AFTER_REFRESH" in r["suppression_reasons"]
               for r in b["public"]["rows"])
    assert all(r["pricing_status"] == "PRICING_SUPPRESSED" for r in b["internal"]["rows"])
    b2 = PB.build_boards(ctx, FX.AS_OF, {"games": {}}, checkpoint_label="T")   # not evaluated -> FAIL
    assert all(r["status"] == "SUPPRESSED" for r in b2["public"]["rows"])


def test_no_publication_after_tip(run, tmp_path):
    b = _boards(run, cp=FX.UPCOMING_TIP)
    assert all(r["status"] == "SUPPRESSED" and "GAME_ALREADY_STARTED" in r["suppression_reasons"] for r in b["public"]["rows"])
    ok = _boards(run)["public"]
    with pytest.raises(PB.PublicationRejected, match="GAME_ALREADY_STARTED"):
        PB.publish(ok, PB.LocalStagingTransport(tmp_path / "stg"), tmp_path / "r.jsonl", now=FX.UPCOMING_TIP)


# ---------------------------------------------------------------- idempotent publication (local staging only)
def test_idempotent_replay_supersession_and_conflict(run, tmp_path):
    t = PB.LocalStagingTransport(tmp_path / "stg")
    rp = tmp_path / "receipts.jsonl"
    a = _boards(run)["public"]
    a_later = _boards(run, cp=FX.AS_OF + pd.Timedelta(minutes=5))["public"]
    assert a["idempotency_key"] == a_later["idempotency_key"]                 # same content later = replay
    r1 = PB.publish(a, t, rp, now=FX.AS_OF)
    assert r1["status"] == "PUBLISHED" and r1["version"] == 1 and r1["read_back"] == "MATCH"
    assert PB.publish(a_later, t, rp, now=FX.AS_OF)["status"] == "DUPLICATE_NOOP"
    changed = copy.deepcopy(a)
    i = next(i for i, r in enumerate(changed["rows"]) if r["status"] == "PUBLISHABLE")
    changed["rows"][i] = {**changed["rows"][i], "status": "SUPPRESSED", "projection": None,
                          "suppression_reasons": ["CONFIRMED_UNAVAILABLE"]}
    changed = PB.envelope({k: v for k, v in changed.items() if k not in ("publication_id", "payload_sha256", "idempotency_key")})
    assert changed["publication_id"] == a["publication_id"] and changed["idempotency_key"] != a["idempotency_key"]
    r2 = PB.publish(changed, t, rp, now=FX.AS_OF)
    assert r2["status"] == "SUPERSEDES" and r2["version"] == 2 and r2["supersedes"] == a["idempotency_key"]
    assert t.read_back(a["publication_id"], a["idempotency_key"])["payload_sha256"] == a["payload_sha256"]   # v1 kept
    tampered = {**changed, "rows": changed["rows"][:-1]}
    with pytest.raises(PB.PublicationRejected, match="DUPLICATE_PUBLICATION_CONFLICTING_PAYLOAD"):
        PB.publish(tampered, t, rp, now=FX.AS_OF)
    statuses = [json.loads(l)["status"] for l in rp.read_text().splitlines()]
    assert statuses == ["PUBLISHED", "DUPLICATE_NOOP", "SUPERSEDES"]


def test_transport_failure_then_retry_and_reconciliation_mismatch(run, tmp_path):
    a = _boards(run)["public"]
    rp = tmp_path / "r.jsonl"

    class Flaky(PB.LocalStagingTransport):
        n = 0

        def send(self, payload):
            Flaky.n += 1
            if Flaky.n == 1:
                raise ConnectionError("down")
            return super().send(payload)
    t = Flaky(tmp_path / "stg")
    with pytest.raises(PB.PublicationRejected, match="PUBLICATION_FAILURE"):
        PB.publish(a, t, rp, now=FX.AS_OF)
    assert PB.publish(a, t, rp, now=FX.AS_OF)["status"] == "PUBLISHED"       # retry is safe

    class Liar(PB.LocalStagingTransport):
        def read_back(self, pid, key):
            return {"payload_sha256": "0" * 64}
    with pytest.raises(PB.PublicationRejected, match="RECONCILIATION_MISMATCH"):
        PB.publish(a, Liar(tmp_path / "stg2"), tmp_path / "r2.jsonl", now=FX.AS_OF)


def test_invalid_or_empty_board_rejected(run, tmp_path):
    b = _boards(run, ctx=run["ctx"])["public"]                                # FIXTURE: every row suppressed
    with pytest.raises(PB.PublicationRejected, match="NOTHING_PUBLISHABLE"):
        PB.publish(b, PB.LocalStagingTransport(tmp_path), tmp_path / "r.jsonl", now=FX.AS_OF)


def test_production_transport_is_pending(run, tmp_path):
    with pytest.raises(PB.TransportPending):
        PB.publish(_boards(run)["public"], PB.PendingSiteConfirmationTransport(), tmp_path / "r.jsonl", now=FX.AS_OF)
    assert not (tmp_path / "r.jsonl").exists() or "PUBLISHED" not in (tmp_path / "r.jsonl").read_text()


# ---------------------------------------------------------------- betting policy + matrix + readiness
def test_betting_policy_required_and_wager_disabled(tmp_path):
    p = PB.betting_policy(tmp_path / "absent.json")
    assert p["ARCHITECT_BETTING_POLICY_REQUIRED"] and set(p["missing_fields"]) == set(PB.BETTING_POLICY_FIELDS)
    full = tmp_path / "full.json"
    full.write_text(json.dumps({f: "x" for f in PB.BETTING_POLICY_FIELDS}))
    p2 = PB.betting_policy(full)
    assert not p2["ARCHITECT_BETTING_POLICY_REQUIRED"] and p2["AUTOMATIC_ACTIONABLE_WAGER"] == "DISABLED"


def test_fail_closed_matrix_complete():
    assert set(REQUIRED_CONDITIONS) <= set(PB.FAIL_CLOSED_MATRIX)
    for c, (pub, price, bet, retry, alert) in PB.FAIL_CLOSED_MATRIX.items():
        assert bet is False, c                                                 # no actionable bet in this phase
    for c in RD.KNOWN:                                                         # every run code has a defined outcome
        assert c in PB.CODE_CONDITION, c
    assert PB.condition_of("SOME_NEW_UNKNOWN_CODE") == (False, False, False, False, True)
    assert PB.condition_of("NO_VALID_QUOTE")[0] is True and PB.condition_of("NO_VALID_QUOTE")[1] is False


def test_readiness_only_when_every_gate_passes():
    assert PB.production_readiness({"a": "PASS", "b": "PASS"})["CBB_ORIGINATOR_PRODUCTION_READINESS"] == \
        "READY_FOR_REAL_2026_27_PREDICTION_AND_PRICING"
    r = PB.production_readiness({"a": "PASS", "WIZARD": "PENDING_SITE_CONFIRMATION"})
    assert r["CBB_ORIGINATOR_PRODUCTION_READINESS"] == "NOT_READY" and r["failing"] == ["WIZARD"]
    assert PB.production_readiness({})["CBB_ORIGINATOR_PRODUCTION_READINESS"] == "NOT_READY"
