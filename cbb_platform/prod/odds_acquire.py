"""PRODUCTION-ONLY The Odds API acquisition for 2026-27 NCAAB player props. Never research, never historical.

Provider support for NCAAB player props is DOCUMENTED (sport `basketball_ncaab`; props one event at a time through
/v4/sports/basketball_ncaab/events/{eventId}/odds). Three endpoints only:

    events       GET /v4/sports/basketball_ncaab/events                   0 credits (documented)
                 params: dateFormat=iso, commenceTimeFrom, commenceTimeTo (both required, inside 2026-08-01..2027-07-31)
    event_markets GET /v4/sports/basketball_ncaab/events/{eventId}/markets 1 credit (documented)
                 params: eventId, regions=us, dateFormat=iso, [bookmakers]: the market keys each bookmaker currently
                 advertises for the event. ALWAYS called before event_odds (odds_feed.collect); same event guards.
    event_odds   GET /v4/sports/basketball_ncaab/events/{eventId}/odds    cost = markets returned x regions (documented)
                 params: eventId, regions=us, markets (the advertised subset of quotes.REQUEST_MARKETS only),
                 oddsFormat=american, dateFormat=iso, includeLinks=true, includeSids=true, [bookmakers]
                 eventId must be in an events listing already acquired (inside the season) and the call must fall
                 inside ODDS_PROP_WINDOW_HOURS before that listed commence time (props are posted near tip);
                 never after commence.
No /odds featured-market call, no historical endpoint, no other sport, no other parameter: there is no switch that
widens it.

Every paid call is costed BEFORE it is made (upper bound = markets requested x 1 region) against a per-stage credit
budget and the account quota reserve; the account quota must be known (the free events call reports it) before any
paid call. Response headers x-requests-remaining / x-requests-used / x-requests-last are recorded.

Payloads are cached immutably under data/prod/cache/raw/the-odds-api (gitignored). Every acquisition is one JSON line
in data/prod/cache/odds_production_requests.jsonl. The API key travels only in the request query: it never reaches
params, logs, payloads, cache metadata or exceptions (network exceptions are reduced to their type name, because
their text can contain the request URL).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from cbb_platform import credentials, raw_store
from cbb_platform.prod import config as PC, quotes as QT

PROVIDER = QT.PROVIDER
BASE = "https://api.the-odds-api.com"
SPORT = QT.SPORT_KEY
PATHS = {"events": f"/v4/sports/{SPORT}/events", "event_markets": f"/v4/sports/{SPORT}/events/{{eventId}}/markets",
         "event_odds": f"/v4/sports/{SPORT}/events/{{eventId}}/odds"}
EVENT_ENDPOINTS = ("event_markets", "event_odds")              # per-event endpoints: listed, pregame, inside the window
ALLOWED = {
    "events": {"dateFormat", "commenceTimeFrom", "commenceTimeTo"},
    "event_markets": {"eventId", "regions", "dateFormat", "bookmakers"},
    "event_odds": {"eventId", "regions", "markets", "oddsFormat", "dateFormat", "includeLinks", "includeSids",
                   "bookmakers"},
}
REQUIRED = {"events": {"dateFormat", "commenceTimeFrom", "commenceTimeTo"},
            "event_markets": {"eventId", "regions", "dateFormat"},
            "event_odds": {"eventId", "regions", "markets", "oddsFormat", "dateFormat"}}
FIXED = {"dateFormat": "iso", "regions": PC.ODDS_REGIONS, "oddsFormat": "american", "includeLinks": "true",
         "includeSids": "true"}
MAX_RETRIES = 3
BACKOFF = 2.0
TIMEOUT = 60


class OddsGuardViolation(RuntimeError):
    pass


class OddsBudgetExceeded(RuntimeError):
    pass


class OddsProviderFailure(RuntimeError):
    def __init__(self, msg: str, entry: dict | None = None):
        super().__init__(msg)
        self.entry = entry or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(x) -> datetime:
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _season_window() -> tuple[datetime, datetime]:
    a = _utc(PC.PRODUCTION_SEASON_START + "T00:00:00Z")
    return a, _utc(PC.PRODUCTION_SEASON_END + "T00:00:00Z") + timedelta(days=1, hours=12)   # late tips of Jul 31 ET


def _markets(v) -> list[str]:
    return [m.strip() for m in str(v).split(",") if m.strip()]


def estimated_cost(endpoint: str, params: dict) -> int:
    """Documented upper bound: unique markets requested x regions (events listing: 0; event markets: 1)."""
    if endpoint == "event_markets":
        return 1
    if endpoint != "event_odds":
        return 0
    return len(set(_markets(params.get("markets", "")))) * len(_markets(params.get("regions", "")))


# ============================================================================ guard (2026-27 NCAAB only)
def check_request(endpoint: str, params: dict, event_commence=None, now=None) -> None:
    """Raise OddsGuardViolation unless this is a 2026-27 NCAAB production request. `event_commence(eventId)` -> the
    commence time of that event in an events listing already acquired (None when not listed)."""
    if endpoint not in ALLOWED:
        raise OddsGuardViolation(f"ODDS_GUARD: endpoint {endpoint!r} is not on the production allowlist")
    extra = sorted(set(params) - ALLOWED[endpoint])
    if extra:
        raise OddsGuardViolation(f"ODDS_GUARD: parameters {extra} not allowed for {endpoint}")
    missing = sorted(REQUIRED[endpoint] - set(params))
    if missing:
        raise OddsGuardViolation(f"ODDS_GUARD: {endpoint} needs {missing}")
    for k, v in FIXED.items():
        if k in params and str(params[k]) != v:
            raise OddsGuardViolation(f"ODDS_GUARD: {k} must be {v!r}; got {params[k]!r}")
    a, b = _season_window()
    if endpoint == "events":
        try:
            lo, hi = _utc(params["commenceTimeFrom"]), _utc(params["commenceTimeTo"])
        except ValueError as e:
            raise OddsGuardViolation(f"ODDS_GUARD: unparseable commence window ({e})") from None
        if not (a <= lo <= hi <= b):
            raise OddsGuardViolation(f"ODDS_GUARD: commence window {lo.isoformat()}..{hi.isoformat()} is outside 2026-27")
        return
    if endpoint == "event_odds":
        mk = _markets(params["markets"])
        bad = [m for m in mk if m not in QT.REQUEST_MARKETS]
        if not mk or bad or len(mk) != len(set(mk)):
            raise OddsGuardViolation(f"ODDS_GUARD: markets must be distinct documented NCAAB player-prop keys; bad={bad}")
    eid = str(params["eventId"])
    if not eid or "/" in eid or "?" in eid:
        raise OddsGuardViolation("ODDS_GUARD: malformed eventId")
    ct = event_commence(eid) if event_commence else None
    if ct is None:
        raise OddsGuardViolation(f"ODDS_GUARD: event {eid} is not in an acquired 2026-27 events listing")
    ct = _utc(ct)
    if not (a <= ct <= b):
        raise OddsGuardViolation(f"ODDS_GUARD: event {eid} commences {ct.isoformat()}, outside 2026-27")
    t = _utc(now or _now())
    if t >= ct:
        raise OddsGuardViolation(f"ODDS_GUARD: event {eid} has already commenced (pregame quotes only)")
    if (ct - t).total_seconds() / 3600.0 > PC.ODDS_PROP_WINDOW_HOURS:
        raise OddsGuardViolation(f"ODDS_GUARD: event {eid} is more than {PC.ODDS_PROP_WINDOW_HOURS} h from commence; "
                                 "player props are not requested this early")


def check_response(endpoint: str, body, params: dict) -> None:
    a, b = _season_window()
    evs = body if endpoint == "events" else [body]
    if endpoint == "events" and not isinstance(body, list):
        raise OddsGuardViolation("ODDS_GUARD: events response is not a list")
    if endpoint in EVENT_ENDPOINTS and not isinstance(body, dict):
        raise OddsGuardViolation(f"ODDS_GUARD: {endpoint} response is not an object")
    for e in evs:
        if e.get("sport_key") not in (None, SPORT):
            raise OddsGuardViolation(f"ODDS_GUARD: response sport_key {e.get('sport_key')!r}")
        ct = e.get("commence_time")
        if ct and not (a <= _utc(ct) <= b):
            raise OddsGuardViolation(f"ODDS_GUARD: response event commences {ct}, outside 2026-27")
    if endpoint in EVENT_ENDPOINTS and str(body.get("id")) != str(params["eventId"]):
        raise OddsGuardViolation(f"ODDS_GUARD: {endpoint} response is for a different event")
    if endpoint in EVENT_ENDPOINTS:
        for bk in body.get("bookmakers") or []:
            if not isinstance(bk, dict) or not isinstance(bk.get("markets") or [], list) or \
                    any(not isinstance(m, dict) or not isinstance(m.get("key"), str) for m in bk.get("markets") or []):
                raise OddsGuardViolation(f"ODDS_GUARD: {endpoint} response has a malformed bookmaker / market entry")


# ============================================================================ cache
def request_key(endpoint: str, params: dict) -> str:
    return f"{endpoint}|{json.dumps(params, sort_keys=True, default=str)}"


def _folder(root: Path, endpoint: str) -> Path:
    return Path(root) / PROVIDER / raw_store._slug(endpoint)


def cached(endpoint: str, params: dict, root: Path | None = None, as_of=None) -> tuple[Path, dict] | None:
    """Latest cached payload of exactly this request received at or before `as_of` (default: now)."""
    folder = _folder(root or PC.ODDS_CACHE_RAW, endpoint)
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


def latest(endpoint: str, root: Path | None = None, as_of=None) -> list[tuple[Path, dict]]:
    """Every cached payload of `endpoint` received at or before `as_of`, oldest first."""
    folder = _folder(root or PC.ODDS_CACHE_RAW, endpoint)
    lim = _now() if as_of is None else _utc(as_of)
    out = []
    for mp in (folder.glob("*.meta.json") if folder.exists() else []):
        m = json.loads(mp.read_text())
        if m["endpoint"] == endpoint and _utc(m["received_at"]) <= lim:
            out.append((mp.parent / m["file"], m))
    return sorted(out, key=lambda x: (x[1]["received_at"], x[1]["file"]))


def read(path: Path):
    return json.loads(raw_store.read_payload(Path(path)))


def _header(hdr, name: str):
    for k, v in (hdr or {}).items():
        if str(k).lower() == name:
            return v
    return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ============================================================================ session
class Session:
    """One acquisition stage: live-request budget, credit budget, ledger, cache root and quota floor."""

    def __init__(self, stage: str, *, max_live_requests: int = PC.ODDS_MAX_LIVE_REQUESTS_PER_STAGE,
                 max_credits: int = PC.ODDS_MAX_CREDITS_PER_STAGE, cache_root: Path | None = None,
                 ledger_path: Path | None = None, http_get=None, sleep=time.sleep, event_commence=None,
                 quota_reserve: int = PC.ODDS_QUOTA_RESERVE, credential=None, clock=None):
        self.stage = stage
        self.max_live = int(max_live_requests)
        self.max_credits = int(max_credits)
        self.cache_root = Path(cache_root or PC.ODDS_CACHE_RAW)
        self.ledger_path = Path(ledger_path or PC.ODDS_REQUEST_LEDGER)
        self.http_get = http_get or requests.get
        self.sleep = sleep
        self.event_commence = event_commence
        self.quota_reserve = int(quota_reserve)
        self._credential = credential or (lambda: credentials.get_strict("ODDS_API_KEY"))
        self.clock = clock or _now
        self.live_requests = 0
        self.cache_hits = 0
        self.credits_charged = 0
        self.quota_remaining: int | None = None
        self.quota_used: int | None = None
        self.entries: list[dict] = []

    def _log(self, entry: dict) -> dict:
        entry = {"provider": PROVIDER, "stage": self.stage, **entry}
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        self.entries.append(entry)
        return entry

    def _store(self, endpoint: str, content: bytes, params: dict, purpose: str):
        t = _utc(self.clock())
        for k in range(1000):
            try:
                return raw_store.store(PROVIDER, endpoint, content, params=params, root=self.cache_root, compress=True,
                                       received_at=t + timedelta(microseconds=k),
                                       extra_meta={"purpose": purpose, "stage": self.stage, "production": True,
                                                   "season": PC.PRODUCTION_SEASON, "sport": SPORT})
            except FileExistsError:
                continue
        raise OddsProviderFailure("raw cache: could not allocate a unique payload file")

    def get(self, endpoint: str, params: dict, *, max_age_seconds: float | None, purpose: str = "") -> dict:
        """Cached-or-live fetch. Returns the ledger entry (+ 'body'). Raises OddsGuardViolation / OddsBudgetExceeded
        (before any call) or OddsProviderFailure."""
        params = {k: str(v) for k, v in params.items()}
        requested_at = _utc(self.clock())
        est = estimated_cost(endpoint, params)
        base = {"endpoint": endpoint, "params": params, "request_key": request_key(endpoint, params),
                "season": PC.PRODUCTION_SEASON, "sport": SPORT, "purpose": purpose,
                "requested_at": requested_at.isoformat(), "estimated_cost": est}
        try:
            check_request(endpoint, params, self.event_commence, requested_at)
        except OddsGuardViolation as e:
            self._log({**base, "status": "REFUSED_GUARD", "error": str(e), "retry_count": 0})
            raise
        hit = cached(endpoint, params, self.cache_root, requested_at)
        if hit is not None:
            path, meta = hit
            age = (requested_at - _utc(meta["received_at"])).total_seconds()
            if max_age_seconds is None or age <= max_age_seconds:
                body = read(path)
                self.cache_hits += 1
                e = self._log({**base, "status": "CACHE_HIT", "received_at": meta["received_at"], "http_status": None,
                               "response_sha256": meta["sha256"], "cache_path": str(path.relative_to(self.cache_root)),
                               "retry_count": 0, "cache_age_seconds": round(age, 1), "cost": 0})
                return {**e, "body": body}
        attempt = 0
        while True:
            if self.live_requests >= self.max_live:
                self._log({**base, "status": "REFUSED_BUDGET", "retry_count": attempt,
                           "error": f"stage {self.stage!r} would exceed {self.max_live} live requests"})
                raise OddsBudgetExceeded(f"stage {self.stage!r} needs more than {self.max_live} live Odds API requests: STOP")
            if est and self.credits_charged + est > self.max_credits:
                self._log({**base, "status": "REFUSED_BUDGET", "retry_count": attempt,
                           "error": f"stage {self.stage!r} would exceed {self.max_credits} credits"})
                raise OddsBudgetExceeded(f"stage {self.stage!r} would exceed {self.max_credits} Odds API credits: STOP")
            if est and (self.quota_remaining is None or self.quota_remaining - est < self.quota_reserve):
                self._log({**base, "status": "REFUSED_QUOTA", "retry_count": attempt,
                           "quota_remaining": self.quota_remaining})
                raise OddsProviderFailure(f"Odds API quota {self.quota_remaining} unknown or would fall below the reserve "
                                          f"{self.quota_reserve}")
            key = self._credential()
            path = PATHS[endpoint].format(**params)
            query = {k: v for k, v in params.items() if k != "eventId"}
            self.live_requests += 1
            err, status, hdr = None, None, {}
            try:
                resp = self.http_get(BASE + path, params={**query, "apiKey": key}, timeout=TIMEOUT)
                status = getattr(resp, "status_code", None)
                hdr = getattr(resp, "headers", None) or {}
            except requests.RequestException as exc:          # never re-raise: the exception text holds the URL
                err = f"network: {type(exc).__name__}"
            del key
            rem, used, last = (_int(_header(hdr, h)) for h in ("x-requests-remaining", "x-requests-used", "x-requests-last"))
            if rem is not None:
                self.quota_remaining = rem
            if used is not None:
                self.quota_used = used
            self.credits_charged += last if last is not None else (est if status and 200 <= status < 300 else 0)
            usage = {"quota_remaining": rem, "quota_used": used, "cost": last}
            if err is None and status is not None and 200 <= status < 300:
                content = resp.content
                try:
                    body = json.loads(content)
                    check_response(endpoint, body, params)
                except OddsGuardViolation as e:
                    self._log({**base, **usage, "status": "REFUSED_RESPONSE_GUARD", "http_status": status,
                               "retry_count": attempt, "error": str(e)})
                    raise
                except json.JSONDecodeError:
                    err = "invalid JSON"
                else:
                    rec = self._store(endpoint, content, params, purpose)
                    n_mk = (len({m.get("key") for bk in body.get("bookmakers", []) or [] for m in bk.get("markets", []) or []})
                            if isinstance(body, dict) else None)
                    e = self._log({**base, **usage, "status": "FETCHED" if rec.created else "FETCHED_UNCHANGED",
                                   "received_at": rec.received_at, "http_status": status,
                                   "record_count": len(body) if isinstance(body, list) else len(body.get("bookmakers") or []),
                                   "markets_returned": n_mk, "response_sha256": rec.sha256,
                                   "cache_path": str(rec.path.relative_to(self.cache_root)), "retry_count": attempt,
                                   "size_bytes": len(content)})
                    return {**e, "body": body}
            retryable = err is not None or status == 429 or (status is not None and status >= 500)
            fail = {**base, **usage, "http_status": status, "retry_count": attempt, "error": err or f"HTTP {status}"}
            if not retryable or attempt >= MAX_RETRIES:
                e = self._log({**fail, "status": "FAILED"})
                raise OddsProviderFailure(f"Odds API {endpoint} failed: {err or f'HTTP {status}'}", e)
            self._log({**fail, "status": "ATTEMPT_FAILED"})
            attempt += 1
            self.sleep(min(BACKOFF * 2 ** attempt, 30.0))

    def summary(self) -> dict:
        return {"stage": self.stage, "live_requests": self.live_requests, "cache_hits": self.cache_hits,
                "credits_charged": self.credits_charged, "max_credits": self.max_credits,
                "max_live_requests": self.max_live, "quota_remaining": self.quota_remaining,
                "quota_used": self.quota_used}


def ledger(path: Path | None = None) -> list[dict]:
    p = Path(path or PC.ODDS_REQUEST_LEDGER)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
