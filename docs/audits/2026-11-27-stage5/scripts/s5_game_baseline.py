"""Read-only replay of the production game layer (game.forecast.forecast) on the real snapshots of one day, with the
snapshot's own model inputs / state version / training cutoff: baseline INITIAL (must reproduce production bit for bit:
pmf_sha256) vs NEWS_FREE (Stage 05). Writes only under OUT/gb/. Usage: s5_game_baseline.py OUT DAY"""
import json, os, sys, glob
from pathlib import Path
import pandas as pd
from cbb_platform.game import forecast as GF
OUT, DAY = Path(sys.argv[1]), sys.argv[2]
P = Path("/var/lib/sports-models/cbb-originator/prod")
snaps = []
for sj in glob.glob(str(P / "ops/forecasts" / DAY / "F*/snapshot.json")):
    s = json.load(open(sj)); d = Path(sj).parent
    if (d / "game_pmfs.parquet").exists() and (P / "runs" / str(s.get("run_id")) / "model_inputs.parquet").exists():
        snaps.append((s.get("prediction_as_of") or s.get("executed_at"), d, s))
snaps.sort(key=lambda x: str(x[0]))
res = {"day": DAY, "snapshots": len(snaps), "modes": {}}
cols = ["game_id", "pmf_sha256", "availability_delta_home", "availability_delta_away", "p_home_win", "model_margin",
        "model_total", "model_home_score", "model_away_score"]
prod_rows, mode_rows = [], {"INITIAL": [], "NEWS_FREE": []}
for _, d, s in snaps:
    g = pd.read_parquet(d / "game_pmfs.parquet")
    prod_rows.append(g[cols].assign(snapshot=s["snapshot_id"]))
for mode in ("INITIAL", "NEWS_FREE"):
    os.environ[GF.BASELINE_ENV] = mode
    for _, d, s in snaps:
        g = pd.read_parquet(d / "game_pmfs.parquet")
        games = pd.DataFrame({"canonical_game_id": g.game_id, "home_team_id": g.home_team_id, "away_team_id": g.away_team_id,
                              "scheduled_tip_utc": pd.to_datetime(g.scheduled_tip_utc, utc=True),
                              "cutoff": pd.to_datetime(g.cutoff_utc, utc=True), "neutral_site": g.neutral_site})
        mi = pd.read_parquet(P / "runs" / s["run_id"] / "model_inputs.parquet")
        inc = P / "state/versions" / s["state_version"] / "increment.duckdb"
        sd = OUT / "gb" / mode / DAY / s["snapshot_id"]
        rec = GF.forecast(sd, s, games, inc, pd.Timestamp(g.training_cutoff_utc.iloc[0]), mi, 2027)
        o = pd.read_parquet(sd / GF.FILE)
        mode_rows[mode].append(o[cols].assign(snapshot=s["snapshot_id"]))
        res["modes"].setdefault(mode, []).append({"snapshot": s["snapshot_id"], "status": rec["status"], "baseline": rec.get("availability_baseline")})
PR = pd.concat(prod_rows); A = pd.concat(mode_rows["INITIAL"]); B = pd.concat(mode_rows["NEWS_FREE"])
m = PR.merge(A, on=["snapshot", "game_id"], suffixes=("_prod", "_init"))
res["initial_reproduces_production"] = {"rows": int(len(m)), "pmf_sha_equal": int((m.pmf_sha256_prod == m.pmf_sha256_init).sum())}
x = PR.merge(B, on=["snapshot", "game_id"], suffixes=("_prod", "_nf"))
for k in ("availability_delta_home", "availability_delta_away", "p_home_win", "model_margin", "model_total"):
    x[f"d_{k}"] = x[f"{k}_nf"] - x[f"{k}_prod"]
ch = x[(x.d_p_home_win.abs() > 1e-9) | (x.d_model_total.abs() > 1e-9)]
last = x.sort_values("snapshot").drop_duplicates("game_id", keep="last")
lc = last[(last.d_p_home_win.abs() > 1e-9) | (last.d_model_total.abs() > 1e-9)]
res["news_free_vs_production"] = {"rows": int(len(x)), "rows_changed": int(len(ch)), "games_changed_final_snapshot": int(len(lc)),
    "final_snapshot_abs_d_p_home_win": lc.d_p_home_win.abs().describe().round(4).to_dict() if len(lc) else {},
    "final_snapshot_abs_d_margin": lc.d_model_margin.abs().describe().round(3).to_dict() if len(lc) else {},
    "final_snapshot_d_total": lc.d_model_total.describe().round(3).to_dict() if len(lc) else {},
    "largest": lc.reindex(lc.d_p_home_win.abs().sort_values(ascending=False).index).head(8)[
        ["game_id", "availability_delta_home_prod", "availability_delta_home_nf", "availability_delta_away_prod",
         "availability_delta_away_nf", "d_p_home_win", "d_model_margin", "d_model_total"]].round(4).to_dict("records")}
y = A.merge(B, on=["snapshot", "game_id"], suffixes=("_init", "_nf"))
for k in ("p_home_win", "model_margin", "model_total", "model_home_score", "model_away_score"):
    y[f"d_{k}"] = y[f"{k}_nf"] - y[f"{k}_init"]
yl = y.sort_values("snapshot").drop_duplicates("game_id", keep="last")
yc = yl[(yl.d_p_home_win.abs() > 1e-9) | (yl.d_model_total.abs() > 1e-9)]
res["news_free_vs_initial_same_code"] = {"games_final_snapshot": int(len(yl)), "games_changed": int(len(yc)),
    "abs_d_p_home_win": yc.d_p_home_win.abs().describe().round(4).to_dict() if len(yc) else {},
    "abs_d_margin": yc.d_model_margin.abs().describe().round(3).to_dict() if len(yc) else {},
    "d_total": yc.d_model_total.describe().round(3).to_dict() if len(yc) else {},
    "largest": yc.reindex(yc.d_p_home_win.abs().sort_values(ascending=False).index).head(6)[
        ["game_id", "availability_delta_home_init", "availability_delta_home_nf", "availability_delta_away_init",
         "availability_delta_away_nf", "d_p_home_win", "d_model_margin", "d_model_total"]].round(4).to_dict("records")}
y.to_csv(OUT / f"game_baseline_vs_initial_{DAY}.csv", index=False)
x.to_csv(OUT / f"game_baseline_{DAY}.csv", index=False)
json.dump(res, open(OUT / f"game_baseline_{DAY}.json", "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
