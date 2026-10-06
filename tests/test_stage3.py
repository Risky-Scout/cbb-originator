"""Stage 3 -- point-in-time feature store, label store, walk-forward folds. Leakage tests are numbered per directive."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import duckdb
import pytest

from cbb_platform import config as C
from cbb_platform.stage3 import (build as B, event_stats, labels, lineup_stats, participation, pitlib, player_state, registry,
                                 sources, spec, team_state, universe)

FEATURE_MODULES = (universe, team_state, player_state, event_stats, lineup_stats, participation, pitlib, registry)
STATE_PROV = {   # feature table -> (provenance source-game column, provenance available_at column) pairs
    "feat.team_features": [("res_state_source_game_id", "res_state_available_at"), ("box_state_source_game_id", "box_state_available_at")],
    "feat.player_features": [("pbox_state_source_game_id", "pbox_state_available_at"), ("part_state_source_game_id", "part_state_available_at")],
    "feat.team_event_features": [("tev_state_source_game_id", "tev_state_available_at")],
    "feat.player_shot_features": [("pshot_state_source_game_id", "pshot_state_available_at")],
    "feat.player_lineup_features": [("onc_state_source_game_id", "onc_state_available_at")],
}
HARD_MISSING = ("NO_PRIOR_HISTORY", "SOURCE_NOT_AVAILABLE", "NOT_APPLICABLE", "UNKNOWN_PARTICIPATION", "IDENTITY_UNRESOLVED")


@pytest.fixture(scope="module")
def fx():
    if not spec.FEATURES_DB.exists():
        pytest.fail("Stage 3 store missing -- run `.venv/bin/python -m cbb_platform.stage3.build` first")
    con = duckdb.connect(str(spec.FEATURES_DB), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{spec.LABELS_DB}' AS lb (READ_ONLY)")
    con.execute(f"ATTACH '{C.DUCKDB_PATH}' AS cdb (READ_ONLY)")
    yield con
    con.close()


def one(con, sql, p=None):
    return con.execute(sql, p or []).fetchone()[0]


@pytest.fixture(scope="module")
def reg():
    return json.loads(spec.REGISTRY_JSON.read_text())


# =================================================================== scope / folds / universe
def test_upstream_dataset_is_v2_2():
    man = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    assert man["dataset_version"] == spec.UPSTREAM_DATASET
    s3 = json.loads(spec.STAGE3_MANIFEST.read_text())
    assert s3["upstream_dataset"] == spec.UPSTREAM_DATASET and s3["lockbox_touched"] is False


def test_modeling_scope_2013_14_to_2024_25(fx):
    for t in ("ex.game_examples", "ex.player_game_examples", "feat.team_box_state", "feat.player_box_state",
              "feat.team_event_state", "feat.player_oncourt_state", "lb.lab.player_game_labels"):
        lo, hi = fx.execute(f"SELECT min(season), max(season) FROM {t}").fetchone()
        assert lo >= 2014 and hi <= 2025, t
    assert one(fx, "SELECT min(season) FROM feat.player_oncourt_state") >= 2024


def test_seven_core_folds_are_frozen():
    d = json.loads((spec.FOLDS_DIR / "core_walk_forward.json").read_text())
    assert [(f["fold"], f["train_seasons"][0], f["train_seasons"][-1], f["validation_season"]) for f in d["folds"]] == \
        [(f"FOLD_{i}", 2014, v - 1, v) for i, v in enumerate(range(2019, 2026), 1)]
    assert d["lockbox"]["season"] == 2026 and "NOT_A_TUNING_FOLD" in d["lockbox"]["role"]
    m = json.loads((spec.FOLDS_DIR / "fold_manifests.json").read_text())
    assert len(m["folds"]) == 8
    for f in m["folds"]:
        assert 2026 not in f["train"]["seasons"] + f["validation"]["seasons"]
        assert max(f["train"]["seasons"]) < f["validation"]["seasons"][0]
        assert f["validation"]["player_game_examples"]["count"] == f["validation"]["player_game_labels"]["count"] > 0


def test_game_universe_is_every_played_di_game(fx):
    assert one(fx, "SELECT count(*) FROM ex.game_examples") == one(
        fx, "SELECT count(*) FROM cdb.flagship.played_games WHERE game_universe = 'DI_VS_DI' AND season BETWEEN 2014 AND 2025")
    assert one(fx, "SELECT count(*) - count(DISTINCT canonical_game_id) FROM ex.game_examples") == 0
    assert one(fx, "SELECT count(*) FROM ex.team_game_examples") == 2 * one(fx, "SELECT count(*) FROM ex.game_examples")


def test_player_universe_never_uses_the_target_box(fx):
    """Universe = roster or EARLIER participation; a player with only a target-game box row is not in it."""
    assert one(fx, """SELECT count(*) FROM ex.player_game_examples x WHERE NOT x.on_season_roster AND NOT EXISTS (
        SELECT 1 FROM feat.player_box_state s WHERE s.canonical_player_id = x.canonical_player_id AND s.season = x.season
          AND s.last_team_id = x.canonical_team_id AND s.available_at <= x.prediction_cutoff_utc)""") == 0
    src = inspect.getsource(universe)
    assert "first_available_at <= t.prediction_cutoff_utc" in src
    assert one(fx, "SELECT count(*) FROM ex.player_game_examples x JOIN ex.identity_conflicts c USING (canonical_player_id, season)") == 0


# =================================================================== leakage 1-6: PIT provenance
@pytest.mark.parametrize("table", list(STATE_PROV))
def test_no_feature_uses_its_own_game_or_any_later_game(fx, table):
    for gcol, acol in STATE_PROV[table]:
        assert one(fx, f"SELECT count(*) FROM {table} WHERE {acol} > prediction_cutoff_utc") == 0, (table, acol)
        key = "player_game_example_id" if "player" in table else "game_example_id"
        exm = "ex.player_game_examples" if "player" in table else "ex.game_examples"
        assert one(fx, f"""SELECT count(*) FROM {table} f JOIN {exm} x USING ({key})
                           WHERE f.{gcol} = x.canonical_game_id""") == 0, (table, gcol)
        # the source game tipped before the target cutoff
        assert one(fx, f"""SELECT count(*) FROM {table} f JOIN cdb.flagship.games g ON g.canonical_game_id = f.{gcol}
                           WHERE g.scheduled_tip_utc >= f.prediction_cutoff_utc""") == 0, (table, gcol)


def test_cutoff_contract(fx):
    assert one(fx, """SELECT count(*) FROM ex.game_examples WHERE tip_time_quality = 'EXACT'
                      AND prediction_cutoff_utc <> scheduled_tip_utc - INTERVAL 1 HOUR""") == 0
    assert one(fx, "SELECT count(*) FROM ex.game_examples WHERE prediction_cutoff_utc >= scheduled_tip_utc") == 0
    # states are stamped with the canonical HR-1 knowable time, never with wallclock
    for m in FEATURE_MODULES + (sources, labels):
        assert "wallclock" not in inspect.getsource(m), m.__name__


def test_adversarial_target_outcomes_cannot_change_their_own_features(tmp_path):
    """Poison one target game's box scores, scores and PBP; rebuild on a one-season slice; its own pregame features must
    be byte-identical while a LATER game of the same team must change (proving the poison flows only forward)."""
    def slice_build(poison: str | None):
        con = B.connect(tmp_path / f"adv_{poison or 'clean'}.duckdb")
        sources.bind(con)
        con.execute("CREATE SCHEMA s2")
        for (v,) in con.execute("SELECT view_name FROM duckdb_views() WHERE schema_name = 'src'").fetchall():
            cols = [c[0] for c in con.execute(f"DESCRIBE src.{v}").fetchall()]
            where = " WHERE season = 2024" if "season" in cols else ""
            con.execute(f"CREATE TABLE s2.{v} AS SELECT * FROM src.{v}{where}")
        sources.unbind(con)
        con.execute("CREATE SCHEMA src")
        for (t,) in con.execute("SELECT table_name FROM duckdb_tables() WHERE schema_name = 's2'").fetchall():
            con.execute(f"CREATE TABLE src.{t} AS SELECT * FROM s2.{t}")
        if poison:
            con.execute(f"UPDATE src.player_games SET points = 999, minutes = 40, fga = 99, three_pa = 50 WHERE canonical_game_id = '{poison}'")
            con.execute(f"UPDATE src.team_games SET points = 999, fga = 199 WHERE canonical_game_id = '{poison}'")
            con.execute(f"UPDATE src.played_games SET home_points = 999 WHERE canonical_game_id = '{poison}'")
            con.execute(f"UPDATE src.plays SET home_score = home_score + 500 WHERE canonical_game_id = '{poison}'")
            con.execute(f"UPDATE src.shots SET made = TRUE WHERE canonical_game_id = '{poison}'")
        universe.build(con)
        team_state.build(con)
        player_state.build(con)
        event_stats.build(con)
        return con
    clean = slice_build(None)
    target, team, tip = clean.execute("""SELECT canonical_game_id, canonical_team_id, scheduled_tip_utc FROM ex.team_game_examples
        ORDER BY scheduled_tip_utc, canonical_game_id LIMIT 1 OFFSET 5000""").fetchone()
    later = clean.execute("""SELECT canonical_game_id FROM ex.team_game_examples WHERE canonical_team_id = ? AND scheduled_tip_utc > ?
                             ORDER BY scheduled_tip_utc LIMIT 1""", [team, tip]).fetchone()[0]
    dirty = slice_build(target)
    for t, k in (("feat.team_features", "canonical_team_id"), ("feat.player_features", "canonical_player_id"),
                 ("feat.team_event_features", "canonical_team_id"), ("feat.player_shot_features", "player_game_example_id")):
        gk = "canonical_game_id" if t != "feat.player_shot_features" else "player_game_example_id"
        if t == "feat.player_shot_features":
            q = f"""SELECT f.* FROM {t} f JOIN ex.player_game_examples x USING (player_game_example_id)
                    WHERE x.canonical_game_id = '{{g}}' ORDER BY ALL"""
        elif t == "feat.team_event_features":
            q = f"""SELECT f.* FROM {t} f JOIN ex.game_examples x USING (game_example_id) WHERE x.canonical_game_id = '{{g}}' ORDER BY ALL"""
        else:
            q = f"SELECT * FROM {t} WHERE canonical_game_id = '{{g}}' ORDER BY ALL"
        assert clean.execute(q.format(g=target)).fetchall() == dirty.execute(q.format(g=target)).fetchall(), t
    assert clean.execute("SELECT te_off_eff FROM feat.team_features WHERE canonical_game_id = ? AND canonical_team_id = ?", [later, team]).fetchone() != \
        dirty.execute("SELECT te_off_eff FROM feat.team_features WHERE canonical_game_id = ? AND canonical_team_id = ?", [later, team]).fetchone()


# =================================================================== leakage 3: completed-season summaries
def test_no_completed_season_summary_enters_features():
    for m in FEATURE_MODULES + (sources,):
        src = inspect.getsource(m)
        for bad in ("season_stats", "season_game_derived", "season_shooting"):
            assert bad not in src, (m.__name__, bad)


def test_prior_season_features_come_from_the_complete_previous_season(fx):
    assert one(fx, """SELECT count(*) FROM feat.team_features f WHERE f.tp_status = 'OK' AND f.tp_prev_games IS DISTINCT FROM (
        SELECT n_games FROM feat.team_results_state r WHERE r.canonical_team_id = f.canonical_team_id AND r.season = f.season - 1
        ORDER BY available_at DESC LIMIT 1)""") == 0
    assert one(fx, "SELECT count(*) FROM feat.team_features WHERE season = 2014 AND tp_status <> 'SOURCE_NOT_AVAILABLE'") == 0


# =================================================================== leakage 7-9: unplayed / alias / corrupt
STATE_TABLES = ("feat.team_box_state", "feat.team_results_state", "feat.player_box_state", "feat.player_participation_state",
                "feat.team_event_state", "feat.player_shot_state", "feat.player_oncourt_state", "feat.lineup_unit_state")


@pytest.mark.parametrize("table", STATE_TABLES)
def test_unplayed_alias_and_corrupt_games_never_enter_history(fx, table):
    bad = """SELECT canonical_game_id FROM cdb.metadata.game_status_resolution WHERE excluded_from_played_games
             UNION SELECT source_canonical_game_id FROM cdb.metadata.game_alias_map WHERE excluded_from_trusted_games"""
    assert one(fx, f"SELECT count(*) FROM {table} WHERE source_game_id IN ({bad})") == 0
    assert one(fx, f"SELECT count(*) FROM {table} WHERE source_game_id NOT IN (SELECT canonical_game_id FROM cdb.flagship.played_games)") == 0
    assert one(fx, f"SELECT count(*) FROM ex.game_examples WHERE canonical_game_id IN ({bad})") == 0


# =================================================================== leakage 10-11
def test_lockbox_untouched(fx):
    for t in ("ex.game_examples", "feat.team_features", "feat.player_features", "lb.lab.player_game_labels"):
        assert one(fx, f"SELECT count(*) FROM {t} WHERE season >= 2026") == 0
    assert one(fx, "SELECT max(prediction_cutoff_utc) FROM ex.game_examples").isoformat() < "2025-08-01"


def test_sportsbook_data_never_reaches_feature_code_or_tables(fx, reg):
    for m in FEATURE_MODULES + (sources, labels):
        src = inspect.getsource(m).lower()
        for bad in ("benchmark", "odds", "moneyline", "bdl"):
            assert bad not in src, (m.__name__, bad)
    for e in reg["entries"]:
        assert not re.search(r"odds|spread|moneyline|line_|vegas|book", e["feature_name"]), e["feature_name"]


def test_label_store_is_separate(fx):
    assert one(fx, "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'features' AND schema_name = 'lab'") == 0
    for m in FEATURE_MODULES + (sources,):
        src = inspect.getsource(m)
        assert "labels.duckdb" not in src and "lab." not in src and "LABELS_DB" not in src, m.__name__
    assert one(fx, "SELECT count(*) FROM duckdb_views() WHERE database_name = 'features'") == 0   # no live link to canonical


# =================================================================== labels
def test_labels_reconcile(fx):
    n, pts, reb = fx.execute("""SELECT count(*), count(*) FILTER (WHERE NOT pts_identity_ok), count(*) FILTER (WHERE NOT reb_identity_ok)
                                FROM lb.lab.player_game_labels WHERE box_row_present AND source_accounting_issues IS NULL""").fetchone()
    assert n > 1_000_000 and pts == 0 and reb == 0
    assert one(fx, "SELECT count(*) FROM lb.lab.player_game_labels WHERE NOT box_row_present AND (pts IS NOT NULL OR min IS NOT NULL)") == 0
    assert one(fx, "SELECT count(*) FROM lb.lab.player_game_labels WHERE (fta = 0 AND ft_pct IS NOT NULL) OR (three_pa = 0 AND three_pct IS NOT NULL)") == 0
    assert one(fx, f"SELECT count(*) FROM lb.lab.player_game_labels WHERE participation_status NOT IN {spec.PARTICIPATION_STATUSES}") == 0
    assert one(fx, "SELECT count(*) FROM lb.lab.player_game_labels WHERE participation_status = 'DNP_CONFIRMED' AND season < 2024") == 0
    assert one(fx, """SELECT count(*) FROM lb.lab.player_game_labels WHERE participation_status = 'DNP_CONFIRMED'
                      AND (pts > 0 OR min > 0)""") == 0
    assert one(fx, "SELECT count(*) FROM lb.lab.player_game_labels") == one(fx, "SELECT count(*) FROM ex.player_game_examples")


def test_absence_of_a_box_row_is_never_dnp(fx):
    assert one(fx, """SELECT count(*) FROM lb.lab.player_game_labels WHERE participation_status = 'DNP_CONFIRMED'
                      AND participation_evidence <> 'COMPLETE_SUBSTITUTION_FEED_NO_STINT'""") == 0


# =================================================================== 13-14 registry + missingness
def test_every_feature_is_registered_and_described(fx, reg):
    names = {(e["table"], e["feature_name"]) for e in reg["entries"]}
    for t in registry.FEATURE_TABLES:
        for (c,) in fx.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema || '.' || table_name = '{t}'").fetchall():
            assert (t, c) in names, (t, c)
    assert len(names) == len(reg["entries"])
    for e in reg["entries"]:
        if e["kind"] == "FEATURE":
            for k in ("feature_family", "description", "unit", "grain", "source_tables", "historical_window", "minimum_observations",
                      "null_meaning", "pit_cutoff_rule", "version", "leakage_risk", "approved_for_modeling"):
                assert e.get(k) not in (None, ""), (e["feature_name"], k)
    assert reg["feature_count"] > 150


def test_missingness_codes_follow_the_contract(fx, reg):
    fams = {}
    for e in reg["entries"]:
        if e["kind"] == "FEATURE":
            fams.setdefault((e["table"], e["status_column"]), []).append(e)
    for (t, sc), feats in fams.items():
        bad = one(fx, f"SELECT count(*) FROM {t} WHERE {sc} NOT IN ('OK', {', '.join(repr(c) for c in spec.MISSING_CODES)})")
        assert bad == 0, (t, sc)
        valued = [e["feature_name"] for e in feats if not e["unit"].startswith("count") and e["unit"] not in ("category", "boolean")]
        if valued:
            leaks = one(fx, f"SELECT count(*) FROM {t} WHERE {sc} IN {HARD_MISSING} AND ({' OR '.join(f'{c} IS NOT NULL' for c in valued)})")
            assert leaks == 0, (t, sc)


def test_no_global_zero_imputation(fx):
    assert one(fx, "SELECT count(*) FROM feat.player_features WHERE pstd_status = 'NO_PRIOR_HISTORY' AND pstd_min_per_app IS NOT NULL") == 0
    assert one(fx, "SELECT count(*) FROM feat.player_features WHERE pstd_min_per_app IS NULL") > 0


# =================================================================== 12 determinism
def test_rebuild_reproduces_fingerprints(tmp_path):
    fresh = B.build(root=tmp_path, with_reports=False)
    man = json.loads(spec.STAGE3_MANIFEST.read_text())["fingerprints"]
    assert fresh == man


def test_no_predictive_model_or_fit_in_stage3():
    for m in FEATURE_MODULES + (sources, labels, B):
        src = inspect.getsource(m).lower()
        for bad in ("sklearn", "statsmodels", "torch", "xgboost", "lightgbm", ".fit(", "negativebinomial", "glm("):
            assert bad not in src, (m.__name__, bad)
