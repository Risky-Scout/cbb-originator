# CBB V2 JM1_FINAL: shadow production integration (2026-27)

> **Prospective operation (2026-10-22 onward):** see `docs/PROSPECTIVE_SHADOW_OPERATION.md` (one idempotent
> `shadow_tick`, durable private evidence, monitors, scheduler report). Historical PTS research is closed.

Status: **SHADOW ONLY**. Nothing is deployed, published or wagered. The only Odds API calls made are two free (0-credit)
events listings; no paid player-prop request has been made.
The integration branch is `release/cbb-v2-shadow-ready`, cut from the frozen commit
`e1353e5` (tag `cbb-v2-jm1-final-2026-27`). The 2026-27 CBBD production data feed (section
"2026-27 production data feed") is on `release/cbb-v2-live-data-feed`, cut from the shadow commit `780673f`.

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
| Odds | **None** at audit time. BDL NCAAB has game odds only | The Odds API v4 production quote feed (`prod/odds_acquire.py`, `prod/odds_feed.py`; section below) |
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

## 2026-27 production data feed (CBBD; `release/cbb-v2-live-data-feed`)

The feed removes the live blocker `LIVE_DATA_FEED_UNAVAILABLE` and resolves `MISSING_PLAYER_STATE` wherever legally valid
roster / cold-start information exists. Nothing predictive changed: the four artifacts, `cbb_platform/v2/*`,
`cbb_platform/stage3/*`, `props_v0*.py`, `props_v1_pricing.py` and every canonical-build module are untouched, and the
cutoff stays `TIP_MINUS_1H`.

### Verified CBBD resources (live, 2026-10-20)

| Need | CBBD resource (production call) | Observed on 2026-10-20 |
|---|---|---|
| A. 2026-27 schedule | `/games?season=2027&startDateRange&endDateRange` | 6,015 games, all `scheduled`; 2,669 with `startTimeTbd` |
| B. current rosters | `/teams/roster?season=2027` (all teams, one call) | 1,544 team records, 365 non-empty, 5,288 players |
| C. transfers | `/recruiting/portal?year=2026` | 1,579 records (1,308 with a destination) |
| D. freshmen | `/recruiting/players?year=2026` | 396 recruits (350 committed), **0 with an `athleteId`** |
| E. completed games | `/games` (status `final`), `/games/teams`, `/games/players`, `/stats/player/season`, `/substitutions/game/{id}` | none completed yet (season opens 2026-11-01) |

The same endpoints, field shapes and season conventions (`season` = end year, `year` = class / portal cycle) are the ones
the lockbox route used for 2025-26 (`cbbd_box_route.py`, `event_build.py`). No endpoint or field was invented.

### Production-only acquisition (`prod/cbbd_acquire.py`)

* The research path (`source_fetch.py` -> `lockbox.check_request`, `cbbd_client.py`) is unchanged, byte for byte, and still
  refuses every 2026-27 request (tested). There is no switch, environment variable or flag that widens either path.
* The production path has its own guard. It accepts only:
  * an endpoint allowlist: `games`, `games/teams`, `games/players`, `stats/player/season`, `substitutions/game`,
    `teams/roster`, `recruiting/players`, `recruiting/portal`;
  * `season` = 2027 exactly;
  * `year` = 2026 exactly for recruiting and portal;
  * closed date ranges inside 2026-08-01 .. 2027-07-31;
  * a `gameId` only when the production schedule proves it is a 2026-27 game;
  * no other parameter.
* Responses are checked before storage. Every `season` must be 2027, every class `year` 2026, and every game date inside
  2026-27. Anything else is refused.
* **Cache.** Raw responses go to `data/prod/cache/raw/cbbd/...`, using the immutable `raw_store` layout. The directory is
  gitignored and is never `data/raw`, which the research canonical build reads. A response younger than its stage TTL is
  reused without a call. The TTLs are:
  * preseason: 24 h;
  * schedule: 6 h;
  * box and substitutions of final games: immutable.
  An unchanged re-fetch keeps the original payload file and records `verified_at`.
