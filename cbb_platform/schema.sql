-- =============================================================================
-- CBB Originator — canonical schema (DuckDB DDL), schema version canonical-1.0.0
-- Human documentation: CANONICAL_SCHEMA.md (grain, keys, units, null semantics).
--
-- Conventions
--   * canonical_*_id are opaque, provider-independent strings (see ID contract in
--     CANONICAL_SCHEMA.md). Provider ids live only in metadata.*_id_map and in the
--     lineage column source_record_key.
--   * All timestamps are TIMESTAMPTZ in UTC. Unknown timestamps are NULL — never invented.
--   * Counting stats are integers. Percentages are NOT stored canonically; they are
--     derived (numerator/denominator) and are NULL when the denominator is 0.
--   * Every row carries lineage: source_system, source_file, source_record_key,
--     ingestion_version, received_at, processed_at.
--   * Foreign keys are documented and enforced by tests (tests/test_canonical_qa.py),
--     not by DuckDB FK constraints, to keep rebuilds simple.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS fact;
CREATE SCHEMA IF NOT EXISTS availability;
CREATE SCHEMA IF NOT EXISTS benchmark;   -- BDL QA data + ALL sportsbook data. Never read by flagship.*
CREATE SCHEMA IF NOT EXISTS metadata;
CREATE SCHEMA IF NOT EXISTS qa;
CREATE SCHEMA IF NOT EXISTS flagship;    -- the ONLY namespace later feature builders may read

