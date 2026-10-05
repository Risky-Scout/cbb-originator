# DATA_REQUIREMENTS.md
## CBB Originator Interconnected Player Prop Predictive Model
### Data Requirements Specification v1.4 — Frozen Source Architecture + Participation/DNP Amendment

**Status:** FROZEN FOR DATA PLATFORM v1.0 — CBBD + SHARP STATS + BDL + NON-BLOCKING BART TORVIK ENRICHMENT  
**Scope:** Pregame model first; future in-play model requirements are identified now so the data architecture does not need to be rebuilt later.  
**Primary objective:** Define every raw field, derived field family, target, timestamp, identity key, and quality requirement needed to train, backtest, deploy, and validate the CBB Originator Interconnected Player Prop Predictive Model and its later, separate in-play model.

---

# 1. Governing principles

1. **The model must be point-in-time correct.** A historical prediction may use only information that was actually available before that prediction timestamp.
2. **Raw event data is preferred over precomputed summaries.** We can derive rates, rolling windows, efficiencies, lineups, and matchup features ourselves if the underlying events are available.
3. **Counts and opportunities are preferred over percentages.** For example, store 3PM and 3PA rather than only 3P%; store FTM and FTA rather than only FT%.
4. **Pregame and in-play are separate predictive models.** They may share canonical data, player/team latent states, lineup representations, and simulation machinery.
5. **The simulator is downstream of fitted probabilistic models.** It propagates learned conditional probabilities and enforces basketball accounting identities; it does not invent predictive relationships. Where an exact PMF/CDF is tractable, direct probability summation is authoritative; where the full joint distribution is not exactly enumerable, a convergence-qualified Monte Carlo draw bank is the numerical distribution representation.
6. **Not every subsystem needs the same historical window.** Player talent can use the deepest reliable player-game history; lineup/PBP uses seasons with adequate event coverage; Sharp Stats availability begins with current-season capture; sportsbook benchmark history may be shorter and prospective.
7. **Every externally sourced observation must carry lineage and timing metadata.**
8. **No model-development phase may silently invent missing data.** Missing P0 data must either be acquired, reconstructed under a documented rule, or explicitly excluded.

---


# 1A. Authoritative CBBD v1.29.0 audit basis

This revision is grounded in a complete audit of the versioned CBBD OpenAPI specification:

`https://api.collegebasketballdata.com/api/1.29.0/cbbd-openapi.json`

The audit was cross-checked against CBBD's official OpenAPI-generated Python client for version 1.29.0.

Audit totals from the attached OpenAPI JSON:

- **40 GET operations**
- **66 named OpenAPI component schemas** (61 object schemas + 5 enum-only schemas)
- **653 direct top-level properties** across the named object schemas
- **1,404 endpoint-expanded scalar/leaf response fields** after recursively resolving nested objects, arrays, `$ref`, and `allOf`

The official CBBD-generated Python client materializes **117 model classes** because inline nested objects are promoted into generated classes. That 117 figure is a client-code generation detail, not the count of named OpenAPI component schemas.

The complete field inventory and Starter Pack crosswalk are maintained in:

- `CBBD_API_FIELD_INVENTORY_AND_CROSSWALK.md`
- `CBBD_API_SCHEMA_FIELD_INVENTORY.tsv`
- `CBBD_API_ENDPOINT_INVENTORY.tsv`
- `CBBD_ENDPOINT_FIELD_CROSSWALK.csv`

## CBBD source-status vocabulary used in this specification

- **CBBD-DIRECT** — the exact field/object exists in OpenAPI v1.29.0.
- **CBBD-DERIVED** — CBBD exposes the primitives necessary to construct the canonical field.
- **CBBD-PIT-DIRECT** — the relevant endpoint supports date bounds suitable for constructing historical season-to-date state.
- **EXTERNAL-P0** — required information is not supplied by CBBD and a second provider/source is mandatory.
- **EXTERNAL-P1/P2** — useful enrichment absent from CBBD; does not block core v1 unless promoted by validation.
- **OUR-INGESTION** — operational timestamps/version metadata that must be recorded by our own pipeline even when the basketball event comes from CBBD.

## Critical conclusions from the audit

1. **CBBD is substantially more complete than the Starter Pack.** The API directly supplies PBP, `onFloor`, shot information, substitutions, five-player lineup aggregates, rosters, recruiting, portal transfers, ratings, lines, scoreboard state, venues, and coverage metadata.
2. **Player injury/availability data is not a CBBD 1.29.0 capability.** The schema named `Availability` describes data-coverage state (`available`, `partial`, `unavailable`), not player health.
3. **Player-prop odds are not supplied by CBBD 1.29.0.** The `/lines` endpoint is game-market data.
4. **CBBD game lines are not a complete timestamped line-history feed.** `GameLineInfo` contains provider, current spread/total/ML and opening spread/total, but no snapshot timestamp, both-side spread/total prices, or change history.
5. **CBBD PBP is unusually valuable.** `PlayInfo` contains event identity, game state, `wallclock`, participants, `onFloor`, and structured `shotInfo` including shooter, made/missed, range, assisted flag, assister, and x/y location.
6. **Substitution rows are athlete-centric.** CBBD gives each athlete's `subIn` and `subOut` game states; paired `player_in_id`/`player_out_id` transactions and exact lineup stints remain canonical derived artifacts.
7. **Lineup endpoints are aggregate lineup-unit statistics, not exact stint intervals.** Exact stint start/end boundaries must be reconstructed from PBP/on-floor/substitution data.
8. **Several core statistics endpoints are date-bounded.** Player/team season stats, shooting stats, and team lineups accept `startDateRange`/`endDateRange`, materially improving point-in-time reconstruction.
9. **Most standalone ratings/leaderboards are not date-bounded.** Historical SRS, adjusted-efficiency, and leaderboard values must be recreated from point-in-time primitives or archived snapshots. **Exception:** `GameInfo` directly exposes `homeTeamEloStart` / `awayTeamEloStart` and corresponding end-of-game Elo fields; the start values are a potentially valid historical pregame Elo feature, subject to coverage and semantic validation.
10. **Venue coordinates/timezone/elevation, coach history, and referee data are not exposed by the audited API.**
11. **CBBD provides current scoreboard/PBP state, but not our receipt latency or generic event revision/deletion metadata.** Live production requires OUR-INGESTION timing and may require a separate low-latency feed with correction semantics.

# 2. Priority definitions

| Priority | Meaning |
|---|---|
| **P0 — Required** | Needed for the core basketball model, valid walk-forward testing, or production inference. Missing P0 data can block a subsystem or force a documented fallback. Market-benchmark data is classified separately and does not become a predictive input merely because it is useful for evaluation. |
| **P1 — Strong accuracy enhancement** | Not necessary to compile a first model, but likely valuable enough that the schema must support it now. |
| **P2 — Optional/research** | Test only after the core model is stable and validated. It must not delay the initial build. |

Model tags:

- **PRE** = Pregame model
- **LIVE** = Future in-play model
- **BOTH** = Used by both

---

# 3. Canonical identity and time fields

These fields are foundational. Every downstream table must join through stable canonical IDs rather than names.

## 3.1 `games`

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `game_id` | P0 | BOTH | Stable game key for every stats, PBP, odds, lineup, injury, and prediction record. |
| `provider_game_id` | P0 | BOTH | Allows deterministic reconciliation with each external source. |
| `season` | P0 | BOTH | Historical partitioning, aging, season priors, walk-forward splits. |
| `season_type` | P0 | BOTH | Regular season/postseason/tournament behavior can differ. |
| `scheduled_start_time_utc` | P0 | BOTH | Defines point-in-time cutoffs and time-to-tip features. |
| `actual_start_time_utc` | P0 | LIVE | Aligns live events and handles delayed starts. |
| `home_team_id` | P0 | BOTH | Canonical matchup identity. |
| `away_team_id` | P0 | BOTH | Canonical matchup identity. |
| `home_conference_id` | P1 | PRE | Conference context and cold-start shrinkage. |
| `away_conference_id` | P1 | PRE | Conference context and cold-start shrinkage. |
| `venue_id` | P0 | BOTH | Venue effects, travel, neutral-site handling. |
| `neutral_site` | P0 | BOTH | Home-court treatment and tournament context. |
| `conference_game` | P1 | PRE | Context/regime feature and schedule segmentation. |
| `game_type` | P1 | BOTH | Regulation/tournament/other rules context. |
| `tournament_id` / `tournament_name` | P1 | BOTH | Neutral floors, postseason rotations, urgency/context. |
| `home_seed` | P2 | PRE | Tournament contextual feature; should be tested rather than assumed useful. |
| `away_seed` | P2 | PRE | Same as above. |
| `venue_city` | P1 | PRE | Travel and location derivation. |
| `venue_state_region` | P1 | PRE | Travel/location derivation. |
| `venue_country` | P1 | PRE | Travel/location derivation. |
| `status` | P0 | BOTH | Scheduled/in progress/final/postponed/cancelled. |
| `periods_played` | P0 | BOTH | Regulation versus overtime and historical target reconciliation. |
| `overtime_count` | P0 | BOTH | Separate regulation opportunity from overtime opportunity. |

