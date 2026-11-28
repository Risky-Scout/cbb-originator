# Stage 07: season-complete universe, bitemporal information integrity and fit lineage (2026-11-27)

**Starting lineage**
* Production 9f65e7f; rollback cae2a02.
* Stage 06 successor 2ac9117 (CI 46062370887); checkpoint d5a7dac.
* Identities authenticated at the start: Droplet `current` → 9f65e7f, `CBB_EXPECTED_COMMIT` = 9f65e7f, `ROLLBACK_RELEASE` = cae2a02. The staged 2ac9117 tree hash-verifies against the Stage 06 sha256sums.

**Companion documents**
* `ASSIGNMENT_REGISTER.md`: every open item mapped to a stage.
* `STAGE08_POPULATIONS.md`: the frozen evaluation contract and the A/B/C/D populations.
* `DEPLOY_PROCEDURE.md`: activation status and blocker.
* Governance: `docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md`, with the 20-stage program in §2 and the Stage 07 addendum in §3a.

## 1. Resource safety

**Remote work.** Three bounded, read-only transient units ran, one at a time, as cbb-originator:
* Units: `cbb-audit-s7-obsledger`, `-fbscan`, `-universe`.
* Limits: ReadOnlyPaths, MemoryMax 1.5 G, RuntimeMaxSec, every thread variable = 1, nice 10.
* The runner refuses to start if a unit of the same name exists.
* Each run waited until no `cbb-originator-*` job other than the quote due-check was active. The exception is the second universe attempt, which started while the 16:00 ET refresh was active and failed within seconds on a schema error. It is recorded here.
* Before each retry, the earlier unit was verified to be inactive or failed.

**Unchanged.** No production unit, timer, lock, state or forecast was touched, and no provider request was made.

**Local work.**
* Focused tests only: one process, threads = 1, nice 10.
* Git-only provenance tracing.

## 2. Bitemporal information integrity

**Times distinguished.** All live in `cbb_platform/prod/replay_contract.py`:
* Event: `scheduled_tip_utc` / `event_time`.
* Provider publication: RotoWire `source_datetime`. CBBD box lines carry none.
* Actual acquisition:
  * the payload `received_at`, which is on every box row and was verified ≤ the first version that holds it;
  * the state `source_cutoff`;
  * RotoWire `collected_at`.
* Canonical ingestion: the state `created_at`.
* Provider revision: the first version holding the revised content.
* Validity period: roster rows' `valid_from` / `valid_to`, and curated rows' `verified_on` / `review_by`.
* Forecast cutoff: tip − 1 h.
* Training cutoff: D 06:00 ET.
* State creation: `created_at`.

The earliest legitimate availability is the latest of the rule time, the publication time and the acquisition time.

**What was already correct (code trace, not re-derived):**
* The walk-forward fit, labels, eligibility and feature cache are bitemporal at state-version granularity. They select the immutable version with `created_at ≤ cutoff` and `source_cutoff ≤ cutoff`, then apply the rule mask.
* The feature cache key includes the state version, increment sha, history sha and semantics sha (`training_rows.causal_key`).
* RotoWire events are usable only when `max(collected_at, source_datetime) < T` and `collected_at < T`.
* Every prospective production path reads the state current at run time. A past `as_of` is flagged POST_CUTOFF_DATA, which blocks the run.

**Measured over all 310 retained state versions** (`results/obs_*.parquet`, `scripts/s7_observation_ledger.py`):

| observation | keys | first-observed version known | content revisions |
|---|---|---|---|
| player box lines | 32,757 | all; 5 games already in the first retained version (11-02 18:45Z) | 2. Identity re-key of one player (P-3 link Milijasevic), 2 lines, 11-11 |
| team box lines | 2,886 | all | 0 |
| game results | 1,443 | all | 8. Six score corrections of 1–3 points, one 0–0 placeholder that became 69–85 (G-f0d7, 85 min), and one non-score field |
| roster rows | 5,591 | all | 2,805 (snapshot relation; replaced every sync) |

