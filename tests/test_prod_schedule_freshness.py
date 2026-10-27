"""Schedule-freshness gate for real prediction / pricing / publication (directive 2026-10-26 §3): 2 h per game, one
targeted refresh of the stale game's schedule window, fail closed with an audit record. Synthetic state + ledger; no
network, no CBBD call."""
import json

import pandas as pd
import pytest

from cbb_platform.prod import cbbd_acquire as A, config as PC, schedule_freshness as SF, sync as SY

TIP = pd.Timestamp("2026-11-11T00:30:00Z")
NOV = ("games", {"season": 2027, "startDateRange": "2026-11-01", "endDateRange": "2026-12-01"})
KEY = A.request_key(*NOV)
SHA = "a" * 64
PRED = {"canonical_game_id": "G1", "provider_game_id": "9001", "home_team_id": "TH", "away_team_id": "TA",
        "start_time": TIP, "tip_time_quality": "EXACT"}


def _sched(**kv):
    r = {"canonical_game_id": "G1", "provider_game_id": "9001", "home_team_id": "TH", "away_team_id": "TA",
         "start_time": TIP, "tip_time_quality": "EXACT", "status": "scheduled", "listed_in_latest_schedule": True}
    r.update(kv)
    return pd.DataFrame([r])


def _manifest(received="2026-11-10T12:00:00Z", sha=SHA):
    return {"feed_provenance": {KEY: {"endpoint": "games", "params": NOV[1], "received_at": received, "sha256": sha}}}


def _ledger(path, *entries):
    with path.open("a") as f:
        for status, t, sha in entries:
            f.write(json.dumps({"request_key": KEY, "status": status, "verified_at": t, "received_at": "2026-11-10T12:00:00Z",
                                "response_sha256": sha}) + "\n")


class World:
    """A fake current state (manifest + prod_schedule) the refresh callable can change."""
    def __init__(self, tmp_path, sched=None, manifest=None):
        self.ledger = tmp_path / "cbbd.jsonl"
        self.ledger.touch()
        self.sched = _sched() if sched is None else sched
        self.manifest = manifest or _manifest()
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(SF, "current_schedule", lambda root: ({"manifest": self.manifest}, self.sched))


def _run(w, cp, refresh=None, tmp_path=None, now=None):
    return SF.gate(pd.DataFrame([PRED]), cp, ledger_path=w.ledger, refresh=refresh,
                   audit_path=(tmp_path / "audit.jsonl") if tmp_path else None, now=now)


def test_policy_constants():
    assert PC.SCHEDULE_FRESHNESS_MAX_AGE_HOURS == 2.0
    assert PC.SCHEDULE_FRESHNESS_POLICY == "2_HOURS_TARGETED_REFRESH_FAIL_CLOSED"
    assert PC.SCHEDULE_MAX_AGE_HOURS == 36                        # recording rule unchanged (additive gate)


def test_window_mapping_is_targeted():
    assert SF.windows_for(TIP) == ["2026-11-01"]
    assert len(SY.schedule_windows()) == 7                        # a targeted refresh names 1 of 7, never all


def test_fresh_within_two_hours_passes_without_refresh(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.install(monkeypatch)
    _ledger(w.ledger, ("FETCHED_UNCHANGED", "2026-11-10T21:00:00Z", SHA))   # unchanged re-fetch counts
    out = _run(w, "2026-11-10T22:30:00Z", refresh=lambda ws: pytest.fail("no refresh when fresh"), tmp_path=tmp_path)
    g = out["games"]["G1"]
    assert g["SCHEDULE_FRESHNESS_GATE"] == "PASS" and g["age_hours"] == pytest.approx(1.5) and out["refresh"] is None
    assert json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[0])["SCHEDULE_FRESHNESS_GATE"] == "PASS"


def test_verification_of_a_different_payload_or_future_instant_is_ignored(tmp_path):
    led = tmp_path / "l.jsonl"
    led.touch()
    _ledger(led, ("FETCHED", "2026-11-10T22:00:00Z", "b" * 64),            # another payload: the state did not use it
            ("FETCHED_UNCHANGED", "2026-11-10T23:59:00Z", SHA))            # after the checkpoint: not knowable
    v = SF.window_verification(_manifest(), led, "2026-11-10T22:30:00Z")
    assert v == {"2026-11-01": pd.Timestamp("2026-11-10T12:00:00Z")}


