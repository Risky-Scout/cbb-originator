"""Daily Excel exports (prod/exports.py): Boards, Distributions, Grades + Performance Summary.

TEST fixtures only (data_label "TEST", temporary evidence trees under tmp_path); no network, no refit, no model change.
Each test names the directive behaviours (1-35) it covers.
"""
import gzip
import hashlib
import json
import math
import socket
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform.prod import config as PC, exports as EX, side_monitor as SD

LABELS = ("TEST",)
DATE = "2026-11-04"


def _qs(rid, game, player, market, line, side, book, p_np, american, *, team="T1", minutes=32.0, proj=None, tick_run="R1",
        aso="2026-11-04T23:00:00+00:00", recv="2026-11-04T22:55:00+00:00", edge=0.01):
    dec = 1 + (american / 100 if american > 0 else 100 / abs(american)) if american and abs(american) >= 100 else 1.9
    return {"record_id": rid, "opportunity_id": f"o-{rid}", "run_id": tick_run, "model_version": "TEST", "data_label": "TEST",
            "prediction_as_of": aso, "game_id": game, "game": f"Away {game} @ Home {game} 2026-11-04", "game_date": DATE,
            "week": "2026-W45", "player_id": player, "player": player, "team_id": team, "market": market,
            "market_kind": "STAT", "line": line, "side": side, "sportsbook": book, "book_decimal_odds": dec,
            "book_american_odds": american, "quote_available_at": recv, "collector_received_at": recv,
            "predicted_minutes": minutes, "p_play": 0.97, "role_pregame": "TOP5_MINUTES", "cold_start_class": "WARM",
            "team_games_pregame": 1.0, "exposure_band": "x", "p_play_band": "x", "high_exposure": False,
            "opening_season": False, "transfer_in": False, "projection": line + 1.0 if proj is None else proj,
            "p_over": p_np, "p_under": 1 - p_np, "p_push": 0.0, "p_side": p_np, "p_side_nonpush": p_np,
            "fair_decimal": 1 / p_np if p_np else None, "fair_american": None, "model_edge": edge, "favored_side": side,
            "recommended_side": side, "two_sided": True}


def _pmf(lam, k=121):
    p = np.array([math.exp(-lam + i * math.log(lam) - math.lgamma(i + 1)) for i in range(k)])
    p[-1] = max(0.0, 1.0 - p[:-1].sum())                       # folded tail at kmax (the model's terminal outcome)
    return p


def build_tree(root: Path, receipts: list[dict], games: list[dict], settlements: list[dict] | None = None,
               pmfs: dict | None = None, actuals: list[dict] | None = None) -> dict:
    ops, runs = root / "ops", root / "runs"
    reg = ops / "registry" / "games.jsonl"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text("".join(json.dumps(g) + "\n" for g in games))
    for g in games:
        tdir = ops / "ticks" / g["tick_id"]
        tdir.mkdir(parents=True, exist_ok=True)
        mine = [r for r in receipts if r["game_id"] == g["game_id"]]
        log = tdir / "side_monitor.jsonl"
        if mine:
            SD.append([{"record_type": "QUOTE_SIDE", "body": r} for r in mine], log)
            gz = tdir / "side_monitor.jsonl.gz"
            with gzip.open(gz, "wt") as f:
                f.write(log.read_text())
            log.unlink()
        players = sorted({(r["player_id"], r["team_id"]) for r in mine}) or [("P1", "T1")]
        pd.DataFrame([{"game_id": g["game_id"], "player_id": p, "team_id": t, "player": p, "game": f"Away {g['game_id']} @ Home"}
                      for p, t in players]).to_parquet(tdir / "projections.parquet", index=False)
    for run in sorted({g["run_id"] for g in games}):            # one market_pmfs.parquet per run (all its games)
        rows = [r for g in games if g["run_id"] == run for r in (pmfs or {}).get(g["game_id"], [])]
        if rows:
            (runs / run).mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_parquet(runs / run / "market_pmfs.parquet", index=False)
    if settlements:
        sd = ops / "ticks" / "KSETTLE"
        sd.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(settlements).to_parquet(sd / "side_settlements.parquet", index=False)
    if actuals:
        pd.DataFrame(actuals).to_parquet(ops / "ticks" / "KSETTLE" / "actuals.parquet", index=False)
    return {"ops_root": ops, "runs_root": runs, "out_root": root / "TEST_exports"}


