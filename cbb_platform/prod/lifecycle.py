"""THE daily production lifecycle (America/New_York): line-independent forecast snapshots + morning settlement/refit.

    PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.lifecycle morning  [--date D]      07:00 ET
    PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.lifecycle refresh  --window HH:MM  10:00 / 13:00 / 16:00 / 18:00 ET
    PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.lifecycle forecast [--window INITIAL]   (one snapshot now)
    PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.lifecycle schedule                  (the fixed schedule, JSON)

07:00 ET  morning(D):
    1. shadow tick (prod.shadow_tick): CBBD sync of the prior night's final games + settlement of prior predictions
    2. CBB_Grades for D-1 (prod.exports; only valid pre-tip priced receipts are graded, never unquoted forecasts)
    3. the authorized daily walk-forward update (walkforward.daily.morning: eligibility -> training rows -> refit ->
       existing hard gates -> atomic promotion, else the last-known-good version stays ACTIVE)
    4. the INITIAL forecast snapshot of D's full eligible slate (below) + the D exports (Distributions always; Boards
       when priced receipts exist)
10:00 / 13:00 / 16:00 / 18:00 ET  refresh(window): RotoWire availability refresh -> re-forecast ONLY the games whose
    availability changed (cutoff not yet passed) -> new immutable snapshot -> D exports regenerated. Without
    ROTOWIRE_API_KEY the refresh is recorded as BLOCKED_NO_KEY and no forecast changes (nothing new is knowable).

MODEL FORECAST vs BETTING OPPORTUNITY (directive §10): a forecast snapshot needs no sportsbook quote. It runs the
existing scorer (prod.run.daily, unchanged, on the ACTIVE model) with NO quotes and buys nothing; the official priced
prediction of each game remains the shadow tick's single pre-cutoff (TIP_MINUS_1H) run, which collects quotes.

Evidence (immutable, never overwritten; a later snapshot never rewrites an earlier one):
    <ops_root>/forecasts/<D>/<snapshot_id>/snapshot.json         window, times, model version, state, games (+cutoffs)
    <ops_root>/forecasts/<D>/<snapshot_id>/pmfs.parquet          complete PMFs (prod.pmf_evidence, directive §13)
    <ops_root>/forecasts/<D>/<snapshot_id>/forecast_players.parquet   expected minutes, P(play), availability per player
    <ops_root>/forecasts/index.jsonl                              one line per snapshot
Only games whose production cutoff is still in the future are forecast: a refresh after a game's cutoff can never
change that game's official forecast, and nothing later is ever backfilled into an earlier snapshot.

Nothing here fits outside the authorized walk-forward step, recalibrates, or changes minutes / PMF / pricing /
dependence / thresholds. No WizardOfOdds publication, no wagering (PC.WAGERING_ENABLED stays False).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from cbb_platform.prod import (config as PC, history_extract as HX, loader as LD, ops_store as OS, pmf_evidence as PE,
                               run as RN, shadow_tick as SH, state as ST)

LIFECYCLE_VERSION = "cbb-prod-daily-lifecycle-1.0.0"
TZ = "America/New_York"
MORNING_TIME_ET = "07:00"
FEATURES_WARMUP_TIME_ET = "01:30"     # ends (budget 150 min) before NBA daily 04:37 ET and the 07:00 morning
ROTOWIRE_REFRESH_TIMES_ET = ("10:00", "13:00", "16:00", "18:00")       # exactly four; no other fixed refresh time
ROTOWIRE_LIGHT_POLL_TIMES_ET = ("08:30",)   # acquisition ONLY (no forecast / fit / simulation / PMF); writer lock only
SCHEDULE_ET = ((MORNING_TIME_ET, "MORNING"),) + tuple((t, "ROTOWIRE_REFRESH") for t in ROTOWIRE_REFRESH_TIMES_ET)
INITIAL = "INITIAL"
# ---- shared cross-model heavy-job lock (operational only; nothing here reaches a forecast). Only the walk-forward step
# takes it. Not acquired within the wait -> DEFERRED_SHARED_LOCK: the last-known-good ACTIVE model stays, the rest of
# the morning still runs (the day is never left without forecasts), and ONE retry runs at REFIT_RETRY_TIME_ET.
REFIT_RETRY = "REFIT_RETRY"
REFIT_RETRY_TIME_ET = "08:00"
DEFERRED_SHARED_LOCK = "DEFERRED_SHARED_LOCK"
# 07:00 feature catch-up bound (2026-11-11: an unbounded catch-up of ~190 newly eligible games ran the morning into its
# 2 h systemd timeout and SIGTERM, so no 07:00 forecast was made). The morning first fills the pregame feature cache
# with the warmup's own builder inside this budget; an unfinished backlog DEFERS the refit (ACTIVE kept, the day is
# forecast with it) and the 08:00 retry / next warmup continue from the persisted cache. Fits and gates are unchanged.
DEFERRED_FEATURE_BACKLOG = "DEFERRED_FEATURE_BACKLOG"
RETRYABLE_DEFERRALS = (DEFERRED_SHARED_LOCK, DEFERRED_FEATURE_BACKLOG)
# The state the refit trains on is the latest version as-of the training cutoff (point in time). When no sync confirmed
# it within PC.MAX_STATE_AGE_AT_REFIT_CUTOFF_MINUTES before that cutoff, the refit is refused rather than trained on a
# stale state; no later refresh can legally change the as-of state, so this deferral is not retried at 08:00.
DEFERRED_STATE_REFRESH_FAILED = "DEFERRED_STATE_REFRESH_FAILED"
MORNING_FEATURE_BUDGET_ENV = "CBB_MORNING_FEATURE_BUDGET_MIN"
MORNING_FEATURE_BUDGET_MIN = 30.0     # worst case 07:00 total ~80 min (tick + 30 + refit ~35 + forecast)
HEAVY_LOCK_ENV, HEAVY_WAIT_ENV, MIN_MEM_ENV = "CBB_HEAVY_LOCK", "CBB_HEAVY_LOCK_WAIT_SEC", "CBB_MIN_MEMAVAILABLE_MB"
WF_KEYS = ("DAILY_REFIT_STATUS", "DAILY_MODEL_PROMOTION", "ACTIVE_MODEL_VERSION", "CANDIDATE_MODEL_VERSION", "note", "reason")
ROTOWIRE_KEY_ENV = "ROTOWIRE_API_KEY"
PLAYER_COLS = ["game_id", "team_id", "player_id", "min_mu", "min_sigma", "p_play", "availability_status",
               "availability_received_at", "player_suppression", "cold_start_class", "role_diagnostic"]


@dataclass
class Life:
    ops_root: Path = PC.OPS_ROOT
    prod_root: Path = PC.PROD_ROOT
    history_db: Path | None = None
    model_root: Path | None = None           # walk-forward store root (ACTIVE model); None -> walkforward.config default
    export_root: Path | None = None
    clock: object = None
    sims: int = PC.SIMS
    artifact_paths: dict | None = None
    rotowire_enabled: bool | None = None     # None -> enabled iff ROTOWIRE_API_KEY is set and the smoke PASSED
    rotowire_web_client: object = None       # injectable prod.rotowire_web_acquire.WebClient (tests); None -> env session

    def now(self) -> pd.Timestamp:
        return SH._utc(self.clock()) if self.clock else pd.Timestamp.now(tz="UTC")

    @property
    def state_root(self) -> Path: return Path(self.prod_root) / "state"
    @property
    def runs(self) -> Path: return Path(self.prod_root) / "runs"
    @property
    def exports(self) -> Path:
        return Path(self.export_root or os.environ.get("CBB_EXPORT_ROOT") or Path(self.prod_root) / "exports")
    def forecasts(self, *p) -> Path: return Path(self.ops_root).joinpath("forecasts", *p)


def schedule_spec() -> dict:
    """The fixed daily schedule the Droplet units implement (deploy/droplet/systemd/cbb-lifecycle-*)."""
    return {"timezone": TZ, "morning": {"at": MORNING_TIME_ET, "steps": [
        "prior-day settlement (shadow tick: CBBD sync of final games, settlement)", "prior-day Grades export",
        "daily walk-forward update (existing gates; last-known-good retained on failure)", "INITIAL forecast snapshot",
        "Distributions export", "Boards export (forecast universe; market consensus when quotes exist)"]},
        "rotowire_refresh": {"at": list(ROTOWIRE_REFRESH_TIMES_ET), "steps": [
            "RotoWire availability refresh (BLOCKED_NO_KEY without ROTOWIRE_API_KEY)",
            "re-forecast only games with an availability change and a future cutoff", "PMFs persisted",
            "Boards / Distributions regenerated"]},
        "features_warmup": {"at": [FEATURES_WARMUP_TIME_ET], "steps": [
            "pre-build the immutable per-game pregame feature cache the next 07:00 walk-forward reads (same code path)",
            "shared heavy lock, hard time budget; no refit / promotion / forecast / simulation / PMF; no writer lock"]},
        "rotowire_light_poll": {"at": list(ROTOWIRE_LIGHT_POLL_TIMES_ET), "steps": [
            "RotoWire WEB acquisition only (snapshot + injury news into the event store; narrows the overnight news gap)",
            "no forecast, no refit, no simulation, no PMF; CBB writer lock only, never the shared heavy lock"]},
        "official_prediction": "per game, the shadow tick's single run inside [cutoff - SCORE_LEAD_MINUTES, cutoff) "
                               f"(cutoff {PC.VALIDATED_PREDICTION_CUTOFF}); a refresh after the cutoff never changes it",
        "other_fixed_rotowire_refresh_times": []}


def window_label(window: str) -> str:
    if window == INITIAL:
        return "INITIAL_0700"
    if window == REFIT_RETRY:
        return "REFIT_RETRY_" + REFIT_RETRY_TIME_ET.replace(":", "")
    if window not in ROTOWIRE_REFRESH_TIMES_ET:
        raise ValueError(f"refresh window {window!r} is not one of {ROTOWIRE_REFRESH_TIMES_ET}")
    return "ROTOWIRE_" + window.replace(":", "")


def et_date(t) -> date:
    return SH._utc(t).tz_convert(TZ).date()


# ============================================================================ the slate of one ET date
def slate(st: dict, day: date, now: pd.Timestamp) -> pd.DataFrame:
    """Eligible D-I games of ET date `day` that have no result and whose production cutoff is still in the future."""
    sch = SH.schedule(st)
    d = pd.to_datetime(sch["game_date_et"]).dt.date
    g = sch[(d == day) & (sch["game_universe"] == "DI_VS_DI") & ~sch["not_played"] & sch["home_points"].isna()
            & (sch["cutoff"] > now)]
    return g.sort_values(["cutoff", "canonical_game_id"]).reset_index(drop=True)


def snapshots(life: Life, day: date | None = None) -> list[dict]:
    rows = OS.read_jsonl(life.forecasts("index.jsonl"))
    return [r for r in rows if day is None or r.get("slate_date") == day.isoformat()]


def _model(life: Life, now: pd.Timestamp):
    """The ACTIVE walk-forward version (its training cutoff <= now), else the frozen opening artifacts. Hash-verified."""
    from cbb_platform.prod import model_source as MS
    try:
        return MS.resolve("ACTIVE_DAILY", now, life.model_root)
    except MS.ModelSourceUnavailable:
        return LD.load(life.artifact_paths)


# ============================================================================ RotoWire availability refresh
def rotowire_state(life: Life) -> str:
    """ROTOWIRE_SOURCE (prod/config.rotowire_source) selects the acquisition source; unset keeps the legacy rule.
    WEB is ENABLED only with a configured subscriber session AND a PASS live-acceptance marker."""
    if life.rotowire_enabled is not None:
        return "ENABLED" if life.rotowire_enabled else "DISABLED"
    src = PC.rotowire_source()
    if src == "DISABLED":
        return "DISABLED"
    if src == "WEB":
        if not os.environ.get(PC.ROTOWIRE_WEB_COOKIE_ENV):
            return "BLOCKED_NO_WEB_SESSION"
        from cbb_platform.prod import rotowire_web_acquire as RWW
        acc = OS.read_json(Path(life.ops_root) / "markers" / RWW.ACCEPTANCE_MARKER, {}) or {}
        return "ENABLED" if acc.get("status") == "PASS" else "DISABLED_WEB_ACCEPTANCE_NOT_PASSED"
    if not os.environ.get(ROTOWIRE_KEY_ENV):
        return "BLOCKED_NO_KEY"
    smoke = OS.read_json(Path(life.ops_root) / "markers" / "ROTOWIRE_SMOKE.json", {})
    return "ENABLED" if smoke.get("status") == "PASS" else "DISABLED_SMOKE_NOT_PASSED"


def availability_changes(life: Life, games: pd.DataFrame, now: pd.Timestamp) -> tuple[list, dict]:
    """Games whose point-in-time availability (RotoWire event store, knowable < now) differs from the status their
    latest snapshot priced with. Returns (affected game ids, per-game changed players)."""
    from cbb_platform.prod import rotowire_feed as RW
    ev = RW.EventStore(PC.ROTOWIRE_ROOT).frame()
    sg = games.rename(columns={"scheduled_tip_utc": "start_time"}) if "start_time" not in games else games
    if "feature_cutoff" not in sg:                 # the slate's validated production cutoff IS the feature cutoff
        sg = sg.assign(feature_cutoff=sg["cutoff"])  # (run.daily: feature_cutoff != prediction cutoff -> FUTURE_LEAKAGE)
    av, _ = RW.availability_frame(ev, sg, now)
    cur = {(r.game_id, r.player_id): r.status for r in av.itertuples(index=False)} if len(av) else {}
    affected, changed = [], {}
    for gid in games["canonical_game_id"]:
        prev = latest_players(life, gid)
        before = {} if prev is None else {(gid, p): s for p, s in zip(prev["player_id"], prev["availability_status"])}
        keys = {k for k in set(cur) | set(before) if k[0] == gid}
        diff = sorted(k[1] for k in keys if (cur.get(k) or None) != (before.get(k) if isinstance(before.get(k), str) else None))
        if diff:
            affected.append(gid)
            changed[gid] = diff
    return affected, changed


def latest_players(life: Life, gid: str) -> pd.DataFrame | None:
    for s in reversed(snapshots(life)):
        if gid in s.get("games", []):
            p = life.forecasts(s["slate_date"], s["snapshot_id"], "forecast_players.parquet")
            if p.exists():
                d = pd.read_parquet(p)
                return d[d["game_id"] == gid]
    return None


# ============================================================================ one forecast snapshot
def forecast(life: Life, window: str = INITIAL, now=None, game_ids=None) -> dict:
    now = SH._utc(now) if now is not None else life.now()
    day = et_date(now)
    sid = "F" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    rec = {"snapshot_id": sid, "version": LIFECYCLE_VERSION, "window": window_label(window), "slate_date": day.isoformat(),
           "scheduled_time_et": {INITIAL: MORNING_TIME_ET, REFIT_RETRY: REFIT_RETRY_TIME_ET}.get(window, window), "executed_at": now.isoformat(),
           "wagering_enabled": PC.WAGERING_ENABLED, "quotes_used": 0, "paid_requests": 0}
    try:
        LD.verify(life.artifact_paths)
    except LD.ArtifactIntegrityError as e:
        return {**rec, "status": "BLOCKED", "reason": e.reason}
    st = ST.current(life.state_root)
    if st is None:
        return {**rec, "status": "BLOCKED", "reason": "LIVE_DATA_FEED_UNAVAILABLE (no promoted 2026-27 state)"}
    games = slate(st, day, now)
    if game_ids is not None:
        games = games[games["canonical_game_id"].isin(set(game_ids))]
    if window not in (INITIAL, REFIT_RETRY):                   # a REFIT_RETRY re-forecasts every pre-cutoff game
        rw = rotowire_state(life)
        rec["rotowire_refresh"] = rw
        if rw != "ENABLED":
            games = games.iloc[0:0]
            rec["note"] = "no availability source refreshed: no forecast changed (prior snapshots stand)"
        else:
            affected, changed = availability_changes(life, games, now)
            rec["availability_changes"] = changed
            games = games[games["canonical_game_id"].isin(affected)]
    rec["games"] = games["canonical_game_id"].tolist()
    rec["cutoffs"] = {g: c.isoformat() for g, c in zip(games["canonical_game_id"], games["cutoff"])}
    rec["scheduled_tips"] = {g: t.isoformat() for g, t in zip(games["canonical_game_id"], games["scheduled_tip_utc"])}
    sdir = life.forecasts(day.isoformat(), sid)
    if not len(games):
        rec.update(status="NO_FORECAST_DUE", forecasts=0, pmfs_persisted=0)
        OS.write_once(sdir / "snapshot.json", rec)
        OS.append_jsonl(life.forecasts("index.jsonl"), [_index(rec)])
        return rec
    if life.history_db is None:
        life.history_db = HX.resolve()
    model = _model(life, now)
    ident = model.identity
    s = RN.daily(now, quotes_paths=[], state_root=life.state_root, history_db=life.history_db,
                 ledger_path=sdir / "forecast_ledger.jsonl", run_root=life.runs, horizon_hours=PC.ODDS_PROP_WINDOW_HOURS,
                 sims=life.sims, game_ids=rec["games"], sync_log=Path(life.prod_root) / "sync" / "sync_log.jsonl",
                 odds_cache_root=Path(life.prod_root) / "cache" / "raw", odds_collections=None,
                 odds_ledger=Path(life.prod_root) / "cache" / "odds_production_requests.jsonl",
                 odds_record=Path(life.prod_root) / "odds" / "integration_validation.json",
                 side_monitor_path=sdir / "side_monitor.jsonl", model=model,
                 rotowire_root=PC.ROTOWIRE_ROOT if rec.get("rotowire_refresh") == "ENABLED" else None)
    run_dir = Path(s.get("run_dir") or life.runs / s["run_id"])
    pe = PE.from_run(run_dir, s["run_id"], now, ident["model_version"], rec["games"])
    w = PE.write(sdir / PE.FILE, pe) if len(pe) else {"pmfs": 0}
    players = pd.DataFrame(columns=PLAYER_COLS)
    if (run_dir / "model_inputs.parquet").exists():
        mi = pd.read_parquet(run_dir / "model_inputs.parquet")
        players = mi[[c for c in PLAYER_COLS if c in mi.columns]].drop_duplicates(["game_id", "player_id"])
        players = players[players["game_id"].isin(rec["games"])].reset_index(drop=True)
    players = players.assign(snapshot_id=sid, run_id=s["run_id"], prediction_as_of=now.isoformat(),
                             model_version=ident["model_version"])
    sdir.mkdir(parents=True, exist_ok=True)
    players.to_parquet(sdir / "forecast_players.parquet", index=False)
    scored = sorted(s.get("banks", {}))
    rec.update(status="FORECAST", run_id=s["run_id"], prediction_as_of=now.isoformat(),
               model_version=ident["model_version"], model_kind=ident.get("model_kind"),
               artifact_sha256=ident.get("artifact_sha256"), state_version=st["manifest"]["state_version"],
               readiness_state=s.get("readiness_state"), run_reasons=s.get("run_reasons"),
               games_forecast=len(scored), games_not_scored={g: s.get("game_reasons", {}).get(g) for g in rec["games"]
                                                             if g not in scored},
               players_forecast=int(pe[["game_id", "player_id"]].drop_duplicates().shape[0]) if len(pe) else 0,
               stat_predictions=int(pe["prediction_receipt_id"].nunique()) if len(pe) else 0,
               pmfs_persisted=int(w.get("pmfs", 0)), pmfs_sha256=w.get("sha256"),
               availability=s.get("availability"))
    OS.write_once(sdir / "snapshot.json", rec)
    OS.append_jsonl(life.forecasts("index.jsonl"), [_index(rec)])
    return rec


def _index(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("snapshot_id", "slate_date", "window", "executed_at", "prediction_as_of", "status",
                                    "games", "cutoffs", "model_version", "pmfs_persisted", "rotowire_refresh")}


# ============================================================================ the scheduled jobs
def exports_for(life: Life, day: date) -> dict:
    from cbb_platform.prod import exports as EX
    return EX.daily(day.isoformat(), ops_root=Path(life.ops_root), runs_root=life.runs, out_root=life.exports,
                    history_db=life.history_db or HX.resolve(), state_root=life.state_root, now=life.now())


# ============================================================================ walk-forward under the shared heavy lock
def _mem_available_mb(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    try:
        for ln in Path(meminfo).read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def acquire_heavy_lock(path: str, wait_s: float, sleep=time.sleep, clock=time.monotonic):
    """Exclusive flock(2) on the shared heavy lock, waiting at most wait_s. Returns the open fd (held until closed;
    the kernel releases it on exit, crash or kill) or None. Never deletes or rewrites the lock file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o660)
    deadline = clock() + float(wait_s)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if clock() >= deadline:
                os.close(fd)
                return None
            sleep(max(0.0, min(5.0, deadline - clock())))


