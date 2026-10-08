"""Pre-Model Integrity Closure v1 (Part 1): cluster-level identity remediation, dataset v2.4, governance freeze.

A. adversarial classifier tests (pure evidence -> class; independent of the audit's own output)
B. adversarial end-to-end tests of the team-attribution repair on synthetic worlds (empty canonical schema)
C. acceptance tests on the built v2.4 canonical database, Stage 3 stores and governance documents
"""
from __future__ import annotations

import csv
import hashlib
import json

import duckdb
import pytest

from cbb_platform import config as C, identity_cluster_audit as A, team_attribution as TA
from cbb_platform.stage3 import spec
from tests.conftest import scalar

V23 = "cbb_data_v2.3+7bef640cccde"
V23_MANIFEST_SHA256 = "6ee97887850cbd703082220da39c6f5bd9f3cedc976fd4f54443128eb21500bf"
W = "BETWEEN 2014 AND 2025"


# ============================================================================ A. classifier (pure)
def _in(**kw):
    return {"kind": "IN_SEASON_MULTI_TEAM", **kw}


def _link(**kw):
    return {"kind": "CROSS_SEASON_LINK", "cross_team": True, "gap": 1, **kw}


def test_whole_roster_under_wrong_team_is_a_team_attribution_defect():
    ev = _in(mirror_in_h2h=True, minority_games_all_h2h=True, h2h_reconciled=True, whole_team_cluster=True,
             provider_roster_both=True, box_teams_after=1)
    assert A.classify(ev)[:2] == ("TEAM_ATTRIBUTION_DEFECT", "HIGH")
    # the same whole-team cluster WITHOUT mirror evidence is never accepted as a transfer, even with roster continuity
    ev = _in(whole_team_cluster=True, provider_roster_both=True, box_teams_after=2)
    assert A.classify(ev)[0] == "UNRESOLVED"


def test_chronology_alone_is_never_identity_evidence():
    """The revoked Stage 3.1 rule: two teams in one season with non-overlapping dates and nothing else."""
    assert A.classify(_in(box_teams_after=2))[0] == "UNRESOLVED"
    assert A.classify(_link(roster_from=False, roster_to=False))[0] == "UNRESOLVED"
    assert A.classify(_link(gap=4, cross_team=False))[0] == "UNRESOLVED"


def test_ordinary_transfer_is_confirmed_only_with_positive_evidence():
    assert A.classify(_link(portal_record=True))[:2] == ("CONFIRMED_TRANSFER", "HIGH")
    assert A.classify(_link(roster_from=True, roster_to=True))[:2] == ("CONFIRMED_TRANSFER", "MEDIUM")
    # a whole-team-sized cross-season move needs an independent record per member
    assert A.classify(_link(roster_from=True, roster_to=True, whole_team_cluster=True))[0] == "UNRESOLVED"
    assert A.classify(_link(roster_from=True, roster_to=True, whole_team_cluster=True, portal_record=True))[0] == "CONFIRMED_TRANSFER"


def test_source_id_collision_and_overlapping_appearances():
    for ev in (_in(overlap_conflict=3, provider_roster_both=True, box_teams_after=2, portal_record=True),
               _link(overlap_conflict=1, portal_record=True)):
        assert A.classify(ev)[:2] == ("SOURCE_ID_COLLISION", "HIGH")
    # provider DOB is measured unreliable: on its own it never decides a collision (nor confirms an identity)
    assert A.classify(_link(dob_age_conflict="DOB 2004", roster_from=True, roster_to=True))[0] == "CONFIRMED_TRANSFER"
    assert A.classify(_link(eligibility_conflict="8 seasons", roster_from=True, roster_to=True))[0] == "SOURCE_ID_COLLISION"
    assert A.classify(_link(profile_conflict=True, roster_from=True, roster_to=True))[0] == "SOURCE_ID_COLLISION"


