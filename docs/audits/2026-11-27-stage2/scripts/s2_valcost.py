"""Cache-validation overhead (fresh process, read-only): each component of TR.cached_valid_games, timed."""
import json, resource, sys, time
from pathlib import Path
OUT = Path(sys.argv[1]); sys.path.insert(0, str(OUT / "code"))
t = {}; c = time.perf_counter
s = c(); from cbb_platform import props_v0 as PV0; from cbb_platform.walkforward import training_rows as TR; t["import_s"] = c() - s
S = Path("/var/lib/sports-models/cbb-originator"); CACHE = S / "walkforward/cbb-originator/training/features"
SR, HIST = S / "prod/state", S / "history/cbb_production_history_minimal.duckdb"
cfg = PV0.load_config()
s = c(); hsha = TR.file_sha256(HIST); t["history_sha256_cold_s"] = c() - s
s = c(); TR.file_sha256(HIST); t["history_sha256_memo_s"] = c() - s
s = c(); sem = TR.feature_semantics_sha(cfg); t["semantics_sha_s"] = c() - s
s = c(); sidx = TR.StateIndex(SR); t["state_index_s"] = c() - s
s = c(); metas = {d.name: json.loads((d / "meta.json").read_text()) for d in CACHE.iterdir() if (d / "meta.json").exists()}; t["read_metas_s"] = c() - s
gc = TR.game_cutoffs(sidx.v[-1]["increment_db"], list(metas)).set_index("canonical_game_id")
s = c(); v = [TR.cache_validity(m, gc.loc[g, "prediction_cutoff_utc"], sidx, sem, hsha) for g, m in metas.items() if g in gc.index]; t["validity_loop_s"] = c() - s
import pandas as pd
games = gc.reset_index()[["canonical_game_id", "prediction_cutoff_utc"]]
s = c(); have = TR.cached_valid_games(S / "walkforward/cbb-originator/training", SR, games, HIST, cfg); t["cached_valid_games_end_to_end_s"] = c() - s
out = {k: round(x, 3) for k, x in t.items()} | {"entries": len(metas), "validated": len(v), "reusable": len(have),
       "history_db_mb": round(HIST.stat().st_size / 2**20, 1), "state_versions": len(sidx.v),
       "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
(OUT / "validation_overhead.json").write_text(json.dumps(out, indent=1)); print(json.dumps(out))
