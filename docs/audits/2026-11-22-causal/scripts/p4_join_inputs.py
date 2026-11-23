"""Join each production PMF row (p4_prod_pmfs) to its run's model inputs (the marginal means / dispersion fed to JM1).
usage: python -B p4_join_inputs.py <runs_root> <p4_prod_pmfs.parquet> <out.parquet>"""
import sys
from pathlib import Path
import pandas as pd
runs, src, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
d = pd.read_parquet(src)
keep = ["game_id", "player_id", "p_play", "min_mu", "min_sigma"] + [f"{s}_{k}" for s in ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV") for k in ("mu", "phi")]
parts = []
for rid in sorted(d.run_id.unique()):
    p = runs / rid / "model_inputs.parquet"
    if p.exists():
        mi = pd.read_parquet(p)
        parts.append(mi[[c for c in keep if c in mi.columns]].assign(run_id=rid).rename(columns={"p_play": "mi_p_play", "min_mu": "mi_min_mu", "min_sigma": "mi_min_sigma"}))
mi = pd.concat(parts, ignore_index=True).drop_duplicates(["run_id", "game_id", "player_id"])
d = d.merge(mi, on=["run_id", "game_id", "player_id"], how="left")
d.to_parquet(out, index=False)
print("rows", len(d), "joined", int(d.PTS_mu.notna().sum()), "runs", d.run_id.nunique(), len(parts))
