"""Stage 2 -- historical CBBD event integration. Numbered comments map to the directive's TEST REQUIREMENTS."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path

import pytest

from cbb_platform import cbbd_client, config as C, event_metrics as M, game_identity_audit as GI, lockbox, raw_store
from cbb_platform import source_fetch, wallclock_audit as W
from cbb_platform.adapters import availability, cbbd
from tests.conftest import FIXTURES, scalar

L = lockbox.LockboxViolation


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self.content = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.status_code = status
        self.headers = headers or {"X-CallLimit-Remaining": "100000"}


# =========================================================================== 1. lockbox is provider-independent
@pytest.mark.parametrize("endpoint,params", [
    ("plays/date", {"date": "2025-12-01"}), ("plays/date", {"date": "2026-03-01"}),
    ("plays/team", {"season": 2026, "team": "Purdue"}), ("substitutions/team", {"season": 2026, "team": "Purdue"}),
    ("lineups/team", {"season": "2025-26", "team": "Purdue"}), ("recruiting/players", {"year": 2026}),
    ("teams/roster", {"season": 2026}), ("plays/date", {}),
])
def test_stage2_endpoints_refuse_the_lockbox_before_any_network(endpoint, params, monkeypatch, tmp_path):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)           # a credential is never even read
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: pytest.fail("network call attempted"))
    client = cbbd_client.Client(log_path=tmp_path / "log.jsonl", raw_root=tmp_path / "raw", known_game_season=lambda g: None)
    with pytest.raises(L):
        client.fetch(endpoint, params, purpose="test")
    assert not (tmp_path / "raw").exists()


def test_lineups_game_for_unprovable_game_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: pytest.fail("network call attempted"))
    client = cbbd_client.Client(log_path=tmp_path / "l.jsonl", raw_root=tmp_path / "raw", known_game_season=lambda g: None)
    with pytest.raises(L):
        client.fetch("lineups/game", {"gameId": 123456789}, purpose="test")


def test_lockbox_response_is_never_stored_by_the_client(monkeypatch, tmp_path):
    monkeypatch.setenv("CBBD_API_KEY", "k" * 20)
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: _Resp([{"id": 1, "season": 2026, "gameId": 5}]))
    client = cbbd_client.Client(log_path=tmp_path / "l.jsonl", raw_root=tmp_path / "raw", known_game_season=lambda g: 2025)
    with pytest.raises(L):
        client.fetch("plays/date", {"date": "2025-01-10"}, purpose="test")
    assert not (tmp_path / "raw").exists() or not any((tmp_path / "raw").rglob("*.json*"))


# =========================================================================== 2. no 2025-26 outcomes anywhere
def test_no_lockbox_rows_in_canonical_event_tables(db):
    for t in ("fact.plays", "fact.shots", "fact.substitutions", "fact.lineup_stats"):
        assert scalar(db, f"SELECT count(*) FROM {t} WHERE season >= 2026") == 0, t
    assert scalar(db, "SELECT count(*) FROM core.rosters WHERE season >= 2026") == 0
    assert scalar(db, "SELECT count(*) FROM core.games WHERE season >= 2026") == 0
    assert scalar(db, "SELECT count(*) FROM core.recruits WHERE recruit_year >= 2025") == 0
    assert scalar(db, "SELECT count(*) FROM core.transfers WHERE transfer_year >= 2025") == 0
    assert scalar(db, """SELECT count(*) FROM fact.plays p JOIN core.games g USING (canonical_game_id)
                         WHERE g.game_date_et >= DATE '2025-08-01'""") == 0


def test_request_log_never_targets_the_lockbox():
    if not cbbd_client.REQUEST_LOG.exists():
        pytest.skip("no Stage 2 requests")
    for line in cbbd_client.REQUEST_LOG.read_text().splitlines():
        e = json.loads(line)
        p = e["params"]
        assert str(p.get("season")) not in ("2026", "2025-26"), e["request_key"]
        assert p.get("year") is None or int(p["year"]) <= 2024, e["request_key"]
        if "date" in p:
            assert not ("2025-08-01" <= p["date"] <= "2026-07-31"), e["request_key"]
        assert e.get("season") is None or e["season"] <= 2025, e["request_key"]


# =========================================================================== 3. raw payloads are immutable
def _cbbd_records():
    return raw_store.records("cbbd")


def test_every_cbbd_raw_payload_is_read_only_and_hash_consistent():
    recs = _cbbd_records()
    assert recs
    for path, meta in recs:
        assert not os.access(path, os.W_OK), path
        data = path.read_bytes()
        if meta.get("encoding") == "gzip":
            assert hashlib.sha256(data).hexdigest() == meta["stored_sha256"], path
        else:
            assert hashlib.sha256(data).hexdigest() == meta["sha256"], path
    # full decompression check on a deterministic sample of compressed payloads (original-bytes hash)
    gz = sorted((p, m) for p, m in recs if m.get("encoding") == "gzip")
    for path, meta in gz[:: max(1, len(gz) // 40)]:
        assert hashlib.sha256(raw_store.read_payload(path)).hexdigest() == meta["sha256"], path
    assert not list(C.RAW.rglob("*.partial"))


def test_gzip_store_is_deterministic_and_hash_is_of_original_bytes(tmp_path):
    body = b'[{"id": 1}]'
    a = raw_store.store("cbbd", "plays/date", body, params={"date": "2025-01-01"}, root=tmp_path, compress=True)
    b = raw_store.store("cbbd", "plays/date", body, params={"date": "2025-01-01"}, root=tmp_path, compress=True)
    assert a.created and not b.created and a.path == b.path
    assert a.sha256 == hashlib.sha256(body).hexdigest() and raw_store.read_payload(a.path) == body
    assert gzip.compress(body, compresslevel=6, mtime=0) == a.path.read_bytes()
    with pytest.raises(PermissionError):
        a.path.write_bytes(b"x")


# =========================================================================== 4. resumable / idempotent backfill
def _client(tmp_path, monkeypatch, responder):
    monkeypatch.setenv("CBBD_API_KEY", "k" * 20)
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return responder(url, params)
    monkeypatch.setattr(source_fetch.requests, "get", get)
    c = cbbd_client.Client(log_path=tmp_path / "log.jsonl", raw_root=tmp_path / "raw",
                           known_game_season=lambda g: 2025, sleep=lambda s: None, min_interval=0)
    return c, calls


def test_backfill_resumes_without_refetching(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, lambda u, p: _Resp([{"gameId": 1, "id": 1, "season": 2025}]))
    jobs = [("plays/date", {"date": f"2025-01-{d:02d}"}, {"purpose": "t", "season": 2025}) for d in range(1, 6)]
    c.run_many(jobs[:3], workers=2)
    assert len(calls) == 3
    c2, calls2 = _client(tmp_path, monkeypatch, lambda u, p: _Resp([{"gameId": 1, "id": 1, "season": 2025}]))
    out = c2.run_many(jobs, workers=2)                          # "restart": only the two missing requests run
    assert len(calls2) == 2 and all(o["status"] in ("STORED", "ALREADY_STORED") for o in out)
    payloads = [p for p in (tmp_path / "raw").rglob("*.json.gz")]
    assert len(payloads) == 5


def test_failed_requests_are_retried_bounded_and_never_counted_as_done(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, lambda u, p: _Resp({"error": "x"}, status=503))
    e = c.fetch("plays/date", {"date": "2025-01-02"}, purpose="t")
    assert e["status"] == "FAILED_RETRIES_EXHAUSTED" and len(calls) == cbbd_client.MAX_RETRIES + 1
    assert not c.is_done("plays/date", {"date": "2025-01-02"})
    c2, calls2 = _client(tmp_path, monkeypatch, lambda u, p: _Resp([]))
    e2 = c2.fetch("plays/date", {"date": "2025-01-02"}, purpose="t")
    assert e2["status"] == "STORED" and e2["empty"] is True and len(calls2) == 1


def test_429_honours_retry_after_and_404_is_permanent(tmp_path, monkeypatch):
    seq = iter([_Resp({}, 429, {"Retry-After": "7"}), _Resp([{"id": 1}])])
    c, calls = _client(tmp_path, monkeypatch, lambda u, p: next(seq))
    assert c.fetch("plays/date", {"date": "2025-01-03"}, purpose="t")["status"] == "STORED"
    assert c.stats["http_429"] == 1 and c.stats["retries"] == 1
    c2, _ = _client(tmp_path, monkeypatch, lambda u, p: _Resp({}, 404))
    assert c2.fetch("plays/date", {"date": "2025-01-04"}, purpose="t")["status"] == "FAILED_PERMANENT"


def test_quota_reserve_stops_the_backfill(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, lambda u, p: _Resp([], headers={"X-CallLimit-Remaining": "10"}))
    c.quota_reserve = 100
    c.fetch("plays/date", {"date": "2025-01-05"}, purpose="t")
    with pytest.raises(cbbd_client.QuotaReserveReached):
        c.fetch("plays/date", {"date": "2025-01-06"}, purpose="t")
    assert len(calls) == 1


def test_credential_with_control_characters_blocks_the_backfill(tmp_path, monkeypatch):
    monkeypatch.setenv("CBBD_API_KEY", "abc\x1b[Bdef123456")
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: pytest.fail("network call attempted"))
    c = cbbd_client.Client(log_path=tmp_path / "l.jsonl", raw_root=tmp_path / "raw", known_game_season=lambda g: 2025)
    with pytest.raises(RuntimeError, match="BLOCKED_CREDENTIAL_FORMAT"):
        c.fetch("plays/date", {"date": "2025-01-07"}, purpose="t")


def test_request_log_and_manifest_hold_no_credentials():
    secret_names = ("Authorization", "Bearer", "CBBD_API_KEY")
    values = [v.encode() for v in __import__("cbb_platform.credentials", fromlist=["x"]).present_values()]
    for p in (cbbd_client.REQUEST_LOG, cbbd_client.MANIFEST):
        if p.exists():
            b = p.read_bytes()
            assert not any(s.encode() in b for s in secret_names), p
            assert not any(v in b for v in values), p
    for meta in C.RAW.glob("cbbd/*/*.meta.json"):
        b = meta.read_bytes()
        assert b"Authorization" not in b and not any(v in b for v in values), meta


# =========================================================================== 5. duplicate payloads never duplicate events
SYN = [{"id": 11, "gameId": 999002, "period": 1, "clock": "19:00", "secondsRemaining": 1140, "homeScore": 0, "awayScore": 0,
        "playType": "SYNTHETIC", "onFloor": [], "participants": []},
       {"id": 12, "gameId": 999002, "period": 1, "clock": "18:00", "secondsRemaining": 1080, "homeScore": 2, "awayScore": 0,
        "playType": "SYNTHETIC", "scoringPlay": True, "scoreValue": 2, "onFloor": [], "participants": []}]


def test_duplicate_payloads_do_not_duplicate_canonical_events(empty_db, raw_root):
    raw_store.store("cbbd", "plays/game", json.dumps(SYN).encode(), params={"gameId": 999002})
    raw_store.store("cbbd", "plays/date", json.dumps(SYN).encode(), params={"date": "2025-01-01"}, compress=True)
    raw_store.store("cbbd", "plays/date", json.dumps(SYN[:1]).encode(), params={"date": "2025-01-02"}, compress=True)
    cbbd.normalize_all(empty_db)
    assert scalar(empty_db, "SELECT count(*) FROM fact.plays") == 2
    assert empty_db.execute("SELECT source_play_id, duplicate_copies FROM fact.plays ORDER BY 1").fetchall() == [("11", 3), ("12", 2)]
    # the kept copy is the bulk backfill payload (deterministic preference)
    assert scalar(empty_db, "SELECT count(*) FROM fact.plays WHERE source_file LIKE 'cbbd/plays_date/%'") == 2


def test_conflicting_duplicate_event_is_a_defect(empty_db, raw_root):
    changed = json.loads(json.dumps(SYN))
    changed[1]["homeScore"] = 3
    raw_store.store("cbbd", "plays/game", json.dumps(SYN).encode(), params={"gameId": 999002})
    raw_store.store("cbbd", "plays/date", json.dumps(changed).encode(), params={"date": "2025-01-01"})
    cbbd.normalize_all(empty_db)
    assert scalar(empty_db, "SELECT count(*) FROM qa.source_defects WHERE reason_code = 'CONFLICTING_DUPLICATE_EVENT'") == 1


def test_real_event_table_dedup_and_copies(db):
    assert scalar(db, "SELECT count(*) - count(DISTINCT (canonical_game_id, source_play_id)) FROM fact.plays") == 0
    assert scalar(db, "SELECT count(*) FROM fact.plays WHERE duplicate_copies > 1") > 0     # copies existed and collapsed


# =========================================================================== 6. event ids unique within scope
def test_event_ids_unique(db):
    assert scalar(db, "SELECT count(*) - count(DISTINCT source_play_id) FROM fact.plays") == 0       # CBBD ids are global
    assert scalar(db, "SELECT count(*) - count(DISTINCT (canonical_game_id, source_play_id)) FROM fact.shots") == 0
    assert scalar(db, "SELECT count(*) - count(DISTINCT (canonical_game_id, source_play_id, floor_order)) FROM fact.play_on_floor") == 0
    assert scalar(db, "SELECT count(*) - count(DISTINCT (canonical_game_id, source_athlete_id, stint_number)) FROM fact.substitutions") == 0


# =========================================================================== 7. athleteId resolves consistently
def test_athlete_id_maps_to_one_canonical_id_everywhere(db):
    for t, a, c in (("fact.play_on_floor", "source_athlete_id", "canonical_player_id"),
                    ("fact.play_participants", "source_athlete_id", "canonical_player_id"),
                    ("fact.substitutions", "source_athlete_id", "canonical_player_id"),
                    ("fact.shots", "shooter_source_athlete_id", "shooter_player_id"),
                    ("core.rosters", "source_athlete_id", "canonical_player_id")):
        # Stage 3.1: the ONLY permitted deviation from cid(athleteId) is an adjudicated SPLIT target
        splits = "SELECT to_canonical_player_id FROM metadata.player_identity_resolution WHERE rule_type = 'SPLIT'"
        split_ids = "SELECT provider_athlete_id FROM metadata.player_identity_resolution WHERE rule_type = 'SPLIT'"
        assert scalar(db, f"""SELECT count(*) FROM {t} WHERE {c} IS NOT NULL
                              AND {c} <> 'P-' || substr(sha256('CBBD|athlete|' || {a}), 1, 16)
                              AND NOT ({a} IN ({split_ids}) AND {c} IN ({splits}))""") == 0, t
        assert scalar(db, f"""SELECT count(*) FROM (SELECT {a} FROM {t} WHERE {c} IS NOT NULL AND {a} NOT IN ({split_ids})
                              GROUP BY 1 HAVING count(DISTINCT {c}) > 1)""") == 0, t


def test_existing_canonical_player_ids_were_not_altered(db):
    """Stage 1 anchors are untouched: every season-file athlete keeps its Stage 1 id and source."""
    assert scalar(db, """SELECT count(*) FROM metadata.player_id_map WHERE provider = 'CBBD'
                         AND canonical_player_id <> 'P-' || substr(sha256('CBBD|athlete|' || provider_player_key), 1, 16)""") == 0
    assert scalar(db, "SELECT count(*) FROM core.players WHERE source_system = 'CBBD_STARTER_PACK'") > 0
    assert scalar(db, "SELECT count(*) - count(DISTINCT canonical_player_id) FROM core.players") == 0


# =========================================================================== 8/9. onFloor validity and quarantine
def test_valid_onfloor_has_five_distinct_players_per_side(db):
    assert scalar(db, """SELECT count(*) FROM (
        SELECT f.canonical_game_id, f.source_play_id, f.side, count(*) n, count(DISTINCT f.source_athlete_id) d
        FROM fact.play_on_floor f JOIN fact.plays p USING (canonical_game_id, source_play_id)
        WHERE p.on_floor_quality = 'VALID_5V5' GROUP BY 1, 2, 3) WHERE n <> 5 OR d <> 5 OR side NOT IN ('HOME', 'AWAY')""") == 0


def test_invalid_onfloor_is_quarantined_not_repaired(db):
    # raw preserved: one normalized row per supplied entry, for every play (valid or not)
    assert scalar(db, """SELECT count(*) FROM fact.plays p WHERE n_on_floor <> (SELECT count(*) FROM fact.play_on_floor f
                         WHERE f.canonical_game_id = p.canonical_game_id AND f.source_play_id = p.source_play_id)
                         AND p.season >= 2024""") == 0
    assert scalar(db, """SELECT count(*) FROM fact.plays p WHERE on_floor_quality NOT IN ('VALID_5V5', 'NO_ONFLOOR')
                         AND NOT EXISTS (SELECT 1 FROM qa.source_defects d WHERE d.family = 'ONFLOOR' AND d.excluded_from_trusted
                         AND d.record_key = 'gameId=' || p.source_game_id || ';playId=' || p.source_play_id)""") == 0
    assert scalar(db, """SELECT count(*) FROM flagship.on_floor_trusted f JOIN fact.plays p USING (canonical_game_id, source_play_id)
                         WHERE p.on_floor_quality <> 'VALID_5V5'""") == 0
    assert scalar(db, "SELECT count(*) FROM fact.plays WHERE on_floor_quality NOT IN ('VALID_5V5', 'NO_ONFLOOR')") > 0


@pytest.mark.parametrize("floor,cls", [
    ([{"id": i, "team": "H"} for i in range(5)] + [{"id": 10 + i, "team": "A"} for i in range(5)], "VALID_5V5"),
    ([{"id": i, "team": "H"} for i in range(6)] + [{"id": 10 + i, "team": "A"} for i in range(5)], "MORE_THAN_5"),
    ([{"id": i, "team": "H"} for i in range(4)] + [{"id": 10 + i, "team": "A"} for i in range(5)], "LESS_THAN_5"),
    ([{"id": 10 + i, "team": "A"} for i in range(5)], "MISSING_HOME"),
    ([{"id": i, "team": "H"} for i in range(5)], "MISSING_AWAY"),
    ([{"id": 1, "team": "H"}, {"id": 1, "team": "H"}] + [{"id": 10 + i, "team": "A"} for i in range(5)], "DUPLICATE_PLAYER"),
    ([{"id": 1, "team": "H"}, {"id": 1, "team": "A"}], "PLAYER_ON_BOTH_TEAMS"),
    ([{"id": None, "team": "H"}], "UNRESOLVED_PLAYER"),
    ([{"id": 1, "team": "Z"}], "OTHER_INVALID"),
    ([], "NO_ONFLOOR"),
])
def test_onfloor_class_rules(floor, cls):
    assert M.onfloor_class(floor, "H", "A") == cls


def test_sql_onfloor_classes_agree_with_python_rules(db):
    """The SQL classifier in event_build reproduces event_metrics.onfloor_class on real plays."""
    rows = db.execute("""
        SELECT p.canonical_game_id, p.source_play_id, p.on_floor_quality,
               list(struct_pack(id := TRY_CAST(f.source_athlete_id AS BIGINT), team := f.source_team_name,
                                known := f.canonical_player_id IS NOT NULL) ORDER BY f.floor_order) AS fl
        FROM (SELECT * FROM fact.plays WHERE on_floor_quality <> 'NO_ONFLOOR' USING SAMPLE 3000 ROWS (reservoir, 7)) p
        JOIN fact.play_on_floor f USING (canonical_game_id, source_play_id) GROUP BY 1, 2, 3""").fetchall()
    for gid, pid, q, fl in rows:
        home = db.execute("""SELECT any_value(source_team_name) FROM fact.play_on_floor WHERE canonical_game_id = ? AND side = 'HOME'""", [gid]).fetchone()[0]
        away = db.execute("""SELECT any_value(source_team_name) FROM fact.play_on_floor WHERE canonical_game_id = ? AND side = 'AWAY'""", [gid]).fetchone()[0]
        known = {e["id"] for e in fl if e["known"]}
        assert M.onfloor_class([{"id": e["id"], "team": e["team"]} for e in fl], home, away, known) == q, (gid, pid)


# =========================================================================== 10/11. shots
def test_trusted_shots_have_their_shooter_on_the_floor(db):
    assert scalar(db, "SELECT count(*) FROM flagship.shots_trusted WHERE shooter_on_team_floor = FALSE") == 0
    chk, ok = db.execute("SELECT count(*) FILTER (WHERE shooter_on_team_floor IS NOT NULL), count(*) FILTER (WHERE shooter_on_team_floor) FROM fact.shots").fetchone()
    if chk:
        assert ok / chk >= 0.95       # measured; residual = same-timestamp substitution semantics, quarantined


def test_assisted_shots_resolve_the_assister_where_supplied(db):
    # Stage 3.1: rows governed by an adjudicated QUARANTINE rule are deliberately unresolved
    assert scalar(db, """SELECT count(*) FROM fact.shots WHERE assister_source_athlete_id IS NOT NULL
                         AND assister_player_id IS NULL AND assister_source_athlete_id IN
                         (SELECT provider_player_key FROM metadata.player_id_map WHERE provider = 'CBBD')
                         AND assister_source_athlete_id NOT IN (SELECT provider_athlete_id FROM metadata.player_identity_resolution
                                                                WHERE rule_type = 'QUARANTINE')""") == 0
    assert scalar(db, """SELECT count(*) FROM fact.shots WHERE assisted AND assister_source_athlete_id IS NULL
                         AND NOT EXISTS (SELECT 1 FROM qa.source_defects d WHERE d.reason_code = 'ASSISTED_WITHOUT_ASSISTER'
                         AND d.record_key LIKE '%;playId=' || source_play_id)""") == 0


def test_no_shot_coordinates_are_invented(db):
    assert scalar(db, "SELECT count(*) FROM fact.shots WHERE (location_x IS NULL) <> (location_y IS NULL)") == 0
    assert scalar(db, "SELECT count(*) FROM fact.shots WHERE location_x IS NULL AND location_valid IS NOT NULL") == 0


# =========================================================================== 12. lineups
def test_lineup_units_have_exactly_five_distinct_athletes(db):
    assert scalar(db, """SELECT count(*) FROM fact.lineup_stats WHERE len(source_athlete_ids) <> 5
                         OR len(list_distinct(source_athlete_ids)) <> 5 OR list_contains(source_athlete_ids, NULL)""") == 0
    assert scalar(db, """SELECT count(*) FROM qa.lineup_units_quarantine q JOIN fact.lineup_stats l
                         USING (canonical_game_id, canonical_team_id, lineup_hash)""") == 0
    assert scalar(db, "SELECT count(*) FROM fact.lineup_stints") == 0          # never manufactured from aggregates


# =========================================================================== 13. substitutions
def test_substitution_ids_resolve_or_are_flagged(db):
    n, res = db.execute("SELECT count(*), count(canonical_player_id) FROM fact.substitutions").fetchone()
    assert n > 0 and res / n >= 0.99
    assert n - res == scalar(db, "SELECT count(*) FROM qa.source_defects WHERE reason_code = 'SUB_ATHLETE_UNRESOLVED'")


def test_derived_pairs_never_force_ambiguous_matches(db):
    assert scalar(db, """SELECT count(*) FROM fact.substitution_pairs WHERE ambiguous_flag
                         AND (player_out_id IS NOT NULL OR player_in_id IS NOT NULL OR pairing_confidence > 0)""") == 0
    assert scalar(db, """SELECT count(*) FROM fact.substitution_pairs WHERE NOT ambiguous_flag
                         AND (len(out_source_athlete_ids) <> 1 OR len(in_source_athlete_ids) <> 1)""") == 0


# =========================================================================== 14. PBP score reconciliation
def test_pbp_reconciliation_is_complete_and_flagged(db):
    assert scalar(db, "SELECT count(DISTINCT canonical_game_id) FROM fact.plays") == scalar(db, "SELECT count(*) FROM qa.pbp_game_reconciliation")
    assert scalar(db, """SELECT count(*) FROM qa.pbp_game_reconciliation WHERE coverage_quality LIKE 'RECONCILED%'
                         AND NOT (pbp_final_home = official_home AND pbp_final_away = official_away)""") == 0
    assert scalar(db, """SELECT count(*) FROM qa.pbp_game_reconciliation r WHERE coverage_quality = 'FINAL_SCORE_MISMATCH'
                         AND NOT EXISTS (SELECT 1 FROM qa.source_defects d WHERE d.reason_code = 'FINAL_SCORE_MISMATCH'
                         AND d.canonical_game_id = r.canonical_game_id AND d.excluded_from_trusted)""") == 0
    assert scalar(db, """SELECT count(*) FROM flagship.plays p JOIN qa.pbp_game_reconciliation r USING (canonical_game_id)
                         WHERE r.coverage_quality IN ('FINAL_SCORE_MISMATCH', 'INCOMPLETE_PERIODS', 'GAME_NOT_IN_CANONICAL')""") == 0


def test_official_scores_are_never_modified_by_pbp(db):
    assert scalar(db, """SELECT count(*) FROM qa.pbp_game_reconciliation r JOIN core.games g USING (canonical_game_id)
                         WHERE r.official_home IS DISTINCT FROM g.home_points OR r.official_away IS DISTINCT FROM g.away_points""") == 0


# =========================================================================== 15. game identity
def test_duplicate_game_candidates_are_surfaced():
    rows = (C.QA / "game_identity_candidates.csv").read_text()
    res = json.loads((C.QA / "game_identity_audit.json").read_text())
    assert res["by_class"].get("TRUE_DUPLICATE", 0) > 0 and res["remediation_performed"] is False
    assert "77823" in rows and "77802" in rows                          # Yale v Central Connecticut, 2011-11-11


def test_game_identity_classifier():
    base = dict(same_orientation=True, same_tip=True, home_pts_a=70, away_pts_a=60, home_pts_b=70, away_pts_b=60,
                venue_a="V", venue_b="V", team_box_rows_a=2, team_box_rows_b=0, player_box_rows_a=20, player_box_rows_b=0,
                date_a="2012-01-01", date_b="2012-01-01")
    assert GI.classify(base)[0] == "TRUE_DUPLICATE"
    assert GI.classify({**base, "team_box_rows_b": 2})[0] != "TRUE_DUPLICATE"
    assert GI.classify({**base, "home_pts_b": 0, "away_pts_b": 0})[0] == "POSSIBLE_DUPLICATE"
    assert GI.classify({**base, "same_tip": False, "date_b": "2012-01-02", "team_box_rows_b": 2})[0] == "LEGITIMATE_DISTINCT_GAME"


# =========================================================================== 16. wallclock never trusted as UTC
def test_wallclock_is_never_a_trusted_timestamp(db):
    cols = db.execute("""SELECT table_schema || '.' || table_name || '.' || column_name, data_type FROM information_schema.columns
                         WHERE lower(column_name) LIKE '%wallclock%'""").fetchall()
    assert cols and all(t == "VARCHAR" for _, t in cols), cols
    res = json.loads(W.OUT_JSON.read_text())
    assert res["approved_for_pit_or_latency"] is False
    pit_src = (C.PROJECT_ROOT / "cbb_platform" / "pit.py").read_text()
    assert "wallclock" not in pit_src


def test_wallclock_residuals_math():
    from datetime import datetime, timezone
    tip = datetime(2025, 1, 10, 0, 0, tzinfo=timezone.utc)                   # 19:00 EST
    r = W.residuals("2025-01-09T19:05:00.000Z", tip, "America/Chicago")
    assert W.consistent(r) == {"EASTERN_TIME_MISLABELED_Z"}
    assert W.consistent(W.residuals("2025-01-10T00:05:00.000Z", tip, "America/Chicago")) == {"UTC"}
    assert W.consistent(W.residuals("2025-01-09T18:05:00.000Z", tip, "America/Chicago")) == {"LOCAL_VENUE_TIME_MISLABELED_Z"}


# =========================================================================== 17. sportsbook isolation (new views)
def test_new_flagship_views_never_touch_benchmark(db):
    for name, sql in db.execute("SELECT view_name, sql FROM duckdb_views() WHERE schema_name = 'flagship'").fetchall():
        low = sql.lower()
        assert "benchmark." not in low and "odds" not in low and "spread" not in low and "moneyline" not in low, name
    names = {r[0] for r in db.execute("SELECT view_name FROM duckdb_views() WHERE schema_name = 'flagship'").fetchall()}
    assert {"plays", "on_floor_trusted", "shots_trusted", "substitutions", "lineup_stats", "rosters"} <= names


# =========================================================================== 18. Sharp Stats snapshots append
def test_sharp_stats_snapshots_append_never_overwrite(empty_db, raw_root):
    a = availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v1.html"), synthetic=True)
    b = availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v2.html"), synthetic=True)
    again = availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v1.html"), synthetic=True)
    assert a.created and b.created and not again.created and a.path.exists() and b.path.exists()
    availability.normalize_all(empty_db)
    assert scalar(empty_db, "SELECT count(DISTINCT snapshot_sha256) FROM availability.events") == 2
    assert scalar(empty_db, "SELECT count(*) FROM availability.events WHERE team_match_confidence NOT IN (0.0, 1.0)") == 0


# =========================================================================== 19/20. Starter Pack + workspace untouched
def test_starter_pack_byte_identical():
    from cbb_platform.audit_starter_pack import starter_pack_manifest
    recorded = json.loads((C.MANIFESTS / "starter_pack_sha256.json").read_text())["files"]
    data = lambda m: {k: v for k, v in m.items() if not k.endswith(".DS_Store")}
    assert data(starter_pack_manifest()) == data(recorded)


# Sibling-workspace immutability is asserted against the CURRENT accepted task baseline. Older baselines are kept
# unchanged as historical evidence, and each successor must account for every difference from its predecessor.
WORKSPACE_BASELINES = ("workspace_snapshot_stage2.json", "workspace_snapshot_pre_model_closure_v1.json")
STAGE2_WORKSPACE_SNAPSHOT_SHA256 = "e040328548bc4aa82a9af82aef5ac1d7346b65174ab4e79ea86c11b87ac1fe98"


def test_workspace_baseline_history_is_preserved_and_drift_is_accounted():
    old_p = C.MANIFESTS / WORKSPACE_BASELINES[0]
    assert hashlib.sha256(old_p.read_bytes()).hexdigest() == STAGE2_WORKSPACE_SNAPSHOT_SHA256   # history never rewritten
    for prev_name, cur_name in zip(WORKSPACE_BASELINES, WORKSPACE_BASELINES[1:]):
        prev_p, cur = C.MANIFESTS / prev_name, json.loads((C.MANIFESTS / cur_name).read_text())
        prev = json.loads(prev_p.read_text())
        assert cur["supersedes"]["file"] == prev_name
        assert cur["supersedes"]["sha256"] == hashlib.sha256(prev_p.read_bytes()).hexdigest()
        diff = {n for n in set(prev["entries"]) | set(cur["entries"]) if prev["entries"].get(n) != cur["entries"].get(n)}
        ledger = {d["entry"]: d for d in cur["drift_from_superseded"]}
        assert set(ledger) == diff, cur_name                                    # every difference recorded, nothing extra
        for n, d in ledger.items():
            assert (d["stage2"], d["baseline"]) == (prev["entries"].get(n), cur["entries"].get(n)), n
            assert d["classification"] == "PRE_EXISTING_WORKSPACE_DRIFT", n


def test_no_unrelated_project_was_modified():
    snap = json.loads((C.MANIFESTS / WORKSPACE_BASELINES[-1]).read_text())
    parent = Path(snap["parent"])
    for name, s in snap["entries"].items():
        p = parent / name
        assert p.exists(), name
        if s["type"] == "file":
            assert (p.stat().st_size, p.stat().st_mtime_ns) == (s["size"], s["mtime_ns"]), name
        else:
            entries = [q for q in p.rglob("*") if q.name != ".DS_Store"]
            assert len(entries) == s["entries"], name
            assert max(q.lstat().st_mtime_ns for q in entries) == s["newest_mtime_ns"], name
    extra = {p.name for p in parent.iterdir()} - set(snap["entries"]) - {"CBB_Originator_Model", ".DS_Store"}
    assert not {x for x in extra if not x.startswith("~$")}, extra


# =========================================================================== 21. deterministic rebuild
# (tests/test_reproducibility.py::test_rebuild_from_inputs_is_deterministic rebuilds everything, event layer
#  included, from the immutable inputs and compares every table fingerprint.)
def test_dataset_manifest_matches_database(db):
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    current = dict(db.execute("""SELECT table_name, content_fingerprint FROM metadata.data_versions
                                 WHERE build_id = (SELECT max(build_id) FROM metadata.data_versions)""").fetchall())
    assert {t: v["fingerprint"] for t, v in man["canonical_tables"].items()} == current
    assert man["excluded_lockbox_season"] == "2025-26" and 2026 not in man["included_seasons"]


def test_no_predictive_model_artifacts():
    bad = [p for p in C.PROJECT_ROOT.rglob("*") if ".venv" not in p.parts and p.suffix in (".pkl", ".joblib", ".onnx", ".pt", ".cbm", ".lgb")]
    assert bad == []
