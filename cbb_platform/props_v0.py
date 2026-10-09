"""Player-prop Challenger v0: minutes x shrunk per-minute rate -> Poisson / Negative Binomial count distributions.

Inputs are ONLY the accepted Stage 3 stores (dataset cbb_data_v2.4+5f19d31e524a): approved point-in-time features of
feat.player_features, prior-season final box sums of feat.player_box_state (used only when available_at <= the
target's prediction cutoff), and the target-game labels of lab.player_game_labels. No market data; 2025-26 is never read.

    minutes   weighted ridge regression (numpy normal equations) on standardized pregame features + missing indicators
    rate      per target: (recency-weighted player sums + k * position prior rate) / (recency-weighted minutes + k)
    mean      predicted minutes x rate
    dist      Negative Binomial with one training-estimated dispersion per target when overdispersion is material,
              Poisson otherwise; minutes get a discretized normal with training residual SD by predicted-minutes band
    baseline  rolling season-to-date per-appearance average shrunk toward the population (prior season, then population
              when the player has no current-season appearance), with the same distribution machinery

Every piece of model state (standardization, ridge coefficients, priors, k, dispersion, residual SDs, baseline means) is
fit by fit() from rows with season <= train_through only. Config: modeling/player_props_v0/config.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, lockbox
from cbb_platform.stage3 import spec

CONFIG_PATH = C.PROJECT_ROOT / "modeling" / "player_props_v0" / "config.json"
POS_GROUPS = ("G", "F", "C", "U")
COLD = ("WARM", "COLD_EARLY_SEASON", "COLD_NO_HISTORY", "COLD_TRANSFER_IN")


def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text())


def _pos_sql(col: str) -> str:
    return f"""CASE WHEN {col} IN ('G', 'PG', 'SG') THEN 'G' WHEN {col} IN ('F', 'SF', 'PF') THEN 'F'
                    WHEN {col} = 'C' THEN 'C' ELSE 'U' END"""


# ============================================================================ data
def load_frames(cfg: dict, seasons=None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(scoring frame, training player-season finals, exclusion counts). Reads only seasons in spec.MODEL_SEASONS."""
    seasons = sorted(seasons or spec.MODEL_SEASONS)
    for s in seasons:
        lockbox.check_season(s)                       # raises on 2025-26
        if s not in spec.MODEL_SEASONS:
            raise ValueError(f"season {s} outside the accepted Stage 3 window")
    lo, hi = seasons[0], seasons[-1]
    targets = list(cfg["count_targets"].values())
    hs = cfg["history_sums"]
    cur = ", ".join(f"({' + '.join(f'f.pstd_sum_{c}::DOUBLE' for c in hs[t])}) AS cur_{t}" for t in targets)
    fin_cols = ", ".join(f"({' + '.join(f's_{c}' for c in hs[t])})::DOUBLE AS {t}" for t in targets)
    hist = ", ".join(f"h{j}.{c} AS h{j}_{c}" for j in range(1, cfg["recency"]["prior_seasons_used"] + 1)
                     for c in ["min", "n_app"] + targets)
    hjoin = " ".join(f"""LEFT JOIN fin h{j} ON h{j}.canonical_player_id = f.canonical_player_id AND h{j}.season = f.season - {j}
                         AND h{j}.available_at <= f.prediction_cutoff_utc"""
                     for j in range(1, cfg["recency"]["prior_seasons_used"] + 1))
    feats = ", ".join(f"f.{c}::DOUBLE AS {c}" for c in cfg["minutes_features"])
    con = duckdb.connect(str(spec.FEATURES_DB), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute(f"ATTACH '{spec.LABELS_DB}' AS lb (READ_ONLY)")
    fin_sql = f"""fin AS (SELECT canonical_player_id, season, available_at, s_min::DOUBLE AS min, n_app::DOUBLE AS n_app,
                          {_pos_sql('last_position')} AS pos, {fin_cols}
                   FROM feat.player_box_state WHERE season BETWEEN {lo} AND {hi}
                   QUALIFY row_number() OVER (PARTITION BY canonical_player_id, season ORDER BY available_at DESC, source_game_id DESC) = 1)"""
    frame = con.execute(f"""WITH {fin_sql}
        SELECT f.player_game_example_id, f.canonical_player_id, f.canonical_team_id, f.season, f.prediction_cutoff_utc,
               f.is_home::DOUBLE AS is_home, {_pos_sql('f.prof_last_box_position')} AS pos, f.cold_start_class,
               f.pstd_sum_min::DOUBLE AS cur_min, f.pstd_appearances::DOUBLE AS cur_n_app, {cur}, {feats}, {hist},
               l.min::DOUBLE AS y_min, {", ".join(f"l.{t}::DOUBLE AS y_{t}" for t in targets)},
               l.counting_labels_available AS counts_ok
        FROM feat.player_features f JOIN lb.lab.player_game_labels l USING (player_game_example_id) {hjoin}
        WHERE f.season BETWEEN {lo} AND {hi} AND l.participation_status = 'PLAYED' AND l.box_row_present
          AND l.minutes_label_available
        ORDER BY f.player_game_example_id""").df()
    finals = con.execute(f"WITH {fin_sql} SELECT * EXCLUDE (available_at) FROM fin ORDER BY canonical_player_id, season").df()
    excl = {}
    for (s, reason, n) in con.execute(f"""SELECT season, CASE
            WHEN participation_status = 'PLAYED' AND box_row_present AND minutes_label_available AND NOT counting_labels_available
                 THEN 'SCORED_MIN_ONLY_COUNT_LABELS_UNAVAILABLE'
            WHEN participation_status = 'PLAYED' AND box_row_present AND minutes_label_available THEN 'SCORED'
            WHEN participation_status = 'PLAYED' AND box_row_present THEN 'EXCLUDED_MINUTES_LABEL_UNAVAILABLE'
            WHEN participation_status = 'PLAYED' THEN 'EXCLUDED_PLAYED_NO_BOX_ROW_NO_OUTCOME'
            WHEN box_row_present THEN 'EXCLUDED_BOX_ROW_' || participation_status || '_NOT_AN_OBSERVED_APPEARANCE'
            ELSE 'NOT_SCORED_NO_BOX_ROW_' || participation_status END, count(*)
        FROM lb.lab.player_game_labels WHERE season BETWEEN {lo} AND {hi} GROUP BY ALL ORDER BY ALL""").fetchall():
        excl.setdefault(int(s), {})[reason] = int(n)
    for table, reason in (("target_box_rows_outside_universe", "EXCLUDED_BOX_ROW_PLAYER_OUTSIDE_PREGAME_UNIVERSE"),
                          ("target_box_rows_unresolved", "EXCLUDED_BOX_ROW_IDENTITY_UNRESOLVED")):
        for s, n in con.execute(f"SELECT season, count(*) FROM lb.lab.{table} WHERE season BETWEEN {lo} AND {hi} GROUP BY 1").fetchall():
            excl.setdefault(int(s), {})[reason] = int(n)
    con.close()
    if frame["season"].max() >= lockbox.LOCKBOX_SEASON or finals["season"].max() >= lockbox.LOCKBOX_SEASON:
        raise lockbox.LockboxViolation("2025-26 row reached the challenger")
    for t in targets:                                  # count labels unavailable -> NaN, never 0
        frame.loc[~frame["counts_ok"].astype(bool), f"y_{t}"] = np.nan
    return frame, finals, excl


# ============================================================================ helpers
def recency_weights(season_age, half_life: float) -> np.ndarray:
    return 0.5 ** (np.asarray(season_age, dtype=float) / half_life)


def _design(df: pd.DataFrame, cfg: dict, st: dict | None = None):
    """Standardized feature matrix + missing indicators + categorical dummies. st=None -> fit the transform (training)."""
    cols, mats = [], []
    fit = st is None
    st = st if st is not None else {"mean": {}, "sd": {}, "ind": []}
    for c in cfg["minutes_features"]:
        x = df[c].to_numpy(dtype=float)
        if c in cfg["log1p_features"]:
            x = np.log1p(np.clip(x, 0, None))
        if fit:
            ok = ~np.isnan(x)
            w = df["_w"].to_numpy()[ok]
            m = float(np.average(x[ok], weights=w)) if ok.any() else 0.0
            sd = float(np.sqrt(np.average((x[ok] - m) ** 2, weights=w))) if ok.any() else 1.0
            st["mean"][c], st["sd"][c] = m, (sd if sd > 0 else 1.0)
            if (~ok).any():
                st["ind"].append(c)
        z = (x - st["mean"][c]) / st["sd"][c]
        mats.append(np.nan_to_num(z, nan=0.0)); cols.append(c)
        if c in st["ind"]:
            mats.append(np.isnan(x).astype(float)); cols.append(f"{c}__missing")
    for g in POS_GROUPS[1:]:
        mats.append((df["pos"] == g).to_numpy(dtype=float)); cols.append(f"pos_{g}")
    for k in COLD[1:]:
        mats.append((df["cold_start_class"] == k).to_numpy(dtype=float)); cols.append(f"cold_{k}")
    mats.append(df["is_home"].fillna(0).to_numpy(dtype=float)); cols.append("is_home")
    return np.column_stack([np.ones(len(df))] + mats), ["intercept"] + cols, st


def _hist(df: pd.DataFrame, cfg: dict, col: str) -> np.ndarray:
    """Recency-weighted history total of `col` (current season to date + prior-season finals)."""
    tot = df[f"cur_{col}"].fillna(0).to_numpy(dtype=float).copy()
    for j in range(1, cfg["recency"]["prior_seasons_used"] + 1):
        tot += recency_weights(j, cfg["recency"]["half_life_seasons"]) * df[f"h{j}_{col}"].fillna(0).to_numpy(dtype=float)
    return tot


def pmf_matrix(mu: np.ndarray, phi: float, kmax: int) -> np.ndarray:
    """Rows: P(Y = 0..kmax-1), P(Y >= kmax). phi = 0 -> Poisson(mu); phi > 0 -> NegBin(mean mu, var mu + phi mu^2). Exact recursion."""
    mu = np.asarray(mu, dtype=float)
    out = np.empty((len(mu), kmax + 1))
    if phi > 0:
        r = 1.0 / phi
        q = mu / (r + mu)
        out[:, 0] = np.exp(r * (np.log(r) - np.log(r + mu)))
        for k in range(kmax):
            out[:, k + 1] = out[:, k] * (k + r) / (k + 1) * q
    else:
        out[:, 0] = np.exp(-mu)
        for k in range(kmax):
            out[:, k + 1] = out[:, k] * mu / (k + 1)
    out[:, -1] += np.maximum(1.0 - out.sum(axis=1), 0.0)     # tail P(Y >= kmax) folded into the last support point
    return out


def _ncdf(x):
    # Abramowitz-Stegun 7.1.26 erf (|error| < 1.5e-7); numpy has no erf and no new dependency is allowed
    z = np.abs(x) / np.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * erf)