* **Request ledger.** `data/prod/cache/cbbd_production_requests.jsonl` gets one line per acquisition. Each line records:
  provider, endpoint, params, requested_at, received_at, verified_at, status, http_status, season, record_count,
  response_sha256, cache_path, retry_count and quota_remaining. The status is one of `FETCHED`, `FETCHED_UNCHANGED`,
  `CACHE_HIT`, `ATTEMPT_FAILED`, `FAILED`, `REFUSED_GUARD`, `REFUSED_RESPONSE_GUARD`, `REFUSED_BUDGET` or `REFUSED_QUOTA`.
  The credential is read only into the `Authorization` header and appears in no log, payload or metadata (tested).
* **Request budget.** Each sync stage stops before any call that would exceed `MAX_LIVE_REQUESTS_PER_STAGE` = 20
  (`BudgetExceeded`). A quota floor of 1,000 calls is also enforced.
* **Retries.** 429, 5xx and network errors are retried up to 3 times, honouring `Retry-After`. Other HTTP errors fail
  permanently. A failure is never cached as data.

### Sync contract (`prod/sync.py`)

`python -m cbb_platform.prod.sync run` turns last-known-good state plus newly legally available 2026-27 information into a
candidate state. The candidate is validated, then promoted atomically (`CURRENT.json` via `os.replace`). The stages are:

| Stage | Requests | Live calls on 2026-10-20 |
|---|---|---|
| preseason | roster 2027, recruiting 2026, portal 2026 | 3 |
| schedule | 7 chained month windows (`[start 00:00Z, end 00:00Z)` semantics, as `cbbd_box_route._windows`) | 7 |
| completed | per newly knowable final game date: `games/teams` and `games/players` (window [d-1, d+1]); `stats/player/season`; one `substitutions/game` per game | 0 (no game final yet) |

* **Snapshot.** The latest cached payload of each request received at or before the sync cutoff is copied into a scratch
  `raw_store` root. The frozen readers therefore see exactly one snapshot and never a stale older copy.
* **Snapshot relations replace, they don't upsert.** `flagship.rosters` 2027, `core.transfers` 2026, `core.recruits`
  2026, `core.team_seasons` 2027 and `metadata.prod_schedule` are replaced whole, so a player dropped from a roster or a
  withdrawn portal record cannot survive from an older snapshot. Games and box rows are upserted.
* **Idempotent.** A candidate whose relations hash exactly like the current state's creates no version. The pointer
  records only `last_successful_sync` and `last_sync_source_cutoff`. Verified live: the second sync returned `UNCHANGED`.
* **Manifest fields:** `state_version`, `created_at`, `source_cutoff`, `last_successful_sync`, `schedule_as_of`,
  `roster_as_of`, `last_completed_game_included`, `feed_provenance` (request key, received_at and sha256 of every
  payload), `preseason_evidence`, `coverage` and relation hashes.
* **Failure handling.** Any failure leaves `CURRENT.json` untouched and is appended to `data/prod/sync/sync_log.jsonl`
  with a reason: `PROVIDER_FAILURE`, `MISSING_SCHEDULE`, `MISSING_ROSTER`, `POST_CUTOFF_DATA` or
  `STATE_UPDATE_FAILED`. The kinds of failure are provider, budget, guard and validation. The next daily run then
  carries `PROVIDER_FAILURE`.

### Canonical increment (`prod/cbbd_feed.py`): the frozen rules re-run on production rows

Identity, PIT and accounting rules are the frozen canonical code. It runs unchanged on a scratch DuckDB that contains the
frozen schema (`schema.sql`) plus read-only seeds from the frozen history. Nothing in the history is modified.

**Schedule.** One `flagship.games` row per CBBD game uses the `build_canonical.build_games` field rules:
* `cid('G','game',id)`;
* tip = `startDate` (UTC);
* `tip_time_quality` = `DATE_ONLY`, `TBD_FLAG` or `EXACT`;
* `game_date_et` = the ET date of the tip;
* results only for status exactly `final` (`cbbd_box_route.is_final_status`) **and** knowable under HR-1 at the cutoff.

