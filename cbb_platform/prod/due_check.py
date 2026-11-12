"""Cheap scheduler gate for the shadow tick. Standard library only: no dependency install, no data, no network.

Reads DUE.json from the ops-state checkout (written by every shadow tick) and prints `due=true|false` (GitHub Actions
output format). Due when: no DUE.json yet (bootstrap), DUE.json says work is due now, its next_due_at has arrived, a pending
pre-refit refresh attempt (pre_refit_refresh.due_at, before its cutoff) has arrived, or the heartbeat (the routine refresh interval since the last recorded next sync) has lapsed. Only then does the
workflow install the environment and run the full tick, so an idle scheduler check costs seconds.

    python cbb_platform/prod/due_check.py <ops_root> [--now ISO]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _t(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def check(ops_root: Path, now: datetime) -> tuple[bool, str]:
    p = Path(ops_root) / "DUE.json"
    if not p.exists():
        return True, "NO_DUE_DOCUMENT (bootstrap)"
    d = json.loads(p.read_text())
    if d.get("due_now"):
        return True, "DUE_NOW"
    nxt = d.get("next_due_at")
    if nxt is None:
        return True, "NO_NEXT_DUE (refresh)"
    if now >= _t(nxt):
        return True, f"NEXT_DUE_REACHED {nxt}"
    pre = (d.get("pre_refit_refresh") or {}).get("due_at")       # a pending pre-refit attempt / retry wakes on its own
    if pre and now >= _t(pre) and ("cutoff" not in d["pre_refit_refresh"] or now < _t(d["pre_refit_refresh"]["cutoff"])):
        return True, f"PRE_REFIT_REFRESH_DUE {pre}"
    sync = (d.get("next_by_kind") or {}).get("sync")
    if sync and now >= _t(sync):
        return True, "HEARTBEAT"
    return False, f"IDLE until {nxt}"


def main(argv=None) -> None:
    a = list(sys.argv[1:] if argv is None else argv)
    now = datetime.now(timezone.utc)
    if "--now" in a:
        i = a.index("--now")
        now = _t(a[i + 1])
        del a[i:i + 2]
    due, why = check(Path(a[0]) if a else Path("ops"), now)
    print(f"due={'true' if due else 'false'}")
    print(f"reason={why}")


if __name__ == "__main__":
    main()
