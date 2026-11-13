"""THE 07:00 ET morning lifecycle as five resumable, memory-isolated stages (one systemd oneshot + cgroup per stage).

2026-11-13: the monolithic morning process trained + promoted cbb-jm1-2026-27-20261113 at 12:40Z (walk-forward peak
VmHWM 3076 MB), then ran the INITIAL forecast in the SAME process and was OOM-killed by the 3200M cgroup at 13:08Z. The
08:00 retry looked only at the walk-forward deferral flag and returned NO_OP. Fix: every stage runs in its own process and
its own systemd service / cgroup (deploy/droplet/systemd/cbb-originator-morning-*.service, chained by
cbb-originator-morning.target with Requires= + After=), so the kernel reclaims each stage's memory before the next one
starts, and every stage writes a durable receipt the recovery reads:

    PREP              prior-night shadow tick (CBBD sync + settlement) + prior-day exports (Grades D-1)
    WALK_FORWARD      lifecycle.walk_forward (unchanged: PIT legality, feature catch-up, fit 1, fit 2, deterministic
                      repeat gate, hard gates, atomic promotion / incumbent retention); the ONLY stage that takes the
                      shared heavy lock. Same-day promoted version on disk -> PASS reconstructed, never refit.
    INITIAL_FORECAST  lifecycle.forecast(INITIAL) on the ACTIVE model (unchanged scorer, PMFs persisted)
    EXPORTS           lifecycle.exports_for(D) from persisted evidence (no fit, no simulation, no network)
    HEALTH            aggregates the stage receipts into prod/health/<D>.json

Durable state: <ops_root>/morning/<D>/progress.json (atomic replace under a progress flock); every invocation is also
appended to <ops_root>/morning/<D>/stage_log.jsonl. Stage statuses: NOT_STARTED | RUNNING | PASS | DEFERRED | FAIL.

08:00 ET recovery (cbb-originator-morning-recover.service, ordered After= every morning stage, so it never overlaps the
07:00 chain): evaluates the FULL state machine -- complete -> ALREADY_COMPLETE (no work); otherwise it records the first
incomplete stage and requests the (idempotent) chain again, which skips every PASS stage. The training cutoff of a
retry is always WC.refit_cutoff(D) (D 06:00 ET): the walk-forward reads the state as-of that cutoff, never later data.

Nothing here changes a model, a fit, a gate, a feature, a PMF, RotoWire, quotes or exports; it only sequences them.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import resource
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from cbb_platform.prod import ops_store as OS

STAGES = ("PREP", "WALK_FORWARD", "INITIAL_FORECAST", "EXPORTS", "HEALTH")
CLI_STAGES = {"prep": "PREP", "walkforward": "WALK_FORWARD", "forecast": "INITIAL_FORECAST", "exports": "EXPORTS",
              "health": "HEALTH"}
NOT_STARTED, RUNNING, PASS, DEFERRED, FAIL = "NOT_STARTED", "RUNNING", "PASS", "DEFERRED", "FAIL"
STATUSES = (NOT_STARTED, RUNNING, PASS, DEFERRED, FAIL)
MAX_ATTEMPTS = 2                         # per stage per ET date: the 07:00 attempt + one recovery attempt
MAX_RECOVERY_PASSES = 2                  # chain passes one 08:00 recovery may request (bounded; never a loop)
# orchestration failures (directive section 15): they never imply MODEL_FAIL / TRAINING_DATA_FAIL -- the durable
# evidence decides what completed
ORCHESTRATION_ERRORS = ("OOM_KILL", "SIGKILL", "SIGTERM", "TIMEOUT", "UNEXPECTED_CHILD_EXIT", "INTERRUPTED", "EXCEPTION")
MODEL_GATE_FAIL = "MODEL_GATE_FAIL"
RESUME_REQUEST = "RESUME_REQUEST"        # <ops_root>/morning/RESUME_REQUEST: the root ExecStartPost re-starts the chain
STAGE_FIELDS = {
    "PREP": (),
    "WALK_FORWARD": ("RUN_ID", "DAILY_REFIT_STATUS", "DAILY_MODEL_PROMOTION", "ACTIVE_MODEL_VERSION"),
    "INITIAL_FORECAST": ("SNAPSHOT_ID", "MODEL_VERSION", "GAME_COUNT", "PLAYER_COUNT", "PMF_COUNT"),
    "EXPORTS": ("BOARDS_STATUS", "DISTRIBUTIONS_STATUS", "GRADES_STATUS"),
    "HEALTH": (),
}
TELEMETRY = ("PID", "START_RSS_MB", "PEAK_RSS_MB", "END_RSS_MB", "SYSTEMD_MEMORY_PEAK_BYTES")
MEMORY_HIGH_MB, MEMORY_MAX_MB = 2800, 3200


# ============================================================================ telemetry (process + cgroup)
def _proc_mb() -> dict:
    out = {}
    try:
        for line in open("/proc/self/status"):
            k, v = line.split(":", 1)
            if k in ("VmRSS", "VmHWM"):
                out[k] = round(int(v.split()[0]) / 1024, 1)
    except OSError:                                          # macOS (tests): peak only
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out["VmHWM"] = round((r / 1e6) if sys.platform == "darwin" else (r / 1024), 1)
    return out


def cgroup_dir() -> Path | None:
    """This process's cgroup v2 directory (the stage's systemd service cgroup on the Droplet), else None."""
    try:
        for line in open("/proc/self/cgroup"):
            if line.startswith("0::"):
                d = Path("/sys/fs/cgroup") / line.strip()[3:].lstrip("/")
                return d if d.is_dir() else None
    except OSError:
        return None
    return None


def cgroup_bytes(name: str) -> int | None:
    d = cgroup_dir()
    try:
        return int((d / name).read_text().split()[0]) if d is not None else None
    except (OSError, ValueError):
        return None


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    return True


# ============================================================================ durable progress (atomic, flocked)
def day_dir(ops_root: Path, day: date) -> Path:
    return Path(ops_root) / "morning" / day.isoformat()


def progress_path(ops_root: Path, day: date) -> Path:
    return day_dir(ops_root, day) / "progress.json"


def resume_request_path(ops_root: Path) -> Path:
    return Path(ops_root) / "morning" / RESUME_REQUEST


def _now_iso() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def _blank_stage(stage: str) -> dict:
    b = {"STATUS": NOT_STARTED, "STARTED_AT": None, "FINISHED_AT": None, "EXIT_CODE": None, "ERROR_TYPE": None,
         "ERROR_DETAIL": None, "ATTEMPTS": 0}
    b.update({k: None for k in STAGE_FIELDS[stage]})
    if stage != "HEALTH":
        b.update({k: None for k in TELEMETRY})
    return b


def new_progress(day: date) -> dict:
    from cbb_platform.walkforward import config as WC
    now = _now_iso()
    p = {"DATE": day.isoformat(), "CODE_COMMIT": os.environ.get("CBB_CODE_COMMIT"),
         "TRAINING_CUTOFF_UTC": WC.refit_cutoff(day).isoformat(), "CREATED_AT": now, "UPDATED_AT": now}
    for s in STAGES:
        p[s] = _blank_stage(s)
    p.update(MORNING_COMPLETE=False, RECOVERY_REQUIRED=False, RECOVERY_LAST_ACTION=None, RECOVERY={"ACTIVE": False,
             "PASSES_REQUESTED": 0, "HISTORY": []})
    return p


def load(ops_root: Path, day: date) -> dict:
    p = OS.read_json(progress_path(ops_root, day), None)
    if not p:
        return new_progress(day)
    for s in STAGES:                                         # forward-compatible: missing keys get their blank value
        p[s] = {**_blank_stage(s), **(p.get(s) or {})}
    p.setdefault("RECOVERY", {"ACTIVE": False, "PASSES_REQUESTED": 0, "HISTORY": []})
    return p


@contextlib.contextmanager
def locked(ops_root: Path, day: date):
    """Exclusive read-modify-write of progress.json: yields the dict; on exit derives the summary flags and writes it
    by atomic replace (tmp + fsync + os.replace). The progress flock is separate from the CBB writer lock."""
    d = day_dir(ops_root, day)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "progress.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        p = load(ops_root, day)
        yield p
        derive(p)
        p["UPDATED_AT"] = _now_iso()
        tmp = d / f".progress.json.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(p, indent=1, default=str))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, progress_path(ops_root, day))
        try:
            fd = os.open(d, os.O_RDONLY)
            os.fsync(fd)
            os.close(fd)
        except OSError:
            pass


def log_event(ops_root: Path, day: date, rec: dict) -> None:
    OS.append_jsonl(day_dir(ops_root, day) / "stage_log.jsonl", [{"at": _now_iso(), "pid": os.getpid(), **rec}])


# ============================================================================ the state machine (pure)
def wf_settled(wf: dict) -> bool:
    """The walk-forward reached a usable model decision that needs no further attempt today."""
    return wf["STATUS"] == PASS or (wf["STATUS"] in (FAIL, DEFERRED) and not wf.get("RETRYABLE"))


def wf_has_decision(wf: dict) -> bool:
    """A model the forecast may use exists: the new ACTIVE, or the incumbent explicitly retained."""
    return wf["STATUS"] == PASS or wf.get("MODEL_DECISION") in ("PROMOTED", "INCUMBENT_RETAINED")


def stage_settled(st: dict) -> bool:
    return st["STATUS"] == PASS or (st["STATUS"] == FAIL and not st.get("RETRYABLE")) or bool(st.get("RECONCILED_TERMINAL"))


def first_incomplete(p: dict) -> str | None:
    """The first stage that still has (recoverable) work, in chain order; None when nothing remains."""
    if p["PREP"]["STATUS"] != PASS and not stage_settled(p["PREP"]):
        return "PREP"
    if not wf_settled(p["WALK_FORWARD"]):
        return "WALK_FORWARD"
    fc = p["INITIAL_FORECAST"]
    if not stage_settled(fc) or forecast_stale(p):
        return "INITIAL_FORECAST"
    if p["EXPORTS"]["STATUS"] != PASS and not stage_settled(p["EXPORTS"]):
        return "EXPORTS"
    if p["EXPORTS"]["STATUS"] == PASS and fc.get("REFIT_RETRY_SNAPSHOT_ID") \
            and p["EXPORTS"].get("EVIDENCE_KEY") != fc.get("REFIT_RETRY_SNAPSHOT_ID") and not fc.get("RECONCILED_TERMINAL"):
        return "EXPORTS"
    if p["HEALTH"]["STATUS"] != PASS or p["HEALTH"].get("AGGREGATED_UPDATED_AT") != _upstream_key(p):
        return "HEALTH"
    return None


def forecast_stale(p: dict) -> bool:
    """A retry promoted a new model after the INITIAL forecast was made with the incumbent: one REFIT_RETRY forecast."""
    wf, fc = p["WALK_FORWARD"], p["INITIAL_FORECAST"]
    return (fc["STATUS"] == PASS and wf["STATUS"] == PASS and wf.get("DAILY_MODEL_PROMOTION") == "PROMOTED"
            and wf.get("ACTIVE_MODEL_VERSION") not in (None, fc.get("MODEL_VERSION"), fc.get("REFIT_RETRY_MODEL_VERSION")))


def _upstream_key(p: dict) -> str:
    return "|".join(f"{s}:{p[s]['STATUS']}:{p[s].get('FINISHED_AT')}" for s in STAGES[:-1])


def complete(p: dict) -> bool:
    """Every stage PASS -- except a walk-forward that settled on the incumbent (gate FAIL / non-retryable deferral /
    retries exhausted), which completes the lifecycle with MODEL_FALLBACK_USED."""
    return (p["PREP"]["STATUS"] == PASS and wf_settled(p["WALK_FORWARD"]) and p["INITIAL_FORECAST"]["STATUS"] == PASS
            and not forecast_stale(p) and p["EXPORTS"]["STATUS"] == PASS and first_incomplete(p) is None)


def derive(p: dict) -> None:
    p["MORNING_COMPLETE"] = complete(p)
    nxt = first_incomplete(p)
    p["FIRST_INCOMPLETE_STAGE"] = nxt
    p["RECOVERY_REQUIRED"] = (not p["MORNING_COMPLETE"]) and nxt is not None and nxt != "HEALTH"


# ============================================================================ stage scaffolding
def _begin(p: dict, stage: str, prev_stage: str | None) -> None:
    st = p[stage]
    m = _proc_mb()
    prev = p.get(prev_stage) or {} if prev_stage else {}
    st.update(STATUS=RUNNING, STARTED_AT=_now_iso(), FINISHED_AT=None, EXIT_CODE=None, ERROR_TYPE=None, ERROR_DETAIL=None,
              ATTEMPTS=int(st.get("ATTEMPTS") or 0) + 1, PID=os.getpid(), START_RSS_MB=m.get("VmRSS", m.get("VmHWM")),
              INVOCATION_ID=os.environ.get("INVOCATION_ID"), CGROUP=str(cgroup_dir() or "") or None,
              SYSTEMD_MEMORY_CURRENT_AT_START_BYTES=cgroup_bytes("memory.current"))
    if prev_stage and prev.get("PID"):
        st["PREV_STAGE"] = prev_stage
        st["PREV_STAGE_PID"] = prev["PID"]
        st["PREV_STAGE_PID_EXITED"] = (int(prev["PID"]) != os.getpid()) and not pid_alive(prev["PID"])


def _end(p: dict, stage: str, status: str, exit_code: int, **fields) -> None:
    assert status in STATUSES
    st = p[stage]
    m = _proc_mb()
    st.update(STATUS=status, FINISHED_AT=_now_iso(), EXIT_CODE=exit_code, END_RSS_MB=m.get("VmRSS"),
              PEAK_RSS_MB=m.get("VmHWM"), SYSTEMD_MEMORY_PEAK_BYTES=cgroup_bytes("memory.peak"))
    st.update(fields)


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:400]


def _skip(ops_root, day, stage, action, **kw) -> tuple[int, dict]:
    rec = {"stage": stage, "action": action, **kw}
    log_event(ops_root, day, rec)
    return 0, rec


# ============================================================================ model evidence (same-day promotion)
def model_evidence(model_root: Path, day: date) -> dict:
    """Day D's same-day promotion proof from the model store (walkforward.store.same_day_promotion)."""
    from cbb_platform.walkforward import store as SO
    return SO.same_day_promotion(Path(model_root), day)