`metadata.prod_schedule` carries `provider_game_id`, status, start_time, TBD flag, teams (canonical and provider),
neutral site, venue, `source_received_at`, `listed_in_latest_schedule`, `schedule_as_of` and `substitutions_acquired`.

**D-I status, 2026-27.** A team is D-I when its 2026-27 CBBD roster is non-empty. On 2025-26 that rule equals the frozen
D-I proxy exactly: 365/365 D-I and 362/362 non-D-I. On 2026-10-20 it gave 365 D-I teams: 364 continuing, 1 new, 1
dropped. A team whose status is unknown makes the game `UNKNOWN`, which raises `MISSING_ROSTER`.

**Rosters, transfers and freshmen.** These come from `event_build.build_rosters`, `_known_players` and
`build_recruiting`, run verbatim. The seeds are every canonical player, the CBBD athlete anchors and the 2025-26 origin
rosters.

* **Returning players.** The CBBD athleteId is the verified persistent id, so a returning roster athlete maps to his
  existing canonical id (`cid('P','athlete',id)`). On 2026-10-20, 4,885 of 5,288 roster players were already known.
  Their whole prior history is the history the frozen state reads, and they are never cold starts (tested).
* **Transfers** use the frozen portal rule.
  * Identity comes from the origin 2025-26 roster (first/last name). When the 2026-27 destination roster lists the same
    id, the record is `RESOLVED_ROSTER_CORROBORATED`; otherwise it is `CANDIDATE_ORIGIN_ROSTER_NAME`. Membership moves to
    the destination through the frozen `TRANSFER_PRESEASON_EVIDENCE`, and the origin returner is excluded by the frozen
    `portal_out`.
  * Prior games stay origin-team history; they are never relabeled (tested).
  * Ambiguous names stay `UNRESOLVED_AMBIGUOUS_NAME` and get no identity. Unmatched names stay
    `UNRESOLVED_NO_ROSTER_MATCH`. Both are suppressed.
  * On 2026-10-20 the counts were 300 RESOLVED, 1,150 CANDIDATE and 129 UNRESOLVED. CBBD's preseason rosters often still
    list a transfer at his origin team, or list him at the destination under a re-issued athlete id. In that case the
    frozen rule keeps the history-bearing identity, and the re-issued duplicate is suppressed (see universe).
* **Freshmen.** The frozen recruit rule needs a CBBD athleteId, and CBBD has linked **none** of the 2026 class yet. The
  frozen rule therefore yields only the candidate `CANDIDATE_COMMITTED_ROSTER_NAME`: a unique exact name on the
  committed team's 2026-27 roster.
  * **Production identity adaptation P-2** promotes exactly that candidate to the canonical id
    (`PROD_P2_COMMITTED_ROSTER_NAME_UNIQUE`).
  * Measured over the 2021-2025 classes, this rule agrees with CBBD's own athleteId link in 1,419 of 1,419 cases, with 0
    disagreements and 0 ambiguous names.
  * On 2026-10-20 it linked 131 of 350 committed recruits. The other 219 committed recruits (plus 46 uncommitted) are
    not on their committed CBBD roster yet. They stay unlinked and suppressed (`RECRUIT_IDENTITY_UNAVAILABLE`); no
    identity is minted and no statistic is fabricated.
  * **Architect confirmation requested for P-2.**
* **Cold start** is exactly the frozen `player_state` behavior. `cold_start = TRUE` marks a true new college player
  (`COLD_NO_HISTORY`); transfers are `COLD_TRANSFER_IN` with their prior college history. Recruiting attributes come from
  the legitimate 2026 class record.
* **Other newcomers.** JUCO, international, walk-on and non-D-I transfers are kept in the roster-awareness table but have
  no frozen membership evidence (`NO_FROZEN_MEMBERSHIP_EVIDENCE`). No new prior is invented.

