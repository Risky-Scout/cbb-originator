"""Prospective Over/Under side monitoring (architect ruling 2026-10-21): pregame-exposure strata, side counters,
settlement by side, warning logic, append-only records. Synthetic data only; diagnostics never alter a forecast."""
import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import directional_audit as DA, side_monitor as SD

AS_OF = "2026-11-10T16:00:00Z"
META = {"run_id": "R-test", "model_version": "JM1_FINAL-test", "prediction_as_of": AS_OF, "data_label": "FIXTURE",
        "game_dates": {"G1": "2026-11-10", "G2": "2026-11-11"}}


def _pois(lam: float, k: int = 80) -> np.ndarray:
    p = np.array([math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1)) for i in range(k)])
    return p / p.sum()


def _dist(rows):
    return pd.DataFrame([{"game_id": g, "team_id": t, "player_id": p, "market": m, "pmf": _pois(lam), "projection": lam,
                          "method": "BANK_MIXTURE_PMF", "p_play": 0.97, "game_label": f"{g} label", "player_label": p}
                         for g, t, p, m, lam in rows])


def _q(g, p, m, line, side, book, dec, status="VALID"):
    return {"canonical_game_id": g, "canonical_player_id": p, "stat": m, "line": line, "side": side, "sportsbook": book,
            "decimal_odds": dec, "american_odds": round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1)),
            "quote_status": status, "quote_available_at": pd.Timestamp("2026-11-10T15:50Z"),
            "collector_received_at": pd.Timestamp("2026-11-10T15:51Z")}


EXPO = pd.DataFrame([
    {"game_id": "G1", "team_id": "T1", "player_id": "star", "min_mu": 33.0, "p_play": 0.98, "role_diagnostic": "TOP5_MINUTES",
     "cold_start_class": "WARM", "team_games": 1.0},
    {"game_id": "G1", "team_id": "T1", "player_id": "bench", "min_mu": 8.0, "p_play": 0.7, "role_diagnostic": "BENCH_9PLUS",
     "cold_start_class": "COLD_TRANSFER_IN", "team_games": 1.0}])


# ---------------------------------------------------------------- pregame-exposure stratification
def test_pregame_exposure_strata_use_pregame_quantities_only():
    assert list(DA.min_band([0, 9.99, 10, 19.9, 20, 29.99, 30, 45, np.nan])) == [
        "MIN_LT10", "MIN_LT10", "MIN_10_20", "MIN_10_20", "MIN_20_30", "MIN_20_30", "MIN_30PLUS", "MIN_30PLUS", "MIN_UNKNOWN"]
    assert list(DA.pplay_band([0.2, 0.5, 0.89, 0.9, np.nan])) == [
        "PPLAY_LT_0.5", "PPLAY_0.5_0.9", "PPLAY_0.5_0.9", "PPLAY_GE_0.9", "PPLAY_UNKNOWN"]
    df = pd.DataFrame({"min_mu": [35.0, 12.0, 31.0], "p_play": [0.95, 0.95, 0.6],
                       "role_diagnostic": ["TOP5_MINUTES", "BENCH_9PLUS", "ROTATION_6_8"],
                       "y_min": [5.0, 40.0, 0.0], "y_PTS": [0, 40, 0]})       # realized outcomes contradict exposure
    st = DA.exposure_strata(df)
    assert st["HIGH_EXPOSURE:min_mu>=30"].tolist() == [True, False, True]
    assert st["HIGH_EXPOSURE:min_mu>=30&p_play>=0.9"].tolist() == [True, False, False]
    assert st["LOW_EXPOSURE:min_mu<20"].tolist() == [False, True, False]
    shuffled = df.assign(y_min=[60.0, 0.0, 60.0], y_PTS=[99, 0, 99])
    assert all((DA.exposure_strata(shuffled)[k] == v).all() for k, v in st.items())   # outcomes never move a stratum
    assert DA.HIGH_EXPOSURE_MIN == 30.0 and DA.MIN_BANDS == (0.0, 10.0, 20.0, 30.0)


