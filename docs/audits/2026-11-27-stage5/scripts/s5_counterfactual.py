"""Stage 05 controlled counterfactuals with the ACTUAL production functions (read-only, isolated; nothing written to
production state). One real game from an existing production run (stored final per-player model inputs, after every
promoted adjustment); availability statuses are swapped per scenario; seed fixed (D.game_seed); SIMS draws.
Player layer: prod.inputs.apply_availability -> prod.simulate.simulate_game -> market_distributions.
Game layer : game.forecast.team_strength (S = sum p_play*PTS_mu, scored set) -> delta vs the baseline scenario (as the
             INITIAL snapshot) -> game.model.targets/build -> game.markets.summary.
Usage: s5_counterfactual.py OUT RUN_ID SNAPSHOT_DIR [GAME_ID] [SIMS]"""
import json, sys
import numpy as np, pandas as pd
from pathlib import Path
from cbb_platform.prod import inputs as IN, simulate as SM, loader as LD, features as PF, config as PC
from cbb_platform.game import forecast as GF, model as GMod, params as GP, markets as GM
from cbb_platform.v2 import joint as J

OUT, RUN, SNAP = sys.argv[1], sys.argv[2], Path(sys.argv[3])
GID = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
SIMS = int(sys.argv[5]) if len(sys.argv) > 5 else 4000
R = Path("/var/lib/sports-models/cbb-originator/prod/runs") / RUN
model = LD.load()
mi = pd.read_parquet(R / "model_inputs.parquet")
gpm = pd.read_parquet(SNAP / "game_pmfs.parquet")
if GID is None:                       # richest ungated game present in both layers, both teams with 5 TOP5 players
    ok = mi[mi.game_id.isin(set(gpm.game_id))]
    s = ok.groupby("game_id").agg(n=("player_id", "size"), t5=("role_diagnostic", lambda x: (x == "TOP5_MINUTES").sum()),
                                  st=("availability_status", lambda x: (x != "NO_REPORT").sum()))
    GID = s[(s.t5 >= 10) & (s.st == 0)].sort_values("n", ascending=False).index[0]
base = mi[mi.game_id == GID].drop(columns=[c for c in ("availability_status", "availability_received_at", "player_suppression",
                                                       "availability_source", "availability_raw_status", "availability_gate") if c in mi])
fr = PF.model_frame(R / "features" / "features_asof.duckdb", model.marginal_cfg, [GID], PC.PRODUCTION_SEASON)
envd = IN.environment(fr["team"], model.jm1["struct"]["env"])
g0 = gpm[gpm.game_id == GID].iloc[0]
home, away = g0.home_team_id, g0.away_team_id
gm = GP.game_model(None); eot = GP.endgame_ot(None)
vpar, adj = GMod.VarianceParams(**gm["variance"]), GMod.AdjustParams(**gm["adjust"])

# scenario subjects
hp = base[base.team_id == home].sort_values("min_mu", ascending=False)
starter = hp.iloc[0]; reserve = hp[hp.role_diagnostic != "TOP5_MINUTES"].iloc[0]
low = base.sort_values("p_play").iloc[0]
t0 = pd.Timestamp(g0.prediction_as_of)
def av(*items):
    return pd.DataFrame([{"game_id": GID, "player_id": p, "team_id": t, "status": s, "received_at": t0 - pd.Timedelta(minutes=m),
                          "availability_source": src, "availability_raw_status": s, "availability_gate": gate}
                         for p, t, s, gate, m, src in items],
                        columns=["game_id", "player_id", "team_id", "status", "received_at", "availability_source",
                                 "availability_raw_status", "availability_gate"])
S, Rv = starter.player_id, reserve.player_id
SC = {
 "J_missing_baseline": None,
 "A_out_starter": av((S, home, "OUT", "", 30, "ROTOWIRE_WEB")),
 "B_out_reserve": av((Rv, home, "OUT", "", 30, "ROTOWIRE_WEB")),
 "C_gtd_starter": av((S, home, "GTD", "AVAILABILITY_UNCERTAIN", 30, "ROTOWIRE_WEB")),
 "D_doubtful_starter": av((S, home, "DOUBTFUL", "AVAILABILITY_UNCERTAIN", 30, "ROTOWIRE_WEB")),
 "E_active_starter": av((S, home, "ACTIVE", "", 30, "ROTOWIRE_WEB")),
 "F_reserve_confirmed_starter": av((Rv, home, "ACTIVE", "", 30, "ROTOWIRE_WEB")),   # 'will start' -> ACTIVE (only mechanism)
 "G_starter_to_bench": None,                       # no source / no status: 'coming off the bench' is not classified
 "H_return_from_absence": av((low.player_id, low.team_id, "ACTIVE", "", 30, "ROTOWIRE_WEB")),
 "I_ineligible_curated": av((S, home, "UNAVAILABLE", "", 30, "CURATED_VERIFIED_STATE")),
 "K_conflict_starter": av((S, home, "UNKNOWN", "AVAILABILITY_SOURCE_CONFLICT", 30, "ROTOWIRE_WEB")),
 "L_stale_out_then_newer_active": av((S, home, "OUT", "", 120, "ROTOWIRE_WEB"), (S, home, "ACTIVE", "", 10, "ROTOWIRE_WEB")),
 "L_newer_out_after_stale_active": av((S, home, "ACTIVE", "", 120, "ROTOWIRE_WEB"), (S, home, "OUT", "", 10, "ROTOWIRE_WEB")),
}
def tails(d, i, s, k):
    return float((d[:, i, J.SI[s]] >= k).mean())
