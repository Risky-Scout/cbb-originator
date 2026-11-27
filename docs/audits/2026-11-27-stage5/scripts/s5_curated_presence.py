"""Read-only: curated (verified) non-participation players with a canonical id -- are they simulated / priced / in S
(game-layer team strength) in existing production runs since 11-20? Also every confirmed-OUT row (availability_status)
for the game-layer delta-vs-INITIAL question."""
import json, sys, glob, os
import pandas as pd
OUT = sys.argv[1]; PS = sys.argv[2]
cur = pd.read_csv(PS, dtype=str).fillna("")
cur = cur[cur.canonical_player_id != ""]
want = {(r.canonical_player_id, r.affiliated_team_id): (r.rotowire_player_name, r.operational_class) for r in cur.itertuples()}
runs = sorted(d for d in glob.glob("/var/lib/sports-models/cbb-originator/prod/runs/R202611*") if os.path.basename(d) >= "R20261120")
rows, outs = [], []
for d in runs:
    m = f"{d}/model_inputs.parquet"
    if not os.path.exists(m):
        continue
    mi = pd.read_parquet(m, columns=None)
    k = list(zip(mi.player_id, mi.team_id))
    hit = mi[[x in want for x in k]]
    u = pd.read_csv(f"{d}/player_universe.csv", usecols=["game_id", "canonical_player_id", "scoreability", "roster_status"]) if os.path.exists(f"{d}/player_universe.csv") else pd.DataFrame()
    for r in hit.itertuples(index=False):
        uu = u[(u.game_id == r.game_id) & (u.canonical_player_id == r.player_id)] if len(u) else u
        rows.append({"run": os.path.basename(d), "game_id": r.game_id, "pid": r.player_id, "name": want[(r.player_id, r.team_id)][0],
                     "class": want[(r.player_id, r.team_id)][1], "p_play": r.p_play, "min_mu": r.min_mu, "PTS_mu": r.PTS_mu,
                     "availability_status": r.availability_status, "player_suppression": r.player_suppression,
                     "scoreability": uu.scoreability.iloc[0] if len(uu) else None, "roster_status": uu.roster_status.iloc[0] if len(uu) else None})
    o = mi[mi.availability_status.isin(["OUT", "OUT_FOR_SEASON", "OUT_SEASON", "SUSPENDED", "UNAVAILABLE", "LONG_TERM_OUT"])]
    for r in o.itertuples(index=False):
        outs.append({"run": os.path.basename(d), "game_id": r.game_id, "pid": r.player_id, "p_play": r.p_play, "min_mu": r.min_mu,
                     "PTS_mu": r.PTS_mu, "status": r.availability_status, "received_at": r.availability_received_at, "role": r.role_diagnostic})
df = pd.DataFrame(rows); od = pd.DataFrame(outs)
df.to_csv(f"{OUT}/curated_presence.csv", index=False); od.to_csv(f"{OUT}/confirmed_out_rows.csv", index=False)
last = df.sort_values("run").drop_duplicates(["game_id", "pid"], keep="last") if len(df) else df
rep = {"runs": len(runs), "curated_with_canonical_id": len(want),
       "curated_player_games_simulated": int(len(last)), "curated_players_simulated": sorted(last.name.unique().tolist()) if len(last) else [],
       "by_class_scoreability": last.groupby(["class", "scoreability", "availability_status"]).size().reset_index().to_dict("records") if len(last) else [],
       "p_play_mean_by_player": last.groupby("name").p_play.mean().round(3).to_dict() if len(last) else {},
       "S_contribution_by_player": (last.p_play * last.PTS_mu).groupby(last.name).mean().round(2).to_dict() if len(last) else {},
       "confirmed_out_player_games": int(od.drop_duplicates(["game_id", "pid"]).shape[0]) if len(od) else 0,
       "confirmed_out_rotation": int(od.drop_duplicates(["game_id", "pid"]).query("role == 'TOP5_MINUTES' or min_mu >= 20").shape[0]) if len(od) else 0}
json.dump(rep, open(f"{OUT}/curated_presence.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str))
