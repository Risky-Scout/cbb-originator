"""CBB storage retention: every bounded-growth rule beyond the run-scratch rule of prod/retention.py.

    python -m cbb_platform.prod.retention ...            runs prod/retention.py THEN these rules (same CLI, same lock)

Docs: docs/operations/CBB_STORAGE_AND_RETENTION.md. Runs under the CBB writer lock (cbb-originator-run prune-scratch),
so no run, forecast, tick or export is being written while it works. Each pass writes a dry-run report
(ops/retention/reports/<ts>-storage[-dryrun].json) BEFORE any change and appends one receipt per changed path to
ops/retention/storage_retention.jsonl.

Rules (BASE = the CBB state root, the parent of CBB_PROD_ROOT):

    ORPHAN_RUN_RECEIPT      prod/runs/<run>/run_failure.json (status ORPHANED) WRITTEN for a run with neither
                            run_summary.json nor run_failure.json whose files are untouched > 7 h (prod/run_receipts):
                            the only non-deleting rule; every run ends in a terminal record
    INCOMPLETE_RUN_SCRATCH  prod/runs/<run>/features/*.duckdb of a run with no valid run_summary.json whose newest file is
                            older than 18 h (a failed / killed run; nothing reads its scratch again). Applied AFTER the
                            ORPHAN_RUN_RECEIPT of the same pass, so the terminal receipt always exists first
    VALIDATION_RUN_SCRATCH  prod/odds/validation_runs/<V>/<run>/features/*.duckdb older than 42 h (the validation record,
                            ledger and side monitor stay)
    DUCKDB_SPILL            <CBB_DUCKDB_TEMP_DIR>/pid-<N>/ whose process is gone and which is older than 1 h
    STAGING                 BASE/staging/<entry> (not tmp/, not cleanup-quarantine-*) older than 14 d;
                            BASE/staging/tmp/<entry> (not the DuckDB spill root) older than 2 d
    QA                      BASE/qa/<dir> without a RETAIN.json marker, older than 14 d
    EXPORT_TEMP             BASE/exports/<D>/ temporary files (*.tmp, *.partial, ~$*, .~lock*, *.bak) older than 1 d
    FORECAST_LEDGER_GZIP    prod/ops/forecasts/<D>/<snapshot>/forecast_ledger.jsonl of an ET date at least 2 days old
                            -> forecast_ledger.jsonl.gz (byte-exact round trip verified before the original goes);
                            written once by the forecast, never read back by production
    RAW_CACHE_SUPERSEDED    prod/cache/raw/<provider>/<endpoint>/ payload + .meta.json older than 14 d (received_at) when
                            a NEWER payload of the exact same request (endpoint + params) exists. The newest payload of
                            every request is kept forever (the cache TTL / as-of lookups read it); the request ledgers
                            keep every payload's sha256
    ROTOWIRE_RAW_GZIP       prod/availability/rotowire_web/raw/<D>/ pages of a day older than 14 d -> gzip in place
                            (immutable availability evidence: compressed, never deleted)
    RUN_PMF_DUPLICATE       prod/runs/<run>/market_pmfs.parquet of a completed run (untouched > 1 h) whose EVERY PMF is
                            in one durable evidence file (ops/ticks/*/pmfs.parquet or ops/forecasts/*/*/pmfs.parquet)
                            bit for bit -> replaced by market_pmfs.ref.json pointing at that canonical file
                            (prod.pmf_evidence.dereference_run; the forecast / tick writers do this at write time, the
                            rule converts the backlog). One physical PMF payload per forecast state.
    RUN_JSON_GZIP           prod/runs/<run>/bundle/predictions.json and game_diagnostics.json of a completed run untouched
                            > 2 d -> .gz (byte-exact round trip verified). Write-once audit copies (the ledgers carry
                            the predictions); read only by the unscheduled walk-forward monitor, which reads .gz too
    PMF_LONG_TO_COMPACT     prod/ops/forecasts/<D>/<F>/pmfs.parquet still in the legacy LONG schema -> the compact canonical
                            form, replaced only when it reads back as the identical long frame; old / new sha256 in
                            the receipt. Tick PMF files are never rewritten (sha256 sealed in CHAIN.jsonl)
    LEDGER_MIRROR_LINK      prod/ops/ledgers/<x>.jsonl (the evidence-tree mirror written by ops_store.mirror_out) is
                            replaced by a hard link to the canonical working ledger when it is a byte prefix of it:
                            one physical copy; mirror_in / mirror_out become no-ops (both writers only append)

Cadence: the retention timer runs every 6 h (05:45 / 11:45 / 17:45 / 23:45 ET), so each age window above plus 6 h is
the guaranteed bound (run scratch 24 h, validation scratch 48 h).

Report only (no change): MODEL_AUDIT classifies every walk-forward model directory (ACTIVE / ROLLBACK / REFERENCED by a
retained forecast / UNREFERENCED / INCOMPLETE, duplicate payload files by sha256) and FOOTPRINT records the size of
every CBB area, the file count, free disk and every directory > 500 MiB with its retention reason (NO_REASON is flagged).

Never touched by any rule: the current state and every state version, the walk-forward store (models, ACTIVE,
rollback, manifests, training cache), ops ticks / CHAIN / registry / markers / reports / morning receipts, durable PMF
evidence, forecast snapshots (except the ledger compression above), final export workbooks and receipts, secrets,
history, evidence, locks. A path outside its rule's allowed pattern is refused (RuntimeError) before anything is changed.
"""
from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC, ops_store as OS, pmf_evidence as PE, run_receipts as RR

