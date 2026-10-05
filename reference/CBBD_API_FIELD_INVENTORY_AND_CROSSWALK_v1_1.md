# CBBD API v1.29.0 — Complete Field Inventory and Starter Pack Crosswalk

**Audited specification:** `https://api.collegebasketballdata.com/api/1.29.0/cbbd-openapi.json`  
**OpenAPI version:** 3.0.0  
**CBBD API version:** 1.29.0  
**Audit purpose:** establish exactly what CBBD supplies before finalizing the data requirements for the CBB Originator Interconnected Player Prop Predictive Model.

## 1. Audit method and completeness controls

This audit was performed against the exact versioned OpenAPI document and cross-checked against CBBD's official generated Python client for OpenAPI **1.29.0**. The attached OpenAPI JSON is authoritative; the generated client is used as a normalization aid for inline nested objects.

- **40 HTTP GET operations** enumerated.
- **66 named OpenAPI component schemas** in the attached JSON: 61 object schemas + 5 enum-only schemas.
- **653 direct top-level properties** across the named object schemas.
- **117 generated Python model classes** after inline nested objects are materialized by the client generator.
- **1,404 endpoint-expanded scalar/leaf response fields** after recursively resolving nested objects, arrays, `$ref`, and `allOf`.
- The attached-schema verification produced the same 1,404 endpoint/leaf-field combinations as the prior crosswalk after normalizing array notation.
- Starter Pack comparison is based on the uploaded Starter Pack schema/header file, not inferred from similarly named public datasets.

### Important terminology

- **CBBD direct** means the OpenAPI response contains the field or object.
- **CBBD derivable** means the API provides the primitives needed to construct it, but does not expose that exact canonical field.
- **Starter Pack match** means the uploaded Starter Pack contains an equivalent exported field.
- **External gap** means the required information is not represented by the audited CBBD 1.29.0 OpenAPI and must come from another source or our own timestamped capture.

## 2. Complete endpoint inventory

| API | Operation | Method | Path | Response schema | Filters / required arguments |
|---|---|---|---|---|---|
| ConferencesApi | `get_conference_history` | GET | `/conferences/history` | `ConferenceHistory` | conference? |
| ConferencesApi | `get_conferences` | GET | `/conferences` | `ConferenceInfo` | — |
| DraftApi | `get_draft_picks` | GET | `/draft/picks` | `DraftPick` | year?, draft_team?, source_team?, position? |
| DraftApi | `get_draft_positions` | GET | `/draft/positions` | `DraftPosition` | — |
| DraftApi | `get_draft_teams` | GET | `/draft/teams` | `DraftTeam` | — |
| GamesApi | `get_broadcasts` | GET | `/games/media` | `GameMediaInfo` | start_date_range?, end_date_range?, team?, conference?, season?, season_type?, tournament? |
| GamesApi | `get_game_players` | GET | `/games/players` | `GameBoxScorePlayers` | start_date_range?, end_date_range?, team?, conference?, season?, season_type?, tournament? |
| GamesApi | `get_game_teams` | GET | `/games/teams` | `GameBoxScoreTeam` | start_date_range?, end_date_range?, team?, conference?, season?, season_type?, tournament? |
| GamesApi | `get_games` | GET | `/games` | `GameInfo` | start_date_range?, end_date_range?, team?, conference?, season?, season_type?, status?, tournament? |
| GamesApi | `get_scoreboard` | GET | `/scoreboard` | `ScoreboardGame` | conference? |
| LinesApi | `get_lines` | GET | `/lines` | `GameLines` | season?, team?, conference?, start_date_range?, end_date_range? |
| LinesApi | `get_providers` | GET | `/lines/providers` | `LineProviderInfo` | — |
| LineupsApi | `get_lineup_stats_by_game` | GET | `/lineups/game/{gameId}` | `LineupStats` | game_id |
| LineupsApi | `get_lineups_by_team_season` | GET | `/lineups/team` | `LineupStats` | season, team, start_date_range?, end_date_range? |
| PlaysApi | `get_play_types` | GET | `/plays/types` | `PlayTypeInfo` | — |
| PlaysApi | `get_plays` | GET | `/plays/game/{gameId}` | `PlayInfo` | game_id, shooting_plays_only? |
| PlaysApi | `get_plays_by_date` | GET | `/plays/date` | `PlayInfo` | date, shooting_plays_only?, utc_offset? |
| PlaysApi | `get_plays_by_player_id` | GET | `/plays/player/{playerId}` | `PlayInfo` | player_id, season, shooting_plays_only? |
| PlaysApi | `get_plays_by_team` | GET | `/plays/team` | `PlayInfo` | season, team, shooting_plays_only? |
| PlaysApi | `get_plays_by_tournament` | GET | `/plays/tournament` | `PlayInfo` | tournament, season, shooting_plays_only? |
| PlaysApi | `get_substitutions_by_game` | GET | `/substitutions/game/{gameId}` | `PlayerSubsititution` | game_id |
| PlaysApi | `get_substitutions_by_player_id` | GET | `/substitutions/player/{playerId}` | `PlayerSubsititution` | player_id, season |
| PlaysApi | `get_substitutions_by_team` | GET | `/substitutions/team` | `PlayerSubsititution` | season, team |
| RankingsApi | `get_rankings` | GET | `/rankings` | `PollTeamInfo` | season?, season_type?, week?, poll_type?, team?, conference? |
| RatingsApi | `get_adjusted_efficiency` | GET | `/ratings/adjusted` | `AdjustedEfficiencyInfo` | season?, team?, conference? |
| RatingsApi | `get_elo` | GET | `/ratings/elo` | `TeamElo` | season?, team?, conference? |
| RatingsApi | `get_srs` | GET | `/ratings/srs` | `SrsInfo` | season?, team?, conference? |
| RecruitingApi | `get_portal_transfers` | GET | `/recruiting/portal` | `Transfer` | year?, source_team?, destination_team?, source_conference?, destination_conference?, position? |
| RecruitingApi | `get_recruits` | GET | `/recruiting/players` | `Recruit` | year?, team?, conference?, position? |
| RecruitingApi | `get_team_recruiting_rankings` | GET | `/recruiting/teams` | `TeamRecruitingRanking` | year?, team?, conference? |
| StatsApi | `get_player_season_shooting_stats` | GET | `/stats/player/shooting/season` | `PlayerSeasonShootingStats` | season, season_type?, team?, conference?, start_date_range?, end_date_range? |
| StatsApi | `get_player_season_stats` | GET | `/stats/player/season` | `PlayerSeasonStats` | season, season_type?, team?, conference?, start_date_range?, end_date_range? |
| StatsApi | `get_team_leaderboard_stats` | GET | `/stats/team/leaderboard` | `TeamStatsLeaderboardRecord` | season?, team?, conference? |
| StatsApi | `get_team_season_shooting_stats` | GET | `/stats/team/shooting/season` | `SeasonShootingStats` | season, season_type?, team?, conference?, start_date_range?, end_date_range? |
| StatsApi | `get_team_season_stats` | GET | `/stats/team/season` | `TeamSeasonStats` | season?, season_type?, team?, conference?, start_date_range?, end_date_range? |
| TeamsApi | `get_directory` | GET | `/teams/directory` | `TeamDirectory` | season |
| TeamsApi | `get_overview` | GET | `/teams/{teamId}/season/{season}/overview` | `TeamSeasonOverview` | team_id, season |
| TeamsApi | `get_team_roster` | GET | `/teams/roster` | `TeamRoster` | season, team? |
| TeamsApi | `get_teams` | GET | `/teams` | `TeamInfo` | conference?, season? |
| VenuesApi | `get_venues` | GET | `/venues` | `VenueInfo` | — |

## 3. Point-in-time capability discovered in the audit

The following endpoints accept `startDateRange` / `endDateRange` filters and are therefore especially useful for leakage-safe historical reconstruction:

- `/games`, `/games/players`, `/games/teams`, and `/games/media`
- `/lines`
- `/lineups/team`
- `/stats/player/season`
- `/stats/player/shooting/season`
- `/stats/team/season`
- `/stats/team/shooting/season`

