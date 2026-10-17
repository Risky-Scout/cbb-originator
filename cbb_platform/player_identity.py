"""Stage 3.1 -- adjudicated, evidence-backed player-identity repairs applied inside every canonical build (v2.3).

Raw source rows are never modified: the Starter Pack and data/raw stay byte-identical. These rules rewrite only
derived canonical mappings, deterministically, and every rule is recorded in metadata.player_identity_resolution.
Evidence: docs/PLAYER_IDENTITY_COLLISION_AUDIT.md.

SPLIT  CBBD athleteId 31449 ("A.J. Lawson") = TWO people (SOURCE_ID_COLLISION):
       * the South Carolina guard (CBBD recruiting class 2018 -> South Carolina, Mississauga ON; roster attributes
         Brampton ON / born 2000) keeps canonical P-7d2ea3fe693fd65b = cid(31449);
       * the North Texas (2016-17, 2017-18) / McNeese (2019-20, 2020-21) guard (CBBD recruiting class 2016 -> North
         Texas, Bryan TX, no athleteId) moves to a new stable canonical id.
       Proof: 23 box appearances for both teams within 6 hours of each other; South Carolina and McNeese never met; the
       South Carolina player was a 2018 recruit, so the 2016-17/2017-18 North Texas rows cannot be his.
       North Texas and McNeese stay ONE entity (not proven distinct; flagged REVIEW_REQUIRED in the audit).
QUARANTINE  CBBD athleteId 19801 ("Omer Yurtseven"): his CBBD team-seasons are NC State 2016-17, 2017-18 and
       Georgetown 2019-20. CBBD PBP also labels a Tulane player with this id/name in 2013-14 .. 2015-16 (before
       Yurtseven reached college; 16 appearances within 6 hours of NC State games). Event rows of 19801 outside his
       team-seasons become IDENTITY_UNRESOLVED (canonical NULL). They are not reassigned: the best box-line match
       (Cameron Reynolds) fits only 26 of 66 games.
TEAM_REATTRIBUTION  game 209587 (Presbyterian 70 - Illinois State 78, 2025-03-23): all 19 player box rows carry
       Illinois State. The 10 rows of players who otherwise played >= 3 games for Presbyterian that season (and once
       for Illinois State) move to Presbyterian; afterwards both teams' player points equal their team box points
       exactly (70 / 78) and no row of either team is lost.
"""
from __future__ import annotations

import hashlib

import duckdb

VERSION = "stage3.1-player-identity-1.0.0"
# new stable canonical id for the North Texas / McNeese player (same opaque format as cid())
SPLIT_31449_NEW = "P-" + hashlib.sha256(b"CBBD|athlete_split|31449|NORTH_TEXAS_MCNEESE").hexdigest()[:16]
TEAMS = {"south_carolina": "266", "mcneese": "162", "north_texas": "204", "nc_state": "185", "georgetown": "97",
         "presbyterian": "233", "illinois_state": "119"}
SPLIT_SCOPE = [(TEAMS["north_texas"], 2017), (TEAMS["north_texas"], 2018), (TEAMS["mcneese"], 2020), (TEAMS["mcneese"], 2021)]
YURTSEVEN_VALID = [(TEAMS["nc_state"], 2017), (TEAMS["nc_state"], 2018), (TEAMS["georgetown"], 2020)]


def _tid(t: str) -> str:
    return f"cid('T','team','{t}')"


def _scope_sql(alias_team: str, alias_season: str, scope) -> str:
    return "(" + " OR ".join(f"({alias_team} = {_tid(t)} AND {alias_season} = {s})" for t, s in scope) + ")"


def new_split_id(con=None) -> str:
    return SPLIT_31449_NEW


