"""Step 6 adapters, exercised with clearly labeled SYNTHETIC fixtures in a temporary raw store and
an in-memory database. Nothing here touches data/raw or the canonical database."""
import json

import pytest

from cbb_platform import raw_store
from cbb_platform.adapters import availability, bdl, cbbd
from tests.conftest import FIXTURES, scalar


# --------------------------------------------------------------------------- availability (Sharp Stats)
@pytest.mark.parametrize("raw,expected", [
    ("Probable", "PROBABLE"), ("Questionable", "QUESTIONABLE"), ("Doubtful", "DOUBTFUL"), ("Out", "OUT"),
    ("Out For Season", "OUT_SEASON"), ("out for the season", "OUT_SEASON"), ("Day-To-Day", "UNKNOWN"),
    ("", "UNKNOWN"), (None, "UNKNOWN"),
])
def test_status_normalization(raw, expected):
    assert availability.normalize_status(raw) == expected


@pytest.mark.parametrize("status,note,flag,ctype", [
    ("QUESTIONABLE", "Is not expected to play Tuesday", True, "NOTE_SAYS_OUT_STATUS_SAYS_LIKELY_AVAILABLE"),
    ("OUT", "Expected to play Saturday after full practice", True, "NOTE_SAYS_AVAILABLE_STATUS_SAYS_UNAVAILABLE"),
    ("OUT", "Underwent season-ending surgery", True, "NOTE_SAYS_OUT_SEASON_STATUS_DIFFERS"),
    ("OUT_SEASON", "Underwent season-ending surgery", False, None),
    ("PROBABLE", "Expected to play", False, None),
    ("QUESTIONABLE", None, False, None),
])
def test_conflict_rules(status, note, flag, ctype):
    assert availability.conflict(status, note) == (flag, ctype)


def _events(con):
    return con.execute("""SELECT source_team_name, source_player_name, status_raw, status_normalized, note_raw,
                                 conflict_flag, conflict_type, snapshot_sha256, is_synthetic_fixture
                          FROM availability.events ORDER BY received_at, line_number""").fetchall()


def test_sharp_stats_html_snapshot_lands_in_availability_events(empty_db, raw_root):
    availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v1.html"), synthetic=True)
    availability.normalize_all(empty_db)
    ev = _events(empty_db)
    assert len(ev) == 6
    one = ev[0]
    assert one[:4] == ("Synthetic State", "Test Player One", "Questionable", "QUESTIONABLE")
    assert one[4] == "Is not expected to play Tuesday, per coach."          # raw note preserved verbatim
    assert one[5] is True and one[6] == "NOTE_SAYS_OUT_STATUS_SAYS_LIKELY_AVAILABLE"
    assert ev[1][3] == "OUT_SEASON" and ev[1][5] is False
    assert ev[3][0] == "Synthetic Tech" and ev[3][3] == "OUT" and ev[3][5] is True
    assert ev[4][2:4] == ("Day-To-Day", "UNKNOWN")                          # unmapped status kept raw
    assert all(e[8] for e in ev)                                           # labeled synthetic
    assert {r[0] for r in empty_db.execute("SELECT DISTINCT provider FROM availability.events").fetchall()} == {"sharp_stats"}
    assert scalar(empty_db, "SELECT count(*) FROM flagship.availability_events") == 0


def test_duplicate_snapshot_is_idempotent_and_changed_snapshot_appends(empty_db, raw_root):
    v1 = FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v1.html"
    first = availability.ingest("sharp_stats", str(v1), synthetic=True)
    again = availability.ingest("sharp_stats", str(v1), synthetic=True)
    assert first.created and not again.created
    changed = availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v2.html"), synthetic=True)
    assert changed.created and changed.sha256 != first.sha256
    availability.normalize_all(empty_db)
    ev = _events(empty_db)
    assert len(ev) == 12                                                   # 6 from v1 + 6 from v2, none overwritten
    p3 = [e for e in ev if e[1] == "Test Player Three"]
    assert [e[3] for e in p3] == ["PROBABLE", "OUT"]                         # history preserved in receipt order
    assert first.path.exists() and changed.path.exists()


def test_csv_capture_format(empty_db, raw_root):
    availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_capture.csv"), synthetic=True)
    availability.normalize_all(empty_db)
    ev = _events(empty_db)
    assert [e[1] for e in ev] == ["Test Player One", "Test Player Seven"]
    assert ev[0][5] is True
    assert scalar(empty_db, "SELECT count(*) FROM availability.events WHERE source_class = 'Jr'") == 1


def test_unknown_team_and_player_stay_unresolved(empty_db, raw_root):
    availability.ingest("sharp_stats", str(FIXTURES / "SYNTHETIC_sharp_stats_snapshot_v1.html"), synthetic=True)
    availability.normalize_all(empty_db)
    assert scalar(empty_db, "SELECT count(*) FROM availability.events WHERE canonical_player_id IS NOT NULL "
                            "OR canonical_team_id IS NOT NULL") == 0
    assert scalar(empty_db, "SELECT count(DISTINCT identity_status) FROM availability.events") == 1


