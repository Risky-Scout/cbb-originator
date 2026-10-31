"""Walk-forward FINALIZATION (architect 2026-10-31, items B / D / E / F / G). CI-safe unless marked local:
    B  the scoped, exception-safe, exclusive 2026-27 season guard over the frozen structural module
    D  box-level correction detection: semantic equality, material changes only
    E  run-id deduplication tied to every material input
    F  explicit scorer model source: FROZEN_OPENING_BASELINE exact on the golden slate, ACTIVE_DAILY verified,
       deterministic, fully provenanced, fail closed
    G  near-tip (T-10m) quote snapshot through the existing acquisition path; CLV vs that proxy
"""
from __future__ import annotations

import json
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import (config as PC, fixtures as FX, ledger as LG, loader as LD, model_source as MS,
                               odds_acquire as OA, odds_feed as OF, odds_fixtures as OX, run as RN, side_monitor as SD)
from cbb_platform.v2 import marginal as M, provenance as PV, structural as S
from cbb_platform.walkforward import (config as WC, corrections as CR, daily as DL, fit as WF, monitor as MON,
                                      near_tip as NT, store as SO)
from tests import test_prod_golden_equivalence as G
from tests.test_walkforward_units import _daily_copy

LOCAL = pytest.mark.skipif(not (PV.LB_FEATURES.exists() and PC.HISTORY_CANONICAL.exists()), reason="frozen local stores not present")


# ============================================================================ B. season guard
def test_guard_is_scoped_and_restored_after_success():
    assert S.MAX_SEASON == WF.FROZEN_MAX_SEASON == 2026
    with WF.production_season_guard():
        assert S.MAX_SEASON == WC.PRODUCTION_SEASON == 2027 and WF.guard_state()["guard_active"]
    assert S.MAX_SEASON == 2026 and WF.guard_state()["RESTORED"]


def test_guard_is_restored_after_an_exception_inside_the_fit(monkeypatch):
    seen = {}

    def boom(*a, **k):
        seen["inside"] = S.MAX_SEASON
        raise RuntimeError("injected fit failure")
    monkeypatch.setattr(WF, "inputs", lambda cfg, new: {"T": 2027, "frame": None, "finals": None, "part": None,
                                                         "team": None, "store": None})
    monkeypatch.setattr(M, "fit_v0", boom)
    with pytest.raises(RuntimeError, match="injected fit failure"):
        WF.fit({}, None)
    assert seen["inside"] == 2027                                  # the production value existed only inside the fit
    assert S.MAX_SEASON == 2026 and WF.guard_state()["RESTORED"]


def test_guard_success_path_through_fit_restores(monkeypatch):
    seen = {}
    e = pd.DataFrame()
    monkeypatch.setattr(WF, "inputs", lambda cfg, new: {"T": 2027, "frame": e, "finals": e, "part": e, "team": e, "store": e})
    monkeypatch.setattr(M, "fit_v0", lambda *a, **k: seen.setdefault("v0", S.MAX_SEASON) and {})
    monkeypatch.setattr(M, "stl_policy", lambda st, cfg: {"v2": {"weight_by_season": {}}})
    monkeypatch.setattr(M, "fit_participation", lambda *a, **k: {})
    monkeypatch.setattr(WF, "fit_procedure", lambda T, tf, store, log=print: {"resid_rows": 0, "T": T})
    out = WF.fit({}, None)
    assert seen["v0"] == 2027 and out["T"] == 2027 and S.MAX_SEASON == 2026


def test_guard_refuses_concurrent_and_nested_use():
    entered, release, errs = threading.Event(), threading.Event(), []

    def hold():
        with WF.production_season_guard():
            entered.set()
            release.wait(5)
    t = threading.Thread(target=hold)
    t.start()
    entered.wait(5)
    try:
        with pytest.raises(WF.SeasonGuardViolation, match="already active"):
            with WF.production_season_guard():
                errs.append("entered")                             # never reached
    finally:
        release.set()
        t.join(5)
    assert errs == [] and S.MAX_SEASON == 2026 and WF.guard_state()["RESTORED"]
    with WF.production_season_guard():
        with pytest.raises(WF.SeasonGuardViolation):
            with WF.production_season_guard():
                pass
    assert S.MAX_SEASON == 2026


