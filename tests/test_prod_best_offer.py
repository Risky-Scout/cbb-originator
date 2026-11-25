"""Best offer of one game x player x stat (prod.market_eval) and the Distributions snapshot / no-refit guarantees.

Contract: every book x line x side priced at its exact line; best offer = highest EV, then probability edge, then model
probability, then payout (decimal odds), then bookmaker key (then line, OVER before UNDER). Exact comparisons.

Legacy test classification (11e736c tests/test_prod_best_offer.py -> this file):
  test_1_one_row_per_game_player_prop ............ VALID_INVARIANT_TO_PRESERVE  -> test_1_one_row_per_game_player_stat
  test_2/3/4_highest_ev_across_books/lines/sides . VALID_INVARIANT_TO_PRESERVE  -> test_2 / test_3 / test_4
  test_5_equal_ev_higher_model_probability ....... SUPERSEDED_CONTRACT (edge now precedes model probability)
                                                   -> test_5_equal_ev_higher_edge_beats_higher_model_probability
  test_6_same_side_bettor_favorable_line ......... SUPERSEDED_CONTRACT (no line rule in the contract order)
  test_6b_line_rule_skipped_for_opposite_sides ... SUPERSEDED_CONTRACT -> test_6_no_line_preference_payout_decides
  test_7_same_line_better_decimal_price .......... VALID_INVARIANT_TO_PRESERVE  -> test_7 (same four cases)
  test_7b_decimal_odds_conversion ................ VALID_INVARIANT_TO_PRESERVE  -> test_7b (+ decimal -> american)
  test_8_equal_remaining_values_higher_edge ...... VALID_INVARIANT_TO_PRESERVE  -> test_8
  test_9_complete_tie_over_before_under .......... VALID_INVARIANT_TO_PRESERVE  -> test_9
  test_10_final_tie_book_alphabetical_normalized . VALID_INVARIANT_TO_PRESERVE  -> test_10
  test_11_no_input_order_dependence .............. VALID_INVARIANT_TO_PRESERVE  -> test_11 (every permutation)
  test_12_13_threshold_sheets_pure_full_board_filters
                                                   VALID_INVARIANT (pure filters of Full Board) with SUPERSEDED
                                                   criterion (tier, not raw model %) -> test_12_13
  test_duplicate_key_aborts ...................... VALID_INVARIANT_TO_PRESERVE  -> test_duplicate_key_aborts
  test_14_provisional_updates_before_cutoff ...... VALID_INVARIANT_TO_PRESERVE  -> test_14 (Board follows the latest
                                                   pre-cutoff observation; nothing frozen / graded before the cutoff)
  test_15_frozen_cannot_change_after_cutoff ...... VALID_INVARIANT_TO_PRESERVE  -> test_15 (+ tamper hard-fails)
  test_16_to_19_grades_use_frozen_receipt_only ... VALID_INVARIANT_TO_PRESERVE  -> test_16_to_19 (frozen evaluation
                                                   only; one graded row per key; frozen odds give the units)
  test_20_21_22_distributions_latest_valid_pre_cutoff
                                                   VALID_INVARIANT_TO_PRESERVE  -> unchanged
  test_23_24_25_quote_refresh_no_refit_no_network_no_artifacts
                                                   VALID_INVARIANT_TO_PRESERVE  -> unchanged except the removed
                                                   official_rows key
  test_ev_comparison_is_exact_no_rounding_or_tolerance
                                                   VALID_INVARIANT_TO_PRESERVE  -> unchanged intent
"""
import hashlib
import itertools
import json
import socket
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import exports as EX, market_eval as ME, market_exports as MX, pmf_evidence as PE, quote_obs as QO
from tests.test_prod_exports import _pmf as _poisson, build_tree

DAY = "2026-11-04"
TIP = pd.Timestamp("2026-11-05T00:00Z")
CUT = TIP - pd.Timedelta(hours=1)


def _pmf(w):
    p = np.zeros(40)
    for k, v in w.items():
        p[k] = v
    return p / p.sum()


PMF = _pmf({8: 0.25, 10: 0.15, 12: 0.35, 16: 0.25})        # P(>10.5)=.60  P(>11.5)=.60  P(>15.5)=.25  P(10)=.15


