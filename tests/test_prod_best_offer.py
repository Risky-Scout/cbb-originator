"""Official best-offer Board / Grades contract (prod/exports.py) and per-game Distributions snapshot selection.

One official row per GAME x PLAYER x PROP: the single offer with MAXIMUM model EV across books, lines and sides; exact
ties broken by MODEL% > bettor-favorable line (same side) > decimal odds > EDGE > OVER before UNDER > BOOK.
TEST fixtures only (data_label "TEST", tmp_path evidence trees); no network, no refit, no model change.
"""
import gzip
import json
import socket
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import exports as EX, pmf_evidence as PE, side_monitor as SD
from tests.test_prod_exports import _game, _pmf, _qs, _sett, build_tree, _run

TIP = "2026-11-05T00:00:00+00:00"                 # cutoff (TIP_MINUS_1H) 2026-11-04T23:00Z
BEFORE = "2026-11-04T22:58:00+00:00"
AFTER = "2026-11-05T06:00:00+00:00"


def _q(rid, line, side, book, p, odds, ev, *, player="P", stat="PTS", market_edge=None):
    r = _qs(rid, "G1", player, stat, line, side, book, p, odds, edge=ev, aso="2026-11-04T22:50:00+00:00",
            recv="2026-11-04T22:45:00+00:00")
    return r


def _daily(tree, now=AFTER):
    return EX.daily("2026-11-04", ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
                    history_db=None, state_root=None, labels=("TEST",), now=now)


def _tree(tmp_path, offers, name="t", settlements=None):
    return build_tree(tmp_path / name, offers, [_game("G1", TIP)], settlements)


def _full(rep):
    return EX.read_xlsx(Path(rep["boards"]["path"]))["Full Board"]["rows"][1:]


def _pick(offers, tmp_path, name="t"):
    """(LINE, PICK, BOOK, ODDS) of the single official row."""
    rows = _full(_daily(_tree(tmp_path, offers, name)))
    assert len(rows) == 1
    return rows[0][4], rows[0][7], rows[0][11], rows[0][12]


def _select(offers):
    of = EX.offers(pd.DataFrame([{**o, "tip_utc": TIP, "window": "W", "tick_id": "K"} for o in offers]),
                   pd.DataFrame(), {})[0]
    return EX.select_official(of)


# ---------------------------------------------------------------- 1-4 maximum EV is the primary decision
def test_1_one_row_per_game_player_prop(tmp_path):
    offers = [_q(f"r{i}", 17.5, "OVER", b, 0.6, o, 0.01 * i) for i, (b, o) in
              enumerate([("fanduel", -110), ("draftkings", -115), ("betmgm", -120), ("caesars", -108)])]
    offers += [_q("s1", 5.5, "UNDER", "fanduel", 0.6, -110, 0.02, stat="REB")]
    rep = _daily(_tree(tmp_path, offers))
    rows = _full(rep)
    assert len(rows) == 2 and len({(r[0], r[2], r[3]) for r in rows}) == 2
    assert rep["raw_quote_rows"] == 5 and rep["official_rows"] == 2 and rep["duplicate_game_player_prop_rows"] == 0


def test_2_highest_ev_across_books(tmp_path):
    assert _pick([_q("a", 17.5, "OVER", "booka", 0.6, -105, 0.03),
                  _q("b", 17.5, "OVER", "bookb", 0.6, -110, 0.05)], tmp_path)[2] == "bookb"   # EV beats the price


def test_3_highest_ev_across_lines(tmp_path):
    assert _pick([_q("a", 17.5, "OVER", "booka", 0.62, -110, 0.02),
                  _q("b", 18.5, "OVER", "bookb", 0.55, 120, 0.08)], tmp_path)[:3] == (18.5, "OVER", "bookb")


def test_4_highest_ev_across_opposite_sides(tmp_path):
    offers = [_q("o1", 17.5, "OVER", "booka", 0.62, -110, 0.02), _q("u1", 17.5, "UNDER", "booka", 0.38, -110, -0.25),
              _q("o2", 19.5, "OVER", "bookb", 0.45, 120, -0.01), _q("u2", 19.5, "UNDER", "bookb", 0.55, -105, 0.07)]
    assert _pick(offers, tmp_path)[:3] == (19.5, "UNDER", "bookb")
    sel = _select(offers)
    assert len(sel) == 1 and set(sel["pick"]) == {"UNDER"}                   # never both sides