def _game(gid, tip, tick="K1", run="R1", rule="TIP_MINUS_1H"):
    return {"game_id": gid, "status": "PREDICTED", "tick_id": tick, "run_id": run, "scheduled_tip": tip,
            "cutoff_rule": rule, "prediction_as_of": "2026-11-04T23:00:00+00:00"}


def _run(tree, date=DATE):
    return EX.daily(date, ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
                    history_db=None, state_root=None, labels=LABELS)


def _sett(rid, result, actual):
    return {"record_id": rid, "settled_at": "2026-11-05T06:00:00+00:00", "result_source": "CBBD_FINAL_BOX",
            "participation": "PLAYED" if result != "NO_ACTION" else "DNP", "actual": actual, "signed_error": None,
            "result": result, "side_win": None, "brier": None, "log_loss": None, "closing_decimal_odds": None, "clv": None}


@pytest.fixture
def full(tmp_path):
    tip1, tip2 = "2026-11-05T00:00:00+00:00", "2026-11-05T02:00:00+00:00"
    rc = [
        _qs("a1", "G1", "Zed", "PTS", 14.5, "OVER", "fanduel", 0.62, 120, minutes=34.0),
        _qs("a2", "G1", "Zed", "PTS", 14.5, "UNDER", "fanduel", 0.38, -150, minutes=34.0),
        _qs("a3", "G1", "Amy", "REB", 5.5, "OVER", "draftkings", 0.599999, -110, minutes=25.0),
        _qs("a4", "G1", "Amy", "REB", 5.5, "OVER", "betmgm", 0.600000, -105, minutes=25.0),
        _qs("a5", "G1", "Amy", "PRA", 20.0, "UNDER", "draftkings", 0.649999, -120, minutes=25.0),
        _qs("a6", "G1", "Amy", "PRA", 20.0, "OVER", "draftkings", 0.650000, 100, minutes=25.0),
        _qs("b1", "G2", "Bo", "AST", 3.5, "UNDER", "fanduel", 0.72, -130, minutes=10.0, team="T2"),
        _qs("b2", "G2", "Bo", "AST", 3.5, "OVER", "fanduel", 0.53, 0, minutes=10.0, team="T2"),      # invalid odds
        _qs("b3", "G2", "Cy", "PTS", 8.5, "OVER", "fanduel", 0.45, 150, minutes=float("nan"), team="T2"),
        _qs("b4", "G2", "Di", "PTS", 2.5, "OVER", "fanduel", 0.56, -110, minutes=0.0, team="T2"),
    ]
    st = [_sett("a1", "WIN", 20.0), _sett("a2", "LOSS", 20.0), _sett("a3", "LOSS", 4.0), _sett("a4", "LOSS", 4.0),
          _sett("a5", "PUSH", 20.0), _sett("a6", "PUSH", 20.0), _sett("b1", "WIN", 2.0), _sett("b2", "LOSS", 2.0),
          _sett("b3", "NO_ACTION", None)]                      # b4: unsettled -> PENDING
    pm = {"G1": [{"game_id": "G1", "team_id": "T1", "player_id": "Zed", "market": "PTS", "method": "BANK_MIXTURE_PMF",
                  "projection": 15.5, "pmf": _pmf(15.5).tolist()},
                 {"game_id": "G1", "team_id": "T1", "player_id": "Amy", "market": "PRA", "method": "BANK_DRAWS",
                  "projection": 21.0, "pmf": [0.0] * 18 + [0.25, 0.25, 0.25, 0.25, 0.0, 0.0]}],
          "G2": [{"game_id": "G2", "team_id": "T2", "player_id": "Bo", "market": "AST", "method": "BANK_MIXTURE_PMF",
                  "projection": 3.0, "pmf": _pmf(3.0).tolist()}]}
    act = [{"game_id": "G2", "player_id": "Bo", "participation": "PLAYED", "minutes": 39.0}]   # realized >> expected
    tree = build_tree(tmp_path, rc, [_game("G2", tip2, "K2", "R1"), _game("G1", tip1, "K1", "R1")], st, pm, act)
    return tree, _run(tree)