def _dec(am):
    return 1 + (am / 100 if am > 0 else 100 / abs(am))


def _rows(offers, at=pd.Timestamp("2026-11-04T22:00Z"), pid="P", stat="PTS", gid="G"):
    return pd.DataFrame([{"sportsbook": b, "line": ln, "side": s, "american_odds": am, "decimal_odds": _dec(am),
                          "market_last_update": at, "collector_received_at": at + pd.Timedelta(minutes=1),
                          "canonical_game_id": gid, "canonical_player_id": pid, "quote_status": "VALID",
                          "is_alternate": False, "stat": stat, "provider_event_id": "E", "market_key": "player_points",
                          "provider_player_name": pid, "bookmaker_last_update": None, "quote_available_at": None,
                          "suppression_reason": None} for b, ln, s, am in offers])


def _best(offers, pmf=PMF):
    b = ME.evaluate(pmf, _rows(offers))["best"]
    return (b["book"], b["line"], b["side"])


def _c(book, ev, edge=0.05, model_p=0.6, decimal=1.9, line=10.5, side="OVER"):
    return {"book": book, "ev": ev, "edge": edge, "model_p": model_p, "decimal": decimal, "line": line, "side": side}


# ---------------------------------------------------------------- 1-4 maximum EV is the primary decision
def test_1_one_row_per_game_player_stat():
    pm = [{"game_id": "G", "player_id": "P", "team_id": "T", "stat": s, "mean": 11.0, "pmf": PMF, "game_label": "G",
           "player_label": "P", "tip": TIP, "cutoff": CUT, "forecast_id": "F", "model_version": "m"} for s in ("PTS", "REB")]
    q = _rows([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110), ("fanduel", 10.5, "OVER", -105),
               ("fanduel", 10.5, "UNDER", -115), ("betmgm", 11.5, "OVER", 105), ("betmgm", 11.5, "UNDER", -135)])
    rows, evals, _, cnt = MX.board(pm, {"G": ({"received_at": "2026-11-04T22:01:00+00:00", "rows": 6, "file": "f"}, q)},
                                   {}, {}, pd.Timestamp("2026-11-04T22:30Z"))
    assert cnt["unique_keys"] == cnt["rows"] == 2 and len(evals) == 1             # six offers -> one PTS row
    sh = MX.board_sheets(rows)
    assert len(sh["Full Board"]) == 1 and sh["Model Coverage"][0][MX.BOARD_COLS.index("Stat")] == "REB"


def test_2_highest_ev_across_books():
    assert _best([("booka", 10.5, "OVER", -110), ("bookb", 10.5, "OVER", 105), ("bookc", 10.5, "OVER", -120)])[0] == "bookb"


def test_3_highest_ev_across_lines():
    # same book, OVER 10.5 -110 (p .60) vs OVER 11.5 +120 (p .60): the higher payout at the same probability wins
    assert _best([("booka", 10.5, "OVER", -110), ("booka", 11.5, "OVER", 120)])[1] == 11.5
    # push-aware: OVER 10 at -110 (win .60, push .15 refunded, lose .25: EV .295) beats OVER 10.5 at -105 (EV .171)
    assert _best([("booka", 10.0, "OVER", -110), ("booka", 10.5, "OVER", -105)])[1] == 10.0
    assert ME.evaluate(PMF, _rows([("booka", 10.0, "OVER", -110)]))["best"]["ev"] == pytest.approx(0.6 * 100 / 110 - 0.25)


def test_4_highest_ev_across_opposite_sides():
    # UNDER 15.5 (p .75) at -200 has EV .125; OVER 10.5 (p .60) at -110 has EV .145
    assert _best([("booka", 15.5, "UNDER", -200), ("bookb", 10.5, "OVER", -110)])[2] == "OVER"
    assert _best([("booka", 15.5, "UNDER", -150), ("bookb", 10.5, "OVER", -110)])[2] == "UNDER"   # EV .25


