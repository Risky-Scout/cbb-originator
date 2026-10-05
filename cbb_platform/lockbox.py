"""2025-26 lockbox guard — ONE provider-independent guard (LOCKBOX_POLICY.md).

The canonical lockbox is the 2025-26 NCAA men's basketball season. Providers spell seasons
differently, so nothing here compares raw integers. Every season value is first converted by
`canonical_season(provider, value)` to the canonical END-YEAR (2025-26 -> 2026), and every date by
`season_of_date` (US/Eastern season boundary: Aug 1). Only then is the lockbox rule evaluated.

    provider        season convention         lockbox spelled as
    cbbd            end year                  season=2026
    starter_pack    end year                  season=2026
    bdl             START year                seasons[]=2025      (BDL 2026 = 2026-27)
    torvik          end year                  year=2026
    any provider    label "2025-26" / "2025-2026"

Two layers, used by every network adapter through cbb_platform/source_fetch.py:
  * check_request  -> before the call: refuses a request that names, or could return, a lockbox game
                      (lockbox season, date or date range touching the window, an unbounded request,
                      a game id whose season cannot be proven to be a development season). Seasons
                      AFTER the lockbox (2026-27+) are also refused until separate production rules exist;
  * check_response -> before storage: refuses a payload containing a lockbox season or game date.
Unknown providers and unknown endpoints are refused (fail closed). Reference endpoints that carry no
game outcomes (conference/team/venue lists) are exempt. Sharp Stats availability capture is a
file ingest, not a network fetch, and is classified ALLOWED_AVAILABILITY_CAPTURE.
"""
from __future__ import annotations

import re
from datetime import date, datetime

LOCKBOX_SEASON = 2026                     # canonical end-year of 2025-26
LOCKBOX_LABEL = "2025-26"
LOCKBOX_START = date(2025, 8, 1)          # first US/Eastern date of 2025-26
LOCKBOX_END = date(2026, 7, 31)           # last US/Eastern date of 2025-26
LAST_DEVELOPMENT_SEASON = 2025            # 2024-25
LOCKBOX_SEASONS = frozenset({LOCKBOX_SEASON})   # kept for callers of the Stage 1 closure API


class LockboxViolation(RuntimeError):
    pass


# ----------------------------------------------------------------------------- season normalization
SEASON_CONVENTION = {"cbbd": "END_YEAR", "starter_pack": "END_YEAR", "torvik": "END_YEAR",
                     "bdl": "START_YEAR", "canonical": "END_YEAR"}
_LABEL = re.compile(r"^\s*(\d{4})\s*[-/]\s*(\d{2}|\d{4})\s*$")