def minutes_pmf(mu: np.ndarray, sigma: np.ndarray, kmax: int) -> np.ndarray:
    """Discretized normal on whole minutes 0..kmax (tails folded into the end points)."""
    k = np.arange(kmax + 1)
    cdf = _ncdf((k[None, :] + 0.5 - mu[:, None]) / sigma[:, None])
    cdf[:, -1] = 1.0
    return np.diff(np.concatenate([np.zeros((len(mu), 1)), cdf], axis=1), axis=1)


def _dispersion(y, mu, w, threshold):
    ok = ~np.isnan(y)
    y, mu, w = y[ok], mu[ok], w[ok]
    ratio = float(np.sum(w * (y - mu) ** 2) / np.sum(w * mu))
    phi = float(max(np.sum(w * ((y - mu) ** 2 - mu)), 0.0) / np.sum(w * mu ** 2))
    fam = "NEGBIN" if ratio > threshold and phi > 0 else "POISSON"
    return {"family": fam, "phi": phi if fam == "NEGBIN" else 0.0, "overdispersion_ratio": ratio}


def _sigma_bands(y, mu, w, edges):
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (mu >= a) & (mu < b)
        out.append(float(np.sqrt(np.average((y[m] - mu[m]) ** 2, weights=w[m]))) if m.sum() > 50 else None)
    fill = float(np.sqrt(np.average((y - mu) ** 2, weights=w)))
    return [s if s else fill for s in out]