**Completed games.** Reshape, dedupe, final-only and team-assignment validation run verbatim from
`cbbd_box_route` (`reshape`, `dedupe`, `final_games_only`, `validate_team_assignment`). These frozen build functions
then run unchanged:
* `build_canonical.build_games`;
* `build_team_games`, including `KNOWABLE_SQL` HR-1;
* `build_players_and_player_games`, which applies the frozen (season, team, exact name) identity rule with the D-I
  `stats/player/season` name key;
* `flag_accounting`;
* `event_build.build_substitutions`.

A final game is ingested only when all of the following hold:
* its HR-1 `knowable_at` is at or before the cutoff;
* both team boxes and the player box are present;
* its substitution request succeeded.

Otherwise its result is withheld. A result never enters the state without its box rows. On fixtures:
* a 03:00Z sync deferred game D;
* the 06:00Z sync ingested it, with 2 team rows, 22 RESOLVED player rows and 10 stints, all knowable at 04:00Z (tip + 4 h).

**Not yet proven on live data:** completed-game ingestion. No 2026-27 game has finished. It is proven on labeled CBBD-format
fixtures only.

**In-season request volume.** A busy day needs about (2 x dates + 1 + games) requests, because substitutions are
requested per game. That is more than the 20-per-stage limit on most game days. The stage STOPs with `BudgetExceeded`
before any call. The budget is an architect decision (quota remaining 105,254).

### Pregame player universe (`prod/universe.py`)

The frozen pool (`ex.player_game_examples`) is never changed. The current-season roster is not membership evidence (Stage
3.1 roster PIT policy); adding it as a dated evidence class would be a feature-semantic change for the architect. The
roster is used for identity and for explicit, recorded suppression.

**Pool player statuses:**

| Roster status | Result |
|---|---|
| `ON_CURRENT_ROSTER` | scoreable |
| `ON_CURRENT_ROSTER_PROVIDER_ID_CHANGED` (same exact name under a re-issued id) | scoreable |
| `ON_OTHER_TEAM_ROSTER` | `IDENTITY_CONFLICT` |
| `NOT_ON_CURRENT_ROSTER` | `NOT_ON_CURRENT_ROSTER` |

A suppressed pool player is still simulated, so the bank is exactly the frozen pool's, but he is never priced. The rows
are `NOT_PRICED` with a reason.

**Roster-only players** are recorded, never predicted:
* `DUPLICATE_PROVIDER_IDENTITY`: the re-issued-id copy of a pool player;
* `TRANSFERRED_OUT`: a stale origin listing;
* `AMBIGUOUS_PLAYER`;
* `NO_FROZEN_MEMBERSHIP_EVIDENCE`.

Committed recruits with no identity are recorded as `RECRUIT_IDENTITY_UNAVAILABLE`.

**Columns:** game_id, team_id, canonical_player_id, provider_player_id, name, roster_status, newcomer_class (RETURNING /
TRANSFER / FRESHMAN / OTHER_NEWCOMER), in_frozen_pool, pool_evidence, cold_start_class, cold_start, scoreability,
suppression_reason, source, source_timestamp. The table is written to `runs/<run>/player_universe.csv` on every run.

**Real 2026-27 check (2026-10-20).** Three opening-night games (2026-11-02) were run through the production state as of
2026-10-20, on a scratch ledger:
* Florida vs Miami;
* Purdue vs Gonzaga;
* BYU vs Ohio State.

Results:
* 0 game-level blockers, so neither `MISSING_PLAYER_STATE` nor `LIVE_DATA_FEED_UNAVAILABLE`;
* 3 joint banks (PSD, no jitter) and 1,164 shadow rows;
* pool of 97: 67 returning, 23 transfers and 7 freshmen; 11 are true cold starts;
* 69 scoreable.

The 28 suppressed pool players:
* 26 `NOT_ON_CURRENT_ROSTER`. This group includes departed players the frozen eligibility rule still admits (e.g., a
  2026 one-and-done), and portal transfers whose destination is not yet on CBBD's current roster.
* 2 `IDENTITY_CONFLICT`.

The roster-only players: 10 `NO_FROZEN_MEMBERSHIP_EVIDENCE`, 8 `RECRUIT_IDENTITY_UNAVAILABLE` and 2 `TRANSFERRED_OUT`.
These clear automatically as CBBD updates its rosters and the daily sync replaces the snapshot.

