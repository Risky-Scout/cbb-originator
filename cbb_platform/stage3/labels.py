"""Stage 3 section 7: the LABEL store -- a separate DuckDB file (spec.LABELS_DB) that feature code never attaches.

lab.player_game_labels   one row per player-game example (the pregame universe):
    participation_status / evidence (shared rules applied to the TARGET game), box-row presence, starter,
    MIN PTS REB AST STL BLK TOV PF ORB DRB FTM FTA 2PM 2PA 3PM 3PA, derived FT% 2P% 3P% TS%,
    identity checks (PTS = 2*2PM + 3*3PM + FTM, REB = ORB + DRB), availability flags.
lab.team_game_labels     one row per team-game example: points, opponent points, possessions, box availability.
lab.target_box_rows_outside_universe   resolved target-game box rows of players absent from the pregame universe
lab.target_box_rows_unresolved         target-game box rows with no canonical player (identity unresolved)

Null rules: a counting label is NULL when there is no box row (never imputed to 0). A percentage is NULL when its
denominator is 0: FT% = FTM/FTA, 2P% = 2PM/2PA, 3P% = 3PM/3PA, TS% = PTS / (2 * (FGA + 0.44 * FTA)).
Labels are derived from the authoritative primitives; PTS and REB are not independent targets (identities flagged).
"""
from __future__ import annotations

from cbb_platform.stage3 import participation as PART, spec


def build(con) -> None:
    """`con` = the labels DB with `src` bound (sources.bind) and the features DB attached read-only as `fx`."""
    con.execute("CREATE SCHEMA IF NOT EXISTS lab")
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE cand AS
    SELECT canonical_game_id, canonical_team_id, canonical_player_id, season FROM fx.ex.player_game_examples;
    CREATE OR REPLACE TEMP TABLE part AS {PART.classify_sql('cand')};
    CREATE OR REPLACE TABLE lab.player_game_labels AS
    SELECT x.player_game_example_id, x.game_example_id, x.canonical_game_id, x.canonical_player_id, x.canonical_team_id,
           x.season, pa.participation_status, pa.participation_evidence, pa.box_row AS box_row_present,
           b.starter AS starter, b.minutes AS min, b.points AS pts, b.reb, b.ast, b.stl, b.blk, b.tov, b.pf, b.orb, b.drb,
           b.ftm, b.fta, b.two_pm, b.two_pa, b.three_pm, b.three_pa,
           CASE WHEN b.fta > 0 THEN b.ftm / b.fta END AS ft_pct,
           CASE WHEN b.two_pa > 0 THEN b.two_pm / b.two_pa END AS two_pct,
           CASE WHEN b.three_pa > 0 THEN b.three_pm / b.three_pa END AS three_pct,
           CASE WHEN (b.fga + 0.44 * b.fta) > 0 THEN b.points / (2 * (b.fga + 0.44 * b.fta)) END AS ts_pct,
           b.points = 2 * b.two_pm + 3 * b.three_pm + b.ftm AS pts_identity_ok,
           b.reb = b.orb + b.drb AS reb_identity_ok,
           b.accounting_issues AS source_accounting_issues,
           pa.box_row AS minutes_label_available,
           pa.box_row AND b.accounting_issues IS NULL AS counting_labels_available,
           pa.box_row AND b.starter IS NOT NULL AS starter_label_available,
           '{spec.STAGE3_VERSION}' AS transformation_version
    FROM fx.ex.player_game_examples x
    JOIN part pa ON pa.canonical_game_id = x.canonical_game_id AND pa.canonical_team_id = x.canonical_team_id
                 AND pa.canonical_player_id = x.canonical_player_id
    LEFT JOIN src.player_games b ON b.canonical_game_id = x.canonical_game_id AND b.canonical_team_id = x.canonical_team_id
                                AND b.canonical_player_id = x.canonical_player_id
    ORDER BY 1;
    CREATE OR REPLACE TABLE lab.team_game_labels AS
    SELECT x.game_example_id, x.canonical_game_id, x.canonical_team_id, x.season,
           CASE WHEN x.is_home THEN g.home_points ELSE g.away_points END AS team_points,
           CASE WHEN x.is_home THEN g.away_points ELSE g.home_points END AS opp_points,
           t.possessions_source AS possessions_source, t.game_minutes, t.canonical_team_id IS NOT NULL AS team_box_present,
           '{spec.STAGE3_VERSION}' AS transformation_version
    FROM fx.ex.team_game_examples x JOIN src.played_games g USING (canonical_game_id)
    LEFT JOIN src.team_games t ON t.canonical_game_id = x.canonical_game_id AND t.canonical_team_id = x.canonical_team_id
    ORDER BY 1, 3;
    CREATE OR REPLACE TABLE lab.target_box_rows_outside_universe AS
    SELECT b.canonical_game_id, b.canonical_team_id, b.canonical_player_id, b.season, b.minutes
    FROM src.player_games b JOIN fx.ex.game_examples ge USING (canonical_game_id)
    WHERE b.canonical_player_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM fx.ex.player_game_examples x
          WHERE x.canonical_game_id = b.canonical_game_id AND x.canonical_team_id = b.canonical_team_id
            AND x.canonical_player_id = b.canonical_player_id)
    ORDER BY 1, 2, 3;
    CREATE OR REPLACE TABLE lab.target_box_rows_unresolved AS
    SELECT b.canonical_game_id, b.canonical_team_id, b.season, b.identity_status, b.minutes
    FROM src.player_games b JOIN fx.ex.game_examples ge USING (canonical_game_id)
    WHERE b.canonical_player_id IS NULL ORDER BY 1, 2;
    """)
