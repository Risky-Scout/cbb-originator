# CBB Originator: 2026-27 daily walk-forward refit

**Status:** implemented on branch `production/cbb-daily-walk-forward-2026-27`, approved in principle by the architect
(2026-10-31), NOT merged, NOT wired into the live scheduler, NOT deployed. Architect directive of 2026-10-31 ("CBB ORIGINATOR, 2026-27 DAILY WALK-FORWARD RETRAINING AND CONTINUOUS
PRODUCTION LEARNING"). Code: `cbb_platform/walkforward/`. Tests: `tests/test_walkforward_*.py`.

**Architect decisions of 2026-10-31 (recorded here; precedence rule 5, `docs/ARCHITECTURE_PRECEDENCE.md`):**

1. **Daily parameter refits are APPROVED.** For the 2026-27 production system, approved JM1_FINAL *parameters* may refit
   daily, and a candidate auto-promotes only after every hard production gate passes. Predictive *architecture* changes
   stay prohibited without explicit architect approval. Feature search, methodology changes, hyperparameter search,
   distribution-family changes and challenger promotion stay DISABLED.
2. **New 2026-27 training-data eligibility:** IDENTITY_RESOLVED, BOX_RECONCILED, FINAL_STATUS, BOX_COMPLETE and
   POINT_IN_TIME_LEGALITY must all hold. An unresolved game is DEFERRED, never discarded, and enters a later version once
   valid (§3). The historical 2013-14..2025-26 training population is NOT changed. The ~158 historical 2025-26 games that
   would fail the new rule stay in the frozen population, so the opening reproduction stays exact.
3. **Monitoring thresholds:** `docs/walkforward/PROPOSED_MONITORING_THRESHOLDS.json` stays PROPOSED / report-only and
   never blocks promotion. Already-approved governance thresholds are preserved.

`DAILY_WALK_FORWARD_REFIT = ENABLED`.
The ARCHITECTURE stays frozen (JM1_FINAL). The PARAMETERS are re-estimated every morning by the SAME approved procedure
on newer legal data. This supersedes two things in operation:
- "FREEZE JM1_FINAL FOR THE ENTIRE 2026-27 SEASON";
- for the daily parameter refit only, the candidate cadence and human-approval promotion rule of
  `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md` §6-7. A daily version is promoted automatically, but
  only when every hard gate passes.

Policy §7-8 still governs every architecture change, every challenger and every rollback, and those need human approval.

## 1. Opening-day baseline

- `OPENING_DAY_BASELINE` = model version `cbb-jm1-2026-27-20261101`. It is a set of byte copies of the four frozen
  artifacts (hash-verified against `prod/config.py: ARTIFACTS`) in `models/2026-11-01/`:
  - marginal `bfcef1fb…`
  - JM1_FINAL structural `b932fa21…`
  - residual J2 `e4254867…`
  - production manifest `7d83e9cf…`

  The originals under `modeling/player_props_v2/` are never modified.
- Trained through the complete 2025-26 season (`supervised_training_through = historical_state_through = 2026`). No
  2026-27 outcome is in it.
- 2026-11-01 predictions use only this model.

## 2. Chronology

| item | rule |
|---|---|
| daily refit cutoff | **06:00 America/New_York** (`WC.DAILY_REFIT_TIME`, env `CBB_WF_REFIT_TIME`). It is converted with `zoneinfo`, so DST is handled: 11:00Z under EST (from 2026-11-01), 10:00Z under EDT (from 2027-03-14) |
| first eligible daily refit | the 06:00 ET cutoff of the morning after the first completed 2026-27 games. 2026-11-01 games enter the 2026-11-02 refit, once they pass eligibility (§3) |
| version id | `cbb-jm1-2026-27-YYYYMMDD` = the ET date of the cutoff |
| no new data | no version is created; ACTIVE carries forward. Before the first eligible game, ACTIVE = OPENING_DAY_BASELINE |
| morning sequence | SCORE PRIOR PREDICTIONS → UPDATE MONITORING → INGEST (state as-of cutoff, eligibility, training rows) → REFIT ×2 → HARD GATES → ATOMIC PROMOTION (or REFUSED) |
| predictions | scored at the validated checkpoint (TIP_MINUS_1H) with the ACTIVE version, whose training cutoff must be ≤ the prediction time (`daily.active_for`) |

## 3. Training eligibility (exact rule; `walkforward/eligibility.py`)

**Point-in-time source of truth.** This is the immutable production state versions (`prod/state.py`).
- The state used at cutoff T is the latest version with `created_at ≤ T` and `source_cutoff ≤ T`.
- A game's `outcome_available_at` is the `created_at` of the first version holding its result. It is a real arrival
  time, not the HR-1 rule. Data cannot be backdated.

A game tipped before T is INCLUDED only if all of the following hold. Otherwise it gets exactly one reason:

| check | failure |
|---|---|
| game universe D-I vs D-I (the Stage 3 example universe) | EXCLUDED `NOT_IN_TRAINING_UNIVERSE` |
| not cancelled / forfeited | EXCLUDED `NOT_PLAYED` |
| result recorded (the feed records results only at provider status `final`) | DEFERRED `NOT_FINAL_AT_CUTOFF` |
| provider schedule status, where present, is exactly `final` | DEFERRED `STATUS_NOT_FINAL` |
| HR-1 `knowable_at ≤ T` **and** `outcome_available_at ≤ T` | DEFERRED `NOT_KNOWABLE_AT_CUTOFF` |
| team box rows for both teams + player rows for both teams | DEFERRED `BOX_INCOMPLETE` |
| per team: Σ player points = team box points = game score (2025-26: 5,733 / 5,752 games) | DEFERRED `BOX_UNRECONCILED` |
| every player box row of both teams RESOLVED to a canonical player | DEFERRED `IDENTITY_UNRESOLVED` |
| a legal pregame example row exists at the game's cutoff | EXCLUDED `NO_PREGAME_EXAMPLES` / `NO_STATE_AT_CUTOFF` |

Behavior and reporting:
- A game is never inferred final from its scheduled end time.
- A DEFERRED game is re-evaluated every morning and enters the first version whose cutoff sees it pass. One late game
  never blocks the refit.
- Each version's `training_manifest.json` records EXPECTED / FINALIZED / INCLUDED / DEFERRED / EXCLUDED games and the
  count by reason.

**Population (architect ruling 2026-10-31).** The identity and reconciliation rules apply to NEW 2026-27 games only.
- In 2025-26, 139 D-I games had ≥ 1 unresolved player row and 19 did not reconcile. They stay in the frozen historical
  population, unchanged.
- A 2026-27 game failing these rules is DEFERRED until valid. Its box rows still feed player-state history for features,
  exactly as historically.

## 4. Training rows (`walkforward/training_rows.py`)

**Features** (pregame, one build per game, cached immutably):
- `prod.features.build` at the game's validated cutoff, over the state version as-of that cutoff, with the target
  games' outcomes stripped. This is the production scoring path itself.
- A correction that arrives later can never be backdated into a feature row.

**Labels and season-to-date finals** (per refit):
- One build over the state as-of the refit cutoff, using `blank_targets=False` (the only change to
  `prod/features.py`; the default is unchanged) and the frozen Stage 3 label SQL.

**OOS base forecasts** (the Phase 1B true-OOS store rule):
- Season F is forecast by the annual state trained through F-1. For 2026-27 that is the opening marginal, applied with
  `prod/inputs.marginal_rows`.

**Equivalence** (`tests/test_walkforward_training_rows.py`, two replayed real 2025-26 games):
- The frame, finals (full season), participation, team frame and OOS rows equal the frozen loaders' rows.
- Floats match at the replay tolerance `rtol = atol = 1e-12`; the observed difference is ≤ 1 ulp.

## 5. The refit (`walkforward/fit.py`): which components refit daily

`fit.py` contains no fitting mathematics. It supplies the rows (the frozen loaders refuse 2026-27 by design) and
re-composes the ~15 lines of `phase3.fit_structure / residual_rows / build_procedure` around the frozen functions.

**DAILY_REFIT_COMPONENTS** (all of them):

| component | frozen function |
|---|---|
| minutes ridge (μ), minutes SD bands, position prior rates, shrinkage k, rolling baseline | `marginal.fit_v0` |
| NB/Poisson count dispersion + STL policy | `marginal.fit_v0`, `marginal.stl_policy` |
| participation P(play) | `marginal.fit_participation` |
| shared game environment (pace / efficiency OLS, shock SDs, overtime law → E[D]) | `structural.env_fit`, `joint_minutes_v2.expected_duration` |
| conditional-mean GLM (JM_TERMS) | `structural.glm_rows` + `phase3.glm_fit_terms` |
| true-OOS structural residual basis | `structural.realized_conditional_mu` |
| conditional dispersion | `phase3.fit_s4` → `structural.dispersion_refit` |
| residual dependence J2 (latent Gaussian copula, MID) | `phase3.fit_s4` → `dependence.fit` |

**NON_DAILY_COMPONENTS:** none of the fitted parameters.
- The 2026-27 OOS base forecasts that feed the GLM and residual basis are produced by the opening marginal. That is the
  approved true-OOS definition (training season < forecast season), not a frozen parameter.
- The structures fitted through 2022-23 … 2025-26 for the historical residual seasons depend only on completed seasons.
  They are recomputed, not cached; the result is identical.

**Seasons:**
- T = 2027 once any 2026-27 training row exists, else 2026 (the opening chronology).
- Environment uses team-games 2013-14..T. The GLM uses OOS rows 2022-23..T.
- The residual basis is 2024..T, with season F under structure F-1 (the frozen SELECT / CONFIRM / FINAL rule).

**Season guard:** `structural.env_fit` itself refuses seasons > 2026 (`structural.MAX_SEASON`). No frozen file is
edited (`test_frozen_predictive_code_unchanged` still pins them). `fit.production_season_guard()` is the only place that
changes it. It:
- refuses unless the module holds the frozen 2026;
- captures the original, sets 2027 for ONE fit, and restores it in `finally` (also when the fit raises);
- verifies the restoration;
- is exclusive per process (a non-reentrant lock: a concurrent or nested entry raises instead of sharing modified module
  state).

`fit()` checks that 2027 is in force inside the fit and that the value is restored afterwards. Every morning run records
`season_guard` (`tests/test_walkforward_finalize.py`).

**Minimum-information rules:** none exists in the approved procedure, and none is added.
- Every structural estimate is a pooled fit over 12+ seasons, so early-season 2026-27 rows enter with their natural
  (small) weight.
- No component has a season-specific parameter that a handful of games could destabilize.

## 6. Recency weighting actually used (verified)

**Marginal training rows:**
- v1 weight `0.5 ** ((S − season) / 1.0)`, `V1REF`, unnormalized. S is the *last training season*, per the props_v0
  config: "age measured from the last training season".
- Once a 2026-27 game is in the training state, S = 2027: 2026-27 → 1.0, 2025-26 → 0.5, 2024-25 → 0.25, 2023-24 → 0.125, …
  2013-14 → 0.5¹³.
- Before that, S = 2026, which is exactly the opening fit.
- Changing S rescales every historical weight by exactly ½. That is exact in binary floating point, and every weighted
  quantity is scale-invariant.
- No day-level decay is added.

**Participation:** unweighted (v1).

**Player rate history features** (unchanged feature definitions):
- current season to date at weight 1; prior-season finals at 0.5 / 0.25 / 0.125.
- last-3 / 5 / 10 game windows and EWM (5-game half-life) minutes, scoring, usage and start rate.

**Structural components** (environment, GLM, conditional dispersion, J2): **unweighted pooled fits**. This is the
approved design; no recency weight exists in them, and none is added. **CURRENT_SEASON_RECEIVES_HIGHEST_CREDIBILITY =
TRUE for the marginal layer** (minutes, rates, participation, dispersion), which is where player-level credibility
lives. The architect should note that the structural layer is pooled by design.

**Minutes / role propagation (directive §10):**
- Players' last-5 minutes rank, EWM minutes, season-to-date minutes per appearance, participation history and team
  rotation depth are Stage 3 state features. They already update at every scoring run from the current-season state,
  even under the frozen opening model.
- The daily refit adds re-estimated coefficients on top: the minutes ridge, the SD bands, and the participation
  logistic, with the current season at weight 1.
- No override is added.

## 7. Versioning, storage, promotion, rollback

**Store:** `WC.STORE_ROOT` (default `data/prod/walkforward/cbb-originator/`, env `CBB_WF_ROOT`). It is runtime state,
never Git, and CBB-namespaced. Layout: `walkforward/store.py` docstring.

**Each version directory** is written complete in `candidates/.candidate-*`, fsynced, then published with one
`os.replace` and never edited again. `version_manifest.json` records, plus the `MANIFEST.sha256` sidecar:
- MODEL_VERSION, CREATED_AT, TRAINING_CUTOFF, DATA_CUTOFF
- TRAINING_THROUGH_DATE, LATEST_INCLUDED_GAME, N_INCLUDED_2026_27_GAMES / PLAYER_GAMES
- FEATURE_STATE_HASH, MARGINAL / STRUCTURAL / DISPERSION / DEPENDENCE hashes
- CODE_COMMIT, CONFIG_HASH, TRAINING_DATA_HASH, INPUT_IDENTITY_SHA, PREVIOUS_MODEL_VERSION
- recency weights, fit seasons and rows, the deterministic-refit record

The PRODUCTION_MANIFEST_HASH is the sidecar, which is also carried in the ACTIVE pointer.

**Pointers:** `active/ACTIVE_MODEL.json` and `rollback/ROLLBACK_MODEL.json`, each replaced atomically (tmp + fsync +
`os.replace`).
- Any failure leaves yesterday's ACTIVE in place. A refused candidate is moved to `refused/` for audit and is not
  loadable as a version.
- Rollback is a human-approved pointer change only (`daily rollback --reason … --approved-by …`), never a refit.

**Hard gates (`walkforward/gates.py`, all must pass):**

| gate | what it checks |
|---|---|
| INPUT_DATA_INTEGRITY | `prod.state.validate`, no duplicate keys, every trained game built |
| POINT_IN_TIME_LEGALITY | every pregame row built at its own cutoff from a state created before it; Stage 3 availability audit (no state after the row's cutoff, none from the target game); no training row at/after the refit cutoff; label state created before the cutoff |
| FUTURE / TARGET-GAME FEATURE / TARGET-GAME OUTCOME leakage | each flag must be FALSE |
| EXACT_IDENTITY | identity of the training rows |
| ARTIFACT_READABILITY | the candidate loads through the production loader path |
| FINITE_COEFFICIENTS, VALID_DISPERSION | finiteness; φ ≥ 0, Poisson φ = 0 |
| ARCHITECTURE_UNCHANGED | terms, families, α, kmax, exposure, seeds, marginal config / coefficient order / transforms / dispersion families / weighting / STL equal the opening artifacts |
| SIMS_20000_PRESERVED, SUPPORTED_MARKETS_UNCHANGED | simulation size and market set |
| DETERMINISTIC_REPEAT_FIT | two independent fits, bit-identical artifacts |
| scorer invariants (the frozen `jm1_final_production.integrity_checks`) | PMFs normalized / non-negative, PSD game correlation, exact pricing + push math, deterministic scoring, P(play) separation, one minutes draw per player feeding 7 stats, same-draw combos, equality with the confirmed `eval_game` path |
| GOLDEN_PRODUCTION_SMOKE + REPRESENTATIVE_SLATE_SCORED | the CI golden slate scored end-to-end, 20,000 draws |
| ROLLBACK_AVAILABLE | the previous active version verifies |

Any failure means `DAILY_MODEL_PROMOTION = REFUSED`.

**Idempotency:**
- The run id = hash(cutoff, training-data identity, code commit, config).
- A rerun settles nothing twice, writes no version, leaves ACTIVE unchanged, rewrites byte-identical reports and adds
  no run record.
- An existing version can never be replaced by different inputs (`VERSION_EXISTS_WITH_DIFFERENT_INPUTS`).
- Feature rows are cached per game, and no API call is made by the refit. Ingestion is the existing production sync,
  which fetches only new or unresolved games.

**Corrections (`walkforward/corrections.py`):** any MATERIAL change of an official box (player or team row added,
removed or changed) of a game already in the previous version's training state is recorded in
`manifests/corrections.jsonl`, with:
- CORRECTION_RECEIVED_AT: the first state version carrying exactly the corrected box;
- ORIGINAL_VALUE / CORRECTED_VALUE;
- FIRST_MODEL_VERSION_USING_CORRECTION.

The detection rules:
- Identity and diff share one normalized representation: NaN = null = None, 5 = 5.0, timestamps compared as UTC
  instants, arrays as lists, row order irrelevant.
- Provenance fields (`event_time`, `historical_knowable_at`, `source_*`, `ingestion_version`, `received_at`,
  `processed_at`, `player_game_key`) are never a correction.
- A correction enters only the next version. Old versions and old predictions are never touched.

**Run identity (`daily.run_identity`):** the run id hashes every material input:
- the cutoff and the state version used;
- every game's eligibility decision and reason;
- the training-data identity;
- the code commit and dirty flag, and the config hash;
- the previous model the candidate chains to (version + manifest hash).

A rerun is deduplicated only when ALL of these are identical. Any material change produces a new run, which is recorded
and not suppressed.

## 8. Determinism and the opening reproduction (measured)

**Daily refit:** two fits of the same inputs are **bit-identical**.
- The frozen team-frame read has no ORDER BY, so its float summation order could vary. The walk-forward path sorts the
  team frame by (season, game_id, team_id).
- DuckDB threads stay at the frozen default (2). One thread changes the row order of other frozen reads and moves
  near-zero parameters at 1e-17.

**OPENING_BASELINE_REPRODUCTION = PASS** (`tests/test_walkforward_fit.py`), with no 2026-27 data:
- marginal `v0_state` / `participation_state`, GLM, conditional dispersion, E[D] and residual J2 are **bit-identical**
  to the frozen artifacts;
- the shared environment matches within the frozen fit-to-fit tolerance `DET_RTOL = 1e-12` (measured max rel 8.5e-13,
  max abs 6.4e-14);
- the golden production slate scored by the refit equals `tests/fixtures/golden/prod_equivalence_v1.json` exactly:
  every bank sha256, input, prediction and board row at atol 1e-12.

The baseline hashes were not updated.

## 8b. Scorer model source (`prod/model_source.py`)

`prod.run daily --model-source {FROZEN_OPENING_BASELINE|ACTIVE_DAILY} [--model-store ROOT]`. The default is
`FROZEN_OPENING_BASELINE`: the frozen loader, unchanged, and what the live scheduler still uses.

`ACTIVE_DAILY`:
- loads only the atomic ACTIVE pointer's finalized version;
- verifies the manifest sidecar, every artifact hash and the pointer's manifest hash;
- refuses a model trained after the prediction time;
- stamps `model_source`, the exact version, the training cutoff and all artifact hashes on every PREDICTION record.

When ACTIVE_DAILY is missing or invalid, the scorer **fails closed** (`ModelSourceUnavailable`). There is no fallback to
the frozen model, because no approved policy defines one. Candidate and refused directories are never loadable.

## 8c. Near-tip quote snapshot (`walkforward/near_tip.py`)

At T−10m before the provider's commence time, each priced game's event odds are captured ONCE. This is a **QUOTE
SNAPSHOT ONLY**; nothing is rescored.
- It goes through the existing `odds_feed.collect` / `odds_acquire.Session`: production guards, budget, quota reserve,
  never at or after commence, and a cached payload under 10 min old is reused.
- Each row keeps: game, player, market, line, over / under price, sportsbook, quote and collector timestamps, and the
  preceding prediction (ids, version, training cutoff, P(over | no push) at the same line).
- Label: **`NEAR_TIP_CLOSE_PROXY_T10M`**, `exact_market_close = False`. It is not a verified closing line.
- CLV is reported as "CLV vs near-tip proxy".
- Command: `walkforward.daily snapshot`. It is not wired into any scheduler.

## 9. Prospective monitoring (`walkforward/monitor.py`; diagnostics only)

**Provenance:** every prediction stores the following, so it is always traceable to the exact daily version
(`predictions/provenance.parquet`, `pmfs.parquet`; the PREDICTION ledger records carry the same fields):
- game, player, stat, role diagnostic, prediction, probabilities, PMF
- model version, training cutoff, prediction (feature) cutoff, data cutoff, artifact hashes, checkpoint
- sportsbook, line, quote timestamps

**Settlement:**
- Done each morning before the refit, only for games that pass the same final/complete/reconciled/identity rule.
- A DNP is NO_ACTION, never a zero (existing monitor convention).
- Every score is attached to the version that made the prediction.

**Metrics:**
- binary O/U Brier and log loss, calibration gap, observed event rate, mean predicted probability
- full-PMF log score, CRPS

**Breakdowns:**
- dimensions: STAT, ROLE (TOP5_MINUTES / ROTATION_6_8 / BENCH_9PLUS / NO_ROLE_HISTORY), STAT×ROLE, LINE, LINE RANGE,
  OVER/UNDER, CHECKPOINT, MODEL_VERSION
- windows: YESTERDAY, ROLLING 7 / 14 / 30, SEASON_TO_DATE
- always N, games and game-clustered 95% intervals

**Market-relative:**
- Only on exactly matched lines: the same run, game, player, market, line, sportsbook and quote snapshot, where the book
  quotes both sides.
- No-vig method (unchanged from `prod/prospective_monitor.py`): p_over = (1/d_over) / (1/d_over + 1/d_under) per book.
  The line-level market is the book average.
- MODEL / MARKET / DELTA Brier and log loss (DELTA < 0: the model is better), by stat, role, stat×role, sportsbook,
  checkpoint, line range and model version.

**CLV:** against the near-tip proxy snapshot only (`monitor.clv_section`), never fabricated. It is reported separately
from proper scoring.

**ROI:** NOT_AUTHORIZED (wagering disabled).

**Drift (§26):**
- calibration slope, reported as an ALERT label only against the approved policy band [0.85, 1.15]
- PTS bias, minutes bias, same-team dependence (`prod.monitoring.same_team_dependence`), high-combo overs (PRA / PR / PA
  at lines ≥ pmf q75)
- all other thresholds: `docs/walkforward/PROPOSED_MONITORING_THRESHOLDS.json`, PROPOSED and NOT ACTIVE

A breach opens a challenger investigation. It never triggers an automatic change.

## 10. Dependency map (audit, directive §3)

| directive item | existing function reused (unchanged) |
|---|---|
| final marginal refit | `v2/marginal.fit_v0`, `stl_policy` (`v2/phase1b.build_artifact` layout) |
| participation refit | `v2/marginal.fit_participation` |
| minutes refit | `v2/marginal.fit_v0` (ridge + `props_v0._sigma_bands`) |
| structural fit | `v2/structural.env_fit`, `glm_rows`; `v2/phase3.glm_fit_terms` (composition of `phase3.fit_structure`) |
| conditional dispersion | `v2/phase3.fit_s4` → `structural.dispersion_refit` |
| residual dependence | `v2/phase3.fit_s4` → `v2/dependence.fit` (MID) |
| historical state building | `prod/features.compact_canonical`, `build_features` (frozen Stage 3 SQL) |
| Stage 3 feature / label updates | `stage3/universe`, `team_state`, `player_state`, `labels` via `prod/features.build`, `stage3/build.build_labels` |
| artifact generation | `v2/jm1_final_production.build_artifacts` (+ `props_v1_refit.dumps`, `phase2._dump` formats) |
| production loading | `v2/jm1_final_production.load_production` layout (`walkforward/store.load_dir`), `prod/run.daily(model=…)` |
| production integrity | `v2/jm1_final_production.integrity_checks` |
| manifest generation | `walkforward/store` + `daily.build_candidate` (fields of the frozen manifest + directive §12) |

Changes to existing non-frozen code (default behavior unchanged; golden and replay tests pass):
- `prod/features.py`: `blank_targets` switch.
- `prod/run.py`: optional `model=`, walk-forward identity fields on PREDICTION records, `market_pmfs.parquet`, bundle
  verified against the model's own hashes when it is a walk-forward version.
- `prod/loader.py`: `WALK_FORWARD_IDENTITY_FIELDS`.

## 11. Compute (measured on this Mac, single-threaded BLAS, `nice 15`)

Shared-Droplet requirement: `docs/walkforward/DROPLET_RESOURCE_REQUIREMENT_CBB.md`. One standalone morning: 118.6 s,
7.17 GB RSS, 11.2 GB peak footprint. **An 8 GB server is unsafe.**

These are measured from the end-to-end chronology test (`tests/test_walkforward_daily.py`, real frozen history,
fixture 2026-27 games).

| morning | new games | ingestion + pregame features | labels + assembly | fit ×2 | gates | total |
|---|---|---|---|---|---|---|
| 11-04 | 1 | 27.5 s (features 12.7 s) | 14.8 s | 32.4 + 28.5 s | 10.7 s | 99 s |
| 11-07 | 5 | 44.2 s (features 28.0 s, 2 cutoff groups) | 16.1 s | 28.6 + 27.3 s | 9.9 s | 110 s |
| 11-11 | 3 | 31.0 s | 15.7 s | 28.2 + 27.1 s | 9.6 s | 96 s |

- Each pregame build costs about 15 s per distinct (cutoff, state) group. A heavy Saturday (about 20-30 distinct tip
  times) adds about 5-7 min, about once per game, because the results are cached.
- Peak RSS is 8.2 GB for the whole test process, which also holds the cached frozen history frames (1.38 M marginal
  rows); a single standalone fit measured 5.5 GB. CPU is one core (BLAS single-threaded, DuckDB 2 threads).
- Disk is about 170 KB per daily version plus about 60 KB per game of cached pregame rows, so roughly 6,000 games
  means about 0.4 GB per season.

A full daily refit is operationally suitable for the shared Droplet (a few minutes per morning), provided the CBB job
gets at least 8 GB and runs alone.

**Incremental without changing the result:** cached per-game pregame rows (done). Not done yet:
- the residual-season structures through 2022-23..2025-26 depend only on completed seasons and could be cached by
  content hash, saving about 10 s per fit;
- the second determinism fit could run weekly instead of daily, but that is a gate-policy change for the architect.

## 12. Droplet requirements (NOT migrated)

- A CBB-only application directory, `.venv` and `CBB_WF_ROOT` on durable storage, with one systemd timer at
  06:00 America/New_York (`TZ` set; zoneinfo handles DST). The morning job holds `locks/morning.lock`.
- The thread environment (`OMP/OPENBLAS/MKL/VECLIB/NUMEXPR_NUM_THREADS=1`), at least 8 GB of RAM for the CBB job, and
  about 12 GB of local frozen stores (lockbox canonical, Stage 3 features / labels, OOS stores). The frozen stores are
  read-only.
- The Git checkout supplies CODE_COMMIT (or set `CBB_CODE_COMMIT`).
- The scorer supports `--model-source ACTIVE_DAILY` (§8b). Switching the live scheduler to it is a separate,
  architect-approved change. It has NOT been made.
