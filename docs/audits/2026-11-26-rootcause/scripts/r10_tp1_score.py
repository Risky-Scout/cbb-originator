"""Score the TP1 walk exactly like g1_backtest (validate.V.run, same lines) and apply TP1 gates vs GC3 (g1_GC3pl)."""
import json, os, sys


def main():
    os.environ.setdefault("CBB_EVAL_WORKERS", "1")
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
    G1 = os.path.join(ROOT, "docs/audits/2026-11-25-game/scripts/g1_backtest.py")
    sys.argv = ["g1_backtest.py", "TP1"]
    src = open(G1).read().split('\nif __name__ == "__main__":')[0]
    ns = {"__file__": G1, "__name__": "g1lib"}; exec(compile(src, G1, "exec"), ns)
    import duckdb, numpy as np, pandas as pd
    D, V, GR, gm = ns["D"], ns["V"], ns["GR"], ns["gm"]
    class _Serial:                                      # in-process executor: same score_rows, no process pool
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def map(self, f, it): return map(f, it)
    V.ProcessPoolExecutor = _Serial
    con = duckdb.connect(str(ns["LOCK"]), read_only=True)
    idm = dict(con.execute("select provider_game_key, canonical_game_id from metadata.game_id_map where provider='CBBD'").fetchall()); con.close()
    L = [ns["lines"](s, idm) for s in (2024, 2025)]
    c2 = duckdb.connect(str(D / "increment.duckdb"), read_only=True)
    idm27 = dict(c2.execute("select source_record_key, canonical_game_id from flagship.games where season = 2027").fetchall()); c2.close()
    L.append(ns["lines"](2027, {str(k).removeprefix("id="): v for k, v in idm27.items()}))
    LN = pd.concat(L, ignore_index=True).drop_duplicates("game_id")
    TAG = os.environ.get("TP_TAG", "TP1")
    P = pd.read_parquet(D / f"g1_{TAG}_walk.parquet")
    out = D / f"g1_{TAG}.parquet"
    if out.exists():
        s = pd.read_parquet(out)
    else:
        s = V.run(P, gm["variance"], LN[["game_id", "spread", "total", "hml", "aml"]], workers=int(os.environ["CBB_EVAL_WORKERS"]))
        s = s.merge(P[["game_id", "game_date_et", "exp_home", "exp_away"]], on="game_id"); s.to_parquet(out)
    b = pd.read_parquet(D / "g1_GC3pl.parquet")
    j = s.merge(b[["game_id", "pm", "pt", "p_home"]], on="game_id", suffixes=("", "_b"))
    rng = np.random.default_rng(7)
    def boot(d, n=2000):
        d = np.asarray(d); i = rng.integers(0, len(d), (n, len(d))); m = d[i].mean(1); return float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))
    def ll(p, y): p = np.clip(p, 1e-6, 1 - 1e-6); return -(y * np.log(p) + (1 - y) * np.log(1 - p))
    R, G = {}, {}
    for season, h in j.groupby("season"):
        yw = (h.y_m > 0).astype(float)
        dm = (h.y_m - h.pm) ** 2 - (h.y_m - h.pm_b) ** 2
        dt = (h.y_t - h.pt) ** 2 - (h.y_t - h.pt_b) ** 2
        dl = ll(h.p_home, yw) - ll(h.p_home_b, yw)
        big = h.pm_b.abs() >= 12
        early = h.ngp <= 8
        R[int(season)] = {"n": int(len(h)), "margin": boot(dm), "total": boot(dt), "ml_ll": boot(dl), "big12_margin_point": float(dm[big].mean()),
                          "early_margin_point": float(dm[early].mean()), "early_n": int(early.sum()),
                          "rmse_margin": [float(np.sqrt(((h.y_m - h.pm_b) ** 2).mean())), float(np.sqrt(((h.y_m - h.pm) ** 2).mean()))]}
    r = R
    G["T1"] = r[2024]["margin"][2] < 0 and r[2025]["margin"][2] < 0
    G["T2"] = r[2027]["margin"][0] <= 0
    G["T3"] = r[2024]["total"][2] <= 0.5 and r[2025]["total"][2] <= 0.5 and r[2027]["total"][0] <= 1.0
    G["T4"] = r[2024]["ml_ll"][2] <= 0.001 and r[2025]["ml_ll"][2] <= 0.001
    G["T5"] = all(r[k]["big12_margin_point"] <= 0.5 for k in r)
    G["T6"] = r[2024]["early_margin_point"] < 0 and r[2025]["early_margin_point"] < 0
    G["DECISION"] = "PROMOTE" if all(G[k] for k in ("T1", "T2", "T3", "T4", "T5", "T6")) else "REJECT"
    json.dump({"results": R, "gates": G}, open(os.path.join(ROOT, f"docs/audits/2026-11-26-rootcause/results/r10_{TAG.lower()}_gates.json"), "w"), indent=1)
    for k, v in R.items():
        print(k, v["n"], "margin dMSE %+.2f [%+.2f,%+.2f]" % tuple(v["margin"]), "total %+.2f [%+.2f,%+.2f]" % tuple(v["total"]), "ML %+.4f [%+.4f,%+.4f]" % tuple(v["ml_ll"]),
              "12+ %+.2f early %+.2f (n %d) RMSE %.2f -> %.2f" % (v["big12_margin_point"], v["early_margin_point"], v["early_n"], *v["rmse_margin"]))
    print(G)


if __name__ == "__main__":
    main()
