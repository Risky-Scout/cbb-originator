# PROJECT_EXECUTION_ORDER v1.1
## CBB Originator Interconnected Player Prop Predictive Model

**Date:** 2026-10-07 · **Status:** AUTHORITATIVE (supersedes `docs/PROJECT_EXECUTION_ORDER_v1_0.md`, preserved)
**Basis:** CBB Originator Audit and Execution Blueprint (2026-10-06) as adopted with architect amendments A-C
(`CBB_Blueprint_v1_4.md`). **Mapping from the old Stage 0-19 numbers:** `docs/STAGE_CROSSWALK.md`.

**Rule (unchanged):** an agent executes one bounded unit, produces evidence, stops, and waits for review. No agent may
weaken a gate, redesign the architecture, or begin the next unit automatically.

## Current position

* Phase A Part 1 (this closure) produced `cbb_data_v2.4+5f19d31e524a`. Its status is **RESEARCH_CANDIDATE, not MODEL_READY**.
* No predictive model, calibration model or simulator has been fitted.
* The 2025-26 lockbox is untouched.

## Phase A — Pre-model integrity closure

### A1. Part 1: governance freeze, clustered identity remediation, next-stage contracts (2026-10-07)
Deliverables: precedence map, crosswalk, Blueprint v1.4, this order, the cluster identity audit and the v2.4 remediation,
frozen contracts (bitemporal checkpoints, possession/stint reconstruction, model eligibility, production
operations / ledger, WizardOfOdds CBB, lockbox opening, commercial rights, reproducible environment), the G4A/G4B
gates, the PBP selection-bias plan and the backup plan.
Gates: **G0** governance, **G1** identity (`docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md` §Gates).

### A2. Part 2: data and checkpoint closure (next, after architect review of A1)
1. Bitemporal five-checkpoint rebuild (`docs/BITEMPORAL_CHECKPOINT_SCHEMA.md`): every observation is stamped with its
   fidelity class. The Stage 3 `1900-01-01` preseason sentinel is replaced by `PRESEASON_ASSUMED`.
2. Exact possession reconstruction and exact chronological lineup stints, with quality thresholds frozen **before**
   the pass/fail evaluation (`docs/POSSESSION_AND_LINEUP_RECONSTRUCTION_POLICY.md`).
3. PBP quarantine selection-bias audit (`docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md`).
4. Per-subsystem eligibility manifests (`docs/MODEL_ELIGIBILITY_POLICY.md`).
5. Team-identity alias remediation (LIU Brooklyn / Long Island University, two Carver College ids), with its own audit.
   It changes the D-I target set by about 29 games.
6. Reproducible environment lock, then a clean-room rebuild that reproduces the accepted fingerprints
   (`docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md`).
7. Off-machine baseline per `docs/OFF_MACHINE_BACKUP_PLAN.md`. This needs the user's choice of destination.
Gate: the architect accepts a dataset as **MODEL_READY**. That status is recorded in a new manifest field, and earlier
manifests are never rewritten.

## Phase B — Prospective availability evidence (runs in parallel from now)
The first real authorized availability snapshot is captured with the existing adapter
(`docs/SHARP_STATS_CAPTURE_WORKFLOW.md`).
**G4A** (`docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md`) must pass before availability information may condition any model.
Evidence keeps accumulating toward **G4B**. G4B does not block Phase C.

## Phase C — Historical challenger-model program (requires A2 MODEL_READY)
Order (each with its own eligibility manifest, walk-forward folds FOLD_1..FOLD_7, lockbox excluded):
1. simple baselines: rolling / rate distributions;
2. participation and minutes (hierarchical; independent CatBoost/LightGBM challenger);
3. dynamic player talent (hierarchical shrinkage, cold starts);
4. game environment (pace / possessions / team strength);
5. shot / opportunity, event heads (needs A2 possessions);
6. location (2021-22+ window);
7. lineup / interaction (2023-24+ stints).
Every model artifact is born with the identity / provenance fields of the frozen production contract
(`PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md` §3).

## Phase D — Joint engine, calibration and selection
Joint distribution engine (exact PMF where tractable, otherwise a convergence-qualified Monte Carlo bank); calibration;
dependence validation; the full challenger tournament under a frozen scorecard; sportsbook benchmark (benchmark
namespace only). The production candidate is frozen. Then the 2025-26 lockbox is opened **once** under
`docs/LOCKBOX_OPENING_PROTOCOL.md`.

## Phase E — Shadow production (2026-27 prospective)
**G4B** reliability gate; append-only prediction ledger; locked pre-tip predictions; grading; no publication.

## Phase F — WizardOfOdds production
Commercial-rights gate (`docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md`), integration staging contract tests
(`WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md`), human promotion approval, rollback readiness. The in-play project
starts only after Pregame v1 is stable.

## Agent reporting contract (unchanged from v1.0)

```
STAGE / STATUS (PASS / BLOCKED / FAIL) / HEAD_SHA / FILES_CREATED / FILES_MODIFIED / TESTS_RUN / TEST_RESULTS /
ACCEPTANCE_CRITERIA / DATA_QUALITY_FINDINGS / UNRESOLVED_ISSUES / ASSUMPTIONS_MADE / READY_FOR_NEXT_STAGE / NEXT_ACTION
```
