"""CBB V2 JointMinutesV2 (experiment cbb-v2-joint-minutes-v2): joint team minutes anchored on the Phase 1B participation
and conditional-minutes marginals, with an aggregate stochastic unmodeled-rotation reservoir, a small role-tier latent
factor and reliability-weighted bounded reconciliation; one reconciled minutes draw feeds all seven stats.

This is a separately versioned challenger to the current champion S0 (Phase 1B marginals + Phase 2 J2). It is NOT Phase 3
S2: no opportunity redistribution, no "fewer than five active" activation rule, no logit-shift allocator. The Phase 3
records (cbb_platform/v2/phase3.py) are read-only here; their helpers are reused as a library.

Stages (each refuses to run out of order):

    .venv/bin/python -m cbb_platform.v2.joint_minutes_v2 protocol   write the frozen protocol (commit before `develop`)
    .venv/bin/python -m cbb_platform.v2.joint_minutes_v2 develop    parameters through 2023-24, ONE 2024-25 development run

2024-25 is development data (inspected during Phase 3 and the S2 audit). 2025-26 is reserved for one locked retrospective
confirmation after this procedure is frozen; this module has no stage that reads it. No API calls.
"""
from __future__ import annotations

import gc
import hashlib
import json
import sys
import time
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P
from cbb_platform.v2 import dependence as D, joint as J, latent as L, marginal as M, phase2 as P2, phase3 as P3
from cbb_platform.v2 import provenance as PV, structural as S

VERSION = "cbb-v2-joint-minutes-v2-1.0.0"
EXPERIMENT = "cbb-v2-joint-minutes-v2"
OUT = M.OUT_DIR
PROTOCOL_PATH = OUT / "joint_minutes_v2_protocol.json"
DEV_PATH = OUT / "joint_minutes_v2_development.json"
PARAMS_PATH = OUT / "joint_minutes_v2_dev_parameters.json"
REPORT_PATH = OUT / "JOINT_MINUTES_V2_REPORT.md"
SCRATCH = C.PROJECT_ROOT / "data" / "qa" / "large" / "v2_jm2"
USER_QA_FILE = C.PROJECT_ROOT / "data" / "qa" / "game_identity_questionable_only.csv"
USER_QA_SHA = "5ca9001c7c4e700b6ca0dd0e27c20290982d0051748ff96fa28317fa002f15b2"
PHASE3_FROZEN = {"phase3_protocol.json": "713e0ca4299ed85e8400e9fdd703eec8575f717b05264e3e002e4f5eb0d049ef",
                 "phase3_selection_rebuild.json": "beace932a5be857d7a62370943dda9e07ddb232d86641d202ce1be45e38852e4",
                 "phase3_selection_rebuild_addendum.json": "aa609597419330cc54b570bca8369a0db149869747bd0e4ac1933dcab6278839"}
CODE = [C.PROJECT_ROOT / "cbb_platform" / "v2" / f for f in ("joint_minutes_v2.py", "structural.py", "phase3.py")]

DEV = {"T": 2024, "eval": 2025, "resid_seasons": (2024,)}      # parameters through 2023-24; develop on 2024-25
MAX_SEASON_THIS_TURN = 2025                                     # 2025-26 is never read by this module
FIRST_RESERVOIR_SEASON = 2014
SEED_BASE = 20271
ARMS = ("S0", "JM1", "JM2_A05", "JM2_A10")
ALPHAS = {"JM2_A05": 0.5, "JM2_A10": 1.0}
JM1_ALPHA = 1.0
JM_TERMS = ("intercept", "log_minutes_ratio", "pace_dev", "own_eff_dev", "opp_eff_dev")   # no opportunity term
TIERS = ("TOP5_MINUTES", "ROTATION_6_8", "BENCH_9PLUS")
TIER_OF_ROLE = {"TOP5_MINUTES": 0, "ROTATION_6_8": 1, "BENCH_9PLUS": 2, "NO_ROLE_HISTORY": 2}
ROLES4 = ("TOP5_MINUTES", "ROTATION_6_8", "BENCH_9PLUS", "NO_ROLE_HISTORY")
W_SHRINK_N0 = 500.0                     # reliability variance: pseudo-rows toward the global variance
CORR_SHRINK_N0 = 2000.0                 # tier correlations: pseudo-pairs toward 0
CORR_CAL_ITERS = 3                      # constraint compensation (training season only)
CORR_CAL_TEAM_GAMES = 400
CORR_CAL_DRAWS = 2000
WR_FLOOR = 1.0                          # reservoir reliability variance floor (minutes^2)
QP_ITERS = 64
CLOSURE_TOL = 1e-9
KMAX = P3.MIN_KMAX                      # 60 = Phase 1B min_support_max
SIMS = J.SIMS
RES_FEATURES = ("intercept", "tg0", "tg1", "tg2", "tg3_4", "tg5_9", "warm_share", "nohist_share", "transfer_share",
                "log_n_cand", "ret_share", "ret_missing")
EARLY_TEAM_GAMES = 2                    # EARLY_SEASON stratum: pregame team games so far <= 2
RESERVOIR_HEAVY_MIN = 10.0              # pregame E[R] >= 10 minutes
NEAR_BUDGET_MIN = 15.0
# development gates (strict: an inconclusive interval is not a pass)
G_MIN_CRPS_ALL = 0.01
G_MIN_CRPS_TIER = 0.02
G_MIN_CRPS_EARLY = 0.02
G_MIN_CRPS_RESERVOIR_HEAVY = 0.05
G_MARG_LOG_ALL = 0.001
G_MARG_LOG_STAT = 0.002
G_MARG_LOG_STAT_ROLE = 0.005
G_ES = 0.003
G_VS = 0.0001


class JMViolation(RuntimeError):
    pass


def _sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


_rel, _dump, _jd = P2._rel, P2._dump, P2._json_default


def check_seasons(seasons) -> list[int]:
    s = sorted({int(x) for x in seasons})
    if s and s[-1] > MAX_SEASON_THIS_TURN:
        raise JMViolation(f"JointMinutesV2 development never reads 2025-26 or later (asked {s})")
    return s


def guards() -> dict:
    out = P3.guards()
    for name, sha in PHASE3_FROZEN.items():
        out[f"{name}_sha256"] = _sha(OUT / name)
        out[f"{name}_UNCHANGED"] = out[f"{name}_sha256"] == sha
    out["USER_QA_FILE_sha256"] = _sha(USER_QA_FILE) if USER_QA_FILE.exists() else None
    out["USER_QA_FILE_UNCHANGED"] = out["USER_QA_FILE_sha256"] == USER_QA_SHA
    if not all(v for k, v in out.items() if k.endswith("UNCHANGED")):
        raise JMViolation(f"a protected file changed: {out}")
    return out


def code_state() -> dict:
    return {_rel(p): _sha(p) for p in CODE}


def stream_seed(game_id: str, stream: str) -> int:
    return S.stream_seed(SEED_BASE, game_id, stream)


def load_store(seasons) -> pd.DataFrame:
    return P3.load_store(check_seasons(seasons))


def expected_duration(p_ot) -> float:
    p = np.asarray(p_ot, dtype=float)
    return float(S.REGULATION + S.OT_PERIOD * (p * np.arange(len(p))).sum())


# ============================================================================ candidate universe (point in time)
def _con():
    con = duckdb.connect(str(PV.LB_FEATURES), read_only=True, config={"threads": C.DUCKDB_THREADS})
    return con


def universe_audit(seasons) -> dict:
    """The pregame candidate universe is ex.player_game_examples (= feat.player_features): every player with legal
    point-in-time evidence (prior appearance, prior-season returner / roster, preseason transfer / recruit). Checks
    whether any other legal point-in-time membership evidence could add a candidate, and how much realized playing
    time came from players outside it (diagnostic only; lab.player_pool_misses is an outcome table)."""
    seasons = check_seasons(seasons)
    sl = ", ".join(map(str, seasons))
    con = _con()
    con.execute(f"ATTACH '{PV.LB_LABELS}' AS lb (READ_ONLY)")
    add = con.execute(f"""SELECT count(*) FROM (SELECT DISTINCT e.canonical_game_id, e.canonical_team_id, m.canonical_player_id
        FROM (SELECT DISTINCT canonical_game_id, canonical_team_id, season, prediction_cutoff_utc FROM ex.player_game_examples
              WHERE season IN ({sl})) e
        JOIN ex.team_membership_evidence m ON m.canonical_team_id = e.canonical_team_id AND m.season = e.season
             AND m.available_from <= e.prediction_cutoff_utc
        ANTI JOIN ex.player_game_examples p ON p.canonical_game_id = e.canonical_game_id
             AND p.canonical_team_id = e.canonical_team_id AND p.canonical_player_id = m.canonical_player_id)""").fetchone()[0]
    roster = con.execute("SELECT roster_knowledge_class, count(*) FROM ex.roster_snapshot_research GROUP BY 1").fetchall()
    miss = con.execute(f"""SELECT season, miss_class, legal_evidence, count(*), sum(minutes) FROM lb.lab.player_pool_misses
        WHERE season IN ({sl}) GROUP BY ALL ORDER BY ALL""").fetchall()
    ev = con.execute(f"SELECT pool_evidence, count(*) FROM ex.player_game_examples WHERE season IN ({sl}) GROUP BY 1 ORDER BY 1").fetchall()
    con.close()
    return {"universe": "ex.player_game_examples (= feat.player_features rows = Phase 1B candidate rows)",
            "pool_evidence_counts": {a: int(b) for a, b in ev},
            "LEGAL_PIT_MEMBERSHIP_EVIDENCE_NOT_IN_UNIVERSE": int(add),
            "roster_snapshot_research": {a: int(b) for a, b in roster},
            "roster_snapshot_note": "SEASON_ROSTER_ONLY_NOT_PIT_CONFIRMED: not point-in-time; not used",
            "realized_players_outside_universe_DIAGNOSTIC": [
                {"season": int(a), "miss_class": b, "legal_evidence": bool(c), "rows": int(d), "minutes": int(e)}
                for a, b, c, d, e in miss],
            "conclusion": ("the universe already holds every player with legal pregame evidence; the remaining realized "
                           "minutes outside it (no legal pregame evidence) are carried by the reservoir") if add == 0 else
                          "legal evidence exists outside the universe"}


