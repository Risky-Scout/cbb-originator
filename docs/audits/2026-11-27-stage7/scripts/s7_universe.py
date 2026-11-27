"""Stage 07, read-only: the complete 2026-27 game and player universe at a frozen evaluation cutoff.

Inputs, all retained production evidence:
* every retained state version: the information state in force at each game's pregame cutoff;
* the current state at the cutoff: outcomes, box lines, schedule status;
* every forecast snapshot and official tick pmfs.parquet: genuine prospective PMFs (prediction_as_of < tip);
* every run's player_universe.csv / run_summary.json: the pregame player pool and the per-game reasons;
* the frozen market evaluations;
* obs_roster.parquet (s7_observation_ledger): the first observation of each roster membership.

    s7_universe.py OUT EVAL_CUTOFF_UTC
"""
import glob, json, os, sys
import duckdb, numpy as np, pandas as pd
OUT, CUT = sys.argv[1], pd.Timestamp(sys.argv[2])
P = "/var/lib/sports-models/cbb-originator/prod"
V = f"{P}/state/versions"
# --------------------------------------------------------------- information states (versions) and the cutoff state
vers = []
for d in sorted(glob.glob(f"{V}/S2026*")):
    m = json.load(open(f"{d}/state_manifest.json"))
    vers.append((pd.Timestamp(m["created_at"]), pd.Timestamp(m["source_cutoff"]), os.path.basename(d)))
vers = pd.DataFrame(vers, columns=["created_at", "source_cutoff", "version"]).sort_values("created_at")
eval_v = vers[(vers.created_at <= CUT) & (vers.source_cutoff <= CUT)].iloc[-1]
FIRST = vers.iloc[0]
con = duckdb.connect(f"{V}/{eval_v.version}/increment.duckdb", read_only=True, config={"threads": 1, "memory_limit": "600MB"})
g = con.execute("""select g.canonical_game_id game_id, g.scheduled_tip_utc tip, g.game_universe, g.home_team_id, g.away_team_id,
                   g.home_points, g.away_points, g.has_player_box_score, g.has_team_box_score, s.status sched_status
                   from flagship.games g left join metadata.prod_schedule s using (canonical_game_id)
                   where g.season = 2027 and g.scheduled_tip_utc <= ?""", [CUT]).df()
box = con.execute("""select canonical_game_id game_id, canonical_player_id player_id, canonical_team_id team_id, minutes,
                     identity_status, historical_knowable_at from flagship.player_game_outcomes where season = 2027""").df()
con.close()
g["tip"] = pd.to_datetime(g["tip"], utc=True)
st = g["sched_status"].fillna("").str.lower()
g["outcome"] = np.where(g.home_points.notna() & g.has_player_box_score.fillna(False) & g.has_team_box_score.fillna(False),
                        "COMPLETE", np.where(g.home_points.notna(), "RESULT_WITHOUT_FULL_BOX",
                        np.where(st.isin(["postponed", "cancelled", "canceled"]), "POSTPONED_OR_CANCELLED",
                        np.where(st == "in_progress", "IN_PROGRESS_AT_CUTOFF", "MISSING_RESULT"))))
# exact pregame information state: latest version acquired by the pregame cutoff (tip - 1 h)
pc = g["tip"] - pd.Timedelta(hours=1)
idx = np.searchsorted(vers["source_cutoff"].to_numpy(dtype="datetime64[ns]"), pc.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]"), side="right") - 1
g["pregame_state_version"] = [vers.version.iloc[i] if i >= 0 else None for i in idx]
g["information_state"] = np.where(idx < 0, "NO_RETAINED_STATE_BEFORE_CUTOFF", "EXACT_RETAINED_STATE")
# sync-log promotions that are not retained (the GitHub era before the 11-07 cutover): a pregame cutoff whose
# in-force promoted version is missing is only CONSERVATIVE (the earlier retained version is a lower bound)
promos = []
for l in open(f"{P}/sync/sync_log.jsonl"):
    x = json.loads(l)
    if x.get("status") == "PROMOTED" or x.get("outcome") == "SUCCESS":
        if x.get("state_version") and x.get("source_cutoff"):
            promos.append((pd.Timestamp(x["source_cutoff"]), x["state_version"]))
