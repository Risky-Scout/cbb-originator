"""Stage 1 final unblock: global lockbox + season normalization, credential secrecy, live CBBD
normalization, athleteId classification, onFloor / lineup / substitution validation, date-bounded PIT,
player-season authority."""
import json

import pytest

from cbb_platform import cbbd_identity as ident, cbbd_validate as V, credentials, lockbox, raw_store, source_fetch
from cbb_platform.adapters import bdl, cbbd
from tests.conftest import scalar

L = lockbox.LockboxViolation


# =========================================================================== season normalization
@pytest.mark.parametrize("provider,value,canonical", [
    ("cbbd", 2026, 2026), ("starter_pack", 2026, 2026), ("torvik", 2026, 2026), ("bdl", 2025, 2026),
    ("cbbd", 2025, 2025), ("bdl", 2024, 2025), ("bdl", 2026, 2027), ("cbbd", "2026", 2026), ("cbbd", 2026.0, 2026),
    ("bdl", "2025-26", 2026), ("cbbd", "2025-26", 2026), ("torvik", "2025-2026", 2026), ("bdl", "2024-25", 2025),
])
def test_canonical_season_normalization(provider, value, canonical):
    assert lockbox.canonical_season(provider, value) == canonical


def test_same_integer_means_different_seasons_by_provider():
    lockbox.check_request("cbbd", "games", {"season": 2025})                     # CBBD 2025 = 2024-25: allowed
    with pytest.raises(L):
        lockbox.check_request("bdl", "games", {"seasons[]": [2025]})             # BDL 2025 = 2025-26: lockbox
    lockbox.check_request("bdl", "games", {"seasons[]": [2024]})                 # BDL 2024 = 2024-25: allowed


def test_unknown_provider_and_bad_labels_fail_closed():
    with pytest.raises(L):
        lockbox.canonical_season("kenpom", 2026)
    with pytest.raises(L):
        lockbox.check_request("kenpom", "ratings", {"season": 2024})
    with pytest.raises(L):
        lockbox.canonical_season("cbbd", "2025-27")


@pytest.mark.parametrize("d,season", [("2025-08-01", 2026), ("2026-07-31", 2026), ("2025-07-31", 2025),
                                      ("20251115", 2026), ("2024-11-04T23:00:00Z", 2025)])
def test_season_of_date(d, season):
    assert lockbox.season_of_date(d) == season


# =========================================================================== provider-independent lockbox
LOCKBOX_GAME = lambda gid: 2026          # resolver: this game id is a 2025-26 game
BLOCKED = [
    # (provider, endpoint, params, data kind)
    ("cbbd", "games", {"season": 2026}, "games"),
    ("cbbd", "games", {"season": "2025-26", "team": "Purdue"}, "games (label)"),
    ("cbbd", "games/players", {"season": 2026, "team": "Purdue"}, "player stats"),
    ("cbbd", "games/teams", {"startDateRange": "2025-11-03", "endDateRange": "2025-11-30"}, "team stats"),
    ("cbbd", "plays/game", {"gameId": 777}, "PBP"),
    ("cbbd", "lineups/game", {"gameId": 777}, "lineups"),
    ("cbbd", "substitutions/game", {"gameId": 777}, "substitutions"),
    ("cbbd", "stats/player/season", {"season": 2026, "startDateRange": "2025-11-01", "endDateRange": "2025-12-01"}, "player season stats"),
    ("cbbd", "stats/team/season", {"season": "2025-2026"}, "team season stats"),
    ("bdl", "games", {"seasons[]": [2025]}, "games"),
    ("bdl", "games", {"start_date": "2026-03-01", "end_date": "2026-03-31"}, "historical results"),
    ("bdl", "player_stats", {"seasons[]": [2024, 2025]}, "player stats (list containing lockbox)"),
    ("bdl", "player_stats", {"game_ids[]": [777]}, "player stats by game"),
    ("bdl", "team_stats", {"dates[]": ["2026-01-15"]}, "team stats"),
    ("bdl", "plays", {"game_id": 777}, "PBP"),
    ("bdl", "odds", {"dates[]": ["2026-02-01"]}, "market-only (blocked for simplicity)"),
    ("torvik", "player_game_stats", {"year": 2026}, "player stats"),
    ("torvik", "team_results", {"begin": "20251101", "end": "20260401"}, "historical results"),
    ("torvik", "game_results", {"year": "2025-26"}, "games"),
]


