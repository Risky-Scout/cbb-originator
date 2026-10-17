"""Lockbox lifecycle state of this tree, and the verification of the post-lockbox production baseline.

    PRE_LOCKBOX_READINESS    guard v3 / scoring-enable not applied (no lockbox.opening_authorized), no decision record
                             (readiness commit d65aac9 and earlier)
    LOCKBOX_OPEN_2025_26     the applied guard is bound to the decision record (opening commit 7dbc0b5 only)
    POST_LOCKBOX_CLOSED_GO   the applied guard is re-closed (OPEN_DECISION_SHA256 = None) and the one committed result is GO
                             (closure commit 1d8d01f and every later commit)

Historical claims about an earlier state are verified at that commit (git objects, or a `git archive` copy of the code),
never by re-reading the live tree. Read-only: nothing here fetches, builds, scores or fits.

    .venv/bin/python -m cbb_platform.lockbox_state   -> data/qa/production_baseline_state.json
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from cbb_platform import config as C, lockbox

ROOT = C.PROJECT_ROOT
OUT = C.QA / "production_baseline_state.json"

PRE_LOCKBOX_READINESS = "PRE_LOCKBOX_READINESS"
LOCKBOX_OPEN = "LOCKBOX_OPEN_2025_26"
POST_LOCKBOX_CLOSED_GO = "POST_LOCKBOX_CLOSED_GO"

READINESS_COMMIT = "d65aac908c5b910476d95d299ae3cf49093d4733"
OPENING_COMMIT = "7dbc0b569271a42c8079946e3e27e76be86703c2"
CLOSURE_COMMIT = "1d8d01f3820fa24f83c36b7656df16a8d481c384"
MODEL_TAG, MODEL_COMMIT = "cbb-props-v1-prelockbox-2024-25-r2", "425211071413735be8e1497105ae51830c971c12"
GO_TAG = "cbb-props-v1-lockbox-2025-26-go"

ARTIFACT = "modeling/player_props_v1/final_prelockbox_2024_25_artifact.json"
ARTIFACT_SHA256 = "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"
SCORECARD = "docs/lockbox/lockbox_scorecard_v2.json"
SCORECARD_SHA256 = "27bdfbdd35b182b6eb88710d34310e9f5f71a262b850a88071b8ac085ce26ecd"
DECISION = "data/manifests/LOCKBOX_OPEN_DECISION_2025_26.json"
DECISION_SHA256 = "819eae1fb6086514101edcca77be0027d7a56d3e4d8d7741d8068f34758e7013"
RESULT = "docs/LOCKBOX_RESULT_2025_26.json"
RESULT_FILE_SHA256 = "e243aa84ad312a1b8578307148ac895d94a9d17c6be6070781d2a160f87d8aef"
RESULT_SHA256 = "c72f6a98c44f456234f0aca6807f63dc41db677c695bee39d6165385d569dda6"
SCORING_RUN = "data/lockbox_2025_26/scoring_run.json"
LOCKBOX_PIT = "data/qa/lockbox_2025_26_pit_audit.json"
DEV_PIT = "data/qa/cbbd_box_route_pit_audit.json"
PIT_MODULE = "cbb_platform/cbbd_box_route_pit_audit.py"
GUARD_V3 = "docs/lockbox/lockbox_open_2025_26_guard_v3.patch"
SCORING_PATCH = "docs/lockbox/lockbox_open_2025_26_scoring_enable.patch"
GUARD_V3_SHA256 = "366ba2d1d1e2aa88491cf0370e05695f020f436a5c83e818bb237a54641cba4e"     # design approved, ruling Q
SCORING_PATCH_SHA256 = "8f964e01062937fde8ff44a7c8af595d3659884ff223771bced2e6d32aff2be7"  # season-window gating only
GATES = ("G1_aggregate", "G2_per_target", "G3_minutes", "G4_participation", "G5_participation_calibration",
         "G6_pricing_integrity")
# the frozen predictive surface of the candidate (tests/test_lockbox_readiness.py FROZEN minus the two guard modules)
PREDICTIVE = ("cbb_platform/props_v0.py", "cbb_platform/props_v0_eval.py", "cbb_platform/props_v0_participation.py",
              "cbb_platform/props_v1_pricing.py", "cbb_platform/props_v1_refit.py", "cbb_platform/stage3/spec.py",
              "modeling/player_props_v0/config.json", "modeling/player_props_v1/final_prelockbox_spec.json", ARTIFACT)
_BINDING = re.compile(r'^OPEN_DECISION_SHA256: str \| None = (None|"([0-9a-f]{64})")', re.M)


# ----------------------------------------------------------------------------- git helpers (read-only)
def git(*args, check=True, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=check, env=env)


def blob(commit: str, path: str) -> bytes | None:
    r = git("show", f"{commit}:{path}", check=False)
    return r.stdout if r.returncode == 0 else None


def sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def file_sha(path: str) -> str | None:
    p = ROOT / path
    return sha(p.read_bytes()) if p.exists() else None


def commits_touching(path: str, rev: str = "HEAD") -> list[str]:
    return git("log", "--format=%H", rev, "--", path).stdout.decode().split()


def archive(commit: str, dest: Path, paths=("cbb_platform", "modeling/player_props_v0/config.json")) -> Path:
    """The code of `commit` extracted into `dest` (a throwaway copy; the worktree is never touched)."""
    tar = git("archive", "--format=tar", commit, *paths).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest, filter="data")
    return dest


def apply_check(commit: str, patches, reverse: bool = False) -> bool:
    """`git apply --cached --check` of the patches, in order, against the tree of `commit`, in a throwaway index."""
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        if git("read-tree", commit, check=False, env=env).returncode:
            return False
        flags = ["-R"] if reverse else []
        return all(git("apply", "--cached", *flags, p, check=False, env=env).returncode == 0 for p in patches)


def tree_after_patches(commit: str, patches, paths) -> dict[str, str] | None:
    """Blob ids of `paths` after applying the patches to the tree of `commit` (throwaway index)."""
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        git("read-tree", commit, env=env)
        if any(git("apply", "--cached", p, check=False, env=env).returncode for p in patches):
            return None
        out = git("ls-files", "-s", "--", *paths, env=env).stdout.decode().splitlines()
        return {l.split("\t", 1)[1]: l.split()[1] for l in out}


def binding_at(commit: str) -> str | None:
    """The guard's decision binding in cbb_platform/lockbox.py at `commit`: None when absent or closed."""
    text = (blob(commit, "cbb_platform/lockbox.py") or b"").decode()
    m = _BINDING.search(text)
    return m.group(2) if m else None


