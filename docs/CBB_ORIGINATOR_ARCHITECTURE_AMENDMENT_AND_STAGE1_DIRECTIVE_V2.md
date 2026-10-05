# CBB Originator — Authoritative Architecture Amendment + Stage 1 Directive v2

**Date:** October 5, 2026  
**Status:** AUTHORITATIVE FOR NEXT IMPLEMENTATION  
**Scope:** Pregame CBB Originator only  
**Implementation mode:** Governed autonomy, simplicity-first, no drift  
**Primary implementation agent:** Claude Code / equivalent coding agent  
**Human operator goal:** Every important transformation, assumption, test, and result must remain understandable and auditable by a technically serious human.

---

# PART I — AUTHORITY, MISSION, AND OPERATING RULES

## 1. Mission

Build a production-grade NCAA Division I men's basketball originator that produces one coherent, point-in-time joint probability distribution for the entire game:

\[
P(\mathbf{Y}_{game}\mid \mathcal{I}_t)
\]

where \(\mathcal{I}_t\) is the information legitimately available at prediction time \(t\).

The flagship system must not be a collection of independent player prop regressions. Player and team outcomes must arise from one conditioned basketball state in which finite minutes, possessions, shot attempts, rebound opportunities, assist opportunities, turnovers, fouls, lineups, and game environment are shared and reallocated coherently.

When a material event occurs — for example, a player being ruled out — the model must ultimately propagate that information through:

\[
\text{availability}
\rightarrow
\text{minutes}
\rightarrow
\text{rotation/lineups}
\rightarrow
\text{usage/opportunities}
\rightarrow
\text{event probabilities}
\rightarrow
P(\mathbf{Y}_{game})
\]

This amendment does **not** authorize predictive-model implementation during Stage 1. Stage 1 exists to build the trustworthy data platform that makes the later model possible.

---

## 2. Authority and precedence

This document is the authoritative amendment for decisions made after the original October 4, 2026 governance freeze.

When this document conflicts with an older project document, this document supersedes the older document **only on the explicitly amended topics listed here**.

All older documents remain authoritative on topics not amended here.

### Explicitly amended topics

1. current-season availability source role;
2. minimum-provider / CBBD-first principle;
3. KenPom and Bart Torvik roles;
4. subsystem-specific historical windows;
5. exact-lineup augmentation policy;
6. continuous point-in-time history and five prediction checkpoints;
7. game-universe contract;
8. canonical realized outcome;
9. validation and lockbox policy;
10. cold-start policy;
11. production recomputation SLA;
12. governed implementation autonomy;
13. simplicity, interpretability, file-count discipline, and no-side-quest rule;
14. DNP / participation truth-state requirements;
15. coverage-audit artifact hierarchy.

Do not silently edit older documents to make them appear historically consistent. This amendment is the explicit record of the change.

---

## 3. Simplicity and no-drift engineering rule

Use the **simplest human-readable implementation that correctly satisfies the frozen statistical and data contracts**.

Complexity must earn its place.

### Required behavior

- Prefer straightforward Python over clever abstractions.
- Prefer small, named transformations over opaque pipelines.
- Prefer functions and simple modules over deep class hierarchies.
- Use descriptive variable names.
- Add comments where basketball meaning, PIT meaning, data provenance, or a non-obvious decision must be understood.
- Add docstrings to important interfaces.
- Use tests as executable documentation.
- Keep production logic out of notebooks.
- Do not create side projects, dashboards, scrapers, orchestration systems, or model experiments that are not required by the current stage.
- Do not add a provider because it is interesting or convenient.
- Do not add a complex statistical method because it is sophisticated.
- Do not silently weaken a QA gate to make tests pass.
- Do not fabricate missing information.

### File-count discipline

Create only files required to make the current stage reproducible, testable, and understandable.

Do not create:
- duplicate documentation files;
- redundant versioned and unversioned copies;
- one-off notebooks containing unique production logic;
- speculative configuration files;
- unused adapters;
- placeholder frameworks for future stages.

If two valid implementations are equivalent, choose the one with fewer moving parts.

---

## 4. Governed autonomy

Routine engineering decisions may be made without owner approval if they remain inside frozen contracts.

