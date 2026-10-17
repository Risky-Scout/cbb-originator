"""Development validation of the fit-free lockbox runner (2024-25 only; 2025-26 is never read).

The recorded final-candidate metrics on 2024-25 are the frozen evaluation B (fit through 2023-24): count metrics in
final_prelockbox_validation.json (evaluations.B.targets.*.final), the rolling baseline and minutes in
modeling/player_props_v0/metrics.json, participation in participation_metrics.json. To reproduce them WITHOUT fitting
inside the runner, the evaluation-B artifact is assembled here first (this step fits, exactly as recorded, and asserts
the recorded state hashes), saved, and then the runner loads and scores it in a FRESH process in which every fit entry
point raises (FIT_CALLS = 0). The final artifact (trained through 2024-25) is also scored on 2024-25: in-sample, a
functional check of the real candidate only.

    .venv/bin/python -m cbb_platform.lockbox_runner_validation      -> data/qa/lockbox_runner_validation.json
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cbb_platform import config as C, props_v0 as P, props_v0_participation as Q, props_v1_refit as R

OUT = C.QA / "lockbox_runner_validation.json"
DEV_SEASON = 2025
TOL = 1e-10
COUNT_KEYS = ("mae", "rmse", "log_score", "crps", "coverage_50", "coverage_80", "coverage_95")
PART_KEYS = ("brier", "log_loss", "auc", "calibration_in_the_large", "calibration_slope")


def evaluation_artifact() -> dict:
    """Evaluation-B state as an artifact: v0 fit through 2023-24 + the accepted STL override; participation fit on
    2023-24. Both state hashes must equal the recorded ones."""
    cfg, spec = P.load_config(), R.load_final_spec()
    recorded = json.loads((P.CONFIG_PATH.parent / "metrics.json").read_text())["evaluations"]["B"]
    frame, finals, _ = P.load_frames(cfg)
    v0 = P.fit(frame, finals, cfg, recorded["train_through"])
    assert v0["state_hash"] == recorded["state_hash"], "evaluation-B v0 state differs from the recorded state"
    part_rows, _ = Q.load()
    part = Q.fit(part_rows)
    rec_p = json.loads((P.CONFIG_PATH.parent / "participation_metrics.json").read_text())["model_state"]["state_hash"]
    assert part["state_hash"] == rec_p, "participation state differs from the recorded state"
    final = R.load_final()
    art = {"artifact_version": "cbb-props-v1-evaluation-B-2023-24 (development validation only; never a candidate)",
           "model_version": final["model_version"], "dataset_version": final["dataset_version"],
           "training_through_season": recorded["train_through"], "stat_training_seasons": [2014, recorded["train_through"]],
           "participation_training_seasons": list(Q.TRAIN_SEASONS), "participation_rows": None,
           "spec_fingerprint": R.spec_fingerprint(cfg), "final_spec": spec, "final_spec_fingerprint": R.final_spec_fingerprint(spec),
           "distribution_overrides": final["distribution_overrides"], "config": cfg,
           "v0_state": R.apply_accepted_changes(v0, spec, cfg), "participation_state": part,
           "coef_order": {"v0": list(v0["coef"]), "participation": list(part["coef"])}}
    art["artifact_fingerprint"] = R.fingerprint(art)
    return art


FRESH_RUN = """
import json, sys
from pathlib import Path
from cbb_platform import props_v0 as P, props_v0_eval as E, props_v0_participation as Q, props_v1_refit as R
calls = []
def _no_fit(*a, **k):
    calls.append(1); raise RuntimeError("fit attempted inside the lockbox runner")
