"""CBB_Performance (prod/performance.py): cumulative market evaluation of the frozen pre-cutoff market evaluations.
Synthetic evidence only; no network, no model.

Legacy test classification (11e736c tests/test_prod_performance.py; the official-selection inputs no longer exist, so
every invariant is restated on frozen market evaluations):
  test_brier_and_log_loss_exact_math ............... VALID_INVARIANT_TO_PRESERVE -> same name (MX.metrics, EPS clip)
  test_push_and_void_excluded_from_brier_win_pct_and_roi
                                                     VALID_INVARIANT_TO_PRESERVE -> same name (+ Hit Rate W/(W+L+P))
  test_calibration_bins_and_integrity_exclusion .... VALID (bins) -> test_calibration_bins_exact; the integrity_status
                                                     exclusion is SUPERSEDED_CONTRACT (future information cannot enter a
                                                     frozen evaluation) -> test_point_in_time_fields_never_after_cutoff
  test_cumulative_window_grouping_and_daily ........ VALID_INVARIANT_TO_PRESERVE -> test_workbook_tabs_cumulative_scope_
                                                     and_daily + test_group_tables_reconcile_to_overall
  test_point_in_time_probability_is_the_frozen_receipt
                                                     VALID_INVARIANT_TO_PRESERVE -> test_point_in_time_probability_is_
                                                     the_frozen_evaluation
  test_exact_line_clv_devig_and_positive_sign ...... VALID_INVARIANT_TO_PRESERVE -> test_metrics_and_clv_from_persisted_
                                                     evidence + test_exact_line_log_odds_and_no_devig_pair
  test_same_line_nearest_close_and_reference_book .. VALID_INVARIANT_TO_PRESERVE -> same name
  test_line_moved_directional_sign_and_no_probability_clv
                                                     VALID_INVARIANT_TO_PRESERVE -> same name (OVER up, UNDER down)
  test_no_close_and_no_future_quote_used ........... VALID_INVARIANT_TO_PRESERVE -> same name (pre-entry + post-cutoff)
  test_integrity_flags_future_information .......... SUPERSEDED_CONTRACT -> test_point_in_time_fields_never_after_cutoff
  test_duplicate_key_prevention .................... VALID_INVARIANT_TO_PRESERVE -> same name
  test_incremental_update_reuses_final_rows_and_rebuilds_changed
                                                     VALID_INVARIANT_TO_PRESERVE -> same name
  test_deterministic_rerun_and_receipt_hashes ...... VALID_INVARIANT_TO_PRESERVE -> same name
  test_workbook_schema_and_formats ................. VALID_INVARIANT_TO_PRESERVE -> same name
  test_empty_and_not_yet_graded .................... VALID_INVARIANT_TO_PRESERVE -> same name (NO_MARKET_EVIDENCE_YET)
  test_daily_contract_requires_performance ......... VALID_INVARIANT_TO_PRESERVE -> test_daily_contract_requires_boards_
                                                     grades_and_performance (Grades now required as well)
Legacy tests/test_prod_exports.py (Grades "Performance Summary" sheet removed):
  test_performance_summary_sections_and_reconciliation
                                                     SUPERSEDED sheet; VALID reconciliation -> test_group_tables_
                                                     reconcile_to_overall
  test_zero_count_role_rows_kept ................... VALID_INVARIANT_TO_PRESERVE -> test_zero_count_role_rows_kept
"""
import json
import math

import numpy as np
import pandas as pd
import pytest
from pathlib import Path

from cbb_platform.prod import exports as EX, market_exports as MX, morning_stages as MS, performance as PF, quote_obs as QO

D1, D2 = "2026-11-04", "2026-11-05"


def _pmf(w):
    p = np.zeros(40)
    for k, v in w.items():
        p[k] = v
    return p / p.sum()


def _q(gid, pid, book, line, side, am, at):
    d = 1 + (am / 100 if am > 0 else 100 / abs(am))
    return {"provider_event_id": "E", "sportsbook": book, "market_key": "player_points", "is_alternate": False,
            "provider_player_name": pid, "stat": "PTS", "line": line, "side": side, "american_odds": am, "decimal_odds": d,
            "market_last_update": at, "bookmaker_last_update": None, "quote_available_at": None,
            "collector_received_at": at, "canonical_game_id": gid, "canonical_player_id": pid, "quote_status": "VALID",
            "suppression_reason": None}


