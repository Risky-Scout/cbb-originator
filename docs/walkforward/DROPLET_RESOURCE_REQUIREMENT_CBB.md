# CBB Originator: shared-Droplet resource requirement for the daily walk-forward refit

**Status:** REQUIREMENT ONLY. Nothing has been migrated.

The future shared Droplet also runs Massey (NCAAF/NFL), NBA Prop Quant and NFLProps.

## 1. Measured (macOS arm64, 10 cores, 32 GB; single-threaded BLAS; `nice 15`)

| measurement | value | source |
|---|---|---|
| one standalone morning cycle (6-game refit, 2 fits + all gates) | **118.6 s** wall, 150 s CPU | `/usr/bin/time -l`, one process |
| peak resident set size (RSS) | **7.17 GB** | same run |
| peak memory footprint (incl. DuckDB working memory / compressed pages) | **11.2 GB** | same run |
| marginal + structural fit alone | 5.5 GB RSS, ~30 s | earlier scratch measurement |
| pytest process running the whole season timeline | 8.2 GB RSS | `tests/test_walkforward_daily.py` |
| Stage 3 build scratch (one compact canonical + features DB, deleted after) | 0.55 GB | one `prod.features.build` |
| version store growth | ~170 KB / version + ~60 KB / game (~0.4 GB / season) | end-to-end run |

**Configured cap.** The DuckDB connections of the Stage 3 builds (`stage3/build.connect`) set
`memory_limit = '12GB'`. This is the configured upper bound of the database part of a morning cycle.

## 2. Requirement

| resource | requirement |
|---|---|
| **free RAM at refit start** | **≥ 13 GB**: the 11.2 GB measured peak footprint (bounded by the 12 GB DuckDB cap) plus about 15 % margin |
| total server RAM | **≥ 16 GB if CBB has its morning window to itself**; more if any other model may be resident at the same time |
| **8 GB total server** | **UNSAFE.** A single CBB refit's measured footprint (11.2 GB) exceeds it, and its RSS alone (7.2 GB) would leave under 1 GB for the OS and every other service. Do not deploy CBB on an 8 GB server |
| CPU | one core sustained for about 2-3 min per morning; DuckDB uses 2 threads during builds. BLAS/OMP threads stay at 1 (`OMP/OPENBLAS/MKL/VECLIB/NUMEXPR_NUM_THREADS=1`) |
| disk (CBB-only) | about **12 GB** of read-only frozen stores (8.8 GB canonical history, 2.5 GB Stage 3 features + labels, 0.2 GB OOS stores) + ≥ 2 GB scratch + about 0.5 GB / season of walk-forward store + about 2 GB `.venv`: **≥ 20 GB reserved** |
| I/O | sequential reads of the frozen DuckDB files on each build (local SSD; not network storage) |

## 3. Scheduling and locking policy (recommended)

1. **Exclusive heavy-job window.**
   - The CBB morning refit runs at 06:00 America/New_York (systemd timer with `TZ`).
   - No other memory-heavy model job (Massey refits, NBA / NFL training) may overlap it.
2. **Cross-model lock.**
   - Every heavy model job takes one shared host lock (e.g. `flock /var/lock/sports-models-heavy.lock`) before it starts.
   - CBB already holds its own `locks/morning.lock`; the shared lock is in addition to it.
   - A job that cannot get the lock waits. It is not killed half-way.
3. **systemd limits for the CBB unit:**
   - `MemoryHigh=12G`, `MemoryMax=14G`, so an over-run is contained to CBB.
   - `Nice=10`, `CPUQuota=200%`, `IOSchedulingClass=best-effort`, `IOSchedulingPriority=7`.
4. **Pre-flight.**
   - The job checks `MemAvailable ≥ 13 GB` before it starts.
   - Otherwise it waits up to 30 min, re-checking. If memory is still short, it records `DAILY_REFIT_STATUS = FAIL (RESOURCE)`, and ACTIVE stays on the last known-good version (the walk-forward failure semantics).
5. **No refit retry storm.** At most one attempt per cutoff, plus one manual retry.
6. **Scoring is unaffected.** Per-game scoring (TIP_MINUS_1H, 20,000 draws) is lighter and keeps its own cadence. It must not run inside another model's heavy window if memory is near the floor.

## 4. Possible reductions (NOT applied)

- Lowering the Stage 3 DuckDB `memory_limit` (for example to 6 GB) would cap the footprint by spilling to disk. The
  results would be identical, but it must be measured first, and it touches shared research code.
- The residual-season structures through 2022-23..2025-26 could be cached by content hash, saving about 10 s per fit.

Neither is needed for correctness.

## 5. Gate

**SHARED_DROPLET_RESOURCE_GATE = UNKNOWN.** The Droplet's RAM, the co-resident models' peaks and their schedules have
not been measured or provided here.

The gate is **PASS** only if all of the following hold:
- MemAvailable ≥ 13 GB at 06:00 ET with every co-resident service in its normal state;
- the shared heavy-job lock is in place;
- ≥ 20 GB of disk is reserved for CBB.

The gate is **FAIL** for any server with less than 16 GB total.