-- ----------------------------------------------------------------------------- core
CREATE TABLE IF NOT EXISTS core.conferences (
    canonical_conference_id VARCHAR PRIMARY KEY,
    name                    VARCHAR,
    abbreviation            VARCHAR,
    short_name              VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS core.teams (
    canonical_team_id VARCHAR PRIMARY KEY,
    school            VARCHAR NOT NULL,
    mascot            VARCHAR,
    nickname          VARCHAR,
    abbreviation      VARCHAR,
    display_name      VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

-- One row per team per season in which the team appears in any game.
-- is_division_one is a MEASURED PROXY: the team has a team_season_stats row that season
-- (364 teams in 2024-25 = the actual D-I count). NULL where no season stats exist (pre-2005).
CREATE TABLE IF NOT EXISTS core.team_seasons (
    canonical_team_id        VARCHAR NOT NULL,
    season                   INTEGER NOT NULL,      -- CBBD convention: year the season ENDS
    canonical_conference_id  VARCHAR,               -- most frequent per-game conference id that season
    conference_assignment_method VARCHAR NOT NULL,  -- 'MODE_OF_GAME_CONFERENCE_IDS' | 'NO_CONFERENCE_ID'
    is_division_one          BOOLEAN,
    division_method          VARCHAR NOT NULL,      -- 'HAS_TEAM_SEASON_STATS_ROW' | 'NO_SEASON_STATS_FOR_SEASON'
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_team_id, season)
);

CREATE TABLE IF NOT EXISTS core.venues (
    canonical_venue_id VARCHAR PRIMARY KEY,
    name VARCHAR, city VARCHAR, state VARCHAR, country VARCHAR,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

-- Grain: one contest. Outcome columns (points/winner/period points) are the official
-- final record and are POST-TIP information (see POINT_IN_TIME_POLICY.md).
CREATE TABLE IF NOT EXISTS core.games (
    canonical_game_id        VARCHAR PRIMARY KEY,
    season                   INTEGER NOT NULL,
    season_label             VARCHAR,
    season_type              VARCHAR,        -- regular | postseason | preseason
    game_type                VARCHAR,        -- STD | TRNMNT | ... (raw CBBD code)
    tournament               VARCHAR,
    notes                    VARCHAR,
    scheduled_tip_utc        TIMESTAMPTZ,    -- from games.csv (naive UTC, BDL-verified); retrospective
    tip_time_quality         VARCHAR NOT NULL, -- EXACT | DATE_ONLY | TBD_FLAG
    game_date_et             DATE NOT NULL,  -- US/Eastern calendar date of the game
    neutral_site             BOOLEAN,
    conference_game          BOOLEAN,
    home_team_id             VARCHAR NOT NULL,
    away_team_id             VARCHAR NOT NULL,
    home_conference_id       VARCHAR,
    away_conference_id       VARCHAR,
    home_seed                INTEGER,
    away_seed                INTEGER,
    canonical_venue_id       VARCHAR,
    home_points              INTEGER,
    away_points              INTEGER,
    home_period_points       INTEGER[],
    away_period_points       INTEGER[],
    overtime_count           INTEGER,        -- from box-score gameMinutes: (minutes-40)/5; NULL if no box score
    game_universe            VARCHAR NOT NULL, -- DI_VS_DI | DI_VS_NON_DI | NON_DI_ONLY | UNKNOWN
    has_team_box_score       BOOLEAN NOT NULL,
    has_player_box_score     BOOLEAN NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

-- Versioned schedule. A checkpoint is anchored to the tip time KNOWN AT THAT POINT.
-- Starter Pack rows are a single retrospective version with observed_at = NULL
-- (we do not know when that schedule became knowable) and is_retrospective = TRUE.
CREATE TABLE IF NOT EXISTS core.game_schedule_versions (
    canonical_game_id   VARCHAR NOT NULL,
    schedule_version    INTEGER NOT NULL,
    scheduled_tip_utc   TIMESTAMPTZ,
    tip_time_quality    VARCHAR NOT NULL,
    status              VARCHAR,         -- scheduled|in_progress|final|postponed|cancelled (NULL if not supplied)
    source_updated_at   TIMESTAMPTZ,
    observed_at         TIMESTAMPTZ,     -- when we (or an archive) actually saw this version
    is_retrospective    BOOLEAN NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, schedule_version)
);

CREATE TABLE IF NOT EXISTS core.players (
    canonical_player_id VARCHAR PRIMARY KEY,
    display_name        VARCHAR NOT NULL,   -- most recent season's name as supplied
    first_season        INTEGER,
    last_season         INTEGER,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

-- Explicit roster universe (CBBD /teams/roster). EMPTY in Stage 1: the Starter Pack has no
-- roster table, and season-stat membership is NOT a roster (it omits non-playing players).
CREATE TABLE IF NOT EXISTS core.rosters (
    canonical_player_id VARCHAR NOT NULL,
    canonical_team_id   VARCHAR NOT NULL,
    season              INTEGER NOT NULL,
    jersey              VARCHAR,
    position            VARCHAR,
    height_inches       DOUBLE,
    weight_lbs          DOUBLE,
    roster_status       VARCHAR,
    valid_from          TIMESTAMPTZ,
    valid_to            TIMESTAMPTZ,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_player_id, canonical_team_id, season)
);

CREATE TABLE IF NOT EXISTS core.recruits (            -- CBBD /recruiting/players; empty in Stage 1
    source_recruit_id VARCHAR PRIMARY KEY, canonical_player_id VARCHAR, recruit_year INTEGER,
    name VARCHAR, position VARCHAR, committed_team_id VARCHAR, stars INTEGER, rating DOUBLE, ranking INTEGER,
    height_inches DOUBLE, weight_lbs DOUBLE,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS core.transfers (           -- CBBD /recruiting/portal; empty in Stage 1
    source_transfer_id VARCHAR PRIMARY KEY, canonical_player_id VARCHAR, transfer_year INTEGER,
    first_name VARCHAR, last_name VARCHAR, position VARCHAR, origin_team_id VARCHAR, destination_team_id VARCHAR,
    eligibility VARCHAR, years_remaining INTEGER, stars INTEGER, rating DOUBLE,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

-- ----------------------------------------------------------------------------- fact
-- Grain: one source player-game box-score line (after collapsing the measured conference
-- fan-out). canonical_player_id is NULL when identity is unresolved — never invented.
CREATE TABLE IF NOT EXISTS fact.player_games (
    player_game_key          VARCHAR PRIMARY KEY,   -- deterministic hash of source record
    canonical_game_id        VARCHAR NOT NULL,
    canonical_team_id        VARCHAR NOT NULL,
    canonical_opponent_id    VARCHAR NOT NULL,
    canonical_player_id      VARCHAR,
    identity_status          VARCHAR NOT NULL,      -- RESOLVED | UNRESOLVED_NO_MATCH | UNRESOLVED_AMBIGUOUS
                                                    -- | UNRESOLVED_SAME_PLAYER_BOTH_TEAMS
    season                   INTEGER NOT NULL,
    is_home                  BOOLEAN,
    source_player_name       VARCHAR NOT NULL,
    source_position          VARCHAR,
    starter                  BOOLEAN,
    minutes                  INTEGER,                -- whole minutes as supplied (rounded by source)
    points INTEGER, fgm INTEGER, fga INTEGER, two_pm INTEGER, two_pa INTEGER, three_pm INTEGER, three_pa INTEGER,
    ftm INTEGER, fta INTEGER, orb INTEGER, drb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER,
    tov INTEGER, pf INTEGER,
    ejected_source           BOOLEAN,                -- raw value; measured constant FALSE (uninformative)
    usage_source             DOUBLE,                 -- P1 supplied summary, kept for QA only
    accounting_issues        VARCHAR,                -- NULL = every accounting identity holds (see doc)
    event_time               TIMESTAMPTZ,            -- = scheduled tip of the game
    historical_knowable_at   TIMESTAMPTZ NOT NULL,   -- PIT rule HR-1 (POINT_IN_TIME_POLICY.md)
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

-- Grain: one team in one game.
CREATE TABLE IF NOT EXISTS fact.team_games (
    canonical_game_id     VARCHAR NOT NULL,
    canonical_team_id     VARCHAR NOT NULL,
    canonical_opponent_id VARCHAR NOT NULL,
    season                INTEGER NOT NULL,
    is_home               BOOLEAN,
    neutral_site          BOOLEAN,
    game_minutes          INTEGER,               -- 40 regulation; +5 per overtime
    overtime_count        INTEGER,
    points INTEGER, fgm INTEGER, fga INTEGER, two_pm INTEGER, two_pa INTEGER, three_pm INTEGER, three_pa INTEGER,
    ftm INTEGER, fta INTEGER, orb INTEGER, drb INTEGER, reb INTEGER,   -- team rebounds INCLUDED (source semantics)
    ast INTEGER, stl INTEGER, blk INTEGER,
    tov_total             INTEGER,               -- all turnovers incl. team turnovers
    team_turnovers        INTEGER,               -- turnovers not credited to a player (source teamTotal)
    pf INTEGER, technical_fouls INTEGER, flagrant_fouls INTEGER,
    possessions_source    DOUBLE,
    pace_source           DOUBLE,
    points_in_paint INTEGER, fast_break_points INTEGER, points_off_turnovers INTEGER, largest_lead INTEGER,
    points_by_period      INTEGER[],
    accounting_issues     VARCHAR,               -- NULL = every accounting identity holds
    player_box_reconciles BOOLEAN,               -- player sums match team PTS and 5x game minutes (+-5)
    event_time            TIMESTAMPTZ,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, canonical_team_id)
);

-- Grain: one candidate player for one team in one game. See CANONICAL_SCHEMA.md §Participation.
CREATE TABLE IF NOT EXISTS fact.participation (
    canonical_game_id     VARCHAR NOT NULL,
    canonical_team_id     VARCHAR NOT NULL,
    candidate_key         VARCHAR NOT NULL,      -- canonical_player_id, or 'UNRESOLVED:'||player_game_key
    canonical_player_id   VARCHAR,
    season                INTEGER NOT NULL,
    participation_state   VARCHAR NOT NULL,      -- PLAYED | CONFIRMED_DNP | UNKNOWN_PARTICIPATION
    participation_evidence VARCHAR NOT NULL,     -- why (see doc); medical reason is never inferred
    candidate_source      VARCHAR NOT NULL,      -- BOX_SCORE_ROW | SEASON_STATS_MEMBERSHIP | ROSTER
    starter               BOOLEAN,               -- orthogonal to participation; NULL when no box row
    minutes               INTEGER,
    minutes_band          VARCHAR,               -- ZERO | MINIMAL (1-4) | REGULAR (>=5); NULL when no box row
    interruption_flag     BOOLEAN,               -- TRUE only with evidence (fouled out); NULL = unknown
    interruption_evidence VARCHAR,
    event_time            TIMESTAMPTZ,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, canonical_team_id, candidate_key)
);

-- END-OF-SEASON aggregates. historical_knowable_at = end of that season, so they can never
-- describe an earlier game of the same season (POINT_IN_TIME_POLICY.md rule HR-3).
CREATE TABLE IF NOT EXISTS fact.player_season_stats (
    canonical_player_id VARCHAR NOT NULL, canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL,
    source_player_name VARCHAR, position VARCHAR, games INTEGER, starts INTEGER, minutes INTEGER,
    points INTEGER, fgm INTEGER, fga INTEGER, two_pm INTEGER, two_pa INTEGER, three_pm INTEGER, three_pa INTEGER,
    ftm INTEGER, fta INTEGER, orb INTEGER, drb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER,
    tov INTEGER, pf INTEGER, usage_source DOUBLE, offensive_rating_source DOUBLE, porpag_source DOUBLE,
    accounting_issues VARCHAR,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_player_id, canonical_team_id, season)
);

CREATE TABLE IF NOT EXISTS fact.player_season_shooting (
    canonical_player_id VARCHAR NOT NULL, canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL,
    tracked_shots INTEGER,
    dunks_made INTEGER, dunks_attempted INTEGER, dunks_assisted INTEGER,
    layups_made INTEGER, layups_attempted INTEGER, layups_assisted INTEGER,
    tipins_made INTEGER, tipins_attempted INTEGER,
    two_pt_jumpers_made INTEGER, two_pt_jumpers_attempted INTEGER, two_pt_jumpers_assisted INTEGER,
    three_pt_jumpers_made INTEGER, three_pt_jumpers_attempted INTEGER, three_pt_jumpers_assisted INTEGER,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_player_id, canonical_team_id, season)
);

CREATE TABLE IF NOT EXISTS fact.team_season_stats (
    canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL,
    games INTEGER, wins INTEGER, losses INTEGER, total_minutes INTEGER, pace_source DOUBLE,
    points INTEGER, fgm INTEGER, fga INTEGER, two_pm INTEGER, two_pa INTEGER, three_pm INTEGER, three_pa INTEGER,
    ftm INTEGER, fta INTEGER, orb INTEGER, drb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER,
    tov_total INTEGER, team_turnovers INTEGER, pf INTEGER, possessions_source DOUBLE,
    opp_points INTEGER, opp_fgm INTEGER, opp_fga INTEGER, opp_three_pm INTEGER, opp_three_pa INTEGER,
    opp_ftm INTEGER, opp_fta INTEGER, opp_orb INTEGER, opp_drb INTEGER, opp_tov_total INTEGER, opp_possessions_source DOUBLE,
    accounting_issues VARCHAR,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_team_id, season)
);