* **0** box lines appeared in any state before their rule time.
* The acquisition lag after the rule time is unchanged from Stage 04: median 4.9 h, p99 191 h over the full season. Early sparse versions inflate it; since dense retention began on 11-18 the p99 is 8.2 h.
* **Revision exposure in production:** three revisions straddled a morning refit cutoff (11-14, 11-15, 11-26). The fit used the then-acquired content, which is correct bitemporal behaviour.
  * The 0–0 placeholder lasted 85 minutes and was not in any daily game-ratings state; the 11-22 ratings exclude it.
  * Every exact-version replay reproduces this by construction.

**Demonstrated replay exposures repaired** (old path ⇒ new path, `tests/test_stage7_bitemporal.py`):

| defect | correction |
|---|---|
| `features.compact_canonical` applied the rule mask to *any* increment. Replaying T over a later state admitted late boxes, provider revisions, later roster, portal and recruit snapshots, and identity re-keys | refuses an increment whose `source_cutoff > as_of` (LATER_INFORMATION_STATE). The provenance now records `information_state` |
| `rotowire_feed.participation_keys` used `event_time ≤ as_of`, looser than the rule: a box line was visible between tip and tip + 4 h | rule `historical_knowable_at ≤ as_of` |
| `model_source.resolve` ignored `promoted_at` | `resolve(..., replay=True)` raises FUTURE_PROMOTION when the pointer was promoted after T. Prospective scoring resolves at the current instant, where no later promotion can exist |
| CTX1 ledger: no per-row acquisition lineage; read whole whatever T | rows carry `added_at`, `state_version`, `training_cutoff`, `incumbent`. `load_ledger(replay=True)` keeps rows with `added_at ≤ T` and refuses legacy rows. A ledger updated after T is LEDGER_FROM_FUTURE |
| Curated statuses are applied from 00:00 ET of `verified_on`. In a replay with the current CSV, 21 of 24 rows are available **18–43 h** before their content was actually deployed (`results/curated_provenance.*`). 3 rows are not yet deployed | the replay contract uses the CSV as deployed in the release in force at T (`replay_contract.curated_csv_at`, `release_in_force`). Prospectively unaffected |
| Game ratings did not record the state they were fit on | `information_state` on every daily ratings state and receipt |

## 3. Silent fitted-model substitution (F1–F9)

**Historical F1 activation** (`results/fallback_scan.*`, every persisted snapshot, tick and run):
* **0 snapshots** (81) scored with the frozen model.
* **0 ticks** after the 2f4a384 tick-path fix (2026-11-24) did either.
* The 204 frozen-model ticks (11-08 → 11-24 04:10Z, 218,640 pregame PMF rows) are the known tick-path defect. They are correctly labelled `cbb-v2-jm1-final-shadow-1.0.0` and are not F1 fallbacks.
* Potential exposure remained in the code path; confirmed activation is none.

**What `prod/model_source.scoring_model` now does on fallback.** It produces an explicit **DEGRADED** substitution. The identity carries a `model_source_status` with:
* the actual model and artifact hashes;
* the actual training cutoff, 2026-04-07 04:50Z (the last knowable 2025-26 line in the frozen lockbox);
* the artifact age;
* the expected version and expected cutoff;
* the pointer, the exact `fallback_code` and reason;
* the policy PRICED_BLOCKED_NON_ACTIONABLE;
* the recovery requirement.

Consequences of a DEGRADED substitution:
* The frozen model is labelled `model_kind` FROZEN_OPENING_BASELINE, never a daily refit.
* `run.daily` adds the blocking run reason **MODEL_SOURCE_FALLBACK**: priced, but BLOCKED.
* The event is appended to `<store>/manifests/model_source_degraded.jsonl`.
* Health carries `model_source` and the contract `PLAYER_MODEL_SOURCE`, with ACTIVE_BLOCKER MODEL_SOURCE_FALLBACK. `ACTIVE_MODEL` becomes false.
* Snapshots and the index carry the status, and `tick.scoring.model_identity` records the model that actually scored. The tick's top-level `artifact_sha256` is now explicitly scoped as the frozen hash gate.
* Explicit frozen runs are labelled EXPLICIT_FROZEN.
* No last-known-good daily fallback is approved, so none was added.

