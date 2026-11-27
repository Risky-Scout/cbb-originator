"""Game model: ratings means + availability adjustment + heteroscedastic variance -> joint.Targets -> GameJointPMF.

    E[H], E[A]          ratings.Ratings.predict (point-in-time ridge through D-1)
    + availability      mu_team += beta * (S_today - S_base) + gamma * availability_delta
                        beta = 0 (walk-forward selection on 2024-25: the fitted coefficient was -0.08 and every
                        beta > 0 raised margin RMSE -- the PIT ratings already carry the team-strength signal).
                        gamma (conservative, NOT fitted) moves the mean only by availability_delta: the model's own
                        expected player points of the scored set minus those of every candidate at the model's own
                        p_play in the same snapshot (Stage 05 news-free baseline; before: vs the day's INITIAL
                        snapshot, which ignored news known at INITIAL). No refit, no new coefficient.
    sd_margin           a_m * (1 + c_m * exp(-n / tau_m)) * (E[T] / 142) ** k_m      n = min games played (season)
    sd_total            a_t * (1 + c_t * exp(-n / tau_t)) * (E[T] / 142) ** k_t
    rho_mt              constant
All coefficients come from params/game_model.json (sealed with its sha256; fit on 2023-24 only).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from cbb_platform.game import joint as J

# The game model version is params/game_model.json "model_version" (sealed with params_sha256). A module constant
# here stamped "cbb-game-model-1.0.0" on every PMF row through 1.1.0 and 1.2.0 (fixed 2026-11-26); rows written before
# the fix are identified by their params_sha256 (ffb91c4375c0 = 1.1.0, abe0533360f9 = 1.2.0).


@dataclass(frozen=True)
class VarianceParams:
    a_m: float
    c_m: float
    k_m: float
    tau_m: float
    a_t: float
    c_t: float
    k_t: float
    tau_t: float
    rho_mt: float

    def sd(self, exp_total: float, n_games: float) -> tuple[float, float]:
        s = exp_total / 142.0
        sm = self.a_m * (1 + self.c_m * math.exp(-n_games / self.tau_m)) * s ** self.k_m
        st = self.a_t * (1 + self.c_t * math.exp(-n_games / self.tau_t)) * s ** self.k_t
        return sm, st


@dataclass(frozen=True)
class AdjustParams:
    beta: float = 0.0
    gamma: float = 0.3


def targets(exp_home: float, exp_away: float, n_games: float, v: VarianceParams, adj: AdjustParams | None = None,
            avail_delta_home: float = 0.0, avail_delta_away: float = 0.0) -> J.Targets:
    adj = adj or AdjustParams()
    h = exp_home + adj.gamma * avail_delta_home
    a = exp_away + adj.gamma * avail_delta_away
    sm, st = v.sd(h + a, n_games)
    return J.Targets(float(h), float(a), float(sm), float(st), float(v.rho_mt))


def build(t: J.Targets, eot) -> J.GameJointPMF:
    """eot = params.EndgameOT (the pace bucket follows the expected total)."""
    return J.build(t, eot.kernel(t.exp_home + t.exp_away), eot.ot)


def games_played(history_home: np.ndarray, history_away: np.ndarray, team: str) -> int:
    return int((history_home == team).sum() + (history_away == team).sum())
