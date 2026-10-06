"""Stage 1 closure (architect review patch): PIT as-of aggregates, season-statistics authority,
Sharp Stats provider identity, 2025-26 lockbox, CBBD athleteId identity logic, credential hygiene."""
import json
import mmap
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from cbb_platform import cbbd_identity as ident, config as C, lockbox, pit, raw_store
from cbb_platform.adapters import availability, cbbd
from tests.conftest import scalar

UTC = timezone.utc
FAR_FUTURE = datetime(2030, 1, 1, tzinfo=UTC)


def _gid(db, cbbd_game_id):
    return scalar(db, "SELECT cid('G', 'game', ?)", [str(cbbd_game_id)])


def _tid(db, cbbd_team_id):
    return scalar(db, "SELECT cid('T', 'team', ?)", [str(cbbd_team_id)])


# =========================================================================== A. PIT as-of aggregates
def test_as_of_aggregate_contains_only_strictly_earlier_games(db):
    """Purdue 2024-25: games 14 (Nov 4) and 300 (Nov 8) precede target 471 (Nov 11)."""
    purdue, target = _tid(db, 236), _gid(db, 471)
    tip = scalar(db, "SELECT scheduled_tip_utc FROM core.games WHERE canonical_game_id = ?", [target])
    got = {r[0] for r in pit.team_games_as_of(db, purdue, target, tip - timedelta(hours=1))}
    assert got == {_gid(db, 14), _gid(db, 300)}


def test_target_and_later_games_never_enter_even_with_a_misset_cutoff(db):
    """A cutoff after the season ends must still not let the target or any later game in."""
    purdue, target = _tid(db, 236), _gid(db, 471)
    got = {r[0] for r in pit.team_games_as_of(db, purdue, target, FAR_FUTURE)}
    assert got == {_gid(db, 14), _gid(db, 300)}
    agg = pit.team_season_to_date(db, purdue, target, FAR_FUTURE)
    assert target not in agg["game_ids"] and agg["games"] == 2


def test_as_of_rule_holds_on_a_sample_of_real_team_games(db):
    sample = db.execute("""SELECT t.canonical_team_id, t.canonical_game_id, g.scheduled_tip_utc
        FROM fact.team_games t JOIN core.games g USING (canonical_game_id)
        WHERE g.tip_time_quality = 'EXACT' ORDER BY hash(t.canonical_game_id || t.canonical_team_id) LIMIT 150""").fetchall()
    for team, target, tip in sample:
        for cutoff in (tip - timedelta(hours=1), FAR_FUTURE):
            agg = pit.team_season_to_date(db, team, target, cutoff)
            if not agg["games"]:
                continue
            assert target not in agg["game_ids"]
            assert agg["feature_as_of_time"] < cutoff
            later = scalar(db, """SELECT count(*) FROM core.games WHERE list_contains(?, canonical_game_id)
                                  AND (scheduled_tip_utc IS NULL OR scheduled_tip_utc >= ?)""", [agg["game_ids"], tip])
            assert later == 0, (target, cutoff)


def test_as_of_totals_equal_an_independent_sum(db):
    purdue, target = _tid(db, 236), _gid(db, 872)          # 2024-11-19 at Marquette
    tip = scalar(db, "SELECT scheduled_tip_utc FROM core.games WHERE canonical_game_id = ?", [target])
    agg = pit.team_season_to_date(db, purdue, target, tip - timedelta(hours=6))
    n, pts, fga = db.execute("""SELECT count(*), sum(t.points), sum(t.fga) FROM fact.team_games t JOIN core.games g
        USING (canonical_game_id) WHERE t.canonical_team_id = ? AND g.season = 2025 AND g.scheduled_tip_utc < ?""",
                             [purdue, tip]).fetchone()
    assert (agg["games"], agg["points"], agg["fga"]) == (n, pts, fga)


def test_completed_season_aggregate_is_illegal_for_its_own_season():
    end_2025 = datetime(2025, 4, 9, tzinfo=UTC)
    assert not pit.completed_season_aggregate_is_legal(2025, end_2025, 2025, FAR_FUTURE)
    assert pit.completed_season_aggregate_is_legal(2024, datetime(2024, 4, 9, tzinfo=UTC), 2025,
                                                   datetime(2024, 11, 4, tzinfo=UTC))
    # prior season, but its aggregate was not yet knowable at the cutoff
    assert not pit.completed_season_aggregate_is_legal(2024, end_2025, 2025, datetime(2024, 11, 4, tzinfo=UTC))


