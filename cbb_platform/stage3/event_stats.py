"""Stage 3 section 12: game-event sufficient statistics from trusted PBP / shotInfo (2013-14 .. 2024-25).

Per team-game (feat.team_event_game) and per player-game (feat.player_shot_game) counts, then PIT season-to-date and
last-10 states, attached to target examples. No event probability is fitted; the target game's events are never used.

Shot range is CBBD shotInfo.range (rim / jumper / three_pointer / free_throw). Location buckets use x/y ONLY for
seasons in the shot-location window (2021-22+) and only shots with location_valid. Court frame (validated on 2024-25):
baskets at x = 52.5 / 887.5, y = 250 (tenths of feet); distance d in feet; dx = distance from the basket along x.
    LOC_RIM      2PA, d < 4          LOC_PAINT  2PA, 4 <= d < 10        LOC_MID  2PA, d >= 10
    LOC_CORNER3  3PA, dx <= 14 ft    LOC_ABOVE_BREAK3  3PA, dx > 14 ft
Event plays: turnovers (play type contains 'Turnover'), steals ('Steal'), blocks ('Block Shot'), fouls (contains
'Foul'), offensive / defensive rebounds. Each event is credited to the play's team.
"""
from __future__ import annotations

from cbb_platform.stage3 import pitlib as P, spec

LOC_LO = spec.LOCATION_SEASONS[0]
DIST = "sqrt(power(least(abs(location_x - 52.5), abs(location_x - 887.5)), 2) + power(location_y - 250, 2)) / 10.0"
DX = "least(abs(location_x - 52.5), abs(location_x - 887.5)) / 10.0"
SHOT_COLS = {
    "rim_a": "shot_range = 'rim'", "rim_m": "shot_range = 'rim' AND made", "rim_ast": "shot_range = 'rim' AND made AND assisted",
    "jmp_a": "shot_range = 'jumper'", "jmp_m": "shot_range = 'jumper' AND made", "jmp_ast": "shot_range = 'jumper' AND made AND assisted",
    "thr_a": "shot_range = 'three_pointer'", "thr_m": "shot_range = 'three_pointer' AND made",
    "thr_ast": "shot_range = 'three_pointer' AND made AND assisted",
    "ft_a": "shot_range = 'free_throw'", "ft_m": "shot_range = 'free_throw' AND made",
    "loc_n": f"season >= {LOC_LO} AND location_valid AND shot_range <> 'free_throw'",
    "loc_rim": f"season >= {LOC_LO} AND location_valid AND shot_range IN ('rim', 'jumper') AND {DIST} < 4",
    "loc_paint": f"season >= {LOC_LO} AND location_valid AND shot_range IN ('rim', 'jumper') AND {DIST} >= 4 AND {DIST} < 10",
    "loc_mid": f"season >= {LOC_LO} AND location_valid AND shot_range IN ('rim', 'jumper') AND {DIST} >= 10",
    "loc_c3": f"season >= {LOC_LO} AND location_valid AND shot_range = 'three_pointer' AND {DX} <= 14",
    "loc_ab3": f"season >= {LOC_LO} AND location_valid AND shot_range = 'three_pointer' AND {DX} > 14",
}
PLAY_COLS = {"tov": "play_type ILIKE '%Turnover%'", "stl": "play_type = 'Steal'", "blk": "play_type = 'Block Shot'",
             "pf": "play_type ILIKE '%Foul%'", "orb": "play_type = 'Offensive Rebound'", "drb": "play_type = 'Defensive Rebound'"}


def _counts(cols: dict) -> str:
    return ", ".join(f"count(*) FILTER (WHERE {c}) AS {k}" for k, c in cols.items())


