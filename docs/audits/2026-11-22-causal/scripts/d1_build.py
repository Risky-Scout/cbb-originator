"""D1: deterministic causal-diagnostic dataset (research only; nothing deployed; no production state touched).
Source: the true point-in-time OOS store (modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet; fold F state trained on
seasons < F) joined to the Stage-3 lockbox feature frame (pregame features only; labels attached after forecasting).
One row per PLAYED player-game of folds F2024 / F2025 / F2026 with: minutes forecast + outcome, every rate component
of the Phase 1B blend (current-season, prior seasons, position prior, k) reproduced exactly, and pregame role / role-
change / transfer / return / cold-start descriptors. Output: data/qa/large/causal_20261122/d1_F<F>.parquet (gitignored;
player-level rows never enter the public repo).
    .venv/bin/python -B docs/audits/2026-11-22-causal/scripts/d1_build.py
"""
import gc, hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd
from cbb_platform import config as C
from cbb_platform.v2 import marginal as MG

ROOT = C.PROJECT_ROOT
OUT = ROOT / "data/qa/large/causal_20261122"
OUT.mkdir(parents=True, exist_ok=True)
cfg = json.loads((ROOT / "modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json").read_text())["config"]
TGT = {"PTS": "pts", "REB": "reb", "AST": "ast", "FG3M": "three_pm", "STL": "stl", "BLK": "blk", "TOV": "tov"}
KEEP = ["player_game_example_id", "canonical_player_id", "canonical_team_id", "game_id", "season", "prediction_cutoff_utc",
        "pos", "cold_start_class", "role_l5_minutes_rank", "role_team_rotation_depth_l5", "pr_l5_min_per_app",
        "pr_ewm_min_per_app", "pstd_min_per_app", "pps_min_per_app", "pr_ewm_start_rate", "pr_l5_starts",
        "pp_games_since_played", "pps_same_team", "prof_portal_transfer_in", "ret_team_returning_min_share",
        "pr_ewm_usage", "pstd_usage", "cur_min", "cur_n_app", "h1_min", "h1_n_app", "h2_min", "h3_min",
        "min_mu", "min_sigma", "y_min", "p_play"]
cols = ["fold_id", "player_game_example_id", "game_id", "min_mu", "min_sigma", "p_play", "y_min"] + \
       [f"{s}_{x}" for s in TGT for x in ("mu", "phi")] + [f"y_{s}" for s in TGT]
oos = pd.read_parquet(ROOT / "modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet", columns=cols)
man = {}
for F in (2024, 2025, 2026):
    fr, _ = MG.load_frames(cfg, range(F - 3, F + 1), "scored")
    fr = fr[fr.season == F].drop(columns=[f"y_{t}" for t in TGT.values()] + ["y_min"])
    st = json.loads((ROOT / f"modeling/player_props_v2/states/F{F}_ANNUAL_REFIT_V1REF.json").read_text())
    st = st.get("v0_state", st)
    o = oos[(oos.fold_id == f"F{F}") & (oos.y_min > 0)]
    d = o.merge(fr, on="player_game_example_id", how="inner", validate="one_to_one")
    w = [0.5 ** (j / cfg["recency"]["half_life_seasons"]) for j in (1, 2, 3)]
    Hm = d.cur_min.fillna(0).to_numpy(float) + sum(wj * d[f"h{j}_min"].fillna(0).to_numpy(float) for j, wj in zip((1, 2, 3), w))
    d["H_min"] = Hm
    out = d[[c for c in KEEP if c in d.columns]].copy()
    ts = pd.to_datetime(out.prediction_cutoff_utc, utc=True)
    out["season_day"] = (ts - ts.min()).dt.days
    rep = {}
    for S, t in TGT.items():
        k = st["prior_k_minutes"][t]
        prior = d["pos"].map(st["prior_rate"][t]).to_numpy(float)
        Hs = d[f"cur_{t}"].fillna(0).to_numpy(float) + sum(wj * d[f"h{j}_{t}"].fillna(0).to_numpy(float)
                                                          for j, wj in zip((1, 2, 3), w))
        r0 = (Hs + k * prior) / (Hm + k)
        mu = d[f"{S}_mu"].to_numpy(float)
        rep[S] = float(np.nanmax(np.abs(np.maximum(d.min_mu.to_numpy(float) * r0, cfg["mean_floor"]) - mu)))
        out[f"{S}_mu"], out[f"{S}_phi"], out[f"y_{S}"] = mu, d[f"{S}_phi"].to_numpy(float), d[f"y_{S}"].to_numpy(float)
        out[f"{S}_rate"], out[f"{S}_prior"], out[f"{S}_k"] = r0, prior, k
        out[f"{S}_cur"] = d[f"cur_{t}"].to_numpy(float)
        for j in (1, 2, 3):
            out[f"{S}_h{j}"] = d[f"h{j}_{t}"].to_numpy(float)
    out["H_min"] = Hm
    out["fold"] = F
    out = out.sort_values("player_game_example_id").reset_index(drop=True)
    p = OUT / f"d1_F{F}.parquet"
    out.to_parquet(p, index=False)
    man[F] = {"rows": len(out), "rate_reproduction_max_abs_err": rep,
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    print(F, man[F], file=sys.stderr)
    del fr, d, out; gc.collect()
(OUT / "d1_manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True))
print(json.dumps(man, indent=1))
