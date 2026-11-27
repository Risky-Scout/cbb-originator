"""One feature build per process (fresh RSS / CPU / IO counters). argv: OUT STATE_VERSION AS_OF CLOSURE(1|0)"""
import json, os, resource, sys, time
from pathlib import Path
OUT, SV, ASOF, CL = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] == "1"
sys.path.insert(0, str(OUT / "code"))
import pandas as pd
from cbb_platform import duckdb_limits as DLM, props_v0 as PV0
DLM.install()
from cbb_platform.prod import features as PF
from cbb_platform.walkforward import training_rows as TR, config as WC
assert Path(PF.__file__).is_relative_to(OUT / "code") and PF.closure_audited()
S = Path("/var/lib/sports-models/cbb-originator"); CACHE = S / "walkforward/cbb-originator/training/features"
HIST = S / "history/cbb_production_history_minimal.duckdb"
io = lambda: dict(l.split(": ") for l in open("/proc/self/io").read().split("\n") if l)
ms = [m for m in (json.loads((d / "meta.json").read_text()) for d in CACHE.iterdir() if (d / "meta.json").exists())
      if m.get("status") == "BUILT" and m["state_version"] == SV and m["build"]["as_of"] == ASOF]
gids = sorted(m["game_id"] for m in ms)
cfg = PV0.load_config()
i0, r0, t0 = io(), resource.getrusage(resource.RUSAGE_SELF), time.perf_counter()
work = OUT / f"bench-{os.getpid()}"
fr = PF.build(work, gids, WC.PRODUCTION_SEASON, ASOF, cfg, HIST, S / f"prod/state/versions/{SV}/increment.duckdb", closure=CL)
wall = time.perf_counter() - t0
r1, i1 = resource.getrusage(resource.RUSAGE_SELF), io()
scratch = sum(p.stat().st_size for p in work.iterdir())
eq = all(TR._sha_df(fr["cand"][fr["cand"].canonical_game_id == m["game_id"]].sort_values("player_game_example_id", kind="mergesort")) == m["cand_sha256"]
         and TR._sha_df(fr["team"][fr["team"].game_id == m["game_id"]].sort_values("team_id", kind="mergesort")) == m["team_sha256"] for m in ms)
for p in work.iterdir(): p.unlink()
work.rmdir()
c = fr["provenance"]["canonical"]
rec = {"state_version": SV, "as_of": ASOF, "closure": CL, "games": len(gids), "cand_rows": len(fr["cand"]), "all_equal": eq,
       "wall_s": round(wall, 2), "cpu_user_s": round(r1.ru_utime - r0.ru_utime, 2), "cpu_sys_s": round(r1.ru_stime - r0.ru_stime, 2),
       "peak_rss_mb": round(r1.ru_maxrss / 1024, 1), "read_mb": round((int(i1["read_bytes"]) - int(i0["read_bytes"])) / 2**20, 1),
       "write_mb": round((int(i1["write_bytes"]) - int(i0["write_bytes"])) / 2**20, 1),
       "rchar_mb": round((int(i1["rchar"]) - int(i0["rchar"])) / 2**20, 1), "scratch_mb": round(scratch / 2**20, 1),
       "player_box_rows": c["counts"]["flagship.player_game_outcomes"], "substitution_rows": c["counts"]["flagship.substitutions"],
       "closure_info": c.get("closure")}
with (OUT / "bench_results.jsonl").open("a") as f: f.write(json.dumps(rec, default=str) + "\n")
print(json.dumps({k: v for k, v in rec.items() if k != "closure_info"}))