WARMUP_BUDGET_ENV, WARMUP_WAIT_ENV = "CBB_WARMUP_BUDGET_MIN", "CBB_WARMUP_HEAVY_LOCK_WAIT_SEC"


def warmup_day(now) -> date:
    """The morning a warmup serves: today's if it runs before the 07:00 ET morning, else tomorrow's."""
    t = SH._utc(now).tz_convert(TZ)
    return t.date() if t.strftime("%H:%M") < MORNING_TIME_ET else (t + pd.Timedelta(days=1)).date()


def features_warmup(life: Life, budget_min: float | None = None, runner=None) -> dict:
    """Overnight pregame-feature warmup (walkforward.daily.warm_features): fills the immutable per-game feature cache the
    next morning's walk-forward reads, under the shared heavy lock, inside a hard time budget. Scheduling only: no
    refit, no promotion, no forecast, no simulation, no PMF, no ops-state write (CBB writer lock not taken)."""
    budget = float(budget_min if budget_min is not None else os.environ.get(WARMUP_BUDGET_ENV, "150")) * 60.0
    lock, wait = os.environ.get(HEAVY_LOCK_ENV), float(os.environ.get(WARMUP_WAIT_ENV, "900"))
    day = warmup_day(life.now())
    rec = {"lifecycle": "FEATURES_WARMUP", "serves_morning": day.isoformat(), "started_at": life.now().isoformat(),
           "heavy_lock": lock, "budget_s": budget}
    fd, t0 = None, time.monotonic()
    if lock:
        fd = acquire_heavy_lock(lock, wait)
        rec["lock_wait_s"] = round(time.monotonic() - t0, 1)
        rec["lock_result"] = "ACQUIRED" if fd is not None else "NOT_ACQUIRED"
        if fd is None:
            rec["status"] = "DEFERRED_SHARED_LOCK"
            OS.append_jsonl(Path(life.prod_root) / "walkforward_attempts" / f"warmup-{day.isoformat()}.jsonl", [rec])
            return rec
    else:
        rec["lock_result"] = "NOT_CONFIGURED"
    try:
        from cbb_platform.walkforward import config as WC, daily as WD
        run = runner or WD.warm_features
        try:
            rec["warmup"] = run(day, root=Path(life.model_root or WC.STORE_ROOT), state_root=life.state_root,
                                budget_s=max(0.0, budget - (time.monotonic() - t0)))
            rec["status"] = rec["warmup"].get("status")
        except Exception as e:                           # noqa: BLE001 -- the cache is append-only; nothing to undo
            rec.update(status="FAIL", reason=f"{type(e).__name__}: {e}"[:400])
    finally:
        if fd is not None:
            os.close(fd)
    rec["finished_at"] = life.now().isoformat()
    OS.append_jsonl(Path(life.prod_root) / "walkforward_attempts" / f"warmup-{day.isoformat()}.jsonl", [rec])
    return rec


