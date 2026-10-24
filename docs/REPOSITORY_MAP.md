# Repository map (stabilization gate, 2026-10-24)

**What code produces tonight's predictions?**
- The scheduled GitHub Actions workflow `.github/workflows/cbb-shadow-tick.yml`, which lives on `main`, runs
  `python -m cbb_platform.prod.shadow_tick --ops-root ../ops --restore --persist --commit --push`.
- The code it runs is checked out from `release/cbb-v2-prospective-shadow`.
- `shadow_tick._tick` → `step_score` → `cbb_platform/prod/run.py: daily()` scores each game on the frozen JM1_FINAL
  artifacts (commit `e1353e5`), using one 20,000-draw joint bank per game.
- Output is SHADOW_ONLY. Nothing is published and nothing is wagered.

**Rule:** DO NOT MODIFY FROZEN PRODUCTION ARTIFACTS IN PLACE (`modeling/MODEL_REGISTRY.json`).

## 1. Production path (AUTHORITATIVE)

The chain was traced statically (an AST import graph) and dynamically. The dynamic trace (a `sys.setprofile` run of
`tests/test_prod_golden_equivalence.py::capture`) lists every function that actually executes.

| step | module : function |
|---|---|
| scheduler gate | `prod/due_check.py: check` (standard library only; reads `DUE.json` from the ops checkout) |
| **entry point** | `prod/shadow_tick.py: main → tick → _tick` (plan, sync, events, validate, quotes, score, missed, settle, reports, finish) |
| scoring composition | `shadow_tick.step_score → prod/run.py: daily` |
| model loader | `prod/loader.py: verify, load` (sha256 + identity checks of the 4 artifacts) → `v2/jm1_final_production.py: load_production` |
| state | `prod/state.py: current, update, validate, staleness`. CBBD 2026-27 feed: `prod/sync.py`, `prod/cbbd_acquire.py`, `prod/cbbd_feed.py`. History: `prod/history_extract.py` |
| slate / cutoff | `prod/slate.py: build, feature_cutoff` (TIP_MINUS_1H) |
| features | `prod/features.py: build, build_features, model_frame, compact_canonical` → `stage3/player_state, team_state, participation, pitlib, sources, spec, build.connect` |
| pregame universe | `prod/universe.py: build, suppression_map` |
| minutes / participation | `prod/inputs.py: marginal_rows` → `props_v0.py: predict, _band_sigma` (minutes μ, σ) and `props_v0_participation.py: predict` (P(play)). Joint minutes draws: `v2/jm1_final.py: simulate_exposure` → `v2/joint_minutes_v2.py: raw_minutes` |
| stat-rate models | `props_v0.py: predict` (the 7 marginal means). `prod/inputs.py: environment` → `v2/structural.py: env_predict`. In-bank: `structural.log_mu_terms, anchor, rates` |
| JM1 / dependence | `prod/simulate.py: simulate_game` → `v2/jm1_final_production.py: score_game` → `v2/phase3.py: _game_input`, `v2/dependence.py: game_correlation, cholesky_unit, game_seed`, `v2/structural.py: mixture_tables, counts, mixture_pmf` |
| combos | `prod/simulate.py: market_distributions, same_draw_check` → `v2/joint.py: derived_markets` (sums of the same draws) |
| quotes | `prod/odds_acquire.py`, `prod/odds_feed.py`, `prod/quotes.py: normalize_event, load_collected`, `prod/matching.py: match_games, match_players` |
| pricing | `prod/pricing.py: price_line, side_view, expected_value` → `props_v1_pricing.py: price` |
| board / readiness | `prod/board.py: build, quote_freshness`, `prod/readiness.py: evaluate, cap` (ceiling SHADOW_ONLY) |
| serialization | `prod/ledger.py: append, verify` (hash chain); `prod/side_monitor.py: append`; the run directory (`board.csv`, `model_inputs.parquet`, `run_summary.json`); `prod/bundle.py: write, verify` |
| evidence / reports | `prod/ops_store.py` (ops branch, encrypted working set), `prod/prospective_monitor.py: write_reports` |
| constants | `prod/config.py` (the single source for production seasons, cutoff, artifacts, budgets and paths) |

**Research-named modules on the production path.** The `v2/` modules `jm1_final_production`, `jm1_final`,
`joint_minutes_v2`, `phase3`, `structural`, `dependence`, `joint`, `latent` and `marginal`, plus `props_v0`,
`props_v0_participation` and `props_v1_pricing`, are runtime dependencies of production scoring. They are FROZEN:
- `tests/test_prod_shadow.py::test_frozen_predictive_code_unchanged` pins their source sha256 to the freeze
  protocol;
