# CBB V2 JM1_FINAL: shadow production integration (2026-27)

Status: **SHADOW ONLY**. Nothing is deployed, published or wagered, and no live data or odds call was made.
The integration branch is `release/cbb-v2-shadow-ready`, cut from the frozen commit
`e1353e5` (tag `cbb-v2-jm1-final-2026-27`).

The model is frozen. Every predictive file is untouched:

- `cbb_platform/v2/*`, `cbb_platform/stage3/*`, `props_v0*.py`, `props_v1_pricing.py`
- the four frozen artifacts

The new code lives only in `cbb_platform/prod/`. The other additions are its tests, this document, a CI workflow and
one `.gitignore` entry (`data/prod/`).

## Architecture map (audit, 2026-10-20)

| Need | What exists | Production use |
|---|---|---|
| Frozen model | `v2/jm1_final_production.py` holds `load_production` and `score_game` (one bank per game), plus 4 hashed artifacts | Called unchanged by `prod/loader.py` and `prod/simulate.py` |
| Pregame features | Stage 3 SQL (`stage3/universe`, `team_state`, `player_state`) reads only `src.*` views (`stage3/sources.bind`) | Run verbatim on an as-of-masked canonical input (`prod/features.py`) |
| Marginal inputs | `marginal.stage_oos` body: `props_v0.predict`, `_band_sigma`, `props_v0_participation.predict` | Same calls (`prod/inputs.py`) |
| Shared environment | `structural.env_predict` with the frozen JM1_FINAL env | Same call |
| Pricing | `props_v1_pricing.price` (exact over/under/push) | Wrapped (`prod/pricing.py`) |
| Historical data | Frozen canonical DB `data/lockbox_2025_26/canonical/cbb.duckdb`, seasons up to 2025-26 | Read-only history; never rebuilt |
| 2026-27 data | **None.** `lockbox.check_request` refuses every 2026-27 fetch until separate production rules exist | Increment contract only (see Gaps) |
| Odds | **None** in this repo: no Odds API code and no cached odds. BDL NCAAB has game odds only | Odds API v4 normalizer, no calls made |
| Ledger, bundle, CI, deploy | Ledger is a design doc only (`docs/PREDICTION_LEDGER_SCHEMA.md`); no bundle, CI or deploy code existed | Built new. The deploy pattern was copied, read-only, from the NCAAF repo's SSH atomic swap |

## Daily graph (cheap, bounded)

`prod/run.py daily` runs these steps, each once per day:

1. State: read the last-known-good 2026-27 increment.
2. Slate: build the day's games.
3. Compact canonical input, masked at `prediction_as_of`.
4. Run the Stage 3 universe, team-state and player-state SQL for the target games only.
5. Load the frozen model and verify its hashes.
6. Simulate one joint bank per game.
7. Build the market PMFs from that bank.
8. Normalize the quotes and match them strictly.
9. Price the matched quotes.
10. Run the readiness gate.
11. Append to the ledger.
12. Write the static bundle.

**Cost.** About 15 s per slate for the feature build and about 35 s for two runs, measured on the real frozen
history with 2 games and 20,000 draws per game.

**What a daily run never does:**

- refit anything
- rebuild history
- reconstruct the OOS store
- refit dependence
- backtest
- re-simulate per sportsbook

## Incremental state (`prod/state.py`)

The state is the frozen through-2025-26 canonical history, which is read-only and never modified, plus a small 2026-27
**increment** in the canonical flagship format.

**Increment relations.** Each relation carries the exact columns the frozen Stage 3 SQL reads:

- games
- team box
- player box
- substitutions (these feed the `DNP_CONFIRMED` participation history behind P(play))
- rosters
- portal year 2026
- recruiting class 2026
- team-seasons
- teams
- players
- quarantine

**How an update works:**

1. Copy the current increment into a new version.
2. Upsert the new rows.
3. Validate the whole increment:
   - season 2026-27 only;
   - canonical ids;
   - no box rows for a game without a result;
   - no row knowable after `source_cutoff`;
   - no result recorded before it was knowable.
4. Write the state manifest. It records `state_version`, `created_at`, `source_cutoff`, `last_completed_game`,
   provider, input hashes and coverage.
5. Swap `CURRENT.json` atomically.

A failed update leaves `CURRENT.json` untouched and is logged. That log raises `STATE_UPDATE_FAILED` on the next run.

## Point-in-time legality

**Validated prediction cutoff: `TIP_MINUS_1H`.** This is the Stage 3 `CUTOFF_RULE`:

- scheduled tip − 1 h for an EXACT tip;
- 00:00 US/Eastern of the game date otherwise.

Every training row, every out-of-sample forecast and the 2025-26 confirmation were built at this cutoff. It is the only
cutoff that has ever been built. The policy docs' T24H/T6H/T30M/T10M checkpoints were never built.

The validation was **retrospective only**: it used final schedules and no prospective schedule versions. Prospective
fidelity is a monitoring item. No new cutoff was invented.

**Leakage controls:**

- The compact input admits a result only when `historical_knowable_at <= prediction_as_of`.
- Target games enter the compact input with every outcome field NULL and with no box or substitution rows.
- Any target-game or post-as-of outcome row in the built input raises `FUTURE_LEAKAGE`.
- The run checks the feature cutoff against the validated rule.

**Slate guards.** The slate builder also blocks:

- `PREDICTION_AFTER_CUTOFF`;
- `STALE_STATE` when an earlier game of either team, knowable by the cutoff, has no result in the state;
- `AMBIGUOUS_GAME` for duplicate pair/date records;
- `NON_DI_GAME`.

