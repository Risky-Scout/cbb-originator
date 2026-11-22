"""Deterministic selection-rule replay from frozen evidence (research / audit only; never on the production decision path).

Input: a frozen player MARKET_EVALUATION record carrying its candidate-offer set (prod.market_eval.OFFER_COLS rows,
schema cbb-candidate-offers-1; for evaluations frozen before that field existed, the set is reconstructed from the
persisted PMF + decision observation and accepted only if it reproduces the stored best offer exactly).

Exactly FOUR rules are evaluated (no open-ended search):
    MAX_EV               the production rule: prod.market_eval.best_offer (EV, then edge, model p, payout, book, line,
                         side); active when its EV > 0 (else NO_POSITIVE_EV)
    MAX_EDGE             the highest probability edge among offers with a same-book de-vig pair (ties: MAX_EV order);
                         active when edge > 0
    MAX_PROB_POS_EV      the highest model probability among offers with EV > 0 (ties: MAX_EV order)
    CONSERVATIVE_MAX_EV  MAX_EV among offers with EV > 0 AND edge >= CONSERVATIVE_MIN_EDGE (= the production
                         ACTION_MIN_EDGE, not tuned here)
Pure functions; identical input -> identical output (replay_hash).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from cbb_platform.prod import market_eval as ME

RULES = ("MAX_EV", "MAX_EDGE", "MAX_PROB_POS_EV", "CONSERVATIVE_MAX_EV")
INCUMBENT_RULE = "MAX_EV"                       # the production selection rule (prod.market_eval.best_offer)
PLAYER_EVAL_FILE = "market_evaluations.jsonl"   # == prod.market_exports.EVAL_FILE (tested)
GAME_EVAL_FILE = "game_market_evaluations.jsonl"  # == prod.game_exports.EVAL_FILE (tested)
CONSERVATIVE_MIN_EDGE = ME.ACTION_MIN_EDGE
DISAGREEMENT_BUCKETS = ("<=-10pp", "-10..-5pp", "-5..0pp", "0..+5pp", "+5..+10pp", ">+10pp")
PROB_BUCKETS = ((0.0, 0.50, "<50"), (0.50, 0.55, "50-55"), (0.55, 0.60, "55-60"), (0.60, 0.65, "60-65"),
                (0.65, 0.70, "65-70"), (0.70, 1.01, "70+"))
EPS = 1e-6


def offers(record: dict) -> list[dict]:
    """The ELIGIBLE (priced) candidates of a frozen record's offer set, as prod.market_eval candidate dicts."""
    out = []
    for row in record.get("candidate_offer_set") or []:
        c = dict(zip(ME.OFFER_COLS, row))
        if c["status"] != ME.ELIGIBLE:
            continue
        c["ev"] = c["ev_net"]
        out.append(c)
    return out


def _tie(c: dict) -> tuple:
    return ME.order_key(c)


def select(rule: str, cands: list[dict]) -> tuple[dict | None, bool]:
    """(chosen offer or None, active). Deterministic; never rounds."""
    if rule == "MAX_EV":
        b = ME.best_offer(cands)
        return b, bool(b is not None and b["ev"] > 0)
    if rule == "MAX_EDGE":
        p = [c for c in cands if c["edge"] is not None]
        b = min(p, key=lambda c: (-c["edge"],) + _tie(c)) if p else None
        return b, bool(b is not None and b["edge"] > 0)
    if rule == "MAX_PROB_POS_EV":
        p = [c for c in cands if c["ev"] > 0]
        b = min(p, key=lambda c: (-c["model_p"],) + _tie(c)) if p else None
        return b, b is not None
    if rule == "CONSERVATIVE_MAX_EV":
        p = [c for c in cands if c["ev"] > 0 and c["edge"] is not None and c["edge"] >= CONSERVATIVE_MIN_EDGE]
        b = min(p, key=_tie) if p else None
        return b, b is not None
    raise ValueError(f"unknown rule {rule!r}")


