# CBB Originator: storage layout and retention (Droplet)

Owner: CBB Originator operations. Established by the 2026-11-14 production-footprint cleanup (evidence:
`/var/lib/sports-models/cbb-originator/ops/cleanup/`). This file says where every artifact belongs, how long it stays,
and what deletes it. Scope is the CBB area only. Other models on the Droplet (NBA Prop Quant, Massey NCAAF/NFL) are
outside it, and so are the shared heavy lock and journald.

## 1. Code: `/opt/sports-models/cbb-originator` (root-owned, read-only to the service user)

| Path | Contents | Retention |
|---|---|---|
| `current -> releases/<sha>` | the deployed immutable release (git archive + `<sha>.sha256sums`, re-verified by every job) | the live release |
| `releases/<sha>` named in `ROLLBACK_RELEASE` | the release `current` pointed to before the last deploy | kept: the one-step rollback |
| `releases/<sha>` listed in `KEEP_RELEASES` | `<sha> <reason>`, one per line: an emergency rollback with a documented reason | kept while listed |
| every other `releases/<sha>` | superseded deploys | deleted by `cbb-prune-releases --apply`; the commits stay in `Risky-Scout/cbb-originator` |
| `venv/` | the pinned runtime (`requirements.lock.txt`) | rebuilt only with a dependency change |

Root tooling (in the release, `deploy/droplet/ops/`; it replaced the old `/root/cbb_*.sh` scripts):
`cbb-deploy-release <sha>` (atomic swap, writes `ROLLBACK_RELEASE`, backs up the env), `cbb-install-units`,
`cbb-prune-releases [--apply]`. The prune keeps only the newest 3 env backups,
`/etc/sports-models/cbb-originator.env.bak-*`. The env file itself is never touched by retention.

## 2. Mutable state: `/var/lib/sports-models/cbb-originator` (`cbb-originator`, mode 2750)

