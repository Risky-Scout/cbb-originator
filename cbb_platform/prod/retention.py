"""Run-scratch retention: delete ONLY the per-run feature DuckDB scratch files prod/runs/<run>/features/*.duckdb.

    python -m cbb_platform.prod.retention --runs-root R --ops-root O              dry run (report only, deletes nothing)
    python -m cbb_platform.prod.retention --runs-root R --ops-root O --apply      dry-run report, then delete

Every run writes features/canonical_asof.duckdb + features/features_asof.duckdb (~530 MB, 2026-11-10), read only while
that run is scored (prod/run, prod/shadow_tick.scoring_evidence; publication is disabled). They are scratch ONLY when the
run COMPLETED SUCCESSFULLY and its DURABLE outputs are VERIFIED:

    completed   run_summary.json exists and parses, its run_id equals the directory
    verified    predictions == 0, or a durable PMF evidence file (ops/ticks/*/pmfs.parquet or
                ops/forecasts/<date>/<snapshot>/pmfs.parquet) holds rows of this run_id and reads back (prod.pmf_evidence)
    kept        the 2 most recent completed runs and every completed run of the last 48 h (whichever keeps more); an
                incomplete / unverified run is never touched (a run in progress has no run_summary.json yet)

Nothing else is ever deleted: run_summary.json, market_pmfs.parquet, model_inputs.parquet, player_universe.csv, board,
bundle, diagnostics, every ops / forecast / PMF / selection / grade / health / state / model / evidence file stay.
Each pass writes a dry-run report (ops/retention/reports/<ts>.json) BEFORE any deletion and appends one receipt per
deleted file (path, bytes, mtime) with the reclaimed total to ops/retention/scratch_prune.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, pmf_evidence as PE

KEEP_RECENT = 2
KEEP_HOURS = 48
SCRATCH_GLOB = "features/*.duckdb"


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _utc(x) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def durable_runs(ops_root: Path) -> dict:
    """run_id -> [durable PMF files holding its rows] (only the run_id column is read)."""
    out: dict = {}
    ops_root = Path(ops_root)
    files = sorted(ops_root.glob(f"ticks/*/{PE.FILE}")) + sorted(ops_root.glob(f"forecasts/*/*/{PE.FILE}"))
    for p in files:
        try:
            ids = pd.read_parquet(p, columns=["run_id"])["run_id"].dropna().astype(str).unique()
        except Exception:                                      # noqa: BLE001 -- an unreadable file verifies nothing
            continue
        for r in ids:
            out.setdefault(r, []).append(str(p))
    return out


def plan(runs_root: Path, ops_root: Path, now=None) -> dict:
    now = _utc(now) if now is not None else _now()
    runs_root = Path(runs_root)
    dur = durable_runs(ops_root)
    runs = []
    for d in sorted(p for p in runs_root.iterdir() if p.is_dir()) if runs_root.exists() else []:
        scratch = sorted(d.glob(SCRATCH_GLOB))
        rec = {"run_id": d.name, "scratch": [{"path": str(f), "bytes": f.stat().st_size} for f in scratch],
               "scratch_bytes": sum(f.stat().st_size for f in scratch)}
        try:
            s = json.loads((d / "run_summary.json").read_text())
        except (OSError, ValueError):
            s = None
        if not s or s.get("run_id") != d.name:
            rec.update(state="INCOMPLETE", reason="no valid run_summary.json (in progress or failed): never touched")
            runs.append(rec)
            continue
        rec["completed_at"] = str(_utc(s.get("prediction_as_of")) or pd.Timestamp((d / "run_summary.json").stat().st_mtime,
                                                                               unit="s", tz="UTC"))
        preds = int(s.get("predictions") or 0)
        if preds and d.name not in dur:
            rec.update(state="UNVERIFIED", reason=f"{preds} predictions but no durable PMF evidence names this run: kept")
        else:
            rec.update(state="COMPLETED_VERIFIED", durable=dur.get(d.name, []), predictions=preds)
        runs.append(rec)
    done = sorted((r for r in runs if r["state"] == "COMPLETED_VERIFIED"), key=lambda r: r["completed_at"], reverse=True)
    keep = {r["run_id"] for r in done[:KEEP_RECENT]} | {
        r["run_id"] for r in done if _utc(r["completed_at"]) >= now - pd.Timedelta(hours=KEEP_HOURS)}
    for r in runs:
        if r["state"] != "COMPLETED_VERIFIED":
            r["action"] = "KEEP"
        elif r["run_id"] in keep:
            r["action"], r["reason"] = "KEEP", f"retention window (latest {KEEP_RECENT} completed / {KEEP_HOURS} h)"
        else:
            r["action"] = "DELETE_SCRATCH" if r["scratch"] else "NOTHING_TO_DELETE"
    dele = [r for r in runs if r["action"] == "DELETE_SCRATCH"]
    return {"at": now.isoformat(), "runs_root": str(runs_root), "policy": {"keep_recent_completed": KEEP_RECENT,
            "keep_hours": KEEP_HOURS, "deletes_only": f"prod/runs/*/{SCRATCH_GLOB}"},
            "runs": runs, "runs_total": len(runs), "runs_delete_scratch": len(dele),
            "bytes_to_reclaim": sum(r["scratch_bytes"] for r in dele),
            "by_state": pd.Series([r["state"] for r in runs], dtype=object).value_counts().to_dict() if runs else {}}


def run(runs_root: Path, ops_root: Path, apply: bool = False, now=None) -> dict:
    p = plan(runs_root, ops_root, now)
    rdir = Path(ops_root) / "retention" / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    report = rdir / f"{pd.Timestamp(p['at']).strftime('%Y%m%dT%H%M%SZ')}{'' if apply else '-dryrun'}.json"
    tmp = report.with_name(report.name + ".tmp")
    tmp.write_text(json.dumps({**p, "mode": "APPLY" if apply else "DRY_RUN"}, indent=1, default=str) + "\n")
    os.replace(tmp, report)                                   # the dry-run report exists BEFORE anything is deleted
    out = {k: p[k] for k in ("at", "runs_total", "runs_delete_scratch", "bytes_to_reclaim", "by_state")}
    out.update(mode="APPLY" if apply else "DRY_RUN", report=str(report), reclaimed_bytes=0, deleted_files=0)
    if not apply:
        return out
    root = Path(runs_root).resolve()
    log = Path(ops_root) / "retention" / "scratch_prune.jsonl"
    with open(log, "a") as f:
        for r in p["runs"]:
            if r["action"] != "DELETE_SCRATCH":
                continue
            for s in r["scratch"]:
                fp = Path(s["path"]).resolve()
                if fp.parent.name != "features" or fp.suffix != ".duckdb" or fp.parent.parent.parent != root:
                    raise RuntimeError(f"refusing to delete outside prod/runs/*/features/*.duckdb: {fp}")
                st = fp.stat()
                fp.unlink()
                out["reclaimed_bytes"] += st.st_size
                out["deleted_files"] += 1
                f.write(json.dumps({"at": p["at"], "run_id": r["run_id"], "path": str(fp), "bytes": st.st_size,
                                    "mtime": pd.Timestamp(st.st_mtime, unit="s", tz="UTC").isoformat(),
                                    "report": str(report)}) + "\n")
        f.write(json.dumps({"at": p["at"], "summary": True, "deleted_files": out["deleted_files"],
                            "reclaimed_bytes": out["reclaimed_bytes"], "report": str(report)}) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.retention")
    ap.add_argument("--runs-root", type=Path, default=PC.RUNS_ROOT)
    ap.add_argument("--ops-root", type=Path, default=PC.OPS_ROOT)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.runs_root, a.ops_root, apply=a.apply), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
