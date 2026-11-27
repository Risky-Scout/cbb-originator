"""Stage 04 read-only: CBBD 2026-27 roster vs 2026-27 box participation divergence (whole population)."""
import json, sys
import duckdb, pandas as pd
from cbb_platform.prod import config as PC, state as ST
OUT = sys.argv[1]
st = ST.current(); con = duckdb.connect(str(st["increment_db"]), read_only=True)
print([c[0] for c in con.execute("describe flagship.player_game_outcomes").fetchall()])
print([c[0] for c in con.execute("describe flagship.games").fetchall()])
print(con.execute("select identity_status, count(*) from flagship.player_game_outcomes where season=? group by 1", [PC.PRODUCTION_SEASON]).fetchall())
q = """WITH p AS (SELECT canonical_player_id pid, canonical_team_id tid, count(*) n, sum(coalesce(minutes,0)) mins,
          min(event_time) first_t, max(event_time) last_t, any_value(source_player_name) nm
        FROM flagship.player_game_outcomes WHERE season=? AND canonical_player_id IS NOT NULL GROUP BY 1,2),
     r AS (SELECT DISTINCT canonical_player_id pid, canonical_team_id tid FROM flagship.rosters WHERE season=?),
     ra AS (SELECT DISTINCT canonical_player_id pid FROM flagship.rosters WHERE season=?),
     multi AS (SELECT pid FROM p GROUP BY 1 HAVING count(*)>1)
   SELECT p.*, (p.pid,p.tid) IN (SELECT (pid,tid) FROM r) on_team_roster, p.pid IN (SELECT pid FROM ra) on_any_roster,
          p.pid IN (SELECT pid FROM multi) multi_team FROM p"""
d = con.execute(q, [PC.PRODUCTION_SEASON]*3).df()
gap = d[~d.on_team_roster]
print("participating pairs", len(d), "not on team roster", len(gap), "of which on other team roster", int(gap.on_any_roster.sum()), "multi-team", int(gap.multi_team.sum()))
print("gap minutes share", float(gap.mins.sum() / d.mins.sum()))
print(gap.sort_values("mins", ascending=False).head(60).to_string())
gap.to_csv(f"{OUT}/roster_gap_participation.csv", index=False)
con.close()