def test_guard_refuses_a_module_state_that_is_not_the_frozen_value(monkeypatch):
    monkeypatch.setattr(S, "MAX_SEASON", 2030)
    with pytest.raises(WF.SeasonGuardViolation, match="not the frozen"):
        with WF.production_season_guard():
            pass
    assert S.MAX_SEASON == 2030 and not WF.guard_state()["guard_active"]


# ============================================================================ D. correction detection
def _box(players, teams):
    return {"flagship.player_game_outcomes": pd.DataFrame(players), "flagship.team_game_outcomes": pd.DataFrame(teams)}


P1 = {"canonical_game_id": "G", "canonical_team_id": "T1", "canonical_player_id": "P1", "points": 10, "reb": 5,
      "minutes": 30.0, "accounting_issues": None, "x": np.nan, "event_time": pd.Timestamp("2026-11-04T02:00Z"),
      "historical_knowable_at": pd.Timestamp("2026-11-04T04:00Z"), "source_file": "a.json", "received_at": pd.Timestamp("2026-11-04T05:00Z"),
      "tipped": pd.Timestamp("2026-11-04T00:00Z")}
P2 = {**P1, "canonical_player_id": "P2", "points": 7, "reb": 2}
TM = {"canonical_game_id": "G", "canonical_team_id": "T1", "points": 17, "reb": 7, "points_by_period": [9, 8],
      "source_system": "CBBD", "processed_at": pd.Timestamp("2026-11-04T05:00Z")}


def test_corrections_semantic_equality():
    base = _box([P1, P2], [TM])
    same = _box([{**P2, "points": 7.0, "x": float("nan"), "accounting_issues": None},     # 5 vs 5.0, NaN vs NaN, null
                 {**P1, "reb": 5.0, "x": None, "tipped": pd.Timestamp("2026-11-03T19:00", tz="America/New_York")}],  # order, tz
                [{**TM, "reb": 7.0, "points_by_period": np.array([9, 8])}])
    assert CR.box_diff(base, same) == [] and CR.box_identity(base) == CR.box_identity(same)
    naive = _box([{**P1, "tipped": pd.Timestamp("2026-11-04 00:00")}, P2], [TM])            # naive = UTC
    assert CR.box_diff(base, naive) == []


def test_corrections_ignore_non_material_provenance():
    prov = _box([{**P1, "source_file": "b.json", "received_at": pd.Timestamp("2026-11-07T09:00Z"),
                  "historical_knowable_at": pd.Timestamp("2026-11-05T00:00Z"), "event_time": pd.NaT}, P2],
                [{**TM, "source_system": "OTHER", "processed_at": pd.Timestamp("2026-11-07T09:00Z")}])
    assert CR.box_diff(_box([P1, P2], [TM]), prov) == []


def test_corrections_detect_real_changes():
    base = _box([P1, P2], [TM])
    d = CR.box_diff(base, _box([{**P1, "reb": 6}, P2], [TM]))
    assert d == [{"relation": "flagship.player_game_outcomes", "canonical_team_id": "T1", "canonical_player_id": "P1",
                  "change": "VALUE_CHANGED", "field": "reb", "original_value": 5.0, "corrected_value": 6.0}]
    d = CR.box_diff(base, _box([P1, P2], [{**TM, "reb": 8}]))
    assert [(x["relation"], x["field"]) for x in d] == [("flagship.team_game_outcomes", "reb")]
    d = CR.box_diff(base, _box([P1, P2, {**P1, "canonical_player_id": "P3"}], [TM]))
    assert [(x["change"], x["canonical_player_id"]) for x in d] == [("ROW_ADDED", "P3")]
    d = CR.box_diff(base, _box([P1], [TM]))
    assert [(x["change"], x["canonical_player_id"]) for x in d] == [("ROW_REMOVED", "P2")]
    assert CR.box_identity(base) != CR.box_identity(_box([P1], [TM]))
    with pytest.raises(ValueError, match="duplicate"):
        CR.box_diff(base, _box([P1, P1], [TM]))


# ============================================================================ E. run identity
def _rid(**over):
    el = pd.DataFrame({"game_id": ["G1", "G2"], "decision": ["INCLUDED", "DEFERRED"], "reason": ["", "BOX_INCOMPLETE"]})
    a = {"cut": WC.refit_cutoff(date(2026, 11, 7)), "state_version": "S1", "eligibility": el,
         "training_identity": {"history": {"x": "1"}, "current_season": {"hashes": {"frame": "f"}}},
         "code": {"commit": "c1", "dirty": False}, "cfg_hash": "h1",
         "previous_model": {"version": "cbb-jm1-2026-27-20261104", "manifest_sha256": "m1"}} | over
    return DL.run_id_of(date(2026, 11, 7), DL.run_identity(a["cut"], a["state_version"], a["eligibility"],
                                                           a["training_identity"], a["code"], a["cfg_hash"], a["previous_model"]))


