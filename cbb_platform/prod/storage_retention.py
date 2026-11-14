"""CBB storage retention: every bounded-growth rule beyond the run-scratch rule of prod/retention.py.

    python -m cbb_platform.prod.retention ...            runs prod/retention.py THEN these rules (same CLI, same lock)

Docs: docs/operations/CBB_STORAGE_AND_RETENTION.md. Runs under the CBB writer lock (cbb-originator-run prune-scratch),
so no run, forecast, tick or export is being written while it works. Each pass writes a dry-run report
(ops/retention/reports/<ts>-storage[-dryrun].json) BEFORE any change and appends one receipt per changed path to
ops/retention/storage_retention.jsonl.

Rules (BASE = the CBB state root, the parent of CBB_PROD_ROOT):

    INCOMPLETE_RUN_SCRATCH  prod/runs/<run>/features/*.duckdb of a run with no valid run_summary.json whose newest file is
                            older than 24 h (a failed / killed run; nothing reads its scratch again)
    VALIDATION_RUN_SCRATCH  prod/odds/validation_runs/<V>/<run>/features/*.duckdb older than 48 h (the validation record,
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
    RUN_PMF_DUPLICATE       prod/runs/<run>/market_pmfs.parquet of a completed run older than 7 d when EVERY PMF in it
                            equals the durable evidence (ops/ticks/*/pmfs.parquet, ops/forecasts/*/*/pmfs.parquet) of
                            the same run_id, outcome by outcome (the durable copy is canonical; exports read it first)
    LEDGER_MIRROR_LINK      prod/ops/ledgers/<x>.jsonl (the evidence-tree mirror written by ops_store.mirror_out) is
                            replaced by a hard link to the canonical working ledger when it is a byte prefix of it:
                            one physical copy; mirror_in / mirror_out become no-ops (both writers only append)

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

from cbb_platform.prod import config as PC, ops_store as OS, pmf_evidence as PE

ET = ZoneInfo("America/New_York")
INCOMPLETE_HOURS = 24
VALIDATION_HOURS = 48
SPILL_HOURS = 1
STAGING_DAYS = 14
STAGING_TMP_DAYS = 2
QA_DAYS = 14
EXPORT_TEMP_DAYS = 1
FORECAST_LEDGER_MIN_AGE_DAYS = 2
RAW_DAYS = 14
ROTOWIRE_RAW_DAYS = 14
RUN_PMF_DAYS = 7
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
        scratch = sorted(d.glob("features/*.duckdb"))
        if ok or not scratch or _newest(d) >= now - timedelta(hours=INCOMPLETE_HOURS):
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
            for d in sorted(qa.iterdir()) if qa.exists()
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


def _durable_frames(files: list[str]) -> pd.DataFrame:
    cols = ["run_id", "game_id", "player_id", "stat", "outcome", "probability"]
    return pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True) if files else \
        pd.DataFrame(columns=cols)


def plan_run_pmfs(P: Paths, now: datetime, durable: dict | None = None) -> list[dict]:
    from cbb_platform.prod import retention as RT
    durable = RT.durable_runs(P.ops) if durable is None else durable
    out = []
    for d in sorted(p for p in P.runs.iterdir() if p.is_dir()) if P.runs.exists() else []:
        mp = d / "market_pmfs.parquet"
        if not mp.exists() or d.name not in durable:
            continue
        try:
            s = json.loads((d / "run_summary.json").read_text())
        except (OSError, ValueError):
            continue
        if s.get("run_id") != d.name or _utc(s.get("prediction_as_of") or _mtime(mp)) >= now - timedelta(days=RUN_PMF_DAYS):
            continue
        ok, why = pmfs_duplicated(mp, d.name, durable[d.name])
        if ok:
            out.append(_item("RUN_PMF_DUPLICATE", mp, "DELETE", why, canonical=durable[d.name]))
    return out


def pmfs_duplicated(market_pmfs: Path, run_id: str, durable_files: list[str]) -> tuple[bool, str]:
    """True only when every PMF of the run file equals (outcome by outcome) the durable evidence of the same run."""
    mp = pd.read_parquet(market_pmfs, columns=["game_id", "player_id", "market", "pmf"])
    ev = _durable_frames(durable_files)
    ev = ev[ev["run_id"].astype(str) == run_id]
    if not len(mp):
        return False, "empty run PMF file: kept"
    have = {k: g.sort_values("outcome")["probability"].to_numpy(dtype=float)
            for k, g in ev.groupby(["game_id", "player_id", "stat"], sort=False)}
    for r in mp.itertuples(index=False):
        a = have.get((r.game_id, r.player_id, r.market))
        b = np.asarray(r.pmf, dtype=float)
        if a is None or len(a) != len(b) or not np.array_equal(a, b):
            return False, f"{r.game_id} {r.player_id} {r.market} differs from / is missing in durable evidence: kept"
    return True, f"all {len(mp)} PMFs equal the durable evidence of {run_id}"


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
            plan_forecast_ledgers, plan_raw_cache, plan_rotowire_raw, plan_run_pmfs, plan_ledger_links)
ALLOWED = {"INCOMPLETE_RUN_SCRATCH": "prod/runs/*/features/*.duckdb",
           "VALIDATION_RUN_SCRATCH": "prod/odds/validation_runs/*/*/features/*.duckdb",
           "DUCKDB_SPILL": "*/pid-*",
           "STAGING": "staging/*",
           "QA": "qa/*",
           "EXPORT_TEMP": "exports/*/*",
           "FORECAST_LEDGER_GZIP": "prod/ops/forecasts/*/*/forecast_ledger.jsonl",
           "RAW_CACHE_SUPERSEDED": "prod/cache/raw/*/*/*",
           "ROTOWIRE_RAW_GZIP": "prod/availability/rotowire_web/raw/*/*",
           "RUN_PMF_DUPLICATE": "prod/runs/*/market_pmfs.parquet",
           "LEDGER_MIRROR_LINK": "prod/ops/ledgers/*.jsonl"}


def plan(paths: Paths, now=None) -> dict:
    now = _utc(now) if now is not None else _now()
    items = []
    for fn in PLANNERS:
        items += fn(paths, now)
    by = {}
    for i in items:
        b = by.setdefault(i["rule"], {"items": 0, "bytes": 0})
        b["items"] += 1
        b["bytes"] += i["bytes"]
    return {"at": now.isoformat(), "base": str(paths.base), "items": items, "by_rule": by,
            "bytes_affected": sum(i["bytes"] for i in items)}


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
            elif i["action"] == "HARDLINK":
                saved = t.stat().st_size
                _hardlink(t, Path(i["canonical"]))
            else:
                raise RuntimeError(f"unknown action {i['action']}")
            out["reclaimed_bytes"] += saved
            out["changed"] += 1
            f.write(json.dumps({"at": p["at"], "rule": i["rule"], "action": i["action"], "path": i["path"],
                                "bytes": i["bytes"], "reclaimed": saved, "report": str(report),
                                **({"sha256": i["sha256"]} if i.get("sha256") else {})}, default=str) + "\n")
        f.write(json.dumps({"at": p["at"], "summary": True, "changed": out["changed"],
                            "reclaimed_bytes": out["reclaimed_bytes"], "report": str(report)}) + "\n")
    return out


def default_paths() -> Paths:
    prod = Path(PC.PROD_ROOT)
    spill = os.environ.get("CBB_DUCKDB_TEMP_DIR")
    return Paths(prod.parent, prod, Path(spill) if spill else None)
