# Architecture precedence map

**Version:** 1.0 · **Date:** 2026-10-07 · **Established by:** Pre-Model Integrity Closure v1, Part 1
(`docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md`).

This file decides which document wins when two project documents disagree. Older documents are **preserved unchanged**.
None was edited to look consistent with the new plan. Where they conflict with a newer document, this map says so.

## 1. Rule of precedence

1. A **newer** governing document beats an older one **only on the topics it explicitly supersedes** (listed below).
2. On every other topic, the older document stays authoritative.
3. Code and tests implement the documents. If code and an authoritative document disagree, the document wins. The
   disagreement is recorded as a defect. It is not resolved by quietly editing the document.
4. Historical-record documents describe what was true when they were written. They are never edited after the fact.
   A correction gets a new, superseding document.
5. An architect directive pasted into a session (for example the Pre-Model Integrity Closure directive of
   2026-10-07) is authoritative for its stated scope once it has been recorded in a repository document.

## 2. Current authoritative set (top = highest precedence)

| # | Document | Status | Supersedes |
|---|---|---|---|
| 1 | `CBB_Blueprint_v1_4.md` | **AUTHORITATIVE** | Blueprint v1.3 on: the governing phase plan (Phases A-F), the five-checkpoint fidelity rule, the Sharp Stats gate split, the production-contract timing rule, the identity evidence rule and the dataset status vocabulary |
| 2 | `PROJECT_EXECUTION_ORDER_v1_1.md` | **AUTHORITATIVE** | `docs/PROJECT_EXECUTION_ORDER_v1_0.md` (Stage 0-19 sequence) |
| 3 | `docs/STAGE_CROSSWALK.md` | **AUTHORITATIVE** (status ledger) | nothing; it maps old stage numbers to the new phases |
| 4 | `docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md` | **AUTHORITATIVE** (Part 1 record + gates) | Stage 3.1 identity acceptance (`docs/PLAYER_IDENTITY_COLLISION_AUDIT.md`) on the chronology rule |
| 5 | `POINT_IN_TIME_POLICY_v2_0.md` + `docs/BITEMPORAL_CHECKPOINT_SCHEMA.md` | **AUTHORITATIVE, FROZEN (design)** | `POINT_IN_TIME_POLICY.md` v1.2 on: the checkpoint set (T-30m added, "final pre-tip" demoted to an operational lock), fidelity/availability classes, the schedule AS-OF semantics and the prohibition of manufactured timestamps (incl. the Stage 3 `1900-01-01` preseason sentinel). v1.2 stays authoritative for HR-1..HR-7 legality rules |
| 6 | `docs/POSSESSION_AND_LINEUP_RECONSTRUCTION_POLICY.md` | **AUTHORITATIVE, FROZEN (design)** | nothing (defines the never-implemented frozen Stage 4) |
| 7 | `docs/MODEL_ELIGIBILITY_POLICY.md` | **AUTHORITATIVE, FROZEN** | any implicit "feature exists" row selection |
| 8 | `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, `docs/PREDICTION_LEDGER_SCHEMA.md`, `WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md`, `docs/LOCKBOX_OPENING_PROTOCOL.md`, `docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md` | **AUTHORITATIVE CONTRACTS, FROZEN** (no implementation yet) | Blueprint v1.3 §15-16 and `PROBABILITY_ENGINE_POLICY.md` §7-8 **only** where these are more specific; any NBA WizardOfOdds contract (never reused) |
| 9 | `docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md`, `docs/OFF_MACHINE_BACKUP_PLAN.md`, `docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md`, `docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md` | **AUTHORITATIVE, FROZEN (plans/gates)** | `docs/SHARP_STATS_CAPTURE_WORKFLOW.md` §"Status" only in that it adds gates |
| 10 | `docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md` (generated) | **AUTHORITATIVE EVIDENCE** for dataset `cbb_data_v2.4+5f19d31e524a` | the classifications in `docs/PLAYER_IDENTITY_COLLISION_AUDIT.md` |

## 3. Older documents: classification

### 3.1 Remain fully authoritative (no conflict with the new set)

| Document | Topic |
|---|---|
| `docs/MODEL_ARCHITECTURE_FREEZE_v1_0.md` (= `MODEL_ARCHITECTURE_FREEZE.md`) | model decomposition, hard accounting constraints, pregame-first |
| `docs/PROBABILITY_ENGINE_POLICY.md` | exact-PMF authority, convergence-qualified Monte Carlo, custom-line pricing |
| `LOCKBOX_POLICY.md` | 2025-26 lockbox rules and enforcement (opening procedure now in `docs/LOCKBOX_OPENING_PROTOCOL.md`) |
| `CANONICAL_SCHEMA.md` | canonical tables, IDs, participation truth states (extended by the v2.4 tables listed in `cbb_platform/schema.sql`) |
| `docs/DATA_SCOPE_RULINGS.md` | pre-2005 out of scope, played-game universe, wallclock UNTRUSTED_MIXED |
| `docs/SEASON_STATISTICS_AUTHORITY.md` | game-derived values beat raw season summaries |
| `docs/WALK_FORWARD_PROTOCOL.md` | fold definitions (folds are rebuilt on v2.4 with the same definitions) |
| `docs/ROSTER_PIT_POLICY.md` | roster snapshots are never pregame membership evidence |
| `docs/GAME_IDENTITY_RESOLUTION.md`, `docs/GAME_STATUS_RESOLUTION.md`, `docs/GAME_IDENTITY_AUDIT.md` | Stage 2.1 / 2.2 game identity and played status (unchanged by v2.4) |
| `docs/CBBD_WALLCLOCK_SEMANTICS.md`, `docs/CBBD_EVENT_COVERAGE_DISCOVERY.md`, `docs/CBBD_BACKFILL_STRATEGY.md`, `docs/CANDIDATE_SUBSYSTEM_TRAINING_WINDOWS.md` | measured source facts and window proposals |
| `reference/*` | CBBD OpenAPI verification, BDL API audit |

### 3.2 Remain authoritative except where superseded

| Document | Superseded on | By |
|---|---|---|
| `docs/CBB_Blueprint_v1_3.docx` / `.pdf` | phase plan, checkpoint fidelity, Sharp Stats gate split, production-contract timing, identity evidence rule, dataset status vocabulary | `CBB_Blueprint_v1_4.md` |
| `docs/CBB_ORIGINATOR_ARCHITECTURE_AMENDMENT_AND_STAGE1_DIRECTIVE_V2.md` (2026-10-05) | five-checkpoint list (now T-24h/T-6h/T-1h/T-30m/T-10m with fidelity classes); historical schedule handling | `POINT_IN_TIME_POLICY_v2_0.md` |
| `POINT_IN_TIME_POLICY.md` v1.2 | checkpoint set, fidelity classes, schedule AS-OF, manufactured-timestamp prohibition | `POINT_IN_TIME_POLICY_v2_0.md` |
| `docs/DATA_SOURCE_POLICY_v1_0.md`, `docs/SOURCE_MATRIX_v1_0.md`, `docs/AMENDMENT_2026-10-04_DATA_SOURCE_FREEZE.md` | production use of any source without verified commercial rights | `docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md` |
| `docs/DATA_REQUIREMENTS_v1_4.md` | per-subsystem training-row selection | `docs/MODEL_ELIGIBILITY_POLICY.md` |
| `docs/SHARP_STATS_CAPTURE_WORKFLOW.md` | when availability may condition a model | `docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md` |
| `docs/STAGE3_FEATURE_COVERAGE.md` | upstream dataset (now v2.4; regenerated by the Stage 3 rebuild) | `modeling/STAGE3_MANIFEST.json` |

### 3.3 Historical records only (never edited; not a basis for new decisions)

| Document | Why historical |
|---|---|
| `docs/PROJECT_EXECUTION_ORDER_v1_0.md`, `docs/PROJECT_EXECUTION_ORDER.md` | Stage 0-19 sequence, replaced by `PROJECT_EXECUTION_ORDER_v1_1.md`. The implemented "Stage 1/2/2.1/2.2/3/3.1" names never matched this numbering (see the crosswalk) |
| `docs/PLAYER_IDENTITY_COLLISION_AUDIT.md`, `data/qa/player_identity_collision_audit.{csv,json}` | Stage 3.1 audit whose rule "non-overlapping multi-team appearances => CONFIRMED_SINGLE_PLAYER" is **REVOKED**. Its 173 CONFIRMED_SINGLE_PLAYER rows include 164 whole-team mirror artefacts |
| `docs/STAGE_1_DATA_PLATFORM_FOUNDATION_DIRECTIVE.md`, `docs/STAGE_1_COVERAGE_SUMMARY.md`, `docs/CBBD_LIVE_SMOKE_RESULTS.md`, `docs/CBBD_PLAYER_IDENTITY_VERIFICATION.md`, `docs/STARTER_PACK_PHYSICAL_AUDIT.md`, `docs/BDL_NCAAB_API_AUDIT_v1_0.md` | stage directives and point-in-time measurements |
| `data/manifests/DATASET_MANIFEST_cbb_data_v2.{0,1,2,3}+*.json` | ancestor dataset manifests, byte-preserved (v2.3 manifest sha256 `6ee97887850cbd703082220da39c6f5bd9f3cedc976fd4f54443128eb21500bf`, tested) |
| `README.md` text before 2026-10-07 and `PROJECT_MANIFEST.json` `"status": "source_architecture_frozen_ready_for_data_platform"` | superseded in place: these two files are *pointer* files and are updated to point at the new set |

## 4. Known inconsistencies between older documents (recorded, not hidden)

1. **Execution numbering.** The frozen order v1.0 calls coverage auditing "Stage 3", possession/lineup reconstruction
   "Stage 4" and the PIT feature store "Stage 5". The implemented work used "Stage 2" for coverage and events,
   and "Stage 3" for the PIT feature store. Possession/lineup reconstruction (frozen Stage 4) was **skipped**, not
   completed. The crosswalk is the reconciliation.
2. **Availability source.** `AMENDMENT_2026-10-04_DATA_SOURCE_FREEZE.md` locks "Sharp Stats" as P0 current availability.
   `CBB_ORIGINATOR_ARCHITECTURE_AMENDMENT_AND_STAGE1_DIRECTIVE_V2.md` (2026-10-05) names Covers as preferred and
   "StatSharp" as secondary/provisional. The implemented adapter and this directive use **Sharp Stats**. Resolution: the
   G4A/G4B gates are source-agnostic and apply to whichever availability source conditions a model. The architect
   must confirm whether "Sharp Stats" and "StatSharp" are the same provider and which source is primary (open item).
3. **Checkpoint list.** PIT v1.2 lists T-24h, T-6h, T-1h, T-10m and "final pre-tip". Amendment V2 and this directive
   require T-24h, T-6h, T-1h, T-30m and T-10m. PIT v2.0 adopts the directive.
4. **Preseason availability sentinel.** Stage 3 (`cbb_platform/stage3/universe.py`, `PRESEASON`) stamps preseason
   membership evidence `1900-01-01`. Under PIT v2.0 that is a manufactured timestamp. It is reclassified as
   `PRESEASON_ASSUMED` and must be replaced before any model consumes it (Part 2 item, see the closure report).
5. **Audit blueprint.** The 2026-10-06 "CBB Originator Audit and Execution Blueprint" is not stored in the repository.
   Its adopted content is recorded in `CBB_Blueprint_v1_4.md` from the architect directive. The Phase A-F
   definitions there should be confirmed against the original blueprint (open item).

## 5. Superseded test invariants (recorded 2026-10-07, four-failure reconciliation)

A test invariant is a written rule too. It is superseded only with evidence, and it is replaced by a stronger rule. It is
never deleted to make a suite green. Evidence: `docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md` §7,
`data/qa/pre_model_closure/four_failure_reconciliation.json`,
`data/qa/pre_model_closure/player_game_154_row_reconciliation.csv`.

| Old invariant (where) | Correct when written because | Superseded by / why | Replacement assertions |
|---|---|---|---|
| Stage 2.1: "`fact.player_games` (and BOX_SCORE_ROW participation) count never changes; = 2,368,206 forever" (`tests/test_stage2_1.py::test_box_facts_are_not_lost`) | Stage 2.1 changed only **game** identity. Every distinct Starter Pack box record (`game_id` + per-row `athleteId`) mapped to exactly one canonical row, and no rule could remove a row. 2,368,206 was correct through v2.3 | v2.4 team-attribution remediation (`DEMIRROR_RECONCILED_GAMES`). In 14 Starter Pack games, 154 box lines also appear under the opponent with the same player name and an identical 18-stat line. Those 154 canonical rows were **copies, not appearances**. v2.4 moves exactly those copies to `qa.identity_remediation_quarantine`, so the correct count is 2,368,206 − 154 = 2,368,052 | (1) raw rows unchanged: Stage 1 inventory = build source manifest = files now (2,460,230 lines / 2,368,206 distinct records); (2) every source record is in exactly one of `fact.player_games` or the quarantine; (3) every removed row has exactly one RESOLVED twin on the other team with an identical box line; (4) every removal has a QUARANTINE audit decision with a matching KEEP, in a game logged REMEDIATED_RECONCILED; (5) no copy reaches a trusted view; (6) BOX_SCORE_ROW participation = one per canonical player-game, and trusted = canonical counts. The only rule allowed to remove box facts is `DEMIRROR_RECONCILED_GAMES` |
| Stage 2: "every sibling of the repository that existed at Stage 2 start exists unchanged forever" (`tests/test_stage2.py::test_no_unrelated_project_was_modified`, `data/manifests/workspace_snapshot_stage2.json`) | It pinned the workspace for the Stage 2 task | It was never meant to forbid the user's own later changes outside the repository. The workspace drifted **before** the Pre-Model Closure session started (proof in the baseline file) | Versioned baselines: `workspace_snapshot_stage2.json` (historical, sha256-pinned, never edited) → `workspace_snapshot_pre_model_closure_v1.json` (current). Immutability is asserted against the current baseline. Each successor must list every difference from its predecessor in `drift_from_superseded`, with a classification (`test_workspace_baseline_history_is_preserved_and_drift_is_accounted`) |