def _day(ops, day, gid, tip, players, actual, obs=None):
    """one game: a decision observation 2 h before the cutoff, a close 15 min before it, frozen, then settled.
    obs: [(minutes before the cutoff (negative = after), [(player, book, line, side, american)])] replaces the default."""
    cut = tip - pd.Timedelta(hours=1)
    if obs is not None:
        pm = [{"game_id": gid, "player_id": pid, "team_id": "T", "stat": "PTS", "mean": 10.0, "pmf": _pmf(w),
               "game_label": gid, "player_label": pid, "tip": tip, "cutoff": cut, "source_kind": "FORECAST_SNAPSHOT",
               "forecast_id": "F", "model_version": "m", "prediction_as_of": cut - pd.Timedelta(hours=3)}
              for pid, w in players.items()]
        for mins, offers in obs:
            at = cut - pd.Timedelta(minutes=mins)
            QO.record(ops, day, gid, "E", at, pd.DataFrame([_q(gid, p, b, ln, s, o, at) for p, b, ln, s, o in offers]),
                      "QUOTE_REFRESH")
        MX.freeze(ops, day, pm, {}, {}, {gid: cut}, cut)
        _settle(ops, gid, players, actual)
        return pm
    pm = []
    for pid, w in players.items():
        pm.append({"game_id": gid, "player_id": pid, "team_id": "T", "stat": "PTS", "mean": 10.0, "pmf": _pmf(w),
                   "game_label": gid, "player_label": pid, "tip": tip, "cutoff": cut, "source_kind": "FORECAST_SNAPSHOT",
                   "forecast_id": "F", "model_version": "m", "prediction_as_of": cut - pd.Timedelta(hours=3)})
    for at, am in ((cut - pd.Timedelta(hours=2), (-110, -110)), (cut - pd.Timedelta(minutes=15), (-125, 105))):
        rows = [_q(gid, p, b, 10.5, s, o, at) for p in players for b in ("draftkings", "fanduel")
                for s, o in (("OVER", am[0]), ("UNDER", am[1]))]
        QO.record(ops, day, gid, "E", at, pd.DataFrame(rows), "QUOTE_REFRESH")
    MX.freeze(ops, day, pm, {}, {}, {gid: cut}, cut)
    _settle(ops, gid, players, actual)
    return pm


def _settle(ops, gid, players, actual, tick=None):
    """final box rows of the players whose actual is known (missing key = not settled yet -> PENDING)."""
    rows = [{"game_id": gid, "player_id": p, "participation": "PLAYED" if actual[p] is not None else "DNP",
             "settled_at": "x", "minutes": 30.0 if actual[p] is not None else 0.0, "act_PTS": actual[p]}
            for p in players if p in actual]
    if not rows:
        return
    t = ops / "ticks" / (tick or f"K-{gid}")
    t.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(t / "actuals.parquet")


@pytest.fixture
def two_days(tmp_path):
    ops = tmp_path / "ops"
    _day(ops, D1, "G1", pd.Timestamp("2026-11-05T00:00Z"), {"A": {6: 0.3, 14: 0.7}, "B": {6: 0.7, 14: 0.3}},
         {"A": 14.0, "B": None})
    _day(ops, D2, "G2", pd.Timestamp("2026-11-06T00:00Z"), {"C": {6: 0.35, 14: 0.65}}, {"C": 6.0})
    return ops, tmp_path / "x"


