"""THE prospective 2026-27 shadow tick: one idempotent command that decides what work is due and does only that.

    python -m cbb_platform.prod.shadow_tick [--restore] [--persist] [--push]

    verify frozen artifact hashes (BLOCK on mismatch)          -> load last-known-good state (restored if --restore)
    -> incremental CBBD sync, only if due                       -> free Odds API events listing, only if due
    -> first real-event quote validation (ONE paid request), only if eligible and never repeated after PASS
    -> games whose TIP_MINUS_1H cutoff is within SCORE_LEAD_MINUTES: ONE quote snapshot per event, then ONE run that
       draws ONE 20,000-draw JM1_FINAL bank per game (run.daily, unchanged), appended once to the shadow evidence
    -> games whose cutoff passed unscored: MISSED_PREDICTION_CUTOFF (never a retrospective "pregame" forecast)
    -> newly completed games: settlement appended (predictions are never mutated; a DNP is NO_ACTION, never a zero)
    -> prospective monitoring + daily / cumulative reports     -> DUE.json (what the cheap scheduler check reads)
    -> evidence tree commit (+ encrypted working set upload with --persist)

A second run with no new information makes no network call, no prediction, no quote, no settlement, no state version
and no commit: every step is gated by a due test on durable state (registry, ledgers, markers, state pointer).

Nothing here fits, refits, recalibrates, re-simulates historically or changes a forecast: scoring is run.daily on the
frozen e1353e5 artifacts, which the loader hash-verifies on every run. Readiness never exceeds SHADOW_ONLY and
WAGERING_ENABLED stays False. Historical PTS research is closed: no warning here triggers any model change.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform.prod import (config as PC, history_extract as HX, ledger as LG, loader as LD, matching as MT,
                               odds_acquire as OA, odds_feed as OF, ops_store as OS, pmf_evidence as PE,
                               prospective_monitor as PM,
                               run as RN, slate as SL, state as ST, sync as Y)

TICK_VERSION = "cbb-prod-shadow-tick-1.0.0"
DECIDED = ("PREDICTED", "EXCLUDED", "MISSED_PREDICTION_CUTOFF", "NOT_SCORED_AT_CUTOFF")
NOT_PLAYED = ("canceled", "cancelled", "postponed")
STAT_COLS = {"PTS": "points", "REB": "reb", "AST": "ast", "FG3M": "three_pm", "STL": "stl", "BLK": "blk", "TOV": "tov"}
MAX_INCONCLUSIVE_VALIDATIONS_PER_DAY = 3
EVENTS_LOOKAHEAD_HOURS = 14 * 24          # routine free listing horizon (first real event discovery); 0 credits


def _f(x):
    """float or None (NaN / None / non-numeric -> None)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def source_commit() -> str | None:
    """The operational code's commit, read from the checkout's own .git (no shell). GITHUB_SHA is only a fallback: in a
    scheduled run it names the default-branch commit that holds the workflow, not the checked-out release code."""
    sha = _git_head(PC.C.PROJECT_ROOT / ".git")
    return sha or os.environ.get("CBB_CODE_COMMIT") or os.environ.get("GITHUB_SHA") or None   # CBB_CODE_COMMIT: git-archive release


def _git_head(g: Path) -> str | None:
    try:
        if g.is_file():
            g = Path(g.read_text().split("gitdir:", 1)[1].strip())
        head = (g / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(" ", 1)[1]
        for root in (g, g.parent.parent if g.parent.name == "worktrees" else g):
            f = root / ref
            if f.exists():
                return f.read_text().strip()
            pk = root / "packed-refs"
            if pk.exists():
                for line in pk.read_text().splitlines():
                    if line.endswith(" " + ref):
                        return line.split(" ", 1)[0]
    except (OSError, IndexError):
        return None
    return None


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(x):
    return None if x is None or (not isinstance(x, str) and pd.isna(x)) else _utc(x).isoformat()


@dataclass
class Ctx:
    """Everything a tick touches. Defaults are the production paths; tests pass temporary roots and fake providers."""
    ops_root: Path = PC.OPS_ROOT
    prod_root: Path = PC.PROD_ROOT
    history_db: Path | None = None
    clock: object = None                      # () -> pd.Timestamp (UTC); default: wall clock
    cbbd_http: object = None                  # fake requests.get (tests); None: real
    odds_http: object = None
    cbbd_credential: object = None
    odds_credential: object = None
    sims: int = PC.SIMS
    artifact_paths: dict | None = None
    assets: object = None                     # asset backend (working set): ops_store.DirAssets, or the encrypted
                                              # private-release backend of cbb_platform/ops_persistence.py
    commit: object = None                     # (ops_root, message) -> dict: evidence-tree commit / push (ops_persistence)
    restore: bool = False
    persist: bool = False
    allow_revalidation: bool = False          # explicit architect action: retry quote validation after a FAIL
    extra: dict = field(default_factory=dict)

    def now(self) -> pd.Timestamp:
        return _utc(self.clock()) if self.clock else pd.Timestamp.now(tz="UTC")

    # production-module paths under prod_root (identical to config.* for the default root)
    @property
    def state_root(self) -> Path: return Path(self.prod_root) / "state"
    @property
    def cache_raw(self) -> Path: return Path(self.prod_root) / "cache" / "raw"
    @property
    def cbbd_ledger(self) -> Path: return Path(self.prod_root) / "cache" / "cbbd_production_requests.jsonl"
    @property
    def odds_ledger(self) -> Path: return Path(self.prod_root) / "cache" / "odds_production_requests.jsonl"
    @property
    def sync_log(self) -> Path: return Path(self.prod_root) / "sync" / "sync_log.jsonl"
    @property
    def collections(self) -> Path: return Path(self.prod_root) / "odds" / "collections"
    @property
    def odds_record(self) -> Path: return Path(self.prod_root) / "odds" / "integration_validation.json"
    @property
    def runs(self) -> Path: return Path(self.prod_root) / "runs"
    def ops(self, *p) -> Path: return Path(self.ops_root).joinpath(*p)


# ============================================================================ durable reads
def registry(ctx: Ctx) -> pd.DataFrame:
    rows = OS.read_jsonl(ctx.ops("registry", "games.jsonl"))
    cols = ["game_id", "status", "target_cutoff", "tick_id", "run_id", "recorded_at"]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)


def decided(ctx: Ctx) -> dict:
    """game_id -> its decided registry row (one decision per game and cutoff)."""
    r = registry(ctx)
    if not len(r):
        return {}
    r = r[r["status"].isin(DECIDED)]
    return {g.game_id: g._asdict() for g in r.drop_duplicates("game_id", keep="first").itertuples(index=False)}


def settled_games(ctx: Ctx) -> set:
    return {r["game_id"] for r in OS.read_jsonl(ctx.ops("registry", "settlements.jsonl"))}


def schedule(st: dict | None) -> pd.DataFrame:
    """The state's 2026-27 schedule with provider status, cutoff and knowable instant."""
    cols = ["canonical_game_id", "scheduled_tip_utc", "tip_time_quality", "game_date_et", "game_universe", "home_points",
            "has_team_box_score", "has_player_box_score", "home_team_id", "away_team_id", "status", "listed",
            "source_received_at", "provider_game_id", "substitutions_acquired"]
    if st is None:
        e = pd.DataFrame(columns=cols + ["cutoff", "knowable_at", "ingested", "not_played"])
        for c in ("scheduled_tip_utc", "cutoff", "knowable_at"):
            e[c] = pd.to_datetime(e[c], utc=True)
        for c in ("ingested", "not_played"):
            e[c] = e[c].astype(bool)
        return e
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    has = con.execute("""SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'metadata'
                         AND table_name = 'prod_schedule'""").fetchone()[0]
    sel = ("s.status, s.listed_in_latest_schedule AS listed, s.source_received_at, s.provider_game_id, "
           "s.substitutions_acquired") if has else \
        "NULL AS status, TRUE AS listed, NULL AS source_received_at, g.source_record_key AS provider_game_id, TRUE AS substitutions_acquired"
    join = "LEFT JOIN metadata.prod_schedule s USING (canonical_game_id)" if has else ""
    df = con.execute(f"""SELECT g.canonical_game_id, g.scheduled_tip_utc, g.tip_time_quality, g.game_date_et,
                                g.game_universe, g.home_points, g.has_team_box_score, g.has_player_box_score,
                                g.home_team_id, g.away_team_id, {sel}
                         FROM flagship.games g {join} WHERE g.season = {PC.PRODUCTION_SEASON}
                         ORDER BY g.scheduled_tip_utc, g.canonical_game_id""").df()
    con.close()
    df["scheduled_tip_utc"] = pd.to_datetime(df["scheduled_tip_utc"], utc=True)
    df["cutoff"] = [SL.feature_cutoff(t, q, d)[0] for t, q, d in zip(df["scheduled_tip_utc"], df["tip_time_quality"],
                                                                      df["game_date_et"])]
    df["knowable_at"] = [Y.knowable_at(t, q, d) for t, q, d in zip(df["scheduled_tip_utc"], df["tip_time_quality"],
                                                                   df["game_date_et"])]
    df["ingested"] = df["home_points"].notna() & df["has_team_box_score"].fillna(False).astype(bool) \
        & df["has_player_box_score"].fillna(False).astype(bool)
    st_ = df["status"].astype(str).str.strip().str.lower()
    df["not_played"] = st_.isin(NOT_PLAYED) | ~df["listed"].fillna(True).astype(bool)
    return df


