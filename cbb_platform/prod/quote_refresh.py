"""Tiered sportsbook quote refresh: request quotes only when a game is due, REPRICE the latest persisted PMF. No refit.

    PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.lifecycle quotes        (the 10-minute Droplet quote timer)

T = the game's OFFICIAL PREDICTION CUTOFF (PC.VALIDATED_PREDICTION_CUTOFF = TIP_MINUS_1H; the registry target cutoff).
The scheduler wakes every SLOT_MINUTES; each game is requested only when due:

    before T-6h                  no paid quote request (the morning discovery pass = the free events listing only)
    T-6h  .. strictly < T-2h     at most one request per game per 60 minutes
    T-2h  .. strictly < T-30m    at most one request per game per 30 minutes
    T-30m .. < T                 at most one request per game per 10 minutes
    at / after T                 no request (nothing after the cutoff can change the official selection)

Spacing is measured on the scheduler's SLOT_MINUTES grid (floor of each request instant), so a request made a few
seconds into a slot is never counted as "late" and pushed a whole slot back. Every request of the game counts: this
module's own requests AND the official shadow tick's single pre-cutoff snapshot (registry/quote_snapshots.jsonl). A
request is also skipped when the official tick's fixed scoring slot (cutoff - SCORE_LEAD_MINUTES) falls inside the
current tier interval, so the official snapshot never lands closer than one interval after a refresh.

A quote request NEVER triggers a refit, a walk-forward, a model fit or a PMF simulation. It REPRICES the latest valid
persisted PMF of the game at or before now (and before the cutoff): the official tick PMFs (ticks/<tick>/pmfs.parquet)
or the latest line-independent forecast snapshot (forecasts/<D>/<snapshot>/pmfs.parquet), read exactly as persisted,
priced with the existing production pricing (prod.side_monitor.quote_side_records -> prod.pricing). Quotes are
normalized, strictly matched and freshness-checked by the existing production code (quotes / matching / board).

Persisted (ops evidence tree, append-only):
    quotes/refresh_requests.jsonl            one line per request attempt: game, event, tier, requested / received
                                             instants, status, credits, response sha256, collection file
    quotes/last_request.json                 game -> last attempt / last successful request (derived, atomic)
    quotes/<D>/reprice_side_monitor.jsonl    hash-chained QUOTE_SIDE receipts (prod.side_monitor format): sportsbook,
                                             market/stat, side, line, odds, quote observation instants, the PMF source
                                             (snapshot / tick id, run id, receipt id, file sha256) and the response sha256
Boards (prod.exports) select among the latest quote observation of each game; at the cutoff the selection freezes.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pandas as pd

from cbb_platform.prod import (board as BO, config as PC, matching as MT, odds_acquire as OA, odds_feed as OF,
                               ops_store as OS, pmf_evidence as PE, quote_cert as QC, quote_obs as QO, quotes as QT,
                               shadow_tick as SH, side_monitor as SD, slate as SL, state as ST)

QR_VERSION = "cbb-prod-quote-refresh-1.0.0"
SLOT_MINUTES = 10
# (name, earliest minutes before cutoff (inclusive), latest minutes before cutoff (exclusive), interval minutes)
TIERS = (("T6H_T2H", 360, 120, 60), ("T2H_T30M", 120, 30, 30), ("T30M_CUTOFF", 30, 0, 10))
EVENTS_REFRESH_MINUTES = 30                 # a due game with no matched event refreshes the FREE events listing
REFRESH_FILE = "reprice_side_monitor.jsonl"
LIVE_STATUSES = ("FETCHED", "FETCHED_UNCHANGED")            # odds_acquire ledger statuses of a live (paid) request
OK_STATUSES = LIVE_STATUSES + ("CACHE_HIT",)


def _utc(x):
    return SH._utc(x)


def slot(t) -> pd.Timestamp:
    return _utc(t).floor(f"{SLOT_MINUTES}min")


def tier(cutoff, now) -> tuple[str, int | None]:
    """(tier name, interval minutes); interval None = no request allowed in this phase."""
    r = (_utc(cutoff) - _utc(now)).total_seconds() / 60.0
    if r <= 0:
        return "AFTER_CUTOFF", None
    for name, hi, lo, iv in TIERS:
        if lo < r <= hi:
            return name, iv
    return "BEFORE_T_MINUS_6H", None


def due(cutoff, now, last_request=None, official_pending: bool = True) -> tuple[bool, str, str]:
    """(due, tier, reason) for one game."""
    now = _utc(now)
    name, iv = tier(cutoff, now)
    if iv is None:
        return False, name, name
    if last_request is not None and (slot(now) - slot(last_request)) < pd.Timedelta(minutes=iv):
        return False, name, f"RATE_LIMIT ({iv} min; last request {_utc(last_request).isoformat()})"
    off = _utc(cutoff) - pd.Timedelta(minutes=PC.SCORE_LEAD_MINUTES)
    off_slot = off.ceil(f"{SLOT_MINUTES}min")
    if official_pending and now < off_slot and (off_slot - slot(now)) < pd.Timedelta(minutes=iv):
        return False, name, f"OFFICIAL_TICK_SNAPSHOT_DUE_AT {off_slot.isoformat()}"
    return True, name, "DUE"


# ============================================================================ request history
def requests_path(ops_root: Path) -> Path:
    return Path(ops_root) / "quotes" / "refresh_requests.jsonl"


def last_requests(ops_root: Path) -> dict:
    """game_id -> latest request instant (this module's attempts + the official tick's quote snapshots)."""
    out: dict = {}

    def put(g, t):
        if g and t is not None:
            t = _utc(t)
            if g not in out or t > out[g]:
                out[g] = t
    for r in OS.read_jsonl(requests_path(ops_root)):
        put(r.get("game_id"), r.get("received_at") or r.get("requested_at"))
    for r in OS.read_jsonl(Path(ops_root) / "registry" / "quote_snapshots.jsonl"):
        if r.get("received_at"):
            put(r.get("game_id"), r["received_at"])
    return out


def _write_last(ops_root: Path) -> None:
    att, ok = {}, {}
    for r in OS.read_jsonl(requests_path(ops_root)):
        g = r.get("game_id")
        att[g] = r.get("requested_at")
        if r.get("status") in OK_STATUSES or r.get("discovery_status") == OF.NO_SUPPORTED:
            ok[g] = {"last_success_at": r.get("received_at"), "event_id": r.get("event_id"),
                     "response_sha256": r.get("response_sha256")}
    doc = {g: {"last_attempt_at": att[g], **ok.get(g, {"last_success_at": None})} for g in sorted(att)}
    p = Path(ops_root) / "quotes" / "last_request.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    os.replace(tmp, p)


# ============================================================================ the PMF a quote is priced against
def latest_pmf_source(ops_root: Path, game_id: str, cutoff, now) -> dict | None:
    """The latest valid persisted PMF of the game made at or before now and at or before its cutoff."""
    cutoff, now = _utc(cutoff), _utc(now)
    best = None
    for r in OS.read_jsonl(Path(ops_root) / "forecasts" / "index.jsonl"):
        if r.get("status") != "FORECAST" or game_id not in (r.get("games") or []) or not r.get("prediction_as_of"):
            continue
        aso = _utc(r["prediction_as_of"])
        d = Path(ops_root) / "forecasts" / r["slate_date"] / r["snapshot_id"]
        if aso <= now and aso <= cutoff and (d / PE.FILE).exists() and (best is None or aso > best["prediction_as_of"]):
            best = {"kind": "FORECAST_SNAPSHOT", "id": r["snapshot_id"], "prediction_as_of": aso, "pmfs": d / PE.FILE,
                    "players": d / "forecast_players.parquet"}
    dec = {}
    for r in OS.read_jsonl(Path(ops_root) / "registry" / "games.jsonl"):
        if r.get("game_id") == game_id and r.get("status") in SH.DECIDED and game_id not in dec:
            dec[game_id] = r
    r = dec.get(game_id)
    if r and r.get("status") == "PREDICTED" and r.get("prediction_as_of"):
        aso = _utc(r["prediction_as_of"])
        d = Path(ops_root) / "ticks" / str(r["tick_id"])
        if aso <= now and aso <= cutoff and (d / PE.FILE).exists() and (best is None or aso > best["prediction_as_of"]):
            best = {"kind": "OFFICIAL_TICK", "id": r["tick_id"], "prediction_as_of": aso, "pmfs": d / PE.FILE,
                    "players": d / "projections.parquet"}
    return best


def _dist_and_exposure(src: dict, game_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    pe = PE.read(src["pmfs"])
    pe = pe[pe["game_id"] == game_id]
    ds = PE.distributions(pe)
    pl = pd.read_parquet(src["players"]) if Path(src["players"]).exists() else pd.DataFrame()
    pl = pl[pl["game_id"] == game_id] if len(pl) else pl
    keep = ["game_id", "team_id", "player_id", "min_mu", "p_play", "role_diagnostic", "cold_start_class"]
    expo = pl[[c for c in keep if c in pl.columns]].drop_duplicates(["game_id", "player_id"]) if len(pl) else pd.DataFrame(columns=keep)
    for c in keep:
        if c not in expo:
            expo[c] = None
    expo = expo.assign(team_games=float("nan"))
    pp = expo.set_index("player_id")["p_play"].to_dict() if len(expo) else {}
    dist = pd.DataFrame([{"game_id": d["game_id"], "team_id": d["team_id"], "player_id": d["player_id"],
                          "market": d["stat"], "pmf": d["pmf"], "projection": d["mean"], "method": d["method"],
                          "p_play": pp.get(d["player_id"]), "game_label": d["game_label"],
                          "player_label": d["player_label"], "prediction_receipt_id": d["prediction_receipt_id"],
                          "pmf_run_id": d["run_id"]} for d in ds])
    cands = dist[["game_id", "team_id", "player_id", "player_label"]].drop_duplicates(["game_id", "player_id"]) \
        .rename(columns={"player_label": "display_name"}) if len(dist) else \
        pd.DataFrame(columns=["game_id", "team_id", "player_id", "display_name"])
    mv = ds[0]["model_version"] if ds else None
    return dist, expo, cands, {"model_version": mv, "pmf_sha256": PE.sha256(src["pmfs"])}


def price_collection(game: dict, src: dict, collection: Path, slate: pd.DataFrame, tidx: dict, now, refresh_id: str,
                     request: dict) -> tuple[dict, list[dict], pd.DataFrame]:
    """(summary, QUOTE_SIDE bodies, matched quote rows) of one game: the persisted PMF `src` priced at the quotes of
    `collection`. Writes nothing; nothing is simulated, fitted or recomputed."""
    now = _utc(now)
    gid = game["canonical_game_id"]
    dist, expo, cands, info = _dist_and_exposure(src, gid)
    out = {"game_id": gid, "pmf_source": src["kind"], "pmf_source_id": src["id"], "pmfs": int(len(dist)), "receipts": 0}
    q = QT.load_collected(collection)
    if len(q):
        q = MT.match_games(q, slate, tidx)
        q = MT.match_players(q, cands, None)
        q = BO.quote_freshness(q, now, slate.set_index("canonical_game_id")["start_time"].to_dict())
    out["quote_rows"] = int(len(q))
    out["valid_quote_rows"] = int((q["quote_status"] == "VALID").sum()) if len(q) else 0
    if not len(dist):
        return {**out, "status": "NO_PMF_ROWS"}, [], q
    meta = {"run_id": refresh_id, "prediction_as_of": src["prediction_as_of"], "model_version": info["model_version"],
            "data_label": "LIVE", "game_dates": {gid: str(pd.Timestamp(game["game_date_et"]).date())}}
    bodies = SD.quote_side_records(dist, q, expo, meta)
    rid = dist.set_index(["player_id", "market"])["prediction_receipt_id"].to_dict()
    for b in bodies:
        b.update({"quote_refresh_id": refresh_id, "quote_refresh_version": QR_VERSION, "priced_at": now.isoformat(),
                  "tier": request["tier"], "tip_utc": _utc(game["scheduled_tip_utc"]).isoformat(),
                  "cutoff_utc": _utc(game["cutoff"]).isoformat(), "window": PC.VALIDATED_PREDICTION_CUTOFF,
                  "pmf_source_kind": src["kind"], "pmf_source_id": src["id"], "pmf_sha256": info["pmf_sha256"],
                  "pmf_prediction_receipt_id": rid.get((b["player_id"], b["market"])),
                  "quote_event_id": request["event_id"], "quote_collection": Path(collection).name,
                  "quote_response_sha256": request.get("response_sha256")})
    out.update(status="REPRICED" if bodies else "NO_MATCHED_VALID_QUOTE", receipts=len(bodies))
    return out, bodies, q


def game_day(game: dict) -> str:
    return SH._utc(game["scheduled_tip_utc"]).tz_convert("America/New_York").date().isoformat()


def reprice(ops_root: Path, game: dict, src: dict, collection: Path, slate: pd.DataFrame, tidx: dict, now,
            refresh_id: str, request: dict) -> dict:
    """price_collection + persistence: the QUOTE_SIDE receipts are appended to quotes/<D>/reprice_side_monitor.jsonl
    and the capture (every normalized row with its status) becomes a quote observation (prod.quote_obs) for the Board."""
    out, bodies, q = price_collection(game, src, collection, slate, tidx, now, refresh_id, request)
    day = game_day(game)
    QO.record(ops_root, day, game["canonical_game_id"], request["event_id"],
              json.loads(Path(collection).read_text())["collector_received_at"], q, "QUOTE_REFRESH",
              {"refresh_id": refresh_id, "collection": Path(collection).name,
               "discovery_status": request.get("discovery_status"), "credits": request.get("credits")})
    if bodies:
        SD.append([{"record_type": "QUOTE_SIDE", "body": b} for b in bodies], Path(ops_root) / "quotes" / day / REFRESH_FILE)
    return out


# ============================================================================ quote-integration certification
CERT_DISCOVERY_BATCH_SIZE = 8    # market discovery (1 credit each) on at most this many candidates per region pass
CERT_DISCOVERY_REGIONS = (PC.ODDS_REGIONS, "us2")   # `us` first; `us2` ONLY when `us` showed no supported market
CERT_STRONG_MARKETS = 3          # early-stop discovery once a candidate advertises >= 3 supported markets
CERT_STRONG_BOOKS = 2            #   ... on >= 2 bookmakers (stop rule only: weaker supported candidates stay selectable)
CERT_MAX_ODDS_EVENTS = 2         # event-odds requests per attempt (the top-ranked event; one more if it returned none)
CERT_CHECKS = ("GAME_MATCH", "PLAYER_MATCH", "STAT_MATCH", "LINE_VALIDATION", "PRICE_VALIDATION",
               "FRESHNESS_VALIDATION", "PMF_LOOKUP", "PRICING_VALIDATION", "RAW_EVIDENCE_PERSISTENCE")


def certification_candidates(ctx: SH.Ctx, st: dict, now, sl: pd.DataFrame, tidx: dict) -> list[dict]:
    """Today's (ET) forecasted, pre-tip D-I games inside the prop window whose event maps strictly and whose PMF is
    already persisted, ordered by tip (then game id)."""
    now = _utc(now)
    sch = SH.schedule(st)
    today = now.tz_convert("America/New_York").date()
    g = sch[(sch["game_universe"] == "DI_VS_DI") & ~sch["not_played"] & sch["home_points"].isna() & (sch["cutoff"] > now)
            & (sch["scheduled_tip_utc"] > now)
            & (sch["scheduled_tip_utc"] - now <= pd.Timedelta(hours=PC.ODDS_PROP_WINDOW_HOURS))
            & (pd.to_datetime(sch["game_date_et"]).dt.date == today)]
    m = OF.match_events((OF.listing(now, ctx.cache_raw) or {}).get("events"), sl, tidx)
    ev = {gg: e for gg, e in zip(m["canonical_game_id"], m["provider_event_id"]) if isinstance(gg, str)}
    out = []
    for r in g.sort_values(["scheduled_tip_utc", "canonical_game_id"]).to_dict(orient="records"):
        src = latest_pmf_source(ctx.ops_root, r["canonical_game_id"], r["cutoff"], now)
        if src is not None and r["canonical_game_id"] in ev:
            out.append({"game": r, "event_id": ev[r["canonical_game_id"]], "src": src})
    return out


def rank(discovered: list[dict]) -> list[dict]:
    """Deterministic certification order: most supported markets advertised, then most bookmakers advertising them,
    then earliest tip, then game id. Events advertising none are dropped."""
    x = [d for d in discovered if d["supported_market_count"] > 0]
    return sorted(x, key=lambda d: (-d["supported_market_count"], -d["bookmakers_advertising"],
                                    _utc(d["tip"]), d["game_id"]))


def certification_checks(game: dict, src: dict, collection: Path, q: pd.DataFrame, bodies: list, summary: dict,
                         cache_root: Path) -> dict:
    """The nine certification checks on one real event's quotes priced at the persisted PMF (CERT_CHECKS)."""
    from cbb_platform import raw_store as RS
    from cbb_platform.prod import consensus as CN
    gid = game["canonical_game_id"]
    doc = json.loads(Path(collection).read_text())
    requested = set((doc.get("market_discovery") or {}).get("requested_markets") or [])
    props = q[q["stat"].notna()] if len(q) else q
    matched = props[props["canonical_player_id"].map(lambda v: isinstance(v, str))] if len(props) else props
    valid = matched[matched["quote_status"] == "VALID"] if len(matched) else matched
    returned = {m.get("key") for e in doc["events"] for b in e.get("bookmakers") or [] for m in b.get("markets") or []}
    c = {}
    c["GAME_MATCH"] = {"pass": bool(len(props)) and bool((props["canonical_game_id"] == gid).all()),
                       "prop_quotes": int(len(props)), "canonical_game_id": gid}
    pmf_players = set(PE.read(src["pmfs"]).query("game_id == @gid")["player_id"]) if src else set()
    c["PLAYER_MATCH"] = {"pass": bool(len(matched)) and set(matched["canonical_player_id"]) <= pmf_players,
                         "matched_quotes": int(len(matched)), "players": int(matched["canonical_player_id"].nunique()) if len(matched) else 0,
                         "unmatched_names": sorted(props.loc[props["canonical_player_id"].map(lambda v: not isinstance(v, str)),
                                                             "provider_player_name"].unique().tolist())[:50] if len(props) else []}
    key_ok = bool(len(props)) and bool((props["market_key"].map(QT.KEY_TO_STAT) == props["stat"]).all())
    c["STAT_MATCH"] = {"pass": key_ok and returned <= requested <= set(QT.REQUEST_MARKETS)
                       and all(b["market"] in PC.MARKETS for b in bodies),
                       "requested_markets": sorted(requested), "returned_markets": sorted(returned)}
    bad = int((props["suppression_reason"] == "INVALID_QUOTE").sum()) if len(props) else 0
    c["LINE_VALIDATION"] = {"pass": bool(len(valid)) and bad <= 0.01 * len(props), "invalid_quotes": bad}
    pv = valid.groupby(["sportsbook", "canonical_player_id", "stat", "line"])["side"].nunique() if len(valid) else pd.Series(dtype=int)
    c["PRICE_VALIDATION"] = {"pass": bool(len(valid)) and bool((valid["decimal_odds"] > 1).all())
                             and bool(valid["american_odds"].map(lambda o: abs(float(o)) >= 100).all()) and bool((pv == 2).any()),
                             "paired_book_lines": int((pv == 2).sum())}
    ts = props["market_last_update"].notna() & (props["market_last_update"] <= props["collector_received_at"]) if len(props) else pd.Series(dtype=bool)
    c["FRESHNESS_VALIDATION"] = {"pass": bool(len(props)) and bool(ts.all()) and bool(len(valid)),
                                 "stale_or_post_cutoff": int(matched["suppression_reason"].isin(["STALE_QUOTE", "POST_CUTOFF_QUOTE"]).sum()) if len(matched) else 0}
    c["PMF_LOOKUP"] = {"pass": src is not None and summary.get("pmfs", 0) > 0
                       and all(b.get("pmf_prediction_receipt_id") for b in bodies) and bool(bodies),
                       "pmf_source": src["kind"] if src else None, "pmf_source_id": src["id"] if src else None,
                       "pmf_sha256": bodies[0]["pmf_sha256"] if bodies else None}
    trace, mass = 0, 0
    for b in bodies:
        hit = valid[(valid["canonical_player_id"] == b["player_id"]) & (valid["stat"] == b["market"])
                    & (valid["line"] == b["line"]) & (valid["side"] == b["side"]) & (valid["sportsbook"] == b["sportsbook"])
                    & ((valid["decimal_odds"] - b["book_decimal_odds"]).abs() < 1e-12)]
        trace += len(hit) == 0
        mass += abs(b["p_over"] + b["p_under"] + b["p_push"] - 1) > 1e-9
    board_ok = 0
    for _, g in matched.groupby(["canonical_player_id", "stat"]) if len(matched) else []:
        board_ok += CN.consensus(g)[0]["market_status"] == "VALID_MARKET"
    c["PRICING_VALIDATION"] = {"pass": bool(bodies) and trace == 0 and mass == 0 and board_ok > 0,
                               "quote_side_receipts": len(bodies), "untraceable": trace, "mass_errors": mass,
                               "consensus_valid_markets": int(board_ok)}
    eo = (doc.get("requests") or {}).get("event_odds") or {}
    raw_ok = False
    if eo.get("cache_path"):
        rp = Path(cache_root) / eo["cache_path"]
        raw_ok = rp.exists() and hashlib.sha256(RS.read_payload(rp)).hexdigest() == eo.get("response_sha256")
    c["RAW_EVIDENCE_PERSISTENCE"] = {"pass": bool(raw_ok) and Path(collection).exists(),
                                     "collection": Path(collection).name, "response_sha256": eo.get("response_sha256")}
    return c


