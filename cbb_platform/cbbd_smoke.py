"""Stage 1 closure — bounded live CBBD smoke + athleteId identity verification.

    .venv/bin/python -m cbb_platform.cbbd_smoke fetch      # 30 live calls (needs CBBD_API_KEY)
    .venv/bin/python -m cbb_platform.cbbd_smoke analyze    # offline: reads data/raw/cbbd only

NOT a backfill. The sample is fixed below and lies entirely inside the development period
(seasons 2019, 2024, 2025). Every call goes through cbbd.fetch, which enforces the 2025-26
lockbox guard before the request and before storage. The analysis reads only stored raw payloads,
so it is reproducible without network access.
"""
from __future__ import annotations

import sys
import time

from cbb_platform.adapters import cbbd

TEAM = "Purdue"
# (endpoint, params). Date ranges are US/Eastern-safe closed windows inside one development season.
BOX_WINDOWS = [
    {"season": 2025, "team": TEAM, "startDateRange": "2024-11-04", "endDateRange": "2024-11-12"},
    {"season": 2024, "team": TEAM, "startDateRange": "2023-11-06", "endDateRange": "2023-11-14"},
]
ROSTERS = [{"season": 2025, "team": TEAM}, {"season": 2024, "team": TEAM}]
# Stage 1 final unblock — transfer probe: three 2024-25 Alabama players transferred in from Auburn,
# Rutgers and South Florida (Starter Pack season files). Alabama's window includes the Purdue game.
TRANSFER_WINDOWS = [
    {"season": 2025, "team": "Alabama", "startDateRange": "2024-11-04", "endDateRange": "2024-11-16"},
    {"season": 2024, "team": "Auburn", "startDateRange": "2023-11-06", "endDateRange": "2023-11-14"},
    {"season": 2024, "team": "Rutgers", "startDateRange": "2023-11-06", "endDateRange": "2023-11-14"},
    {"season": 2024, "team": "South Florida", "startDateRange": "2023-11-06", "endDateRange": "2023-11-14"},
]
# Date-bounded PIT experiment: cutoffs bracketing Purdue's 2024-11-15 ET / 2024-11-16 00:00Z game vs Alabama.
PIT_WINDOWS = [{"season": 2025, "team": TEAM, "startDateRange": "2024-11-01", "endDateRange": end}
               for end in ("2024-11-15", "2024-11-16", "2024-11-12")]
# 2024-11-12 discriminates "tip <= end 00:00Z" from "tip < end + 1 day": game 471 tipped 2024-11-12 01:00Z.
EVENT_GAMES = [14, 300, 5882, 35928]   # 2025, 2025, 2024, 2019 (CBBD game ids, all in core.games)


def plan() -> list[tuple[str, dict]]:
    calls = []
    for w in BOX_WINDOWS:
        calls += [("games", w), ("games/players", w), ("games/teams", w)]
    calls += [("teams/roster", r) for r in ROSTERS]
    calls += [("games/players", w) for w in TRANSFER_WINDOWS]
    for w in PIT_WINDOWS:
        calls += [("stats/player/season", w), ("stats/team/season", w)]
    for gid in EVENT_GAMES:
        calls += [(ep, {"gameId": gid}) for ep in ("plays/game", "substitutions/game", "lineups/game")]
    return calls


def fetch_all() -> list[dict]:
    out = []
    for endpoint, params in plan():
        rec = cbbd.fetch(endpoint, dict(params))
        out.append({"endpoint": endpoint, "params": params, "file": rec.path.name, "created": rec.created})
        time.sleep(0.3)
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["fetch"]:
        for r in fetch_all():
            print(r["endpoint"], r["params"], r["file"], "new" if r["created"] else "already stored")
    elif sys.argv[1:2] == ["analyze"]:
        # optional: analyze --blocked "<reason>" records why no live payload exists
        from cbb_platform import cbbd_identity
        cbbd_identity.main(sys.argv[3] if sys.argv[2:3] == ["--blocked"] else None)
    else:
        print(__doc__)