def test_quote_side_record_carries_pregame_exposure_and_strata():
    d = _dist([("G1", "T1", "star", "PTS", 18.0), ("G1", "T1", "bench", "STOCKS", 1.0)])
    q = pd.DataFrame([_q("G1", "star", "PTS", 17.5, "OVER", "dk", 1.91), _q("G1", "star", "PTS", 17.5, "UNDER", "dk", 1.91),
                      _q("G1", "bench", "STOCKS", 0.5, "OVER", "dk", 1.5)])
    recs = {(r["player_id"], r["side"]): r for r in SD.quote_side_records(d, q, EXPO, META)}
    s = recs[("star", "OVER")]
    assert s["predicted_minutes"] == 33.0 and s["high_exposure"] and s["exposure_band"] == "MIN_30PLUS"
    assert s["p_play_band"] == "PPLAY_GE_0.9" and s["role_pregame"] == "TOP5_MINUTES" and s["opening_season"]
    assert s["game_date"] == "2026-11-10" and s["week"] == "2026-W46" and s["market_kind"] == "STAT"
    b = recs[("bench", "OVER")]
    assert b["transfer_in"] and not b["high_exposure"] and b["exposure_band"] == "MIN_LT10" and b["market_kind"] == "COMBO"
    assert set(SD.QUOTE_SIDE_FIELDS) <= set(s)


# ---------------------------------------------------------------- side counters
def test_side_counters_and_favored_side():
    d = _dist([("G1", "T1", "star", "PTS", 18.0)])
    pmf_before = d["pmf"].iloc[0].copy()
    q = pd.DataFrame([
        _q("G1", "star", "PTS", 15.5, "OVER", "dk", 1.91), _q("G1", "star", "PTS", 15.5, "UNDER", "dk", 1.91),   # OVER value
        _q("G1", "star", "PTS", 21.5, "OVER", "fd", 1.91), _q("G1", "star", "PTS", 21.5, "UNDER", "fd", 1.91),   # UNDER value
        _q("G1", "star", "PTS", 18.0, "OVER", "mgm", 1.30),                                                     # one-sided, bad
        _q("G1", "star", "PTS", 18.0, "UNDER", "mgm", 1.91, status="SUPPRESSED")])                                # ignored
    recs = SD.quote_side_records(d, q, EXPO, META)
    np.testing.assert_array_equal(d["pmf"].iloc[0], pmf_before)          # monitoring never alters the forecast
    assert len(recs) == 5 and len({r["opportunity_id"] for r in recs}) == 3
    by = {(r["sportsbook"], r["side"]): r for r in recs}
    assert by[("dk", "OVER")]["favored_side"] == "OVER" and by[("dk", "OVER")]["recommended_side"] == "OVER"
    assert by[("fd", "UNDER")]["recommended_side"] == "UNDER" and by[("fd", "UNDER")]["model_edge"] > 0
    m = by[("mgm", "OVER")]
    assert m["favored_side"] == "OVER" and m["recommended_side"] == "NONE" and not m["two_sided"] and m["p_push"] > 0
    assert m["p_over"] + m["p_under"] + m["p_push"] == pytest.approx(1.0)
    assert by[("dk", "OVER")]["p_side"] == pytest.approx(by[("dk", "UNDER")]["p_over"])
    c = SD.side_counters(pd.DataFrame(recs))
    assert (c["opportunities"], c["over_favored"], c["under_favored"]) == (3, 2, 1)
    assert (c["over_recommended"], c["under_recommended"], c["no_recommendation"]) == (1, 1, 1)
    assert c["over_share"] == pytest.approx(2 / 3) and c["two_sided_opportunities"] == 2
    assert c["under_recommended_share"]["share"] == pytest.approx(0.5)
    assert c["mean_over_edge"] == pytest.approx(np.mean([by[(b, "OVER")]["model_edge"] for b in ("dk", "fd", "mgm")]))


