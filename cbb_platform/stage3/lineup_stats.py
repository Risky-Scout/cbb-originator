"""Stage 3 section 13: lineup / on-court interaction sufficient statistics (2023-24 and 2024-25 ONLY).

Source: trusted plays whose floor state is VALID_5V5 (src.on_floor = flagship.on_floor_trusted); every invalid,
>5, <5 or defect state is excluded, never repaired. For each trusted play i of a game, the clock interval
(t_{i-1}, t_i] (previous play of the same game, any quality) and the score change at play i are attributed to the
floor recorded at play i (the Stage 1 substitution convention in < t <= out). Negative intervals (clock order
violations) contribute 0 seconds and are counted.

Game-level sufficient statistics (persisted):
    feat.lineup_unit_game      team x five-player unit:   seconds, points for/against, offensive/defensive events
    feat.lineup_pair_game      team x player pair:        seconds, points for/against, possessions
    feat.player_oncourt_game   player:                    seconds, points for/against, possessions
    feat.lineup_matchup_game   home unit x away unit:     seconds, points
PIT state streams (cumulative across the lineup window, ordered by available_at) for later ASOF use:
    feat.lineup_unit_state, feat.lineup_pair_state, feat.player_oncourt_state
No exact-lineup parameter and no embedding is fitted.
"""
from __future__ import annotations

from cbb_platform.stage3 import pitlib as P, spec

LO, HI = spec.LINEUP_SEASONS
EL = """(CASE WHEN {p} <= 2 THEN ({p} - 1) * 1200 + 1200 - {s} ELSE 2400 + ({p} - 3) * 300 + 300 - {s} END)"""


