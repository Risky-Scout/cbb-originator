"""The DAILY REFIT: the approved JM1_FINAL training procedure, unchanged, on the historical rows + the legal 2026-27 rows.

No fitting mathematics lives here. Every estimate comes from the frozen functions the opening artifacts were fit with:
    marginal   marginal.fit_v0 + marginal.stl_policy (minutes ridge, minutes SD bands, position prior rates, shrinkage k,
               rolling baseline, NB/Poisson dispersion), marginal.fit_participation  -- = phase1b.build_artifact
    structure  structural.env_fit (shared environment + overtime law), structural.glm_rows + phase3.glm_fit_terms
               (JM_TERMS conditional-mean GLM), structural.realized_conditional_mu (true-OOS residual basis),
               phase3.fit_s4 (conditional dispersion = structural.dispersion_refit; residual J2 = dependence.fit MID),
               joint_minutes_v2.expected_duration                              -- = jm1_final.fit_procedure
This module only supplies the rows (the frozen loaders refuse 2026-27 by design) and re-composes the ~15 lines of
phase3.fit_structure / residual_rows / build_procedure around them. tests/test_walkforward_fit.py proves that with no
2026-27 row the composition reproduces the opening artifacts.

Recency (exactly the approved policy, verified): marginal training rows carry v1 weight 0.5 ** (S - season) with
S = the LAST TRAINING SEASON (props_v0 config "age measured from the last training season"): once a 2026-27 game is in
the training state S = 2027, so 2026-27 rows weigh 1, 2025-26 0.5, 2024-25 0.25, ... (V1REF is unnormalized and every
weighted quantity is scale-invariant, and halving every weight is exact in binary floating point). Participation is
unweighted (v1). The structural components are UNWEIGHTED pooled fits by approved design (no recency weight exists in
them; none is added here).

Seasons: T = 2027 once any 2026-27 training row exists, else 2026 (the opening chronology). Environment on team-games
2013-14 .. T; GLM on Phase 1B OOS rows 2022-23 .. T; residual basis 2024 .. T with season F under structure F-1 (the
frozen SELECT / CONFIRM / FINAL rule). The research season guard structural.MAX_SEASON = 2026 is lifted to 2027 ONLY
inside fit() (production_season_guard), mirroring prod.features.stage3_window; no frozen file is edited.

Determinism: the frozen team-frame read (structural.load_team_frame) has no ORDER BY, so env_fit's float summation
order could vary run to run (documented in the production manifest). Here the team frame is sorted by
(season, game_id, team_id) before any fit, so two fits of the same inputs are bit-identical.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import threading

import numpy as np
import pandas as pd

from cbb_platform import props_v0 as P, props_v1_refit as R
from cbb_platform.v2 import jm1_final as F, jm1_final_production as JP, joint_minutes_v2 as JM, marginal as M
from cbb_platform.v2 import phase1b as B, phase3 as P3, structural as S
from cbb_platform.walkforward import config as WC, history_source as HS

_HIST: dict = {}


class WalkForwardFitError(RuntimeError):
    pass


FROZEN_MAX_SEASON = 2026                 # structural.MAX_SEASON as frozen (2025-26); never changed outside the guard
_GUARD_LOCK = threading.Lock()          # one guarded window per process: no concurrent / nested use of the module state


class SeasonGuardViolation(RuntimeError):
    pass


@contextlib.contextmanager
def production_season_guard():
    """Admit the production season to the frozen structural functions for ONE daily fit (structural.py is not edited).

    The original value is captured and restored in `finally` (also when the fit raises) and the restoration is verified
    before the context exits. The window is exclusive per process (a second concurrent or nested entry raises instead of
    sharing the modified module state); other processes have their own module state."""
    if not _GUARD_LOCK.acquire(blocking=False):
        raise SeasonGuardViolation("production_season_guard is already active (concurrent or nested use refused)")
    try:
        old = S.MAX_SEASON
        if old != FROZEN_MAX_SEASON:
            raise SeasonGuardViolation(f"structural.MAX_SEASON is {old}, not the frozen {FROZEN_MAX_SEASON}, before the fit")
        S.MAX_SEASON = WC.PRODUCTION_SEASON
        try:
            yield
        finally:
            S.MAX_SEASON = old
            if S.MAX_SEASON != FROZEN_MAX_SEASON:                # pragma: no cover -- defensive
                raise SeasonGuardViolation("structural.MAX_SEASON was not restored")
    finally:
        _GUARD_LOCK.release()


def guard_state() -> dict:
    return {"structural_MAX_SEASON": S.MAX_SEASON, "frozen": FROZEN_MAX_SEASON, "guard_active": _GUARD_LOCK.locked(),
            "RESTORED": S.MAX_SEASON == FROZEN_MAX_SEASON and not _GUARD_LOCK.locked()}


def _sort_team(tf: pd.DataFrame) -> pd.DataFrame:
    return tf.sort_values(["season", "game_id", "team_id"], kind="mergesort").reset_index(drop=True)


def history(cfg: dict) -> dict:
    """The frozen through-2025-26 training inputs, read once per process by the FROZEN loaders (never mutated)."""
    if not _HIST:
        fdb, ldb = HS.stage3_paths()                 # CONTROL: the frozen defaults (provenance.LB_FEATURES / LB_LABELS)
        frame, finals = M.load_frames(cfg, range(WC.MARGINAL_FIRST_SEASON, WC.OPENING_THROUGH + 1), features_db=fdb,
                                      labels_db=ldb)
        _HIST.update(frame=frame, finals=finals, source=HS.selected(),
                     part=M.load_participation(range(WC.PARTICIPATION_FIRST_SEASON, WC.OPENING_THROUGH + 1),
                                               features_db=fdb, labels_db=ldb),
                     team=_sort_team(S.load_team_frame(range(WC.TEAM_FIRST_SEASON, WC.OPENING_THROUGH + 1), labels=True)),
                     store=P3.load_store(range(WC.GLM_FIRST_SEASON, WC.OPENING_THROUGH + 1)))
    return _HIST


def inputs(cfg: dict, new: dict | None) -> dict:
    """Historical rows + the 2026-27 rows (appended; the frozen frames are never re-sorted)."""
    h = history(cfg)
    has_new = new is not None and len(new["frame"]) > 0
    cat = lambda k: pd.concat([h[k], new[k]], ignore_index=True) if has_new else h[k]
    out = {"T": WC.PRODUCTION_SEASON if has_new else WC.OPENING_THROUGH, "frame": cat("frame"), "finals": cat("finals"),
           "part": cat("part"), "team": _sort_team(cat("team")) if has_new else h["team"],
           "store": pd.concat([h["store"], new["oos"]], ignore_index=True) if has_new else h["store"]}
    if has_new and (new["frame"]["season"] != WC.PRODUCTION_SEASON).any():
        raise WalkForwardFitError("a non-production-season row reached the current-season training rows")
    return out


# ============================================================================ phase3 composition on supplied frames
def fit_structure(T: int, tf: pd.DataFrame, store: pd.DataFrame, terms) -> dict:
    """phase3.fit_structure body: environment on team-games 2013-14..T, GLM on OOS rows 2022-23..T."""
    t = tf[(tf["season"] >= WC.TEAM_FIRST_SEASON) & (tf["season"] <= T)]
    env = S.env_fit(t)
    seasons = list(range(WC.GLM_FIRST_SEASON, T + 1))
    rows, acc = S.glm_rows(store[store["forecast_season"].isin(seasons)], t[t["season"].isin(seasons)], env)
    glm = S.glm_fit(rows) if tuple(terms) == S.GLM_TERMS else P3.glm_fit_terms(rows, terms)
    return {"T": T, "env": env, "glm": glm, "glm_population": acc, "pit_audit": S.pit_audit(t), "terms": list(terms)}


def residual_rows(F_: int, prev: dict, tf: pd.DataFrame, store: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """phase3.residual_rows body: season F under structural parameters fitted through F-1 (true OOS)."""
    if prev["T"] >= F_:
        raise WalkForwardFitError("structural parameters must predate the residual season")
    rows, acc = S.glm_rows(store[store["forecast_season"] == F_], tf[tf["season"] == F_], prev["env"])
    mu = S.realized_conditional_mu(rows, prev["glm"], prev["terms"])
    for j, s in enumerate(S.STATS):
        rows[f"cond_{s}_mu"] = mu[:, j]
    rows["structure_T"] = prev["T"]
    return rows, acc


def fit_procedure(T: int, tf: pd.DataFrame, store: pd.DataFrame, log=print) -> dict:
    """jm1_final.fit_procedure = phase3.build_procedure(T, 2024..T, terms=JM_TERMS) + E[D]."""
    terms = JM.JM_TERMS
    resid_seasons = tuple(range(WC.RESID_FIRST_SEASON, T + 1))
    struct = fit_structure(T, tf, store, terms)
    parts, accs, used = [], {}, {}
    for F_ in resid_seasons:
        prev = struct if F_ - 1 == T else fit_structure(F_ - 1, tf, store, terms)
        log(f"structural residual basis F{F_} from parameters through {F_ - 1}")
        r, a = residual_rows(F_, prev, tf, store)
        parts.append(r)
        accs[F_], used[F_] = a, F_ - 1
    resid = pd.concat(parts, ignore_index=True)
    log(f"S4 components on {len(resid)} residual rows")
    s4 = P3.fit_s4(resid, used)
    s4["residual_basis_population"] = accs
    if max(resid["forecast_season"]) > T or struct["T"] != T:
        raise WalkForwardFitError("a season after T entered the procedure")
    return {"T": T, "struct": struct, "s4": s4, "resid_rows": int(len(resid)),
            "resid_seasons": sorted(int(x) for x in resid["forecast_season"].unique()),
            "e_dur": JM.expected_duration(struct["env"]["overtime"]["p"])}


def fit(cfg: dict, new: dict | None, log=print) -> dict:
    """One complete daily fit: marginal + structural + conditional dispersion + residual dependence."""
    x = inputs(cfg, new)
    T = x["T"]
    with production_season_guard():
        if S.MAX_SEASON != WC.PRODUCTION_SEASON:
            raise SeasonGuardViolation("guard did not admit the production season")
        v0 = M.stl_policy(M.fit_v0(x["frame"], x["finals"], cfg, T, B.POLICY, "WALK_FORWARD"), cfg)
        pst = M.fit_participation(x["part"], range(WC.PARTICIPATION_FIRST_SEASON, T + 1), B.POLICY, "WALK_FORWARD")
        params = fit_procedure(T, x["team"], x["store"], log)
    if not guard_state()["RESTORED"]:
        raise SeasonGuardViolation(f"season guard not restored after the fit: {guard_state()}")
    return {"T": T, "v0": v0, "pst": pst, "params": params,
            "rows": {"frame": int(len(x["frame"])), "part": int(len(x["part"])), "team": int(len(x["team"])),
                     "store": int(len(x["store"])), "resid": params["resid_rows"]},
            "weight_by_season": v0["v2"]["weight_by_season"]}


# ============================================================================ artifacts (deterministic content)
def _canon(x) -> str:
    return JP._canon(x)


def content_sha(x) -> str:
    return hashlib.sha256(_canon(x).encode()).hexdigest()


def marginal_artifact(f: dict, cfg: dict, meta: dict) -> dict:
    """phase1b.build_artifact layout with the walk-forward chronology (no wall-clock field: content is reproducible)."""
    T = f["T"]
    spec = R.load_final_spec()
    v0, pst = f["v0"], f["pst"]
    art = {"artifact_version": f"cbb-props-v2-marginal-v1ref-{meta['model_version']}",
           "role": "V2_MARGINAL_FOUNDATION daily walk-forward refit (phase1b procedure, unchanged)",
           "phase": "cbb-v2-phase1b", "SUPERVISED_TRAINING_THROUGH": T, "HISTORICAL_STATE_THROUGH": T,
           "PRODUCTION_SEASON": WC.PRODUCTION_SEASON, "ANNUAL_REFIT": False, "DAILY_WALK_FORWARD_REFIT": True,
           "weighting_policy": B.POLICY, "half_life": B.HALF_LIFE,
           "weight_rule": f"v1 verbatim: training-row weight 0.5 ** (({T} - season) / 1.0) (props_v0.fit); "
                          "participation unweighted (v1)",
           "datasets": {"stage3_store": str(M.FEATURES_DB.relative_to(M.C.PROJECT_ROOT)),
                        "lockbox_dataset_version_2025_26": "cbb_data_v3.0+86a63cb6d902",
                        "current_season_training_state": meta["training_state"]},
           "stat_training_seasons": [WC.MARGINAL_FIRST_SEASON, T], "participation_training_seasons": pst["train_seasons"],
           "spec_fingerprint": R.spec_fingerprint(cfg), "final_spec_fingerprint": R.final_spec_fingerprint(spec),
           "stl_policy": spec["accepted_changes"]["STL"], "config": cfg, "v0_state": v0, "participation_state": pst,
           "coef_order": {"v0": list(v0["coef"]), "participation": list(pst["coef"])},
           "architect_ruling_sha256": M._sha(B.RULING_PATH), "MODEL_VERSION": meta["model_version"],
           "TRAINING_CUTOFF": meta["training_cutoff"]}
    art["artifact_fingerprint"] = R.fingerprint(art)
    return art


def structural_artifacts(f: dict, meta: dict, marginal_sha: str) -> tuple[dict, dict]:
    """jm1_final_production.build_artifacts, with the walk-forward chronology and the daily marginal reference."""
    T = f["T"]
    art, dep = JP.build_artifacts(f["params"])
    for a in (art, dep):
        a["version"] = f"{JP.VERSION}+{meta['model_version']}"
        a["MARGINAL_ARTIFACT_SHA256"] = marginal_sha
        a["MODEL_VERSION"], a["TRAINING_CUTOFF"] = meta["model_version"], meta["training_cutoff"]
    art["role"] = "V2_JM1_FINAL_STRUCTURAL daily walk-forward refit (jm1_final.fit_procedure procedure, unchanged)"
    dep["role"] = "V2_JM1_FINAL_RESIDUAL_DEPENDENCE daily walk-forward refit"
    art["MARGINAL_ARTIFACT"] = "marginal.json"
    art["SUPERVISED_TRAINING_THROUGH"] = art["HISTORICAL_STATE_THROUGH"] = T
    dep["OUTCOME_DATA_THROUGH"] = T
    return art, dep


def finite_tree(x) -> bool:
    if isinstance(x, dict):
        return all(finite_tree(v) for v in x.values())
    if isinstance(x, (list, tuple)):
        return all(finite_tree(v) for v in x)
    if isinstance(x, float):
        return bool(np.isfinite(x))
    return True


def predictive_view(marg: dict, art: dict, dep: dict) -> dict:
    """The predictive parameters only (what scoring reads), for reproduction / determinism comparisons."""
    return {"v0_state": {k: v for k, v in marg["v0_state"].items() if k != "v2"},
            "participation_state": {k: v for k, v in marg["participation_state"].items() if k != "v2"},
            "env": art["structure"]["env"], "glm": art["structure"]["glm"], "e_dur": art["e_dur"],
            "conditional_dispersion": art["conditional_dispersion"], "residual_dependence": dep["parameters"]}


def json_roundtrip(x):
    return json.loads(_canon(x))