def strong(d: dict) -> bool:
    """Discovery is strong enough to stop scanning (never a quote-validity or model gate)."""
    return d["supported_market_count"] >= CERT_STRONG_MARKETS and d["bookmakers_advertising"] >= CERT_STRONG_BOOKS


def discovery_pass(session: OA.Session, batch: list[dict], region: str, rep: dict, found: dict) -> list[dict]:
    """One region pass over the bounded candidate batch in its deterministic order (tip, game id): /markets per event,
    stopping right after the first strong candidate. Returns the supported candidates discovered, ranked."""
    for c in batch:
        gid = c["game"]["canonical_game_id"]
        try:
            d = OF.discover(session, c["event_id"], region=region)
        except (OA.OddsProviderFailure, OA.OddsGuardViolation, OA.OddsBudgetExceeded) as e:
            rep["discovery"].append({"game_id": gid, "event_id": c["event_id"], "region": region, "status": "FAILED",
                                     "error": str(e)[:200]})
            continue
        found[(region, c["event_id"])] = (c, d)
        rep["discovery"].append({"game_id": gid, "event_id": c["event_id"], "region": region,
                                 "tip": _utc(c["game"]["scheduled_tip_utc"]).isoformat(), "status": d["status"],
                                 "supported_market_count": d["supported_market_count"],
                                 "bookmakers_advertising": d["bookmakers_advertising"],
                                 "supported_markets": d["supported_markets"]})
        if strong(d):
            rep["discovery_early_stop"] = {"region": region, "event_id": c["event_id"]}
            break
    return rank([r for r in rep["discovery"] if r["region"] == region and r["status"] != "FAILED"])


