"""Strict quote -> canonical identity matching. Never fuzzy.

Order (games and players alike):
  1. explicit provider-id / provider-name mapping (curated map files);
  2. canonical mapping;
  3. EXACT normalized identity inside the game / team context (normalization is lossless for identity: case,
     punctuation, whitespace and generational suffixes only);
  4. otherwise the quote is suppressed: UNMATCHED_GAME / AMBIGUOUS_GAME / UNMATCHED_PLAYER / AMBIGUOUS_PLAYER.
Games: both provider teams must resolve to the slate game's two canonical teams with matching home/away orientation
(swapped orientation only at a neutral site) and the provider commence time within COMMENCE_TOLERANCE of the scheduled
tip; anything else is not a match.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

COMMENCE_TOLERANCE = pd.Timedelta(hours=3)
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm(name) -> str:
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`’,]", "", s)
    s = re.sub(r"[-_/]", " ", s)
    s = SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def team_index(teams: pd.DataFrame, team_map: pd.DataFrame | None) -> dict:
    """normalized provider team name -> set(canonical_team_id). Explicit map entries win outright."""
    idx: dict[str, set] = {}
    for r in teams.itertuples(index=False):
        names = {r.display_name, r.school}
        if getattr(r, "mascot", None):
            names.add(f"{r.school} {r.mascot}")
        for n in names:
            if norm(n):
                idx.setdefault(norm(n), set()).add(r.canonical_team_id)
    explicit = {}
    if team_map is not None:
        for r in team_map.itertuples(index=False):
            explicit[norm(r.provider_team_name)] = {r.canonical_team_id}
    return {"explicit": explicit, "exact": idx}


def resolve_team(name: str, tidx: dict) -> tuple[str | None, str]:
    k = norm(name)
    if k in tidx["explicit"]:
        return next(iter(tidx["explicit"][k])), "EXPLICIT_MAP"
    ids = tidx["exact"].get(k, set())
    if len(ids) == 1:
        return next(iter(ids)), "EXACT_NORMALIZED"
    return None, "AMBIGUOUS" if len(ids) > 1 else "UNMATCHED"


def match_games(quotes: pd.DataFrame, slate: pd.DataFrame, tidx: dict) -> pd.DataFrame:
    q = quotes.copy()
    ev = q.drop_duplicates("provider_event_id")[["provider_event_id", "provider_home_team", "provider_away_team",
                                                 "provider_commence_time"]]
    res = {}
    for e in ev.itertuples(index=False):
        h, hm = resolve_team(e.provider_home_team, tidx)
        a, am = resolve_team(e.provider_away_team, tidx)
        if h is None or a is None:
            res[e.provider_event_id] = (None, "AMBIGUOUS_GAME" if "AMBIGUOUS" in (hm, am) else "UNMATCHED_GAME")
            continue
        near = slate[(slate["start_time"] - e.provider_commence_time).abs() <= COMMENCE_TOLERANCE]
        direct = near[(near["home_team_id"] == h) & (near["away_team_id"] == a)]
        swapped = near[(near["home_team_id"] == a) & (near["away_team_id"] == h) & near["neutral_site"]]
        cand = pd.concat([direct, swapped]).drop_duplicates("canonical_game_id")
        if len(cand) == 1:
            res[e.provider_event_id] = (cand["canonical_game_id"].iat[0], "")
        else:
            res[e.provider_event_id] = (None, "AMBIGUOUS_GAME" if len(cand) > 1 else "UNMATCHED_GAME")
    q["canonical_game_id"] = pd.Series([res[x][0] for x in q["provider_event_id"]], index=q.index, dtype=object)
    bad = q["provider_event_id"].map(lambda x: res[x][1])
    sel = (q["quote_status"] == "VALID") & (bad != "")
    q.loc[sel, "quote_status"] = "SUPPRESSED"
    q.loc[sel, "suppression_reason"] = bad[sel]
    return q


def match_players(quotes: pd.DataFrame, candidates: pd.DataFrame, player_map: pd.DataFrame | None) -> pd.DataFrame:
    """candidates: game_id, team_id, player_id, display_name (the game's pregame universe)."""
    q = quotes.copy()
    explicit: dict[tuple, set] = {}                    # (normalized provider name, canonical team) -> player ids
    if player_map is not None:
        for r in player_map.itertuples(index=False):
            explicit.setdefault((norm(r.provider_player_name), r.canonical_team_id), set()).add(r.canonical_player_id)
    by_game = {g: d for g, d in candidates.assign(_n=candidates["display_name"].map(norm)).groupby("game_id")}
    out_id, out_reason = [], []
    for r in q.itertuples(index=False):
        if r.quote_status != "VALID" or not isinstance(r.canonical_game_id, str):
            out_id.append(None), out_reason.append(None)
            continue
        c = by_game.get(r.canonical_game_id)
        k = norm(r.provider_player_name)
        mapped = set().union(*[explicit.get((k, t), set()) for t in c["team_id"].unique()]) if c is not None else set()
        if len(mapped) > 1:
            out_id.append(None), out_reason.append("AMBIGUOUS_PLAYER")
            continue
        pid = next(iter(mapped)) if mapped else None
        if pid is not None:
            ok = c is not None and (c["player_id"] == pid).any()
            out_id.append(pid if ok else None), out_reason.append("" if ok else "UNMATCHED_PLAYER")
            continue
        hits = c[c["_n"] == k]["player_id"].unique() if (c is not None and k) else []
        if len(hits) == 1:
            out_id.append(hits[0]), out_reason.append("")
        else:
            out_id.append(None), out_reason.append("AMBIGUOUS_PLAYER" if len(hits) > 1 else "UNMATCHED_PLAYER")
    q["canonical_player_id"] = pd.Series(out_id, index=q.index, dtype=object)
    reason = pd.Series(out_reason, index=q.index)
    sel = reason.notna() & (reason != "")
    q.loc[sel, "quote_status"] = "SUPPRESSED"
    q.loc[sel, "suppression_reason"] = reason[sel]
    return q
