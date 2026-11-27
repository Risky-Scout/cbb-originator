"""PRODUCTION-ONLY verified CBBD roster omissions (CBBD_ROSTER_OMISSION: diagnostic + identity metadata, NOT an
availability state, never a model feature, never a CBBD row).

Source precedence for CURRENT player / team membership (2026-11-09 directive):
    1 current official school roster / official game notes   2 actual current-season game participation
    3 current reputable transaction / eligibility reporting   4 CBBD / canonical roster   5 RotoWire roster assignment
A missing CBBD 2026-27 roster row is not, by itself, a reason to reject a player that a rank-1 or rank-2 source places
on the team. This table records exactly those cases. CBBD responses, the raw cache and flagship.rosters are never
edited: a row only SUPPLEMENTS the roster that RotoWire identity resolution and the pregame universe read, and it stops
mattering the moment CBBD lists the player (MOOT_CBBD_LISTS_PLAYER).

A row is APPLIED only when every override-safety condition holds (else REJECTED, reason recorded, fails closed):
  * evidence_source is rank 1 or 2 (reporting alone is not enough), verified_on <= ET day < review_by;
  * identity: an EXISTING canonical id known to the frozen history / production state (never created here), or -- for
    a freshman with no canonical id -- the CBBD recruit record (source_recruit_id) as the identity anchor;
  * the player is on no other team's CBBD 2026-27 roster (no competing assignment);
  * no other player of that team's CBBD roster carries the same normalized name (no competing candidate);
  * rotowire_player_id and canonical_player_id are each unique in the table, and the persisted RotoWire map holds
    neither for anyone else (an existing mapping is never overwritten).
Applied rows with a canonical id join the identity roster (so the persisted exact-name RotoWire map and the universe
see a current-roster player); recruit-anchored rows carry no model identity (like any unlinked 2026 recruit) and are
only exempt from whole-game identity blocking.

BOX_PARTICIPATION (2026-11-27, Stage 04): precedence rank 2 is ALSO derived, deterministically, from the production
state's own 2026-27 box scores -- the curated rows above had been the only route, and they lapse. A canonical player
whose RESOLVED box lines are knowable at as_of (historical_knowable_at, canonical rule HR-1 -- the instant the frozen
pool uses for PRIOR_APPEARANCE_CONFIRMED) for exactly ONE team this season is a current-roster row of that team (roster_source
BOX_PARTICIPATION) when every override-safety condition holds, else REJECTED / WITHHELD (reason recorded):
  * no knowable box line for any other team this season (a mid-season move with lines at two teams is not guessed);
  * not on any CBBD 2026-27 roster of another team; no other canonical id with his normalized name on the team's
    CBBD roster (no competing candidate); not a suppressed IDENTITY_CONFLICT id;
  * no curated non-current row (STALE_NONCURRENT / PROFESSIONAL_NONCURRENT) in force for him at that team -- newer
    human-verified evidence that he left outranks his earlier participation (prod/rotowire_player_states).
A player an APPLIED curated omission row already lists is MOOT (pass the roster with those rows included).
Nothing is written anywhere; box lines after as_of are never read (no future roster information).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, matching as MT

CBBD_ROSTER_OMISSION = "CBBD_ROSTER_OMISSION"
BOX_PARTICIPATION = "BOX_PARTICIPATION"
BOX_COLS = ["canonical_game_id", "canonical_player_id", "canonical_team_id", "identity_status", "source_player_name",
            "source_position", "event_time", "historical_knowable_at"]   # historical_knowable_at: canonical HR-1 instant
NONCURRENT_CLASSES = ("STALE_NONCURRENT", "PROFESSIONAL_NONCURRENT")
SOURCES = ("OFFICIAL_SCHOOL_ROSTER", "CURRENT_SEASON_GAME_PARTICIPATION")   # precedence ranks 1 and 2 only
COLS = ["rotowire_player_id", "rotowire_player_name", "rotowire_team_name", "canonical_team_id", "canonical_player_id",
        "source_recruit_id", "player_name", "first_name", "last_name", "position", "evidence_source", "evidence_url",
        "verified_on", "review_by", "evidence"]
REQUIRED = ["rotowire_player_id", "rotowire_player_name", "rotowire_team_name", "canonical_team_id", "player_name",
            "first_name", "last_name", "evidence_source", "evidence_url", "verified_on", "review_by", "evidence"]
ROSTER_COLS = ["canonical_player_id", "canonical_team_id", "first_name", "last_name", "player_name", "position"]


def load(path: Path | None = None) -> pd.DataFrame:
    p = Path(path or PC.ROSTER_OMISSIONS)
    m = pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else pd.DataFrame(columns=COLS)
    if list(m.columns) != COLS:
        raise ValueError(f"roster omissions: columns must be {COLS}")
    if len(m) and (m[REQUIRED] == "").any().any():
        raise ValueError(f"roster omissions: {REQUIRED} are required on every row")
    if not m["evidence_source"].isin(SOURCES).all():
        raise ValueError(f"roster omissions: evidence_source must be one of {SOURCES} (precedence rank 1 or 2)")
    if ((m["canonical_player_id"] == "") == (m["source_recruit_id"] == "")).any():
        raise ValueError("roster omissions: exactly one identity anchor (canonical_player_id XOR source_recruit_id)")
    for c in ("rotowire_player_id", "canonical_player_id", "source_recruit_id"):
        v = m.loc[m[c] != "", c]
        if v.duplicated().any():
            raise ValueError(f"roster omissions: duplicate {c} {sorted(set(v[v.duplicated()]))}")
    for c in ("verified_on", "review_by"):
        pd.to_datetime(m[c], format="%Y-%m-%d")
    if (pd.to_datetime(m["review_by"]) <= pd.to_datetime(m["verified_on"])).any():
        raise ValueError("roster omissions: review_by must be after verified_on")
    return m


def _day(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return pd.Timestamp(ts.tz_convert("America/New_York").date())


def in_force(m: pd.DataFrame, as_of) -> pd.DataFrame:
    if m is None or not len(m):
        return pd.DataFrame(columns=COLS)
    d = _day(as_of)
    return m[(pd.to_datetime(m["verified_on"]) <= d) & (d < pd.to_datetime(m["review_by"]))]


def effective(m: pd.DataFrame, roster: pd.DataFrame, as_of, player_map: pd.DataFrame | None = None,
              known_ids: set | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """(applied rows, decisions) at as_of against the CBBD 2026-27 roster (canonical_player_id, canonical_team_id,
    player_name[, first_name, last_name]). player_map: the persisted RotoWire map (None -> not checked here);
    known_ids: canonical ids of the frozen history + production state (None -> not checked here)."""
    f = in_force(m, as_of)
    r = roster.copy()
    full = (r.get("first_name", pd.Series("", index=r.index)).fillna("") + " "
            + r.get("last_name", pd.Series("", index=r.index)).fillna("")).str.strip()
    r["_n"] = [MT.norm(a) or MT.norm(b) for a, b in zip(full, r["player_name"])]
    pm = player_map if player_map is not None else pd.DataFrame(columns=["rotowire_player_id", "canonical_player_id"])
    by_rw = pm.groupby(pm["rotowire_player_id"].astype(str))["canonical_player_id"].agg(set).to_dict() if len(pm) else {}
    by_c = pm.groupby("canonical_player_id")["rotowire_player_id"].agg(lambda s: set(map(str, s))).to_dict() if len(pm) else {}
    keep, out = [], []
    for row in f.to_dict("records"):
        pid, tid, rw = row["canonical_player_id"], row["canonical_team_id"], row["rotowire_player_id"]
        on = r[r["canonical_player_id"] == pid] if pid else r.iloc[0:0]
        same = r[(r["canonical_team_id"] == tid) & (r["_n"] == MT.norm(row["player_name"]))
                 & ((r["canonical_player_id"] != pid) if pid else True)]
        if pid and (on["canonical_team_id"] == tid).any():
            why = "MOOT_CBBD_LISTS_PLAYER"
        elif pid and len(on):
            why = "REJECTED_ON_OTHER_TEAM_CBBD_ROSTER"
        elif len(same):
            why = "REJECTED_COMPETING_CANONICAL_CANDIDATE"
        elif pid and known_ids is not None and pid not in known_ids:
            why = "REJECTED_UNKNOWN_CANONICAL_ID"
        elif by_rw.get(rw, set()) - {pid}:
            why = "REJECTED_WOULD_OVERWRITE_ROTOWIRE_MAPPING"
        elif pid and by_c.get(pid, set()) - {rw}:
            why = "REJECTED_CANONICAL_HELD_BY_OTHER_ROTOWIRE_ID"
        else:
            why = "APPLIED"
            keep.append(row)
        out.append({"rotowire_player_id": rw, "canonical_player_id": pid or None, "canonical_team_id": tid,
                    "source_recruit_id": row["source_recruit_id"] or None, "decision": why})
    return pd.DataFrame(keep, columns=COLS), out


def roster_rows(applied: pd.DataFrame) -> pd.DataFrame:
    """Applied rows WITH a canonical id, in the identity-roster shape (never written anywhere)."""
    a = applied[applied["canonical_player_id"] != ""]
    return a[ROSTER_COLS].replace({"position": {"": None}}).reset_index(drop=True)


def recruit_anchored(applied: pd.DataFrame, rotowire_player_id, canonical_team_id) -> bool:
    """A recruit-anchored applied row for this RotoWire ID at this team (identity deterministic, no model identity)."""
    a = applied[(applied["canonical_player_id"] == "") & (applied["rotowire_player_id"] == str(rotowire_player_id))
                & (applied["canonical_team_id"] == str(canonical_team_id))]
    return bool(len(a))


def _utc(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def participation_rows(box: pd.DataFrame, roster: pd.DataFrame, as_of, states: pd.DataFrame | None = None,
                       suppressed_ids: set | None = None
                       ) -> tuple[pd.DataFrame, list[dict]]:
    """(identity-roster rows, decisions): BOX_PARTICIPATION current-roster rows at as_of (see the module docstring).
    box: 2026-27 player box lines (BOX_COLS); roster: the CBBD 2026-27 roster (canonical_player_id, canonical_team_id,
    player_name[, first_name, last_name]); states: the curated player states (None -> not checked);
    suppressed_ids: IDENTITY_CONFLICT suppressed ids."""
    out_cols = ROSTER_COLS + ["roster_source", "box_games", "box_last_knowable_at"]
    if box is None or not len(box):
        return pd.DataFrame(columns=out_cols), []
    T = _utc(as_of)
    b = box[(box["identity_status"] == "RESOLVED") & box["canonical_player_id"].notna()].copy()
    b["_k"] = pd.to_datetime(b["historical_knowable_at"], utc=True)
    b = b[b["_k"] <= T]
    if not len(b):
        return pd.DataFrame(columns=out_cols), []
    r = roster.copy()
    full = (r.get("first_name", pd.Series("", index=r.index)).fillna("") + " "
            + r.get("last_name", pd.Series("", index=r.index)).fillna("")).str.strip()
    r["_n"] = [MT.norm(a) or MT.norm(c) for a, c in zip(full, r["player_name"])]
    on = set(zip(r["canonical_player_id"], r["canonical_team_id"]))
    by_pid = r.groupby("canonical_player_id")["canonical_team_id"].agg(set).to_dict() if len(r) else {}
    names = r.groupby(["canonical_team_id", "_n"])["canonical_player_id"].agg(set).to_dict() if len(r) else {}
    from cbb_platform.prod import rotowire_player_states as PS
    nonc = set()
    if states is not None and len(states):
        f = PS.in_force(states, T)
        f = f[f["operational_class"].isin(NONCURRENT_CLASSES) & (f["canonical_player_id"] != "")]
        nonc = set(zip(f["canonical_player_id"], f["affiliated_team_id"]))
    sup = set(suppressed_ids or ())
    keep, dec = [], []
    for pid, g in b.sort_values(["_k", "event_time"], kind="mergesort").groupby("canonical_player_id", sort=True):
        teams = set(g["canonical_team_id"])
        last = g.iloc[-1]
        tid = last["canonical_team_id"]
        nm = last["source_player_name"] if isinstance(last["source_player_name"], str) else ""
        d = {"canonical_player_id": pid, "canonical_team_id": tid, "player_name": nm, "box_games": int(g["canonical_game_id"].nunique())
             if "canonical_game_id" in g else int(len(g)), "box_last_knowable_at": g["_k"].max().isoformat()}
        if len(teams) > 1:
            why = "REJECTED_MULTI_TEAM_PARTICIPATION"
        elif (pid, tid) in on:
            why = "MOOT_CBBD_LISTS_PLAYER"
        elif by_pid.get(pid):
            why = "REJECTED_ON_OTHER_TEAM_CBBD_ROSTER"
        elif names.get((tid, MT.norm(nm)), set()) - {pid}:
            why = "REJECTED_COMPETING_CANONICAL_CANDIDATE"
        elif pid in sup:
            why = "REJECTED_IDENTITY_CONFLICT_SUPPRESSED"
        elif (pid, tid) in nonc:
            why = "WITHHELD_CURATED_NONCURRENT"
        elif not nm:
            why = "REJECTED_NO_BOX_NAME"
        else:
            why = "APPLIED"
            first, _, lastn = nm.partition(" ")
            pos = last.get("source_position") if isinstance(last.get("source_position"), str) else None
            keep.append({"canonical_player_id": pid, "canonical_team_id": tid, "first_name": first or None,
                         "last_name": lastn or None, "player_name": nm, "position": pos or None,
                         "roster_source": BOX_PARTICIPATION, "box_games": d["box_games"],
                         "box_last_knowable_at": d["box_last_knowable_at"]})
        dec.append({**d, "decision": why})
    return pd.DataFrame(keep, columns=out_cols), dec


def participation_rows_committed(box: pd.DataFrame, roster: pd.DataFrame, as_of) -> pd.DataFrame:
    """participation_rows with the COMMITTED curated tables (player states: withhold-only; identity-conflict registry).
    A curated row can only WITHHOLD a derived row (keep a player suppressed), never add one."""
    from cbb_platform.prod import identity_conflicts as IC, rotowire_player_states as PS
    rows, _ = participation_rows(box, roster, as_of, PS.load(), {e["suppressed_canonical_player_id"] for e in IC.load()})
    return rows
