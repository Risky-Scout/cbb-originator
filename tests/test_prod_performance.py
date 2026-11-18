"""CBB_Performance (prod/performance.py): cumulative market evaluation of the frozen pre-cutoff market evaluations.
Synthetic evidence only; no network, no model."""
import json

import numpy as np
import pandas as pd
import pytest

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


def _day(ops, day, gid, tip, players, actual):
    """one game: a decision observation 2 h before the cutoff, a close 15 min before it, frozen, then settled."""
    cut = tip - pd.Timedelta(hours=1)
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
    t = ops / "ticks" / f"K-{gid}"
    t.mkdir(parents=True)
    pd.DataFrame([{"game_id": gid, "player_id": p, "participation": "PLAYED" if actual[p] is not None else "DNP",
                   "settled_at": "x", "minutes": 30.0 if actual[p] is not None else 0.0, "act_PTS": actual[p]}
                  for p in players]).to_parquet(t / "actuals.parquet")


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
