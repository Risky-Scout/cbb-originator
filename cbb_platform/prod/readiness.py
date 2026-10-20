"""ONE fail-closed readiness evaluator.

States: BLOCKED < SHADOW_ONLY < PUBLIC_READY. This phase's ceiling is SHADOW_ONLY: PUBLIC_READY is never returned,
whatever the inputs, and WAGERING_ENABLED is False. Any blocking reason at the run, game or row level blocks the row;
an unknown reason code also blocks (fail closed). A blocked forecast is still written to the shadow ledger with its
reasons; blocking concerns eligibility, not recording.
"""
from __future__ import annotations

from cbb_platform.prod import config as PC

RUN_REASONS = ("ARTIFACT_HASH_MISMATCH", "WRONG_PRODUCTION_SEASON", "STATE_UPDATE_FAILED", "STALE_STATE",
               "CUTOFF_UNRESOLVED", "BUNDLE_CHECKSUM_FAILURE", "LIVE_DATA_FEED_UNAVAILABLE")
GAME_REASONS = ("FUTURE_LEAKAGE", "AMBIGUOUS_GAME", "UNMATCHED_GAME", "NON_DI_GAME", "UNSUPPORTED_ROTATION_SHOCK",
                "NON_PSD_DEPENDENCE", "SCORING_FAILURE", "MISSING_PLAYER_STATE", "STALE_STATE",
                "PREDICTION_AFTER_CUTOFF", "PMF_NOT_NORMALIZED")
ROW_REASONS = ("AMBIGUOUS_PLAYER", "UNMATCHED_PLAYER", "UNSUPPORTED_MARKET", "INVALID_PROBABILITIES",
               "PMF_NOT_NORMALIZED", "STALE_QUOTE", "POST_CUTOFF_QUOTE", "INVALID_QUOTE", "MISSING_PLAYER_STATE",
               "CONFIRMED_UNAVAILABLE", "NO_VALID_QUOTE", "UNMATCHED_GAME", "AMBIGUOUS_GAME")
REQUIRED_CODES = ("ARTIFACT_HASH_MISMATCH", "WRONG_PRODUCTION_SEASON", "STATE_UPDATE_FAILED", "STALE_STATE",
                  "FUTURE_LEAKAGE", "CUTOFF_UNRESOLVED", "AMBIGUOUS_GAME", "AMBIGUOUS_PLAYER", "UNSUPPORTED_MARKET",
                  "INVALID_PROBABILITIES", "PMF_NOT_NORMALIZED", "NON_PSD_DEPENDENCE", "MISSING_PLAYER_STATE",
                  "UNSUPPORTED_ROTATION_SHOCK", "STALE_QUOTE", "POST_CUTOFF_QUOTE", "INVALID_QUOTE", "SCORING_FAILURE",
                  "BUNDLE_CHECKSUM_FAILURE")
KNOWN = set(RUN_REASONS) | set(GAME_REASONS) | set(ROW_REASONS)
assert set(REQUIRED_CODES) <= KNOWN


def cap(state: str) -> str:
    """The phase ceiling: never above SHADOW_ONLY."""
    if state not in PC.READINESS_STATES:
        return "BLOCKED"
    order = {s: i for i, s in enumerate(PC.READINESS_STATES)}
    return state if order[state] <= order[PC.MAX_READINESS_STATE] else PC.MAX_READINESS_STATE


def evaluate(run_reasons=(), game_reasons=(), row_reasons=()) -> dict:
    reasons = sorted({r for r in (*run_reasons, *game_reasons, *row_reasons) if r})
    unknown = [r for r in reasons if r not in KNOWN]
    if unknown:
        reasons = sorted(set(reasons) | {"SCORING_FAILURE"})
    if PC.VALIDATED_PREDICTION_CUTOFF in (None, "", "UNRESOLVED"):
        reasons = sorted(set(reasons) | {"CUTOFF_UNRESOLVED"})
    state = "BLOCKED" if reasons else "SHADOW_ONLY"
    state = cap(state)
    return {"readiness_state": state, "reasons": reasons, "unknown_reasons": unknown,
            "wager_eligible": False, "wagering_enabled": PC.WAGERING_ENABLED,
            "eligibility_state": state if state == "BLOCKED" else "SHADOW_ONLY_NO_WAGERING"}


def run_state(run_reasons) -> str:
    return evaluate(run_reasons)["readiness_state"]
