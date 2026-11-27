"""Read-only: candidate availability_frame on the live upcoming 7-day slate (now): which curated verified
non-participation rows enter as CURATED_VERIFIED_STATE, and do the blocked-game set / RotoWire rows stay identical to the
same frame without them (the identity-exemption logic is untouched)."""
import json, sys
from datetime import datetime, timezone
import duckdb, pandas as pd
from cbb_platform.prod import config as PC, state as ST, rotowire_feed as RW, rotowire_player_states as PS
OUT = sys.argv[1]
now = pd.Timestamp(datetime.now(timezone.utc)); st = ST.current()
con = duckdb.connect(str(st["increment_db"]), read_only=True)
sch = con.execute("SELECT canonical_game_id, start_time, home_team_id, away_team_id FROM metadata.prod_schedule WHERE season=?", [PC.PRODUCTION_SEASON]).df()
con.close()
sch["start"] = pd.to_datetime(sch.start_time, utc=True)
up = sch[(sch.start > now) & (sch.start < now + pd.Timedelta(days=7))]
g = pd.DataFrame({"canonical_game_id": up.canonical_game_id, "home_team_id": up.home_team_id, "away_team_id": up.away_team_id,
                  "feature_cutoff": up.start - pd.Timedelta(minutes=30)})
ev = RW.EventStore(PC.ROTOWIRE_ROOT).frame()
states = PS.load()
fr, rs = RW.availability_frame(ev, g, now, states=states)
cur = fr[fr.availability_source == RW.CURATED_SOURCE]
noncur = fr[fr.availability_source != RW.CURATED_SOURCE]
res = {"as_of": now.isoformat(), "state_version": st["pointer"]["state_version"], "games_7d": int(len(g)),
       "frame_rows": int(len(fr)), "curated_rows": int(len(cur)),
       "curated_players": cur.groupby(["rotowire_player_id", "player_id", "team_id", "status", "availability_raw_status"]).size().reset_index(name="games").to_dict("records"),
       "blocked_games": sorted(k for k, v in rs.items() if "AVAILABILITY_IDENTITY_UNRESOLVED" in v),
       "duplicate_game_player_keys": int(fr.duplicated(["game_id", "player_id"]).sum())}
json.dump(res, open(f"{OUT}/curated_after_now.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
