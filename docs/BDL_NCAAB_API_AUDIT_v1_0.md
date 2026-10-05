# BDL_NCAAB_API_AUDIT.md
## BALLDONTLIE NCAAB OpenAPI Review
### Version 1.0
**Date:** October 4, 2026  
**Source:** user-provided BALLDONTLIE NCAAB OpenAPI 3.1.0, API version 1.0.0

## 1. Endpoint inventory

The supplied specification contains 19 NCAAB paths:

1. `/ncaab/v1/conferences`
2. `/ncaab/v1/conferences/{id}`
3. `/ncaab/v1/teams`
4. `/ncaab/v1/teams/{id}`
5. `/ncaab/v1/players`
6. `/ncaab/v1/players/{id}`
7. `/ncaab/v1/players/active`
8. `/ncaab/v1/standings`
9. `/ncaab/v1/games`
10. `/ncaab/v1/games/{id}`
11. `/ncaab/v1/rankings`
12. `/ncaab/v1/plays`
13. `/ncaab/v1/player_stats`
14. `/ncaab/v1/team_stats`
15. `/ncaab/v1/player_season_stats`
16. `/ncaab/v1/team_season_stats`
17. `/ncaab/v1/bracket`
18. `/ncaab/v1/odds`
19. `/ncaab/v1/odds/opening`

## 2. High-value data for this model

### Player game statistics
`NCAABPlayerStat`:
- player
- team
- game id/date/season
- MIN
- FGM/FGA
- 3PM/3PA
- FTM/FTA
- OREB/DREB/REB
- AST/STL/BLK
- TOV
- PF
- PTS

Use: independent reconciliation of CBBD player-game outcomes and identity mapping.

### Team game statistics
`NCAABTeamStat`:
- team
- game id/date/season
- shooting
- offensive/defensive/total rebounds
- assists
- steals
- blocks
- turnovers
- fouls

Use: independent team-game QA.

### Games
`NCAABGame`:
- date
- season
- postseason
- status/state
- period
- home/visitor teams
- total score
- H1/H2 scores
- OT score arrays

Use: independent game/score reconciliation and useful future in-play QA.

### Players
`NCAABPlayer`:
- ID
- name
- position
- height
- weight
- jersey
- team

`/players/active` provides a current active-player listing.

Use: player identity/roster QA.

### Odds
`NCAABBettingOdd`:
- vendor
- home spread value + price
- away spread value + price
- home/away moneyline
- total
- over/under prices
- `updated_at`

`NCAABOpeningBettingOdd`:
- same core pricing structure
- `opened_at`

Use: preferred prospective sportsbook benchmark because both sides and timestamps are available.

Opening-odds documentation says historical opening coverage is limited to the most recently completed season and ongoing seasons where available.

## 3. Data BDL does not replace

BDL play-by-play is not sufficient for the interconnected lineup/event architecture because its `NCAABPlay` schema provides:
- game
- order
- type
- text
- score
- period
- clock
- scoring flag/value
- team

but does not provide the CBBD equivalents of:
- ten-player `onFloor`
- structured player participants
- structured shooter / assister / shot coordinates
- substitutions
- lineup aggregates

Therefore CBBD remains the P0 PBP/lineup source.

## 4. Recommended role

**BDL is a secondary source, not a duplicate primary basketball feed.**

Use BDL for:
1. independent player/team/game reconciliation;
2. current active-player QA;
3. prospective opening/current market benchmark;
4. optional targeted PBP comparison if later useful.

Do not ingest all BDL PBP historically unless the coverage audit identifies a specific QA need.

## 5. Market-isolation rule

All BDL odds fields live in the benchmark namespace and are excluded from the pure-originator feature path.

## 6. API implementation notes

- API-key authorization header is required.
- Cursor pagination is used.
- `per_page` maximum is 100.
- games/player_stats/team_stats support date/season filtering.
- season convention uses the season starting year (e.g. 2025 for 2025-26).
- acquisition code must normalize BDL season semantics before joins.
