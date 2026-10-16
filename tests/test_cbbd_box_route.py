"""CBBD box-score route (cbb_platform/cbbd_box_route.py): synthetic inputs only. The closed guard refuses 2025-26 before
any client, credential or network call; reshaping matches the Starter Pack text conventions the canonical build reads;
de-duplication collapses identical overlap rows and fails closed on conflicting ones."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from cbb_platform import cbbd_box_route as B, config as C, lockbox


def _no_client(*a, **k):
    raise AssertionError("a client was created for a refused season")


@pytest.mark.parametrize("season", [lockbox.LOCKBOX_SEASON, 2027, 2028])
def test_closed_guard_refuses_lockbox_and_later_seasons(season, monkeypatch, tmp_path):
    monkeypatch.setattr(B, "_client", _no_client)
    with pytest.raises(lockbox.LockboxViolation):
        B.plan_base(season)
    with pytest.raises(lockbox.LockboxViolation):
        B.plan_players(season, [f"{season - 1}-12-01"])
    with pytest.raises(lockbox.LockboxViolation):
        B.fetch(season, raw_root=tmp_path, log_path=tmp_path / "log.jsonl")
    with pytest.raises(lockbox.LockboxViolation):
        B.build([season], tmp_path / "x.duckdb", tmp_path / "pq", raw_root=tmp_path, log_path=tmp_path / "log.jsonl")
    assert not list(tmp_path.rglob("*.json*"))


def _api_filter(games: list[dict], start: str, end: str) -> list[dict]:
    """The observed CBBD date filter: startDate in [start 00:00Z, end 00:00Z] (end date exclusive after 00:00:00Z)."""
    lo, hi = f"{start}T00:00:00", f"{end}T00:00:00"
    return [g for g in games if lo <= g["startDate"][:19] <= hi]


@pytest.mark.parametrize("season", [2024, 2025])
def test_windows_are_chained_end_exclusive_and_cover_the_season(season):
    reqs = B.plan_base(season)
    lo, hi = B.season_dates(season)
    for ep in ("games", "games/teams"):
        w = [(date.fromisoformat(p["startDateRange"]), date.fromisoformat(p["endDateRange"])) for e, p in reqs if e == ep]
        assert w[0][0] == lo and w[-1][1] == hi                       # first start = Aug 1, last (guard-capped) end = Jul 31
        assert all(timedelta(days=1) <= b - a <= timedelta(days=B.WEEK) for a, b in w)
        assert all(w[i + 1][0] == w[i][1] for i in range(len(w) - 1))  # next window starts where the previous ends
    assert ("stats/team/season", {"season": season}) in reqs and ("stats/player/season", {"season": season}) in reqs


def test_no_boundary_day_game_is_lost_and_none_survives_twice():
    """Synthetic games at 00:00:00Z, just after 00:00Z, midday and 23:59:59Z on EVERY date of the 2024-25 plan, run through
    the observed end-exclusive filter: every game is returned by some window, boundary-instant games twice, and
    dedupe leaves each game exactly once."""
    lo, hi = B.season_dates(2025)
    games, d, i = [], lo, 0
    while d < hi:
        for t in ("00:00:00", "00:00:01", "12:00:00", "23:59:59"):
            i += 1
            games.append({"id": i, "season": 2025, "startDate": f"{d.isoformat()}T{t}.000Z", "homeTeamId": 1, "awayTeamId": 2})
        d += timedelta(days=1)
    wins = [p for e, p in B.plan_base(2025) if e == "games"]
    returned = [g for p in wins for g in _api_filter(games, p["startDateRange"], p["endDateRange"])]
    assert {g["id"] for g in returned} == {g["id"] for g in games}
    first_days = {p["startDateRange"] for p in wins[1:]}
    twice = [g for g in games if g["startDate"][:10] in first_days and g["startDate"][11:19] == "00:00:00"]
    assert len(returned) == len(games) + len(twice)                 # only exact-boundary instants overlap
    rows = [r for p in wins for r in B.reshape("games", _api_filter(games, p["startDateRange"], p["endDateRange"]),
                                                f"cbbd:{p['startDateRange']}")]
    deduped, dup = B.dedupe("games", rows)
    assert dup == len(twice) and sorted(int(r["id"]) for r in deduped) == sorted(g["id"] for g in games)


def test_old_inclusive_plan_would_have_lost_end_day_games():
    """Regression: the 1.0.0 plan ([d, d+6] read as inclusive) misses a game at 20:00Z on a window's last date."""
    g = [{"id": 1, "startDate": "2023-11-06T20:00:00.000Z"}]
    assert _api_filter(g, "2023-10-31", "2023-11-06") == []
    assert _api_filter(g, "2023-10-31", "2023-11-07") == g