# ============================================================================ unmodeled-rotation reservoir
def reservoir_frame(seasons, labels: bool) -> pd.DataFrame:
    """Per team-game PREGAME completeness features from feat.player_features (+ realized minutes of players outside the
    candidate universe as the TRAINING label when labels=True)."""
    seasons = check_seasons(seasons)
    sl = ", ".join(map(str, seasons))
    con = _con()
    lab_cte, lab_sel, lab_join = "", "", ""
    if labels:
        con.execute(f"ATTACH '{PV.LB_LABELS}' AS lb (READ_ONLY)")
        lab_cte = (", mi AS (SELECT canonical_game_id AS game_id, canonical_team_id AS team_id, sum(minutes)::DOUBLE AS "
                   "res_minutes FROM lb.lab.player_pool_misses WHERE season IN (%s) GROUP BY 1, 2)" % sl)
        lab_sel = ", coalesce(mi.res_minutes, 0) AS res_minutes"
        lab_join = "LEFT JOIN mi USING (game_id, team_id)"
    df = con.execute(f"""WITH tg AS (
            SELECT f.season, f.canonical_game_id AS game_id, f.canonical_team_id AS team_id,
                   any_value(f.prediction_cutoff_utc) AS prediction_cutoff_utc, count(*)::DOUBLE AS n_cand,
                   avg((f.cold_start_class = 'WARM')::DOUBLE) AS warm_share,
                   avg((f.cold_start_class = 'COLD_NO_HISTORY')::DOUBLE) AS nohist_share,
                   avg((f.cold_start_class = 'COLD_TRANSFER_IN')::DOUBLE) AS transfer_share,
                   coalesce(max(f.pp_team_games), 0)::DOUBLE AS team_games,
                   any_value(f.ret_team_returning_min_share)::DOUBLE AS ret_share_raw,
                   max(f.part_state_available_at) AS part_state_max, max(f.pbox_state_available_at) AS pbox_state_max
            FROM feat.player_features f WHERE f.season IN ({sl}) GROUP BY 1, 2, 3){lab_cte}
        SELECT tg.*{lab_sel} FROM tg {lab_join} ORDER BY game_id, team_id""").df()
    con.close()
    if len(df) and df["season"].max() > MAX_SEASON_THIS_TURN:
        raise JMViolation("a season after the development season reached the reservoir loader")
    return df


def reservoir_pit_audit(rf: pd.DataFrame) -> dict:
    cut = pd.to_datetime(rf["prediction_cutoff_utc"], utc=True)
    out = {c: int((pd.to_datetime(rf[c], utc=True).notna() & (pd.to_datetime(rf[c], utc=True) > cut)).sum())
           for c in ("part_state_max", "pbox_state_max")}
    out["rows"] = int(len(rf))
    out["violations"] = int(out["part_state_max"] + out["pbox_state_max"])
    return out


def reservoir_design(rf: pd.DataFrame) -> np.ndarray:
    tg = rf["team_games"].to_numpy(dtype=float)
    ret = rf["ret_share_raw"].to_numpy(dtype=float)
    cols = {"intercept": np.ones(len(rf)), "tg0": (tg == 0).astype(float), "tg1": (tg == 1).astype(float),
            "tg2": (tg == 2).astype(float), "tg3_4": ((tg >= 3) & (tg <= 4)).astype(float),
            "tg5_9": ((tg >= 5) & (tg <= 9)).astype(float), "warm_share": rf["warm_share"].to_numpy(dtype=float),
            "nohist_share": rf["nohist_share"].to_numpy(dtype=float),
            "transfer_share": rf["transfer_share"].to_numpy(dtype=float),
            "log_n_cand": np.log(np.maximum(rf["n_cand"].to_numpy(dtype=float), 1.0)),
            "ret_share": np.where(np.isnan(ret), 0.0, ret), "ret_missing": np.isnan(ret).astype(float)}
    return np.column_stack([cols[k] for k in RES_FEATURES])


def logistic_irls(X, y, ridge=1e-6, iters=100, tol=1e-10):
    n, k = X.shape
    pen = np.full(k, ridge * n)
    pen[0] = 0.0
    b = np.zeros(k)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(X @ b)))
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(H, X.T @ (y - p) - pen * b)
        b = b + step
        if np.abs(step).max() < tol:
            break
    return b


def fit_reservoir(T: int) -> dict:
    """Two-part pregame model of the realized minutes played by players outside the candidate universe, fitted on
    team-games 2013-14..T (labels <= T): P(R > 0) logistic; E[R | R > 0] log-linear (Poisson pseudo-likelihood);
    a pooled Gamma CV of the positive part (moments)."""
    rf = reservoir_frame(range(FIRST_RESERVOIR_SEASON, T + 1), labels=True)
    X = reservoir_design(rf)
    y = rf["res_minutes"].to_numpy(dtype=float)
    pos = y > 0
    b_any = logistic_irls(X, pos.astype(float))
    lyb = float(np.log(y[pos].mean()))                         # constant offset = start at the log mean (same MLE)
    b_pos, _ = S.poisson_irls(X[pos], y[pos], np.full(pos.sum(), lyb))
    b_pos[0] += lyb
    mu_pos = np.exp(X[pos] @ b_pos)
    cv2 = float(np.mean((y[pos] / mu_pos - 1.0) ** 2))
    pi = 1.0 / (1.0 + np.exp(-(X @ b_any)))
    pred = pi * np.exp(X @ b_pos)
    return {"features": list(RES_FEATURES), "seasons": [FIRST_RESERVOIR_SEASON, T], "team_games": int(len(rf)),
            "positive_share": float(pos.mean()), "mean_label": float(y.mean()),
            "coef_any": dict(zip(RES_FEATURES, b_any.tolist())), "coef_pos": dict(zip(RES_FEATURES, b_pos.tolist())),
            "cv2_pos": cv2, "train_mean_pred": float(pred.mean()), "pit_audit": reservoir_pit_audit(rf),
            "label": "sum of realized minutes of players outside the pregame candidate universe (lab.player_pool_misses)"}


def reservoir_predict(rf: pd.DataFrame, fit: dict) -> pd.DataFrame:
    X = reservoir_design(rf)
    ba = np.array([fit["coef_any"][k] for k in RES_FEATURES])
    bp = np.array([fit["coef_pos"][k] for k in RES_FEATURES])
    pi = 1.0 / (1.0 + np.exp(-(X @ ba)))
    mpos = np.exp(X @ bp)
    cv2 = fit["cv2_pos"]
    out = rf[["game_id", "team_id", "team_games", "n_cand", "warm_share", "prediction_cutoff_utc"]].copy()
    out["res_pi"], out["res_mu_pos"] = pi, mpos
    out["res_mean"] = pi * mpos
    out["res_var"] = pi * mpos * mpos * (1 + cv2) - (pi * mpos) ** 2
    return out


# ============================================================================ reliability weights and tier factor
def _cold2(c) -> np.ndarray:
    return np.where(np.asarray(c) == "WARM", "WARM", "COLD")


def fit_weights(T: int) -> dict:
    """W: pooled OOS Phase 1B minutes-error variance Var(y - min_mu) of PLAYED rows by role x {WARM, COLD}, shrunk to
    the global variance with W_SHRINK_N0 pseudo-rows; Phase 1B OOS rows 2022-23..T."""
    st = load_store(range(P3.OOS_FIRST, T + 1))
    st = st[st["participation_status"].eq("PLAYED") & st["y_min"].notna()]
    e2 = (st["y_min"] - st["min_mu"]).to_numpy(dtype=float) ** 2
    glob = float(e2.mean())
    grp = pd.DataFrame({"role": st["role_diagnostic"].to_numpy(), "cold": _cold2(st["cold_start_class"]), "e2": e2})
    out = {"global": glob, "n0": W_SHRINK_N0, "groups": {}}
    for r in ROLES4:
        for c in ("WARM", "COLD"):
            g = grp[(grp["role"] == r) & (grp["cold"] == c)]
            n = len(g)
            raw = float(g["e2"].mean()) if n else glob
            out["groups"][f"{r}|{c}"] = {"n": int(n), "raw": raw, "w": (n * raw + W_SHRINK_N0 * glob) / (n + W_SHRINK_N0)}
    out["seasons"] = sorted(int(x) for x in st["forecast_season"].unique())
    return out


def weights_of(role, cold, wfit) -> np.ndarray:
    return np.array([wfit["groups"][f"{r}|{c}"]["w"] for r, c in zip(role, _cold2(cold))], dtype=float)


def tier_of(role) -> np.ndarray:
    return np.array([TIER_OF_ROLE[r] for r in role], dtype=np.int64)


def pmf_cdf(min_mu, min_sigma) -> np.ndarray:
    return np.minimum(np.cumsum(P.minutes_pmf(np.asarray(min_mu, float), np.asarray(min_sigma, float), KMAX), axis=1), 1.0)


def normal_score(x, cdf: np.ndarray) -> np.ndarray:
    """Mid-PIT normal score of minutes x (rounded) under each column's Phase 1B discretized PMF (cdf: n x (KMAX+1));
    x: (..., n)."""
    k = np.clip(np.round(x), 0, KMAX).astype(np.int64)
    n = cdf.shape[0]
    hi = cdf[np.arange(n), k] if k.ndim == 1 else np.take_along_axis(np.broadcast_to(cdf, k.shape[:-1] + cdf.shape),
                                                                       k[..., None], axis=-1)[..., 0]
    lo_k = np.maximum(k - 1, 0)
    lo = cdf[np.arange(n), lo_k] if k.ndim == 1 else np.take_along_axis(np.broadcast_to(cdf, k.shape[:-1] + cdf.shape),
                                                                          lo_k[..., None], axis=-1)[..., 0]
    lo = np.where(k > 0, lo, 0.0)
    return L.ndtri(np.clip(0.5 * (lo + hi), 1e-6, 1 - 1e-6))


def pair_tier_sums(z: np.ndarray, tier: np.ndarray, ok: np.ndarray, team: np.ndarray, acc: dict) -> None:
    """Accumulate pooled same-team pair moments per tier pair: sum z_i z_j, sum z_i^2, sum z_j^2, count over (draw,
    unordered pair) with both entries valid. z, ok: (R, n)."""
    n = z.shape[1]
    zz = np.where(ok, z, 0.0)
    for i in range(n):
        for j in range(i + 1, n):
            if team[i] != team[j]:
                continue
            a, b = sorted((int(tier[i]), int(tier[j])))
            m = ok[:, i] & ok[:, j]
            c = int(m.sum())
            if not c:
                continue
            r = acc.setdefault((a, b), np.zeros(4))
            r += [float((zz[:, i] * zz[:, j]).sum()), float((zz[:, i] ** 2 * m).sum()), float((zz[:, j] ** 2 * m).sum()), c]


def pooled_corr(acc: dict) -> dict:
    return {f"{TIERS[a]}|{TIERS[b]}": {"corr": float(v[0] / np.sqrt(v[1] * v[2])) if v[1] > 0 and v[2] > 0 else 0.0,
                                         "pairs": int(v[3])} for (a, b), v in sorted(acc.items())}


def corr_matrix(pc: dict, shrink_n0: float = 0.0) -> np.ndarray:
    Cm = np.zeros((3, 3))
    for k, v in pc.items():
        a, b = (TIERS.index(x) for x in k.split("|"))
        r = v["corr"] * (v["pairs"] / (v["pairs"] + shrink_n0) if shrink_n0 else 1.0)
        Cm[a, b] = Cm[b, a] = r
    return Cm


def project_factor(Cm: np.ndarray) -> tuple[np.ndarray, bool]:
    """Tier factor covariance: symmetric, diagonal in [0, 0.95], PSD by eigenvalue clipping (count the repair)."""
    A = 0.5 * (Cm + Cm.T)
    np.fill_diagonal(A, np.clip(np.diag(A), 0.0, 0.95))
    w, V = np.linalg.eigh(A)
    rep = bool((w < 0).any())
    if rep:
        A = (V * np.maximum(w, 0.0)) @ V.T
        np.fill_diagonal(A, np.clip(np.diag(A), 0.0, 0.95))
    return A, rep


def factor_loadings(Cf: np.ndarray) -> np.ndarray:
    w, V = np.linalg.eigh(Cf)
    return V * np.sqrt(np.maximum(w, 0.0))                     # f = Lf g, Cov f = Cf