def _book(rep, kind):
    return EX.read_xlsx(Path(rep[kind]["path"]))


# ---------------------------------------------------------------- 1-6 Boards
def test_boards_sheets_columns_membership_thresholds_and_order(full):
    """1 sheet names/order, 2 columns/order, 3 Full Board membership, 4 60% boundary, 5 65% boundary, 6 ordering."""
    tree, rep = full
    wb = _book(rep, "boards")
    assert list(wb) == ["Full Board", "60 Percent Plus", "65 Percent Plus"]
    last = EX._col(len(EX.BOARD_COLS) - 1)
    for s in wb.values():
        assert s["rows"][0] == list(EX.BOARD_COLS)
        assert s["freeze"] == "A2" and s["filters"] == [s["filters"][0]] and s["filters"][0].startswith(f"A1:{last}")
        assert all(f"{EX._col(i)}1" in s["bold"] for i in range(len(EX.BOARD_COLS)))
    board = wb["Full Board"]["rows"][1:]                        # the forecast universe: one row per persisted PMF key
    col = {c: i for i, c in enumerate(EX.BOARD_COLS)}
    assert sorted((r[col["Canonical Game ID"]], r[col["Canonical Player ID"]], r[col["Stat"]]) for r in board) == \
        [("G1", "Amy", "PRA"), ("G1", "Zed", "PTS"), ("G2", "Bo", "AST")]
    assert {r[col["Market Status"]] for r in board} == {"NO_VALID_MARKET"}   # no quote observation: blank market
    assert rep["boards"]["duplicate_keys"] == 0 and rep["boards"]["unique_player_stat_keys"] == 3
    full_rows = rep["official_selection_rows"]                  # the official best-offer selection (drives Grades)
    assert len(full_rows) == 6                                  # one official best offer per GAME x PLAYER x PROP
    p60 = {(r[2], r[3], r[7], r[11]) for r in full_rows if r[8] >= 0.60}
    assert ("Amy", "REB", "OVER", "betmgm") in p60 and ("Amy", "REB", "OVER", "draftkings") not in p60
    p65 = {(r[2], r[3], r[7]) for r in full_rows if r[8] >= 0.65}
    assert ("Amy", "PRA", "OVER") in p65 and ("Amy", "PRA", "UNDER") not in p65
    # order: tip (G1 00:00Z before G2 02:00Z), GAME, PLAYER, PROP, LINE, PICK, BOOK
    keys = [(r[0], r[2], r[3], r[4], r[7], r[11]) for r in full_rows]
    assert [k[0].startswith("Away G1") for k in keys] == [True] * 3 + [False] * 3
    assert keys[:3] == sorted(keys[:3]) and keys[3:] == sorted(keys[3:])
    z = [r for r in full_rows if r[2] == "Zed" and r[7] == "OVER"][0]
    assert z[5] == pytest.approx(15.5) and z[6] == pytest.approx(1.0)          # PROJ, ΔLINE = PROJ - LINE
    assert z[9] == pytest.approx((1 / 2.2) / (1 / 2.2 + 1 / (1 + 100 / 150)))   # same-book no-vig MARKET%
    assert z[10] == pytest.approx(z[8] - z[9])                                   # EDGE = MODEL% - MARKET%
    assert z[12] == 120 and isinstance(z[12], int) and z[13] == pytest.approx(0.01)
    assert z[14] == "TIP_MINUS_1H" and z[15] == "2026-11-04T22:55:00Z"
    fm = wb["Full Board"]["styles"]
    assert fm[f"{EX._col(col['Model Projection'])}2"] == "0.00"
    assert all(w <= 40 for w in wb["Full Board"]["widths"].values())