def test_same_name_players_and_aliases():
    # a stale roster row on a second team is a team-attribution defect, an alias team id a mapping error
    assert A.classify({"kind": "ROSTER_MULTI_TEAM", "box_teams_after": 1, "box_on_other_roster_team": True})[0] == "TEAM_ATTRIBUTION_DEFECT"
    assert A.classify({"kind": "ROSTER_MULTI_TEAM", "same_school_name": True})[0] == "CANONICAL_MAPPING_ERROR"
    assert A.classify({"kind": "EVENT_FOREIGN_ACTOR", "team_alias": True})[0] == "CANONICAL_MAPPING_ERROR"
    assert A.classify({"kind": "EVENT_FOREIGN_ACTOR", "event_team_not_in_game": True})[0] == "TEAM_ATTRIBUTION_DEFECT"


def test_every_class_and_confidence_is_from_the_frozen_vocabulary():
    kinds = [_in(), _link(), {"kind": "EVENT_FOREIGN_ACTOR"}, {"kind": "ROSTER_MULTI_TEAM"}, {"kind": "PLAYER_IMPOSSIBILITY"}]
    for ev in kinds:
        cls, conf, why = A.classify(ev)
        assert cls in A.CLASSES and conf in A.CONFIDENCE and why


# ============================================================================ B. synthetic worlds (repair logic)
LIN0 = "'SYNTHETIC' AS source_system, 'k' AS source_record_key, 'test' AS ingestion_version, now() AS received_at, now() AS processed_at"
LIN = "now() AS historical_knowable_at, " + LIN0


def _world(con, *, break_box=False, same_name=False, overlap_same_date=False):
    """Team A vs Team B (G1) with BOTH rosters' box lines mirrored onto both teams; A's and B's other games resolved."""
    tid = lambda k: scalar(con, "SELECT cid('T','team', ?)", [k])
    gid = lambda k: scalar(con, "SELECT cid('G','game', ?)", [k])
    pid = lambda k: scalar(con, "SELECT cid('P','athlete', ?)", [k])
    ta, tb, tc, td = (tid(k) for k in "ABCD")
    g1, g2, g3 = (gid(k) for k in ("1", "2", "3"))
    for g, h, a in ((g1, ta, tb), (g2, ta, tc), (g3, tb, td)):
        con.execute(f"""INSERT INTO core.games BY NAME SELECT '{g}' AS canonical_game_id, 2020 AS season, 'EXACT' AS tip_time_quality,
                        DATE '2020-01-01' AS game_date_et, '{h}' AS home_team_id, '{a}' AS away_team_id, 'DI_VS_DI' AS game_universe,
                        TRUE AS has_team_box_score, TRUE AS has_player_box_score, {LIN0}""")
    players = [(f"a{i}", ta, ("John Smith" if same_name and i == 1 else f"A Player {i}"), 10, 4) for i in range(1, 6)] + \
              [(f"b{i}", tb, ("John Smith" if same_name and i == 1 else f"B Player {i}"), 8, 3) for i in range(1, 6)]
    rows = []
    for aid, team, name, pts, fgm in players:
        own_other = g2 if team == ta else g3
        opp = tc if team == ta else td
        rows.append((f"K-{aid}-own", own_other, team, opp, pid(aid), "RESOLVED", name, pts, fgm))
        for side, other in ((ta, tb), (tb, ta)):                       # mirrored copies in G1
            rows.append((f"K-{aid}-{side[-4:]}", g1, side, other, None, "UNRESOLVED_SAME_PLAYER_BOTH_TEAMS", name, pts, fgm))
        con.execute(f"""INSERT INTO fact.player_season_stats BY NAME SELECT '{pid(aid)}' AS canonical_player_id, '{team}' AS canonical_team_id,
                        2020 AS season, '{name}' AS source_player_name, 2 AS games, {LIN.replace("'k'", f"'sf-{aid}-own'")}""")
        wrong = tb if team == ta else ta                               # the season-file echo of the mirror
        con.execute(f"""INSERT INTO fact.player_season_stats BY NAME SELECT '{pid(aid)}' AS canonical_player_id, '{wrong}' AS canonical_team_id,
                        2020 AS season, '{name}' AS source_player_name, 1 AS games, {LIN.replace("'k'", f"'sf-{aid}-echo'")}""")
    for key, g, team, opp, p, st, name, pts, fgm in rows:
        con.execute(f"""INSERT INTO fact.player_games BY NAME SELECT ? AS player_game_key, ? AS canonical_game_id, ? AS canonical_team_id,
                        ? AS canonical_opponent_id, ? AS canonical_player_id, ? AS identity_status, 2020 AS season, ? AS source_player_name,
                        ? AS points, ? AS fgm, 1 AS three_pm, 1 AS ftm, 30 AS minutes, {LIN}""", [key, g, team, opp, p, st, name, pts, fgm])
    for team, opp, pts, fgm in ((ta, tb, 50, 20), (tb, ta, 40, 15)):
        con.execute(f"""INSERT INTO fact.team_games BY NAME SELECT '{g1}' AS canonical_game_id, '{team}' AS canonical_team_id,
                        '{opp}' AS canonical_opponent_id, 2020 AS season, {pts + (1 if break_box and team == ta else 0)} AS points,
                        {fgm} AS fgm, 5 AS three_pm, 5 AS ftm, {LIN}""")
    if overlap_same_date:   # a resolved 'a1' row for team B in G3 as well -> a1 now has box rows for both teams
        con.execute(f"""INSERT INTO fact.player_games BY NAME SELECT 'K-a1-onB' AS player_game_key, '{g3}' AS canonical_game_id,
                        '{tb}' AS canonical_team_id, '{td}' AS canonical_opponent_id, '{pid("a1")}' AS canonical_player_id,
                        'RESOLVED' AS identity_status, 2020 AS season, 'A Player 1' AS source_player_name, 2 AS points, {LIN}""")
    return {"ta": ta, "tb": tb, "g1": g1, "pid": pid}


