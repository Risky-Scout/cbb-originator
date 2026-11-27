"""Read-only: for every INITIAL_0700 / REFIT_RETRY snapshot, the point-in-time availability knowable at its executed_at
(RotoWire event store, knowable < T, plus in-force curated verified rows on the candidate code) vs what the snapshot
simulated (it is run with rotowire_root=None). Also: when the first later snapshot for the game corrected it."""
import json, sys
import pandas as pd
from cbb_platform.prod import config as PC, lifecycle as LC, rotowire_feed as RW, rotowire_player_states as PS
OUT = sys.argv[1]
life = LC.Life(); idx = LC.snapshots(life)
ev = RW.EventStore(PC.ROTOWIRE_ROOT).frame(); states = PS.load()
UNAV = {"OUT", "OUT_FOR_SEASON", "OUT_SEASON", "SUSPENDED", "UNAVAILABLE", "LONG_TERM_OUT"}
rows, snaps = [], []
for s in idx:
    if s.get("window") not in ("INITIAL_0700", "REFIT_RETRY") or not s.get("games") or s["slate_date"] < "2026-11-20":
        continue
    fp = life.forecasts(s["slate_date"], s["snapshot_id"], "forecast_players.parquet")
    if not fp.exists():
        continue
    sim = pd.read_parquet(fp)
    T = pd.Timestamp(s["executed_at"])
    g = pd.DataFrame({"canonical_game_id": s["games"]})
    full = json.load(open(life.forecasts(s["slate_date"], s["snapshot_id"], "snapshot.json")))
    g["feature_cutoff"] = pd.to_datetime(g.canonical_game_id.map(full["cutoffs"]), utc=True)
    g["start_time"] = pd.to_datetime(g.canonical_game_id.map(full["scheduled_tips"]), utc=True)
    m = sim.drop_duplicates("game_id").set_index("game_id")
    g["home_team_id"] = g.canonical_game_id.map(m["home_team_id"]) if "home_team_id" in m else None
    g["away_team_id"] = g.canonical_game_id.map(m["away_team_id"]) if "away_team_id" in m else None
    if g["home_team_id"].isna().any():
        teams = sim.groupby("game_id")["team_id"].unique()
        g["home_team_id"] = g.canonical_game_id.map(lambda x: teams[x][0] if x in teams else None)
        g["away_team_id"] = g.canonical_game_id.map(lambda x: teams[x][1] if x in teams and len(teams[x]) > 1 else None)
    import time; t0 = time.time()
    av, rs = RW.availability_frame(ev, g, T, states=states)
    print(s['snapshot_id'], len(g), 'frame', round(time.time() - t0, 1), 's', flush=True)
    simk = set(zip(sim.game_id, sim.player_id))
    col = "p_play" if "p_play" in sim else None
    pp = dict(zip(zip(sim.game_id, sim.player_id), sim[col])) if col else {}
    known = av[av.status.isin(UNAV)] if len(av) else av
    n_rw = n_cur = 0
    for r in known.itertuples(index=False):
        k = (r.game_id, r.player_id)
        if k not in simk:
            continue
        cur = r.availability_source == RW.CURATED_SOURCE
        n_cur += cur; n_rw += (not cur)
        later = [x for x in idx if x["snapshot_id"] > s["snapshot_id"] and r.game_id in (x.get("games") or [])]
        rows.append({"slate_date": s["slate_date"], "snapshot_id": s["snapshot_id"], "window": s["window"], "executed_at": s["executed_at"],
                     "game_id": r.game_id, "player_id": r.player_id, "status": r.status, "source": r.availability_source,
                     "received_at": r.received_at, "p_play_simulated": pp.get(k),
                     "next_snapshot": later[0]["snapshot_id"] if later else None, "next_window": later[0]["window"] if later else None})
    gated = av[(av.availability_gate != "") & av.set_index(["game_id", "player_id"]).index.isin(simk)] if len(av) else av
    snaps.append({"snapshot_id": s["snapshot_id"], "window": s["window"], "games": len(s["games"]), "simulated_rows": len(sim),
                  "knowable_unavailable_simulated_rotowire": n_rw, "knowable_unavailable_simulated_curated": n_cur,
                  "knowable_gated_simulated": int(len(gated)),
                  "identity_blocked_games_ignored": sum("AVAILABILITY_IDENTITY_UNRESOLVED" in v for v in rs.values())})
pd.DataFrame(rows).to_csv(f"{OUT}/initial_gap_rows.csv", index=False)
json.dump(snaps, open(f"{OUT}/initial_gap.json", "w"), indent=1, default=str)
print(json.dumps(snaps, indent=1, default=str))
d = pd.DataFrame(rows)
if len(d):
    print(d.groupby(["window", "source", "next_window"], dropna=False).size())
    print("p_play simulated, knowable-unavailable:", d.p_play_simulated.describe().round(3).to_dict())