def settle(side: str, line: float, actual) -> str | None:
    if actual is None or (isinstance(actual, float) and math.isnan(actual)):
        return None
    if actual == line:
        return "PUSH"
    return "WIN" if (actual > line) == (side == "OVER") else "LOSS"


def units(result: str | None, decimal: float) -> float | None:
    return {"WIN": decimal - 1.0, "LOSS": -1.0, "PUSH": 0.0}.get(result) if result else None


def disagreement_bucket(d) -> str | None:
    """model_probability - market_fair_probability (fractions): <=-10pp | (-10,-5] | (-5,0] | (0,5] | (5,10] | >10pp."""
    if d is None or (isinstance(d, float) and math.isnan(d)):
        return None
    if d <= -0.10:
        return "<=-10pp"
    if d <= -0.05:
        return "-10..-5pp"
    if d <= 0.0:
        return "-5..0pp"
    if d <= 0.05:
        return "0..+5pp"
    if d <= 0.10:
        return "+5..+10pp"
    return ">+10pp"


def prob_bucket(p) -> str | None:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return None
    for lo, hi, name in PROB_BUCKETS:
        if lo <= p < hi:
            return name
    return None


def replay(record: dict, cands: list[dict], actual) -> list[dict]:
    """One row per rule: the offer it picks from `cands` and its settlement against `actual` (the realized stat)."""
    out = []
    for rule in RULES:
        c, active = select(rule, cands)
        row = {"rule": rule, "eval_key": f"{record['game_id']}|{record['player_id']}|{record['stat']}|"
                                         f"{record['slate_date']}", "active": active, "picked": c is not None}
        if c is not None:
            res = settle(c["side"], c["line"], actual)
            row.update(book=c["book"], line=c["line"], side=c["side"], decimal=c["decimal"], model_p=c["model_p"],
                       fair_p=c["fair_p"], edge=c["edge"], ev=c["ev"], p_push=c["p_push"], result=res,
                       units=units(res, c["decimal"]), quote_id=c.get("quote_id"),
                       disagreement=c["edge"], disagreement_bucket=disagreement_bucket(c["edge"]),
                       prob_bucket=prob_bucket(c["model_p"]))
        out.append(row)
    return out


def _ll(p, y):
    p = min(max(p, EPS), 1 - EPS)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def metrics(rows: list[dict]) -> dict:
    """WIN/LOSS rows only for probability scores (a push refunds; model p is the no-push probability)."""
    g = [r for r in rows if r.get("result") in ("WIN", "LOSS")]
    n = len(g)
    if not n:
        return {"N": 0}
    y = [1.0 if r["result"] == "WIN" else 0.0 for r in g]
    p = [r["model_p"] for r in g]
    m = [(r["fair_p"], yy) for r, yy in zip(g, y) if r.get("fair_p") is not None]
    clv = [r["probability_clv"] for r in g if r.get("probability_clv") is not None]
    st = [r for r in rows if r.get("result") in ("WIN", "LOSS", "PUSH")]
    out = {"N": n, "mean_model_p": sum(p) / n, "hit_rate": sum(y) / n, "calibration_gap": sum(p) / n - sum(y) / n,
           "brier": sum((a - b) ** 2 for a, b in zip(p, y)) / n, "log_loss": sum(_ll(a, b) for a, b in zip(p, y)) / n,
           "N_market": len(m),
           "market_brier": sum((a - b) ** 2 for a, b in m) / len(m) if m else None,
           "market_log_loss": sum(_ll(a, b) for a, b in m) / len(m) if m else None,
           "model_brier_on_market_rows": (sum((r["model_p"] - yy) ** 2 for r, yy in zip(g, y)
                                              if r.get("fair_p") is not None) / len(m)) if m else None,
           "mean_market_p": sum(a for a, _ in m) / len(m) if m else None,
           "N_clv": len(clv), "mean_clv": sum(clv) / len(clv) if clv else None,
           "positive_clv_pct": sum(c > 0 for c in clv) / len(clv) if clv else None,
           "roi": sum(r["units"] for r in st) / len(st) if st else None,
           "mean_edge": (sum(r["edge"] for r in g if r.get("edge") is not None) /
                         max(1, sum(r.get("edge") is not None for r in g))),
           "mean_ev": sum(r["ev"] for r in g) / n}
    out.update(_paired(g, y))
    return out


