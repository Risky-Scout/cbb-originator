"""CBB daily Excel exports: Boards, Distributions, Grades (+ Performance Summary). REPORTING ONLY.

    PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.exports daily --date YYYY-MM-DD

writes, for the America/New_York slate date D, into data/prod/exports/D/ (gitignored):

    CBB_Boards_MM_DD_YYYY.xlsx         Full Board | 60 Percent Plus | 65 Percent Plus
    CBB_Distributions_MM_DD_YYYY.xlsx  Distributions
    CBB_Grades_MM_DD_YYYY.xlsx         Graded Bets | Performance Summary

Read-only over evidence that production has ALREADY persisted (no refit, no scoring, no provider / API / GitHub call:
the module imports no network or acquisition code; tests run it with every socket entry point refused):

    ops evidence tree   registry/games.jsonl (the decided PREDICTED row of each game: tick, run, scheduled tip, cutoff
                        rule), ticks/<tick>/side_monitor.jsonl.gz (the hash-chained QUOTE_SIDE receipts: one per
                        prediction x sportsbook offer, prediction-time fields only, written by prod.side_monitor),
                        ticks/*/side_settlements.parquet (settlement against the CBBD final box), ticks/<tick>/
                        projections.parquet (labels)
    durable PMFs        ticks/<tick>/pmfs.parquet (the official prediction's PMFs, prod.pmf_evidence) and
                        forecasts/<D>/<snapshot>/pmfs.parquet (line-independent forecast snapshots, prod.lifecycle);
                        per game the LATEST prediction made before the game's production cutoff is exported. Legacy
                        fallback: runs/<run_id>/market_pmfs.parquet (ephemeral on a GitHub runner)
    names               core.teams of the frozen history store + the current 2026-27 state increment (read-only)

Field sources (no new convention is introduced; every figure is copied from the receipt or uses an existing formula):
    PROJ / MEAN   receipt projection / PMF row projection (official prediction mean)
    MODEL%        p_side_nonpush = P(side) / (1 - P(push)) (prod.pricing: a push refunds the stake)
    MARKET%       the same book's two-sided no-vig probability (1/d_side) / (1/d_over + 1/d_under), the prospective
                  monitor's market-benchmark normalization (prod.prospective_monitor.line_table); blank when the book
                  did not quote both sides of the line
    EV            model_edge = P(win)(d - 1) - P(lose) (prod.pricing.expected_value; computed at prediction time)
    WINDOW        the receipt's prediction-cutoff rule (registry cutoff_rule: TIP_MINUS_1H / ET_GAME_DATE_START)
    CAPTURE_UTC   collector_received_at of the quote (always <= prediction_as_of < scheduled tip)
    PLAYER_ROLE   from predicted_minutes (= the min_mu the official prediction used, recorded in the receipt at
                  prediction time); never realized minutes
    RESULT        WIN / LOSS / PUSH from the side settlement; NO_ACTION (DNP or game not played) -> VOID; no settlement
                  yet -> PENDING; invalid American odds (|odds| < 100, missing, non-finite) or a side other than
                  OVER / UNDER -> UNSUPPORTED
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import math
import os
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC, pmf_evidence as PE

EXPORT_VERSION = "cbb-prod-daily-exports-1.0.0"
TZ = "America/New_York"
EXPORT_ROOT = Path(os.environ.get("CBB_EXPORT_ROOT") or (PC.PROD_ROOT / "exports"))   # <root>/<YYYY-MM-DD>/CBB_*.xlsx
RUNS_ROOT = PC.RUNS_ROOT
PMF_TOL = 1e-9                                   # = prod.pricing.SUM_TOL = prod.simulate.PMF_TOL (production PMF tolerance)
LIVE_LABELS = ("LIVE",)
DECIDED = ("PREDICTED", "EXCLUDED", "MISSED_PREDICTION_CUTOFF", "NOT_SCORED_AT_CUTOFF")   # = shadow_tick.DECIDED (tested)
SIDES = ("OVER", "UNDER")

BOARD_SHEETS = ("Full Board", "60 Percent Plus", "65 Percent Plus")
BOARD_THRESHOLDS = {"Full Board": None, "60 Percent Plus": 0.60, "65 Percent Plus": 0.65}
BOARD_COLS = ("GAME", "TEAM", "PLAYER", "PROP", "LINE", "PROJ", "ΔLINE", "PICK", "MODEL%", "MARKET%", "EDGE", "BOOK",
              "ODDS", "EV", "WINDOW", "CAPTURE_UTC")
DIST_SHEET = "Distributions"            # further sheets "Distributions 2", "Distributions 3", ... (EXCEL_MAX_ROWS)
EXCEL_MAX_ROWS = 1_048_576               # SpreadsheetML worksheet limits (ECMA-376 / Excel): a row or column ref
EXCEL_MAX_COLS = 16_384                  # beyond them is invalid content (Excel "repairs" by deleting the cells)
DIST_COLS = ("GAME", "TEAM", "PLAYER", "PROP", "MEAN", "OUTCOME", "PROBABILITY")
GRADE_SHEETS = ("Graded Bets", "Performance Summary")
GRADE_COLS = ("GAME", "TEAM", "PLAYER", "PROP", "LINE", "PICK", "MODEL%", "MARKET%", "EDGE", "BOOK", "ODDS", "RESULT",
              "REALIZED", "PROFIT")
RESULTS = ("WIN", "LOSS", "PUSH", "VOID", "PENDING", "UNSUPPORTED")
IMMUTABLE_FIELDS = ("GAME", "TEAM", "PLAYER", "PROP", "LINE", "PICK", "MODEL%", "MARKET%", "EDGE", "BOOK", "ODDS")

ROLES = ("CORE_30_PLUS", "ROTATION_20_TO_30", "LIMITED_UNDER_20", "NO_EXPECTED_MINUTES", "UNKNOWN_ROLE")
BANDS = (("<50%", None, 0.50), ("50-55%", 0.50, 0.55), ("55-60%", 0.55, 0.60), ("60-65%", 0.60, 0.65),
         ("65-70%", 0.65, 0.70), ("70%+", 0.70, None))
SOURCE_TEXT = ("Every official pre-tip CBB Originator selection (one best offer per game x player x prop) settled against "
               "authoritative final player outcomes.")
SECTIONS = ("Overall Performance", "Performance by Stat", "Performance by Player Role",
            "Performance by Stat × Player Role", "Performance by Side", "Performance by Model Probability")
OVERALL_COLS = ("NIGHT", "SOURCE", "BETS", "WINS", "LOSSES", "PUSHES", "VOIDS", "PENDING", "UNSUPPORTED", "DECISIONS",
                "HIT_RATE", "PROFIT", "ROI")
GROUP_METRICS = ("BETS", "DECISIONS", "WINS", "LOSSES", "PUSHES", "VOIDS", "HIT_RATE", "PROFIT", "ROI", "AVG_MODEL%",
                 "AVG_MARKET%", "AVG_EDGE")
BAND_COLS = ("MODEL_BAND", "BETS", "DECISIONS", "WINS", "LOSSES", "HIT_RATE", "AVG_MODEL%", "REALIZED_WIN_RATE",
             "CALIBRATION_ERROR", "PROFIT", "ROI")

FORMATS = {"MODEL%": "0.0%", "MARKET%": "0.0%", "EDGE": "0.0%", "HIT_RATE": "0.0%", "ROI": "0.0%",
           "AVG_MODEL%": "0.0%", "AVG_MARKET%": "0.0%", "AVG_EDGE": "0.0%", "REALIZED_WIN_RATE": "0.0%",
           "CALIBRATION_ERROR": "0.0%", "LINE": "0.0", "PROJ": "0.00", "ΔLINE": "0.00", "MEAN": "0.000",
           "PROBABILITY": "0.0000%", "EV": "0.000", "REALIZED": "0.0###", "PROFIT": "0.000", "ODDS": "0",
           "OUTCOME": "0"}


class ExportIntegrityError(RuntimeError):
    """An invariant of the export contract failed (immutable field changed, negative / non-normalized PMF, ...)."""


# ============================================================================ small helpers
def _f(x):
    """Finite float or None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _utc(x) -> pd.Timestamp | None:
    if x is None or (not isinstance(x, (str, pd.Timestamp)) and pd.isna(x)):
        return None
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def slate_date_of(tip) -> str | None:
    t = _utc(tip)
    return None if t is None else str(t.tz_convert(TZ).date())


def file_names(date: str) -> dict:
    d = pd.Timestamp(date)
    tag = f"{d.month:02d}_{d.day:02d}_{d.year:04d}"
    return {"boards": f"CBB_Boards_{tag}.xlsx", "distributions": f"CBB_Distributions_{tag}.xlsx",
            "grades": f"CBB_Grades_{tag}.xlsx"}