P.fit = Q.fit = R.fit_artifact = R.build_final_artifact = E.evaluate = P._fit_baseline = _no_fit
from cbb_platform import lockbox_runner as L
r = L.run(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
Path(sys.argv[5]).write_text(L.dumps(r))
print(json.dumps({"fit_calls": len(calls), "result_sha256": r["result_sha256"]}))
"""


def fresh_run(artifact: Path, features_db: Path, labels_db: Path, season: int, out: Path) -> dict:
    p = subprocess.run([sys.executable, "-c", FRESH_RUN, str(artifact), str(features_db), str(labels_db), str(season), str(out)],
                       cwd=C.PROJECT_ROOT, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-3000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def compare(result: dict) -> dict:
    """Max |runner - recorded| per metric family, plus row counts."""
    v = json.loads((R.ARTIFACT_PATH.parent / "final_prelockbox_validation.json").read_text())["evaluations"]["B"]["targets"]
    m = json.loads((P.CONFIG_PATH.parent / "metrics.json").read_text())["evaluations"]["B"]["targets"]
    p = json.loads((P.CONFIG_PATH.parent / "participation_metrics.json").read_text())
    tg, diffs, n_ok = result["counts_and_minutes"]["targets"], {}, True
    for t in ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"):
        diffs[f"{t}.candidate"] = max(abs(tg[t]["candidate"][k] - v[t]["final"][k]) for k in COUNT_KEYS)
        diffs[f"{t}.rolling_baseline"] = max(abs(tg[t]["rolling_baseline"][k] - m[t]["baseline"][k]) for k in COUNT_KEYS)
        n_ok &= tg[t]["n"] == v[t]["n"] == m[t]["n"]
    diffs["MIN.candidate"] = max(abs(tg["MIN"]["candidate"][k] - m["MIN"]["challenger"][k]) for k in COUNT_KEYS)
    diffs["MIN.rolling_baseline"] = max(abs(tg["MIN"]["rolling_baseline"][k] - m["MIN"]["baseline"][k]) for k in COUNT_KEYS)
    part = result["participation"]
    diffs["participation.candidate"] = max(abs(part["candidate"][k] - p["challenger"][k]) for k in PART_KEYS)
    diffs["participation.heuristic"] = max(abs(part["heuristic"][k] - p["benchmark"][k]) for k in PART_KEYS)
    n_ok &= part["n"] == p["challenger"]["n"]
    return {"max_abs_difference": diffs, "tolerance": TOL, "row_counts_equal": bool(n_ok),
            "reproduced": bool(n_ok and max(diffs.values()) <= TOL)}


def validate() -> dict:
    from cbb_platform.stage3 import spec
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        art_path = d / "evaluation_B_artifact.json"
        R.save(evaluation_artifact(), art_path)
        runs = [fresh_run(art_path, spec.FEATURES_DB, spec.LABELS_DB, DEV_SEASON, d / f"eval_b_{i}.json") for i in (1, 2)]
        texts = [(d / f"eval_b_{i}.json").read_text() for i in (1, 2)]
        res = json.loads(texts[0])
        final_runs = [fresh_run(R.FINAL_ARTIFACT_PATH, spec.FEATURES_DB, spec.LABELS_DB, DEV_SEASON, d / f"final_{i}.json")
                      for i in (1, 2)]
        final_texts = [(d / f"final_{i}.json").read_text() for i in (1, 2)]
        final = json.loads(final_texts[0])
        art_sha = hashlib.sha256(art_path.read_bytes()).hexdigest()
    return {"runner": "cbb_platform/lockbox_runner.py", "runner_sha256": hashlib.sha256(
                (C.PROJECT_ROOT / "cbb_platform" / "lockbox_runner.py").read_bytes()).hexdigest(),
            "season": DEV_SEASON, "lockbox_touched": False,
            "evaluation_B": {"artifact_sha256": art_sha, "fit_calls": [r["fit_calls"] for r in runs],
                             "deterministic_rerun_byte_identical": texts[0] == texts[1],
                             "result_sha256": runs[0]["result_sha256"], "reproduction": compare(res),
                             "gates": {k: (v["pass"] if isinstance(v, dict) else v) for k, v in res["gates"].items()}},
            "final_artifact_in_sample_functional_check": {
                "artifact_sha256": final["artifact"]["sha256"], "fit_calls": [r["fit_calls"] for r in final_runs],
                "deterministic_rerun_byte_identical": final_texts[0] == final_texts[1],
                "result_sha256": final_runs[0]["result_sha256"],
                "note": "the final artifact was trained through 2024-25, so these 2024-25 numbers are IN-SAMPLE (not evidence)",
                "gates": {k: (v["pass"] if isinstance(v, dict) else v) for k, v in final["gates"].items()}}}


if __name__ == "__main__":
    out = validate()
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: out["evaluation_B"][k] for k in ("fit_calls", "deterministic_rerun_byte_identical", "reproduction")}, indent=1))
