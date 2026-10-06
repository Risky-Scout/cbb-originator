"""Steps 4, 5, 7, 8 — deterministic Starter Pack -> canonical build.

    .venv/bin/python -m cbb_platform.build_canonical

What it does, in order:
  1. Verifies the immutable Starter Pack still matches its registered SHA-256 manifest.
  2. Creates a fresh DuckDB file from cbb_platform/schema.sql.
  3. Exposes the Starter Pack CSVs as lossless all-VARCHAR *staging views* (no copy is made;
     the Starter Pack is a read-only mounted upstream, see config.py).
  4. Transforms staging -> canonical tables (core.*, fact.*, metadata.*, qa.*).
  5. Exports every non-empty canonical table to data/canonical/<schema>/<table>.parquet and
     records an order-independent content fingerprint per table in metadata.data_versions.

Determinism: canonical rows depend only on the immutable inputs plus the persisted
registration time (received_at). Only `processed_at` varies between builds, and it is
excluded from the fingerprints — so two builds with the same inputs must produce identical
fingerprints (tested in tests/test_reproducibility.py).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import duckdb

from cbb_platform import config as C
from cbb_platform.audit_starter_pack import starter_pack_manifest

SRC = "CBBD_STARTER_PACK"   # source_system for every Starter Pack-derived row

# Canonical ID contract (CANONICAL_SCHEMA.md §2): prefix + first 16 hex chars of
# sha256('<anchor provider>|<entity>|<anchor provider id>'). Opaque, deterministic, and stable
# as long as the anchoring provider id is stable. CBBD is the anchor provider for every entity.
ID_MACROS = """
CREATE OR REPLACE MACRO cid(prefix, entity, provider_id) AS
    prefix || '-' || substr(sha256('CBBD|' || entity || '|' || CAST(provider_id AS VARCHAR)), 1, 16);
-- Starter Pack numbers arrive as text like '37.0'. Counting stats must be whole numbers;
-- tests/test_canonical_qa.py proves no fractional value is silently rounded here.
CREATE OR REPLACE MACRO as_int(x) AS CAST(TRY_CAST(x AS DOUBLE) AS INTEGER);
CREATE OR REPLACE MACRO as_dbl(x) AS TRY_CAST(x AS DOUBLE);
CREATE OR REPLACE MACRO as_bool(x) AS CASE x WHEN 'True' THEN TRUE WHEN 'False' THEN FALSE END;
"""


# ---------------------------------------------------------------------------
# Immutable-upstream registration (gives Starter Pack rows a fixed received_at)
# ---------------------------------------------------------------------------
def register_starter_pack() -> dict:
    """Create (once) or verify the Starter Pack registration record.

    received_at for Starter Pack rows = the moment we first registered the mount. It is
    persisted so rebuilds stay deterministic. If the Starter Pack data bytes ever change, this
    raises instead of silently ingesting different data under the old registration.
    (.DS_Store files are Finder metadata and are excluded from the data digest.)
    """
    path = C.MANIFESTS / "starter_pack_registration.json"
    data_only = {k: v for k, v in starter_pack_manifest().items()
                 if k.startswith("data/") and not k.endswith(".DS_Store")}
    digest = _manifest_digest(data_only)
    if path.exists():
        reg = json.loads(path.read_text())
        if reg["data_manifest_digest"] != digest:
            raise RuntimeError("Starter Pack data files differ from their registered manifest — refusing to build.")
        return reg
    C.MANIFESTS.mkdir(parents=True, exist_ok=True)
    reg = {
        "registered_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "starter_pack_root": str(C.STARTER_PACK_ROOT),
        "data_manifest_digest": digest,
        "data_file_count": len(data_only),
        "note": "received_at for every Starter Pack-derived canonical row. Written once; never rewritten.",
    }
    path.write_text(json.dumps(reg, indent=1))
    return reg


def _manifest_digest(manifest: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Staging: lossless all-VARCHAR views over the immutable CSVs
# ---------------------------------------------------------------------------
def create_staging_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    for family, (pattern, _key) in C.STARTER_FAMILIES.items():
        glob = str(C.STARTER_PACK_DATA / pattern)
        # all_varchar: values are kept exactly as written; union_by_name: column order differs
        # by season (measured); filename: lineage back to the exact source file.
        con.execute(f"""CREATE OR REPLACE VIEW staging.sp_{family} AS
            SELECT * REPLACE (replace(filename, '{C.STARTER_PACK_ROOT}/', '') AS filename)
            FROM read_csv('{glob}', all_varchar=true, union_by_name=true, filename=true)""")


# ---------------------------------------------------------------------------
# Canonical transforms (plain SQL, one block per table, in dependency order)
# ---------------------------------------------------------------------------
def build_core(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    INSERT INTO core.conferences
    SELECT cid('C','conference',id), name, abbreviation, short_name,
           getvariable('src'), filename, 'id=' || id, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM staging.sp_conferences;

    INSERT INTO metadata.conference_id_map
    SELECT 'CBBD', id, cid('C','conference',id), 'ANCHOR', 1.0, FALSE, 'conferences.csv', getvariable('ver')
    FROM staging.sp_conferences;

    INSERT INTO core.teams
    SELECT cid('T','team',id), team, mascot, nickname, abbreviation, display_name,
           getvariable('src'), filename, 'id=' || id, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM staging.sp_teams;

    INSERT INTO metadata.team_id_map
    SELECT 'CBBD', id, cid('T','team',id), NULL, 'ANCHOR', 1.0, FALSE, 'teams.csv', getvariable('ver')
    FROM staging.sp_teams;

    -- Venues appear only inside games.csv. venue_id arrives as '371.0'.
    INSERT INTO core.venues
    -- Latest game's spelling wins; (start_date, id) makes the choice deterministic.
    SELECT cid('V','venue', as_int(venue_id)), arg_max(venue, start_date || id),
           arg_max(venue_city, start_date || id), arg_max(venue_state, start_date || id),
           arg_max(venue_country, start_date || id),
           getvariable('src'), 'data/games.csv', 'venue_id=' || as_int(venue_id), getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM staging.sp_games WHERE venue_id IS NOT NULL GROUP BY as_int(venue_id);
    """)


