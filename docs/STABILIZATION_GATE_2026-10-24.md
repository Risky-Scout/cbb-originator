# Repository stabilization gate (2026-10-24)

**Scope:** one bounded stabilization pass before the production / publication / betting directive. It made no model
change, no refit, no rename, no move, no history rewrite and no CBBD or Odds API call.

**Branch:** `stabilization/repo-gate-2026-10-24`, from `ca00d6a` (`release/cbb-v2-prospective-shadow`). It is local
and not pushed.
- It does not touch the release branch the scheduler checks out.
- The production/publication directive is NOT started here; it waits for architect review.

Companion documents:
- `docs/REPOSITORY_MAP.md`: the production path, the classification of every module, and the data layout.
- `modeling/MODEL_REGISTRY.json`: the artifact registry.
- `README.md`: rewritten.

## 1. Baseline (before any change)

| item | value |
|---|---|
| start SHA | `ca00d6a89a1b487c7e4928641c3a7147bbb84049` |
| git status | `?? data/qa/game_identity_questionable_only.csv` only (ruling-I exception; sha256 `5ca9001c…15b2`, unchanged) |
| champion artifacts (sha256) | marginal `bfcef1fb…d10f`, jm1_final `b932fa21…28f9`, residual_j2 `e4254867…70f1`, production_manifest `7d83e9cf…cbcab`; rollback J2 `b41564a9…b1ff` |
| frozen files vs `e1353e5` | `git diff e1353e5 HEAD` is empty for the 5 artifacts and for all predictive code (`cbb_platform/v2`, `props_v0*`, `props_v1_pricing`, `stage3`) |
| `prod.run verify-artifacts` | PASS (model `cbb-v2-jm1-final-shadow-1.0.0`, JM1_FINAL, commit e1353e5, season 2027, training through 2026) |
| focused tests | **135 passed** in 56 s. The CI production set, plus `test_v2_jm1_final_production`, `test_props_v1_pricing`, `test_relocation` |
| golden fixture | written from the unchanged code and committed alone (`a02cabb`) before any cleanup |

**Golden fixture:** `tests/test_prod_golden_equivalence.py` with `tests/fixtures/golden/prod_equivalence_v1.json`.
- **Inputs:** the labeled synthetic FIXTURE history, the 2026-27 increment and the fixture quotes, run through
  `run.daily`. It uses the frozen artifacts, 20,000 draws per game and temporary roots only.