def _apply_evidence(wf: dict, ev: dict, how: str) -> None:
    wf.update(STATUS=PASS, RUN_ID=ev["RUN_ID"], DAILY_REFIT_STATUS="PASS", DAILY_MODEL_PROMOTION="PROMOTED",
              ACTIVE_MODEL_VERSION=ev["ACTIVE_MODEL_VERSION"], MODEL_DECISION="PROMOTED", RETRYABLE=False,
              PROMOTED_AT=ev["PROMOTED_AT"], RECOVERED_FROM=how, REFIT_SKIPPED=True,
              MODEL_EVIDENCE={k: ev.get(k) for k in ("MODEL_VERSION", "TRAINING_CUTOFF_UTC", "PRODUCTION_MANIFEST_HASH",
                                                    "STATE_VERSION", "DETERMINISTIC_REFIT_BIT_IDENTICAL", "RUN_RECORD")})
    if not wf.get("FINISHED_AT"):
        wf["FINISHED_AT"] = _now_iso()


def _model_root(life) -> Path:
    from cbb_platform.walkforward import config as WC
    return Path(life.model_root or WC.STORE_ROOT)


# ============================================================================ the five stages
def run_prep(life, day: date) -> tuple[int, dict]:
    from cbb_platform.prod import lifecycle as L, shadow_tick as SH
    with locked(life.ops_root, day) as p:
        if p["PREP"]["STATUS"] == PASS:
            return _skip(life.ops_root, day, "PREP", "SKIP_ALREADY_PASS")
        if stage_settled(p["PREP"]) and p["PREP"]["STATUS"] == FAIL:
            return _skip(life.ops_root, day, "PREP", "SKIP_ATTEMPTS_EXHAUSTED")
        _begin(p, "PREP", None)
    log_event(life.ops_root, day, {"stage": "PREP", "action": "RUN"})
    try:
        ctx = SH.Ctx(ops_root=Path(life.ops_root), prod_root=Path(life.prod_root), history_db=life.history_db,
                     clock=life.clock, artifact_paths=life.artifact_paths, sims=life.sims)
        t = SH.tick(ctx)                                         # prior-night ingestion + settlement (idempotent)
        life.history_db = life.history_db or ctx.history_db
        prior = L._brief(L.exports_for(life, day - timedelta(days=1)))
        tick = {k: t.get(k) for k in ("tick_id", "status", "sync", "settlement", "did_work")}
    except Exception as e:                                       # noqa: BLE001
        with locked(life.ops_root, day) as p:
            _end(p, "PREP", FAIL, 1, ERROR_TYPE="EXCEPTION", ERROR_DETAIL=_err(e),
                 RETRYABLE=p["PREP"]["ATTEMPTS"] < MAX_ATTEMPTS)
        return 1, {"stage": "PREP", "status": FAIL, "error": _err(e)}
    with locked(life.ops_root, day) as p:
        _end(p, "PREP", PASS, 0, TICK=tick, PRIOR_DAY_EXPORTS=prior, RETRYABLE=False,
             PRIOR_DAY_GRADES_STATUS=(prior.get("grades") or {}).get("status") if isinstance(prior.get("grades"), dict) else None)
    return 0, {"stage": "PREP", "status": PASS, "tick": tick}