def _paired(g: list[dict], y: list[float]) -> dict:
    """Per-row model-minus-market Brier / log-loss differences on rows with a de-vigged market price (negative = the
    model beats the market) with their standard errors."""
    db = [(r["model_p"] - yy) ** 2 - (r["fair_p"] - yy) ** 2 for r, yy in zip(g, y) if r.get("fair_p") is not None]
    dl = [_ll(r["model_p"], yy) - _ll(r["fair_p"], yy) for r, yy in zip(g, y) if r.get("fair_p") is not None]

    def ms(v):
        if len(v) < 2:
            return None, None
        m = sum(v) / len(v)
        return m, math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1) / len(v))
    (bm, bs), (lm, ls) = ms(db), ms(dl)
    return {"brier_diff_vs_market": bm, "brier_diff_se": bs, "log_loss_diff_vs_market": lm, "log_loss_diff_se": ls}


def replay_hash(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda r: (r["eval_key"], r["rule"])), sort_keys=True,
                                     default=str).encode()).hexdigest()


def matches_stored(record: dict, cands: list[dict]) -> bool:
    """The reconstructed candidate set reproduces the frozen best offer exactly (book, line, side, price, model p, EV)."""
    b = ME.best_offer(cands)
    return b is not None and (b["book"], float(b["line"]), b["side"], b["decimal"], b["model_p"], b["ev"]) == (
        record["book"], float(record["line"]), record["side"], record["decimal_odds"], record["model_probability"],
        record["ev"])


# ---------------------------------------------------------------- aggregation (STAT x ROLE x SIDE, buckets)
ROLES = ("HIGH_MINUTES_STARTER", "STARTER", "ROTATION", "LOW_MINUTES_BENCH", "ROLE_INCREASE", "ROLE_DECREASE",
         "NEW_TEAM_TRANSFER", "INJURY_REPLACEMENT", "RETURNING_FROM_ABSENCE")
ROLES_NOT_IN_FROZEN_EVIDENCE = ("ROLE_INCREASE", "ROLE_DECREASE", "RETURNING_FROM_ABSENCE")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def role_tags(expected_minutes, ctx: dict) -> list[str]:
    """Prediction-time role tags (overlapping) from the frozen record + its own forecast's player context
    (role_diagnostic, transfer_in / cold_start_class, cur_min, team_games); never realized minutes.
    INJURY_REPLACEMENT is a proxy: expected minutes >= 8 above the season-to-date minutes per team game."""
    mm, rd = _num(expected_minutes), ctx.get("role_diagnostic")
    out = []
    if rd == "TOP5_MINUTES":
        out.append("STARTER")
        if mm is not None and mm >= 30:
            out.append("HIGH_MINUTES_STARTER")
    elif rd is not None and mm is not None and mm >= 15:
        out.append("ROTATION")
    if mm is not None and mm < 15:
        out.append("LOW_MINUTES_BENCH")
    if ctx.get("transfer_in") in (True, 1) or ctx.get("cold_start_class") == "COLD_TRANSFER_IN":
        out.append("NEW_TEAM_TRANSFER")
    cur, tg = _num(ctx.get("cur_min")), _num(ctx.get("team_games"))
    if mm is not None and cur is not None and tg is not None and tg >= 2 and mm - cur / tg >= 8:
        out.append("INJURY_REPLACEMENT")
    return out or ["UNKNOWN_ROLE"]


def group_metrics(rows: list[dict], keys) -> dict:
    """{key tuple joined by ' | ': metrics}; keys(row) -> list of key tuples (a row may sit in several groups)."""
    grp: dict = {}
    for r in rows:
        for k in keys(r):
            grp.setdefault(k, []).append(r)
    return {" | ".join(map(str, k)): metrics(v) for k, v in sorted(grp.items(), key=lambda t: tuple(map(str, t[0])))}