# ---------------------------------------------------------------- 5-11 tie-breaks (contract order)
def test_5_equal_ev_higher_edge_beats_higher_model_probability():
    a, b = _c("a", 0.08, edge=0.04, model_p=0.70), _c("b", 0.08, edge=0.06, model_p=0.55)
    assert ME.best_offer([a, b])["book"] == "b"
    a, b = _c("a", 0.08, edge=0.05, model_p=0.70), _c("b", 0.08, edge=0.05, model_p=0.55)   # equal EV and edge
    assert ME.best_offer([a, b])["book"] == "a"
    a, b = _c("a", 0.08, edge=None, model_p=0.70), _c("b", 0.08, edge=0.0, model_p=0.55)    # unpaired edge ranks last
    assert ME.best_offer([a, b])["book"] == "b"


def test_6_no_line_preference_payout_decides():
    a = _c("a", 0.08, line=17.5, decimal=1.90)
    b = _c("b", 0.08, line=18.5, decimal=1.95)
    assert ME.best_offer([a, b])["line"] == 18.5                                    # same side, no line rule
    c = _c("c", 0.08, line=17.5, side="OVER", decimal=1.90)
    d = _c("d", 0.08, line=18.5, side="UNDER", decimal=2.05)
    assert ME.best_offer([c, d])["side"] == "UNDER"                                 # opposite sides: payout decides


@pytest.mark.parametrize("a,b,win", [(-115, -105, "b"), (105, 110, "b"), (-105, 100, "b"), (-105, -115, "a")])
def test_7_same_line_better_decimal_price(a, b, win):
    assert ME.best_offer([_c("a", 0.08, decimal=_dec(a)), _c("b", 0.08, decimal=_dec(b))])["book"] == win
    assert _best([("a", 10.5, "OVER", a), ("b", 10.5, "OVER", b)])[0] == win        # through pricing (EV follows price)


def test_7b_decimal_odds_conversion():
    dec = [EX.decimal_odds(o) for o in (120, 105, -105, -110, -125)]
    assert dec == sorted(dec, reverse=True) and EX.decimal_odds(120) == pytest.approx(2.2)
    assert EX.decimal_odds(-125) == pytest.approx(1.8) and EX.decimal_odds(0) is None and EX.decimal_odds(50) is None
    for am in (120, 105, 100, -105, -110, -125, -250):
        assert ME.decimal_to_american(_dec(am)) == am


def test_8_equal_remaining_values_higher_edge():
    assert ME.best_offer([_c("a", 0.08, edge=0.02), _c("b", 0.08, edge=0.05)])["book"] == "b"


def test_9_complete_tie_over_before_under():
    o, u = _c("a", 0.08, side="OVER"), _c("a", 0.08, side="UNDER")
    assert ME.best_offer([u, o])["side"] == "OVER"


def test_10_final_tie_book_alphabetical():
    assert ME.best_offer([_c("zeta", 0.08), _c("alpha", 0.08)])["book"] == "alpha"


def test_11_no_input_order_dependence():
    offers = [("zeta", 10.5, "OVER", -110), ("alpha", 10.5, "OVER", -110), ("mid", 15.5, "UNDER", -300),
              ("x", 11.5, "OVER", -110), ("alpha", 10.5, "UNDER", -110)]
    picks = {_best(list(p)) for p in itertools.permutations(offers)}
    assert picks == {("alpha", 10.5, "OVER")}          # 10.5 and 11.5 OVER tie on EV/edge/p/payout: book key decides


def test_ev_comparison_is_exact_no_rounding_or_tolerance():
    """A larger EV wins however small the difference (no EV rounding / tolerance), even against a higher MODEL%."""
    lo, hi = 0.05, 0.05 + 2e-15
    assert hi > lo and round(hi, 12) == round(lo, 12)          # a 1e-12 rounding would have called this a tie
    assert ME.best_offer([_c("a", lo, model_p=0.70), _c("b", hi, model_p=0.55)])["book"] == "b"