@pytest.mark.parametrize("provider,endpoint,params,kind", BLOCKED, ids=[f"{b[0]}-{b[3]}" for b in BLOCKED])
def test_lockbox_blocks_every_provider_and_data_kind(provider, endpoint, params, kind):
    with pytest.raises(L):
        lockbox.check_request(provider, endpoint, params, known_game_season=LOCKBOX_GAME)


@pytest.mark.parametrize("provider,endpoint,params", [
    ("bdl", "games", {"seasons[]": [2026]}),              # 2026-27: current/future, separate rules
    ("bdl", "games", {}),                                 # unbounded: provider default is "now"
    ("torvik", "team_results", {"begin": "20250301"}),    # open-ended range
    ("cbbd", "stats/team/season", {"team": "Purdue"}),
])
def test_lockbox_blocks_future_and_unbounded(provider, endpoint, params):
    with pytest.raises(L):
        lockbox.check_request(provider, endpoint, params, known_game_season=lambda gid: None)


@pytest.mark.parametrize("provider,endpoint,params", [
    ("cbbd", "plays/game", {"gameId": 300}), ("bdl", "player_stats", {"seasons[]": [2023, 2024]}),
    ("bdl", "games", {"dates[]": ["2025-03-01"]}), ("torvik", "team_results", {"year": 2025}),
    ("cbbd", "conferences", {}), ("bdl", "teams", {}), ("sharp_stats", "injuries", {}),
])
def test_development_reference_and_availability_requests_are_allowed(provider, endpoint, params):
    lockbox.check_request(provider, endpoint, params, known_game_season=lambda gid: 2025)


def test_response_guard_normalizes_provider_seasons():
    lockbox.check_response("cbbd", [{"season": 2025}])                         # 2024-25
    with pytest.raises(L):
        lockbox.check_response("bdl", {"data": [{"season": 2025}]})            # BDL 2025 = 2025-26
    with pytest.raises(L):
        lockbox.check_response("bdl", {"data": [{"game": {"date": "2025-12-01", "season": 2024}}]})   # nested date
    with pytest.raises(L):
        lockbox.check_response("torvik", [{"year": 2026}])


class _Resp:
    def __init__(self, body, status=200):
        self.content, self.status_code = json.dumps(body).encode(), status


def test_bdl_fetch_refuses_lockbox_before_network_and_storage(monkeypatch, raw_root):
    monkeypatch.setenv("BDL_API_KEY", "x" * 10)
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: pytest.fail("network call attempted"))
    with pytest.raises(L):
        bdl.fetch("player_stats", {"seasons[]": [2025]})
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: _Resp({"data": [{"id": 1, "season": 2025}], "meta": {}}))
    with pytest.raises(L):
        bdl.fetch("games", {"seasons[]": [2024]})                      # request legal, response is not
    assert not raw_root.exists() or not any(raw_root.rglob("*"))


def test_every_network_adapter_uses_the_global_guard():
    import inspect
    for module in (cbbd, bdl):
        src = inspect.getsource(module.fetch)
        assert "source_fetch.get(" in src and "lockbox.check_request(" in src, module.__name__
        assert "requests.get(" not in src, module.__name__


# =========================================================================== credential secrecy
SECRET = "SECRET-abc123-not-for-disk"


def test_credential_cleaning_strips_only_terminal_control_sequences(monkeypatch):
    monkeypatch.setenv("CBBD_API_KEY", "ab\x1b[Bcd\x1b[B+/=")
    assert credentials.get("CBBD_API_KEY") == "abcd+/="
    monkeypatch.delenv("CBBD_API_KEY")
    with pytest.raises(RuntimeError, match="BLOCKED_CREDENTIAL: CBBD_API_KEY is not set"):
        credentials.get("CBBD_API_KEY")
    monkeypatch.setenv("CBBD_API_KEY", "bad key with spaces")
    with pytest.raises(RuntimeError) as e:
        credentials.get("CBBD_API_KEY")
    assert "bad key" not in str(e.value)


def test_http_errors_never_carry_credentials_or_bodies(monkeypatch):
    monkeypatch.setenv("CBBD_API_KEY", SECRET)
    monkeypatch.setattr(source_fetch.requests, "get", lambda *a, **k: _Resp({"echo": SECRET}, status=401))
    with pytest.raises(source_fetch.SourceHTTPError) as e:
        cbbd.fetch("games", {"season": 2025, "team": "Purdue"})
    assert SECRET not in str(e.value) and "401" in str(e.value)

    def boom(*a, **k):
        raise source_fetch.requests.ConnectionError(f"failed with header Bearer {SECRET}")
    monkeypatch.setattr(source_fetch.requests, "get", boom)
    with pytest.raises(RuntimeError) as e:
        cbbd.fetch("games", {"season": 2025, "team": "Purdue"})
    assert SECRET not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