def test_workbook_tabs_cumulative_scope_and_daily(two_days):
    ops, out = two_days
    rep = PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    assert rep["status"] == "WRITTEN" and rep["start_date"] == D1 and rep["rows"] == 3
    wb = EX.read_xlsx(out / D2 / PF.performance_name(D2))
    assert tuple(wb) == PF.SHEETS == ("Overall", "By Stat", "By Player Role", "Stat x Role", "By Side",
                                      "By Selection Tier", "Daily", "Calibration", "Selections")
    assert tuple(wb["Daily"]["rows"][0]) == ("Date",) + PF.METRICS
    assert [r[0] for r in wb["Daily"]["rows"][1:]] == [D1, D2]
    rows = PF.read_dataset(ops / PF.DATASET)
    m = PF._metrics(rows)
    assert (m["Predictions"], m["Graded"], m["VOID_DNP"]) == (3, 2, 1)       # B DNP: VOID_DNP, excluded
    r1 = PF.export(D1, ops, out / D1, pd.Timestamp("2026-11-06T12:00Z"))
    assert r1["rows"] == 2                                                   # through D1 only


def test_metrics_and_clv_from_persisted_evidence(two_days):
    ops, out = two_days
    PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    rows = {r["player_id"]: r for r in PF.read_dataset(ops / PF.DATASET)}
    a = rows["A"]
    assert a["side"] == "OVER" and a["result"] == "WIN" and a["clv_status"] == "EXACT_LINE"
    assert a["entry_fair_probability"] == pytest.approx(0.5)
    assert a["probability_clv"] == pytest.approx(a["close_fair_probability"] - 0.5) and a["probability_clv"] > 0
    c = rows["C"]
    assert c["result"] == "LOSS" and c["units"] == -1.0
    m = PF._metrics(list(rows.values()))
    assert m["Units Won"] == pytest.approx(a["units"] - 1.0) and m["ROI"] == pytest.approx((a["units"] - 1.0) / 2)
    assert m["Exact-Line CLV N"] == 2 and m["Positive CLV %"] == 1.0


def test_incremental_reuse_and_exact_recompute(two_days):
    ops, out = two_days
    now = pd.Timestamp("2026-11-06T12:00Z")
    r1 = PF.export(D2, ops, out / D2, now)
    assert r1["dataset"]["rebuilt"] == 3
    r2 = PF.export(D2, ops, out / D2, now)
    assert r2["dataset"]["reused"] == 3 and r2["dataset"]["rewritten"] is False and r2["rewritten"] is False
    chk = PF.verify(out / D2, D2, ops, recompute=True, now=now)
    assert chk == {"WORKBOOK_READABLE": True, "DATASET_READABLE": True, "RECEIPT_VALID": True,
                   "RECOMPUTE_AGREES": True, "PERFORMANCE_EVIDENCE": "PASS"}
    rec = json.loads((out / D2 / PF.RECEIPT).read_text())
    assert rec["canonical_dataset_sha256"] == EX._file_sha(ops / PF.DATASET)


def test_lost_frozen_evaluation_is_an_integrity_failure(two_days):
    ops, out = two_days
    PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    p = MX.eval_path(ops, D2)
    p.write_text("")                                                         # evidence removed
    rep = PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    assert rep["status"] == "ABORTED_INTEGRITY"


def test_daily_contract_requires_boards_grades_and_performance():
    ok, why = MS._export_ok({"boards": {"status": "WRITTEN"}, "grades": {"status": "WRITTEN"}})
    assert not ok and "performance=PERFORMANCE_MISSING" in why
    ok, why = MS._export_ok({"boards": {"status": "WRITTEN"}, "performance": {"status": "WRITTEN"}})
    assert not ok and "grades=GRADES_MISSING" in why
    ok, _ = MS._export_ok({"boards": {"status": "WRITTEN"}, "grades": {"status": "WRITTEN"},
                           "performance": {"status": "WRITTEN"}, "distributions": {"status": "MISSING_EVIDENCE"}})
    assert ok                                                                # Distributions is supporting
    ok, why = MS._export_ok({"boards": {"status": "MISSING_EVIDENCE"}, "grades": {"status": "WRITTEN"},
                             "performance": {"status": "WRITTEN"}, "predicted_games": 3})
    assert not ok and "boards=MISSING_EVIDENCE" in why
    ok, _ = MS._export_ok({"boards": {"status": "MISSING_EVIDENCE"}, "grades": {"status": "WRITTEN"},
                           "performance": {"status": "WRITTEN"}, "predicted_games": 0})
    assert ok                                                                # no slate
    ok, why = MS._export_ok({"boards": {"status": "WRITTEN"}, "grades": {"status": "WRITTEN"},
                             "performance": {"status": "ABORTED_INTEGRITY"}})
    assert not ok and "ABORTED_INTEGRITY" in why