def _repair(con):
    con.execute("SET VARIABLE ver = 'test'")
    TA._demirror(con)
    TA._spurious_season_membership(con)
    return con.execute("SELECT decision, rows_restored, rows_quarantined, reconciles FROM metadata.team_attribution_resolution").fetchall()


def test_synthetic_whole_roster_mirror_is_repaired_and_reconciles(empty_db):
    w = _world(empty_db)
    assert _repair(empty_db) == [("REMEDIATED_RECONCILED", 10, 10, True)]
    # every player keeps exactly one G1 row, on his true team, with his canonical id
    got = empty_db.execute("SELECT canonical_player_id, canonical_team_id FROM fact.player_games WHERE canonical_game_id = ? ORDER BY 1",
                           [w["g1"]]).fetchall()
    assert len(got) == 10 and all(p is not None for p, _ in got)
    assert {t for p, t in got if p == w["pid"]("a3")} == {w["ta"]} and {t for p, t in got if p == w["pid"]("b2")} == {w["tb"]}
    sums = dict(empty_db.execute("SELECT canonical_team_id, sum(points) FROM fact.player_games WHERE canonical_game_id = ? GROUP BY 1",
                                 [w["g1"]]).fetchall())
    assert sums == {w["ta"]: 50, w["tb"]: 40}
    # the copies are preserved, not lost; the season-file echoes are gone from the fact table
    assert scalar(empty_db, "SELECT count(*) FROM qa.identity_remediation_quarantine WHERE table_name = 'fact.player_games'") == 10
    assert scalar(empty_db, """SELECT count(*) FROM (SELECT canonical_player_id FROM fact.player_season_stats GROUP BY 1
                               HAVING count(DISTINCT canonical_team_id) > 1)""") == 0


def test_synthetic_mirror_that_does_not_reconcile_is_left_unresolved(empty_db):
    _world(empty_db, break_box=True)
    assert _repair(empty_db) == [("QUARANTINED_UNRECONCILED", 0, 0, False)]
    assert scalar(empty_db, "SELECT count(*) FROM fact.player_games WHERE identity_status = 'UNRESOLVED_SAME_PLAYER_BOTH_TEAMS'") == 20
    assert scalar(empty_db, "SELECT count(*) FROM fact.player_season_stats WHERE games = 1") == 10     # nothing removed


def test_synthetic_same_name_players_on_opposing_teams_are_never_guessed(empty_db):
    _world(empty_db, same_name=True)
    assert _repair(empty_db) == [("QUARANTINED_UNRECONCILED", 0, 0, False)]


