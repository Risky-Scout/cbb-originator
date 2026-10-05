# PROJECT_EXECUTION_ORDER.md
## CBB Originator Interconnected Player Prop Predictive Model
### Frozen Execution Order v1.0
**Date:** October 4, 2026  
**Implementation agents:** Claude Code or ChatGPT Codex  
**Rule:** The agent executes one bounded stage, produces evidence, stops, and waits for review.

## Stage 0 — Documentation freeze (COMPLETE after this package)

Required frozen documents:
- `MODEL_ARCHITECTURE_FREEZE_v1_0.md`
- `DATA_SOURCE_POLICY_v1_0.md`
- `SOURCE_MATRIX_v1_0.md`
- `DATA_REQUIREMENTS_v1_4.md`
- `PROBABILITY_ENGINE_POLICY.md`
- CBBD schema/audit references
- BDL API audit

Gate:
- all P0 data families have a source or derivation;
- no unresolved architectural question blocks ingestion.

## Stage 1 — Canonical schema + PIT contract

Create:
- `CANONICAL_SCHEMA.md`
- `POINT_IN_TIME_POLICY.md`
- database DDL/schema code
- ID-map contract
- source-lineage contract

Do not train a model.

Gate:
- every source field maps to a canonical field or is explicitly ignored;
- every mutable observation has a PIT policy;
- no market fields can enter originator feature views.

## Stage 2 — Data Platform v1.0 ingestion

Implement immutable raw ingestion and canonicalization.

Order:
1. CBBD games/teams/conferences/rosters
2. CBBD player/team game outcomes
3. CBBD PBP
4. CBBD substitutions
5. CBBD lineups
6. CBBD shooting/season summaries
7. CBBD recruiting/transfers/venues
8. BDL games/player_stats/team_stats/players-active for QA
9. BDL odds/opening-odds into benchmark namespace
10. Sharp Stats availability adapter + append-only snapshots
11. Bart Torvik enrichment adapter if stable/permitted; otherwise record non-blocking deferment

Outputs:
- immutable raw store
- canonical Parquet tables
- DuckDB analytical database
- source manifests/checksums
- QA exception tables

Gate:
- ingestion idempotent/restartable;
- primary joins work;
- no raw data overwritten.

## Stage 3 — Coverage audit

Generate `COVERAGE_AUDIT.json` by season/source.

Measure:
- game/player/team coverage
- player/team ID join rates
- CBBD vs BDL reconciliation
- PBP coverage
- `onFloor` completeness
- shotInfo/location coverage
- substitution coverage
- lineup coverage
- stint reconstruction feasibility
- Sharp Stats current-season mapping
- Torvik mapping/coverage if enabled
- market benchmark coverage
- PIT timestamp completeness

Gate:
- choose valid training window separately for each subsystem;
- failed P0 coverage creates a documented fallback or blocks that subsystem.

## Stage 4 — Possession / lineup reconstruction

Build:
- canonical possessions
- paired substitutions
- exact lineup stints
- confidence/reconstruction quality metrics
- accounting reconciliation

Gate:
- score/minutes/lineup invariants pass at predefined rates.

## Stage 5 — PIT feature store

Build strictly lagged views/features:
- player latent-history inputs
- role/minutes history
- historical DNP/absence-response features
- team/opponent environment
- lineup exposure
- recruiting/transfer priors
- optional Torvik enrichment

Gate:
- automated leakage tests pass.

## Stage 6 — Challenger baselines

Train:
- rolling/rate distribution baseline
- independent hierarchical statistical baseline
- independent CatBoost/LightGBM baseline

Gate:
- deterministic walk-forward evaluation pipeline exists;
- probability and calibration metrics stored.

## Stage 7 — Dynamic player talent

Build hierarchically shrunk, time-evolving player skill estimates.

Gate:
- beats rolling/raw averages out of sample;
- sparse/freshman/transfer behavior is stable.

## Stage 8 — Availability / minutes / rotation

Train:
- active state handling
- starter probability
- full minutes distribution
- historical absence-response redistribution
- current Sharp Stats conditioning

Do not require historical injury diagnosis labels.

Gate:
- minutes/calibration/absence slices beat baselines;
- 200-minute coherence.

## Stage 9 — Game environment

Build:
- pace / possession distribution
- team offense/defense
- foul/turnover/rebounding latent environment

Gate:
- improved held-out likelihood/calibration;
- player opportunities reconcile to team environment.

## Stage 10 — Lineup and interaction layer

Build regularized player main effects + low-rank teammate/opponent interactions.

Gate:
- lineup-aware model beats no-lineup challenger;
- shrinkage prevents sparse-lineup explosion.

## Stage 11 — Possession/event models

Fit:
- turnover
- shot/foul branch
- shooter
- shot family
- make/miss
- assist
- block
- rebound
- steal
- FT sequence

Gate:
- each head calibrated out of sample;
- event accounting reconciles.

## Stage 12 — Joint distribution engine

Implement:
- exact distribution where tractable
- otherwise production convergence-qualified Monte Carlo bank

Gate:
- hard accounting identities;
- convergence/MCSE;
- reproducibility.

## Stage 13 — Calibration + dependency validation

Evaluate:
- Brier/log loss
- CRPS
- calibration
- interval coverage
- energy score
- variogram/dependence diagnostics
- targeted co-hit relationships

Add residual dependence only if diagnostics justify it.

## Stage 14 — Full challenger tournament

Compare:
- simple baselines
- independent models
- joint model
- ablations with/without Torvik enrichment and interaction components

Production candidate selected by frozen scorecard.

## Stage 15 — Sportsbook benchmark

Use BDL/CBBD benchmark namespace only.

Compare pure-originator probabilities against no-vig market probabilities at aligned checkpoints.

Market never feeds the originator.

## Stage 16 — Production pipeline + readiness gates

Automate:
- data refresh
- feature build
- availability conditioning
- simulation/distribution generation
- calibration
- board pricing
- artifact persistence

No-prediction behavior on failed gates.

## Stage 17 — Shadow production

Timestamp and lock predictions before games.
Grade after outcomes.
No retrospective edits.

## Stage 18 — Deploy Pregame v1

Version:
- dataset
- model
- calibration
- source manifest
- probability method
- prediction artifact

Only after stable shadow evidence.

## Stage 19 — Separate in-play project

Begins only after Pregame v1 is stable.

## Agent reporting contract

Every implementation stage must end with:

```
STAGE:
STATUS: PASS / BLOCKED / FAIL
HEAD_SHA:
FILES_CREATED:
FILES_MODIFIED:
TESTS_RUN:
TEST_RESULTS:
ACCEPTANCE_CRITERIA:
DATA_QUALITY_FINDINGS:
UNRESOLVED_ISSUES:
ASSUMPTIONS_MADE:
READY_FOR_NEXT_STAGE: YES/NO
NEXT_ACTION:
```

The agent may not silently weaken a gate or redesign the architecture.