def wf_attempts_path(life: Life, day: date) -> Path:
    return Path(life.prod_root) / "walkforward_attempts" / f"{day.isoformat()}.jsonl"


def state_freshness(life: Life, day: date) -> dict:
    """The state the day's refit would train on (eligibility.state_asof at the training cutoff) and the latest instant
    a successful sync (PROMOTED, or UNCHANGED = same version re-confirmed) confirmed it, no later than the cutoff."""
    from cbb_platform.walkforward import config as WC, eligibility as EL
    cut = WC.refit_cutoff(day)
    st = EL.state_asof(life.state_root, cut)
    out = {"training_cutoff": cut.isoformat(), "state_version": None, "confirmed_at": None, "STATE_AGE_MIN": None,
           "max_age_min": PC.MAX_STATE_AGE_AT_REFIT_CUTOFF_MINUTES, "STATE_FRESH": None,
           "FAILED_RETRYABLE_REQUESTS": 0, "FAILED_NONRETRYABLE_REQUESTS": 0, "last_sync_before_cutoff": None}
    log = OS.read_jsonl(Path(life.prod_root) / "sync" / "sync_log.jsonl")
    before = [r for r in log if r.get("finished_at") and SH._utc(r["finished_at"]) <= cut]
    if before:                                  # the completed-stage request failures the refit's state still carries
        last = before[-1]
        fails = [f for f in last.get("completed_request_failures") or [] if f.get("ATTEMPT_TIME")]
        out.update(FAILED_RETRYABLE_REQUESTS=sum(bool(f.get("RETRYABLE")) for f in fails),
                   FAILED_NONRETRYABLE_REQUESTS=sum(not f.get("RETRYABLE") for f in fails),
                   last_sync_before_cutoff={k: last.get(k) for k in ("sync_id", "status", "outcome", "finished_at")})
    if st is None:
        return out
    seen = [SH._utc(st["source_cutoff"])]
    for r in log:
        if r.get("status") in ("PROMOTED", "UNCHANGED") and r.get("state_version") == st["state_version"] \
                and r.get("source_cutoff") and r.get("finished_at") and SH._utc(r["finished_at"]) <= cut:
            seen.append(min(SH._utc(r["source_cutoff"]), cut))
    conf = max(seen)
    age = (cut - conf).total_seconds() / 60.0
    out.update(state_version=st["state_version"], confirmed_at=conf.isoformat(), STATE_AGE_MIN=round(age, 1),
               STATE_FRESH=age <= PC.MAX_STATE_AGE_AT_REFIT_CUTOFF_MINUTES)
    return out


