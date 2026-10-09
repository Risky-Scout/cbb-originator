"""Player-prop v0 participation layer: P(player appears in the game), plus the wrapper that keeps P(play) and the
UNCHANGED Challenger v0 conditional-on-appearance outputs side by side.

Labels (lab.player_game_labels, target game): PLAYED = 1, DNP_CONFIRMED = 0. UNKNOWN_PARTICIPATION and NOT_ON_ROSTER
(an end-of-season roster label, not a confirmed DNP) are excluded, never converted. DNP_CONFIRMED exists only from
2023-24 (complete substitution feeds; cbb_platform/stage3/participation.py), so identifiable history through 2023-24 is
the 2023-24 season; 2024-25 is the validation season. 2025-26 is never read.

    model      L2-regularized logistic regression (numpy IRLS; no sklearn in the environment), 23 approved pregame
               inputs + missing indicators, standardized on training rows
    benchmark  season participation rate over identifiable prior games, last game counted triple, shrunk toward the
               training base rate (fixed constants)
    wrapper    participation_probability | conditional_minutes | conditional stat distributions (v0, unchanged). DNP
               mass is NOT folded into the stat distributions (a book may void the prop when the player does not play)

    .venv/bin/python -m cbb_platform.props_v0_participation     -> modeling/player_props_v0/participation_metrics.json
"""
from __future__ import annotations

import hashlib
import json

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, lockbox, props_v0 as P
from cbb_platform.stage3 import spec

TRAIN_SEASONS = (2024,)                 # 2023-24: first season with confirmed DNP labels
VALIDATE_SEASON = 2025                  # 2024-25
LABEL = {"PLAYED": 1.0, "DNP_CONFIRMED": 0.0}
NUMERIC = ["pp_participation_rate", "pp_played", "pp_dnp_confirmed", "pp_unknown", "pp_l3_played", "pp_l5_played",
           "pp_games_since_played", "pr_ewm_min_per_app", "pr_last_box_minutes", "pr_ewm_start_rate", "pstd_min_per_app",
           "pstd_appearances", "pps_min_per_app", "role_l5_minutes_rank", "role_team_rotation_depth_l5"]
LOG1P = {"pp_played", "pp_dnp_confirmed", "pp_unknown", "pp_games_since_played", "pstd_appearances"}
FLAGS = ["last_played", "last_dnp", "last_unknown", "ev_prior_season_roster", "ev_transfer_preseason",
         "ev_recruit_preseason", "cold_no_history", "cold_transfer_in"]
L2_ALPHA = 1e-4                         # penalty = alpha * n on standardized coefficients (intercept free); fixed
HEUR_K, HEUR_LAST_W = 3.0, 2.0          # benchmark shrinkage pseudo-games / extra weight of the last game; fixed
BUCKETS = [0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0000001]
OUT = P.CONFIG_PATH.parent / "participation_metrics.json"
DEMO_GAMES = 3