# ---------------------------------------------------------------- 7-9 Distributions
def test_distributions_columns_mass_and_no_negative(full, tmp_path):
    """7 exact columns, 8 PMF mass (folded tail kept), 9 negative PMF fails."""
    tree, rep = full
    wb = _book(rep, "distributions")
    assert list(wb) == ["Distributions"]
    rows = wb["Distributions"]["rows"]
    assert rows[0] == list(EX.DIST_COLS)
    mass = {}
    for r in rows[1:]:
        mass[(r[0], r[2], r[3])] = mass.get((r[0], r[2], r[3]), 0) + r[6]
        assert r[6] >= 0
    assert len(mass) == 3 and all(abs(v - 1) <= EX.PMF_TOL for v in mass.values())
    zed = [r for r in rows[1:] if r[2] == "Zed"]
    assert zed[-1][5] == 120 and zed[-1][6] > 0                 # terminal (folded tail) outcome exported
    assert rep["distributions"]["PMF_RECONCILIATION"] == "PASS"
    assert wb["Distributions"]["styles"]["G2"] == "0.0000%" and wb["Distributions"]["styles"]["E2"] == "0.000"
    bad = build_tree(tmp_path / "neg", [], [_game("G9", "2026-11-05T00:00:00+00:00", "K9", "R9")],
                     pmfs={"G9": [{"game_id": "G9", "team_id": "T", "player_id": "P", "market": "PTS", "method": "X",
                                   "projection": 1.0, "pmf": [1.2, -0.2]}]})
    with pytest.raises(EX.ExportIntegrityError):
        _run(bad)
    assert EX.pmf_mass_check([["g", "t", "p", "PTS", 1.0, 0, 0.5], ["g", "t", "p", "PTS", 1.0, 1, 0.4]])["PMF_RECONCILIATION"] == "FAIL"


# ---------------------------------------------------------------- 10-18 Grades + profit
def test_grades_sheets_columns_results_and_profit(full):
    """10 sheets, 11 columns, 12 WIN +odds, 13 WIN -odds, 14 LOSS, 15 PUSH, 16 VOID, 17 PENDING, 18 invalid odds."""
    tree, rep = full
    wb = _book(rep, "grades")
    assert list(wb) == ["Graded Bets", "Performance Summary", "Status"]
    assert wb["Status"]["rows"][0] == list(EX.GRADE_STATUS_COLS)
    g = wb["Graded Bets"]["rows"]
    assert g[0] == list(EX.GRADE_COLS)
    by = {(r[2], r[3], r[5], r[9]): r for r in g[1:]}
    assert len(g) - 1 == 6 and all(r[11] in EX.RESULTS for r in g[1:])
    assert by[("Zed", "PTS", "OVER", "fanduel")][13] == pytest.approx(1.2)               # +120 WIN
    assert by[("Bo", "AST", "UNDER", "fanduel")][13] == pytest.approx(100 / 130)          # -130 WIN
    assert by[("Amy", "REB", "OVER", "betmgm")][13] == -1.0                               # LOSS
    assert ("Zed", "PTS", "UNDER", "fanduel") not in by and ("Amy", "REB", "OVER", "draftkings") not in by
    assert by[("Amy", "PRA", "OVER", "draftkings")][11] == "PUSH" and by[("Amy", "PRA", "OVER", "draftkings")][13] == 0.0
    v = by[("Cy", "PTS", "OVER", "fanduel")]
    assert v[11] == "VOID" and v[13] == 0.0 and v[12] is None
    p = by[("Di", "PTS", "OVER", "fanduel")]
    assert p[11] == "PENDING" and p[12] is None and p[13] is None
    assert ("Bo", "AST", "OVER", "fanduel") not in by                                     # odds 0: never an official selection
    assert by[("Zed", "PTS", "OVER", "fanduel")][12] == 20.0                             # REALIZED = authoritative stat
    assert EX.profit("WIN", 250) == 2.5 and EX.profit("WIN", -200) == 0.5 and EX.profit("LOSS", 100) == -1.0
    assert EX.profit("PUSH", -110) == 0.0 and EX.profit("VOID", -110) == 0.0
    assert EX.profit("PENDING", -110) is None and EX.profit("UNSUPPORTED", -110) is None
    assert EX.grade_result("OVER", 0, "WIN") == "UNSUPPORTED" and EX.grade_result("OVER", float("nan"), None) == "UNSUPPORTED"
    assert EX.grade_result("OVER", 50, "WIN") == "UNSUPPORTED" and EX.grade_result("PUSHSIDE", -110, "WIN") == "UNSUPPORTED"
    assert EX.grade_result("UNDER", -110, None) == "PENDING" and EX.grade_result("UNDER", -110, "NO_ACTION") == "VOID"
    s = wb["Graded Bets"]["styles"]
    assert s["G2"] == "0.0%" and s["N2"] == "0.000" and s["K2"] == "0"
    assert any(wb["Graded Bets"]["styles"][f"M{i}"] == "0.0###" for i in range(2, 12) if f"M{i}" in s)