CREATE TABLE IF NOT EXISTS fact.team_season_shooting (
    canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL, tracked_shots INTEGER,
    dunks_made INTEGER, dunks_attempted INTEGER, dunks_assisted INTEGER,
    layups_made INTEGER, layups_attempted INTEGER, layups_assisted INTEGER,
    tipins_made INTEGER, tipins_attempted INTEGER,
    two_pt_jumpers_made INTEGER, two_pt_jumpers_attempted INTEGER, two_pt_jumpers_assisted INTEGER,
    three_pt_jumpers_made INTEGER, three_pt_jumpers_attempted INTEGER, three_pt_jumpers_assisted INTEGER,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_team_id, season)
);

-- AUTHORITATIVE team-season totals for modeling, derived from canonical game-level rows
-- (docs/SEASON_STATISTICS_AUTHORITY.md). Two explicit bases, never mixed in one identity:
--   *_all_results / games_with_result : every game with a final score in core.games
--   box columns / games_with_team_box : only games with a team box score (fact.team_games)
-- Completed-season object: same PIT rule (HR-3) as the raw summaries. In-season features are
-- rebuilt as-of the cutoff from fact.team_games (cbb_platform/pit.py), never read from here.
CREATE TABLE IF NOT EXISTS fact.team_season_game_derived (
    canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL,
    games_with_result INTEGER NOT NULL, wins INTEGER, losses INTEGER,
    points_all_results INTEGER, opp_points_all_results INTEGER,
    games_with_team_box INTEGER NOT NULL,
    points INTEGER, fgm INTEGER, fga INTEGER, two_pm INTEGER, two_pa INTEGER, three_pm INTEGER, three_pa INTEGER,
    ftm INTEGER, fta INTEGER, orb INTEGER, drb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER,
    tov_total INTEGER, pf INTEGER,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_team_id, season)
);