def unchanged_since_readiness_except_reviewed_patches(base: str, files) -> bool:
    """Post-lockbox form of a frozen-file check `git diff --quiet <base> -- files`: the files equal `base` at readiness
    commit d65aac9, every change since is exactly the output of the reviewed guard v3 + scoring-enable patches (season
    window gating, byte for byte), and nothing is uncommitted."""
    files = list(files)
    if git("diff", "--quiet", base, READINESS_COMMIT, "--", *files, check=False).returncode \
            or git("diff", "--quiet", "HEAD", "--", *files, check=False).returncode:
        return False
    if file_sha(GUARD_V3) != GUARD_V3_SHA256 or file_sha(SCORING_PATCH) != SCORING_PATCH_SHA256:
        return False
    changed = git("diff", "--name-only", READINESS_COMMIT, "HEAD", "--", *files).stdout.decode().split()
    patched = tree_after_patches(READINESS_COMMIT, (GUARD_V3, SCORING_PATCH), changed) if changed else {}
    return patched is not None and all(patched.get(f) == git("rev-parse", f"HEAD:{f}").stdout.decode().strip() for f in changed)


# ----------------------------------------------------------------------------- the authorized 2025-26 input set
REQUEST_LOG = "data/manifests/cbbd_request_log.jsonl"
AUTHORIZED_REQUESTS = 987              # data/manifests/cbbd_request_log.jsonl lines added by the closure commit


def authorized_lockbox_requests() -> list[str]:
    """The exact request-log lines of the one authorized 2025-26 fetch: the lines the closure commit added to the
    committed request log (its parent is the opening commit). Nothing else is authorized."""
    assert git("rev-parse", f"{CLOSURE_COMMIT}^").stdout.decode().strip() == OPENING_COMMIT
    diff = git("diff", "--unified=0", OPENING_COMMIT, CLOSURE_COMMIT, "--", REQUEST_LOG).stdout.decode().splitlines()
    lines = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
    assert not [l for l in diff if l.startswith("-") and not l.startswith("---")]      # append-only
    assert len(lines) == len(set(lines)) == AUTHORIZED_REQUESTS
    return lines


