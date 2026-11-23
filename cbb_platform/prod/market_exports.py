"""CBB market-evaluation exports: the CBB_Boards rows, the frozen pre-cutoff market evaluations, Grades (D grades the
D-1 ET slate) and the shared graded-row / CLV / metric functions that CBB_Performance (prod.performance) also uses, so
Grades and Performance agree exactly. REPORTING ONLY: no network, no refit, no simulation; every probability is the
persisted PMF priced by prod.market_eval (existing pricing / EV / de-vig).

Board grain: EXACTLY one row per GAME x PLAYER x STAT (asserted; a violation aborts the Boards export).
    Full Board       rows with a real market (>= 1 eligible, priceable pre-cutoff sportsbook offer)
    60 Percent Plus  REPORTING VIEW: threshold screen and model probability >= 0.60 (tiers 60_PLUS, 65_PLUS)
    65 Percent Plus  REPORTING VIEW: threshold screen and model probability >= 0.65 (tier 65_PLUS)
                     (neither implies Is Actionable: market_eval.ACTIONABILITY_VALIDATED is False)
    Model Coverage   model-only rows (no real market): never on the Full Board

Frozen evaluations (the graded evidence; compact, write-once):
    ops/evaluations/<D>/market_evaluations.jsonl
        MARKET_EVALUATION  one per market GAME x PLAYER x STAT, written once the game's cutoff has passed: the Board
                           evaluation (prod.market_eval, the latest pre-cutoff PMF) at the DECISION observation = the
                           first persisted pre-cutoff observation received at or after that PMF's prediction instant
                           (else the latest pre-cutoff observation before it). Entry = that observation (its book,
                           line, side, price and same-book de-vig). content_hash; never rewritten.
        GAME_FROZEN        one per game after its evaluations (market rows, model-only rows, decision observation)

Close (CLV, evaluation only): the LAST persisted observation of the game received at or before
min(commence, legal cutoff) and at most CLOSE_MAX_STALE_MINUTES before that instant; otherwise NO_CLOSE_QUOTE.
    EXACT_LINE               the close observation quotes the same book at the same line and side
    SAME_LINE_NEAREST_CLOSE  else: the latest in-window observation with the same book / line / side, else another book
                             at the same line in the close observation
    LINE_MOVED               no quote of the line: entry line, close line and directional movement (positive =
                             favorable: OVER line raised, UNDER line lowered), never converted into probability CLV
    NO_DEVIG_PAIR            a same-line close exists but entry or close lacks a same-book two-sided price
    probability CLV = close fair - entry fair; log-odds CLV = logit(close fair) - logit(entry fair)

Grading (exact persisted line): WIN / LOSS / PUSH from the final box (ops ticks/*/actuals.parquet); 0 minutes / no box
row of a final game = VOID_DNP; game not played = VOID_NO_GAME; no final yet = PENDING. VOID_* and PENDING are excluded
from Brier, Log Loss, win rate and ROI. One unit is staked per graded row at the recorded best price; a push refunds it.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import engine as ENG, market_eval as ME, ops_store as OS

MX_VERSION = "cbb-market-exports-1.0.0"
TZ = "America/New_York"
CLOSE_MAX_STALE_MINUTES = 60
EVAL_DIR = "evaluations"
EVAL_FILE = "market_evaluations.jsonl"
EPS = 1e-15

BOARD_KEY = ("Canonical Game ID", "Canonical Player ID", "Stat")
BOARD_COLS = (
    "Slate Date", "Canonical Game ID", "Game", "Tip Time ET", "Legal Cutoff ET", "Player", "Canonical Player ID", "Team",
    "Opponent", "Player Role", "Stat", "Model Projection", "Best Market Line", "Preferred Side", "Model Probability",
    "Push Probability", "Fair Market Probability", "Probability Edge", "Model Fair Odds", "Best Book",
    "Best American Odds", "Best Decimal Odds", "Preferred EV", "Is Actionable", "Reporting Tier", "Actionability Status",
    "Consensus Line",
    "Consensus Over Odds", "Consensus Under Odds", "Consensus Fair Over %", "Consensus Fair Under %", "Raw Book Count",
    "Consensus Book Count", "Outlier Book Count", "Quote Timestamp", "Quote Age", "Forecast ID", "Model Version",
    "Engine ID", "Market Status", "Best Offer Side", "Books At Line", "Candidate Offers")
BOARD_SHEETS = ("Full Board", "60 Percent Plus", "65 Percent Plus", "Model Coverage")
VALID_MARKET = "VALID_MARKET"
PCT = "0.0%"
BOARD_FORMATS = {"Model Projection": "0.00", "Best Market Line": "0.0", "Model Probability": PCT, "Push Probability": PCT,
                 "Fair Market Probability": PCT, "Probability Edge": PCT, "Model Fair Odds": "0",
                 "Best American Odds": "0", "Best Decimal Odds": "0.000", "Preferred EV": "0.000",
                 "Consensus Line": "0.0", "Consensus Over Odds": "0", "Consensus Under Odds": "0",
                 "Consensus Fair Over %": PCT, "Consensus Fair Under %": PCT, "Quote Age": "0"}

GRADED = ("WIN", "LOSS", "PUSH")
RESULTS = ("WIN", "LOSS", "PUSH", "VOID_DNP", "VOID_NO_GAME", "PENDING")
CLV_STATUSES = ("EXACT_LINE", "SAME_LINE_NEAREST_CLOSE", "LINE_MOVED", "NO_CLOSE_QUOTE", "NO_DEVIG_PAIR")
CLV_COMPUTED = ("EXACT_LINE", "SAME_LINE_NEAREST_CLOSE")
ROW_COLS = (
    "eval_key", "slate_date", "game_id", "player_id", "team_id", "game", "player", "team", "opponent", "player_role",
    "expected_minutes", "stat", "tip_utc", "cutoff_utc", "forecast_id", "model_version", "engine_id",
    "prediction_as_of", "projection", "side", "preferred_side", "line", "book", "american_odds", "decimal_odds", "model_probability",
    "push_probability", "fair_probability", "edge", "ev", "actionable", "tier", "books_at_line", "quote_received_at",
    "entry_received_at", "entry_decimal_odds", "entry_fair_probability", "close_ts", "close_book", "close_line",
    "close_fair_probability", "probability_clv", "log_odds_clv", "raw_line_movement", "directional_line_movement",
    "clv_status", "actual", "minutes", "result", "units", "close_window_closed", "content_hash")
METRICS = ("N", "Graded", "Wins", "Losses", "Pushes", "VOID_DNP", "VOID_NO_GAME", "Pending", "Hit Rate", "Win %",
           "Mean Model Probability", "Actual Hit Rate", "Calibration Gap", "Brier Score", "Log Loss", "Mean Edge",
           "Median Edge", "Mean EV", "Units Won", "ROI", "Mean Probability CLV", "Median Probability CLV",
           "Mean Log-Odds CLV", "Positive CLV %", "Exact-Line CLV N", "Line-Moved N", "No-Close N", "No-Devig-Pair N")
GRADE_METRICS = ("N", "Graded", "Wins", "Losses", "Pushes", "VOID_DNP", "Hit Rate", "Mean Model Probability",
                 "Realized Hit Rate", "Calibration Gap", "Brier Score", "Log Loss", "Mean Edge", "Mean EV", "Units Won",
                 "ROI", "Mean Probability CLV", "Positive CLV %")
METRIC_FORMATS = {**{c: PCT for c in ("Hit Rate", "Win %", "Mean Model Probability", "Actual Hit Rate",
                                      "Realized Hit Rate", "Calibration Gap", "Mean Edge", "Median Edge",
                                      "Positive CLV %", "ROI", "Mean Probability CLV", "Median Probability CLV",
                                      "Model Probability", "Push Probability", "Fair Market Probability",
                                      "Probability Edge", "Entry Fair Probability", "Close Fair Probability",
                                      "Probability CLV", "Mean Predicted Probability")},
                  "Brier Score": "0.0000", "Log Loss": "0.0000", "Mean EV": "0.000", "Units Won": "0.000",
                  "Mean Log-Odds CLV": "0.0000", "Log-Odds CLV": "0.0000", "EV": "0.000", "Units": "0.000",
                  "Line": "0.0", "Close Line": "0.0", "Decimal Odds": "0.000", "American Odds": "0",
                  "Directional Line Movement": "0.0", "Actual": "0.##", "Minutes": "0.0"}
GRADED_COLS = (
    "Slate Date", "Game", "Tip Time ET", "Player", "Team", "Opponent", "Player Role", "Stat", "Side", "Preferred Side",
    "Line", "Book", "American Odds", "Decimal Odds", "Model Probability", "Push Probability", "Fair Market Probability",
    "Probability Edge", "EV", "Is Actionable", "Reporting Tier", "Actual", "Minutes", "Result", "Units",
    "Entry Timestamp", "Entry Fair Probability", "Close Timestamp", "Close Book", "Close Line",
    "Close Fair Probability", "Probability CLV", "Log-Odds CLV", "Directional Line Movement", "CLV Status",
    "Canonical Game ID", "Canonical Player ID", "Forecast ID", "Model Version", "Engine ID", "Evaluation Hash")
GRADE_SHEETS = ("Graded Predictions", "By Stat", "By Player Role", "Stat x Role", "By Side", "By Reporting Tier",
                "By Engine", "Status")
STATUS_COLS = ("GRADE_STATUS", "GRADED_SLATE_DATE", "MARKET_PREDICTIONS", "GRADED", "VOID_DNP", "PENDING",
               "GAMES_FROZEN", "SOURCE")
GROUPS = {"By Stat": ("stat",), "By Player Role": ("player_role",), "Stat x Role": ("stat", "player_role"),
          "By Side": ("side",), "By Reporting Tier": ("tier",), "By Engine": ("engine_id",)}
GROUP_LABELS = {"stat": "Stat", "player_role": "Player Role", "side": "Side", "tier": "Reporting Tier",
                "engine_id": "Engine ID"}


class MarketExportIntegrityError(RuntimeError):
    """An invariant of the market export contract failed (duplicate key, altered frozen evaluation, ...)."""


# ============================================================================ helpers
def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _utc(x):
    if x is None or (not isinstance(x, (pd.Timestamp, str)) and pd.isna(x)):
        return None
    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    if pd.isna(t):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(t):
    t = _utc(t)
    return None if t is None else t.isoformat()


def _et(t, fmt="%Y-%m-%d %H:%M"):
    t = _utc(t)
    return None if t is None else t.tz_convert(TZ).strftime(fmt)


def _hash(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def _clean(v):
    if isinstance(v, (np.floating, float)):
        return None if not math.isfinite(float(v)) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def player_role(expected_minutes) -> str:
    from cbb_platform.prod import exports as EX
    return EX.player_role(expected_minutes)


def profit(result: str, american):
    """One unit staked: WIN pays the American price, LOSS -1, PUSH 0 (refund); otherwise None."""
    o = _f(american)
    if result == "WIN" and o is not None:
        return o / 100.0 if o > 0 else 100.0 / abs(o)
    if result == "LOSS":
        return -1.0
    if result == "PUSH":
        return 0.0
    return None


# ============================================================================ Board
def _quote_ts(b: dict):
    return _utc(b.get("market_last_update")) or _utc(b.get("collector_received_at"))


def board(pmfs: list, obs: dict, names: dict, ctx: dict, at) -> tuple[list[list], list[dict], list[dict], dict]:
    """(rows in BOARD_COLS order, market evaluation bodies, audit rows, counts). One row per persisted (game, player,
    stat) PMF; market fields from the game's latest pre-cutoff observation (obs: prod.quote_obs.latest)."""
    at = _utc(at)
    teams: dict = {}
    for r in pmfs:
        teams.setdefault(r["game_id"], set()).add(r["team_id"])
    grouped: dict = {}
    for gid, (meta, q) in obs.items():
        if len(q):
            q = q.assign(stat=q["stat"].astype(object))
            ok = q["canonical_player_id"].map(lambda v: isinstance(v, str)) & q["stat"].map(lambda v: isinstance(v, str)) \
                & (q["canonical_game_id"] == gid)
            for key, g in q[ok].groupby(["canonical_player_id", "stat"], sort=True):
                grouped[(gid,) + key] = g
    rows, evals, audit = [], [], []
    seen: dict = {}
    far = pd.Timestamp.max.tz_localize("UTC")
    for r in sorted(pmfs, key=lambda x: (_utc(x["tip"]) or far, x["game_label"], x["player_label"], x["stat"],
                                         x["player_id"])):
        k = (r["game_id"], r["player_id"], r["stat"])
        seen[k] = seen.get(k, 0) + 1
        g = grouped.get(k)
        ev = ME.evaluate(r["pmf"], g if g is not None else pd.DataFrame())
        audit += ev["audit"]
        meta = obs.get(r["game_id"], (None, None))[0]
        status = VALID_MARKET if ev["market"] else ("NO_QUOTE_OBSERVATION" if meta is None else
                                                    "NO_SUPPORTED_PROP_MARKETS_POSTED" if not meta.get("rows") else
                                                    "NO_VALID_MARKET_FOR_PLAYER_STAT")
        tip, cut = _utc(r["tip"]), _utc(r.get("cutoff"))
        pc = ctx.get((r.get("forecast_id"), r["player_id"])) or {}
        mins = _f(pc.get("min_mu"))
        role = player_role(mins)
        opp = sorted(t for t in teams.get(r["game_id"], set()) if t != r["team_id"])
        opp_name = names.get(opp[0], opp[0]) if len(opp) == 1 else None
        team = names.get(r["team_id"], r["team_id"])
        c, b = ev["consensus"], ev["best"]
        slate = None if tip is None else str(tip.tz_convert(TZ).date())
        qts = None if b is None else _quote_ts(b)
        age = None if b is None or b["quote_age_seconds"] is None else int(round(b["quote_age_seconds"]))   # at capture
        mfo = None if b is None else _f(b["model_fair_american"])
        rows.append([
            slate, r["game_id"], r["game_label"], _et(tip), _et(cut), r["player_label"], r["player_id"], team, opp_name,
            role, r["stat"], _f(r["mean"]), None if b is None else b["line"], ev["preferred_side"],
            None if b is None else b["model_p"], None if b is None else b["p_push"], None if b is None else b["fair_p"],
            None if b is None else b["edge"], None if mfo is None else int(round(mfo)), None if b is None else b["book"],
            None if b is None else ME.decimal_to_american(b["decimal"]) if b["american"] is None else int(round(b["american"])),
            None if b is None else b["decimal"], None if b is None else b["ev"],
            None if b is None else ("YES" if ev["actionable"] else "NO"), ev["tier"],
            None if b is None else ev["actionability_status"], _f(c["consensus_line"]),
            _f(c["consensus_over_odds"]), _f(c["consensus_under_odds"]), _f(c["market_over_prob"]),
            _f(c["market_under_prob"]), c["raw_book_count"], c["consensus_book_count"], c["outlier_book_count"],
            _iso(qts), age, r.get("forecast_id"), r.get("model_version"),
            ENG.attribute(r.get("engine_id"), r.get("prediction_as_of")), status, None if b is None else b["side"],
            ev["books_at_line"], ev["candidates"]])
        if b is not None:
            body = {"eval_version": ME.EVAL_VERSION, "slate_date": slate, "game_id": r["game_id"],
                    "player_id": r["player_id"], "team_id": r["team_id"], "stat": r["stat"], "game": r["game_label"],
                    "player": r["player_label"], "team": team, "opponent": opp_name, "player_role": role,
                    "expected_minutes": mins, "tip_utc": _iso(tip), "cutoff_utc": _iso(cut),
                    "forecast_id": r.get("forecast_id"), "source_kind": r.get("source_kind"),
                    "model_version": r.get("model_version"),
                    "engine_id": ENG.attribute(r.get("engine_id"), r.get("prediction_as_of")),
                    "prediction_as_of": _iso(r.get("prediction_as_of")),
                    "projection": _f(r["mean"]), "side": b["side"], "preferred_side": ev["preferred_side"],
                    "line": b["line"], "book": b["book"],
                    "american_odds": rows[-1][BOARD_COLS.index("Best American Odds")], "decimal_odds": b["decimal"],
                    "model_probability": b["model_p"], "push_probability": b["p_push"], "fair_probability": b["fair_p"],
                    "edge": b["edge"], "ev": b["ev"], "actionable": bool(ev["actionable"]), "tier": ev["tier"],
                    "screen_pass": bool(ev["screen_pass"]), "actionability_status": ev["actionability_status"],
                    "reliable": bool(ev["reliable"]), "books_at_line": ev["books_at_line"],
                    "candidate_offers": ev["candidates"], "quote_received_at": _iso(b["collector_received_at"]),
                    "market_last_update": _iso(b["market_last_update"]),
                    "observation_received_at": None if meta is None else meta.get("received_at"),
                    "observation_file": None if meta is None else meta.get("file"),
                    "consensus_line": _f(c["consensus_line"]), "consensus_confidence": c["consensus_confidence"],
                    "candidate_offer_schema": ME.OFFER_SET_SCHEMA, "candidate_offer_set": ev["offer_set"]}
            evals.append(json.loads(json.dumps({k: _clean(v) for k, v in body.items()}, default=str)))
    keys = set(seen)
    for gid, (meta, q) in obs.items():                      # quotes that never reached a Board key
        if not len(q):
            continue
        from cbb_platform.prod import consensus as CN
        ok = q["canonical_player_id"].map(lambda v: isinstance(v, str)) & q["stat"].map(lambda v: isinstance(v, str)) & \
            (q["canonical_game_id"] == gid)
        inkey = pd.Series([(gid, p, s) in keys for p, s in zip(q["canonical_player_id"], q["stat"])], index=q.index)
        for x in q[~ok | ~inkey].to_dict(orient="records"):
            why = CN.row_exclusions(pd.DataFrame([x])).iat[0] or "NO_FORECAST_FOR_PLAYER_STAT"
            audit.append({**{c: x.get(c) for c in CN.AUDIT_COLS if c in x}, "canonical_game_id": gid,
                          "quote_age_seconds": None, "status": "EXCLUDED", "reason": why})
    dup = sorted(list(k) for k, n in seen.items() if n > 1)
    i_st, i_side, i_tier, i_act = (BOARD_COLS.index(c) for c in ("Market Status", "Preferred Side", "Reporting Tier",
                                                                 "Is Actionable"))
    market = [x for x in rows if x[i_st] == VALID_MARKET]
    games = {r["game_id"] for r in pmfs}
    games_props = {x[1] for x in market}
    counts = {"rows": len(rows), "unique_keys": len(seen), "duplicate_keys": len(dup), "duplicates": dup[:20],
              "model_player_stat_rows": len(rows), "valid_market": len(market),
              "model_only_rows": len(rows) - len(market), "games": len(games), "games_with_props": len(games_props),
              "games_without_props": len(games - games_props),
              "full_board_rows": len(market), "over_rows": sum(1 for x in market if x[i_side] == "OVER"),
              "under_rows": sum(1 for x in market if x[i_side] == "UNDER"),
              "no_positive_ev_rows": sum(1 for x in market if x[i_side] == ME.NO_POSITIVE_EV),
              "actionable_rows": sum(1 for x in market if x[i_act] == "YES"),
              "screen_pass_rows": sum(1 for x in market if x[i_tier] in (ME.TIER_65, ME.TIER_60, ME.TIER_ACT)),
              "actionability_status": ME.ACTIONABILITY_STATUS,
              "plus60_rows": sum(1 for x in market if x[i_tier] in (ME.TIER_65, ME.TIER_60)),
              "plus65_rows": sum(1 for x in market if x[i_tier] == ME.TIER_65),
              "tier_65_plus": sum(1 for x in market if x[i_tier] == ME.TIER_65),
              "tier_60_plus": sum(1 for x in market if x[i_tier] == ME.TIER_60),
              "tier_actionable": sum(1 for x in market if x[i_tier] == ME.TIER_ACT),
              "tier_pass_no_edge": sum(1 for x in market if x[i_tier] == ME.TIER_PASS),
              "audit_rows": len(audit), "audit_excluded": sum(1 for a in audit if a["status"] == "EXCLUDED")}
    return rows, evals, audit, counts


