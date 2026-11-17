"""CBB_Performance: cumulative evaluation of every OFFICIAL CBB Originator selection. REPORTING ONLY.

Built by `python -m cbb_platform.prod.exports daily --date D` after Boards / Grades, from evidence production has ALREADY
persisted (no refit, no scoring, no simulation, no provider call):

    ops/selections/<D>/official_selections.jsonl  the OFFICIAL_FROZEN best-offer receipts (prod.exports.official; content
                                                  and receipt hashes verified by exports.load_selections). MODEL% is the
                                                  receipt's model_p = P(side)/(1-P(push)) persisted when the selection
                                                  was made; a later forecast never replaces it.
    ops/ticks/*/side_settlements.parquet          grading (exports.grade_result / exports.profit: identical to Grades)
    ops/quotes/<D>/observations.jsonl (+parquet)  closing quotes (prod.quote_obs: every normalized, matched quote row)
    ops/registry/games.jsonl, forecast snapshots  scheduled tips (exports.slate_games / exports.snapshot_tips)

Canonical dataset (ONE file, one compact row per official selection, no PMF vectors / payloads):
    ops/performance/cbb_performance.parquet
The daily workbook exports/<D>/CBB_Performance_MM_DD_YYYY.xlsx is derived from it (rows with slate date <= D) and
performance_receipt.json is written next to it.

Conventions (no new methodology):
    entry fair probability   the receipt's market_p: exports.no_vig on the SAME book's two sides of the selected line
    closing fair probability exports.no_vig on the closing quote's same book / same line two sides
    model edge               model_p - entry fair probability
    probability CLV          closing fair - entry fair (selected side; > 0: the market moved toward the selection)
    log-odds CLV             logit(closing fair) - logit(entry fair)
    directional line move    (closing line - entry line) for OVER, (entry line - closing line) for UNDER:
                             > 0 is favorable to the original selection (OVER: the market raised the line; UNDER:
                             the market lowered it). Never converted into probability CLV.
    close                    the LATEST quote observation of the game received at or after the entry capture and
                             strictly BEFORE the scheduled tip (the market close; evaluation only -- the selection was
                             frozen at its prediction cutoff from quotes strictly before it)
    Brier / Log Loss         graded non-push decisions (WIN / LOSS) only; p clipped to [1e-15, 1-1e-15] for Log Loss
    ROI                      net units / units risked, one unit per WIN / LOSS at the recorded selection price (a PUSH
                             or VOID refunds the stake: exports.profit, exports.group_metrics)
    player role              the receipt's role = exports.player_role(prediction-time expected minutes)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import exports as EX, ops_store as OS

PERFORMANCE_VERSION = "cbb-prod-performance-1.0.0"
DATASET = Path("performance") / "cbb_performance.parquet"        # under ops_root
RECEIPT = "performance_receipt.json"
SHEETS = ("Overall", "By Stat", "By Player Role", "Stat x Role", "Daily", "Selections")
CLV_STATUSES = ("EXACT_LINE", "SAME_LINE_NEAREST_CLOSE", "LINE_MOVED", "NO_CLOSE_QUOTE", "NO_DEVIG_PAIR")
CLV_COMPUTED = ("EXACT_LINE", "SAME_LINE_NEAREST_CLOSE")
SETTLED = ("WIN", "LOSS", "PUSH", "VOID")
EPS = 1e-15

PERF_COLS = (
    "perf_key", "slate_date", "official_selection_id", "selection_receipt_hash", "quote_record_id", "game_id",
    "player_id", "team_id", "player_name", "team", "opponent", "game", "stat", "player_role", "expected_minutes",
    "side", "line", "official_selection_ts", "frozen_recorded_at", "prediction_as_of", "entry_capture_utc", "tip_utc",
    "model_version", "forecast_snapshot_id", "tick_id", "model_probability", "entry_book", "entry_american_odds",
    "entry_decimal_price", "entry_implied_probability", "entry_fair_probability", "model_edge", "close_ts",
    "close_book", "close_observation", "closing_line", "closing_implied_probability", "closing_fair_probability",
    "probability_clv", "log_odds_clv", "raw_line_movement", "directional_line_movement", "clv_status", "actual_stat",
    "outcome", "grade", "pnl", "integrity_status", "close_window_closed")

METRICS = ("Predictions", "Graded", "Wins", "Losses", "Pushes", "Voids", "Pending", "Win %", "Mean Model Probability",
           "Actual Hit Rate", "Calibration Gap", "Brier Score", "Log Loss", "Mean Model Edge at Selection",
           "Median Model Edge at Selection", "Mean Entry Implied Probability", "Mean Entry Fair Probability",
           "Mean Closing Fair Probability", "Mean Probability CLV (pp)", "Median Probability CLV (pp)",
           "Mean Log-Odds CLV", "Positive CLV %", "Exact-Line CLV N", "Line-Moved N", "No-Close N", "No-Devig-Pair N",
           "Mean Directional Line Movement", "Average Recorded Price", "Units Risked", "Net Units", "ROI",
           "Integrity Excluded", "First Date", "Last Date")
DAILY_COLS = ("Date", "Predictions", "Graded", "Wins", "Losses", "Pushes", "Win %", "Brier Score", "Log Loss",
              "Mean Probability CLV (pp)", "Positive CLV %", "ROI")
BIN_COLS = ("Model Probability Bin", "N", "Mean Predicted Probability", "Actual Hit Rate", "Brier Score")
STATUS_COLS = ("CLV Status", "Selections")
SEL_COLS = ("date", "game_id", "player_id", "player_name", "team", "opponent", "stat", "player_role", "side", "line",
            "official_selection_timestamp", "model_probability", "entry_book", "entry_decimal_price",
            "entry_implied_probability", "entry_fair_probability", "model_edge", "close_timestamp", "close_book",
            "closing_line", "closing_implied_probability", "closing_fair_probability", "probability_clv_pp",
            "log_odds_clv", "directional_line_movement", "clv_status", "actual_stat", "outcome", "grade", "pnl",
            "integrity_status", "selection_receipt", "quote_record_id", "forecast_snapshot_id", "tick_id",
            "close_observation")
PCT = "0.0%"
FORMATS = {**{c: PCT for c in ("Win %", "Mean Model Probability", "Actual Hit Rate", "Calibration Gap",
                               "Mean Model Edge at Selection", "Median Model Edge at Selection",
                               "Mean Entry Implied Probability", "Mean Entry Fair Probability",
                               "Mean Closing Fair Probability", "Positive CLV %", "ROI", "Mean Predicted Probability",
                               "model_probability", "entry_implied_probability", "entry_fair_probability",
                               "model_edge", "closing_implied_probability", "closing_fair_probability")},
           "Brier Score": "0.0000", "Log Loss": "0.0000", "Mean Log-Odds CLV": "0.0000", "log_odds_clv": "0.0000",
           "Mean Probability CLV (pp)": "0.00", "Median Probability CLV (pp)": "0.00", "probability_clv_pp": "0.00",
           "Mean Directional Line Movement": "0.00", "directional_line_movement": "0.0", "line": "0.0",
           "closing_line": "0.0", "Average Recorded Price": "0.000", "entry_decimal_price": "0.000",
           "Units Risked": "0", "Net Units": "0.000", "pnl": "0.000", "actual_stat": "0.##"}


class PerformanceIntegrityError(EX.ExportIntegrityError):
    """A canonical-performance invariant failed (duplicate key, vanished official selection, ...)."""


# ============================================================================ small helpers
def _iso(t) -> str | None:
    t = EX._utc(t)
    return None if t is None else t.isoformat()


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def perf_key(r: dict) -> str:
    """official selection x game x player x stat x side x line x official selection timestamp."""
    k = [r["official_selection_id"], r["game_id"], r["player_id"], r["stat"], r["side"], repr(float(r["line"])),
         r["official_selection_ts"]]
    return hashlib.sha256(json.dumps(k).encode()).hexdigest()


def opponent(game, team) -> str | None:
    """The other side of an 'AWAY @ HOME [date]' label when `team` is exactly one side; otherwise blank (never guessed)."""
    if not isinstance(game, str) or not isinstance(team, str) or " @ " not in game:
        return None
    away, home = game.split(" @ ", 1)
    parts = home.rsplit(" ", 1)
    if len(parts) == 2 and len(parts[1]) == 10 and parts[1][4] == "-" and parts[1][7] == "-":
        home = parts[0]
    away, home = away.strip(), home.strip()
    return home if team == away else away if team == home else None


def selection_dates(ops_root: Path, through: str) -> list[str]:
    d = Path(ops_root) / "selections"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if (p / "official_selections.jsonl").exists():
            try:
                day = str(pd.Timestamp(p.name).date())
            except ValueError:
                continue
            if EX.PC.PRODUCTION_SEASON_START <= day <= through:
                out.append(day)
    return out


def _tips(ops_root: Path, day: str) -> dict:
    tips = dict(EX.snapshot_tips(ops_root, day))
    g = EX.slate_games(ops_root, day)
    if len(g):
        tips.update({a: b for a, b in zip(g["game_id"], g["tip_utc"]) if b is not None})     # the registry tip wins
    return {k: EX._utc(v) for k, v in tips.items() if EX._utc(v) is not None}


def _refresh_tips(ops_root: Path, day: str) -> dict:
    """tip_utc carried by the tiered quote-refresh receipts (games the registry does not list)."""
    recs = EX._jsonl(Path(ops_root) / "quotes" / day / EX.REFRESH_RECEIPTS)
    out = {}
    for r in recs:
        b = r.get("body") or {}
        if r.get("record_type") == "QUOTE_SIDE" and b.get("game_id") and EX._utc(b.get("tip_utc")) is not None:
            out.setdefault(b["game_id"], EX._utc(b["tip_utc"]))
    return out


class _Observations:
    """quote_obs index of one ET date; rows read lazily per observation file."""

    def __init__(self, ops_root: Path, day: str):
        self.dir = Path(ops_root) / "quotes" / day
        self.index = OS.read_jsonl(self.dir / "observations.jsonl") if (self.dir / "observations.jsonl").exists() else []
        self._rows: dict = {}

    def window(self, game_id: str, start, end) -> list[dict]:
        """Observations of the game received in [start, end), latest first (received_at, then source)."""
        out = [r for r in self.index if r["game_id"] == game_id and (start is None or EX._utc(r["received_at"]) >= start)
               and EX._utc(r["received_at"]) < end]
        return sorted(out, key=lambda r: (EX._utc(r["received_at"]), r["source"]), reverse=True)

    def rows(self, ob: dict) -> pd.DataFrame:
        f = ob.get("file")
        if not f:
            return pd.DataFrame()
        if f not in self._rows:
            self._rows[f] = pd.read_parquet(self.dir / f)
        return self._rows[f]


def _contract(rows: pd.DataFrame, game_id: str, player_id: str, stat: str) -> pd.DataFrame:
    if not len(rows):
        return rows
    q = rows[(rows["quote_status"] == "VALID") & (rows["canonical_game_id"] == game_id)
             & (rows["canonical_player_id"] == player_id) & (rows["stat"].astype(object) == stat)]
    return q.assign(_book=q["sportsbook"].map(EX.norm_book), _line=q["line"].map(EX._f),
                    _dec=q["decimal_odds"].map(EX._f))


def _pair(q: pd.DataFrame, book: str, line: float, side: str):
    """(decimal selected side, decimal other side) of one book at one exact line (None when absent)."""
    other = "UNDER" if side == "OVER" else "OVER"
    b = q[(q["_book"] == book) & (q["_line"] == line)]

    def dec(s):
        x = b[b["side"] == s]["_dec"].dropna()
        return None if not len(x) else float(x.iloc[0])
    return dec(side), dec(other)


def closing(obs: _Observations, sel: dict, tip) -> dict:
    """Closing evidence of one selection (exactly one CLV status; nothing fabricated)."""
    out = {"close_ts": None, "close_book": None, "close_observation": None, "closing_line": None,
           "closing_implied_probability": None, "closing_fair_probability": None, "probability_clv": None,
           "log_odds_clv": None, "raw_line_movement": None, "directional_line_movement": None,
           "clv_status": "NO_CLOSE_QUOTE"}
    if tip is None:
        return out
    side, line, book = sel["side"], float(sel["line"]), EX.norm_book(sel["entry_book"])
    win = obs.window(sel["game_id"], EX._utc(sel["entry_capture_utc"]), tip)
    quoted = [(ob, _contract(obs.rows(ob), sel["game_id"], sel["player_id"], sel["stat"])) for ob in win]
    quoted = [(ob, q) for ob, q in quoted if len(q)]
    if not quoted:
        return out

    def take(ob, q, bk, ln, status):
        ds, do = _pair(q, bk, ln, side)
        out.update(close_ts=_iso(ob["received_at"]), close_book=bk, close_observation=ob.get("file"), closing_line=ln,
                   closing_implied_probability=None if ds is None or ds <= 1 else 1.0 / ds,
                   closing_fair_probability=EX.no_vig(side, ds, do), raw_line_movement=ln - line,
                   directional_line_movement=(ln - line) if side == "OVER" else (line - ln), clv_status=status)
        return out
    latest_ob, latest_q = quoted[0]
    exact = []                                                 # (observation, q, book) with the exact line, selected side
    for ob, q in quoted:
        for bk in sorted(set(q[(q["_line"] == line) & (q["side"] == side)]["_book"])):
            exact.append((ob, q, bk))
    pick = None
    if any(ob is latest_ob and bk == book for ob, _, bk in exact):
        pick = (latest_ob, latest_q, book, "EXACT_LINE")
    else:
        same = [(ob, q, bk) for ob, q, bk in exact if bk == book]
        ref = [(ob, q, bk) for ob, q, bk in exact if ob is latest_ob]
        cand = same[:1] or ref[:1]
        if cand:
            pick = cand[0] + ("SAME_LINE_NEAREST_CLOSE",)
    if pick is not None:
        ob, q, bk, status = pick
        take(ob, q, bk, line, status)
        ef, cf = EX._f(sel["entry_fair_probability"]), out["closing_fair_probability"]
        if ef is None or cf is None or not 0 < ef < 1 or not 0 < cf < 1:
            out["clv_status"] = "NO_DEVIG_PAIR"
        else:
            out["probability_clv"] = cf - ef
            out["log_odds_clv"] = _logit(cf) - _logit(ef)
        return out
    # no exact-line quote of the selected side: the line moved (same book first, else any book) -> movement only
    s = latest_q[latest_q["side"] == side].dropna(subset=["_line"])
    if not len(s):
        return out
    mine = s[s["_book"] == book]
    s = mine if len(mine) else s
    s = s.assign(_alt=s["is_alternate"].map(lambda v: v is True), _d=(s["_line"] - line).abs())
    r = s.sort_values(["_alt", "_d", "_line", "_book"], kind="mergesort").iloc[0]
    take(latest_ob, latest_q, r["_book"], float(r["_line"]), "LINE_MOVED")
    return out


def integrity(row: dict) -> str:
    """PASS, or the first point-in-time / evidence failure (the row is kept in Selections, excluded from metrics)."""
    p, cut, tip = EX._f(row["model_probability"]), EX._utc(row["official_selection_ts"]), EX._utc(row["tip_utc"])
    aso, cap = EX._utc(row["prediction_as_of"]), EX._utc(row["entry_capture_utc"])
    if p is None or not 0 < p < 1:
        return "MISSING_EVIDENCE:model_probability"
    if row["side"] not in EX.SIDES or EX._f(row["line"]) is None or not EX.valid_american(row["entry_american_odds"]):
        return "MISSING_EVIDENCE:contract"
    if cut is None:
        return "MISSING_EVIDENCE:official_selection_ts"
    if tip is None:
        return "MISSING_EVIDENCE:tip"
    if aso is None or aso > cut:
        return "FUTURE_LEAK:prediction_after_selection"
    if cap is None or cap >= cut:
        return "FUTURE_LEAK:entry_quote_not_before_selection"
    if cut > tip:
        return "FUTURE_LEAK:selection_after_tip"
    ct = EX._utc(row["close_ts"])
    if ct is not None and (ct >= tip or ct < cap):
        return "FUTURE_LEAK:close_outside_entry_to_tip"
    if row["player_role"] != EX.player_role(row["expected_minutes"]):
        return "FUTURE_LEAK:role_not_prediction_time"
    return "PASS"


def selection_row(day: str, r: dict, tip, settle: dict, obs: _Observations, now) -> dict:
    dec = EX.decimal_odds(r["odds"])
    row = {"slate_date": day, "official_selection_id": r["content_hash"], "selection_receipt_hash": r["receipt_hash"],
           "quote_record_id": r["record_id"], "game_id": r["game_id"], "player_id": r["player_id"],
           "team_id": r.get("team_id"), "player_name": r.get("player"), "team": r.get("team"),
           "opponent": opponent(r.get("game"), r.get("team")), "game": r.get("game"), "stat": r["stat"],
           "player_role": r.get("role") or "UNKNOWN_ROLE", "expected_minutes": EX._f(r.get("expected_minutes")),
           "side": r["pick"], "line": EX._f(r["line"]), "official_selection_ts": _iso(r.get("cutoff_utc")),
           "frozen_recorded_at": r.get("selected_at"), "prediction_as_of": _iso(r.get("prediction_as_of")),
           "entry_capture_utc": _iso(r.get("capture_utc")), "tip_utc": _iso(tip),
           "model_version": r.get("model_version"), "forecast_snapshot_id": r.get("forecast_snapshot_id"),
           "tick_id": r.get("tick_id"), "model_probability": EX._f(r.get("model_p")), "entry_book": r.get("book"),
           "entry_american_odds": EX._f(r.get("odds")), "entry_decimal_price": dec,
           "entry_implied_probability": None if dec is None else 1.0 / dec,
           "entry_fair_probability": EX._f(r.get("market_p"))}
    mp, ef = row["model_probability"], row["entry_fair_probability"]
    row["model_edge"] = None if mp is None or ef is None else mp - ef
    row.update(closing(obs, row, tip))
    s = settle.get(r["record_id"])
    res = EX.grade_result(row["side"], row["entry_american_odds"], None if s is None else s.get("result"))
    row.update(actual_stat=None if s is None else EX._f(s.get("actual")),
               outcome=None if s is None or not isinstance(s.get("result"), str) else s.get("result"), grade=res,
               pnl=EX.profit(res, row["entry_american_odds"]) if res not in ("PENDING", "UNSUPPORTED") else None,
               close_window_closed=bool(tip is not None and EX._utc(now) >= tip))
    row["integrity_status"] = integrity(row)
    row["perf_key"] = perf_key(row) if row["official_selection_ts"] and row["line"] is not None else \
        hashlib.sha256(r["receipt_hash"].encode()).hexdigest()
    return {c: row.get(c) for c in PERF_COLS}


def _final(row: dict) -> bool:
    return row.get("grade") in SETTLED and bool(row.get("close_window_closed"))


# ============================================================================ canonical dataset (incremental)
def _content_sha(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        return None if not math.isfinite(float(v)) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def read_dataset(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    df = pd.read_parquet(path)
    missing = [c for c in PERF_COLS if c not in df.columns]
    if missing:
        raise PerformanceIntegrityError(f"{path}: canonical performance dataset lacks {missing}")
    return [{c: _clean(r[c]) for c in PERF_COLS} for r in df.to_dict(orient="records")]


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(PERF_COLS))
    for c in ("expected_minutes", "line", "model_probability", "entry_american_odds", "entry_decimal_price",
              "entry_implied_probability", "entry_fair_probability", "model_edge", "closing_line",
              "closing_implied_probability", "closing_fair_probability", "probability_clv", "log_odds_clv",
              "raw_line_movement", "directional_line_movement", "actual_stat", "pnl"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["close_window_closed"] = df["close_window_closed"].astype(bool)
    for c in PERF_COLS:
        if df[c].dtype == object:
            df[c] = df[c].astype(object)
    return df


def update(ops_root: Path, through: str, now=None) -> dict:
    """Bring the canonical dataset up to date for every official selection with slate date <= `through`:
    existing FINAL rows (graded and past tip) of an unchanged receipt are reused as stored; every other row is rebuilt
    from evidence; rows of later slate dates are kept untouched. Written atomically, only when the content changed.
    Returns {"rows", "path", "rewritten", "reused", "rebuilt", "dataset_sha256"}."""
    ops_root = Path(ops_root)
    now = EX._utc(now) or pd.Timestamp.now(tz="UTC")
    path = ops_root / DATASET
    old = read_dataset(path)
    have = {}
    for r in old:
        if r["perf_key"] in have:
            raise PerformanceIntegrityError(f"duplicate perf_key {r['perf_key'][:12]} in {path}")
        have[r["perf_key"]] = r
    settle = {}
    st = EX.side_settlements(ops_root)
    if len(st):
        settle = {r["record_id"]: r for r in st.to_dict(orient="records")}
    rows, reused, rebuilt, seen_sel = [], 0, 0, set()
    by_receipt = {(o["official_selection_id"], o["slate_date"], o["selection_receipt_hash"]): o for o in old}
    for day in selection_dates(ops_root, through):
        sel = EX.load_selections(EX.selections_path(ops_root, day))
        frozen = [v["frozen"] for _, v in sorted(sel.items()) if v["frozen"] is not None]
        if not frozen:
            continue
        tips = {**_refresh_tips(ops_root, day), **_tips(ops_root, day)}
        obs = _Observations(ops_root, day)
        for r in frozen:
            if (r["game_id"], r["player_id"], r["stat"]) in seen_sel:
                raise PerformanceIntegrityError(f"official selection {r['game_id']} {r['player_id']} {r['stat']} "
                                                f"appears on two slate dates")
            seen_sel.add((r["game_id"], r["player_id"], r["stat"]))
            prev = by_receipt.get((r["content_hash"], day, r["receipt_hash"]))
            if prev is not None and _final(prev):
                rows.append(prev)
                reused += 1
                continue
            rows.append(selection_row(day, r, tips.get(r["game_id"]), settle, obs, now))
            rebuilt += 1
    keys = {r["perf_key"] for r in rows}
    if len(keys) != len(rows):
        raise PerformanceIntegrityError("duplicate canonical performance keys")
    vanished = [k for k, r in have.items() if r["slate_date"] <= through and k not in keys]
    if vanished:
        raise PerformanceIntegrityError(f"{len(vanished)} canonical performance row(s) lost their official selection")
    rows += [r for r in old if r["slate_date"] > through]
    rows = sorted(rows, key=lambda r: (r["slate_date"], r["tip_utc"] or "", r["game_id"], r["player_id"], r["stat"],
                                       r["perf_key"]))
    rows = [{c: _clean(r[c]) for c in PERF_COLS} for r in rows]
    rewritten = _content_sha(rows) != _content_sha(old) or not path.exists()
    if rewritten:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        _frame(rows).to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, path)
    return {"rows": len(rows), "path": str(path), "rewritten": rewritten, "reused": reused, "rebuilt": rebuilt,
            "dataset_sha256": EX._file_sha(path)}


# ============================================================================ metrics
def _m(v):
    x = [float(a) for a in v if EX._f(a) is not None]
    return sum(x) / len(x) if x else None


def _med(v):
    x = [float(a) for a in v if EX._f(a) is not None]
    return float(np.median(x)) if x else None


def brier(p, y) -> float | None:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2)) if len(p) else None


def log_loss(p, y) -> float | None:
    p, y = np.clip(np.asarray(p, float), EPS, 1 - EPS), np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else None


def metrics(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["integrity_status"] == "PASS"]
    g = [r["grade"] for r in ok]
    dec = [r for r in ok if r["grade"] in ("WIN", "LOSS")]
    p = [float(r["model_probability"]) for r in dec]
    y = [1.0 if r["grade"] == "WIN" else 0.0 for r in dec]
    w, l = g.count("WIN"), g.count("LOSS")
    hit = w / (w + l) if w + l else None
    mp = _m(p)
    clv = [r["probability_clv"] for r in ok if r["clv_status"] in CLV_COMPUTED and EX._f(r["probability_clv"]) is not None]
    net = sum(float(r["pnl"]) for r in dec if EX._f(r["pnl"]) is not None)
    risked = len([r for r in dec if EX._f(r["pnl"]) is not None])
    st = [r["clv_status"] for r in ok]
    dates = sorted(r["slate_date"] for r in ok)
    return {"Predictions": len(ok), "Graded": w + l + g.count("PUSH"), "Wins": w, "Losses": l, "Pushes": g.count("PUSH"),
            "Voids": g.count("VOID"), "Pending": g.count("PENDING") + g.count("UNSUPPORTED"), "Win %": hit,
            "Mean Model Probability": mp, "Actual Hit Rate": hit,
            "Calibration Gap": None if hit is None or mp is None else hit - mp,
            "Brier Score": brier(p, y), "Log Loss": log_loss(p, y),
            "Mean Model Edge at Selection": _m(r["model_edge"] for r in ok),
            "Median Model Edge at Selection": _med(r["model_edge"] for r in ok),
            "Mean Entry Implied Probability": _m(r["entry_implied_probability"] for r in ok),
            "Mean Entry Fair Probability": _m(r["entry_fair_probability"] for r in ok),
            "Mean Closing Fair Probability": _m(r["closing_fair_probability"] for r in ok
                                                if r["clv_status"] in CLV_COMPUTED),
            "Mean Probability CLV (pp)": None if not clv else 100.0 * _m(clv),
            "Median Probability CLV (pp)": None if not clv else 100.0 * _med(clv),
            "Mean Log-Odds CLV": _m(r["log_odds_clv"] for r in ok if r["clv_status"] in CLV_COMPUTED),
            "Positive CLV %": None if not clv else sum(1 for c in clv if c > 0) / len(clv),
            "Exact-Line CLV N": len(clv), "Line-Moved N": st.count("LINE_MOVED"), "No-Close N": st.count("NO_CLOSE_QUOTE"),
            "No-Devig-Pair N": st.count("NO_DEVIG_PAIR"),
            "Mean Directional Line Movement": _m(r["directional_line_movement"] for r in ok if r["closing_line"] is not None),
            "Average Recorded Price": _m(r["entry_decimal_price"] for r in ok),
            "Units Risked": risked, "Net Units": net if risked else None, "ROI": net / risked if risked else None,
            "Integrity Excluded": len(rows) - len(ok), "First Date": dates[0] if dates else None,
            "Last Date": dates[-1] if dates else None}


def calibration_bins(rows: list[dict]) -> list[list]:
    out = []
    dec = [r for r in rows if r["integrity_status"] == "PASS" and r["grade"] in ("WIN", "LOSS")]
    for name, _, _ in EX.BANDS:
        b = [r for r in dec if EX.model_band(r["model_probability"]) == name]
        p = [float(r["model_probability"]) for r in b]
        y = [1.0 if r["grade"] == "WIN" else 0.0 for r in b]
        out.append([name, len(b), _m(p), _m(y), brier(p, y)])
    return out


def _group(rows: list[dict], keys: tuple) -> list[list]:
    groups: dict = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in keys), []).append(r)
    return [list(k) + [metrics(g)[c] for c in METRICS] for k, g in sorted(groups.items(), key=lambda x: tuple(map(str, x[0])))]


def tables(rows: list[dict]) -> dict:
    """{sheet: [blocks]} of the workbook (pure function of the canonical rows)."""
    m = metrics(rows)
    status = [[s, sum(1 for r in rows if r["integrity_status"] == "PASS" and r["clv_status"] == s)] for s in CLV_STATUSES]
    daily = []
    for d, g in sorted(_groups(rows, "slate_date").items()):
        mm = metrics(g)
        daily.append([d] + [mm[c] for c in DAILY_COLS[1:]])
    sel = []
    for r in rows:
        sel.append([r["slate_date"], r["game_id"], r["player_id"], r["player_name"], r["team"], r["opponent"], r["stat"],
                    r["player_role"], r["side"], r["line"], r["official_selection_ts"], r["model_probability"],
                    r["entry_book"], r["entry_decimal_price"], r["entry_implied_probability"],
                    r["entry_fair_probability"], r["model_edge"], r["close_ts"], r["close_book"], r["closing_line"],
                    r["closing_implied_probability"], r["closing_fair_probability"],
                    None if EX._f(r["probability_clv"]) is None else 100.0 * r["probability_clv"], r["log_odds_clv"],
                    r["directional_line_movement"], r["clv_status"], r["actual_stat"], r["outcome"], r["grade"], r["pnl"],
                    r["integrity_status"], r["official_selection_id"], r["quote_record_id"], r["forecast_snapshot_id"],
                    r["tick_id"], r["close_observation"]])
    return {
        "Overall": [{"title": "Overall", "columns": ("Scope",) + METRICS, "rows": [["ALL"] + [m[c] for c in METRICS]]},
                    {"title": "Calibration by Model Probability", "columns": BIN_COLS, "rows": calibration_bins(rows)},
                    {"title": "CLV Status", "columns": STATUS_COLS, "rows": status}],
        "By Stat": [{"title": None, "columns": ("Stat",) + METRICS, "rows": _group(rows, ("stat",))}],
        "By Player Role": [{"title": None, "columns": ("Player Role",) + METRICS, "rows": _group(rows, ("player_role",))}],
        "Stat x Role": [{"title": None, "columns": ("Stat", "Player Role") + METRICS,
                         "rows": _group(rows, ("stat", "player_role"))}],
        "Daily": [{"title": None, "columns": DAILY_COLS, "rows": daily}],
        "Selections": [{"title": None, "columns": SEL_COLS, "rows": sel}]}


def _groups(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def workbook(tbl: dict, path: Path) -> Path:
    return EX.write_xlsx(path, [{"name": s, "blocks": [{**b, "formats": FORMATS} for b in tbl[s]],
                                 "table_filters": s == "Overall"} for s in SHEETS])


def status_of(rows: list[dict]) -> str:
    ok = [r for r in rows if r["integrity_status"] == "PASS"]
    if not rows:
        return "NO_OFFICIAL_SELECTIONS_YET"
    graded = sum(1 for r in ok if r["grade"] in SETTLED)
    return "NOTHING_GRADED_YET" if not graded else ("GRADED" if graded == len(ok) else "PARTIALLY_GRADED")


# ============================================================================ the daily export step
def verify(out_dir: Path, date: str, ops_root: Path) -> dict:
    """Health evidence only (never a quality gate): workbook readable with the six tabs, canonical dataset readable,
    receipt valid (its sha256s match the files)."""
    wb = Path(out_dir) / performance_name(date)
    ds = Path(ops_root) / DATASET
    chk = {"WORKBOOK_READABLE": False, "DATASET_READABLE": False, "RECEIPT_VALID": False}
    try:
        chk["WORKBOOK_READABLE"] = tuple(EX.read_xlsx(wb)) == SHEETS
    except Exception:                                                # noqa: BLE001 -- reported, never raised
        pass
    try:
        read_dataset(ds)
        chk["DATASET_READABLE"] = ds.exists()
    except Exception:                                                # noqa: BLE001
        pass
    try:
        rec = json.loads((Path(out_dir) / RECEIPT).read_text())
        chk["RECEIPT_VALID"] = rec.get("through_date") == date and wb.exists() and ds.exists() and \
            rec.get("workbook_sha256") == EX._file_sha(wb) and rec.get("canonical_dataset_sha256") == EX._file_sha(ds)
    except Exception:                                                # noqa: BLE001
        pass
    chk["PERFORMANCE_EVIDENCE"] = "PASS" if all(chk.values()) else "FAIL"
    return chk


def performance_name(date: str) -> str:
    return EX.file_names(date)["performance"]


def export(date: str, ops_root: Path, out_dir: Path, now=None) -> dict:
    """Update the canonical dataset and write exports/<date>/CBB_Performance_*.xlsx + performance_receipt.json.
    Workbook rewritten only when its rows or this writer changed. Returns the report (status WRITTEN when verified)."""
    date = str(pd.Timestamp(date).date())
    out_dir = Path(out_dir)
    try:
        ds = update(ops_root, date, now)
        rows = [r for r in read_dataset(Path(ds["path"])) if r["slate_date"] <= date]
    except EX.ExportIntegrityError as e:
        return {"status": "ABORTED_INTEGRITY", "missing": str(e)[:400]}
    except (OSError, ValueError) as e:
        return {"status": "PERFORMANCE_MISSING_EVIDENCE", "missing": f"{type(e).__name__}: {str(e)[:400]}"}
    tbl = tables(rows)
    path = out_dir / performance_name(date)
    rec_p = out_dir / RECEIPT
    writer = hashlib.sha256(Path(__file__).read_bytes() + Path(EX.__file__).read_bytes()).hexdigest()
    content = _content_sha([tbl[s] for s in SHEETS])
    try:
        old = json.loads(rec_p.read_text())
    except (OSError, ValueError):
        old = None
    same = old and old.get("content_sha256") == content and old.get("writer_sha256") == writer and path.exists() \
        and EX._file_sha(path) == old.get("workbook_sha256")
    if not same:
        workbook(tbl, path)
    m = metrics(rows)
    ok = [r for r in rows if r["integrity_status"] == "PASS"]
    rec = {"version": PERFORMANCE_VERSION, "file": path.name, "through_date": date, "status": status_of(rows),
           "total_selections": len(rows), "graded_selections": m["Graded"], "integrity_excluded": m["Integrity Excluded"],
           "brier_score": m["Brier Score"], "log_loss": m["Log Loss"], "exact_line_clv_n": m["Exact-Line CLV N"],
           "line_moved_n": m["Line-Moved N"], "no_close_n": m["No-Close N"], "no_devig_pair_n": m["No-Devig-Pair N"],
           "mean_probability_clv": None if m["Mean Probability CLV (pp)"] is None else m["Mean Probability CLV (pp)"] / 100,
           "positive_clv_pct": m["Positive CLV %"], "stat_category_count": len({r["stat"] for r in ok}),
           "role_category_count": len({r["player_role"] for r in ok}),
           "stat_role_count": len({(r["stat"], r["player_role"]) for r in ok}),
           "canonical_dataset": str(Path(ds["path"])), "canonical_dataset_rows": ds["rows"],
           "canonical_dataset_sha256": ds["dataset_sha256"], "workbook_sha256": EX._file_sha(path),
           "content_sha256": content, "writer_sha256": writer}
    if old != rec:
        tmp = rec_p.with_name(rec_p.name + ".tmp")
        tmp.write_text(json.dumps(rec, sort_keys=True, indent=1, default=str) + "\n")
        os.replace(tmp, rec_p)
    chk = verify(out_dir, date, ops_root)
    return {"status": "WRITTEN" if chk["PERFORMANCE_EVIDENCE"] == "PASS" else "PERFORMANCE_EVIDENCE_INVALID",
            "path": str(path), "rows": len(rows), "rewritten": not same, "PERFORMANCE_STATUS": rec["status"],
            "receipt": str(rec_p), "dataset": {k: ds[k] for k in ("path", "rows", "rewritten", "reused", "rebuilt")},
            "verification": chk, **{k: rec[k] for k in ("graded_selections", "brier_score", "log_loss",
                                                        "exact_line_clv_n", "mean_probability_clv", "positive_clv_pct")}}
