# Stage 03 successor candidate — deployment and rollback procedure (NOT DEPLOYED)

The successor candidate `25b83ed8c945573bd1b707a68d1b6518947daa42` (branch `research/root-cause-20261126`) supersedes the Stage 02 candidate 56f3dae by
lineage. It contains 56f3dae unchanged, plus the Stage 03 quote-wake export deferral. Only one candidate is deployed: the
successor. The `56f3dae….tmp` staging tree becomes deletable once the successor is staged. Its evidence and lineage stay in
`docs/audits/2026-11-27-stage2/` and in the ledger.

Staged inert at `/opt/sports-models/cbb-originator/releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp` (root:cbb-originator, read-only; per-file
hashes `/var/lib/sports-models/cbb-originator/audit/stage3-20261127/candidate_25b83ed8c945573bd1b707a68d1b6518947daa42.sha256sums`).
`current` → 9f65e7f and `ROLLBACK_RELEASE` = cae2a02 are unchanged.

Diff vs deployed 9f65e7f: the Stage 02 set (`prod/features.py`, `walkforward/{training_rows,daily}.py`) plus
`prod/lifecycle.py` (quotes), tests and docs. There is no systemd unit, env, wrapper, schema, model, parameter or export-format change.

## Preconditions (all required)

1. The natural 2026-11-27 receipts for the deployed 46e07f3 are recorded: the 11:10Z morning (warm-up receipt, builds vs
   cutoffs, wall and lock time), the 13:30Z poll and the 14:10Z scoring.
2. No `cbb-originator-*` oneshot is active. The deploy is not inside 11:00–15:00Z, not within ±10 min of a quote tick, and not
   within 30 min of a refresh window (10/13/16/18 ET). MemAvailable ≥ 4 GiB and free disk ≥ 38.5 GB.
3. The staged tree still matches:
   `cd releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp && find . -type f -print0 | sort -z | xargs -0 sha256sum | LC_ALL=C sort -k2 | diff - <(LC_ALL=C sort -k2 /var/lib/sports-models/cbb-originator/audit/stage3-20261127/candidate_25b83ed8c945573bd1b707a68d1b6518947daa42.sha256sums)`
4. Fast-forward `origin/production/root-cause-20261126` to the successor at deploy time. CI re-runs on push.

## Deploy (root on the Droplet)

```
bash /opt/sports-models/cbb-originator/releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp/deploy/droplet/ops/cbb-deploy-release 25b83ed8c945573bd1b707a68d1b6518947daa42
bash /opt/sports-models/cbb-originator/current/deploy/droplet/ops/cbb-install-units      # expect no unit diffs
/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run preflight # expect PREFLIGHT OK (25b83ed8c945573bd1b707a68d1b6518947daa42)
rm -rf /opt/sports-models/cbb-originator/releases/56f3dae71ae50680afdc4df4d799e901a4790b05.tmp   # superseded, inert
```

Post-deploy checks:

* Stage 02: new cache metas carry `closure.version = TARGET_TEAM_PLAYER_CLOSURE_V1` and `causal_key`, with no mass supersession.
  Record the warm-up wall and heavy-lock time.
* Stage 03: at the first refresh window, health has `exports_deferred` (DEFERRED_TO_AVAILABILITY_REFRESH),
  `PHASE_B_LOCK_WAIT_SEC` is well below the 146 s median, the refresh ends PUBLISHED, and the next wake rebuilds exports normally.

## Rollback

Code-only and state-compatible. The new health keys (`exports_deferred`, `quote_refresh_totals.exports_deferred`) are
additive and ignored by 9f65e7f. The Stage 02 cache compatibility is unchanged (see the stage2 procedure). With no CBB oneshot active:

```
cd /opt/sports-models/cbb-originator
ln -sfn releases/9f65e7f9196c21a15811560d14c9974cb0bb2d7f current.new && mv -T current.new current
echo 25b83ed8c945573bd1b707a68d1b6518947daa42 > ROLLBACK_RELEASE.new && mv -T ROLLBACK_RELEASE.new ROLLBACK_RELEASE
sed -i 's/^CBB_EXPECTED_COMMIT=.*/CBB_EXPECTED_COMMIT=9f65e7f9196c21a15811560d14c9974cb0bb2d7f/' /etc/sports-models/cbb-originator.env
/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run preflight
```

The in-place fallback for Stage 02 alone is still available: set `CBB_FEATURE_CLOSURE=OFF`. The Stage 03 deferral has no flag. It is inert
unless a live refresh marker exists, and a rollback removes it.

## If not deploying

`rm -rf /opt/sports-models/cbb-originator/releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp` (inert).
