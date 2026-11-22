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


# ---------------------------------------------------------------- 2026-11-22: frozen gate, live checks, daily accumulation
def test_validation_gate_v1_is_frozen_and_tamper_evident(tmp_path):
    from cbb_platform import config as C
    f = json.loads((C.PROJECT_ROOT / "docs" / "governance" / "PROPOSED_VALIDATION_GATE_V1.json").read_text())
    assert f["definition"] == SR.gate_definition() and f["definition_sha256"] == SR.gate_sha256()
    assert f["declared_at"] == SR.GATE_DECLARED_AT == "2026-11-22T04:26:24+00:00"
    assert (SR.GATE_ID, SR.GATE_MIN_GRADED, SR.GATE_MIN_SLATES, SR.GATE_Z, SR.GATE_MAX_ABS_CAL_GAP) == \
        ("PROPOSED_VALIDATION_GATE_V1", 300, 10, 2.0, 0.05)
    g = SR.decision_gate({ru: SR.metrics([]) for ru in SR.RULES}, SR.metrics([]), 0, [])
    assert g["gate_id"] == SR.GATE_ID and g["gate_sha256"] == SR.gate_sha256()
    assert SA.pin_gate(tmp_path, "2026-11-22T12:00:00+00:00")["status"] == "PINNED"
    assert SA.pin_gate(tmp_path, "2026-11-23T12:00:00+00:00")["status"] == "UNCHANGED"
    pin = json.loads((tmp_path / "gate_freeze.json").read_text())
    assert pin["pinned_at"] == "2026-11-22T12:00:00+00:00"                     # first pin is never rewritten
    (tmp_path / "gate_freeze.json").write_text(json.dumps({**pin, "definition_sha256": "edited"}))
    assert SA.pin_gate(tmp_path, "2026-11-24T12:00:00+00:00")["status"] == "GATE_DEFINITION_CHANGED"


def _live():
    return {**_record(), "frozen_at": (CUT_TS()).isoformat()}


def CUT_TS():
    from tests.test_market_contract import CUT
    return pd.Timestamp(CUT)


def test_offer_timing_rejects_stale_post_cutoff_and_duplicate_quotes():
    from cbb_platform.prod import config as PC
    e = _live()
    rows = [dict(zip(ME.OFFER_COLS, r)) for r in e["candidate_offer_set"]]
    assert SA.offer_timing(e, rows) == {}
    dec = pd.Timestamp(e.get("entry_received_at") or e["observation_received_at"])
    i = next(i for i, r in enumerate(rows) if r["status"] == ME.ELIGIBLE)
    stale = [dict(r) for r in rows]
    stale[i]["collector_received_at"] = (dec - pd.Timedelta(seconds=PC.QUOTE_MAX_AGE_SECONDS + 60)).isoformat()
    assert SA.offer_timing(e, stale)["STALE_OFFER_ELIGIBLE"] == 1
    late = [dict(r) for r in rows]
    late[i]["collector_received_at"] = (pd.Timestamp(e["cutoff_utc"]) + pd.Timedelta(minutes=1)).isoformat()
    assert SA.offer_timing(e, late)["POST_CUTOFF_OFFER_ELIGIBLE"] == 1
    dup = [dict(r) for r in rows]
    j = next(j for j, r in enumerate(rows) if r["status"] == ME.ELIGIBLE and j != i)
    dup[j]["quote_id"] = dup[i]["quote_id"]
    assert SA.offer_timing(e, dup)["DUPLICATE_QUOTE_ID"] == 1
    bad = [list(x) for x in e["candidate_offer_set"]]
    bad[i][ME.OFFER_COLS.index("collector_received_at")] = (dec - pd.Timedelta(hours=2)).isoformat()
    v = SA.verify_player_record({**e, "candidate_offer_set": bad})
    assert not v["PERSISTENCE_PASS"] and not v["PASS"] and v["timing_defects"] == {"STALE_OFFER_ELIGIBLE": 1}


def test_daily_is_incremental_pins_the_gate_and_never_counts_pre_start_records(tmp_path, monkeypatch):
    from cbb_platform.prod import market_exports as MX
    ops, out = tmp_path / "ops", tmp_path / "audit"

    def sealed(**b):
        b = {k: v for k, v in b.items() if k not in ("record_type", "content_hash")}
        return {"record_type": "MARKET_EVALUATION", **b, "content_hash": MX._hash(b)}
    e = _live()
    old = {k: v for k, v in e.items() if k not in ("candidate_offer_set", "candidate_offer_schema")}
    _write(ops / "evaluations" / "2026-11-21" / SR.PLAYER_EVAL_FILE, [sealed(**old)])
    _write(ops / "evaluations" / "2026-11-22" / SR.PLAYER_EVAL_FILE, [sealed(**e)])
    calls = []

    def fake_rows(o, day, evals, mode):
        calls.append(day)
        persisted = sum(bool(x.get("candidate_offer_set")) for x in evals.values())
        return {"picks": [], "allm": [], "pol": [], "graded": False,
                "recon": {"PERSISTED": persisted, "NOT_REPLAYABLE_BEFORE_START": len(evals) - persisted}}
    monkeypatch.setattr(SA, "_day_rows", fake_rows)
    monkeypatch.setattr(SA, "close_lineage", lambda obs, e, now: {"status": "PENDING_TIP"})
    monkeypatch.setattr(MX, "Observations", lambda ops, day: None)
    r = SA.daily(ops, out, "2026-11-23T14:15:00+00:00")
    assert sorted(calls) == ["2026-11-21", "2026-11-22"]
    assert r["evidence_window"]["replayable_evidence_start"] == e["frozen_at"]
    assert r["records"] == {"PERSISTED": 1, "NOT_REPLAYABLE_BEFORE_START": 1}
    assert r["live"]["LIVE_PLAYER_CANDIDATE_PERSISTENCE"] == "PASS"
    assert r["live"]["LIVE_PLAYER_SELECTION_REPLAY"] == "PASS"
    assert r["live"]["LIVE_PLAYER_CLOSE_LINEAGE"] == "PENDING_TIP"
    assert r["gate_pin"]["status"] == "PINNED" and r["decision_gate"]["gate"] == "INSUFFICIENT_SAMPLE"
    assert r["actionability_validated"] is False and r["headline"]["gate_id"] == SR.GATE_ID
    calls.clear()
    SA.daily(ops, out, "2026-11-24T14:15:00+00:00")
    assert calls == []                                   # complete, unchanged slates come from the per-slate cache
    hist = [json.loads(x) for x in (out / "history.jsonl").read_text().splitlines()]
    assert len(hist) == 2 and (out / "latest.json").exists() and len(list((out / "reports").glob("*.json"))) == 2
    _write(ops / "evaluations" / "2026-11-22" / SR.PLAYER_EVAL_FILE,
           [sealed(**e), sealed(**{**e, "stat": "REB"})])
    SA.daily(ops, out, "2026-11-25T14:15:00+00:00")
    assert calls == ["2026-11-22"]                       # a slate whose evaluation file changed is recomputed
