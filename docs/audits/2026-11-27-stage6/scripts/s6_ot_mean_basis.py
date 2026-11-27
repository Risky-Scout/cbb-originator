"""Stage 06 validation of the game mean-basis correction (E1): FINAL (pre-Stage-06) vs REGULATION mean basis.

Replays the sealed production game PMF construction (game.model.targets / build, sealed variance + endgame / OT) on the
existing point-in-time walk-forward means of the production configuration (data/qa/large/game_20261125/g1_TP1P_walk
.parquet: 2023-24, 2024-25, 2026-27; no refit, no parameter change) and scores both bases on the realized FINAL score:
mean total / team bias, joint log score of the observed (home, away) cell, CRPS of the total, P(home win) Brier.

    s6_ot_mean_basis.py <walk parquet> <out json> [stride]
"""
import json
import os
import sys
import time

for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[v] = "1"

import numpy as np
import pandas as pd

from cbb_platform.game import joint as J, model as GMod, params as GP


def score(g, h, a):
    H, A = g._grid()
    P = g.p
    cell = P[(H == h) & (A == a)].sum()
    T = (H + A).ravel()
    pt = np.bincount(T - T.min(), weights=P.ravel())
    x = np.arange(T.min(), T.min() + len(pt))
    cdf = np.cumsum(pt)
    crps = float((((cdf - (x >= h + a)) ** 2)).sum())
    mo = g.moments()
    return {"e_total": mo["exp_total"], "e_home": mo["exp_home"], "e_away": mo["exp_away"],
            "logp": float(np.log(max(cell, 1e-300))), "crps_total": crps, "p_home": float(P[H > A].sum())}


def main(walk, out, stride=1):
    w = pd.read_parquet(walk).iloc[::int(stride)].reset_index(drop=True)
    eot = GP.endgame_ot()
    vp = GMod.VarianceParams(**GP.game_model()["variance"])
    rows = []
    t0 = time.time()
    for i, r in enumerate(w.itertuples(index=False)):
        t = GMod.targets(float(r.exp_home), float(r.exp_away), int(r.ngp), vp)
        rec = {"game_id": r.game_id, "season": int(r.season), "home_pts": r.home_pts, "away_pts": r.away_pts,
               "ot": 0 if pd.isna(r.ot) else int(r.ot)}
        for basis in ("FINAL", "REGULATION"):
            os.environ[J.MEAN_BASIS_ENV] = basis
            s = score(GMod.build(t, eot), int(r.home_pts), int(r.away_pts))
            rec.update({f"{k}_{basis}": v for k, v in s.items()})
        rows.append(rec)
        if i % 500 == 0:
            print(i, len(w), round(time.time() - t0, 1), flush=True)
    d = pd.DataFrame(rows)
    d.to_parquet(out.replace(".json", ".parquet"), index=False)
    res = {}
    for s, h in list(d.groupby("season")) + [("ALL", d)]:
        tot = h.home_pts + h.away_pts
        y = (h.home_pts > h.away_pts).astype(float)
        m = {"n": len(h)}
        for b in ("FINAL", "REGULATION"):
            m[b] = {"total_bias_actual_minus_model": float((tot - h[f"e_total_{b}"]).mean()),
                    "home_bias": float((h.home_pts - h[f"e_home_{b}"]).mean()),
                    "away_bias": float((h.away_pts - h[f"e_away_{b}"]).mean()),
                    "mean_log_score": float(h[f"logp_{b}"].mean()),
                    "mean_crps_total": float(h[f"crps_total_{b}"].mean()),
                    "brier_home_win": float(((h[f"p_home_{b}"] - y) ** 2).mean())}
        dl = h["logp_REGULATION"] - h["logp_FINAL"]
        dc = h["crps_total_REGULATION"] - h["crps_total_FINAL"]
        m["paired_log_score_gain"] = {"mean": float(dl.mean()), "se": float(dl.std() / np.sqrt(len(h)))}
        m["paired_crps_change"] = {"mean": float(dc.mean()), "se": float(dc.std() / np.sqrt(len(h)))}
        res[str(s)] = m
    json.dump(res, open(out, "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