## 3.2 Canonical time/lineage fields required on time-sensitive observations

| Field | Priority | Why required |
|---|---:|---|
| `effective_at` | P0 | When the information became true in the real world. |
| `provider_published_at` | P0 where available | When the provider says it published/updated the information. |
| `received_at` | P0 | When our system actually received it; crucial for honest backtests. |
| `ingested_at` | P0 | Data-pipeline traceability and debugging. |
| `valid_from` | P0 for mutable facts | Bitemporal history of status/roster/odds changes. |
| `valid_to` | P0 for mutable facts | Determines when a version stopped being current. |
| `provider` | P0 | Source lineage. |
| `provider_record_id` | P0 where available | Deduplication/reconciliation. |
| `revision_number` | P0 for PBP/odds/live | Corrections and replays. |
| `is_deleted_or_voided` | P0 for PBP/odds | Prevents corrected events from remaining active. |
| `schema_version` | P0 | Reproducibility across vendor/schema changes. |
| `ingestion_run_id` | P1 | Operational reproducibility. |
| `raw_payload_hash` | P1 | Detect silent source changes and duplicates. |

---

# 4. Team identity and season membership

## 4.1 `teams`

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `team_id` | P0 | BOTH | Stable canonical team key. |
| `provider_team_id` | P0 | BOTH | Cross-source reconciliation. |
| `team_name` | P0 | BOTH | Human-readable output and QA. |
| `abbreviation` | P1 | BOTH | Output/QA. |
| `conference_id` | P0 | PRE | Opponent strength, schedule, hierarchical priors. |
| `conference_name` | P1 | PRE | Human-readable analysis. |
| `division_level` | P1 | PRE | Needed if FBS/FCS-like cross-level CBB schedules or non-DI opponents appear. |
| `valid_from` / `valid_to` | P0 | BOTH | Conference/team identity can change over time. |

## 4.2 `team_season_membership`

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `team_id` | P0 | BOTH | Join key. |
| `season` | P0 | BOTH | Team-season state. |
| `conference_id` | P0 | PRE | Conference strength and schedule context. |
| `head_coach_id` | P1 | BOTH | Rotation/substitution tendencies and regime changes. |
| `coach_tenure_start` | P1 | PRE | Regime-change feature. |
| `coach_tenure_end` | P1 | PRE | Regime-change feature. |

---

# 5. Player identity, bio, and roster membership

## 5.1 `players`

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `player_id` | P0 | BOTH | Stable player identity across seasons and transfers. |
| `provider_player_id` | P0 | BOTH | Cross-source mapping. |
| `full_name` | P0 | BOTH | QA/output; never use name as primary key. |
| `position` | P0 | BOTH | Hierarchical priors, matchup and role features. |
| `height_inches` | P1 | PRE | Rebound/block/shot-profile priors and cold starts. |
| `weight_lbs` | P1 | PRE | Physical/role prior. |
| `class_year` | P1 | PRE | Development/cold-start prior. |
| `birth_date` or `age` | P2 | PRE | Development curve if reliably available. |
| `dominant_hand` | P2 | PRE | Only if reliable and proven useful. |

## 5.2 `roster_membership`

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `player_id` | P0 | BOTH | Player identity. |
| `team_id` | P0 | BOTH | Team assignment. |
| `season` | P0 | BOTH | Historical roster state. |
| `valid_from` | P0 | BOTH | Handles transfers, dismissals, midseason changes. |
| `valid_to` | P0 | BOTH | Same. |
| `roster_status` | P0 | BOTH | Active/inactive/redshirt/left team if available. |
| `jersey_number` | P2 | LIVE | Can help PBP identity reconciliation, not predictive by itself. |

---

# 6. Player-game outcome data

**Grain:** one player × one game.

These are historical outcome variables and core inputs for latent player talent, rate, exposure, and role models.

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `game_id` | P0 | BOTH | Join key. |
| `player_id` | P0 | BOTH | Join key. |
| `team_id` | P0 | BOTH | Team context. |
| `opponent_team_id` | P0 | PRE | Matchup/opponent adjustment. |
| `starter` | P0 | BOTH | Rotation/minutes model. |
| `minutes` | P0 | BOTH | Exposure target and normalization base. |
| `ejected` | P0 | PRE | Distinguishes abnormal exposure interruption. |
| `points` | P0 | BOTH | Target and reconciliation. |
| `fgm` | P0 | BOTH | Scoring accounting. |
| `fga` | P0 | BOTH | Shot-opportunity modeling. |
| `two_pm` | P0 | BOTH | Primitive scoring outcome. |
| `two_pa` | P0 | BOTH | Primitive shot opportunity. |
| `three_pm` | P0 | BOTH | Primitive scoring outcome. |
| `three_pa` | P0 | BOTH | Primitive shot opportunity. |
| `ftm` | P0 | BOTH | Primitive scoring outcome. |
| `fta` | P0 | BOTH | Foul/FT opportunity modeling. |
| `orb` | P0 | BOTH | Offensive-rebound event target. |
| `drb` | P0 | BOTH | Defensive-rebound event target. |
| `reb` | P0 | BOTH | Output/reconciliation; derivable as ORB+DRB. |
| `ast` | P0 | BOTH | Assist allocation/event model. |
| `stl` | P0 | BOTH | Steal/turnover dependence. |
| `blk` | P0 | BOTH | Shot-defense event model. |
| `tov` | P0 | BOTH | Turnover event model. |
| `pf` | P0 | BOTH | Foul risk, minutes, FT environment. |
| `usage` | P1 | PRE | Useful supplied summary; should also be derivable from primitives. |
| `game_pace` | P1 | PRE | Useful supplied exposure context; team possessions are preferred. |
| `offensive_rating` | P1 | PRE | Summary feature; derive point-in-time versions ourselves. |
| `defensive_rating` | P1 | PRE | Summary feature. |
| `net_rating` | P1 | PRE | Summary feature. |
| `ts_pct` | P1 | PRE | Derived efficiency summary, not a primitive target. |
| `efg_pct` | P1 | PRE | Derived efficiency summary. |
| `ft_rate` | P1 | PRE | Derived opportunity-style feature. |
| `orb_pct` | P1 | PRE | Derived role/rebound feature. |

### Rule
Percentages (`FT%`, `2P%`, `3P%`, `TS%`) are **derived outputs**, not independent primitive predictions. Store the numerator/denominator counts and derive the percentage.

---

# 7. Team-game outcome and environment data

