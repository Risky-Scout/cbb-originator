"""Stage 3 sections 5-6 (revised Stage 3.1): prediction universes (PREGAME information only).

ex.game_examples          one row per played D-I vs D-I contest, 2013-14 .. 2024-25 (the prediction targets)
ex.team_game_examples     two rows per target game (team perspective)
ex.team_membership_evidence  every LEGAL piece of evidence that a player belongs to a team-season, with the instant it
                          became knowable (available_from)
ex.player_game_examples   the PIT player pool of each team in each target game = members whose evidence was
                          knowable at the prediction cutoff (pool_evidence = strongest class)
ex.roster_snapshot_research  NON-PIT research table: end-of-season roster members NOT in the PIT pool at the cutoff

Roster PIT policy (architect, Stage 3.1). CBBD season rosters are END-OF-SEASON snapshots: a current-season roster
row never establishes pregame membership. Legal membership evidence classes:
    PRIOR_APPEARANCE_CONFIRMED     a box row for the team earlier this season (available at that game's knowable time)
    PRIOR_SEASON_RETURNER          the player's most recent earlier window season (s-1, or s-2 after one sit-out season) was
                                   with this team and he has played < 4 earlier seasons (< 5 for 2021-22 / 2022-23, the
                                   COVID extra year) -- past-only information (preseason)
    PRIOR_SEASON_ROSTER_RETURNER   on the team's PREVIOUS-season roster (a completed season's snapshot; preseason), with
                                   fewer than 4 seasons since its start_season (5 for 2021-22 / 2022-23)
    TRANSFER_PRESEASON_EVIDENCE    portal record year = season - 1, destination = team, identity via the ORIGIN roster
    RECRUIT_PRESEASON_EVIDENCE     recruiting class year = season - 1 committed to the team (provider athlete id)
Exclusion: a returner with an outbound portal record from this team (year = season - 1) is not a returner.
SEASON_ROSTER_ONLY_NOT_PIT_CONFIRMED rows live only in ex.roster_snapshot_research. The universe is never derived
from the target game's own box score or from any later appearance. Participation outcomes live in the label store.
Policy (architect): D-I vs non-D-I contests and non-D-I teams are NOT prediction targets, but every valid played
contest in the window MAY contribute history (state reads src.played_games, not ex.game_examples).
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

PRESEASON = "TIMESTAMPTZ '1900-01-01 00:00:00+00'"
EVIDENCE_RANK = ("PRIOR_APPEARANCE_CONFIRMED", "PRIOR_SEASON_RETURNER", "PRIOR_SEASON_ROSTER_RETURNER",
                 "TRANSFER_PRESEASON_EVIDENCE", "RECRUIT_PRESEASON_EVIDENCE")

MEMBERSHIP_SQL = f"""
CREATE OR REPLACE TABLE ex.team_membership_evidence AS
WITH ts AS (SELECT DISTINCT canonical_team_id, season FROM src.team_games),
box AS (SELECT canonical_player_id, canonical_team_id, season, min(historical_knowable_at) AS first_available_at
        FROM src.player_games WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2, 3),
last_team AS (   -- each player's most recent window season before s (s-1 or s-2: one sit-out season allowed), its team(s),
                 -- and how many distinct earlier seasons he already played (eligibility, past-only information)
    SELECT DISTINCT b1.canonical_player_id, s.season, b1.canonical_team_id
    FROM (SELECT DISTINCT season FROM ts) s
    JOIN box b1 ON b1.season < s.season
    WHERE b1.season = (SELECT max(b2.season) FROM box b2 WHERE b2.canonical_player_id = b1.canonical_player_id AND b2.season < s.season)
      AND b1.season >= s.season - 2
      AND {spec.link_guard('b1.canonical_player_id', 'b1.season', 's.season')}
      AND (SELECT count(DISTINCT b3.season) FROM box b3 WHERE b3.canonical_player_id = b1.canonical_player_id AND b3.season < s.season)
          < CASE WHEN s.season IN (2022, 2023) THEN 5 ELSE 4 END),
portal_out AS (SELECT coalesce(canonical_player_id, candidate_canonical_player_id) AS canonical_player_id, origin_team_id AS canonical_team_id,
                      transfer_year + 1 AS season FROM src.transfers
               WHERE identity_method IN ('RESOLVED_ROSTER_CORROBORATED', 'CANDIDATE_ORIGIN_ROSTER_NAME')),
