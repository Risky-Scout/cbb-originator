"""Read-only measurements over existing production artifacts since 11-20:
(1) game layer: confirmed-OUT player mass (sum p_play*PTS_mu of CONFIRMED_UNAVAILABLE rows) in each forecast snapshot vs
    the availability delta the game layer applied (delta is measured against the day's INITIAL snapshot);
(2) participation calibration by RotoWire status: last pre-tip p_play vs realized participation (box minutes > 0)."""
import json, sys, glob, os
import duckdb, numpy as np, pandas as pd
OUT = sys.argv[1]
P = "/var/lib/sports-models/cbb-originator/prod"
UN = ("OUT", "OUT_FOR_SEASON", "OUT_SEASON", "SUSPENDED", "UNAVAILABLE", "LONG_TERM_OUT")
g_rows, p_rows = [], []
for sj in sorted(glob.glob(f"{P}/ops/forecasts/2026-11-2*/F*/snapshot.json")):
    d = os.path.dirname(sj); snap = json.load(open(sj))
    run = snap.get("run_id"); gp = f"{d}/game_pmfs.parquet"; mi = f"{P}/runs/{run}/model_inputs.parquet"
    if not run or not os.path.exists(gp) or not os.path.exists(mi):
        continue
    g = pd.read_parquet(gp, columns=["game_id", "home_team_id", "away_team_id", "availability_delta_home", "availability_delta_away", "availability_base_snapshot"])
    m = pd.read_parquet(mi, columns=["game_id", "team_id", "player_id", "p_play", "PTS_mu", "min_mu", "role_diagnostic", "availability_status", "prediction_cutoff_utc"])
    o = m[m.availability_status.isin(UN)]
    mass = (o.p_play * o.PTS_mu).groupby([o.game_id, o.team_id]).sum()
    for r in g.itertuples(index=False):
        for side, t, dl in (("H", r.home_team_id, r.availability_delta_home), ("A", r.away_team_id, r.availability_delta_away)):
            ms = float(mass.get((r.game_id, t), 0.0))
            g_rows.append({"snapshot": snap.get("snapshot_id"), "window": snap.get("window"), "game_id": r.game_id, "team": t,
                           "out_mass": ms, "delta": float(dl), "is_base": r.availability_base_snapshot in (None, snap.get("snapshot_id"))})
    m = m.assign(snapshot=snap.get("snapshot_id"), as_of=snap.get("prediction_as_of") or snap.get("executed_at"))
    p_rows.append(m)
G = pd.DataFrame(g_rows)
last = G.sort_values("snapshot").drop_duplicates(["game_id", "team"], keep="last")
w = last[last.out_mass > 0]
rep1 = {"team_games_with_confirmed_out_mass": int(len(w)), "out_mass_total": round(float(w.out_mass.sum()), 2),
        "delta_total_applied_S_units": round(float(w.delta.sum()), 2),
        "share_of_out_mass_reflected": round(float(-w.delta.sum() / w.out_mass.sum()), 3) if len(w) else None,
        "team_games_out_mass_ge5_with_zero_delta": int(((w.out_mass >= 5) & (w.delta.abs() < 1e-9)).sum()),
        "team_games_out_mass_ge5": int((w.out_mass >= 5).sum())}
# (2) participation calibration
M = pd.concat(p_rows, ignore_index=True)
M = M[pd.to_datetime(M.as_of, utc=True) <= pd.to_datetime(M.prediction_cutoff_utc, utc=True)]
M = M.sort_values("as_of").drop_duplicates(["game_id", "player_id"], keep="last")
st = json.load(open(f"{P}/state/CURRENT.json"))
sv = st.get("state_version") or st.get("current")
con = duckdb.connect(f"{P}/state/versions/{sv}/increment.duckdb", read_only=True, config={"threads": 1, "memory_limit": "300MB"})
box = con.execute("SELECT canonical_game_id game_id, canonical_player_id player_id, coalesce(minutes,0) mins FROM flagship.player_game_outcomes WHERE season=2027").df()
done = set(con.execute("SELECT DISTINCT canonical_game_id FROM flagship.player_game_outcomes WHERE season=2027").df().canonical_game_id)
con.close()
M = M[M.game_id.isin(done)].merge(box, on=["game_id", "player_id"], how="left")
M["played"] = (M.mins.fillna(0) > 0).astype(int)
cal = M.groupby("availability_status").agg(n=("played", "size"), mean_p_play=("p_play", "mean"), realized=("played", "mean")).round(3)
rot = M[(M.role_diagnostic == "TOP5_MINUTES") | (M.min_mu >= 20)]
calr = rot.groupby("availability_status").agg(n=("played", "size"), mean_p_play=("p_play", "mean"), realized=("played", "mean")).round(3)
rep = {"game_layer_out_reflection": rep1, "state_version": sv, "finished_games": len(done),
       "participation_calibration_by_status_all": cal.reset_index().to_dict("records"),
       "participation_calibration_by_status_rotation": calr.reset_index().to_dict("records")}
G.to_csv(f"{OUT}/game_layer_out_mass.csv", index=False)
json.dump(rep, open(f"{OUT}/status_measure.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str))
