"""Small end-to-end smoke of the ACTUAL production path on clearly labeled production-format FIXTURES (1-3 games).

    state update (fixture 2026-27 increment) -> slate -> legal pregame universe -> frozen artifact validation
    -> one joint simulation per game -> market pricing -> fixture quote normalization / strict matching
    -> readiness gate -> shadow ledger (a SMOKE ledger, never the production one) -> static bundle -> rollback dry run

    .venv/bin/python -m cbb_platform.prod.smoke [--synthetic-history]   (default history: the frozen local canonical DB)
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from cbb_platform.prod import (bundle as BD, config as PC, deploy_dryrun as DR, fixtures as FX, ledger as LG,
                               run as RN, state as ST)


def smoke(work: Path, history_db: Path = PC.HISTORY_CANONICAL, sims: int = PC.SIMS) -> dict:
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rows, meta = FX.increment_rows(history_db)
    state_root = work / "state"
    man = ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], state_root,
                    created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    qpaths = FX.quotes_files(history_db, meta, work / "quotes")
    av = FX.availability(meta)
    ledger = work / "smoke_ledger.jsonl"
    common = dict(availability=av, state_root=state_root, history_db=history_db, ledger_path=ledger,
                  run_root=work / "runs", sims=sims)
    a = RN.daily(FX.AS_OF, qpaths, bundle_dir=work / "bundle_A", **common)
    b = RN.daily(FX.AS_OF + pd.Timedelta(minutes=10), qpaths[:1], bundle_dir=work / "bundle_B", **common)
    sandbox = PC.PROD_ROOT / "dryrun_remote" / work.name
    if sandbox.exists():
        shutil.rmtree(sandbox)
    dr = DR.dry_run(work / "bundle_A", work / "bundle_B", sandbox)
    out = {"data_label": RN.FIXTURE_LABEL, "state": {k: man[k] for k in ("state_version", "source_cutoff", "coverage")},
           "fixture_meta": {k: meta[k] for k in ("teams", "upcoming", "recruit", "transfer")},
           "run_A": a, "run_B": {k: b[k] for k in ("run_id", "readiness_state", "predictions", "bundle")},
           "ledger": LG.verify(ledger), "bundle_A": BD.verify(work / "bundle_A"), "rollback_dry_run": dr}
    (work / "smoke_summary.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic-history", action="store_true")
    ap.add_argument("--work", default=str(PC.PROD_ROOT / "smoke"))
    a = ap.parse_args()
    w = Path(a.work)
    hist = FX.synthetic_history(w.parent / "synthetic_history.duckdb") if a.synthetic_history else PC.HISTORY_CANONICAL
    r = smoke(w, hist)
    print(json.dumps({"readiness_A": r["run_A"]["readiness_state"], "games": r["run_A"].get("game_reasons"),
                      "banks": r["run_A"].get("banks"), "predictions": r["run_A"].get("predictions"),
                      "quotes": r["run_A"].get("quotes"), "ledger": r["ledger"], "bundle": r["bundle_A"],
                      "rollback": r["rollback_dry_run"]["ROLLBACK_DRY_RUN"]}, indent=1, default=str))
