# CBB Originator: Droplet deployment package (pre-migration readiness)

> **SUPERSEDED IN PART (2026-11-07, Droplet deployment).** The installed layout is: immutable git-archive release
> `/opt/sports-models/cbb-originator/releases/<sha>` (+ root-owned `<sha>.sha256sums`, re-verified before every job)
> behind `current`; mutable state only under `/var/lib/sports-models/cbb-originator/{prod,walkforward,exports,locks}`
> (`CBB_PROD_ROOT` / `CBB_WF_ROOT` / `CBB_EXPORT_ROOT`); units `deploy/droplet/systemd/cbb-originator-{morning-prep,
> walkforward,morning-forecast,morning-exports,morning-health,morning-recover,availability@,quotes}` (2026-11-13 split); shared heavy lock `/run/lock/sports-models/heavy.lock` (group
> `sports-models-heavy`, held by `nba-prop-daily.service` via an authorized drop-in and by the CBB walk-forward step
> only; busy for 30 min -> `DAILY_REFIT_STATUS = DEFERRED_SHARED_LOCK`, last-known-good kept, one 08:00 ET recovery retry);
> heavy-unit limits `MemoryHigh=2800M` / `MemoryMax=3200M` / `MemorySwapMax=0` on the 8 GB host. The `.../app` checkout,
> bind mounts, `sports-model-heavy.lock` path and 12G/14G/13 GB values below are historical.


**Status:** PREPARED, NOT DEPLOYED. Nothing has been installed, copied, enabled or cut over. The live writer is still the GitHub scheduled workflow `CBB shadow tick` on `main`.

**Package files**

| path | role |
|---|---|
| `deploy/droplet/bin/cbb-originator-run` | the only entrypoint the units call. It handles the safe-mode refusal, exact-commit check, writer-role check, locks, memory gate and exit-code mapping. |
| `deploy/droplet/systemd/cbb-originator-morning.{service,timer}` | morning refit: 06:05 America/New_York, heavy |
| `deploy/droplet/systemd/cbb-originator-tick.{service,timer}` | prospective shadow tick: the approved GitHub cadence, in UTC, light |
| `deploy/droplet/env/cbb-originator.env.example` | the environment file template: variable names and safe defaults only, no secret values |
| `deploy/droplet/tmpfiles/sports-models-locks.conf` | shared lock directory. A cross-model decision; not part of a CBB-only install. |
| `deploy/droplet/STATE_TRANSFER_MANIFEST.json` | every artifact or store: source, destination, bytes, sha256, required, regenerable, transfer method |
| `docs/operations/SHARED_DROPLET_HEAVY_JOB_POLICY.md` | heavy-job lock contract |
| `tests/test_droplet_deploy_package.py` | focused tests of the package: safe defaults, refusals, no secrets, hashes |

## 1. Gate summary (2026-11-01)

| gate | state | blocks |
|---|---|---|
| CBB model integrity | PASS. Frozen hashes match, golden PASS, contamination audit PASS. | — |
| Deployment package / templates / plans | READY (this document) | — |
| **Shared-Droplet resource gate** | **UNKNOWN.** The read-only audit was not run: no approved SSH access to the Droplet exists for CBB (§3). | migration start |
| **Off-machine data scope for the Droplet** | **NOT COVERED.** The 2026-10-23 attestation covers only the minimized history extract plus the working set, in a private GitHub release processed by GitHub-hosted runners (`docs/OFF_MACHINE_DATA_LICENSE.json`). The Droplet needs about 12.2 GB of Starter-Pack-derived frozen stores (§5). | transfer of the frozen stores |
| RotoWire rights | UNVERIFIED. RotoWire is disabled by default and the wrapper refuses to enable it. | sustained RotoWire use; not migration |
| G4B prospective availability validation | ACCUMULATING | real-money actionability only; not migration (§9) |
| WizardOfOdds | DISABLED_PENDING_SSH_ACCESS | publication only; not migration |

## 2. Isolation layout