def walk_forward(life: Life, day: date, attempt: str = "MORNING_0700", runner=None, warm_runner=None) -> dict:
    """One authorized walk-forward attempt (walkforward.daily.morning: existing gates, atomic promotion, last-known-good
    kept on any failure). The shared heavy lock (env CBB_HEAVY_LOCK) is held for exactly this step."""
    lock, wait = os.environ.get(HEAVY_LOCK_ENV), float(os.environ.get(HEAVY_WAIT_ENV, "1800"))
    rec = {"attempt": attempt, "day": day.isoformat(), "heavy_lock": lock, "lock_wait_limit_s": wait,
           "started_at": life.now().isoformat()}
    fd, t0 = None, time.monotonic()
    if lock:
        fd = acquire_heavy_lock(lock, wait)
        rec["lock_wait_s"] = round(time.monotonic() - t0, 1)
        rec["lock_result"] = "ACQUIRED" if fd is not None else "NOT_ACQUIRED"
        if fd is None:
            rec.update(DAILY_REFIT_STATUS=DEFERRED_SHARED_LOCK, DAILY_MODEL_PROMOTION="NOT_ATTEMPTED",
                       note="shared heavy lock busy; last-known-good ACTIVE model retained")
            OS.append_jsonl(wf_attempts_path(life, day), [rec])
            return rec
    else:
        rec["lock_result"] = "NOT_CONFIGURED"
    try:
        need = os.environ.get(MIN_MEM_ENV)
        have = _mem_available_mb()
        rec["mem_available_mb_at_start"] = None if have is None else round(have, 1)
        if need and have is not None and have < float(need):
            rec.update(DAILY_REFIT_STATUS="FAIL", DAILY_MODEL_PROMOTION="NOT_ATTEMPTED",
                       reason=f"RESOURCE: MemAvailable {have:.0f} MB < {float(need):.0f} MB; last-known-good retained")
        elif (fresh := state_freshness(life, day)) and fresh["STATE_FRESH"] is False:
            rec["state_freshness"] = fresh
            rec.update(DAILY_REFIT_STATUS=DEFERRED_STATE_REFRESH_FAILED, DAILY_MODEL_PROMOTION="NOT_ATTEMPTED",
                       reason=f"STATE_STALE_AT_CUTOFF: state {fresh['state_version']} last confirmed "
                              f"{fresh['confirmed_at']}, {fresh['STATE_AGE_MIN']} min before the training cutoff "
                              f"{fresh['training_cutoff']} (max {fresh['max_age_min']}); last-known-good ACTIVE retained")
        else:
            rec["state_freshness"] = fresh
            from cbb_platform.walkforward import config as WC, daily as WD
            run = runner or WD.morning
            warm = warm_runner or (WD.warm_features if runner is None else None)
            root = Path(life.model_root or WC.STORE_ROOT)
            try:
                if warm is not None:                     # bounded catch-up first: the refit then only fits
                    budget = float(os.environ.get(MORNING_FEATURE_BUDGET_ENV, MORNING_FEATURE_BUDGET_MIN)) * 60.0
                    t1 = time.monotonic()
                    fw = warm(day, root=root, state_root=life.state_root, budget_s=budget)
                    rec["feature_catchup"] = {**{k: fw.get(k) for k in ("status", "included_games", "cached_before",
                                                                         "cutoffs_todo", "cutoffs_built", "games_built")},
                                              "budget_s": budget, "elapsed_s": round(time.monotonic() - t1, 1)}
                    if fw.get("status") == "BUDGET_EXHAUSTED":
                        rec.update(DAILY_REFIT_STATUS=DEFERRED_FEATURE_BACKLOG, DAILY_MODEL_PROMOTION="NOT_ATTEMPTED",
                                   note=f"pregame feature backlog {fw.get('cutoffs_todo')} cutoffs > the morning budget "
                                        f"({fw.get('cutoffs_built')} built and kept); last-known-good ACTIVE model "
                                        "retained and used for today's forecast")
                if rec.get("DAILY_REFIT_STATUS") != DEFERRED_FEATURE_BACKLOG:
                    wf = run(day, root=root, state_root=life.state_root, now=life.now())
                    rec.update({k: wf.get(k) for k in WF_KEYS})
                    for k in ("memory_mb", "prior_day_completeness"):
                        if wf.get(k) is not None:
                            rec[k] = wf[k]
            except Exception as e:                       # noqa: BLE001 -- promotion is atomic: ACTIVE is unchanged
                rec.update(DAILY_REFIT_STATUS="FAIL", DAILY_MODEL_PROMOTION="NOT_PROMOTED",
                           reason=f"{type(e).__name__}: {e}"[:400])
                if runner is None:                       # the morning job's stage markers (walkforward.daily.PROGRESS)
                    rec["failed_stage"] = WD.PROGRESS.get("stage")
                    rec["memory_mb"] = dict(WD.PROGRESS.get("memory_mb") or {})
    finally:
        if fd is not None:
            os.close(fd)
    rec["finished_at"] = life.now().isoformat()
    OS.append_jsonl(wf_attempts_path(life, day), [rec])
    return rec


