"""Clearly labeled CBBD-FORMAT FIXTURES for the 2026-27 production feed (synthetic; never live data).

Built on fixtures.synthetic_history (8 teams x 12 players, CBBD-anchored ids). The payloads mirror the CBBD response
shapes the production path reads (games, games/teams, games/players, stats/player/season, substitutions/game,
teams/roster, recruiting/players, recruiting/portal) and exercise every identity case:

    returning players          team i keeps players j = 0..10 under the same CBBD athlete id
    DEPARTED                   team 0 player 11: on the 2025-26 roster / box, absent from every 2026-27 roster
    TRANSFER_SAME_ID           team 1 player 11 -> team 0, same athlete id (portal resolves RESOLVED_ROSTER_CORROBORATED)
    TRANSFER_NEW_ID            team 2 player 11 -> team 3; team 3 lists him under a NEW athlete id and team 2 still lists
                               the old id (stale provider roster): portal identity = CANDIDATE_ORIGIN_ROSTER_NAME
    TRANSFER_UNRESOLVED        "Nobody Known" from team 4 (no origin roster match) -> team 5 roster under a new id
    FRESHMAN                   "Fresh Recruit": class of 2026, committed to team 0, NO CBBD athleteId; on team 0's 2026-27
                               roster (P-2 links him)
    UNLINKED_RECRUIT           "Missing Recruit": committed to team 0, on no roster
    WALK_ON                    "Walk On": team 0 roster, start season 2027, no recruiting / portal record
    NON_DI                     provider team 9099: empty 2026-27 roster
schedule (2026-27)
    D  team 0 vs team 2   2026-11-05T00:00Z   final in the AFTER payloads (box + substitutions supplied)
    A  team 0 vs team 1   2026-11-11T00:30Z   B team 2 vs team 3, C team 4 vs team 5 (same tip)
    T  team 6 vs team 7   2026-11-12, startTimeTbd
    N  team 7 vs non-D-I  2026-11-11T00:30Z
    X  team 6 vs team 5   2026-11-11T00:30Z   status canceled
"""
from __future__ import annotations

import json

import pandas as pd

from cbb_platform.adapters import cbbd as CBBD_ADAPTER
from cbb_platform.prod import cbbd_acquire as A, config as PC, fixtures as FX

LABEL = "FIXTURE (synthetic CBBD-format 2026-27 payloads; NOT live)"
NONDI = 9099
GAMES = {"D": (7001, 0, 2, "2026-11-05T00:00:00.000Z", False), "A": (7002, 0, 1, "2026-11-11T00:30:00.000Z", False),
         "B": (7003, 2, 3, "2026-11-11T00:30:00.000Z", False), "C": (7004, 4, 5, "2026-11-11T00:30:00.000Z", False),
         "T": (7005, 6, 7, "2026-11-12T05:00:00.000Z", True), "N": (7006, 7, None, "2026-11-11T00:30:00.000Z", False),
         "X": (7007, 6, 5, "2026-11-11T00:30:00.000Z", False)}
