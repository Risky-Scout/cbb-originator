# Three-model isolation audit — 2026-11-24 (CBB session)

Read-only audit of the shared Droplet (CBB Originator, NBA Prop Quant, Massey; plus a fourth stack, the Fanatics NBA demo).
No predictive math changed.

| Resource | CBB | NBA | Massey |
|---|---|---|---|
| Source | /opt/sports-models/cbb-originator/releases/<sha> (`current`) | /srv/nba-production/nba-prop-quant, /opt/nba-prop-runtime/bin | /root/Massey Predictive Models (git checkout) |
| Runtime | /opt/sports-models/cbb-originator/venv | /opt/nba-prop-runtime/venv, /srv/nba-production/reporting/venv | /root/Massey Predictive Models/.venv |
| User | cbb-originator (uid 997) | nbaquant (uid 999) | root (manual runs); container uid 70 |
| Env | /etc/sports-models/cbb-originator.env (root:cbb-originator 640) | /etc/nba-prop-quant/production.env (root:nbaquant 640), /root/secrets.env (capture/lifecycle) | /root/Massey Predictive Models/.env (600) |
| State | /var/lib/sports-models/cbb-originator (2750) | /srv/nba-production/{nba-prop-v2-data,nba-prop-v2-fit-registry,work} | Docker volume massey_pgdata |
| Exports | /var/lib/sports-models/cbb-originator/exports | /srv/nba-production/exports | Massey public export stack |
| Locks | .../cbb-originator/locks/{writer,game-quotes}.lock; shared /run/lock/sports-models/heavy.lock | /srv/nba-production/{work/locks,nba-prop-v2-data/.locks,fit-registry/locks}, reporting/nba-weekly-report.lock | none on host |
| systemd | cbb-originator-* (13 services, 9 timers, 1 target) | nba-* (capture, export, lifecycle, performance, prop-*, weekly-report) | none |
| Schedulers | systemd timers only | systemd timers (+ tmux sessions) | none on host (no cron/systemd) |
| DB/container | none | none | massey-postgres (postgres:17-alpine, no published ports, restart unless-stopped) |
| Deploy | immutable git-archive release + cbb-deploy-release (symlink swap, ROLLBACK_RELEASE) | NBA-owned tooling | git checkout |
| Rollback | /opt/sports-models/cbb-originator/ROLLBACK_RELEASE | NBA-owned | git |

Cross-references (grep of code/config/units; docs excluded): none from CBB to NBA/Massey except the deliberate
`InaccessiblePaths=` deny-list in every CBB unit; none from NBA, Massey or Fanatics to CBB. No cross-project symlinks.
Processes: CBB processes run in the CBB release; NBA in /srv/nba-production or /opt/nba-prop-runtime; postgres in its container.

## Findings
- ISOLATION_DEFECT (fixed): `nbaquant` was a member of group `cbb-originator` (usermod 2026-11-07 18:26Z, from the
  first CBB provisioning). The NBA service user could read the CBB env (API keys, ops encryption key, GH token) and all
  CBB state, and write ~4,100 group-writable CBB files (qa/, audit/, prod/runs run_failure.json). No NBA code or unit
  referenced CBB, so nothing depended on it. Fix: `gpasswd -d nbaquant cbb-originator`; verified nbaquant can no longer
  read the env or list CBB state, NBA paths still readable/writable. Residual: tmux session "weekly-paired" (pids 626560/1,
  started before the fix) keeps the old group set until it is restarted (NBA-owned; not touched).
- ISOLATION_DEFECT (needs owner decision): the same ODDS_API_KEY value is in the CBB env, the NBA env and /root/secrets.env
  — one Odds API quota shared by CBB and NBA; heavy use by one can exhaust the other, and rotation affects both. Fix
  requires a separate key per project.
- INTENTIONAL_SHARED_RESOURCE_COORDINATION: /run/lock/sports-models/heavy.lock (group sports-models-heavy: cbb-originator,
  nbaquant). Only CBB code takes it today; it coordinates CPU/memory-heavy jobs, it carries no data.
- INTENTIONAL_SHARED_OS_RESOURCE: dockerd/containerd, /tmp (CBB units use PrivateTmp=yes; no collision), Python 3.12.
- BENIGN: empty leftover /opt/nba-prop-runtime/.config/cbb-originator (nbaquant home, 700, no files).
- Fanatics demo (fanatics-demo-publish, root, sandboxed) reads NBA ledgers/exports read-only: NBA-side, out of CBB scope.

## Guard
tests/test_cross_project_isolation.py fails CI if cbb_platform/, deploy/ or .github/ reference NBA/Massey/Fanatics
paths, users, units or containers (outside the unit deny-list), run host-wide commands (glob systemctl verbs, pkill,
killall, broad rm/find -delete), add foreign users to CBB groups, or if a CBB unit writes outside CBB state.
