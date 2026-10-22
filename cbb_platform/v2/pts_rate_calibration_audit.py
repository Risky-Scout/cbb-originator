"""PTS RATE CALIBRATION-GEOMETRY AUDIT (research, DIAGNOSIS ONLY; protocol
modeling/player_props_v2/PTS_RATE_CALIBRATION_AUDIT_PROTOCOL.md, commit db54556, frozen before any calibration quantity).

Is the frozen R0 PTS per-minute rate cross-sectionally compressed? Fixed reference quintiles, minutes-weighted reliability,
NB2 offset calibration regression log E[PTS] = log(min) + a + b log(r_R0) [+ c z(min_mu)] with game-clustered CIs, and
the protocol strata. No transform is built or applied, no model is refit, no simulation, no API call.

    .venv/bin/python -m cbb_platform.v2.pts_rate_calibration_audit run
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.v2 import joint_minutes_v2 as JM, phase3_prep as PP, pts_rate_challenger as R1

PROTOCOL = R1.OUT / "PTS_RATE_CALIBRATION_AUDIT_PROTOCOL.md"
PROTOCOL_COMMIT = "db545563e7f79a33f6c9797b2a0a786d43379430"
RESULT_PATH = R1.OUT / "pts_rate_calibration_audit.json"
PROTECTED = C.PROJECT_ROOT / "data" / "qa" / "game_identity_questionable_only.csv"
PROTECTED_SHA = "5ca9001c7c4e700b6ca0dd0e27c20290982d0051748ff96fa28317fa002f15b2"
STORES = {2023: PP.STORE_PATH, 2024: R1.FIT_FOLDS[2024], 2025: R1.FIT_FOLDS[2024], 2026: R1.FIT_FOLDS[2024]}
SEASON = {2023: "2022-23", 2024: "2023-24", 2025: "2024-25", 2026: "2025-26"}
REFERENCE = (2023, 2024)
SAMPLES = {"REFERENCE": REFERENCE, "2024-25": (2025,), "2025-26": (2026,)}
QS = (0.2, 0.4, 0.6, 0.8)
Z95 = 1.959964
MIN_N_FIT = 2000
EXPOSURE = {"MIN_LT20": (-np.inf, 20.0), "MIN_20_30": (20.0, 30.0), "MIN_30PLUS": (30.0, np.inf)}
POSITIONS = ("G", "F", "C", "U")
EARLY_DAYS, EARLY_TEAM_GAMES = 30, 2
# frozen decision thresholds (protocol section 11)
REF_SLOPE_MIN, SLOPE_TOL, C_MATERIAL, CURVE_GRADIENT, EARLY_REL = 1.05, 0.15, 0.02, 0.03, 0.03
COLS = ["player_game_example_id", "forecast_season", "game_id", "team_id", "prediction_cutoff_utc", "position_group",
        "cold_start_class", "min_mu", "PTS_mu", "participation_status", "y_min", "y_PTS", "training_through_season"]


class AuditViolation(RuntimeError):
    pass


def protocol_frozen() -> dict:
    committed = R1._git("show", f"{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(C.PROJECT_ROOT)}")
    if committed != PROTOCOL.read_text().strip():
        raise AuditViolation("the calibration audit protocol changed after its freeze commit")
    return {"protocol_commit": PROTOCOL_COMMIT, "protocol_sha256": R1._sha(PROTOCOL)}


# ============================================================================ data (protocol sections 1-4)
def team_games(seasons) -> pd.DataFrame:
    """PIT team games so far = max(pp_team_games) per (season, game, team) (joint_minutes_v2.reservoir_frame definition)."""
    con = JM._con()
    sl = ", ".join(str(int(s)) for s in seasons)
    df = con.execute(f"""SELECT f.season AS forecast_season, f.canonical_game_id AS game_id, f.canonical_team_id AS team_id,
                         coalesce(max(f.pp_team_games), 0)::DOUBLE AS team_games
                         FROM feat.player_features f WHERE f.season IN ({sl}) GROUP BY 1, 2, 3""").df()
    con.close()
    return df


def load_season(F_: int, tg: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    s = pd.read_parquet(STORES[F_], columns=COLS)
    s = s[s["forecast_season"] == F_].reset_index(drop=True)
    if (s["training_through_season"] >= F_).any():
        raise AuditViolation(f"F{F_}: store row not out-of-sample")
    h, cfg = R1.history(F_)
    s = s.merge(h, on="player_game_example_id", how="left", validate="one_to_one")
    if s["pos"].isna().any():
        raise AuditViolation(f"F{F_}: history join incomplete")
    parts = R1.rate_parts(s, R1._state(F_), cfg)
    err = float(np.max(np.abs(np.maximum(s["min_mu"].to_numpy(float) * parts["r_R0"], cfg["mean_floor"])
                              - s["PTS_mu"].to_numpy(float))))
    if err > 1e-9:
        raise AuditViolation(f"F{F_}: r_R0 does not reproduce the frozen PTS_mu ({err}): implementation-defect stop")
    s["r_R0"] = parts["r_R0"]
    s["cur_min"] = parts["cur_min"]
    s = s.merge(tg, on=["forecast_season", "game_id", "team_id"], how="left", validate="many_to_one")
    cand = len(s)
    u = s[s["participation_status"].eq("PLAYED") & s["y_PTS"].notna() & (s["y_min"] > 0)].reset_index(drop=True)
    t = pd.to_datetime(u["prediction_cutoff_utc"], utc=True).dt.tz_convert("America/New_York").dt.normalize()
    u["season_day"] = (t - t.min()).dt.days.to_numpy()
    u["r_act"] = u["y_PTS"] / u["y_min"]
    keep = ["forecast_season", "game_id", "position_group", "cold_start_class", "min_mu", "y_min", "y_PTS", "r_R0",
            "r_act", "cur_min", "team_games", "season_day"]
    return u[keep], {"candidate_rows": cand, "universe_rows": int(len(u)), "games": int(u["game_id"].nunique()),
                     "r_R0_reproduction_max_abs_error": err, "team_games_missing": int(u["team_games"].isna().sum())}


# ============================================================================ NB2 offset regression (protocol section 8)
def _theta_ll(phi: float, y: np.ndarray, mu: np.ndarray, ycount: np.ndarray) -> float:
    """NB2 log-likelihood up to the constant -sum lgamma(y+1); sum lgamma(y+r)-lgamma(r) = sum_j N_{y>j} log(r+j)."""
    r = 1.0 / phi
    return float(ycount @ np.log(r + np.arange(len(ycount))) + np.sum(r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))))


def nb2_fit(y, X, offset, groups, tol: float = 1e-8, max_rounds: int = 200) -> dict:
    """ML NB2 (Var = mu + phi mu^2) with log link and offset; alternating Fisher scoring / golden-section on log phi;
    game-clustered sandwich covariance for the mean coefficients at the ML phi."""
    y, X, off = np.asarray(y, float), np.asarray(X, float), np.asarray(offset, float)
    yi = y.astype(np.int64)
    ycount = np.bincount(yi, minlength=int(yi.max()) + 1)[::-1].cumsum()[::-1][1:].astype(float)   # N_{y > j}, j = 0..max-1
    beta = np.linalg.lstsq(X, np.log(np.maximum(y, 0.5)) - off, rcond=None)[0]
    lphi, conv = np.log(0.1), False
    gr = (np.sqrt(5) - 1) / 2
    for rnd in range(max_rounds):
        phi = np.exp(lphi)
        old = beta.copy()
        for _ in range(50):
            mu = np.exp(off + X @ beta)
            w = mu / (1 + phi * mu)
            z = X @ beta + (y - mu) / mu
            nb = np.linalg.solve(X.T @ (X * w[:, None]), X.T @ (w * z))
            step = np.max(np.abs(nb - beta))
            beta = nb
            if step < tol * 1e-2:
                break
        mu = np.exp(off + X @ beta)
        lo, hi = np.log(1e-4), np.log(10.0)
        c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
        fc, fd = _theta_ll(np.exp(c), y, mu, ycount), _theta_ll(np.exp(d), y, mu, ycount)
        while hi - lo > 1e-10:
            if fc > fd:
                hi, d, fd = d, c, fc
                c = hi - gr * (hi - lo)
                fc = _theta_ll(np.exp(c), y, mu, ycount)
            else:
                lo, c, fc = c, d, fd
                d = lo + gr * (hi - lo)
                fd = _theta_ll(np.exp(d), y, mu, ycount)
        nl = 0.5 * (lo + hi)
        dl = abs(nl - lphi)
        lphi = nl
        if np.max(np.abs(beta - old)) < tol and dl < tol:
            conv = True
            break
    phi = float(np.exp(lphi))
    mu = np.exp(off + X @ beta)
    w = mu / (1 + phi * mu)
    bread = np.linalg.inv(X.T @ (X * w[:, None]))
    sc = X * ((y - mu) / (1 + phi * mu))[:, None]
    _, g = np.unique(np.asarray(groups), return_inverse=True)
    G = int(g.max()) + 1
    U = np.zeros((G, X.shape[1]))
    np.add.at(U, g, sc)
    cov = bread @ (U.T @ U * G / (G - 1)) @ bread
    se = np.sqrt(np.diag(cov))
    return {"coef": beta.tolist(), "se": se.tolist(), "ci_lo": (beta - Z95 * se).tolist(), "ci_hi": (beta + Z95 * se).tolist(),
            "phi": phi, "N": int(len(y)), "clusters": G, "converged": bool(conv), "rounds": rnd + 1,
            "ll": _theta_ll(phi, y, mu, ycount)}


def calib(df: pd.DataFrame, zref: tuple[float, float] | None = None) -> dict:
    """Section 8 (zref None) or section 9 (zref = (mean_ref, sd_ref)) regression on df."""
    cols = [np.ones(len(df)), np.log(df["r_R0"].to_numpy(float))]
    names = ["a", "b"]
    if zref is not None:
        cols.append((df["min_mu"].to_numpy(float) - zref[0]) / zref[1])
        names.append("c")
    f = nb2_fit(df["y_PTS"].to_numpy(float), np.column_stack(cols), np.log(df["y_min"].to_numpy(float)),
                df["forecast_season"].astype(str).to_numpy() + ":" + df["game_id"].astype(str).to_numpy())
    out = {"N": f["N"], "games": f["clusters"], "phi": f["phi"], "converged": f["converged"], "rounds": f["rounds"]}
    for i, n in enumerate(names):
        out[n] = {"est": f["coef"][i], "se": f["se"][i], "ci95": [f["ci_lo"][i], f["ci_hi"][i]]}
    return out


# ============================================================================ reliability + strata (sections 6, 7, 10)
def boundaries(ref: pd.DataFrame) -> list[float]:
    return [float(q) for q in np.quantile(ref["r_R0"].to_numpy(float), QS)]


def quintile(r: np.ndarray, b: list[float]) -> np.ndarray:
    return np.searchsorted(np.asarray(b), np.asarray(r, float), side="right") + 1        # Q1 r<q20 ... Q5 r>=q80


def rate_cell(d: pd.DataFrame) -> dict:
    m = d["y_min"].to_numpy(float)
    pw = float((d["r_R0"] * d["y_min"]).sum() / m.sum())
    aw = float(d["y_PTS"].sum() / m.sum())
    return {"N": int(len(d)), "mean_r_R0": float(d["r_R0"].mean()), "mean_r_actual": float(d["r_act"].mean()),
            "unweighted_signed_err": float(d["r_R0"].mean() - d["r_act"].mean()),
            "pred_rate_w": pw, "actual_rate_w": aw, "signed_err": pw - aw, "rel_err": (pw - aw) / aw,
            "mean_min_mu": float(d["min_mu"].mean()), "mean_y_min": float(m.mean())}


def reliability(d: pd.DataFrame, b: list[float]) -> dict:
    q = quintile(d["r_R0"].to_numpy(float), b)
    cells = {f"Q{k}": rate_cell(d[q == k]) for k in range(1, 6)}
    e = [cells[f"Q{k}"]["rel_err"] for k in range(1, 6)]
    inv = int(sum(e[k + 1] > e[k] for k in range(4)))
    grad = e[0] - e[4]
    return {"cells": cells, "rel_err": e, "inversions": inv, "gradient": grad,
            "compression_pattern": bool(e[0] > 0 and e[4] < 0 and inv <= 1 and grad >= CURVE_GRADIENT)}


def stratum(d: pd.DataFrame) -> dict:
    out = rate_cell(d) if len(d) else {"N": 0}
    if len(d) >= MIN_N_FIT:
        c = calib(d)
        out.update({"a": c["a"]["est"], "b": c["b"]["est"], "b_ci95": c["b"]["ci95"], "a_ci95": c["a"]["ci95"],
                    "converged": c["converged"]})
    else:
        out["fit"] = "not estimable (N < 2000)"
    return out


def strata_masks(d: pd.DataFrame) -> dict:
    mm, cm = d["min_mu"].to_numpy(float), d["cur_min"].to_numpy(float)
    m = {f"EXPOSURE:{k}": (mm >= lo) & (mm < hi) for k, (lo, hi) in EXPOSURE.items()}
    m.update({f"POSITION:{p}": (d["position_group"] == p).to_numpy() for p in POSITIONS})
    m.update({"EVIDENCE:CUR_0": cm <= 0, "EVIDENCE:CUR_0_100": (cm > 0) & (cm <= 100),
              "EVIDENCE:CUR_100_300": (cm > 100) & (cm <= 300), "EVIDENCE:CUR_300PLUS": cm > 300})
    m.update({"EARLY:TEAM_GAMES_LE2": d["team_games"].fillna(0).to_numpy(float) <= EARLY_TEAM_GAMES,
              "EARLY:FIRST_30_DAYS": d["season_day"].to_numpy() < EARLY_DAYS,
              "EARLY:REMAINDER": d["season_day"].to_numpy() >= EARLY_DAYS})
    m.update({f"COLD:{c}": (d["cold_start_class"].fillna("UNKNOWN") == c).to_numpy() for c in R1.COLD})
    return m


# ============================================================================ decision (section 11)
def _opposite(ci: list[float], ref_b: float) -> bool:
    """CI entirely on the opposite side of 1 from the reference slope point estimate."""
    return (ref_b > 1 and ci[1] < 1) or (ref_b < 1 and ci[0] > 1)


def decide(res: dict) -> dict:
    cal = res["calibration"]
    br, b25, b26 = cal["REFERENCE"]["b"], cal["2024-25"]["b"]["est"], cal["2025-26"]["b"]["est"]
    rel = res["reliability"]
    crit = {"1_REF": br["est"] >= REF_SLOPE_MIN and br["ci95"][0] > 1,
            "2_S25": b25 > 1, "3_S26": b26 > 1,
            "4_CONSISTENT": all(abs(cal[s]["b"]["est"] - br["est"]) <= SLOPE_TOL or rel[s]["compression_pattern"]
                                for s in ("2024-25", "2025-26")),
            "5_CURVES": all(rel[s]["compression_pattern"] for s in ("2022-23", "2023-24", "2024-25", "2025-26"))}
    mc = res["min_mu_after_rate"]["REFERENCE"]["c"]
    crit["6_MIN_MU"] = not (abs(mc["est"]) >= C_MATERIAL and (mc["ci95"][0] > 0 or mc["ci95"][1] < 0))
    pos_rev, early_rev = [], []
    for s in SAMPLES:
        st, gb = res["strata"][s], cal[s]["b"]["est"]
        for p in POSITIONS:
            c = st[f"POSITION:{p}"]
            if c["N"] >= MIN_N_FIT and "b_ci95" in c and _opposite(c["b_ci95"], gb):
                pos_rev.append(f"{s}:{p}")
        rem = st["EARLY:REMAINDER"]
        for k in ("EARLY:TEAM_GAMES_LE2", "EARLY:FIRST_30_DAYS"):
            c = st[k]
            if ("b_ci95" in c and "b" in rem and _opposite(c["b_ci95"], rem["b"])) or (
                    c["N"] and np.sign(c["signed_err"]) != np.sign(rem["signed_err"]) and abs(c["rel_err"]) >= EARLY_REL):
                early_rev.append(f"{s}:{k}")
    crit["7_POSITION"], crit["8_EARLY"] = not pos_rev, not early_rev
    conv = all(v.get("converged", True) for v in list(cal.values()) + list(res["min_mu_after_rate"].values()))
    if all(crit.values()) and conv:
        d = "CALIBRATION_DECOMPRESSION_JUSTIFIED"
    elif not conv or all(crit[k] for k in ("1_REF", "2_S25", "3_S26", "5_CURVES", "7_POSITION", "8_EARLY")):
        d = "INCONCLUSIVE"
    else:
        d = "CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED"
    return {"criteria": crit, "position_reversals": pos_rev, "early_reversals": early_rev, "all_converged": conv,
            "RATE_COMPRESSION_CONFIRMED": all(crit[k] for k in ("1_REF", "2_S25", "3_S26", "4_CONSISTENT", "5_CURVES")),
            "DECISION": d}


# ============================================================================ run
def run(log=print) -> dict:
    pf = protocol_frozen()
    t0, c0 = time.time(), time.process_time()
    tg = team_games(STORES)
    frames, data = {}, {}
    for F_ in STORES:
        frames[F_], data[SEASON[F_]] = load_season(F_, tg)
    log(f"[data] rows " + ", ".join(f"{SEASON[F_]} {len(frames[F_])}" for F_ in frames) + f"  {time.time() - t0:.0f}s")
    samp = {k: pd.concat([frames[F_] for F_ in v], ignore_index=True) for k, v in SAMPLES.items()}
    ref = samp["REFERENCE"]
    b = boundaries(ref)
    rel = {SEASON[F_]: reliability(frames[F_], b) for F_ in frames}
    rel["REFERENCE"] = reliability(ref, b)
    log("[reliability] boundaries " + ", ".join(f"{x:.4f}" for x in b) + "; " + "; ".join(
        f"{s} rel_err " + "/".join(f"{e:+.3f}" for e in r["rel_err"]) + f" pattern {r['compression_pattern']}"
        for s, r in rel.items()))
    cal = {k: calib(v) for k, v in samp.items()}
    cal.update({f"{SEASON[F_]} (info)": calib(frames[F_]) for F_ in REFERENCE})
    log("[calibration] " + "; ".join(f"{k} a {v['a']['est']:+.4f} b {v['b']['est']:.4f} "
                                     f"[{v['b']['ci95'][0]:.4f}, {v['b']['ci95'][1]:.4f}]" for k, v in cal.items()))
    zm, zs = float(ref["min_mu"].mean()), float(ref["min_mu"].std(ddof=0))
    mmu = {}
    for k, v in samp.items():
        c = calib(v, (zm, zs))
        ce = c["c"]["est"]
        c["effect_per_sd"] = float(np.exp(ce) - 1)
        c["factor_min_mu_32_vs_10"] = float(np.exp(ce * 22.0 / zs))
        mmu[k] = c
    log("[min_mu] z ref mean %.3f sd %.3f; " % (zm, zs) + "; ".join(
        f"{k} c {v['c']['est']:+.4f} [{v['c']['ci95'][0]:+.4f}, {v['c']['ci95'][1]:+.4f}] b {v['b']['est']:.4f}"
        for k, v in mmu.items()))
    strata = {k: {n: stratum(v[m]) for n, m in strata_masks(v).items()} for k, v in samp.items()}
    res = {"protocol": pf, "data": data, "quintile_boundaries": b, "z_min_mu_ref": {"mean": zm, "sd": zs},
           "reliability": rel, "calibration": cal, "min_mu_after_rate": mmu, "strata": strata}
    res["decision"] = decide(res)
    res["compute"] = {"wall_seconds": round(time.time() - t0, 1), "process_cpu_seconds": round(time.process_time() - c0, 1)}
    res["integrity"] = {"protected_qa_file_unchanged": R1._sha(PROTECTED) == PROTECTED_SHA, "api_calls": 0,
                        "new_model_candidate_created": False, "historical_resimulation": False}
    RESULT_PATH.write_text(json.dumps(res, indent=1))
    log(f"[decision] {res['decision']['criteria']} -> {res['decision']['DECISION']}  "
        f"(compression confirmed {res['decision']['RATE_COMPRESSION_CONFIRMED']}); {res['compute']}")
    return res


if __name__ == "__main__":
    {"run": run}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: (_ for _ in ()).throw(SystemExit(__doc__)))()
