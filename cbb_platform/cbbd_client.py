"""Stage 2 CBBD historical client: resumable, idempotent, checkpointed, rate-limit and quota aware.

Every call still goes through the ONE guarded fetch path (source_fetch -> lockbox.check_request /
check_response). This module adds what a historical backfill needs on top of it:

  * checkpoint / resume  -- a request (endpoint + exact params) that already has a successfully stored
                            raw payload is NEVER re-fetched (`request_key`). Restarting a run is free.
  * retries              -- HTTP 429 (honouring Retry-After), 5xx and network timeouts are retried with
                            bounded exponential backoff. 4xx other than 429 is permanent.
  * quota                -- CBBD reports `X-CallLimit-Remaining`. The client refuses to start a call
                            when the last observed remaining quota is below `QUOTA_RESERVE`
                            (QuotaReserveReached), so a backfill can never exhaust the account.
  * pacing               -- a global minimum interval between request starts, shared by all workers;
                            a 429 pauses every worker. Concurrency is bounded by the caller.
  * request log          -- one JSON line per request outcome in data/manifests/cbbd_request_log.jsonl
                            (append-only, non-secret). `compile_manifest` folds it into
                            data/manifests/cbbd_backfill_manifest.json.

A FAILED request is recorded as failed -- never as "no data". Only a 2xx response is a measurement.
The credential is read with credentials.get_strict (BLOCKED_CREDENTIAL_FORMAT on control characters)
and travels only in the Authorization header: never in params, logs, payloads or metadata.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from cbb_platform import config as C, credentials, lockbox, raw_store, source_fetch
from cbb_platform.adapters import cbbd

PROVIDER = "cbbd"
REQUEST_LOG = C.MANIFESTS / "cbbd_request_log.jsonl"
MANIFEST = C.MANIFESTS / "cbbd_backfill_manifest.json"

QUOTA_RESERVE = 20_000          # never start a call when fewer than this many remain on the account
MAX_RETRIES = 5
BACKOFF_BASE = 2.0              # seconds; attempt n waits BACKOFF_BASE * 2**n (capped)
BACKOFF_CAP = 120.0
MIN_INTERVAL = 0.35             # seconds between request starts, across all workers
TIMEOUT = 300                   # big /plays/date payloads can take a while
COMPRESS_ENDPOINTS = {"plays/game", "plays/date", "plays/team", "substitutions/game", "substitutions/team",
                      "lineups/game", "lineups/team", "teams/roster", "recruiting/players", "recruiting/portal"}


class QuotaReserveReached(RuntimeError):
    pass


def request_key(endpoint: str, params: dict) -> str:
    return f"{endpoint}|{json.dumps(params, sort_keys=True, default=str)}"


def _row_count(body) -> int | None:
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict):
        return 1
    return None


class Client:
    def __init__(self, *, log_path: Path | None = None, raw_root: Path | None = None,
                 quota_reserve: int = QUOTA_RESERVE, min_interval: float = MIN_INTERVAL,
                 known_game_season=None, sleep=time.sleep):
        self.log_path = log_path or REQUEST_LOG
        self.raw_root = raw_root
        self.quota_reserve = quota_reserve
        self.min_interval = min_interval
        self.sleep = sleep
        self.resolver = known_game_season or cbbd._default_game_season
        self._lock = threading.Lock()
        self._next_start = 0.0
        self._pause_until = 0.0
        self.quota_remaining: int | None = None
        self.stats = {"calls": 0, "successful_calls": 0, "empty_successful": 0, "retries": 0, "http_429": 0,
                      "permanent_failures": 0, "skipped_already_stored": 0, "started_at": time.time()}
        self._done = self._load_done()

    # ------------------------------------------------------------------ checkpoint
    def _load_done(self) -> dict:
        """request_key -> log entry for every request whose payload is stored (resume state)."""
        done = {}
        if self.log_path.exists():
            for line in self.log_path.read_text().splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                if e.get("status") in ("STORED", "ALREADY_STORED"):
                    done[e["request_key"]] = e
        return done

    def is_done(self, endpoint: str, params: dict) -> bool:
        return request_key(endpoint, params) in self._done

    def done_entry(self, endpoint: str, params: dict) -> dict | None:
        return self._done.get(request_key(endpoint, params))

    def _log(self, entry: dict) -> None:
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
            if entry.get("status") in ("STORED", "ALREADY_STORED"):
                self._done[entry["request_key"]] = entry

    # ------------------------------------------------------------------ pacing
    def _wait_turn(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                start = max(self._next_start, self._pause_until)
                if now >= start:
                    if self.quota_remaining is not None and self.quota_remaining < self.quota_reserve:
                        raise QuotaReserveReached(f"CBBD quota remaining {self.quota_remaining} < reserve "
                                                  f"{self.quota_reserve}; stopping before the next call")
                    self._next_start = now + self.min_interval
                    return
            self.sleep(min(start - now, 5.0))

    def _pause_all(self, seconds: float) -> None:
        with self._lock:
            self._pause_until = max(self._pause_until, time.monotonic() + seconds)

    # ------------------------------------------------------------------ fetch
    def fetch(self, endpoint: str, params: dict, *, purpose: str, season: int | None = None,
              scope: str | None = None) -> dict:
        """Fetch one request (or return its stored checkpoint). Returns the log entry.

        status: STORED | ALREADY_STORED | FAILED_PERMANENT | FAILED_RETRIES_EXHAUSTED.
        LockboxViolation and QuotaReserveReached propagate (fail closed)."""
        params = dict(params)
        key = request_key(endpoint, params)
        if key in self._done:
            with self._lock:
                self.stats["skipped_already_stored"] += 1
            return self._done[key]
        lockbox.check_request(PROVIDER, endpoint, params, self.resolver)     # before reading the credential
        api_key = credentials.get_strict("CBBD_API_KEY")
        path = cbbd.ENDPOINTS[endpoint].format(**params)
        query = {k: v for k, v in params.items() if "{" + k + "}" not in cbbd.ENDPOINTS[endpoint]}
        entry = {"provider": PROVIDER, "endpoint": endpoint, "params": params, "request_key": key,
                 "purpose": purpose, "season": season, "scope": scope, "retries": 0}
        attempt = 0
        while True:
            self._wait_turn()
            entry["requested_at"] = datetime.now(timezone.utc).isoformat()
            t0 = time.monotonic()
            with self._lock:
                self.stats["calls"] += 1
            try:
                content, body, meta = source_fetch.get_with_meta(
                    PROVIDER, endpoint, cbbd.BASE + path, query, {"Authorization": f"Bearer {api_key}"},
                    known_game_season=self.resolver, guard_params=params, timeout=TIMEOUT)
            except lockbox.LockboxViolation:
                raise
            except source_fetch.SourceHTTPError as e:
                entry.update(http_status=e.status, elapsed_s=round(time.monotonic() - t0, 2))
                retryable = e.status == 429 or e.status >= 500
                if e.status == 429:
                    with self._lock:
                        self.stats["http_429"] += 1
                    wait = e.retry_after if e.retry_after is not None else min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempt + 2))
                    self._pause_all(wait)
                if not retryable:
                    entry.update(status="FAILED_PERMANENT", error=str(e))
                    with self._lock:
                        self.stats["permanent_failures"] += 1
                    self._log(entry)
                    return entry
            except (source_fetch.SourceNetworkError, json.JSONDecodeError) as e:
                entry.update(http_status=None, error=type(e).__name__ if isinstance(e, json.JSONDecodeError) else str(e),
                             elapsed_s=round(time.monotonic() - t0, 2))
            else:
                remaining = meta.get("X-CallLimit-Remaining")
                if remaining is not None:
                    with self._lock:
                        self.quota_remaining = int(remaining)
                rec = raw_store.store(PROVIDER, endpoint, content, params=params, root=self.raw_root,
                                      compress=endpoint in COMPRESS_ENDPOINTS,
                                      extra_meta={"purpose": purpose, "season": season, "scope": scope})
                n = _row_count(body)
                entry.update(status="STORED" if rec.created else "ALREADY_STORED", http_status=200,
                             payload_sha256=rec.sha256, stored_file=str(rec.path.relative_to(rec.path.parents[2])),
                             size_bytes=len(content), row_count=n, empty=(n == 0),
                             quota_remaining=self.quota_remaining, elapsed_s=round(time.monotonic() - t0, 2),
                             canonicalization_state="PENDING")
                entry.pop("error", None)
                with self._lock:
                    self.stats["successful_calls"] += 1
                    self.stats["empty_successful"] += int(n == 0)
                self._log(entry)
                return entry
            # retryable failure: every failed attempt is its own append-only audit line (exact 429/5xx counts)
            self._log({**entry, "status": "ATTEMPT_FAILED", "attempt": attempt})
            if attempt >= MAX_RETRIES:
                entry.update(status="FAILED_RETRIES_EXHAUSTED")
                with self._lock:
                    self.stats["permanent_failures"] += 1
                self._log(entry)
                return entry
            attempt += 1
            entry["retries"] = attempt
            with self._lock:
                self.stats["retries"] += 1
            self.sleep(min(BACKOFF_CAP, BACKOFF_BASE * 2 ** attempt))

    def run_many(self, jobs: list[tuple[str, dict, dict]], workers: int = 1, progress_every: int = 50,
                 label: str = "") -> list[dict]:
        """jobs = [(endpoint, params, {purpose, season, scope})]. Bounded concurrency; stops on quota reserve."""
        from concurrent.futures import ThreadPoolExecutor
        out: list[dict | None] = [None] * len(jobs)
        todo = [i for i, (ep, p, _) in enumerate(jobs) if not self.is_done(ep, p)]
        for i, (ep, p, _) in enumerate(jobs):
            if out[i] is None and self.is_done(ep, p):
                out[i] = self.done_entry(ep, p)
                self.stats["skipped_already_stored"] += 1
        print(f"[{label}] {len(jobs)} requests, {len(jobs) - len(todo)} already stored, {len(todo)} to fetch, "
              f"workers={workers}", flush=True)
        stop = threading.Event()

        def one(i):
            if stop.is_set():
                return
            ep, p, kw = jobs[i]
            try:
                out[i] = self.fetch(ep, p, **kw)
            except QuotaReserveReached:
                stop.set()
                raise
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in [ex.submit(one, i) for i in todo]:
                fut.result()
                done += 1
                if done % progress_every == 0:
                    print(f"[{label}] {done}/{len(todo)} quota_remaining={self.quota_remaining} "
                          f"stats={ {k: v for k, v in self.stats.items() if k != 'started_at'} }", flush=True)
        return out


# ---------------------------------------------------------------------- manifest
def compile_manifest(log_path: Path | None = None, out: Path | None = None) -> dict:
    """Fold the append-only request log into the per-request manifest (latest outcome per request)."""
    log_path = log_path or REQUEST_LOG
    out = out or MANIFEST
    latest: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    for line in log_path.read_text().splitlines() if log_path.exists() else []:
        if not line.strip():
            continue
        e = json.loads(line)
        k = e["request_key"]
        attempts[k] = attempts.get(k, 0) + 1
        prev = latest.get(k)
        # a stored success is final; otherwise the latest outcome wins
        if prev is None or prev.get("status") not in ("STORED", "ALREADY_STORED"):
            latest[k] = e
    requests_ = sorted(latest.values(), key=lambda e: e["request_key"])
    by = {}
    for e in requests_:
        b = by.setdefault(e["endpoint"], {"requests": 0, "stored": 0, "empty": 0, "failed": 0, "rows": 0, "bytes": 0})
        b["requests"] += 1
        if e.get("status") in ("STORED", "ALREADY_STORED"):
            b["stored"] += 1
            b["empty"] += int(bool(e.get("empty")))
            b["rows"] += e.get("row_count") or 0
            b["bytes"] += e.get("size_bytes") or 0
        else:
            b["failed"] += 1
    manifest = {"provider": PROVIDER, "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": "Non-secret request metadata only. No Authorization header or API key is ever recorded. "
                        "A failed request is NOT evidence of absent source data.",
                "summary_by_endpoint": by, "log_lines": sum(attempts.values()),
                "requests": [{**e, "log_lines_for_request": attempts[e["request_key"]]} for e in requests_]}
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest
