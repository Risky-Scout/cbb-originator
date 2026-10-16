"""Point-in-time / anti-leakage audit of a CBBD ROUTE build (read-only). Mirrors the data-level assertions of
tests/test_stage3.py and tests/test_stage3_1.py, which are hard-wired to the reference store, plus route-specific checks
(POINTS_ONLY provider team boxes; route repair logic confined to staging). Categories: TARGET_LEAKAGE, SAME_GAME_LEAKAGE,
FUTURE_DATA_LEAKAGE, FUTURE_ROSTER_LEAKAGE, LABEL_LEAKAGE, LOOKAHEAD_BIAS.

    .venv/bin/python -m cbb_platform.cbbd_box_route_pit_audit <route_build_dir>   -> data/qa/cbbd_box_route_pit_audit.json
"""
import hashlib
import inspect
import json
import sys
from pathlib import Path

import duckdb

from cbb_platform import cbbd_box_route as RB, config as C
from cbb_platform.stage3 import (event_stats, labels, lineup_stats, participation, pitlib, player_state, registry, sources,
                                 spec, team_state, universe)

OUT = C.QA / "cbbd_box_route_pit_audit.json"


def audit(d: Path) -> dict:
    con = duckdb.connect(str(d / "stage3" / "features.duckdb"), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{d / 'stage3' / 'labels.duckdb'}' AS lb (READ_ONLY)")
    con.execute(f"ATTACH '{d / 'cbb.duckdb'}' AS cdb (READ_ONLY)")
    one = lambda q: con.execute(q).fetchone()[0]
    LEGAL = tuple(universe.EVIDENCE_RANK)
    FEATURE_MODULES = (universe, team_state, player_state, event_stats, lineup_stats, participation, pitlib, registry)
    STATE_PROV = {
        "feat.team_features": [("res_state_source_game_id", "res_state_available_at"), ("box_state_source_game_id", "box_state_available_at")],
        "feat.player_features": [("pbox_state_source_game_id", "pbox_state_available_at"), ("part_state_source_game_id", "part_state_available_at")],
        "feat.team_event_features": [("tev_state_source_game_id", "tev_state_available_at")],
        "feat.player_shot_features": [("pshot_state_source_game_id", "pshot_state_available_at")],
        "feat.player_lineup_features": [("onc_state_source_game_id", "onc_state_available_at")],
    }
    chk = {}   # name -> (category, violations)


    def add(cat, name, n):
        chk[name] = {"category": cat, "violations": int(n)}


    for t, pairs in STATE_PROV.items():
        key = "player_game_example_id" if "player" in t else "game_example_id"
        exm = "ex.player_game_examples" if "player" in t else "ex.game_examples"
        for g, a in pairs:
            add("FUTURE_DATA_LEAKAGE", f"{t}.{a} > cutoff", one(f"SELECT count(*) FROM {t} WHERE {a} > prediction_cutoff_utc"))
            add("TARGET_LEAKAGE", f"{t}.{g} = target game", one(f"SELECT count(*) FROM {t} f JOIN {exm} x USING ({key}) WHERE f.{g} = x.canonical_game_id"))
            add("SAME_GAME_LEAKAGE", f"{t}.{g} tipped at/after cutoff", one(f"""SELECT count(*) FROM {t} f JOIN cdb.flagship.games g
                ON g.canonical_game_id = f.{g} WHERE g.scheduled_tip_utc >= f.prediction_cutoff_utc"""))
    add("LOOKAHEAD_BIAS", "cutoff = tip - 1h (EXACT)", one("""SELECT count(*) FROM ex.game_examples WHERE tip_time_quality = 'EXACT'
        AND prediction_cutoff_utc <> scheduled_tip_utc - INTERVAL 1 HOUR"""))
    add("LOOKAHEAD_BIAS", "cutoff before tip", one("SELECT count(*) FROM ex.game_examples WHERE prediction_cutoff_utc >= scheduled_tip_utc"))
    add("FUTURE_ROSTER_LEAKAGE", "pool evidence class legal", one(f"SELECT count(*) FROM ex.player_game_examples WHERE pool_evidence NOT IN {LEGAL}"))
    add("FUTURE_ROSTER_LEAKAGE", "membership evidence class legal", one(f"SELECT count(*) FROM ex.team_membership_evidence WHERE evidence_class NOT IN {LEGAL}"))
    add("FUTURE_ROSTER_LEAKAGE", "every pool member has evidence knowable at cutoff", one("""SELECT count(*) FROM ex.player_game_examples x
        WHERE NOT EXISTS (SELECT 1 FROM ex.team_membership_evidence e WHERE e.canonical_player_id = x.canonical_player_id
          AND e.canonical_team_id = x.canonical_team_id AND e.season = x.season AND e.available_from <= x.prediction_cutoff_utc)"""))
    add("FUTURE_ROSTER_LEAKAGE", "appearance evidence knowable before cutoff, never the target box", one("""SELECT count(*)
        FROM ex.player_game_examples x WHERE x.pool_evidence = 'PRIOR_APPEARANCE_CONFIRMED' AND NOT EXISTS (
          SELECT 1 FROM cdb.flagship.player_game_outcomes p JOIN cdb.flagship.played_games g USING (canonical_game_id)
          WHERE p.canonical_player_id = x.canonical_player_id AND p.canonical_team_id = x.canonical_team_id AND g.season = x.season
            AND p.historical_knowable_at <= x.prediction_cutoff_utc AND p.canonical_game_id <> x.canonical_game_id)"""))
    add("FUTURE_ROSTER_LEAKAGE", "end-of-season roster never creates a pool member", one("""SELECT count(*) FROM ex.roster_snapshot_research r
        JOIN ex.player_game_examples x USING (game_example_id, canonical_team_id, canonical_player_id)"""))
    add("LABEL_LEAKAGE", "no lab schema in the feature store", one("SELECT count(*) FROM duckdb_tables() WHERE database_name = 'features' AND schema_name = 'lab'"))
    add("LABEL_LEAKAGE", "no live views in the feature store", one("SELECT count(*) FROM duckdb_views() WHERE database_name = 'features'"))
    add("LABEL_LEAKAGE", "DNP only from a complete substitution feed (never box absence)", one("""SELECT count(*) FROM lb.lab.player_game_labels
        WHERE participation_status = 'DNP_CONFIRMED' AND participation_evidence <> 'COMPLETE_SUBSTITUTION_FEED_NO_STINT'"""))
    bad = """SELECT canonical_game_id FROM cdb.metadata.game_status_resolution WHERE excluded_from_played_games
             UNION SELECT source_canonical_game_id FROM cdb.metadata.game_alias_map WHERE excluded_from_trusted_games"""
    for t in ("feat.team_box_state", "feat.team_results_state", "feat.player_box_state", "feat.player_participation_state",
              "feat.team_event_state", "feat.player_shot_state", "feat.player_oncourt_state", "feat.lineup_unit_state"):
        add("FUTURE_DATA_LEAKAGE", f"{t}: only played, trusted games in history",
            one(f"SELECT count(*) FROM {t} WHERE source_game_id IN ({bad}) OR source_game_id NOT IN (SELECT canonical_game_id FROM cdb.flagship.played_games)"))
    for t in ("ex.game_examples", "feat.team_features", "feat.player_features", "lb.lab.player_game_labels"):
        add("FUTURE_DATA_LEAKAGE", f"{t}: no lockbox season", one(f"SELECT count(*) FROM {t} WHERE season >= 2026"))
    # code-level (source) contracts, as in the test suite
    src_bad = 0
    for m in FEATURE_MODULES + (sources,):
        s = inspect.getsource(m)
        src_bad += sum(x in s for x in ("labels.duckdb", "lab.", "LABELS_DB", "season_stats", "season_shooting", "wallclock"))
        src_bad += "cbbd_box_route" in s               # route reconciliation logic never reaches feature code
    add("LABEL_LEAKAGE", "feature code never reads labels / season summaries / wallclock / route repair logic", src_bad)
    add("FUTURE_ROSTER_LEAKAGE", "feature history never consults the season roster snapshot",
        int("classify_sql('part_cand')" not in inspect.getsource(player_state)))
    # route-specific: the skeleton team boxes add only game_minutes, which the game record already implies (no overtime
    # period in the period-points line, knowable at the same instant); the repaired / echo-removed facts are absent from features
    add("FUTURE_DATA_LEAKAGE", "POINTS_ONLY team box minutes implied by the game's own period line", one("""SELECT count(*)
        FROM cdb.fact.team_games t JOIN cdb.core.games g USING (canonical_game_id)
        WHERE t.season IN (2024, 2025) AND t.fga IS NULL AND t.points IS NOT NULL
          AND t.game_minutes IS DISTINCT FROM 40 + 5 * (len(g.home_period_points) - 2)"""))
    add("FUTURE_DATA_LEAKAGE", "POINTS_ONLY team box knowable no earlier than its game", one("""SELECT count(*)
        FROM cdb.fact.team_games t JOIN cdb.core.games g USING (canonical_game_id)
        WHERE t.season IN (2024, 2025) AND t.fga IS NULL AND t.historical_knowable_at <= g.scheduled_tip_utc"""))
    rb = inspect.getsource(RB)
    add("FUTURE_ROSTER_LEAKAGE", "route echo/team-assignment logic lives only in staging (not Stage 3 / live scoring)",
        int("def validate_team_assignment" not in rb))
    pit = sum(v["violations"] for v in chk.values())
    by = {}
    for v in chk.values():
        by[v["category"]] = by.get(v["category"], 0) + v["violations"]
    route_build = json.loads((d / "route_build.json").read_text())["identity"]
    out = {"route_build_dir": str(d), "route_build": route_build, "route_version": RB.ROUTE_VERSION,
           "route_module_sha256": hashlib.sha256(Path(RB.__file__).read_bytes()).hexdigest(),
           "audit_module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "checks": chk, "violations_by_category": by, "PIT_VIOLATIONS": pit,
           "counts": {"pool_rows": one("SELECT count(*) FROM ex.player_game_examples"),
                      "points_only_team_boxes": one("SELECT count(*) FROM cdb.fact.team_games WHERE season IN (2024, 2025) AND fga IS NULL AND points IS NOT NULL")}}
    OUT.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps({"violations_by_category": by, "PIT_VIOLATIONS": pit, "counts": out["counts"]}, indent=1))
    return out


if __name__ == "__main__":
    audit(Path(sys.argv[1]))