| purpose | path | owner / mode |
|---|---|---|
| application (exact Git commit, read-only to the service) | `/opt/sports-models/cbb-originator/app` | root:root 0755 |
| virtual environment (from `requirements.lock.txt`, `--require-hashes`, Python 3.12) | `/opt/sports-models/cbb-originator/venv` | root:root 0755 |
| runtime state | `/var/lib/sports-models/cbb-originator/{prod,walkforward,scratch/duckdb_tmp}` | cbb-originator 0700 |
| frozen read-only stores | `/var/lib/sports-models/cbb-originator/stores/{lockbox_2025_26/{canonical,stage3},v2_oos}` | root:root, files 0444 |
| secrets / environment | `/etc/sports-models/cbb-originator.env` | root:cbb-originator 0640 |
| locks | `/run/lock/sports-models/` (shared heavy lock + `cbb-originator.writer.lock`) | tmpfiles, group `sports-models` |
| logs | journald: `cbb-originator-morning`, `cbb-originator-tick` | — |

**Why bind mounts.** The code resolves its runtime paths relative to the checkout, for example `PC.PROD_ROOT = <app>/data/prod`. The units bind the CBB state directories onto exactly those paths, so no production code changes:
- `data/prod`, the DuckDB spill directory `modeling/store/duckdb_tmp`, and the walk-forward store (`CBB_WF_ROOT`) are writable.
- `data/lockbox_2025_26/canonical/cbb.duckdb`, `data/lockbox_2025_26/stage3/` and `modeling/v2_oos/` are read-only.

**Why `ProtectSystem=strict`.** The checkout itself is read-only, so any undeclared write fails loudly instead of landing in the app tree. No other model's directory is referenced.

## 3. Shared-Droplet read-only resource audit: NOT RUN

**Why it was not run**
- No SSH access to the Droplet has been approved for CBB.
- No host is recorded in this repository, and there is no SSH config.
- The deploy keys on this Mac belong to other projects (Massey, NBA Prop Quant, sportsodds, Wizard). Using another project's credentials is not an approved CBB access path, so none was used.

**Status:** `SHARED_DROPLET_RESOURCE_GATE = UNKNOWN`. Hardware values are not measured.

**Commands to run once approved access is provided.** All are read-only. Nothing is installed, edited, restarted or stopped.

```
hostname; uname -a; cat /etc/os-release; nproc
free -h; grep -E 'MemTotal|MemAvailable|MemFree|SwapTotal|SwapFree' /proc/meminfo; swapon --show
df -h /; df -h /var/lib /opt 2>/dev/null; uptime
ps -eo pid,user,%cpu,%mem,rss,etime,command --sort=-rss | head -25
ls -la /opt /srv /var/lib 2>/dev/null; ls -la /opt/sports-models /var/lib/sports-models 2>/dev/null
systemctl list-timers --all --no-pager; systemctl list-units --type=service --no-pager
crontab -l 2>/dev/null; ls -la /etc/cron.d; cat /etc/crontab; timedatectl
```

Report names only, never environment contents. For each timer and cron entry, note whether it fires between 05:30 and 07:30 America/New_York.

**PASS rule** (from `docs/walkforward/DROPLET_RESOURCE_REQUIREMENT_CBB.md`). All must hold:
- MemAvailable ≥ 13 GB at 06:00 ET with every co-resident service in its normal state;
- no co-resident heavy job overlaps 06:00–07:00 ET, or every co-resident heavy job honours the shared lock;
- ≥ 20 GB disk free for CBB: about 12.24 GB frozen stores + 2 GB scratch + 0.5 GB per season + venv.

**FAIL** if total RAM is under 16 GB. 8 GB is UNSAFE. On FAIL, the required upgrade is reported: total RAM ≥ 16 GB if CBB has its morning window to itself, otherwise 13 GB + Σ(co-resident peaks) + an OS margin. Swap is not added and no configuration is changed to compensate.

## 4. Safe-by-default deployment mode

A fresh install uses the environment template unchanged. `cbb-originator-run` **refuses (exit 3)** any deviation:

| setting | default | refusal rule |
|---|---|---|
| `CBB_MODEL_SOURCE` | `FROZEN_OPENING_BASELINE` | anything else. The scheduled tick scores the frozen baseline only. ACTIVE_DAILY scoring needs an architect approval plus a reviewed unit. |
| `ROTOWIRE_ENABLED` | `false` | `true` is refused, with or without `ROTOWIRE_RIGHTS_STATUS=PRODUCTION_APPROVED`. RotoWire is not wired into the scheduled units, and turning it on is a separate reviewed change. |
| `WIZARDOFODDS_PUBLICATION` | `disabled` | anything else. No unit publishes. |
| `AUTOMATIC_WAGERING` | `disabled` | anything else. Code is also `PC.WAGERING_ENABLED = False` and `AUTOMATIC_ACTIONABLE_WAGER = "DISABLED"`. |
| `REAL_MONEY_ACTIONABILITY` | `disabled` | anything else. Code is also `PC.REAL_MONEY_ACTIONABILITY = DISABLED_BY_AVAILABILITY_VALIDATION`. |
| thread variables | `1` | anything else |
| `CBB_EXPECTED_COMMIT` | empty | empty, or a checkout at another commit, or a modified checkout |
| `CBB_SCHEDULER_ROLE` | `DISABLED` | `morning` and `tick` exit 4 and do nothing unless it is `PRIMARY_WRITER`. This is set only at the cutover step. |

**No silent go-live.** Three independent things stop a newly installed service from becoming live:
1. units are installed but not enabled;
2. the writer role defaults to DISABLED;
3. the safe flags are enforced at every start.

**Exit codes**

| code | meaning |
|---|---|
| 0 | success |
| 1 | job failure, including `DAILY_REFIT_STATUS` FAIL or NOT_RUN |
| 2 | frozen-hash gate FAIL in the tick |
| 3 | refused configuration |
| 4 | this host is not the writer |
| 75 | lock or memory unavailable (`FAIL (RESOURCE)`; ACTIVE unchanged) |

## 5. State and artifact portability

The full manifest is `deploy/droplet/STATE_TRANSFER_MANIFEST.json`. The large gitignored stores are listed explicitly.

| what | how the Droplet receives it | required | regenerable |
|---|---|---|---|
| code | fresh `git clone` + `checkout --detach <commit>`. The Mac directory is never copied. | yes | yes (Git) |
| frozen JM1 artifacts (4) | in Git at the commit; checked with sha256 and `prod.run verify-artifacts` | yes | no (frozen) |
| golden fixture `tests/fixtures/golden/prod_equivalence_v1.json` (330,510 B, a5d6f862…) | in Git | yes (validation) | no; never rewritten |
| lockbox canonical `cbb.duckdb` (9,455,546,368 B, c4a0e3a1…) | encrypted transfer from the Mac, then `sha256sum -c` | yes (morning refit) | no |
| Stage 3 `features.duckdb` (2,436,116,480 B, 38e89053…) and `labels.duckdb` (236,990,464 B, 86c77e3c…) | encrypted transfer, then `sha256sum -c`; pinned by the frozen manifest `data_versions` | yes | no |
| OOS `phase1b_v1ref` (80,090,263 B, d0859e48…) and `…_F2023` (27,945,912 B, aeb1d115…) | same; manifest-pinned | yes | no |
| operational working set (state, caches, ledgers; about 13.4 MB tar) | **one** `shadow_tick --restore` from the private encrypted release, after the GitHub writer is disabled. The authoritative copy is the release, not the Mac's stale `data/prod/state`. | yes | no |
| ops-state evidence tree | sparse blobless clone of `ops/cbb-2026-27-shadow-state` | yes | no |
| history extract (126,627,840 B, 0338b32d…) | release asset, only if the canonical is absent | optional | yes |
| walk-forward store | created on the Droplet by `walkforward.daily register-opening`. No daily version exists yet anywhere. From the first PROMOTED version on it is non-regenerable and must be backed up. | yes | only before the first daily version |

**Not transferred:**
- the research canonical `data/canonical/cbb.duckdb` (about 10 GB);
- the research Stage 3 store `modeling/store/*.duckdb`;
- the lockbox parquet export (1.53 GB);
- the Starter Pack;
- the protected QA file;
- the Mac `.venv`.

Production reads none of them. `prod/features` and `walkforward` pass `history_db = PC.HISTORY_CANONICAL` explicitly.