# ============================================================================ legacy invariants (restated)
def _r(**k):
    base = {"result": "WIN", "model_probability": 0.6, "edge": 0.05, "ev": 0.1, "units": 1.0, "probability_clv": None,
            "log_odds_clv": None, "clv_status": "NO_CLOSE_QUOTE", "stat": "PTS", "player_role": "CORE_30_PLUS",
            "side": "OVER", "tier": "PASS_NO_EDGE", "slate_date": D1}
    return {**base, **k}


def test_brier_and_log_loss_exact_math():
    p, y = [0.6, 0.7, 0.55, 0.8], ["WIN", "LOSS", "WIN", "WIN"]
    m = MX.metrics([_r(model_probability=a, result=b) for a, b in zip(p, y)])
    assert m["Brier Score"] == pytest.approx((0.4 ** 2 + 0.7 ** 2 + 0.45 ** 2 + 0.2 ** 2) / 4, abs=1e-15)
    assert m["Log Loss"] == pytest.approx(-(math.log(0.6) + math.log(0.3) + math.log(0.55) + math.log(0.8)) / 4,
                                          abs=1e-15)
    lo = MX.metrics([_r(model_probability=1.0, result="LOSS")])["Log Loss"]       # clipped for numerical protection
    assert math.isfinite(lo) and lo == pytest.approx(-math.log(1 - (1 - MX.EPS)), rel=1e-12)   # p clipped to 1-EPS
    assert MX.metrics([_r(model_probability=0.0, result="WIN")])["Log Loss"] == pytest.approx(-math.log(MX.EPS), rel=1e-12)
    e = MX.metrics([])
    assert e["Brier Score"] is None and e["Log Loss"] is None and e["N"] == 0


def test_push_and_void_excluded_from_brier_win_pct_and_roi():
    rows = [_r(model_probability=0.6, result="WIN", units=1.0), _r(model_probability=0.7, result="LOSS", units=-1.0),
            _r(model_probability=0.9, result="PUSH", units=0.0), _r(model_probability=0.95, result="VOID_DNP", units=None),
            _r(model_probability=0.97, result="VOID_NO_GAME", units=None), _r(model_probability=0.99, result="PENDING",
                                                                               units=None)]
    m = MX.metrics(rows)
    assert (m["N"], m["Graded"], m["Wins"], m["Losses"], m["Pushes"], m["VOID_DNP"], m["VOID_NO_GAME"], m["Pending"]) \
        == (6, 3, 1, 1, 1, 1, 1, 1)
    assert m["Brier Score"] == pytest.approx((0.4 ** 2 + 0.7 ** 2) / 2)
    assert m["Log Loss"] == pytest.approx(-(math.log(0.6) + math.log(0.3)) / 2)
    assert m["Win %"] == m["Actual Hit Rate"] == m["Realized Hit Rate"] == 0.5 and m["Hit Rate"] == pytest.approx(1 / 3)
    assert m["Mean Model Probability"] == pytest.approx(0.65) and m["Calibration Gap"] == pytest.approx(0.5 - 0.65)
    assert (m["Units Won"], m["ROI"]) == (0.0, 0.0)                                 # push refunds; ROI over W + L


def test_calibration_bins_exact():
    rows = [_r(model_probability=0.52, result="WIN"), _r(model_probability=0.57, result="LOSS"),
            _r(model_probability=0.72, result="WIN"), _r(model_probability=0.61, result="PUSH"),
            _r(model_probability=0.63, result="VOID_DNP")]
    bins = PF.calibration_bins(rows)
    assert [b[0] for b in bins] == [n for n, _, _ in EX.BANDS]
    b = {r[0]: r for r in bins}
    assert b["50-55%"][1:] == [1, 0.52, 1.0, pytest.approx(0.48 ** 2)]
    assert b["60-65%"][1] == 0 and b["60-65%"][2] is None                         # push / void never binned
    assert sum(r[1] for r in bins) == 3