-- AUTHORITATIVE player-season totals for modeling where reconstructable (docs/SEASON_STATISTICS_AUTHORITY.md §5):
-- the sum of RESOLVED fact.player_games rows. `identity_complete` = the team-season has no unresolved
-- player-game rows, i.e. the derivation cannot be missing any of this player's games.
CREATE TABLE IF NOT EXISTS fact.player_season_game_derived (
    canonical_player_id VARCHAR NOT NULL, canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL,
    games INTEGER NOT NULL, games_positive_minutes INTEGER NOT NULL, starts INTEGER NOT NULL,
    minutes INTEGER, points INTEGER, fgm INTEGER, fga INTEGER, two_pm INTEGER, two_pa INTEGER,
    three_pm INTEGER, three_pa INTEGER, ftm INTEGER, fta INTEGER, orb INTEGER, drb INTEGER, reb INTEGER,
    ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER,
    identity_complete BOOLEAN NOT NULL,
    historical_knowable_at TIMESTAMPTZ NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_player_id, canonical_team_id, season)
);

-- CBBD PBP family (populated by the CBBD adapter; EMPTY until CBBD_API_KEY is supplied).
CREATE TABLE IF NOT EXISTS fact.plays (
    canonical_game_id VARCHAR NOT NULL, source_play_id VARCHAR NOT NULL, play_sequence INTEGER,
    period INTEGER, clock VARCHAR, seconds_remaining INTEGER, play_type VARCHAR,
    canonical_team_id VARCHAR, home_score INTEGER, away_score INTEGER,
    scoring_play BOOLEAN, shooting_play BOOLEAN, score_value INTEGER, play_text VARCHAR,
    participant_player_ids VARCHAR[],      -- canonical ids where resolvable (roles are NOT typed by CBBD)
    on_floor_player_ids VARCHAR[],         -- exactly as supplied; never manufactured
    wallclock_utc TIMESTAMPTZ,             -- event_time when supplied
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, source_play_id)
);

