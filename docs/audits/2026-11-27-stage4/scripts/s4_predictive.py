"""Read-only: for the 18 APPLIED box-participation pairs, where do they sit in existing production runs since 11-20?
universe (in_frozen_pool / roster_status / scoreability), model_inputs (simulated, p_play, minutes), board/market (method)."""
import json, sys, glob, os
import pandas as pd
OUT = sys.argv[1]
a = json.load(open(f"{OUT}/after_now.json"))
pairs = {(r["canonical_player_id"], r["canonical_team_id"]): r["player_name"] for r in a["participation_non_moot"]}
dec = {(r["canonical_player_id"], r["canonical_team_id"]): r["decision"] for r in a["participation_non_moot"]}
runs = sorted(d for d in glob.glob("/var/lib/sports-models/cbb-originator/prod/runs/R202611*") if os.path.basename(d) >= "R20261120")
rows = []
cols_seen = None
for d in runs:
    u = f"{d}/player_universe.csv"; m = f"{d}/model_inputs.parquet"
    if not os.path.exists(u):
        continue
    uv = pd.read_csv(u, usecols=["game_id", "team_id", "canonical_player_id", "roster_status", "in_frozen_pool", "pool_evidence", "scoreability", "suppression_reason"])
    key = list(zip(uv.canonical_player_id, uv.team_id))
    uv = uv[[k in pairs for k in key]]
    mi = pd.read_parquet(m) if os.path.exists(m) else pd.DataFrame()
    if cols_seen is None and len(mi):
        cols_seen = list(mi.columns)
    for r in uv.itertuples(index=False):
        x = mi[(mi.get("game_id") == r.game_id) & (mi.get("player_id") == r.canonical_player_id)] if len(mi) else mi
        rows.append({"run": os.path.basename(d), "game_id": r.game_id, "pid": r.canonical_player_id, "name": pairs[(r.canonical_player_id, r.team_id)],
                     "decision": dec[(r.canonical_player_id, r.team_id)], "roster_status": r.roster_status, "in_pool": bool(r.in_frozen_pool),
                     "pool_evidence": r.pool_evidence, "scoreability": r.scoreability, "in_model_inputs": bool(len(x)),
                     "p_play": float(x["p_play"].iloc[0]) if len(x) and "p_play" in x else None,
                     "min_mu": float(x["min_mu"].iloc[0]) if len(x) and "min_mu" in x else None})
df = pd.DataFrame(rows)
df.to_csv(f"{OUT}/predictive_presence.csv", index=False)
per = df.drop_duplicates(["game_id", "pid"])
summ = {"runs_scanned": len(runs), "model_input_columns_sample": (cols_seen or [])[:60],
        "player_game_rows": int(len(per)), "players_seen": int(per.pid.nunique()),
        "by_decision_pool_inputs": per.groupby(["decision", "in_pool", "in_model_inputs", "scoreability"]).size().reset_index().to_dict("records"),
        "pool_evidence": per.pool_evidence.value_counts(dropna=False).to_dict(),
        "players_never_in_pool": sorted(set(per[~per.in_pool].name) - set(per[per.in_pool].name)),
        "applied_players_not_seen_in_any_run": sorted(set(n for k, n in pairs.items() if dec[k] == "APPLIED") - set(per.name))}
json.dump(summ, open(f"{OUT}/predictive_presence.json", "w"), indent=1, default=str)
print(json.dumps(summ, indent=1, default=str))
