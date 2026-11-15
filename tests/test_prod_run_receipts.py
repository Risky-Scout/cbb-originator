"""Terminal run receipts (prod/run_receipts.py): a run ends in run_summary.json or run_failure.json; SIGTERM and
exceptions are recorded and still propagate. TEST fixtures only."""
import json
import os
import signal

import pytest

from cbb_platform.prod import run_receipts as RR


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


def test_write_failure_marks_an_orphan_explicitly(tmp_path):
    d = tmp_path / "R-killed"
    d.mkdir()
    assert RR.state(d) == "IN_PROGRESS"
    RR.write_failure(d, "ORPHANED", "OOM kill", evidence="journal")
    assert RR.state(d) == "ORPHANED"