# ---------------------------------------------------------------- 12-13 threshold sheets / keys
def test_12_13_threshold_sheets_pure_full_board_filters():
    col = {c: i for i, c in enumerate(MX.BOARD_COLS)}

    def row(pid, status, tier):
        r = [None] * len(MX.BOARD_COLS)
        r[col["Canonical Player ID"]], r[col["Market Status"]], r[col["Reporting Tier"]] = pid, status, tier
        return r
    rows = [row("a", "VALID_MARKET", ME.TIER_65), row("b", "VALID_MARKET", ME.TIER_60),
            row("c", "VALID_MARKET", ME.TIER_ACT), row("d", "VALID_MARKET", ME.TIER_PASS),
            row("e", "NO_QUOTE_OBSERVATION", None), row("f", "VALID_MARKET", ME.TIER_65)]
    sh = MX.board_sheets(rows)
    ids = lambda s: [r[col["Canonical Player ID"]] for r in sh[s]]
    assert ids("Full Board") == ["a", "b", "c", "d", "f"] and ids("Model Coverage") == ["e"]
    assert ids("60 Percent Plus") == ["a", "b", "f"] and ids("65 Percent Plus") == ["a", "f"]
    for s in ("60 Percent Plus", "65 Percent Plus"):                          # pure, order-preserving filters
        assert all(any(r is f for f in sh["Full Board"]) for r in sh[s])


def test_duplicate_key_aborts():
    pm = [{"game_id": "G", "player_id": "P", "team_id": "T", "stat": "PTS", "mean": 11.0, "pmf": PMF, "game_label": "G",
           "player_label": "P", "tip": TIP, "cutoff": CUT, "forecast_id": "F", "model_version": "m"}] * 2
    rows, _, _, cnt = MX.board(pm, {}, {}, {}, None)
    assert cnt["duplicate_keys"] == 1
    with pytest.raises(MX.MarketExportIntegrityError):
        MX.assert_unique(rows)


# ---------------------------------------------------------------- 14-19 Board before the cutoff, frozen after, Grades
def _pm(aso=CUT - pd.Timedelta(hours=3)):
    return [{"game_id": "G", "player_id": "P", "team_id": "T", "stat": "PTS", "mean": 11.0, "pmf": PMF, "game_label": "G",
             "player_label": "P", "tip": TIP, "cutoff": CUT, "forecast_id": "F", "model_version": "m",
             "prediction_as_of": aso}]


def _record(ops, at, offers):
    QO.record(ops, DAY, "G", "E", at, _rows(offers, at=at - pd.Timedelta(minutes=1)), "QUOTE_REFRESH")


def _board(ops, at):
    latest = QO.latest(ops, DAY, {"G": CUT}, at)
    obs = {g: v for g, v in latest.items()}
    rows, _, _, _ = MX.board(_pm(), obs, {}, {}, at)
    col = {c: i for i, c in enumerate(MX.BOARD_COLS)}
    r = MX.board_sheets(rows)["Full Board"]
    return (r[0][col["Best Book"]], r[0][col["Best Market Line"]]) if r else None


def test_14_board_follows_the_latest_pre_cutoff_observation_nothing_frozen_before_cutoff(tmp_path):
    ops = tmp_path / "ops"
    _record(ops, CUT - pd.Timedelta(hours=2), [("booka", 10.5, "OVER", -110), ("booka", 10.5, "UNDER", -110)])
    assert _board(ops, CUT - pd.Timedelta(minutes=90)) == ("booka", 10.5)
    _record(ops, CUT - pd.Timedelta(minutes=60), [("bookb", 10.5, "OVER", -102), ("bookb", 10.5, "UNDER", -118)])
    assert _board(ops, CUT - pd.Timedelta(minutes=30)) == ("bookb", 10.5)           # provisional: updates
    f = MX.freeze(ops, DAY, _pm(), {}, {}, {"G": CUT}, CUT - pd.Timedelta(minutes=1))
    assert f["written"] == 0 and f["games_frozen_now"] == 0                          # not before the cutoff
    assert MX.graded_rows(ops, DAY, CUT - pd.Timedelta(minutes=1))[0] == []


