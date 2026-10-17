"""V2 Phase 1: annual refit + training-season recency weighting of the UNCHANGED v1 marginal architecture.

Only two things vary (modeling/player_props_v2/phase1_protocol.json, frozen before any fold was fit):
    A. the coefficient training cutoff (ANNUAL_REFIT: through F-1; ONE_SEASON_STALE: through F-2, forecast season F);
    B. the training-row weight by season: w(s) = 2 ** (-(S - s) / h), S = last training season, h in {inf (W0), 4, 2},
       normalized to mean 1 over the rows of each fit (so sum(w) = n and the ridge / L2 penalties alpha * sum(w) and
       alpha * n are the same for every policy).
Everything else is v1, reused: feature definitions and loader SQL (props_v0.load_frames / props_v0_participation.load),
the player-rate history half-life (1.0 season: a FEATURE definition, not a training weight), minutes ridge, position
priors, shrinkage k (method of moments on unweighted player-season finals, as in v1), dispersion rule, STL NB phi 0.16
(final_prelockbox_spec.json), PMF construction (props_v0.pmf_matrix / minutes_pmf), pricing, eligibility.

The weights enter exactly where v1 uses its training-row weights (props_v0.fit `_w`: minutes standardization + ridge,
minutes residual SD bands, position prior rates, dispersion, rolling-baseline population means) and the participation
log-likelihood (v1: unweighted; its standardization stays unweighted). V1REF (h = 1, the v1 weights) is a reference,
never selectable.

All seasons come from ONE common Stage 3 store, the 2025-26 lockbox store (2013-14 .. 2025-26), equivalent to the dev
store for <= 2024-25 (modeling/player_props_v2/data_provenance.json). Seasons after 2025-26 are refused by this module.

    .venv/bin/python -m cbb_platform.v2.marginal dev        development folds F2024, F2025 + weighting selection
    .venv/bin/python -m cbb_platform.v2.marginal confirm    2025-26 retrospective confirmation (exactly once)
    .venv/bin/python -m cbb_platform.v2.marginal final      V2 challenger artifact, training through 2025-26
    .venv/bin/python -m cbb_platform.v2.marginal oos        out-of-sample forecast store (no fitting)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P, props_v0_eval as E, props_v0_participation as Q
from cbb_platform import lockbox_runner as LR, props_v1_refit as R
from cbb_platform.v2 import provenance as PV

OUT_DIR = C.PROJECT_ROOT / "modeling" / "player_props_v2"
STATE_DIR = OUT_DIR / "states"
PROTOCOL_PATH = OUT_DIR / "phase1_protocol.json"
DEV_PATH = OUT_DIR / "dev_folds.json"
SELECTION_PATH = OUT_DIR / "weighting_selection.json"
CONFIRM_PATH = OUT_DIR / "retrospective_2025_26.json"
ARTIFACT_PATH = OUT_DIR / "v2_marginal_challenger_through_2025_26.json"
FINAL_RECORD_PATH = OUT_DIR / "final_refit_record.json"
OOS_DIR = C.PROJECT_ROOT / "modeling" / "v2_oos"                      # gitignored (large); manifest is tracked
OOS_PATH = OOS_DIR / "oos_forecasts_phase1.parquet"
OOS_MANIFEST_PATH = OUT_DIR / "oos_forecast_store_manifest.json"

FEATURES_DB, LABELS_DB = PV.LB_FEATURES, PV.LB_LABELS
FIRST_SEASON = 2014                                    # 2013-14, as v1
V2_MAX_SEASON = 2026                                   # 2025-26: last season with outcomes in this phase
PRODUCTION_SEASON = 2027                               # 2026-27: never read
DEV_FOLDS = (2024, 2025)
CONFIRM_SEASON = 2026
PARTICIPATION_FIRST_SEASON = 2024                      # first season with DNP_CONFIRMED labels (v1)
POLICIES = {"W0": None, "W4": 4.0, "W2": 2.0}          # selectable; None = uniform
REFERENCE = {"V1REF": 1.0}                             # v1 training weights (h = 1, unnormalized as in v1); never selectable
ARMS = {"ANNUAL_REFIT": 1, "ONE_SEASON_STALE": 2}      # train_through = F - offset
TARGETS = LR.TARGETS
VERSION = "cbb-v2-phase1-marginal-1.0.0"

FIT_CALLS: dict = {}


class V2SeasonViolation(RuntimeError):
    pass


def check_seasons(seasons) -> list[int]:
    seasons = sorted(int(s) for s in seasons)
    if seasons[0] < FIRST_SEASON or seasons[-1] > V2_MAX_SEASON:
        raise V2SeasonViolation(f"V2 Phase 1 reads {FIRST_SEASON}..{V2_MAX_SEASON} only; refused {seasons}")
    return seasons


def _count(fold: str, kind: str) -> None:
    FIT_CALLS.setdefault(fold, {}).setdefault(kind, 0)
    FIT_CALLS[fold][kind] += 1


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _connect(features_db: Path, labels_db: Path):
    con = duckdb.connect(str(features_db), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute(f"ATTACH '{labels_db}' AS lb (READ_ONLY)")
    return con


# ============================================================================ loaders (v1 SQL, V2 season guard)
def load_frames(cfg: dict, seasons, population: str = "scored", features_db: Path = FEATURES_DB,
                labels_db: Path = LABELS_DB) -> tuple[pd.DataFrame, pd.DataFrame]:
    """props_v0.load_frames, verbatim SQL, on an explicit store; seasons limited to FIRST_SEASON..V2_MAX_SEASON."""
    if population not in ("scored", "candidates"):
        raise ValueError(population)
    seasons = check_seasons(seasons)
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
    con = _connect(features_db, labels_db)
    fin_sql = f"""fin AS (SELECT canonical_player_id, season, available_at, s_min::DOUBLE AS min, n_app::DOUBLE AS n_app,
                          {P._pos_sql('last_position')} AS pos, {fin_cols}
                   FROM feat.player_box_state WHERE season BETWEEN {lo} AND {hi}
                   QUALIFY row_number() OVER (PARTITION BY canonical_player_id, season ORDER BY available_at DESC, source_game_id DESC) = 1)"""
    frame = con.execute(f"""WITH {fin_sql}
        SELECT f.player_game_example_id, f.canonical_player_id, f.canonical_team_id, f.season, f.prediction_cutoff_utc,
               f.is_home::DOUBLE AS is_home, {P._pos_sql('f.prof_last_box_position')} AS pos, f.cold_start_class,
               f.pstd_sum_min::DOUBLE AS cur_min, f.pstd_appearances::DOUBLE AS cur_n_app, {cur}, {feats}, {hist},
               l.min::DOUBLE AS y_min, {", ".join(f"l.{t}::DOUBLE AS y_{t}" for t in targets)},
               l.counting_labels_available AS counts_ok{", l.participation_status, f.canonical_game_id" if population == "candidates" else ""}
        FROM feat.player_features f JOIN lb.lab.player_game_labels l USING (player_game_example_id) {hjoin}
        WHERE f.season BETWEEN {lo} AND {hi}{"" if population == "candidates" else """ AND l.participation_status = 'PLAYED' AND l.box_row_present
          AND l.minutes_label_available"""}
        ORDER BY f.player_game_example_id""").df()
    finals = con.execute(f"WITH {fin_sql} SELECT * EXCLUDE (available_at) FROM fin ORDER BY canonical_player_id, season").df()
    con.close()
    if max(frame["season"].max(), finals["season"].max()) > V2_MAX_SEASON:
        raise V2SeasonViolation("a season after 2025-26 reached the V2 loader")
    for t in targets:                                  # count labels unavailable -> NaN, never 0
        frame.loc[~frame["counts_ok"].astype(bool), f"y_{t}"] = np.nan
    return frame, finals


def load_participation(seasons, features_db: Path = FEATURES_DB, labels_db: Path = LABELS_DB) -> pd.DataFrame:
    """props_v0_participation.load, verbatim SQL, on an explicit store (identifiable label y; UNKNOWN / NOT_ON_ROSTER NaN)."""
    seasons = check_seasons(seasons)
    sl = ", ".join(map(str, seasons))
    con = _connect(features_db, labels_db)
    df = con.execute(f"""
        SELECT f.player_game_example_id, f.canonical_game_id, f.canonical_team_id, f.canonical_player_id, f.season,
               f.prediction_cutoff_utc, l.participation_status, {", ".join(f"f.{c}::DOUBLE AS {c}" for c in Q.NUMERIC)},
               (f.pp_last_game_participation = 'PLAYED')::DOUBLE AS last_played,
               (f.pp_last_game_participation = 'DNP_CONFIRMED')::DOUBLE AS last_dnp,
               (f.pp_last_game_participation = 'UNKNOWN_PARTICIPATION')::DOUBLE AS last_unknown,
               coalesce(x.ev_prior_season_roster, FALSE)::DOUBLE AS ev_prior_season_roster,
               coalesce(x.ev_transfer_preseason, FALSE)::DOUBLE AS ev_transfer_preseason,
               coalesce(x.ev_recruit_preseason, FALSE)::DOUBLE AS ev_recruit_preseason,
               (f.cold_start_class = 'COLD_NO_HISTORY')::DOUBLE AS cold_no_history,
               (f.cold_start_class = 'COLD_TRANSFER_IN')::DOUBLE AS cold_transfer_in
        FROM feat.player_features f JOIN ex.player_game_examples x USING (player_game_example_id)
        JOIN lb.lab.player_game_labels l USING (player_game_example_id)
        WHERE f.season IN ({sl}) ORDER BY f.player_game_example_id""").df()
    con.close()
    if df["season"].max() > V2_MAX_SEASON:
        raise V2SeasonViolation("a season after 2025-26 reached the V2 participation loader")
    for c in Q.FLAGS:
        df[c] = df[c].fillna(0.0)
    df["y"] = df["participation_status"].map(Q.LABEL)
    return df


def games_of(features_db: Path = FEATURES_DB, seasons=None) -> pd.DataFrame:
    """player_game_example_id -> canonical_game_id + the pregame role field used for diagnostics."""
    seasons = check_seasons(seasons)
    con = duckdb.connect(str(features_db), read_only=True, config={"threads": C.DUCKDB_THREADS})
    out = con.execute(f"""SELECT player_game_example_id, canonical_game_id, role_l5_minutes_rank::DOUBLE AS role_l5_minutes_rank
        FROM feat.player_features WHERE season IN ({", ".join(map(str, seasons))})""").df()
    con.close()
    return out


# ============================================================================ weights
def season_weights(seasons: np.ndarray, last: int, policy: str) -> np.ndarray:
    """Training-row weights. Selectable policies are normalized to mean 1 over the fit's rows; V1REF is v1 verbatim."""
    age = last - np.asarray(seasons, dtype=float)
    if policy in REFERENCE:
        return P.recency_weights(age, REFERENCE[policy])
    h = POLICIES[policy]
    raw = np.ones(len(age)) if h is None else 2.0 ** (-age / h)
    return raw / raw.mean()