def assert_unique(rows: list[list]) -> None:
    idx = [BOARD_COLS.index(c) for c in BOARD_KEY]
    seen = set()
    for r in rows:
        k = tuple(r[i] for i in idx)
        if k in seen:
            raise MarketExportIntegrityError(f"duplicate Board key game x player x stat {k}")
        seen.add(k)


def board_sheets(rows: list[list]) -> dict:
    i_st, i_tier = BOARD_COLS.index("Market Status"), BOARD_COLS.index("Reporting Tier")
    market = [r for r in rows if r[i_st] == VALID_MARKET]
    return {"Full Board": market,
            "60 Percent Plus": [r for r in market if r[i_tier] in (ME.TIER_65, ME.TIER_60)],
            "65 Percent Plus": [r for r in market if r[i_tier] == ME.TIER_65],
            "Model Coverage": [r for r in rows if r[i_st] != VALID_MARKET]}


def boards_workbook(rows: list[list], path: Path) -> Path:
    from cbb_platform.prod import exports as EX
    sh = board_sheets(rows)
    return EX.write_xlsx(path, [{"name": s, "blocks": [{"title": None, "columns": BOARD_COLS, "rows": sh[s],
                                                        "formats": BOARD_FORMATS}]} for s in BOARD_SHEETS])