| id | exact activation (Stage 06 inventory) | disposition |
|---|---|---|
| F1 | ACTIVE unavailable / unverifiable / FUTURE_LEAKAGE / FUTURE_PROMOTION → frozen | **eliminated as silent**: DEGRADED + blocked + health (above) |
| F2 | CTX ledger missing / stale / incumbent mismatch; rows without history → factor 1 | INCUMBENT_MISMATCH and LEDGER_FROM_FUTURE statuses, `ledger_meta` vs `expected_incumbent`, `rows_factor_exactly_1` per stat |
| F3 | PI2 transfer strength diff → 0, min/app → 20, cur_min → 0 | `neutral_defaults` counters and `clip_bound_rows` per stat in the PI2 receipt. The values are a Stage 10 decision |
| F4 | RA2 / DV absent or NaN column → 0 | `defaulted_to_0` per column (count, or ABSENT) in the RA2 and DV1 receipts. Numerics unchanged; the imputation decision is Stage 10 / 11 |
| F5 | team environment missing → zeros / league prior | `environment_defaulted` (game\|team) in run_summary |
| F6 | design NaN → training mean without an indicator | `design_imputation`: per frozen feature without an indicator, the scored-row NaN count (marginal, participation), in run_summary. Computed in the run layer; the hash-sealed frozen model files are unchanged |
| F7 | dependence group R → global R (role NO_ROLE_HISTORY has no group R) | `dependence_global_r_fallbacks` per game bank: groups answered by the global R. Computed in the run layer |
| F8 | game: neutral NULL → home, unseen team, no schedule table → pending 0, INITIAL baseline request → NEWS_FREE | `pending_finals_source`, `availability_baseline_requested`, `information_state` in game_forecast.json |
| F9 | behaviour-changing env flags unrecorded | `runtime_flags` (raw + effective mean basis / joint version) in every run identity; not part of engine_id |

## 4. Historical fit lineage

Source: `results/historical_lineage.{csv,json}`. It covers 920 persisted artifacts: 366 runs, 81 snapshots and 473 ticks.

| field | recorded | derived | unverifiable |
|---|---|---|---|
| code commit | 591 | 257 (release in force, from the tick `source_commit` before and after) | 50 ambiguous (deploy between ticks) + 22 ticks |
| fit identity (`model_version`) | all with PMFs | — | 192 ticks with no PMF (no slate) |
| fit kind / training cutoff | — | all (version day 06:00 ET; frozen 2026-04-07 04:50Z) | — |
| player engine_id | 140 | the rest, by time (`prod.engine.legacy_engine`, the Stage 06 attribution correction) | — |
| state version | snapshots and runs | — | — |
| game joint version / mean basis | — | all = 1.0.0 / FINAL (every historical game PMF predates C4) | — |
| runtime flags | — | — | all historical (recorded from Stage 07 on) |
| fallback status | — | frozen tick-path rows labelled; no F1 | — |

Corrections are explicit attribution rows. No immutable receipt was rewritten.

## 5. Season universe

Contract: evaluation cutoff 2026-11-27T20:40:23Z, state `S20261127T201352Z-8eeab0c20aef`. Counts: `results/universe_summary.json`.

**Games**

| category | count |
|---|---|
| tip ≤ cutoff | **1,485** (DI_VS_DI 1,164, DI_VS_NON_DI 321) |
| COMPLETE (result + player + team box) | **1,443** (DI 1,134) |
| missing result / in progress / postponed-cancelled | 25 / 13 / 4 |
| exact retained pregame information state | 1,430 complete (DI 1,127). The other 13 (DI 7) tipped before 2026-11-02 18:45Z |
| prospective forecast (≥1 pregame PMF) | 833 games; **810 of 1,134** complete DI |
| missing forecast, complete DI | 324 = 300 before Droplet production forecasts (11-01 … 11-08) + 24 NEVER_SLATED_PREGAME |
| invalid PMFs | 0 of 1,014,192 pregame rows |
| player market observations | 514 games (3,036 rows, 598 player-games); game markets 349 games |