**Production adaptation P-1** (the only one). The frozen `MEMBERSHIP_SQL` derives the team-season set `ts` from box
rows. Historically those came from the completed season, but before a team's first 2026-27 game no box row exists. So
`ts` is also taken from the target schedule. For any team that has already played, the result is identical. The code
asserts the frozen SQL text is unchanged before it substitutes.

**Replay equivalence (`tests/test_prod_replay.py`, local only).** Two real 2025-26 games were replayed at tip − 1h
through the production path. It reproduces:

- the lockbox Stage 3 player features, team features and pregame universe, bit for bit;
- the validated Phase 1B OOS store forecasts (P(play), minutes, all 7 stat means) to rtol 1e-10;
- the frozen pregame environment.

## Availability policy

JM1_FINAL has no validated teammate-redistribution model, so:

- A player confirmed OUT is not priced. He stays recorded, with the reason `CONFIRMED_UNAVAILABLE`.
- Nothing is redistributed. Every teammate's P(play), minutes and stat means are unchanged; a test checks this.
- If an OUT player is in the pregame `TOP5_MINUTES` role or has `min_mu >= 20`, the game is marked
  `UNSUPPORTED_ROTATION_SHOCK` and is never eligible. The forecast is still shadowed.

## Markets and pricing

- **Base stats:** PTS, REB, AST, FG3M, STL, BLK, TOV. Each is priced from the bank's mixture PMF, which is the PMF the
  confirmation scored.
- **Combos:** PRA, PR, PA, RA, STOCKS. Each is the exact sum of the **same** joint draws, with no separate combo model.
- **Probabilities:** P(over), P(under) and P(push) sum to 1. A half line never pushes. The fair price is conditional on
  no push, and EV = P(win)(d − 1) − P(lose).
- **DNP:** probabilities are conditional on playing, with P(play) reported beside them. A DNP is never settled as a zero.

## Readiness (`prod/readiness.py`)

The evaluator fails closed: any listed reason blocks, and an unknown reason also blocks. The ceiling is
**SHADOW_ONLY**, so `PUBLIC_READY` is never returned and `WAGERING_ENABLED = False`.

A blocked forecast is still written to the ledger.

## Ledger, bundle, deploy dry run, CI

- **Ledger:** `prod/ledger.py` is append-only JSON Lines with a hash chain. A prediction is immutable. Settlements are
  appended separately and may not carry prediction-time fields.
- **Bundle:** `prod/bundle.py` writes `index.html`, `predictions.json`, `predictions.csv`, `DEPLOYMENT_MANIFEST.json`
  and `SHA256SUMS`. It is verified NCAAF-style:
  - exact checksum coverage;
  - safe file names;
  - no data stores in the bundle;
  - the frozen artifact hashes;
  - readiness at or below SHADOW_ONLY.
- **Deploy dry run:** `prod/deploy_dryrun.py` replays the NCAAF incoming → verify → previous/live swap → rollback
  sequence on a **local sandbox only**. The module has no network code and refuses any non-sandbox root. The proposed
  path `/tools/odds-scanner/predictions/basketball/ncaab/player-props/` is **not approved**.
- **CI:** `.github/workflows/cbb-shadow-ci.yml` runs on GitHub-hosted Ubuntu with no secrets. It verifies the artifact
  hashes, runs the focused tests and the fixture smoke, then verifies the bundle. It never deploys.

## Prospective monitoring (diagnostics only; never adjusts a forecast)

| Item | 2025-26 confirmation |
|---|---|
| A. PTS mean bias | JM1_FINAL +0.212 vs S0 +0.033 |
| B. High PRA / PR / PA lines | q75 Brier slightly worse than S0 |
| C. Same-team dependence | Correlation-calibration RMSE 0.0161 vs S0 0.0051 |

For C, each game's bank stores its predicted pair correlations beside the ledger. The tracking code is in
`prod/monitoring.py`.

## Gaps that keep LIVE readiness BLOCKED (architect decisions needed)

1. **No 2026-27 data feed.**
   - The lockbox network guard refuses all 2026-27 fetches "until separate production rules exist".
   - A CBBD → canonical increment adapter has not been written. It needs:
     - games, box and substitutions for every completed game;
     - the schedule;
     - portal year 2026 and recruiting class 2026;
     - CBBD id → canonical id via `cid()`, plus the identity repairs.
   - Until then, `prod/run.py daily` returns `BLOCKED` with `STATE_UPDATE_FAILED` and `LIVE_DATA_FEED_UNAVAILABLE`.
2. **Missing preseason evidence.** The frozen history has **no 2026 portal or recruiting rows** (max year 2025).
   Incoming transfers and freshmen cannot enter the pregame universe until the feed supplies them and declares them
   complete. Until then, every game is `MISSING_PLAYER_STATE`.
3. **Quote provider coverage is unresolved.** The provider is The Odds API v4, and the normalizer is implemented.
   NCAAB player-prop coverage has not been established. One discovery request needs approval:
   - **Request:** `GET https://api.the-odds-api.com/v4/sports/basketball_ncaab/events/{eventId}/odds`
   - **Parameters:** `regions=us`, `oddsFormat=american`, `markets=` the 12 player markets in `prod/quotes.py`
     `MARKET_MAP`
   - **Quota cost:** markets × regions per event. For one event that is ≤ 12 credits (the provider charges only the
     markets it returns).
   - **Event id:** must come first from `GET .../basketball_ncaab/events`.
   - **Timing:** meaningful only once 2026-27 games are listed (early November 2026).
   - **Why local evidence is not enough:** there is no NCAAB odds payload anywhere locally; the NBA repo establishes
     NBA coverage only; BDL NCAAB carries game odds only.
4. **Cutoff fidelity.** Prospective cutoff fidelity is unvalidated, because live schedule versions were never part of
   training.
