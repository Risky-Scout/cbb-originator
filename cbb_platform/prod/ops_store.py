"""Durable prospective-shadow persistence: survives a Mac shutdown, a fresh GitHub-hosted runner and a process restart.

Two layers, nothing on /tmp or a runner's disk is ever the only copy:

1. EVIDENCE TREE (text + small Parquet; the private branch `ops/cbb-2026-27-shadow-state`, never merged into source).
   Compact, append-only evidence. Each tick that did work writes one immutable folder ticks/<tick_id>/; CHAIN.jsonl
   indexes them with a hash chain (entry hash = sha256(prev | tick_id | sorted file sha256s)), so any edit, deletion,
   reordering or insertion of a tick folder is detectable (verify_chain). Layout:
       OPERATION.json  DUE.json  WORKING_SET.json  CHAIN.jsonl
       markers/        QUOTE_INTEGRATION_VALIDATED.json (immutable), quote_validation_attempts.jsonl,
                       FIRST_LIVE_COMPLETED_GAME_INGESTION.json (immutable)
       ledgers/        cbbd_production_requests.jsonl, odds_production_requests.jsonl, sync_log.jsonl,
                       state_update_failures.jsonl
       state_manifests/<state_version>.json
       registry/       games.jsonl (one decision per game and cutoff), quote_snapshots.jsonl, settlements.jsonl
       ticks/<tick_id>/  tick.json, shadow_ledger.jsonl.gz, side_monitor.jsonl.gz, quotes_normalized.csv.gz,
                       projections.parquet, predictions_quoted.parquet, quote_sides.parquet, settlements.jsonl.gz,
                       side_settlements.parquet, actuals.parquet
       reports/        cumulative.json/.md, daily/<ET date>.json/.md
2. WORKING SET (binary; a tar stored through an asset backend: on GitHub, AES-256 encrypted on the private release
   `cbb-2026-27-shadow-ops`). The last-known-good
   state version (increment.duckdb + manifest + CURRENT.json) and the minimal provider cache the TTL / snapshot logic
   reads. WORKING_SET.json names the current archive and the previous ones with their sha256; restore() falls back to
   the previous archive when the current one is missing or corrupt, and refuses (raises) rather than guess.

No credential, API key, model store, simulation cube or training dataset is ever written to either layer. This module
has no network or shell code: the private-release backend (AES-256 encryption, gh) and the git commit / push live in
cbb_platform/ops_persistence.py, outside the production layer.
"""
from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
import shutil
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cbb_platform.prod import config as PC

GENESIS = "0" * 64
KEEP_ARCHIVES = 4
# provider cache kept in the working set (latest payload per request key; completed-game payloads only while recent)
CACHE_RECENT_HOURS = {"games/teams": 72, "games/players": 72, "stats/player/season": 72, "substitutions/game": 72,
                      "event_odds": 24}
EVENTS_RECENT_HOURS = 72
COLLECTIONS_RECENT_HOURS = 48
# operational ledgers mirrored between the evidence tree and the paths the production modules write
def ledger_mirror(prod_root: Path = PC.PROD_ROOT) -> dict:
    prod_root = Path(prod_root)
    return {"ledgers/cbbd_production_requests.jsonl": prod_root / "cache" / "cbbd_production_requests.jsonl",
            "ledgers/odds_production_requests.jsonl": prod_root / "cache" / "odds_production_requests.jsonl",
            "ledgers/sync_log.jsonl": prod_root / "sync" / "sync_log.jsonl",
            "ledgers/state_update_failures.jsonl": prod_root / "state" / "update_failures.jsonl"}


class OpsStoreError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(x) -> datetime:
    import pandas as pd
    t = pd.Timestamp(x)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================================ evidence tree helpers
def read_json(p: Path, default=None):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else default


def write_json(p: Path, obj) -> bool:
    """Atomic write; returns False (and writes nothing) when the content is unchanged (no spurious commit)."""
    p = Path(p)
    text = json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n"
    if p.exists() and p.read_text() == text:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}")
    tmp.write_text(text)
    os.replace(tmp, p)
    return True


def write_once(p: Path, obj) -> bool:
    """Immutable marker: written once; an existing marker is never overwritten."""
    p = Path(p)
    if p.exists():
        return False
    return write_json(p, obj)


def read_jsonl(p: Path) -> list[dict]:
    p = Path(p)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


_CRITICAL = {"depth": 0, "pending": None}