| Path | Class | Contents | Retention |
|---|---|---|---|
| `prod/state/CURRENT.json`, `prod/state/versions/<S>/` | CURRENT_PRODUCTION / PERMANENT_AUDIT | season increment (`increment.duckdb`, a full self-contained copy per version) + manifest | **kept**: every version is referenced by ticks, forecasts, runs or the walk-forward training manifests, and `walkforward.eligibility.outcome_available_at` / point-in-time exports read old increments |
| `prod/runs/<R>/` | mixed | one directory per scoring / forecast run | see §3 |
| `prod/ops/` | PERMANENT_AUDIT | evidence tree: `CHAIN.jsonl` (hash chain over `ticks/`), `ticks/<K>/` (official prediction / settlement evidence, `pmfs.parquet`), `forecasts/<D>/<F>/` (forecast snapshots, `pmfs.parquet`), `registry/`, `markers/`, `reports/`, `morning/`, `state_manifests/`, `ledgers/` (mirror, §5), `retention/` (retention reports + receipts) | kept. Only `forecasts/*/*/forecast_ledger.jsonl` is compressed (§4) |
| `prod/cache/raw/<provider>/<endpoint>/` | CACHE_EXPIRABLE | raw CBBD / The Odds API payloads + `.meta.json` | §4 |
| `prod/cache/*_production_requests.jsonl` | PERMANENT_AUDIT | **canonical** request ledgers (every payload's sha256) | kept (append-only) |
| `prod/availability/` | PERMANENT_AUDIT | RotoWire event log, web acquisitions, news, raw pages | raw pages gzip after 14 d, never deleted |
| `prod/odds/` | mixed | `collections/` (quote collections), `integration_validation.json`, `validation_runs/` | validation-run feature scratch deleted after 48 h |
| `prod/sync/`, `prod/health/`, `prod/maps/`, `prod/walkforward_attempts/`, `prod/STATE_IMPORT*.json` | PERMANENT_AUDIT | sync log, daily health receipts, id maps, attempt receipts, import provenance | kept (small) |
| `walkforward/cbb-originator/` | CURRENT_PRODUCTION / PERMANENT_AUDIT | `active/ACTIVE_MODEL.json`, `rollback/ROLLBACK_MODEL.json`, `models/<date>/` (each about 0.5 MB), `manifests/` (promotion log, runs), `training/features` (walk-forward feature cache) | **every promoted model kept**: forecasts reference each by version. Never touched by retention |
| `history/cbb_production_history_minimal.duckdb` | CURRENT_PRODUCTION | frozen 2013-14 to 2025-26 history extract (`CBB_HISTORY_CANDIDATE`) | kept, read-only |
| `exports/<D>/` | RETAINED_HISTORY | final `CBB_Boards_*`, `CBB_Distributions_*`, `CBB_Grades_*` workbooks + receipts | final workbooks kept for the season; temporary files deleted after 1 d |
| `evidence/<date>-<topic>/` | PERMANENT_AUDIT | small audit notes / hashes of one-off operations (cutover, identity, cleanup) | kept |
| `ops/cleanup/` | PERMANENT_AUDIT | storage cleanup inventories, classification, delete manifests, receipts | kept |
| `qa/<name>/` | QA_TEMPORARY / PERMANENT_AUDIT | QA probes. A directory with `RETAIN.json` holds accepted evidence only and is kept | without `RETAIN.json`: deleted 14 d after its last change |
| `staging/` | QA_TEMPORARY | one-off scratch; `staging/tmp/` = `TMPDIR`; `staging/tmp/duckdb/pid-<N>/` = DuckDB spill | §4 |
| `secrets/` | CURRENT_PRODUCTION | RotoWire subscriber cookie (0600) | never touched |
| `locks/` | CURRENT_PRODUCTION | `cbb-originator.writer.lock` | never touched |

Canonical sources of truth:

* **PMF (one physical payload per forecast state):** `prod/ops/ticks/<K>/pmfs.parquet` (official predictions) and
  `prod/ops/forecasts/<D>/<F>/pmfs.parquet` (forecast snapshots), written once, immutable, in the COMPACT canonical
  format (`prod.pmf_compact`, zstd Parquet): one row per snapshot x game x player x stat with `support_min`,
  `support_max`, the complete ordered float64 vector (Arrow `list<double>`, lossless) and `pmf_sha256`, plus the
  receipt id, run id, model version, as-of instant, mean, method and labels. The long one-row-per-outcome form is
  DERIVED_ON_DEMAND (`pmf_evidence.read` / `pmf_compact.expand`, byte-identical) and never written by production.
  Legacy long-schema files are read unchanged.
  `runs/<R>/market_pmfs.parquet` is what a run writes; as soon as the snapshot / tick file holds every one of its PMFs
  bit for bit it is replaced by `market_pmfs.ref.json` (canonical path + sha256). The Distributions export carries
  REFERENCES (`CBB_Distributions_<D>_pmf_refs.parquet`: canonical file + `pmf_sha256` per row), never vectors.
* **Simulation draws:** never persisted. Each game's joint draw bank lives only while that game is simulated and its
  PMFs derived (`prod.run`: the previous game's bank and mixture tables are released before the next game); no slate
  matrix exists. Over / under / push probabilities, fair odds and EV exist only for real quoted lines, derived from the
  persisted PMF when the quote is priced (no hypothetical-line grid is stored).
* **Forecast evidence:** `forecasts/<D>/<F>/snapshot.json` + `pmfs.parquet` + `forecast_players.parquet` +
  `forecast_ledger.jsonl[.gz]` (hash-chained PREDICTION rows).
* **Official prediction / grading:** `ops/ticks/*` (sealed by `CHAIN.jsonl`), `ops/registry/` and the Grades export.
* **Model:** `walkforward/cbb-originator/models/<date>/` plus `active/` and `rollback/` pointers, and
  `manifests/promotion_log.jsonl`.

## 3. Run directories (`prod/runs/<R>/`)

Every run ends in exactly one terminal record: `run_summary.json` (SUCCESS / DEFERRED, with its readiness state) or
`run_failure.json` (FAILED / ABORTED / ORPHANED; `prod.run_receipts`, written by the run itself or by retention's
ORPHAN_RUN_RECEIPT for a SIGKILLed / OOM-killed run). Retained files and their readers:

| File | Reader | Retention |
|---|---|---|
| `run_summary.json` / `run_failure.json` | retention, receipts, lifecycle / tick (same process), audit | permanent (a few KB) |
| `market_pmfs.ref.json` | `pmf_evidence.read_run_pmfs` (walk-forward monitor), audit | permanent (pointer; the payload is in `ops/`) |
| `model_inputs.parquet` | lifecycle / shadow tick (same process), walk-forward monitor, audit (state / input provenance) | permanent (~70 KB) |
| `player_universe.csv`, `board.csv` | audit / reproduction of the run's universe and board | permanent (small) |
| `bundle/` (`predictions.json`, `SHA256SUMS`, `DEPLOYMENT_MANIFEST.json`, ...) | `bundle.verify` (same run), audit | `predictions.json` gzipped after 2 d |
| `game_diagnostics.json` | walk-forward monitor (reads `.gz` too), audit | gzipped after 2 d |
| `features/canonical_asof.duckdb`, `features/features_asof.duckdb` (~530 MB) | the run itself only | scratch: §4 |

Scratch rules:

| File | Rule |
|---|---|
| `features/canonical_asof.duckdb`, `features/features_asof.duckdb` (about 530 MB per run, rebuilt from state + history as of the run) | completed and PMF-verified: deleted 18 h after completion (`RUN_SCRATCH`, no count floor). Never completed (no valid `run_summary.json`), untouched for 18 h: deleted after its terminal receipt (`INCOMPLETE_RUN_SCRATCH`). With the 6-hourly timer: gone within 24 h |
| `market_pmfs.parquet` | replaced by `market_pmfs.ref.json` at write time; the backlog by `RUN_PMF_DUPLICATE` (bit-exact check) |

## 4. Automatic retention (`cbb-originator-retention.timer`, every 6 h from 05:45 ET: 05:45 / 11:45 / 17:45 / 23:45)

`cbb-originator-run prune-scratch --apply` runs `prod/retention.py` first, then `prod/storage_retention.py`. It holds the CBB writer lock, so no
run, forecast, tick or export is in progress, and it never takes the heavy lock. Each pass writes a report before
changing anything (`prod/ops/retention/reports/<ts>.json`, `<ts>-storage.json`). Every change gets a receipt in
`prod/ops/retention/scratch_prune.jsonl` / `storage_retention.jsonl`. A target outside its rule's path pattern, or
inside a protected tree, is refused before the first change.

| Rule | Target | Window |
|---|---|---|
| RUN_SCRATCH | completed, verified run feature DuckDBs | 18 h (gone within 24 h) |
| ORPHAN_RUN_RECEIPT | `run_failure.json` ORPHANED for a run with no terminal record, untouched 7 h | writes only |
| INCOMPLETE_RUN_SCRATCH | feature DuckDBs of runs that never completed (after the receipt) | 18 h after the last write (gone within 24 h) |
| VALIDATION_RUN_SCRATCH | `prod/odds/validation_runs/*/*/features/*.duckdb` | 42 h (gone within 48 h) |
| DUCKDB_SPILL | `staging/tmp/duckdb/pid-<N>/` of an exited process | 1 h |
| STAGING | `staging/<entry>` (not `tmp/`, not `cleanup-quarantine-*`) / `staging/tmp/<entry>` | 14 d / 2 d |
| QA | `qa/<dir>` without `RETAIN.json` | 14 d |
| EXPORT_TEMP | `exports/<D>/*.tmp, *.partial, ~$*, .~lock*, *.bak` | 1 d |
| FORECAST_LEDGER_GZIP | `forecasts/<D>/*/forecast_ledger.jsonl` → `.jsonl.gz` (byte-exact round trip checked) | ET date at least 2 days old |
| RAW_CACHE_SUPERSEDED | raw payload + meta superseded by a newer payload of the same request | 14 d (newest per request kept forever) |
| ROTOWIRE_RAW_GZIP | `availability/rotowire_web/raw/<D>/*` → gzip | 14 d (never deleted) |
| RUN_PMF_DUPLICATE | `runs/<R>/market_pmfs.parquet` bit-identical to one durable file → `market_pmfs.ref.json` | 1 h (backlog; writers do it at once) |
| RUN_JSON_GZIP | `runs/<R>/bundle/predictions.json`, `game_diagnostics.json` of completed runs → gzip (byte-exact) | 2 d |
| LEDGER_MIRROR_LINK | `ops/ledgers/*.jsonl` → hard link to the canonical working ledger | when it is a byte prefix |

Report only, in every `<ts>-storage*.json`: `model_audit` (each model dir ACTIVE / ROLLBACK /
REFERENCED_BY_RETAINED_FORECAST / UNREFERENCED / INCOMPLETE, duplicate payload files) and `footprint_before` /
`footprint_after` (bytes per area, file count, free disk, every directory > 500 MiB with its reason:
CURRENT_PRODUCTION / REQUIRED_HISTORY / REQUIRED_AUDIT / REQUIRED_ROLLBACK, or NO_REASON, which must be reduced).

Never touched by retention: `prod/state`, `walkforward/`, `history/`, `secrets/`, `locks/`, `evidence/`,
`ops/cleanup/`, `ops/ticks`, `CHAIN.jsonl`, `registry`, `markers`, `reports`, `morning`, `state_manifests`, durable
`pmfs.parquet` files, forecast snapshots (except the ledger compression), final export workbooks and receipts,
`/etc/sports-models/cbb-originator.env`, and `/opt` (read-only for the service; releases are pruned by the root tooling).

## 5. Ledgers (one canonical copy)

The production modules append to the canonical working ledgers: `prod/cache/{cbbd,odds,rotowire}_production_requests.jsonl`,
`prod/sync/sync_log.jsonl` and `prod/state/update_failures.jsonl`. `prod/ops/ledgers/` mirrors the CBBD and Odds request ledgers, the sync log and the state-update failures
(`ops_store.ledger_mirror`); it is written by `ops_store.mirror_out`; it dates from the GitHub-runner era. On the Droplet each mirror is a **hard link** to its
canonical file, so there is one physical copy and `mirror_out` / `mirror_in` find identical content and write nothing.
Both writers only append, so the link survives. LEDGER_MIRROR_LINK re-links a mirror if a copy ever reappears.
`ops/ledgers/tick_log.jsonl` exists only in the evidence tree. The ledgers are append-only audit receipts (tens of MB
per season) and are not rotated, because rotation would break the prefix invariants. There are no CBB text log files:
every unit logs to journald, which is managed by its own retention.

## 6. Rollback

* Code: `current` → the release in `ROLLBACK_RELEASE` (runbook §4). Restore the env backup that matches
  `CBB_EXPECTED_COMMIT`, reinstall that release's units, then run preflight. State is never rolled back.
* Model: `walkforward/cbb-originator/rollback/ROLLBACK_MODEL.json` (pointer change only, never a refit). Every promoted
  model directory is kept.

## 7. Known growth (reviewed 2026-11-14)

* `prod/state/versions`: about 15 MB per state version, several per day. Every version is referenced, so it grows to
  several GB per season. A future change could store old increments compressed, but `outcome_available_at` and
  point-in-time exports read them directly. Re-measure monthly.
* `prod/ops/ticks` and `forecasts`: compact Parquet + gzip; the forecast ledgers are gzipped after 2 days.
* `walkforward/cbb-originator/training/features`: the walk-forward feature cache, about 1 GB, grows with the season.
  It is CURRENT_PRODUCTION.
* `exports/`: Boards + one-row-per-player-stat Distributions summary + PMF reference Parquet + Grades per day (the
  exploded one-row-per-outcome workbook, 21–56 MB / day before 2026-11-15, is produced only by
  `python -m cbb_platform.prod.exports expand-distributions --date D`). Pre-2026-11-15 daily folders keep their
  exploded workbooks until they are replaced or the season ends (they are final exports of record).