# ---------------------------------------------------------------- 5-10 exact tie order
def test_5_equal_ev_higher_model_probability(tmp_path):
    assert _pick([_q("a", 17.5, "UNDER", "booka", 0.58, -110, 0.04),
                  _q("b", 17.5, "OVER", "bookb", 0.61, -110, 0.04)], tmp_path)[:3] == (17.5, "OVER", "bookb")
    assert _pick([_q("a", 17.5, "UNDER", "booka", 0.63, -110, 0.04),
                  _q("b", 17.5, "OVER", "bookb", 0.61, -110, 0.04)], tmp_path, "u")[1] == "UNDER"   # no OVER default


def test_6_same_side_bettor_favorable_line(tmp_path):
    assert _pick([_q("a", 18.5, "OVER", "booka", 0.6, 110, 0.04),
                  _q("b", 17.5, "OVER", "bookb", 0.6, -120, 0.04)], tmp_path)[:3] == (17.5, "OVER", "bookb")
    assert _pick([_q("a", 17.5, "UNDER", "booka", 0.6, 110, 0.04),
                  _q("b", 18.5, "UNDER", "bookb", 0.6, -120, 0.04)], tmp_path, "u")[:3] == (18.5, "UNDER", "bookb")


def test_6b_line_rule_skipped_for_opposite_sides(tmp_path):
    # equal EV / MODEL%, opposite sides: line rule skipped -> decimal odds decides
    assert _pick([_q("a", 17.5, "OVER", "booka", 0.6, -110, 0.04),
                  _q("b", 18.5, "UNDER", "bookb", 0.6, 105, 0.04)], tmp_path)[:3] == (18.5, "UNDER", "bookb")


@pytest.mark.parametrize("a,b,win", [(-115, -105, "bookb"), (105, 110, "bookb"), (-105, 100, "bookb"), (-105, -115, "booka")])
def test_7_same_line_better_decimal_price(tmp_path, a, b, win):
    assert _pick([_q("a", 17.5, "OVER", "booka", 0.6, a, 0.04),
                  _q("b", 17.5, "OVER", "bookb", 0.6, b, 0.04)], tmp_path)[2] == win


def test_7b_decimal_odds_conversion():
    dec = [EX.decimal_odds(o) for o in (120, 105, -105, -110, -125)]
    assert dec == sorted(dec, reverse=True) and EX.decimal_odds(120) == pytest.approx(2.2)
    assert EX.decimal_odds(-125) == pytest.approx(1.8) and EX.decimal_odds(0) is None and EX.decimal_odds(50) is None


def test_8_equal_remaining_values_higher_edge(tmp_path):
    # same side/line/price/EV/MODEL%; book A quotes both sides (MARKET% defined), book B one side (EDGE blank)
    offers = [_q("a", 17.5, "OVER", "zbook", 0.6, -110, 0.04), _q("a2", 17.5, "UNDER", "zbook", 0.4, -110, -0.3),
              _q("b", 17.5, "OVER", "abook", 0.6, -110, 0.04)]
    assert _pick(offers, tmp_path)[2] == "zbook"                           # defined EDGE beats blank


def test_9_complete_tie_over_before_under(tmp_path):
    assert _pick([_q("a", 17.5, "UNDER", "booka", 0.6, -110, 0.04),
                  _q("b", 17.5, "OVER", "bookb", 0.6, -110, 0.04)], tmp_path)[1] == "OVER"


def test_10_final_tie_book_alphabetical_normalized(tmp_path):
    assert _pick([_q("a", 17.5, "OVER", "Zeta", 0.6, -110, 0.04),
                  _q("b", 17.5, "OVER", " alpha", 0.6, -110, 0.04)], tmp_path)[2] == " alpha"


def test_11_no_input_order_dependence(tmp_path):
    offers = [_q("a", 17.5, "OVER", "Zeta", 0.6, -110, 0.04), _q("b", 17.5, "OVER", "alpha", 0.6, -110, 0.04),
              _q("c", 18.5, "UNDER", "mid", 0.6, -110, 0.04), _q("d", 16.5, "OVER", "x", 0.59, -110, 0.04)]
    a = _pick(offers, tmp_path, "x")
    b = _pick(list(reversed(offers)), tmp_path, "y")
    c = _pick(offers[2:] + offers[:2], tmp_path, "z")
    assert a == b == c == (17.5, "OVER", "alpha", -110)


