"""PTS RATE CONSTRAINED SELECTION from the EXISTING R2 (tau, rho) candidates (research; protocol
modeling/player_props_v2/PTS_RATE_CONSTRAINED_SELECTION_PROTOCOL.md, commit 9efecf3, frozen before any screening).

No new candidate, formula or grid: the universe is the committed R2 fit surface (coarse 861 + refinement 441 records).

Stages (in order; each writes a record):
    .venv/bin/python -m cbb_platform.v2.pts_rate_constrained accounting   step 0 (read-only A / B / C accounting)
    .venv/bin/python -m cbb_platform.v2.pts_rate_constrained scaling      step 1 (rate-ratio scaling check, R1 exact rows)
    .venv/bin/python -m cbb_platform.v2.pts_rate_constrained screen       steps 2-3 (cheap feasibility + shortlist)
No API call, no refit, no simulation in these stages. Not a production component.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
from numpy.polynomial.hermite_e import hermegauss
import math

from cbb_platform import config as C
from cbb_platform.prod import directional_audit as DA
from cbb_platform.v2 import pts_rate_challenger as R1, pts_rate_challenger_r2 as R2, structural as S

PROTOCOL = R1.OUT / "PTS_RATE_CONSTRAINED_SELECTION_PROTOCOL.md"
PROTOCOL_COMMIT = "9efecf39ac79cded8465c0912b04a6ee872b0da7"
OUT = R1.OUT
ACCOUNTING_PATH = OUT / "pts_rate_constrained_accounting.json"
SCREEN_PATH = OUT / "pts_rate_constrained_screen.json"
LOCK_PATH = OUT / "pts_rate_constrained_lock.json"
RESULT_PATH = OUT / "pts_rate_constrained_results.json"
ROWS_2025 = R2.R1_ROWS["2024-25"]
DEV_PARAMS = R1.EVAL["2024-25"]["params"]
PROTECTED = C.PROJECT_ROOT / "data" / "qa" / "game_identity_questionable_only.csv"
PROTECTED_SHA = "5ca9001c7c4e700b6ca0dd0e27c20290982d0051748ff96fa28317fa002f15b2"
MIN_CAP = 60.0
SHORTLIST_MAX = 3
TAU_STAR = 74.70                                                      # R2 optimum (tie-break distance anchor)
# cheap constraints (protocol section 5)
G_HIGH, G_COMP, G_OVER, G_BIAS = 0.75, 0.75, 0.15, 0.05
G_GROUP, G_AGG = R1.G_GROUP, R1.G_AGG_LOG                             # +0.005 / +0.002


def protocol_frozen() -> dict:
    committed = R1._git("show", f"{PROTOCOL_COMMIT}:{PROTOCOL.relative_to(C.PROJECT_ROOT)}")
    if committed != PROTOCOL.read_text().strip():
        raise R1.ChallengerViolation("the constrained-selection protocol changed after its freeze commit")
    return {"protocol_commit": PROTOCOL_COMMIT, "protocol_sha256": R1._sha(PROTOCOL)}


def protected_unchanged() -> bool:
    return R1._sha(PROTECTED) == PROTECTED_SHA


# ============================================================================ candidate universe
def candidates(fit_rec: dict | None = None) -> tuple[list[dict], int]:
    """Distinct (tau, rho) pairs of the committed R2 fit surfaces; returns (distinct, total records)."""
    rec = fit_rec or json.loads(R2.FIT_PATH.read_text())
    allc = [("coarse", c) for c in rec["coarse_surface"]] + [("refine", c) for c in rec["refine_surface"]]
    out = []
    for src, c in allc:
        lt, rh = np.log(c["tau"]), c["rho"]
        for o in out:
            if abs(np.log(o["tau"]) - lt) <= 1e-9 and abs(o["rho"] - rh) <= 1e-9:
                o["sources"].append(src)
                break
        else:
            out.append({"tau": float(c["tau"]), "rho": float(rh), "sources": [src]})
    return out, len(allc)


# ============================================================================ step 0 accounting
_erf = np.frompyfunc(math.erf, 1, 1)


def _cdf(z):
    return 0.5 * (1.0 + _erf(np.asarray(z, float) / math.sqrt(2.0)).astype(float))


def _pdf(z):
    return np.exp(-0.5 * np.asarray(z, float) ** 2) / math.sqrt(2.0 * math.pi)


def clipped_normal_mean(m, s, upper=MIN_CAP):
    """E[clip(N(m, s^2), 0, upper)] in closed form."""
    a, b = (0.0 - m) / s, (upper - m) / s
    return m * (_cdf(b) - _cdf(a)) + s * (_pdf(a) - _pdf(b)) + upper * (1.0 - _cdf(b))


def k_factor(m, s, beta, p_ot, e_dur, nodes: int = 64):
    """K = E[(max(M, 1) / m)^beta], M = clip(m + s eps, 0, 60) D / E[D] (Gauss-Hermite on eps, exact discrete D)."""
    x, w = hermegauss(nodes)
    w = w / np.sqrt(2 * np.pi)
    raw = np.clip(m[:, None] + s[:, None] * x[None, :], 0.0, MIN_CAP)
    k = np.zeros(len(m))
    for kk, p in enumerate(p_ot):
        M = raw * ((S.REGULATION + S.OT_PERIOD * kk) / e_dur)
        k += p * ((np.maximum(M, S.MIN_FLOOR) / m[:, None]) ** beta) @ w
    return k


def _rows_2025() -> pd.DataFrame:
    rows = pd.read_parquet(ROWS_2025)
    src = pd.read_parquet(C.PROJECT_ROOT / DA.SOURCES["2024-25"]["path"], columns=["player_game_example_id", "min_sigma"])
    return rows.merge(src, on="player_game_example_id", how="left", validate="one_to_one")


def accounting(log=print) -> dict:
    pf = protocol_frozen()
    rows = _rows_2025()
    a = json.loads(DEV_PARAMS.read_text())
    coef = a["structure"]["glm"]["stats"]["PTS"]["coef"]
    beta, c0 = float(coef["log_minutes_ratio"]), float(coef["intercept"])
    p_ot, e_dur = a["structure"]["env"]["overtime"]["p"], float(a["e_dur"])
    m, s = rows["min_mu"].to_numpy(float), rows["min_sigma"].to_numpy(float)
    mu, r0 = rows["R0|mean"].to_numpy(float), rows["r_R0"].to_numpy(float)
    y, ym = rows["y_PTS"].to_numpy(float), rows["y_min"].to_numpy(float)
    em = clipped_normal_mean(m, s)
    A, B, Cc = mu - r0 * em, r0 * (em - ym), r0 * ym - y
    ident = float(np.max(np.abs(A + B + Cc - (mu - y))))
    K = k_factor(m, s, beta, p_ot, e_dur)
    K_lin = (em / m) ** beta
    mu1b = rows["mu1B_R0"].to_numpy(float)
    mk = R2.masks(rows)
    strata = {}
    for k, msk in mk.items():
        if not msk.any():
            continue
        strata[k] = {"N": int(msk.sum()), "A": float(A[msk].mean()), "B": float(B[msk].mean()), "C": float(Cc[msk].mean()),
                     "total_bias": float((mu - y)[msk].mean()), "mean_E_minutes_minus_min_mu": float((em - m)[msk].mean()),
                     "mean_mu_JM1_over_mu_1B": float((mu / mu1b)[msk].mean()),
                     "mean_K": float(K[msk].mean()), "mean_K_lin": float(K_lin[msk].mean()),
                     "mean_K_over_K_lin": float((K / K_lin)[msk].mean()),
                     "mean_exp_c0_K": float((np.exp(c0) * K)[msk].mean()),
                     "A_part_from_K_minus_K_lin": float((mu1b * np.exp(c0) * (K - K_lin))[msk].mean())}
    out = {"step": "0 accounting (read-only)", **pf, "rows": int(len(rows)), "inputs": str(ROWS_2025.relative_to(C.PROJECT_ROOT)),
           "beta_log_minutes_ratio": beta, "intercept_c0": c0, "e_dur": e_dur,
           "E_JM1_minutes": "closed-form E[clip(N(min_mu, min_sigma), 0, 60)] (eps independent of D, E[D/E[D]] = 1)",
           "IDENTITY_MAX_ABS_ERROR": ident, "IDENTITY_CHECK": "PASS" if ident <= 1e-9 else "FAIL",
           "anchor_alpha": 1.0, "strata": strata, "protected_qa_file_unchanged": protected_unchanged()}
    ACCOUNTING_PATH.write_text(json.dumps(out, indent=1))
    log(f"[accounting] identity {out['IDENTITY_CHECK']} ({ident:.2e}); " + "; ".join(
        f"{k} A {v['A']:+.3f} B {v['B']:+.3f} C {v['C']:+.3f} K/Klin {v['mean_K_over_K_lin']:.3f}"
        for k, v in strata.items() if k.startswith("MIN_") or k == "ALL"))
    return out


# ============================================================================ step 1 scaling check
def scaling(log=print) -> dict:
    """R1 exact JM1 means vs the rate-ratio surrogate R0|mean x mu1B_R1 / mu1B_R0 on every 2024-25 row (no simulation)."""
    protocol_frozen()
    rows = _rows_2025()
    ex = rows["R1|mean"].to_numpy(float)
    sur = rows["R0|mean"].to_numpy(float) * rows["mu1B_R1"].to_numpy(float) / rows["mu1B_R0"].to_numpy(float)
    y = rows["y_PTS"].to_numpy(float)
    d = sur - ex
    mk = R2.masks(rows)
    st = {k: {"N": int(m.sum()), "bias_exact": float((ex - y)[m].mean()), "bias_surrogate": float((sur - y)[m].mean()),
              "difference": float(d[m].mean())} for k, m in mk.items() if m.any()}
    out = {"step": "1 rate-ratio scaling check", "rows": int(len(rows)), "max_abs_diff": float(np.abs(d).max()),
           "mean_abs_diff": float(np.abs(d).mean()), "max_rel_diff": float(np.max(np.abs(d) / np.maximum(ex, 1e-9))),
           "strata": st}
    rec = json.loads(ACCOUNTING_PATH.read_text())
    rec["scaling_check"] = out
    ACCOUNTING_PATH.write_text(json.dumps(rec, indent=1))
    log(f"[scaling] max |surrogate - exact| {out['max_abs_diff']:.4f} PTS (rel {out['max_rel_diff']:.2e}), "
        f"mean {out['mean_abs_diff']:.2e}; max stratum bias diff {max(abs(v['difference']) for v in st.values()):.2e}")
    return out


# ============================================================================ steps 2-3 screen + shortlist
def constraint_check(c: dict, r0: dict) -> dict:
    """Protocol section 5 constraints A-H for one candidate's stratum summary c vs the R0 summary r0."""
    b, b0 = c["bias"], r0["bias"]
    C1, C0 = b["MIN_0_10"] - b["MIN_30PLUS"], b0["MIN_0_10"] - b0["MIN_30PLUS"]
    return {"A": abs(b["MIN_30PLUS"]) <= G_HIGH * abs(b0["MIN_30PLUS"]),
            "B": b["MIN_30PLUS"] <= G_OVER,
            "C": abs(C1) <= G_COMP * abs(C0),
            "D": abs(b["ALL"]) <= abs(b0["ALL"]) + G_BIAS,
            "E": abs(b["LOW_LT20"]) <= abs(b0["LOW_LT20"]) + G_BIAS,
            "F": abs(b["EARLY_FIRST_30_DAYS"]) <= abs(b0["EARLY_FIRST_30_DAYS"]) + G_BIAS,
            "G": (abs(b["EARLY_TEAM_GAMES_LE2"]) <= abs(b0["EARLY_TEAM_GAMES_LE2"]) + G_BIAS
                  and c["dlog"]["EARLY_TEAM_GAMES_LE2"] <= G_GROUP and c["dcrps_scaled"]["EARLY_TEAM_GAMES_LE2"] <= G_GROUP),
            "H": c["dlog"]["ALL"] <= G_AGG and c["dcrps_scaled"]["ALL"] <= G_AGG}


