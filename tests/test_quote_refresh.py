"""Tiered quote refresh (prod/quote_refresh.py) + latest-observation Boards / freeze (prod/exports.py).

T = the official prediction cutoff (TIP_MINUS_1H). Before T-6h: no paid request. T-6h..<T-2h: <= 1 / 60 min.
T-2h..<T-30m: <= 1 / 30 min. T-30m..<T: <= 1 / 10 min. At / after T: none. A quote change only REPRICES the latest
persisted PMF (no refit, no walk-forward, no simulation). Synthetic fixtures; no network, no credential.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import (config as PC, exports as EX, matching as MT, pmf_evidence as PE, pricing as PX,
                               quote_refresh as QR, run as RN, shadow_tick as SH, simulate as SM)

TIP = pd.Timestamp("2026-11-08T01:00:00Z")          # 20:00 ET on 2026-11-07
CUT = TIP - pd.Timedelta(hours=1)                    # official cutoff 00:00Z
DAY = "2026-11-07"


def T(minutes_before_cutoff: float) -> pd.Timestamp:
    return CUT - pd.Timedelta(minutes=minutes_before_cutoff)


# ---------------------------------------------------------------- tier boundaries (exact, inclusive / exclusive)
@pytest.mark.parametrize("mins,name,iv", [
    (361, "BEFORE_T_MINUS_6H", None), (360, "T6H_T2H", 60), (121, "T6H_T2H", 60), (120, "T2H_T30M", 30),
    (31, "T2H_T30M", 30), (30, "T30M_CUTOFF", 10), (0.5, "T30M_CUTOFF", 10), (0, "AFTER_CUTOFF", None),
    (-10, "AFTER_CUTOFF", None)])
def test_tier_boundaries(mins, name, iv):
    assert QR.tier(CUT, T(mins)) == (name, iv)


def test_due_rate_limits_and_no_request_before_6h_or_after_cutoff():
    assert QR.due(CUT, T(400), None)[0] is False                         # morning discovery only
    assert QR.due(CUT, T(0), None)[0] is False and QR.due(CUT, T(-5), None)[0] is False
    assert QR.due(CUT, T(360), None)[0] is True
    assert QR.due(CUT, T(310), T(360))[0] is False                       # 50 min < 60
    assert QR.due(CUT, T(300), T(360) + pd.Timedelta(seconds=40))[0] is True   # slot grid: 60 min
    assert QR.due(CUT, T(100), T(120))[0] is False and QR.due(CUT, T(90), T(120))[0] is True
    assert QR.due(CUT, T(20), T(30), official_pending=False)[0] is True


def test_official_tick_snapshot_is_counted_in_advance():
    # official scoring slot = ceil(cutoff - 50 min) = T(50); a refresh 10 min before it (30-min tier) is skipped
    ok, _, why = QR.due(CUT, T(60), T(90))
    assert ok is False and why.startswith("OFFICIAL_TICK_SNAPSHOT_DUE_AT")
    assert QR.due(CUT, T(60), T(90), official_pending=False)[0] is True


# ---------------------------------------------------------------- one full day of scheduler wakes
class _Session:
    def __init__(self):
        self.entries = []
        self.event_commence = None

    def get(self, *a, **k):
        raise AssertionError("no events listing needed: the event is already matched")


def _sim_day(tmp_path, monkeypatch, official=True):
    ops = tmp_path / "ops"
    ctx = SH.Ctx(ops_root=ops, prod_root=tmp_path / "prod", history_db=tmp_path / "h.duckdb")
    sch = pd.DataFrame([{"canonical_game_id": "G-1", "scheduled_tip_utc": TIP, "cutoff": CUT, "game_universe": "DI_VS_DI",
                         "not_played": False, "home_points": np.nan, "game_date_et": DAY}])
    calls = []
    decided = {}
    monkeypatch.setattr(QR.ST, "current", lambda root: {"increment_db": "x", "manifest": {"source_cutoff": "x"}})
    monkeypatch.setattr(QR.SH, "_validation_state", lambda c, n: {"passed": True})
    monkeypatch.setattr(QR.SH, "schedule", lambda st: sch)
    monkeypatch.setattr(QR.SH, "decided", lambda c: decided)
    monkeypatch.setattr(QR.SL, "build", lambda *a, **k: pd.DataFrame({"canonical_game_id": ["G-1"], "start_time": [TIP]}))
    monkeypatch.setattr(QR.SH, "team_names", lambda c, st: pd.DataFrame())
    monkeypatch.setattr(QR.MT, "odds_team_map", lambda: None)
    monkeypatch.setattr(QR.MT, "team_index", lambda t, m: {})
    monkeypatch.setattr(QR.OF, "listing", lambda now, root: {"events": None, "received_at": now})
    monkeypatch.setattr(QR.OF, "match_events", lambda e, s, t: pd.DataFrame({"canonical_game_id": ["G-1"],
                                                                            "provider_event_id": ["E1"]}))
    monkeypatch.setattr(QR.OF, "event_commence_resolver", lambda *a: None)
    monkeypatch.setattr(QR, "latest_pmf_source", lambda *a: {"kind": "FORECAST_SNAPSHOT", "id": "F1"})
    sess = _Session()
    monkeypatch.setattr(QR.SH, "_session", lambda c, stage: sess)

    def collect(session, eids, out):
        t = clock["now"]
        calls.append(t)
        e = {"endpoint": "event_odds", "params": {"eventId": eids[0]}, "status": "FETCHED",
             "received_at": t.isoformat(), "cost": 7, "response_sha256": "s"}
        session.entries.append(e)
        p = tmp_path / "coll" / f"E1_{t.strftime('%H%M')}.json"          # a collection file as odds_feed.collect writes
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"collector_received_at": t.isoformat(), "events": [{"id": eids[0], "bookmakers": []}],
                                 "market_discovery": {"status": "SUPPORTED_PLAYER_PROP_MARKETS_ADVERTISED"},
                                 "requests": {"event_markets": {**e, "endpoint": "event_markets", "cost": 1},
                                              "event_odds": e}}))
        return [p]
    monkeypatch.setattr(QR.OF, "collect", collect)
    monkeypatch.setattr(QR, "reprice", lambda *a, **k: {"receipts": 0, "status": "NO_MATCHED_VALID_QUOTE"})
    clock = {"now": None}
    ctx.clock = lambda: clock["now"]
    t = T(420)
    while t <= CUT + pd.Timedelta(minutes=60):
        clock["now"] = t + pd.Timedelta(seconds=7)                    # timer jitter inside the slot
        if official and not decided and t >= (CUT - pd.Timedelta(minutes=PC.SCORE_LEAD_MINUTES)).ceil("10min"):
            (ops / "registry").mkdir(parents=True, exist_ok=True)     # the official tick buys ONE snapshot
            with (ops / "registry" / "quote_snapshots.jsonl").open("a") as f:
                f.write(json.dumps({"game_id": "G-1", "event_id": "E1", "status": "LIVE",
                                    "received_at": clock["now"].isoformat()}) + "\n")
            calls.append(("OFFICIAL", clock["now"]))
            decided["G-1"] = {"status": "PREDICTED"}
        QR.run(ctx)
        t += pd.Timedelta(minutes=10)
    return calls, ops


def test_full_day_request_spacing_by_tier_and_zero_after_cutoff(tmp_path, monkeypatch):
    calls, ops = _sim_day(tmp_path, monkeypatch)
    times = [(c[1], "OFFICIAL") if isinstance(c, tuple) else (c, "REFRESH") for c in calls]
    assert all(t < CUT for t, _ in times)                                 # nothing at / after the cutoff
    assert all(t >= T(360) for t, _ in times)                             # nothing before T-6h
    for (a, _), (b, _) in zip(times, times[1:]):                          # spacing >= the tier interval of b
        iv = QR.tier(CUT, b)[1]
        assert QR.slot(b) - QR.slot(a) >= pd.Timedelta(minutes=iv), (a, b)
    refresh = [t for t, k in times if k == "REFRESH"]
    tiers = pd.Series([QR.tier(CUT, t)[0] for t in refresh]).value_counts().to_dict()
    assert tiers == {"T6H_T2H": 4, "T2H_T30M": 2, "T30M_CUTOFF": 3}
    assert sum(1 for _, k in times if k == "OFFICIAL") == 1
    req = [json.loads(l) for l in (ops / "quotes" / "refresh_requests.jsonl").read_text().splitlines()]
    assert len(req) == len(refresh) and {r["tier"] for r in req} == set(tiers)
    last = json.loads((ops / "quotes" / "last_request.json").read_text())
    assert last["G-1"]["last_success_at"] == req[-1]["received_at"]


# ---------------------------------------------------------------- reprice: persisted PMF x real quote, no model work
def _pmf(lam, k=60):
    from math import exp, lgamma, log
    p = np.array([exp(-lam + i * log(lam) - lgamma(i + 1)) for i in range(k)])
    p[-1] = max(0.0, 1.0 - p[:-1].sum())
    return p


def _snapshot(ops: Path, sid: str, aso: pd.Timestamp, lam: float) -> Path:
    d = ops / "forecasts" / DAY / sid
    pmf = _pmf(lam)
    df = pd.DataFrame({"prediction_receipt_id": f"rx-{sid}", "game_id": "G-1", "player_id": "P-1", "team_id": "T-H",
                       "stat": "PTS", "prediction_as_of": aso.isoformat(), "model_version": "MV-TEST",
                       "outcome": np.arange(len(pmf)), "probability": pmf, "mean": float((np.arange(len(pmf)) * pmf).sum()),
                       "run_id": f"R-{sid}", "method": "JM1_FINAL", "game_label": "Away U @ Home U 2026-11-07",
                       "player_label": "John Smith"})
    PE.write(d / PE.FILE, df)
    pd.DataFrame([{"game_id": "G-1", "team_id": "T-H", "player_id": "P-1", "min_mu": 31.0, "min_sigma": 4.0, "p_play": 0.98,
                   "role_diagnostic": "TOP5", "cold_start_class": "WARM"}]).to_parquet(d / "forecast_players.parquet")
    with (ops / "forecasts" / "index.jsonl").open("a") as f:
        f.write(json.dumps({"snapshot_id": sid, "slate_date": DAY, "status": "FORECAST", "games": ["G-1"],
                            "prediction_as_of": aso.isoformat(), "cutoffs": {"G-1": CUT.isoformat()}}) + "\n")
    return d / PE.FILE


def _collection(root: Path, now: pd.Timestamp, line: float, over: int, under: int, book="fanduel") -> Path:
    upd = (now - pd.Timedelta(minutes=3)).isoformat()
    ev = {"id": "E1", "home_team": "Home U", "away_team": "Away U", "commence_time": TIP.isoformat(),
          "collector_received_at": (now - pd.Timedelta(minutes=1)).isoformat(),
          "bookmakers": [{"key": book, "title": book, "last_update": upd, "markets": [
              {"key": "player_points", "last_update": upd, "outcomes": [
                  {"name": "Over", "description": "John Smith", "price": over, "point": line},
                  {"name": "Under", "description": "John Smith", "price": under, "point": line}]}]}]}
    p = root / f"E1_{now.strftime('%H%M%S')}.json"
    root.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"label": "LIVE", "provider": "the-odds-api", "collector_received_at": ev["collector_received_at"],
                             "events": [ev]}))
    return p


GAME = {"canonical_game_id": "G-1", "scheduled_tip_utc": TIP, "cutoff": CUT, "game_date_et": DAY}
SLATE = pd.DataFrame([{"canonical_game_id": "G-1", "start_time": TIP, "home_team_id": "T-H", "away_team_id": "T-A",
                       "neutral_site": False}])
TIDX = MT.team_index(pd.DataFrame([{"canonical_team_id": "T-H", "school": "Home U", "display_name": "Home U", "mascot": None},
                                   {"canonical_team_id": "T-A", "school": "Away U", "display_name": "Away U", "mascot": None}]),
                     None)


def _no_model_work(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a quote refresh may never refit / walk-forward / simulate")
    from cbb_platform.walkforward import daily as WD, fit as WF
    for mod, name in ((SM, "simulate_game"), (RN, "daily"), (WD, "morning"), (WF, "history")):
        monkeypatch.setattr(mod, name, boom)


def test_reprice_uses_persisted_pmf_exactly_and_never_refits(tmp_path, monkeypatch):
    _no_model_work(monkeypatch)
    ops = tmp_path / "ops"
    f = _snapshot(ops, "F1", T(400), 15.0)
    before = PE.sha256(f)
    now = T(200)
    src = QR.latest_pmf_source(ops, "G-1", CUT, now)
    assert src["id"] == "F1"
    col = _collection(tmp_path / "col", now, 14.5, -115, -105)
    out = QR.reprice(ops, GAME, src, col, SLATE, TIDX, now, "Q1", {"tier": "T6H_T2H", "event_id": "E1",
                                                                   "response_sha256": "abc"})
    assert out["status"] == "REPRICED" and out["receipts"] == 2
    rr = EX.refresh_receipts(ops, DAY)
    pr = PX.price_line(_pmf(15.0), 14.5)
    o = rr[rr["side"] == "OVER"].iloc[0]
    assert o["p_over"] == pr["p_over"] and o["p_side_nonpush"] == pr["p_over"] / pr["p_nonpush"]
    assert o["model_edge"] == PX.expected_value(pr["p_over"], pr["p_under"], 1 + 100 / 115)
    assert (o["pmf_source_id"], o["pmf_prediction_receipt_id"], o["quote_response_sha256"]) == ("F1", "rx-F1", "abc")
    assert o["cutoff_utc"] == CUT.isoformat() and o["sportsbook"] == "fanduel" and o["line"] == 14.5
    assert PE.sha256(f) == before                                          # the PMF is never rewritten
    assert EX.REFRESH_RECEIPTS == QR.REFRESH_FILE


def test_latest_pmf_is_used_and_post_cutoff_snapshot_ignored(tmp_path):
    ops = tmp_path / "ops"
    _snapshot(ops, "F1", T(400), 15.0)
    _snapshot(ops, "F2", T(250), 16.0)
    _snapshot(ops, "F3", CUT + pd.Timedelta(minutes=5), 18.0)
    assert QR.latest_pmf_source(ops, "G-1", CUT, T(300))["id"] == "F1"
    assert QR.latest_pmf_source(ops, "G-1", CUT, T(100))["id"] == "F2"
    assert QR.latest_pmf_source(ops, "G-1", CUT, CUT + pd.Timedelta(hours=1))["id"] == "F2"


def _boards(ops, out, now):
    return EX.daily(DAY, ops_root=ops, runs_root=out / "runs", out_root=out / "x", history_db=None, state_root=None,
                    now=now)


def _board_line(rep):
    """(consensus line, market status, confidence) of the single Board row, read right after this export."""
    rows = EX.read_xlsx(Path(rep["boards"]["path"]))["Full Board"]["rows"][1:]
    assert len(rows) == 1
    col = {c: i for i, c in enumerate(EX.BOARD_COLS)}
    return rows[0][col["Consensus Line"]], rows[0][col["Market Status"]], rows[0][col["Consensus Confidence"]]


def test_boards_provisional_then_frozen_latest_observation_never_replaced(tmp_path, monkeypatch):
    _no_model_work(monkeypatch)
    ops = tmp_path / "ops"
    _snapshot(ops, "F1", T(400), 15.0)
    src = QR.latest_pmf_source(ops, "G-1", CUT, T(300))
    QR.reprice(ops, GAME, src, _collection(tmp_path / "c", T(300), 12.5, 150, -190), SLATE, TIDX, T(300), "Q1",
               {"tier": "T6H_T2H", "event_id": "E1"})
    r1 = _boards(ops, tmp_path, T(299))
    lines = [_board_line(r1)]
    assert r1["boards"]["status"] == "WRITTEN" and r1["official_frozen_rows"] == 0
    sel = [json.loads(l) for l in EX.selections_path(ops, DAY).read_text().splitlines()]
    assert [s["state"] for s in sel] == ["PROVISIONAL"] and sel[0]["line"] == 12.5
    # a newer observation of the same game supersedes the old one (the old 12.5 OVER +150 is no longer offered)
    QR.reprice(ops, GAME, src, _collection(tmp_path / "c", T(20), 16.5, -110, -110), SLATE, TIDX, T(20), "Q2",
               {"tier": "T30M_CUTOFF", "event_id": "E1"})
    r2 = _boards(ops, tmp_path, T(19))
    lines.append(_board_line(r2))
    sel = [json.loads(l) for l in EX.selections_path(ops, DAY).read_text().splitlines()]
    assert [s["state"] for s in sel] == ["PROVISIONAL", "PROVISIONAL"] and sel[-1]["line"] == 16.5
    assert r2["official_rows"] == 1 and r2["duplicate_game_player_prop_rows"] == 0
    # at the cutoff: frozen; a later (post-cutoff) observation can never replace it
    r3 = _boards(ops, tmp_path, CUT)
    QR.reprice(ops, GAME, src, _collection(tmp_path / "c", CUT + pd.Timedelta(minutes=10), 10.5, 400, -900), SLATE, TIDX,
               CUT + pd.Timedelta(minutes=10), "Q3", {"tier": "AFTER", "event_id": "E1"})
    r4 = _boards(ops, tmp_path, CUT + pd.Timedelta(minutes=11))
    lines.append(_board_line(r4))
    sel = [json.loads(l) for l in EX.selections_path(ops, DAY).read_text().splitlines()]
    frozen = [s for s in sel if s["state"] == "OFFICIAL_FROZEN"]
    assert r3["official_frozen_rows"] == 1 and len(frozen) == 1 and frozen[0]["line"] == 16.5
    assert r4["official_frozen_rows"] == 1 and len(sel) == 3
    assert r4["grades"]["status"] == "WRITTEN"                             # graded from the frozen receipt (PENDING)
    # the consensus Board: the forecast row, market fields from the latest PRE-cutoff observation (never post-cutoff)
    obs = [json.loads(l) for l in (ops / "quotes" / DAY / "observations.jsonl").read_text().splitlines()]
    assert [o["source"] for o in obs] == ["QUOTE_REFRESH"] * 3 and all(o["rows"] > 0 for o in obs)
    assert lines == [(12.5, "VALID_MARKET", "SINGLE_BOOK"), (16.5, "VALID_MARKET", "SINGLE_BOOK"),
                     (16.5, "VALID_MARKET", "SINGLE_BOOK")]
    assert r4["boards"]["rewritten"] is False and r4["distributions"]["rewritten"] is False


def test_latest_observation_rule_keeps_single_official_tick_unchanged():
    of = pd.DataFrame([{"game_id": "G", "run_id": "R1", "capture_utc": "2026-11-07T20:00:00Z",
                        "prediction_as_of": "2026-11-07T20:05:00+00:00", "x": i} for i in range(3)])
    assert len(EX.latest_observation(of)) == 3
    two = pd.concat([of, of.assign(run_id="Q9", capture_utc="2026-11-07T21:00:00Z")], ignore_index=True)
    assert set(EX.latest_observation(two)["run_id"]) == {"Q9"}
