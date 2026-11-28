"""Stage 08: freeze the benchmark manifest (identities, hashes, populations, definitions).   s8_manifest.py MIRROR WORK OUT"""
import glob, hashlib, json, os, sys, subprocess
import pandas as pd
MIRROR, WORK, OUT = sys.argv[1:4]
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
# compaction log: current (compact) sha -> recorded original sha
new = {}
for fn in ("pmf_compact_migration", "storage_retention"):
    for l in open(f"{MIRROR}/retention/{fn}.jsonl"):
        x = json.loads(l)
        if x.get("new_sha256"):
            new[x["new_sha256"]] = x.get("old_sha256") or x.get("sha256")
src = []
for f in sorted(glob.glob(f"{MIRROR}/forecasts/*/F*/snapshot.json")):
    s = json.load(open(f)); p = f.replace("snapshot.json", "pmfs.parquet"); h = sha(p)
    st = "MATCHES_RECORDED" if s["pmfs_sha256"] == h else ("LOGGED_LOSSLESS_COMPACTION_2026-11-16" if new.get(h) == s["pmfs_sha256"] else "UNEXPLAINED")
    gp = p.replace("pmfs.parquet", "game_pmfs.parquet")
    src.append({"artifact": os.path.relpath(os.path.dirname(f), MIRROR), "kind": "SNAPSHOT", "window": s.get("window"),
                "model_version": s.get("model_version"), "engine_id": s.get("engine_id"), "code_commit": s.get("code_commit"),
                "state_version": s.get("state_version"), "pmfs_sha256": h, "recorded_sha256": s["pmfs_sha256"], "hash_status": st,
                "game_pmfs_sha256": sha(gp) if os.path.exists(gp) else None})
for f in sorted(glob.glob(f"{MIRROR}/ticks/*/pmfs.parquet")):
    t = json.load(open(f.replace("pmfs.parquet", "tick.json"))); h = sha(f)
    rec = ((t.get("scoring") or {}).get("evidence") or {}).get("pmfs_sha256")
    src.append({"artifact": os.path.relpath(os.path.dirname(f), MIRROR), "kind": "TICK", "window": "OFFICIAL_TICK",
                "code_commit": t.get("source_commit") or t.get("frozen_commit"), "pmfs_sha256": h, "recorded_sha256": rec,
                "hash_status": "MATCHES_RECORDED" if rec == h else "UNEXPLAINED"})
S = pd.DataFrame(src); S.to_csv(f"{OUT}/benchmark_sources.csv", index=False)
res = {os.path.basename(p): sha(p) for p in sorted(glob.glob(f"{OUT}/*")) if not p.endswith(("BENCHMARK_MANIFEST.json", "benchmark_sources.csv"))}
scr = {os.path.basename(p): sha(p) for p in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "*.py")))}
inter = {os.path.basename(p): sha(p) for p in sorted(glob.glob(f"{WORK}/*.parquet"))}
m = {"benchmark_id": "CBB-S08-BENCHMARK-20261128", "frozen_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
     "evaluation_cutoff_utc": "2026-11-27T20:40:23Z", "evaluation_state": "S20261127T201352Z-8eeab0c20aef",
     "evaluation_state_increment_sha256": sha(f"{MIRROR}/state/S20261127T201352Z-8eeab0c20aef/increment.duckdb"),
     "code_lineage": {"stage7_successor": "7d160b1c7c5a02db834793954924c4ff240a0690", "ci_run": 46078107204,
                      "stage7_checkpoint": "45761b7d577cb4c076acf571c4abdf0e74c7c64c", "production_at_freeze": "9f65e7f9196c21a15811560d14c9974cb0bb2d7f"},
     "incumbent_identities": {"player_engine_final": "JM1_FINAL+RA2V1[AST,FG3M,STL,REB]+PTSC1[0]+TOVT1+DEV1+CTX1+PI2+PS2#972018d0ebf9",
                              "game_model": "cbb-game-model-1.0.0..1.2.0 SHADOW, all historical PMFs joint 1.0.0 (pre-C4)",
                              "frozen_opening_model": "cbb-v2-jm1-final-shadow-1.0.0"},
     "source_artifacts": {"count": int(len(S)), "hash_status": S.hash_status.value_counts().to_dict(),
                          "model_versions": S.model_version.dropna().value_counts().to_dict()},
     "populations": {"A_HISTORICAL_PRODUCTION": "every pregame PMF in a COMPLETE game (989,604 rows, 22,997 player-games, 811 games); game PMFs 331 games",
                     "B_CORRECTED_CANDIDATE_REPLAY": "NOT EXECUTED (BLOCKED): outstanding population = 1,127 complete DI games with an exact retained state; see README §7",
                     "C_MARKET_OVERLAP": "player: 2,299 primary decision contracts (97 games); game: 1,415 consensus main-line contracts (285 games) + 112,241 book-line contracts",
                     "D_DIAGNOSTIC_RECONSTRUCTION": "selection-exposed dates (all 2026-27 games <= 2026-11-26), lockbox seasons, pre-11-02 games, time-attributed engine ids"},
     "definitions": {"primary_player_vintage": "LAST_PREGAME = latest PMF with prediction_as_of < tip per (game, player, stat)",
                     "other_vintages": ["INITIAL (INITIAL_0700 / REFIT_RETRY_0800, earliest)", "REFRESH_LAST (ROTOWIRE_*, latest)", "CUTOFF_TICK (OFFICIAL_TICK, latest)"],
                     "played": "box line with minutes > 0 in the evaluation state", "bias": "mean(forecast - actual)",
                     "crps": "discrete CRPS sum_k (F(k) - 1{y<=k})^2", "log_score": "-ln p(y), floor 1e-12", "pit": "randomized PIT, blake2b-seeded per artifact",
                     "intervals": "central quantile intervals of the PMF (q.10-q.90, q.05-q.95, q.01-q.99)",
                     "market_probability": "same-book two-way no-vig, non-push conditional; player props void on DNP; game markets OT-inclusive",
                     "horizons": "OPEN = first retained observation; DECISION = the evaluation's observation / last capture <= cutoff; CLOSE = last observation before tip; model held at the decision forecast",
                     "inference": "cluster bootstrap over games, B=1000, seed 20261128; date-block bootstrap for game-distribution bias; Holm over the DECISION family",
                     "noninferiority": "no margin pre-registered before data exposure: no noninferiority claim is made in Stage 08",
                     "min_n": 30},
     "results_sha256": res, "scripts_sha256": scr, "intermediate_sha256": inter}
json.dump(m, open(f"{OUT}/BENCHMARK_MANIFEST.json", "w"), indent=1, default=str)
print(json.dumps(m["source_artifacts"], indent=1))
