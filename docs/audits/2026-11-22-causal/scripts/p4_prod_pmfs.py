"""Phase 4 (distribution shape) on the PRODUCTION PMFs: every persisted 2026-27 forecast-snapshot PMF (the latest
snapshot at or before each game's cutoff, exactly the one the exports use) joined to the settled box score. READ-ONLY.

Writes one compact row per (game, player, stat) PMF: mean, variance, P(0), the randomized-PIT inputs (F(y-1), F(y)),
-log p(y), the pmf itself is NOT kept. Player-level output stays on the Droplet audit dir / gitignored data dir.

usage: python -B p4_prod_pmfs.py <ops_root> <out.parquet>
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import exports as EX, market_exports as MX, pmf_evidence as PE

STATS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "PRA", "PR", "PA", "RA", "STOCKS")
ops, out = Path(sys.argv[1]), Path(sys.argv[2])
rows = []
days = sorted(p.name for p in (ops / "forecasts").iterdir() if p.is_dir() and p.name[:4] == "2026")
for day in days:
    src = EX.forecast_sources(ops, day)
    best = {}
    for gid, lst in src.items():
        aso, cut, p = max(lst, key=lambda t: t[0])
        best.setdefault(p, []).append((gid, aso, cut))
    act = MX.actuals(ops, set(src))
    for p, games in best.items():
        cmp = PE.read_compact(p)
        gset = {g for g, _, _ in games}
        cmp = cmp[cmp["game_id"].isin(gset) & cmp["stat"].isin(STATS)]
        fp = pd.read_parquet(p.parent / "forecast_players.parquet")
        ctx = fp.set_index(["game_id", "player_id"]).to_dict(orient="index")
        for r in cmp.itertuples(index=False):
            a = act.get((r.game_id, r.player_id))
            if a is None or a.get("participation") == "GAME_NOT_PLAYED":
                continue
            y = a.get(f"act_{r.stat}")
            mins = a.get("minutes")
            played = a.get("participation") == "PLAYED" and bool(mins)
            pm = np.asarray(r.probabilities, dtype=float)
            k = np.arange(r.support_min, r.support_min + len(pm))
            mu = float((k * pm).sum()); var = float(((k - mu) ** 2 * pm).sum())
            c = ctx.get((r.game_id, r.player_id), {})
            rec = {"day": day, "game_id": r.game_id, "player_id": r.player_id, "stat": r.stat, "run_id": r.run_id,
                   "prediction_as_of": r.prediction_as_of, "mean": mu, "var": var, "p0": float(pm[k == 0].sum()),
                   "min_mu": c.get("min_mu"), "min_sigma": c.get("min_sigma"), "p_play": c.get("p_play"),
                   "cold_start_class": c.get("cold_start_class"), "role": c.get("role_diagnostic"),
                   "played": played, "minutes": mins, "y": None, "F_lo": None, "F_hi": None, "nll": None}
            if played and y is not None and not pd.isna(y):
                yi = int(round(float(y)))
                cdf = np.cumsum(pm)
                j = int(np.clip(yi - r.support_min, -1, len(pm) - 1))
                rec.update(y=yi, F_lo=float(cdf[j - 1]) if j >= 1 else 0.0, F_hi=float(cdf[j]) if j >= 0 else 0.0,
                           nll=float(-np.log(max(pm[j] if 0 <= j < len(pm) else 0.0, 1e-300))))
            rows.append(rec)
        del cmp
    print(day, len(rows), flush=True)
df = pd.DataFrame(rows)
df.to_parquet(out, index=False)
print("rows", len(df), "played", int(df.played.sum()), "graded", int(df.y.notna().sum()))
