"""Subprocess harness for tests/test_prod_morning_stages.py: runs ONE morning stage (or the recovery, or a lock holder)
in a FRESH Python process, exactly as a systemd stage unit would (one process per stage). Fakes are installed only in
--mode fake; --mode real runs the production stage code unchanged (equivalence tests).

    python tests/morning_stage_harness.py --ops-root O --prod-root P --model-root M --now ISO stage forecast --mode fake
    python tests/morning_stage_harness.py ... recover --writer-lock L
    python tests/morning_stage_harness.py ... hold --writer-lock L --stage walkforward --release-file F
Prints one JSON line: {"pid": ..., "code": ..., "out": ...}.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from cbb_platform.prod import lifecycle as L, morning_stages as MS  # noqa: E402


def _fakes(alloc_mb: int, fail: str | None) -> dict:
    calls = {}

    def tick(ctx):
        calls["tick"] = calls.get("tick", 0) + 1
        return {"tick_id": "K-fake", "status": "OK", "did_work": False}

    def exports_for(life, day):
        if fail == "exports":
            raise RuntimeError("synthetic export failure")
        return {"date": day.isoformat(), "boards": {"status": "WRITTEN", "rows": 1},
                "distributions": {"status": "WRITTEN", "rows": 2}, "grades": {"status": "NOTHING_SETTLED_YET"}}

    def forecast(life, window=L.INITIAL, **k):
        if fail == "forecast":
            raise MemoryError("synthetic forecast failure")
        return {"status": "FORECAST", "window": L.window_label(window), "snapshot_id": f"F-{os.getpid()}",
                "model_version": "cbb-jm1-fake", "games_forecast": 2, "players_forecast": 20, "pmfs_persisted": 240,
                "stat_predictions": 240}

    def wf_runner(day, root, state_root, now):
        blob = None
        if alloc_mb:
            import numpy as np
            blob = np.ones(alloc_mb * 1024 * 1024 // 8)       # touched pages: real resident memory
            blob[::512] = 2.0
        del blob
        return {"DAILY_REFIT_STATUS": "PASS", "DAILY_MODEL_PROMOTION": "PROMOTED", "ACTIVE_MODEL_VERSION": "cbb-jm1-fake",
                "run_id": "WF-fake", "training_cutoff": str(day)}

    MS_shadow = __import__("cbb_platform.prod.shadow_tick", fromlist=["tick"])
    MS_shadow.tick = tick
    L.exports_for = exports_for
    L.forecast = forecast
    return {"runner": wf_runner}


def _lock(path: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o660)
    fcntl.flock(fd, fcntl.LOCK_EX)                           # blocking, like the wrapper's flock -w
    return fd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops-root", required=True)
    ap.add_argument("--prod-root", required=True)
    ap.add_argument("--model-root", required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--history-db", default=None)
    ap.add_argument("--sims", type=int, default=None)
    ap.add_argument("--max-state-age", type=float, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("stage", choices=sorted(MS.CLI_STAGES))
    s.add_argument("--mode", choices=("fake", "real"), default="fake")
    s.add_argument("--alloc-mb", type=int, default=0)
    s.add_argument("--fail", default=None)
    r = sub.add_parser("recover")
    r.add_argument("--writer-lock", required=True)
    h = sub.add_parser("hold")
    h.add_argument("--writer-lock", required=True)
    h.add_argument("--release-file", required=True)
    a = ap.parse_args()
    now = pd.Timestamp(a.now)
    kw = {"sims": a.sims} if a.sims else {}
    life = L.Life(ops_root=Path(a.ops_root), prod_root=Path(a.prod_root), model_root=Path(a.model_root),
                  history_db=Path(a.history_db) if a.history_db else None, clock=lambda: now, **kw)
    if a.max_state_age is not None:
        L.PC.MAX_STATE_AGE_AT_REFIT_CUTOFF_MINUTES = a.max_state_age
    day = L.et_date(now)
    if a.cmd == "stage":
        st = MS.CLI_STAGES[a.stage]
        extra = {}
        if a.mode == "fake":
            f = _fakes(a.alloc_mb, a.fail)
            if st == "WALK_FORWARD":
                extra["runner"] = f["runner"]
        code, out = MS.run_stage(life, day, st, **extra)
    elif a.cmd == "recover":
        fd = _lock(a.writer_lock)
        try:
            out, code = MS.recover(life, day), 0
        finally:
            os.close(fd)
    else:                                                    # a 07:00 stage that holds the writer lock while running
        fd = _lock(a.writer_lock)
        with MS.locked(life.ops_root, day) as p:
            MS._begin(p, "WALK_FORWARD", "PREP")
        print(json.dumps({"pid": os.getpid(), "event": "HOLDING"}), flush=True)
        while not Path(a.release_file).exists():
            time.sleep(0.05)
        with MS.locked(life.ops_root, day) as p:
            MS._end(p, "WALK_FORWARD", MS.PASS, 0, RUN_ID="WF-held", DAILY_REFIT_STATUS="PASS",
                    DAILY_MODEL_PROMOTION="PROMOTED", ACTIVE_MODEL_VERSION="cbb-jm1-held", MODEL_DECISION="PROMOTED",
                    RETRYABLE=False)
        os.close(fd)
        out, code = {"held": True}, 0
    print(json.dumps({"pid": os.getpid(), "code": code, "out": out}, default=str), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
