"""Phase 5/6 ground truth from box scores (flagship.player_game_outcomes; 2024-25 TRAIN, 2025-26 EVAL): when a regular
(trailing-5 mean minutes >= 24, appeared in the team's previous game) does NOT appear, how do teammates' minutes / FGA /
points / rebounds / assists move relative to their own trailing-5 means (the information the incumbent uses)?
Strictly historical expectations (shifted rolling means). Writes results/r6_redistribution_truth.json + the per-team-game
panel (gitignored) used for fitting a redistribution rule."""
import json, os
import duckdb, numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
c = duckdb.connect(os.path.join(ROOT, "data/lockbox_2025_26/canonical/cbb.duckdb"), read_only=True)
g = c.execute("""select o.canonical_game_id g, o.canonical_team_id t, o.canonical_player_id p, o.season, o.minutes m, o.fga, o.fta,
                 o.three_pa tpa, o.points pts, o.reb, o.ast, o.tov, o.starter, gm.scheduled_tip_utc tip
                 from flagship.player_game_outcomes o join flagship.games gm on gm.canonical_game_id = o.canonical_game_id
                 where o.season in (2024, 2025, 2026) and o.identity_status = 'RESOLVED'""").df() if False else None
cols = [r[0] for r in c.execute("select column_name from information_schema.columns where table_schema='flagship' and table_name='games'").fetchall()]
tipc = next(x for x in ("scheduled_tip_utc", "start_utc", "game_start_utc", "start_date", "game_date") if x in cols)
g = c.execute(f"""select o.canonical_game_id g, o.canonical_team_id t, o.canonical_player_id p, o.season, o.minutes m, o.fga, o.fta,
                 o.three_pa tpa, o.points pts, o.reb, o.ast, o.tov, coalesce(o.starter, false) starter, gm.{tipc} tip
                 from flagship.player_game_outcomes o join flagship.games gm on gm.canonical_game_id = o.canonical_game_id
                 where o.season in (2025, 2026)""").df()
c.close()
g = g[g.m.notna()]
g["tip"] = pd.to_datetime(g.tip, utc=True)
# team game sequence and roster universe: players who appeared for the team earlier in the season
tg = g[["g", "t", "season", "tip"]].drop_duplicates().sort_values(["t", "season", "tip"])
tg["k"] = tg.groupby(["t", "season"]).cumcount()
g = g.merge(tg[["g", "t", "k"]], on=["g", "t"])
V = ["m", "fga", "fta", "tpa", "pts", "reb", "ast", "tov"]
# player x team-game grid: every player who has appeared for the team, at each later team game (absent = no box row / 0 min)
first = g.groupby(["p", "t", "season"]).k.min().rename("k0").reset_index()
grid = tg.merge(first, on=["t", "season"])
grid = grid[grid.k > grid.k0]
grid = grid.merge(g[["g", "t", "p"] + V], on=["g", "t", "p"], how="left").fillna({v: 0.0 for v in V})
grid = grid.sort_values(["p", "t", "season", "k"])
played = grid.m > 0
for v in V:                                                 # trailing mean over the last 5 APPEARANCES (strictly earlier)
    s = grid[v].where(played)
    grid[f"{v}5"] = s.groupby([grid.p, grid.t, grid.season]).transform(lambda x: x.shift(1).rolling(5, min_periods=3).mean())
    grid[f"{v}5"] = grid.groupby([grid.p, grid.t, grid.season])[f"{v}5"].ffill()
grid["prev_played"] = played.groupby([grid.p, grid.t, grid.season]).shift(1).fillna(False).astype(bool)
grid["reg"] = (grid.m5 >= 24) & grid.prev_played
grid["absent_reg"] = grid.reg & ~played
team = grid.groupby(["g", "t", "season"]).agg(n_abs=("absent_reg", "sum"),
                                              lost_m=("m5", lambda x: 0.0)).reset_index()
lost = grid[grid.absent_reg].groupby(["g", "t"])[[f"{v}5" for v in V]].sum().add_prefix("lost_").reset_index()
team = team.drop(columns="lost_m").merge(lost, on=["g", "t"], how="left").fillna(0)
pl = grid[played & grid.m5.notna()].merge(team, on=["g", "t", "season"])
pl["grp"] = np.where(pl.n_abs == 0, "NONE", np.where(pl.n_abs == 1, "ONE_REGULAR_OUT", "TWO_PLUS_OUT"))
out = {}
for season, q in pl.groupby("season"):
    r = {}
    for grp, h in q.groupby("grp"):
        r[grp] = {"player_games": int(len(h)), "team_games": int(h[["g", "t"]].drop_duplicates().shape[0]),
                  **{f"{v}_ratio": round(float(h[v].sum() / h[f"{v}5"].sum()), 4) for v in V}}
    # where do the lost minutes go: by teammate trailing-minute rank and same-position-free role bands
    one = q[q.grp == "ONE_REGULAR_OUT"].copy()
    one["band"] = pd.cut(one.m5, [0, 10, 20, 28, 45], labels=["<10", "10-20", "20-28", "28+"])
    gain = one.groupby("band", observed=True).apply(lambda h: pd.Series({"n": len(h), "extra_min_per_player": float((h.m - h.m5).mean()),
                                                                          "extra_fga_per_player": float((h.fga - h.fga5).mean())}), include_groups=False)
    tm = one.groupby(["g", "t"]).agg(extra=("m", "sum"), base=("m5", "sum"), lost=("lost_m5", "first"))
    base_none = q[q.grp == "NONE"].groupby(["g", "t"]).agg(extra=("m", "sum"), base=("m5", "sum"))
    r["redistribution"] = {"by_teammate_band": gain.round(3).to_dict("index"),
                           "team_extra_minutes_mean": float((tm.extra - tm.base).mean()), "lost_minutes_mean": float(tm.lost.mean()),
                           "absorbed_share": float(((tm.extra - tm.base) / tm.lost).median()),
                           "none_group_team_extra": float((base_none.extra - base_none.base).mean())}
    out[int(season)] = r
os.makedirs(os.path.join(ROOT, "data/qa/large/rootcause_20261126"), exist_ok=True)
pl.to_parquet(os.path.join(ROOT, "data/qa/large/rootcause_20261126/redistribution_panel.parquet"), index=False)
json.dump(out, open(os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results/r6_redistribution_truth.json"), "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str)[:4000])
