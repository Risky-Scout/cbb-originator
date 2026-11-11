"""PRODUCTION-ONLY identity-conflict routing: IDENTITY_CONFLICT_SUPPRESSED (directive 2026-11-11, Khoi Thurmon).

One real player carries two canonical identities that can NOT be merged safely (a merge would overwrite an existing
frozen-history identity -- prohibited; identity P-3 refuses it). Without routing, both identities enter the same game's
modeling state and the player is simulated / priced twice. A registry entry (identity_conflicts.json, curated, every
field required) names the PRIMARY modeling identity (deterministic continuity evidence) and the SUPPRESSED duplicate:

    identity_conflict = TRUE, canonical_merge_performed = FALSE, historical_records_preserved = TRUE
    the suppressed id never enters the model frame: zero model-input rows, so zero simulation draws, zero PMFs, zero
    pricing rows, zero quote candidates (the features database itself is the frozen Stage 3 build, unchanged)
    its availability report (RotoWire resolves the CURRENT-roster id) is routed to the primary for that game, unless
    the primary has his own report
    blocks_game = NO while one valid primary identity remains

Nothing is deleted, merged, re-keyed or written: the frozen history, the season-state increment and every canonical row
stay as they are, so a later deterministic, conflict-safe merge stays possible (the entry then becomes a no-op: the
suppressed id no longer appears).

Every run re-verifies the entry's deterministic evidence on that run's canonical input (verify). If it no longer holds,
the duplicate stays excluded AND the primary is simulated but NOT_PRICED (IDENTITY_CONFLICT_UNVERIFIED): fail closed for
this player only. guard() is the last check before pricing: if both identities still reach one game's distributions,
both are NOT_PRICED (IDENTITY_CONFLICT_DOUBLE_COUNT).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform.prod import config as PC

STATUS = "IDENTITY_CONFLICT_SUPPRESSED"
UNVERIFIED = "IDENTITY_CONFLICT_UNVERIFIED"
DOUBLE_COUNT = "IDENTITY_CONFLICT_DOUBLE_COUNT"
REQUIRED = ("conflict_id", "status", "player_name", "first_name", "last_name", "team_id",
            "primary_canonical_player_id", "suppressed_canonical_player_id", "source_athlete_ids",
            "portal_person_source_id", "portal_records", "attribute_match", "primary_selection",
            "merge_not_performed_reason", "suppression_reason", "recorded_at", "canonical_merge_performed",
            "historical_records_preserved")
CHECKS = ("primary_history", "portal_move", "destination_roster", "exact_name_height_hometown", "no_conflicting_assignment")


def load(path: Path | None = None) -> list[dict]:
    p = Path(path or PC.IDENTITY_CONFLICTS)
    if not p.exists():
        return []
    ent = json.loads(p.read_text())["conflicts"]
    seen = set()
    for e in ent:
        miss = [k for k in REQUIRED if e.get(k) in (None, "", [], {})]
        if miss:
            raise ValueError(f"identity conflicts: {e.get('conflict_id')} missing {miss}")
        if e["status"] != STATUS or e["canonical_merge_performed"] is not False or e["historical_records_preserved"] is not True:
            raise ValueError(f"identity conflicts: {e['conflict_id']} must be {STATUS}, no merge, history preserved")
        a, b = e["primary_canonical_player_id"], e["suppressed_canonical_player_id"]
        if a == b or {a, b} & seen:
            raise ValueError(f"identity conflicts: {e['conflict_id']} ids must be distinct and in one entry only")
        seen |= {a, b}
    return ent


def _q(con, sql, params=()):
    return con.execute(sql, list(params)).df()


def verify(canonical_db: Path, e: dict) -> dict:
    """The entry's deterministic evidence on this run's canonical input (read only). {check: bool} + verified."""
    season, cls = PC.PRODUCTION_SEASON, PC.PRESEASON_CLASS_YEAR
    a, b, team = e["primary_canonical_player_id"], e["suppressed_canonical_player_id"], e["team_id"]
    fn, ln, name = e["first_name"], e["last_name"], e["player_name"]
    out = {c: False for c in CHECKS}
    try:
        con = duckdb.connect(str(canonical_db), read_only=True)
    except Exception as x:                                       # noqa: BLE001 -- unreadable evidence verifies nothing
        return {**out, "verified": False, "error": f"{type(x).__name__}: {x}"}
    try:
        cols = "canonical_player_id, canonical_team_id, season, player_name, first_name, last_name, height_inches, " \
               "hometown_city, hometown_state"
        hist = _q(con, f"SELECT {cols} FROM flagship.rosters WHERE canonical_player_id = ? AND season < ? "
                       "ORDER BY season DESC LIMIT 1", (a, season))
        cur = _q(con, f"SELECT {cols} FROM flagship.rosters WHERE season = ?", (season,))
        tr = _q(con, "SELECT * FROM core.transfers WHERE transfer_year = ? AND destination_team_id = ? "
                     "AND first_name = ? AND last_name = ?", (cls, team, fn, ln))
    except Exception as x:                                       # noqa: BLE001 -- a missing column verifies nothing
        con.close()
        return {**out, "verified": False, "error": f"{type(x).__name__}: {x}"}
    con.close()
    out["primary_history"] = len(hist) == 1
    if out["primary_history"]:
        h = hist.iloc[0]
        tr = tr[(tr.get("candidate_canonical_player_id", pd.Series(index=tr.index, dtype=object)).astype(object) == a)
                | (tr.get("canonical_player_id", pd.Series(index=tr.index, dtype=object)).astype(object) == a)]
        out["portal_move"] = (len(tr) == 1 and tr.iloc[0]["origin_team_id"] == h.canonical_team_id
                              and str(tr.iloc[0].get("source_transfer_source_id")) == str(e["portal_person_source_id"]))
        dest = cur[(cur["canonical_team_id"] == team) & (cur["player_name"] == name)]
        out["destination_roster"] = len(dest) == 1 and dest.iloc[0]["canonical_player_id"] == b
        if out["destination_roster"]:
            d = dest.iloc[0]
            out["exact_name_height_hometown"] = bool(
                d.first_name == h.first_name == fn and d.last_name == h.last_name == ln and h.player_name == name
                and all(pd.notna(x) for x in (h.height_inches, h.hometown_city, h.hometown_state))
                and d.height_inches == h.height_inches and d.hometown_city == h.hometown_city
                and d.hometown_state == h.hometown_state)
        out["no_conflicting_assignment"] = (not (cur["canonical_player_id"] == a).any()
                                            and set(cur.loc[cur["canonical_player_id"] == b, "canonical_team_id"]) <= {team})
    out["verified"] = all(out[c] for c in CHECKS)
    return out


def apply(cand: pd.DataFrame, availability: pd.DataFrame | None, canonical_db: Path, entries: list[dict],
          as_of=None) -> tuple[pd.DataFrame, pd.DataFrame | None, dict, dict]:
    """cand: the run's pregame candidates (prod/features.model_frame). Returns (cand without any suppressed id,
    availability routed to the primary, {(game_id, player_id): reason} for primaries to price NOT_PRICED, audit)."""
    block, audit = {}, []
    if not entries or cand is None or cand.empty:
        return cand, availability, block, {"entries": len(entries or []), "conflicts": []}
    keep = pd.Series(True, index=cand.index)
    av = availability.copy() if availability is not None and len(availability) else availability
    for e in entries:
        a, b = e["primary_canonical_player_id"], e["suppressed_canonical_player_id"]
        hit = cand["canonical_player_id"].isin([a, b])
        if not hit.any():                                        # not in this run: nothing recorded, output unchanged
            continue
        chk = verify(canonical_db, e)
        games = []
        for gid, g in cand[hit].groupby("canonical_game_id", sort=True):
            ids = set(g["canonical_player_id"])
            routed = False
            if av is not None and len(av):
                own = (av["game_id"] == gid) & (av["player_id"] == a)
                dup = (av["game_id"] == gid) & (av["player_id"] == b)
                if dup.any() and not own.any():
                    av.loc[dup, "player_id"] = a
                    routed = True
                elif dup.any():
                    av = av[~dup]
            if a in ids and not chk["verified"]:
                block[(gid, a)] = UNVERIFIED
            games.append({"game_id": gid, "team_ids": sorted(set(g["canonical_team_id"])),
                          "primary_in_pool": a in ids, "suppressed_in_pool": b in ids,
                          "suppressed_rows_excluded": int((g["canonical_player_id"] == b).sum()),
                          "availability_routed_to_primary": routed,
                          "primary_priced": a in ids and chk["verified"],
                          "blocks_game": False})
        keep &= cand["canonical_player_id"] != b
        audit.append({"conflict_id": e["conflict_id"], "present": True, "status": STATUS, "identity_conflict": True,
                      "canonical_merge_performed": False, "historical_records_preserved": True,
                      "primary_canonical_player_id": a, "suppressed_canonical_player_id": b,
                      "include_suppressed_in_features": False, "include_suppressed_in_simulation": False,
                      "include_suppressed_in_pmfs": False, "include_suppressed_in_pricing": False,
                      "verification": chk, "games": games,
                      **{k: e[k] for k in ("player_name", "team_id", "source_athlete_ids", "portal_person_source_id",
                                           "portal_records", "attribute_match", "primary_selection",
                                           "merge_not_performed_reason", "suppression_reason", "recorded_at")}})
    rep = {"entries": len(entries), "as_of": str(as_of) if as_of is not None else None,
           "release_commit": os.environ.get("CBB_EXPECTED_COMMIT"), "conflicts": audit}
    return cand[keep].reset_index(drop=True), av, block, rep


def mark_universe(universe: pd.DataFrame, entries: list[dict]) -> pd.DataFrame:
    """The suppressed id's universe rows: SUPPRESSED / IDENTITY_CONFLICT_SUPPRESSED (recorded, never modeled)."""
    if not entries or universe.empty:
        return universe
    sup = {e["suppressed_canonical_player_id"] for e in entries}
    u = universe.copy()
    m = u["canonical_player_id"].isin(sup)
    u.loc[m, "suppression_reason"] = STATUS
    u.loc[m, "scoreability"] = "SUPPRESSED"
    return u


