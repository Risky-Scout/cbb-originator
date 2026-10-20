"""PRODUCTION-ONLY CBBD acquisition for the current season (2026-27). Separate from research acquisition.

Research / lockbox acquisition (cbb_platform/source_fetch.py -> lockbox.check_request) is NOT used, modified or
bypassed here: it still refuses every 2026-27 request. This module is a second, narrow path that admits ONLY the
production season, through its own guard:

    endpoint allowlist        games, games/teams, games/players, substitutions/game, teams/roster,
                              recruiting/players, recruiting/portal
    season                    must equal PRODUCTION_SEASON (2027 = 2026-27), required on every seasonal endpoint
    year (recruiting/portal)  must equal PRESEASON_CLASS_YEAR (2026: the class / portal cycle that feeds 2026-27)
    date range                required for game-family endpoints; closed and inside 2026-08-01 .. 2027-07-31
    gameId                    must be a 2026-27 game of the production schedule (resolver); otherwise refused
    any other parameter       refused
There is no switch that widens it: no historical season, no backfill, no research store.

Responses are checked before storage (every `season` = 2027, every `year` = 2026, every game date inside the season).
Payloads are cached in the immutable raw_store layout under data/prod/cache/raw (gitignored; never data/raw, which the
research canonical build reads). A sufficiently fresh cached response is reused without any network call.

Every acquisition (live or cache hit, success or failure) is one JSON line in data/prod/cache/cbbd_production_requests.jsonl:
provider, endpoint, params, requested_at, received_at, status, http_status, season, record_count, response_sha256,
cache_path, retry_count, quota_remaining, stage. No credential ever reaches params, logs, payloads or exceptions.

A Session carries a hard per-stage live-request budget (MAX_LIVE_REQUESTS_PER_STAGE = 20). A stage that would exceed it
raises BudgetExceeded BEFORE the call (STOP and report).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from cbb_platform import credentials, raw_store
from cbb_platform.adapters import cbbd as CBBD_ADAPTER
from cbb_platform.prod import config as PC

PROVIDER = "cbbd"
BASE = CBBD_ADAPTER.BASE
SEASONAL = ("games", "games/teams", "games/players", "teams/roster", "stats/player/season")
CLASS_YEAR = ("recruiting/players", "recruiting/portal")
ALLOWED = {
    "games": {"season", "startDateRange", "endDateRange"},
    "games/teams": {"season", "startDateRange", "endDateRange"},
    "games/players": {"season", "startDateRange", "endDateRange"},
    "substitutions/game": {"gameId"},
    "teams/roster": {"season"},
    "stats/player/season": {"season"},
    "recruiting/players": {"year"},
    "recruiting/portal": {"year"},
}
RANGED = ("games", "games/teams", "games/players")
COMPRESS = {"games/players", "substitutions/game", "teams/roster", "recruiting/players", "recruiting/portal",
            "stats/player/season"}
GAME_DATE_KEYS = ("startDate", "gameStartDate", "start_date", "date")
MAX_RETRIES = 3
BACKOFF = 2.0
TIMEOUT = 120


class ProductionGuardViolation(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    def __init__(self, msg: str, entry: dict | None = None):
        super().__init__(msg)
        self.entry = entry or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _season_window() -> tuple[date, date]:
    return date.fromisoformat(PC.PRODUCTION_SEASON_START), date.fromisoformat(PC.PRODUCTION_SEASON_END)


def _as_date(v) -> date:
    return date.fromisoformat(str(v).strip()[:10])


# ============================================================================ guard (production season only)
def check_request(endpoint: str, params: dict, game_season=None) -> None:
    """Raise ProductionGuardViolation unless the request is a 2026-27 production request. `game_season(gameId)` ->
    canonical end-year from the PRODUCTION schedule (None when unknown)."""
    if endpoint not in ALLOWED:
        raise ProductionGuardViolation(f"PRODUCTION_GUARD: endpoint {endpoint!r} is not on the production allowlist")
    extra = sorted(set(params) - ALLOWED[endpoint])
    if extra:
        raise ProductionGuardViolation(f"PRODUCTION_GUARD: parameters {extra} not allowed for {endpoint}")
    if endpoint in SEASONAL:
        s = params.get("season")
        if s is None or isinstance(s, (list, tuple, set)) or str(s) != str(PC.PRODUCTION_SEASON):
            raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} season must be exactly {PC.PRODUCTION_SEASON} "
                                           f"(2026-27); got {s!r}")
    if endpoint in CLASS_YEAR:
        y = params.get("year")
        if y is None or isinstance(y, (list, tuple, set)) or str(y) != str(PC.PRESEASON_CLASS_YEAR):
            raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} year must be exactly {PC.PRESEASON_CLASS_YEAR} "
                                           f"(feeds 2026-27); got {y!r}")
    if endpoint in RANGED:
        lo, hi = params.get("startDateRange"), params.get("endDateRange")
        if not (lo and hi):
            raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} needs a closed date range")
        a, b = _season_window()
        if not (a <= _as_date(lo) <= _as_date(hi) <= b):
            raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} range {lo}..{hi} is outside 2026-27 ({a}..{b})")
    if endpoint == "substitutions/game":
        gid = params.get("gameId")
        s = game_season(gid) if (game_season and gid is not None) else None
        if s != PC.PRODUCTION_SEASON:
            raise ProductionGuardViolation(f"PRODUCTION_GUARD: game {gid} is not a proven 2026-27 production game")


def check_response(endpoint: str, body) -> None:
    """Every season field = 2027, every class year = 2026, every game date inside 2026-27 (any nesting depth)."""
    a, b = _season_window()
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k == "season" and v is not None and str(v).strip() and str(v) != str(PC.PRODUCTION_SEASON):
                    raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} response contains season={v}")
                if k == "year" and endpoint in CLASS_YEAR and v is not None and str(v) != str(PC.PRESEASON_CLASS_YEAR):
                    raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} response contains year={v}")
                if k in GAME_DATE_KEYS and isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
                    if not (a <= _as_date(v) <= b):
                        raise ProductionGuardViolation(f"PRODUCTION_GUARD: {endpoint} response contains {k}={v[:10]} "
                                                       "outside 2026-27")
                if isinstance(v, (dict, list)):
                    stack.append(v)


# ============================================================================ cache
def request_key(endpoint: str, params: dict) -> str:
    return f"{endpoint}|{json.dumps(params, sort_keys=True, default=str)}"


def cached(endpoint: str, params: dict, root: Path | None = None, as_of=None) -> tuple[Path, dict] | None:
    """Latest cached payload of exactly this request received at or before `as_of` (default: now)."""
    root = Path(root or PC.CBBD_CACHE_RAW)
    folder = root / PROVIDER / raw_store._slug(endpoint)
    if not folder.exists():
        return None
    lim = _now() if as_of is None else _utc(as_of)
    best = None
    for mp in folder.glob("*.meta.json"):
        m = json.loads(mp.read_text())
        if m["endpoint"] != endpoint or not raw_store._same_params(m["params"], params):
            continue
        if _utc(m["received_at"]) > lim:
            continue
        if best is None or (m["received_at"], m["file"]) > (best[1]["received_at"], best[1]["file"]):
            best = (mp.parent / m["file"], m)
    return best


def _utc(x) -> datetime:
    if isinstance(x, datetime):
        t = x
    else:
        s = str(x).replace("Z", "+00:00")
        t = datetime.fromisoformat(s)
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def read(path: Path):
    return json.loads(raw_store.read_payload(Path(path)))


def _count(body) -> int | None:
    return len(body) if isinstance(body, list) else (1 if isinstance(body, dict) else None)


def _season_of(endpoint: str, params: dict) -> int:
    return PC.PRODUCTION_SEASON


# ============================================================================ session
class Session:
    """One acquisition stage: shared budget, ledger, cache root and quota floor."""

    def __init__(self, stage: str, *, max_live_requests: int = PC.MAX_LIVE_REQUESTS_PER_STAGE,
                 cache_root: Path | None = None, ledger_path: Path | None = None, http_get=None, sleep=time.sleep,
                 game_season=None, quota_reserve: int = PC.CBBD_QUOTA_RESERVE, credential=None, clock=None):
        self.stage = stage
        self.max_live = int(max_live_requests)
        self.cache_root = Path(cache_root or PC.CBBD_CACHE_RAW)
        self.ledger_path = Path(ledger_path or PC.CBBD_REQUEST_LEDGER)
        self.http_get = http_get or requests.get
        self.sleep = sleep
        self.game_season = game_season
        self.quota_reserve = quota_reserve
        self._credential = credential or (lambda: credentials.get_strict("CBBD_API_KEY"))
        self.clock = clock or _now
        self.live_requests = 0
        self.cache_hits = 0
        self.quota_remaining: int | None = None
        self.entries: list[dict] = []

    def _log(self, entry: dict) -> dict:
        entry = {"provider": PROVIDER, "stage": self.stage, **entry}
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        self.entries.append(entry)
        return entry

    def _store(self, endpoint: str, content: bytes, params: dict, purpose: str):
        """raw_store names a file by receipt instant + content hash: two identical bodies (e.g. two empty windows) received
        in the same microsecond would collide, so the receipt instant is advanced by 1 us (never overwritten)."""
        t = _utc(self.clock())
        for k in range(1000):
            try:
                return raw_store.store(PROVIDER, endpoint, content, params=params, root=self.cache_root,
                                       compress=endpoint in COMPRESS, received_at=t + timedelta(microseconds=k),
                                       extra_meta={"purpose": purpose, "stage": self.stage, "production": True,
                                                   "season": PC.PRODUCTION_SEASON})
            except FileExistsError:
                continue
        raise ProviderFailure("raw cache: could not allocate a unique payload file")

    def get(self, endpoint: str, params: dict, *, max_age_hours: float | None, purpose: str = "") -> dict:
        """Cached-or-live fetch of one request. Returns the ledger entry (+ 'body'). max_age_hours=None: any cached copy.
        Raises ProductionGuardViolation / BudgetExceeded (before any call) or ProviderFailure."""
        params = dict(params)
        requested_at = _utc(self.clock())
        base = {"endpoint": endpoint, "params": params, "request_key": request_key(endpoint, params),
                "season": _season_of(endpoint, params), "purpose": purpose, "requested_at": requested_at.isoformat()}
        try:
            check_request(endpoint, params, self.game_season)
        except ProductionGuardViolation as e:
            self._log({**base, "status": "REFUSED_GUARD", "error": str(e), "retry_count": 0})
            raise
        hit = cached(endpoint, params, self.cache_root, requested_at)
        if hit is not None:
            path, meta = hit
            age_h = (requested_at - _utc(meta["received_at"])).total_seconds() / 3600.0
            if max_age_hours is None or age_h <= max_age_hours:
                body = read(path)
                self.cache_hits += 1
                e = self._log({**base, "status": "CACHE_HIT", "received_at": meta["received_at"], "http_status": None,
                               "record_count": _count(body), "response_sha256": meta["sha256"],
                               "cache_path": str(path.relative_to(self.cache_root)), "retry_count": 0,
                               "cache_age_hours": round(age_h, 3)})
                return {**e, "body": body}
        attempt = 0
        while True:
            if self.live_requests >= self.max_live:
                self._log({**base, "status": "REFUSED_BUDGET", "retry_count": attempt,
                           "error": f"stage {self.stage!r} would exceed {self.max_live} live requests"})
                raise BudgetExceeded(f"stage {self.stage!r} needs more than {self.max_live} live CBBD requests: STOP")
            if self.quota_remaining is not None and self.quota_remaining < self.quota_reserve:
                self._log({**base, "status": "REFUSED_QUOTA", "retry_count": attempt, "quota_remaining": self.quota_remaining})
                raise ProviderFailure(f"CBBD quota {self.quota_remaining} below reserve {self.quota_reserve}")
            key = self._credential()
            path = CBBD_ADAPTER.ENDPOINTS[endpoint].format(**params)
            query = {k: v for k, v in params.items() if "{" + k + "}" not in CBBD_ADAPTER.ENDPOINTS[endpoint]}
            self.live_requests += 1
            err, status, retry_after = None, None, None
            try:
                resp = self.http_get(BASE + path, params=query, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
                status = getattr(resp, "status_code", None)
                hdr = getattr(resp, "headers", None) or {}
                if hdr.get("X-CallLimit-Remaining") is not None:
                    self.quota_remaining = int(hdr.get("X-CallLimit-Remaining"))
                retry_after = hdr.get("Retry-After")
            except requests.RequestException as exc:          # never re-raise: the exception may hold the request
                err = f"network: {type(exc).__name__}"
            del key
            if err is None and status is not None and 200 <= status < 300:
                content = resp.content
                try:
                    body = json.loads(content)
                    check_response(endpoint, body)
                except ProductionGuardViolation as e:
                    self._log({**base, "status": "REFUSED_RESPONSE_GUARD", "http_status": status, "retry_count": attempt,
                               "error": str(e), "quota_remaining": self.quota_remaining})
                    raise
                except json.JSONDecodeError:
                    err = "invalid JSON"
                else:
                    rec = self._store(endpoint, content, params, purpose)
                    e = self._log({**base, "status": "FETCHED" if rec.created else "FETCHED_UNCHANGED",
                                   "received_at": rec.received_at, "verified_at": _utc(self.clock()).isoformat(),
                                   "http_status": status, "record_count": _count(body),
                                   "response_sha256": rec.sha256, "cache_path": str(rec.path.relative_to(self.cache_root)),
                                   "retry_count": attempt, "quota_remaining": self.quota_remaining,
                                   "size_bytes": len(content)})
                    return {**e, "body": body}
            retryable = err is not None or status == 429 or (status is not None and status >= 500)
            fail = {**base, "http_status": status, "retry_count": attempt, "error": err or f"HTTP {status}",
                    "quota_remaining": self.quota_remaining}
            if not retryable or attempt >= MAX_RETRIES:
                e = self._log({**fail, "status": "FAILED"})
                raise ProviderFailure(f"CBBD {endpoint} failed: {err or f'HTTP {status}'}", e)
            self._log({**fail, "status": "ATTEMPT_FAILED"})
            attempt += 1
            try:
                wait = float(retry_after) if retry_after is not None else BACKOFF * 2 ** attempt
            except ValueError:
                wait = BACKOFF * 2 ** attempt
            self.sleep(min(wait, 60.0))

    def summary(self) -> dict:
        return {"stage": self.stage, "live_requests": self.live_requests, "cache_hits": self.cache_hits,
                "max_live_requests": self.max_live, "quota_remaining": self.quota_remaining}


def ledger(path: Path | None = None) -> list[dict]:
    p = Path(path or PC.CBBD_REQUEST_LEDGER)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