These date filters allow us to rebuild season-to-date box-score, shooting, and lineup aggregates as of a historical prediction cutoff. They do **not** by themselves prove that the source record was available to CBBD at that exact historical moment; our point-in-time policy must still distinguish event time from ingestion/receipt time.

Several useful endpoints do **not** expose date-range parameters, including `/ratings/adjusted`, `/ratings/elo`, `/ratings/srs`, `/stats/team/leaderboard`, and the team-season overview endpoint. For historical backtests, those should not be queried today and treated as if they were known earlier. We should instead compute historical snapshots ourselves from date-bounded raw data or use archived snapshots captured at the time.

## 4. High-value CBBD capabilities for this project

| Model need | CBBD 1.29.0 capability | Assessment |
|---|---|---|
| Historical player box scores | `/games/players` | **Direct, P0** |
| Historical team box scores | `/games/teams` | **Direct, P0** |
| PBP events | `/plays/*` → `PlayInfo` | **Direct, P0** |
| Players on court | `PlayInfo.onFloor[]` | **Direct, P0; coverage must be measured** |
| PBP participants | `PlayInfo.participants[]` | **Direct; roles are not separately typed** |
| Shot result / range | `PlayInfo.shotInfo.made`, `.range` | **Direct** |
| Shooter | `shotInfo.shooter.id/name` | **Direct** |
| Assisted flag / assister | `shotInfo.assisted`, `assistedBy` | **Direct** |
| Shot location | `shotInfo.location.x/y` | **Direct, nullable; coverage audit required** |
| Event wall-clock | `PlayInfo.wallclock` | **Direct, nullable** |
| Current score / clock in PBP | period, clock, secondsRemaining, homeScore, awayScore | **Direct** |
| Historical substitutions | `/substitutions/*` | **Direct, P0** |
| Five-player lineup aggregates | `/lineups/*` | **Direct, P0** |
| Date-bounded lineup aggregates | `/lineups/team` + start/end | **Direct, strong PIT capability** |
| Player season stats | `/stats/player/season` | **Direct + date-bounded** |
| Player shot profiles | `/stats/player/shooting/season` | **Direct + date-bounded** |
| Team season stats | `/stats/team/season` | **Direct + date-bounded** |
| Team shot profiles | `/stats/team/shooting/season` | **Direct + date-bounded** |
| Rosters / player bio | `/teams/roster` | **Direct** |
| Recruiting | `/recruiting/players`, `/recruiting/teams` | **Direct, valuable for cold starts** |
| Transfers | `/recruiting/portal` | **Direct, season-level timing** |
| SRS / Elo / adjusted efficiency | `/ratings/*` | **Direct, but not date-bounded** |
| Game spread/total/ML | `/lines` | **Direct current/final-style values plus opening spread/total** |
| Live-ish scoreboard state | `/scoreboard` | **Direct current status/period/clock/score/betting snapshot** |
| Coverage metadata | `Coverage` in team overview | **Direct, useful for readiness/QA** |
| Venues | `/venues` | **Direct city/state/country; no venue coordinates/timezone/elevation** |

## 5. Important limitations and external gaps found in v1.29.0

### 5.1 No injury / player-availability feed

The audited specification contains no injury endpoint or structured injury-status history. The `Availability` enum in the schema is **coverage availability** used by the team overview, not player health/availability. A timestamped external injury/status source is required for the **automated availability layer and honest pregame status replay**, but it is not required to train the underlying player-performance engine because CBBD already supplies actual participation, minutes, starters, substitutions, lineups and outcomes. Recommended retrieval window: **2021-22 through 2025-26**, with continuous capture beginning in **2026-27**; older history is helpful if easy to obtain but should not delay the build.

### 5.2 No sportsbook player-prop feed

The lines API is game-market oriented. The audited schema exposes provider, spread, over/under, home moneyline, away moneyline, opening spread, and opening over/under. It does not expose player-prop markets. **Historical player-prop odds are not required to train or operate the pure originator.** They are P1 benchmark data only: useful for asking whether the model's probability at a sportsbook's offered line beat the sportsbook's no-vig probability. The basketball targets themselves come from CBBD outcomes.

### 5.3 Game line history is not a timestamped snapshot history

`GameLineInfo` has `spread`, `overUnder`, `homeMoneyline`, `awayMoneyline`, `spreadOpen`, and `overUnderOpen`, but no line-change timestamp, snapshot timestamp, spread-side price, total-side price, or revision history. CBBD is sufficient for **opening-line and stored/final-line context**, subject to verifying whether the stored `spread`/`overUnder` are truly closing values for completed games. Dense historical line movement is **not a build-blocking requirement** for the pure originator. For prospective 2026-27 benchmarking, archive standardized snapshots (recommended: **T-24h, T-6h, T-1h, T-10m**) from the Odds API or equivalent.

### 5.4 PBP has event wall-clock but no vendor receipt/revision metadata

`PlayInfo.wallclock` is valuable, but the schema does not expose provider publication time, our receipt time, revision number, superseded-event ID, or deletion flag. For production live modeling we must record our own `received_at`/`processed_at` timestamps, and a production feed must have explicit correction semantics.

### 5.5 Substitution rows are athlete-centric, not a paired substitution transaction

`PlayerSubsititution` identifies one athlete and contains `subIn` and `subOut` game states. It does not directly provide a single row containing `player_in_id` and `player_out_id`. Canonical paired substitutions and exact stint boundaries therefore remain derived artifacts.

### 5.6 Lineups are aggregate units, not exact stint intervals

`LineupStats` supplies athletes, total seconds, pace, offense/defense/net ratings, and detailed team/opponent unit stats. Exact start/end clocks for every lineup stint are not fields of this response and should be reconstructed from PBP/on-floor/substitution information.

### 5.7 PBP participant roles are not generically typed

`participants[]` contains player `id` and `name`, not a universal actor-role field such as rebounder/stealer/fouler. Shooting plays receive stronger structure through `shotInfo`. Non-shot player attribution may therefore require play-type semantics and validation against `playText`; coverage must be quantified before depending on those derived roles.

### 5.8 Venue geographic detail is incomplete for travel modeling

`VenueInfo` contains ID, source ID, name, city, state, and country. Venue latitude/longitude, elevation, and timezone are not part of that schema. Those are external/derived P1 fields if travel/circadian/altitude features prove useful.

### 5.9 No coach or referee data in the audited API

Coach history, referee assignments, and official tendencies remain external P1/P2 candidates and must not block v1.

## 6. Starter Pack crosswalk — table level

| Starter Pack export | Closest CBBD API schema/endpoint | Relationship |
|---|---|---|
| `conferences.csv` | `ConferenceInfo` / `/conferences` | Direct subset/near-match; API additionally exposes `sourceId` and `shortName`. |
| `teams.csv` | `TeamInfo` / `/teams` | Direct core identity overlap; API adds source ID, current venue and conference context. |
| `games.csv` | `GameInfo` / `/games` | Strong overlap; API adds status, attendance, Elo start/end, excitement, source ID. Starter export includes `venue_country`, obtainable via venue join rather than directly from `GameInfo`. |
| `player_game_stats/*.csv` | `GameBoxScorePlayers` / `/games/players` | Strong overlap; API adds `athleteSourceId`, `seasonLabel`, start-time-TBD, tournament/notes. |
| `player_season_stats/*.csv` | `PlayerSeasonStats` / `/stats/player/season` | Strong overlap; API additionally exposes `athleteSourceId`. API can be date-bounded for PIT snapshots. |
| `player_season_shooting_stats/*.csv` | `PlayerSeasonShootingStats` | Strong overlap; API also includes free-throw rate and free-throw breakdown object. Date-bounded. |
| `team_game_stats/*.csv` | `GameBoxScoreTeam` / `/games/teams` | Strong overlap; nested API objects correspond to flattened Starter fields. |
| `team_season_stats/*.csv` | `TeamSeasonStats` / `/stats/team/season` | Strong overlap and date-bounded in API. |
| `team_season_shooting_stats/*.csv` | `SeasonShootingStats` | Strong overlap; API adds free-throw details. Date-bounded. |

### API-only families not present as Starter Pack tables

- play-by-play, `onFloor`, participants, shot coordinates and event wall-clock
- substitutions
- lineup aggregates
- current scoreboard
- game betting lines/providers
- rosters and richer player bio/hometown data
- recruiting and transfer portal data
- rankings/polls
- SRS, Elo and adjusted efficiency
- team overview/coverage metadata
- broadcasts
- draft data
- venue directory