def test_pit_policy_states_the_corrected_rule():
    text = (C.PROJECT_ROOT / "POINT_IN_TIME_POLICY.md").read_text()
    assert "season-to-date" in text and "completed-season aggregate" in text.lower()
    assert "become knowable only after the **last game of that season**" not in text   # superseded wording


# =========================================================================== C. season-statistics authority
FIELDS = ["points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta",
          "orb", "drb", "reb", "ast", "stl", "blk", "tov_total", "pf"]


def test_game_derived_season_totals_recompute_from_game_rows(db):
    sums = ", ".join(f"sum({f}) {f}" for f in FIELDS)
    bad = scalar(db, f"""SELECT count(*) FROM fact.team_season_game_derived d
        LEFT JOIN (SELECT canonical_team_id, season, count(*) n, {sums} FROM flagship.team_game_outcomes GROUP BY 1, 2) g
        USING (canonical_team_id, season)
        WHERE d.games_with_team_box <> coalesce(g.n, 0) OR {' OR '.join(f'd.{f} IS DISTINCT FROM g.{f}' for f in FIELDS)}""")
    assert bad == 0
    bad = scalar(db, """SELECT count(*) FROM fact.team_season_game_derived d JOIN (
        SELECT team, season, count(*) n, sum(p) p FROM (
            -- Stage 2.1: game-derived totals are defined over the TRUSTED game relation (one real contest once)
            SELECT home_team_id team, season, home_points p FROM flagship.games WHERE home_points IS NOT NULL AND away_points IS NOT NULL
            UNION ALL SELECT away_team_id, season, away_points FROM flagship.games WHERE home_points IS NOT NULL AND away_points IS NOT NULL)
        GROUP BY 1, 2) r ON r.team = d.canonical_team_id AND r.season = d.season
        WHERE d.games_with_result <> r.n OR d.points_all_results <> r.p""")
    assert bad == 0


def test_every_summary_conflict_is_recorded_with_game_derived_authority(db):
    for f in ["games"] + FIELDS:
        twin = "games_with_team_box" if f == "games" else f
        n = scalar(db, f"""SELECT count(*) FROM fact.team_season_stats s JOIN fact.team_season_game_derived d
                           USING (canonical_team_id, season) WHERE s.{f} IS DISTINCT FROM d.{twin}""")
        assert n == scalar(db, "SELECT count(*) FROM qa.season_summary_conflicts WHERE field = ?", [f]), f
    assert scalar(db, "SELECT count(*) FROM qa.season_summary_conflicts WHERE authoritative_source <> 'GAME_DERIVED'") == 0


def test_measured_root_cause_of_summary_conflicts(db):
    """Raw summaries count games/points over ALL source results but shot primitives over BOXED games only.
    Stage 2.1: the raw summaries also count the duplicate source records, so they now differ from the trusted
    game-derived totals on EXACTLY the remediated team-seasons, by EXACTLY the removed duplicate contributions."""
    import csv
    removed = {(r["canonical_team_id"], int(r["season"])): (int(r["old_games"]) - int(r["new_games"]),
                                                            int(r["old_points_for"]) - int(r["new_points_for"]))
               for r in csv.DictReader((C.QA / "game_identity_aggregate_impact.csv").open())}
    diff = {(t, s): (g, p) for t, s, g, p in db.execute("""SELECT canonical_team_id, season, s.games - d.games_with_result,
        s.points - d.points_all_results FROM fact.team_season_stats s JOIN fact.team_season_game_derived d
        USING (canonical_team_id, season) WHERE s.games <> d.games_with_result OR s.points <> d.points_all_results""").fetchall()}
    with_summary = {(t, s) for t, s in db.execute("SELECT canonical_team_id, season FROM fact.team_season_stats").fetchall()}
    expected = {k: v for k, v in removed.items() if k in with_summary}
    assert expected and diff == expected
    assert all(v[0] > 0 for v in diff.values())
    assert scalar(db, "SELECT count(*) FROM qa.season_summary_conflicts WHERE field NOT IN ('games', 'points')") == 0


