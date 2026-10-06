# Label store (Stage 3)

Built by `cbb_platform/stage3/labels.py` into a SEPARATE DuckDB file (`modeling/store/labels.duckdb`) and exported as
season-partitioned Parquet under `modeling/labels/<table>/season=YYYY/`. Feature code never attaches this store
(enforced by tests/test_stage3.py).

## lab.player_game_labels  (grain: one pregame player-game example; key `player_game_example_id`)

| column | meaning / rule |
|---|---|
| participation_status | PLAYED / DNP_CONFIRMED / UNKNOWN_PARTICIPATION / NOT_ON_ROSTER (shared rules, `cbb_platform/stage3/participation.py`, applied to the TARGET game) |
| participation_evidence | BOX_ROW_POSITIVE, SUBSTITUTION_STINT, COMPLETE_SUBSTITUTION_FEED_NO_STINT, BOX_ROW_ZERO_MINUTES_NO_STATS_AMBIGUOUS, NO_BOX_ROW, NO_BOX_ROW_NOT_ON_TEAM_ROSTER |
| box_row_present | the player has a box row in the target game |
| starter | box starter flag (NULL without a box row) |
| min | whole minutes as supplied (NULL without a box row; never imputed) |
| pts reb ast stl blk tov pf orb drb ftm fta two_pm two_pa three_pm three_pa | authoritative box primitives (NULL without a box row) |
| ft_pct, two_pct, three_pct | makes / attempts; NULL when attempts = 0 |
| ts_pct | PTS / (2 x (FGA + 0.44 x FTA)); NULL when the denominator is 0 |
| pts_identity_ok | PTS = 2 x 2PM + 3 x 3PM + FTM (PTS is not an independent target) |
| reb_identity_ok | REB = ORB + DRB |
| source_accounting_issues | Stage 1 accounting flags of the source box row |
| minutes_label_available, counting_labels_available, starter_label_available | target-availability flags |

## lab.team_game_labels  (grain: team-game example)
team_points, opp_points, possessions_source, game_minutes, team_box_present.

## Audit tables
* `lab.target_box_rows_outside_universe`: resolved target-game box rows of players absent from the pregame universe.
* `lab.target_box_rows_unresolved`: target-game box rows without a canonical player (identity unresolved).