@contextlib.contextmanager
def alarm_deferred():
    """A critical section for complete-line appends: a budget handler that fires inside it (the availability Phase A
    SIGALRM, prod/lifecycle.availability_acquire) defers its exception to the section's end via defer_if_critical(), so
    a budget expiring mid-append fires right after the append, never inside it (no torn jsonl line). Python runs
    signal handlers in the main thread between bytecodes, so this holds whichever OS thread received the signal."""
    _CRITICAL["depth"] += 1
    try:
        yield
    finally:
        _CRITICAL["depth"] -= 1
        if _CRITICAL["depth"] == 0 and _CRITICAL["pending"] is not None:
            fn, _CRITICAL["pending"] = _CRITICAL["pending"], None
            fn()


def defer_if_critical(fn) -> bool:
    """Called by a signal handler: inside alarm_deferred() -> run fn at the section's end (True); else False."""
    if _CRITICAL["depth"] > 0:
        _CRITICAL["pending"] = fn
        return True
    return False


@contextlib.contextmanager
def file_lock(lock_path: Path, shared: bool = False):
    """A narrow advisory flock(2) on `lock_path` (LOCK_SH for readers, LOCK_EX for writers). Never the CBB global
    writer lock. Released by the kernel when the holder exits or dies. A reader that cannot create the lock file
    (read-only tree) reads unlocked, exactly as before."""
    lock_path = Path(lock_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a")
    except OSError:
        if not shared:
            raise
        yield
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        fh.close()


def lock_path_for(p: Path) -> Path:
    p = Path(p)
    return p.with_name(p.name + ".lock")


def append_jsonl(p: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with alarm_deferred(), open(p, "a") as f:
        f.writelines(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
        f.flush()
        os.fsync(f.fileno())
    return len(rows)


def append_jsonl_locked(p: Path, rows: list[dict]) -> int:
    """append_jsonl under the file's own narrow lock (<p>.lock, LOCK_EX): for a ledger written by processes that no
    longer share the global writer lock (the availability Phase A acquisition)."""
    if not rows:
        return 0
    with file_lock(lock_path_for(p)):
        return append_jsonl(p, rows)


def read_jsonl_locked(p: Path) -> list[dict]:
    """read_jsonl under <p>.lock LOCK_SH: never observes a half-appended batch."""
    p = Path(p)
    if not p.exists():
        return []
    with file_lock(lock_path_for(p), shared=True):
        return read_jsonl(p)


def create_once(p: Path, obj) -> bool:
    """Atomic write-once: the complete file appears under its final name exactly once (link(2) of a fully written,
    fsynced temp file; EEXIST -> False, the existing file is never replaced). Readers never see a partial file."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, p)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def gzip_file(src: Path, dst: Path) -> Path:
    """Deterministic gzip (mtime 0), so identical content gives identical bytes."""
    data = Path(src).read_bytes()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(data)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(buf.getvalue())
    return Path(dst)


def read_gz_lines(p: Path) -> list[dict]:
    with gzip.open(p, "rt") as f:
        return [json.loads(l) for l in f if l.strip()]


# ============================================================================ tick chain
def chain_append(ops_root: Path, tick_id: str) -> dict:
    """Seal ticks/<tick_id>/ into CHAIN.jsonl (after every file of the tick is written)."""
    ops_root = Path(ops_root)
    d = ops_root / "ticks" / tick_id
    if not d.is_dir():
        raise OpsStoreError(f"tick folder {tick_id} missing")
    rows = read_jsonl(ops_root / "CHAIN.jsonl")
    if any(r["tick_id"] == tick_id for r in rows):
        raise OpsStoreError(f"tick {tick_id} already sealed (immutable)")
    files = {p.name: sha256_file(p) for p in sorted(d.iterdir()) if p.is_file()}
    prev = rows[-1]["hash"] if rows else GENESIS
    h = sha256_bytes(canon({"prev": prev, "tick_id": tick_id, "files": files}).encode())
    row = {"seq": len(rows) + 1, "tick_id": tick_id, "files": files, "prev": prev, "hash": h}
    append_jsonl(ops_root / "CHAIN.jsonl", [row])
    return row


def verify_chain(ops_root: Path, check_files: bool = True) -> dict:
    """Recompute the tick chain; optionally re-hash every present tick file (a sparse checkout may omit some)."""
    ops_root = Path(ops_root)
    prev, n, missing = GENESIS, 0, 0
    for r in read_jsonl(ops_root / "CHAIN.jsonl"):
        n += 1
        if r["prev"] != prev or r["seq"] != n:
            raise OpsStoreError(f"chain broken at seq {n}")
        if sha256_bytes(canon({"prev": prev, "tick_id": r["tick_id"], "files": r["files"]}).encode()) != r["hash"]:
            raise OpsStoreError(f"chain hash mismatch at seq {n} ({r['tick_id']})")
        if check_files:
            for name, sha in r["files"].items():
                p = ops_root / "ticks" / r["tick_id"] / name
                if not p.exists():
                    missing += 1
                elif sha256_file(p) != sha:
                    raise OpsStoreError(f"tick {r['tick_id']} file {name} changed after sealing")
        prev = r["hash"]
    return {"ticks": n, "head": prev, "files_not_checked_out": missing, "CHAIN_VALID": True}


# ============================================================================ ledger mirror (evidence tree <-> working paths)
def mirror_in(ops_root: Path, prod_root: Path = PC.PROD_ROOT) -> None:
    """Evidence-tree ledgers -> the paths the production modules append to. Only an evidence copy that EXTENDS the
    local one is copied in (a fresh runner); a longer local ledger is kept; diverged ledgers raise (never truncate)."""
    for rel, dst in ledger_mirror(prod_root).items():
        src = Path(ops_root) / rel
        if not src.exists():
            continue
        new, old = src.read_bytes(), (Path(dst).read_bytes() if Path(dst).exists() else b"")
        if new.startswith(old) and new != old:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(new)
        elif not old.startswith(new):
            raise OpsStoreError(f"{rel}: evidence and working ledgers diverged")


def mirror_out(ops_root: Path, prod_root: Path = PC.PROD_ROOT) -> list[str]:
    """Working ledgers -> evidence tree. Append-only check: the evidence copy must be a prefix of the working copy."""
    changed = []
    for rel, src in ledger_mirror(prod_root).items():
        src, dst = Path(src), Path(ops_root) / rel
        if not src.exists():
            continue
        new = src.read_bytes()
        old = dst.read_bytes() if dst.exists() else b""
        if not new.startswith(old):
            raise OpsStoreError(f"{rel}: working ledger is not an append-only extension of the evidence copy")
        if new != old:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(new)
            changed.append(rel)
    return changed


# ============================================================================ working set (state + minimal cache)
def _recent(meta: dict, hours: float, now: datetime) -> bool:
    return _utc(meta["received_at"]) >= now - timedelta(hours=hours)


def _cache_selection(cache_raw: Path, now: datetime) -> list[Path]:
    """Latest payload per request key (+ its meta); completed-game / event-odds payloads only while recent; events
    listings of the last EVENTS_RECENT_HOURS (OF.listing / event_commence_resolver read the newest ones)."""
    keep = []
    for prov in ("cbbd", "the-odds-api"):
        root = Path(cache_raw) / prov
        if not root.exists():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            latest = {}
            for mp in folder.glob("*.meta.json"):
                m = json.loads(mp.read_text())
                ep = m["endpoint"]
                if ep == "events":
                    if _recent(m, EVENTS_RECENT_HOURS, now):
                        keep += [mp, mp.parent / m["file"]]
                    continue
                if ep in CACHE_RECENT_HOURS and not _recent(m, CACHE_RECENT_HOURS[ep], now):
                    continue
                k = canon({"e": ep, "p": m.get("params")})
                if k not in latest or (m["received_at"], m["file"]) > (latest[k][0]["received_at"], latest[k][0]["file"]):
                    latest[k] = (m, mp)
            for m, mp in latest.values():
                keep += [mp, mp.parent / m["file"]]
    return sorted(set(keep))


def pack_working_set(out_tar: Path, prod_root: Path = PC.PROD_ROOT, now=None) -> dict:
    """Deterministic tar of the operational working set (relative to prod_root)."""
    now = _utc(now) if now is not None else _now()
    prod_root = Path(prod_root)
    st = prod_root / "state"
    ptr = read_json(st / "CURRENT.json")
    files = []
    if ptr is not None:
        files.append(st / "CURRENT.json")
        vd = st / "versions" / ptr["state_version"]
        files += sorted(p for p in vd.iterdir() if p.is_file())
    files += _cache_selection(prod_root / "cache" / "raw", now)
    col = prod_root / "odds" / "collections"
    if col.exists():
        for p in sorted(col.glob("*.json")):
            try:
                rx = _utc(json.loads(p.read_text())["collector_received_at"])
            except (KeyError, ValueError):
                continue
            if rx >= now - timedelta(hours=COLLECTIONS_RECENT_HOURS):
                files.append(p)
    rec = prod_root / "odds" / "integration_validation.json"
    if rec.exists():
        files.append(rec)
    out_tar = Path(out_tar)
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar, "w", format=tarfile.PAX_FORMAT) as t:
        for p in files:
            info = t.gettarinfo(str(p), arcname=str(p.relative_to(prod_root)))
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            info.mode = 0o644
            with open(p, "rb") as f:
                t.addfile(info, f)
    return {"files": len(files), "bytes": out_tar.stat().st_size, "sha256": sha256_file(out_tar),
            "state_version": ptr["state_version"] if ptr else None}


def unpack_working_set(tar_path: Path, prod_root: Path = PC.PROD_ROOT) -> dict:
    prod_root = Path(prod_root)
    n = 0
    with tarfile.open(tar_path, "r") as t:
        for m in t.getmembers():
            if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                raise OpsStoreError(f"unsafe working-set member {m.name!r}")
            dst = prod_root / m.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            with t.extractfile(m) as src:
                data = src.read()
            if dst.exists() and dst.read_bytes() == data:
                continue
            tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex}")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
            n += 1
    return {"restored_files": n}


# ============================================================================ asset backends
# A backend stores PLAINTEXT files by name: put(local, name), get(name, dest), delete(name), names(). DirAssets (below)
# is the local / test backend. The private-release backend, which encrypts every asset with AES-256 before upload and
# decrypts after download, lives outside the production layer (cbb_platform/ops_persistence.py: the only code that
# shells out to git / gh / openssl), so this package keeps its no-subprocess guarantee.
class DirAssets:
    """Asset store on a directory (tests; a local mirror)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, local: Path, name: str) -> None:
        tmp = self.root / f".{name}.{uuid.uuid4().hex}"
        shutil.copyfile(local, tmp)
        os.replace(tmp, self.root / name)

    def get(self, name: str, dest: Path) -> Path:
        src = self.root / name
        if not src.exists():
            raise OpsStoreError(f"asset {name} not found")
        shutil.copyfile(src, dest)
        return Path(dest)

    def delete(self, name: str) -> None:
        (self.root / name).unlink(missing_ok=True)

    def names(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_file() and not p.name.startswith("."))


# ============================================================================ persist / restore
def persist_working_set(ops_root: Path, assets, tick_id: str, prod_root: Path = PC.PROD_ROOT, scratch: Path | None = None,
                        now=None) -> dict:
    """Pack -> upload a NEW archive (the backend encrypts) -> only then point WORKING_SET.json at it (the pointer is
    the commit point). Unchanged content uploads nothing. Archives beyond KEEP_ARCHIVES are deleted after the move."""
    ops_root = Path(ops_root)
    scratch = Path(scratch or (Path(prod_root) / "ops_scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    tar = scratch / f"ws-{tick_id}.tar"
    info = pack_working_set(tar, prod_root, now)
    ptr = read_json(ops_root / "WORKING_SET.json", {"current": None, "previous": []})
    if ptr.get("current") and ptr["current"]["tar_sha256"] == info["sha256"]:
        tar.unlink(missing_ok=True)
        return {"uploaded": False, "current": ptr["current"]}
    name = f"ws-{tick_id}.tar"
    assets.put(tar, name)
    cur = {"asset": name, "tar_sha256": info["sha256"], "files": info["files"], "tar_bytes": info["bytes"],
           "state_version": info["state_version"], "tick_id": tick_id,
           "created_at": (_utc(now) if now is not None else _now()).isoformat()}
    prev = ([ptr["current"]] if ptr.get("current") else []) + list(ptr.get("previous", []))
    keep, drop = prev[:KEEP_ARCHIVES - 1], prev[KEEP_ARCHIVES - 1:]
    write_json(ops_root / "WORKING_SET.json", {"current": cur, "previous": keep,
                                              "storage": getattr(assets, "description", type(assets).__name__)})
    for d in drop:
        assets.delete(d["asset"])
    tar.unlink(missing_ok=True)
    return {"uploaded": True, "current": cur, "deleted": [d["asset"] for d in drop]}


def restore_working_set(ops_root: Path, assets, prod_root: Path = PC.PROD_ROOT, scratch: Path | None = None) -> dict:
    """Download (the backend decrypts) -> sha256 check -> unpack, newest archive first, falling back to the previous
    ones; raises when none verifies (never guesses)."""
    ptr = read_json(Path(ops_root) / "WORKING_SET.json")
    if not ptr or not ptr.get("current"):
        return {"restored": False, "reason": "NO_WORKING_SET_YET"}
    scratch = Path(scratch or (Path(prod_root) / "ops_scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    errors = []
    for cand in [ptr["current"]] + list(ptr.get("previous", [])):
        tar = scratch / (cand["asset"] + ".restore")
        try:
            assets.get(cand["asset"], tar)
            if sha256_file(tar) != cand["tar_sha256"]:
                raise OpsStoreError("archive sha256 mismatch")
            out = unpack_working_set(tar, prod_root)
            return {"restored": True, "asset": cand["asset"], "state_version": cand["state_version"],
                    "fallback_used": cand is not ptr["current"], "errors": errors, **out}
        except (OpsStoreError, tarfile.TarError, OSError) as e:
            errors.append({"asset": cand["asset"], "error": str(e)[:300]})
        finally:
            tar.unlink(missing_ok=True)
    raise OpsStoreError(f"no restorable working set: {errors}")
