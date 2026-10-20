"""Exact over / under / push pricing from a market pmf (cbb_platform/props_v1_pricing.price, unchanged).

    over  = P(X > L), under = P(X < L), push = P(X = L) (integer lines only; half lines never push)
    p_nonpush = 1 - push;  fair decimal = 1 / P(side | no push)  (a push refunds the stake)
    expected value per unit staked at decimal d: EV = P(win) (d - 1) - P(lose)     (push returns the stake)
All probabilities are CONDITIONAL ON PLAYING. Settlement of DNP is a separate (book-rule) question: void-on-DNP is
never assumed here and a DNP is never a settled zero.
"""
from __future__ import annotations

import numpy as np

from cbb_platform import props_v1_pricing as PR

SUM_TOL = 1e-9
SIDES = ("OVER", "UNDER")


class InvalidProbabilities(ValueError):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def _pad(pmf: np.ndarray, line: float) -> np.ndarray:
    need = int(np.floor(line)) + 2                       # props_v1_pricing.price needs line <= K - 0.5
    return np.pad(pmf, (0, max(0, need - len(pmf))))


def price_line(pmf: np.ndarray, line: float) -> dict:
    pmf = np.asarray(pmf, dtype=float)
    if not np.isfinite(pmf).all() or (pmf < -1e-15).any() or abs(pmf.sum() - 1) > SUM_TOL:
        raise InvalidProbabilities("PMF_NOT_NORMALIZED", f"sum {pmf.sum()}")
    if not np.isclose(line * 2, round(line * 2)) or line < 0:
        raise InvalidProbabilities("INVALID_QUOTE", f"line {line} is not a non-negative multiple of 0.5")
    p = PR.price(_pad(pmf, line)[None, :], line)
    o, u, pu = float(p["over_probability"][0]), float(p["under_probability"][0]), float(p["push_probability"][0])
    if min(o, u, pu) < -1e-12 or abs(o + u + pu - 1) > SUM_TOL:
        raise InvalidProbabilities("INVALID_PROBABILITIES", f"over {o} under {u} push {pu}")
    return {"p_over": o, "p_under": u, "p_push": pu, "p_nonpush": 1.0 - pu,
            "p_over_nonpush": float(p["over_probability_nonpush"][0]), "p_under_nonpush": float(p["under_probability_nonpush"][0]),
            "fair_over_decimal": float(p["fair_over_decimal"][0]), "fair_under_decimal": float(p["fair_under_decimal"][0]),
            "fair_over_american": float(p["fair_over_american"][0]), "fair_under_american": float(p["fair_under_american"][0])}


def side_view(pr: dict, side: str) -> dict:
    if side not in SIDES:
        raise InvalidProbabilities("INVALID_QUOTE", f"side {side}")
    w, l = (pr["p_over"], pr["p_under"]) if side == "OVER" else (pr["p_under"], pr["p_over"])
    fd = pr["fair_over_decimal"] if side == "OVER" else pr["fair_under_decimal"]
    fa = pr["fair_over_american"] if side == "OVER" else pr["fair_under_american"]
    return {"win": w, "lose": l, "push": pr["p_push"], "fair_decimal": fd, "fair_american": fa}


def expected_value(win: float, lose: float, decimal_odds: float) -> float:
    return float(win * (decimal_odds - 1.0) - lose)


def quantile(pmf: np.ndarray, q: float) -> int:
    return int(np.searchsorted(np.cumsum(pmf), q - 1e-12))


def mcse(p: float, sims: int) -> float:
    return float(np.sqrt(max(p * (1 - p), 0.0) / sims))