res, Sbase = {}, None
for name, a in SC.items():
    rows, flags = IN.apply_availability(base.copy(), a)
    gp = rows[rows.game_id == GID]
    bank = SM.simulate_game(model.jm1, GID, gp, envd, SIMS)
    br, dr, Mx = bank["rows"], bank["draws"], bank["M"]
    md = SM.market_distributions(bank)
    per = {}
    for i, r in enumerate(br.itertuples(index=False)):
        pm = md[md.player_id == r.player_id].set_index("market")["projection"].to_dict()
        per[r.player_id] = {"team": "H" if r.team_id == home else "A", "p_play": float(r.p_play), "min_mu_input": float(r.min_mu),
                            "min_sim_mean": float(Mx[:, i].mean()), "min_sim_sd": float(Mx[:, i].std()),
                            **{f"{s}_mean": float(pm[s]) for s in PC.BASE_STATS}, **{f"{c}_mean": float(pm[c]) for c in PC.COMBOS},
                            "PTS_sd": float(dr[:, i, J.SI["PTS"]].std()), "P_PTS_ge20": tails(dr, i, "PTS", 20),
                            "P_REB_ge10": tails(dr, i, "REB", 10)}
    sup = gp[gp.player_suppression != ""][["player_id", "player_suppression", "availability_status"]].to_dict("records")
    Sd = GF.team_strength(gp)
    if Sbase is None:
        Sbase = Sd
    dh, da = Sd.get((GID, home), 0) - Sbase.get((GID, home), 0), Sd.get((GID, away), 0) - Sbase.get((GID, away), 0)
    t = GMod.targets(float(g0.top_down_home), float(g0.top_down_away), int(g0.games_played_min), vpar, adj, dh, da)
    g = GF.canonical(GMod.build(t, eot))
    sm = {k: float(v) for k, v in GM.summary(g).items()}
    res[name] = {"flags": flags.get(GID, {}), "suppressed": sup, "priced_players": int(len(br)), "S_home": Sd.get((GID, home)),
                 "S_away": Sd.get((GID, away)), "delta_home": dh, "delta_away": da,
                 "target_exp_home": t.exp_home, "target_exp_away": t.exp_away, "game_summary": sm,
                 "players": per, "bank_sha256": bank["bank_sha256"]}
subj = {"game_id": GID, "home": home, "away": away, "starter": [S, float(starter.min_mu), float(starter.p_play), starter.role_diagnostic],
        "reserve": [Rv, float(reserve.min_mu), float(reserve.p_play), reserve.role_diagnostic],
        "low_p_play": [low.player_id, float(low.p_play), float(low.min_mu)], "sims": SIMS, "run": RUN, "snapshot": SNAP.name,
        "gamma": adj.gamma, "beta": adj.beta}
json.dump({"subjects": subj, "scenarios": res}, open(f"{OUT}/counterfactual_{GID}.json", "w"), indent=1, default=str)
# compact deltas vs baseline
b = res["J_missing_baseline"]
def dlt(name):
    r = res[name]; o = {"flags": r["flags"].get("reasons"), "suppressed": [s["player_id"] for s in r["suppressed"]],
                        "priced": r["priced_players"], "dS_home": round(r["delta_home"], 3),
                        "d_exp_home": round(r["target_exp_home"] - b["target_exp_home"], 3)}
    gs, gb = r["game_summary"], b["game_summary"]
    o["game_delta"] = {k: round(gs[k] - gb[k], 4) for k in gs if k in gb}
    for who, pid in (("starter", S), ("reserve", Rv)):
        if pid in r["players"] and pid in b["players"]:
            o[who] = {k: round(r["players"][pid][k] - b["players"][pid][k], 4) for k in ("p_play", "min_sim_mean", "PTS_mean", "PRA_mean", "P_PTS_ge20") if k in r["players"][pid]}
        else:
            o[who] = "NOT_IN_BANK" if pid not in r["players"] else "ADDED"
    mates = [p for p in b["players"] if p in r["players"] and p not in (S, Rv) and b["players"][p]["team"] == "H"]
    o["teammates_max_abs_d_min_input"] = max(abs(r["players"][p]["min_mu_input"] - b["players"][p]["min_mu_input"]) for p in mates)
    o["teammates_max_abs_d_PTS_mean"] = max(abs(r["players"][p]["PTS_mean"] - b["players"][p]["PTS_mean"]) for p in mates)
    o["teammates_max_abs_d_min_sim"] = max(abs(r["players"][p]["min_sim_mean"] - b["players"][p]["min_sim_mean"]) for p in mates)
    return o
print(json.dumps({"subjects": subj, "game_summary_keys": list(b["game_summary"].keys()),
                  "baseline_game": b["game_summary"], "deltas": {k: dlt(k) for k in res if k != "J_missing_baseline"}}, indent=1, default=str))
