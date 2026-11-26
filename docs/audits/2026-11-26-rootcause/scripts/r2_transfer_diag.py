"""Phase 3 diagnostic: transfer residual (realized / forecast) vs origin->destination strength, usage, minutes; TRAIN F2025
(PRODX3) and F2026 / LIVE (PRODALLB). Rows = played player-games (min_mu > 0, y known), first 45 season days."""
import json, os
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
Q = os.path.join(ROOT, "data/qa/large/causal_20261122")
P = pd.read_parquet(os.path.join(ROOT, "data/qa/large/rootcause_20261126/transition_panel.parquet"))
P = P.dropna(subset=["canonical_player_id"]).drop_duplicates(["canonical_player_id", "season"])
def load(ds, file, arm):
    r = pd.read_parquet(f"{Q}/p4/{file}", columns=["arm", "pgx", "stat", "y", "mean", "var", "min_mu", "cold"])
    r = r[(r.arm == arm) & r.y.notna() & (r.min_mu > 0) & r.stat.isin(["PTS", "REB", "AST", "FG3M", "TOV"])]
    fr = (pd.read_parquet(f"{Q}/live/frame_2027.parquet", columns=["player_game_example_id", "canonical_player_id", "season", "canonical_team_id"])
          if ds == "LIVE" else pd.read_parquet(f"{Q}/d1_{ds}.parquet", columns=["player_game_example_id", "canonical_player_id", "season", "canonical_team_id", "season_day"]))
    r = r.merge(fr.rename(columns={"player_game_example_id": "pgx"}), on="pgx")
    return r.merge(P[["canonical_player_id", "season", "cls", "dstr", "usage_p", "mpg_p", "origin_net_prev", "dest_net_prev"]],
                   on=["canonical_player_id", "season"], how="left")
out = {}
for ds, file, arm in (("F2025", "p5_F2025_train3.parquet", "PRODX3"), ("F2026", "p5_F2026_joint.parquet", "PRODALLB"), ("LIVE", "p5_LIVE_joint.parquet", "PRODALLB")):
    r = load(ds, file, arm)
    r["cls"] = r["cls"].fillna("NO_PRIOR_HISTORY")
    agree = pd.crosstab(r.cold, r.cls)
    t = r[r.cls == "TRANSFER"].copy()
    t["dbin"] = pd.cut(t.dstr, [-99, -10, -3, 3, 10, 99])
    t["ubin"] = pd.cut(t.usage_p, [0, 16, 20, 24, 99])
    t["mbin"] = pd.cut(t.mpg_p, [0, 10, 20, 28, 99])
    f = lambda q: pd.Series({"n": len(q), "ratio": q.y.sum() / q["mean"].sum()})
    out[ds] = {"class_ratio": r.groupby(["cls", "stat"]).apply(f, include_groups=False).round(3).reset_index().to_dict("records"),
               "transfer_by_dstr": t.groupby(["dbin"], observed=True).apply(f, include_groups=False).round(3).reset_index().astype(str).to_dict("records"),
               "transfer_by_usage": t.groupby(["ubin"], observed=True).apply(f, include_groups=False).round(3).reset_index().astype(str).to_dict("records"),
               "transfer_by_mpg": t.groupby(["mbin"], observed=True).apply(f, include_groups=False).round(3).reset_index().astype(str).to_dict("records"),
               "cold_vs_panel": agree.to_dict()}
    print(ds, "rows", len(r))
    print(" class:", {f"{k}": round(v, 3) for k, v in r.groupby("cls").apply(lambda q: q.y.sum() / q["mean"].sum(), include_groups=False).items()})
    print(" transfer by dstr:", [(x["dbin"], x["n"], x["ratio"]) for x in out[ds]["transfer_by_dstr"]])
    print(" transfer by usage:", [(x["ubin"], x["n"], x["ratio"]) for x in out[ds]["transfer_by_usage"]])
    print(" transfer by mpg:", [(x["mbin"], x["n"], x["ratio"]) for x in out[ds]["transfer_by_mpg"]])
    print(" cold x panel:\n", agree)
json.dump(out, open(os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results/r2_transfer_diag.json"), "w"), indent=1, default=str)
