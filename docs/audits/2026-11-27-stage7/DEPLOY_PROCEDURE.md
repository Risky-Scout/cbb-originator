# Stage 07 successor: activation status, blocker and procedure

> **Authoritative candidate:** the Stage 07 successor commit recorded in the ledger key `stage7_universe_bitemporal_lineage_20261127`.
> * It is built on 2ac9117 (Stage 06) and carries the whole Stage 02–07 lineage. **Only one candidate is deployed.**
> * At deploy, also remove the superseded `.tmp` trees still present: 2ac9117, d392832, 93e5cd2, 56f3dae and 25b83ed.
> * The Stage 02–06 preconditions and post-deploy checks still apply. See `../2026-11-27-stage6/DEPLOY_PROCEDURE.md` and the earlier stages.

## Activation status (§12)

Measured 2026-11-27, about 20:46Z:
* Production is still **9f65e7f**, with rollback cae2a02.
* The accepted Stage 02–06 enhancements are staged, not active.

**Blocker: the free-disk deploy precondition.** The authorized procedure (Stages 02–06) requires free disk ≥ **38.5 GB**. The Droplet has **37.95 GB** free (165.3 GB volume).

The procedure permits removing the superseded staging trees, which would free about 0.28 GB. That still leaves about 38.2 GB, short of the gate.

Free space falls by about 1 GB per day:
* about 25–35 immutable state versions per day at about 19 MB each;
* plus forecasts, runs and exports.

The 4.9 GB of retained state versions is replay evidence and must not be pruned (Stage 07 §5). The gate will therefore not reopen without one of:
* **Volume expansion**, or another owner-approved change in capacity. This is the owner's decision.
* **An owner-approved retention change for non-evidence data**, assigned to Stage 19. Examples: compacted run directories already migrated (`market_pmfs.ref.json`) and old export copies. The immutable state versions and the PMF evidence are not candidates.
* **An explicit owner revision of the 38.5 GB gate.** Stage 07 does not lower it (§12, "do not lower resource thresholds").

**What was not done:**
* The threshold was not lowered.
* No timer was disabled.
* No data was deleted to open a window.
* No deployment watcher was started.

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
S=<stage 7 successor sha>; R=/opt/sports-models/cbb-originator
bash $R/releases/$S.tmp/deploy/droplet/ops/cbb-deploy-release $S
bash $R/current/deploy/droplet/ops/cbb-install-units                 # expect no unit diffs
$R/current/deploy/droplet/bin/cbb-originator-run preflight           # expect PREFLIGHT OK ($S)
rm -rf $R/releases/2ac9117e25a92fcc090f2a5da4584a5248213e90.tmp $R/releases/d392832e579727088f690930f1ac87f29c7867e5.tmp \
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