def apply_box(con: duckdb.DuckDBPyConnection) -> None:
    """Box / season-file facts (after build_season_tables, before participation and derived tables)."""
    old = con.execute("SELECT cid('P','athlete','31449')").fetchone()[0]
    new = new_split_id(con)
    ev = ("SOURCE_ID_COLLISION: CBBD athleteId 31449 carried by two people; same-window appearances for two teams, "
          "no meeting between South Carolina and McNeese, South Carolina player a 2018 recruit")
    log = []
    if con.execute("SELECT count(*) FROM core.players WHERE canonical_player_id = ?", [old]).fetchone()[0]:
        con.execute("""INSERT INTO core.players SELECT ?, display_name, 2017, 2021, 'CBBD_IDENTITY_SPLIT', source_file,
                       'athleteId=31449;split=NORTH_TEXAS_MCNEESE', ingestion_version, received_at, processed_at
                       FROM core.players WHERE canonical_player_id = ?""", [new, old])
        con.execute("UPDATE core.players SET first_season = 2019 WHERE canonical_player_id = ?", [old])
        con.execute("""INSERT INTO metadata.player_id_map VALUES ('CBBD_IDENTITY_SPLIT', '31449|NORTH_TEXAS_MCNEESE', ?, NULL, NULL,
                       'MANUAL', 1.0, FALSE, 'Stage 3.1 split of CBBD athleteId 31449 (docs/PLAYER_IDENTITY_COLLISION_AUDIT.md)', ?)""",
                    [new, VERSION])
    for t, team_col in (("fact.player_games", "canonical_team_id"), ("fact.player_season_stats", "canonical_team_id"),
                        ("fact.player_season_shooting", "canonical_team_id")):
        n = con.execute(f"""UPDATE {t} SET canonical_player_id = ? WHERE canonical_player_id = ?
                            AND {_scope_sql(team_col, 'season', SPLIT_SCOPE)}""", [new, old]).fetchone()[0]
        log.append(("SPLIT_CBBD_ATHLETE_31449", "SPLIT", "SOURCE_ID_COLLISION", "31449", old, new, "North Texas 2017-2018; McNeese 2020-2021", t, n, ev))
    n = con.execute(f"""UPDATE metadata.player_id_map SET canonical_player_id = ? WHERE provider = 'CBBD_STARTER_PACK_TEAM_SEASON_NAME'
                        AND canonical_player_id = ? AND ({' OR '.join(f"provider_player_key = '{s}|{t}|A.J. Lawson'" for t, s in SPLIT_SCOPE)})""",
                    [new, old]).fetchone()[0]
    log.append(("SPLIT_CBBD_ATHLETE_31449", "SPLIT", "SOURCE_ID_COLLISION", "31449", old, new, "name-key map rows", "metadata.player_id_map", n, ev))
    # box team re-attribution, game 209587 (rule-selected rows; reconciliation asserted)
    g = con.execute("SELECT cid('G','game','209587')").fetchone()[0]
    isu, pres = con.execute(f"SELECT {_tid(TEAMS['illinois_state'])}, {_tid(TEAMS['presbyterian'])}").fetchone()
    con.execute("""CREATE OR REPLACE TEMP TABLE reattr AS
        SELECT p.player_game_key FROM fact.player_games p WHERE p.canonical_game_id = ? AND p.canonical_team_id = ?
          AND p.canonical_player_id IS NOT NULL
          AND (SELECT count(*) FROM fact.player_games x WHERE x.canonical_player_id = p.canonical_player_id AND x.season = p.season
               AND x.canonical_team_id = ?) >= 3
          AND (SELECT count(*) FROM fact.player_games x WHERE x.canonical_player_id = p.canonical_player_id AND x.season = p.season
               AND x.canonical_team_id = ?) = 1""", [g, isu, pres, isu])
    if con.execute("SELECT count(*) FROM reattr").fetchone()[0]:
        moved = con.execute("""SELECT sum(points) FROM fact.player_games WHERE player_game_key IN (SELECT player_game_key FROM reattr)""").fetchone()[0]
        kept = con.execute("""SELECT sum(points) FROM fact.player_games WHERE canonical_game_id = ? AND canonical_team_id = ?
                              AND player_game_key NOT IN (SELECT player_game_key FROM reattr)""", [g, isu]).fetchone()[0]
        box = dict(con.execute("SELECT canonical_team_id, points FROM fact.team_games WHERE canonical_game_id = ?", [g]).fetchall())
        if (moved, kept) != (box.get(pres), box.get(isu)):
            raise RuntimeError(f"game 209587 re-attribution would not reconcile: moved {moved} / kept {kept} vs box {box}")
        n = con.execute("""UPDATE fact.player_games SET canonical_team_id = ?, canonical_opponent_id = ?, is_home = NOT is_home
                           WHERE player_game_key IN (SELECT player_game_key FROM reattr)""", [pres, isu]).fetchone()[0]
        log.append(("BOX_TEAM_REATTRIBUTION_GAME_209587", "TEAM_REATTRIBUTION", "SOURCE_TEAM_MISATTRIBUTION", None, None, None,
                    "game 209587 (2025-03-23) Illinois State -> Presbyterian", "fact.player_games", n,
                    f"all 19 box rows filed under Illinois State; moved rows sum {moved} = Presbyterian box {moved}; remaining {kept} = Illinois State box"))
    con.executemany("INSERT INTO metadata.player_identity_resolution VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (VERSION,) for r in log])


def apply_events(con: duckdb.DuckDBPyConnection) -> None:
    """CBBD API event facts (inside event_build, before reconciliation/defects). Context = the game's teams."""
    old = con.execute("SELECT cid('P','athlete','31449')").fetchone()[0]
    yur = con.execute("SELECT cid('P','athlete','19801')").fetchone()[0]
    new = new_split_id(con)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE id_ctx AS
        SELECT canonical_game_id, season,
               {_scope_sql('home_team_id', 'season', SPLIT_SCOPE)} OR {_scope_sql('away_team_id', 'season', SPLIT_SCOPE)} AS split_game,
               {_scope_sql('home_team_id', 'season', YURTSEVEN_VALID)} OR {_scope_sql('away_team_id', 'season', YURTSEVEN_VALID)} AS yurtseven_game
        FROM core.games""")
    log = []
    ev31 = "SOURCE_ID_COLLISION (see apply_box); event rows assigned by the game's teams"
    ev19 = ("SOURCE_ID_COLLISION: CBBD athleteId 19801 also labels a Tulane player (2013-14..2015-16); rows outside NC State "
            "2017-2018 / Georgetown 2020 games are IDENTITY_UNRESOLVED")
    for t, idcol, pcol in (("fact.play_participants", "source_athlete_id", "canonical_player_id"),
                           ("fact.play_on_floor", "source_athlete_id", "canonical_player_id"),
                           ("fact.substitutions", "source_athlete_id", "canonical_player_id"),
                           ("fact.shots", "shooter_source_athlete_id", "shooter_player_id"),
                           ("fact.shots", "assister_source_athlete_id", "assister_player_id")):
        n = con.execute(f"""UPDATE {t} SET {pcol} = ? WHERE {idcol} = '31449' AND canonical_game_id IN
                            (SELECT canonical_game_id FROM id_ctx WHERE split_game)""", [new]).fetchone()[0]
        log.append(("SPLIT_CBBD_ATHLETE_31449", "SPLIT", "SOURCE_ID_COLLISION", "31449", old, new, "events in North Texas 2017-2018 / McNeese 2020-2021 games", f"{t}.{pcol}", n, ev31))
        n = con.execute(f"""UPDATE {t} SET {pcol} = NULL WHERE {idcol} = '19801' AND canonical_game_id IN
                            (SELECT canonical_game_id FROM id_ctx WHERE NOT yurtseven_game)""").fetchone()[0]
        log.append(("QUARANTINE_CBBD_ATHLETE_19801", "QUARANTINE", "SOURCE_ID_COLLISION", "19801", yur, None, "events outside NC State 2017-2018 / Georgetown 2020 games", f"{t}.{pcol}", n, ev19))
    # list columns of fact.plays mirror the per-row tables
    for col in ("participant_player_ids", "on_floor_player_ids"):
        con.execute(f"""UPDATE fact.plays SET {col} = list_transform({col}, x -> CASE WHEN x = '{old}' THEN '{new}' ELSE x END)
                        WHERE list_contains({col}, '{old}') AND canonical_game_id IN (SELECT canonical_game_id FROM id_ctx WHERE split_game)""")
        con.execute(f"""UPDATE fact.plays SET {col} = list_transform({col}, x -> CASE WHEN x = '{yur}' THEN 'CBBD_ATHLETE:19801' ELSE x END)
                        WHERE list_contains({col}, '{yur}') AND canonical_game_id IN (SELECT canonical_game_id FROM id_ctx WHERE NOT yurtseven_game)""")
    # roster rows: membership on a split-scope team belongs to the split entity
    n = con.execute(f"""UPDATE core.rosters SET canonical_player_id = ? WHERE source_athlete_id = '31449'
                        AND {_scope_sql('canonical_team_id', 'season', SPLIT_SCOPE)}""", [new]).fetchone()[0]
    log.append(("SPLIT_CBBD_ATHLETE_31449", "SPLIT", "SOURCE_ID_COLLISION", "31449", old, new,
                "roster rows on McNeese/North Texas (attributes on the 2020 McNeese row belong to the South Carolina player: flagged)",
                "core.rosters", n, ev31))
    con.executemany("INSERT INTO metadata.player_identity_resolution VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (VERSION,) for r in log])
