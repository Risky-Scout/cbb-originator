"""Stage 2D-2J, 2N, 2P -- canonical CBBD event layer, built set-wise in DuckDB from immutable raw payloads.

Called by build_canonical (via adapters.load_all_raw -> cbbd.normalize_all) on every build, so the event
tables are a pure function of (Starter Pack + data/raw/cbbd). Order of work:

    rosters -> players added for roster-only athletes (CBBD athleteId is the verified persistent id)
    plays (dedup on gameId+play id; conflicting copies are a defect) -> participants -> onFloor (+quality)
    shots -> substitutions (+derived pairs) -> lineups (+quarantine) -> recruiting / portal / venues
    PBP score reconciliation -> source-defect quarantine

Nothing here repairs a source record. Classes and qa.source_defects describe defects; flagship.* trusted
views exclude them (schema.sql).
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

from cbb_platform import config as C, raw_store

SRC = "CBBD_API"
VERSION = "stage2-events-1.0.0"
PLAY_ENDPOINT_RANK = {"plays/date": 1, "plays/game": 2, "plays/team": 3}   # deterministic copy preference

PLAY_COLUMNS = """{'id':'BIGINT','sourceId':'VARCHAR','gameId':'BIGINT','gameSourceId':'VARCHAR','gameStartDate':'VARCHAR',
'season':'INTEGER','seasonType':'VARCHAR','gameType':'VARCHAR','tournament':'VARCHAR','playType':'VARCHAR',
'isHomeTeam':'BOOLEAN','teamId':'BIGINT','team':'VARCHAR','conference':'VARCHAR','teamSeed':'DOUBLE','opponentId':'BIGINT',
'opponent':'VARCHAR','opponentConference':'VARCHAR','opponentSeed':'DOUBLE','period':'INTEGER','clock':'VARCHAR',
'secondsRemaining':'INTEGER','homeScore':'INTEGER','awayScore':'INTEGER','homeWinProbability':'DOUBLE',
'scoringPlay':'BOOLEAN','shootingPlay':'BOOLEAN','scoreValue':'DOUBLE','wallclock':'VARCHAR','playText':'VARCHAR',
'participants':'STRUCT(id BIGINT, name VARCHAR)[]','onFloor':'STRUCT(id BIGINT, name VARCHAR, team VARCHAR)[]',
'shotInfo':'STRUCT(shooter STRUCT(id BIGINT, name VARCHAR), made BOOLEAN, "range" VARCHAR, assisted BOOLEAN, assistedBy STRUCT(id BIGINT, name VARCHAR), location STRUCT(x DOUBLE, y DOUBLE))'}"""
SUB_COLUMNS = """{'gameId':'BIGINT','startDate':'VARCHAR','teamId':'BIGINT','team':'VARCHAR','conference':'VARCHAR',
'athleteId':'BIGINT','athlete':'VARCHAR','position':'VARCHAR','opponentId':'BIGINT','opponent':'VARCHAR',
'subIn':'STRUCT(period INTEGER, secondsRemaining INTEGER, teamPoints INTEGER, opponentPoints INTEGER)',
'subOut':'STRUCT(period INTEGER, secondsRemaining INTEGER, teamPoints INTEGER, opponentPoints INTEGER)'}"""

# Court frame measured from the data: CBBD shot locations are in a 0..940 x 0..500 frame (tenths of feet).
COURT_X = (0.0, 940.0)
COURT_Y = (0.0, 500.0)


def _files(con, endpoints: tuple[str, ...], root: Path | None = None) -> list[str]:
    """Register non-empty raw payloads of `endpoints` in temp table raw_files; return their paths."""
    root = root or C.RAW
    rows = []
    for path, meta in raw_store.records("cbbd", root=root):
        if meta["endpoint"] in endpoints and meta.get("size_bytes", 3) > 2:
            rows.append((str(path), str(path.relative_to(root)), meta["endpoint"], meta["received_at"],
                         json.dumps(meta["params"], sort_keys=True), PLAY_ENDPOINT_RANK.get(meta["endpoint"], 9)))
    con.execute("""CREATE OR REPLACE TEMP TABLE raw_files (path VARCHAR, rel VARCHAR, endpoint VARCHAR,
                   received_at TIMESTAMPTZ, params VARCHAR, rank INTEGER)""")
    if rows:
        con.executemany("INSERT INTO raw_files VALUES (?,?,?,?,?,?)", rows)
    return [r[0] for r in rows]


def _read_json(paths: list[str], columns: str) -> str:
    lst = "[" + ",".join("'" + p.replace("'", "''") + "'" for p in paths) + "]"
    return (f"read_json({lst}, format='array', columns={columns}, filename=true, "
            "maximum_object_size=200000000)")


def _known_players(con) -> None:
    from cbb_platform.cbbd_identity import verified_anchor_compatible
    con.execute("""CREATE OR REPLACE TEMP TABLE known_athletes AS
                   SELECT CAST(provider_player_key AS BIGINT) AS athlete_id, canonical_player_id
                   FROM metadata.player_id_map WHERE provider = 'CBBD' AND canonical_player_id IS NOT NULL
                     AND TRY_CAST(provider_player_key AS BIGINT) IS NOT NULL AND ?""", [verified_anchor_compatible()])


def _lineage_vars(con) -> None:
    con.execute("SET VARIABLE ev_src = ?", [SRC])
    try:
        con.execute("SELECT getvariable('ver'), getvariable('proc')").fetchone()
    except duckdb.Error:
        pass


def _game_pit(con) -> None:
    """event_time / historical_knowable_at per game (PIT rule HR-1), same SQL as build_canonical."""
    from cbb_platform.build_canonical import KNOWABLE_SQL
    con.execute(KNOWABLE_SQL)


# ============================================================================ rosters + players
def build_rosters(con, root=None) -> None:
    paths = _files(con, ("teams/roster",), root)
    if not paths:
        return
    # Rosters are nested two levels; read them as typed structs.
    cols = """{'teamId':'BIGINT','teamSourceId':'VARCHAR','team':'VARCHAR','conference':'VARCHAR','season':'INTEGER',
    'players':'STRUCT(id BIGINT, sourceId VARCHAR, name VARCHAR, firstName VARCHAR, lastName VARCHAR, jersey VARCHAR,
     position VARCHAR, height DOUBLE, weight DOUBLE, hometown STRUCT(city VARCHAR, state VARCHAR, country VARCHAR,
     latitude DOUBLE, longitude DOUBLE, countyFips VARCHAR), dateOfBirth VARCHAR, startSeason INTEGER, endSeason INTEGER)[]'}"""
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE roster_rows AS
    SELECT f.rel, f.received_at, t.teamId, t.teamSourceId, t.season, unnest(t.players) AS p
    FROM {_read_json(paths, cols)} t JOIN raw_files f ON f.path = t.filename
    WHERE len(t.players) > 0;
    CREATE OR REPLACE TEMP TABLE roster_dedup AS
    SELECT DISTINCT ON (p.id, teamId, season) * FROM roster_rows ORDER BY p.id, teamId, season, received_at DESC, rel;
    """)
    # Roster-only athletes become persistent players (CBBD athleteId = verified persistent id). Existing
    # canonical ids are never altered: only athletes with no core.players row are added.
    con.execute("""
    INSERT INTO core.players
    SELECT cid('P','athlete', p.id), arg_max(p.name, season * 1000000 + teamId), min(season), max(season),
           getvariable('ev_src'), arg_max(rel, season * 1000000 + teamId), 'athleteId=' || p.id,
           getvariable('ver'), min(received_at), getvariable('proc')
    FROM roster_dedup WHERE p.id IS NOT NULL AND p.name IS NOT NULL
      AND cid('P','athlete', p.id) NOT IN (SELECT canonical_player_id FROM core.players)
    GROUP BY p.id;
    INSERT INTO metadata.player_id_map
    SELECT 'CBBD', CAST(p.id AS VARCHAR), cid('P','athlete', p.id), NULL, NULL, 'ANCHOR', 1.0, FALSE,
           'persistent CBBD athleteId from /teams/roster (Stage 2; no season-file row)', getvariable('ver')
    FROM (SELECT DISTINCT p.id AS id FROM roster_dedup WHERE p.id IS NOT NULL) x(id),
         LATERAL (SELECT struct_pack(id := x.id) AS p)
    WHERE CAST(x.id AS VARCHAR) NOT IN (SELECT provider_player_key FROM metadata.player_id_map WHERE provider = 'CBBD');
    """)
    con.execute("""
    INSERT INTO core.rosters
    SELECT cid('P','athlete', p.id), cid('T','team', teamId), season, CAST(p.id AS VARCHAR), p.sourceId,
           CAST(teamId AS VARCHAR), teamSourceId, p.name, p.firstName, p.lastName, p.jersey, p.position,
           p.height, p.weight, p.dateOfBirth, p.hometown.city, p.hometown.state, p.hometown.country,
           p.hometown.latitude, p.hometown.longitude, p.hometown.countyFips, p.startSeason, p.endSeason,
           NULL, NULL, NULL,
           EXISTS (SELECT 1 FROM metadata.player_id_map m WHERE m.provider = 'CBBD' AND m.provider_player_key = CAST(p.id AS VARCHAR)
                   AND m.source_context NOT LIKE '%/teams/roster%'),
           getvariable('ev_src'), rel, 'season=' || season || ';teamId=' || teamId || ';athleteId=' || p.id,
           getvariable('ver'), received_at, getvariable('proc')
    FROM roster_dedup WHERE p.id IS NOT NULL;
    """)


# ============================================================================ plays
def build_plays(con, root=None) -> None:
    """Chunked by GAME (bounded memory). Pass 1 reads only (gameId, season, file) from every play payload and
    assigns each game ONE chunk = its canonical season (else the payload's own season field, else -1). Pass 2
    builds plays + participants + onFloor + shots one chunk at a time, reading only files that hold the
    chunk's games and keeping only those games, so every copy of a game is deduplicated together."""
    paths = _files(con, tuple(PLAY_ENDPOINT_RANK), root)
    if not paths:
        return
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE play_index AS
    SELECT DISTINCT p.gameId, p.filename, p.season FROM {_read_json(paths, "{'gameId':'BIGINT','season':'INTEGER'}")} p;
    CREATE OR REPLACE TEMP TABLE game_chunk AS
    -- onFloor-era seasons are wide (10 nested entries per play): split them into 4 game-hash sub-chunks
    SELECT i.gameId, coalesce(any_value(g.season), max(i.season), -1) * 10
           + CASE WHEN coalesce(any_value(g.season), max(i.season), -1) >= 2024 THEN i.gameId % 4 ELSE 0 END AS chunk
    FROM play_index i LEFT JOIN core.games g ON g.canonical_game_id = cid('G','game', i.gameId) GROUP BY 1;
    CREATE OR REPLACE TEMP TABLE play_dup_all (gameId BIGINT, id BIGINT, copies BIGINT, variants BIGINT);
    """)
    for (chunk,) in con.execute("SELECT DISTINCT chunk FROM game_chunk ORDER BY 1").fetchall():
        files = [r[0] for r in con.execute("""SELECT DISTINCT i.filename FROM play_index i JOIN game_chunk c USING (gameId)
                                              WHERE c.chunk = ? ORDER BY 1""", [chunk]).fetchall()]
        _plays_chunk(con, files, chunk)
        build_shots(con)
        con.execute("INSERT INTO play_dup_all SELECT gameId, id, copies, variants FROM play_dup")
        for t in ("pl", "of_norm", "of_class", "pp_norm", "play_lists", "play_dup", "game_sides"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute("DROP TABLE IF EXISTS play_index")


def _plays_chunk(con, paths: list[str], chunk: int) -> None:
    # row_hash = content hash of every source field (the reader's filename column excluded)
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE play_copies AS
    SELECT p.* EXCLUDE (filename), f.rel, f.received_at AS recv, f.rank,
           md5(CAST(to_json(struct_pack(
        p.id, p.sourceId, p.gameId, p.gameSourceId, p.gameStartDate, p.season, p.seasonType, p.gameType, p.tournament,
        p.playType, p.isHomeTeam, p.teamId, p.team, p.conference, p.teamSeed, p.opponentId, p.opponent, p.opponentConference,
        p.opponentSeed, p.period, p.clock, p.secondsRemaining, p.homeScore, p.awayScore, p.homeWinProbability, p.scoringPlay,
        p.shootingPlay, p.scoreValue, p.wallclock, p.playText, p.participants, p.onFloor, p.shotInfo)) AS VARCHAR)) AS row_hash
    FROM {_read_json(paths, PLAY_COLUMNS)} p JOIN raw_files f ON f.path = p.filename
    WHERE p.gameId IN (SELECT gameId FROM game_chunk WHERE chunk = {int(chunk)});
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE play_dup AS
    SELECT gameId, id, count(*) AS copies, count(DISTINCT row_hash) AS variants FROM play_copies GROUP BY 1, 2;
    CREATE OR REPLACE TEMP TABLE pl AS
    SELECT DISTINCT ON (c.gameId, c.id) c.*, d.copies, d.variants
    FROM play_copies c JOIN play_dup d USING (gameId, id)
    ORDER BY c.gameId, c.id, c.rank, c.rel;
    DROP TABLE play_copies;
    ALTER TABLE pl ADD COLUMN seq INTEGER;
    UPDATE pl SET seq = s.seq FROM (SELECT gameId, id, row_number() OVER (PARTITION BY gameId ORDER BY id) AS seq FROM pl) s
    WHERE s.gameId = pl.gameId AND s.id = pl.id;

    -- home/away team NAMES of each game (onFloor entries carry team names only)
    CREATE OR REPLACE TEMP TABLE game_sides AS
    WITH h AS (SELECT gameId, CASE WHEN isHomeTeam THEN team ELSE opponent END AS home,
                      CASE WHEN isHomeTeam THEN opponent ELSE team END AS away FROM pl WHERE isHomeTeam IS NOT NULL),
    hm AS (SELECT gameId, home, row_number() OVER (PARTITION BY gameId ORDER BY count(*) DESC, home) AS r FROM h GROUP BY 1, 2),
    am AS (SELECT gameId, away, row_number() OVER (PARTITION BY gameId ORDER BY count(*) DESC, away) AS r FROM h GROUP BY 1, 2)
    SELECT hm.gameId, hm.home, am.away FROM hm JOIN am ON am.gameId = hm.gameId AND am.r = 1 WHERE hm.r = 1;

    CREATE OR REPLACE TEMP TABLE of_rows AS
    SELECT p.gameId, p.id AS play_id, unnest(generate_series(1, len(p.onFloor))) AS floor_order,
           unnest(p.onFloor) AS e
    FROM pl p WHERE len(p.onFloor) > 0;
    CREATE OR REPLACE TEMP TABLE of_norm AS
    SELECT o.gameId, o.play_id, o.floor_order, o.e.id AS aid, o.e.name AS aname, o.e.team AS team_name,
           k.canonical_player_id,
           CASE WHEN o.e.team = s.home THEN 'HOME' WHEN o.e.team = s.away THEN 'AWAY' ELSE 'UNKNOWN' END AS side,
           s.home IS NULL OR s.away IS NULL AS sides_unknown
    FROM of_rows o LEFT JOIN known_athletes k ON k.athlete_id = o.e.id LEFT JOIN game_sides s ON s.gameId = o.gameId;
    DROP TABLE of_rows;
    CREATE OR REPLACE TEMP TABLE pp_norm AS
    SELECT x.gameId, x.play_id, x.i, x.e.id AS aid, x.e.name AS aname, k.canonical_player_id
    FROM (SELECT gameId, id AS play_id, unnest(generate_series(1, len(participants))) AS i, unnest(participants) AS e
          FROM pl WHERE len(participants) > 0) x LEFT JOIN known_athletes k ON k.athlete_id = x.e.id;
    CREATE OR REPLACE TEMP TABLE play_lists AS
    WITH f AS (SELECT gameId, play_id, list(coalesce(canonical_player_id, 'CBBD_ATHLETE:' || aid) ORDER BY floor_order) AS fl
               FROM of_norm GROUP BY 1, 2),
         q AS (SELECT gameId, play_id, list(coalesce(canonical_player_id, 'CBBD_ATHLETE:' || aid) ORDER BY i) AS pl_
               FROM pp_norm GROUP BY 1, 2)
    SELECT coalesce(f.gameId, q.gameId) AS gameId, coalesce(f.play_id, q.play_id) AS play_id, f.fl, q.pl_
    FROM f FULL OUTER JOIN q ON f.gameId = q.gameId AND f.play_id = q.play_id;

    -- on-floor quality: same rules and order as event_metrics.onfloor_class (unit tested for agreement)
    CREATE OR REPLACE TEMP TABLE of_class AS
    WITH per_ath AS (SELECT gameId, play_id, aid, count(*) AS n, count(DISTINCT team_name) AS nt
                     FROM of_norm WHERE aid IS NOT NULL GROUP BY 1, 2, 3),
    pa AS (SELECT gameId, play_id, max(nt) AS max_teams, max(n) AS max_n FROM per_ath GROUP BY 1, 2),
    pc AS (SELECT gameId, play_id, bool_or(aid IS NULL OR canonical_player_id IS NULL) AS unresolved,
                  bool_or(sides_unknown) AS sides_unknown,
                  count(*) FILTER (WHERE side = 'HOME') AS nh, count(*) FILTER (WHERE side = 'AWAY') AS na,
                  count(*) FILTER (WHERE side = 'UNKNOWN') AS nx
           FROM of_norm GROUP BY 1, 2)
    SELECT pc.gameId, pc.play_id, pc.nh, pc.na,
           CASE WHEN pc.unresolved THEN 'UNRESOLVED_PLAYER'
                WHEN coalesce(pa.max_teams, 1) > 1 THEN 'PLAYER_ON_BOTH_TEAMS'
                WHEN coalesce(pa.max_n, 1) > 1 THEN 'DUPLICATE_PLAYER'
                WHEN pc.sides_unknown OR pc.nx > 0 THEN 'OTHER_INVALID'
                WHEN pc.nh = 0 THEN 'MISSING_HOME' WHEN pc.na = 0 THEN 'MISSING_AWAY'
                WHEN pc.nh > 5 OR pc.na > 5 THEN 'MORE_THAN_5'
                WHEN pc.nh < 5 OR pc.na < 5 THEN 'LESS_THAN_5'
                ELSE 'VALID_5V5' END AS quality
    FROM pc LEFT JOIN pa USING (gameId, play_id);
    """)
    con.execute("""
    INSERT INTO fact.plays
    SELECT cid('G','game', p.gameId), CAST(p.id AS VARCHAR), p.seq, CAST(p.gameId AS VARCHAR), p.sourceId, p.season,
           p.period, p.clock, p.secondsRemaining, p.playType,
           CASE WHEN p.teamId IS NOT NULL THEN cid('T','team', p.teamId) END, CAST(p.teamId AS VARCHAR), p.isHomeTeam,
           p.homeScore, p.awayScore, p.scoringPlay, p.shootingPlay, CAST(p.scoreValue AS INTEGER), p.playText,
           p.homeWinProbability,
           coalesce(ls.pl_, []), coalesce(ls.fl, []),
           len(coalesce(p.participants, [])), len(coalesce(p.onFloor, [])),
           coalesce(c.quality, 'NO_ONFLOOR'), p.wallclock, p.copies,
           -- PIT rule HR-1; for a CBBD game absent from core.games (quarantined) the provider's own tip is used
           coalesce(gp.event_time, TRY_CAST(p.gameStartDate AS TIMESTAMPTZ)),
           coalesce(gp.knowable_at, TRY_CAST(p.gameStartDate AS TIMESTAMPTZ) + INTERVAL 4 HOUR), p.rel,
           getvariable('ev_src'), p.rel, 'gameId=' || p.gameId || ';playId=' || p.id, getvariable('ver'), p.recv, getvariable('proc')
    FROM pl p LEFT JOIN of_class c ON c.gameId = p.gameId AND c.play_id = p.id
    LEFT JOIN play_lists ls ON ls.gameId = p.gameId AND ls.play_id = p.id
    LEFT JOIN game_pit gp ON gp.canonical_game_id = cid('G','game', p.gameId);

    INSERT INTO fact.play_participants
    SELECT cid('G','game', x.gameId), CAST(x.play_id AS VARCHAR), x.i, CAST(x.aid AS VARCHAR), x.canonical_player_id, x.aname,
           getvariable('ev_src'), p.rel, 'gameId=' || x.gameId || ';playId=' || x.play_id || ';participant=' || x.i,
           getvariable('ver'), p.recv, getvariable('proc')
    FROM pp_norm x JOIN pl p ON p.gameId = x.gameId AND p.id = x.play_id;

    INSERT INTO fact.play_on_floor
    SELECT cid('G','game', o.gameId), CAST(o.play_id AS VARCHAR), o.floor_order, CAST(o.aid AS VARCHAR),
           o.canonical_player_id, o.aname, o.team_name,
           CASE o.side WHEN 'HOME' THEN g.home_team_id WHEN 'AWAY' THEN g.away_team_id END, o.side,
           getvariable('ev_src'), p.rel, 'gameId=' || o.gameId || ';playId=' || o.play_id || ';floor=' || o.floor_order,
           getvariable('ver'), p.recv, getvariable('proc')
    FROM of_norm o JOIN pl p ON p.gameId = o.gameId AND p.id = o.play_id
    LEFT JOIN core.games g ON g.canonical_game_id = cid('G','game', o.gameId);
    """)


