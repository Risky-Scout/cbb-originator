# Stage 03 — Availability synchronization and writer-lock contention (2026-11-27)

Scope: the availability refresh (`cbb_platform/availability_refresh.py`, Phase A / Phase B), the 10-minute quote wake
(`deploy/droplet/bin/cbb-originator-run` tick → `prod/lifecycle.quotes`), and the global writer lock between them.
Production is unchanged: `current` = 9f65e7f, rollback = cae2a02, and the Stage 02 candidate 56f3dae is staged but not deployed.
All Droplet work was read-only: journal reads plus one transient read-only unit (`scripts/s3_run.sh`: uid cbb-originator,
`ReadOnlyPaths=/var/lib/sports-models`, MemoryMax 3200M, nice 10, IOWeight 50, one thread). There were no new RotoWire,
Odds API or other provider requests, no host-wide cache or OS operations, and no unit started, stopped or delayed.

## 1. Existing repairs verified

| Repair (in production) | Evidence |
|---|---|
| Phase A acquisition runs outside the writer lock (10d1538) | Journal 11-20..26: every refresh's Phase A ends before `PHASE_B_LOCK_WAIT_START`. 11-26 23:00Z: Phase A took 12 s. Tests: `test_phase_a_never_touches_the_global_writer_lock`, `test_slow_phase_a_never_blocks_a_quote_tick` (≥ 60 s Phase A, real flock). |
| Bounded Phase B wait (300 s) with contiguous retries | 11-24 10/13/16Z `DEFERRED_WRITER_LOCK` led to the retry-anchor fix. Since then, 0 deferrals in 13 refreshes. Tests: `test_busy_writer_defers_and_retries_then_publishes`, `test_retries_are_anchored_at_the_first_wait_start_so_attempts_are_contiguous`, `test_three_retries_max_then_deferred_writer_lock_exit_75`. |
| Priority marker: a new tick yields to a waiting refresh | No tick→tick handoff starvation since 11-24. Tests: `test_tick_yields_to_a_live_waiting_availability_refresh_and_ignores_a_stale_marker`, `test_priority_marker_exists_only_while_phase_b_waits`, `test_priority_marker_is_cleared_after_a_deferred_exit`. |
| Close-window game-line capture without the writer lock | 11-26 23:10Z: the tick found the writer busy, captured under the game-quotes lock, then waited. Tests: `test_tick_with_writer_lock_busy_captures_close_evidence_before_waiting`, `tests/test_close_capture_lock.py` (7). |
| Phase B watchdog and atomic publication | Tests: `test_watchdog_dumps_stacks_aborts_phase_b_and_preserves_prior_outputs`, `test_death_during_phase_b_before_the_index_append`, `test_orchestrator_death_alone_keeps_the_lock_with_the_phase_b_child`. |

## 2. New defects discovered

**D3.1: a quote wake already holding the writer lock rebuilds exports that the waiting refresh rebuilds again.**
The refresh units (10:00 / 13:00 / 16:00 / 18:00 ET) fire in the same second as a quote wake (:00). The wake takes the
lock first. The priority marker only makes *new* ticks yield; the wake that already holds the lock runs to the end,
including an unconditional `exports_for` rebuild of the same day's workbooks. Phase B then repeats that rebuild minutes later.

* Phase B lock wait across all 25 published refreshes 11-20..26 (`refresh_lock_journal_20261120_27.txt`): median 146 s,
  range 110–270 s. Only one wait (11-25 18:00) was under 100 s (3.0 s).
* 11-26 23:00Z: the wake had `due=false reason=IDLE`, held the lock 23:00:00–23:03:38, and Phase B waited 206 s.
* Read-only profile of an idle wake on production data (`wake_profile_idle_20261127T0344Z.json`): total 142.8 s, of which
  `exports.daily` was 137.1 s (96%). Every workbook came back `rewritten: false` (content unchanged). QR.run took 1.7 s and the
  game-quotes plan 0.8 s.

**D3.2 (recorded, not corrected): idle quote wakes recompute unchanged exports every 10 minutes.**
Over 24 h (`quote_wake_journal_24h.txt`) there were 141 wakes holding the writer lock for 11.39 h in total (47% of the day). The 96 idle
wakes had a median of 181.5 s. The prior-day (D-1) grades and performance (~60 s of each rebuild) are recomputed on every wake.
This is occupancy, not a correctness defect. Every timed official tick met its deadline (§6), and the waits it causes outside
the refresh windows are absorbed by the existing tick yield and close-capture paths. A content-fingerprint skip
would have to model `now`-dependent freezes and cutoffs. That is a riskier change, deliberately left out of this stage.

