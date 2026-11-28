# Stage 07 successor: activation status, blocker and procedure

> **Authoritative candidate:** `7d160b1c7c5a02db834793954924c4ff240a0690`, staged inert at `releases/7d160b1c7c5a02db834793954924c4ff240a0690.tmp` (960 files, hash-identical to `git archive`; list in `audit/stage7-20261127/candidate_7d160b1c….sha256sums`). Ledger key `stage7_universe_bitemporal_lineage_20261127`.
> * It is built on 2ac9117 (Stage 06) and carries the whole Stage 02–07 lineage. **Only one candidate is deployed.**
> * The superseded 2ac9117 tree was removed after the new tree verified. At deploy, also remove the older superseded `.tmp` trees still present: d392832, 93e5cd2, 56f3dae and 25b83ed.
> * The Stage 02–06 preconditions and post-deploy checks still apply. See `../2026-11-27-stage6/DEPLOY_PROCEDURE.md` and the earlier stages.

## Activation status (§12)

Measured 2026-11-27, about 20:46Z:
* Production is still **9f65e7f**, with rollback cae2a02.
* The accepted Stage 02–06 enhancements are staged, not active.

**Blocker: the free-disk deploy precondition.** The authorized procedure (Stages 02–06) requires free disk ≥ **38.5 GB**. The Droplet has **37.95 GB** free (165.3 GB volume).

The procedure permits removing the superseded staging trees, which would free about 0.28 GB. That still leaves about 38.2 GB, short of the gate.

**Correction (storage investigation, `../2026-11-27-storage/STORAGE_ROOT_CAUSE.md` §4).** The "about 1 GB per day" figure was an estimate. The gate is not permanently closed: free space swings with run scratch, and the gate (strictest reading 38.5 GiB = 41,339,060,224 bytes) has passed after every 16:45Z retention pass and after some 04:45Z passes.

**Scheduled activation (owner-authorized 2026-11-28: after the 04:45Z pass, only if CI and every established safeguard pass).**
* **Script:** `/root/cbb-s7-deploy.sh`, sha256 `5aceb74ebb92651afd283c5288ed3bca644b3c2a3a69cf51b44d9e00605dfcdf`; copy at `scripts/cbb-s7-deploy.sh`.
* **Timers:** two transient one-shot timers, `cbb-s7-deploy-7d160b1-w1` (2026-11-28 04:50Z, gives up at 05:40Z) and `-w2` (16:50Z, gives up at 17:25Z). They share a lock and a terminal state file (`/root/cbb-s7-deploy.state`), so at most one deploy can happen. CI 46078107204 was verified green before scheduling.
* **Every attempt re-checks:**
  * `current` is still 9f65e7f; otherwise ABORT_SUPERSEDED, so it never races a later deploy;
  * no other staged `.tmp` candidate exists (ABORT_SUPERSEDED);
  * no cancel file `/root/cbb-s7-deploy.CANCEL`;
  * minute 21–29 or 51–59 (more than 10 min from the :10 / :40 ticks); the 04:50–05:40Z and 16:50–17:25Z windows already lie outside 11–15Z and the refresh margins;
  * no active `cbb-originator-*` oneshot;
  * free disk ≥ 41,339,060,224 bytes;
  * MemAvailable ≥ 4 GiB;
  * the staged tree matches `candidate_7d160b1….sha256sums`.
* **Then, in order:**
  1. `cbb-deploy-release`;
  2. `cbb-install-units`;
  3. preflight (must print PREFLIGHT OK). On failure it rolls back automatically to 9f65e7f (ROLLBACK_RELEASE cae2a02, CBB_EXPECTED_COMMIT restored) and re-runs preflight;
  4. rollback verification (ROLLBACK_RELEASE = 9f65e7f and `sha256sum -c` of its tree);
  5. `cbb-prune-releases` plan, then `--apply` only after a verified rollback.
* **Receipts:** Droplet `audit/stage7-20261127/deploy_receipt.jsonl`; terminal state in `/root/cbb-s7-deploy.state`.
* **To cancel:** `touch /root/cbb-s7-deploy.CANCEL` (or `systemctl stop cbb-s7-deploy-7d160b1-w{1,2}.timer`).
* **Pending operator steps after DEPLOYED (cannot be done from the Droplet):** fast-forward `origin/main` and `origin/production/root-cause-20261126` to 7d160b1 (Stage 05 precondition 4); then the post-deploy checks below after the next 11:10Z morning.
* **If both windows fail the gate,** the state is NOT_DEPLOYED_WINDOWS_EXHAUSTED and the successor stays staged. The blocker is disk headroom (S19-STOR-1…7, NBA tenant growth).