**Grain:** one team × one game.

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `game_id` | P0 | BOTH | Join key. |
| `team_id` | P0 | BOTH | Team identity. |
| `opponent_team_id` | P0 | BOTH | Opponent identity. |
| `is_home` | P0 | PRE | Home/away effect. |
| `neutral_site` | P0 | PRE | Home-court treatment. |
| `game_minutes` | P0 | BOTH | Overtime/reconciliation. |
| `possessions` | P0 | BOTH | Fundamental exposure variable. |
| `pace` | P0 | BOTH | Game environment and count-stat opportunity. |
| `points` | P0 | BOTH | Team scoring target/reconciliation. |
| `fgm` / `fga` | P0 | BOTH | Team shot environment. |
| `two_pm` / `two_pa` | P0 | BOTH | Team shot mix. |
| `three_pm` / `three_pa` | P0 | BOTH | Team shot mix. |
| `ftm` / `fta` | P0 | BOTH | Foul/FT environment. |
| `orb` / `drb` / `reb` | P0 | BOTH | Team rebounding environment. |
| `ast` | P0 | BOTH | Team creation environment. |
| `stl` | P0 | BOTH | Turnover/pressure environment. |
| `blk` | P0 | BOTH | Rim/shot-defense environment. |
| `turnovers` | P0 | BOTH | Possession-outcome environment. |
| `team_turnovers` | P1 | BOTH | Official team turnovers when not assigned to a player. |
| `fouls` | P0 | BOTH | FT/minutes/bonus environment. |
| `offensive_rating` | P1 | PRE | Derived tempo-free team strength. |
| `defensive_rating` | P1 | PRE | Derived opponent suppression. |
| `efg_pct` | P1 | PRE | Four-Factor feature. |
| `turnover_rate` | P1 | PRE | Four-Factor feature. |
| `orb_pct` | P1 | PRE | Four-Factor feature. |
| `ft_rate` | P1 | PRE | Four-Factor feature. |
| `points_in_paint` | P1 | PRE | Style/matchup feature. |
| `fast_break_points` | P1 | PRE | Pace/transition style feature. |
| `points_off_turnovers` | P1 | PRE | Transition/pressure style. |
| `points_by_period` | P1 | BOTH | Regulation/OT QA and live-state validation. |

Opponent summary fields may be stored, but if the opponent's own team-game row exists they are redundant and should not become a second conflicting source of truth.

---

# 8. Play-by-play event data

**CBBD v1.29.0 source note — CBBD-DIRECT.** `/plays/*` returns `PlayInfo`, including `id`, `sourceId`, game IDs, season/game context, `playType`, period/clock/seconds remaining, score, optional `wallclock`, `playText`, `participants[]`, `onFloor[]`, and optional `shotInfo`. The canonical fields below that are not explicit CBBD fields (for example possession owner, event subtype, revision metadata) remain derived or OUR-INGESTION requirements.

**Grain:** one official play/event.

This is P0 for the elite interconnected architecture because it is the source for possession mechanics, player-event relationships, lineup context, and future in-play training.

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `event_id` | P0 | BOTH | Stable event identity. |
| `game_id` | P0 | BOTH | Game join. |
| `event_sequence` | P0 | BOTH | Deterministic ordering. |
| `period` | P0 | BOTH | Game-state reconstruction. |
| `clock_remaining` | P0 | BOTH | Rotation and game-state timing. |
| `event_time_utc` | P0 where available | LIVE | Latency and exact replay. |
| `event_type` | P0 | BOTH | Shot/turnover/foul/rebound/substitution/etc. |
| `event_subtype` | P0 | BOTH | Turnover/foul/shot detail. |
| `team_id` | P0 | BOTH | Team responsible for event. |
| `primary_player_id` | P0 when applicable | BOTH | Main actor. |
| `secondary_player_id` | P0 when applicable | BOTH | Assister/stealer/blocker/fouler/etc. |
| `tertiary_player_id` | P1 | BOTH | Complex events if source supports them. |
| `possession_team_id` | P0 | BOTH | Possession state and event tree. |
| `points_scored` | P0 | BOTH | Score reconciliation. |
| `home_score_after` | P0 | BOTH | State reconstruction. |
| `away_score_after` | P0 | BOTH | State reconstruction. |
| `is_scoring_play` | P1 | BOTH | QA/processing convenience. |
| `possession_ended` | P0 derived/observed | BOTH | Possession segmentation. |
| `official_description` | P1 | BOTH | QA and event-repair fallback. |
| `is_correction` / revision fields | P0 | LIVE | Official feeds can revise events. |

---

# 9. Shot-event detail

**CBBD v1.29.0 source note — mixed DIRECT/DERIVED.** Structured `shotInfo` directly supplies shooter, made/missed, range, assisted flag, assister, and x/y location. The API does not expose every desired canonical shot annotation (for example `is_putback`, generic blocker identity, transition flag, or structured foul-on-shot flag) as dedicated fields; those require derivation/validation from play type, participants, sequence, and play text or another source if coverage proves inadequate.

**Grain:** one field-goal attempt or free-throw event.

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `event_id` | P0 | BOTH | Link to PBP. |
| `shooter_player_id` | P0 | BOTH | Shooter allocation model. |
| `shot_value` (2/3/FT) | P0 | BOTH | Scoring mechanics. |
| `shot_family` | P0 | BOTH | Rim/layup/dunk/jumper/3P modeling. |
| `made` | P0 | BOTH | Conversion outcome. |
| `assisted` | P0 where known | BOTH | Shooter–creator dependence. |
| `assister_player_id` | P0 where applicable | BOTH | Assist assignment model. |
| `blocked` | P0 where known | BOTH | Block dependence. |
| `blocker_player_id` | P0 where applicable | BOTH | Defensive player attribution. |
| `shot_distance` | P1 | BOTH | Shot-quality/context. |
| `shot_x` | P1 | BOTH | Spatial shot model. |
| `shot_y` | P1 | BOTH | Spatial shot model. |
| `shot_zone` | P1 | BOTH | Robust categorical spatial feature. |
| `is_putback` | P1 | BOTH | ORB → shot dependence. |
| `is_transition` | P1 | BOTH | Transition efficiency/style. |
| `fouled_on_shot` | P1 | BOTH | FTA coupling. |
| `ft_number` | P0 for FT | BOTH | FT sequence mechanics. |
| `ft_total_in_trip` | P0 for FT | BOTH | FT sequence mechanics. |

---

# 10. Rebound, turnover, foul, and possession event detail

**CBBD v1.29.0 source note — primarily CBBD-DERIVED.** PBP exposes play type, participant IDs/names, play text, scores, on-floor players, and shot structure, but `participants[]` does not include a universal typed role. We must prove that rebounder/stealer/fouler/turnover attribution can be deterministically reconstructed at acceptable coverage before making those event labels P0 training dependencies.

## 10.1 Rebounds

| Field | Priority | Why |
|---|---:|---|
| `rebounder_player_id` | P0 | Player rebound allocation. |
| `rebound_type` (`ORB`/`DRB`/team) | P0 | Correct dependence and continuation. |
| `missed_shot_event_id` | P0 derived where possible | Links the rebound to the exact miss. |
| `team_rebound` | P0 | Official rebounds not credited to a player. |

## 10.2 Turnovers/steals

| Field | Priority | Why |
|---|---:|---|
| `turnover_player_id` | P0 | Player TOV model. |
| `turnover_type` | P1 | Pressure/role/matchup detail. |
| `steal_player_id` | P0 where applicable | Steal ↔ turnover dependence. |
| `is_team_turnover` | P0 | Reconciliation. |

## 10.3 Fouls

| Field | Priority | Why |
|---|---:|---|
| `fouler_player_id` | P0 | Player foul/minutes model. |
| `drawn_by_player_id` | P1 | Foul-drawing/FTA opportunity. |
| `foul_type` | P0 | Shooting/non-shooting/technical/flagrant distinctions. |
| `team_foul_number` | P0 for LIVE | Bonus state. |
| `player_foul_number` | P0 for LIVE | Foul-trouble state. |
| `bonus_state` | P0 for LIVE; derived if possible | Changes foul-to-FT transition probability. |

---

# 11. On-floor and lineup data

**CBBD v1.29.0 source note — CBBD-DIRECT + DERIVED.** `PlayInfo.onFloor[]` directly supplies player IDs/names/team at each recorded play. `/lineups/*` directly supplies aggregate five-player units with athletes, total seconds, pace, ratings, possession counts, shooting/rebounding/turnover/assist/block/steal and Four-Factor statistics. Exact stint boundaries remain derived.

This is the center of the interconnected model.

## 11.1 Event-level players on floor

For every PBP event or possession:

| Field | Priority | Model | Why |
|---|---:|---|---|
| `home_player_1_id` … `home_player_5_id` | P0 | BOTH | Teammate interactions, shooter/assist/rebound opportunity, matchup context. |
| `away_player_1_id` … `away_player_5_id` | P0 | BOTH | Opponent-player interactions and defensive context. |
| `lineup_confidence` | P1 | BOTH | Allows exclusion/downweighting of reconstructed uncertain lineups. |
| `lineup_source` | P1 | BOTH | Direct feed vs reconstructed from substitutions. |

