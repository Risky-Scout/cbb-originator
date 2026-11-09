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
    ROSTERED_LONG_TERM_OUT            on the team's current official roster, definitively out long term (season-ending)
    STALE_NONCURRENT                  RotoWire still lists him at this team, but he is not with it in 2026-27
    PROFESSIONAL_NONCURRENT           no longer an NCAA player: professional career, newer than any college listing
    SOURCE_CONFLICT                   credible sources disagree about a material identity / team / eligibility fact.
                                      Recorded, never resolved here. Blocks UNLESS current-game participation is
                                      deterministically impossible (see conflict_blocks): current_game_available FALSE
                                      blocks only while the live evidence keeps proving it; UNKNOWN / POSSIBLY_ACTIVE block
    IDENTITY_UNRESOLVED               derived, not curated: everything else -> fails closed

The NON_BLOCKING classes do not block the whole game; the player is never put into the availability frame (he is
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
ROSTERED_LONG_TERM_OUT = "ROSTERED_LONG_TERM_OUT"
PROFESSIONAL_NONCURRENT = "PROFESSIONAL_NONCURRENT"
SOURCE_CONFLICT = "SOURCE_CONFLICT"
IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
NON_BLOCKING = (AFFILIATED_INACTIVE_ELIGIBILITY, AFFILIATED_ROSTER_LIMIT, DELAYED_ELIGIBILITY, STALE_NONCURRENT,
                ROSTERED_LONG_TERM_OUT, PROFESSIONAL_NONCURRENT)
CURATED = NON_BLOCKING + (SOURCE_CONFLICT,)
COLS = ["rotowire_player_id", "rotowire_player_name", "rotowire_team_name", "affiliated_team_id", "canonical_player_id",
        "operational_class", "eligible_from", "verified_on", "review_by", "current_game_available",
        "evidence_checked_at", "evidence"]
CONFLICT_AVAILABILITY = ("FALSE", "UNKNOWN", "POSSIBLY_ACTIVE")   # SOURCE_CONFLICT rows only
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
    c = m["operational_class"] == SOURCE_CONFLICT
    if not m.loc[c, "current_game_available"].isin(CONFLICT_AVAILABILITY).all():
        raise ValueError(f"rotowire player states: SOURCE_CONFLICT needs current_game_available in {CONFLICT_AVAILABILITY}")
    if (m.loc[c, "evidence_checked_at"] == "").any():
        raise ValueError("rotowire player states: SOURCE_CONFLICT needs evidence_checked_at (UTC instant)")
    pd.to_datetime(m.loc[c, "evidence_checked_at"], utc=True, format="ISO8601")
    if (m.loc[~c, ["current_game_available", "evidence_checked_at"]] != "").any().any():
        raise ValueError("rotowire player states: current_game_available / evidence_checked_at are SOURCE_CONFLICT-only")
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


def conflicts(states: pd.DataFrame, teams, as_of) -> pd.DataFrame:
    """SOURCE_CONFLICT rows in force at as_of for these canonical teams."""
    f = in_force(states, as_of)
    return f[(f["operational_class"] == SOURCE_CONFLICT) & f["affiliated_team_id"].isin(set(teams))]


def conflict_blocks(row: dict, state: dict | None, last_news_at, participated: bool | None) -> tuple[bool, str]:
    """(blocks, why) for one SOURCE_CONFLICT row at one game instant. The conflict itself is never resolved here; the
    game is blocked unless current-game participation is DETERMINISTICALLY impossible on the LIVE evidence:
      * the row says current_game_available FALSE (UNKNOWN / POSSIBLY_ACTIVE always block);
      * RotoWire has a current status for him, identity still unresolved, effective status OUT-like, no gate
        (ACTIVE / NOT_LISTED / GTD / QUESTIONABLE / UNKNOWN / conflicting news vs snapshot -> possibly active);
      * no RotoWire news item knowable after evidence_checked_at (a new item = possible activation -> re-verify);
      * no 2026-27 box-score participation for him (participated None = not checkable -> block).
    state: player_state row (None = no current RotoWire record). Re-evaluated at every acquisition / game instant."""
    from cbb_platform.prod import rotowire_feed as RW
    if row.get("current_game_available") != "FALSE":
        return True, f"CONFLICT_AVAILABILITY_{row.get('current_game_available') or 'UNKNOWN'}"
    if state is None:
        return True, "CONFLICT_NO_CURRENT_STATUS"
    if state.get("identity_resolution_status") == "RESOLVED":
        return False, "RESOLVED_NORMAL_HANDLING"
    if state.get("effective_status") not in RW.OUT_LIKE or state.get("gate"):
        return True, f"CONFLICT_POSSIBLY_ACTIVE_{state.get('effective_status')}"
    if last_news_at is not None and pd.Timestamp(last_news_at) > pd.to_datetime(row["evidence_checked_at"], utc=True):
        return True, "CONFLICT_NEW_NEWS_SINCE_EVIDENCE_CHECK"
    if participated is None:
        return True, "CONFLICT_PARTICIPATION_UNCHECKED"
    if participated:
        return True, "CONFLICT_CURRENT_SEASON_PARTICIPATION"
    return False, "CONFLICT_DETERMINISTICALLY_UNAVAILABLE"