def classify_walk_forward(rec: dict, attempts: int) -> tuple[str, int, dict]:
    """lifecycle.walk_forward record -> (stage status, exit code, fields). Exit 0 = a usable model decision exists
    (new ACTIVE or the retained incumbent): the forecast may run. Exit 1 = a retryable failure before a decision."""
    from cbb_platform.prod import lifecycle as L
    s, prom = rec.get("DAILY_REFIT_STATUS"), rec.get("DAILY_MODEL_PROMOTION")
    can_retry = attempts < MAX_ATTEMPTS
    f = {"RUN_ID": rec.get("run_id"), "DAILY_REFIT_STATUS": s, "DAILY_MODEL_PROMOTION": prom,
         "ACTIVE_MODEL_VERSION": rec.get("ACTIVE_MODEL_VERSION"), "ATTEMPT_RECORD": _wf_brief(rec)}
    if s == "PASS" or s == "NO_NEW_DATA":
        return PASS, 0, {**f, "MODEL_DECISION": "PROMOTED" if prom in ("PROMOTED", "IDEMPOTENT_NO_CHANGE") and s == "PASS"
                         else "INCUMBENT_RETAINED", "RETRYABLE": False}
    if s in L.RETRYABLE_DEFERRALS:                               # shared lock busy / feature backlog: incumbent today
        return DEFERRED, 0, {**f, "MODEL_DECISION": "INCUMBENT_RETAINED", "RETRYABLE": can_retry, "ERROR_TYPE": s,
                             "ERROR_DETAIL": rec.get("note") or rec.get("reason")}
    if s == L.DEFERRED_STATE_REFRESH_FAILED:                     # no later sync can change the as-of state: final
        return DEFERRED, 0, {**f, "MODEL_DECISION": "INCUMBENT_RETAINED", "RETRYABLE": False, "ERROR_TYPE": s,
                             "ERROR_DETAIL": rec.get("reason")}
    if s == "FAIL" and prom == "REFUSED":                        # the hard gates refused the candidate: deterministic
        return FAIL, 0, {**f, "MODEL_DECISION": "INCUMBENT_RETAINED", "RETRYABLE": False, "ERROR_TYPE": MODEL_GATE_FAIL,
                         "ERROR_DETAIL": rec.get("reason") or json.dumps((rec.get("gates") or {}).get("failed"), default=str)[:400]}
    reason = str(rec.get("reason") or s)
    etype = "RESOURCE" if reason.startswith("RESOURCE") else "EXCEPTION"
    if can_retry and etype == "EXCEPTION":                       # orchestration / code failure, no decision yet
        return FAIL, 1, {**f, "MODEL_DECISION": None, "RETRYABLE": True, "ERROR_TYPE": etype, "ERROR_DETAIL": reason[:400]}
    return FAIL, 0, {**f, "MODEL_DECISION": "INCUMBENT_RETAINED", "RETRYABLE": can_retry, "ERROR_TYPE": etype,
                     "ERROR_DETAIL": reason[:400]}