## 11.2 `substitutions`

**CBBD v1.29.0 substitution mapping.** `/substitutions/*` returns athlete-centric `PlayerSubsititution` records with team/opponent context plus `subIn` and `subOut`, each carrying team points, opponent points, seconds remaining and period. Therefore the canonical `player_in_id` / `player_out_id` pairing below is a **derived representation**, not a direct CBBD row shape.

| Field | Priority | Model | Why |
|---|---:|---|---|
| `game_id` | P0 | BOTH | Join. |
| `event_id` | P0 | BOTH | Exact timing. |
| `team_id` | P0 | BOTH | Team rotation. |
| `player_out_id` | P0 | BOTH | Rotation transition. |
| `player_in_id` | P0 | BOTH | Rotation transition. |
| `period` | P0 | BOTH | Game-state context. |
| `clock_remaining` | P0 | BOTH | Stint reconstruction. |
| `score_margin` | P1 derived | BOTH | Coach substitution behavior. |
| `player_foul_state` | P1 derived | BOTH | Foul-driven substitutions. |

## 11.3 `lineup_stints` (derived, but required)

| Field | Priority | Why |
|---|---:|---|
| `stint_id` | P0 | Stable unit of lineup exposure. |
| `game_id` | P0 | Join. |
| `start_event_id` / `end_event_id` | P0 | Replayability. |
| `start_period` / `start_clock` | P0 | Timing. |
| `end_period` / `end_clock` | P0 | Timing. |
| 10 on-floor player IDs | P0 | Exact competing units. |
| `seconds_played` | P0 | Exposure. |
| `offensive_possessions_home` / `away` | P0 | Lineup efficiency normalization. |
| `home_points` / `away_points` | P0 | Outcome. |
| `home_fga`, `away_fga` | P1 | Lineup shot environment. |
| `home_fta`, `away_fta` | P1 | Lineup foul environment. |
| `home_tov`, `away_tov` | P1 | Lineup turnover environment. |
| `home_orb`, `away_orb` | P1 | Lineup rebound environment. |
| `garbage_time_flag` | P1 derived | Role/rotation regime. |
| `reconstruction_quality` | P0 | Prevents bad stint reconstruction from poisoning training. |

---

# 12. Rotation, depth-chart, and pregame role information

**CBBD v1.29.0 source note.** Historical actual starters, minutes, rosters, substitutions, on-floor states and lineup usage are available and can train our rotation model. CBBD does not provide a dedicated pregame projected-starter/depth-chart probability feed; those pregame forecasts are model outputs or external enrichments.

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `projected_active` | P0 | PRE | Feasible player set. |
| `projected_starter` | P0 | PRE | Minutes/rotation distribution. |
| `starter_probability` | P1 | PRE | Better than hard starter label when uncertain. |
| `depth_role` | P1 | PRE | Replacement/minutes redistribution. |
| `projected_minutes_external` | P2 | PRE | Challenger/ensemble input only; do not make dependency mandatory. |
| `minutes_cap` | P0 when known | PRE | Returning-from-injury restrictions. |
| `role_notes` | P1 | PRE | Structured/manual information for known special cases. |

These fields may come from a source or from our own pregame inference. The canonical schema must support them either way.

---

# 13. Injury and availability history

**Frozen source note — Sharp Stats current-season availability + CBBD historical participation.** CBBD has no structured injury endpoint, but the core historical availability/absence-response problem does **not** require a multi-year injury archive. Historical training uses actual participation/DNP, starts, minutes, substitutions, lineups, and teammate redistribution from CBBD (plus Bart Torvik DNP/advanced enrichment if available). Sharp Stats supplies current-season pregame status/reason/note information prospectively.

**Historical injury archives are P2 enrichment, not P0.** Do not delay Data Platform v1.0 or model construction trying to reconstruct five years of historical medical/status reports. Begin append-only Sharp Stats capture now and preserve our own exact `received_at_utc` timestamps.

**This must be timestamped. Final game status alone is insufficient.**

| Field | Priority | Model | Why required |
|---|---:|---|---|
| `availability_event_id` | P0 | BOTH | Versioned status-change identity. |
| `player_id` | P0 | BOTH | Player affected. |
| `game_id` | P0 where game-specific | BOTH | Links status to target game. |
| `team_id` | P0 | BOTH | Team context. |
| `status` | P0 | BOTH | Active / probable / questionable / doubtful / out / suspended / etc. |
| `reason_category` | P1 | BOTH | Injury, illness, suspension, personal, rest, unknown. |
| `injury_body_part` | P2 | PRE | Could inform minutes/skill return-to-play effects if reliable. |
| `effective_at` | P0 | BOTH | When status became true. |
| `provider_published_at` | P0 where available | BOTH | Historical information availability. |
| `received_at` | P0 | BOTH | Honest point-in-time replay. |
| `expected_return_date` | P1 | PRE | Availability prior. |
| `minutes_restriction` | P0 when known | BOTH | Rotation distribution. |
| `restriction_text` | P1 | BOTH | Audit/manual parsing. |
| `availability_probability` | DERIVED | PRE | Model output, not a raw vendor truth. |
| `final_played_flag` | P0 outcome | PRE | Availability-model training target. |
| `final_minutes` | P0 outcome | PRE | Restriction/rotation training target. |

---


## 13.1 Historical participation / DNP history — P0

Historical absence-response training must be built from the actual basketball record, not from historical injury labels.

Required per player-game/candidate roster slot:
- scheduled game
- roster membership as-of game date where available
- `played_flag`
- `dnp_flag`
- starter
- minutes
- prior rolling minutes/usage/role
- actual lineup/substitution participation
- teammate minute/usage/assist/rebound/shot-share responses

Primary source: **CBBD**.  
Secondary enrichment/verification: **Bart Torvik explicit DNP and advanced player-game logs**, if a stable/permitted acquisition route is available.

This is a P0 historical training asset.

# 14. Historical absence-response data (derived)

We must explicitly create training examples describing how teams reallocate opportunities when players are absent.

| Derived field | Priority | Why |
|---|---:|---|
| `absent_player_id` | P0 | Defines shock. |
| `replacement_starter_id` | P0 when identifiable | Starting-lineup response. |
| `teammate_minutes_delta` | P0 | Minutes redistribution target. |
| `teammate_usage_delta` | P0 | Possession-role redistribution. |
| `teammate_fga_share_delta` | P0 | Shot reallocation. |
| `teammate_three_pa_share_delta` | P1 | Shot-mix reallocation. |
| `teammate_fta_share_delta` | P1 | Foul-drawing/role reallocation. |
| `teammate_assist_share_delta` | P0 | Ball-handling/creation redistribution. |
| `teammate_rebound_share_delta` | P0 | Rebound opportunity redistribution. |
| `lineup_frequency_delta` | P0 | Which units replace the unavailable player. |
| `team_pace_delta` | P1 | Environment change. |
| `team_efficiency_delta` | P1 | Team-strength response. |
| `opponent_efficiency_delta` | P1 | Defensive response. |

All deltas must be estimated point-in-time and shrunk; naïve raw on/off averages should not be used as unregularized truth.

---

# 15. Shot-profile data

**CBBD v1.29.0 source note — CBBD-PIT-DIRECT.** Player and team season shooting endpoints contain tracked shots, assisted percentage, free-throw rate, dunks, layups, tip-ins, two-point jumpers, three-point jumpers, free throws and attempt mix. These endpoints support date bounds, so historical profiles can be reconstructed without using final-season aggregates.

The historical model should retain player- and team-level shot composition.

| Field | Priority | Model | Why |
|---|---:|---|---|
| `tracked_shots` | P1 | PRE | Reliability/exposure for shot-profile estimates. |
| `dunks_made` / `dunks_attempted` | P1 | PRE | Rim profile and matchup. |
| `layups_made` / `layups_attempted` | P1 | PRE | Rim finishing profile. |
| `tipins_made` / `tipins_attempted` | P1 | PRE | ORB/inside role. |
| `two_point_jumpers_made` / `attempted` | P1 | PRE | Midrange profile. |
| `three_point_jumpers_made` / `attempted` | P1 | PRE | Perimeter profile. |
| assisted counts/rates by shot type | P1 | PRE | Creation dependence on teammates. |
| attempt share by shot type | DERIVED | PRE | Style representation. |