def _window_of(tip: pd.Timestamp) -> str | None:
    d = _utc(tip).date()
    for a, b in Y.schedule_windows():
        if pd.Timestamp(a).date() <= d < pd.Timestamp(b).date():
            return a
    return None


# ============================================================================ plan (what is due now)
def plan(ctx: Ctx, st: dict | None, now: pd.Timestamp) -> dict:
    op = OS.read_json(ctx.ops("OPERATION.json"), {})
    started = _utc(op["operation_started_at"]) if op.get("operation_started_at") else now
    lead = pd.Timedelta(minutes=PC.SCORE_LEAD_MINUTES)
    sch = schedule(st)
    dec = decided(ctx)
    di = sch[(sch["game_universe"] == "DI_VS_DI") & ~sch["not_played"] & ~sch["canonical_game_id"].isin(dec)]
    open_ = di[di["home_points"].isna()]
    score = open_[(open_["cutoff"] - lead <= now) & (now < open_["cutoff"])]
    missed = di[(di["cutoff"] <= now) & (di["cutoff"] >= started)]
    nxt = []
    fut = open_[open_["cutoff"] - lead > now]
    if len(fut):
        nxt.append(("score", (fut["cutoff"] - lead).min()))
    # ------------------------------------------------------------ sync
    ptr = (st or {}).get("pointer") or {}
    last_ok = _utc(ptr["last_sync_source_cutoff"]) if ptr.get("last_sync_source_cutoff") else None
    log = OS.read_jsonl(ctx.sync_log)
    last_try = _utc(log[-1]["finished_at"]) if log else None
    retry_ok = last_try is None or now - last_try >= pd.Timedelta(minutes=PC.SYNC_RETRY_MINUTES)
    why, windows = [], set()
    if st is None or last_ok is None:
        why.append("NO_STATE")
    elif now - last_ok >= pd.Timedelta(hours=PC.SYNC_INTERVAL_HOURS):
        why.append("ROUTINE_REFRESH")
    halted = ctx.ops("markers", "COMPLETED_INGESTION_HALTED.json").exists()
    unres = sch[(sch["knowable_at"] <= now) & ~sch["ingested"] & ~sch["not_played"]]
    # every sync force-refreshes the schedule windows of games that became knowable within RESULT_LAG_RETRY_HOURS
    recent = unres[now - unres["knowable_at"] <= pd.Timedelta(hours=PC.RESULT_LAG_RETRY_HOURS)]
    final = recent["status"].astype(str).str.lower() == "final"
    windows = set() if halted else {w for w in map(_window_of, recent.loc[~final, "scheduled_tip_utc"]) if w}
    # a result-driven sync (beyond the routine refresh) only when an imminent scoring window needs that team's prior
    # result (else the forecast would carry STALE_STATE); settlement simply waits for the next routine refresh
    horizon = now + lead + pd.Timedelta(minutes=60)
    soon_cut = open_[(open_["cutoff"] > now) & (open_["cutoff"] <= horizon)]
    soon_teams = set(soon_cut["home_team_id"]) | set(soon_cut["away_team_id"])
    blocking = unres[(unres["home_team_id"].isin(soon_teams) | unres["away_team_id"].isin(soon_teams))
                     & (unres["knowable_at"] <= soon_cut["cutoff"].max() if len(soon_cut) else False)]
    if len(blocking) and not halted and retry_ok:
        why.append("RESULT_NEEDED_FOR_SCORING")
        windows |= {w for w in map(_window_of, blocking["scheduled_tip_utc"]) if w}
    if last_ok is not None:
        nxt.append(("sync", last_ok + pd.Timedelta(hours=PC.SYNC_INTERVAL_HOURS)))
    if len(blocking) and not halted and last_try is not None:
        nxt.append(("sync", last_try + pd.Timedelta(minutes=PC.SYNC_RETRY_MINUTES)))
    # ------------------------------------------------------------ events listing (free)
    lst = OF.listing(now, ctx.cache_raw)
    soon = sch[(sch["scheduled_tip_utc"] > now) & (sch["scheduled_tip_utc"] <= now + pd.Timedelta(hours=EVENTS_LOOKAHEAD_HOURS))
               & (sch["game_universe"] == "DI_VS_DI") & ~sch["not_played"]]
    events_due = bool(len(soon)) and (lst is None or now - lst["received_at"] >= pd.Timedelta(hours=PC.ODDS_EVENTS_MAX_AGE_HOURS))
    upcoming = sch[(sch["scheduled_tip_utc"] > now) & (sch["game_universe"] == "DI_VS_DI") & ~sch["not_played"]]
    if len(upcoming):
        nxt.append(("events", max(lst["received_at"] + pd.Timedelta(hours=PC.ODDS_EVENTS_MAX_AGE_HOURS) if lst else now,
                                  upcoming["scheduled_tip_utc"].min() - pd.Timedelta(hours=EVENTS_LOOKAHEAD_HOURS))))
    # ------------------------------------------------------------ settlement
    pred = [g for g, r in dec.items() if r["status"] == "PREDICTED"]
    done = settled_games(ctx)
    s = sch.set_index("canonical_game_id")
    settle = []
    for g in pred:
        if g in done or g not in s.index:
            continue
        r = s.loc[g]
        if r["ingested"]:
            settle.append((g, "FINAL"))
        elif r["not_played"] and now >= r["scheduled_tip_utc"] + pd.Timedelta(hours=PC.VOID_AFTER_HOURS):
            settle.append((g, "VOID_GAME_NOT_PLAYED"))
        elif r["not_played"]:
            nxt.append(("settle", r["scheduled_tip_utc"] + pd.Timedelta(hours=PC.VOID_AFTER_HOURS)))
    # ------------------------------------------------------------ reports
    rep_due = PM.daily_report_due(ctx.ops_root, now, started)
    nxt.append(("report", PM.next_daily_report_at(now)))
    return {"now": now, "score": score, "missed": missed, "sync_due": bool(why), "sync_why": why,
            "refresh_windows": sorted(windows), "events_due": events_due, "settle": settle, "report_due": rep_due,
            "next": sorted((k, _utc(t)) for k, t in nxt if pd.notna(t)), "operation_started_at": started,
            "schedule": sch}


def due_document(p: dict, st: dict | None, ctx: Ctx) -> dict:
    """DUE.json: what the cheap scheduler check reads. No wall-clock field, so an idle tick leaves it unchanged."""
    immediate = bool(len(p["score"]) or len(p["missed"]) or p["sync_due"] or p["events_due"] or p["settle"]
                     or p["report_due"])
    nxt = {}
    for k, t in p["next"]:
        if k not in nxt or t < nxt[k]:
            nxt[k] = t
    head = (OS.read_jsonl(ctx.ops("CHAIN.jsonl")) or [{"hash": OS.GENESIS}])[-1]["hash"]
    return {"version": TICK_VERSION, "due_now": immediate,
            "next_due_at": (p["now"].isoformat() if immediate else (min(nxt.values()).isoformat() if nxt else None)),
            "next_by_kind": {k: v.isoformat() for k, v in sorted(nxt.items())},
            "heartbeat_hours": PC.SYNC_INTERVAL_HOURS, "state_version": ((st or {}).get("manifest") or {}).get("state_version"),
            "chain_head": head, "score_lead_minutes": PC.SCORE_LEAD_MINUTES}


