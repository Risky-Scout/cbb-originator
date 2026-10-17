"""Lockbox readiness records for the frozen CBB pre-lockbox candidate (docs/LOCKBOX_OPENING_PROTOCOL.md §1), v3.1 content.

Candidate: tag cbb-props-v1-prelockbox-2024-25-r2 (the evidence-closure successor of cbb-props-v1-prelockbox-2024-25;
identical model, code and artifact, plus the tracked accepted-STL evidence). Writes deterministic files (no wall-clock
content; every value derives from committed inputs, recorded evidence files, the observed environment and the git
state). It reads NOTHING from 2025-26 and opens nothing: lockbox_open is always false and approval is NOT_GRANTED.
Architect rulings of 2026-10-13: docs/lockbox/ARCHITECT_RULINGS_2026-10-13_LOCKBOX_READINESS.md; of 2026-10-16 (route
closure, relocation, guard v3 design): docs/lockbox/ARCHITECT_RULINGS_2026-10-16_ROUTE_CLOSURE.md.

    ENVIRONMENT_FINGERPRINT.json                      docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md §2.2
    docs/lockbox/lockbox_scorecard_v2.json            protocol §1.2 (content v2.1.0: G1-G6 verbatim + rulings)
    data/manifests/LOCKBOX_READINESS_v2.json          protocol §1 -- ledger-shaped readiness record with every gate

    .venv/bin/python -m cbb_platform.lockbox_readiness
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
import platform
import subprocess
import sys
import tomllib
from pathlib import Path

import duckdb

from cbb_platform import config as C, lockbox, props_v0_participation as Q, props_v1_refit as R
from cbb_platform.stage3 import spec

ROOT = C.PROJECT_ROOT
ENV_PATH = ROOT / "ENVIRONMENT_FINGERPRINT.json"
SCORECARD_PATH = ROOT / "docs" / "lockbox" / "lockbox_scorecard_v2.json"
LEDGER_PATH = C.MANIFESTS / "LOCKBOX_READINESS_v2.json"
PLAN_PATH = "docs/lockbox/LOCKBOX_DATA_OPENING_PLAN_2025_26.md"
RULINGS = "docs/lockbox/ARCHITECT_RULINGS_2026-10-13_LOCKBOX_READINESS.md"
GUARD_PATCH = "docs/lockbox/lockbox_open_2025_26_guard_v2.patch"
GUARD_PATCH_V3 = "docs/lockbox/lockbox_open_2025_26_guard_v3.patch"
SCORING_PATCH = "docs/lockbox/lockbox_open_2025_26_scoring_enable.patch"
RULINGS_2 = "docs/lockbox/ARCHITECT_RULINGS_2026-10-16_ROUTE_CLOSURE.md"
# ruling Q (2026-10-16): DESIGN approval of exactly these bytes; neither patch is applied by this module
APPROVED_DESIGN_SHA256 = {GUARD_PATCH_V3: "366ba2d1d1e2aa88491cf0370e05695f020f436a5c83e818bb237a54641cba4e",
                          SCORING_PATCH: "8f964e01062937fde8ff44a7c8af595d3659884ff223771bced2e6d32aff2be7"}
GUARD_CHECKS = ("docs/lockbox/guard_v2_synthetic_check.py", "docs/lockbox/guard_v2_extended_check.py")
READINESS_DOC = "docs/lockbox/LOCKBOX_READINESS_v2.md"
ROUTE_SPEC = "docs/lockbox/CBBD_BOX_ROUTE_RECONCILIATION_SPEC.md"
ORIGINAL_COMMIT = "6eab9ddc667942b271bdbc50cccc3c6a9d02843c"
ORIGINAL_TAG = "cbb-props-v1-prelockbox-2024-25"
MODEL_TAG = "cbb-props-v1-prelockbox-2024-25-r2"
MODEL_COMMIT = "425211071413735be8e1497105ae51830c971c12"
ARTIFACT = "modeling/player_props_v1/final_prelockbox_2024_25_artifact.json"
ARTIFACT_SHA256 = "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"
SPEC_FINGERPRINT = "3e343c1ad1e8668dc1fadade4c85a92c4bc8cbb651ff49284ac4a8a7106b8a9f"
ARTIFACT_FINGERPRINT = "191175928969a38edd431cd8410a9fd8050cf68f23a9fb1836814bb771839997"
STL_EVIDENCE = ("modeling/player_props_v1/stl_nb_challenger.json", "modeling/player_props_v1/STL_NB_CHALLENGER.md")
SUITE_PATH = "data/qa/lockbox_readiness_suite.json"            # written from the authoritative full-suite run
RUNNER_EVIDENCE = "data/qa/lockbox_runner_validation.json"
ROUTE_SMOKE = "data/qa/cbbd_box_route_smoke_reconciliation.json"
ROUTE_FULL = "data/qa/cbbd_box_route_reconciliation.json"      # the authoritative full development reconciliation
ROUTE_PIT = "data/qa/cbbd_box_route_pit_audit.json"            # PIT / anti-leakage audit of the reconciled route build
ROUTE_MODULE = "cbb_platform/cbbd_box_route.py"
RECONCILER = "cbb_platform/cbbd_box_route_reconcile.py"
PIT_CATEGORIES = ("TARGET_LEAKAGE", "SAME_GAME_LEAKAGE", "FUTURE_DATA_LEAKAGE", "FUTURE_ROSTER_LEAKAGE", "LABEL_LEAKAGE",
                  "LOOKAHEAD_BIAS")
FROZEN_DEV_DB = "data/canonical/cbb.duckdb"
REQUIRED_VERSIONS = {"python": "3.12.13", "duckdb": "1.5.6", "numpy": "2.5.3", "pandas": "3.0.6"}
ELIG_INDEX = "modeling/eligibility/INDEX.json"
QA_FILE_EXCEPTION = "data/qa/game_identity_questionable_only.csv"
ARCHIVE_MANIFEST = Path.home() / "CBB_Project_External_Artifacts" / "CBB_Originator_Prelockbox_Archive" / "ARCHIVE_MANIFEST.json"
LOCK_FILES = ("pyproject.toml", "uv.lock", "requirements.lock.txt")
VALIDATION = "modeling/player_props_v1/final_prelockbox_validation.json"
EVIDENCE = ("modeling/player_props_v0/metrics.json", "modeling/player_props_v0/participation_metrics.json",
            "modeling/player_props_v0/config.json", ARTIFACT, VALIDATION, "modeling/player_props_v1/final_prelockbox_spec.json")
TARGETS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
# Phase A2 (PROJECT_EXECUTION_ORDER_v1_1.md): MODEL_READY is granted by the architect only after every item
PHASE_A2 = {
    "A2.1 bitemporal five-checkpoint rebuild (PRESEASON_ASSUMED replaces the 1900-01-01 sentinel)": False,
    "A2.2 exact possession reconstruction + exact lineup stints": False,
    "A2.3 PBP quarantine selection-bias audit": False,
    "A2.4 per-subsystem eligibility manifests": None,          # evaluated from the manifests
    "A2.5 team-identity alias remediation (LIU / Carver; changes dataset contents)": False,
    "A2.6a environment lock": None,                            # evaluated from the lock
    "A2.6b clean-room rebuild reproducing the accepted fingerprints (independent machine / container, policy §4)": False,
    "A2.7 off-machine baseline (owner's destination choice)": False}


def sha(path) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def _tracked(path: str) -> bool:
    return _git("ls-files", "--error-unmatch", path, check=False).returncode == 0


def _json(path) -> dict | None:
    p = ROOT / path
    return json.loads(p.read_text()) if p.exists() else None


# ============================================================================ environment
def environment() -> dict:
    con = duckdb.connect(config={"threads": C.DUCKDB_THREADS})
    settings = dict(con.execute("SELECT name, value FROM duckdb_settings() WHERE name IN ('threads', 'memory_limit')").fetchall())
    con.close()
    env = {"python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                      "build": list(platform.python_build()), "compiler": platform.python_compiler(),
                      "executable_relative": os.path.relpath(sys.executable, ROOT)},
           "platform": {"platform": platform.platform(), "system": platform.system(), "release": platform.release(),
                        "machine": platform.machine(), "cpu_count": os.cpu_count()},
           "duckdb": {"version": duckdb.__version__, "threads": settings.get("threads"),
                      "memory_limit": settings.get("memory_limit")},
           "thread_policy": {"duckdb_threads_config": C.DUCKDB_THREADS, "OMP/OPENBLAS/MKL_NUM_THREADS": 2},
           "installed_distributions": sorted(f"{d.metadata['Name'].lower()}=={d.version}" for d in md.distributions()),
           "lock_files_sha256": {f: sha(f) for f in LOCK_FILES}}
    return {"policy": "docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md §2.2", "environment": env,
            "environment_fingerprint": hashlib.sha256(_canon(env).encode()).hexdigest(),
            "environment_lock_sha256": sha("uv.lock")}


def lock_matches_venv() -> dict:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {p["name"]: p["version"] for p in lock["package"] if p.get("source", {}).get("registry")}
    installed = {d.metadata["Name"].lower(): d.version for d in md.distributions()}
    drift = sorted(n for n, v in locked.items() if n in installed and installed[n] != v)
    observed = {"python": platform.python_version(), "duckdb": duckdb.__version__, "numpy": installed.get("numpy"),
                "pandas": installed.get("pandas")}
    unlocked = sorted(set(installed) - set(locked))
    req = {}
    cur = None
    for line in (ROOT / "requirements.lock.txt").read_text().splitlines():
        if line and not line[0].isspace() and "==" in line:
            cur = line.split("==")[0].strip().lower()
            req[cur] = {"version": line.split("==")[1].split()[0].rstrip("\\").strip(), "hashes": 0}
        elif "--hash=sha256:" in line and cur:
            req[cur]["hashes"] += 1
    return {"locked_packages": len(locked), "version_drift": drift, "installed_not_locked": unlocked,
            "locked_not_installed": sorted(set(locked) - set(installed)),       # win32-only extras expected
            "all_wheels_sha256_hashed": all(w.get("hash", "").startswith("sha256:") for p in lock["package"]
                                            for w in p.get("wheels", [])),
            "requirements_lock_matches_uv_lock": {n: v["version"] for n, v in req.items()} == locked,
            "requirements_lock_all_hashed": bool(req) and all(v["hashes"] > 0 for v in req.values()),
            "python_matches_policy": platform.python_version() == "3.12.13", "duckdb": duckdb.__version__,
            "required_versions": REQUIRED_VERSIONS, "observed_versions": observed,
            "required_versions_match": observed == REQUIRED_VERSIONS,
            "pass": not drift and not unlocked}


# ============================================================================ scorecard (v2 file, content 2.1.0)
def _reference() -> dict:
    v = json.loads((ROOT / VALIDATION).read_text())["evaluations"]["B"]["targets"]
    m = json.loads((ROOT / EVIDENCE[0]).read_text())["evaluations"]["B"]["targets"]
    p = json.loads((ROOT / EVIDENCE[1]).read_text())
    keys = ("mae", "log_score", "crps", "coverage_50", "coverage_80", "coverage_95")
    return {"source": "frozen 2024-25 forward validation (evaluation B: fit through 2023-24); final candidate = baseline "
                      "for every non-STL target, STL = NegBin phi 0.16 (final_prelockbox_validation.json)",
            "counts_candidate": {t: {k: round(v[t]["final"][k], 6) for k in keys} for t in TARGETS},
            "counts_rolling_baseline": {t: {k: round(m[t]["baseline"][k], 6) for k in ("mae", "log_score", "crps")} for t in TARGETS},
            "minutes": {"candidate": {k: round(m["MIN"]["challenger"][k], 6) for k in ("mae", "rmse")},
                        "rolling_baseline": {k: round(m["MIN"]["baseline"][k], 6) for k in ("mae", "rmse")}},
            "participation": {"candidate": {k: round(p["challenger"][k], 6) for k in
                                            ("brier", "log_loss", "calibration_slope", "calibration_in_the_large", "auc")},
                              "heuristic": {k: round(p["benchmark"][k], 6) for k in ("brier", "log_loss", "auc")}},
            "stl_thresholds": {L: {k: round(d[k], 6) for k in ("pred", "observed", "gap", "brier", "log_loss")}
                               for L, d in v["STL"]["final"]["thresholds"].items()}}


PASS_CRITERIA = {      # VERBATIM from scorecard 2.0.0 (architect ruling F: ratified exactly as written)
    "G1_aggregate": "candidate aggregate log score < baseline AND candidate aggregate CRPS < baseline",
    "G2_per_target": "candidate beats baseline on BOTH log score and CRPS for >= 6 of 7 count targets",
    "G3_minutes": "candidate MAE < baseline MAE AND candidate RMSE < baseline RMSE",
    "G4_participation": "candidate Brier < heuristic Brier AND candidate log loss < heuristic log loss",
    "G5_participation_calibration": "calibration slope in [0.80, 1.25] AND |calibration-in-the-large| <= 0.25",
    "G6_pricing_integrity": "every PMF row sums to 1 within 1e-9, no negative entry, 0 P(over) monotonicity "
                            "violations, push semantics exact, every fair price finite where 0 < p < 1",
    "verdict": "GO only if G1-G6 all hold; any failure = NO-GO for this frozen candidate, never tuned on (protocol §3)"}


def scorecard() -> dict:
    return {
        "scorecard_id": "CBB_PROPS_V1_LOCKBOX_SCORECARD", "version": "2.1.0",
        "status": "FROZEN_CONTENT: G1-G6 ratified exactly as written (architect ruling F, 2026-10-13). Binding for the "
                  "opening once committed before opening and cited by sha256 in the LOCKBOX_OPEN decision (protocol §1.2 / §1.4)",
        "supersedes": "content 2.0.0 of this file (identical G1-G6, aggregation, metrics and reference values; 2.1.0 adds "
                      "the r2 candidate identity, the architect rulings and the fail-closed missing-data semantics)",
        "rulings": RULINGS,
        "candidate": {"model_tag": MODEL_TAG, "model_commit": MODEL_COMMIT, "original_tag": ORIGINAL_TAG,
                      "original_commit": ORIGINAL_COMMIT, "artifact": ARTIFACT, "artifact_sha256": ARTIFACT_SHA256,
                      "specification_fingerprint": SPEC_FINGERPRINT, "artifact_fingerprint": ARTIFACT_FINGERPRINT},
        "season": "2025-26 (canonical 2026), every game scored under the frozen 2024-25 eligibility rules",
        "checkpoint": "T-1h only (prediction cutoff = scheduled tip - 1 h, spec.CUTOFF_RULE); the other four pregame "
                      "checkpoints are N/A for v1 (ruling E)",
        "procedure": "the artifact is loaded, never refit, recalibrated or re-selected (protocol §2.4); the rolling "
                     "baseline and the participation heuristic are computed by their frozen code with the same inputs",
        "evaluator": "cbb_platform/lockbox_runner.py (gates() implements pass_criteria; development reproduction in "
                     "data/qa/lockbox_runner_validation.json)",
        "scoring_populations": {
            "participation": "pregame candidates labelled PLAYED (1) or DNP_CONFIRMED (0); UNKNOWN_PARTICIPATION and "
                             "NOT_ON_ROSTER are excluded and counted (props_v0_participation)",
            "minutes_and_counts": "PLAYED rows with a box row and an available label (props_v0); predictions are "
                                  "conditional on appearance; rows with an unavailable label are excluded and counted"},
        "metrics": {
            "minutes": ["mae", "rmse"],
            "counts": {"targets": TARGETS, "gated": ["log_score", "crps"],
                       "descriptive": ["mae", "coverage_50", "coverage_80", "coverage_95", "mean_bias"]},
            "participation": {"gated": ["brier", "log_loss", "calibration_slope", "calibration_in_the_large"],
                              "descriptive": ["auc", "calibration buckets 0-10/10-25/25-50/50-75/75-90/90-100 %"]},
            "prop_threshold_calibration": {"descriptive": "per target, P(over) vs observed rate, Brier and log loss at "
                                           "the half-lines 0.5 / 1.5 / 2.5 (STL, BLK, FG3M, TOV, AST), 2.5 / 4.5 / 6.5 "
                                           "(REB), 5.5 / 9.5 / 14.5 (PTS); flagged (not failed) when |gap| > 0.02"},
            "pricing_integrity": ["max PMF row-sum error", "min PMF entry", "P(over) monotone in the line",
                                  "integer-line push = P(Y = L), half-line push = 0", "finite coherent fair prices"]},
        "aggregation": {
            "per_target": "unweighted mean over the scored player-game rows of the 2025-26 season (one row per player-game)",
            "aggregate_counts": "scored-row-weighted mean over the 7 count targets",
            "participation": "mean over scored candidates; calibration slope / in-the-large from the logistic "
                             "recalibration fit of y on logit(p), as in props_v0_participation",
            "uncertainty": "game-clustered CR1 95 % intervals reported for every gated difference (descriptive; gates "
                           "use point estimates)"},
        "pass_criteria": PASS_CRITERIA,
        "pass_fail_semantics": {
            "comparisons": "strict inequalities exactly as written; bounds in G5 are inclusive; G6 'push semantics exact' "
                           "= integer-line push equals P(Y = L) and P(over) + P(under) + P(push) = 1 within 1e-12, half-line "
                           "push = 0 exactly",
            "missing_data": "a gated value that cannot be computed (no scored rows, missing, NaN or infinite) FAILS its gate; "
                            "the verdict is then NO-GO. Rows with an unavailable label are excluded from that metric and "
                            "counted, never imputed",
            "no_reinterpretation": "no gate may be added, removed, re-weighted or re-thresholded after opening; the "
                                   "verdict is the mechanical output of the evaluator"},
        "threshold_basis": "G1-G5 carried unchanged from the v1 proposal (fixed from the protocol and the frozen "
                           "2023-24 / 2024-25 evidence); G6 restates the development integrity checks; nothing from 2025-26",
        "development_check": "on the frozen 2024-25 forward validation the candidate meets G1-G6 (reference values below; "
                             "reproduced fit-free by the evaluator)",
        "not_applicable_by_ruling": {
            "market_benchmark": "NOT_APPLICABLE_FOR_LOCKBOX_GO_NO_GO (ruling C): no rights-cleared historical CBB player-prop "
                                "market data exists; none is invented, scraped, synthesized or backfilled. No result may be "
                                "described as a market or sportsbook comparison",
            "energy_variogram_scores": "NOT_APPLICABLE (ruling D): v1 prices marginal player-stat distributions; there is no "
                                       "joint distribution to score",
            "five_pit_checkpoints": "NOT_APPLICABLE (ruling E): v1 is scored at the single pregame T-1h cutoff; the five "
                                    "checkpoints are all pregame, and only T-1h exists in the v1 feature store"},
        "labels_unavailable": "excluded and counted; never imputed",
        "no_post_open_changes": "this file's sha256 is recorded in the readiness record and must be cited in the "
                                "LOCKBOX_OPEN decision; any change after opening voids the evaluation",
        "frozen_reference_values_2024_25": _reference()}


# ============================================================================ gate evidence
def _refused() -> bool:
    try:
        lockbox.check_season(lockbox.LOCKBOX_SEASON)
    except lockbox.LockboxViolation:
        return True
    return False


def identity() -> dict:
    art = R.load_final(R.FINAL_ARTIFACT_PATH)
    at = lambda tag: hashlib.sha256(subprocess.run(["git", "show", f"{tag}:{ARTIFACT}"], cwd=ROOT, capture_output=True,
                                                   check=True).stdout).hexdigest()
    changed = sorted(_git("diff", "--name-only", ORIGINAL_TAG, MODEL_TAG).stdout.split())
    r = {"tag": MODEL_TAG, "tag_commit": _git("rev-list", "-n1", MODEL_TAG).stdout.strip(),
         "tag_is_annotated": _git("cat-file", "-t", MODEL_TAG).stdout.strip() == "tag",
         "original_tag_commit": _git("rev-list", "-n1", ORIGINAL_TAG).stdout.strip(),
         "original_tag_preserved": _git("rev-list", "-n1", ORIGINAL_TAG).stdout.strip() == ORIGINAL_COMMIT,
         "files_changed_original_to_r2": changed,
         "artifact_sha256_working_tree": sha(ARTIFACT), "artifact_sha256_at_tag": at(MODEL_TAG),
         "artifact_sha256_at_original_tag": at(ORIGINAL_TAG),
         "specification_fingerprint": art["final_spec_fingerprint"], "artifact_fingerprint": art["artifact_fingerprint"],
         "stl": art["v0_state"]["dispersion"]["stl"]["family"] + f" phi={art['v0_state']['dispersion']['stl']['phi']}",
         "rejected_changes_activated": art["final_spec"]["rejected_changes_activated"]}
    r["pass"] = (r["tag_commit"] == MODEL_COMMIT and r["tag_is_annotated"] and r["original_tag_preserved"]
                 and changed == sorted(STL_EVIDENCE) and r["artifact_sha256_working_tree"] == ARTIFACT_SHA256
                 and r["artifact_sha256_at_tag"] == ARTIFACT_SHA256 == r["artifact_sha256_at_original_tag"]
                 and r["specification_fingerprint"] == SPEC_FINGERPRINT and r["artifact_fingerprint"] == ARTIFACT_FINGERPRINT
                 and r["rejected_changes_activated"] == [])
    return r


def stl_evidence() -> dict:
    out = {}
    for f in STL_EVIDENCE:
        blob = subprocess.run(["git", "show", f"{MODEL_TAG}:{f}"], cwd=ROOT, capture_output=True)
        out[f] = {"in_r2_tag": blob.returncode == 0, "in_original_tag":
                  subprocess.run(["git", "cat-file", "-e", f"{ORIGINAL_TAG}:{f}"], cwd=ROOT, capture_output=True).returncode == 0,
                  "sha256_at_r2": hashlib.sha256(blob.stdout).hexdigest() if blob.returncode == 0 else None,
                  "sha256_working_tree": sha(f)}
    return {"files": out, "pass": all(v["in_r2_tag"] and v["sha256_at_r2"] == v["sha256_working_tree"] for v in out.values())}


def worktree() -> dict:
    lines = [l for l in _git("status", "--porcelain", "--untracked-files=all").stdout.splitlines() if l]
    tracked_dirty = [l[3:] for l in lines if not l.startswith("??")]
    untracked = sorted(l[3:] for l in lines if l.startswith("??"))
    other = [u for u in untracked if u != QA_FILE_EXCEPTION]
    return {"definition": "protocol §1.5 'a clean, committed working tree': no tracked modification and no untracked file, "
                          "except the single user-owned path of architect ruling I",
            "exception": {"path": QA_FILE_EXCEPTION, "ruling": RULINGS + " (ruling I)", "present": QA_FILE_EXCEPTION in untracked},
            "tracked_modifications": tracked_dirty, "untracked_other_than_exception": other,
            "pass": not tracked_dirty and not other}


def eligibility() -> dict:
    idx = _json(ELIG_INDEX)
    if idx is None:
        return {"pass": False, "reason": "manifests missing"}
    files = {m: {"path": v["path"], "sha256": sha(v["path"]), "tracked": _tracked(v["path"]),
                 "eligible_player_games_sha256": v["eligible_player_games_sha256"], "count": v["count"]}
             for m, v in idx["required_for_v1"].items()}
    return {"index": ELIG_INDEX, "index_sha256": sha(ELIG_INDEX), "required": files, "not_required": idx["not_required_for_v1"],
            "gate_1_linkage": idx["gate_1_linkage"], "gate_3_enforcement": idx["gate_3_enforcement"],
            "pass": set(files) == {"ELIG_PARTICIPATION", "ELIG_MINUTES", "ELIG_BOX_COUNTS"}}


def runner() -> dict:
    ev = _json(RUNNER_EVIDENCE)
    if ev is None:
        return {"pass": False, "reason": "no development validation evidence"}
    b, f = ev["evaluation_B"], ev["final_artifact_in_sample_functional_check"]
    return {"runner": "cbb_platform/lockbox_runner.py", "runner_sha256": sha("cbb_platform/lockbox_runner.py"),
            "evidence": RUNNER_EVIDENCE, "evidence_sha256": sha(RUNNER_EVIDENCE),
            "runner_sha256_matches_evidence": ev["runner_sha256"] == sha("cbb_platform/lockbox_runner.py"),
            "development_reproduction": b["reproduction"]["reproduced"], "fit_calls": b["fit_calls"] + f["fit_calls"],
            "deterministic": b["deterministic_rerun_byte_identical"] and f["deterministic_rerun_byte_identical"],
            "development_gates": b["gates"], "lockbox_season_refused_while_closed": _refused(),
            "pass": (b["reproduction"]["reproduced"] and not any(b["fit_calls"] + f["fit_calls"])
                     and b["deterministic_rerun_byte_identical"] and f["deterministic_rerun_byte_identical"]
                     and ev["runner_sha256"] == sha("cbb_platform/lockbox_runner.py") and _refused())}


def route() -> dict:
    """The accepted, closed CBBD route (rulings K-O): the authoritative reconciliation report and the PIT audit, both
    bound to the current route module by sha256; the measured E8 and its architect classification both recorded."""
    smoke, full, pit = _json(ROUTE_SMOKE), _json(ROUTE_FULL), _json(ROUTE_PIT)
    v = (full or {}).get("verdict", {})
    r = {"module": ROUTE_MODULE, "module_sha256": sha(ROUTE_MODULE), "route_version": (full or {}).get("route_version"),
         "reconciler": RECONCILER, "reconciler_sha256": sha(RECONCILER),
         "reconciliation_spec": ROUTE_SPEC, "reconciliation_spec_sha256": sha(ROUTE_SPEC),
         "rulings": RULINGS_2 + " (K-O)", "lockbox_season_refused_while_closed": _refused(),
         "context_plan_matches_development_requests": "tests/test_cbbd_box_route.py (2023-24, 2024-25)",
         "smoke_scale": None if smoke is None else {
             "evidence": ROUTE_SMOKE, "evidence_sha256": sha(ROUTE_SMOKE), "unexplained": smoke["staging_reconciliation"]["unexplained"]},
         "full_development_seasons": None if full is None else {
             "evidence": ROUTE_FULL, "evidence_sha256": sha(ROUTE_FULL), "seasons": full["route_seasons"],
             "report_route_module_sha256": full["route_module_sha256"], "route_build": full["route_build"],
             "architect_ruling": full.get("architect_ruling"),
             "verdict": {k: v.get(k) for k in (
                 "CBBD_INGESTION_ROUTE", "CBBD_AUTHORITY", "BOUNDARY_DATE_LOSSES", "FALSE_DI_CLASSIFICATIONS",
                 "NON_FINAL_GAMES_IN_MODELING_STATE", "UNRESOLVED_TEAM_ASSIGNMENTS", "SURVIVING_CANONICAL_DUPLICATES",
                 "UNRESOLVED_PLAYER_IDENTITIES", "EXPLAINED_PROVIDER_DATA_REVISIONS", "EXPLAINED_PROVIDER_LABEL_REVISIONS",
                 "UNEXPLAINED_CANONICAL_DIFFERENCES", "UNTRACED_E6_FEATURE_ROWS", "E1", "E2", "E3", "E4", "E5", "E6", "E7",
                 "E8_FROZEN_REFERENCE_COMPARISON", "E8_MAX_GATED_METRIC_DIFFERENCE", "E8_DIFFERENCE_CLASSIFICATION",
                 "E8_ABLATION_MAX_GATED_METRIC_DIFFERENCE", "E8_GATE_VERDICTS_IDENTICAL", "DETERMINISTIC", "FIT_CALLS",
                 "PREDICTIVE_SPEC_CHANGED", "GUARD_REFUSES_2025_26", "2025_26_TOUCHED")}},
         "pit_audit": None if pit is None else {
             "evidence": ROUTE_PIT, "evidence_sha256": sha(ROUTE_PIT), "audit_module_sha256": pit["audit_module_sha256"],
             "route_module_sha256": pit["route_module_sha256"], "route_build": pit["route_build"],
             "violations_by_category": pit["violations_by_category"], "PIT_VIOLATIONS": pit["PIT_VIOLATIONS"]}}
    pit_ok = bool(pit) and pit["PIT_VIOLATIONS"] == 0 and set(pit["violations_by_category"]) == set(PIT_CATEGORIES) \
        and not any(pit["violations_by_category"].values()) and pit["route_module_sha256"] == r["module_sha256"] \
        and pit["audit_module_sha256"] == sha("cbb_platform/cbbd_box_route_pit_audit.py") \
        and full is not None and pit["route_build"] == full["route_build"]
    r["anti_leakage_contract"] = "PASS" if pit_ok else "FAIL"
    r["pass"] = bool(full and full.get("pass") and v.get("CBBD_INGESTION_ROUTE") == "PASS"
                     and full["route_module_sha256"] == r["module_sha256"] and v.get("FIT_CALLS") == 0
                     and v.get("PREDICTIVE_SPEC_CHANGED") is False and v.get("2025_26_TOUCHED") is False and pit_ok)
    r["remaining"] = "" if r["pass"] else "route evidence missing, stale (module sha256) or not passing"
    return r


def _apply_check_sequence(patches) -> bool:
    """`git apply --check` of the patches in order against HEAD, in a throwaway index (the worktree is never touched)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, env=env, capture_output=True).returncode == 0
        return run("read-tree", "HEAD") and all(run("apply", "--cached", p) for p in patches)