## 7. Field-level Starter Pack crosswalk summary
- Endpoint-expanded leaf fields classified: **1,404**
- `STARTER_PACK_MATCH`: **386** endpoint-field occurrences
- `API_FIELD_NOT_IN_STARTER_EXPORT`: **32** endpoint-field occurrences
- `API_ONLY_NO_STARTER_TABLE`: **986** endpoint-field occurrences

The complete row-level crosswalk is delivered separately as `CBBD_ENDPOINT_FIELD_CROSSWALK.csv`. It preserves endpoint, response model, nested field path, type, nullable flag, matching Starter table/field when one exists, and crosswalk status.

## 8. Generated-client normalized schema field inventory

The following section lists every field or enum value across the **117 generated v1.29.0 Python model classes**. These 117 classes are **not** the same thing as the 66 named component schemas in the attached OpenAPI; inline nested objects are promoted into additional client classes. For the exact named-component inventory from the uploaded JSON, use `CBBD_OPENAPI_VERIFIED_COMPONENT_INVENTORY.csv`. `Nullable = yes` means the field may contain a null value; that is distinct from whether the property key is required to be present in the OpenAPI payload.

### `AdjustedEfficiencyInfo`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `StrictStr` | no |
| `offensiveRating` | `Union[StrictFloat, StrictInt]` | no |
| `defensiveRating` | `Union[StrictFloat, StrictInt]` | no |
| `netRating` | `Union[StrictFloat, StrictInt]` | no |
| `rankings` | `AdjustedEfficiencyInfoRankings` | no |

### `AdjustedEfficiencyInfoRankings`

| Field | Type | Nullable |
|---|---|---|
| `net` | `Union[StrictFloat, StrictInt]` | no |
| `defense` | `Union[StrictFloat, StrictInt]` | no |
| `offense` | `Union[StrictFloat, StrictInt]` | no |

### `AdjustedRatings`

| Field | Type | Nullable |
|---|---|---|
| `offense` | `RatedValue` | no |
| `defense` | `RatedValue` | no |
| `net` | `RatedValue` | no |
| `population` | `Union[StrictFloat, StrictInt]` | no |

### `Availability`
Enum values: `available`, `partial`, `unavailable`

### `Conference`

| Field | Type | Nullable |
|---|---|---|
| `id` | `Union[StrictFloat, StrictInt]` | no |
| `name` | `StrictStr` | no |
| `abbreviation` | `StrictStr` | no |

### `ConferenceHistory`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `abbreviation` | `StrictStr` | no |
| `shortName` | `StrictStr` | no |
| `teams` | `conlist(ConferenceHistoryTeamsInner)` | no |

### `ConferenceHistoryTeamsInner`

| Field | Type | Nullable |
|---|---|---|
| `endSeason` | `Optional[StrictInt]` | yes |
| `startSeason` | `StrictInt` | no |
| `mascot` | `Optional[StrictStr]` | yes |
| `school` | `StrictStr` | no |
| `sourceId` | `StrictStr` | no |
| `id` | `StrictInt` | no |

### `ConferenceInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `abbreviation` | `StrictStr` | no |
| `shortName` | `StrictStr` | no |

### `CountRecord`

| Field | Type | Nullable |
|---|---|---|
| `games` | `Union[StrictFloat, StrictInt]` | no |
| `wins` | `Union[StrictFloat, StrictInt]` | no |
| `losses` | `Union[StrictFloat, StrictInt]` | no |
| `unresolved` | `Union[StrictFloat, StrictInt]` | no |

### `Coverage`

| Field | Type | Nullable |
|---|---|---|
| `state` | `Availability` | no |
| `reason` | `Optional[Reason]` | yes |
| `eligibleGames` | `Union[StrictFloat, StrictInt]` | no |
| `coveredGames` | `Union[StrictFloat, StrictInt]` | no |

### `DirectoryTeam`

| Field | Type | Nullable |
|---|---|---|
| `id` | `Union[StrictFloat, StrictInt]` | no |
| `sourceId` | `Optional[StrictStr]` | yes |
| `school` | `StrictStr` | no |
| `mascot` | `Optional[StrictStr]` | yes |
| `abbreviation` | `Optional[StrictStr]` | yes |
| `displayName` | `Optional[StrictStr]` | yes |
| `shortDisplayName` | `Optional[StrictStr]` | yes |
| `conferenceId` | `Union[StrictFloat, StrictInt]` | no |

### `DraftPick`

| Field | Type | Nullable |
|---|---|---|
| `athleteId` | `Optional[StrictInt]` | yes |
| `sourceTeamId` | `Optional[StrictInt]` | yes |
| `sourceTeamLocation` | `Optional[StrictStr]` | yes |
| `sourceTeamName` | `Optional[StrictStr]` | yes |
| `sourceTeamLeagueAffiliation` | `Optional[StrictStr]` | yes |
| `sourceTeamCollegeId` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `draftTeamId` | `Union[StrictFloat, StrictInt]` | no |
| `draftTeam` | `StrictStr` | no |
| `year` | `StrictInt` | no |
| `overall` | `StrictInt` | no |
| `round` | `StrictInt` | no |
| `pick` | `StrictInt` | no |
| `name` | `StrictStr` | no |
| `overallRank` | `Optional[StrictInt]` | yes |
| `positionRank` | `Optional[StrictInt]` | yes |
| `height` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `weight` | `Optional[StrictInt]` | yes |

### `DraftPosition`

| Field | Type | Nullable |
|---|---|---|
| `name` | `StrictStr` | no |
| `abbreviation` | `StrictStr` | no |

### `DraftTeam`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `Union[StrictFloat, StrictInt]` | no |
| `location` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `displayName` | `StrictStr` | no |
| `abbreviation` | `StrictStr` | no |

### `GameBoxScorePlayers`