# ---------------------------------------------------------------- 12-13 threshold sheets
def test_12_13_threshold_sheets_pure_full_board_filters(tmp_path):
    offers = [_q("a", 17.5, "OVER", "booka", 0.66, -110, 0.03), _q("b", 17.5, "OVER", "bookb", 0.66, -102, 0.05),
              _q("c", 5.5, "UNDER", "booka", 0.61, -105, 0.02, player="Q", stat="REB"),
              _q("d", 6.5, "UNDER", "bookb", 0.61, -130, 0.01, player="Q", stat="REB"),
              _q("e", 2.5, "OVER", "booka", 0.55, 100, 0.01, player="R", stat="AST")]
    wb = EX.read_xlsx(Path(_daily(_tree(tmp_path, offers))["boards"]["path"]))
    full = wb["Full Board"]["rows"][1:]
    assert len(full) == 3
    for sheet, thr in (("60 Percent Plus", 0.60), ("65 Percent Plus", 0.65)):
        rows = wb[sheet]["rows"][1:]
        assert rows == [r for r in full if r[8] >= thr] and rows[0] in full
    assert [r[11] for r in wb["65 Percent Plus"]["rows"][1:]] == ["bookb"]
    assert [(r[4], r[11]) for r in wb["60 Percent Plus"]["rows"][1:] if r[2] == "Q"] == [(5.5, "booka")]


def test_duplicate_key_aborts():
    with pytest.raises(EX.ExportIntegrityError):
        EX.assert_unique(pd.DataFrame([{"game_id": "G", "player_id": "P", "prop": "PTS"}] * 2))


# ---------------------------------------------------------------- 14-19 provisional / frozen receipts and Grades
def _append_offer(tree, offer):
    tdir = tree["ops_root"] / "ticks" / "K1"
    log = tdir / "side_monitor.jsonl"
    with gzip.open(tdir / "side_monitor.jsonl.gz", "rt") as f:
        log.write_text(f.read())
    SD.append([{"record_type": "QUOTE_SIDE", "body": offer}], log)
    with gzip.open(tdir / "side_monitor.jsonl.gz", "wt") as f:
        f.write(log.read_text())
    log.unlink()


def test_14_provisional_updates_before_cutoff(tmp_path):
    tree = _tree(tmp_path, [_q("a", 17.5, "OVER", "booka", 0.6, -110, 0.02)])
    r1 = _daily(tree, now=BEFORE)
    assert r1["official_selection"]["written"] == {"PROVISIONAL": 1, "OFFICIAL_FROZEN": 0}
    assert r1["boards"]["status"] == "WRITTEN" and r1["grades"]["status"] == "MISSING_EVIDENCE"   # not frozen: no grade
    _append_offer(tree, _q("b", 17.5, "OVER", "bookb", 0.6, -102, 0.05))
    r2 = _daily(tree, now=BEFORE)
    assert r2["official_selection"]["written"]["PROVISIONAL"] == 1 and _full(r2)[0][11] == "bookb"
    assert _daily(tree, now=BEFORE)["official_selection"]["written"]["PROVISIONAL"] == 0      # idempotent
    r3 = _daily(tree, now=AFTER)
    assert r3["official_selection"]["written"]["OFFICIAL_FROZEN"] == 1 and r3["official_frozen_rows"] == 1
    recs = [json.loads(l) for l in EX.selections_path(tree["ops_root"], "2026-11-04").read_text().splitlines()]
    assert [(r["state"], r["book"]) for r in recs] == [("PROVISIONAL", "booka"), ("PROVISIONAL", "bookb"),
                                                       ("OFFICIAL_FROZEN", "bookb")]
    for f in ("game_id", "player_id", "team_id", "stat", "pick", "line", "book", "odds", "model_p", "market_p", "edge",
              "ev", "expected_minutes", "role", "model_version", "forecast_snapshot_id", "prediction_as_of",
              "capture_utc", "selected_at", "window", "cutoff_utc", "content_hash", "receipt_hash"):
        assert f in recs[-1]


def test_15_frozen_cannot_change_after_cutoff(tmp_path):
    tree = _tree(tmp_path, [_q("a", 17.5, "OVER", "booka", 0.6, -110, 0.02)])
    _daily(tree, now=AFTER)
    path = EX.selections_path(tree["ops_root"], "2026-11-04")
    before = path.read_text()
    late = _qs("late", "G1", "P", "PTS", 16.5, "OVER", "bookz", 0.7, 150, edge=0.5, aso="2026-11-04T22:50:00+00:00",
               recv="2026-11-04T23:30:00+00:00")                          # after cutoff, before tip
    _append_offer(tree, late)
    rep = _daily(tree, now=AFTER)
    assert path.read_text() == before and rep["official_selection"]["written"] == {"PROVISIONAL": 0, "OFFICIAL_FROZEN": 0}
    assert _full(rep)[0][11] == "booka"
    # a different selection for a frozen key is a hard fail
    have = EX.load_selections(path)
    k = next(iter(have))
    bad = dict(have[k]["frozen"], odds=999)
    body = {f: bad[f] for f in EX.CONTENT_FIELDS}
    bad["content_hash"] = EX._hash(body)
    with pytest.raises(EX.ExportIntegrityError):
        EX.write_receipt(path, bad, have)
    assert EX.write_receipt(path, have[k]["frozen"], have) is False      # identical content: idempotent
    d = json.loads(before)
    d["odds"] = 999                                                         # tampered receipt fails the export
    path.write_text(json.dumps(d) + "\n")
    with pytest.raises(EX.ExportIntegrityError):
        _daily(tree)