CREATE TABLE IF NOT EXISTS fact.shots (
    canonical_game_id VARCHAR NOT NULL, source_play_id VARCHAR NOT NULL,
    shooter_player_id VARCHAR, made BOOLEAN, shot_range VARCHAR, assisted BOOLEAN,
    assister_player_id VARCHAR, location_x DOUBLE, location_y DOUBLE,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, source_play_id)
);

-- CBBD substitutions are ATHLETE-CENTRIC (one row = one athlete's sub-in and sub-out states).
-- Paired player_in/player_out transactions are a later derived artifact (Stage 4).
CREATE TABLE IF NOT EXISTS fact.substitutions (
    canonical_game_id VARCHAR NOT NULL, canonical_team_id VARCHAR NOT NULL, canonical_player_id VARCHAR,
    source_athlete_id VARCHAR NOT NULL, stint_number INTEGER NOT NULL,
    sub_in_period INTEGER, sub_in_seconds_remaining INTEGER, sub_in_team_points INTEGER, sub_in_opponent_points INTEGER,
    sub_out_period INTEGER, sub_out_seconds_remaining INTEGER, sub_out_team_points INTEGER, sub_out_opponent_points INTEGER,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, source_athlete_id, stint_number)
);

-- CBBD /lineups aggregates (five-man unit totals; NOT stints).
CREATE TABLE IF NOT EXISTS fact.lineup_stats (
    canonical_game_id VARCHAR NOT NULL, canonical_team_id VARCHAR NOT NULL, lineup_hash VARCHAR NOT NULL,
    player_ids VARCHAR[], total_seconds DOUBLE, team_possessions INTEGER, team_points INTEGER,
    opponent_possessions INTEGER, opponent_points INTEGER,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, canonical_team_id, lineup_hash)
);

-- Derived exact stints (Stage 4). Empty by design in Stage 1; never manufactured from minutes.
CREATE TABLE IF NOT EXISTS fact.lineup_stints (
    canonical_game_id VARCHAR NOT NULL, stint_id VARCHAR NOT NULL,
    start_period INTEGER, start_seconds_remaining INTEGER, end_period INTEGER, end_seconds_remaining INTEGER,
    home_player_ids VARCHAR[], away_player_ids VARCHAR[], seconds_played DOUBLE,
    home_points INTEGER, away_points INTEGER, reconstruction_quality VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, stint_id)
);