def guard(dist: pd.DataFrame, entries: list[dict]) -> tuple[pd.DataFrame, list]:
    """Last check before pricing: both identities of one conflict priced in one game -> both NOT_PRICED."""
    hits = []
    if not entries or dist is None or dist.empty:
        return dist, hits
    dist = dist.copy()
    for e in entries:
        ids = [e["primary_canonical_player_id"], e["suppressed_canonical_player_id"]]
        live = dist[dist["player_id"].isin(ids) & (dist["method"] != "NOT_PRICED")]
        for gid, g in live.groupby("game_id"):
            if g["player_id"].nunique() > 1:
                m = (dist["game_id"] == gid) & dist["player_id"].isin(ids)
                dist.loc[m, "method"] = "NOT_PRICED"
                dist.loc[m, "player_suppression"] = DOUBLE_COUNT
                hits.append({"conflict_id": e["conflict_id"], "game_id": gid})
    return dist, hits


def counts(df: pd.DataFrame | None, entries: list[dict], pcol: str = "player_id") -> dict:
    """{conflict_id: {player_id: rows}} of one layer (model inputs, distributions, board) for the run summary."""
    out = {}
    for e in entries:
        ids = [e["primary_canonical_player_id"], e["suppressed_canonical_player_id"]]
        n = df[pcol].value_counts() if df is not None and len(df) and pcol in df else pd.Series(dtype=int)
        out[e["conflict_id"]] = {i: int(n.get(i, 0)) for i in ids}
    return out