def _wf_brief(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("attempt", "day", "started_at", "finished_at", "DAILY_REFIT_STATUS",
                                    "DAILY_MODEL_PROMOTION", "ACTIVE_MODEL_VERSION", "CANDIDATE_MODEL_VERSION", "run_id",
                                    "note", "reason", "lock_result", "lock_wait_s", "heavy_lock", "state_freshness",
                                    "prior_day_completeness", "feature_catchup", "memory_mb", "failed_stage",
                                    "mem_available_mb_at_start") if rec.get(k) is not None}


def run_walkforward(life, day: date, runner=None, warm_runner=None) -> tuple[int, dict]:
    """The authorized walk-forward, at most MAX_ATTEMPTS times per ET date, and never again once a same-day version was
    gated + promoted (durable model evidence, checked BEFORE the heavy lock is taken and before any ingestion)."""
    from cbb_platform.prod import lifecycle as L
    with locked(life.ops_root, day) as p:
        wf = p["WALK_FORWARD"]
        if wf["STATUS"] == PASS:
            return _skip(life.ops_root, day, "WALK_FORWARD", "SKIP_ALREADY_PASS", model=wf.get("ACTIVE_MODEL_VERSION"))
        ev = model_evidence(_model_root(life), day)
        if ev["VALID"]:                                          # promoted before its receipt was written (or killed)
            _apply_evidence(wf, ev, "MODEL_STORE_EVIDENCE")
            rec = {"stage": "WALK_FORWARD", "action": "RECONSTRUCTED_PASS_FROM_MODEL_EVIDENCE_NO_REFIT",
                   "model": ev["MODEL_VERSION"], "run_id": ev["RUN_ID"]}
            log_event(life.ops_root, day, rec)
            return 0, rec
        if wf_settled(wf):
            return _skip(life.ops_root, day, "WALK_FORWARD", f"SKIP_SETTLED_{wf['STATUS']}", error=wf.get("ERROR_TYPE"))
        if int(wf.get("ATTEMPTS") or 0) >= MAX_ATTEMPTS:         # retries exhausted: the incumbent stays ACTIVE
            wf.update(RETRYABLE=False, MODEL_DECISION="INCUMBENT_RETAINED",
                      ERROR_DETAIL=f"{wf.get('ERROR_DETAIL') or ''} [retries exhausted after {wf['ATTEMPTS']}]"[:400])
            return _skip(life.ops_root, day, "WALK_FORWARD", "SKIP_ATTEMPTS_EXHAUSTED_INCUMBENT_RETAINED")
        _begin(p, "WALK_FORWARD", "PREP")
        attempts = p["WALK_FORWARD"]["ATTEMPTS"]
    label = "MORNING_0700" if attempts == 1 else f"RECOVERY_{attempts}"
    log_event(life.ops_root, day, {"stage": "WALK_FORWARD", "action": "RUN", "attempt": label})
    rec = L.walk_forward(life, day, label, runner=runner, warm_runner=warm_runner)   # training cutoff = refit_cutoff(D)
    status, code, fields = classify_walk_forward(rec, attempts)
    with locked(life.ops_root, day) as p:                        # the PASS receipt is durable BEFORE this process exits
        _end(p, "WALK_FORWARD", status, code, **fields)
    return code, {"stage": "WALK_FORWARD", "status": status, **{k: fields.get(k) for k in STAGE_FIELDS["WALK_FORWARD"]}}