This includes:
- module organization;
- canonical ID mechanics;
- DuckDB/Parquet implementation details;
- partitioning;
- schema validation tooling;
- adapter internals;
- hashing/versioning;
- caching;
- test layout;
- deterministic rebuild mechanics;
- implementation-level QA thresholds where no architecture document prescribes one.

### STOP and return for owner review if

- a frozen architectural decision must change;
- a P0 source assumption is empirically false;
- an implementation would require fabricated data;
- a PIT rule would be weakened;
- the flagship prediction universe would change;
- canonical outcome semantics would change;
- sportsbook isolation would change;
- a core provider would be added or substituted;
- the 2025-26 lockbox policy would change;
- a QA/readiness gate would be materially relaxed;
- proceeding would push a known upstream defect downstream.

---

# PART II — FROZEN ARCHITECTURE AMENDMENTS

## 5. Provider roles

### CBBD — foundational basketball source

CBBD is the primary basketball backbone.

Use CBBD first when it provides the required raw basketball information.

Do not add another provider merely because it offers a convenient derived statistic that can be constructed defensibly from CBBD.

CBBD is expected to provide or support:
- games and schedules;
- teams and conferences;
- rosters and identity;
- player game outcomes;
- team game outcomes;
- PBP where available;
- on-floor information where available;
- substitutions where available;
- lineups where available;
- shooting;
- recruiting;
- transfers;
- venues and other basketball context.

Use CBBD intentionally. Do not redownload large historical datasets already present merely for convenience.

### CBB Starter Pack — research/prototyping input, not production dependency

The Starter Pack is immutable and read-only.

It may be used for:
- physical historical audit;
- Stage 1 canonicalization;
- research/prototyping;
- schema validation;
- historical QA.

The commercial production system must remain rebuildable from appropriately licensed production providers without requiring Starter Pack files.

### BALLDONTLIE NCAAB — independent QA + benchmark

Use BDL for:
- independent game reconciliation;
- player box-score reconciliation;
- team box-score reconciliation;
- active-player QA;
- prospective opening/current sportsbook benchmark data.

BDL market data is benchmark-only and may never enter the flagship basketball feature namespace.

### Covers — preferred current-season availability source

Covers is the preferred operational current-season CBB availability/injury source.

Capture prospectively and preserve:
- source player name;
- source team;
- position if present;
- raw injury/reason;
- raw status;
- raw update date/time if present;
- raw narrative note;
- our exact `received_at`;
- raw source snapshot/reference;
- parsing/version metadata.

Do not require a historical medical archive for the core model.

### StatSharp — secondary/provisional availability QA

StatSharp is no longer trusted as the sole P0 availability source.

It may be used for:
- secondary QA;
- supplementary current-season status context;
- fixture/parser validation.

Do not architect the production availability system around StatSharp.

### KenPom — optional team-strength QA/enrichment

KenPom is optional.

Do not require KenPom to construct the core team-strength state.

Use it only if:
- independent QA is useful; or
- later chronological validation demonstrates incremental value that cannot be replicated cleanly from the CBBD foundation.

### Bart Torvik — optional player-level QA/enrichment

Torvik is optional and non-blocking.

Do not require Torvik for:
- historical DNP construction;
- core player strength;
- core shooting state;
- the deep-history model.

Only integrate it later if a measured CBBD gap or chronological incremental-value test justifies it.

### Minimum-provider principle

\[
\boxed{\text{If CBBD provides the raw information needed, use CBBD and derive the state ourselves.}}
\]

---

## 6. Historical-window architecture

Different subsystems may use different admissible historical windows.

Historical depth is determined by the measured availability and quality of the data required by that subsystem, not by the youngest data source in the entire model.

### Deep-history core

Use the deepest defensible history for:
- team offense/defense;
- opponent-adjusted strength;
- pace;
- Four Factors;
- player talent;
- player role;
- usage;
- minutes;
- starter probability;
- rebounding;
- passing/creation;
- turnover/foul profiles;
- recent form;
- shot-family state where supported;
- absence/minutes redistribution where participation quality is adequate.

### Exact-lineup augmentation