def refit_retry(life: Life, day: date | None = None, runner=None, warm_runner=None) -> dict:
    """08:00 ET: one retry, ONLY when the 07:00 attempt of the day was deferred (DEFERRED_SHARED_LOCK or
    DEFERRED_FEATURE_BACKLOG); a 07:00 that ran (PASS / FAIL / NO_NEW_DATA) is a NO_OP. A retry that promotes a
    new model re-forecasts the still-pre-cutoff games (new immutable snapshot; earlier snapshots stand)."""
    day = day or et_date(life.now())
    recs = OS.read_jsonl(wf_attempts_path(life, day))
    out = {"lifecycle": REFIT_RETRY, "day": day.isoformat()}
    if not recs or recs[-1].get("DAILY_REFIT_STATUS") not in RETRYABLE_DEFERRALS \
            or any(r.get("attempt") == "RETRY_0800" for r in recs):
        out.update(status="NO_OP", reason="the 07:00 walk-forward was not deferred (or already retried)")
        health(life, day, refit_retry=out)
        return out
    wf = walk_forward(life, day, "RETRY_0800", runner=runner, warm_runner=warm_runner)
    out.update(status="RAN", walk_forward=wf)
    if wf.get("DAILY_MODEL_PROMOTION") == "PROMOTED":
        out["forecast"] = forecast(life, REFIT_RETRY)
        out["exports"] = _brief(exports_for(life, day))
    health(life, day, refit_retry=out)
    return out