def build_games(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    -- One row per team-game after collapsing the measured conference fan-out.
    CREATE TEMP TABLE tg AS
    SELECT DISTINCT ON (gameId, teamId) * FROM staging.sp_team_game_stats
    ORDER BY gameId, teamId, conference NULLS LAST, opponentConference NULLS LAST, filename;

    CREATE TEMP TABLE game_box AS
    SELECT gameId AS game_id, max(as_int(gameMinutes)) AS game_minutes,
           bool_or(as_bool(startTimeTbd)) AS start_time_tbd
    FROM tg GROUP BY 1;

    CREATE TEMP TABLE player_box_games AS SELECT DISTINCT game_id FROM staging.sp_player_game_stats;

    -- D-I proxy: a team-season row exists in team_season_stats (measured: 364 teams in 2025).
    CREATE TEMP TABLE di AS SELECT DISTINCT teamId AS team_id, as_int(season) AS season
    FROM staging.sp_team_season_stats;
    CREATE TEMP TABLE seasons_with_stats AS SELECT DISTINCT season FROM di;

    CREATE TEMP TABLE g AS
    SELECT s.*,
           as_int(s.season) AS season_i,
           -- games.csv start_date is naive UTC: 804 of 804 games independently timed by BDL agree
           -- to the minute (both DST regimes). The box-score files' '+00:00' stamps are NOT used:
           -- they equal this value re-read as US/Eastern, i.e. they are 4-5 hours late (measured).
           timezone('UTC', try_strptime(s.start_date, '%Y-%m-%d %H:%M:%S')) AS tip_utc
    FROM staging.sp_games s;
    -- The US/Eastern calendar date of the game (date-only rows keep their supplied date).
    ALTER TABLE g ADD COLUMN date_et DATE;
    UPDATE g SET date_et = coalesce(CAST(timezone('America/New_York', tip_utc) AS DATE),
                                    CAST(substr(start_date, 1, 10) AS DATE));

    INSERT INTO core.games
    SELECT cid('G','game', g.id), g.season_i, g.season_label, g.season_type, g.game_type, g.tournament, g.notes,
           g.tip_utc,
           CASE WHEN g.tip_utc IS NULL THEN 'DATE_ONLY'
                WHEN gb.start_time_tbd THEN 'TBD_FLAG'
                ELSE 'EXACT' END,
           g.date_et, as_bool(g.neutral_site), as_bool(g.conference_game),
           cid('T','team', g.home_team_id), cid('T','team', g.away_team_id),
           CASE WHEN g.home_conference_id IS NOT NULL THEN cid('C','conference', as_int(g.home_conference_id)) END,
           CASE WHEN g.away_conference_id IS NOT NULL THEN cid('C','conference', as_int(g.away_conference_id)) END,
           as_int(g.home_seed), as_int(g.away_seed),
           CASE WHEN g.venue_id IS NOT NULL THEN cid('V','venue', as_int(g.venue_id)) END,
           as_int(g.home_points), as_int(g.away_points),
           CAST(g.home_period_points AS INTEGER[]), CAST(g.away_period_points AS INTEGER[]),
           CASE WHEN gb.game_minutes IS NOT NULL THEN (gb.game_minutes - 40) // 5 END,
           CASE WHEN g.season_i NOT IN (SELECT season FROM seasons_with_stats) THEN 'UNKNOWN'
                WHEN hd.team_id IS NOT NULL AND ad.team_id IS NOT NULL THEN 'DI_VS_DI'
                WHEN hd.team_id IS NOT NULL OR  ad.team_id IS NOT NULL THEN 'DI_VS_NON_DI'
                ELSE 'NON_DI_ONLY' END,
           gb.game_id IS NOT NULL, pb.game_id IS NOT NULL,
           getvariable('src'), g.filename, 'id=' || g.id, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM g
    LEFT JOIN game_box gb ON gb.game_id = g.id
    LEFT JOIN player_box_games pb ON pb.game_id = g.id
    LEFT JOIN di hd ON hd.team_id = g.home_team_id AND hd.season = g.season_i
    LEFT JOIN di ad ON ad.team_id = g.away_team_id AND ad.season = g.season_i;

    INSERT INTO metadata.game_id_map
    SELECT 'CBBD', id, cid('G','game', id), 'ANCHOR', 1.0, FALSE, 'games.csv', getvariable('ver') FROM staging.sp_games;

    -- Single retrospective schedule version: we do not know when this schedule was knowable.
    INSERT INTO core.game_schedule_versions
    SELECT canonical_game_id, 1, scheduled_tip_utc, tip_time_quality, NULL, NULL, NULL, TRUE,
           source_system, source_file, source_record_key, ingestion_version, received_at, processed_at
    FROM core.games;

    -- Team-season membership for every team that played a game that season.
    INSERT INTO core.team_seasons
    WITH appearances AS (
        SELECT season_i AS season, home_team_id AS team_id, home_conference_id AS conf_id FROM g
        UNION ALL SELECT season_i, away_team_id, away_conference_id FROM g),
    conf_counts AS (
        SELECT season, team_id, as_int(conf_id) AS conf_id, count(*) AS n FROM appearances GROUP BY 1, 2, 3),
    conf_mode AS (   -- most frequent non-null conference id; ties broken by the smallest id
        SELECT season, team_id,
               arg_max(conf_id, n * 100000 - conf_id) FILTER (WHERE conf_id IS NOT NULL) AS conf_id
        FROM conf_counts GROUP BY 1, 2)
    SELECT cid('T','team', cm.team_id), cm.season,
           CASE WHEN cm.conf_id IS NOT NULL THEN cid('C','conference', cm.conf_id) END,
           CASE WHEN cm.conf_id IS NOT NULL THEN 'MODE_OF_GAME_CONFERENCE_IDS' ELSE 'NO_CONFERENCE_ID' END,
           CASE WHEN cm.season NOT IN (SELECT season FROM seasons_with_stats) THEN NULL
                ELSE di.team_id IS NOT NULL END,
           CASE WHEN cm.season NOT IN (SELECT season FROM seasons_with_stats) THEN 'NO_SEASON_STATS_FOR_SEASON'
                ELSE 'HAS_TEAM_SEASON_STATS_ROW' END,
           getvariable('src'), 'data/games.csv', 'team_id=' || cm.team_id || ';season=' || cm.season, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM conf_mode cm LEFT JOIN di ON di.team_id = cm.team_id AND di.season = cm.season;
    """)


# PIT rule HR-1 (POINT_IN_TIME_POLICY.md): when is a completed game's official box score
# treated as historically knowable?
#   EXACT tip time               -> tip + 4 hours
#   DATE_ONLY / TBD_FLAG tip time -> 06:00 US/Eastern on the day AFTER the game's ET date
KNOWABLE_SQL = """
CREATE OR REPLACE TEMP TABLE game_pit AS
SELECT canonical_game_id, season, scheduled_tip_utc AS event_time,
       CASE WHEN tip_time_quality = 'EXACT' THEN scheduled_tip_utc + INTERVAL 4 HOUR
            ELSE timezone('America/New_York', CAST(game_date_et + INTERVAL 1 DAY AS TIMESTAMP) + INTERVAL 6 HOUR)
       END AS knowable_at
FROM core.games;
-- PIT rule HR-3: end-of-season aggregates become knowable only after the season's last game.
CREATE OR REPLACE TEMP TABLE season_pit AS
SELECT season, max(knowable_at) AS season_knowable_at FROM game_pit GROUP BY 1;
"""


def build_team_games(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(KNOWABLE_SQL)
    con.execute("""
    INSERT INTO fact.team_games
    SELECT cid('G','game', t.gameId), cid('T','team', t.teamId), cid('T','team', t.opponentId), as_int(t.season),
           as_bool(t.isHome), as_bool(t.neutralSite), as_int(t.gameMinutes), (as_int(t.gameMinutes) - 40) // 5,
           as_int(t.teamStats_points_total), as_int(t.teamStats_fieldGoals_made), as_int(t.teamStats_fieldGoals_attempted),
           as_int(t.teamStats_twoPointFieldGoals_made), as_int(t.teamStats_twoPointFieldGoals_attempted),
           as_int(t.teamStats_threePointFieldGoals_made), as_int(t.teamStats_threePointFieldGoals_attempted),
           as_int(t.teamStats_freeThrows_made), as_int(t.teamStats_freeThrows_attempted),
           as_int(t.teamStats_rebounds_offensive), as_int(t.teamStats_rebounds_defensive), as_int(t.teamStats_rebounds_total),
           as_int(t.teamStats_assists), as_int(t.teamStats_steals), as_int(t.teamStats_blocks),
           as_int(t.teamStats_turnovers_total), as_int(t.teamStats_turnovers_teamTotal),
           as_int(t.teamStats_fouls_total), as_int(t.teamStats_fouls_technical), as_int(t.teamStats_fouls_flagrant),
           as_dbl(t.teamStats_possessions), as_dbl(t.pace),
           as_int(t.teamStats_points_inPaint), as_int(t.teamStats_points_fastBreak), as_int(t.teamStats_points_offTurnovers),
           as_int(t.teamStats_points_largestLead),
           list_filter([as_int(t.teamStats_points_byPeriod_0), as_int(t.teamStats_points_byPeriod_1),
                        as_int(t.teamStats_points_byPeriod_2), as_int(t.teamStats_points_byPeriod_3),
                        as_int(t.teamStats_points_byPeriod_4), as_int(t.teamStats_points_byPeriod_5),
                        as_int(t.teamStats_points_byPeriod_6), as_int(t.teamStats_points_byPeriod_7)], x -> x IS NOT NULL),
           NULL, NULL, gp.event_time, gp.knowable_at,
           getvariable('src'), t.filename, 'gameId=' || t.gameId || ';teamId=' || t.teamId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM tg t JOIN game_pit gp ON gp.canonical_game_id = cid('G','game', t.gameId);

    -- Box-score games missing from games.csv cannot join a canonical game: surfaced, not dropped silently.
    INSERT INTO qa.exceptions
    SELECT DISTINCT 'TEAM_BOX_GAME_NOT_IN_GAMES_CSV', 'WARN', 'gameId=' || gameId,
           'team_game_stats row has no games.csv record; excluded from fact.team_games', getvariable('src'), getvariable('ver')
    FROM tg WHERE cid('G','game', gameId) NOT IN (SELECT canonical_game_id FROM core.games);

    INSERT INTO qa.exceptions
    SELECT 'DUPLICATE_FANOUT_COLLAPSED', 'INFO', 'gameId=' || gameId || ';teamId=' || teamId,
           count(*) || ' source rows differing only in conference/opponentConference', getvariable('src'), getvariable('ver')
    FROM staging.sp_team_game_stats GROUP BY gameId, teamId HAVING count(*) > 1;
    """)


def build_players_and_player_games(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    -- Persistent CBBD player ids exist ONLY in the season files (measured). They anchor core.players.
    CREATE TEMP TABLE ps AS
    SELECT DISTINCT ON (season, teamId, athleteId) * FROM staging.sp_player_season_stats
    ORDER BY season, teamId, athleteId, conference NULLS LAST, filename;
    CREATE TEMP TABLE pss AS
    SELECT DISTINCT ON (season, teamId, athleteId) * FROM staging.sp_player_season_shooting_stats
    ORDER BY season, teamId, athleteId, conference NULLS LAST, filename;

    CREATE TEMP TABLE athlete_anchor AS
    SELECT athleteId, as_int(season) AS season, name, filename FROM ps
    UNION ALL SELECT athleteId, as_int(season), athleteName, filename FROM pss;

    INSERT INTO core.players
    SELECT cid('P','athlete', athleteId), arg_max(name, lpad(season::VARCHAR, 4, '0') || filename),
           min(season), max(season),
           getvariable('src'), arg_max(filename, lpad(season::VARCHAR, 4, '0') || filename), 'athleteId=' || athleteId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM athlete_anchor GROUP BY athleteId;

    INSERT INTO metadata.player_id_map
    SELECT 'CBBD', athleteId, cid('P','athlete', athleteId), NULL, NULL, 'ANCHOR', 1.0, FALSE,
           'persistent athleteId from player_season_stats / player_season_shooting_stats', getvariable('ver')
    FROM (SELECT DISTINCT athleteId FROM athlete_anchor);

    -- player_game_stats.athleteId is a per-RECORD id (measured), so player-game rows are resolved
    -- through the composite natural key (season, team, exact name) -> persistent athleteId.
    -- Exact string equality only. Ambiguous or missing keys stay UNRESOLVED (NULL player id).
    CREATE TEMP TABLE name_key AS
    SELECT as_int(season) AS season, teamId, name,
           count(DISTINCT athleteId) AS n_ids, min(athleteId) AS athleteId
    FROM ps GROUP BY 1, 2, 3;

    -- Collapse the conference fan-out: one row per source player-game record.
    CREATE TEMP TABLE pg AS
    SELECT DISTINCT ON (game_id, athleteId) * FROM staging.sp_player_game_stats
    ORDER BY game_id, athleteId, conference NULLS LAST, opponent_conference NULLS LAST, filename;

    -- Two different source records with the same (game, team, name) cannot be told apart.
    CREATE TEMP TABLE same_name_in_game AS
    SELECT game_id, team_id, name FROM pg GROUP BY 1, 2, 3 HAVING count(*) > 1;

    CREATE TEMP TABLE pg_resolved AS
    SELECT pg.*, coalesce(nk.n_ids, 0) AS name_key_ids, nk.athleteId AS name_key_athlete,
           sn.game_id IS NOT NULL AS same_name_in_game,
           'PG-' || substr(sha256('CBBD|player_game_record|' || pg.game_id || '|' || pg.athleteId), 1, 20) AS player_game_key,
           CASE WHEN sn.game_id IS NOT NULL THEN 'UNRESOLVED_AMBIGUOUS'
                WHEN nk.n_ids = 1 THEN 'RESOLVED'
                WHEN nk.n_ids > 1 THEN 'UNRESOLVED_AMBIGUOUS'
                ELSE 'UNRESOLVED_NO_MATCH' END AS identity_status,
           CASE WHEN sn.game_id IS NULL AND nk.n_ids = 1 THEN cid('P','athlete', nk.athleteId) END AS canonical_player_id
    FROM pg
    LEFT JOIN name_key nk ON nk.season = as_int(pg.season) AND nk.teamId = pg.team_id AND nk.name = pg.name
    LEFT JOIN same_name_in_game sn ON sn.game_id = pg.game_id AND sn.team_id = pg.team_id AND sn.name = pg.name;

    -- Measured source defect: in some games one team's box lines are mirrored onto the opponent,
    -- so the same player appears for BOTH teams. The Starter Pack cannot say which side is true,
    -- so both copies are left unresolved (never two box rows for one canonical player-game).
    CREATE TEMP TABLE both_teams AS
    SELECT game_id, canonical_player_id FROM pg_resolved WHERE canonical_player_id IS NOT NULL
    GROUP BY 1, 2 HAVING count(DISTINCT team_id) > 1;
    INSERT INTO qa.exceptions
    SELECT 'PLAYER_ON_BOTH_TEAMS_IN_GAME', 'WARN', 'game_id=' || game_id || ';player=' || canonical_player_id,
           'identical player listed for both teams (mirrored box lines); both rows left UNRESOLVED',
           'CBBD_STARTER_PACK', getvariable('ver')
    FROM both_teams;
    UPDATE pg_resolved SET identity_status = 'UNRESOLVED_SAME_PLAYER_BOTH_TEAMS', canonical_player_id = NULL
    WHERE (game_id, canonical_player_id) IN (SELECT game_id, canonical_player_id FROM both_teams);

    INSERT INTO fact.player_games
    SELECT r.player_game_key, cid('G','game', r.game_id), cid('T','team', r.team_id), cid('T','team', r.opponent_id),
           r.canonical_player_id, r.identity_status, as_int(r.season), as_bool(r.is_home),
           r.name, r.position, as_bool(r.starter), as_int(r.minutes),
           as_int(r.points), as_int(r.fieldGoals_made), as_int(r.fieldGoals_attempted),
           as_int(r.twoPointFieldGoals_made), as_int(r.twoPointFieldGoals_attempted),
           as_int(r.threePointFieldGoals_made), as_int(r.threePointFieldGoals_attempted),
           as_int(r.freeThrows_made), as_int(r.freeThrows_attempted),
           as_int(r.rebounds_offensive), as_int(r.rebounds_defensive), as_int(r.rebounds_total),
           as_int(r.assists), as_int(r.steals), as_int(r.blocks), as_int(r.turnovers), as_int(r.fouls),
           as_bool(r.ejected), as_dbl(r.usage), NULL,
           gp.event_time, gp.knowable_at,
           getvariable('src'), r.filename, 'game_id=' || r.game_id || ';athleteId_record=' || r.athleteId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM pg_resolved r JOIN game_pit gp ON gp.canonical_game_id = cid('G','game', r.game_id);

    -- Identity map rows for the composite key (non-exact: method + confidence + review flag).
    -- Manual review is required only where a season-stats row SHOULD exist (D-I team-seasons).
    INSERT INTO metadata.player_id_map
    SELECT 'CBBD_STARTER_PACK_TEAM_SEASON_NAME', r.season || '|' || r.team_id || '|' || r.name,
           CASE WHEN min(r.name_key_ids) = 1 THEN cid('P','athlete', min(r.name_key_athlete)) END,
           as_int(r.season), r.team_id,
           CASE WHEN min(r.name_key_ids) = 1 THEN 'EXACT_TEAM_SEASON_NAME' ELSE 'UNRESOLVED' END,
           CASE WHEN min(r.name_key_ids) = 1 THEN 1.0 ELSE 0.0 END,
           (min(r.name_key_ids) <> 1 OR bool_or(r.same_name_in_game)) AND coalesce(bool_or(ts.is_division_one), FALSE),
           CASE WHEN min(r.name_key_ids) = 0 THEN 'NO_SEASON_STATS_NAME_MATCH'
                WHEN min(r.name_key_ids) > 1 THEN 'AMBIGUOUS_' || min(r.name_key_ids) || '_SEASON_IDS_SAME_NAME'
                ELSE 'UNIQUE_SEASON_STATS_NAME_MATCH' END
               || '; player_game records=' || count(*)
               || '; same-name records within one game=' || count(*) FILTER (WHERE r.same_name_in_game)
               || CASE WHEN coalesce(bool_or(ts.is_division_one), FALSE) THEN '; D-I team-season'
                       ELSE '; non-D-I/unknown team-season (no season stats expected)' END,
           getvariable('ver')
    FROM pg_resolved r
    LEFT JOIN core.team_seasons ts ON ts.canonical_team_id = cid('T','team', r.team_id) AND ts.season = as_int(r.season)
    GROUP BY r.season, r.team_id, r.name;

    INSERT INTO qa.exceptions
    SELECT CASE WHEN source_context LIKE 'AMBIGUOUS%' THEN 'AMBIGUOUS_PLAYER' ELSE 'UNRESOLVED_PLAYER' END,
           CASE WHEN needs_manual_review THEN 'WARN' ELSE 'INFO' END,
           provider_player_key, source_context, 'CBBD_STARTER_PACK', getvariable('ver')
    FROM metadata.player_id_map WHERE provider = 'CBBD_STARTER_PACK_TEAM_SEASON_NAME' AND match_method = 'UNRESOLVED';

    -- Record-level ambiguity: two source records with the same name in one team-game.
    INSERT INTO qa.exceptions
    SELECT 'AMBIGUOUS_PLAYER_GAME_RECORD', 'WARN', 'game_id=' || game_id || ';team_id=' || team_id || ';name=' || name,
           count(*) || ' source records share this name in one team-game; all left UNRESOLVED_AMBIGUOUS',
           'CBBD_STARTER_PACK', getvariable('ver')
    FROM pg_resolved WHERE same_name_in_game GROUP BY game_id, team_id, name;

    INSERT INTO qa.exceptions
    SELECT 'DUPLICATE_FANOUT_COLLAPSED', 'INFO', 'game_id=' || game_id || ';athleteId_record=' || athleteId,
           count(*) || ' source rows differing only in conference/opponent_conference', getvariable('src'), getvariable('ver')
    FROM staging.sp_player_game_stats GROUP BY game_id, athleteId HAVING count(*) > 1;
    """)