def test_player_windows_cover_the_whole_utc_game_day():
    pl = B.plan_players(2025, ["2024-11-04", "2024-11-04", "2025-04-07"])
    assert pl == [("games/players", {"season": 2025, "startDateRange": "2024-11-03", "endDateRange": "2024-11-05"}),
                  ("games/players", {"season": 2025, "startDateRange": "2025-04-06", "endDateRange": "2025-04-08"})]
    late = [{"id": 1, "startDate": "2024-11-04T23:59:59.000Z"}, {"id": 2, "startDate": "2024-11-04T00:00:00.000Z"}]
    assert _api_filter(late, "2024-11-03", "2024-11-05") == late
    assert {e for e, _ in pl} <= B.ROUTE_ENDPOINTS


def test_last_development_window_never_names_a_lockbox_date():
    last = [p for e, p in B.plan_base(2025) if e == "games"][-1]
    assert last["endDateRange"] == "2025-07-31"
    lockbox.check_request("cbbd", "games", last)
    with pytest.raises(lockbox.LockboxViolation):                    # what an uncapped half-open end would request
        lockbox.check_request("cbbd", "games", {**last, "endDateRange": "2025-08-01"})


def test_route_never_writes_the_development_dataset(tmp_path):
    with pytest.raises(ValueError):
        B.build([2025], C.DUCKDB_PATH, tmp_path / "pq", raw_root=tmp_path, log_path=tmp_path / "log.jsonl")


def test_reshape_games_matches_starter_pack_text():
    g = {"id": 14, "season": 2025, "seasonLabel": "20242025", "seasonType": "regular", "startDate": "2024-11-04T23:00:00.000Z",
         "startTimeTbd": False, "neutralSite": False, "conferenceGame": True, "gameType": "STD", "gameNotes": None,
         "homeTeamId": 1, "homeTeam": "A", "homePoints": 90, "homePeriodPoints": [49, 41], "homeWinner": True,
         "awayTeamId": 2, "awayTeam": "B", "awayPoints": 70, "awayPeriodPoints": [30, 40], "awayWinner": False, "venueId": 11}
    r = B.reshape("games", [g], "cbbd:x")[0]
    assert r["start_date"] == "2024-11-04 23:00:00" and r["neutral_site"] == "False" and r["conference_game"] == "True"
    assert r["home_period_points"] == "[49, 41]" and r["home_points"] == "90" and r["venue_id"] == "11"
    assert r["notes"] is None and r["filename"] == "cbbd:x" and r["season"] == "2025"


def test_reshape_player_and_team_boxes():
    head = {"gameId": 14, "season": 2025, "seasonType": "regular", "startDate": "2024-11-04T23:00:00.000Z", "teamId": 1,
            "team": "A", "isHome": True, "neutralSite": False, "opponentId": 2, "gameMinutes": 40}
    pl = {"athleteId": 366, "athleteSourceId": "x", "name": "P", "starter": True, "minutes": 33, "points": 15,
          "fieldGoals": {"made": 6, "attempted": 10, "pct": 60.0}, "rebounds": {"offensive": 1, "defensive": 4, "total": 5}}
    rows = B.reshape("player_game_stats", [{**head, "players": [pl]}], "cbbd:p")
    assert len(rows) == 1
    r = rows[0]
    assert r["game_id"] == "14" and r["team_id"] == "1" and r["is_home"] == "True" and r["athleteId"] == "366"
    assert r["fieldGoals_made"] == "6" and r["rebounds_total"] == "5" and "athleteSourceId" not in r and r["starter"] == "True"
    t = B.reshape("team_game_stats", [{**head, "teamStats": {"points": {"total": 90, "byPeriod": [49, 41]}}}], "cbbd:t")[0]
    assert t["teamStats_points_total"] == "90" and t["teamStats_points_byPeriod_0"] == "49" and t["teamStats_points_byPeriod_1"] == "41"


