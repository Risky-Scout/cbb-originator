"""Daily Excel exports (prod/exports.py): Boards, Distributions, Grades (D grades D-1), Performance.

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
from cbb_platform.prod import (config as PC, exports as EX, market_exports as MX, pmf_compact as PCX, quote_obs as QO,
                               side_monitor as SD)

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


def _full_tree(tmp_path):
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
    return build_tree(tmp_path, rc, [_game("G2", tip2, "K2", "R1"), _game("G1", tip1, "K1", "R1")], st, pm, act)


@pytest.fixture
def full(tmp_path):
    tree = _full_tree(tmp_path)
    return tree, _run(tree)


def _book(rep, kind):
    return EX.read_xlsx(Path(rep[kind]["path"]))


# ---------------------------------------------------------------- Boards (exact-line market evaluation)
CUT1 = pd.Timestamp("2026-11-04T23:00:00Z")                         # G1: tip 00:00Z = 19:00 ET on 2026-11-04


def _quote(player, stat, book, line, side, american, at):
    d = 1 + (american / 100 if american > 0 else 100 / abs(american))
    return {"provider_event_id": "E1", "sportsbook": book, "market_key": "player_x", "is_alternate": False,
            "provider_player_name": player, "stat": stat, "line": line, "side": side, "american_odds": american,
            "decimal_odds": d, "market_last_update": at - pd.Timedelta(minutes=2), "bookmaker_last_update": None,
            "quote_available_at": None, "collector_received_at": at, "canonical_game_id": "G1",
            "canonical_player_id": player, "quote_status": "VALID", "suppression_reason": None}


@pytest.fixture
def market(tmp_path):
    """the `full` evidence + one real pre-cutoff observation of G1 (Zed PTS at two books, Amy PRA at one) and the G1
    final box, exported once after the cutoff."""
    tree = _full_tree(tmp_path)
    at = CUT1 - pd.Timedelta(minutes=30)
    rows = [_quote("Zed", "PTS", b, 14.5, s, o, at) for b, s, o in (("draftkings", "OVER", 120), ("draftkings", "UNDER", -150),
                                                                   ("fanduel", "OVER", 115), ("fanduel", "UNDER", -145))]
    rows += [_quote("Amy", "PRA", "betmgm", 20.0, s, o, at) for s, o in (("OVER", -110), ("UNDER", -110))]
    QO.record(tree["ops_root"], DATE, "G1", "E1", at, pd.DataFrame(rows), "QUOTE_REFRESH")
    k = tree["ops_root"] / "ticks" / "KACT"
    k.mkdir(parents=True)
    pd.DataFrame([{"game_id": "G1", "player_id": "Zed", "participation": "PLAYED", "settled_at": "x", "minutes": 33.0,
                   "act_PTS": 18.0, "act_PRA": None},
                  {"game_id": "G1", "player_id": "Amy", "participation": "PLAYED", "settled_at": "x", "minutes": 26.0,
                   "act_PTS": 9.0, "act_PRA": 20.0}]).to_parquet(k / "actuals.parquet")
    return tree, _run(tree)


def test_boards_sheets_columns_membership_and_tiers(market):
    tree, rep = market
    wb = _book(rep, "boards")
    assert tuple(wb) == MX.BOARD_SHEETS
    for s in MX.BOARD_SHEETS:
        assert tuple(wb[s]["rows"][0]) == MX.BOARD_COLS
    col = {c: i for i, c in enumerate(MX.BOARD_COLS)}
    full_ = wb["Full Board"]["rows"][1:]
    assert sorted((r[col["Player"]], r[col["Stat"]]) for r in full_) == [("Amy", "PRA"), ("Zed", "PTS")]
    cov = wb["Model Coverage"]["rows"][1:]
    assert [(r[col["Player"]], r[col["Stat"]]) for r in cov] == [("Bo", "AST")]
    assert all(r[col["Market Status"]] == "VALID_MARKET" for r in full_)
    amy = next(r for r in full_ if r[col["Player"]] == "Amy")
    assert amy[col["Push Probability"]] == pytest.approx(0.25) and amy[col["Best Market Line"]] == 20.0   # PMF mass at 20
    keys = [(r[col["Canonical Game ID"]], r[col["Canonical Player ID"]], r[col["Stat"]]) for r in full_ + cov]
    assert len(keys) == len(set(keys)) == rep["boards"]["rows"] == 3
    r = rep["boards"]["receipt"]
    assert r["full_board_rows"] == 2 and r["model_only_rows"] == 1 and r["games_with_props"] == 1
    assert r["over_rows"] + r["under_rows"] + r["no_positive_ev_rows"] == 2
    assert r["tier_65_plus"] + r["tier_60_plus"] + r["tier_actionable"] + r["tier_pass_no_edge"] == 2
    for k in ("raw_quote_observations", "matched_quotes", "unmatched_player_quotes", "unmatched_game_events",
              "credits_consumed", "quota_remaining", "model_player_stat_rows", "games_without_props"):
        assert k in r
    rec = json.loads((Path(rep["boards"]["path"]).parent / EX.BOARD_RECEIPT).read_text())
    assert rec["full_board_rows"] == 2


def test_grades_d_grades_prior_slate_against_the_exact_line(market):
    tree, rep = market
    assert rep["grades"]["graded_slate_date"] == "2026-11-03" and rep["grades"]["rows"] == 0     # never same-day
    assert rep["evaluations"]["written"] == 2                         # G1 cutoff passed: frozen
    nxt = _run(tree, "2026-11-05")
    g = nxt["grades"]
    assert g["status"] == "WRITTEN" and g["graded_slate_date"] == DATE and g["rows"] == 2
    wb = _book(nxt, "grades")
    assert tuple(wb) == MX.GRADE_SHEETS and tuple(wb["Graded Predictions"]["rows"][0]) == MX.GRADED_COLS
    col = {c: i for i, c in enumerate(MX.GRADED_COLS)}
    res = {(r[col["Player"]], r[col["Stat"]]): r for r in wb["Graded Predictions"]["rows"][1:]}
    amy = res[("Amy", "PRA")]
    assert amy[col["Line"]] == 20.0 and amy[col["Actual"]] == 20 and amy[col["Result"]] == "PUSH"
    assert amy[col["Units"]] == 0                                     # a push is not a loss
    zed = res[("Zed", "PTS")]
    assert zed[col["Result"]] == ("WIN" if zed[col["Side"]] == "OVER" else "LOSS")
    assert nxt["performance"]["status"] == "WRITTEN" and nxt["performance"]["start_date"] == DATE


def test_role_boundaries_unknown_and_no_realized_minutes_leakage():
    """role from prediction-time expected minutes only."""
    assert EX.player_role(30.0) == "CORE_30_PLUS" and EX.player_role(29.999) == "ROTATION_20_TO_30"
    assert EX.player_role(20.0) == "ROTATION_20_TO_30" and EX.player_role(19.999) == "LIMITED_UNDER_20"
    assert EX.player_role(0.001) == "LIMITED_UNDER_20" and EX.player_role(0.0) == "NO_EXPECTED_MINUTES"
    assert EX.player_role(-3) == "NO_EXPECTED_MINUTES"
    for x in (float("nan"), None, "abc", float("inf"), -float("inf"), True):
        assert EX.player_role(x) == "UNKNOWN_ROLE"


def test_export_date_uses_america_new_york():
    """a 22:30 ET tip (03:30Z next UTC day) belongs to the ET slate date; file names follow MM_DD_YYYY."""
    assert EX.file_names("2026-11-04") == {"boards": "CBB_Boards_11_04_2026.xlsx",
                                           "distributions": "CBB_Distributions_11_04_2026.xlsx",
                                           "grades": "CBB_Grades_11_04_2026.xlsx",
                                           "performance": "CBB_Performance_11_04_2026.xlsx",
                                           "distribution_pmfs": "CBB_Distributions_11_04_2026_pmf_refs.parquet",
                                           "distributions_long": "CBB_Distributions_11_04_2026_LONG.xlsx"}
    assert EX.slate_date_of("2026-11-05T03:30:00Z") == "2026-11-04"
    assert EX.slate_date_of("2026-11-05T04:59:00Z") == "2026-11-04" and EX.slate_date_of("2026-11-05T05:00:00Z") == "2026-11-05"
    assert MX.prior_day("2026-11-01") == "2026-10-31"


def test_export_semantic_idempotence(market):
    """identical sheets / order / values on rerun at the same instant; nothing rewritten."""
    tree, _ = market
    now = pd.Timestamp("2026-11-05T12:00:00Z")
    rep = EX.daily("2026-11-05", ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
                   history_db=None, state_root=None, now=now)
    before = {k: EX.semantic(Path(rep[k]["path"])) for k in ("grades", "performance")}
    rep2 = EX.daily("2026-11-05", ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
                    history_db=None, state_root=None, now=now)
    assert {k: EX.semantic(Path(rep2[k]["path"])) for k in before} == before
    assert rep2["grades"]["rewritten"] is False and rep2["performance"]["rewritten"] is False


def test_distributions_columns_mass_and_no_negative(full, tmp_path):
    """7 summary workbook: one row per (game, player, stat) with DIST_SUMMARY_COLS; 8 PMF mass (folded tail kept) from
    the compact PMF parquet beside it and from the on-request long derivative; 9 negative PMF fails."""
    tree, rep = full
    wb = _book(rep, "distributions")
    assert list(wb) == ["Distributions"]
    rows = wb["Distributions"]["rows"]
    assert rows[0] == list(EX.DIST_SUMMARY_COLS)
    assert len(rows) - 1 == 3 and len({(r[0], r[3], r[4]) for r in rows[1:]}) == 3          # one row per distribution
    col = {c: i for i, c in enumerate(EX.DIST_SUMMARY_COLS)}
    refs = pd.read_parquet(rep["distributions"]["pmf_refs_path"])
    assert len(refs) == 3 and rep["distributions"]["pmf_refs"] == 3
    assert "probabilities" not in refs.columns                       # references only: the payload is stored once
    assert all(Path(p).exists() for p in refs["pmf_source"])         # ... in the canonical durable / run file
    pm = EX.resolve_pmf_refs(refs)
    for r in pm.itertuples(index=False):
        v = np.asarray(r.probabilities)
        assert (v >= 0).all() and abs(v.sum() - 1) <= EX.PMF_TOL and r.support_max == len(v) - 1
        assert r.pmf_sha256 == PCX.content_sha(0, v)
    zed = pm[pm["player_label"] == "Zed"].iloc[0]
    assert zed.support_max == 120 and zed.probabilities[-1] > 0                  # terminal (folded tail) outcome kept
    zrow = next(r for r in rows[1:] if r[col["PLAYER"]] == "Zed")
    assert zrow[col["PMF_SHA256"]] == zed.pmf_sha256 and zrow[col["MAX_OUTCOME"]] == 120
    assert rep["distributions"]["PMF_RECONCILIATION"] == "PASS"
    assert wb["Distributions"]["styles"][f"{EX._col(col['MEAN'])}2"] == "0.000"
    long = EX.read_xlsx(EX.expand_distributions(DATE, tree["out_root"]))["Distributions"]["rows"]
    assert long[0] == list(EX.DIST_COLS)
    mass = {}
    for r in long[1:]:
        mass[(r[0], r[2], r[3])] = mass.get((r[0], r[2], r[3]), 0) + r[6]
        assert r[6] >= 0
    assert len(mass) == 3 and all(abs(v - 1) <= EX.PMF_TOL for v in mass.values())
    lz = [r for r in long[1:] if r[2] == "Zed"]
    assert lz[-1][5] == 120 and lz[-1][6] > 0
    bad = build_tree(tmp_path / "neg", [], [_game("G9", "2026-11-05T00:00:00+00:00", "K9", "R9")],
                     pmfs={"G9": [{"game_id": "G9", "team_id": "T", "player_id": "P", "market": "PTS", "method": "X",
                                   "projection": 1.0, "pmf": [1.2, -0.2]}]})
    with pytest.raises(EX.ExportIntegrityError):
        _run(bad)
    assert EX.pmf_mass_check([["g", "t", "p", "PTS", 1.0, 0, 0.5], ["g", "t", "p", "PTS", 1.0, 1, 0.4]])["PMF_RECONCILIATION"] == "FAIL"


# ---------------------------------------------------------------- 10-18 Grades + profit
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
    assert rep["grades"]["status"] == "WRITTEN" and rep["grades"]["GRADE_STATUS"] == "NO_MARKET_PREDICTIONS"
    assert sorted(p.name for p in (tree["out_root"] / DATE).glob("*.xlsx")) == ["CBB_Grades_11_04_2026.xlsx",
                                                                                "CBB_Performance_11_04_2026.xlsx"]
    assert rep["performance"]["status"] == "WRITTEN"            # REQUIRED: cumulative, written even without evidence
    from cbb_platform.prod import shadow_tick as TK
    assert EX.DECIDED == TK.DECIDED
    assert EX.main(["daily", "--date", DATE, "--ops-root", str(tree["ops_root"]), "--runs-root", str(tree["runs_root"]),
                    "--out-root", str(tree["out_root"]), "--history-db", str(tmp_path / "none.duckdb"),
                    "--state-root", str(tmp_path / "none")]) == 2


def test_pmf_refs_detect_tampered_canonical_payload(full):
    """A reference whose canonical vector no longer hashes to its pmf_sha256 is refused, never silently expanded."""
    tree, rep = full
    refs = pd.read_parquet(rep["distributions"]["pmf_refs_path"])
    bad = refs.assign(pmf_sha256=["0" * 64] + list(refs["pmf_sha256"][1:]))
    with pytest.raises(EX.ExportIntegrityError):
        EX.resolve_pmf_refs(bad)
    gone = refs.assign(pmf_source=str(tree["out_root"] / "absent.parquet"), pmf_source_rel=None)
    with pytest.raises(EX.ExportIntegrityError):
        EX.resolve_pmf_refs(gone)


def test_migrate_replaces_an_exploded_workbook_only_when_derivation_matches(full):
    """A legacy exploded Distributions workbook is replaced by summary + refs only when the long form derived from the
    references equals it cell for cell; a workbook that differs is left untouched (MISMATCH)."""
    tree, rep = full
    out = tree["out_root"] / DATE
    n = EX.file_names(DATE)
    legacy = EX.read_xlsx(EX.expand_distributions(DATE, tree["out_root"]))       # what the old writer produced
    rows = [r for s in legacy.values() for r in s["rows"][1:]]
    EX.distributions_workbook(rows, out / n["distributions"])                    # an exploded workbook of record
    (out / n["distribution_pmfs"]).unlink()
    kw = dict(ops_root=tree["ops_root"], runs_root=tree["runs_root"], out_root=tree["out_root"],
              history_db=tree.get("history_db"), state_root=tree.get("state_root"))
    bad = [list(r) for r in rows]
    bad[0][6] = bad[0][6] + 1e-12
    EX.distributions_workbook(bad, out / n["distributions"])
    before = (out / n["distributions"]).read_bytes()
    assert EX.migrate_distributions(DATE, **kw)["status"] == "MISMATCH"
    assert (out / n["distributions"]).read_bytes() == before and not (out / n["distribution_pmfs"]).exists()
    EX.distributions_workbook(rows, out / n["distributions"])
    r = EX.migrate_distributions(DATE, **kw)
    assert r["status"] == "MIGRATED" and r["long_rows_verified"] == len(rows)
    wb = EX.read_xlsx(out / n["distributions"])
    assert wb["Distributions"]["rows"][0] == list(EX.DIST_SUMMARY_COLS) and (out / n["distribution_pmfs"]).exists()
    assert EX.migrate_distributions(DATE, **kw)["status"] == "ALREADY_SUMMARY"
    assert not any(p.name.startswith(".migrate-") for p in tree["out_root"].iterdir())