**Data-scope blocker.**
- The lockbox canonical, the Stage 3 stores and the OOS stores are Starter-Pack- and CBBD-derived.
- `docs/OFF_MACHINE_DATA_LICENSE.json` authorizes only the minimized extract and the working set, and only as a private encrypted GitHub release processed by GitHub-hosted runners.
- Copying them to a self-managed Droplet needs an **explicit user/architect scope extension** before any transfer.
- The code's `license_gate` checks only `status == PERMITTED`, not scope, so it does not catch this. The check is procedural.

## 6. Future install procedure (NOT executed)

1. **Accounts.**
   - `groupadd --system sports-models` (once, per the shared policy);
   - `useradd --system --home-dir /var/lib/sports-models/cbb-originator --shell /usr/sbin/nologin --groups sports-models cbb-originator`.
2. **Code.**
   - `git clone --no-checkout https://github.com/Risky-Scout/cbb-originator.git /opt/sports-models/cbb-originator/app`, then `git -C … checkout --detach <commit>`.
   - Record the commit in `CBB_EXPECTED_COMMIT`.
3. **Mount points in the checkout.** These paths are all gitignored, so `git status --porcelain` stays empty; verify that it does.
   - `mkdir -p data/prod modeling/store/duckdb_tmp data/lockbox_2025_26/stage3 modeling/v2_oos`
   - `touch data/lockbox_2025_26/canonical/cbb.duckdb`
4. **State and store directories** per §2. Transfer the stores per §5, but only after the scope extension, and run `sha256sum -c` against the manifest.
5. **Virtual environment.**
   - `python3.12 -m venv /opt/sports-models/cbb-originator/venv`
   - `…/venv/bin/pip install --require-hashes -r app/requirements.lock.txt`
   - `pip check`
6. **Environment file.** `install -m 0640 -o root -g cbb-originator deploy/droplet/env/cbb-originator.env.example /etc/sports-models/cbb-originator.env`. Fill the secrets on the server only.
   - `GH_TOKEN` is a fine-grained token limited to this repository (contents read/write), used for the ops-branch push and the release assets.
   - Git uses it via `gh auth git-credential`.
   - `ROTOWIRE_API_KEY` stays empty.
7. **Units.**
   - Copy the four units to `/etc/systemd/system/`.
   - Run `systemd-analyze verify` on them, and `systemd-analyze calendar` on every `OnCalendar` line, including `--iterations=5` across the DST change.
   - `systemctl daemon-reload`.
   - **Do not enable.**
8. Run `cbb-originator-run preflight` as the service user, through `systemd-run --uid=cbb-originator` with the same properties. It must print `PREFLIGHT OK`.

## 7. Scheduler cutover plan (prepared, NOT executed)

There is exactly one ops-state writer at every moment. The GitHub writer and the Droplet writer never run concurrently.

**Existing collision protections are preserved:**
- the GitHub `concurrency: cbb-shadow-ops-state` group;
- the tick's own `.shadow_tick.lock`;
- **normal** (never force) pushes to `ops/cbb-2026-27-shadow-state`, where a non-fast-forward rejection signals a second writer;
- idempotent evidence commits.

The release-asset upload has **no** compare-and-swap. That is why the order below is mandatory.

**Steps**
1. **Preconditions.** All §8 validation passes. The resource gate is PASS. The data-scope extension is recorded. The architect approves the cutover.
2. **Disable the GitHub scheduled writer.** Run `gh workflow disable "CBB shadow tick"` on `main`. This is a change to the live scheduler and needs explicit authorization at that time.
3. **Verify it is disabled.**
   - `gh workflow view "CBB shadow tick"` shows `disabled_manually`.
   - `gh run list --workflow "CBB shadow tick" --status in_progress` and `--status queued` are both empty.
   - Note the ops-branch tip SHA and `WORKING_SET.json current`.
4. **Restore once on the Droplet.**
   - `CBB_SCHEDULER_ROLE=PRIMARY_WRITER`.
   - One manual `shadow_tick --restore --persist --commit --push` as the service user.
   - Verify the restored tar sha256 equals `WORKING_SET.json current.tar_sha256`, and that the push fast-forwarded the noted tip.
