"""PRODUCTION-ONLY RotoWire CBB availability acquisition (2026-27). Never research, never historical, never a scraper.

Three documented REST endpoints, nothing else:

    snapshot       GET https://api.rotowire.com/Basketball/CBB/Injuries.php         current injury list (reconciliation)
    news_injuries  GET https://api.rotowire.com/Basketball/CBB/News/Injuries.php    timestamped injury updates (primary)
    news_updates   GET https://api.rotowire.com/Basketball/CBB/News/Updates.php     optional (PC.ROTOWIRE_INCLUDE_UPDATES)

The only query parameter is the API key (API_KEY_PARAM). No date / hours / priority / format parameter is sent: none
is confirmed for CBB, so none is assumed (a request carrying any other parameter is refused before it is made).

The key comes from ROTOWIRE_API_KEY (credentials.get_strict) and travels only in the request query: it never reaches
params, the ledger, raw-cache metadata, events or exceptions (network exceptions are reduced to their type name,
because their text can contain the request URL). Payloads are cached immutably under data/prod/cache/raw/rotowire
(gitignored, licensed data: never published). Every acquisition is one JSON line in
data/prod/cache/rotowire_production_requests.jsonl; `collected_at` is the instant this process received the response.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cbb_platform import credentials, raw_store
from cbb_platform.prod import config as PC, ops_store as OS

PROVIDER = "rotowire"
BASE = "https://api.rotowire.com"
PATHS = {"snapshot": "/Basketball/CBB/Injuries.php", "news_injuries": "/Basketball/CBB/News/Injuries.php",
         "news_updates": "/Basketball/CBB/News/Updates.php"}
KEY_ENV = "ROTOWIRE_API_KEY"
API_KEY_PARAM = "key"          # "API key via query parameter"; the name is to be confirmed by the first live smoke test
MAX_RETRIES = 2
BACKOFF = 2.0
TIMEOUT = 30


class RotoWireGuardViolation(RuntimeError):
    pass


class RotoWireBudgetExceeded(RuntimeError):
    pass


class RotoWireProviderFailure(RuntimeError):
    def __init__(self, msg: str, entry: dict | None = None):
        super().__init__(msg)
        self.entry = entry or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def check_request(endpoint: str, params: dict) -> None:
    if endpoint not in PATHS:
        raise RotoWireGuardViolation(f"ROTOWIRE_GUARD: endpoint {endpoint!r} is not on the production allowlist")
    if endpoint == "news_updates" and not PC.ROTOWIRE_INCLUDE_UPDATES:
        raise RotoWireGuardViolation("ROTOWIRE_GUARD: News/Updates.php is disabled (PC.ROTOWIRE_INCLUDE_UPDATES)")
    if params:
        raise RotoWireGuardViolation(f"ROTOWIRE_GUARD: parameters {sorted(params)} are not confirmed for {endpoint}")


def _folder(root: Path, endpoint: str) -> Path:
    return Path(root) / PROVIDER / raw_store._slug(endpoint)


def read(path: Path):
    return json.loads(raw_store.read_payload(Path(path)))


class Session:
    """One acquisition stage: live-request budget, ledger, cache root, injectable HTTP / credential / clock."""

    def __init__(self, stage: str, *, max_live_requests: int = PC.ROTOWIRE_MAX_LIVE_REQUESTS_PER_STAGE,
                 cache_root: Path | None = None, ledger_path: Path | None = None, http_get=None, sleep=time.sleep,
                 credential=None, clock=None):
        self.stage = stage
        self.max_live = int(max_live_requests)
        self.cache_root = Path(cache_root or PC.ROTOWIRE_CACHE_RAW)
        self.ledger_path = Path(ledger_path or PC.ROTOWIRE_REQUEST_LEDGER)
        self.http_get = http_get or requests.get
        self.sleep = sleep
        self._credential = credential or (lambda: credentials.get_strict(KEY_ENV))
        self.clock = clock or _now
        self.live_requests = 0
        self.entries: list[dict] = []

    def _log(self, entry: dict) -> dict:
        entry = {"provider": PROVIDER, "stage": self.stage, "feed_version": PC.ROTOWIRE_FEED_VERSION, **entry}
        OS.append_jsonl_locked(self.ledger_path, [entry])   # the same narrow <ledger>.lock as the WEB adapter
        self.entries.append(entry)
        return entry

    def _store(self, endpoint: str, content: bytes, collected_at: datetime):
        for k in range(1000):
            try:
                return raw_store.store(PROVIDER, endpoint, content, params={}, root=self.cache_root, compress=True,
                                       received_at=collected_at + timedelta(microseconds=k),
                                       extra_meta={"stage": self.stage, "production": True,
                                                   "season": PC.PRODUCTION_SEASON, "licensed_raw": True})
            except FileExistsError:
                continue
        raise RotoWireProviderFailure("raw cache: could not allocate a unique payload file")

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        """Live fetch. Returns the ledger entry (+ 'body', 'collected_at'). Raises RotoWireGuardViolation /
        RotoWireBudgetExceeded (before any call) or RotoWireProviderFailure (outage, HTTP error, non-JSON body)."""
        params = dict(params or {})
        requested_at = _utc(self.clock())
        base = {"endpoint": endpoint, "path": PATHS.get(endpoint), "params": params,
                "season": PC.PRODUCTION_SEASON, "requested_at": requested_at.isoformat()}
        try:
            check_request(endpoint, params)
        except RotoWireGuardViolation as e:
            self._log({**base, "status": "REFUSED_GUARD", "error": str(e), "retry_count": 0})
            raise
        attempt = 0
        while True:
            if self.live_requests >= self.max_live:
                self._log({**base, "status": "REFUSED_BUDGET", "retry_count": attempt})
                raise RotoWireBudgetExceeded(f"stage {self.stage!r} needs more than {self.max_live} live RotoWire requests")
            try:
                key = self._credential()
            except RuntimeError as e:                        # BLOCKED_CREDENTIAL: names the variable, never a value
                self._log({**base, "status": "BLOCKED_CREDENTIAL", "error": str(e), "retry_count": attempt})
                raise RotoWireProviderFailure(str(e)) from None
            self.live_requests += 1
            err, status = None, None
            try:
                resp = self.http_get(BASE + PATHS[endpoint], params={API_KEY_PARAM: key}, timeout=TIMEOUT)
                status = getattr(resp, "status_code", None)
            except requests.RequestException as exc:          # never re-raise: the exception text holds the URL
                err = f"network: {type(exc).__name__}"
            del key
            collected_at = _utc(self.clock())
            if err is None and status is not None and 200 <= status < 300:
                content = resp.content
                try:
                    body = json.loads(content)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    err = "MALFORMED_RESPONSE: body is not JSON"
                else:
                    rec = self._store(endpoint, content, collected_at)
                    e = self._log({**base, "status": "FETCHED" if rec.created else "FETCHED_UNCHANGED",
                                   "collected_at": collected_at.isoformat(), "http_status": status,
                                   "raw_payload_sha256": rec.sha256, "cache_path": str(rec.path.relative_to(self.cache_root)),
                                   "retry_count": attempt, "size_bytes": len(content)})
                    return {**e, "body": body}
            retryable = err is not None and err.startswith("network") or status == 429 or (status is not None and status >= 500)
            fail = {**base, "http_status": status, "retry_count": attempt, "error": err or f"HTTP {status}"}
            if not retryable or attempt >= MAX_RETRIES:
                e = self._log({**fail, "status": "FAILED"})
                raise RotoWireProviderFailure(f"RotoWire {endpoint} failed: {err or f'HTTP {status}'}", e)
            self._log({**fail, "status": "ATTEMPT_FAILED"})
            attempt += 1
            self.sleep(min(BACKOFF * 2 ** attempt, 30.0))


def ledger(path: Path | None = None) -> list[dict]:
    return OS.read_jsonl_locked(Path(path or PC.ROTOWIRE_REQUEST_LEDGER))   # LOCK_SH: never a half-appended entry


def last_success(entries: list[dict], endpoint: str, as_of) -> datetime | None:
    """Latest collected_at of a successful acquisition of `endpoint` that is processed (feed.ingest) at or before as_of."""
    lim = _utc(as_of)
    ts = [_utc(e["collected_at"]) for e in entries if e.get("endpoint") == endpoint and e.get("status") == "PROCESSED"
          and e.get("collected_at") and _utc(e["collected_at"]) <= lim]
    return max(ts) if ts else None
