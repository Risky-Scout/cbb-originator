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
ROTOWIRE_REFRESH_TIMES_ET = ("10:00", "13:00", "16:00", "18:00")       # exactly four; no other fixed refresh time
SCHEDULE_ET = ((MORNING_TIME_ET, "MORNING"),) + tuple((t, "ROTOWIRE_REFRESH") for t in ROTOWIRE_REFRESH_TIMES_ET)
INITIAL = "INITIAL"
# ---- shared cross-model heavy-job lock (operational only; nothing here reaches a forecast). Only the walk-forward step
# takes it. Not acquired within the wait -> DEFERRED_SHARED_LOCK: the last-known-good ACTIVE model stays, the rest of
# the morning still runs (the day is never left without forecasts), and ONE retry runs at REFIT_RETRY_TIME_ET.
REFIT_RETRY = "REFIT_RETRY"
REFIT_RETRY_TIME_ET = "08:00"
DEFERRED_SHARED_LOCK = "DEFERRED_SHARED_LOCK"
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
        "Distributions export", "Boards export (only when priced receipts exist)"]},
        "rotowire_refresh": {"at": list(ROTOWIRE_REFRESH_TIMES_ET), "steps": [
            "RotoWire availability refresh (BLOCKED_NO_KEY without ROTOWIRE_API_KEY)",
            "re-forecast only games with an availability change and a future cutoff", "PMFs persisted",
            "Boards / Distributions regenerated"]},
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
    if life.rotowire_enabled is not None:
        return "ENABLED" if life.rotowire_enabled else "DISABLED"
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


def wf_attempts_path(life: Life, day: date) -> Path:
    return Path(life.prod_root) / "walkforward_attempts" / f"{day.isoformat()}.jsonl"


def walk_forward(life: Life, day: date, attempt: str = "MORNING_0700", runner=None) -> dict:
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
        else:
            from cbb_platform.walkforward import config as WC, daily as WD
            run = runner or WD.morning
            try:
                wf = run(day, root=Path(life.model_root or WC.STORE_ROOT), state_root=life.state_root, now=life.now())
                rec.update({k: wf.get(k) for k in WF_KEYS})
            except Exception as e:                       # noqa: BLE001 -- promotion is atomic: ACTIVE is unchanged
                rec.update(DAILY_REFIT_STATUS="FAIL", DAILY_MODEL_PROMOTION="NOT_PROMOTED",
                           reason=f"{type(e).__name__}: {e}"[:400])
    finally:
        if fd is not None:
            os.close(fd)
    rec["finished_at"] = life.now().isoformat()
    OS.append_jsonl(wf_attempts_path(life, day), [rec])
    return rec


def refit_retry(life: Life, day: date | None = None, runner=None) -> dict:
    """08:00 ET: one retry, ONLY when the 07:00 attempt of the day was DEFERRED_SHARED_LOCK. A retry that promotes a
    new model re-forecasts the still-pre-cutoff games (new immutable snapshot; earlier snapshots stand)."""
    day = day or et_date(life.now())
    recs = OS.read_jsonl(wf_attempts_path(life, day))
    out = {"lifecycle": REFIT_RETRY, "day": day.isoformat()}
    if not recs or recs[-1].get("DAILY_REFIT_STATUS") != DEFERRED_SHARED_LOCK \
            or any(r.get("attempt") == "RETRY_0800" for r in recs):
        out.update(status="NO_OP", reason="the 07:00 walk-forward was not DEFERRED_SHARED_LOCK (or already retried)")
        health(life, day, refit_retry=out)
        return out
    wf = walk_forward(life, day, "RETRY_0800", runner=runner)
    out.update(status="RAN", walk_forward=wf)
    if wf.get("DAILY_MODEL_PROMOTION") == "PROMOTED":
        out["forecast"] = forecast(life, REFIT_RETRY)
        out["exports"] = _brief(exports_for(life, day))
    health(life, day, refit_retry=out)
    return out


# ============================================================================ daily health record (no secrets)
def health_path(life: Life, day: date) -> Path:
    return Path(life.prod_root) / "health" / f"{day.isoformat()}.json"


def health(life: Life, day: date, **sections) -> dict:
    """Merge this step's machine-readable summary into <prod_root>/health/<D>.json (one file per ET date)."""
    p = health_path(life, day)
    h = OS.read_json(p, {}) or {}
    h.update({"et_date": day.isoformat(), "code_sha": os.environ.get("CBB_CODE_COMMIT") or SH.source_commit(),
              "updated_at": life.now().isoformat(), "rotowire_mode": "HARD_DISABLED_NO_KEY"
              if not os.environ.get(ROTOWIRE_KEY_ENV) else "KEY_PRESENT",
              "automatic_wagering": "DISABLED", "wizardofodds_publication": "DISABLED"})
    for k, v in sections.items():
        h[k] = v
    fc = sections.get("forecast")
    if isinstance(fc, dict) and fc.get("status") == "FORECAST":
        h["latest_forecast"] = {k: fc.get(k) for k in ("snapshot_id", "window", "model_version", "games_forecast",
                                                        "players_forecast", "stat_predictions", "pmfs_persisted")}
    ex = sections.get("exports")
    if isinstance(ex, dict):
        h["export_paths"] = {k: (v or {}).get("path") for k, v in ex.items() if isinstance(v, dict)}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(h, indent=1, default=str))
    os.replace(tmp, p)
    return h


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


def refresh(life: Life, window: str) -> dict:
    rec = forecast(life, window)
    out = {"lifecycle": "REFRESH", "window": window, "forecast": rec, "exports": _brief(exports_for(life, et_date(life.now())))}
    health(life, et_date(life.now()), **{f"availability_{window.replace(':', '')}": {
        "rotowire_refresh": rec.get("rotowire_refresh"), "status": rec.get("status"), "note": rec.get("note")}},
        exports=out["exports"])
    return out


def exports_now(life: Life) -> dict:
    """Post-tick export (quote refresh): reprices nothing and fits nothing; rebuilds today's workbooks from the
    persisted evidence so a new official tick's priced offers appear on the provisional Board and freeze at cutoff."""
    out = {"lifecycle": "EXPORTS", "exports": _brief(exports_for(life, et_date(life.now())))}
    health(life, et_date(life.now()), exports=out["exports"])
    return out


def _brief(rep: dict) -> dict:
    return {k: {kk: v.get(kk) for kk in ("status", "path", "rows", "missing")} if isinstance(v, dict) else v
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
    f = sub.add_parser("forecast")
    f.add_argument("--window", default=INITIAL, choices=(INITIAL,) + ROTOWIRE_REFRESH_TIMES_ET)
    sub.add_parser("schedule")
    sub.add_parser("exports")
    rr = sub.add_parser("refit-retry")
    rr.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    life = Life(ops_root=a.ops_root, prod_root=a.prod_root, model_root=a.model_root)
    if a.cmd == "morning":
        out = morning(life, date.fromisoformat(a.date) if a.date else None)
    elif a.cmd == "refresh":
        out = refresh(life, a.window)
    elif a.cmd == "forecast":
        out = forecast(life, a.window)
    elif a.cmd == "exports":
        out = exports_now(life)
    elif a.cmd == "refit-retry":
        out = refit_retry(life, date.fromisoformat(a.date) if a.date else None)
    else:
        out = schedule_spec()
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