def guard() -> dict:
    v3_ok, sc_ok = (sha(p) == APPROVED_DESIGN_SHA256[p] for p in (GUARD_PATCH_V3, SCORING_PATCH))
    v3_text = (ROOT / GUARD_PATCH_V3).read_text()
    return {"live_guard_closed": _refused() and not hasattr(lockbox, "opening_authorized"),
            "decision_record_absent": not (C.MANIFESTS / "LOCKBOX_OPEN_DECISION_2025_26.json").exists(),
            "v2": {"patch": GUARD_PATCH, "sha256": sha(GUARD_PATCH), "applies_cleanly":
                   _git("apply", "--check", GUARD_PATCH, check=False).returncode == 0,
                   "human_design_ruling": "PASS under ruling H for the reviewed v2 bytes; superseded by v3 (ruling Q)"},
            "v3": {"patch": GUARD_PATCH_V3, "sha256": sha(GUARD_PATCH_V3), "applies_cleanly":
                   _git("apply", "--check", GUARD_PATCH_V3, check=False).returncode == 0,
                   "difference_from_v2": "adds exactly 'stats/team/season' to OPEN_PLAN_ENDPOINTS",
                   "opens_stats_team_season": '"stats/team/season"' in v3_text,
                   "closed_by_default": "OPEN_DECISION_SHA256: str | None = None" in v3_text,
                   "later_seasons_refused": "s > LOCKBOX_SEASON" in v3_text and "after {LOCKBOX_LABEL}" in v3_text,
                   "engineering_review": "PASS (synthetic + extended checks; tests/test_guard_v2_review.py)",
                   "human_design_ruling": ("PASS: design approved as reviewed, design approval only (ruling Q, " + RULINGS_2 + ")")
                   if v3_ok else "FAIL: bytes differ from the design approved by ruling Q"},
            "scoring_enable": {"patch": SCORING_PATCH, "sha256": sha(SCORING_PATCH), "applies_cleanly":
                               _git("apply", "--check", SCORING_PATCH, check=False).returncode == 0,
                               "applies_after_v3": _apply_check_sequence((GUARD_PATCH_V3, SCORING_PATCH)),
                               "enables_only_when_authorized": "not lockbox.opening_authorized()" in (ROOT / SCORING_PATCH).read_text(),
                               "human_design_ruling": ("PASS: design approved as reviewed, design approval only (ruling Q)")
                               if sc_ok else "FAIL: bytes differ from the design approved by ruling Q"},
            "design_approved": v3_ok and sc_ok,
            "checks_sha256": {c: sha(c) for c in GUARD_CHECKS}, "applied": False}


