"""PRODUCTION-ONLY operational classification of RotoWire players who are NOT on the canonical active roster
(availability / reconciliation METADATA ONLY -- never a model feature, never an identity, never a roster row).

The canonical roster (CBBD teams/roster) is the active-roster source and is never edited here. A RotoWire injury row for
a player who is not on it is IDENTITY_UNRESOLVED and fails his team's games closed (rotowire_feed.availability_frame:
AVAILABILITY_IDENTITY_UNRESOLVED) -- unless a human verified, with written evidence, that the player is a KNOWN
non-active case. Known inactive or stale identity != unresolved identity:

    ACTIVE_CURRENT                    derived, not curated: resolved through the persisted map onto the canonical roster
    AFFILIATED_INACTIVE_ELIGIBILITY   with the program (enrolled / practicing / litigating / awaiting NCAA) but ineligible
    AFFILIATED_ROSTER_LIMIT           with the program but holding no active roster slot
    DELAYED_ELIGIBILITY               with the program; eligible from a documented date (eligible_from)
    STALE_NONCURRENT                  RotoWire still lists him at this team, but he is not with it in 2026-27
    SOURCE_CONFLICT                   credible sources disagree -> still fails closed (recorded, never exempted)
    IDENTITY_UNRESOLVED               derived, not curated: everything else -> fails closed

The four NON_BLOCKING classes do not block the whole game; the player is never put into the availability frame (he is
not in the active universe, so no projection is conditioned on him). A row applies only:
  * to the exact RotoWire provider ID, while RotoWire places him at the row's canonical team (team re-resolved per event);
  * from verified_on (point in time: earlier game states are untouched) up to -- not including -- review_by;
  * for DELAYED_ELIGIBILITY, also only before eligible_from (after it: unresolved again until CBBD rosters him).
After review_by the row lapses and the player fails closed again until a human re-verifies. Never inferred at run time.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC

ACTIVE_CURRENT = "ACTIVE_CURRENT"
AFFILIATED_INACTIVE_ELIGIBILITY = "AFFILIATED_INACTIVE_ELIGIBILITY"
AFFILIATED_ROSTER_LIMIT = "AFFILIATED_ROSTER_LIMIT"
DELAYED_ELIGIBILITY = "DELAYED_ELIGIBILITY"
STALE_NONCURRENT = "STALE_NONCURRENT"
SOURCE_CONFLICT = "SOURCE_CONFLICT"
IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
NON_BLOCKING = (AFFILIATED_INACTIVE_ELIGIBILITY, AFFILIATED_ROSTER_LIMIT, DELAYED_ELIGIBILITY, STALE_NONCURRENT)
CURATED = NON_BLOCKING + (SOURCE_CONFLICT,)
COLS = ["rotowire_player_id", "rotowire_player_name", "rotowire_team_name", "affiliated_team_id", "canonical_player_id",
        "operational_class", "eligible_from", "verified_on", "review_by", "evidence"]
REQUIRED = ["rotowire_player_id", "rotowire_player_name", "rotowire_team_name", "affiliated_team_id", "operational_class",
            "verified_on", "review_by", "evidence"]


def load(path: Path | None = None) -> pd.DataFrame:
    p = Path(path or PC.ROTOWIRE_PLAYER_STATES)
    m = pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else pd.DataFrame(columns=COLS)
    if list(m.columns) != COLS:
        raise ValueError(f"rotowire player states: columns must be {COLS}")
    if len(m) and (m[REQUIRED] == "").any().any():
        raise ValueError(f"rotowire player states: {REQUIRED} are required on every row")
    bad = sorted(set(m["operational_class"]) - set(CURATED))
    if bad:
        raise ValueError(f"rotowire player states: operational_class {bad} not in {CURATED}")
    dup = m[m["rotowire_player_id"].duplicated(keep=False)]
    if len(dup):
        raise ValueError(f"rotowire player states: duplicate rotowire_player_id {sorted(set(dup['rotowire_player_id']))}")
    for c in ("verified_on", "review_by"):
        pd.to_datetime(m[c], format="%Y-%m-%d")                          # raises on a malformed date
    d = m[m["operational_class"] == DELAYED_ELIGIBILITY]
    if (d["eligible_from"] == "").any():
        raise ValueError("rotowire player states: DELAYED_ELIGIBILITY needs eligible_from")
    pd.to_datetime(d["eligible_from"], format="%Y-%m-%d")
    if (pd.to_datetime(m["review_by"]) <= pd.to_datetime(m["verified_on"])).any():
        raise ValueError("rotowire player states: review_by must be after verified_on")
    return m


def _day(t) -> date:
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return ts.tz_convert("America/New_York").date()


def in_force(states: pd.DataFrame, as_of) -> pd.DataFrame:
    """The rows that apply at as_of (ET date): verified_on <= day < review_by, and day < eligible_from when set."""
    if states is None or not len(states):
        return pd.DataFrame(columns=COLS)
    day = pd.Timestamp(_day(as_of))
    s = states
    ok = (pd.to_datetime(s["verified_on"]) <= day) & (day < pd.to_datetime(s["review_by"]))
    el = pd.to_datetime(s["eligible_from"].replace("", None))
    ok &= el.isna() | (day < el)
    return s[ok]


def classify(states: pd.DataFrame, rotowire_player_id, canonical_team_id, as_of) -> str | None:
    """The curated class in force for this provider ID at this team, else None (-> unresolved, fails closed)."""
    f = in_force(states, as_of)
    hit = f[(f["rotowire_player_id"] == str(rotowire_player_id)) & (f["affiliated_team_id"] == str(canonical_team_id))]
    return hit["operational_class"].iat[0] if len(hit) else None


def non_blocking(states: pd.DataFrame, rotowire_player_id, canonical_team_id, as_of) -> bool:
    return classify(states, rotowire_player_id, canonical_team_id, as_of) in NON_BLOCKING
