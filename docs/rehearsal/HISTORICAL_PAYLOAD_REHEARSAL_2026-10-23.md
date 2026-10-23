# CBB pre-season rehearsal: real historical payload through production components (2026-10-23)

Architect decision: **Approach 2 (REAL PAYLOAD + COMPONENTS)**. No season-shifted harness was built. Every 2026-27
production guard is unchanged: the CBBD season guard, the artifact production-season check, `odds_acquire`'s refusal
of the historical endpoint, and the 2027 filters in `slate.build` / `run.daily` / `shadow_tick`.

Tool: `cbb_platform/rehearsal/historical_payload.py`. It is an isolated one-off utility. Nothing in `cbb_platform/prod`
imports it, and it is not on the scheduler's path. It composes the production functions in `run.daily` /
`shadow_tick` order on an isolated work root. Evidence is in `docs/rehearsal/historical_payload_2026-10-23/`.

**This validates PIPELINE MECHANICS only.** `MODEL_PARAMETER_HOLDOUT = FALSE`: JM1_FINAL was fit through 2025-26, so
this game is in-sample for the parameters. No number below is prospective performance evidence, and nothing here
implies anything about the market.

## Event (selected on data availability only, before any odds or model output)

Pre-registered rule (`selection.json`, SQL sha256 `a7e8ab82…`):
- the earliest 2025-26 regular-season, D-I vs D-I, non-neutral game with an EXACT tip on or after 2026-02-10 UTC
  (the validated replay window);
- both teams high-major (ACC / Big Ten / Big 12 / SEC / Big East), as a proxy for prop coverage;
- both team and player box scores, a substitution feed, and every box-score player canonically identified;
- the second row of the same order is the only allowed fallback.

- **Event:** NC State @ Louisville, 2026-02-09 19:00 ET (`G-8feee9ee50320ff3`; Odds API event
  `5e1aa928201fd5c2d87346ed74e4d0c4`).
- **Fallback:** not used, because the first event returned player props. The fallback guard refuses a second event
  in that case.

## Historical Odds API (one-off, authorized)

| request | date param | credits | quota after | response sha256 |
|---|---|---|---|---|
| historical events (27 listed) | 2026-02-09T22:42:00Z | 1 | 4,932,577 | `9ac69f58…` |
| historical event odds, 12 documented base keys, region us | 2026-02-09T22:42:00Z | 40 | 4,932,537 | `2865bbaf…` |

- **Total:** 41 credits.
- **Snapshot:** 2026-02-09T22:40:38Z (previous 22:35:37Z, next 22:45:37Z).
- **Books:** betonlineag, betmgm, williamhill_us (Caesars), fanduel.
- **Markets returned:** player_points, player_rebounds, player_assists, player_threes. The other 8 documented keys
  came back `MARKET_NOT_OFFERED_FOR_EVENT`, and no unsupported key was returned.
- **Re-buy guard:** a repeat fetch is refused ("already acquired: never re-bought"), and so is the fallback.

**Timestamp convention:**
- A historical snapshot has no collector receipt time, so `collector_received_at` is set to the provider's snapshot
  `timestamp`. The wall-clock fetch time is recorded separately as `fetched_at_wallclock`.
- The prediction as-of is cutoff − 18 min = 22:42Z, the second attempt of the `:12/:42` cadence.

## Results

| check | result | evidence |
|---|---|---|
| Frozen model hash gate | PASS | the 4 sha256 values equal the frozen e1353e5 values; no refit |
| Point-in-time feature/state leakage | **FALSE** | the as-of canonical has no target result, player box, team box or substitutions; no outcome knowable after the as-of; feature cutoff = TIP_MINUS_1H; candidate season = 2026 |
| Model-parameter holdout | **FALSE** | fit through 2025-26 (pipeline rehearsal only) |
| One joint bank | PASS | 1 bank, 20,000 draws, `same_draw_check` true; 44 players; all 12 markets derived from it; one bank hash across every distribution and projection row |
| Real payload normalization | PASS | 170 prop quotes, 170 VALID, 0 invalid; production `odds_feed.validation_checks` = PASS on all 7 checks |
| Strict identity matching | PASS | event resolves to exactly one game; 170/170 quotes matched to canonical players; 0 unmatched; 0 ambiguous; no fuzzy matching |
| Timestamps | PASS | market `last_update` 22:39:28–22:40:21Z ≤ snapshot ≤ as-of; every quote fresh (≤ 20 min) |
| Deep links | NOT_RETURNED | the historical payload carries no `link` fields; the normalizer left them empty (nothing fabricated). Link extraction is covered by the fixture tests |
| Pricing engine | PASS | 90/90 priced rows re-derived from their own pmf: P(over), P(under), P(push), fair decimal, edge |
| Best executable board | PASS | 90 rows, each backed by a real quote; best price = maximum decimal among valid quotes (0 wrong, 0 fabricated); all SHADOW_ONLY_NO_WAGERING; not published |
| Pregame ledger | PASS | 584-row hash chain (583 PREDICTION records); the hashes of ledger / projections / predictions_quoted / quote_sides / side_monitor are byte-identical after settlement |
| Outcome chronology | PASS | outcome knowable 2026-02-10T04:00Z > prediction as-of 2026-02-09T22:42Z; settled 04:30Z |
| Settlement math | PASS | `shadow_tick.step_settle` on the real final box; 90 quoted predictions (28 OVER, 62 UNDER), 0 mismatches against an independent re-derivation; 170 side settlements |
| DNP rule | PASS (player level) | 20 of 44 pool players DNP: participation DNP, no numeric actual, never zero. No quoted player was DNP in this game; quoted-row DNP → NO_ACTION is covered by `test_settlement_appended_once_dnp_no_action` |
| PUSH | NOT_OBSERVED | every real line was a half point (P(push)=0); integer-line push is covered by `test_integer_lines_preserve_push…` and `test_push_handling_and_pricing` |
| Side monitor | PASS | 85 opportunities = 170 quote sides / 2; 170 side settlements |
| High-exposure PTS / center / evidence buckets | PASS | each report N equals an independent count (3 / 2 + 22 / 4 + 4 + 16) |
| Early-season / transfer / cold-start | NOT_APPLICABLE | this mid-February game has no row in those strata (report N = independent N = 0) |
| Market benchmark | PASS | 45 two-sided lines; the report equals an independent recomputation exactly (model Brier 0.226014, market 0.241239; log loss 0.643537 / 0.675131). **N = 45 lines from 1 game: not evidence of anything** |
| Daily + cumulative reports | PASS | written on the isolated ops root; a second write is byte-identical; hash chain valid (2 ticks) |