def relocation_evidence() -> dict:
    """Ruling P: frozen manifests keep their historical paths (unmodified at HEAD); the Starter Pack resolves to its
    current location only with its registered content proven identical; the frozen development DB is not modified."""
    from cbb_platform import relocation as RL
    frozen = ("data/manifests/starter_pack_registration.json", "data/manifests/starter_pack_sha256.json",
              "data/manifests/workspace_snapshot_stage2.json", "data/manifests/workspace_snapshot_pre_model_closure_v1.json")
    sp = RL.starter_pack_identity()
    db = ROOT / FROZEN_DEV_DB
    return {"historical_project_parent": str(RL.HISTORICAL_PROJECT_PARENT), "relocated": RL.relocated(),
            "rule": RULINGS_2 + " (P); cbb_platform/relocation.py", "starter_pack": sp,
            "frozen_manifests": {f: sha(f) for f in frozen},
            "frozen_manifests_modified": _git("diff", "--quiet", "HEAD", "--", *frozen, check=False).returncode != 0,
            "frozen_dev_db": {"path": FROZEN_DEV_DB, "size": db.stat().st_size, "mtime_ns": db.stat().st_mtime_ns,
                              "binding": "tests/conftest.py::starter_db (READ_ONLY attach + in-memory staging overlay)"}}


