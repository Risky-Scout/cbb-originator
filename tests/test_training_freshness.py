"""TRAINING FRESHNESS (walkforward.freshness + the health gate): the model serving ET date D is trained through D-1.

Hermetic: temporary model stores / state manifests only (no frozen history, no fit). Scenarios (lifecycle directive
2026-11-18 section 11): normal D-1 update, no D-1 games, incomplete D-1 data, stale-cache regression, intraday refresh
(no refit), restart after promotion (no refit), plus the future game-model joint contract.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import lifecycle as L, morning_stages as MS
from cbb_platform.walkforward import config as WC, daily as DL, eligibility as EL, freshness as FR, store as SO

D = date(2026, 11, 18)
PRIOR = D - timedelta(days=1)


def _tip(day: date, hour_et: int = 19) -> pd.Timestamp:
    return pd.Timestamp(f"{day.isoformat()} {hour_et:02d}:00", tz=WC.TZ).tz_convert("UTC")


def _row(gid, day, decision="INCLUDED", reason="", universe=WC.TRAINING_UNIVERSE, status="final", hour=19):
    return {"game_id": gid, "scheduled_tip_utc": _tip(day, hour), "game_universe": universe, "provider_status": status,
            "has_result": decision == "INCLUDED" or reason in ("BOX_UNRECONCILED", "BOX_INCOMPLETE", "IDENTITY_UNRESOLVED"),
            "knowable_at": _tip(day, hour) + pd.Timedelta(hours=3), "outcome_available_at": _tip(day, hour) + pd.Timedelta(hours=6),
            "outcome_state_version": "S1", "box_complete": reason not in ("BOX_INCOMPLETE",),
            "box_reconciled": reason not in ("BOX_INCOMPLETE", "BOX_UNRECONCILED"), "unresolved_rows": 0,
            "decision": decision, "reason": reason}


def _el(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=EL.COLUMNS)


def _normal_el() -> pd.DataFrame:
    return _el([_row("G-old", PRIOR - timedelta(days=1)), _row("G-a", PRIOR), _row("G-b", PRIOR, hour=23),
                _row("G-nd1", PRIOR, "EXCLUDED", "NOT_IN_TRAINING_UNIVERSE", universe="DI_VS_NON_DI")])


def _version(root: Path, day: date, el: pd.DataFrame | None, promote=True, cutoff=None, inputs="i") -> str:
    """A hash-consistent version directory (as walkforward.daily.build_candidate lays it out) + optional promotion."""
    SO.ensure(root)
    vid = WC.version_id(day)
    cand = SO.new_candidate_dir(root, day)
    msha = SO.write_json(cand / SO.FILES["marginal"], {"m": 1})
    rsha = SO.write_json(cand / SO.FILES["residual_j2"], {"MARGINAL_ARTIFACT_SHA256": msha})
    jsha = SO.write_json(cand / SO.FILES["jm1_final"], {"MARGINAL_ARTIFACT_SHA256": msha,
                                                         "RESIDUAL_DEPENDENCE_ARTIFACT_SHA256": rsha})
    inc = el[el["decision"] == "INCLUDED"].sort_values("scheduled_tip_utc") if el is not None and len(el) else None
    SO.write_manifest(cand, {
        "MODEL_VERSION": vid, "MODEL_KIND": WC.DAILY_KIND, "TRAINING_CUTOFF": (cutoff or WC.refit_cutoff(day)).isoformat(),
        "TRAINING_THROUGH_DATE": str(inc.iloc[-1]["scheduled_tip_utc"].tz_convert(WC.TZ).date()) if inc is not None else None,
        "LATEST_INCLUDED_GAME": ({"game_id": inc.iloc[-1]["game_id"], "scheduled_tip_utc": str(inc.iloc[-1]["scheduled_tip_utc"])}
                                 if inc is not None else None),
        "N_INCLUDED_2026_27_GAMES": 0 if inc is None else len(inc), "N_INCLUDED_2026_27_PLAYER_GAMES": 0 if inc is None else 20 * len(inc),
        "STATE_VERSION": f"S-{day:%Y%m%d}", "TRAINING_DATA_HASH": f"h-{day:%Y%m%d}", "INPUT_IDENTITY_SHA": inputs,
        "fit": {"rows": {"frame": 1000}}, "deterministic_refit": {"BIT_IDENTICAL": True},
        "artifacts": {"marginal": {"sha256": msha}, "residual_j2": {"sha256": rsha}, "jm1_final": {"sha256": jsha}}})
    SO.write_json(cand / "gates.json", {"ALL_PASS": True})
    if el is not None:
        el.to_parquet(cand / "eligibility.parquet", index=False)
    SO.finalize(root, cand, day)
    if promote:
        SO.promote(root, vid, "DAILY_WALK_FORWARD gates PASS", f"WF-{day:%Y%m%d}-x")
    return vid


def _index(ops: Path, *snaps) -> None:
    p = ops / "forecasts" / "index.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for sid, window, model in snaps:
            f.write(json.dumps({"snapshot_id": sid, "slate_date": D.isoformat(), "window": window,
                                "model_version": model, "executed_at": "2026-11-18T12:00:00+00:00"}) + "\n")


def _state(state_root: Path, sv: str, created) -> None:
    d = state_root / "versions" / sv
    d.mkdir(parents=True, exist_ok=True)
    (d / "state_manifest.json").write_text(json.dumps({"state_version": sv, "created_at": pd.Timestamp(created).isoformat(),
                                                       "source_cutoff": pd.Timestamp(created).isoformat()}))


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.delenv(L.HEAVY_LOCK_ENV, raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    root = tmp_path / "wf" / "cbb-originator"
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=root,
                  clock=lambda: pd.Timestamp("2026-11-18T14:00:00Z"))
    return {"root": root, "ops": tmp_path / "ops", "life": life, "tmp": tmp_path}


# ============================================================================ 1. normal D-1 update
def test_normal_d1_update_is_fresh_and_forecast_references_the_new_model(w):
    _version(w["root"], PRIOR, _el([_row("G-old", PRIOR - timedelta(days=1))]))
    vid = _version(w["root"], D, _normal_el())
    _index(w["ops"], ("F1", "INITIAL_0700", WC.version_id(PRIOR)), ("F2", "REFIT_RETRY_0800", vid),
           ("F3", "ROTOWIRE_1000", vid))
    r = FR.evaluate(w["root"], D, w["ops"])
    assert r["TRAINING_FRESHNESS"] == "PASS" and r["ACTIVE_BLOCKER"] is None
    assert r["ACTIVE_MODEL_TRAINING_THROUGH"] == r["EXPECTED_TRAINING_THROUGH"] == "2026-11-17"
    assert r["LATEST_GAME_DATE_INCLUDED"] == "2026-11-17" and r["TRAINING_THROUGH_BASIS"] == "ACTIVE_FIT_AT_D_CUTOFF"
    assert (r["PREVIOUS_DAY_FINAL_GAMES"], r["PREVIOUS_DAY_INCLUDED_GAMES"], r["PREVIOUS_DAY_EXCLUDED_GAMES"]) == (2, 2, 0)
    assert r["PREVIOUS_DAY_STATUS"] == "COMPLETE" and r["FULL_SLATE_FORECAST"]["snapshot_id"] == "F2"
    for k in ("MODEL_VERSION", "TRAINING_CUTOFF", "TRAINING_THROUGH_DATE", "LATEST_GAME_DATE_INCLUDED", "PREVIOUS_DAY_DATE",
              "PREVIOUS_DAY_FINAL_GAMES", "PREVIOUS_DAY_INCLUDED_GAMES", "PREVIOUS_DAY_EXCLUDED_GAMES",
              "PREVIOUS_DAY_EXCLUSION_REASONS", "N_TRAIN_ROWS", "SOURCE_STATE_VERSION", "SOURCE_HISTORY_HASH"):
        assert k in r, k
    rc = L.health(w["life"], D)["receipt"]
    assert rc["TRAINING_FRESHNESS"] == "PASS" and rc["DAY_OPERATIONAL_CONDITIONS"]["TRAINING_FRESHNESS"] is True
    assert rc["ACTIVE_MODEL_TRAINING_THROUGH"] == rc["EXPECTED_TRAINING_THROUGH"] == "2026-11-17"
    assert not any("STALE" in b for b in rc["ACTIVE_BLOCKERS"])


def test_stale_active_model_is_a_hard_blocker(w):
    """07:00 walk-forward deferred (2026-11-17 / 11-18 feature backlog): the incumbent serves D until the retry."""
    old = _version(w["root"], PRIOR, _el([_row("G-old", PRIOR - timedelta(days=1))]))
    _index(w["ops"], ("F1", "INITIAL_0700", old))
    r = FR.evaluate(w["root"], D, w["ops"])
    assert r["TRAINING_FRESHNESS"] == "FAIL" and r["ACTIVE_BLOCKER"] == FR.STALE_TRAINING_MODEL
    assert (r["ACTIVE_MODEL_TRAINING_THROUGH"], r["EXPECTED_TRAINING_THROUGH"]) == ("2026-11-16", "2026-11-17")
    rc = L.health(w["life"], D)["receipt"]
    assert rc["ACTIVE_BLOCKER"] == "STALE_TRAINING_MODEL" and rc["DAY_OPERATIONAL"] is False
    assert any(b.startswith("STALE_TRAINING_MODEL") for b in rc["ACTIVE_BLOCKERS"])


def test_promoted_fresh_model_without_a_fresh_full_slate_forecast_is_blocked(w):
    """A retry promoted, but its REFIT_RETRY forecast never ran: the slate is still priced from the stale fit."""
    old = _version(w["root"], PRIOR, _el([_row("G-old", PRIOR - timedelta(days=1))]))
    _version(w["root"], D, _normal_el())
    _index(w["ops"], ("F1", "INITIAL_0700", old))
    r = FR.evaluate(w["root"], D, w["ops"])
    assert r["TRAINING_FRESHNESS"] == "FAIL" and r["ACTIVE_BLOCKER"] == FR.STALE_FORECAST_MODEL
    assert L.health(w["life"], D)["receipt"]["DAY_OPERATIONAL"] is False


def test_unreadable_store_never_looks_fresh(w, monkeypatch):
    _version(w["root"], D, _normal_el())
    monkeypatch.setattr(FR, "evaluate", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    rc = L.health(w["life"], D)["receipt"]
    assert rc["TRAINING_FRESHNESS"] == "FAIL" and rc["ACTIVE_BLOCKER"] == "STALE_TRAINING_MODEL"


# ============================================================================ 2. no D-1 games
def test_no_d1_games_is_explicit_no_new_games_and_certified_carry_forward(w):
    el = _el([_row("G-old", PRIOR - timedelta(days=2))])
    inc = _version(w["root"], PRIOR, el)
    assert FR.evaluate(w["root"], D, w["ops"])["TRAINING_FRESHNESS"] == "FAIL"      # no D receipt: not certified
    rec = {"run_id": "WF-x", "DAILY_REFIT_STATUS": "NO_NEW_DATA", "DAILY_MODEL_PROMOTION": "NOT_REQUIRED",
           "ACTIVE_MODEL_VERSION": inc}
    body = FR.write_run_receipt(w["root"], D, rec, {"eligibility": el, "state": {"state_version": "S2"}},
                                SO.manifest(w["root"], inc))
    assert body["PREVIOUS_DAY_STATUS"] == FR.NO_NEW_GAMES and body["PREVIOUS_DAY_FINAL_GAMES"] == 0
    r = FR.evaluate(w["root"], D, w["ops"])
    assert r["TRAINING_FRESHNESS"] == "PASS" and r["TRAINING_THROUGH_BASIS"] == "CERTIFIED_NO_NEW_DATA_AT_D_CUTOFF"
    assert r["ACTIVE_MODEL_TRAINING_THROUGH"] == "2026-11-17" and r["PREVIOUS_DAY_STATUS"] == FR.NO_NEW_GAMES


def test_a_refused_run_never_certifies_the_incumbent(w):
    el = _el([_row("G-old", PRIOR - timedelta(days=2))])
    inc = _version(w["root"], PRIOR, el)
    FR.write_run_receipt(w["root"], D, {"DAILY_REFIT_STATUS": "FAIL", "ACTIVE_MODEL_VERSION": inc},
                         {"eligibility": el}, SO.manifest(w["root"], inc))
    assert FR.evaluate(w["root"], D, w["ops"])["TRAINING_FRESHNESS"] == "FAIL"


# ============================================================================ 3. incomplete D-1 data
def test_incomplete_d1_games_are_classified_once_with_ids_and_counters_reconcile(w):
    el = _el([_row("G-ok", PRIOR), _row("G-unr", PRIOR, "DEFERRED", "BOX_UNRECONCILED"),
              _row("G-box", PRIOR, "DEFERRED", "BOX_INCOMPLETE"), _row("G-id", PRIOR, "DEFERRED", "IDENTITY_UNRESOLVED"),
              _row("G-fin", PRIOR, "DEFERRED", "FINAL_DATA_INCOMPLETE_AT_CUTOFF"),
              _row("G-late", PRIOR, "DEFERRED", "NOT_FINAL_AT_CUTOFF", status="in_progress"),
              _row("G-nos", PRIOR, "EXCLUDED", "NO_STATE_AT_CUTOFF"),
              _row("G-canc", PRIOR, "EXCLUDED", "NOT_PLAYED", status="cancelled"),
              _row("G-ndi", PRIOR, "EXCLUDED", "NOT_IN_TRAINING_UNIVERSE", universe="DI_VS_NON_DI")])
    c = FR.classify_prior_day(el, D)
    assert c["COUNTERS_RECONCILE"] and c["PREVIOUS_DAY_STATUS"] == "ACCEPTED_DATA_GAPS"
    assert (c["PREVIOUS_DAY_ALL_GAMES"], c["PREVIOUS_DAY_OUT_OF_SCOPE"], c["PREVIOUS_DAY_FINAL_GAMES"]) == (9, 2, 7)
    assert c["PREVIOUS_DAY_CLASS_COUNTS"] == {"TRAINING_INCLUDED": 1, "UNRECONCILED_FINAL": 1, "INCOMPLETE_FINAL": 3,
                                              "DATA_NOT_AVAILABLE_AT_CUTOFF": 2}
    assert c["PREVIOUS_DAY_EXCLUDED_GAME_IDS"] == {"UNRECONCILED_FINAL": ["G-unr"],
                                                   "INCOMPLETE_FINAL": ["G-box", "G-fin", "G-id"],
                                                   "DATA_NOT_AVAILABLE_AT_CUTOFF": ["G-late", "G-nos"]}
    ids = [x["game_id"] for x in c["PREVIOUS_DAY_EXCLUSIONS"]] + c["PREVIOUS_DAY_INCLUDED_GAME_IDS"]
    assert sorted(ids) == sorted(set(ids)) and len(ids) == c["PREVIOUS_DAY_FINAL_GAMES"]          # exactly once
    _version(w["root"], D, el)
    rc = L.health(w["life"], D)["receipt"]
    assert rc["TRAINING_FRESHNESS"] == "PASS"                    # explicit, accepted, deferred data gaps
    assert (rc["PREVIOUS_DAY_FINAL_GAMES"], rc["PREVIOUS_DAY_INCLUDED_GAMES"], rc["PREVIOUS_DAY_EXCLUDED_GAMES"]) == (7, 1, 6)
    assert rc["PREVIOUS_DAY_STATUS"] == "ACCEPTED_DATA_GAPS"


def test_all_d1_games_deferred_passes_only_as_an_explicit_training_data_lag(w):
    """2026-11-08 / 11-10 production: every in-scope D-1 game NOT_FINAL_AT_CUTOFF (result latency)."""
    el = _el([_row("G-old", PRIOR - timedelta(days=1)),
              _row("G-l1", PRIOR, "DEFERRED", "NOT_FINAL_AT_CUTOFF", status=None),
              _row("G-l2", PRIOR, "DEFERRED", "NOT_FINAL_AT_CUTOFF", status=None)])
    _version(w["root"], D, el)
    r = FR.evaluate(w["root"], D, w["ops"])
    assert r["TRAINING_FRESHNESS"] == "PASS" and r["TRAINING_DATA_LAG"] is True
    assert r["LATEST_GAME_DATE_INCLUDED"] == "2026-11-16" and r["PREVIOUS_DAY_INCLUDED_GAMES"] == 0
    rc = L.health(w["life"], D)["receipt"]
    assert any(x["warning"].startswith("TRAINING_DATA_LAG") and x["class"] == "INFORMATIONAL" for x in rc["WARNING_CLASSES"])


def test_an_unknown_exclusion_reason_is_never_silent(w):
    el = _el([_row("G-ok", PRIOR), _row("G-x", PRIOR, "DEFERRED", "SOMETHING_NEW")])
    c = FR.classify_prior_day(el, D)
    assert c["PREVIOUS_DAY_STATUS"] == FR.UNCLASSIFIED and c["PREVIOUS_DAY_UNCLASSIFIED_GAME_IDS"] == ["G-x"]
    _version(w["root"], D, el)
    r = FR.evaluate(w["root"], D, w["ops"])
    assert r["TRAINING_FRESHNESS"] == "FAIL" and r["UNACCEPTED_GAPS"] == ["G-x"]


# ============================================================================ 4. stale-cache regression
def _ts(state_root: Path, el: pd.DataFrame, state_version: str, trained=None) -> dict:
    trained = sorted(el.loc[el["decision"] == "INCLUDED", "game_id"]) if trained is None else trained
    return {"training_cutoff": WC.refit_cutoff(D), "state": {"state_version": state_version,
                                                             "created_at": pd.Timestamp("2026-11-17T09:00Z"),
                                                             "source_cutoff": pd.Timestamp("2026-11-17T09:00Z")},
            "eligibility": el, "trained_games": trained, "frames": None, "feature_meta": {},
            "labels": pd.DataFrame({"canonical_game_id": trained}), "identity": {"history": {}, "current_season": {"games": trained}},
            "timings": {}}


def test_candidate_from_an_old_state_or_missing_d1_games_is_rejected(w):
    sr = w["tmp"] / "state"
    _state(sr, "S-old", "2026-11-17T09:00Z")
    _state(sr, "S-new", "2026-11-18T09:20Z")                     # holds the D-1 finals, created before D's cutoff
    el = _normal_el()
    assert FR.candidate_inputs_check(_ts(sr, el, "S-new"), sr, D)["PASS"]
    stale = FR.candidate_inputs_check(_ts(sr, el, "S-old"), sr, D)
    assert not stale["PASS"] and stale["checks"]["STATE_IS_LATEST_AS_OF_CUTOFF"] is False
    miss = FR.candidate_inputs_check(_ts(sr, el, "S-new", trained=["G-old"]), sr, D)
    assert not miss["PASS"] and miss["INCLUDED_NOT_TRAINED"] == ["G-a", "G-b"]
    assert miss["checks"]["PREVIOUS_DAY_INCLUDED_TRAINED"] is False


def test_stale_cache_candidate_is_refused_before_any_fit_and_active_is_unchanged(w, monkeypatch):
    sr = w["tmp"] / "state"
    _state(sr, "S-old", "2026-11-17T09:00Z")
    _state(sr, "S-new", "2026-11-18T09:20Z")
    old = _version(w["root"], PRIOR, _el([_row("G-old", PRIOR - timedelta(days=1))]))
    monkeypatch.setattr(DL.SO, "register_opening", lambda root: {})
    monkeypatch.setattr(DL.P, "load_config", lambda: {})
    monkeypatch.setattr(DL.MON, "settle", lambda *a, **k: {})
    monkeypatch.setattr(DL.MON, "write_report", lambda *a, **k: {})
    monkeypatch.setattr(DL, "training_state", lambda *a, **k: _ts(sr, _normal_el(), "S-old"))
    monkeypatch.setattr(DL, "build_candidate", lambda *a, **k: pytest.fail("a stale-input candidate must never be fit"))
    rec = DL._morning(D, w["root"], sr, None, pd.Timestamp("2026-11-18T12:00Z"), lambda s: None)
    assert (rec["DAILY_REFIT_STATUS"], rec["DAILY_MODEL_PROMOTION"]) == ("FAIL", "REFUSED")
    assert rec["reason"].startswith("STALE_TRAINING_INPUT") and "STATE_IS_LATEST_AS_OF_CUTOFF" in rec["reason"]
    assert SO.active(w["root"])["ACTIVE_MODEL_VERSION"] == old and not SO.version_dir(w["root"], D).exists()
    body = json.loads(FR.receipt_path(w["root"], D).read_text())
    assert body["TRAINING_THROUGH_DATE"] == "2026-11-16" and body["DAILY_MODEL_PROMOTION"] == "REFUSED"
    assert FR.evaluate(w["root"], D, w["ops"])["ACTIVE_BLOCKER"] == FR.STALE_TRAINING_MODEL


# ============================================================================ 5. intraday: no refit
def test_intraday_availability_refresh_never_refits(w, monkeypatch):
    from cbb_platform.walkforward import fit as WF
    vid = _version(w["root"], D, _normal_el())
    _index(w["ops"], ("F2", "INITIAL_0700", vid))
    trip = lambda *a, **k: pytest.fail("intraday refresh must never refit")              # noqa: E731
    for mod, name in ((DL, "morning"), (DL, "_morning"), (DL, "build_candidate"), (DL, "training_state"), (WF, "fit"),
                      (L, "walk_forward")):
        monkeypatch.setattr(mod, name, trip)
    seen = []
    monkeypatch.setattr(L, "forecast", lambda life, window, **k: seen.append(window) or {
        "status": "FORECAST", "snapshot_id": "F3", "window": L.window_label(window),
        "model_version": SO.active(life.model_root)["ACTIVE_MODEL_VERSION"], "pmfs_persisted": 5})
    monkeypatch.setattr(L, "exports_for", lambda life, d: {"date": d.isoformat()})
    monkeypatch.setattr(L, "rotowire_state", lambda life: "DISABLED")
    for window in L.ROTOWIRE_REFRESH_TIMES_ET:
        out = L.refresh(w["life"], window)
        assert out["forecast"]["model_version"] == vid
    assert seen == list(L.ROTOWIRE_REFRESH_TIMES_ET)
    assert SO.versions(w["root"]) == [vid] and SO.active(w["root"])["ACTIVE_MODEL_VERSION"] == vid
    assert json.loads(L.health_path(w["life"], D).read_text())["receipt"]["TRAINING_FRESHNESS"] == "PASS"


# ============================================================================ 6. restart after promotion
def test_restart_after_promotion_loads_the_same_active_and_never_refits(w, monkeypatch):
    calls = {"fit": 0}

    def runner(day, root, state_root, now):
        calls["fit"] += 1
        v = _version(Path(root), day, _normal_el())
        return {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED", "ACTIVE_MODEL_VERSION": v,
                "run_id": f"WF-{day:%Y%m%d}-x"}

    _version(w["root"], PRIOR, _el([_row("G-old", PRIOR - timedelta(days=1))]))
    monkeypatch.setattr(L.SH, "tick", lambda ctx: {"tick_id": "K1", "status": "OK", "did_work": True})
    monkeypatch.setattr(L, "exports_for", lambda life, d: {"date": d.isoformat(), "grades": {"status": "WRITTEN"},
                                                           "boards": {"status": "WRITTEN"}, "distributions": {"status": "WRITTEN"}})
    monkeypatch.setattr(L, "forecast", lambda life, window=L.INITIAL, **k: {
        "status": "FORECAST", "snapshot_id": "F1", "window": L.window_label(window), "games_forecast": 2,
        "model_version": SO.active(life.model_root)["ACTIVE_MODEL_VERSION"], "pmfs_persisted": 5})
    for st in MS.STAGES:
        MS.run_stage(w["life"], D, st, **({"runner": runner, "warm_runner": None} if st == "WALK_FORWARD" else {}))
    first = SO.active(w["root"])
    for st in MS.STAGES:                                         # the process / Droplet restarts: the chain re-runs
        MS.run_stage(w["life"], D, st, **({"runner": runner, "warm_runner": None} if st == "WALK_FORWARD" else {}))
    MS.recover(w["life"], D)
    assert calls["fit"] == 1 and SO.active(w["root"]) == first
    assert first["ACTIVE_MODEL_VERSION"] == WC.version_id(D)
    rc = json.loads(L.health_path(w["life"], D).read_text())["receipt"]
    assert rc["TRAINING_FRESHNESS"] == "PASS" and rc["ACTIVE_MODEL_TRAINING_THROUGH"] == "2026-11-17"


# ============================================================================ the future game-model contract
@pytest.mark.parametrize("player,team,allowed", [
    ("2026-11-17", None, True),                 # game layer not built yet: player freshness alone
    ("2026-11-17", "2026-11-17", True),
    ("2026-11-17", "2026-11-16", False),        # stale team ratings with fresh player forecasts
    ("2026-11-16", "2026-11-17", False),        # fresh team ratings with a stale player fit
    (None, "2026-11-17", False)])
def test_game_pmf_requires_player_and_team_state_both_through_d_minus_1(player, team, allowed):
    assert FR.joint_contract(D, player, team)["GAME_PMF_ALLOWED"] is allowed


def test_past_day_is_not_judged_against_a_later_active(w):
    _version(w["root"], D, _normal_el())
    assert FR.evaluate(w["root"], PRIOR, w["ops"])["TRAINING_FRESHNESS"] == FR.NOT_APPLICABLE