def ingestion_totals(ops_root: Path, day: str) -> dict:
    """Today's quote-ingestion telemetry (prod.quote_refresh ingestion receipts): calls, credits, quota headers."""
    recs = OS.read_jsonl(Path(ops_root) / "quotes" / day / "ingestion_receipts.jsonl")
    last = recs[-1] if recs else {}

    def tot(k):
        return int(sum(int(r.get(k) or 0) for r in recs))
    return {"ingestion_runs": len(recs), "event_discovery_calls": tot("event_discovery_calls"),
            "market_discovery_calls": tot("market_discovery_calls"), "event_odds_calls": tot("event_odds_calls"),
            "raw_quote_observations": tot("quote_rows_returned"), "matched_quotes": tot("player_quotes_matched"),
            "unmatched_player_quotes": tot("player_quotes_unmatched"),
            "unmatched_game_events": len({g for r in recs for g in r.get("unmatched_games") or []}),
            "unmatched_events_listed": tot("events_unmatched"),
            "credits_consumed": tot("credits_this_run"), "quota_remaining": last.get("quota_remaining"),
            "quota_used": last.get("quota_used")}


# ============================================================================ observations (entry / close)
class Observations:
    """quote_obs index of one ET date; per observation file a contract map (VALID, non-alternate rows only)."""

    def __init__(self, ops_root: Path, day: str):
        self.dir = Path(ops_root) / "quotes" / day
        self.index = OS.read_jsonl(self.dir / "observations.jsonl")
        self._maps: dict = {}

    def of_game(self, game_id: str) -> list[dict]:
        return sorted([r for r in self.index if r["game_id"] == game_id],
                      key=lambda r: (_utc(r["received_at"]), r["source"]))

    def frame(self, ob: dict) -> pd.DataFrame:
        f = ob.get("file")
        return pd.read_parquet(self.dir / f) if f else pd.DataFrame(columns=__import__(
            "cbb_platform.prod.quote_obs", fromlist=["KEEP"]).KEEP)

    def contracts(self, ob: dict) -> dict:
        """(player_id, stat, book, line, side) -> decimal (latest market_last_update per contract)."""
        f = ob.get("file")
        if not f:
            return {}
        if f not in self._maps:
            q = pd.read_parquet(self.dir / f)
            m: dict = {}
            # ME.eligible dedups ONE player x stat per (book, line, side): applied to the whole observation it kept one
            # player per (book, line, side) and dropped every other player's contract (2026-11-21: all 353 player
            # NO_CLOSE_QUOTE rows 11-17..11-20 were this collision; same rule as the Board's per-player grouping).
            if len(q):
                q = q.assign(stat=q["stat"].astype(object))
                ok = q["canonical_player_id"].map(lambda v: isinstance(v, str)) & q["stat"].map(lambda v: isinstance(v, str))
                for _, g in q[ok].groupby(["canonical_player_id", "stat"], sort=True):
                    for o in ME.eligible(g):
                        m[(o["canonical_player_id"], str(o["stat"]), o["_book"], o["_line"], o["side"])] = o["_d"]
            self._maps[f] = m
        return self._maps[f]


