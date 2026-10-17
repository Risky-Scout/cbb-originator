"""Engineering review of the UNAPPLIED v2 opening guard (docs/lockbox/lockbox_open_2025_26_guard_v2.patch). The patch is
applied only inside a throwaway `git archive` copy; the live guard stays closed. Proves: the reviewed bytes are
unchanged; the patched guard is closed without a decision record; with a synthetic decision it opens only CBBD /
canonical 2025-26 for planned endpoints; 2026-27 and later, BDL, Torvik and unregistered providers stay refused;
reverting restores the byte-identical closed guard.

State-aware (cbb_platform/lockbox_state.py): the review is of the PRE_LOCKBOX_READINESS tree. In that state the patches
are applied to a copy of HEAD; after the lockbox (POST_LOCKBOX_CLOSED_GO, where v3 + scoring-enable are applied live) the
copy and the "not applied" checks use readiness commit d65aac9, the tree the patches were reviewed against."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import io

import pytest

from cbb_platform import config as C, lockbox, lockbox_state as S

PATCH = C.PROJECT_ROOT / "docs" / "lockbox" / "lockbox_open_2025_26_guard_v2.patch"
REVIEWED_V2_SHA256 = "c1ffec876bfb278585bd01d12a67c0bdd07d3736fee2ab8b7321bd2600c6076e"   # LOCKBOX_READINESS_v2 record
CHECKS = ("guard_v2_synthetic_check.py", "guard_v2_extended_check.py")
PRE = S.current() == S.PRE_LOCKBOX_READINESS
BASE = "HEAD" if PRE else S.READINESS_COMMIT          # the reviewed (unopened) tree


def _copy(tmp_path):
    tar = subprocess.run(["git", "archive", "--format=tar", BASE, "cbb_platform"], cwd=C.PROJECT_ROOT,
                         capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(tmp_path, filter="data")
    for name in CHECKS:
        (tmp_path / name).write_bytes((C.PROJECT_ROOT / "docs" / "lockbox" / name).read_bytes())
    return tmp_path


def _git_apply(d, *args):
    return subprocess.run(["git", "apply", *args, str(PATCH)], cwd=d, capture_output=True, text=True)


def _run(d, script):
    p = subprocess.run([sys.executable, "-I", script], cwd=d, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(d)})
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.rsplit("ALL_PASS", 1)[0]), p.stdout.strip().endswith("ALL_PASS True")


def test_patch_bytes_are_the_reviewed_v2():
    assert hashlib.sha256(PATCH.read_bytes()).hexdigest() == REVIEWED_V2_SHA256
    rec = json.loads((C.MANIFESTS / "LOCKBOX_READINESS_v2.json").read_text())
    assert rec["guard"]["patch_sha256"] == REVIEWED_V2_SHA256 or rec["guard"]["v2_patch_sha256"] == REVIEWED_V2_SHA256


def test_live_guard_is_closed(tmp_path):
    if PRE:
        assert not hasattr(lockbox, "opening_authorized") and not hasattr(lockbox, "OPEN_DECISION_SHA256")
        with pytest.raises(lockbox.LockboxViolation):
            lockbox.check_season(lockbox.LOCKBOX_SEASON)
        assert not (C.MANIFESTS / "LOCKBOX_OPEN_DECISION_2025_26.json").exists()
        assert _git_apply(C.PROJECT_ROOT, "--check", "-R").returncode != 0      # the patch is NOT applied to the live tree
        return
    # post-lockbox: the same facts for the reviewed tree, readiness commit d65aac9 (its own code, fresh interpreter)
    code = S.archive(S.READINESS_COMMIT, tmp_path)
    for flag in (False, True):
        b = S.probe(code, flag)
        assert not b["opening_authorized_attr"] and b["binding"] is None and b["season_2026"]
    assert S.blob(S.READINESS_COMMIT, "data/manifests/LOCKBOX_OPEN_DECISION_2025_26.json") is None
    assert not S.apply_check(S.READINESS_COMMIT, [str(PATCH)], reverse=True)   # the patch is NOT applied to that tree


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    d = _copy(tmp_path_factory.mktemp("guard_v2"))
    before = {f: (d / f).read_bytes() for f in ("cbb_platform/lockbox.py", "cbb_platform/source_fetch.py")}
    r = _git_apply(d)
    assert r.returncode == 0, r.stderr
    return d, before


def test_synthetic_check_all_pass(patched):
    d, _ = patched
    res, all_pass = _run(d, "guard_v2_synthetic_check.py")
    assert all_pass and len(res) == 18 and all(res.values())


def test_extended_check_all_pass(patched):
    d, _ = patched
    res, all_pass = _run(d, "guard_v2_extended_check.py")
    assert all_pass, {k: v for k, v in res.items() if v is False}
    assert res["open_planned_endpoints"] == sorted(["games", "games/players", "games/teams", "teams/roster", "substitutions/team",
                                                    "stats/player/season", "recruiting/players", "recruiting/portal"])


def test_v2_endpoint_set_lacks_the_route_endpoint_stats_team_season(patched):
    """Review finding: the CBBD box route needs stats/team/season (the D-I classification behind game_universe), which
    the reviewed v2 plan does not open. Recorded so v2 cannot be marked sufficient for the frozen data plan."""
    d, _ = patched
    res, _ = _run(d, "guard_v2_extended_check.py")
    assert res["route_endpoint_stats_team_season_allowed_by_v2"] is False


def test_revert_restores_closed_guard_byte_identically(patched, tmp_path):
    d, before = patched
    r = _git_apply(d, "-R")
    assert r.returncode == 0, r.stderr
    assert all((d / f).read_bytes() == b for f, b in before.items())
    code = ("import sys; sys.path.insert(0, '.'); from cbb_platform import lockbox as L\n"
            "try:\n    L.check_season(2026); print('OPEN')\nexcept L.LockboxViolation:\n    print('CLOSED')\n"
            "print(hasattr(L, 'opening_authorized'))")
    p = subprocess.run([sys.executable, "-I", "-c", code], cwd=d, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "LOCKBOX_OPEN_2025_26": "1"})
    assert p.stdout.split() == ["CLOSED", "False"], p.stderr
    assert _git_apply(d).returncode == 0                       # re-apply for any later fixture user


# ---------------------------------------------------------------------------- v3 candidate (PENDING human design review)
V3 = C.PROJECT_ROOT / "docs" / "lockbox" / "lockbox_open_2025_26_guard_v3.patch"
V3_SHA256 = "366ba2d1d1e2aa88491cf0370e05695f020f436a5c83e818bb237a54641cba4e"


def test_v3_differs_from_v2_only_by_the_route_endpoint():
    assert hashlib.sha256(V3.read_bytes()).hexdigest() == V3_SHA256
    body = lambda p: [l for l in p.read_text().splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    v2, v3 = body(PATCH), body(V3)
    removed = [l for l in v2 if l not in v3]
    added = [l for l in v3 if l not in v2]
    assert removed == ['+                                 "stats/player/season", "recruiting/players", "recruiting/portal"})   # frozen data plan']
    assert added == ['+                                 "stats/player/season", "stats/team/season", "recruiting/players",',
                     '+                                 "recruiting/portal"})   # frozen data plan (v3: + stats/team/season, the D-I proxy)']


def test_v3_checks_pass_and_revert(tmp_path):
    d = _copy(tmp_path)
    before = (d / "cbb_platform" / "lockbox.py").read_bytes()
    r = subprocess.run(["git", "apply", str(V3)], cwd=d, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    res, ok1 = _run(d, "guard_v2_synthetic_check.py")
    ext, ok2 = _run(d, "guard_v2_extended_check.py")
    assert ok1 and ok2 and len(res) == 18
    assert ext["route_endpoint_stats_team_season_allowed_by_v2"] is True     # the key name is shared with the v2 check
    assert "stats/team/season" in ext["open_planned_endpoints"] and len(ext["open_planned_endpoints"]) == 9
    assert subprocess.run(["git", "apply", "-R", str(V3)], cwd=d).returncode == 0
    assert (d / "cbb_platform" / "lockbox.py").read_bytes() == before


# ---------------------------------------------------------------------------- opening-time scoring enable (PENDING review)
SCORING = C.PROJECT_ROOT / "docs" / "lockbox" / "lockbox_open_2025_26_scoring_enable.patch"
SCORING_SHA256 = "8f964e01062937fde8ff44a7c8af595d3659884ff223771bced2e6d32aff2be7"
PROBE = """
import hashlib, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd())
from cbb_platform import lockbox as L
assert Path(L.__file__).resolve().is_relative_to(Path.cwd().resolve())
if sys.argv[1] == "open":
    rec = Path(tempfile.mkdtemp()) / "dec.json"
    rec.write_bytes(json.dumps({"decision_type": "LOCKBOX_OPEN", "lockbox_open": True, "approved_by": ["synthetic"]}).encode())
    L.OPEN_DECISION_PATH, L.OPEN_DECISION_SHA256 = rec, hashlib.sha256(rec.read_bytes()).hexdigest()
    os.environ["LOCKBOX_OPEN_2025_26"] = "1"
