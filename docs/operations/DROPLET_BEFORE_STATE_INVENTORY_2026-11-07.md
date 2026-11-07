# Droplet before-state inventory — 2026-11-07 (~18:25 UTC)

Read-only. Collected before any CBB write. Host `143.198.43.248`, hostname `ubuntu-s-2vcpu-4gb-tor1`.

## Hardware (actual, differs from directive §3)
- nproc: **4**
- MemTotal: **8,326,938,624 B (~7.76 GiB)**; MemAvailable 7,625,646,080 B; swap 0
- `/` (`/dev/vda1`): 165,295,407,104 B total, 58,859,393,024 used, **106,419,236,864 B available (36%)**
- Python 3.12.3

## Existing sports-model trees (not touched)
- `/opt/nba-prop-runtime` (nbaquant:nbaquant 0750)
- `/srv/nba-production` (nbaquant:nbaquant 0750)
- `/var/lib/nba-prop` (nbaquant:nbaquant 0775)
- `/root/Massey Predictive Models` (code/research tree; no systemd units, no cron, no running process)
- CBB namespace pre-existing and empty: `/opt/sports-models/cbb-originator`, `/var/lib/sports-models/cbb-originator` (both nbaquant-owned, empty); `/etc/sports-models/cbb-originator.env` (46 B, one key name: `ODDS_API_KEY`, value length 32)

## Crontabs
root: none. nbaquant: none.

## Running processes of note
nbaquant systemd --user, tmux session `serve-1102` in `/srv/nba-production/nba-prop-quant`. No CBB or Massey process.

## NBA systemd unit fingerprints (sha256, /etc/systemd/system)
```
e04ba0d213e57783de2bfef41925b4aae20d0201a91a7773a4e1e6ffc1007d02  nba-capture-00-25.timer
bb2d56af89b6e7e815ea2d1cb5d8eda84fb8365f7e3500f74946b3a1f818ce1e  nba-capture-09-25.timer
66f06c80cc0ae1f094220fca5744272bf850e1def3945d0d1407a5ba1cc8bf7b  nba-capture-14-25.timer
385d99618423c12538f2467eb009def652384f40c991ed7d71975357aeb4ee96  nba-capture-19-25.timer
afe2e8853f202aa8412916923e5ac9f7bed50219e2182a4d360a0943678aec79  nba-capture@.service
b06beeadde0232a4f74cb0c80edbd80cc750e5b43f3b96a60cc79f44267b7dd8  nba-export.service
708d19d79bdaf0ff97b652c3dd44bfff9b7144952ef6ef50c4b93f0f26d3c213  nba-export.timer
2b14bfce2be8227c265421848780b93834731cdc68c9a83a992c46a7265dbd34  nba-lifecycle.service
6ab44361d000ae06a9f1c2c408a9d4fd49045051ad11ba70f6410ef1ea31fb2c  nba-lifecycle.timer
0f3344b9215adddae0dd8f8aef933e485cb319985ce718aa39637e8fcd7b1ca2  nba-performance.service
93681e488b3ebc5939bc7802ddca25255b4f071fa49dcb4c2a17dc9837035c2d  nba-performance.timer
41fa6020418185095b96392e0ff9ad2724b629a11fbec9706fdfc9ef8674b6b6  nba-prop-daily.service
b5424bf7f4b838131a396bb7ca15b847139d1e53810c9525d0ff0935ff1c47c0  nba-prop-daily.timer
629995e6d965943b66423641130bdd8ecd5c580ed2d4dadeda567b3234cb1c5d  nba-prop-forecast.service
81fbffd472bacb913aa249924cee330f150d7688c28c27bdde5c391d19cceb5f  nba-prop-forecast.timer
9fa24f6684a07cb25e441e706461e65f140f6f7d92b442ddfc78b5c4e2254125  nba-prop-grade.service
e3171d9b53037c21f38e6a6c0bbae3bf5ec07405b04556802838ec28f74b1c4b  nba-prop-grade.timer
34ed8c02880816732641b5112805924384ae750544f2b124ae632c223c209da6  nba-prop-pregame.service
b35b348e28e90f0c2e0b1cb43cfb9502f49ac64a1ade59dcf37120282a6aadf9  nba-prop-pregame.timer
aa504110bc614c51fe30ab5bd8160e36bb861ca5a99108f17a95c797bdf57c22  nba-prop-t20.service
aa2102cfab5ac12ba7060b88c98b6147fe49788fe983472f5de8416004a265b2  nba-prop-t20.timer
```
Active NBA timers: nba-prop-pregame, nba-prop-t20 (every few min), nba-prop-daily 04:37 ET, nba-prop-grade 08:15 ET,
nba-performance 08:30 ET, nba-prop-forecast 11:00 ET. Failed units (pre-existing, not CBB): nba-capture@00-25,
nba-capture@14-25, nba-capture@19-25, nba-prop-daily-cutover.

