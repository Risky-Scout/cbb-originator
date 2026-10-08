"""Stage 2.1 -- canonical game identity remediation. Numbers map to the directive's TEST list."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from cbb_platform import config as C, game_identity as G, pit
from tests.conftest import scalar

TRUE_DUP_PAIRS_EXPECTED = 41
POSSIBLE = {(71900, 71901), (72094, 72095), (72671, 72672), (72723, 72724), (72765, 72766), (74035, 74036)}


@pytest.fixture(scope="module")
def amap(db):
    cur = db.execute("SELECT * FROM metadata.game_alias_map")
    cols = [c[0] for c in cur.description]
    return {r[1]: dict(zip(cols, r)) for r in cur.fetchall()}


def _cid(db, gid):
    return scalar(db, "SELECT canonical_game_id FROM core.games WHERE source_record_key = ?", [f"id={gid}"])


# ------------------------------------------------------------------ 1. TRUE duplicates
def test_every_true_duplicate_pair_resolves_to_one_trusted_contest(db, amap):
    rows = list(csv.DictReader((C.QA / "game_identity_candidates.csv").open()))
    true_pairs = [(int(r["game_a"]), int(r["game_b"])) for r in rows if r["classification"] == "TRUE_DUPLICATE"]
    assert len(true_pairs) == TRUE_DUP_PAIRS_EXPECTED
    for a, b in true_pairs:
        ra, rb = amap[str(a)], amap[str(b)]
        assert {ra["resolution_class"], rb["resolution_class"]} == {"CANONICAL", "DUPLICATE_ALIAS"}, (a, b)
        rep = ra if ra["resolution_class"] == "CANONICAL" else rb
        alias = rb if rep is ra else ra
        assert alias["resolved_canonical_game_id"] == rep["source_canonical_game_id"]
        trusted = scalar(db, "SELECT count(*) FROM flagship.games WHERE canonical_game_id IN (?, ?)",
                         [ra["source_canonical_game_id"], rb["source_canonical_game_id"]])
        assert trusted == 1, (a, b)
        # priority 1: the representative is the box-attached record
        assert scalar(db, "SELECT count(*) FROM fact.team_games WHERE canonical_game_id = ?", [rep["source_canonical_game_id"]]) == 2
        assert scalar(db, "SELECT count(*) FROM fact.team_games WHERE canonical_game_id = ?", [alias["source_canonical_game_id"]]) == 0


# ------------------------------------------------------------------ 2. POSSIBLE duplicates
def test_possible_duplicates_follow_the_architect_adjudication(db, amap):
    for a, b in POSSIBLE:
        ra, rb = amap[str(a)], amap[str(b)]
        assert ra["adjudication_method"] == rb["adjudication_method"] == G.RULING
        rep = ra if ra["resolution_class"] == "CANONICAL" else rb
        alias = rb if rep is ra else ra
        assert alias["resolution_class"] == "DUPLICATE_ALIAS" and alias["resolved_canonical_game_id"] == rep["source_canonical_game_id"]
        h, aw, hp, ap = db.execute("""SELECT th.school, ta.school, g.home_points, g.away_points FROM core.games g
            JOIN core.teams th ON th.canonical_team_id = g.home_team_id JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
            WHERE g.canonical_game_id = ?""", [rep["source_canonical_game_id"]]).fetchone()
        assert {h: hp, aw: ap} == G.POSSIBLE_RULINGS[frozenset({a, b})]


def test_contradicting_adjudication_stops_the_build(db, monkeypatch):
    """A ruling that contradicts the box-attached record must STOP, never be silently overridden."""
    import duckdb
    bad = dict(G.POSSIBLE_RULINGS)
    bad[frozenset({71900, 71901})] = {"Morgan State": 59, "UTSA": 71}
    monkeypatch.setattr(G, "POSSIBLE_RULINGS", bad)
    con = duckdb.connect()
    con.execute(f"ATTACH '{C.DUCKDB_PATH}' AS src (READ_ONLY)")
    for t in ("core.games", "core.teams", "fact.team_games", "fact.player_games"):
        s, n = t.split(".")
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}" + (" WHERE season BETWEEN 2012 AND 2013" if n in ("games", "team_games", "player_games") else ""))
    with pytest.raises(G.GameIdentityStop):
        G.resolve(con)


# ------------------------------------------------------------------ 3. corrupt record
def test_118539_is_preserved_raw_but_quarantined(db, amap):
    r = amap["118539"]
    assert r["resolution_class"] == "SOURCE_CORRUPT_MISATTRIBUTED" and r["excluded_from_trusted_games"]
    assert r["resolved_canonical_game_id"] is None
    assert scalar(db, "SELECT count(*) FROM core.games WHERE source_record_key = 'id=118539'") == 1        # raw kept
    assert scalar(db, "SELECT count(*) FROM flagship.games WHERE source_record_key = 'id=118539'") == 0
    assert scalar(db, "SELECT count(*) FROM metadata.trusted_game_map WHERE source_canonical_game_id = ?", [r["source_canonical_game_id"]]) == 0
    assert amap["118538"]["resolution_class"] == "CANONICAL"
    # the 81-78 score belongs to a separately represented real contest that stays trusted (USC vs UAB, 118601)
    assert scalar(db, """SELECT count(*) FROM flagship.games WHERE source_record_key = 'id=118601'
                         AND home_points = 81 AND away_points = 78""") == 1
    assert "118601" in r["adjudication_evidence"]


# ------------------------------------------------------------------ 4/5/6. map integrity
def test_every_alias_maps_to_exactly_one_surviving_canonical_game(db, amap):
    for r in amap.values():
        if r["resolution_class"] == "DUPLICATE_ALIAS":
            assert r["resolved_canonical_game_id"] is not None
            assert scalar(db, "SELECT count(*) FROM flagship.games WHERE canonical_game_id = ?", [r["resolved_canonical_game_id"]]) == 1


def test_no_self_alias_and_no_chain(db, amap):
    by_cid = {r["source_canonical_game_id"]: r for r in amap.values()}
    for r in amap.values():
        if r["resolution_class"] == "DUPLICATE_ALIAS":
            assert r["resolved_canonical_game_id"] != r["source_canonical_game_id"]
            assert by_cid[r["resolved_canonical_game_id"]]["resolution_class"] == "CANONICAL"   # target is terminal
        elif r["resolution_class"] in ("CANONICAL", "LEGITIMATE_DISTINCT_GAME"):
            assert r["resolved_canonical_game_id"] == r["source_canonical_game_id"]
    assert scalar(db, "SELECT count(*) - count(DISTINCT source_canonical_game_id) FROM metadata.trusted_game_map") == 0


def test_all_48_questionable_pairs_are_explicitly_adjudicated(amap):
    rows = [r for r in csv.DictReader((C.QA / "game_identity_candidates.csv").open())
            if r["classification"] != "LEGITIMATE_DISTINCT_GAME"]
    assert len(rows) == 48
    for r in rows:
        for g in (r["game_a"], r["game_b"]):
            assert amap[g]["resolution_class"] in ("CANONICAL", "DUPLICATE_ALIAS", "SOURCE_CORRUPT_MISATTRIBUTED")
    assert sum(1 for r in amap.values() if r["resolution_class"] == "UNRESOLVED") == 0


# ------------------------------------------------------------------ 7. trusted games unique
def test_trusted_games_contain_no_duplicate_contest(db):
    assert scalar(db, """SELECT count(*) FROM (SELECT season, least(home_team_id, away_team_id), greatest(home_team_id, away_team_id),
                         scheduled_tip_utc FROM flagship.games WHERE scheduled_tip_utc IS NOT NULL GROUP BY ALL HAVING count(*) > 1)""") == 0
    lo, hi = C.AUDITED_SEASONS
    assert scalar(db, "SELECT count(*) FROM flagship.games") == scalar(
        db, f"SELECT count(*) FROM core.games WHERE season BETWEEN {lo} AND {hi}") - scalar(
        db, "SELECT count(*) FROM metadata.game_alias_map WHERE excluded_from_trusted_games")
    assert scalar(db, f"SELECT count(*) FROM flagship.games WHERE season NOT BETWEEN {lo} AND {hi}") == 0


# ------------------------------------------------------------------ 8/9/10. fact preservation
@pytest.mark.parametrize("raw,trusted", [("fact.player_games", "flagship.player_game_outcomes"),
                                         ("fact.team_games", "flagship.team_game_outcomes"),
                                         ("fact.participation", "flagship.participation")])
def test_box_facts_are_not_lost(db, raw, trusted):
    """Stage 2.1 invariant "the canonical player-game count never changes" held through v2.3. It is SUPERSEDED for
    fact.player_games / fact.participation by the v2.4 team-attribution remediation (docs/ARCHITECTURE_PRECEDENCE.md,
    docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md §4): v2.0-v2.3 carried 154 canonical rows that were opponent-side COPIES of
    real box lines (mirrored Starter Pack games), and v2.4 moves exactly those copies to qa.identity_remediation_quarantine.
    The replacement asserts that no source record and no real appearance is lost; every removal is a proven mirror copy
    (tests/test_stage2_1.py::test_every_removed_player_game_is_an_audited_mirror_copy)."""
    before = json.loads((C.QA / "game_identity_before_counts_v2.0.json").read_text())["counts"][raw]
    mirror_copies = scalar(db, """SELECT count(*) FROM qa.identity_remediation_quarantine
                                  WHERE table_name = 'fact.player_games' AND rule_id = 'DEMIRROR_RECONCILED_GAMES'""")
    removed_other = scalar(db, """SELECT count(*) FROM qa.identity_remediation_quarantine
                                  WHERE table_name = 'fact.player_games' AND rule_id <> 'DEMIRROR_RECONCILED_GAMES'""")
    assert removed_other == 0                                                  # no other rule may remove a box fact
    if raw == "fact.player_games":
        # every v2.0 source box record lives in exactly one place: the fact table or the mirror-copy quarantine
        assert scalar(db, """SELECT count(*) FROM (SELECT source_file, source_record_key FROM fact.player_games UNION ALL
                             SELECT json_extract_string(row_json, '$.source_file'), json_extract_string(row_json, '$.source_record_key')
                             FROM qa.identity_remediation_quarantine WHERE table_name = 'fact.player_games')""") == before
        assert scalar(db, """SELECT count(*) FROM fact.player_games p JOIN qa.identity_remediation_quarantine q
                             ON q.table_name = 'fact.player_games' AND p.source_record_key = json_extract_string(q.row_json, '$.source_record_key')
                             AND p.source_file = json_extract_string(q.row_json, '$.source_file')""") == 0
        before = before - mirror_copies
    if raw == "fact.participation":
        # SEASON_STATS_MEMBERSHIP candidates legitimately move with identity repairs; the BOX-derived participation
        # rows are exactly one per canonical player-game (none for a quarantined mirror copy, none missing)
        n_box = scalar(db, "SELECT count(*) FROM fact.participation WHERE candidate_source = 'BOX_SCORE_ROW'")
        assert n_box == scalar(db, "SELECT count(*) FROM fact.player_games") == \
            json.loads((C.QA / "game_identity_before_counts_v2.0.json").read_text())["counts"]["fact.player_games"] - mirror_copies
        assert scalar(db, """SELECT count(*) FROM fact.player_games x WHERE NOT EXISTS (SELECT 1 FROM fact.participation p
                             WHERE p.candidate_source = 'BOX_SCORE_ROW' AND p.source_file = x.source_file
                             AND p.source_record_key = x.source_record_key)""") == 0
        assert scalar(db, """SELECT count(*) FROM (SELECT source_file, source_record_key FROM fact.participation
                             WHERE candidate_source = 'BOX_SCORE_ROW' GROUP BY ALL HAVING count(*) > 1)""") == 0
        before = scalar(db, f"SELECT count(*) FROM {raw}")
    assert scalar(db, f"SELECT count(*) FROM {raw}") == before               # canonical facts = source facts - proven copies
    assert scalar(db, f"SELECT count(*) FROM {trusted}") == before            # every one reaches a trusted game
    assert scalar(db, f"SELECT count(*) FROM {trusted} WHERE canonical_game_id NOT IN (SELECT canonical_game_id FROM flagship.games)") == 0


def test_every_removed_player_game_is_an_audited_mirror_copy(db):
    """Each removed canonical player-game is a mirror copy: its raw source record has a twin in the same source game, same
    player name and identical 18-stat box vector, filed under the other team; that twin survives exactly once as a
    RESOLVED canonical row with a player id, both decisions are in the audit table, and both teams of every repaired game
    reconcile. Raw source rows are unchanged (Stage 1 inventory = build-time source manifest = file contents now)."""
    box = ("minutes", "points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta", "orb", "drb", "reb",
           "ast", "stl", "blk", "tov", "pf")
    same_box = " AND ".join(f"(json_extract_string(q.row_json, '$.{c}'))::INT IS NOT DISTINCT FROM s.{c}" for c in box)
    copies = scalar(db, "SELECT count(*) FROM qa.identity_remediation_quarantine WHERE table_name = 'fact.player_games'")
    assert copies > 0
    # the surviving twin: same game, same source name, identical box line, the OTHER team, resolved, exactly one
    assert scalar(db, f"""SELECT count(*) FROM qa.identity_remediation_quarantine q WHERE q.table_name = 'fact.player_games' AND
        (SELECT count(*) FROM fact.player_games s WHERE s.canonical_game_id = q.canonical_game_id
           AND s.source_player_name = json_extract_string(q.row_json, '$.source_player_name')
           AND s.canonical_team_id <> q.canonical_team_id AND s.identity_status = 'RESOLVED' AND s.canonical_player_id IS NOT NULL
           AND {same_box}) = 1""") == copies
    # audit: a QUARANTINE decision per copy, a KEEP per survivor, all in games logged REMEDIATED_RECONCILED
    assert scalar(db, """SELECT count(*) FROM qa.identity_remediation_quarantine q JOIN metadata.player_game_team_attribution a
                         ON a.player_game_key = q.record_key AND a.decision = 'QUARANTINE'
                         WHERE q.table_name = 'fact.player_games'""") == copies
    assert scalar(db, "SELECT count(*) FROM metadata.player_game_team_attribution WHERE decision = 'KEEP'") == copies
    assert scalar(db, """SELECT count(*) FROM qa.identity_remediation_quarantine q WHERE q.table_name = 'fact.player_games'
                         AND q.canonical_game_id NOT IN (SELECT canonical_game_id FROM metadata.team_attribution_resolution
                                                         WHERE decision = 'REMEDIATED_RECONCILED' AND reconciles)""") == 0
    # no copy reaches a trusted view
    assert scalar(db, """SELECT count(*) FROM flagship.player_game_outcomes o JOIN qa.identity_remediation_quarantine q
                         ON q.table_name = 'fact.player_games' AND o.player_game_key = q.record_key""") == 0
    # raw source rows unchanged: Stage 1 inventory == build-time source manifest == the files now
    inv = {r["relative_path"]: int(r["row_count"]) for r in csv.DictReader((C.QA / "starter_pack_inventory.csv").open())
           if r["family"] == "player_game_stats"}
    built = dict(db.execute("""SELECT source_path, row_count FROM metadata.source_manifest
                               WHERE source_path LIKE 'data/player_game_stats/%'""").fetchall())
    now = dict(db.execute(f"""SELECT 'data/player_game_stats/' || split_part(filename, '/', -1), count(*) FROM read_csv(
        '{C.STARTER_PACK_DATA}/player_game_stats/*.csv', all_varchar=true, union_by_name=true, filename=true) GROUP BY 1""").fetchall())
    assert inv == built == now and len(now) == 21


@pytest.mark.parametrize("raw,trusted,key", [
    ("fact.plays", "flagship.plays", "canonical_game_id, source_play_id"),
    ("fact.shots", "flagship.shots_trusted", "canonical_game_id, source_play_id"),
    ("fact.substitutions", "flagship.substitutions", "canonical_game_id, source_athlete_id, stint_number"),
    ("fact.lineup_stats", "flagship.lineup_stats", "canonical_game_id, canonical_team_id, lineup_hash")])
def test_event_facts_are_neither_lost_nor_duplicated_by_aliasing(db, raw, trusted, key):
    before = json.loads((C.QA / "game_identity_before_counts_v2.0.json").read_text())["counts"][raw]
    assert scalar(db, f"SELECT count(*) FROM {raw}") == before
    assert scalar(db, f"SELECT count(*) - count(DISTINCT ({key})) FROM {trusted}") == 0
    assert scalar(db, f"""SELECT count(*) FROM {raw} f JOIN metadata.game_alias_map m
                          ON m.source_canonical_game_id = f.canonical_game_id AND m.excluded_from_trusted_games""") == 0


def test_fact_safety_guard_refuses_facts_on_corrupt_records(db):
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE SCHEMA metadata; CREATE SCHEMA fact")
    con.execute("""CREATE TABLE metadata.game_alias_map AS SELECT 'x' AS source_game_id, 'SOURCE_CORRUPT_MISATTRIBUTED' AS resolution_class,
                   'G-bad' AS source_canonical_game_id, NULL::VARCHAR AS resolved_canonical_game_id, TRUE AS excluded_from_trusted_games""")
    for t in G.FACT_TABLES:
        con.execute(f"CREATE TABLE {t} (canonical_game_id VARCHAR)")
    con.execute("INSERT INTO fact.plays VALUES ('G-bad')")
    with pytest.raises(G.GameIdentityStop):
        G.assert_fact_safety(con)


# ------------------------------------------------------------------ 11. affected team-seasons recomputed
def test_every_affected_team_season_is_recomputed_through_trusted_games(db):
    impact = list(csv.DictReader((C.QA / "game_identity_aggregate_impact.csv").open()))
    summary = json.loads((C.QA / "game_identity_resolution.json").read_text())
    # 83 TRUE/POSSIBLE team-seasons (Stage 2) + USC & Indiana State 2004-05 (118539 now quarantined)
    assert len(impact) == summary["affected_team_seasons"] == 85
    for r in impact:
        removed = int(r["removed_duplicate_contributions"])
        assert removed >= 1 and int(r["old_games"]) - int(r["new_games"]) == removed, r
    # the derived table equals an independent recomputation over the trusted (since 2.2: played) relation, every team-season
    assert scalar(db, """SELECT count(*) FROM fact.team_season_game_derived d JOIN (
        SELECT team, season, count(*) n, count(*) FILTER (WHERE p > o) w, count(*) FILTER (WHERE p < o) l, sum(p) pf, sum(o) pa FROM (
            SELECT home_team_id team, season, home_points p, away_points o FROM flagship.played_games WHERE home_points IS NOT NULL AND away_points IS NOT NULL
            UNION ALL SELECT away_team_id, season, away_points, home_points FROM flagship.played_games WHERE home_points IS NOT NULL AND away_points IS NOT NULL)
        GROUP BY 1, 2) r ON r.team = d.canonical_team_id AND r.season = d.season
        WHERE (d.games_with_result, d.wins, d.losses, d.points_all_results, d.opp_points_all_results) IS DISTINCT FROM (r.n, r.w, r.l, r.pf, r.pa)""") == 0
    # same team-season set as v2.0, except team-seasons left with NO played game by Stage 2.2 (only 0-0 records)
    no_played = [r for r in csv.DictReader((C.QA / "game_status_aggregate_impact.csv").open()) if r["new_games"] == ""]
    assert scalar(db, "SELECT count(*) FROM fact.team_season_game_derived") == json.loads(
        (C.QA / "game_identity_before_counts_v2.0.json").read_text())["counts"]["fact.team_season_game_derived"] - len(no_played)


# ------------------------------------------------------------------ 12. PIT counts the contest once
def test_season_to_date_counts_each_duplicated_contest_once(db, amap):
    checked = 0
    for r in amap.values():
        if r["resolution_class"] != "CANONICAL":
            continue
        rep = r["source_canonical_game_id"]
        excluded = [x["source_canonical_game_id"] for x in amap.values()
                    if x["excluded_from_trusted_games"] and r["source_game_id"] in (x["notes"] or "").split(",")]
        season, tip, home, away = db.execute("SELECT season, scheduled_tip_utc, home_team_id, away_team_id FROM flagship.games "
                                             "WHERE canonical_game_id = ?", [rep]).fetchone()
        for team in (home, away):
            nxt = db.execute("""SELECT canonical_game_id FROM flagship.games WHERE season = ? AND ? IN (home_team_id, away_team_id)
                                AND scheduled_tip_utc > ? ORDER BY scheduled_tip_utc LIMIT 1""", [season, team, tip]).fetchone()
            if nxt is None:
                continue
            res = pit.team_results_to_date(db, team, nxt[0])
            assert res["game_ids"].count(rep) == 1, (rep, team)
            assert not set(excluded) & set(res["game_ids"]), (rep, team)
            raw_n, raw_excluded = db.execute("""SELECT count(*), count(*) FILTER (WHERE canonical_game_id IN (
                    SELECT source_canonical_game_id FROM metadata.game_alias_map WHERE excluded_from_trusted_games)
                    OR canonical_game_id IN (SELECT canonical_game_id FROM metadata.game_status_resolution
                                             WHERE excluded_from_played_games))   -- Stage 2.2 unplayed records
                FROM core.games WHERE season = ? AND ? IN (home_team_id, away_team_id) AND home_points IS NOT NULL
                  AND scheduled_tip_utc < (SELECT scheduled_tip_utc FROM core.games WHERE canonical_game_id = ?)""",
                [season, team, nxt[0]]).fetchone()
            assert raw_excluded >= len(excluded) >= 1
            assert res["games"] == raw_n - raw_excluded, (rep, team)          # no alias adds a game
            box = pit.team_season_to_date(db, team, nxt[0], pit_far_future())
            assert box["game_ids"].count(rep) <= 1 and not set(excluded) & set(box["game_ids"])
            checked += 1
    assert checked >= 80


def pit_far_future():
    from datetime import datetime, timezone
    return datetime(2100, 1, 1, tzinfo=timezone.utc)


def test_aliases_never_reach_any_flagship_relation(db, amap):
    excluded = [r["source_canonical_game_id"] for r in amap.values() if r["excluded_from_trusted_games"]]
    for (v,) in db.execute("""SELECT view_name FROM duckdb_views() WHERE schema_name = 'flagship'
                               AND sql ILIKE '%canonical_game_id%'""").fetchall():
        cols = [c[0] for c in db.execute(f"DESCRIBE flagship.{v}").fetchall()]
        if "canonical_game_id" in cols:
            assert scalar(db, f"SELECT count(*) FROM flagship.{v} WHERE canonical_game_id IN (SELECT unnest(?))", [excluded]) == 0, v


# ------------------------------------------------------------------ 13. deterministic mapping
def test_resolution_is_deterministic(db):
    """Re-running the resolver on the built inputs reproduces the stored map exactly (same hash)."""
    import duckdb
    con = duckdb.connect()
    con.execute(f"ATTACH '{C.DUCKDB_PATH}' AS src (READ_ONLY)")
    from cbb_platform.build_canonical import ID_MACROS
    con.execute(ID_MACROS)
    for t in ("core.games", "core.teams", "fact.team_games", "fact.player_games"):
        s, _ = t.split(".")
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    con.execute("CREATE SCHEMA IF NOT EXISTS metadata")
    con.execute("CREATE TABLE metadata.game_alias_map AS SELECT * FROM src.metadata.game_alias_map LIMIT 0")
    G.resolve(con)
    q = "SELECT * EXCLUDE (created_by_version) FROM {} ORDER BY source_game_id"
    assert con.execute(q.format("metadata.game_alias_map")).fetchall() == con.execute(q.format("src.metadata.game_alias_map")).fetchall()


# ------------------------------------------------------------------ manifest / policy / guards (14-17 also in Stage 1/2 suites)
def test_new_dataset_version_and_parent(db):
    # the Stage 2.1 dataset is preserved verbatim as an ancestor of the current version
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST_cbb_data_v2.1+292c2d2b1739.json").read_text())
    assert man["dataset_version"] == "cbb_data_v2.1+292c2d2b1739"
    assert man["parent_dataset_version"] == "cbb_data_v2.0+b00e8d3dc798"
    assert "cbb_data_v2.1+292c2d2b1739" in json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())["lineage"]
    parent = json.loads((C.MANIFESTS / "DATASET_MANIFEST_cbb_data_v2.0+b00e8d3dc798.json").read_text())
    assert parent["dataset_version"] == "cbb_data_v2.0+b00e8d3dc798"                # parent identity preserved
    gi = man["game_identity"]
    assert gi["aliases"] == 47 and gi["quarantined_corrupt"] == 1 and gi["affected_team_seasons"] == 85
    assert gi["canonical_trusted_games"] == scalar(db, "SELECT count(*) FROM flagship.games")   # unchanged by 2.2
    assert man["excluded_lockbox_season"] == "2025-26" and man["lockbox_touched"] is False


def test_wallclock_policy_is_untrusted_mixed():
    w = json.loads((C.QA / "cbbd_wallclock_semantics.json").read_text())
    assert w["policy"] == "UNTRUSTED_MIXED" and w["approved_for_pit_or_latency"] is False
    for src in ("pit.py", "game_identity.py", "build_canonical.py"):
        assert "wallclock" not in (C.PROJECT_ROOT / "cbb_platform" / src).read_text()


def test_lockbox_untouched_by_remediation(db):
    assert scalar(db, "SELECT count(*) FROM metadata.game_alias_map WHERE season >= 2026") == 0
    assert scalar(db, "SELECT count(*) FROM flagship.games WHERE season >= 2026") == 0
