# CANONICAL_SCHEMA.md — CBB Originator canonical data contract

**Schema version:** `canonical-1.0.0` + Stage 1 closure patch (2026-10-05) · **Ingestion version:** `stage1-1.0.0` · **Status:** Stage 1 deliverable
**Executable definition:** `cbb_platform/schema.sql` (DuckDB DDL). If this document and the DDL ever disagree, the DDL is what runs, and the disagreement is a bug.
**Companion contracts:** `POINT_IN_TIME_POLICY.md` (timing), `LOCKBOX_POLICY.md` (2025-26 holdout), `docs/SEASON_STATISTICS_AUTHORITY.md`, `docs/CBBD_PLAYER_IDENTITY_VERIFICATION.md`, `docs/STARTER_PACK_PHYSICAL_AUDIT.md` (measured source facts), `data/qa/COVERAGE_AUDIT.json` (measured coverage).

---

## 0. Rebuild from scratch

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m cbb_platform.audit_starter_pack     # Step 1: physical audit + Starter Pack SHA-256 manifest
.venv/bin/python -m cbb_platform.build_canonical        # Steps 4-8: data/canonical/cbb.duckdb + Parquet
.venv/bin/python -m cbb_platform.coverage               # Step 11: COVERAGE_AUDIT.json + summaries + unresolved ids
.venv/bin/python -m pytest                              # Step 10: QA suite (includes a full deterministic rebuild)
```

Optional live smoke fetches. Credentials are read from the environment only and are never written to disk.

```bash
.venv/bin/python -m cbb_platform.adapters.bdl smoke                     # needs BDL_API_KEY
.venv/bin/python -m cbb_platform.cbbd_smoke fetch                     # bounded closure sample; needs CBBD_API_KEY
.venv/bin/python -m cbb_platform.cbbd_smoke analyze                   # offline: athleteId identity + coverage smoke
.venv/bin/python -m cbb_platform.adapters.availability ingest sharp_stats <saved_capture.html|.csv>
```

Every CBBD request passes the 2025-26 lockbox guard (`cbb_platform/lockbox.py`) before it is sent and again before it is stored.

After any new raw payload arrives, rerun `build_canonical` and then `coverage`. No notebook is ever required.

## 1. Layers and storage

| Layer | Where | What it holds | Rule |
|---|---|---|---|
| Immutable upstream | `../CBB Starter Pack/starter_pack` | CBBD-derived CSVs | Read-only. Never copied; SHA-256 recorded in `data/manifests/starter_pack_sha256.json`. Registration time is in `data/manifests/starter_pack_registration.json`. Research and prototyping only, never a production dependency. |
| Raw | `data/raw/<provider>/<endpoint>/<UTCstamp>__<sha16>.<ext>` + `.meta.json` | Every payload we fetched or received (BDL, CBBD, Sharp Stats captures) | Written once with mode 0444. Identical bytes are a no-op; changed bytes append a new file (`cbb_platform/raw_store.py`). |
| Staging | DuckDB schema `staging` (views) | Starter Pack CSVs as all-VARCHAR, columns read **by name** (`union_by_name`), with `filename` lineage | Lossless: values exactly as written. No copy is made. |
| Canonical | `data/canonical/cbb.duckdb` schemas `core`, `fact`, `availability`, `benchmark`, `metadata`, `qa`; Parquet twins at `data/canonical/<schema>/<table>.parquet` | Provider-independent normalized tables plus lineage | Deterministic rebuild from Starter Pack + raw. An empty table has no Parquet file. |
| Flagship namespace | DuckDB schema `flagship` (views only) | The **only** namespace later PIT feature builders may read | Never references `benchmark`. Contains no sportsbook column. Excludes synthetic fixtures. |
| Benchmark namespace | DuckDB schema `benchmark` | BDL QA box scores and **all** sportsbook data | Physically separate; tested by `tests/test_market_isolation.py`. |

The build writes to `cbb.duckdb.building` and atomically renames it on success, so a failed build never leaves a half-written database.

## 2. Canonical ID contract

```
canonical_<entity>_id = <PREFIX> '-' || first 16 hex chars of sha256('CBBD|<entity>|<CBBD id>')
    G- game      T- team      P- player (athlete)      C- conference      V- venue
