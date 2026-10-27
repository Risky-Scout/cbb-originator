"""Per-game schedule-freshness gate for REAL prediction / pricing / publication (architect decision 2026-10-26).

    At every official production checkpoint, for each game: if its schedule state was verified by the provider within
    the previous SCHEDULE_FRESHNESS_MAX_AGE_HOURS (2 h), PASS. Otherwise run ONE targeted refresh of the schedule
    window(s) holding the stale games (never a full-season refetch), then require
        canonical game identity unchanged, home / away unchanged, scheduled tip unchanged and EXACT,
        game listed and not cancelled / postponed, game not started, freshness <= 2 h.
    Any failure or unresolved contradiction: SCHEDULE_FRESHNESS_GATE = FAIL for that game, which means
    NO PUBLICATION, NO PRICING RECOMMENDATION, NO ACTIONABLE BET. Every evaluation is written to an append-only audit log.

"Verified" = the latest instant the provider returned THE SAME payload the current state was built from (FETCHED or
FETCHED_UNCHANGED in the CBBD request ledger; an unchanged re-fetch counts). A verification newer than the checkpoint is
never used. This gate is additive: the 36 h run-level STALE_STATE rule (PC.SCHEDULE_MAX_AGE_HOURS) is unchanged and
still governs recording; nothing here reaches a forecast.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from cbb_platform.prod import cbbd_acquire as A, config as PC, state as ST, sync as SY

PASS, FAIL = "PASS", "FAIL"
# reasons (each one fails the gate for its game)
STALE_AFTER_REFRESH = "SCHEDULE_STALE_AFTER_REFRESH"
REFRESH_FAILED = "SCHEDULE_REFRESH_FAILED"
GAME_NOT_IN_SCHEDULE = "GAME_NOT_IN_CURRENT_SCHEDULE"
IDENTITY_CHANGED = "GAME_IDENTITY_CHANGED"
TEAMS_CHANGED = "HOME_AWAY_CHANGED"
TIP_CHANGED = "SCHEDULED_TIP_CHANGED"
TIP_NOT_EXACT = "SCHEDULED_TIP_NOT_EXACT"
NOT_LISTED = "GAME_NOT_LISTED_OR_CANCELLED"
STARTED = "GAME_ALREADY_STARTED"
NO_VERIFICATION = "NO_PROVIDER_VERIFICATION"
REASONS = (STALE_AFTER_REFRESH, REFRESH_FAILED, GAME_NOT_IN_SCHEDULE, IDENTITY_CHANGED, TEAMS_CHANGED, TIP_CHANGED,
           TIP_NOT_EXACT, NOT_LISTED, STARTED, NO_VERIFICATION)
AUDIT_PATH = PC.PROD_ROOT / "gates" / "schedule_freshness.jsonl"


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def windows_for(tip) -> list[str]:
    """startDateRange of every schedule window whose [start, end] (00:00Z bounds, inclusive) holds the tip."""
    d = _utc(tip)
    out = []
    for a, b in SY.schedule_windows():
        lo, hi = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
        if lo <= d <= hi:
            out.append(a)
    return out


def window_verification(manifest: dict, ledger_path: Path, checkpoint_at) -> dict:
    """{window startDateRange: latest provider verification <= checkpoint of the payload the state was built from}."""
    cp = _utc(checkpoint_at)
    prov = manifest.get("feed_provenance") or {}
    want = {}
    for ep, p in SY.plan_schedule():
        k = A.request_key(ep, p)
        if k in prov:
            want[k] = (p["startDateRange"], prov[k]["sha256"], _utc(prov[k]["received_at"]))
    out = {w: rec for w, _, rec in want.values() if rec <= cp}         # receipt itself is a verification
    for e in A.ledger(ledger_path) if Path(ledger_path).exists() else []:
        k = e.get("request_key")
        if k not in want or e.get("status") not in ("FETCHED", "FETCHED_UNCHANGED"):
            continue
        w, sha, _ = want[k]
        t = e.get("verified_at") or e.get("received_at")
        if e.get("response_sha256") == sha and t and _utc(t) <= cp and (w not in out or _utc(t) > out[w]):
            out[w] = _utc(t)
    return out


def current_schedule(state_root: Path) -> tuple[dict | None, pd.DataFrame]:
    st = ST.current(state_root)
    if st is None:
        return None, pd.DataFrame()
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    try:
        s = con.execute("SELECT * FROM metadata.prod_schedule").df()
    finally:
        con.close()
    return st, s


def check_game(pred: dict, sched: pd.DataFrame, verified: dict, checkpoint_at) -> dict:
    """pred: the game as the prediction saw it (canonical_game_id, provider_game_id, home_team_id, away_team_id,
    start_time, tip_time_quality). Returns {status, reasons, verified_at, age_hours, windows}."""
    cp = _utc(checkpoint_at)
    gid = pred["canonical_game_id"]
    reasons = []
    row = sched[sched["canonical_game_id"] == gid] if len(sched) else sched
    tip = _utc(pred["start_time"])
    wins = windows_for(tip)
    ver = [verified.get(w) for w in wins]
    vat = min(ver) if wins and all(v is not None for v in ver) else None   # every holding window must be fresh
    age = None if vat is None else (cp - vat).total_seconds() / 3600.0
    if vat is None:
        reasons.append(NO_VERIFICATION)
    if len(row) != 1:
        reasons.append(GAME_NOT_IN_SCHEDULE)
    else:
        r = row.iloc[0]
        if str(r["provider_game_id"]) != str(pred["provider_game_id"]):
            reasons.append(IDENTITY_CHANGED)
        if (r["home_team_id"], r["away_team_id"]) != (pred["home_team_id"], pred["away_team_id"]):
            reasons.append(TEAMS_CHANGED)
        if pd.isna(r["start_time"]) or _utc(r["start_time"]) != tip:
            reasons.append(TIP_CHANGED)
        if str(r["tip_time_quality"]) != "EXACT" or str(pred.get("tip_time_quality")) != "EXACT":
            reasons.append(TIP_NOT_EXACT)
        status = str(r.get("status") or "").strip().lower()
        if not bool(r["listed_in_latest_schedule"]) or status in ("canceled", "cancelled", "postponed"):
            reasons.append(NOT_LISTED)
    if cp >= tip:
        reasons.append(STARTED)
    fresh = age is not None and 0 <= age <= PC.SCHEDULE_FRESHNESS_MAX_AGE_HOURS
    return {"canonical_game_id": gid, "fresh": fresh, "reasons": sorted(set(reasons)),
            "verified_at": None if vat is None else vat.isoformat(), "age_hours": age, "windows": wins}


def default_refresh(windows: list[str]) -> dict:
    """Targeted refresh: the normal production sync with ONLY the named schedule windows forced past their TTL (the
    other windows and stages keep their TTL / cache). Never a full-season refetch."""
    return SY.run(refresh_schedule_windows=windows)


def gate(predicted: pd.DataFrame, checkpoint_at, *, state_root: Path = PC.STATE_ROOT,
         ledger_path: Path = PC.CBBD_REQUEST_LEDGER, refresh: Callable[[list[str]], dict] | None = default_refresh,
         audit_path: Path | None = AUDIT_PATH, now: Callable[[], pd.Timestamp] | None = None) -> dict:
    """Evaluate every predicted game at `checkpoint_at`; refresh stale windows once; re-evaluate. Returns
    {"games": {gid: {"SCHEDULE_FRESHNESS_GATE": PASS|FAIL, "reasons": [...], ...}}, "refresh": record|None}.
    `now` (tests) gives the instant used for the post-refresh evaluation (default: checkpoint_at)."""
    cp = _utc(checkpoint_at)
    preds = predicted.to_dict(orient="records")
    st, sched = current_schedule(state_root)
    ver = window_verification(st["manifest"], ledger_path, cp) if st else {}
    first = {p["canonical_game_id"]: check_game(p, sched, ver, cp) for p in preds}
    stale = [g for g, c in first.items() if not c["fresh"] and STARTED not in c["reasons"]]
    refresh_rec, after = None, dict(first)
    if stale and refresh is not None:
        wins = sorted({w for g in stale for w in first[g]["windows"]})
        try:
            refresh_rec = refresh(wins) or {}
        except Exception as e:                                   # noqa: BLE001 -- any refresh error fails closed
            refresh_rec = {"status": "FAILED", "error": f"{type(e).__name__}: {str(e)[:300]}"}
        refresh_rec = {"windows": wins, **{k: refresh_rec.get(k) for k in ("status", "reason", "error", "sync_id",
                                                                             "state_version", "source_cutoff")}}
        t2 = _utc(now()) if now else cp
        st, sched = current_schedule(state_root)
        ver = window_verification(st["manifest"], ledger_path, t2) if st else {}
        for p in preds:
            if p["canonical_game_id"] in stale:
                after[p["canonical_game_id"]] = {**check_game(p, sched, ver, t2), "refreshed": True}
    games = {}
    for g, c in after.items():
        reasons = list(c["reasons"])
        if not c["fresh"]:
            if c.get("refreshed") and refresh_rec and refresh_rec.get("status") == "FAILED":
                reasons.append(REFRESH_FAILED)
            elif NO_VERIFICATION not in reasons:
                reasons.append(STALE_AFTER_REFRESH)
        reasons = sorted(set(reasons))
        games[g] = {**c, "reasons": reasons, "SCHEDULE_FRESHNESS_GATE": FAIL if reasons else PASS,
                    "publication_allowed": not reasons, "pricing_allowed": not reasons,
                    "actionable_bet_allowed": not reasons}
    out = {"checkpoint_at": cp.isoformat(), "max_age_hours": PC.SCHEDULE_FRESHNESS_MAX_AGE_HOURS,
           "policy": PC.SCHEDULE_FRESHNESS_POLICY, "refresh": refresh_rec, "games": games}
    if audit_path is not None:
        Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(audit_path).open("a") as f:
            for g, c in games.items():
                f.write(json.dumps({"record_type": "SCHEDULE_FRESHNESS", "checkpoint_at": out["checkpoint_at"],
                                    "canonical_game_id": g, **{k: c[k] for k in ("SCHEDULE_FRESHNESS_GATE", "reasons",
                                                                                 "verified_at", "age_hours", "windows")},
                                    "refreshed": bool(c.get("refreshed")), "refresh": refresh_rec},
                                   sort_keys=True, default=str) + "\n")
    return out