ET = ZoneInfo("America/New_York")
INCOMPLETE_HOURS = 18           # 18 h window + 6-hourly cadence: failed-run scratch gone within 24 h (receipt first)
VALIDATION_HOURS = 42           # 42 h window + 6-hourly cadence: validation scratch gone within 48 h
RUN_JSON_GZIP_DAYS = 2
RUN_JSON_FILES = ("bundle/predictions.json", "game_diagnostics.json")
LARGE_DIR_BYTES = 500 * 2**20
SPILL_HOURS = 1
STAGING_DAYS = 14
STAGING_TMP_DAYS = 2
QA_DAYS = 14
EXPORT_TEMP_DAYS = 1
FORECAST_LEDGER_MIN_AGE_DAYS = 2
RAW_DAYS = 14
ROTOWIRE_RAW_DAYS = 14
RUN_PMF_HOURS = 1                                  # the writer dereferences at once; this catches the backlog
EXPORT_TEMP_PATTERNS = ("*.tmp", "*.partial", "~$*", ".~lock*", "*.bak")
QA_RETAIN_MARKER = "RETAIN.json"
QUARANTINE_PREFIX = "cleanup-quarantine-"
# Never deleted or rewritten by any rule (relative to BASE). A target under one of these is refused.
PROTECTED = ("prod/state", "walkforward", "secrets", "history", "evidence", "locks", "ops", "prod/ops/ticks",
             "prod/ops/registry", "prod/ops/markers", "prod/ops/reports", "prod/ops/morning", "prod/ops/CHAIN.jsonl",
             "prod/ops/state_manifests", "prod/ops/retention")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(x) -> datetime:
    t = pd.Timestamp(x)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def _mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)


def _newest(p: Path) -> datetime:
    """Newest mtime of a file, or of a directory and everything below it."""
    p = Path(p)
    best = p.stat().st_mtime
    if p.is_dir():
        for root, dirs, files in os.walk(p):
            for n in dirs + files:
                try:
                    best = max(best, os.lstat(os.path.join(root, n)).st_mtime)
                except OSError:
                    pass
    return datetime.fromtimestamp(best, timezone.utc)


def _run_activity(d: Path) -> datetime:
    """Newest mtime of the files a run itself wrote (a retention receipt is not run activity)."""
    best = None
    for root, _, files in os.walk(d):
        for n in files:
            if n == RR.FAILURE_FILE or n.startswith(f".{RR.FAILURE_FILE}"):
                continue
            try:
                m = os.lstat(os.path.join(root, n)).st_mtime
            except OSError:
                continue
            best = m if best is None else max(best, m)
    return datetime.fromtimestamp(best if best is not None else d.stat().st_mtime, timezone.utc)


def _size(p: Path) -> int:
    p = Path(p)
    if p.is_file() or p.is_symlink():
        return p.lstat().st_size
    return sum(os.lstat(os.path.join(r, f)).st_size for r, _, fs in os.walk(p) for f in fs)


def _sha(p: Path) -> str:
    return OS.sha256_file(p)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                          # exists, owned by another user
        return True
    return True