# =========================================================================== live CBBD normalization (built database)
def _raw(endpoint):
    """Stage 1 SMOKE payloads only. Stage 2 backfill payloads carry a `request` metadata block and are
    covered by tests/test_stage2.py; these Stage 1 assertions keep their original scope unchanged."""
    return [(json.loads(raw_store.read_payload(p)), m) for p, m in raw_store.records("cbbd")
            if m["endpoint"] == endpoint and "request" not in m]


def _smoke_games(db):
    gids = sorted({m["params"]["gameId"] for ep in ("plays/game", "substitutions/game", "lineups/game") for _, m in _raw(ep)})
    return [scalar(db, "SELECT 'G-' || substr(sha256('CBBD|game|' || ?), 1, 16)", [str(g)]) for g in gids]


def test_live_pbp_substitutions_lineups_normalized_completely(db):
    g = _smoke_games(db)
    q = lambda sql: scalar(db, sql + " WHERE canonical_game_id IN (SELECT unnest(?))", [g])
    assert q("SELECT count(*) FROM fact.plays") == sum(len(b) for b, _ in _raw("plays/game")) > 0
    assert q("SELECT count(*) FROM fact.substitutions") == sum(len(b) for b, _ in _raw("substitutions/game")) > 0
    assert q("SELECT count(*) FROM fact.lineup_stats") == sum(len(b) for b, _ in _raw("lineups/game")) > 0
    assert q("SELECT count(*) FROM fact.shots") == sum(1 for b, _ in _raw("plays/game") for p in b if p.get("shotInfo"))
    # play_sequence is a dense 1..n per game (every game, not only the smoke sample)
    assert scalar(db, """SELECT count(*) FROM (SELECT canonical_game_id, max(play_sequence) mx, count(*) n,
                         count(DISTINCT play_sequence) d FROM fact.plays GROUP BY 1) WHERE mx <> n OR d <> n""") == 0


def test_live_player_ids_resolve_to_persistent_players(db):
    g = _smoke_games(db)
    assert scalar(db, """SELECT count(*) FROM (SELECT unnest(on_floor_player_ids) x FROM fact.plays WHERE canonical_game_id IN (SELECT unnest(?)))
                         WHERE x NOT IN (SELECT canonical_player_id FROM core.players)""", [g]) == 0
    assert scalar(db, "SELECT count(*) FROM fact.substitutions WHERE canonical_player_id IS NULL AND canonical_game_id IN (SELECT unnest(?))", [g]) == 0
    assert scalar(db, """SELECT count(*) FROM (SELECT unnest(player_ids) x FROM fact.lineup_stats WHERE canonical_game_id IN (SELECT unnest(?)))
                         WHERE x NOT IN (SELECT canonical_player_id FROM core.players)""", [g]) == 0


def test_live_plays_join_canonical_games_and_scores(db):
    g = _smoke_games(db)
    assert scalar(db, "SELECT count(*) FROM fact.plays p WHERE canonical_game_id IN (SELECT unnest(?)) AND NOT EXISTS (SELECT 1 FROM core.games g "
                      "WHERE g.canonical_game_id = p.canonical_game_id AND g.season <= 2025)", [g]) == 0
    assert scalar(db, """SELECT count(*) FROM (SELECT canonical_game_id, arg_max(home_score, play_sequence) h,
                         arg_max(away_score, play_sequence) a FROM fact.plays WHERE canonical_game_id IN (SELECT unnest(?)) GROUP BY 1) p
                         JOIN core.games g USING (canonical_game_id)
                         WHERE p.h <> g.home_points OR p.a <> g.away_points""", [g]) == 0


# =========================================================================== live athleteId classification
@pytest.fixture(scope="module")
def verification():
    return json.loads(ident.RESULT_JSON.read_text())


def test_live_athlete_id_classification_is_resolved_and_reproducible(verification):
    assert verification["status"] == "MEASURED"
    assert verification["classification"] in (ident.PERSISTENT, ident.NON_PERSISTENT)       # never MIXED_UNRESOLVED
    rows = [r for b, _ in _raw("games/players") for r in ident.player_rows(b)]
    assert ident.classify(rows)[0] == verification["classification"]
    assert verification["evidence"]["within_season"]["multi_game_players"] >= ident.MIN_MULTI_GAME


