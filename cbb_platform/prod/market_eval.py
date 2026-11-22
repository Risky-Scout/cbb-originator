"""Exact-line market evaluation of ONE game x player x stat (CBB_Boards row, Grades / Performance evidence).

Pure functions: no network, no file IO, no model. The predictive mathematics are unchanged: every probability comes
from the persisted discrete PMF through prod.pricing.price_line (props_v1_pricing.price), EV from
prod.pricing.expected_value, the de-vig from prod.exports.no_vig (the existing proportional method). The model
projection (PMF mean) is display only and never enters a decision.

Input: the quote rows of the game's latest pre-cutoff observation for this player x stat (prod.quote_obs: already
normalized, strictly matched and freshness-checked; quote_status VALID rows only are eligible, alternates never).

For EVERY eligible offer (book x line x side; the latest market_last_update per book x line x side):
    P(OVER) = P(X > L), P(UNDER) = P(X < L), P(PUSH) = P(X = L)           (exact line, discrete PMF)
    Model Probability = P(side | no push) = P(side) / (1 - P(PUSH))        (comparable to a de-vigged two-way price)
    Fair Market Probability = no_vig of the SAME book's two sides at the SAME line (None when the book lacks a side)
    Probability Edge = Model Probability - Fair Market Probability          (None without a same-book pair)
    EV (per unit staked, push-aware) = P(win) (decimal - 1) - P(lose)       (a push refunds the stake: never a loss)

Best offer (deterministic; exact comparisons, no rounding): highest EV, then highest probability edge (missing edge
ranks last), then highest model probability, then highest payout (decimal odds), then bookmaker key ascending, then
line ascending, then OVER before UNDER (only identical offers can reach the last two).

Preferred Side = the best offer's side whenever any candidate has EV > 0 (the best offer then has EV > 0), else
NO_POSITIVE_EV. Threshold screen (reporting) = Preferred Side in (OVER, UNDER) and EV >= ACTION_MIN_EV and edge >= ACTION_MIN_EDGE
and model probability >= ACTION_MIN_MODEL_PROBABILITY and RELIABLE, where RELIABLE = at least RELIABLE_MIN_BOOKS
distinct books quote a two-sided (de-viggable) price at the selected line, OR the selected line is the consensus line
with consensus confidence MEDIUM / HIGH (prod.consensus: >= 3 books at the line). Selection Tier (a REPORTING view of
the screen): 65_PLUS (screen and model probability >= 0.65), 60_PLUS (screen and >= 0.60), ACTIONABLE (screen; the
historical label), else PASS_NO_EDGE. Is Actionable = screen AND ACTIONABILITY_VALIDATED (False: not validated OOS).
Consensus fields (prod.consensus) are informational only.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from cbb_platform.prod import consensus as CN, pricing as PX

EVAL_VERSION = "cbb-market-eval-1.0.0"
ACTION_MIN_EV = 0.03
ACTION_MIN_EDGE = 0.03
ACTION_MIN_MODEL_PROBABILITY = 0.54
RELIABLE_MIN_BOOKS = 2
RELIABLE_CONSENSUS_CONFIDENCE = ("MEDIUM", "HIGH")
TIER_65, TIER_60, TIER_ACT, TIER_PASS = "65_PLUS", "60_PLUS", "ACTIONABLE", "PASS_NO_EDGE"
TIERS = (TIER_65, TIER_60, TIER_ACT, TIER_PASS)
# Selection tiers are REPORTING VIEWS of the threshold screen above, not validated wagering thresholds. The 2026-11-22
# corrected-evidence audit (docs/audits/2026-11-22) found no selection rule, edge / EV / probability / disagreement band
# or STAT x ROLE x SIDE cell whose OOS proper scores beat the de-vigged market (information slope 0.14 +- 0.13), so
# Is Actionable stays NO until a policy is validated OOS: actionable = screen AND ACTIONABILITY_VALIDATED.
ACTIONABILITY_VALIDATED = False
ACTIONABILITY_STATUS = "NOT_VALIDATED_REPORTING_TIERS_ONLY"
NO_POSITIVE_EV = "NO_POSITIVE_EV"
ORDER = ("EV", "PROBABILITY_EDGE", "MODEL_PROBABILITY", "DECIMAL_ODDS", "BOOKMAKER_KEY")
SIDES = ("OVER", "UNDER")


def _t(x):
    return CN._t(x)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def decimal_to_american(d: float) -> int | None:
    """The executable American price of a decimal price (display; American odds are integers)."""
    if d is None or d <= 1:
        return None
    return int(round((d - 1) * 100)) if d >= 2 else int(round(-100 / (d - 1)))


def no_vig(d_side, d_other):
    """(1/d_side) / (1/d_side + 1/d_other): the existing production de-vig (prod.exports.no_vig)."""
    a, b = _num(d_side), _num(d_other)
    if a is None or b is None or a <= 1 or b <= 1:
        return None
    ia, ib = 1.0 / a, 1.0 / b
    return ia / (ia + ib)


def eligible(q: pd.DataFrame) -> list[dict]:
    """Eligible offers of one player x stat: VALID, non-alternate rows with a finite decimal > 1 and a non-negative
    line; one per (book, line, side): the latest market_last_update (then the higher price, for determinism)."""
    if q is None or not len(q):
        return []
    why = CN.row_exclusions(q).tolist()
    best: dict = {}
    for r, w in zip(q.to_dict(orient="records"), why):
        if w or r.get("side") not in SIDES:
            continue
        d, ln = _num(r.get("decimal_odds")), _num(r.get("line"))
        if d is None or d <= 1 or ln is None or ln < 0:
            continue
        k = (str(r["sportsbook"]), ln, r["side"])
        rank = (_t(r.get("market_last_update")) or pd.Timestamp.min.tz_localize("UTC"), d)
        if k not in best or rank > best[k][0]:
            best[k] = (rank, {**r, "_d": d, "_line": ln, "_book": str(r["sportsbook"])})
    return [v[1] for _, v in sorted(best.items())]


def candidates(pmf: np.ndarray, q: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """(evaluated candidates, rejected offers). One candidate per eligible book x line x side."""
    offers = eligible(q)
    by = {(o["_book"], o["_line"], o["side"]): o for o in offers}
    priced: dict = {}
    out, rejected = [], []
    for o in offers:
        ln = o["_line"]
        if ln not in priced:
            try:
                priced[ln] = PX.price_line(pmf, ln)
            except PX.InvalidProbabilities as e:
                priced[ln] = e
        pr = priced[ln]
        if isinstance(pr, Exception):
            rejected.append({"book": o["_book"], "line": ln, "side": o["side"], "reason": getattr(pr, "reason", "INVALID"),
                             "american": _num(o.get("american_odds")), "decimal": o["_d"],
                             "market_last_update": o.get("market_last_update"),
                             "collector_received_at": o.get("collector_received_at"), "quote_id": quote_id(o)})
            continue
        win, lose = (pr["p_over"], pr["p_under"]) if o["side"] == "OVER" else (pr["p_under"], pr["p_over"])
        mp = pr["p_over_nonpush"] if o["side"] == "OVER" else pr["p_under_nonpush"]
        other = by.get((o["_book"], ln, "UNDER" if o["side"] == "OVER" else "OVER"))
        fair = no_vig(o["_d"], None if other is None else other["_d"])
        out.append({"book": o["_book"], "line": ln, "side": o["side"], "decimal": o["_d"],
                    "american": _num(o.get("american_odds")), "p_win": win, "p_lose": lose, "p_push": pr["p_push"],
                    "model_p": mp, "fair_p": fair, "edge": None if fair is None else mp - fair,
                    "ev": PX.expected_value(win, lose, o["_d"]),
                    "model_fair_american": pr["fair_over_american"] if o["side"] == "OVER" else pr["fair_under_american"],
                    "paired": other is not None, "market_last_update": o.get("market_last_update"),
                    "collector_received_at": o.get("collector_received_at"), "quote_age_seconds": CN._age(o),
                    "provider_event_id": o.get("provider_event_id"), "market_key": o.get("market_key"),
                    "quote_id": quote_id(o)})
    return out, rejected


# ---------------------------------------------------------------- persisted candidate-offer set (replay evidence)
# Every offer of one game x player x stat seen at the decision observation, written once into its frozen evaluation:
# the priced candidates (status ELIGIBLE), the unpriceable ones (UNPRICEABLE:<reason>) and the quote rows excluded by
# the normalizer / matcher / freshness checks (status = the exclusion reason, unpriced). game / player / stat and the
# evaluation timestamp live once on the parent record (no duplication). Values are stored unrounded, so selection
# rules replay exactly (prod.selection_replay). EV_GROSS == EV_NET: no commission is modelled (as on the game board).
OFFER_SET_SCHEMA = "cbb-candidate-offers-1"
OFFER_COLS = ("book", "line", "side", "american", "decimal", "p_win", "p_lose", "p_push", "model_p", "fair_p", "edge",
              "ev_gross", "ev_net", "paired", "market_last_update", "collector_received_at", "quote_id", "status")
ELIGIBLE = "ELIGIBLE"


def _iso(x):
    t = _t(x)
    return None if t is None else t.isoformat()


def quote_id(r: dict) -> str:
    """Deterministic id of one player prop quote (provider event | book | market key | side | line)."""
    return f"{r.get('provider_event_id')}|{r.get('sportsbook', r.get('book'))}|{r.get('market_key')}|{r.get('side')}|" \
           f"{_num(r.get('line'))}"


def offer_set(q: pd.DataFrame, cands: list[dict], rejected: list[dict]) -> list[list]:
    """Compact rows (OFFER_COLS order) of every offer of one player x stat; deterministic order."""
    out = []
    for c in cands:
        out.append([c["book"], c["line"], c["side"], c["american"], c["decimal"], c["p_win"], c["p_lose"], c["p_push"],
                    c["model_p"], c["fair_p"], c["edge"], c["ev"], c["ev"], bool(c["paired"]),
                    _iso(c["market_last_update"]), _iso(c["collector_received_at"]), c["quote_id"], ELIGIBLE])
    for r in rejected:
        out.append([r["book"], r["line"], r["side"], r.get("american"), r.get("decimal"), None, None, None, None, None,
                    None, None, None, None, _iso(r.get("market_last_update")), _iso(r.get("collector_received_at")),
                    r.get("quote_id"), f"UNPRICEABLE:{r['reason']}"])
    if q is not None and len(q):
        seen = set()
        for x, w in zip(q.to_dict(orient="records"), CN.row_exclusions(q).tolist()):
            if not w:
                continue
            row = [str(x.get("sportsbook")), _num(x.get("line")), x.get("side"), _num(x.get("american_odds")),
                   _num(x.get("decimal_odds")), None, None, None, None, None, None, None, None, None,
                   _iso(x.get("market_last_update")), _iso(x.get("collector_received_at")), quote_id(x), w]
            k = tuple(str(v) for v in row)
            if k not in seen:
                seen.add(k)
                out.append(row)
    return sorted(out, key=lambda r: (r[17] != ELIGIBLE, str(r[0]), -1 if r[1] is None else r[1], str(r[2]),
                                      str(r[14]), str(r[17])))


def order_key(c: dict) -> tuple:
    """Sort key: ascending = better (EV, edge, model probability, payout desc; book, line asc; OVER first)."""
    e = c["edge"]
    return (-c["ev"], -(e if e is not None else -math.inf), -c["model_p"], -c["decimal"], c["book"], c["line"],
            0 if c["side"] == "OVER" else 1)


def best_offer(cands: list[dict]) -> dict | None:
    return min(cands, key=order_key) if cands else None


def reliable(cands: list[dict], best: dict, cons: dict) -> tuple[bool, int]:
    """(reliable, books with a two-sided price at the selected line)."""
    books = len({c["book"] for c in cands if c["line"] == best["line"] and c["paired"]})
    at_consensus = cons.get("consensus_line") is not None and float(cons["consensus_line"]) == best["line"] and \
        cons.get("consensus_confidence") in RELIABLE_CONSENSUS_CONFIDENCE
    return books >= RELIABLE_MIN_BOOKS or at_consensus, books


def tier(actionable: bool, model_p) -> str:
    if not actionable:
        return TIER_PASS
    return TIER_65 if model_p >= 0.65 else TIER_60 if model_p >= 0.60 else TIER_ACT


def evaluate(pmf: np.ndarray, q: pd.DataFrame) -> dict:
    """The market fields of one Board row. market = False when no eligible, priceable offer exists (model-only)."""
    cons, audit = CN.consensus(q if q is not None else pd.DataFrame())
    cands, rejected = candidates(pmf, q)
    out = {"eval_version": EVAL_VERSION, "market": bool(cands), "candidates": len(cands), "rejected": rejected,
           "consensus": cons, "audit": audit, "best": None, "preferred_side": None, "actionable": False,
           "tier": None, "reliable": False, "books_at_line": 0, "books_quoted": len({c["book"] for c in cands}),
           "offer_set": offer_set(q, cands, rejected)}
    b = best_offer(cands)
    if b is None:
        return out
    rel, n = reliable(cands, b, cons)
    pref = b["side"] if b["ev"] > 0 else NO_POSITIVE_EV
    screen = pref in SIDES and b["ev"] >= ACTION_MIN_EV and b["edge"] is not None and b["edge"] >= ACTION_MIN_EDGE \
        and b["model_p"] >= ACTION_MIN_MODEL_PROBABILITY and rel
    out.update(best=b, preferred_side=pref, screen_pass=bool(screen),
               actionable=bool(screen and ACTIONABILITY_VALIDATED), actionability_status=ACTIONABILITY_STATUS,
               tier=tier(screen, b["model_p"]), reliable=bool(rel), books_at_line=n)
    return out