def build_participation(con: duckdb.DuckDBPyConnection) -> None:
    """Step 7. See CANONICAL_SCHEMA.md §Participation for the truth-state rules."""
    con.execute("""
    -- (a) Every box-score row is a candidate.
    INSERT INTO fact.participation
    SELECT p.canonical_game_id, p.canonical_team_id,
           coalesce(p.canonical_player_id, 'UNRESOLVED:' || p.player_game_key), p.canonical_player_id, p.season,
           -- A 0-minute starter is a contradictory source line (architect ruling, Stage 1 closure):
           -- never PLAYED and never DNP until another reliable source confirms participation.
           CASE WHEN p.minutes > 0 THEN 'PLAYED'
                WHEN p.minutes = 0 AND p.starter THEN 'UNKNOWN_PARTICIPATION'
                WHEN greatest(p.points, p.fga, p.fta, p.reb, p.ast, p.stl, p.blk, p.tov, p.pf) > 0 THEN 'PLAYED'
                ELSE 'UNKNOWN_PARTICIPATION' END,
           CASE WHEN p.minutes > 0 THEN 'BOX_ROW_POSITIVE_MINUTES'
                WHEN p.minutes = 0 AND p.starter
                     AND greatest(p.points, p.fga, p.fta, p.reb, p.ast, p.stl, p.blk, p.tov, p.pf) > 0
                     THEN 'FLAGGED_ZERO_MIN_STARTER_WITH_STATS'
                WHEN p.minutes = 0 AND p.starter THEN 'FLAGGED_ZERO_MIN_STARTER'
                WHEN greatest(p.points, p.fga, p.fta, p.reb, p.ast, p.stl, p.blk, p.tov, p.pf) > 0
                     THEN 'BOX_ROW_ZERO_MINUTES_WITH_STATS'
                -- Integer minutes cannot separate a listed DNP from a <30-second appearance.
                ELSE 'BOX_ROW_ZERO_MINUTES_NO_STATS_AMBIGUOUS' END,
           'BOX_SCORE_ROW', p.starter, p.minutes,
           CASE WHEN p.minutes = 0 THEN 'ZERO' WHEN p.minutes BETWEEN 1 AND 4 THEN 'MINIMAL'
                WHEN p.minutes >= 5 THEN 'REGULAR' END,
           CASE WHEN p.pf >= 5 THEN TRUE END,
           CASE WHEN p.pf >= 5 THEN 'FOULED_OUT_PF_GE_5'
                ELSE 'NO_EVIDENCE_IN_SOURCE (ejected field constant FALSE; injury exits not recorded)' END,
           p.event_time, p.historical_knowable_at,
           p.source_system, p.source_file, p.source_record_key, p.ingestion_version, p.received_at, p.processed_at
    FROM fact.player_games p;

    -- (b) Season-stat members of the team with no resolved box row in a game the team played
    --     WITH a player box score. Without a roster/DNP list this is UNKNOWN, never DNP.
    INSERT INTO fact.participation
    WITH members AS (
        SELECT DISTINCT canonical_player_id, canonical_team_id, season FROM fact.player_season_stats),
    team_box_games AS (
        SELECT DISTINCT canonical_game_id, canonical_team_id, season, event_time, historical_knowable_at
        FROM fact.player_games)
    SELECT g.canonical_game_id, g.canonical_team_id, m.canonical_player_id, m.canonical_player_id, g.season,
           'UNKNOWN_PARTICIPATION', 'NO_RESOLVED_BOX_ROW_SEASON_MEMBER_NO_ROSTER', 'SEASON_STATS_MEMBERSHIP',
           NULL, NULL, NULL, NULL, NULL, g.event_time, g.historical_knowable_at,
           getvariable('src'), 'derived: player_season_stats x player_game_stats',
           'game=' || g.canonical_game_id || ';player=' || m.canonical_player_id, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM team_box_games g JOIN members m ON m.canonical_team_id = g.canonical_team_id AND m.season = g.season
    WHERE NOT EXISTS (SELECT 1 FROM fact.player_games p
                      WHERE p.canonical_game_id = g.canonical_game_id AND p.canonical_player_id = m.canonical_player_id);
    """)


