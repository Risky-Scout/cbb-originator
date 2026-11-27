"""Stage baseline (read-only). Reads CBB production state; writes only into OUT."""
import collections, glob, hashlib, json, os, sys
REL = os.path.realpath("/opt/sports-models/cbb-originator/current")
sys.path.insert(0, REL)
import numpy as np, pandas as pd, pyarrow.parquet as pq
from cbb_platform.prod import pmf_compact as PCX, config as PC
from cbb_platform.game import forecast as GF
from cbb_platform.v2 import joint as VJ

OUT = sys.argv[1]
S = "/var/lib/sports-models/cbb-originator"
O = S + "/prod/ops"
FC = {"morning_INITIAL_0700": "F20261126T140651Z-2bdce5",
      "refresh_ROTOWIRE_1300_18Z": "F20261126T180352Z-e2916f",
      "refresh_ROTOWIRE_1800_23Z": "F20261126T230340Z-7f5614"}


def fsha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def ledger_chain(path):
    n, bad_hash, bad_link, prev, types = 0, 0, 0, None, collections.Counter()
    first_prev = None
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            body = {k: v for k, v in r.items() if k != "row_hash"}
            if hashlib.sha256(canon(body).encode()).hexdigest() != r["row_hash"]:
                bad_hash += 1
            if n == 0:
                first_prev = r.get("prev_row_hash")
            elif r.get("prev_row_hash") != prev:
                bad_link += 1
            prev = r["row_hash"]; n += 1; types[r.get("record_type")] += 1
    return {"rows": n, "row_hash_mismatch": bad_hash, "chain_link_breaks": bad_link, "genesis_prev": first_prev,
            "head_row_hash": prev, "record_types": dict(types)}


idx = {}
with open(O + "/forecasts/index.jsonl") as f:
    for line in f:
        d = json.loads(line)
        if d.get("snapshot_id") in FC.values():
            idx[d["snapshot_id"]] = hashlib.sha256(line.rstrip("\n").encode()).hexdigest()

res, frames = {}, {}
for k, fid in FC.items():
    d = f"{O}/forecasts/2026-11-26/{fid}"
    files = {n: fsha(f"{d}/{n}") for n in sorted(os.listdir(d))}
    snap = json.load(open(d + "/snapshot.json")); gf = json.load(open(d + "/game_forecast.json"))
    pm = pq.read_table(d + "/pmfs.parquet").to_pandas()
    gp = pq.read_table(d + "/game_pmfs.parquet").to_pandas()
    fp = pq.read_table(d + "/forecast_players.parquet").to_pandas()
    pbad = sum(PCX.content_sha(int(r.support_min), np.asarray(r.probabilities, dtype=np.float64)) != r.pmf_sha256
               for r in pm.itertuples())
    psum = float(max(abs(np.sum(np.asarray(p, dtype=np.float64)) - 1) for p in pm.probabilities))
    gbad = 0
    for r in gp.to_dict("records"):
        g = GF.decode(r["pmf_zlib"], r["h_min"], r["a_min"], (int(r["rows"]), int(r["cols"])))
        gbad += g.sha256() != r["pmf_sha256"]
    frames[k] = (pm, gp, fp, snap)
    res[k] = {
        "forecast_id": fid, "run_id": snap["run_id"], "code_commit": snap["code_commit"], "window": snap["window"],
        "executed_at": snap["executed_at"], "engine_id": snap["engine_id"], "model_version": snap["model_version"],
        "state_version": snap["state_version"], "run_reasons": snap["run_reasons"],
        "readiness_state": snap["readiness_state"], "artifact_sha256": snap["artifact_sha256"],
        "file_sha256": files, "index_line_sha256": idx.get(fid),
        "snapshot_pmfs_sha256_matches_file": snap["pmfs_sha256"] == files["pmfs.parquet"],
        "game_forecast_file_sha256_matches_file": gf["file_sha256"] == files["game_pmfs.parquet"],
        "player_pmf_rows": len(pm), "player_pmf_sha_recompute_mismatch": int(pbad), "player_pmf_max_abs_sum_err": psum,
        "game_pmf_rows": len(gp), "game_pmf_sha_recompute_mismatch": int(gbad),
        "game_labels": gp.groupby(["model_version", "params_sha256", "joint_version"]).size().reset_index()
                          .assign(params_sha256=lambda x: x.params_sha256.str[:12]).values.tolist(),
        "game_params_sha256": sorted(gp.params_sha256.unique().tolist()),
        "game_ratings_state": sorted(gp.ratings_state_id.unique().tolist()),
        "game_ratings_prior": sorted(gp.ratings_prior_id.unique().tolist()),
        "game_ratings_source": gf.get("ratings_source"), "team_ratings_fresh": gf["ratings_fresh"]["TEAM_RATINGS_FRESH"],
        "ledger": ledger_chain(d + "/forecast_ledger.jsonl"),
    }

