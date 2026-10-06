"""Stage 2 offline pipeline: rebuild canonical data from immutable inputs and regenerate every Stage 2 artifact.

    .venv/bin/python -m cbb_platform.stage2_pipeline

No network access. Order: canonical build (Starter Pack + all raw CBBD payloads) -> discovery analysis ->
endpoint-equivalence analysis -> game identity audit -> wallclock audit -> event coverage audit ->
COVERAGE_AUDIT.json -> strategy / training-window docs -> backfill manifest -> DATASET_MANIFEST.json.
"""
from __future__ import annotations

import time


def run() -> dict:
    from cbb_platform import (build_canonical, cbbd_client, coverage, dataset_manifest, event_audit, event_discovery,
                              event_equivalence, game_identity_audit, game_identity_report, stage2_reports, wallclock_audit)
    t = {}
    for name, fn in (("build_canonical", build_canonical.build), ("discovery", event_discovery.analyze),
                     ("equivalence", event_equivalence.analyze), ("game_identity", game_identity_audit.run),
                     ("wallclock", wallclock_audit.run), ("event_audit", event_audit.run),
                     ("game_identity_resolution", game_identity_report.run), ("coverage", coverage.run),
                     ("reports", stage2_reports.run), ("backfill_manifest", cbbd_client.compile_manifest),
                     ("dataset_manifest", dataset_manifest.build)):
        t0 = time.time()
        out = fn()
        t[name] = round(time.time() - t0, 1)
        print(f"{name:20s} {t[name]:>7.1f}s", flush=True)
        if name == "dataset_manifest":
            print("DATASET_VERSION", out["dataset_version"])
    return t


if __name__ == "__main__":
    run()