def test_run_id_is_deterministic_and_tied_to_every_material_input():
    base = _rid()
    assert base == _rid()                                                     # same inputs -> same id
    el2 = pd.DataFrame({"game_id": ["G2", "G1"], "decision": ["DEFERRED", "INCLUDED"], "reason": ["BOX_INCOMPLETE", ""]})
    assert _rid(eligibility=el2) == base                                      # row order is not material
    changed = {
        "cutoff": _rid(cut=WC.refit_cutoff(date(2026, 11, 8))),
        "state_version": _rid(state_version="S2"),
        "eligibility": _rid(eligibility=el2.assign(decision=["INCLUDED", "INCLUDED"], reason=["", ""])),
        "deferral_reason": _rid(eligibility=el2.assign(reason=["IDENTITY_UNRESOLVED", ""])),
        "training_data": _rid(training_identity={"history": {"x": "1"}, "current_season": {"hashes": {"frame": "g"}}}),
        "code": _rid(code={"commit": "c2", "dirty": False}),
        "code_dirty": _rid(code={"commit": "c1", "dirty": True}),
        "config": _rid(cfg_hash="h2"),
        "previous_model": _rid(previous_model={"version": "cbb-jm1-2026-27-20261105", "manifest_sha256": "m2"}),
        "previous_manifest": _rid(previous_model={"version": "cbb-jm1-2026-27-20261104", "manifest_sha256": "m9"})}
    assert all(v != base for v in changed.values()), [k for k, v in changed.items() if v == base]
    assert len(set(changed.values())) == len(changed)


@LOCAL
def test_a_materially_different_morning_run_is_not_suppressed(tmp_path, monkeypatch):
    hist = FX.synthetic_history(tmp_path / "h.duckdb")
    rows, meta = FX.increment_rows(hist)
    g = rows["flagship.games"].copy()
    g[["home_points", "away_points"]] = None                          # schedule only: a cheap NO_NEW_DATA morning
    ST = __import__("cbb_platform.prod.state", fromlist=["update"])
    ST.update({"flagship.games": g, "core.team_seasons": rows["core.team_seasons"]}, pd.Timestamp("2026-11-02T12:00Z"),
              FX.FIXTURE_PROVIDER, meta["preseason_evidence"], tmp_path / "state", created_at=pd.Timestamp("2026-11-02T12:00Z"))
    root = tmp_path / "cbb-originator"
    day = date(2026, 11, 4)
    now = WC.refit_cutoff(day) + pd.Timedelta(minutes=5)
    a = DL.morning(day, root, tmp_path / "state", hist, now=now, log=lambda s: None)
    b = DL.morning(day, root, tmp_path / "state", hist, now=now, log=lambda s: None)
    runs = SO.read_log(root / "manifests" / "runs.jsonl")
    assert a["run_id"] == b["run_id"] and len(runs) == 1                     # identical rerun: deduplicated
    monkeypatch.setattr(DL, "config_hash", lambda cfg: "a-materially-different-config")
    c = DL.morning(day, root, tmp_path / "state", hist, now=now, log=lambda s: None)
    runs = SO.read_log(root / "manifests" / "runs.jsonl")
    assert c["run_id"] != a["run_id"] and len(runs) == 2                     # material change: NOT suppressed
    assert runs[1]["run_identity"]["config_hash"] == "a-materially-different-config"


# ============================================================================ F. scorer model source
def test_frozen_opening_baseline_source_is_exact_on_the_golden_slate(tmp_path, monkeypatch):
    model = MS.resolve("FROZEN_OPENING_BASELINE")
    assert model.identity == LD.load().identity and "model_source" not in model.identity
    monkeypatch.setattr(RN.LD, "load", lambda paths=None, expected=None: model)
    got = G.capture(tmp_path / "g")
    assert G.compare(got, json.loads(G.GOLDEN.read_text())) == []          # identity, banks, every record: exact


def _active_store(tmp_path):
    root = tmp_path / "cbb-originator"
    SO.register_opening(root)
    v = _daily_copy(root, date(2026, 11, 2))
    SO.promote(root, v, "test")
    return root, v