def test_16_to_19_grades_use_frozen_receipt_only(tmp_path):
    offers = [_q("a", 17.5, "OVER", "booka", 0.6, -110, 0.02), _q("b", 17.5, "OVER", "bookb", 0.6, 120, 0.06),
              _q("c", 17.5, "UNDER", "bookb", 0.4, -115, -0.1), _q("d", 18.5, "OVER", "bookc", 0.5, 150, 0.04)]
    st = [_sett(x, "WIN" if x != "c" else "LOSS", 20.0) for x in "abcd"]
    tree = _tree(tmp_path, offers, settlements=st)
    rep = _daily(tree)
    g = EX.read_xlsx(Path(rep["grades"]["path"]))["Graded Bets"]["rows"][1:]
    assert len(g) == 1 and len({(r[0], r[2], r[3]) for r in g}) == 1               # 17, 18: one row, rejected not graded
    assert (g[0][9], g[0][10], g[0][11]) == ("bookb", 120, "WIN") and g[0][13] == pytest.approx(1.2)   # 19: frozen odds
    sec = EX.read_xlsx(Path(rep["grades"]["path"]))["Performance Summary"]["rows"]
    assert sec[2][2] == 1                                                         # Overall BETS = official rows only


# ---------------------------------------------------------------- 20-22 Distributions snapshot selection
def _forecast(ops, sid, aso, gid, cutoff, lam):
    d = ops / "forecasts" / "2026-11-04" / sid
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"prediction_receipt_id": f"{sid}-x", "game_id": gid, "player_id": "P", "team_id": "T", "stat": "PTS",
                   "prediction_as_of": aso, "model_version": "TEST", "outcome": i, "probability": float(p),
                   "mean": lam, "run_id": sid, "method": "TEST", "game_label": "Away @ Home", "player_label": "P"}
                  for i, p in enumerate(_pmf(lam, 40))]).to_parquet(d / PE.FILE, index=False)
    with (ops / "forecasts" / "index.jsonl").open("a") as f:
        f.write(json.dumps({"snapshot_id": sid, "slate_date": "2026-11-04", "status": "FORECAST", "prediction_as_of": aso,
                            "games": [gid], "cutoffs": {gid: cutoff}}) + "\n")


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
    assert {r[4] for r in rows} == {9.0}                                       # F2 mean
    assert abs(sum(r[6] for r in rows) - 1) <= EX.PMF_TOL and all(r[6] >= 0 for r in rows)


# ---------------------------------------------------------------- 23-25 no refit / no network / no artifact change
def test_23_24_25_quote_refresh_no_refit_no_network_no_artifacts(tmp_path, monkeypatch):
    from cbb_platform.walkforward import daily as WD, fit as WF
    from cbb_platform.prod import lifecycle as L, run as RN
    def boom(*a, **k):
        raise AssertionError("refit / scoring / network attempted")
    monkeypatch.setattr(WD, "morning", boom)
    for name in dir(WF):
        if not name.startswith("_") and callable(getattr(WF, name)) and getattr(getattr(WF, name), "__module__", "") == WF.__name__:
            monkeypatch.setattr(WF, name, boom)
    monkeypatch.setattr(RN, "daily", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    tree = _tree(tmp_path, [_q("a", 17.5, "OVER", "booka", 0.6, -110, 0.02)])
    import hashlib
    from cbb_platform import config as C
    from cbb_platform.prod import config as PC
    def frozen():
        return {k: hashlib.sha256((C.PROJECT_ROOT / rel).read_bytes()).hexdigest() for k, (rel, _) in PC.ARTIFACTS.items()}
    snap = frozen()
    assert snap == {k: h for k, (_, h) in PC.ARTIFACTS.items()}
    rep = _daily(tree, now=BEFORE)
    assert rep["network_calls"] == 0 and rep["refits_triggered"] == 0 and rep["boards"]["status"] == "WRITTEN"
    life = L.Life(ops_root=tree["ops_root"], prod_root=tmp_path / "t", clock=lambda: pd.Timestamp(BEFORE),
                  history_db=Path("/nonexistent"))
    out = L.exports_now(life)
    assert out["lifecycle"] == "EXPORTS" and out["exports"]["distributions"]["status"] in ("WRITTEN", "MISSING_EVIDENCE")
    assert frozen() == snap
