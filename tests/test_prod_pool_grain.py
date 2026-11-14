"""Canonical production grain (prod/pool_grain.py): one row per game x player. Regression for the 2026-11-14
InvalidIndexError (a transfer carried by both teams of one game: PRIOR_APPEARANCE_CONFIRMED for the new team,
stale PRIOR_SEASON_RETURNER for the old one). TEST fixtures only."""
import pandas as pd
import pytest

from cbb_platform.prod import pool_grain as PG


def _cand(rows):
    return pd.DataFrame(rows, columns=["canonical_game_id", "canonical_team_id", "canonical_player_id", "pool_evidence", "x"])


def test_unique_frame_is_returned_untouched():
    c = _cand([("G1", "T1", "P1", "PRIOR_APPEARANCE_CONFIRMED", 1), ("G1", "T2", "P2", "PRIOR_SEASON_RETURNER", 2)])
    out, reasons, rec = PG.resolve(c)
    assert out is c and reasons == {} and rec == []


def test_cross_team_duplicate_keeps_strongest_evidence():
    c = _cand([("G1", "T_OLD", "P1", "PRIOR_SEASON_RETURNER", 1), ("G1", "T_NEW", "P1", "PRIOR_APPEARANCE_CONFIRMED", 2),
               ("G1", "T_OLD", "P2", "PRIOR_SEASON_RETURNER", 3), ("G2", "T_OLD", "P1", "PRIOR_SEASON_RETURNER", 4)])
    out, reasons, rec = PG.resolve(c)
    assert reasons == {}
    assert out.sort_values("x")["x"].tolist() == [2, 3, 4]               # only the stale cross-team row is removed
    assert rec == [{"game_id": "G1", "player_id": "P1", "resolution": "STRONGEST_POOL_EVIDENCE", "kept_team_id": "T_NEW",
                    "candidates": [{"team_id": "T_OLD", "pool_evidence": "PRIOR_SEASON_RETURNER"},
                                   {"team_id": "T_NEW", "pool_evidence": "PRIOR_APPEARANCE_CONFIRMED"}]}]
    PG.assert_canonical_key_is_unique(out, ["canonical_game_id", "canonical_player_id"], "t")


def test_equal_evidence_fails_the_game_closed():
    c = _cand([("G1", "T1", "P1", "PRIOR_SEASON_RETURNER", 1), ("G1", "T2", "P1", "PRIOR_SEASON_RETURNER", 2),
               ("G1", "T1", "P2", "PRIOR_SEASON_RETURNER", 3), ("G2", "T3", "P9", "PRIOR_SEASON_RETURNER", 4)])
    out, reasons, rec = PG.resolve(c)
    assert reasons == {"G1": [PG.AMBIGUOUS]} and out["x"].tolist() == [4]
    assert rec[0]["resolution"] == PG.AMBIGUOUS and rec[0]["kept_team_id"] is None


def test_same_team_duplicate_is_never_guessed():
    c = _cand([("G1", "T1", "P1", "PRIOR_SEASON_RETURNER", 1), ("G1", "T1", "P1", "PRIOR_APPEARANCE_CONFIRMED", 2)])
    with pytest.raises(AssertionError, match="game x team x player"):
        PG.resolve(c)


def test_unknown_evidence_class_raises():
    c = _cand([("G1", "T1", "P1", "SOMETHING_NEW", 1), ("G1", "T2", "P1", "PRIOR_SEASON_RETURNER", 2)])
    with pytest.raises(ValueError, match="EVIDENCE_RANK"):
        PG.resolve(c)


def test_assertion_names_the_duplicate_keys():
    r = pd.DataFrame({"game_id": ["G-44ee75bf566355a4"] * 2, "player_id": ["P-f8ce9aeaf18227b9"] * 2})
    with pytest.raises(AssertionError, match="G-44ee75bf566355a4.*P-f8ce9aeaf18227b9"):
        PG.assert_canonical_key_is_unique(r, ["game_id", "player_id"], "model inputs")