# ============================================================================ steps
def _session(ctx: Ctx, stage: str) -> OA.Session:
    return OA.Session(stage, cache_root=ctx.cache_raw, ledger_path=ctx.odds_ledger, http_get=ctx.odds_http,
                      credential=ctx.odds_credential, clock=(lambda: ctx.now().to_pydatetime()))


def step_sync(ctx: Ctx, p: dict, tick: dict) -> dict:
    halted = ctx.ops("markers", "COMPLETED_INGESTION_HALTED.json").exists()
    stages = ("preseason", "schedule") if halted else ("preseason", "schedule", "completed")
    before = ST.current(ctx.state_root)
    rec = Y.run(stages=stages, history_db=ctx.history_db, state_root=ctx.state_root, cache_root=ctx.cache_raw,
                ledger_path=ctx.cbbd_ledger, sync_log=ctx.sync_log, work_root=Path(ctx.prod_root) / "sync" / "work",
                http_get=ctx.cbbd_http, credential=ctx.cbbd_credential, clock=(lambda: ctx.now().to_pydatetime()),
                refresh_schedule_windows=p["refresh_windows"])
    tick["sync"] = {k: rec.get(k) for k in ("sync_id", "status", "reason", "error", "state_version", "requests",
                                            "completed_games_new", "completed_games_ingested", "completed_budget_exhausted",
                                            "completed_requests_deferred", "results_deferred_not_yet_knowable", "coverage")}
    tick["sync"]["why"] = p["sync_why"]
    tick["sync"]["refresh_windows"] = p["refresh_windows"]
    after = ST.current(ctx.state_root)
    if after is not None:
        v = after["manifest"]["state_version"]
        OS.write_once(ctx.ops("state_manifests", f"{v}.json"), after["manifest"])
    if rec.get("status") == "PROMOTED" and (rec.get("completed_games_ingested") or 0) > 0:
        first_ingestion_check(ctx, before, after, rec, tick)
    return rec