# ============================================================================ shots
def build_shots(con) -> None:
    if not _has(con, "pl"):
        return
    con.execute(f"""
    INSERT INTO fact.shots
    SELECT cid('G','game', p.gameId), CAST(p.id AS VARCHAR),
           ks.canonical_player_id, p.shotInfo.made, p.shotInfo."range", p.shotInfo.assisted, ka.canonical_player_id,
           p.shotInfo.location.x, p.shotInfo.location.y,
           p.season, p.period, p.clock, p.secondsRemaining,
           CASE WHEN p.teamId IS NOT NULL THEN cid('T','team', p.teamId) END, p.playType, CAST(p.scoreValue AS INTEGER),
           CAST(p.shotInfo.shooter.id AS VARCHAR), CAST(p.shotInfo.assistedBy.id AS VARCHAR),
           coalesce(c.quality, 'NO_ONFLOOR'),
           CASE WHEN c.quality = 'VALID_5V5' THEN EXISTS (
               SELECT 1 FROM of_norm o WHERE o.gameId = p.gameId AND o.play_id = p.id AND o.aid = p.shotInfo.shooter.id
                 AND o.side = CASE WHEN p.isHomeTeam THEN 'HOME' WHEN NOT p.isHomeTeam THEN 'AWAY' END) END,
           CASE WHEN p.shotInfo.location.x IS NULL OR p.shotInfo.location.y IS NULL THEN NULL
                ELSE p.shotInfo.location.x BETWEEN {COURT_X[0]} AND {COURT_X[1]}
                 AND p.shotInfo.location.y BETWEEN {COURT_Y[0]} AND {COURT_Y[1]} END,
           coalesce(gp.event_time, TRY_CAST(p.gameStartDate AS TIMESTAMPTZ)),
           coalesce(gp.knowable_at, TRY_CAST(p.gameStartDate AS TIMESTAMPTZ) + INTERVAL 4 HOUR),
           getvariable('ev_src'), p.rel, 'gameId=' || p.gameId || ';playId=' || p.id, getvariable('ver'), p.recv, getvariable('proc')
    FROM pl p LEFT JOIN of_class c ON c.gameId = p.gameId AND c.play_id = p.id
    LEFT JOIN known_athletes ks ON ks.athlete_id = p.shotInfo.shooter.id
    LEFT JOIN known_athletes ka ON ka.athlete_id = p.shotInfo.assistedBy.id
    LEFT JOIN game_pit gp ON gp.canonical_game_id = cid('G','game', p.gameId)
    WHERE p.shotInfo IS NOT NULL;
    """)