CREATE TABLE IF NOT EXISTS fact.possessions (     -- derived (Stage 4); empty in Stage 1
    canonical_game_id VARCHAR NOT NULL, possession_number INTEGER NOT NULL,
    offense_team_id VARCHAR, start_play_id VARCHAR, end_play_id VARCHAR, points INTEGER,
    end_type VARCHAR, reconstruction_quality VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL, source_file VARCHAR, source_record_key VARCHAR NOT NULL,
    ingestion_version VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (canonical_game_id, possession_number)
);

-- ----------------------------------------------------------------------------- availability
-- Append-only. One row per (snapshot, player line). Raw values are never overwritten.
CREATE TABLE IF NOT EXISTS availability.events (
    availability_event_id   VARCHAR PRIMARY KEY,   -- hash(provider, snapshot content hash, line number)
    provider                VARCHAR NOT NULL,      -- sharp_stats
    snapshot_sha256         VARCHAR NOT NULL,      -- hash of the raw snapshot file in data/raw
    line_number             INTEGER NOT NULL,
    source_team_name        VARCHAR,
    source_player_name      VARCHAR NOT NULL,
    source_position         VARCHAR,
    source_class            VARCHAR,
    injury_raw              VARCHAR,
    status_raw              VARCHAR,
    status_normalized       VARCHAR NOT NULL,      -- PROBABLE|QUESTIONABLE|DOUBTFUL|OUT|OUT_SEASON|UNKNOWN
    note_raw                VARCHAR,
    source_updated_raw      VARCHAR,
    source_updated_at       TIMESTAMPTZ,           -- NULL unless the source gives an unambiguous time
    received_at             TIMESTAMPTZ NOT NULL,  -- authoritative PIT time for availability
    processed_at            TIMESTAMPTZ NOT NULL,
    conflict_flag           BOOLEAN NOT NULL,
    conflict_type           VARCHAR,
    canonical_team_id       VARCHAR,
    canonical_player_id     VARCHAR,
    identity_status         VARCHAR NOT NULL,
    parser_version          VARCHAR NOT NULL,
    is_synthetic_fixture    BOOLEAN NOT NULL DEFAULT FALSE,
    source_file VARCHAR NOT NULL
);

-- ----------------------------------------------------------------------------- benchmark
-- Physically isolated. flagship.* views must never reference this schema (tested).
CREATE TABLE IF NOT EXISTS benchmark.bdl_games (
    bdl_game_id BIGINT PRIMARY KEY, canonical_game_id VARCHAR, bdl_season_start_year INTEGER, cbbd_season INTEGER,
    game_date_utc TIMESTAMPTZ, status VARCHAR, postseason BOOLEAN,
    bdl_home_team_id BIGINT, bdl_visitor_team_id BIGINT, home_score INTEGER, away_score INTEGER,
    home_score_h1 INTEGER, away_score_h1 INTEGER, home_score_h2 INTEGER, away_score_h2 INTEGER,
    home_ot_scores INTEGER[], away_ot_scores INTEGER[],
    raw_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark.bdl_player_games (
    bdl_game_id BIGINT NOT NULL, bdl_player_id BIGINT NOT NULL, bdl_team_id BIGINT,
    canonical_game_id VARCHAR, canonical_player_id VARCHAR, canonical_team_id VARCHAR,
    player_first_name VARCHAR, player_last_name VARCHAR, minutes_raw VARCHAR,
    fgm INTEGER, fga INTEGER, three_pm INTEGER, three_pa INTEGER, ftm INTEGER, fta INTEGER,
    orb INTEGER, drb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER, points INTEGER,
    raw_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bdl_game_id, bdl_player_id)
);

CREATE TABLE IF NOT EXISTS benchmark.bdl_team_games (
    bdl_game_id BIGINT NOT NULL, bdl_team_id BIGINT NOT NULL, canonical_game_id VARCHAR, canonical_team_id VARCHAR,
    fgm INTEGER, fga INTEGER, three_pm INTEGER, three_pa INTEGER, ftm INTEGER, fta INTEGER,
    orb INTEGER, drb INTEGER, reb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER,
    raw_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bdl_game_id, bdl_team_id)
);

