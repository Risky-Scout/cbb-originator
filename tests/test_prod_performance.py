"""CBB_Performance (prod/performance.py): cumulative evaluation of the OFFICIAL frozen selections.

TEST fixtures only (data_label "TEST", tmp_path evidence trees): official selections are produced by the real
exports.daily() path from QUOTE_SIDE receipts; closes by prod.quote_obs.record; grades by side_settlements. No network,
no refit, no model change.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import exports as EX, performance as PF, quote_obs as QO
from tests.test_prod_exports import _game, _qs, _sett, build_tree

D1, D2 = "2026-11-04", "2026-11-05"
TIP1, TIP2 = "2026-11-05T00:00:00+00:00", "2026-11-06T00:00:00+00:00"     # cutoffs: TIP - 1h
AFTER1, AFTER2 = "2026-11-05T06:00:00+00:00", "2026-11-06T06:00:00+00:00"


def _o(rid, game, player, stat, line, side, book, p, odds, *, minutes=32.0, day=D1, run="R1"):
    return _qs(rid, game, player, stat, line, side, book, p, odds, minutes=minutes, edge=p - 0.5, tick_run=run,
               aso=f"{day}T22:50:00+00:00", recv=f"{day}T22:45:00+00:00")


def _two_day_tree(tmp_path, settlements=None):
    """D1: G1 Zed PTS OVER 17.5 (bookb, two-sided), G1 Amy REB UNDER 5.5 (booka, two-sided).
    D2: G2 Bo AST OVER 3.5 (bookb, two-sided, bench minutes)."""
    rc = [_o("z_o", "G1", "Zed", "PTS", 17.5, "OVER", "bookb", 0.60, 120, minutes=34.0),
          _o("z_u", "G1", "Zed", "PTS", 17.5, "UNDER", "bookb", 0.40, -150, minutes=34.0),
          _o("a_u", "G1", "Amy", "REB", 5.5, "UNDER", "booka", 0.62, -110, minutes=24.0),
          _o("a_o", "G1", "Amy", "REB", 5.5, "OVER", "booka", 0.38, -110, minutes=24.0),
          _o("b_o", "G2", "Bo", "AST", 3.5, "OVER", "bookb", 0.58, 105, minutes=12.0, day=D2, run="R2"),
          _o("b_u", "G2", "Bo", "AST", 3.5, "UNDER", "bookb", 0.42, -135, minutes=12.0, day=D2, run="R2")]
    g2 = {**_game("G2", TIP2, "K2", "R2"), "prediction_as_of": f"{D2}T23:00:00+00:00"}
    return build_tree(tmp_path, rc, [_game("G1", TIP1, "K1", "R1"), g2], settlements)


def _daily(tree, day, now):
    return EX.daily(day, ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
                    history_db=None, state_root=None, labels=("TEST",), now=now)


def _obs(tree, day, game, recv, rows, source="TEST_CLOSE"):
    base = {"provider_event_id": "E", "is_alternate": False, "provider_player_name": None, "market_key": "k",
            "market_last_update": recv, "bookmaker_last_update": recv, "quote_available_at": recv,
            "collector_received_at": recv, "canonical_game_id": game, "quote_status": "VALID", "suppression_reason": None}
    df = pd.DataFrame([{**base, "canonical_player_id": p, "stat": s, "line": ln, "side": sd, "sportsbook": bk,
                        "american_odds": am, "decimal_odds": EX.decimal_odds(am)} for p, s, ln, sd, bk, am in rows])
    QO.record(tree["ops_root"], day, game, "E", recv, df, source)


def _rows(tree):
    return {(r["player_id"], r["stat"]): r for r in PF.read_dataset(tree["ops_root"] / PF.DATASET)}


def _wb(rep):
    return EX.read_xlsx(Path(rep["performance"]["path"]))


def _tab(rep, sheet):
    rows = _wb(rep)[sheet]["rows"]
    if sheet == "Overall":                                 # titled blocks: "Overall" title, header, the ALL row
        return [dict(zip(rows[1], rows[2]))]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


# ---------------------------------------------------------------- 7, 8 Brier / Log Loss exact math
def test_brier_and_log_loss_exact_math():
    p, y = [0.6, 0.7, 0.55, 0.8], [1, 0, 1, 1]
    assert PF.brier(p, y) == pytest.approx(((0.4) ** 2 + 0.7 ** 2 + 0.45 ** 2 + 0.2 ** 2) / 4, abs=1e-15)
    ll = -(math.log(0.6) + math.log(0.3) + math.log(0.55) + math.log(0.8)) / 4
    assert PF.log_loss(p, y) == pytest.approx(ll, abs=1e-15)
    assert math.isfinite(PF.log_loss([1.0, 0.0], [0, 1]))                 # clipped for numerical protection only
    assert PF.log_loss([1.0], [0]) == pytest.approx(-math.log(1 - (1 - 1e-15)), rel=1e-12)   # p clipped to 1-1e-15
    assert PF.log_loss([0.0], [1]) == pytest.approx(-math.log(1e-15), rel=1e-12)
    assert PF.brier([], []) is None and PF.log_loss([], []) is None


def _r(**k):
    base = {"integrity_status": "PASS", "grade": "WIN", "model_probability": 0.6, "model_edge": 0.05, "slate_date": D1,
            "entry_implied_probability": 0.5, "entry_fair_probability": 0.55, "closing_fair_probability": None,
            "probability_clv": None, "log_odds_clv": None, "clv_status": "NO_CLOSE_QUOTE", "closing_line": None,
            "directional_line_movement": None, "entry_decimal_price": 2.0, "pnl": 1.0, "stat": "PTS",
            "player_role": "CORE_30_PLUS"}
    return {**base, **k}


def test_push_and_void_excluded_from_brier_win_pct_and_roi():
    rows = [_r(model_probability=0.6, grade="WIN", pnl=1.0), _r(model_probability=0.7, grade="LOSS", pnl=-1.0),
            _r(model_probability=0.9, grade="PUSH", pnl=0.0), _r(model_probability=0.95, grade="VOID", pnl=0.0),
            _r(model_probability=0.99, grade="PENDING", pnl=None)]
    m = PF.metrics(rows)
    assert (m["Predictions"], m["Graded"], m["Wins"], m["Losses"], m["Pushes"], m["Voids"], m["Pending"]) == (5, 3, 1, 1, 1, 1, 1)
    assert m["Brier Score"] == pytest.approx((0.4 ** 2 + 0.7 ** 2) / 2)
    assert m["Log Loss"] == pytest.approx(-(math.log(0.6) + math.log(0.3)) / 2)
    assert m["Win %"] == m["Actual Hit Rate"] == 0.5
    assert m["Mean Model Probability"] == pytest.approx(0.65) and m["Calibration Gap"] == pytest.approx(0.5 - 0.65)
    assert (m["Units Risked"], m["Net Units"], m["ROI"]) == (2, 0.0, 0.0)              # push refunds (exports.profit)


def test_calibration_bins_and_integrity_exclusion():
    rows = [_r(model_probability=0.52, grade="WIN"), _r(model_probability=0.57, grade="LOSS"),
            _r(model_probability=0.72, grade="WIN"), _r(model_probability=0.61, grade="WIN",
                                                        integrity_status="FUTURE_LEAK:entry_quote_not_before_selection")]
    b = {r[0]: r for r in PF.calibration_bins(rows)}
    assert [r[0] for r in PF.calibration_bins(rows)] == [n for n, _, _ in EX.BANDS]
    assert b["50-55%"][1:] == [1, 0.52, 1.0, pytest.approx(0.48 ** 2)]
    assert b["60-65%"][1] == 0                                                         # the leaking row is excluded
    m = PF.metrics(rows)
    assert m["Predictions"] == 3 and m["Integrity Excluded"] == 1


# ---------------------------------------------------------------- end-to-end: selections -> dataset -> workbook
def test_cumulative_window_grouping_and_daily(tmp_path):
    st = [_sett("z_o", "WIN", 20.0), _sett("a_u", "LOSS", 7.0), _sett("b_o", "PUSH", 3.5)]
    tree = _two_day_tree(tmp_path, st)
    r1 = _daily(tree, D1, AFTER1)
    assert r1["performance"]["status"] == "WRITTEN" and r1["performance"]["rows"] == 2     # D2 not yet official
    r2 = _daily(tree, D2, AFTER2)
    assert r2["performance"]["rows"] == 3 and r2["performance"]["PERFORMANCE_STATUS"] == "GRADED"
    # a re-export of D1 still reports through D1 only (no future dates) and leaves the D2 row intact
    r1b = _daily(tree, D1, AFTER2)
    assert [d["Date"] for d in _tab(r1b, "Daily")] == [D1]
    assert len(PF.read_dataset(tree["ops_root"] / PF.DATASET)) == 3
    daily = _tab(r2, "Daily")
    assert [(d["Date"], d["Predictions"], d["Wins"], d["Losses"], d["Pushes"]) for d in daily] == \
        [(D1, 2, 1, 1, 0), (D2, 1, 0, 0, 1)]
    by_stat = {d["Stat"]: d for d in _tab(r2, "By Stat")}
    assert set(by_stat) == {"PTS", "REB", "AST"} and by_stat["PTS"]["Wins"] == 1 and by_stat["REB"]["Losses"] == 1
    roles = {d["Player Role"]: d["Predictions"] for d in _tab(r2, "By Player Role")}
    assert roles == {"CORE_30_PLUS": 1, "ROTATION_20_TO_30": 1, "LIMITED_UNDER_20": 1}       # exports.ROLES taxonomy
    sxr = {(d["Stat"], d["Player Role"]): d for d in _tab(r2, "Stat x Role")}
    assert set(sxr) == {("PTS", "CORE_30_PLUS"), ("REB", "ROTATION_20_TO_30"), ("AST", "LIMITED_UNDER_20")}
    ov = _tab(r2, "Overall")[0]
    assert ov["Predictions"] == sum(d["Predictions"] for d in by_stat.values()) == sum(roles.values()) == 3
    assert (ov["First Date"], ov["Last Date"]) == (D1, D2)
    assert ov["Brier Score"] == pytest.approx(((1 - 0.60) ** 2 + (0 - 0.62) ** 2) / 2)    # the push is excluded
    sel = {(d["player_id"], d["stat"]): d for d in _tab(r2, "Selections")}
    z = sel[("Zed", "PTS")]
    assert (z["side"], z["line"], z["entry_book"], z["grade"], z["actual_stat"]) == ("OVER", 17.5, "bookb", "WIN", 20.0)
    assert z["model_probability"] == 0.60 and z["pnl"] == pytest.approx(1.2)
    assert z["entry_fair_probability"] == pytest.approx(EX.no_vig("OVER", 2.2, EX.decimal_odds(-150)))
    assert z["model_edge"] == pytest.approx(0.60 - z["entry_fair_probability"])
    assert z["entry_implied_probability"] == pytest.approx(1 / 2.2)
    assert z["official_selection_timestamp"] == "2026-11-04T23:00:00+00:00" and z["integrity_status"] == "PASS"
    assert z["opponent"] is None or isinstance(z["opponent"], str)


def test_point_in_time_probability_is_the_frozen_receipt(tmp_path):
    """A newer forecast / repricing after the selection froze never replaces the official probability."""
    tree = _two_day_tree(tmp_path, [_sett("z_o", "WIN", 20.0)])
    _daily(tree, D1, AFTER1)
    before = _rows(tree)[("Zed", "PTS")]["model_probability"]
    sel = EX.load_selections(EX.selections_path(tree["ops_root"], D1))
    assert sel[("G1", "Zed", "PTS")]["frozen"]["model_p"] == before == 0.60
    rep = _daily(tree, D1, "2026-11-07T00:00:00+00:00")
    assert _rows(tree)[("Zed", "PTS")]["model_probability"] == 0.60 and rep["performance"]["status"] == "WRITTEN"


def test_exact_line_clv_devig_and_positive_sign(tmp_path):
    tree = _two_day_tree(tmp_path)
    _obs(tree, D1, "G1", "2026-11-04T23:40:00+00:00",                   # after the cutoff, before the tip
         [("Zed", "PTS", 17.5, "OVER", "bookb", 100), ("Zed", "PTS", 17.5, "UNDER", "bookb", -130),
          ("Amy", "REB", 5.5, "UNDER", "booka", -105)])                  # one-sided close of Amy's selection
    _daily(tree, D1, AFTER1)
    z, a = _rows(tree)[("Zed", "PTS")], _rows(tree)[("Amy", "REB")]
    ef, cf = EX.no_vig("OVER", 2.2, EX.decimal_odds(-150)), EX.no_vig("OVER", 2.0, EX.decimal_odds(-130))
    assert z["clv_status"] == "EXACT_LINE" and z["close_book"] == "bookb" and z["closing_line"] == 17.5
    assert z["closing_fair_probability"] == pytest.approx(cf) and z["entry_fair_probability"] == pytest.approx(ef)
    assert z["probability_clv"] == pytest.approx(cf - ef) and z["probability_clv"] > 0   # market moved to our side
    assert z["log_odds_clv"] == pytest.approx(math.log(cf / (1 - cf)) - math.log(ef / (1 - ef)))
    assert z["closing_implied_probability"] == pytest.approx(0.5) and z["directional_line_movement"] == 0.0
    assert a["clv_status"] == "NO_DEVIG_PAIR" and a["probability_clv"] is None and a["closing_fair_probability"] is None


def test_same_line_nearest_close_and_reference_book(tmp_path):
    tree = _two_day_tree(tmp_path)
    _obs(tree, D1, "G1", "2026-11-04T23:20:00+00:00", [("Zed", "PTS", 17.5, "OVER", "bookb", 110),
                                                       ("Zed", "PTS", 17.5, "UNDER", "bookb", -140)], "S1")
    _obs(tree, D1, "G1", "2026-11-04T23:50:00+00:00", [("Zed", "PTS", 17.5, "OVER", "bookc", 105),
                                                       ("Zed", "PTS", 17.5, "UNDER", "bookc", -125)], "S2")
    _daily(tree, D1, AFTER1)
    z = _rows(tree)[("Zed", "PTS")]
    assert z["clv_status"] == "SAME_LINE_NEAREST_CLOSE" and z["close_book"] == "bookb"   # same book first
    assert z["close_ts"] == "2026-11-04T23:20:00+00:00"
    assert z["closing_fair_probability"] == pytest.approx(EX.no_vig("OVER", 2.1, EX.decimal_odds(-140)))


def test_line_moved_directional_sign_and_no_probability_clv(tmp_path):
    tree = _two_day_tree(tmp_path)
    _obs(tree, D1, "G1", "2026-11-04T23:30:00+00:00",
         [("Zed", "PTS", 18.5, "OVER", "bookb", -110), ("Zed", "PTS", 18.5, "UNDER", "bookb", -110),
          ("Amy", "REB", 4.5, "UNDER", "booka", -110), ("Amy", "REB", 4.5, "OVER", "booka", -110)])
    _daily(tree, D1, AFTER1)
    z, a = _rows(tree)[("Zed", "PTS")], _rows(tree)[("Amy", "REB")]
    assert z["clv_status"] == a["clv_status"] == "LINE_MOVED"
    assert (z["closing_line"], z["raw_line_movement"], z["directional_line_movement"]) == (18.5, 1.0, 1.0)  # OVER: up
    assert (a["closing_line"], a["raw_line_movement"], a["directional_line_movement"]) == (4.5, -1.0, 1.0)  # UNDER: down
    assert z["probability_clv"] is None and z["log_odds_clv"] is None and a["probability_clv"] is None


def test_no_close_and_no_future_quote_used(tmp_path):
    tree = _two_day_tree(tmp_path)
    _obs(tree, D1, "G1", "2026-11-05T00:30:00+00:00", [("Zed", "PTS", 17.5, "OVER", "bookb", -200),
                                                       ("Zed", "PTS", 17.5, "UNDER", "bookb", 160)])   # after the tip
    _obs(tree, D1, "G1", "2026-11-04T22:00:00+00:00", [("Zed", "PTS", 17.5, "OVER", "bookb", -200),
                                                       ("Zed", "PTS", 17.5, "UNDER", "bookb", 160)], "OLD")  # pre-entry
    _daily(tree, D1, AFTER1)
    for r in _rows(tree).values():
        assert r["clv_status"] == "NO_CLOSE_QUOTE" and r["close_ts"] is None and r["integrity_status"] == "PASS"


def test_integrity_flags_future_information():
    ok = {"model_probability": 0.6, "side": "OVER", "line": 17.5, "entry_american_odds": 120.0,
          "official_selection_ts": "2026-11-04T23:00:00+00:00", "tip_utc": TIP1,
          "prediction_as_of": "2026-11-04T22:50:00+00:00", "entry_capture_utc": "2026-11-04T22:45:00+00:00",
          "close_ts": "2026-11-04T23:40:00+00:00", "player_role": "CORE_30_PLUS", "expected_minutes": 34.0}
    assert PF.integrity(ok) == "PASS"
    cases = {"prediction_as_of": ("2026-11-04T23:10:00+00:00", "FUTURE_LEAK:prediction_after_selection"),
             "entry_capture_utc": ("2026-11-04T23:00:00+00:00", "FUTURE_LEAK:entry_quote_not_before_selection"),
             "close_ts": ("2026-11-05T00:00:00+00:00", "FUTURE_LEAK:close_outside_entry_to_tip"),
             "player_role": ("LIMITED_UNDER_20", "FUTURE_LEAK:role_not_prediction_time"),
             "official_selection_ts": ("2026-11-05T00:30:00+00:00", "FUTURE_LEAK:selection_after_tip"),
             "model_probability": (None, "MISSING_EVIDENCE:model_probability"),
             "tip_utc": (None, "MISSING_EVIDENCE:tip")}
    for k, (v, want) in cases.items():
        assert PF.integrity({**ok, k: v}) == want, k
    # actuals enter only grading fields: none of the point-in-time fields are derived from them
    assert not {"actual_stat", "outcome", "grade", "pnl"} & {"model_probability", "player_role", "entry_fair_probability"}


# ---------------------------------------------------------------- keys, incremental, deterministic
def test_duplicate_key_prevention(tmp_path):
    tree = _two_day_tree(tmp_path)
    _daily(tree, D1, AFTER1)
    p = tree["ops_root"] / PF.DATASET
    df = pd.read_parquet(p)
    pd.concat([df, df.iloc[:1]]).to_parquet(p, index=False)
    with pytest.raises(PF.PerformanceIntegrityError):
        PF.update(tree["ops_root"], D1, AFTER1)
    rep = _daily(tree, D1, AFTER1)
    assert rep["performance"]["status"] == "ABORTED_INTEGRITY"
    keys = [PF.perf_key({"official_selection_id": "s", "game_id": "G", "player_id": "P", "stat": "PTS", "side": s,
                         "line": ln, "official_selection_ts": "t"}) for s in ("OVER", "UNDER") for ln in (17.5, 18.5)]
    assert len(set(keys)) == 4


def test_incremental_update_reuses_final_rows_and_rebuilds_changed(tmp_path):
    tree = _two_day_tree(tmp_path, [_sett("z_o", "WIN", 20.0)])          # Amy pending
    _daily(tree, D1, AFTER1)
    u = PF.update(tree["ops_root"], D1, AFTER1)
    assert (u["reused"], u["rebuilt"], u["rewritten"]) == (1, 1, False)    # Zed final; Amy still pending
    s = tree["ops_root"] / "ticks" / "KSETTLE2"
    s.mkdir()
    pd.DataFrame([_sett("a_u", "WIN", 4.0)]).to_parquet(s / "side_settlements.parquet", index=False)
    u2 = PF.update(tree["ops_root"], D1, AFTER1)
    assert (u2["reused"], u2["rebuilt"], u2["rewritten"]) == (1, 1, True)
    assert _rows(tree)[("Amy", "REB")]["grade"] == "WIN"
    u3 = PF.update(tree["ops_root"], D1, AFTER1)
    assert (u3["reused"], u3["rebuilt"], u3["rewritten"]) == (2, 0, False)


def test_deterministic_rerun_and_receipt_hashes(tmp_path):
    tree = _two_day_tree(tmp_path, [_sett("z_o", "WIN", 20.0), _sett("a_u", "LOSS", 7.0)])
    rep = _daily(tree, D1, AFTER1)
    wb, ds = Path(rep["performance"]["path"]), tree["ops_root"] / PF.DATASET
    rec_p = wb.parent / PF.RECEIPT
    b = (wb.read_bytes(), ds.read_bytes(), rec_p.read_text())
    rep2 = _daily(tree, D1, AFTER1)
    assert (wb.read_bytes(), ds.read_bytes(), rec_p.read_text()) == b and rep2["performance"]["rewritten"] is False
    rec = json.loads(rec_p.read_text())
    assert rec["workbook_sha256"] == EX._file_sha(wb) and rec["canonical_dataset_sha256"] == EX._file_sha(ds)
    for k in ("version", "through_date", "total_selections", "graded_selections", "brier_score", "log_loss",
              "exact_line_clv_n", "mean_probability_clv", "positive_clv_pct", "stat_category_count",
              "role_category_count", "canonical_dataset_sha256", "workbook_sha256"):
        assert k in rec, k
    assert (rec["through_date"], rec["total_selections"], rec["graded_selections"]) == (D1, 2, 2)
    assert rec["brier_score"] == pytest.approx(((1 - 0.6) ** 2 + 0.62 ** 2) / 2)
    assert PF.verify(wb.parent, D1, tree["ops_root"])["PERFORMANCE_EVIDENCE"] == "PASS"
    wb.write_bytes(wb.read_bytes() + b"x")                               # tampered workbook -> evidence FAIL
    assert PF.verify(wb.parent, D1, tree["ops_root"])["RECEIPT_VALID"] is False
    assert _daily(tree, D1, AFTER1)["performance"]["status"] == "WRITTEN"   # rewritten from the canonical rows


def test_workbook_schema_and_formats(tmp_path):
    tree = _two_day_tree(tmp_path, [_sett("z_o", "WIN", 20.0)])
    rep = _daily(tree, D1, AFTER1)
    wb = _wb(rep)
    assert tuple(wb) == PF.SHEETS
    assert tuple(wb["Selections"]["rows"][0]) == PF.SEL_COLS
    assert tuple(wb["By Stat"]["rows"][0]) == ("Stat",) + PF.METRICS
    assert tuple(wb["Stat x Role"]["rows"][0]) == ("Stat", "Player Role") + PF.METRICS
    assert tuple(wb["Daily"]["rows"][0]) == PF.DAILY_COLS
    for name in PF.SHEETS:
        assert wb[name]["freeze"] and wb[name]["filters"], name
    hdr = wb["By Stat"]["rows"][0]
    st = wb["By Stat"]["styles"]
    col = lambda c: EX._col(hdr.index(c))
    assert st[f"{col('Brier Score')}2"] == "0.0000" and st[f"{col('Win %')}2"] == "0.0%"
    assert st[f"{col('Mean Model Probability')}2"] == "0.0%"
    for metric in PF.METRICS:                                              # every required metric is present
        assert metric in hdr
    flat = json.dumps(wb["Selections"]["rows"], default=str)
    assert "pmf" not in flat.lower().replace("pmf_", "")                   # no PMF vectors in the workbook
    assert set(PF.PERF_COLS).isdisjoint({"pmf", "probabilities", "pmf_vector"})


def test_empty_and_not_yet_graded(tmp_path):
    tree = build_tree(tmp_path / "e", [], [_game("G1", TIP1)])
    rep = _daily(tree, D1, AFTER1)
    assert rep["performance"]["status"] == "WRITTEN" and rep["performance"]["PERFORMANCE_STATUS"] == "NO_OFFICIAL_SELECTIONS_YET"
    assert tuple(_wb(rep)) == PF.SHEETS and _tab(rep, "Overall")[0]["Predictions"] == 0
    assert _tab(rep, "Selections") == [] and _tab(rep, "Daily") == []
    tree2 = _two_day_tree(tmp_path / "p")                                  # official, nothing settled
    rep2 = _daily(tree2, D1, AFTER1)
    assert rep2["performance"]["PERFORMANCE_STATUS"] == "NOTHING_GRADED_YET"
    ov = _tab(rep2, "Overall")[0]
    assert ov["Predictions"] == 2 and ov["Graded"] == 0 and ov["Brier Score"] is None and ov["Win %"] is None
    assert {r["grade"] for r in _rows(tree2).values()} == {"PENDING"}
    before_cut = build_tree(tmp_path / "b", [_o("z_o", "G1", "Zed", "PTS", 17.5, "OVER", "bookb", 0.6, 120)],
                            [_game("G1", TIP1)])
    rep3 = _daily(before_cut, D1, "2026-11-04T22:55:00+00:00")             # provisional only: not yet official
    assert rep3["performance"]["rows"] == 0


def test_daily_contract_requires_performance(tmp_path):
    from cbb_platform.prod import morning_stages as MS
    tree = _two_day_tree(tmp_path)
    rep = _daily(tree, D1, AFTER1)
    names = sorted(p.name for p in (tree["out_root"] / D1).glob("*.xlsx"))
    assert {"CBB_Boards_11_04_2026.xlsx", "CBB_Grades_11_04_2026.xlsx", "CBB_Performance_11_04_2026.xlsx"} <= set(names) \
        or {"CBB_Grades_11_04_2026.xlsx", "CBB_Performance_11_04_2026.xlsx"} <= set(names)
    assert EX.file_names(D1)["performance"] == "CBB_Performance_11_04_2026.xlsx"
    ok, why = MS._export_ok({"boards": {"status": "WRITTEN"}, "grades": {"status": "WRITTEN"}})
    assert not ok and "performance=PERFORMANCE_MISSING" in why
    ok, _ = MS._export_ok({"boards": {"status": "WRITTEN"}, "performance": {"status": "WRITTEN"}})
    assert ok
    ok, why = MS._export_ok({"performance": {"status": "ABORTED_INTEGRITY"}})
    assert not ok and "ABORTED_INTEGRITY" in why
    assert rep["performance"]["verification"]["PERFORMANCE_EVIDENCE"] == "PASS"
