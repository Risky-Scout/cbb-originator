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

import json
import os
import uuid
from pathlib import Path

import pandas as pd

from cbb_platform.prod import (board as BO, config as PC, matching as MT, odds_acquire as OA, odds_feed as OF,
                               ops_store as OS, pmf_evidence as PE, quotes as QT, shadow_tick as SH, side_monitor as SD,
                               slate as SL, state as ST)

QR_VERSION = "cbb-prod-quote-refresh-1.0.0"
SLOT_MINUTES = 10
# (name, earliest minutes before cutoff (inclusive), latest minutes before cutoff (exclusive), interval minutes)
TIERS = (("T6H_T2H", 360, 120, 60), ("T2H_T30M", 120, 30, 30), ("T30M_CUTOFF", 30, 0, 10))
EVENTS_REFRESH_MINUTES = 30                 # a due game with no matched event refreshes the FREE events listing
REFRESH_FILE = "reprice_side_monitor.jsonl"


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
        if r.get("status") in ("LIVE", "CACHE"):
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


def reprice(ops_root: Path, game: dict, src: dict, collection: Path, slate: pd.DataFrame, tidx: dict, now,
            refresh_id: str, request: dict) -> dict:
    """QUOTE_SIDE receipts of one game: the persisted PMF `src` priced at the quotes of `collection`. Appends them to
    quotes/<D>/reprice_side_monitor.jsonl. Nothing is simulated, fitted or recomputed."""
    now = _utc(now)
    gid = game["canonical_game_id"]
    dist, expo, cands, info = _dist_and_exposure(src, gid)
    out = {"game_id": gid, "pmf_source": src["kind"], "pmf_source_id": src["id"], "pmfs": int(len(dist)), "receipts": 0}
    if not len(dist):
        return {**out, "status": "NO_PMF_ROWS"}
    q = QT.load_collected(collection)
    if len(q):
        q = MT.match_games(q, slate, tidx)
        q = MT.match_players(q, cands, None)
        q = BO.quote_freshness(q, now, slate.set_index("canonical_game_id")["start_time"].to_dict())
    out["quote_rows"] = int(len(q))
    out["valid_quote_rows"] = int((q["quote_status"] == "VALID").sum()) if len(q) else 0
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
    if bodies:
        day = SH._utc(game["scheduled_tip_utc"]).tz_convert("America/New_York").date().isoformat()
        SD.append([{"record_type": "QUOTE_SIDE", "body": b} for b in bodies], Path(ops_root) / "quotes" / day / REFRESH_FILE)
    out.update(status="REPRICED" if bodies else "NO_MATCHED_VALID_QUOTE", receipts=len(bodies))
    return out


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
    if not SH._validation_state(ctx, now)["passed"]:
        return {**rep, "status": "SKIPPED", "reason": "QUOTE_INTEGRATION_NOT_VALIDATED (the official tick validates first)"}
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
        ent = [x for x in session.entries if x.get("endpoint") == "event_odds"
               and x.get("params", {}).get("eventId") == eid][-1]
        req.update(status=ent["status"], received_at=ent.get("received_at"), response_sha256=ent.get("response_sha256"),
                   credits=int(ent.get("cost") or 0), collection=paths[-1].name)
        reqs.append(req)
        row["result"] = reprice(ctx.ops_root, g, src, paths[-1], sl, tidx, now, rid, req)
    OS.append_jsonl(requests_path(ctx.ops_root), reqs)
    if reqs:
        _write_last(ctx.ops_root)
    rep["requests"] = sum(1 for r in reqs if r.get("status") == "LIVE")
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