def player_role(expected_minutes) -> str:
    """Fixed role from prediction-time expected minutes (never realized minutes)."""
    if isinstance(expected_minutes, bool):
        return "UNKNOWN_ROLE"
    m = _f(expected_minutes)
    if m is None:
        return "UNKNOWN_ROLE"
    if m >= 30:
        return "CORE_30_PLUS"
    if m >= 20:
        return "ROTATION_20_TO_30"
    if m > 0:
        return "LIMITED_UNDER_20"
    return "NO_EXPECTED_MINUTES"


def model_band(p) -> str | None:
    p = _f(p)
    if p is None:
        return None
    for name, lo, hi in BANDS:
        if (lo is None or p >= lo) and (hi is None or p < hi):
            return name
    return None


def valid_american(odds) -> bool:
    o = _f(odds)
    return o is not None and abs(o) >= 100


def profit(result: str, odds):
    """One unit risked per grade row."""
    if result == "WIN":
        o = float(odds)
        return o / 100.0 if o > 0 else 100.0 / abs(o)
    if result == "LOSS":
        return -1.0
    if result in ("PUSH", "VOID"):
        return 0.0
    return None                                   # PENDING / UNSUPPORTED: blank


def grade_result(side, odds, settlement: str | None) -> str:
    if side not in SIDES or not valid_american(odds):
        return "UNSUPPORTED"
    if settlement is None or (not isinstance(settlement, str) and pd.isna(settlement)):
        return "PENDING"
    if settlement in ("WIN", "LOSS", "PUSH"):
        return settlement
    if settlement == "NO_ACTION":
        return "VOID"
    return "UNSUPPORTED"


def no_vig(side: str, d_side, d_other):
    """(1/d_side) / (1/d_side + 1/d_other): prospective_monitor.line_table's same-book normalization."""
    a, b = _f(d_side), _f(d_other)
    if a is None or b is None or a <= 1 or b <= 1:
        return None
    ia, ib = 1.0 / a, 1.0 / b
    return ia / (ia + ib)


# ============================================================================ evidence
def _jsonl(p: Path) -> list[dict]:
    p = Path(p)
    if not p.exists():
        return []
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt") as f:
        return [json.loads(l) for l in f if l.strip()]


def _verify_chain(recs: list[dict], path: Path) -> None:
    """Hash chain of a prod.ledger / prod.side_monitor log (any edit / reorder -> ExportIntegrityError)."""
    prev = "0" * 64
    for n, rec in enumerate(recs, 1):
        body = {k: v for k, v in rec.items() if k != "row_hash"}
        if rec.get("prev_row_hash") != prev or hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest() != rec.get("row_hash"):
            raise ExportIntegrityError(f"{path}: hash chain broken at line {n}")
        prev = rec["row_hash"]


def slate_games(ops_root: Path, date: str) -> pd.DataFrame:
    """Decided PREDICTED registry rows (first decision per game, as shadow_tick.decided) whose scheduled tip falls on
    the America/New_York date `date`."""
    rows = _jsonl(Path(ops_root) / "registry" / "games.jsonl")
    cols = ["game_id", "status", "tick_id", "run_id", "scheduled_tip", "cutoff_rule", "prediction_as_of"]
    if not rows:
        return pd.DataFrame(columns=cols + ["tip_utc", "cutoff_utc"])
    r = pd.DataFrame(rows)
    for c in cols:
        if c not in r:
            r[c] = None
    r = r[r["status"].isin(DECIDED)].drop_duplicates("game_id", keep="first")
    r = r[r["status"] == "PREDICTED"].copy()
    r["slate_date"] = r["scheduled_tip"].map(slate_date_of)
    r = r[r["slate_date"] == date].copy()
    r["tip_utc"] = r["scheduled_tip"].map(_utc)
    tc = r["target_cutoff"] if "target_cutoff" in r else pd.Series([None] * len(r), index=r.index)
    r["cutoff_utc"] = [_utc(c) if _utc(c) is not None else (None if t is None else t - pd.Timedelta(hours=1))
                       for c, t in zip(tc, r["tip_utc"])]     # registry target_cutoff, else TIP_MINUS_1H
    return r[cols + ["tip_utc", "cutoff_utc"]].reset_index(drop=True)


def receipts(ops_root: Path, games: pd.DataFrame, labels=LIVE_LABELS) -> pd.DataFrame:
    """QUOTE_SIDE receipts (prediction x sportsbook offer) of the decided prediction tick of each slate game."""
    out = []
    for g in games.itertuples(index=False):
        tdir = Path(ops_root) / "ticks" / str(g.tick_id)
        src = tdir / "side_monitor.jsonl.gz"
        if not src.exists() and (tdir / "side_monitor.jsonl").exists():
            src = tdir / "side_monitor.jsonl"
        if src.exists():
            recs = _jsonl(src)
            _verify_chain(recs, src)
            body = [r["body"] for r in recs if r.get("record_type") == "QUOTE_SIDE"]
            df = pd.DataFrame(body)
        elif (tdir / "quote_sides.parquet").exists():
            df = pd.read_parquet(tdir / "quote_sides.parquet")
        else:
            continue
        if not len(df):
            continue
        df = df[(df["game_id"] == g.game_id) & (df["run_id"] == g.run_id) & df["data_label"].isin(labels)].copy()
        df["tip_utc"] = g.tip_utc
        df["window"] = g.cutoff_rule
        df["tick_id"] = g.tick_id
        out.append(df)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True).drop_duplicates("record_id", keep="first").reset_index(drop=True)


REFRESH_RECEIPTS = "reprice_side_monitor.jsonl"        # = prod.quote_refresh.REFRESH_FILE (tested)


def refresh_receipts(ops_root: Path, date: str, labels=LIVE_LABELS) -> pd.DataFrame:
    """QUOTE_SIDE receipts of the tiered quote refresh (prod.quote_refresh) for ET slate date `date`: the persisted PMF
    of the game repriced at a later real quote (no refit, no simulation). Each carries its own tip and cutoff."""
    src = Path(ops_root) / "quotes" / date / REFRESH_RECEIPTS
    recs = _jsonl(src)
    if not recs:
        return pd.DataFrame()
    _verify_chain(recs, src)
    df = pd.DataFrame([r["body"] for r in recs if r.get("record_type") == "QUOTE_SIDE"])
    if not len(df):
        return df
    df = df[df["data_label"].isin(labels)].copy()
    df["tip_utc"] = df["tip_utc"].map(_utc)
    df["tick_id"] = None
    return df.drop_duplicates("record_id", keep="first").reset_index(drop=True)


def side_settlements(ops_root: Path) -> pd.DataFrame:
    parts = [pd.read_parquet(p) for p in sorted(Path(ops_root, "ticks").glob("*/side_settlements.parquet"))]
    if not parts:
        return pd.DataFrame(columns=["record_id", "result", "actual"])
    s = pd.concat(parts, ignore_index=True)
    return s.drop_duplicates("record_id", keep="first")          # append-only: a record is settled once


def team_names(history_db: Path | None, state_root: Path | None) -> dict:
    """canonical_team_id -> display_name: frozen history core.teams + the 2026-27 increment (read-only)."""
    import duckdb
    names: dict = {}
    dbs = []
    if state_root is not None:
        try:
            from cbb_platform.prod import state as ST
            st = ST.current(Path(state_root))
            if st and Path(st["increment_db"]).exists():
                dbs.append(Path(st["increment_db"]))
        except Exception:                                         # noqa: BLE001 -- names only; fall back to ids
            pass
    if history_db is not None and Path(history_db).exists():
        dbs.append(Path(history_db))
    for db in reversed(dbs):                                      # history first, increment adds new teams
        try:
            con = duckdb.connect(str(db), read_only=True)
            has = con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name='core' AND table_name='teams'").fetchone()[0]
            if has:
                for tid, dn in con.execute("SELECT canonical_team_id, any_value(display_name) FROM core.teams GROUP BY 1").fetchall():
                    if dn is not None:
                        names.setdefault(tid, dn)
            con.close()
        except Exception:                                         # noqa: BLE001
            pass
    return names