def test_active_daily_source_is_verified_deterministic_and_provenanced(tmp_path, monkeypatch):
    root, v = _active_store(tmp_path)
    model = MS.resolve("ACTIVE_DAILY", FX.AS_OF, root)
    man = SO.manifest(root, v)
    ident = model.identity
    assert ident["model_version"] == v and ident["model_source"] == "ACTIVE_DAILY"
    assert {k: ident["artifact_sha256"][k] for k in SO.FILES} == {k: man["artifacts"][k]["sha256"] for k in SO.FILES}
    monkeypatch.setattr(RN.LD, "load", lambda paths=None, expected=None: model)
    a, b = G.capture(tmp_path / "a"), G.capture(tmp_path / "b")
    assert a["banks"] == b["banks"] and a["predictions"] == b["predictions"] and a["board"] == b["board"]
    preds = [json.loads(l)["body"] for l in open(tmp_path / "a" / "ledger.jsonl") if json.loads(l)["record_type"] == "PREDICTION"]
    assert preds and all(p["model_version"] == v and p["model_source"] == "ACTIVE_DAILY"
                         and p["training_cutoff"] == man["TRAINING_CUTOFF"]
                         and p["artifact_sha256"] == ident["artifact_sha256"] for p in preds)
    assert set(preds[0]["artifact_sha256"]) == {"marginal", "jm1_final", "residual_j2", "production_manifest"}


def test_active_daily_source_fails_closed(tmp_path):
    root = tmp_path / "cbb-originator"
    SO.ensure(root)
    with pytest.raises(MS.ModelSourceUnavailable, match="no ACTIVE"):          # missing pointer
        MS.resolve("ACTIVE_DAILY", FX.AS_OF, root)
    root, v = _active_store(tmp_path / "x")
    with pytest.raises(MS.ModelSourceUnavailable, match="FUTURE_LEAKAGE"):     # trained after the prediction time
        MS.resolve("ACTIVE_DAILY", pd.Timestamp("2026-11-01T00:00Z"), root)
    f = SO.version_dir(root, date(2026, 11, 2)) / SO.FILES["jm1_final"]
    f.write_bytes(f.read_bytes() + b" ")                                      # tampered artifact
    with pytest.raises(MS.ModelSourceUnavailable, match="integrity"):
        MS.resolve("ACTIVE_DAILY", FX.AS_OF, root)
    ptr = root / "active" / "ACTIVE_MODEL.json"                              # pointer at a candidate / unknown version
    ptr.write_text(json.dumps({**json.loads(ptr.read_text()), "ACTIVE_MODEL_VERSION": "cbb-jm1-2026-27-20261109"}))
    with pytest.raises(MS.ModelSourceUnavailable):
        MS.resolve("ACTIVE_DAILY", FX.AS_OF, root)
    with pytest.raises(ValueError):
        MS.resolve("SOMETHING_ELSE")
    assert MS.FALLBACK_POLICY.startswith("NONE")


