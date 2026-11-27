"""Stage 2 equivalence + scaling on production data (read-only). Candidate code from OUT/code (not the release).
argv: OUT N_SAMPLE N_INCUMBENT"""
import json, os, resource, sys, time
from pathlib import Path
OUT = Path(sys.argv[1]); N, NI = int(sys.argv[2]), int(sys.argv[3])
sys.path.insert(0, str(OUT / "code"))
import pandas as pd
from cbb_platform import duckdb_limits as DLM, props_v0 as PV0
DLM.install()
from cbb_platform.prod import features as PF
from cbb_platform.walkforward import training_rows as TR, config as WC
assert Path(PF.__file__).is_relative_to(OUT / "code"), PF.__file__

S = Path("/var/lib/sports-models/cbb-originator")
CACHE = S / "walkforward/cbb-originator/training/features"
HIST = S / "history/cbb_production_history_minimal.duckdb"
CFG = PV0.load_config()
RES = OUT / "equivalence_results.jsonl"
done = {json.loads(l)["key"] for l in RES.open()} if RES.exists() else set()

metas = [json.loads((d / "meta.json").read_text()) for d in CACHE.iterdir() if (d / "meta.json").exists()]
groups = {}
for m in metas:
    if m.get("status") != "BUILT":
        continue
    groups.setdefault((m["state_version"], m["build"]["as_of"]), []).append(m)
ok = [(k, v) for k, v in groups.items() if all(x["build"]["group_games"] == len(v) for x in v)]
ok.sort(key=lambda kv: kv[0][1])
idx = sorted({round(i * (len(ok) - 1) / (N - 1)) for i in range(N)})
sample = [ok[i] for i in idx]
big = max(ok, key=lambda kv: len(kv[1]))
if big not in sample:
    sample.append(big)
inc_idx = {sample[round(i * (len(sample) - 1) / (NI - 1))][0] for i in range(NI)} if NI > 1 else set()
print("groups", len(groups), "consistent", len(ok), "sample", len(sample), "incumbent re-timed", len(inc_idx), flush=True)


def run(sv, asof, ms, closure):
    gids = sorted(m["game_id"] for m in ms)
    work = OUT / f"work-{os.getpid()}"
    t = time.perf_counter()
    fr = PF.build(work, gids, WC.PRODUCTION_SEASON, asof, CFG, HIST, S / f"prod/state/versions/{sv}/increment.duckdb",
                  closure=closure)
    wall = time.perf_counter() - t
    pit = TR._pit_audit(fr["features_db"], gids)
    cand, team = fr["cand"], fr["team"]
    eq = {}
    for m in ms:
        g = m["game_id"]
        c = cand[cand["canonical_game_id"] == g].sort_values("player_game_example_id", kind="mergesort")
        tt = team[team["game_id"] == g].sort_values("team_id", kind="mergesort")
        eq[g] = [TR._sha_df(c) == m["cand_sha256"], TR._sha_df(tt) == m["team_sha256"], len(c), len(tt)]
    prov = fr["provenance"]
    for p in work.iterdir():
        p.unlink()
    work.rmdir()
    return {"wall_s": round(wall, 2), "pit_violations": pit["violations"], "pit": pit,
            "target_outcome_rows": prov["canonical"]["target_outcome_rows"], "post_as_of_rows": prov["canonical"]["post_as_of_rows"],
            "canonical_counts": prov["canonical"]["counts"], "closure": prov["canonical"].get("closure"),
            "games": len(ms), "all_equal": all(v[0] and v[1] for v in eq.values()), "per_game": eq}


for (sv, asof), ms in sample:
    for closure in ([True, False] if (sv, asof) in inc_idx else [True]):
        key = f"{sv}|{asof}|{closure}"
        if key in done:
            continue
        ma = open("/proc/meminfo").read()
        r = run(sv, asof, ms, closure)
        r.update({"key": key, "state_version": sv, "as_of": asof, "closure_on": closure,
                  "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)})
        with RES.open("a") as f:
            f.write(json.dumps(r, default=str) + "\n")
        print(key, "wall", r["wall_s"], "equal", r["all_equal"], "pit", r["pit_violations"],
              "rows", r["canonical_counts"].get("flagship.player_game_outcomes"), flush=True)