CREATE TABLE IF NOT EXISTS benchmark.game_odds (       -- BDL /odds (current); one row per vendor snapshot
    bdl_odds_id BIGINT NOT NULL, bdl_game_id BIGINT NOT NULL, canonical_game_id VARCHAR, vendor VARCHAR NOT NULL,
    spread_home_value DOUBLE, spread_home_price INTEGER, spread_away_value DOUBLE, spread_away_price INTEGER,
    moneyline_home_price INTEGER, moneyline_away_price INTEGER,
    total_value DOUBLE, total_over_price INTEGER, total_under_price INTEGER,
    source_updated_at TIMESTAMPTZ,                     -- BDL updated_at
    raw_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bdl_odds_id, raw_sha256)
);

CREATE TABLE IF NOT EXISTS benchmark.opening_odds (    -- BDL /odds/opening
    bdl_odds_id BIGINT NOT NULL, bdl_game_id BIGINT NOT NULL, canonical_game_id VARCHAR, vendor VARCHAR NOT NULL,
    spread_home_value DOUBLE, spread_home_price INTEGER, spread_away_value DOUBLE, spread_away_price INTEGER,
    moneyline_home_price INTEGER, moneyline_away_price INTEGER,
    total_value DOUBLE, total_over_price INTEGER, total_under_price INTEGER,
    opened_at TIMESTAMPTZ,                             -- BDL opened_at
    raw_sha256 VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bdl_odds_id, raw_sha256)
);