def _fair(m: dict, pid, stat, book, line, side):
    other = "UNDER" if side == "OVER" else "OVER"
    return ME.no_vig(m.get((pid, stat, book, line, side)), m.get((pid, stat, book, line, other)))


def closing(obs: Observations, e: dict) -> dict:
    """Closing evidence of one graded evaluation (exactly one CLV status; nothing fabricated)."""
    out = {"close_ts": None, "close_book": None, "close_line": None, "close_fair_probability": None,
           "probability_clv": None, "log_odds_clv": None, "raw_line_movement": None, "directional_line_movement": None,
           "clv_status": "NO_CLOSE_QUOTE"}
    tip, cut = _utc(e.get("tip_utc")), _utc(e.get("cutoff_utc"))
    end = min([t for t in (tip, cut) if t is not None], default=None)
    if end is None:
        return out
    lo = end - pd.Timedelta(minutes=CLOSE_MAX_STALE_MINUTES)
    start = _utc(e.get("entry_received_at")) or _utc(e.get("prediction_as_of"))
    pid, stat, book, line, side = e["player_id"], e["stat"], e["book"], float(e["line"]), e["side"]
    win = [ob for ob in obs.of_game(e["game_id"])
           if lo <= _utc(ob["received_at"]) <= end and (start is None or _utc(ob["received_at"]) >= start)]
    win = [(ob, obs.contracts(ob)) for ob in reversed(win)]          # latest first
    win = [(ob, m) for ob, m in win if any(k[0] == pid and k[1] == stat for k in m)]
    if not win:
        return out
    latest_ob, latest = win[0]

    def take(ob, m, bk, ln, status):
        out.update(close_ts=_iso(ob["received_at"]), close_book=bk, close_line=ln,
                   close_fair_probability=_fair(m, pid, stat, bk, ln, side), raw_line_movement=ln - line,
                   directional_line_movement=(ln - line) if side == "OVER" else (line - ln), clv_status=status)
    pick = None
    if (pid, stat, book, line, side) in latest:
        pick = (latest_ob, latest, book, "EXACT_LINE")
    else:
        same = [(ob, m) for ob, m in win if (pid, stat, book, line, side) in m]
        if same:
            pick = (same[0][0], same[0][1], book, "SAME_LINE_NEAREST_CLOSE")
        else:
            other = sorted(k[2] for k in latest if k[:2] == (pid, stat) and k[3] == line and k[4] == side)
            if other:
                pick = (latest_ob, latest, other[0], "SAME_LINE_NEAREST_CLOSE")
    if pick is not None:
        ob, m, bk, status = pick
        take(ob, m, bk, line, status)
        ef, cf = _f(e.get("entry_fair_probability")), out["close_fair_probability"]
        if ef is None or cf is None or not 0 < ef < 1 or not 0 < cf < 1:
            out["clv_status"] = "NO_DEVIG_PAIR"
        else:
            out["probability_clv"] = cf - ef
            out["log_odds_clv"] = _logit(cf) - _logit(ef)
        return out
    lines = sorted({k[3] for k in latest if k[:2] == (pid, stat) and k[4] == side and k[2] == book}) or \
        sorted({k[3] for k in latest if k[:2] == (pid, stat) and k[4] == side})
    if not lines:
        return out
    ln = min(lines, key=lambda x: (abs(x - line), x))
    bks = sorted(k[2] for k in latest if k[:2] == (pid, stat) and k[3] == ln and k[4] == side)
    take(latest_ob, latest, book if book in bks else bks[0], ln, "LINE_MOVED")
    out["close_fair_probability"] = None                              # a moved line is never probability CLV
    return out


