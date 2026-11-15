"""Terminal run receipts: every production run directory ends in exactly one terminal record.

    prod/runs/<run>/run_summary.json    the run COMPLETED (written by prod/run.daily)
    prod/runs/<run>/run_failure.json    the run ended WITHOUT completing:
        FAILED     an exception inside daily (traceback tail, exception type) -- written by terminal() before re-raising
        TERMINATED SIGTERM (systemd TimeoutStartSec / stop) -- terminal() turns SIGTERM into RunTerminated for the run's
                   duration, so the receipt is written and the process still exits non-zero
        ORPHANED   the process vanished without either record (SIGKILL / OOM kill / host stop): written by the 05:45
                   retention pass (prod/storage_retention: ORPHAN_RUN_RECEIPT) once the run is older than
                   ORPHAN_HOURS, longer than the longest CBB unit timeout, so no live run is ever marked

A run directory with neither record and younger than ORPHAN_HOURS is IN_PROGRESS. Receipts never change a prediction;
they make the run's end auditable and let retention bound its scratch (INCOMPLETE_RUN_SCRATCH).
"""
from __future__ import annotations

import functools
import json
import os
import signal
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

FAILURE_FILE = "run_failure.json"
SUMMARY_FILE = "run_summary.json"
ORPHAN_HOURS = 7                                  # > the longest CBB unit TimeoutStartSec (morning-recover 360 min)

_active: list[Path] = []


class RunTerminated(BaseException):
    """SIGTERM delivered while a production run was in progress."""


def register(run_dir: Path) -> None:
    """Called by daily directly after the run directory exists."""
    if _active:
        _active[-1] = Path(run_dir)


TERMINAL_STATES = ("SUCCESS", "FAILED", "ABORTED", "DEFERRED")
_TERMINAL_OF = {"FAILED": "FAILED", "TERMINATED": "ABORTED", "ORPHANED": "ABORTED"}


def terminal_state(run_dir: Path) -> str:
    """Exactly one of TERMINAL_STATES (or IN_PROGRESS): SUCCESS = run_summary.json with a usable readiness state;
    DEFERRED = run_summary.json whose readiness is BLOCKED (completed, nothing priced for use); FAILED = an exception;
    ABORTED = SIGTERM (TERMINATED) or a vanished process (ORPHANED)."""
    run_dir = Path(run_dir)
    try:
        s = json.loads((run_dir / SUMMARY_FILE).read_text())
        if s.get("run_id") == run_dir.name:
            return "DEFERRED" if s.get("readiness_state") == "BLOCKED" else "SUCCESS"
    except (OSError, ValueError, AttributeError):
        pass
    st = state(run_dir)
    return _TERMINAL_OF.get(st, "FAILED" if st != "IN_PROGRESS" else "IN_PROGRESS")


def write_failure(run_dir: Path, status: str, reason: str, **kw) -> Path:
    run_dir = Path(run_dir)
    rec = {"run_id": run_dir.name, "status": status, "terminal_state": _TERMINAL_OF.get(status, "FAILED"), "reason": reason,
           "at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), **kw}
    p = run_dir / FAILURE_FILE
    tmp = run_dir / f".{FAILURE_FILE}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(rec, indent=1, default=str) + "\n")
    os.replace(tmp, p)
    return p


def state(run_dir: Path, now: datetime | None = None) -> str:
    run_dir = Path(run_dir)
    try:
        if json.loads((run_dir / SUMMARY_FILE).read_text()).get("run_id") == run_dir.name:
            return "COMPLETED"
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return str(json.loads((run_dir / FAILURE_FILE).read_text())["status"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "IN_PROGRESS"


def _context(e: BaseException, a, k) -> dict:
    """stage (innermost cbb_platform frame), model, state root and as-of of the failed run (best effort, never raises)."""
    out = {}
    try:
        tb, stage = e.__traceback__, None
        while tb is not None:
            f = tb.tb_frame.f_code.co_filename
            if "cbb_platform" in f and not f.endswith("run_receipts.py"):
                stage = f"{Path(f).parent.name}/{Path(f).name}:{tb.tb_lineno}:{tb.tb_frame.f_code.co_name}"
            tb = tb.tb_next
        out["stage"] = stage
        m = k.get("model")
        ident = getattr(m, "identity", None) or {}
        out["model_version"] = ident.get("model_version")
        out["state_root"] = str(k.get("state_root")) if k.get("state_root") is not None else None
        out["as_of"] = str(a[0]) if a else str(k.get("as_of")) if k.get("as_of") is not None else None
    except Exception:                                # noqa: BLE001 -- context is optional; the receipt is not
        pass
    return out


def terminal(fn):
    """Decorator for prod/run.daily: on any exception (or SIGTERM) a run without run_summary.json gets run_failure.json."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        _active.append(None)
        main = threading.current_thread() is threading.main_thread()
        prev = None
        if main:
            def _term(signum, frame):
                raise RunTerminated(f"signal {signum}")
            prev = signal.signal(signal.SIGTERM, _term)
        try:
            return fn(*a, **k)
        except BaseException as e:
            d = _active[-1]
            if d is not None and d.exists() and not (d / SUMMARY_FILE).exists():
                status = "TERMINATED" if isinstance(e, (RunTerminated, KeyboardInterrupt)) else "FAILED"
                try:
                    write_failure(d, status, f"{type(e).__name__}: {e}"[:500], error_type=type(e).__name__,
                                  traceback_tail=traceback.format_exc().splitlines()[-12:], **_context(e, a, k))
                except OSError:
                    pass                          # the exception below is the primary signal; never mask it
            raise
        finally:
            _active.pop()
            if main:
                signal.signal(signal.SIGTERM, prev if prev is not None else signal.SIG_DFL)
    return wrapper