def _band_sigma(mu, edges, sig):
    idx = np.clip(np.searchsorted(edges, mu, side="right") - 1, 0, len(sig) - 1)
    return np.asarray(sig)[idx]


# ============================================================================ fit / predict
def fit(frame: pd.DataFrame, finals: pd.DataFrame, cfg: dict, train_through: int) -> dict:
    """All model state from rows with season <= train_through (frame AND player-season finals)."""
    tr = frame[frame["season"] <= train_through].copy()
    fin = finals[finals["season"] <= train_through]
    hl = cfg["recency"]["half_life_seasons"]
    tr["_w"] = recency_weights(train_through - tr["season"].to_numpy(), hl)
    w = tr["_w"].to_numpy()
    targets = list(cfg["count_targets"].values())
    st = {"train_through": train_through, "n_train_rows": int(len(tr)),
          "train_max_cutoff": str(tr["prediction_cutoff_utc"].max())}
    # minutes: weighted ridge (intercept unpenalized)
    X, names, tf = _design(tr, cfg)
    y = tr["y_min"].to_numpy(dtype=float)
    lam = cfg["ridge_alpha"] * w.sum()
    A = (X * w[:, None]).T @ X + lam * np.diag([0.0] + [1.0] * (X.shape[1] - 1))
    beta = np.linalg.solve(A, (X * w[:, None]).T @ y)
    st.update(transform=tf, coef=dict(zip(names, beta.tolist())))
    mu_min = np.clip(X @ beta, *cfg["minutes_clip"])
    edges = cfg["minutes_sigma_buckets"]
    st["minutes_sigma"] = _sigma_bands(y, mu_min, w, edges)
    # rate priors: recency-weighted per-minute rate by position group (training rows)
    st["prior_rate"], st["prior_k_minutes"], st["dispersion"] = {}, {}, {}
    for t in targets:
        yt = tr[f"y_{t}"].to_numpy(dtype=float); ok = ~np.isnan(yt)
        st["prior_rate"][t] = {g: float(np.sum((w * yt)[ok & (tr["pos"] == g).to_numpy()]) /
                                        np.sum((w * tr["y_min"].to_numpy())[ok & (tr["pos"] == g).to_numpy()]))
                               for g in POS_GROUPS}
        # prior strength k (minutes) by method of moments on training player-season finals: gamma prior on the rate
        f = fin[fin["min"] >= cfg["rate_prior_min_minutes"]]
        r = f[t].to_numpy() / f["min"].to_numpy()
        mu = f["pos"].map(st["prior_rate"][t]).to_numpy()
        m = f["min"].to_numpy()
        vb = max(float(np.sum(m * ((r - mu) ** 2 - mu / m)) / m.sum()), 1e-3 * float(np.mean(mu)) ** 2)
        st["prior_k_minutes"][t] = float(np.sum(m * mu) / m.sum() / vb)
    # dispersion of the challenger and of the baseline, on training rows only
    bst = _fit_baseline(tr, cfg)
    st["baseline"] = bst
    pred_tr = _predict_means(tr, cfg, st)
    base_tr = _baseline_means(tr, cfg, bst)
    thr = cfg["negbin_overdispersion_ratio_threshold"]
    for t in targets:
        yt = tr[f"y_{t}"].to_numpy(dtype=float)
        st["dispersion"][t] = _dispersion(yt, pred_tr[t], w, thr)
        bst["dispersion"][t] = _dispersion(yt, base_tr[t], w, thr)
    bst["minutes_sigma"] = _sigma_bands(y, base_tr["min"], w, edges)
    st["state_hash"] = hashlib.sha256(json.dumps(st, sort_keys=True, default=str).encode()).hexdigest()
    return st