### Point-in-time protections (all tested)

* **Payload timing.** Every payload used was received at or before the sync cutoff. The snapshot refuses a roster
  received later (`MISSING_ROSTER`).
* **State newer than prediction time.** A state whose cutoff is after `prediction_as_of` raises `POST_CUTOFF_DATA`.
* **Results.** A final game's result enters only once its HR-1 `knowable_at` is at or before the cutoff. A later game's
  features see it only when its own as-of is at or after `knowable_at` (03:59Z excluded, 04:00Z included).
* **Target games.** Target games carry no result, box or substitution rows (`FUTURE_LEAKAGE` = 0).
* **Recorded times.** The validated cutoff stays `TIP_MINUS_1H`. Each run records `prediction_as_of`, `feature_cutoff`,
  `schedule_as_of`, `roster_as_of` and `state_as_of` (the state cutoff).

### Failure modes and blockers

| Code | Level | When |
|---|---|---|
| `LIVE_DATA_FEED_UNAVAILABLE` | run | no promoted live state |
| `PROVIDER_FAILURE` | run | the latest sync attempt failed (the run continues on last-known-good) |
| `STALE_STATE` | run / game | last verified sync older than 36 h; schedule older than 36 h; an earlier knowable game of a team not fully ingested |
| `STALE_ROSTER` / `MISSING_ROSTER` | run / game | roster verification older than 7 days / absent; a team's D-I status unknown |
| `MISSING_SCHEDULE` | game | a game cancelled, postponed or no longer listed |
| `UNKNOWN_TEAM` | game | a schedule record without a team id |
| `POST_CUTOFF_DATA` | run | the state was cut after `prediction_as_of` |
| `AMBIGUOUS_PLAYER` / `IDENTITY_CONFLICT` / `NOT_ON_CURRENT_ROSTER` / `DUPLICATE_PROVIDER_IDENTITY` / `TRANSFERRED_OUT` / `NO_FROZEN_MEMBERSHIP_EVIDENCE` / `RECRUIT_IDENTITY_UNAVAILABLE` | row | universe (above) |
| quote states | run / game / row | see "2026-27 sportsbook quotes" below (`QUOTE_COVERAGE_UNVERIFIED` is retired) |

### State locations and daily invocation

```
data/prod/cache/raw/cbbd/...                       immutable production payload cache (gitignored)
data/prod/cache/cbbd_production_requests.jsonl     request ledger
data/prod/state/CURRENT.json                       last-known-good pointer (+ last_successful_sync)
data/prod/state/versions/<state_version>/          increment.duckdb + state_manifest.json (immutable)
data/prod/sync/sync_log.jsonl                      one line per sync attempt
data/prod/runs/<run_id>/                           run_summary.json, player_universe.csv, board, bundle
```

```
.venv/bin/python -m cbb_platform.prod.sync run                 # daily (and before each slate): preseason, schedule, completed
.venv/bin/python -m cbb_platform.prod.sync status
.venv/bin/python -m cbb_platform.prod.run daily --as-of <now>  # shadow run on the last-known-good state
```

Production state on 2026-10-20: `S20261020T235544Z-a518475bc494` (6,015 games, 5,288 roster rows, 1,579 portal, 396
recruits). 11 live CBBD production requests in total, all season 2027 / class year 2026. No historical backfill.

## Gaps that keep LIVE readiness BLOCKED (architect decisions needed)

1. **Resolved: no 2026-27 data feed.** The production-only CBBD feed above replaces it, and the lockbox guard is
   unchanged.
2. **Resolved where data exist: missing preseason evidence.** The 2026 portal and the 2026 class are ingested.
   * Freshmen depend on production adaptation P-2, for which architect confirmation is requested.
   * Recruits CBBD has not yet placed on a roster stay suppressed until CBBD updates.