def test_raw_season_summaries_are_preserved_unchanged(db):
    bad = scalar(db, """SELECT count(*) FROM fact.team_season_stats f JOIN (
            SELECT DISTINCT ON (season, teamId) * FROM staging.sp_team_season_stats
            ORDER BY season, teamId, conference NULLS LAST, filename) s
        ON f.canonical_team_id = cid('T', 'team', s.teamId) AND f.season = as_int(s.season)
        WHERE f.points IS DISTINCT FROM as_int(s.teamStats_points_total) OR f.games IS DISTINCT FROM as_int(s.games)
           OR f.fgm IS DISTINCT FROM as_int(s.teamStats_fieldGoals_made)""")
    assert bad == 0
    assert scalar(db, "SELECT count(*) FROM fact.team_season_stats") == scalar(
        db, "SELECT count(DISTINCT (season, teamId)) FROM staging.sp_team_season_stats")


def test_game_derived_boxed_totals_satisfy_the_points_identity(db):
    assert scalar(db, """SELECT count(*) FROM fact.team_season_game_derived d
        WHERE NOT EXISTS (SELECT 1 FROM fact.team_games t WHERE t.canonical_team_id = d.canonical_team_id
                          AND t.season = d.season AND t.accounting_issues IS NOT NULL)
          AND d.games_with_team_box > 0 AND d.points <> 2 * d.two_pm + 3 * d.three_pm + d.ftm""") == 0


def test_flagship_exposes_game_derived_season_totals(db):
    assert scalar(db, "SELECT count(*) FROM duckdb_views() WHERE schema_name = 'flagship' "
                      "AND view_name = 'team_season_game_derived'") == 1


# =========================================================================== D. Sharp Stats provider identity
STALE = re.compile(r"COVERS|Covers|StatSharp|STATSHARP|statsharp|covers_snapshot")


def test_availability_provider_identity_is_sharp_stats(raw_root):
    assert availability.PROVIDERS == ("sharp_stats",)
    with pytest.raises(ValueError):
        availability.ingest("COVERS", str(C.PROJECT_ROOT / "tests" / "fixtures" / "SYNTHETIC_capture.csv"))


def test_no_stale_covers_naming_in_code_tests_or_platform_docs():
    files = [*(C.PROJECT_ROOT / "cbb_platform").rglob("*.py"), C.PROJECT_ROOT / "cbb_platform" / "schema.sql",
             *(C.PROJECT_ROOT / "tests").rglob("*.*"),
             *(C.PROJECT_ROOT / n for n in ("CANONICAL_SCHEMA.md", "POINT_IN_TIME_POLICY.md", "README.md",
                                             "LOCKBOX_POLICY.md")),
             C.PROJECT_ROOT / "docs" / "STAGE_1_COVERAGE_SUMMARY.md", C.PROJECT_ROOT / "docs" / "SEASON_STATISTICS_AUTHORITY.md",
             C.PROJECT_ROOT / "docs" / "CBBD_PLAYER_IDENTITY_VERIFICATION.md"]
    me = os.path.abspath(__file__)
    hits = [f"{p.name}:{m.group()}" for p in files if p.is_file() and os.path.abspath(p) != me
            and "__pycache__" not in str(p) for m in [STALE.search(p.read_text(errors="ignore"))] if m]
    assert hits == []


# =========================================================================== E. lockbox
@pytest.mark.parametrize("endpoint,params", [
    ("games", {"season": 2026}),
    ("games/players", {"season": 2026, "team": "Purdue"}),
    ("games/teams", {"startDateRange": "2025-11-01", "endDateRange": "2025-11-30"}),
    ("games", {"season": 2025, "startDateRange": "2025-03-01", "endDateRange": "2025-09-01"}),   # straddles
    ("games", {"startDateRange": "2026-01-01"}),                                                  # open-ended
    ("games/players", {"team": "Purdue"}),                                                        # provider default = now
    ("teams/roster", {"team": "Purdue"}),
    ("teams/roster", {"season": 2026, "team": "Purdue"}),
    ("games", {"season": 2027}),
    ("plays/game", {"gameId": 99999999}),                                                         # unprovable season
])
def test_lockbox_request_guard_refuses(endpoint, params):
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_request("cbbd", endpoint, params, known_game_season=lambda gid: None)