def _accounting(rep: dict, session: OA.Session) -> dict:
    """Structured discovery vs certification-attempt accounting (shadow_tick._validation_state reads these fields)."""
    ent = session.entries
    net = [e for e in ent if e.get("status") not in ("CACHE_HIT",) and not str(e.get("status") or "").startswith("REFUSED")]
    mk = [e for e in net if e.get("endpoint") == "event_markets"]
    n_odds = sum(1 for e in net if e.get("endpoint") == "event_odds")
    counts = bool(rep.get("counts_toward_certification_limit"))
    return {"discovery_run": bool(rep.get("discovery")), "discovery_only": not counts, "event_odds_request_count": n_odds,
            "counts_toward_certification_limit": counts,
            "us_market_discovery_calls": sum(1 for e in mk if (e.get("params") or {}).get("regions") == "us"),
            "us2_market_discovery_calls": sum(1 for e in mk if (e.get("params") or {}).get("regions") == "us2"),
            "supported_markets_found": sum(1 for r in rep.get("discovery") or [] if r.get("supported_market_count")),
            "requests": _reqs(session)}


def certify(ctx: SH.Ctx, st: dict, now=None, quota_remaining: int | None = None, progress: dict | None = None) -> dict:
    """One certification invocation (shadow_tick.step_validate). DISCOVERY: today's candidates in deterministic order,
    at most CERT_DISCOVERY_BATCH_SIZE /markets calls per region, `us` first and stopping at the first strong candidate;
    `us2` only if `us` showed no supported market at all. The best discovered supported candidate (ranked: markets,
    bookmakers, tip, game id) is then requested with ONE event-odds call in the SAME region for exactly its advertised
    markets (one more only if it returned no prop rows); the nine checks on its real quotes priced at the
    already-persisted PMF. Only an invocation that reaches event odds is a CERTIFICATION ATTEMPT
    (counts_toward_certification_limit; marked in `progress` before the first event-odds call so a crash still counts);
    discovery-only outcomes never consume the day's attempts. Nothing is simulated or fitted."""
    now = _utc(now or ctx.now())
    progress = {} if progress is None else progress
    rid = "V" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    rep = {"certification_id": rid, "attempted_at": now.isoformat(), "checks_required": list(CERT_CHECKS),
           "integration_fingerprint": QC.fingerprint(), "discovery": [], "odds_events": [],
           "counts_toward_certification_limit": False}
    session = SH._session(ctx, f"{rid}:certify")
    session.quota_remaining = quota_remaining          # known from the tick's live (free) listing, when there was one
    done = lambda status, **kw: {**rep, "status": status, **kw, **_accounting(rep, session)}   # noqa: E731
    sl = SL.build(st["increment_db"], now, st["manifest"].get("schedule_as_of") or st["manifest"]["source_cutoff"],
                  PC.ODDS_PROP_WINDOW_HOURS)
    tidx = MT.team_index(SH.team_names(ctx, st), MT.odds_team_map())
    try:
        # 0 credits; live when the quota (every paid call is checked against it) is not known yet
        session.get("events", OF.events_params(now, now + pd.Timedelta(hours=PC.ODDS_PROP_WINDOW_HOURS)),
                    max_age_seconds=0 if quota_remaining is None else PC.ODDS_EVENTS_MAX_AGE_HOURS * 3600,
                    purpose="events listing (0 credits)")
    except (OA.OddsProviderFailure, OA.OddsGuardViolation, OA.OddsBudgetExceeded) as e:
        return done("ERROR", error=f"events listing: {str(e)[:200]}")
    session.event_commence = OF.event_commence_resolver(ctx.cache_raw, now)
    cands = certification_candidates(ctx, st, now, sl, tidx)
    rep["candidates"] = len(cands)
    if not cands:
        return done("INCONCLUSIVE_NO_CANDIDATE_GAME")
    batch = cands[:CERT_DISCOVERY_BATCH_SIZE]
    rep["discovery_batch"] = len(batch)
    rep["discovery_truncated"] = max(0, len(cands) - len(batch))
    found: dict = {}
    ranked: list[dict] = []
    for region in CERT_DISCOVERY_REGIONS:
        ranked = discovery_pass(session, batch, region, rep, found)
        ours = [r for r in rep["discovery"] if r["region"] == region]
        if ranked or all(r["status"] == "FAILED" for r in ours):
            break                                        # a supported candidate, or the provider failed: no fallback
    rep["ranking"] = [r["event_id"] for r in ranked]
    rep["ranking_region"] = ranked[0]["region"] if ranked else None
    if not ranked:
        failed = [r for r in rep["discovery"] if r["status"] == "FAILED"]
        if failed and len(failed) == len(rep["discovery"]):
            return done("ERROR", error=f"market discovery failed: {failed[0]['error']}")
        return done("INCONCLUSIVE_" + OF.NO_SUPPORTED)
    rep["selected_region"] = ranked[0]["region"]
    for r in ranked[:CERT_MAX_ODDS_EVENTS]:
        c, d = found[(r["region"], r["event_id"])]
        rep["counts_toward_certification_limit"] = progress["counts_toward_certification_limit"] = True
        try:
            path = OF.collect(session, [r["event_id"]], ctx.collections, discovered={r["event_id"]: d})[-1]
        except (OA.OddsProviderFailure, OA.OddsGuardViolation, OA.OddsBudgetExceeded) as e:
            rep["odds_events"].append({"event_id": r["event_id"], "region": r["region"], "status": "FAILED",
                                       "error": str(e)[:200]})
            continue
        req = {"event_id": r["event_id"], "region": r["region"], "tier": "CERTIFICATION", **OF.collection_receipt(path)}
        summ, bodies, q = price_collection(c["game"], c["src"], path, sl, tidx, now, rid, req)
        props = int(q["stat"].notna().sum()) if len(q) else 0
        rep["odds_events"].append({**req, "collection": path.name, "prop_quotes": props})
        if not props:
            continue                                     # advertised, but nothing returned: the next-ranked event
        checks = certification_checks(c["game"], c["src"], path, q, bodies, summ, ctx.cache_raw)
        ok = all(x["pass"] for x in checks.values())
        run_dir = Path(ctx.prod_root) / "odds" / "validation_runs" / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        q.to_csv(run_dir / "quotes_normalized.csv", index=False)
        (run_dir / "quote_side_receipts.jsonl").write_text("".join(json.dumps(x, sort_keys=True, default=str) + "\n"
                                                                   for x in bodies))
        out = done("PASS" if ok else "FAIL", validated_at=now.isoformat(), event_id=r["event_id"],
                   canonical_game_id=c["game"]["canonical_game_id"], feed_version=PC.ODDS_FEED_VERSION,
                   collection=path.name, pmf_source={k: str(v) for k, v in c["src"].items()}, checks=checks,
                   **{k: ("PASS" if checks[k]["pass"] else "FAIL") for k in CERT_CHECKS},
                   validation_receipt=str(run_dir / "certification.json"))
        (run_dir / "certification.json").write_text(json.dumps(out, indent=1, default=str))
        out["VALIDATION_RECEIPT_WRITTEN"] = (run_dir / "certification.json").exists()
        return out
    return done("INCONCLUSIVE_NO_PLAYER_PROPS_RETURNED")


