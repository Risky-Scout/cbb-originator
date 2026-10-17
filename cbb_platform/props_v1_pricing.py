"""Production candidate v1: exact marginal prop pricing + deterministic slate scorer on the FROZEN model.

Frozen inputs (unchanged): Challenger v0 conditional models (tag cbb-player-props-v0) and the participation model
(tag cbb-player-props-v0-participation, commit f9cf4df), dataset cbb_data_v2.4+5f19d31e524a. This module only prices
their PMFs; it fits nothing new. No sportsbook data; 2025-26 is refused by the frozen loaders.

Pricing (Y = the conditional-on-playing count, PMF over 0..K with the last point = P(Y >= K)):
    integer line L    P_OVER = P(Y > L), P_PUSH = P(Y = L), P_UNDER = P(Y < L)
    half line L       P_OVER = P(Y > L), P_PUSH = 0,        P_UNDER = P(Y < L)
Lines must be multiples of 0.5 with L <= K - 0.5, where every price is exact. Fair odds use the non-push probabilities
p / (P_OVER + P_UNDER); the raw push probability is kept. participation_probability is reported beside the prices and
is never mixed into the PMF (the candidate prices the market conditional on the player playing).

    .venv/bin/python -m cbb_platform.props_v1_pricing   -> modeling/player_props_v1/sample_predictions.csv
"""
from __future__ import annotations

import time

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_participation as Q

PRICING_VERSION = "props-v1-pricing-1.0.0"
MODEL_VERSION = "cbb-props-v0@53c0c2c+participation@f9cf4df+" + PRICING_VERSION
SAMPLE_PATH = C.PROJECT_ROOT / "modeling" / "player_props_v1" / "sample_predictions.csv"
SAMPLE_SEASON = 2025                    # 2024-25 validation season; model state fit through 2023-24 only
SAMPLE_GAMES = 2
LADDER_TAIL = 0.005                     # ladder covers the central 99 %: quantiles 0.005 .. 0.995
QUANTILES = {"p10": 0.10, "p25": 0.25, "p50": 0.50, "p75": 0.75, "p90": 0.90}
COLUMNS = ["game_id", "scheduled_tip", "team", "opponent", "player", "canonical_player_id", "stat",
           "participation_probability", "conditional_minutes", "mean", "median", *QUANTILES, "line",
           "over_probability", "under_probability", "push_probability", "over_probability_nonpush",
           "under_probability_nonpush", "fair_over_decimal", "fair_under_decimal", "fair_over_american",
           "fair_under_american", "model_version", "dataset_version"]


# ============================================================================ pricing (pure)
def price(pmf: np.ndarray, lines) -> dict:
    """Exact over / under / push for each (pmf row, line). pmf: (n, K+1); lines: scalar or (n,)."""
    pmf = np.atleast_2d(pmf)
    lines = np.broadcast_to(np.asarray(lines, dtype=float), (pmf.shape[0],))
    K = pmf.shape[1] - 1
    if not np.all(np.isclose(lines * 2, np.round(lines * 2))) or np.any(lines > K - 0.5):
        raise ValueError(f"lines must be multiples of 0.5 and <= {K - 0.5}")
    cdf = np.cumsum(pmf, axis=1)
    rows = np.arange(pmf.shape[0])
    integer = np.isclose(lines, np.round(lines))
    fl = np.floor(lines).astype(int)
    # P(Y <= floor(L)) and P(Y = L) for integer L; negative lines have no mass below them
    cdf_fl = np.where(fl >= 0, cdf[rows, np.clip(fl, 0, K)], 0.0)
    push = np.where(integer & (fl >= 0), pmf[rows, np.clip(fl, 0, K)], 0.0)
    under = cdf_fl - push                                    # P(Y < L)
    over = 1.0 - cdf_fl                                       # P(Y > L)
    settled = over + under
    with np.errstate(divide="ignore", invalid="ignore"):
        o_np, u_np = over / settled, under / settled
    return {"over_probability": over, "under_probability": under, "push_probability": push,
            "over_probability_nonpush": o_np, "under_probability_nonpush": u_np,
            "fair_over_decimal": prob_to_decimal(o_np), "fair_under_decimal": prob_to_decimal(u_np),
            "fair_over_american": decimal_to_american(prob_to_decimal(o_np)),
            "fair_under_american": decimal_to_american(prob_to_decimal(u_np))}


