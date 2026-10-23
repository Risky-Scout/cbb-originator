# CBB 2026-27 shadow: off-Mac automation readiness

**Date:** 2026-10-23.

| item | value |
|---|---|
| Branch | `release/cbb-v2-prospective-shadow` |
| Model | frozen JM1_FINAL `e1353e5`. The four artifact SHA256 values were re-verified and are unchanged. |
| Readiness ceiling | SHADOW_ONLY |
| Public launch | HOLD |
| Wagering | disabled |

> **Verdict: automation is NOT enabled.**
>
> The blocker is the licence: `OFF_MACHINE_DATA_LICENSE = UNCLEAR`. Every off-Mac path (a GitHub runner or any other
> host) needs the Starter Pack-derived history and working set off this machine. The local licence does not clearly
> permit that.
>
> Because of that, none of these steps ran:
> * secrets setup;
> * bootstrap;
> * restore test;
> * unattended runs;
> * the merge to main.
>
> Everything else is analysed, and the in-limit mitigations are implemented. Enabling is a short checklist once
> permission exists (end of this document).

## 1. Licence / provenance (gate A)

**Starter Pack `LICENSE`** (`../CBB Starter Pack/starter_pack/LICENSE`, Rad Sports Analytics LLC, © 2026):

* **Grant.** *Personal, non-commercial use only by the original purchaser*. The purchaser MAY modify the code or the
  data *for private use*.
* **Prohibited:**
  * redistributing or publishing it, in part or whole, without written permission;
  * sharing access to the datasets with others;
  * incorporating it into a commercial or paid product;
  * uploading it to a *public* repository (GitHub, Google Drive, Kaggle).
* **Silent on:**
  * private, encrypted storage on third-party cloud infrastructure (a private GitHub release asset);
  * processing on third-party hosted runners;
  * whether this project (a WizardOfOdds pricing originator) counts as "non-commercial".

**Project policy.**
* `COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md` classes the Starter Pack as RESEARCH_ONLY and the CBBD API
  as UNVERIFIED.
* `OFF_MACHINE_BACKUP_PLAN.md` lists "private storage only" for the Starter Pack, but its bulk-data destination
  decision (§3) was never made.

**CBBD API terms:** not present locally.

**Answers.**
* A. Is private cloud storage clearly permitted? **No.** It is not addressed.
* B. Is use on a private GitHub-hosted runner clearly permitted? **No.** It is not addressed.
* C. Is redistribution prohibited? **Yes.**
* D. Does the extract contain anything beyond what production needs? **No.** See §2.

**Result: `OFF_MACHINE_DATA_LICENSE = UNCLEAR`.**

**Missing permission.** We need written confirmation from the licensor (admin@collegefootballdata.com), or an
organizational licence, that the purchaser may:
1. store an AES-256 encrypted, derived subset in a private GitHub release;
2. process it on GitHub-hosted runners for a non-public shadow study;
3. treat this use as within "personal, non-commercial".

Repository privacy was **not** taken as permission. The record is `docs/OFF_MACHINE_DATA_LICENSE.json`.

**Now enforced in code.** `ops_persistence.license_gate()` refuses every off-machine asset store and `bootstrap`
unless that record says `PERMITTED`. A tick run with `--restore` or `--persist` fails closed with `BLOCKED_LICENSE`.

**This also blocks a cheaper "no history upload" design.** The working set's state DB itself carries copied history
rows (players, the ID map and the 2025-26 rosters, seeded from the frozen DB). So no off-Mac design avoids the question
without a historical rebuild from non-Starter-Pack sources, which is not authorized.

## 2. Reduced history extract (gate B)

**File:** `data/prod/history/history_extract.duckdb`
* size: 126,627,840 bytes;
* sha256: `0338b32d5228697862e599e7d545cd81718a5750fadfb51f010dbef4bbf0ca71`;
* one file holding 15 relations (11 with rows, 4 schema-only);
* manifest: `cbb_platform/prod/history_extract_manifest.json`.