def test_dedupe_collapses_identical_and_refuses_conflicts():
    a = {"gameId": "1", "teamId": "2", "x": "5", "filename": "w1"}
    rows, dup = B.dedupe("team_game_stats", [a, {**a, "filename": "w2"}])
    assert dup == 1 and rows == [a]
    with pytest.raises(ValueError):
        B.dedupe("team_game_stats", [a, {**a, "x": "6"}])


def test_staging_refuses_stray_seasons(tmp_path, monkeypatch):
    from cbb_platform import raw_store
    rec = raw_store.store("cbbd", "stats/team/season", json.dumps([{"season": 2024, "teamId": 1}]).encode(),
                          params={"season": 2025}, root=tmp_path)
    entry = {"status": "STORED", "purpose": B.PURPOSE, "endpoint": "stats/team/season", "params": {"season": 2025},
             "season": 2025, "request_key": "k", "payload_sha256": rec.sha256,
             "stored_file": str(rec.path.relative_to(tmp_path))}
    monkeypatch.setattr(B, "payload_index", lambda *a, **k: [entry])
    with pytest.raises(lockbox.LockboxViolation):
        B.staged_rows([2025], tmp_path, tmp_path / "log.jsonl")


def test_route_endpoints_are_box_score_families_only():
    assert B.ROUTE_ENDPOINTS == {"games", "games/teams", "games/players", "stats/player/season", "stats/team/season"}
    assert not any(e.startswith(("plays", "lines", "lineups")) for e in B.ROUTE_ENDPOINTS)
    assert B.DEV_RAW_ROOT != C.RAW / "cbbd" and B.DEV_LOG != C.MANIFESTS / "cbbd_request_log.jsonl"


