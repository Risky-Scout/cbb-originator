"""Candidate-offer persistence (player + game frozen evaluations) and deterministic selection replay
(prod.selection_replay): exact reconstruction of the production best offer, the four rules, stale / excluded offers
never replayed as candidates, corrected-close CLV usage, role / stat / side aggregation, disagreement buckets and
actionability-policy application. Synthetic evidence only."""
import json
import math

import pandas as pd
import pytest

from cbb_platform.prod import market_eval as ME, market_exports as MX, selection_replay as SR
from tests.test_market_contract import CUT, TIP, _obs, _pmfs, pmf_mix, q

BOOKS = [("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110),
         ("fanduel", 10.5, "OVER", -115), ("fanduel", 10.5, "UNDER", -105),
         ("betmgm", 11.5, "OVER", 120), ("betmgm", 11.5, "UNDER", -150),
         ("caesars", 12.5, "OVER", 150)]                         # unpaired: no de-vig, no edge


def _record():
    rows, evals, _, _ = MX.board(_pmfs(players=("P-1",)), _obs(q(BOOKS)), {}, {}, CUT - pd.Timedelta(minutes=5))
    assert len(evals) == 1
    return {**evals[0], "decimal_odds": evals[0]["decimal_odds"]}


def test_candidate_offer_set_persisted_complete_and_compact():
    e = _record()
    assert e["candidate_offer_schema"] == ME.OFFER_SET_SCHEMA
    rows = e["candidate_offer_set"]
    assert all(len(r) == len(ME.OFFER_COLS) for r in rows)
    el = [dict(zip(ME.OFFER_COLS, r)) for r in rows if r[-1] == ME.ELIGIBLE]
    assert len(el) == len(BOOKS) == e["candidate_offers"]
    for c in el:                                    # every required per-offer field is present
        for k in ("book", "line", "side", "american", "decimal", "model_p", "p_push", "fair_p", "edge", "ev_gross",
                  "ev_net", "market_last_update", "collector_received_at", "quote_id", "status"):
            assert k in c
        assert c["ev_gross"] == c["ev_net"]
    assert {c["book"] for c in el if c["fair_p"] is None} == {"caesars"}
    # game / player / stat / evaluation time once on the parent record, never per offer
    assert {"game_id", "player_id", "stat", "cutoff_utc", "observation_received_at"} <= set(e)
    assert not {"game_id", "player_id", "stat"} & set(ME.OFFER_COLS)
    json.dumps(e)                                   # JSON-serialisable as frozen


def test_excluded_quotes_carry_their_status_and_are_never_replayed():
    rows = q(BOOKS)
    rows.loc[0, "quote_status"] = "POST_CUTOFF_QUOTE"
    rows.loc[0, "suppression_reason"] = "STALE_QUOTE"
    ev = ME.evaluate(pmf_mix({6: 0.3, 14: 0.7}), rows)
    st = [r for r in ev["offer_set"] if r[-1] != ME.ELIGIBLE]
    assert len(st) == 1 and st[0][0] == "draftkings" and st[0][2] == "OVER" and st[0][8] is None
    cands = SR.offers({"candidate_offer_set": ev["offer_set"]})
    assert ("draftkings", 10.5, "OVER") not in {(c["book"], c["line"], c["side"]) for c in cands}


def test_exact_selection_reconstruction_matches_the_frozen_best_offer():
    e = _record()
    cands = SR.offers(e)
    assert SR.matches_stored(e, cands)
    b, active = SR.select("MAX_EV", cands)
    assert (b["book"], b["line"], b["side"]) == (e["book"], e["line"], e["side"]) and active
    assert not SR.matches_stored({**e, "ev": e["ev"] + 1e-12}, cands)     # exact, no tolerance


def test_replay_is_deterministic_and_input_order_free():
    e = _record()
    c = SR.offers(e)
    a = SR.replay(e, c, 12.0)
    b = SR.replay(e, list(reversed(c)), 12.0)
    assert a == b and SR.replay_hash(a) == SR.replay_hash(b) and len(a) == len(SR.RULES) == 4


def _c(book, line, side, model_p, fair_p, ev, dec=1.9):
    return {"book": book, "line": line, "side": side, "model_p": model_p, "fair_p": fair_p,
            "edge": None if fair_p is None else model_p - fair_p, "ev": ev, "decimal": dec, "p_push": 0.0}


def test_the_four_rules():
    cands = [_c("a", 10.5, "OVER", 0.58, 0.50, 0.10, 2.0),          # max EV, edge .08
             _c("b", 10.5, "UNDER", 0.42, 0.30, -0.05, 2.5),        # max edge .12 but EV < 0
             _c("c", 8.5, "OVER", 0.75, 0.73, 0.02, 1.36),          # max model p with EV > 0, edge .02
             _c("d", 12.5, "OVER", 0.40, None, 0.20, 3.0)]          # unpaired: highest EV, no edge
    assert SR.select("MAX_EV", cands)[0]["book"] == "d"
    assert SR.select("MAX_EDGE", cands)[0]["book"] == "b"
    assert SR.select("MAX_PROB_POS_EV", cands)[0]["book"] == "c"
    assert SR.select("CONSERVATIVE_MAX_EV", cands)[0]["book"] == "a"     # needs edge >= 0.03 and EV > 0
    neg = [_c("a", 10.5, "OVER", 0.45, 0.50, -0.05)]
    assert SR.select("MAX_EV", neg) == (neg[0], False)                   # NO_POSITIVE_EV: picked, inactive
    assert SR.select("MAX_PROB_POS_EV", neg) == (None, False)
    assert SR.select("CONSERVATIVE_MAX_EV", []) == (None, False)
    with pytest.raises(ValueError):
        SR.select("OTHER", cands)


def test_settlement_and_units():
    assert SR.settle("OVER", 10.5, 11) == "WIN" and SR.settle("UNDER", 10.5, 11) == "LOSS"
    assert SR.settle("OVER", 10.0, 10) == "PUSH" and SR.settle("OVER", 10.5, None) is None
    assert SR.units("WIN", 1.9) == pytest.approx(0.9) and SR.units("PUSH", 1.9) == 0.0 and SR.units(None, 2) is None


@pytest.mark.parametrize("d,b", [(-0.2, "<=-10pp"), (-0.10, "<=-10pp"), (-0.07, "-10..-5pp"), (-0.05, "-10..-5pp"),
                                 (-0.01, "-5..0pp"), (0.0, "-5..0pp"), (0.03, "0..+5pp"), (0.05, "0..+5pp"),
                                 (0.07, "+5..+10pp"), (0.10, "+5..+10pp"), (0.11, ">+10pp"), (None, None)])
def test_disagreement_buckets(d, b):
    assert SR.disagreement_bucket(d) == b


def test_metrics_use_corrected_close_and_exclude_pushes():
    rows = [{"result": "WIN", "model_p": 0.6, "fair_p": 0.5, "edge": 0.1, "ev": 0.1, "units": 0.9,
             "probability_clv": 0.02},
            {"result": "LOSS", "model_p": 0.6, "fair_p": 0.5, "edge": 0.1, "ev": 0.1, "units": -1.0,
             "probability_clv": -0.01},
            {"result": "PUSH", "model_p": 0.9, "fair_p": 0.5, "edge": 0.4, "ev": 0.1, "units": 0.0,
             "probability_clv": None}]
    m = SR.metrics(rows)
    assert m["N"] == 2 and m["hit_rate"] == 0.5 and m["calibration_gap"] == pytest.approx(0.1)
    assert m["brier"] == pytest.approx(0.26) and m["market_brier"] == pytest.approx(0.25)
    assert m["log_loss"] == pytest.approx(-(math.log(0.6) + math.log(0.4)) / 2)
    assert m["mean_clv"] == pytest.approx(0.005) and m["positive_clv_pct"] == 0.5
    assert m["roi"] == pytest.approx(-0.1 / 3)


def test_corrected_close_is_the_performance_closing_function(tmp_path):
    from tests.test_market_contract import _setup
    close = [("draftkings", 10.5, "OVER", -130), ("draftkings", 10.5, "UNDER", 110)]
    ops = _setup(tmp_path, close=close)
    (row,), _ = MX.graded_rows(ops, "2026-11-19", CUT + pd.Timedelta(hours=6))
    evals, _ = MX.load_evaluations(MX.eval_path(ops, "2026-11-19"))
    e = next(iter(evals.values()))
    cl = MX.closing(MX.Observations(ops, "2026-11-19"), {**e, "entry_fair_probability": e["fair_probability"]})
    assert cl["clv_status"] == row["clv_status"] == "EXACT_LINE"
    assert cl["probability_clv"] == row["probability_clv"]


def test_role_stat_side_aggregation_and_flags():
    assert SR.role_tags(32, {"role_diagnostic": "TOP5_MINUTES"}) == ["STARTER", "HIGH_MINUTES_STARTER"]
    assert SR.role_tags(20, {"role_diagnostic": "ROTATION_6_8"}) == ["ROTATION"]
    assert SR.role_tags(10, {"role_diagnostic": "BENCH_9PLUS"}) == ["LOW_MINUTES_BENCH"]
    assert "NEW_TEAM_TRANSFER" in SR.role_tags(25, {"role_diagnostic": "ROTATION_6_8", "transfer_in": True})
    assert "INJURY_REPLACEMENT" in SR.role_tags(28, {"role_diagnostic": "ROTATION_6_8", "cur_min": 30, "team_games": 3})
    assert SR.role_tags(None, {}) == ["UNKNOWN_ROLE"]
    rows = [{"stat": "PTS", "side": s, "roles": ["STARTER"], "result": r, "model_p": 0.6, "fair_p": 0.55, "edge": 0.05,
             "ev": 0.05, "units": 0.0, "probability_clv": 0.0} for s, r in [("OVER", "WIN"), ("UNDER", "LOSS")] * 30]
    g = SR.group_metrics(rows, lambda r: [(r["stat"], x, r["side"]) for x in r["roles"]])
    assert set(g) == {"PTS | STARTER | OVER", "PTS | STARTER | UNDER"} and g["PTS | STARTER | OVER"]["N"] == 30
    assert SR.cell_flag(g["PTS | STARTER | OVER"]) == "INSUFFICIENT_SAMPLE"            # N < CELL_MIN_N
    watch = SR.metrics([{**r, "model_p": 0.55, "fair_p": 0.52} for r in rows])      # slightly worse than market
    assert SR.cell_flag(watch) == "WATCH"
    good = SR.metrics([{**r, "model_p": 0.50, "fair_p": 0.60} for r in rows])       # beats the market, calibrated
    assert SR.cell_flag(good) == "GOOD"
    assert SR.cell_flag(SR.metrics(rows)) == "BAD"                                   # over-confident by 10pp
    bad = SR.metrics([{**r, "result": "LOSS"} for r in rows])
    assert SR.cell_flag(bad) == "BAD"


def test_actionability_policy_application():
    c = {"ev": 0.05, "edge": 0.04, "model_p": 0.58, "stat": "PTS", "side": "UNDER"}
    assert SR.apply_policy(c, {})
    assert SR.apply_policy(c, {"min_ev": 0.03, "min_edge": 0.03, "max_abs_disagreement": 0.05})
    assert not SR.apply_policy(c, {"max_abs_disagreement": 0.03})
    assert not SR.apply_policy({**c, "edge": None}, {"min_edge": 0.0})
    assert not SR.apply_policy(c, {"excluded_stats": ("PTS",)}) and not SR.apply_policy(c, {"excluded_sides": ("UNDER",)})
    assert not SR.apply_policy(c, {"min_books": 2}, books_at_line=1) and SR.apply_policy(c, {"min_books": 2}, 2)
    assert not SR.apply_policy(None, {})


def test_game_candidate_record_persists_priced_and_stale_offers_once_per_game():
    from cbb_platform.prod import game_exports as GX, game_quotes as GQ
    t = pd.Timestamp("2026-11-20T23:00Z")
    c = {"game_id": "G1", "book": "dk", "market_type": "SPREAD", "base_market": "SPREAD", "side": "HOME", "point": -3.5,
         "decimal": 1.91, "american": -110, "p_win": 0.55, "p_lose": 0.45, "p_push": 0.0, "model_p": 0.55,
         "fair_p": 0.5, "edge": 0.05, "ev": 0.0505, "model_fair_american": -122, "paired": True, "quote_id": "e|dk|s",
         "market_last_update": t, "collector_received_at": t}
    stale = pd.DataFrame([{"game_id": "G1", "book": "fd", "market_type": "ALT_TOTAL", "side": "OVER", "point": 150.5,
                           "decimal": 2.0, "american": 100, "quote_id": "e|fd|t", "market_last_update": t,
                           "collector_received_at": t - pd.Timedelta(hours=5)}])
    r = {"game_id": "G1", "home_team_id": "TH", "away_team_id": "TA", "scheduled_tip_utc": (t + pd.Timedelta(hours=1)).isoformat(),
         "cutoff_utc": t.isoformat(), "forecast_id": "F1", "pmf_sha256": "abc", "model_version": "g1"}
    rec = GX.candidate_record(r, "2026-11-20", [c], stale, t)
    assert rec["kind"] == "GAME_CANDIDATE_OFFERS" and rec["pmf_sha256"] == "abc" and rec["schema"] == GX.OFFER_SET_SCHEMA
    rows = [dict(zip(GX.OFFER_COLS, x)) for x in rec["offers"]]
    assert [x["status"] for x in rows] == ["ELIGIBLE", GQ.STALE_OFFER]
    assert rows[0]["market_type"] == "SPREAD" and rows[0]["side"] == "HOME" and rows[0]["ev_net"] == c["ev"]
    assert rows[1]["model_p"] is None and rows[1]["ev_net"] is None                 # stale: never priced
    body = {k: v for k, v in rec.items() if k != "content_hash"}
    assert GX._hash(body) == rec["content_hash"]
    json.dumps(rec)