Exact substitutions, on-floor states, stints, and five-man effects are a separate augmentation layer.

Initial assumption:
- modern exact-lineup/substitution history begins around 2024;
- earlier games are admitted only if they pass the same strict measured QC gate.

Never manufacture historical exact lineups from:
- minutes;
- starters;
- incomplete PBP;
- guessed substitution sequences.

Use clearly distinct namespaces:

- `rotation_proxy_*`
- `absence_response_*`
- `minutes_redistribution_*`

versus:

- `exact_lineup_*`
- `exact_onoff_*`
- `exact_stint_*`

The exact-lineup layer must eventually be tested against the core-only challenger. It is not protected from removal if it adds no out-of-sample value.

---

## 7. Continuous point-in-time architecture

The CBB Originator will maintain an immutable, continuous point-in-time information history from which five formal pregame prediction states are materialized:

- T-24h
- T-6h
- T-1h
- T-10m
- final pre-tip

These checkpoints are evaluation and production anchors, not separate data silos.

Every state may contain only information legitimately observable by its cutoff.

Later information may never be backfilled into an earlier authentic state.

The model may share structural talent parameters across checkpoints while availability, minutes, rotation, lineup, and game-state uncertainty evolve as tip approaches.

### Schedule-as-known rule

A checkpoint is anchored to the **scheduled tip time known at that point**, not retrospectively to the game's eventual actual tip time.

Schedule changes must therefore be versioned.

Earlier authentic snapshots remain immutable after a reschedule.

### Required time semantics

Where applicable preserve distinct timestamps:

- `event_time`
- `source_updated_at`
- `effective_at`
- `observed_at` / `received_at`
- `ingested_at` / `processed_at`
- `prediction_cutoff`
- `feature_as_of_time`

Unknown timestamps remain NULL.

Never invent historical timestamps.

---

## 8. Flagship game universe

### Publication and formal validation

NCAA Division I men's basketball:

\[
\boxed{\text{D-I vs D-I only}}
\]

### Auxiliary state updates

Eligible D-I vs non-D-I games may update player/team state when:
- underlying data pass QA;
- the game is legitimate/counting;
- opponent quality is explicitly modeled.

Do not allow raw efficiency in weak-opponent games to mechanically inflate latent strength.

### Exclude from primary training/evaluation

- exhibitions;
- scrimmages;
- non-counting games;
- other contests explicitly excluded by future governance.

---

## 9. Canonical realized outcome

The authoritative realized target is:

\[
\boxed{\text{final official full-game NCAA statistics, including all overtime periods}}
\]

Rules:
- overtime counts;
- regulation team player-minutes are approximately 200;
- each five-minute overtime adds 25 team player-minutes;
- official stat corrections are authoritative;
- true DNP is not equivalent to playing with zero production;
- zero-denominator percentages are NULL/undefined, not 0%;
- regulation-only outcomes may be retained for diagnostics/research but are not co-equal canonical targets;
- sportsbook settlement rules are handled downstream in the benchmark/pricing layer.

---

## 10. Historical participation / DNP truth states

A missing player-game row is **not sufficient evidence of DNP**.

At minimum, canonical participation must distinguish:

- `PLAYED`
- `CONFIRMED_DNP` / `CONFIRMED_NONPARTICIPANT`
- `UNKNOWN_PARTICIPATION`

Starter state is orthogonal to participation.

Minutes, minimal/zero minutes, ejection, abnormal interruption, and similar states should be represented separately when the data support them.

Historical medical reason is not required to learn absence response.

---

## 11. Validation architecture

Use strict expanding-window chronological walk-forward validation for model development.

No random train/test splits for the production model.

### Historical lockbox

\[
\boxed{\text{2025-26 = untouched historical lockbox}}
\]

assuming it remains the most recent completed season when the modeling specification is frozen.

Do not repeatedly inspect 2025-26 during model development.

After:
1. architecture is frozen;
2. feature families are frozen;
3. priors/shrinkage are frozen;
4. hyperparameters are frozen;
5. calibration is frozen;
6. model-selection rules are frozen;

evaluate the lockbox once and record the result permanently.