def build(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS feat")
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE lp AS
    SELECT p.canonical_game_id, p.source_play_id, p.play_sequence, p.season, p.on_floor_quality, p.canonical_team_id AS act_team,
           p.play_type, p.shooting_play, p.scoring_play,
           {EL.format(p='p.period', s='p.seconds_remaining')} AS t,
           p.home_score - coalesce(lag(p.home_score) OVER g, 0) AS dh, p.away_score - coalesce(lag(p.away_score) OVER g, 0) AS da,
           {EL.format(p='p.period', s='p.seconds_remaining')} - coalesce(lag({EL.format(p='p.period', s='p.seconds_remaining')}) OVER g, 0) AS dt_raw,
           s.shot_range, s.made
    FROM src.plays p LEFT JOIN src.shots s USING (canonical_game_id, source_play_id)
    WHERE p.season BETWEEN {LO} AND {HI} AND p.period IS NOT NULL AND p.seconds_remaining IS NOT NULL
    WINDOW g AS (PARTITION BY p.canonical_game_id ORDER BY p.play_sequence);
    CREATE OR REPLACE TEMP TABLE side AS
    SELECT f.canonical_game_id, f.source_play_id, f.side, any_value(f.canonical_team_id) AS team_id,
           list_sort(list(f.canonical_player_id)) AS ids
    FROM src.on_floor f WHERE f.side IN ('HOME', 'AWAY')
    GROUP BY 1, 2, 3 HAVING count(*) = 5 AND count(DISTINCT f.canonical_player_id) = 5 AND count(f.canonical_player_id) = 5;
    CREATE OR REPLACE TEMP TABLE seg AS
    SELECT l.canonical_game_id, l.source_play_id, l.season, h.team_id AS home_team, a.team_id AS away_team, h.ids AS home_ids,
           a.ids AS away_ids, greatest(l.dt_raw, 0) AS dt, (l.dt_raw < 0)::INT AS clock_violation, l.dh, l.da, l.act_team,
           l.play_type, l.shot_range, l.made
    FROM lp l JOIN side h ON h.canonical_game_id = l.canonical_game_id AND h.source_play_id = l.source_play_id AND h.side = 'HOME'
    JOIN side a ON a.canonical_game_id = l.canonical_game_id AND a.source_play_id = l.source_play_id AND a.side = 'AWAY'
    WHERE l.on_floor_quality = 'VALID_5V5';
    -- one row per (segment, team side) with offense events of that side
    CREATE OR REPLACE TEMP TABLE seg_side AS
    SELECT canonical_game_id, source_play_id, season, team, opp_team, ids, opp_ids, dt, clock_violation, pts_for, pts_against,
           (act_team = team AND shot_range IN ('rim', 'jumper', 'three_pointer'))::INT AS fga,
           (act_team = team AND shot_range IN ('rim', 'jumper', 'three_pointer') AND made)::INT AS fgm,
           (act_team = team AND shot_range = 'three_pointer')::INT AS tpa, (act_team = team AND shot_range = 'three_pointer' AND made)::INT AS tpm,
           (act_team = team AND shot_range = 'free_throw')::INT AS fta,
           (act_team = team AND play_type ILIKE '%Turnover%')::INT AS tov,
           (act_team = team AND play_type = 'Offensive Rebound')::INT AS orb, (act_team = team AND play_type = 'Defensive Rebound')::INT AS drb,
           (act_team = opp_team AND shot_range IN ('rim', 'jumper', 'three_pointer'))::INT AS opp_fga,
           (act_team = opp_team AND shot_range = 'free_throw')::INT AS opp_fta,
           (act_team = opp_team AND play_type ILIKE '%Turnover%')::INT AS opp_tov,
           (act_team = opp_team AND play_type = 'Offensive Rebound')::INT AS opp_orb
    FROM (SELECT *, home_team AS team, away_team AS opp_team, home_ids AS ids, away_ids AS opp_ids, dh AS pts_for, da AS pts_against FROM seg
          UNION ALL
          SELECT *, away_team, home_team, away_ids, home_ids, da, dh FROM seg);
    """)
    sums = "sum(dt) AS seconds, sum(pts_for) AS pts_for, sum(pts_against) AS pts_against, sum(fga) AS fga, sum(fgm) AS fgm, " \
           "sum(tpa) AS tpa, sum(tpm) AS tpm, sum(fta) AS fta, sum(tov) AS tov, sum(orb) AS orb, sum(drb) AS drb, " \
           "sum(fga - orb + tov + 0.475 * fta) AS poss, sum(opp_fga - opp_orb + opp_tov + 0.475 * opp_fta) AS opp_poss, " \
           "sum(clock_violation) AS clock_violations, count(*) AS plays"
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.lineup_unit_game AS
    SELECT s.canonical_game_id AS source_game_id, s.season, s.team AS canonical_team_id, array_to_string(s.ids, '|') AS unit_key,
           any_value(s.ids) AS player_ids, any_value(g.scheduled_tip_utc) AS source_tip, any_value(g.historical_knowable_at) AS available_at, {sums}
    FROM seg_side s JOIN src.played_games g USING (canonical_game_id) GROUP BY 1, 2, 3, 4 ORDER BY 3, 7, 4;
    CREATE OR REPLACE TABLE feat.lineup_pair_game AS
    WITH pr AS (SELECT s.*, a.pid AS p1, b.pid AS p2 FROM seg_side s,
                LATERAL (SELECT unnest(s.ids) AS pid) a, LATERAL (SELECT unnest(s.ids) AS pid) b WHERE a.pid < b.pid)
    SELECT pr.canonical_game_id AS source_game_id, pr.season, pr.team AS canonical_team_id, p1, p2,
           any_value(g.scheduled_tip_utc) AS source_tip, any_value(g.historical_knowable_at) AS available_at,
           sum(dt) AS seconds, sum(pts_for) AS pts_for, sum(pts_against) AS pts_against,
           sum(fga - orb + tov + 0.475 * fta) AS poss, sum(opp_fga - opp_orb + opp_tov + 0.475 * opp_fta) AS opp_poss
    FROM pr JOIN src.played_games g USING (canonical_game_id) GROUP BY 1, 2, 3, 4, 5 ORDER BY 3, 4, 5, 7;
    CREATE OR REPLACE TABLE feat.player_oncourt_game AS
    WITH pl AS (SELECT s.*, u.pid FROM seg_side s, LATERAL (SELECT unnest(s.ids) AS pid) u)
    SELECT pl.canonical_game_id AS source_game_id, pl.season, pl.team AS canonical_team_id, pid AS canonical_player_id,
           any_value(g.scheduled_tip_utc) AS source_tip, any_value(g.historical_knowable_at) AS available_at,
           sum(dt) AS seconds, sum(pts_for) AS pts_for, sum(pts_against) AS pts_against,
           sum(fga - orb + tov + 0.475 * fta) AS poss, sum(opp_fga - opp_orb + opp_tov + 0.475 * opp_fta) AS opp_poss
    FROM pl JOIN src.played_games g USING (canonical_game_id) GROUP BY 1, 2, 3, 4 ORDER BY 4, 6;
    CREATE OR REPLACE TABLE feat.lineup_matchup_game AS
    SELECT s.canonical_game_id AS source_game_id, s.season, s.home_team, s.away_team, array_to_string(s.home_ids, '|') AS home_unit_key,
           array_to_string(s.away_ids, '|') AS away_unit_key, any_value(g.historical_knowable_at) AS available_at,
           sum(s.dt) AS seconds, sum(s.dh) AS home_pts, sum(s.da) AS away_pts
    FROM seg s JOIN src.played_games g USING (canonical_game_id) GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 5, 6;
    """)
    # cumulative PIT streams across the lineup window (prior season of the window is legal history)
    cum = lambda part, cols: ", ".join(f"sum({c}) OVER (PARTITION BY {part} ORDER BY available_at, source_game_id ROWS UNBOUNDED PRECEDING) AS c_{c}"
                                       for c in cols)
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.lineup_unit_state AS
    SELECT canonical_team_id, unit_key, player_ids, season, source_game_id, available_at,
           count(*) OVER (PARTITION BY canonical_team_id, unit_key ORDER BY available_at, source_game_id ROWS UNBOUNDED PRECEDING) AS c_games,
           {cum('canonical_team_id, unit_key', ['seconds', 'pts_for', 'pts_against', 'poss', 'opp_poss', 'fga', 'fgm', 'tpa', 'tpm', 'fta', 'tov', 'orb', 'drb'])}
    FROM feat.lineup_unit_game ORDER BY 1, 2, 6;
    CREATE OR REPLACE TABLE feat.lineup_pair_state AS
    SELECT canonical_team_id, p1, p2, season, source_game_id, available_at,
           count(*) OVER (PARTITION BY canonical_team_id, p1, p2 ORDER BY available_at, source_game_id ROWS UNBOUNDED PRECEDING) AS c_games,
           {cum('canonical_team_id, p1, p2', ['seconds', 'pts_for', 'pts_against', 'poss', 'opp_poss'])}
    FROM feat.lineup_pair_game ORDER BY 1, 2, 3, 6;
    CREATE OR REPLACE TABLE feat.player_oncourt_state AS
    SELECT canonical_player_id, canonical_team_id, season, source_game_id, available_at,
           {cum('canonical_player_id', ['seconds', 'pts_for', 'pts_against', 'poss', 'opp_poss'])},
           sum(seconds) OVER (PARTITION BY canonical_player_id, season ORDER BY available_at, source_game_id ROWS UNBOUNDED PRECEDING) AS s_seconds,
           sum(pts_for - pts_against) OVER (PARTITION BY canonical_player_id, season ORDER BY available_at, source_game_id ROWS UNBOUNDED PRECEDING) AS s_plus_minus,
           sum(poss) OVER (PARTITION BY canonical_player_id, season ORDER BY available_at, source_game_id ROWS UNBOUNDED PRECEDING) AS s_poss
    FROM feat.player_oncourt_game ORDER BY 1, 5;
    """)
    P.attach(con, "ex.player_game_examples", "feat.player_oncourt_state", ("canonical_player_id",), "f_onc",
             select="s.source_game_id AS onc_state_source_game_id, s.available_at AS onc_state_available_at, s.season AS onc_state_season, "
                    "s.c_seconds, s.c_pts_for, s.c_pts_against, s.c_poss, s.c_opp_poss, s.s_seconds, s.s_plus_minus, s.s_poss")
    R = P.ratio
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.player_lineup_features AS
    SELECT x.player_game_example_id, x.season, x.prediction_cutoff_utc, '{spec.STAGE3_VERSION}' AS transformation_version,
           CASE WHEN x.season BETWEEN {LO} AND {HI} THEN o.onc_state_source_game_id END AS onc_state_source_game_id,
           CASE WHEN x.season BETWEEN {LO} AND {HI} THEN o.onc_state_available_at END AS onc_state_available_at,
           CASE WHEN x.season BETWEEN {LO} AND {HI} THEN coalesce(o.c_seconds, 0) / 60.0 END AS lu_window_on_court_min,
           CASE WHEN x.season BETWEEN {LO} AND {HI} THEN CASE WHEN o.onc_state_season = x.season THEN coalesce(o.s_seconds, 0) / 60.0 ELSE 0 END END
               AS lu_season_on_court_min,
           CASE WHEN x.season BETWEEN {LO} AND {HI} AND o.onc_state_season = x.season THEN {R('o.s_plus_minus', 'o.s_poss', 100.0)} END
               AS lu_season_on_court_net_per100,
           CASE WHEN x.season BETWEEN {LO} AND {HI} THEN {R('o.c_pts_for - o.c_pts_against', 'o.c_poss', 100.0)} END AS lu_window_on_court_net_per100,
           CASE WHEN x.season BETWEEN {LO} AND {HI} THEN o.c_poss END AS lu_window_on_court_poss,
           CASE WHEN x.season NOT BETWEEN {LO} AND {HI} THEN 'SOURCE_NOT_AVAILABLE'
                WHEN coalesce(o.c_seconds, 0) = 0 THEN 'NO_PRIOR_HISTORY' ELSE 'OK' END AS lu_status
    FROM ex.player_game_examples x JOIN f_onc o USING (player_game_example_id) ORDER BY 1;
    """)


FAMILIES = {
    "player_lineup": ("On-court exposure and on-court net rating from trusted VALID_5V5 floor states (2023-24, 2024-25).",
                      "feat.player_oncourt_state", "lu_status"),
}
