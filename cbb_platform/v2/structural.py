"""V2 Phase 3: structural interdependence layer over the Phase 1B marginals (research; game-level box-score model).

Chain per simulated game draw d (all quantities pregame-legal; realized outcomes are training labels only):

    duration      D_d = 40 + 5 K_d, K_d ~ training overtime distribution; ONE duration per draw for both teams
    environment   expected pace per 40 (shared by both teams) and expected offensive efficiency per team from a small
                  OLS on prior-game team pace / efficiency (shrunk to prior season / league), home / away; shared pace
                  shock eps_g ~ N(0, sd_pace) and team efficiency shocks (eps_0, eps_1) ~ BVN(0, sd_eff, rho_eff)
    participation every rostered candidate active with its pregame P(play) (or an explicit availability override)
    minutes       anchored logit-shift allocation: M_i = 5 D s_i / sum_active s_j, s_i = sigmoid(a_i + kappa sd_i xi_i +
                  delta_d), delta_d solving sum s = 5 (Newton with bracketing); a_i calibrated so that
                  E[M_i | i active] = Phase 1B min_mu_i under the base scenario. Hence 0 <= M_i <= D, inactive = 0, team
                  total = 5 D exactly in every draw.
    opportunity   family usage U_f = sum_active M_j rate_jf (rate = Phase 1B per-minute rate); team budget
                  B_f = U_ref^theta U^(1-theta), player share M_i rate_i / U_f: deficit term theta_s log(U_ref / U)
    stats         log mu_isd = log mu_1B,is + c_s + beta_s log(max(M,1)/min_mu) + gamma_s . x_td + theta_s r_f(s)
                  (conditional Poisson-pseudo-likelihood GLM with offset log mu_1B, fitted on Phase 1B OOS forecast
                  rows given the REALIZED state; 6 coefficients per stat, no player or pair parameter)
    counts        NB / Poisson (Phase 1B families) at mu_isd; latent Gaussian copula (J2 architecture) across the
                  game's scored players; X = #{k : Phi^-1(F_mu(k)) < z} with mu quantized on a 0.5% log grid.

Ladder (cbb_platform/v2/phase3.py): S0 Phase 2 J2; S1 = S0 + environment terms; S2 = S1 + joint minutes (+ minutes
terms); S3 = S2 + opportunity terms; S4 = S3 + refitted conditional dispersion + refitted residual J2 + mean anchor alpha.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P
from cbb_platform.v2 import dependence as D, latent as L, provenance as PV

STATS = L.STATS
NS = len(STATS)
SI = {s: i for i, s in enumerate(STATS)}
FAMILIES = {"SCORING": ("PTS", "FG3M"), "PLAYMAKING": ("AST", "TOV"), "REBOUNDING": ("REB", "BLK"), "DEFENSIVE": ("STL",)}
FAMILY_RATE_STAT = {"SCORING": "PTS", "PLAYMAKING": "AST", "REBOUNDING": "REB", "DEFENSIVE": "STL"}
FAM = tuple(FAMILIES)
FAMILY_OF = {s: f for f, ss in FAMILIES.items() for s in ss}
FAM_IDX = np.array([FAM.index(FAMILY_OF[s]) for s in STATS])

REGULATION = 40.0
OT_PERIOD = 5.0
ON_COURT = 5
MAX_SEASON = 2026                       # 2025-26: last season with outcomes; 2026-27 is never read
SHRINK_GAMES_PACE = 4.0                 # declared shrinkage (prior-game counts), not tuned
SHRINK_GAMES_EFF = 6.0
KAPPA = 1.0                             # minute-demand noise = Phase 1B's own minutes SD (delta method into logits)
LOGIT_SD_MAX = 3.0
P_CLIP = (0.005, 0.995)
CALIB_ITERS = 6
CALIB_DRAWS = 2000
MIN_FLOOR = 1.0
GRID_H = 0.005                          # log-mean quantization of conditional NB means (0.5%)
GLM_RIDGE = 1e-6                        # relative ridge on the non-intercept GLM coefficients
GLM_TERMS = ("intercept", "log_minutes_ratio", "pace_dev", "own_eff_dev", "opp_eff_dev", "opportunity_deficit")
LAYER_TERMS = {"S1": ("pace_dev", "own_eff_dev", "opp_eff_dev"),
               "S2": ("intercept", "log_minutes_ratio", "pace_dev", "own_eff_dev", "opp_eff_dev"),
               "S3": GLM_TERMS}
ENV_TERMS = ("intercept", "own_pace", "opp_pace", "own_off", "opp_def", "home", "away")
SEED_BASE = 20265
THR_CAP = 40.0                          # thresholds clipped to [-THR_CAP, THR_CAP] (|z| < 9 always)
AVAILABILITY = {"ACTIVE": 1.0, "OUT": 0.0}   # EXPECTED_ACTIVE / QUESTIONABLE: see availability_probability


class V2Phase3Violation(RuntimeError):
    pass


def check_seasons(seasons) -> list[int]:
    s = sorted(int(x) for x in seasons)
    if s and s[-1] > MAX_SEASON:
        raise V2Phase3Violation(f"Phase 3 reads seasons <= {MAX_SEASON}; refused {s}")
    return s


def stream_seed(base: int, game_id: str, stream: str) -> int:
    d = hashlib.blake2b(f"{base}|{stream}|{game_id}".encode(), digest_size=8, person=b"cbbv2st3").digest()
    return int.from_bytes(d, "big") >> 1


# ============================================================================ team-game data
def load_team_frame(seasons, labels: bool) -> pd.DataFrame:
    """Pregame team features (feat.team_features) for every team-game of `seasons`; target-game labels only when
    labels=True (callers pass labels=True for TRAINING seasons only)."""
    seasons = check_seasons(seasons)
    con = duckdb.connect(str(PV.LB_FEATURES), read_only=True, config={"threads": C.DUCKDB_THREADS})
    lab = ""
    if labels:
        con.execute(f"ATTACH '{PV.LB_LABELS}' AS lb (READ_ONLY)")
        lab = """, l.team_points::DOUBLE AS pts, l.possessions_source::DOUBLE AS poss, l.game_minutes::DOUBLE AS game_minutes"""
    df = con.execute(f"""SELECT f.season, f.canonical_game_id AS game_id, f.canonical_team_id AS team_id,
            f.opponent_team_id AS opp_id, f.is_home::DOUBLE AS is_home, f.neutral_site::DOUBLE AS neutral,
            f.prediction_cutoff_utc, f.res_state_available_at, f.box_state_available_at,
            f.te_games_boxed::DOUBLE AS n_boxed, f.te_pace_per40::DOUBLE AS pace40, f.te_off_eff::DOUBLE AS off_eff,
            f.te_def_eff::DOUBLE AS def_eff, f.tp_prev_off_eff::DOUBLE AS prev_off_eff, f.tp_prev_def_eff::DOUBLE AS prev_def_eff
            {lab}
        FROM feat.team_features f {"JOIN lb.lab.team_game_labels l USING (game_example_id, canonical_team_id)" if labels else ""}
        WHERE f.season IN ({", ".join(map(str, seasons))})""").df()
    con.close()
    if len(df) and df["season"].max() > MAX_SEASON:
        raise V2Phase3Violation("a season after 2025-26 reached the team loader")
    return df


def pit_audit(tf: pd.DataFrame) -> dict:
    """Every team feature row must come from states available at or before the prediction cutoff."""
    cut = pd.to_datetime(tf["prediction_cutoff_utc"], utc=True)
    out = {}
    for c in ("res_state_available_at", "box_state_available_at"):
        a = pd.to_datetime(tf[c], utc=True)
        out[c] = int((a.notna() & (a > cut)).sum())
    out["rows"] = int(len(tf))
    out["violations"] = int(sum(v for k, v in out.items() if k != "rows"))
    return out


# ============================================================================ environment
def _shrink(x, n, prior, k):
    x = np.asarray(x, dtype=float)
    n = np.nan_to_num(np.asarray(n, dtype=float))
    return np.where(np.isnan(x), prior, (n * np.nan_to_num(x) + k * prior) / (n + k))


def env_design(tf: pd.DataFrame, pri: dict) -> pd.DataFrame:
    """Pregame log pace / offense / defense estimates for team and opponent + the design matrix columns."""
    d = tf[["season", "game_id", "team_id", "opp_id", "is_home", "neutral"]].copy()
    n = tf["n_boxed"].to_numpy()
    d["space"] = np.log(_shrink(tf["pace40"], n, pri["pace40"], SHRINK_GAMES_PACE))
    po = np.where(tf["prev_off_eff"].notna(), tf["prev_off_eff"] / 100.0, pri["eff"])
    pd_ = np.where(tf["prev_def_eff"].notna(), tf["prev_def_eff"] / 100.0, pri["eff"])
    d["soe"] = np.log(_shrink(tf["off_eff"] / 100.0, n, po, SHRINK_GAMES_EFF))
    d["sde"] = np.log(_shrink(tf["def_eff"] / 100.0, n, pd_, SHRINK_GAMES_EFF))
    o = d[["game_id", "team_id", "space", "soe", "sde"]].rename(
        columns={"team_id": "opp_id", "space": "o_space", "soe": "o_soe", "sde": "o_sde"})
    d = d.merge(o, on=["game_id", "opp_id"], how="left", validate="one_to_one")
    for c, p in (("o_space", np.log(pri["pace40"])), ("o_soe", np.log(pri["eff"])), ("o_sde", np.log(pri["eff"]))):
        d[c] = d[c].fillna(p)
    neutral = d["neutral"].fillna(0).astype(bool)
    home = d["is_home"].fillna(0).astype(bool)
    d["home"] = (home & ~neutral).astype(float)
    d["away"] = (~home & ~neutral).astype(float)
    return d


def _env_X(d: pd.DataFrame) -> np.ndarray:
    return np.column_stack([np.ones(len(d)), d["space"], d["o_space"], d["soe"], d["o_sde"], d["home"], d["away"]])


def realized_env(tf: pd.DataFrame) -> pd.DataFrame:
    """Training labels: realized log pace per 40 (game level: mean of both teams) and log points per possession."""
    ok = (tf["poss"] >= 30) & (tf["pts"] >= 10) & (tf["game_minutes"] >= REGULATION)
    r = tf[["game_id", "team_id"]].copy()
    r["y_pace_t"] = np.where(ok, np.log(tf["poss"] * REGULATION / tf["game_minutes"]), np.nan)
    r["y_eff"] = np.where(ok, np.log(tf["pts"] / tf["poss"]), np.nan)
    g = r.groupby("game_id")["y_pace_t"].agg(["mean", "count"]).rename(columns={"mean": "y_pace", "count": "n_pace"})
    r = r.merge(g, on="game_id", how="left")
    r.loc[r["n_pace"] < 2, "y_pace"] = np.nan
    return r[["game_id", "team_id", "y_pace", "y_eff"]]


def env_fit(tf_train: pd.DataFrame) -> dict:
    """OLS of realized log pace / log efficiency on pregame team estimates; residual shock SDs; overtime law."""
    seasons = check_seasons(tf_train["season"].unique())
    rz = realized_env(tf_train)
    ok = rz["y_eff"].notna()
    pace_real = np.exp(rz.loc[rz["y_pace"].notna(), "y_pace"])
    pri = {"pace40": float(pace_real.mean()), "eff": float(np.exp(rz.loc[ok, "y_eff"]).mean())}
    d = env_design(tf_train, pri).merge(rz, on=["game_id", "team_id"], how="left", validate="one_to_one")
    X = _env_X(d)
    mp = d["y_pace"].notna().to_numpy()
    me = (d["y_eff"].notna() & d["y_pace"].notna()).to_numpy()
    bp = np.linalg.lstsq(X[mp], d.loc[mp, "y_pace"].to_numpy(), rcond=None)[0]
    be = np.linalg.lstsq(X[me], d.loc[me, "y_eff"].to_numpy(), rcond=None)[0]
    d["e_pace_t"], d["e_eff"] = X @ bp, X @ be
    d["e_pace"] = d.groupby("game_id")["e_pace_t"].transform("mean")
    d["r_pace"], d["r_eff"] = d["y_pace"] - d["e_pace"], d["y_eff"] - d["e_eff"]
    gp = d.groupby("game_id")["r_pace"].first().dropna()
    pair = d[["game_id", "team_id", "opp_id", "r_eff"]].merge(
        d[["game_id", "team_id", "r_eff"]].rename(columns={"team_id": "opp_id", "r_eff": "o_r_eff"}), on=["game_id", "opp_id"])
    pair = pair[(pair["team_id"] < pair["opp_id"]) & pair["r_eff"].notna() & pair["o_r_eff"].notna()]
    games = tf_train.drop_duplicates("game_id")
    gm = tf_train.groupby("game_id")["game_minutes"].max().dropna()
    k = np.clip(np.round((gm.to_numpy() - REGULATION) / OT_PERIOD), 0, None).astype(int)
    cnt = np.bincount(k)
    return {"seasons": seasons, "priors": pri, "pace_coef": dict(zip(ENV_TERMS, bp.tolist())),
            "eff_coef": dict(zip(ENV_TERMS, be.tolist())),
            "sd_pace": float(gp.std(ddof=1)), "sd_eff": float(d["r_eff"].std(ddof=1)),
            "rho_eff": float(np.corrcoef(pair["r_eff"], pair["o_r_eff"])[0, 1]),
            "pace_r2": float(1 - d.loc[mp, "r_pace"].var() / d.loc[mp, "y_pace"].var()),
            "eff_r2": float(1 - d.loc[me, "r_eff"].var() / d.loc[me, "y_eff"].var()),
            "n_team_games_pace": int(mp.sum()), "n_team_games_eff": int(me.sum()), "n_games": int(len(games)),
            "overtime": {"games": int(len(k)), "p": (cnt / cnt.sum()).tolist(),
                         "rule": "K = round((game_minutes - 40) / 5) over training games; duration D = 40 + 5K"}}


def env_predict(tf: pd.DataFrame, fit: dict) -> pd.DataFrame:
    """Per team-game pregame environment: game-level expected log pace, team expected log efficiency, and the mean
    deviations from the team's own history that enter the player GLM (pace_dev, own_eff_dev, opp_eff_dev)."""
    d = env_design(tf, fit["priors"])
    X = _env_X(d)
    d["e_pace_t"] = X @ np.array([fit["pace_coef"][k] for k in ENV_TERMS])
    d["e_eff"] = X @ np.array([fit["eff_coef"][k] for k in ENV_TERMS])
    d["e_pace"] = d.groupby("game_id")["e_pace_t"].transform("mean")
    o = d[["game_id", "team_id", "e_eff", "soe"]].rename(columns={"team_id": "opp_id", "e_eff": "o_e_eff", "soe": "o_soe2"})
    d = d.merge(o, on=["game_id", "opp_id"], how="left", validate="one_to_one")
    d["o_e_eff"] = d["o_e_eff"].fillna(np.log(fit["priors"]["eff"]))
    d["o_soe2"] = d["o_soe2"].fillna(d["o_soe"])
    d["pace_dev"] = d["e_pace"] - d["space"]
    d["own_eff_dev"] = d["e_eff"] - d["soe"]
    d["opp_eff_dev"] = d["o_e_eff"] - d["o_soe2"]
    return d[["game_id", "team_id", "opp_id", "space", "soe", "o_soe2", "e_pace", "e_eff", "o_e_eff",
              "pace_dev", "own_eff_dev", "opp_eff_dev"]]


