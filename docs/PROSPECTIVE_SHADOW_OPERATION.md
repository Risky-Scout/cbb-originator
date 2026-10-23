# CBB 2026-27 prospective shadow operation

Status: **SHADOW_ONLY**. There is no public deployment, no wagering and no model change. The historical PTS repair
campaign is closed (`modeling/player_props_v2/PTS_REPAIR_CAMPAIGN_CLOSEOUT.md`).

| item | value |
|---|---|
| Branch | `release/cbb-v2-prospective-shadow`, cut from `35d8705`. It carries the shadow integration, the CBBD live feed, the Odds API feed, the side monitor and the root-cause audit, and no challenger code. |
| Model | frozen JM1_FINAL `e1353e5`. The four artifacts are hash-verified on every tick; a mismatch BLOCKS the tick before any network call. |
| Cutoff | `TIP_MINUS_1H`, unchanged |
| Command | `python -m cbb_platform.prod.shadow_tick` |
| Evidence | private orphan branch `ops/cbb-2026-27-shadow-state`, never merged |

## Lineage audit

The commit history is linear:

`e1353e5 → 780673f (shadow) → 5c370b2 (CBBD feed) → f4d5829 (Odds API) → 8d324fb / 11cf6fc (directional audit + side monitor) → 35d8705 (root cause, diagnostic)`

After that come the rejected research commits: R1 (`3e9e52c..e4b6e3d`), R2 (`0126280..49d1a99`), constrained selection
(`9efecf3, 734bb25`) and the calibration audit (`db54556, 16cdaf9`). `35d8705` is the latest clean descendant: its
only addition over `11cf6fc` is a read-only audit module. Nothing from the research commits was cherry-picked.

## One tick

Every step is gated by a due test on durable state, so a re-run with no new information makes no network call, no
prediction, no quote, no settlement, no state version and no commit. Both properties are verified live (2026-10-23)
and in tests.

1. **Frozen-artifact hash gate.**
2. **Last-known-good state**, restored from the encrypted working set on a fresh runner.
3. **CBBD sync, if due.** It is due when:
   * there is no state yet;
   * the routine refresh is due (every 6 h, which equals the schedule TTL);
   * an imminent scoring window (cutoff within lead + 60 min) needs a team's knowable but un-ingested prior result.

   Every sync force-refreshes the schedule windows of games that became knowable in the last 24 h.
4. **Free Odds API events listing, if due.** This happens every 6 h while a D-I game is within 14 days, and again live
   at every scoring tick, which also gives a known quota.
5. **First real-event validation.** It is the existing `odds_feed.validate`: ONE paid request, at most about 12 credits.
   * It runs at the first scoring tick that has a strictly matched event inside the 36 h prop window. That is the
     game's own T-60, when props are most likely posted.
   * PASS writes the immutable marker `markers/QUOTE_INTEGRATION_VALIDATED.json`, and validation never runs again.
   * FAIL is persisted, quote collection stays BLOCKED, and no other event is queried. Only an explicit architect
     action (`--allow-revalidation`) permits another attempt.
   * A payload with no player-prop market at all is `INCONCLUSIVE_NO_PLAYER_PROPS_POSTED`: nothing was posted yet. It
     may retry at a later eligible event, at most 3 times per day.
6. **T-60 scoring.** A game is scored at the first tick in `[cutoff − 50 min, cutoff)` (`SCORE_LEAD_MINUTES`).
   * First, ONE quote snapshot per matched event (only after PASS; base NCAAB markets only; existing per-stage budget
     of 60 credits / 20 requests).
   * Then ONE `run.daily` call, unchanged. It builds ONE 20,000-draw joint bank per game, and every stat, combo, line,
     side and book reads from it.
   * Every record carries `scheduled_tip`, `target_cutoff`, `prediction_as_of`, `executed_at` and
     `timing_deviation_seconds`. Quotes are collected before `prediction_as_of`, so none is post-cutoff.
7. **Missed cutoffs.** A D-I game whose cutoff passed with no decision is recorded as `MISSED_PREDICTION_CUTOFF`; a
   game whose only attempts produced no bank is `NOT_SCORED_AT_CUTOFF`. No retrospective forecast is ever made.
8. **Settlement.** It runs once per game, after the final box is in the state; a cancelled or unlisted game is voided
   48 h after tip.
   * Settlements are new hash-chained records. A prediction is never mutated.
   * Outcomes are OVER / UNDER / PUSH / NO_ACTION. A DNP is NO_ACTION, never a zero.
9. **Monitoring and reports.** Daily reports cover each ET date at D+1 12:00Z; the cumulative report is season to
   date. See the list below.
10. **`DUE.json` and persistence.** The tick writes `DUE.json`, which has no wall-clock field. On GitHub it then
    uploads the encrypted working set and pushes the evidence commit.