class Paths:
    def __init__(self, base: Path, prod_root: Path | None = None, spill_root: Path | None = None):
        self.base = Path(base)
        self.prod = Path(prod_root) if prod_root else self.base / "prod"
        self.ops = self.prod / "ops"
        self.runs = self.prod / "runs"
        self.spill = Path(spill_root) if spill_root else self.base / "staging" / "tmp" / "duckdb"


def _guard(paths: Paths, target: Path, allowed: str) -> Path:
    """target must resolve inside BASE, match the rule's glob (relative to BASE) and lie outside every PROTECTED tree."""
    base = paths.base.resolve()
    t = Path(target).resolve() if not Path(target).is_symlink() else Path(os.path.abspath(target))
    try:
        rel = t.relative_to(base).as_posix()
    except ValueError:
        raise RuntimeError(f"refusing: {t} is outside the CBB state root {base}") from None
    for p in PROTECTED:
        if rel == p or rel.startswith(p + "/"):
            raise RuntimeError(f"refusing: {rel} is protected ({p})")
    if not fnmatch.fnmatchcase(rel, allowed):
        raise RuntimeError(f"refusing: {rel} does not match the rule pattern {allowed}")
    return t


# ============================================================================ planning (read only)
def _item(rule: str, path: Path, action: str, reason: str, **kw) -> dict:
    return {"rule": rule, "path": str(path), "action": action, "bytes": _size(path), "reason": reason, **kw}


def plan_incomplete_runs(P: Paths, now: datetime) -> list[dict]:
    out = []
    for d in sorted(p for p in P.runs.iterdir() if p.is_dir()) if P.runs.exists() else []:
        try:
            ok = json.loads((d / "run_summary.json").read_text()).get("run_id") == d.name
        except (OSError, ValueError):
            ok = False
        if ok:
            continue
        newest = _run_activity(d)
        if not (d / RR.FAILURE_FILE).exists() and newest < now - timedelta(hours=RR.ORPHAN_HOURS):
            out.append(_item("ORPHAN_RUN_RECEIPT", d / RR.FAILURE_FILE, "WRITE_RECEIPT",
                             f"run {d.name} has neither run_summary.json nor run_failure.json and is untouched > "
                             f"{RR.ORPHAN_HOURS} h (longer than any CBB unit timeout): process ended without a record "
                             f"(SIGKILL / OOM / host stop)", newest_mtime=newest.isoformat()))
        scratch = sorted(d.glob("features/*.duckdb"))
        if not scratch or newest >= now - timedelta(hours=INCOMPLETE_HOURS):
            continue
        for f in scratch:
            out.append(_item("INCOMPLETE_RUN_SCRATCH", f, "DELETE",
                             f"run {d.name} never completed (no valid run_summary.json); untouched > {INCOMPLETE_HOURS} h"))
    return out


def plan_validation_runs(P: Paths, now: datetime) -> list[dict]:
    root = P.prod / "odds" / "validation_runs"
    return [_item("VALIDATION_RUN_SCRATCH", f, "DELETE", f"quote-validation run feature scratch older than {VALIDATION_HOURS} h")
            for f in sorted(root.glob("*/*/features/*.duckdb")) if _mtime(f) < now - timedelta(hours=VALIDATION_HOURS)]


def plan_spill(P: Paths, now: datetime) -> list[dict]:
    out = []
    for d in sorted(P.spill.glob("pid-*")) if P.spill.exists() else []:
        try:
            pid = int(d.name.split("-", 1)[1])
        except ValueError:
            continue
        if not _pid_alive(pid) and _newest(d) < now - timedelta(hours=SPILL_HOURS):
            out.append(_item("DUCKDB_SPILL", d, "DELETE_TREE", f"DuckDB spill of exited process {pid}"))
    return out


def plan_staging(P: Paths, now: datetime) -> list[dict]:
    out, st = [], P.base / "staging"
    tmp = st / "tmp"
    for e in sorted(st.iterdir()) if st.exists() else []:
        if e.name == "tmp" or e.name.startswith(QUARANTINE_PREFIX):
            continue
        if _newest(e) < now - timedelta(days=STAGING_DAYS):
            out.append(_item("STAGING", e, "DELETE_TREE" if e.is_dir() else "DELETE",
                             f"staging scratch untouched > {STAGING_DAYS} d"))
    for e in sorted(tmp.iterdir()) if tmp.exists() else []:
        if e.resolve() == P.spill.resolve():
            continue
        if _newest(e) < now - timedelta(days=STAGING_TMP_DAYS):
            out.append(_item("STAGING", e, "DELETE_TREE" if e.is_dir() else "DELETE",
                             f"staging/tmp entry untouched > {STAGING_TMP_DAYS} d"))
    return out