**What was not done:**
* The threshold was not lowered.
* No timer was disabled.
* No data was deleted to open a window.
* No deployment watcher was started.

Re-measured after staging, 2026-11-27 22:40Z: **36.40 GB free** (36,398,166,016 bytes), down about 1.5 GB in 2 h. Removing the four remaining superseded trees would free about 0.28 GB.

The other preconditions were satisfied at measurement:
* no `cbb-originator-*` oneshot active outside the quote due-check;
* MemAvailable 6.1 GB;
* the staged tree hash-verifies.

## Changes at deploy (Stage 07 only; Stage 06 changes: see its procedure)

| change | module | in-place fallback |
|---|---|---|
| Explicit DEGRADED frozen-model substitution. Blocking run reason MODEL_SOURCE_FALLBACK; health `model_source` / `PLAYER_MODEL_SOURCE`; `<store>/manifests/model_source_degraded.jsonl`; tick `scoring.model_identity` | `prod/model_source.py`, `run.py`, `readiness.py`, `production_board.py`, `lifecycle.py`, `shadow_tick.py`, `loader.py` | none needed: identical behaviour while ACTIVE resolves. A DEGRADED run blocks (by design) instead of scoring silently |
| Later-information-state guard in `compact_canonical` (refuses `source_cutoff > as_of`). `run.daily` passes the override only on its already-blocking POST_CUTOFF_DATA path | `prod/features.py`, `run.py` | none: prospective runs read a state acquired before `as_of` |
| `participation_keys` by rule time | `prod/rotowire_feed.py` | none: identical prospectively (sync admits lines only after the rule time) |
| CTX ledger row lineage (`added_at`, `state_version`, `training_cutoff`, `incumbent`); INCUMBENT_MISMATCH / LEDGER_FROM_FUTURE statuses (applied, visible) | `prod/context_adj.py`, `run.py` | none. Legacy rows remain usable prospectively. The next morning's rebuild (Stage 06 C3 tag change) writes lineage on every row |
| Receipt counters F3–F9; `runtime_flags` in the run identity; game ratings `information_state`; `pending_finals_source` | `preseason_prior.py`, `rate_a2.py`, `dev_trend.py`, `engine.py`, `run.py`, `game/forecast.py` | none: lineage only, numerics unchanged |
| `replay_contract.py` (replay tooling only; not imported by any scheduled path) | new | n/a |

* No unit, wrapper, schema, model parameter or `engine_id` change. `runtime_flags` is deliberately outside `engine_id`.
* The game `model_version` and `params_sha256` are unchanged.

## Deploy (root on the Droplet, once the disk gate passes)

```
S=7d160b1c7c5a02db834793954924c4ff240a0690; R=/opt/sports-models/cbb-originator
bash $R/releases/$S.tmp/deploy/droplet/ops/cbb-deploy-release $S
bash $R/current/deploy/droplet/ops/cbb-install-units                 # expect no unit diffs
$R/current/deploy/droplet/bin/cbb-originator-run preflight           # expect PREFLIGHT OK ($S)
rm -rf $R/releases/d392832e579727088f690930f1ac87f29c7867e5.tmp \
       $R/releases/93e5cd278323e5e78fc43a58b8eb2bed68195c4a.tmp $R/releases/56f3dae71ae50680afdc4df4d799e901a4790b05.tmp \
       $R/releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp
```

## Post-deploy checks (Stage 07)

* The first run summary carries:
  * `model_identity.model_source_status.status = OK`;
  * `model_identity.runtime_flags`;
  * `feature_provenance.canonical.information_state.check = OK`;
  * `team_opponent_context.expected_incumbent = af49d60f2175`.
* Before the next morning its ledger status is `INCUMBENT_MISMATCH` (the expected Stage 06 tag change), and after the rebuild it is `OK`. After the rebuild every ledger row has a non-null `added_at`.
* Health: `model_source.PLAYER_MODEL_SOURCE = OK`, contract `PLAYER_MODEL_SOURCE = OK`.
* The first tick's `tick.json` `scoring.model_identity.model_version` equals the ACTIVE daily version.
* The game ratings index carries `information_state.state_version`.

## Rollback

Code-only and state-compatible. The new ledger columns and the degraded log are ignored by older code. Use the Stage 06 rollback block, with this sha in `ROLLBACK_RELEASE`.
