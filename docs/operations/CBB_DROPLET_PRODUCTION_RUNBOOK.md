# CBB Originator: Droplet production runbook

The Droplet (`root@143.198.43.248`) is the **sole CBB production writer** (`CBB_SCHEDULER_ROLE=PRIMARY_WRITER`).
The GitHub "CBB shadow tick" workflow stays `disabled_manually` while any Droplet CBB timer is enabled. Never run
both writers. NBA (`/opt/nba-prop-runtime`, `/srv/nba-production`), Massey NCAAF / NFL, and every other model on the host
are out of scope. CBB units list them under `InaccessiblePaths=` and never touch them.

## 1. Directory layout

```
/opt/sports-models/cbb-originator/            code (root-owned)
  current -> releases/<sha>                   the active release (atomic symlink swap)
  releases/<sha>/                             immutable `git archive` of <sha>, dr-xr-xr-x root:cbb-originator
  releases/<sha>.sha256sums                   root-owned manifest "<sha256>  ./path"; the runner re-verifies the
                                              hashes and the file count before every job
  venv/                                       shared Python env (reused while requirements are unchanged)
/etc/sports-models/cbb-originator.env         root:cbb-originator 0640, never world-readable
                                              (CBB_EXPECTED_COMMIT, ROTOWIRE_*, lock waits; API keys)
/var/lib/sports-models/cbb-originator/        mutable state (cbb-originator)
  prod/                                       production state, ops evidence (prod/ops), health (prod/health/<date>.json),
                                              markers (prod/ops/markers), walk-forward attempt logs
  walkforward/cbb-originator/                 walk-forward store; training/features/<game_id>/ = immutable per-game
                                              pregame feature cache (first writer wins, os.replace)
  exports/<date>/                             Boards / Distributions / Grades
  history/cbb_production_history_minimal.duckdb   frozen history extract (HX.resolve())
  secrets/ (2700)  rotowire_web.cookies (0600)    RotoWire WEB session; never in Git, logs, docs or /tmp
  staging/tmp/duckdb/                         DuckDB spill (per-pid, removed at exit)
/run/lock/sports-models/heavy.lock            host-wide heavy-job lock shared with the other models
```

## 2. Lifecycle (systemd, America/New_York)

| unit (timer) | when | locks | memory (High / Max) |
|---|---|---|---|
| `cbb-originator-features-warmup` | 01:30 | heavy lock only (150-min budget; no fit / forecast / manifest) | 2800M / 3200M |
| `cbb-originator-morning` | 07:00 | CBB writer lock; heavy lock inside walk-forward | 2800M / 3200M |
| `cbb-originator-refit-retry` | 08:00 | as morning | 2800M / 3200M |
| `cbb-originator-availability-poll` (`-0830` timer) | 08:30 | writer lock; RotoWire acquisition only | 1000M / 1400M |
| `cbb-originator-availability@1000/1300/1600/1800` | 10:00, 13:00, 16:00, 18:00 | writer lock, never heavy | 2800M / 3200M |
| `cbb-originator-quotes` | every 10 min | writer lock; official tick + reprice of persisted PMFs, never fits | 2800M / 3200M |

Availability refreshes re-forecast only pre-cutoff games whose availability changed (the targeted reforecast path).
They never retrain. The quotes tick and the availability refreshes build full-state pregame features before scoring. They
carry the heavy-unit memory ceiling (2026-11-09: a 1800M/2200M quotes cgroup throttled, then OOM-killed after the
season state grew). Writer-lock holders never overlap one another. The 01:30 warmup window has no scoring cutoffs.

## 3. Deploy a release (atomic)

```
sha=<commit on production/cbb-daily-walk-forward-2026-27, pushed, CI green>
R=/opt/sports-models/cbb-originator
git archive $sha | (mkdir $R/releases/$sha.tmp && tar -x -C $R/releases/$sha.tmp)
(cd $R/releases/$sha.tmp && find . -type f -print0 | sort -z | xargs -0 sha256sum) > $R/releases/$sha.sha256sums
mv $R/releases/$sha.tmp $R/releases/$sha
chown -R root:cbb-originator $R/releases/$sha
chmod -R a-w,g+rX,o-rwx $R/releases/$sha
cp -p /etc/sports-models/cbb-originator.env /etc/sports-models/cbb-originator.env.bak-<date>-pre-<sha8>
set CBB_EXPECTED_COMMIT=$sha in the env file
ln -sfn releases/$sha $R/current.new && mv -T $R/current.new $R/current
install -m 0644 $R/current/deploy/droplet/systemd/cbb-originator-* /etc/systemd/system/ && systemctl daemon-reload
cbb-originator-run preflight
```

## 4. Rollback

1. `systemctl stop 'cbb-originator-*.timer'`, then wait for any running `cbb-originator-*` service to finish.
2. `ln -sfn releases/<previous sha> current.new && mv -T current.new current`.
3. Restore the matching env backup (`CBB_EXPECTED_COMMIT=<previous sha>`). For RotoWire only, set
   `ROTOWIRE_ENABLED=false` instead.
4. Reinstall that release's units from `current/deploy/droplet/systemd/`, then `systemctl daemon-reload`.
5. Run preflight, then re-enable the timers of that release.

State under `/var/lib/sports-models/cbb-originator` is append-only / versioned and is never rolled back by a code rollback.

## 5. Daily health receipt

`prod/health/<date>.json` → `receipt`: DATE, RELEASE, MODEL_VERSION, MORNING_REFIT_STATUS, MODEL_PROMOTION,
FORECAST_STATUS / GAME_COUNT / PLAYER_COUNT, PMF_COUNT, ROTOWIRE_AUTH / LAST_SUCCESS / NEWS_OVERLAP,
QUOTE_REFRESH_STATUS, REAL_QUOTES, BOARD / DISTRIBUTIONS / GRADES_STATUS, IDENTITY_BLOCKED_GAMES, WRITER_COUNT,
HEAVY_LOCK_STATUS, ERRORS, WARNINGS, FINAL_STATUS. No board is fabricated: without real quotes the board status is
`NONE_NO_REAL_QUOTES`. Grades use official results only.

## 6. Checks

* `systemctl list-timers 'cbb-originator-*'`: next runs.
* `journalctl -u cbb-originator-<unit>`: per-unit logs (journald retention).
* `journalctl -k | grep -i "killed process"`: OOM events must be 0.
* `gh api repos/Risky-Scout/cbb-originator/actions/workflows` → CBB shadow tick `disabled_manually` (WRITER_COUNT = 1).