## Report-lag fix (authorized operational fix)

**Defect:**
- `shadow_tick._tick` wrote the reports before `_finish` mirrored that tick's CBBD / Odds request ledgers into the
  evidence tree, so a work tick's report counted the previous tick's requests.
- Seen live in `0e15d26` and again in `c7d6b55` (the 18:34Z slot): 10 CBBD rows and 1 Odds row were added to the
  ledgers, but the report did not change.

**Fix:** `OS.mirror_out(ctx.ops_root, ctx.prod_root)` now runs immediately before `PM.write_reports`.
- `mirror_out` is append-only and makes no change when nothing changed, so idle ticks stay byte-identical. The
  later mirror in `_finish` becomes a no-op.
- Predictions, model state, artifacts, pricing and settlement are untouched.

**Regression test:** `test_work_tick_report_includes_its_own_ledger_activity`.
- Before the fix it failed: the report showed CBBD 0 / Odds 0 against ledgers of 10 / 1.
- After the fix it passes, for the bootstrap work tick and for the scoring tick.

## Focused tests

- **Production test files:** `test_prod_shadow_tick`, `test_prod_odds_feed`, `test_prod_live_feed`,
  `test_prod_shadow`, `test_prod_side_monitor` and `test_props_v1_pricing`: 117 passed. These include provider
  failure, retry, permanent failure, stage budget, quota headers, quote budget exhaustion, ambiguous identity,
  stale data, last-known-good recovery, working-set fallback and the single-writer lock.
- **Replay equivalence:** `test_prod_replay` (local replay path): 3 passed.

## Isolation and cleanup

- **Isolation:** all work happened in a session scratch root, which the tool refuses to place inside `data/prod` or
  the ops checkout. The ops branch `ops/cbb-2026-27-shadow-state` received no rehearsal record. The live prediction,
  quote and settlement ledgers were not touched.
- **Cleanup:** the work root (515 MB: the as-of canonical / feature DuckDB, raw payloads and run directories) has been
  deleted.
- **What is kept:** selection, request metadata (no key), fetch summary, normalized quotes, the board, the run
  report and the rehearsal cumulative report.

## Readiness classification

**CLEARED:**
- REAL_ODDS_PAYLOAD_NORMALIZATION
- STRICT_QUOTE_MATCHING
- PRICING_ENGINE
- BEST_EXECUTABLE_BOARD
- SETTLEMENT_MATH
- DNP_SETTLEMENT
- SIDE_MONITOR_PIPELINE
- MARKET_BENCHMARK_PIPELINE
- REPORTING_PIPELINE

**LIVE_MILESTONE_ARMED** (not blockers):
- FIRST_REAL_2026_27_T60_PREDICTION
- FIRST_REAL_2026_27_COMPLETED_GAME_INGESTION
- FIRST_REAL_2026_27_SETTLEMENT
- PROSPECTIVE_2026_27_MODEL_PERFORMANCE

**Operational readiness:** CBB_ORIGINATOR_OPERATIONAL_READINESS = READY_FOR_2026_27_SHADOW_SEASON.
- The report-lag fix takes effect on the scheduled runner once it is pushed to `release/cbb-v2-prospective-shadow`,
  the branch the workflow checks out.
- Until then, live reports keep the one-tick lag. That lag is not mathematically wrong after the next tick, so it is
  not a blocker.

**Public launch:** PUBLIC_LAUNCH_STATUS = HOLD.
