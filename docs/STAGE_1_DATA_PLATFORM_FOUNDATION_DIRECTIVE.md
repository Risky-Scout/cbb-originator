# STAGE_1_DATA_PLATFORM_FOUNDATION_DIRECTIVE.md
## CBB Originator — Claude Code / ChatGPT Codex Implementation Directive
### Stage 1: Canonical Schema + PIT Contract + Data Platform Smoke Build
**Date:** October 4, 2026

## Mission

Build the first executable data-platform foundation for the CBB Originator Interconnected Player Prop Predictive Model.

This stage must convert the frozen planning documents into a concrete canonical schema, point-in-time policy, database/storage scaffold, source adapters, and representative smoke ingestions.

**Do not train any predictive model in this stage.**

## Absolute repository safety

1. Operate only inside the CBB Originator model repository/worktree.
2. Before changing anything, print:
   - repository root
   - current branch
   - HEAD SHA
   - `git status --short`
3. If the repository cannot be unambiguously identified as the CBB Originator project, **STOP**.
4. Do not modify sibling repositories, shared football/basketball runtimes, or any other sports model.
5. Do not merge, deploy, or push unless explicitly instructed in a later directive.
6. Do not commit API keys, session cookies, credentials, or paid-source content.

## Authoritative project documents

Read these first and treat them as contracts:

- `docs/MODEL_ARCHITECTURE_FREEZE.md`
- `docs/DATA_SOURCE_POLICY.md`
- `docs/SOURCE_MATRIX.md`
- `docs/DATA_REQUIREMENTS.md`
- `docs/PROBABILITY_ENGINE_POLICY.md`
- `docs/PROJECT_EXECUTION_ORDER.md`
- `reference/CBBD_OPENAPI_VERIFICATION_v1_1.md`
- `reference/CBBD_API_FIELD_INVENTORY_AND_CROSSWALK_v1_1.md`
- `reference/BDL_NCAAB_API_AUDIT.md`

If they are not yet present in the repo, copy the supplied frozen package into the repo's project-docs/model-docs area **without altering their contents**, then record their hashes.

## Frozen source responsibilities

- **CBBD:** P0 basketball backbone.
- **Sharp Stats:** P0 current-season availability.
- **BDL:** P1 independent QA + prospective market benchmark.
- **Bart Torvik:** P1 non-blocking enrichment.
- **Odds API:** fallback market benchmark only.
- **Historical injury archive:** P2, not required.
- **Historical player props:** not required for v1.

Do not redesign this assignment.

## Stage 1 deliverables

### A. `CANONICAL_SCHEMA.md`

Define exact table grain, primary keys, foreign keys, units, null semantics, lineage fields, and source-ID maps for at minimum:

- `games`
- `teams`
- `conferences`
- `players`
- `rosters`
- `player_games`
- `team_games`
- `plays`
- `shots`
- `substitutions_raw`
- `substitution_events` (canonical paired/derived structure)
- `lineup_stats`
- `lineup_stints`
- `possessions`
- `availability_events`
- `recruiting`
- `transfers`
- `venues`
- `benchmark_game_odds`
- `source_id_map`
- `source_manifest`
- `qa_exceptions`

The canonical model must use internal IDs and preserve provider IDs separately.

### B. `POINT_IN_TIME_POLICY.md`

Define what information is legal for a prediction at:
- T-24h
- T-6h
- T-1h
- T-10m
- final pre-tip snapshot

Mandatory rules:
- no post-tip information;
- no final-season aggregate used for an earlier game;
- date-bounded CBBD aggregates must stop before the prediction cutoff;
- Sharp Stats `received_at_utc` is the authoritative exact availability receipt time;
- market data is benchmark-only and excluded from originator feature views;
- corrected/revised source data may not appear in a historical replay earlier than it was actually received if receipt history exists.

### C. Storage architecture

Create:

```
data/
  raw/
    cbdd/
    bdl/
    sharp_stats/
    torvik/
  canonical/
  manifests/
  qa/
  features/
    pregame/
    live/
```

Rules:
- raw payloads are immutable;
- canonical tables are deterministic transforms;
- source manifests record retrieval time, endpoint/source, request parameters, row counts, hashes, schema version, and code version;
- never overwrite a raw source snapshot.

### D. DuckDB + Parquet scaffold

Create an analytical database, e.g.:

`data/canonical/cbb.duckdb`

with schema namespaces or clearly separated tables/views such as:

- `core.*`
- `fact.*`
- `availability.*`
- `enrichment.*`
- `benchmark.*`
- `metadata.*`
- `qa.*`

Market benchmark tables must be physically/logically isolated so they cannot accidentally enter the pure-originator feature pipeline.

### E. Source adapters

Implement source interfaces with deterministic serialization and testability.

#### CBBD adapter
Environment credential only, e.g.:
`CBBD_API_KEY`

Support representative calls for:
- games
- player game stats
- team game stats
- PBP
- substitutions
- lineups
- rosters

The adapter design must accommodate all P0 endpoints in `SOURCE_MATRIX.md`.

#### BDL adapter
Environment credential only:
`BDL_API_KEY`

Support representative calls for:
- games
- player_stats
- team_stats
- players/active
- odds
- odds/opening

Normalize BDL's season convention explicitly.

#### Sharp Stats adapter
Do **not** build an automated scraper unless an explicitly permitted automated access method is already available.

For Stage 1, implement an adapter capable of ingesting user-provided Sharp Stats page snapshots exported as text/HTML/CSV into canonical `availability_events`.