5. **Enable the Droplet timers.** `systemctl enable --now cbb-originator-tick.timer`, and the morning timer only when the morning refit is approved for this host.
6. **Verify exactly one writer.**
   - `systemctl list-timers 'cbb-originator-*'` shows the timers.
   - The GitHub workflow is still disabled.
   - The next ops commits are authored only by the Droplet tick.
   - Watch 2 consecutive ticks.

**Rollback**
1. Disable the Droplet timers and set `CBB_SCHEDULER_ROLE=DISABLED`.
2. Verify no `cbb-originator-tick` is running.
3. Re-enable the GitHub workflow.

The Droplet's `--persist` uploads keep the release current, so the GitHub runner's `--restore` picks up from the Droplet's last state.

## 8. Droplet golden-equivalence validation (first run after migration; no timer before it passes)

1. **Code:** `git -C app rev-parse HEAD` equals the approved commit, and `git status --porcelain` is empty.
2. **Frozen artifacts:** `sha256sum -c` over the 4 JM1 hashes, and `python -m cbb_platform.prod.run verify-artifacts` exits 0.
3. **Transferred state:** `sha256sum -c` over every REQUIRED store in the manifest. The restored working-set tar sha256 equals `WORKING_SET.json`.
4. **Environment:** a fresh venv from the lock (`--require-hashes`, then `pip check`).
5. **Golden test**, as the service user with thread variables at 1:
   - `python -m pytest -q tests/test_prod_golden_equivalence.py tests/test_model_registry.py tests/test_droplet_deploy_package.py`
   - also `python -m pytest -q -rs tests/test_v2_jm1_final_production.py tests/test_walkforward_units.py tests/test_walkforward_fit.py`. With the stores present these run in full, with no skips.
6–10. **Identical outputs.** The golden comparison checks model inputs, the JM1 bank of every game, base-stat predictions, combo predictions, and pricing and board rows.
   - **Platform caveat.** The golden file was written on `darwin-arm64`. On a different platform the test, by its documented contract, does not require bank sha256 equality, and compares floats with `CROSS_PLATFORM_ATOL`. Exact bank-hash identity against the Mac file is therefore not achievable on an x86_64 Linux Droplet.
   - **The exact requirement on the Droplet is:**
     - (a) golden PASS under the documented tolerance;
     - (b) `test_capture_is_deterministic` PASS, meaning a byte-identical repeat on the Droplet;
     - (c) bank sha256 and every float **exactly** equal to a reference capture of the same commit taken on GitHub `ubuntu-24.04` x86_64, when the Droplet platform string is `linux-x86_64`. That makes `compare()` exact.
   - Any other difference is a model change: STOP.
11. **First morning refit dry run** (manual, timers still disabled):
    - `CBB_SCHEDULER_ROLE=PRIMARY_WRITER` on a scratch copy of the state only, or right after cutover step 4, with the architect's approval;
    - record RSS and duration, and confirm `ProtectSystem=strict` produced no denied writes.

## 9. What G4B and the RotoWire rights do and do not block

**G4B** (`G4B_PROSPECTIVE_VALIDATION = ACCUMULATING`) blocks `REAL_MONEY_ACTIONABILITY` only. It does **not** block:
- deploying the CBB application;
- validating the Droplet;
- generating internal non-actionable predictions;
- model monitoring;
- collecting prospective evidence, once data rights permit.

**RotoWire rights** (`UNVERIFIED`) block sustained production use of the RotoWire feed. The deployment therefore defaults to `ROTOWIRE_ENABLED=false`. A one-time authenticated smoke or schema validation does not change the rights status. Packaging and migrating the service is not blocked.

## 10. Readiness definitions

- **SAFE_TO_BEGIN_CBB_DROPLET_MIGRATION** requires software, data, resource and deployment safety: model integrity, a ready package and plans, a resource gate of PASS, and data scope covering the Droplet.
- **LIVE_PRODUCTION_READY** additionally requires:
  - RotoWire rights `PRODUCTION_APPROVED`;
  - G4B PASS;
  - WizardOfOdds access and approval;
  - explicit architect activation decisions for publication and actionability.

Migration-safe therefore never implies live-production-ready.
