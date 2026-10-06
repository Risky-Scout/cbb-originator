"""Stage 2.2 -- game status resolution: only contests with POSITIVE evidence of having been played enter the
modeling universe (flagship.played_games). Built inside build_canonical AFTER every box and event fact exists.

One row per trusted game (flagship.games, audited seasons 2004-05..2024-25) in metadata.game_status_resolution.

Evidence (any one confirms play):
    box       team or player box-score rows
    events    PBP plays, shots, substitutions or lineup units
    periods   supplied per-period points, both sides with >= 2 periods (a forfeit carries one entry per side)
    result    an official final score that is neither 0-0 nor a forfeit signature (2-0 / 1-0)
Status (first matching rule):
    PLAYED_CONFIRMED          any evidence above
    UNPLAYED_OR_CANCELLED     an explicit source cancellation/postponement indicator (none exists in current data)
    SCHEDULE_PLACEHOLDER      no evidence, and a team of the record played a DIFFERENT scored contest on the same
                              ET date, or within one day in the same named event (the slot went to another matchup)
    SOURCE_STATUS_UNRESOLVED  no evidence and no placeholder signal (includes 0-0 and forfeit-signature records)
Every status other than PLAYED_CONFIRMED is excluded from played games. Scores are never fabricated and no
winner is inferred; the raw schedule record stays in core.games / flagship.games.
"""
from __future__ import annotations

import duckdb

VERSION = "stage2.2-game-status-1.0.0"
FORFEIT_SIGNATURES = ((2, 0), (0, 2), (1, 0), (0, 1))

STATUS_SQL = f"""
INSERT INTO metadata.game_status_resolution
WITH g AS (SELECT * FROM flagship.games),
ev AS (
    SELECT g.canonical_game_id,
           (SELECT count(*) FROM fact.team_games t WHERE t.canonical_game_id = g.canonical_game_id) AS team_box,
           (SELECT count(*) FROM fact.player_games p WHERE p.canonical_game_id = g.canonical_game_id) AS player_box,
           (SELECT count(*) FROM fact.plays p WHERE p.canonical_game_id = g.canonical_game_id) AS plays,
           (SELECT count(*) FROM fact.shots s WHERE s.canonical_game_id = g.canonical_game_id) AS shots,
           (SELECT count(*) FROM fact.substitutions s WHERE s.canonical_game_id = g.canonical_game_id) AS subs,
           (SELECT count(*) FROM fact.lineup_stats l WHERE l.canonical_game_id = g.canonical_game_id) AS lineups,
           -- per-period scores count only when BOTH sides have >= 2 periods (a forfeit carries one entry each)
           CASE WHEN len(coalesce(g.home_period_points, [])) >= 2 AND len(coalesce(g.away_period_points, [])) >= 2
                THEN len(g.home_period_points) + len(g.away_period_points) ELSE 0 END AS period_values,
           g.home_points IS NOT NULL AND g.away_points IS NOT NULL AND NOT (g.home_points = 0 AND g.away_points = 0)
             AND (g.home_points, g.away_points) NOT IN {tuple(FORFEIT_SIGNATURES)} AS real_result
    FROM g),
ph AS (   -- placeholder signal: a team of the record played a different scored contest that day / that event
    SELECT g.canonical_game_id, EXISTS (
        SELECT 1 FROM g r WHERE r.canonical_game_id <> g.canonical_game_id
          AND r.home_points IS NOT NULL AND NOT (r.home_points = 0 AND r.away_points = 0)
          AND (r.home_team_id IN (g.home_team_id, g.away_team_id) OR r.away_team_id IN (g.home_team_id, g.away_team_id))
          AND (r.game_date_et = g.game_date_et
               OR (abs(date_diff('day', r.game_date_et, g.game_date_et)) <= 1 AND r.notes = g.notes))) AS slot_filled
    FROM g)
SELECT g.canonical_game_id, replace(g.source_record_key, 'id=', ''), g.season,
       CASE WHEN e.team_box + e.player_box + e.plays + e.shots + e.subs + e.lineups + e.period_values > 0 OR e.real_result
                 THEN 'PLAYED_CONFIRMED'
            WHEN p.slot_filled THEN 'SCHEDULE_PLACEHOLDER'
            ELSE 'SOURCE_STATUS_UNRESOLVED' END,
       'team_box=' || e.team_box || ';player_box=' || e.player_box || ';plays=' || e.plays || ';shots=' || e.shots
           || ';subs=' || e.subs || ';lineups=' || e.lineups || ';period_values=' || e.period_values
           || ';final=' || coalesce(g.home_points::VARCHAR, 'NULL') || '-' || coalesce(g.away_points::VARCHAR, 'NULL')
           || ';slot_filled_by_other_contest=' || p.slot_filled,
       NOT (e.team_box + e.player_box + e.plays + e.shots + e.subs + e.lineups + e.period_values > 0 OR e.real_result),
       CASE WHEN e.team_box + e.player_box > 0 THEN 'EVIDENCE_BOX_SCORE'
            WHEN e.plays + e.shots + e.subs + e.lineups > 0 THEN 'EVIDENCE_EVENT_DATA'
            WHEN e.period_values > 0 THEN 'EVIDENCE_PERIOD_SCORES'
            WHEN e.real_result THEN 'EVIDENCE_OFFICIAL_NONZERO_FINAL_SCORE'
            WHEN p.slot_filled THEN 'NO_EVIDENCE_SLOT_FILLED_BY_OTHER_CONTEST'
            WHEN (g.home_points, g.away_points) IN {tuple(FORFEIT_SIGNATURES)} THEN 'NO_EVIDENCE_FORFEIT_SIGNATURE_SCORE'
            ELSE 'NO_EVIDENCE_ZERO_ZERO_OR_MISSING_SCORE' END,
       '{VERSION}'
FROM g JOIN ev e USING (canonical_game_id) JOIN ph p USING (canonical_game_id)
"""


def resolve(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(STATUS_SQL)
    return dict(con.execute("SELECT status, count(*) FROM metadata.game_status_resolution GROUP BY 1").fetchall())
