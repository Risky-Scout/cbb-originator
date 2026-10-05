# DATA_SOURCE_POLICY.md
## CBB Originator Interconnected Player Prop Predictive Model
### Frozen Data-Source Architecture v1.0
**Date:** October 4, 2026  
**Status:** FROZEN FOR DATA PLATFORM v1.0 IMPLEMENTATION

## 1. Governing decision

The model will not wait for a multi-year historical injury archive. Historical player strength, absence impact, role changes, and teammate redistribution will be learned primarily from actual historical participation, DNPs, minutes, starts, lineups, substitutions, play-by-play, and player game performance.

Current pregame availability remains essential and will be supplied prospectively by Sharp Stats.

No source is allowed to become a predictive dependency merely because it is convenient. Each source has a defined responsibility.

## 2. Authoritative source hierarchy

| Source | Authoritative role | Priority | May enter pure-originator feature path? |
|---|---|---:|---|
| CollegeBasketballData (CBBD) | Primary historical basketball event/performance backbone: games, player/team box scores, PBP, on-floor players, substitutions, lineups, shooting, rosters, recruiting, transfers, ratings context | P0 | Yes |
| Sharp Stats | Current-season pregame player availability, injury/absence reason, structured status, update date, descriptive note | P0 production availability | Yes, availability information only |
| BALLDONTLIE NCAAB (BDL) | Independent box-score/identity QA; current and opening sportsbook benchmark with side prices and timestamps; active-player QA | P1 QA / benchmark | No market fields; basketball QA fields only if promoted after reconciliation |
| Bart Torvik | Historical advanced player-strength enrichment: explicit DNPs, game-level advanced rates/shot-location buckets, BPM/OPM/DPM where available | P1 non-blocking enrichment | Yes only after PIT, licensing/access, and incremental validation gates |
| Odds API | Fallback prospective market snapshot source only if BDL market coverage/latency/vendor breadth is inadequate | Fallback | No |
| Historical injury archive | Optional research enrichment; not required for core model | P2 | Only if later acquired and PIT-valid |
| Historical player-prop prices | Retrospective sportsbook benchmark only | P2/P1 later | No |

## 3. CBBD responsibilities

CBBD remains the primary basketball truth for the initial model.

Required families:
- games / schedule / game state
- teams / conferences / venues
- player game box scores
- team game box scores
- player/team season statistics
- player/team shooting profiles
- play-by-play
- event `onFloor`
- event participants / structured shot information
- substitutions
- aggregate five-man lineups
- rosters
- recruiting
- transfer portal
- game-level Elo start/end where covered
- ratings/polls as contextual or QA information subject to PIT rules
- game lines as secondary benchmark/context only

CBBD-derived canonical artifacts:
- possessions
- exact lineup stints
- paired substitution transactions
- historical DNP/participation indicators
- historical absence-response examples
- role/minutes/usage redistribution
- time-decayed player/team states

## 4. Sharp Stats responsibilities

Sharp Stats is the production availability source beginning with the current NCAAB season.

Retain:
- team
- player
- position
- class
- injury/absence reason
- structured status
- update date
- full descriptive note
- all page-level/current context needed to identify the target team/game
- our own exact `received_at_utc`

Normalized status vocabulary must support at least:
- `PROBABLE`
- `QUESTIONABLE`
- `DOUBTFUL`
- `OUT`
- `OUT_SEASON`
- `UNKNOWN`

The raw status and raw note are both retained.

### Status-note conflicts

Sharp Stats records may contain conflicts between the structured status and descriptive note. The ingestion layer must:
1. preserve both raw values;
2. set `conflict_flag = true`;
3. classify the conflict;
4. never silently overwrite one with the other;
5. apply only a documented and tested normalization rule downstream.

The descriptive note may contain signals such as:
- expected to play
- decent chance to play
- not expected to play
- game-time decision
- out for season
- extended absence
- no timetable
- surgery
- left last game
- personal reason
- suspension

These may become derived structured fields, but raw note text remains immutable.

### Prospective snapshot strategy

Because Sharp Stats exposes an update date rather than an exact source time, our collection timestamp is authoritative for PIT replay.