def build_season_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
    INSERT INTO fact.player_season_stats
    SELECT cid('P','athlete', athleteId), cid('T','team', teamId), as_int(s.season), name, position,
           as_int(games), as_int(starts), as_int(minutes), as_int(points),
           as_int(fieldGoals_made), as_int(fieldGoals_attempted), as_int(twoPointFieldGoals_made),
           as_int(twoPointFieldGoals_attempted), as_int(threePointFieldGoals_made), as_int(threePointFieldGoals_attempted),
           as_int(freeThrows_made), as_int(freeThrows_attempted), as_int(rebounds_offensive), as_int(rebounds_defensive),
           as_int(rebounds_total), as_int(assists), as_int(steals), as_int(blocks), as_int(turnovers), as_int(fouls),
           as_dbl(usage), as_dbl(offensiveRating), as_dbl(PORPAG), NULL, sp.season_knowable_at,
           getvariable('src'), filename, 'season=' || s.season || ';teamId=' || teamId || ';athleteId=' || athleteId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM ps s JOIN season_pit sp ON sp.season = as_int(s.season);

    INSERT INTO fact.player_season_shooting
    SELECT cid('P','athlete', athleteId), cid('T','team', teamId), as_int(s.season), as_int(trackedShots),
           as_int(dunks_made), as_int(dunks_attempted), as_int(dunks_assisted),
           as_int(layups_made), as_int(layups_attempted), as_int(layups_assisted),
           as_int(tipIns_made), as_int(tipIns_attempted),
           as_int(twoPointJumpers_made), as_int(twoPointJumpers_attempted), as_int(twoPointJumpers_assisted),
           as_int(threePointJumpers_made), as_int(threePointJumpers_attempted), as_int(threePointJumpers_assisted),
           sp.season_knowable_at,
           getvariable('src'), filename, 'season=' || s.season || ';teamId=' || teamId || ';athleteId=' || athleteId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM pss s JOIN season_pit sp ON sp.season = as_int(s.season);

    INSERT INTO fact.team_season_stats
    SELECT cid('T','team', teamId), as_int(s.season), as_int(games), as_int(wins), as_int(losses),
           as_int(totalMinutes), as_dbl(pace),
           as_int(teamStats_points_total), as_int(teamStats_fieldGoals_made), as_int(teamStats_fieldGoals_attempted),
           as_int(teamStats_twoPointFieldGoals_made), as_int(teamStats_twoPointFieldGoals_attempted),
           as_int(teamStats_threePointFieldGoals_made), as_int(teamStats_threePointFieldGoals_attempted),
           as_int(teamStats_freeThrows_made), as_int(teamStats_freeThrows_attempted),
           as_int(teamStats_rebounds_offensive), as_int(teamStats_rebounds_defensive), as_int(teamStats_rebounds_total),
           as_int(teamStats_assists), as_int(teamStats_steals), as_int(teamStats_blocks),
           as_int(teamStats_turnovers_total), as_int(teamStats_turnovers_teamTotal), as_int(teamStats_fouls_total),
           as_dbl(teamStats_possessions),
           as_int(opponentStats_points_total), as_int(opponentStats_fieldGoals_made), as_int(opponentStats_fieldGoals_attempted),
           as_int(opponentStats_threePointFieldGoals_made), as_int(opponentStats_threePointFieldGoals_attempted),
           as_int(opponentStats_freeThrows_made), as_int(opponentStats_freeThrows_attempted),
           as_int(opponentStats_rebounds_offensive), as_int(opponentStats_rebounds_defensive),
           as_int(opponentStats_turnovers_total), as_dbl(opponentStats_possessions),
           NULL, sp.season_knowable_at,
           getvariable('src'), filename, 'season=' || s.season || ';teamId=' || teamId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM (SELECT DISTINCT ON (season, teamId) * FROM staging.sp_team_season_stats
          ORDER BY season, teamId, conference NULLS LAST, filename) s
    JOIN season_pit sp ON sp.season = as_int(s.season);

    INSERT INTO fact.team_season_shooting
    SELECT cid('T','team', teamId), as_int(s.season), as_int(trackedShots),
           as_int(dunks_made), as_int(dunks_attempted), as_int(dunks_assisted),
           as_int(layups_made), as_int(layups_attempted), as_int(layups_assisted),
           as_int(tipIns_made), as_int(tipIns_attempted),
           as_int(twoPointJumpers_made), as_int(twoPointJumpers_attempted), as_int(twoPointJumpers_assisted),
           as_int(threePointJumpers_made), as_int(threePointJumpers_attempted), as_int(threePointJumpers_assisted),
           sp.season_knowable_at,
           getvariable('src'), filename, 'season=' || s.season || ';teamId=' || teamId, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM (SELECT DISTINCT ON (season, teamId) * FROM staging.sp_team_season_shooting_stats
          ORDER BY season, teamId, conference NULLS LAST, filename) s
    JOIN season_pit sp ON sp.season = as_int(s.season);
    """)


# Season-statistics authority (docs/SEASON_STATISTICS_AUTHORITY.md). Canonical game-level tables are
# authoritative for modeling. Raw season summaries stay unchanged in fact.team_season_stats for
# audit and priors; every summary value that disagrees with its game-derived twin is recorded.
SEASON_COMPARE_FIELDS = ("games",) + tuple(
    "points fgm fga two_pm two_pa three_pm three_pa ftm fta orb drb reb ast stl blk tov_total pf".split())


def build_season_authority(con: duckdb.DuckDBPyConnection) -> None:
    sums = ", ".join(f"sum(t.{f}) AS {f}" for f in SEASON_COMPARE_FIELDS[1:])
    con.execute(f"""
    INSERT INTO fact.team_season_game_derived
    -- Stage 2.1/2.2: through the trusted PLAYED-game relation: a duplicate source record or an unplayed schedule
    -- record never adds a result
    WITH results AS (   -- every trusted played game with a final score, boxed or not
        SELECT home_team_id AS team, season, home_points AS pts, away_points AS opp FROM flagship.played_games WHERE home_points IS NOT NULL AND away_points IS NOT NULL
        UNION ALL
        SELECT away_team_id, season, away_points, home_points FROM flagship.played_games WHERE home_points IS NOT NULL AND away_points IS NOT NULL),
    r AS (SELECT team, season, count(*) AS n, count(*) FILTER (WHERE pts > opp) AS w, count(*) FILTER (WHERE pts < opp) AS l,
                 sum(pts) AS pts, sum(opp) AS opp FROM results GROUP BY 1, 2),
    b AS (SELECT t.canonical_team_id AS team, t.season, count(*) AS games, {sums} FROM flagship.team_game_outcomes t GROUP BY 1, 2)
    SELECT r.team, r.season, r.n, r.w, r.l, r.pts, r.opp,
           coalesce(b.games, 0), {", ".join("b." + f for f in SEASON_COMPARE_FIELDS[1:])},
           sp.season_knowable_at,
           'CANONICAL_DERIVED', 'derived: flagship.played_games + flagship.team_game_outcomes (trusted played games)',
           'team=' || r.team || ';season=' || r.season, getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM r LEFT JOIN b ON b.team = r.team AND b.season = r.season
    JOIN season_pit sp ON sp.season = r.season
    WHERE r.season IN (SELECT DISTINCT season FROM fact.team_season_stats);

    INSERT INTO qa.season_summary_conflicts
    {" UNION ALL ".join(f"""
    SELECT s.canonical_team_id, s.season, '{f}', s.{f}, d.{"games_with_team_box" if f == "games" else f},
           d.{"games_with_result" if f == "games" else "points_all_results" if f == "points" else f}, 'GAME_DERIVED'
    FROM fact.team_season_stats s JOIN fact.team_season_game_derived d USING (canonical_team_id, season)
    WHERE s.{f} IS DISTINCT FROM d.{"games_with_team_box" if f == "games" else f}""" for f in SEASON_COMPARE_FIELDS)};
    """)


PLAYER_SEASON_FIELDS = tuple("minutes points fgm fga two_pm two_pa three_pm three_pa ftm fta orb drb reb "
                             "ast stl blk tov pf".split())


def build_player_season_authority(con: duckdb.DuckDBPyConnection) -> None:
    """Player-season counterpart of build_season_authority (architect ruling, Stage 1 final unblock)."""
    sums = ", ".join(f'sum(p."{f}") AS "{f}"' for f in PLAYER_SEASON_FIELDS)
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE identity_gap AS
    SELECT DISTINCT canonical_team_id, season FROM fact.player_games WHERE canonical_player_id IS NULL;

    INSERT INTO fact.player_season_game_derived
    SELECT p.canonical_player_id, p.canonical_team_id, p.season, count(*), count(*) FILTER (WHERE p.minutes > 0),
           count(*) FILTER (WHERE p.starter), {sums},
           NOT EXISTS (SELECT 1 FROM identity_gap g WHERE g.canonical_team_id = p.canonical_team_id AND g.season = p.season),
           sp.season_knowable_at,
           'CANONICAL_DERIVED', 'derived: flagship.player_game_outcomes (resolved rows, trusted games)',
           'player=' || p.canonical_player_id || ';team=' || p.canonical_team_id || ';season=' || p.season,
           getvariable('ver'), getvariable('recv'), getvariable('proc')
    FROM flagship.player_game_outcomes p JOIN season_pit sp ON sp.season = p.season
    WHERE p.canonical_player_id IS NOT NULL
    GROUP BY p.canonical_player_id, p.canonical_team_id, p.season, sp.season_knowable_at;

    INSERT INTO qa.player_season_summary_conflicts
    {" UNION ALL ".join(f"""
    SELECT s.canonical_player_id, s.canonical_team_id, s.season, '{f}', s."{f}", d."{f}",
           coalesce(d.identity_complete, NOT EXISTS (SELECT 1 FROM identity_gap g
                    WHERE g.canonical_team_id = s.canonical_team_id AND g.season = s.season)),
           CASE WHEN coalesce(d.identity_complete, FALSE) THEN 'GAME_DERIVED' ELSE 'IDENTITY_GAP_NO_AUTHORITY' END
    FROM fact.player_season_stats s LEFT JOIN fact.player_season_game_derived d USING (canonical_player_id, canonical_team_id, season)
    WHERE s."{f}" IS DISTINCT FROM d."{f}" """ for f in ("games", "starts") + PLAYER_SEASON_FIELDS)};
    """)