@pytest.mark.parametrize("season", [2024, 2025])
def test_context_plan_reproduces_the_requests_that_built_the_development_data(season):
    """For a development season the route's context plan equals the stored Stage 2 request set that produced the
    accepted v2.4 substitution / roster / recruiting / portal data (so the same plan is what 2025-26 would use)."""
    import duckdb
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    teams = [r[0] for r in con.execute("""SELECT DISTINCT t.school FROM core.team_seasons s JOIN core.teams t
                                          USING (canonical_team_id) WHERE s.season = ?""", [season]).fetchall()]
    con.close()
    stored = set()
    for line in (C.MANIFESTS / "cbbd_request_log.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("status") in ("STORED", "ALREADY_STORED") and e["endpoint"] in B.CONTEXT_ENDPOINTS:
            stored.add(json.dumps([e["endpoint"], e["params"]], sort_keys=True))
    plan = {json.dumps([e, p], sort_keys=True) for e, p in B.plan_context(season, teams)}
    assert plan <= stored, sorted(plan - stored)[:5]
    subs = {x for x in stored if json.loads(x)[0] == "substitutions/team" and json.loads(x)[1]["season"] == season}
    assert {x for x in plan if json.loads(x)[0] == "substitutions/team"} == subs
    with pytest.raises(lockbox.LockboxViolation):
        B.plan_context(lockbox.LOCKBOX_SEASON, teams)


def _stage(tmp_path, monkeypatch, entries: list[tuple[str, dict, list]]):
    from cbb_platform import raw_store
    idx = []
    for n, (ep, params, body) in enumerate(entries):
        rec = raw_store.store("cbbd", ep, json.dumps(body).encode(), params=params, root=tmp_path)
        idx.append({"status": "STORED", "purpose": B.PURPOSE, "endpoint": ep, "params": params, "season": params.get("season"),
                    "request_key": f"k{n}", "payload_sha256": rec.sha256, "stored_file": str(rec.path.relative_to(tmp_path))})
    monkeypatch.setattr(B, "payload_index", lambda *a, **k: idx)
    return B.staged_rows([2025], tmp_path, tmp_path / "log.jsonl")


def test_non_di_schools_with_cbbd_season_stats_are_not_di(tmp_path, monkeypatch):
    """CBBD stats/team/season and stats/player/season return non-D-I schools (e.g. Bowie State, Mount Olive) with a NULL
    conference; they must not reach staging.sp_*_season_stats, whose rows define D-I membership in the canonical build."""
    teams = [{"season": 2025, "teamId": 1, "team": "Kansas", "conference": "Big 12"},
             {"season": 2025, "teamId": 2, "team": "Bowie State", "conference": None},
             {"season": 2025, "teamId": 3, "team": "Mount Olive", "conference": None}]
    players = [{"season": 2025, "teamId": 1, "athleteId": 10, "team": "Kansas", "conference": "Big 12"},
               {"season": 2025, "teamId": 2, "athleteId": 20, "team": "Bowie State", "conference": None}]
    rows, rep = _stage(tmp_path, monkeypatch, [("stats/team/season", {"season": 2025}, teams),
                                                ("stats/player/season", {"season": 2025}, players)])
    assert [r["teamId"] for r in rows["team_season_stats"]] == ["1"]
    assert [r["athleteId"] for r in rows["player_season_stats"]] == ["10"]
    assert rep["team_season_stats_non_di_rows_dropped"] == 2 and rep["player_season_stats_non_di_rows_dropped"] == 1
    assert not B.is_di_season_row({"conference": ""}) and B.is_di_season_row({"conference": "Independent"})


def test_staging_fails_if_a_route_game_falls_in_the_unrequested_off_season(tmp_path, monkeypatch):
    g = {"id": 9, "season": 2025, "status": "final", "startDate": "2025-07-15T20:00:00.000Z", "homeTeamId": 1, "awayTeamId": 2}
    with pytest.raises(ValueError, match="June-July"):
        _stage(tmp_path, monkeypatch, [("games", {"season": 2025, "startDateRange": "2025-07-10", "endDateRange": "2025-07-17"}, [g])])


def test_payload_index_ignores_superseded_window_payloads(tmp_path):
    """Payloads of the 1.0.0 windows stay in the request log but are not part of the 1.1.0 route payload set."""
    plan = B.plan_base(2025)
    log = []
    for n, (ep, p) in enumerate(plan + [("games", {"season": 2025, "startDateRange": "2024-08-01", "endDateRange": "2024-08-07"})]):
        log.append({"status": "STORED", "purpose": B.PURPOSE, "endpoint": ep, "params": p, "season": 2025,
                    "request_key": f"k{n}", "payload_sha256": str(n), "stored_file": f"f{n}"})
    (tmp_path / "log.jsonl").write_text("\n".join(json.dumps(e) for e in log))
    idx = B.payload_index([2025], tmp_path, tmp_path / "log.jsonl")
    assert len(idx) == len(plan) and all(e["params"] != {"season": 2025, "startDateRange": "2024-08-01",
                                                         "endDateRange": "2024-08-07"} for e in idx)


# ============================================================================ route 1.2.0: completed games only
def _game(i, status, home=1, away=2, hp=70, ap=60):
    return {"id": i, "season": 2025, "status": status, "startDate": f"2024-12-{i:02d}T20:00:00.000Z", "homeTeamId": home,
            "homeTeam": f"T{home}", "awayTeamId": away, "awayTeam": f"T{away}", "homePoints": hp, "awayPoints": ap}


def _team_box(gid, team, opp, pts, fgm=0, tpm=0, ftm=0):
    return {"gameId": gid, "season": 2025, "teamId": team, "team": f"T{team}", "opponentId": opp, "opponent": f"T{opp}",
            "teamStats": {"points": {"total": pts}, "fieldGoals": {"made": fgm}, "threePointFieldGoals": {"made": tpm},
                          "freeThrows": {"made": ftm}}}


def _player_box(gid, team, opp, players, home=None):
    return {"gameId": gid, "season": 2025, "teamId": team, "team": f"T{team}", "opponentId": opp, "opponent": f"T{opp}",
            "isHome": team == home if home is not None else None, "startDate": f"2024-12-{gid:02d}T20:00:00.000Z",
            "players": [{"athleteId": a, "name": n, "points": p, "fieldGoals": {"made": 0}, "threePointFieldGoals": {"made": 0},
                         "freeThrows": {"made": p}} for a, n, p in players]}


W = {"season": 2025, "startDateRange": "2024-11-29", "endDateRange": "2024-12-06"}


@pytest.mark.parametrize("status,kept", [("final", True), (" Final ", True), ("FINAL\n", True), ("scheduled", False),
                                         ("cancelled", False), ("postponed", False), ("suspended", False), ("in_progress", False),
                                         ("completed", False), ("final ot", False), ("", False), (None, False)])
def test_only_normalized_final_status_is_a_completed_game(status, kept):
    assert B.is_final_status(status) is kept


def test_non_final_game_contributes_zero_staged_rows_even_with_scores_and_boxes(tmp_path, monkeypatch):
    """A cancelled game that nevertheless carries scores, team boxes and player rows is excluded entirely: completion is
    never inferred from scores or box presence. Unknown statuses fail closed the same way."""
    games = [_game(1, "final"), _game(2, "cancelled"), _game(3, "scheduled"), _game(4, "Postponed"), _game(5, None),
             _game(6, "weird-new-status")]
    tb = [_team_box(g["id"], 1, 2, 70) for g in games] + [_team_box(g["id"], 2, 1, 60) for g in games]
    pb = [_player_box(g["id"], 1, 2, [(100 + g["id"], f"P{g['id']}", 70)]) for g in games]
    rows, rep = _stage(tmp_path, monkeypatch, [("games", W, games), ("games/teams", W, tb), ("games/players", W, pb)])
    assert [r["id"] for r in rows["games"]] == ["1"]
    assert {r["gameId"] for r in rows["team_game_stats"]} == {"1"} and {r["game_id"] for r in rows["player_game_stats"]} == {"1"}
    assert all(B.STATUS_COL not in r for r in rows["games"])            # never reaches the Starter Pack layout
    fo = rep["final_only"]
    assert fo["non_final_games_excluded"] == 5 and [e["game_id"] for e in fo["non_final_games"]] == ["2", "3", "4", "5", "6"]
    assert fo["team_game_stats_rows_of_non_final_games_dropped"] == 10 and fo["player_game_stats_rows_of_non_final_games_dropped"] == 5
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA staging")
    for fam in ("games", "team_game_stats", "player_game_stats"):        # what the canonical build would read
        B._register(con, f"cbbd_{fam}", rows[fam])
    assert con.execute("SELECT count(*) FROM staging.cbbd_games WHERE id <> '1'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM staging.cbbd_team_game_stats WHERE gameId <> '1'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM staging.cbbd_player_game_stats WHERE game_id <> '1'").fetchone()[0] == 0


def test_box_rows_without_any_games_record_are_dropped(tmp_path, monkeypatch):
    rows, rep = _stage(tmp_path, monkeypatch, [("games", W, [_game(1, "final")]),
                                                ("games/teams", W, [_team_box(1, 1, 2, 70), _team_box(7, 1, 2, 70)])])
    assert {r["gameId"] for r in rows["team_game_stats"]} == {"1"}
    assert rep["final_only"]["team_game_stats_rows_without_a_games_record_dropped"] == 1


def test_conflicting_status_for_one_game_fails_closed(tmp_path, monkeypatch):
    w2 = {**W, "startDateRange": "2024-12-06", "endDateRange": "2024-12-13"}
    with pytest.raises(ValueError, match="conflicting"):
        _stage(tmp_path, monkeypatch, [("games", W, [_game(1, "final")]), ("games", w2, [_game(1, "cancelled")])])


# ============================================================================ route 1.2.0: provider team assignment
def _season_rows(spec):
    """spec: [(athleteId, name, team, games)] -> D-I stats/player/season rows."""
    return [{"season": 2025, "athleteId": a, "name": n, "teamId": t, "team": f"T{t}", "conference": "C", "games": g}
            for a, n, t, g in spec]


def _history(team, opp, athletes, first_gid=20):
    """Each athlete played two OTHER games for `team` (box rows + final games + reconciling team boxes)."""
    games, tb, pb = [], [], []
    for k, gid in enumerate((first_gid, first_gid + 1)):
        games.append(_game(gid, "final", home=team, away=opp + 10 + k))
        pts = sum(1 for _ in athletes)
        tb += [_team_box(gid, team, opp + 10 + k, pts, ftm=pts), _team_box(gid, opp + 10 + k, team, 0)]
        pb.append(_player_box(gid, team, opp + 10 + k, [(a, n, 1) for a, n in athletes], home=team))
    return games, tb, pb


def _team_case(tmp_path, monkeypatch, game_pb, team_boxes, season_spec, history):
    games = [_game(1, "final", home=1, away=2, hp=10, ap=6)]
    tb, pb = list(team_boxes), list(game_pb)
    for team, opp, ath, gid0 in history:
        g, t, p = _history(team, opp, ath, gid0)
        games, tb, pb = games + g, tb + t, pb + p
    return _stage(tmp_path, monkeypatch, [("games", W, games), ("games/teams", W, tb), ("games/players", W, pb),
                                          ("stats/player/season", {"season": 2025}, _season_rows(season_spec))])


def test_misfiled_rows_are_moved_to_the_evidenced_team_under_the_reconciliation_gate(tmp_path, monkeypatch):
    """Team 2's players (athletes 21, 22) are filed under team 1. Evidence: two other team-2 games each, a 3-game team-2
    season row and only a 1-game team-1 echo; after the move both teams reconcile with their team boxes."""
    pb = [_player_box(1, 1, 2, [(11, "A", 10), (21, "X", 4), (22, "Y", 2)], home=1)]
    tb = [_team_box(1, 1, 2, 10, ftm=10), _team_box(1, 2, 1, 6, ftm=6)]
    season = [(11, "A", 1, 3), (21, "X", 2, 3), (21, "X", 1, 1), (22, "Y", 2, 3), (22, "Y", 1, 1)]
    rows, rep = _team_case(tmp_path, monkeypatch, pb, tb, season, [(1, 1, [(11, "A")], 20), (2, 2, [(21, "X"), (22, "Y")], 30)])
    g1 = {(r["athleteId"], r["team_id"], r["opponent_id"], r["is_home"]) for r in rows["player_game_stats"] if r["game_id"] == "1"}
    assert g1 == {("11", "1", "2", "True"), ("21", "2", "1", "False"), ("22", "2", "1", "False")}
    ta = rep["team_assignment"]
    assert ta["repaired"] == 2 and ta["quarantined"] == 0 and ta["games_repaired"] == ["1"]
    assert {(r["athleteId"], r["raw_provider_team_id"], r["corrected_team_id"]) for r in ta["repairs"]} == {("21", "1", "2"), ("22", "1", "2")}
    # the 1-game season-file echo on the wrong team is removed (else the players become team-1 members for participation)
    assert sorted((s["athleteId"], s["teamId"]) for s in rows["player_season_stats"]) == [("11", "1"), ("21", "2"), ("22", "2")]
    assert sorted((e["athleteId"], e["team_id"]) for e in ta["season_stats_echo_rows_removed"]) == [("21", "1"), ("22", "1")]


def test_mirrored_copy_is_removed_and_true_team_line_kept(tmp_path, monkeypatch):
    """Athlete 21 (team 2) is listed under BOTH teams. The persistent CBBD id would make the canonical DISTINCT ON
    (game_id, athleteId) keep an arbitrary copy, so the route removes the copy under the team the player does not belong to."""
    pb = [_player_box(1, 1, 2, [(11, "A", 10), (21, "X", 6)], home=1), _player_box(1, 2, 1, [(21, "X", 6)], home=1)]
    tb = [_team_box(1, 1, 2, 10, ftm=10), _team_box(1, 2, 1, 6, ftm=6)]
    season = [(11, "A", 1, 3), (21, "X", 2, 3), (21, "X", 1, 1)]
    rows, rep = _team_case(tmp_path, monkeypatch, pb, tb, season, [(1, 1, [(11, "A")], 20), (2, 2, [(21, "X")], 30)])
    g1 = sorted((r["athleteId"], r["team_id"]) for r in rows["player_game_stats"] if r["game_id"] == "1")
    assert g1 == [("11", "1"), ("21", "2")]
    r = rep["team_assignment"]["repairs"]
    assert len(r) == 1 and r[0]["kind"] == "MIRRORED_COPY_REMOVED" and (r[0]["raw_provider_team_id"], r[0]["corrected_team_id"]) == ("1", "2")
    assert [(s["athleteId"], s["teamId"]) for s in rows["player_season_stats"] if s["athleteId"] == "21"] == [("21", "2")]


def test_undecidable_misfiled_rows_are_quarantined_not_guessed(tmp_path, monkeypatch):
    """Same misfiling, but the team boxes do not reconcile after the move: every flagged row is quarantined."""
    pb = [_player_box(1, 1, 2, [(11, "A", 10), (21, "X", 4), (22, "Y", 2)], home=1)]
    tb = [_team_box(1, 1, 2, 10, ftm=10), _team_box(1, 2, 1, 9, ftm=9)]          # team 2 box 9 != moved 6
    season = [(11, "A", 1, 3), (21, "X", 2, 3), (21, "X", 1, 1), (22, "Y", 2, 3), (22, "Y", 1, 1)]
    rows, rep = _team_case(tmp_path, monkeypatch, pb, tb, season, [(1, 1, [(11, "A")], 20), (2, 2, [(21, "X"), (22, "Y")], 30)])
    assert sorted(r["athleteId"] for r in rows["player_game_stats"] if r["game_id"] == "1") == ["11"]
    ta = rep["team_assignment"]
    assert ta["repaired"] == 0 and ta["quarantined"] == 2 and ta["unresolved"] == 0
    assert all("do not reconcile" in q["rule"] and q["row"]["team_id"] == "1" for q in ta["quarantines"])
    assert ta["season_stats_echo_rows_removed"] == []                      # only repairs remove echoes


def test_rows_without_contrary_evidence_or_already_reconciling_are_untouched(tmp_path, monkeypatch):
    """A one-game walk-on (no season row anywhere) and a provider game that reconciles are never altered."""
    pb = [_player_box(1, 1, 2, [(11, "A", 4), (99, "Walk On", 6)], home=1), _player_box(1, 2, 1, [(21, "X", 6)], home=1)]
    tb = [_team_box(1, 1, 2, 10, ftm=10), _team_box(1, 2, 1, 6, ftm=6)]
    season = [(11, "A", 1, 3), (21, "X", 2, 3)]
    rows, rep = _team_case(tmp_path, monkeypatch, pb, tb, season, [(1, 1, [(11, "A")], 20), (2, 2, [(21, "X")], 30)])
    assert sorted((r["athleteId"], r["team_id"]) for r in rows["player_game_stats"] if r["game_id"] == "1") == \
        [("11", "1"), ("21", "2"), ("99", "1")]
    assert rep["team_assignment"]["repaired"] == rep["team_assignment"]["quarantined"] == 0


def test_same_name_records_follow_the_canonical_ambiguity_policy_and_are_not_merged(tmp_path, monkeypatch):
    """Two different CBBD records with one name in a team-game (provider double listing) both stay staged; the canonical
    build leaves them UNRESOLVED_AMBIGUOUS. The route reports them; it never merges records."""
    pb = [_player_box(1, 1, 2, [(31, "Same Name", 2), (32, "Same Name", 2), (11, "A", 6)], home=1)]
    rows, rep = _stage(tmp_path, monkeypatch, [("games", W, [_game(1, "final")]), ("games/players", W, pb)])
    assert sorted(r["athleteId"] for r in rows["player_game_stats"]) == ["11", "31", "32"]
    assert rep["same_name_records"] == [{"game_id": "1", "team_id": "1", "name": "Same Name", "records": 2,
                                         "athleteIds": ["31", "32"], "identical_stat_lines": True}]


def test_route_reads_only_the_registered_starter_pack_path_and_no_sibling_folder():
    """No automatic discovery: the only path outside the repository is the registered Starter Pack root."""
    import inspect
    from cbb_platform import cbbd_box_route_reconcile as R
    assert C.STARTER_PACK_ROOT == C.PROJECT_ROOT.parent / "CBB Starter Pack" / "starter_pack"
    for mod in (B, R):
        src = inspect.getsource(mod)
        assert "PROJECT_ROOT.parent" not in src and "Preseason" not in src and ".iterdir()" not in src