def _sections(rows):
    """Parse the Performance Summary sheet into {title: (header, body)}; checks two blank rows between sections."""
    out, i = {}, 0
    while i < len(rows):
        title = rows[i][0]
        hdr = rows[i + 1]
        j = i + 2
        body = []
        while j < len(rows) and rows[j]:
            body.append(rows[j])
            j += 1
        out[title] = (hdr, body)
        if j < len(rows):
            assert rows[j] == [] and rows[j + 1] == []
        i = j + 2
    return out


# ---------------------------------------------------------------- 19-30 Performance Summary
def test_performance_summary_sections_and_reconciliation(full):
    """19 overall reconciliation, 20 stat grouping, 26 stat x role, 27 side, 28 band boundaries, 29 calibration error,
    30 TOTAL reconciliation."""
    tree, rep = full
    wb = _book(rep, "grades")
    ps = wb["Performance Summary"]
    sec = _sections(ps["rows"])
    assert list(sec) == list(EX.SECTIONS)
    assert len(ps["filters"]) == len(EX.SECTIONS)                       # one filtered table per section
    hdr, body = sec["Overall Performance"]
    assert hdr == list(EX.OVERALL_COLS)
    ov = dict(zip(hdr, body[0]))
    assert ov["NIGHT"] == DATE and ov["SOURCE"] == EX.SOURCE_TEXT
    assert (ov["BETS"], ov["WINS"], ov["LOSSES"], ov["PUSHES"], ov["VOIDS"], ov["PENDING"], ov["UNSUPPORTED"]) == (6, 2, 1, 1, 1, 1, 0)
    assert ov["DECISIONS"] == 3 and ov["HIT_RATE"] == pytest.approx(2 / 3)
    prof = 1.2 + 100 / 130 - 1
    assert ov["PROFIT"] == pytest.approx(prof) and ov["ROI"] == pytest.approx(prof / 3)
    hdr, body = sec["Performance by Stat"]
    assert hdr == ["STAT_CATEGORY"] + list(EX.GROUP_METRICS)
    assert [r[0] for r in body] == ["AST", "PRA", "PTS", "REB", "TOTAL"]      # canonical keys, alphabetical, no merging
    tot = dict(zip(hdr, body[-1]))
    for k in ("BETS", "DECISIONS", "WINS", "LOSSES", "PUSHES", "VOIDS"):
        assert tot[k] == ov[k] == sum(dict(zip(hdr, r))[k] for r in body[:-1])
    assert tot["PROFIT"] == pytest.approx(ov["PROFIT"])
    pts = dict(zip(hdr, body[2]))
    assert pts["BETS"] == 3 and pts["AVG_MODEL%"] == pytest.approx((0.62 + 0.45 + 0.56) / 3)
    hdr, body = sec["Performance by Stat × Player Role"]
    assert hdr == ["STAT_CATEGORY", "PLAYER_ROLE"] + list(EX.GROUP_METRICS)
    assert [(r[0], r[1]) for r in body] == [("AST", "LIMITED_UNDER_20"), ("PRA", "ROTATION_20_TO_30"),
                                            ("PTS", "CORE_30_PLUS"), ("PTS", "NO_EXPECTED_MINUTES"),
                                            ("PTS", "UNKNOWN_ROLE"), ("REB", "ROTATION_20_TO_30")]
    assert sum(r[2] for r in body) == ov["BETS"] and all(r[2] > 0 for r in body)
    hdr, body = sec["Performance by Side"]
    assert [r[0] for r in body] == ["OVER", "UNDER", "TOTAL"]
    assert body[0][1] + body[1][1] == body[2][1] == ov["BETS"]
    hdr, body = sec["Performance by Model Probability"]
    assert hdr == list(EX.BAND_COLS) and [r[0] for r in body] == [b[0] for b in EX.BANDS]
    bands = {r[0]: dict(zip(hdr, r)) for r in body}
    assert sum(b["BETS"] for b in bands.values()) == ov["BETS"]
    assert bands["55-60%"]["BETS"] == 1 and bands["<50%"]["BETS"] == 1 and bands["60-65%"]["BETS"] == 2 and bands["65-70%"]["BETS"] == 1
    w = bands["70%+"]
    assert w["BETS"] == 1 and w["REALIZED_WIN_RATE"] == 1.0 and w["CALIBRATION_ERROR"] == pytest.approx(1.0 - 0.72)
    assert bands["50-55%"]["BETS"] == 0 and bands["50-55%"]["DECISIONS"] == 0
    assert bands["50-55%"]["REALIZED_WIN_RATE"] is None and bands["50-55%"]["CALIBRATION_ERROR"] is None
    assert [EX.model_band(p) for p in (0.4999, 0.5, 0.5499, 0.55, 0.5999, 0.6, 0.6499, 0.65, 0.6999, 0.7)] == \
        ["<50%", "50-55%", "50-55%", "55-60%", "55-60%", "60-65%", "60-65%", "65-70%", "65-70%", "70%+"]
    assert rep["grades"]["GRADE_RECONCILIATION"] == "PASS" and all(rep["grades"]["reconciliation"].values())
    st = ps["styles"]
    pct = [ref for ref, f in st.items() if f == "0.0%"]
    assert pct and all(ref in ps["bold"] for ref in ("A1", "A2"))


