# CBB Originator Model

A point-in-time, interconnected NCAA men's basketball player-prop model, its historical data platform and its
2026-27 prospective shadow operation.

> **DO NOT MODIFY FROZEN PRODUCTION ARTIFACTS IN PLACE.**
> - The four JM1_FINAL artifacts in `modeling/player_props_v2/` are hash-verified on every run.
> - A changed byte blocks scoring (`ARTIFACT_HASH_MISMATCH`).
> - A new model is a new artifact, a new registry entry and an architect promotion. It is never an edit.

## Current state (2026-10-24)

| item | value |
|---|---|
| current champion | **JM1_FINAL**, frozen at `e1353e5` (tag `cbb-v2-jm1-final-2026-27`), trained through 2025-26. Registry: `modeling/MODEL_REGISTRY.json` |
| model version | `cbb-v2-jm1-final-shadow-1.0.0` (`cbb_platform/prod/config.py`) |
| supported stats | PTS, REB, AST, FG3M, STL, BLK, TOV, plus combos PRA, PR, PA, RA and STOCKS (sums of the same joint draws) |
| distributions | conditional on playing; P(play) is reported beside the prices; one 20,000-draw joint bank per game; cutoff TIP_MINUS_1H |
| operation | 2026-27 **prospective shadow**: SHADOW_ONLY, `WAGERING_ENABLED = False`, **public launch HOLD** |
| scheduler | `.github/workflows/cbb-shadow-tick.yml` on `main` (GitHub-hosted). It checks out code from `release/cbb-v2-prospective-shadow`; evidence goes to `ops/cbb-2026-27-shadow-state` |
| rollback | S0 = Phase 1B marginals + Phase 2 J2 (documented, not wired for scoring) |
| historical dataset | `cbb_data_v2.4+5f19d31e524a`, status RESEARCH_CANDIDATE (the dataset manifest label; the frozen model was fit on it under the lockbox and freeze protocols) |

## Where things are

- **Production scoring starts at** `cbb_platform/prod/shadow_tick.py: main` → `step_score` → `cbb_platform/prod/run.py:
  daily`.
  - The full chain (loader → features → minutes → rates → JM1 → pricing → serialization) is in
    `docs/REPOSITORY_MAP.md` §1.
  - Every production constant (seasons, cutoff, artifacts, budgets, paths) is in `cbb_platform/prod/config.py`.
- **Model artifacts:** `modeling/player_props_v2/` (production and V2 research); `modeling/player_props_v1/`,
  `player_props_v0/` (superseded generations).
- **Research:**
  - `cbb_platform/v2/` holds the V2 phase experiments. Several of its modules are also frozen production
    dependencies; do not edit them.
  - Reports are in `modeling/player_props_v*/`.
  - Rejected and closed experiments (JM2, Phase 3 S2/S3, R1, R2, PTS weighting, calibration audit) are kept as
    evidence on their `research/*` branches and reports.
- **Data platform (historical):** the `cbb_platform/*.py` stage modules and `cbb_platform/stage3/`. The canonical
  stores live under `data/` (gitignored, rebuildable). The dataset manifest is `data/manifests/DATASET_MANIFEST.json`.
- **Stabilization record:** `docs/STABILIZATION_GATE_2026-10-24.md`.

## How production state is generated

- **Every tick:**
  - The shadow tick restores the encrypted working set (`--restore`).
  - It runs an incremental CBBD 2026-27 sync when due (`prod/sync.py`).
  - It scores the games whose cutoff is within 50 minutes (`run.daily`).
  - It settles completed games and writes the reports.
  - It commits the evidence tree to the ops branch and persists the encrypted working set (`--persist --commit
    --push`).
- **History:** the frozen 2025-26 lockbox canonical DB (local) or the encrypted history extract (on the runner).
- **Gate:** `prod/due_check.py` decides from `DUE.json` whether any work is due. When nothing is due, the runner
  never installs Python dependencies.

## Commands

**Safe anywhere** (read-only, no network, no credentials):

```bash
.venv/bin/python -m cbb_platform.prod.run verify-artifacts          # sha256 + identity of the frozen artifacts
.venv/bin/python -m pytest -q tests/test_prod_shadow.py tests/test_prod_golden_equivalence.py tests/test_model_registry.py
.venv/bin/python -m pytest -q tests/test_prod_live_feed.py tests/test_prod_odds_feed.py tests/test_prod_shadow_tick.py
.venv/bin/python -m cbb_platform.prod.smoke --synthetic-history --work data/prod/ci_smoke   # labeled FIXTURE data only
```

**Production (the scheduler runs these; do not run them on a Mac while the workflow is enabled):**

```bash
python -m cbb_platform.prod.shadow_tick --ops-root ../ops --restore --persist --commit --push   # GitHub Actions only
```

**Research only** (heavy, local stores required; these never touch production state):

```bash
.venv/bin/python -m cbb_platform.build_canonical          # canonical DB (2 DuckDB threads; never two pipelines at once)
.venv/bin/python -m cbb_platform.dataset_manifest         # dataset version
.venv/bin/python -m cbb_platform.identity_cluster_audit   # docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md
.venv/bin/python -m cbb_platform.stage3.build             # PIT features, labels, folds
```

**Never:**
- Rerun `cbb_platform.v2.jm1_final_production final` (the one-time final refit).
- Edit an artifact listed in `modeling/MODEL_REGISTRY.json`.
- Commit `data/prod/` or any credential.
- Commit `data/qa/game_identity_questionable_only.csv` (it stays untracked by architect ruling I).

The full test suite is long and needs the local frozen stores. Production work runs the focused set above (the CI list
is in `.github/workflows/cbb-shadow-ci.yml`).

## Credentials

- Credentials are read from the environment only (`cbb_platform/credentials.py`): `CBBD_API_KEY`, `ODDS_API_KEY`
  and `CBB_OPS_ENCRYPTION_KEY`.
- On the runner they are GitHub Actions secrets. They are never written to disk and never logged.

## Governance documents

1. `docs/ARCHITECTURE_PRECEDENCE.md`: which document wins, and which older documents are historical records.
2. `CBB_Blueprint_v1_4.md`: the governing architecture (it supersedes Blueprint v1.3 on the topics it lists).
3. `PROJECT_EXECUTION_ORDER_v1_1.md`: Phases A-F.
4. `docs/STAGE_CROSSWALK.md`: old stage numbers → implemented stages → phases.
5. Frozen contracts:
   - point in time and checkpoints: `POINT_IN_TIME_POLICY_v2_0.md`, `docs/BITEMPORAL_CHECKPOINT_SCHEMA.md`;
   - data, eligibility and sources: `docs/POSSESSION_AND_LINEUP_RECONSTRUCTION_POLICY.md`,
     `docs/MODEL_ELIGIBILITY_POLICY.md`, `docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md`,
     `docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md`, `docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md`;
   - scoring, ledger and publication: `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`,
     `docs/PREDICTION_LEDGER_SCHEMA.md`, `WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md`;
   - lockbox, environment and backup: `docs/LOCKBOX_OPENING_PROTOCOL.md`, `docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md`,
     `docs/OFF_MACHINE_BACKUP_PLAN.md`.

Historical directives (for example `docs/STAGE_1_DATA_PLATFORM_FOUNDATION_DIRECTIVE.md`) are preserved as records.
They are not the next instruction.