| relation | rows | provenance (`source_system` / file) | read by | class |
|---|---:|---|---|---|
| flagship.player_game_outcomes (2014–2026) | 1,555,270 | Starter Pack files 92%; CBBD API cache 8% (2025-26) | frozen features | REQUIRED_FOR_SCORING |
| flagship.team_game_outcomes (2014–2026) | 153,597 | Starter Pack 100% | frozen features | REQUIRED_FOR_SCORING |
| flagship.played_games (2014–2026) | 76,886 | Starter Pack games.csv about 92%; CBBD API about 8% | frozen features | REQUIRED_FOR_SCORING |
| flagship.rosters | 208,281 | CBBD API | features (union); 2025-26 portal seed | SCORING + SYNC |
| core.transfers | 5,102 | CBBD API | features (union) | REQUIRED_FOR_SCORING |
| core.recruits | 10,701 | CBBD API | features (union) | REQUIRED_FOR_SCORING |
| core.team_seasons | 27,427 | Starter Pack games.csv | features (union) | REQUIRED_FOR_SCORING |
| core.teams | 1,535 | Starter Pack teams.csv + 18 CBBD | features, sync names | SCORING + SYNC |
| core.players | 94,034 | CBBD API 57%, Starter Pack 43% | features, sync seed, names | SCORING + SYNC + IDENTITY |
| metadata.identity_link_quarantine | 985 | project QA | features (union) | REQUIRED_FOR_IDENTITY |
| metadata.player_id_map (provider CBBD) | 94,033 | project anchor map | sync seed | REQUIRED_FOR_IDENTITY |
| flagship.substitutions / plays / shots_trusted / on_floor_trusted | 0 | schema only | features (schema) | REQUIRED (empty) |

**Minimality: PASS.**
* The extract holds exactly the relations the production readers select, with the same row filters.
* It holds no development or QA evidence.
* **NOT_REQUIRED:** none at the relation level.
* Two relations (core.team_seasons back to 1951, and all roster seasons) are read *whole* by the frozen union. Trimming
  their rows would void the equivalence proof, so it was not done.

**Equivalence was reused, not rerun.** The evidence is `docs/PROSPECTIVE_SHADOW_HISTORY_EXTRACT_EQUIVALENCE.json`. It
covers the same extract sha and is PASS:
* 3 real games;
* feature and canonical counts, model-input sha, projections sha, every 20,000-draw bank sha and all 1,392 predictions
  identical to the 8.8 GB DB;
* sync path relation hashes identical.

## 3. Encryption / persistence (gate C): code audit PASS; nothing uploaded

**Design.** It is the existing minimal mechanism, with no new service.
* **Compact evidence** lives on the private orphan branch `ops/cbb-2026-27-shadow-state`. It is hash-chained and
  append-only.
* **Binary assets** (history extract and working set) are encrypted with AES-256-CBC (openssl, pbkdf2 200k
  iterations, salted) before upload. They go to one private pre-release asset store, `cbb-2026-27-shadow-ops`.

**Audit points.**
* **Key handling.** The key comes only from `CBB_OPS_ENCRYPTION_KEY`. It reaches openssl as `-pass env:NAME`, never on
  argv; this is now tested. No key is in source or git history (scanned).
* **Integrity.** AES-CBC is not authenticated, so integrity rests on sha256 checks after decryption:
  * the history sha is pinned in the tracked manifest;
  * the working-set sha is pinned in the git-tracked `WORKING_SET.json`.
* **Failed restores leave the last-good copy in place.**
  * History is decrypted to a temporary file and verified, then put in place with `os.replace`. A mismatch deletes the
    temporary file and leaves the last-good file untouched; this is tested.
  * The working set falls back to one of the 3 previous archives (4 are kept), and unsafe tar members are refused.
* **No leftovers.** Encrypted temporaries are removed in `finally` blocks.
* **No secrets in logs.** Error text is the truncated stderr of gh or git and never includes the key; this is tested.

## 4. Secrets (gate D): NOT configured (by design)

* `CBBD_API_KEY` and `ODDS_API_KEY` exist in the local environment (checked without printing them).
* `CBB_OPS_ENCRYPTION_KEY` does not exist yet. None was generated, because there is nothing to encrypt until the
  licence allows an upload.
* No key value appears in the repository history or the ops branch.
* Target `Risky-Scout/cbb-originator` is **PRIVATE**. Actions is enabled, and the repository has no secrets.
* The secrets were not set, because the licence precondition failed.

## 5. Scheduler options

