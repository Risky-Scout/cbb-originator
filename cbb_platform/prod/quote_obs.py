"""Persisted sportsbook quote observations: one per collection of one game (official tick snapshot or tiered quote
refresh), with EVERY normalized quote row (valid or not, with its reason), already strictly matched and freshness-checked
by the existing production code. The Board consensus (prod.exports -> prod.consensus) reads only these. No network.

    quotes/<ET date>/observations.jsonl              index: game, event, received_at, source, rows, discovery status
    quotes/<ET date>/observations/<game>_<received>.parquet   the rows (absent when the capture has zero rows)

A capture with zero rows (event advertised none of the 12 production markets, or the book pulled them) is still an
observation: the Board then shows the game's rows with NO_VALID_MARKET instead of an older market.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from cbb_platform.prod import ops_store as OS

INDEX = "observations.jsonl"
KEEP = ["provider_event_id", "sportsbook", "market_key", "is_alternate", "provider_player_name", "stat", "line", "side",
        "american_odds", "decimal_odds", "market_last_update", "bookmaker_last_update", "quote_available_at",
        "collector_received_at", "canonical_game_id", "canonical_player_id", "quote_status", "suppression_reason"]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def record(ops_root: Path, day: str, game_id: str, event_id: str, received_at, rows: pd.DataFrame, source: str,
           ref: dict | None = None) -> dict:
    """Persist one observation (idempotent per game x received_at x source)."""
    d = Path(ops_root) / "quotes" / day
    rx = _utc(received_at)
    idx = d / INDEX
    for r in OS.read_jsonl(idx):
        if r["game_id"] == game_id and r["received_at"] == rx.isoformat() and r["source"] == source:
            return r
    rows = rows if rows is not None else pd.DataFrame(columns=KEEP)
    x = rows.reindex(columns=KEEP).copy()
    for c in ("market_last_update", "bookmaker_last_update", "quote_available_at", "collector_received_at"):
        x[c] = pd.to_datetime(x[c], utc=True)
    x["american_odds"] = pd.to_numeric(x["american_odds"], errors="coerce")
    x["is_alternate"] = x["is_alternate"].map(lambda v: v is True or v == "True" or (isinstance(v, (int, float)) and v == 1))
    x = x.astype({c: object for c in ("canonical_game_id", "canonical_player_id", "suppression_reason")})
    f = None
    if len(x):
        f = f"observations/{game_id}_{rx.strftime('%Y%m%dT%H%M%S%fZ')}_{source}.parquet"
        (d / "observations").mkdir(parents=True, exist_ok=True)
        x.to_parquet(d / f, index=False)
    row = {"game_id": game_id, "event_id": event_id, "received_at": rx.isoformat(), "source": source, "rows": int(len(x)),
           "valid_rows": int((x["quote_status"] == "VALID").sum()) if len(x) else 0, "file": f, **(ref or {})}
    OS.append_jsonl(idx, [row])
    return row


def latest(ops_root: Path, day: str, cutoffs: dict, as_of) -> dict:
    """game_id -> (index row, rows frame) of the LATEST observation received at or before as_of and strictly before the
    game's cutoff (a capture after the cutoff never changes the Board of that game)."""
    d = Path(ops_root) / "quotes" / day
    best: dict = {}
    at = _utc(as_of)
    for r in OS.read_jsonl(d / INDEX):
        rx = _utc(r["received_at"])
        cut = cutoffs.get(r["game_id"])
        if rx > at or (cut is not None and rx >= _utc(cut)):
            continue
        if r["game_id"] not in best or (rx, r["source"]) > (_utc(best[r["game_id"]]["received_at"]), best[r["game_id"]]["source"]):
            best[r["game_id"]] = r
    out = {}
    for g, r in best.items():
        rows = pd.read_parquet(d / r["file"]) if r.get("file") else pd.DataFrame(columns=KEEP)
        out[g] = (r, rows)
    return out