-- ----------------------------------------------------------------------------- metadata
CREATE TABLE IF NOT EXISTS metadata.team_id_map (
    provider VARCHAR NOT NULL, provider_team_key VARCHAR NOT NULL, canonical_team_id VARCHAR,
    season INTEGER,                 -- NULL = valid for all seasons
    match_method VARCHAR NOT NULL,  -- ANCHOR | EXACT_ID | EXACT_NAME | NORMALIZED_NAME | MANUAL | UNRESOLVED
    match_confidence DOUBLE NOT NULL, needs_manual_review BOOLEAN NOT NULL, source_context VARCHAR,
    created_by_version VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata.player_id_map (
    provider VARCHAR NOT NULL, provider_player_key VARCHAR NOT NULL, canonical_player_id VARCHAR,
    season INTEGER, provider_team_key VARCHAR,
    match_method VARCHAR NOT NULL, match_confidence DOUBLE NOT NULL, needs_manual_review BOOLEAN NOT NULL,
    source_context VARCHAR, created_by_version VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata.game_id_map (
    provider VARCHAR NOT NULL, provider_game_key VARCHAR NOT NULL, canonical_game_id VARCHAR,
    match_method VARCHAR NOT NULL, match_confidence DOUBLE NOT NULL, needs_manual_review BOOLEAN NOT NULL,
    source_context VARCHAR, created_by_version VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata.conference_id_map (
    provider VARCHAR NOT NULL, provider_conference_key VARCHAR NOT NULL, canonical_conference_id VARCHAR,
    match_method VARCHAR NOT NULL, match_confidence DOUBLE NOT NULL, needs_manual_review BOOLEAN NOT NULL,
    source_context VARCHAR, created_by_version VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata.source_manifest (
    source_system VARCHAR NOT NULL, source_path VARCHAR NOT NULL, sha256 VARCHAR NOT NULL, size_bytes BIGINT,
    endpoint VARCHAR, request_params VARCHAR, row_count BIGINT, received_at TIMESTAMPTZ NOT NULL,
    is_immutable_upstream BOOLEAN NOT NULL, notes VARCHAR
);
CREATE TABLE IF NOT EXISTS metadata.data_versions (
    build_id VARCHAR NOT NULL, table_name VARCHAR NOT NULL, row_count BIGINT NOT NULL,
    content_fingerprint VARCHAR NOT NULL,   -- order-independent hash of all columns except processed_at
    ingestion_version VARCHAR NOT NULL, schema_version VARCHAR NOT NULL, built_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (build_id, table_name)
);

-- ----------------------------------------------------------------------------- qa
CREATE TABLE IF NOT EXISTS qa.exceptions (
    exception_type VARCHAR NOT NULL,  -- e.g. UNRESOLVED_PLAYER, AMBIGUOUS_PLAYER, BDL_MISMATCH, DUPLICATE_FANOUT
    severity VARCHAR NOT NULL,        -- INFO | WARN | ERROR
    entity_key VARCHAR NOT NULL, detail VARCHAR, source_system VARCHAR NOT NULL, created_by_version VARCHAR NOT NULL
);

-- Raw season-summary value vs its game-derived twin, one row per disagreeing (team, season, field).
-- authoritative_source is always GAME_DERIVED for modeling; the raw summary is never modified.
CREATE TABLE IF NOT EXISTS qa.season_summary_conflicts (
    canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL, field VARCHAR NOT NULL,
    summary_value DOUBLE, game_derived_boxed_value DOUBLE, game_derived_all_results_value DOUBLE,
    authoritative_source VARCHAR NOT NULL
);

-- Raw player-season summary value vs its game-derived twin. authoritative_source = GAME_DERIVED where the
-- field is reconstructable (identity_complete); IDENTITY_GAP_NO_AUTHORITY where the derivation itself may
-- be missing games (unresolved player-game rows): neither value silently wins there.
CREATE TABLE IF NOT EXISTS qa.player_season_summary_conflicts (
    canonical_player_id VARCHAR NOT NULL, canonical_team_id VARCHAR NOT NULL, season INTEGER NOT NULL,
    field VARCHAR NOT NULL, summary_value DOUBLE, game_derived_value DOUBLE,
    identity_complete BOOLEAN NOT NULL, authoritative_source VARCHAR NOT NULL
);

-- Cross-source comparisons (CBBD vs BDL). Disagreements are reported here, never used to overwrite.
CREATE TABLE IF NOT EXISTS qa.reconciliation (
    entity_type VARCHAR NOT NULL,   -- GAME_SCORE | PLAYER_GAME | TEAM_GAME
    canonical_game_id VARCHAR NOT NULL, canonical_team_id VARCHAR, canonical_player_id VARCHAR,
    stat VARCHAR NOT NULL, cbbd_value DOUBLE, other_value DOUBLE, values_match BOOLEAN NOT NULL,
    other_source VARCHAR NOT NULL
);

-- ----------------------------------------------------------------------------- flagship
-- The flagship basketball-feature namespace: the ONLY place later PIT feature builders may
-- read from. It exposes basketball facts with their PIT columns and never touches benchmark.*.
-- tests/test_market_isolation.py proves no sportsbook column or benchmark reference appears here.
CREATE OR REPLACE VIEW flagship.games                  AS SELECT * FROM core.games;
CREATE OR REPLACE VIEW flagship.game_schedule_versions AS SELECT * FROM core.game_schedule_versions;
CREATE OR REPLACE VIEW flagship.team_seasons           AS SELECT * FROM core.team_seasons;
CREATE OR REPLACE VIEW flagship.player_game_outcomes   AS SELECT * FROM fact.player_games;
CREATE OR REPLACE VIEW flagship.team_game_outcomes     AS SELECT * FROM fact.team_games;
CREATE OR REPLACE VIEW flagship.participation          AS SELECT * FROM fact.participation;
CREATE OR REPLACE VIEW flagship.player_season_stats    AS SELECT * FROM fact.player_season_stats;
CREATE OR REPLACE VIEW flagship.player_season_shooting AS SELECT * FROM fact.player_season_shooting;
-- Raw season summaries: secondary / QA / completed-season prior only (game-derived wins on conflict).
CREATE OR REPLACE VIEW flagship.team_season_stats      AS SELECT * FROM fact.team_season_stats;
CREATE OR REPLACE VIEW flagship.team_season_game_derived AS SELECT * FROM fact.team_season_game_derived;
CREATE OR REPLACE VIEW flagship.player_season_game_derived AS SELECT * FROM fact.player_season_game_derived;
CREATE OR REPLACE VIEW flagship.team_season_shooting   AS SELECT * FROM fact.team_season_shooting;
-- Synthetic test fixtures can never reach the flagship path.
CREATE OR REPLACE VIEW flagship.availability_events    AS
    SELECT * FROM availability.events WHERE NOT is_synthetic_fixture;
