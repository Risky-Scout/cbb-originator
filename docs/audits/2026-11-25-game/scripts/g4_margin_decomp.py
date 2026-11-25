"""P3 (Defect 5): game margin decomposition of the production game model (g1_base) by |market close spread| band
(<3, 3-7, 7-12, 12+), seasons 2024 (dev) / 2025 (selection) / 2027 (live). Components: level bias, home-court (non-neutral
vs neutral residual), slope of y on model margin (strength over/under-dispersion), market encompassing weight, and
team-strength updating (split-half persistence of team residuals within season, home and away sides). Single process."""
import json
import numpy as np, pandas as pd
from cbb_platform import config as C
s = pd.read_parquet(C.PROJECT_ROOT / "data/qa/large/game_20261125/g1_base.parquet").dropna(subset=["close_spread"])
s["mk"] = -s.close_spread
s["band"] = pd.cut(s.mk.abs(), [-0.1, 3, 7, 12, 99], labels=["<3", "3-7", "7-12", "12+"]).astype(str)
s["r"], s["rk"] = s.y_m - s.pm, s.y_m - s.mk
def ols(y, X):
    X = np.c_[np.ones(len(X)), X]; return np.linalg.lstsq(X, y, rcond=None)[0]
R = {}
for season, h in s.groupby("season"):
    out = {}
    for b, g in h.groupby("band"):
        bm = ols(g.y_m.to_numpy(float), g.pm.to_numpy(float)); be = ols(g.y_m.to_numpy(float), np.c_[g.pm, g.mk])
        out[b] = {"n": len(g), "rmse_model": float(np.sqrt((g.r ** 2).mean())), "rmse_mkt": float(np.sqrt((g.rk ** 2).mean())),
                  "bias_model": float(g.r.mean()), "slope_y_on_model": float(bm[1]), "b_model": float(be[1]), "b_mkt": float(be[2]),
                  "D_sd": float((g.pm - g.mk).std())}
    nn = h[~h.neutral.astype(bool)]; ne = h[h.neutral.astype(bool)]
    out["home_court"] = {"resid_nonneutral": float(nn.r.mean()), "resid_neutral": float(ne.r.mean()) if len(ne) else None,
                         "mkt_resid_nonneutral": float(nn.rk.mean()), "model_minus_mkt_nonneutral": float((nn.pm - nn.mk).mean())}
    # team residual persistence: each game's residual credited +r to home, -r to away; split by within-season game order
    t = pd.concat([pd.DataFrame({"team": h.home, "d": h.game_date_et, "r": h.r, "rk": h.rk}),
                   pd.DataFrame({"team": h.away, "d": h.game_date_et, "r": -h.r, "rk": -h.rk})]).sort_values("d")
    t["k"] = t.groupby("team").cumcount(); t["n"] = t.groupby("team").team.transform("size")
    a = t[t.n >= 8].assign(half=lambda x: (x.k >= x.n / 2).astype(int)).groupby(["team", "half"])[["r", "rk"]].mean().unstack()
    out["team_persistence"] = {"teams": int(len(a)), "model_split_half_corr": float(np.corrcoef(a[("r", 0)], a[("r", 1)])[0, 1]),
                               "market_split_half_corr": float(np.corrcoef(a[("rk", 0)], a[("rk", 1)])[0, 1])}
    # updating: residual of a team's early games predicts its later residual? (lagged team mean residual)
    R[int(season)] = out
json.dump(R, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-game/results/g4_margin_decomp.json", "w"), indent=1)
for season, o in R.items():
    print("==", season, "home", {k: round(v, 2) if v is not None else None for k, v in o["home_court"].items()}, "persist", {k: round(v, 3) for k, v in o["team_persistence"].items()})
    for b in ("<3", "3-7", "7-12", "12+"):
        if b in o: x = o[b]; print(f"   {b:5s} n {x['n']:5d} rmse m {x['rmse_model']:.2f} k {x['rmse_mkt']:.2f} bias {x['bias_model']:+.2f} slope {x['slope_y_on_model']:.3f} enc b_model {x['b_model']:+.2f} b_mkt {x['b_mkt']:.2f} Dsd {x['D_sd']:.2f}")
