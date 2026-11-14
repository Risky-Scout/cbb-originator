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

* **PMF:** `prod/ops/ticks/<K>/pmfs.parquet` (official predictions) and `prod/ops/forecasts/<D>/<F>/pmfs.parquet`
  (forecast snapshots), in `prod.pmf_evidence` long format. They are written once and are immutable.
  `runs/<R>/market_pmfs.parquet` is the source a run wrote. It is a duplicate once the durable copy holds it. The
  Distributions workbook is a report derived from these files, never the store.
* **Forecast evidence:** `forecasts/<D>/<F>/snapshot.json` + `pmfs.parquet` + `forecast_players.parquet` +
  `forecast_ledger.jsonl[.gz]` (hash-chained PREDICTION rows).
* **Official prediction / grading:** `ops/ticks/*` (sealed by `CHAIN.jsonl`), `ops/registry/` and the Grades export.
* **Model:** `walkforward/cbb-originator/models/<date>/` plus `active/` and `rollback/` pointers, and
  `manifests/promotion_log.jsonl`.

## 3. Run directories (`prod/runs/<R>/`)

Minimum canonical evidence of a run: `run_summary.json` (identity, state, model), `model_inputs.parquet`,
`player_universe.csv`, `board.csv`, `bundle/`, `game_diagnostics.json`, and the durable PMFs in `ops/`. The rest is
scratch:

| File | Rule |
|---|---|
| `features/canonical_asof.duckdb`, `features/features_asof.duckdb` (about 510 MB per run, rebuilt from state + history as of the run) | completed and PMF-verified: deleted outside the latest 2 completed runs / 24 h (`RUN_SCRATCH`). Never completed (no valid `run_summary.json`) and untouched for 24 h: deleted (`INCOMPLETE_RUN_SCRATCH`) |
| `market_pmfs.parquet` | after 7 d, deleted only when every PMF equals the durable evidence of the same run_id outcome by outcome (`RUN_PMF_DUPLICATE`) |

## 4. Automatic retention (`cbb-originator-retention.timer`, 05:45 ET)

`cbb-originator-run prune-scratch --apply` runs `prod/retention.py` first, then `prod/storage_retention.py`. It holds the CBB writer lock, so no
run, forecast, tick or export is in progress, and it never takes the heavy lock. Each pass writes a report before
changing anything (`prod/ops/retention/reports/<ts>.json`, `<ts>-storage.json`). Every change gets a receipt in
`prod/ops/retention/scratch_prune.jsonl` / `storage_retention.jsonl`. A target outside its rule's path pattern, or
inside a protected tree, is refused before the first change.

| Rule | Target | Window |
|---|---|---|
| RUN_SCRATCH | completed, verified run feature DuckDBs | latest 2 completed / 24 h kept |
| INCOMPLETE_RUN_SCRATCH | feature DuckDBs of runs that never completed | 24 h after the last write |
| VALIDATION_RUN_SCRATCH | `prod/odds/validation_runs/*/*/features/*.duckdb` | 48 h |
| DUCKDB_SPILL | `staging/tmp/duckdb/pid-<N>/` of an exited process | 1 h |
| STAGING | `staging/<entry>` (not `tmp/`, not `cleanup-quarantine-*`) / `staging/tmp/<entry>` | 14 d / 2 d |
| QA | `qa/<dir>` without `RETAIN.json` | 14 d |
| EXPORT_TEMP | `exports/<D>/*.tmp, *.partial, ~$*, .~lock*, *.bak` | 1 d |
| FORECAST_LEDGER_GZIP | `forecasts/<D>/*/forecast_ledger.jsonl` → `.jsonl.gz` (byte-exact round trip checked) | ET date at least 2 days old |
| RAW_CACHE_SUPERSEDED | raw payload + meta superseded by a newer payload of the same request | 14 d (newest per request kept forever) |
| ROTOWIRE_RAW_GZIP | `availability/rotowire_web/raw/<D>/*` → gzip | 14 d (never deleted) |
| RUN_PMF_DUPLICATE | `runs/<R>/market_pmfs.parquet` equal to durable evidence | 7 d |
| LEDGER_MIRROR_LINK | `ops/ledgers/*.jsonl` → hard link to the canonical working ledger | when it is a byte prefix |

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
* `exports/`: about 30 to 110 MB per day, dominated by the Distributions workbook (a derived report). The compact summary
  workbook + PMF Parquet change (`b9ab155`, not deployed as of 2026-11-14) shrinks it.
