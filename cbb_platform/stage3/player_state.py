"""Stage 3 sections 9-11 and 16: point-in-time PLAYER state, participation / rotation state, minutes-role inputs and
cold-start inputs. One feature row per player-game example (feat.player_features).

State streams (persisted sufficient statistics, partition = player-season unless noted):
    feat.player_box_state           every prior box row of the player (any team): sums, last-N, EWM, last game
    feat.player_participation_state every prior TEAM game of the player's team-season, classified by the shared
                                    participation rules (PLAYED / DNP_CONFIRMED / UNKNOWN / NOT_ON_ROSTER)
Usage proxy = sum(FGA + 0.44 FTA + TOV) / sum(team (FGA + 0.44 FTA + TOV) x player minutes / team minutes).
Team-level rotation inputs (depth, active count, returning-minutes share) are computed from the PREGAME features of
the team's own example rows, so they use nothing beyond each player's legal history.
"""
from __future__ import annotations

from cbb_platform.stage3 import participation as PART, pitlib as P, spec

PP = "canonical_player_id, season"
STATS = ["pts", "two_pm", "two_pa", "tpm", "tpa", "ftm", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf"]
PER40 = ["pts", "two_pa", "tpa", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf"]


def build(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS feat")
    con.execute("""
    CREATE OR REPLACE TEMP TABLE pg AS
    SELECT p.canonical_player_id, p.season, p.canonical_team_id, p.canonical_game_id AS source_game_id,
           g.scheduled_tip_utc AS source_tip, p.historical_knowable_at AS available_at,
           coalesce(p.minutes, 0) AS mins, coalesce(p.starter, FALSE)::INT AS st,
           (coalesce(p.minutes, 0) > 0 OR greatest(p.points, p.fga, p.fta, p.reb, p.ast, p.stl, p.blk, p.tov, p.pf) > 0)::INT AS app,
           p.points AS pts, p.two_pm, p.two_pa, p.three_pm AS tpm, p.three_pa AS tpa, p.ftm, p.fta, p.orb, p.drb, p.ast,
           p.stl, p.blk, p.tov, p.pf,
           p.fga + 0.44 * p.fta + p.tov AS used,
           -- team possessions used while the player was on the floor = team used x (player minutes / game minutes)
           CASE WHEN t.game_minutes > 0 THEN (t.fga + 0.44 * t.fta + t.tov_total) * coalesce(p.minutes, 0) / t.game_minutes
                END AS team_used_on
    FROM src.player_games p JOIN src.played_games g USING (canonical_game_id)
    LEFT JOIN src.team_games t ON t.canonical_game_id = p.canonical_game_id AND t.canonical_team_id = p.canonical_team_id
    WHERE p.canonical_player_id IS NOT NULL AND p.accounting_issues IS NULL;
    """)
    cols = [f"{P.std('1', PP)} AS n_rows", f"{P.std('app', PP)} AS n_app", f"{P.std('st', PP)} AS n_starts",
            f"{P.std('mins', PP)} AS s_min", f"{P.std('used', PP)} AS s_used", f"{P.std('team_used_on', PP)} AS s_team_used_on"]
    cols += [f"{P.std(c, PP)} AS s_{c}" for c in STATS]
    for n in spec.ROLLING_WINDOWS:
        cols += [f"{P.roll('app', PP, n)} AS l{n}_app", f"{P.roll('st', PP, n)} AS l{n}_st", f"{P.roll('mins', PP, n)} AS l{n}_min",
                 f"{P.roll('pts', PP, n)} AS l{n}_pts", f"{P.roll('used', PP, n)} AS l{n}_used",
                 f"{P.roll('team_used_on', PP, n)} AS l{n}_team_used_on", f"{P.roll('two_pa + tpa + fta', PP, n)} AS l{n}_shots"]
    cols += [f"{P.ewm('app', PP)} AS e_app", f"{P.ewm('st', PP)} AS e_st", f"{P.ewm('1', PP)} AS e_w", f"{P.ewm('mins', PP)} AS e_min",
             f"{P.ewm('pts', PP)} AS e_pts", f"{P.ewm('used', PP)} AS e_used", f"{P.ewm('team_used_on', PP)} AS e_team_used_on"]
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE pg_k AS SELECT *, row_number() OVER {P.w(PP)} AS k FROM pg;
    CREATE OR REPLACE TABLE feat.player_box_state AS
    SELECT canonical_player_id, season, canonical_team_id AS last_team_id, source_game_id, source_tip, available_at,
           mins AS last_min, st AS last_st, app AS last_app, {', '.join(cols)},
           max(CASE WHEN app = 1 THEN source_tip END) OVER (PARTITION BY {PP} ORDER BY available_at, source_tip, source_game_id
                                                           ROWS UNBOUNDED PRECEDING) AS last_app_tip
    FROM pg_k ORDER BY 1, 2, 6;
    """)
    # ---------------------------------------------------------------- participation history (team-game based)
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE part_cand AS
    SELECT DISTINCT tg.canonical_game_id, tg.canonical_team_id, c.canonical_player_id, tg.season
    FROM src.team_games tg
    JOIN (SELECT canonical_player_id, canonical_team_id, season FROM src.rosters
          UNION SELECT canonical_player_id, canonical_team_id, season FROM src.player_games WHERE canonical_player_id IS NOT NULL) c
      ON c.canonical_team_id = tg.canonical_team_id AND c.season = tg.season;
    CREATE OR REPLACE TEMP TABLE part AS
    SELECT x.*, g.scheduled_tip_utc AS source_tip, g.historical_knowable_at AS available_at, x.canonical_game_id AS source_game_id
    FROM ({PART.classify_sql('part_cand')}) x JOIN src.played_games g USING (canonical_game_id);
    CREATE OR REPLACE TABLE feat.player_participation_state AS
    SELECT canonical_player_id, canonical_team_id, season, source_game_id, source_tip, available_at,
           participation_status AS last_status,
           {P.std('1', 'canonical_player_id, canonical_team_id, season')} AS n_team_games,
           {P.std("(participation_status = 'PLAYED')::INT", 'canonical_player_id, canonical_team_id, season')} AS n_played,
           {P.std("(participation_status = 'DNP_CONFIRMED')::INT", 'canonical_player_id, canonical_team_id, season')} AS n_dnp,
           {P.std("(participation_status = 'UNKNOWN_PARTICIPATION')::INT", 'canonical_player_id, canonical_team_id, season')} AS n_unknown,
           {P.roll("(participation_status = 'PLAYED')::INT", 'canonical_player_id, canonical_team_id, season', 3)} AS l3_played,
           {P.roll("(participation_status = 'PLAYED')::INT", 'canonical_player_id, canonical_team_id, season', 5)} AS l5_played,
           -- team games since the player's last PLAYED game (0 = played the team's last game)
           {P.std('1', 'canonical_player_id, canonical_team_id, season')}
             - coalesce(max(CASE WHEN participation_status = 'PLAYED' THEN k END) OVER (PARTITION BY canonical_player_id,
                 canonical_team_id, season ORDER BY available_at, source_tip, source_game_id ROWS UNBOUNDED PRECEDING), 0)
             AS games_since_played
    FROM (SELECT *, row_number() OVER {P.w('canonical_player_id, canonical_team_id, season')} AS k FROM part)
    ORDER BY 1, 2, 3, 6;
    """)
    # ---------------------------------------------------------------- attach to examples
    P.attach(con, "ex.player_game_examples", "feat.player_box_state", ("canonical_player_id", "season"), "f_pbox",
             select="s.source_game_id AS pbox_state_source_game_id, s.available_at AS pbox_state_available_at, s.* EXCLUDE "
                    "(canonical_player_id, season, source_game_id, source_tip, available_at)")
    P.attach(con, "ex.player_game_examples", "feat.player_participation_state",
             ("canonical_player_id", "canonical_team_id", "season"), "f_part",
             select="s.source_game_id AS part_state_source_game_id, s.available_at AS part_state_available_at, s.n_team_games, "
                    "s.n_played, s.n_dnp, s.n_unknown, s.l3_played, s.l5_played, s.games_since_played, s.last_status")
    # previous season (inside the window) and career-in-window history
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE pseason AS
    SELECT canonical_player_id, season, last_team_id AS team_id, n_app, n_rows, s_min, s_pts, s_two_pa, s_tpa, s_fta, s_ftm,
           s_orb, s_drb, s_ast, s_used, s_team_used_on
    FROM feat.player_box_state QUALIFY row_number() OVER (PARTITION BY canonical_player_id, season ORDER BY available_at DESC) = 1;
    CREATE OR REPLACE TEMP TABLE team_prev_minutes AS
    SELECT canonical_team_id, season, sum(coalesce(minutes, 0)) AS team_min FROM src.player_games GROUP BY 1, 2;
    CREATE OR REPLACE TEMP TABLE player_team_prev_minutes AS
    SELECT canonical_player_id, canonical_team_id, season, sum(coalesce(minutes, 0)) AS min_for_team
    FROM src.player_games WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2, 3;
    CREATE OR REPLACE TEMP TABLE rec AS     -- recruiting class known before the season (class year < season)
    SELECT canonical_player_id, arg_max(stars, recruit_year) AS stars, arg_max(rating, recruit_year) AS rating,
           arg_max(ranking, recruit_year) AS ranking, max(recruit_year) AS recruit_year
    FROM src.recruits WHERE canonical_player_id IS NOT NULL GROUP BY 1;
    CREATE OR REPLACE TEMP TABLE trf AS     -- portal record of year Y feeds season Y+1
    SELECT canonical_player_id, transfer_year + 1 AS season, destination_team_id, origin_team_id, stars, rating
    FROM src.transfers WHERE canonical_player_id IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY canonical_player_id, transfer_year ORDER BY source_transfer_id) = 1;
    CREATE OR REPLACE TEMP TABLE ros AS
    SELECT canonical_player_id, canonical_team_id, season, any_value(position) AS position, any_value(height_inches) AS height_in,
           any_value(weight_lbs) AS weight_lb, min(start_season) AS roster_start_season
    FROM src.rosters GROUP BY 1, 2, 3;
    """)
    R = P.ratio
    MIN = spec.MIN_OBS["player"]
    st = lambda n: (f"CASE WHEN coalesce({n}, 0) = 0 THEN 'NO_PRIOR_HISTORY' WHEN {n} < {MIN} THEN 'INSUFFICIENT_SAMPLE' ELSE 'OK' END")
    per40 = ", ".join(f"{R(f'b.s_{c}', 'b.s_min', 40.0)} AS pstd_{c}_per40" for c in PER40)
    sums = ", ".join(f"b.s_{c} AS pstd_sum_{c}" for c in STATS)
    recent = []
    for n in spec.ROLLING_WINDOWS:
        recent += [f"b.l{n}_app AS pr_l{n}_appearances", f"b.l{n}_st AS pr_l{n}_starts",
                   f"{R(f'b.l{n}_min', f'b.l{n}_app')} AS pr_l{n}_min_per_app", f"{R(f'b.l{n}_pts', f'b.l{n}_app')} AS pr_l{n}_pts_per_app",
                   f"{R(f'b.l{n}_used', f'b.l{n}_team_used_on')} AS pr_l{n}_usage", f"{R(f'b.l{n}_shots', f'b.l{n}_min', 40.0)} AS pr_l{n}_shots_per40"]
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE pf1 AS
    SELECT x.player_game_example_id, x.game_example_id, x.canonical_game_id, x.canonical_player_id, x.canonical_team_id,
           x.opponent_team_id, x.season, x.is_home, x.prediction_cutoff_utc, '{spec.STAGE3_VERSION}' AS transformation_version,
           b.pbox_state_source_game_id, b.pbox_state_available_at, pa.part_state_source_game_id, pa.part_state_available_at,
           -- player_season_to_date
           coalesce(b.n_rows, 0) AS pstd_box_rows, coalesce(b.n_app, 0) AS pstd_appearances, coalesce(b.n_starts, 0) AS pstd_starts,
           b.s_min AS pstd_sum_min, {sums},
           {R('b.s_min', 'b.n_app')} AS pstd_min_per_app, {R('b.s_pts', 'b.n_app')} AS pstd_pts_per_app,
           {R('b.s_orb + b.s_drb', 'b.n_app')} AS pstd_reb_per_app, {R('b.s_ast', 'b.n_app')} AS pstd_ast_per_app,
           {per40},
           {R('b.s_ftm', 'b.s_fta')} AS pstd_ft_pct, {R('b.s_two_pm', 'b.s_two_pa')} AS pstd_two_pct, {R('b.s_tpm', 'b.s_tpa')} AS pstd_three_pct,
           {R('b.s_tpa', 'b.s_tpa + b.s_two_pa')} AS pstd_three_rate, {R('b.s_used', 'b.s_team_used_on')} AS pstd_usage,
           {st('b.n_app')} AS pstd_status,
           -- player_recent
           {', '.join(recent)},
           {R('b.e_min', 'b.e_app')} AS pr_ewm_min_per_app, {R('b.e_pts', 'b.e_app')} AS pr_ewm_pts_per_app,
           {R('b.e_used', 'b.e_team_used_on')} AS pr_ewm_usage, {R('b.e_st', 'b.e_w')} AS pr_ewm_start_rate,
           b.last_min AS pr_last_box_minutes, b.last_st AS pr_last_box_started,
           {st('b.n_rows')} AS pr_status,   -- recent windows count box rows (a DNP box row is a real non-start)
           -- player_participation (team-game based history)
           coalesce(pa.n_team_games, 0) AS pp_team_games, coalesce(pa.n_played, 0) AS pp_played, coalesce(pa.n_dnp, 0) AS pp_dnp_confirmed,
           coalesce(pa.n_unknown, 0) AS pp_unknown, {R('pa.n_played', 'pa.n_team_games')} AS pp_participation_rate,
           pa.l3_played AS pp_l3_played, pa.l5_played AS pp_l5_played, pa.games_since_played AS pp_games_since_played,
           pa.last_status AS pp_last_game_participation,
           CASE WHEN coalesce(pa.n_team_games, 0) = 0 THEN 'NO_PRIOR_HISTORY' ELSE 'OK' END AS pp_status,
           -- player_prior_season (previous season in window)
           ps.n_app AS pps_appearances, {R('ps.s_min', 'ps.n_app')} AS pps_min_per_app, {R('ps.s_pts', 'ps.s_min', 40.0)} AS pps_pts_per40,
           {R('ps.s_orb + ps.s_drb', 'ps.s_min', 40.0)} AS pps_reb_per40, {R('ps.s_ast', 'ps.s_min', 40.0)} AS pps_ast_per40,
           {R('ps.s_tpa', 'ps.s_tpa + ps.s_two_pa')} AS pps_three_rate, {R('ps.s_ftm', 'ps.s_fta')} AS pps_ft_pct,
           {R('ps.s_used', 'ps.s_team_used_on')} AS pps_usage,
           (ps.team_id = x.canonical_team_id) AS pps_same_team,
           CASE WHEN x.season = {spec.SOURCE_SEASONS[0]} THEN 'SOURCE_NOT_AVAILABLE'
                WHEN coalesce(ps.n_app, 0) = 0 THEN 'NO_PRIOR_HISTORY' ELSE 'OK' END AS pps_status,
           -- player_profile (cold-start inputs; known before the season)
           ro.position AS prof_position, ro.height_in AS prof_height_in, ro.weight_lb AS prof_weight_lb,
           rc.stars AS prof_recruit_stars, rc.rating AS prof_recruit_rating, rc.ranking AS prof_recruit_ranking,
           rc.recruit_year AS prof_recruit_year,
           (tr.canonical_player_id IS NOT NULL) AS prof_portal_transfer_in, tr.stars AS prof_portal_stars,
           tr.origin_team_id AS prof_portal_origin_team_id,
           EXISTS (SELECT 1 FROM pseason h WHERE h.canonical_player_id = x.canonical_player_id AND h.season < x.season
                   AND h.n_app > 0) AS prof_has_prior_season_in_window,
           CASE WHEN ro.canonical_player_id IS NULL AND rc.canonical_player_id IS NULL AND tr.canonical_player_id IS NULL
                THEN 'SOURCE_NOT_AVAILABLE' ELSE 'OK' END AS prof_status,
           tpm.min_for_team AS ret_prev_season_min_for_team, tpmt.team_min AS ret_prev_season_team_min
    FROM ex.player_game_examples x
    JOIN f_pbox b USING (player_game_example_id)
    JOIN f_part pa USING (player_game_example_id)
    LEFT JOIN pseason ps ON ps.canonical_player_id = x.canonical_player_id AND ps.season = x.season - 1
    LEFT JOIN ros ro ON ro.canonical_player_id = x.canonical_player_id AND ro.canonical_team_id = x.canonical_team_id AND ro.season = x.season
    LEFT JOIN rec rc ON rc.canonical_player_id = x.canonical_player_id AND rc.recruit_year < x.season
    LEFT JOIN trf tr ON tr.canonical_player_id = x.canonical_player_id AND tr.season = x.season
    LEFT JOIN player_team_prev_minutes tpm ON tpm.canonical_player_id = x.canonical_player_id
         AND tpm.canonical_team_id = x.canonical_team_id AND tpm.season = x.season - 1
    LEFT JOIN team_prev_minutes tpmt ON tpmt.canonical_team_id = x.canonical_team_id AND tpmt.season = x.season - 1;
    """)
    # ---------------------------------------------------------------- team rotation / role (from pregame rows only)
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.player_features AS
    SELECT p.*,
           -- player_role: rank and team rotation context, all derived from the same game's PREGAME rows
           rank() OVER (PARTITION BY p.game_example_id, p.canonical_team_id ORDER BY coalesce(p.pr_l5_min_per_app * p.pr_l5_appearances, 0) DESC,
                        p.canonical_player_id) AS role_l5_minutes_rank,
           count(*) FILTER (WHERE p.pr_l5_appearances >= 3 AND p.pr_l5_min_per_app >= 10)
               OVER (PARTITION BY p.game_example_id, p.canonical_team_id) AS role_team_rotation_depth_l5,
           count(*) FILTER (WHERE p.pp_last_game_participation = 'PLAYED')
               OVER (PARTITION BY p.game_example_id, p.canonical_team_id) AS role_team_active_last_game,
           count(*) OVER (PARTITION BY p.game_example_id, p.canonical_team_id) AS role_team_universe_size,
           sum(coalesce(p.pr_l5_min_per_app * p.pr_l5_appearances, 0))
               OVER (PARTITION BY p.game_example_id, p.canonical_team_id) - coalesce(p.pr_l5_min_per_app * p.pr_l5_appearances, 0)
               AS role_teammates_l5_minutes,
           'OK' AS role_status,   -- always derivable from the same game's pregame rows
           -- team_returning_minutes: previous-season minutes of this pregame universe for this team
           CASE WHEN max(p.ret_prev_season_team_min) OVER (PARTITION BY p.game_example_id, p.canonical_team_id) > 0
                THEN sum(coalesce(p.ret_prev_season_min_for_team, 0)) OVER (PARTITION BY p.game_example_id, p.canonical_team_id)
                     / max(p.ret_prev_season_team_min) OVER (PARTITION BY p.game_example_id, p.canonical_team_id) END
               AS ret_team_returning_min_share,
           CASE WHEN p.season = {spec.SOURCE_SEASONS[0]} THEN 'SOURCE_NOT_AVAILABLE'
                WHEN max(p.ret_prev_season_team_min) OVER (PARTITION BY p.game_example_id, p.canonical_team_id) IS NULL
                THEN 'NO_PRIOR_HISTORY' ELSE 'OK' END AS ret_status,
           -- cold start classification (inputs only; no prior is fitted)
           CASE WHEN p.pstd_appearances >= {MIN} THEN 'WARM'
                WHEN p.prof_portal_transfer_in OR (p.pps_same_team = FALSE) THEN 'COLD_TRANSFER_IN'
                WHEN NOT p.prof_has_prior_season_in_window AND p.pstd_appearances = 0 THEN 'COLD_NO_HISTORY'
                ELSE 'COLD_EARLY_SEASON' END AS cold_start_class
    FROM pf1 p
    ORDER BY p.game_example_id, p.canonical_team_id, p.canonical_player_id;
    """)


FAMILIES = {
    "player_season_to_date": ("Season-to-date box sufficient statistics and derived per-appearance / per-40 / shooting rates.",
                              "feat.player_box_state", "pstd_status"),
    "player_recent": ("Last 3/5/10 box rows and exponentially weighted (half-life 5) minutes, scoring, usage, start rate.",
                      "feat.player_box_state", "pr_status"),
    "player_participation": ("Team-game participation history under the shared rules (PLAYED/DNP_CONFIRMED/UNKNOWN).",
                             "feat.player_participation_state", "pp_status"),
    "player_prior_season": ("Previous season (inside the window) volume and rates.", "feat.player_box_state", "pps_status"),
    "player_profile": ("Cold-start inputs known pregame: roster attributes, recruiting class, portal transfer, history flags.",
                       "src.rosters, src.recruits, src.transfers", "prof_status"),
    "player_role": ("Minutes-role and team rotation context from the same game's pregame rows.", "feat.player_features", "role_status"),
    "team_returning_minutes": ("Previous-season minutes for this team of the players in this pregame universe (returning share).",
                               "src.player_games (previous season)", "ret_status"),
}
