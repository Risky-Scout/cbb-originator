"""Clearly labeled PRODUCTION-FORMAT FIXTURES (synthetic; never live data, never presented as live).

    synthetic_history(path)        a tiny canonical history DB (2024-25, 2025-26) with the relations Stage 3 reads,
                                   for CI / unit tests that cannot see the local frozen canonical DB
    increment_rows(history_db)     a 2026-27 increment (schedule, two completed rounds with box + substitutions,
                                   upcoming games incl. a duplicate-record pair and a non-D-I game, one portal transfer,
                                   one recruit) built from the TEAMS / PLAYERS of whatever history DB is given (the real
                                   frozen canonical DB locally; the synthetic one in CI). Every stat is synthetic.
    quotes_files(...)              The Odds API v4-format quote collections (fresh / stale / late / unmatched /
                                   unsupported / invalid cases)
    availability(...)              one bench player OUT, one rotation player OUT (rotation shock)
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

FIXTURE_PROVIDER = {"kind": "FIXTURE", "name": "cbb_platform.prod.fixtures",
                    "note": "synthetic production-format fixture; NOT live 2026-27 data"}
AS_OF = pd.Timestamp("2026-11-10T16:00:00Z")
SOURCE_CUTOFF = pd.Timestamp("2026-11-10T15:00:00Z")
ROUND1_TIP = pd.Timestamp("2026-11-04T00:00:00Z")       # Tue 2026-11-03 19:00 ET
ROUND2_TIP = pd.Timestamp("2026-11-07T00:00:00Z")       # Fri 2026-11-06 19:00 ET
UPCOMING_TIP = pd.Timestamp("2026-11-11T00:30:00Z")     # Tue 2026-11-10 19:30 ET
SEED = 20270


TEAM_BASE = 9000


def cid(prefix: str, entity: str, provider_id) -> str:
    import hashlib
    return f"{prefix}-" + hashlib.sha256(f"CBBD|{entity}|{provider_id}".encode()).hexdigest()[:16]


def athlete_id(team_index: int, j: int) -> int:
    return 100000 + 100 * (team_index + 1) + j


def _gid(tag: str) -> str:
    import hashlib
    return "G-" + hashlib.sha256(f"FIXTURE|game|{tag}".encode()).hexdigest()[:16]


def _pid(tag: str) -> str:
    import hashlib
    return "P-" + hashlib.sha256(f"FIXTURE|player|{tag}".encode()).hexdigest()[:16]


def _knowable(tip: pd.Timestamp) -> pd.Timestamp:
    return tip + pd.Timedelta(hours=4)


# ============================================================================ box generation (synthetic)
def _box(rng, gid, season, tip, team, opp, is_home, players: pd.DataFrame) -> tuple[list, dict, list]:
    """players: player_id, w (minutes weight), rates per minute for pts-ish stats. Minutes sum to exactly 200."""
    w = players["w"].to_numpy(dtype=float)
    m = np.floor(200 * w / w.sum()).astype(int)
    m[np.argmax(w)] += 200 - m.sum()
    rows, subs = [], []
    k = _knowable(tip)
    tot = {c: 0 for c in ("points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta", "orb", "drb",
                          "reb", "ast", "stl", "blk", "tov", "pf")}
    for i, p in enumerate(players.itertuples(index=False)):
        mins = int(m[i])
        if mins <= 0:
            continue
        lam = mins / 40.0
        two_pa, three_pa, fta = rng.poisson(9 * lam * p.r), rng.poisson(5 * lam * p.r), rng.poisson(4 * lam * p.r)
        two_pm, three_pm, ftm = rng.binomial(two_pa, 0.5), rng.binomial(three_pa, 0.34), rng.binomial(fta, 0.7)
        orb, drb = rng.poisson(1.5 * lam), rng.poisson(4.0 * lam)
        s = {"two_pm": two_pm, "two_pa": two_pa, "three_pm": three_pm, "three_pa": three_pa, "ftm": ftm, "fta": fta,
             "fgm": two_pm + three_pm, "fga": two_pa + three_pa, "points": 2 * two_pm + 3 * three_pm + ftm,
             "orb": orb, "drb": drb, "reb": orb + drb, "ast": rng.poisson(3 * lam), "stl": rng.poisson(1.2 * lam),
             "blk": rng.poisson(0.7 * lam), "tov": rng.poisson(2 * lam), "pf": rng.poisson(2.5 * lam)}
        for c in tot:
            tot[c] += s[c]
        rows.append({"canonical_game_id": gid, "canonical_team_id": team, "canonical_opponent_id": opp,
                     "canonical_player_id": p.player_id, "identity_status": "RESOLVED", "season": season,
                     "is_home": is_home, "starter": i < 5, "minutes": mins, **s, "source_position": p.pos,
                     "accounting_issues": None, "historical_knowable_at": k})
        out_el = mins * 60
        subs.append({"canonical_game_id": gid, "canonical_team_id": team, "canonical_player_id": p.player_id,
                     "season": season, "stint_number": 1, "sub_in_period": 1, "sub_in_seconds_remaining": 1200,
                     "sub_out_period": 1 if out_el <= 1200 else 2,
                     "sub_out_seconds_remaining": 1200 - out_el if out_el <= 1200 else 2400 - out_el,
                     "timing_complete": True, "historical_knowable_at": k})
    team_row = {"canonical_game_id": gid, "canonical_team_id": team, "canonical_opponent_id": opp, "season": season,
                "is_home": is_home, "neutral_site": False, "game_minutes": 40, "overtime_count": 0,
                **{c: tot[c] for c in ("points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta",
                                       "orb", "drb", "reb", "ast", "stl", "blk", "pf")},
                "tov_total": tot["tov"], "accounting_issues": None, "historical_knowable_at": k}
    return rows, team_row, subs


def _game(gid, season, tip, home, away, universe="DI_VS_DI", hp=None, ap=None, key=None) -> dict:
    et = tip.tz_convert("America/New_York")
    return {"canonical_game_id": gid, "season": season, "season_type": "regular", "game_type": "STD", "tournament": None,
            "scheduled_tip_utc": tip, "tip_time_quality": "EXACT", "game_date_et": pd.Timestamp(et.date()),
            "neutral_site": False, "conference_game": False, "home_team_id": home, "away_team_id": away,
            "home_conference_id": None, "away_conference_id": None, "canonical_venue_id": None,
            "home_points": hp, "away_points": ap, "game_universe": universe,
            "has_team_box_score": hp is not None, "has_player_box_score": hp is not None,
            "source_system": "FIXTURE", "source_record_key": key or gid}


# ============================================================================ synthetic history (CI)
def synthetic_history(path: Path, n_teams: int = 8, n_players: int = 12) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = np.random.default_rng(SEED)
    # CBBD-anchored ids (cid of a provider id), exactly like the frozen canonical history: provider team 9001.., athlete
    # 100000 + 100 * team + j, so production-format CBBD payload fixtures resolve to the same canonical identities
    teams = [cid("T", "team", TEAM_BASE + i) for i in range(1, n_teams + 1)]
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel"]
    pl = []
    for t_i, t in enumerate(teams):
        for j in range(n_players):
            aid = athlete_id(t_i, j)
            pl.append({"player_id": cid("P", "athlete", aid), "athlete_id": aid, "team": t, "j": j, "pos": "GFC"[j % 3],
                       "first": names[t_i % len(names)], "last": f"Player{j:02d}",
                       "name": f"{names[t_i % len(names)]} Player{j:02d}", "w": max(0.2, 1.0 - 0.07 * j), "r": 1.0 - 0.04 * j})
    pl = pd.DataFrame(pl)
    games, tb, pb = [], [], []
    for season in (2025, 2026):
        start = pd.Timestamp(f"{season - 1}-11-08T00:00:00Z")
        k = 0
        for rnd in range(2):
            for a in range(n_teams):
                for b in range(a + 1, n_teams):
                    tip = start + pd.Timedelta(days=3 * k)
                    k += 1
                    home, away = (teams[a], teams[b]) if rnd == 0 else (teams[b], teams[a])
                    gid = _gid(f"hist|{season}|{home}|{away}|{rnd}")
                    hr, ht, _ = _box(rng, gid, season, tip, home, away, True, pl[pl["team"] == home])
                    ar, at, _ = _box(rng, gid, season, tip, away, home, False, pl[pl["team"] == away])
                    games.append(_game(gid, season, tip, home, away, hp=ht["points"], ap=at["points"]))
                    tb += [ht, at]
                    pb += hr + ar
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA {s}")
    G = pd.DataFrame(games)
    con.register("g", G)
    con.execute("CREATE TABLE flagship.games AS SELECT * FROM g")
    con.execute("CREATE TABLE flagship.played_games AS SELECT * FROM g")
    con.register("tb", pd.DataFrame(tb))
    con.execute("CREATE TABLE flagship.team_game_outcomes AS SELECT * FROM tb")
    con.register("pb", pd.DataFrame(pb))
    con.execute("CREATE TABLE flagship.player_game_outcomes AS SELECT * FROM pb")
    ros = pd.concat([pd.DataFrame({"canonical_player_id": pl["player_id"], "canonical_team_id": pl["team"], "season": s,
                                   "source_athlete_id": pl["athlete_id"].astype(str), "start_season": 2025,
                                   "position": pl["pos"], "height_inches": 78.0, "weight_lbs": 200.0,
                                   "player_name": pl["name"], "first_name": pl["first"], "last_name": pl["last"]})
                     for s in (2025, 2026)])
    con.register("ros", ros)
    con.execute("CREATE TABLE flagship.rosters AS SELECT * FROM ros")
    con.execute("""CREATE TABLE flagship.substitutions (canonical_game_id VARCHAR, canonical_team_id VARCHAR,
        canonical_player_id VARCHAR, season INTEGER, stint_number INTEGER, sub_in_period INTEGER,
        sub_in_seconds_remaining INTEGER, sub_out_period INTEGER, sub_out_seconds_remaining INTEGER,
        timing_complete BOOLEAN, historical_knowable_at TIMESTAMPTZ)""")
    con.execute("CREATE TABLE flagship.plays (canonical_game_id VARCHAR, season INTEGER)")
    con.execute("""CREATE TABLE flagship.shots_trusted (canonical_game_id VARCHAR, shooter_player_id VARCHAR,
        assister_player_id VARCHAR, canonical_team_id VARCHAR, season INTEGER)""")
    con.execute("CREATE TABLE flagship.on_floor_trusted (canonical_game_id VARCHAR, canonical_player_id VARCHAR)")
    T = pd.DataFrame({"canonical_team_id": teams, "school": [f"Fixture {n}" for n in names[:n_teams]],
                      "mascot": ["Owls"] * n_teams, "display_name": [f"Fixture {n} Owls" for n in names[:n_teams]]})
    con.register("p", pl.rename(columns={"player_id": "canonical_player_id", "name": "display_name"})[["canonical_player_id", "display_name"]])
    con.execute("CREATE TABLE core.players AS SELECT * FROM p")
    con.register("pm", pd.DataFrame({"provider": "CBBD", "provider_player_key": pl["athlete_id"].astype(str),
                                     "canonical_player_id": pl["player_id"], "match_method": "ANCHOR", "match_confidence": 1.0,
                                     "needs_manual_review": False, "source_context": "fixture", "created_by_version": "fixture"}))
    con.execute("CREATE TABLE metadata.player_id_map AS SELECT * FROM pm")
    T["source_team_id"] = [str(TEAM_BASE + i) for i in range(1, n_teams + 1)]
    con.register("ts", pd.DataFrame([{"canonical_team_id": t, "season": s, "is_division_one": True} for t in teams for s in (2025, 2026)]))
    con.execute("CREATE TABLE core.team_seasons AS SELECT * FROM ts")
    con.register("t", T)
    con.execute("CREATE TABLE core.teams AS SELECT * FROM t")
    con.execute("""CREATE TABLE core.transfers (source_transfer_id VARCHAR, transfer_year INTEGER, first_name VARCHAR,
        last_name VARCHAR, canonical_player_id VARCHAR, candidate_canonical_player_id VARCHAR, identity_method VARCHAR,
        origin_team_id VARCHAR, destination_team_id VARCHAR, stars INTEGER, rating DOUBLE)""")
    con.execute("""CREATE TABLE core.recruits (source_recruit_id VARCHAR, recruit_year INTEGER, name VARCHAR,
        canonical_player_id VARCHAR, committed_team_id VARCHAR, stars INTEGER, rating DOUBLE, ranking INTEGER,
        height_inches DOUBLE, weight_lbs DOUBLE)""")
    con.execute("""CREATE TABLE metadata.identity_link_quarantine (canonical_player_id VARCHAR, from_season INTEGER,
        to_season INTEGER, reason VARCHAR)""")
    con.close()
    return path


# ============================================================================ 2026-27 increment (fixture)
def pick(history_db: Path, n_teams: int = 6) -> dict:
    """Deterministic fixture teams (most 2025-26 D-I games) and their 2025-26 players (minutes order)."""
    con = duckdb.connect(str(history_db), read_only=True)
    teams = con.execute("""SELECT t, count(*) n FROM (SELECT home_team_id t FROM flagship.played_games
                               WHERE season = 2026 AND game_universe = 'DI_VS_DI'
                           UNION ALL SELECT away_team_id FROM flagship.played_games WHERE season = 2026 AND game_universe = 'DI_VS_DI')
                           GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?""", [n_teams]).df()["t"].tolist()
    pl = con.execute(f"""SELECT p.canonical_team_id AS team, p.canonical_player_id AS player_id, sum(p.minutes) AS mins,
            count(*) AS gp, sum(p.points) AS pts, any_value(p.source_position) AS pos
        FROM flagship.player_game_outcomes p WHERE p.season = 2026 AND p.canonical_player_id IS NOT NULL
          AND p.canonical_team_id IN ({", ".join(f"'{t}'" for t in teams)})
        GROUP BY 1, 2 HAVING sum(p.minutes) > 0 ORDER BY 1, 3 DESC, 2""").df()
    other = con.execute(f"""SELECT canonical_player_id, canonical_team_id FROM flagship.player_game_outcomes
        WHERE season = 2026 AND canonical_player_id IS NOT NULL AND canonical_team_id NOT IN ({", ".join(f"'{t}'" for t in teams)})
        GROUP BY 1, 2 HAVING sum(minutes) > 100 ORDER BY 1 LIMIT 1""").fetchone()
    con.close()
    return {"teams": teams, "players": pl, "transfer": other}


def increment_rows(history_db: Path) -> tuple[dict, dict]:
    """(rows by relation, metadata) for a 2026-27 fixture increment."""
    rng = np.random.default_rng(SEED + 1)
    pk = pick(history_db)
    t = pk["teams"]
    if len(t) < 6:
        raise ValueError("history DB has fewer than 6 D-I teams in 2025-26")
    roster = {}
    for team in t:
        p = pk["players"][pk["players"]["team"] == team].head(10).copy()
        p["w"] = (p["mins"] / p["gp"]).clip(lower=2.0)
        p["r"] = 1.0
        p["pos"] = p["pos"].fillna("G")
        roster[team] = p.reset_index(drop=True)
    games, tb, pb, subs = [], [], [], []
    rounds = [(ROUND1_TIP, [(0, 1), (2, 3), (4, 5)]), (ROUND2_TIP, [(1, 2), (3, 4), (5, 0)])]
    for tip, pairs in rounds:
        for h, a in pairs:
            gid = _gid(f"2027|{tip.isoformat()}|{t[h]}|{t[a]}")
            hr, ht, hs = _box(rng, gid, 2027, tip, t[h], t[a], True, roster[t[h]])
            ar, at, as_ = _box(rng, gid, 2027, tip, t[a], t[h], False, roster[t[a]])
            games.append(_game(gid, 2027, tip, t[h], t[a], hp=ht["points"], ap=at["points"]))
            tb += [ht, at]
            pb += hr + ar
            subs += hs + as_
    upcoming = {}
    for h, a, tag in ((0, 3, "U1"), (1, 4, "U2"), (2, 5, "U3")):
        gid = _gid(f"2027|upcoming|{tag}")
        games.append(_game(gid, 2027, UPCOMING_TIP, t[h], t[a], key=f"fixture-event-{tag}"))
        upcoming[tag] = gid
    dup = _gid("2027|upcoming|U3-duplicate-record")        # second record of the same pair / date -> AMBIGUOUS_GAME
    games.append(_game(dup, 2027, UPCOMING_TIP + pd.Timedelta(minutes=30), t[5], t[2], key="fixture-event-U3b"))
    upcoming["U3b"] = dup
    nondi = _gid("2027|upcoming|nonDI")
    games.append(_game(nondi, 2027, UPCOMING_TIP, t[0], "T-00000000fffffff0", universe="DI_VS_NON_DI"))
    upcoming["NONDI"] = nondi
    recruit = _pid("2027|recruit|1")
    rows = {
        "flagship.games": pd.DataFrame(games), "flagship.team_game_outcomes": pd.DataFrame(tb),
        "flagship.player_game_outcomes": pd.DataFrame(pb), "flagship.substitutions": pd.DataFrame(subs),
        "core.team_seasons": pd.DataFrame([{"canonical_team_id": x, "season": 2027, "is_division_one": True} for x in t]),
        "core.players": pd.DataFrame([{"canonical_player_id": recruit, "display_name": "Fixture Freshman"}]),
        "core.recruits": pd.DataFrame([{"source_recruit_id": "FIXTURE-R1", "recruit_year": 2026,
                                        "canonical_player_id": recruit, "committed_team_id": t[1],
                                        "stars": 4, "rating": 0.95, "ranking": 40, "height_inches": 79.0, "weight_lbs": 200.0}]),
    }
    if pk["transfer"] is not None:
        rows["core.transfers"] = pd.DataFrame([{"source_transfer_id": "FIXTURE-T1", "transfer_year": 2026,
                                                "canonical_player_id": pk["transfer"][0], "candidate_canonical_player_id": None,
                                                "identity_method": "RESOLVED_ROSTER_CORROBORATED",
                                                "origin_team_id": pk["transfer"][1], "destination_team_id": t[0],
                                                "stars": 3, "rating": 0.85}])
    meta = {"teams": t, "upcoming": upcoming, "roster": {k: v["player_id"].tolist() for k, v in roster.items()},
            "recruit": recruit, "transfer": pk["transfer"][0] if pk["transfer"] is not None else None,
            "preseason_evidence": {"portal_2026_complete": True, "recruits_2026_complete": True,
                                   "source": "FIXTURE_DECLARED_COMPLETE (synthetic)"}}
    return rows, meta


# ============================================================================ quotes (The Odds API v4 format)
def _names(history_db: Path, ids_t, ids_p) -> tuple[dict, dict]:
    con = duckdb.connect(str(history_db), read_only=True)
    tn = dict(con.execute(f"SELECT canonical_team_id, any_value(display_name) FROM core.teams WHERE canonical_team_id IN "
                          f"({', '.join(repr(x) for x in ids_t)}) GROUP BY 1").fetchall())
    pn = dict(con.execute(f"SELECT canonical_player_id, any_value(display_name) FROM core.players WHERE canonical_player_id IN "
                          f"({', '.join(repr(x) for x in ids_p)}) GROUP BY 1").fetchall())
    con.close()
    return tn, pn


def _outcomes(name, line, over, under):
    return [{"name": "Over", "description": name, "price": over, "point": line},
            {"name": "Under", "description": name, "price": under, "point": line}]


def quotes_files(history_db: Path, meta: dict, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t = meta["teams"]
    tn, pn = _names(history_db, t, [p for v in meta["roster"].values() for p in v])
    commence = UPCOMING_TIP.isoformat().replace("+00:00", "Z")
    fresh, stale, late = "2026-11-10T15:50:00Z", "2026-11-10T15:00:00Z", "2026-11-10T16:30:00Z"

    def event(eid, home, away, books):
        return {"id": eid, "sport_key": "basketball_ncaab", "commence_time": commence, "home_team": home,
                "away_team": away, "bookmakers": books}

    def players(team, k):
        return [pn[p] for p in meta["roster"][team][:k] if p in pn]

    h1, a1 = t[0], t[3]
    p_h1, p_a1 = players(h1, 3), players(a1, 2)
    mk = lambda key, outs, upd=fresh: {"key": key, "last_update": upd, "outcomes": outs}
    book_a = {"key": "fixturebook_a", "title": "Fixture Book A", "last_update": fresh, "markets": [
        mk("player_points", _outcomes(p_h1[0], 14.5, -115, -105) + _outcomes(p_a1[0], 12.0, -110, -110)),
        mk("player_rebounds", _outcomes(p_h1[1], 4.5, +100, -120)),
        mk("player_points_rebounds_assists", _outcomes(p_h1[0], 22.5, -110, -110)),
        mk("player_blocks_steals", _outcomes(p_h1[2], 1.5, +120, -150)),
        mk("player_threes", _outcomes("Not A Rostered Player", 1.5, -110, -110)),
        mk("player_double_double", [{"name": "Yes", "description": p_h1[0], "price": +300}]),
        mk("player_assists", [{"name": "Over", "description": p_h1[1], "price": 50, "point": 2.5}]),
        mk("player_turnovers", _outcomes(p_a1[1], 1.5, -125, +105), upd=stale)]}
    book_b = {"key": "fixturebook_b", "title": "Fixture Book B", "last_update": fresh, "markets": [
        mk("player_points", _outcomes(p_h1[0], 14.5, -105, -115)),
        mk("player_points_alternate", _outcomes(p_h1[0], 19.5, +190, -260))]}
    e1 = event("fixture-event-U1", tn.get(h1, h1), tn.get(a1, a1), [book_a, book_b])
    h2, a2 = t[1], t[4]
    e2 = event("fixture-event-U2", tn.get(h2, h2), tn.get(a2, a2), [
        {"key": "fixturebook_a", "title": "Fixture Book A", "last_update": fresh, "markets": [
            mk("player_points", _outcomes(players(h2, 1)[0], 10.5, -110, -110))]}])
    e_unknown = event("fixture-event-UNKNOWN", "Nonexistent State Fixtures", tn.get(a2, a2), [
        {"key": "fixturebook_a", "title": "Fixture Book A", "last_update": fresh, "markets": [
            mk("player_points", _outcomes("Someone Else", 9.5, -110, -110))]}])
    main = {"label": "FIXTURE (synthetic quotes; NOT live)", "provider": "the-odds-api",
            "collector_received_at": "2026-11-10T15:55:00Z", "events": [e1, e2, e_unknown]}
    late_doc = {"label": "FIXTURE late collection (after prediction_as_of)", "provider": "the-odds-api",
                "collector_received_at": "2026-11-10T16:35:00Z", "events": [event("fixture-event-U1", tn.get(h1, h1), tn.get(a1, a1), [
                    {"key": "fixturebook_c", "title": "Fixture Book C", "last_update": late, "markets": [
                        mk("player_points", _outcomes(p_h1[0], 14.5, +150, -190), upd=late)]}])]}
    paths = [out_dir / "quotes_fixture_main.json", out_dir / "quotes_fixture_late.json"]
    paths[0].write_text(json.dumps(main, indent=1))
    paths[1].write_text(json.dumps(late_doc, indent=1))
    return paths


def availability(meta: dict) -> pd.DataFrame:
    """U1: the home team's LAST rostered player OUT (bench: suppressed only). U2: the home team's top-minutes player
    OUT (rotation shock)."""
    t, up = meta["teams"], meta["upcoming"]
    return pd.DataFrame([
        {"game_id": up["U1"], "player_id": meta["roster"][t[0]][-1], "status": "OUT", "received_at": SOURCE_CUTOFF,
         "source": "FIXTURE"},
        {"game_id": up["U2"], "player_id": meta["roster"][t[1]][0], "status": "OUT", "received_at": SOURCE_CUTOFF,
         "source": "FIXTURE"}])