def test_15_frozen_evaluation_cannot_change_after_cutoff(tmp_path):
    ops = tmp_path / "ops"
    _record(ops, CUT - pd.Timedelta(hours=2), [("booka", 10.5, "OVER", -110), ("booka", 10.5, "UNDER", -110)])
    MX.freeze(ops, DAY, _pm(), {}, {}, {"G": CUT}, CUT)
    path = MX.eval_path(ops, DAY)
    before = path.read_text()
    _record(ops, CUT + pd.Timedelta(minutes=20), [("bookz", 10.5, "OVER", 150), ("bookz", 10.5, "UNDER", -190)])
    better = _pm(aso=CUT - pd.Timedelta(minutes=5))
    better[0]["pmf"] = _pmf({16: 1.0})                                              # a newer, very different PMF
    again = MX.freeze(ops, DAY, better, {}, {}, {"G": CUT}, CUT + pd.Timedelta(hours=3))
    assert again["written"] == 0 and path.read_text() == before                     # write-once
    ev, _ = MX.load_evaluations(path)
    (e,) = ev.values()
    assert (e["book"], e["american_odds"]) == ("booka", -110)
    d = [json.loads(x) for x in before.splitlines()]
    d[0]["american_odds"] = 999                                                     # tampered evaluation hard-fails
    path.write_text("".join(json.dumps(x) + "\n" for x in d))
    with pytest.raises(MX.MarketExportIntegrityError):
        MX.load_evaluations(path)


def test_16_to_19_grades_use_the_frozen_evaluation_only(tmp_path):
    ops = tmp_path / "ops"
    _record(ops, CUT - pd.Timedelta(hours=2), [("booka", 10.5, "OVER", -110), ("bookb", 10.5, "OVER", 120),
                                                ("bookb", 10.5, "UNDER", -150), ("bookc", 15.5, "UNDER", -400)])
    MX.freeze(ops, DAY, _pm(), {}, {}, {"G": CUT}, CUT)
    _record(ops, CUT + pd.Timedelta(minutes=10), [("bookz", 10.5, "OVER", 300)])    # post-cutoff: never graded
    t = ops / "ticks" / "KACT"
    t.mkdir(parents=True)
    pd.DataFrame([{"game_id": "G", "player_id": "P", "participation": "PLAYED", "settled_at": "x", "minutes": 30.0,
                   "act_PTS": 12.0}]).to_parquet(t / "actuals.parquet")
    rows, _ = MX.graded_rows(ops, DAY, TIP + pd.Timedelta(hours=4))
    assert len(rows) == 1                                                           # one graded row per key
    (r,) = rows
    assert (r["book"], r["line"], r["side"], r["american_odds"], r["result"]) == ("bookb", 10.5, "OVER", 120, "WIN")
    assert r["units"] == pytest.approx(1.2)                                         # frozen odds give the units
    assert MX.metrics(rows)["N"] == 1


# ---------------------------------------------------------------- 20-22 Distributions snapshot selection (unchanged)
def _forecast(ops, sid, aso, gid, cutoff, lam):
    d = ops / "forecasts" / "2026-11-04" / sid
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"prediction_receipt_id": f"{sid}-x", "game_id": gid, "player_id": "P", "team_id": "T", "stat": "PTS",
                   "prediction_as_of": aso, "model_version": "TEST", "outcome": i, "probability": float(p),
                   "mean": lam, "run_id": sid, "method": "TEST", "game_label": "Away @ Home", "player_label": "P"}
                  for i, p in enumerate(_poisson(lam, 40))]).to_parquet(d / PE.FILE, index=False)
    with (ops / "forecasts" / "index.jsonl").open("a") as f:
        f.write(json.dumps({"snapshot_id": sid, "slate_date": "2026-11-04", "status": "FORECAST", "prediction_as_of": aso,
                            "games": [gid], "cutoffs": {gid: cutoff}}) + "\n")


def _daily(tree, now="2026-11-05T06:00:00+00:00"):
    return EX.daily("2026-11-04", ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
                    history_db=None, state_root=None, labels=("TEST",), now=now)