# ---- changed game / player PMFs: morning vs 18Z on the 14 shared games
pm0, gp0, _, _ = frames["morning_INITIAL_0700"]; pm1, gp1, fp1, _ = frames["refresh_ROTOWIRE_1300_18Z"]
m = gp1.merge(gp0, on="game_id", suffixes=("_18z", "_am"))
m["changed"] = m.pmf_sha256_18z != m.pmf_sha256_am
m["avail_delta_18z"] = m.availability_delta_home_18z.astype(float).abs() + m.availability_delta_away_18z.astype(float).abs()
changed_games = {"shared_games": int(len(m)), "pmf_changed": int(m.changed.sum()),
                 "changed_with_availability_delta": int((m.changed & (m.avail_delta_18z > 0)).sum()),
                 "unchanged_with_availability_delta": int((~m.changed & (m.avail_delta_18z > 0)).sum()),
                 "changed_without_availability_delta": int((m.changed & (m.avail_delta_18z == 0)).sum()),
                 "per_game": m[["game_id", "changed", "avail_delta_18z", "model_margin_am", "model_margin_18z"]]
                     .assign(model_margin_am=lambda x: x.model_margin_am.astype(float).round(3),
                             model_margin_18z=lambda x: x.model_margin_18z.astype(float).round(3)).values.tolist()}
pk = ["game_id", "player_id", "stat"]
pmm = pm1.merge(pm0, on=pk, suffixes=("_18z", "_am"), how="left", indicator=True)
changed_players = {"rows_18z": int(len(pm1)), "matched_in_morning": int((pmm._merge == "both").sum()),
                   "pmf_changed": int(((pmm._merge == "both") & (pmm.pmf_sha256_18z != pmm.pmf_sha256_am)).sum())}

# ---- historical label reconciliation over every persisted game PMF row (read-only; nothing rewritten)
KNOWN = {"ffb91c4375c0": "cbb-game-model-1.1.0", "abe0533360f9": "cbb-game-model-1.2.0"}
recon = collections.Counter()
PMAP = {}
for p in sorted(glob.glob(O + "/forecasts/2026-*/F*/game_pmfs.parquet")):
    t = pq.read_table(p, columns=["forecast_id", "game_id", "pmf_sha256", "model_version", "params_sha256"]).to_pandas()
    day = p.split("/")[-3]
    for r in t.itertuples():
        PMAP[(r.forecast_id, r.game_id, r.pmf_sha256)] = r.params_sha256
    for (lab, ps), n in t.groupby(["model_version", "params_sha256"]).size().items():
        recon[(day, lab, ps[:12], KNOWN.get(ps[:12], "UNKNOWN_PARAMS"))] += int(n)
ev = collections.Counter()
for p in sorted(glob.glob(O + "/evaluations/*/game_market_evaluations.jsonl")):
    with open(p) as f:
        for line in f:
            r = json.loads(line); b = r.get("body", r)
            lab = str(b.get("game_model_version") or b.get("model_version"))
            ps = (PMAP.get((b.get("forecast_id"), b.get("game_id"), b.get("pmf_sha256"))) or "")[:12]
            ev[(p.split("/")[-2], lab, ps or "PMF_NOT_FOUND", KNOWN.get(ps, "UNRESOLVED" if not ps else "UNKNOWN_PARAMS"))] += 1