# ============================================================================ frozen evaluations
def eval_path(ops_root: Path, day: str) -> Path:
    return Path(ops_root) / EVAL_DIR / day / EVAL_FILE


def eval_key(e: dict) -> tuple:
    return e["game_id"], e["player_id"], e["stat"]


def load_evaluations(path: Path) -> tuple[dict, dict]:
    """(key -> MARKET_EVALUATION record, game_id -> GAME_FROZEN record); hashes verified, write-once enforced."""
    evals, games = {}, {}
    for r in OS.read_jsonl(path):
        body = {k: v for k, v in r.items() if k not in ("record_type", "content_hash")}
        if _hash(body) != r.get("content_hash"):
            raise MarketExportIntegrityError(f"{path}: {r.get('record_type')} record altered")
        if r.get("record_type") == "MARKET_EVALUATION":
            k = eval_key(r)
            if k in evals or k[0] in games:
                raise MarketExportIntegrityError(f"{path}: evaluation {k} written twice / after its game froze")
            evals[k] = r
        elif r.get("record_type") == "GAME_FROZEN":
            if r["game_id"] in games:
                raise MarketExportIntegrityError(f"{path}: game {r['game_id']} frozen twice")
            games[r["game_id"]] = r
    return evals, games


def decision_observation(obs: Observations, game_id: str, prediction_as_of, cutoff) -> dict | None:
    """The first observation received at or after the PMF's prediction instant and strictly before the cutoff; else the
    latest one before the cutoff (the decision uses the latest quote available when the forecast was made)."""
    cut, aso = _utc(cutoff), _utc(prediction_as_of)
    pre = [ob for ob in obs.of_game(game_id) if cut is None or _utc(ob["received_at"]) < cut]
    if not pre:
        return None
    after = [ob for ob in pre if aso is not None and _utc(ob["received_at"]) >= aso]
    return after[0] if after else pre[-1]


