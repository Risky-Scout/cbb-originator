"""Canonical sportsbook quote schema + normalizer for The Odds API v4 event-odds payloads (sport `basketball_ncaab`).
This module never performs a network request: it normalizes payloads that the production collector
(cbb_platform/prod/odds_acquire.py) saved, with the collector's own received_at.

Market keys are NOT guessed. MARKET_KEYS is transcribed from The Odds API market-key documentation
(https://the-odds-api.com/sports-odds-data/betting-markets.html, read 2026-10-21):
  * section "NBA, NCAAB, WNBA Player Props API" documents every base key below for NCAAB;
  * section "Alternate NBA Player Props API" documents the *_alternate keys under an NBA-only heading, so for NCAAB they
    are DOCUMENTED_FOR_NCAAB = False: they are never requested, but a real alternate quote the provider returns is kept
    (it is an actual sportsbook quote; nothing is fabricated);
  * Blocks + Steals (STOCKS) has a base key and NO documented alternate key.
Player props are served one event at a time by GET /v4/sports/basketball_ncaab/events/{eventId}/odds. Any key not in
KEY_TO_STAT (including an undocumented *_alternate such as player_blocks_steals_alternate) is UNSUPPORTED_MARKET.

Decimal odds are the canonical internal odds. Every quote keeps provider identifiers verbatim; canonical ids are
attached only by cbb_platform/prod/matching.py (strict).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROVIDER = "the-odds-api"
SPORT_KEY = "basketball_ncaab"
MARKET_DOC_URL = "https://the-odds-api.com/sports-odds-data/betting-markets.html"
MARKET_DOC_READ = "2026-10-21"
# (model stat, base key, base documented for NCAAB, alternate key or None, alternate documented for NCAAB)
MARKET_KEYS = (
    ("PTS", "player_points", True, "player_points_alternate", False),
    ("REB", "player_rebounds", True, "player_rebounds_alternate", False),
    ("AST", "player_assists", True, "player_assists_alternate", False),
    ("FG3M", "player_threes", True, "player_threes_alternate", False),
    ("STL", "player_steals", True, "player_steals_alternate", False),
    ("BLK", "player_blocks", True, "player_blocks_alternate", False),
    ("TOV", "player_turnovers", True, "player_turnovers_alternate", False),
    ("PRA", "player_points_rebounds_assists", True, "player_points_rebounds_assists_alternate", False),
    ("PR", "player_points_rebounds", True, "player_points_rebounds_alternate", False),
    ("PA", "player_points_assists", True, "player_points_assists_alternate", False),
    ("RA", "player_rebounds_assists", True, "player_rebounds_assists_alternate", False),
    ("STOCKS", "player_blocks_steals", True, None, False),
)
KEY_TO_STAT = {**{b: s for s, b, _, _, _ in MARKET_KEYS}, **{a: s for s, _, _, a, _ in MARKET_KEYS if a}}
ALTERNATE_KEYS = frozenset(a for _, _, _, a, _ in MARKET_KEYS if a)
# What the collector requests: the documented NCAAB base keys only (cost = markets returned x regions).
REQUEST_MARKETS = tuple(b for _, b, doc, _, _ in MARKET_KEYS if doc)
MARKET_MAP = KEY_TO_STAT                                            # backwards-compatible name
COLUMNS = ["provider", "sportsbook", "sportsbook_title", "provider_event_id", "provider_home_team", "provider_away_team",
           "provider_commence_time", "market_key", "market_id", "is_alternate", "provider_player_name", "stat", "line",
           "side", "american_odds", "decimal_odds", "market_last_update", "bookmaker_last_update", "deep_link",
           "quote_available_at", "collector_received_at", "canonical_game_id", "canonical_player_id", "quote_status",
           "suppression_reason"]


TIME_COLS = ("provider_commence_time", "market_last_update", "bookmaker_last_update", "quote_available_at",
             "collector_received_at")
NUM_COLS = ("line", "decimal_odds")


def typed(df: pd.DataFrame) -> pd.DataFrame:
    """Stable dtypes whatever the rows (an event with no posted market yields no rows; concatenating it must not turn
    timestamps into objects)."""
    df = df.copy()
    for c in TIME_COLS:
        df[c] = pd.to_datetime(df[c], utc=True)
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c])
    return df


def market_mapping() -> pd.DataFrame:
    """The reportable stat -> documented key table (MODEL_STAT, ODDS_API_MARKET_KEY, DOCUMENTED_SUPPORTED, ALTERNATE_KEY)."""
    return pd.DataFrame([{"MODEL_STAT": s, "ODDS_API_MARKET_KEY": b, "DOCUMENTED_SUPPORTED": d,
                          "ALTERNATE_KEY": a or "", "ALTERNATE_DOCUMENTED_FOR_NCAAB": ad} for s, b, d, a, ad in MARKET_KEYS])


def _utc(x):
    if x in (None, "") or (isinstance(x, float) and np.isnan(x)):
        return pd.NaT
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def american_to_decimal(a: float) -> float:
    a = float(a)
    if a == 0 or (-100 < a < 100):
        raise ValueError(f"invalid American odds {a}")
    return 1.0 + a / 100.0 if a > 0 else 1.0 + 100.0 / abs(a)


def _link(*nodes) -> str:
    """Deepest deep link the provider returned (outcome > market > bookmaker; only with includeLinks=true)."""
    for n in nodes:
        v = (n or {}).get("link")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def normalize_event(payload: dict, collector_received_at) -> pd.DataFrame:
    """One Odds API event-odds object -> canonical quote rows (unmatched)."""
    ev = payload.get("data", payload)
    rx = _utc(collector_received_at)
    rows = []
    for bk in ev.get("bookmakers", []) or []:
        bk_upd = _utc(bk.get("last_update"))
        for m in bk.get("markets", []) or []:
            key = str(m.get("key", ""))
            stat = KEY_TO_STAT.get(key)
            m_upd = _utc(m.get("last_update"))
            avail = m_upd if not pd.isna(m_upd) else bk_upd           # the API documents last_update on the market
            for oc in m.get("outcomes", []) or []:
                side = str(oc.get("name") or "").strip().upper()
                status, reason, dec, line = "VALID", "", np.nan, np.nan
                if stat is None:
                    status, reason = "INVALID", "UNSUPPORTED_MARKET"
                else:
                    try:
                        line = float(oc.get("point"))
                        dec = american_to_decimal(oc.get("price"))
                    except (TypeError, ValueError):
                        status, reason = "INVALID", "INVALID_QUOTE"
                if status == "VALID" and (side not in ("OVER", "UNDER") or not np.isclose(line * 2, round(line * 2))
                                          or line < 0 or not dec > 1.0):
                    status, reason = "INVALID", "INVALID_QUOTE"
                if status == "VALID" and not str(oc.get("description") or "").strip():
                    status, reason = "INVALID", "INVALID_QUOTE"            # a player prop without a player
                if status == "VALID" and (pd.isna(avail) or pd.isna(rx) or avail > rx):
                    status, reason = "INVALID", "INVALID_QUOTE"            # chronology: seen before it existed
                rows.append({"provider": PROVIDER, "sportsbook": str(bk.get("key") or "").strip().lower(),
                             "sportsbook_title": str(bk.get("title") or "").strip(), "provider_event_id": ev.get("id"),
                             "provider_home_team": ev.get("home_team"), "provider_away_team": ev.get("away_team"),
                             "provider_commence_time": _utc(ev.get("commence_time")), "market_key": key,
                             "market_id": f"{ev.get('id')}|{bk.get('key')}|{key}", "is_alternate": key in ALTERNATE_KEYS,
                             "provider_player_name": str(oc.get("description") or oc.get("participant") or "").strip(),
                             "stat": stat, "line": line, "side": side, "american_odds": oc.get("price"),
                             "decimal_odds": dec, "market_last_update": m_upd, "bookmaker_last_update": bk_upd,
                             "deep_link": _link(oc, m, bk), "quote_available_at": avail, "collector_received_at": rx,
                             "canonical_game_id": None, "canonical_player_id": None, "quote_status": status,
                             "suppression_reason": reason})
    return typed(pd.DataFrame(rows, columns=COLUMNS))


def load_collected(path: Path) -> pd.DataFrame:
    """A saved collection file: {"collector_received_at": ..., "provider": ..., "events": [event-odds payloads]}.
    An event may carry its own "collector_received_at" (one live request per event); it overrides the file's."""
    doc = json.loads(Path(path).read_text())
    if doc.get("provider", PROVIDER) != PROVIDER:
        raise ValueError(f"unsupported provider {doc.get('provider')}")
    parts = [normalize_event(e, e.get("collector_received_at") or doc["collector_received_at"]) for e in doc.get("events", [])]
    df = typed(pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS))
    df["collection_label"] = doc.get("label", "")
    return df