Event-level shot data supersedes season summaries when available; summaries remain useful for long-history priors.

---

# 16. Recruiting and transfer data for cold starts

**CBBD v1.29.0 source note — CBBD-DIRECT.** Recruiting includes IDs, position, school/commitment, hometown, athlete ID when mapped, year, height, weight, stars, rating and ranking. Portal transfers include source ID, year, name, position, origin, destination, eligibility, years remaining, stars and rating. Transfer timing is season/year level rather than a fully timestamped transaction history.

| Field | Priority | Model | Why |
|---|---:|---|---|
| `player_id` | P0/P1 | PRE | Link to player. |
| `recruiting_class_year` | P1 | PRE | Cohort prior. |
| `recruiting_position` | P1 | PRE | Skill/role prior. |
| `stars` | P1 | PRE | Freshman talent prior. |
| `recruiting_rating` | P1 | PRE | More granular talent prior. |
| `national_rank` | P1 | PRE | Freshman prior. |
| `position_rank` | P1 | PRE | Positional prior. |
| `height` / `weight` at recruitment | P2 | PRE | Physical prior if current bio unavailable. |
| `previous_team_id` | P1 | PRE | Transfer history. |
| `destination_team_id` | P1 | PRE | New context. |
| `transfer_effective_season` | P1 | PRE | Point-in-time team assignment. |
| `transfer_date` | P1 if available | PRE | Timing and preseason information state. |
| `previous_level/conference` | P1 derived | PRE | Translation/shrinkage across competition levels. |
| `eligibility_status` | P1 | PRE | Roster availability/cold start. |

---

# 17. Venue, schedule, travel, and rest data

**CBBD v1.29.0 source note — mixed.** `VenueInfo` supplies ID/source ID/name/city/state/country and `GameInfo` supplies venue ID/name/city/state. It does **not** supply venue latitude/longitude, elevation, or timezone. Those enrichment fields are EXTERNAL-P1/P2; rest and schedule density are CBBD-DERIVED from game timestamps.

Raw inputs:

| Field | Priority | Model | Why |
|---|---:|---|---|
| `venue_id` | P0 | PRE | Venue effects. |
| `venue_latitude` / `longitude` | P1 | PRE | Travel distance. |
| `venue_elevation_ft` | P2 | PRE | Test altitude effects. |
| `venue_timezone` | P1 | PRE | Circadian/travel features. |
| `team_home_location_lat/lon` | P1 | PRE | Travel origin proxy. |
| `scheduled_start_local` | P1 | PRE | Local tip-time effects. |

Derived:

| Field | Priority | Why |
|---|---:|---|
| `days_rest` | P0 | Fatigue/rotation. |
| `games_last_3_days` | P1 | Schedule compression. |
| `games_last_7_days` | P1 | Schedule load. |
| `travel_distance_since_last_game` | P1 | Fatigue. |
| `timezone_shift_hours` | P1 | Circadian effect. |
| `road_trip_length` | P2 | Research feature. |
| `neutral_site_distance` | P2 | Crowd/travel context. |

---

# 18. Coach information

**CBBD v1.29.0 source note — EXTERNAL-P1.** No coach entity/history is exposed in the audited schema. Do not delay v1 for this; add only after the core rotation model is working and validation indicates incremental value.

| Field | Priority | Model | Why |
|---|---:|---|---|
| `coach_id` | P1 | BOTH | Rotation/substitution regime. |
| `team_id` | P1 | BOTH | Link. |
| `valid_from` / `valid_to` | P1 | BOTH | Coaching changes. |
| `tenure_game_number` | DERIVED P1 | PRE | Early-coach uncertainty. |
| historical rotation depth | DERIVED P1 | PRE | Minutes model. |
| starter continuity | DERIVED P1 | PRE | Rotation stability. |
| substitution timing profile | DERIVED P1 | BOTH | Rotation/state model. |
| foul-trouble substitution response | DERIVED P1 | BOTH | Minutes distribution. |
| blowout substitution behavior | DERIVED P1 | BOTH | Tail minutes. |

---

# 19. Referee data

**CBBD v1.29.0 source note — EXTERNAL-P2.** No referee assignment/tendency entity is exposed in the audited schema.

Do **not** delay the core build for this, but schema support is worthwhile.

| Field | Priority | Model | Why |
|---|---:|---|---|
| `game_id` | P2 | BOTH | Link. |
| `referee_id` | P2 | BOTH | Official-specific effects. |
| historical foul rate | DERIVED P2 | PRE | Potential FT/foul environment. |
| technical/flagrant rate | DERIVED P2 | PRE | Possible game-flow effect. |

Only retain if out-of-sample tests justify it.

---

# 20. Historical sportsbook game-market data

**Policy: benchmark/context only for the flagship model.** The pure CBB Originator must not use sportsbook spread, total, or moneyline as predictive features. CBBD's `/lines` endpoint is still useful for independent benchmarking and contextual diagnostics.

CBBD v1.29.0 directly supplies `provider`, `spread`, `overUnder`, `homeMoneyline`, `awayMoneyline`, `spreadOpen`, and `overUnderOpen`. Opening spread/total are explicit. The semantics of the stored `spread` / `overUnder` for completed games must be verified in `COVERAGE_AUDIT.json` before calling them closing lines.

| Field | Priority | Model | Why |
|---|---:|---|---|
| `game_id` | P1 | BENCHMARK | Join model predictions to the market. |
| `sportsbook_id` / provider | P1 | BENCHMARK | Provider-specific market context. |
| `market_type` | P1 | BENCHMARK | Spread / total / moneyline. |
| `line` | P1 | BENCHMARK | Opening/final stored threshold. |
| `american_price` or `decimal_price` | P2 when available | BENCHMARK | Required only for probability/CLV comparisons where prices exist. |
| `is_open` | P1 | BENCHMARK | Opening-market comparison. |
| `is_close` | DERIVED/VERIFY | BENCHMARK | Do not infer until CBBD stored-line semantics are empirically confirmed. |
| `snapshot_time_utc` | P2 historical / P1 prospective | BENCHMARK | Exact time-aligned market comparison; not needed to train the pure originator. |
| `received_at` | P1 prospective | BENCHMARK | Honest prospective replay. |

### Timestamped line movement policy

Historical intraday line movement is **not a build-blocking requirement**. It is P2 for retrospective analysis. Prospectively archive **BDL** market snapshots at standardized checkpoints such as **T-24h, T-6h, T-1h, and T-10m**, using BDL `updated_at`/`opened_at` plus our own receipt timestamp. Use The Odds API only if BDL vendor breadth, coverage, or reliability fails the benchmark gate.

---

# 21. Historical sportsbook player-prop data

**CBBD v1.29.0 source note — not supplied by CBBD.** No player-prop endpoint exists in the audited API.

**Revised policy: historical player-prop odds are P1 benchmark data, not P0 model data and never a predictive input to the flagship originator.** The model's training labels come from historical basketball outcomes in CBBD box scores/PBP: points, rebounds, assists, threes, minutes, turnovers, fouls, etc. Historical sportsbook props are useful only when asking whether our probability at a specific offered line was better than the sportsbook's probability.

The absence of historical player-prop prices therefore does **not** block training, calibration of the basketball distribution, injury re-simulation, or deployment of the pure originator. We may collect historical props if convenient, but we should not spend substantial build time reconstructing them.

For a prospective benchmark beginning in 2026-27, capture:

| Field | Priority | Model | Why |
|---|---:|---|---|
| `prop_snapshot_id` | P1 | BENCHMARK | Versioned market observation. |
| `game_id` | P1 | BENCHMARK | Game link. |
| `player_id` | P1 | BENCHMARK | Player link. |
| `sportsbook_id` | P1 | BENCHMARK | Book-specific comparison. |
| `market_type` | P1 | BENCHMARK | PTS / REB / AST / 3PM / combos / etc. |
| `line` | P1 | BENCHMARK | Offered threshold. |
| `over_price` | P1 | BENCHMARK | No-vig comparison. |
| `under_price` | P1 | BENCHMARK | No-vig comparison. |
| `snapshot_time_utc` | P1 | BENCHMARK | Match to the model checkpoint. |
| `market_status` | P1 | BENCHMARK | Open/suspended/closed. |
| `received_at` | P1 | BENCHMARK | Honest prospective replay. |