def test_live_persistence_evidence_against_independent_anchors(verification):
    if verification["classification"] != ident.PERSISTENT:
        pytest.skip("only meaningful for PERSISTENT_PLAYER_ID")
    assert verification["roster_agreement"]["mismatches"] == []
    sf = verification["season_file_compatibility"]
    assert sf["agreement"] >= ident.MIN_ANCHOR_AGREEMENT
    assert all(m["cause"] != "ID_DIFFERS_FROM_SEASON_FILE" for m in sf["mismatches"])   # every mismatch explained
    assert verification["evidence"]["multi_team_contexts"]["names_with_different_ids"] == 0


# =========================================================================== onFloor / lineups / substitutions
def _floor(home=5, away=5):
    return [{"id": i, "team": "H"} for i in range(home)] + [{"id": 100 + i, "team": "A"} for i in range(away)]


@pytest.mark.parametrize("on_floor,state", [
    (_floor(), "VALID_5V5"), ([], "EMPTY"), (None, "EMPTY"), (_floor(4, 5), "PARTIAL"), (_floor(5, 0), "PARTIAL"),
    (_floor(6, 5), "MALFORMED"), (_floor()[:-1] + [{"id": 0, "team": "A"}], "MALFORMED"),
    (_floor()[:-1] + [{"id": None, "team": "A"}], "MALFORMED"), (_floor() + [{"id": 999, "team": "X"}], "MALFORMED"),
])
def test_onfloor_state(on_floor, state):
    assert V.onfloor_state(on_floor) == state


def test_live_onfloor_measurements_match_the_raw_feed(verification):
    plays = {m["params"]["gameId"]: b for b, m in _raw("plays/game")}
    for gid, body in plays.items():
        states = verification["smoke"]["onfloor"][str(gid)]["states"]
        assert sum(states.values()) == len(body)
        assert states.get("VALID_5V5", 0) == sum(V.onfloor_state(p.get("onFloor")) == "VALID_5V5" for p in body)


@pytest.mark.parametrize("lineup,ok", [
    ({"idHash": "1-2-3-4-5", "athletes": [{"id": i} for i in (5, 3, 1, 2, 4)]}, True),
    ({"athletes": [{"id": i} for i in range(5)]}, True),
    ({"idHash": "1-2-3-4", "athletes": [{"id": i} for i in (1, 2, 3, 4)]}, False),
    ({"idHash": "1-1-2-3-4", "athletes": [{"id": i} for i in (1, 1, 2, 3, 4)]}, False),
    ({"idHash": "1-2-3-4-6", "athletes": [{"id": i} for i in (1, 2, 3, 4, 5)]}, False),
    ({"athletes": [{"id": None}] + [{"id": i} for i in range(4)]}, False),
])
def test_lineup_five_player_validity(lineup, ok):
    assert V.lineup_is_valid(lineup) is ok


def test_live_lineups_are_valid_five_player_units():
    units = [u for b, _ in _raw("lineups/game") for u in b]
    assert units and all(V.lineup_is_valid(u) for u in units)


@pytest.mark.parametrize("period,rem,elapsed", [(1, 1200, 0), (1, 0, 1200), (2, 1200, 1200), (2, 0, 2400),
                                                 (3, 300, 2400), (3, 0, 2700), (4, 150, 2850)])
def test_elapsed_seconds(period, rem, elapsed):
    assert V.elapsed_seconds(period, rem) == elapsed


def test_substitution_parsing_and_floor_reconstruction():
    rows = [{"gameId": 1, "teamId": 9, "team": "H", "athleteId": 7,
             "subIn": {"period": 1, "secondsRemaining": 1200}, "subOut": {"period": 2, "secondsRemaining": 600}},
            {"gameId": 1, "teamId": 9, "team": "H", "athleteId": 8,
             "subIn": {"period": 2, "secondsRemaining": 600}, "subOut": {"period": None, "secondsRemaining": None}}]
    st = V.parse_substitutions(rows)
    assert (st[0]["in"], st[0]["out"], st[0]["timing_complete"]) == (0, 1800, True)
    assert st[1]["timing_complete"] is False and st[1]["in"] is None
    assert V.floor_from_substitutions(st, "H", 1800) == {7}          # a play AT the sub moment belongs to the outgoing player
    assert V.floor_from_substitutions(st, "H", 1801) == set()        # incomplete stint never placed on the floor