| Field | Type | Nullable |
|---|---|---|
| `gameId` | `StrictInt` | no |
| `season` | `StrictInt` | no |
| `seasonLabel` | `StrictStr` | no |
| `seasonType` | `SeasonType` | no |
| `tournament` | `Optional[StrictStr]` | yes |
| `startDate` | `datetime` | no |
| `startTimeTbd` | `StrictBool` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `teamSeed` | `Optional[StrictInt]` | yes |
| `opponentId` | `StrictInt` | no |
| `opponent` | `StrictStr` | no |
| `opponentConference` | `Optional[StrictStr]` | yes |
| `opponentSeed` | `Optional[StrictInt]` | yes |
| `neutralSite` | `StrictBool` | no |
| `isHome` | `StrictBool` | no |
| `conferenceGame` | `StrictBool` | no |
| `gameType` | `Optional[StrictStr]` | yes |
| `notes` | `Optional[StrictStr]` | yes |
| `gameMinutes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `gamePace` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `players` | `conlist(GameBoxScorePlayersPlayersInner)` | no |

### `GameBoxScorePlayersPlayersInner`

| Field | Type | Nullable |
|---|---|---|
| `rebounds` | `TeamBoxScoreRebounds` | no |
| `freeThrows` | `TeamSeasonUnitStatsFieldGoals` | no |
| `threePointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `twoPointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `fieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `offensiveReboundPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `freeThrowRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assistsTurnoverRatio` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `gameScore` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShootingPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `effectiveFieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `netRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `usage` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `blocks` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `steals` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fouls` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `points` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `minutes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `ejected` | `Optional[StrictBool]` | yes |
| `starter` | `Optional[StrictBool]` | yes |
| `position` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `athleteSourceId` | `StrictStr` | no |
| `athleteId` | `StrictInt` | no |

### `GameBoxScoreTeam`

| Field | Type | Nullable |
|---|---|---|
| `gameId` | `StrictInt` | no |
| `season` | `StrictInt` | no |
| `seasonLabel` | `StrictStr` | no |
| `seasonType` | `SeasonType` | no |
| `tournament` | `Optional[StrictStr]` | yes |
| `startDate` | `datetime` | no |
| `startTimeTbd` | `StrictBool` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `teamSeed` | `Optional[StrictInt]` | yes |
| `opponentId` | `StrictInt` | no |
| `opponent` | `StrictStr` | no |
| `opponentConference` | `Optional[StrictStr]` | yes |
| `opponentSeed` | `Optional[StrictInt]` | yes |
| `neutralSite` | `StrictBool` | no |
| `isHome` | `StrictBool` | no |
| `conferenceGame` | `StrictBool` | no |
| `gameType` | `Optional[StrictStr]` | yes |
| `notes` | `Optional[StrictStr]` | yes |
| `gameMinutes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pace` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `teamStats` | `GameBoxScoreTeamStats` | no |
| `opponentStats` | `GameBoxScoreTeamStats` | no |

### `GameBoxScoreTeamStats`

| Field | Type | Nullable |
|---|---|---|
| `fieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `twoPointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `threePointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `freeThrows` | `TeamSeasonUnitStatsFieldGoals` | no |
| `rebounds` | `TeamBoxScoreRebounds` | no |
| `turnovers` | `TeamSeasonUnitStatsTurnovers` | no |
| `fouls` | `TeamSeasonUnitStatsFouls` | no |
| `points` | `GameBoxScoreTeamStatsPoints` | no |
| `fourFactors` | `TeamSeasonUnitStatsFourFactors` | no |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `blocks` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `steals` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `possessions` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShooting` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `gameScore` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `GameBoxScoreTeamStatsPoints`

| Field | Type | Nullable |
|---|---|---|
| `fastBreak` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offTurnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `inPaint` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `byPeriod` | `Optional[conlist(Union[StrictFloat, StrictInt])]` | yes |
| `largestLead` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `GameInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `seasonLabel` | `StrictStr` | no |
| `season` | `StrictInt` | no |
| `seasonType` | `SeasonType` | no |
| `startDate` | `datetime` | no |
| `startTimeTbd` | `StrictBool` | no |
| `neutralSite` | `StrictBool` | no |
| `conferenceGame` | `StrictBool` | no |
| `gameType` | `Optional[StrictStr]` | yes |
| `tournament` | `Optional[StrictStr]` | yes |
| `gameNotes` | `Optional[StrictStr]` | yes |
| `status` | `GameStatus` | no |
| `attendance` | `Optional[StrictInt]` | yes |
| `homeTeamId` | `StrictInt` | no |
| `homeTeam` | `StrictStr` | no |
| `homeConferenceId` | `Optional[StrictInt]` | yes |
| `homeConference` | `Optional[StrictStr]` | yes |
| `homeSeed` | `Optional[StrictInt]` | yes |
| `homePoints` | `Optional[StrictInt]` | yes |
| `homePeriodPoints` | `Optional[conlist(StrictInt)]` | yes |
| `homeWinner` | `Optional[StrictBool]` | yes |
| `homeTeamEloStart` | `Optional[StrictInt]` | yes |
| `homeTeamEloEnd` | `Optional[StrictInt]` | yes |
| `awayTeamId` | `StrictInt` | no |
| `awayTeam` | `StrictStr` | no |
| `awayConferenceId` | `Optional[StrictInt]` | yes |
| `awayConference` | `Optional[StrictStr]` | yes |
| `awaySeed` | `Optional[StrictInt]` | yes |
| `awayPoints` | `Optional[StrictInt]` | yes |
| `awayPeriodPoints` | `Optional[conlist(StrictInt)]` | yes |
| `awayWinner` | `Optional[StrictBool]` | yes |
| `awayTeamEloStart` | `Optional[StrictInt]` | yes |
| `awayTeamEloEnd` | `Optional[StrictInt]` | yes |
| `excitement` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `venueId` | `Optional[StrictInt]` | yes |
| `venue` | `Optional[StrictStr]` | yes |
| `city` | `Optional[StrictStr]` | yes |
| `state` | `Optional[StrictStr]` | yes |

### `GameLineInfo`

| Field | Type | Nullable |
|---|---|---|
| `provider` | `StrictStr` | no |
| `spread` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `overUnder` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `homeMoneyline` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `awayMoneyline` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `spreadOpen` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `overUnderOpen` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `GameLines`

| Field | Type | Nullable |
|---|---|---|
| `gameId` | `StrictInt` | no |
| `season` | `StrictInt` | no |
| `seasonType` | `SeasonType` | no |
| `startDate` | `datetime` | no |
| `homeTeamId` | `StrictInt` | no |
| `homeTeam` | `StrictStr` | no |
| `homeConference` | `Optional[StrictStr]` | yes |
| `homeScore` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `awayTeamId` | `StrictInt` | no |
| `awayTeam` | `StrictStr` | no |
| `awayConference` | `Optional[StrictStr]` | yes |
| `awayScore` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `lines` | `conlist(GameLineInfo)` | no |

### `GameMediaInfo`

| Field | Type | Nullable |
|---|---|---|
| `gameId` | `StrictInt` | no |
| `season` | `StrictInt` | no |
| `seasonLabel` | `StrictStr` | no |
| `seasonType` | `SeasonType` | no |
| `tournament` | `Optional[StrictStr]` | yes |
| `startDate` | `datetime` | no |
| `startTimeTbd` | `StrictBool` | no |
| `homeTeamId` | `StrictInt` | no |
| `homeTeam` | `StrictStr` | no |
| `homeConference` | `Optional[StrictStr]` | yes |
| `awayTeamId` | `StrictInt` | no |
| `awayTeam` | `StrictStr` | no |
| `awayConference` | `Optional[StrictStr]` | yes |
| `neutralSite` | `StrictBool` | no |
| `conferenceGame` | `StrictBool` | no |
| `gameType` | `Optional[StrictStr]` | yes |
| `notes` | `Optional[StrictStr]` | yes |
| `broadcasts` | `conlist(GameMediaInfoBroadcastsInner)` | no |

### `GameMediaInfoBroadcastsInner`

| Field | Type | Nullable |
|---|---|---|
| `broadcastName` | `StrictStr` | no |
| `broadcastType` | `StrictStr` | no |

### `GameStatus`
Enum values: `scheduled`, `in_progress`, `final`, `postponed`, `cancelled`

### `LineProviderInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `name` | `StrictStr` | no |

### `LineupStats`

| Field | Type | Nullable |
|---|---|---|
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `StrictStr` | no |
| `idHash` | `StrictStr` | no |
| `athletes` | `conlist(PlayInfoParticipantsInner)` | no |
| `totalSeconds` | `Union[StrictFloat, StrictInt]` | no |
| `pace` | `Union[StrictFloat, StrictInt]` | no |
| `offenseRating` | `Union[StrictFloat, StrictInt]` | no |
| `defenseRating` | `Union[StrictFloat, StrictInt]` | no |
| `netRating` | `Union[StrictFloat, StrictInt]` | no |
| `teamStats` | `LineupUnitStats` | no |
| `opponentStats` | `LineupUnitStats` | no |

### `LineupUnitStats`

| Field | Type | Nullable |
|---|---|---|
| `possessions` | `StrictInt` | no |
| `points` | `StrictInt` | no |
| `blocks` | `StrictInt` | no |
| `assists` | `StrictInt` | no |
| `steals` | `StrictInt` | no |
| `turnovers` | `StrictInt` | no |
| `defensiveRebounds` | `StrictInt` | no |
| `offensiveRebounds` | `StrictInt` | no |
| `trueShooting` | `Union[StrictFloat, StrictInt]` | no |
| `fieldGoals` | `ShootingStats` | no |
| `twoPointers` | `LineupUnitStatsTwoPointers` | no |
| `threePointers` | `ShootingStats` | no |
| `freeThrows` | `ShootingStats` | no |
| `fourFactors` | `LineupUnitStatsFourFactors` | no |

### `LineupUnitStatsFourFactors`

| Field | Type | Nullable |
|---|---|---|
| `freeThrowRate` | `Union[StrictFloat, StrictInt]` | no |
| `offensiveReboundPct` | `Union[StrictFloat, StrictInt]` | no |
| `turnoverRatio` | `Union[StrictFloat, StrictInt]` | no |
| `effectiveFieldGoalPct` | `Union[StrictFloat, StrictInt]` | no |

### `LineupUnitStatsTwoPointers`