def freeze(ops_root: Path, day: str, pmfs: list, names: dict, ctx: dict, cutoffs: dict, now) -> dict:
    """Write the frozen evaluations of every game of ET date `day` whose cutoff has passed (once per game).
    pmfs: the latest pre-cutoff PMF rows (exports.distribution_records); cutoffs: game_id -> legal cutoff (games
    without PMFs still get a GAME_FROZEN marker)."""
    path = eval_path(ops_root, day)
    have, games = load_evaluations(path)
    now = _utc(now)
    obs = Observations(ops_root, day)
    by: dict = {}
    for r in pmfs:
        by.setdefault(r["game_id"], []).append(r)
    written, frozen_games, out = 0, [], []
    for gid in sorted(set(by) | set(cutoffs)):
        cut = _utc(cutoffs.get(gid))
        if gid in games or cut is None or now < cut:
            continue
        rows = by.get(gid, [])
        aso = max((_utc(r.get("prediction_as_of")) for r in rows if _utc(r.get("prediction_as_of")) is not None),
                  default=None)
        ob = decision_observation(obs, gid, aso, cut)
        q = obs.frame(ob) if ob is not None else None
        brows, evals, _, _ = board(rows, {} if ob is None else {gid: (ob, q)}, names, ctx,
                                   cut if ob is None else _utc(ob["received_at"])) if rows else ([], [], [], {})
        recs = sorted(evals, key=lambda e: (e["player_id"], e["stat"]))
        for e in recs:
            if eval_key(e) in have:
                raise MarketExportIntegrityError(f"{gid}: evaluation {eval_key(e)} exists but the game is not frozen")
            body = {"record_type": "MARKET_EVALUATION", **e, "entry_received_at": e["observation_received_at"],
                    "entry_decimal_odds": e["decimal_odds"], "entry_fair_probability": e["fair_probability"],
                    "frozen_at": now.isoformat()}
            out.append({**body, "content_hash": _hash({k: v for k, v in body.items() if k != "record_type"})})
        g = {"game_id": gid, "slate_date": day, "cutoff_utc": cut.isoformat(), "frozen_at": now.isoformat(),
             "market_rows": len(recs), "model_only_rows": len(brows) - len(recs),
             "pmf_prediction_as_of": _iso(aso), "decision_observation": None if ob is None else ob.get("file"),
             "decision_received_at": None if ob is None else ob["received_at"]}
        out.append({"record_type": "GAME_FROZEN", **g, "content_hash": _hash(g)})
        written += len(recs)
        frozen_games.append(gid)
    if out:
        OS.append_jsonl(path, out)
    return {"path": str(path), "written": written, "games_frozen_now": len(frozen_games),
            "games_frozen_total": len(games) + len(frozen_games), "evaluations_total": len(have) + written}