NEW_IDS = {"TRANSFER_NEW_ID": 900001, "TRANSFER_UNRESOLVED": 900002, "FRESHMAN": 800001, "WALK_ON": 800002}
NAMES = FX.__dict__.get("NAMES", ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"])


def team_id(i: int) -> int:
    return FX.TEAM_BASE + 1 + i


def gid(tag: str) -> str:
    return FX.cid("G", "game", GAMES[tag][0])


def pid(i: int, j: int) -> str:
    return FX.cid("P", "athlete", FX.athlete_id(i, j))


def _player(aid, first, last, start=2025, pos="Guard"):
    return {"id": aid, "sourceId": f"fx{aid}", "name": f"{first} {last}", "firstName": first, "lastName": last,
            "jersey": str(aid % 100), "position": pos, "height": 78, "weight": 200,
            "hometown": {"city": None, "state": None, "country": None, "latitude": None, "longitude": None, "countyFips": None},
            "dateOfBirth": None, "startSeason": start, "endSeason": 2028}


def rosters() -> list:
    out = []
    for i in range(8):
        pl = [_player(FX.athlete_id(i, j), NAMES[i], f"Player{j:02d}") for j in range(11)]
        if i == 2:
            pl.append(_player(FX.athlete_id(2, 11), "Charlie", "Player11"))             # stale origin listing
        if i == 0:
            pl.append(_player(FX.athlete_id(1, 11), "Bravo", "Player11"))               # TRANSFER_SAME_ID
            pl.append(_player(NEW_IDS["FRESHMAN"], "Fresh", "Recruit", start=2027))
            pl.append(_player(NEW_IDS["WALK_ON"], "Walk", "On", start=2027))
        if i == 3:
            pl.append(_player(NEW_IDS["TRANSFER_NEW_ID"], "Charlie", "Player11", start=2027))
        if i == 5:
            pl.append(_player(NEW_IDS["TRANSFER_UNRESOLVED"], "Nobody", "Known", start=2027))
        out.append({"teamId": team_id(i), "teamSourceId": f"fx{team_id(i)}", "team": f"Fixture {NAMES[i]}",
                    "conference": "Fixture Conference", "season": 2027, "players": pl})
    out.append({"teamId": NONDI, "teamSourceId": "fx9099", "team": "Fixture Nondi", "conference": None, "season": 2027,
                "players": []})
    return out


def recruits() -> list:
    base = {"position": "SF", "schoolId": 1, "school": "Fixture Prep", "athleteId": None, "year": 2026,
            "hometown": {"city": None, "state": None, "country": None}}
    return [{**base, "id": 5001, "sourceId": "r5001", "name": "Fresh Recruit", "heightInches": 80, "weightPounds": 210,
             "stars": 4, "rating": 0.95, "ranking": 50, "committedTo": {"id": team_id(0), "name": "Fixture Alpha",
                                                                         "conference": "Fixture Conference"}},
            {**base, "id": 5002, "sourceId": "r5002", "name": "Missing Recruit", "heightInches": 77, "weightPounds": 190,
             "stars": 3, "rating": 0.88, "ranking": 140, "committedTo": {"id": team_id(0), "name": "Fixture Alpha",
                                                                          "conference": "Fixture Conference"}}]


def portal() -> list:
    def t(i, first, last, o, d):
        return {"id": i, "sourceId": f"t{i}", "year": 2026, "firstName": first, "lastName": last, "position": "G",
                "origin": {"id": team_id(o), "name": f"Fixture {NAMES[o]}", "conference": "Fixture Conference"},
                "destination": {"id": team_id(d), "name": f"Fixture {NAMES[d]}", "conference": "Fixture Conference"},
                "stars": 3, "rating": 0.85, "eligibility": "Immediate", "yearsRemaining": 2}
    return [t(6001, "Bravo", "Player11", 1, 0), t(6002, "Charlie", "Player11", 2, 3), t(6003, "Nobody", "Known", 4, 5)]


def _game(tag, status="scheduled", hp=None, ap=None):
    gid_, h, a, start, tbd = GAMES[tag]
    away = (team_id(a) if a is not None else NONDI)
    return {"id": gid_, "sourceId": f"g{gid_}", "seasonLabel": "20262027", "season": 2027, "seasonType": "regular",
            "tournament": None, "startDate": start, "startTimeTbd": tbd, "neutralSite": False, "conferenceGame": False,
            "gameType": "STD", "status": "canceled" if tag == "X" else status, "gameNotes": None, "attendance": None,
            "homeTeamId": team_id(h), "homeTeam": f"Fixture {NAMES[h]}", "homeConferenceId": 900,
            "homeConference": "Fixture Conference", "homeSeed": None, "homePoints": hp, "homePeriodPoints": None,
            "homeWinner": None, "awayTeamId": away, "awayTeam": f"Fixture {NAMES[a]}" if a is not None else "Fixture Nondi",
            "awayConferenceId": 900 if a is not None else None, "awayConference": "Fixture Conference" if a is not None else None,
            "awaySeed": None, "awayPoints": ap, "awayPeriodPoints": None, "awayWinner": None, "venueId": 77, "venue": "Fixture Arena",
            "city": "Fixture City", "state": "FX"}


# ----------------------------------------------------------------------------- completed game D (box + subs)
def _line(i, j, mins, starter):
    pts2, pts3, ft = (4, 1, 2) if mins else (0, 0, 0)
    return {"athleteId": FX.athlete_id(i, j), "athleteSourceId": f"fx{FX.athlete_id(i, j)}", "name": f"{NAMES[i]} Player{j:02d}",
            "position": "G", "starter": starter, "ejected": False, "minutes": mins, "points": 2 * pts2 + 3 * pts3 + ft,
            "turnovers": 1 if mins else 0, "fouls": 2 if mins else 0, "assists": 3 if mins else 0, "steals": 1 if mins else 0,
            "blocks": 0, "gameScore": 0, "offensiveRating": 0, "defensiveRating": 0, "netRating": 0, "usage": 0,
            "effectiveFieldGoalPct": 0, "trueShootingPct": 0, "assistsTurnoverRatio": 0, "freeThrowRate": 0, "offensiveReboundPct": 0,
            "fieldGoals": {"made": pts2 + pts3, "attempted": 2 * (pts2 + pts3), "pct": 50},
            "twoPointFieldGoals": {"made": pts2, "attempted": 2 * pts2, "pct": 50},
            "threePointFieldGoals": {"made": pts3, "attempted": 2 * pts3, "pct": 50},
            "freeThrows": {"made": ft, "attempted": ft, "pct": 100},
            "rebounds": {"offensive": 1 if mins else 0, "defensive": 3 if mins else 0, "total": 4 if mins else 0}}


def _team_lines(i):
    """five starters play all 40 minutes; players 5..10 are on the bench with no stint (DNP_CONFIRMED)"""
    return [_line(i, j, 40, True) for j in range(5)] + [_line(i, j, 0, False) for j in range(5, 11)]


def _sum(lines, k, sub=None):
    return sum((l[k][sub] if sub else l[k]) for l in lines)


def box_payloads(tag: str = "D") -> dict:
    gid_, h, a, start, _ = GAMES[tag]
    head = {"gameId": gid_, "season": 2027, "seasonLabel": "20262027", "seasonType": "regular", "tournament": None,
            "startDate": start, "startTimeTbd": False, "conferenceGame": False, "neutralSite": False, "gameType": "STD",
            "notes": None, "gameMinutes": 40, "gamePace": 70}
    players, teams, subs, season = [], [], [], []
    for i, o, home in ((h, a, True), (a, h, False)):
        lines = _team_lines(i)
        players.append({**head, "isHome": home, "teamId": team_id(i), "team": f"Fixture {NAMES[i]}", "conference": "Fixture Conference",
                        "teamSeed": None, "opponentId": team_id(o), "opponent": f"Fixture {NAMES[o]}",
                        "opponentConference": "Fixture Conference", "opponentSeed": None, "players": lines})
        ts = {"possessions": 70, "assists": _sum(lines, "assists"), "steals": _sum(lines, "steals"), "blocks": 0,
              "trueShooting": 0, "rating": 0, "gameScore": 0,
              "points": {"total": _sum(lines, "points"), "byPeriod": [_sum(lines, "points") // 2, _sum(lines, "points") - _sum(lines, "points") // 2],
                         "largestLead": 5, "fastBreak": 0, "inPaint": 0, "offTurnovers": 0},
              "twoPointFieldGoals": {"made": _sum(lines, "twoPointFieldGoals", "made"), "attempted": _sum(lines, "twoPointFieldGoals", "attempted"), "pct": 50},
              "threePointFieldGoals": {"made": _sum(lines, "threePointFieldGoals", "made"), "attempted": _sum(lines, "threePointFieldGoals", "attempted"), "pct": 50},
              "freeThrows": {"made": _sum(lines, "freeThrows", "made"), "attempted": _sum(lines, "freeThrows", "attempted"), "pct": 100},
              "fieldGoals": {"made": _sum(lines, "fieldGoals", "made"), "attempted": _sum(lines, "fieldGoals", "attempted"), "pct": 50},
              "turnovers": {"total": _sum(lines, "turnovers"), "teamTotal": 0},
              "rebounds": {"offensive": _sum(lines, "rebounds", "offensive"), "defensive": _sum(lines, "rebounds", "defensive"),
                           "total": _sum(lines, "rebounds", "total")},
              "fouls": {"total": _sum(lines, "fouls"), "technical": 0, "flagrant": 0},
              "fourFactors": {"effectiveFieldGoalPct": 0, "freeThrowRate": 0, "turnoverRatio": 0, "offensiveReboundPct": 0}}
        teams.append({k: v for k, v in head.items() if k not in ("gamePace",)} | {
            "teamId": team_id(i), "team": f"Fixture {NAMES[i]}", "conference": "Fixture Conference", "teamSeed": None,
            "opponentId": team_id(o), "opponent": f"Fixture {NAMES[o]}", "opponentConference": "Fixture Conference",
            "opponentSeed": None, "isHome": home, "pace": 70, "teamStats": ts, "opponentStats": ts})
        for l in lines:
            season.append({"season": 2027, "seasonLabel": "20262027", "teamId": team_id(i), "team": f"Fixture {NAMES[i]}",
                           "conference": "Fixture Conference", "athleteId": l["athleteId"], "athleteSourceId": l["athleteSourceId"],
                           "name": l["name"], "position": "G", "games": 1, "starts": int(l["starter"]), "minutes": l["minutes"]})
            if l["minutes"]:
                subs.append({"gameId": gid_, "startDate": start, "teamId": team_id(i), "team": f"Fixture {NAMES[i]}",
                             "conference": "Fixture Conference", "athleteId": l["athleteId"], "athlete": l["name"],
                             "position": "G", "opponentId": team_id(o), "opponent": f"Fixture {NAMES[o]}",
                             "opponentConference": "Fixture Conference",
                             "subIn": {"period": 1, "secondsRemaining": 1200, "teamPoints": 0, "opponentPoints": 0},
                             "subOut": {"period": 2, "secondsRemaining": 0, "teamPoints": 0, "opponentPoints": 0}})
    hp, ap = teams[0]["teamStats"]["points"]["total"], teams[1]["teamStats"]["points"]["total"]
    return {"players": players, "teams": teams, "season": season, "subs": subs, "hp": hp, "ap": ap}


def payloads(stage: str = "before") -> dict:
    """request_key -> body. stage 'before': every game scheduled. 'after': game D final + its box payloads."""
    from cbb_platform.prod import sync as Y
    out = {A.request_key("teams/roster", {"season": 2027}): rosters(),
           A.request_key("recruiting/players", {"year": 2026}): recruits(),
           A.request_key("recruiting/portal", {"year": 2026}): portal()}
    bx = box_payloads()
    games = [_game(t) for t in GAMES]
    if stage == "after":
        games[0] = _game("D", "final", bx["hp"], bx["ap"])
    for ep, p in Y.plan_schedule():
        out[A.request_key(ep, p)] = games if p["startDateRange"] == "2026-11-01" else []
    if stage == "after":
        for ep in ("games/teams", "games/players"):
            out[A.request_key(ep, {"season": 2027, "startDateRange": "2026-11-04", "endDateRange": "2026-11-06"})] = \
                bx["teams"] if ep == "games/teams" else bx["players"]
        out[A.request_key("stats/player/season", {"season": 2027})] = bx["season"]
        out[A.request_key("substitutions/game", {"gameId": GAMES["D"][0]})] = bx["subs"]
    return out


class _Resp:
    def __init__(self, status: int, body=None, headers=None):
        self.status_code = status
        self.content = json.dumps(body).encode() if body is not None else b"{}"
        self.headers = headers or {"X-CallLimit-Remaining": "50000"}


class FakeCBBD:
    """Stands in for requests.get: serves `bodies` (request_key -> JSON), counts calls, can fail on demand. It never
    sees a real credential (tests pass a dummy credential provider)."""

    def __init__(self, bodies: dict, fail: dict | None = None):
        self.bodies, self.fail = dict(bodies), dict(fail or {})
        self.calls: list[str] = []
        self.headers_seen: list[dict] = []

    def _key(self, url: str, params: dict) -> str:
        path = url[len(A.BASE):] if url.startswith(A.BASE) else url
        for ep, tmpl in CBBD_ADAPTER.ENDPOINTS.items():
            if "{" in tmpl:
                pre = tmpl.split("{")[0]
                if path.startswith(pre) and path[len(pre):].isdigit():
                    return A.request_key(ep, {**params, "gameId": int(path[len(pre):])})
            elif path == tmpl:
                return A.request_key(ep, params)
        raise KeyError(path)

    def __call__(self, url, params=None, headers=None, timeout=None):
        key = self._key(url, dict(params or {}))
        self.calls.append(key)
        self.headers_seen.append(dict(headers or {}))
        if key in self.fail:
            st = self.fail[key]
            return _Resp(st, {"error": "fixture failure"}, {"Retry-After": "0"})
        if key not in self.bodies:
            return _Resp(404, {"error": "not found"})
        return _Resp(200, self.bodies[key])


DUMMY_CREDENTIAL = "fixture-not-a-real-key"


def clock_at(ts):
    t = pd.Timestamp(ts)
    return lambda: t