Recommended checkpoints: **T-24h, T-6h, T-1h, T-10m**, plus an optional final pre-tip snapshot where practical.

The flagship prediction path remains:

`P(Y | basketball information only)`

A market-aware challenger may be researched later, but it is outside the core build and must never contaminate the pure-originator benchmark.

---

# 21A. Secondary data sources — BDL and Bart Torvik

## 21A.1 BALLDONTLIE NCAAB (BDL)

BDL is **P1 QA/benchmark**, not the primary basketball source.

Use:
- `/ncaab/v1/player_stats` for independent player-game reconciliation;
- `/ncaab/v1/team_stats` for team-game reconciliation;
- `/ncaab/v1/games` for score/H1/H2/OT reconciliation;
- `/ncaab/v1/players/active` for current player identity/active-list QA;
- `/ncaab/v1/odds` and `/ncaab/v1/odds/opening` for prospective market benchmarking with side prices and explicit timestamps.

Do not replace CBBD PBP/on-floor/substitution/lineup data with BDL PBP.

All BDL odds live outside the pure-originator feature namespace.

## 21A.2 Bart Torvik

Bart Torvik is **P1 non-blocking enrichment**.

High-value historical player-game fields include:
- explicit DNP/played state;
- minutes;
- ORtg, usage, eFG%, TS%;
- OR%, DR%, AST%, TO%, STL%, BLK%;
- dunk/rim/midrange game-level shot splits;
- 2PT/3PT/FT;
- AST/TOV/STL/BLK/ORB/DRB/PF;
- BPM/OPM/DPM/NET/PTS.

The core build proceeds without Torvik if automated acquisition is not stable/permitted. No model component may become dependent on a Torvik-only field until coverage and incremental out-of-sample value are established.

# 22. Future in-play feed requirements

**CBBD v1.29.0 source note.** `/scoreboard` supplies current status/period/clock/score and a betting snapshot, while PBP supplies event state and optional wall-clock plus `onFloor`. However the audited schema does not provide our receipt latency or a generic revision/supersession/deletion protocol. The later live model must record OUR-INGESTION timestamps and should use a feed whose production latency/correction semantics meet the trading requirement.

These are identified now but **must not delay the pregame model build**.

## 22.1 Live game state

| Field | Priority | Why required |
|---|---:|---|
| `game_id` | P0 | Join. |
| `period` | P0 | State. |
| `clock_remaining` | P0 | Remaining opportunity. |
| `home_score` / `away_score` | P0 | Score state. |
| `possession_team_id` | P0 | Next-event distribution. |
| 10 `on_floor_player_id`s | P0 | Lineup-conditioned probabilities. |
| player foul counts | P0 | Minutes/rotation/defense. |
| team foul counts | P0 | Bonus and FT probability. |
| bonus state | P0 | Foul-to-FT transition. |
| timeouts remaining | P1 | End-game strategy. |
| current player box-score primitives | P0 | Observed portion of final stat. |
| current team box-score primitives | P0 | State/reconciliation. |
| current possessions | P0 | Live pace. |
| possession arrow / jump-ball state | P2 | Minor state improvement. |
| live availability/injury/ejection status | P0 | Immediate conditional update. |

## 22.2 Live latency and corrections

| Field | Priority | Why |
|---|---:|---|
| `event_time_utc` | P0 | True event time. |
| `provider_updated_at` | P0 | Source latency. |
| `received_at` | P0 | What production actually knew. |
| `processed_at` | P0 | Pipeline latency. |
| `prediction_generated_at` | P0 | Fair backtest/trading latency. |
| `prediction_published_at` | P1 | End-to-end latency. |
| `revision_number` | P0 | Corrections. |
| `supersedes_event_id` | P0 where applicable | Corrected event handling. |
| `is_deleted_or_voided` | P0 | Prevent phantom events. |

## 22.3 Live sportsbook prices

These are **benchmark/trading-evaluation data for the future in-play model, not inputs to the flagship pure-originator state update**. If collected, snapshots must support provider-appropriate timestamps and suspension/resumption state so model probabilities can be compared fairly to executable market prices.

---

# 23. Internal primitive variables the model should generate

The model's **display targets** are:

`MIN, PTS, TS%, REB, AST, STL, BLK, TOV, PF, ORB, DRB, FT, FT%, 2P, 2P%, 3P, 3P%`

But internally, the generative system should model or generate at least:

- minutes / court exposure
- possessions
- field-goal attempts
- 2PA / 2PM
- 3PA / 3PM
- FTA / FTM
- turnovers
- steals
- fouls
- blocks
- offensive rebounds
- defensive rebounds
- made-shot assist attribution
- shot type / shot location where supported
- substitution/lineup transitions
- overtime state

Then calculate deterministic outputs:

- `PTS = 2*2PM + 3*3PM + FTM`
- `REB = ORB + DRB`
- `2P% = 2PM / 2PA` when `2PA > 0`
- `3P% = 3PM / 3PA` when `3PA > 0`
- `FT% = FTM / FTA` when `FTA > 0`
- `TS% = PTS / (2*(FGA + c*FTA))`, using one frozen documented convention for `c`

Undefined percentages when the denominator is zero must remain explicitly undefined/null, not silently converted to 0%.

---

# 24. Derived pregame feature families

These are **not purchased fields**. They should be derived from point-in-time raw data.

## 24.1 Player latent talent
- time-decayed shooting skill by shot family
- FT skill
- turnover propensity
- assist creation propensity
- ORB/DRB propensity
- steal/block/foul propensity
- role-adjusted efficiency
- opponent-adjusted player rates
- posterior uncertainty for every latent skill

## 24.2 Exposure/role
- projected active probability
- projected starter probability
- projected minutes distribution, not only mean
- rotation rank
- recent minutes
- minutes volatility
- usage share
- shot share
- ball-handling/assist share
- rebound opportunity share
- teammate-absence-conditioned role

## 24.3 Team environment
- pace posterior
- offensive strength
- defensive strength
- shot-mix profile
- Four Factors
- transition profile
- foul/FT environment
- turnover pressure
- ORB/DRB environment
- opponent-adjusted ratings
- schedule-strength-adjusted form

## 24.4 Lineup interaction
- five-man lineup exposure
- player offensive/defensive main effects
- teammate interaction embeddings
- opponent interaction embeddings
- lineup shot mix
- lineup pace
- lineup rebound/turnover/foul profiles
- uncertainty/shrinkage for sparse lineups

## 24.5 Matchup
- offensive shot profile × opponent shot suppression
- primary creator × opposing pressure
- rebound role × opponent shot/miss/rebound environment
- rim role × opponent rim protection
- perimeter role × opponent 3PA/3P defense
- lineup-versus-lineup interaction representation

## 24.6 Schedule/context
- rest
- travel
- local tip time
- neutral site
- tournament/postseason
- coach regime
- roster continuity

Every derived feature must have an `as_of_time` and be recomputable without using future games.

---

# 25. Pregame model training labels

The model must support separate labels for the major stochastic layers.

| Submodel | Core labels |
|---|---|
| Availability | played / did not play; active status |
| Starter | started |
| Minutes | regulation minutes + OT minutes, with interruption flags |
| Rotation | substitution transition / lineup stint duration |
| Pace | possessions / possession rate |
| Turnover | possession ends in turnover; player attribution; steal attribution |
| Shot occurrence | FGA / shot type / shooter |
| Shot conversion | make/miss conditional on attempt and type |
| Foul/FT | foul event, drawn foul, FT trip, number of FTs |
| Assist | assisted make and assister identity |
| Rebound | rebound event, ORB/DRB/team rebound, rebounder |
| Block | blocked attempt and blocker |
| Player box score | all primitive counts for validation |
| Team game | score, possessions, margin, total for reconciliation |
| Joint distribution | complete same-game outcome vector |

---

# 26. Prediction output requirements

For every player/stat/line, store more than a point projection.

