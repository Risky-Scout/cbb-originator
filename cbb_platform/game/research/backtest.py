"""RESEARCH ONLY: point-in-time walk-forward backtest of the game model (ratings -> means; variance model; player
adjustment; GameJointPMF metrics). The production rule replayed: for each date D, ratings are refit at D's training
cutoff (walkforward.config.refit_cutoff) on every game knowable before it, and D's games are predicted.

Splits (directive): development 2023-24 (season 2024), selection 2024-25 (2025), frozen confirmation 2025-26 (2026),
prospective 2026-27 (production shadow). Exposure note: the 2026-11-18 design prototype computed margin RMSE on 2025-26
as a diagnostic (12.69 / 12.52); no parameter here is chosen on 2025-26.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from cbb_platform.game import ratings as GR
from cbb_platform.walkforward import config as WC


def season_final(games: pd.DataFrame, season: int, prior, p: GR.RatingParams):
    g = games[games["season"] == season]
    cut = g["knowable_at"].max() + pd.Timedelta(seconds=1)
    return GR.fit(g, cut, prior, p)


def prior_chain(games: pd.DataFrame, first: int, upto: int, p: GR.RatingParams) -> dict:
    """{season: prior Ratings for that season}: season-final fits chained from `first`, shrunk by rho_carry."""
    pri, out = None, {}
    for s in range(first, upto + 1):
        out[s] = pri
        fin = season_final(games, s, pri, p)
        pri = fin.prior_for(p.rho_carry, p.carry_level, p.carry_pace)
    return out


def walk_season(games: pd.DataFrame, season: int, prior, p: GR.RatingParams) -> pd.DataFrame:
    g = games[games["season"] == season]
    rows = []
    for d, day in g.groupby(g["game_date_et"].dt.date):
        cut = pd.Timestamp(WC.refit_cutoff(d))
        r = GR.fit(g, cut, prior, p)
        day = day[day["tip_utc"] >= cut]                  # games already final at D's cutoff are training, not forecast
        if not len(day):
            continue
        pr = r.predict(day["home"].to_numpy(), day["away"].to_numpy(), day["neutral"].to_numpy())
        out = day[["game_id", "season", "game_date_et", "home", "away", "neutral", "conference_game", "home_pts",
                   "away_pts", "ot", "poss", "home_conf", "away_conf"]].reset_index(drop=True)
        out = pd.concat([out, pr.rename(columns={"poss": "pred_poss"})], axis=1)
        out["train_games"] = r.meta["games"]
        out["cutoff"] = cut
        # PIT check: every training game knowable strictly before the cutoff; no forecast game is in the training set
        assert r.meta["max_knowable_at"] is None or pd.Timestamp(r.meta["max_knowable_at"]) < cut
        assert not set(day["game_id"]) & set(g.loc[g["knowable_at"] < cut, "game_id"])
        rows.append(out)
    return pd.concat(rows, ignore_index=True)


def metrics(df: pd.DataFrame) -> dict:
    m = df["home_pts"] - df["away_pts"]
    t = df["home_pts"] + df["away_pts"]
    pm = df["exp_home"] - df["exp_away"]
    pt = df["exp_home"] + df["exp_away"]
    return {"n": int(len(df)), "margin_rmse": float(np.sqrt(((m - pm) ** 2).mean())),
            "margin_mae": float((m - pm).abs().mean()), "total_rmse": float(np.sqrt(((t - pt) ** 2).mean())),
            "total_mae": float((t - pt).abs().mean()), "margin_bias": float((m - pm).mean()),
            "total_bias": float((t - pt).mean())}


def crps_normal(y, mu, sd) -> np.ndarray:
    z = (np.asarray(y) - np.asarray(mu)) / np.asarray(sd)
    phi = np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    Phi = 0.5 * (1 + np.vectorize(math.erf)(z / math.sqrt(2)))
    return np.asarray(sd) * (z * (2 * Phi - 1) + 2 * phi - 1 / math.sqrt(math.pi))


def crps_discrete(vals: np.ndarray, pmf: np.ndarray, y: float) -> float:
    cdf = np.cumsum(pmf)
    step = (vals >= y).astype(float)
    return float(((cdf - step) ** 2).sum())                       # integer support: unit spacing


def load(db: str) -> pd.DataFrame:
    import duckdb
    con = duckdb.connect(db, read_only=True)
    return GR.games_frame(con, (2013, 2026))


if __name__ == "__main__":
    import sys
    games = load(sys.argv[1] if len(sys.argv) > 1 else "data/lockbox_2025_26/canonical/cbb.duckdb")
    p = GR.RatingParams()
    pri = prior_chain(games, 2014, 2024, p)
    df = walk_season(games, 2024, pri[2024], p)
    print(metrics(df))