def test_synthetic_overlapping_team_appearances_block_the_repair(empty_db):
    _world(empty_db, overlap_same_date=True)
    assert _repair(empty_db)[0][0] == "QUARANTINED_UNRECONCILED"


# ============================================================================ C. acceptance on the built data
@pytest.fixture(scope="module")
def audit():
    return json.loads(A.OUT_JSON.read_text())


def test_v2_3_manifest_preserved_and_v2_4_successor():
    p = C.MANIFESTS / f"DATASET_MANIFEST_{V23}.json"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == V23_MANIFEST_SHA256
    assert json.loads(p.read_text())["dataset_version"] == V23
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    assert man["dataset_version"].startswith("cbb_data_v2.4+") and man["parent_dataset_version"] == V23
    assert man["lineage"][-1] == V23 and "cbb_data_v2.2+95dd9c2fa5b7" in man["lineage"]
    assert man["lockbox_touched"] is False and "RESEARCH_CANDIDATE" in man["status"]
    rem = man["identity_remediation"]
    assert rem["player_game_rows_restored"] == rem["player_game_rows_quarantined_mirror_copies"] > 0
    assert json.loads(spec.STAGE3_MANIFEST.read_text())["upstream_dataset"] == man["dataset_version"] == spec.UPSTREAM_DATASET


def test_cluster_audit_covers_the_window_and_gates(audit):
    assert audit["mode"] == "AFTER_REMEDIATION" and audit["window"] == "2013-14 .. 2024-25"
    assert audit["every_candidate_has_evidence_class_confidence"] is True
    assert audit["whole_team_members_accepted_without_positive_evidence"] == 0
    assert audit["remaining_in_season_box_multi_team"] == 0 and audit["remaining_in_season_season_file_multi_team"] == 0
    assert audit["repaired_games_unreconciled"] == 0
    rows = list(csv.DictReader(A.OUT_CSV.open()))
    assert len(rows) == audit["candidates"]
    assert all(r["classification"] in A.CLASSES and r["confidence"] in A.CONFIDENCE and r["evidence_json"] for r in rows)
    named = {"TCU / Washington", "DePaul / Missouri State", "Bradley / Kansas State", "Pennsylvania / Saint Joseph's",
             "LSU / Old Dominion", "Coastal Carolina / Southern Utah", "Illinois State / Presbyterian",
             "Southeast Missouri State / UNC Asheville", "Arkansas-Pine Bluff / High Point"}
    whole = {" / ".join(sorted(c["teams"].split(" / "))): c for c in audit["whole_team_clusters_list"] if c["kind"] == "IN_SEASON_MULTI_TEAM"}
    assert named <= set(whole)
    for k in named:
        assert set(whole[k]["classes"]) == {"TEAM_ATTRIBUTION_DEFECT"} and set(whole[k]["confidence"]) == {"HIGH"}, k


def test_every_repaired_game_reconciles_on_four_primitives(db):
    games = [g for (g,) in db.execute("""SELECT canonical_game_id FROM metadata.team_attribution_resolution
                                         WHERE decision = 'REMEDIATED_RECONCILED'""").fetchall()]
    assert len(games) >= 14
    bad = scalar(db, """WITH ps AS (SELECT canonical_game_id, canonical_team_id, sum(points) p, sum(fgm) f, sum(three_pm) t, sum(ftm) m
                                    FROM fact.player_games GROUP BY 1, 2)
        SELECT count(*) FROM fact.team_games x JOIN ps USING (canonical_game_id, canonical_team_id)
        WHERE x.canonical_game_id IN (SELECT unnest(?::VARCHAR[])) AND (p, f, t, m) IS DISTINCT FROM (x.points, x.fgm, x.three_pm, x.ftm)""", [games])
    assert bad == 0
    assert scalar(db, "SELECT count(*) FROM metadata.team_attribution_resolution WHERE decision = 'QUARANTINED_UNRECONCILED'") == 0


