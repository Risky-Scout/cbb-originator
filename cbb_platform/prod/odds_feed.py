"""2026-27 NCAAB player-prop quote feed (The Odds API v4) around the documented event-odds endpoint.

    listing    GET events (0 credits)  -> which 2026-27 events are listed, with provider event ids
    discover   GET events/{id}/markets (1 credit) -> which of the 12 production prop markets are advertised
    collect    discover, then GET events/{id}/odds for the advertised subset only (none advertised: no odds call)
               -> one load_collected()-format file per event (data/prod/odds/collections)
    coverage   precise operational quote states for a slate (replaces the retired QUOTE_COVERAGE_UNVERIFIED):

      game   ODDS_EVENT_NOT_YET_LISTED          no listed event matches the game (or no listing covers its tip)
             QUOTE_IDENTITY_UNRESOLVED          a listed event near the tip shares a team with the game but does not
                                                resolve strictly to it (never guessed)
             PLAYER_PROP_MARKET_NOT_YET_POSTED  the event is listed but no documented player-prop market is posted
             QUOTE_STALE                        props were posted but every one is older than QUOTE_MAX_AGE
      row    MARKET_NOT_OFFERED_FOR_EVENT       the event has props, but not this market (provider support is
                                                documented: absence is per event, never a global conclusion)
             QUOTE_IDENTITY_UNRESOLVED          the market is posted, the player has no quote, and quotes in that
                                                market for this event could not be matched to a canonical player
             STALE_QUOTE / POST_CUTOFF_QUOTE    the player's own quotes were not usable at prediction time
             NO_VALID_QUOTE                     the market is posted for other players only
      run    ODDS_API_FAILURE                   the most recent Odds API acquisition by prediction time FAILED
             QUOTE_INTEGRATION_UNVALIDATED      the built integration has not yet passed its first real-event validation
                                                (validate(); record data/prod/odds/integration_validation.json)

    .venv/bin/python -m cbb_platform.prod.odds_feed mapping
    .venv/bin/python -m cbb_platform.prod.odds_feed events [--hours 240]        # free listing + strict event matching
    .venv/bin/python -m cbb_platform.prod.odds_feed validate --event <eventId>   # ONE paid request, inside 36 h of tip
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, matching as MT, odds_acquire as OA, quote_cert as QC, quotes as QT

PROP_STATS = set(QT.KEY_TO_STAT.values())
EVENT_COLS = ["provider_event_id", "provider_home_team", "provider_away_team", "provider_commence_time"]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(t) -> str:
    return _utc(t).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================================ listing (0 credits)
def events_params(lo, hi) -> dict:
    """Hour-aligned window (floor / ceil) so repeated listings within ODDS_EVENTS_MAX_AGE_HOURS reuse the cache."""
    return {"dateFormat": "iso", "commenceTimeFrom": _iso(_utc(lo).floor("h")), "commenceTimeTo": _iso(_utc(hi).ceil("h"))}


def fetch_events(session: OA.Session, lo, hi) -> dict:
    return session.get("events", events_params(lo, hi), max_age_seconds=PC.ODDS_EVENTS_MAX_AGE_HOURS * 3600,
                       purpose="events listing (0 credits)")


def listing(as_of=None, root: Path | None = None) -> dict:
    """The most recent events listing received by `as_of`: {"events": DataFrame, "window": (lo, hi), "received_at"}.
    None when no listing was acquired."""
    got = OA.latest("events", root, as_of)
    if not got:
        return None
    path, meta = got[-1]
    body = OA.read(path)
    ev = pd.DataFrame([{"provider_event_id": e.get("id"), "provider_home_team": e.get("home_team"),
                        "provider_away_team": e.get("away_team"), "provider_commence_time": _utc(e.get("commence_time"))}
                       for e in body], columns=EVENT_COLS)
    p = meta["params"]
    return {"events": ev, "window": (_utc(p["commenceTimeFrom"]), _utc(p["commenceTimeTo"])),
            "received_at": _utc(meta["received_at"]), "sha256": meta["sha256"]}


def event_commence_resolver(root: Path | None = None, as_of=None):
    """eventId -> commence time from every listing acquired by `as_of` (latest listing wins)."""
    known = {}
    for path, _ in OA.latest("events", root, as_of):
        for e in OA.read(path):
            known[str(e.get("id"))] = e.get("commence_time")
    return lambda eid: known.get(str(eid))


def match_events(events: pd.DataFrame, slate: pd.DataFrame, tidx: dict) -> pd.DataFrame:
    """Strict event -> canonical game (matching.match_games rules). Adds canonical_game_id + match_reason."""
    if events is None or not len(events):
        out = pd.DataFrame(columns=EVENT_COLS + ["canonical_game_id", "match_reason"])
        out["provider_commence_time"] = pd.to_datetime(out["provider_commence_time"], utc=True)
        return out
    q = events.assign(quote_status="VALID", suppression_reason="", canonical_game_id=None)
    m = MT.match_games(q, slate, tidx)
    return m.assign(match_reason=m["suppression_reason"])[EVENT_COLS + ["canonical_game_id", "match_reason"]]


# ============================================================================ collection (paid)
NO_SUPPORTED = "NO_SUPPORTED_PLAYER_PROP_MARKETS"
DISCOVERED = "SUPPORTED_PLAYER_PROP_MARKETS_ADVERTISED"


def _brief(e: dict | None) -> dict | None:
    """Ledger entry -> the request receipt carried by a collection file (quota headers included; never the body)."""
    if e is None:
        return None
    return {k: e.get(k) for k in ("endpoint", "status", "params", "requested_at", "received_at", "http_status", "cost",
                                  "quota_remaining", "quota_used", "markets_returned", "response_sha256", "cache_path",
                                  "estimated_cost")}


def advertised(body: dict) -> dict:
    """An event-markets payload -> the production markets advertised: SUPPORTED_FOR_EVENT = advertised keys INTERSECT
    quotes.REQUEST_MARKETS (production order), the bookmakers advertising at least one of them, and every other key."""
    keys, books = set(), {}
    for bk in body.get("bookmakers") or []:
        ks = {m.get("key") for m in bk.get("markets") or [] if isinstance(m.get("key"), str)}
        keys |= ks
        hit = sorted(ks & set(QT.REQUEST_MARKETS))
        if hit:
            books[str(bk.get("key") or "").strip().lower()] = hit
    supported = [m for m in QT.REQUEST_MARKETS if m in keys]
    return {"supported_markets": supported, "supported_market_count": len(supported),
            "bookmakers_advertising": len(books), "bookmaker_markets": dict(sorted(books.items())),
            "other_market_keys": sorted(keys - set(QT.REQUEST_MARKETS)),
            "status": DISCOVERED if supported else NO_SUPPORTED}


def discover(session: OA.Session, event_id: str, region: str = PC.ODDS_REGIONS) -> dict:
    """GET events/{id}/markets (1 credit; a recent cached answer is reused) -> advertised() + the request receipt.
    `region`: production `us`; `us2` only for the certification discovery fallback."""
    params = {"eventId": event_id, "regions": region, "dateFormat": "iso"}
    e = session.get("event_markets", params, max_age_seconds=PC.ODDS_EVENT_ODDS_MAX_AGE_MINUTES * 60,
                    purpose="player-prop market discovery (1 credit)")
    return {**advertised(e["body"]), "event_id": event_id, "region": region, "body": e["body"], "request": _brief(e)}


def collect(session: OA.Session, event_ids, out_dir: Path | None = None, markets=QT.REQUEST_MARKETS,
            label: str = "LIVE The Odds API event odds (2026-27 NCAAB)", discovered: dict | None = None) -> list[Path]:
    """Per event: market discovery FIRST (events/{id}/markets), then ONE event-odds request for exactly the advertised
    subset of the production markets (never a key discovery did not show). An event advertising none of them gets NO
    event-odds request: its collection file carries the discovery receipt, status NO_SUPPORTED_PLAYER_PROP_MARKETS and
    no bookmakers (it normalizes to zero quotes). One collection file per event, stamped with the instant its quote
    evidence was received. Recent cached payloads (ODDS_EVENT_ODDS_MAX_AGE_MINUTES) are reused, never re-bought.
    `discovered`: event id -> a discover() result already made in this session (not re-requested)."""
    out = Path(out_dir or PC.ODDS_COLLECTIONS)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for eid in event_ids:
        d = (discovered or {}).get(eid) or discover(session, eid)
        want = [m for m in markets if m in d["supported_markets"]]
        disc = {k: d[k] for k in ("status", "supported_markets", "supported_market_count", "bookmakers_advertising",
                                  "bookmaker_markets", "other_market_keys")}
        disc["requested_markets"] = want
        if not want:
            disc["status"] = NO_SUPPORTED
            rx = d["request"]["received_at"]
            ev = {k: d["body"].get(k) for k in ("id", "sport_key", "sport_title", "commence_time", "home_team", "away_team")}
            doc = {"label": label, "provider": QT.PROVIDER, "collector_received_at": rx,
                   "request_sha256": None, "market_discovery": disc,
                   "requests": {"event_markets": d["request"], "event_odds": None},
                   "events": [{**ev, "bookmakers": [], "collector_received_at": rx}]}
        else:
            params = {"eventId": eid, "regions": d.get("region", PC.ODDS_REGIONS), "markets": ",".join(want), "oddsFormat": "american",
                      "dateFormat": "iso", "includeLinks": "true", "includeSids": "true"}
            e = session.get("event_odds", params, max_age_seconds=PC.ODDS_EVENT_ODDS_MAX_AGE_MINUTES * 60,
                            purpose="player props")
            rx = e["received_at"]
            doc = {"label": label, "provider": QT.PROVIDER, "collector_received_at": rx,
                   "request_sha256": e.get("response_sha256"), "market_discovery": disc,
                   "requests": {"event_markets": d["request"], "event_odds": _brief(e)},
                   "events": [{**e["body"], "collector_received_at": rx}]}
        p = out / f"{eid}_{_utc(rx).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        p.write_text(json.dumps(doc, indent=1, default=str))
        paths.append(p)
    return paths


def collection_receipt(path: Path) -> dict:
    """What one collection file cost and showed: discovery status, credits of both requests, quota headers."""
    doc = json.loads(Path(path).read_text())
    rq = doc.get("requests") or {}
    em, eo = rq.get("event_markets"), rq.get("event_odds")
    books = (doc.get("events") or [{}])[0].get("bookmakers") or []
    last = eo or em or {}
    return {"discovery_status": (doc.get("market_discovery") or {}).get("status"),
            "supported_markets": (doc.get("market_discovery") or {}).get("supported_markets"),
            "requested_markets": (doc.get("market_discovery") or {}).get("requested_markets"),
            "markets_credits": int((em or {}).get("cost") or 0), "odds_credits": int((eo or {}).get("cost") or 0),
            "credits": int((em or {}).get("cost") or 0) + int((eo or {}).get("cost") or 0),
            "markets_status": (em or {}).get("status"), "odds_status": (eo or {}).get("status"),
            "status": last.get("status"), "received_at": doc.get("collector_received_at"),
            "response_sha256": (eo or {}).get("response_sha256"),
            "markets_response_sha256": (em or {}).get("response_sha256"),
            "quota_remaining": last.get("quota_remaining"), "quota_used": last.get("quota_used"),
            "markets_returned": len({m.get("key") for b in books for m in b.get("markets") or []}),
            "bookmakers": len(books)}


def collections(as_of, root: Path | None = None) -> list[Path]:
    """The latest collection file per event received by `as_of` (an older one never stands in for a newer one)."""
    folder = Path(root or PC.ODDS_COLLECTIONS)
    best: dict[str, tuple] = {}
    for p in sorted(folder.glob("*.json")) if folder.exists() else []:
        doc = json.loads(p.read_text())
        rx = _utc(doc["collector_received_at"])
        if rx > _utc(as_of):
            continue
        for e in doc.get("events", []):
            k = str(e.get("id"))
            if k not in best or rx > best[k][0]:
                best[k] = (rx, p)
    return sorted({v[1] for v in best.values()})


# ============================================================================ readiness coverage
def coverage(slate: pd.DataFrame, quotes: pd.DataFrame, lst: dict | None, tidx: dict) -> dict:
    """quotes: normalized + match_games + match_players + quote_freshness. Returns game_reasons {gid: [..]},
    row_reason(gid, pid, market) -> reason, and per-game diagnostics."""
    games = slate[slate["slate_status"] == "SCORE"] if "slate_status" in slate else slate
    ev = lst["events"] if lst else pd.DataFrame(columns=EVENT_COLS)
    lo, hi = lst["window"] if lst else (None, None)
    mev = match_events(ev, slate, tidx)
    q = quotes if len(quotes) else pd.DataFrame(columns=QT.COLUMNS)
    game_reasons, diag, ev_of = {}, {}, {}
    for g in games.itertuples(index=False):
        gid, tip = g.canonical_game_id, _utc(g.start_time)
        hit = mev[mev["canonical_game_id"] == gid]
        covered = lst is not None and lo <= tip <= hi
        d = {"listing_covers_tip": bool(covered), "event_ids": hit["provider_event_id"].tolist()}
        if len(hit) == 0:
            near = mev[(mev["provider_commence_time"] - tip).abs() <= MT.COMMENCE_TOLERANCE]
            teams = {g.home_team_id, g.away_team_id}
            partial = [r for r in near.itertuples(index=False) if r.match_reason
                       and {MT.resolve_team(r.provider_home_team, tidx)[0], MT.resolve_team(r.provider_away_team, tidx)[0]} & teams]
            game_reasons[gid] = ["QUOTE_IDENTITY_UNRESOLVED" if partial else "ODDS_EVENT_NOT_YET_LISTED"]
            d["unresolved_events"] = [r.provider_event_id for r in partial]
            diag[gid] = d
            continue
        eids = set(hit["provider_event_id"])
        ev_of[gid] = eids
        props = q[q["provider_event_id"].isin(eids) & q["stat"].isin(PROP_STATS)]
        d["prop_quotes"] = int(len(props))
        d["markets_posted"] = sorted(props["stat"].dropna().unique().tolist())
        d["markets_not_offered"] = sorted(set(PC.MARKETS) - set(d["markets_posted"])) if len(props) else []
        if not len(props):
            game_reasons[gid] = ["PLAYER_PROP_MARKET_NOT_YET_POSTED"]
        elif (not (props["quote_status"] == "VALID").any()
              and (props["suppression_reason"] == "STALE_QUOTE").any()
              and (props.loc[props["canonical_player_id"].notna(), "suppression_reason"] == "STALE_QUOTE").all()):
            game_reasons[gid] = ["QUOTE_STALE"]          # identity-resolved props exist, every one of them stale
        else:
            game_reasons[gid] = []
        diag[gid] = d
    unresolved = q[q["suppression_reason"].isin(("UNMATCHED_PLAYER", "AMBIGUOUS_PLAYER"))]
    own = q[q["canonical_player_id"].notna() & (q["quote_status"] != "VALID")]
    own_reason = {}
    for r in own.itertuples(index=False):
        own_reason.setdefault((r.canonical_game_id, r.canonical_player_id, r.stat), r.suppression_reason)
    posted = {(gid, s) for gid, eids in ev_of.items()
              for s in q.loc[q["provider_event_id"].isin(eids) & q["stat"].isin(PROP_STATS), "stat"].unique()}
    unres = {(gid, s) for gid, eids in ev_of.items()
             for s in unresolved.loc[unresolved["provider_event_id"].isin(eids), "stat"].unique()}

    def row_reason(gid, pid, market) -> str:
        if gid not in ev_of or not any(k[0] == gid for k in posted):
            return "NO_VALID_QUOTE"                     # the game-level reason already names the cause
        if (gid, market) not in posted:
            return "MARKET_NOT_OFFERED_FOR_EVENT"
        if (gid, pid, market) in own_reason:
            return own_reason[(gid, pid, market)]
        if (gid, market) in unres:
            return "QUOTE_IDENTITY_UNRESOLVED"
        return "NO_VALID_QUOTE"

    return {"game_reasons": game_reasons, "row_reason": row_reason, "diagnostics": diag,
            "listing": None if lst is None else {"received_at": lst["received_at"], "window": [lo, hi],
                                                 "events": int(len(ev)), "matched": int(mev["canonical_game_id"].notna().sum())}}


def last_odds_failure(ledger_path: Path, aso) -> dict | None:
    """The most recent Odds API acquisition attempt by prediction time, when it FAILED (ODDS_API_FAILURE)."""
    rows = [r for r in OA.ledger(ledger_path) if r.get("status") in ("FETCHED", "FETCHED_UNCHANGED", "CACHE_HIT", "FAILED")
            and _utc(r["requested_at"]) <= _utc(aso)]
    return rows[-1] if rows and rows[-1]["status"] == "FAILED" else None


def integration_validated(record: Path, aso) -> dict | None:
    p = Path(record)
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    if r.get("integration_fingerprint") and r["integration_fingerprint"] != QC.fingerprint():
        return None                                   # certified by a materially different integration
    return r if r.get("status") == "PASS" and _utc(r["validated_at"]) <= _utc(aso) else None


# ============================================================================ first real-event validation
def validation_checks(collection_paths, run_dir: Path, ledger_path: Path) -> dict:
    """Implementation checks on a real event: markets returned, bookmaker normalization, identity matching, parsing,
    timestamps, best-price selection, nothing fabricated (every priced line / price traces to a valid quote)."""
    raw = pd.concat([QT.load_collected(p) for p in collection_paths], ignore_index=True)
    qp = Path(run_dir) / "quotes_normalized.csv"
    qn = pd.read_csv(qp) if qp.exists() else pd.DataFrame(columns=QT.COLUMNS)
    recs = [json.loads(l)["body"] for l in Path(ledger_path).read_text().splitlines() if l.strip()] \
        if Path(ledger_path).exists() else []
    preds = [r for r in recs if "prediction_id" in r]
    docs = [json.loads(Path(p).read_text()) for p in collection_paths]
    returned = sorted({m.get("key") for d in docs for e in d["events"] for bk in e.get("bookmakers") or []
                       for m in bk.get("markets") or []})
    per_market = {k: ("RETURNED" if k in returned else "MARKET_NOT_OFFERED_FOR_EVENT") for k in QT.REQUEST_MARKETS}
    props = raw[raw["stat"].notna()]
    c = {"expected_markets_returned": {"pass": "RETURNED" in per_market.values(), "per_market": per_market}}
    unsupported = sorted(set(returned) - set(QT.KEY_TO_STAT))
    books = raw[["sportsbook", "sportsbook_title"]].drop_duplicates()
    c["bookmaker_normalization"] = {"pass": bool(len(books)) and bool(books["sportsbook"].str.fullmatch(r"[a-z0-9_]+").all())
                                    and bool((books["sportsbook_title"].str.len() > 0).all()),
                                    "books": books.to_dict(orient="records")}
    bad = int((props["suppression_reason"] == "INVALID_QUOTE").sum())
    c["lines_sides_prices_parse"] = {"pass": len(props) > 0 and bad <= 0.01 * len(props), "prop_quotes": int(len(props)),
                                     "invalid": bad}
    ts_ok = props["market_last_update"].notna() & (props["market_last_update"] <= props["collector_received_at"])
    c["timestamps_usable"] = {"pass": bool(len(props)) and bool(ts_ok.all()), "missing_or_after_receipt": int((~ts_ok).sum())}
    m = qn[qn["stat"].notna()] if len(qn) else qn
    matched = m[m["canonical_player_id"].notna()] if len(m) else m
    n_unm = int((m["suppression_reason"] == "UNMATCHED_PLAYER").sum()) if len(m) else 0
    c["player_identity"] = {"pass": len(matched) > 0 and bool(m["canonical_game_id"].notna().all()),
                            "prop_quotes": int(len(m)), "matched": int(len(matched)),
                            "ambiguous_suppressed": int((m["suppression_reason"] == "AMBIGUOUS_PLAYER").sum()) if len(m) else 0,
                            "unmatched_suppressed": n_unm,
                            "unmatched_names": sorted(m.loc[m["suppression_reason"] == "UNMATCHED_PLAYER",
                                                            "provider_player_name"].unique().tolist()) if n_unm else []}
    v = qn[qn["quote_status"] == "VALID"] if len(qn) else qn
    quoted = {(r.canonical_game_id, r.canonical_player_id, r.stat, float(r.line)) for r in matched.itertuples(index=False)} \
        if len(matched) else set()
    fabricated, wrong_best, priced = 0, 0, 0
    for p in preds:
        if p.get("line") is None:
            continue
        if (p["game_id"], p["player_id"], p["market"], float(p["line"])) not in quoted:
            fabricated += 1                              # a priced line no sportsbook quoted for this player
        if p.get("best_decimal_odds") is None:
            continue
        priced += 1
        cand = v[(v["canonical_game_id"] == p["game_id"]) & (v["canonical_player_id"] == p["player_id"])
                 & (v["stat"] == p["market"]) & (v["line"] == p["line"]) & (v["side"] == p["side"])]
        if not len(cand):
            fabricated += 1
        elif abs(cand["decimal_odds"].max() - float(p["best_decimal_odds"])) > 1e-9 \
                or p["sportsbook"] not in set(cand.loc[(cand["decimal_odds"] - cand["decimal_odds"].max()).abs() < 1e-9,
                                                       "sportsbook"]):
            wrong_best += 1
    c["best_price_selection"] = {"pass": priced > 0 and wrong_best == 0, "priced_rows": priced, "wrong_best": wrong_best}
    c["no_fabricated_market"] = {"pass": fabricated == 0 and not unsupported, "fabricated_rows": fabricated,
                                 "unsupported_keys_returned": unsupported}
    ok = all(x["pass"] for x in c.values())
    return {"status": "PASS" if ok else "FAIL", "checks": c}


def validate(event_id: str, *, now=None, session: OA.Session | None = None, run_root: Path | None = None,
             record: Path = PC.ODDS_VALIDATION_RECORD, state_root: Path = PC.STATE_ROOT,
             history_db: Path = PC.HISTORY_CANONICAL, collections_dir: Path | None = None, sims: int = PC.SIMS,
             sync_log: Path = PC.SYNC_LOG) -> dict:
    """IMPLEMENTATION VALIDATION (not capability discovery) on one real, listed 2026-27 event inside the prop window:
    one events listing (free) + ONE event-odds request, then the full shadow pipeline for that one game into a scratch
    ledger, then validation_checks. Writes the PASS record only when every check passes."""
    from cbb_platform.prod import run as RN, slate as SL, state as ST   # late: run imports this module
    t = _utc(now or pd.Timestamp.now(tz="UTC"))
    run_root = Path(run_root or PC.ODDS_ROOT / "validation_runs")
    session = session or OA.Session("odds-validate", clock=lambda: t.to_pydatetime())
    fetch_events(session, t, t + timedelta(hours=PC.ODDS_PROP_WINDOW_HOURS))
    session.event_commence = event_commence_resolver(session.cache_root, t)
    st = ST.current(state_root)
    if st is None:
        raise RuntimeError("no promoted 2026-27 state")
    slate = SL.build(st["increment_db"], t, st["manifest"].get("schedule_as_of") or st["manifest"]["source_cutoff"],
                     PC.ODDS_PROP_WINDOW_HOURS)
    lst = listing(t, session.cache_root)
    teams, _ = RN._names(history_db)
    mev = match_events(lst["events"], slate, MT.team_index(teams, MT.odds_team_map()))
    hit = mev[mev["provider_event_id"] == event_id]
    if not len(hit) or not isinstance(hit["canonical_game_id"].iat[0], str):
        raise RuntimeError(f"event {event_id} does not resolve strictly to a 2026-27 slate game "
                           f"({hit['match_reason'].iat[0] if len(hit) else 'NOT_LISTED'}): not validated")
    gid = hit["canonical_game_id"].iat[0]
    paths = collect(session, [event_id], collections_dir)
    # the prediction is made once the quote is in hand (as the scheduled tick does: as-of taken after collection).
    # Scoring at t, the instant BEFORE the request, made every freshly received quote POST_CUTOFF_QUOTE, so nothing
    # could ever be priced and best_price_selection could never pass (2026-11-09 attempt: 10 valid quotes, 0 priced).
    aso = max([t] + [_utc(json.loads(Path(p).read_text())["collector_received_at"]) for p in paths])
    run_dir = run_root / ("V" + t.strftime("%Y%m%dT%H%M%SZ"))
    s = RN.daily(aso, quotes_paths=paths, state_root=state_root, history_db=history_db,
                 ledger_path=run_dir / "validation_ledger.jsonl", run_root=run_dir, game_ids=[gid],
                 horizon_hours=PC.ODDS_PROP_WINDOW_HOURS, sims=sims, sync_log=sync_log, odds_cache_root=session.cache_root,
                 odds_collections=None, odds_ledger=session.ledger_path, odds_record=record)
    res = validation_checks(paths, Path(s["run_dir"]), run_dir / "validation_ledger.jsonl")
    out = {"status": res["status"], "validated_at": aso.isoformat(), "prediction_as_of": aso.isoformat(),
           "validation_started_at": t.isoformat(), "event_id": event_id, "canonical_game_id": gid,
           "feed_version": PC.ODDS_FEED_VERSION, "collections": [str(p) for p in paths], "run_dir": s["run_dir"],
           "acquisition": session.summary(), "requests": [{k: v for k, v in e.items() if k != "body"} for e in session.entries],
           **res}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "odds_validation.json").write_text(json.dumps(out, indent=1, default=str))
    if out["status"] == "PASS":
        Path(record).parent.mkdir(parents=True, exist_ok=True)
        Path(record).write_text(json.dumps(out, indent=1, default=str))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.odds_feed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mapping")
    e = sub.add_parser("events")
    e.add_argument("--hours", type=float, default=PC.ODDS_PROP_WINDOW_HOURS)
    v = sub.add_parser("validate")
    v.add_argument("--event", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "mapping":
        print(QT.market_mapping().to_string(index=False))
        return
    if a.cmd == "events":
        from cbb_platform.prod import run as RN, slate as SL, state as ST
        t = pd.Timestamp.now(tz="UTC")
        s = OA.Session("odds-events")
        ent = fetch_events(s, t, t + timedelta(hours=a.hours))
        lst = listing(ent["received_at"], s.cache_root)
        st = ST.current()
        slate = SL.build(st["increment_db"], t, st["manifest"].get("schedule_as_of") or st["manifest"]["source_cutoff"],
                         a.hours)
        teams, _ = RN._names(PC.HISTORY_CANONICAL)
        mev = match_events(lst["events"], slate, MT.team_index(teams, MT.odds_team_map()))
        print(json.dumps({"request": {k: v for k, v in ent.items() if k != "body"}, "acquisition": s.summary(),
                          "listed_events": int(len(lst["events"])), "slate_games": int(len(slate)),
                          "matched": int(mev["canonical_game_id"].notna().sum()),
                          "match_reasons": mev["match_reason"].value_counts().to_dict()}, indent=1, default=str))
        return
    print(json.dumps(validate(a.event), indent=1, default=str))


if __name__ == "__main__":
    main()
