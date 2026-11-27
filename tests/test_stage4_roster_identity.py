"""Stage 04 (2026-11-27): roster / transfer / player-identity integrity.

1. BOX_PARTICIPATION: precedence rank 2 (current-season game participation) derived deterministically from the state's
   own knowable 2026-27 box lines -- the curated omission rows had been the only route and they lapsed.
2. Stale identity re-resolution: a RotoWire record that already left the current list is re-resolved against the
   current maps (it is never re-observed, so it kept an UNMATCHED identity forever).
3. Curated-state maintenance: rotowire_player_states.reconcile is read-only monitoring of expiring / expired rows,
   team changes, rows contradicted by newer participation and records that became resolvable.
"""
from __future__ import annotations

import pandas as pd
import pytest

from cbb_platform.prod import roster_omissions as RO, rotowire_feed as RW, rotowire_player_states as PS

T = pd.Timestamp("2026-11-27T15:00Z")


def _box(*rows):
    out = []
    for i, r in enumerate(rows):
        d = {"canonical_game_id": f"G{i}", "canonical_player_id": "P1", "canonical_team_id": "TA",
             "identity_status": "RESOLVED", "source_player_name": "Robbie Avila", "source_position": "C",
             "event_time": pd.Timestamp("2026-11-20T01:00Z"), "historical_knowable_at": pd.Timestamp("2026-11-20T05:00Z")}
        d.update(r)
        out.append(d)
    return pd.DataFrame(out, columns=RO.BOX_COLS)


ROSTER = pd.DataFrame([{"canonical_player_id": "P9", "canonical_team_id": "TA", "first_name": "Other", "last_name": "Guy",
                        "player_name": "Other Guy", "position": "G"},
                       {"canonical_player_id": "P8", "canonical_team_id": "TB", "first_name": "Bee", "last_name": "Player",
                        "player_name": "Bee Player", "position": "F"}])


def _state(**kv):
    d = {c: "" for c in PS.COLS}
    d.update({"rotowire_player_id": "21059", "rotowire_player_name": "Robbie Avila", "rotowire_team_name": "TCU",
              "affiliated_team_id": "TA", "canonical_player_id": "P1", "operational_class": PS.STALE_NONCURRENT,
              "verified_on": "2026-11-20", "review_by": "2026-12-20", "evidence": "x" * 50})
    d.update(kv)
    return pd.DataFrame([d], columns=PS.COLS)


# ============================================================================ 1. BOX_PARTICIPATION
def test_participation_row_applies_for_a_player_cbbd_omits():
    rows, dec = RO.participation_rows(_box({}, {"canonical_game_id": "G9"}), ROSTER, T)
    assert dec[0]["decision"] == "APPLIED" and dec[0]["box_games"] == 2
    r = rows.iloc[0]
    assert (r.canonical_player_id, r.canonical_team_id, r.player_name, r.first_name, r.last_name, r.roster_source) == (
        "P1", "TA", "Robbie Avila", "Robbie", "Avila", RO.BOX_PARTICIPATION)


def test_participation_is_point_in_time_never_future_lines():
    b = _box({"historical_knowable_at": pd.Timestamp("2026-11-27T16:00Z")})      # knowable after the run instant
    rows, dec = RO.participation_rows(b, ROSTER, T)
    assert rows.empty and dec == []
    assert len(RO.participation_rows(b, ROSTER, pd.Timestamp("2026-11-27T16:00Z"))[0]) == 1   # exactly at knowability


@pytest.mark.parametrize("box,roster,states,sup,why", [
    (_box({}, {"canonical_team_id": "TB", "canonical_game_id": "G7"}), ROSTER, None, None, "REJECTED_MULTI_TEAM_PARTICIPATION"),
    (_box({}), pd.concat([ROSTER, pd.DataFrame([{"canonical_player_id": "P1", "canonical_team_id": "TA", "first_name": "Robbie",
                                                 "last_name": "Avila", "player_name": "Robbie Avila", "position": "C"}])]),
     None, None, "MOOT_CBBD_LISTS_PLAYER"),
    (_box({}), pd.concat([ROSTER, pd.DataFrame([{"canonical_player_id": "P1", "canonical_team_id": "TB", "first_name": "Robbie",
                                                 "last_name": "Avila", "player_name": "Robbie Avila", "position": "C"}])]),
     None, None, "REJECTED_ON_OTHER_TEAM_CBBD_ROSTER"),
    (_box({}), pd.concat([ROSTER, pd.DataFrame([{"canonical_player_id": "P2", "canonical_team_id": "TA", "first_name": "Robbie",
                                                 "last_name": "Avila", "player_name": "Robbie Avila", "position": "C"}])]),
     None, None, "REJECTED_COMPETING_CANONICAL_CANDIDATE"),
    (_box({}), ROSTER, None, {"P1"}, "REJECTED_IDENTITY_CONFLICT_SUPPRESSED"),
    (_box({}), ROSTER, _state(), None, "WITHHELD_CURATED_NONCURRENT"),
])
def test_participation_override_safety(box, roster, states, sup, why):
    rows, dec = RO.participation_rows(box, roster, T, states, sup)
    assert rows.empty and [d["decision"] for d in dec] == [why]


