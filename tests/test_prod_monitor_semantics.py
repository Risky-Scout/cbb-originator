"""Monitor market-list naming (production readiness directive 2026-10-26 §4). The two diagnostic modules used to share
ambiguous names (EARLY / OPENING, TRANSFER) for DIFFERENT lists. They are renamed to their real purpose; every membership
is preserved exactly; nothing on the forecast or pricing path reads them.

    FORECASTS_CHANGED = FALSE, PRICING_CHANGED = FALSE (also pinned by tests/test_prod_golden_equivalence.py)
    MONITOR_SEMANTICS_PRESERVED = TRUE
"""
import ast
from pathlib import Path

import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import prospective_monitor as PM, side_monitor as SD
from tests.test_prod_side_monitor import EXPO, META, _dist, _q

# memberships at ca00d6a (before the rename), copied verbatim
SIDE_OPENING_AT_CA00D6A = ("PTS", "AST", "STL", "TOV")                 # side_monitor.OPENING_MARKETS
SIDE_TRANSFER_AT_CA00D6A = ("STL", "STOCKS", "TOV", "REB")             # side_monitor.TRANSFER_MARKETS
REPORT_EARLY_AT_CA00D6A = ("PTS", "AST", "STL", "TOV", "PRA", "PR", "PA")   # prospective_monitor.EARLY_MARKETS
REPORT_TRANSFER_AT_CA00D6A = ("PTS", "REB", "STL", "TOV", "STOCKS")    # prospective_monitor.TRANSFER_MARKETS
OLD_NAMES = ("OPENING_MARKETS", "EARLY_MARKETS", "TRANSFER_MARKETS")
NEW_NAMES = {SD: ("SIDE_MONITOR_OPENING_SEASON_MARKETS", "SIDE_MONITOR_TRANSFER_IN_MARKETS"),
             PM: ("EARLY_SEASON_REPORT_MARKETS", "TRANSFER_COLD_START_REPORT_MARKETS")}
# modules that produce forecasts, prices, the board or the ledger
FORECAST_PATH = ("run.py", "board.py", "pricing.py", "simulate.py", "inputs.py", "features.py", "slate.py",
                 "universe.py", "loader.py", "matching.py", "quotes.py", "readiness.py", "ledger.py", "bundle.py")


def test_memberships_preserved_exactly():
    assert SD.SIDE_MONITOR_OPENING_SEASON_MARKETS == SIDE_OPENING_AT_CA00D6A
    assert SD.SIDE_MONITOR_TRANSFER_IN_MARKETS == SIDE_TRANSFER_AT_CA00D6A
    assert PM.EARLY_SEASON_REPORT_MARKETS == REPORT_EARLY_AT_CA00D6A
    assert PM.TRANSFER_COLD_START_REPORT_MARKETS == REPORT_TRANSFER_AT_CA00D6A


def test_ambiguous_names_gone_and_new_names_distinct():
    for mod in (SD, PM):
        assert not [n for n in OLD_NAMES if hasattr(mod, n)], mod.__name__
    names = [n for ns in NEW_NAMES.values() for n in ns]
    assert len(set(names)) == len(names)
    assert not set(NEW_NAMES[SD]) & set(dir(PM)) and not set(NEW_NAMES[PM]) & set(dir(SD))


def test_side_monitor_report_strata_follow_the_lists(tmp_path):
    p = tmp_path / "side.jsonl"
    d = _dist([("G1", "T1", "star", "PTS", 18.0)])
    q = pd.DataFrame([_q("G1", "star", "PTS", 17.5, s, "dk", 1.91) for s in ("OVER", "UNDER")])
    SD.append([{"record_type": "QUOTE_SIDE", "body": b} for b in SD.quote_side_records(d, q, EXPO, META)], p)
    rep = SD.report(p)
    assert rep["status"] == "TRACKING"
    assert tuple(rep["opening_season"]) == SIDE_OPENING_AT_CA00D6A          # report keys unchanged by the rename
    assert tuple(rep["transfer"]) == SIDE_TRANSFER_AT_CA00D6A


def test_monitor_lists_never_reach_forecast_or_pricing():
    root = C.PROJECT_ROOT / "cbb_platform" / "prod"
    watched = set(OLD_NAMES) | {n for ns in NEW_NAMES.values() for n in ns}
    for f in FORECAST_PATH:
        tree = ast.parse(Path(root / f).read_text())
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
               {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not used & watched, f