def plan_qa(P: Paths, now: datetime) -> list[dict]:
    qa = P.base / "qa"
    return [_item("QA", d, "DELETE_TREE", f"QA tree without {QA_RETAIN_MARKER}, untouched > {QA_DAYS} d")
            for d in (sorted(qa.iterdir()) if qa.exists() else [])
            if d.is_dir() and not (d / QA_RETAIN_MARKER).exists() and _newest(d) < now - timedelta(days=QA_DAYS)]


def plan_export_temp(P: Paths, now: datetime) -> list[dict]:
    out = []
    for f in sorted((P.base / "exports").glob("*/*")) if (P.base / "exports").exists() else []:
        if f.is_file() and any(fnmatch.fnmatchcase(f.name, p) for p in EXPORT_TEMP_PATTERNS) \
                and _mtime(f) < now - timedelta(days=EXPORT_TEMP_DAYS):
            out.append(_item("EXPORT_TEMP", f, "DELETE", "temporary export file"))
    return out


def plan_forecast_ledgers(P: Paths, now: datetime) -> list[dict]:
    last = now.astimezone(ET).date() - timedelta(days=FORECAST_LEDGER_MIN_AGE_DAYS)
    out = []
    for f in sorted((P.ops / "forecasts").glob("*/*/forecast_ledger.jsonl")):
        try:
            day = datetime.strptime(f.parent.parent.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day <= last:
            out.append(_item("FORECAST_LEDGER_GZIP", f, "GZIP", f"forecast ledger of {day} (closed): compressed"))
    return out


def _params_key(m: dict) -> str:
    return json.dumps([m.get("endpoint"), m.get("params")], sort_keys=True, default=str)


def plan_raw_cache(P: Paths, now: datetime) -> list[dict]:
    out, cut = [], now - timedelta(days=RAW_DAYS)
    raw = P.prod / "cache" / "raw"
    for folder in sorted(raw.glob("*/*")) if raw.exists() else []:
        groups: dict = {}
        for mp in folder.glob("*.meta.json"):
            try:
                m = json.loads(mp.read_text())
                groups.setdefault(_params_key(m), []).append((str(m["received_at"]), m["file"], mp, m))
            except (OSError, ValueError, KeyError):
                continue                                   # unreadable meta: never deleted
        for rows in groups.values():
            rows.sort(key=lambda r: (r[0], r[1]))
            for rec, fname, mp, m in rows[:-1]:            # the newest payload of each request is always kept
                if _utc(rec) >= cut:
                    continue
                payload = mp.parent / fname
                for f in (payload, mp):
                    if f.exists():
                        out.append(_item("RAW_CACHE_SUPERSEDED", f, "DELETE",
                                         f"superseded raw payload received {rec} (> {RAW_DAYS} d; a newer payload of "
                                         f"the same request exists)", sha256=m.get("sha256")))
    return out


def plan_rotowire_raw(P: Paths, now: datetime) -> list[dict]:
    root = P.prod / "availability" / "rotowire_web" / "raw"
    last = now.astimezone(ET).date() - timedelta(days=ROTOWIRE_RAW_DAYS)
    out = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        try:
            day = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < last:
            out += [_item("ROTOWIRE_RAW_GZIP", f, "GZIP", f"RotoWire raw page of {day}: compressed, never deleted")
                    for f in sorted(d.iterdir()) if f.is_file() and not f.name.endswith(".gz")]
    return out


def plan_run_pmfs(P: Paths, now: datetime, durable: dict | None = None) -> list[dict]:
    from cbb_platform.prod import retention as RT
    durable = RT.durable_runs(P.ops) if durable is None else durable
    out = []
    for d in sorted(p for p in P.runs.iterdir() if p.is_dir()) if P.runs.exists() else []:
        mp = d / PE.RUN_FILE
        if not mp.exists() or d.name not in durable:
            continue
        try:
            s = json.loads((d / "run_summary.json").read_text())
        except (OSError, ValueError):
            continue
        if s.get("run_id") != d.name or _mtime(mp) >= now - timedelta(hours=RUN_PMF_HOURS):
            continue
        for f in durable[d.name]:
            ok, why = PE.run_copy_verified(d, d.name, Path(f))
            if ok:
                out.append(_item("RUN_PMF_DUPLICATE", mp, "DEREFERENCE", why, canonical=str(f), run_id=d.name))
                break
    return out


# PMF_LONG_TO_COMPACT holds the long frame twice plus the compact bytes while it verifies (QA 2026-11-16: 1,383,541 long
# rows -> > 1.5 GB). The retention unit runs at MemoryHigh=500M / MemoryMax=800M, so it compacts only files up to this
# many long rows (~1.9 kB/row at the verification peak); a larger legacy file is KEPT (unchanged, still read by
# pmf_evidence.read) and reported for a one-off migration in a unit with the heavy-job memory limits.
PMF_COMPACT_MAX_LONG_ROWS = 100_000


def plan_pmf_compact(P: Paths, now: datetime) -> list[dict]:
    """Legacy LONG forecast-snapshot pmfs.parquet (written before the compact format) older than 1 h. Tick PMF files are
    never rewritten: their sha256 is sealed in CHAIN.jsonl / tick.json (prod/ops/ticks is protected)."""
    out = []
    for f in sorted((P.ops / "forecasts").glob(f"*/*/{PE.FILE}")):
        if _mtime(f) >= now - timedelta(hours=1):
            continue
        try:
            import pyarrow.parquet as pq
            if "probabilities" in pq.read_schema(f).names:
                continue
            rows = pq.ParquetFile(f).metadata.num_rows
        except Exception:                                  # noqa: BLE001 -- unreadable: never touched
            continue
        if rows > PMF_COMPACT_MAX_LONG_ROWS:
            out.append(_item("PMF_LONG_TO_COMPACT", f, "KEEP", f"legacy long-format file of {rows} rows exceeds the "
                             f"retention unit's memory budget ({PMF_COMPACT_MAX_LONG_ROWS} rows): kept unchanged; "
                             "one-off migration with the heavy-job memory limits", long_rows=rows))
            continue
        out.append(_item("PMF_LONG_TO_COMPACT", f, "COMPACT", "legacy long-format forecast PMF file -> compact "
                         "canonical form (replaced only when it reads back as the identical long frame)", sha256=_sha(f)))
    return out


def _compact_verified(f: Path) -> int:
    """f (long) -> compact, replaced only when PE.read(compact) equals the original long frame exactly. Returns saved."""
    old = PE.read(f)
    tmp = f.with_name(f.name + ".compact.tmp")
    tmp.write_bytes(PE._bytes(old))
    try:
        back = PE.read(tmp)
        if not (back.equals(old) and list(back.dtypes) == list(old.dtypes)):
            raise RuntimeError(f"{f}: compact round trip differs: refused")
        PE.read_compact(tmp)                                 # every vector re-hashed
    except Exception:
        tmp.unlink()
        raise
    saved = f.stat().st_size - tmp.stat().st_size
    side = f.with_name(f.name + PE.MIGRATION_SUFFIX)          # the audit link: snapshot.json records the OLD sha256
    side.write_text(json.dumps({"rule": "PMF_LONG_TO_COMPACT", "at": _now().isoformat(), "old_sha256": _sha(f),
                                "new_sha256": _sha(tmp), "old_bytes": f.stat().st_size, "new_bytes": tmp.stat().st_size,
                                "verification": "pmf_evidence.read(new) equals the original long frame exactly "
                                                "(values, dtypes, order); every vector re-hashed"}, indent=1) + "\n")
    shutil.copystat(f, tmp)
    os.replace(tmp, f)
    return saved


def plan_run_json(P: Paths, now: datetime) -> list[dict]:
    out = []
    for d in sorted(p for p in P.runs.iterdir() if p.is_dir()) if P.runs.exists() else []:
        try:
            ok = json.loads((d / "run_summary.json").read_text()).get("run_id") == d.name
        except (OSError, ValueError):
            ok = False
        if not ok:
            continue
        for rel in RUN_JSON_FILES:
            f = d / rel
            if f.is_file() and _mtime(f) < now - timedelta(days=RUN_JSON_GZIP_DAYS):
                out.append(_item("RUN_JSON_GZIP", f, "GZIP", f"write-once run audit copy older than {RUN_JSON_GZIP_DAYS} d"))
    return out


def plan_ledger_links(P: Paths, now: datetime) -> list[dict]:
    out = []
    for rel, src in OS.ledger_mirror(P.prod).items():
        dst = P.ops / rel
        if not (Path(src).exists() and dst.exists()):
            continue
        a, b = Path(src).stat(), dst.stat()
        if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) or a.st_dev != b.st_dev:
            continue
        with open(src, "rb") as fs, open(dst, "rb") as fd:
            prefix = fs.read(b.st_size) == fd.read() if a.st_size >= b.st_size else False
        if prefix:
            out.append(_item("LEDGER_MIRROR_LINK", dst, "HARDLINK", "evidence mirror is a prefix of the canonical ledger",
                             canonical=str(src)))
    return out