After that evaluation, the exact frozen specification may be refit on all eligible data through 2025-26 for 2026-27 production.

### Prospective validation

2026-27 is the prospective shadow/production validation season.

Persist predictions before outcomes are known.

Never retroactively rewrite a prediction to make an earlier model version look better.

Material changes create a new model version and a new forward track record.

### Grouping

All five checkpoints for the same game remain in the same fold.

Correlated player/market examples from one game are not treated as independent observations for uncertainty estimation.

### Calibration

Calibration is part of the model.

It must be learned only from data preceding the validation period.

---

## 12. Cold-start architecture

Talent and role are separate latent states.

\[
\boxed{\text{Talent} \neq \text{Role}}
\]

Use PIT-safe hierarchical probabilistic priors.

### Freshmen

May use legitimately available:
- recruiting information;
- physical information;
- position/archetype;
- roster/team context;
- class/context.

Do not hard-code recruiting rank into production.

Do not assume a specific prior distribution family.

Chronological validation selects the distributional form and shrinkage.

### Transfers

Retain prior basketball evidence.

Translate it probabilistically into the new environment using empirically learned historical transfer relationships.

Do not hard-code conference multipliers.

Different skills may translate differently.

### Missing information

Missing is a state, not zero.

Missing information should usually imply:
- more partial pooling;
- wider uncertainty;
- explicit missingness indicators where validated.

---

## 13. Production recomputation requirement

Upon ingestion of a material:
- availability update;
- starter update;
- minutes restriction;
- roster update;
- rotation update;

the production system must regenerate the authoritative joint distribution for the affected game.

### SLA

- design target: <= 30 seconds;
- normal production upper target: <= 60 seconds;
- latency is measured from information ingestion through authoritative publication.

Never retrain structural models on the hot path.

Late changes trigger conditional inference against precomputed structural state.

Do not lower Monte Carlo quality to hit the SLA.

If a new output fails its numerical quality gate:
- preserve the prior authoritative snapshot;
- mark the state as recomputing/stale as appropriate;
- publish the replacement only after it passes the frozen quality gate.

---

# PART III — STAGE 1 DATA PLATFORM FOUNDATION DIRECTIVE v2

## 14. Stage 1 objective

Build the validated, reproducible, point-in-time-aware local basketball data platform that every predictive stage will consume.

This is **not** a predictive-modeling stage.

Do not train, fit, tune, select, backtest, calibrate, simulate for pricing, or deploy a predictive model.

The Stage 1 question is:

> Can we prove what each important record means, where it came from, when it was knowable, which canonical entity it belongs to, and whether it satisfies basketball/accounting/data-quality rules?

If not, do not proceed downstream.

---

## 15. Working boundaries

### Writable project root

`/Users/josephshackelford/CBB Predictive Model 2026/CBB_Originator_Model`

### Immutable Starter Pack

`/Users/josephshackelford/CBB Predictive Model 2026/CBB Starter Pack/starter_pack`

The Starter Pack is read-only.

Do not:
- edit it;
- rename anything in it;
- move anything in it;
- delete anything in it;
- generate outputs inside it.

Do not modify any unrelated repository or project.

---

## 16. Pre-execution safety gate

Before changing any file:

1. Resolve and print:
   - current working directory;
   - project root;
   - git root if any;
   - absolute Starter Pack path.
2. Confirm the writable root is exactly the CBB Originator project.
3. Record:
   - HEAD SHA if applicable;
   - `git status --porcelain`;
   - pre-existing user changes.
4. Do not overwrite or revert pre-existing user work.
5. Compute a SHA-256 manifest of the immutable Starter Pack.
6. Read the authoritative documents completely before implementation.
7. Build a brief internal conflict matrix.
8. If a material semantic conflict remains after applying this amendment's explicit precedence rules, STOP with `BLOCKED`.

Do not write code before this gate passes.

---

## 17. Authoritative documents to read

At minimum:

1. `README.md`
2. `PROJECT_MANIFEST.json`
3. this amendment/directive
4. `docs/STAGE_1_DATA_PLATFORM_FOUNDATION_DIRECTIVE.md`
5. `docs/MODEL_ARCHITECTURE_FREEZE.md`
6. `docs/DATA_SOURCE_POLICY.md`
7. `docs/DATA_REQUIREMENTS.md`
8. `docs/SOURCE_MATRIX.md`
9. `docs/PROJECT_EXECUTION_ORDER.md`
10. `docs/PROBABILITY_ENGINE_POLICY.md`
11. `docs/AMENDMENT_2026-10-04_DATA_SOURCE_FREEZE.md`
12. `reference/CBBD_API_FIELD_INVENTORY_AND_CROSSWALK_v1_1.md`
13. `reference/CBBD_OPENAPI_VERIFICATION_v1_1.md`
14. `reference/BDL_NCAAB_API_AUDIT.md`
15. `reference/BDL_NCAAB_OpenAPI_user_provided.md`

This amendment supersedes older source-role or architectural wording only where explicitly stated above.

---

# STAGE 1 EXECUTION STEPS

## 18. Step 1 — Physical Starter Pack audit

Programmatically inspect the actual physical files.

Do not rely on:
- notebook assumptions;
- `headers.md`;
- API documentation;
- expected schemas.

For every table/file measure:

- relative path;
- table family;
- season;
- file size;
- row count;
- column count;
- exact column names;
- inferred types;
- null rate by column;
- duplicate candidate-key count;
- earliest/latest applicable game date;
- unique team count;
- unique player count where applicable;
- observed schema differences between seasons.

Measure historical coverage for at least:

- games;
- teams;
- conferences;
- player_game_stats;
- player_season_stats;
- player_season_shooting_stats;
- team_game_stats;
- team_season_stats;
- team_season_shooting_stats.

Do not fabricate or infer coverage that was not measured.

### Required outputs

- `data/qa/starter_pack_inventory.json`
- `data/qa/starter_pack_inventory.csv`
- `docs/STARTER_PACK_PHYSICAL_AUDIT.md`

### Human mastery checkpoint

Before moving on, the operator should be able to explain:
- which tables actually exist;
- which seasons each family supports;
- which schemas change by season;
- where missingness is material;
- which candidate keys appear safe or unsafe.

---

## 19. Step 2 — Canonical schema

Create/finalize:

- `CANONICAL_SCHEMA.md`
- executable schema/DDL definitions.

Use provider-independent canonical IDs:

- `canonical_game_id`
- `canonical_team_id`
- `canonical_player_id`

Provider IDs are mappings, not business keys.

At minimum define canonical entities/tables for:

### Core

- `core.games`
- `core.game_schedule_versions`
- `core.teams`
- `core.conferences`
- `core.players`
- `core.rosters`

### Facts

- `fact.player_games`
- `fact.team_games`
- `fact.plays`
- `fact.shots`
- `fact.substitutions`
- `fact.lineup_stints`
- `fact.possessions`
- `fact.participation`

### Availability

- `availability.events`

### Benchmark

- `benchmark.bdl_player_games`
- `benchmark.bdl_team_games`
- `benchmark.game_odds`
- `benchmark.opening_odds`

### Metadata

- `metadata.player_id_map`
- `metadata.team_id_map`
- `metadata.game_id_map`
- `metadata.source_manifest`
- `metadata.data_versions`

Do not populate unavailable data with fake records.

Empty canonical tables are acceptable.

### Raw/staging/canonical separation

- raw/staging layers preserve provider fields losslessly;
- canonical tables contain normalized provider-independent fields plus lineage/source keys;
- do not pollute canonical normalized tables merely to retain every vendor column;
- do not discard source fields from raw/staging because they are not currently used.

### Human mastery checkpoint

The operator should be able to trace one player-game row from:
source file/API payload → raw/staging → canonical IDs → canonical fact row.

---

## 20. Step 3 — Point-in-time policy

Create/finalize:

- `POINT_IN_TIME_POLICY.md`

Define mechanically:

- `event_time`
- `source_updated_at`
- `effective_at`
- `received_at`
- `processed_at`
- `prediction_cutoff`
- `feature_as_of_time`

Also define schedule-version semantics.

A historical pregame feature may use only information available before the applicable cutoff.

Explicitly prohibit:

- target-game outcome leakage;
- completed/end-of-season aggregates used in earlier games;
- future roster states;
- future statuses;
- future schedule changes;
- closing market information as a flagship basketball feature;
- today's finalized metric treated as historically known without legitimate PIT reconstruction.

Document materialization rules for:

- T-24h
- T-6h
- T-1h
- T-10m
- final pre-tip

Do not build the final feature store in Stage 1.

### Human mastery checkpoint

The operator should be able to answer:

> "Why was this record legally available to the T-1h prediction?"

using timestamps and lineage, not memory or intuition.

---

## 21. Step 4 — Storage platform

Use:

- Parquet for canonical analytical datasets;
- DuckDB for local analytical access.

Project-owned structure:

```text
data/
  raw/
  staging/
  canonical/
  manifests/
  qa/
```

Raw external payloads ingested into the project are immutable once written.

Canonical datasets are deterministic rebuilds from immutable upstream/raw inputs.

Use a documented DuckDB database, preferably:

`data/canonical/cbb.duckdb`

The database must be reproducible from command-line code.

Do not require manual notebook execution.

Keep the implementation small and obvious.

---

## 22. Step 5 — Starter Pack ingestion

Implement reproducible canonical ingestion for:

- games;
- teams;
- conferences;
- player_games;
- player_season_stats;
- player_season_shooting_stats;
- team_games;
- team_season_stats;
- team_season_shooting_stats.

Preserve:

- original provider/source IDs;
- source lineage;
- season;
- source filename;
- ingestion version;
- original fields in raw/staging.

Do not overwrite source CSVs.

Do not copy the entire Starter Pack into project-owned raw storage merely to duplicate it.

Treat it as an immutable mounted upstream source and record:
- path;
- hashes;
- source manifest.

Remember: the Starter Pack must not become a permanent commercial production dependency.

---

## 23. Step 6 — Source adapter interfaces

Implement simple source adapters/interfaces for:

- CBBD;
- BDL NCAAB;
- Covers availability.

Also keep a lightweight StatSharp snapshot parser only if already useful for secondary QA/fixtures.

### CBBD

Implement adapter/interface plus bounded smoke fetch if credentials are available.

Do not launch a full historical API backfill.

Only fetch information that Stage 1 genuinely needs to verify.

Cache raw responses that are fetched.

### BDL

Support:
- player-game QA;
- team-game QA;
- current odds;
- opening odds;
- relevant update/open timestamps.

Market data belongs only in the benchmark namespace.

### Covers

Implement a provider-independent availability adapter capable of parsing a saved snapshot/text/HTML capture.

Preserve raw values.

Canonical availability fields should support at least:
- player source name;
- team source name;
- position if available;
- injury/reason raw;
- status raw;
- status normalized;
- source update timestamp/date if available;
- narrative note;
- `received_at`;
- parser/version metadata;
- conflict flags.

Do not require historical injury data.

Do not spend Stage 1 building an elaborate scraper framework.

---

## 24. Step 7 — Participation representation

Build the canonical participation fact/state needed later for absence-response modeling.

At minimum represent:

- played;
- confirmed nonparticipant/DNP;
- unknown participation;
- starter/non-starter;
- minutes;
- minimal/zero minutes;
- ejection/abnormal interruption when evidence supports it.

Rules:

- missing player-game row alone != DNP;
- DNP != injury;
- do not assign a medical reason without evidence;
- roster/scheduled-player universe must be explicit before confirmed DNP can be inferred.

The later model must be able to ask:

> "When Player A did not participate, how were minutes, usage, opportunity, and outcomes redistributed?"

Stage 1 builds the trustworthy representation, not the predictive answer.

---

## 25. Step 8 — Identity resolution

Create deterministic mapping infrastructure for:

- CBBD IDs;
- BDL IDs;
- Covers player/team context;
- future optional KenPom/Torvik identity if ever enabled.

Do not silently fuzzy-match.

Every non-exact resolution must preserve:

- match method;
- confidence;
- source context;
- manual-review flag below threshold.

Generate unresolved-identity reports.

Do not create false matches to reach 100%.

Provider-independent canonical IDs must be stable and deterministic under the documented ID contract.

---