- **What it pins:**
  - the model identity and artifact hashes;
  - 50 model-input rows (P(play), minutes μ/σ, the 7 stat μ/φ);
  - 2 JM1 banks (the draws' sha256, players, min eigenvalue);
  - all 607 PREDICTION records (341 base-stat pmf rows, 242 combo rows drawn from the same bank, 24 not priced, plus
    quoted lines with probability, push, fair price and edge);
  - the 12-row board.
- **Comparison:** exact on the recording platform (darwin-arm64). Off-platform, the documented tolerance applies.
- **Sensitivity:** the comparator was checked to flag a 1e-10 change in an input, a base-stat projection or a combo
  projection, and any bank-hash change.

## 2. Authoritative production path

- **CURRENT_CHAMPION:** JM1_FINAL. Its artifacts are listed in §1; frozen at commit `e1353e5`, tag
  `cbb-v2-jm1-final-2026-27`, model version `cbb-v2-jm1-final-shadow-1.0.0`.
- **PRODUCTION_ENTRYPOINT:** `cbb_platform/prod/shadow_tick.py: main()`.
  - Workflow: `.github/workflows/cbb-shadow-tick.yml` on `main`.
  - Command: `python -m cbb_platform.prod.shadow_tick --ops-root ../ops --restore --persist --commit --push`.
  - Chain: `_tick` → `step_score` → `prod/run.py: daily()`.
  - The cheap gate before it is `prod/due_check.py`.

**How the chain was traced:**
- statically: an AST import graph of all 120 modules;
- dynamically: a `sys.setprofile` trace of the golden capture, which lists every function that executes;
- the full step-by-step table is in `docs/REPOSITORY_MAP.md` §1.

**Summary of the chain:**
- loader: `prod/loader.verify/load` → `v2/jm1_final_production.load_production`
- features: `prod/state`, `slate`, `features` (→ `stage3`), `universe`
- minutes and participation: `prod/inputs.marginal_rows` → `props_v0.predict`, `_band_sigma` and
  `props_v0_participation.predict`; joint minutes come from `v2/jm1_final.simulate_exposure` →
  `joint_minutes_v2.raw_minutes`
- rates: `props_v0.predict`, plus `v2/structural.env_predict`, `log_mu_terms` and `anchor`
- JM1: `prod/simulate.simulate_game` → `v2/jm1_final_production.score_game` (via `phase3._game_input`,
  `dependence.game_correlation`, `cholesky_unit`, `structural.mixture_tables`, `counts`, `mixture_pmf`) → combos via
  `joint.derived_markets`
- pricing: `prod/pricing.price_line` → `props_v1_pricing.price`
- serialization: `prod/board`, `ledger`, `side_monitor`, `bundle`, `ops_store`, `prospective_monitor`

**One production path:**
- `prod/run.py daily` is the same function that the tick, the smoke test and the CI call.
- The other paths that can score are classified in `docs/REPOSITORY_MAP.md` §2, and none of them is reachable from
  the tick:
  - LEGACY: the v1 `lockbox_runner` / `props_v1_refit`;
  - RESEARCH_ONLY: the V2 phase CLIs and the one-off rehearsal.

## 3. Repository map

`docs/REPOSITORY_MAP.md` is created. It covers:
- production code and frozen artifacts;
- research, protocols and QA;
- ingestion, sportsbook and publishing code;
- tests and scripts;
- legacy areas;
- generated and local-only data.

## 4. Generated and accidental files

**Tracked files:**
- No `__pycache__`, `.pyc`, `.DS_Store`, notebook checkpoint, DuckDB, `.env` or key file is tracked.
- Eight tracked `*.log` files under `data/qa/pre_model_closure/` are evidence logs: they are referenced by the closure
  record and stay.

**Removed** (local, already gitignored, regenerable, with no scientific or production role):
- 7 `__pycache__/` directories (192 `.pyc` files, some stale bytecode for modules that exist only on other
  branches);
- `.pytest_cache/`;
- the root `.DS_Store`;
- a stale 162-byte Word owner file `docs/~$B_Blueprint_v1_3.docx` from 2026-10-07.

**Untouched:** `data/qa/game_identity_questionable_only.csv`.
- It is user-owned and untracked by architect ruling I. The clean-tree gate expects exactly this one untracked path.
- It is therefore deliberately NOT gitignored.

**Large tracked artifacts** (candidates for release or object storage later; nothing is moved now):

| file | size |
|---|---|
| `data/manifests/cbbd_backfill_manifest.json` | 13.4 MB |
| `data/manifests/cbbd_request_log.jsonl` | 12.2 MB |
| `data/qa/player_identity_cluster_audit.csv` | 4.9 MB |
| `data/qa/pre_model_closure/before_v2.3/player_identity_cluster_audit.csv` | 4.9 MB |
| `data/qa/player_pool_misses_after.csv` | 3.5 MB |
| `data/qa/superseded/cbbd_box_route_full_reconciliation.route-1.1.0.json` | 3.2 MB |
| `modeling/player_props_v1/sample_predictions.csv` | 2.6 MB |

All are reproducibility evidence; their canonical home is git until an object store is designated.

**Local-only generated stores** (gitignored, about 25 GB): `data/canonical`, `data/lockbox_2025_26` canonical and
stage3, `data/raw`, `modeling/store`, `modeling/features`, `modeling/v2_oos`, `data/qa/large` and `data/prod`.
- They are rebuildable or recorded by sha256 in tracked manifests.
- `data/lockbox_2025_26/canonical/cbb.duckdb` is the frozen history that production reads. Its runner copy is the
  encrypted history extract.

## 5. Secrets and credentials audit

**What was scanned:**
- the current tree (tracked and untracked files);
- every added line of the full history of all 48 local and remote-tracking refs (81 commits), with the ops branch
  fetched at `7acc8c2`.

**Patterns:** private-key blocks, GitHub / AWS / OpenAI / Anthropic / Slack / Google tokens, `key|secret|token|password=`
assignments, `?apiKey=` query strings, bearer headers and credentialed DB URLs.

**Live-value check:** the current values of `CBBD_API_KEY` and `ODDS_API_KEY` were compared byte-for-byte against the
tree and the history, with 0 occurrences. `CBB_OPS_ENCRYPTION_KEY` is not present locally, so it was checked by
pattern only.

| SECRET_TYPE | FILE/PATH | STATUS | ROTATION_REQUIRED |
|---|---|---|---|
| synthetic test sentinel (`KEY_ASSIGNMENT`) | `tests/test_stage1_final.py` | FAKE: a fixed non-credential string used to prove keys are never written to disk | FALSE |
| CBBD API key | environment / GitHub secret `CBBD_API_KEY` | never committed (0 hits by value and by pattern) | FALSE |
| The Odds API key | environment / GitHub secret `ODDS_API_KEY` | never committed (0 hits by value and by pattern); the rehearsal request ledger stores the URL without the key | FALSE |
| ops AES passphrase | GitHub secret `CBB_OPS_ENCRYPTION_KEY` | never committed (pattern scan) | FALSE |
| SSH / deploy material | none in the repository | NONE FOUND | FALSE |
| hard-coded local paths | see §7 (a username in recorded paths; not a credential) | DOC / LEGACY evidence | FALSE |

## 6. Configuration inventory

| CONFIG_SOURCE | PURPOSE | AUTHORITATIVE? | DUPLICATE / CONFLICT? |
|---|---|---|---|
| `cbb_platform/prod/config.py` | production seasons, cutoff, artifacts and hashes, model version, draws, budgets, quota reserves, freshness, ops cadence, paths, publication path (unapproved) | **YES** (production) | — |
| `cbb_platform/config.py` | project root, data paths, Starter Pack root, ingestion / schema versions, DuckDB threads | YES (data platform) | — |
| `cbb_platform/stage3/spec.py` | model seasons, `CUTOFF_RULE`, `CUTOFF_OFFSET_HOURS` | YES; production imports it (no copy) | — |
| `modeling/player_props_v2/jm1_final_production_manifest_2026_27.json` | frozen model identity, seasons, stats, combos, seed policy, draws | YES (frozen artifact) | equal to `prod/config.py`; the loader asserts the equality on every run |
| `.github/workflows/cbb-shadow-tick.yml` (on `main`) | cron cadence, code branch, the 4 artifact sha256 | YES (scheduler) | DUPLICATE hashes: **equal** to `PC.ARTIFACTS` (checked) |
| `.github/workflows/cbb-shadow-ci.yml` | CI test list, the 4 artifact sha256 | YES (CI) | DUPLICATE hashes: **equal** (checked) |
| `v2/jm1_final_production.py` (`PRODUCTION_SEASON`, `ROLLBACK`) | freeze-time constants | frozen code | DUPLICATE: 2027 / rollback sha **equal** |
| `v2/marginal.py` (`PRODUCTION_SEASON = 2027`, `V2_MAX_SEASON`), `v2/structural.py`, `v2/jm1_final.py` (`MAX_SEASON = 2026`) | research season guards | frozen code | DUPLICATE: **equal** to `PC.PRODUCTION_SEASON` / `PC.TRAINING_THROUGH` |
| `v2/joint.py` `SIMS = 20_000`, `SIM_SEED`; `structural.SEED_BASE`; `joint_minutes_v2.SEED_BASE` | draws and seeds | frozen code | `PC.SIMS` **equal** (the loader asserts the manifest draws = `PC.SIMS`) |
| `cbb_platform/lockbox.py` `LOCKBOX_SEASON = 2026` | research lockbox guard (refuses 2026-27) | YES (research) | separate on purpose from the production season admission |
| `cbb_platform/cbbd_client.py` `QUOTA_RESERVE = 20_000` vs `PC.CBBD_QUOTA_RESERVE = 1_000` | research backfill client vs production feed | each in its own scope | same concept, two values, two clients: **not a conflict** (production never uses the research client) |
| `prod/prospective_monitor.py` `EARLY_MARKETS`, `TRANSFER_MARKETS` vs `prod/side_monitor.py` `OPENING_MARKETS`, `TRANSFER_MARKETS` | diagnostic monitoring strata | each module's own report | **CONFLICT (name, not math):** the same names hold different market lists in two modules. Diagnostic only, never reaches a forecast. Review before publication |
| `prod/quotes.py` `MARKET_KEYS`, `SPORT_KEY`; `prod/matching.py` `COMMENCE_TOLERANCE` | sportsbook market map, identity tolerance | YES (single definition) | — (no sportsbook whitelist exists: all books the provider returns for region `us`) |
| `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md` | no-publish rules | YES (policy) | **CONFLICT for publication:** the policy withholds a publish when the schedule version is older than 2 h at T1H; the code's `SCHEDULE_MAX_AGE_HOURS = 36` (STALE_STATE). Harmless in SHADOW_ONLY; it must be resolved in the publication directive |
| `WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md` vs `PC.PROPOSED_PUBLIC_PATH` | publication target | neither approved | **CONFLICT:** the spec says POST `/api/cbb/originator/v1/boards` *(confirm)*; config has a static path `/tools/odds-scanner/.../player-props/` marked NOT approved |
| `docs/PROBABILITY_ENGINE_POLICY.md` (≥100,000-draw diagnostic) vs `PC.SIMS = 20,000` | exact-vs-simulation validation vs production draws | different purposes | not a conflict |
| `requirements.lock.txt` (hashes), `uv.lock`, `requirements.txt`, `pyproject.toml`, `ENVIRONMENT_FINGERPRINT.json` | environment | the lock file is what the runner installs | not consolidated (no change made) |
| `PROJECT_MANIFEST.json` | authoritative document list | YES (governance) | — |

Nothing was consolidated: none of the duplicates is a conflict in value, and the three real conflicts are open
decisions for the architect.

## 7. Absolute local paths

| occurrence | class |
|---|---|
| `cbb_platform/relocation.py:25` `HISTORICAL_PROJECT_PARENT` | LEGACY (by design) |
| `tests/test_relocation.py:31` | TEST_ONLY |
| `docs/CBB_ORIGINATOR_ARCHITECTURE_AMENDMENT_AND_STAGE1_DIRECTIVE_V2.md`, `docs/STARTER_PACK_PHYSICAL_AUDIT.md`, `docs/lockbox/ARCHITECT_RULINGS_2026-10-16_ROUTE_CLOSURE.md` | DOC_ONLY |
| `data/manifests/LOCKBOX_READINESS_v2.json` (10), `starter_pack_registration.json`, `starter_pack_sha256.json`, `workspace_snapshot_*.json`, `data/qa/*pit_audit.json`, `data/qa/pre_model_closure/full_test_suite.log` | LEGACY (frozen evidence: recorded paths are never rewritten; `relocation.resolve` maps them) |

- **About `relocation.py`:** it is the one known 2026-10-15 project move. Only research provenance and lockbox
  readiness import it, never the tick. On the runner `relocated()` is simply true, and the historical path is never
  opened.
- **PRODUCTION_BLOCKER: none.** The production path runs on GitHub-hosted Ubuntu: scheduled runs `39956418792`,
  `40006249663` and `40048186895` succeeded, as did CI run `40068933507` on `ca00d6a`. Every production path derives from `PROJECT_ROOT`, `--ops-root`
  or environment variables, so nothing was rewritten.

## 8. Import / dead-code audit

**Static and dynamic map:** `docs/REPOSITORY_MAP.md` §1–2.
- Production loads 73 modules at runtime.
- 37 of them execute functions during scoring. The others are import-only and must stay importable.

**Candidates for later removal** (nothing deleted):

| path | why |
|---|---|
| `cbb_platform/adapters/torvik.py` | DEAD: non-network skeleton; no importer, no test |
| `cbb_platform/prod/monitoring.py` | LEGACY: superseded in operation by `prospective_monitor.py`; used only by `test_prod_shadow.py` |
| `cbb_platform/prod/deploy_dryrun.py` | LEGACY / pre-publication: keep until the publication directive decides the deploy path |
| `lockbox_runner.py`, `props_v1_refit.py`, `props_v0_eval.py` CLIs | LEGACY v1 scoring and evaluation; their modules are still imported (not executed) by production through `v2` code, so they cannot be removed without a frozen-code change |

**Duplicate implementations:**
- **Pricing:** none. Production pricing has one path (`prod/pricing.price_line` → `props_v1_pricing.price`), and the
  v1 baseline used the same function.
- **Model loader:** the obsolete loader is the v1 `lockbox_runner` artifact loader (LEGACY). `prod/loader.py` is the
  one production loader.

**No UNKNOWN modules:** every module has an importer, a test, a documented `python -m` CLI, or is the one DEAD
skeleton.

## 9. Artifact governance

`modeling/MODEL_REGISTRY.json` is created. It records:
- the champion's name, model and artifact version, git commit and tag, artifact paths and sha256;
- training-through, validation status and promotion dates;
- the rollback artifact (S0, not wired);
- the superseded v1 baseline;
- the research artifacts and the rejected-experiment evidence.

It mirrors the runtime source of truth (`prod/config.py`). `tests/test_model_registry.py` proves the registry, the
config and the bytes on disk agree, and that research and rollback artifacts are distinct from the production ones. No
artifact was modified.

## 10. Branch / experiment inventory

| BRANCH | PURPOSE | LATEST_SHA | STATUS | MERGE_ALLOWED? | KEEP? | ARCHIVE_LATER? |
|---|---|---|---|---|---|---|
| `main` | default branch: scheduler workflow (cron enabled) | d5824da | ACTIVE (scheduler) | n/a | YES | NO |
| `release/cbb-v2-prospective-shadow` | production code the cron checks out | ca00d6a | **AUTHORITATIVE PRODUCTION** | n/a | YES | NO |
| `ops/cbb-2026-27-shadow-state` | live shadow evidence (bot commits) | 7acc8c2 (remote) | ACTIVE (append-only) | NEVER | YES | NO |
| `research/cbb-v2-interconnected` | V2 research history incl. Phase 1–3, JM2, JM1_FINAL; champion freeze | e1353e5 | FROZEN champion source (tag `cbb-v2-jm1-final-2026-27`) | NO | YES | NO |
| `release/cbb-v2-shadow-ready` | shadow integration | 780673f | SUPERSEDED (ancestor of production) | already in production lineage | YES | YES |
| `release/cbb-v2-live-data-feed` | 2026-27 CBBD feed | 5c370b2 | SUPERSEDED (in production lineage) | already merged | YES | YES |
| `release/cbb-v2-odds-quotes` | Odds API quote feed | f4d5829 | SUPERSEDED (in production lineage) | already merged | YES | YES |
| `release/cbb-v2-directional-bias-audit` | directional / root-cause audit (diagnostic) | 35d8705 | CLOSED (in production lineage) | already merged | YES | YES |
| `integration/main-shadow-tick-workflow` | prepared workflow (schedule commented) | de08de5 | SUPERSEDED by main d5824da | NO | YES | YES |
| `research/cbb-v2-pts-rate-challenger` | R1 PTS rate challenger | e4b6e3d | **R1_REJECT** | **NO** | YES (evidence) | YES |
| `research/cbb-v2-pts-rate-challenger-r2` | R2 PTS rate challenger | 49d1a99 | **R2_EARLY_REJECT** | **NO** | YES (evidence) | YES |
| `research/cbb-v2-pts-rate-constrained-selection` | constrained weighting selection | 734bb25 | **WEIGHTING_FAMILY_INFEASIBLE** | **NO** | YES (evidence) | YES |
| `research/cbb-v2-pts-rate-calibration-audit` | calibration audit; campaign closeout | fe19fcb | **NOT_JUSTIFIED / CLOSED** | **NO** | YES (evidence) | YES |
| `release/cbb-v1-production` | v1 production baseline (tag `cbb-props-v1-production-baseline`) | cc56eea | LEGACY (superseded) | NO | YES | YES |
| `release/cbb-v1-prelockbox`, `lockbox/open-2025-26` | v1 lockbox readiness, lockbox GO | d65aac9, 1d8d01f | LEGACY evidence | NO | YES | YES |
| `modeling/cbb-props-production-candidate-v1`, `modeling/cbb-v1-nightly-refit-validation` | v1 candidate refit through 2024-25 | c123a3b | LEGACY evidence | NO | YES | YES |
| `modeling/player-props-challenger-v0`, `modeling/player-props-v0-participation` | v0 challenger, participation | 53c0c2c, f9cf4df | LEGACY evidence | NO | YES | YES |
| `research/stage3-pit-feature-store` | Pre-Model Integrity Part 1 | e691b11 | LEGACY evidence | NO | YES | YES |
| `stabilization/repo-gate-2026-10-24` | this gate | (this commit) | AWAITING ARCHITECT REVIEW | after review only | YES | — |

- **Where the other experiments live:** the Phase 3 S2/S3 and JM2 work is in the `research/cbb-v2-interconnected`
  history (reports `PHASE3_*`, `JM2_POSTMORTEM.md`). No separate S1–S4 branch exists.
- **What this gate did to branches:** none was merged, deleted or rewritten.

## 11–12. Tests after cleanup

See the final report (`AFTER_TESTS`).
- The after-run repeats the baseline set and adds the golden, registry and README-governance tests.
- The champion hash and the JM1 outputs are compared against §1.

## Files requiring later refactor (not done here)

1. **Artifact hashes are hard-coded in four places:** `prod/config.py`, both workflow YAMLs, and
   `jm1_final_production.ROLLBACK`. All are equal today. Later, the workflows could check against
   `modeling/MODEL_REGISTRY.json`. That is a workflow change, so it needs approval.
2. **`tests/test_prod_golden_equivalence.py` and `tests/test_model_registry.py` are not yet in
   `.github/workflows/cbb-shadow-ci.yml`.** Adding them is a CI workflow change, so it needs approval.
3. **Production scoring calls functions inside research-named frozen modules** (`v2/phase3._game_input`,
   `jm1_final.simulate_exposure`, `joint_minutes_v2`). Extracting a dedicated scoring module would change the
   protocol-pinned code sha256, so it is a new freeze and promotion, not a cleanup.
4. **`prod/shadow_tick.py` (963 lines) and `prod/run.py: daily` (about 230 lines)** are long, single-function
   compositions. Split them only together with the publication work, with the golden fixture as the guard.
5. **Monitor market lists:** `EARLY_MARKETS` / `OPENING_MARKETS` and the two `TRANSFER_MARKETS` should get
   distinct names or one definition (diagnostic only).
6. **Publication settings:**
   - the schedule-age rule (policy 2 h vs code 36 h);
   - the target (spec API vs `PROPOSED_PUBLIC_PATH`).
   Both belong to the publication directive.
7. **Removal candidates:** `adapters/torvik.py` (DEAD) and `prod/monitoring.py` (LEGACY).
8. **Large tracked evidence files** (§4): move them to release or object storage once a canonical store is
   designated.