| Field | Priority | Why |
|---|---:|---|
| `prediction_id` | P0 | Reproducibility. |
| `model_version` | P0 | Version control. |
| `data_cutoff_time` | P0 | PIT audit. |
| `game_id` / `player_id` / `stat` | P0 | Identity. |
| `mean` | P0 | Central projection. |
| `median` | P0 | Prop-line relevance. |
| `variance` / `sd` | P0 | Distribution. |
| selected quantiles | P0 | Tail/interval validation. |
| exact PMF/CDF reference or converged simulation-bank reference | P0 | Authoritative distribution source for alternate lines/joint queries. |
| `probability_method` | P0 | Must identify `exact_pmf` versus `converged_mc`. |
| `distribution_version` | P0 | Ensures every line query uses the same authoritative distribution state. |
| `p_over`, `p_under`, `p_push` | P0 | Pricing from the authoritative distribution. |
| fair decimal odds | P0 | Pricing. |
| fair American odds | P1 | Presentation. |
| uncertainty/quality flag | P0 | Suppress unreliable markets. |
| `availability_scenario` | P0 | Active/out/questionable conditioning. |
| `simulation_bank_id` | P0 when MC | Ensures custom lines and joint queries reuse the production draw bank rather than launching a new low-N sample. |
| `simulation_draw_count` | P0 when MC | Monte Carlo precision/reproducibility. |
| `simulation_seed_or_reference` | P0 when MC | Reproducibility. |
| `mcse` / simulation interval | P0 when MC | Quantifies numerical sampling uncertainty. |
| `convergence_status` | P0 when MC | Publication gate. |
| support/truncation diagnostics | P1 | Detect lost tail mass or finite-support errors. |
| calibration version | P0 | Pricing reproducibility. |

**Probability authority rule:** if an exact PMF/CDF is available for the requested market, custom-line probabilities must be direct sums of that exact distribution and simulation cannot replace them. If the high-dimensional CBB joint distribution is not exactly enumerable, pricing must query a convergence-qualified production simulation bank. A small user-triggered simulation is never an official price source.

For simulation-based joint pricing, the same production draw must preserve all players and all game-level outputs together so arbitrary joint probabilities can be queried coherently.

---

# 27. Validation data requirements

To evaluate the model, maintain two separate evidence tracks: (A) basketball-distribution accuracy from official outcomes, and (B) sportsbook comparison only where contemporaneous market snapshots exist. Retain:

- exact historical prediction snapshot
- exact data cutoff
- model version
- bookmaker snapshot at comparable time **when available for the benchmark layer**
- no-vig market probability **when both-side prices are available**
- final official outcome
- closing line/price
- postgame corrections
- availability status known at prediction time
- forecast distribution, not just mean
- joint simulation set/reference where feasible

Required scoring families:
- MAE/RMSE for means
- log loss / Brier for threshold events
- calibration / ECE-style diagnostics
- CRPS or equivalent univariate probabilistic score
- multivariate energy score
- variogram-style dependence score
- interval coverage
- sportsbook no-vig comparison **for benchmark samples only**
- closing-line value **for benchmark samples only**
- realized ROI only as a secondary noisy economic metric
- results by market, season, team, player role, time-to-tip, and edge bucket

---


## 27.1 Probability-engine validation requirements

When an exact benchmark distribution exists:

- compare the exact PMF/CDF with a fixed-seed high-N sampler;
- report Monte Carlo standard error/confidence intervals for sampled event probabilities;
- test moneyline, spreads, totals, team totals, score marginals, margin distribution and total distribution;
- calculate maximum absolute CDF error and a distribution-level distance such as total-variation distance where appropriate;
- treat persistent exact-vs-simulation differences outside the frozen Monte Carlo tolerance as a blocking defect;
- never diagnose small-probability PMF bins using raw relative percentage differences from a low-N run.

When the joint distribution is simulation-only:

- require a frozen convergence gate before any probability can be published;
- retain draw count, effective sample size where relevant, MCSE/interval and convergence status;
- use adaptive or sufficiently large N for tail markets;
- reuse the same production draw bank for standard lines, alternate lines, custom lines and joint queries.

Interactive/user simulations are evaluated as diagnostics/visualizations only and are not part of official probability grading.

---

# 28. Current Starter Pack: fields already represented

Based on the uploaded Starter Pack schemas reviewed for this project, the existing package already represents substantial portions of:

### Game metadata
- game/season identity
- date/start information
- venue name/city/state/country
- neutral site
- conference game
- tournament
- home/away team IDs and outcomes

### Player-game
- minutes
- starter/ejected
- points
- FGM/FGA
- 2PM/2PA
- 3PM/3PA
- FTM/FTA
- ORB/DRB/REB
- AST/STL/BLK/TOV/PF
- usage
- game pace
- shooting/efficiency summaries
- team/opponent context
- persistent `athleteId`

### Player-season
- games/starts/minutes
- scoring/rebounding/playmaking/defensive counts
- usage and ratings
- shooting attempts/makes/percentages
- efficiency summaries
- win-share-style summaries

### Player shot profile
- tracked shots
- dunks
- layups
- tip-ins
- 2P jumpers
- 3P jumpers
- assisted-rate information
- attempt-mix breakdown

### Team-game
- pace and possessions
- shooting makes/attempts
- rebounds
- turnovers
- fouls
- points
- Four Factors
- assists/blocks/steals
- offensive-style summaries
- opponent mirror statistics

### Team-season
- season summaries of the same major team/opponent statistical families

These existing fields provide an excellent historical box-score/tempo/shot-profile foundation.

---


# 29. CBBD v1.29.0 crosswalk against the model requirements

The full row-level audit lives in `CBBD_API_FIELD_INVENTORY_AND_CROSSWALK.md` and `CBBD_ENDPOINT_FIELD_CROSSWALK.csv`. For model planning, the authoritative classification is:

## 29.1 P0/P1 requirements supplied directly by CBBD

- game/team/conference identifiers and season membership context
- game start date/time, status, neutral/conference/tournament context and venue IDs/names
- player game box scores and actual starter/minute outcomes
- team game box scores, possessions, pace and Four Factors
- player and team season statistics
- player and team shooting profiles
- **date-bounded** player/team season statistics and shooting profiles
- PBP with play ID/type, period, clock, seconds remaining, score and optional wall-clock
- event `participants[]`
- event `onFloor[]`
- shot shooter, made flag, range, assisted flag, assister, x/y location
- historical substitutions
- aggregate five-player lineup statistics
- **date-bounded** team lineup aggregates
- team rosters, player IDs/source IDs, position, height, weight, DOB and hometown where available
- recruiting player ratings/stars/rankings and commitment context
- transfer portal origin/destination/eligibility/years remaining/stars/rating
- game spreads/totals/moneylines plus opening spread/total
- SRS, Elo and adjusted efficiency
- polls/rankings
- current scoreboard state
- venue city/state/country
- coverage/completeness metadata in the team-season overview

## 29.2 Required canonical fields that are CBBD-DERIVED rather than direct

- exact possession segmentation and `possession_team_id`
- canonical event sequence index if CBBD event ID/order is not sufficient
- typed generic event actor roles for rebound/steal/foul/turnover where not structurally exposed
- paired `player_in_id`/`player_out_id` substitution transactions
- exact lineup stint start/end boundaries
- team/player rest and schedule-load variables
- travel distance once venue/team coordinates are enriched
- absence-response deltas
- all rolling/time-decayed/opponent-adjusted features
- player latent skill, role and environment posteriors
- game/player joint simulation outputs
- no-vig probabilities and betting comparison metrics

These derivations must have automated reconciliation and coverage gates; we must not assume a derivation is reliable until measured.

## 29.3 True external requirements after the API audit

### Core / automated availability requirement

1. **Current-season Sharp Stats availability snapshots** — P0 for automated pregame availability. Store raw status, raw injury/reason, raw note, source update date, and our exact `received_at_utc`.
2. **Historical participation/DNP/role history from CBBD** — P0 for learning absence impact and redistribution.
3. **Known pregame minutes restrictions / restriction timestamps when stated** — P0 when available for the specific game.
4. **Multi-year historical injury/status archives** — P2 optional enrichment; not a build gate.
3. **Production receipt/processing timestamps** — OUR-INGESTION fields that must be recorded prospectively.
4. **Production live correction/revision semantics** — required for the eventual in-play feed if CBBD does not satisfy latency/replay requirements.

### Benchmark data — useful but not core-model P0

