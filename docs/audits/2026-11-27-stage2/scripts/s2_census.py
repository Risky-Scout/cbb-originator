import json, sys, collections, time
from pathlib import Path
OUT = Path(sys.argv[1]); sys.path.insert(0, str(OUT / "code"))
import pandas as pd
from cbb_platform import props_v0 as PV0
from cbb_platform.walkforward import training_rows as TR, eligibility as EL
S = Path("/var/lib/sports-models/cbb-originator"); CACHE = S / "walkforward/cbb-originator/training/features"
SR, HIST = S / "prod/state", S / "history/cbb_production_history_minimal.duckdb"
t = time.perf_counter()
cfg = PV0.load_config(); sem, hsha = TR.feature_semantics_sha(cfg), TR.file_sha256(HIST); sidx = TR.StateIndex(SR)
latest = sidx.v[-1]
metas = {d.name: json.loads((d / "meta.json").read_text()) for d in CACHE.iterdir() if (d / "meta.json").exists()}
nometa = [d.name for d in CACHE.iterdir() if d.is_dir() and not d.name.startswith(".") and not (d / "meta.json").exists()]
gc = TR.game_cutoffs(latest["increment_db"], list(metas)).set_index("canonical_game_id")
c, ex = collections.Counter(), []
for g, m in metas.items():
    if g not in gc.index:
        c["NOT_IN_CURRENT_SCHEDULE"] += 1; continue
    v = TR.cache_validity(m, gc.loc[g, "prediction_cutoff_utc"], sidx, sem, hsha); c[v] += 1
    if v not in TR.REUSABLE and len(ex) < 20:
        ex.append({"game": g, "v": v, "cached_cutoff": m["prediction_cutoff_utc"], "now_cutoff": str(gc.loc[g, "prediction_cutoff_utc"]), "sv": m.get("state_version")})
out = {"entries": len(metas), "dirs_without_meta": nometa, "status": dict(c), "examples": ex, "latest_state": latest["state_version"],
       "semantics_sha256": sem, "history_sha256": hsha, "seconds": round(time.perf_counter() - t, 1)}
(OUT / "cache_census.json").write_text(json.dumps(out, indent=1, default=str)); print(json.dumps(out, default=str)[:3000])
