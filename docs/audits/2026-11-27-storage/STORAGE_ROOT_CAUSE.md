# Droplet storage growth: root cause (2026-11-27)

**Method: read-only, metadata only.**
* Sources:
  * `du` (allocated blocks and apparent size) and `stat`;
  * the manifests' recorded relation hashes;
  * 46 existing retention footprint reports (`prod/ops/retention/reports/*-storage.json`, 11-16 → 11-27).
* Nothing was deleted, modified or hashed.
* Other tenants' trees (`/srv/*`) were not scanned. Their share is host used minus the CBB footprint.

## 1. Was "1 GB/day" measured?

No. It was an estimate made during Stage 06/07. The measured figures are below. Both readings were taken right after the 11:45 ET retention pass, at the same time of day.

| 16:4xZ after retention | host used GB | CBB footprint GB | non-CBB GB |
|---|---|---|---|
| 2026-11-16 | 83.71 | 7.34 | 76.37 |
| 2026-11-27 | 120.61 | 18.36 | 102.24 |
| change, 11 days | **+36.90 (3.35 GB/day)** | +11.02 | **+25.87 (2.35 GB/day)** |

The +11.02 GB of CBB change splits into three parts:

* **Persistent, +6.56 GB (0.60 GB/day):**
  * state versions +3.63
  * ops +1.33
  * audit and evidence +0.81 (one-off)
  * qa +0.33 (one-off)
  * cache +0.35
  * exports and walkforward +0.11
* **Run scratch held at the second reading, +3.39 GB.** This is not growth: the scratch inside the 18–24 h window cycles daily between about 1 and 17 GB.
* **Two UNVERIFIED runs, +1.07 GB (one-off).**

**Attribution of the host growth:**
* CBB persistent: about 19%, of which about 0.50 GB/day recurs.
* Run-scratch timing: about 9%. This is not growth.
* Outside CBB: **about 70%**.

Logical size matches allocated size within 1%. Sparse files and block overhead are not a factor.

## 2. CBB census (allocated, 2026-11-27 about 22:45Z)

Total: **26.4 GB**. That is 25.4 GB under `/var/lib/sports-models/cbb-originator` plus 1.0 GB under `/opt/sports-models/cbb-originator`. About 11.9 GB of it is transient run scratch; the persistent footprint is about 14.5 GB.

