"""Phase 5/6 quantitative audit: when a REGULAR (forecast min_mu >= 24) does not play (y_min == 0 / missing), are the
teammates who did play under-forecast (minutes and stats)? F2026 + LIVE, PRODALLB replay rows (played rows only)."""
import json, os
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122")
out = {}
for ds in ("F2026", "LIVE"):
    if ds == "LIVE":
        f = pd.read_parquet(f"{Q}/live/oos_2027.parquet", columns=["player_game_example_id", "game_id", "min_mu", "y_min", "p_play"])
        f = f.merge(pd.read_parquet(f"{Q}/live/frame_2027.parquet", columns=["player_game_example_id", "canonical_team_id"]), on="player_game_example_id")
    else:
        f = pd.read_parquet(f"{Q}/d1_{ds}.parquet", columns=["player_game_example_id", "game_id", "canonical_team_id", "min_mu", "y_min", "p_play"])
    f = f.rename(columns={"player_game_example_id": "pgx", "canonical_team_id": "team"})
    f["absent_reg"] = (f.min_mu >= 24) & ~(f.y_min.fillna(0) > 0)
    tg = f.groupby(["game_id", "team"]).agg(n_abs=("absent_reg", "sum"), lost_min=("min_mu", lambda s: 0.0)).reset_index()
    lost = f[f.absent_reg].groupby(["game_id", "team"]).min_mu.sum().rename("lost_min")
    tg = tg.drop(columns="lost_min").merge(lost, on=["game_id", "team"], how="left").fillna({"lost_min": 0})
    pl = f[f.y_min.fillna(0) > 0].merge(tg, on=["game_id", "team"])
    pl["grp"] = np.where(pl.n_abs == 0, "NONE_ABSENT", np.where(pl.n_abs == 1, "ONE_REGULAR_ABSENT", "2PLUS_ABSENT"))
    mins = pl.groupby("grp").apply(lambda q: pd.Series({"players": len(q), "teams": q[["game_id", "team"]].drop_duplicates().shape[0],
                                                         "min_ratio": q.y_min.sum() / q.min_mu.sum()}), include_groups=False)
    # team-level minutes conservation: forecast minutes of players who played vs realized
    team = pl.groupby(["game_id", "team", "grp"]).agg(fc=("min_mu", "sum"), real=("y_min", "sum")).reset_index()
    tm = team.groupby("grp").apply(lambda q: pd.Series({"forecast_team_min": q.fc.mean(), "realized_team_min": q.real.mean()}), include_groups=False)
    j = pd.read_parquet(f"{Q}/p4/p5_{ds}_joint.parquet", columns=["arm", "pgx", "stat", "y", "mean"])
    j = j[(j.arm == "PRODALLB") & j.stat.isin(["PTS", "REB", "AST", "FG3M"])].merge(pl[["pgx", "grp"]], on="pgx")
    st = j.groupby(["grp", "stat"]).apply(lambda q: round(q.y.sum() / q["mean"].sum(), 3), include_groups=False).unstack()
    out[ds] = {"minutes": mins.round(3).to_dict("index"), "team_minutes": tm.round(1).to_dict("index"), "stat_ratio": st.to_dict("index")}
    print(ds); print(mins.round(3)); print(tm.round(1)); print(st)
json.dump(out, open(os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results/r5_absence_response.json"), "w"), indent=1, default=str)
