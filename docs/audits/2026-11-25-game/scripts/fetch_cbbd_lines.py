"""RESEARCH ONLY (2026-11-25): CBBD /lines for seasons 2024 (2023-24 dev), 2025 (2024-25 selection) and 2027 (2026-27 live).
2026 (2025-26) is NOT fetched: CBBD /lines is outside the frozen lockbox opening plan (docs/game_model/CBB_GAME_MODEL_RELEASE_B.md).
Runs on the Droplet as cbb-originator (EnvironmentFile supplies CBBD_API_KEY; never printed). Month windows (<= 3,000 games per
response). Output: <out>/lines_<season>.json (raw list) + a manifest with request counts and sha256."""
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

OUT = sys.argv[1]
SEASONS = [int(s) for s in sys.argv[2].split(",")]
KEY = "".join(ch for ch in os.environ["CBBD_API_KEY"] if 33 <= ord(ch) <= 126)
os.makedirs(OUT, exist_ok=True)
man = {}
for season in SEASONS:
    rows, calls = [], 0
    y0 = season - 1
    months = [(y0, m) for m in (10, 11, 12)] + [(season, m) for m in (1, 2, 3, 4)]
    for (y, m) in months:
        if date(y, m, 1) > date.today():
            continue
        end = date(y + (m == 12), (m % 12) + 1, 1)
        q = urllib.parse.urlencode({"season": season, "startDateRange": f"{y}-{m:02d}-01", "endDateRange": end.isoformat()})
        req = urllib.request.Request(f"https://api.collegebasketballdata.com/lines?{q}",
                                     headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    part = json.loads(r.read())
                break
            except Exception as e:                      # noqa: BLE001
                if attempt == 3:
                    raise RuntimeError(f"season {season} {y}-{m:02d}: {type(e).__name__}") from None
                time.sleep(5 * (attempt + 1))
        calls += 1
        rows += part
        print(f"season {season} {y}-{m:02d}: {len(part)} games", flush=True)
        time.sleep(1)
    blob = json.dumps(rows, sort_keys=True).encode()
    p = os.path.join(OUT, f"lines_{season}.json")
    open(p, "wb").write(blob)
    man[season] = {"games": len(rows), "calls": calls, "sha256": hashlib.sha256(blob).hexdigest()}
json.dump(man, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
print(json.dumps(man))