def env_realized_dev(tf: pd.DataFrame, fit: dict) -> pd.DataFrame:
    """TRAINING ONLY: realized deviations (realized pace / efficiency minus the team's own history)."""
    d = env_predict(tf, fit).merge(realized_env(tf), on=["game_id", "team_id"], how="left", validate="one_to_one")
    o = d[["game_id", "team_id", "y_eff"]].rename(columns={"team_id": "opp_id", "y_eff": "o_y_eff"})
    d = d.merge(o, on=["game_id", "opp_id"], how="left", validate="one_to_one")
    d["x_pace_dev"] = d["y_pace"] - d["space"]
    d["x_own_eff_dev"] = d["y_eff"] - d["soe"]
    d["x_opp_eff_dev"] = d["o_y_eff"] - d["o_soe2"]
    return d[["game_id", "team_id", "x_pace_dev", "x_own_eff_dev", "x_opp_eff_dev"]]


# ============================================================================ conditional mean GLM
def rates(rows: pd.DataFrame) -> np.ndarray:
    """Phase 1B per-minute family rates (n x 4): mu_rate_stat / min_mu."""
    m = rows["min_mu"].to_numpy(dtype=float)
    return np.column_stack([rows[f"{FAMILY_RATE_STAT[f]}_mu"].to_numpy(dtype=float) / m for f in FAM])