**First live completed game.** When the first real final game is ingested, the tick writes the immutable marker
`FIRST_LIVE_COMPLETED_GAME_INGESTION` (PASS / FAIL). The marker checks:
* game identity and final status;
* both team boxes and the player box;
* substitutions;
* cache and request ledger;
* the atomic state update;
* future-game chronology.

On FAIL, the state pointer returns to the previous version and completed-game ingestion halts until architect review.

## Durable evidence (`cbb_platform/prod/ops_store.py`, `cbb_platform/ops_persistence.py`)

**Evidence tree (git, private ops branch).** Contents:
* `OPERATION.json`, `DUE.json`, `WORKING_SET.json`;
* `CHAIN.jsonl`, a hash chain over sealed tick folders;
* `markers/`;
* `ledgers/`: the CBBD and Odds API request ledgers, the sync log, state-update failures and the tick log;
* `state_manifests/`;
* `registry/`: one decision per game, quote snapshots, settlements;
* `ticks/<id>/`:
  * the full shadow ledger and side monitor, hash-chained and gzipped;
  * the normalized quote snapshot;
  * compact Parquet: projections, quoted predictions, quote sides, actuals and side settlements;
  * the settlement chain;
* `reports/`.

The runner uses a blobless, sparse checkout, so the gzipped full ledgers stay on the server and the tip tree stays
small.

**Working set (binary, private release `cbb-2026-27-shadow-ops`).**
* It holds the current state version plus the minimal provider cache that the TTL and snapshot logic reads.
* It is AES-256 encrypted with openssl and the `CBB_OPS_ENCRYPTION_KEY` secret.
* Each new archive is uploaded before the pointer moves. The last 4 are kept, and restore falls back to the previous
  archive and refuses otherwise.

**History extract.**
* `prod/history_extract.py` holds exactly the 15 frozen history relations the production path reads. It is 127 MB
  (sha256 in the tracked `history_extract_manifest.json`), against 8.8 GB for the full DB.
* It is relation-fingerprint-verified against the source.
* On 3 real 2026-27 games the production path is bit-identical with the full DB: the same 20,000-draw banks, model
  inputs and 1,392 projections. The sync/identity path is also identical
  (`docs/PROSPECTIVE_SHADOW_HISTORY_EXTRACT_EQUIVALENCE.json`).
* It contains Starter Pack- and CBBD-derived research-only data, so it is stored only encrypted and private.

**Guards.**
* No credential or key ever reaches either layer (tested).
* `cbb_platform/prod` keeps its no-shell / no-socket guarantee. Shelling out is confined to `ops_persistence.py`, and
  only to `git`, `gh` and `openssl` (tested).

## Operational fixes on this branch (no model effect)

* **Completed games, same-window box payloads.** A box payload cached when a window's first game finished was reused
  forever. That would have meant later games of the same window were never ingested. Reuse is now content-checked.
* **Completed games, budget.** Under the unchanged 20-request stage cap, the games whose requests all completed are
  ingested and the rest wait, instead of the whole sync failing. The season player file is requested first.
* **Cache TTL.** The TTL now runs from the last verification of an unchanged payload, not from its first receipt.
  Before this, every sync after a TTL lapse re-fetched the unchanged roster, portal, recruits and schedule windows
  (estimated about 45k extra calls a season).

## Monitoring (diagnostic only; `prod/prospective_monitor.py`)

Every figure carries its N. Means carry a game-clustered 95% CI, and no launch sample threshold is defined.

* **High-exposure PTS (`min_mu` ≥ 30).**
  * N; mean model vs actual; signed error; relative error.
  * Over-favored and Under-favored counts; P(over) and P(under).
  * Quoted-line Brier and log loss; model vs no-vig market.
  * `PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING` fires when the 95% CI of the signed error lies below 0.
* **Early season.** Team games ≤ 2 and the first 30 days, for PTS, AST, STL, TOV, PRA, PR and PA.
* **Current-season evidence buckets.** 0, (0,100], (100,300] and 300+ minutes (PTS).
* **Transfers and cold starts** (existing `cold_start_class`), for PTS, REB, STL, TOV and STOCKS.
* **Centers vs non-centers** (PTS).
* **Over/Under sides.** Uses the existing side monitor and its warnings.
* **Market benchmark.** Per book quoting both sides of the same line in the same snapshot, the normalized reciprocal
  decimal odds, averaged over books. It is compared only on identical game, player, market, line and cutoff.

Rows from games with data-integrity blockers (`STALE_STATE`, `MISSING_PLAYER_STATE`, `FUTURE_LEAKAGE`, …) are excluded
from model scores and counted separately. Warnings never trigger a correction, a refit or reopened research.

## Scheduler (ENABLED 2026-10-23; see `docs/SHADOW_AUTOMATION_ACTIVATION.md`, which also holds emergency manual recovery)

