# Stage 04 successor candidate: deployment and rollback procedure

> **Authoritative candidate: 93e5cd2** (= af1998f + release pinning in `cbb-originator-run`; CI 46037886213 green). af1998f was never deployed. Its staging tree was removed 2026-11-27 17:18Z after the bounded watcher was retired with no deploy begun (`audit/stage4-20261127/s4_deploy.log`). Its CI run (45947348593) and hashes are kept as lineage.

The candidate `93e5cd278323e5e78fc43a58b8eb2bed68195c4a` (branch `research/root-cause-20261126`) supersedes the Stage 03 candidate 25b83ed
by lineage. It contains 25b83ed and therefore 56f3dae unchanged, plus Stage 04. Only one candidate is deployed: 93e5cd2. The Stage 02 and Stage 03 post-deploy
checks still apply (see `../2026-11-27-stage3/DEPLOY_PROCEDURE.md`).

Staged inert at `/opt/sports-models/cbb-originator/releases/93e5cd278323e5e78fc43a58b8eb2bed68195c4a.tmp` (root:cbb-originator, read-only).
Hashes: `candidate_93e5cd278323e5e78fc43a58b8eb2bed68195c4a.sha256sums` (884 files; the local `git archive` matches the Droplet tree, identical). A
Droplet copy is in `/var/lib/sports-models/cbb-originator/audit/stage4-20261127/`.

Diff vs 25b83ed: `deploy/droplet/bin/cbb-originator-run` (release pinning), `prod/{roster_omissions,rotowire_feed,rotowire_web_acquire,universe,rotowire_player_states,lifecycle}.py`,
`prod/rotowire_player_states.csv`, `prod/roster_omissions.csv`, tests. There is no systemd unit, env, wrapper, schema, model, parameter or
export-format change. The new output directory `ops/identity/curated_reconciliation/` is created on first use.

## Preconditions (all required)

1. The natural 2026-11-27 receipts for the deployed release are recorded: the 11:10Z morning, the 13:30Z poll and the 14:10Z scoring.
2. No `cbb-originator-*` oneshot is active. The deploy is not inside 11:00–15:00Z, not within ±10 min of a quote tick, and not within 30 min of a
   refresh window (10/13/16/18 ET = 15/18/21/23Z). MemAvailable ≥ 4 GiB and free disk ≥ 38.5 GB.
3. The staged tree still matches:
   `cd releases/93e5cd278323e5e78fc43a58b8eb2bed68195c4a.tmp && find . -type f -print0 | sort -z | xargs -0 sha256sum | LC_ALL=C sort -k2 | diff - /var/lib/sports-models/cbb-originator/audit/stage4-20261127/candidate_93e5cd278323e5e78fc43a58b8eb2bed68195c4a.sha256sums`
4. Fast-forward `origin/production/root-cause-20261126` (and `main`) to the deployed lineage at deploy time.

## Deploy (root on the Droplet)

```
S=93e5cd278323e5e78fc43a58b8eb2bed68195c4a; R=/opt/sports-models/cbb-originator
bash $R/releases/$S.tmp/deploy/droplet/ops/cbb-deploy-release $S
bash $R/current/deploy/droplet/ops/cbb-install-units                 # expect no unit diffs
$R/current/deploy/droplet/bin/cbb-originator-run preflight           # expect PREFLIGHT OK ($S)
rm -rf $R/releases/56f3dae71ae50680afdc4df4d799e901a4790b05.tmp $R/releases/25b83ed8c945573bd1b707a68d1b6518947daa42.tmp   # superseded, inert
```

## Post-deploy checks (Stage 04)

* After the first web acquisition: `ops/identity/curated_reconciliation/latest.json` exists with `status OK`, and `action_required` is 0 or listed.
* `prod/availability/rotowire/events.jsonl` gains `IDENTITY_RERESOLVED_OFF_LIST` events (expected: 21059, 20575, 28337, 22290), once each.
* `rotowire_web_player_map.csv` gains rows with method `EXACT_NAME_ON_BOX_PARTICIPATION_ROSTER` (expected: 28337, 21059, 20575).
* The next morning universe has `ON_CURRENT_ROSTER_BOX_PARTICIPATION` rows. There are no multi-team players.
* Identity-blocked games: Rutgers@Indiana, Wake Forest@LSU and the TCU game are no longer `AVAILABILITY_IDENTITY_UNRESOLVED`. Oregon@UCLA and North
  Alabama@DePaul remain blocked.
* The engine and game-model ids are unchanged. The receipt shows no new blocker.

## Rollback

Code-only and state-compatible. The new events, map rows and `ops/identity` files are additive and are ignored or tolerated by earlier
releases: the map rows are ordinary explicit map rows, and the events are ordinary append-only events. With no CBB oneshot active:

```
cd /opt/sports-models/cbb-originator
ln -sfn releases/9f65e7f9196c21a15811560d14c9974cb0bb2d7f current.new && mv -T current.new current
echo 93e5cd278323e5e78fc43a58b8eb2bed68195c4a > ROLLBACK_RELEASE.new && mv -T ROLLBACK_RELEASE.new ROLLBACK_RELEASE
sed -i 's/^CBB_EXPECTED_COMMIT=.*/CBB_EXPECTED_COMMIT=9f65e7f9196c21a15811560d14c9974cb0bb2d7f/' /etc/sports-models/cbb-originator.env
/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run preflight
```

Rolling back restores the 9f65e7f curated tables. That brings back the lapsed or mismatched rows, so the 5 games block again, which is fail-closed.