def authorized_lockbox_payloads() -> dict[str, str]:
    """{raw-store relative path: payload sha256} of the authorized fetch, from its request-log lines."""
    out = {}
    for line in authorized_lockbox_requests():
        e = json.loads(line)
        assert e["provider"] == "cbbd" and e["status"] == "STORED" and e.get("season") in (None, lockbox.LOCKBOX_SEASON)
        out[e["stored_file"]] = e["payload_sha256"]
    assert len(out) == AUTHORIZED_REQUESTS
    return out


def historical_raw_root(dest: Path) -> dict:
    """A shadow raw store under `dest`: a symlink to every file of data/raw EXCEPT exactly the authorized 2025-26
    payloads and their .meta.json files (verified by sha256 first). The real raw store is never written."""
    auth = authorized_lockbox_payloads()
    for rel, digest in auth.items():
        meta = json.loads(Path(str(C.RAW / rel) + ".meta.json").read_text())
        assert meta["sha256"] == digest, rel
    skipped = 0
    for dp, _, fns in os.walk(C.RAW):
        rel = Path(dp).relative_to(C.RAW)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for f in fns:
            r = str(rel / f)
            if r.removesuffix(".meta.json") in auth:
                skipped += 1
                continue
            os.symlink(Path(dp) / f, dest / rel / f)
    assert skipped == 2 * AUTHORIZED_REQUESTS, skipped
    return {"root": dest, "excluded_files": skipped}


# ----------------------------------------------------------------------------- state
def current() -> str:
    if not hasattr(lockbox, "opening_authorized"):
        return PRE_LOCKBOX_READINESS
    if lockbox.OPEN_DECISION_SHA256 is not None:
        return LOCKBOX_OPEN
    res = ROOT / RESULT
    if res.exists() and sha(res.read_bytes()) == RESULT_FILE_SHA256 and json.loads(res.read_text())["gates"]["verdict"] == "GO":
        return POST_LOCKBOX_CLOSED_GO
    raise RuntimeError("unrecognized lockbox state: guard applied and closed, but no committed GO result")


PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from cbb_platform import config as C, lockbox as L
def refused(f):
    try:
        f()
    except (L.LockboxViolation, ValueError):
        return True
    return False
from cbb_platform import props_v0 as P
r = {"opening_authorized_attr": hasattr(L, "opening_authorized"),
     "binding": getattr(L, "OPEN_DECISION_SHA256", None),
     "authorized": L.opening_authorized() if hasattr(L, "opening_authorized") else False,
     "season_2026": refused(lambda: L.check_season(2026)),
     "cbbd_2026": refused(lambda: L.check_request("cbbd", "games", {"season": 2026})),
     "cbbd_2026_box": refused(lambda: L.check_request("cbbd", "games/players", {"season": 2026})),
     "cbbd_2027": refused(lambda: L.check_request("cbbd", "games", {"season": 2027})),
     "cbbd_2027_box": refused(lambda: L.check_request("cbbd", "games/players", {"season": 2027})),
     "response_2026": refused(lambda: L.check_response("cbbd", {"season": 2026})),
     "stage3_2026": refused(lambda: P.load_frames(P.load_config(), seasons=[2026])),
     "stage3_2027": refused(lambda: P.load_frames(P.load_config(), seasons=[2027]))}
try:
    from cbb_platform.stage3 import spec
    r["model_seasons_max"], r["evaluation_seasons"] = max(spec.MODEL_SEASONS), list(spec.EVALUATION_SEASONS)
    r["audited_seasons"] = list(C.AUDITED_SEASONS)
except AttributeError:
    pass
print(json.dumps(r))
"""


def probe(code_root: Path, open_flag: bool) -> dict:
    """The guard's behaviour for the code under `code_root`, in a fresh interpreter (LOCKBOX_OPEN_2025_26 set or not)."""
    env = {k: v for k, v in os.environ.items() if k != "LOCKBOX_OPEN_2025_26"}
    if open_flag:
        env["LOCKBOX_OPEN_2025_26"] = "1"
    p = subprocess.run([sys.executable, "-c", PROBE, str(code_root)], cwd=code_root, env=env, capture_output=True,
                       text=True, check=True)
    return json.loads(p.stdout.strip().splitlines()[-1])