> **Superseded (2026-10-23)** by `docs/SHADOW_AUTOMATION_READINESS.md`. That report selects the `:12/:42` cadence
> (36 runs a day, 50-min lead, measured GitHub jitter) and records the blocker: the off-machine data licence is
> UNCLEAR. The original proposal below is kept as history.

**Where it can run.** Only GitHub-hosted Actions (`ubuntu-24.04`). No Mac self-hosted runner and no paid service.

**Why it is inert.** GitHub runs `schedule` and `workflow_dispatch` workflows only from the **default branch**. The
workflow `.github/workflows/cbb-shadow-tick.yml` (code checked out from `release/cbb-v2-prospective-shadow`) must be
placed on `main`, which this task did not authorize. The `schedule:` block is commented out pending architect review
of this report.

**Proposed cadence (UTC).**
* Every 20 min from 14:00 to 05:59 (`4,24,44 14-23,0-5 * * *`).
* Hourly from 06:00 to 13:59 (`34 6-13 * * *`).

That is 56 runs a day.

**Why it captures T-60.**
* Tips from 11:00 to 01:00 ET give cutoffs from 15:00 to 05:00 UTC.
* With a 40-min scoring lead, every cutoff has at least 2 scheduled attempts inside its window. One attempt can be
  late by up to about 20 min, and the game is still scored before its cutoff.
* The expected prediction is 10–40 min before the cutoff, recorded exactly. A miss is recorded, never back-filled.

**Expected jitter.** GitHub scheduled runs typically start 3–15 min late, worse near the top of the hour (the minute
offsets avoid :00). Under platform load runs can be delayed further or dropped, and those cases surface as
`MISSED_PREDICTION_CUTOFF`.

**Cost control.** The gate is cheap: two sparse checkouts and a standard-library script that reads `DUE.json`. Only
when work is due does the job install the locked environment and run the tick. A no-op run is about 15–25 s of
runtime, billed as 1 min.

| estimate (in season, per month) | value |
|---|---|
| gate runs | 56/day × 30 = 1,680 billed min (each job rounds up to 1 min) |
| heavy work (environment install about 1 min + restore + tick) | about 10 scoring ticks + 4 routine syncs + 1–3 result syncs per game day × about 2 min ≈ 30–35 min/day ≈ 900–1,050 min |
| **total** | **about 2,600–2,750 min/month** in season; about 1,900 on idle months |
| GitHub plan | Free (private): 2,000 included, so an in-season overage of about 600–750 min ≈ $5–6/month at $0.008/min, which needs a spending limit above $0 or Actions stop mid-month. Pro: 3,000 included, which fits. |
| lower-cost alternative | 30-min gates with a 50-min lead: about 2,100 min/month (1 attempt per cutoff window, so more jitter risk) |

**Odds API credits, full season.**
* Listings cost 0.
* Validation is one event, at most 12 credits.
* Snapshots are one per matched event at decision time, costing (markets returned) × 1 region, at most 12.
* The upper bound is about 5,500 D-I games × 12 = **66,000**. The planning range is about **7,000–22,000**, assuming
  25–50% of events carry props with 5–8 markets.
* The quota remaining is 4,934,558, on a key shared with other projects.

**CBBD request load.**
* Schedule: about 28 calls a day (7 windows every 6 h).
* Preseason files: 3 a day.
* Completed games: about 1 substitutions call per game, plus 2 box windows per game date, plus 1 season file per sync.
* The completed stage stays capped at **20 requests per sync stage (unchanged)**, so about 80–140 completed-game
  requests a day at the proposed cadence.
* That is roughly 12k–17k calls a season; the quota remaining is 105,184.
* **Busy days with more than about 100 final games exceed the existing cap.** Their results are ingested over several
  syncs, and affected next-game forecasts carry `STALE_STATE` until then. The in-season budget remains an architect
  decision; it was not raised here.

## Remaining decisions (architect / user)

1. Approve the cadence above and place `cbb-shadow-tick.yml` on the default branch (YAML only), then uncomment the
   schedule.
2. Approve encrypted, private off-machine storage of the research-only history extract and the working set. Then set
   the repository secrets `CBBD_API_KEY`, `ODDS_API_KEY` and `CBB_OPS_ENCRYPTION_KEY`, and run once
   `python -m cbb_platform.ops_persistence bootstrap`.
3. Set the in-season CBBD completed-stage budget.
4. Decide whether to raise the per-stage Odds API budget (60 credits / 20 requests). Clusters with more than about 5
   full-prop events at the same cutoff exceed it.
5. Confirm production adaptation P-2 (recruit identity), which is still pending from the live-feed phase.

Until 1–2 are done, the operation runs only on this Mac (`python -m cbb_platform.prod.shadow_tick --commit`, then
push the ops branch).