def n_eff(w: np.ndarray) -> float:
    return float(w.sum() ** 2 / np.sum(w ** 2))


# ============================================================================ fits (v1 bodies, weights injected)
def fit_v0(frame: pd.DataFrame, finals: pd.DataFrame, cfg: dict, train_through: int, policy: str, fold: str) -> dict:
    """props_v0.fit with the training-row weight replaced by `policy`; every other line is v1 (V1REF reproduces
    props_v0.fit byte-for-byte: tests/test_v2_phase1.py)."""
    _count(fold, "v0_fit")
    tr = frame[frame["season"] <= train_through].copy()
    fin = finals[finals["season"] <= train_through]
    tr["_w"] = season_weights(tr["season"].to_numpy(), train_through, policy)
    w = tr["_w"].to_numpy()
    targets = list(cfg["count_targets"].values())
    st = {"train_through": train_through, "n_train_rows": int(len(tr)),
          "train_max_cutoff": str(tr["prediction_cutoff_utc"].max())}
    X, names, tf = P._design(tr, cfg)
    y = tr["y_min"].to_numpy(dtype=float)
    lam = cfg["ridge_alpha"] * w.sum()
    A = (X * w[:, None]).T @ X + lam * np.diag([0.0] + [1.0] * (X.shape[1] - 1))
    beta = np.linalg.solve(A, (X * w[:, None]).T @ y)
    st.update(transform=tf, coef=dict(zip(names, beta.tolist())))
    mu_min = np.clip(X @ beta, *cfg["minutes_clip"])
    edges = cfg["minutes_sigma_buckets"]
    st["minutes_sigma"] = P._sigma_bands(y, mu_min, w, edges)
    st["prior_rate"], st["prior_k_minutes"], st["dispersion"] = {}, {}, {}
    for t in targets:
        yt = tr[f"y_{t}"].to_numpy(dtype=float); ok = ~np.isnan(yt)
        st["prior_rate"][t] = {g: float(np.sum((w * yt)[ok & (tr["pos"] == g).to_numpy()]) /
                                        np.sum((w * tr["y_min"].to_numpy())[ok & (tr["pos"] == g).to_numpy()]))
                               for g in P.POS_GROUPS}
        f = fin[fin["min"] >= cfg["rate_prior_min_minutes"]]
        r = f[t].to_numpy() / f["min"].to_numpy()
        mu = f["pos"].map(st["prior_rate"][t]).to_numpy()
        m = f["min"].to_numpy()
        vb = max(float(np.sum(m * ((r - mu) ** 2 - mu / m)) / m.sum()), 1e-3 * float(np.mean(mu)) ** 2)
        st["prior_k_minutes"][t] = float(np.sum(m * mu) / m.sum() / vb)
    bst = P._fit_baseline(tr, cfg)
    st["baseline"] = bst
    pred_tr = P._predict_means(tr, cfg, st)
    base_tr = P._baseline_means(tr, cfg, bst)
    thr = cfg["negbin_overdispersion_ratio_threshold"]
    for t in targets:
        yt = tr[f"y_{t}"].to_numpy(dtype=float)
        st["dispersion"][t] = P._dispersion(yt, pred_tr[t], w, thr)
        bst["dispersion"][t] = P._dispersion(yt, base_tr[t], w, thr)
    bst["minutes_sigma"] = P._sigma_bands(y, base_tr["min"], w, edges)
    st["state_hash"] = hashlib.sha256(json.dumps(st, sort_keys=True, default=str).encode()).hexdigest()
    st["v2"] = {"weighting_policy": policy, "half_life": REFERENCE.get(policy, POLICIES.get(policy)),
                "n_eff": n_eff(w), "n": int(len(w)), "sum_w": float(w.sum()),
                "rows_by_season": {str(s): int(n) for s, n in tr["season"].value_counts().sort_index().items()},
                "weight_by_season": {str(s): float(v) for s, v in tr.groupby("season")["_w"].first().items()}}
    return st


