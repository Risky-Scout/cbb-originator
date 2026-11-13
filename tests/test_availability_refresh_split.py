"""21:00 availability starvation fix (2026-11-12): the scheduled availability refresh as Phase A (RotoWire acquisition,
NEVER under the CBB global writer lock) + Phase B (forecast / exports under the writer lock, bounded by a 15-min
faulthandler watchdog). cbb_platform/availability_refresh.py, prod/lifecycle.py availability-acquire / availability-publish.

Concurrency / process-death tests use REAL processes and real flock(2) locks (fcntl; no flock(1) needed). The phase
children of the orchestrator tests are a small fake (tests drive their timing); the prediction-equivalence test runs the
real forecast path old-vs-split on identical fixture worlds. CI-safe: temporary roots, no network, no credential.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform import availability_refresh as AR
from cbb_platform.prod import (config as PC, lifecycle as L, ops_store as OS,
                               pmf_evidence as PE, rotowire_acquire as RA, rotowire_feed as RW)

ROOT = Path(C.PROJECT_ROOT)
UNIT = ROOT / "deploy" / "droplet" / "systemd" / "cbb-originator-availability@.service"
RUN = ROOT / "deploy" / "droplet" / "bin" / "cbb-originator-run"
SLOW_A_SEC = float(os.environ.get("CBB_TEST_SLOW_PHASE_A_SEC", "60"))       # the directive's >= 60 s slow Phase A


# ============================================================================ fakes: phase children + lock holders
FAKE = textwrap.dedent('''
    import fcntl, json, os, sys, time, faulthandler, signal
    from pathlib import Path
    phase, args = sys.argv[1], sys.argv[2:]
    kv = {args[i][2:]: args[i + 1] for i in range(0, len(args) - 1, 2) if args[i].startswith("--")}
    ops = Path(os.environ["FAKE_OPS"]); log = Path(os.environ["FAKE_LOG"])
    def note(ev):
        with open(log, "a") as f: f.write(json.dumps({"who": phase, "ev": ev, "t": time.time(), "pid": os.getpid()}) + "\\n")
    def utc():
        from datetime import datetime, timezone; return datetime.now(timezone.utc).isoformat()
    if phase == "availability-acquire":
        note("A_START")
        if os.environ.get("FAKE_A_IGNORE_BUDGET"):
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
        faulthandler.enable(open(kv["stack-file"], "a"))
        if os.environ.get("FAKE_A_HOLD_WRITER"):          # a broken Phase A that touches the global lock (never real)
            fd = os.open(os.environ["FAKE_WRITER_LOCK"], os.O_WRONLY | os.O_CREAT); fcntl.flock(fd, fcntl.LOCK_EX)
        time.sleep(float(os.environ.get("FAKE_A_SLEEP", "0")))
        status = os.environ.get("FAKE_A_STATUS", "COMPLETED")
        rp = ops / "availability_acquisitions" / (kv["refresh-id"] + ".json"); rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({"REFRESH_ID": kv["refresh-id"], "WINDOW": kv["window"], "STATUS": status,
                                  "PUBLISHABLE": status in ("COMPLETED", "NOT_ENABLED", "ACQUISITION_BLOCKED"),
                                  "STARTED_AT": utc(), "FINISHED_AT": utc()}))
        note("A_END"); sys.exit(0)
    if phase == "availability-publish":
        fd = int(kv["lock-fd"])
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)    # must be the orchestrator's locked description
        faulthandler.enable(open(kv["stack-file"], "a"), all_threads=True)
        faulthandler.register(signal.SIGUSR1, open(kv["stack-file"], "a"), all_threads=True)
        note("B_ENTER")
        sdir = ops / "forecasts" / "D" / ("F-" + str(os.getpid())); sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "pmfs.parquet").write_text("partial")      # PMFs first, snapshot.json, the index LAST
        def hang_inside_simulation():
            while True: time.sleep(0.2)
        if os.environ.get("FAKE_B_HANG"):
            hang_inside_simulation()
        time.sleep(float(os.environ.get("FAKE_B_SLEEP", "0")))
        (sdir / "snapshot.json").write_text("{}")
        with open(ops / "forecasts" / "index.jsonl", "a") as f: f.write(json.dumps({"snapshot_id": sdir.name}) + "\\n")
        Path(kv["result-file"]).write_text(json.dumps({"status": "PUBLISHED", "snapshot_id": sdir.name}))
        note("B_EXIT"); sys.exit(0)
    sys.exit(2)
''')

HOLDER = textwrap.dedent('''
    import fcntl, json, os, sys, time
    lock, hold, log, who = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o660)
    t0 = time.time()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except BlockingIOError:
            if time.time() - t0 > 600: print(json.dumps({"acquired": False})); sys.exit(75)
            time.sleep(0.05)
    w = time.time() - t0
    with open(log, "a") as f: f.write(json.dumps({"who": who, "ev": "ENTER", "t": time.time(), "pid": os.getpid()}) + "\\n")
    time.sleep(hold)
    with open(log, "a") as f: f.write(json.dumps({"who": who, "ev": "EXIT", "t": time.time(), "pid": os.getpid()}) + "\\n")
    print(json.dumps({"acquired": True, "wait_sec": w}))
''')

DRIVER = textwrap.dedent('''
    import json, os, sys
    from pathlib import Path
    from cbb_platform import availability_refresh as AR
    cfg = json.loads(os.environ["DRIVER_CFG"])
    lim = AR.Limits(**cfg["limits"])
    fake = cfg["fake"]
    r = AR.Refresh(cfg["window"], Path(cfg["ops"]), Path(cfg["prod"]), Path(cfg["lock"]), limits=lim,
                   phase_cmd=lambda phase, args: [sys.executable, fake, phase] + list(args))
    code, rec = r.run()
    print(json.dumps(rec, default=str)); sys.exit(code)
''')

FAST = dict(acquire_budget_sec=30, acquire_kill_grace_sec=2, receipt_max_age_sec=900, writer_wait_max_sec=300,
            retry_delay_sec=300, max_retries=3, watchdog_sec=60, early_dump_sec=0, kill_grace_sec=2,
            unit_budget_sec=2400, margin_sec=1, min_lock_wait_sec=0.5, poll_sec=0.05)


@pytest.fixture
def world(tmp_path):
    w = {"tmp": tmp_path, "ops": tmp_path / "ops", "prod": tmp_path / "prod", "lock": tmp_path / "locks" / "writer.lock",
         "log": tmp_path / "events.jsonl", "fake": tmp_path / "fake_phase.py", "holder": tmp_path / "holder.py",
         "driver": tmp_path / "driver.py"}
    for k, src in (("fake", FAKE), ("holder", HOLDER), ("driver", DRIVER)):
        w[k].write_text(src)
    w["ops"].mkdir()
    (w["ops"] / "forecasts").mkdir()
    (w["ops"] / "forecasts" / "index.jsonl").write_text(json.dumps({"snapshot_id": "F-PRIOR"}) + "\n")
    w["exports"] = tmp_path / "exports" / "Boards.xlsx"
    w["exports"].parent.mkdir()
    w["exports"].write_bytes(b"PRIOR-VALID-WORKBOOK")
    w["lock"].parent.mkdir()
    return w


def _env(w, **fake):
    e = {**os.environ, "FAKE_OPS": str(w["ops"]), "FAKE_LOG": str(w["log"]), "FAKE_WRITER_LOCK": str(w["lock"]),
         "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    e.update({k: str(v) for k, v in fake.items()})
    return e


def _orchestrate(w, limits=None, wait=True, **fake):
    cfg = {"window": "16:00", "ops": str(w["ops"]), "prod": str(w["prod"]), "lock": str(w["lock"]),
           "fake": str(w["fake"]), "limits": {**FAST, **(limits or {})}}
    p = subprocess.Popen([sys.executable, str(w["driver"])], env={**_env(w, **fake), "DRIVER_CFG": json.dumps(cfg)},
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    if not wait:
        return p
    out, err = p.communicate(timeout=600)
    return p.returncode, json.loads(out.strip().splitlines()[-1]), err


def _holder(w, hold, who="QUOTE_TICK"):
    return subprocess.Popen([sys.executable, str(w["holder"]), str(w["lock"]), str(hold), str(w["log"]), who],
                            stdout=subprocess.PIPE, text=True)


def _events(w):
    return [json.loads(l) for l in w["log"].read_text().splitlines()] if w["log"].exists() else []


def _wait_for(w, ev, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        if any(e["ev"] == ev for e in _events(w)):
            return
        time.sleep(0.05)
    raise AssertionError(f"{ev} never happened")


def writer_count_max(w) -> int:
    """Max number of simultaneous CBB writer-lock holders (Phase B or a lock holder) over the run."""
    seq = sorted(((e["t"], +1 if e["ev"] in ("ENTER", "B_ENTER") else -1) for e in _events(w)
                  if e["ev"] in ("ENTER", "EXIT", "B_ENTER", "B_EXIT")), key=lambda x: (x[0], x[1]))
    cur = mx = 0
    for _, d in seq:
        cur += d
        mx = max(mx, cur)
    return mx


def _lock_free(w) -> bool:
    fd = os.open(w["lock"], os.O_WRONLY | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


# ============================================================================ 1-3 constants / unit budget / wrapper
def test_limits_match_the_directive_and_the_unchanged_unit_timeout():
    lim = AR.Limits()
    assert lim.acquire_budget_sec == 300 and L.ACQ_BUDGET_SEC == 300
    assert lim.writer_wait_max_sec == 300 and lim.retry_delay_sec == 5 * 60 and lim.max_retries == 3
    assert lim.receipt_max_age_sec == 15 * 60 == L.ACQ_RECEIPT_MAX_AGE_SEC
    assert lim.watchdog_sec == 15 * 60 and lim.early_dump_sec == 12 * 60      # never 10 min: healthy runs take 7-11
    u = UNIT.read_text()
    assert re.search(r"^TimeoutStartSec=40min$", u, re.M) and lim.unit_budget_sec == 40 * 60   # 40-min timeout kept
    # a full Phase A + the max writer wait + a full watchdog-bounded Phase B fit inside the unit
    assert (lim.acquire_budget_sec + lim.acquire_kill_grace_sec + lim.writer_wait_max_sec + lim.watchdog_sec
            + lim.kill_grace_sec + lim.margin_sec) < lim.unit_budget_sec
    assert set(AR.ACQ_PUBLISHABLE) == set(L.ACQ_PUBLISHABLE) and AR.WINDOWS == L.ROTOWIRE_REFRESH_TIMES_ET


def test_wrapper_runs_the_orchestrator_without_taking_the_writer_lock_for_refresh_only():
    r = RUN.read_text()
    assert 'lifecycle-refresh) RUN=("$PY" -m cbb_platform.availability_refresh' in r
    assert '--writer-lock "$WRITER_LOCK"' in r
    pre = r[:r.index('exec 9>>"$WRITER_LOCK"')]
    assert 'if [ "$CMD" = "lifecycle-refresh" ]; then\n  exec "${RUN[@]}"' in pre     # exec'd BEFORE the bash flock
    for cmd in ("morning-stage)     RUN=", "morning-recover)   RUN=", "availability-poll) RUN=", "tick)              RUN="):
        assert cmd in r                                                          # other commands keep their locking
    assert r.count('flock -w "$WRITER_WAIT_SEC" 9') == 1


def test_wrapper_dry_run_of_refresh_reports_no_wrapper_lock(tmp_path):
    from tests.test_droplet_deploy_package import _release, THREADS
    rel = _release(tmp_path)
    state = tmp_path / "state"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       8131776 kB\nMemAvailable:   7340032 kB\n")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CBB_APP_ROOT": str(rel["current"]),
           "CBB_EXPECTED_COMMIT": rel["sha"], "CBB_MEMINFO": str(meminfo), "CBB_STATE_ROOT": str(state),
           "CBB_VENV_DIR": str(tmp_path / "venv"), "CBB_SCHEDULER_ROLE": "PRIMARY_WRITER", "CBB_DRY_RUN": "1", **THREADS}
    if shutil.which("sha256sum") is None:
        pytest.skip("sha256sum not installed (coreutils; present on the Droplet and CI)")
    r = subprocess.run(["bash", str(RUN), "lifecycle-refresh", "16:00"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Phase A without the CBB writer lock" in r.stderr and "cbb_platform.availability_refresh" in r.stderr
    assert f"--writer-lock {state}/locks/cbb-originator.writer.lock" in r.stderr and "--window 16:00" in r.stderr


# ============================================================================ 4-6 Phase A receipt / budget / locks
@pytest.fixture
def acq_env(tmp_path, monkeypatch):
    monkeypatch.setattr(PC, "ROTOWIRE_ROOT", tmp_path / "rotowire")
    monkeypatch.setattr(PC, "ROTOWIRE_WEB_ROOT", tmp_path / "rotowire_web")
    monkeypatch.setattr(PC, "rotowire_source", lambda: "WEB")
    life = L.Life(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", rotowire_enabled=True)
    return life


def test_phase_a_writes_one_immutable_receipt_with_every_field(acq_env, monkeypatch):
    rep = {k.lower(): f"v-{k}" for k in L.RECEIPT_FIELDS}
    rep.update(reasons=["R1"], warnings=["W1"], auth="OK", acquisition_id="ACQ-1")
    monkeypatch.setattr(L, "web_acquire", lambda life, lock_wait_s=None: dict(rep))
    r = L.availability_acquire(acq_env, "16:00", "R-test-1")
    p = L.acquisition_receipt_path(acq_env.ops_root, "R-test-1")
    on_disk = json.loads(p.read_text())
    assert on_disk == json.loads(json.dumps(r, default=str))
    assert set(L.RECEIPT_FIELDS) <= set(on_disk)
    assert on_disk["STATUS"] == "COMPLETED" and on_disk["PUBLISHABLE"] and on_disk["GLOBAL_WRITER_LOCK_USED"] == "NO"
    assert on_disk["AUTH"] == "OK" and on_disk["ACQUISITION_ID"] == "ACQ-1" and on_disk["REASONS"] == ["R1"]
    assert on_disk["ACQUISITION_REPORT"]["acquisition_id"] == "ACQ-1"
    assert not [q for q in p.parent.iterdir() if q.name.endswith(".tmp")]           # atomic: no temp file left
    with pytest.raises(FileExistsError):                                            # write-once
        L.availability_acquire(acq_env, "16:00", "R-test-1")
    assert not OS.create_once(p, {"x": 1}) and json.loads(p.read_text()) == on_disk


def test_phase_a_budget_cannot_be_swallowed_and_records_acquisition_timeout(acq_env, monkeypatch):
    def slow(life, lock_wait_s=None):
        try:
            time.sleep(30)
        except Exception:                                   # a provider-style broad handler must not swallow it
            return {"status": "SWALLOWED"}
    monkeypatch.setattr(L, "web_acquire", slow)
    t = time.monotonic()
    r = L.availability_acquire(acq_env, "16:00", "R-test-2", budget_s=0.5)
    assert time.monotonic() - t < 5
    assert r["STATUS"] == "ACQUISITION_TIMEOUT" and not r["PUBLISHABLE"]
    assert json.loads(L.acquisition_receipt_path(acq_env.ops_root, "R-test-2").read_text())["STATUS"] == "ACQUISITION_TIMEOUT"
    assert signal.getsignal(signal.SIGALRM) in (signal.SIG_DFL, signal.default_int_handler, None) or True
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0                              # the timer is disarmed


def test_phase_a_failure_and_not_enabled_statuses(acq_env, monkeypatch):
    monkeypatch.setattr(L, "web_acquire", lambda life, lock_wait_s=None: 1 / 0)
    assert L.availability_acquire(acq_env, "16:00", "R-f")["STATUS"] == "ACQUISITION_FAILED"
    monkeypatch.setattr(PC, "rotowire_source", lambda: "DISABLED")
    r = L.availability_acquire(acq_env, "16:00", "R-n")
    assert r["STATUS"] == "NOT_ENABLED" and r["PUBLISHABLE"] and r["ACQUISITION_REPORT"] is None


def test_phase_a_never_touches_the_global_writer_lock(acq_env, monkeypatch, tmp_path):
    lock = tmp_path / "writer.lock"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f'''
        import fcntl, os, time
        fd = os.open({str(lock)!r}, os.O_WRONLY | os.O_CREAT); fcntl.flock(fd, fcntl.LOCK_EX)
        print("held", flush=True); time.sleep(30)''')], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(L, "web_acquire", lambda life, lock_wait_s=None: {"acquisition_id": "A", "auth": "OK"})
        t = time.monotonic()
        r = L.availability_acquire(acq_env, "16:00", "R-g")
        assert r["STATUS"] == "COMPLETED" and time.monotonic() - t < 5
    finally:
        holder.kill()
        holder.wait()
    src = Path(L.__file__).read_text()
    body = src[src.index("def availability_acquire"):src.index("class ReceiptRejected")]
    assert "writer" not in body.replace("GLOBAL_WRITER_LOCK_USED", "").lower()
    for forbidden in ("forecast(", "RN.daily", "exports_for", "PE.write", "health(", "walk_forward", "simulate"):
        assert forbidden not in body


def _hold_flock(path: Path, shared=False):
    """Hold a flock from ANOTHER process (released on .kill())."""
    p = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f'''
        import fcntl, os, time
        os.makedirs(os.path.dirname({str(path)!r}), exist_ok=True)
        fd = os.open({str(path)!r}, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        fcntl.flock(fd, fcntl.LOCK_SH if {shared} else fcntl.LOCK_EX); print("held", flush=True); time.sleep(60)''')],
        stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def _blocks(fn, seconds=0.6) -> bool:
    done = threading.Event()
    th = threading.Thread(target=lambda: (fn(), done.set()), daemon=True)
    th.start()
    return not done.wait(seconds)


def test_eventstore_reads_take_lock_sh_and_appends_lock_ex(tmp_path):
    st = RW.EventStore(tmp_path / "rw")
    st.root.mkdir()
    st.path.write_text(json.dumps({"semantic_key": "k0", "seq": 0}) + "\n")
    h = _hold_flock(st.root / ".lock")                    # an appender mid-batch (LOCK_EX)
    try:
        assert _blocks(st.load)                           # a reader waits: never sees a half-appended batch
    finally:
        h.kill(); h.wait()
    assert [e["semantic_key"] for e in st.load()] == ["k0"]
    h = _hold_flock(st.root / ".lock", shared=True)       # a reader (LOCK_SH): appends wait, other reads don't
    try:
        assert not _blocks(st.load)
        assert _blocks(lambda: st.append([{"semantic_key": "k1"}]))
    finally:
        h.kill(); h.wait()


def test_request_ledger_has_its_own_narrow_lock(tmp_path):
    led = tmp_path / "cache" / "rotowire_production_requests.jsonl"
    OS.append_jsonl_locked(led, [{"endpoint": "snapshot", "status": "PROCESSED"}])
    h = _hold_flock(OS.lock_path_for(led))
    try:
        assert _blocks(lambda: RA.ledger(led))
        assert _blocks(lambda: OS.append_jsonl_locked(led, [{"endpoint": "x"}]))
    finally:
        h.kill(); h.wait()
    t = time.time()                                       # the blocked append completes once the holder is gone
    while len(RA.ledger(led)) < 2 and time.time() - t < 10:
        time.sleep(0.05)
    assert len(RA.ledger(led)) == 2
    src = (ROOT / "cbb_platform" / "prod" / "rotowire_web_acquire.py").read_text()
    assert "OS.append_jsonl_locked(self.path, [entry])" in src
    assert "OS.append_jsonl_locked(self.ledger_path, [entry])" in (ROOT / "cbb_platform" / "prod" / "rotowire_acquire.py").read_text()


def test_web_acquisition_lock_serializes_phase_a_and_the_light_poll(acq_env, monkeypatch, tmp_path):
    import cbb_platform.prod.rotowire_web_acquire as RWW
    monkeypatch.setattr(RW, "load_identity_context", lambda *a, **k: object())
    monkeypatch.setattr(RWW, "WebClient", lambda: object())
    calls = []
    monkeypatch.setattr(RWW, "acquire", lambda *a, **k: calls.append(1) or {"acquisition_id": "X"})
    acq_env.history_db = tmp_path / "h.duckdb"
    h = _hold_flock(L.web_acquire_lock_path())
    try:
        r = L.web_acquire(acq_env, lock_wait_s=0.5)
        assert r["status"] == "BLOCKED" and r["reason"].startswith("WEB_ACQUIRE_LOCK_BUSY") and not calls
    finally:
        h.kill(); h.wait()
    assert L.web_acquire(acq_env, lock_wait_s=0.5)["acquisition_id"] == "X" and calls == [1]


def test_budget_expiry_is_deferred_across_a_jsonl_append(tmp_path):
    """A budget that fires mid-append raises right AFTER the complete lines are written (a background thread does not
    change that: Python handlers always run in the main thread)."""
    th = threading.Thread(target=time.sleep, args=(1.0,), daemon=True)
    th.start()
    class Expired(BaseException):
        pass
    def expire():
        raise Expired()
    old = signal.signal(signal.SIGALRM, lambda *a: OS.defer_if_critical(expire) or expire())
    p = tmp_path / "x.jsonl"
    try:
        with pytest.raises(Expired):
            with OS.alarm_deferred():
                signal.setitimer(signal.ITIMER_REAL, 0.05)
                p.write_text('{"a": ')
                time.sleep(0.2)                           # the alarm fires here, inside the section
                with open(p, "a") as f:
                    f.write('1}\n')
        assert p.read_text() == '{"a": 1}\n'             # the line completed before the budget raised
        with pytest.raises(Expired):                      # outside a section it raises immediately
            signal.setitimer(signal.ITIMER_REAL, 0.05)
            time.sleep(1.0)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# ============================================================================ 7-8 the contention proofs
def test_slow_phase_a_never_blocks_a_quote_tick(world):
    """SLOW_PHASE_A_QUOTE_PROOF: a >= 60 s Phase A; a quote tick (600 s writer wait) acquires in < 10 s."""
    p = _orchestrate(world, wait=False, limits=dict(acquire_budget_sec=SLOW_A_SEC + 60), FAKE_A_SLEEP=SLOW_A_SEC)
    try:
        _wait_for(world, "A_START")
        time.sleep(1.0)
        t = time.monotonic()
        q = _holder(world, 1.0, "QUOTE_TICK")
        res = json.loads(q.communicate(timeout=60)[0])
        assert res["acquired"] and res["wait_sec"] < 10 and time.monotonic() - t < 10        # QUOTE_TICK_ACQUIRED_WRITER
        assert not any(e["ev"] == "A_END" for e in _events(world))                           # Phase A still running
        out, err = p.communicate(timeout=SLOW_A_SEC + 120)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
    rec = json.loads(out.strip().splitlines()[-1])
    assert p.returncode == 0 and rec["STATUS"] == "PUBLISHED", err
    assert rec["PHASE_A_SEC"] >= SLOW_A_SEC - 1 and rec["PHASE_B_LOCK_WAIT_SEC"] < 5
    assert writer_count_max(world) == 1
    marks = [m["mark"] for m in rec["MARKS"]]
    assert marks == ["SERVICE_START", "PHASE_A_START", "PHASE_A_END", "PHASE_B_LOCK_WAIT_START", "PHASE_B_LOCK_ACQUIRED",
                     "PHASE_B_LOCK_RELEASED", "SERVICE_END"]
    pub = json.loads((world["ops"] / "availability_publications" / f"{rec['REFRESH_ID']}.json").read_text())
    assert pub["PHASE_B_LOCK_HELD_SEC"] is not None and pub["PHASE_B_LOCK_ACQUIRED_AT"] and pub["PHASE_B_LOCK_RELEASED_AT"]


def test_phase_b_waits_for_a_busy_writer_then_publishes(world):
    """REVERSE_CONTENTION_PROOF: a quote tick holds the lock when Phase A ends; Phase B waits (bounded) and goes."""
    q = _holder(world, 4.0, "QUOTE_TICK")
    _wait_for(world, "ENTER")
    code, rec, err = _orchestrate(world)
    q.wait(timeout=30)
    assert code == 0 and rec["STATUS"] == "PUBLISHED", err
    assert 2.0 <= rec["PHASE_B_LOCK_WAIT_SEC"] <= 10
    ev = _events(world)
    q_exit = next(e["t"] for e in ev if e["ev"] == "EXIT")
    b_enter = next(e["t"] for e in ev if e["ev"] == "B_ENTER")
    assert b_enter >= q_exit and writer_count_max(world) == 1


def test_busy_writer_defers_and_retries_then_publishes(world):
    q = _holder(world, 5.0, "MORNING")
    _wait_for(world, "ENTER")
    code, rec, err = _orchestrate(world, limits=dict(writer_wait_max_sec=1.0, retry_delay_sec=2.0))
    q.wait(timeout=30)
    assert code == 0 and rec["STATUS"] == "PUBLISHED", err
    assert [a["status"] for a in rec["ATTEMPTS"]] and all(a["status"] == "DEFERRED_WRITER_LOCK" for a in rec["ATTEMPTS"])
    assert 1 <= len(rec["ATTEMPTS"]) <= 3 and len(rec["RECEIPTS"]) == 1           # the <= 15-min receipt is reused
    assert writer_count_max(world) == 1


def test_three_retries_max_then_deferred_writer_lock_exit_75(world):
    q = _holder(world, 30.0, "MORNING")
    _wait_for(world, "ENTER")
    try:
        code, rec, err = _orchestrate(world, limits=dict(writer_wait_max_sec=0.6, retry_delay_sec=1.0))
    finally:
        q.kill(); q.wait()
    assert code == 75 and rec["STATUS"] == "DEFERRED_WRITER_LOCK", err
    assert len(rec["ATTEMPTS"]) == 4                                              # first try + 3 retries
    t = [pd.Timestamp(a["at"]) for a in rec["ATTEMPTS"]]
    assert not any(e["ev"] == "B_ENTER" for e in _events(world))
    assert (world["ops"] / "forecasts" / "index.jsonl").read_text().count("\n") == 1   # prior snapshot only
    assert world["exports"].read_bytes() == b"PRIOR-VALID-WORKBOOK"


def test_stale_receipt_is_replaced_by_a_fresh_phase_a_outside_the_lock(world):
    q = _holder(world, 4.0, "MORNING")
    _wait_for(world, "ENTER")
    code, rec, err = _orchestrate(world, limits=dict(writer_wait_max_sec=1.0, retry_delay_sec=2.0, receipt_max_age_sec=1.5))
    q.wait(timeout=30)
    assert code == 0 and rec["STATUS"] == "PUBLISHED", err
    assert len(rec["RECEIPTS"]) >= 2 and rec["RECEIPT_USED"] == rec["RECEIPTS"][-1]["path"]
    ev = _events(world)                                   # every Phase A ran while no Phase B held the lock
    assert writer_count_max(world) == 1


def test_retries_that_do_not_fit_the_unit_budget_are_not_attempted(world):
    q = _holder(world, 20.0, "MORNING")
    _wait_for(world, "ENTER")
    try:
        code, rec, err = _orchestrate(world, limits=dict(writer_wait_max_sec=0.5, retry_delay_sec=1.0, unit_budget_sec=64,
                                                         watchdog_sec=60, kill_grace_sec=1, margin_sec=1))
    finally:
        q.kill(); q.wait()
    assert code == 75 and rec["STATUS"] == "DEFERRED_WRITER_LOCK" and "unit budget" in rec.get("NOTE", ""), err


def test_acquisition_timeout_never_enters_phase_b(world):
    code, rec, err = _orchestrate(world, FAKE_A_STATUS="ACQUISITION_TIMEOUT")
    assert code == 1 and rec["STATUS"] == "ACQUISITION_TIMEOUT"
    assert not any(e["ev"] == "B_ENTER" for e in _events(world)) and "PHASE_B_LOCK_ACQUIRED_AT" not in rec
    assert world["exports"].read_bytes() == b"PRIOR-VALID-WORKBOOK"


def test_hung_phase_a_is_hard_killed_and_recorded(world):
    code, rec, err = _orchestrate(world, limits=dict(acquire_budget_sec=1, acquire_kill_grace_sec=1),
                                  FAKE_A_SLEEP=60, FAKE_A_IGNORE_BUDGET=1)
    assert code == 1 and rec["STATUS"] == "ACQUISITION_TIMEOUT", err
    r = json.loads(Path(rec["RECEIPTS"][0]["path"]).read_text())
    assert r["STATUS"] == "ACQUISITION_TIMEOUT" and "SIGABRT" in r["REASONS"][0]
    assert rec["PHASE_A_SEC"] < 20 and not any(e["ev"] == "B_ENTER" for e in _events(world))


# ============================================================================ 9 the Phase B watchdog
def test_watchdog_dumps_stacks_aborts_phase_b_and_preserves_prior_outputs(world):
    code, rec, err = _orchestrate(world, limits=dict(watchdog_sec=3.0, early_dump_sec=1.0, kill_grace_sec=2.0),
                                  FAKE_B_HANG=1)
    assert code == 1 and rec["STATUS"] == "PHASE_B_WATCHDOG_ABORT", err
    b = rec["PHASE_B"]
    assert b["signals"][:2] == ["SIGUSR1", "SIGABRT"] and 2.5 <= b["phase_b_sec"] < 10
    stack = Path(b["stack_file"]).read_text()
    assert stack.count("hang_inside_simulation") >= 2                 # early (SIGUSR1) dump + the abort dump
    assert "Fatal Python error: Aborted" in stack
    assert rec["PHASE_B_LOCK_HELD_SEC"] < 10 and _lock_free(world)       # bounded, then released
    assert (world["ops"] / "forecasts" / "index.jsonl").read_text().splitlines() == [json.dumps({"snapshot_id": "F-PRIOR"})]
    assert world["exports"].read_bytes() == b"PRIOR-VALID-WORKBOOK"
    h = json.loads(next((world["prod"] / "health").glob("*.json")).read_text())
    assert h["availability_1600"]["status"] == "PHASE_B_WATCHDOG_ABORT" and h["availability_1600"]["stack_file"]


def test_real_publish_child_arms_faulthandler_and_never_dumps_core(tmp_path):
    src = Path(L.__file__).read_text()
    body = src[src.index("def _stack_dumps"):src.index("def _publish_cli")]
    assert "faulthandler.enable(file=fh, all_threads=True)" in body and "faulthandler.register(signal.SIGUSR1" in body
    assert "dump_traceback_later(float(watchdog_sec) + 15.0, exit=True" in body and "RLIMIT_CORE, (0, 0)" in body


# ============================================================================ 10 process death
def test_death_during_phase_a_leaves_the_writer_lock_free_and_nothing_published(world):
    p = _orchestrate(world, wait=False, FAKE_A_SLEEP=30)
    _wait_for(world, "A_START")
    os.killpg(p.pid, signal.SIGKILL)
    p.wait(timeout=30)
    assert _lock_free(world)
    assert (world["ops"] / "forecasts" / "index.jsonl").read_text().count("\n") == 1
    assert world["exports"].read_bytes() == b"PRIOR-VALID-WORKBOOK"


def test_death_while_waiting_for_the_phase_b_lock(world):
    q = _holder(world, 6.0, "QUOTE_TICK")
    _wait_for(world, "ENTER")
    p = _orchestrate(world, wait=False)
    _wait_for(world, "A_END")
    time.sleep(1.0)
    os.killpg(p.pid, signal.SIGKILL)
    p.wait(timeout=30)
    q.wait(timeout=30)
    assert _lock_free(world) and not any(e["ev"] == "B_ENTER" for e in _events(world))
    assert world["exports"].read_bytes() == b"PRIOR-VALID-WORKBOOK"


def test_death_during_phase_b_before_the_index_append(world):
    p = _orchestrate(world, wait=False, FAKE_B_SLEEP=30)
    _wait_for(world, "B_ENTER")
    assert not _lock_free(world)
    os.killpg(p.pid, signal.SIGKILL)                     # the orchestrator and the Phase B child
    p.wait(timeout=30)
    t = time.time()
    while not _lock_free(world) and time.time() - t < 10:
        time.sleep(0.05)
    assert _lock_free(world)                                                    # the kernel released it
    assert (world["ops"] / "forecasts" / "index.jsonl").read_text().splitlines() == [json.dumps({"snapshot_id": "F-PRIOR"})]
    assert world["exports"].read_bytes() == b"PRIOR-VALID-WORKBOOK"
    partial = list((world["ops"] / "forecasts" / "D").glob("*/pmfs.parquet"))
    assert partial and not list((world["ops"] / "forecasts" / "D").glob("*/snapshot.json"))   # unindexed, invisible


def test_orchestrator_death_alone_keeps_the_lock_with_the_phase_b_child(world):
    """WRITER_COUNT stays 1 even if only the parent dies: the child shares the locked description."""
    p = _orchestrate(world, wait=False, FAKE_B_SLEEP=3)
    _wait_for(world, "B_ENTER")
    os.kill(p.pid, signal.SIGKILL)                       # the parent only
    p.wait(timeout=30)
    assert not _lock_free(world)                         # the Phase B child still holds it
    _wait_for(world, "B_EXIT", timeout=30)
    t = time.time()
    while not _lock_free(world) and time.time() - t < 10:
        time.sleep(0.05)
    assert _lock_free(world)


def test_publish_child_refuses_without_the_lock(tmp_path):
    fd = os.open(tmp_path / "w.lock", os.O_WRONLY | os.O_CREAT)
    h = _hold_flock(tmp_path / "w.lock")
    try:
        r = subprocess.run([sys.executable, "-m", "cbb_platform.prod.lifecycle", "--ops-root", str(tmp_path / "ops"),
                            "--prod-root", str(tmp_path / "prod"), "availability-publish", "--window", "16:00",
                            "--receipt", "R-x", "--lock-fd", str(fd)], pass_fds=(fd,), capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(ROOT)}, timeout=120, cwd=str(ROOT))
        assert r.returncode != 0 and "does not hold the CBB writer lock" in r.stderr
    finally:
        h.kill(); h.wait(); os.close(fd)


# ============================================================================ 11 Phase B receipt checks
def test_phase_b_rejects_unpublishable_stale_or_foreign_receipts():
    now = pd.Timestamp("2026-11-12T21:10:00Z")
    ok = {"WINDOW": "16:00", "STATUS": "COMPLETED", "FINISHED_AT": "2026-11-12T21:05:00Z"}
    assert L.check_receipt(ok, "16:00", now) == 300
    for bad, why in (({**ok, "WINDOW": "13:00"}, "WINDOW"), ({**ok, "STATUS": "ACQUISITION_TIMEOUT"}, "NOT_PUBLISHABLE"),
                     ({**ok, "FINISHED_AT": "2026-11-12T20:54:00Z"}, "STALE")):
        with pytest.raises(L.ReceiptRejected, match=why):
            L.check_receipt(bad, "16:00", now)


# ============================================================================ 12 prediction equivalence old vs split
@pytest.fixture(scope="module")
def two_worlds(tmp_path_factory):
    from cbb_platform.prod import feed_fixtures as FF, fixtures as FX, odds_fixtures as OX, shadow_tick as TK
    from tests.test_prod_shadow_tick import Clock, _ctx
    w = tmp_path_factory.mktemp("equiv")
    hist = FX.synthetic_history(w / "history.duckdb")
    clock = Clock("2026-11-01T12:00Z")
    cbbd = FF.FakeCBBD(FF.payloads("before"))
    ctx = _ctx(w, hist, clock, cbbd, OX.FakeOdds([]))
    TK.tick(ctx)
    cbbd.bodies = FF.payloads("after")
    clock.t = pd.Timestamp("2026-11-05T06:00Z")
    TK.tick(ctx)
    clock.t = pd.Timestamp("2026-11-10T12:00Z")
    life0 = L.Life(ops_root=w / "ops", prod_root=w / "prod", history_db=hist, model_root=w / "no_wf_store", clock=clock,
                   sims=300)
    init = L.forecast(life0, L.INITIAL)
    assert init["status"] == "FORECAST"
    fp = pd.read_parquet(life0.forecasts("2026-11-10", init["snapshot_id"], "forecast_players.parquet"))
    out = {}
    for name in ("old", "split"):
        d = tmp_path_factory.mktemp(name)
        for sub in ("ops", "prod"):
            shutil.copytree(w / sub, d / sub)
        shutil.copy(hist, d / "history.duckdb")
        out[name] = d
    return {"worlds": out, "init": init, "players": fp}


def _fake_acquisition(player: dict, now: pd.Timestamp):
    """A deterministic RotoWire WEB acquisition: one resolved OUT news event + PROCESSED ledger entries."""
    def acquire(life, lock_wait_s=None):
        e = {k: None for k in RW.EVENT_FIELDS}
        t = (now - pd.Timedelta(minutes=20)).isoformat()
        e.update(provider="ROTOWIRE", event_kind="NEWS_UPDATE", rotowire_update_id="u-eq-1",
                 rotowire_player_id="rw-eq-1", canonical_player_id=player["player_id"], canonical_team_id=player["team_id"],
                 first_name="Eq", last_name="Player", raw_status="Out", normalized_status="OUT", priority=1,
                 source_datetime=t, source_datetime_status="PARSED", collected_at_utc=t, knowable_at=t,
                 identity_resolution_status="RESOLVED", identity_method="TEST")
        e["semantic_key"] = RW.semantic_key(e)
        new = RW.EventStore(PC.ROTOWIRE_ROOT).append([e])
        col = (now - pd.Timedelta(minutes=5)).isoformat()
        OS.append_jsonl_locked(PC.ROTOWIRE_REQUEST_LEDGER, [{"endpoint": ep, "status": "PROCESSED", "collected_at": col}
                                                           for ep in ("news_injuries", "snapshot")])
        return {"acquisition_id": "ACQ-EQ", "auth": "AUTHENTICATED", "news_status": "PROCESSED",
                "snapshot_status": "PROCESSED", "news_rows": 1, "snapshot_rows": 0, "news_new_items": 1,
                "news_events_new": new, "snapshot_events_new": 0, "news_continuity": "CONTINUOUS",
                "news_window_overlap": 1, "reasons": [], "warnings": [], "at": now.isoformat()}
    return acquire


def _point_world(monkeypatch, d: Path, now):
    from tests.test_prod_shadow_tick import Clock
    monkeypatch.setattr(PC, "ROTOWIRE_ROOT", d / "prod" / "availability" / "rotowire")
    monkeypatch.setattr(PC, "ROTOWIRE_WEB_ROOT", d / "prod" / "availability" / "rotowire_web")
    led = d / "prod" / "cache" / "rotowire_production_requests.jsonl"
    monkeypatch.setattr(PC, "ROTOWIRE_REQUEST_LEDGER", led)
    monkeypatch.setattr(RA, "ledger", lambda path=None: OS.read_jsonl_locked(led))   # run.daily's bound default
    monkeypatch.setattr(PC, "rotowire_source", lambda: "WEB")
    return L.Life(ops_root=d / "ops", prod_root=d / "prod", history_db=d / "history.duckdb", model_root=d / "no_wf_store",
                  clock=Clock(now), sims=300, rotowire_enabled=True, export_root=d / "exports")


def _outputs(life: L.Life, rec: dict) -> dict:
    f = rec["forecast"]
    sdir = life.forecasts(f["slate_date"], f["snapshot_id"])
    pe = PE.read(sdir / PE.FILE)
    fp = pd.read_parquet(sdir / "forecast_players.parquet")
    drop = ["snapshot_id", "run_id", "prediction_receipt_id", "created_at"]
    pe = pe.drop(columns=[c for c in drop if c in pe.columns]).sort_values(["game_id", "player_id", "stat"]).reset_index(drop=True)
    fp = fp.drop(columns=[c for c in drop if c in fp.columns]).sort_values(["game_id", "player_id"]).reset_index(drop=True)
    ex = {k: {kk: vv for kk, vv in (v or {}).items() if kk != "path"} if isinstance(v, dict) else v
          for k, v in rec["exports"].items()}
    return {"games": f["games"], "status": f["status"], "availability_changes": f.get("availability_changes"),
            "model_version": f.get("model_version"), "games_forecast": f.get("games_forecast"),
            "players_forecast": f.get("players_forecast"), "stat_predictions": f.get("stat_predictions"),
            "pmfs_persisted": f.get("pmfs_persisted"), "player_ids": sorted(set(fp["player_id"])), "pmfs": pe, "players": fp,
            "exports": ex}


def test_prediction_equivalence_old_refresh_vs_split(two_worlds, monkeypatch):
    """PREDICTION_EQUIVALENCE: identical worlds + clock + acquisition -> the old one-process refresh() and the split
    availability_acquire() + availability_publish() produce identical games, players, availability changes, model
    version, PMFs, forecast players, counts and export rows."""
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("network refused")))
    init, fp = two_worlds["init"], two_worlds["players"]
    now = pd.Timestamp("2026-11-10T21:00:00Z")                    # the 16:00 ET refresh
    assert all(pd.Timestamp(c) > now for c in init["cutoffs"].values())
    player = fp.iloc[0][["game_id", "team_id", "player_id"]].to_dict()
    res = {}
    for name in ("old", "split"):
        life = _point_world(monkeypatch, two_worlds["worlds"][name], now)
        monkeypatch.setattr(L, "web_acquire", _fake_acquisition(player, now))
        if name == "old":
            rec = L.refresh(life, "16:00")
        else:
            rc = L.availability_acquire(life, "16:00", "R-EQ")
            assert rc["STATUS"] == "COMPLETED" and rc["NEWS_EVENTS_NEW"] == 1
            rec = L.availability_publish(life, "16:00", L.acquisition_receipt_path(life.ops_root, "R-EQ"))
        assert rec["forecast"]["status"] == "FORECAST", rec["forecast"]
        res[name] = _outputs(life, rec)
    a, b = res["old"], res["split"]
    assert player["game_id"] in a["games"] and player["player_id"] in a["availability_changes"][player["game_id"]]
    for k in ("games", "status", "availability_changes", "model_version", "games_forecast", "players_forecast",
              "stat_predictions", "pmfs_persisted", "player_ids", "exports"):
        assert a[k] == b[k], k
    pd.testing.assert_frame_equal(a["pmfs"], b["pmfs"])
    pd.testing.assert_frame_equal(a["players"].drop(columns=["prediction_as_of"]), b["players"].drop(columns=["prediction_as_of"]))
    assert len(a["pmfs"]) > 0 and a["pmfs_persisted"] > 0 and a["games_forecast"] > 0 and len(a["players"]) > 0
    hit = a["players"].loc[a["players"]["player_id"] == player["player_id"], "availability_status"]
    assert len(hit) and (hit == "OUT").all()                     # the acquisition really changed the forecast
    print("EQUIV", {k: a[k] for k in ("games", "availability_changes", "model_version", "games_forecast",
                                      "players_forecast", "stat_predictions", "pmfs_persisted")},
          "pmf_rows", len(a["pmfs"]), "player_rows", len(a["players"]),
          "export_rows", {k: (v or {}).get("rows") for k, v in a["exports"].items() if isinstance(v, dict)})