# ============================================================================ grading
def actuals(ops_root: Path, game_ids) -> dict:
    """(game_id, player_id) -> final box row (prod.shadow_tick settlement: participation, minutes, act_<stat>)."""
    want = set(game_ids)
    out: dict = {}
    if not want:
        return out
    for p in sorted(Path(ops_root, "ticks").glob("*/actuals.parquet")):
        a = pd.read_parquet(p)
        a = a[a["game_id"].isin(want)]
        for r in a.to_dict(orient="records"):
            out.setdefault((r["game_id"], r["player_id"]), r)          # append-only: settled once
    return out


def grade(e: dict, act: dict) -> dict:
    a = act.get((e["game_id"], e["player_id"]))
    if a is None:
        return {"actual": None, "minutes": None, "result": "PENDING", "units": None}
    part, mins = a.get("participation"), _f(a.get("minutes"))
    if part == "GAME_NOT_PLAYED":
        return {"actual": None, "minutes": mins, "result": "VOID_NO_GAME", "units": None}
    if part != "PLAYED" or not mins:
        return {"actual": None, "minutes": mins, "result": "VOID_DNP", "units": None}
    y = _f(a.get(f"act_{e['stat']}"))
    if y is None:
        return {"actual": None, "minutes": mins, "result": "PENDING", "units": None}
    line = float(e["line"])
    res = "PUSH" if y == line else ("WIN" if (y > line) == (e["side"] == "OVER") else "LOSS")
    return {"actual": y, "minutes": mins, "result": res, "units": profit(res, e["american_odds"])}


def graded_row(e: dict, act: dict, obs: Observations, now) -> dict:
    row = {k: e.get(k) for k in ROW_COLS if k in e}
    row["engine_id"] = ENG.attribute(e.get("engine_id"), e.get("prediction_as_of"))   # frozen before attribution
    row.update(eval_key="|".join(eval_key(e)) + "|" + e["slate_date"], content_hash=e["content_hash"])
    row.update(closing(obs, e))
    row.update(grade(e, act))
    end = min([t for t in (_utc(e.get("tip_utc")), _utc(e.get("cutoff_utc"))) if t is not None], default=None)
    row["close_window_closed"] = bool(end is not None and _utc(now) >= end)
    return {c: _clean(row.get(c)) for c in ROW_COLS}


def final(row: dict) -> bool:
    return row["result"] in GRADED + ("VOID_DNP", "VOID_NO_GAME") and bool(row["close_window_closed"])


def graded_rows(ops_root: Path, day: str, now, reuse: dict | None = None) -> tuple[list[dict], dict]:
    """Graded rows of the frozen evaluations of ET date `day` (reuse: eval_key -> stored FINAL row of the same hash)."""
    evals, games = load_evaluations(eval_path(ops_root, day))
    if not evals:
        return [], {"games_frozen": len(games), "evaluations": 0}
    act = actuals(ops_root, {k[0] for k in evals})
    obs = Observations(ops_root, day)
    rows, reused = [], 0
    for k in sorted(evals):
        e = evals[k]
        key = "|".join(k) + "|" + day
        prev = (reuse or {}).get(key)
        if prev is not None and prev["content_hash"] == e["content_hash"] and final(prev):
            if not prev.get("engine_id"):                        # stored before attribution
                prev = {**prev, "engine_id": ENG.attribute(e.get("engine_id"), e.get("prediction_as_of"))}
            rows.append(prev)
            reused += 1
            continue
        rows.append(graded_row(e, act, obs, now))
    return rows, {"games_frozen": len(games), "evaluations": len(evals), "reused": reused}


# ============================================================================ metrics (Grades == Performance)
def _m(v):
    x = [float(a) for a in v if _f(a) is not None]
    return sum(x) / len(x) if x else None


def _med(v):
    x = [float(a) for a in v if _f(a) is not None]
    return float(np.median(x)) if x else None