def build(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS feat")
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.team_event_game AS
    WITH sh AS (SELECT canonical_game_id, canonical_team_id, season, {_counts(SHOT_COLS)} FROM src.shots
                WHERE canonical_team_id IS NOT NULL GROUP BY 1, 2, 3),
    pl AS (SELECT canonical_game_id, canonical_team_id, {_counts(PLAY_COLS)} FROM src.plays
           WHERE canonical_team_id IS NOT NULL GROUP BY 1, 2),
    tm AS (SELECT DISTINCT canonical_game_id, canonical_team_id, canonical_opponent_id FROM src.team_games)
    SELECT tm.canonical_game_id AS source_game_id, tm.canonical_team_id, g.season, g.scheduled_tip_utc AS source_tip,
           g.historical_knowable_at AS available_at,
           {', '.join(f'coalesce(s.{k}, 0) AS {k}' for k in SHOT_COLS)}, {', '.join(f'coalesce(p.{k}, 0) AS {k}' for k in PLAY_COLS)},
           {', '.join(f'coalesce(o.{k}, 0) AS opp_{k}' for k in ('rim_a', 'rim_m', 'jmp_a', 'jmp_m', 'thr_a', 'thr_m', 'ft_a'))},
           {', '.join(f'coalesce(op.{k}, 0) AS opp_{k}' for k in PLAY_COLS)}
    FROM tm JOIN src.played_games g USING (canonical_game_id)
    JOIN (SELECT DISTINCT canonical_game_id FROM src.plays) has_pbp USING (canonical_game_id)
    LEFT JOIN sh s ON s.canonical_game_id = tm.canonical_game_id AND s.canonical_team_id = tm.canonical_team_id
    LEFT JOIN pl p ON p.canonical_game_id = tm.canonical_game_id AND p.canonical_team_id = tm.canonical_team_id
    LEFT JOIN sh o ON o.canonical_game_id = tm.canonical_game_id AND o.canonical_team_id = tm.canonical_opponent_id
    LEFT JOIN pl op ON op.canonical_game_id = tm.canonical_game_id AND op.canonical_team_id = tm.canonical_opponent_id
    ORDER BY 2, 3, 5;
    """)
    keys = list(SHOT_COLS) + list(PLAY_COLS) + [f"opp_{k}" for k in ('rim_a', 'rim_m', 'jmp_a', 'jmp_m', 'thr_a', 'thr_m', 'ft_a')] \
        + [f"opp_{k}" for k in PLAY_COLS]
    PT = "canonical_team_id, season"
    cols = [f"{P.std('1', PT)} AS n_games"] + [f"{P.std(k, PT)} AS s_{k}" for k in keys]
    cols += [f"{P.roll('1', PT, 10)} AS l10_n"] + [f"{P.roll(k, PT, 10)} AS l10_{k}" for k in ("rim_a", "jmp_a", "thr_a", "rim_m", "jmp_m", "thr_m")]
    con.execute(f"""CREATE OR REPLACE TABLE feat.team_event_state AS
        SELECT canonical_team_id, season, source_game_id, source_tip, available_at, {', '.join(cols)}
        FROM feat.team_event_game ORDER BY 1, 2, 5""")
    # player shot profile
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.player_shot_game AS
    SELECT shooter_player_id AS canonical_player_id, season, canonical_game_id AS source_game_id,
           any_value(gtip) AS source_tip, any_value(gav) AS available_at, {_counts(SHOT_COLS)}
    FROM (SELECT s.*, g.scheduled_tip_utc AS gtip, g.historical_knowable_at AS gav
          FROM src.shots s JOIN src.played_games g USING (canonical_game_id) WHERE s.shooter_player_id IS NOT NULL)
    GROUP BY 1, 2, 3 ORDER BY 1, 2, 5;
    """)
    PPK = "canonical_player_id, season"
    pcols = [f"{P.std('1', PPK)} AS n_games"] + [f"{P.std(k, PPK)} AS s_{k}" for k in SHOT_COLS]
    con.execute(f"""CREATE OR REPLACE TABLE feat.player_shot_state AS
        SELECT canonical_player_id, season, source_game_id, source_tip, available_at, {', '.join(pcols)}
        FROM feat.player_shot_game ORDER BY 1, 2, 5""")
    # ---------------------------------------------------------------- attach
    P.attach(con, "ex.team_game_examples", "feat.team_event_state", ("canonical_team_id", "season"), "f_tev",
             select="s.source_game_id AS tev_state_source_game_id, s.available_at AS tev_state_available_at, "
                    "s.* EXCLUDE (canonical_team_id, season, source_game_id, source_tip, available_at)")
    P.attach(con, "ex.player_game_examples", "feat.player_shot_state", ("canonical_player_id", "season"), "f_pshot",
             select="s.source_game_id AS pshot_state_source_game_id, s.available_at AS pshot_state_available_at, "
                    "s.* EXCLUDE (canonical_player_id, season, source_game_id, source_tip, available_at)")
    R = P.ratio
    MIN = spec.MIN_OBS["event"]
    fga = "s_rim_a + s_jmp_a + s_thr_a"
    ofga = "s_opp_rim_a + s_opp_jmp_a + s_opp_thr_a"
    st = lambda n: (f"CASE WHEN coalesce({n}, 0) = 0 THEN 'NO_PRIOR_HISTORY' WHEN {n} < {MIN} THEN 'INSUFFICIENT_SAMPLE' ELSE 'OK' END")
    loc_st = lambda n: (f"CASE WHEN x.season < {LOC_LO} THEN 'SOURCE_NOT_AVAILABLE' WHEN coalesce({n}, 0) = 0 THEN 'NO_PRIOR_HISTORY' "
                        f"ELSE 'OK' END")
    loc = lambda p, pre: ", ".join(f"CASE WHEN x.season >= {LOC_LO} THEN {R(f'{p}s_loc_{b}', f'{p}s_loc_n')} END AS {pre}_{b}_share"
                                   for b in ("rim", "paint", "mid", "c3", "ab3"))
    con.execute(f"""
    CREATE OR REPLACE TABLE feat.team_event_features AS
    SELECT x.game_example_id, x.canonical_team_id, x.season, x.prediction_cutoff_utc, '{spec.STAGE3_VERSION}' AS transformation_version,
           e.tev_state_source_game_id, e.tev_state_available_at,
           coalesce(e.n_games, 0) AS ev_games_with_pbp,
           {R('e.s_rim_a', 'e.' + fga.replace(' + ', ' + e.'))} AS ev_rim_rate, {R('e.s_jmp_a', 'e.' + fga.replace(' + ', ' + e.'))} AS ev_jumper_rate,
           {R('e.s_thr_a', 'e.' + fga.replace(' + ', ' + e.'))} AS ev_three_rate,
           {R('e.s_rim_m', 'e.s_rim_a')} AS ev_rim_fg, {R('e.s_jmp_m', 'e.s_jmp_a')} AS ev_jumper_fg, {R('e.s_thr_m', 'e.s_thr_a')} AS ev_three_fg,
           {R('e.s_rim_ast', 'e.s_rim_m')} AS ev_rim_assisted_share, {R('e.s_jmp_ast', 'e.s_jmp_m')} AS ev_jumper_assisted_share,
           {R('e.s_thr_ast', 'e.s_thr_m')} AS ev_three_assisted_share,
           {R('e.s_opp_rim_a', 'e.' + ofga.replace(' + ', ' + e.'))} AS ev_opp_rim_rate,
           {R('e.s_opp_thr_a', 'e.' + ofga.replace(' + ', ' + e.'))} AS ev_opp_three_rate,
           {R('e.s_opp_rim_m', 'e.s_opp_rim_a')} AS ev_opp_rim_fg, {R('e.s_opp_thr_m', 'e.s_opp_thr_a')} AS ev_opp_three_fg,
           {R('e.s_tov', 'e.n_games')} AS ev_tov_pg, {R('e.s_opp_tov', 'e.n_games')} AS ev_forced_tov_pg,
           {R('e.s_stl', 'e.n_games')} AS ev_stl_pg, {R('e.s_blk', 'e.s_opp_rim_a + e.s_opp_jmp_a')} AS ev_blk_per_opp_2pa,
           {R('e.s_pf', 'e.n_games')} AS ev_fouls_pg, {R('e.s_opp_ft_a', 'e.' + ofga.replace(' + ', ' + e.'))} AS ev_opp_fta_per_fga,
           {R('e.s_orb', 'e.s_orb + e.s_opp_drb')} AS ev_orb_rate, {R('e.s_drb', 'e.s_drb + e.s_opp_orb')} AS ev_drb_rate,
           e.l10_n AS ev_l10_games, {R('e.l10_thr_a', 'e.l10_rim_a + e.l10_jmp_a + e.l10_thr_a')} AS ev_l10_three_rate,
           {R('e.l10_rim_a', 'e.l10_rim_a + e.l10_jmp_a + e.l10_thr_a')} AS ev_l10_rim_rate,
           {st('e.n_games')} AS ev_status,
           coalesce(e.s_loc_n, 0) AS loc_team_located_fga, {loc('e.', 'loc_team')}, {loc_st('e.s_loc_n')} AS loc_team_status
    FROM ex.team_game_examples x JOIN f_tev e USING (game_example_id, canonical_team_id)
    ORDER BY 1, 2;
    CREATE OR REPLACE TABLE feat.player_shot_features AS
    SELECT x.player_game_example_id, x.season, x.prediction_cutoff_utc, '{spec.STAGE3_VERSION}' AS transformation_version,
           e.pshot_state_source_game_id, e.pshot_state_available_at,
           coalesce(e.n_games, 0) AS ps_games_with_shots,
           e.s_rim_a AS ps_sum_rim_a, e.s_rim_m AS ps_sum_rim_m, e.s_jmp_a AS ps_sum_jmp_a, e.s_jmp_m AS ps_sum_jmp_m,
           e.s_thr_a AS ps_sum_thr_a, e.s_thr_m AS ps_sum_thr_m, e.s_ft_a AS ps_sum_ft_a, e.s_ft_m AS ps_sum_ft_m,
           {R('e.s_rim_a', 'e.s_rim_a + e.s_jmp_a + e.s_thr_a')} AS ps_rim_share, {R('e.s_jmp_a', 'e.s_rim_a + e.s_jmp_a + e.s_thr_a')} AS ps_jumper_share,
           {R('e.s_thr_a', 'e.s_rim_a + e.s_jmp_a + e.s_thr_a')} AS ps_three_share,
           {R('e.s_rim_m', 'e.s_rim_a')} AS ps_rim_fg, {R('e.s_jmp_m', 'e.s_jmp_a')} AS ps_jumper_fg, {R('e.s_thr_m', 'e.s_thr_a')} AS ps_three_fg,
           {R('e.s_rim_ast', 'e.s_rim_m')} AS ps_rim_assisted_share, {R('e.s_jmp_ast', 'e.s_jmp_m')} AS ps_jumper_assisted_share,
           {R('e.s_thr_ast', 'e.s_thr_m')} AS ps_three_assisted_share,
           {st('e.n_games')} AS ps_status,
           coalesce(e.s_loc_n, 0) AS loc_player_located_fga, {loc('e.', 'loc_player')}, {loc_st('e.s_loc_n')} AS loc_player_status
    FROM ex.player_game_examples x JOIN f_pshot e USING (player_game_example_id)
    ORDER BY 1;
    """)


FAMILIES = {
    "team_event": ("Season-to-date (and last-10) shot-mix, shot-making, assisted-share, turnover, steal, block, foul and "
                   "rebounding sufficient statistics from trusted PBP.", "feat.team_event_state", "ev_status"),
    "team_shot_location": ("Season-to-date team shot-location bucket shares (2021-22+ only).", "feat.team_event_state", "loc_team_status"),
    "player_shot_profile": ("Season-to-date player shot mix / making / assisted share by CBBD shot range.", "feat.player_shot_state", "ps_status"),
    "player_shot_location": ("Season-to-date player shot-location bucket shares (2021-22+ only).", "feat.player_shot_state", "loc_player_status"),
}