def fit_participation(rows: pd.DataFrame, train_seasons, policy: str, fold: str) -> dict | None:
    """props_v0_participation.fit with a weighted log-likelihood (W0 reproduces it exactly). None when the training
    window holds no identifiable participation label (seasons before 2023-24)."""
    seasons = [s for s in train_seasons if s >= PARTICIPATION_FIRST_SEASON]
    tr = rows[rows["season"].isin(seasons) & rows["y"].notna()]
    if not len(tr):
        return None
    _count(fold, "participation_fit")
    X, cols, tf = Q._design(tr)
    y = tr["y"].to_numpy()
    w = season_weights(tr["season"].to_numpy(), max(seasons), policy) if policy not in REFERENCE else np.ones(len(tr))
    lam = np.array([0.0] + [Q.L2_ALPHA * len(tr)] * (X.shape[1] - 1))
    beta = _irls_weighted(X, y, w, lam)
    st = {"train_seasons": list(seasons), "n_train": int(len(tr)), "n_inputs": len(Q.NUMERIC) + len(Q.FLAGS),
          "train_max_cutoff": str(tr["prediction_cutoff_utc"].max()), "base_rate": float(y.mean()),
          "transform": tf, "coef": dict(zip(cols, beta.tolist()))}
    st["state_hash"] = hashlib.sha256(json.dumps(st, sort_keys=True).encode()).hexdigest()
    st["v2"] = {"weighting_policy": policy, "n_eff": n_eff(w), "n": int(len(w)),
                "note": "V1REF participation = v1 (unweighted)" if policy in REFERENCE else "weighted log-likelihood"}
    return st


def _irls_weighted(X, y, w, lam_diag, iters=100, tol=1e-10):
    """Q._irls with observation weights; w == 1 is the identical computation."""
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = Q._sigmoid(X @ beta)
        v = np.maximum(p * (1 - p), 1e-12) * w
        H = (X * v[:, None]).T @ X + np.diag(lam_diag)
        step = np.linalg.solve(H, X.T @ (w * (y - p)) - lam_diag * beta)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta


def stl_policy(st: dict, cfg: dict) -> dict:
    """The fixed v1 STL NB phi policy (final_prelockbox_spec.json accepted change) on a fitted state."""
    return R.apply_accepted_changes(st, R.load_final_spec(), cfg)


# ============================================================================ evaluation
def role_of(rank) -> np.ndarray:
    """Pregame role diagnostic from role_l5_minutes_rank (approved pregame feature: team minutes rank over the last 5
    box rows). Diagnostic grouping only."""
    r = np.asarray(rank, dtype=float)
    return np.where(np.isnan(r), "NO_ROLE_HISTORY", np.where(r <= 5, "TOP5_MINUTES", np.where(r <= 8, "ROTATION_6_8", "BENCH_9PLUS")))