# Accounting identities (MODEL_ARCHITECTURE_FREEZE §3). Source rows that violate them are
# FLAGGED, never corrected or deleted. tests/test_canonical_qa.py recomputes every identity
# independently and proves the flags are complete and the unflagged rows are clean.
def accounting_rules(a: str, tov: str) -> str:
    """SQL expression -> ';'-joined list of failed identities ('' when all hold). `a` = table alias."""
    c = lambda col: f"{a}.{col}"
    prims = ["points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta", "orb", "drb", "reb"]
    counts = prims + ["ast", "stl", "blk", tov, "pf"]
    return f"""concat_ws(';',
        CASE WHEN {' OR '.join(c(x) + ' IS NULL' for x in prims)} THEN 'NULL_PRIMITIVE' END,
        CASE WHEN {c('points')} <> 2 * {c('two_pm')} + 3 * {c('three_pm')} + {c('ftm')} THEN 'PTS_IDENTITY' END,
        CASE WHEN {c('reb')} <> {c('orb')} + {c('drb')} THEN 'REB_IDENTITY' END,
        CASE WHEN {c('fgm')} <> {c('two_pm')} + {c('three_pm')} THEN 'FGM_IDENTITY' END,
        CASE WHEN {c('fga')} <> {c('two_pa')} + {c('three_pa')} THEN 'FGA_IDENTITY' END,
        CASE WHEN {c('fgm')} > {c('fga')} OR {c('two_pm')} > {c('two_pa')} OR {c('three_pm')} > {c('three_pa')}
               OR {c('ftm')} > {c('fta')} THEN 'MAKES_GT_ATTEMPTS' END,
        CASE WHEN least({', '.join(c(x) for x in counts)}) < 0 THEN 'NEGATIVE_COUNT' END)"""


