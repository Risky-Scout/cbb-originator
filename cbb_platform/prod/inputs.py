"""Frozen-model inputs for each target game: the Phase 1B marginal forecasts (props_v0.predict / _band_sigma and
props_v0_participation.predict with the frozen marginal artifact -- exactly marginal.stage_oos), the pregame shared
environment (structural.env_predict with the frozen JM1_FINAL env), and the availability record.

Availability policy (JM1_FINAL has no validated teammate opportunity-redistribution model):
  * a player CONFIRMED unavailable (status OUT) is not priced (his own markets are suppressed);
  * statuses are recorded beside the forecast; nothing is redistributed to teammates (P(play) and the minutes draws
    of every other candidate are unchanged);
  * an unavailable player who carries a material share of the team's rotation (pregame TOP5_MINUTES role or a
    conditional minutes forecast >= ROTATION_SHOCK_MINUTES) is a rotation shock the frozen model cannot represent:
    the game is marked UNSUPPORTED_ROTATION_SHOCK and is never wager-eligible (the forecast is still shadowed).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cbb_platform import props_v0 as P, props_v0_participation as Q
from cbb_platform.v2 import latent as L, marginal as M, structural as S

ROTATION_SHOCK_MINUTES = 20.0
UNAVAILABLE = ("OUT", "OUT_FOR_SEASON", "OUT_SEASON", "SUSPENDED", "UNAVAILABLE", "LONG_TERM_OUT")
# Optional provider columns (prod/rotowire_feed.availability_frame) carried beside the status. availability_gate marks an
# unresolved status (GTD / QUESTIONABLE / DOUBTFUL / RETURN_DESIGNATION / UNKNOWN, or a source conflict): the player is
# still forecast with the model's own P(play), unchanged, and every row of his is blocked (never actionable).
AVAILABILITY_EXTRA = ("availability_source", "availability_raw_status", "availability_gate")


def marginal_rows(cand: pd.DataFrame, cfg: dict, v0_state: dict, p_state: dict) -> pd.DataFrame:
    """Phase 1B store columns for every pregame candidate (marginal.stage_oos body; no label column)."""
    if cand.empty:
        raise ValueError("no pregame candidates")
    mu = P.predict(cand, cfg, v0_state)["challenger"]
    sig = P._band_sigma(mu["min"], cfg["minutes_sigma_buckets"], v0_state["minutes_sigma"])
    out = pd.DataFrame({
        "game_id": cand["canonical_game_id"], "team_id": cand["canonical_team_id"], "player_id": cand["canonical_player_id"],
        "player_game_example_id": cand["player_game_example_id"], "prediction_cutoff_utc": cand["prediction_cutoff_utc"],
        "is_home": cand["is_home"], "position_group": cand["pos"], "cold_start_class": cand["cold_start_class"],
        "pool_evidence": cand.get("pool_evidence"), "role_l5_minutes_rank": cand["role_l5_minutes_rank"],
        "role_diagnostic": M.role_of(cand["role_l5_minutes_rank"].to_numpy()),
        "p_play": Q.predict(cand, p_state), "min_mu": mu["min"], "min_sigma": sig})
    for k, t in cfg["count_targets"].items():
        out[f"{k}_family"] = v0_state["dispersion"][t]["family"]
        out[f"{k}_mu"] = mu[t]
        out[f"{k}_phi"] = float(v0_state["dispersion"][t]["phi"])
    out["pmf_support_max"] = cfg["pmf_support_max"]
    # phase3.load_store normalization of the diagnostic groups
    out["role_diagnostic"] = out["role_diagnostic"].fillna("NO_ROLE_HISTORY")
    out["position_group"] = out["position_group"].fillna("U")
    return out.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)


def environment(team_frame: pd.DataFrame, env_fit: dict) -> dict:
    envp = S.env_predict(team_frame, env_fit)
    return {(r.game_id, r.team_id): np.array([r.pace_dev, r.own_eff_dev, r.opp_eff_dev]) for r in envp.itertuples(index=False)}


def apply_availability(rows: pd.DataFrame, availability: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """Attach availability status; choose the priced set; detect rotation shocks. Returns (rows, game_flags)."""
    rows = rows.copy()
    rows["availability_status"] = "NO_REPORT"
    rows["availability_received_at"] = pd.NaT
    extra = [c for c in AVAILABILITY_EXTRA if availability is not None and c in availability]
    if availability is not None and len(availability):
        a = availability.sort_values("received_at").drop_duplicates(["game_id", "player_id"], keep="last")
        rows = rows.drop(columns=["availability_status", "availability_received_at"]).merge(
            a[["game_id", "player_id", "status", "received_at"] + extra].rename(
                columns={"status": "availability_status", "received_at": "availability_received_at"}),
            on=["game_id", "player_id"], how="left")
        rows["availability_status"] = rows["availability_status"].fillna("NO_REPORT")
    if "availability_gate" in rows:
        rows["availability_gate"] = rows["availability_gate"].fillna("")
    out = rows["availability_status"].isin(UNAVAILABLE)
    rows["player_suppression"] = np.where(out, "CONFIRMED_UNAVAILABLE", "")
    missing = ~np.isfinite(rows[["p_play", "min_mu", "min_sigma"] + [f"{s}_mu" for s in L.STATS]].to_numpy(dtype=float)).all(axis=1)
    rows.loc[missing, "player_suppression"] = "MISSING_PLAYER_STATE"
    rows["_scored"] = (rows["player_suppression"] == "") & (rows["p_play"] > 0)
    flags = {}
    for gid, gp in rows.groupby("game_id", sort=True):
        shock = gp[out.loc[gp.index] & ((gp["role_diagnostic"] == "TOP5_MINUTES") | (gp["min_mu"] >= ROTATION_SHOCK_MINUTES))]
        f = []
        if len(shock):
            f.append("UNSUPPORTED_ROTATION_SHOCK")
        if "availability_gate" in gp:                     # an unresolved status in the rotation: the game is uncertain
            unc = gp[(gp["availability_gate"] != "") & ((gp["role_diagnostic"] == "TOP5_MINUTES")
                                                        | (gp["min_mu"] >= ROTATION_SHOCK_MINUTES))]
            if len(unc):
                f.append("AVAILABILITY_UNCERTAIN_ROTATION")
        if gp.loc[gp["_scored"], "team_id"].nunique() < 2:
            f.append("MISSING_PLAYER_STATE")
        flags[gid] = {"reasons": f, "shock_players": shock["player_id"].tolist()}
    return rows.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True), flags