def metrics(rows: list[dict]) -> dict:
    res = [r["result"] for r in rows]
    w, l, p = res.count("WIN"), res.count("LOSS"), res.count("PUSH")
    dec = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    gr = [r for r in rows if r["result"] in GRADED]
    pr = [float(r["model_probability"]) for r in dec]
    y = [1.0 if r["result"] == "WIN" else 0.0 for r in dec]
    win = w / (w + l) if w + l else None
    mp = _m(pr)
    pc = np.clip(np.asarray(pr, float), EPS, 1 - EPS)
    yy = np.asarray(y, float)
    clv = [r["probability_clv"] for r in gr if r["clv_status"] in CLV_COMPUTED and _f(r["probability_clv"]) is not None]
    units = sum(float(r["units"]) for r in gr if _f(r["units"]) is not None)
    st = [r["clv_status"] for r in gr]
    return {"N": len(rows), "Graded": len(gr), "Wins": w, "Losses": l, "Pushes": p, "VOID_DNP": res.count("VOID_DNP"),
            "VOID_NO_GAME": res.count("VOID_NO_GAME"), "Pending": res.count("PENDING"),
            "Hit Rate": w / len(gr) if gr else None, "Win %": win, "Mean Model Probability": mp,
            "Actual Hit Rate": win, "Realized Hit Rate": win,
            "Calibration Gap": None if win is None or mp is None else win - mp,
            "Brier Score": float(np.mean((np.asarray(pr) - yy) ** 2)) if dec else None,
            "Log Loss": float(-np.mean(yy * np.log(pc) + (1 - yy) * np.log(1 - pc))) if dec else None,
            "Mean Edge": _m(r["edge"] for r in gr), "Median Edge": _med(r["edge"] for r in gr),
            "Mean EV": _m(r["ev"] for r in gr), "Units Won": units if gr else None,
            "ROI": units / (w + l) if w + l else None,
            "Mean Probability CLV": _m(clv), "Median Probability CLV": _med(clv),
            "Mean Log-Odds CLV": _m(r["log_odds_clv"] for r in gr if r["clv_status"] in CLV_COMPUTED),
            "Positive CLV %": sum(1 for c in clv if c > 0) / len(clv) if clv else None,
            "Exact-Line CLV N": st.count("EXACT_LINE"), "Line-Moved N": st.count("LINE_MOVED"),
            "No-Close N": st.count("NO_CLOSE_QUOTE"), "No-Devig-Pair N": st.count("NO_DEVIG_PAIR")}


def _taxonomy(key: str) -> tuple:
    """The fixed categories shown even with zero rows (rates blank), in canonical order."""
    from cbb_platform.prod import exports as EX
    return {"player_role": EX.ROLES, "side": ("OVER", "UNDER"), "tier": ME.TIERS}.get(key, ())


def groups(rows: list[dict], keys: tuple) -> list[tuple]:
    """(group key, rows) pairs: single-key taxonomies (role, side, tier) list every category (zero-count kept, canonical
    order, unexpected values after); everything else is the observed keys sorted alphabetically."""
    g: dict = {}
    for r in rows:
        g.setdefault(tuple(r[k] for k in keys), []).append(r)
    fixed = _taxonomy(keys[0]) if len(keys) == 1 else ()
    head = [((c,), g.pop((c,), [])) for c in fixed]
    return head + sorted(g.items(), key=lambda x: tuple(map(str, x[0])))


def group_rows(rows: list[dict], keys: tuple, cols: tuple) -> list[list]:
    return [list(k) + [metrics(v)[c] for c in cols] for k, v in groups(rows, keys)]


def graded_table_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in sorted(rows, key=lambda r: (r["tip_utc"] or "", r["game"] or "", r["player"] or "", r["stat"])):
        out.append([r["slate_date"], r["game"], _et(r["tip_utc"]), r["player"], r["team"], r["opponent"],
                    r["player_role"], r["stat"], r["side"], r["preferred_side"], r["line"], r["book"],
                    r["american_odds"], r["decimal_odds"], r["model_probability"], r["push_probability"],
                    r["fair_probability"], r["edge"], r["ev"], "YES" if r["actionable"] else "NO", r["tier"],
                    r["actual"], r["minutes"], r["result"], r["units"], r["entry_received_at"],
                    r["entry_fair_probability"], r["close_ts"], r["close_book"], r["close_line"],
                    r["close_fair_probability"], r["probability_clv"], r["log_odds_clv"],
                    r["directional_line_movement"], r["clv_status"], r["game_id"], r["player_id"], r["forecast_id"],
                    r["model_version"], r.get("engine_id"), r["content_hash"]])
    return out


def grade_status(rows: list[dict]) -> str:
    if not rows:
        return "NO_MARKET_PREDICTIONS"
    res = [r["result"] for r in rows]
    if res.count("PENDING") == len(res):
        return "NOTHING_SETTLED_YET"
    return "PARTIALLY_SETTLED" if "PENDING" in res else "SETTLED"


def grades_tables(rows: list[dict], graded_day: str, info: dict) -> list[dict]:
    m = metrics(rows)
    status = [grade_status(rows), graded_day, len(rows), m["Graded"], m["VOID_DNP"], m["Pending"],
              info.get("games_frozen", 0), f"frozen pre-cutoff market evaluations of {graded_day} "
                                           f"(ops/{EVAL_DIR}/{graded_day}/{EVAL_FILE})"]
    sheets = [{"name": "Graded Predictions", "blocks": [{"title": None, "columns": GRADED_COLS,
                                                         "rows": graded_table_rows(rows), "formats": METRIC_FORMATS}]}]
    for name, keys in GROUPS.items():
        sheets.append({"name": name, "blocks": [{"title": None, "columns": tuple(GROUP_LABELS[k] for k in keys)
                                                 + GRADE_METRICS, "rows": group_rows(rows, keys, GRADE_METRICS),
                                                 "formats": METRIC_FORMATS}]})
    sheets.append({"name": "Status", "blocks": [
        {"title": None, "columns": STATUS_COLS, "rows": [status]},
        {"title": "All Predictions", "columns": GRADE_METRICS, "rows": [[m[c] for c in GRADE_METRICS]],
         "formats": METRIC_FORMATS}]})
    return sheets


def prior_day(date: str) -> str:
    return str((pd.Timestamp(date) - pd.Timedelta(days=1)).date())
