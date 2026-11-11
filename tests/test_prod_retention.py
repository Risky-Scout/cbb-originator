"""Run-scratch retention (prod/retention.py): only prod/runs/*/features/*.duckdb of completed, verified runs outside the
keep window (latest 2 completed / 48 h, whichever keeps more) are deleted; dry-run report first; receipts with bytes.
TEST fixtures only (temporary trees)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import pmf_evidence as PE, retention as RT

NOW = pd.Timestamp("2026-11-11T05:45:00Z")
EVIDENCE = ("run_summary.json", "market_pmfs.parquet", "model_inputs.parquet", "player_universe.csv", "board.csv")


def _run(runs, rid, hours_ago, *, summary=True, predictions=10, scratch=True):
    d = runs / rid
    (d / "features").mkdir(parents=True)
    if scratch:
        for f in ("canonical_asof.duckdb", "features_asof.duckdb"):
            (d / "features" / f).write_bytes(b"x" * 1000)
    for f in EVIDENCE[1:]:
        (d / f).write_bytes(b"evidence")
    if summary:
        (d / "run_summary.json").write_text(json.dumps({"run_id": rid, "predictions": predictions,
                                                        "prediction_as_of": str(NOW - pd.Timedelta(hours=hours_ago))}))
    return d


def _durable(ops, tick, run_ids):
    p = ops / "ticks" / tick / PE.FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"run_id": list(run_ids), "x": range(len(run_ids))}).to_parquet(p, index=False)


@pytest.fixture
def tree(tmp_path):
    runs, ops = tmp_path / "runs", tmp_path / "ops"
    _run(runs, "R1-old-verified", 100)
    _run(runs, "R2-old-verified", 90)
    _run(runs, "R3-old-unverified", 80)                         # predictions but no durable PMF evidence
    _run(runs, "R4-incomplete", 70, summary=False)              # in progress / failed: never touched
    _run(runs, "R5-old-no-predictions", 60, predictions=0)      # nothing to verify: summary is the durable record
    _run(runs, "R6-recent", 30)
    _run(runs, "R7-recent", 2)
    _run(runs, "R8-recent", 1)
    _durable(ops, "K1", ["R1-old-verified", "R2-old-verified"])
    _durable(ops, "K2", ["R6-recent", "R7-recent", "R8-recent"])
    return runs, ops


def _actions(p):
    return {r["run_id"]: (r["state"], r["action"]) for r in p["runs"]}


def test_plan_states_and_keep_window(tree):
    runs, ops = tree
    a = _actions(RT.plan(runs, ops, NOW))
    assert a == {"R1-old-verified": ("COMPLETED_VERIFIED", "DELETE_SCRATCH"),
                 "R2-old-verified": ("COMPLETED_VERIFIED", "DELETE_SCRATCH"),
                 "R3-old-unverified": ("UNVERIFIED", "KEEP"),
                 "R4-incomplete": ("INCOMPLETE", "KEEP"),
                 "R5-old-no-predictions": ("COMPLETED_VERIFIED", "DELETE_SCRATCH"),
                 "R6-recent": ("COMPLETED_VERIFIED", "KEEP"),               # within 48 h (more than the latest 2)
                 "R7-recent": ("COMPLETED_VERIFIED", "KEEP"),
                 "R8-recent": ("COMPLETED_VERIFIED", "KEEP")}


def test_latest_two_completed_kept_even_when_older_than_48h(tmp_path):
    runs, ops = tmp_path / "runs", tmp_path / "ops"
    for rid, h in (("A", 300), ("B", 200), ("C", 100)):
        _run(runs, rid, h)
    _durable(ops, "K", ["A", "B", "C"])
    assert _actions(RT.plan(runs, ops, NOW)) == {"A": ("COMPLETED_VERIFIED", "DELETE_SCRATCH"),
                                                 "B": ("COMPLETED_VERIFIED", "KEEP"), "C": ("COMPLETED_VERIFIED", "KEEP")}


def test_dry_run_deletes_nothing_and_reports_bytes(tree):
    runs, ops = tree
    before = sorted(str(p) for p in runs.rglob("*"))
    out = RT.run(runs, ops, apply=False, now=NOW)
    assert sorted(str(p) for p in runs.rglob("*")) == before
    assert out["mode"] == "DRY_RUN" and out["runs_delete_scratch"] == 3 and out["bytes_to_reclaim"] == 6000
    assert out["reclaimed_bytes"] == 0 and Path(out["report"]).exists() and out["report"].endswith("-dryrun.json")
    assert not (ops / "retention" / "scratch_prune.jsonl").exists()


def test_apply_deletes_only_scratch_and_records_receipts(tree):
    runs, ops = tree
    out = RT.run(runs, ops, apply=True, now=NOW)
    assert out["deleted_files"] == 6 and out["reclaimed_bytes"] == 6000 and Path(out["report"]).exists()
    for rid in ("R1-old-verified", "R2-old-verified", "R5-old-no-predictions"):
        assert list((runs / rid / "features").glob("*.duckdb")) == []
        assert all((runs / rid / f).exists() for f in EVIDENCE)         # every non-scratch file stays
    for rid in ("R3-old-unverified", "R4-incomplete", "R6-recent", "R7-recent", "R8-recent"):
        assert len(list((runs / rid / "features").glob("*.duckdb"))) == 2
    assert (ops / "ticks" / "K1" / PE.FILE).exists()
    rec = [json.loads(l) for l in (ops / "retention" / "scratch_prune.jsonl").read_text().splitlines()]
    assert sum(r["bytes"] for r in rec if not r.get("summary")) == 6000 and rec[-1]["reclaimed_bytes"] == 6000
    assert all(r["path"].endswith(".duckdb") and "/features/" in r["path"] for r in rec if not r.get("summary"))
    again = RT.run(runs, ops, apply=True, now=NOW)                    # idempotent: nothing left to delete
    assert again["deleted_files"] == 0 and again["reclaimed_bytes"] == 0


def test_refuses_a_path_outside_run_scratch(tree, monkeypatch):
    runs, ops = tree
    real = RT.plan

    def evil(*a, **k):
        p = real(*a, **k)
        for r in p["runs"]:
            if r["action"] == "DELETE_SCRATCH":
                r["scratch"] = [{"path": str(ops / "ticks" / "K1" / PE.FILE), "bytes": 1}]
        return p
    monkeypatch.setattr(RT, "plan", evil)
    with pytest.raises(RuntimeError, match="refusing"):
        RT.run(runs, ops, apply=True, now=NOW)
    assert (ops / "ticks" / "K1" / PE.FILE).exists()