def test_group_tables_reconcile_to_overall(two_days):
    ops, out = two_days
    PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    rows = PF.read_dataset(ops / PF.DATASET)
    t = PF.tables(rows)
    ov = dict(zip(t["Overall"][0]["columns"], t["Overall"][0]["rows"][0]))
    for tab, width in (("By Stat", 1), ("By Player Role", 1), ("By Side", 1), ("By Selection Tier", 1), ("Daily", 1),
                       ("Stat x Role", 2)):
        cols, body = t[tab][0]["columns"], t[tab][0]["rows"]
        for k in ("Predictions", "Graded", "Wins", "Losses", "Pushes", "VOID_DNP", "VOID_NO_GAME", "Pending",
                  "Exact-Line CLV N", "Line-Moved N", "No-Close N", "No-Devig-Pair N"):
            assert sum(dict(zip(cols, r))[k] for r in body) == ov[k], (tab, k)
        assert sum(dict(zip(cols, r))["Units Won"] or 0 for r in body) == pytest.approx(ov["Units Won"])
    assert sum(r[1] for r in t["Calibration"][0]["rows"]) == ov["Wins"] + ov["Losses"]
    assert len(t["Selections"][0]["rows"]) == ov["Predictions"] == 3


def test_zero_count_role_rows_kept(two_days):
    ops, out = two_days
    PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    rows = PF.read_dataset(ops / PF.DATASET)
    for tbl in (PF.tables(rows)["By Player Role"][0], {"columns": ("Player Role",) + MX.GRADE_METRICS,
                                                       "rows": MX.group_rows(rows, ("player_role",), MX.GRADE_METRICS)}):
        body = [dict(zip(tbl["columns"], r)) for r in tbl["rows"]]
        assert [r["Player Role"] for r in body] == list(EX.ROLES)                 # full taxonomy, canonical order
        assert sum(r["N"] if "N" in r else r["Predictions"] for r in body) == 3
        empty = [r for r in body if (r.get("N") if "N" in r else r["Predictions"]) == 0]
        assert empty and all(r["Brier Score"] is None and r["ROI"] is None for r in empty)
    sides = [r[0] for r in PF.tables(rows)["By Side"][0]["rows"]]
    assert sides == ["OVER", "UNDER"]
    assert [r[0] for r in PF.tables(rows)["By Selection Tier"][0]["rows"]] == list(MX.ME.TIERS)


def test_point_in_time_probability_is_the_frozen_evaluation(tmp_path):
    ops = tmp_path / "ops"
    tip = pd.Timestamp("2026-11-05T00:00Z")
    pm = _day(ops, D1, "G1", tip, {"A": {6: 0.3, 14: 0.7}}, {"A": 14.0})
    PF.export(D1, ops, tmp_path / "x", pd.Timestamp("2026-11-06T12:00Z"))
    before = PF.read_dataset(ops / PF.DATASET)[0]["model_probability"]
    newer = [{**pm[0], "pmf": _pmf({6: 0.9, 14: 0.1}), "prediction_as_of": tip - pd.Timedelta(minutes=61)}]
    assert MX.freeze(ops, D1, newer, {}, {}, {"G1": tip - pd.Timedelta(hours=1)}, tip)["written"] == 0
    rep = PF.export(D1, ops, tmp_path / "x", pd.Timestamp("2026-11-07T12:00Z"))
    assert rep["status"] == "WRITTEN" and PF.read_dataset(ops / PF.DATASET)[0]["model_probability"] == before == 0.7


def test_point_in_time_fields_never_after_cutoff(two_days):
    ops, out = two_days
    PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    for r in PF.read_dataset(ops / PF.DATASET):
        cut = pd.Timestamp(r["cutoff_utc"])
        assert pd.Timestamp(r["prediction_as_of"]) <= pd.Timestamp(r["entry_received_at"]) < cut
        assert pd.Timestamp(r["quote_received_at"]) < cut
        if r["close_ts"] is not None:
            assert pd.Timestamp(r["entry_received_at"]) <= pd.Timestamp(r["close_ts"]) <= min(cut, pd.Timestamp(r["tip_utc"]))
        assert r["player_role"] in EX.ROLES                     # prediction-time minutes, never the realized minutes