def evaluate_counts(va: pd.DataFrame, cfg: dict, st: dict) -> tuple[dict, dict]:
    """Minutes + 7 counts on the scored population of one forecast season. Returns (metrics, per-row losses)."""
    means = P.predict(va, cfg, st)["challenger"]
    names = {"min": "MIN", **{v: k for k, v in cfg["count_targets"].items()}}
    out, rows = {}, {}
    for t in ["min"] + list(cfg["count_targets"].values()):
        y = va["y_min" if t == "min" else f"y_{t}"].to_numpy(dtype=float)
        ok = ~np.isnan(y)
        mu, yy = means[t][ok], y[ok]
        pmf = LR._dist(mu, t, st, cfg)
        d = {"n": int(ok.sum()), "mae": float(np.mean(np.abs(mu - yy))), "rmse": float(np.sqrt(np.mean((mu - yy) ** 2))),
             "mean_pred": float(mu.mean()), "observed_mean": float(yy.mean()), "mean_bias": float(mu.mean() - yy.mean()),
             **E.score_distribution(pmf, yy, cfg["interval_levels"]),
             "family": "DISCRETIZED_NORMAL" if t == "min" else st["dispersion"][t]["family"],
             "phi": None if t == "min" else st["dispersion"][t]["phi"],
             "calibration": E._calibration(mu, yy, cfg["minutes_sigma_buckets"] if t == "min" else cfg["count_mean_buckets"])}
        ls, cr = LR._row_losses(pmf, yy)
        if t != "min":
            th = LR._thresholds(pmf, yy, LR.HALF_LINES[names[t]])
            d["thresholds"] = th
            d["mean_threshold_brier"] = float(np.mean([r["brier"] for r in th.values()]))
            d["mean_abs_threshold_gap"] = float(np.mean([abs(r["gap"]) for r in th.values()]))
        assert abs(float(np.mean(ls)) - d["log_score"]) <= 1e-12 and abs(float(np.mean(cr)) - d["crps"]) <= 1e-12
        out[names[t]] = d
        rows[names[t]] = {"idx": np.flatnonzero(ok), "log": ls, "crps": cr, "abs": np.abs(mu - yy), "sq": (mu - yy) ** 2}
    n = {k: out[k]["n"] for k in TARGETS}
    out["AGGREGATE_7"] = {m: float(sum(n[k] * out[k][m] for k in TARGETS) / sum(n.values()))
                          for m in ("log_score", "crps", "mean_threshold_brier")} | {"n_cells": int(sum(n.values()))}
    return out, rows


def subgroups(va: pd.DataFrame, rows: dict) -> dict:
    """Diagnostics (not gates): mean log score / CRPS by stat x pregame role and stat x position; minutes MAE likewise."""
    out = {}
    for field, lab in (("role", role_of(va["role_l5_minutes_rank"].to_numpy())), ("position", va["pos"].to_numpy())):
        g = {}
        for k, r in rows.items():
            grp = lab[r["idx"]]
            df = pd.DataFrame({"g": grp, **({"mae": r["abs"], "sq": r["sq"]} if k == "MIN" else {"log": r["log"], "crps": r["crps"]})})
            agg = df.groupby("g").agg(["mean", "size"])
            g[k] = {str(i): ({"n": int(agg.loc[i, ("mae", "size")]), "mae": float(agg.loc[i, ("mae", "mean")]),
                              "rmse": float(np.sqrt(agg.loc[i, ("sq", "mean")]))} if k == "MIN" else
                             {"n": int(agg.loc[i, ("log", "size")]), "log_score": float(agg.loc[i, ("log", "mean")]),
                              "crps": float(agg.loc[i, ("crps", "mean")])}) for i in agg.index}
        tot = {}
        for k in TARGETS:
            for i, v in g[k].items():
                a = tot.setdefault(i, {"n": 0, "log": 0.0, "crps": 0.0})
                a["n"] += v["n"]; a["log"] += v["n"] * v["log_score"]; a["crps"] += v["n"] * v["crps"]
        g["AGGREGATE_7"] = {i: {"n": a["n"], "log_score": a["log"] / a["n"], "crps": a["crps"] / a["n"]} for i, a in tot.items()}
        out[f"{field}_x_stat"] = g
    return out


def evaluate_participation(rows: pd.DataFrame, pst: dict | None) -> dict:
    lab = rows[rows["y"].notna()]
    if pst is None:
        return {"n": int(len(lab)), "status": "NOT_ESTIMABLE_NO_PRIOR_PARTICIPATION_LABELS"}
    y, p = lab["y"].to_numpy(), Q.predict(lab, pst)
    return {"n": int(len(lab)), "status": "OK", "train_seasons": pst["train_seasons"], **Q.metrics(y, p),
            "_p": p, "_y": y, "_games": lab["canonical_game_id"].to_numpy()}


def _cells(rows: dict, va_games: np.ndarray, metric: str, targets=TARGETS) -> tuple[np.ndarray, np.ndarray]:
    v = np.concatenate([rows[k][metric] for k in targets])
    g = np.concatenate([va_games[rows[k]["idx"]] for k in targets])
    return v, g


def paired(rows_a: list, rows_b: list, games: list, metric: str, targets=TARGETS) -> dict:
    """Game-clustered CR1 interval of mean(a - b) over all (row, target) cells of one or more forecast seasons."""
    d, g = [], []
    for ra, rb, gm in zip(rows_a, rows_b, games):
        va, ga = _cells(ra, gm, metric, targets)
        vb, gb = _cells(rb, gm, metric, targets)
        assert np.array_equal(ga, gb)
        d.append(va - vb); g.append(ga)
    return LR.cr1_interval(np.concatenate(d), np.concatenate(g))


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if not str(k).startswith("_")}
    return x


