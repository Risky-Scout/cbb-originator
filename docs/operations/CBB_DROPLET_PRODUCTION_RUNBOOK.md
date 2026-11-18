# CBB Originator: Droplet production runbook

The Droplet (`root@143.198.43.248`) is the **sole CBB production writer** (`CBB_SCHEDULER_ROLE=PRIMARY_WRITER`).
The GitHub "CBB shadow tick" workflow stays `disabled_manually` while any Droplet CBB timer is enabled. Never run
both writers. NBA (`/opt/nba-prop-runtime`, `/srv/nba-production`), Massey NCAAF / NFL, and every other model on the host
are out of scope. CBB units list them under `InaccessiblePaths=` and never touch them.

## 1. Directory layout

```
/opt/sports-models/cbb-originator/            code (root-owned)
  current -> releases/<sha>                   the active release (atomic symlink swap)
  releases/<sha>/                             immutable `git archive` of <sha>, root:cbb-originator: directories 0555
                                              (dr-xr-xr-x), files 0444 (-r--r--r--), deploy/droplet/bin/cbb-originator-run 0555
  releases/<sha>.sha256sums                   root:cbb-originator 0444 manifest "<sha256>  ./path"; the runner re-verifies the
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
| `cbb-originator-morning.target` (`cbb-originator-morning` timer) | 06:10 | the six-stage chain below (Requires= + After=), 10 min after the 06:00 training cutoff | per stage |
| `cbb-originator-morning-prep` | stage 1/6 | writer lock (taken by the stage), never heavy; D-1 sync + settlement tick, prior-day Grades | 2800M / 3200M |
| `cbb-originator-morning-warmup` | stage 2/6 | heavy lock ONLY (no writer lock; 150-min budget); feature cache over the frozen cutoff state; advisory (always exit 0) | 2800M / 3200M |
| `cbb-originator-walkforward` | stage 3/6 | writer lock + heavy lock (fit step only); After= warmup, Requires= prep only | 2800M / 3200M |
| `cbb-originator-morning-forecast` | stage 4/6 | writer lock, never heavy; INITIAL on the same-day model, HELD while a walk-forward retry remains | 2800M / 3200M |
| `cbb-originator-morning-exports` | stage 5/6 | writer lock, never heavy; exports from persisted evidence | 2800M / 3200M |
| `cbb-originator-morning-health` | stage 6/6 | writer lock, never heavy; Wants= (always runs) | 600M / 800M |
| `cbb-originator-morning-recover` | 08:00 | writer lock, never heavy; After= every stage (never overlaps) | 600M / 800M |
| `cbb-originator-availability-poll` (`-0830` timer) | 08:30 | writer lock; RotoWire acquisition only | 1000M / 1400M |
| `cbb-originator-availability@1000/1300/1600/1800` | 10:00, 13:00, 16:00, 18:00 | writer lock, never heavy | 2800M / 3200M |
| `cbb-originator-quotes` | every 10 min | writer lock; official tick + reprice of persisted PMFs, never fits | 2800M / 3200M |
| `cbb-originator-retention` | 05:45 | writer lock, never heavy; run-scratch retention (below) | 500M / 800M |

Morning stages (`cbb_platform/prod/morning_stages.py`; 2026-11-13 incident: the single 07:00 process promoted the day's
model, then was OOM-killed in the INITIAL forecast). Each stage is a fresh process in its own cgroup and exits before
the next starts. Each writes a durable receipt to `prod/ops/morning/<D>/progress.json` (atomic replace; statuses NOT_STARTED /
RUNNING / PASS / DEFERRED / FAIL, PID, START/PEAK/END RSS, cgroup `memory.peak`), plus `stage_log.jsonl`. The
ExecStopPost `morning-finalize` records SERVICE_RESULT (oom-kill / timeout / signal) for a stage killed before its
receipt. A walk-forward killed after promotion is reconstructed as PASS from the model store
(`walkforward.store.same_day_promotion`), never refit. The 08:00 recovery evaluates the whole state machine. If the
morning is complete it records ALREADY_COMPLETE. Otherwise it writes `prod/ops/morning/RESUME_REQUEST`, and its root
`ExecStartPost=+` (`deploy/droplet/bin/cbb-originator-morning-resume`) re-starts the same idempotent chain, which skips
PASS stages, at most twice. Every retry trains as of the original `D 06:00 ET` cutoff. Inspect:
`jq . /var/lib/sports-models/cbb-originator/prod/ops/morning/$(TZ=America/New_York date +%F)/progress.json`.

Forecast memory (measured 2026-11-13 in a clean QA process with production limits, 55-game INITIAL slate). The frozen
`structural.mixture_tables` built every table row of a 20,000-draw game at once, a transient of about 2.1 GB per game. The
pre-fix forecast reached at least 3,283 MiB VmHWM and never finished under throttling. `prod/simulate.bounded_mixture_tables`
now assembles the identical tables from the frozen function on two-player column blocks. The tables are byte-identical
(`tests/test_prod_bounded_tables.py`), and the frozen files stay hash-pinned. The full slate then finishes at 2,463 MiB.
The forecast stage unit also pins glibc `MALLOC_MMAP_THRESHOLD_` / `MALLOC_TRIM_THRESHOLD_` / `MALLOC_ARENA_MAX`. This
stops heap baseline creep across games: 1,971 MiB, with PMFs bit-identical on all 1,647,530 rows.
The walk-forward unit pins the same allocator settings. On the standalone 11-13 replay, VmHWM drops from 3,081 to 2,856 MiB,
and every model artifact is byte-identical to production. Its fit 1 (`marginal.fit_v0`) peak remains above MemoryHigh
(throttled, not killed) and grows with the season's training rows. Watch `MORNING_STAGE_MEMORY.WALK_FORWARD` in the
health receipt.

Availability refreshes re-forecast only pre-cutoff games whose availability changed (the targeted reforecast path).
They never retrain. The quotes tick and the availability refreshes build full-state pregame features before scoring. They
carry the heavy-unit memory ceiling (2026-11-09: a 1800M/2200M quotes cgroup throttled, then OOM-killed after the
season state grew). Writer-lock holders never overlap one another. The 06:10-07:30 warmup window has no scoring cutoffs (earliest T-60
official predictions are ~10:00 ET), so a warmup and a quotes tick never both build full-state features.

Run-scratch retention (`cbb_platform/prod/retention.py`, `cbb-originator-run prune-scratch [--apply]`): deletes ONLY
`prod/runs/<run>/features/*.duckdb` (~530 MB per run) of runs that completed (valid `run_summary.json`) and whose PMFs
are in durable evidence (`ops/ticks/*/pmfs.parquet` or `ops/forecasts/*/*/pmfs.parquet`; or 0 predictions). The latest
2 completed runs and every completed run of the last 24 h are kept, whichever keeps more. Incomplete / unverified runs and
every other file (summaries, PMFs, inputs, universe, boards, bundles, ops evidence, state, models, exports, health,
audit evidence) are never deleted. A dry-run report (`ops/retention/reports/<ts>.json`) is written before any deletion;
each deleted file and the reclaimed total go to `ops/retention/scratch_prune.jsonl`. Without `--apply`: report only.
The same pass then runs the bounded-growth rules of `prod/storage_retention.py` (report `ops/retention/reports/<ts>-storage.json`,
receipts `ops/retention/storage_retention.jsonl`); full policy and directory map: `docs/operations/CBB_STORAGE_AND_RETENTION.md`.

## 3. Deploy a release (atomic)

Supported tooling (root, from the release tree; `docs/operations/CBB_STORAGE_AND_RETENTION.md` §1):

```
git archive $sha | gzip -1 | ssh root@<droplet> "mkdir $R/releases/$sha.tmp && gunzip | tar -x -C $R/releases/$sha.tmp"
bash $R/current/deploy/droplet/ops/cbb-deploy-release $sha     # aborts while any cbb-originator unit is running
bash $R/current/deploy/droplet/ops/cbb-install-units
sudo -u cbb-originator ... cbb-originator-run preflight
bash $R/current/deploy/droplet/ops/cbb-prune-releases           # dry run; then --apply (keeps current, ROLLBACK_RELEASE, KEEP_RELEASES)
```

The equivalent manual steps:

```
sha=<commit on production/cbb-daily-walk-forward-2026-27, pushed, CI green>
R=/opt/sports-models/cbb-originator
git archive $sha | (mkdir $R/releases/$sha.tmp && tar -x -C $R/releases/$sha.tmp)
(cd $R/releases/$sha.tmp && find . -type f -print0 | sort -z | xargs -0 sha256sum) > $R/releases/$sha.sha256sums
mv $R/releases/$sha.tmp $R/releases/$sha
chown -R root:cbb-originator $R/releases/$sha $R/releases/$sha.sha256sums
chmod -R a-w,a+rX $R/releases/$sha            # dirs 0555, files 0444, the executable runner 0555
chmod 0444 $R/releases/$sha.sha256sums
find $R/releases/$sha -printf '%M %y\n' | sort | uniq -c   # expect only dr-xr-xr-x d, -r--r--r-- f, -r-xr-xr-x f (runner)
cp -p /etc/sports-models/cbb-originator.env /etc/sports-models/cbb-originator.env.bak-<date>-pre-<sha8>
set CBB_EXPECTED_COMMIT=$sha in the env file
ln -sfn releases/$sha $R/current.new && mv -T $R/current.new $R/current
install -m 0644 $R/current/deploy/droplet/systemd/cbb-originator-* /etc/systemd/system/ && systemctl daemon-reload
cbb-originator-run preflight
```

## 4. Rollback

1. `systemctl stop 'cbb-originator-*.timer'`, then wait for any running `cbb-originator-*` service to finish.
2. `ln -sfn releases/$(cat ROLLBACK_RELEASE) current.new && mv -T current.new current` (the previous release; an
   emergency one may be pinned in `KEEP_RELEASES`).
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

## 7. Identity conflicts (IDENTITY_CONFLICT_SUPPRESSED)

`cbb_platform/prod/identity_conflicts.json` is the curated registry, committed and released like code. Each entry
covers one real player who has two canonical identities that cannot be merged without overwriting an existing identity.
The first entry is Khoi Thurmon (Old Dominion), added 2026-11-11.

Every run does the following:
* The suppressed id never enters the model frame: zero model inputs, simulation draws, PMFs and priced rows.
* The suppressed id's availability report goes to the primary.
* The run re-verifies the entry's evidence: portal move, destination roster, exact name, height and hometown.
* If the evidence no longer holds, the primary is simulated but `NOT_PRICED` (`IDENTITY_CONFLICT_UNVERIFIED`).
* If both ids ever reach one game's distributions, both are `NOT_PRICED` (`IDENTITY_CONFLICT_DOUBLE_COUNT`).
* The audit and per-layer appearance counts are written to `prod/runs/<run>/identity_conflicts.json` and to the run
  summary's `identity_conflicts`.

Nothing is merged, re-keyed or deleted. When deterministic evidence later allows a conflict-safe merge, remove the
entry in the same release as the merge.

## 8. Quote integration: certification, invalidation, re-validation after a FAIL

The Odds API, sport `basketball_ncaab`, region `us` (no bookmaker allowlist, no weight table), odds `american`,
dates `iso`, the 12 production player-prop keys (`quotes.REQUEST_MARKETS`; no `_alternate` key):

    GET /v4/sports/basketball_ncaab/events                    0 credits   event ids (+ account quota)
    GET /v4/sports/basketball_ncaab/events/{id}/markets       1 credit    ALWAYS first: advertised keys per book
    GET /v4/sports/basketball_ncaab/events/{id}/odds          markets returned x 1 region; only the advertised subset
                                                                          (none advertised: no odds call,
                                                                          NO_SUPPORTED_PROP_MARKETS_POSTED)

Every live request's `x-requests-last / -used / -remaining` is in `prod/cache/odds_production_requests.jsonl` and in
the collection file's `requests` receipt.

**Certification** (`quote_refresh.certify`, run by a due scoring tick while not certified). Discovery: today's
forecasted pre-tip games with a persisted PMF, in tip order, at most 8 `/markets` calls in ONE pass with the explicit
approved bookmakers (`bookmakers=` `PC.ODDS_BOOKMAKERS`: draftkings, fanduel, betmgm, williamhill_us (Caesars),
betrivers, hardrockbet, espnbet (theScore Bet), bovada, betonlineag, fliff; 10 books = 1 region-equivalent; never
`regions=`), stopping right after the first strong candidate (>= 3 supported markets on >= 2 bookmakers: a stop rule
only). The best discovered supported candidate (supported markets, then bookmakers, then tip, then game id) gets ONE
event-odds request with the SAME bookmakers for exactly its advertised keys (one more, next-ranked, only if it returned no
prop rows). Nine checks on those real quotes priced at the persisted PMF (no simulation): GAME_MATCH, PLAYER_MATCH,
STAT_MATCH, LINE_VALIDATION, PRICE_VALIDATION, FRESHNESS_VALIDATION, PMF_LOOKUP, PRICING_VALIDATION,
RAW_EVIDENCE_PERSISTENCE. Receipt: `prod/odds/validation_runs/<id>/certification.json`; a PASS is appended to
`prod/ops/markers/quote_certifications.jsonl` (the first also writes the write-once `QUOTE_INTEGRATION_VALIDATED.json`)
and the run gate `prod/odds/integration_validation.json`.

Accounting (`prod/ops/markers/quote_validation_attempts.jsonl`: `discovery_run`, `discovery_only`,
`event_odds_request_count`, `counts_toward_certification_limit`, `us_/us2_market_discovery_calls`, `selected_region`):
only an invocation that reached event odds is a CERTIFICATION ATTEMPT (one per invocation, however many odds calls);
at most 3 per ET day, 90 min apart. Discovery-only runs never consume them; an empty (or failed) discovery-only run
starts a 60-minute discovery cooldown (no new scan before; the quote timer keeps running).

The certification is **persistent** (no daily re-certification). It closes only on a material event:
`INVALIDATED_INTEGRATION_CHANGED` (automatic: endpoint / sport / market keys / fixed parameters or the source of
`quotes.py`, `matching.py`, `pricing.py`, `props_v1_pricing.py` changed -> the next due tick re-certifies),
`PROVIDER_SCHEMA_CHANGE` (response guard) or `CREDENTIAL_FAILURE` (HTTP 401/403), both automatic, or an architect
invalidation (no tick runs):

    $CBB_PY -m cbb_platform.prod.shadow_tick \
      --ops-root /var/lib/sports-models/cbb-originator/prod/ops \
      --invalidate-certification SYSTEMATIC_MATCH_FAILURE --detail "<evidence>"

One rejected sportsbook quote never invalidates: it is excluded with its reason in the Board audit.

A recorded FAIL stops further paid attempts. The scheduled unit cannot take `--allow-revalidation`, so after the
defect behind a FAIL is fixed and deployed, the architect records ONE authorization (no tick runs, no provider call):

    $CBB_PY -m cbb_platform.prod.shadow_tick \
      --ops-root /var/lib/sports-models/cbb-originator/prod/ops \
      --authorize-revalidation "<what was fixed>" --fix-commit <sha>

It appends to `prod/ops/markers/quote_revalidation_authorizations.jsonl`, lifts only the FAILs recorded before it,
and is refused when nothing is blocked or after a PASS. A FAIL after the authorization blocks again.

## 7z. Manual Python invocations (service user, bytecode disabled, real roots)

Never run Python in the release as root and never without `-B`: one interpreter start that writes `__pycache__` into
`/opt/sports-models/cbb-originator/releases/<sha>` makes every unit refuse the release (files outside its manifest,
exit 3; recovery = `restore_release.py`, never weakening the manifest). Every manual command uses this prefix, which
runs as `cbb-originator` with the production env file, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, the release read-only
and the production roots / history DB of the units:

    CBB_PY="systemd-run --pipe --wait --quiet --uid=cbb-originator --gid=cbb-originator -p UMask=0027 \
      -p EnvironmentFile=/etc/sports-models/cbb-originator.env -p ReadOnlyPaths=/opt/sports-models/cbb-originator \
      -E PYTHONDONTWRITEBYTECODE=1 -E PYTHONPATH=/opt/sports-models/cbb-originator/current \
      -E CBB_PROD_ROOT=/var/lib/sports-models/cbb-originator/prod -E CBB_EXPORT_ROOT=/var/lib/sports-models/cbb-originator/exports \
      -E CBB_WF_ROOT=/var/lib/sports-models/cbb-originator/walkforward \
      --working-directory=/opt/sports-models/cbb-originator/current /opt/sports-models/cbb-originator/venv/bin/python -B"

Manual export of ET date D (the real history DB; the CLI default path does not exist on the Droplet), under the writer
lock:

    flock -w 600 /var/lib/sports-models/cbb-originator/locks/cbb-originator.writer.lock \
      $CBB_PY -m cbb_platform.prod.exports daily --date D \
      --history-db /var/lib/sports-models/cbb-originator/history/cbb_production_history_minimal.duckdb

## 8a. CBB_Boards / CBB_Grades / CBB_Performance (market evaluation contract)

Odds ingestion (prod/quote_refresh.py): event-specific endpoints only (`/events` -> `/events/{id}/markets` ->
`/events/{id}/odds`), explicit `bookmakers=` (draftkings, fanduel, betmgm, williamhill_us (= Caesars; the provider's
key for Caesars Sportsbook), betrivers, bovada, betonlineag, hardrockbet, espnbet, fliff), the 12 base player markets,
`oddsFormat=american`. A game with no supported market is `NO_SUPPORTED_PROP_MARKETS_POSTED` (no /odds call) and is
rediscovered once per window (08:30 10:00 11:00 12:00 13:30 15:30 17:30 19:30 ET); a `PROP_DISCOVERED` game is
refreshed every 30 min (> 120 min before its cutoff) / 10 min (<= 120 min); nothing at or after min(commence, cutoff).
A FAILED / BUDGET_EXHAUSTED attempt never consumes a window or slot. `CBB_ODDS_QUOTA_SAFETY_THRESHOLD` (default
250000 remaining) stops optional polling. Per run: `quotes/<D>/ingestion_receipts.jsonl` (event / market / odds calls,
supported markets, credits consumed, quota remaining); event map `quotes/event_map.jsonl`.

`exports/<D>/CBB_Boards_MM_DD_YYYY.xlsx` (every quote tick; rewritten only on content change; `boards_receipt.json`):
exactly one row per GAME x PLAYER x STAT (a duplicate aborts). Every VALID non-alternate book x line x side of the
game's latest pre-cutoff observation is priced at its exact line from the persisted PMF (P over / under / push),
de-vigged same book same line only, push-aware EV; best offer = EV, edge, model probability, payout, book key.
Preferred Side OVER / UNDER when any candidate EV > 0, else NO_POSITIVE_EV. Is Actionable: EV >= 0.03, edge >= 0.03,
model probability >= 0.54 and >= 2 books two-sided at the line (or MEDIUM / HIGH consensus at it). Tiers 65_PLUS,
60_PLUS, ACTIONABLE, PASS_NO_EDGE. Sheets: Full Board (real-market rows only), 60 Percent Plus, 65 Percent Plus, Model
Coverage (model-only rows). Consensus fields are informational.

Frozen evaluations: once a game's cutoff passes, `ops/evaluations/<D>/market_evaluations.jsonl` records (write-once,
hash-verified) its evaluation at the decision observation (first pre-cutoff observation at / after the latest pre-cutoff
PMF). `CBB_Grades` of export date D grades D-1 (every market evaluation incl. PASS_NO_EDGE, exact line; VOID_DNP /
VOID_NO_GAME / PENDING excluded from metrics). `CBB_Performance` is cumulative from the first date with real pre-cutoff
quotes (`ops/performance/cbb_market_performance.parquet`; nine tabs). Close = last observation <= min(commence, cutoff),
at most 60 min stale, at / after the entry; CLV statuses EXACT_LINE, SAME_LINE_NEAREST_CLOSE, LINE_MOVED (directional
movement, no probability CLV), NO_DEVIG_PAIR, NO_CLOSE_QUOTE. Export health requires Boards, Grades and Performance;
Distributions is supporting. No automatic historical-odds calls.

## 9. Morning feature warmup and the bounded catch-up (DEFERRED_FEATURE_BACKLOG)

Since 2026-11-18 (Release A) the warmup is morning stage 2/6 (`FEATURE_WARMUP`), after PREP and after the 06:00 ET
training cutoff, so it builds over exactly the frozen as-of state the fit trains on (`WARMUP_LINEAGE.
WARMUP_STATE_MATCHES_FIT`). The retired 01:30 ET warmup ran before the late D-1 finals reached the state (01:53-04:23 ET
measured 11-16..18), so the 07:00 walk-forward deferred on a 35-49 min backlog and INITIAL ran on the D-2 model.
A retryable walk-forward deferral now HOLDS INITIAL (`AWAITING_FRESH_MODEL`) for the 08:00 recovery; REFIT_RETRY is
recovery-only (INITIAL already made on the incumbent after `INITIAL_HOLD_UNTIL_ET` 09:30). The historical text below
describes the earlier mechanism. The walk-forward first
runs the same builder inside `CBB_MORNING_FEATURE_BUDGET_MIN` (default 30). If the backlog is not finished the refit is
DEFERRED_FEATURE_BACKLOG: nothing is fitted or promoted, the ACTIVE model is kept and today's 07:00 forecast is made
with it; every cutoff already built stays cached. The 08:00 retry retries a deferred morning once (NO_OP after a
morning that ran). 2026-11-11: ~190 games newly eligible at once (cutoffs from 11-02) ran an unbounded catch-up into
the 2 h systemd timeout (SIGTERM at 09:00 ET, no 07:00 forecast; the 08:00 retry found the writer lock still held).