def _has(con, temp: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name = ? AND temporary", [temp]).fetchone()[0])


# ============================================================================ substitutions
def build_substitutions(con, root=None) -> None:
    paths = _files(con, ("substitutions/game", "substitutions/team"), root)
    if not paths:
        return
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE sub_copies AS
    SELECT s.* EXCLUDE (filename), f.rel, f.received_at AS recv, f.endpoint,
           md5(CAST(to_json(struct_pack(s.gameId, s.teamId, s.athleteId, s.subIn, s.subOut)) AS VARCHAR)) AS row_hash
    FROM {_read_json(paths, SUB_COLUMNS)} s JOIN raw_files f ON f.path = s.filename;
    -- the same athlete stint can arrive from /substitutions/game AND /substitutions/team: one copy is kept
    -- (team endpoint preferred: it is the backfill source), identical copies counted
    CREATE OR REPLACE TEMP TABLE sub AS
    SELECT DISTINCT ON (row_hash) *, count(*) OVER (PARTITION BY row_hash) AS copies
    FROM sub_copies ORDER BY row_hash, endpoint DESC, rel;
    DROP TABLE sub_copies;
    INSERT INTO fact.substitutions
    SELECT cid('G','game', s.gameId), cid('T','team', s.teamId), k.canonical_player_id, CAST(s.athleteId AS VARCHAR),
           row_number() OVER (PARTITION BY s.gameId, s.athleteId ORDER BY s.subIn.period, -s.subIn.secondsRemaining,
                              s.subOut.period, -s.subOut.secondsRemaining, s.teamId, s.row_hash),
           s.subIn.period, s.subIn.secondsRemaining, s.subIn.teamPoints, s.subIn.opponentPoints,
           s.subOut.period, s.subOut.secondsRemaining, s.subOut.teamPoints, s.subOut.opponentPoints,
           CAST(s.gameId AS VARCHAR), CAST(s.teamId AS VARCHAR), s.athlete, s.position, g.season,
           s.subIn.period IS NOT NULL AND s.subIn.secondsRemaining IS NOT NULL
             AND s.subOut.period IS NOT NULL AND s.subOut.secondsRemaining IS NOT NULL,
           s.copies, gp.event_time, gp.knowable_at,
           getvariable('ev_src'), s.rel, 'gameId=' || s.gameId || ';teamId=' || s.teamId || ';athleteId=' || s.athleteId
               || ';in=' || coalesce(s.subIn.period, -1) || ':' || coalesce(s.subIn.secondsRemaining, -1),
           getvariable('ver'), s.recv, getvariable('proc')
    FROM sub s LEFT JOIN known_athletes k ON k.athlete_id = s.athleteId
    LEFT JOIN core.games g ON g.canonical_game_id = cid('G','game', s.gameId)
    LEFT JOIN game_pit gp ON gp.canonical_game_id = cid('G','game', s.gameId);
    """)
    build_substitution_pairs(con)


def build_substitution_pairs(con) -> None:
    """Derived pairing. Period starts (in at 1200/300) and period ends (out at 0) are boundaries, not swaps."""
    con.execute("""
    INSERT INTO fact.substitution_pairs
    WITH ev AS (
        SELECT canonical_game_id, canonical_team_id, sub_out_period AS period, sub_out_seconds_remaining AS sr,
               'OUT' AS dir, source_athlete_id, canonical_player_id, source_file, received_at
        FROM fact.substitutions WHERE timing_complete AND sub_out_seconds_remaining > 0
        UNION ALL
        SELECT canonical_game_id, canonical_team_id, sub_in_period, sub_in_seconds_remaining, 'IN', source_athlete_id,
               canonical_player_id, source_file, received_at
        FROM fact.substitutions WHERE timing_complete
          AND sub_in_seconds_remaining < CASE WHEN sub_in_period <= 2 THEN 1200 ELSE 300 END),
    grp AS (
        SELECT canonical_game_id, canonical_team_id, period, sr,
               coalesce(list_sort(list(source_athlete_id) FILTER (WHERE dir = 'OUT')), []) AS outs,
               coalesce(list_sort(list(source_athlete_id) FILTER (WHERE dir = 'IN')), []) AS ins,
               any_value(canonical_player_id) FILTER (WHERE dir = 'OUT') AS out_pid,
               any_value(canonical_player_id) FILTER (WHERE dir = 'IN') AS in_pid,
               min(source_file) AS source_file, min(received_at) AS received_at
        FROM ev GROUP BY 1, 2, 3, 4)
    SELECT canonical_game_id, canonical_team_id, period, sr, outs, ins,
           CASE WHEN len(outs) = 1 AND len(ins) = 1 THEN out_pid END,
           CASE WHEN len(outs) = 1 AND len(ins) = 1 THEN in_pid END,
           CASE WHEN len(outs) = 1 AND len(ins) = 1 THEN 'UNIQUE_ONE_FOR_ONE'
                WHEN len(outs) = len(ins) THEN 'AMBIGUOUS_MULTI_SWAP'
                ELSE 'UNBALANCED' END,
           CASE WHEN len(outs) = 1 AND len(ins) = 1 THEN 1.0 ELSE 0.0 END,
           NOT (len(outs) = 1 AND len(ins) = 1),
           getvariable('ev_src'), source_file,
           'derived:' || canonical_game_id || ';team=' || canonical_team_id || ';t=' || period || ':' || sr,
           getvariable('ver'), received_at, getvariable('proc')
    FROM grp;
    """)


# ============================================================================ lineups
def build_lineups(con, root=None) -> None:
    paths = _files(con, ("lineups/game",), root)
    if not paths:
        return
    cols = """{'teamId':'BIGINT','team':'VARCHAR','conference':'VARCHAR','idHash':'VARCHAR',
    'athletes':'STRUCT(id BIGINT, name VARCHAR)[]','totalSeconds':'DOUBLE','pace':'DOUBLE','offenseRating':'DOUBLE',
    'defenseRating':'DOUBLE','netRating':'DOUBLE','teamStats':'JSON','opponentStats':'JSON'}"""
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE lu AS
    SELECT u.* EXCLUDE (filename), f.rel, f.received_at AS recv,
           CAST(json_extract_string(f.params, '$.gameId') AS BIGINT) AS gameId,
           list_transform(u.athletes, a -> a.id) AS ids
    FROM {_read_json(paths, cols)} u JOIN raw_files f ON f.path = u.filename;
    CREATE OR REPLACE TEMP TABLE lu2 AS
    SELECT *, row_number() OVER (ORDER BY rel, teamId, idHash, totalSeconds) AS rid, len(list_filter(ids, x -> x IS NOT NULL)) = 5 AND len(list_distinct(list_filter(ids, x -> x IS NOT NULL))) = 5 AND len(ids) = 5 AS valid,
           CASE WHEN len(ids) <> 5 THEN 'NOT_FIVE_ATHLETES'
                WHEN len(list_filter(ids, x -> x IS NULL)) > 0 THEN 'NULL_ATHLETE_ID'
                WHEN len(list_distinct(ids)) <> 5 THEN 'DUPLICATE_ATHLETE' END AS reason
    FROM lu;
    INSERT INTO fact.lineup_stats
    SELECT DISTINCT ON (cid('G','game', l.gameId), cid('T','team', l.teamId), l.idHash)
           cid('G','game', l.gameId), cid('T','team', l.teamId), l.idHash,
           lm.pids,
           l.totalSeconds, CAST(json_extract(l.teamStats, '$.possessions') AS INTEGER), CAST(json_extract(l.teamStats, '$.points') AS INTEGER),
           CAST(json_extract(l.opponentStats, '$.possessions') AS INTEGER), CAST(json_extract(l.opponentStats, '$.points') AS INTEGER),
           list_transform(l.ids, x -> CAST(x AS VARCHAR)), l.pace, l.offenseRating, l.defenseRating, l.netRating,
           l.teamStats, l.opponentStats, g.season, gp.event_time, gp.knowable_at,
           getvariable('ev_src'), l.rel, 'gameId=' || l.gameId || ';teamId=' || l.teamId || ';idHash=' || l.idHash,
           getvariable('ver'), l.recv, getvariable('proc')
    FROM lu2 l
    LEFT JOIN (SELECT rid, list(coalesce(k.canonical_player_id, 'CBBD_ATHLETE:' || x.aid) ORDER BY x.i) AS pids
               FROM (SELECT rid, unnest(ids) AS aid, unnest(generate_series(1, len(ids))) AS i FROM lu2 WHERE valid) x
               LEFT JOIN known_athletes k ON k.athlete_id = x.aid GROUP BY rid) lm ON lm.rid = l.rid
    LEFT JOIN core.games g ON g.canonical_game_id = cid('G','game', l.gameId)
    LEFT JOIN game_pit gp ON gp.canonical_game_id = cid('G','game', l.gameId)
    WHERE l.valid ORDER BY 1, 2, 3, l.rel;
    INSERT INTO qa.lineup_units_quarantine
    SELECT cid('G','game', gameId), cid('T','team', teamId), idHash, list_transform(ids, x -> CAST(x AS VARCHAR)), totalSeconds,
           CAST(json_extract(teamStats, '$.possessions') AS INTEGER), CAST(json_extract(teamStats, '$.points') AS INTEGER),
           CAST(json_extract(opponentStats, '$.possessions') AS INTEGER), CAST(json_extract(opponentStats, '$.points') AS INTEGER),
           to_json(struct_pack(teamId, idHash, athletes, totalSeconds)), reason,
           getvariable('ev_src'), rel, 'gameId=' || gameId || ';teamId=' || teamId || ';idHash=' || coalesce(idHash, 'NULL'),
           getvariable('ver'), recv, getvariable('proc')
    FROM lu2 WHERE NOT valid;
    """)