def test_live_substitutions_parse_and_reconcile_with_onfloor(verification):
    subs = [r for b, _ in _raw("substitutions/game") for r in b]
    assert subs and all(s["timing_complete"] for s in V.parse_substitutions(subs))
    for gid, m in verification["smoke"]["substitutions"].items():
        if m["team_play_floor_sets_equal_onFloor"]["of"]:
            assert m["team_play_floor_sets_equal_onFloor"]["share"] >= 0.95, gid   # measured 0.98-0.99 at freeze


# =========================================================================== date-bounded PIT
def test_compare_primitives():
    assert V.compare_primitives({"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 9})["match"] is True
    r = V.compare_primitives({"a": 1, "b": 2}, {"a": 1, "b": 3})
    assert r["match"] is False and r["mismatched"] == ["b"]


def test_date_bounded_pit_result_is_reproducible_and_unambiguous(db, verification):
    payloads, params = ident.load_payloads(with_params=True)
    again = V.date_bounded_pit(payloads, params, db)
    stored = verification["date_bounded_pit"]
    assert again["classification"] == stored["classification"]
    assert again["reconciling_interpretations"] == stored["reconciling_interpretations"]
    if stored["classification"] == "VALIDATED_FOR_TESTED_FIELDS":
        assert len(stored["reconciling_interpretations"]) == 1
        assert len({w[2] for w in stored["windows"]}) >= 3        # discriminating end dates were tested


# =========================================================================== player-season authority
F = ["minutes", "points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta",
     "orb", "drb", "reb", "ast", "stl", "blk", "tov", "pf"]


def test_player_season_game_derived_recomputes_from_resolved_player_games(db):
    sums = ", ".join(f'sum("{f}") AS "{f}"' for f in F)
    bad = scalar(db, f"""SELECT count(*) FROM fact.player_season_game_derived d FULL JOIN (
            SELECT canonical_player_id, canonical_team_id, season, count(*) n, {sums} FROM fact.player_games
            WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2, 3) g USING (canonical_player_id, canonical_team_id, season)
        WHERE d.games IS DISTINCT FROM g.n OR {' OR '.join(f'd."{f}" IS DISTINCT FROM g."{f}"' for f in F)}""")
    assert bad == 0


def test_reconstructable_player_seasons_have_game_derived_authority(db):
    # every recorded conflict on a reconstructable player-season names GAME_DERIVED as authority ...
    assert scalar(db, "SELECT count(*) FROM qa.player_season_summary_conflicts WHERE identity_complete "
                      "AND authoritative_source <> 'GAME_DERIVED'") == 0
    # ... and where the derivation may be missing games, nothing silently wins
    assert scalar(db, "SELECT count(*) FROM qa.player_season_summary_conflicts WHERE NOT identity_complete "
                      "AND authoritative_source <> 'IDENTITY_GAP_NO_AUTHORITY'") == 0


def test_every_player_season_conflict_is_recorded(db):
    for f in ["games", "starts"] + F:
        n = scalar(db, f"""SELECT count(*) FROM fact.player_season_stats s LEFT JOIN fact.player_season_game_derived d
                           USING (canonical_player_id, canonical_team_id, season) WHERE s."{f}" IS DISTINCT FROM d."{f}" """)
        assert n == scalar(db, "SELECT count(*) FROM qa.player_season_summary_conflicts WHERE field = ?", [f]), f


def test_raw_player_season_summaries_are_preserved_unchanged(starter_db):
    db = starter_db
    assert scalar(db, """SELECT count(*) FROM fact.player_season_stats f JOIN (
            SELECT DISTINCT ON (season, teamId, athleteId) * FROM staging.sp_player_season_stats
            ORDER BY season, teamId, athleteId, conference NULLS LAST, filename) s
        ON f.canonical_player_id = cid('P', 'athlete', s.athleteId) AND f.canonical_team_id = cid('T', 'team', s.teamId)
           AND f.season = as_int(s.season)
        WHERE f.points IS DISTINCT FROM as_int(s.points) OR f.games IS DISTINCT FROM as_int(s.games)
           OR f.minutes IS DISTINCT FROM as_int(s.minutes)""") == 0


def test_flagship_exposes_player_season_game_derived(db):
    assert scalar(db, "SELECT count(*) FROM duckdb_views() WHERE schema_name = 'flagship' "
                      "AND view_name = 'player_season_game_derived'") == 1