PLANNERS = (plan_incomplete_runs, plan_validation_runs, plan_spill, plan_staging, plan_qa, plan_export_temp,
            plan_forecast_ledgers, plan_raw_cache, plan_rotowire_raw, plan_run_pmfs, plan_run_json, plan_pmf_compact, plan_ledger_links)
ALLOWED = {"ORPHAN_RUN_RECEIPT": "prod/runs/*/run_failure.json",
           "INCOMPLETE_RUN_SCRATCH": "prod/runs/*/features/*.duckdb",
           "VALIDATION_RUN_SCRATCH": "prod/odds/validation_runs/*/*/features/*.duckdb",
           "DUCKDB_SPILL": "*/pid-*",
           "STAGING": "staging/*",
           "QA": "qa/*",
           "EXPORT_TEMP": "exports/*/*",
           "FORECAST_LEDGER_GZIP": "prod/ops/forecasts/*/*/forecast_ledger.jsonl",
           "RAW_CACHE_SUPERSEDED": "prod/cache/raw/*/*/*",
           "ROTOWIRE_RAW_GZIP": "prod/availability/rotowire_web/raw/*/*",
           "RUN_PMF_DUPLICATE": "prod/runs/*/market_pmfs.parquet",
           "RUN_JSON_GZIP": "prod/runs/*.json",
           "PMF_LONG_TO_COMPACT": "prod/ops/forecasts/*/*/pmfs.parquet",
           "LEDGER_MIRROR_LINK": "prod/ops/ledgers/*.jsonl"}