def first_ingestion_check(ctx: Ctx, before: dict | None, after: dict, rec: dict, tick: dict) -> None:
    """FIRST_LIVE_COMPLETED_GAME_INGESTION: the first real 2026-27 final game through the exact production path."""
    marker = ctx.ops("markers", "FIRST_LIVE_COMPLETED_GAME_INGESTION.json")
    if marker.exists() or str(after["manifest"].get("provider", {}).get("kind", "")).upper() != "LIVE":
        return
    prev_done = set() if before is None else set(schedule(before).query("ingested")["canonical_game_id"])
    sch = schedule(after)
    new = sch[sch["ingested"] & ~sch["canonical_game_id"].isin(prev_done)]
    con = duckdb.connect(str(after["increment_db"]), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    g = con.register("g", pd.DataFrame({"canonical_game_id": new["canonical_game_id"]}))
    tb = con.execute("""SELECT canonical_game_id, count(*) n, count(DISTINCT canonical_team_id) t,
                               max(historical_knowable_at) k FROM flagship.team_game_outcomes
                        WHERE canonical_game_id IN (SELECT * FROM g) GROUP BY 1""").df().set_index("canonical_game_id")
    pb = con.execute("""SELECT canonical_game_id, count(*) n, count(DISTINCT canonical_team_id) t,
                               max(historical_knowable_at) k FROM flagship.player_game_outcomes
                        WHERE canonical_game_id IN (SELECT * FROM g) GROUP BY 1""").df().set_index("canonical_game_id")
    sb = con.execute("""SELECT canonical_game_id, count(*) n FROM flagship.substitutions
                        WHERE canonical_game_id IN (SELECT * FROM g) GROUP BY 1""").df().set_index("canonical_game_id")
    con.close()
    led = [e for e in OS.read_jsonl(ctx.cbbd_ledger) if str(e.get("stage", "")).startswith(rec["sync_id"] + ":completed")]
    cut = _utc(after["manifest"]["source_cutoff"])
    checks = {}
    for r in new.itertuples(index=False):
        gid = r.canonical_game_id
        sub_ok = [e for e in led if e.get("endpoint") == "substitutions/game"
                  and str(e.get("params", {}).get("gameId")) == str(r.provider_game_id)
                  and e.get("status") in ("FETCHED", "FETCHED_UNCHANGED", "CACHE_HIT")]
        c = {"game_identity": bool(pd.notna(r.provider_game_id) and r.home_team_id != r.away_team_id
                                   and str(r.home_team_id).startswith("T-") and str(r.away_team_id).startswith("T-")),
             "final_status": str(r.status).lower() == "final" and pd.notna(r.home_points),
             "player_box_scores": gid in pb.index and int(pb.loc[gid, "t"]) == 2 and int(pb.loc[gid, "n"]) > 0,
             "team_box_scores": gid in tb.index and int(tb.loc[gid, "n"]) == 2,
             "substitution_data": bool(r.substitutions_acquired) and bool(sub_ok),
             "cache": all((ctx.cache_raw / e["cache_path"]).exists() for e in sub_ok if e.get("cache_path")) and bool(sub_ok),
             "request_ledger": bool(led) and all(e.get("status") != "FAILED" for e in led),
             "atomic_state_update": after["manifest"].get("parent_state_version") == (before or {}).get("manifest", {}).get("state_version")
                                    and ST.current(ctx.state_root)["manifest"]["state_version"] == after["manifest"]["state_version"],
             "future_game_chronology": bool(gid in pb.index and _utc(pb.loc[gid, "k"]) <= cut
                                            and gid in tb.index and _utc(tb.loc[gid, "k"]) <= cut
                                            and r.knowable_at <= cut)}
        checks[gid] = {**c, "substitution_rows": int(sb.loc[gid, "n"]) if gid in sb.index else 0}
    ok = bool(checks) and all(all(v for k, v in c.items() if k != "substitution_rows") for c in checks.values())
    body = {"status": "PASS" if ok else "FAIL", "recorded_at": ctx.now().isoformat(), "sync_id": rec["sync_id"],
            "state_version": after["manifest"]["state_version"], "parent_state_version": after["manifest"].get("parent_state_version"),
            "games": checks}
    OS.write_once(marker, body)
    tick["first_live_completed_game_ingestion"] = body["status"]
    if not ok and before is not None:                  # keep the previous good state; future-game state does not advance
        ST._write_pointer(ctx.state_root, {**before["pointer"], "rolled_back_from": after["manifest"]["state_version"],
                                           "rolled_back_at": ctx.now().isoformat()})
        OS.write_once(ctx.ops("markers", "COMPLETED_INGESTION_HALTED.json"),
                      {"reason": "FIRST_LIVE_COMPLETED_GAME_INGESTION FAIL", "at": ctx.now().isoformat(),
                       "restored_state_version": before["manifest"]["state_version"],
                       "action": "completed-game ingestion halted until architect review"})


def step_events(ctx: Ctx, tick: dict, max_age_seconds: float, hours: float = PC.ODDS_PROP_WINDOW_HOURS) -> OA.Session | None:
    """Free events listing (0 credits). Returns the session (its quota is known after a live listing)."""
    now = ctx.now()
    s = _session(ctx, f"{tick['tick_id']}:odds")
    try:
        e = s.get("events", OF.events_params(now, now + pd.Timedelta(hours=hours)),
                  max_age_seconds=max_age_seconds, purpose="events listing (0 credits)")
        tick["events"] = {"status": e["status"], "listed": e.get("record_count"), "received_at": e.get("received_at"),
                          "quota_remaining": e.get("quota_remaining")}
    except (OA.OddsProviderFailure, OA.OddsGuardViolation, OA.OddsBudgetExceeded) as ex:
        tick["events"] = {"status": "FAILED", "error": str(ex)[:300]}
        return None
    s.event_commence = OF.event_commence_resolver(ctx.cache_raw, now)
    return s


def team_names(ctx: Ctx, st: dict) -> pd.DataFrame:
    """Canonical team names: frozen history + teams new in the 2026-27 increment (strict matching input)."""
    teams, _ = RN._names(ctx.history_db)
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    has = con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'core' AND table_name = 'teams'").fetchone()[0]
    inc = pd.DataFrame()
    if has:
        cols = {r[0] for r in con.execute("DESCRIBE core.teams").fetchall()}
        sel = ", ".join(f"any_value({c}) AS {c}" if c in cols else f"NULL AS {c}" for c in ("school", "display_name", "mascot"))
        inc = con.execute(f"SELECT canonical_team_id, {sel} FROM core.teams GROUP BY 1").df()
    con.close()
    if not len(inc):
        return teams
    out = pd.concat([teams, inc[~inc["canonical_team_id"].isin(teams["canonical_team_id"])]], ignore_index=True)
    return out.astype(object).where(out.notna(), None)


REVALIDATION_LOG = "quote_revalidation_authorizations.jsonl"


def _validation_state(ctx: Ctx, now: pd.Timestamp) -> dict:
    """A FAIL blocks quotes until an architect re-validation authorization recorded AFTER it (authorize_revalidation):
    that lifts only the FAILs before it, so a FAIL after the authorization blocks again (one attempt per authorization)."""
    att = OS.read_jsonl(ctx.ops("markers", "quote_validation_attempts.jsonl"))
    auth = OS.read_jsonl(ctx.ops("markers", REVALIDATION_LOG))
    since = max((_utc(a["authorized_at"]) for a in auth), default=None)
    passed = ctx.ops("markers", "QUOTE_INTEGRATION_VALIDATED.json").exists()
    fails = [a for a in att if a["status"] == "FAIL"]
    failed = any(since is None or _utc(a["attempted_at"]) > since for a in fails)
    day = now.tz_convert("America/New_York").date()
    inconcl = sum(1 for a in att if a["status"] != "PASS" and a["status"] != "FAIL"
                  and _utc(a["attempted_at"]).tz_convert("America/New_York").date() == day)
    return {"passed": passed, "failed": failed, "inconclusive_today": inconcl, "attempts": len(att),
            "fails_recorded": len(fails), "revalidation_authorized_at": since.isoformat() if since is not None else None}


def authorize_revalidation(ctx: Ctx, reason: str, fix_commit: str) -> dict:
    """Architect action: allow ONE more paid validation after a recorded FAIL (the scheduled tick then validates on its
    own first due game, under the same checks). Refused unless quotes are currently blocked by a FAIL. Append-only;
    the FAIL attempts themselves are never edited or removed."""
    now = ctx.now()
    v = _validation_state(ctx, now)
    if v["passed"] or not v["failed"]:
        raise ValueError(f"re-validation not needed: passed={v['passed']} failed={v['failed']}")
    if not reason.strip() or not fix_commit.strip():
        raise ValueError("re-validation needs a reason and the commit that fixes the recorded FAIL")
    att = [a for a in OS.read_jsonl(ctx.ops("markers", "quote_validation_attempts.jsonl")) if a["status"] == "FAIL"]
    row = {"authorized_at": now.isoformat(), "reason": reason, "fix_commit": fix_commit,
           "release_commit": os.environ.get("CBB_EXPECTED_COMMIT"),
           "lifts_fail_attempts": [a["attempted_at"] for a in att]}
    OS.append_jsonl(ctx.ops("markers", REVALIDATION_LOG), [row])
    return row


def validation_eligible(vstate: dict, ctx: Ctx) -> bool:
    """One paid validation request is allowed only when: no PASS exists (never repeated after PASS); no FAIL is
    recorded (a FAIL stays BLOCKED until an explicit architect re-validation); and fewer than
    MAX_INCONCLUSIVE_VALIDATIONS_PER_DAY inconclusive / error attempts today (props not yet posted)."""
    return (not vstate["passed"] and (not vstate["failed"] or ctx.allow_revalidation)
            and vstate["inconclusive_today"] < MAX_INCONCLUSIVE_VALIDATIONS_PER_DAY)


def step_validate(ctx: Ctx, session: OA.Session, event_id: str, gid: str, tick: dict) -> str:
    """The existing one-event validation (odds_feed.validate). PASS -> immutable QUOTE_INTEGRATION_VALIDATED. A payload
    with no player-prop market at all is INCONCLUSIVE (nothing posted yet), not an integration failure."""
    now = ctx.now()
    a = {"attempted_at": now.isoformat(), "event_id": event_id, "canonical_game_id": gid}
    try:
        out = OF.validate(event_id, now=now, session=session, run_root=Path(ctx.prod_root) / "odds" / "validation_runs",
                          record=ctx.odds_record, state_root=ctx.state_root, history_db=ctx.history_db,
                          collections_dir=ctx.collections, sims=ctx.sims, sync_log=ctx.sync_log)
    except Exception as e:                                            # noqa: BLE001 -- recorded; never retried blindly
        a.update({"status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:300]}"})
        OS.append_jsonl(ctx.ops("markers", "quote_validation_attempts.jsonl"), [a])
        tick["quote_validation"] = a
        return "ERROR"
    per = out["checks"]["expected_markets_returned"]["per_market"]
    status = out["status"]
    if status == "FAIL" and "RETURNED" not in per.values():
        status = "INCONCLUSIVE_NO_PLAYER_PROPS_POSTED"
    credits = sum(int(e.get("cost") or 0) for e in out.get("requests", []) if e.get("endpoint") == "event_odds")
    a.update({"status": status, "credits": credits, "checks": {k: v["pass"] for k, v in out["checks"].items()},
              "requests": [{k: e.get(k) for k in ("endpoint", "status", "cost", "markets_returned", "response_sha256",
                                                  "received_at")} for e in out.get("requests", [])]})
    OS.append_jsonl(ctx.ops("markers", "quote_validation_attempts.jsonl"), [a])
    if status == "PASS":
        OS.write_once(ctx.ops("markers", "QUOTE_INTEGRATION_VALIDATED.json"),
                      {"QUOTE_INTEGRATION_VALIDATED": True, **{k: out[k] for k in ("validated_at", "event_id",
                       "canonical_game_id", "feed_version", "checks")}, "credits": credits})
    tick["quote_validation"] = {k: a[k] for k in ("status", "event_id", "credits")}
    return status


def step_quotes(ctx: Ctx, session: OA.Session, due_events: list[tuple], tick: dict) -> list[Path]:
    """ONE quote snapshot per due event at the decision time (documented NCAAB base keys; the existing per-stage credit
    and request budgets; a validation fetch of the same event within 10 min is reused, not re-bought)."""
    paths, snaps = [], []
    for i, (gid, eid) in enumerate(due_events):
        try:
            got = OF.collect(session, [eid], ctx.collections)
        except OA.OddsBudgetExceeded as e:
            # every remaining due event is recorded (no silent gap in the quote evidence); none is bought
            snaps += [{"event_id": e2, "game_id": g2, "status": "BUDGET_EXHAUSTED", "error": str(e)[:200]}
                      for g2, e2 in due_events[i:]]
            break
        except (OA.OddsProviderFailure, OA.OddsGuardViolation) as e:
            snaps.append({"event_id": eid, "game_id": gid, "status": "FAILED", "error": str(e)[:200]})
            continue
        ent = [x for x in session.entries if x.get("endpoint") == "event_odds" and x.get("params", {}).get("eventId") == eid][-1]
        paths += got
        body = (json.loads(got[-1].read_text()).get("events") or [{}])[0]
        books = body.get("bookmakers") or []
        snaps.append({"event_id": eid, "game_id": gid, "status": ent["status"], "received_at": ent.get("received_at"),
                      "response_sha256": ent.get("response_sha256"), "credits": int(ent.get("cost") or 0),
                      "markets_returned": len({m.get("key") for b in books for m in b.get("markets") or []}),
                      "bookmakers": len(books), "collection": paths[-1].name})
    for s in snaps:
        s["tick_id"] = tick["tick_id"]
    OS.append_jsonl(ctx.ops("registry", "quote_snapshots.jsonl"), snaps)
    tick["quote_snapshots"] = {"events": len(due_events), "collected": len(paths),
                               "credits": sum(s.get("credits", 0) for s in snaps),
                               "statuses": pd.Series([s["status"] for s in snaps]).value_counts().to_dict() if snaps else {}}
    return paths


def _evidence_bucket(cur_min) -> str:
    x = 0.0 if cur_min is None or pd.isna(cur_min) else float(cur_min)
    for lo, hi, name in PC.EVIDENCE_BUCKETS:
        if (lo is None and x <= hi) or (lo is not None and x > lo and (hi is None or x <= hi)):
            return name
    return "CUR_UNKNOWN"


def scoring_evidence(ctx: Ctx, tdir: Path, s: dict, timing: dict, sch: pd.DataFrame) -> dict:
    """Compact, analysis-ready evidence of one scoring run (the hash-chained ledgers are kept verbatim, gzipped)."""
    run_dir = Path(s["run_dir"])
    led = tdir / "shadow_ledger.jsonl"
    recs = [json.loads(l) for l in led.read_text().splitlines() if l.strip()] if led.exists() else []
    preds = pd.DataFrame([r["body"] for r in recs if r["record_type"] == "PREDICTION"])
    inputs = pd.read_parquet(run_dir / "model_inputs.parquet") if (run_dir / "model_inputs.parquet").exists() else pd.DataFrame()
    fdb = run_dir / "features" / "features_asof.duckdb"
    feats = pd.DataFrame(columns=["game_id", "player_id", "cur_min", "team_games"])
    if fdb.exists():
        con = duckdb.connect(str(fdb), read_only=True)
        feats = con.execute("""SELECT canonical_game_id AS game_id, canonical_player_id AS player_id,
                                      pstd_sum_min::DOUBLE AS cur_min, pp_team_games::DOUBLE AS team_games
                               FROM feat.player_features""").df()
        con.close()
    di = sch[sch["game_universe"] == "DI_VS_DI"]
    first = pd.Timestamp(str(pd.to_datetime(di["game_date_et"]).min().date())) if len(di) else None
    gdate = sch.set_index("canonical_game_id")["game_date_et"]
    reasons = s.get("game_reasons", {})
    # timing fields (the records already carry prediction_as_of, identical to the run's as-of)
    tim = pd.DataFrame([{"game_id": g, **{k: v for k, v in t.items() if k != "prediction_as_of"}} for g, t in timing.items()])
    out = {}
    # the complete PMFs this prediction used, persisted with the tick (the run directory is ephemeral on a runner)
    pe = PE.from_run(run_dir, s["run_id"], s["prediction_as_of"], (s.get("model_identity") or {}).get("model_version"))
    if len(pe):
        w = PE.write(tdir / PE.FILE, pe)
        out["pmfs"] = w.get("pmfs", int(pe["prediction_receipt_id"].nunique()))
        out["pmfs_sha256"] = w["sha256"]
    if len(preds):
        base = preds.drop_duplicates(["game_id", "player_id", "market"])
        proj = base.set_index(["game_id", "player_id", "team_id", "market"])["projection"].astype(float).unstack(
            "market").add_prefix("proj_").reset_index()               # suppressed players kept (NaN projections)
        proj.columns.name = None
        meta = base.drop_duplicates(["game_id", "player_id"])[["game_id", "player_id", "player", "game", "p_play",
                                                               "distribution_method", "suppression_reason", "readiness_state",
                                                               "bank_sha256", "run_id", "prediction_as_of", "feature_cutoff"]]
        proj = proj.merge(meta, on=["game_id", "player_id"], how="left")
        if len(inputs):
            keep = ["game_id", "player_id", "min_mu", "min_sigma", "role_diagnostic", "position_group", "cold_start_class",
                    "player_suppression"]
            proj = proj.merge(inputs[keep].drop_duplicates(["game_id", "player_id"]), on=["game_id", "player_id"], how="left")
        proj = proj.merge(feats.drop_duplicates(["game_id", "player_id"]), on=["game_id", "player_id"], how="left")
        proj = proj.merge(tim, on="game_id", how="left")
        proj["game_date"] = proj["game_id"].map(lambda g: str(pd.Timestamp(gdate[g]).date()) if g in gdate.index else None)
        proj["days_since_season_start"] = proj["game_date"].map(
            lambda d: None if d is None or first is None else int((pd.Timestamp(d) - first).days))
        proj["first_30_days"] = proj["days_since_season_start"].map(lambda x: x is not None and 0 <= x < PC.FIRST_DAYS_WINDOW)
        proj["opening_season"] = proj["team_games"].fillna(0) <= 2
        proj["evidence_bucket"] = proj["cur_min"].map(_evidence_bucket)
        proj["high_exposure"] = proj["min_mu"].astype(float) >= 30.0
        proj["transfer_in"] = proj["cold_start_class"] == "COLD_TRANSFER_IN"
        proj["cold_start_new"] = proj["cold_start_class"] == "COLD_NO_HISTORY"
        proj["center"] = proj["position_group"] == "C"
        proj["game_reasons"] = proj["game_id"].map(lambda g: ";".join(sorted(set(reasons.get(g, [])))))
        proj["clean"] = proj["game_reasons"].map(lambda r: not (set(filter(None, r.split(";"))) & PM.DATA_INTEGRITY_REASONS))
        proj["priced"] = proj["distribution_method"] != "NOT_PRICED"
        proj.to_parquet(tdir / "projections.parquet", index=False)
        out["projections"] = int(len(proj))
        q = preds[preds["line"].notna() & preds["side"].notna()].copy()
        if len(q):
            q = q.merge(tim, on="game_id", how="left")
            q.drop(columns=[c for c in ("artifact_sha256",) if c in q]).to_parquet(tdir / "predictions_quoted.parquet", index=False)
        out["predictions_quoted"] = int(len(q))
    sm = tdir / "side_monitor.jsonl"
    sides = [json.loads(l)["body"] for l in sm.read_text().splitlines() if l.strip()] if sm.exists() else []
    qn = run_dir / "quotes_normalized.csv"
    if qn.exists():
        OS.gzip_file(qn, tdir / "quotes_normalized.csv.gz")
    if sides:
        d = pd.DataFrame(sides)
        if "projections" in out:
            ex = pd.read_parquet(tdir / "projections.parquet")[["game_id", "player_id", "position_group", "cur_min",
                                                                "evidence_bucket", "cold_start_class", "first_30_days",
                                                                "center", "cold_start_new", "clean", "game_reasons"]]
            d = d.merge(ex.drop(columns=["cold_start_class"]), on=["game_id", "player_id"], how="left")
        if qn.exists():
            qq = pd.read_csv(qn)
            qq = qq[qq["canonical_player_id"].notna()][["canonical_game_id", "canonical_player_id", "stat", "line", "side",
                                                         "sportsbook", "provider_event_id", "market_key", "market_last_update",
                                                         "bookmaker_last_update", "deep_link", "american_odds"]]
            qq = qq.rename(columns={"canonical_game_id": "game_id", "canonical_player_id": "player_id", "stat": "market"})
            d = d.merge(qq.drop_duplicates(["game_id", "player_id", "market", "line", "side", "sportsbook"]),
                        on=["game_id", "player_id", "market", "line", "side", "sportsbook"], how="left")
        snaps = {r["event_id"]: r.get("response_sha256") for r in OS.read_jsonl(ctx.ops("registry", "quote_snapshots.jsonl"))
                 if r.get("tick_id") == tdir.name}
        if "provider_event_id" in d:
            d["response_sha256"] = d["provider_event_id"].map(snaps)
        d = d.merge(tim, on="game_id", how="left")
        d.to_parquet(tdir / "quote_sides.parquet", index=False)
        out["quote_sides"] = int(len(d))
    for f in ("shadow_ledger.jsonl", "side_monitor.jsonl"):
        if (tdir / f).exists():
            OS.gzip_file(tdir / f, tdir / f"{f}.gz")
            (tdir / f).unlink()
    return out


def step_score(ctx: Ctx, p: dict, st: dict, tick: dict, tdir: Path) -> None:
    due = p["score"].sort_values(["cutoff", "canonical_game_id"])
    vstate = _validation_state(ctx, ctx.now())
    session = step_events(ctx, tick, max_age_seconds=0)          # live free listing: fresh events + known quota
    paths = []
    if session is not None:
        now = ctx.now()
        sl = SL.build(st["increment_db"], now, st["manifest"].get("schedule_as_of") or st["manifest"]["source_cutoff"],
                      PC.ODDS_PROP_WINDOW_HOURS)
        teams = team_names(ctx, st)
        lst = OF.listing(now, ctx.cache_raw)
        mev = OF.match_events(lst["events"] if lst else None, sl, MT.team_index(teams, MT.odds_team_map()))
        mev = mev[mev["canonical_game_id"].isin(set(due["canonical_game_id"])) & mev["canonical_game_id"].map(lambda x: isinstance(x, str))]
        order = due["canonical_game_id"].tolist()
        due_events = sorted(((g, e) for g, e in zip(mev["canonical_game_id"], mev["provider_event_id"])),
                            key=lambda x: (order.index(x[0]), x[1]))
        tick["events_matched_to_due_games"] = len(due_events)
        if due_events and validation_eligible(vstate, ctx):
            step_validate(ctx, session, due_events[0][1], due_events[0][0], tick)
            vstate = _validation_state(ctx, ctx.now())
        if vstate["passed"] and due_events:
            paths = step_quotes(ctx, session, due_events, tick)
        elif due_events:
            tick["quote_snapshots"] = {"skipped": "QUOTE_INTEGRATION_NOT_VALIDATED" if not vstate["failed"]
                                       else "QUOTE_INTEGRATION_VALIDATION_FAILED (architect review)"}
    aso = ctx.now()                                              # every quote used was received at or before aso
    exec_at = pd.Timestamp.now(tz="UTC")
    late = due[due["cutoff"] <= aso]
    for r in late.itertuples(index=False):
        _register(ctx, r.canonical_game_id, "MISSED_PREDICTION_CUTOFF", r, tick, extra={"reason": "cutoff passed during quote collection"})
    due = due[due["cutoff"] > aso]
    if not len(due):
        return
    timing = {r.canonical_game_id: {"scheduled_tip": _iso(r.scheduled_tip_utc), "target_cutoff": _iso(r.cutoff),
                                    "cutoff_rule": PC.VALIDATED_PREDICTION_CUTOFF if r.tip_time_quality == "EXACT" else "ET_GAME_DATE_START",
                                    "prediction_as_of": aso.isoformat(), "executed_at": exec_at.isoformat(),
                                    "timing_deviation_seconds": float((r.cutoff - aso).total_seconds()),
                                    "timing_note": "prediction made BEFORE the cutoff by timing_deviation_seconds"}
              for r in due.itertuples(index=False)}
    s = RN.daily(aso, quotes_paths=paths, state_root=ctx.state_root, history_db=ctx.history_db,
                 ledger_path=tdir / "shadow_ledger.jsonl", run_root=ctx.runs, horizon_hours=PC.ODDS_PROP_WINDOW_HOURS,
                 sims=ctx.sims, artifact_paths=ctx.artifact_paths, game_ids=list(timing), sync_log=ctx.sync_log,
                 odds_cache_root=ctx.cache_raw, odds_collections=None, odds_ledger=ctx.odds_ledger,
                 odds_record=ctx.odds_record, side_monitor_path=tdir / "side_monitor.jsonl")
    banks = s.get("banks", {})
    ev = scoring_evidence(ctx, tdir, s, timing, p["schedule"]) if s.get("predictions") else {}
    led = pd.DataFrame()
    if (tdir / "projections.parquet").exists():
        led = pd.read_parquet(tdir / "projections.parquet")
    one_bank = all(led.loc[led["game_id"] == g, "bank_sha256"].nunique() == 1 for g in banks) if len(led) else True
    tick["scoring"] = {"run_id": s.get("run_id"), "readiness_state": s.get("readiness_state"),
                       "run_reasons": s.get("run_reasons"), "games_due": len(timing), "games_scored": len(banks),
                       "banks": banks, "one_joint_bank_per_game": bool(one_bank and len(set(b["bank_sha256"] for b in banks.values())) == len(banks)),
                       "predictions": s.get("predictions"), "quotes": s.get("quotes"), "quote_coverage": s.get("quote_coverage"),
                       "player_universe": s.get("player_universe"), "evidence": ev, "game_reasons": s.get("game_reasons"),
                       "side_monitor": s.get("side_monitor")}
    sl = {r.canonical_game_id: r for r in due.itertuples(index=False)}
    for gid in timing:
        r = sl[gid]
        why = s.get("game_reasons", {}).get(gid, [])
        if gid in banks:
            _register(ctx, gid, "PREDICTED", r, tick, extra={**timing[gid], "run_id": s["run_id"],
                                                             "bank_sha256": banks[gid]["bank_sha256"], "game_reasons": why,
                                                             "readiness_state": s.get("readiness_state")})
        elif {"NON_DI_GAME", "UNMATCHED_GAME", "AMBIGUOUS_GAME", "WRONG_PRODUCTION_SEASON", "MISSING_SCHEDULE",
              "UNKNOWN_TEAM", "MISSING_ROSTER"} & set(why):
            _register(ctx, gid, "EXCLUDED", r, tick, extra={"game_reasons": why, "run_id": s.get("run_id")})
        else:                                                    # retried by a later tick before the cutoff
            _register(ctx, gid, "NO_BANK", r, tick, extra={"game_reasons": why, "run_id": s.get("run_id")})


def _register(ctx: Ctx, gid: str, status: str, r, tick: dict, extra: dict | None = None) -> None:
    row = {"game_id": gid, "status": status, "scheduled_tip": _iso(r.scheduled_tip_utc), "target_cutoff": _iso(r.cutoff),
           "tick_id": tick["tick_id"], "recorded_at": pd.Timestamp.now(tz="UTC").isoformat(), **(extra or {})}
    OS.append_jsonl(ctx.ops("registry", "games.jsonl"), [row])
    tick.setdefault("registry", []).append({k: row.get(k) for k in ("game_id", "status", "timing_deviation_seconds")})


def step_missed(ctx: Ctx, p: dict, tick: dict) -> None:
    """Cutoff passed with no decision: MISSED_PREDICTION_CUTOFF (a game that had only NO_BANK attempts is
    NOT_SCORED_AT_CUTOFF). No forecast is ever made for it afterwards."""
    tried = set(registry(ctx).query("status == 'NO_BANK'")["game_id"]) if len(registry(ctx)) else set()
    for r in p["missed"].itertuples(index=False):
        _register(ctx, r.canonical_game_id, "NOT_SCORED_AT_CUTOFF" if r.canonical_game_id in tried else "MISSED_PREDICTION_CUTOFF",
                  r, tick, extra={"detected_at": p["now"].isoformat()})


def _actuals(st: dict, gid: str) -> pd.DataFrame:
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    cols = ", ".join(f"{c}::DOUBLE AS {s}" for s, c in STAT_COLS.items())
    df = con.execute(f"""SELECT canonical_player_id AS player_id, canonical_team_id AS team_id, minutes::DOUBLE AS minutes,
                                {cols}, historical_knowable_at FROM flagship.player_game_outcomes
                         WHERE canonical_game_id = ? AND canonical_player_id IS NOT NULL""", [gid]).df()
    con.close()
    return df.drop_duplicates("player_id")


def _stat(row, market: str):
    if row is None:
        return None
    keys = PC.COMBOS.get(market, (market,))
    v = [row.get(k) for k in keys]
    return None if any(x is None or pd.isna(x) for x in v) else float(sum(v))


def step_settle(ctx: Ctx, p: dict, st: dict, tick: dict, tdir: Path) -> None:
    """Append settlements for predicted games that became final (or were not played). The prediction records are never
    touched: settlements are new hash-chained records referencing prediction_id / record_id."""
    reg = {g: r for g, r in decided(ctx).items() if r["status"] == "PREDICTED"}
    now = ctx.now()
    chain, prev = [], LG.GENESIS
    side_rows, act_rows, summ = [], [], []
    for gid, kind in p["settle"]:
        src = ctx.ops("ticks", reg[gid]["tick_id"])
        act = _actuals(st, gid) if kind == "FINAL" else pd.DataFrame(columns=["player_id"])
        a = act.set_index("player_id") if len(act) else pd.DataFrame()
        proj = pd.read_parquet(src / "projections.parquet") if (src / "projections.parquet").exists() else pd.DataFrame()
        proj = proj[proj["game_id"] == gid] if len(proj) else proj

        def part(pid):
            if kind != "FINAL" or pid not in a.index:
                return "DNP" if kind == "FINAL" else "GAME_NOT_PLAYED", None
            row = a.loc[pid]
            return ("PLAYED", row) if (row.get("minutes") or 0) > 0 else ("DNP", None)
        for r in proj.itertuples(index=False):
            pa, row = part(r.player_id)
            act_rows.append({"game_id": gid, "player_id": r.player_id, "participation": pa, "settled_at": now.isoformat(),
                             "minutes": None if row is None else float(row["minutes"]),
                             **{f"act_{m}": _stat(row, m) for m in PC.MARKETS}})
        pq = pd.read_parquet(src / "predictions_quoted.parquet") if (src / "predictions_quoted.parquet").exists() else pd.DataFrame()
        pq = pq[pq["game_id"] == gid] if len(pq) else pq
        n_pred = 0
        for r in pq.itertuples(index=False):
            pa, row = part(r.player_id)
            y = _stat(row, r.market) if pa == "PLAYED" else None
            if y is None:
                outcome, res, win = "NO_ACTION", "NO_ACTION", None
            else:
                outcome = "PUSH" if y == r.line else ("OVER" if y > r.line else "UNDER")
                res = "PUSH" if outcome == "PUSH" else ("WIN" if outcome == r.side else "LOSS")
                win = None if res == "PUSH" else float(res == "WIN")
            pr_, pnp = _f(r.probability), _f(r.p_nonpush)
            pn = pr_ / pnp if pr_ is not None and pnp else None
            body = {"prediction_id": r.prediction_id, "game_id": gid, "player_id": r.player_id, "market": r.market,
                    "line": float(r.line), "side": r.side, "settled_at": now.isoformat(),
                    "result_source": "CBBD_FINAL_BOX (production 2026-27 state)" if kind == "FINAL" else kind,
                    "state_version": st["manifest"]["state_version"], "participation": pa, "actual": y,
                    "outcome": outcome, "side_result": res,
                    "predicted_probability": pr_, "predicted_probability_nonpush": pn, "fair_decimal": _f(r.fair_decimal),
                    "quoted_decimal": _f(r.best_decimal_odds), "quoted_american": _f(r.best_american_odds),
                    "sportsbook": r.sportsbook if isinstance(r.sportsbook, str) else None,
                    "brier": None if win is None or pn is None else float((pn - win) ** 2),
                    "log_loss": None if win is None or pn is None else float(-np.log(np.clip(pn if win else 1 - pn, 1e-12, 1)))}
            rec = {"record_type": "SETTLEMENT", "body": body, "prev_row_hash": prev}
            rec["row_hash"] = hashlib.sha256(LG._canon(rec).encode()).hexdigest()
            prev = rec["row_hash"]
            chain.append(rec)
            n_pred += 1
        qs = pd.read_parquet(src / "quote_sides.parquet") if (src / "quote_sides.parquet").exists() else pd.DataFrame()
        qs = qs[qs["game_id"] == gid] if len(qs) else qs
        from cbb_platform.prod import quote_refresh as QR         # lazy: quote_refresh imports this module
        qr = QR.receipts_for_game(ctx.ops_root, gid)             # tiered-refresh repricings of this game's PMFs
        n_refresh = int(len(qr))
        if n_refresh:
            qs = pd.concat([x for x in (qs, qr) if len(x)], ignore_index=True)
        for r in qs.itertuples(index=False):
            pa, row = part(r.player_id)
            y = _stat(row, r.market) if pa == "PLAYED" else None
            if y is None:
                res, win = "NO_ACTION", None
            else:
                res = "PUSH" if y == r.line else ("WIN" if (y > r.line) == (r.side == "OVER") else "LOSS")
                win = None if res == "PUSH" else float(res == "WIN")
            pn = _f(r.p_side_nonpush)
            side_rows.append({"record_id": r.record_id, "game_id": gid, "settled_at": now.isoformat(),
                              "result_source": "CBBD_FINAL_BOX" if kind == "FINAL" else kind, "participation": pa,
                              "actual": y, "signed_error": None if y is None else float(r.projection) - y, "result": res,
                              "side_win": win, "brier": None if win is None or pn is None else float((pn - win) ** 2),
                              "log_loss": None if win is None or pn is None else
                              float(-np.log(np.clip(pn if win else 1 - pn, 1e-12, 1))),
                              "closing_decimal_odds": None, "clv": None})
        summ.append({"game_id": gid, "kind": kind, "settled_at": now.isoformat(), "tick_id": tick["tick_id"],
                     "prediction_tick_id": reg[gid]["tick_id"], "quoted_predictions": n_pred, "quote_sides": int(len(qs)),
                     "refresh_quote_sides": n_refresh,
                     "players": int(len(proj))})
    if chain:
        tmp = tdir / "settlements.jsonl"
        tmp.write_text("".join(LG._canon(r) + "\n" for r in chain))
        OS.gzip_file(tmp, tdir / "settlements.jsonl.gz")
        tmp.unlink()
    if side_rows:
        pd.DataFrame(side_rows).to_parquet(tdir / "side_settlements.parquet", index=False)
    if act_rows:
        pd.DataFrame(act_rows).to_parquet(tdir / "actuals.parquet", index=False)
    OS.append_jsonl(ctx.ops("registry", "settlements.jsonl"), summ)
    tick["settlement"] = {"games": len(summ), "quoted_predictions": len(chain), "quote_sides": len(side_rows),
                          "players": len(act_rows), "kinds": pd.Series([s["kind"] for s in summ]).value_counts().to_dict() if summ else {}}


# ============================================================================ the tick
@contextlib.contextmanager
def _lock(prod_root: Path):
    Path(prod_root).mkdir(parents=True, exist_ok=True)
    f = open(Path(prod_root) / ".shadow_tick.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RuntimeError("another shadow tick holds the lock")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _restore(ctx: Ctx, tick: dict) -> None:
    if ctx.assets is not None:
        tick["restore"] = OS.restore_working_set(ctx.ops_root, ctx.assets, ctx.prod_root)
    OS.mirror_in(ctx.ops_root, ctx.prod_root)
    mk = ctx.ops("markers", "QUOTE_INTEGRATION_VALIDATED.json")
    rec = ctx.ops("markers", "odds_integration_validation_record.json")
    if mk.exists() and rec.exists() and not ctx.odds_record.exists():
        ctx.odds_record.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rec, ctx.odds_record)


def tick(ctx: Ctx) -> dict:
    with _lock(ctx.prod_root):
        return _tick(ctx)


def _tick(ctx: Ctx) -> dict:
    t0 = ctx.now()
    tid = "K" + t0.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    t = {"tick_id": tid, "version": TICK_VERSION, "source_commit": source_commit(), "frozen_commit": PC.FROZEN_COMMIT,
         "started_at": t0.isoformat(), "max_readiness": PC.MAX_READINESS_STATE,
         "wagering_enabled": PC.WAGERING_ENABLED, "public_deployment": False, "model_refit": False}
    Path(ctx.ops_root).mkdir(parents=True, exist_ok=True)
    tdir = ctx.ops("ticks", tid)
    # ---------------------------------------------------------------- frozen model gate (every tick)
    try:
        ident = LD.verify(ctx.artifact_paths)
        t["frozen_model_hash_gate"] = "PASS"
        t["artifact_sha256"] = ident["artifact_sha256"]
    except LD.ArtifactIntegrityError as e:
        t.update({"frozen_model_hash_gate": "FAIL", "status": "BLOCKED", "reason": e.reason, "error": str(e)[:500]})
        tdir.mkdir(parents=True, exist_ok=True)
        OS.write_json(tdir / "tick.json", t)
        OS.chain_append(ctx.ops_root, tid)
        _finish(ctx, t, persist_ws=False)
        return t
    if ctx.restore or ctx.assets is None:
        # fresh runner: restore the working set, then mirror the evidence ledgers in. Persistent host (Droplet, no
        # release assets): the same idempotent mirror_in on every tick (evidence -> working when the working copy is
        # absent or shorter; a longer working copy is kept; diverged ledgers raise), so a state imported or restored
        # as an evidence tree never starts a fresh working ledger beside it
        _restore(ctx, t)
    if ctx.history_db is None:
        ctx.history_db = HX.resolve()
    op = OS.read_json(ctx.ops("OPERATION.json"))
    if op is None:
        OS.write_json(ctx.ops("OPERATION.json"), {
            "operation": "CBB 2026-27 prospective shadow (JM1_FINAL, frozen e1353e5)", "operation_started_at": t0.isoformat(),
            "frozen_commit": PC.FROZEN_COMMIT, "frozen_tag": PC.FROZEN_TAG, "artifact_sha256": ident["artifact_sha256"],
            "validated_prediction_cutoff": PC.VALIDATED_PREDICTION_CUTOFF, "score_lead_minutes": PC.SCORE_LEAD_MINUTES,
            "max_readiness": PC.MAX_READINESS_STATE, "wagering_enabled": False, "historical_pts_repair_campaign": "CLOSED",
            "automatic_model_correction": False})
        t["bootstrapped_operation"] = True
    st = ST.current(ctx.state_root)
    p = plan(ctx, st, ctx.now())
    work = False
    # ---------------------------------------------------------------- 1. state sync (CBBD) if due
    if p["sync_due"]:
        step_sync(ctx, p, t)
        work = True
        st = ST.current(ctx.state_root)
        p = plan(ctx, st, ctx.now())
    if st is None:
        t["status"] = "BLOCKED"
        t["reason"] = "LIVE_DATA_FEED_UNAVAILABLE (no promoted 2026-27 state)"
    else:
        sv = st["manifest"]["state_version"]
        OS.write_once(ctx.ops("state_manifests", f"{sv}.json"), st["manifest"])
        if str(st["manifest"].get("provider", {}).get("kind", "")).upper() == "FIXTURE" and \
                Path(ctx.ops_root).resolve() == PC.OPS_ROOT.resolve():
            raise RuntimeError("fixture state may never reach the production ops evidence tree")
        # ------------------------------------------------------------ 2. scoring (T-60) incl. quote validation / snapshot
        if len(p["score"]):
            tdir.mkdir(parents=True, exist_ok=True)
            step_score(ctx, p, st, t, tdir)
            work = True
            p = plan(ctx, st, ctx.now())
        elif p["events_due"]:
            step_events(ctx, t, max_age_seconds=PC.ODDS_EVENTS_MAX_AGE_HOURS * 3600, hours=EVENTS_LOOKAHEAD_HOURS)
            work = True
            p = plan(ctx, st, ctx.now())
        # ------------------------------------------------------------ 3. missed cutoffs
        if len(p["missed"]):
            step_missed(ctx, p, t)
            work = True
        # ------------------------------------------------------------ 4. settlement
        if p["settle"]:
            tdir.mkdir(parents=True, exist_ok=True)
            step_settle(ctx, p, st, t, tdir)
            work = True
    # ---------------------------------------------------------------- 5. monitoring reports (only when inputs changed)
    if work and tdir.exists() and any(tdir.iterdir()):
        OS.write_json(tdir / "tick.json", {**t, "finished_at": ctx.now().isoformat()})
        OS.chain_append(ctx.ops_root, tid)
    if work or p["report_due"]:
        OS.mirror_out(ctx.ops_root, ctx.prod_root)               # this tick's request ledgers first: no one-tick report lag
        t["reports"] = PM.write_reports(ctx.ops_root, ctx.now(), p["operation_started_at"])
    # ---------------------------------------------------------------- 6. what is due next
    p = plan(ctx, st, ctx.now())
    OS.write_json(ctx.ops("DUE.json"), due_document(p, st, ctx))
    t.setdefault("status", "OK")
    t["did_work"] = work
    if work and not (tdir / "tick.json").exists():
        OS.append_jsonl(ctx.ops("ledgers", "tick_log.jsonl"), [{k: t.get(k) for k in (
            "tick_id", "started_at", "status", "sync", "events", "registry", "settlement")}])
    _finish(ctx, t, persist_ws=work)
    return t


def _finish(ctx: Ctx, t: dict, persist_ws: bool) -> None:
    OS.mirror_out(ctx.ops_root, ctx.prod_root)
    if ctx.odds_record.exists() and ctx.ops("markers", "QUOTE_INTEGRATION_VALIDATED.json").exists():
        OS.write_once(ctx.ops("markers", "odds_integration_validation_record.json"), json.loads(ctx.odds_record.read_text()))
    if ctx.persist and persist_ws and ctx.assets is not None:
        t["working_set"] = OS.persist_working_set(ctx.ops_root, ctx.assets, t["tick_id"], ctx.prod_root, now=ctx.now())
    if ctx.commit is not None:
        t["git"] = ctx.commit(ctx.ops_root, f"shadow tick {t['tick_id']}: {t.get('status')}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.shadow_tick")
    ap.add_argument("--ops-root", type=Path, default=PC.OPS_ROOT)
    ap.add_argument("--restore", action="store_true", help="restore the encrypted working set first (fresh runner)")
    ap.add_argument("--persist", action="store_true", help="upload the encrypted working set after work")
    ap.add_argument("--commit", action="store_true", help="commit the evidence tree (ops-branch checkout)")
    ap.add_argument("--push", action="store_true", help="push that commit to the private ops-state branch")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Risky-Scout/cbb-originator"))
    ap.add_argument("--allow-revalidation", action="store_true",
                    help="architect action: allow one more quote validation after a recorded FAIL")
    ap.add_argument("--authorize-revalidation", metavar="REASON",
                    help="architect action, no tick: record ONE re-validation authorization after a recorded FAIL "
                         "(the next scheduled tick validates); needs --fix-commit")
    ap.add_argument("--fix-commit", default="", help="the commit that fixes the recorded FAIL")
    a = ap.parse_args(argv)
    if a.authorize_revalidation is not None:
        print(json.dumps(authorize_revalidation(Ctx(ops_root=a.ops_root), a.authorize_revalidation, a.fix_commit),
                         indent=1, default=str))
        return
    from cbb_platform import duckdb_limits as DLM
    DLM.install()                                  # DuckDB memory cap + spill dir when configured (no-op otherwise)
    assets = commit = None
    if a.restore or a.persist or a.commit or a.push:          # off-machine I/O lives outside the production layer
        from cbb_platform import ops_persistence as OP
        if a.restore or a.persist:
            assets = OP.release_assets(a.repo)
        if a.restore and not PC.HISTORY_CANONICAL.exists():
            OP.fetch_history_extract(assets, HX.DEFAULT_OUT, json.loads(HX.MANIFEST.read_text()))
        if a.commit or a.push:
            commit = lambda root, msg: OP.git_commit_push(root, msg, push=a.push)    # noqa: E731
    ctx = Ctx(ops_root=a.ops_root, assets=assets, commit=commit, restore=a.restore, persist=a.persist,
              allow_revalidation=a.allow_revalidation)
    out = tick(ctx)
    keep = ("tick_id", "status", "reason", "frozen_model_hash_gate", "did_work", "sync", "events", "quote_validation",
            "quote_snapshots", "registry", "settlement", "first_live_completed_game_ingestion", "git", "working_set",
            "restore")
    sc = out.get("scoring") or {}
    print(json.dumps({**{k: out[k] for k in keep if k in out},
                      "scoring": {k: sc.get(k) for k in ("run_id", "games_due", "games_scored", "one_joint_bank_per_game",
                                                         "predictions", "readiness_state", "run_reasons")} if sc else None},
                     indent=1, default=str))
    if out.get("frozen_model_hash_gate") == "FAIL":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