def run_forecast(life, day: date) -> tuple[int, dict]:
    from cbb_platform.prod import lifecycle as L
    with locked(life.ops_root, day) as p:
        wf, fc = p["WALK_FORWARD"], p["INITIAL_FORECAST"]
        if not wf_has_decision(wf):
            rec = {"stage": "INITIAL_FORECAST", "action": "BLOCKED_NO_MODEL_DECISION", "walk_forward": wf["STATUS"]}
            log_event(life.ops_root, day, rec)
            return 1, rec
        if fc.get("RECONCILED_TERMINAL"):
            return _skip(life.ops_root, day, "INITIAL_FORECAST", "SKIP_RECONCILED_TERMINAL", error=fc.get("ERROR_TYPE"))
        window = L.INITIAL
        if fc["STATUS"] == PASS:
            if not forecast_stale(p):
                return _skip(life.ops_root, day, "INITIAL_FORECAST", "SKIP_ALREADY_PASS", snapshot=fc.get("SNAPSHOT_ID"))
            window = L.REFIT_RETRY                               # a retry promoted after the incumbent INITIAL forecast
        elif fc["STATUS"] == FAIL and not fc.get("RETRYABLE"):
            return _skip(life.ops_root, day, "INITIAL_FORECAST", "SKIP_ATTEMPTS_EXHAUSTED")
        if window == L.INITIAL:
            _begin(p, "INITIAL_FORECAST", "WALK_FORWARD")
    log_event(life.ops_root, day, {"stage": "INITIAL_FORECAST", "action": "RUN", "window": window})
    try:
        rec = L.forecast(life, window)
    except Exception as e:                                       # noqa: BLE001
        rec = {"status": "EXCEPTION", "reason": _err(e)}
    ok = rec.get("status") in ("FORECAST", "NO_FORECAST_DUE")
    brief = {k: rec.get(k) for k in L.FORECAST_SUMMARY + ("status", "reason", "executed_at", "games")}
    with locked(life.ops_root, day) as p:
        if window == L.REFIT_RETRY:
            if ok:
                p["INITIAL_FORECAST"].update(REFIT_RETRY_SNAPSHOT_ID=rec.get("snapshot_id"),
                                             REFIT_RETRY_MODEL_VERSION=rec.get("model_version") or p["WALK_FORWARD"]["ACTIVE_MODEL_VERSION"],
                                             REFIT_RETRY_RECORD=brief)
            else:
                p["INITIAL_FORECAST"].update(REFIT_RETRY_MODEL_VERSION=p["WALK_FORWARD"]["ACTIVE_MODEL_VERSION"],
                                             REFIT_RETRY_RECORD=brief)       # one attempt only; INITIAL stands
        elif ok:
            _end(p, "INITIAL_FORECAST", PASS, 0, SNAPSHOT_ID=rec.get("snapshot_id"),
                 MODEL_VERSION=rec.get("model_version"), GAME_COUNT=rec.get("games_forecast", 0),
                 PLAYER_COUNT=rec.get("players_forecast", 0), PMF_COUNT=rec.get("pmfs_persisted", 0), RETRYABLE=False,
                 FORECAST_STATUS=rec.get("status"), RECORD=brief)
        else:
            _end(p, "INITIAL_FORECAST", FAIL, 1, ERROR_TYPE="EXCEPTION" if rec.get("status") == "EXCEPTION" else
                 f"FORECAST_{rec.get('status')}", ERROR_DETAIL=str(rec.get("reason"))[:400],
                 RETRYABLE=p["INITIAL_FORECAST"]["ATTEMPTS"] < MAX_ATTEMPTS, RECORD=brief)
    return (0 if ok else 1), {"stage": "INITIAL_FORECAST", "window": window, "status": rec.get("status"),
                              "snapshot_id": rec.get("snapshot_id")}


def exports_evidence_key(p: dict) -> str | None:
    fc = p["INITIAL_FORECAST"]
    return fc.get("REFIT_RETRY_SNAPSHOT_ID") or fc.get("SNAPSHOT_ID")


def _export_ok(ex: dict) -> tuple[bool, str | None]:
    bad = [f"{k}={(ex.get(k) or {}).get('status')}" for k in ("boards", "distributions", "grades")
           if isinstance(ex.get(k), dict) and str((ex.get(k) or {}).get("status") or "").startswith(("ABORTED", "FAIL", "ERROR"))]
    return (not bad), ("; ".join(bad) or None)


def run_exports(life, day: date) -> tuple[int, dict]:
    from cbb_platform.prod import lifecycle as L
    with locked(life.ops_root, day) as p:
        ex = p["EXPORTS"]
        key = exports_evidence_key(p)
        if ex["STATUS"] == PASS and ex.get("EVIDENCE_KEY") == key:
            return _skip(life.ops_root, day, "EXPORTS", "SKIP_ALREADY_PASS_EVIDENCE_UNCHANGED", evidence=key)
        fc = p["INITIAL_FORECAST"]
        if fc["STATUS"] != PASS and not fc.get("RECONCILED_TERMINAL") and not stage_settled(fc):
            rec = {"stage": "EXPORTS", "action": "BLOCKED_FORECAST_INCOMPLETE", "forecast": fc["STATUS"]}
            log_event(life.ops_root, day, rec)
            return 1, rec
        if ex["STATUS"] == FAIL and not ex.get("RETRYABLE") and ex.get("EVIDENCE_KEY") == key:
            return _skip(life.ops_root, day, "EXPORTS", "SKIP_ATTEMPTS_EXHAUSTED")
        if ex["STATUS"] == PASS:
            ex["ATTEMPTS"] = 0                                   # new evidence (retry forecast): a fresh export budget
        _begin(p, "EXPORTS", "INITIAL_FORECAST")
    log_event(life.ops_root, day, {"stage": "EXPORTS", "action": "RUN", "evidence": key})
    try:
        out = L._brief(L.exports_for(life, day))
        ok, why = _export_ok(out)
    except Exception as e:                                       # noqa: BLE001
        out, ok, why = {}, False, _err(e)
    st = lambda k: (out.get(k) or {}).get("status") if isinstance(out.get(k), dict) else None
    with locked(life.ops_root, day) as p:
        _end(p, "EXPORTS", PASS if ok else FAIL, 0 if ok else 1, BOARDS_STATUS=st("boards"),
             DISTRIBUTIONS_STATUS=st("distributions"), GRADES_STATUS=st("grades"), EVIDENCE_KEY=key, DETAIL=out,
             RETRYABLE=(not ok) and p["EXPORTS"]["ATTEMPTS"] < MAX_ATTEMPTS,
             **({} if ok else {"ERROR_TYPE": "EXPORT_FAILED", "ERROR_DETAIL": why}))
    return (0 if ok else 1), {"stage": "EXPORTS", "status": PASS if ok else FAIL}


