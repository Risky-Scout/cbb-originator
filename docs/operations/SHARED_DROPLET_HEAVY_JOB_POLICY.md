# Shared Droplet heavy-job coordination policy

> **SUPERSEDED IN PART (2026-11-07, Droplet deployment).** The installed layout is: immutable git-archive release
> `/opt/sports-models/cbb-originator/releases/<sha>` (+ root-owned `<sha>.sha256sums`, re-verified before every job)
> behind `current`; mutable state only under `/var/lib/sports-models/cbb-originator/{prod,walkforward,exports,locks}`
> (`CBB_PROD_ROOT` / `CBB_WF_ROOT` / `CBB_EXPORT_ROOT`); units `deploy/droplet/systemd/cbb-originator-{morning,
> refit-retry,availability@,quotes}`; shared heavy lock `/run/lock/sports-models/heavy.lock` (group
> `sports-models-heavy`, held by `nba-prop-daily.service` via an authorized drop-in and by the CBB walk-forward step
> only; busy for 30 min -> `DAILY_REFIT_STATUS = DEFERRED_SHARED_LOCK`, last-known-good kept, one 08:00 ET retry);
> heavy-unit limits `MemoryHigh=2800M` / `MemoryMax=3200M` / `MemorySwapMax=0` on the 8 GB host. The `.../app` checkout,
> bind mounts, `sports-model-heavy.lock` path and 12G/14G/13 GB values below are historical.


**Status:** DESIGN. Not deployed globally. Nothing has been installed on any server.

**Scope:** every memory-heavy job on the shared sports-model Droplet:
- CBB Originator morning refit;
- NBA Prop Quant fits and training;
- NFLProps training;
- Massey NCAAF and NFL refits.

**This policy does not solve concurrency today.** Only CBB Originator implements it, in `deploy/droplet/bin/cbb-originator-run`. A lock protects only against jobs that also take it. Until every heavy model adopts the same contract, the other models' schedules must be staggered by hand (§5), and the CBB memory gate (§4) is the last line of defence.

## 1. Lock contract

| item | value |
|---|---|
| lock file | `/run/lock/sports-models/sports-model-heavy.lock` (the directive suggested `/run/lock/sports-model-heavy.lock`; it is placed in the shared `sports-models/` lock directory so one tmpfiles entry owns both) |
| creation | `deploy/droplet/tmpfiles/sports-models-locks.conf`: directory `2775 root:sports-models`, lock file `0660 root:sports-models`. This is a host-wide change, installed once by an explicit cross-model decision, never by one model's deployment. |
| membership | every heavy model's dedicated service account joins group `sports-models` |
| primitive | `flock(2)`, an advisory exclusive lock on an open file descriptor, e.g. `exec 8>>LOCK; flock -w T 8` |
| held for | the whole heavy phase, from the memory check to the job's exit. The kernel releases it on process exit, including crash, OOM kill and `SIGKILL`. |
| scope | **heavy jobs only:** any job whose measured peak is above 2 GB or whose measured RSS is above 1 GB. Light jobs never take it, so they can never block a heavy job. Light jobs include scoring ticks, quote snapshots, status checks and publication. |
| per-model locks | separate from this lock. CBB also takes its own writer lock (`/run/lock/sports-models/cbb-originator.writer.lock`) and the walk-forward store lock (`locks/morning.lock`). |
| lock order | **shared heavy lock first, then the model's own locks.** A job must never wait for the heavy lock while holding a lock another job needs. |

## 2. Timeout behaviour

- A job waits at most a bounded time for the lock, `flock -w <seconds>`. CBB waits 1,800 s (`CBB_HEAVY_LOCK_WAIT_SEC`).
- On timeout the job does **not** run. It exits `75` (EX_TEMPFAIL) and records its own resource failure. For CBB that is `DAILY_REFIT_STATUS = FAIL (RESOURCE)`, and the last known-good ACTIVE version stays in place.
- No automatic retry storms. systemd `Restart=no`. At most one attempt per cutoff, plus one manual retry by the operator.
- A job that already holds the lock is **never** killed by another model to free it.

## 3. Stale-lock behaviour

- `flock` locks are tied to an open file descriptor, not to the file's existence or contents. A dead process cannot leave a stale lock: the kernel releases it on exit.
- The lock file is therefore never deleted to "break" a lock. Deleting it would let two jobs lock two different inodes. `tmpfiles` recreates it at boot because `/run` is tmpfs.
- **Hung holder.** A holder that is alive but hung is the only real "stale" case. It is bounded by each unit's own `TimeoutStartSec`; CBB morning is 75 min. systemd kills the hung unit, and the kernel then releases the lock.
- **Breaking a lock manually.** Inspect it with `lslocks` or `fuser /run/lock/sports-models/sports-model-heavy.lock`, then stop the owning **unit** with `systemctl stop`. Never `rm` the file.

## 4. Fail-safe behaviour

- **Memory gate after the lock.** After taking the lock, and before any model work, a heavy job checks `MemAvailable` against its own measured requirement. CBB requires 13 GB: the 11.2 GB peak plus about 15 % margin. It re-checks every 60 s for up to 30 min. If memory is still short, it exits `75` without starting.
- **Contain an over-run to its own unit.** systemd `MemoryHigh` / `MemoryMax` per unit, `MemorySwapMax=0`, and `Nice` / `CPUQuota` / `IOSchedulingClass`. CBB morning uses 12 G / 14 G / 0, Nice 10, CPU 200 %, best-effort I/O at priority 7.
- **Fail closed, never degrade silently.**
  - A job that cannot get the lock or the memory does not run in a "smaller" mode.
  - No swap is added and no system setting is changed as compensation.
  - The model's last known-good output stays in force.
- **Fail visibly.** Failures go to journald under model-specific identifiers such as `cbb-originator-morning`.

## 5. Schedule-staggering recommendation (interim, until every model honours the lock)

| window (America/New_York) | owner | notes |
|---|---|---|
| 06:00–07:00 | **CBB Originator morning refit** (starts 06:05; measured 118.6 s, wall-clock budget 75 min including waits) | no other heavy job is scheduled here |
| outside 06:00–07:00 | NBA Prop Quant / NFLProps / Massey heavy jobs | each owner places their jobs; two heavy jobs never share a window |
| any time | light jobs (CBB ticks at :12 and :42 UTC, quote snapshots) | they never take the heavy lock |

The actual schedules of NBA Prop Quant, NFLProps and Massey on the Droplet have **not been measured** (the read-only Droplet audit has not been run; see the deployment package §3). This table is therefore a recommendation, not a verified non-overlap.

## 6. Adoption requirement

- **Every heavy model must adopt the same contract** before the shared Droplet counts as safe for concurrent heavy work: the same lock file, `flock`, heavy lock first, bounded wait, exit 75 on timeout, memory gate, and per-unit memory limits.
- **Until all four models have adopted it, the shared-Droplet resource gate can pass only if the hardware makes concurrency harmless.** That means MemAvailable at 06:00 ET is at least 13 GB plus the co-resident models' peaks.
- **Each model's owner changes that model's own units.** No model's deployment edits another model's directories, units or schedules.