def test_lockbox_request_guard_refuses_a_known_lockbox_game():
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_request("cbbd", "plays/game", {"gameId": 1}, known_game_season=lambda gid: 2026)


@pytest.mark.parametrize("endpoint,params", [
    ("games", {"season": 2025, "team": "Purdue", "startDateRange": "2024-11-04", "endDateRange": "2024-11-12"}),
    ("games/players", {"startDateRange": "2023-11-06", "endDateRange": "2023-11-14"}),
    ("teams/roster", {"season": 2024, "team": "Purdue"}),
    ("plays/game", {"gameId": 14}),
])
def test_lockbox_request_guard_allows_development_seasons(endpoint, params):
    lockbox.check_request("cbbd", endpoint, params, known_game_season=lambda gid: 2025)


def test_lockbox_response_guard():
    lockbox.check_response("cbbd", [{"season": 2025, "startDate": "2025-03-01T00:00:00Z"}])
    for bad in ([{"season": 2026}], [{"season": 2025, "startDate": "2025-11-04T23:00:00Z"}]):
        with pytest.raises(lockbox.LockboxViolation):
            lockbox.check_response("cbbd", bad)


class _Resp:
    def __init__(self, body):
        self.content = json.dumps(body).encode()

    def raise_for_status(self):
        pass


def test_fetch_refuses_lockbox_before_any_network_call(monkeypatch, raw_root):
    monkeypatch.setenv("CBBD_API_KEY", "x")
    monkeypatch.setattr(cbbd.requests, "get", lambda *a, **k: pytest.fail("network call attempted"))
    with pytest.raises(lockbox.LockboxViolation):
        cbbd.fetch("games", {"season": 2026})
    assert not raw_root.exists() or not any(raw_root.rglob("*"))


def test_fetch_refuses_to_store_a_lockbox_response(monkeypatch, raw_root):
    monkeypatch.setenv("CBBD_API_KEY", "x")
    monkeypatch.setattr(cbbd.requests, "get", lambda *a, **k: _Resp([{"id": 1, "season": 2026}]))
    with pytest.raises(lockbox.LockboxViolation):
        cbbd.fetch("games", {"season": 2025})
    assert not raw_root.exists() or not any(raw_root.rglob("*"))