| option | status |
|---|---|
| A. GitHub-hosted Actions (ubuntu-24.04), this private repo | Available. Selected **if** the licence clears. Measured gate job 6 s wall (1.9 s of steps), billed 1 min. Environment setup (checkout + cached pip) 17 s (CI run 39884866684). |
| B. Existing always-on Linux / Wizard host | **Not suitable.** The Wizard server is a public web host reached by SSH deploy (NCAAF template), not a build host. Running private jobs and licensed data there risks the public site and is the same licence question. |
| C. Existing paid scheduler | None found. Other repos use GitHub-hosted Actions from the **same account minute pool**: `ncaaf-originator-2026` used ~1,930 private minutes in the last 30 days (now disabled), `syndicate-cbb` ~70 and the old NCAA repo ~35. The plan tier could not be read (the token lacks the `user` scope). |
| Mac self-hosted runner | Excluded by directive. |

## 6. T-60 timing (real 2026-27 schedule, state S20261023T010605Z)

**Schedule.**
* 5,424 D-I vs D-I games (6,015 including non-D-I opponents), 2026-11-01 to 2027-03-08. The postseason is not scheduled
  yet.
* **Exact-tip cutoffs (2,970 games):**
  * 80% fall at :00 and 19% at :30;
  * they run from 13:00 to 04:00 UTC, peaking at 23:00 UTC (749 games).
* **TBD-tip games (2,454, 45%)** use the ET game-date-start cutoff, 05:00 UTC. They form clusters of up to 85 games on
  Saturdays.

**Jitter.** Measured on this account's own scheduled runs: 78 runs of `ncaaf-originator-2026`, on :00/:30 slots. The
queue delay was:

| median | p75 | p90 | p95 | max |
|---:|---:|---:|---:|---:|
| 9.7 min | 13.1 | 16.1 | 20.0 | 28.5 |

One run in 78 waited 525 min for a runner, so it was treated as dropped (1.3%).

**Monte Carlo:** 20,000 draws per cutoff class, with i.i.d. empirical delays and the 1.3% drop rate.

| cadence | runs/day (in season) | lead | ≥ 2 nominal slots | P(≥ 1 in-window exec) | P(≥ 2) | mean exec before cutoff |
|---|---:|---:|---:|---:|---:|---:|
| C0 current proposal: `4,24,44` 14–05Z + hourly | 56 | 40 | 99.9% | 99.82% | 87.1% | 27.0 min |
| C1 20-min tuned | 54 | 40 | 100% | 99.88% | 90.2% | 28.1 min |
| **C2 selected: `12,42` 12–04Z + 06:34, 09:34** | **36** | **50** | **99.9%** | **99.9%** | **90.9%** | **37.4 min** |
| C2b 30-min, 60 lead | 37 | 60 | 100% | 99.9% | 91.8% | 38.5 min |
| C3 15-min | 71 | 35 | 100% | 99.97% | 96.7% | 27.8 min |

**Why C2.** It costs 36% fewer runs than C0, and its coverage is equal or better. Every :00 and :30 cutoff has slots at
−48 and −18 min.
* **Execution window.** The earliest execution is about 45 min before the cutoff (−48 plus at least 3 min of delay). The
  latest valid execution is just before the cutoff.
* **Typical execution.** The first in-window execution is about 37 min before the cutoff.
* **Maximum plausible delay.** The observed maximum (28.5 min) still lands the −48 slot in the window.
* **Coverage.** ≥ 99.9% of D-I games are covered. The rest are 3 exact cutoffs outside the cadence hours (07, 10 UTC)
  and odd-minute cutoffs.
* **What the shift costs.** Predictions are made about 10 min earlier than with C0. The cutoff stays TIP_MINUS_1H.
  Every record keeps `prediction_as_of` and `timing_deviation_seconds`, a miss is MISSED_PREDICTION_CUTOFF, and nothing
  is back-filled.

**Changes made.**
* `SCORE_LEAD_MINUTES` 40 → 50.
* The commented crons in `cbb-shadow-tick.yml` now carry C2, restricted to Nov–Mar plus Apr 1–8. Outside that window a
  6-hourly refresh runs (4/day).
* A test asserts that the YAML and the lead agree.

## 7. No-op cost and runner minutes

**No-op runs are cheap.**
* A no-op run is two sparse/blobless checkouts plus the standard-library `due_check.py`. It loads no store, simulates
  nothing and makes no paid call.
* Measured cost:
  * locally, 0.02 s;
  * on GitHub, a 6 s job and 1.9 s of steps (CI run 39895829485, job `noop-gate-timing`);
  * billed as 1 minute, because GitHub rounds each job up to the minute.
