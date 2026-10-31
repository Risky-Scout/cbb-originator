"""NEAR-TIP QUOTE SNAPSHOT for CLV monitoring (architect finalization G). QUOTE SNAPSHOT ONLY: no model is rescored, no
price or probability is changed.

At T-10m (SNAPSHOT_MINUTES_BEFORE_TIP before the provider's listed commence time) each priced game's event odds are
captured once through the EXISTING acquisition path (odds_feed.collect -> odds_acquire.Session: production guards, per
stage budget, quota reserve, never at/after commence; a cached payload younger than ODDS_EVENT_ODDS_MAX_AGE_MINUTES is
reused instead of a new call) and normalized / matched by the existing quotes / matching code.

LABEL = NEAR_TIP_CLOSE_PROXY_T10M. It is the last quote this system observed about ten minutes before the scheduled tip,
NOT a verified final pregame (closing) quote: lines can still move after it, and a delayed tip moves the real close.
CLV against it is reported as "CLV vs near-tip proxy", never as CLV vs the market close.

Each snapshot row keeps: game, player, market, line, over / under price (decimal + American), sportsbook, the provider's
quote timestamp, the collector timestamp, and the model prediction(s) that preceded it (prediction id, version,
training cutoff, prediction time, P(over | no push) when the same line was priced).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, matching as MT, odds_feed as OF, quotes as QT
from cbb_platform.walkforward import monitor as MON, store as SO

SNAPSHOT_MINUTES_BEFORE_TIP = 10
LABEL = "NEAR_TIP_CLOSE_PROXY_T10M"
EXACT_MARKET_CLOSE = False
KEY = ["game_id", "player_id", "market", "line", "sportsbook"]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def paths(root: Path) -> dict:
    p = SO.paths(root)["predictions"]
    return {"events": p / "events.parquet", "snapshots": p / "near_tip_snapshots.parquet",
            "collections": p / "near_tip_collections"}


def due(root: Path, now) -> pd.DataFrame:
    """Priced games whose snapshot window [commence - 10m, commence) contains `now` and that have no snapshot yet."""
    pp = paths(root)
    if not pp["events"].exists():
        return pd.DataFrame(columns=["game_id", "provider_event_id", "provider_commence_time"])
    ev = pd.read_parquet(pp["events"])
    t = _utc(now)
    ct = pd.to_datetime(ev["provider_commence_time"], utc=True)
    ev = ev[(ct - pd.Timedelta(minutes=SNAPSHOT_MINUTES_BEFORE_TIP) <= t) & (t < ct)]
    if pp["snapshots"].exists():
        ev = ev[~ev["game_id"].isin(set(pd.read_parquet(pp["snapshots"])["game_id"]))]
    return ev.sort_values(["provider_commence_time", "game_id"]).reset_index(drop=True)


def _rows(q: pd.DataFrame, gid: str, ev: pd.Series, prov: pd.DataFrame, now) -> pd.DataFrame:
    q = q[(q["quote_status"] == "VALID") & q["stat"].notna()].copy()
    if not len(q):
        return pd.DataFrame()
    side = {s: q[q["side"] == s].set_index(["canonical_player_id", "stat", "line", "sportsbook"]) for s in ("OVER", "UNDER")}
    j = side["OVER"][["decimal_odds", "american_odds", "quote_available_at", "collector_received_at"]].join(
        side["UNDER"][["decimal_odds", "american_odds", "quote_available_at"]], how="outer", lsuffix="_over", rsuffix="_under")
    j = j.reset_index().rename(columns={"canonical_player_id": "player_id", "stat": "market"})
    p = prov[prov["game_id"] == gid].copy()
    p["_t"] = pd.to_datetime(p["prediction_as_of"], utc=True)
    p = p[p["_t"] < _utc(now)]
    out = []
    for r in j.itertuples(index=False):
        pr = p[(p["player_id"] == r.player_id) & (p["market"] == r.market)].sort_values("_t")
        last = pr[pr["_t"] == pr["_t"].max()] if len(pr) else pr
        same = last[(last["line"] == r.line) & (last["side"] == "OVER")]
        out.append({"game_id": gid, "player_id": r.player_id, "market": r.market, "line": float(r.line),
                    "sportsbook": r.sportsbook, "over_decimal": r.decimal_odds_over, "under_decimal": r.decimal_odds_under,
                    "over_american": r.american_odds_over, "under_american": r.american_odds_under,
                    "quote_available_at": str(r.quote_available_at_over if pd.notna(r.quote_available_at_over)
                                              else r.quote_available_at_under),
                    "collector_received_at": str(r.collector_received_at), "provider_event_id": ev["provider_event_id"],
                    "provider_commence_time": str(ev["provider_commence_time"]), "snapshot_taken_at": str(_utc(now)),
                    "minutes_before_commence": (pd.Timestamp(ev["provider_commence_time"]) - _utc(now)).total_seconds() / 60,
                    "label": LABEL, "exact_market_close": EXACT_MARKET_CLOSE,
                    "preceding_prediction_ids": json.dumps(sorted(last["prediction_id"])) if len(last) else "[]",
                    "preceding_model_version": last["model_version"].iat[0] if len(last) else None,
                    "preceding_training_cutoff": last["training_cutoff"].iat[0] if len(last) else None,
                    "preceding_prediction_as_of": str(last["prediction_as_of"].iat[0]) if len(last) else None,
                    "preceding_p_over_nonpush": float(same["probability"].iat[0] / same["p_nonpush"].iat[0])
                    if len(same) and same["p_nonpush"].iat[0] else None})
    return pd.DataFrame(out)


def snapshot(root: Path, now, session, collect=OF.collect) -> dict:
    """Take every due near-tip snapshot (one event-odds request per due event at most; idempotent per game)."""
    pp = paths(root)
    d = due(root, now)
    if not len(d):
        return {"due": 0, "events_requested": 0, "rows": 0}
    prov = pd.read_parquet(MON._p(root)["provenance"])
    cands = prov[["game_id", "team_id", "player_id", "player"]].drop_duplicates(["game_id", "player_id"]).rename(
        columns={"player": "display_name"})
    rows, errors = [], {}
    for ev in d.to_dict("records"):
        try:
            fs = collect(session, [ev["provider_event_id"]], out_dir=pp["collections"],
                         label=f"{LABEL} (near-tip quote snapshot; not a verified market close)")
        except Exception as e:                                   # noqa: BLE001 -- recorded; never blocks other games
            errors[ev["game_id"]] = f"{type(e).__name__}: {str(e)[:200]}"
            continue
        q = pd.concat([QT.load_collected(f) for f in fs], ignore_index=True)
        q["canonical_game_id"] = ev["game_id"]                   # the event was matched to this game at prediction time
        q = MT.match_players(q, cands, None)
        rows.append(_rows(q, ev["game_id"], pd.Series(ev), prov, now))
    new = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    n = MON._upsert(pp["snapshots"], new, KEY) if len(new) else 0
    return {"due": int(len(d)), "events_requested": int(len(d) - len(errors)), "rows": n, "errors": errors,
            "session": session.summary() if hasattr(session, "summary") else None}


def closing_frame(root: Path) -> pd.DataFrame | None:
    """Snapshot prices in monitor.clv form (one row per side)."""
    p = paths(root)["snapshots"]
    if not p.exists():
        return None
    s = pd.read_parquet(p)
    parts = [s.assign(side=side, closing_decimal_odds=s[f"{side.lower()}_decimal"]) for side in ("OVER", "UNDER")]
    out = pd.concat(parts, ignore_index=True)
    return out[out["closing_decimal_odds"].notna()][KEY + ["side", "closing_decimal_odds", "label"]]