def flag_accounting(con: duckdb.DuckDBPyConnection) -> None:
    player = accounting_rules("p", "tov")
    con.execute(f"""
    UPDATE fact.player_games SET accounting_issues = f.issues FROM (
        SELECT p.player_game_key,
               nullif(concat_ws(';', nullif({player}, ''),
                      -- minutes cannot exceed the game's length (40 + 5 per overtime)
                      CASE WHEN p.minutes < 0 OR p.minutes > t.game_minutes THEN 'MINUTES_OUT_OF_RANGE' END), '') AS issues
        FROM fact.player_games p
        LEFT JOIN fact.team_games t ON t.canonical_game_id = p.canonical_game_id AND t.canonical_team_id = p.canonical_team_id
    ) f WHERE f.player_game_key = fact.player_games.player_game_key;

    UPDATE fact.team_games t SET accounting_issues = nullif({accounting_rules('t', 'tov_total')}, '');
    UPDATE fact.player_season_stats p SET accounting_issues = nullif({accounting_rules('p', 'tov')}, '');
    UPDATE fact.team_season_stats t SET accounting_issues = nullif({accounting_rules('t', 'tov_total')}, '');

    -- Player box completeness for each team-game: are any played lines missing?
    UPDATE fact.team_games t SET player_box_reconciles = (s.pts = t.points AND abs(s.mins - 5 * t.game_minutes) <= 5)
    FROM (SELECT canonical_game_id, canonical_team_id, sum(points) AS pts, sum(minutes) AS mins
          FROM fact.player_games GROUP BY 1, 2) s
    WHERE s.canonical_game_id = t.canonical_game_id AND s.canonical_team_id = t.canonical_team_id;
    """)