## 26. Step 9 — Representative smoke ingestion

Do not run full historical API acquisition.

Prove the pipeline with bounded samples:

- Starter Pack historical ingestion;
- several teams;
- several historical games;
- player-game canonicalization;
- team-game canonicalization;
- one CBBD PBP sample if access permits;
- one substitution/onFloor/lineup sample where supported;
- BDL reconciliation for representative games;
- one saved Covers availability fixture/snapshot path.

Missing live credentials or temporary source access is a blocker only for the live smoke fetch, not permission to fabricate data.

Synthetic test fixtures, if used, must:
- live only under tests;
- be clearly labeled synthetic;
- never enter canonical production data;
- never enter measured coverage counts.

---

## 27. Step 10 — Automated data QA

At minimum test:

### Basketball accounting

\[
PTS = 2(2PM) + 3(3PM) + FTM
\]

\[
REB = ORB + DRB
\]

\[
FGM = 2PM + 3PM
\]

Also:
- makes <= attempts;
- no impossible negative counting stats except legitimate source semantics;
- reasonable minute values;
- full-game/O.T. minute handling documented.

### Keys and joins

- no duplicate canonical player-game primary keys;
- valid game/team/player joins;
- valid dates/seasons;
- source lineage present;
- PIT fields valid.

### Cross-source reconciliation

Where appropriate:
- CBBD vs BDL score/player/team outcomes;
- discrepancies are reported, not silently overwritten.

### Market isolation

No sportsbook field may appear in the flagship basketball-feature namespace.

Do not weaken tests to obtain green results.

---

## 28. Step 11 — Coverage audit

Create the authoritative machine contract:

- `data/qa/COVERAGE_AUDIT.json`

Then generate derivatives:

- `data/qa/STAGE_1_COVERAGE_SUMMARY.json`
- `docs/STAGE_1_COVERAGE_SUMMARY.md`

The summary artifacts must be generated from `COVERAGE_AUDIT.json`, not recalculated independently.

Report measured:

- player-game seasons;
- player-season seasons;
- shooting seasons;
- team-game seasons;
- team-season seasons;
- missing years;
- row counts;
- player ID coverage;
- game ID coverage;
- schema consistency;
- PBP coverage where actually fetched/measured;
- on-floor coverage where actually fetched/measured;
- substitution coverage where actually fetched/measured;
- lineup coverage where actually fetched/measured.

Do not claim historical coverage that has not been fetched and measured.

Coverage determines admissible history by subsystem.

---

## 29. Step 12 — Post-execution integrity gate

Before reporting PASS:

1. recompute the Starter Pack SHA-256 manifest;
2. prove it is byte-for-byte identical to the pre-run manifest;
3. confirm no path outside the CBB Originator project was modified;
4. list every created/modified project file from actual filesystem/git evidence;
5. run the complete Stage 1 test suite from command line;
6. rebuild canonical outputs from source inputs to verify deterministic reproducibility;
7. confirm no predictive model was trained;
8. confirm no sportsbook feature entered the flagship feature namespace;
9. confirm unresolved identity/data-quality issues are explicitly reported.

---

# NON-GOALS

Do not:

- train a predictive model;
- build talent models;
- build minutes models;
- build rotation prediction models;
- build the possession simulator;
- fit a lineup-interaction model;
- calibrate probabilities;
- tune hyperparameters;
- run sportsbook profitability tests;
- scrape Torvik at scale;
- integrate KenPom into the predictive path;
- hunt historical injury archives;
- acquire historical player props;
- perform full historical CBBD PBP/substitution backfills;
- deploy anything;
- build WizardOfOdds UI;
- build microservices/orchestration for future stages;
- modify other repositories;
- redesign frozen architecture.

---

# STOP CONDITIONS

STOP and report `BLOCKED` if:

- authoritative documents materially conflict after applying this amendment's precedence rules;
- canonical identity requires an owner-level architectural decision not covered here;
- physical Starter Pack data materially contradicts the frozen plan;
- a source field has ambiguous semantics that could create leakage or invalid accounting;
- implementing the behavior would require another repository to be modified;
- a P0 assumption is empirically false;
- Stage 1 QA reveals a defect that would contaminate later modeling;
- a proposed workaround requires fabricating information.