# ============================================================================ offers (Boards + Grades population)
def offers(rc: pd.DataFrame, settlements: pd.DataFrame, names: dict) -> tuple[pd.DataFrame, dict]:
    """One row per pre-tip priced receipt (prediction x sportsbook offer side). Returns (frame, leakage report)."""
    cols = ["record_id", "tip_utc", "game", "team", "player", "prop", "line", "proj", "dline", "pick", "model_p",
            "market_p", "edge", "book", "odds", "ev", "window", "capture_utc", "expected_minutes", "role", "result",
            "realized", "profit", "game_id", "player_id", "team_id", "model_version", "prediction_as_of", "run_id",
            "tick_id"]
    leak = {"receipts": int(len(rc)), "post_tip_excluded": 0}
    if not len(rc):
        return pd.DataFrame(columns=cols), leak
    rc = rc.copy()
    tip = rc["tip_utc"].map(_utc)
    aso = rc["prediction_as_of"].map(_utc)
    cap = rc["collector_received_at"].map(_utc)
    qav = rc["quote_available_at"].map(_utc)
    ok = [t is not None and a is not None and a < t and (c is None or c < t) and (q is None or q < t)
          for t, a, c, q in zip(tip, aso, cap, qav)]
    leak["post_tip_excluded"] = int(len(rc) - sum(ok))
    rc = rc[ok].copy()
    # same-book other side at the same line (two-sided no-vig)
    key = ["run_id", "game_id", "player_id", "market", "line", "sportsbook"]
    other = rc.set_index(key + ["side"])["book_decimal_odds"].to_dict()
    st = settlements.set_index("record_id") if len(settlements) else pd.DataFrame()
    rows = []
    for r in rc.itertuples(index=False):
        k = tuple(getattr(r, c) for c in key)
        opp = "UNDER" if r.side == "OVER" else "OVER"
        mp = _f(r.p_side_nonpush)
        mk = no_vig(r.side, r.book_decimal_odds, other.get(k + (opp,))) if r.side in SIDES else None
        s = st.loc[r.record_id] if len(st) and r.record_id in st.index else None
        res = grade_result(r.side, r.book_american_odds, None if s is None else s.get("result"))
        realized = None if s is None else _f(s.get("actual"))
        odds = _f(r.book_american_odds)
        proj, line = _f(r.projection), _f(r.line)
        rows.append({"record_id": r.record_id, "tip_utc": _utc(r.tip_utc), "game": r.game,
                     "team": names.get(r.team_id, r.team_id), "player": r.player, "prop": r.market, "line": line,
                     "proj": proj, "dline": None if proj is None or line is None else proj - line, "pick": r.side,
                     "model_p": mp, "market_p": mk, "edge": None if mp is None or mk is None else mp - mk,
                     "book": r.sportsbook, "odds": None if odds is None else int(round(odds)), "ev": _f(r.model_edge),
                     "window": r.window, "capture_utc": None if _utc(r.collector_received_at) is None
                     else _utc(r.collector_received_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "expected_minutes": _f(r.predicted_minutes), "role": player_role(r.predicted_minutes),
                     "result": res, "realized": realized,
                     "profit": profit(res, odds) if res not in ("PENDING", "UNSUPPORTED") else None,
                     "game_id": r.game_id, "player_id": r.player_id, "team_id": r.team_id,
                     "model_version": getattr(r, "model_version", None),
                     "prediction_as_of": None if _utc(r.prediction_as_of) is None else _utc(r.prediction_as_of).isoformat(),
                     "run_id": r.run_id, "tick_id": getattr(r, "tick_id", None)})
    df = pd.DataFrame(rows, columns=cols)
    df = df.sort_values(["tip_utc", "game", "player", "prop", "line", "pick", "book"], kind="mergesort",
                        na_position="last").reset_index(drop=True)
    return df, leak


# ============================================================================ official best-offer selection
OFFICIAL_KEY = ("game_id", "player_id", "prop")              # Board / Grade grain: GAME x PLAYER x PROP (BOOK, LINE not)
SELECTION_VERSION = "cbb-best-offer-selection-2.0.0"
PROVISIONAL, FROZEN = "PROVISIONAL", "OFFICIAL_FROZEN"
TIE_ORDER = ("MAX_EV", "MODEL%", "BETTOR_FAVORABLE_LINE_WHEN_SAME_SIDE", "DECIMAL_ODDS", "EDGE", "OVER_BEFORE_UNDER",
             "BOOK_ALPHA")
CONTENT_FIELDS = ("selection_version", "record_id", "game_id", "player_id", "team_id", "stat", "pick", "line", "book",
                  "odds", "model_p", "market_p", "edge", "ev", "model_version", "expected_minutes", "role",
                  "forecast_snapshot_id", "prediction_as_of", "capture_utc", "window", "cutoff_utc", "tick_id",
                  "run_id", "game", "team", "player", "proj", "dline", "candidate_offers")


def decimal_odds(american) -> float | None:
    """Exact bettor-facing decimal payout of American odds (None when not a valid American price)."""
    if not valid_american(american):
        return None
    o = float(american)
    return 1.0 + o / 100.0 if o > 0 else 1.0 + 100.0 / abs(o)


def norm_book(b) -> str:
    return "" if b is None or (not isinstance(b, str) and pd.isna(b)) else str(b).strip().lower()


def eligible_offers(of: pd.DataFrame, cutoffs: dict | None = None, at=None) -> pd.DataFrame:
    """Valid executable offers: OVER / UNDER, valid American odds, finite MODEL% / EV / LINE, a quote timestamp, quote
    and prediction strictly before the game's cutoff (and the tip, enforced in offers()) and not after `at`."""
    if not len(of):
        return of.iloc[0:0]
    cutoffs = cutoffs or {}
    at = _utc(at)

    def timely(r):
        cap, aso, cut = _utc(r["capture_utc"]), _utc(r["prediction_as_of"]), _utc(cutoffs.get(r["game_id"]))
        if cap is None:
            return False
        if cut is not None and (cap >= cut or (aso is not None and aso > cut)):
            return False
        return at is None or cap <= at
    ok = of["pick"].isin(SIDES) & of["odds"].map(valid_american) & of["model_p"].map(lambda v: _f(v) is not None) & \
        of["ev"].map(lambda v: _f(v) is not None) & of["line"].map(lambda v: _f(v) is not None) & \
        of.apply(timely, axis=1)
    return latest_observation(of[ok])


def latest_observation(el: pd.DataFrame) -> pd.DataFrame:
    """Per game, only the offers of its LATEST quote observation (the receipts of one pricing run: the official tick
    or one tiered quote refresh): an earlier observation superseded by a newer quote of the same game is no longer an
    executable offer. Latest = max capture instant, then max prediction_as_of, then run_id. One observation per game
    (the official-tick-only case) is unchanged."""
    if not len(el):
        return el
    k = el.assign(_cap=el["capture_utc"].map(_utc), _aso=el["prediction_as_of"].map(_utc))
    b = k.groupby(["game_id", "run_id"], sort=True).agg(_cap=("_cap", "max"), _aso=("_aso", "max")).reset_index()
    b = b.sort_values(["game_id", "_cap", "_aso", "run_id"], kind="mergesort").drop_duplicates("game_id", keep="last")
    keep = set(zip(b["game_id"], b["run_id"]))
    return el[[(g, r) in keep for g, r in zip(el["game_id"], el["run_id"])]]


def select_best(g: pd.DataFrame) -> pd.Series:
    """The single official offer of one GAME x PLAYER x PROP: MAXIMUM model EV across every book, line and side.
    EXACT EV comparison (no rounding, no tolerance); exact ties only are broken, in this order, by: higher MODEL%; the bettor-favorable line when every
    remaining candidate has the same PICK (OVER lower, UNDER higher; skipped when PICKs differ); higher decimal odds;
    higher EDGE; OVER before UNDER; normalized BOOK ascending. Sequential filtering: input-order independent."""
    c = g.assign(_ev=g["ev"].map(float), _mp=g["model_p"].map(float),
                 _dec=g["odds"].map(decimal_odds), _edge=g["edge"].map(lambda v: -math.inf if _f(v) is None else float(v)),
                 _book=g["book"].map(norm_book))
    c = c[c["_ev"] == c["_ev"].max()]
    c = c[c["_mp"] == c["_mp"].max()]
    if c["pick"].nunique() == 1 and len(c) > 1:
        best = c["line"].min() if c["pick"].iloc[0] == "OVER" else c["line"].max()
        c = c[c["line"] == best]
    c = c[c["_dec"] == c["_dec"].max()]
    c = c[c["_edge"] == c["_edge"].max()]
    if (c["pick"] == "OVER").any():
        c = c[c["pick"] == "OVER"]
    c = c[c["_book"] == c["_book"].min()]
    c = c.sort_values(["line", "record_id"], kind="mergesort")      # only identical offers remain: stable identity
    return c.iloc[0].drop(["_ev", "_mp", "_dec", "_edge", "_book"])


def select_official(of: pd.DataFrame, cutoffs: dict | None = None, at=None) -> pd.DataFrame:
    """Exactly one selected row per GAME x PLAYER x PROP that has at least one valid executable offer."""
    el = eligible_offers(of, cutoffs, at)
    if not len(el):
        return of.iloc[0:0].assign(candidate_offers=pd.Series(dtype="int64"))
    rows = []
    for _, g in el.groupby(list(OFFICIAL_KEY), sort=True):
        r = select_best(g)
        r["candidate_offers"] = int(len(g))
        rows.append(r)
    out = pd.DataFrame(rows).reset_index(drop=True)
    assert_unique(out)
    return out.sort_values(["tip_utc", "game", "player", "prop"], kind="mergesort", na_position="last").reset_index(drop=True)


def assert_unique(df: pd.DataFrame, cols=OFFICIAL_KEY) -> None:
    if len(df) and int(df.duplicated(list(cols)).sum()):
        raise ExportIntegrityError(f"duplicate GAME x PLAYER x PROP rows: {int(df.duplicated(list(cols)).sum())}")


def selections_path(ops_root: Path, date: str) -> Path:
    return Path(ops_root) / "selections" / date / "official_selections.jsonl"


def _hash(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def _key(r: dict) -> tuple:
    return r["game_id"], r["player_id"], r["stat"]


def load_selections(path: Path) -> dict:
    """key -> {"provisional": latest PROVISIONAL receipt | None, "frozen": OFFICIAL_FROZEN receipt | None}. Every
    receipt's content and receipt hashes are verified; any receipt after a key's frozen receipt fails."""
    out: dict = {}
    for r in _jsonl(path):
        body = {f: r.get(f) for f in CONTENT_FIELDS}
        full = {k: v for k, v in r.items() if k != "receipt_hash"}
        if _hash(body) != r.get("content_hash") or _hash(full) != r.get("receipt_hash"):
            raise ExportIntegrityError(f"{path}: selection receipt {_key(r)} altered")
        st = out.setdefault(_key(r), {"provisional": None, "frozen": None})
        if st["frozen"] is not None:
            raise ExportIntegrityError(f"{path}: receipt written after the frozen selection of {_key(r)}")
        st["frozen" if r["state"] == FROZEN else "provisional"] = r
    return out


def selection_receipt(r: dict, state: str, at: str, cutoff) -> dict:
    def v(x):
        return None if x is None or (not isinstance(x, str) and pd.isna(x)) else x
    body = {"selection_version": SELECTION_VERSION, "record_id": r["record_id"], "game_id": r["game_id"],
            "player_id": r["player_id"], "team_id": v(r["team_id"]), "stat": r["prop"], "pick": r["pick"],
            "line": float(r["line"]), "book": r["book"], "odds": int(r["odds"]), "model_p": _f(r["model_p"]),
            "market_p": _f(r["market_p"]), "edge": _f(r["edge"]), "ev": _f(r["ev"]),
            "model_version": v(r["model_version"]), "expected_minutes": _f(r["expected_minutes"]), "role": r["role"],
            "forecast_snapshot_id": v(r["run_id"]), "prediction_as_of": v(r["prediction_as_of"]),
            "capture_utc": v(r["capture_utc"]), "window": v(r["window"]),
            "cutoff_utc": None if _utc(cutoff) is None else _utc(cutoff).isoformat(), "tick_id": v(r["tick_id"]),
            "run_id": v(r["run_id"]), "game": v(r["game"]), "team": v(r["team"]), "player": v(r["player"]),
            "proj": _f(r["proj"]), "dline": _f(r["dline"]), "candidate_offers": int(r["candidate_offers"])}
    body = json.loads(json.dumps(body, default=str))
    rec = {**body, "content_hash": _hash(body), "state": state, "selected_at": at}
    return {**rec, "receipt_hash": _hash(rec)}


def write_receipt(path: Path, rec: dict, have: dict) -> bool:
    """Write-once per state. Same content again: idempotent (False, nothing written). A frozen key with different
    content: ExportIntegrityError. A changed provisional selection before the cutoff: a new PROVISIONAL receipt."""
    st = have.get(_key(rec), {"provisional": None, "frozen": None})
    if st["frozen"] is not None:
        if st["frozen"]["content_hash"] != rec["content_hash"]:
            raise ExportIntegrityError(f"frozen selection of {_key(rec)} may not change")
        return False
    if rec["state"] == PROVISIONAL and st["provisional"] is not None and \
            st["provisional"]["content_hash"] == rec["content_hash"]:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    st[("frozen" if rec["state"] == FROZEN else "provisional")] = rec
    have[_key(rec)] = st
    return True


def official(of: pd.DataFrame, path: Path, cutoffs: dict | None = None, now=None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(board rows, graded rows, report). Per GAME x PLAYER x PROP:
      frozen receipt exists        -> it is used as written; never reselected (a later / better quote cannot change it)
      no frozen, now >= cutoff     -> the selection over the offers valid before the cutoff is frozen (OFFICIAL_FROZEN)
      no frozen, now <  cutoff     -> the current best offer is a PROVISIONAL receipt (superseded by a later refresh)
    Board rows: frozen + provisional; graded rows: OFFICIAL_FROZEN only."""
    path, cutoffs = Path(path), cutoffs or {}
    now = _utc(now) or pd.Timestamp.now(tz="UTC")
    have = load_selections(path)
    sel = select_official(of, cutoffs, now)
    written = {PROVISIONAL: 0, FROZEN: 0}
    for r in sel.to_dict(orient="records"):
        k = (r["game_id"], r["player_id"], r["prop"])
        if have.get(k, {}).get("frozen") is not None:
            continue
        cut = _utc(cutoffs.get(r["game_id"]))
        state = FROZEN if cut is not None and now >= cut else PROVISIONAL
        rec = selection_receipt(r, state, now.isoformat(), cut)
        written[state] += write_receipt(path, rec, have)
    for k, st in have.items():                                    # a provisional key whose cutoff passed: freeze it
        cut = _utc(cutoffs.get(k[0]))
        if st["frozen"] is None and st["provisional"] is not None and cut is not None and now >= cut:
            p = st["provisional"]
            rec = {**{f: p[f] for f in CONTENT_FIELDS}, "content_hash": p["content_hash"], "state": FROZEN,
                   "selected_at": now.isoformat()}
            written[FROZEN] += write_receipt(path, {**rec, "receipt_hash": _hash(rec)}, have)
    byid = of.set_index("record_id") if len(of) else pd.DataFrame()

    def row(r):
        if not len(byid) or r["record_id"] not in byid.index:
            raise ExportIntegrityError(f"selected receipt {r['record_id']} absent from the QUOTE_SIDE evidence")
        o = byid.loc[r["record_id"]]
        if (o["pick"], o["book"]) != (r["pick"], r["book"]) or _f(o["line"]) != _f(r["line"]) or _f(o["odds"]) != _f(r["odds"]):
            raise ExportIntegrityError(f"selection {r['content_hash'][:12]} differs from its QUOTE_SIDE receipt")
        return {"record_id": r["record_id"], "tip_utc": o["tip_utc"], "game": r["game"], "team": r["team"],
                "player": r["player"], "prop": r["stat"], "line": r["line"], "proj": r["proj"], "dline": r["dline"],
                "pick": r["pick"], "model_p": r["model_p"], "market_p": r["market_p"], "edge": r["edge"],
                "book": r["book"], "odds": r["odds"], "ev": r["ev"], "window": r["window"],
                "capture_utc": r["capture_utc"], "expected_minutes": r["expected_minutes"], "role": r["role"],
                "result": o["result"], "realized": o["realized"], "profit": o["profit"], "game_id": r["game_id"],
                "player_id": r["player_id"], "team_id": r["team_id"], "model_version": r["model_version"],
                "prediction_as_of": r["prediction_as_of"], "run_id": r["run_id"], "tick_id": r["tick_id"]}

    def frame(recs):
        out = pd.DataFrame([row(r) for r in recs], columns=list(of.columns))
        assert_unique(out)
        return out.sort_values(["tip_utc", "game", "player", "prop"], kind="mergesort",
                               na_position="last").reset_index(drop=True)
    frozen = [st["frozen"] for st in have.values() if st["frozen"] is not None]
    board = [st["frozen"] or st["provisional"] for st in have.values() if st["frozen"] or st["provisional"]]
    return frame(board), frame(frozen), {
        "path": str(path), "raw_offers": int(len(of)), "eligible_offers": int(len(eligible_offers(of, cutoffs, now))),
        "written": written, "frozen": len(frozen), "provisional": len(board) - len(frozen), "tie_order": list(TIE_ORDER)}


def board_frames(of: pd.DataFrame) -> dict:
    """Full Board = the official (deduplicated) rows; the threshold sheets are filters of Full Board only."""
    assert_unique(of)
    out = {}
    for sheet, thr in BOARD_THRESHOLDS.items():
        x = of if thr is None else of[of["model_p"].map(lambda p: p is not None and not pd.isna(p) and p >= thr)]
        out[sheet] = [[r.game, r.team, r.player, r.prop, r.line, r.proj, r.dline, r.pick, r.model_p, r.market_p, r.edge,
                       r.book, r.odds, r.ev, r.window, r.capture_utc] for r in x.itertuples(index=False)]
    return out


def grade_rows(of: pd.DataFrame) -> list[list]:
    return [[r.game, r.team, r.player, r.prop, r.line, r.pick, r.model_p, r.market_p, r.edge, r.book, r.odds, r.result,
             r.realized, r.profit] for r in of.itertuples(index=False)]


# ============================================================================ performance summary
def _mean(v):
    x = [float(a) for a in v if _f(a) is not None]
    return sum(x) / len(x) if x else None


def group_metrics(g: pd.DataFrame) -> dict:
    res = g["result"].tolist()
    w, l = res.count("WIN"), res.count("LOSS")
    dec = w + l
    pr = sum(float(p) for p in g["profit"] if _f(p) is not None)
    return {"BETS": int(len(g)), "DECISIONS": dec, "WINS": w, "LOSSES": l, "PUSHES": res.count("PUSH"),
            "VOIDS": res.count("VOID"), "PENDING": res.count("PENDING"), "UNSUPPORTED": res.count("UNSUPPORTED"),
            "HIT_RATE": w / dec if dec else None, "PROFIT": pr, "ROI": pr / dec if dec else None,
            "AVG_MODEL%": _mean(g["model_p"]), "AVG_MARKET%": _mean(g["market_p"]), "AVG_EDGE": _mean(g["edge"])}


def summary(of: pd.DataFrame, night: str) -> dict:
    """The six Performance Summary tables: {section: (columns, rows)}."""
    m = group_metrics(of)
    out = {"Overall Performance": (OVERALL_COLS, [[night, SOURCE_TEXT] + [m[c] for c in OVERALL_COLS[2:]]])}
    stat_rows = [[s] + [group_metrics(g)[c] for c in GROUP_METRICS] for s, g in sorted(of.groupby("prop"), key=lambda x: x[0])]
    out["Performance by Stat"] = (("STAT_CATEGORY",) + GROUP_METRICS, stat_rows + [["TOTAL"] + [m[c] for c in GROUP_METRICS]])
    role_rows = [[r] + [group_metrics(of[of["role"] == r])[c] for c in GROUP_METRICS] for r in ROLES]
    out["Performance by Player Role"] = (("PLAYER_ROLE",) + GROUP_METRICS, role_rows + [["TOTAL"] + [m[c] for c in GROUP_METRICS]])
    sr = []
    for s in sorted(of["prop"].unique()) if len(of) else []:
        for r in ROLES:
            g = of[(of["prop"] == s) & (of["role"] == r)]
            if len(g):
                sr.append([s, r] + [group_metrics(g)[c] for c in GROUP_METRICS])
    out["Performance by Stat × Player Role"] = (("STAT_CATEGORY", "PLAYER_ROLE") + GROUP_METRICS, sr)
    side_rows = [[s] + [group_metrics(of[of["pick"] == s])[c] for c in GROUP_METRICS] for s in SIDES]
    out["Performance by Side"] = (("SIDE",) + GROUP_METRICS, side_rows + [["TOTAL"] + [m[c] for c in GROUP_METRICS]])
    band = of["model_p"].map(model_band) if len(of) else pd.Series(dtype=object)
    br = []
    for name, _, _ in BANDS:
        g = group_metrics(of[band == name]) if len(of) else group_metrics(of)
        rw = g["WINS"] / g["DECISIONS"] if g["DECISIONS"] else None
        br.append([name, g["BETS"], g["DECISIONS"], g["WINS"], g["LOSSES"], g["HIT_RATE"], g["AVG_MODEL%"], rw,
                   None if rw is None or g["AVG_MODEL%"] is None else rw - g["AVG_MODEL%"], g["PROFIT"], g["ROI"]])
    out["Performance by Model Probability"] = (BAND_COLS, br)
    return out


def reconcile(of: pd.DataFrame, summ: dict) -> dict:
    """Every TOTAL / partition reconciles exactly to Overall Performance."""
    ov = dict(zip(OVERALL_COLS, summ["Overall Performance"][1][0]))
    chk = {}
    for sec, key in (("Performance by Stat", "STAT_CATEGORY"), ("Performance by Player Role", "PLAYER_ROLE"),
                     ("Performance by Side", "SIDE")):
        cols, rows = summ[sec]
        body = [dict(zip(cols, r)) for r in rows if r[0] != "TOTAL"]
        tot = dict(zip(cols, [r for r in rows if r[0] == "TOTAL"][0]))
        chk[sec] = all(tot[c] == ov[c] for c in ("BETS", "DECISIONS", "WINS", "LOSSES", "PUSHES", "VOIDS")) and \
            all(sum(b[c] for b in body) == ov[c] for c in ("BETS", "DECISIONS", "WINS", "LOSSES", "PUSHES", "VOIDS")) and \
            abs(sum(b["PROFIT"] for b in body) - ov["PROFIT"]) <= 1e-9 and abs(tot["PROFIT"] - ov["PROFIT"]) <= 1e-9
    cols, rows = summ["Performance by Stat × Player Role"]
    body = [dict(zip(cols, r)) for r in rows]
    chk["Performance by Stat × Player Role"] = sum(b["BETS"] for b in body) == ov["BETS"] and \
        sum(b["DECISIONS"] for b in body) == ov["DECISIONS"]
    cols, rows = summ["Performance by Model Probability"]
    body = [dict(zip(cols, r)) for r in rows]
    nomodel = int(of["model_p"].map(lambda p: _f(p) is None).sum()) if len(of) else 0
    chk["Performance by Model Probability"] = sum(b["BETS"] for b in body) + nomodel == ov["BETS"]
    chk["Overall Performance"] = ov["BETS"] == len(of) and ov["DECISIONS"] == ov["WINS"] + ov["LOSSES"] and \
        sum(ov[c] for c in ("WINS", "LOSSES", "PUSHES", "VOIDS", "PENDING", "UNSUPPORTED")) == ov["BETS"]
    return chk


# ============================================================================ distributions
def _check_pmf(pmf: np.ndarray, what: str) -> None:
    if not np.isfinite(pmf).all() or (pmf < 0).any():
        raise ExportIntegrityError(f"negative / non-finite PMF {what}")
    if abs(pmf.sum() - 1.0) > PMF_TOL:
        raise ExportIntegrityError(f"PMF mass {pmf.sum()} {what}")


def _dists_of(path: Path, gid: str, cache: dict | None) -> list[dict]:
    """PE.distributions of one durable PMF file restricted to one game. cache (one export run): path -> {game_id: [...]}
    so each evidence file is read and parsed once, not once per game it covers (same rows, same order)."""
    if cache is None:
        return [x for x in PE.distributions(PE.read(path)) if x["game_id"] == gid]
    k = str(path)
    if k not in cache:
        by: dict = {}
        for x in PE.distributions(PE.read(path)):
            by.setdefault(x["game_id"], []).append(x)
        cache[k] = by
    return list(cache[k].get(gid, []))


def _official_pmfs(ops_root: Path, runs_root: Path, g, cache: dict | None = None) -> tuple[pd.DataFrame | None, str | None, list]:
    """The PMFs of one official (shadow-tick) prediction: the durable tick evidence ticks/<tick>/pmfs.parquet
    (prod.pmf_evidence), else the legacy run-directory market_pmfs.parquet. Returns (frame with game_label / player_label
    / team_id / stat / mean / pmf, source path, paths looked in)."""
    tick = Path(ops_root) / "ticks" / str(g.tick_id)
    durable = tick / PE.FILE
    cand = [durable, tick / "market_pmfs.parquet", Path(runs_root) / str(g.run_id) / "market_pmfs.parquet"]
    if durable.exists():
        return pd.DataFrame(_dists_of(durable, g.game_id, cache)), str(durable), cand
    src = next((p for p in cand[1:] if p.exists()), None)
    if src is None:
        return None, None, cand
    mp = pd.read_parquet(src)
    mp = mp[mp["game_id"] == g.game_id]
    lab = tick / "projections.parquet"
    lb = pd.read_parquet(lab).drop_duplicates(["game_id", "player_id"]).set_index(["game_id", "player_id"]) \
        if lab.exists() else pd.DataFrame()
    rows = []
    for r in mp.itertuples(index=False):
        k = (g.game_id, r.player_id)
        rows.append({"game_id": g.game_id, "player_id": r.player_id, "team_id": r.team_id, "stat": r.market,
                     "mean": _f(r.projection), "pmf": np.asarray(r.pmf, dtype=float),
                     "game_label": lb.loc[k, "game"] if len(lb) and k in lb.index else g.game_id,
                     "player_label": lb.loc[k, "player"] if len(lb) and k in lb.index else r.player_id})
    return pd.DataFrame(rows), str(src), cand


def forecast_sources(ops_root: Path, date: str) -> dict:
    """game_id -> [(prediction_as_of, cutoff, pmfs path)] of every line-independent forecast snapshot of ET date
    `date` (prod.lifecycle) that forecast the game BEFORE its production cutoff."""
    out: dict = {}
    for r in _jsonl(Path(ops_root) / "forecasts" / "index.jsonl"):
        if r.get("slate_date") != date or r.get("status") != "FORECAST" or not r.get("prediction_as_of"):
            continue
        p = Path(ops_root) / "forecasts" / date / r["snapshot_id"] / PE.FILE
        if not p.exists():
            continue
        aso = _utc(r["prediction_as_of"])
        for gid in r.get("games") or []:
            cut = _utc((r.get("cutoffs") or {}).get(gid))
            if cut is not None and aso <= cut:                    # at or before the cutoff (directive §5)
                out.setdefault(gid, []).append((aso, cut, p))
    return out


def distribution_rows(ops_root: Path, runs_root: Path, games: pd.DataFrame, names: dict,
                      date: str | None = None, audit: dict | None = None) -> tuple[list, list]:
    """(rows, missing): the complete PMFs of every eligible (game, player, stat) prediction of the slate. Per game the
    LATEST valid pre-cutoff prediction is used (directive §8): the official shadow-tick prediction when it exists (it
    is made inside [cutoff - lead, cutoff)), else the latest forecast snapshot made before the game's cutoff. PMFs are
    read from the durable evidence exactly as persisted (never recomputed). games: the PREDICTED registry rows."""
    rows, missing, recs = [], [], []
    cache: dict = {}
    fsrc = forecast_sources(ops_root, date) if date else {}
    official = {g.game_id: g for g in games.itertuples(index=False)}
    order = {g.game_id: g.tip_utc for g in games.itertuples(index=False)}
    for gid in sorted(set(official) | set(fsrc)):
        best = None
        if gid in official:
            g = official[gid]
            df, src, looked = _official_pmfs(ops_root, runs_root, g, cache)
            aso = _utc(getattr(g, "prediction_as_of", None))
            cut = _utc(getattr(g, "cutoff_utc", None))
            if df is not None and aso is not None and cut is not None and aso > cut:
                df = None                                         # post-cutoff official evidence is never used
                if gid not in fsrc:
                    missing.append({"game_id": gid, "run_id": g.run_id, "looked_in": ["post-cutoff prediction"]})
                    continue
            if df is not None:
                best = (aso or pd.Timestamp.max.tz_localize("UTC"), df, src)
            elif gid not in fsrc:
                missing.append({"game_id": gid, "run_id": g.run_id, "looked_in": [str(p) for p in looked]})
                continue
        for aso, cut, p in fsrc.get(gid, []):
            if best is None or (aso > best[0] and aso <= cut):
                best = (aso, pd.DataFrame(_dists_of(p, gid, cache)), str(p))
        if best is None or not len(best[1]):
            continue
        df = best[1]
        tip = order.get(gid)
        if tip is None:
            tip = min((c for _, c, _ in fsrc.get(gid, [])), default=None)
        for r in df.itertuples(index=False):
            pmf = np.asarray(r.pmf, dtype=float)
            _check_pmf(pmf, f"{gid} {r.player_id} {r.stat}")
            nz = np.flatnonzero(pmf)
            last = int(nz[-1]) if len(nz) else 0                 # trailing zeros only; the folded tail point stays
            recs.append((tip, r.game_label, r.player_label, r.stat, names.get(r.team_id, r.team_id), _f(r.mean),
                         pmf[: last + 1], (gid, r.player_id, r.stat)))
    if audit is not None:                                        # identity keys are canonical ids, never display labels
        canon: dict = {}
        label: dict = {}
        for x in recs:
            canon[x[7]] = canon.get(x[7], 0) + 1
            label.setdefault((x[1], x[2], x[3]), set()).add(x[7])
        audit["canonical_duplicate_keys"] = sorted(list(k) for k, n in canon.items() if n > 1)
        audit["display_label_collisions"] = sorted([list(k), sorted(list(x) for x in v)] for k, v in label.items() if len(v) > 1)
    recs.sort(key=lambda x: (x[0] or pd.Timestamp.max.tz_localize("UTC"), x[1], x[2], x[3]))
    for _, game, player, market, team, mean, pmf, _ in recs:
        rows += [[game, team, player, market, mean, int(o), float(p)] for o, p in enumerate(pmf)]
    return rows, missing


def pmf_mass_check(rows: list) -> dict:
    """Mass of every exported distribution. A distribution is the run of rows opened by OUTCOME 0 (distribution_rows
    writes each canonical (game, player_id, stat) PMF as outcomes 0..k): two players sharing a display name are two
    distributions, never one summed key."""
    sums: list = []
    neg = 0
    for _, _, _, _, _, outcome, p in rows:
        if outcome == 0 or not sums:
            sums.append(0.0)
        sums[-1] += p
        neg += p < 0
    worst = max((abs(v - 1) for v in sums), default=0.0)
    return {"distributions": len(sums), "max_abs_mass_error": worst, "negative": int(neg),
            "PMF_RECONCILIATION": "PASS" if worst <= PMF_TOL and neg == 0 else "FAIL"}


DIST_RECEIPT = "distributions_receipt.json"


def _rows_sha(rows: list) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode())
        h.update(b"\n")
    return h.hexdigest()


def _writer_sha() -> str:
    """The workbook bytes are a pure function of the rows and this module (write_xlsx is deterministic)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def distributions_if_changed(rows: list, path: Path) -> tuple[Path, bool]:
    """Write the Distributions workbook unless the existing file was written from exactly these rows by this writer
    (its receipt records rows / writer / file sha256 and the file still hashes to it). Returns (path, written)."""
    path = Path(path)
    rec_p = path.with_name(DIST_RECEIPT)
    want = {"file": path.name, "rows_sha256": _rows_sha(rows), "writer_sha256": _writer_sha(), "rows": len(rows)}
    try:
        rec = json.loads(rec_p.read_text())
    except (OSError, ValueError):
        rec = None
    if rec and all(rec.get(k) == v for k, v in want.items()) and path.exists() and _file_sha(path) == rec.get("xlsx_sha256"):
        return path, False
    p = distributions_workbook(rows, path)
    tmp = rec_p.with_name(rec_p.name + ".tmp")
    tmp.write_text(json.dumps({**want, "xlsx_sha256": _file_sha(p)}, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, rec_p)
    return p, True


# ============================================================================ minimal deterministic XLSX (stdlib)
def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


class _Styles:
    BUILTIN = {"General": 0, "0": 1}

    def __init__(self):
        self.fmts: dict = {}
        self.xfs = [(0, 0)]                                       # (font, numFmtId); 0 = default

    def xf(self, fmt: str | None, bold: bool = False) -> int:
        fmt = fmt or "General"
        fid = self.BUILTIN.get(fmt)
        if fid is None:
            fid = self.fmts.setdefault(fmt, 164 + len(self.fmts))
        key = (1 if bold else 0, fid)
        if key not in self.xfs:
            self.xfs.append(key)
        return self.xfs.index(key)

    def xml(self) -> str:
        nf = "".join(f'<numFmt numFmtId="{i}" formatCode="{escape(f, {chr(34): "&quot;"})}"/>' for f, i in self.fmts.items())
        xfs = "".join(f'<xf numFmtId="{n}" fontId="{f}" fillId="0" borderId="0" xfId="0"'
                      f'{" applyNumberFormat=" + chr(34) + "1" + chr(34) if n else ""}{" applyFont=" + chr(34) + "1" + chr(34) if f else ""}/>'
                      for f, n in self.xfs)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<numFmts count="{len(self.fmts)}">{nf}</numFmts>'
                '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                f'<cellXfs count="{len(self.xfs)}">{xfs}</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                '</styleSheet>')


def _cell(ref: str, v, style: int) -> str:
    s = f' s="{style}"' if style else ""
    if v is None or (isinstance(v, (float, np.floating)) and not math.isfinite(v)):
        return f'<c r="{ref}"{s}/>' if style else ""            # export contract: non-finite -> blank cell
    if isinstance(v, (bool, np.bool_)):
        v = str(bool(v)).upper()
    if isinstance(v, (int, np.integer)):
        return f'<c r="{ref}"{s}><v>{int(v)}</v></c>'
    if isinstance(v, (float, np.floating)):
        return f'<c r="{ref}"{s}><v>{repr(float(v))}</v></c>'
    t = escape(str(v))
    sp = ' xml:space="preserve"' if t != t.strip() else ""
    return f'<c r="{ref}"{s} t="inlineStr"><is><t{sp}>{t}</t></is></c>'


def _width(v) -> int:
    if v is None:
        return 0
    if isinstance(v, float):
        return len(f"{v:.4f}")
    return len(str(v))


def write_xlsx(path: Path, sheets: list[dict]) -> Path:
    """sheets: [{"name", "blocks": [{"title"|None, "columns", "rows"}], "table_filters": bool}]. Bold headers, frozen
    pane below the first header, AutoFilter (sheet filter for one-table sheets, a filtered table per section
    otherwise), widths capped at 40, numeric cells numeric. Deterministic bytes (fixed zip timestamps)."""
    styles = _Styles()
    files, wb_sheets, rels, ctypes, defined = {}, [], [], [], []
    table_id = 0
    for si, sh in enumerate(sheets, 1):
        rows_xml, widths, tables = [], {}, []
        r = 0
        freeze = None
        sheet_filter = None
        for bi, b in enumerate(sh["blocks"]):
            if bi:
                r += 2                                            # exactly two blank rows between sections
            cols = list(b["columns"])
            if b.get("title"):
                r += 1
                rows_xml.append(f'<row r="{r}">{_cell(f"A{r}", b["title"], styles.xf(None, True))}</row>')
            r += 1
            hdr = r
            rows_xml.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", c, styles.xf(None, True))
                                                       for i, c in enumerate(cols)) + "</row>")
            for i, c in enumerate(cols):
                widths[i] = max(widths.get(i, 0), len(c))
            xf = [styles.xf(FORMATS.get(c)) for c in cols]
            for row in b["rows"]:
                r += 1
                rows_xml.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", v, xf[i] if v is not None else 0)
                                                           for i, v in enumerate(row)) + "</row>")
                for i, v in enumerate(row):
                    widths[i] = max(widths.get(i, 0), _width(v))
            if r > EXCEL_MAX_ROWS or len(cols) > EXCEL_MAX_COLS:
                raise ExportIntegrityError(f"sheet {sh['name']!r}: {r} rows x {len(cols)} columns exceeds the Excel "
                                           f"worksheet limit {EXCEL_MAX_ROWS} x {EXCEL_MAX_COLS}")
            last = f"{_col(len(cols) - 1)}{r}"
            if freeze is None:
                freeze = hdr
            if sh.get("table_filters"):
                if b["rows"]:
                    table_id += 1
                    tname = f"T{table_id}_" + "".join(ch for ch in (b.get("title") or sh["name"]) if ch.isalnum())
                    tables.append((table_id, tname, f"A{hdr}:{last}", cols))
            elif sheet_filter is None:
                sheet_filter = f"A{hdr}:{last}"
        cols_xml = "<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{min(40, w + 2)}" customWidth="1"/>'
                                      for i, w in sorted(widths.items())) + "</cols>" if widths else ""
        pane = (f'<pane ySplit="{freeze}" topLeftCell="A{freeze + 1}" activePane="bottomLeft" state="frozen"/>'
                f'<selection pane="bottomLeft" activeCell="A{freeze + 1}" sqref="A{freeze + 1}"/>') if freeze else ""
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
               f'<sheetViews><sheetView workbookViewId="0"{" tabSelected=" + chr(34) + "1" + chr(34) if si == 1 else ""}>{pane}</sheetView></sheetViews>'
               '<sheetFormatPr defaultRowHeight="15"/>' + cols_xml +
               "<sheetData>" + "".join(rows_xml) + "</sheetData>" +
               (f'<autoFilter ref="{sheet_filter}"/>' if sheet_filter else ""))
        srels = []
        if tables:
            xml += f'<tableParts count="{len(tables)}">' + "".join(
                f'<tablePart r:id="rId{k}"/>' for k in range(1, len(tables) + 1)) + "</tableParts>"
            for k, (tid, tname, ref, cols) in enumerate(tables, 1):
                srels.append(f'<Relationship Id="rId{k}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             f'relationships/table" Target="../tables/table{tid}.xml"/>')
                tc = "".join(f'<tableColumn id="{j}" name="{escape(c, {chr(34): "&quot;"})}"/>' for j, c in enumerate(cols, 1))
                files[f"xl/tables/table{tid}.xml"] = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="{tid}" name="{tname}" '
                    f'displayName="{tname}" ref="{ref}" totalsRowShown="0"><autoFilter ref="{ref}"/>'
                    f'<tableColumns count="{len(cols)}">{tc}</tableColumns>'
                    '<tableStyleInfo showFirstColumn="0" showLastColumn="0" showRowStripes="0" showColumnStripes="0"/></table>')
                ctypes.append(f'<Override PartName="/xl/tables/table{tid}.xml" ContentType="application/'
                              'vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>')
        xml += "</worksheet>"
        files[f"xl/worksheets/sheet{si}.xml"] = xml
        if srels:
            files[f"xl/worksheets/_rels/sheet{si}.xml.rels"] = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(srels) + "</Relationships>")
        if sheet_filter:
            nm = escape(sh["name"]).replace("'", "''")
            absref = ":".join("$" + "".join(ch for ch in x if ch.isalpha()) + "$" + "".join(ch for ch in x if ch.isdigit())
                              for x in sheet_filter.split(":"))
            defined.append(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{si - 1}" hidden="1">'
                           f"'{nm}'!{absref}</definedName>")
        wb_sheets.append(f'<sheet name="{escape(sh["name"])}" sheetId="{si}" r:id="rId{si}"/>')
        rels.append(f'<Relationship Id="rId{si}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    f'relationships/worksheet" Target="worksheets/sheet{si}.xml"/>')
        ctypes.append(f'<Override PartName="/xl/worksheets/sheet{si}.xml" ContentType="application/'
                      'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    n = len(sheets)
    rels.append(f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/styles" Target="styles.xml"/>')
    files["xl/workbook.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                "<sheets>" + "".join(wb_sheets) + "</sheets>"
                                + (f"<definedNames>{''.join(defined)}</definedNames>" if defined else "") + "</workbook>")
    files["xl/_rels/workbook.xml.rels"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                                           '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                           + "".join(rels) + "</Relationships>")
    files["xl/styles.xml"] = styles.xml()
    files["_rels/.rels"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.styles+xml"/>' + "".join(ctypes) + "</Types>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    order = ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"]
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in order + sorted(k for k in files if k not in order):
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name])
    os.replace(tmp, path)
    return path


def read_xlsx(path: Path) -> dict:
    """{sheet name: {"rows": [[values]], "styles": {ref: numFmt}, "bold": set(refs), "freeze": ref, "filters": [refs],
    "widths": {col: width}}} -- semantic reader for tests / idempotence (inline strings + numbers)."""
    import re
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    out = {}
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        st = ET.fromstring(z.read("xl/styles.xml"))
        fm = {int(n.get("numFmtId")): n.get("formatCode") for n in (st.find("m:numFmts", ns) if st.find("m:numFmts", ns) is not None else [])}
        fm.update({0: "General", 1: "0"})
        xfs = [(int(x.get("fontId")), fm[int(x.get("numFmtId"))]) for x in st.find("m:cellXfs", ns)]
        for i, s in enumerate(wb.find("m:sheets", ns), 1):
            ws = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            grid: dict = {}
            fmts, bold = {}, set()
            for c in ws.iter(f"{{{ns['m']}}}c"):
                ref = c.get("r")
                m = re.match(r"([A-Z]+)(\d+)", ref)
                col = sum((ord(ch) - 64) * 26 ** k for k, ch in enumerate(reversed(m.group(1)))) - 1
                row = int(m.group(2)) - 1
                sidx = int(c.get("s", 0))
                font, f = xfs[sidx]
                if font:
                    bold.add(ref)
                fmts[ref] = f
                if c.get("t") == "inlineStr":
                    v = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
                else:
                    vv = c.find("m:v", ns)
                    if vv is None:
                        v = None
                    else:
                        v = float(vv.text)
                        v = int(v) if f in ("0",) and v.is_integer() else v
                grid.setdefault(row, {})[col] = v
            nrow = max(grid) + 1 if grid else 0
            rows = []
            for rr in range(nrow):
                d = grid.get(rr, {})
                rows.append([d.get(cc) for cc in range(max(d) + 1)] if d else [])
            pane = ws.find(".//m:pane", ns)
            filters = [a.get("ref") for a in ws.findall("m:autoFilter", ns)]
            rels = f"xl/worksheets/_rels/sheet{i}.xml.rels"
            if rels in z.namelist():
                for rel in ET.fromstring(z.read(rels)):
                    t = ET.fromstring(z.read("xl/" + rel.get("Target").replace("../", "")))
                    filters.append(t.find("m:autoFilter", ns).get("ref"))
            widths = {int(c.get("min")) - 1: float(c.get("width")) for c in ws.iter(f"{{{ns['m']}}}col")}
            out[s.get("name")] = {"rows": rows, "styles": fmts, "bold": bold,
                                  "freeze": None if pane is None else pane.get("topLeftCell"), "filters": filters,
                                  "widths": widths}
    return out


def semantic(path: Path) -> list:
    """Sheet names, order and every cell value: the idempotence comparison (bytes are not compared)."""
    return [(name, s["rows"]) for name, s in read_xlsx(path).items()]


# ============================================================================ workbooks
def boards_workbook(of: pd.DataFrame, path: Path) -> Path:
    bf = board_frames(of)
    return write_xlsx(path, [{"name": s, "blocks": [{"title": None, "columns": BOARD_COLS, "rows": bf[s]}]}
                             for s in BOARD_SHEETS])


def distribution_sheets(rows: list, max_rows: int | None = None) -> list[list]:
    """Split the Distributions rows into consecutive sheets of at most `max_rows` data rows (plus the header) without
    ever splitting one (GAME, PLAYER, PROP) distribution; row order and every value are unchanged."""
    max_rows = EXCEL_MAX_ROWS - 1 if max_rows is None else max_rows      # header row + data rows <= EXCEL_MAX_ROWS
    sheets, cur, start = [], [], 0
    for i, row in enumerate(rows):
        if i and (row[0], row[2], row[3]) != (rows[i - 1][0], rows[i - 1][2], rows[i - 1][3]):
            start = len(cur)                                     # index of the current distribution's first row
        cur.append(row)
        if len(cur) > max_rows:
            if start == 0:
                raise ExportIntegrityError(f"one distribution has more than {max_rows} outcomes")
            sheets.append(cur[:start])
            cur, start = cur[start:], 0
    return sheets + [cur] if cur or not sheets else sheets


def distributions_workbook(rows: list, path: Path) -> Path:
    parts = distribution_sheets(rows)
    return write_xlsx(path, [{"name": DIST_SHEET if k == 0 else f"{DIST_SHEET} {k + 1}",
                              "blocks": [{"title": None, "columns": DIST_COLS, "rows": part}]}
                             for k, part in enumerate(parts)])


def grades_workbook(of: pd.DataFrame, night: str, path: Path) -> Path:
    summ = summary(of, night)
    return write_xlsx(path, [
        {"name": GRADE_SHEETS[0], "blocks": [{"title": None, "columns": GRADE_COLS, "rows": grade_rows(of)}]},
        {"name": GRADE_SHEETS[1], "table_filters": True,
         "blocks": [{"title": sec, "columns": summ[sec][0], "rows": summ[sec][1]} for sec in SECTIONS]}])


def _immutable(of: pd.DataFrame) -> dict:
    keys = ["game", "team", "player", "prop", "line", "pick", "model_p", "market_p", "edge", "book", "odds"]
    return {r["record_id"]: dict(zip(IMMUTABLE_FIELDS, [None if _f(r[k]) is None and not isinstance(r[k], str) else r[k]
                                                        for k in keys])) for r in of.to_dict(orient="records")}


def check_receipts(sidecar: Path, of: pd.DataFrame) -> dict:
    """A re-export may replace PENDING outcomes; it may never change an immutable prediction field or drop a receipt."""
    now = _immutable(of)
    if not Path(sidecar).exists():
        return {"previous": 0, "changed": [], "missing": [], "new": len(now)}
    prev = json.loads(Path(sidecar).read_text())["receipts"]
    changed = sorted(k for k in prev if k in now and json.dumps(prev[k], sort_keys=True, default=str)
                     != json.dumps(json.loads(json.dumps(now[k], default=str)), sort_keys=True, default=str))
    missing = sorted(k for k in prev if k not in now)
    if changed or missing:
        raise ExportIntegrityError(f"immutable prediction fields differ from the previous export: changed {changed[:5]} "
                                   f"missing {missing[:5]}")
    return {"previous": len(prev), "changed": [], "missing": [], "new": len(set(now) - set(prev))}


def daily(date: str, *, ops_root: Path = PC.OPS_ROOT, runs_root: Path = RUNS_ROOT, out_root: Path = EXPORT_ROOT,
          history_db: Path | None = PC.HISTORY_CANONICAL, state_root: Path | None = PC.STATE_ROOT,
          labels=LIVE_LABELS, now=None) -> dict:
    """Generate the three workbooks of slate date `date` (America/New_York) from persisted evidence only. A workbook
    whose evidence is absent is NOT written (status MISSING_EVIDENCE + the missing dependency); nothing is fetched."""
    date = str(pd.Timestamp(date).date())
    out_dir = Path(out_root) / date
    names_ = file_names(date)
    rep = {"version": EXPORT_VERSION, "date": date, "timezone": TZ, "out_dir": str(out_dir), "network_calls": 0,
           "refits_triggered": 0}
    games = slate_games(ops_root, date)
    rep["predicted_games"] = int(len(games))
    names = team_names(history_db, state_root)
    audit: dict = {}
    rows, missing = distribution_rows(ops_root, runs_root, games, names, date, audit=audit)
    if rows and not missing:
        p, written = distributions_if_changed(rows, out_dir / names_["distributions"])
        mass = pmf_mass_check(rows)
        if audit["canonical_duplicate_keys"]:
            mass["PMF_RECONCILIATION"] = "FAIL"                  # one canonical prediction exported twice
        rep["distributions"] = {"status": "WRITTEN", "path": str(p), "rows": len(rows), "rewritten": written, **mass,
                                "canonical_duplicate_keys": len(audit["canonical_duplicate_keys"]),
                                "display_label_collisions": audit["display_label_collisions"][:20]}
    elif rows:
        rep["distributions"] = {"status": "MISSING_EVIDENCE",
                                "missing": f"durable PMFs absent for {len(missing)} predicted game(s)", "games": missing[:20]}
    else:
        rep["distributions"] = {"status": "MISSING_EVIDENCE",
                                "missing": f"no durable PMF evidence (official tick pmfs.parquet or pre-cutoff forecast "
                                           f"snapshot) for ET slate date {date}", "games": missing[:20]}
    rr = refresh_receipts(ops_root, date, labels)
    rep["refresh_receipts"] = int(len(rr))
    if not len(games) and not len(rr):
        for k in ("boards", "grades"):
            rep[k] = {"status": "MISSING_EVIDENCE", "missing": f"no PREDICTED registry game with ET slate date {date} "
                                                               f"in {Path(ops_root) / 'registry' / 'games.jsonl'}"}
        return rep
    rc = receipts(ops_root, games, labels) if len(games) else pd.DataFrame()
    parts = [x for x in (rc, rr) if len(x)]
    rc = pd.concat(parts, ignore_index=True).drop_duplicates("record_id", keep="first") if parts else pd.DataFrame()
    of, leak = offers(rc, side_settlements(ops_root), names)
    rep["no_post_tip_leakage"] = {**leak, "NO_POST_TIP_LEAKAGE": "PASS"}
    rep["raw_quote_rows"] = int(len(of))
    board, graded = of.iloc[0:0], of.iloc[0:0]
    if len(of):
        cutoffs = {**(dict(zip(rr["game_id"], rr["cutoff_utc"].map(_utc))) if len(rr) else {}),
                   **dict(zip(games["game_id"], games["cutoff_utc"]))}       # the registry cutoff wins
        board, graded, rep["official_selection"] = official(of, selections_path(ops_root, date), cutoffs, now)
        rep["official_rows"] = int(len(board))
        rep["official_frozen_rows"] = int(len(graded))
        rep["duplicate_game_player_prop_rows"] = int(board.duplicated(list(OFFICIAL_KEY)).sum())
    miss = (f"no QUOTE_SIDE receipt (priced pre-tip sportsbook offer) for the {len(games)} predicted game(s) of "
            f"{date}; ticks: {sorted(set(games['tick_id']))}")
    if len(board):
        p = boards_workbook(board, out_dir / names_["boards"])
        bf = board_frames(board)
        rep["boards"] = {"status": "WRITTEN", "path": str(p), **{s: len(bf[s]) for s in BOARD_SHEETS}}
    else:
        rep["boards"] = {"status": "MISSING_EVIDENCE", "missing": miss if not len(of) else
                         "no valid executable offer for any GAME x PLAYER x PROP"}
    if len(graded):
        sidecar = out_dir / "grade_receipts.json"
        rep["receipt_immutability"] = check_receipts(sidecar, graded)
        p = grades_workbook(graded, date, out_dir / names_["grades"])
        summ = summary(graded, date)
        rec = reconcile(graded, summ)
        rep["grades"] = {"status": "WRITTEN", "path": str(p), "rows": int(len(graded)),
                         "results": graded["result"].value_counts().to_dict(), "reconciliation": rec,
                         "GRADE_RECONCILIATION": "PASS" if all(rec.values()) else "FAIL"}
        tmp = sidecar.with_name(sidecar.name + ".tmp")
        tmp.write_text(json.dumps({"date": date, "receipts": _immutable(graded)}, sort_keys=True, indent=0, default=str))
        os.replace(tmp, sidecar)
    else:
        rep["grades"] = {"status": "MISSING_EVIDENCE", "missing": miss if not len(of) else
                         "no OFFICIAL_FROZEN selection (cutoff not yet passed or no valid offer at cutoff)"}
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cbb_platform.prod.exports")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily", help="write the Boards, Distributions and Grades workbooks of one slate date")
    d.add_argument("--date", required=True, help="slate date YYYY-MM-DD (America/New_York)")
    d.add_argument("--ops-root", default=str(PC.OPS_ROOT))
    d.add_argument("--runs-root", default=str(RUNS_ROOT))
    d.add_argument("--out-root", default=str(EXPORT_ROOT))
    d.add_argument("--history-db", default=str(PC.HISTORY_CANONICAL))
    d.add_argument("--state-root", default=str(PC.STATE_ROOT))
    a = ap.parse_args(argv)
    rep = daily(a.date, ops_root=Path(a.ops_root), runs_root=Path(a.runs_root), out_root=Path(a.out_root),
                history_db=Path(a.history_db), state_root=Path(a.state_root))
    print(json.dumps(rep, indent=1, default=str))
    return 0 if all(rep[k]["status"] == "WRITTEN" for k in ("boards", "distributions", "grades")) else 2


if __name__ == "__main__":
    sys.exit(main())