promos = pd.DataFrame(promos, columns=["source_cutoff", "version"]).sort_values("source_cutoff")
retained = set(vers.version)
pi = np.searchsorted(promos["source_cutoff"].to_numpy(dtype="datetime64[ns]"), pc.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]"), side="right") - 1
in_force = [promos.version.iloc[i] if i >= 0 else None for i in pi]
g["in_force_promoted_version"] = in_force
g.loc[(g.information_state == "EXACT_RETAINED_STATE") & np.array([v is not None and v not in retained for v in in_force]),
      "information_state"] = "CONSERVATIVE_EARLIER_RETAINED_STATE"
# --------------------------------------------------------------- prospective PMFs (snapshots + ticks)
pm = []
for f in sorted(glob.glob(f"{P}/ops/forecasts/*/*/pmfs.parquet")) + sorted(glob.glob(f"{P}/ops/ticks/*/pmfs.parquet")):
    try:
        d = pd.read_parquet(f)
    except Exception:
        continue
    if not len(d):
        continue
    kind = "SNAPSHOT" if "/forecasts/" in f else "TICK"
    for c in ("engine_id", "method", "model_version"):          # older files predate engine attribution
        if c not in d:
            d[c] = None
    if "probabilities" in d:
        s = d["probabilities"].map(lambda a: float(np.sum(a)))
        mn = d["probabilities"].map(lambda a: float(np.min(a)) if len(a) else -1.0)
        a = d[["game_id", "player_id", "stat", "prediction_as_of", "model_version", "engine_id", "method"]].copy()
        a["valid"] = (np.abs(s - 1) <= 1e-6) & (mn >= 0)
    else:                                                     # legacy long format
        a = d.fillna({"engine_id": "", "method": "", "model_version": ""}).groupby(
            ["game_id", "player_id", "stat", "prediction_as_of", "model_version", "engine_id", "method"], dropna=False)["probability"].agg(lambda p: bool(abs(p.sum() - 1) <= 1e-6 and p.min() >= 0)).reset_index(name="valid")
    a["kind"] = kind
    pm.append(a)
pm = pd.concat(pm, ignore_index=True)
pm["prediction_as_of"] = pd.to_datetime(pm["prediction_as_of"], utc=True)
pm = pm.merge(g[["game_id", "tip"]], on="game_id", how="inner")
pm = pm[pm.prediction_as_of < pm.tip]
pm["frozen_model"] = pm["model_version"].astype(str).eq("cbb-v2-jm1-final-shadow-1.0.0")
pmg = pm.groupby("game_id").agg(pmf_rows=("valid", "size"), pmf_invalid=("valid", lambda v: int((~v).sum())),
                                first_forecast_at=("prediction_as_of", "min"), snapshot_rows=("kind", lambda k: int((k == "SNAPSHOT").sum())),
                                daily_model_rows=("frozen_model", lambda x: int((~x).sum())))
pmp = pm.groupby(["game_id", "player_id"]).agg(stats=("stat", "nunique"), valid_pmfs=("valid", "sum"),
                                               priced=("method", lambda m: bool((m != "NOT_PRICED").any())),
                                               any_daily_model=("frozen_model", lambda x: bool((~x).any()))).reset_index()
# --------------------------------------------------------------- pregame pool + reasons (latest pregame run per game)
pool, reasons = [], {}
for d in sorted(glob.glob(f"{P}/runs/R2026*")):
    try:
        s = json.load(open(f"{d}/run_summary.json"))
    except Exception:
        continue
    aso = s.get("prediction_as_of")
    if not aso:
        continue
    aso = pd.Timestamp(aso)
    for gid, rs in (s.get("game_reasons") or {}).items():
        reasons.setdefault(gid, []).append((aso, rs))
    u = f"{d}/player_universe.csv"
    if os.path.exists(u):
        x = pd.read_csv(u, usecols=["game_id", "team_id", "canonical_player_id", "roster_status", "in_frozen_pool",
                                    "scoreability", "suppression_reason"])
        x["as_of"] = aso
        pool.append(x)
