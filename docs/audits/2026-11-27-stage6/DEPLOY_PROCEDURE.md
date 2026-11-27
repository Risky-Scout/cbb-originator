# Stage 06 successor candidate: deployment and rollback procedure

> **Authoritative candidate:** the Stage 06 successor commit recorded in the ledger key `stage6_math_provenance_20261127`.
> * It is built on d392832 (Stage 05 incl. closure) and supersedes d392832 by lineage. **Only one candidate is deployed.**
> * At deploy, also remove `releases/d392832e579727088f690930f1ac87f29c7867e5.tmp`, together with the 93e5cd2 / 56f3dae / 25b83ed `.tmp` trees if they are still present.
> * The Stage 02–05 preconditions and post-deploy checks still apply (`../2026-11-27-stage5/DEPLOY_PROCEDURE.md`, `../2026-11-27-stage4/DEPLOY_PROCEDURE.md`).
> * Engineering completion is not activation. This deploy needs its own approved window.

## What changes at deploy

| change | module | in-place fallback |
|---|---|---|
| Grades 2nd worksheet `Success by Stat and Role`: populations (forecast rows / market-matched / positive EV / screen pass / official eligible selections) plus success by stat and by player role, for market-matched rows and for official selections | `prod/market_exports.py`, `prod/exports.py` | none needed (additive sheet; every existing sheet is kept, by name) |
| CTX1 ledger incumbent includes PS2 (tag a4b0cb644c60 → af49d60f2175) | `prod/engine.py` | none. Rolling back the code restores the old tag, and the next morning rebuilds again |
| Game PMF means matched on the regulation PMF; OT adds its expectation (joint 1.1.0) | `game/joint.py` | `CBB_GAME_MEAN_BASIS=FINAL` in the env file (joint 1.0.0) |
| Daily ratings reuse keyed on prior + fit params; β ≠ 0 fails closed; carry lineage; `endgame_ot_manifest_sha256` and `mean_basis` columns | `game/forecast.py`, `game/model.py`, `game/params.py` | none needed |

There is no systemd unit, wrapper, schema, player-model, player-parameter or public-contract change.
* The player `engine_id` is unchanged.
* The game `model_version` stays `cbb-game-model-1.2.0` (the sealed `params_sha256` is unchanged). The construction is identified per row by `joint_version` = `cbb-game-joint-1.1.0` and `mean_basis` = `REGULATION`.

## Operational effect to plan for

**The first morning walk-forward after the deploy rebuilds the whole CTX1 context ledger.** The tag changes, so every 2026-27 game is re-scored at
2,000 draws.
* The 2026-11-26 seeding (1,079 games) took the walk-forward from about 24 to 51 CPU-minutes and still finished on time. Expect a similar cost once, on about 1,100–1,150 games.
* Deploy after that day's morning chain. The rebuild then happens at the next 11:10Z morning, with the 08:00 ET recovery available.

## Preconditions

The Stage 05 preconditions 1–4 apply, with the Stage 06 tree and hash file substituted. In short:
* No `cbb-originator-*` oneshot is active.
* The deploy is outside 11:00–15:00Z, ±10 min of a quote tick, and 30 min of a refresh window.
* MemAvailable ≥ 4 GiB; free disk ≥ 38.5 GB.
* The staged tree matches `candidate_<sha>.sha256sums`.

## Deploy (root on the Droplet)

```
S=<stage 6 successor sha>; R=/opt/sports-models/cbb-originator
bash $R/releases/$S.tmp/deploy/droplet/ops/cbb-deploy-release $S
bash $R/current/deploy/droplet/ops/cbb-install-units                 # expect no unit diffs
$R/current/deploy/droplet/bin/cbb-originator-run preflight           # expect PREFLIGHT OK ($S)
rm -rf $R/releases/d392832e579727088f690930f1ac87f29c7867e5.tmp $R/releases/93e5cd278323e5e78fc43a58b8eb2bed68195c4a.tmp \
       $R/releases/56f3dae71ae50680afdc4df4d799e901a4790b05.tmp $R/releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp
```

## Post-deploy checks (Stage 06)

* The first game forecast after the swap carries `joint_version = cbb-game-joint-1.1.0`, `mean_basis = REGULATION`, and a non-empty
  `endgame_ot_manifest_sha256` in `game_pmfs.parquet`. Its `ratings_source` is `REUSED_DAILY_STATE`, because the prior and params are unchanged.
* Per game, the final PMF mean exceeds the target by `P(regulation tie) × E[OT points]`, about 0.5–0.8 per team. The coherence check passes.
* The next morning: walk-forward `context ledger: {... 'incumbent': 'af49d60f2175', 'added_games': ~all ...}`. Then promotion PASS, and CTX1 status OK in the receipt.
* The next export folder D: `CBB_Grades_<D-1>.xlsx`'s second sheet is `Success by Stat and Role`. Its `MARKET_MATCHED_ROWS` equals the Grades row count,
  and `OFFICIAL_ELIGIBLE_SELECTIONS` is 0 while actionability is not validated.
* Re-run `scripts/s6_verify_exports.py` on that folder (local copy): `failed_checks` must be `[]`.

## Rollback

The rollback is code-only and state-compatible. With no CBB oneshot active, use the Stage 05 rollback block with the Stage 06 sha in `ROLLBACK_RELEASE`, or roll
forward to d392832 if it is still staged.
* The rebuilt ledger (tag af49d60f2175) is rebuilt again under the old tag by the next morning.
* The game construction fallback alone is env-only: `CBB_GAME_MEAN_BASIS=FINAL`.