def gates(env: dict, lock: dict) -> dict:
    status = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())["status"]
    ident, stl, wt, elig, run, rt, gd = identity(), stl_evidence(), worktree(), eligibility(), runner(), route(), guard()
    rel = relocation_evidence()
    suite = _json(SUITE_PATH)
    a2 = {k: (elig["pass"] if v is None and "eligibility" in k else lock["pass"] if v is None else v) for k, v in PHASE_A2.items()}
    lock_tracked = all(_tracked(f) for f in LOCK_FILES) and _tracked("ENVIRONMENT_FINGERPRINT.json")
    sc = SCORECARD_PATH.relative_to(ROOT)
    p = lambda ok: "PASS" if ok else "FAIL"
    g = {
        "CANDIDATE_IDENTITY": {"status": p(ident["pass"]), "evidence": ident},
        "STL_EVIDENCE_SELF_CONTAINED": {"status": p(stl["pass"]), "evidence": stl},
        "ELIGIBILITY_MANIFESTS": {"status": p(elig["pass"]), "evidence": elig},
        "DATASET_MODEL_READY_BLUEPRINT_A2": {
            "status": "OPEN" if not all(a2.values()) else "PASS",
            "evidence": {"dataset_status": status, "phase_a2": a2, "a2_n_a_for_v1": A2_N_A_FOR_V1,
                         "scope": "the broader Blueprint v1.4 §8 dataset-level program; informational for this frozen "
                                  "candidate under ruling R (amends ruling A only to separate candidate readiness); items "
                                  "marked false are NOT done and are not marked complete; DATASET_MANIFEST.json unchanged"}},
        "MARKET_BENCHMARK": {"status": "N/A", "evidence": {"ruling": RULINGS + " C"}},
        "ENERGY_VARIOGRAM": {"status": "N/A", "evidence": {"ruling": RULINGS + " D"}},
        "FIVE_PIT_CHECKPOINTS": {"status": "N/A", "evidence": {"ruling": RULINGS + " E", "scored_checkpoint": "T-1h"}},
        "SCORECARD_FROZEN": {"status": p(scorecard()["pass_criteria"] == PASS_CRITERIA and _tracked(str(sc))),
                             "evidence": {"path": str(sc), "sha256": sha(sc), "g1_g6_verbatim": True, "committed": _tracked(str(sc))}},
        "CBBD_INGESTION_ROUTE": {"status": p(rt["pass"]), "evidence": rt},
        "ANTI_LEAKAGE_CONTRACT": {"status": rt["anti_leakage_contract"], "evidence": rt["pit_audit"]},
        "STARTER_PACK_CONTENT_IDENTITY": {"status": p(rel["starter_pack"]["pass"] and rel["starter_pack"]["registered_data_files"] == 111),
                                          "evidence": rel["starter_pack"]},
        "RELOCATION_SAFE_RESOLUTION": {"status": p(rel["starter_pack"]["pass"] and not rel["frozen_manifests_modified"]),
                                       "evidence": rel},
        "FIT_FREE_LOCKBOX_RUNNER": {"status": p(run["pass"]), "evidence": run},
        "GUARD_ENGINEERING_REVIEW": {"status": "PASS", "evidence": gd},
        "GUARD_HUMAN_DESIGN_RULING": {"status": p(gd["design_approved"]), "evidence": {
            "v3": gd["v3"]["human_design_ruling"], "scoring_enable": gd["scoring_enable"]["human_design_ruling"],
            "scope": "design approval only; neither patch applied (ruling Q)"}},
        "GUARD_V3_READY": {"status": p(gd["design_approved"] and gd["v3"]["applies_cleanly"] and gd["v3"]["opens_stats_team_season"]
                                       and gd["v3"]["closed_by_default"] and gd["v3"]["later_seasons_refused"]),
                           "evidence": gd["v3"]},
        "SCORING_LOADER_PATCH_READY": {"status": p(gd["design_approved"] and gd["scoring_enable"]["applies_after_v3"]
                                                   and gd["scoring_enable"]["enables_only_when_authorized"]),
                                       "evidence": gd["scoring_enable"]},
        "GUARD_APPLIED": {"status": "FALSE", "evidence": {"live_guard_closed": gd["live_guard_closed"]}},
        "ENVIRONMENT_LOCK": {"status": p(lock["pass"] and lock["requirements_lock_matches_uv_lock"] and lock_tracked
                                         and lock["required_versions_match"]),
                             "evidence": {**lock, "lock_files_committed": lock_tracked, "uv_lock_sha256": env["environment_lock_sha256"],
                                          "environment_fingerprint": env["environment_fingerprint"]}},
        "WORKTREE_CLEANLINESS": {"status": p(wt["pass"]), "evidence": wt},
        "DATA_OPENING_PLAN_FROZEN": {"status": p(rt["pass"] and gd["design_approved"] and _tracked(PLAN_PATH)),
                                     "evidence": {"path": PLAN_PATH, "sha256": sha(PLAN_PATH), "committed": _tracked(PLAN_PATH)}},
        "LOCKBOX_GUARD_CLOSED": {"status": p(gd["live_guard_closed"] and gd["decision_record_absent"]),
                                 "evidence": {"season_2026_refused": _refused()}},
        "TRACKED_TEST_SUITE": {"status": p(bool(suite) and suite["failed"] == 0 and suite["passed"] > 0),
                               "evidence": suite or {"reason": "no recorded run"}},
        "LOCKBOX_OPEN_APPROVAL": {"status": "WITHHELD", "evidence": {"approved_by": [], "lockbox_open": False}}}
    required = [k for k in g if k not in INFORMATIONAL and k != "LOCKBOX_OPEN_APPROVAL"]
    failing = [k for k in required if g[k]["status"] not in NON_APPROVAL_OK]
    g["CANDIDATE_MODEL_READY"] = {"status": p(not failing), "evidence": {
        "ruling": RULINGS_2 + " (R)", "candidate": MODEL_TAG, "artifact_sha256": ARTIFACT_SHA256,
        "required_gates": required, "failing": failing,
        "meaning": "the frozen candidate is technically ready for the one-time lockbox evaluation; NOT lockbox opened, "
                   "NOT production approved, NOT deployed"}}
    g["LOCKBOX_OPEN_APPROVAL"] = g.pop("LOCKBOX_OPEN_APPROVAL")             # keep approval last
    return g