pool = pd.concat(pool, ignore_index=True).rename(columns={"canonical_player_id": "player_id"})
pool = pool.merge(g[["game_id", "tip"]], on="game_id", how="inner")
pool = pool[pool.as_of < pool.tip].sort_values("as_of").groupby(["game_id", "player_id"]).tail(1)
last_reason = {}
for gid, lst in reasons.items():
    tip = g.loc[g.game_id == gid, "tip"]
    if len(tip):
        pre = [r for a, r in lst if a < tip.iloc[0]]
        if pre:
            last_reason[gid] = "|".join(sorted(set(pre[-1]))) or "SCORED"
# --------------------------------------------------------------- market observations (frozen evaluations)
mk_p, mk_g = set(), set()
for f in glob.glob(f"{P}/ops/evaluations/*/market_evaluations.jsonl"):
    for l in open(f):
        x = json.loads(l)
        mk_p.add((x.get("game_id"), x.get("player_id"), x.get("stat")))
for f in glob.glob(f"{P}/ops/evaluations/*/game_market_evaluations.jsonl"):
    for l in open(f):
        mk_g.add(json.loads(l).get("game_id"))
mkp = pd.DataFrame(list(mk_p), columns=["game_id", "player_id", "stat"])
# --------------------------------------------------------------- game table
G = g.merge(pmg, on="game_id", how="left")
G["prospective_forecast"] = G.pmf_rows.fillna(0) > 0
G["player_market_observations"] = G.game_id.map(mkp.groupby("game_id").size()).fillna(0).astype(int)
G["game_market_observation"] = G.game_id.isin(mk_g)
G["pregame_reason"] = G.game_id.map(last_reason)
G["missing_forecast_reason"] = np.where(G.prospective_forecast, "",
                               np.where(G.tip < pd.Timestamp("2026-11-08T12:00:00Z"), "BEFORE_DROPLET_PRODUCTION_FORECASTS",
                               np.where(G.game_universe.fillna("") != "DI_VS_DI", "NOT_DI_VS_DI",
                               G.pregame_reason.fillna("NEVER_SLATED_PREGAME"))))
G.to_csv(f"{OUT}/universe_games.csv", index=False)
# --------------------------------------------------------------- player table (completed games)
comp = G[G.outcome == "COMPLETE"]
bx = box[box.game_id.isin(comp.game_id)].copy()
bx["played"] = bx["minutes"].fillna(0) > 0
ro = pd.read_parquet(f"{OUT}/obs_roster.parquet")
ro["first_at"] = pd.to_datetime(ro["first_at"], utc=True)
rkey = ro.set_index(["canonical_player_id", "canonical_team_id"])["first_at"]
U = bx.merge(pool[["game_id", "player_id", "roster_status", "in_frozen_pool", "scoreability", "suppression_reason"]],
             on=["game_id", "player_id"], how="outer", indicator="pool_join")
U = U.merge(pmp, on=["game_id", "player_id"], how="left").merge(comp[["game_id", "tip", "information_state"]], on="game_id", how="inner")
U["played"] = U["played"].astype("boolean").fillna(False).astype(bool)
U["in_box"] = U.pool_join.isin(["both", "left_only"])
U["in_pregame_pool"] = U.pool_join.isin(["both", "right_only"])
U["forecast"] = U.stats.fillna(0) > 0
fa = [rkey.get((p, t)) for p, t in zip(U.player_id, U.team_id)]
U["roster_first_observed_at"] = fa
U["roster_membership_at_pregame"] = np.where(pd.isna(U.roster_first_observed_at), "NO_ROSTER_ROW_AT_TEAM",
                                    np.where(pd.to_datetime(U.roster_first_observed_at, utc=True) <= U.tip - pd.Timedelta(hours=1),
                                             "ON_ROSTER_BY_PREGAME", "ROSTER_ROW_FIRST_SEEN_AFTER_PREGAME"))
U["category"] = np.select(
    [U.in_box & U.played & U.forecast, U.in_box & U.played & U.in_pregame_pool & ~U.forecast,
     U.in_box & U.played & ~U.in_pregame_pool, U.in_box & ~U.played & U.forecast, U.in_box & ~U.played & ~U.forecast,
     ~U.in_box & U.forecast, ~U.in_box & ~U.forecast],
    ["PLAYED_FORECAST", "PLAYED_IN_POOL_NOT_FORECAST", "PLAYED_UNEXPECTED_NOT_IN_POOL", "DNP_FORECAST",
     "DNP_NOT_FORECAST", "POOL_NO_BOX_LINE_FORECAST", "POOL_NO_BOX_LINE_NOT_FORECAST"], "OTHER")