def _walk_dates_and_seasons(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("date", "startDate", "datetime", "game_date", "wallclock") and isinstance(v, str):
                out["dates"].add(v[:10])
            if k == "season" and isinstance(v, (int, float)):
                out["seasons"].add(int(v))
            _walk_dates_and_seasons(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _walk_dates_and_seasons(x, out)


BIG_PLAY_PAYLOAD = 5_000_000      # bytes; larger PBP payloads are scanned column-wise by DuckDB (same keys)


def test_no_lockbox_payload_was_ever_stored():
    big = []
    for provider, lockbox_season in (("cbbd", 2026), ("bdl", 2025)):   # BDL seasons are start-year
        for path, meta in raw_store.records(provider):
            assert str(meta["params"].get("season")) != str(lockbox_season), path
            if provider == "cbbd" and meta["endpoint"].startswith("plays/") and meta["size_bytes"] > BIG_PLAY_PAYLOAD:
                big.append(str(path))
                continue
            seen = {"dates": set(), "seasons": set()}
            _walk_dates_and_seasons(json.loads(raw_store.read_payload(path)), seen)
            assert lockbox_season not in seen["seasons"], path
            assert not [d for d in seen["dates"] if "2025-08-01" <= d <= "2026-07-31"], path
    if big:   # PlayInfo carries its dates/seasons only at top level: season, gameStartDate, wallclock
        import duckdb
        lst = "[" + ",".join(f"'{p}'" for p in big) + "]"
        n_bad = duckdb.sql(f"""SELECT count(*) FROM read_json({lst}, format='array', maximum_object_size=200000000,
                columns={{'season':'INTEGER','gameStartDate':'VARCHAR','wallclock':'VARCHAR'}})
                WHERE season = 2026 OR substr(gameStartDate, 1, 10) BETWEEN '2025-08-01' AND '2026-07-31'
                   OR substr(wallclock, 1, 10) BETWEEN '2025-08-01' AND '2026-07-31'""").fetchone()[0]
        assert n_bad == 0


def test_lockbox_policy_document_exists():
    text = (C.PROJECT_ROOT / "LOCKBOX_POLICY.md").read_text()
    for rule in ("game outcomes", "player/team game statistics", "model selection", "feature engineering",
                 "tuning", "calibration", "architecture comparisons"):
        assert rule in text, rule


# =========================================================================== CBBD athleteId identity logic
def _rows(spec):
    """spec: list of (game_id, season, team_id, name, athlete_id)."""
    return [dict(zip(("game_id", "season", "team_id", "name", "athlete_id"), r)) for r in spec]


def _persistent(n_players=12, games=(1, 2, 3), season=2025):
    return [(g, season, 1, f"P{i}", 1000 + i) for i in range(n_players) for g in games]


def test_classify_persistent():
    cls, ev = ident.classify(_rows(_persistent() + _persistent(games=(9, 10), season=2024)))
    assert cls == ident.PERSISTENT and ev["across_seasons"]["players_seen_in_2plus_seasons"] == 12


def test_classify_per_record_ids_as_non_persistent():
    rows = [(g, 2025, 1, f"P{i}", g * 100 + i) for i in range(12) for g in (1, 2, 3)]
    assert ident.classify(_rows(rows))[0] == ident.NON_PERSISTENT


def test_classify_mixed_and_insufficient_evidence_as_unresolved():
    mixed = _persistent(6) + [(g, 2025, 1, f"Q{i}", g * 100 + i) for i in range(6) for g in (1, 2, 3)]
    assert ident.classify(_rows(mixed))[0] == ident.UNRESOLVED
    assert ident.classify(_rows(_persistent(3)))[0] == ident.UNRESOLVED
    assert ident.classify([])[0] == ident.UNRESOLVED


def test_classify_rejects_id_reuse_and_cross_season_change():
    reuse = _persistent() + [(1, 2025, 2, "Someone Else", 1000)]          # id 1000 also used by another player
    assert ident.classify(_rows(reuse))[0] != ident.PERSISTENT
    changed = _persistent() + [(g, 2024, 1, f"P{i}", 5000 + i) for i in range(12) for g in (7, 8)]
    assert ident.classify(_rows(changed))[0] != ident.PERSISTENT


def test_anchor_compatibility_requires_persistence_and_season_file_agreement():
    good = {"classification": ident.PERSISTENT,
            "season_file_compatibility": {"agreement": 1.0, "name_resolved_rows": 50}}
    assert ident.anchor_compatible(good)
    assert not ident.anchor_compatible({**good, "classification": ident.UNRESOLVED})
    assert not ident.anchor_compatible({**good, "season_file_compatibility": {"agreement": 0.9, "name_resolved_rows": 50}})


def test_analysis_without_live_payloads_fails_closed(raw_root):
    r = ident.analyze(None, root=raw_root, blocked_reason="test")
    assert r["classification"] == ident.UNRESOLVED and r["status"] == "BLOCKED_CREDENTIAL"
    assert r["anchor_compatible"] is False


def test_adapter_never_maps_api_ids_until_verified(empty_db, tmp_path, monkeypatch):
    empty_db.execute("INSERT INTO metadata.player_id_map VALUES ('CBBD', '900001', 'P-x', NULL, NULL, 'ANCHOR', 1.0, FALSE, NULL, 't')")
    monkeypatch.setattr(ident, "RESULT_JSON", tmp_path / "v.json")
    assert cbbd._player_token(empty_db, 900001) == "CBBD_ATHLETE:900001"                  # no verification file
    (tmp_path / "v.json").write_text(json.dumps({"classification": ident.UNRESOLVED}))
    assert cbbd._player_token(empty_db, 900001) == "CBBD_ATHLETE:900001"
    (tmp_path / "v.json").write_text(json.dumps({"classification": ident.PERSISTENT,
                                                 "season_file_compatibility": {"agreement": 1.0, "name_resolved_rows": 99}}))
    assert cbbd._player_token(empty_db, 900001) == "P-x"


def test_identity_artifacts_exist_and_markdown_is_rendered_from_json():
    result = json.loads(ident.RESULT_JSON.read_text())
    assert result["classification"] in (ident.PERSISTENT, ident.NON_PERSISTENT, ident.UNRESOLVED)
    assert ident.RESULT_MD.read_text() == ident.render_md(result)


def test_coverage_smoke_metrics_on_a_synthetic_payload():
    plays = [{"shootingPlay": True, "onFloor": [{"id": i} for i in range(10)], "wallclock": "2024-11-04T23:01:00Z",
              "shotInfo": {"location": {"x": 1.0}}},
             {"shootingPlay": False, "onFloor": [], "wallclock": None, "shotInfo": None}]
    payloads = {"games": [[{"id": 7, "season": 2025, "homePoints": 70, "awayPoints": 60}]],
                "games/players": [[{"gameId": 7, "season": 2025, "teamId": 1, "gameMinutes": 40,
                                    "players": [{"name": "A", "athleteId": 1, "minutes": 200}]}]],
                "games/teams": [[{"gameId": 7, "isHome": True, "teamStats": {"points": {"total": 70}}},
                                 {"gameId": 7, "isHome": False, "teamStats": {"points": {"total": 60}}}]],
                "plays/game": {7: plays}, "substitutions/game": {7: [{}]}, "lineups/game": {7: []}}
    cov = ident.coverage(payloads, None)
    assert cov["player_box_coverage"]["share"] == 1 and cov["team_box_coverage"]["share"] == 1
    assert cov["score_reconciliation"]["team_box_vs_games"]["share"] == 1
    assert cov["minutes_reconciliation"]["share"] == 1
    ev = cov["event_games"][7]
    assert ev["onFloor_complete_10"]["share"] == 0.5 and ev["shotInfo_on_shooting_plays"]["share"] == 1
    assert ev["substitutions"] == 1 and ev["lineups"] == 0


# =========================================================================== credentials never persisted
SECRET = "TEST-SECRET-KEY-do-not-persist-7f3a"


def test_fetch_sends_the_key_only_in_the_header_and_never_stores_it(monkeypatch, raw_root):
    monkeypatch.setenv("CBBD_API_KEY", SECRET)
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return _Resp([{"id": 14, "season": 2025, "startDate": "2024-11-04T23:00:00Z"}])

    monkeypatch.setattr(cbbd.requests, "get", fake_get)
    rec = cbbd.fetch("games", {"season": 2025, "team": "Purdue"})
    assert seen["headers"] == {"Authorization": f"Bearer {SECRET}"}
    assert SECRET not in json.dumps(seen["params"]) and SECRET not in seen["url"]
    stored = [p for p in raw_root.rglob("*") if p.is_file()]
    assert rec.path in stored and len(stored) == 2
    assert not [p for p in stored if SECRET.encode() in p.read_bytes()]


CREDENTIAL_ENV = ("CBBD_API_KEY", "BDL_API_KEY", "BALLDONTLIE_API_KEY")


def test_no_live_credential_appears_in_any_project_artifact():
    from cbb_platform import credentials
    secrets = [os.environ[k].encode() for k in CREDENTIAL_ENV if len(os.environ.get(k, "")) >= 8]
    secrets += [v.encode() for v in credentials.present_values() if v.encode() not in secrets]   # cleaned forms too
    if not secrets:
        pytest.skip("no credential in the environment to search for")
    hits = []
    for base in (C.DATA, C.PROJECT_ROOT / "docs", C.PROJECT_ROOT / "cbb_platform", C.PROJECT_ROOT / "tests"):
        for p in base.rglob("*"):
            if not p.is_file() or p.stat().st_size == 0 or "__pycache__" in p.parts:
                continue
            with open(p, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as m:
                hits += [str(p) for s in secrets if m.find(s) != -1]
    for p in C.PROJECT_ROOT.glob("*.*"):
        if p.is_file():
            hits += [str(p) for s in secrets if s in p.read_bytes()]
    assert hits == []
