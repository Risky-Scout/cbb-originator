# CBB Originator Model — Authoritative Project Documents

Planning, governance and data platform for the CBB Originator Interconnected Player Prop Predictive Model.

## Start here (authoritative, 2026-10-07)

1. `docs/ARCHITECTURE_PRECEDENCE.md` — which document wins, and which older documents are historical records.
2. `CBB_Blueprint_v1_4.md` — the governing architecture (supersedes Blueprint v1.3 on the topics it lists).
3. `PROJECT_EXECUTION_ORDER_v1_1.md` — Phases A-F (supersedes the Stage 0-19 order v1.0).
4. `docs/STAGE_CROSSWALK.md` — old stage numbers → implemented stages → phases, with status.
5. `docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md` — the current closure record and gates.

## Current state

* Dataset: **`cbb_data_v2.4+5f19d31e524a`** (parent `cbb_data_v2.3+7bef640cccde`). Status: **RESEARCH_CANDIDATE — not
  MODEL_READY**. Manifest: `data/manifests/DATASET_MANIFEST.json`; every ancestor is preserved verbatim as
  `data/manifests/DATASET_MANIFEST_<version>.json`.
* Phase A Part 1 (governance freeze, cluster identity remediation, frozen contracts) is done. **Next: Phase A Part 2**,
  after architect review.
* No predictive model has been trained. The 2025-26 lockbox is untouched (`LOCKBOX_POLICY.md`).

## Frozen contracts (design / documentation only; nothing implemented)

`POINT_IN_TIME_POLICY_v2_0.md`, `docs/BITEMPORAL_CHECKPOINT_SCHEMA.md`, `docs/POSSESSION_AND_LINEUP_RECONSTRUCTION_POLICY.md`,
`docs/MODEL_ELIGIBILITY_POLICY.md`, `docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md`,
`PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, `docs/PREDICTION_LEDGER_SCHEMA.md`,
`WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md`, `docs/LOCKBOX_OPENING_PROTOCOL.md`,
`docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md`, `docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md`,
`docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md`, `docs/OFF_MACHINE_BACKUP_PLAN.md`.

## Rebuild (2 DuckDB threads; never two pipelines at once)

```bash
.venv/bin/python -m cbb_platform.build_canonical          # canonical DB incl. v2.4 identity remediation
.venv/bin/python -m cbb_platform.dataset_manifest         # dataset version
.venv/bin/python -m cbb_platform.identity_cluster_audit   # docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md
.venv/bin/python -m cbb_platform.stage3.build             # PIT features, labels, folds
.venv/bin/python -m pytest -q
```

Historical directives (e.g. `docs/STAGE_1_DATA_PLATFORM_FOUNDATION_DIRECTIVE.md`) are preserved as records. They are not
the next instruction.
