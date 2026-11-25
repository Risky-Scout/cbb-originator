"""DEFECT_PROTOCOL.json: TRAIN fits (F2025) and point-in-time per-row mean multipliers for DV1, OC1, OT1 on F2026 and
LIVE. Output data/qa/large/defects_20261125/mult_{DS}_{ARM}.parquet (pgx, <stat>_m) + results/p1_train_fit.json.
Single process."""
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C

Q = C.PROJECT_ROOT / "data/qa/large/causal_20261122"
O = C.PROJECT_ROOT / "data/qa/large/defects_20261125"
O.mkdir(parents=True, exist_ok=True)
BASE7 = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]
KGRID = [5, 10, 20, 40, 80, 160, 320, 640, np.inf]


def frame(ds):
    cols = ["player_game_example_id", "canonical_team_id", "prediction_cutoff_utc", "cur_min", "h1_min", "h2_min", "h3_min", "pps_same_team", "y_min"]
    if ds == "LIVE":
        f = pd.read_parquet(Q / "live/frame_2027.parquet", columns=cols[:-1])
        f = f.merge(pd.read_parquet(Q / "live/oos_2027.parquet", columns=["player_game_example_id", "y_min"]), on="player_game_example_id")
    else:
        f = pd.read_parquet(Q / f"d1_{ds}.parquet", columns=cols)
    f = f.rename(columns={"player_game_example_id": "pgx", "canonical_team_id": "team"})
    h = (0.5 * f.h1_min.fillna(0) + 0.25 * f.h2_min.fillna(0) + 0.125 * f.h3_min.fillna(0))
    f["s_prev"] = h / (f.cur_min.fillna(0) + h + 31.0)
    f["same"] = (f.pps_same_team.fillna(0) == 1).astype(int)
    ts = pd.to_datetime(f.prediction_cutoff_utc, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    f["date"] = ts.dt.normalize()
    yr = ts.dt.year.where(ts.dt.month >= 7, ts.dt.year - 1)
    f["season_day"] = (ts - pd.to_datetime(yr.astype(str) + "-11-01")).dt.days
    return f


def cells(ds, arm, file):
    r = pd.read_parquet(Q / f"p4/{file}", columns=["arm", "game_id", "pgx", "stat", "y", "mean", "min_mu"])
    r = r[(r.arm == arm) & r.stat.isin(BASE7)].drop(columns="arm")
    r = r.merge(frame(ds), on="pgx")
    tg = r[["game_id", "team"]].drop_duplicates()
    opp = tg.merge(tg, on="game_id").query("team_x != team_y").rename(columns={"team_x": "team", "team_y": "opp"})
    r = r.merge(opp, on=["game_id", "team"], how="left")
    r["cf"] = np.where(r.y_min > 0, r["mean"] * r.y_min / r.min_mu, np.nan)
    return r


def pit_sums(r, key):
    """for each row: sums (Y, CF, n_games) over rows sharing `key` on strictly earlier dates."""
    p = r[r.y_min > 0].groupby([key, "stat", "date"]).agg(Y=("y", "sum"), CF=("cf", "sum"), G=("game_id", "nunique")).reset_index()
    p = p.sort_values([key, "stat", "date"])
    for c in ("Y", "CF", "G"):
        p[c] = p.groupby([key, "stat"])[c].cumsum() - p[c]            # strictly earlier dates
    return r.merge(p, on=[key, "stat", "date"], how="left").fillna({"Y": 0, "CF": 0, "G": 0})


def pois_ll(y, m):
    m = np.maximum(m, 1e-9)
    return float((y * np.log(m) - m).sum())


if __name__ == "__main__":
    T = cells("F2025", "PRODX3", "p5_F2025_train3.parquet")
    Tp = T[T.y_min > 0]
    fit = {"DV1": {}, "OC1": {}, "OT1": {}}
    for s in BASE7:
        for same in (0, 1):
            h = Tp[(Tp.stat == s) & (Tp.same == same)]
            y, cf, x = h.y.to_numpy(float), h.cf.to_numpy(float), h.s_prev.to_numpy(float)
            d = 0.0
            for _ in range(50):
                m = cf * (1 + d * x)
                g = (y * x / (1 + d * x) - cf * x).sum()
                H = -(y * x * x / (1 + d * x) ** 2).sum()
                st = g / H
                d -= st
                if abs(st) < 1e-10:
                    break
            fit["DV1"][f"{s}|{same}"] = {"delta": float(d), "se": float(np.sqrt(-1 / H)), "n": int(len(h))}
    for arm, key in (("OC1", "opp"), ("OT1", "team")):
        S = pit_sums(T, key)
        Sp = S[S.y_min > 0]
        for s in BASE7:
            h = Sp[Sp.stat == s]
            ll = {str(k): pois_ll(h.y.to_numpy(float), h.cf.to_numpy(float) * ((h.Y + k) / (h.CF + k) if np.isfinite(k) else 1.0)) for k in KGRID}
            best = max(ll, key=ll.get)
            fit[arm][s] = {"kappa": float(best), "ll_gain_vs_inf": ll[best] - ll["inf"], "n": int(len(h))}
    json.dump(fit, open(C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results/p1_train_fit.json", "w"), indent=1)
    print(json.dumps(fit, indent=1))
    for ds in ("F2026", "LIVE"):
        R = cells(ds, "PRODXT1", f"p5_{ds}_t1.parquet")
        base = R[["pgx", "stat", "s_prev", "same"]].drop_duplicates(["pgx", "stat"])
        dv = base.assign(m=1 + base.apply(lambda q: fit["DV1"][f"{q.stat}|{q.same}"]["delta"], axis=1) * base.s_prev)
        outs = {"DV1": dv}
        for arm, key in (("OC1", "opp"), ("OT1", "team")):
            S = pit_sums(R, key).drop_duplicates(["pgx", "stat"])
            k = S.stat.map({s: fit[arm][s]["kappa"] for s in BASE7}).to_numpy(float)
            m = np.where(np.isfinite(k), (S.Y + np.where(np.isfinite(k), k, 0)) / (S.CF + np.where(np.isfinite(k), k, 0)), 1.0)
            outs[arm] = S[["pgx", "stat", "G"]].assign(m=m)
        for arm, df in outs.items():
            w = df.pivot(index="pgx", columns="stat", values="m").add_suffix("_m").reset_index()
            if "G" in df:
                w = w.merge(df.drop_duplicates("pgx")[["pgx", "G"]], on="pgx")
            w.to_parquet(O / f"mult_{ds}_{arm}.parquet", index=False)
            print(ds, arm, {c: (round(float(w[c].mean()), 4), round(float(w[c].quantile(.05)), 3), round(float(w[c].quantile(.95)), 3)) for c in w if c.endswith("_m")})
