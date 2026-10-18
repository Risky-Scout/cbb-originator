"""V2 Phase 3 prerequisite: Phase 1B out-of-sample forecasts for 2022-23 (forecast season F2023).

Why. Phase 3's structural parameters (minutes elasticity, environment elasticities, opportunity elasticities) are
estimated from Phase 1B forecast/outcome pairs. The selection stage needs TRUE-OOS structural forecasts of 2023-24 to fit
the S4 conditional dispersion and residual dependence; those require structural parameters fitted on an earlier season,
hence Phase 1B forecasts of 2022-23. The Phase 1B store (modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet) starts at
2023-24, so this module extends it backward by one season with the frozen Phase 1B procedure (ANNUAL_REFIT, V1REF:
cbb_platform/v2/marginal.fit_v0 + stl_policy, training through 2021-22). Nothing here is tuned, no existing artifact or
store is modified, and no API is called; 2022-23 rows come from the same Stage 3 store as every other season.

P(play) cannot be estimated for 2022-23 (participation labels start in 2023-24), exactly as for F2024; it stays NaN.

    .venv/bin/python -m cbb_platform.v2.phase3_prep
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from cbb_platform import config as C, props_v0 as P, props_v0_participation as Q
from cbb_platform.v2 import marginal as M, phase1b as B

F = 2023
TRAIN_THROUGH = 2022
POLICY = "V1REF"
STATE_NAME = f"F{F}_ANNUAL_REFIT_{POLICY}"
STORE_PATH = M.OOS_DIR / "oos_forecasts_phase1b_v1ref_F2023.parquet"
MANIFEST_PATH = M.OUT_DIR / "oos_forecast_store_v1ref_F2023_manifest.json"


def oos_rows(F: int, st: dict, pst: dict | None, name: str, cfg: dict) -> pd.DataFrame:
    """Pregame-candidate forecast rows of season F from a fitted state: the row builder of marginal.stage_oos, verbatim
    (checked against the existing F2024 store rows by reproduce_f2024)."""
    cand, _ = M.load_frames(cfg, range(F - cfg["recency"]["prior_seasons_used"], F + 1), population="candidates")
    cand = cand[cand["season"] == F].reset_index(drop=True)
    prow = M.load_participation((F,))
    cand = cand.merge(prow[["player_game_example_id"] + [c for c in Q.NUMERIC + Q.FLAGS if c not in cand.columns]],
                      on="player_game_example_id", how="left", validate="one_to_one")
    mu = P.predict(cand, cfg, st)["challenger"]
    sig = P._band_sigma(mu["min"], cfg["minutes_sigma_buckets"], st["minutes_sigma"])
    out = pd.DataFrame({
        "fold_id": f"F{F}", "forecast_season": F, "game_id": cand["canonical_game_id"], "team_id": cand["canonical_team_id"],
        "player_id": cand["canonical_player_id"], "player_game_example_id": cand["player_game_example_id"],
        "prediction_cutoff_utc": cand["prediction_cutoff_utc"], "is_home": cand["is_home"], "position_group": cand["pos"],
        "cold_start_class": cand["cold_start_class"], "role_l5_minutes_rank": cand["role_l5_minutes_rank"],
        "role_diagnostic": M.role_of(cand["role_l5_minutes_rank"].to_numpy()),
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
    out["model_version"] = M.VERSION
    out["data_version"] = "stage3-lockbox-store(cbb_data_v2.4+5f19d31e524a<=2025; cbb_data_v3.0+86a63cb6d902 2026)"
    out["training_through_season"] = st["train_through"]
    out["participation_training_seasons"] = json.dumps(pst["train_seasons"]) if pst else "[]"
    out["weighting_policy"] = POLICY
    out["state_name"] = name
    out["state_hash"] = st["state_hash"]
    assert (out["prediction_cutoff_utc"] > pd.Timestamp(st["train_max_cutoff"])).all()
    return out


def reproduce_f2024(cfg: dict) -> dict:
    """The row builder applied to the saved F2024 V1REF state must reproduce the existing store's F2024 rows."""
    s = M.load_state("F2024_ANNUAL_REFIT_V1REF")
    mine = oos_rows(2024, s["v0_state"], s["participation_state"], "F2024_ANNUAL_REFIT_V1REF", cfg)
    ref = pd.read_parquet(B.OOS_PATH, filters=[("forecast_season", "==", 2024)])
    key = "player_game_example_id"
    a = mine.sort_values(key).reset_index(drop=True)
    b = ref.sort_values(key).reset_index(drop=True)
    out = {"rows_mine": int(len(a)), "rows_store": int(len(b)), "same_rows": bool(a[key].equals(b[key]))}
    num = [c for c in b.columns if b[c].dtype.kind == "f"]
    out["max_abs_diff"] = {c: float(np.nanmax(np.abs(a[c].to_numpy() - b[c].to_numpy()))) if a[c].notna().any() else 0.0
                           for c in num}
    out["nan_pattern_equal"] = {c: bool((a[c].isna() == b[c].isna()).all()) for c in num}
    out["REPRODUCED"] = bool(out["same_rows"] and all(v <= 1e-9 for v in out["max_abs_diff"].values())
                             and all(out["nan_pattern_equal"].values()))
    return out