NON_APPROVAL_OK = {"PASS", "N/A", "FALSE"}
# ruling R (2026-10-16): the Blueprint dataset-level A2 program is recorded, not a gate of this frozen candidate
INFORMATIONAL = {"DATASET_MODEL_READY_BLUEPRINT_A2"}
A2_N_A_FOR_V1 = {"A2.1 bitemporal five-checkpoint rebuild (PRESEASON_ASSUMED replaces the 1900-01-01 sentinel)":
                 "N/A for v1: scored at the single T-1h cutoff (ruling E); open for the Blueprint program"}


# ============================================================================ readiness record (ledger-shaped)
def readiness(env: dict) -> dict:
    art = R.load_final(R.FINAL_ARTIFACT_PATH)
    s3 = json.loads(spec.STAGE3_MANIFEST.read_text())
    dm = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    lock = lock_matches_venv()
    g = gates(env, lock)
    elig = g["ELIGIBILITY_MANIFESTS"]["evidence"]
    route_v = g["CBBD_INGESTION_ROUTE"]["evidence"]["full_development_seasons"]["verdict"]
    non_approval = [k for k, v in g.items() if k != "LOCKBOX_OPEN_APPROVAL" and k not in INFORMATIONAL
                    and v["status"] not in NON_APPROVAL_OK]
    rec = {
        "record_id": "LOCKBOX_READINESS_CBB_PROPS_V1", "version": "3.1.0",
        "supersedes": "content 3.0.0 of this file (uncommitted; route FAIL, guard v3 ruling pending), content 2.0.0 (candidate "
                      "tag cbb-props-v1-prelockbox-2024-25) and LOCKBOX_READINESS_v1.json (c123a3b; archived outside the repository)",
        "lockbox_open": False, "lockbox_touched": False, "touched_2025_26": False, "used_2026_27_preseason_data": False,
        "protocol": "docs/LOCKBOX_OPENING_PROTOCOL.md v1.0 + LOCKBOX_POLICY.md + docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md",
        "rulings": {"path": RULINGS, "sha256": sha(RULINGS), "route_closure": {"path": RULINGS_2, "sha256": sha(RULINGS_2)}},
        "cbbd_route": {"module": ROUTE_MODULE, "module_sha256": g["CBBD_INGESTION_ROUTE"]["evidence"]["module_sha256"],
                       "reconciler": RECONCILER, "reconciler_sha256": g["CBBD_INGESTION_ROUTE"]["evidence"]["reconciler_sha256"],
                       "reconciliation_report": ROUTE_FULL, "reconciliation_report_sha256": sha(ROUTE_FULL),
                       "pit_audit": ROUTE_PIT, "pit_audit_sha256": sha(ROUTE_PIT),
                       "CBBD_INGESTION_ROUTE": g["CBBD_INGESTION_ROUTE"]["status"], "CBBD_AUTHORITY": "ACCEPTED (ruling K)",
                       "E8_FROZEN_REFERENCE_COMPARISON": route_v["E8_FROZEN_REFERENCE_COMPARISON"],
                       "E8_MAX_GATED_METRIC_DIFFERENCE": route_v["E8_MAX_GATED_METRIC_DIFFERENCE"],
                       "E8_DIFFERENCE_CLASSIFICATION": route_v["E8_DIFFERENCE_CLASSIFICATION"],
                       "E8_GATE_VERDICTS_IDENTICAL": route_v["E8_GATE_VERDICTS_IDENTICAL"],
                       "ANTI_LEAKAGE_CONTRACT": g["ANTI_LEAKAGE_CONTRACT"]["status"],
                       "PIT_VIOLATIONS": g["CBBD_INGESTION_ROUTE"]["evidence"]["pit_audit"]["PIT_VIOLATIONS"],
                       "DETERMINISTIC_A_B": route_v["DETERMINISTIC"], "FIT_CALLS": route_v["FIT_CALLS"],
                       "PREDICTIVE_SPEC_CHANGED": route_v["PREDICTIVE_SPEC_CHANGED"]},
        "starter_pack": {k: g["STARTER_PACK_CONTENT_IDENTITY"]["evidence"][k] for k in (
            "registered_root", "current_root", "registered_data_files", "registered_data_digest", "current_data_digest")},
        "candidate": {"authoritative_tag": MODEL_TAG, "authoritative_commit": MODEL_COMMIT, "original_tag": ORIGINAL_TAG,
                      "original_commit": ORIGINAL_COMMIT, "superseded_tag_preserved": g["CANDIDATE_IDENTITY"]["evidence"]["original_tag_preserved"]},
        "model_artifact": {                                                    # ledger.model_artifact shape
            "model_version": art["model_version"], "artifact_version": art["artifact_version"],
            "subsystem": "player_props_marginal (participation + minutes + 7 counts)",
            "artifact_sha256": ARTIFACT_SHA256, "storage_uri": ARTIFACT, "specification_fingerprint": SPEC_FINGERPRINT,
            "artifact_fingerprint": ARTIFACT_FINGERPRINT, "dataset_version": art["dataset_version"], "dataset_status": dm["status"],
            "dataset_linkage": {"canonical_content_digest": dm["canonical_content_digest"], "manifest_sha256": sha("data/manifests/DATASET_MANIFEST.json")},
            "feature_version": {"stage3_version": s3["version"], "features_db_sha_of_fingerprints": s3["features_db_sha_of_fingerprints"],
                                "labels_db_sha_of_fingerprints": s3["labels_db_sha_of_fingerprints"]},
            "eligibility_manifests": {m: {"sha256": v["sha256"], "eligible_player_games_sha256": v["eligible_player_games_sha256"]}
                                      for m, v in elig.get("required", {}).items()},
            "code_sha": MODEL_COMMIT, "model_tag": MODEL_TAG, "environment_lock_sha256": env["environment_lock_sha256"],
            "environment_fingerprint": env["environment_fingerprint"],
            "hyperparameters": {"base_spec_fingerprint": art["spec_fingerprint"], "config_sha256": sha(EVIDENCE[2]),
                                "participation_l2_alpha": Q.L2_ALPHA, "stl_family": "NEGBIN", "stl_phi": 0.16},
            "seed": 0, "seed_note": "no randomness: closed-form ridge / IRLS / method of moments",
            "train_seasons": {"stat_minutes": list(range(art["stat_training_seasons"][0], art["training_through_season"] + 1)),
                              "participation": art["participation_training_seasons"]},
            "training_through": "2024-25",
            "validation_evidence": {f: sha(f) for f in (EVIDENCE[0], EVIDENCE[1], VALIDATION, EVIDENCE[5], STL_EVIDENCE[0])},
            "lockbox_touched": False},
        "calibration_artifact": {"calibration_version": "NONE", "note": "v1 applies no post-hoc calibration layer"},
        "scorecard": {"path": str(SCORECARD_PATH.relative_to(ROOT)), "sha256": sha(SCORECARD_PATH.relative_to(ROOT))},
        "data_opening_plan": {"path": PLAN_PATH, "sha256": sha(PLAN_PATH)},
        "guard": {"patch": GUARD_PATCH, "patch_sha256": sha(GUARD_PATCH), "v3_patch": GUARD_PATCH_V3,
                  "v3_patch_sha256": sha(GUARD_PATCH_V3), "scoring_enable_patch": SCORING_PATCH,
                  "scoring_enable_patch_sha256": sha(SCORING_PATCH), "design_approval": "ruling Q (design approval only)",
                  "applied": False, "season_2026_refused": _refused()},
        "fit_free_runner": {"path": "cbb_platform/lockbox_runner.py", "sha256": sha("cbb_platform/lockbox_runner.py")},
        "archive_manifest": {"path": str(ARCHIVE_MANIFEST), "sha256": hashlib.sha256(ARCHIVE_MANIFEST.read_bytes()).hexdigest()
                             if ARCHIVE_MANIFEST.exists() else None},
        "approval_record": {                                                   # ledger.promotion_decision shape
            "decision_id": "LOCKBOX_OPEN_2025_26_CBB_PROPS_V1", "decision_type": "LOCKBOX_OPEN", "lockbox_open": False,
            "status": "NOT_GRANTED", "from_bundle": None,
            "to_bundle": {"player_props_marginal": art["artifact_version"], "artifact_sha256": ARTIFACT_SHA256, "tag": MODEL_TAG},
            "evidence_uri": READINESS_DOC, "approved_by": [], "approved_at": None, "effective_at": None,
            "approval_mode_required": "two human approvals, OR one architect ruling that states only one person is available"},
        "gates": g,
        "non_approval_blockers": non_approval,
        "MODEL_READY": g["CANDIDATE_MODEL_READY"]["status"],
        "MODEL_READY_SCOPE": "candidate-specific (ruling R); dataset status stays " + dm["status"],
        "NON_APPROVAL_LOCKBOX_GATES": "PASS" if not non_approval else "FAIL",
        "LOCKBOX_OPEN_APPROVAL": "WITHHELD",
        "LOCKBOX_READY": "FAIL_APPROVAL_ONLY" if not non_approval else "FAIL"}
    rec["record_sha256"] = hashlib.sha256(_canon(rec).encode()).hexdigest()
    return rec