3. **Quote integration not yet validated on a real event** (`QUOTE_INTEGRATION_UNVALIDATED`). NCAAB player-prop
   support is documented by the provider; the integration is built and fixture-tested. On 2026-10-21 the Odds API
   listed **0** NCAAB events for the next 14 days, so every opening-night game carries `ODDS_EVENT_NOT_YET_LISTED`.
   Validation runs once a real event is listed and inside the 36 h prop window (`odds_feed validate --event <id>`).
4. **Cutoff fidelity.** Prospective cutoff fidelity is unvalidated, because live schedule versions were never part of
   training.
5. **In-season request budget.** Per-game substitution requests exceed 20 per stage on most game days, so a budget
   decision is needed before the first completed games (2026-11-01).

## 2026-27 sportsbook quotes (The Odds API v4; `release/cbb-v2-odds-quotes`)

NCAAB player-prop **support is documented** by the provider (sport `basketball_ncaab`; player props one event at a
time through `GET /v4/sports/basketball_ncaab/events/{eventId}/odds`). No paid request is ever made to discover it.

### Documented market keys (`prod/quotes.py` `MARKET_KEYS`)

Source: <https://the-odds-api.com/sports-odds-data/betting-markets.html>, read 2026-10-21. The base keys are in the
section headed "NBA, NCAAB, WNBA Player Props API". The `*_alternate` keys are in a section headed "Alternate NBA
Player Props API" (NBA only), so for NCAAB they are not documented: they are **never requested**, but a real
alternate quote the provider does return is kept (it is an actual sportsbook price, not a fabrication).

| MODEL_STAT | ODDS_API_MARKET_KEY | DOCUMENTED_SUPPORTED (NCAAB) | ALTERNATE_KEY (NBA-documented) |
|---|---|---|---|
| PTS | `player_points` | TRUE | `player_points_alternate` |
| REB | `player_rebounds` | TRUE | `player_rebounds_alternate` |
| AST | `player_assists` | TRUE | `player_assists_alternate` |
| FG3M | `player_threes` | TRUE | `player_threes_alternate` |
| STL | `player_steals` | TRUE | `player_steals_alternate` |
| BLK | `player_blocks` | TRUE | `player_blocks_alternate` |
| TOV | `player_turnovers` | TRUE | `player_turnovers_alternate` |
| PRA | `player_points_rebounds_assists` | TRUE | `player_points_rebounds_assists_alternate` |
| PR | `player_points_rebounds` | TRUE | `player_points_rebounds_alternate` |
| PA | `player_points_assists` | TRUE | `player_points_assists_alternate` |
| RA | `player_rebounds_assists` | TRUE | `player_rebounds_assists_alternate` |
| STOCKS | `player_blocks_steals` | TRUE | none documented |

Every other key, including an undocumented alternate such as `player_blocks_steals_alternate`, is
`UNSUPPORTED_MARKET`. The model prices all 12 markets internally. A market enters the Best Executable Wager board only
at a line an actual sportsbook quoted for that player. A market an event does not offer is
`MARKET_NOT_OFFERED_FOR_EVENT`, which is never a conclusion about the provider in general.

### Production-only acquisition (`prod/odds_acquire.py`)

* **Endpoints.** Only two: `events` (0 credits, documented) and `event_odds`. There are no featured `/odds` calls, no
  historical endpoint and no other sport.
* **Parameters.** `regions=us` (one region), `oddsFormat=american`, `dateFormat=iso`, `includeLinks=true` and
  `includeSids=true`. `markets` must be documented NCAAB base keys.
* **Events.** The `eventId` must come from an events listing already acquired inside 2026-27. The call must fall
  within `ODDS_PROP_WINDOW_HOURS` (36) before the listed commence time, never after it.
* **Cost control.** Each paid call is costed before it is made, with an upper bound of markets requested × 1 region
  (12 credits). It is checked against a per-stage budget (60 credits, 20 requests) and a 500-credit quota reserve.
  The account quota must already be known, from the free listing, before any paid call.
* **Usage headers.** `x-requests-remaining`, `x-requests-used` and `x-requests-last` are logged for every call.
* **Storage.** Payloads are cached under `data/prod/cache/raw/the-odds-api/`, and the request ledger is
  `data/prod/cache/odds_production_requests.jsonl`.
