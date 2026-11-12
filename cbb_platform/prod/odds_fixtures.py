"""Clearly labeled The Odds API v4 FORMAT FIXTURES (synthetic; never live data, never a real credential).

Shapes follow the documented responses: GET /v4/sports/basketball_ncaab/events -> [{id, sport_key, sport_title,
commence_time, home_team, away_team}], GET .../events/{eventId}/odds -> {id, sport_key, sport_title, commence_time,
home_team, away_team, bookmakers: [{key, title, [link], markets: [{key, last_update, [link], outcomes: [{name,
description, price, point, [link]}]}]}]}; usage headers x-requests-remaining / x-requests-used / x-requests-last;
cost = unique markets returned x regions.
"""
from __future__ import annotations

import json

from cbb_platform.prod import odds_acquire as OA

LABEL = "FIXTURE (synthetic The Odds API v4-format payloads; NOT live)"
DUMMY_ODDS_CREDENTIAL = "fixture-odds-not-a-real-key"


class _Resp:
    def __init__(self, status: int, body, headers: dict | None = None):
        self.status_code = status
        self.content = json.dumps(body).encode()
        self.headers = headers or {}


def event(eid: str, home: str, away: str, commence: str) -> dict:
    return {"id": eid, "sport_key": "basketball_ncaab", "sport_title": "NCAAB", "commence_time": commence,
            "home_team": home, "away_team": away}


def ou(player: str, line: float, over: int, under: int, link: bool = False) -> list[dict]:
    out = [{"name": "Over", "description": player, "price": over, "point": line},
           {"name": "Under", "description": player, "price": under, "point": line}]
    if link:
        for o in out:
            o["link"] = f"https://sportsbook.example/betslip?sel={player.replace(' ', '')}-{o['name']}-{line}"
    return out


def market(key: str, outcomes: list[dict], last_update: str, link: bool = False) -> dict:
    m = {"key": key, "last_update": last_update, "outcomes": outcomes}
    if link:
        m["link"] = f"https://sportsbook.example/market/{key}"
    return m


def book(key: str, title: str, markets: list[dict], link: bool = False) -> dict:
    b = {"key": key, "title": title, "markets": markets}
    if link:
        b["link"] = f"https://sportsbook.example/{key}/event"
    return b


def event_odds(ev: dict, books: list[dict]) -> dict:
    return {**ev, "bookmakers": books}


class FakeOdds:
    """Stands in for requests.get against The Odds API: serves the events listing, per-event market discovery (the
    keys each bookmaker of the event's odds payload carries; 1 credit) and per-event odds, charges the documented cost
    (unique markets returned x regions), reports usage headers, can fail on demand."""

    def __init__(self, events: list[dict], odds: dict | None = None, remaining: int = 20_000, fail: dict | None = None,
                 raise_exc: Exception | None = None):
        self.events, self.odds = list(events), dict(odds or {})
        self.remaining, self.used = remaining, 0
        self.fail = dict(fail or {})
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.raise_exc is not None:
            raise self.raise_exc
        path = url[len(OA.BASE):]
        if path in self.fail:
            return _Resp(self.fail[path], {"message": "fixture failure"}, self._hdr(0))
        if path == OA.PATHS["events"]:
            return _Resp(200, self.events, self._hdr(0))
        pre = OA.PATHS["event_odds"].split("{")[0]
        if path.startswith(pre) and path.endswith("/markets"):     # event markets: advertised keys, 1 credit
            eid = path[len(pre):-len("/markets")]
            ev = next((e for e in self.events if e.get("id") == eid), None)
            if ev is None and eid not in self.odds:
                return _Resp(404, {"message": "Event not found"}, self._hdr(0))
            src = self.odds.get(eid) or {**ev, "bookmakers": []}
            body = {k: src.get(k) for k in ("id", "sport_key", "sport_title", "commence_time", "home_team", "away_team")}
            body["bookmakers"] = [{"key": b["key"], "title": b.get("title"), "last_update": b.get("last_update"),
                                   "markets": [{"key": m["key"], "last_update": m.get("last_update")}
                                               for m in b.get("markets", [])]} for b in src.get("bookmakers", [])]
            return _Resp(200, body, self._hdr(1))
        eid = path[len(pre):-len("/odds")]
        if eid not in self.odds:
            return _Resp(404, {"message": "Event not found"}, self._hdr(0))
        body = json.loads(json.dumps(self.odds[eid]))
        want = set(params.get("markets", "").split(","))
        for b in body.get("bookmakers", []):
            b["markets"] = [m for m in b["markets"] if m["key"] in want]
        body["bookmakers"] = [b for b in body.get("bookmakers", []) if b["markets"]]
        cost = len({m["key"] for b in body["bookmakers"] for m in b["markets"]}) * len(params["regions"].split(","))
        return _Resp(200, body, self._hdr(cost))

    def _hdr(self, cost: int) -> dict:
        self.remaining -= cost
        self.used += cost
        return {"x-requests-remaining": str(self.remaining), "x-requests-used": str(self.used),
                "x-requests-last": str(cost)}