# ============================================================================ daily health record (no secrets)
def health_path(life: Life, day: date) -> Path:
    return Path(life.prod_root) / "health" / f"{day.isoformat()}.json"


FORECAST_SUMMARY = ("snapshot_id", "window", "model_version", "games_forecast", "players_forecast", "stat_predictions", "pmfs_persisted", "games_not_scored")


def health(life: Life, day: date, **sections) -> dict:
    """Merge this step's machine-readable summary into <prod_root>/health/<D>.json (one file per ET date)."""
    p = health_path(life, day)
    h = OS.read_json(p, {}) or {}
    h.update({"et_date": day.isoformat(), "code_sha": os.environ.get("CBB_CODE_COMMIT") or SH.source_commit(),
              "updated_at": life.now().isoformat(), "rotowire_mode": _rotowire_mode(),
              "automatic_wagering": "DISABLED", "wizardofodds_publication": "DISABLED"})
    for k, v in sections.items():
        h[k] = v
    for k, v in sections.items():                  # the newest forecast of the day (morning, retry or refresh)
        if isinstance(v, dict) and v.get("status") == "FORECAST" and (k == "forecast" or v.get("snapshot_id")):
            h["latest_forecast"] = {k: v.get(k) for k in FORECAST_SUMMARY}
    ex = sections.get("exports")
    if isinstance(ex, dict):
        h["export_paths"] = {k: (v or {}).get("path") for k, v in ex.items() if isinstance(v, dict)}
    h["receipt"] = receipt(h)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(h, indent=1, default=str))
    os.replace(tmp, p)
    return h


def board_status(export_status: str | None, quote_refresh: dict, real_quotes: bool) -> str | None:
    """Why there is (no) Board today, explicitly -- never a silent absence: WRITTEN; NONE_QUOTE_INTEGRATION_NOT_VALIDATED
    (quotes not acquired: the gate is closed); NONE_NO_REAL_QUOTES (acquired, no sportsbook player-prop quote);
    NONE_NO_VALID_OFFER (real quotes, none executable / matched)."""
    if export_status is None:
        return None
    if export_status == "WRITTEN":
        return "WRITTEN"
    if export_status.startswith("ABORTED"):
        return export_status                       # a defect (e.g. ABORTED_DUPLICATE_BOARD_KEYS), never a quote cause
    if not real_quotes and str(quote_refresh.get("reason") or "").startswith("QUOTE_INTEGRATION_NOT_VALIDATED"):
        return "NONE_QUOTE_INTEGRATION_NOT_VALIDATED"
    return "NONE_NO_REAL_QUOTES" if not real_quotes else "NONE_NO_VALID_OFFER"