eval_files = {p.split("/")[-2] + "/" + os.path.basename(p): fsha(p) for p in sorted(glob.glob(O + "/evaluations/*/*.jsonl"))}

# ---- reference sample (deterministic; 18Z refresh for games it covers, morning for the 6 early games)
G = pd.concat([gp1.assign(src="refresh_ROTOWIRE_1300_18Z"),
               gp0[~gp0.game_id.isin(gp1.game_id)].assign(src="morning_INITIAL_0700")])
G["absdelta"] = G.availability_delta_home.astype(float).abs() + G.availability_delta_away.astype(float).abs()
G["absmargin"] = G.model_margin.astype(float).abs()
inj = G[G.absdelta > 0].sort_values(["absdelta", "game_id"], ascending=[False, True]).head(3)
una = G[G.absdelta == 0].sort_values("game_id").head(3)
close = G.sort_values(["absmargin", "game_id"]).head(3)
ref_g = pd.concat([inj.assign(case="INJURY_AFFECTED"), una.assign(case="UNAFFECTED"), close.assign(case="CLOSE_SPREAD")])
ref_g = ref_g.groupby("game_id", as_index=False).agg({**{c: "first" for c in ref_g.columns if c not in ("game_id", "case")},
                                                       "case": lambda s: "+".join(sorted(set(s)))})
fp1 = fp1.copy(); fp1["cat"] = fp1.cold_start_class + "|" + fp1.role_diagnostic
players = pm1.merge(fp1[["game_id", "player_id", "cold_start_class", "role_diagnostic", "availability_status",
                         "player_suppression", "p_play", "min_mu", "cat"]], on=["game_id", "player_id"], how="left")
picks = []
for (stat, cat), grp in players.groupby(["stat", "cat"]):
    picks.append(grp.sort_values("player_id").head(1).assign(case="CATEGORY:" + cat))
for st, grp in players[players.availability_status != "NO_REPORT"].groupby("availability_status"):
    for stat in ("PTS", "REB"):
        g2 = grp[grp.stat == stat].sort_values("player_id").head(1)
        picks.append(g2.assign(case="AVAILABILITY:" + st))
sup = players[players.player_suppression.fillna("") != ""]
for s_, grp in sup.groupby("player_suppression"):
    picks.append(grp.sort_values(["player_id", "stat"]).head(1).assign(case="SUPPRESSION:" + s_))
for gid in ref_g.game_id:   # every stat of the top-minutes player per team in each reference game
    src = pm1 if gid in set(gp1.game_id) else pm0
    pool = players if gid in set(gp1.game_id) else pm0.merge(frames["morning_INITIAL_0700"][2][
        ["game_id", "player_id", "cold_start_class", "role_diagnostic", "availability_status", "player_suppression",
         "p_play", "min_mu"]], on=["game_id", "player_id"], how="left")
    pool = pool[pool.game_id == gid]
    for tid, tg in pool.groupby("team_id"):
        top = tg.sort_values(["min_mu", "player_id"], ascending=[False, True]).player_id.iloc[0]
        picks.append(tg[tg.player_id == top].assign(case="REF_GAME_TOP_MINUTES"))
ref_p = pd.concat(picks, ignore_index=True)
ref_p = ref_p.groupby(pk, as_index=False).agg({**{c: "first" for c in ref_p.columns if c not in pk + ["case"]},
                                               "case": lambda s: "+".join(sorted(set(s)))})
ref_p["probabilities"] = ref_p.probabilities.map(lambda p: [float(x) for x in p])
ref_p["pmf_sha256_recomputed"] = [PCX.content_sha(int(a), np.asarray(b)) for a, b in zip(ref_p.support_min, ref_p.probabilities)]
assert (ref_p.pmf_sha256_recomputed == ref_p.pmf_sha256).all()
ref_g_out = ref_g.drop(columns=["absdelta", "absmargin"])
ref_g_out["pmf_zlib"] = ref_g_out.pmf_zlib.map(lambda b: bytes(b).hex())
def jl(df, path):
    def cv(v):
        if isinstance(v, (np.integer,)): return int(v)
        if isinstance(v, (np.floating,)): return float(v)
        if isinstance(v, np.ndarray): return v.tolist()
        return v
    with open(path, "w") as f:
        for r in df.to_dict("records"):
            f.write(json.dumps({k: cv(v) for k, v in r.items()}, sort_keys=True, default=str) + "\n")