Minimum capture:
- daily full report snapshot
- game-day T-6h
- game-day T-1h
- game-day T-10m

Every snapshot is append-only.

## 5. Historical injuries are not foundational

Historical injury labels are explicitly **not required** for the core player-performance or absence-response model.

Historical training learns:
- who played
- who did not play
- who started
- minutes
- usage / opportunity
- lineup membership
- teammate role changes
- replacement starters
- team environment changes

The core target is:

`P(rotation, minutes, lineup, role, opportunities | player unavailable)`

not:

`P(rotation | specific historical medical diagnosis)`.

If a historical injury archive is later obtained, it may improve the availability-status model or return-from-injury minutes model, but its absence does not block v1.

## 6. BDL responsibilities

The attached BDL NCAAB OpenAPI exposes 19 NCAAB endpoints, including:
- conferences
- teams
- players
- active players
- standings
- games
- rankings
- play-by-play
- player game stats
- team game stats
- player season stats
- team season stats
- bracket
- current odds
- opening odds

### BDL fields of special value

**Independent player-game reconciliation**
- MIN
- FGM/FGA
- 3PM/3PA
- FTM/FTA
- OREB/DREB/REB
- AST
- STL
- BLK
- TOV
- PF
- PTS

**Independent game reconciliation**
- home/away score
- first-half score
- second-half score
- overtime-period score arrays

**Market benchmark**
Current odds:
- vendor
- home spread and price
- away spread and price
- home/away moneyline
- total
- over/under prices
- `updated_at`

Opening odds:
- same pricing fields
- `opened_at`

### BDL is not the primary PBP/lineup source

BDL play-by-play contains game/order/type/text/score/period/clock/team information but does not replace CBBD's:
- `onFloor`
- structured participants
- substitutions
- lineup units
- shot shooter/assister/x-y detail

Therefore BDL PBP is nonessential for Data Platform v1.0 unless used in targeted QA.

## 7. Bart Torvik responsibilities

Bart Torvik is a **non-blocking enrichment source**, not a dependency of the critical path.

High-value game-level player fields visible in the historical player logs include:
- DNP / played state
- minutes
- ORtg
- usage
- eFG%
- TS%
- OR%
- DR%
- AST%
- TO%
- STL%
- BLK%
- dunk attempts/makes
- rim attempts/makes
- midrange attempts/makes
- 2PT / 3PT / FT lines
- AST / TO / STL / BLK / OR / DR / PF
- BPM
- OPM
- DPM
- NET
- PTS

Use cases:
- richer historical player latent-strength estimation
- explicit DNP verification
- game-level shot-location/style enrichment
- challenger features
- independent performance QA

Implementation rule:
- core model proceeds if Torvik ingestion is unavailable, brittle, slow, or not permitted under the chosen access method;
- no architecture is allowed to depend on a Torvik-only field until its coverage and incremental predictive value are demonstrated out of sample.

## 8. Market isolation

The flagship originator prediction path must not use:
- sportsbook spread
- sportsbook total
- sportsbook moneyline
- player prop line
- market movement

Market data lives in a separate benchmark namespace.

BDL becomes the preferred prospective benchmark source because it provides side prices and explicit `updated_at` / `opened_at` timestamps.

Capture prospective benchmark snapshots at standardized checkpoints where available:
- T-24h
- T-6h
- T-1h
- T-10m

Odds API is used only if BDL coverage, vendor breadth, or reliability fails the benchmark gate.

## 9. Source conflict policy

Primary-source order for basketball outcomes:
1. CBBD canonical data
2. BDL independent reconciliation
3. Bart Torvik enrichment/reconciliation

A disagreement must create a QA exception. It must not trigger an undocumented overwrite.

Availability:
1. Sharp Stats raw structured status + note
2. canonical conflict/normalization logic
3. actual CBBD participation outcome after the game for training labels

## 10. Freeze rule

This source architecture is frozen for Data Platform v1.0.

A source or priority may change only if:
- coverage audit fails a P0 gate;
- a source is inaccessible/unlicensed;
- reconciliation exposes material quality problems;
- controlled out-of-sample testing proves a new source materially improves performance.

No source shopping or blueprint redesign is permitted merely because another dataset appears interesting.