| Field | Type | Nullable |
|---|---|---|
| `made` | `StrictInt` | no |
| `attempted` | `StrictInt` | no |
| `pct` | `Union[StrictFloat, StrictInt]` | no |
| `jumpers` | `ShootingStats` | no |
| `layups` | `ShootingStats` | no |
| `dunks` | `ShootingStats` | no |
| `tipIns` | `ShootingStats` | no |

### `PlayInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `gameId` | `StrictInt` | no |
| `gameSourceId` | `StrictStr` | no |
| `gameStartDate` | `datetime` | no |
| `season` | `Union[StrictFloat, StrictInt]` | no |
| `seasonType` | `SeasonType` | no |
| `gameType` | `StrictStr` | no |
| `tournament` | `Optional[StrictStr]` | yes |
| `playType` | `StrictStr` | no |
| `isHomeTeam` | `Optional[StrictBool]` | yes |
| `teamId` | `Optional[StrictInt]` | yes |
| `team` | `Optional[StrictStr]` | yes |
| `conference` | `Optional[StrictStr]` | yes |
| `teamSeed` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `opponentId` | `Optional[StrictInt]` | yes |
| `opponent` | `Optional[StrictStr]` | yes |
| `opponentConference` | `Optional[StrictStr]` | yes |
| `opponentSeed` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `period` | `StrictInt` | no |
| `clock` | `StrictStr` | no |
| `secondsRemaining` | `StrictInt` | no |
| `homeScore` | `StrictInt` | no |
| `awayScore` | `StrictInt` | no |
| `homeWinProbability` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `scoringPlay` | `Optional[StrictBool]` | yes |
| `shootingPlay` | `Optional[StrictBool]` | yes |
| `scoreValue` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `wallclock` | `Optional[datetime]` | yes |
| `playText` | `Optional[StrictStr]` | yes |
| `participants` | `conlist(PlayInfoParticipantsInner)` | no |
| `onFloor` | `conlist(PlayInfoOnFloorInner)` | no |
| `shotInfo` | `Optional[ShotInfo]` | yes |

### `PlayInfoOnFloorInner`

| Field | Type | Nullable |
|---|---|---|
| `team` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `id` | `StrictInt` | no |

### `PlayInfoParticipantsInner`

| Field | Type | Nullable |
|---|---|---|
| `name` | `StrictStr` | no |
| `id` | `StrictInt` | no |

### `PlayTypeInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `name` | `StrictStr` | no |

### `PlayerSeasonDetails`

| Field | Type | Nullable |
|---|---|---|
| `fieldGoals` | `ShootingLine` | no |
| `twoPointFieldGoals` | `ShootingLine` | no |
| `threePointFieldGoals` | `ShootingLine` | no |
| `freeThrows` | `ShootingLine` | no |
| `rebounds` | `TeamBoxScoreRebounds` | no |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `steals` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `blocks` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fouls` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `starts` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assistTurnoverRatio` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `freeThrowRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveReboundPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `advanced` | `PlayerSeasonDetailsAdvanced` | no |

### `PlayerSeasonDetailsAdvanced`

| Field | Type | Nullable |
|---|---|---|
| `winShares` | `PlayerSeasonDetailsAdvancedWinShares` | no |
| `porpag` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `netRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `games` | `Union[StrictFloat, StrictInt]` | no |

### `PlayerSeasonDetailsAdvancedWinShares`

| Field | Type | Nullable |
|---|---|---|
| `per40` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `PlayerSeasonShootingStats`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `trackedShots` | `StrictInt` | no |
| `assistedPct` | `Union[StrictFloat, StrictInt]` | no |
| `freeThrowRate` | `Union[StrictFloat, StrictInt]` | no |
| `dunks` | `SeasonShootingStatsDunks` | no |
| `layups` | `SeasonShootingStatsDunks` | no |
| `tipIns` | `ShotTypeBreakdown` | no |
| `twoPointJumpers` | `SeasonShootingStatsDunks` | no |
| `threePointJumpers` | `SeasonShootingStatsDunks` | no |
| `freeThrows` | `ShotTypeBreakdown` | no |
| `attemptsBreakdown` | `SeasonShootingStatsAttemptsBreakdown` | no |
| `athleteId` | `StrictInt` | no |
| `athleteName` | `StrictStr` | no |

### `PlayerSeasonStats`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `seasonLabel` | `StrictStr` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `athleteId` | `StrictInt` | no |
| `athleteSourceId` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `position` | `StrictStr` | no |
| `games` | `Union[StrictFloat, StrictInt]` | no |
| `starts` | `Union[StrictFloat, StrictInt]` | no |
| `minutes` | `Union[StrictFloat, StrictInt]` | no |
| `points` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fouls` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `steals` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `blocks` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `usage` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `netRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `PORPAG` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `effectiveFieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShootingPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assistsTurnoverRatio` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `freeThrowRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveReboundPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `twoPointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `threePointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `freeThrows` | `TeamSeasonUnitStatsFieldGoals` | no |
| `rebounds` | `TeamBoxScoreRebounds` | no |
| `winShares` | `PlayerSeasonStatsWinShares` | no |

### `PlayerSeasonStatsWinShares`

| Field | Type | Nullable |
|---|---|---|
| `totalPer40` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `PlayerSubsititution`

| Field | Type | Nullable |
|---|---|---|
| `gameId` | `StrictInt` | no |
| `startDate` | `datetime` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `athleteId` | `StrictInt` | no |
| `athlete` | `StrictStr` | no |
| `position` | `Optional[StrictStr]` | yes |
| `opponentId` | `StrictInt` | no |
| `opponent` | `StrictStr` | no |
| `opponentConference` | `Optional[StrictStr]` | yes |
| `subIn` | `PlayerSubsititutionSubIn` | no |
| `subOut` | `PlayerSubsititutionSubIn` | no |

### `PlayerSubsititutionSubIn`

| Field | Type | Nullable |
|---|---|---|
| `opponentPoints` | `StrictInt` | no |
| `teamPoints` | `StrictInt` | no |
| `secondsRemaining` | `StrictInt` | no |
| `period` | `StrictInt` | no |

### `PollStanding`

| Field | Type | Nullable |
|---|---|---|
| `state` | `StrictStr` | no |
| `date` | `Optional[StrictStr]` | yes |
| `week` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `seasonType` | `Optional[StrictStr]` | yes |
| `rank` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `PollTeamInfo`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `seasonType` | `SeasonType` | no |
| `week` | `StrictInt` | no |
| `pollDate` | `Optional[datetime]` | yes |
| `pollType` | `StrictStr` | no |
| `teamId` | `Union[StrictFloat, StrictInt]` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `ranking` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `points` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `firstPlaceVotes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `ProfileGame`

| Field | Type | Nullable |
|---|---|---|
| `id` | `Union[StrictFloat, StrictInt]` | no |
| `opponentId` | `Union[StrictFloat, StrictInt]` | no |
| `opponent` | `StrictStr` | no |
| `opponentHasProfile` | `StrictBool` | no |
| `startDate` | `StrictStr` | no |
| `calendarDate` | `StrictStr` | no |
| `startTimeTbd` | `StrictBool` | no |
| `location` | `StrictStr` | no |
| `status` | `StrictStr` | no |
| `seasonType` | `StrictStr` | no |
| `gameType` | `Optional[StrictStr]` | yes |
| `eligibility` | `StrictStr` | no |
| `conferenceGame` | `Optional[StrictBool]` | yes |
| `teamPoints` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `opponentPoints` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `result` | `Optional[StrictStr]` | yes |
| `venue` | `Optional[StrictStr]` | yes |

### `ProfilePlayer`

