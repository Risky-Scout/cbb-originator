"""Internal / shadow Best Executable Wager board.

For each (game, player, market, line, side) quoted by a VALID, MATCHED, FRESH quote, the price is read from the game's
single draw bank; BestOdds is the highest decimal price among those quotes (ties: sportsbook key order). No valid quote
-> no executable edge. WAGERING_ENABLED = False: no row is ever wager-eligible in this phase.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC, pricing as PX, readiness as RD

BOARD_COLS = ["Game", "Player", "Bet", "Projection", "Line", "WinChance", "PushProbability", "FairPrice", "BestOdds",
              "Sportsbook", "Edge", "PredictionAsOf", "QuoteAvailableAt", "CollectorReceivedAt", "ModelVersion",
              "EligibilityState", "SuppressionReason"]


def quote_freshness(q: pd.DataFrame, prediction_as_of, tips: dict) -> pd.DataFrame:
    """STALE_QUOTE: older than QUOTE_MAX_AGE at prediction time. POST_CUTOFF_QUOTE: available or collected after the
    prediction / at or after tip (not knowable when the forecast was made)."""
    q = q.copy()
    aso = pd.Timestamp(prediction_as_of)
    ok = q["quote_status"] == "VALID"
    tip = q["canonical_game_id"].map(tips)
    post = ok & ((q["quote_available_at"] > aso) | (q["collector_received_at"] > aso)
                 | (tip.notna() & ((q["quote_available_at"] >= tip) | (q["collector_received_at"] >= tip))))
    q.loc[post, ["quote_status", "suppression_reason"]] = ["SUPPRESSED", "POST_CUTOFF_QUOTE"]
    ok = q["quote_status"] == "VALID"
    stale = ok & ((aso - q["quote_available_at"]).dt.total_seconds() > PC.QUOTE_MAX_AGE_SECONDS)
    q.loc[stale, ["quote_status", "suppression_reason"]] = ["SUPPRESSED", "STALE_QUOTE"]
    return q


def build(dists: pd.DataFrame, quotes: pd.DataFrame, meta: dict) -> tuple[pd.DataFrame, list[dict]]:
    """dists: simulate.market_distributions rows (+ game/player labels, game_reasons, player_suppression, sims, bank).
    Returns (board rows, priced records for the ledger)."""
    priced = []
    q = quotes[quotes["quote_status"] == "VALID"] if len(quotes) else quotes
    for d in dists.itertuples(index=False):
        qq = q[(q["canonical_game_id"] == d.game_id) & (q["canonical_player_id"] == d.player_id)
               & (q["stat"] == d.market)] if len(q) else q
        lines = sorted(qq["line"].unique()) if len(qq) else []
        base = {"game_id": d.game_id, "game": d.game_label, "player_id": d.player_id, "player": d.player_label,
                "team_id": d.team_id, "market": d.market, "projection": d.projection, "p_play": d.p_play,
                "distribution_method": d.method, "pmf_q75": PX.quantile(d.pmf, 0.75), "bank_sha256": d.bank_sha256,
                "game_reasons": list(d.game_reasons), "player_suppression": d.player_suppression}
        if d.method == "NOT_PRICED":                     # suppressed player: recorded with its reason, never priced
            priced.append({**base, "line": None, "side": None, "row_reasons": [d.player_suppression]})
            continue
        if not lines:
            priced.append({**base, "line": None, "side": None, "row_reasons": ["NO_VALID_QUOTE"]})
            continue
        for line in lines:
            try:
                pr = PX.price_line(d.pmf, float(line))
                reasons_line = []
            except PX.InvalidProbabilities as e:
                pr, reasons_line = None, [e.reason]
            for side in PX.SIDES:
                sq = qq[(qq["line"] == line) & (qq["side"] == side)].sort_values(["decimal_odds", "sportsbook"],
                                                                                 ascending=[False, True])
                rec = {**base, "line": float(line), "side": side, "row_reasons": list(reasons_line)}
                if pr is not None:
                    sv = PX.side_view(pr, side)
                    rec.update({"probability": sv["win"], "lose_probability": sv["lose"], "push_probability": sv["push"],
                                "p_nonpush": pr["p_nonpush"], "fair_decimal": sv["fair_decimal"],
                                "fair_american": sv["fair_american"],
                                "mcse": PX.mcse(sv["win"], meta["sims"]) if d.method == "BANK_DRAWS" else 0.0})
                if len(sq) and pr is not None:
                    b = sq.iloc[0]
                    rec.update({"best_decimal_odds": float(b["decimal_odds"]), "best_american_odds": b["american_odds"],
                                "sportsbook": b["sportsbook"], "quote_available_at": b["quote_available_at"],
                                "collector_received_at": b["collector_received_at"],
                                "edge": PX.expected_value(rec["probability"], rec["lose_probability"], float(b["decimal_odds"]))})
                elif not len(sq):
                    rec["row_reasons"].append("NO_VALID_QUOTE")
                priced.append(rec)
    rows = []
    for r in priced:
        ev = RD.evaluate(meta["run_reasons"], r["game_reasons"],
                         r["row_reasons"] + ([r["player_suppression"]] if r["player_suppression"] else []))
        r["readiness_state"] = ev["readiness_state"]
        r["eligibility_state"] = ev["eligibility_state"]
        r["suppression_reason"] = ";".join(ev["reasons"])
        if r.get("side") is None:
            continue
        rows.append({"Game": r["game"], "Player": r["player"], "Bet": f"{r['side'].title()} {r['line']:g} {r['market']}",
                     "Projection": round(r["projection"], 3), "Line": r["line"],
                     "WinChance": r.get("probability"), "PushProbability": r.get("push_probability"),
                     "FairPrice": r.get("fair_decimal"), "BestOdds": r.get("best_decimal_odds"),
                     "Sportsbook": r.get("sportsbook"), "Edge": r.get("edge"), "PredictionAsOf": meta["prediction_as_of"],
                     "QuoteAvailableAt": r.get("quote_available_at"), "CollectorReceivedAt": r.get("collector_received_at"),
                     "ModelVersion": meta["model_version"], "EligibilityState": r["eligibility_state"],
                     "SuppressionReason": r["suppression_reason"]})
    board = pd.DataFrame(rows, columns=BOARD_COLS)
    if len(board):
        board = board.sort_values(["Game", "Player", "Bet"], kind="mergesort").reset_index(drop=True)
    return board, priced