```

- **Opaque and provider-independent.** No business logic may parse them. Provider ids exist only in `metadata.*_id_map` and in each row's `source_record_key`.
- **Deterministic and stable.** The same CBBD id always yields the same canonical id. CBBD is the anchor provider for every entity (minimum-provider principle). The Starter Pack is CBBD-derived, so a later CBBD API ingestion produces identical game/team/conference ids.
- **The player anchor is the persistent CBBD athlete id**, which exists only in `player_season_stats` / `player_season_shooting_stats` (see §6). If the CBBD API later reveals a different player-id space, that is a STOP-and-review event, not a silent remap. The CBBD adapter maps an API `athleteId` onto a canonical player **only** when `data/qa/cbbd_player_identity_verification.json` classifies it `PERSISTENT_PLAYER_ID` **and** shows ≥ 99% agreement with the season-file id (`cbbd_identity.verified_anchor_compatible`). Otherwise it keeps a provider-scoped `CBBD_ATHLETE:<id>` token. Current status (Stage 1 final unblock): **`PERSISTENT_PLAYER_ID`, anchor-compatible**. Roster agreement is 28/28 and season-file agreement 73/73 name-resolved players; the 2 unmatched players differ only by a `Jr.` suffix. Live CBBD PBP, substitution, lineup and shot rows therefore resolve to canonical players.
- **No unresolved record ever receives a canonical id.** Unresolved rows keep `canonical_player_id = NULL` and carry an `identity_status`. Collisions are tested (`test_canonical_ids_unique`).

## 3. Table catalogue

Populated means populated in the Stage 1 build. Row counts are in `metadata.data_versions` and `data/qa/COVERAGE_AUDIT.json`.

| Table | Grain | Primary key | Main foreign keys | Stage 1 |
|---|---|---|---|---|
| `core.conferences` | conference | `canonical_conference_id` | — | populated (34) |
| `core.teams` | team | `canonical_team_id` | — | populated |
| `core.team_seasons` | team × season it played | (`canonical_team_id`, `season`) | teams, conferences | populated; carries the D-I proxy |
| `core.venues` | venue | `canonical_venue_id` | — | populated (from games.csv) |
| `core.games` | contest | `canonical_game_id` | teams, conferences, venues | populated 1949-2025 |
| `core.game_schedule_versions` | game × schedule version | (`canonical_game_id`, `schedule_version`) | games | 1 retrospective version per game |
| `core.players` | persistent athlete | `canonical_player_id` | — | populated |
| `core.rosters` | player × team × season (CBBD /teams/roster) | (player, team, season) | players, teams | Stage 2 backfill (see §12 and `data/qa/cbbd_event_coverage_by_season.csv`): 2004-05..2024-25 |
| `core.recruits`, `core.transfers` | recruit / portal entry | source id | players, teams | **empty**: CBBD API not fetched |
| `fact.player_games` | one source box-score line | `player_game_key` | games, teams, players (nullable) | populated 2005-2025 |
| `fact.team_games` | team × game | (`canonical_game_id`, `canonical_team_id`) | games, teams | populated 2005-2025 |
| `fact.participation` | candidate player × team × game | (game, team, `candidate_key`) | games, teams, players | populated (see §8) |
| `fact.player_season_stats` / `fact.player_season_shooting` | player × team × season (end-of-season) | (player, team, season) | players, teams | populated (shooting 2014+) |
| `fact.team_season_stats` / `fact.team_season_shooting` | team × season (end-of-season, **raw summary, unchanged**; secondary/QA/prior) | (team, season) | teams | populated (shooting 2014+) |
| `fact.team_season_game_derived` | team × season, **authoritative** totals derived from `core.games` + `fact.team_games` | (team, season) | teams | populated (seasons with summaries) |
| `fact.player_season_game_derived` | player × team × season, **authoritative** where `identity_complete` (sum of resolved `fact.player_games`) | (player, team, season) | players, teams | populated |
| `fact.plays` / `fact.shots` (+ `fact.play_participants`, `fact.play_on_floor`) | CBBD play / shot / participant / onFloor entry | (game, `source_play_id`[, order]) | games, teams, players | Stage 2 backfill (see §12 and `data/qa/cbbd_event_coverage_by_season.csv`): 2005-06..2024-25 |
| `fact.substitutions` (+ derived `fact.substitution_pairs`) | athlete stint as CBBD supplies it (athlete-centric) | (game, athlete, `stint_number`) | games, teams, players | Stage 2 backfill (see §12 and `data/qa/cbbd_event_coverage_by_season.csv`) |
| `fact.lineup_stats` (+ `qa.lineup_units_quarantine`) | CBBD five-man aggregate (not stints); valid units only | (game, team, `lineup_hash`) | games, teams | Stage 2 backfill (see §12 and `data/qa/cbbd_event_coverage_by_season.csv`): 2023-24, 2024-25 |
| `fact.lineup_stints`, `fact.possessions` | derived exact stint / possession | (game, id) | games | **empty by design**: derived in Stage 4; never manufactured |
| `availability.events` | one player line of one received snapshot | `availability_event_id` | teams, players (nullable) | **empty**: no real capture supplied |
| `benchmark.bdl_games`, `bdl_player_games`, `bdl_team_games` | BDL record | BDL ids | games, teams, players (nullable) | populated by the BDL smoke fetch |
| `benchmark.game_odds`, `benchmark.opening_odds` | vendor snapshot | (`bdl_odds_id`, `raw_sha256`) | games | empty: BDL returned no odds for 2024-25 games |
| `metadata.{player,team,game,conference}_id_map` | provider identity → canonical | — | — | populated |
| `metadata.source_manifest`, `metadata.data_versions` | file / build × table | — | — | populated |
| `qa.exceptions`, `qa.reconciliation` | issue / cross-source comparison | — | — | populated |
| `qa.season_summary_conflicts` | raw summary value ≠ game-derived value, per (team, season, field) | — | teams | populated |
| `qa.player_season_summary_conflicts` | same, per (player, team, season, field), with `identity_complete` and authority | — | players, teams | populated |

The superseded Stage-1 directive also named `substitutions_raw`, `lineup_stats`, `recruiting`, `transfers`, `venues`, `source_id_map`, and `qa_exceptions`. These map as follows:
- `substitutions_raw` → raw CBBD payloads plus `fact.substitutions`. CBBD rows are already athlete-centric; the paired `substitution_events` are Stage 4.
- `lineup_stats` → `fact.lineup_stats`.
- `recruiting` / `transfers` → `core.recruits` / `core.transfers`.
- `venues` → `core.venues`.
- `source_id_map` → the four `metadata.*_id_map` tables.
- `qa_exceptions` → `qa.exceptions`.

## 4. Units and null semantics

- **Season** = CBBD convention, the calendar year the season **ends** (`2025` = 2024-25; measured on all 272,560 game rows). BDL seasons are start-year and are joined as `bdl_season + 1`.
- **Timestamps** are `TIMESTAMPTZ` in UTC. `game_date_et` is the US/Eastern calendar date. Unknown times stay NULL and are never invented.
- **Counting stats** are integers. **Minutes** are whole minutes as supplied (the source rounds). `game_minutes` = 40 + 5 × overtime periods.
- **Percentages are not canonical columns.** They are derived from made/attempted and are NULL (undefined) when the denominator is 0. Supplied summaries such as `usage_source` and `pace_source` keep a `_source` suffix and are for QA only.
- **NULL means unknown, never zero.** A zero is stored only when the source states zero.
- **Rebounds:** team `orb/drb/reb` **include team rebounds** (source semantics), so player sums are ≤ team totals.
- **Turnovers:** team `tov_total` **includes** team turnovers; `team_turnovers` is the part not credited to a player. Measured: Σ player TOV = `tov_total − team_turnovers` in 99.84% of team-games.

## 5. Lineage columns (every core/fact row)

`source_system` (e.g. `CBBD_STARTER_PACK`, `CBBD_API`), `source_file` (relative path), `source_record_key` (the provider key of the exact source record, e.g. `game_id=14;athleteId_record=467`), `ingestion_version`, `received_at`, `processed_at`. Benchmark rows carry `raw_sha256` and `received_at` of the raw page they came from. `processed_at` is the only column that legitimately varies between rebuilds.

**Trace example (real row; human mastery checkpoint):**

1. Source: `data/player_game_stats/2025.csv`, row `game_id=14, athleteId=467, team_id=236 (Purdue), name="Braden Smith", minutes=35, points=12, starter=True`.
2. Staging: `staging.sp_player_game_stats` shows the same values verbatim, with `filename = data/player_game_stats/2025.csv`.
3. Identity: `athleteId=467` is a per-game record id (§6). The composite key `2025|236|Braden Smith` matches exactly one persistent CBBD athlete id, `368`, in `player_season_stats` (`metadata.player_id_map`, method `EXACT_TEAM_SEASON_NAME`). `368` is the anchor of `P-24fcff8bc37e02c8`.
4. Game: CBBD game `14` → `G-c7ea584e0f911a7c`. The tip time is `2024-11-04 23:00Z` from games.csv read as UTC, quality `EXACT`.
5. Canonical: `fact.player_games` row `PG-0211791f82fbd6de6a22`, `identity_status = RESOLVED`, `minutes = 35`, `points = 12`, `event_time = 2024-11-04 23:00Z`, `historical_knowable_at = 2024-11-05 03:00Z` (PIT rule HR-1), `source_record_key = game_id=14;athleteId_record=467`.
6. Participation: `fact.participation` = `PLAYED / BOX_ROW_POSITIVE_MINUTES / REGULAR`.

## 6. Identity resolution

| Provider identity | Method | Confidence | Manual review |
|---|---|---|---|
| CBBD game / team / conference / venue id | `ANCHOR` (hash of the CBBD id) | 1.0 | no |
| CBBD persistent athlete id (season files) | `ANCHOR` | 1.0 | no |
| Starter Pack player-game line `(season, team_id, exact name)` | `EXACT_TEAM_SEASON_NAME` → athlete id. Unique match required; exact string equality only, **no fuzzy matching** | 1.0 | only when unresolved on a D-I team-season |
| BDL team | `EXACT_NAME`: BDL `full_name` = CBBD `display_name` (0.99), else BDL `college` = CBBD `school` (0.95) | 0.99 / 0.95 | if unresolved |
| BDL game | `TEAMS_AND_ET_DATE`: unordered mapped team pair + US/Eastern date | 1.0 | if unresolved |
| BDL player | `EXACT_NAME_WITHIN_GAME_TEAM` (0.98); `EXACT_NAME_TEAM_SEASON` for BDL non-participant rows (0.95); `NORMALIZED_NAME_WITHIN_GAME_TEAM` (case/punctuation/Jr.-III suffix) (0.85) | as listed | yes for normalized/unresolved |
| Sharp Stats (`sharp_stats`) team, player | exact CBBD school/display name; exact name among players whose most recent season team matches → `RESOLVED_NEEDS_REVIEW_PRIOR_SEASON_TEAM_EXACT_NAME` until current rosters exist | — | always |

Key measured facts behind these rules (see `docs/STARTER_PACK_PHYSICAL_AUDIT.md` §5):

- **`player_game_stats.athleteId` is not a player id.** It is unique per player-game record: 127,331 distinct values for 127,331 rows in 2025. Joining it to season-file athlete ids creates thousands of coincidental false matches. This contradicts DATA_REQUIREMENTS §28 ("persistent `athleteId`") **for the Starter Pack export**. The architect ruled it non-persistent/unsafe for the Starter Pack. The live CBBD `/games/players` athleteId is a **separate question** with its own test harness (`cbb_platform/cbbd_identity.py`). It was measured live and classified `PERSISTENT_PLAYER_ID`, in the **same id space as the Starter Pack season files**. It stays constant across games, seasons and transfers (Auburn → Alabama, Rutgers → Alabama), and it agrees with `/teams/roster` ids. See `docs/CBBD_PLAYER_IDENTITY_VERIFICATION.md`. So the API id may anchor players directly, while the Starter Pack `player_game_stats.athleteId` remains a record id.
- **Unresolved player-game rows** stay `canonical_player_id = NULL` and appear in `data/qa/unresolved_identities.csv`:
  - `UNRESOLVED_NO_MATCH`: no season-stats row, almost always a non-D-I team.
  - `UNRESOLVED_AMBIGUOUS`: two athletes share a name in one team-season, or two records share a name in one team-game.
  - `UNRESOLVED_SAME_PLAYER_BOTH_TEAMS`: a measured source defect where one team's box lines are mirrored onto the opponent. Both copies are left unresolved because the source cannot say which side is true.
- **Future Torvik/KenPom identities** would enter `metadata.player_id_map` / `team_id_map` under their own `provider` value with the same method/confidence/review fields. Nothing is built for them in Stage 1 (optional, `DEFERRED_NON_BLOCKING`).

## 7. QA rules and frozen thresholds

- **Accounting identities** (MODEL_ARCHITECTURE_FREEZE §3) on player-games, team-games, and both season-stats tables:
  - `PTS = 2·2PM + 3·3PM + FTM`, `REB = ORB + DRB`, `FGM = 2PM + 3PM`, `FGA = 2PA + 3PA`;
  - makes ≤ attempts; no negative counts; for players, `0 ≤ minutes ≤ game_minutes`.

  Source rows that violate an identity are **not corrected and not deleted**. They carry `accounting_issues` (a `;`-list of failed rules; NULL means clean). Tests recompute every identity independently and require:
  1. zero violations among unflagged rows;
  2. every violation is flagged;
  3. flagged share ≤ **0.01%** of player-games and ≤ **0.1%** of team-games.

  These implementation-level thresholds were frozen in Stage 1 *after* measuring 0.004% and 0.058%. Season aggregates have no rate threshold because their inconsistency is structural (§9).
- `fact.team_games.player_box_reconciles` = player point sum equals team points **and** player minutes are within 5 of 5 × `game_minutes`. It is FALSE for 2,448 team-games and NULL where no player box exists. Downstream absence modelling should treat non-reconciling team-games as incomplete boxes.
- Cross-source: CBBD vs BDL disagreements are written to `qa.reconciliation` and `qa.exceptions` (`BDL_MISMATCH`). CBBD values are never overwritten.

## 8. Participation (DNP truth states)

`fact.participation` has one row per candidate player × team × game, built from two candidate sources:

| Candidate source | State | Evidence code |
|---|---|---|
| box-score row with minutes > 0 | `PLAYED` | `BOX_ROW_POSITIVE_MINUTES` |
| box-score row, starter, 0 minutes (contradictory line; architect ruling) | `UNKNOWN_PARTICIPATION`. Never a confirmed PLAYED or DNP training label unless another reliable source confirms | `FLAGGED_ZERO_MIN_STARTER`, or `FLAGGED_ZERO_MIN_STARTER_WITH_STATS` when a counting stat > 0. 322 rows (240 with stats) |
| box-score row, non-starter, 0 minutes, some counting stat > 0 | `PLAYED` | `BOX_ROW_ZERO_MINUTES_WITH_STATS` |
| box-score row, 0 minutes, no stats, not starter | `UNKNOWN_PARTICIPATION` (whole-minute rounding cannot separate a listed DNP from a <30-second appearance) | `BOX_ROW_ZERO_MINUTES_NO_STATS_AMBIGUOUS` |
| player with a season-stats row for that team-season but no resolved box row in a game where the team has a player box | `UNKNOWN_PARTICIPATION` | `NO_RESOLVED_BOX_ROW_SEASON_MEMBER_NO_ROSTER` |
| explicit roster / scheduled-player universe plus explicit non-participation evidence | `CONFIRMED_DNP` | requires `candidate_source = ROSTER`; **0 rows in Stage 1** because no roster source exists |

- `starter` is orthogonal to participation and is NULL when there is no box row.
- `minutes_band`: `ZERO`, `MINIMAL` (1–4), `REGULAR` (≥ 5).
- `interruption_flag` is TRUE only with evidence (`FOULED_OUT_PF_GE_5`). Otherwise it is NULL (unknown), because the source `ejected` field is constant FALSE in all 2,460,230 rows and records no injury exits.
- **No medical reason is ever inferred.** There is no injury column in this table.
- This answers the later absence question ("when Player A did not participate, how were minutes/usage redistributed?") without pretending to know *why*. `tests/test_participation.py::test_absence_question_is_answerable` runs that query shape.
- BDL lists non-participating roster members (NULL minutes) in its player stats. That is a candidate future source of `CONFIRMED_DNP` evidence for the seasons BDL covers; it is not used in Stage 1.

## 9. Source semantics resolved by measurement

| Issue | Measurement | Canonical rule |
|---|---|---|
| `games.csv.start_date` has no offset; box files say `+00:00` | Box stamp = games.csv **re-read as US/Eastern** for 117,827/117,827 games. BDL's independent UTC tip = games.csv **read as UTC** for 804/804 games (2023-24, 2024-25, both DST regimes) | games.csv is naive UTC. The box-score `+00:00` stamps are 4–5 h late and are **not used** (they would push pregame cutoffs past tip = leakage). The 00:00 UTC peak is a 7 pm EST tip, not a placeholder |
| Duplicate keys in every game/season family | Rows inside each duplicate group differ **only** in `conference` / `opponent(_)conference` | Collapse to one row (deterministic tie-break); conference taken from per-game ids; staging keeps all rows; `DUPLICATE_FANOUT_COLLAPSED` exceptions |
| Same player listed for both teams in one game | 154 player pairs, mostly 2017 | both copies `UNRESOLVED_SAME_PLAYER_BOTH_TEAMS` |
| Team-season `points` vs its own shot primitives | inconsistent in 1,673 / 7,265 rows, concentrated in low-box-coverage seasons 2005–07 and 2012–13. **Root cause (measured):** the summary's `games` and `points` count every game with a final result (7,265 / 7,265 rows match `core.games`), while every shot/rebound/turnover primitive sums only the games with a team box (no primitive conflicts at all) | rows still flagged `PTS_IDENTITY`; raw summaries preserved unchanged. **Game-level tables are authoritative**: `fact.team_season_game_derived` keeps the two bases separate and wins on conflict, and every conflict is in `qa.season_summary_conflicts` (`docs/SEASON_STATISTICS_AUTHORITY.md`) |
| BDL NULL counting stats | NULL where CBBD has 0 for players with minutes | compared as 0 in reconciliation only; benchmark rows keep NULL |
| Pre-2003 game times | date-only strings | `tip_time_quality = DATE_ONLY`, `scheduled_tip_utc = NULL` |
| CBBD PBP `wallclock` (live smoke) | stamped `Z` but is US/Eastern wall time: first play −4.96 h / −4.91 h before the UTC tip | stored verbatim in `fact.plays.raw_wallclock` (VARCHAR); **never** used as a UTC instant (POINT_IN_TIME_POLICY §1) |
| CBBD feed gaps and defects (live smoke) | 2024-25 opener (game 14): substitutions present, but **no PBP and no lineups**. 2018-19 game: no PBP, substitutions or lineups. Game 5882: 30 plays with 6 Samford players on the floor, which propagates into Samford's lineups (3,390 s vs 2,400; 70 lineup points vs 45 final) | ingested as supplied, never repaired; onFloor states and lineup reconciliation are measured per game (`docs/CBBD_LIVE_SMOKE_RESULTS.md`). Stage 4 stint reconstruction must validate per game |

## 10. Source field disposition (Stage 1 gate: every field mapped or explicitly ignored)

Every Starter Pack column remains available losslessly in `staging.sp_*`. Canonical tables take:

- **games.csv:**
  - `id`, `season`, `season_label`, `season_type`, `game_type`, `tournament`, `notes`, `start_date`, `neutral_site`, `conference_game`, home/away team id, conference id, seed, points, period points, venue fields → `core.games` / `core.venues` / `core.team_seasons`.
  - Explicitly not canonicalized: team/conference **names** (they live on core.teams/conferences) and `home_winner`/`away_winner` (derivable from points).
- **player_game_stats:**
  - primitives, `starter`, `minutes`, `ejected`, `position`, `name`, `usage` → `fact.player_games`.
  - Explicitly not canonicalized (derivable or vendor summaries, kept in staging): all `_pct` columns, `offensiveReboundPct`, `freeThrowRate`, `assistsTurnoverRatio`, `gameScore`, `trueShootingPct`, `effectiveFieldGoalPct`, ratings, `game_pace`, `game_minutes` (taken from the team row), seeds, conference names, `start_date` (wrong offset; see §9).
- **team_game_stats:**
  - team primitives, turnovers (total + team), fouls (total, technical, flagrant), points split (paint, fast break, off turnovers, largest lead, by period), possessions, pace, `gameMinutes` → `fact.team_games`.
  - Not canonicalized: `opponentStats_*` (the opponent's own row is the source of truth; DATA_REQUIREMENTS §7), percentages, four-factors, ratings, `gameScore`, `startDate` (§9).
- **player/team season stats and shooting:** primitives and shot-family made/attempted/assisted → `fact.*_season_*`. Percentages, attempt-mix shares, win shares, and ratings are not canonicalized.
- **teams.csv / conferences.csv:** all fields except team colours.

## 11. Market isolation

Sportsbook data exists only in `benchmark.game_odds` and `benchmark.opening_odds`, with BDL `updated_at` → `source_updated_at` and `opened_at` preserved. `flagship.*` views select only from `core`, `fact`, and `availability`, and `tests/test_market_isolation.py` enforces four things:
1. no market-vocabulary column in `flagship`/`core`/`fact`/`availability`;
2. no flagship view references `benchmark`;
3. the flagship namespace contains only views;
4. a positive control proves the detector catches every real odds column.

## 12. Stage 2 event layer (CBBD PBP, onFloor, shots, substitutions, lineups, rosters, recruiting, portal, venues)

Built by `cbb_platform/event_build.py` on every canonical build, from immutable raw payloads only. Endpoint choice,
dedup keys and equivalence evidence: `docs/CBBD_BACKFILL_STRATEGY.md`. Measured coverage: `docs/CBBD_EVENT_COVERAGE_DISCOVERY.md`
(sample), `data/qa/cbbd_event_coverage_by_season.csv` (full backfill).

| table | grain | notes |
|---|---|---|
| `fact.plays` | one CBBD event (`source_play_id` = CBBD play id, globally unique) | `play_sequence` = source id order; `on_floor_quality` classifies the raw onFloor list; `raw_wallclock` verbatim VARCHAR, never a UTC instant; `duplicate_copies` = payload copies collapsed on (gameId, play id) |
| `fact.play_participants` | one participant entry of a play, in source order | roles are not typed by CBBD |
| `fact.play_on_floor` | one onFloor entry of a play, in source order | `side` HOME/AWAY from the entry's team name vs the game's home/away names; UNKNOWN otherwise. Raw entries are never added, removed or reordered |
| `fact.shots` | one CBBD shotInfo | shooter/assister ids; `location_valid` (0..940 x 0..500 frame); `shooter_on_team_floor` only when the play's floor is VALID_5V5 |
| `fact.substitutions` | one athlete stint as supplied | athlete-centric; `stint_number` = order of the athlete's stints |
| `fact.substitution_pairs` | one substitution instant per team | DERIVED. A pair only for exactly one OUT and one IN at the instant; otherwise `ambiguous_flag` with the athlete lists and no pairing |
| `fact.lineup_stats` | one valid five-man unit per team-game | exactly five distinct athlete ids; invalid units go to `qa.lineup_units_quarantine` |
| `core.rosters` | athlete × team × season | CBBD athleteId → `cid('P','athlete', id)`; roster-only athletes are ADDED to `core.players` (existing ids untouched) |
| `core.recruits`, `core.team_recruiting_rankings`, `core.transfers`, `core.venue_directory` | provider records | recruit identity only via the provider's athleteId; portal identity via roster corroboration (origin roster + destination roster next season); candidates carry `needs_manual_review` |
| `qa.source_defects` | one defect | family + reason code; `excluded_from_trusted` removes the record (or game) from `flagship.*` trusted views |
| `qa.pbp_game_reconciliation` | one game with PBP | final score, score decreases, increment mismatches, periods, terminal event, clock order; official scores are never modified |

On-floor quality classes (first failing rule wins; `cbb_platform/event_metrics.py`, unit tested against the SQL):
`NO_ONFLOOR`, `UNRESOLVED_PLAYER`, `PLAYER_ON_BOTH_TEAMS`, `DUPLICATE_PLAYER`, `OTHER_INVALID`, `MISSING_HOME`,
`MISSING_AWAY`, `MORE_THAN_5`, `LESS_THAN_5`, `VALID_5V5`.

Trusted views: `flagship.plays` (games without PBP-level defects), `flagship.on_floor_trusted` (VALID_5V5 plays of trusted
games), `flagship.shots_trusted`, `flagship.substitutions`, `flagship.lineup_stats`, `flagship.rosters`.
PIT: every event row carries the game's `event_time` / `historical_knowable_at` (rule HR-1); event-level times are not trusted.

## 13. Stage 2.1 canonical game identity (one real contest = one trusted game)

* `core.games` keeps every source record unchanged (raw truth).
* `metadata.game_alias_map` (built by `cbb_platform/game_identity.py` inside every build) gives each game of every
  candidate pair an explicit class: `CANONICAL`, `DUPLICATE_ALIAS` (-> `resolved_canonical_game_id`),
  `SOURCE_CORRUPT_MISATTRIBUTED` (excluded, resolves to nothing) or `LEGITIMATE_DISTINCT_GAME`, with method,
  evidence and confidence. Unadjudicated POSSIBLE/UNRESOLVED pairs, or evidence contradicting a ruling, STOP the build.
* `metadata.trusted_game_map` maps every source game id of the audited window to its trusted game id.
* `flagship.games` is THE trusted game relation: each real contest exactly once, seasons 2004-05..2024-25 only.
  Every flagship fact view and every game-derived aggregate (`fact.team_season_game_derived`,
  `fact.player_season_game_derived`) and the PIT primitives (`cbb_platform/pit.py`) read through it.
* After all facts are built, `assert_fact_safety` stops the build if any fact would be lost (fact on a corrupt record)
  or duplicated (same fact on an alias and its survivor).
* Reports: `docs/GAME_IDENTITY_RESOLUTION.md`, `data/qa/game_identity_resolution.csv`,
  `data/qa/game_identity_aggregate_impact.csv`.
* CBBD raw wallclock is `UNTRUSTED_MIXED` (architect ruling, closed): preserved verbatim, never an authoritative time.
