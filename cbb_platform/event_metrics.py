"""Pure, unit-tested event-quality rules shared by discovery, the canonical build and the audits.

On-floor quality classes (Stage 2E). Evaluated in this order; the first failing rule wins:
    NO_ONFLOOR             the play carries no onFloor list (not a defect class: the feed is absent)
    UNRESOLVED_PLAYER      an onFloor entry has no athlete id (or, in the build, an id that resolves to no
                           known player)
    PLAYER_ON_BOTH_TEAMS   one athlete id listed for both teams
    DUPLICATE_PLAYER       one athlete id listed twice for the same team
    OTHER_INVALID          an entry whose team is neither the home nor the away team
    MISSING_HOME / MISSING_AWAY   no player listed for that side
    MORE_THAN_5            a side lists more than five players
    LESS_THAN_5            a side lists fewer than five players
    VALID_5V5              exactly five distinct players per side
The raw onFloor list is never modified; this only labels it.
"""
from __future__ import annotations

from collections import Counter

ONFLOOR_CLASSES = ("VALID_5V5", "MISSING_HOME", "MISSING_AWAY", "LESS_THAN_5", "MORE_THAN_5", "DUPLICATE_PLAYER",
                   "PLAYER_ON_BOTH_TEAMS", "UNRESOLVED_PLAYER", "OTHER_INVALID")


def game_sides(plays: list[dict]) -> tuple[str | None, str | None]:
    """(home team name, away team name) of one game's plays, from the plays that carry isHomeTeam."""
    home, away = Counter(), Counter()
    for p in plays:
        if p.get("isHomeTeam") is True:
            home[p.get("team")] += 1
            away[p.get("opponent")] += 1
        elif p.get("isHomeTeam") is False:
            away[p.get("team")] += 1
            home[p.get("opponent")] += 1
    h = home.most_common(1)[0][0] if home else None
    a = away.most_common(1)[0][0] if away else None
    return h, a


def onfloor_class(on_floor, home: str | None, away: str | None, known_ids: set | None = None) -> str:
    if not on_floor:
        return "NO_ONFLOOR"
    ids = [e.get("id") for e in on_floor]
    if any(i is None for i in ids) or (known_ids is not None and any(i not in known_ids for i in ids)):
        return "UNRESOLVED_PLAYER"
    teams_by_id: dict = {}
    for e in on_floor:
        teams_by_id.setdefault(e["id"], []).append(e.get("team"))
    if any(len(set(t)) > 1 for t in teams_by_id.values()):
        return "PLAYER_ON_BOTH_TEAMS"
    if any(len(t) > 1 for t in teams_by_id.values()):
        return "DUPLICATE_PLAYER"
    sides = Counter("H" if e.get("team") == home else "A" if e.get("team") == away else "X" for e in on_floor)
    if home is None or away is None or sides["X"]:
        return "OTHER_INVALID"
    if sides["H"] == 0:
        return "MISSING_HOME"
    if sides["A"] == 0:
        return "MISSING_AWAY"
    if sides["H"] > 5 or sides["A"] > 5:
        return "MORE_THAN_5"
    if sides["H"] < 5 or sides["A"] < 5:
        return "LESS_THAN_5"
    return "VALID_5V5"


def play_metrics(plays: list[dict]) -> dict:
    """Coverage metrics of ONE game's /plays payload (discovery + equivalence tests)."""
    home, away = game_sides(plays)
    cls = Counter(onfloor_class(p.get("onFloor"), home, away) for p in plays)
    shooting = [p for p in plays if p.get("shootingPlay")]
    with_shot = [p for p in shooting if p.get("shotInfo")]
    fg = [p for p in with_shot if (p["shotInfo"].get("range") or "") != "free_throw"]
    xy = [p for p in fg if ((p["shotInfo"].get("location") or {}).get("x") is not None
                            and (p["shotInfo"].get("location") or {}).get("y") is not None)]
    return {"plays": len(plays), "plays_with_onfloor": len(plays) - cls["NO_ONFLOOR"],
            "onfloor_valid_5v5": cls["VALID_5V5"],
            "onfloor_classes": {k: v for k, v in sorted(cls.items())},
            "shooting_plays": len(shooting), "shooting_plays_with_shotinfo": len(with_shot),
            "field_goal_shots": len(fg), "field_goal_shots_with_xy": len(xy),
            "plays_with_participants": sum(1 for p in plays if p.get("participants")),
            "plays_with_wallclock": sum(1 for p in plays if p.get("wallclock")),
            "periods": sorted({p.get("period") for p in plays if p.get("period") is not None}),
            "final_home_score": max((p.get("homeScore") or 0 for p in plays), default=None),
            "final_away_score": max((p.get("awayScore") or 0 for p in plays), default=None)}
