"""Phase 2: historical player transition panel (t-1 -> t) from the CBBD Starter Pack, joined ONLY by persistent athleteId.

Output (gitignored, research): data/qa/large/rootcause_20261126/transition_panel.parquet, one row per player-season t:
    athleteId, canonical_player_id, season, team (canonical), cls {SAME_TEAM_RETURNER, TRANSFER, NO_PRIOR_HISTORY},
    prior (t-1) games/starts/minutes/mpg/usage/per-minute rates/ORtg/DRtg/PORPAG, origin team (canonical) and its t-1
    adjusted net, destination team t-1 adjusted net (dest_net_prev), dstr = dest_net_prev - origin_net_prev,
    conference change; outcomes of season t (for fitting/diagnostics only: never a feature of season t).
2027: destination = the 2027 CBBD roster (production cache, 2026-11-12), outcomes absent.
Also writes results/r1_panel_audit.json (coverage, duplicates, unmatched ids)."""
import gzip, glob, json, os
import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
SP = os.path.join(ROOT, "../CBB_MOV_GT_TT/CBB_Player_Input_Game_Level_Output/starter_pack_2026/data")
OUT = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
STATS = {"PTS": "points", "AST": "assists", "STL": "steals", "BLK": "blocks", "TOV": "turnovers", "REB": "rebounds_total",
         "FG3M": "threePointFieldGoals_made", "FG3A": "threePointFieldGoals_attempted", "FGA": "fieldGoals_attempted",
         "FTA": "freeThrows_attempted"}
COLS = ["season", "teamId", "conference", "athleteId", "games", "starts", "minutes", "usage", "offensiveRating",
        "defensiveRating", "PORPAG"] + list(STATS.values())

c = duckdb.connect(os.path.join(ROOT, "data/lockbox_2025_26/canonical/cbb.duckdb"), read_only=True)
pmap = dict(c.execute("select provider_player_key, canonical_player_id from metadata.player_id_map where provider='CBBD'").fetchall())
tmap = dict(c.execute("select provider_team_key, canonical_team_id from metadata.team_id_map where provider='CBBD'").fetchall())
c.close()
adj = pd.read_csv(f"{SP}/adjusted_efficiency.csv")
net = {(int(r.team_id), int(r.season)): float(r.net) for r in adj.itertuples()}

seasons = sorted(int(os.path.basename(f)[:4]) for f in glob.glob(f"{SP}/player_season_stats/*.csv"))
ps = {}
for y in seasons:
    d = pd.read_csv(f"{SP}/player_season_stats/{y}.csv", usecols=lambda x: x in COLS)
    d = d.sort_values("minutes", ascending=False).drop_duplicates("athleteId")        # 20 dup ids in 2025: keep max-minutes row
    ps[y] = d
audit = {"seasons": seasons, "dup_athlete_ids_dropped": {}, "unmatched": {}}
for y in seasons:
    raw = pd.read_csv(f"{SP}/player_season_stats/{y}.csv", usecols=["athleteId"])
    audit["dup_athlete_ids_dropped"][y] = int(raw.athleteId.duplicated().sum())


def roster2027():
    f = glob.glob(os.path.join(OUT, "cbbd_prod", "20261112*.json.gz"))[0]
    b = json.load(gzip.open(f))
    return pd.DataFrame([{"teamId": t["teamId"], "athleteId": p["id"]} for t in b for p in t["players"]])


rows = []
for y in [s for s in seasons if s - 1 in ps] + [2027]:
    prev = ps[y - 1].set_index("athleteId")
    cur = roster2027() if y == 2027 else ps[y][["athleteId", "teamId"] + [v for v in COLS if v not in ("season", "athleteId", "teamId")]]
    cur = cur.copy()
    cur["season"] = y
    j = cur.join(prev, on="athleteId", rsuffix="_p")
    has = j["teamId_p"].notna()
    j["cls"] = np.where(~has, "NO_PRIOR_HISTORY", np.where(j["teamId"] == j["teamId_p"], "SAME_TEAM_RETURNER", "TRANSFER"))
    j["origin_net_prev"] = [net.get((int(t), y - 1), np.nan) if pd.notna(t) else np.nan for t in j["teamId_p"]]
    j["dest_net_prev"] = [net.get((int(t), y - 1), np.nan) for t in j["teamId"]]
    j["dstr"] = j["dest_net_prev"] - j["origin_net_prev"]
    rows.append(j)
P = pd.concat(rows, ignore_index=True)
P["canonical_player_id"] = P["athleteId"].astype(str).map(pmap)
P["team"] = P["teamId"].astype(int).astype(str).map(tmap)
P["origin_team"] = P["teamId_p"].map(lambda t: tmap.get(str(int(t))) if pd.notna(t) else None)
P["mpg_p"] = P["minutes_p"] / P["games_p"].clip(lower=1)
for s, col in STATS.items():
    P[f"{s}_pm_p"] = P[f"{col}_p"] / P["minutes_p"].clip(lower=1)
    if f"{col}" in P:
        P[f"{s}_pm"] = P[col] / P["minutes"].clip(lower=1)
P["conf_change"] = (P["conference"] != P["conference_p"]) & P["conference_p"].notna() if "conference" in P else np.nan
keep = ["athleteId", "canonical_player_id", "season", "team", "teamId", "cls", "origin_team", "teamId_p", "origin_net_prev",
        "dest_net_prev", "dstr", "conf_change", "games_p", "starts_p", "minutes_p", "mpg_p", "usage_p", "offensiveRating_p",
        "defensiveRating_p", "PORPAG_p", "games", "minutes", "usage"] + [c for c in P if c.endswith("_pm_p") or c.endswith("_pm")]
P = P[[k for k in keep if k in P]]
os.makedirs(OUT, exist_ok=True)
P.to_parquet(os.path.join(OUT, "transition_panel.parquet"), index=False)
for y, g in P.groupby("season"):
    audit["unmatched"][int(y)] = {"rows": int(len(g)), "no_canonical_player": int(g.canonical_player_id.isna().sum()),
                                  "no_canonical_team": int(g.team.isna().sum()),
                                  "cls": g.cls.value_counts().to_dict(),
                                  "transfer_no_origin_strength": int((g.cls.eq("TRANSFER") & g.origin_net_prev.isna()).sum())}
audit["rights"] = "Starter Pack = RESEARCH_ONLY (rights register); 2027 roster = production CBBD API cache; panel is research-only, gitignored"
json.dump(audit, open(os.path.join(RES, "r1_panel_audit.json"), "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in audit["unmatched"].items() if k >= 2022}, indent=0, default=str))
