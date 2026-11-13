"""The scheduled availability refresh (10:00 / 13:00 / 16:00 / 18:00 ET) as Phase A + Phase B.

    cbb-originator-run lifecycle-refresh HH:MM  ->  python -m cbb_platform.availability_refresh --window HH:MM

Why: the old refresh held the CBB global writer lock for its WHOLE run (acquisition + forecast + exports), so one slow
refresh (2026-11-12 21:00Z: the forecast stalled after model_inputs at ~0% CPU until the 40-min unit timeout) starved
every 10-minute quote tick (600 s writer-lock wait -> "tick skipped").

Phase A (child process: lifecycle availability-acquire): the RotoWire acquisition ONLY, NEVER under the global writer
    lock. Narrow locks only: the RotoWire web-acquisition lock, the EventStore lock, the request-ledger lock. In-process
    budget CBB_AVAILABILITY_ACQUIRE_BUDGET_SEC (300 s, SIGALRM); this parent hard-kills it at budget + 60 s (SIGABRT ->
    faulthandler stacks, then SIGKILL). Result: one immutable receipt <ops>/availability_acquisitions/<refresh_id>.json.
    ACQUISITION_TIMEOUT / ACQUISITION_FAILED -> no Phase B: the prior forecast and exports stand.
Phase B (child process: lifecycle availability-publish, the lock fd inherited): taken only AFTER Phase A, waiting at most
    PHASE_B_WRITER_WAIT_MAX_SEC (300 s). Busy -> DEFERRED_WRITER_LOCK, retried at +5 / +10 / +15 min after the first
    deferral (max 3, each only if it still fits the unit's 40-min budget); a receipt older than 15 min is replaced by a
    fresh Phase A (outside the lock) first. Under the lock it re-reads state / ACTIVE / snapshots, forecasts (PMFs ->
    forecast_players -> snapshot.json -> index LAST), exports, health.
    WATCHDOG (bounded lock hold): early non-fatal stack dump (SIGUSR1) at PHASE_B_EARLY_DUMP_SEC (720 s), abort
    (SIGABRT: faulthandler dumps every thread, then the process dies) at PHASE_B_WATCHDOG_SEC (900 s = 15 min;
    healthy refreshes take 7-11 min), SIGKILL 30 s later. Nothing in model / PMF / simulation logic changes: an aborted
    Phase B never appended its index line, so the prior snapshots remain the latest; every workbook is replaced
    atomically, so the prior exports stay valid. The abort (with the stack file) is recorded in the health file while
    the lock is still held, then the lock is released.
WRITER_COUNT stays 1: the Phase B child shares this parent's locked open file description; the lock is released only
when both are gone (the kernel releases it on any death).

Records: <ops>/availability_acquisitions/<id>.json (receipts) and <ops>/availability_publications/<refresh_id>.json
(phase timings, lock times, attempts, outcome, stack file). Exit: 0 published | 1 failed / aborted / acquisition
timeout | 75 DEFERRED_WRITER_LOCK after the last retry (EX_TEMPFAIL).
Stdlib only (plus prod.ops_store): this parent stays small while it holds the lock. It lives OUTSIDE the production
layer (cbb_platform/prod has no process / shell code; tests/test_prod_shadow.py) -- like cbb_platform/ops_persistence.py.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cbb_platform.prod import ops_store as OS

WINDOWS = ("10:00", "13:00", "16:00", "18:00")
ACQ_PUBLISHABLE = ("COMPLETED", "ACQUISITION_BLOCKED", "NOT_ENABLED")      # == lifecycle.ACQ_PUBLISHABLE
DEFERRED = "DEFERRED_WRITER_LOCK"


def _env_f(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else float(default)


@dataclass
class Limits:
    acquire_budget_sec: float = field(default_factory=lambda: _env_f("CBB_AVAILABILITY_ACQUIRE_BUDGET_SEC", 300))
    acquire_kill_grace_sec: float = 60.0
    receipt_max_age_sec: float = 15 * 60
    writer_wait_max_sec: float = 300.0            # PHASE_B_WRITER_WAIT_MAX_SEC
    retry_delay_sec: float = 5 * 60               # PHASE_B_RETRY_DELAY_MIN = 5 -> retries at +5 / +10 / +15 min
    max_retries: int = 3                          # PHASE_B_MAX_RETRIES
    watchdog_sec: float = field(default_factory=lambda: _env_f("CBB_PHASE_B_WATCHDOG_SEC", 900))
    early_dump_sec: float = field(default_factory=lambda: _env_f("CBB_PHASE_B_EARLY_DUMP_SEC", 720))
    kill_grace_sec: float = 30.0
    unit_budget_sec: float = 2400.0               # == the unit's TimeoutStartSec=40min (test-asserted); never changed
    margin_sec: float = 60.0
    min_lock_wait_sec: float = 10.0
    poll_sec: float = 1.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def lifecycle_cmd(python: str, ops_root: Path, prod_root: Path):
    base = [python, "-m", "cbb_platform.prod.lifecycle", "--ops-root", str(ops_root), "--prod-root", str(prod_root)]
    return lambda phase, args: base + [phase] + list(args)


class Refresh:
    def __init__(self, window: str, ops_root: Path, prod_root: Path, writer_lock: Path, limits: Limits | None = None,
                 phase_cmd=None, log=None, clock=time.monotonic, sleep=time.sleep):
        if window not in WINDOWS:
            raise ValueError(f"window {window!r} not in {WINDOWS}")
        self.window, self.ops, self.prod, self.lock = window, Path(ops_root), Path(prod_root), Path(writer_lock)
        self.L = limits or Limits()
        self.cmd = phase_cmd or lifecycle_cmd(sys.executable, self.ops, self.prod)
        self.clock, self.sleep = clock, sleep
        self._log = log or (lambda m: print(f"cbb-availability-refresh[{window}]: {m}", file=sys.stderr, flush=True))
        self.t0 = clock()
        self.rid = "R" + _utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.pub_dir = self.ops / "availability_publications"
        self.rec = {"REFRESH_ID": self.rid, "WINDOW": window, "SERVICE_START": _iso(_utcnow()), "PID": os.getpid(),
                    "GLOBAL_WRITER_LOCK_HELD_DURING_WEB_ACQUIRE": "NO", "ATTEMPTS": [], "RECEIPTS": [],
                    "PHASE_A_SEC": 0.0, "LIMITS": vars(self.L).copy()}

    # ------------------------------------------------------------------ helpers
    def elapsed(self) -> float:
        return self.clock() - self.t0

    def mark(self, name: str, **kv) -> None:
        self.rec.setdefault("MARKS", []).append({"mark": name, "at": _iso(_utcnow()), "elapsed_sec": round(self.elapsed(), 1), **kv})
        self._log(f"{name} at={_iso(_utcnow())} elapsed={self.elapsed():.1f}s" + "".join(f" {k}={v}" for k, v in kv.items()))

    def _wait_child(self, p: subprocess.Popen, schedule: list[tuple[float, int]]) -> tuple[int, list[str]]:
        """Wait for p; send each (after_sec, signal) of the schedule once. Returns (returncode, signals sent)."""
        start, sent, todo = self.clock(), [], sorted(schedule)
        while True:
            rc = p.poll()
            if rc is not None:
                return rc, sent
            t = self.clock() - start
            while todo and t >= todo[0][0]:
                _, sig = todo.pop(0)
                try:
                    p.send_signal(sig)
                    sent.append(signal.Signals(sig).name)
                    self._log(f"sent {signal.Signals(sig).name} to pid {p.pid} at {t:.0f}s")
                except ProcessLookupError:
                    pass
            self.sleep(self.L.poll_sec)

    # ------------------------------------------------------------------ Phase A
    def phase_a(self, n: int) -> dict:
        aid = self.rid if n == 1 else f"{self.rid}-A{n}"
        rp = self.ops / "availability_acquisitions" / f"{aid}.json"
        stack = rp.with_suffix(".stack.txt")
        rp.parent.mkdir(parents=True, exist_ok=True)
        self.mark("PHASE_A_START", acquisition=aid)
        t = self.clock()
        p = subprocess.Popen(self.cmd("availability-acquire", ["--window", self.window, "--refresh-id", aid,
                                                              "--budget-sec", str(self.L.acquire_budget_sec),
                                                              "--stack-file", str(stack)]),
                             stdout=subprocess.DEVNULL)
        hard = self.L.acquire_budget_sec + self.L.acquire_kill_grace_sec
        rc, sent = self._wait_child(p, [(hard, signal.SIGABRT), (hard + 10, signal.SIGKILL)])
        sec = self.clock() - t
        self.rec["PHASE_A_SEC"] = round(self.rec["PHASE_A_SEC"] + sec, 1)
        rcpt = OS.read_json(rp, None) if rp.exists() else None
        if rcpt is None:                                  # the child never wrote one: the parent records it (write-once)
            status = "ACQUISITION_TIMEOUT" if sent else "ACQUISITION_FAILED"
            rcpt = {"REFRESH_ID": aid, "WINDOW": self.window, "STARTED_AT": None, "FINISHED_AT": _iso(_utcnow()),
                    "STATUS": status, "PUBLISHABLE": False, "GLOBAL_WRITER_LOCK_USED": "NO",
                    "REASONS": [f"{status} (phase A child rc={rc}; signals {sent or 'none'}; no receipt written)"],
                    "WARNINGS": [], "STACK_FILE": str(stack) if stack.exists() and stack.stat().st_size else None}
            OS.create_once(rp, rcpt)
            rcpt = OS.read_json(rp, rcpt)
        self.rec["RECEIPTS"].append({"path": str(rp), "status": rcpt.get("STATUS"), "phase_a_sec": round(sec, 1)})
        self.mark("PHASE_A_END", status=rcpt.get("STATUS"), sec=round(sec, 1))
        return {"path": rp, "receipt": rcpt, "finished_mono": self.clock()}

    # ------------------------------------------------------------------ writer lock
    def _lock(self, wait_s: float):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o660)
        deadline = self.clock() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if self.clock() >= deadline:
                    os.close(fd)
                    return None
                self.sleep(min(self.L.poll_sec, max(0.0, deadline - self.clock())))

    def _b_cost(self) -> float:
        return self.L.watchdog_sec + self.L.kill_grace_sec + self.L.margin_sec

    def _fits(self, extra: float) -> bool:
        return self.elapsed() + extra + self._b_cost() <= self.L.unit_budget_sec

    # ------------------------------------------------------------------ Phase B
    def phase_b(self, fd: int, acq: dict, wait_sec: float, acquired_at: datetime) -> dict:
        stack = self.pub_dir / f"{self.rid}.stack.txt"
        result = self.pub_dir / f"{self.rid}.publish.json"
        meta = {"phase_b_lock_acquired_at": _iso(acquired_at), "phase_b_lock_wait_sec": round(wait_sec, 1),
                "refresh_id": self.rid}
        self.pub_dir.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen(self.cmd("availability-publish", ["--window", self.window, "--receipt", str(acq["path"]),
                                                              "--lock-fd", str(fd), "--stack-file", str(stack),
                                                              "--watchdog-sec", str(self.L.watchdog_sec),
                                                              "--result-file", str(result), "--lock-meta", json.dumps(meta)]),
                             pass_fds=(fd,), stdout=subprocess.DEVNULL)
        t = self.clock()
        sched = [(self.L.watchdog_sec, signal.SIGABRT), (self.L.watchdog_sec + self.L.kill_grace_sec, signal.SIGKILL)]
        if 0 < self.L.early_dump_sec < self.L.watchdog_sec:
            sched.append((self.L.early_dump_sec, signal.SIGUSR1))
        rc, sent = self._wait_child(p, sched)
        sec = self.clock() - t
        aborted = "SIGABRT" in sent or "SIGKILL" in sent
        res = OS.read_json(result, None) if result.exists() else None
        status = ("PHASE_B_WATCHDOG_ABORT" if aborted else "PUBLISHED" if rc == 0 and res and res.get("status") == "PUBLISHED"
                  else (res or {}).get("status") or "PHASE_B_FAILED")
        out = {"status": status, "rc": rc, "signals": sent, "phase_b_sec": round(sec, 1), "result": res,
               "stack_file": str(stack) if stack.exists() and stack.stat().st_size else None}
        if status != "PUBLISHED":                        # still under the lock: record the failure in the health file
            self._health_failure(out, acq)
        return out

    def _health_failure(self, out: dict, acq: dict) -> None:
        """Merge the failed / aborted Phase B into <prod>/health/<ET date>.json (the lock is still held). Prior
        snapshots and exports are untouched (no index line, no workbook replaced by this record)."""
        try:
            day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
            p = self.prod / "health" / f"{day}.json"
            h = OS.read_json(p, {}) or {}
            h[f"availability_{self.window.replace(':', '')}"] = {
                "status": out["status"], "refresh_id": self.rid, "phase_b_rc": out["rc"], "signals": out["signals"],
                "phase_b_sec": out["phase_b_sec"], "stack_file": out["stack_file"],
                "note": "Phase B did not complete: prior snapshots / exports stand (nothing indexed by this refresh)",
                "availability_acquisition": {"receipt": str(acq["path"]), "status": acq["receipt"].get("STATUS")},
                "at": _iso(_utcnow())}
            rep = acq["receipt"].get("ACQUISITION_REPORT")
            if isinstance(rep, dict):                    # the acquisition itself did happen (events are stored)
                h[f"availability_{self.window.replace(':', '')}"]["rotowire_web_acquisition"] = rep
            h["updated_at"] = _iso(_utcnow())
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(h, indent=1, default=str))
            os.replace(tmp, p)
        except Exception as e:                           # noqa: BLE001 -- the publication record still carries it
            self._log(f"health record of the Phase B failure failed: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ the whole refresh
    def run(self) -> tuple[int, dict]:
        self.mark("SERVICE_START")
        n_acq = 1
        acq = self.phase_a(n_acq)
        code = self._run_after_a(acq, n_acq)
        self.rec["TOTAL_REFRESH_SEC"] = round(self.elapsed(), 1)
        self.mark("SERVICE_END", status=self.rec.get("STATUS"))
        self.rec["SERVICE_END"] = _iso(_utcnow())
        OS.create_once(self.pub_dir / f"{self.rid}.json", self.rec)
        return code, self.rec

    def _run_after_a(self, acq: dict, n_acq: int) -> int:
        retries, first_defer = 0, None
        while True:
            r = acq["receipt"]
            if not r.get("PUBLISHABLE") or r.get("STATUS") not in ACQ_PUBLISHABLE:
                self.rec["STATUS"] = r.get("STATUS") or "ACQUISITION_FAILED"
                self.rec["NOTE"] = "no Phase B: the prior forecast snapshots and exports stand"
                return 1
            wait = min(self.L.writer_wait_max_sec, self.L.unit_budget_sec - self.elapsed() - self._b_cost())
            if wait < self.L.min_lock_wait_sec:
                self.rec.update(STATUS=DEFERRED, NOTE="no time left in the unit budget for a bounded Phase B")
                return 75
            self.mark("PHASE_B_LOCK_WAIT_START", max_wait_sec=round(wait, 1), attempt=retries + 1)
            t = self.clock()
            fd = self._lock(wait)
            waited = self.clock() - t
            self.rec["PHASE_B_LOCK_WAIT_SEC"] = round(self.rec.get("PHASE_B_LOCK_WAIT_SEC", 0.0) + waited, 1)
            if fd is None:
                self.rec["ATTEMPTS"].append({"attempt": retries + 1, "status": DEFERRED, "waited_sec": round(waited, 1),
                                             "at": _iso(_utcnow())})
                self.mark(DEFERRED, waited_sec=round(waited, 1), attempt=retries + 1)
                first_defer = first_defer if first_defer is not None else self.clock()
                if retries >= self.L.max_retries:
                    self.rec["STATUS"] = DEFERRED
                    return 75
                retries += 1
                at = first_defer + retries * self.L.retry_delay_sec
                stale = (at - acq["finished_mono"]) > self.L.receipt_max_age_sec
                need = (at - self.clock()) + self.L.min_lock_wait_sec + (
                    self.L.acquire_budget_sec + self.L.acquire_kill_grace_sec if stale else 0)
                if not self._fits(need):
                    self.rec.update(STATUS=DEFERRED, NOTE=f"retry {retries} at +{retries * self.L.retry_delay_sec / 60:.0f} min "
                                                          "does not fit the unit budget with a full Phase B watchdog")
                    return 75
                if at > self.clock():
                    self.sleep(at - self.clock())
                if (self.clock() - acq["finished_mono"]) > self.L.receipt_max_age_sec:
                    n_acq += 1
                    acq = self.phase_a(n_acq)            # fresh Phase A, outside the writer lock
                continue
            acquired_at = _utcnow()
            self.rec.update(PHASE_B_LOCK_ACQUIRED_AT=_iso(acquired_at))
            self.mark("PHASE_B_LOCK_ACQUIRED", waited_sec=round(waited, 1))
            held_t = self.clock()
            try:
                if (self.clock() - acq["finished_mono"]) > self.L.receipt_max_age_sec:
                    os.close(fd)
                    fd = None
                    self.mark("PHASE_B_LOCK_RELEASED", reason="receipt stale; fresh Phase A outside the lock")
                    n_acq += 1
                    acq = self.phase_a(n_acq)
                    continue
                self.rec["RECEIPT_USED"] = str(acq["path"])
                b = self.phase_b(fd, acq, waited, acquired_at)
            finally:
                if fd is not None:
                    os.close(fd)                         # the child already exited: this releases the lock
                    rel = _utcnow()
                    self.rec.update(PHASE_B_LOCK_RELEASED_AT=_iso(rel),
                                    PHASE_B_LOCK_HELD_SEC=round(self.clock() - held_t, 1))
                    self.mark("PHASE_B_LOCK_RELEASED", held_sec=self.rec["PHASE_B_LOCK_HELD_SEC"])
            self.rec.update(PHASE_B=b, STATUS=b["status"])
            return 0 if b["status"] == "PUBLISHED" else 1


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.availability_refresh")
    ap.add_argument("--window", required=True, choices=WINDOWS)
    ap.add_argument("--ops-root", type=Path, required=True)
    ap.add_argument("--prod-root", type=Path, required=True)
    ap.add_argument("--writer-lock", type=Path, default=os.environ.get("CBB_WRITER_LOCK"))
    a = ap.parse_args(argv)
    if not a.writer_lock:
        raise SystemExit("availability_refresh needs --writer-lock or CBB_WRITER_LOCK")
    code, rec = Refresh(a.window, a.ops_root, a.prod_root, a.writer_lock).run()
    print(json.dumps({k: rec.get(k) for k in ("REFRESH_ID", "WINDOW", "STATUS", "PHASE_A_SEC", "PHASE_B_LOCK_WAIT_SEC",
                                               "PHASE_B_LOCK_ACQUIRED_AT", "PHASE_B_LOCK_RELEASED_AT",
                                               "PHASE_B_LOCK_HELD_SEC", "TOTAL_REFRESH_SEC", "RECEIPT_USED")}, indent=1))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