def prob_to_decimal(p):
    p = np.asarray(p, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where((p > 0) & (p < 1), 1.0 / p, np.nan)          # no finite fair price at 0 or 1


def decimal_to_american(d):
    d = np.asarray(d, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(d >= 2.0, 100.0 * (d - 1.0), -100.0 / (d - 1.0))


def american_to_decimal(a):
    a = np.asarray(a, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(a > 0, 1.0 + a / 100.0, 1.0 + 100.0 / np.abs(a))


def quantiles(pmf: np.ndarray, qs) -> np.ndarray:
    """Discrete quantile: smallest k with P(Y <= k) >= q. Returns (n, len(qs))."""
    cdf = np.cumsum(np.atleast_2d(pmf), axis=1)
    return np.stack([np.argmax(cdf >= q - 1e-12, axis=1) for q in qs], axis=1)


def ladder(pmf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(row index, line) pairs: half lines from q_0.005 - 0.5 to q_0.995 + 0.5 and integer lines >= 1 inside
    [q_0.005, q_0.995]. Capped at K - 0.5 so every line prices exactly."""
    K = pmf.shape[1] - 1
    q = quantiles(pmf, (LADDER_TAIL, 1 - LADDER_TAIL))
    idx, out = [], []
    for i, (lo, hi) in enumerate(q):
        halves = np.arange(max(lo - 1, 0), min(hi, K - 1) + 1) + 0.5
        ints = np.arange(max(lo, 1), min(hi, K - 1) + 1, dtype=float)
        ls = np.sort(np.concatenate([halves, ints]))
        idx.append(np.full(len(ls), i)); out.append(ls)
    return np.concatenate(idx), np.concatenate(out)


# ============================================================================ frozen model state + slate scorer
def load_model(train_through: int = SAMPLE_SEASON - 1) -> dict:
    """The frozen v0 and participation models, fit exactly as frozen (v0 evaluation B state; participation 2023-24)."""
    if train_through != SAMPLE_SEASON - 1:
        raise ValueError("v1 candidate demonstration uses the frozen 2023-24 states only")
    cfg = P.load_config()
    frame, finals, _ = P.load_frames(cfg)
    part_rows, _ = Q.load()
    return {"cfg": cfg, "v0": P.fit(frame, finals, cfg, train_through), "part": Q.fit(part_rows),
            "part_rows": part_rows}


def load_candidates(season: int = SAMPLE_SEASON) -> pd.DataFrame:
    lockbox.check_season(season)
    cfg = P.load_config()
    hist = [s for s in range(season - cfg["recency"]["prior_seasons_used"], season + 1) if s in P.spec.MODEL_SEASONS]
    cand, _, _ = P.load_frames(cfg, seasons=hist, population="candidates")
    return cand[cand["season"] == season]


def _context(game_ids) -> pd.DataFrame:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    out = con.execute("""SELECT g.canonical_game_id, g.scheduled_tip_utc, g.home_team_id, g.away_team_id,
                                h.display_name AS home, a.display_name AS away
                         FROM core.games g JOIN core.teams h ON h.canonical_team_id = g.home_team_id
                         JOIN core.teams a ON a.canonical_team_id = g.away_team_id
                         WHERE g.canonical_game_id IN (SELECT unnest(?::VARCHAR[]))""", [list(game_ids)]).df()
    con.close()
    return out


def _player_names(pids) -> dict:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    out = dict(con.execute("SELECT canonical_player_id, display_name FROM core.players WHERE canonical_player_id IN "
                           "(SELECT unnest(?::VARCHAR[]))", [list(pids)]).fetchall())
    con.close()
    return out


def score(cand: pd.DataFrame, model: dict) -> pd.DataFrame:
    """One row per game / team / player / stat / ladder line. Deterministic (sorted, fixed rounding)."""
    cfg = model["cfg"]
    res = Q.pregame_outputs(cand, model["part_rows"], cfg, model["v0"], model["part"])
    base = res["table"].reset_index(drop=True)
    ctx = _context(base["canonical_game_id"].unique()).set_index("canonical_game_id")
    names = _player_names(base["canonical_player_id"].unique())
    g = ctx.loc[base["canonical_game_id"]].reset_index(drop=True)
    home = (base["canonical_team_id"] == g["home_team_id"]).to_numpy()
    meta = pd.DataFrame({"game_id": base["canonical_game_id"], "scheduled_tip": g["scheduled_tip_utc"].astype(str),
                         "team": np.where(home, g["home"], g["away"]), "opponent": np.where(home, g["away"], g["home"]),
                         "player": base["canonical_player_id"].map(names), "canonical_player_id": base["canonical_player_id"],
                         "participation_probability": base["participation_probability"],
                         "conditional_minutes": base["conditional_minutes"]})
    parts = []
    for stat, t in cfg["count_targets"].items():
        pmf = res["conditional_stat_distribution"][stat]
        q = quantiles(pmf, list(QUANTILES.values()))
        rows, lines = ladder(pmf)
        pr = price(pmf[rows], lines)
        df = meta.iloc[rows].reset_index(drop=True)
        df.insert(6, "stat", stat)
        df["mean"] = base[f"{stat}_mean_given_play"].to_numpy()[rows]
        df["median"] = q[rows, 2]
        for j, k in enumerate(QUANTILES):
            df[k] = q[rows, j]
        df["line"] = lines
        for k, v in pr.items():
            df[k] = v
        parts.append(df)
    out = pd.concat(parts, ignore_index=True)
    out["model_version"], out["dataset_version"] = MODEL_VERSION, cfg["upstream_dataset"]
    out = out[COLUMNS].sort_values(["scheduled_tip", "game_id", "team", "player", "canonical_player_id", "stat", "line"],
                                   kind="mergesort").reset_index(drop=True)
    return out


def write_csv(df: pd.DataFrame, path=SAMPLE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n", float_format="%.6f")


def sample_games(cand: pd.DataFrame) -> list[str]:
    """Deterministic: the SAMPLE_GAMES smallest canonical game ids of the season (ids are content hashes)."""
    return sorted(cand["canonical_game_id"].unique())[:SAMPLE_GAMES]


def run() -> dict:
    t0 = time.perf_counter(); model = load_model(); t_model = time.perf_counter() - t0
    cand = load_candidates()
    games = sample_games(cand)
    sample = score(cand[cand["canonical_game_id"].isin(games)], model)
    write_csv(sample)
    timings = {"model_load_and_fit_s": t_model}
    one = cand[cand["canonical_game_id"] == games[0]]
    t = time.perf_counter(); score(one.iloc[:1], model); timings["one_player_s"] = time.perf_counter() - t
    t = time.perf_counter(); score(one, model); timings["one_game_s"] = time.perf_counter() - t
    tips = _context(cand["canonical_game_id"].unique())
    day = tips["scheduled_tip_utc"].dt.tz_convert("US/Eastern").dt.date
    busiest = day.value_counts().sort_index().idxmax()
    slate_ids = tips.loc[day == busiest, "canonical_game_id"]
    slate = cand[cand["canonical_game_id"].isin(slate_ids)]
    t = time.perf_counter(); s = score(slate, model); timings["representative_slate_s"] = time.perf_counter() - t
    timings.update(slate_date=str(busiest), slate_games=int(slate["canonical_game_id"].nunique()),
                   slate_candidates=int(len(slate)), slate_rows=int(len(s)), sample_rows=int(len(sample)))
    return timings


if __name__ == "__main__":
    for k, v in run().items():
        print(k, round(v, 3) if isinstance(v, float) else v)