def write() -> dict:
    env = environment()
    dirty = bool([l for l in _git("status", "--porcelain", "--untracked-files=no").stdout.splitlines() if l])
    ENV_PATH.write_text(json.dumps({**env, "recorded_for_candidate_commit": MODEL_COMMIT,          # policy §2.2: HEAD + dirty
                                    "recorded_at_git_head": _git("rev-parse", "HEAD").stdout.strip(),
                                    "tracked_tree_dirty": dirty}, indent=1) + "\n")
    SCORECARD_PATH.write_text(json.dumps(scorecard(), indent=1) + "\n")
    rec = readiness(env)
    LEDGER_PATH.write_text(json.dumps(rec, indent=1) + "\n")
    return {"environment_fingerprint": env["environment_fingerprint"], "scorecard_sha256": sha(SCORECARD_PATH.relative_to(ROOT)),
            "readiness_record_sha256": rec["record_sha256"], "readiness_file_sha256": sha(LEDGER_PATH.relative_to(ROOT)),
            "NON_APPROVAL_LOCKBOX_GATES": rec["NON_APPROVAL_LOCKBOX_GATES"], "blockers": rec["non_approval_blockers"],
            "LOCKBOX_READY": rec["LOCKBOX_READY"]}


if __name__ == "__main__":
    print(json.dumps(write(), indent=1))