# Promotion-matrix flags, declared BEFORE the corrected-evidence results were read (no tuning on them):
CELL_MIN_N = 40
CELL_BAD_CAL_GAP = 0.08
CELL_BAD_BRIER_MARGIN = 0.01
CELL_GOOD_CAL_GAP = 0.05


def cell_flag(m: dict) -> str:
    """INSUFFICIENT_SAMPLE (N < CELL_MIN_N or no market pair) | BAD (model Brier worse than the de-vigged market by >
    CELL_BAD_BRIER_MARGIN, or over-confident by > CELL_BAD_CAL_GAP) | GOOD (model Brier <= market Brier, |gap| <=
    CELL_GOOD_CAL_GAP and mean CLV >= 0) | WATCH."""
    if m.get("N", 0) < CELL_MIN_N or not m.get("N_market") or m.get("market_brier") is None:
        return "INSUFFICIENT_SAMPLE"
    mb, xb = m["model_brier_on_market_rows"], m["market_brier"]
    if mb > xb + CELL_BAD_BRIER_MARGIN or m["calibration_gap"] > CELL_BAD_CAL_GAP:
        return "BAD"
    if mb <= xb and abs(m["calibration_gap"]) <= CELL_GOOD_CAL_GAP and (m.get("mean_clv") or 0.0) >= 0:
        return "GOOD"
    return "WATCH"


# ---------------------------------------------------------------- actionability policy (pure application)
def apply_policy(c: dict, policy: dict, books_at_line: int = 0) -> bool:
    """True when offer `c` passes every bound of `policy`: min_ev, min_edge, min_model_p, max_abs_disagreement,
    min_books, excluded_stats (with c['stat']), excluded_sides. Missing keys = no bound; a missing edge fails any
    edge / disagreement bound."""
    if c is None:
        return False
    e = c.get("edge")
    if "min_ev" in policy and not c["ev"] >= policy["min_ev"]:
        return False
    if "min_edge" in policy and (e is None or e < policy["min_edge"]):
        return False
    if "min_model_p" in policy and c["model_p"] < policy["min_model_p"]:
        return False
    if "max_abs_disagreement" in policy and (e is None or abs(e) > policy["max_abs_disagreement"]):
        return False
    if "min_books" in policy and books_at_line < policy["min_books"]:
        return False
    if c.get("stat") in policy.get("excluded_stats", ()) or c.get("side") in policy.get("excluded_sides", ()):
        return False
    return True


# ---------------------------------------------------------------- prospective evidence window + decision gate
def replayable_evidence(ops_root) -> dict:
    """REPLAYABLE_EVIDENCE_START: the first production frozen_at of a player MARKET_EVALUATION carrying its persisted
    candidate_offer_set (records frozen before it cannot be replayed from decision-time offers and are never
    back-filled), with the count of such records and of the slate dates holding them."""
    root = Path(ops_root) / "evaluations"
    start, n, days = None, 0, []
    for d in sorted(root.iterdir()) if root.exists() else []:
        p = d / PLAYER_EVAL_FILE
        if not p.exists():
            continue
        k = 0
        for line in p.read_text().splitlines():
            if '"candidate_offer_set"' not in line:
                continue
            r = json.loads(line)
            if r.get("record_type") == "MARKET_EVALUATION" and r.get("candidate_offer_set"):
                k += 1
                start = r["frozen_at"] if start is None or r["frozen_at"] < start else start
        if k:
            n += k
            days.append(d.name)
    return {"replayable_evidence_start": start, "replayable_records": n, "replayable_slate_dates": days}