# ============================================================================ report-only audits
def model_audit(P: Paths) -> dict:
    """Every walk-forward model directory, classified (nothing is changed: the model store is protected)."""
    W = P.base / "walkforward" / "cbb-originator"
    if not W.exists():
        return {"models": [], "candidates": [], "duplicate_payload_files": []}
    def _j(p):
        try:
            return json.loads(Path(p).read_text())
        except (OSError, ValueError):
            return {}
    active = _j(W / "active" / "ACTIVE_MODEL.json").get("ACTIVE_MODEL_VERSION")
    rollback = _j(W / "rollback" / "ROLLBACK_MODEL.json").get("ROLLBACK_MODEL_VERSION")
    referenced = set()
    for r in OS.read_jsonl(P.ops / "forecasts" / "index.jsonl") if (P.ops / "forecasts" / "index.jsonl").exists() else []:
        if r.get("model_version"):
            referenced.add(r["model_version"])
    models, shas = [], {}
    for d in sorted(p for p in (W / "models").iterdir() if p.is_dir()) if (W / "models").exists() else []:
        vm = _j(d / "version_manifest.json")
        v = vm.get("model_version") or vm.get("MODEL_VERSION") or f"cbb-jm1-2026-27-{d.name.replace('-', '')}"
        complete = (d / "MANIFEST.sha256").exists()
        cls = ("ACTIVE" if v == active else "ROLLBACK" if v == rollback else "INCOMPLETE" if not complete
               else "REFERENCED_BY_RETAINED_FORECAST" if v in referenced else "UNREFERENCED")
        for f in d.iterdir():
            if f.is_file():
                shas.setdefault(_sha(f), []).append(str(f.relative_to(W)))
        models.append({"dir": d.name, "model_version": v, "class": cls, "bytes": _size(d)})
    return {"active": active, "rollback": rollback, "models": models,
            "candidates": sorted(p.name for p in (W / "candidates").iterdir()) if (W / "candidates").exists() else [],
            "duplicate_payload_files": sorted(v for v in shas.values() if len(v) > 1)}


def run_census(P: Paths) -> dict:
    """Run directories by terminal state (SUCCESS / FAILED / ABORTED / DEFERRED / IN_PROGRESS)."""
    out: dict = {}
    for d in sorted(p for p in P.runs.iterdir() if p.is_dir()) if P.runs.exists() else []:
        st = RR.terminal_state(d)
        out.setdefault(st, []).append(d.name)
    return {"counts": {k: len(v) for k, v in out.items()}, "in_progress": out.get("IN_PROGRESS", [])}