Requirements:
- retain raw status;
- retain raw note;
- normalize status;
- derive conflict flags;
- preserve update date;
- record exact ingestion/receipt timestamp;
- append, never overwrite.

#### Bart Torvik adapter
Non-blocking.

Implement:
- schema/interface;
- parser fixture from a supplied sample if available;
- ID/date/team mapping contract.

Do not make the stage fail if no stable/permitted automated retrieval mechanism exists.
Record `DEFERRED_NON_BLOCKING` instead.

### F. Identity mapping

Create deterministic source-ID maps:

- canonical team ↔ CBBD team ↔ BDL team ↔ Sharp Stats team name ↔ Torvik team
- canonical player ↔ CBBD athlete ID ↔ BDL player ID ↔ Sharp Stats player name/team ↔ Torvik player/team/date identity

Rules:
- never join solely on normalized player name without team/season context;
- retain mapping confidence and method;
- unresolved ambiguous identities go to `qa_exceptions`;
- no silent fuzzy-match acceptance below the documented threshold.

### G. Historical participation / DNP representation

Create the schema and derivation contract for one candidate player-game record containing:
- game
- player
- team
- roster eligibility/candidate status when reconstructable
- `played_flag`
- `dnp_flag`
- starter
- minutes
- previous-role features (not computed yet unless needed for smoke test)

This becomes P0 historical absence evidence.

Do not require historical injury diagnoses.

### H. Smoke ingestion

Do **not** launch the full historical backfill yet.

Use a small representative sample:
- at least 2 completed games with player/team stats;
- at least 1 game with PBP;
- at least 1 game with substitutions/on-floor/lineup data if available;
- one Sharp Stats sample snapshot supplied by the user;
- matching BDL player/team/game records if credentials permit;
- matching BDL odds/opening odds if available.

Purpose:
prove schemas, joins, raw preservation, and provider adapters before expensive historical acquisition.

### I. QA tests

At minimum:

#### Identity
- no duplicate canonical game IDs;
- team mappings deterministic;
- player mappings include source/confidence;
- unresolved matches surfaced.

#### Box score
- `PTS == 2*2PM + 3*3PM + FTM` where primitives exist;
- `REB == ORB + DRB`;
- makes <= attempts;
- player/team score reconciliation where possible.

#### BDL reconciliation
Compare overlapping CBBD vs BDL:
- game score;
- player minutes/PTS/rebounds/assists/3PM/etc.;
- team totals.

Report disagreements; do not overwrite.

#### Sharp Stats
Test:
- Probable/Questionable/Doubtful/Out/Out Season normalization;
- raw note preservation;
- status-note contradiction creates a conflict flag;
- duplicate snapshot ingestion is idempotent;
- changed snapshot produces a new append-only event.

#### Market isolation
Automated test must prove benchmark odds columns are inaccessible from the default pure-originator feature view/module.

#### Raw immutability
Re-running the same fetch/snapshot does not mutate prior raw artifacts.

## Explicit non-goals

Do not:
- train baseline models;
- build latent player talent;
- tune feature importance;
- implement the joint simulator;
- scrape Bart Torvik at scale;
- backfill historical injury reports;
- fetch historical player props;
- ingest dense historical line movement;
- implement the in-play model;
- deploy anything.

## Acceptance gate

Stage 1 passes only if:

1. `CANONICAL_SCHEMA.md` exists and maps every Stage 1 source.
2. `POINT_IN_TIME_POLICY.md` exists and is enforceable.
3. DuckDB/Parquet scaffold is functional.
4. raw ingestion is immutable/idempotent.
5. CBBD smoke data lands in canonical tables.
6. Sharp Stats sample lands in `availability_events`.
7. BDL smoke reconciliation/benchmark works if credentials are available; otherwise code/tests are ready and status is `BLOCKED_CREDENTIAL`, not fabricated.
8. Bart Torvik is either parsed from a supplied sample or explicitly `DEFERRED_NON_BLOCKING`.
9. market-isolation test passes.
10. no predictive model is trained.
11. no other repository is modified.
12. all tests pass or the stage stops as BLOCKED/FAIL with exact evidence.

## Final report format

Return exactly:

```
STAGE: Data Platform Foundation — Stage 1
STATUS: PASS / BLOCKED / FAIL

REPO_ROOT:
BRANCH:
HEAD_SHA_BEFORE:
HEAD_SHA_AFTER:
SOURCE_PROJECT_MODIFIED: TRUE/FALSE

FILES_CREATED:
FILES_MODIFIED:

CONTRACTS:
CANONICAL_SCHEMA: PASS/FAIL
POINT_IN_TIME_POLICY: PASS/FAIL
MARKET_ISOLATION: PASS/FAIL

SMOKE_INGEST:
CBBD:
BDL:
SHARP_STATS:
BART_TORVIK:

IDENTITY_MAPPING:
BOX_SCORE_RECONCILIATION:
BDL_RECONCILIATION:
SHARP_STATS_CONFLICT_TESTS:
RAW_IMMUTABILITY:

TESTS_RUN:
TEST_RESULTS:

ACCEPTANCE_CRITERIA:
1. PASS/FAIL — ...
...

DATA_QUALITY_FINDINGS:
UNRESOLVED_ISSUES:
BLOCKERS:
ASSUMPTIONS_MADE:

READY_FOR_STAGE_2_FULL_INGESTION: YES/NO

NEXT_ACTION:
```

If any P0 requirement is ambiguous, do not improvise. Stop and report the exact ambiguity.
