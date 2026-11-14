"""Terminal run receipts (prod/run_receipts.py): a run ends in run_summary.json or run_failure.json; SIGTERM and
exceptions are recorded and still propagate; retention writes ORPHANED for killed runs. TEST fixtures only."""
import json
import os
import signal
from datetime import datetime, timedelta, timezone

import pytest

from cbb_platform.prod import run_receipts as RR, storage_retention as SR


def _fake_daily(root, behaviour):
    @RR.terminal
    def daily():
        d = root / "R-test"
        d.mkdir()
        RR.register(d)
        if behaviour == "raise":
            raise KeyError("boom")
        if behaviour == "sigterm":
            os.kill(os.getpid(), signal.SIGTERM)
        (d / RR.SUMMARY_FILE).write_text(json.dumps({"run_id": "R-test"}))
        return "ok"
    return daily


def test_exception_writes_failed_receipt_and_reraises(tmp_path):
    with pytest.raises(KeyError):
        _fake_daily(tmp_path, "raise")()
    rec = json.loads((tmp_path / "R-test" / RR.FAILURE_FILE).read_text())
    assert rec["status"] == "FAILED" and rec["error_type"] == "KeyError" and "boom" in rec["reason"]
    assert RR.state(tmp_path / "R-test") == "FAILED"


def test_sigterm_writes_terminated_receipt_and_restores_handler(tmp_path):
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(RR.RunTerminated):
        _fake_daily(tmp_path, "sigterm")()
    assert json.loads((tmp_path / "R-test" / RR.FAILURE_FILE).read_text())["status"] == "TERMINATED"
    assert signal.getsignal(signal.SIGTERM) == before


def test_completed_run_has_no_failure_receipt(tmp_path):
    assert _fake_daily(tmp_path, "ok")() == "ok"
    assert not (tmp_path / "R-test" / RR.FAILURE_FILE).exists() and RR.state(tmp_path / "R-test") == "COMPLETED"


def test_retention_marks_only_old_unrecorded_runs_orphaned(tmp_path):
    runs = tmp_path / "prod" / "runs"
    for d in ("qa", "staging/tmp/duckdb", "exports", "prod/ops"):
        (tmp_path / d).mkdir(parents=True)
    old, fresh, failed = (runs / n for n in ("R-killed", "R-running", "R-failed"))
    for d in (old, fresh, failed):
        (d / "features").mkdir(parents=True)
        (d / "model_inputs.parquet").write_bytes(b"x")
    RR.write_failure(failed, "FAILED", "x")
    t = (datetime.now(timezone.utc) - timedelta(hours=RR.ORPHAN_HOURS + 1)).timestamp()
    for d in (old, failed):
        for f in [d, *d.rglob("*")]:
            os.utime(f, (t, t))
    P = SR.Paths(tmp_path)
    items = [i for i in SR.plan(P)["items"] if i["rule"] == "ORPHAN_RUN_RECEIPT"]
    assert [i["path"] for i in items] == [str(old / RR.FAILURE_FILE)]
    SR.run(P, apply=True)
    assert RR.state(old) == "ORPHANED" and RR.state(failed) == "FAILED" and RR.state(fresh) == "IN_PROGRESS"
    assert [i for i in SR.plan(P)["items"] if i["rule"] == "ORPHAN_RUN_RECEIPT"] == []     # idempotent