def save_state(name: str, st: dict) -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = STATE_DIR / f"{name}.json"
    p.write_text(json.dumps(st, indent=1, default=str) + "\n")
    return _sha(p)


def load_state(name: str) -> dict:
    return json.loads((STATE_DIR / f"{name}.json").read_text())


# ============================================================================ one forecast season
def run_fold(F: int, frame, finals, part, gm: pd.DataFrame, cfg: dict, policies) -> dict:
    """Every (arm, policy) for forecast season F on the SAME forecast-season rows / features."""
    fold = f"F{F}"
    va = frame[frame["season"] == F].reset_index(drop=True)
    va = va.merge(gm, on="player_game_example_id", how="left", validate="one_to_one")
    assert va["canonical_game_id"].notna().all()
    games = va["canonical_game_id"].to_numpy()
    prow = part[part["season"] == F]
    res = {"forecast_season": F, "n_rows": int(len(va)), "n_games": int(va["canonical_game_id"].nunique()),
           "row_identity_sha256": hashlib.sha256("\n".join(va["player_game_example_id"].astype(str)).encode()).hexdigest(),
           "candidates": {}}
    keep = {}
    for arm, off in ARMS.items():
        T = F - off
        for pol in policies:
            st = stl_policy(fit_v0(frame, finals, cfg, T, pol, fold), cfg)
            assert va["prediction_cutoff_utc"].min() > pd.Timestamp(st["train_max_cutoff"]), "no future information"
            pst = fit_participation(part, range(PARTICIPATION_FIRST_SEASON, T + 1), pol, fold)
            m, rows = evaluate_counts(va, cfg, st)
            pm = evaluate_participation(prow, pst)
            name = f"{fold}_{arm}_{pol}"
            sh = save_state(name, {"v0_state": st, "participation_state": pst})
            res["candidates"][name] = {"arm": arm, "policy": pol, "train_through": T, "state_file_sha256": sh,
                                       "v0_n_eff": st["v2"]["n_eff"], "v0_n": st["v2"]["n"],
                                       "v0_weight_by_season": st["v2"]["weight_by_season"],
                                       "participation_n_eff": pst["v2"]["n_eff"] if pst else None,
                                       "targets": m, "participation": _strip(pm), "subgroups": subgroups(va, rows)}
            keep[name] = {"rows": rows, "part": pm}
    res["_keep"], res["_games"] = keep, games
    return res


def compare(folds: list[dict], a: str, b: str) -> dict:
    """a minus b (candidate keys without the fold prefix), per fold and pooled; negative = a better."""
    out = {"per_fold": {}, "pooled": {}}
    ra, rb, gs = [], [], []
    for f in folds:
        fa, fb = f"F{f['forecast_season']}_{a}", f"F{f['forecast_season']}_{b}"
        ka, kb = f["_keep"][fa]["rows"], f["_keep"][fb]["rows"]
        out["per_fold"][str(f["forecast_season"])] = {
            "agg_log_score": paired([ka], [kb], [f["_games"]], "log"), "agg_crps": paired([ka], [kb], [f["_games"]], "crps"),
            "minutes_abs": paired([ka], [kb], [f["_games"]], "abs", ("MIN",)),
            "minutes_sq": paired([ka], [kb], [f["_games"]], "sq", ("MIN",)),
            "by_target_log_score": {t: f["candidates"][fa]["targets"][t]["log_score"] - f["candidates"][fb]["targets"][t]["log_score"] for t in TARGETS},
            "by_target_crps": {t: f["candidates"][fa]["targets"][t]["crps"] - f["candidates"][fb]["targets"][t]["crps"] for t in TARGETS}}
        pa, pb = f["_keep"][fa]["part"], f["_keep"][fb]["part"]
        if pa.get("status") == "OK" and pb.get("status") == "OK":
            y = pa["_y"]
            la = -(y * np.log(np.clip(pa["_p"], 1e-15, 1)) + (1 - y) * np.log(np.clip(1 - pa["_p"], 1e-15, 1)))
            lb = -(y * np.log(np.clip(pb["_p"], 1e-15, 1)) + (1 - y) * np.log(np.clip(1 - pb["_p"], 1e-15, 1)))
            out["per_fold"][str(f["forecast_season"])]["participation"] = {
                "brier": LR.cr1_interval((pa["_p"] - y) ** 2 - (pb["_p"] - y) ** 2, pa["_games"]),
                "log_loss": LR.cr1_interval(la - lb, pa["_games"])}
        ra.append(ka); rb.append(kb); gs.append(f["_games"])
    out["pooled"] = {"agg_log_score": paired(ra, rb, gs, "log"), "agg_crps": paired(ra, rb, gs, "crps"),
                     "minutes_abs": paired(ra, rb, gs, "abs", ("MIN",)), "minutes_sq": paired(ra, rb, gs, "sq", ("MIN",))}
    return out


# ============================================================================ selection (protocol section "selection")
def _clearly_worse(cmp: dict, key: str, folds) -> bool:
    """candidate minus best: pooled CR1 95 % interval entirely above 0 AND positive in every dev fold."""
    p = cmp["pooled"][key]
    return p["ci95"][0] > 0 and all(cmp["per_fold"][str(F)][key]["mean_difference"] > 0 for F in folds)


def _pooled(folds, key_fn) -> dict:
    """row-weighted pooled value per policy over the dev folds, annual arm."""
    out = {}
    for pol in POLICIES:
        num = den = 0.0
        for f in folds:
            v, n = key_fn(f["candidates"][f"F{f['forecast_season']}_ANNUAL_REFIT_{pol}"])
            num += v * n; den += n
        out[pol] = num / den
    return out


