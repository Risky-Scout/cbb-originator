"""Extended synthetic checks for the UNAPPLIED v2 opening patch (run only inside a throwaway copy with the patch applied).
Literal inputs only; no data is read. Complements guard_v2_synthetic_check.py with: seasons after 2026-27, endpoint
scoping for every planned / unplanned endpoint (including stats/team/season, which the CBBD box route needs), the
closed state with the patch applied but no decision record, and availability capture unchanged."""
import hashlib, json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path
from cbb_platform import lockbox as L
assert Path(L.__file__).resolve().is_relative_to(Path.cwd().resolve())
def ok(f):
    try: f(); return True
    except L.LockboxViolation: return False
R = {}
LOCKBOX_REQUESTS = [("cbbd", "games", {"season": 2026}), ("cbbd", "games/players", {"season": 2026}),
                    ("cbbd", "games/teams", {"startDateRange": "2025-11-01", "endDateRange": "2025-11-07"}),
                    ("cbbd", "plays/date", {"date": "2026-01-10"}), ("cbbd", "stats/team/season", {"season": 2026}),
                    ("bdl", "games", {"seasons[]": [2025]}), ("bdl", "stats", {"dates[]": ["2026-01-10"]}),
                    ("torvik", "games", {"year": 2026})]
# 1. patch applied, decision constant unset (the state of every commit but the opening commit): fully closed
R["closed_patched_all_lockbox_requests_refused"] = all(not ok(lambda p=p, e=e, q=q: L.check_request(p, e, q))
                                                       for p, e, q in LOCKBOX_REQUESTS)
R["closed_patched_canonical_refused"] = not ok(lambda: L.check_season(2026))
R["closed_patched_response_refused"] = not ok(lambda: L.check_response("cbbd", {"season": 2026}, "games"))
R["closed_patched_development_allowed"] = ok(lambda: L.check_request("cbbd", "games", {"season": 2025}))
# 2. authorized synthetic opening
os.environ["LOCKBOX_OPEN_2025_26"] = "1"
rec = Path(tempfile.mkdtemp()) / "dec.json"
rec.write_bytes(json.dumps({"decision_type": "LOCKBOX_OPEN", "lockbox_open": True, "approved_by": ["synthetic"]}).encode())
L.OPEN_DECISION_PATH = rec; L.OPEN_DECISION_SHA256 = hashlib.sha256(rec.read_bytes()).hexdigest()
for s in (2027, 2028, 2030):
    R[f"open_season_{s}_request_refused"] = not ok(lambda s=s: L.check_request("cbbd", "games", {"season": s}))
    R[f"open_season_{s}_response_refused"] = not ok(lambda s=s: L.check_response("cbbd", {"season": s}, "games"))
    R[f"open_season_{s}_canonical_refused_as_future_request"] = not ok(lambda s=s: L.check_request("cbbd", "games/players", {"season": s}))
R["open_future_date_range_refused"] = not ok(lambda: L.check_request("cbbd", "games", {"startDateRange": "2026-11-01", "endDateRange": "2026-11-30"}))
R["open_range_crossing_into_2026_27_refused"] = not ok(lambda: L.check_request("cbbd", "games", {"startDateRange": "2026-07-01", "endDateRange": "2026-08-15"}))
planned = sorted(L.OPEN_PLAN_ENDPOINTS)
R["open_planned_endpoints"] = planned
R["open_every_planned_endpoint_allowed_2026"] = all(ok(lambda e=e: L.check_request("cbbd", e, {"season": 2026})) for e in planned)
for e in ("plays/date", "plays/game", "lineups/game", "lines", "substitutions/game", "stats/player/shooting/season"):
    R[f"open_unplanned_{e}_refused"] = not ok(lambda e=e: L.check_request("cbbd", e, {"season": 2026}))
R["route_endpoint_stats_team_season_allowed_by_v2"] = ok(lambda: L.check_request("cbbd", "stats/team/season", {"season": 2026}))
R["open_bdl_all_spellings_refused"] = all(not ok(lambda q=q: L.check_request("bdl", "games", q)) for q in
                                          ({"season": 2025}, {"seasons[]": [2025]}, {"dates[]": ["2026-01-10"]},
                                           {"start_date": "2025-11-01", "end_date": "2025-11-30"}))
R["open_bdl_response_refused"] = not ok(lambda: L.check_response("bdl", {"season": 2025, "date": "2026-01-10"}, "games"))
R["open_torvik_refused"] = not ok(lambda: L.check_request("torvik", "games", {"year": 2026}))
R["open_unregistered_provider_refused"] = not ok(lambda: L.check_request("kenpom", "games", {"season": 2026}))
R["open_sharp_stats_capture_unchanged"] = ok(lambda: L.check_request("sharp_stats", "snapshot", {}))
R["open_cbbd_season_2026_with_planned_endpoint_only_via_cbbd_or_canonical"] = (
    L.opening_authorized("cbbd", "games") and L.opening_authorized("canonical") and not L.opening_authorized("bdl", "games"))
os.environ.pop("LOCKBOX_OPEN_2025_26")
R["flag_removed_closes_again"] = not ok(lambda: L.check_season(2026))
gates = {k: v for k, v in R.items() if k not in ("open_planned_endpoints", "route_endpoint_stats_team_season_allowed_by_v2")}
print(json.dumps(R, indent=1)); print("ALL_PASS", all(gates.values()))