def _fit_baseline(tr: pd.DataFrame, cfg: dict) -> dict:
    w = tr["_w"].to_numpy()
    pop = {"min": float(np.average(tr["y_min"], weights=w))}
    for t in cfg["count_targets"].values():
        yt = tr[f"y_{t}"].to_numpy(dtype=float); ok = ~np.isnan(yt)
        pop[t] = float(np.average(yt[ok], weights=w[ok]))
    return {"population_per_appearance": pop, "dispersion": {}}


def _baseline_means(df: pd.DataFrame, cfg: dict, bst: dict) -> dict:
    k0 = cfg["baseline_pseudo_games"]
    n_cur = df["cur_n_app"].fillna(0).to_numpy(); n_prev = df["h1_n_app"].fillna(0).to_numpy()
    out = {}
    for t in ["min"] + list(cfg["count_targets"].values()):
        pop = bst["population_per_appearance"][t]
        cur = (df[f"cur_{t}"].fillna(0).to_numpy() + k0 * pop) / (n_cur + k0)
        prev = (df[f"h1_{t}"].fillna(0).to_numpy() + k0 * pop) / (n_prev + k0)
        out[t] = np.maximum(np.where(n_cur > 0, cur, np.where(n_prev > 0, prev, pop)), cfg["mean_floor"])
    return out


def _predict_means(df: pd.DataFrame, cfg: dict, st: dict) -> dict:
    X, _, _ = _design(df, cfg, st["transform"])
    beta = np.array(list(st["coef"].values()))
    out = {"min": np.clip(X @ beta, *cfg["minutes_clip"])}
    m_hist = _hist(df, cfg, "min")
    for t in cfg["count_targets"].values():
        prior = df["pos"].map(st["prior_rate"][t]).to_numpy(dtype=float)
        k = st["prior_k_minutes"][t]
        rate = (_hist(df, cfg, t) + k * prior) / (m_hist + k)
        out[t] = np.maximum(out["min"] * rate, cfg["mean_floor"])
        out[f"rate_{t}"] = rate
    return out


def predict(df: pd.DataFrame, cfg: dict, st: dict) -> dict:
    """Means for the challenger and the baseline (distributions are built from these + state in evaluation)."""
    return {"challenger": _predict_means(df, cfg, st), "baseline": _baseline_means(df, cfg, st["baseline"])}
