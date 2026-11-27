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

The NON_BLOCKING classes do not block the whole game. Stage 05 (2026-11-27): the premise "he is not in the active
universe, so no projection is conditioned on him" was false -- the frozen pool simulates such a player from his prior
appearances (RJ Luis LSU p_play 0.77 / 0.17 / 0.12). A NON_BLOCKING row in force that names a deterministic
canonical_player_id therefore enters the availability frame as CONFIRMED non-participation (verified_unavailable ->
rotowire_feed.availability_frame status UNAVAILABLE / LONG_TERM_OUT, source CURATED_VERIFIED_STATE): the player is not
simulated, not priced and leaves the game layer's expected points. A live RESOLVED RotoWire status for the same player
always governs instead; a row without a canonical id, and SOURCE_CONFLICT (genuine uncertainty), never do this.
It never adds a player, an identity or a roster row. A row applies only:
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


def verified_unavailable(states: pd.DataFrame, teams, as_of) -> pd.DataFrame:
    """NON_BLOCKING rows in force at as_of for these canonical teams that name a deterministic canonical_player_id:
    verified current-game non-participation (Stage 05). Pure; the caller applies RotoWire precedence."""
    f = in_force(states, as_of)
    return f[f["operational_class"].isin(NON_BLOCKING) & f["affiliated_team_id"].isin(set(teams))
             & (f["canonical_player_id"].astype(str).str.strip() != "")]


def verified_status(operational_class: str) -> str:
    """The inputs.apply_availability status of a verified non-participation class (both are inputs.UNAVAILABLE)."""
    return "LONG_TERM_OUT" if operational_class == ROSTERED_LONG_TERM_OUT else "UNAVAILABLE"


def verified_knowable_at(row) -> pd.Timestamp:
    """The instant the verification became usable: the start (ET) of verified_on, in UTC (in_force is by ET date)."""
    return pd.Timestamp(row["verified_on"]).tz_localize("America/New_York").tz_convert("UTC")


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


# ============================================================================ curated-state maintenance (Stage 04)
# Curated rows lapse by design (fail closed again until a human re-verifies) -- and on 2026-11-13..26 they lapsed
# silently, so known inactive players blocked their teams' games, and rows that newer authorized observations had
# already contradicted (a player who has since played; RotoWire moved the player to another team) were never noticed.
# reconcile() is READ-ONLY monitoring: it never extends, edits or applies a row, and nothing fails open because of it.
EXPIRY_WARN_DAYS = 7
INACTIVE_CLASSES = (AFFILIATED_INACTIVE_ELIGIBILITY, AFFILIATED_ROSTER_LIMIT, DELAYED_ELIGIBILITY, STALE_NONCURRENT,
                    PROFESSIONAL_NONCURRENT, ROSTERED_LONG_TERM_OUT)
ACTION = ("EXPIRED_BLOCKING", "TEAM_CHANGED_BLOCKING", "CONTRADICTED_BY_PARTICIPATION", "EXPIRING_SOON_BLOCKING_IF_LAPSED",
          "UNRESOLVED_NOW_RESOLVABLE", "UNRESOLVED_UNCURATED_BLOCKING", "OMISSION_EXPIRED_NOT_MOOT")


def _lapse(row: dict, day: pd.Timestamp, warn_days: int) -> str:
    v, r = pd.Timestamp(row["verified_on"]), pd.Timestamp(row["review_by"])
    if day < v:
        return "NOT_YET_IN_FORCE"
    if day >= r:
        return "EXPIRED"
    if row.get("eligible_from") and day >= pd.Timestamp(row["eligible_from"]):
        return "ELIGIBLE_FROM_REACHED"
    return "EXPIRING_SOON" if (r - day).days <= warn_days else "IN_FORCE"


