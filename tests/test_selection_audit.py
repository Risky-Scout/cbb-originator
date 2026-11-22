"""2026-11-22 prospective evidence window: replayable start, live verification, decision gate, export labels."""
from __future__ import annotations

import json

import pandas as pd

from cbb_platform.prod import market_eval as ME, market_exports as MX, selection_audit as SA, selection_replay as SR
from tests.test_selection_replay import _record


def _rows(n, model_p, fair_p, win_every=2, stat="PTS", side="OVER", roles=("STARTER",)):
    return [{"stat": stat, "side": side, "roles": list(roles), "result": "WIN" if i % win_every == 0 else "LOSS",
             "model_p": model_p, "fair_p": fair_p, "edge": model_p - fair_p, "ev": 0.05, "units": 0.0,
             "probability_clv": 0.0} for i in range(n)]


def test_eval_file_constants_match_the_writers():
    from cbb_platform.prod import game_exports as GE
    assert SR.PLAYER_EVAL_FILE == MX.EVAL_FILE and SR.GAME_EVAL_FILE == GE.EVAL_FILE
    assert SR.INCUMBENT_RULE == SR.RULES[0] == "MAX_EV"


def _write(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))


def test_replayable_evidence_start_is_the_first_persisted_offer_set(tmp_path):
    e = _record()
    old = {k: v for k, v in e.items() if k not in ("candidate_offer_set", "candidate_offer_schema")}
    _write(tmp_path / "evaluations" / "2026-11-21" / SR.PLAYER_EVAL_FILE,
           [{"record_type": "MARKET_EVALUATION", **old, "frozen_at": "2026-11-21T23:00:00+00:00"}])
    _write(tmp_path / "evaluations" / "2026-11-22" / SR.PLAYER_EVAL_FILE,
           [{"record_type": "MARKET_EVALUATION", **e, "frozen_at": "2026-11-22T18:00:00+00:00"},
            {"record_type": "MARKET_EVALUATION", **e, "frozen_at": "2026-11-22T17:00:00+00:00"},
            {"record_type": "GAME_FROZEN", "game_id": "G", "frozen_at": "2026-11-22T16:00:00+00:00"}])
    w = SR.replayable_evidence(tmp_path)
    assert w == {"replayable_evidence_start": "2026-11-22T17:00:00+00:00", "replayable_records": 2,
                 "replayable_slate_dates": ["2026-11-22"]}
    assert SR.replayable_evidence(tmp_path / "missing")["replayable_evidence_start"] is None


def test_verify_player_record_requires_complete_set_exact_replay_and_fail_closed():
    e = {**_record(), "frozen_at": "2026-11-22T17:00:00+00:00"}
    assert e["actionable"] is False
    r = SA.verify_player_record(e)
    assert r["PASS"] and r["replay_matches_stored"] and r["offers"] == len(e["candidate_offer_set"])
    assert not SA.verify_player_record({**e, "ev": e["ev"] + 1e-9})["PASS"]            # pick not reproduced
    assert not SA.verify_player_record({**e, "actionable": True})["PASS"]              # fail-closed violated
    bad = [list(x) for x in e["candidate_offer_set"]]
    bad[0][ME.OFFER_COLS.index("quote_id")] = None
    assert SA.verify_player_record({**e, "candidate_offer_set": bad})["missing_offer_fields"] == {"quote_id": 1}
    assert not SA.verify_player_record({**e, "candidate_offer_set": []})["PASS"]


def test_verify_game_record_replays_the_stored_best_by_market():
    from cbb_platform.prod import game_exports as GX
    t = pd.Timestamp("2026-11-20T23:00Z")

    def c(book, side, point, dec, mp, fp):
        return {"game_id": "G1", "book": book, "market_type": "SPREAD", "base_market": "SPREAD", "side": side,
                "point": point, "decimal": dec, "american": -110, "p_win": mp, "p_lose": 1 - mp, "p_push": 0.0,
                "model_p": mp, "fair_p": fp, "edge": mp - fp, "ev": mp * dec - 1, "model_fair_american": -120,
                "paired": True, "quote_id": f"e|{book}|{side}", "market_last_update": t, "collector_received_at": t}
    cands = [c("dk", "HOME", -3.5, 1.91, 0.55, 0.5), c("fd", "HOME", -3.5, 1.95, 0.55, 0.5),
             c("dk", "AWAY", 3.5, 1.91, 0.45, 0.5)]
    r = {"game_id": "G1", "home_team_id": "TH", "away_team_id": "TA", "scheduled_tip_utc": t.isoformat(),
         "cutoff_utc": t.isoformat(), "forecast_id": "F1", "pmf_sha256": "abc", "model_version": "g1"}
    rec = GX.candidate_record(r, "2026-11-20", cands, pd.DataFrame(), t)
    stored = {**cands[1], "market_type": "SPREAD", "pmf_sha256": "abc", "kind": "GAME_MARKET_EVALUATION"}
    v = SA.verify_game_record(rec, [stored])
    assert v["PASS"] and v["replay_matches_stored"] == {"SPREAD": True} and not v["missing_header"]
    assert not SA.verify_game_record(rec, [{**stored, "book": "dk"}])["PASS"]
    assert not SA.verify_game_record(rec, [{**stored, "pmf_sha256": "zzz"}])["PASS"]
    assert not SA.verify_game_record({**rec, "pmf_sha256": "tampered"}, [stored])["PASS"]  # hash + pmf mismatch