* **A heavy run** (when DUE.json says work is due) adds a cached install (17 s measured), restore, sync, and scoring
  (about 6 s per game locally).

**Heavy runs per season** come from the analytic replay of every gate against the real schedule: **1,578 for C2**
(about 12/day) and 1,679 for C0.

| per in-season month | C2 selected | C0 current |
|---|---:|---:|
| gate jobs | 1,080 | 1,680 |
| no-op billed minutes | ~710 | ~1,290 |
| heavy billed minutes: expected (1.5 min base + 8 s/game) | ~720 | ~760 |
| heavy billed minutes: conservative (2.5 min base + 15 s/game) | ~1,240 | ~1,290 |
| **total: expected / conservative** | **~1,430 / ~1,950** | **~2,050 / ~2,580** |

Off-season or preseason months run about 120 gate jobs, about 180–300 min.

**Overage.**
* **This workflow alone:** C2 fits a 2,000-min Free allowance in expectation and is at the limit conservatively.
* **The pool is shared.** The account used ~2,050 private minutes in the last 30 days, mostly `ncaaf-originator-2026`,
  now disabled. If that repo resumes, the C2 minutes become overage: about 1,400–1,950 min/month, roughly $8–16 at
  $0.006–0.008/min. A spending limit above $0 would be needed, or Actions stops mid-month.
* The plan tier is unverified.

## 8. CBBD completed-game throughput (cap 20 requests per sync stage, unchanged)

**Method.** An analytic replay, with no API calls, of every C2 gate over the season (median delay). It models:
* routine syncs every 6 h;
* result-driven syncs (imminent scoring window, 20-min retry);
* the real request plan (season file, 2 box windows per date, 1 substitutions call per game), including cache reuse.

| ordering | STALE_STATE forecasts | ingest lag p50 / p95 / max | worst day |
|---|---:|---|---|
| by game id (before) | **25 / 5,424 (0.46%)** | 6.4 h / 29.4 h / 74 h | 2026-11-05 (74 h) |
| **by the team's next game (implemented)** | **0** | 7.4 h / 44.9 h / 139 h | 2026-11-03 (139 h; only games whose teams do not play soon, so it delays settlement, not forecasts) |

* Peak load is about 150–180 finals per ET date (p95 147). The first week of November and Saturdays in January–February
  are the heaviest.
* **CBBD_CAP_CHANGE_REQUIRED = FALSE.** Mitigation B is now implemented: `sync.completion_urgency` changes the order
  only, so every request is still made and the cap is unchanged.
* The replay with C0 gives 9 stale forecasts by game-id order and 0 with urgency order.

## 9. Odds API throughput (cap 60 credits / 20 requests per stage, unchanged)

* All games sharing a cutoff are scored, and quoted, at the same tick. Per scoring tick there are:
  * 2 D-I games at the median;
  * 9 at p90;
  * at most 85 (TBD cluster) and 30 exact-tip games.
* **Unknown inputs.** Prop coverage and the markets per event are unmeasured until the first real validation.
* **Planning assumptions.** 25% or 50% of exact-tip events carry props, versus 5% of TBD events, with 5, 8 or 12
  markets per event.

| assumption | events/tick allowed by the cap | snapshots lost | ticks over cap | credits/season |
|---|---:|---:|---:|---:|
| 25% × 5 markets (expected) | 12 | 0 | 0 | ~4,300 |
| 50% × 8 markets | 7 | ~29 (1.8%) | ~10 | ~12,600 |
| 50% × 12 markets (conservative) | 5 | ~76 (4.7%) | ~30 | ~18,400 |

* **Result.** The cap is sufficient at expected coverage. With heavy coverage it loses ≤ 5% of snapshots, all on peak
  Saturday clusters.
* **Changed now.** A budget stop records **every** remaining due event as `BUDGET_EXHAUSTED`, so the evidence has no
  silent gap.
* **Smallest further change (recommended, not implemented).** It needs an architect ruling because it touches the
  "snapshot at decision time" rule. Score at the first tick as now, then take the snapshot for events left out by the
  budget at the *second* in-window attempt, ~30 min later and still pre-cutoff, labelled as such. That attempt exists
  for about 91% of cutoffs. The order would be deterministic: exact-tip events first.

## 10. Full-season resource forecast (2026-11-01 to 2027-03-08 as scheduled; the postseason adds ~10–15%)