def test_unresolved_or_lapsed_curated_rows_do_not_withhold():
    b = _box({}, {"canonical_game_id": "G2", "identity_status": "UNRESOLVED_NO_MATCH", "canonical_team_id": "TB"})
    assert RO.participation_rows(b, ROSTER, T)[1][0]["decision"] == "APPLIED"          # unresolved line ignored
    lapsed = _state(review_by="2026-11-26")
    assert RO.participation_rows(_box({}), ROSTER, T, lapsed)[1][0]["decision"] == "APPLIED"
    other_team = _state(affiliated_team_id="TB")                                       # non-current elsewhere only
    assert RO.participation_rows(_box({}), ROSTER, T, other_team)[1][0]["decision"] == "APPLIED"


def test_identity_roster_carries_participation_rows_and_one_team_per_player():
    om = pd.DataFrame(columns=RO.COLS)
    r = RW.with_omissions(ROSTER, om, T, box=_box({}))
    assert set(r["roster_source"]) == {"CBBD", RO.BOX_PARTICIPATION}
    assert r.groupby("canonical_player_id")["canonical_team_id"].nunique().max() == 1
    assert RW.with_omissions(ROSTER, om, T).equals(ROSTER.assign(roster_source="CBBD"))    # no box -> unchanged
    ctx = RW.IdentityContext(pd.DataFrame({"canonical_team_id": ["TA", "TB"], "school": ["TCU", "B"],
                                           "display_name": ["TCU", "B"], "mascot": [None, None]}), r)
    assert ("P1", "TA") in set(zip(ctx._roster["canonical_player_id"], ctx._roster["canonical_team_id"]))


# ============================================================================ 2. stale identity re-resolution
class _Ctx:
    """resolve() for listed rows; resolve_stored() for stored rows (rotowire_web_acquire.WebIdentity contract)."""

    def __init__(self, mapped: bool):
        self.mapped = mapped

    def resolve(self, f):
        return {"canonical_player_id": None, "canonical_team_id": "TA", "identity_resolution_status": "UNMATCHED",
                "identity_method": "PROVIDER_ID_UNMAPPED", "team_resolution": "EXPLICIT_WEB_TEAM_MAP"}

    def resolve_stored(self, prev):
        if not self.mapped:
            return self.resolve(prev)
        return {"canonical_player_id": "P1", "canonical_team_id": prev["canonical_team_id"],
                "identity_resolution_status": "RESOLVED", "identity_method": "EXPLICIT_WEB_PLAYER_MAP",
                "team_resolution": "EXPLICIT_WEB_TEAM_MAP"}


class _Led:
    def _log(self, e):
        pass


def _snap(store, ctx, at, rows):
    fields = [{"rotowire_player_id": pid, "sportdataio_player_id": None, "first_name": "Robbie", "last_name": "Avila",
               "position": "C", "rotowire_team_id": None, "team_name": "TCU", "raw_status": raw, "normalized_status": st,
               "injury_type": None, "injury_location": None, "injury_detail": None, "injury_side": None,
               "return_date": None, "priority": None, "source_datetime": None,
               "source_datetime_status": "SNAPSHOT_COLLECTED_AT", "source_last_edited": None,
               "source_last_edited_status": "MISSING", "record_sha256": pid + str(raw)} for pid, raw, st in rows]
    return RW.ingest_parsed(fields, len(fields), 0, {"collected_at": at.isoformat(), "raw_payload_sha256": "s" + at.isoformat()},
                            "snapshot", ctx, store, _Led())