def record_source_manifest(con: duckdb.DuckDBPyConnection, reg: dict) -> None:
    manifest = json.loads((C.MANIFESTS / "starter_pack_sha256.json").read_text())["files"]
    rows = [("CBBD_STARTER_PACK", rel, m["sha256"], m["size_bytes"], None, None, None,
             reg["registered_at_utc"], True, "immutable mounted upstream; not copied")
            for rel, m in sorted(manifest.items()) if rel.startswith("data/") and rel.endswith(".csv")]
    con.executemany("INSERT INTO metadata.source_manifest VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    # Row counts per file, measured from the staging views.
    con.execute("""
    UPDATE metadata.source_manifest m SET row_count = c.n FROM (
        SELECT filename, count(*) n FROM (
            SELECT filename FROM staging.sp_games UNION ALL SELECT filename FROM staging.sp_teams
            UNION ALL SELECT filename FROM staging.sp_conferences UNION ALL SELECT filename FROM staging.sp_player_game_stats
            UNION ALL SELECT filename FROM staging.sp_player_season_stats
            UNION ALL SELECT filename FROM staging.sp_player_season_shooting_stats
            UNION ALL SELECT filename FROM staging.sp_team_game_stats UNION ALL SELECT filename FROM staging.sp_team_season_stats
            UNION ALL SELECT filename FROM staging.sp_team_season_shooting_stats) GROUP BY 1) c
    WHERE m.source_path = c.filename""")


# ---------------------------------------------------------------------------
# Export + fingerprints
# ---------------------------------------------------------------------------
CANONICAL_SCHEMAS = ("core", "fact", "availability", "benchmark", "metadata", "qa")


def canonical_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [f"{s}.{t}" for s, t in con.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE schema_name IN "
        f"{CANONICAL_SCHEMAS} ORDER BY 1, 2").fetchall()]