| resource | expected | conservative upper |
|---|---|---|
| runner minutes (C2) | ~1,430/in-season month (~7,000–8,000 for Nov–Apr) | ~1,950/month |
| Odds API credits | ~4,300–12,600 (+12 validation) | ~18,400 (the theoretical bound of every game × 12 = 66,000 is not a planning value) |
| CBBD calls | ~11,000 (replay 10.7k + preseason files) | ~17,000 (forced window refreshes, retries); quota 105,184 |
| ops-state git growth | ~0.6 GB/season (~240 B per prediction, ~110 KB per scored game, 5,424 games) | ~1 GB. The runner checkout is blobless/sparse, so tick cost is unaffected. Moving ops state to its own private repo is an option if repository size matters. |
| encrypted private release storage | 127 MB extract + 4 working-set archives (~10–16 MB now, est. 30–80 MB in season) ≈ 170–450 MB | ~500 MB |

## 11. Ops branch audit: PASS

`ops/cbb-2026-27-shadow-state` at `1c53785`:
* 2 commits;
* 10 files;
* **62 KB of blobs across its whole history**.

Contents:
* JSON request ledgers holding metadata, request keys and response hashes, not payloads;
* the sync and tick logs;
* one state manifest;
* reports, README, OPERATION.json and DUE.json.

The scan found:
* no key value (checked against the real local keys);
* no secret-like strings;
* no raw provider payloads;
* no Starter Pack data;
* no large binary.

The extract is not, and will not be, stored in this branch.

## 12. Default branch (main)

* `origin/main` = `e1353e5`, with no `.github`. The release branch descends from it, 58 files ahead.
* **The minimum main change is one file**, `.github/workflows/cbb-shadow-tick.yml`. The workflow checks out its code
  from `release/cbb-v2-prospective-shadow`.
* That commit is prepared on `integration/main-shadow-tick-workflow` @ `de08de5` (pushed, **not merged**, schedule
  still commented). The diff against main is 1 file, +113 lines:
  * FROZEN MODEL ARTIFACTS CHANGED = FALSE;
  * PUBLIC DEPLOYMENT ADDED = FALSE (no SSH, FTP or Wizard step; `contents: write` covers only the ops branch and the
    release assets);
  * WAGERING ADDED = FALSE.
* **Fixed.** Evidence now records the commit of the checked-out *release* code. In a scheduled run `GITHUB_SHA` would
  name main's commit instead.

## 13. Steps 16–19: not run (gate A failed)

* **Bootstrap:** not run. `license_gate` refuses it.
* **Clean off-Mac restore:** not run.
* **First unattended dispatch:** not run.
* **Second idempotence run:** not run.

The same mechanisms are covered by fixture tests: fresh-root restore, archive fallback, a tampered restore refused, and
the idempotent re-tick. MAC_REQUIRED stays TRUE.

## 14. Tests

113 focused tests pass: the 105 existing plus 8 new. The new tests cover:
* the licence gate (stores and bootstrap);
* bootstrap's key requirement;
* the key never on argv and never in error text;
* the restore hash check and last-good copy;
* urgency ordering (same request multiset);
* the quote-budget records;
* cadence / lead agreement;
* the source commit.

GitHub CI is green, including `noop-gate-timing`. No historical suite was run; no frozen predictive code changed.

## 15. Enable checklist (once permission exists)

1. Record the written permission in `docs/OFF_MACHINE_DATA_LICENSE.json`: `status: PERMITTED`, `decided_by`, and the
   evidence. This is an architect / user action.
2. Generate `CBB_OPS_ENCRYPTION_KEY` locally (e.g. `openssl rand -base64 48`) without displaying it. Then set the three
   secrets with `gh secret set NAME -R Risky-Scout/cbb-originator` reading from the environment, and verify the names
   only with `gh secret list`. Keep an offline copy of the encryption key: without it the assets cannot be recovered.
3. Run `python -m cbb_platform.ops_persistence bootstrap` once.
4. Merge `integration/main-shadow-tick-workflow` into main (one file).
5. Dispatch once (`gh workflow run "CBB shadow tick" -f force=true`) and verify restore, hashes, a promoted or unchanged
   state, and the ops commit.
6. Dispatch again and expect no new prediction, quote, settlement or state version.
7. Uncomment the C2 schedule on main and confirm the workflow is active.
8. Set an Actions spending limit if the account's other private repos resume.