def _one(tmp_path, obs, w={6: 0.3, 14: 0.7}, actual=14.0):
    ops = tmp_path / "ops"
    _day(ops, D1, "G1", pd.Timestamp("2026-11-05T00:00Z"), {"A": w}, {"A": actual}, obs=obs)
    PF.export(D1, ops, tmp_path / "x", pd.Timestamp("2026-11-06T12:00Z"))
    return PF.read_dataset(ops / PF.DATASET)[0]


def test_exact_line_log_odds_and_no_devig_pair(tmp_path):
    r = _one(tmp_path / "a", [(120, [("A", "bk", 10.5, "OVER", -110), ("A", "bk", 10.5, "UNDER", -110)]),
                              (15, [("A", "bk", 10.5, "OVER", -130), ("A", "bk", 10.5, "UNDER", 110)])])
    ef = 0.5
    cf = (1 / ME_dec(-130)) / (1 / ME_dec(-130) + 1 / ME_dec(110))
    assert r["clv_status"] == "EXACT_LINE" and r["close_fair_probability"] == pytest.approx(cf)
    assert r["probability_clv"] == pytest.approx(cf - ef) and r["probability_clv"] > 0
    assert r["log_odds_clv"] == pytest.approx(math.log(cf / (1 - cf)) - math.log(ef / (1 - ef)))
    assert r["directional_line_movement"] == 0.0
    r = _one(tmp_path / "b", [(120, [("A", "bk", 10.5, "OVER", -110), ("A", "bk", 10.5, "UNDER", -110)]),
                              (15, [("A", "bk", 10.5, "OVER", -130)])])                  # one-sided close
    assert r["clv_status"] == "NO_DEVIG_PAIR" and r["probability_clv"] is None and r["close_fair_probability"] is None


def ME_dec(am):
    return 1 + (am / 100 if am > 0 else 100 / abs(am))


def test_same_line_nearest_close_and_reference_book(tmp_path):
    two = lambda bk, o, u: [("A", bk, 10.5, "OVER", o), ("A", bk, 10.5, "UNDER", u)]
    # same book first: the latest observation lacks the entry book, an earlier in-window one has it
    r = _one(tmp_path / "a", [(120, two("bk", -110, -110)), (40, two("bk", -120, 100)), (10, two("other", -140, 120))])
    assert r["clv_status"] == "SAME_LINE_NEAREST_CLOSE" and r["close_book"] == "bk"
    assert r["close_fair_probability"] == pytest.approx((1 / ME_dec(-120)) / (1 / ME_dec(-120) + 1 / ME_dec(100)))
    # the entry book never re-quoted after the entry: another book at the same line in the latest observation
    r = _one(tmp_path / "b", [(120, two("bk", -110, -110)), (10, two("other", -140, 120) + two("abc", -125, 105))])
    assert r["clv_status"] == "SAME_LINE_NEAREST_CLOSE" and r["close_book"] == "abc"   # alphabetical reference book


def test_line_moved_directional_sign_and_no_probability_clv(tmp_path):
    two = lambda ln, o, u: [("A", "bk", ln, "OVER", o), ("A", "bk", ln, "UNDER", u)]
    r = _one(tmp_path / "o", [(120, two(10.5, -110, -110)), (15, two(11.5, -110, -110))])          # OVER entry
    assert r["side"] == "OVER" and r["clv_status"] == "LINE_MOVED"
    assert (r["close_line"], r["raw_line_movement"], r["directional_line_movement"]) == (11.5, 1.0, 1.0)
    assert r["probability_clv"] is None and r["log_odds_clv"] is None and r["close_fair_probability"] is None
    r = _one(tmp_path / "u", [(120, two(10.5, -110, -110)), (15, two(9.5, -110, -110))], w={6: 0.7, 14: 0.3}, actual=6.0)
    assert r["side"] == "UNDER" and r["clv_status"] == "LINE_MOVED"
    assert (r["close_line"], r["raw_line_movement"], r["directional_line_movement"]) == (9.5, -1.0, 1.0)  # UNDER: down