def summary(p: dict) -> dict:
    """The stage-aware morning section of the daily health record (no secrets; pure projection of progress.json)."""
    wf, fc, ex = p["WALK_FORWARD"], p["INITIAL_FORECAST"], p["EXPORTS"]
    mem = {s: {k: p[s].get(k) for k in TELEMETRY + ("PREV_STAGE_PID_EXITED", "SERVICE_RESULT")} for s in STAGES[:-1]}
    return {"DATE": p["DATE"], "TRAINING_CUTOFF_UTC": p["TRAINING_CUTOFF_UTC"], "CODE_COMMIT": p.get("CODE_COMMIT"),
            "MORNING_PREP_STATUS": p["PREP"]["STATUS"], "MORNING_WALK_FORWARD_STATUS": wf["STATUS"],
            "MORNING_REFIT_STATUS": wf.get("DAILY_REFIT_STATUS"), "MODEL_PROMOTION": wf.get("DAILY_MODEL_PROMOTION"),
            "MORNING_ACTIVE_MODEL": wf.get("ACTIVE_MODEL_VERSION"), "MORNING_RUN_ID": wf.get("RUN_ID"),
            "MORNING_MODEL_DECISION": wf.get("MODEL_DECISION"), "MORNING_WALK_FORWARD_ERROR": wf.get("ERROR_TYPE"),
            "MORNING_INITIAL_FORECAST_STATUS": fc["STATUS"], "MORNING_INITIAL_FORECAST_ERROR": fc.get("ERROR_TYPE"),
            "MORNING_INITIAL_FORECAST_HISTORICAL_STATUS": fc.get("HISTORICAL_STATUS"),
            "MORNING_INITIAL_SNAPSHOT_ID": fc.get("SNAPSHOT_ID"), "MORNING_EXPORTS_STATUS": ex["STATUS"],
            "MORNING_HEALTH_STATUS": p["HEALTH"]["STATUS"], "MORNING_COMPLETE": p["MORNING_COMPLETE"],
            "FIRST_INCOMPLETE_STAGE": p.get("FIRST_INCOMPLETE_STAGE"), "RECOVERY_REQUIRED": p["RECOVERY_REQUIRED"],
            "RECOVERY_LAST_ACTION": p.get("RECOVERY_LAST_ACTION"), "STAGE_MEMORY": mem}


def _health_sections(p: dict) -> dict:
    wf, fc, ex = p["WALK_FORWARD"], p["INITIAL_FORECAST"], p["EXPORTS"]
    out = {}
    if wf["STATUS"] not in (NOT_STARTED,):
        out["walk_forward"] = {**(wf.get("ATTEMPT_RECORD") or {}), **{k: wf.get(k) for k in (
            "DAILY_REFIT_STATUS", "DAILY_MODEL_PROMOTION", "ACTIVE_MODEL_VERSION") if wf.get(k) is not None},
            "STAGE_STATUS": wf["STATUS"], "RUN_ID": wf.get("RUN_ID"), "RECOVERED_FROM": wf.get("RECOVERED_FROM")}
    if fc.get("RECORD"):
        out["forecast"] = fc["RECORD"]
    if p["PREP"].get("TICK"):
        out["morning_tick"] = p["PREP"]["TICK"]
    if p["PREP"].get("PRIOR_DAY_EXPORTS"):
        out["prior_day_exports"] = p["PREP"]["PRIOR_DAY_EXPORTS"]
    if ex["STATUS"] == PASS and ex.get("DETAIL"):
        out["exports"] = ex["DETAIL"]
    return out


def run_health(life, day: date) -> tuple[int, dict]:
    from cbb_platform.prod import lifecycle as L
    with locked(life.ops_root, day) as p:
        key = _upstream_key(p)
        if p["HEALTH"]["STATUS"] == PASS and p["HEALTH"].get("AGGREGATED_UPDATED_AT") == key and p["MORNING_COMPLETE"]:
            p["RECOVERY"]["ACTIVE"] = False
            return _skip(life.ops_root, day, "HEALTH", "ALREADY_COMPLETE")
        st = p["HEALTH"]
        st.update(STATUS=RUNNING, STARTED_AT=_now_iso(), FINISHED_AT=None, EXIT_CODE=None, ATTEMPTS=int(st.get("ATTEMPTS") or 0) + 1)
    log_event(life.ops_root, day, {"stage": "HEALTH", "action": "RUN"})
    try:
        with locked(life.ops_root, day) as p:
            p["HEALTH"].update(STATUS=PASS, FINISHED_AT=_now_iso(), EXIT_CODE=0, AGGREGATED_UPDATED_AT=_upstream_key(p))
            derive(p)
            rec = p["RECOVERY"]
            if rec.get("ACTIVE"):                                # inside an 08:00 recovery: one more bounded pass?
                if p["RECOVERY_REQUIRED"] and int(rec.get("PASSES_REQUESTED") or 0) < MAX_RECOVERY_PASSES:
                    rec["PASSES_REQUESTED"] = int(rec.get("PASSES_REQUESTED") or 0) + 1
                    _write_resume_request(life.ops_root, day, p["FIRST_INCOMPLETE_STAGE"], rec["PASSES_REQUESTED"])
                    p["RECOVERY_LAST_ACTION"] = f"RESUME_AT_{p['FIRST_INCOMPLETE_STAGE']} (pass {rec['PASSES_REQUESTED']})"
                else:
                    rec["ACTIVE"] = False
            snap = json.loads(json.dumps(p, default=str))
        L.health(life, day, morning=summary(snap), **_health_sections(snap))
    except Exception as e:                                       # noqa: BLE001
        with locked(life.ops_root, day) as p:
            p["HEALTH"].update(STATUS=FAIL, FINISHED_AT=_now_iso(), EXIT_CODE=1, ERROR_TYPE="EXCEPTION", ERROR_DETAIL=_err(e))
        return 1, {"stage": "HEALTH", "status": FAIL, "error": _err(e)}
    return 0, {"stage": "HEALTH", "status": PASS, "MORNING_COMPLETE": snap["MORNING_COMPLETE"],
               "FIRST_INCOMPLETE_STAGE": snap["FIRST_INCOMPLETE_STAGE"], "RECOVERY_REQUIRED": snap["RECOVERY_REQUIRED"]}


