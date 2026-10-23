"""Off-machine persistence for the prospective shadow operation: the ONLY module that shells out, and only to

    git      commit / push of the evidence tree to the PRIVATE ops-state branch (never a source branch, never main)
    gh       upload / download / delete of assets on the PRIVATE release `cbb-2026-27-shadow-ops`
    openssl  AES-256 encryption of every asset before it leaves the machine (key from the environment only)

It lives outside cbb_platform/prod on purpose: the production layer keeps its guarantee of no shell, socket or
deploy code (tests/test_prod_shadow.py). Nothing here publishes, deploys, opens SSH or touches a public host.

Assets: the working set (state + minimal provider cache; see prod/ops_store.py) and the compact frozen history extract
(prod/history_extract.py; Starter Pack- and CBBD-derived, research-only data -> stored encrypted, private, never public).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from cbb_platform.prod import config as PC, ops_store as OS

ALLOWED_TOOLS = ("git", "gh", "openssl")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    if cmd[0] not in ALLOWED_TOOLS:
        raise OS.OpsStoreError(f"tool {cmd[0]!r} not allowed")
    return subprocess.run(cmd, capture_output=True, text=kw.pop("text", True), **kw)


# ============================================================================ encryption
def _key_env(key_env: str) -> dict:
    if not os.environ.get(key_env):
        raise OS.OpsStoreError(f"BLOCKED_CREDENTIAL: {key_env} is not set")
    return dict(os.environ)


def encrypt(src: Path, dst: Path, key_env: str = PC.OPS_KEY_ENV) -> None:
    r = _run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-in", str(src), "-out", str(dst),
              "-pass", f"env:{key_env}"], env=_key_env(key_env))
    if r.returncode:
        raise OS.OpsStoreError("encryption failed")


def decrypt(src: Path, dst: Path, key_env: str = PC.OPS_KEY_ENV) -> None:
    r = _run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-in", str(src), "-out", str(dst),
              "-pass", f"env:{key_env}"], env=_key_env(key_env))
    if r.returncode:
        raise OS.OpsStoreError("decryption failed (wrong key or corrupt archive)")


class _Encrypted:
    """Wraps a raw store: put() encrypts first, get() decrypts after; names carry an .enc suffix."""
    description = "AES-256-CBC (openssl, pbkdf2 200000); key " + PC.OPS_KEY_ENV

    def __init__(self, raw, key_env: str = PC.OPS_KEY_ENV, scratch: Path | None = None):
        self.raw, self.key_env = raw, key_env
        self.scratch = Path(scratch or (PC.PROD_ROOT / "ops_scratch"))

    def put(self, local: Path, name: str) -> None:
        self.scratch.mkdir(parents=True, exist_ok=True)
        enc = self.scratch / f"{name}.enc"
        encrypt(local, enc, self.key_env)
        try:
            self.raw.put(enc, f"{name}.enc")
        finally:
            enc.unlink(missing_ok=True)

    def get(self, name: str, dest: Path) -> Path:
        self.scratch.mkdir(parents=True, exist_ok=True)
        enc = self.scratch / f"{name}.{uuid.uuid4().hex}.enc"
        try:
            self.raw.get(f"{name}.enc", enc)
            decrypt(enc, dest, self.key_env)
        finally:
            enc.unlink(missing_ok=True)
        return Path(dest)

    def delete(self, name: str) -> None:
        self.raw.delete(f"{name}.enc")

    def names(self) -> list[str]:
        return [n[:-4] for n in self.raw.names() if n.endswith(".enc")]


class GhRelease:
    """Raw asset store: one release of the PRIVATE repository, through gh (GH_TOKEN or gh auth)."""

    def __init__(self, repo: str, tag: str = PC.OPS_RELEASE_TAG):
        self.repo, self.tag = repo, tag

    def _gh(self, *args, check=True) -> subprocess.CompletedProcess:
        r = _run(["gh", *args, "-R", self.repo])
        if check and r.returncode:
            raise OS.OpsStoreError(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
        return r

    def exists(self) -> bool:
        return self._gh("release", "view", self.tag, check=False).returncode == 0

    def put(self, local: Path, name: str) -> None:
        staged = Path(local)
        if staged.name != name:
            staged = Path(local).with_name(name)
            shutil.copyfile(local, staged)
        try:
            self._gh("release", "upload", self.tag, str(staged), "--clobber")
        finally:
            if staged != Path(local):
                staged.unlink(missing_ok=True)

    def get(self, name: str, dest: Path) -> Path:
        self._gh("release", "download", self.tag, "-p", name, "-O", str(dest), "--clobber")
        return Path(dest)

    def delete(self, name: str) -> None:
        self._gh("release", "delete-asset", self.tag, name, "-y", check=False)

    def names(self) -> list[str]:
        import json
        r = self._gh("release", "view", self.tag, "--json", "assets")
        return sorted(a["name"] for a in json.loads(r.stdout)["assets"])


def release_assets(repo: str, tag: str = PC.OPS_RELEASE_TAG, key_env: str = PC.OPS_KEY_ENV):
    rel = GhRelease(repo, tag)
    if not rel.exists():
        raise OS.OpsStoreError(f"private release {tag} missing on {repo}")
    return _Encrypted(rel, key_env)


def encrypted_dir_assets(root: Path, key_env: str = PC.OPS_KEY_ENV):
    """Encrypted directory store (local mirror / tests of the encryption path)."""
    return _Encrypted(OS.DirAssets(root), key_env, scratch=Path(root) / ".scratch")


# ============================================================================ history extract asset
def history_asset_name(manifest: dict) -> str:
    return f"history-extract-{manifest['extract_sha256'][:16]}.duckdb"


def upload_history_extract(assets, extract: Path, manifest: dict) -> str:
    if OS.sha256_file(extract) != manifest["extract_sha256"]:
        raise OS.OpsStoreError("local extract differs from the tracked manifest")
    name = history_asset_name(manifest)
    assets.put(Path(extract), name)
    return name


def fetch_history_extract(assets, dest: Path, manifest: dict) -> Path:
    dest = Path(dest)
    if dest.exists() and OS.sha256_file(dest) == manifest["extract_sha256"]:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}")
    assets.get(history_asset_name(manifest), tmp)
    if OS.sha256_file(tmp) != manifest["extract_sha256"]:
        tmp.unlink(missing_ok=True)
        raise OS.OpsStoreError("history extract sha256 differs from the tracked manifest")
    os.replace(tmp, dest)
    return dest


# ============================================================================ git (evidence tree = ops-branch checkout)
def git_commit_push(ops_root: Path, message: str, push: bool = True, branch: str = PC.OPS_BRANCH) -> dict:
    """Commit every change of the evidence tree; no change -> no commit (an idle tick leaves no trace). Push only
    to the ops-state branch."""
    ops_root = Path(ops_root)
    if not (ops_root / ".git").exists():
        return {"committed": False, "reason": "NOT_A_GIT_CHECKOUT"}

    def g(*a, check=True):
        r = _run(["git", "-C", str(ops_root), *a])
        if check and r.returncode:
            raise OS.OpsStoreError(f"git {a[0]} failed: {r.stderr.strip()[:300]}")
        return r
    g("add", "-A", "--sparse")              # the runner checkout is sparse (bulky ledgers are not checked out)
    if not g("status", "--porcelain").stdout.strip():
        return {"committed": False, "reason": "NO_CHANGE"}
    g("commit", "-q", "-m", message)
    sha = g("rev-parse", "HEAD").stdout.strip()
    if push:
        g("push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    return {"committed": True, "sha": sha, "pushed": push, "branch": branch}