def select_policy(folds: list[dict]) -> dict:
    """The frozen hierarchy: 1 aggregate log score, 2 aggregate CRPS, 3 per-target stability, 4 participation,
    5 minutes MAE / RMSE, 6 calibration; tie-break: the largest effective sample size (W0, then W4, then W2)."""
    Fs = [f["forecast_season"] for f in folds]
    agg = lambda m: (lambda c: (c["targets"]["AGGREGATE_7"][m], c["targets"]["AGGREGATE_7"]["n_cells"]))
    steps, tied = [], list(POLICIES)

    def clear_step(name, values, key):
        nonlocal tied
        best = min(tied, key=lambda p: values[p])
        worse = {p: _clearly_worse(compare(folds, f"ANNUAL_REFIT_{p}", f"ANNUAL_REFIT_{best}"), key, Fs) for p in tied if p != best}
        steps.append({"step": name, "pooled_values": {p: values[p] for p in tied}, "best": best,
                      "clearly_worse_than_best": worse, "remaining": [best] + [p for p in tied if p != best and not worse[p]]})
        tied = steps[-1]["remaining"]

    def point_step(name, values, lower_better=True):
        nonlocal tied
        vals = {p: values[p] for p in tied}
        best_v = min(vals.values()) if lower_better else max(vals.values())
        rem = [p for p in tied if vals[p] == best_v]
        steps.append({"step": name, "values": vals, "remaining": rem})
        tied = rem

    clear_step("1_aggregate_log_score", _pooled(folds, agg("log_score")), "agg_log_score")
    if len(tied) > 1:
        clear_step("2_aggregate_crps", _pooled(folds, agg("crps")), "agg_crps")
    if len(tied) > 1:
        wins = {p: 0 for p in tied}
        for f in folds:
            for t in TARGETS:
                vals = {p: f["candidates"][f"F{f['forecast_season']}_ANNUAL_REFIT_{p}"]["targets"][t]["log_score"] for p in tied}
                wins[min(vals, key=vals.get)] += 1
        point_step("3_target_stability_log_score_wins", wins, lower_better=False)
    if len(tied) > 1:
        part = {}
        for p in tied:
            vals = [f["candidates"][f"F{f['forecast_season']}_ANNUAL_REFIT_{p}"]["participation"] for f in folds]
            vals = [v for v in vals if v.get("status") == "OK"]
            part[p] = (round(sum(v["brier"] * v["n"] for v in vals) / sum(v["n"] for v in vals), 12),
                       round(sum(v["log_loss"] * v["n"] for v in vals) / sum(v["n"] for v in vals), 12)) if vals else (0.0, 0.0)
        point_step("4_participation_brier_then_log_loss", part)
    if len(tied) > 1:
        clear_step("5a_minutes_mae", _pooled(folds, lambda c: (c["targets"]["MIN"]["mae"], c["targets"]["MIN"]["n"])), "minutes_abs")
    if len(tied) > 1:
        clear_step("5b_minutes_rmse_sq", _pooled(folds, lambda c: (c["targets"]["MIN"]["rmse"] ** 2, c["targets"]["MIN"]["n"])), "minutes_sq")
    if len(tied) > 1:
        cal = _pooled(folds, lambda c: (float(np.mean([c["targets"][t]["mean_abs_threshold_gap"] for t in TARGETS])), 1))
        point_step("6_calibration_mean_abs_threshold_gap", {p: round(v, 12) for p, v in cal.items()})
    tie_break = None
    if len(tied) > 1:
        order = ["W0", "W4", "W2"]
        tie_break = {"rule": "largest effective sample size", "tied": tied}
        tied = [min(tied, key=order.index)]
    sel = tied[0]
    return {"SELECTED_WEIGHTING_POLICY": sel, "SELECTED_HALF_LIFE": "UNIFORM" if POLICIES[sel] is None else POLICIES[sel],
            "steps": steps, "tie_break": tie_break, "selection_folds": Fs, "selection_arm": "ANNUAL_REFIT",
            "decided_at_step": steps[-1]["step"] if not tie_break else "TIE_BREAK"}


# ============================================================================ git evidence
def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=C.PROJECT_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def committed_unchanged(path: Path) -> str:
    """The commit that holds `path` exactly as on disk; refuse when it is untracked or modified."""
    rel = str(path.relative_to(C.PROJECT_ROOT))
    if _git("status", "--porcelain", "--", rel):
        raise RuntimeError(f"{rel} must be committed unchanged before this stage")
    return _git("log", "-1", "--format=%H", "--", rel)


# ============================================================================ stages
def _strip_fold(f: dict) -> dict:
    return {k: _strip(v) for k, v in f.items() if not k.startswith("_")}


def stage_dev() -> dict:
    committed_unchanged(PROTOCOL_PATH)
    cfg = P.load_config()
    frame, finals = load_frames(cfg, range(FIRST_SEASON, max(DEV_FOLDS) + 1))       # 2025-26 is not loaded here
    part = load_participation(range(PARTICIPATION_FIRST_SEASON, max(DEV_FOLDS) + 1))
    gm = games_of(seasons=DEV_FOLDS)
    pols = list(POLICIES) + list(REFERENCE)
    folds = [run_fold(F, frame, finals, part, gm, cfg, pols) for F in DEV_FOLDS]
    comparisons = {}
    for F_res in folds:
        F = F_res["forecast_season"]
        comparisons[f"F{F}_annual_minus_stale"] = {p: compare([F_res], f"ANNUAL_REFIT_{p}", f"ONE_SEASON_STALE_{p}")["per_fold"][str(F)]
                                                   for p in pols}
    comparisons["pooled_policy_vs_W0_annual"] = {p: compare(folds, f"ANNUAL_REFIT_{p}", "ANNUAL_REFIT_W0") for p in pols if p != "W0"}
    comparisons["pooled_policy_vs_V1REF_annual"] = {p: compare(folds, f"ANNUAL_REFIT_{p}", "ANNUAL_REFIT_V1REF") for p in POLICIES}
    comparisons["pooled_annual_minus_stale"] = {p: compare(folds, f"ANNUAL_REFIT_{p}", f"ONE_SEASON_STALE_{p}")["pooled"] for p in pols}
    sel = select_policy(folds)
    out = {"version": VERSION, "stage": "DEV", "protocol_sha256": _sha(PROTOCOL_PATH), "seasons_loaded": [FIRST_SEASON, max(DEV_FOLDS)],
           "season_2025_26_loaded": False, "folds": [_strip_fold(f) for f in folds], "comparisons": comparisons,
           "fit_calls": FIT_CALLS, "selection": sel}
    DEV_PATH.write_text(json.dumps(out, indent=1, default=str) + "\n")
    SELECTION_PATH.write_text(json.dumps({
        "record": "V2 Phase 1 weighting-policy selection (development folds only; recorded before any 2025-26 evaluation)",
        "POLICY_SELECTED_BEFORE_2025_26": True, **sel, "dev_folds_sha256": _sha(DEV_PATH), "protocol_sha256": _sha(PROTOCOL_PATH),
        "season_2025_26_loaded": False}, indent=1, default=str) + "\n")
    return out