def test_role_boundaries_unknown_and_no_realized_minutes_leakage(full):
    """21 boundary 30, 22 boundary 20, 23 boundary 0, 24 UNKNOWN_ROLE, 25 no realized-minutes leakage + role table."""
    assert EX.player_role(30.0) == "CORE_30_PLUS" and EX.player_role(29.999) == "ROTATION_20_TO_30"
    assert EX.player_role(20.0) == "ROTATION_20_TO_30" and EX.player_role(19.999) == "LIMITED_UNDER_20"
    assert EX.player_role(0.001) == "LIMITED_UNDER_20" and EX.player_role(0.0) == "NO_EXPECTED_MINUTES"
    assert EX.player_role(-3) == "NO_EXPECTED_MINUTES"
    for x in (float("nan"), None, "abc", float("inf"), -float("inf"), True):
        assert EX.player_role(x) == "UNKNOWN_ROLE"
    tree, rep = full
    sec = _sections(_book(rep, "grades")["Performance Summary"]["rows"])
    hdr, body = sec["Performance by Player Role"]
    assert hdr == ["PLAYER_ROLE"] + list(EX.GROUP_METRICS)
    assert [r[0] for r in body] == list(EX.ROLES) + ["TOTAL"]
    roles = {r[0]: r[1] for r in body}
    # Bo: expected 10 min (prediction time) but realized 39 min -> stays LIMITED_UNDER_20
    assert roles == {"CORE_30_PLUS": 1, "ROTATION_20_TO_30": 2, "LIMITED_UNDER_20": 1, "NO_EXPECTED_MINUTES": 1,
                     "UNKNOWN_ROLE": 1, "TOTAL": 6}
    assert sum(roles[r] for r in EX.ROLES) == roles["TOTAL"]