# ============================================================================ recruiting / portal / venues
def build_recruiting(con, root=None) -> None:
    paths = _files(con, ("recruiting/players",), root)
    if paths:
        cols = """{'id':'BIGINT','sourceId':'VARCHAR','position':'VARCHAR','schoolId':'BIGINT','school':'VARCHAR',
        'hometown':'STRUCT(city VARCHAR, state VARCHAR, country VARCHAR)','committedTo':'STRUCT(id BIGINT, name VARCHAR, conference VARCHAR)',
        'athleteId':'BIGINT','year':'INTEGER','name':'VARCHAR','heightInches':'DOUBLE','weightPounds':'DOUBLE','stars':'INTEGER',
        'rating':'DOUBLE','ranking':'INTEGER'}"""
        con.execute(f"""
        CREATE OR REPLACE TEMP TABLE rec AS
        SELECT DISTINCT ON (r.id) r.* EXCLUDE (filename), f.rel, f.received_at AS recv
        FROM {_read_json(paths, cols)} r JOIN raw_files f ON f.path = r.filename ORDER BY r.id, f.received_at DESC, f.rel;
        -- candidate: exactly one same-name athlete on the committed team's roster in the recruit's first season
        CREATE OR REPLACE TEMP TABLE rec_cand AS
        SELECT r.id, min(ro.canonical_player_id) AS pid, count(DISTINCT ro.canonical_player_id) AS n
        FROM rec r JOIN core.rosters ro ON ro.canonical_team_id = cid('T','team', r.committedTo.id)
             AND ro.season = r.year + 1 AND lower(ro.player_name) = lower(r.name)
        WHERE r.committedTo.id IS NOT NULL GROUP BY 1;
        INSERT INTO core.recruits
        SELECT CAST(r.id AS VARCHAR), r.sourceId, r.year, r.name, r.position, r.school, CAST(r.schoolId AS VARCHAR),
               r.hometown.city, r.hometown.state, r.hometown.country,
               CAST(r.committedTo.id AS VARCHAR), r.committedTo.name, r.committedTo.conference,
               CASE WHEN r.committedTo.id IS NOT NULL THEN cid('T','team', r.committedTo.id) END,
               r.stars, r.rating, r.ranking, r.heightInches, r.weightPounds,
               CAST(r.athleteId AS VARCHAR), k.canonical_player_id,
               CASE WHEN k.canonical_player_id IS NULL AND c.n = 1 THEN c.pid END,
               CASE WHEN k.canonical_player_id IS NOT NULL THEN 'PROVIDER_ATHLETE_ID'
                    WHEN r.athleteId IS NOT NULL THEN 'UNRESOLVED_PROVIDER_ID_NOT_KNOWN'
                    WHEN c.n = 1 THEN 'CANDIDATE_COMMITTED_ROSTER_NAME'
                    WHEN c.n > 1 THEN 'UNRESOLVED_AMBIGUOUS_NAME'
                    ELSE 'UNRESOLVED_NO_ATHLETE_ID' END,
               CASE WHEN k.canonical_player_id IS NOT NULL THEN 1.0 WHEN c.n = 1 THEN 0.8 ELSE 0.0 END,
               k.canonical_player_id IS NULL AND (coalesce(c.n, 0) >= 1 OR r.athleteId IS NOT NULL),
               getvariable('ev_src'), r.rel, 'recruitId=' || r.id, getvariable('ver'), r.recv, getvariable('proc')
        FROM rec r LEFT JOIN known_athletes k ON k.athlete_id = r.athleteId LEFT JOIN rec_cand c ON c.id = r.id;
        """)
    paths = _files(con, ("recruiting/teams",), root)
    if paths:
        cols = "{'teamId':'BIGINT','team':'VARCHAR','conference':'VARCHAR','year':'INTEGER','ranking':'INTEGER','rating':'DOUBLE'}"
        con.execute(f"""
        INSERT INTO core.team_recruiting_rankings
        SELECT DISTINCT ON (r.teamId, r.year) CAST(r.teamId AS VARCHAR), cid('T','team', r.teamId), r.team, r.conference,
               r.year, r.ranking, r.rating, getvariable('ev_src'), f.rel, 'teamId=' || r.teamId || ';year=' || r.year,
               getvariable('ver'), f.received_at, getvariable('proc')
        FROM {_read_json(paths, cols)} r JOIN raw_files f ON f.path = r.filename
        ORDER BY r.teamId, r.year, f.received_at DESC, f.rel;
        """)
    paths = _files(con, ("recruiting/portal",), root)
    if paths:
        cols = """{'id':'BIGINT','sourceId':'VARCHAR','year':'INTEGER','firstName':'VARCHAR','lastName':'VARCHAR','position':'VARCHAR',
        'origin':'STRUCT(id BIGINT, name VARCHAR, conference VARCHAR)','destination':'STRUCT(id BIGINT, name VARCHAR, conference VARCHAR)',
        'eligibility':'VARCHAR','yearsRemaining':'INTEGER','stars':'INTEGER','rating':'DOUBLE'}"""
        con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tr AS
        SELECT DISTINCT ON (t.id) t.* EXCLUDE (filename), f.rel, f.received_at AS recv
        FROM {_read_json(paths, cols)} t JOIN raw_files f ON f.path = t.filename ORDER BY t.id, f.received_at DESC, f.rel;
        CREATE OR REPLACE TEMP TABLE tr_origin AS
        SELECT t.id, count(DISTINCT ro.canonical_player_id) AS n, min(ro.canonical_player_id) AS pid
        FROM tr t JOIN core.rosters ro ON ro.canonical_team_id = cid('T','team', t.origin.id) AND ro.season = t.year
             AND lower(ro.first_name) = lower(t.firstName) AND lower(ro.last_name) = lower(t.lastName)
        WHERE t.origin.id IS NOT NULL GROUP BY 1;
        CREATE OR REPLACE TEMP TABLE tr_dest AS
        SELECT t.id, bool_or(ro.canonical_player_id = o.pid) AS corroborated
        FROM tr t JOIN tr_origin o ON o.id = t.id AND o.n = 1
        JOIN core.rosters ro ON ro.canonical_team_id = cid('T','team', t.destination.id) AND ro.season = t.year + 1
        GROUP BY 1;
        INSERT INTO core.transfers
        SELECT CAST(t.id AS VARCHAR), t.sourceId, t.year, t.firstName, t.lastName, t.position,
               CAST(t.origin.id AS VARCHAR), t.origin.name, t.origin.conference,
               CASE WHEN t.origin.id IS NOT NULL THEN cid('T','team', t.origin.id) END,
               CAST(t.destination.id AS VARCHAR), t.destination.name, t.destination.conference,
               CASE WHEN t.destination.id IS NOT NULL THEN cid('T','team', t.destination.id) END,
               t.eligibility, t.yearsRemaining, t.stars, t.rating,
               CASE WHEN o.n = 1 AND d.corroborated THEN o.pid END,
               CASE WHEN o.n = 1 AND NOT coalesce(d.corroborated, FALSE) THEN o.pid END,
               CASE WHEN o.n = 1 AND d.corroborated THEN 'RESOLVED_ROSTER_CORROBORATED'
                    WHEN o.n = 1 THEN 'CANDIDATE_ORIGIN_ROSTER_NAME'
                    WHEN o.n > 1 THEN 'UNRESOLVED_AMBIGUOUS_NAME'
                    WHEN t.origin.id IS NULL THEN 'UNRESOLVED_NO_ORIGIN_TEAM'
                    ELSE 'UNRESOLVED_NO_ROSTER_MATCH' END,
               CASE WHEN o.n = 1 AND d.corroborated THEN 0.99 WHEN o.n = 1 THEN 0.7 ELSE 0.0 END,
               NOT (coalesce(o.n, 0) = 1 AND coalesce(d.corroborated, FALSE)) AND coalesce(o.n, 0) >= 1,
               getvariable('ev_src'), t.rel, 'transferId=' || t.id, getvariable('ver'), t.recv, getvariable('proc')
        FROM tr t LEFT JOIN tr_origin o ON o.id = t.id LEFT JOIN tr_dest d ON d.id = t.id;
        """)
    paths = _files(con, ("venues",), root)
    if paths:
        cols = "{'id':'BIGINT','sourceId':'VARCHAR','name':'VARCHAR','city':'VARCHAR','state':'VARCHAR','country':'VARCHAR'}"
        con.execute(f"""
        INSERT INTO core.venue_directory
        SELECT DISTINCT ON (v.id) cid('V','venue', v.id), CAST(v.id AS VARCHAR), v.sourceId, v.name, v.city, v.state, v.country,
               cid('V','venue', v.id) IN (SELECT canonical_venue_id FROM core.venues),
               getvariable('ev_src'), f.rel, 'venueId=' || v.id, getvariable('ver'), f.received_at, getvariable('proc')
        FROM {_read_json(paths, cols)} v JOIN raw_files f ON f.path = v.filename ORDER BY v.id, f.received_at DESC, f.rel;
        """)


# ============================================================================ 2N reconciliation + 2P defects
def build_reconciliation(con) -> None:
    con.execute("""
    INSERT INTO qa.pbp_game_reconciliation
    WITH p AS (
        SELECT canonical_game_id, play_sequence, period, seconds_remaining, clock, home_score, away_score,
               scoring_play, score_value, is_home_team,
               lag(home_score) OVER w AS ph, lag(away_score) OVER w AS pa,
               lag(period) OVER w AS pp, lag(seconds_remaining) OVER w AS ps
        FROM fact.plays WINDOW w AS (PARTITION BY canonical_game_id ORDER BY play_sequence)),
    agg AS (
        SELECT canonical_game_id, count(*) AS plays, count(DISTINCT period) AS periods_present, max(period) AS max_period,
               avg(CASE WHEN period IS NOT NULL AND seconds_remaining IS NOT NULL AND clock IS NOT NULL THEN 1.0 ELSE 0.0 END) AS pcc,
               arg_max(home_score, play_sequence) AS fh, arg_max(away_score, play_sequence) AS fa,
               count(*) FILTER (WHERE home_score < ph OR away_score < pa) AS decreases,
               count(*) FILTER (WHERE scoring_play AND (home_score + away_score) - (ph + pa) <> score_value) AS inc_mis,
               sum(score_value) FILTER (WHERE scoring_play AND is_home_team) AS sh,
               sum(score_value) FILTER (WHERE scoring_play AND NOT is_home_team) AS sa,
               bool_or(seconds_remaining = 0 AND period = (SELECT max(period) FROM fact.plays x WHERE x.canonical_game_id = p.canonical_game_id)) AS has_terminal,
               count(*) FILTER (WHERE period < pp OR (period = pp AND seconds_remaining > ps)) AS clock_viol
        FROM p GROUP BY 1)
    SELECT a.canonical_game_id, g.season, a.plays, a.periods_present,
           CASE WHEN g.overtime_count IS NOT NULL THEN 2 + g.overtime_count END,
           a.pcc, a.fh, a.fa, g.home_points, g.away_points,
           CASE WHEN g.home_points IS NOT NULL THEN a.fh = g.home_points AND a.fa = g.away_points END,
           a.decreases, a.inc_mis, a.sh, a.sa,
           CASE WHEN g.home_points IS NOT NULL THEN a.sh = g.home_points AND a.sa = g.away_points END,
           NOT coalesce(a.has_terminal, FALSE), a.clock_viol,
           CASE WHEN g.canonical_game_id IS NULL THEN 'GAME_NOT_IN_CANONICAL'
                WHEN g.home_points IS NULL OR (g.home_points = 0 AND g.away_points = 0) THEN 'NO_OFFICIAL_SCORE'
                WHEN NOT (a.fh = g.home_points AND a.fa = g.away_points) THEN 'FINAL_SCORE_MISMATCH'
                WHEN g.overtime_count IS NOT NULL AND a.periods_present < 2 + g.overtime_count THEN 'INCOMPLETE_PERIODS'
                WHEN a.decreases > 0 THEN 'RECONCILED_WITH_SCORE_DECREASES'
                ELSE 'RECONCILED' END
    FROM agg a LEFT JOIN core.games g USING (canonical_game_id);
    """)


DEFECT_SQL = [
    # family, reason, severity, excluded, SQL producing (canonical_game_id, season, record_key, detail)
    ("PBP", "GAME_NOT_IN_CANONICAL", "ERROR", True, """SELECT canonical_game_id, season, 'game=' || canonical_game_id,
        plays || ' plays for a CBBD game id absent from core.games' FROM qa.pbp_game_reconciliation WHERE coverage_quality = 'GAME_NOT_IN_CANONICAL'"""),
    ("PBP", "FINAL_SCORE_MISMATCH", "ERROR", True, """SELECT canonical_game_id, season, 'game=' || canonical_game_id,
        'pbp ' || pbp_final_home || '-' || pbp_final_away || ' vs official ' || official_home || '-' || official_away
        FROM qa.pbp_game_reconciliation WHERE coverage_quality = 'FINAL_SCORE_MISMATCH'"""),
    ("PBP", "INCOMPLETE_PERIODS", "ERROR", True, """SELECT canonical_game_id, season, 'game=' || canonical_game_id,
        periods_present || ' of ' || periods_expected || ' periods' FROM qa.pbp_game_reconciliation WHERE coverage_quality = 'INCOMPLETE_PERIODS'"""),
    ("PBP", "SCORE_DECREASE", "WARN", False, """SELECT canonical_game_id, season, 'game=' || canonical_game_id,
        score_decreases || ' plays where a running score decreases' FROM qa.pbp_game_reconciliation WHERE score_decreases > 0"""),
    ("PBP", "CONFLICTING_DUPLICATE_EVENT", "ERROR", True, """SELECT cid('G','game', gameId), NULL, 'gameId=' || gameId || ';playId=' || id,
        variants || ' different contents across ' || copies || ' payload copies' FROM play_dup_all WHERE variants > 1"""),
    ("ONFLOOR", "ONFLOOR_INVALID", "WARN", True, """SELECT canonical_game_id, season, 'gameId=' || source_game_id || ';playId=' || source_play_id,
        on_floor_quality FROM fact.plays WHERE on_floor_quality NOT IN ('VALID_5V5', 'NO_ONFLOOR')"""),
    ("SHOT", "SHOT_NO_SHOOTER", "WARN", True, """SELECT canonical_game_id, season, 'gameId=' || split_part(source_record_key, ';', 1)[8:] || ';playId=' || source_play_id,
        'shotInfo without shooter id' FROM fact.shots WHERE shooter_source_athlete_id IS NULL"""),
    ("SHOT", "SHOOTER_UNRESOLVED", "WARN", True, """SELECT canonical_game_id, season, 'gameId=' || split_part(source_record_key, ';', 1)[8:] || ';playId=' || source_play_id,
        'shooter athleteId ' || shooter_source_athlete_id || ' not a known player' FROM fact.shots
        WHERE shooter_source_athlete_id IS NOT NULL AND shooter_player_id IS NULL"""),
    ("SHOT", "ASSISTED_WITHOUT_ASSISTER", "WARN", False, """SELECT canonical_game_id, season, 'gameId=' || split_part(source_record_key, ';', 1)[8:] || ';playId=' || source_play_id,
        'assisted=true but no assister id' FROM fact.shots WHERE assisted AND assister_source_athlete_id IS NULL"""),
    ("SHOT", "INVALID_COORDINATES", "WARN", True, """SELECT canonical_game_id, season, 'gameId=' || split_part(source_record_key, ';', 1)[8:] || ';playId=' || source_play_id,
        'x=' || location_x || ' y=' || location_y FROM fact.shots WHERE location_valid = FALSE"""),
    ("SHOT", "SHOOTER_NOT_ON_FLOOR", "WARN", True, """SELECT canonical_game_id, season, 'gameId=' || split_part(source_record_key, ';', 1)[8:] || ';playId=' || source_play_id,
        'shooter absent from his team''s VALID_5V5 floor set' FROM fact.shots WHERE shooter_on_team_floor = FALSE"""),
    ("SUBSTITUTION", "SUB_TIMING_INCOMPLETE", "WARN", True, """SELECT canonical_game_id, season, source_record_key, 'missing sub-in/out period or clock'
        FROM fact.substitutions WHERE NOT timing_complete"""),
    ("SUBSTITUTION", "SUB_OUT_BEFORE_IN", "ERROR", True, """SELECT canonical_game_id, season, source_record_key, 'stint ends before it starts'
        FROM fact.substitutions WHERE timing_complete AND (sub_out_period < sub_in_period
             OR (sub_out_period = sub_in_period AND sub_out_seconds_remaining > sub_in_seconds_remaining))"""),
    ("SUBSTITUTION", "SUB_ATHLETE_UNRESOLVED", "WARN", False, """SELECT canonical_game_id, season, source_record_key,
        'athleteId ' || source_athlete_id || ' not a known player' FROM fact.substitutions WHERE canonical_player_id IS NULL"""),
    ("SUBSTITUTION", "SUB_GAME_NOT_IN_CANONICAL", "ERROR", True, """SELECT canonical_game_id, season, source_record_key, 'game absent from core.games'
        FROM fact.substitutions WHERE season IS NULL"""),
    ("LINEUP", "LINEUP_INVALID_UNIT", "WARN", True, """SELECT canonical_game_id, NULL, source_record_key, defect_reason FROM qa.lineup_units_quarantine"""),
]


def build_defects(con) -> None:
    for fam, reason, sev, excl, sql in DEFECT_SQL:
        if "play_dup_all" in sql and not _has(con, "play_dup_all"):
            continue
        con.execute(f"""INSERT INTO qa.source_defects
                        SELECT '{fam}', '{reason}', '{sev}', x.*, {str(excl).upper()}, '{VERSION}' FROM ({sql}) x""")
    # overlapping stints of one athlete in one game (impossible transition) -- game-level exclusion
    con.execute(f"""
    INSERT INTO qa.source_defects
    WITH s AS (SELECT canonical_game_id, season, source_athlete_id, source_record_key,
                      (CASE WHEN sub_in_period <= 2 THEN (sub_in_period - 1) * 1200 + 1200 - sub_in_seconds_remaining
                            ELSE 2400 + (sub_in_period - 3) * 300 + 300 - sub_in_seconds_remaining END) AS t_in,
                      (CASE WHEN sub_out_period <= 2 THEN (sub_out_period - 1) * 1200 + 1200 - sub_out_seconds_remaining
                            ELSE 2400 + (sub_out_period - 3) * 300 + 300 - sub_out_seconds_remaining END) AS t_out
               FROM fact.substitutions WHERE timing_complete)
    SELECT 'SUBSTITUTION', 'SUB_OVERLAPPING_STINTS', 'ERROR', a.canonical_game_id, a.season, a.source_record_key,
           'overlaps ' || b.source_record_key, TRUE, '{VERSION}'
    FROM s a JOIN s b ON a.canonical_game_id = b.canonical_game_id AND a.source_athlete_id = b.source_athlete_id
     AND a.source_record_key < b.source_record_key AND a.t_in < b.t_out AND b.t_in < a.t_out;
    """)


# ============================================================================ entry point
def build_events(con: duckdb.DuckDBPyConnection, root: Path | None = None) -> None:
    con.execute("SET VARIABLE ev_src = ?", [SRC])
    for var, default in (("ver", C.INGESTION_VERSION),):
        if con.execute(f"SELECT getvariable('{var}') IS NULL").fetchone()[0]:
            con.execute(f"SET VARIABLE {var} = ?", [default])
    if con.execute("SELECT getvariable('proc') IS NULL").fetchone()[0]:
        con.execute("SET VARIABLE proc = now()")
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET memory_limit = '18GB'")
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    con.execute(f"SET temp_directory = '{C.DATA / 'duckdb_tmp'}'")
    _game_pit(con)
    build_rosters(con, root)
    _known_players(con)
    build_plays(con, root)
    build_substitutions(con, root)
    build_lineups(con, root)
    build_recruiting(con, root)
    build_reconciliation(con)
    build_defects(con)
    for t in ("pl", "of_norm", "of_class", "pp_norm", "play_lists", "sub", "lu", "lu2"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