def stage_confirm() -> dict:
    if CONFIRM_PATH.exists():
        raise RuntimeError("the 2025-26 retrospective confirmation runs exactly once; its record already exists")
    sel_commit = committed_unchanged(SELECTION_PATH)
    sel = json.loads(SELECTION_PATH.read_text())
    pol = sel["SELECTED_WEIGHTING_POLICY"]
    cfg = P.load_config()
    frame, finals = load_frames(cfg, range(FIRST_SEASON, CONFIRM_SEASON + 1))
    part = load_participation(range(PARTICIPATION_FIRST_SEASON, CONFIRM_SEASON + 1))
    gm = games_of(seasons=(CONFIRM_SEASON,))
    f = run_fold(CONFIRM_SEASON, frame, finals, part, gm, cfg, [pol, "V1REF"])
    cmp = {"selected_annual_minus_stale": compare([f], f"ANNUAL_REFIT_{pol}", f"ONE_SEASON_STALE_{pol}")["per_fold"][str(CONFIRM_SEASON)],
           "V1REF_annual_minus_stale": compare([f], "ANNUAL_REFIT_V1REF", "ONE_SEASON_STALE_V1REF")["per_fold"][str(CONFIRM_SEASON)],
           "selected_annual_minus_V1REF_annual": compare([f], f"ANNUAL_REFIT_{pol}", "ANNUAL_REFIT_V1REF")["per_fold"][str(CONFIRM_SEASON)]}
    out = {"version": VERSION, "stage": "RETROSPECTIVE_CONFIRMATION_2025_26",
           "nature": "retrospective confirmation; not a lockbox, not a tuning set; the policy is not changed by this result",
           "selected_policy": pol, "selection_record_commit": sel_commit, "selection_record_sha256": _sha(SELECTION_PATH),
           "fold": _strip_fold(f), "comparisons": cmp, "fit_calls": FIT_CALLS}
    CONFIRM_PATH.write_text(json.dumps(out, indent=1, default=str) + "\n")
    return out


