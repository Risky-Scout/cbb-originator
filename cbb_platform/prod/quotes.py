"""Canonical sportsbook quote schema + normalizer for The Odds API v4 event-odds payloads (format as used by the NBA
props repo; NCAAB player-prop COVERAGE IS UNRESOLVED -- no call has been made). This module never performs a network
request: it normalizes payloads that a collector saved, with the collector's own received_at.

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
MARKET_MAP = {"player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST", "player_threes": "FG3M",
              "player_steals": "STL", "player_blocks": "BLK", "player_turnovers": "TOV",
              "player_points_rebounds_assists": "PRA", "player_points_rebounds": "PR", "player_points_assists": "PA",
              "player_rebounds_assists": "RA", "player_blocks_steals": "STOCKS"}
COLUMNS = ["provider", "sportsbook", "provider_event_id", "provider_home_team", "provider_away_team",
           "provider_commence_time", "market_key", "market_id", "is_alternate", "provider_player_name", "stat", "line",
           "side", "american_odds", "decimal_odds", "quote_available_at", "collector_received_at",
           "canonical_game_id", "canonical_player_id", "quote_status", "suppression_reason"]


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


def normalize_event(payload: dict, collector_received_at) -> pd.DataFrame:
    """One Odds API event-odds object -> canonical quote rows (unmatched)."""
    ev = payload.get("data", payload)
    rx = _utc(collector_received_at)
    rows = []
    for bk in ev.get("bookmakers", []) or []:
        for m in bk.get("markets", []) or []:
            key = str(m.get("key", ""))
            base = key[:-len("_alternate")] if key.endswith("_alternate") else key
            stat = MARKET_MAP.get(base)
            avail = _utc(m.get("last_update") or bk.get("last_update"))
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
                if status == "VALID" and (pd.isna(avail) or pd.isna(rx) or avail > rx):
                    status, reason = "INVALID", "INVALID_QUOTE"            # chronology: seen before it existed
                rows.append({"provider": PROVIDER, "sportsbook": bk.get("key"), "provider_event_id": ev.get("id"),
                             "provider_home_team": ev.get("home_team"), "provider_away_team": ev.get("away_team"),
                             "provider_commence_time": _utc(ev.get("commence_time")), "market_key": key,
                             "market_id": f"{ev.get('id')}|{bk.get('key')}|{key}", "is_alternate": key != base,
                             "provider_player_name": oc.get("description") or oc.get("participant") or "",
                             "stat": stat, "line": line, "side": side, "american_odds": oc.get("price"),
                             "decimal_odds": dec, "quote_available_at": avail, "collector_received_at": rx,
                             "canonical_game_id": None, "canonical_player_id": None, "quote_status": status,
                             "suppression_reason": reason})
    return pd.DataFrame(rows, columns=COLUMNS)


def load_collected(path: Path) -> pd.DataFrame:
    """A saved collection file: {"collector_received_at": ..., "provider": ..., "events": [event-odds payloads]}."""
    doc = json.loads(Path(path).read_text())
    if doc.get("provider", PROVIDER) != PROVIDER:
        raise ValueError(f"unsupported provider {doc.get('provider')}")
    parts = [normalize_event(e, doc["collector_received_at"]) for e in doc.get("events", [])]
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS)
    df["collection_label"] = doc.get("label", "")
    return df