def glm_rows(store: pd.DataFrame, tf: pd.DataFrame, env: dict) -> tuple[pd.DataFrame, dict]:
    """Training rows given the REALIZED state: PLAYED with minutes + 7 labels, both teams, realized environment.
    Realized minutes / lineup / environment are training labels (never target-game inputs)."""
    acc = {"candidate_rows": int(len(store))}
    s = store[store["participation_status"].eq("PLAYED")].copy()
    ycols = ["y_min"] + [f"y_{x}" for x in STATS]
    miss = s[ycols].isna().any(axis=1)
    acc["played_label_missing"] = int(miss.sum())
    s = s[~miss]
    r = rates(s)
    for j, f in enumerate(FAM):
        s[f"_U_{f}"] = s["y_min"].to_numpy() * r[:, j]
    tg = s.groupby(["forecast_season", "game_id", "team_id"])[[f"_U_{f}" for f in FAM]].sum().reset_index()
    for f in FAM:
        tg[f"_Uref_{f}"] = tg.groupby(["forecast_season", "team_id"])[f"_U_{f}"].transform("mean")
        tg[f"opp_{f}"] = np.log(tg[f"_Uref_{f}"] / tg[f"_U_{f}"])
    s = s.merge(tg[["forecast_season", "game_id", "team_id"] + [f"opp_{f}" for f in FAM]],
                on=["forecast_season", "game_id", "team_id"], how="left")
    x = env_realized_dev(tf, env)
    s = s.merge(x, on=["game_id", "team_id"], how="left", validate="many_to_one")
    bad = s[["x_pace_dev", "x_own_eff_dev", "x_opp_eff_dev"]].isna().any(axis=1)
    acc["realized_environment_missing"] = int(bad.sum())
    s = s[~bad]
    nteam = s.groupby("game_id")["team_id"].transform("nunique")
    acc["one_team_only"] = int((nteam < 2).sum())
    s = s[nteam == 2]
    acc["rows"] = int(len(s))
    acc["games"] = int(s["game_id"].nunique())
    return s.reset_index(drop=True), acc