| directory | allocated GB | daily growth MB | root cause | class | required GB | safely reclaimable GB | permanent correction |
|---|---|---|---|---|---|---|---|
| `prod/runs` feature scratch | 12.4 at a daily peak (cycles about 1–17) | 0 persistent; about 11,900 intraday swing | each run writes `canonical_asof` + `features_asof` DuckDB, about 540 MB. `prod/retention.py` deletes it 18–24 h after a verified completion. **Working**: 356 runs pruned | E | the latest run's scratch only | intraday about 11 (by policy) | delete scratch as soon as the run is verified (keep the newest 1–2). Consumers are the run itself and `shadow_tick.scoring_evidence` |
| `prod/runs` UNVERIFIED (R20261117T150102Z, R20261117T210124Z) | 1.07 | 0 | 30,360 predictions but no durable PMF evidence file names the run, so retention fails safe and keeps the scratch | E (features reproducible from the as-of state version); `market_pmfs.parquet` in the run dir is the evidence and stays | 0 | **1.07** | write the durable PMF evidence from the run's `market_pmfs.parquet`; the normal rule then prunes it |
| `prod/runs` records | 0.29 | about 15 | run_summary, market_pmfs, inputs, diagnostics | C | 0.29 | 0 | none |
| `prod/state/versions` | 5.29 | 330 (11-16 → 27); 370–640 in the last 4 days | **every sync writes a full copy of the whole increment DB.** 313 versions, 11–37 a day. The copy has grown from 8.2 to 19.4 MB, about 7.5 KB per completed game. Manifests: `metadata.prod_schedule` changes on 312 of 312 syncs; games / rosters / players / recruits / transfers on 231–235; box outcomes on 207; teams / team_seasons on 1. Unchanged relations are re-stored on every sync | **B** (Stage 07/08 replay evidence; nothing may be lost) | about 1 if stored as deltas (estimate, unverified until prototyped) | about 4, by **lossless migration**, not deletion | relation-level content-addressed store: each relation stored once per distinct sha256 (the manifests already compute them), a version = a manifest of hashes. Better: append-only bitemporal rows (first_seen / superseded version) for the outcome relations. `walkforward.eligibility.state_asof` must reproduce every version byte-for-byte at the relation level before any copy is removed |
| `prod/ops` | 2.03 | 120 | forecasts 1.00 (pregame PMF snapshots), quotes 0.36, ticks 0.34 (478 ticks, about 0.9 MB each, full PMFs every 30 min), game_quotes 0.10, evaluations 0.07, ledgers 0.05 | C | 2.03 | 0 | content-address tick PMFs per (game, model, inputs) so an unchanged game is not rewritten. The repetition rate is not measured: reading the PMFs was out of scope |
| `prod/cache/raw` | 0.72 | 30 | provider payloads (cbbd 0.58, odds 0.14). 19,056 files gzipped, 20,026 JSON uncompressed (0.47 GB) | B (payload sha256 in `feed_provenance`) | about 0.3 | about 0.4 | gzip at write, keeping the hash of the raw bytes. The existing compression rule covers RotoWire only |
| `walkforward` | 1.12 | about 5 | training frames / feature cache, models, rollback | E / D | 1.12 | 0 | none (bounded) |
| `history` | 0.70 | 0 | lockbox history | A | 0.70 | 0 | none |
| `audit` | 0.89 | 0 (one-off) | `audit/causal/ov/candidates` 0.63: 11-22 causal research scratch, retention class NO_REASON; findings already in the repo. Stage audits 0.26 | G / E (causal), B (stage audits) | 0.26 | **0.63** | add an audit TTL or a RETAIN marker rule, as for `qa/` |
| `qa` | 0.36 | 0 | 7 dirs; 6 carry RETAIN.json (market-20261118 0.20, perf 0.06, quotes 0.06, …) | G, retained by marker | owner | up to 0.35 | review the RETAIN markers |
| `exports` | 0.39 | about 20 | daily workbooks | C | 0.39 | 0 | none |
| `/opt` releases + venv | 1.00 | 0 | current 9f65e7f, rollback cae2a02, venv 0.41, Stage 07 candidate 7d160b1 0.08. Superseded: 4 staged `.tmp` trees 0.29 and the old release c30c173 0.07 | D / G | 0.63 | **0.36** | bounded release retention: current + rollback + one candidate |

**Totals:**
* Safely reclaimable now, with authorization: about **2.8 GB** (1.07 + 0.63 + 0.36 + 0.35 + 0.4).
* Plus about 4 GB from the state-version migration (code change, Stage 19).
* The intraday peak falls by about 11 GB once scratch is deleted at verification.

## 3. Projection and infrastructure

**CBB without correction.** The state copy grows with completed games, from 8.2 MB at the season start to 19.4 MB now. It reaches about 50 MB by March, which is about 1 GB/day at the current sync rate, or an estimated 60–90 GB for the season.

**CBB after correction.** About 0.15 GB/day (ops evidence plus state deltas plus cache). The sustainable persistent footprint is about 10–11 GB, plus 1–2 GB of transient scratch. CBB needs no additional storage.

**Host.** Free space was 40.2 GB after the 22:45Z retention pass. Non-CBB growth of about 2.35 GB/day would exhaust that in about 17 days even if CBB growth were zero.

That growth lies in tenants outside this scope: `/srv/nba-production`, `/srv/massey-predictive-models`, `/srv/fanatics-demo`, `/srv/nba-release-review`. Some `/tmp` data was also observed earlier: 6.85 GB, including nbaquant `t20-cohort` 5.1 GB (11-13) and `reprice-fix` 0.7 GB. The journal holds 0.67 GB.