def load(seasons=TRAIN_SEASONS + (VALIDATE_SEASON,)) -> tuple[pd.DataFrame, dict]:
    """Pregame candidate rows with appearance labels; returns (frame of identifiable rows, counts by season/status)."""
    for s in seasons:
        lockbox.check_season(s)
        if s not in spec.MODEL_SEASONS:
            raise ValueError(f"season {s} outside the accepted Stage 3 window")
    sl = ", ".join(map(str, sorted(seasons)))
    con = duckdb.connect(str(spec.FEATURES_DB), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute(f"ATTACH '{spec.LABELS_DB}' AS lb (READ_ONLY)")
    df = con.execute(f"""
        SELECT f.player_game_example_id, f.canonical_game_id, f.canonical_team_id, f.canonical_player_id, f.season,
               f.prediction_cutoff_utc, l.participation_status, {", ".join(f"f.{c}::DOUBLE AS {c}" for c in NUMERIC)},
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
    if df["season"].max() >= lockbox.LOCKBOX_SEASON:
        raise lockbox.LockboxViolation("2025-26 row reached the participation model")
    for c in FLAGS:
        df[c] = df[c].fillna(0.0)
    counts = {str(s): {k: int(v) for k, v in g["participation_status"].value_counts().sort_index().items()}
              for s, g in df.groupby("season")}
    df["y"] = df["participation_status"].map(LABEL)            # UNKNOWN / NOT_ON_ROSTER -> NaN, never 0
    return df, counts


# ============================================================================ model
def _design(df: pd.DataFrame, st: dict | None = None):
    fit = st is None
    st = st if st is not None else {"mean": {}, "sd": {}, "ind": []}
    cols, mats = ["intercept"], [np.ones(len(df))]
    for c in NUMERIC:
        x = df[c].to_numpy(dtype=float)
        if c in LOG1P:
            x = np.log1p(np.clip(x, 0, None))
        if fit:
            ok = ~np.isnan(x)
            st["mean"][c] = float(x[ok].mean()) if ok.any() else 0.0
            sd = float(x[ok].std()) if ok.any() else 1.0
            st["sd"][c] = sd if sd > 0 else 1.0
            if (~ok).any():
                st["ind"].append(c)
        mats.append(np.nan_to_num((x - st["mean"][c]) / st["sd"][c], nan=0.0)); cols.append(c)
        if c in st["ind"]:
            mats.append(np.isnan(x).astype(float)); cols.append(f"{c}__missing")
    for c in FLAGS:
        mats.append(df[c].to_numpy(dtype=float)); cols.append(c)
    return np.column_stack(mats), cols, st


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def _irls(X, y, lam_diag, iters=100, tol=1e-10):
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = _sigmoid(X @ beta)
        w = np.maximum(p * (1 - p), 1e-12)
        H = (X * w[:, None]).T @ X + np.diag(lam_diag)
        step = np.linalg.solve(H, X.T @ (y - p) - lam_diag * beta)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta


def fit(df: pd.DataFrame, train_seasons=TRAIN_SEASONS) -> dict:
    """All state from identifiable rows of the training seasons only."""
    tr = df[df["season"].isin(train_seasons) & df["y"].notna()]
    X, cols, tf = _design(tr)
    y = tr["y"].to_numpy()
    lam = np.array([0.0] + [L2_ALPHA * len(tr)] * (X.shape[1] - 1))
    beta = _irls(X, y, lam)
    st = {"train_seasons": list(train_seasons), "n_train": int(len(tr)), "n_inputs": len(NUMERIC) + len(FLAGS),
          "train_max_cutoff": str(tr["prediction_cutoff_utc"].max()), "base_rate": float(y.mean()),
          "transform": tf, "coef": dict(zip(cols, beta.tolist()))}
    st["state_hash"] = hashlib.sha256(json.dumps(st, sort_keys=True).encode()).hexdigest()
    return st


def predict(df: pd.DataFrame, st: dict) -> np.ndarray:
    X, _, _ = _design(df, st["transform"])
    return _sigmoid(X @ np.array(list(st["coef"].values())))


def heuristic(df: pd.DataFrame, st: dict) -> np.ndarray:
    played = df["pp_played"].fillna(0).to_numpy(); dnp = df["pp_dnp_confirmed"].fillna(0).to_numpy()
    last_p = df["last_played"].to_numpy(); last_known = last_p + df["last_dnp"].to_numpy()
    return (played + HEUR_LAST_W * last_p + HEUR_K * st["base_rate"]) / (played + dnp + HEUR_LAST_W * last_known + HEUR_K)


# ============================================================================ metrics
def _auc(y, p):
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)       # average ranks for ties
    ranks = (np.bincount(inv, weights=ranks) / cnt)[inv]
    n1 = y.sum(); n0 = len(y) - n1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _recalibration(y, p):
    lp = np.log(np.clip(p, 1e-12, 1 - 1e-12) / np.clip(1 - p, 1e-12, 1))
    a = 0.0                                                     # calibration-in-the-large: intercept with offset logit(p)
    for _ in range(100):
        q = _sigmoid(a + lp); step = np.sum(y - q) / max(np.sum(q * (1 - q)), 1e-12); a += step
        if abs(step) < 1e-12:
            break
    ab = _irls(np.column_stack([np.ones(len(lp)), lp]), y, np.zeros(2))   # slope: y ~ a + b logit(p)
    return float(a), float(ab[1])


def metrics(y, p) -> dict:
    pc = np.clip(p, 1e-15, 1 - 1e-15)
    a, b = _recalibration(y, p)
    buckets = []
    for lo, hi in zip(BUCKETS[:-1], BUCKETS[1:]):
        m = (p >= lo) & (p < hi)
        buckets.append({"bucket": f"{int(lo * 100)}-{min(int(round(hi * 100)), 100)}%", "n": int(m.sum()),
                        "mean_pred": round(float(p[m].mean()), 4) if m.any() else None,
                        "obs_rate": round(float(y[m].mean()), 4) if m.any() else None})
    return {"n": int(len(y)), "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))), "auc": _auc(y, p),
            "calibration_in_the_large": a, "calibration_slope": b, "mean_pred": float(p.mean()),
            "obs_rate": float(y.mean()), "buckets": buckets}


# ============================================================================ wrapper (v0 conditional models unchanged)
def pregame_outputs(cand: pd.DataFrame, part_rows: pd.DataFrame, cfg: dict, v0_state: dict, p_state: dict) -> dict:
    """Separate quantities per pregame candidate: participation_probability, conditional_minutes and the conditional
    stat distributions (pmf rows over 0..support; last point = P(Y >= support)). Nothing is mixed with DNP mass."""
    extra = [c for c in NUMERIC + FLAGS if c not in cand.columns]          # shared columns: same feature-store source
    cand = cand.merge(part_rows[["player_game_example_id"] + extra], on="player_game_example_id", how="left",
                      validate="one_to_one")
    means = P.predict(cand, cfg, v0_state)["challenger"]
    sig = P._band_sigma(means["min"], cfg["minutes_sigma_buckets"], v0_state["minutes_sigma"])
    dists = {"MIN": P.minutes_pmf(means["min"], sig, cfg["minutes_support_max"])}
    for name, t in cfg["count_targets"].items():
        dists[name] = P.pmf_matrix(means[t], v0_state["dispersion"][t]["phi"], cfg["pmf_support_max"])
    table = pd.DataFrame({"player_game_example_id": cand["player_game_example_id"],
                          "canonical_game_id": cand["canonical_game_id"], "canonical_team_id": cand["canonical_team_id"],
                          "canonical_player_id": cand["canonical_player_id"],
                          "participation_probability": predict(cand, p_state), "conditional_minutes": means["min"],
                          **{f"{n}_mean_given_play": means[t] for n, t in cfg["count_targets"].items()}})
    return {"table": table, "conditional_stat_distribution": dists}


def _names(table: pd.DataFrame) -> pd.DataFrame:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    con.register("t", table)
    out = con.execute("""SELECT g.game_date_et::VARCHAR || ' ' || ah.display_name || ' @ ' || hh.display_name AS game,
            tm.display_name AS team, pl.display_name AS player, t.* EXCLUDE (canonical_game_id, canonical_team_id, canonical_player_id)
        FROM t JOIN core.games g USING (canonical_game_id) JOIN core.teams hh ON hh.canonical_team_id = g.home_team_id
        JOIN core.teams ah ON ah.canonical_team_id = g.away_team_id JOIN core.teams tm ON tm.canonical_team_id = t.canonical_team_id
        LEFT JOIN core.players pl ON pl.canonical_player_id = t.canonical_player_id
        ORDER BY game, team, t.participation_probability DESC, t.conditional_minutes DESC""").df()
    con.close()
    return out


def run(write: bool = True) -> dict:
    df, counts = load()
    st = fit(df)
    va = df[(df["season"] == VALIDATE_SEASON) & df["y"].notna()]
    assert va["prediction_cutoff_utc"].min() > pd.Timestamp(st["train_max_cutoff"])
    y = va["y"].to_numpy()
    p_model, p_heur = predict(va, st), heuristic(va, st)
    tr = df[df["season"].isin(TRAIN_SEASONS)]
    out = {"version": "props-v0-participation-1.0.0", "upstream_dataset": P.load_config()["upstream_dataset"],
           "lockbox_touched": False, "labels": "PLAYED=1, DNP_CONFIRMED=0; UNKNOWN_PARTICIPATION and NOT_ON_ROSTER excluded",
           "train_seasons": list(TRAIN_SEASONS), "validate_season": VALIDATE_SEASON, "label_counts_by_season": counts,
           "train_rows": {"played": int((tr["y"] == 1).sum()), "dnp": int((tr["y"] == 0).sum()),
                          "unknown_excluded": int((tr["participation_status"] == "UNKNOWN_PARTICIPATION").sum()),
                          "not_on_roster_excluded": int((tr["participation_status"] == "NOT_ON_ROSTER").sum())},
           "validation_rows": {"played": int((y == 1).sum()), "dnp": int((y == 0).sum()),
                               "unknown_excluded": counts[str(VALIDATE_SEASON)].get("UNKNOWN_PARTICIPATION", 0),
                               "not_on_roster_excluded": counts[str(VALIDATE_SEASON)].get("NOT_ON_ROSTER", 0)},
           "benchmark": metrics(y, p_heur), "challenger": metrics(y, p_model),
           "model_state": {k: st[k] for k in ("n_train", "n_inputs", "base_rate", "coef", "state_hash")},
           "benchmark_rule": f"(played + {HEUR_LAST_W} * last_played + {HEUR_K} * base_rate) / "
                             f"(played + dnp_confirmed + {HEUR_LAST_W} * last_known + {HEUR_K}); season-to-date prior games"}
    # end-to-end: fixed sample of 2024-25 games, v0 state of evaluation B (train <= 2023-24), unchanged
    cfg = P.load_config()
    frame, finals, _ = P.load_frames(cfg)
    v0_state = P.fit(frame, finals, cfg, cfg["evaluations"][-1]["train_through"])
    cand, _, _ = P.load_frames(cfg, seasons=[VALIDATE_SEASON - 3, VALIDATE_SEASON - 2, VALIDATE_SEASON - 1, VALIDATE_SEASON],
                               population="candidates")
    cand = cand[cand["season"] == VALIDATE_SEASON]
    games = sorted(cand["canonical_game_id"].unique())[:DEMO_GAMES]
    res = pregame_outputs(cand[cand["canonical_game_id"].isin(games)], df, cfg, v0_state, st)
    demo = _names(res["table"])
    out["end_to_end_sample"] = {"games": games, "v0_state_hash": v0_state["state_hash"], "rows": json.loads(
        demo.drop(columns=["player_game_example_id"]).round(3).to_json(orient="records"))}
    out["output_hash"] = hashlib.sha256(json.dumps([out["challenger"], out["benchmark"], out["end_to_end_sample"]],
                                                   sort_keys=True).encode()).hexdigest()
    if write:
        OUT.write_text(json.dumps(out, indent=1) + "\n")
    return out


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: r[k] for k in ("train_rows", "validation_rows")}, indent=1))
    for k in ("benchmark", "challenger"):
        m = r[k]
        print(k, {x: round(m[x], 4) for x in ("brier", "log_loss", "auc", "calibration_in_the_large", "calibration_slope")})
        print("  ", m["buckets"])
