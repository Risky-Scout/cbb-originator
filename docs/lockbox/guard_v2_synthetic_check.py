"""Synthetic guard checks for the prepared (unapplied) lockbox opening patch. No data is read; inputs are literals."""
import sys, os, json, hashlib, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path
from cbb_platform import lockbox as L
assert Path(L.__file__).resolve().is_relative_to(Path.cwd().resolve())
def ok(f):
    try: f(); return True
    except L.LockboxViolation: return False
R = {}
os.environ["LOCKBOX_OPEN_2025_26"] = "1"
R["closed_flag_only_season"] = not ok(lambda: L.check_season(2026))
R["closed_flag_only_request"] = not ok(lambda: L.check_request("cbbd", "games", {"season": 2026}))
R["closed_flag_only_response"] = not ok(lambda: L.check_response("cbbd", {"season": 2026}, "games"))
d = tempfile.mkdtemp(); rec = Path(d) / "dec.json"
rec.write_bytes(json.dumps({"decision_type": "LOCKBOX_OPEN", "lockbox_open": True, "approved_by": ["synthetic"]}).encode())
L.OPEN_DECISION_PATH = rec; L.OPEN_DECISION_SHA256 = hashlib.sha256(rec.read_bytes()).hexdigest()
R["open_season_2026_planned"] = ok(lambda: L.check_request("cbbd", "games", {"season": 2026}))
R["open_response_2026_planned"] = ok(lambda: L.check_response("cbbd", {"season": 2026, "startDate": "2026-01-10T00:00Z"}, "games"))
R["open_canonical_reader"] = ok(lambda: L.check_season(2026))
R["open_2027_request_refused"] = not ok(lambda: L.check_request("cbbd", "games", {"season": 2027}))
R["open_2027_response_refused"] = not ok(lambda: L.check_response("cbbd", {"season": 2027}, "games"))
R["open_post_window_date_response_refused"] = not ok(lambda: L.check_response("cbbd", {"startDate": "2026-11-10T00:00Z"}, "games"))
R["open_nested_2027_refused"] = not ok(lambda: L.check_response("cbbd", [{"x": {"season": 2027}}], "games"))
R["open_unplanned_endpoint_refused"] = not ok(lambda: L.check_request("cbbd", "plays/date", {"date": "2026-01-10"}))
R["open_unplanned_endpoint_response_refused"] = not ok(lambda: L.check_response("cbbd", {"season": 2026}, "lines"))
R["open_bdl_season_refused"] = not ok(lambda: L.check_request("bdl", "games", {"season": 2025}))
R["open_bdl_response_refused"] = not ok(lambda: L.check_response("bdl", {"season": 2025}, "games"))
R["open_torvik_refused"] = not ok(lambda: L.check_request("torvik", "games", {"year": 2026}))
R["open_unbounded_refused"] = not ok(lambda: L.check_request("cbbd", "games", {}))
rec.write_bytes(rec.read_bytes() + b" ")
R["open_tampered_record_refused"] = not ok(lambda: L.check_season(2026))
rec.write_bytes(rec.read_bytes()[:-1])
os.environ.pop("LOCKBOX_OPEN_2025_26")
R["open_without_flag_refused"] = not ok(lambda: L.check_season(2026))
print(json.dumps(R, indent=1)); print("ALL_PASS", all(R.values()))
