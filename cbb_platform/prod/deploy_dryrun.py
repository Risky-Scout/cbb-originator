"""LOCAL dry run of the proven NCAAF Wizard activation pattern (ncaaf-originator-2026 scripts/deploy_wizard.sh,
wizard_remote_activate.sh, rollback_wizard.sh), executed ONLY against a local sandbox directory standing in for the
remote root. This module contains no network, SSH, SCP, RSYNC or FTP code and refuses any root outside the sandbox
areas. Nothing is published.

    remote_root/.player-props.incoming-<run_id>   upload target (verified: exact SHA256SUMS coverage, safe names, digests)
    remote_root/player-props                      LIVE
    remote_root/player-props.previous             kept for rollback
activate: rm previous; live -> previous; incoming -> live; re-verify live (failure restores previous).
rollback: verify previous; live -> swap; previous -> live; swap -> previous (running it twice swaps back).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from cbb_platform.prod import bundle as BD, config as PC

LIVE, PREVIOUS = "player-props", "player-props.previous"
ALLOWED_ROOTS = (PC.PROD_ROOT / "dryrun_remote", Path(tempfile.gettempdir()).resolve())


class DryRunRefused(RuntimeError):
    pass


def _guard(remote_root: Path) -> Path:
    r = Path(remote_root).resolve()
    if not any(r == a.resolve() or a.resolve() in r.parents for a in ALLOWED_ROOTS):
        raise DryRunRefused(f"{r} is not a local dry-run sandbox")
    return r


def stage_incoming(bundle_dir: Path, remote_root: Path, run_id: str) -> Path:
    r = _guard(remote_root)
    BD.verify(bundle_dir)                                   # local pre-upload verification
    inc = r / f".{LIVE}.incoming-{run_id}"
    if inc.exists():
        shutil.rmtree(inc)
    shutil.copytree(bundle_dir, inc)                         # stands in for scp -r
    return inc


def activate(remote_root: Path, run_id: str) -> dict:
    r = _guard(remote_root)
    live, prev, inc = r / LIVE, r / PREVIOUS, r / f".{LIVE}.incoming-{run_id}"
    try:
        BD.verify(inc)
    except BD.BundleChecksumFailure as e:
        shutil.rmtree(inc, ignore_errors=True)
        return {"activated": False, "reason": f"INCOMING_INVALID: {e}"}
    if prev.exists():
        shutil.rmtree(prev)
    if live.exists():
        live.rename(prev)
    try:
        inc.rename(live)
        BD.verify(live)
    except Exception as e:                                  # noqa: BLE001 -- restore previous on any failure
        if live.exists():
            shutil.rmtree(live)
        if prev.exists():
            prev.rename(live)
        return {"activated": False, "reason": f"ROLLBACK_RESTORED_PREVIOUS: {e}"}
    for stale in r.glob(f".{LIVE}.incoming-*"):
        shutil.rmtree(stale, ignore_errors=True)
    return {"activated": True, "live_sha256sums": BD._sha(live / "SHA256SUMS")}


def rollback(remote_root: Path) -> dict:
    r = _guard(remote_root)
    live, prev, swap = r / LIVE, r / PREVIOUS, r / f".{LIVE}.rollback-swap"
    if not prev.exists():
        return {"rolled_back": False, "reason": "ROLLBACK_FAIL: no previous release"}
    BD.verify(prev)
    if swap.exists():
        shutil.rmtree(swap)
    if live.exists():
        live.rename(swap)
    prev.rename(live)
    if swap.exists():
        swap.rename(prev)
    return {"rolled_back": True, "live_sha256sums": BD._sha(live / "SHA256SUMS")}


def remote_plan(run_id: str) -> list[str]:
    """What an approved deployment WOULD run (documentation only; never executed here)."""
    return [f"verify package locally (SHA256SUMS exact coverage, safe names, digests)",
            f"create <remote>/.{LIVE}.incoming-{run_id} ; copy bundle into it",
            f"remote: verify incoming; rm -rf {PREVIOUS}; mv {LIVE} {PREVIOUS}; mv .{LIVE}.incoming-{run_id} {LIVE}",
            f"remote: sha256sum -c in {LIVE}; on failure restore {PREVIOUS}",
            "public HTTPS read-back verification",
            f"rollback: verify {PREVIOUS}; swap {LIVE} <-> {PREVIOUS}"]


def dry_run(bundle_a: Path, bundle_b: Path, sandbox: Path) -> dict:
    """Two activations + rollback + roll-forward, all local. Returns a step log."""
    root = _guard(sandbox)
    root.mkdir(parents=True, exist_ok=True)
    log = []
    for rid, b in (("A", bundle_a), ("B", bundle_b)):
        stage_incoming(b, root, rid)
        log.append({"step": f"activate {rid}", **activate(root, rid)})
    live_b = BD._sha(root / LIVE / "SHA256SUMS")
    log.append({"step": "rollback", **rollback(root)})
    live_after_rb = BD._sha(root / LIVE / "SHA256SUMS")
    log.append({"step": "roll forward", **rollback(root)})
    ok = (all(s.get("activated", s.get("rolled_back")) for s in log)
          and live_after_rb == BD._sha(Path(bundle_a) / "SHA256SUMS")
          and BD._sha(root / LIVE / "SHA256SUMS") == live_b)
    return {"steps": log, "ROLLBACK_DRY_RUN": "PASS" if ok else "FAIL", "network_used": False, "remote_plan": remote_plan("B")}