def glm_design(rows: pd.DataFrame, stat: str, terms=GLM_TERMS, realized: bool = True) -> np.ndarray:
    pre = "x_" if realized else ""
    cols = {"intercept": np.ones(len(rows)),
            "log_minutes_ratio": np.log(np.maximum(rows["y_min"].to_numpy(dtype=float), MIN_FLOOR) / rows["min_mu"].to_numpy(dtype=float)),
            "pace_dev": rows[f"{pre}pace_dev"].to_numpy(dtype=float),
            "own_eff_dev": rows[f"{pre}own_eff_dev"].to_numpy(dtype=float),
            "opp_eff_dev": rows[f"{pre}opp_eff_dev"].to_numpy(dtype=float),
            "opportunity_deficit": rows[f"opp_{FAMILY_OF[stat]}"].to_numpy(dtype=float)}
    return np.column_stack([cols[t] for t in terms])


def poisson_irls(X, y, off, ridge=GLM_RIDGE, iters=100, tol=1e-10):
    n, k = X.shape
    pen = np.full(k, ridge * n)
    pen[0] = 0.0
    b = np.zeros(k)
    for _ in range(iters):
        mu = np.exp(off + X @ b)
        H = (X * mu[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(H, X.T @ (y - mu) - pen * b)
        b = b + step
        if np.abs(step).max() < tol:
            break
    mu = np.exp(off + X @ b)
    H = (X * mu[:, None]).T @ X + np.diag(pen)
    # sandwich SEs, clustered by row (robust to overdispersion)
    S = X * (y - mu)[:, None]
    Hi = np.linalg.inv(H)
    se = np.sqrt(np.diag(Hi @ (S.T @ S) @ Hi))
    return b, se


def glm_fit(rows: pd.DataFrame) -> dict:
    out = {"terms": list(GLM_TERMS), "n_rows": int(len(rows)), "seasons": sorted(int(x) for x in rows["forecast_season"].unique()),
           "stats": {}}
    for s in STATS:
        X = glm_design(rows, s)
        b, se = poisson_irls(X, rows[f"y_{s}"].to_numpy(dtype=float), np.log(rows[f"{s}_mu"].to_numpy(dtype=float)))
        out["stats"][s] = {"coef": dict(zip(GLM_TERMS, b.tolist())), "se_robust": dict(zip(GLM_TERMS, se.tolist())),
                           "family": FAMILY_OF[s]}
    return out


def realized_conditional_mu(rows: pd.DataFrame, glm: dict, terms=GLM_TERMS) -> np.ndarray:
    """Conditional means given the realized state (structural residual basis), n x 7."""
    out = np.empty((len(rows), NS))
    for j, s in enumerate(STATS):
        c = glm["stats"][s]["coef"]
        X = glm_design(rows, s, terms)
        out[:, j] = rows[f"{s}_mu"].to_numpy(dtype=float) * np.exp(X @ np.array([c[t] for t in terms]))
    return out


def dispersion_refit(y: np.ndarray, mu: np.ndarray, families: dict) -> dict:
    """Method of moments conditional dispersion per stat (props_v0._dispersion moment, unweighted); the Phase 1B
    family is kept (NB stays NB, Poisson stays Poisson). An NB stat whose moment is <= 0 would be reported, not
    silently changed."""
    out = {}
    for j, s in enumerate(STATS):
        yy, m = y[:, j], mu[:, j]
        ratio = float(np.sum((yy - m) ** 2) / np.sum(m))
        phi = float(np.sum((yy - m) ** 2 - m) / np.sum(m * m))
        fam = families[s]
        out[s] = {"family": fam, "phi": max(phi, 0.0) if fam == "NEGBIN" else 0.0, "moment_phi": phi,
                  "overdispersion_ratio": ratio, "nonpositive_moment": bool(fam == "NEGBIN" and phi <= 0)}
    return out


# ============================================================================ minutes allocation
def _sig(x):
    return 0.5 * (1.0 + np.tanh(0.5 * x))


def solve_shift(a: np.ndarray, act: np.ndarray, delta0: np.ndarray | None = None, tol: float = 1e-12) -> np.ndarray:
    """delta per row with sum_active sigmoid(a + delta) = 5 (rows with exactly 5 active: +inf). Newton with a
    bisection bracket; a must be finite where act."""
    R = a.shape[0]
    n_act = act.sum(axis=1)
    if (n_act < ON_COURT).any():
        raise V2Phase3Violation("fewer than 5 active players in a team draw")
    delta = np.zeros(R) if delta0 is None else np.where(np.isfinite(delta0), delta0, 0.0).copy()
    lo, hi = np.full(R, -80.0), np.full(R, 80.0)
    fprev = np.full(R, np.inf)
    todo = np.flatnonzero(n_act > ON_COURT)
    am = np.where(act, a, -np.inf)
    for _ in range(300):
        if not len(todo):
            break
        s = _sig(am[todo] + delta[todo, None])
        f = s.sum(axis=1) - ON_COURT
        fp = (s * (1 - s)).sum(axis=1)
        pos = f > 0
        hi[todo] = np.where(pos, delta[todo], hi[todo])
        lo[todo] = np.where(pos, lo[todo], delta[todo])
        with np.errstate(divide="ignore", invalid="ignore"):
            nd = delta[todo] - f / fp
        # safeguarded Newton: bisect when the step leaves the bracket or |f| failed to halve (zig-zag)
        bad = ~np.isfinite(nd) | (nd <= lo[todo]) | (nd >= hi[todo]) | (np.abs(f) > 0.5 * fprev[todo])
        nd = np.where(bad, 0.5 * (lo[todo] + hi[todo]), nd)
        fprev[todo] = np.abs(f)
        done = (np.abs(f) < tol) | (hi[todo] - lo[todo] < 1e-13)
        delta[todo] = np.where(done, delta[todo], nd)
        todo = todo[~done]
    if len(todo):
        raise V2Phase3Violation(f"minutes shift did not converge in {len(todo)} rows")
    delta[n_act == ON_COURT] = np.inf
    return delta


def allocate(a: np.ndarray, act: np.ndarray, dur: np.ndarray, delta0=None) -> tuple[np.ndarray, np.ndarray]:
    """Joint team minutes for R draws: M (R x n), exactly 5 D per row, 0 for inactive, <= D. Returns (M, delta)."""
    delta = solve_shift(a, act, delta0)
    fin = np.isfinite(delta)
    s = np.where(act, _sig(np.where(act, a, 0.0) + np.where(fin, delta, 0.0)[:, None]), 0.0)
    s[~fin] = act[~fin].astype(float)                       # exactly 5 active: everyone plays the full game
    M = ON_COURT * dur[:, None] * s / s.sum(axis=1, keepdims=True)
    return M, delta


def logit_anchor(min_mu: np.ndarray, min_sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.clip(min_mu / REGULATION, *P_CLIP)
    a0 = np.log(p / (1 - p))
    sd = np.minimum(min_sigma / (REGULATION * p * (1 - p)), LOGIT_SD_MAX)
    return a0, sd


def ensure_five(act: np.ndarray, pp: np.ndarray) -> tuple[np.ndarray, int]:
    """Rows with < 5 active: activate the highest-P(play) inactive candidates (declared rule; counted)."""
    short = np.flatnonzero(act.sum(axis=1) < ON_COURT)
    if not len(short):
        return act, 0
    if act.shape[1] < ON_COURT:
        raise V2Phase3Violation("a team has fewer than 5 candidates")
    order = np.argsort(-pp, kind="mergesort")
    for r in short:
        for j in order:
            if act[r].sum() >= ON_COURT:
                break
            act[r, j] = True
    return act, int(len(short))


def calibrate_anchor(a0, sd, xi, act, dur, min_mu, iters=CALIB_ITERS) -> tuple[np.ndarray, list]:
    """Shift each player's logit so that E[M_i | i active] = Phase 1B min_mu_i under the base scenario (first
    CALIB_DRAWS draws; a player's own activation is applied by one Newton step on the shift)."""
    hist = []
    a0 = a0.copy()
    for _ in range(iters):
        a = a0[None, :] + KAPPA * sd[None, :] * xi
        delta = solve_shift(a, act)
        fin = np.isfinite(delta)
        dl = np.where(fin, delta, 30.0)
        s = np.where(act, _sig(a + dl[:, None]), 0.0)
        der = (s * (1 - s)).sum(axis=1)
        si = _sig(a + dl[:, None])
        with np.errstate(divide="ignore", invalid="ignore"):
            dli = np.where(act, dl[:, None], dl[:, None] - si / np.maximum(der, 1e-12)[:, None])
        EM = (dur[:, None] * _sig(a + dli)).mean(axis=0)
        pe = np.clip(EM / REGULATION, 1e-6, 1 - 1e-6)
        pt = np.clip(min_mu / REGULATION, 1e-6, 1 - 1e-6)
        a0 = a0 + (np.log(pt / (1 - pt)) - np.log(pe / (1 - pe)))
        hist.append(float(np.max(np.abs(EM - min_mu))))
    return a0, hist


# ============================================================================ game simulation (structural state)
@dataclass
class GameInput:
    game_id: str
    team: np.ndarray            # 0/1 per candidate (team_id order)
    scored: np.ndarray          # bool per candidate (eligible PLAYED rows, Phase 2 population)
    p_play: np.ndarray          # scenario participation probability per candidate
    min_mu: np.ndarray
    min_sigma: np.ndarray
    rate: np.ndarray            # n x 4 family rates
    env: np.ndarray             # 2 x 3 mean deviations per team (pace_dev, own_eff_dev, opp_eff_dev)


@dataclass
class State:
    dur: np.ndarray             # sims
    x: np.ndarray               # sims x 2 x 3 environment deviations per team
    M: np.ndarray               # sims x n_scored: minutes of each scored player in his own (forced-active) world
    opp_deficit: np.ndarray     # sims x n_scored x 4: log(U_ref / U) per family in that world
    base_M: np.ndarray          # sims x n_candidates: base (unforced) scenario minutes
    checks: dict
    shares_mean: np.ndarray     # n_scored x 4 mean opportunity share M rate / U
    calib_max_err: float


def simulate_state(g: GameInput, env: dict, sims: int, minutes: bool = True, seed_base: int = SEED_BASE) -> State:
    """Duration, environment shocks, participation, joint minutes. Per scored player i the forced-active world is the
    base draw when i is active in it, else the same draw (same uniforms / noise) re-solved with i active: conditioning
    on i playing, every other candidate keeps its own pregame P(play)."""
    p_ot = np.asarray(env["overtime"]["p"])
    r_d = np.random.default_rng(stream_seed(seed_base, g.game_id, "duration"))
    r_e = np.random.default_rng(stream_seed(seed_base, g.game_id, "environment"))
    dur = REGULATION + OT_PERIOD * r_d.choice(len(p_ot), size=sims, p=p_ot)
    eg = env["sd_pace"] * r_e.standard_normal(sims)
    e = r_e.standard_normal((sims, 2))
    rho = env["rho_eff"]
    et = env["sd_eff"] * np.column_stack([e[:, 0], rho * e[:, 0] + np.sqrt(1 - rho * rho) * e[:, 1]])
    x = np.empty((sims, 2, 3))
    for t in (0, 1):
        x[:, t, 0] = g.env[t, 0] + eg
        x[:, t, 1] = g.env[t, 1] + et[:, t]
        x[:, t, 2] = g.env[t, 2] + et[:, 1 - t]
    sc = np.flatnonzero(g.scored)
    checks = {"team_draws": 0, "team_minutes_violations": 0, "minutes_bound_violations": 0, "inactive_minutes_nonzero": 0,
              "negative_share": 0, "share_sum_violations": 0, "forced_rows": 0, "ensure_five_rows": 0}
    if not minutes:
        return State(dur, x, np.zeros((sims, 0)), np.zeros((sims, 0, len(FAM))), np.zeros((sims, 0)), checks,
                     np.zeros((0, len(FAM))), 0.0)
    r_p = np.random.default_rng(stream_seed(seed_base, g.game_id, "participation"))
    r_x = np.random.default_rng(stream_seed(seed_base, g.game_id, "minutes"))
    n = len(g.team)
    U = r_p.random((sims, n))
    xi = r_x.standard_normal((sims, n))
    base_act = U < g.p_play[None, :]
    base_M = np.zeros((sims, n))
    Mi = np.zeros((sims, len(sc)))
    opp = np.zeros((sims, len(sc), len(FAM)))
    shares = np.zeros((len(sc), len(FAM)))
    cal_err = 0.0
    for t in (0, 1):
        idx = np.flatnonzero(g.team == t)
        act, nfix = ensure_five(base_act[:, idx].copy(), g.p_play[idx])
        checks["ensure_five_rows"] += nfix
        a0, sd = logit_anchor(g.min_mu[idx], g.min_sigma[idx])
        c = slice(0, min(CALIB_DRAWS, sims))
        a0, hist = calibrate_anchor(a0, sd, xi[c][:, idx], act[c], dur[c], g.min_mu[idx])
        cal_err = max(cal_err, hist[-1])
        checks["team_expected_minutes_gap_abs"] = checks.get("team_expected_minutes_gap_abs", 0.0) + abs(
            float((g.p_play[idx] * g.min_mu[idx]).sum()) - ON_COURT * float(dur.mean()))
        checks["teams"] = checks.get("teams", 0) + 1
        a = a0[None, :] + KAPPA * sd[None, :] * xi[:, idx]
        Mb, dlb = allocate(a, act, dur)
        base_M[:, idx] = Mb
        rt = g.rate[idx]
        Ub = Mb @ rt                                                   # sims x 4
        Uref = Ub.mean(axis=0)
        _check(checks, Mb, act, dur, rt, Ub)
        for k_i, i in enumerate(sc):
            if g.team[i] != t:
                continue
            j = int(np.flatnonzero(idx == i)[0])
            off = ~act[:, j]
            M_i, U_i = Mb[:, j].copy(), Ub.copy()
            if off.any():
                act_f = act[off].copy()
                act_f[:, j] = True
                Mf, _ = allocate(a[off], act_f, dur[off], dlb[off])
                _check(checks, Mf, act_f, dur[off], rt, Mf @ rt)
                M_i[off] = Mf[:, j]
                U_i[off] = Mf @ rt
                checks["forced_rows"] += int(off.sum())
            Mi[:, k_i] = M_i
            with np.errstate(divide="ignore"):
                opp[:, k_i] = np.log(Uref[None, :] / U_i)
            shares[k_i] = (M_i[:, None] * rt[j][None, :] / U_i).mean(axis=0)
    return State(dur, x, Mi, opp, base_M, checks, shares, cal_err)


def _check(checks, M, act, dur, rt, U):
    checks["team_draws"] += M.shape[0]
    checks["team_minutes_violations"] += int((np.abs(M.sum(axis=1) - ON_COURT * dur) > 1e-9).sum())
    checks["minutes_bound_violations"] += int(((M < 0) | (M > dur[:, None] + 1e-9)).sum())
    checks["inactive_minutes_nonzero"] += int((M[~act] != 0).sum())
    sh = M[:, :, None] * rt[None] / U[:, None, :]
    checks["negative_share"] += int((sh < 0).sum())
    checks["share_sum_violations"] += int((np.abs(sh.sum(axis=1) - 1) > 1e-9).sum())


def log_mu(base_log_mu: np.ndarray, min_mu: np.ndarray, team: np.ndarray, st: State, glm: dict, layer: str) -> np.ndarray:
    """sims x n_scored x 7 conditional log means for one ladder layer (S1 / S2 / S3)."""
    return log_mu_terms(base_log_mu, min_mu, team, st, glm, LAYER_TERMS[layer])


def log_mu_terms(base_log_mu: np.ndarray, min_mu: np.ndarray, team: np.ndarray, st: State, glm: dict, terms) -> np.ndarray:
    """sims x n_scored x 7 conditional log means using the given GLM terms."""
    sims = len(st.dur)
    n = base_log_mu.shape[0]
    out = np.broadcast_to(base_log_mu[None], (sims, n, NS)).copy()
    xs = st.x[:, team, :]                                              # sims x n x 3
    lmr = np.log(np.maximum(st.M, MIN_FLOOR) / min_mu[None, :]) if "log_minutes_ratio" in terms else None
    for j, s in enumerate(STATS):
        c = glm["stats"][s]["coef"]
        v = np.zeros((sims, n))
        if "pace_dev" in terms:
            v += c["pace_dev"] * xs[..., 0]
        if "own_eff_dev" in terms:
            v += c["own_eff_dev"] * xs[..., 1]
        if "opp_eff_dev" in terms:
            v += c["opp_eff_dev"] * xs[..., 2]
        if "intercept" in terms:
            v += c["intercept"]
        if lmr is not None:
            v += c["log_minutes_ratio"] * lmr
        if "opportunity_deficit" in terms:
            v += c["opportunity_deficit"] * st.opp_deficit[:, :, FAM_IDX[j]]
        out[:, :, j] += v
    return out


def needs_minutes(terms) -> bool:
    return "log_minutes_ratio" in terms or "opportunity_deficit" in terms


def anchor(lm: np.ndarray, base_log_mu: np.ndarray, alpha: float) -> tuple[np.ndarray, np.ndarray]:
    """Mean anchor: scale each (player, stat) so E_d[mu] = (1 - alpha) mu_1B + alpha E_d[mu_structural]."""
    m = np.exp(lm).mean(axis=0)
    mu1b = np.exp(base_log_mu)
    target = (1 - alpha) * mu1b + alpha * m
    shift = np.log(target / m)
    return lm + shift[None], m


# ============================================================================ counts from conditional means
def mixture_tables(lm: np.ndarray, phi: np.ndarray, kmax: int = L.KMAX) -> dict:
    """Quantize log means to GRID_H, build the NB / Poisson PMF + latent thresholds for every distinct (dim, grid)
    pair. lm: sims x d (player-major, stat = d % 7). Returns global row indices per (draw, dim) and the tables."""
    sims, d = lm.shape
    q = np.round(lm / GRID_H).astype(np.int64)
    qmin = q.min(axis=0)
    width = q.max(axis=0) - qmin + 1
    base = np.concatenate([[0], np.cumsum(width)[:-1]])
    key = q - qmin[None, :] + base[None, :]
    used = np.bincount(key.ravel(), minlength=int(width.sum())) > 0
    rank = np.cumsum(used) - 1
    inv = rank[key]
    dim_of = np.repeat(np.arange(d), width)[used]
    grid = (np.concatenate([np.arange(w) for w in width]) + np.repeat(qmin, width))[used]
    mu = np.exp(grid * GRID_H)
    so = dim_of % NS
    pmf = np.empty((len(mu), kmax + 1))
    for j in range(NS):
        m = so == j
        if m.any():
            pmf[m] = P.pmf_matrix(mu[m], float(phi[j]), kmax)
    cdf = np.minimum(np.maximum.accumulate(np.cumsum(pmf, axis=1), axis=1), 1.0)
    cdf[:, -1] = 1.0
    thr = np.full(cdf.shape, THR_CAP)
    need = (cdf < 1.0 - 1e-15)
    with np.errstate(divide="ignore"):
        thr[need] = np.clip(L.ndtri(np.clip(cdf[need], 1e-300, 1.0)), -THR_CAP, THR_CAP)
    starts = np.searchsorted(dim_of, np.arange(d + 1), side="left")
    return {"inv": inv, "starts": starts, "pmf": pmf, "cdf": cdf, "thr": thr}


def counts(tab: dict, z: np.ndarray) -> np.ndarray:
    """X = #{k : thr(k) < z} per (draw, dim): ONE searchsorted over all row-offset thresholds (rows are disjoint
    intervals [span r - cap, span r + cap], z clipped to |z| <= 9)."""
    sims, d = z.shape
    K = tab["thr"].shape[1]
    span = 4 * THR_CAP
    rows = np.arange(len(tab["thr"]), dtype=float)
    flat = (tab["thr"] + span * rows[:, None]).ravel()
    r = tab["inv"]
    out = np.searchsorted(flat, (np.clip(z, -9.0, 9.0) + span * r).ravel(), side="left").reshape(sims, d) - r * K
    return out.astype(np.int16)


def mixture_pmf(tab: dict, sims: int) -> np.ndarray:
    """Rao-Blackwellized predictive PMF per dim: the mixture of the conditional PMFs over the draws (d x (kmax+1))."""
    d = tab["inv"].shape[1]
    w = np.bincount(tab["inv"].ravel(), minlength=len(tab["pmf"])) / sims
    out = np.empty((d, tab["pmf"].shape[1]))
    for c in range(d):
        a, b = tab["starts"][c], tab["starts"][c + 1]
        out[c] = w[a:b] @ tab["pmf"][a:b]
    return out


# ============================================================================ availability interface (production)
def availability_probability(status: str | None, p_model: float, questionable_p: float = 0.5) -> float:
    """Pregame availability override -> participation probability for the scenario (interface; prospectively
    validated in 2026-27): ACTIVE 1; OUT 0; EXPECTED_ACTIVE max(model, 0.9); QUESTIONABLE / UNCERTAIN the supplied
    probability (default 0.5); None -> the pregame P(play) model."""
    if status is None:
        return float(p_model)
    s = status.upper()
    if s in AVAILABILITY:
        return AVAILABILITY[s]
    if s == "EXPECTED_ACTIVE":
        return float(max(p_model, 0.9))
    if s in ("QUESTIONABLE", "UNCERTAIN"):
        return float(questionable_p)
    raise ValueError(f"unknown availability status {status!r}")