# --------------------------------------------------------------------------- CBBD (synthetic payload shaped per OpenAPI v1.29.0)
SYNTHETIC_PLAYS = [  # SYNTHETIC TEST FIXTURE — invented ids, not a real CBBD response
    {"id": 2, "gameId": 999001, "period": 1, "clock": "19:40", "secondsRemaining": 1180, "playType": "JumpShot",
     "teamId": 1, "homeScore": 2, "awayScore": 0, "scoringPlay": True, "shootingPlay": True, "scoreValue": 2,
     "playText": "SYNTHETIC made jumper", "wallclock": "2026-11-04T00:02:00Z",
     "participants": [{"name": "A", "id": 900001}],
     "onFloor": [{"team": "H", "name": f"h{i}", "id": 900000 + i} for i in range(1, 6)]
                + [{"team": "A", "name": f"a{i}", "id": 900100 + i} for i in range(1, 6)],
     "shotInfo": {"shooter": {"name": "A", "id": 900001}, "made": True, "range": "jumper", "assisted": False,
                  "assistedBy": {"name": None, "id": None}, "location": {"x": 10.5, "y": 20.0}}},
    {"id": 1, "gameId": 999001, "period": 1, "clock": "20:00", "secondsRemaining": 1200, "playType": "JumpBall",
     "teamId": None, "homeScore": 0, "awayScore": 0, "participants": [], "onFloor": [], "shotInfo": None},
]
SYNTHETIC_SUBS = [{"gameId": 999001, "teamId": 1, "athleteId": 900001, "subIn": {"period": 1, "secondsRemaining": 1200,
                   "teamPoints": 0, "opponentPoints": 0}, "subOut": {"period": 1, "secondsRemaining": 600,
                   "teamPoints": 12, "opponentPoints": 10}}]
SYNTHETIC_LINEUPS = [{"teamId": 1, "idHash": "abc", "athletes": [{"name": "x", "id": 900000 + i} for i in range(1, 6)],
                      "totalSeconds": 600, "teamStats": {"possessions": 10, "points": 12},
                      "opponentStats": {"possessions": 10, "points": 10}}]


def test_cbbd_normalization_of_pbp_substitutions_lineups(empty_db, raw_root):
    raw_store.store("cbbd", "plays/game", json.dumps(SYNTHETIC_PLAYS).encode(), params={"gameId": 999001})
    raw_store.store("cbbd", "substitutions/game", json.dumps(SYNTHETIC_SUBS).encode(), params={"gameId": 999001})
    raw_store.store("cbbd", "lineups/game", json.dumps(SYNTHETIC_LINEUPS).encode(), params={"gameId": 999001})
    cbbd.normalize_all(empty_db)
    plays = empty_db.execute("SELECT source_play_id, play_sequence, on_floor_player_ids FROM fact.plays "
                             "ORDER BY play_sequence").fetchall()
    assert [p[:2] for p in plays] == [("1", 1), ("2", 2)]                    # ordered by game clock
    assert len(plays[1][2]) == 10 and plays[0][2] == []                     # onFloor exactly as supplied
    assert all(x.startswith("CBBD_ATHLETE:") for x in plays[1][2])          # unmapped ids are never faked
    assert scalar(empty_db, "SELECT count(*) FROM fact.shots WHERE made AND location_x = 10.5") == 1
    assert scalar(empty_db, "SELECT stint_number FROM fact.substitutions") == 1
    assert scalar(empty_db, "SELECT len(player_ids) FROM fact.lineup_stats") == 5
    assert scalar(empty_db, "SELECT count(*) FROM fact.lineup_stints") == 0   # stints are never manufactured


def test_cbbd_fetch_without_credential_is_blocked(monkeypatch):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BLOCKED_CREDENTIAL"):
        # game 14 = a 2024-25 game, so the request passes the lockbox guard and reaches the credential check
        cbbd.fetch("plays/game", {"gameId": 14}, known_game_season=lambda gid: 2025)


# --------------------------------------------------------------------------- BDL (synthetic odds; real data has none for 2024-25)
SYNTHETIC_ODDS = {"data": [{"id": 77, "game_id": 555, "vendor": "synthetic_book", "spread_home_value": "-3.5",
                            "spread_home_odds": -110, "spread_away_value": "3.5", "spread_away_odds": -110,
                            "moneyline_home_odds": -160, "moneyline_away_odds": 135, "total_value": "141.5",
                            "total_over_odds": -108, "total_under_odds": -112, "updated_at": "2026-11-03T18:00:00Z"}],
                  "meta": {"per_page": 100}}


def test_bdl_season_normalization():
    assert bdl.bdl_to_cbbd_season(2024) == 2025   # BDL 2024 = 2024-25 = CBBD 2025


def test_bdl_odds_land_only_in_benchmark_with_timestamps(empty_db, raw_root):
    raw_store.store("bdl", "odds", json.dumps(SYNTHETIC_ODDS).encode())
    opening = json.loads(json.dumps(SYNTHETIC_ODDS))
    opening["data"][0].pop("updated_at")
    opening["data"][0]["opened_at"] = "2026-11-01T12:00:00Z"
    raw_store.store("bdl", "odds/opening", json.dumps(opening).encode())
    bdl.normalize_all(empty_db)
    assert empty_db.execute("SELECT spread_home_value, total_value, source_updated_at::VARCHAR FROM benchmark.game_odds"
                            ).fetchone() == (-3.5, 141.5, "2026-11-03 18:00:00+00")
    assert scalar(empty_db, "SELECT opened_at::VARCHAR FROM benchmark.opening_odds") == "2026-11-01 12:00:00+00"
    touched = empty_db.execute("""SELECT schema_name, table_name, estimated_size FROM duckdb_tables()
                                  WHERE schema_name IN ('core', 'fact', 'availability') AND estimated_size > 0""").fetchall()
    assert touched == []


def test_bdl_fetch_without_credential_is_blocked(monkeypatch):
    monkeypatch.delenv("BDL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BLOCKED_CREDENTIAL"):
        # BDL season 2024 = 2024-25 (development), so the request passes the global lockbox guard first
        bdl.fetch("games", {"seasons[]": [2024]})