def reconcile(states: pd.DataFrame, omissions: pd.DataFrame, pstate: pd.DataFrame, roster: pd.DataFrame,
              box: pd.DataFrame | None, as_of, warn_days: int = EXPIRY_WARN_DAYS, names: dict | None = None) -> dict:
    """Every curated row and every unresolved non-active RotoWire record at as_of, classified (read-only).
    pstate: rotowire_feed.player_state at as_of; roster: the identity roster (CBBD + applied omissions + box
    participation; roster_source column optional); box: 2026-27 box lines (roster_omissions.BOX_COLS) or None;
    names: {rotowire_player_id: observed RotoWire name} (event_names) for the uncurated unresolved records."""
    from cbb_platform.prod import matching as MT, roster_omissions as RO, rotowire_feed as RW
    T = pd.Timestamp(as_of)
    T = T.tz_localize("UTC") if T.tzinfo is None else T.tz_convert("UTC")
    day = pd.Timestamp(_day(T))
    ps = {str(r["rotowire_player_id"]): r for r in (pstate.to_dict("records") if pstate is not None else [])}
    bx = pd.DataFrame(columns=RO.BOX_COLS) if box is None else box
    bx = bx[pd.to_datetime(bx["historical_knowable_at"], utc=True) <= T] if len(bx) else bx
    played = {}
    for p, t, k in zip(bx.get("canonical_player_id", []), bx.get("canonical_team_id", []),
                       pd.to_datetime(bx["historical_knowable_at"], utc=True) if len(bx) else []):
        if isinstance(p, str):
            played.setdefault(p, []).append((t, k))
    r = roster.copy()
    full = (r.get("first_name", pd.Series("", index=r.index)).fillna("") + " "
            + r.get("last_name", pd.Series("", index=r.index)).fillna("")).str.strip()
    r["_n"] = [MT.norm(a) or MT.norm(b) for a, b in zip(full, r["player_name"])]
    by_name = r.groupby(["canonical_team_id", "_n"])["canonical_player_id"].agg(set).to_dict() if len(r) else {}
    unresolved = lambda s: s is not None and s.get("identity_resolution_status") != "RESOLVED" \
        and s.get("effective_status") not in ("ACTIVE", RW.NOT_LISTED)
    rows = []
    for row in (states.to_dict("records") if states is not None else []):
        pid, tid = row["rotowire_player_id"], row["affiliated_team_id"]
        s = ps.get(pid)
        lapse = _lapse(row, day, warn_days)
        obs_team = s.get("canonical_team_id") if s else None
        flags = []
        if s is None:
            obs = "NOT_OBSERVED"
        elif s.get("identity_resolution_status") == "RESOLVED":
            obs = "RESOLVED_ROW_MOOT"
        elif obs_team != tid:
            obs = "TEAM_CHANGED"
        elif not unresolved(s):
            obs = "INERT_ACTIVE_OR_NOT_LISTED"
        else:
            obs = "APPLIES"
        blocking_if_lapsed = unresolved(s) and row["operational_class"] in NON_BLOCKING
        if lapse == "EXPIRED" and obs == "APPLIES":
            flags.append("EXPIRED_BLOCKING")
        if obs == "TEAM_CHANGED" and unresolved(s):
            flags.append("TEAM_CHANGED_BLOCKING")
        if lapse == "EXPIRING_SOON" and blocking_if_lapsed and obs == "APPLIES":
            flags.append("EXPIRING_SOON_BLOCKING_IF_LAPSED")
        cid = row.get("canonical_player_id") or ""
        later = [k for t, k in played.get(cid, []) if k.tz_convert("America/New_York").date() >= pd.Timestamp(row["verified_on"]).date()] \
            if cid else []
        if later and row["operational_class"] in INACTIVE_CLASSES:
            flags.append("CONTRADICTED_BY_PARTICIPATION")
        rows.append({"kind": "CURATED_STATE", "rotowire_player_id": pid, "name": row["rotowire_player_name"],
                     "operational_class": row["operational_class"], "affiliated_team_id": tid,
                     "observed_team_id": obs_team, "effective_status": s and s.get("effective_status"),
                     "identity": s and s.get("identity_resolution_status"), "lapse": lapse, "review_by": row["review_by"],
                     "observation": obs, "box_lines_since_verified": len(later), "flags": flags})
    cur = set(states["rotowire_player_id"]) if states is not None and len(states) else set()
    for pid, s in sorted(ps.items()):
        if pid in cur or not unresolved(s):
            continue
        flags = []
        tid = s.get("canonical_team_id")
        om = RO.in_force(omissions, T) if omissions is not None else pd.DataFrame(columns=RO.COLS)
        if isinstance(tid, str) and RO.recruit_anchored(om, pid, tid):
            obs = "RECRUIT_ANCHORED_OMISSION"
        else:
            obs = "UNCURATED"
            flags.append("UNRESOLVED_UNCURATED_BLOCKING")
        rows.append({"kind": "UNRESOLVED_RECORD", "rotowire_player_id": pid, "name": (names or {}).get(pid),
                     "observed_team_id": tid,
                     "effective_status": s.get("effective_status"), "identity": s.get("identity_resolution_status"),
                     "observation": obs, "flags": flags})
    for row in (omissions.to_dict("records") if omissions is not None else []):
        lapse = _lapse(row, day, warn_days)
        cid, tid = row["canonical_player_id"], row["canonical_team_id"]
        listed = bool(cid) and cid in by_name.get((tid, MT.norm(row["player_name"])), set())
        named = by_name.get((tid, MT.norm(row["player_name"])), set())
        moot = listed or (not cid and bool(named))
        flags = ["OMISSION_EXPIRED_NOT_MOOT"] if lapse == "EXPIRED" and not moot else []
        rows.append({"kind": "ROSTER_OMISSION", "rotowire_player_id": row["rotowire_player_id"], "name": row["player_name"],
                     "canonical_team_id": tid, "lapse": lapse, "review_by": row["review_by"],
                     "observation": "MOOT_ROSTER_LISTS_PLAYER" if moot else "NEEDED", "flags": flags})
    # unresolved records whose identity has since become deterministically resolvable (exact name on the identity
    # roster of the observed team -- the map rule extend_maps applies at the next acquisition)
    for x in rows:
        if x["kind"] == "ROSTER_OMISSION" or x.get("identity") == "RESOLVED" or not isinstance(x.get("observed_team_id"), str):
            continue
        s = ps.get(x["rotowire_player_id"])
        if s is None:
            continue
        hit = by_name.get((x["observed_team_id"], MT.norm(x.get("name") or "")), set())
        if len(hit) == 1:
            x["flags"].append("UNRESOLVED_NOW_RESOLVABLE")
            x["resolvable_canonical_player_id"] = next(iter(hit))
    counts = {}
    for x in rows:
        for k in [x.get("lapse"), x.get("observation")] + x["flags"]:
            if k:
                counts[f"{x['kind']}:{k}"] = counts.get(f"{x['kind']}:{k}", 0) + 1
    action = [x for x in rows if set(x["flags"]) & set(ACTION)]
    return {"as_of": T.isoformat(), "warn_days": warn_days, "curated_states": int(len(states)) if states is not None else 0,
            "unresolved_nonactive": sum(1 for s in ps.values() if unresolved(s)), "counts": dict(sorted(counts.items())),
            "action_required": len(action), "actions": [{k: x.get(k) for k in ("kind", "rotowire_player_id", "name",
                                                         "observed_team_id", "flags")} for x in action],
            "rows": rows}


def event_names(events: pd.DataFrame) -> dict:
    """{rotowire_player_id: 'first last'} of each provider ID's latest stored event."""
    if events is None or events.empty:
        return {}
    last = events.sort_values("seq", kind="mergesort").groupby("rotowire_player_id").tail(1)
    return {str(p): " ".join(x for x in (f, l) if isinstance(x, str) and x)
            for p, f, l in zip(last["rotowire_player_id"], last["first_name"], last["last_name"])}