def test_zero_count_role_rows_kept(tmp_path):
    tree = build_tree(tmp_path, [_qs("x", "G1", "P", "PTS", 5.5, "OVER", "fanduel", 0.6, -110, minutes=31)],
                      [_game("G1", "2026-11-05T00:00:00+00:00")])
    sec = _sections(_book(_run(tree), "grades")["Performance Summary"]["rows"])
    body = sec["Performance by Player Role"][1]
    assert [r[0] for r in body] == list(EX.ROLES) + ["TOTAL"] and [r[1] for r in body] == [1, 0, 0, 0, 0, 1]
    row = body[1] + [None] * 20                                    # blank trailing cells are not stored
    assert row[7] is None and row[9] is None                # HIT_RATE / ROI blank when DECISIONS = 0


# ---------------------------------------------------------------- 31 date semantics
def test_export_date_uses_america_new_york(tmp_path):
    """31: a 22:30 ET tip (03:30Z next UTC day) belongs to the ET slate date; file names follow MM_DD_YYYY."""
    tree = build_tree(tmp_path, [_qs("e1", "GL", "P", "PTS", 5.5, "OVER", "fanduel", 0.61, -110,
                                     aso="2026-11-05T02:00:00+00:00", recv="2026-11-05T01:59:00+00:00")],
                      [_game("GL", "2026-11-05T03:30:00+00:00")])
    rep = _run(tree, DATE)
    assert rep["official_rows"] == 1 and rep["grades"]["status"] == "WRITTEN"
    assert Path(rep["grades"]["path"]).name == "CBB_Grades_11_04_2026.xlsx"
    assert Path(rep["grades"]["path"]).parent == tree["out_root"] / "2026-11-04"
    assert EX.file_names("2026-11-04") == {"boards": "CBB_Boards_11_04_2026.xlsx",
                                           "distributions": "CBB_Distributions_11_04_2026.xlsx",
                                           "grades": "CBB_Grades_11_04_2026.xlsx"}
    nxt = _run(tree, "2026-11-05")
    assert nxt["predicted_games"] == 0 and nxt["boards"]["status"] == "MISSING_EVIDENCE" and "official_rows" not in nxt
    assert EX.slate_date_of("2026-11-05T04:59:00Z") == "2026-11-04" and EX.slate_date_of("2026-11-05T05:00:00Z") == "2026-11-05"


def test_post_tip_receipts_excluded(tmp_path):
    """NO_POST_TIP_LEAKAGE: a receipt captured at / after the tip is never a board or grade row."""
    tree = build_tree(tmp_path, [_qs("ok", "G1", "P", "PTS", 5.5, "OVER", "fanduel", 0.61, -110),
                                 _qs("late", "G1", "P", "REB", 5.5, "OVER", "fanduel", 0.61, -110,
                                     recv="2026-11-05T00:00:00+00:00")],
                      [_game("G1", "2026-11-05T00:00:00+00:00")])
    rep = _run(tree)
    assert rep["no_post_tip_leakage"]["post_tip_excluded"] == 1 and rep["official_rows"] == 1


