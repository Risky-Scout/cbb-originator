"""RESEARCH ONLY (Mac): fit the frozen endgame kernel + overtime parameters of the GameJointPMF from historical PBP /
period scores, and write the compact production artifact cbb_platform/game/params/endgame_ot.npz (+ .json manifest).
Production never reads PBP: it loads only this artifact (game.params.load).

Endgame: state at T* (seconds left in the 2nd half) = the score of the last play at or above T*; increment to the end
of regulation = reconciled period-1+2 scores minus that state. Conditioned on the home lead d at T* (-D..D) and a
scoring-pace bucket (regulation points-per-minute at T*), home/away-mirrored (K_d(x, y) = K_-d(y, x)), lightly smoothed
across neighbouring leads. Fit seasons default 2014-2023 (development + earlier only: 2023-24.. stay out of sample).
OT: per OT period, per-team points; kappa = sum(OT pts) / sum(5/40 * regulation pts), team SD and home/away correlation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "params"
T_STAR, D, R = 120, 20, 30


def fit(db: Path, seasons: tuple[int, int]) -> dict:
    con = duckdb.connect(str(db), read_only=True)
    lo, hi = seasons
    rows = con.execute(f"""
        with g as (select canonical_game_id, home_period_points hp, away_period_points ap, overtime_count
                   from flagship.played_games where season between {lo} and {hi} and game_universe = 'DI_VS_DI'
                     and len(home_period_points) >= 2 and len(away_period_points) >= 2),
        s as (select canonical_game_id, arg_max(home_score, play_sequence) h, arg_max(away_score, play_sequence) a,
                     max(play_sequence) seq
              from flagship.plays where season between {lo} and {hi} and period = 2 and seconds_remaining >= {T_STAR}
              group by 1),
        e as (select canonical_game_id, arg_max(home_score, play_sequence) h2, arg_max(away_score, play_sequence) a2
              from flagship.plays where season between {lo} and {hi} and period = 2 group by 1)
        select s.h, s.a, g.hp[1] + g.hp[2] rh, g.ap[1] + g.ap[2] ra, g.overtime_count, e.h2, e.a2,
               g.hp, g.ap
        from g join s using (canonical_game_id) join e using (canonical_game_id)""").fetchall()
    st = {"games_joined": len(rows)}
    H = np.array([r[0] for r in rows], float); A = np.array([r[1] for r in rows], float)
    RH = np.array([r[2] for r in rows], float); RA = np.array([r[3] for r in rows], float)
    E2H = np.array([r[5] for r in rows], float); E2A = np.array([r[6] for r in rows], float)
    ok = (RH >= H) & (RA >= A) & (RH - H <= R) & (RA - A <= R) & (E2H == RH) & (E2A == RA) & (H + A > 40)
    st.update(games_used=int(ok.sum()), dropped_inconsistent=int((~ok).sum()))
    H, A, RH, RA = H[ok], A[ok], RH[ok], RA[ok]
    d = (H - A).astype(int)
    ppm = (H + A) / (40.0 - T_STAR / 60.0)
    edges = tuple(float(round(q, 4)) for q in np.quantile(ppm, [1 / 3, 2 / 3]))   # combined pts/min tertiles
    st["pace_edges_ppm"] = list(edges)
    pb = np.digitize(ppm, edges)
    dh, da = (RH - H).astype(int), (RA - A).astype(int)
    cnt = np.zeros((3, 2 * D + 1, R + 1, R + 1))
    di = np.clip(d, -D, D) + D
    np.add.at(cnt, (pb, di, dh, da), 1.0)
    np.add.at(cnt, (pb, np.clip(-d, -D, D) + D, da, dh), 1.0)                  # home/away mirror
    # light smoothing across neighbouring leads (triangular 1-2-1, never across the d = 0 / sign boundary it mirrors)
    sm = cnt.copy()
    sm[:, 1:-1] = 0.25 * cnt[:, :-2] + 0.5 * cnt[:, 1:-1] + 0.25 * cnt[:, 2:]
    pooled = sm.sum(axis=0, keepdims=True)                                     # lead-only fallback for thin buckets
    n = sm.sum(axis=(2, 3), keepdims=True)
    MIN_N = 200.0                                    # shrink a (pace, lead) bucket toward its lead's pooled shape
    w = n / (n + MIN_N)
    tab = w * np.divide(sm, n, out=np.zeros_like(sm), where=n > 0) + (1 - w) * pooled / pooled.sum(axis=(2, 3), keepdims=True)
    st["thin_buckets_lt_200"] = int((n < 200).sum())
    st["cell_counts_by_pace"] = [int(x) for x in cnt.sum(axis=(1, 2, 3)) / 2]
    st["min_lead_bucket_count"] = int((cnt.sum(axis=(2, 3)) / 2).min())
    reg_tie = float(np.mean(RH == RA))
    # OT
    ot = con.execute(f"""select home_period_points, away_period_points from flagship.played_games where season between {lo} and {hi}
                         and game_universe = 'DI_VS_DI' and overtime_count > 0
                         and len(home_period_points) = 2 + overtime_count""").fetchall()
    x, y, bx, by, more = [], [], [], [], 0
    for hp, ap in ot:
        rh, ra = hp[0] + hp[1], ap[0] + ap[1]
        if rh != ra:
            continue
        for k in range(2, len(hp)):
            x.append(hp[k]); y.append(ap[k]); bx.append(rh); by.append(ra)
            more += int(hp[k] == ap[k])
    x, y, bx, by = map(lambda v: np.asarray(v, float), (x, y, bx, by))
    kappa = float((x.sum() + y.sum()) / ((5 / 40) * (bx.sum() + by.sum())))
    rx, ry = x - kappa * (5 / 40) * bx, y - kappa * (5 / 40) * by
    sd = float(np.sqrt((rx.var() + ry.var()) / 2))
    rho = float(np.corrcoef(rx, ry)[0, 1])
    st.update(regulation_tie_rate=reg_tie, ot_periods=int(len(x)), ot_period_tie_rate=float(more / max(len(x), 1)),
              ot_kappa=kappa, ot_sd_team=sd, ot_rho=rho, ot_mean_points_per_team=float((x.mean() + y.mean()) / 2))
    return {"table": tab.astype(np.float64), "stats": st,
            "ot": {"kappa": kappa, "sd_team": sd, "rho": rho, "reach": R}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/canonical/cbb.duckdb")
    ap.add_argument("--seasons", default="2014-2023")
    a = ap.parse_args()
    lo, hi = (int(v) for v in a.seasons.split("-"))
    f = fit(Path(a.db), (lo, hi))
    OUT.mkdir(parents=True, exist_ok=True)
    npz = OUT / "endgame_ot.npz"
    np.savez_compressed(npz, table=f["table"].astype(np.float32))
    sha = hashlib.sha256(npz.read_bytes()).hexdigest()
    man = {"artifact": npz.name, "sha256": sha, "t_star_sec": T_STAR, "max_lead": D, "reach": R,
           "pace_edges_ppm": f["stats"]["pace_edges_ppm"], "fit_seasons": [lo, hi], "source": "flagship.plays + played_games",
           "ot": f["ot"], "fit_stats": f["stats"]}
    (OUT / "endgame_ot.json").write_text(json.dumps(man, indent=1))
    print(json.dumps(man, indent=1))


if __name__ == "__main__":
    main()