| Field | Type | Nullable |
|---|---|---|
| `seasonStats` | `Optional[PlayerSeasonDetails]` | yes |
| `athleteId` | `Union[StrictFloat, StrictInt]` | no |
| `name` | `StrictStr` | no |
| `position` | `Optional[StrictStr]` | yes |
| `onRoster` | `StrictBool` | no |
| `hasStats` | `StrictBool` | no |
| `games` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `minutes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `points` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rebounds` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `minutesPerGame` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pointsPerGame` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `usagePct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShootingPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `effectiveFieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `usageGames` | `Union[StrictFloat, StrictInt]` | no |
| `complete` | `StrictBool` | no |

### `RatedValue`

| Field | Type | Nullable |
|---|---|---|
| `value` | `Union[StrictFloat, StrictInt]` | no |
| `rank` | `Union[StrictFloat, StrictInt]` | no |

### `Reason`
Enum values: `no_games`, `no_source_rows`, `incomplete_inputs`, `zero_denominator`, `unclassified_games`, `unclassified_shots`

### `Recruit`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `Optional[StrictStr]` | yes |
| `position` | `Optional[StrictStr]` | yes |
| `schoolId` | `Optional[StrictInt]` | yes |
| `school` | `Optional[StrictStr]` | yes |
| `hometown` | `Optional[RecruitHometown]` | yes |
| `committedTo` | `Optional[RecruitCommittedTo]` | yes |
| `athleteId` | `Optional[StrictInt]` | yes |
| `year` | `StrictInt` | no |
| `name` | `StrictStr` | no |
| `heightInches` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `weightPounds` | `Optional[StrictInt]` | yes |
| `stars` | `StrictInt` | no |
| `rating` | `Union[StrictFloat, StrictInt]` | no |
| `ranking` | `Optional[StrictInt]` | yes |

### `RecruitCommittedTo`

| Field | Type | Nullable |
|---|---|---|
| `conference` | `Optional[StrictStr]` | yes |
| `name` | `Optional[StrictStr]` | yes |
| `id` | `Optional[StrictInt]` | yes |

### `RecruitHometown`

| Field | Type | Nullable |
|---|---|---|
| `countyFips` | `Optional[StrictStr]` | yes |
| `longitude` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `latitude` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `country` | `Optional[StrictStr]` | yes |
| `state` | `Optional[StrictStr]` | yes |
| `city` | `Optional[StrictStr]` | yes |

### `ScoreboardGame`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `startDate` | `datetime` | no |
| `startTimeTbd` | `StrictBool` | no |
| `tv` | `Optional[StrictStr]` | yes |
| `neutralSite` | `StrictBool` | no |
| `conferenceGame` | `StrictBool` | no |
| `status` | `GameStatus` | no |
| `period` | `Optional[StrictInt]` | yes |
| `clock` | `Optional[StrictStr]` | yes |
| `venue` | `Optional[StrictStr]` | yes |
| `city` | `Optional[StrictStr]` | yes |
| `state` | `Optional[StrictStr]` | yes |
| `homeTeam` | `ScoreboardGameHomeTeam` | no |
| `awayTeam` | `ScoreboardGameHomeTeam` | no |
| `betting` | `ScoreboardGameBetting` | no |

### `ScoreboardGameBetting`

| Field | Type | Nullable |
|---|---|---|
| `awayMoneyline` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `homeMoneyline` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `overUnder` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `spread` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `ScoreboardGameHomeTeam`

| Field | Type | Nullable |
|---|---|---|
| `lineScores` | `Optional[conlist(StrictInt)]` | yes |
| `points` | `Optional[StrictInt]` | yes |
| `conferenceAbbreviation` | `Optional[StrictStr]` | yes |
| `conference` | `Optional[StrictStr]` | yes |
| `location` | `Optional[StrictStr]` | yes |
| `name` | `StrictStr` | no |
| `id` | `StrictInt` | no |

### `SeasonShootingStats`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `trackedShots` | `StrictInt` | no |
| `assistedPct` | `Union[StrictFloat, StrictInt]` | no |
| `freeThrowRate` | `Union[StrictFloat, StrictInt]` | no |
| `dunks` | `SeasonShootingStatsDunks` | no |
| `layups` | `SeasonShootingStatsDunks` | no |
| `tipIns` | `ShotTypeBreakdown` | no |
| `twoPointJumpers` | `SeasonShootingStatsDunks` | no |
| `threePointJumpers` | `SeasonShootingStatsDunks` | no |
| `freeThrows` | `ShotTypeBreakdown` | no |
| `attemptsBreakdown` | `SeasonShootingStatsAttemptsBreakdown` | no |

### `SeasonShootingStatsAttemptsBreakdown`

| Field | Type | Nullable |
|---|---|---|
| `threePointJumpers` | `Union[StrictFloat, StrictInt]` | no |
| `twoPointJumpers` | `Union[StrictFloat, StrictInt]` | no |
| `tipIns` | `Union[StrictFloat, StrictInt]` | no |
| `layups` | `Union[StrictFloat, StrictInt]` | no |
| `dunks` | `Union[StrictFloat, StrictInt]` | no |

### `SeasonShootingStatsDunks`

| Field | Type | Nullable |
|---|---|---|
| `made` | `StrictInt` | no |
| `attempted` | `StrictInt` | no |
| `pct` | `Union[StrictFloat, StrictInt]` | no |
| `assistedPct` | `Union[StrictFloat, StrictInt]` | no |
| `assisted` | `StrictInt` | no |

### `SeasonType`
Enum values: `postseason`, `preseason`, `regular`

### `ShootingLine`

| Field | Type | Nullable |
|---|---|---|
| `made` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `attempted` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `ShootingStats`

| Field | Type | Nullable |
|---|---|---|
| `made` | `StrictInt` | no |
| `attempted` | `StrictInt` | no |
| `pct` | `Union[StrictFloat, StrictInt]` | no |

### `ShotBucket`

| Field | Type | Nullable |
|---|---|---|
| `key` | `StrictStr` | no |
| `attempts` | `Union[StrictFloat, StrictInt]` | no |
| `made` | `Union[StrictFloat, StrictInt]` | no |
| `attemptPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `ShotInfo`

| Field | Type | Nullable |
|---|---|---|
| `shooter` | `ShotInfoShooter` | no |
| `made` | `StrictBool` | no |
| `range` | `StrictStr` | no |
| `assisted` | `StrictBool` | no |
| `assistedBy` | `ShotInfoShooter` | no |
| `location` | `ShotInfoLocation` | no |

### `ShotInfoLocation`

| Field | Type | Nullable |
|---|---|---|
| `y` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `x` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `ShotInfoShooter`

| Field | Type | Nullable |
|---|---|---|
| `name` | `Optional[StrictStr]` | yes |
| `id` | `Optional[StrictInt]` | yes |

### `ShotTypeBreakdown`

| Field | Type | Nullable |
|---|---|---|
| `made` | `StrictInt` | no |
| `attempted` | `StrictInt` | no |
| `pct` | `Union[StrictFloat, StrictInt]` | no |

### `SrsInfo`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `StrictStr` | no |
| `rating` | `Union[StrictFloat, StrictInt]` | no |

### `TeamBoxScore`

| Field | Type | Nullable |
|---|---|---|
| `fieldGoals` | `ShootingLine` | no |
| `twoPointFieldGoals` | `ShootingLine` | no |
| `threePointFieldGoals` | `ShootingLine` | no |
| `freeThrows` | `ShootingLine` | no |
| `rebounds` | `TeamBoxScoreRebounds` | no |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `steals` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `blocks` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fouls` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `minutes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `teamTurnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `technicalFouls` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `flagrantFouls` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pointsInPaint` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pointsOffTurnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fastBreakPoints` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShootingPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamBoxScoreRebounds`

| Field | Type | Nullable |
|---|---|---|
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamDirectory`

| Field | Type | Nullable |
|---|---|---|
| `season` | `Union[StrictFloat, StrictInt]` | no |
| `seasonLabel` | `StrictStr` | no |
| `teams` | `conlist(DirectoryTeam)` | no |
| `conferences` | `conlist(Conference)` | no |

### `TeamElo`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `StrictStr` | no |
| `elo` | `Optional[StrictInt]` | yes |

### `TeamInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `school` | `StrictStr` | no |
| `mascot` | `Optional[StrictStr]` | yes |
| `abbreviation` | `Optional[StrictStr]` | yes |
| `displayName` | `Optional[StrictStr]` | yes |
| `shortDisplayName` | `Optional[StrictStr]` | yes |
| `primaryColor` | `Optional[StrictStr]` | yes |
| `secondaryColor` | `Optional[StrictStr]` | yes |
| `currentVenueId` | `Optional[StrictInt]` | yes |
| `currentVenue` | `Optional[StrictStr]` | yes |
| `currentCity` | `Optional[StrictStr]` | yes |
| `currentState` | `Optional[StrictStr]` | yes |
| `conferenceId` | `Optional[StrictInt]` | yes |
| `conference` | `Optional[StrictStr]` | yes |

