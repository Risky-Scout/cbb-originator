# Possession and lineup reconstruction policy

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN (design only)**. Nothing is reconstructed here.
The build is Phase A2. This document is the original frozen Stage 4 ("possession / lineup reconstruction"). That stage
was never implemented: `fact.possessions` and `fact.lineup_stints` hold 0 rows in v2.3 and v2.4.

Inputs: trusted relations only (`flagship.plays`, `flagship.on_floor_trusted`, `flagship.substitutions`,
`flagship.lineup_stats`, `flagship.team_game_outcomes`), games in `flagship.played_games`. CBBD play types observed
(2023-24): JumpShot, LayUpShot, DunkShot, TipShot, MadeFreeThrow, Defensive/Offensive/Dead Ball Rebound, PersonalFoul,
Technical Foul, Lost Ball Turnover, Steal, Block Shot, Jumpball, End Period, End Game, OfficialTVTimeOut,
ShortTimeOut, RegularTimeOut.

## 1. Possession definitions

A **possession** is an uninterrupted period in which one team (the **offensive team**) has the ball. It ends exactly
when the other team gains possession or the period ends. Team possessions in a game differ by at most 1 per period,
plus explicit exceptions (§1.9).

| # | situation | rule |
|---|---|---|
| 1.1 | **start** | at the period's opening possession (jump ball / alternating arrow), or the instant the previous possession ends |
| 1.2 | **offensive team** | the team credited with the ending event: shooter's team on a FGA; the turnover team on a turnover; the fouled team on FTs. For rebounds, the rebounding team holds the *next* possession |
| 1.3 | **turnover** | ends the possession of the committing team; the next possession belongs to the opponent. A steal is a turnover by the other team, not a separate possession event |
| 1.4 | **made field goal** | ends the possession, **unless** an and-one free throw follows: the possession then ends after the last FT of that trip (§1.6) |
| 1.5 | **missed shot / rebound** | a miss does not end the possession. A **defensive** rebound (incl. team / dead-ball defensive rebounds) ends it. An **offensive** rebound continues the **same** possession (the possession counts once; second-chance attempts are flagged `oreb_extension`) |
| 1.6 | **fouls / free throws** | a shooting-foul trip belongs to the fouled team's possession. The possession ends after the **last** FT of the trip: on a make → the opponent's possession; on a miss → by the rebound (§1.5). Non-shooting fouls without FTs (team not in bonus) do not end the possession |
| 1.7 | **technical / flagrant** | technical FTs **never** start or end a possession; possession stays with whoever held it (or the arrow for dead-ball technicals). Flagrant FTs belong to the fouled team's possession, and the fouled team retains the ball: the possession continues after the FTs |
| 1.8 | **jump balls** | a held ball mid-possession: the arrow team keeps or gains possession. A change of team = turnover-type end (`end_reason = JUMP_BALL_ARROW`) |
| 1.9 | **end of period** | the possession in progress ends at End Period (`end_reason = PERIOD_END`) even with no event; a last-second heave is a FGA of that possession. Period-end possessions are flagged `truncated` |
| 1.10 | **overtime** | each OT period is a period: opening jump ball, same rules, `period >= 3`, 5 min (300 s) |

Every possession row records: `game, period, possession_no, offensive_team, start_seconds, end_seconds, start_event,
end_event, end_reason (MADE_FG | DEF_REB | TURNOVER | LAST_FT | PERIOD_END | JUMP_BALL_ARROW), points, fga, fta,
orb_extensions, truncated, quality_flags`. Points are summed only from scoring events in the window.

## 2. Exact chronological stint construction

A **stint** is a maximal time interval within a period during which the same five players of one team are on the floor.

1. **Sources, in order of authority:**
   (a) substitution events with complete timing (`flagship.substitutions`, `timing_complete`);
   (b) per-play `onFloor` sets on VALID_5V5 plays (`flagship.on_floor_trusted`);
   (c) PBP actor evidence (an actor must be on the floor at his event);
   (d) `lineup_stats` aggregates, **as a check only**.
2. **Construction:** start each period from the starters, or the first VALID_5V5 onFloor set of the period when the
   starters are not recorded. Apply substitutions in clock order; ties at the same clock instant are applied as one
   block, after the event that stopped play. The result must be a 5-player set at every instant.
3. **Cross-validation:** every VALID_5V5 play's onFloor set must equal the reconstructed set at that clock. Every PBP
   actor must be in his team's set (`SHOOTER_NOT_ON_FLOOR`-type contradictions are counted).
4. **Never** infer stints from aggregate lineup minutes alone. `lineup_stats` totals may validate a reconstruction (seconds
   per unit within tolerance) but never create one.
5. Seasons without a substitution feed or VALID_5V5 onFloor coverage get **no** exact stints. Exact-lineup history
   exists only where CBBD provides it (2023-24+). This is not manufactured for older seasons.

## 3. Quality score and acceptance classes (per team-game)

Quality components (each in [0, 1]):

| component | measure |
|---|---|
| `q_five` | share of game seconds with exactly 5 players in the reconstructed set |
| `q_onfloor_agree` | share of VALID_5V5 plays whose onFloor set equals the reconstruction |
| `q_actor_on_floor` | share of player actor events whose actor is in the set |
| `q_minutes` | 1 − abs(Σ stint seconds − 5 × game seconds) / (5 × game seconds) |
| `q_box_minutes` | share of players whose stint minutes are within ±2 min of the box minutes |
| `q_possession_balance` | team possessions differ by ≤ 1 per period (or a recorded exception) |
| `q_score` | possession points reproduce the official final score exactly |

`quality_score = min(components)` (weakest link, not an average). Classes:

| class | rule |
|---|---|
| `accepted` | every component ≥ its frozen threshold |
| `ambiguous` | all components ≥ the lower bound but at least one below its threshold; usable only by a model whose eligibility manifest explicitly admits `ambiguous` |
| `rejected` | any component below its lower bound, or `q_score < 1` |
| `fallback_core_only` | the team-game has no usable stint inputs. It stays in core (box / possession-level) models but is excluded from lineup / interaction / substitution models |

## 4. Freezing thresholds before the pass/fail evaluation (anti-tuning rule)

1. Before the full reconstruction is evaluated, a **calibration sample** is fixed and hashed: the first 200
   2023-24 games by `canonical_game_id` sort. Thresholds and lower bounds are chosen on that sample only.
2. The chosen thresholds, the sample's hash and the code commit are written to
   `modeling/reconstruction/THRESHOLDS_FROZEN.json` and committed **before** any full-window run.
3. The full-window evaluation (all 2023-24 and 2024-25 games, then any older season with inputs) runs against the
   frozen file. The pass/fail result is recorded next to it.
4. A threshold may not be weakened after the result is seen. A change requires a new version of this policy, an
   architect ruling, and a fresh hashed calibration sample disjoint from the first. Previous results are kept.
5. Gate (proposed for A2, values set in step 2): accepted share per season reported. Lineup and interaction models are
   eligible only on `accepted` team-games (plus `ambiguous` if the manifest says so).

## 5. Outputs (Phase A2)

`fact.possessions` and `fact.lineup_stints` (DDL already in `cbb_platform/schema.sql`), `qa.reconstruction_quality`
(per team-game components, score and class), a per-season summary, and tests for: possession balance, score
reproduction, five-on-floor, determinism and lockbox exclusion.