def run_stage(life, day: date, stage: str, **kw) -> tuple[int, dict]:
    return {"PREP": run_prep, "WALK_FORWARD": run_walkforward, "INITIAL_FORECAST": run_forecast, "EXPORTS": run_exports,
            "HEALTH": run_health}[stage](life, day, **kw)


# ============================================================================ ExecStopPost: how did the stage end?
SIGNALS = {"KILL": "SIGKILL", "TERM": "SIGTERM", "9": "SIGKILL", "15": "SIGTERM"}


def classify_service_result(service_result: str | None, exit_code: str | None, exit_status: str | None) -> str | None:
    """systemd ExecStopPost env (SERVICE_RESULT / EXIT_CODE / EXIT_STATUS) -> orchestration error type, or None."""
    r = (service_result or "").strip()
    if r in ("", "success"):
        return None
    if r == "oom-kill":
        return "OOM_KILL"
    if r == "timeout":
        return "TIMEOUT"
    if r in ("signal", "core-dump") or exit_code in ("killed", "dumped"):
        return SIGNALS.get(str(exit_status or "").upper(), "UNEXPECTED_CHILD_EXIT")
    return "UNEXPECTED_CHILD_EXIT"


def finalize(life, day: date, stage: str, env: dict | None = None) -> dict:
    """Run by each stage unit's ExecStopPost (same cgroup, after the main process is gone): records the cgroup memory
    peak + SERVICE_RESULT, and turns a stage still RUNNING (killed before its receipt) into FAIL with the orchestration
    cause. A walk-forward killed AFTER promotion is reconstructed as PASS from the model store, never refit."""
    env = os.environ if env is None else env
    res, code, status = env.get("SERVICE_RESULT"), env.get("EXIT_CODE"), env.get("EXIT_STATUS")
    etype = classify_service_result(res, code, status)
    out = {"stage": stage, "service_result": res, "exit_code": code, "exit_status": status, "error_type": etype}
    with locked(life.ops_root, day) as p:
        st = p[stage]
        if env.get("INVOCATION_ID") and st.get("INVOCATION_ID") != env["INVOCATION_ID"]:
            out["action"] = "NOT_THIS_INVOCATION"                # a skip invocation: the stage record is not ours
            log_event(life.ops_root, day, {"stage": stage, "action": "FINALIZE_SKIP", **out})
            return out
        st["SERVICE_RESULT"] = res
        peak = cgroup_bytes("memory.peak")
        if peak is not None and st.get("STATUS") in (RUNNING, PASS, FAIL, DEFERRED) and stage != "HEALTH":
            st["SYSTEMD_MEMORY_PEAK_BYTES"] = max(peak, int(st.get("SYSTEMD_MEMORY_PEAK_BYTES") or 0))
        if st.get("STATUS") == RUNNING:
            etype = etype or "UNEXPECTED_CHILD_EXIT"
            st.update(STATUS=FAIL, FINISHED_AT=_now_iso(), EXIT_CODE=_int(status), ERROR_TYPE=etype,
                      ERROR_DETAIL=f"service result {res}, exit {code}/{status}: killed before the stage receipt",
                      RETRYABLE=int(st.get("ATTEMPTS") or 0) < MAX_ATTEMPTS)
            if stage == "WALK_FORWARD":
                ev = model_evidence(_model_root(life), day)
                if ev["VALID"]:
                    _apply_evidence(st, ev, f"MODEL_STORE_EVIDENCE_AFTER_{etype}")
                    st["KILLED_AFTER_PROMOTION"] = etype
                else:
                    st["MODEL_DECISION"] = None if st["RETRYABLE"] else "INCUMBENT_RETAINED"
            out["action"] = f"RECORDED_{st['STATUS']}"
        else:
            out["action"] = "TELEMETRY_ONLY"
    log_event(life.ops_root, day, {"stage": stage, "action": "FINALIZE", **out})
    return out


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ============================================================================ 08:00 recovery
def _reconcile_dead(p: dict) -> list[str]:
    """A stage left RUNNING whose process is gone (no ExecStopPost record: host crash, manual kill) -> FAIL INTERRUPTED."""
    fixed = []
    for s in STAGES:
        st = p[s]
        if st["STATUS"] == RUNNING and not pid_alive(st.get("PID")):
            st.update(STATUS=FAIL, FINISHED_AT=_now_iso(), ERROR_TYPE="INTERRUPTED",
                      ERROR_DETAIL="stage process gone without a receipt", RETRYABLE=int(st.get("ATTEMPTS") or 0) < MAX_ATTEMPTS)
            fixed.append(s)
    return fixed


def _write_resume_request(ops_root: Path, day: date, stage: str | None, n: int) -> None:
    rp = resume_request_path(ops_root)
    rp.parent.mkdir(parents=True, exist_ok=True)
    tmp = rp.with_name(f".{RESUME_REQUEST}.{os.getpid()}")
    tmp.write_text(json.dumps({"date": day.isoformat(), "resume_at": stage, "pass": n, "at": _now_iso()}))
    os.replace(tmp, rp)