- `tests/test_prod_golden_equivalence.py` pins their outputs.

**Do not move, rename or edit them.**

**Imported by production but never executed by scoring** (they must stay importable):
- `v2/phase1b`, `v2/phase2`, `v2/phase3_prep`, `v2/provenance`
- `lockbox`, `lockbox_runner`
- `props_v0_eval`, `props_v1_refit`
- `stage3/event_stats`, `labels`, `lineup_stats`, `universe`
- `build_canonical`, `cbbd_box_route`, `event_build`, `source_fetch`, `raw_store`
- `audit_starter_pack`, `adapters/cbbd`, `credentials`

## 2. Other entry points (classification)

| path | class | role |
|---|---|---|
| `prod/run.py` CLI (`daily`, `verify-artifacts`, `verify-ledger`, `verify-bundle`) | AUTHORITATIVE (manual / CI) | the same `daily()` the tick calls; CI uses `verify-artifacts` |
| `prod/smoke.py`, `prod/fixtures.py`, `prod/feed_fixtures.py`, `prod/odds_fixtures.py` | AUTHORITATIVE test support | labeled synthetic FIXTURES only; CI smoke |
| `prod/deploy_dryrun.py` | LEGACY / pre-publication | local dry run of the NCAAF Wizard pattern; deploys nothing |
| `prod/monitoring.py` | LEGACY | earlier read-only warning monitor; superseded in operation by `prospective_monitor.py`; used only by tests |
| `prod/directional_audit.py`, `prod/root_cause_audit.py` | RESEARCH_ONLY (diagnostic), except three helpers | closed PTS bias campaign. Production `side_monitor` imports `directional_audit.min_band`, `pplay_band` and `EARLY_TEAM_GAMES` at runtime, so `directional_audit.py` must stay importable and unchanged |
| `rehearsal/historical_payload.py` | RESEARCH_ONLY (one-off) | 2026-10-23 pre-season rehearsal; nothing in `prod/` imports it |
| `v2/jm1_final_production.py` CLI `final` | RESEARCH_ONLY (frozen) | the one-time final refit that wrote the artifacts. **Never rerun** |
| `lockbox_runner.py`, `props_v1_refit.py`, `props_v0*.py` CLIs | LEGACY | v1 production baseline (cc56eea) and lockbox scoring; superseded by JM1_FINAL |
| `v2/marginal.py`, `phase1b.py`, `phase2.py`, `phase3.py`, `structural.py`, `joint_minutes_v2.py`, `jm1_final.py` CLIs | RESEARCH_ONLY | V2 phase experiments. Their *functions* are frozen production dependencies (§1); their *CLIs* are research |
| `build_canonical`, `dataset_manifest`, `stage2_pipeline`, `stage3/build`, `cbbd_*`, `event_*`, `game_*`, `player_identity*`, `identity_cluster_audit`, `team_attribution`, `wallclock_audit`, `coverage`, `eligibility_manifests`, `pit`, `lockbox_*`, `relocation` | RESEARCH_ONLY (data platform) | historical canonical build, QA and lockbox evidence. Never called by the tick |
| `adapters/torvik.py` | DEAD/UNREFERENCED | non-network interface skeleton (Stage 2S); no importer, no test. Candidate for later removal |
| `adapters/bdl.py`, `adapters/availability.py` | RESEARCH_ONLY | Stage 1/2 adapters with tests; not used by production |
| `docs/lockbox/*.py`, `*.patch` | LEGACY evidence | guard check scripts and patches kept as audit records |

No path is UNKNOWN: every module has an importer, a test, a documented `python -m` CLI, or is the one DEAD skeleton
above.

## 3. Frozen model artifacts

- **Production (immutable):** see `modeling/MODEL_REGISTRY.json` (`tests/test_model_registry.py`):
  - `modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json`
  - `modeling/player_props_v2/jm1_final_through_2025_26.json`
  - `modeling/player_props_v2/jm1_final_residual_dependence_through_2025_26.json`
  - `modeling/player_props_v2/jm1_final_production_manifest_2026_27.json`
- **Rollback (documented, not wired):** S0 = the marginal artifact above plus `modeling/player_props_v2/dependence_J2.json`.
- **Superseded v1 baseline:** `modeling/player_props_v1/final_prelockbox_2024_25_artifact.json`.
- **Research / evidence:** `modeling/player_props_v2/states/`, the `*_protocol.json`, `*_selection*.json`,
  `*_verification.json` and `*_confirmation*.json` files, and `oos_forecast_store*_manifest.json`. The OOS stores
  themselves are under `modeling/v2_oos/` (gitignored).

## 4. Research, protocols, reports