def test_off_list_record_is_re_resolved_once_and_point_in_time(tmp_path):
    store = RW.EventStore(tmp_path / "rw")
    t0, t1, t2, t3 = (pd.Timestamp(f"2026-11-{d}T13:30Z") for d in (19, 20, 26, 27))
    _snap(store, _Ctx(False), t0, [("21059", "Out", "OUT")])
    _snap(store, _Ctx(False), t1, [])                                     # leaves the list -> NOT_LISTED, unresolved
    assert _snap(store, _Ctx(False), t2, [])["events_new"] == 0           # unchanged identity: nothing new
    assert _snap(store, _Ctx(True), t3, [])["events_new"] == 1            # map row now exists -> re-resolved
    e = store.load()[-1]
    assert (e["reconciliation"], e["normalized_status"], e["identity_resolution_status"], e["canonical_player_id"]) == (
        "IDENTITY_RERESOLVED_OFF_LIST", RW.NOT_LISTED, "RESOLVED", "P1")
    assert _snap(store, _Ctx(True), t3 + pd.Timedelta(hours=1), [])["events_new"] == 0      # idempotent
    ev = store.frame()
    before = RW.player_state(ev, t2 + pd.Timedelta(hours=1)).set_index("rotowire_player_id")
    after = RW.player_state(ev, t3 + pd.Timedelta(minutes=1)).set_index("rotowire_player_id")
    assert before.loc["21059", "identity_resolution_status"] == "UNMATCHED"                  # earlier state untouched
    assert after.loc["21059", "identity_resolution_status"] == "RESOLVED"


def test_web_identity_resolve_stored_requires_roster_membership(tmp_path):
    from cbb_platform.prod import rotowire_web_acquire as WA
    (tmp_path / WA.TEAM_MAP_FILE).write_text("rotowire_team_name,canonical_team_id,method,added_at,parser_version\nTCU,TA,X,t,v\n")
    (tmp_path / WA.PLAYER_MAP_FILE).write_text(
        "rotowire_player_id,canonical_player_id,canonical_team_id,rotowire_player_name,rotowire_team_name,method,added_at,"
        "parser_version\n21059,P1,TA,Robbie Avila,TCU,X,t,v\n")
    teams = pd.DataFrame({"canonical_team_id": ["TA"], "school": ["TCU"], "display_name": ["TCU"], "mascot": [None]})
    prev = {"rotowire_player_id": "21059", "canonical_team_id": "TA", "team_resolution": "EXPLICIT_WEB_TEAM_MAP"}
    off = WA.WebIdentity(RW.IdentityContext(teams, ROSTER.assign(roster_source="CBBD")), tmp_path)
    assert off.resolve_stored(prev)["identity_method"] == "EXPLICIT_MAP_NOT_ON_ROSTER"
    on = WA.WebIdentity(RW.IdentityContext(teams, RW.with_omissions(ROSTER, pd.DataFrame(columns=RO.COLS), T,
                                                                   box=_box({}))), tmp_path)
    assert on.resolve_stored(prev)["identity_resolution_status"] == "RESOLVED"
    assert on.resolve_stored({**prev, "canonical_team_id": None}) is None
    assert on.team_name_for("TA") == "TCU" and on.team_name_for("TZ") is None


# ============================================================================ 3. curated-state maintenance
def _pstate(rows):
    cols = ["rotowire_player_id", "effective_status", "canonical_team_id", "identity_resolution_status"]
    return pd.DataFrame(rows, columns=cols)


def test_reconcile_flags_every_maintenance_case_and_writes_nothing():
    states = pd.concat([
        _state(rotowire_player_id="1", operational_class=PS.AFFILIATED_INACTIVE_ELIGIBILITY, review_by="2026-11-22",
               verified_on="2026-11-08", canonical_player_id="PX"),                                # lapsed, still blocks
        _state(rotowire_player_id="2", affiliated_team_id="TOLD", canonical_player_id="PY"),       # RotoWire moved him
        _state(rotowire_player_id="3", operational_class=PS.AFFILIATED_INACTIVE_ELIGIBILITY, canonical_player_id="P1",
               verified_on="2026-11-10"),                                                          # has since played
        _state(rotowire_player_id="4", review_by="2026-11-30", canonical_player_id="PZ"),          # expiring soon
    ], ignore_index=True)
    before = states.copy()
    ps = _pstate([("1", "OUT", "TA", "UNMATCHED"), ("2", "OUT", "TNEW", "UNMATCHED"), ("3", "GTD", "TA", "UNMATCHED"),
                  ("4", "OUT", "TA", "UNMATCHED"), ("5", "OUT", "TA", "UNMATCHED"), ("6", "OUT", "TB", "UNMATCHED")])
    roster = RW.with_omissions(ROSTER, pd.DataFrame(columns=RO.COLS), T, box=_box({}))
    rep = PS.reconcile(states, pd.DataFrame(columns=RO.COLS), ps, roster, _box({}), T,
                       names={"5": "Nobody Here", "6": "Bee Player"})
    f = {x["rotowire_player_id"]: set(x["flags"]) for x in rep["rows"]}
    assert "EXPIRED_BLOCKING" in f["1"]
    assert "TEAM_CHANGED_BLOCKING" in f["2"]
    assert "CONTRADICTED_BY_PARTICIPATION" in f["3"]
    assert "EXPIRING_SOON_BLOCKING_IF_LAPSED" in f["4"]
    assert f["5"] == {"UNRESOLVED_UNCURATED_BLOCKING"}
    assert f["6"] == {"UNRESOLVED_UNCURATED_BLOCKING", "UNRESOLVED_NOW_RESOLVABLE"}     # exact name on TB's roster
    assert rep["action_required"] == 6 and rep["unresolved_nonactive"] == 6
    assert states.equals(before)                                                       # read-only: never extends a row
    assert not PS.non_blocking(states, "1", "TA", T)                                   # lapsed row still fails closed