def stage_final() -> dict:
    committed_unchanged(CONFIRM_PATH)
    sel = json.loads(SELECTION_PATH.read_text())
    pol = sel["SELECTED_WEIGHTING_POLICY"]
    cfg = P.load_config()
    frame, finals = load_frames(cfg, range(FIRST_SEASON, V2_MAX_SEASON + 1))
    part = load_participation(range(PARTICIPATION_FIRST_SEASON, V2_MAX_SEASON + 1))
    v0 = stl_policy(fit_v0(frame, finals, cfg, V2_MAX_SEASON, pol, "FINAL"), cfg)
    pst = fit_participation(part, range(PARTICIPATION_FIRST_SEASON, V2_MAX_SEASON + 1), pol, "FINAL")
    spec = R.load_final_spec()
    art = {"artifact_version": "cbb-props-v2-marginal-challenger-through-2025-26",
           "role": "V2_CHALLENGER (not promoted; the v1 champion cbb-props-v1-prelockbox-2024-25 is unchanged)",
           "phase": VERSION, "SUPERVISED_TRAINING_THROUGH": V2_MAX_SEASON, "HISTORICAL_STATE_THROUGH": V2_MAX_SEASON,
           "PRODUCTION_SEASON": PRODUCTION_SEASON,
           "weighting_policy": pol, "half_life": sel["SELECTED_HALF_LIFE"],
           "weight_rule": "w(s) = 2 ** (-(S - s) / h), S = 2026, normalized to mean 1 over each fit's rows",
           "datasets": {"stage3_store": str(FEATURES_DB.relative_to(C.PROJECT_ROOT)),
                        "dev_dataset_version_le_2024_25": "cbb_data_v2.4+5f19d31e524a",
                        "lockbox_dataset_version_2025_26": "cbb_data_v3.0+86a63cb6d902",
                        "provenance": "modeling/player_props_v2/data_provenance.json"},
           "stat_training_seasons": [FIRST_SEASON, V2_MAX_SEASON], "participation_training_seasons": pst["train_seasons"],
           "spec_fingerprint": R.spec_fingerprint(cfg), "final_spec_fingerprint": R.final_spec_fingerprint(spec),
           "stl_policy": spec["accepted_changes"]["STL"], "config": cfg, "v0_state": v0, "participation_state": pst,
           "coef_order": {"v0": list(v0["coef"]), "participation": list(pst["coef"])},
           "selection_record_sha256": _sha(SELECTION_PATH), "confirmation_record_sha256": _sha(CONFIRM_PATH)}
    art["artifact_fingerprint"] = R.fingerprint(art)
    ARTIFACT_PATH.write_text(R.dumps(art))
    rec = {"artifact": str(ARTIFACT_PATH.relative_to(C.PROJECT_ROOT)), "sha256": _sha(ARTIFACT_PATH),
           "artifact_fingerprint": art["artifact_fingerprint"], "fit_calls": FIT_CALLS,
           "v0_n_eff": v0["v2"]["n_eff"], "v0_n": v0["v2"]["n"], "participation_n_eff": pst["v2"]["n_eff"],
           "participation_n": pst["v2"]["n"], "v1_artifact_sha256": _sha(PV.V1_ARTIFACT)}
    FINAL_RECORD_PATH.write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def stage_oos() -> dict:
    """Pregame-candidate forecasts for every forecast season, from the SAVED annual-refit states of the selected policy
    (trained only on earlier seasons). No model is fit here."""
    sel = json.loads(SELECTION_PATH.read_text())
    pol = sel["SELECTED_WEIGHTING_POLICY"]
    cfg = P.load_config()
    names = {"MIN": "min", **cfg["count_targets"]}
    parts = []
    for F in DEV_FOLDS + (CONFIRM_SEASON,):
        name = f"F{F}_ANNUAL_REFIT_{pol}"
        s = load_state(name)
        st, pst = s["v0_state"], s["participation_state"]
        cand, _ = load_frames(cfg, range(F - cfg["recency"]["prior_seasons_used"], F + 1), population="candidates")
        cand = cand[cand["season"] == F].reset_index(drop=True)
        prow = load_participation((F,))
        cand = cand.merge(prow[["player_game_example_id"] + [c for c in Q.NUMERIC + Q.FLAGS if c not in cand.columns]],
                          on="player_game_example_id", how="left", validate="one_to_one")
        gm = games_of(seasons=(F,))[["player_game_example_id", "role_l5_minutes_rank"]]
        cand = cand.merge(gm, on="player_game_example_id", how="left", validate="one_to_one")
        mu = P.predict(cand, cfg, st)["challenger"]
        sig = P._band_sigma(mu["min"], cfg["minutes_sigma_buckets"], st["minutes_sigma"])
        out = pd.DataFrame({
            "fold_id": f"F{F}", "forecast_season": F, "game_id": cand["canonical_game_id"], "team_id": cand["canonical_team_id"],
            "player_id": cand["canonical_player_id"], "player_game_example_id": cand["player_game_example_id"],
            "prediction_cutoff_utc": cand["prediction_cutoff_utc"], "is_home": cand["is_home"], "position_group": cand["pos"],
            "cold_start_class": cand["cold_start_class"], "role_l5_minutes_rank": cand["role_l5_minutes_rank"],
            "role_diagnostic": role_of(cand["role_l5_minutes_rank"].to_numpy()),
            "p_play": Q.predict(cand, pst) if pst else np.nan,
            "p_play_status": "OK" if pst else "NOT_ESTIMABLE_NO_PRIOR_PARTICIPATION_LABELS",
            "min_family": "DISCRETIZED_NORMAL", "min_mu": mu["min"], "min_sigma": sig, "min_support_max": cfg["minutes_support_max"]})
        for k, t in cfg["count_targets"].items():
            out[f"{k}_family"] = st["dispersion"][t]["family"]
            out[f"{k}_mu"] = mu[t]
            out[f"{k}_phi"] = float(st["dispersion"][t]["phi"])
        out["pmf_support_max"] = cfg["pmf_support_max"]
        out["participation_status"] = cand["participation_status"]
        out["y_min"] = cand["y_min"]
        for k, t in cfg["count_targets"].items():
            out[f"y_{k}"] = cand[f"y_{t}"]
        out["model_version"] = VERSION
        out["data_version"] = "stage3-lockbox-store(cbb_data_v2.4+5f19d31e524a<=2025; cbb_data_v3.0+86a63cb6d902 2026)"
        out["training_through_season"] = st["train_through"]
        out["participation_training_seasons"] = json.dumps(pst["train_seasons"]) if pst else "[]"
        out["weighting_policy"] = pol
        out["state_name"] = name
        out["state_hash"] = st["state_hash"]
        assert (out["prediction_cutoff_utc"] > pd.Timestamp(st["train_max_cutoff"])).all()
        parts.append(out)
    df = pd.concat(parts, ignore_index=True)
    OOS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OOS_PATH, index=False)
    man = {"store": str(OOS_PATH.relative_to(C.PROJECT_ROOT)), "sha256": _sha(OOS_PATH), "rows": int(len(df)),
           "games": int(df["game_id"].nunique()), "rows_by_fold": df.groupby("fold_id").size().to_dict(),
           "games_by_fold": df.groupby("fold_id")["game_id"].nunique().to_dict(),
           "played_rows_by_fold": df[df["participation_status"] == "PLAYED"].groupby("fold_id").size().to_dict(),
           "weighting_policy": pol, "population": "every pregame player candidate of the forecast season (all participation statuses)",
           "forecast_source": "annual-refit states trained only on seasons < forecast season (modeling/player_props_v2/states)",
           "pmf_identity": "counts: props_v0.pmf_matrix(mu, phi, pmf_support_max) (phi = 0 Poisson; STL phi 0.16); minutes: "
                           "props_v0.minutes_pmf(mu, sigma, min_support_max). Distributions are conditional on playing.",
           "outcomes": "y_* = target-game labels (NaN when unavailable); attached after forecasting, never used to forecast",
           "in_sample_residuals_used": False, "columns": list(df.columns),
           "state_files_sha256": {f"F{F}": _sha(STATE_DIR / f"F{F}_ANNUAL_REFIT_{pol}.json") for F in DEV_FOLDS + (CONFIRM_SEASON,)}}
    OOS_MANIFEST_PATH.write_text(json.dumps(man, indent=1, default=str) + "\n")
    return man


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"dev": stage_dev, "confirm": stage_confirm, "final": stage_final, "oos": stage_oos}.get(stage)
    if fn is None:
        raise SystemExit("usage: python -m cbb_platform.v2.marginal dev|confirm|final|oos")
    r = fn()
    print(json.dumps({k: r[k] for k in ("selection", "fit_calls", "sha256", "rows", "games") if k in r}, indent=1, default=str))