| location | content |
|---|---|
| `modeling/player_props_v0/`, `player_props_v1/`, `player_props_v2/` | per-generation reports (`*.md`), protocols, selections, scorecards, confirmations, postmortems (JM2, PTS campaign) |
| `modeling/eligibility/`, `modeling/folds/`, `modeling/FEATURE_REGISTRY.json`, `modeling/STAGE3_MANIFEST.json` | Stage 3 eligibility, walk-forward folds, feature registry |
| root `*.md` | governing policies: `CBB_Blueprint_v1_4.md`, `POINT_IN_TIME_POLICY_v2_0.md`, `LOCKBOX_POLICY_v2.md`, `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, `WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md`, `PROJECT_EXECUTION_ORDER_v1_1.md`, `CANONICAL_SCHEMA.md` (older versions are kept as records; `docs/ARCHITECTURE_PRECEDENCE.md` says which wins) |
| `docs/` | policies, audits and stage records. Production operation: `SHADOW_PRODUCTION_INTEGRATION.md`, `PROSPECTIVE_SHADOW_OPERATION.md`, `SHADOW_AUTOMATION_READINESS.md`, `SHADOW_AUTOMATION_ACTIVATION.md`, `OFF_MACHINE_DATA_LICENSE.json` |
| `docs/lockbox/` | 2025-26 lockbox rulings, incident, scorecard, guard patches |
| `docs/rehearsal/` | 2026-10-23 real-payload component rehearsal and its compact evidence |
| other branches | R1 / R2 / constrained selection / calibration audit work lives on its `research/*` branch (see `docs/STABILIZATION_GATE_2026-10-24.md` §10) |

## 5. Data

| location | tracked? | content |
|---|---|---|
| `data/manifests/` | yes | dataset manifests (every version), the CBBD backfill manifest and request log, Starter Pack registration, lockbox decisions |
| `data/qa/` | yes (compact) | coverage, identity, status, PIT and lockbox QA reports. `data/qa/pre_model_closure/*.log` are evidence logs |
| `data/qa/game_identity_questionable_only.csv` | **untracked by ruling I** | user-owned QA file. Never commit it, never gitignore it, never delete it (the clean-tree gate expects exactly this one untracked path) |
| `data/lockbox_2025_26/*.json` | yes | lockbox build and scoring records |
| `data/raw/`, `data/canonical/`, `data/staging/`, `data/lockbox_2025_26/canonical/`, `data/lockbox_2025_26/stage3/` | no (local, about 25 GB) | rebuildable stores. `data/lockbox_2025_26/canonical/cbb.duckdb` is the frozen history the production layer reads (`PC.HISTORY_CANONICAL`) |
| `data/prod/` | no (local only) | local production state, ledgers, runs, cache, ops checkout, dry-run sandbox. **Never commit** |
| `data/qa/large/`, `modeling/store/`, `modeling/features/`, `modeling/labels/*/`, `modeling/v2_oos/` | no | large generated research stores (sha256 values are recorded in tracked manifests) |
| ops evidence | branch `ops/cbb-2026-27-shadow-state` + release `cbb-2026-27-shadow-ops` | live shadow evidence tree; AES-encrypted working set (the key is in GitHub secrets only) |
| Starter Pack | sibling directory `../CBB Starter Pack/starter_pack` (read-only) | immutable upstream input, sha256-registered in `data/manifests/` |

## 6. Tests and CI

- Run the tests with `.venv/bin/python -m pytest -q <files>`. The full suite is long and needs the local frozen stores.
- **Production-focused set** (the CI list in `.github/workflows/cbb-shadow-ci.yml`):
  - `test_prod_shadow.py`, `test_prod_replay.py` (skips without the local stores)
  - `test_prod_live_feed.py`, `test_prod_odds_feed.py`
  - `test_prod_directional_audit.py`, `test_prod_side_monitor.py`
  - `test_prod_shadow_tick.py`
- **Added by this gate:** `test_prod_golden_equivalence.py` and `test_model_registry.py`.
- **Data platform and research tests:** `test_stage*.py`, `test_lockbox*.py`, `test_v2_*.py`, `test_props_*.py`, etc.

## 7. Generated and local-only (never commit)

- `.venv/`, `__pycache__/`, `.pytest_cache/`
- `data/prod/`, `data/raw/`, `data/canonical/`, `data/staging/`, the `*.duckdb` files
- `modeling/store|features|labels/*|v2_oos/`, `data/qa/large/`
- `.env*`, `*.key`, `secrets*`, `*.pem`, `id_rsa*`
- Office lock files `~$*`, `.DS_Store`

All of these are covered by `.gitignore`.