- historical player-prop lines/prices: **P2/P1-later benchmark only; not needed for v1**
- timestamped historical game-line movement: **P2 retrospective benchmark**
- prospective BDL odds snapshots at T-24h / T-6h / T-1h / T-10m: **P1 benchmark and strongly recommended**; Odds API is fallback only

The pure-originator prediction path must remain free of sportsbook inputs.

## 29.4 P1/P2 external enrichments that must not block v1

- venue latitude/longitude/timezone/elevation
- coach identity/history
- referee assignments/tendencies
- externally projected starters/depth charts
- richer travel/circadian context
- any optical tracking data
- public sentiment or news NLP beyond structured availability

## 29.5 Fields in the API that should not be naively used as historical features

The following are useful but require special point-in-time treatment because their endpoints do not expose date-bounded historical snapshots:

- `/ratings/adjusted`
- `/ratings/srs`
- `/stats/team/leaderboard`
- `/teams/{teamId}/season/{season}/overview`

`/ratings/elo` is also not date-bounded, but `/games` exposes `homeTeamEloStart` and `awayTeamEloStart` on each historical game. Prefer those game-level start-Elo fields for historical pregame Elo context if the coverage audit validates them.

For any non-date-bounded completed-season metric, compute an equivalent from information available before the cutoff or use snapshots archived at the time. Never query a finalized past season today and inject that final value into an earlier game.

# 30. Data-quality constraints and hard invariants

The canonical data pipeline must test these before modeling.

## Identity
- no duplicate canonical game IDs for the same contest
- one unambiguous canonical team mapping per provider team ID at a point in time
- player identity must survive transfers/name changes
- player-game rows must join to valid roster/team/game combinations

## Box-score accounting
- `PTS = 2*2PM + 3*3PM + FTM`
- `REB = ORB + DRB`
- `FGM = 2PM + 3PM`
- `FGA = 2PA + 3PA`
- makes cannot exceed attempts
- player totals should reconcile to official team totals subject to documented team-stat exceptions

## Minutes
- regulation team player-minutes should reconcile to 200 minutes
- overtime increments must reconcile to 25 player-minutes per five-minute OT
- interruption/ejection/injury cases must remain observable rather than silently deleted

## PBP
- event sequence must be orderable
- score must reconcile to final score
- possession reconstruction must be deterministic or carry a confidence flag
- lineups must contain exactly five valid players per team when marked complete
- substitution transitions must not create impossible duplicate/on-court states
- corrections must supersede prior versions

## Odds
- snapshots must have timestamps
- price/line/book/market must be non-null for usable records
- no-vig derivation must use both sides when both are required
- closing snapshot definition must be frozen
- suspended/void markets must be distinguishable


## Probability-engine / numerical integrity

- exact-PMF pricing and custom-line pricing must agree bit-for-bit or within deterministic floating-point tolerance when they query the same exact distribution
- exact PMF/CDF probabilities are the source of truth wherever exact enumeration is available
- low-N interactive simulations may never overwrite or become the official probability record
- simulation-based official probabilities must pass the frozen MCSE/convergence gate
- every official probability must identify `probability_method`
- all alternate/custom-line queries must use the same `distribution_version`
- where an exact benchmark exists, high-N simulated probabilities must be statistically consistent with it
- support truncation / lost tail mass must be measured and bounded before publication

## Point-in-time
- no feature may have a source/receipt timestamp after the prediction cutoff
- season-to-date aggregates must exclude the target game's outcome
- final injury status cannot leak into earlier prediction snapshots
- corrected PBP unavailable at prediction time cannot be used in live replay before its actual receipt time

---

# 31. Data-readiness gate for model construction

### CBBD retrieval-limit controls

The attached schema explicitly documents response caps:
- `/games` and `/games/teams`: up to **3,000 games** per filtered request
- `/games/players`: up to **1,000 games** per filtered request
- `/lines`: up to **3,000 games** per filtered request

Historical ingestion must therefore use deterministic date-range chunking and verify unique game counts per chunk. A broad request that hits a cap must be treated as potentially truncated, not as complete.

### CBBD-specific coverage measurements required

For every season we intend to use, `COVERAGE_AUDIT.json` must separately measure:

- `/games/players` game coverage and player-box reconciliation
- `/games/teams` game coverage and team-box reconciliation
- PBP game coverage
- `onFloor` array completeness and five-per-team validity
- `wallclock` availability
- structured `shotInfo` coverage and x/y location coverage
- participant-attribution coverage by play type
- substitution coverage and rotation reconciliation
- lineup aggregate availability
- exact stint reconstruction success rate
- roster/player-ID join rate
- recruiting-to-player mapping rate
- transfer-to-player/team mapping rate
- date-bounded stats reproducibility
- line-provider/game coverage
- coverage metadata states/reasons where the overview exposes them

No published percentage may be estimated from documentation. It must be measured from retrieved records.

Before the full predictive architecture is allowed to enter production development, `COVERAGE_AUDIT.json` must report the following for every relevant season/source:

1. row counts
2. unique games
3. unique teams
4. unique players
5. join rates across canonical IDs
6. null rate by required field
7. duplicate rate
8. PBP game coverage
9. PBP score-reconciliation rate
10. on-floor completeness rate
11. substitution completeness/reconciliation rate
12. lineup-stint reconstruction rate
13. shot-detail coverage
14. historical injury-event coverage and timestamp availability **if that dataset is acquired**
15. CBBD opening/stored game-line coverage and provider coverage
16. prospective market-snapshot coverage by checkpoint **for benchmark samples; not a core-model gate**
17. player-prop benchmark coverage **if collected; not a core-model gate**
18. recruiting/transfer coverage
19. point-in-time timestamp completeness
20. source latency distribution for live-capable feeds
21. correction/revision frequency
22. licensing/usage status

**No fabricated coverage values are permitted.** Every percentage in the audit must be measured directly from acquired data.

If a subsystem fails its gate:
- exclude the affected seasons from that subsystem,
- use a documented fallback model,
- or acquire a better source.

Do not lower quality thresholds silently just to keep the schedule moving.

---

# 32. Minimum build-order implied by the data requirements

The data sequence is:

1. canonical games/teams/players/rosters
2. player-game + team-game outcomes
3. PBP + substitutions + on-floor reconstruction
4. lineup stints
5. recruiting/transfers and cold-start priors
6. schedule/rest/context derivations
7. point-in-time feature store
8. coverage audit for the basketball data backbone
9. Sharp Stats current-season availability ingestion in parallel; historical absence-response training comes from CBBD participation/DNP/rotation history
10. CBBD opening/stored game-line benchmark ingestion
11. prospective BDL odds snapshot collector for market benchmarking; Odds API only as fallback
12. only then freeze the detailed model specification and begin final model fitting

Historical player-prop backfilling and dense historical line-movement backfilling are explicitly **not prerequisites** for model construction.

The future live feed contract should be chosen and schema-compatible now, but implementation of the in-play model occurs only after the pregame model is built and validated.

---

# 33. Required next file

The next artifact should be created **only after this v1.1 requirements specification and the CBBD field audit are accepted**:



## `SOURCE_MATRIX.md`

For every raw field/table above, it must answer:

- Do we already have it?
- What exact file/table/endpoint contains it?
- What provider owns it?
- What seasons are available?
- Is it historical, live, or both?
- Does it contain source timestamps?
- Can we reconstruct the information point-in-time?
- What is the observed coverage?
- What is the latency?
- Is this source in the **prediction path** or only the **benchmark path**?
- What is the license/use restriction?
- What is the fallback source?
- Is it PRE, LIVE, or BOTH?
- Is it P0, P1, or P2?

No source should be selected merely because it advertises a field. Coverage, timestamps, historical depth, corrections, stable IDs, and licensing must be verified.

---


# 34. Frozen source architecture

For Data Platform v1.0, source responsibilities are frozen as:

- **CBBD** — P0 basketball backbone
- **Sharp Stats** — P0 current-season availability
- **BDL** — P1 independent QA + prospective market benchmark
- **Bart Torvik** — P1 non-blocking player-strength/DNP enrichment
- **Odds API** — fallback benchmark source only
- **Historical injury archives** — P2
- **Historical player props** — P2/P1-later benchmark only

No further source shopping or architecture change is permitted before implementation unless a P0 coverage/access gate fails.

