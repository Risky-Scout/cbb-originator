# WizardOfOdds × CBB Originator production contract v2

**Date:** 2026-10-26.

**Status:**
- This is the AUTHORITATIVE DRAFT for architect review. It is not active until the architect approves it.
- The transport is `PENDING_SITE_CONFIRMATION` (§9).

**Supersedes:** `WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md` (2026-10-07).
- That file is kept unchanged as historical evidence. It is now `SUPERSEDED_PENDING_V2`.
- Where the two differ, v2 governs. §11 lists the differences.

**Implementation:**
- `cbb_platform/prod/production_board.py` (boards, gates, envelope, receipts, transports)
- `cbb_platform/prod/schedule_freshness.py` (the 2 h schedule gate)

**Tests:**
- `tests/test_prod_production_board.py`
- `tests/test_prod_schedule_freshness.py`
- `tests/test_prod_checkpoint_invariance.py`

**Operations semantics:** `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, unchanged.

## 1. What the frozen model actually produces

| item | value | source of truth |
|---|---|---|
| champion | JM1_FINAL, `cbb-v2-jm1-final-shadow-1.0.0`, frozen commit `e1353e5`, tag `cbb-v2-jm1-final-2026-27` | `cbb_platform/prod/config.py`, `modeling/MODEL_REGISTRY.json` |
| artifacts (sha256) | marginal `bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f`; jm1_final `b932fa2155de77f95e2c2afb15b45b22ecf8fa1e49a2d175b7da7cbfca2428f9`; residual_j2 `e42548675fd3c0e363cf26a464d133f384ae659d99b603105510f0937b6570f1`; production_manifest `7d83e9cf4e9c68478e5e71e57ccdcf204b8d3267c4de98d7ea86ee8d5ddcbcab` | verified before every run (`prod/loader.py`) and again when each board is built |
| draws | **20,000 joint draws per game**: one bank per game, shared by every market and every sportsbook | `PC.SIMS`, manifest `random_seed_policy.draws_per_game` |
| markets | **PTS, REB, AST, FG3M, STL, BLK, TOV** (base stats: mixture PMF from the bank) and **PRA, PR, PA, RA, STOCKS** (exact sums of the same draws) | `PC.MARKETS` |
| season | 2026-27 only (canonical 2027); training through 2025-26 | `PC.PRODUCTION_SEASON`, `PC.TRAINING_THROUGH` |
| validated cutoff | `TIP_MINUS_1H` (Stage 3 `CUTOFF_RULE`) | `PC.VALIDATED_PREDICTION_CUTOFF` |

**Not produced, and therefore never advertised:**
- MIN as a priced market;
- TS%, PF, ORB, DRB, FT, FT%, 2P, 2P%, 3P, 3P%;
- team totals and spreads.

JM1 is not modified to add any of them.

## 2. Identity

| concept | field | rule |
|---|---|---|
| game | `canonical_game_id` (`G-…`) | from the production schedule. An ambiguous or duplicate pairing is suppressed (`AMBIGUOUS_GAME`) and never guessed |
| teams | `home_team_id`, `away_team_id` (`T-…`) plus display names | as listed by the provider at the verified schedule instant |
| player | `player_id` (canonical `P-…`) plus `player_name`, `team_id` | frozen identity rules. An unresolved, ambiguous, transferred-out or roster-less player is suppressed with its reason. No fuzzy matching |
| scheduled tip | `scheduled_tip_utc` | EXACT tips only for publication (`SCHEDULED_TIP_NOT_EXACT` otherwise) |
| checkpoint | `checkpoint_label`, `checkpoint_at` | §6 |
| prediction | `prediction_as_of`, `feature_cutoff` (= tip − 1 h), `data_cutoff` (the state's `source_cutoff`) | written by `run.daily`. The board layer never re-scores |
| schedule verification | `schedule_verified_at` | the latest provider confirmation of the exact schedule payload the state was built from (§5) |
| model | `model_identity` {`model_version`, `architecture`, `frozen_commit`, `frozen_tag`, `artifact_sha256`, `production_manifest`} plus `draws_per_game = 20000` | |
| state | `run_id`, `state_version` (an immutable increment, verified by sha256) | |

## 3. Public projection board (`board_kind = PUBLIC_PROJECTION_BOARD`)

There is **one row for every (scored game, player, supported market)**, whether or not a sportsbook line exists.
Projections never wait for a line.

| field | meaning |
|---|---|
| `canonical_game_id`, `game`, `scheduled_tip_utc`, `home_team_id`, `home_team`, `away_team_id`, `away_team` | game |
| `player_id`, `player_name`, `team_id`, `team` | player |
| `market` | one of the 12 supported markets |
| `projection` | model mean, **conditional on playing** (`projection_basis = MEAN_CONDITIONAL_ON_PLAYING`). `null` when suppressed |
| `p_play` | model probability that the player plays. This is an existing model output, published beside the projection |
| `pmf_q75` | 75th percentile of the market PMF. This is an existing field, `null` when suppressed |
| `distribution_method` | `BANK_MIXTURE_PMF` (base stats), `BANK_DRAWS` (combos) or `NOT_PRICED` |
| `prediction_as_of`, `feature_cutoff`, `data_cutoff`, `schedule_verified_at` | point-in-time provenance |
| `model_version`, `architecture`, `frozen_commit` | model identity |
| `status` | `PUBLISHABLE` or `SUPPRESSED` |
| `suppression_reasons` | the codes (§8). Empty when publishable |

Board header fields:
- `contract_version` (`cbb-wizard-production-contract-2.0.0`), `operational_mode` (`PRODUCTION`)
- `model_identity`, `draws_per_game`, `supported_markets`
- `run_id`, `state_version`, `checkpoint_label`, `checkpoint_at`, `prediction_as_of`, `data_cutoff`, `data_label`
- `schedule_freshness_policy`
- `games` (per game: `publication_allowed`, `POINT_IN_TIME_LEGALITY`, `SCHEDULE_FRESHNESS_GATE`,
  `schedule_verified_at`)
- `publication_id`, `payload_sha256`, `idempotency_key`

**Visibility:**
- Only `PUBLIC_FIELDS` are emitted.
- The private pricing fields (§4) are added only when `PUBLISH_PRIVATE_FIELDS` is separately authorized. It defaults
  to `False` and is never set by this contract.

## 4. Internal pricing board (`board_kind = INTERNAL_PRICING_BOARD`): never public by default

There is one row per quoted (player, market, line, side). It carries every public field, plus:

| field | meaning |
|---|---|
| `line`, `side` | sportsbook line and side (OVER / UNDER) |
| `p_over` / `p_under`, `p_push` | model probabilities (conditional on playing). P(over) + P(under) + P(push) = 1; a half line never pushes |
| `fair_decimal_over` / `fair_decimal_under`, `fair_american_over` / `fair_american_under` | **FAIR PRICE**: the model probability (conditional on no push) converted to odds. Never executable |
| `sportsbook`, `executable_decimal`, `executable_american` | **EXECUTABLE PRICE**: a real current sportsbook price returned by the provider. Best price among VALID, matched, fresh quotes |
| `quote_available_at`, `collector_received_at`, `quote_age_seconds` | quote provenance and age at the checkpoint |
| `price_kind` | `{"fair": "MODEL_FAIR_PRICE", "executable": "SPORTSBOOK_EXECUTABLE_PRICE"}` on every row |
| `raw_edge_ev_per_unit` | EV per unit staked = P(win)(d − 1) − P(lose) at the **executable** price. `null` unless every quote gate passes. It is never computed against a fair price or a stale or absent quote |
| `mcse` | Monte Carlo standard error (combos) |
| `QUOTE_QUALITY_GATE`, `failed_quote_gates` | §7 |
| `pricing_status` | `PRICING_ALLOWED` or `PRICING_SUPPRESSED` |
| `actionable_bet_allowed` | `true` only for `board_state = PRICED_ACTIONABLE`; in this phase always `false` (§10) |
| `board_state` | `PRICED_ACTIONABLE`, `PRICED_BLOCKED` or `SUPPRESSED` |
| `actionable_block_reasons` | why the row is not actionable (docs/ROTOWIRE_AVAILABILITY_2026_27.md §7a) |
| `stake` | always `0.0` (no staking policy) |
| `availability_status`, `availability_received_at` | the availability status the row was priced with (internal only) |

The board header also carries `betting_policy` (§10) and the full per-game gate status.

## 5. Schedule freshness: `2_HOURS_TARGETED_REFRESH_FAIL_CLOSED`

The architect's decision of 2026-10-26 sets the requirement at 2 hours.

At every checkpoint, each game's schedule must have been verified by the provider within the previous **2 h**. An
unchanged re-fetch counts as a verification, but only for the exact payload the state used, and never one dated after
the checkpoint.

If verification is older than 2 h, the gate runs **one targeted refresh**:
- It re-fetches only the month window(s) holding the stale games, through the existing production sync
  (`refresh_schedule_windows`). It never re-fetches the whole season.
- It then requires that:
  - the canonical id and provider id are unchanged;
  - home and away are unchanged;
  - the tip is unchanged and EXACT;
  - the game is listed and not cancelled or postponed;
  - the game has not started;
  - the age is ≤ 2 h.

**Failure:** `SCHEDULE_FRESHNESS_GATE = FAIL`, with no publication, no pricing recommendation and no actionable bet.
Every evaluation is appended to `data/prod/gates/schedule_freshness.jsonl`.

**Relation to the existing 36 h rule:** `PC.SCHEDULE_MAX_AGE_HOURS = 36` still governs the run-level STALE_STATE
recording rule. The 2 h gate is additive and never reaches a forecast.

## 6. Checkpoints, versions and immutability

- **Each board is immutable.** A later checkpoint creates a new board: a different `checkpoint_label`, so a different
  `publication_id`. The earlier one stays auditable.
- **Versions within a checkpoint:** a re-send of identical content is a replay, recorded as `DUPLICATE_NOOP`. A
  materially different board for the same slot is version n+1, with `SUPERSEDES` lineage pointing to the previous
  `idempotency_key`. Both payloads are kept.
- **Material late information** (for example a player confirmed OUT, a tip move or a cancellation) produces a
  superseding board in which the affected rows are `SUPPRESSED`. The historical prediction in the append-only ledger is
  never rewritten.
- **No publication at or after tip** (`GAME_ALREADY_STARTED`). A retry that would land after tip is abandoned.
- **Checkpoint policy:** the current policy is a single T−60 window. The PROPOSED multi-checkpoint policy needs
  architect approval: see `docs/PRODUCTION_CONTRACT_RECONCILIATION_2026-10-26.md` §7.

## 7. Gates

**Point in time.** Each game carries these flags:
- `POINT_IN_TIME_LEGALITY` (PASS / FAIL)
- `TARGET_GAME_FEATURE_LEAKAGE_DETECTED`: set by `FUTURE_LEAKAGE`, or when a record's `feature_cutoff` differs from the
  validated rule
- `TARGET_GAME_OUTCOME_LEAKAGE_DETECTED`: set when the target game's result, team box or player box appears in the
  state increment or in the run's as-of canonical database
- `FUTURE_DATA_LEAKAGE_DETECTED`: set by `POST_CUTOFF_DATA` or `PREDICTION_AFTER_CUTOFF`, or when
  `prediction_as_of > feature_cutoff` or the state cutoff is after `prediction_as_of`

Any TRUE flag means no publication, no pricing and no betting recommendation for that game.

**Quote quality.** All of these are required for actionable pricing:

| gate | meaning |
|---|---|
| `EXACT_EVENT_MATCH` | exact canonical event match |
| `STRICT_PLAYER_IDENTITY` | strict player identity; `prod/matching.py`, never fuzzy |
| `SUPPORTED_MARKET` | supported market mapping |
| `VALID_LINE` | non-negative multiple of 0.5 |
| `VALID_SIDE` | OVER or UNDER |
| `VALID_PRICE` | decimal > 1 |
| `QUOTE_FRESH_AT_CHECKPOINT` | within `QUOTE_MAX_AGE_SECONDS` = 20 min, at or before the checkpoint |
| `GAME_NOT_STARTED` | the game has not started |
| `PROVIDER_RETURNED_QUOTE` | the provider actually returned the quote |
| `MODEL_HASH_GATE` | model hashes match |
| `POINT_IN_TIME_GATE` | point-in-time gate passed |

A failed gate suppresses actionable pricing for that row. No other book or line is substituted unless that quote
independently passes every gate.

**Commercial rights.** A **public** transport refuses every board while any production source in
`COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md` §2 is not `PRODUCTION_APPROVED`. Today the Starter Pack is
`RESEARCH_ONLY` and the CBBD API is `UNVERIFIED`.

**FIXTURE data** is never publishable.

## 8. Fail-closed matrix

The authoritative matrix is `production_board.FAIL_CLOSED_MATRIX`; the reconciliation document reproduces it.
- Every reason code of `prod/readiness.py` maps to a matrix condition. An unknown code blocks everything.
- Quote-only codes block pricing but **not** a public projection: `NO_VALID_QUOTE`, `STALE_QUOTE`,
  `ODDS_EVENT_NOT_YET_LISTED`, `PLAYER_PROP_MARKET_NOT_YET_POSTED`, `QUOTE_INTEGRATION_UNVALIDATED`,
  `ODDS_API_FAILURE` and similar codes.

## 9. Transport: `PUBLISH_TRANSPORT = PENDING_SITE_CONFIRMATION`

No CBB-compatible WizardOfOdds interface is confirmed.
- The v1 HTTPS `POST /api/cbb/originator/v1/boards` exists nowhere.
- The NCAAF SSH atomic-swap deployment is confirmed for NCAAF only: it is scoped to `…/predictions/NCAAF` with a
  restricted `wizard-deploy` account.
- `PC.PROPOSED_PUBLIC_PATH` is unapproved.

The only implemented transports are:
- `PendingSiteConfirmationTransport`, which always refuses;
- `LocalStagingTransport`, which is private, local and used by the tests.

The transport-independent parts are implemented and tested:
- the envelope;
- idempotency;
- receipts;
- supersession;
- read-back reconciliation;
- no-post-tip;
- the retry-safety semantics.

**Required behaviour of any confirmed transport:**

| property | requirement |
|---|---|
| idempotency | `idempotency_key` = sha256(`publication_id` \| `payload_sha256`). Same key twice gives one logical publication (`DUPLICATE_NOOP`) |
| identity | `publication_id` = `PUB-` + sha256(contract_version, board_kind, sorted game ids, checkpoint_label)[:24]. `payload_sha256` = sha256 of the canonical JSON (sorted keys, no NaN) excluding re-verification instants (`checkpoint_at`, `schedule_verified_at`, `quote_age_seconds`) |
| receipt | an append-only receipt per attempt: `PUBLISHED` (v1), `SUPERSEDES` (vN with `supersedes`), `DUPLICATE_NOOP`, `PUBLICATION_FAILURE`, `PUBLICATION_RECONCILIATION_MISMATCH` |
| read-back | the payload is re-read after every send and must hash-match, or the attempt is recorded as `PUBLICATION_RECONCILIATION_MISMATCH` and nothing is marked published |
| retry | on a transport error, or on HTTP 429 / 5xx if HTTP. A retry is safe: same key. Never after tip |
| conflict | a payload whose hash does not match its content is rejected (`DUPLICATE_PUBLICATION_CONFLICTING_PAYLOAD`) |
| rollback | point the site to the previous immutable board version (or the NCAAF-style `.previous` swap if SSH is confirmed). History is never deleted |

## 10. Real-money decision support

- `AUTOMATIC_ACTIONABLE_WAGER = DISABLED`. No bet placement is authorized.
- The internal board shows projections, probabilities, fair prices and raw edge only.
- The architect must supply the betting policy at `config/betting_policy.json` (absent today). Its fields are
  `eligible_sportsbook_accounts`, `bankroll`, `stake_sizing_method`, `kelly_fraction`, `max_wager`,
  `daily_exposure_cap`, `player_exposure_cap`, `game_exposure_cap`, `correlated_market_exposure_cap`,
  `min_edge_to_wager`, `approved_by` and `approved_at`.
- Until it is supplied: `ARCHITECT_BETTING_POLICY_REQUIRED = TRUE`.

## 11. Differences from v1 (reasons)

| v1 | v2 | why |
|---|---|---|
| `draw_count: 200000` | **20,000** joint draws per game | the frozen manifest and `PC.SIMS` |
| stats MIN, PTS, TS%, REB, AST, STL, BLK, TOV, PF, ORB, DRB, FT, FT%, 2P, 2P%, 3P, 3P% | PTS, REB, AST, FG3M, STL, BLK, TOV, PRA, PR, PA, RA, STOCKS | what JM1_FINAL actually produces |
| five checkpoints T24H/T6H/T1H/T30M/T10M | single T−60 implemented. Multi-checkpoint is PROPOSED | only TIP_MINUS_1H is validated or built. T30M and T10M fall after the validated cutoff |
| HTTPS POST API *(confirm)* | `PENDING_SITE_CONFIRMATION` | no such API exists |
| one board = one game × checkpoint, with lines | public projection board (no lines) separate from the internal pricing board | projections must not wait for lines; private fields are not published by default |
| `idempotency_key` over (game, checkpoint, bundle, payload) | over (`publication_id`, `payload_sha256`), with volatile verification instants excluded | a re-send at a later instant is a replay |
| wire rounding 4 decimals | full precision in the payload. Rounding is presentation only and must be agreed with the site | no site schema confirmed |