U["market_overlap_stats"] = U.set_index(["game_id", "player_id"]).index.map(mkp.groupby(["game_id", "player_id"]).size()).fillna(0).astype(int)
keep = ["game_id", "player_id", "team_id", "minutes", "played", "identity_status", "in_box", "in_pregame_pool", "roster_status",
        "in_frozen_pool", "scoreability", "suppression_reason", "forecast", "stats", "valid_pmfs", "priced", "any_daily_model",
        "roster_membership_at_pregame", "information_state", "category", "market_overlap_stats"]
U[keep].to_parquet(f"{OUT}/universe_players.parquet", index=False)
# --------------------------------------------------------------- summary
def vc(s):
    return {str(k): int(v) for k, v in s.value_counts(dropna=False).items()}
rep = {"evaluation_cutoff_utc": CUT.isoformat(), "evaluation_state_version": eval_v.version,
       "evaluation_state_source_cutoff": str(eval_v.source_cutoff), "first_retained_state": FIRST.version,
       "first_retained_created_at": str(FIRST.created_at), "retained_versions": int(len(vers)),
       "sync_promotions": int(len(promos)), "promotions_not_retained": int(sum(v not in retained for v in promos.version)),
       "games": {"scheduled_tip_by_cutoff": int(len(G)), "by_universe": vc(G.game_universe), "outcome": vc(G.outcome),
                 "complete_by_universe": vc(G.loc[G.outcome == "COMPLETE", "game_universe"]),
                 "information_state": vc(G.information_state),
                 "complete_di_information_state": vc(G.loc[(G.outcome == "COMPLETE") & (G.game_universe == "DI_VS_DI"), "information_state"]),
                 "prospective_forecast": int(G.prospective_forecast.sum()),
                 "complete_di_with_forecast": int((G.prospective_forecast & (G.outcome == "COMPLETE") & (G.game_universe == "DI_VS_DI")).sum()),
                 "complete_di": int(((G.outcome == "COMPLETE") & (G.game_universe == "DI_VS_DI")).sum()),
                 "missing_forecast_reason_complete_di": vc(G.loc[(G.outcome == "COMPLETE") & (G.game_universe == "DI_VS_DI") & ~G.prospective_forecast, "missing_forecast_reason"]),
                 "games_with_invalid_pmfs": int((G.pmf_invalid.fillna(0) > 0).sum()),
                 "player_market_observation_games": int((G.player_market_observations > 0).sum()),
                 "game_market_observation_games": int(G.game_market_observation.sum())},
       "players_complete_games": {"rows": int(len(U)), "category": vc(U.category), "box_lines": int(U.in_box.sum()),
                                  "played": int((U.in_box & U.played).sum()), "dnp_box_lines": int((U.in_box & ~U.played).sum()),
                                  "pregame_pool": int(U.in_pregame_pool.sum()), "forecast_players": int(U.forecast.sum()),
                                  "forecast_players_all_12_stats": int((U.stats == 12).sum()),
                                  "valid_pmfs": int(U.valid_pmfs.fillna(0).sum()),
                                  "identity_status_box": vc(U.loc[U.in_box, "identity_status"]),
                                  "roster_membership_at_pregame_played": vc(U.loc[U.in_box & U.played, "roster_membership_at_pregame"]),
                                  "played_not_forecast_suppression": vc(U.loc[U.category == "PLAYED_IN_POOL_NOT_FORECAST", "suppression_reason"]),
                                  "market_overlap_player_games": int((U.market_overlap_stats > 0).sum()),
                                  "market_overlap_rows": int(U.market_overlap_stats.sum())},
       "pmf_rows_pregame": int(len(pm)), "pmf_rows_invalid": int((~pm.valid).sum()),
       "pmf_rows_frozen_model": int(pm.frozen_model.sum())}
json.dump(rep, open(f"{OUT}/universe_summary.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str))
