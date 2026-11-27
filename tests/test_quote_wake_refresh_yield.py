"""Stage 3 (2026-11-27): a 10-minute quote wake that finds an availability refresh WAITING for the writer lock skips only
its export rebuild (the refresh's Phase B rebuilds the same day's exports under the same lock minutes later).

Demonstrated defect (production journal 2026-11-20..26): every 10:00/13:00/16:00/18:00 ET refresh starts in the same second
as a quote wake; the wake holds the writer lock 110-270 s (median 146 s), ~96% of it rebuilding unchanged exports
(read-only profile of an idle wake on production data: exports 137.1 s of 142.8 s, every workbook `rewritten: false`),
which Phase B then rebuilds again. The priority marker made only NEW ticks yield; the wake already holding the lock did not.

Kept: quote requests / repricing / game-line capture / health merge still run; a dead or stale marker never defers;
dry runs never export. Synthetic: QR.run, the capture and the export builder are stubs (no network, no data)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import lifecycle as LC, quote_refresh as QR


@pytest.fixture()
def world(tmp_path, monkeypatch):
    state = tmp_path / "cbb-originator"
    prod, locks = state / "prod", state / "locks"
    (prod / "ops").mkdir(parents=True)
    calls = {"exports": 0, "qr": 0, "gq": 0}
    monkeypatch.delenv("CBB_LOCK_DIR", raising=False)
    monkeypatch.setattr(QR, "run", lambda ctx, dry_run=False: calls.__setitem__("qr", calls["qr"] + 1) or
                        {"refresh_id": "Q1", "status": "NOTHING_DUE", "due_games": 0, "requests": 0, "credits": 0})
    monkeypatch.setattr(LC, "game_quotes_capture", lambda life, day: calls.__setitem__("gq", calls["gq"] + 1) or
                        {"capture_id": "GQ1", "status": "COMPLETE"})

    def exports(life, day):
        calls["exports"] += 1
        return {"boards": {"status": "WRITTEN", "rewritten": False}}
    monkeypatch.setattr(LC, "exports_for", exports)
    now = pd.Timestamp("2026-11-26T23:00:05Z")
    life = LC.Life(ops_root=prod / "ops", prod_root=prod, history_db=tmp_path / "hist.duckdb", clock=lambda: now)
    return {"life": life, "calls": calls, "marker": locks / LC.WRITER_PRIORITY, "prod": prod, "day": LC.et_date(now)}


def _marker(path: Path, pid: int, age_s: float = 0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"pid={pid}\nrefresh_id=R20261126T230001Z-ed50c0\nwindow=18:00\nat=2026-11-26T23:00:13+00:00\n")
    if age_s:
        t = time.time() - age_s
        os.utime(path, (t, t))


def _health(w):
    return json.loads((w["prod"] / "health" / f"{w['day'].isoformat()}.json").read_text())


def test_marker_path_is_the_one_the_refresh_and_the_wrapper_use(world):
    assert LC.writer_priority_path(world["life"]) == world["prod"].parent / "locks" / "cbb-originator.writer.priority"
    run = (Path(__file__).resolve().parents[1] / "deploy/droplet/bin/cbb-originator-run").read_text()
    assert 'PRIORITY="$LOCK_DIR/cbb-originator.writer.priority"' in run and "CBB_PRIORITY_MAX_AGE_MIN:-50" in run
    assert LC.WRITER_PRIORITY_MAX_AGE_SEC == 50 * 60


def test_live_waiting_refresh_defers_only_the_export_rebuild(world):
    _marker(world["marker"], os.getpid())                        # a live process waits for Phase B
    out = LC.quotes(world["life"])
    c = world["calls"]
    assert c == {"exports": 0, "qr": 1, "gq": 1}                 # requests + capture ran; rebuild skipped
    assert out["exports_deferred"]["status"] == LC.EXPORTS_DEFERRED
    assert out["exports_deferred"]["refresh_id"] == "R20261126T230001Z-ed50c0" and "exports" not in out
    h = _health(world)
    assert h["exports_deferred"]["refresh_id"] == "R20261126T230001Z-ed50c0" and "exports" not in h
    assert h["quote_refresh_totals"]["wakes"] == 1 and h["quote_refresh_totals"]["exports_deferred"] == 1
    assert world["marker"].read_text().startswith(f"pid={os.getpid()}\n")       # never touched by the wake


def test_no_marker_dead_waiter_or_stale_marker_rebuild_as_before(world):
    LC.quotes(world["life"])
    assert world["calls"]["exports"] == 1
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()                                                     # a reaped pid: the waiter died
    _marker(world["marker"], p.pid)
    LC.quotes(world["life"])
    assert world["calls"]["exports"] == 2
    _marker(world["marker"], os.getpid(), age_s=LC.WRITER_PRIORITY_MAX_AGE_SEC + 60)   # older than the unit budget
    LC.quotes(world["life"])
    assert world["calls"]["exports"] == 3
    world["marker"].write_text("garbage\n")                      # unreadable content never defers
    LC.quotes(world["life"])
    assert world["calls"]["exports"] == 4
    h = _health(world)
    assert h["exports"]["boards"]["rewritten"] is False and h["quote_refresh_totals"]["exports_deferred"] == 0


def test_a_deferred_wake_keeps_the_last_real_exports_section(world):
    LC.quotes(world["life"])                                     # a normal wake records its exports brief
    before = _health(world)["exports"]
    _marker(world["marker"], os.getpid())
    LC.quotes(world["life"])
    h = _health(world)
    assert h["exports"] == before and h["exports_deferred"]["status"] == LC.EXPORTS_DEFERRED
    assert h["quote_refresh_totals"] == {"wakes": 2, "requests": 0, "credits": 0, "receipts": 0, "exports_deferred": 1}


def test_dry_run_never_exports_and_never_defers(world):
    _marker(world["marker"], os.getpid())
    out = LC.quotes(world["life"], dry_run=True)
    assert world["calls"]["exports"] == 0 and world["calls"]["gq"] == 0 and "exports_deferred" not in out


def test_lock_dir_override_is_honoured(world, monkeypatch, tmp_path):
    d = tmp_path / "other_locks"
    monkeypatch.setenv("CBB_LOCK_DIR", str(d))
    _marker(world["marker"], os.getpid())                        # default dir: not the configured one
    LC.quotes(world["life"])
    assert world["calls"]["exports"] == 1
    _marker(d / LC.WRITER_PRIORITY, os.getpid())
    LC.quotes(world["life"])
    assert world["calls"]["exports"] == 1


# ============================================================================ controlled contention: real processes
WAKE = '''
import fcntl, json, os, sys, time
from pathlib import Path
import pandas as pd
from cbb_platform.prod import lifecycle as LC, quote_refresh as QR
lock, prod, log, export_sec, pre_sec, yield_on = sys.argv[1], Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]), float(sys.argv[5]), sys.argv[6] == "1"
fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o660)
fcntl.flock(fd, fcntl.LOCK_EX)                                   # the wrapper's writer lock (fd 9), held for the wake
note = lambda ev: open(log, "a").write(json.dumps({"who": "QUOTE_WAKE", "ev": ev, "t": time.time(), "pid": os.getpid()}) + "\\n")
note("ENTER")
QR.run = lambda ctx, dry_run=False: time.sleep(pre_sec) or {"status": "RAN", "requests": 1, "credits": 0}
LC.game_quotes_capture = lambda life, day: None
LC.exports_for = lambda life, day: time.sleep(export_sec) or {"boards": {"status": "WRITTEN", "rewritten": False}}
if not yield_on:
    LC.writer_priority_waiter = lambda life: None                # the pre-Stage-3 behaviour (control)
(prod / "ops").mkdir(parents=True, exist_ok=True)
out = LC.quotes(LC.Life(ops_root=prod / "ops", prod_root=prod, history_db=prod / "h.duckdb"))
note("EXIT")
print(json.dumps({"deferred": "exports_deferred" in out}))
'''


@pytest.mark.parametrize("yield_on", [True, False])
def test_controlled_contention_refresh_waits_only_for_the_quote_work(tmp_path, yield_on):
    """23:00Z 2026-11-26 shape: the wake holds the lock first, the refresh's Phase A ends while it runs; the wake's
    quote work (pre_sec) completes, then its export rebuild (export_sec) is skipped -> Phase B enters early."""
    from tests import test_availability_refresh_split as TS
    w = {"tmp": tmp_path, "ops": tmp_path / "ops", "prod": tmp_path / "prod", "lock": tmp_path / "locks" / "writer.lock",
         "log": tmp_path / "events.jsonl", "fake": tmp_path / "fake_phase.py", "driver": tmp_path / "driver.py"}
    w["fake"].write_text(TS.FAKE)                                 # the split tests' fake Phase A / Phase B children
    w["driver"].write_text(TS.DRIVER)
    (w["ops"] / "forecasts").mkdir(parents=True)
    (w["ops"] / "forecasts" / "index.jsonl").write_text(json.dumps({"snapshot_id": "F-PRIOR"}) + "\n")
    w["lock"].parent.mkdir()
    export_sec, pre_sec = 6.0, 1.5
    wake_py = tmp_path / "wake.py"
    wake_py.write_text(WAKE)
    q = subprocess.Popen([sys.executable, str(wake_py), str(w["lock"]), str(w["prod"]), str(w["log"]), str(export_sec),
                          str(pre_sec), "1" if yield_on else "0"], env=TS._env(w), stdout=subprocess.PIPE, text=True)
    TS._wait_for(w, "ENTER")
    code, rec, err = TS._orchestrate(w)                           # Phase A ~0 s, then waits on the held lock
    wake = json.loads(q.communicate(timeout=60)[0].strip().splitlines()[-1])
    assert code == 0 and rec["STATUS"] == "PUBLISHED", err
    assert TS.writer_count_max(w) == 1                            # still exactly one writer at a time
    ev = TS._events(w)
    assert next(e["t"] for e in ev if e["ev"] == "B_ENTER") >= next(e["t"] for e in ev if e["ev"] == "EXIT")
    assert not (w["lock"].parent / "cbb-originator.writer.priority").exists()       # cleared once Phase B holds the lock
    if yield_on:
        assert wake["deferred"] and rec["PHASE_B_LOCK_WAIT_SEC"] < export_sec
    else:
        assert not wake["deferred"] and rec["PHASE_B_LOCK_WAIT_SEC"] >= export_sec - 0.5
