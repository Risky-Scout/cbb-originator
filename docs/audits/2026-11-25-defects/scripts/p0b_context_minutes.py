"""P0b: (1) is the opponent context residual PERSISTENT (predictable) or within-game noise? Split-half reliability:
per-opponent per-minute log residual in the first vs second half of the season (by date), correlation across opponents,
for the defending opponent and, as a control, for the player's own team. (2) minutes calibration slope:
OLS y_min ~ min_mu by season phase (slope < 1 => minutes forecasts too extreme)."""
import json
import numpy as np, pandas as pd
import importlib.util, sys
spec = importlib.util.spec_from_file_location("p0", "docs/audits/2026-11-25-defects/scripts/p0_location_decomp.py")
from cbb_platform import config as C
Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
src = open("docs/audits/2026-11-25-defects/scripts/p0_location_decomp.py").read().split("\nR = {}")[0]
ns = {}; exec(src, ns)
R = {}
for ds in ("F2026", "LIVE"):
    r = ns["load"](ds); r = r[(r.y_min > 0) & r.opp.notna()].copy()
    r["cf"] = r["mean"] * r.y_min / r.min_mu
    gd = r.groupby("game_id").ngroup()
    dates = pd.Series(r.game_id.unique())
    order = {g: i for i, g in enumerate(sorted(r.game_id.unique()))}
    # date proxy: game order inside the replay (games sorted chronologically by id? use pgx cutoff not present) -> use early/late via cumulative count per opp
    r["k"] = r.groupby(["opp"]).cumcount()
    res = {}
    for s in ns["BASE7"]:
        h = r[r.stat == s]
        out = {}
        for key in ("opp", "team"):
            hh = h.sort_values("game_id").copy()
            n_games = hh.groupby(key).game_id.transform(lambda x: x.rank(method="dense"))
            tot = hh.groupby(key).game_id.transform("nunique")
            hh["half"] = np.where(n_games <= tot / 2, 0, 1)
            a = hh.groupby([key, "half"]).agg(y=("y", "sum"), cf=("cf", "sum")).unstack()
            a = a.dropna()
            l0, l1 = np.log(a[("y", 0)] / a[("cf", 0)]), np.log(a[("y", 1)] / a[("cf", 1)])
            ok = np.isfinite(l0) & np.isfinite(l1)
            out[key] = {"n_groups": int(ok.sum()), "split_half_corr": round(float(np.corrcoef(l0[ok], l1[ok])[0, 1]), 3)}
        res[s] = out
    m = r.drop_duplicates("pgx")
    sl = {}
    for e, g in m.groupby("early"):
        X = np.c_[np.ones(len(g)), g.min_mu]; b = np.linalg.lstsq(X, g.y_min.to_numpy(float), rcond=None)[0]
        sl[str(e)] = {"n": int(len(g)), "intercept": round(float(b[0]), 2), "slope": round(float(b[1]), 3)}
    res["minutes_slope"] = sl
    R[ds] = res
    print(ds, json.dumps(res))
json.dump(R, open("docs/audits/2026-11-25-defects/results/p0b_context_minutes.json", "w"), indent=1)