def test_no_close_and_no_future_quote_used(tmp_path):
    two = lambda o, u: [("A", "bk", 10.5, "OVER", o), ("A", "bk", 10.5, "UNDER", u)]
    # entry at T-30m (the first observation after the PMF); an older T-170m observation and a post-cutoff one exist;
    # the only in-window observation at or after the entry is the entry itself -> EXACT_LINE against itself (CLV 0)
    r = _one(tmp_path / "a", [(200, []), (30, two(-110, -110)), (-20, two(-200, 160))])
    assert r["entry_received_at"] == (pd.Timestamp("2026-11-04T23:00Z") - pd.Timedelta(minutes=30)).isoformat()
    assert r["probability_clv"] == pytest.approx(0.0)                               # the post-cutoff -200 never closes
    # everything in the close window is older than 60 min before min(tip, cutoff): NO_CLOSE_QUOTE
    r = _one(tmp_path / "b", [(120, two(-110, -110)), (-20, two(-200, 160))])
    assert r["clv_status"] == "NO_CLOSE_QUOTE" and r["close_ts"] is None and r["probability_clv"] is None


def test_duplicate_key_prevention(two_days):
    ops, out = two_days
    PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    p = ops / PF.DATASET
    df = pd.read_parquet(p)
    pd.concat([df, df.iloc[:1]]).to_parquet(p, index=False)
    with pytest.raises(PF.PerformanceIntegrityError):
        PF.update(ops, D2, pd.Timestamp("2026-11-06T12:00Z"))
    assert PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))["status"] == "ABORTED_INTEGRITY"


def test_incremental_update_reuses_final_rows_and_rebuilds_changed(tmp_path):
    ops = tmp_path / "ops"
    now = pd.Timestamp("2026-11-06T12:00Z")
    _day(ops, D1, "G1", pd.Timestamp("2026-11-05T00:00Z"), {"A": {6: 0.3, 14: 0.7}, "B": {6: 0.7, 14: 0.3}},
         {"A": 14.0})                                                                # B not settled yet
    u = PF.update(ops, D1, now)
    assert (u["reused"], u["rebuilt"], u["rewritten"]) == (0, 2, True)
    u = PF.update(ops, D1, now)
    assert (u["reused"], u["rebuilt"], u["rewritten"]) == (1, 1, False)             # A final; B still pending
    before = {r["player_id"]: r for r in PF.read_dataset(ops / PF.DATASET)}
    _settle(ops, "G1", {"B": None}, {"B": 6.0}, tick="K-late")
    u = PF.update(ops, D1, now)
    assert (u["reused"], u["rebuilt"], u["rewritten"]) == (1, 1, True)
    after = {r["player_id"]: r for r in PF.read_dataset(ops / PF.DATASET)}
    assert after["A"] == before["A"] and after["B"]["result"] == "WIN"
    changed = {k for k in after["B"] if after["B"][k] != before["B"][k]}
    assert changed <= {"actual", "minutes", "result", "units"}                      # settlement fills only results
    u = PF.update(ops, D1, now)
    assert (u["reused"], u["rebuilt"], u["rewritten"]) == (2, 0, False)


def test_deterministic_rerun_and_receipt_hashes(two_days):
    ops, out = two_days
    now = pd.Timestamp("2026-11-06T12:00Z")
    rep = PF.export(D2, ops, out / D2, now)
    wb, ds, rec_p = Path(rep["path"]), ops / PF.DATASET, out / D2 / PF.RECEIPT
    b = (wb.read_bytes(), ds.read_bytes(), rec_p.read_text())
    rep2 = PF.export(D2, ops, out / D2, now)
    assert (wb.read_bytes(), ds.read_bytes(), rec_p.read_text()) == b and rep2["rewritten"] is False
    rec = json.loads(rec_p.read_text())
    assert rec["workbook_sha256"] == EX._file_sha(wb) and rec["canonical_dataset_sha256"] == EX._file_sha(ds)
    for k in ("version", "through_date", "start_date", "evaluations", "graded", "void_dnp", "pending", "brier_score",
              "log_loss", "roi", "units_won", "exact_line_clv_n", "mean_probability_clv", "positive_clv_pct",
              "canonical_dataset_sha256", "workbook_sha256", "content_sha256", "writer_sha256"):
        assert k in rec, k
    assert (rec["through_date"], rec["evaluations"], rec["graded"], rec["void_dnp"]) == (D2, 3, 2, 1)
    wb.write_bytes(wb.read_bytes() + b"x")                               # tampered workbook -> evidence FAIL
    assert PF.verify(wb.parent, D2, ops)["RECEIPT_VALID"] is False
    assert PF.export(D2, ops, out / D2, now)["status"] == "WRITTEN"      # rewritten from the canonical rows
    assert PF.verify(wb.parent, D2, ops)["PERFORMANCE_EVIDENCE"] == "PASS"