# ----------------------------------------------------------------------------- historical: PRE_LOCKBOX_READINESS at d65aac9
def historical_pre_lockbox() -> dict:
    """At readiness commit d65aac9 the lockbox was unopened: no guard patch applied (both still apply cleanly to that tree
    and do not reverse-apply), no decision record, no result, and that commit's code refuses 2025-26 even with the flag."""
    with tempfile.TemporaryDirectory() as d:
        code = archive(READINESS_COMMIT, Path(d))
        beh = {mode: probe(code, mode == "flag_set") for mode in ("flag_unset", "flag_set")}
    r = {"commit": READINESS_COMMIT, "behaviour": beh,
         "guard_patches_apply_cleanly": apply_check(READINESS_COMMIT, (GUARD_V3, SCORING_PATCH)),
         "guard_patches_not_applied": not apply_check(READINESS_COMMIT, (SCORING_PATCH, GUARD_V3), reverse=True),
         "decision_record_absent": blob(READINESS_COMMIT, DECISION) is None,
         "result_absent": blob(READINESS_COMMIT, RESULT) is None,
         "readiness_record_unchanged_since": commits_touching("data/manifests/LOCKBOX_READINESS_v2.json")[0] == READINESS_COMMIT}
    r["unopened"] = (all(not b["opening_authorized_attr"] and b["season_2026"] and b["cbbd_2026"] and b["stage3_2026"]
                         for b in beh.values())
                     and r["guard_patches_apply_cleanly"] and r["guard_patches_not_applied"]
                     and r["decision_record_absent"] and r["result_absent"])
    return r


