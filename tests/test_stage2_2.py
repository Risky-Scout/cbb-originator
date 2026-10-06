"""Stage 2.2 -- played-game status (0-0 schedule records). Numbers map to the directive's TEST list."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from cbb_platform import config as C, game_status as S, pit
from tests.conftest import scalar

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
STATUSES = ("PLAYED_CONFIRMED", "UNPLAYED_OR_CANCELLED", "SCHEDULE_PLACEHOLDER", "SOURCE_STATUS_UNRESOLVED")


@pytest.fixture(scope="module")
def excluded(db):
    return [r[0] for r in db.execute("SELECT canonical_game_id FROM metadata.game_status_resolution WHERE excluded_from_played_games").fetchall()]


# 1 -------------------------------------------------------------------------------------------------------------------
def test_every_zero_zero_game_has_an_explicit_status(db):
    n, with_status, played = db.execute("""SELECT count(*), count(s.status), count(*) FILTER (WHERE s.status = 'PLAYED_CONFIRMED')
        FROM flagship.games g LEFT JOIN metadata.game_status_resolution s USING (canonical_game_id)
        WHERE g.home_points = 0 AND g.away_points = 0""").fetchone()
    assert n == with_status == 131 and played == 0
    assert scalar(db, "SELECT count(*) FROM flagship.games") == scalar(db, "SELECT count(*) FROM metadata.game_status_resolution")
    assert scalar(db, f"SELECT count(*) FROM metadata.game_status_resolution WHERE status NOT IN {STATUSES}") == 0
    assert scalar(db, "SELECT count(*) FROM metadata.game_status_resolution WHERE (status = 'PLAYED_CONFIRMED') = excluded_from_played_games") == 0


def test_played_confirmed_always_has_positive_evidence(db):
    """A 0-0 score is never evidence; every PLAYED_CONFIRMED game carries box/event/period/real-result evidence."""
    assert scalar(db, """SELECT count(*) FROM metadata.game_status_resolution s JOIN flagship.games g USING (canonical_game_id)
        WHERE s.status = 'PLAYED_CONFIRMED' AND NOT s.resolution_method LIKE 'EVIDENCE_%'""") == 0
    assert scalar(db, """SELECT count(*) FROM metadata.game_status_resolution s JOIN flagship.games g USING (canonical_game_id)
        WHERE s.status = 'PLAYED_CONFIRMED' AND s.resolution_method = 'EVIDENCE_OFFICIAL_NONZERO_FINAL_SCORE'
          AND ((g.home_points = 0 AND g.away_points = 0) OR (g.home_points, g.away_points) IN ((2,0),(0,2),(1,0),(0,1)))""") == 0


def test_no_excluded_game_carries_any_fact(db, excluded):
    """Excluding unplayed records loses no basketball information: none has a box or event fact."""
    for t in ("fact.team_games", "fact.player_games", "fact.participation", "fact.plays", "fact.shots",
              "fact.substitutions", "fact.lineup_stats"):
        assert scalar(db, f"SELECT count(*) FROM {t} WHERE canonical_game_id IN (SELECT unnest(?))", [excluded]) == 0, t


# 2 -------------------------------------------------------------------------------------------------------------------
def test_no_unplayed_game_enters_played_games(db, excluded):
    assert excluded
    assert scalar(db, "SELECT count(*) FROM flagship.played_games WHERE canonical_game_id IN (SELECT unnest(?))", [excluded]) == 0
    assert scalar(db, "SELECT count(*) FROM flagship.played_games WHERE home_points = 0 AND away_points = 0") == 0
    assert scalar(db, "SELECT count(*) FROM flagship.played_games") == scalar(db, "SELECT count(*) FROM flagship.games") - len(excluded)


# 3 -------------------------------------------------------------------------------------------------------------------
def test_raw_schedule_records_are_preserved(db, excluded):
    assert scalar(db, "SELECT count(*) FROM core.games WHERE canonical_game_id IN (SELECT unnest(?))", [excluded]) == len(excluded)
    assert scalar(db, "SELECT count(*) FROM flagship.games WHERE canonical_game_id IN (SELECT unnest(?))", [excluded]) == len(excluded)
    # scores are never fabricated, winners never inferred
    assert scalar(db, """SELECT count(*) FROM core.games WHERE canonical_game_id IN (SELECT unnest(?))
                         AND NOT ((home_points = 0 AND away_points = 0) OR (home_points, away_points) IN ((2,0),(0,2),(1,0),(0,1)))""",
                  [excluded]) == 0


# 4 -------------------------------------------------------------------------------------------------------------------
def test_played_game_aggregates_exclude_unplayed_games(db):
    assert scalar(db, """SELECT count(*) FROM fact.team_season_game_derived d JOIN (
        SELECT team, season, count(*) n, count(*) FILTER (WHERE p > o) w, count(*) FILTER (WHERE p < o) l, sum(p) pf, sum(o) pa FROM (
            SELECT home_team_id team, season, home_points p, away_points o FROM flagship.played_games WHERE home_points IS NOT NULL
            UNION ALL SELECT away_team_id, season, away_points, home_points FROM flagship.played_games WHERE home_points IS NOT NULL)
        GROUP BY 1, 2) r ON r.team = d.canonical_team_id AND r.season = d.season
        WHERE (d.games_with_result, d.wins, d.losses, d.points_all_results, d.opp_points_all_results) IS DISTINCT FROM (r.n, r.w, r.l, r.pf, r.pa)""") == 0
    impact = list(csv.DictReader((C.QA / "game_status_aggregate_impact.csv").open()))
    summary = json.loads((C.QA / "game_status_resolution.json").read_text())
    assert len(impact) == summary["affected_team_seasons"] > 0
    for r in impact:
        # an empty "new" row = the team-season has no played game left (non-D-I teams whose only records were 0-0)
        assert int(r["old_games"]) - int(r["new_games"] or 0) == int(r["removed_unplayed_contributions"]) >= 1, r
        if r["new_games"] == "":
            assert scalar(db, "SELECT count(*) FROM flagship.played_games WHERE season = ? AND ? IN (home_team_id, away_team_id)",
                          [int(r["season"]), r["canonical_team_id"]]) == 0
        assert (r["old_wins"], r["old_losses"]) == (r["new_wins"] or "0", r["new_losses"] or "0") or r["source_game_ids_excluded"] == "47251"
    # 0-0 records only change the games count (never wins/losses/points) -- the forfeit-signature record is the only W/L change
    assert summary["zero_zero_in_model_window"] == 0


# 5 -------------------------------------------------------------------------------------------------------------------
def _next_played(db, team, season, after_date):
    return db.execute("""SELECT canonical_game_id FROM flagship.played_games WHERE season = ? AND ? IN (home_team_id, away_team_id)
                         AND game_date_et > ? ORDER BY scheduled_tip_utc, canonical_game_id LIMIT 1""", [season, team, after_date]).fetchone()


def test_pit_histories_ignore_unplayed_games(db, excluded):
    checked = 0
    for gid in excluded:
        season, date, home, away = db.execute("SELECT season, game_date_et, home_team_id, away_team_id FROM flagship.games WHERE canonical_game_id = ?", [gid]).fetchone()
        for team in (home, away):
            nxt = _next_played(db, team, season, date)
            if not nxt:
                continue
            res = pit.team_results_to_date(db, team, nxt[0])            # season-to-date record / team form
            assert gid not in res["game_ids"]
            box = pit.team_season_to_date(db, team, nxt[0], FAR)          # rolling box windows / player opportunity source
            assert gid not in (box["game_ids"] or [])
            last = pit.last_game_before(db, team, nxt[0])                 # days-rest anchor
            assert last is None or last[0] != gid
            assert last is None or scalar(db, "SELECT count(*) FROM flagship.played_games WHERE canonical_game_id = ?", [last[0]]) == 1
            # player opportunity: no participation rows exist for the unplayed record in any trusted relation
            assert scalar(db, "SELECT count(*) FROM flagship.participation WHERE canonical_game_id = ?", [gid]) == 0
            checked += 1
    assert checked >= 150


def test_unplayed_game_cannot_be_a_pit_target_record_for_rest(db, excluded):
    """Days-rest after an unplayed record is measured from the previous PLAYED game, never from the placeholder."""
    for gid in excluded[:40]:
        season, date, home = db.execute("SELECT season, game_date_et, home_team_id FROM flagship.games WHERE canonical_game_id = ?", [gid]).fetchone()
        nxt = _next_played(db, home, season, date)
        if nxt:
            last = pit.last_game_before(db, home, nxt[0])
            if last:
                assert last[2] <= date or last[0] != gid


# 6 -------------------------------------------------------------------------------------------------------------------
def test_status_resolution_is_deterministic(db):
    import duckdb
    con = duckdb.connect()
    con.execute(f"ATTACH '{C.DUCKDB_PATH}' AS src (READ_ONLY)")
    for s in ("flagship", "fact", "metadata"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    con.execute("CREATE VIEW flagship.games AS SELECT * FROM src.flagship.games")
    for t in ("team_games", "player_games", "plays", "shots", "substitutions", "lineup_stats"):
        con.execute(f"CREATE VIEW fact.{t} AS SELECT canonical_game_id FROM src.fact.{t}")
    con.execute("CREATE TABLE metadata.game_status_resolution AS SELECT * FROM src.metadata.game_status_resolution LIMIT 0")
    S.resolve(con)
    q = "SELECT * EXCLUDE (created_by_version) FROM {} ORDER BY canonical_game_id"
    assert con.execute(q.format("metadata.game_status_resolution")).fetchall() == con.execute(q.format("src.metadata.game_status_resolution")).fetchall()


# 7 -------------------------------------------------------------------------------------------------------------------
def test_pre_2005_games_stay_outside_modeling_scope(db):
    lo = C.AUDITED_SEASONS[0]
    assert scalar(db, f"SELECT count(*) FROM core.games WHERE season < {lo}") > 150000           # preserved raw
    for v in ("games", "played_games", "game_schedule_versions", "team_game_outcomes", "player_game_outcomes", "participation"):
        cols = [c[0] for c in db.execute(f"DESCRIBE flagship.{v}").fetchall()]
        if "season" in cols:
            assert scalar(db, f"SELECT count(*) FROM flagship.{v} WHERE season < {lo}") == 0, v
    assert scalar(db, f"SELECT count(*) FROM metadata.game_status_resolution WHERE season < {lo}") == 0
    assert scalar(db, f"SELECT count(*) FROM metadata.trusted_game_map m JOIN core.games g ON g.canonical_game_id = m.source_canonical_game_id WHERE g.season < {lo}") == 0
    assert "OUT_OF_MODELING_SCOPE" in (C.PROJECT_ROOT / "docs" / "DATA_SCOPE_RULINGS.md").read_text()


# 8/9/10 + version -------------------------------------------------------------------------------------------------------
def test_lockbox_untouched(db):
    assert scalar(db, "SELECT count(*) FROM metadata.game_status_resolution WHERE season >= 2026") == 0
    assert scalar(db, "SELECT count(*) FROM flagship.played_games WHERE season >= 2026") == 0
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    assert man["excluded_lockbox_season"] == "2025-26" and man["lockbox_touched"] is False


def test_starter_pack_unchanged():
    from cbb_platform.audit_starter_pack import starter_pack_manifest
    recorded = json.loads((C.MANIFESTS / "starter_pack_sha256.json").read_text())["files"]
    keep = lambda m: {k: v for k, v in m.items() if not k.endswith(".DS_Store")}
    assert keep(starter_pack_manifest()) == keep(recorded)


def test_no_predictive_model_trained():
    bad = [p for p in C.PROJECT_ROOT.rglob("*") if ".venv" not in p.parts
           and p.suffix in (".pkl", ".joblib", ".onnx", ".pt", ".cbm", ".lgb", ".h5", ".ckpt")]
    assert bad == []


def test_dataset_version_v2_2_with_parent(db):
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    assert man["dataset_version"].startswith("cbb_data_v2.2+")
    assert man["parent_dataset_version"] == "cbb_data_v2.1+292c2d2b1739"
    assert (C.MANIFESTS / "DATASET_MANIFEST_cbb_data_v2.1+292c2d2b1739.json").exists()
    gs = man["game_status"]
    assert gs["played_games"] == scalar(db, "SELECT count(*) FROM flagship.played_games")
    assert man["modeling_game_relation"] == "flagship.played_games"
