"""Stage 3 sections 5-6: prediction universes (PREGAME information only).

ex.game_examples          one row per played D-I vs D-I contest, 2013-14 .. 2024-25 (the prediction targets)
ex.team_game_examples     two rows per target game (team perspective)
ex.player_game_examples   the PREGAME player universe of each team in each target game

Policy (architect): D-I vs non-D-I contests and non-D-I teams are NOT prediction targets, but every valid played
contest in the window MAY contribute history (team and player state read src.played_games, not ex.game_examples).

Player universe of (target game, team) = players on the team's season roster  UNION  players who had a box row for
the team in an EARLIER game of the season whose data was knowable at the cutoff. It is never derived from the target
game's own box score; participation outcomes (PLAYED / DNP_CONFIRMED / UNKNOWN / NOT_ON_ROSTER) live in the label
store. CBBD rosters are season snapshots (retrospective membership); the flag `roster_pit_quality` records this.
"""
from __future__ import annotations

from cbb_platform.stage3 import spec

GAME_EXAMPLES_SQL = f"""
CREATE OR REPLACE TABLE ex.game_examples AS
SELECT 'GX-' || substr(sha256('stage3|game|' || g.canonical_game_id), 1, 16) AS game_example_id,
       g.canonical_game_id, g.season, g.season_type, g.game_type, g.tournament, g.scheduled_tip_utc, g.tip_time_quality,
       g.game_date_et, g.home_team_id, g.away_team_id, g.neutral_site, g.canonical_venue_id AS venue_id,
       g.conference_game, g.home_conference_id, g.away_conference_id,
       CASE WHEN g.tip_time_quality = 'EXACT' AND g.scheduled_tip_utc IS NOT NULL
            THEN g.scheduled_tip_utc - INTERVAL {spec.CUTOFF_OFFSET_HOURS} HOUR
            ELSE timezone('America/New_York', CAST(g.game_date_et AS TIMESTAMP)) END AS prediction_cutoff_utc,
       CASE WHEN g.tip_time_quality = 'EXACT' THEN 'TIP_MINUS_1H' ELSE 'ET_GAME_DATE_START' END AS cutoff_rule,
       '{spec.STAGE3_VERSION}' AS transformation_version
FROM src.played_games g
WHERE g.game_universe = 'DI_VS_DI'
ORDER BY g.scheduled_tip_utc, g.canonical_game_id;
"""

TEAM_GAME_EXAMPLES_SQL = """
CREATE OR REPLACE TABLE ex.team_game_examples AS
SELECT game_example_id, canonical_game_id, season, home_team_id AS canonical_team_id, away_team_id AS opponent_team_id,
       TRUE AS is_home, neutral_site, scheduled_tip_utc, game_date_et, prediction_cutoff_utc
FROM ex.game_examples
UNION ALL
SELECT game_example_id, canonical_game_id, season, away_team_id, home_team_id, FALSE, neutral_site, scheduled_tip_utc,
       game_date_et, prediction_cutoff_utc
FROM ex.game_examples
ORDER BY 1, 4;
"""

PLAYER_GAME_EXAMPLES_SQL = """
CREATE OR REPLACE TABLE ex.player_game_examples AS
WITH first_seen AS (     -- the first moment each player's membership of a team-season became knowable
    SELECT canonical_player_id, canonical_team_id, season, min(historical_knowable_at) AS first_available_at
    FROM src.player_games WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2, 3),
roster AS (SELECT DISTINCT canonical_player_id, canonical_team_id, season FROM src.rosters),
cand AS (
    SELECT t.game_example_id, t.canonical_game_id, t.season, t.canonical_team_id, t.opponent_team_id, t.is_home,
           t.neutral_site, t.prediction_cutoff_utc, r.canonical_player_id, TRUE AS on_roster
    FROM ex.team_game_examples t JOIN roster r ON r.canonical_team_id = t.canonical_team_id AND r.season = t.season
    UNION ALL
    SELECT t.game_example_id, t.canonical_game_id, t.season, t.canonical_team_id, t.opponent_team_id, t.is_home,
           t.neutral_site, t.prediction_cutoff_utc, f.canonical_player_id, FALSE
    FROM ex.team_game_examples t JOIN first_seen f ON f.canonical_team_id = t.canonical_team_id AND f.season = t.season
     AND f.first_available_at <= t.prediction_cutoff_utc)
SELECT 'PX-' || substr(sha256('stage3|player_game|' || c.canonical_game_id || '|' || c.canonical_team_id || '|'
                              || c.canonical_player_id), 1, 20) AS player_game_example_id,
       c.game_example_id, c.canonical_game_id, c.canonical_player_id, c.canonical_team_id, c.opponent_team_id,
       c.is_home, c.neutral_site, c.season, c.prediction_cutoff_utc,
       bool_or(c.on_roster) AS on_season_roster,
       bool_or(NOT c.on_roster) AS prior_participant_this_season,
       CASE WHEN bool_or(c.on_roster) THEN 'RETROSPECTIVE_SEASON_SNAPSHOT' ELSE 'NOT_ON_ROSTER_SNAPSHOT' END AS roster_pit_quality
FROM cand c
GROUP BY ALL
ORDER BY 2, 5, 4;
"""


def build(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS ex")
    con.execute("CREATE OR REPLACE TABLE ex.identity_conflicts AS SELECT * FROM src.identity_conflicts ORDER BY ALL")
    con.execute(GAME_EXAMPLES_SQL)
    con.execute(TEAM_GAME_EXAMPLES_SQL)
    con.execute(PLAYER_GAME_EXAMPLES_SQL)
