"""RESEARCH ONLY (Step 2, 2026-11-25): point-in-time walk-forward of the PRODUCTION game model (sealed params) on
2023-24 (dev), 2024-25 (selection) and 2026-27 live (prospective), scored with the production GameJointPMF scorer
(research/validate.py) and joined to CBBD opening / closing lines (DraftKings preferred, else the first provider).

    g1_backtest.py <tag> [ratings overrides json]   -> data/qa/large/game_20261125/g1_<tag>.parquet

Overrides (challengers) replace RatingParams fields for every season; 2027 uses the sealed prior unless the
override changes a prior-chain field (rho_carry / lam_*), in which case the 2027 prior is rebuilt from the canonical
chain through 2026 with the override (same construction as the sealed prior)."""
import json
import os
import sys

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")                    # one numerical-library thread per process (Mac resource rule)
WORKERS = int(os.environ.get("CBB_EVAL_WORKERS", "2"))  # at most two CPU-intensive workers

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.game import forecast as GF, params as GP, ratings as GR
from cbb_platform.game.research import backtest as BT, validate as V
from cbb_platform.walkforward import config as WC

D = C.PROJECT_ROOT / "data/qa/large/game_20261125"
LOCK = C.PROJECT_ROOT / "data/lockbox_2025_26/canonical/cbb.duckdb"
tag = sys.argv[1]
ov = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
gm = GP.game_model()
rp = GR.RatingParams(**{**{k: gm["ratings"][k] for k in ("tau_days", "lam_off", "lam_def", "lam_pace", "rho_carry", "lam_league")}, **ov})


def ngp(day, g, cut):
    k = g[g.knowable_at < cut]
    c = pd.concat([k.home, k.away]).value_counts()
    return np.minimum(day.home.map(c).fillna(0).to_numpy(), day.away.map(c).fillna(0).to_numpy())


def walk(g, pri):
    rows = []
    for d, day in g.groupby(g["game_date_et"].dt.date):
        cut = pd.Timestamp(WC.refit_cutoff(d))
        day = day[day["tip_utc"] >= cut]
        if not len(day):
            continue
        r = GR.fit(g, cut, pri, rp)
        pr = r.predict(day["home"].to_numpy(), day["away"].to_numpy(), day["neutral"].to_numpy())
        out = day[["game_id", "season", "game_date_et", "home", "away", "neutral", "conference_game", "home_pts", "away_pts", "ot"]].reset_index(drop=True)
        out = pd.concat([out, pr[["exp_home", "exp_away"]].reset_index(drop=True)], axis=1)
        out["ngp"] = ngp(day, g, cut)
        rows.append(out)
    return pd.concat(rows, ignore_index=True)


def lines(season, idmap):
    raw = json.load(open(D / f"lines_{season}.json"))
    out = []
    for g in raw:
        ls = sorted(g.get("lines") or [], key=lambda l: l.get("provider") != "Draft Kings")
        if not ls:
            continue
        sp = next((l for l in ls if l.get("spread") is not None), {})
        tt = next((l for l in ls if l.get("overUnder") is not None), {})
        ml = next((l for l in ls if l.get("homeMoneyline") and l.get("awayMoneyline")), {})
        out.append({"cbbd": str(g["gameId"]), "spread": sp.get("spread"), "spread_open": sp.get("spreadOpen"),
                    "total": tt.get("overUnder"), "total_open": tt.get("overUnderOpen"),
                    "hml": ml.get("homeMoneyline"), "aml": ml.get("awayMoneyline"), "provider": sp.get("provider")})
    df = pd.DataFrame(out)
    df["game_id"] = df.cbbd.map(idmap)
    return df.dropna(subset=["game_id"])


if __name__ == "__main__":
    con = duckdb.connect(str(LOCK), read_only=True)
    hist = GR.games_frame(con, (2013, 2026))
    idm = dict(con.execute("select provider_game_key, canonical_game_id from metadata.game_id_map where provider='CBBD'").fetchall())
    con.close()
    L = []
    for s in (2024, 2025):
        L.append(lines(s, idm))
    inc = D / "increment.duckdb"
    ck = D / f"g1_{tag}_walk.parquet"                 # checkpoint: walk predictions survive a scoring crash
    if ck.exists():
        P = pd.read_parquet(ck)
    else:
        preds = []
        chain = BT.prior_chain(hist, gm["ratings"]["prior_chain_first_season"], 2026, rp)
        for s in (2024, 2025):
            preds.append(walk(hist[hist.season == s], chain[s]))
        g27 = GF.season_games(inc, 2027)
        if any(k in ov for k in ("rho_carry", "lam_off", "lam_def", "lam_pace", "lam_league", "tau_days")):
            pri27 = BT.season_final(hist, 2026, chain[2026], rp).prior_for(rp.rho_carry)
        else:
            pri27 = GF.prior(2027)
        preds.append(walk(g27, pri27))
        P = pd.concat(preds, ignore_index=True)
        P.to_parquet(ck)
    c2 = duckdb.connect(str(inc), read_only=True)
    idm27 = dict(c2.execute("select source_record_key, canonical_game_id from flagship.games where season = 2027").fetchall())
    c2.close()
    L.append(lines(2027, {str(k).removeprefix("id="): v for k, v in idm27.items()}))
    LN = pd.concat(L, ignore_index=True).drop_duplicates("game_id")
    s = V.run(P, gm["variance"], LN[["game_id", "spread", "total", "hml", "aml"]], workers=WORKERS)
    s = s.merge(P[["game_id", "game_date_et", "exp_home", "exp_away"]], on="game_id").merge(
        LN[["game_id", "spread_open", "total_open", "provider"]], on="game_id", how="left")
    s.to_parquet(D / f"g1_{tag}.parquet")
    for season, h in s.groupby("season"):
        o = h.dropna(subset=["close_spread"])
        print(season, len(h), "with close", len(o), "margin RMSE model", round(float(np.sqrt(((h.y_m - h.pm) ** 2).mean())), 3),
              "| same games model", round(float(np.sqrt(((o.y_m - o.pm) ** 2).mean())), 3), "close", round(float(np.sqrt(((o.y_m + o.close_spread) ** 2).mean())), 3), flush=True)