jl(ref_p.drop(columns=["cat"]), OUT + "/reference_players.jsonl")
jl(ref_g_out, OUT + "/reference_games.jsonl")

pins = {"seed": {"SIM_SEED": VJ.SIM_SEED, "structural_base": 20271, "draws_per_game": PC.SIMS,
                 "per_game_seed": "D.game_seed(J.SIM_SEED, game_id) (prod/simulate.py)"},
        "fit": {"model_version": res["refresh_ROTOWIRE_1300_18Z"]["model_version"],
                "artifact_sha256": res["refresh_ROTOWIRE_1300_18Z"]["artifact_sha256"]},
        "input_cutoff": {"training_cutoff_utc": sorted(gp1.training_cutoff_utc.unique().tolist()),
                         "state_version_am_18z": res["refresh_ROTOWIRE_1300_18Z"]["state_version"],
                         "state_version_23z": res["refresh_ROTOWIRE_1800_23Z"]["state_version"],
                         "prediction_as_of_18z": frames["refresh_ROTOWIRE_1300_18Z"][3]["prediction_as_of"]},
        "config": {"engine_id": res["refresh_ROTOWIRE_1300_18Z"]["engine_id"],
                   "game_params_sha256": res["refresh_ROTOWIRE_1300_18Z"]["game_params_sha256"],
                   "endgame_ot_sha256": sorted(gp1.endgame_ot_sha256.unique().tolist()),
                   "ratings_state_id": res["refresh_ROTOWIRE_1300_18Z"]["game_ratings_state"],
                   "ratings_prior_id": res["refresh_ROTOWIRE_1300_18Z"]["game_ratings_prior"],
                   "game_model_json_sha256_current": fsha(REL + "/cbb_platform/game/params/game_model.json"),
                   "env_file_sha256": fsha("/etc/sports-models/cbb-originator.env")}}

out = {"generated_by": "stage baseline (read-only)", "release_current": os.path.basename(REL), "forecasts": res,
       "changed_game_pmfs_morning_vs_18z": changed_games, "changed_player_pmfs_morning_vs_18z": changed_players,
       "label_reconciliation_game_pmfs": [list(k) + [v] for k, v in sorted(recon.items())],
       "label_reconciliation_frozen_game_evaluations": [list(k) + [v] for k, v in sorted(ev.items())],
       "frozen_evaluation_file_sha256": eval_files, "pins": pins,
       "reference_sample": {"games": ref_g_out[["game_id", "src", "case", "pmf_sha256", "model_margin", "model_total",
                                                "availability_delta_home", "availability_delta_away"]].values.tolist(),
                            "player_rows": int(len(ref_p)),
                            "player_cases": collections.Counter(c for cs in ref_p.case for c in cs.split("+")).most_common(),
                            "stats": sorted(ref_p.stat.unique().tolist()),
                            "cold_start_classes": sorted(ref_p.cold_start_class.dropna().unique().tolist()),
                            "roles": sorted(ref_p.role_diagnostic.dropna().unique().tolist())}}
json.dump(out, open(OUT + "/baseline_droplet.json", "w"), indent=1, default=str)
print(json.dumps({k: out[k] for k in ("changed_game_pmfs_morning_vs_18z", "changed_player_pmfs_morning_vs_18z")}, default=str)[:3000])
for k, v in res.items():
    print(k, {x: v[x] for x in ("snapshot_pmfs_sha256_matches_file", "game_forecast_file_sha256_matches_file",
                                 "player_pmf_sha_recompute_mismatch", "game_pmf_sha_recompute_mismatch", "game_labels")}, v["ledger"])
print("recon", out["label_reconciliation_game_pmfs"][-12:])
print("evrecon", out["label_reconciliation_frozen_game_evaluations"][-12:])
print("ref", json.dumps(out["reference_sample"], default=str)[:2500])
