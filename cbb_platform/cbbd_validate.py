"""CBBD live-smoke validation: pure structural checks + measured SMOKE SAMPLE metrics.

Pure functions (unit tested on synthetic inputs):
    onfloor_state(onFloor)            -> VALID_5V5 | EMPTY | PARTIAL | MALFORMED
    lineup_is_valid(lineup)           -> five unique non-null athletes and idHash = sorted ids
    elapsed_seconds(period, secs_rem) -> game clock -> elapsed seconds (20-min halves, 5-min OTs)
    parse_substitutions(rows)         -> stints with elapsed in/out times
    floor_from_substitutions(...)     -> who the substitution feed says was on the floor
    compare_primitives(api, ours)     -> field-by-field equality for the date-bounded PIT test

`measure(payloads, con)` produces every SMOKE SAMPLE metric. Percentages describe this bounded
sample only and must never be generalized to whole seasons.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

import duckdb

HALF, OT = 1200, 300


# ----------------------------------------------------------------------------- pure structure checks
def onfloor_state(on_floor) -> str:
    if not on_floor:
        return "EMPTY"
    ids = [p.get("id") for p in on_floor]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        return "MALFORMED"
    by_team = Counter(p.get("team") for p in on_floor)
    if None in by_team or len(by_team) > 2 or any(n > 5 for n in by_team.values()):
        return "MALFORMED"
    return "VALID_5V5" if len(by_team) == 2 and all(n == 5 for n in by_team.values()) else "PARTIAL"


def lineup_is_valid(lineup: dict) -> bool:
    ids = [a.get("id") for a in lineup.get("athletes") or []]
    if len(ids) != 5 or any(i is None for i in ids) or len(set(ids)) != 5:
        return False
    return lineup.get("idHash") in (None, "-".join(str(i) for i in sorted(ids)))


def elapsed_seconds(period: int, seconds_remaining: float) -> float:
    if period <= 2:
        return (period - 1) * HALF + (HALF - seconds_remaining)
    return 2 * HALF + (period - 3) * OT + (OT - seconds_remaining)


def parse_substitutions(rows: list[dict]) -> list[dict]:
    """One stint per CBBD row: who, which team, elapsed in/out. Incomplete timing is kept and flagged."""
    out = []
    for r in rows:
        i, o = r.get("subIn") or {}, r.get("subOut") or {}
        complete = None not in (i.get("period"), i.get("secondsRemaining"), o.get("period"), o.get("secondsRemaining"))
        out.append({"game_id": r.get("gameId"), "team_id": r.get("teamId"), "team": r.get("team"),
                    "athlete_id": r.get("athleteId"), "timing_complete": complete,
                    "in": elapsed_seconds(i["period"], i["secondsRemaining"]) if complete else None,
                    "out": elapsed_seconds(o["period"], o["secondsRemaining"]) if complete else None})
    return out


def floor_from_substitutions(stints: list[dict], team: str, elapsed: float) -> set:
    """Athletes of `team` whose stint covers `elapsed` (in < t <= out: a play at the exact moment of
    a substitution is attributed to the players who were on the floor during the preceding time)."""
    return {s["athlete_id"] for s in stints
            if s["team"] == team and s["timing_complete"] and s["in"] < elapsed <= s["out"]}


def compare_primitives(api: dict, ours: dict) -> dict:
    """{field: [api, ours]} for every field present in both; `match` is True only if all are equal."""
    fields = {k: [api[k], ours.get(k)] for k in api if k in ours}
    return {"fields": fields, "mismatched": sorted(k for k, (a, b) in fields.items() if a != b),
            "match": all(a == b for a, b in fields.values())}


# ----------------------------------------------------------------------------- helpers
def _share(n, of):
    return {"n": n, "of": of, "share": round(n / of, 4) if of else None}


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _cid(con, prefix, entity, pid):
    return con.execute("SELECT cid(?, ?, ?)", [prefix, entity, str(pid)]).fetchone()[0]


def _known_players(con, ids) -> set:
    ids = sorted({i for i in ids if i is not None})
    if not ids:
        return set()
    rows = con.execute("""SELECT x.id FROM unnest(?) AS x(id)
                          JOIN core.players p ON p.canonical_player_id = cid('P', 'athlete', CAST(x.id AS VARCHAR))""",
                       [ids]).fetchall()
    return {r[0] for r in rows}


PLAYER_BOX = {"points": ("points",), "minutes": ("minutes",), "fgm": ("fieldGoals", "made"),
              "fga": ("fieldGoals", "attempted"), "two_pm": ("twoPointFieldGoals", "made"),
              "two_pa": ("twoPointFieldGoals", "attempted"), "three_pm": ("threePointFieldGoals", "made"),
              "three_pa": ("threePointFieldGoals", "attempted"), "ftm": ("freeThrows", "made"),
              "fta": ("freeThrows", "attempted"), "orb": ("rebounds", "offensive"), "drb": ("rebounds", "defensive"),
              "reb": ("rebounds", "total"), "ast": ("assists",), "stl": ("steals",), "blk": ("blocks",),
              "tov": ("turnovers",), "pf": ("fouls",)}
TEAM_BOX = {"points": ("points", "total"), "fgm": ("fieldGoals", "made"), "fga": ("fieldGoals", "attempted"),
            "two_pm": ("twoPointFieldGoals", "made"), "two_pa": ("twoPointFieldGoals", "attempted"),
            "three_pm": ("threePointFieldGoals", "made"), "three_pa": ("threePointFieldGoals", "attempted"),
            "ftm": ("freeThrows", "made"), "fta": ("freeThrows", "attempted"), "orb": ("rebounds", "offensive"),
            "drb": ("rebounds", "defensive"), "reb": ("rebounds", "total"), "ast": ("assists",),
            "stl": ("steals",), "blk": ("blocks",), "tov_total": ("turnovers", "total"), "pf": ("fouls", "total")}


def dig(d: dict, path: tuple):
    for k in path:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return d


# ----------------------------------------------------------------------------- measured smoke metrics
def measure(payloads: dict, con: duckdb.DuckDBPyConnection) -> dict:
    games = {g["id"]: g for p in payloads.get("games", []) for g in p}
    box = [b for p in payloads.get("games/players", []) for b in p]
    teams = [t for p in payloads.get("games/teams", []) for t in p]
    rosters = [t for p in payloads.get("teams/roster", []) for t in p]
    box_ids_by_game = defaultdict(set)
    for b in box:
        for pl in b.get("players") or []:
            box_ids_by_game[b["gameId"]].add(pl.get("athleteId"))
    out = {"label": "SMOKE SAMPLE — bounded; never generalize to whole seasons"}

    # ---- GAME DATA
    g_known = tip_ok = score_ok = team_join = 0
    for gid, g in games.items():
        row = con.execute("""SELECT g.scheduled_tip_utc, g.home_points, g.away_points, g.home_team_id, g.away_team_id
            FROM metadata.game_id_map m JOIN core.games g USING (canonical_game_id)
            WHERE m.provider = 'CBBD' AND m.provider_game_key = ?""", [str(gid)]).fetchone()
        if not row:
            continue
        g_known += 1
        tip_ok += row[0] == _ts(g["startDate"])
        score_ok += (row[1], row[2]) == (g.get("homePoints"), g.get("awayPoints"))
        team_join += (row[3], row[4]) == (_cid(con, "T", "team", g["homeTeamId"]), _cid(con, "T", "team", g["awayTeamId"]))
    out["game_data"] = {"games_returned": len(games), "game_ids": sorted(games), "seasons": sorted({g["season"] for g in games.values()}),
                        "status_final": _share(sum(g.get("status") == "final" for g in games.values()), len(games)),
                        "game_id_joins_starter_pack": _share(g_known, len(games)),
                        "team_identity_joins": _share(team_join, g_known),
                        "tip_time_reconciliation_vs_starter_pack": _share(tip_ok, g_known),
                        "final_score_reconciliation_vs_starter_pack": _share(score_ok, g_known)}

    # ---- PLAYER BOX
    rows = [(b, pl) for b in box for pl in b.get("players") or []]
    known = _known_players(con, [pl.get("athleteId") for _, pl in rows])
    pts_identity = sum(1 for _, pl in rows if None not in (pl.get("points"), dig(pl, ("twoPointFieldGoals", "made")),
                       dig(pl, ("threePointFieldGoals", "made")), dig(pl, ("freeThrows", "made")))
                       and pl["points"] == 2 * dig(pl, ("twoPointFieldGoals", "made")) + 3 * dig(pl, ("threePointFieldGoals", "made"))
                       + dig(pl, ("freeThrows", "made")))
    team_pts = {(t["gameId"], t["teamId"]): dig(t, ("teamStats", "points", "total")) for t in teams}
    sum_ok = sum_n = min_ok = 0
    sp_match = sp_n = 0
    for b in box:
        pls = b.get("players") or []
        if (b["gameId"], b["teamId"]) in team_pts:
            sum_n += 1
            sum_ok += sum(p.get("points") or 0 for p in pls) == team_pts[(b["gameId"], b["teamId"])]
        min_ok += abs(sum(p.get("minutes") or 0 for p in pls) - 5 * (b.get("gameMinutes") or 40)) <= 5
        for pl in pls:   # same player-game line in the Starter Pack (resolved by persistent id)
            r = con.execute("""SELECT points, minutes, fga, reb FROM fact.player_games
                WHERE canonical_game_id = cid('G', 'game', ?) AND canonical_player_id = cid('P', 'athlete', ?)""",
                            [str(b["gameId"]), str(pl.get("athleteId"))]).fetchone()
            if r:
                sp_n += 1
                sp_match += r == (pl.get("points"), pl.get("minutes"), dig(pl, ("fieldGoals", "attempted")), dig(pl, ("rebounds", "total")))
    out["player_box"] = {
        "team_game_rows": len(box), "player_rows": len(rows),
        "athleteId_joins_persistent_player": _share(sum(pl.get("athleteId") in known for _, pl in rows), len(rows)),
        "starter_coverage": _share(sum(pl.get("starter") is not None for _, pl in rows), len(rows)),
        "minutes_coverage": _share(sum(pl.get("minutes") is not None for _, pl in rows), len(rows)),
        "counting_stat_coverage": _share(sum(all(dig(pl, p) is not None for p in PLAYER_BOX.values()) for _, pl in rows), len(rows)),
        "player_pts_identity": _share(pts_identity, len(rows)),
        "sum_player_points_equals_team_points": _share(sum_ok, sum_n),
        "sum_player_minutes_within_5_of_5x_game_minutes": _share(min_ok, len(box)),
        "line_equals_starter_pack_line(points,minutes,fga,reb)": _share(sp_match, sp_n)}

    # ---- TEAM BOX
    ff = ("effectiveFieldGoalPct", "freeThrowRate", "turnoverRatio", "offensiveReboundPct")
    tb_match = tb_n = 0
    for t in teams:
        r = con.execute(f"""SELECT {", ".join(TEAM_BOX)} FROM fact.team_games
            WHERE canonical_game_id = cid('G', 'game', ?) AND canonical_team_id = cid('T', 'team', ?)""",
                        [str(t["gameId"]), str(t["teamId"])]).fetchone()
        if r:
            tb_n += 1
            tb_match += list(r) == [dig(t["teamStats"], p) for p in TEAM_BOX.values()]
    out["team_box"] = {
        "team_rows": len(teams),
        "possessions_coverage": _share(sum(dig(t, ("teamStats", "possessions")) is not None for t in teams), len(teams)),
        "pace_coverage": _share(sum(t.get("pace") is not None for t in teams), len(teams)),
        "four_factor_coverage_by_field": {f: _share(sum(dig(t, ("teamStats", "fourFactors", f)) is not None for t in teams),
                                                    len(teams)) for f in ff},
        "points_equal_games_final_score": _share(sum(
            dig(t, ("teamStats", "points", "total")) == (games.get(t["gameId"], {}).get("homePoints" if t.get("isHome") else "awayPoints"))
            for t in teams if t["gameId"] in games), sum(t["gameId"] in games for t in teams)),
        "all_17_primitives_equal_starter_pack": _share(tb_match, tb_n)}

    # ---- PBP / ONFLOOR / SHOTINFO / SUBSTITUTIONS / LINEUPS (per requested game)
    plays_by_game = payloads.get("plays/game", {})
    subs_by_game = payloads.get("substitutions/game", {})
    lineups_by_game = payloads.get("lineups/game", {})
    requested = sorted(set(plays_by_game) | set(subs_by_game) | set(lineups_by_game))
    pbp, onf, shot, subs_out, lu_out = {}, {}, {}, {}, {}
    for gid in requested:
        g = con.execute("""SELECT g.season, g.scheduled_tip_utc, g.home_points, g.away_points, g.overtime_count
                           FROM core.games g WHERE canonical_game_id = cid('G', 'game', ?)""", [str(gid)]).fetchone()
        season, tip, hp, ap, ot = g
        plays = plays_by_game.get(gid, [])
        n = len(plays)
        last = max(plays, key=lambda p: (p["period"], -p["secondsRemaining"], int(p["id"]))) if plays else None
        wall = [_ts(p["wallclock"]) for p in plays if p.get("wallclock")]
        periods = sorted({p.get("period") for p in plays})
        pbp[gid] = {
            "season": season, "plays": n,
            "final_score_matches_game": (last["homeScore"], last["awayScore"]) == (hp, ap) if last else None,
            "period_clock_complete": _share(sum(None not in (p.get("period"), p.get("clock"), p.get("secondsRemaining"))
                                                for p in plays), n),
            "periods_present": periods, "periods_expected": list(range(1, 3 + (ot or 0))),
            "wallclock_present": _share(len(wall), n),
            "first_wallclock_minus_tip_hours": round((min(wall) - tip).total_seconds() / 3600, 2) if wall and tip else None,
            "wallclock_span_minutes": round((max(wall) - min(wall)).total_seconds() / 60, 1) if wall else None,
        }
        states = Counter(onfloor_state(p.get("onFloor")) for p in plays)
        ids = [x.get("id") for p in plays for x in p.get("onFloor") or []]
        kp = _known_players(con, ids)
        onf[gid] = {"plays": n, "states": dict(states), "valid_5v5": _share(states["VALID_5V5"], n),
                    "onFloor_player_ids_join_persistent_player": _share(sum(i in kp for i in ids), len(ids))}
        shooting = [p for p in plays if p.get("shootingPlay")]
        si = [p["shotInfo"] for p in shooting if p.get("shotInfo")]
        assisted = [s for s in si if s.get("assisted")]
        shot[gid] = {"shooting_plays": len(shooting), "with_shotInfo": _share(len(si), len(shooting)),
                     "shooter_id": _share(sum(dig(s, ("shooter", "id")) is not None for s in si), len(si)),
                     "assisted_shots": len(assisted),
                     "assister_id_when_assisted": _share(sum(dig(s, ("assistedBy", "id")) is not None for s in assisted), len(assisted)),
                     "range": _share(sum(s.get("range") is not None for s in si), len(si)),
                     "range_values": dict(Counter(s.get("range") for s in si)),
                     "location_xy": _share(sum(dig(s, ("location", "x")) is not None and dig(s, ("location", "y")) is not None
                                               for s in si), len(si))}
        stints = parse_substitutions(subs_by_game.get(gid, []))
        sk = _known_players(con, [s["athlete_id"] for s in stints])
        agree = checked = 0
        for p in plays:
            if onfloor_state(p.get("onFloor")) != "VALID_5V5":
                continue
            e = elapsed_seconds(p["period"], p["secondsRemaining"])
            for team in {x["team"] for x in p["onFloor"]}:
                checked += 1
                agree += floor_from_substitutions(stints, team, e) == {x["id"] for x in p["onFloor"] if x["team"] == team}
        subs_out[gid] = {"rows": len(stints), "teams": len({s["team_id"] for s in stints}),
                         "athlete_ids_join_persistent_player": _share(sum(s["athlete_id"] in sk for s in stints), len(stints)),
                         "timing_complete": _share(sum(s["timing_complete"] for s in stints), len(stints)),
                         "team_play_floor_sets_equal_onFloor": _share(agree, checked)}
        lus = lineups_by_game.get(gid, [])
        lk = _known_players(con, [a.get("id") for u in lus for a in u.get("athletes") or []])
        secs = defaultdict(float)
        pts = defaultdict(int)
        for u in lus:
            secs[u["teamId"]] += u.get("totalSeconds") or 0
            pts[u["teamId"]] += dig(u, ("teamStats", "points")) or 0
        game_secs = 2 * HALF + OT * (ot or 0)
        finals = dict(con.execute("""SELECT x.tid, x.pts FROM core.games g, (VALUES (g.home_team_id, g.home_points),
                                     (g.away_team_id, g.away_points)) x(tid, pts)
                                     WHERE g.canonical_game_id = cid('G', 'game', ?)""", [str(gid)]).fetchall())
        reconciles = {str(k): secs[k] == game_secs and pts[k] == finals.get(_cid(con, "T", "team", k)) for k in secs}
        lu_out[gid] = {"units": len(lus), "teams": len(secs), "valid_five_player_units": _share(sum(map(lineup_is_valid, lus)), len(lus)),
                       "athlete_ids_join_persistent_player": _share(sum(a.get("id") in lk for u in lus for a in u.get("athletes") or []),
                                                                    sum(len(u.get("athletes") or []) for u in lus)),
                       "seconds_coverage": _share(sum(u.get("totalSeconds") is not None for u in lus), len(lus)),
                       "possessions_coverage": _share(sum(dig(u, ("teamStats", "possessions")) is not None for u in lus), len(lus)),
                       "team_seconds_vs_game_seconds": {str(k): [round(v, 1), game_secs] for k, v in secs.items()},
                       "team_points_vs_final": {str(k): [v, finals.get(_cid(con, "T", "team", k))] for k, v in pts.items()},
                       "team_units_reconcile_seconds_and_points": reconciles}
    out["pbp"], out["onfloor"], out["shotinfo"], out["substitutions"], out["lineups"] = pbp, onf, shot, subs_out, lu_out

    # ---- ROSTERS
    box_by_name = {(b["teamId"], b["season"], pl.get("name")): pl.get("athleteId") for b in box for pl in b.get("players") or []}
    r_players = [(t, p) for t in rosters for p in t.get("players") or []]
    rk = _known_players(con, [p.get("id") for _, p in r_players])
    both = [(t, p) for t, p in r_players if (t["teamId"], int(t["season"]), p.get("name")) in box_by_name]
    out["rosters"] = {
        "team_seasons": [{"team": t["team"], "season": t["season"], "players": len(t.get("players") or [])} for t in rosters],
        "roster_player_rows": len(r_players),
        "roster_ids_join_persistent_player": _share(sum(p.get("id") in rk for _, p in r_players), len(r_players)),
        "roster_id_equals_box_athleteId_same_name": _share(sum(box_by_name[(t["teamId"], int(t["season"]), p["name"])] == p["id"]
                                                               for t, p in both), len(both)),
        "team_season_maps_to_core_team_seasons": _share(sum(con.execute(
            "SELECT count(*) FROM core.team_seasons WHERE canonical_team_id = cid('T', 'team', ?) AND season = ?",
            [str(t["teamId"]), int(t["season"])]).fetchone()[0] == 1 for t in rosters), len(rosters))}
    return out


# ----------------------------------------------------------------------------- date-bounded PIT test
INTERPRETATIONS = {
    "tip_utc_le_end_midnight_utc": "g.scheduled_tip_utc >= CAST($lo AS TIMESTAMPTZ) AND g.scheduled_tip_utc <= CAST($hi AS TIMESTAMPTZ)",
    # name: SQL predicate on core.games g given $lo / $hi (DATE)
    "tip_utc_lt_end_midnight_utc": "g.scheduled_tip_utc >= CAST($lo AS TIMESTAMPTZ) AND g.scheduled_tip_utc < CAST($hi AS TIMESTAMPTZ)",
    "tip_utc_le_end_day_utc": "g.scheduled_tip_utc >= CAST($lo AS TIMESTAMPTZ) AND g.scheduled_tip_utc < CAST($hi AS TIMESTAMPTZ) + INTERVAL 1 DAY",
    "et_date_le_end": "g.game_date_et >= CAST($lo AS DATE) AND g.game_date_et <= CAST($hi AS DATE)",
}
P_FIELDS = {"games": None, "minutes": ("minutes",), "points": ("points",), "fgm": ("fieldGoals", "made"),
            "fga": ("fieldGoals", "attempted"), "three_pm": ("threePointFieldGoals", "made"),
            "three_pa": ("threePointFieldGoals", "attempted"), "ftm": ("freeThrows", "made"),
            "fta": ("freeThrows", "attempted"), "orb": ("rebounds", "offensive"), "drb": ("rebounds", "defensive"),
            "reb": ("rebounds", "total"), "ast": ("assists",), "stl": ("steals",), "blk": ("blocks",),
            "tov": ("turnovers",), "pf": ("fouls",)}
T_FIELDS = {"games": None, "points": ("teamStats", "points", "total"), "fgm": ("teamStats", "fieldGoals", "made"),
            "fga": ("teamStats", "fieldGoals", "attempted"), "three_pm": ("teamStats", "threePointFieldGoals", "made"),
            "three_pa": ("teamStats", "threePointFieldGoals", "attempted"), "ftm": ("teamStats", "freeThrows", "made"),
            "fta": ("teamStats", "freeThrows", "attempted"), "orb": ("teamStats", "rebounds", "offensive"),
            "drb": ("teamStats", "rebounds", "defensive"), "reb": ("teamStats", "rebounds", "total"),
            "ast": ("teamStats", "assists"), "stl": ("teamStats", "steals"), "blk": ("teamStats", "blocks"),
            "tov_total": ("teamStats", "turnovers", "total"), "pf": ("teamStats", "fouls", "total")}


def date_bounded_pit(payloads: dict, metas: dict, con: duckdb.DuckDBPyConnection) -> dict:
    """Compare CBBD date-bounded season stats with canonical game-level aggregates under each candidate
    date-bound interpretation. VALIDATED only if ONE interpretation reconciles every tested field for
    every tested window, for both the player and the team endpoint."""
    results = {name: {"player_rows": 0, "player_rows_matched": 0, "team_rows": 0, "team_rows_matched": 0,
                      "mismatches": []} for name in INTERPRETATIONS}
    windows = []
    for kind in ("stats/player/season", "stats/team/season"):
        for body, params in zip(payloads.get(kind, []), metas.get(kind, [])):
            windows.append((kind, params["startDateRange"], params["endDateRange"]))
            for name, pred in INTERPRETATIONS.items():
                r = results[name]
                for row in body:
                    if kind == "stats/player/season":
                        cols = ", ".join(f"sum({f})" for f in list(P_FIELDS)[1:])
                        ours = con.execute(f"""SELECT count(*), {cols} FROM fact.player_games p JOIN core.games g USING (canonical_game_id)
                            WHERE p.canonical_player_id = cid('P', 'athlete', $aid) AND p.canonical_team_id = cid('T', 'team', $tid)
                              AND g.season = $season AND {pred}""",
                                           {"aid": str(row["athleteId"]), "tid": str(row["teamId"]), "season": row["season"],
                                            "lo": params["startDateRange"], "hi": params["endDateRange"]}).fetchone()
                        api = {k: row["games"] if p is None else dig(row, p) for k, p in P_FIELDS.items()}
                        key, who = "player", row["name"]
                    else:
                        cols = ", ".join(f"sum({f})" for f in list(T_FIELDS)[1:])
                        ours = con.execute(f"""SELECT count(*), {cols} FROM fact.team_games t JOIN core.games g USING (canonical_game_id)
                            WHERE t.canonical_team_id = cid('T', 'team', $tid) AND g.season = $season AND {pred}""",
                                           {"tid": str(row["teamId"]), "season": row["season"],
                                            "lo": params["startDateRange"], "hi": params["endDateRange"]}).fetchone()
                        api = {k: row["games"] if p is None else dig(row, p) for k, p in T_FIELDS.items()}
                        key, who = "team", row["team"]
                    cmp = compare_primitives(api, dict(zip(api, ours)))
                    r[f"{key}_rows"] += 1
                    r[f"{key}_rows_matched"] += cmp["match"]
                    if not cmp["match"]:
                        r["mismatches"].append({"endpoint": kind, "window": [params["startDateRange"], params["endDateRange"]],
                                                "who": who, "fields": {f: cmp["fields"][f] for f in cmp["mismatched"]}})
    for r in results.values():
        r["mismatches"] = r["mismatches"][:12]
        r["all_reconcile"] = (r["player_rows"] > 0 and r["team_rows"] > 0 and r["player_rows_matched"] == r["player_rows"]
                              and r["team_rows_matched"] == r["team_rows"])
    winners = [n for n, r in results.items() if r["all_reconcile"]]
    return {"windows": windows, "interpretations": results, "reconciling_interpretations": winners,
            "classification": "VALIDATED_FOR_TESTED_FIELDS" if len(winners) == 1 else "NOT_VALIDATED",
            "tested_fields": {"player": list(P_FIELDS), "team": list(T_FIELDS)}}