def fingerprint(con: duckdb.DuckDBPyConnection, table: str) -> tuple[int, str]:
    """Order-independent content hash of every column except processed_at."""
    cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
    excl = " EXCLUDE (processed_at)" if "processed_at" in cols else ""
    n, x, s = con.execute(
        f"SELECT count(*), bit_xor(hash(t)), sum(hash(t)) FROM (SELECT *{excl} FROM {table}) t").fetchone()
    return n, f"{n}:{x}:{s}"


def export_parquet(con: duckdb.DuckDBPyConnection, build_id: str, built_at: datetime, parquet_root) -> dict:
    results = {}
    for table in canonical_tables(con):
        if table == "metadata.data_versions":
            continue
        n, fp = fingerprint(con, table)
        con.execute("INSERT INTO metadata.data_versions VALUES (?,?,?,?,?,?,?)",
                    [build_id, table, n, fp, C.INGESTION_VERSION, C.SCHEMA_VERSION, built_at])
        results[table] = {"rows": n, "fingerprint": fp}
        schema, name = table.split(".")
        out = parquet_root / schema / f"{name}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        if n:
            con.execute(f"COPY (SELECT * FROM {table} ORDER BY ALL) TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
        elif out.exists():
            out.unlink()   # an empty table has no Parquet file; stale outputs are removed
    return results


def build(db_path=None, parquet_root=None) -> dict:
    """Rebuild everything. Defaults: data/canonical/cbb.duckdb and data/canonical/<schema>/<table>.parquet."""
    db_path = db_path or C.DUCKDB_PATH
    parquet_root = parquet_root or C.CANONICAL
    reg = register_starter_pack()
    built_at = datetime.now(timezone.utc)
    build_id = built_at.strftime("%Y%m%dT%H%M%SZ")
    tmp = str(db_path) + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(tmp)
    con.execute("SET TimeZone = 'UTC'")
    con.execute("SET enable_progress_bar = false")
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    con.execute(C.SCHEMA_SQL.read_text())
    con.execute(ID_MACROS)
    # Shared lineage values, read inside SQL via getvariable(...).
    con.execute("SET VARIABLE src = ?", [SRC])
    con.execute("SET VARIABLE ver = ?", [C.INGESTION_VERSION])
    con.execute("SET VARIABLE recv = CAST(? AS TIMESTAMPTZ)", [reg["registered_at_utc"]])
    con.execute("SET VARIABLE proc = CAST(? AS TIMESTAMPTZ)", [built_at.isoformat()])
    create_staging_views(con)
    build_core(con)
    build_games(con)
    build_team_games(con)
    build_players_and_player_games(con)
    from cbb_platform import game_identity          # Stage 2.1: before any game-derived aggregate
    game_identity.resolve(con)
    build_season_tables(con)          # before participation: membership comes from season stats
    build_participation(con)
    flag_accounting(con)
    record_source_manifest(con, reg)

    # Raw adapter payloads already in data/raw (BDL / CBBD / availability snapshots) are
    # re-normalized on every build so canonical tables stay a pure function of raw inputs.
    from cbb_platform.adapters import load_all_raw
    load_all_raw(con)
    game_identity.assert_fact_safety(con)            # no fact duplicated on, or lost from, a trusted game
    # Stage 2.2: played-game status needs every box AND event fact, so game-derived aggregates are built after it
    from cbb_platform import game_status
    game_status.resolve(con)
    con.execute(KNOWABLE_SQL)
    build_season_authority(con)
    build_player_season_authority(con)

    results = export_parquet(con, build_id, built_at, parquet_root)
    con.close()
    os.replace(tmp, db_path)
    return {"build_id": build_id, "tables": results}


if __name__ == "__main__":
    out = build()
    print("build", out["build_id"])
    for t, r in out["tables"].items():
        print(f"  {t:40s} {r['rows']:>10,}")