def test_no_canonical_player_on_two_teams_in_a_season_or_game(db):
    assert scalar(db, f"""SELECT count(*) FROM (SELECT canonical_player_id, season FROM fact.player_games WHERE canonical_player_id IS NOT NULL
                          AND season {W} GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) > 1)""") == 0
    assert scalar(db, """SELECT count(*) FROM (SELECT canonical_game_id, canonical_player_id FROM fact.player_games
                         WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1)""") == 0
    assert scalar(db, f"""SELECT count(*) FROM (SELECT canonical_player_id, season FROM fact.player_season_stats WHERE season {W}
                          GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) > 1)""") == 0


def test_removed_rows_are_preserved_and_logged(db):
    for table in ("fact.player_games", "fact.player_season_stats", "core.rosters"):
        kept = scalar(db, "SELECT count(*) FROM qa.identity_remediation_quarantine WHERE table_name = ?", [table])
        logged = scalar(db, """SELECT coalesce(sum(rows_affected), 0) FROM metadata.player_identity_resolution
                               WHERE created_by_version = ? AND table_name = ? AND rule_type = 'QUARANTINE'
                                 AND NOT (table_name = 'fact.player_games' AND rule_id LIKE 'QUARANTINE_ERA_%')""", [TA.VERSION, table])
        assert kept == logged > 0, table
    assert scalar(db, "SELECT count(*) FROM qa.identity_remediation_quarantine WHERE row_json IS NULL OR row_json = ''") == 0


def test_collision_eras_are_quarantined_not_reassigned(db):
    for aid, seasons, rule, _ in TA.COLLISIONS:
        pid = scalar(db, "SELECT cid('P','athlete', ?)", [aid])
        assert scalar(db, f"SELECT count(*) FROM fact.player_games WHERE canonical_player_id = ? AND season IN ({', '.join(map(str, seasons))})", [pid]) == 0
        assert scalar(db, f"""SELECT count(*) FROM fact.player_games WHERE identity_status = 'QUARANTINED_SOURCE_ID_COLLISION'
                              AND season IN ({', '.join(map(str, seasons))})""") > 0
    # no impossible career survives in the window
    assert scalar(db, """WITH s AS (SELECT canonical_player_id, min(season) a, max(season) b, count(DISTINCT season) n FROM fact.player_games
                         WHERE canonical_player_id IS NOT NULL GROUP BY 1) SELECT count(*) FROM s WHERE b >= 2014 AND (n >= 8 OR b - a >= 8)""") == 0


def test_event_feed_naming_a_non_participant_team_never_reaches_trusted_views(db):
    assert scalar(db, """SELECT count(*) FROM metadata.team_attribution_resolution WHERE defect_class = 'EVENT_TEAM_NOT_IN_GAME'""") >= 1
    for view, col in (("flagship.plays", "canonical_team_id"), ("flagship.shots_trusted", "canonical_team_id"),
                      ("flagship.substitutions", "canonical_team_id"), ("flagship.lineup_stats", "canonical_team_id")):
        assert scalar(db, f"""SELECT count(*) FROM {view} x JOIN core.games g USING (canonical_game_id)
                              WHERE x.{col} IS NOT NULL AND x.{col} NOT IN (g.home_team_id, g.away_team_id)""") == 0, view


