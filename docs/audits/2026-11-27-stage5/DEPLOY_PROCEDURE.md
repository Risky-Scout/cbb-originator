# Stage 05 successor candidate: deployment and rollback procedure

> **Authoritative candidate: d392832** (Stage 05 incl. closure; supersedes 594ec8b, never deployed, tree removed 2026-11-27 19:05Z). It is 93e5cd2 (Stage 04 + release pinning) plus Stage 05: C1 verified curated
> non-participation in `prod/rotowire_feed.availability_frame` / `prod/rotowire_player_states`, and C2 the game-layer news-free availability
> baseline in `game/forecast.py` (`CBB_GAME_AVAILABILITY_BASELINE=INITIAL` = in-place fallback, only with C3 OFF), and C3 INITIAL / REFIT_RETRY consume the knowable
> availability (`prod/lifecycle.py`; `CBB_INITIAL_AVAILABILITY=OFF` = in-place fallback). It supersedes 93e5cd2 by lineage. Only one candidate
> is deployed: d392832. The Stage 02/03/04 post-deploy checks still apply (`../2026-11-27-stage4/DEPLOY_PROCEDURE.md`). At deploy, also remove
> `releases/93e5cd278323e5e78fc43a58b8eb2bed68195c4a.tmp` (with 56f3dae.tmp and 25b83ed.tmp).
>
> Stage 05 post-deploy checks:
> * the first RotoWire-window forecast's `model_inputs.parquet` has `availability_source = CURATED_VERIFIED_STATE` rows for in-force
>   verified players present in the pool (expected among 20354/20395/20474/20532/20703/20936/21070/21195/21227/26154/26727);
> * those rows have `player_suppression = CONFIRMED_UNAVAILABLE`, and the players have no PMF;
> * `game_forecast.json` reports `availability_baseline: NEWS_FREE`;
> * the game PMFs are coherent and the identity-blocked games are unchanged;
> * the next morning's INITIAL `snapshot.json` has `availability_source: ENABLED` and no `rotowire_refresh` key, and its
>   `forecast_players.parquet` carries `CONFIRMED_UNAVAILABLE` rows for players OUT in the event store before 07:00 ET.

Staged inert at `/opt/sports-models/cbb-originator/releases/d392832e579727088f690930f1ac87f29c7867e5.tmp` (root:cbb-originator, read-only).
Hashes: `candidate_d392832e579727088f690930f1ac87f29c7867e5.sha256sums` (921 files; the local `git archive` matches the Droplet tree, identical). A
Droplet copy is in `/var/lib/sports-models/cbb-originator/audit/stage5-20261127/`.

Diff vs 25b83ed (cumulative): `game/forecast.py`, `game/model.py` (Stage 05), `deploy/droplet/bin/cbb-originator-run` (release pinning), `prod/{roster_omissions,rotowire_feed,rotowire_web_acquire,universe,rotowire_player_states,lifecycle}.py`,
`prod/rotowire_player_states.csv`, `prod/roster_omissions.csv`, tests. There is no systemd unit, env, wrapper, schema, model, parameter or
export-format change. The new output directory `ops/identity/curated_reconciliation/` is created on first use.

## Preconditions (all required)

1. The natural 2026-11-27 receipts for the deployed release are recorded: the 11:10Z morning, the 13:30Z poll and the 14:10Z scoring.
2. No `cbb-originator-*` oneshot is active. The deploy is not inside 11:00–15:00Z, not within ±10 min of a quote tick, and not within 30 min of a
   refresh window (10/13/16/18 ET = 15/18/21/23Z). MemAvailable ≥ 4 GiB and free disk ≥ 38.5 GB.
3. The staged tree still matches:
   `cd releases/d392832e579727088f690930f1ac87f29c7867e5.tmp && find . -type f -print0 | sort -z | xargs -0 sha256sum | LC_ALL=C sort -k2 | diff - /var/lib/sports-models/cbb-originator/audit/stage5-20261127/candidate_d392832e579727088f690930f1ac87f29c7867e5.sha256sums`
4. Fast-forward `origin/production/root-cause-20261126` (and `main`) to the deployed lineage at deploy time.

## Deploy (root on the Droplet)

```
S=d392832e579727088f690930f1ac87f29c7867e5; R=/opt/sports-models/cbb-originator
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
echo d392832e579727088f690930f1ac87f29c7867e5 > ROLLBACK_RELEASE.new && mv -T ROLLBACK_RELEASE.new ROLLBACK_RELEASE
sed -i 's/^CBB_EXPECTED_COMMIT=.*/CBB_EXPECTED_COMMIT=9f65e7f9196c21a15811560d14c9974cb0bb2d7f/' /etc/sports-models/cbb-originator.env
/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run preflight
```

Rolling back restores the 9f65e7f curated tables. That brings back the lapsed or mismatched rows, so the 5 games block again, which is fail-closed.

Rolling back also restores the news-free INITIAL / REFIT_RETRY snapshots and the INITIAL game-layer baseline. The two in-place fallbacks are env-only
(`CBB_INITIAL_AVAILABILITY=OFF` together with `CBB_GAME_AVAILABILITY_BASELINE=INITIAL` in `/etc/sports-models/cbb-originator.env`) and need no code rollback.