Attributing that growth needs a scoped, owner-authorized read-only census of those tenants. Buying storage would not fix it either.

## 4. Deploy-gate consequence

The gate's unit was established in Stage 02 ("38.5 GB … observed 41 GB of 154 GB", i.e. GiB on this 165.3e9-byte disk). The strictest reading is used: **38.5 GiB = 41,339,060,224 bytes**.

| measurement | bytes | GiB | gate |
|---|---|---|---|
| 11-27 16:53Z, after the 11:45 ET pass | 44.66e9 | 41.59 | pass |
| 11-27 22:40Z, intraday | 36.40e9 | 33.90 | fail |
| 11-27 22:50Z, after the 17:45 ET pass | 40.21e9 | 37.45 | fail |
| 11-28 01:10Z, after reclaiming 0.29 GB | 37.80e9 | 35.21 | fail |

* After the 04:45Z pass the history is 41.9e9 (11-27), 37.5e9 (11-26) and 41.3e9 (11-25), so the gate may or may not pass then.
* After the 16:45Z pass it has passed every day.
* The decimal figures in sections 1–3 are unchanged.

## 5. Other tenants (metadata-only census, authorized 2026-11-28)

`du` and `find -printf` were used; no file contents were read and nothing was modified.

| tenant | allocated GB | allocated in files modified since 11-16 16:45Z |
|---|---|---|
| `/srv/nba-production` | 61.75 | **≈15.6 GB** (`nba-prop-v2-work` 9.6, `models` 4.8, `nba-prop-v2-data` 0.7, `work` 0.3) |
| `/root` | 19.57 | ≈2.1 GB (`.vscode-server` 1.8, Massey root venv 0.3); `riskyscout` 14.4 GB and `massey-postgres.sql` 2.4 GB are older |
| `/tmp` | 6.85 | 0.4 GB (vsix); `t20-cohort` 5.1 GB is from 11-13 (nbaquant) |
| `/srv/massey-predictive-models` | 4.27 | ≈1.2 GB (`public_export` 0.9, `.venv` 0.3) |
| `/var/lib/docker` | (in `/var/lib` 2.65) | ≈1.0 GB |
| `/var/log` | 0.82 | 0.4 GB (journal) |
| `/srv/fanatics-demo`, `/srv/nba-release-review` | 0.49, 0.05 | under 0.2 GB |

`/mnt/nba-ro` is a read-only bind mount of `/srv/nba-production` and was excluded to avoid double counting.

These modified-file totals do not net out deletions, so they are upper bounds on growth. They account for about 20 of the 25.9 GB of non-CBB growth. **NBA production (≈1.4 GB/day) is the largest single source of host growth**, larger than all of CBB. That is the NBA owner's decision; nothing there was touched.

## 6. Reclamation executed (authorized 2026-11-28)

| artifact | bytes | why disposable | recovery |
|---|---|---|---|
| staged `.tmp` trees d392832, 93e5cd2, 56f3dae, 25b83ed | 292,876,288 | superseded candidates; not current, rollback or unit-referenced | `git archive <sha>` (all on origin); hash lists in `docs/audits/2026-11-27-stage{2..5}` |

Receipts: Droplet `audit/stage7-20261127/reclaim_receipts.jsonl`.

**Not reclaimed:**
* The two UNVERIFIED runs' scratch (1.07 GB): their `market_pmfs.parquet` does not hold all their predictions.
* `audit/causal/ov/candidates` (0.63 GB): past candidate-evaluation evidence.
* The RETAIN-marked qa dirs.
* The c30c173 / cae2a02 release trees: they are pruned by the established `cbb-prune-releases` step at deploy, which keeps current and rollback.
* The raw cache: compression is a code change (S19-STOR-4).