ev AS (
    SELECT canonical_player_id, canonical_team_id, season, 'PRIOR_APPEARANCE_CONFIRMED' AS evidence_class, first_available_at AS available_from
    FROM box
    UNION ALL
    SELECT l.canonical_player_id, l.canonical_team_id, l.season, 'PRIOR_SEASON_RETURNER', {PRESEASON}
    FROM last_team l JOIN ts USING (canonical_team_id, season)
    UNION ALL
    SELECT r.canonical_player_id, r.canonical_team_id, r.season + 1, 'PRIOR_SEASON_ROSTER_RETURNER', {PRESEASON}
    FROM src.rosters r JOIN ts ON ts.canonical_team_id = r.canonical_team_id AND ts.season = r.season + 1
    -- eligibility from the COMPLETED previous-season snapshot (start_season is past information)
    WHERE (r.start_season IS NULL OR (r.season + 1) - r.start_season < CASE WHEN r.season + 1 IN (2022, 2023) THEN 5 ELSE 4 END)
      AND {spec.link_guard('r.canonical_player_id', 'r.season', 'r.season + 1')}
    UNION ALL
    SELECT coalesce(t.canonical_player_id, t.candidate_canonical_player_id), t.destination_team_id, t.transfer_year + 1,
           'TRANSFER_PRESEASON_EVIDENCE', {PRESEASON}
    FROM src.transfers t JOIN ts ON ts.canonical_team_id = t.destination_team_id AND ts.season = t.transfer_year + 1
    WHERE t.identity_method IN ('RESOLVED_ROSTER_CORROBORATED', 'CANDIDATE_ORIGIN_ROSTER_NAME')
    UNION ALL
    SELECT r.canonical_player_id, r.committed_team_id, r.recruit_year + 1, 'RECRUIT_PRESEASON_EVIDENCE', {PRESEASON}
    FROM src.recruits r JOIN ts ON ts.canonical_team_id = r.committed_team_id AND ts.season = r.recruit_year + 1
    WHERE r.canonical_player_id IS NOT NULL)
SELECT e.* FROM ev e
WHERE e.canonical_player_id IS NOT NULL
  AND NOT (e.evidence_class IN ('PRIOR_SEASON_RETURNER', 'PRIOR_SEASON_ROSTER_RETURNER') AND EXISTS (
           SELECT 1 FROM portal_out o WHERE o.canonical_player_id = e.canonical_player_id
             AND o.canonical_team_id = e.canonical_team_id AND o.season = e.season))
  AND NOT EXISTS (SELECT 1 FROM src.identity_conflicts c WHERE c.canonical_player_id = e.canonical_player_id AND c.season = e.season)
ORDER BY ALL;
"""

PLAYER_GAME_EXAMPLES_SQL = f"""
CREATE OR REPLACE TABLE ex.player_game_examples AS
SELECT 'PX-' || substr(sha256('stage3|player_game|' || t.canonical_game_id || '|' || t.canonical_team_id || '|'
                              || m.canonical_player_id), 1, 20) AS player_game_example_id,
       t.game_example_id, t.canonical_game_id, m.canonical_player_id, t.canonical_team_id, t.opponent_team_id,
       t.is_home, t.neutral_site, t.season, t.prediction_cutoff_utc,
       arg_min(m.evidence_class, list_position({list(EVIDENCE_RANK)}, m.evidence_class)) AS pool_evidence,
       bool_or(m.evidence_class = 'PRIOR_APPEARANCE_CONFIRMED') AS ev_prior_appearance,
       bool_or(m.evidence_class = 'PRIOR_SEASON_RETURNER') AS ev_prior_season_returner,
       bool_or(m.evidence_class = 'PRIOR_SEASON_ROSTER_RETURNER') AS ev_prior_season_roster,
       bool_or(m.evidence_class = 'TRANSFER_PRESEASON_EVIDENCE') AS ev_transfer_preseason,
       bool_or(m.evidence_class = 'RECRUIT_PRESEASON_EVIDENCE') AS ev_recruit_preseason
FROM ex.team_game_examples t
JOIN ex.team_membership_evidence m ON m.canonical_team_id = t.canonical_team_id AND m.season = t.season
     AND m.available_from <= t.prediction_cutoff_utc
GROUP BY ALL
ORDER BY 2, 5, 4;
"""

ROSTER_RESEARCH_SQL = """
CREATE OR REPLACE TABLE ex.roster_snapshot_research AS   -- NON-PIT: never a feature, never a pool criterion
SELECT t.game_example_id, t.canonical_game_id, r.canonical_player_id, t.canonical_team_id, t.season,
       'SEASON_ROSTER_ONLY_NOT_PIT_CONFIRMED' AS roster_knowledge_class
FROM ex.team_game_examples t
JOIN (SELECT DISTINCT canonical_player_id, canonical_team_id, season FROM src.rosters) r
  ON r.canonical_team_id = t.canonical_team_id AND r.season = t.season
WHERE NOT EXISTS (SELECT 1 FROM ex.player_game_examples x WHERE x.game_example_id = t.game_example_id
                  AND x.canonical_team_id = t.canonical_team_id AND x.canonical_player_id = r.canonical_player_id)
ORDER BY ALL;
"""


def build(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS ex")
    con.execute("CREATE OR REPLACE TABLE ex.identity_conflicts AS SELECT * FROM src.identity_conflicts ORDER BY ALL")
    con.execute(GAME_EXAMPLES_SQL)
    con.execute(TEAM_GAME_EXAMPLES_SQL)
    con.execute(MEMBERSHIP_SQL)
    con.execute(PLAYER_GAME_EXAMPLES_SQL)
    con.execute(ROSTER_RESEARCH_SQL)