def receipt(h: dict) -> dict:
    """The machine-readable daily production receipt: a pure summary of the health sections already recorded today
    (derived on every health write; it computes nothing new and never reads model state)."""
    wf = h.get("walk_forward") or {}
    lf = h.get("latest_forecast") or {}
    fc = h.get("forecast") or {}
    ex = h.get("exports") or {}
    qr = h.get("quote_refresh") or {}
    acqs = sorted([v["rotowire_web_acquisition"] for k, v in h.items() if k.startswith("availability_")
                   and isinstance(v, dict) and isinstance(v.get("rotowire_web_acquisition"), dict)],
                  key=lambda a: str(a.get("at") or a.get("acquisition_id") or ""))
    last = acqs[-1] if acqs else {}
    ok = [a for a in acqs if a.get("auth") == "PASS" and a.get("news_status") == "PROCESSED"
          and a.get("snapshot_status") == "PROCESSED"]
    st = lambda k: (ex.get(k) or {}).get("status") if isinstance(ex.get(k), dict) else None
    errors, warnings = [], []
    if not wf:                                     # no morning walk-forward recorded for this ET date (yet / killed)
        warnings.append("MORNING_NOT_RECORDED")
    if wf.get("DAILY_REFIT_STATUS") == "FAIL":
        errors.append(f"WALK_FORWARD_FAIL: {wf.get('reason')}")
    if wf.get("DAILY_REFIT_STATUS") in RETRYABLE_DEFERRALS:
        warnings.append(f"WALK_FORWARD_{wf.get('DAILY_REFIT_STATUS')}")
    if wf.get("DAILY_REFIT_STATUS") == DEFERRED_STATE_REFRESH_FAILED:
        errors.append(f"WALK_FORWARD_{DEFERRED_STATE_REFRESH_FAILED}: {wf.get('reason')}")
    pdc = wf.get("prior_day_completeness") or {}
    if pdc.get("STATUS") not in (None, "COMPLETE"):
        warnings.append(f"{pdc['STATUS']}: {pdc.get('INCOMPLETE_FINAL')} of {pdc.get('FINAL_GAMES')} prior-day finals")
    sf = wf.get("state_freshness") or {}
    if sf.get("FAILED_RETRYABLE_REQUESTS") or sf.get("FAILED_NONRETRYABLE_REQUESTS"):
        warnings.append(f"PRE_REFIT_REQUEST_FAILURES: {sf.get('FAILED_RETRYABLE_REQUESTS')} retryable, "
                        f"{sf.get('FAILED_NONRETRYABLE_REQUESTS')} non-retryable completed-stage requests failed")
    real = bool((ex.get("raw_quote_rows") or 0) > 0 or ((h.get("quote_refresh_totals") or {}).get("receipts") or 0) > 0)
    board = board_status(st("boards"), qr, real)
    bx = ex.get("boards") if isinstance(ex.get("boards"), dict) else {}
    valid_rows = bx.get("rows_with_valid_market")
    market = None if board is None else ("VALID_MARKETS" if valid_rows else
                                         board_status("NONE", qr, real))      # why no market field is populated
    if fc and fc.get("status") not in (None, "FORECAST", "NO_GAMES"):
        warnings.append(f"FORECAST_{fc.get('status')}")
    if last and last.get("auth") not in (None, "PASS"):
        errors.append(f"ROTOWIRE_AUTH_{last.get('auth')}")
    if last.get("news_status") == "COVERAGE_GAP":
        warnings.append("ROTOWIRE_WEB_NEWS_COVERAGE_GAP")
    return {"DATE": h.get("et_date"), "RELEASE": h.get("code_sha"),
            "MODEL_VERSION": lf.get("model_version") or wf.get("ACTIVE_MODEL_VERSION"),
            "MORNING_REFIT_STATUS": wf.get("DAILY_REFIT_STATUS"), "MODEL_PROMOTION": wf.get("DAILY_MODEL_PROMOTION"),
            "FORECAST_STATUS": fc.get("status") or ("FORECAST" if lf else None),
            "FORECAST_WINDOW": lf.get("window"), "FORECAST_GAME_COUNT": lf.get("games_forecast"),
            "FORECAST_PLAYER_COUNT": lf.get("players_forecast"), "PMF_COUNT": lf.get("pmfs_persisted"),
            "ROTOWIRE_MODE": h.get("rotowire_mode"), "ROTOWIRE_AUTH": last.get("auth"),
            "ROTOWIRE_LAST_SUCCESS": (ok[-1].get("at") or ok[-1].get("acquisition_id")) if ok else None,
            "ROTOWIRE_NEWS_OVERLAP": last.get("news_continuity"),
            "QUOTE_REFRESH_STATUS": qr.get("status"),
            "REAL_QUOTES": real, "BOARD_STATUS": board, "BOARD_EXPORT_STATUS": st("boards"),
            "BOARD_ROWS": bx.get("rows"), "BOARD_ROWS_WITH_VALID_MARKET": valid_rows,
            "BOARD_MARKET_STATUS": market,
            "MODEL_FALLBACK_USED": bool(wf) and wf.get("DAILY_MODEL_PROMOTION") != "PROMOTED"
                                   and wf.get("DAILY_REFIT_STATUS") not in ("PASS", "NO_NEW_DATA"),
            "FALLBACK_REASON": (wf.get("note") or wf.get("reason") or wf.get("DAILY_REFIT_STATUS"))
                               if wf and wf.get("DAILY_REFIT_STATUS") not in ("PASS", "NO_NEW_DATA") else None,
            "DISTRIBUTIONS_STATUS": st("distributions"), "GRADES_STATUS": st("grades"),
            "IDENTITY_BLOCKED_GAMES": sum(1 for r in ((lf.get("games_not_scored") if lf else None)
                                                      or fc.get("games_not_scored") or {}).values()
                                          if r and "AVAILABILITY_IDENTITY_UNRESOLVED" in r),
            "WRITER_COUNT": 1 if os.environ.get("CBB_SCHEDULER_ROLE") == "PRIMARY_WRITER" else 0,
            "HEAVY_LOCK_STATUS": wf.get("lock_result"),
            "ERRORS": errors, "WARNINGS": warnings, "FINAL_STATUS": "FAIL" if errors else ("DEGRADED" if warnings else "OK")}


def _rotowire_mode() -> str:
    src = PC.rotowire_source()
    if src is not None:
        return f"SOURCE_{src}"
    return "HARD_DISABLED_NO_KEY" if not os.environ.get(ROTOWIRE_KEY_ENV) else "KEY_PRESENT"


def morning(life: Life, day: date | None = None, tick_ctx: SH.Ctx | None = None) -> dict:
    now = life.now()
    day = day or et_date(now)
    out = {"lifecycle": "MORNING", "day": day.isoformat(), "started_at": now.isoformat()}
    ctx = tick_ctx or SH.Ctx(ops_root=Path(life.ops_root), prod_root=Path(life.prod_root), history_db=life.history_db,
                             clock=life.clock, artifact_paths=life.artifact_paths, sims=life.sims)
    t = SH.tick(ctx)                                             # prior-night ingestion + settlement (idempotent)
    out["tick"] = {k: t.get(k) for k in ("tick_id", "status", "sync", "settlement", "did_work")}
    life.history_db = life.history_db or ctx.history_db
    out["prior_day_exports"] = _brief(exports_for(life, day - timedelta(days=1)))
    out["walk_forward"] = walk_forward(life, day, "MORNING_0700")
    out["forecast"] = forecast(life, INITIAL)
    out["exports"] = _brief(exports_for(life, day))
    health(life, day, morning_tick=out["tick"], prior_day_exports=out["prior_day_exports"],
           walk_forward=out["walk_forward"], forecast=out["forecast"], exports=out["exports"])
    return out


def web_acquire(life: Life) -> dict:
    """ROTOWIRE_SOURCE=WEB: one authenticated web acquisition (snapshot + injury news) into the existing event store,
    right before the refresh forecast. Provider failures are recorded with reason codes, never raised: the existing
    freshness gate then fails the affected games closed."""
    from cbb_platform.prod import rotowire_feed as RW, rotowire_web_acquire as RWW
    client = life.rotowire_web_client or RWW.WebClient()
    try:
        if life.history_db is None:
            life.history_db = HX.resolve()
        ctx = RW.load_identity_context(life.state_root, life.history_db)
    except Exception as e:                               # noqa: BLE001 -- no roster: nothing can be resolved
        return {"status": "BLOCKED", "reason": f"NO_IDENTITY_CONTEXT ({type(e).__name__})"}
    # the same event store / request ledger forecast() and prod.run.daily read (PC paths follow CBB_PROD_ROOT)
    rep = RWW.acquire(client, ctx, store=RW.EventStore(PC.ROTOWIRE_ROOT), root=PC.ROTOWIRE_WEB_ROOT,
                      ledger_path=PC.ROTOWIRE_REQUEST_LEDGER, map_root=PC.ROTOWIRE_MAP_ROOT)
    out = {k: rep.get(k) for k in ("acquisition_id", "auth", "news_status", "snapshot_status", "news_rows",
                                   "snapshot_rows", "news_new_items", "news_events_new", "snapshot_events_new",
                                   "news_continuity", "news_window_overlap", "reasons", "warnings")}
    out["at"] = life.now().isoformat()
    return out