AREAS = ("prod/runs", "prod/state", "prod/ops", "prod/cache", "prod/availability", "walkforward", "history", "exports",
         "evidence", "qa", "staging")
REASONS = (("prod/runs", "CURRENT_PRODUCTION"), ("prod/state", "REQUIRED_HISTORY"), ("prod/ops", "REQUIRED_AUDIT"),
           ("prod/cache", "CURRENT_PRODUCTION"), ("prod/availability", "REQUIRED_AUDIT"),
           ("walkforward", "REQUIRED_ROLLBACK"), ("history", "REQUIRED_HISTORY"), ("exports", "REQUIRED_AUDIT"),
           ("evidence", "REQUIRED_AUDIT"), ("prod", "CURRENT_PRODUCTION"))


def footprint(P: Paths) -> dict:
    """Size of every CBB area, file count, free disk, and every directory > 500 MiB with its retention reason.
    qa/<x> is REQUIRED_AUDIT only with a RETAIN.json marker; staging and anything unlisted is NO_REASON."""
    sizes: dict = {}
    files = 0
    for root, _, fs in os.walk(P.base):
        n = 0
        for f in fs:
            try:
                n += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
        files += len(fs)
        rel = Path(root).relative_to(P.base)
        for k in range(len(rel.parts) + 1):
            key = Path(*rel.parts[:k]).as_posix() if k else "."
            sizes[key] = sizes.get(key, 0) + n

    def reason(rel: str) -> str:
        if rel.startswith("qa/") or rel == "qa":
            q = P.base / "/".join(rel.split("/")[:2])
            return "REQUIRED_AUDIT" if rel != "qa" and (q / QA_RETAIN_MARKER).exists() else "NO_REASON"
        return next((r for p, r in REASONS if rel == p or rel.startswith(p + "/")), "NO_REASON")
    large = [{"dir": k, "bytes": v, "reason": reason(k)} for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])
             if k != "." and v > LARGE_DIR_BYTES and len(Path(k).parts) <= 3]
    du = shutil.disk_usage(P.base)
    return {"total_bytes": sizes.get(".", 0), "files": files, "areas": {a: sizes.get(a, 0) for a in AREAS},
            "disk_free_bytes": du.free, "disk_used_fraction": round(du.used / du.total, 4), "large_dirs": large,
            "large_dirs_without_reason": [x["dir"] for x in large if x["reason"] == "NO_REASON"]}


def plan(paths: Paths, now=None) -> dict:
    now = _utc(now) if now is not None else _now()
    items = []
    for fn in PLANNERS:
        items += fn(paths, now)
    kept = [i for i in items if i["action"] == "KEEP"]          # reported, never acted on
    items = [i for i in items if i["action"] != "KEEP"]
    by = {}
    for i in items:
        b = by.setdefault(i["rule"], {"items": 0, "bytes": 0})
        b["items"] += 1
        b["bytes"] += i["bytes"]
    return {"at": now.isoformat(), "base": str(paths.base), "items": items, "kept": kept, "by_rule": by,
            "bytes_affected": sum(i["bytes"] for i in items), "model_audit": model_audit(paths),
            "run_terminal_states": run_census(paths),
            "footprint_before": footprint(paths)}