**Players** (complete games):
* 32,757 box lines: 32,531 played, 226 DNP lines.
* Identity: 28,642 RESOLVED, 4,112 UNRESOLVED_NO_MATCH, 3 ambiguous.
* Roster membership at pregame for players who played:
  * 28,031 on the roster by pregame;
  * 316 with the roster row first observed after pregame (later membership is never admitted earlier);
  * 4,184 with no roster row at that team. These are mostly non-DI or unresolved identities.

The 810 forecast games break down as follows (pool = the latest pregame run's `player_universe`):

| category | player-games |
|---|---|
| played and forecast | 16,783 |
| played, in the pool, not forecast | 494 (NO_FROZEN_MEMBERSHIP_EVIDENCE 402, NOT_ON_CURRENT_ROSTER 70, AMBIGUOUS 7, TRANSFERRED_OUT 7, identity conflicts 7, 1 none) |
| played, not in the pregame pool (unexpected) | 44 (32 unresolved box identities, 12 resolved: 10 without a roster row, 2 roster row first seen after pregame) |
| forecast, did not play (no box line or 0 min) | 6,214 |
| in the pool, no box line, not forecast | 15,782 |
| DNP box line, not forecast | 13 |

Every forecast player-game carries all 12 supported stats; 989,604 valid PMFs. The universe is not restricted to listed players. Market overlap is a sub-population.

## 6. Parameter-estimation readiness

Stages 09–17 fit from the walk-forward point-in-time frames. These are already bitemporal (§2) and are now protected against later-state replays and unlinked ledgers.

The fit lineage is recorded from Stage 07 on: state version, training cutoff, runtime flags and model-source status.

Selection-exposed dates are identified in `STAGE08_POPULATIONS.md` D. A new parameter must not be fitted on the Stage 08 final evaluation population.

## 7. Governance

* The 20-stage program is recorded in governance §2, and the Stage 07 addendum in §3a.
* The full texts of the Parameter Estimation Directive, the Mathematical Enhancement Addendum and the Engineering Campaign Override were **not included** in the Stage 07 message, and none is present on disk. They cannot be preserved verbatim, and the 55 groups cannot be reconciled against them. Status: BLOCKED, owner to supply.

## 8. Acceptance gates (§14)

| gate | status | evidence |
|---|---|---|
| COMPLETE_STAGE_MAPPING_PRESERVED | **PASS** | governance §2 (exact 20-stage table); `ASSIGNMENT_REGISTER.md` (42 open items, each with a stage; none unassigned) |
| PARAMETER_DIRECTIVE_PRESERVED | **BLOCKED** | the three directive texts were not in the message or on disk (§7). Nothing was paraphrased into "verbatim". Owner must supply them |
| SEASON_GAME_UNIVERSE_RECONCILED | **PASS** | §5; 1,485 games, each with one outcome class and exclusion reason |
| SEASON_PLAYER_UNIVERSE_RECONCILED | **PASS** | §5; 32,757 box lines, every forecast-game player classified; 4,112 unresolved identities counted, not dropped |
| HISTORICAL_INFORMATION_AVAILABILITY_VERIFIED | **PASS** (2026-27, retained window) | §2; first observation over 310 immutable versions; 0 lines before rule time. Before 11-02 18:45Z and all lockbox seasons: rule-only, classified D |
| PROVIDER_REVISION_PROTECTION_VERIFIED | **PASS** | 8 result + 2 player-line revisions located by version; exact-version replay plus LATER_INFORMATION_STATE refusal |
| AVAILABILITY_EVENT_CHRONOLOGY_VERIFIED | **PASS** | RotoWire `collected_at` rule (unchanged); participation by rule time (fixed); curated rows by deploy time (`replay_contract`, 21/24 backdating quantified) |
| HISTORICAL_ROSTER_INTEGRITY_VERIFIED | **PASS** | roster comes only from the state as-of T; 316 roster rows first seen after pregame are not admitted earlier |
| REPLAY_LEAKAGE_DEFECTS_REPAIRED | **PASS** | §2 table; `tests/test_stage7_bitemporal.py` (old path fails, corrected path passes) |
| CAUSAL_FEATURE_CACHE_PROTECTED | **PASS** | `causal_key` includes state version and hashes (verified, unchanged); compact build now records and checks the information state |
| SILENT_MODEL_FALLBACKS_ELIMINATED | **PASS** (engineering; active in production once the scheduled deploy runs) | §3, F1–F9 |
| HISTORICAL_FIT_LINEAGE_VERIFIED | **PASS** (with classified gaps) | §4; 848 of 920 code commits recorded or derived; 50 AMBIGUOUS and 22 UNVERIFIED kept as such |
| ORIGINAL_PROSPECTIVE_FORECASTS_PRESERVED | **PASS** | all audit units ran read-only (ReadOnlyPaths); corrections are separate attribution rows |
| CORRECTED_REPLAY_POPULATION_DEFINED | **PASS** | `STAGE08_POPULATIONS.md` B; 1,430 complete games (1,127 DI) eligible |
| MARKET_OVERLAP_POPULATION_DEFINED | **PASS** | `STAGE08_POPULATIONS.md` C |
| MISSING_INFORMATION_LIMITATIONS_QUANTIFIED | **PASS** | §5, `STAGE08_POPULATIONS.md` D and missing/excluded table |
| STAGE_08_BENCHMARK_READY | **PASS** (engineering) | populations, frozen cutoff, replay contract. Stage 08 not started |
| RESOURCE_SAFETY_VERIFIED | **PASS** | §1 |
| SUCCESSOR_LINEAGE_PRESERVED | **PASS** | 7d160b1 descends from 2ac9117 → … → 9f65e7f (all Stage 02–06 commits are ancestors); CI 46078107204 success (1,186 passed, 36 skipped; 33-test step passed); staged tree identical to `git archive` (960 files); ledger key `stage7_universe_bitemporal_lineage_20261127` |

## 9. Activation

**At handoff (2026-11-28 01:15Z): production 9f65e7f, rollback cae2a02.** Successor `7d160b1c7c5a02db834793954924c4ff240a0690` is staged inert and hash-verified (960 files).

The owner-authorized activation is **scheduled**, not yet executed. It is one guarded script with two bounded windows: 2026-11-28 04:50–05:40Z, with a fallback at 16:50–17:25Z. Details are in `DEPLOY_PROCEDURE.md`.
* It deploys only if every established safeguard passes, including free disk ≥ 38.5 GiB.
* It aborts if production or the staged set changed, so it cannot activate a superseded candidate or race later work.
* On preflight failure it rolls back to 9f65e7f automatically.

Free disk at handoff is 37.80e9 bytes (35.2 GiB), below the gate. Whether a window opens depends on the retention passes; the 16:45Z pass has passed the gate every day. Disk growth: see `../2026-11-27-storage/STORAGE_ROOT_CAUSE.md` and the mandatory Stage 19 requirements S19-STOR-1…7 in `ASSIGNMENT_REGISTER.md`. Details and the owner options are in `DEPLOY_PROCEDURE.md`.

## 10. Validation

* **Focused local:**
  * The first run of 69 files gave 15 failures and 12 errors. All were Stage 07 regressions:
    * hash-sealed frozen files had been touched; the F6/F7 counters were moved to the run layer;
    * `subprocess` was imported in the production layer;
    * FUTURE_PROMOTION fired in prospective scoring, so it is now replay-only;
    * the new run reason had no board mapping;
    * two tests had outdated expectations.
  * Rerun of the 15 affected files plus the new and changed tests: **200 passed, 2 skipped**.
* **GitHub CI `cbb-production-ci.yml`:** run 46078107204 on 7d160b1, **success**: 1,186 passed, 36 skipped (Stage 06: 1,172 / 36); a 33-test step passed.