# ---------------------------------------------------------------- settlement by side
def test_settlement_by_side(tmp_path):
    path = tmp_path / "side.jsonl"
    d = _dist([("G1", "T1", "star", "PTS", 18.0), ("G1", "T1", "star", "PRA", 26.0), ("G1", "T1", "bench", "PTS", 4.0)])
    q = pd.DataFrame([_q("G1", "star", "PTS", 20.0, s, "dk", 1.91) for s in ("OVER", "UNDER")]
                     + [_q("G1", "star", "PRA", 25.5, s, "dk", 1.91) for s in ("OVER", "UNDER")]
                     + [_q("G1", "bench", "PTS", 3.5, s, "dk", 1.91) for s in ("OVER", "UNDER")])
    recs = SD.quote_side_records(d, q, EXPO, META)
    SD.append([{"record_type": "QUOTE_SIDE", "body": r} for r in recs], path)
    act = pd.DataFrame([{"game_id": "G1", "player_id": "star", "participation": "PLAYED", "PTS": 20, "REB": 4, "AST": 3,
                         "FG3M": 2, "STL": 1, "BLK": 0, "TOV": 2},
                        {"game_id": "G1", "player_id": "bench", "participation": "DNP", **{s: None for s in SD.STAT_COLS}}])
    close = pd.DataFrame([{"record_id": next(r["record_id"] for r in recs if r["market"] == "PRA" and r["side"] == "OVER"),
                           "closing_decimal_odds": 1.80}])
    st = SD.settlement_records(path, act, "2026-11-11T05:00Z", "TEST_FINAL_BOX", closing=close)
    SD.append([{"record_type": "SIDE_SETTLEMENT", "body": b} for b in st], path)
    df = SD.load(path).set_index(["player_id", "market", "side"])
    assert df.loc[("star", "PTS", "OVER"), "result"] == "PUSH" and df.loc[("star", "PTS", "UNDER"), "result"] == "PUSH"
    assert pd.isna(df.loc[("star", "PTS", "OVER"), "brier"])                  # pushes carry no side Brier
    o, u = df.loc[("star", "PRA", "OVER")], df.loc[("star", "PRA", "UNDER")]
    assert o["actual"] == 27 and o["result"] == "WIN" and u["result"] == "LOSS"   # combo actual = exact sum
    assert o["signed_error"] == pytest.approx(26.0 - 27)
    assert o["brier"] == pytest.approx((o["p_side_nonpush"] - 1) ** 2)
    assert u["log_loss"] == pytest.approx(-math.log(1 - u["p_side_nonpush"]))
    assert o["clv"] == pytest.approx(1.91 / 1.80 - 1) and pd.isna(u["clv"])      # CLV only from a real later price
    assert (df.xs(("bench", "PTS"), level=(0, 1))["result"] == "NO_ACTION").all()           # DNP is never a settled zero
    assert df.xs(("bench", "PTS"), level=(0, 1))["actual"].isna().all()
    cal = SD.side_calibration(SD.load(path), "OVER", recommended_only=False)
    assert (cal["wins"], cal["losses"], cal["pushes"]) == (1, 0, 1)
    assert SD.settlement_records(path, act, "2026-11-11T06:00Z", "TEST_FINAL_BOX") == []   # never settled twice