## Locks
NBA-private only: `/srv/nba-production/work/locks/*.lock`, `/srv/nba-production/nba-prop-v2-*/…/*.lock`,
per-game t20 locks. **No common cross-model heavy-job lock exists.** No NBA unit uses `flock`;
`/run/lock/sports-models/` does not exist. Massey has no scheduled jobs.

## Addendum — before NBA heavy-lock integration (2026-11-07 ~18:50 UTC)

### `nba-prop-daily.service` (as found; base unit `/etc/systemd/system/nba-prop-daily.service`, no drop-ins)
- User/Group: `nbaquant`/`nbaquant` (from `systemctl show`)
- WorkingDirectory: `/srv/nba-production/nba-prop-quant`
- EnvironmentFile: `/etc/nba-prop-quant/production.env`
- Environment: `PYTHONUNBUFFERED=1`, `TZ=America/New_York`
- ExecStart: `/opt/nba-prop-runtime/venv/bin/python ops/accept_scheduled_phase.py run-morning --phase morning`
- TimeoutStartSec=infinity, Nice=10, Type=oneshot
- unit sha256 `41fa6020418185095b96392e0ff9ad2724b629a11fbec9706fdfc9ef8674b6b6`
- timer sha256 `b5424bf7f4b838131a396bb7ca15b847139d1e53810c9525d0ff0935ff1c47c0`, `OnCalendar=*-*-* 04:37:00 America/New_York`, Persistent=true
- last run 2026-11-07 09:37:02–10:24:45 UTC (04:37–05:24 ET), exit 0

### NBA timer enabled states
enabled+active: nba-performance, nba-prop-daily, nba-prop-forecast, nba-prop-grade, nba-prop-pregame, nba-prop-t20.
disabled: nba-capture-00-25, -09-25, -14-25, -19-25, nba-export, nba-lifecycle.
All NBA services run as `nbaquant`; none had drop-ins.

### Tree fingerprints (sha256 of sorted `path size mtime` listing)
- `/opt/nba-prop-runtime`: `6d10d2d9777cda2f5d095a009c78e9dd9e8e2e3a4bdc4f496835a21deca68ea3` (25,774 files)
- `/srv/nba-production/nba-prop-quant/**/*.py`: `4228da67fda3ce94365c8c76641158671017ba92fd00ea2add6134835fc3fd2a`
- `/root/Massey Predictive Models`: `ae8a10dc057346a335d1079f579e13e9deec6d3a70a55cd9b4346db6495c18fb` (20,139 files)

### Massey
No systemd units or timers (0 matches in /etc/systemd/system, /lib/systemd/system, loaded units). No cron. No process.

### Resources
MemTotal 8,131,776 kB, MemAvailable 7,459,348 kB; `/` available 105,941,102,592 B.

### CBB env key presence (values never read)
ODDS_API_KEY PRESENT; CBBD_API_KEY MISSING; GH_TOKEN MISSING; CBB_OPS_ENCRYPTION_KEY MISSING; ROTOWIRE_API_KEY MISSING.

## NBA heavy-lock rollback (do not run unless required)
1. `rm /etc/systemd/system/nba-prop-daily.service.d/10-sports-models-heavy-lock.conf` (only this drop-in)
2. `systemctl daemon-reload`
3. verify `systemctl show nba-prop-daily.service -p ExecStart` is again
   `/opt/nba-prop-runtime/venv/bin/python ops/accept_scheduled_phase.py run-morning --phase morning`
4. leave base unit and timer untouched (hashes above).
