"""BR1 (ROOT_CAUSE_PROTOCOL.json): player->team bridge.
    r13_bridge.py build <base>   fit on season 2024 (TRAIN), write data/qa/large/game_20261125/g1_<BR>_walk.parquet
                                 (<base> = GC3pl -> BR1, TP1P -> BR1TP)
    r13_bridge.py gates          after r10 scoring (TP_TAG=BR1 / BR1TP): gates B1-B5 -> results/r13_br1_gates.json"""
import json, os, sys
import numpy as np, pandas as pd

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(H, "../../../.."))
G = os.path.join(ROOT, "data/qa/large/game_20261125")
RES = os.path.join(ROOT, "docs/audits/2026-11-26-rootcause/results")
src = open(os.path.join(H, "r7_rd1_build.py")).read().split('\nP25 = grid(rows("F2025"))')[0]
R7 = {"__file__": os.path.join(H, "r7_rd1_build.py"), "__name__": "r7lib"}
exec(compile(src, "r7_rd1_build.py", "exec"), R7)


def team_loss(ds):
    P = R7["grid"](R7["rows"](ds))
    g = P.groupby(["game_id", "team_id"]).agg(lost=("lost_y_PTS5", "first"), tot=("tot_y_PTS5", "first")).reset_index()
    g["L"] = (g.lost.fillna(0) / (g.lost.fillna(0) + g.tot).clip(lower=1e-6)).clip(0, 1)
    return g[["game_id", "team_id", "L"]]


def with_loss(w):
    L = pd.concat([team_loss(ds) for ds in ("F2024", "F2025", "LIVE")], ignore_index=True).drop_duplicates(["game_id", "team_id"])
    m = dict(zip(zip(L.game_id, L.team_id), L.L))
    w = w.copy()
    w["L_home"] = [m.get((g, t), 0.0) for g, t in zip(w.game_id, w.home)]
    w["L_away"] = [m.get((g, t), 0.0) for g, t in zip(w.game_id, w.away)]
    return w


def fit(w):
    t = w[w.season == 2024]
    y = np.r_[t.home_pts - t.exp_home, t.away_pts - t.exp_away]
    X = np.c_[np.r_[t.L_home, t.L_away], np.r_[t.L_away, t.L_home]]
    a, b = np.linalg.lstsq(X, y, rcond=None)[0]
    return float(a), float(b), int((X.sum(1) > 0).sum())


if sys.argv[1] == "build":
    base = sys.argv[2]
    out = {"GC3pl": "BR1", "TP1P": "BR1TP"}[base]
    w = with_loss(pd.read_parquet(f"{G}/g1_{base}_walk.parquet"))
    co_w = with_loss(pd.read_parquet(f"{G}/g1_GC3pl_walk.parquet"))         # coefficients always from the incumbent walk
    a, b, n = fit(co_w)
    w["exp_home"] = w.exp_home + a * w.L_home + b * w.L_away
    w["exp_away"] = w.exp_away + a * w.L_away + b * w.L_home
    w.drop(columns=["L_home", "L_away"]).to_parquet(f"{G}/g1_{out}_walk.parquet")
    w[["game_id", "season", "L_home", "L_away"]].to_parquet(f"{G}/g1_{out}_loss.parquet")
    json.dump({"a_self": a, "b_opp": b, "train_team_games_with_absence": n,
               "games_with_absence": {int(s): int(((q.L_home + q.L_away) > 0).sum()) for s, q in w.groupby("season")}},
              open(f"{RES}/r13_br1_fit.json", "w"), indent=1)
    print(out, "a", round(a, 2), "b", round(b, 2), "n", n)

if sys.argv[1] == "gates":
    rng = np.random.default_rng(13)

    def boot(d):
        d = np.asarray(d, float)
        if len(d) < 5:
            return [float(d.mean()) if len(d) else None, None, None]
        m = d[rng.integers(0, len(d), (2000, len(d)))].mean(1)
        return [float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]

    def ll(p, y):
        p = np.clip(p, 1e-6, 1 - 1e-6); return -(y * np.log(p) + (1 - y) * np.log(1 - p))

    def compare(tag, base):
        s = pd.read_parquet(f"{G}/g1_{tag}.parquet").merge(pd.read_parquet(f"{G}/g1_{tag}_loss.parquet"), on=["game_id", "season"])
        b = pd.read_parquet(f"{G}/g1_{base}.parquet")[["game_id", "pm", "pt", "p_home"]]
        j = s.merge(b, on="game_id", suffixes=("", "_b"))
        R = {}
        for season, h in j.groupby("season"):
            yw = (h.y_m > 0).astype(float)
            dm = (h.y_m - h.pm) ** 2 - (h.y_m - h.pm_b) ** 2
            ab = (h.L_home + h.L_away) > 0
            R[int(season)] = {"n": int(len(h)), "n_absence": int(ab.sum()), "margin_absence": boot(dm[ab]), "margin_all": boot(dm),
                              "total_all": boot((h.y_t - h.pt) ** 2 - (h.y_t - h.pt_b) ** 2),
                              "ml_all": boot(ll(h.p_home, yw) - ll(h.p_home_b, yw)),
                              "big12_margin_point": float(dm[h.pm_b.abs() >= 12].mean())}
        return R

    R = compare("BR1", "GC3pl")
    Gt = {"B1": R[2025]["margin_absence"][2] < 0 and R[2027]["margin_absence"][0] <= 0,
          "B2": R[2025]["ml_all"][2] <= 0.001,
          "B3": R[2025]["total_all"][2] <= 0.5 and R[2027]["total_all"][0] <= 1.0,
          "B4": all(R[s]["big12_margin_point"] <= 0.5 for s in (2025, 2027))}
    out = {"vs_incumbent": R}
    if os.path.exists(f"{G}/g1_BR1TP.parquet") and os.path.exists(f"{G}/g1_TP1P.parquet"):
        R2 = compare("BR1TP", "TP1P"); out["vs_TP1P"] = R2
        Gt["B5"] = R2[2025]["margin_absence"][2] < 0
    Gt["DECISION"] = "PROMOTE" if all(Gt.get(k) for k in ("B1", "B2", "B3", "B4", "B5")) else "REJECT"
    out["gates"] = Gt
    json.dump(out, open(f"{RES}/r13_br1_gates.json", "w"), indent=1)
    for k, v in out.items():
        if k != "gates":
            for s, r in v.items():
                print(k, s, "absence n", r["n_absence"], "margin(abs) %+.2f [%s]" % (r["margin_absence"][0], r["margin_absence"][1:]),
                      "ML %+.4f ub %s" % (r["ml_all"][0], r["ml_all"][2]), "total %+.3f" % r["total_all"][0], "12+ %+.2f" % r["big12_margin_point"])
    print(Gt)