def test_raw_starter_pack_still_carries_the_mirror(db):
    """Repairs live in derived rows only: the Starter Pack still lists Xavier Sneed under BOTH Bradley and Kansas State in
    source game 31324 (Kansas State vs Bradley, tip 2019-11-27 local = start_date 2019-11-28 04:00 UTC), with one identical
    box line; the canonical layer keeps the Kansas State copy and holds the Bradley copy in the remediation quarantine.
    (Keyed on the source game id: start_date is UTC, so a local-date filter matches nothing.)"""
    raw = duckdb.sql(f"""SELECT team, 'game_id=' || game_id || ';athleteId_record=' || athleteId AS k, start_date,
                         minutes, points, fieldGoals_made, rebounds_total, assists
                         FROM read_csv('{C.STARTER_PACK_DATA}/player_game_stats/2020.csv', all_varchar=true)
                         WHERE game_id = '31324' AND name = 'Xavier Sneed'""").fetchall()
    assert sorted(r[0] for r in raw) == ["Bradley", "Kansas State"]                  # raw source defect preserved
    assert len({r[2:] for r in raw}) == 1 and raw[0][2].startswith("2019-11-28 04:00")
    keys = {r[0]: r[1] for r in raw}
    src = "data/player_game_stats/2020.csv"
    assert scalar(db, """SELECT count(*) FROM fact.player_games WHERE source_file = ? AND source_record_key = ?
                         AND identity_status = 'RESOLVED' AND canonical_player_id IS NOT NULL""", [src, keys["Kansas State"]]) == 1
    assert scalar(db, "SELECT count(*) FROM fact.player_games WHERE source_file = ? AND source_record_key = ?", [src, keys["Bradley"]]) == 0
    assert scalar(db, """SELECT count(*) FROM qa.identity_remediation_quarantine WHERE rule_id = 'DEMIRROR_RECONCILED_GAMES'
                         AND json_extract_string(row_json, '$.source_record_key') = ?""", [keys["Bradley"]]) == 1   # derived remediation


def test_stage3_never_joins_history_across_a_quarantined_link(db):
    fx = duckdb.connect(str(spec.FEATURES_DB), read_only=True)
    fx.execute(f"ATTACH '{C.DUCKDB_PATH}' AS cdb (READ_ONLY)")
    assert scalar(fx, "SELECT count(*) FROM cdb.metadata.identity_link_quarantine") > 0
    assert scalar(fx, """SELECT count(*) FROM ex.team_membership_evidence e JOIN cdb.metadata.identity_link_quarantine q
        ON q.canonical_player_id = e.canonical_player_id AND e.season >= q.to_season AND e.season - 2 <= q.from_season
        WHERE e.evidence_class = 'PRIOR_SEASON_RETURNER'
          AND NOT EXISTS (SELECT 1 FROM cdb.fact.player_games b WHERE b.canonical_player_id = e.canonical_player_id
                          AND b.canonical_team_id = e.canonical_team_id AND b.season BETWEEN q.to_season AND e.season - 1)""") == 0
    assert scalar(fx, """SELECT count(*) FROM feat.player_features f JOIN cdb.metadata.identity_link_quarantine q
        ON q.canonical_player_id = f.canonical_player_id AND q.to_season = f.season AND q.from_season = f.season - 1
        WHERE f.pps_appearances IS NOT NULL""") == 0
    fx.close()


def test_governance_documents_exist_and_point_to_the_new_versions():
    root = C.PROJECT_ROOT
    for f in ("CBB_Blueprint_v1_4.md", "PROJECT_EXECUTION_ORDER_v1_1.md", "docs/STAGE_CROSSWALK.md", "docs/ARCHITECTURE_PRECEDENCE.md",
              "docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md", "POINT_IN_TIME_POLICY_v2_0.md", "docs/BITEMPORAL_CHECKPOINT_SCHEMA.md",
              "docs/POSSESSION_AND_LINEUP_RECONSTRUCTION_POLICY.md", "docs/MODEL_ELIGIBILITY_POLICY.md",
              "PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md", "docs/PREDICTION_LEDGER_SCHEMA.md",
              "WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md", "docs/LOCKBOX_OPENING_PROTOCOL.md",
              "docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md", "docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md",
              "docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md", "docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md", "docs/OFF_MACHINE_BACKUP_PLAN.md"):
        assert (root / f).exists(), f
    man = json.loads((root / "PROJECT_MANIFEST.json").read_text())
    assert "CBB_Blueprint_v1_4.md" in man["authoritative_documents"] and "docs/ARCHITECTURE_PRECEDENCE.md" in man["authoritative_documents"]
    readme = (root / "README.md").read_text()
    assert "PROJECT_EXECUTION_ORDER_v1_1.md" in readme and "RESEARCH_CANDIDATE" in readme
    # historical documents are preserved, not rewritten
    assert "Frozen Execution Order v1.0" in (root / "docs/PROJECT_EXECUTION_ORDER_v1_0.md").read_text()
    assert "Version:** 1.2" in (root / "POINT_IN_TIME_POLICY.md").read_text()