def availability_poll(life: Life, window: str = ROTOWIRE_LIGHT_POLL_TIMES_ET[0]) -> dict:
    """Light RotoWire availability poll: ONE web acquisition into the event store and nothing else -- never a
    forecast, fit, simulation, PMF or export (the next refresh / official tick consumes the events). Records itself in
    the health file. Inert unless ROTOWIRE_SOURCE=WEB is ENABLED (session + PASS acceptance marker)."""
    if window not in ROTOWIRE_LIGHT_POLL_TIMES_ET:
        raise ValueError(f"light poll window {window!r} is not one of {ROTOWIRE_LIGHT_POLL_TIMES_ET}")
    st = rotowire_state(life)
    rec = {"lifecycle": "AVAILABILITY_POLL", "window": window, "rotowire_source": PC.rotowire_source(),
           "rotowire_refresh": st, "forecast": None}
    if PC.rotowire_source() == "WEB" and st == "ENABLED":
        rec["rotowire_web_acquisition"] = web_acquire(life)
    else:
        rec["note"] = "no availability source enabled: nothing requested"
    health(life, et_date(life.now()), **{f"availability_poll_{window.replace(':', '')}": rec})
    return rec


def refresh(life: Life, window: str) -> dict:
    acq = None
    if PC.rotowire_source() == "WEB" and rotowire_state(life) == "ENABLED":
        acq = web_acquire(life)
    rec = forecast(life, window)
    out = {"lifecycle": "REFRESH", "window": window, "forecast": rec, "exports": _brief(exports_for(life, et_date(life.now())))}
    sec = {"rotowire_refresh": rec.get("rotowire_refresh"), "status": rec.get("status"), "note": rec.get("note"),
           **({k: rec.get(k) for k in FORECAST_SUMMARY} if rec.get("status") == "FORECAST" else {})}
    if acq is not None:
        out["rotowire_web_acquisition"] = sec["rotowire_web_acquisition"] = acq
    health(life, et_date(life.now()), **{f"availability_{window.replace(':', '')}": sec}, exports=out["exports"])
    return out


def exports_now(life: Life) -> dict:
    """Post-tick export (quote refresh): reprices nothing and fits nothing; rebuilds today's workbooks from the
    persisted evidence so a new official tick's priced offers appear on the provisional Board and freeze at cutoff."""
    out = {"lifecycle": "EXPORTS", "exports": _brief(exports_for(life, et_date(life.now())))}
    health(life, et_date(life.now()), exports=out["exports"])
    return out


def quotes(life: Life, dry_run: bool = False) -> dict:
    """10-minute quote scheduler wake (prod.quote_refresh): request only due games (tiered by time to cutoff), reprice
    the latest persisted PMF, rebuild today's Boards (provisional; frozen at the cutoff). Never fits or simulates."""
    from cbb_platform.prod import quote_refresh as QR
    ctx = SH.Ctx(ops_root=Path(life.ops_root), prod_root=Path(life.prod_root), history_db=life.history_db or HX.resolve(),
                 clock=life.clock)
    life.history_db = ctx.history_db
    rep = QR.run(ctx, dry_run=dry_run)
    out = {"lifecycle": "QUOTES", "quote_refresh": rep}
    day = et_date(life.now())
    if not dry_run:
        out["exports"] = _brief(exports_for(life, day))
    h = OS.read_json(health_path(life, day), {}) or {}
    tot = h.get("quote_refresh_totals") or {"wakes": 0, "requests": 0, "credits": 0, "receipts": 0}
    tot = {"wakes": tot["wakes"] + 1, "requests": tot["requests"] + int(rep.get("requests", 0)),
           "credits": tot["credits"] + int(rep.get("credits", 0)), "receipts": tot["receipts"] + int(rep.get("receipts", 0) or 0)}
    health(life, day, quote_refresh={k: rep.get(k) for k in ("refresh_id", "now", "status", "reason", "due_games",
                                                             "requests", "credits", "receipts", "refits_triggered")},
           quote_refresh_totals=tot, **({"exports": out["exports"]} if "exports" in out else {}))
    return out


def _brief(rep: dict) -> dict:
    return {k: {kk: v.get(kk) for kk in ("status", "path", "rows", "missing", "rewritten", "rows_with_valid_market",
                                         "rows_with_no_valid_market", "unique_player_stat_keys", "duplicate_keys",
                                         "GRADE_STATUS") if kk in v} if isinstance(v, dict) else v
            for k, v in rep.items() if k in ("date", "boards", "distributions", "grades", "predicted_games",
                                             "raw_quote_rows", "official_rows", "official_frozen_rows")}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.lifecycle")
    ap.add_argument("--ops-root", type=Path, default=PC.OPS_ROOT)
    ap.add_argument("--prod-root", type=Path, default=PC.PROD_ROOT)
    ap.add_argument("--model-root", type=Path, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("morning")
    m.add_argument("--date", default=None)
    r = sub.add_parser("refresh")
    r.add_argument("--window", required=True, choices=ROTOWIRE_REFRESH_TIMES_ET)
    ap_ = sub.add_parser("availability-poll")
    ap_.add_argument("--window", required=True, choices=ROTOWIRE_LIGHT_POLL_TIMES_ET)
    f = sub.add_parser("forecast")
    f.add_argument("--window", default=INITIAL, choices=(INITIAL,) + ROTOWIRE_REFRESH_TIMES_ET)
    sub.add_parser("schedule")
    sub.add_parser("exports")
    qp = sub.add_parser("quotes")
    qp.add_argument("--dry-run", action="store_true")
    rr = sub.add_parser("refit-retry")
    rr.add_argument("--date", default=None)
    fw = sub.add_parser("features-warmup")
    fw.add_argument("--budget-min", type=float, default=None)
    a = ap.parse_args(argv)
    from cbb_platform import duckdb_limits as DLM
    DLM.install()                                  # DuckDB memory cap + spill dir when configured (no-op otherwise)
    life = Life(ops_root=a.ops_root, prod_root=a.prod_root, model_root=a.model_root)
    if a.cmd == "morning":
        out = morning(life, date.fromisoformat(a.date) if a.date else None)
    elif a.cmd == "refresh":
        out = refresh(life, a.window)
    elif a.cmd == "availability-poll":
        out = availability_poll(life, a.window)
    elif a.cmd == "forecast":
        out = forecast(life, a.window)
    elif a.cmd == "exports":
        out = exports_now(life)
    elif a.cmd == "quotes":
        out = quotes(life, dry_run=a.dry_run)
    elif a.cmd == "refit-retry":
        out = refit_retry(life, date.fromisoformat(a.date) if a.date else None)
    elif a.cmd == "features-warmup":
        out = features_warmup(life, a.budget_min)
    else:
        out = schedule_spec()
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
