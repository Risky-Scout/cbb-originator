# Stage 02 release candidate — deployment and rollback procedure (NOT DEPLOYED)

Candidate `56f3dae71ae50680afdc4df4d799e901a4790b05` (branch `research/root-cause-20261126`; required CI
`cbb-production-ci.yml` run 45930954740, workflow_dispatch, **success**: full regression 1131 passed / 36 skipped).
Staged inert on the Droplet at `/opt/sports-models/cbb-originator/releases/56f3dae….tmp` (root:cbb-originator,
read-only, 850 files = `git ls-tree` of the commit; per-file hashes `candidate_56f3dae….sha256sums`, identical to a
local `git archive` extract). `current` → 9f65e7f and `ROLLBACK_RELEASE` = cae2a02 were NOT changed.

Diff vs deployed 9f65e7f: `cbb_platform/prod/features.py`, `cbb_platform/walkforward/{training_rows,daily}.py`, tests, docs.
No systemd unit, env, wrapper, schema, model, parameter or export change.

## Preconditions (all required)

1. The natural 2026-11-27 receipts for the deployed 46e07f3 warm-up grouping are in and recorded (11:10Z morning:
   warm-up stage receipt, builds vs cutoffs, wall / lock time; 13:30Z poll; 14:10Z scoring) — Stage 1 D1/D2.
   This candidate must not be deployed before them (it would confound that evidence).
2. No `cbb-originator-*` oneshot active (the deploy script aborts otherwise); not inside 11:00–15:00Z or ±10 min of a
   quote tick; MemAvailable ≥ 4 GiB, free disk ≥ 38.5 GB.
3. Staged tree still matches: `cd releases/56f3dae….tmp && find . -type f -print0 | sort -z | xargs -0 sha256sum |
   diff - /var/lib/sports-models/cbb-originator/audit/stage2-20261127/candidate_56f3dae….sha256sums`.
4. Fast-forward `origin/production/root-cause-20261126` to the candidate lineage at deploy time (CI on push re-runs).

## Deploy (root on the Droplet)

```
bash /opt/sports-models/cbb-originator/releases/56f3dae71ae50680afdc4df4d799e901a4790b05.tmp/deploy/droplet/ops/cbb-deploy-release 56f3dae71ae50680afdc4df4d799e901a4790b05
# -> current -> releases/56f3dae…; ROLLBACK_RELEASE = 9f65e7f…; env backed up, CBB_EXPECTED_COMMIT updated
bash /opt/sports-models/cbb-originator/current/deploy/droplet/ops/cbb-install-units      # expect no unit diffs
/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run preflight # expect PREFLIGHT OK (commit 56f3dae)
bash /opt/sports-models/cbb-originator/current/deploy/droplet/ops/cbb-prune-releases     # DRY RUN first; keep cae2a02 via KEEP_RELEASES if wanted
```

Post-deploy checks: release sha256sums INTEGRITY_OK (850 files); next morning warm-up receipt shows
`closure.version = TARGET_TEAM_PLAYER_CLOSURE_V1` in new cache metas, `causal_key` present, no mass supersession
(census predicted 0 of 1 083), warm-up wall and heavy-lock time, and the walk-forward promotion gates.

## Rollback

Code-only and cache-compatible: rows built by the candidate are byte-identical to incumbent rows; 9f65e7f reads
`meta.json` as a whole dict (extra `causal_key` / `build.closure` fields ignored) and skips dot-directories
(`.superseded/`) in the warm-up listing. With no CBB oneshot active:

```
cd /opt/sports-models/cbb-originator
ln -sfn releases/9f65e7f9196c21a15811560d14c9974cb0bb2d7f current.new && mv -T current.new current
echo 56f3dae71ae50680afdc4df4d799e901a4790b05 > ROLLBACK_RELEASE.new && mv -T ROLLBACK_RELEASE.new ROLLBACK_RELEASE
sed -i 's/^CBB_EXPECTED_COMMIT=.*/CBB_EXPECTED_COMMIT=9f65e7f9196c21a15811560d14c9974cb0bb2d7f/' /etc/sports-models/cbb-originator.env
/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run preflight
```

Faster in-place fallback without a code swap: set `CBB_FEATURE_CLOSURE=OFF` in the env file (incumbent full build,
causal-key validation kept).

## If not deploying

`rm -rf /opt/sports-models/cbb-originator/releases/56f3dae….tmp` (inert; the prune script also treats a `<sha>.tmp`
not referenced by `current` / `ROLLBACK_RELEASE` / `KEEP_RELEASES` as deletable).