def recover(life, day: date) -> dict:
    """08:00 ET: evaluate the FULL morning state machine (replaces the old 'retry only a deferred walk-forward' NO_OP).
    Runs under the CBB writer lock, after every 07:00 stage unit has finished (systemd After=). It never fits, forecasts
    or exports itself: it records the decision and requests the idempotent stage chain, which resumes at the first
    incomplete stage in fresh processes / cgroups."""
    from cbb_platform.prod import lifecycle as L
    with locked(life.ops_root, day) as p:
        alive = [s for s in STAGES if p[s]["STATUS"] == RUNNING and pid_alive(p[s].get("PID"))]
        if alive:                                                # never a concurrent writer: wait for the next run
            out = {"status": "DEFERRED_STAGE_RUNNING", "running": alive}
            p["RECOVERY_LAST_ACTION"] = f"DEFERRED_STAGE_RUNNING {alive}"
            log_event(life.ops_root, day, {"stage": "RECOVERY", **out})
            return out
        interrupted = _reconcile_dead(p)
        wf = p["WALK_FORWARD"]
        if wf["STATUS"] != PASS:                                 # CASE 6 / 27: promotion exists without a receipt
            ev = model_evidence(_model_root(life), day)
            if ev["VALID"]:
                _apply_evidence(wf, ev, "MODEL_STORE_EVIDENCE_AT_RECOVERY")
        derive(p)
        nxt = p["FIRST_INCOMPLETE_STAGE"]
        if p["MORNING_COMPLETE"]:
            action, status = "ALREADY_COMPLETE", "ALREADY_COMPLETE"
        elif not p["RECOVERY_REQUIRED"] and nxt is None:
            action, status = "NOTHING_RECOVERABLE", "INCOMPLETE_NOT_RECOVERABLE"
        else:
            action, status = f"RESUME_AT_{nxt}", "RESUME_REQUESTED"
            p["RECOVERY"].update(ACTIVE=True, PASSES_REQUESTED=1)
            _write_resume_request(life.ops_root, day, nxt, 1)
        p["RECOVERY_LAST_ACTION"] = action
        p["RECOVERY"]["HISTORY"] = (p["RECOVERY"].get("HISTORY") or [])[-20:] + [
            {"at": _now_iso(), "action": action, "interrupted": interrupted, "pid": os.getpid(),
             "walk_forward": wf["STATUS"], "first_incomplete": nxt}]
        snap = json.loads(json.dumps(p, default=str))
    out = {"lifecycle": "MORNING_RECOVERY", "day": day.isoformat(), "status": status, "action": action,
           "first_incomplete_stage": nxt, "interrupted_stages": interrupted, "MORNING_COMPLETE": snap["MORNING_COMPLETE"],
           "walk_forward": {k: snap["WALK_FORWARD"].get(k) for k in ("STATUS", "RUN_ID", "ACTIVE_MODEL_VERSION",
                                                                     "RECOVERED_FROM", "ERROR_TYPE")}}
    log_event(life.ops_root, day, {"stage": "RECOVERY", "action": action, "first_incomplete": nxt})
    L.health(life, day, morning=summary(snap), morning_recovery=out, **_health_sections(snap))
    return out


# ============================================================================ historical reconciliation (2026-11-13)
def reconcile_missed_morning(life, day: date, missed_reason: str, prep_evidence: str) -> dict:
    """Reconstruct the durable morning record of a day whose monolithic morning was killed AFTER promotion, from
    existing evidence only. WALK_FORWARD <- the model store (never refit). The INITIAL forecast that never ran is
    recorded as FAIL / <missed_reason> and RECONCILED_TERMINAL (no historical snapshot is fabricated; the later
    availability snapshots of that day are untouched)."""
    from cbb_platform.prod import lifecycle as L
    ev = model_evidence(_model_root(life), day)
    if not ev["VALID"]:
        raise SystemExit(f"reconcile: no valid same-day promotion for {day}: {ev.get('reason')}")
    att = [r for r in OS.read_jsonl(L.wf_attempts_path(life, day)) if r.get("attempt") == "MORNING_0700"]
    with locked(life.ops_root, day) as p:
        if p["INITIAL_FORECAST"].get("RECONCILED_TERMINAL"):
            return {"status": "ALREADY_RECONCILED", "progress": str(progress_path(life.ops_root, day))}
        p["RECONCILED_AT"] = _now_iso()
        p["RECONCILIATION"] = {"reason": missed_reason, "prep_evidence": prep_evidence,
                               "model_evidence": {k: ev.get(k) for k in ("MODEL_VERSION", "RUN_ID", "PROMOTED_AT")}}
        if p["PREP"]["STATUS"] != PASS:
            p["PREP"].update(STATUS=PASS, STARTED_AT=None, FINISHED_AT=att[0]["started_at"] if att else None, EXIT_CODE=None,
                             ATTEMPTS=1, RETRYABLE=False, RECONCILED_FROM=prep_evidence)
        wf = p["WALK_FORWARD"]
        _apply_evidence(wf, ev, "HISTORICAL_RECONCILIATION_MODEL_STORE")
        wf["ATTEMPTS"] = max(1, int(wf.get("ATTEMPTS") or 0))
        if att:
            a = att[-1]
            mem = a.get("memory_mb") or {}
            wf.update(STARTED_AT=a.get("started_at"), FINISHED_AT=a.get("finished_at"), EXIT_CODE=None,
                      ATTEMPT_RECORD=_wf_brief({**a, "run_id": ev["RUN_ID"]}),
                      PEAK_RSS_MB=(mem.get("end") or {}).get("VmHWM"), END_RSS_MB=(mem.get("end") or {}).get("VmRSS"),
                      START_RSS_MB=(mem.get("settle_and_monitor") or {}).get("VmRSS"),
                      TELEMETRY_NOTE="monolithic 07:00 process (pre-split): VmHWM includes the in-process tick + exports")
        fc = p["INITIAL_FORECAST"]
        fc.update(STATUS=FAIL, ERROR_TYPE=missed_reason, HISTORICAL_STATUS="MISSED_DUE_TO_MORNING_OOM",
                  ERROR_DETAIL="the monolithic 07:00 process was OOM-killed in the INITIAL forecast after the model "
                               "promotion; no INITIAL snapshot exists and none is fabricated",
                  RECONCILED_TERMINAL=True, RETRYABLE=False, ATTEMPTS=max(1, int(fc.get("ATTEMPTS") or 0)))
        snap = json.loads(json.dumps(p, default=str))
    log_event(life.ops_root, day, {"stage": "RECONCILE", "action": "HISTORICAL_RECONCILIATION", "reason": missed_reason})
    return {"status": "RECONCILED", "progress": str(progress_path(life.ops_root, day)), "summary": summary(snap)}