# ----------------------------------------------------------------------------- current: POST_LOCKBOX_CLOSED_GO
def verify() -> dict:
    """The production baseline: opened once under authorization, one fit-free scoring run with result GO, guard closed
    again, 2025-26 and 2026-27 refused, model artifact and predictive surface unchanged, genuine PIT violations 0."""
    head = git("rev-parse", "HEAD").stdout.decode().strip()
    hist = git("rev-list", "HEAD").stdout.decode().split()
    res, run = json.loads((ROOT / RESULT).read_text()), json.loads((ROOT / SCORING_RUN).read_text())
    dec = json.loads((ROOT / DECISION).read_text())
    lpit, dpit = json.loads((ROOT / LOCKBOX_PIT).read_text()), json.loads((ROOT / DEV_PIT).read_text())

    # opened exactly once: the guard was bound to the decision record in exactly one commit, the opening commit
    lockbox_commits = commits_touching("cbb_platform/lockbox.py")
    bound = {c: binding_at(c) for c in lockbox_commits}
    opening = {
        "decision_record_sha256": file_sha(DECISION), "decision_record_commits": commits_touching(DECISION),
        "decision_lockbox_open": dec["lockbox_open"] is True and dec["decision_type"] == "LOCKBOX_OPEN" and bool(dec["approved_by"]),
        "decision_candidate_artifact_sha256": dec["candidate"]["artifact_sha256"],
        "commits_binding_the_guard": sorted(c for c, b in bound.items() if b is not None),
        "binding_at_opening_commit": bound.get(OPENING_COMMIT),
        "opening_commit_parent_is_readiness": git("rev-parse", f"{OPENING_COMMIT}^").stdout.decode().strip() == READINESS_COMMIT}
    opening["OPENED_EXACTLY_ONCE_UNDER_AUTHORIZATION"] = (
        opening["decision_record_sha256"] == DECISION_SHA256 and opening["decision_record_commits"] == [OPENING_COMMIT]
        and opening["decision_lockbox_open"] and opening["commits_binding_the_guard"] == [OPENING_COMMIT]
        and opening["binding_at_opening_commit"] == DECISION_SHA256 and opening["opening_commit_parent_is_readiness"]
        and OPENING_COMMIT in hist and READINESS_COMMIT in hist)

    # exactly one scoring run, GO: one committed result, written once and never rewritten, consistent with the run record
    gates = res["gates"]
    scoring = {
        "result_file_sha256": file_sha(RESULT), "result_sha256": res["result_sha256"],
        "result_commits": commits_touching(RESULT), "scoring_run_commits": commits_touching(SCORING_RUN),
        "scoring_run_result_sha256": run["result_sha256"], "verdict": gates["verdict"],
        "gates_pass": {g: gates[g]["pass"] for g in GATES}, "fit_calls": run["fit_calls"], "fit_free": res["fit_free"],
        "refit_recalibrated_or_selected": res["refit_recalibrated_or_selected"],
        "result_artifact_sha256": res["artifact"]["sha256"], "result_scorecard_sha256": res["scorecard"]["sha256"],
        "go_tag_commit": git("rev-list", "-n1", GO_TAG).stdout.decode().strip()}
    scoring["EXACTLY_ONE_SCORING_RUN_GO"] = (
        scoring["result_file_sha256"] == RESULT_FILE_SHA256 and scoring["result_sha256"] == RESULT_SHA256
        == scoring["scoring_run_result_sha256"] and scoring["result_commits"] == [CLOSURE_COMMIT]
        and scoring["scoring_run_commits"] == [CLOSURE_COMMIT] and scoring["verdict"] == "GO"
        and all(scoring["gates_pass"].values()) and scoring["go_tag_commit"] == CLOSURE_COMMIT)

    # the guard now: closed in this process, and in fresh interpreters with and without the opening flag
    beh = {mode: probe(ROOT, mode == "flag_set") for mode in ("flag_unset", "flag_set")}
    guard = {"binding": lockbox.OPEN_DECISION_SHA256, "behaviour": beh,
             "guard_v3_and_scoring_enable_applied": apply_check("HEAD", (SCORING_PATCH, GUARD_V3), reverse=True),
             "trusted_window": list(C.AUDITED_SEASONS), "schema_sql_is_frozen_text": C.schema_sql() == C.SCHEMA_SQL.read_text()}
    guard["GUARD_CURRENTLY_OPEN"] = bool(lockbox.OPEN_DECISION_SHA256 is not None or lockbox.opening_authorized()
                                         or any(b["authorized"] for b in beh.values()))
    refused_2026 = all(b["season_2026"] and b["cbbd_2026"] and b["cbbd_2026_box"] and b["response_2026"] and b["stage3_2026"]
                       and b["model_seasons_max"] == 2025 and b["evaluation_seasons"] == [] and b["audited_seasons"] == [2005, 2025]
                       for b in beh.values()) and guard["schema_sql_is_frozen_text"]
    refused_2027 = all(b["cbbd_2027"] and b["cbbd_2027_box"] and b["stage3_2027"] for b in beh.values())

    # the model: artifact bytes unchanged; predictive surface = r2 tag, except the reviewed window gating of the
    # scoring-enable patch (the files equal exactly the readiness tree + guard v3 + scoring-enable)
    patched = tree_after_patches(READINESS_COMMIT, (GUARD_V3, SCORING_PATCH), PREDICTIVE) or {}
    changed = sorted(git("diff", "--name-only", MODEL_TAG, "HEAD", "--", *PREDICTIVE).stdout.decode().split())
    dirty = sorted(git("diff", "--name-only", "HEAD", "--", *PREDICTIVE).stdout.decode().split())
    head_blob = lambda p: git("rev-parse", f"HEAD:{p}").stdout.decode().strip()
    model = {"artifact_sha256": file_sha(ARTIFACT), "artifact_sha256_at_r2_tag": sha(blob(MODEL_TAG, ARTIFACT)),
             "artifact_commits_after_r2": git("log", "--format=%H", f"{MODEL_TAG}..HEAD", "--", ARTIFACT).stdout.decode().split(),
             "scorecard_sha256": file_sha(SCORECARD), "predictive_files_changed_since_r2": changed,
             "predictive_files_dirty": dirty,
             "changed_files_equal_reviewed_patch_output": bool(changed) and all(patched.get(p) == head_blob(p) for p in changed),
             "scoring_enable_is_the_reviewed_window_gating": file_sha(SCORING_PATCH) == SCORING_PATCH_SHA256,
             "guard_v3_is_the_reviewed_design": file_sha(GUARD_V3) == GUARD_V3_SHA256}
    model["MODEL_ARTIFACT_UNCHANGED"] = (model["artifact_sha256"] == model["artifact_sha256_at_r2_tag"] == ARTIFACT_SHA256
                                         == scoring["result_artifact_sha256"] == opening["decision_candidate_artifact_sha256"]
                                         and model["artifact_commits_after_r2"] == [])
    model["PREDICTIVE_SPEC_CHANGED"] = not (model["changed_files_equal_reviewed_patch_output"] or not changed) \
        or bool(dirty) or not model["scoring_enable_is_the_reviewed_window_gating"] \
        or not model["guard_v3_is_the_reviewed_design"] or model["scorecard_sha256"] != SCORECARD_SHA256

    # PIT: the lockbox build (open-mode rule) and the development route build both 0; this tree's audit is mode-aware
    from cbb_platform import cbbd_box_route_pit_audit as PA
    pit = {"lockbox_audit": {k: lpit[k] for k in ("GENUINE_PIT_VIOLATIONS", "UNAUTHORIZED_FUTURE_SEASON_ROWS",
                                                  "AUTHORIZED_LOCKBOX_2026_ROWS_TOTAL", "PIT_VIOLATIONS", "season_rule",
                                                  "audit_module_sha256")},
           "lockbox_audit_module_is_current": lpit["audit_module_sha256"] == file_sha(PIT_MODULE),
           "development_audit_PIT_VIOLATIONS": dpit["PIT_VIOLATIONS"],
           "development_audit_module_at_readiness_commit": dpit["audit_module_sha256"] == sha(blob(READINESS_COMMIT, PIT_MODULE)),
           "current_season_rule": PA.season_rule()}
    pit["GENUINE_PIT_VIOLATIONS"] = int(lpit["GENUINE_PIT_VIOLATIONS"]) + int(dpit["PIT_VIOLATIONS"]) \
        + sum(lpit["violations_by_category"].values()) + sum(dpit["violations_by_category"].values())

    out = {"head": head, "state": current(), "opening": opening, "scoring": scoring, "guard": guard, "model": model,
           "pit": pit, "historical_pre_lockbox": historical_pre_lockbox()}
    out["summary"] = {
        "LOCKBOX_STATE": out["state"],
        "LOCKBOX_RESULT": scoring["verdict"] if scoring["EXACTLY_ONE_SCORING_RUN_GO"] else "UNVERIFIED",
        "OPENED_EXACTLY_ONCE_UNDER_AUTHORIZATION": opening["OPENED_EXACTLY_ONCE_UNDER_AUTHORIZATION"],
        "EXACTLY_ONE_SCORING_RUN_GO": scoring["EXACTLY_ONE_SCORING_RUN_GO"],
        "GUARD_CURRENTLY_OPEN": guard["GUARD_CURRENTLY_OPEN"],
        "CURRENT_LOCKBOX_ACCESS_WITHOUT_AUTHORIZATION": "REFUSED" if refused_2026 else "ALLOWED",
        "2027_ACCESS": "REFUSED" if refused_2027 else "ALLOWED",
        "MODEL_ARTIFACT_SHA256": model["artifact_sha256"], "MODEL_ARTIFACT_UNCHANGED": model["MODEL_ARTIFACT_UNCHANGED"],
        "FIT_CALLS": scoring["fit_calls"], "PREDICTIVE_SPEC_CHANGED": model["PREDICTIVE_SPEC_CHANGED"],
        "REFIT_PERFORMED": bool(res["refit_recalibrated_or_selected"]) or not model["MODEL_ARTIFACT_UNCHANGED"],
        "RECALIBRATION_PERFORMED": bool(res["refit_recalibrated_or_selected"]) or not model["MODEL_ARTIFACT_UNCHANGED"],
        "GENUINE_PIT_VIOLATIONS": pit["GENUINE_PIT_VIOLATIONS"],
        "PRE_LOCKBOX_READINESS_UNOPENED_AT_d65aac9": out["historical_pre_lockbox"]["unopened"]}
    s = out["summary"]
    out["pass"] = (s["LOCKBOX_STATE"] == POST_LOCKBOX_CLOSED_GO and s["LOCKBOX_RESULT"] == "GO"
                   and s["OPENED_EXACTLY_ONCE_UNDER_AUTHORIZATION"] and s["GUARD_CURRENTLY_OPEN"] is False
                   and s["CURRENT_LOCKBOX_ACCESS_WITHOUT_AUTHORIZATION"] == "REFUSED" and s["2027_ACCESS"] == "REFUSED"
                   and s["MODEL_ARTIFACT_SHA256"] == ARTIFACT_SHA256 and s["MODEL_ARTIFACT_UNCHANGED"] and s["FIT_CALLS"] == 0
                   and s["PREDICTIVE_SPEC_CHANGED"] is False and s["REFIT_PERFORMED"] is False
                   and s["RECALIBRATION_PERFORMED"] is False and s["GENUINE_PIT_VIOLATIONS"] == 0
                   and s["PRE_LOCKBOX_READINESS_UNOPENED_AT_d65aac9"] and guard["guard_v3_and_scoring_enable_applied"])
    return out


if __name__ == "__main__":
    res = verify()
    res.pop("head")       # the record is committed with the tree it describes; HEAD would be self-referential
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print(json.dumps({**res["summary"], "pass": res["pass"]}, indent=1))