def latent_minutes(Cf: np.ndarray, tier: np.ndarray, team: np.ndarray, g: np.ndarray, eps: np.ndarray) -> np.ndarray:
    """z_i = f_{team(i), tier(i)} + sqrt(1 - Cf_tt) eps_i, f_team ~ N(0, Cf) independent across the two teams; unit
    variance; g: (R, 2, 3) standard normals, eps: (R, n)."""
    Lf = factor_loadings(Cf)
    f = np.einsum("rtk,jk->rtj", g, Lf)                         # (R, 2, 3)
    d = np.sqrt(np.maximum(1.0 - np.diag(Cf), 0.0))
    return f[:, team, tier] + d[tier][None, :] * eps


def raw_minutes(min_mu, min_sigma, z, dur, e_dur) -> np.ndarray:
    """Phase 1B conditional-minutes marginal sampled directly: clip(min_mu + min_sigma z, 0, KMAX) rounds exactly to the
    Phase 1B discretized normal PMF; scaled by D / E[D] so the mean over durations is the Phase 1B mean and longer games
    carry proportionally more raw demand."""
    x = np.clip(min_mu[None, :] + min_sigma[None, :] * z, 0.0, float(KMAX))
    return x * (dur / e_dur)[:, None]


# ============================================================================ reliability-weighted bounded reconciliation
def reconcile(r: np.ndarray, w: np.ndarray, act: np.ndarray, dur: np.ndarray, rR: np.ndarray, wR: np.ndarray):
    """argmin sum_i (m_i - r_i)^2 / w_i + (R - rR)^2 / wR  s.t.  sum_i m_i + R = 5 D, 0 <= m_i <= D, m_i = 0 for
    nonparticipants, R >= 0. KKT: m_i = clip(r_i + lam w_i, 0, D), R = max(0, rR + lam wR); g(lam) = sum + R - 5D is
    continuous, nondecreasing, piecewise linear: bracket + bisection, then the exact linear solve on the final active
    set; floating-point closure (|e| <= 1e-9, else raise) is absorbed by R when R > 0, else by the free player with the
    most room. Rows: draws; vectorized. Returns (m, R, lam)."""
    Rn, n = r.shape
    B = S.ON_COURT * dur
    ww = np.broadcast_to(w, (Rn, n)) if w.ndim == 1 else w
    big = np.where(act, np.maximum(r / ww, (dur[:, None] - r) / ww) + 1.0, 0.0).max(axis=1) if n else np.zeros(Rn)
    lo = -np.maximum(big, rR / wR + 1.0) - 1.0
    hi = (B - rR) / wR + np.where(n > 0, big, 0.0) + 1.0
    hi = np.maximum(hi, (B - np.minimum(rR, B)) / wR + 1.0)

    def g(lam):
        m = np.where(act, np.clip(r + lam[:, None] * ww, 0.0, dur[:, None]), 0.0)
        return m.sum(axis=1) + np.maximum(0.0, rR + lam * wR) - B

    for _ in range(QP_ITERS):
        mid = 0.5 * (lo + hi)
        pos = g(mid) > 0
        hi = np.where(pos, mid, hi)
        lo = np.where(pos, lo, mid)
    lam = 0.5 * (lo + hi)
    v = r + lam[:, None] * ww
    free = act & (v > 0) & (v < dur[:, None])
    upper = act & (v >= dur[:, None])
    rfree = (rR + lam * wR) > 0
    num = B - (upper * dur[:, None]).sum(axis=1) - np.where(free, r, 0.0).sum(axis=1) - np.where(rfree, rR, 0.0)
    den = np.where(free, ww, 0.0).sum(axis=1) + np.where(rfree, wR, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lam_x = np.where(den > 0, num / den, lam)
    ok = (lam_x >= lo - 1e-9) & (lam_x <= hi + 1e-9)
    lam = np.where(ok, lam_x, lam)
    m = np.where(act, np.clip(r + lam[:, None] * ww, 0.0, dur[:, None]), 0.0)
    R = np.maximum(0.0, rR + lam * wR)
    e = m.sum(axis=1) + R - B
    if np.abs(e).max(initial=0.0) > CLOSURE_TOL:
        raise JMViolation(f"reconciliation closure error {np.abs(e).max()}")
    useR = R > 0
    R = np.where(useR, R - e, R)
    fix = np.flatnonzero(~useR & (e != 0))
    if len(fix):
        room = np.where(act[fix], np.where(e[fix, None] > 0, m[fix], dur[fix, None] - m[fix]), -1.0)
        j = room.argmax(axis=1)
        m[fix, j] -= e[fix]
    return m, np.maximum(R, 0.0), lam


# ============================================================================ game state
@dataclass
class JMInput:
    game_id: str
    team: np.ndarray
    scored: np.ndarray
    p_play: np.ndarray
    min_mu: np.ndarray
    min_sigma: np.ndarray
    tier: np.ndarray
    w: np.ndarray
    env: np.ndarray
    res_mean: np.ndarray        # per team (2,)
    res_pi: np.ndarray
    res_mu_pos: np.ndarray
    res_var: np.ndarray


@dataclass
class JMState:
    dur: np.ndarray
    x: np.ndarray
    M: np.ndarray               # sims x n_scored: reconciled minutes of each scored player in his forced-active world
    M1: np.ndarray              # sims x n_scored: JM1 raw independent Phase 1B minutes (no factor, no reconciliation)
    opp_deficit: np.ndarray     # unused (no opportunity term); kept for structural.log_mu_terms
    checks: dict
    diag: dict


def simulate_jm(g: JMInput, env: dict, params: dict, sims: int, diagnostics: bool = True) -> JMState:
    base = S.simulate_state(S.GameInput(g.game_id, g.team, g.scored, g.p_play, g.min_mu, g.min_sigma,
                                        np.ones((len(g.team), len(S.FAM))), g.env), env, sims, minutes=False,
                            seed_base=SEED_BASE)
    dur, x = base.dur, base.x
    e_dur = params["e_dur"]
    n = len(g.team)
    sc = np.flatnonzero(g.scored)
    U = np.random.default_rng(stream_seed(g.game_id, "participation")).random((sims, n))
    eps = np.random.default_rng(stream_seed(g.game_id, "minutes")).standard_normal((sims, n))
    gf = np.random.default_rng(stream_seed(g.game_id, "tier_factor")).standard_normal((sims, 2, 3))
    rr = np.random.default_rng(stream_seed(g.game_id, "reservoir"))
    any_u = rr.random((sims, 2))
    gam = rr.standard_gamma(1.0 / params["reservoir"]["cv2_pos"], size=(sims, 2)) * params["reservoir"]["cv2_pos"]
    z = latent_minutes(params["tier_factor"], g.tier, g.team, gf, eps)
    raw = raw_minutes(g.min_mu, g.min_sigma, z, dur, e_dur)
    raw1 = raw_minutes(g.min_mu, g.min_sigma, eps, dur, e_dur)
    act = U < g.p_play[None, :]
    rR_all = np.where(any_u < g.res_pi[None, :], g.res_mu_pos[None, :] * gam, 0.0)
    rR_all = np.minimum(rR_all, S.ON_COURT * dur[:, None])
    wR = np.maximum(g.res_var, WR_FLOOR)
    Mi = np.zeros((sims, len(sc)))
    checks = {"team_draws": 0, "team_minutes_violations": 0, "minutes_bound_violations": 0, "inactive_minutes_nonzero": 0,
              "reservoir_negative": 0, "forced_rows": 0, "activation_repairs": 0}
    diag = {"rows": [], "teams": [], "pair_acc": {}, "sigma1_max_rel": 0.0, "act_freq": [], "p_play": []}
    cdf = pmf_cdf(g.min_mu, g.min_sigma)
    m40 = dur == S.REGULATION
    for t in (0, 1):
        idx = np.flatnonzero(g.team == t)
        a_t, r_t, w_t = act[:, idx], raw[:, idx], g.w[idx]
        rR = rR_all[:, t]
        wRv = np.full(sims, wR[t])
        m, R, lam = reconcile(r_t, w_t, a_t, dur, rR, wRv)
        _check(checks, m, R, a_t, dur)
        if diagnostics:
            diag["act_freq"].append(a_t.mean(axis=0))
            diag["p_play"].append(g.p_play[idx])
            diag["teams"].append({"team": t, "R_mean": float(R.mean()), "R_q50": float(np.quantile(R, 0.5)),
                                  "R_q90": float(np.quantile(R, 0.9)), "R_q95": float(np.quantile(R, 0.95)),
                                  "R_q99": float(np.quantile(R, 0.99)), "R_max": float(R.max()),
                                  "R_pos_share": float((R > 1e-9).mean()), "rR_mean": float(rR.mean()),
                                  "res_mean_pred": float(g.res_mean[t]),
                                  "n_active_lt5_share": float((a_t.sum(axis=1) < S.ON_COURT).mean()),
                                  "sum_p_min_mu": float((g.p_play[idx] * g.min_mu[idx]).sum())})
            if m40.sum() > 10:
                Mt = np.column_stack([m[m40], R[m40]])
                Cv = np.cov(Mt.T)
                diag["sigma1_max_rel"] = max(diag["sigma1_max_rel"], float(np.abs(Cv.sum(axis=1)).max()
                                                                           / max(np.abs(np.diag(Cv)).max(), 1e-12)))
                zs = normal_score(m[m40], cdf[idx])
                pair_tier_sums(zs, g.tier[idx], a_t[m40], np.zeros(len(idx), int), diag["pair_acc"])
        for k_i, i in enumerate(sc):
            if g.team[i] != t:
                continue
            j = int(np.flatnonzero(idx == i)[0])
            off = ~a_t[:, j]
            M_i = m[:, j].copy()
            if off.any():
                af = a_t[off].copy()
                af[:, j] = True
                mf, Rf, _ = reconcile(r_t[off], w_t, af, dur[off], rR[off], wRv[off])
                _check(checks, mf, Rf, af, dur[off])
                M_i[off] = mf[:, j]
                checks["forced_rows"] += int(off.sum())
            Mi[:, k_i] = M_i
            if diagnostics:
                d_ = np.abs(M_i - r_t[:, j])
                diag["rows"].append({"k": k_i, "raw_mean": float(r_t[:, j].mean()), "raw_sd": float(r_t[:, j].std()),
                                     "rec_mean": float(M_i.mean()), "rec_sd": float(M_i.std()),
                                     "disp_mean": float(d_.mean()), "disp_p50": float(np.quantile(d_, 0.5)),
                                     "disp_p90": float(np.quantile(d_, 0.9)), "disp_p95": float(np.quantile(d_, 0.95)),
                                     "disp_p99": float(np.quantile(d_, 0.99)), "disp_max": float(d_.max()),
                                     "rec_at_D_share": float((M_i >= dur - 1e-9).mean()),
                                     "res_team_mean": float(R.mean())})
    M1 = raw1[:, sc]
    return JMState(dur, x, Mi, M1, np.zeros((sims, len(sc), len(S.FAM))), checks, diag)


def _check(checks, m, R, act, dur):
    checks["team_draws"] += m.shape[0]
    checks["team_minutes_violations"] += int((np.abs(m.sum(axis=1) + R - S.ON_COURT * dur) > CLOSURE_TOL).sum())
    checks["minutes_bound_violations"] += int(((m < 0) | (m > dur[:, None] + CLOSURE_TOL)).sum())
    checks["inactive_minutes_nonzero"] += int((m[~act] != 0).sum())
    checks["reservoir_negative"] += int((R < 0).sum())


# ============================================================================ parameters (training seasons only)
def correlation_targets(T: int) -> dict:
    """Empirical pooled tier-pair correlations of OOS Phase 1B minutes normal scores of PLAYED teammates in regulation
    games (realized game_minutes = 40), Phase 1B OOS rows 2022-23..T (training labels)."""
    seasons = list(range(P3.OOS_FIRST, T + 1))
    st = load_store(seasons)
    st = st[st["participation_status"].eq("PLAYED") & st["y_min"].notna()]
    tf = S.load_team_frame(seasons, labels=True)[["game_id", "team_id", "game_minutes"]]
    st = st.merge(tf, on=["game_id", "team_id"], how="inner")
    st = st[st["game_minutes"] == S.REGULATION]
    return empirical_pairs(st)


def empirical_pairs(st: pd.DataFrame) -> dict:
    zs = normal_score(st["y_min"].to_numpy(dtype=float), pmf_cdf(st["min_mu"], st["min_sigma"]))
    d = pd.DataFrame({"g": st["game_id"].to_numpy(), "t": st["team_id"].to_numpy(),
                      "tier": tier_of(st["role_diagnostic"]), "z": zs, "rid": np.arange(len(st))})
    m2 = d.merge(d, on=["g", "t"])
    m2 = m2[m2["rid_x"] < m2["rid_y"]]                            # unordered teammate pairs, no self pairs
    a = np.minimum(m2["tier_x"], m2["tier_y"]).to_numpy()
    b = np.maximum(m2["tier_x"], m2["tier_y"]).to_numpy()
    acc = {}
    for (aa, bb), grp in pd.DataFrame({"a": a, "b": b, "x": m2["z_x"].to_numpy(), "y": m2["z_y"].to_numpy()}).groupby(["a", "b"]):
        acc[(int(aa), int(bb))] = np.array([float((grp.x * grp.y).sum()), float((grp.x ** 2).sum()),
                                            float((grp.y ** 2).sum()), float(len(grp))])
    return pooled_corr(acc)


def compensate_factor(T: int, target: dict, wfit: dict, e_dur: float, log=print) -> dict:
    """Constraint compensation on the TRAINING season T only: simulate reconciliation for a fixed sample of regulation
    team-games of T with the REALIZED participants active (training labels) and the realized out-of-universe minutes as
    the reservoir, and move the latent tier matrix so the simulated post-reconciliation tier correlations match the
    empirical ones (CORR_CAL_ITERS fixed-point steps, PSD-projected)."""
    st = load_store([T])
    st = st[st["participation_status"].eq("PLAYED") & st["y_min"].notna()]
    tf = S.load_team_frame([T], labels=True)[["game_id", "team_id", "game_minutes"]]
    st = st.merge(tf, on=["game_id", "team_id"], how="inner")
    st = st[st["game_minutes"] == S.REGULATION]
    rf = reservoir_frame([T], labels=True)[["game_id", "team_id", "res_minutes"]]
    keys = st[["game_id", "team_id"]].drop_duplicates().sort_values(["game_id", "team_id"])
    keys = keys.iloc[np.random.default_rng(SEED_BASE).choice(len(keys), min(CORR_CAL_TEAM_GAMES, len(keys)), replace=False)]
    keys = keys.merge(rf, on=["game_id", "team_id"], how="left").fillna({"res_minutes": 0.0})
    groups = {k: g for k, g in st.groupby(["game_id", "team_id"])}
    Ct = corr_matrix(target, CORR_SHRINK_N0)
    Cl, rep0 = project_factor(Ct)
    hist = []
    reps = int(rep0)
    for it in range(CORR_CAL_ITERS + 1):
        acc = {}
        for kk in keys.itertuples(index=False):
            g = groups[(kk.game_id, kk.team_id)]
            n = len(g)
            rng = np.random.default_rng(stream_seed(kk.game_id + "|" + kk.team_id, "calibration"))
            gf = rng.standard_normal((CORR_CAL_DRAWS, 2, 3))
            eps = rng.standard_normal((CORR_CAL_DRAWS, n))
            tier = tier_of(g["role_diagnostic"])
            z = latent_minutes(Cl, tier, np.zeros(n, int), gf, eps)
            dur = np.full(CORR_CAL_DRAWS, S.REGULATION)
            mu, sg = g["min_mu"].to_numpy(dtype=float), g["min_sigma"].to_numpy(dtype=float)
            r = raw_minutes(mu, sg, z, dur, e_dur)
            act = np.ones((CORR_CAL_DRAWS, n), bool)
            res = min(float(kk.res_minutes), S.ON_COURT * S.REGULATION)
            m, R, _ = reconcile(r, weights_of(g["role_diagnostic"], g["cold_start_class"], wfit), act, dur,
                                np.full(CORR_CAL_DRAWS, res), np.full(CORR_CAL_DRAWS, WR_FLOOR))
            pair_tier_sums(normal_score(m, pmf_cdf(mu, sg)), tier, act, np.zeros(n, int), acc)
        sim = pooled_corr(acc)
        Cs = corr_matrix(sim)
        err = float(np.sqrt(np.mean([(target[k]["corr"] - sim[k]["corr"]) ** 2 for k in target if k in sim])))
        hist.append({"iteration": it, "latent": Cl.tolist(), "simulated": {k: v["corr"] for k, v in sim.items()},
                     "rmse_vs_target": err})
        log(f"tier-factor compensation step {it}: rmse {err:.4f}")
        if it == CORR_CAL_ITERS:
            break
        Cl, rep = project_factor(Cl + (Ct - Cs))
        reps += int(rep)
    return {"target_empirical": target, "target_matrix_shrunk": Ct.tolist(), "latent_factor": Cl.tolist(),
            "psd_repairs": reps, "history": hist, "team_games": int(len(keys)), "draws": CORR_CAL_DRAWS,
            "season": T, "note": "realized participants / out-of-universe minutes of the TRAINING season are labels here"}


def fit_parameters(T: int, resid_seasons, log=print) -> dict:
    struct, s4, resid, accs = P3.build_procedure(T, resid_seasons, log, terms=JM_TERMS)
    if max(resid["forecast_season"]) > T:
        raise JMViolation("a season after T entered the residual fit")
    e_dur = expected_duration(struct["env"]["overtime"]["p"])
    log("reservoir model")
    res = fit_reservoir(T)
    log("reliability weights")
    wfit = fit_weights(T)
    log("tier correlation targets")
    tgt = correlation_targets(T)
    comp = compensate_factor(T, tgt, wfit, e_dur, log)
    return {"T": T, "struct": struct, "s4": s4, "resid_rows": int(len(resid)), "e_dur": e_dur, "reservoir": res,
            "weights": wfit, "tier_factor": np.asarray(comp["latent_factor"]), "tier_factor_fit": comp}


# ============================================================================ evaluation
def season_inputs(F: int, params: dict):
    df, envp = P3.season_inputs(F, params["struct"])
    rf = reservoir_frame([F], labels=False)
    rp = reservoir_predict(rf, params["reservoir"])
    return df, envp, rp, reservoir_pit_audit(rf)


def jm_input(gid, gp: pd.DataFrame, envd: dict, resd: dict, params: dict) -> JMInput:
    gi = P3._game_input(gid, gp, envd)
    tids = [gp["team_id"].min(), gp["team_id"].max()]
    rs = [resd.get((gid, t)) for t in tids]
    if any(r is None for r in rs):
        raise JMViolation(f"{gid}: reservoir features missing")
    return JMInput(gid, gi.team, gi.scored, gi.p_play, gi.min_mu, gi.min_sigma, tier_of(gp["role_diagnostic"]),
                   weights_of(gp["role_diagnostic"], gp["cold_start_class"], params["weights"]), gi.env,
                   np.array([r["res_mean"] for r in rs]), np.array([r["res_pi"] for r in rs]),
                   np.array([r["res_mu_pos"] for r in rs]), np.array([r["res_var"] for r in rs]))


def eval_game(gid, gp, envd, resd, params, s0_struct, s4_struct, scale, sims=SIMS, arms=ARMS, diagnostics=True) -> dict:
    """Every arm of one game from shared random numbers (Phase 3 evaluation conventions: single-player quantities in the
    player's forced-active world; the joint vector couples those worlds through common random numbers)."""
    gi = jm_input(gid, gp, envd, resd, params)
    rows = gp[gp["_scored"]].reset_index(drop=True)
    n = len(rows)
    y = rows[[f"y_{s}" for s in L.STATS]].to_numpy(dtype=float)
    team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
    groups = list(D.group_of(rows))
    marg0 = J.game_marginals(rows)
    E = np.random.default_rng(D.game_seed(J.SIM_SEED, gid)).standard_normal((sims, n * J.NS))
    gc0 = D.game_correlation(s0_struct, groups, team)
    L0, jit0 = D.cholesky_unit(gc0["corr"])
    gc4 = D.game_correlation(s4_struct, groups, team)
    L4, jit4 = D.cholesky_unit(gc4["corr"])
    z0, z4 = E @ L0.T, E @ L4.T
    st = simulate_jm(gi, params["struct"]["env"], params, sims, diagnostics)
    base_lm = np.log(rows[[f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float))
    min_mu = rows["min_mu"].to_numpy(dtype=float)
    phic = np.array([params["s4"]["dispersion"][s]["phi"] for s in L.STATS])
    stat_idx = np.tile(np.arange(J.NS), n)
    yv = y.ravel()
    ymin = rows["y_min"].to_numpy(dtype=float)
    draws, single, mins, info, anchors = {}, {}, {}, {}, {}
    pmf_min0 = P.minutes_pmf(min_mu, rows["min_sigma"].to_numpy(dtype=float), KMAX)
    glm = params["struct"]["glm"]
    for a in arms:
        if a == "S0":
            draws[a] = J.counts_from_latent(z0, marg0["thr"]).reshape(sims, n, J.NS)
            single[a] = P3.single_metrics(marg0["pmf"].reshape(n * J.NS, -1), yv, stat_idx)
            mins[a] = P3.minutes_metrics(pmf_min0, ymin)
            info[a] = {"min_eig": gc0["min_eig"], "invalid": gc0["invalid"], "jitter": jit0}
            continue
        Mx = st.M1 if a == "JM1" else st.M
        alpha = JM1_ALPHA if a == "JM1" else ALPHAS[a]
        sv = S.State(st.dur, st.x, Mx, st.opp_deficit, np.zeros((sims, 0)), {}, np.zeros((n, len(S.FAM))), 0.0)
        lm_s = S.log_mu_terms(base_lm, min_mu, team, sv, glm, JM_TERMS)
        lm, m_struct = S.anchor(lm_s, base_lm, alpha)
        anchors[a] = m_struct
        tab = S.mixture_tables(lm.reshape(sims, n * J.NS), phic)
        draws[a] = S.counts(tab, z4).reshape(sims, n, J.NS)
        single[a] = P3.single_metrics(S.mixture_pmf(tab, sims), yv, stat_idx)
        info[a] = {"min_eig": gc4["min_eig"], "invalid": gc4["invalid"], "jitter": jit4}
        mr = np.clip(np.round(Mx), 0, KMAX).astype(np.int64)
        pm = np.stack([np.bincount(mr[:, i], minlength=KMAX + 1) for i in range(n)]) / sims
        mins[a] = P3.minutes_metrics(pm, ymin)
        del tab
    joint = joint_block(gid, n, team, rows, y, draws, single, info, marg0, sims, scale)
    return {"joint": joint["joint"], "single": single, "mins": mins, "events": joint["events"], "moments": joint["moments"],
            "checks": st.checks, "state": st, "anchors": anchors, "rows": rows}


def joint_block(gid, n, team, rows, y, draws, single, info, marg0, sims, scale) -> dict:
    """Phase 2 / Phase 3 joint scores, verbatim definitions (phase3.eval_game)."""
    masks = J.pair_masks(n, team)
    lines = J.combo_lines(marg0)
    ys = (y / scale).ravel()
    rl = J.roles(rows, team)
    h = sims // 2
    joint = {"game_id": gid, "n_players": n}
    mom = {}
    for a, x in draws.items():
        xs = (x.astype(float) / scale).reshape(sims, n * J.NS)
        va, vb = J.variogram(xs[:J.VS_DRAWS], ys, masks), J.variogram(xs[J.VS_DRAWS:2 * J.VS_DRAWS], ys, masks)
        joint.update({f"{a}|ES": J.energy_score(xs, ys), f"{a}|ES_rep_a": J.energy_score(xs[:h], ys),
                      f"{a}|ES_rep_b": J.energy_score(xs[h:], ys),
                      f"{a}|VS_BAL": 0.5 * (va["balanced"] + vb["balanced"]), f"{a}|VS_rep_a": va["balanced"],
                      f"{a}|VS_rep_b": vb["balanced"]})
        for k in ("within_player", "same_team", "cross_team", "uniform"):
            joint[f"{a}|VS_{k.upper()}"] = 0.5 * (va[k] + vb[k])
        cs = J.combo_scores(x, y, lines, sims)
        tot = 0.0
        for cb, v in cs.items():
            joint[f"{a}|{cb}_LOG"] = float(v["log"].sum())
            joint[f"{a}|{cb}_CRPS"] = float(v["crps"].sum())
            for li, q in enumerate(J.COMBO_LINE_Q):
                joint[f"{a}|{cb}_BRIER_q{int(q * 100)}"] = float(v["brier"][:, li].sum())
            tot += float(v["log"].sum())
        joint[f"{a}|COMBO_LOG"] = tot
        mean = single[a]["mean"].reshape(n, J.NS)
        sd = np.sqrt(np.maximum(single[a]["var"].reshape(n, J.NS), 1e-12))
        mom[a] = {"obs": J.moment_sums((y - mean) / sd, team), "sim": J.moment_sums((x - mean) / sd, team)}
    events = J.joint_events(draws, y, marg0, rl)
    joint["n_events"] = len(events)
    fl = 0.5 / sims
    for a in draws:
        joint[f"{a}|EVENT_BRIER"] = float(sum((e[f"p_{a}"] - e["outcome"]) ** 2 for e in events))
        joint[f"{a}|EVENT_BRIER_SHARED"] = float(sum((e[f"p_{a}"] - e["outcome"]) ** 2 for e in events
                                                     if e["event"] in P3.SHARED_EVENTS))
        joint[f"{a}|EVENT_LOG"] = float(sum(-np.log(np.clip(e[f"p_{a}"], fl, 1 - fl)) if e["outcome"] else
                                            -np.log(np.clip(1 - e[f"p_{a}"], fl, 1 - fl)) for e in events))
    joint["n_events_shared"] = sum(e["event"] in P3.SHARED_EVENTS for e in events)
    for a, x in draws.items():
        der = J.derived_markets(x[:50])
        joint[f"{a}|COMBO_IDENTITY_VIOLATIONS"] = int(sum(
            int((der[c] != x[:50][:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2)).sum())
            for c, parts in J.COMBOS.items()))
        joint[f"{a}|INVALID_MATRIX"] = int(info[a].get("invalid", False))
    return {"joint": joint, "events": events, "moments": mom}


def evaluate(F: int, params: dict, s0_struct, scale, tag: str, log=print, sims: int = SIMS, arms=ARMS,
             limit_games: int | None = None, df_override: pd.DataFrame | None = None) -> dict:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    df, envp, rp, res_pit = season_inputs(F, params) if df_override is None else df_override
    pit = S.pit_audit(envp)
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    resd = {(r["game_id"], r["team_id"]): r for r in rp.to_dict("records")}
    s4_struct = D.structure("J2", params["s4"]["residual_dependence"])
    games = list(df.groupby("game_id", sort=True))
    if limit_games:
        games = games[:limit_games]
    keys = ("log", "crps", "mean", "var", "pover0", "pover1", "pover2", "pit_lo", "pit_hi", "zc", "vz")
    mkeys = ("min_log", "min_crps", "min_mean", "min_sd", "min_median", "min_pit_lo", "min_pit_hi",
             "min_q05", "min_q10", "min_q25", "min_q75", "min_q90", "min_q95")
    cell_parts = {a: {k: [] for k in keys} for a in arms}
    min_parts = {a: {k: [] for k in mkeys} for a in arms}
    row_parts, joint_rows, ev_rows, diag_rows, team_rows = [], [], [], [], []
    obs_m = {a: [] for a in arms}
    sim_m = {a: np.zeros((3, J.NS, J.NS)) for a in arms}
    pairs_n, act_f, pp_f = [], [], []
    pair_acc = {}
    sig1 = 0.0
    checks = {}
    t0 = time.time()
    for gi_, (gid, gp) in enumerate(games):
        r = eval_game(gid, gp, envd, resd, params, s0_struct, s4_struct, scale, sims, arms)
        rows = r["rows"]
        n = len(rows)
        meta = rows[["forecast_season", "game_id", "team_id", "player_id", "player_game_example_id", "role_diagnostic",
                     "position_group", "cold_start_class", "p_play", "min_mu", "min_sigma", "y_min",
                     "prediction_cutoff_utc"] + [f"y_{s}" for s in L.STATS]].copy()
        row_parts.append(meta)
        for a in arms:
            for k in keys:
                cell_parts[a][k].append(r["single"][a][k].astype(np.float32))
            for k in mkeys:
                min_parts[a][k].append(r["mins"][a][k].astype(np.float32))
            mo = r["moments"][a]
            obs_m[a].append(np.stack([mo["obs"]["within_player"], mo["obs"]["same_team"], mo["obs"]["cross_team"]]))
            sim_m[a] += np.stack([mo["sim"]["within_player"], mo["sim"]["same_team"], mo["sim"]["cross_team"]])
        team = (rows["team_id"] != rows["team_id"].min()).astype(int).to_numpy()
        nt = [(team == t).sum() for t in (0, 1)]
        pairs_n.append([n, nt[0] * (nt[0] - 1) + nt[1] * (nt[1] - 1), 2 * nt[0] * nt[1]])
        joint_rows.append(r["joint"])
        for e in r["events"]:
            ev_rows.append({"game_id": gid, **e})
        for k, v in r["checks"].items():
            checks[k] = checks.get(k, 0) + v
        st = r["state"]
        dg = pd.DataFrame(st.diag["rows"]).sort_values("k")
        dg["player_game_example_id"] = rows["player_game_example_id"].to_numpy()[dg["k"].to_numpy()]
        diag_rows.append(dg.drop(columns="k"))
        tids = [gp["team_id"].min(), gp["team_id"].max()]
        for tr in st.diag["teams"]:
            team_rows.append({"game_id": gid, "team_id": tids[tr["team"]], **{k: v for k, v in tr.items() if k != "team"}})
        for k, v in st.diag["pair_acc"].items():
            pair_acc[k] = pair_acc.get(k, np.zeros(4)) + v
        sig1 = max(sig1, st.diag["sigma1_max_rel"])
        act_f.append(np.concatenate(st.diag["act_freq"]))
        pp_f.append(np.concatenate(st.diag["p_play"]))
        if (gi_ + 1) % 100 == 0 or gi_ + 1 == len(games):
            el = time.time() - t0
            log(f"[{tag}] {gi_ + 1}/{len(games)} games  {el / 60:.1f} min  eta {(len(games) - gi_ - 1) * el / (gi_ + 1) / 60:.1f} min")
        del r
        if (gi_ + 1) % 500 == 0:
            gc.collect()
    meta = pd.concat(row_parts, ignore_index=True)
    cells = {a: {k: np.concatenate(v) for k, v in cell_parts[a].items()} for a in arms}
    mins = {a: {k: np.concatenate(v) for k, v in min_parts[a].items()} for a in arms}
    gt = pd.DataFrame(joint_rows)
    ev = pd.DataFrame(ev_rows)
    cols = {}
    for a in arms:
        for k in keys:
            v = cells[a][k].reshape(len(meta), J.NS)
            for j, s in enumerate(L.STATS):
                cols[f"{a}|{s}|{k}"] = v[:, j]
        for k in mkeys:
            cols[f"{a}|{k}"] = mins[a][k]
    wide = pd.concat([meta, pd.DataFrame(cols)], axis=1)
    rd = pd.concat(diag_rows, ignore_index=True)
    td = pd.DataFrame(team_rows)
    wide = wide.merge(rd, on="player_game_example_id", how="left", validate="one_to_one")
    wide = wide.merge(rp[["game_id", "team_id", "team_games", "res_mean"]], on=["game_id", "team_id"], how="left")
    wide = wide.merge(td[["game_id", "team_id", "sum_p_min_mu"]], on=["game_id", "team_id"], how="left")
    wide.to_parquet(SCRATCH / f"{tag}_rows.parquet", index=False)
    gt.to_parquet(SCRATCH / f"{tag}_games.parquet", index=False)
    ev.to_parquet(SCRATCH / f"{tag}_events.parquet", index=False)
    td.to_parquet(SCRATCH / f"{tag}_teams.parquet", index=False)
    om = {a: np.stack(obs_m[a]) for a in arms}
    af, pf = np.concatenate(act_f), np.concatenate(pp_f)
    out = {"wide": wide, "gt": gt, "ev": ev, "om": om, "sim_m": sim_m, "pairs_n": np.asarray(pairs_n, dtype=float),
           "df": df, "teams": td, "pair_acc": pair_acc,
           "accounting": checks | {"ACCOUNTING_VIOLATIONS": checks.get("team_minutes_violations", 0)
                                   + checks.get("minutes_bound_violations", 0) + checks.get("inactive_minutes_nonzero", 0)
                                   + checks.get("reservoir_negative", 0),
                                   "COMBO_IDENTITY_VIOLATIONS": int(sum(gt[f"{a}|COMBO_IDENTITY_VIOLATIONS"].sum() for a in arms)),
                                   "INVALID_CORRELATION_MATRICES": {a: int(gt[f"{a}|INVALID_MATRIX"].sum()) for a in arms},
                                   "SIGMA_M_1_MAX_RELATIVE_AT_FIXED_DURATION": sig1},
           "participation_post_feasibility": {"candidates": int(len(af)), "mean_activation": float(af.mean()),
                                              "mean_p_play": float(pf.mean()),
                                              "max_abs_activation_minus_p": float(np.abs(af - pf).max()),
                                              "activation_repairs": checks.get("activation_repairs", 0),
                                              "note": "independent Bernoulli(P(play)); no activation rule"},
           "pit_audit_team_features": pit, "pit_audit_reservoir_features": res_pit, "draws_per_game": sims}
    return out


# ============================================================================ summaries
def strata_of(wide: pd.DataFrame, df: pd.DataFrame, realized_res: pd.DataFrame | None, e_dur: float) -> dict:
    role = wide["role_diagnostic"].to_numpy()
    pos = wide["position_group"].to_numpy()
    cold = wide["cold_start_class"].fillna("UNKNOWN").to_numpy()
    month = pd.to_datetime(wide["prediction_cutoff_utc"], utc=True).dt.tz_convert("America/New_York").dt.month.to_numpy()
    resm = wide["res_mean"].to_numpy(dtype=float)
    gap = wide["sum_p_min_mu"].to_numpy(dtype=float) + resm
    rc = P3.role_change_flags(wide, df)
    s = {"ALL": None, **{f"ROLE:{r}": role == r for r in ROLES4},
         **{f"POS:{p}": pos == p for p in ("G", "F", "C", "U")},
         **{f"COLD:{c}": cold == c for c in ("WARM", "COLD_EARLY_SEASON", "COLD_TRANSFER_IN", "COLD_NO_HISTORY")},
         "COLD_START": np.isin(cold, ("COLD_NO_HISTORY", "COLD_EARLY_SEASON")), "TRANSFER": cold == "COLD_TRANSFER_IN",
         "RETURNER": cold == "WARM", "ROTATION_OR_BENCH": np.isin(role, ("ROTATION_6_8", "BENCH_9PLUS")),
         "EARLY_SEASON": wide["team_games"].to_numpy(dtype=float) <= EARLY_TEAM_GAMES, "MONTH:NOV": month == 11,
         "RESERVOIR_HEAVY": resm >= RESERVOIR_HEAVY_MIN,
         "NEAR_BUDGET": (resm < RESERVOIR_HEAVY_MIN) & (np.abs(gap - S.ON_COURT * e_dur) <= NEAR_BUDGET_MIN),
         "PREGAME_EXPECTED_ROTATION_CHANGE": rc["pregame"], "REALIZED_TOP5_ABSENCE_DIAGNOSTIC": rc["realized"]}
    if realized_res is not None:
        k = wide[["game_id", "team_id"]].merge(realized_res, on=["game_id", "team_id"], how="left")["res_minutes"].fillna(0)
        s["REALIZED_INCOMPLETE_DIAGNOSTIC"] = k.to_numpy() >= 20
        s["REALIZED_COMPLETE_DIAGNOSTIC"] = k.to_numpy() < 20
    return {k: v for k, v in s.items() if v is None or v.any()}


def summarize(ev_out: dict, arms, sims, scale, e_dur: float, realized_res=None) -> dict:
    wide, gt, ev, df = ev_out["wide"], ev_out["gt"], ev_out["ev"], ev_out["df"]
    G = len(gt)
    gidx = pd.Index(gt["game_id"]).get_indexer(wide["game_id"])
    W = P3._boot_weights(G)
    sc = dict(zip(L.STATS, np.asarray(scale, dtype=float).tolist()))
    strata = strata_of(wide, df, realized_res, e_dur)
    cells = {st: {a: P3.cell_arrays(wide, a, gidx, G, sc, m) for a in arms} for st, m in strata.items()}
    mins = {st: {a: P3.minutes_arrays(wide, a, gidx, G, m) for a in arms} for st, m in strata.items()}
    jo = {a: P3.joint_arrays(gt, a) for a in arms}
    pairs = [(a, "S0") for a in arms if a != "S0"] + [(a, "JM1") for a in ALPHAS if "JM1" in arms] + [("JM2_A05", "JM2_A10")]
    pairs = [p for p in pairs if p[0] in arms and p[1] in arms]
    levels, deltas, min_levels, min_deltas = {}, {}, {}, {}
    for st in cells:
        levels[st] = {a: {k: P3._ratio_ci(*v, W) for k, v in cells[st][a].items()} for a in arms}
        min_levels[st] = {a: {k: P3._ratio_ci(*v, W) for k, v in mins[st][a].items()} for a in arms}
        for a in arms:
            min_levels[st][a]["RMSE"] = float(np.sqrt(min_levels[st][a]["MIN_SE_MEAN"]["value"]))
        for a, b in pairs:
            deltas.setdefault(f"{a}-{b}", {})[st] = {k: P3._ratio_ci(cells[st][a][k][0] - cells[st][b][k][0], cells[st][a][k][1], W)
                                                     for k in cells[st][a]}
            min_deltas.setdefault(f"{a}-{b}", {})[st] = {k: P3._ratio_ci(mins[st][a][k][0] - mins[st][b][k][0], mins[st][a][k][1], W)
                                                         for k in mins[st][a]}
    jl = {a: {k: P3._ratio_ci(*v, W) for k, v in jo[a].items()} for a in arms}
    jd = {f"{a}-{b}": {k: P3._ratio_ci(jo[a][k][0] - jo[b][k][0], jo[a][k][1], W) for k in jo[a]} for a, b in pairs}
    mc = {}
    for a, b in pairs:
        for m_, ka, kb in (("ES", "ES_rep_a", "ES_rep_b"), ("VS_BAL", "VS_rep_a", "VS_rep_b")):
            dg = (gt[f"{a}|{ka}"] - gt[f"{b}|{ka}"]) - (gt[f"{a}|{kb}"] - gt[f"{b}|{kb}"])
            mc[f"{a}-{b}|{m_}"] = float(dg.std(ddof=1) / np.sqrt(G) / 2)
    lam = {st: {a: P3.latent_scale(wide, a, m) for a in arms} for st, m in strata.items()
           if st.startswith("ROLE:") or st in ("ALL", "EARLY_SEASON", "RESERVOIR_HEAVY")}
    calib = {a: {s: P3.pit_calibration(wide, a, s) for s in L.STATS} for a in arms}
    min_cal = {a: P3.minutes_calibration(wide, a) for a in arms}
    min_cal_strata = {st: {a: P3.minutes_calibration(wide[m] if m is not None else wide, a) for a in arms}
                      for st, m in strata.items() if st.startswith(("ROLE:", "COLD:")) or st in
                      ("ALL", "EARLY_SEASON", "RESERVOIR_HEAVY", "NEAR_BUDGET", "REALIZED_INCOMPLETE_DIAGNOSTIC")}
    bias = {st: {a: {s: float((wide.loc[m if m is not None else slice(None), f"{a}|{s}|mean"]
                               - wide.loc[m if m is not None else slice(None), f"y_{s}"]).mean()) for s in L.STATS}
                 for a in arms} for st, m in strata.items() if st in ("ALL",) or st.startswith("ROLE:")}
    return {"games": G, "player_games": int(len(wide)), "levels": levels, "deltas": deltas,
            "minutes_levels": min_levels, "minutes_deltas": min_deltas, "joint_levels": jl, "joint_deltas": jd,
            "mc_se": mc, "latent_scale_lam": lam, "pit_calibration": calib, "minutes_calibration": min_cal,
            "minutes_calibration_strata": min_cal_strata, "stat_bias_pred_minus_obs": bias,
            "events_by_family": P3.event_families(ev, arms, W, gt, sims),
            "correlation_calibration": P3.correlation_calibration(ev_out["om"], ev_out["sim_m"], ev_out["pairs_n"], arms),
            "strata_sizes": {st: int(len(wide) if m is None else m.sum()) for st, m in strata.items()},
            "descriptive_scale": sc, "participation": P3.participation_metrics(df)}


def reconciliation_summary(wide: pd.DataFrame, teams: pd.DataFrame) -> dict:
    """Displacement |M - raw| (forced-active world, per row quantiles over draws, pooled by mean of the row statistics)
    and the distortion of the reconciled minutes marginal from the Phase 1B conditional marginal."""
    def blk(d):
        return {"n": int(len(d)), **{k: float(d[f"disp_{k}"].mean()) for k in ("mean", "p50", "p90", "p95", "p99")},
                "max": float(d["disp_max"].max()), "raw_mean": float(d["raw_mean"].mean()),
                "rec_mean": float(d["rec_mean"].mean()), "phase1b_min_mu_mean": float(d["min_mu"].mean()),
                "mean_abs_rec_minus_raw_mean": float((d["rec_mean"] - d["raw_mean"]).abs().mean()),
                "sd_ratio_rec_over_raw": float(d["rec_sd"].mean() / max(d["raw_sd"].mean(), 1e-12)),
                "rec_at_duration_share": float(d["rec_at_D_share"].mean())}
    out = {"ALL": blk(wide), "by_role": {r: blk(g) for r, g in wide.groupby("role_diagnostic")},
           "by_cold": {c: blk(g) for c, g in wide.groupby("cold_start_class")},
           "definition": "per scored player-game: quantiles over draws of |M_reconciled - M_raw| in the forced-active "
                         "world; reported values are the means over player-games of those per-row quantiles (max: max)"}
    t = teams.copy()
    t["month"] = pd.to_datetime(t["game_id"].map(dict(zip(wide["game_id"], wide["prediction_cutoff_utc"]))),
                                utc=True).dt.tz_convert("America/New_York").dt.month
    res = {"team_games": int(len(t)), **{f"R_{k}": float(t[f"R_{k}"].mean()) for k in ("mean", "q50", "q90", "q95", "q99")},
           "R_max": float(t["R_max"].max()), "R_positive_draw_share": float(t["R_pos_share"].mean()),
           "teams_with_mean_R_ge_10": int((t["R_mean"] >= 10).sum()),
           "by_month": {int(m): {"team_games": int(len(g)), "R_mean": float(g["R_mean"].mean()),
                                 "share_mean_R_ge_10": float((g["R_mean"] >= 10).mean())} for m, g in t.groupby("month")},
           "lt5_known_active_draw_share_mean": float(t["n_active_lt5_share"].mean()),
           "definition": "per team-game distribution over draws of the base-world reservoir minutes R; pooled by mean"}
    return {"displacement": out, "reservoir": res}


def minutes_covariance(pair_acc: dict, F: int) -> dict:
    """Simulated (base world, both active, 40-minute draws) vs empirical (realized season F, both PLAYED, regulation
    games) pooled tier-pair correlations of Phase 1B normal scores."""
    sim = pooled_corr(pair_acc)
    st = load_store([F])
    st = st[st["participation_status"].eq("PLAYED") & st["y_min"].notna()]
    tf = S.load_team_frame([F], labels=True)[["game_id", "team_id", "game_minutes"]]
    st = st.merge(tf, on=["game_id", "team_id"], how="inner")
    emp = empirical_pairs(st[st["game_minutes"] == S.REGULATION])
    keys = sorted(set(sim) & set(emp))
    diff = np.array([sim[k]["corr"] - emp[k]["corr"] for k in keys])
    return {"simulated": sim, "empirical": emp, "rmse": float(np.sqrt((diff ** 2).mean())),
            "max_abs_error": float(np.abs(diff).max()), "sign_agreement": int(sum(np.sign(sim[k]["corr"]) == np.sign(emp[k]["corr"]) for k in keys)),
            "pairs": len(keys)}


# ============================================================================ development decision (frozen)
def _hi(d) -> float:
    return d["hi"]


def select_alpha(summ: dict) -> dict:
    v = {a: summ["levels"]["ALL"][a]["MARG_LOG"]["value"] for a in ALPHAS}
    best = min(sorted(v), key=lambda a: (v[a], -ALPHAS[a]))
    return {"MARG_LOG": v, "selected_arm": best, "alpha": ALPHAS[best]}


def gates(summ: dict, cand: str, acc: dict, extra: dict) -> dict:
    d, jd, md = summ["deltas"][f"{cand}-S0"], summ["joint_deltas"][f"{cand}-S0"], summ["minutes_deltas"][f"{cand}-S0"]
    out = {}
    out["G1_minutes_crps_overall"] = {"delta": md["ALL"]["MIN_CRPS"], "margin": G_MIN_CRPS_ALL,
                                      "PASS": _hi(md["ALL"]["MIN_CRPS"]) <= G_MIN_CRPS_ALL}
    tier = {r: md[f"ROLE:{r}"]["MIN_CRPS"] for r in TIERS if f"ROLE:{r}" in md}
    out["G2_minutes_crps_tiers"] = {"deltas": tier, "margin": G_MIN_CRPS_TIER,
                                    "PASS": len(tier) == 3 and all(_hi(v) <= G_MIN_CRPS_TIER for v in tier.values())}
    g3 = {}
    for st, mg in (("EARLY_SEASON", G_MIN_CRPS_EARLY), ("MONTH:NOV", G_MIN_CRPS_EARLY), ("RESERVOIR_HEAVY", G_MIN_CRPS_RESERVOIR_HEAVY)):
        if st in md:
            g3[st] = {"delta": md[st]["MIN_CRPS"], "margin": mg, "PASS": _hi(md[st]["MIN_CRPS"]) <= mg}
    out["G3_early_season_incomplete_catastrophe_eliminated"] = {**g3, "PASS": len(g3) == 3 and all(v["PASS"] for v in g3.values())}
    out["G4_aggregate_marginal"] = {"MARG_LOG": d["ALL"]["MARG_LOG"], "MARG_CRPS_SCALED": d["ALL"]["MARG_CRPS_SCALED"],
                                    "margin": G_MARG_LOG_ALL, "PASS": _hi(d["ALL"]["MARG_LOG"]) <= G_MARG_LOG_ALL}
    stat = {s: d["ALL"][f"MARG_LOG|{s}"] for s in L.STATS}
    sr = {f"{s}|{r}": d[f"ROLE:{r}"][f"MARG_LOG|{s}"] for s in L.STATS for r in TIERS if f"ROLE:{r}" in d}
    out["G5_no_stat_family_deteriorates"] = {"stat": stat, "stat_x_role": sr, "margin_stat": G_MARG_LOG_STAT,
                                             "margin_stat_role": G_MARG_LOG_STAT_ROLE,
                                             "failures": [k for k, v in stat.items() if _hi(v) > G_MARG_LOG_STAT]
                                             + [k for k, v in sr.items() if _hi(v) > G_MARG_LOG_STAT_ROLE]}
    out["G5_no_stat_family_deteriorates"]["PASS"] = not out["G5_no_stat_family_deteriorates"]["failures"]
    mc_es = summ["mc_se"].get(f"{cand}-S0|ES")
    mc_vs = summ["mc_se"].get(f"{cand}-S0|VS_BAL")
    es_ok = _hi(jd["ES"]) <= G_ES
    vs_ok = _hi(jd["VS_BAL"]) <= G_VS
    near ={k: (abs(_hi(v) - mg) / se if se else None) for k, v, mg, se in (("ES", jd["ES"], G_ES, mc_es),
                                                                             ("VS_BAL", jd["VS_BAL"], G_VS, mc_vs))}
    mc_adequate = all(x is None or x >= P3.MC_DECISION_RATIO for x in near.values())
    out["G6_joint_scoring"] = {"ES": jd["ES"], "VS_BAL": jd["VS_BAL"], "margins": {"ES": G_ES, "VS_BAL": G_VS},
                               "bound_to_margin_in_mc_se": near, "MC_ADEQUATE": mc_adequate,
                               "PASS": bool(es_ok and vs_ok and mc_adequate)}
    ev = summ["events_by_family"].get("TM_SCORER_SCORER", {})
    if ev:
        gap0, gap1 = abs(ev["S0"]["calibration_gap_obs_minus_pred"]), abs(ev[cand]["calibration_gap_obs_minus_pred"])
        out["G7_both_top_scorers_over"] = {"observed": ev["observed_rate"], "S0_mean_p": ev["S0"]["mean_p"],
                                           f"{cand}_mean_p": ev[cand]["mean_p"], "brier_minus_S0": ev[cand]["brier_minus_S0"],
                                           "abs_gap_S0": gap0, f"abs_gap_{cand}": gap1,
                                           "PASS": bool(ev[cand]["brier_minus_S0"]["value"] < 0 and gap1 < gap0)}
    else:
        out["G7_both_top_scorers_over"] = {"PASS": False, "note": "event family missing"}
    out["G8_accounting_exact"] = {"ACCOUNTING_VIOLATIONS": acc["ACCOUNTING_VIOLATIONS"],
                                  "COMBO_IDENTITY_VIOLATIONS": acc["COMBO_IDENTITY_VIOLATIONS"],
                                  "INVALID_CORRELATION_MATRICES": acc["INVALID_CORRELATION_MATRICES"].get(cand, 0),
                                  "PASS": acc["ACCOUNTING_VIOLATIONS"] == 0 and acc["COMBO_IDENTITY_VIOLATIONS"] == 0
                                  and acc["INVALID_CORRELATION_MATRICES"].get(cand, 0) == 0}
    out["G9_universe_reservoir_point_in_time"] = {"reservoir_feature_pit_violations": extra["res_pit"]["violations"],
                                                  "legal_evidence_outside_universe": extra["universe"]["LEGAL_PIT_MEMBERSHIP_EVIDENCE_NOT_IN_UNIVERSE"],
                                                  "PASS": extra["res_pit"]["violations"] == 0}
    out["G10_no_pit_leakage"] = {"GENUINE_PIT_VIOLATIONS": extra["env_pit"]["violations"],
                                 "PASS": extra["env_pit"]["violations"] == 0}
    out["G11_deterministic"] = {"check": extra["determinism"], "PASS": bool(extra["determinism"]["identical"])}
    out["G12_2025_26_untouched"] = {"PASS": True, "enforced_by": "joint_minutes_v2.check_seasons (max season 2025 = "
                                                                 "2024-25) on every loader of this module"}
    out["ALL_PASS"] = bool(all(v["PASS"] for k, v in out.items() if k[:1] == "G"))
    return out


def determinism(F, params, s0_struct, scale, n_games=2) -> dict:
    df, envp, rp, _ = season_inputs(F, params)
    envd = {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}
    resd = {(r["game_id"], r["team_id"]): r for r in rp.to_dict("records")}
    s4s = D.structure("J2", params["s4"]["residual_dependence"])
    hs = []
    for _ in range(2):
        h = hashlib.sha256()
        for gid, gp in list(df.groupby("game_id", sort=True))[:n_games]:
            r = eval_game(gid, gp, envd, resd, params, s0_struct, s4s, scale, sims=5000)
            for a in ARMS:
                h.update(np.ascontiguousarray(r["single"][a]["log"]).tobytes())
                h.update(np.ascontiguousarray(r["state"].M).tobytes())
                h.update(json.dumps(r["joint"], sort_keys=True, default=_jd).encode())
        hs.append(h.hexdigest())
    return {"games": n_games, "draws": 5000, "hashes": hs, "identical": hs[0] == hs[1]}


# ============================================================================ protocol
def protocol() -> dict:
    return {
        "version": VERSION, "experiment": EXPERIMENT,
        "phase": "V2 JOINTMINUTESV2: joint team minutes anchored on Phase 1B + unmodeled-rotation reservoir + role-tier "
                 "factor + reliability-weighted bounded reconciliation",
        "frozen_before_full_development_evaluation": True,
        "pre_freeze_disclosure": (
            "2024-25 is DEVELOPMENT data: it was evaluated in Phase 3 (selection + rebuild) and in the read-only S2 audit, "
            "whose findings (incomplete early-season candidate universe, budget infeasibility, the old activation repair, "
            "role-tier minutes correlations of 2024-25 OOS residuals, rotation under-dispersion of the logit-normal) "
            "motivated this design. Before this freeze the implementation was debugged only mechanically on 2023-24 inputs "
            "with a synthetic P(play) (no predictive metric of any arm was inspected) and on unit tests. Reservoir, "
            "reliability weights and tier factor are FITTED on training seasons only (labels <= 2023-24). 2025-26 was "
            "not read."),
        "current_champion": "S0 = Phase 1B marginals + Phase 2 J2 (phase2_selection_fit_2023_24.json for this chronology)",
        "inputs": {"phase1b_oos_store": _rel(P2.STORE), "phase1b_oos_store_sha256": P3.STORE_SHA,
                   "phase1b_F2023_extension_sha256": P3.F2023_SHA, "MARGINAL_ARTIFACT_SHA256": P3.MARGINAL_SHA,
                   "PHASE2_DEPENDENCE_ARTIFACT_SHA256": P3.J2_SHA, "phase3_frozen_records": PHASE3_FROZEN,
                   "HISTORICAL_API_CALLS": 0},
        "arms": {"S0": "JM0 = champion: Phase 1B marginals + Phase 2 J2",
                 "JM1": "diagnostic: random Phase 1B conditional minutes (independent, no tier factor, no reconciliation, "
                        "no reservoir) through the JM2 GLM / refit dispersion / residual J2, alpha = 1.0",
                 "JM2_A05": "JointMinutesV2, structural means anchored alpha = 0.5 toward Phase 1B",
                 "JM2_A10": "JointMinutesV2, alpha = 1.0",
                 "alpha_rule": "alpha* = argmin aggregate MARG_LOG over JM2_A05 / JM2_A10 (tie -> 1.0); gates apply to alpha*"},
        "chronology": {"development": {"parameters_through": DEV["T"], "evaluate": DEV["eval"],
                                       "residual_basis_true_OOS_seasons": list(DEV["resid_seasons"]),
                                       "runs": "exactly once (a genuine, result-independent implementation bug is the "
                                               "only reason for a documented rerun)"},
                       "confirmation_2025_26": "NOT in this protocol: one locked retrospective confirmation, only after "
                                               "architect approval, parameters through 2024-25",
                       "2025_26_read": False},
        "candidate_universe": {"definition": "ex.player_game_examples = feat.player_features = Phase 1B candidate rows: "
                                             "every player with legal pregame evidence (prior appearance, prior-season "
                                             "returner / roster, preseason transfer / recruit)",
                               "completion_check": "ex.team_membership_evidence available_from <= cutoff adds 0 players; "
                                                   "ex.roster_snapshot_research is SEASON_ROSTER_ONLY_NOT_PIT_CONFIRMED "
                                                   "(not used); every lab.player_pool_misses row has legal_evidence = false",
                               "never_used_for_membership": ["target-game participants / box rows / minutes / DNP status"]},
        "reservoir": {"what": "aggregate stochastic unmodeled-rotation minutes R per team-game draw: absorbs team minutes "
                              "of players outside the candidate universe; never priced, never a player, no stats, no combos",
                      "features_pregame": list(RES_FEATURES),
                      "feature_source": "feat.player_features aggregated per team-game (n candidates, cold-start class "
                                        "shares, team games so far = max pp_team_games, returning-minutes share)",
                      "model": "P(R > 0) logistic; E[R | R > 0] log-linear (Poisson pseudo-likelihood); positive part "
                               "Gamma with pooled CV (moments); fitted on team-games 2013-14..T; label = realized minutes "
                               "of players outside the universe (lab.player_pool_misses, TRAINING label only)",
                      "draw": "R_raw = 1[u < pi] mu_pos Gamma(1/cv2, cv2), capped at 5 D",
                      "reliability": "w_R = max(Var(R), %g)" % WR_FLOOR},
        "minutes": {"participation": "independent Bernoulli(Phase 1B P(play)) per candidate; NO activation repair; "
                                     "fewer than five known players active is absorbed by the reservoir",
                    "raw": "z_i = f_(team, tier(i)) + sqrt(1 - Cf_tt) eps_i; raw_i = clip(min_mu + min_sigma z_i, 0, %d) "
                           "x D / E[D] (the Phase 1B discretized-normal conditional marginal sampled directly; no "
                           "role-specific scale correction)" % KMAX,
                    "tier_factor": "3 tiers (TOP5_MINUTES, ROTATION_6_8, BENCH_9PLUS; NO_ROLE_HISTORY -> bench); target = "
                                   "pooled OOS normal-score correlations of PLAYED teammates in regulation games, Phase "
                                   "1B OOS 2022-23..T, shrunk with %g pseudo-pairs; latent Cf = PSD projection, diagonal "
                                   "in [0, 0.95]; %d constraint-compensation fixed-point steps on %d training team-games "
                                   "of season T (realized participants, %d draws); independent across the two teams; "
                                   "no named-player or pair parameters" % (CORR_SHRINK_N0, CORR_CAL_ITERS,
                                                                           CORR_CAL_TEAM_GAMES, CORR_CAL_DRAWS),
                    "reconciliation": "m* = argmin (m - raw)' W^-1 (m - raw) + (R - R_raw)^2 / w_R s.t. sum m + R = 5 D, "
                                      "0 <= m_i <= D, m_i = 0 nonparticipants, R >= 0; exact (bisection + linear solve "
                                      "on the active set; closure <= %g absorbed by R, else the freest player)" % CLOSURE_TOL,
                    "W": "diagonal: pooled OOS Var(y_min - min_mu) of PLAYED rows by role x {WARM, COLD}, shrunk to the "
                         "global variance with %g pseudo-rows; Phase 1B OOS 2022-23..T" % W_SHRINK_N0,
                    "duration": "one draw per game shared by both teams from the Phase 3 overtime law (D = 40 + 5K); pace "
                                "never changes the clock",
                    "scored_player_world": "single-player quantities in the player's forced-active world (same draw, "
                                           "same uniforms / normals, re-solved with him active)"},
        "stat_means": {"terms": list(JM_TERMS), "exposure": "log(max(M, 1) / min_mu) with ONE reconciled M per player "
                       "per draw for all 7 stats", "environment": "accepted S1 shared game environment",
                       "no_opportunity_redistribution": True,
                       "glm": "Poisson pseudo-likelihood with offset log mu_1B on Phase 1B OOS rows 2022-23..T given the "
                              "realized state (phase3.fit_structure with these terms)"},
        "dispersion_refit": "one phi per stat by method of moments on the true-OOS structural residual basis given realized "
                            "minutes + environment (families kept); never Phase 1B phi + minutes variance",
        "residual_dependence": "J2 architecture (dependence.fit, MID) refit on the conditional PMFs of the true-OOS "
                               "structural residual basis (season F with parameters through F-1)",
        "metrics": {"draws_per_game": SIMS, "uncertainty": "game-clustered bootstrap, %d draws, seed %d" % (P3.EVAL_BOOT, P3.EVAL_SEED),
                    "minutes": ["MAE (median, mean)", "RMSE", "CRPS", "bias", "PIT deciles", "80% / 90% coverage"],
                    "marginal": ["log score", "CRPS (scaled)", "threshold Brier", "PIT coverage", "bias"],
                    "joint": ["ES", "VS_BAL (+ components)", "pairwise correlation calibration", "joint-event Brier "
                              "(incl. tail events)", "PRA / PR / PA / RA / STOCKS log / CRPS / Brier"],
                    "strata": ["stat", "role", "position", "stat x role", "stat x position", "WARM / COLD_EARLY_SEASON / "
                               "COLD_TRANSFER_IN / COLD_NO_HISTORY", "EARLY_SEASON (pregame team games <= %d)" % EARLY_TEAM_GAMES,
                               "MONTH:NOV", "RESERVOIR_HEAVY (pregame E[R] >= %g)" % RESERVOIR_HEAVY_MIN,
                               "NEAR_BUDGET", "REALIZED_INCOMPLETE_DIAGNOSTIC (outcome-defined, diagnostic only)"],
                    "diagnostics_not_gates": ["PTS latent spread ratio by role", "reconciliation displacement",
                                              "reservoir usage", "minutes covariance calibration", "old J2 vs residual J2"]},
        "development_gates_vs_S0": {
            "rule": "strict: a gate passes only if the 95% bootstrap interval bound clears its margin; inconclusive = fail",
            "G1": "MIN_CRPS(JM2) - MIN_CRPS(S0) upper bound <= +%g (all scored player-games)" % G_MIN_CRPS_ALL,
            "G2": "same for TOP5_MINUTES, ROTATION_6_8, BENCH_9PLUS: upper bound <= +%g" % G_MIN_CRPS_TIER,
            "G3": "EARLY_SEASON and MONTH:NOV upper bound <= +%g; RESERVOIR_HEAVY upper bound <= +%g" % (G_MIN_CRPS_EARLY, G_MIN_CRPS_RESERVOIR_HEAVY),
            "G4": "aggregate MARG_LOG delta upper bound <= +%g" % G_MARG_LOG_ALL,
            "G5": "every stat MARG_LOG upper bound <= +%g and every stat x tier upper bound <= +%g" % (G_MARG_LOG_STAT, G_MARG_LOG_STAT_ROLE),
            "G6": "ES upper bound <= +%g and VS_BAL upper bound <= +%g; each bound at least %g MC SE from its margin "
                  "(else inconclusive)" % (G_ES, G_VS, P3.MC_DECISION_RATIO),
            "G7": "BOTH_TOP_SCORERS_OVER (TM_SCORER_SCORER): Brier below S0 and |observed - mean predicted| smaller "
                  "(the Phase 3 A6 rule, unchanged)",
            "G8": "0 accounting violations (team minutes incl. reservoir = 5 D within %g, bounds, inactive = 0, "
                  "reservoir >= 0), 0 combo identity violations, 0 invalid matrices" % CLOSURE_TOL,
            "G9": "0 point-in-time violations of the reservoir features; universe is the legal pregame universe",
            "G10": "0 genuine PIT violations of the team environment features",
            "G11": "bit-identical re-simulation", "G12": "2025-26 untouched",
            "decision": "ADVANCE_TO_CONFIRMATION iff every gate passes for alpha*; otherwise REJECT. A design failure is "
                        "not tuned and rerun."},
        "seeds": {"structural_streams": "BLAKE2b(%d|stream|game): duration, environment, participation, minutes, "
                                        "tier_factor, reservoir" % SEED_BASE,
                  "count_latents": "BLAKE2b(%d|game) (Phase 2 bank, common across arms)" % J.SIM_SEED},
        "code_sha256_at_freeze": code_state(),
    }


def stage_protocol() -> dict:
    if PROTOCOL_PATH.exists():
        raise RuntimeError("the JointMinutesV2 protocol is frozen; it is never rewritten")
    if DEV_PATH.exists():
        raise RuntimeError("development record exists before the protocol")
    g = guards()
    out = protocol()
    out["guards_at_freeze"] = g
    out["candidate_universe_audit_training_and_development"] = universe_audit([2023, 2024, 2025])
    _dump(PROTOCOL_PATH, out)
    return out


def compact_params(params: dict) -> dict:
    return {"version": VERSION, "T": params["T"], "e_dur": params["e_dur"], "reservoir": params["reservoir"],
            "weights": params["weights"], "tier_factor_fit": params["tier_factor_fit"],
            "structure": P3._compact_struct(params["struct"]), "s4_components": P3._compact_s4(params["s4"]),
            "resid_rows": params["resid_rows"]}


def stage_develop(log=print, limit_games=None) -> dict:
    if DEV_PATH.exists():
        raise RuntimeError("the development record exists; the development evaluation runs exactly once")
    prot_commit = M.committed_unchanged(PROTOCOL_PATH)
    prot = json.loads(PROTOCOL_PATH.read_text())
    now = code_state()
    changed = sorted(k for k in now if now[k] != prot["code_sha256_at_freeze"].get(k))
    g = guards()
    T, F = DEV["T"], DEV["eval"]
    params = fit_parameters(T, DEV["resid_seasons"], log)
    _dump(PARAMS_PATH, compact_params(params))
    s0, scale = P3._s0_structure("select")
    univ = universe_audit([F])
    log(f"evaluating {ARMS} on F{F}")
    ev_out = evaluate(F, params, s0, scale, f"develop_{F}", log, limit_games=limit_games)
    realized = reservoir_frame([F], labels=True)[["game_id", "team_id", "res_minutes"]]
    summ = summarize(ev_out, ARMS, SIMS, scale, params["e_dur"], realized)
    det = determinism(F, params, s0, scale)
    alpha = select_alpha(summ)
    cand = alpha["selected_arm"]
    extra = {"res_pit": ev_out["pit_audit_reservoir_features"], "env_pit": ev_out["pit_audit_team_features"],
             "universe": univ, "determinism": det}
    gt_ = gates(summ, cand, ev_out["accounting"], extra)
    rec = reconciliation_summary(ev_out["wide"], ev_out["teams"])
    cov = minutes_covariance(ev_out["pair_acc"], F)
    j2 = P3.j2_comparison(json.loads(P3.PHASE2_FITS["select"].read_text())["MID"], params["s4"]["residual_dependence"])
    out = {"version": VERSION, "experiment": EXPERIMENT, "stage": "DEVELOPMENT_2024_25", "protocol_commit": prot_commit,
           "protocol_sha256": _sha(PROTOCOL_PATH), "code_sha256": now, "code_changed_since_protocol": changed,
           "guards": g, "parameters_through": T, "evaluation_season": F, "season_2025_26_loaded": False,
           "nature": "development evaluation on 2024-25 (already inspected in Phase 3 / S2 audit); not a holdout",
           "parameters_record": _rel(PARAMS_PATH), "parameters_sha256": _sha(PARAMS_PATH),
           "candidate_universe": univ, "summary": summ, "accounting": ev_out["accounting"],
           "participation_post_feasibility": ev_out["participation_post_feasibility"],
           "pit": {"team_features": ev_out["pit_audit_team_features"], "reservoir_features": ev_out["pit_audit_reservoir_features"]},
           "reconciliation": rec, "minutes_covariance_calibration": cov, "old_J2_vs_residual_J2": j2,
           "alpha_selection": alpha, "gates": gt_, "determinism": det,
           "JOINT_MINUTES_V2_DEVELOPMENT_DECISION": "ADVANCE_TO_CONFIRMATION" if gt_["ALL_PASS"] else "REJECT",
           "HISTORICAL_API_CALLS": 0, "draws_per_game": SIMS}
    _dump(DEV_PATH, out)
    return out


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "protocol":
        r = stage_protocol()
        print(json.dumps({"protocol": _rel(PROTOCOL_PATH), "sha256": _sha(PROTOCOL_PATH)}, indent=1))
    elif stage == "develop":
        r = stage_develop(log=lambda m: print(m, flush=True))
        print(json.dumps({"DECISION": r["JOINT_MINUTES_V2_DEVELOPMENT_DECISION"], "alpha": r["alpha_selection"]}, indent=1))
    else:
        raise SystemExit("usage: python -m cbb_platform.v2.joint_minutes_v2 protocol|develop")