def test_stale_triggers_one_targeted_refresh_then_passes(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.install(monkeypatch)

    def refresh(windows):
        w.calls.append(windows)
        _ledger(w.ledger, ("FETCHED_UNCHANGED", "2026-11-10T22:31:00Z", SHA))
        return {"status": "UNCHANGED", "sync_id": "Y1"}
    out = _run(w, "2026-11-10T22:30:00Z", refresh, tmp_path, now=lambda: pd.Timestamp("2026-11-10T22:32:00Z"))
    assert w.calls == [["2026-11-01"]]
    g = out["games"]["G1"]
    assert g["SCHEDULE_FRESHNESS_GATE"] == "PASS" and g["refreshed"] and out["refresh"]["windows"] == ["2026-11-01"]


@pytest.mark.parametrize("change, reason", [
    ({"start_time": TIP + pd.Timedelta(hours=2)}, SF.TIP_CHANGED),
    ({"home_team_id": "TA", "away_team_id": "TH"}, SF.TEAMS_CHANGED),
    ({"provider_game_id": "9999"}, SF.IDENTITY_CHANGED),
    ({"status": "postponed"}, SF.NOT_LISTED),
    ({"listed_in_latest_schedule": False}, SF.NOT_LISTED),
    ({"tip_time_quality": "DATE_ONLY"}, SF.TIP_NOT_EXACT),
    ({"canonical_game_id": "OTHER"}, SF.GAME_NOT_IN_SCHEDULE)])
def test_contradiction_after_refresh_fails_closed(tmp_path, monkeypatch, change, reason):
    w = World(tmp_path)
    w.install(monkeypatch)

    def refresh(windows):
        w.sched = _sched(**change)
        w.manifest = _manifest(received="2026-11-10T22:31:00Z", sha="c" * 64)
        return {"status": "PROMOTED"}
    g = _run(w, "2026-11-10T22:30:00Z", refresh, now=lambda: pd.Timestamp("2026-11-10T22:32:00Z"))["games"]["G1"]
    assert g["SCHEDULE_FRESHNESS_GATE"] == "FAIL" and reason in g["reasons"]
    assert not (g["publication_allowed"] or g["pricing_allowed"] or g["actionable_bet_allowed"])


def test_refresh_failure_fails_closed_and_is_audited(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.install(monkeypatch)

    def boom(windows):
        raise RuntimeError("CBBD down")
    out = _run(w, "2026-11-10T22:30:00Z", boom, tmp_path)
    g = out["games"]["G1"]
    assert g["SCHEDULE_FRESHNESS_GATE"] == "FAIL" and SF.REFRESH_FAILED in g["reasons"]
    rec = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert rec["refresh"]["status"] == "FAILED" and "CBBD down" in rec["refresh"]["error"]


def test_refresh_that_does_not_reverify_stays_stale(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.install(monkeypatch)
    g = _run(w, "2026-11-10T22:30:00Z", lambda ws: {"status": "UNCHANGED"})["games"]["G1"]
    assert g["SCHEDULE_FRESHNESS_GATE"] == "FAIL" and SF.STALE_AFTER_REFRESH in g["reasons"]


def test_started_game_never_refreshed_and_fails(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.install(monkeypatch)
    _ledger(w.ledger, ("FETCHED_UNCHANGED", "2026-11-11T00:00:00Z", SHA))
    out = _run(w, "2026-11-11T00:31:00Z", refresh=lambda ws: pytest.fail("never refresh a started game"))
    assert out["games"]["G1"]["SCHEDULE_FRESHNESS_GATE"] == "FAIL" and SF.STARTED in out["games"]["G1"]["reasons"]


def test_no_state_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(SF, "current_schedule", lambda root: (None, pd.DataFrame()))
    led = tmp_path / "l.jsonl"
    led.touch()
    g = SF.gate(pd.DataFrame([PRED]), "2026-11-10T22:30:00Z", ledger_path=led, refresh=None,
                audit_path=None)["games"]["G1"]
    assert g["SCHEDULE_FRESHNESS_GATE"] == "FAIL" and SF.NO_VERIFICATION in g["reasons"]


# ---------------------------------------------------------------- end to end: real sync path, fake CBBD HTTP layer
def test_targeted_refresh_through_the_production_sync(tmp_path):
    from cbb_platform.prod import feed_fixtures as FF, fixtures as FX, state as ST
    hist = FX.synthetic_history(tmp_path / "history.duckdb")
    kw = dict(history_db=hist, state_root=tmp_path / "state", cache_root=tmp_path / "cache",
              ledger_path=tmp_path / "ledger.jsonl", sync_log=tmp_path / "sync.jsonl", work_root=tmp_path / "work",
              credential=lambda: FF.DUMMY_CREDENTIAL, provider_kind="LIVE")
    http = FF.FakeCBBD(FF.payloads("after"))
    assert SY.run(clock=FF.clock_at("2026-11-10T12:00Z"), http_get=http, **kw)["status"] == "PROMOTED"
    _, sched = SF.current_schedule(kw["state_root"])
    cp = pd.Timestamp("2026-11-10T15:00Z")       # 3 h after the last verification: stale for the 2 h gate, inside the 6 h TTL
    up = sched[(pd.to_datetime(sched["start_time"], utc=True) > cp) & (sched["tip_time_quality"] == "EXACT")]
    assert len(up)
    pred = up.assign(start_time=pd.to_datetime(up["start_time"], utc=True))
    n0 = len(A.ledger(kw["ledger_path"]))

    def refresh(windows):
        return SY.run(clock=FF.clock_at("2026-11-10T15:01Z"), http_get=http, refresh_schedule_windows=windows, **kw)
    out = SF.gate(pred, cp, state_root=kw["state_root"], ledger_path=kw["ledger_path"], refresh=refresh,
                  audit_path=tmp_path / "audit.jsonl", now=lambda: pd.Timestamp("2026-11-10T15:02Z"))
    live = [e for e in A.ledger(kw["ledger_path"])[n0:] if e["endpoint"] == "games"
            and e["status"] in ("FETCHED", "FETCHED_UNCHANGED")]
    assert {e["params"]["startDateRange"] for e in live} == set(out["refresh"]["windows"])   # targeted, not 7 windows
    assert len(out["refresh"]["windows"]) < len(SY.schedule_windows())
    st = up.set_index("canonical_game_id")
    ok = st["listed_in_latest_schedule"].astype(bool) & ~st["status"].astype(str).str.lower().isin(
        ["canceled", "cancelled", "postponed"])
    assert ok.any() and (~ok).any()                               # the fixture holds a cancelled / unlisted game
    for gid, g in out["games"].items():
        assert g["SCHEDULE_FRESHNESS_GATE"] == ("PASS" if ok[gid] else "FAIL"), (gid, g)
        assert ok[gid] or SF.NOT_LISTED in g["reasons"]
    assert ST.current(kw["state_root"]) is not None
