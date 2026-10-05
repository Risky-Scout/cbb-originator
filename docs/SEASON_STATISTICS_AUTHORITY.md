# Season statistics authority

**Status:** FROZEN (architect rulings: Stage 1 closure + Stage 1 final unblock, 2026-10-05) · **Build:** `20261005T163639Z`
**Enforced by:** `cbb_platform/build_canonical.py::build_season_authority`, `tests/test_stage1_closure.py` (section C)

## 1. Rule

1. **Canonical game-level tables are the authoritative foundation** for historical season-to-date model features. These tables are `core.games` (results) and `fact.team_games` / `fact.player_games` (box scores).
2. **Season summary exports are secondary.** They serve as QA sources and as completed-season priors. The exports are `fact.team_season_stats`, `fact.team_season_shooting`, `fact.player_season_stats` and `fact.player_season_shooting`, together with their raw Starter Pack files.
3. **When a summary value conflicts with the value recomputed from game-level rows, the game-derived value wins for modeling.** The game-derived twin is `fact.team_season_game_derived` (flagship view `flagship.team_season_game_derived`).
4. **Raw season summaries are preserved unchanged** for audit. They are never corrected, overwritten or deleted. `test_raw_season_summaries_are_preserved_unchanged` proves canonical values equal the staging source values.
5. Every disagreement is recorded in `qa.season_summary_conflicts` with `authoritative_source = 'GAME_DERIVED'`. `test_every_summary_conflict_is_recorded_with_game_derived_authority` checks this.

PIT is unaffected. Both the raw summaries and the game-derived season totals are *completed-season* objects (POINT_IN_TIME_POLICY HR-3). In-season features are rebuilt as-of the cutoff from `fact.team_games` (`cbb_platform/pit.py`).

## 2. What is wrong with the raw summaries (measured root cause)

The team-season summary mixes two different game bases in one row:

| Summary field(s) | Basis actually used by the export | Evidence |
|---|---|---|
| `games`, `points` | **every game with a final score** | equals the per-team count and point sum over `core.games` in **7,265 / 7,265** team-seasons |
| `fgm`, `fga`, `two_pm`, `two_pa`, `three_pm`, `three_pa`, `ftm`, `fta`, `orb`, `drb`, `reb`, `ast`, `stl`, `blk`, `tov_total`, `pf` | **only games with a team box score** | **zero** conflicts with the sums over `fact.team_games` |

So whenever a team played a game with a result but no box score, its summary `points` include that game's points, while its shot primitives do not. That breaks `PTS = 2·2PM + 3·3PM + FTM`, which is why so many summary rows carry `PTS_IDENTITY`. The conflict is structural; it is not random corruption.

`fact.team_season_game_derived` keeps the two bases explicitly separate:
- `games_with_result`, `wins`, `losses`, `points_all_results`, `opp_points_all_results` come from `core.games`;
- `games_with_team_box` and all box columns come from `fact.team_games`.

The two bases are never combined in a single identity.

## 3. Affected seasons and fields (team-season summaries)

Conflicts are counted against the boxed basis, the only basis on which the summary's identities can be checked. Only `games` and `points` ever conflict: 1,588 and 1,579 rows across 1,588 team-seasons, 3,167 conflict rows in total.

| Season | Team-season summaries | `games` conflicts | `points` conflicts | rows flagged `PTS_IDENTITY` |
|---|---:|---:|---:|---:|
| 2005 | 326 | 321 | 321 | 321 |
| 2006 | 327 | 203 | 203 | 203 |
| 2007 | 326 | 223 | 223 | 224 |
| 2008 | 328 | 42 | 42 | 43 |
| 2009 | 331 | 23 | 23 | 28 |
| 2010 | 335 | 15 | 15 | 16 |
| 2011 | 346 | 9 | 9 | 11 |
| 2012 | 345 | 327 | 321 | 321 |
| 2013 | 347 | 335 | 333 | 333 |
| 2014 | 351 | 18 | 18 | 23 |
| 2015 | 351 | 1 | 1 | 13 |
| 2016 | 351 | 13 | 13 | 21 |
| 2017 | 350 | 31 | 31 | 39 |
| 2018 | 351 | 2 | 1 | 7 |
| 2019 | 353 | 4 | 4 | 8 |
| 2020 | 353 | 0 | 0 | 4 |
| 2021 | 347 | 0 | 0 | 3 |
| 2022 | 358 | 0 | 0 | 9 |
| 2023 | 363 | 4 | 4 | 9 |
| 2024 | 362 | 4 | 4 | 8 |
| 2025 | 364 | 13 | 13 | 29 |