Do not improvise around a blocking defect.

---

# REQUIRED FINAL REPORT

When Stage 1 is complete, STOP.

Do not begin Stage 2.

Return exactly this structure:

```text
STAGE:
Stage 1 — CBB Data Platform Foundation

STATUS:
PASS / BLOCKED / FAIL

HEAD_SHA:
<git SHA or NOT A GIT REPOSITORY>

FILES_CREATED:
<complete list>

FILES_MODIFIED:
<complete list>

SOURCE_PROJECT_MODIFIED:
FALSE for the immutable Starter Pack and every unrelated project.

STARTER_PACK_AUDIT:
- tables found
- seasons found by family
- row counts
- important schema changes
- important missingness
- duplicate findings
- ID findings

CANONICAL_DATABASE:
- path
- tables/views created
- row counts

IDENTITY_RESOLUTION:
- exact match rates
- non-exact match rates
- unresolved teams
- unresolved players
- manual-review records

SMOKE_INGESTION:
- sources tested
- records/games tested
- results

TESTS_RUN:
<commands>

TEST_RESULTS:
<exact pass/fail counts>

ACCEPTANCE_CRITERIA:
1. PASS/FAIL — Starter Pack physically audited
2. PASS/FAIL — canonical schema implemented
3. PASS/FAIL — continuous PIT policy + five checkpoint rules frozen
4. PASS/FAIL — Starter Pack canonical ingestion works
5. PASS/FAIL — CBBD adapter exists
6. PASS/FAIL — BDL adapter exists
7. PASS/FAIL — Covers availability adapter exists
8. PASS/FAIL — identity mapping framework exists
9. PASS/FAIL — DNP/participation/unknown representation exists
10. PASS/FAIL — QA tests pass
11. PASS/FAIL — authoritative COVERAGE_AUDIT.json generated
12. PASS/FAIL — preliminary coverage summaries generated
13. PASS/FAIL — no predictive model trained
14. PASS/FAIL — immutable Starter Pack unchanged
15. PASS/FAIL — no unrelated repository modified
16. PASS/FAIL — benchmark sportsbook fields isolated from flagship feature namespace

DATA_QUALITY_FINDINGS:
<all meaningful findings>

UNRESOLVED_ISSUES:
<all unresolved issues>

ASSUMPTIONS_MADE:
<all assumptions; NONE if none>

READY_FOR_NEXT_STAGE:
YES / NO

NEXT_ACTION:
STOP and return this report for quantitative-architecture review.
```

---

# STAGE 1 HUMAN MASTERY CHECKLIST

Stage 1 is not complete for the human operator merely because the code runs.

Before approving the next stage, the operator should be able to explain in plain language:

1. **Raw vs staging vs canonical** — why each layer exists.
2. **Canonical IDs** — why provider IDs are mappings rather than core business keys.
3. **Lineage** — how one canonical record can be traced back to source evidence.
4. **PIT** — why source time, effective time, receipt time, and prediction cutoff are different.
5. **DNP truth** — why a missing box-score row is not automatically a DNP.
6. **Coverage windows** — why team/player talent can use deep history while exact-lineup models use shorter history.
7. **Market isolation** — why sportsbook prices can benchmark the originator but cannot contaminate its basketball feature path.
8. **QA invariants** — how basketball identities and cross-source reconciliation catch upstream defects.
9. **Reproducibility** — how the same immutable inputs rebuild the same canonical outputs.
10. **Stage discipline** — why no predictive modeling begins until these data contracts pass.

If any of those cannot be explained from the code and artifacts, improve the documentation before moving on.

---

# FINAL OPERATING RULE

Build in a straight line.

\[
\boxed{
\text{correct data}
\rightarrow
\text{correct PIT state}
\rightarrow
\text{simple challengers}
\rightarrow
\text{earned model complexity}
\rightarrow
\text{joint distribution}
\rightarrow
\text{honest validation}
\rightarrow
\text{production}
}
\]

Do not skip a gate because the downstream work is more interesting.

The purpose of Stage 1 is to make every later quantitative decision trustworthy.
