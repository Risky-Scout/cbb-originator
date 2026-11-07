# CBB live daily lifecycle (2026-11-04)

> **SUPERSEDED IN PART (2026-11-07, Droplet deployment).** The installed layout is: immutable git-archive release
> `/opt/sports-models/cbb-originator/releases/<sha>` (+ root-owned `<sha>.sha256sums`, re-verified before every job)
> behind `current`; mutable state only under `/var/lib/sports-models/cbb-originator/{prod,walkforward,exports,locks}`
> (`CBB_PROD_ROOT` / `CBB_WF_ROOT` / `CBB_EXPORT_ROOT`); units `deploy/droplet/systemd/cbb-originator-{morning,
> refit-retry,availability@,quotes}`; shared heavy lock `/run/lock/sports-models/heavy.lock` (group
> `sports-models-heavy`, held by `nba-prop-daily.service` via an authorized drop-in and by the CBB walk-forward step
> only; busy for 30 min -> `DAILY_REFIT_STATUS = DEFERRED_SHARED_LOCK`, last-known-good kept, one 08:00 ET retry);
> heavy-unit limits `MemoryHigh=2800M` / `MemoryMax=3200M` / `MemorySwapMax=0` on the 8 GB host. The `.../app` checkout,
> bind mounts, `sports-model-heavy.lock` path and 12G/14G/13 GB values below are historical.


No model rebuild, refit outside the authorized walk-forward step, recalibration or threshold change. Frozen JM1_FINAL
artifacts unchanged. No WizardOfOdds publication, no wagering.

## Defects found and fixed

1. **Completed-game ingestion: unresolved player keys** (`prod/state.py`).
   - Box and substitution rows of players with no canonical identity (`canonical_player_id` NULL, e.g. non-D-I
     opponents) collapsed onto one NULL key and failed state validation, so no state was promoted.
   - They are now keyed by their source record: `coalesce(canonical_player_id, 'UNRESOLVED:' || player_game_key / source_athlete_id)`,
     as in `build_canonical`.
   - The fallback applies only when the source column exists. Resolved duplicates still fail.
2. **Completed-game ingestion: overlapping box windows** (`prod/sync.py`).
   - A date window cached earlier could carry an older provider version of a game that a later window returns
     corrected, and dedupe then failed closed. Observed in the GitHub ticks since 2026-11-03 04:15Z:
     `team_game_stats: conflicting CBBD records for key ('372661', '168')`.
   - Each game's box records are now taken whole from the most recently received payload. Records of one game are never
     mixed across payloads, and a conflict inside one response still fails closed.
3. **Odds discovery.**
   - Every GitHub tick's free events listing returned **HTTP 401**: the runner's `ODDS_API_KEY` secret is invalid or
     stale; the local credential works.
   - With no listing, coverage recorded `ODDS_EVENT_NOT_YET_LISTED`; the run-level `ODDS_API_FAILURE` was also recorded.
   - Separately, strict team matching never resolved provider spellings such as "Wichita St", "CSU Northridge" or
     "Loyola (Chi)". The curated, committed explicit map `prod/odds_team_map.csv` (matching step 1, never fuzzy) now
     resolves them.
4. **PMF persistence** (`prod/pmf_evidence.py`).
   - `market_pmfs.parquet` lived only in the ephemeral run directory.
   - Every official tick now writes `ticks/<tick>/pmfs.parquet`, and every forecast snapshot writes
     `forecasts/<D>/<snapshot>/pmfs.parquet`.
   - Both are written once and immutable, carrying the receipt id, game, player, team, stat, prediction_as_of,
     model_version, outcome, probability, mean and run id.

## Lifecycle (`prod/lifecycle.py`, America/New_York)

- **07:00 morning.**
  1. Shadow tick: sync and settle.
  2. Prior-day Grades.
  3. Walk-forward update (existing gates; the last-known-good version is retained).
  4. INITIAL line-independent forecast of today's full eligible slate.
  5. Distributions; Boards when priced receipts exist.
- **10:00 / 13:00 / 16:00 / 18:00 refresh.**
  - RotoWire availability refresh. It is `BLOCKED_NO_KEY` without `ROTOWIRE_API_KEY` or the smoke PASS.
  - Only games with an availability change and a future cutoff are re-forecast, into a new immutable snapshot.
  - Exports are regenerated.
- **Official priced prediction.** Per game, the shadow tick's single run inside `[cutoff - 50 min, cutoff)`. A later
  refresh never changes it.
- **Droplet units.** Prepared, not installed: `deploy/droplet/lifecycle/`, run through `cbb-originator-run
  lifecycle-morning | lifecycle-refresh HH:MM`.

## 2026-11-04 local live run (evidence under `data/prod/live/`, gitignored)

- **State.** `S20261104T175724Z-fffec130124d`: 211 of 213 completed games ingested, including 28 of 29 from 11-03 ET.
- **Walk-forward.** 2026-11-04: `NO_NEW_DATA`. No state existed at the 06:00 ET cutoff, so the last-good
  `cbb-jm1-2026-27-20261101` (opening baseline, frozen artifact hashes) stays ACTIVE.
- **Forecast.** Snapshot `F20261104T180532Z-b808ca`: 10 games, 260 players, 3,120 stat predictions and 3,120 PMFs.
- **Distributions.** `data/prod/live/exports/2026-11-04/CBB_Distributions_11_04_2026.xlsx`: 269,368 rows, maximum
  mass error 1.15e-14, no negative values.
- **Quotes.** The validation at 13:07 ET returned `INCONCLUSIVE_NO_PLAYER_PROPS_POSTED` (0 credits).
- **RotoWire.** `BLOCKED_NO_KEY`.