from cbb_platform.stage3 import spec
from cbb_platform import props_v0, props_v0_participation
print(json.dumps({"model_seasons": list(spec.MODEL_SEASONS), "evaluation": list(spec.EVALUATION_SEASONS),
                  "folds_last_validation": spec.CORE_FOLDS[-1]["validation_season"], "subsystem": spec.SUBSYSTEM_WINDOWS}))
"""


def test_scoring_enable_patch_is_closed_unless_opened(tmp_path):
    assert hashlib.sha256(SCORING.read_bytes()).hexdigest() == SCORING_SHA256
    d = _copy(tmp_path)
    for p in (V3, SCORING):
        r = subprocess.run(["git", "apply", str(p)], cwd=d, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    run = lambda mode: json.loads(subprocess.run([sys.executable, "-c", PROBE, mode], cwd=d, capture_output=True, text=True,
                                                 check=True).stdout.strip().splitlines()[-1])
    closed, opened = run("closed"), run("open")
    assert closed["model_seasons"] == list(range(2014, 2026)) and closed["evaluation"] == []
    assert opened["model_seasons"] == list(range(2014, 2027)) and opened["evaluation"] == [2026]
    assert closed["folds_last_validation"] == opened["folds_last_validation"] == 2025       # folds never include 2025-26
    assert closed["subsystem"] == opened["subsystem"]
    text = SCORING.read_text()
    assert text.count("top > lockbox.LOCKBOX_SEASON or (top == lockbox.LOCKBOX_SEASON and not lockbox.opening_authorized())") == 2
    if PRE:
        assert subprocess.run(["git", "apply", "--check", str(SCORING)], cwd=C.PROJECT_ROOT).returncode == 0   # not applied live
    else:                                                     # not applied at readiness (applied live since the opening)
        assert S.apply_check(S.READINESS_COMMIT, [str(SCORING)])