def test_paired_differences_sign_and_se():
    worse = SR.metrics(_rows(100, 0.70, 0.50))             # realized .5: the model is worse than the market
    assert worse["brier_diff_vs_market"] > 0 and worse["log_loss_diff_vs_market"] > 0
    better = SR.metrics(_rows(100, 0.50, 0.70))
    assert better["brier_diff_vs_market"] < 0 and better["brier_diff_se"] is not None


def test_decision_gate_three_outcomes():
    bad = SR.metrics(_rows(400, 0.62, 0.50))
    good = SR.metrics([{**r, "model_p": 0.5 if r["result"] == "LOSS" else 0.55, "fair_p": 0.6 if r["result"] == "LOSS"
                        else 0.45} for r in _rows(400, 0.5, 0.5)])
    assert SR.validates(good) and not SR.validates(bad)
    assert not SR.validates({**good, "N": SR.GATE_MIN_GRADED - 1})
    rules = {ru: bad for ru in SR.RULES}
    g = SR.decision_gate(rules, SR.metrics([]), 3, [])
    assert g["gate"] == "INSUFFICIENT_SAMPLE" and not g["enough_sample"]
    assert SR.decision_gate(rules, good, 12, [])["gate"] == "SELECTION_POLICY_VALIDATED"
    assert SR.decision_gate(rules, good, 5, [])["gate"] == "INSUFFICIENT_SAMPLE"        # too few slates
    f = SR.decision_gate(rules, bad, 12, ["PTS | STARTER"])
    assert f["gate"] == "SELECTION_POLICY_FAILED_MODEL_CHALLENGER_JUSTIFIED"
    n = SR.decision_gate(rules, bad, 12, [])
    assert n["gate"] == "INSUFFICIENT_SAMPLE" and n["note"]                               # no localized defect


def test_localized_defects_need_size_and_significance():
    cells = {"PTS | STARTER": SR.metrics(_rows(200, 0.38, 0.5)),       # model .38 vs realized .5
             "REB | STARTER": SR.metrics(_rows(200, 0.49, 0.5)),       # calibrated
             "AST | BENCH": SR.metrics(_rows(20, 0.20, 0.5))}          # too small
    assert SR.localized_defects(cells) == ["PTS | STARTER"]


def test_summarize_reports_every_breakdown_and_keeps_fail_closed():
    picks = []
    for ru in SR.RULES:
        for r in _rows(50, 0.62, 0.50):
            picks.append({**r, "rule": ru, "active": True, "picked": True, "prob_bucket": SR.prob_bucket(0.62),
                          "disagreement_bucket": SR.disagreement_bucket(0.12), "clv_status": "EXACT_LINE"})
    allm = [{**r, "disagreement_bucket": "<=-10pp", "prob_bucket": "<50"} for r in _rows(60, 0.38, 0.5)]
    s = SA.summarize(picks, allm, [], 4)
    for k in ("rules", "rule_x_stat", "rule_x_role", "rule_x_side", "rule_x_prob", "rule_x_disagreement",
              "stat_role_side_flags", "all_markets_disagreement", "info_slope_all_markets", "decision_gate"):
        assert k in s
    assert set(s["rules"]) == set(SR.RULES) and s["rules"]["MAX_EV"]["N"] == 50
    assert s["stat_role_side_flags"]["MAX_EV"]["PTS | STARTER | OVER"] == "BAD"
    assert s["decision_gate"]["gate"] == "INSUFFICIENT_SAMPLE"
    assert s["actionability_recommendation"].startswith("KEEP_FAIL_CLOSED")
    assert s["model_beats_market"] == []


def test_board_exposes_reporting_tier_and_is_actionable_separately():
    from tests.test_market_contract import _obs, _pmfs, q, CUT
    from tests.test_selection_replay import BOOKS
    rows, _, _, counts = MX.board(_pmfs(players=("P-1",)), _obs(q(BOOKS)), {}, {}, CUT - pd.Timedelta(minutes=5))
    col = {c: i for i, c in enumerate(MX.BOARD_COLS)}
    assert {"Reporting Tier", "Is Actionable", "Actionability Status"} <= set(col) and "Selection Tier" not in col
    r = rows[0]
    assert r[col["Is Actionable"]] == "NO" and r[col["Actionability Status"]] == ME.ACTIONABILITY_STATUS
    assert counts["actionable_rows"] == 0


def test_performance_overall_names_rule_actionability_and_replayable_start():
    from cbb_platform.prod import performance as PF
    ev = {"selection_rule": "MAX_EV", "actionability_status": ME.ACTIONABILITY_STATUS,
          "replayable_evidence_start": "2026-11-22T17:00:00+00:00", "replayable_records": 7}
    scope = dict((k, v) for k, v in PF.tables([], ev)["Overall"][1]["rows"])
    assert scope["Selection Rule"] == "MAX_EV"
    assert scope["Replayable Evidence Start (first frozen candidate-offer set)"] == "2026-11-22T17:00:00+00:00"
    assert ME.ACTIONABILITY_STATUS in scope["Actionability"] and "not a wagering approval" in scope["Actionability"]
    assert "By Reporting Tier" in PF.SHEETS and "Reporting Tier" in MX.GRADED_COLS