def run() -> dict:
    if STORE_PATH.exists() or MANIFEST_PATH.exists():
        raise RuntimeError("the F2023 Phase 1B OOS store exists; it is built once")
    cfg = P.load_config()
    rep = reproduce_f2024(cfg)
    if not rep["REPRODUCED"]:
        raise RuntimeError(f"row builder does not reproduce the F2024 store rows: {rep}")
    frame, finals = M.load_frames(cfg, range(M.FIRST_SEASON, TRAIN_THROUGH + 1))
    if frame["season"].max() > TRAIN_THROUGH or finals["season"].max() > TRAIN_THROUGH:
        raise RuntimeError("a season after the training cutoff was loaded for fitting")
    st = M.stl_policy(M.fit_v0(frame, finals, cfg, TRAIN_THROUGH, POLICY, f"F{F}"), cfg)
    pst = None                                     # no participation label before 2023-24
    sh = M.save_state(STATE_NAME, {"v0_state": st, "participation_state": pst})
    df = oos_rows(F, st, pst, STATE_NAME, cfg)
    df.to_parquet(STORE_PATH, index=False)
    man = {"store": str(STORE_PATH.relative_to(C.PROJECT_ROOT)), "sha256": M._sha(STORE_PATH), "rows": int(len(df)),
           "games": int(df["game_id"].nunique()),
           "played_rows": int((df["participation_status"] == "PLAYED").sum()),
           "forecast_season": F, "training_through_season": TRAIN_THROUGH, "weighting_policy": POLICY,
           "procedure": "frozen Phase 1B (ANNUAL_REFIT + V1REF): marginal.fit_v0(..., train_through=2022, 'V1REF') + "
                        "marginal.stl_policy; rows built by phase3_prep.oos_rows (= marginal.stage_oos row builder)",
           "row_builder_reproduces_F2024_store": rep,
           "state_file": f"modeling/player_props_v2/states/{STATE_NAME}.json", "state_file_sha256": sh,
           "p_play": "NaN: NOT_ESTIMABLE_NO_PRIOR_PARTICIPATION_LABELS (as F2024)",
           "purpose": "Phase 3 structural-parameter fitting for the 2023-24 true-OOS structural forecasts only",
           "existing_phase1b_store_modified": False, "HISTORICAL_API_CALLS": 0,
           "columns": list(df.columns)}
    MANIFEST_PATH.write_text(json.dumps(man, indent=1, default=str) + "\n")
    return man


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: r[k] for k in ("sha256", "rows", "games", "played_rows")}, indent=1))
    print(json.dumps(r["row_builder_reproduces_F2024_store"]["REPRODUCED"]))
