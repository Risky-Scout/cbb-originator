# Stage 1 Coverage Summary

Rendered from `data/qa/STAGE_1_COVERAGE_SUMMARY.json`, which is derived from the authoritative `data/qa/COVERAGE_AUDIT.json` (canonical build `20261006T022302Z`). Every number is measured from physically present data. Nothing here is estimated from documentation.

## Season coverage by family (CBBD season = year the season ends)

| Family | First | Last | Missing in range | Rows |
|---|---:|---:|---|---:|
| games (schedule/results) | 1949 | 2025 | none | 272,560 |
| player_game_stats | 2005 | 2025 | none | 2,460,230 |
| player_season_stats | 2005 | 2025 | none | 101,563 |
| player_season_shooting_stats | 2014 | 2025 | none | 56,173 |
| team_game_stats | 2005 | 2025 | none | 244,808 |
| team_season_stats | 2005 | 2025 | none | 7,412 |
| team_season_shooting_stats | 2014 | 2025 | none | 4,311 |

Seasons present in games.csv (from 2003) with no box scores at all: [2003, 2004].
games.csv also covers 1949–2002 with schedule and results only.

## Game ID coverage (games with a player box score / games in games.csv)

| Season | Player box / games |
|---:|---|
| 2005 | 4356/5031 |
| 2006 | 4922/5172 |
| 2007 | 5243/5478 |
| 2008 | 5559/5590 |
| 2009 | 5649/5726 |
| 2010 | 5757/5761 |
| 2011 | 5762/5770 |
| 2012 | 5045/5739 |
| 2013 | 5025/5825 |
| 2014 | 5932/5948 |
| 2015 | 5925/5932 |
| 2016 | 5940/5951 |
| 2017 | 5919/5964 |
| 2018 | 5965/5968 |
| 2019 | 6043/6049 |
| 2020 | 5767/5767 |
| 2021 | 4285/4285 |
| 2022 | 5966/5966 |
| 2023 | 6221/6222 |
| 2024 | 6243/6243 |
| 2025 | 6286/6292 |

## Player ID coverage

- All player-game rows resolved to a persistent canonical player: 95.8883%.
- D-I team-season player-game rows: 2,271,654; unresolved: 821.
- Resolution method: exact `(season, team, name)` match to the persistent id in player_season_stats. `player_game_stats.athleteId` is a per-record id; see the physical audit.
- BDL smoke games mapped to canonical games: {'fetched': 816, 'mapped_to_canonical': 804}.

## Schema consistency (Starter Pack, across season files)

| Family | Distinct column sets | Distinct column orders | Columns changing type |
|---|---:|---:|---:|
| player_game_stats | 1 | 4 | 5 |
| player_season_stats | 1 | 2 | 6 |
| player_season_shooting_stats | 1 | 1 | 0 |
| team_game_stats | 6 | 20 | 62 |
| team_season_stats | 1 | 2 | 5 |
| team_season_shooting_stats | 1 | 1 | 0 |

## Event-level families (PBP / on-floor / substitutions / lineups)

- pbp: **MEASURED**
- on_floor: **MEASURED**
- shots: **MEASURED**
- substitutions: **MEASURED**
- lineups: **MEASURED**
- lineup_stints: **NOT_MEASURED**
- rosters: **MEASURED**
- recruits: **MEASURED**
- transfers: **MEASURED**
- venue_directory: **MEASURED**

No historical PBP, on-floor, substitution, or lineup coverage is claimed. MEASURED here means only that rows from the bounded CBBD live SMOKE SAMPLE exist (no backfill). Per-game smoke metrics are in docs/CBBD_LIVE_SMOKE_RESULTS.md and must not be generalized to whole seasons.

## Independent checks against BDL (2023-24 and 2024-25 smoke samples)

- GAME_SCORE: 1,606 / 1,608 comparisons agree
- PLAYER_GAME: 1,376 / 1,376 comparisons agree
- TEAM_GAME: 112 / 112 comparisons agree
- Tip times: 804 / 804 canonical tip times equal BDL's independent UTC tip time.

## Lockbox

- 2025-26 (CBBD 2026): 0 rows present. Starter Pack ends at 2024-25; no lockbox data was fetched or ingested.