# ---------------------------------------------------------------- warning logic
def _frame(n_games=60, err=0.0, under_hit=None, high=True, market="PTS", side_rec="UNDER", seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for k in range(4):
            proj = 20.0
            y = proj + err + rng.normal(0, 4)       # err > 0: outcomes above projection (UNDER-PREDICTION)
            p = 0.55
            win = float(rng.random() < (p if under_hit is None else under_hit))
            for side in ("OVER", "UNDER"):
                rec = side_rec
                rows.append({"record_id": f"{g}-{k}-{side}", "opportunity_id": f"{g}-{k}", "game_id": f"G{g}",
                             "player_id": f"P{k}", "market": market, "side": side, "line": 19.5, "sportsbook": "dk",
                             "high_exposure": high, "exposure_band": "MIN_30PLUS" if high else "MIN_LT10",
                             "opening_season": False, "transfer_in": False, "role_pregame": "TOP5_MINUTES",
                             "market_kind": "STAT", "game_date": "2026-11-10", "week": "2026-W46",
                             "prediction_as_of": AS_OF, "projection": proj, "p_over": 0.45, "p_under": 0.55, "p_push": 0.0,
                             "p_side_nonpush": p if side == "UNDER" else 1 - p, "model_edge": 0.05 if side == rec else -0.1,
                             "favored_side": rec, "recommended_side": rec, "two_sided": True, "settled_at": "x",
                             "actual": y, "signed_error": proj - y, "result": "WIN" if (win if side == "UNDER" else 1 - win) else "LOSS",
                             "side_win": win if side == "UNDER" else 1 - win, "brier": None, "log_loss": None, "clv": None})
    d = pd.DataFrame(rows)
    d["brier"] = (d["p_side_nonpush"] - d["side_win"]) ** 2
    return d


def _codes(d):
    rep = {"high_exposure_pts": SD.high_exposure_pts(d),
           "opening_season": {m: SD._group(d[d["opening_season"] & (d["market"] == m)]) for m in SD.SIDE_MONITOR_OPENING_SEASON_MARKETS},
           "transfer": {m: SD._group(d[d["transfer_in"] & (d["market"] == m)]) for m in SD.SIDE_MONITOR_TRANSFER_IN_MARKETS},
           "by": {"market": {m: SD._group(g) for m, g in d.groupby("market")}}}
    return {w["code"] for w in SD.warnings(rep, d)}


def test_warning_logic_and_stop_condition():
    calm = _frame(err=0.0, under_hit=0.55, side_rec="OVER", seed=1)
    calm["recommended_side"] = np.where(np.arange(len(calm)) % 4 < 2, "OVER", "UNDER")
    assert not _codes(calm) - {"OVER_UNDER_CONCENTRATION_WARNING"}
    under = _frame(err=3.0, under_hit=0.55, seed=2)                 # projections 3 points low; Under side well calibrated
    c = _codes(under)
    assert "PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING" in c
    assert "STOP_HIGH_EXPOSURE_UNDER_PROJECTION_WITH_POOR_UNDER_CALIBRATION" not in c
    bad = _frame(err=3.0, under_hit=0.30, seed=3)                   # + Under recommendations lose far more than expected
    c = _codes(bad)
    assert {"PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING", "SIDE_CALIBRATION_WARNING",
            "STOP_HIGH_EXPOSURE_UNDER_PROJECTION_WITH_POOR_UNDER_CALIBRATION", "OVER_UNDER_CONCENTRATION_WARNING"} <= c
    low = _frame(err=-3.0, under_hit=0.55, high=False, seed=4)      # low exposure, projections too high
    assert "LOW_EXPOSURE_OVER_BIAS_WARNING" in _codes(low)
    early = _frame(err=-2.0, under_hit=0.55, high=False, market="TOV", seed=5).assign(opening_season=True, transfer_in=True)
    assert {"EARLY_SEASON_DIRECTIONAL_WARNING", "TRANSFER_DIRECTIONAL_WARNING"} <= _codes(early)
    assert SD.AUTOMATIC_MODEL_CORRECTION is False


def test_report_on_empty_and_populated_log(tmp_path):
    p = tmp_path / "side.jsonl"
    r = SD.report(p)
    assert r["status"] == "NO_QUOTED_OPPORTUNITIES_YET" and r["warnings"] == [] and not r["stop_for_architect_review"]
    d = _dist([("G1", "T1", "star", "PTS", 18.0)])
    q = pd.DataFrame([_q("G1", "star", "PTS", 17.5, s, "dk", 1.91) for s in ("OVER", "UNDER")])
    SD.append([{"record_type": "QUOTE_SIDE", "body": b} for b in SD.quote_side_records(d, q, EXPO, META)], p)
    r = SD.report(p)
    assert r["status"] == "TRACKING" and r["high_exposure_pts"]["opportunities"] == 1
    assert set(r["by"]) == {"market", "market_kind", "exposure_band", "role_pregame", "sportsbook", "day", "week"}
    assert r["high_exposure_pts"]["under_settled"] == 0                # nothing settled yet: reported, not assumed


# ---------------------------------------------------------------- append-only monitoring records
def test_append_only_records(tmp_path):
    p = tmp_path / "side.jsonl"
    d = _dist([("G1", "T1", "star", "PTS", 18.0)])
    q = pd.DataFrame([_q("G1", "star", "PTS", 17.5, s, "dk", 1.91) for s in ("OVER", "UNDER")])
    recs = SD.quote_side_records(d, q, EXPO, META)
    SD.append([{"record_type": "QUOTE_SIDE", "body": r} for r in recs], p)
    with pytest.raises(SD.MonitorViolation, match="immutable"):
        SD.append([{"record_type": "QUOTE_SIDE", "body": recs[0]}], p)
    with pytest.raises(SD.MonitorViolation, match="missing fields"):
        SD.append([{"record_type": "QUOTE_SIDE", "body": {k: v for k, v in recs[0].items() if k != "p_play"}}], p)
    with pytest.raises(SD.MonitorViolation, match="prediction-time"):
        SD.append([{"record_type": "SIDE_SETTLEMENT", "body": {"record_id": recs[0]["record_id"], "projection": 1.0}}], p)
    with pytest.raises(SD.MonitorViolation, match="unknown record"):
        SD.append([{"record_type": "SIDE_SETTLEMENT", "body": {"record_id": "nope"}}], p)
    s = {k: None for k in SD.SETTLEMENT_FIELDS} | {"record_id": recs[0]["record_id"], "result": "NO_ACTION"}
    SD.append([{"record_type": "SIDE_SETTLEMENT", "body": s}], p)
    with pytest.raises(SD.MonitorViolation, match="already settled"):
        SD.append([{"record_type": "SIDE_SETTLEMENT", "body": s}], p)
    assert SD.verify(p) == {**SD.verify(p), "rows": 3, "quote_sides": 2, "settlements": 1, "CHAIN_VALID": True}
    lines = p.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["body"]["projection"] = 99.0
    bad = tmp_path / "tampered.jsonl"
    bad.write_text("\n".join([json.dumps(tampered)] + lines[1:]) + "\n")
    with pytest.raises(SD.MonitorViolation, match="row_hash"):
        SD.verify(bad)
    drop = tmp_path / "dropped.jsonl"
    drop.write_text("\n".join(lines[1:]) + "\n")
    with pytest.raises(SD.MonitorViolation, match="chain broken"):
        SD.verify(drop)
