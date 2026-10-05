# SOURCE_MATRIX.md
## CBB Originator Data Platform v1.0
### Source Assignment Matrix
**Date:** October 4, 2026  
**Status:** APPROVED FOR IMPLEMENTATION

## 1. Core assignment

| Canonical family | Primary source | Secondary / QA | Classification | Blocking? |
|---|---|---|---|---|
| games / schedule / scores | CBBD `/games` | BDL `/ncaab/v1/games` | P0 | Yes |
| teams / conferences | CBBD `/teams`, `/conferences`, directory/history | BDL teams/conferences | P0 | Yes |
| rosters / player identity | CBBD `/teams/roster` | BDL players/active | P0 | Yes |
| player game outcomes | CBBD `/games/players` | BDL `/player_stats`; Bart Torvik enrichment | P0 | Yes |
| team game outcomes | CBBD `/games/teams` | BDL `/team_stats` | P0 | Yes |
| player season summaries | CBBD date-bounded player stats | BDL player season stats; Torvik | P0-derived | No if player-game data complete |
| player shot profiles | CBBD player shooting season endpoint + PBP shotInfo | Bart Torvik game-level rim/mid/dunk enrichment | P0/P1 | P0 core, Torvik non-blocking |
| team season summaries | CBBD date-bounded team stats | BDL team season stats | P0-derived | No if team-game data complete |
| PBP | CBBD `/plays/*` | BDL targeted QA only | P0 | Yes |
| on-floor players | CBBD `PlayInfo.onFloor[]` | none | P0 | Yes for interconnected layer |
| substitutions | CBBD `/substitutions/*` | none | P0 | Yes for rotation/stint layer |
| aggregate lineups | CBBD `/lineups/*` | none | P0 | Yes for lineup layer |
| exact lineup stints | DERIVED from CBBD PBP/onFloor/substitutions | CBBD aggregate lineups for QA | P0-derived | Yes |
| possessions | DERIVED from CBBD PBP | team possessions / scores for QA | P0-derived | Yes |
| recruiting | CBBD `/recruiting/players`, `/recruiting/teams` | none | P1/P0 cold start | No for initial veteran-only baseline |
| transfers | CBBD `/recruiting/portal` | none | P1/P0 cold start | No for initial baseline |
| current availability/injuries | Sharp Stats | actual CBBD postgame participation label | P0 production | Yes for automated current predictions |
| historical DNP/participation | DERIVED from CBBD schedule/rosters/player-games | Bart Torvik explicit DNP | P0-derived | Yes |
| historical injury diagnosis/status | none required | future archive if acquired | P2 | No |
| game odds benchmark | BDL `/odds`, `/odds/opening` | CBBD `/lines` | P1 benchmark | No |
| prospective market snapshots | BDL + our snapshot timestamps | Odds API fallback | P1 benchmark | No |
| historical player props | none required | later external source | P2 benchmark | No |
| tournament/bracket context | CBBD game/tournament fields | BDL `/bracket` | P1 | No |
| venue city/state/country | CBBD `/venues` | game metadata | P1 | No |
| venue lat/lon/timezone/elevation | external enrichment if later justified | derived geocoding | P2/P1 | No |
| coach history | external if later justified | none | P1 | No |
| referee data | external if later justified | none | P2 | No |

## 2. CBBD P0 endpoint plan

Acquire and preserve raw responses for:
- `/games`
- `/games/players`
- `/games/teams`
- `/plays/game/{gameId}` or equivalent efficient bulk routes
- `/plays/types`
- `/substitutions/game/{gameId}` / efficient team/player alternatives as appropriate
- `/lineups/game/{gameId}` and/or `/lineups/team`
- `/teams`
- `/teams/directory`
- `/teams/roster`
- `/conferences`
- `/conferences/history`
- `/stats/player/season`
- `/stats/player/shooting/season`
- `/stats/team/season`
- `/stats/team/shooting/season`
- `/recruiting/players`
- `/recruiting/teams`
- `/recruiting/portal`
- `/venues`

Acquire CBBD `/lines` for secondary benchmark/context, not for the originator feature path.

## 3. Sharp Stats canonical mapping

| Sharp Stats raw field | Canonical field | Use |
|---|---|---|
| team heading | `source_team_name` → `team_id` mapping | identity |
| Player | `source_player_name` → `player_id` mapping | identity |
| Pos | `source_position` | QA/context |
| Class | `source_class` | QA/context |
| Injury | `injury_raw`, derived `injury_category` | availability context |
| Status | `status_raw`, derived `status_normalized` | availability state |
| Updated | `source_updated_date` | source freshness |
| note line | `note_raw` + derived note flags | critical contextual availability signal |
| collection timestamp | `received_at_utc` | authoritative PIT timestamp |

Sharp Stats performance metrics are not authoritative player-performance fields; retain only if convenient for source QA.

## 4. BDL canonical mapping

### Basketball QA
`/ncaab/v1/player_stats`
- player/team/game IDs
- min
- fgm/fga
- fg3m/fg3a
- ftm/fta
- oreb/dreb/reb
- ast/stl/blk
- turnover
- pf
- pts

`/ncaab/v1/team_stats`
- team/game
- shooting
- rebounding
- assists
- steals
- blocks
- turnovers
- fouls

`/ncaab/v1/games`
- game/time/season
- teams
- final score
- H1/H2 scores
- OT arrays

`/ncaab/v1/players/active`
- current player identity/active-player QA

### Benchmark
`/ncaab/v1/odds`
- vendor
- home/away spread values
- home/away spread prices
- home/away ML
- total
- over/under prices
- `updated_at`

`/ncaab/v1/odds/opening`
- same pricing fields
- `opened_at`

Market fields are physically isolated from the pure-originator feature namespace.

## 5. Bart Torvik canonical enrichment mapping

If a permitted/stable automated retrieval method is available, map historical player-game rows into:
- `played`
- `dnp`
- `minutes`
- `ortg`
- `usage`
- `efg_pct`
- `ts_pct`
- `orb_pct`
- `drb_pct`
- `ast_pct`
- `tov_pct`
- `stl_pct`
- `blk_pct`
- `dunk_made`, `dunk_attempted`
- `rim_made`, `rim_attempted`
- `mid_made`, `mid_attempted`
- `two_pm`, `two_pa`
- `three_pm`, `three_pa`
- `ftm`, `fta`
- `ast`, `tov`, `stl`, `blk`, `orb`, `drb`, `pf`
- `bpm`, `opm`, `dpm`, `net`, `pts`

Use only after:
- player/team/date identity mapping passes;
- PIT semantics are clear;
- retrieval method is permitted;
- coverage is measured;
- incremental value is validated.

## 6. No source conflict may be silently resolved

Store all provider IDs and source lineage.

For each canonical record:
- preserve source payload/hash/reference;
- reconcile CBBD vs BDL player/team/game box scores;
- create QA exception records for mismatches;
- preserve both Sharp Stats structured status and note;
- never overwrite CBBD with BDL or Torvik without an explicit adjudication rule.

## 7. Decision complete

All P0 data families have a source or a documented derivation.

The project is cleared to proceed to:
1. canonical schema finalization,
2. PIT policy finalization,
3. Data Platform v1.0 implementation,
4. coverage audit.

No further data-source research is required before implementation.