def _reqs(session: OA.Session) -> list[dict]:
    return [{k: e.get(k) for k in ("endpoint", "status", "cost", "markets_returned", "response_sha256", "received_at",
                                   "quota_remaining", "quota_used", "http_status")} | {"event_id": (e.get("params") or {}).get("eventId")}
            for e in session.entries]


# ============================================================================ one scheduler wake
def run(ctx: SH.Ctx, dry_run: bool = False) -> dict:
    """One wake of the quote scheduler. Requests only due games; reprices; never fits or simulates."""
    now = ctx.now()
    rid = "Q" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    rep = {"version": QR_VERSION, "refresh_id": rid, "now": now.isoformat(), "requests": 0, "credits": 0,
           "refits_triggered": 0, "simulations_triggered": 0, "games": []}
    st = ST.current(ctx.state_root)
    if st is None:
        return {**rep, "status": "BLOCKED", "reason": "no promoted 2026-27 state"}
    vs = SH._validation_state(ctx, now)
    if not vs["passed"]:
        return {**rep, "status": "SKIPPED", "reason": f"QUOTE_INTEGRATION_NOT_VALIDATED ({vs['certification_reason']}; "
                                                     "the official tick certifies first)"}
    sch = SH.schedule(st)
    dec = SH.decided(ctx)
    open_ = sch[(sch["game_universe"] == "DI_VS_DI") & ~sch["not_played"] & sch["home_points"].isna()
                & (sch["cutoff"] > now) & (sch["cutoff"] - pd.Timedelta(minutes=TIERS[0][1]) <= now)]
    last = last_requests(ctx.ops_root)
    plan = []
    for g in open_.sort_values(["cutoff", "canonical_game_id"]).to_dict(orient="records"):
        gid = g["canonical_game_id"]
        ok, tname, why = due(g["cutoff"], now, last.get(gid), official_pending=gid not in dec)
        src = latest_pmf_source(ctx.ops_root, gid, g["cutoff"], now) if ok else None
        if ok and src is None:
            ok, why = False, "NO_PERSISTED_PMF (no forecast snapshot / official PMF yet)"
        rep["games"].append({"game_id": gid, "tier": tname, "due": ok, "reason": why})
        if ok:
            plan.append((g, tname, src))
    rep["due_games"] = len(plan)
    if not plan or dry_run:
        return {**rep, "status": "NOTHING_DUE" if not plan else "DRY_RUN"}
    session = SH._session(ctx, f"{rid}:quote_refresh")
    sl = SL.build(st["increment_db"], now, st["manifest"].get("schedule_as_of") or st["manifest"]["source_cutoff"],
                  PC.ODDS_PROP_WINDOW_HOURS)
    tidx = MT.team_index(SH.team_names(ctx, st), MT.odds_team_map())
    lst = OF.listing(now, ctx.cache_raw)

    def matched(lst_):
        m = OF.match_events(lst_["events"] if lst_ else None, sl, tidx)
        return {g: e for g, e in zip(m["canonical_game_id"], m["provider_event_id"]) if isinstance(g, str)}
    ev = matched(lst)
    if any(g["canonical_game_id"] not in ev for g, _, _ in plan) and \
            (lst is None or now - lst["received_at"] >= pd.Timedelta(minutes=EVENTS_REFRESH_MINUTES)):
        try:                                                              # FREE listing (0 credits)
            session.get("events", OF.events_params(now, now + pd.Timedelta(hours=PC.ODDS_PROP_WINDOW_HOURS)),
                        max_age_seconds=0, purpose="events listing (0 credits)")
            lst = OF.listing(now, ctx.cache_raw)
            ev = matched(lst)
        except (OA.OddsProviderFailure, OA.OddsGuardViolation, OA.OddsBudgetExceeded) as e:
            rep["events_listing_error"] = str(e)[:200]
    session.event_commence = OF.event_commence_resolver(ctx.cache_raw, now)
    reqs = []
    for g, tname, src in plan:
        gid = g["canonical_game_id"]
        row = next(x for x in rep["games"] if x["game_id"] == gid)
        eid = ev.get(gid)
        if eid is None:
            row.update(due=False, reason="NO_MATCHED_ODDS_EVENT")
            continue
        req = {"refresh_id": rid, "game_id": gid, "event_id": eid, "tier": tname, "requested_at": ctx.now().isoformat(),
               "cutoff_utc": _utc(g["cutoff"]).isoformat()}
        try:
            paths = OF.collect(session, [eid], ctx.collections)
        except OA.OddsBudgetExceeded as e:
            reqs.append({**req, "status": "BUDGET_EXHAUSTED", "error": str(e)[:200]})
            row["result"] = "BUDGET_EXHAUSTED"
            break
        except (OA.OddsProviderFailure, OA.OddsGuardViolation) as e:
            reqs.append({**req, "status": "FAILED", "error": str(e)[:200]})
            row["result"] = "FAILED"
            continue
        rc = OF.collection_receipt(paths[-1])
        req.update(**rc, collection=paths[-1].name)
        reqs.append(req)
        row["result"] = reprice(ctx.ops_root, g, src, paths[-1], sl, tidx, now, rid, req)
    OS.append_jsonl(requests_path(ctx.ops_root), reqs)
    inv = SH.check_material_failure(ctx, session, now)
    if inv:
        rep["certification_invalidated"] = inv
    if reqs:
        _write_last(ctx.ops_root)
    rep["requests"] = sum((r.get("markets_status") in LIVE_STATUSES) + (r.get("odds_status") in LIVE_STATUSES) for r in reqs)
    rep["market_discovery"] = pd.Series([r.get("discovery_status") for r in reqs if r.get("discovery_status")]
                                        ).value_counts().to_dict() if reqs else {}
    rep["credits"] = sum(int(r.get("credits") or 0) for r in reqs)
    rep["receipts"] = sum(int((x.get("result") or {}).get("receipts", 0)) for x in rep["games"] if isinstance(x.get("result"), dict))
    rep["status"] = "RAN"
    return rep


def _bodies(p: Path) -> pd.DataFrame:
    SD.verify(p)                                                     # hash chain (any edit / reorder refused)
    return pd.DataFrame([r["body"] for _, r in SD._records(p) if r["record_type"] == "QUOTE_SIDE"])


def receipts_for_day(ops_root: Path, date: str) -> pd.DataFrame:
    """The ET slate day's repriced QUOTE_SIDE bodies (every field, hash chain verified)."""
    p = Path(ops_root) / "quotes" / date / REFRESH_FILE
    return _bodies(p) if p.exists() else pd.DataFrame()


def receipts_for_game(ops_root: Path, game_id: str) -> pd.DataFrame:
    parts = []
    for p in sorted((Path(ops_root) / "quotes").glob(f"*/{REFRESH_FILE}")):
        d = _bodies(p)
        if len(d):
            parts.append(d[d["game_id"] == game_id])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
