"""Relocation-safe resolution (cbb_platform/relocation.py): frozen evidence keeps its historical absolute paths; a
recorded path resolves to the current location only for the one known project relocation and only after the registered
Starter Pack content is proven identical; the frozen development database is attached READ_ONLY and never modified."""
from __future__ import annotations

import hashlib
import json
import subprocess

import duckdb
import pytest

from cbb_platform import config as C, relocation as RL

FROZEN = ("data/manifests/starter_pack_registration.json", "data/manifests/starter_pack_sha256.json",
          "data/manifests/workspace_snapshot_stage2.json", "data/manifests/workspace_snapshot_pre_model_closure_v1.json")


def test_frozen_manifests_keep_their_historical_paths_unmodified():
    assert subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *FROZEN], cwd=C.PROJECT_ROOT).returncode == 0
    for f in FROZEN[:2]:
        assert json.loads((C.PROJECT_ROOT / f).read_text())["starter_pack_root"].startswith(str(RL.HISTORICAL_PROJECT_PARENT))


def test_resolution_is_scoped_to_the_known_relocation_only():
    if not RL.relocated():
        pytest.skip("project is at its historical location")
    old = RL.HISTORICAL_PROJECT_PARENT / "CBB Starter Pack" / "starter_pack"
    assert RL.resolve(old) == C.STARTER_PACK_ROOT
    assert RL.resolve(str(old / "data" / "games.csv")) == C.STARTER_PACK_DATA / "games.csv"
    for other in ("/Users/josephshackelford/CBB Predictive Model 2026x/a", "/tmp/a", "/Users/josephshackelford/Other/a"):
        assert str(RL.resolve(other)) == other                         # no general path rewriting


def test_starter_pack_content_identity_is_the_registered_content():
    ident = RL.starter_pack_identity()
    assert ident["pass"] and ident["registered_data_files"] == ident["current_data_files"] == 111
    assert ident["current_data_digest"] == ident["registered_data_digest"] == \
        "cc6ced32d28fd026f06179fd2c0cecbdd6c40a554e5d9927e52528777cd1ab83"
    assert ident["inventory_identical"]


def test_rebinding_refuses_without_proven_identity():
    with pytest.raises(RL.RelocationError):
        RL.connect_canonical_relocated(identity={"pass": False})


def test_staging_views_rebound_in_memory_only(tmp_path):
    """A synthetic frozen DB with a staging view on a historical root: the overlay reads the current root, the persistent
    definition and the database bytes are unchanged, and nothing can be written to the attached database."""
    import shutil
    old, new = str(tmp_path / "historical" / "starter_pack"), str(C.STARTER_PACK_ROOT)
    (tmp_path / "historical" / "starter_pack" / "data").mkdir(parents=True)
    shutil.copyfile(C.STARTER_PACK_DATA / "teams.csv", f"{old}/data/teams.csv")    # the view binds at creation
    db = tmp_path / "frozen.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA staging; CREATE SCHEMA fact; CREATE TABLE fact.t AS SELECT 1 AS a")
    con.execute(f"""CREATE VIEW staging.sp_teams AS SELECT * REPLACE (replace(filename, '{old}/', '') AS filename)
        FROM read_csv('{old}/data/teams.csv', all_varchar=true, union_by_name=true, filename=true)""")
    con.close()
    shutil.rmtree(tmp_path / "historical")                                         # the historical root is gone
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    rc = RL.connect_canonical_relocated(db, identity={"pass": True, "registered_root": old})
    assert RL.bound_views(rc) == ["sp_teams"]
    assert rc.execute("SELECT DISTINCT filename FROM staging.sp_teams").fetchall() == [("data/teams.csv",)]
    assert rc.execute("SELECT a FROM fact.t").fetchall() == [(1,)]
    assert old in rc.execute("SELECT sql FROM duckdb_views() WHERE database_name = 'cbb' AND view_name = 'sp_teams'").fetchone()[0]
    with pytest.raises(duckdb.Error):
        rc.execute("CREATE TABLE cbb.fact.x AS SELECT 1")
    rc.close()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before and new != old


def test_frozen_development_db_staging_rebinding(starter_db):
    assert RL.bound_views(starter_db) == ["sp_conferences", "sp_games", "sp_player_game_stats",
                                          "sp_player_season_shooting_stats", "sp_player_season_stats",
                                          "sp_team_game_stats", "sp_team_season_shooting_stats",
                                          "sp_team_season_stats", "sp_teams"]
    assert starter_db.execute("SELECT count(*) FROM staging.sp_teams").fetchone()[0] > 0
    assert starter_db.execute("SELECT count(*) FROM (SELECT DISTINCT filename FROM staging.sp_games) "
                              "WHERE filename LIKE '/%'").fetchone()[0] == 0     # lineage still relative