# ============================================================================ apply
def _gzip_verified(f: Path) -> int:
    """f -> f.gz, byte-exact round trip checked, then f removed. Returns bytes saved."""
    gz = f.with_name(f.name + ".gz")
    want = _sha(f)
    if gz.exists():
        if hashlib.sha256(gzip.decompress(gz.read_bytes())).hexdigest() != want:
            raise RuntimeError(f"{gz} exists with different content: refused")
    else:
        tmp = gz.with_name(gz.name + ".tmp")
        with open(f, "rb") as src, gzip.open(tmp, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        if hashlib.sha256(gzip.decompress(tmp.read_bytes())).hexdigest() != want:
            tmp.unlink()
            raise RuntimeError(f"{f}: gzip round trip mismatch: refused")
        shutil.copystat(f, tmp)
        os.replace(tmp, gz)
    saved = f.stat().st_size - gz.stat().st_size
    f.unlink()
    return saved


def _hardlink(dst: Path, src: Path) -> int:
    tmp = dst.with_name(f".{dst.name}.link")
    if tmp.exists():
        tmp.unlink()
    os.link(src, tmp)
    os.replace(tmp, dst)
    return 0


def run(paths: Paths, apply: bool = False, now=None) -> dict:
    p = plan(paths, now)
    rdir = paths.ops / "retention" / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    report = rdir / f"{pd.Timestamp(p['at']).strftime('%Y%m%dT%H%M%SZ')}-storage{'' if apply else '-dryrun'}.json"
    tmp = report.with_name(report.name + ".tmp")
    tmp.write_text(json.dumps({**p, "mode": "APPLY" if apply else "DRY_RUN"}, indent=1, default=str) + "\n")
    os.replace(tmp, report)                                   # the report exists BEFORE anything changes
    out = {"at": p["at"], "mode": "APPLY" if apply else "DRY_RUN", "report": str(report), "by_rule": p["by_rule"],
           "bytes_affected": p["bytes_affected"], "reclaimed_bytes": 0, "changed": 0}
    if not apply:
        return out
    for i in p["items"]:                                      # validate EVERY target before the first change
        _guard(paths, Path(i["path"]), ALLOWED[i["rule"]] if i["rule"] != "DUCKDB_SPILL" else "**")
        if i["rule"] == "DUCKDB_SPILL" and Path(i["path"]).resolve().parent != paths.spill.resolve():
            raise RuntimeError(f"refusing: {i['path']} is not a spill directory of {paths.spill}")
    log = paths.ops / "retention" / "storage_retention.jsonl"
    with open(log, "a") as f:
        for i in p["items"]:
            t = Path(i["path"])
            if i["action"] == "WRITE_RECEIPT":
                if t.exists():
                    continue
                RR.write_failure(t.parent, "ORPHANED", i["reason"], detected_by="storage_retention",
                                 newest_mtime=i.get("newest_mtime"))
                out["changed"] += 1
                f.write(json.dumps({"at": p["at"], "rule": i["rule"], "action": i["action"], "path": i["path"],
                                    "bytes": 0, "reclaimed": 0, "report": str(report)}) + "\n")
                continue
            if not t.exists():
                continue
            if i["action"] == "DELETE":
                saved = t.stat().st_size
                t.unlink()
            elif i["action"] == "DELETE_TREE":
                saved = _size(t)
                shutil.rmtree(t) if t.is_dir() else t.unlink()
            elif i["action"] == "GZIP":
                saved = _gzip_verified(t)
            elif i["action"] == "COMPACT":
                if _sha(t) != i["sha256"]:
                    continue                                  # changed since planning: left for the next pass
                saved = _compact_verified(t)
                i["new_sha256"] = _sha(t)
            elif i["action"] == "DEREFERENCE":
                r = PE.dereference_run(t.parent, i["run_id"], Path(i["canonical"]))
                if r["status"] != "REFERENCED":
                    continue
                saved = r["replaced_bytes"] - (t.parent / PE.RUN_REF).stat().st_size
            elif i["action"] == "HARDLINK":
                saved = t.stat().st_size
                _hardlink(t, Path(i["canonical"]))
            else:
                raise RuntimeError(f"unknown action {i['action']}")
            out["reclaimed_bytes"] += saved
            out["changed"] += 1
            f.write(json.dumps({"at": p["at"], "rule": i["rule"], "action": i["action"], "path": i["path"],
                                "bytes": i["bytes"], "reclaimed": saved, "report": str(report),
                                **({"sha256": i["sha256"]} if i.get("sha256") else {}),
                                **({"new_sha256": i["new_sha256"]} if i.get("new_sha256") else {})}, default=str) + "\n")
        f.write(json.dumps({"at": p["at"], "summary": True, "changed": out["changed"],
                            "reclaimed_bytes": out["reclaimed_bytes"], "report": str(report)}) + "\n")
    after = footprint(paths)
    out["footprint_after"] = {k: after[k] for k in ("total_bytes", "files", "disk_free_bytes", "large_dirs_without_reason")}
    tmp = report.with_name(report.name + ".tmp")
    tmp.write_text(json.dumps({**p, "mode": "APPLY", "result": out, "footprint_after": after}, indent=1, default=str) + "\n")
    os.replace(tmp, report)
    return out


def default_paths() -> Paths:
    prod = Path(PC.PROD_ROOT)
    spill = os.environ.get("CBB_DUCKDB_TEMP_DIR")
    return Paths(prod.parent, prod, Path(spill) if spill else None)