def shortlist(cands: list[dict]) -> list[dict]:
    """Feasible only; ascending Phase 1B log, then CRPS, then distance to (log 74.70, 1.0); at most 3."""
    feas = [c for c in cands if c["feasible"]]
    key = lambda c: (c["log"], c["crps"], np.hypot(np.log(c["tau"]) - np.log(TAU_STAR), c["rho"] - 1.0))
    return sorted(feas, key=key)[:SHORTLIST_MAX]


def screen(log=print) -> dict:
    if SCREEN_PATH.exists():
        raise R1.ChallengerViolation("the screen runs exactly once")
    pf = protocol_frozen()
    acc = json.loads(ACCOUNTING_PATH.read_text())
    if acc["IDENTITY_CHECK"] != "PASS":
        raise R1.ChallengerViolation("accounting identity failed: implementation-defect stop")
    t0 = time.time()
    rows = _rows_2025()
    st = R1._state(2025)
    h, cfg = R1.history(2025)
    rows = rows.merge(h.drop(columns=["cur_min"]), on="player_game_example_id", how="left", validate="one_to_one")
    parts = R1.rate_parts(rows, st, cfg)
    mm = rows["min_mu"].to_numpy(float)
    mu0 = np.maximum(mm * parts["r_R0"], cfg["mean_floor"])
    if np.max(np.abs(mu0 - rows["mu1B_R0"].to_numpy(float))) > 1e-9:
        raise R1.ChallengerViolation("screen: Phase 1B R0 not reproduced")
    y = rows["y_PTS"].to_numpy(float)
    phi = float(st["dispersion"]["pts"]["phi"])
    m0 = rows["R0|mean"].to_numpy(float)
    l0, c0 = R1._nb_scores(mu0, phi, y)
    mk = R2.masks(rows)
    keys = ("ALL", "MIN_0_10", "MIN_10_20", "MIN_20_30", "MIN_30PLUS", "LOW_LT20", "EARLY_TEAM_GAMES_LE2", "EARLY_FIRST_30_DAYS")
    r0 = {"bias": {k: float((m0 - y)[mk[k]].mean()) for k in keys}}
    cands, total = candidates()
    for i, c in enumerate(cands):
        mu = np.maximum(mm * R2.r_r2(parts, c["tau"], c["rho"]), cfg["mean_floor"])
        l1, c1 = R1._nb_scores(mu, phi, y)
        e = m0 * mu / mu0 - y
        c["bias"] = {k: float(e[mk[k]].mean()) for k in keys}
        c["dlog"] = {k: float((l1 - l0)[mk[k]].mean()) for k in ("ALL", "MIN_30PLUS", "EARLY_TEAM_GAMES_LE2", "EARLY_FIRST_30_DAYS")}
        c["dcrps_scaled"] = {k: float((c1 - c0)[mk[k]].mean()) / R1.PTS_SCALE for k in c["dlog"]}
        c["log"], c["crps"] = float(l1.mean()), float(c1.mean())
        c["checks"] = constraint_check(c, r0)
        c["feasible"] = bool(all(c["checks"].values()))
        if (i + 1) % 200 == 0:
            log(f"[screen] {i + 1}/{len(cands)}  {time.time() - t0:.0f}s")
    sl = shortlist(cands)
    fails = {k: int(sum(not c["checks"][k] for c in cands)) for k in "ABCDEFGH"}
    # how close did the family come: candidates passing all but one constraint, and the best per constraint pair
    near = {k: int(sum((not c["checks"][k]) and sum(not v for v in c["checks"].values()) == 1 for c in cands)) for k in "ABCDEFGH"}
    out = {"steps": "2 cheap feasibility screen + 3 shortlist (2024-25 only)", **pf,
           "TOTAL_EXISTING_RECORDS": total, "TOTAL_EXISTING_CANDIDATES": len(cands),
           "source_artifacts": [str(R2.FIT_PATH.relative_to(C.PROJECT_ROOT)) + ":coarse_surface",
                                str(R2.FIT_PATH.relative_to(C.PROJECT_ROOT)) + ":refine_surface"],
           "R0": {**r0, "phase1b_log": float(l0.mean()), "phase1b_crps": float(c0.mean())},
           "CHEAP_FEASIBLE_CANDIDATES": int(sum(c["feasible"] for c in cands)),
           "constraint_fail_counts": fails, "sole_failing_constraint_counts": near,
           "SHORTLIST": [{k: c[k] for k in ("tau", "rho", "log", "crps", "bias", "dlog", "dcrps_scaled")} for c in sl],
           "WEIGHTING_FAMILY_FEASIBLE": bool(sl), "candidates": cands, "seconds": round(time.time() - t0, 1),
           "protected_qa_file_unchanged": protected_unchanged(), "api_calls": 0}
    SCREEN_PATH.write_text(json.dumps(out, indent=1))
    log(f"[screen] candidates {len(cands)} (records {total}); feasible {out['CHEAP_FEASIBLE_CANDIDATES']}; "
        f"fail counts {fails}; sole-fail {near}; {out['seconds']}s")
    return out


# ============================================================================ lock / confirmation guards (steps 5-6)
def require_lock() -> dict:
    """2025-26 may only be touched with a committed lock record of exactly one candidate (protocol sections 2, 8)."""
    if not LOCK_PATH.exists():
        raise R1.ChallengerViolation("no locked candidate: 2025-26 is not accessible")
    rel = str(LOCK_PATH.relative_to(C.PROJECT_ROOT))
    R1._git("ls-files", "--error-unmatch", rel)
    if R1._git("status", "--porcelain", rel):
        raise R1.ChallengerViolation("the lock record differs from its committed version")
    rec = json.loads(LOCK_PATH.read_text())
    if not ({"tau", "rho"} <= set(rec)):
        raise R1.ChallengerViolation("lock record incomplete")
    return rec


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"accounting": accounting, "scaling": scaling, "screen": screen}.get(cmd, lambda: (_ for _ in ()).throw(SystemExit(__doc__)))()