### `TeamLeaderboardAdjustedEfficiency`

| Field | Type | Nullable |
|---|---|---|
| `offensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `netRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rankings` | `TeamLeaderboardAdjustedEfficiencyRankings` | no |

### `TeamLeaderboardAdjustedEfficiencyRankings`

| Field | Type | Nullable |
|---|---|---|
| `net` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defense` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offense` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamRecruitingRanking`

| Field | Type | Nullable |
|---|---|---|
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `year` | `StrictInt` | no |
| `ranking` | `StrictInt` | no |
| `rating` | `Union[StrictFloat, StrictInt]` | no |

### `TeamRoster`

| Field | Type | Nullable |
|---|---|---|
| `teamId` | `StrictInt` | no |
| `teamSourceId` | `StrictStr` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `season` | `Union[StrictFloat, StrictInt]` | no |
| `players` | `conlist(TeamRosterPlayer)` | no |

### `TeamRosterPlayer`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `name` | `StrictStr` | no |
| `firstName` | `Optional[StrictStr]` | yes |
| `lastName` | `Optional[StrictStr]` | yes |
| `jersey` | `Optional[StrictStr]` | yes |
| `position` | `Optional[StrictStr]` | yes |
| `height` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `weight` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `hometown` | `TeamRosterPlayerHometown` | no |
| `dateOfBirth` | `Optional[date]` | yes |
| `startSeason` | `Union[StrictFloat, StrictInt]` | no |
| `endSeason` | `Union[StrictFloat, StrictInt]` | no |

### `TeamRosterPlayerHometown`

| Field | Type | Nullable |
|---|---|---|
| `countyFips` | `Optional[StrictStr]` | yes |
| `longitude` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `latitude` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `country` | `Optional[StrictStr]` | yes |
| `state` | `Optional[StrictStr]` | yes |
| `city` | `Optional[StrictStr]` | yes |

### `TeamSeasonOverview`

| Field | Type | Nullable |
|---|---|---|
| `formatVersion` | `Union[StrictFloat, StrictInt]` | no |
| `teamId` | `Union[StrictFloat, StrictInt]` | no |
| `season` | `Union[StrictFloat, StrictInt]` | no |
| `seasonLabel` | `StrictStr` | no |
| `generatedAt` | `StrictStr` | no |
| `team` | `TeamSeasonOverviewTeam` | no |
| `record` | `TeamSeasonOverviewRecord` | no |
| `ratings` | `TeamSeasonOverviewRatings` | no |
| `efficiency` | `TeamSeasonOverviewEfficiency` | no |
| `shooting` | `TeamSeasonOverviewShooting` | no |
| `players` | `TeamSeasonOverviewPlayers` | no |
| `schedule` | `TeamSeasonOverviewSchedule` | no |
| `sources` | `TeamSeasonOverviewSources` | no |

### `TeamSeasonOverviewEfficiency`

| Field | Type | Nullable |
|---|---|---|
| `defense` | `UnitMetrics` | no |
| `offense` | `UnitMetrics` | no |
| `paceGames` | `Union[StrictFloat, StrictInt]` | no |
| `pace` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `coverage` | `Coverage` | no |

### `TeamSeasonOverviewPlayers`

| Field | Type | Nullable |
|---|---|---|
| `rows` | `conlist(ProfilePlayer)` | no |
| `coverage` | `Coverage` | no |

### `TeamSeasonOverviewRatings`

| Field | Type | Nullable |
|---|---|---|
| `polls` | `TeamSeasonOverviewRatingsPolls` | no |
| `srs` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `elo` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `adjusted` | `Optional[AdjustedRatings]` | yes |

### `TeamSeasonOverviewRatingsPolls`

| Field | Type | Nullable |
|---|---|---|
| `coaches` | `PollStanding` | no |
| `ap` | `PollStanding` | no |

### `TeamSeasonOverviewRecord`

| Field | Type | Nullable |
|---|---|---|
| `unknownConferenceGames` | `Union[StrictFloat, StrictInt]` | no |
| `unknownEligibilityGames` | `Union[StrictFloat, StrictInt]` | no |
| `complete` | `StrictBool` | no |
| `conference` | `CountRecord` | no |
| `overall` | `CountRecord` | no |

### `TeamSeasonOverviewSchedule`

| Field | Type | Nullable |
|---|---|---|
| `games` | `conlist(ProfileGame)` | no |

### `TeamSeasonOverviewShooting`

| Field | Type | Nullable |
|---|---|---|
| `buckets` | `conlist(ShotBucket)` | no |
| `trackedAttempts` | `Union[StrictFloat, StrictInt]` | no |
| `coverage` | `Coverage` | no |

### `TeamSeasonOverviewSources`

| Field | Type | Nullable |
|---|---|---|
| `notes` | `conlist(StrictStr)` | no |
| `latestFinalStartDate` | `Optional[StrictStr]` | yes |
| `leaderboardUpdatedAt` | `Optional[StrictStr]` | yes |

### `TeamSeasonOverviewTeam`

| Field | Type | Nullable |
|---|---|---|
| `conference` | `Conference` | no |
| `sourceId` | `Optional[StrictStr]` | yes |
| `mascot` | `Optional[StrictStr]` | yes |
| `school` | `StrictStr` | no |

### `TeamSeasonStats`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `seasonLabel` | `StrictStr` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `Optional[StrictStr]` | yes |
| `games` | `StrictInt` | no |
| `wins` | `Union[StrictFloat, StrictInt]` | no |
| `losses` | `Union[StrictFloat, StrictInt]` | no |
| `totalMinutes` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pace` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `teamStats` | `TeamSeasonUnitStats` | no |
| `opponentStats` | `TeamSeasonUnitStats` | no |

### `TeamSeasonUnitStats`

| Field | Type | Nullable |
|---|---|---|
| `fieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `twoPointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `threePointFieldGoals` | `TeamSeasonUnitStatsFieldGoals` | no |
| `freeThrows` | `TeamSeasonUnitStatsFieldGoals` | no |
| `rebounds` | `TeamBoxScoreRebounds` | no |
| `turnovers` | `TeamSeasonUnitStatsTurnovers` | no |
| `fouls` | `TeamSeasonUnitStatsFouls` | no |
| `points` | `TeamSeasonUnitStatsPoints` | no |
| `fourFactors` | `TeamSeasonUnitStatsFourFactors` | no |
| `assists` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `blocks` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `steals` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `possessions` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShooting` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamSeasonUnitStatsFieldGoals`

| Field | Type | Nullable |
|---|---|---|
| `pct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `attempted` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `made` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamSeasonUnitStatsFouls`

| Field | Type | Nullable |
|---|---|---|
| `flagrant` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `technical` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamSeasonUnitStatsFourFactors`

| Field | Type | Nullable |
|---|---|---|
| `freeThrowRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveReboundPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnoverRatio` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `effectiveFieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamSeasonUnitStatsPoints`

| Field | Type | Nullable |
|---|---|---|
| `fastBreak` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offTurnovers` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `inPaint` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamSeasonUnitStatsRebounds`

| Field | Type | Nullable |
|---|---|---|
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `defensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensive` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamSeasonUnitStatsTurnovers`

| Field | Type | Nullable |
|---|---|---|
| `teamTotal` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `total` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamStatsLeaderboardConference`

| Field | Type | Nullable |
|---|---|---|
| `id` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `abbreviation` | `Optional[StrictStr]` | yes |

### `TeamStatsLeaderboardRecord`

| Field | Type | Nullable |
|---|---|---|
| `season` | `StrictInt` | no |
| `teamId` | `StrictInt` | no |
| `team` | `StrictStr` | no |
| `conference` | `TeamStatsLeaderboardConference` | no |
| `record` | `TeamStatsLeaderboardRecordSummary` | no |
| `summary` | `TeamStatsLeaderboardSummary` | no |
| `teamStats` | `TeamStatsLeaderboardUnitStats` | no |
| `opponentStats` | `TeamStatsLeaderboardUnitStats` | no |
| `shotProfile` | `TeamStatsLeaderboardShotProfile` | no |
| `adjustedEfficiency` | `Optional[TeamLeaderboardAdjustedEfficiency]` | yes |