# ---------------------------------------------------------------- 32 idempotence + immutability
def test_export_semantic_idempotence_and_immutable_fields(full):
    """32: identical sheets / order / values on rerun; settlement may fill PENDING; immutable fields may never change."""
    tree, rep = full
    before = {k: EX.semantic(Path(rep[k]["path"])) for k in ("boards", "distributions", "grades")}
    raw = {k: Path(rep[k]["path"]).read_bytes() for k in before}
    rep2 = _run(tree)
    assert {k: EX.semantic(Path(rep2[k]["path"])) for k in before} == before
    assert {k: Path(rep2[k]["path"]).read_bytes() for k in before} == raw        # deterministic package as well
    # settle the PENDING row -> only RESULT / REALIZED / PROFIT change
    s = tree["ops_root"] / "ticks" / "KSETTLE2"
    s.mkdir()
    pd.DataFrame([_sett("b4", "WIN", 4.0)]).to_parquet(s / "side_settlements.parquet", index=False)
    rep3 = _run(tree)
    g3 = {(r[2], r[3], r[5], r[9]): r for r in EX.semantic(Path(rep3["grades"]["path"]))[0][1][1:]}
    assert g3[("Di", "PTS", "OVER", "fanduel")][11:] == ["WIN", 4.0, pytest.approx(100 / 110)]
    # tamper an immutable field in the previous export's receipt record -> FAIL
    sc = Path(rep3["grades"]["path"]).parent / "grade_receipts.json"
    d = json.loads(sc.read_text())
    d["receipts"]["a1"]["ODDS"] = 999
    sc.write_text(json.dumps(d))
    with pytest.raises(EX.ExportIntegrityError):
        _run(tree)


# ---------------------------------------------------------------- 33-35 no network / no refit / frozen artifacts
def test_export_no_network_no_refit_no_artifact_change(full, monkeypatch, tmp_path):
    """33 zero network calls, 34 no refit / scoring invoked, 35 frozen artifacts unchanged."""
    tree, _ = full
    arts = {k: hashlib.sha256((C.PROJECT_ROOT / p).read_bytes()).hexdigest() for k, (p, _) in PC.ARTIFACTS.items()}
    assert arts == {k: h for k, (_, h) in PC.ARTIFACTS.items()}
    calls = []

    def refuse(*a, **k):
        calls.append(a)
        raise OSError("network refused in export test")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    import cbb_platform.prod.run as RN
    import cbb_platform.walkforward.daily as WD
    import cbb_platform.walkforward.fit as WF

    def boom(*a, **k):
        raise AssertionError("export invoked scoring / refit")
    monkeypatch.setattr(RN, "daily", boom)
    for mod in (WD, WF):
        for name in dir(mod):
            if callable(getattr(mod, name)) and getattr(getattr(mod, name), "__module__", "") == mod.__name__:
                monkeypatch.setattr(mod, name, boom)
    rep = _run(tree)
    assert calls == [] and rep["network_calls"] == 0 and rep["refits_triggered"] == 0
    imports = [l.strip() for l in Path(EX.__file__).read_text().splitlines() if l.strip().startswith(("import ", "from "))]
    for forbidden in ("walkforward", "run", "simulate", "odds_acquire", "cbbd_acquire", "rotowire_acquire", "requests",
                      "urllib", "http", "sync", "shadow_tick"):
        assert not any(forbidden in l.replace("from __future__", "") for l in imports), forbidden
    assert {k: hashlib.sha256((C.PROJECT_ROOT / p).read_bytes()).hexdigest() for k, (p, _) in PC.ARTIFACTS.items()} == arts


def test_missing_evidence_writes_nothing_and_decided_matches_tick(tmp_path):
    tree = build_tree(tmp_path, [], [_game("G1", "2026-11-05T00:00:00+00:00")])
    rep = _run(tree)
    assert all(rep[k]["status"] == "MISSING_EVIDENCE" for k in ("boards", "distributions"))
    assert rep["grades"]["status"] == "WRITTEN" and rep["grades"]["GRADE_STATUS"] == "NOTHING_SETTLED_YET"
    assert [p.name for p in (tree["out_root"] / DATE).glob("*.xlsx")] == ["CBB_Grades_11_04_2026.xlsx"]
    from cbb_platform.prod import shadow_tick as TK
    assert EX.DECIDED == TK.DECIDED
    assert EX.main(["daily", "--date", DATE, "--ops-root", str(tree["ops_root"]), "--runs-root", str(tree["runs_root"]),
                    "--out-root", str(tree["out_root"]), "--history-db", str(tmp_path / "none.duckdb"),
                    "--state-root", str(tmp_path / "none")]) == 2
