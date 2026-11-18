"""Game-market probabilities and best-offer evaluation -- every market from the SAME final GameJointPMF.

    SPREAD / ALT_SPREAD  side HOME at point p: win P(M + p > 0), push P(M + p = 0); side AWAY at point q: win P(M < q)
    GAME_TOTAL / ALT_TOTAL  OVER t: P(T > t), push P(T = t); UNDER t: P(T < t)
    MONEYLINE           HOME P(H > A), AWAY P(A > H) (no tie mass: they sum to 1)
    HOME_TEAM_TOTAL / AWAY_TEAM_TOTAL (+ ALT_)  OVER t: P(H > t) (resp. A), push P(= t)

M = H - A, T = H + A. Odds API `point` is per outcome. Conventions identical to the player markets (prod.market_eval):
model_p = P(win) / (1 - P(push)), EV = P(win)(d - 1) - P(lose) (push refunds), fair = same-book two-way de-vig,
edge = model_p - fair, best offer = max EV over books x lines x sides (ties: edge, model_p, price, book, line).
Game markets are SHADOW: Is Actionable = FALSE, Selection Tier = RESEARCH until Release C validation.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cbb_platform.game.joint import GameJointPMF

MARKETS_VERSION = "cbb-game-markets-1.0.0"
CORE = ("SPREAD", "GAME_TOTAL", "MONEYLINE", "HOME_TEAM_TOTAL", "AWAY_TEAM_TOTAL")
ALTS = ("ALT_SPREAD", "ALT_TOTAL", "ALT_HOME_TEAM_TOTAL", "ALT_AWAY_TEAM_TOTAL")
BASE = {"ALT_SPREAD": "SPREAD", "ALT_TOTAL": "GAME_TOTAL", "ALT_HOME_TEAM_TOTAL": "HOME_TEAM_TOTAL",
        "ALT_AWAY_TEAM_TOTAL": "AWAY_TEAM_TOTAL"}
SIDES = {"SPREAD": ("HOME", "AWAY"), "MONEYLINE": ("HOME", "AWAY"), "GAME_TOTAL": ("OVER", "UNDER"),
         "HOME_TEAM_TOTAL": ("OVER", "UNDER"), "AWAY_TEAM_TOTAL": ("OVER", "UNDER")}
TIER_RESEARCH = "RESEARCH"
NO_POSITIVE_EV = "NO_POSITIVE_EV"


def base(mt: str) -> str:
    return BASE.get(mt, mt)


class Dist:
    """Cumulative views of one GameJointPMF (margin, total, home, away) for O(1) side probabilities."""

    def __init__(self, g: GameJointPMF):
        self.g = g
        self._v = {}
        for k, (vals, p) in (("M", g.margin()), ("T", g.total()), ("H", g.home()), ("A", g.away())):
            self._v[k] = (int(vals[0]), np.asarray(p, float), np.concatenate([[0.0], np.cumsum(p)]))

    def p_eq(self, k: str, x: float) -> float:
        lo, p, _ = self._v[k]
        if not float(x).is_integer():
            return 0.0
        i = int(x) - lo
        return float(p[i]) if 0 <= i < len(p) else 0.0

    def p_lt(self, k: str, x: float) -> float:
        """P(V < x)."""
        lo, p, c = self._v[k]
        i = int(math.ceil(x)) - lo                     # values v < x  <=>  v <= ceil(x) - 1
        return float(c[min(max(i, 0), len(p))])

    def p_gt(self, k: str, x: float) -> float:
        return max(0.0, 1.0 - self.p_lt(k, x) - self.p_eq(k, x))


def side_probs(d: Dist, market_type: str, side: str, point: float | None) -> tuple[float, float, float]:
    """(P(win), P(lose), P(push)) of one side; sums to 1."""
    mt = base(market_type)
    if mt == "MONEYLINE":
        hw = d.p_gt("M", 0)
        aw = d.p_lt("M", 0)
        win = hw if side == "HOME" else aw
        return win, 1.0 - win, 0.0
    if point is None or not math.isfinite(float(point)):
        raise ValueError(f"{market_type} {side}: point required")
    p = float(point)
    if mt == "SPREAD":
        if side == "HOME":                              # M + p > 0  <=>  M > -p
            win, push = d.p_gt("M", -p), d.p_eq("M", -p)
        else:                                           # A + p > H  <=>  M < p
            win, push = d.p_lt("M", p), d.p_eq("M", p)
        return win, max(0.0, 1.0 - win - push), push
    k = {"GAME_TOTAL": "T", "HOME_TEAM_TOTAL": "H", "AWAY_TEAM_TOTAL": "A"}[mt]
    over, push = d.p_gt(k, p), d.p_eq(k, p)
    under = max(0.0, 1.0 - over - push)
    return (over, under, push) if side == "OVER" else (under, over, push)


def model_prob(win: float, push: float) -> float:
    return win / (1.0 - push) if push < 1.0 else 0.0


def expected_value(win: float, lose: float, decimal: float) -> float:
    return float(win * (decimal - 1.0) - lose)


def no_vig(d_side, d_other):
    try:
        a, b = float(d_side), float(d_other)
    except (TypeError, ValueError):
        return None
    if not (a > 1 and b > 1):
        return None
    return (1 / a) / (1 / a + 1 / b)


def fair_american(p: float) -> float | None:
    if not 0 < p < 1:
        return None
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def opposite(market_type: str, side: str, point):
    """The same-book complement of a side (for the de-vig pair)."""
    mt = base(market_type)
    a, b = SIDES[mt]
    other = b if side == a else a
    if mt == "SPREAD":
        return other, None if point is None else -float(point)
    return other, point


def summary(g: GameJointPMF) -> dict:
    """Model fair lines of the core markets (the Board's MODEL_* columns and fair-price context)."""
    d = Dist(g)
    mo = g.moments()
    return {"MODEL_HOME_SCORE": mo["exp_home"], "MODEL_AWAY_SCORE": mo["exp_away"], "MODEL_MARGIN": mo["exp_margin"],
            "MODEL_TOTAL": mo["exp_total"], "P_HOME_WIN": d.p_gt("M", 0), "P_AWAY_WIN": d.p_lt("M", 0),
            "SD_MARGIN": mo["sd_margin"], "SD_TOTAL": mo["sd_total"], "RHO_MT": mo["rho_mt"]}


# ============================================================================ offers -> candidates -> best offer
QUOTE_COLS = ("game_id", "book", "market_type", "side", "point", "decimal", "american", "quote_id", "market_last_update",
              "collector_received_at")


def candidates(g: GameJointPMF, q: pd.DataFrame) -> list[dict]:
    """Every eligible (book, market_type, side, point) offer of one game evaluated on the joint PMF. One offer per key:
    the latest market_last_update (then the higher price)."""
    if q is None or not len(q):
        return []
    d = Dist(g)
    best: dict = {}
    for r in q.to_dict(orient="records"):
        dec = r.get("decimal")
        if dec is None or not (float(dec) > 1) or r.get("market_type") not in CORE + ALTS:
            continue
        pt = r.get("point")
        pt = None if pt is None or (isinstance(pt, float) and math.isnan(pt)) else float(pt)
        if base(r["market_type"]) != "MONEYLINE" and (pt is None or not float(pt * 2).is_integer()):
            continue
        k = (str(r["book"]), r["market_type"], r["side"], pt)
        rank = (pd.Timestamp(r.get("market_last_update") or pd.Timestamp.min.tz_localize("UTC")), float(dec))
        if k not in best or rank > best[k][0]:
            best[k] = (rank, r)
    offers = {k: v[1] for k, v in best.items()}
    out = []
    for (book, mt, side, pt), r in sorted(offers.items(), key=lambda kv: str(kv[0])):
        win, lose, push = side_probs(d, mt, side, pt)
        mp = model_prob(win, push)
        os_, op = opposite(mt, side, pt)
        other = offers.get((book, mt, os_, op))
        fair = no_vig(r["decimal"], None if other is None else other["decimal"])
        dec = float(r["decimal"])
        out.append({"game_id": r.get("game_id"), "book": book, "market_type": mt, "base_market": base(mt), "side": side,
                    "point": pt, "decimal": dec, "american": r.get("american"), "p_win": win, "p_lose": lose,
                    "p_push": push, "model_p": mp, "fair_p": fair, "edge": None if fair is None else mp - fair,
                    "ev": expected_value(win, lose, dec), "model_fair_american": fair_american(mp),
                    "paired": other is not None, "quote_id": r.get("quote_id"),
                    "market_last_update": r.get("market_last_update"),
                    "collector_received_at": r.get("collector_received_at")})
    return out


def order_key(c: dict) -> tuple:
    e = c["edge"]
    return (-c["ev"], -(e if e is not None else -math.inf), -c["model_p"], -c["decimal"], c["book"],
            -math.inf if c["point"] is None else c["point"], c["side"])


def best_by_market(cands: list[dict], alternates: bool = False) -> dict:
    """{market_type: best candidate} over books x lines x sides; core markets only unless `alternates`."""
    out: dict = {}
    for c in cands:
        if c["market_type"] in ALTS and not alternates:
            continue
        if c["market_type"] not in out or order_key(c) < order_key(out[c["market_type"]]):
            out[c["market_type"]] = c
    return out


def decision(c: dict) -> dict:
    """SHADOW decision fields (Release B): never actionable; the reason says why a positive-EV row is not a bet."""
    pos = c["ev"] > 0
    return {"PICK_SIDE": c["side"] if pos else NO_POSITIVE_EV, "IS_ACTIONABLE": False, "SELECTION_TIER": TIER_RESEARCH,
            "DECISION": "SHADOW_RESEARCH" if pos else "PASS",
            "NO_BET_REASON": "GAME_MARKETS_NOT_VALIDATED (Release B shadow)" if pos else NO_POSITIVE_EV}


# ============================================================================ coherence
def coherence(g: GameJointPMF, lines: dict | None = None) -> dict:
    """PMF sum, zero tie mass, ML complement, spread / total / team-total monotonicity over a line ladder."""
    d = Dist(g)
    v = g.validate()
    hw, aw = d.p_gt("M", 0), d.p_lt("M", 0)
    lad = np.arange(-40, 40.5, 0.5)
    sp = [d.p_gt("M", -p) for p in lad]                               # home cover prob as the home line rises
    tl = np.arange(90, 200.5, 0.5)
    ov = [d.p_gt("T", t) for t in tl]
    tt = np.arange(40, 110.5, 0.5)
    ho = [d.p_gt("H", t) for t in tt]
    ao = [d.p_gt("A", t) for t in tt]
    mono = lambda xs, inc: all((b - a) * (1 if inc else -1) >= -1e-12 for a, b in zip(xs, xs[1:]))   # noqa: E731
    sides_ok = True
    for p in (-7.5, -3.0, 0.0, 2.5, 11.0):
        for mt, ss in (("SPREAD", ("HOME", "AWAY")), ("GAME_TOTAL", ("OVER", "UNDER"))):
            pt = p if mt == "SPREAD" else 140 + p
            for s in ss:
                w, l, u = side_probs(d, mt, s, pt)
                sides_ok &= abs(w + l + u - 1) < 1e-9 and min(w, l, u) >= 0
    out = {**v, "ML_SUM": hw + aw, "ML_OK": abs(hw + aw - 1) < 1e-9, "SPREAD_MONOTONE": mono(sp, True),
           "TOTAL_MONOTONE": mono(ov, False), "TEAM_TOTAL_MONOTONE": mono(ho, False) and mono(ao, False),
           "SIDES_SUM_TO_ONE": bool(sides_ok)}
    out["COHERENT"] = bool(out["VALID"] and out["ML_OK"] and out["SPREAD_MONOTONE"] and out["TOTAL_MONOTONE"]
                           and out["TEAM_TOTAL_MONOTONE"] and out["SIDES_SUM_TO_ONE"])
    return out