def test_daily_cli_default_is_the_frozen_baseline(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(RN, "daily", lambda *a, **k: seen.append(k.get("model")) or {})
    RN.main(["daily", "--as-of", "2026-11-10T16:00:00Z"])
    assert MS.DEFAULT_SOURCE == "FROZEN_OPENING_BASELINE" and seen == [None]        # None -> LD.load (frozen)
    root, v = _active_store(tmp_path)
    RN.main(["daily", "--as-of", "2026-11-10T16:00:00Z", "--model-source", "ACTIVE_DAILY", "--model-store", str(root)])
    assert seen[-1].identity["model_version"] == v and seen[-1].identity["model_source"] == "ACTIVE_DAILY"


# ============================================================================ G. near-tip snapshot + CLV
TIP = "2026-11-11T00:30:00Z"


def _snapshot_store(tmp_path):
    root = tmp_path / "cbb-originator"
    SO.ensure(root)
    pp = MON._p(root)
    prov = pd.DataFrame([{"prediction_id": f"p-{s}", "run_id": "R1", "prediction_as_of": "2026-11-10T16:00:00+00:00",
                          "feature_cutoff": "2026-11-10T23:30:00+00:00", "model_version": "cbb-jm1-2026-27-20261110",
                          "training_cutoff": "2026-11-10T11:00:00+00:00", "game_id": "G-1", "team_id": "T-1",
                          "player_id": "P-1", "player": "Jon Doe", "market": "PTS", "line": 14.5, "side": s,
                          "probability": p, "p_nonpush": 1.0, "sportsbook": "bk"} for s, p in (("OVER", 0.55), ("UNDER", 0.45))])
    prov.to_parquet(pp["provenance"], index=False)
    pd.DataFrame([{"game_id": "G-1", "provider_event_id": "evt-1", "provider_commence_time": TIP, "first_run_id": "R1"}]
                 ).to_parquet(NT.paths(root)["events"], index=False)
    base = {k: None for k in SD.QUOTE_SIDE_FIELDS}
    SD.append([{"record_type": "QUOTE_SIDE", "body": {**base, "record_id": f"r-{s}", "run_id": "R1", "game_id": "G-1",
                                                      "player_id": "P-1", "market": "PTS", "line": 14.5, "side": s,
                                                      "sportsbook": "bk", "book_decimal_odds": d,
                                                      "model_version": "cbb-jm1-2026-27-20261110", "two_sided": True}}
               for s, d in (("OVER", 1.95), ("UNDER", 1.87))], pp["sides"])
    return root


def _odds_session(tmp_path, fake, now):
    s = OA.Session("near_tip_snapshot", cache_root=tmp_path / "cache", ledger_path=tmp_path / "odds.jsonl", http_get=fake,
                   credential=lambda: OX.DUMMY_ODDS_CREDENTIAL, clock=lambda: pd.Timestamp(now), sleep=lambda s: None,
                   event_commence={"evt-1": TIP}.get)
    OF.fetch_events(s, now, "2026-11-12T04:00Z")                            # free listing: makes the quota known
    return s


def test_near_tip_snapshot_records_prices_and_preceding_prediction_without_rescoring(tmp_path):
    root = _snapshot_store(tmp_path)
    ev = OX.event("evt-1", "Home", "Away", TIP)
    odds = OX.event_odds(ev, [OX.book("bk", "Bk", [OX.market("player_points", OX.ou("Jon Doe", 14.5, -120, +100),
                                                             "2026-11-11T00:19:00Z")])])
    fake = OX.FakeOdds([ev], {"evt-1": odds}, remaining=1000)
    assert NT.due(root, "2026-11-10T23:00:00Z").empty                         # outside the T-10m window
    now = "2026-11-11T00:21:00Z"
    s = _odds_session(tmp_path, fake, now)
    r = NT.snapshot(root, now, s)
    assert r["due"] == 1 and r["rows"] == 1
    snap = pd.read_parquet(NT.paths(root)["snapshots"]).iloc[0]
    assert snap["label"] == "NEAR_TIP_CLOSE_PROXY_T10M" and not snap["exact_market_close"]
    assert (snap["game_id"], snap["player_id"], snap["market"], snap["line"], snap["sportsbook"]) == ("G-1", "P-1", "PTS", 14.5, "bk")
    assert np.isclose(snap["over_decimal"], 1 + 100 / 120) and np.isclose(snap["under_decimal"], 2.0)
    assert snap["quote_available_at"].startswith("2026-11-11 00:19") and snap["collector_received_at"] != "None"
    assert snap["preceding_model_version"] == "cbb-jm1-2026-27-20261110" and np.isclose(snap["preceding_p_over_nonpush"], 0.55)
    assert json.loads(snap["preceding_prediction_ids"]) == ["p-OVER", "p-UNDER"]
    odds_calls = lambda: sum("/events/evt-1/odds" in u for u, _ in fake.calls)
    assert odds_calls() == 1
    again = NT.snapshot(root, "2026-11-11T00:23:00Z", _odds_session(tmp_path, fake, "2026-11-11T00:23:00Z"))
    assert again["due"] == 0 and odds_calls() == 1                          # snapshot already taken: no second odds call
    assert len(pd.read_parquet(NT.paths(root)["snapshots"])) == 1
    c = MON.clv_section(root)
    assert c["status"] == "OK" and c["reference"] == NT.LABEL and c["exact_market_close"] is False and c["n"] == 2
    exp = np.mean([1.95 / (1 + 100 / 120) - 1, 1.87 / 2.0 - 1])
    assert np.isclose(c["clv"]["value"], exp)


def test_near_tip_snapshot_never_requests_after_commence(tmp_path):
    root = _snapshot_store(tmp_path)
    assert NT.due(root, TIP).empty and NT.due(root, "2026-11-11T00:40:00Z").empty
    assert MON.clv_section(tmp_path / "empty")["status"] == "BLOCKED_NO_CLOSE_QUOTES"
