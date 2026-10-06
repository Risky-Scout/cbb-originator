"""Stage 3 section 8: point-in-time TEAM / game-environment state.

State streams (persisted, sufficient statistics; partition = team-season, order = available_at):
    feat.team_results_state   every PLAYED game (box or not): W/L, points, venue role, recent tips
    feat.team_box_state       every played game with a team box: possessions, efficiency inputs, shot mix, rates,
                              home/away splits, PIT opponent strength
Feature table:
    feat.team_features        one row per (target game, team) = ASOF(state, prediction_cutoff_utc)

Possessions: 0.5 * (own estimate + opponent estimate), estimate = FGA - ORB + TOV + 0.475 * FTA (both boxes
required, else the team's own estimate). Opponent strength: the opponent's own PIT net efficiency at THAT earlier
game's cutoff (never later information). No sportsbook data, no season summaries.
"""
from __future__ import annotations

from cbb_platform.stage3 import pitlib as P, spec

PT = "canonical_team_id, season"


def build(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS feat")
    # ---------------------------------------------------------------- per team-game source rows
    con.execute("""
    CREATE OR REPLACE TEMP TABLE tg AS
    SELECT t.canonical_team_id, t.canonical_opponent_id AS opponent_id, t.season, t.canonical_game_id AS source_game_id,
           g.scheduled_tip_utc AS source_tip, t.historical_knowable_at AS available_at, t.is_home, g.neutral_site,
           t.game_minutes AS mins, t.points AS pts, t.fgm, t.fga, t.three_pm AS tpm, t.three_pa AS tpa, t.ftm, t.fta,
           t.orb, t.drb, t.ast, t.stl, t.blk, t.tov_total AS tov, t.pf,
           o.canonical_team_id IS NOT NULL AS complete,
           o.points AS opp_pts, o.fgm AS opp_fgm, o.fga AS opp_fga, o.three_pm AS opp_tpm, o.three_pa AS opp_tpa,
           o.fta AS opp_fta, o.orb AS opp_orb, o.drb AS opp_drb, o.tov_total AS opp_tov, o.two_pa AS opp_two_pa,
           t.fga - t.orb + t.tov_total + 0.475 * t.fta AS own_poss_est,
           o.fga - o.orb + o.tov_total + 0.475 * o.fta AS opp_poss_est
    FROM src.team_games t JOIN src.played_games g USING (canonical_game_id)
    LEFT JOIN src.team_games o ON o.canonical_game_id = t.canonical_game_id AND o.canonical_team_id = t.canonical_opponent_id
    -- efficiency state uses COMPLETE games only (both team boxes): every rate shares one denominator basis.
    -- Result-only and one-box games still count in the results stream.
    WHERE t.points IS NOT NULL AND t.fga IS NOT NULL AND o.canonical_team_id IS NOT NULL AND o.fga IS NOT NULL;
    ALTER TABLE tg ADD COLUMN poss DOUBLE;
    UPDATE tg SET poss = CASE WHEN complete THEN 0.5 * (own_poss_est + opp_poss_est) ELSE own_poss_est END;
    """)
    # ---------------------------------------------------------------- box state (first pass, no SOS yet)
    sums = ["poss", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf", "mins"]
    csums = ["opp_pts", "opp_fgm", "opp_fga", "opp_tpm", "opp_tpa", "opp_fta", "opp_orb", "opp_drb", "opp_tov", "opp_two_pa"]
    cols = [f"{P.std(c, PT)} AS s_{c}" for c in sums]
    cols += [f"{P.std(f'CASE WHEN complete THEN {c} ELSE 0 END', PT)} AS s_{c}" for c in csums]
    cols += [f"{P.std('CASE WHEN complete THEN poss ELSE 0 END', PT)} AS s_cposs",
             f"{P.std('CASE WHEN complete THEN pts ELSE 0 END', PT)} AS s_cpts",
             f"{P.std('complete::INT', PT)} AS n_complete", f"{P.std('1', PT)} AS n_box"]
    for role, cond in (("home", "is_home AND NOT coalesce(neutral_site, FALSE)"), ("away", "NOT is_home AND NOT coalesce(neutral_site, FALSE)"),
                       ("neutral", "coalesce(neutral_site, FALSE)")):
        for c in ("cposs", "cpts", "opp_pts"):
            expr = {"cposs": "poss", "cpts": "pts", "opp_pts": "opp_pts"}[c]
            cols.append(f"{P.std(f'CASE WHEN complete AND {cond} THEN {expr} ELSE 0 END', PT)} AS s_{role}_{c}")
        cols.append(f"{P.std(f'CASE WHEN complete AND {cond} THEN 1 ELSE 0 END', PT)} AS n_{role}")
    for n in (5, 10):
        for c in ("cposs", "cpts", "opp_pts", "mins", "poss"):
            expr = {"cposs": "CASE WHEN complete THEN poss ELSE 0 END", "cpts": "CASE WHEN complete THEN pts ELSE 0 END",
                    "opp_pts": "CASE WHEN complete THEN opp_pts ELSE 0 END", "mins": "mins", "poss": "poss"}[c]
            cols.append(f"{P.roll(expr, PT, n)} AS l{n}_{c}")
        cols.append(f"{P.roll('1', PT, n)} AS l{n}_n")
    for c, expr in (("cposs", "CASE WHEN complete THEN poss ELSE 0 END"), ("cpts", "CASE WHEN complete THEN pts ELSE 0 END"),
                    ("opp_pts", "CASE WHEN complete THEN opp_pts ELSE 0 END"), ("mins", "mins"), ("poss", "poss")):
        cols.append(f"{P.ewm(expr, PT)} AS e_{c}")
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE tg_k AS SELECT *, row_number() OVER {P.w(PT)} AS k FROM tg;
    CREATE OR REPLACE TABLE feat.team_box_state AS
    SELECT canonical_team_id, season, source_game_id, source_tip, available_at, k, {', '.join(cols)}
    FROM tg_k ORDER BY canonical_team_id, season, available_at;
    """)
    # ---------------------------------------------------------------- PIT opponent strength (SOS)
    # every source team-game gets the OPPONENT's net efficiency as it stood at that game's own cutoff
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE tg_targets AS
    SELECT canonical_team_id AS opp_key, opponent_id AS canonical_team_id, season, source_game_id AS game_id,
           source_tip - INTERVAL {spec.CUTOFF_OFFSET_HOURS} HOUR AS prediction_cutoff_utc
    FROM tg;
    """)
    P.attach(con, "tg_targets", "feat.team_box_state", ("canonical_team_id", "season"), "opp_pre",
             select=f"{P.ratio('s.s_cpts - s.s_opp_pts', 's.s_cposs', 100.0)} AS opp_pre_net, s.n_complete AS opp_pre_n")
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE sos_src AS
    SELECT t.canonical_team_id, t.season, t.source_game_id, t.source_tip, t.available_at,
           o.opp_pre_net, (o.opp_pre_n >= {spec.MIN_OBS['team']})::INT AS has_rating
    FROM tg t LEFT JOIN opp_pre o ON o.opp_key = t.canonical_team_id AND o.game_id = t.source_game_id;
    CREATE OR REPLACE TABLE feat.team_sos_state AS
    SELECT canonical_team_id, season, source_game_id, source_tip, available_at,
           {P.std('CASE WHEN has_rating = 1 THEN opp_pre_net ELSE 0 END', PT)} AS s_opp_pre_net,
           {P.std('has_rating', PT)} AS n_rated_opp
    FROM sos_src ORDER BY 1, 2, 5;
    """)
    # ---------------------------------------------------------------- results state (every played game)
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE rs AS
    SELECT home_team_id AS canonical_team_id, season, canonical_game_id AS source_game_id, scheduled_tip_utc AS source_tip,
           historical_knowable_at AS available_at, home_points AS pf, away_points AS pa, TRUE AS is_home, neutral_site
    FROM src.played_games WHERE home_points IS NOT NULL
    UNION ALL
    SELECT away_team_id, season, canonical_game_id, scheduled_tip_utc, historical_knowable_at, away_points, home_points,
           FALSE, neutral_site FROM src.played_games WHERE home_points IS NOT NULL;
    CREATE OR REPLACE TABLE feat.team_results_state AS
    SELECT canonical_team_id, season, source_game_id, source_tip, available_at,
           {P.std('1', PT)} AS n_games, {P.std('(pf > pa)::INT', PT)} AS n_wins, {P.std('(pf < pa)::INT', PT)} AS n_losses,
           {P.std('pf', PT)} AS s_pf, {P.std('pa', PT)} AS s_pa,
           {P.std('(is_home AND NOT coalesce(neutral_site, FALSE))::INT', PT)} AS n_home,
           {P.std('(NOT is_home AND NOT coalesce(neutral_site, FALSE))::INT', PT)} AS n_away,
           {P.std('coalesce(neutral_site, FALSE)::INT', PT)} AS n_neutral,
           list(source_tip) OVER (PARTITION BY {PT} ORDER BY available_at, source_tip, source_game_id
                                  ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) AS recent_tips
    FROM rs ORDER BY 1, 2, 5;
    """)
    # ---------------------------------------------------------------- features
    P.attach(con, "ex.team_game_examples", "feat.team_results_state", ("canonical_team_id", "season"), "f_res",
             select="s.source_game_id AS res_state_source_game_id, s.available_at AS res_state_available_at, s.source_tip AS last_tip, "
                    "s.n_games, s.n_wins, s.n_losses, s.s_pf, s.s_pa, s.n_home, s.n_away, s.n_neutral, s.recent_tips")
    P.attach(con, "ex.team_game_examples", "feat.team_box_state", ("canonical_team_id", "season"), "f_box",
             select="s.source_game_id AS box_state_source_game_id, s.available_at AS box_state_available_at, s.* EXCLUDE "
                    "(canonical_team_id, season, source_game_id, source_tip, available_at, k)")
    P.attach(con, "ex.team_game_examples", "feat.team_sos_state", ("canonical_team_id", "season"), "f_sos",
             select="s.source_game_id AS sos_state_source_game_id, s.available_at AS sos_state_available_at, s.s_opp_pre_net, s.n_rated_opp")
    # prior season: the LAST state of season-1 (complete before the current season starts), inside the window only
    con.execute("""
    CREATE OR REPLACE TEMP TABLE prior AS
    SELECT b.canonical_team_id, b.season + 1 AS season, b.s_cpts, b.s_opp_pts, b.s_cposs, b.s_mins, b.n_complete,
           r.n_games, r.n_wins
    FROM (SELECT * FROM feat.team_box_state QUALIFY row_number() OVER (PARTITION BY canonical_team_id, season ORDER BY available_at DESC) = 1) b
    JOIN (SELECT * FROM feat.team_results_state QUALIFY row_number() OVER (PARTITION BY canonical_team_id, season ORDER BY available_at DESC) = 1) r
      USING (canonical_team_id, season);
    """)
    R = P.ratio
    MIN = spec.MIN_OBS["team"]
    status = lambda n, have_source=True: (f"CASE WHEN NOT ({have_source}) THEN 'SOURCE_NOT_AVAILABLE' WHEN coalesce({n}, 0) = 0 "
                                          f"THEN 'NO_PRIOR_HISTORY' WHEN {n} < {MIN} THEN 'INSUFFICIENT_SAMPLE' ELSE 'OK' END")
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.team_features AS
    SELECT x.game_example_id, x.canonical_game_id, x.canonical_team_id, x.opponent_team_id, x.season, x.is_home,
           x.neutral_site, x.prediction_cutoff_utc, '{spec.STAGE3_VERSION}' AS transformation_version,
           -- provenance
           r.res_state_source_game_id, r.res_state_available_at, b.box_state_source_game_id, b.box_state_available_at,
           so.sos_state_source_game_id,
           -- team_results
           coalesce(r.n_games, 0) AS tr_games, coalesce(r.n_wins, 0) AS tr_wins, coalesce(r.n_losses, 0) AS tr_losses,
           {R('r.n_wins', 'r.n_games')} AS tr_win_pct, {R('r.s_pf', 'r.n_games')} AS tr_pts_for_pg,
           {R('r.s_pa', 'r.n_games')} AS tr_pts_against_pg, {R('r.s_pf - r.s_pa', 'r.n_games')} AS tr_margin_pg,
           coalesce(r.n_home, 0) AS tr_home_games, coalesce(r.n_away, 0) AS tr_away_games, coalesce(r.n_neutral, 0) AS tr_neutral_games,
           {status('r.n_games')} AS tr_status,
           -- team_rest
           CASE WHEN r.last_tip IS NOT NULL THEN date_diff('minute', r.last_tip, x.scheduled_tip_utc) / 1440.0 END AS rest_days,
           coalesce(len(list_filter(r.recent_tips, z -> z > x.scheduled_tip_utc - INTERVAL 7 DAY)), 0) AS rest_games_last_7d,
           coalesce(len(list_filter(r.recent_tips, z -> z > x.scheduled_tip_utc - INTERVAL 14 DAY)), 0) AS rest_games_last_14d,
           CASE WHEN r.last_tip IS NULL THEN 'NO_PRIOR_HISTORY' ELSE 'OK' END AS rest_status,
           -- team_efficiency (season to date; possessions-based)
           coalesce(b.n_box, 0) AS te_games_boxed, coalesce(b.n_complete, 0) AS te_games_complete,
           b.s_cposs AS te_sum_poss, b.s_cpts AS te_sum_pts, b.s_opp_pts AS te_sum_opp_pts,
           {R('b.s_poss', 'b.s_mins', 40.0)} AS te_pace_per40,
           {R('b.s_cpts', 'b.s_cposs', 100.0)} AS te_off_eff, {R('b.s_opp_pts', 'b.s_cposs', 100.0)} AS te_def_eff,
           {R('b.s_cpts - b.s_opp_pts', 'b.s_cposs', 100.0)} AS te_net_eff,
           {R('b.s_tov', 'b.s_poss')} AS te_tov_rate, {R('b.s_opp_tov', 'b.s_cposs')} AS te_opp_tov_rate,
           {R('b.s_orb', 'b.s_orb + b.s_opp_drb')} AS te_orb_rate, {R('b.s_drb', 'b.s_drb + b.s_opp_orb')} AS te_drb_rate,
           {R('b.s_tpa', 'b.s_fga')} AS te_three_rate, {R('b.s_fta', 'b.s_fga')} AS te_ft_rate,
           {R('b.s_fgm + 0.5 * b.s_tpm', 'b.s_fga')} AS te_efg, {R('b.s_opp_fgm + 0.5 * b.s_opp_tpm', 'b.s_opp_fga')} AS te_opp_efg,
           {R('b.s_opp_tpa', 'b.s_opp_fga')} AS te_opp_three_rate, {R('b.s_opp_fta', 'b.s_opp_fga')} AS te_opp_ft_rate,
           {R('b.s_ftm', 'b.s_fta')} AS te_ft_pct, {R('b.s_ast', 'b.s_fgm')} AS te_ast_rate,
           {R('b.s_stl', 'b.s_cposs')} AS te_stl_rate, {R('b.s_blk', 'b.s_opp_two_pa')} AS te_blk_rate,
           {status('b.n_complete')} AS te_status,
           -- team_form (recent windows, possession-based)
           b.l5_n AS tf_l5_games, {R('b.l5_cpts', 'b.l5_cposs', 100.0)} AS tf_l5_off_eff, {R('b.l5_opp_pts', 'b.l5_cposs', 100.0)} AS tf_l5_def_eff,
           {R('b.l5_poss', 'b.l5_mins', 40.0)} AS tf_l5_pace_per40,
           b.l10_n AS tf_l10_games, {R('b.l10_cpts', 'b.l10_cposs', 100.0)} AS tf_l10_off_eff, {R('b.l10_opp_pts', 'b.l10_cposs', 100.0)} AS tf_l10_def_eff,
           {R('b.l10_poss', 'b.l10_mins', 40.0)} AS tf_l10_pace_per40,
           {R('b.e_cpts', 'b.e_cposs', 100.0)} AS tf_ewm_off_eff, {R('b.e_opp_pts', 'b.e_cposs', 100.0)} AS tf_ewm_def_eff,
           {R('b.e_poss', 'b.e_mins', 40.0)} AS tf_ewm_pace_per40,
           {status('b.n_complete')} AS tf_status,
           -- team_splits (venue role, season to date)
           coalesce(b.n_home, 0) AS ts_home_games, {R('b.s_home_cpts - b.s_home_opp_pts', 'b.s_home_cposs', 100.0)} AS ts_home_net_eff,
           coalesce(b.n_away, 0) AS ts_away_games, {R('b.s_away_cpts - b.s_away_opp_pts', 'b.s_away_cposs', 100.0)} AS ts_away_net_eff,
           coalesce(b.n_neutral, 0) AS ts_neutral_games, {R('b.s_neutral_cpts - b.s_neutral_opp_pts', 'b.s_neutral_cposs', 100.0)} AS ts_neutral_net_eff,
           {status('b.n_complete')} AS ts_status,
           -- team_sos (PIT opponent strength)
           coalesce(so.n_rated_opp, 0) AS sos_rated_opponents, {R('so.s_opp_pre_net', 'so.n_rated_opp')} AS sos_opp_pregame_net_eff,
           {status('so.n_rated_opp')} AS sos_status,
           -- team_prior_season (complete previous season inside the window)
           pr.n_games AS tp_prev_games, {R('pr.n_wins', 'pr.n_games')} AS tp_prev_win_pct,
           {R('pr.s_cpts - pr.s_opp_pts', 'pr.s_cposs', 100.0)} AS tp_prev_net_eff, {R('pr.s_cpts', 'pr.s_cposs', 100.0)} AS tp_prev_off_eff,
           {R('pr.s_opp_pts', 'pr.s_cposs', 100.0)} AS tp_prev_def_eff,
           CASE WHEN x.season = {spec.SOURCE_SEASONS[0]} THEN 'SOURCE_NOT_AVAILABLE' WHEN pr.n_games IS NULL THEN 'NO_PRIOR_HISTORY'
                ELSE 'OK' END AS tp_status
    FROM ex.team_game_examples x
    JOIN f_res r USING (game_example_id, canonical_team_id)
    JOIN f_box b USING (game_example_id, canonical_team_id)
    JOIN f_sos so USING (game_example_id, canonical_team_id)
    LEFT JOIN prior pr ON pr.canonical_team_id = x.canonical_team_id AND pr.season = x.season
    ORDER BY x.game_example_id, x.canonical_team_id;
    """)


FAMILIES = {
    "team_results": ("Season-to-date record from every prior PLAYED game (box or not).", "feat.team_results_state",
                     ["tr_games", "tr_wins", "tr_losses", "tr_win_pct", "tr_pts_for_pg", "tr_pts_against_pg", "tr_margin_pg",
                      "tr_home_games", "tr_away_games", "tr_neutral_games"], "tr_status"),
    "team_rest": ("Days since the previous played game (target tip - previous tip) and schedule density.", "feat.team_results_state",
                  ["rest_days", "rest_games_last_7d", "rest_games_last_14d"], "rest_status"),
    "team_efficiency": ("Season-to-date possession-based efficiency, pace and rate state (games with team box).", "feat.team_box_state",
                        ["te_games_boxed", "te_games_complete", "te_sum_poss", "te_sum_pts", "te_sum_opp_pts", "te_pace_per40",
                         "te_off_eff", "te_def_eff", "te_net_eff", "te_tov_rate", "te_opp_tov_rate", "te_orb_rate", "te_drb_rate",
                         "te_three_rate", "te_ft_rate", "te_efg", "te_opp_efg", "te_opp_three_rate", "te_opp_ft_rate", "te_ft_pct",
                         "te_ast_rate", "te_stl_rate", "te_blk_rate"], "te_status"),
    "team_form": ("Recent form: last-5, last-10 and exponentially weighted (half-life 5 games) efficiency and pace.", "feat.team_box_state",
                  ["tf_l5_games", "tf_l5_off_eff", "tf_l5_def_eff", "tf_l5_pace_per40", "tf_l10_games", "tf_l10_off_eff",
                   "tf_l10_def_eff", "tf_l10_pace_per40", "tf_ewm_off_eff", "tf_ewm_def_eff", "tf_ewm_pace_per40"], "tf_status"),
    "team_splits": ("Season-to-date net efficiency by venue role (home / away / neutral).", "feat.team_box_state",
                    ["ts_home_games", "ts_home_net_eff", "ts_away_games", "ts_away_net_eff", "ts_neutral_games", "ts_neutral_net_eff"], "ts_status"),
    "team_sos": ("Opponent strength: mean of each prior opponent's net efficiency AS OF that earlier game's cutoff.", "feat.team_sos_state",
                 ["sos_rated_opponents", "sos_opp_pregame_net_eff"], "sos_status"),
    "team_prior_season": ("Previous season's final record and efficiency (inside the 2013-14+ window only).", "feat.team_box_state",
                          ["tp_prev_games", "tp_prev_win_pct", "tp_prev_net_eff", "tp_prev_off_eff", "tp_prev_def_eff"], "tp_status"),
}