### `TeamStatsLeaderboardRecordSummary`

| Field | Type | Nullable |
|---|---|---|
| `games` | `Union[StrictFloat, StrictInt]` | no |
| `wins` | `Union[StrictFloat, StrictInt]` | no |
| `losses` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardShotProfile`

| Field | Type | Nullable |
|---|---|---|
| `assistedPct` | `Union[StrictFloat, StrictInt]` | no |
| `atRim` | `TeamStatsLeaderboardShotProfileAtRim` | no |
| `dunk` | `TeamStatsLeaderboardShotProfileDunk` | no |
| `layup` | `TeamStatsLeaderboardShotProfileDunk` | no |
| `tipIn` | `TeamStatsLeaderboardShotProfileTipIn` | no |
| `twoPointJumper` | `TeamStatsLeaderboardShotProfileTwoPointJumper` | no |
| `threePointJumper` | `TeamStatsLeaderboardShotProfileTwoPointJumper` | no |
| `freeThrows` | `TeamStatsLeaderboardShotProfileFreeThrows` | no |
| `distribution` | `TeamStatsLeaderboardShotProfileDistribution` | no |

### `TeamStatsLeaderboardShotProfileAtRim`

| Field | Type | Nullable |
|---|---|---|
| `assistedPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamStatsLeaderboardShotProfileDistribution`

| Field | Type | Nullable |
|---|---|---|
| `threeRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `jumperRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `midrangeRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamStatsLeaderboardShotProfileDunk`

| Field | Type | Nullable |
|---|---|---|
| `rate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assisted` | `Union[StrictFloat, StrictInt]` | no |
| `made` | `Union[StrictFloat, StrictInt]` | no |
| `attempts` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardShotProfileFreeThrows`

| Field | Type | Nullable |
|---|---|---|
| `rate` | `Union[StrictFloat, StrictInt]` | no |
| `made` | `Union[StrictFloat, StrictInt]` | no |
| `attempts` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardShotProfileTipIn`

| Field | Type | Nullable |
|---|---|---|
| `rate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `made` | `Union[StrictFloat, StrictInt]` | no |
| `attempts` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardShotProfileTwoPointJumper`

| Field | Type | Nullable |
|---|---|---|
| `assistedPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `pct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `assisted` | `Union[StrictFloat, StrictInt]` | no |
| `made` | `Union[StrictFloat, StrictInt]` | no |
| `attempts` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardSummary`

| Field | Type | Nullable |
|---|---|---|
| `totalMinutes` | `Union[StrictFloat, StrictInt]` | no |
| `trackedShots` | `Union[StrictFloat, StrictInt]` | no |
| `pace` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rawNetRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TeamStatsLeaderboardUnitStats`

| Field | Type | Nullable |
|---|---|---|
| `points` | `TeamStatsLeaderboardUnitStatsPoints` | no |
| `possessions` | `Union[StrictFloat, StrictInt]` | no |
| `rawOffensiveRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `trueShootingPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `effectiveFieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnoverRatio` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveReboundPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `freeThrowRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `fieldGoals` | `TeamStatsLeaderboardUnitStatsFieldGoals` | no |
| `twoPointFieldGoals` | `TeamStatsLeaderboardUnitStatsFieldGoals` | no |
| `threePointFieldGoals` | `TeamStatsLeaderboardUnitStatsFieldGoals` | no |
| `freeThrows` | `TeamStatsLeaderboardUnitStatsFieldGoals` | no |
| `rebounds` | `TeamStatsLeaderboardUnitStatsRebounds` | no |
| `turnovers` | `TeamStatsLeaderboardUnitStatsTurnovers` | no |
| `fouls` | `TeamStatsLeaderboardUnitStatsFouls` | no |
| `assists` | `Union[StrictFloat, StrictInt]` | no |
| `blocks` | `Union[StrictFloat, StrictInt]` | no |
| `steals` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardUnitStatsFieldGoals`

| Field | Type | Nullable |
|---|---|---|
| `attempted` | `Union[StrictFloat, StrictInt]` | no |
| `made` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardUnitStatsFouls`

| Field | Type | Nullable |
|---|---|---|
| `flagrant` | `Union[StrictFloat, StrictInt]` | no |
| `technical` | `Union[StrictFloat, StrictInt]` | no |
| `total` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardUnitStatsPoints`

| Field | Type | Nullable |
|---|---|---|
| `fastBreak` | `Union[StrictFloat, StrictInt]` | no |
| `offTurnovers` | `Union[StrictFloat, StrictInt]` | no |
| `inPaint` | `Union[StrictFloat, StrictInt]` | no |
| `total` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardUnitStatsRebounds`

| Field | Type | Nullable |
|---|---|---|
| `total` | `Union[StrictFloat, StrictInt]` | no |
| `defensive` | `Union[StrictFloat, StrictInt]` | no |
| `offensive` | `Union[StrictFloat, StrictInt]` | no |

### `TeamStatsLeaderboardUnitStatsTurnovers`

| Field | Type | Nullable |
|---|---|---|
| `technical` | `Union[StrictFloat, StrictInt]` | no |
| `team` | `Union[StrictFloat, StrictInt]` | no |
| `total` | `Union[StrictFloat, StrictInt]` | no |

### `Transfer`

| Field | Type | Nullable |
|---|---|---|
| `id` | `StrictInt` | no |
| `sourceId` | `StrictStr` | no |
| `year` | `StrictInt` | no |
| `firstName` | `StrictStr` | no |
| `lastName` | `StrictStr` | no |
| `position` | `StrictStr` | no |
| `origin` | `Optional[TransferOrigin]` | yes |
| `destination` | `Optional[RecruitCommittedTo]` | yes |
| `eligibility` | `Optional[TransferEligibility]` | yes |
| `yearsRemaining` | `Optional[StrictInt]` | yes |
| `stars` | `Optional[StrictInt]` | yes |
| `rating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `TransferEligibility`
Enum values: `Withdrawn`, `TBD`, `PendingAppeal`, `SittingOne`, `Immediate`

### `TransferOrigin`

| Field | Type | Nullable |
|---|---|---|
| `conference` | `Optional[StrictStr]` | yes |
| `name` | `Optional[StrictStr]` | yes |
| `id` | `Optional[StrictInt]` | yes |

### `UnitMetrics`

| Field | Type | Nullable |
|---|---|---|
| `boxScore` | `Optional[TeamBoxScore]` | yes |
| `points` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `possessions` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `rawRating` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `effectiveFieldGoalPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `turnoverPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `offensiveReboundPct` | `Optional[Union[StrictFloat, StrictInt]]` | yes |
| `freeThrowRate` | `Optional[Union[StrictFloat, StrictInt]]` | yes |

### `VenueInfo`

| Field | Type | Nullable |
|---|---|---|
| `id` | `Union[StrictFloat, StrictInt]` | no |
| `sourceId` | `Optional[StrictStr]` | yes |
| `name` | `StrictStr` | no |
| `city` | `Optional[StrictStr]` | yes |
| `state` | `Optional[StrictStr]` | yes |
| `country` | `Optional[StrictStr]` | yes |

## 9. Audit conclusion for model planning

CBBD v1.29.0 can serve as the **primary historical basketball-event backbone** for the pregame model because it directly supplies box scores, date-bounded season aggregates, PBP with players on floor, shot context, substitutions, lineup aggregates, roster data, recruiting, transfer data, ratings, and game-market context.

The source gaps that remain genuinely external are concentrated and clear. For the **core/automated availability path**, the important gap is timestamped injuries/availability and minutes restrictions. For the **benchmark path**, historical player-prop prices and dense game-line movement are optional rather than build-blocking; prospective standardized Odds API snapshots are strongly recommended. Production live-feed latency/correction metadata, coach/referee data if later justified, and venue geospatial/timezone/elevation enrichment remain separate external concerns.

The revised `DATA_REQUIREMENTS.md` v1.2 treats CBBD as the primary source wherever the audited schema supports the need, distinguishes prediction-path requirements from benchmark-only data, and keeps the flagship originator independent of sportsbook inputs. `SOURCE_MATRIX.md` should be built from that policy.