def test_20_21_22_distributions_latest_valid_pre_cutoff(tmp_path):
    tree = build_tree(tmp_path, [], [])
    ops = tree["ops_root"]
    cut = "2026-11-04T23:00:00+00:00"
    _forecast(ops, "F1", "2026-11-04T12:00:00+00:00", "G7", cut, 5.0)          # early
    _forecast(ops, "F2", "2026-11-04T23:00:00+00:00", "G7", cut, 9.0)          # later, exactly at cutoff
    _forecast(ops, "F3", "2026-11-04T23:30:00+00:00", "G7", cut, 15.0)         # post-cutoff: ignored
    rep = _daily(tree)
    assert rep["distributions"]["status"] == "WRITTEN" and rep["distributions"]["PMF_RECONCILIATION"] == "PASS"
    rows = EX.read_xlsx(Path(rep["distributions"]["path"]))["Distributions"]["rows"][1:]
    c = {k: i for i, k in enumerate(EX.DIST_SUMMARY_COLS)}
    assert {r[c["MEAN"]] for r in rows} == {9.0} and {r[c["FORECAST_ID"]] for r in rows} == {"F2"}   # F2 mean
    pm = EX.resolve_pmf_refs(pd.read_parquet(rep["distributions"]["pmf_refs_path"]))
    v = np.asarray(pm.iloc[0].probabilities)
    assert len(pm) == 1 and abs(v.sum() - 1) <= EX.PMF_TOL and (v >= 0).all()


# ---------------------------------------------------------------- 23-25 no refit / no network / no artifact change
def test_23_24_25_quote_refresh_no_refit_no_network_no_artifacts(tmp_path, monkeypatch):
    from cbb_platform import config as C
    from cbb_platform.prod import config as PC, lifecycle as L, run as RN
    from cbb_platform.walkforward import daily as WD, fit as WF

    def boom(*a, **k):
        raise AssertionError("refit / scoring / network attempted")
    monkeypatch.setattr(WD, "morning", boom)
    for name in dir(WF):
        if not name.startswith("_") and callable(getattr(WF, name)) and getattr(getattr(WF, name), "__module__", "") == WF.__name__:
            monkeypatch.setattr(WF, name, boom)
    monkeypatch.setattr(RN, "daily", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    tree = build_tree(tmp_path / "t", [], [])
    _forecast(tree["ops_root"], "F1", "2026-11-04T20:00:00+00:00", "G1", CUT.isoformat(), 11.0)
    QO.record(tree["ops_root"], DAY, "G1", "E", CUT - pd.Timedelta(minutes=30),
              _rows([("booka", 10.5, "OVER", -110), ("booka", 10.5, "UNDER", -110)], gid="G1",
                    at=CUT - pd.Timedelta(minutes=31)), "QUOTE_REFRESH")

    def frozen():
        return {k: hashlib.sha256((C.PROJECT_ROOT / rel).read_bytes()).hexdigest() for k, (rel, _) in PC.ARTIFACTS.items()}
    snap = frozen()
    assert snap == {k: h for k, (_, h) in PC.ARTIFACTS.items()}
    rep = _daily(tree, now=(CUT - pd.Timedelta(minutes=2)).isoformat())
    assert rep["network_calls"] == 0 and rep["refits_triggered"] == 0
    assert rep["boards"]["status"] == "WRITTEN" and rep["boards"]["rows_with_valid_market"] == 1
    pub = rep["public_contract"]                                  # 2026-11-25 CBB_PUBLIC_DATA_CONTRACT_V1 from the same rows
    assert pub["status"] == "WRITTEN" and pub["WIZARDOFODDS_ROW_QA"] == "PASS" and pub["prop_rows"] == rep["boards"]["rows_with_valid_market"]
    assert json.loads((Path(pub["dir"]) / "qa_receipt.json").read_text())["WIZARDOFODDS_ROW_QA"] == "PASS"
    pj = json.loads((Path(pub["dir"]) / "cbb_public_v1.json").read_text())
    assert pj["contract"] == "CBB_PUBLIC_DATA_CONTRACT_V1" and pj["actionability_validated"] is False
    assert (Path(pub["dir"]) / "index.html").read_text().startswith("<!doctype html>")
    life = L.Life(ops_root=tree["ops_root"], prod_root=tmp_path / "t", clock=lambda: CUT - pd.Timedelta(minutes=2),
                  history_db=Path("/nonexistent"))
    out = L.exports_now(life)
    assert out["lifecycle"] == "EXPORTS" and out["exports"]["distributions"]["status"] in ("WRITTEN", "MISSING_EVIDENCE")
    assert frozen() == snap
