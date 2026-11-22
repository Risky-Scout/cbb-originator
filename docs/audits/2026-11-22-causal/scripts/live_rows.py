"""READ-ONLY on production: rebuild the walk-forward's 2026-27 training rows (pregame frame + opening-marginal OOS
forecasts + labels) exactly as the 06:10 morning would, on an overlay store root; writes only <out>.
    python -B live_rows.py <overlay_root> <out_dir> <YYYY-MM-DD>"""
import json, os, sys
from datetime import date
from pathlib import Path
import pandas as pd
from cbb_platform import props_v0 as P
from cbb_platform.prod import config as PC
from cbb_platform.walkforward import config as WC, daily as DY, history_source as HS, store as SO
ov, out, day = Path(sys.argv[1]), Path(sys.argv[2]), date.fromisoformat(sys.argv[3])
real = Path(WC.STORE_ROOT)
for k in ("models", "active", "rollback", "refused", "predictions", "monitoring", "locks"):
    if not (ov / k).exists() and (real / k).exists():
        os.symlink(real / k, ov / k)
for k in ("manifests", "candidates", "training/features"):
    (ov / k).mkdir(parents=True, exist_ok=True)
for g in (real / "training" / "features").iterdir():
    if not (ov / "training" / "features" / g.name).exists():
        os.symlink(g, ov / "training" / "features" / g.name)
src = real / "manifests" / "outcome_available_at.json"
if src.exists() and not (ov / "manifests" / "outcome_available_at.json").exists():
    (ov / "manifests" / "outcome_available_at.json").write_text(src.read_text())
from cbb_platform import duckdb_limits as DLM
DLM.install()
cfg = P.load_config()
with HS.active():
    ts = DY.training_state(day, ov, PC.STATE_ROOT, HS.history_db(), cfg)
fr = ts["frames"]
out.mkdir(parents=True, exist_ok=True)
fr["frame"].to_parquet(out / "frame_2027.parquet", index=False)
fr["oos"].to_parquet(out / "oos_2027.parquet", index=False)
opening = SO.load_version(real, WC.version_id(WC.OPENING_DATE))
(out / "opening_v0_state.json").write_text(json.dumps(opening.v0_state, default=float))
print(json.dumps({"training_cutoff": str(ts["training_cutoff"]), "games": len(ts["trained_games"]),
                  "frame_rows": len(fr["frame"]), "oos_rows": len(fr["oos"]), "hashes": fr["hashes"]}, default=str))