## 3. New defects corrected (D3.1)

`prod/lifecycle.quotes`: after QR.run (quote requests and repricing) and `game_quotes_capture`, if
`writer_priority_waiter(life)` finds a fresh (≤ 50 min, the same bound as the wrapper) priority marker whose pid is alive,
the wake skips only `exports_for`. It records `exports_deferred` (`DEFERRED_TO_AVAILABILITY_REFRESH`, refresh_id, window)
in its output and in the health file, and adds `quote_refresh_totals.exports_deferred`.

* Unchanged:
  * Every quote request, repricing, game-line capture and health merge.
  * The official tick before it (the wrapper's `due_check` path is untouched).
  * Lock acquisition and release (still the wrapper's fd 9).
  * The single writer.
  * Dry runs.
* Fail-safe: a missing, stale, malformed or dead-pid marker means the wake rebuilds exactly as before. The wake only reads
  the marker and never writes or removes it.
* Exports are never left stale. The deferral happens only while a refresh waits; its Phase B rebuilds the same day's exports under
  the same lock right after. If Phase B fails, the refresh clears its marker, and the next wake (≤ 10 min) rebuilds.
  Frozen evaluations are cutoff-indexed (`market_exports.freeze` / `game_exports.freeze` select offers as of the cutoff);
  a deferral can only shift `frozen_at`, which Phase B's own export then sets.
* Old/new path proof (`scripts/s3_oldpath.py`, live marker): with the pre-fix `lifecycle.py` the wake rebuilt exports once
  (`OLD_PATH_DEFECT`); with the fix it rebuilt them 0 times (`NEW_PATH_OK`).
* Tests: `tests/test_quote_wake_refresh_yield.py` (8). They cover the marker-path contract with the wrapper and refresh, the deferral with a
  live waiter, rebuilds for no marker / dead pid / stale / garbage markers, preservation of the last real exports
  health section, dry runs, the `CBB_LOCK_DIR` override, and controlled contention (below).

## 4. Controlled contention validation

`test_controlled_contention_refresh_waits_only_for_the_quote_work[True|False]` uses real processes and real `flock(2)` locks.
A wake process takes the writer lock and runs `lifecycle.quotes` with 1.5 s of quote work and a 6 s export rebuild. The real
`availability_refresh` orchestrator (the split tests' fake Phase A/B children) runs its Phase A and waits for Phase B.

* Fix on: the wake defers its rebuild and Phase B waits < 6 s.
* Fix off (control): the wake rebuilds and Phase B waits ≥ 5.5 s.
* Both: one writer at a time (`writer_count_max == 1`), Phase B enters only after the wake exits, and the marker is cleared.

Projected production effect (not measured; needs a natural refresh on the successor): when the colliding wake is idle
(e.g. 11-26 23:00Z), the Phase B wait falls from ~206 s to about the wake's pre-export work (~3–12 s). When the wake also
runs a due official tick, that tick still runs first. That is legitimate, because an official prediction must not wait behind a refresh.

## 5. Remaining lock-ownership requirements (legitimate, kept)

| Holder | Why it needs the global writer lock |
|---|---|
| Official tick (`shadow_tick`) | Reads ACTIVE model / state and appends the immutable forecast and tick records. Single official writer, atomic promotion. |
| Phase B (forecast ~349 s + exports ~163 s on 11-26) | Re-reads state / ACTIVE / snapshots under the lock, writes PMFs → forecast_players → snapshot.json → index last, then the exports and health. Computing the forecast outside the lock would need re-validation of every input under the lock. That is not justified by a 513 s hold that never caused a missed deadline. |
| Quote-wake export rebuild (when no refresh waits) | Atomically replaces today's workbooks and writes freezes once at cutoff. Must not interleave with another writer. |
| Morning chain | Walk-forward refit, promotion and forecast (heavy lock + writer lock as before). |

Phase A, the 08:30 light poll, the close-window capture (game-quotes lock), retention and the feature warm-up do not take the writer lock.

## 6. Deadline safety (existing evidence)

From `ops/ticks/*/projections.parquet` (11-01 → 11-27): 920 scored games across 290 ticks. 0 were executed after the target cutoff and 0
after tip. Margin to cutoff: min 11.7 min, median 46.5, mean 45.7. Since 11-20: n = 340, min 20.6 min, p05 40.3.
Cutoff rules: TIP_MINUS_1H 892, ET_GAME_DATE_START 28. Early games: the close-capture path and the tick yield keep
working while a refresh holds the lock. The fix only shortens the refresh's lock wait and never delays a tick.

## 7. Reliability scenarios → evidence

| Scenario | Evidence |
|---|---|
| Concurrent walk-forward fitting | `test_32_only_the_walk_forward_stage_takes_the_shared_heavy_lock`, `test_shared_lock_deferral_is_retried_once_then_settles_on_the_incumbent`, `test_31b_a_stage_alive_without_the_lock_defers_the_recovery`, `test_features_warmup_is_heavy_locked_writer_lock_free_and_bounded` |
| Delayed acquisition | `test_slow_phase_a_never_blocks_a_quote_tick`, `test_phase_a_budget_cannot_be_swallowed_and_records_acquisition_timeout`, `test_hung_phase_a_is_hard_killed_and_recorded`, `test_acquisition_timeout_never_enters_phase_b` |
| Repeated / out-of-order / stale snapshots | `test_duplicate_response_is_idempotent`, `test_18_duplicate_content_sha_is_stored_once_metadata_kept`, `test_late_collected_update_cannot_backdate`, `test_last_edited_creates_a_new_immutable_version`, `test_light_poll_health_section_is_never_replaced_by_an_older_receipt`, `test_stale_receipt_is_replaced_by_a_fresh_phase_a_outside_the_lock`, `test_phase_b_rejects_unpublishable_stale_or_foreign_receipts`, `test_17_stale_feed_fails_the_game_closed` |
| Interrupted writes | `test_death_during_phase_a_…`, `test_death_while_waiting_for_the_phase_b_lock`, `test_death_during_phase_b_before_the_index_append`, `test_watchdog_…_preserves_prior_outputs`, `test_capture_files_are_written_atomically`, Stage 02 `test_interrupted_writes_are_ignored_and_rebuilt` |
| Deferred health merges | `test_light_poll_acquires_while_another_writer_holds_the_writer_lock_and_health_folds_in_later`, `test_a_deferred_wake_keeps_the_last_real_exports_section` (new) |
| Quote-capture contention | `tests/test_close_capture_lock.py`, `test_tick_with_writer_lock_busy_captures_close_evidence_before_waiting`, `test_controlled_contention_…` (new) |
| Early-game deadlines | §6. `test_due_rate_limits_discovered_game_and_nothing_after_cutoff`, `test_quote_received_at_or_after_the_official_cutoff_is_never_priced`, `test_game_quotes_near_tip_cadence_engages_after_the_cutoff` |
| Provider failure and recovery | `test_provider_outage_fails_closed_through_freshness`, `test_13_login_redirect_fails_closed`, `test_15_empty_responses_never_mean_everyone_healthy`, `test_failed_polls_and_acceptance_runs_do_not_reset_continuity`, `test_failed_attempt_does_not_consume_the_discovery_window`, `test_phase_a_failure_and_not_enabled_statuses` |

## 8. Commercial authorization status

Unchanged and separate from engineering correctness. The RotoWire production acquisition path remains configured as before.
An authenticated subscription or cookie is **not** commercial authorization for automated acquisition or publication, and
no such authorization is on record. Stage 03 made no new RotoWire or other provider requests. All acquisition behavior was verified with
existing receipts and isolated fixtures. The authorization decision belongs to the owner.

## 9. Candidate integration

The successor candidate is committed on top of the Stage 02 lineage (56f3dae → 945353b → 25b83ed8c945573bd1b707a68d1b6518947daa42). It is one candidate, not a competing one.
Diff vs 56f3dae: `cbb_platform/prod/lifecycle.py` (quotes deferral + marker reader), `tests/test_quote_wake_refresh_yield.py`,
and docs. The Stage 3 SQL and features are untouched, so `CLOSURE_AUDITED_STAGE3_SHA256` still matches. There is no unit, env, wrapper, schema,
model or parameter change. The Stage 02 regression contract was re-run in full (see `regression.md`). Not deployed.

## 10. Pending natural production verification

1. 11-27 11:10Z morning, 13:30Z poll and 14:10Z scoring receipts for the deployed 46e07f3 (precondition for any deploy).
2. After deploying the successor: the first morning's warm-up receipt (Stage 02 projection) and the first refresh whose
   window collides with an idle wake. Expect `exports_deferred` in health and `PHASE_B_LOCK_WAIT_SEC` ≪ 146 s.
