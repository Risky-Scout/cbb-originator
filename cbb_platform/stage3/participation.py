"""Shared participation classifier (Stage 3 sections 6, 10). Used by FEATURES for PRIOR games and by the LABEL store
for TARGET games -- the same rules, never the same game.

For a set of (game, team, player) candidates:
    PLAYED               a box row with minutes > 0 or any counting stat > 0, or a substitution stint (2023-24+)
    DNP_CONFIRMED        2023-24+ only: the team-game substitution feed is COMPLETE (stint seconds within 5% of
                         5 x game seconds) and the player has no stint (and no positive box line)
    NOT_ON_ROSTER        no box row, not on this team's season roster, but on ANOTHER team's roster that season
    UNKNOWN_PARTICIPATION everything else (e.g. a 0-minute/no-stat box row, or simply no row). Absence of a box row is
                         NEVER converted into a DNP.
"""
from __future__ import annotations

ELAPSED = """(CASE WHEN {p} <= 2 THEN ({p} - 1) * 1200 + 1200 - {s} ELSE 2400 + ({p} - 3) * 300 + 300 - {s} END)"""


def sub_feed_sql() -> str:
    """team-game substitution feed completeness (src.substitutions + src.team_games)."""
    tin = ELAPSED.format(p="sub_in_period", s="sub_in_seconds_remaining")
    tout = ELAPSED.format(p="sub_out_period", s="sub_out_seconds_remaining")
    return f"""
    SELECT s.canonical_game_id, s.canonical_team_id, sum({tout} - {tin}) AS stint_seconds,
           max(t.game_minutes) * 60 * 5 AS expected_seconds,
           abs(sum({tout} - {tin}) - max(t.game_minutes) * 300) <= 0.05 * max(t.game_minutes) * 300 AS feed_complete
    FROM src.substitutions s JOIN src.team_games t USING (canonical_game_id, canonical_team_id)
    WHERE s.timing_complete GROUP BY 1, 2"""


def classify_sql(candidates: str) -> str:
    """`candidates` = relation(canonical_game_id, canonical_team_id, canonical_player_id, season). Returns rows with
    participation_status, participation_evidence and the box row fields needed downstream."""
    return f"""
    WITH c AS (SELECT DISTINCT canonical_game_id, canonical_team_id, canonical_player_id, season FROM {candidates}),
    box AS (SELECT canonical_game_id, canonical_team_id, canonical_player_id, minutes, starter,
                   coalesce(minutes, 0) > 0 OR greatest(points, fga, fta, reb, ast, stl, blk, tov, pf) > 0 AS positive
            FROM src.player_games WHERE canonical_player_id IS NOT NULL),
    feed AS ({sub_feed_sql()}),
    stint AS (SELECT DISTINCT canonical_game_id, canonical_team_id, canonical_player_id FROM src.substitutions
              WHERE canonical_player_id IS NOT NULL),
    ros AS (SELECT DISTINCT canonical_player_id, canonical_team_id, season FROM src.rosters)
    SELECT c.*, b.minutes AS box_minutes, b.starter AS box_starter, b.canonical_player_id IS NOT NULL AS box_row,
           CASE WHEN b.positive THEN 'PLAYED'
                WHEN st.canonical_player_id IS NOT NULL THEN 'PLAYED'
                WHEN f.feed_complete AND c.season >= 2024 THEN 'DNP_CONFIRMED'
                WHEN b.canonical_player_id IS NULL AND r.canonical_player_id IS NULL AND EXISTS (
                     SELECT 1 FROM ros o WHERE o.canonical_player_id = c.canonical_player_id AND o.season = c.season
                       AND o.canonical_team_id <> c.canonical_team_id) THEN 'NOT_ON_ROSTER'
                ELSE 'UNKNOWN_PARTICIPATION' END AS participation_status,
           CASE WHEN b.positive THEN 'BOX_ROW_POSITIVE'
                WHEN st.canonical_player_id IS NOT NULL THEN 'SUBSTITUTION_STINT'
                WHEN f.feed_complete AND c.season >= 2024 THEN 'COMPLETE_SUBSTITUTION_FEED_NO_STINT'
                WHEN b.canonical_player_id IS NULL AND r.canonical_player_id IS NULL THEN 'NO_BOX_ROW_NOT_ON_TEAM_ROSTER'
                WHEN b.canonical_player_id IS NOT NULL THEN 'BOX_ROW_ZERO_MINUTES_NO_STATS_AMBIGUOUS'
                ELSE 'NO_BOX_ROW' END AS participation_evidence
    FROM c
    LEFT JOIN box b USING (canonical_game_id, canonical_team_id, canonical_player_id)
    LEFT JOIN stint st USING (canonical_game_id, canonical_team_id, canonical_player_id)
    LEFT JOIN feed f USING (canonical_game_id, canonical_team_id)
    LEFT JOIN ros r ON r.canonical_player_id = c.canonical_player_id AND r.canonical_team_id = c.canonical_team_id
                   AND r.season = c.season
    """
