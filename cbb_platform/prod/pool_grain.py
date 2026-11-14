"""Canonical production grain: one row per (canonical_game_id, canonical_player_id).

The frozen Stage 3 pool (stage3/universe.py) records membership evidence per team-season, so a player whose previous
team still carries a PRIOR_SEASON_RETURNER / roster record (no outbound portal record matched) can appear for BOTH
teams of a game in which he faces that team -- e.g. PRIOR_APPEARANCE_CONFIRMED for his new team and
PRIOR_SEASON_RETURNER for his old one. ex.player_game_examples is keyed game x team x player, so this is legal there;
the production scoring path is keyed game x player (market PMFs, availability, board), so it is not.

resolve() is the earliest production-owned boundary (run.daily, directly after identity_conflicts.apply): a player
duplicated across teams within a game keeps the row whose pool_evidence is strongest by the frozen
universe.EVIDENCE_RANK (the same precedence the universe uses within a team). Equal evidence is genuinely ambiguous:
the game fails closed (PLAYER_TEAM_AMBIGUOUS) and is not scored. Every resolution is recorded. Nothing is dropped
silently and no row of a non-duplicated player is touched. Frozen code (stage3, v2, features.model_frame) is unchanged.
"""
from __future__ import annotations

import pandas as pd

from cbb_platform.stage3.universe import EVIDENCE_RANK

GAME, PLAYER, TEAM = "canonical_game_id", "canonical_player_id", "canonical_team_id"
AMBIGUOUS = "PLAYER_TEAM_AMBIGUOUS"
_RANK = {e: i for i, e in enumerate(EVIDENCE_RANK)}


def duplicate_keys(df: pd.DataFrame, keys) -> list[tuple]:
    keys = list(keys)
    if df.empty:
        return []
    d = df.loc[df.duplicated(keys, keep=False), keys].drop_duplicates()
    return [tuple(r) for r in d.itertuples(index=False)]


def assert_canonical_key_is_unique(df: pd.DataFrame, keys, what: str) -> None:
    dup = duplicate_keys(df, keys)
    assert not dup, f"canonical_key_is_unique failed for {what} on {list(keys)}: {len(dup)} duplicate keys {dup[:20]}"


def resolve(cand: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[str]], list[dict]]:
    """(cand at game x player grain, {game_id: [reason]}, conflict records). Ambiguous games keep no rows."""
    if cand.empty or not cand.duplicated([GAME, PLAYER]).any():
        return cand, {}, []
    if cand.duplicated([GAME, TEAM, PLAYER]).any():                    # not the cross-team case: never guessed
        raise AssertionError("canonical_key_is_unique failed for cand on game x team x player: "
                             f"{duplicate_keys(cand, [GAME, TEAM, PLAYER])[:20]}")
    rank = cand["pool_evidence"].map(_RANK)
    if rank.isna().any():
        bad = sorted(cand.loc[rank.isna(), "pool_evidence"].astype(str).unique())
        raise ValueError(f"pool_evidence outside the frozen EVIDENCE_RANK: {bad}")
    dmask = cand.duplicated([GAME, PLAYER], keep=False)
    reasons: dict[str, list[str]] = {}
    records, drop = [], []
    for (gid, pid), g in cand[dmask].assign(_rank=rank[dmask]).groupby([GAME, PLAYER], sort=True):
        best = g["_rank"].min()
        win = g[g["_rank"] == best]
        rec = {"game_id": gid, "player_id": pid,
               "candidates": [{"team_id": r[TEAM], "pool_evidence": r["pool_evidence"]} for _, r in g.iterrows()]}
        if len(win) > 1:
            rec.update(resolution=AMBIGUOUS, kept_team_id=None)
            reasons.setdefault(gid, []).append(AMBIGUOUS)
        else:
            rec.update(resolution="STRONGEST_POOL_EVIDENCE", kept_team_id=win.iloc[0][TEAM])
            drop += list(g.index.difference(win.index))
        records.append(rec)
    out = cand.drop(index=drop)
    if reasons:
        out = out[~out[GAME].isin(list(reasons))]
    assert_canonical_key_is_unique(out, [GAME, PLAYER], "cand after pool_grain.resolve")
    return out, reasons, records