* **Credential.** `ODDS_API_KEY` travels only in the request query. It is never in the ledger, cache, metadata or
  exceptions; network errors are reduced to their type name.
* **Collections.** `odds_feed.collect` writes one `load_collected`-format file per event under
  `data/prod/odds/collections/`, carrying the payload's own `received_at`. A daily live run loads the latest
  collection per event received by `prediction_as_of`.

### Canonical quote schema (`prod/quotes.py` `COLUMNS`)

* **Identity and source:** event id, bookmaker key (lowercased) and title, player description, market key and stat.
* **Quote:** side (OVER/UNDER), line, American price and decimal price.
* **Timestamps:** market `last_update` (the documented timestamp), bookmaker `last_update` (kept if present),
  `quote_available_at`, and `collector_received_at`.
* **Deep link:** taken from the outcome, else the market, else the bookmaker.
* **Validity:** a quote is invalid if it has no player, a non-OVER/UNDER side, a line that is not a non-negative
  multiple of 0.5, a non-positive price, or a market update later than receipt.
* **Matching:** strict, unchanged (`prod/matching.py`); nothing is fuzzy.
* **Best price:** the highest decimal price among VALID, matched, fresh quotes, with ties broken by sportsbook key.

### Quote readiness states (replace `QUOTE_COVERAGE_UNVERIFIED`)

| Code | Level | When |
|---|---|---|
| `QUOTE_INTEGRATION_UNVALIDATED` | run (live) | no PASS record of the first real-event validation at prediction time |
| `ODDS_API_FAILURE` | run (live) | the latest Odds API acquisition by prediction time failed |
| `ODDS_EVENT_NOT_YET_LISTED` | game | no listed event resolves to the game, or no listing covers its tip |
| `QUOTE_IDENTITY_UNRESOLVED` | game / row | a listed event near the tip shares a team but does not resolve strictly; or the market is posted, the player has no quote, and quotes in that market did not resolve to a canonical player |
| `PLAYER_PROP_MARKET_NOT_YET_POSTED` | game | the event is listed but no documented player-prop market is posted |
| `QUOTE_STALE` | game | every identity-resolved prop quote is older than 20 min (row level: `STALE_QUOTE`) |
| `MARKET_NOT_OFFERED_FOR_EVENT` | row | the event has props, but not this market |

The retired code `QUOTE_COVERAGE_UNVERIFIED` is unknown to the evaluator, so if it ever appears it still blocks.

### First real-event validation (implementation validation, not capability discovery)

```
.venv/bin/python -m cbb_platform.prod.odds_feed mapping                    # the table above
.venv/bin/python -m cbb_platform.prod.odds_feed events [--hours 336]       # free listing + strict event matching
.venv/bin/python -m cbb_platform.prod.odds_feed validate --event <eventId> # ONE paid request (<= 12 credits)
```

`validate` runs only for a listed event that resolves strictly to a 2026-27 slate game and is inside the prop window.
It makes one event-odds request and runs the full shadow pipeline for that game into a scratch ledger. It then checks:

* expected markets were returned (each documented key is recorded as RETURNED or `MARKET_NOT_OFFERED_FOR_EVENT`);
* bookmaker keys and titles normalize;
* player identities resolve, with unmatched names listed;
* lines, sides and prices parse;
* timestamps are usable;
* best-price selection is correct;
* no priced line or price lacks a sportsbook quote, and no unsupported key was returned.

Only a full PASS writes `data/prod/odds/integration_validation.json`. That record is what removes
`QUOTE_INTEGRATION_UNVALIDATED`.

Live status on 2026-10-21:
* Two free events listings returned 0 NCAAB events (cost 0; quota remaining 4,937,648).
* A live-format run of Florida–Miami, Purdue–Gonzaga and BYU–Ohio State (scratch ledger) scored all 3 games. It is
  BLOCKED only by `QUOTE_INTEGRATION_UNVALIDATED` (run) and `ODDS_EVENT_NOT_YET_LISTED` (each game).