def test_reconcile_omission_rows_moot_or_needed():
    om = pd.DataFrame([{**{c: "" for c in RO.COLS}, "rotowire_player_id": "27385", "player_name": "Bee Player",
                        "canonical_team_id": "TB", "canonical_player_id": "P8", "verified_on": "2026-11-09",
                        "review_by": "2026-11-23"},
                       {**{c: "" for c in RO.COLS}, "rotowire_player_id": "28337", "player_name": "Lost Soul",
                        "canonical_team_id": "TB", "source_recruit_id": "1", "verified_on": "2026-11-09",
                        "review_by": "2026-11-23"}], columns=RO.COLS)
    rep = PS.reconcile(pd.DataFrame(columns=PS.COLS), om, _pstate([]), ROSTER, None, T)
    o = {x["rotowire_player_id"]: x for x in rep["rows"] if x["kind"] == "ROSTER_OMISSION"}
    assert o["27385"]["observation"] == "MOOT_ROSTER_LISTS_PLAYER" and o["27385"]["flags"] == []
    assert o["28337"]["flags"] == ["OMISSION_EXPIRED_NOT_MOOT"]


def test_receipt_surfaces_curated_identity_action_as_informational():
    from cbb_platform.prod import lifecycle as L
    acq = {"auth": "PASS", "news_status": "PROCESSED", "snapshot_status": "PROCESSED", "at": "2026-11-27T15:00Z",
           "identity_reconciliation": {"status": "OK", "action_required": 2,
                                       "actions": [{"flags": ["EXPIRED_BLOCKING"]}, {"flags": ["TEAM_CHANGED_BLOCKING"]}]}}
    r = L.receipt({"availability_1000": {"rotowire_web_acquisition": acq}})
    w = [c for c in r["WARNING_CLASSES"] if c["warning"].startswith("CURATED_IDENTITY_ACTION_REQUIRED")]
    assert w and w[0]["warning"].endswith("(EXPIRED_BLOCKING, TEAM_CHANGED_BLOCKING)")
    assert not any(b.startswith("CURATED_IDENTITY") for b in r["ACTIVE_BLOCKERS"])     # monitoring, never a blocker


# ============================================================================ shipped tables (2026-11-27 re-verification)
def test_shipped_tables_after_stage4_reverification():
    s = PS.load()
    at = pd.Timestamp("2026-11-27T15:00Z")
    assert PS.classify(s, "26154", "T-aacc10aee85ee832", at) == PS.STALE_NONCURRENT     # Nwuli: Rutgers record
    assert PS.classify(s, "26154", "T-f898e42ddf3bd0cb", at) is None
    assert PS.classify(s, "20703", "T-ac5ed60df62aa41e", at) == PS.STALE_NONCURRENT     # Luis: left LSU after 1 game
    assert PS.classify(s, "20923", "T-262cae9a09cabf1a", at) == PS.SOURCE_CONFLICT      # Carter: genuine conflict
    c = s.set_index("rotowire_player_id").loc["20923"]
    assert c["current_game_available"] == "UNKNOWN"                                     # -> keeps blocking
    assert PS.conflict_blocks(c.to_dict(), None, None, False)[0]
    assert PS.classify(s, "23921", "T-7dd7b1683d08fb35", at) == PS.SOURCE_CONFLICT      # Maclin unchanged
    assert not {"21059", "20575"} & set(s["rotowire_player_id"])                         # Avila / Lindsay-Martin: playing
    assert RO.load().empty                                                              # omissions moot (CBBD / box)