- **Most affected:** 2005–2007 and 2012–2013. These are the low box-coverage seasons, where most teams have results without box scores.
- **Where `PTS_IDENTITY` exceeds the `points` conflicts** (e.g. 2015: 13 vs 1), the summary faithfully sums team-game boxes that themselves fail the identity (the 137 flagged `fact.team_games` rows). The game-derived table inherits those rows unchanged, and `test_game_derived_boxed_totals_satisfy_the_points_identity` proves the identity holds whenever every underlying team-game is clean.
- **Player summaries** are covered by §5. **Shooting summaries** (`*_season_shooting`) are non-reconstructable, because shot-type families do not exist per game in the Starter Pack. They remain priors or QA only, never in-season features.

## 4. Consequences for later stages

- Season-to-date team features: use `pit.team_season_to_date` over `fact.team_games`. Use `points_all_results`-style scoring only with its own `games_with_result` denominator.
- Completed-season priors: prefer `fact.team_season_game_derived`. A raw summary field may be used as a prior only where it has no row in `qa.season_summary_conflicts`.
- Raw summaries must never be used to "repair" game-level rows.

## 5. Player-season authority (frozen, Stage 1 final unblock)

The same principle applies to player-season statistics:

1. **Canonical player-game records are authoritative** for reconstructable player season-to-date counts and rates (`fact.player_games`, and the flagship view `flagship.player_season_game_derived`).
2. **Player-season summary exports remain useful** for QA, priors, and non-reconstructable fields. Those fields include `usage_source`, `offensive_rating_source`, `porpag_source` and shot-type families. The raw `fact.player_season_stats` / `fact.player_season_shooting` tables are kept unchanged; `test_raw_player_season_summaries_are_preserved_unchanged` checks this.
3. **When a reconstructable field conflicts with the aggregation of canonical player-game records, the game-derived value wins** for modeling.

**What "reconstructable" means.** It is measured, not assumed. `fact.player_season_game_derived` sums the *resolved* player-game rows. A player-season is reconstructable (`identity_complete = TRUE`) only when its team-season has **no** unresolved player-game rows. Otherwise the game-derived sum could be missing some of that player's games, which is an identity gap rather than a source disagreement. Those conflicts are recorded with `authoritative_source = 'IDENTITY_GAP_NO_AUTHORITY'`: neither value silently wins, and later stages must treat such player-seasons as incomplete.

**Measured (build `20261005T163639Z`):**

| Quantity | Value |
|---|---:|
| raw player-season summary rows | 99,596 |
| game-derived player-season rows | 99,390 (98,667 `identity_complete`) |
| summary rows with no resolved player-game row at all | 206 |
| conflict rows in `qa.player_season_summary_conflicts` | 6,095 (360 player-seasons) |
| conflicts on **reconstructable** player-seasons | **0**: all 20 compared fields (`games`, `starts`, minutes, all counting primitives) agree on 98,667 / 98,667 |
| conflicts on identity-gap player-seasons | 6,095, all `IDENTITY_GAP_NO_AUTHORITY` |

The identity-gap player-seasons are concentrated in 2017 (206, the mirrored-box-line defect, CANONICAL_SCHEMA §9), followed by 2020 (44), 2015 (24), 2025 (22) and 2006 (16). Unlike the team summaries (§2), the player summaries contain no mixed-basis defect. Every disagreement traces to unresolved player-game identity. In the summary, `games` counts every box row, including 0-minute rows.

**Tests** (`tests/test_stage1_final.py`):
- `test_player_season_game_derived_recomputes_from_resolved_player_games`;
- `test_reconstructable_player_seasons_have_game_derived_authority`;
- `test_every_player_season_conflict_is_recorded`;
- `test_raw_player_season_summaries_are_preserved_unchanged`;
- `test_flagship_exposes_player_season_game_derived`.
