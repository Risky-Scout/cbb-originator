"""CBB market-data and evaluation contract: exact-line evaluation (prod.market_eval), Board grain / sheets / columns,
frozen evaluations, Grades (D grades D-1) and Performance (prod.market_exports / prod.performance). Synthetic
evidence only; no network, no model."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import (exports as EX, market_eval as ME, market_exports as MX, performance as PF, pricing as PX,
                               quote_obs as QO)

DAY = "2026-11-20"
PRIOR = "2026-11-19"
TIP = pd.Timestamp("2026-11-20T00:00:00Z")              # 19:00 ET on 2026-11-19
CUT = TIP - pd.Timedelta(hours=1)


def pmf_point(k: int, n: int = 40) -> np.ndarray:
    p = np.zeros(n)
    p[k] = 1.0
    return p


def pmf_mix(weights: dict, n: int = 40) -> np.ndarray:
    p = np.zeros(n)
    for k, w in weights.items():
        p[k] = w
    return p / p.sum()


def dec(american: float) -> float:
    return 1 + (american / 100 if american > 0 else 100 / abs(american))


def q(rows):
    """quote rows of one player x stat (VALID, matched)."""
    out = []
    for book, line, side, am in rows:
        out.append({"provider_event_id": "E1", "sportsbook": book, "market_key": "player_points", "is_alternate": False,
                    "provider_player_name": "A", "stat": "PTS", "line": line, "side": side, "american_odds": am,
                    "decimal_odds": dec(am), "market_last_update": CUT - pd.Timedelta(minutes=30),
                    "bookmaker_last_update": None, "quote_available_at": None,
                    "collector_received_at": CUT - pd.Timedelta(minutes=20), "canonical_game_id": "G-1",
                    "canonical_player_id": "P-1", "quote_status": "VALID", "suppression_reason": None})
    return pd.DataFrame(out)


# ============================================================================ exact-line evaluation
def test_every_offer_priced_at_its_exact_line_with_push_and_push_aware_ev():
    pmf = pmf_mix({8: 0.3, 10: 0.2, 12: 0.5})                    # P(X=10) = 0.2 = push at line 10
    c, rej = ME.candidates(pmf, q([("draftkings", 10.0, "OVER", -110), ("draftkings", 10.0, "UNDER", -110),
                                   ("fanduel", 9.5, "OVER", -150)]))
    assert not rej and len(c) == 3
    o = next(x for x in c if x["book"] == "draftkings" and x["side"] == "OVER")
    assert o["p_win"] == pytest.approx(0.5) and o["p_lose"] == pytest.approx(0.3) and o["p_push"] == pytest.approx(0.2)
    assert o["model_p"] == pytest.approx(0.5 / 0.8)                  # P(side | no push)
    assert o["ev"] == pytest.approx(0.5 * (dec(-110) - 1) - 0.3)     # a push refunds: not a loss
    assert o["ev"] == pytest.approx(PX.expected_value(0.5, 0.3, dec(-110)))
    h = next(x for x in c if x["book"] == "fanduel")
    assert h["p_push"] == 0 and h["p_win"] == pytest.approx(0.7)       # half line: no push


def test_devig_only_same_book_same_line_pairs():
    pmf = pmf_mix({8: 0.4, 12: 0.6})
    c, _ = ME.candidates(pmf, q([("draftkings", 10.5, "OVER", -120), ("draftkings", 10.5, "UNDER", 100),
                                 ("fanduel", 10.5, "OVER", -105), ("betmgm", 11.5, "UNDER", -110)]))
    dk = next(x for x in c if x["book"] == "draftkings" and x["side"] == "OVER")
    assert dk["fair_p"] == pytest.approx(ME.no_vig(dec(-120), dec(100))) and dk["paired"]
    assert dk["fair_p"] == pytest.approx(EX.no_vig("OVER", dec(-120), dec(100)))         # the existing method
    fd = next(x for x in c if x["book"] == "fanduel")
    assert fd["fair_p"] is None and fd["edge"] is None                # never another book's / line's price
    mg = next(x for x in c if x["book"] == "betmgm")
    assert mg["fair_p"] is None


def test_best_offer_ordering_ev_then_edge_then_model_p_then_payout_then_book():
    base = {"line": 10.5, "side": "OVER", "model_p": 0.6, "decimal": 1.9, "p_push": 0, "paired": True}
    a = {**base, "book": "fanduel", "ev": 0.05, "edge": 0.02}
    b = {**base, "book": "draftkings", "ev": 0.05, "edge": 0.03}
    assert ME.best_offer([a, b])["book"] == "draftkings"             # equal EV: higher edge
    c = {**base, "book": "betmgm", "ev": 0.06, "edge": None}
    assert ME.best_offer([a, b, c])["book"] == "betmgm"              # higher EV first, even without an edge
    d = {**b, "book": "betrivers", "model_p": 0.61}
    assert ME.best_offer([b, d])["book"] == "betrivers"              # then model probability
    e = {**b, "book": "bovada", "decimal": 1.95}
    assert ME.best_offer([b, e])["book"] == "bovada"                 # then payout
    f = {**b, "book": "betonlineag"}
    assert ME.best_offer([b, f])["book"] == "betonlineag"            # then bookmaker key
    assert ME.ORDER == ("EV", "PROBABILITY_EDGE", "MODEL_PROBABILITY", "DECIMAL_ODDS", "BOOKMAKER_KEY")
    for perm in ([a, b, c, d, e, f], [f, e, d, c, b, a]):
        assert ME.best_offer(perm)["book"] == "betmgm"               # input-order independent


def test_preferred_side_over_or_under_whenever_any_candidate_has_positive_ev():
    under_heavy = pmf_mix({6: 0.8, 14: 0.2})
    ev = ME.evaluate(under_heavy, q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110)]))
    assert ev["preferred_side"] == "UNDER" and ev["best"]["ev"] > 0
    over_heavy = pmf_mix({6: 0.2, 14: 0.8})
    assert ME.evaluate(over_heavy, q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110)]))[
        "preferred_side"] == "OVER"
    coin = pmf_mix({6: 0.5, 14: 0.5})
    ev = ME.evaluate(coin, q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110)]))
    assert ev["preferred_side"] == ME.NO_POSITIVE_EV and ev["tier"] == ME.TIER_PASS and not ev["actionable"]
    assert ev["best"]["ev"] < 0                                      # the best offer is still evaluated and graded


def test_actionable_requires_ev_edge_model_probability_and_two_book_reliability():
    pmf = pmf_mix({6: 0.30, 14: 0.70})                               # P(OVER 10.5) = 0.70
    one = ME.evaluate(pmf, q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110)]))
    assert one["preferred_side"] == "OVER" and one["best"]["ev"] >= 0.03 and one["best"]["edge"] >= 0.03
    assert one["reliable"] is False and one["actionable"] is False and one["tier"] == ME.TIER_PASS
    two = ME.evaluate(pmf, q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110),
                              ("fanduel", 10.5, "OVER", -115), ("fanduel", 10.5, "UNDER", -105)]))
    assert two["reliable"] and two["books_at_line"] == 2 and two["actionable"] and two["tier"] == ME.TIER_65
    p62 = pmf_mix({6: 0.38, 14: 0.62})
    t = ME.evaluate(p62, q([("draftkings", 10.5, "OVER", 110), ("draftkings", 10.5, "UNDER", -140),
                            ("fanduel", 10.5, "OVER", 105), ("fanduel", 10.5, "UNDER", -135)]))
    assert t["actionable"] and t["tier"] == ME.TIER_60
    p56 = pmf_mix({6: 0.44, 14: 0.56})
    t = ME.evaluate(p56, q([("draftkings", 10.5, "OVER", 130), ("draftkings", 10.5, "UNDER", -160),
                            ("fanduel", 10.5, "OVER", 125), ("fanduel", 10.5, "UNDER", -155)]))
    assert t["actionable"] and t["tier"] == ME.TIER_ACT
    p53 = pmf_mix({6: 0.47, 14: 0.53})                               # model probability < 0.54: never actionable
    t = ME.evaluate(p53, q([("draftkings", 10.5, "OVER", 150), ("draftkings", 10.5, "UNDER", -190),
                            ("fanduel", 10.5, "OVER", 150), ("fanduel", 10.5, "UNDER", -190)]))
    assert t["best"]["ev"] > 0.03 and t["best"]["edge"] > 0.03 and not t["actionable"]
    assert (ME.ACTION_MIN_EV, ME.ACTION_MIN_EDGE, ME.ACTION_MIN_MODEL_PROBABILITY) == (0.03, 0.03, 0.54)


def test_alternate_and_invalid_quotes_never_evaluated():
    rows = q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110)])
    rows.loc[0, "is_alternate"] = True
    rows.loc[1, "quote_status"] = "POST_CUTOFF_QUOTE"
    assert ME.evaluate(pmf_point(12), rows)["market"] is False


# ============================================================================ Board
def _pmfs(players=("P-1", "P-2"), stats=("PTS",)):
    out = []
    for p in players:
        for s in stats:
            out.append({"game_id": "G-1", "player_id": p, "team_id": "T-A", "stat": s, "mean": 11.0,
                        "pmf": pmf_mix({6: 0.3, 14: 0.7}), "game_label": "A @ B", "player_label": p, "tip": TIP,
                        "cutoff": CUT, "source_kind": "FORECAST_SNAPSHOT", "forecast_id": "F1", "model_version": "m1",
                        "prediction_as_of": CUT - pd.Timedelta(hours=5)})
    out.append({**out[0], "team_id": "T-B", "player_id": "P-9", "player_label": "P-9"})
    return out


def _obs(rows):
    return {"G-1": ({"game_id": "G-1", "received_at": (CUT - pd.Timedelta(minutes=20)).isoformat(), "rows": len(rows),
                     "file": "x.parquet"}, rows)}


def test_board_one_row_per_game_player_stat_full_board_market_only_and_model_coverage():
    rows = q([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110),
              ("fanduel", 10.5, "OVER", -115), ("fanduel", 10.5, "UNDER", -105), ("betmgm", 11.5, "OVER", -140),
              ("betmgm", 11.5, "UNDER", 110)])
    brows, evals, _, cnt = MX.board(_pmfs(), _obs(rows), {"T-A": "Alpha", "T-B": "Beta"}, {}, CUT - pd.Timedelta(minutes=5))
    assert len(brows) == 3 and cnt["duplicate_keys"] == 0 and len(evals) == 1          # one market row
    MX.assert_unique(brows)
    sh = MX.board_sheets(brows)
    assert [r[MX.BOARD_COLS.index("Canonical Player ID")] for r in sh["Full Board"]] == ["P-1"]
    assert sorted(r[MX.BOARD_COLS.index("Canonical Player ID")] for r in sh["Model Coverage"]) == ["P-2", "P-9"]
    r = dict(zip(MX.BOARD_COLS, sh["Full Board"][0]))
    assert r["Market Status"] == "VALID_MARKET" and r["Preferred Side"] == "OVER" and r["Is Actionable"] == "YES"
    assert r["Selection Tier"] == "65_PLUS" and sh["65 Percent Plus"] == sh["Full Board"]
    assert r["Best Book"] in ("draftkings", "fanduel", "betmgm") and r["Best Decimal Odds"] > 1
    assert r["Raw Book Count"] == 3 and r["Opponent"] == "Beta" and r["Legal Cutoff ET"] == "2026-11-19 18:00"
    assert r["Quote Age"] == 10 * 60                                 # capture - bookmaker last update (stable)
    assert r["Model Projection"] == 11.0                             # display only
    assert cnt["full_board_rows"] == 1 and cnt["over_rows"] == 1 and cnt["games_with_props"] == 1


def test_required_board_columns_present():
    need = ("Slate Date", "Canonical Game ID", "Game", "Tip Time ET", "Legal Cutoff ET", "Player", "Canonical Player ID",
            "Team", "Opponent", "Player Role", "Stat", "Model Projection", "Best Market Line", "Preferred Side",
            "Model Probability", "Push Probability", "Fair Market Probability", "Probability Edge", "Model Fair Odds",
            "Best Book", "Best American Odds", "Best Decimal Odds", "Preferred EV", "Is Actionable", "Selection Tier",
            "Consensus Line", "Consensus Over Odds", "Consensus Under Odds", "Consensus Fair Over %",
            "Consensus Fair Under %", "Raw Book Count", "Consensus Book Count", "Outlier Book Count", "Quote Timestamp",
            "Quote Age", "Forecast ID", "Model Version", "Market Status")
    assert set(need) <= set(MX.BOARD_COLS) and MX.BOARD_SHEETS == ("Full Board", "60 Percent Plus", "65 Percent Plus",
                                                                   "Model Coverage")


def test_duplicate_board_key_aborts():
    brows, _, _, cnt = MX.board(_pmfs() + [_pmfs()[0]], {}, {}, {}, CUT)
    assert cnt["duplicate_keys"] == 1
    with pytest.raises(MX.MarketExportIntegrityError):
        MX.assert_unique(brows)


# ============================================================================ frozen evaluations, Grades, Performance
def _record(ops, day, at, rows):
    QO.record(ops, day, "G-1", "E1", at, rows, "QUOTE_REFRESH")


def _setup(tmp_path, entry_am=(-110, -110), close=None, close_at=None, actual=None, minutes=30.0, part="PLAYED",
           day=PRIOR):
    ops = tmp_path / "ops"
    e_at = CUT - pd.Timedelta(minutes=90)
    _record(ops, day, e_at, q([("draftkings", 10.5, "OVER", entry_am[0]), ("draftkings", 10.5, "UNDER", entry_am[1]),
                               ("fanduel", 10.5, "OVER", -110), ("fanduel", 10.5, "UNDER", -110)]))
    if close is not None:
        _record(ops, day, close_at or CUT - pd.Timedelta(minutes=10), q(close))
    MX.freeze(ops, day, _pmfs(players=("P-1",)), {}, {}, {"G-1": CUT}, CUT)
    if actual is not None or part != "PLAYED":
        t = ops / "ticks" / "K1"
        t.mkdir(parents=True)
        pd.DataFrame([{"game_id": "G-1", "player_id": "P-1", "participation": part, "settled_at": "x",
                       "minutes": minutes, "act_PTS": actual}]).to_parquet(t / "actuals.parquet")
    return ops


def test_freeze_is_write_once_and_tamper_evident(tmp_path):
    ops = _setup(tmp_path)
    p = MX.eval_path(ops, PRIOR)
    ev, games = MX.load_evaluations(p)
    assert len(ev) == 1 and list(games) == ["G-1"]
    assert MX.freeze(ops, PRIOR, _pmfs(players=("P-1",)), {}, {}, {"G-1": CUT}, CUT)["written"] == 0
    lines = p.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["line"] = 9.5
    p.write_text(json.dumps(rec) + "\n" + "\n".join(lines[1:]) + "\n")
    with pytest.raises(MX.MarketExportIntegrityError):
        MX.load_evaluations(p)


@pytest.mark.parametrize("actual,minutes,part,result,units", [
    (12.0, 30.0, "PLAYED", "WIN", 100 / 110), (9.0, 30.0, "PLAYED", "LOSS", -1.0),
    (None, 0.0, "DNP", "VOID_DNP", None), (None, None, "GAME_NOT_PLAYED", "VOID_NO_GAME", None)])
def test_grading_against_exact_persisted_line(tmp_path, actual, minutes, part, result, units):
    ops = _setup(tmp_path, actual=actual, minutes=minutes, part=part)
    rows, _ = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))
    assert len(rows) == 1 and rows[0]["result"] == result and rows[0]["line"] == 10.5
    assert (rows[0]["units"] is None and units is None) or rows[0]["units"] == pytest.approx(units)


def test_push_is_not_a_loss_and_void_dnp_excluded_from_metrics():
    base = {"model_probability": 0.6, "edge": 0.05, "ev": 0.04, "clv_status": "EXACT_LINE", "probability_clv": 0.01,
            "log_odds_clv": 0.04}
    rows = [{**base, "result": "WIN", "units": 0.9}, {**base, "result": "LOSS", "units": -1.0},
            {**base, "result": "PUSH", "units": 0.0}, {**base, "result": "VOID_DNP", "units": None, "model_probability": 0.99},
            {**base, "result": "PENDING", "units": None}]
    m = MX.metrics(rows)
    assert (m["N"], m["Graded"], m["Wins"], m["Losses"], m["Pushes"], m["VOID_DNP"], m["Pending"]) == (5, 3, 1, 1, 1, 1, 1)
    assert m["Win %"] == 0.5 and m["Hit Rate"] == pytest.approx(1 / 3)
    assert m["Units Won"] == pytest.approx(-0.1) and m["ROI"] == pytest.approx(-0.05)
    assert m["Brier Score"] == pytest.approx(((0.6 - 1) ** 2 + 0.6 ** 2) / 2)      # VOID / PUSH / PENDING excluded
    assert m["Log Loss"] == pytest.approx(-(math.log(0.6) + math.log(0.4)) / 2)
    assert m["Mean Model Probability"] == 0.6 and m["Calibration Gap"] == pytest.approx(-0.1)


def test_clv_exact_line_movement_and_log_odds(tmp_path):
    close = [("draftkings", 10.5, "OVER", -130), ("draftkings", 10.5, "UNDER", 110)]
    ops = _setup(tmp_path, close=close)
    rows, _ = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))
    r = rows[0]
    if r["book"] == "draftkings":
        assert r["clv_status"] == "EXACT_LINE"
    else:                                                            # fanduel entry; close quotes only draftkings
        assert r["clv_status"] == "SAME_LINE_NEAREST_CLOSE" and r["close_book"] == "draftkings"
    assert r["entry_received_at"] == (CUT - pd.Timedelta(minutes=90)).isoformat()
    assert r["close_ts"] == (CUT - pd.Timedelta(minutes=10)).isoformat()
    ef, cf = r["entry_fair_probability"], r["close_fair_probability"]
    assert r["probability_clv"] == pytest.approx(cf - ef) and r["probability_clv"] > 0
    assert r["log_odds_clv"] == pytest.approx(math.log(cf / (1 - cf)) - math.log(ef / (1 - ef)))


def test_clv_line_moved_stores_directional_movement(tmp_path):
    close = [("draftkings", 12.5, "OVER", -110), ("draftkings", 12.5, "UNDER", -110),
             ("fanduel", 12.5, "OVER", -110), ("fanduel", 12.5, "UNDER", -110)]
    ops = _setup(tmp_path, close=close)
    r = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))[0][0]
    assert r["side"] == "OVER" and r["clv_status"] == "LINE_MOVED" and r["close_line"] == 12.5
    assert r["directional_line_movement"] == 2.0 and r["probability_clv"] is None     # favorable to an OVER


def test_close_more_than_60_minutes_stale_is_no_close_quote(tmp_path):
    ops = tmp_path / "ops"
    _record(ops, PRIOR, CUT - pd.Timedelta(minutes=90), q([("draftkings", 10.5, "OVER", -110),
                                                          ("draftkings", 10.5, "UNDER", -110)]))
    MX.freeze(ops, PRIOR, _pmfs(players=("P-1",)), {}, {}, {"G-1": CUT}, CUT)
    r = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))[0][0]
    assert r["clv_status"] == "NO_CLOSE_QUOTE" and r["probability_clv"] is None


def test_post_cutoff_observation_never_closes_or_freezes(tmp_path):
    close = [("draftkings", 14.5, "OVER", -110), ("draftkings", 14.5, "UNDER", -110)]
    ops = _setup(tmp_path, close=close, close_at=CUT + pd.Timedelta(minutes=1))
    e = next(iter(MX.load_evaluations(MX.eval_path(ops, PRIOR))[0].values()))
    assert e["line"] == 10.5
    r = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))[0][0]
    assert r["clv_status"] == "NO_CLOSE_QUOTE" and r["close_line"] is None        # only the stale T-90 quote is pre-cutoff


def test_decision_is_the_first_observation_after_the_pmf_and_close_is_the_last_before_cutoff(tmp_path):
    close = [("draftkings", 10.5, "OVER", -130), ("draftkings", 10.5, "UNDER", 110),
             ("fanduel", 10.5, "OVER", -130), ("fanduel", 10.5, "UNDER", 110)]
    ops = _setup(tmp_path, close=close)
    g = MX.load_evaluations(MX.eval_path(ops, PRIOR))[1]["G-1"]
    assert g["decision_received_at"] == (CUT - pd.Timedelta(minutes=90)).isoformat()
    r = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))[0][0]
    assert r["clv_status"] == "EXACT_LINE" and r["decimal_odds"] == pytest.approx(dec(-110))     # graded entry price


def test_no_devig_pair_status(tmp_path):
    ops = tmp_path / "ops"
    _record(ops, PRIOR, CUT - pd.Timedelta(minutes=30), q([("draftkings", 10.5, "OVER", -110),
                                                          ("fanduel", 10.5, "UNDER", -110)]))
    MX.freeze(ops, PRIOR, _pmfs(players=("P-1",)), {}, {}, {"G-1": CUT}, CUT)
    r = MX.graded_rows(ops, PRIOR, CUT + pd.Timedelta(hours=6))[0][0]
    assert r["fair_probability"] is None and r["clv_status"] == "NO_DEVIG_PAIR"


def test_grades_d_grades_prior_day_and_performance_agrees_exactly(tmp_path):
    ops = _setup(tmp_path, actual=12.0)
    out = tmp_path / "x" / DAY
    now = CUT + pd.Timedelta(hours=12)
    grows, info = MX.graded_rows(ops, MX.prior_day(DAY), now)
    sheets = MX.grades_tables(grows, PRIOR, info)
    p = EX.write_xlsx(out / "CBB_Grades.xlsx", sheets)
    wb = EX.read_xlsx(p)
    assert tuple(wb) == MX.GRADE_SHEETS and len(wb["Graded Predictions"]["rows"]) == 2
    rep = PF.export(DAY, ops, out, now)
    assert rep["status"] == "WRITTEN" and rep["start_date"] == PRIOR
    assert tuple(EX.read_xlsx(out / PF.performance_name(DAY))) == PF.SHEETS
    pm = PF._metrics(PF.read_dataset(ops / PF.DATASET))
    gm = MX.metrics(grows)
    assert all(pm[k] == gm[k] for k in MX.GRADE_METRICS if k in pm)
    chk = PF.verify(out, DAY, ops, recompute=True, now=now)
    assert chk["RECOMPUTE_AGREES"] and chk["PERFORMANCE_EVIDENCE"] == "PASS"


def test_performance_without_market_evidence_is_written_and_empty(tmp_path):
    rep = PF.export(DAY, tmp_path / "ops", tmp_path / "x", CUT)
    assert rep["status"] == "WRITTEN" and rep["PERFORMANCE_STATUS"] == "NO_MARKET_EVIDENCE_YET" and rep["rows"] == 0
