"""Best offer of one game x player x stat (prod.market_eval): every book x line x side priced at its exact line;
highest EV, then probability edge, then model probability, then payout, then bookmaker key. Exact comparisons."""
import socket

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import market_eval as ME, market_exports as MX


def _pmf(w):
    p = np.zeros(40)
    for k, v in w.items():
        p[k] = v
    return p / p.sum()


def _rows(offers):
    out = []
    for book, line, side, am in offers:
        d = 1 + (am / 100 if am > 0 else 100 / abs(am))
        out.append({"sportsbook": book, "line": line, "side": side, "american_odds": am, "decimal_odds": d,
                    "market_last_update": pd.Timestamp("2026-11-04T22:00Z"), "collector_received_at":
                    pd.Timestamp("2026-11-04T22:01Z"), "canonical_game_id": "G", "canonical_player_id": "P",
                    "quote_status": "VALID", "is_alternate": False, "stat": "PTS"})
    return pd.DataFrame(out)


PMF = _pmf({8: 0.25, 10: 0.15, 12: 0.35, 16: 0.25})


def test_highest_ev_across_books_lines_and_sides():
    ev = ME.evaluate(PMF, _rows([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110),
                                 ("fanduel", 11.5, "OVER", 105), ("fanduel", 11.5, "UNDER", -135),
                                 ("betmgm", 15.5, "UNDER", -200), ("betmgm", 15.5, "OVER", 160)]))
    cands, _ = ME.candidates(PMF, _rows([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110),
                                         ("fanduel", 11.5, "OVER", 105), ("fanduel", 11.5, "UNDER", -135),
                                         ("betmgm", 15.5, "UNDER", -200), ("betmgm", 15.5, "OVER", 160)]))
    assert ev["best"]["ev"] == max(c["ev"] for c in cands) and ev["candidates"] == 6


def test_exact_ev_comparison_no_rounding():
    base = {"line": 10.5, "side": "OVER", "model_p": 0.7, "decimal": 1.9, "edge": 0.05}
    lo, hi = {**base, "book": "a", "ev": 0.05}, {**base, "book": "b", "ev": 0.05 + 2e-15, "model_p": 0.55}
    assert ME.best_offer([lo, hi])["book"] == "b"


def test_integer_line_push_lowers_ev_but_is_not_a_loss():
    c, _ = ME.candidates(PMF, _rows([("draftkings", 10.0, "OVER", -110), ("draftkings", 10.0, "UNDER", -110)]))
    o = next(x for x in c if x["side"] == "OVER")
    assert o["p_push"] == pytest.approx(0.15) and o["ev"] == pytest.approx(0.60 * (1 + 100 / 110 - 1) - 0.25)


def test_one_row_per_key_and_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    pm = [{"game_id": "G", "player_id": "P", "team_id": "T", "stat": s, "mean": 11.0, "pmf": PMF, "game_label": "G",
           "player_label": "P", "tip": pd.Timestamp("2026-11-05T00:00Z"), "cutoff": pd.Timestamp("2026-11-04T23:00Z"),
           "forecast_id": "F", "model_version": "m"} for s in ("PTS", "REB")]
    obs = {"G": ({"received_at": "2026-11-04T22:01:00+00:00", "rows": 4, "file": "f"},
                 _rows([("draftkings", 10.5, "OVER", -110), ("draftkings", 10.5, "UNDER", -110),
                        ("fanduel", 10.5, "OVER", -105), ("fanduel", 10.5, "UNDER", -115)]))}
    rows, evals, _, cnt = MX.board(pm, obs, {}, {}, pd.Timestamp("2026-11-04T22:30Z"))
    assert cnt["unique_keys"] == cnt["rows"] == 2 and len(evals) == 1           # REB: model coverage only
    assert MX.board_sheets(rows)["Model Coverage"][0][MX.BOARD_COLS.index("Stat")] == "REB"