# Decision gate, declared 2026-11-22 BEFORE any prospective (persisted-offer-set) evidence existed. Not to be tuned
# on the prospective sample. Applied to PERSISTED candidate sets only.
GATE_MIN_GRADED = 300          # graded WIN/LOSS active picks of a rule (or of the policy subset)
GATE_MIN_SLATES = 10           # distinct slate dates
GATE_Z = 2.0                   # model must beat the market by > GATE_Z standard errors (paired Brier)
GATE_MAX_ABS_CAL_GAP = 0.05
# The ONE pre-declared simple global actionability candidate (not fitted; motivated by the retrospective audit:
# large disagreements were model location bias, so require consensus support and bound the disagreement).
POLICY_CANDIDATE = {"rule": "CONSERVATIVE_MAX_EV", "min_ev": 1e-12, "min_edge": CONSERVATIVE_MIN_EDGE,
                    "max_abs_disagreement": 0.10, "min_books": 3}
DEFECT_MIN_N = 40              # selection-free STAT x ROLE cell size for a localized predictive defect
DEFECT_MIN_GAP = 0.05          # |mean model P(OVER) - realized OVER rate|, and > GATE_Z SE


def validates(m: dict) -> bool:
    """A rule / policy subset beats the de-vigged market out of sample: enough graded rows, paired Brier difference
    < 0 by more than GATE_Z SE, log loss no worse than the market, calibrated within GATE_MAX_ABS_CAL_GAP."""
    if m.get("N", 0) < GATE_MIN_GRADED or m.get("brier_diff_vs_market") is None or m.get("brier_diff_se") is None:
        return False
    return (m["brier_diff_vs_market"] + GATE_Z * m["brier_diff_se"] < 0 and m["log_loss_diff_vs_market"] < 0
            and abs(m["calibration_gap"]) <= GATE_MAX_ABS_CAL_GAP)


def localized_defects(cells: dict) -> list[str]:
    """Selection-free (one OVER offer per market) cells whose mean model P(OVER) misses the realized rate by more than
    DEFECT_MIN_GAP and GATE_Z binomial SE."""
    out = []
    for k, m in cells.items():
        n = m.get("N", 0)
        if n < DEFECT_MIN_N:
            continue
        h = m["hit_rate"]
        se = math.sqrt(max(h * (1 - h), 1e-9) / n)
        if abs(m["calibration_gap"]) > DEFECT_MIN_GAP and abs(m["calibration_gap"]) > GATE_Z * se:
            out.append(k)
    return out


def decision_gate(rule_metrics: dict, policy_metrics: dict, slates: int, defects: list[str]) -> dict:
    """SELECTION_POLICY_VALIDATED | INSUFFICIENT_SAMPLE | SELECTION_POLICY_FAILED_MODEL_CHALLENGER_JUSTIFIED."""
    cand = {**{r: m for r, m in rule_metrics.items()}, "POLICY_CANDIDATE": policy_metrics}
    ok = [k for k, m in cand.items() if validates(m)]
    enough = slates >= GATE_MIN_SLATES and all(m.get("N", 0) >= GATE_MIN_GRADED for m in rule_metrics.values())
    if ok and slates >= GATE_MIN_SLATES:
        gate = "SELECTION_POLICY_VALIDATED"
    elif enough and defects:
        gate = "SELECTION_POLICY_FAILED_MODEL_CHALLENGER_JUSTIFIED"
    else:
        gate = "INSUFFICIENT_SAMPLE"
    note = ("selection failed on a sufficient sample but no localized predictive defect: no model work justified"
            if gate == "INSUFFICIENT_SAMPLE" and enough and not ok else None)
    return {"gate": gate, "note": note, "validated": ok, "slates": slates, "enough_sample": enough,
            "localized_defects": defects,
            "criteria": {"min_graded": GATE_MIN_GRADED, "min_slates": GATE_MIN_SLATES, "z": GATE_Z,
                         "max_abs_cal_gap": GATE_MAX_ABS_CAL_GAP, "policy_candidate": POLICY_CANDIDATE,
                         "defect_min_n": DEFECT_MIN_N, "defect_min_gap": DEFECT_MIN_GAP}}