def test_workbook_schema_and_formats(two_days):
    ops, out = two_days
    rep = PF.export(D2, ops, out / D2, pd.Timestamp("2026-11-06T12:00Z"))
    wb = EX.read_xlsx(Path(rep["path"]))
    assert tuple(wb) == PF.SHEETS
    assert tuple(wb["Selections"]["rows"][0]) == PF.SEL_COLS
    assert tuple(wb["By Stat"]["rows"][0]) == ("Stat",) + PF.METRICS
    assert tuple(wb["Stat x Role"]["rows"][0]) == ("Stat", "Player Role") + PF.METRICS
    assert tuple(wb["Daily"]["rows"][0]) == ("Date",) + PF.METRICS
    assert tuple(wb["Calibration"]["rows"][0]) == PF.BIN_COLS
    for name in PF.SHEETS:
        assert wb[name]["freeze"] and wb[name]["filters"], name
    hdr = wb["By Stat"]["rows"][0]
    st = wb["By Stat"]["styles"]
    col = lambda c: EX._col(hdr.index(c))
    assert st[f"{col('Brier Score')}2"] == "0.0000" and st[f"{col('Win %')}2"] == MX.PCT
    assert st[f"{col('Mean Model Probability')}2"] == MX.PCT
    for metric in ("Brier Score", "Log Loss", "Win %", "ROI", "Units Won", "Mean Probability CLV", "Mean Log-Odds CLV",
                   "Positive CLV %", "Exact-Line CLV N", "Line-Moved N", "No-Close N", "Calibration Gap"):
        assert metric in hdr
    flat = json.dumps([wb[s]["rows"] for s in PF.SHEETS], default=str).lower()
    assert "probabilities" not in flat and "pmf_vector" not in flat               # no PMF vectors in the workbook
    assert set(pd.read_parquet(ops / PF.DATASET).columns).isdisjoint({"pmf", "probabilities", "pmf_vector"})


def test_empty_and_not_yet_graded(tmp_path):
    rep = PF.export(D1, tmp_path / "e", tmp_path / "x", pd.Timestamp("2026-11-06T12:00Z"))
    assert rep["status"] == "WRITTEN" and rep["PERFORMANCE_STATUS"] == "NO_MARKET_EVIDENCE_YET" and rep["rows"] == 0
    wb = EX.read_xlsx(Path(rep["path"]))
    assert tuple(wb) == PF.SHEETS and wb["Selections"]["rows"][1:] == [] and wb["Daily"]["rows"][1:] == []
    ops = tmp_path / "p"
    _day(ops, D1, "G1", pd.Timestamp("2026-11-05T00:00Z"), {"A": {6: 0.3, 14: 0.7}}, {})       # frozen, not settled
    rep = PF.export(D1, ops, tmp_path / "y", pd.Timestamp("2026-11-05T06:00Z"))
    assert rep["PERFORMANCE_STATUS"] == "NOTHING_GRADED_YET" and rep["rows"] == 1 and rep["brier_score"] is None
    ops = tmp_path / "b"                                                             # before the cutoff: nothing frozen
    QO.record(ops, D1, "G1", "E", pd.Timestamp("2026-11-04T21:00Z"),
              pd.DataFrame([_q("G1", "A", "bk", 10.5, s, -110, pd.Timestamp("2026-11-04T21:00Z"))
                            for s in ("OVER", "UNDER")]), "QUOTE_REFRESH")
    assert PF.export(D1, ops, tmp_path / "z", pd.Timestamp("2026-11-04T22:55Z"))["rows"] == 0