def canonical_season(provider: str, value) -> int:
    """Provider season value -> canonical end-year. Raises on an unknown provider or a bad value."""
    if isinstance(value, str) and (m := _LABEL.match(value)):
        start, end = int(m.group(1)), m.group(2)
        end = int(end) if len(end) == 4 else (start // 100) * 100 + int(end)
        if end != start + 1:
            raise LockboxViolation(f"LOCKBOX: malformed season label {value!r}")
        return end
    convention = SEASON_CONVENTION.get(provider)
    if convention is None:
        raise LockboxViolation(f"LOCKBOX: provider {provider!r} has no registered season convention")
    year = int(float(value))
    return year + 1 if convention == "START_YEAR" else year


def season_label(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def _as_date(value) -> date:
    s = str(value).strip()
    if re.fullmatch(r"\d{8}", s):                                   # Torvik / compact YYYYMMDD
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    return date.fromisoformat(s[:10])


def season_of_date(value) -> int:
    d = _as_date(value)
    return d.year + 1 if d.month >= 8 else d.year


def _overlaps_lockbox(start, end) -> bool:
    lo = _as_date(start) if start else date.min
    hi = _as_date(end) if end else date.max
    return lo <= LOCKBOX_END and hi >= LOCKBOX_START


# ----------------------------------------------------------------------------- provider registry
# For each provider: which request parameters carry seasons, single dates, date ranges and game ids,
# and which endpoints are pure reference data. Every other endpoint is GUARDED.
PROVIDERS = {
    "cbbd": {"season": ("season", "year"), "date": (), "range": (("startDateRange", "endDateRange"),),
             "game": ("gameId",), "reference": {"conferences", "teams", "venues"}},
    "bdl": {"season": ("season", "seasons", "seasons[]"), "date": ("date", "dates", "dates[]"),
            "range": (("start_date", "end_date"),), "game": ("game_id", "game_ids", "game_ids[]"),
            "reference": {"teams", "conferences", "players", "players/active"}},
    "torvik": {"season": ("year", "season"), "date": ("date",), "range": (("begin", "end"),),
               "game": ("game_id",), "reference": set()},
}
ALLOWED_AVAILABILITY_CAPTURE = {"sharp_stats"}


def _values(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple, set)) else [v]


def check_season(season, provider: str = "canonical") -> None:
    if season is not None and canonical_season(provider, season) == LOCKBOX_SEASON:
        raise LockboxViolation(f"LOCKBOX: {provider} season {season} is {LOCKBOX_LABEL}, the frozen holdout")


def check_request(provider: str, endpoint: str, params: dict | None, known_game_season=None) -> None:
    """Raise unless the request provably cannot return 2025-26 data.

    `known_game_season(game_id) -> canonical end-year | None` resolves a provider game id from data
    already held. A game id whose season cannot be proven is refused (fail closed).
    """
    if provider in ALLOWED_AVAILABILITY_CAPTURE:
        return
    rules = PROVIDERS.get(provider)
    if rules is None:
        raise LockboxViolation(f"LOCKBOX: provider {provider!r} is not registered with the lockbox guard")
    if endpoint in rules["reference"]:
        return
    params = params or {}
    bounded = False

    for name in rules["season"]:
        for v in _values(params.get(name)):
            s = canonical_season(provider, v)
            check_season(s)
            if s > LOCKBOX_SEASON:
                raise LockboxViolation(f"LOCKBOX: {provider} season {v} = {season_label(s)} is current/future; "
                                       "separate production rules apply")
            bounded = True
    for name in rules["date"]:
        for v in _values(params.get(name)):
            if _overlaps_lockbox(v, v) or season_of_date(v) > LOCKBOX_SEASON:
                raise LockboxViolation(f"LOCKBOX: {provider} date {v} is not a development date")
            bounded = True
    for lo_name, hi_name in rules["range"]:
        lo, hi = params.get(lo_name), params.get(hi_name)
        if lo or hi:
            if _overlaps_lockbox(lo, hi):
                raise LockboxViolation(f"LOCKBOX: {provider} date range {lo}..{hi} touches "
                                       f"{LOCKBOX_START}..{LOCKBOX_END}")
            if hi and season_of_date(hi) > LOCKBOX_SEASON:
                raise LockboxViolation(f"LOCKBOX: {provider} date range ends in a current/future season")
            bounded = bounded or bool(lo and hi)
    for name in rules["game"]:
        for gid in _values(params.get(name)):
            s = known_game_season(gid) if known_game_season else None
            if s is None:
                raise LockboxViolation(f"LOCKBOX: season of {provider} game {gid} is not provable; refusing")
            check_season(s)
            if s > LAST_DEVELOPMENT_SEASON:
                raise LockboxViolation(f"LOCKBOX: {provider} game {gid} is season {season_label(s)}")
            bounded = True
    if not bounded:
        raise LockboxViolation(f"LOCKBOX: unbounded {provider} {endpoint} request (provider default is 'now'); "
                               "pass a development season, explicit dates, a closed date range, or game ids")


SEASON_KEYS = {"cbbd": ("season",), "bdl": ("season",), "torvik": ("year", "season"), "starter_pack": ("season",)}
GAME_DATE_KEYS = ("date", "startDate", "start_date", "gameDate", "game_date", "gameStartDate", "datetime", "wallclock")


def check_response(provider: str, payload) -> None:
    """Refuse a parsed payload that contains any 2025-26 season or game date, at any nesting depth."""
    season_keys = SEASON_KEYS.get(provider, ("season",))
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in season_keys and isinstance(v, (int, float, str)) and str(v).strip():
                    try:
                        s = canonical_season(provider, v)
                    except ValueError:
                        s = None
                    if s == LOCKBOX_SEASON:
                        raise LockboxViolation(f"LOCKBOX: {provider} response contains {k}={v} ({LOCKBOX_LABEL})")
                elif k in GAME_DATE_KEYS and isinstance(v, str) and re.match(r"\d{4}-?\d{2}-?\d{2}", v):
                    if _overlaps_lockbox(v, v):
                        raise LockboxViolation(f"LOCKBOX: {provider} response contains {k}={v[:10]} inside 2025-26")
                elif isinstance(v, (dict, list)):
                    stack.append(v)


# ----------------------------------------------------------------------------- game-id resolvers
def canonical_game_season(con, provider: str = "cbbd"):
    """A `known_game_season` resolver backed by the canonical database (provider game id -> end-year)."""
    if provider == "cbbd":
        sql = """SELECT g.season FROM metadata.game_id_map m JOIN core.games g USING (canonical_game_id)
                 WHERE m.provider = 'CBBD' AND m.provider_game_key = ?"""
    elif provider == "bdl":
        sql = "SELECT cbbd_season FROM benchmark.bdl_games WHERE CAST(bdl_game_id AS VARCHAR) = ?"
    else:
        return lambda gid: None

    def resolve(game_id):
        row = con.execute(sql, [str(game_id)]).fetchone()
        return row[0] if row else None
    return resolve
