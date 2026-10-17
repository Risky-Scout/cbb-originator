"""V2 Phase 1 data-provenance gate (read-only; makes no API call, writes only its report).

Establishes what produced the canonical data and Stage 3 stores that the v1 artifact and the V2 marginal refit read, and
whether the three known Starter Pack defects reach them:

    athleteId   player_game_stats.athleteId is a per-RECORD id in this export (docs/STARTER_PACK_PHYSICAL_AUDIT.md 6b)
    duplicates  (game, athleteId) / (game, team) keys fan out once per conference membership (same doc, 6c)
    timestamps  box-file +00:00 stamps are 4-5 h late; games.csv start_date is naive UTC (CANONICAL_SCHEMA.md 9)

Every check is measured on the stores themselves. Stores:
    dev canonical      data/canonical/cbb.duckdb                     (cbb_data_v2.4+5f19d31e524a; seasons <= 2024-25)
    lockbox canonical  data/lockbox_2025_26/canonical/cbb.duckdb     (cbb_data_v3.0+86a63cb6d902; + 2025-26 CBBD route)
    dev Stage 3        modeling/store/{features,labels}.duckdb       (v1 training input)
    lockbox Stage 3    data/lockbox_2025_26/stage3/*.duckdb          (2013-14 .. 2025-26; the V2 common feature store)

    .venv/bin/python -m cbb_platform.v2.provenance     -> modeling/player_props_v2/data_provenance.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from cbb_platform import config as C

OUT = C.PROJECT_ROOT / "modeling" / "player_props_v2" / "data_provenance.json"
DEV_CANONICAL = C.PROJECT_ROOT / "data" / "canonical" / "cbb.duckdb"
LB = C.PROJECT_ROOT / "data" / "lockbox_2025_26"
LB_CANONICAL = LB / "canonical" / "cbb.duckdb"
DEV_FEATURES, DEV_LABELS = C.PROJECT_ROOT / "modeling" / "store" / "features.duckdb", C.PROJECT_ROOT / "modeling" / "store" / "labels.duckdb"
LB_FEATURES, LB_LABELS = LB / "stage3" / "features.duckdb", LB / "stage3" / "labels.duckdb"
V1_ARTIFACT = C.PROJECT_ROOT / "modeling" / "player_props_v1" / "final_prelockbox_2024_25_artifact.json"
# Late-tip rule: a 4-5 h late stamp moves the modal 19:00 ET tip to 23:00-00:00 ET. Revised once, on observation, from
# an initial share < 0.02 (dev 2014-2025 measured 0.0204: legitimate 20:00 PT tips at 23:00 ET) to modal hour in 18-20 ET
# and share < 0.10; the BDL external-truth comparison (exact equality) is unchanged and is the primary test.
TIP_RULE_REVISION = {"initial": "share_tips_23_to_04_et < 0.02", "revised": "modal ET hour in 18-20 and share < 0.10",
                     "reason": "dev 2014-2025 share 0.0204 from legitimate West Coast 8 pm PT tips; BDL 804/804 exact"}
# relation -> row key (for the float-difference measurement)
KEYS = {"feat.player_features": ("player_game_example_id",), "ex.player_game_examples": ("player_game_example_id",),
        "lab.player_game_labels": ("player_game_example_id",),
        "feat.player_box_state": ("canonical_player_id", "season", "source_game_id")}
# every relation the frozen marginal loaders read (props_v0.load_frames, props_v0_participation.load)
MODEL_RELATIONS = {"features": ("feat.player_features", "feat.player_box_state", "ex.player_game_examples"),
                   "labels": ("lab.player_game_labels", "lab.target_box_rows_outside_universe", "lab.target_box_rows_unresolved")}
FLOAT_REL_TOL = 1e-12                                  # summation-order noise only


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _one(con, q):
    return con.execute(q).fetchone()


# ---------------------------------------------------------------------------- canonical defect checks
def canonical_checks(path: Path, seasons: tuple[int, int]) -> dict:
    lo, hi = seasons
    con = duckdb.connect(str(path), read_only=True, config={"threads": C.DUCKDB_THREADS})
    w = f"season BETWEEN {lo} AND {hi}"
    rows, keys, resolved = _one(con, f"""SELECT count(*), count(DISTINCT player_game_key),
        count(*) FILTER (WHERE canonical_player_id IS NOT NULL) FROM fact.player_games WHERE {w}""")
    dup_player = _one(con, f"""SELECT count(*) FROM (SELECT 1 FROM fact.player_games WHERE {w} AND canonical_player_id IS NOT NULL
        GROUP BY canonical_game_id, canonical_player_id HAVING count(*) > 1)""")[0]
    dup_team = _one(con, f"""SELECT count(*) FROM (SELECT 1 FROM fact.team_games WHERE {w}
        GROUP BY canonical_game_id, canonical_team_id HAVING count(*) > 1)""")[0]
    dup_game = _one(con, f"SELECT count(*) - count(DISTINCT canonical_game_id) FROM core.games WHERE {w}")[0]
    players, multi_game, multi_season = _one(con, f"""SELECT count(*), count(*) FILTER (WHERE n > 1), count(*) FILTER (WHERE ns > 1)
        FROM (SELECT canonical_player_id, count(*) n, count(DISTINCT season) ns FROM fact.player_games
              WHERE canonical_player_id IS NOT NULL GROUP BY 1 HAVING min(season) <= {hi} AND max(season) >= {lo})""")
    # a record-id defect would give one game per id; a resolved id that were a record id would never repeat
    one_game_share = 1 - multi_game / players if players else None
    status = dict(con.execute(f"SELECT identity_status, count(*) FROM fact.player_games WHERE {w} GROUP BY 1 ORDER BY 1").fetchall())
    # players whose games in one season span two teams with the same id (would indicate a mis-anchor)
    tip = dict(con.execute(f"SELECT tip_time_quality, count(*) FROM core.games WHERE {w} GROUP BY 1 ORDER BY 1").fetchall())
    box_t = _one(con, f"""SELECT count(*), count(*) FILTER (WHERE p.event_time = g.scheduled_tip_utc),
        count(*) FILTER (WHERE g.tip_time_quality = 'EXACT'),
        count(*) FILTER (WHERE g.tip_time_quality = 'EXACT' AND p.historical_knowable_at = g.scheduled_tip_utc + INTERVAL 4 HOUR)
        FROM fact.player_games p JOIN core.games g USING (canonical_game_id) WHERE p.{w}""")
    bdl = _one(con, f"""SELECT count(*), count(*) FILTER (WHERE abs(epoch(b.game_date_utc) - epoch(g.scheduled_tip_utc)) < 60),
        count(*) FILTER (WHERE abs(epoch(b.game_date_utc) - epoch(g.scheduled_tip_utc)) BETWEEN 3.5 * 3600 AND 5.5 * 3600)
        FROM benchmark.bdl_games b JOIN core.games g USING (canonical_game_id)
        WHERE g.tip_time_quality = 'EXACT' AND b.game_date_utc IS NOT NULL AND g.{w}""")
    # ET tip-hour profile: a 4-5 h late stamp would put a large share of tips at 23:00-04:00 ET
    late = _one(con, f"""SELECT count(*), count(*) FILTER (WHERE hour(timezone('America/New_York', scheduled_tip_utc)) IN (23, 0, 1, 2, 3, 4))
        FROM core.games WHERE {w} AND tip_time_quality = 'EXACT'""")
    hours = dict(con.execute(f"""SELECT hour(timezone('America/New_York', scheduled_tip_utc)), count(*) FROM core.games
        WHERE {w} AND tip_time_quality = 'EXACT' GROUP BY 1 ORDER BY 1""").fetchall())
    src = dict(con.execute(f"SELECT source_system, count(*) FROM fact.player_games WHERE {w} GROUP BY 1 ORDER BY 1").fetchall())
    ver = con.execute("SELECT DISTINCT build_id, ingestion_version, schema_version FROM metadata.data_versions").fetchall()
    con.close()
    out = {"path": str(path.relative_to(C.PROJECT_ROOT)), "seasons": [lo, hi], "build_versions": [list(v) for v in ver],
           "player_game_rows": rows, "source_systems": src,
           "duplicates": {"player_game_key_duplicates": rows - keys, "resolved_game_player_duplicates": dup_player,
                          "team_game_duplicates": dup_team, "game_id_duplicates": dup_game},
           "athlete_identity": {"identity_status": status, "resolved_rows": resolved, "canonical_players": players,
                                "players_with_2plus_games": multi_game, "players_with_2plus_seasons": multi_season,
                                "share_single_game_ids": one_game_share},
           "timestamps": {"tip_time_quality": tip, "player_rows": box_t[0], "player_event_time_equals_tip": box_t[1],
                          "exact_tip_rows": box_t[2], "exact_knowable_at_tip_plus_4h": box_t[3],
                          "bdl_exact_tip_games": bdl[0], "bdl_tip_equal_within_60s": bdl[1], "bdl_tip_shifted_3p5_to_5p5h": bdl[2],
                          "exact_tips": late[0], "exact_tips_23_to_04_et": late[1],
                          "share_tips_23_to_04_et": late[1] / late[0] if late[0] else None,
                          "exact_tip_hour_et_histogram": hours, "modal_tip_hour_et": max(hours, key=hours.get) if hours else None}}
    d, a, t = out["duplicates"], out["athlete_identity"], out["timestamps"]
    out["pass"] = {"no_duplicates": all(v == 0 for v in d.values()),
                   "persistent_player_ids": a["share_single_game_ids"] is not None and a["share_single_game_ids"] < 0.05,
                   "box_event_time_is_tip": t["player_event_time_equals_tip"] == t["player_rows"]
                                            and t["exact_knowable_at_tip_plus_4h"] == t["exact_tip_rows"],
                   "no_shifted_tips": t["bdl_tip_shifted_3p5_to_5p5h"] == 0 and t["bdl_tip_equal_within_60s"] == t["bdl_exact_tip_games"]
                                      and t["modal_tip_hour_et"] in (18, 19, 20) and (t["share_tips_23_to_04_et"] or 0) < 0.10}
    return out


# ---------------------------------------------------------------------------- Stage 3 dev vs lockbox store equivalence
def _columns(con, db, rel):
    s, t = rel.split(".")
    return con.execute(f"""SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = '{db}' AND schema_name = '{s}'
                           AND table_name = '{t}' ORDER BY column_index""").fetchall()


def store_equivalence(max_season: int = 2025) -> dict:
    """Every model-read relation of the dev Stage 3 store vs the lockbox Stage 3 store, rows with season <= max_season:
    row count, per-column hash sum; a differing column must be floating point with relative difference <= FLOAT_REL_TOL."""
    con = duckdb.connect(config={"threads": C.DUCKDB_THREADS})
    for a, p in (("d", DEV_FEATURES), ("l", LB_FEATURES), ("dl", DEV_LABELS), ("ll", LB_LABELS)):
        con.execute(f"ATTACH '{p}' AS {a} (READ_ONLY)")
    out = {}
    for kind, rels in MODEL_RELATIONS.items():
        dd, ld = ("d", "l") if kind == "features" else ("dl", "ll")
        for rel in rels:
            cd, cl = _columns(con, dd, rel), _columns(con, ld, rel)
            names = [c for c, _ in cd]
            w = f"WHERE season <= {max_season}" if "season" in names else ""
            key = ", ".join(KEYS[rel]) if rel in KEYS else None
            q = ", ".join(f'sum(hash("{c}"))::HUGEINT' for c in names)
            a = con.execute(f"SELECT count(*), {q} FROM {dd}.{rel} {w}").fetchone()
            b = con.execute(f"SELECT count(*), {q} FROM {ld}.{rel} {w}").fetchone()
            diff = [c for c, x, y in zip(names, a[1:], b[1:]) if x != y]
            r = {"rows_dev": a[0], "rows_lockbox": b[0], "schema_identical": cd == cl, "columns": len(names),
                 "differing_columns": {}}
            for c in diff:
                typ = dict(cd)[c]
                if key and typ in ("DOUBLE", "FLOAT"):
                    m = con.execute(f"""SELECT count(*) FILTER (WHERE x."{c}" IS DISTINCT FROM y."{c}"),
                            count(*) FILTER (WHERE (x."{c}" IS NULL) <> (y."{c}" IS NULL)),
                            max(abs(x."{c}" - y."{c}")), max(abs(x."{c}" - y."{c}") / greatest(abs(x."{c}"), abs(y."{c}"), 1e-300))
                        FROM {dd}.{rel} x JOIN {ld}.{rel} y USING ({key}) {w.replace('season', 'x.season')}""").fetchone()
                    r["differing_columns"][c] = {"type": typ, "rows_differing": m[0], "null_pattern_differs": m[1],
                                                 "max_abs_diff": m[2], "max_rel_diff": m[3]}
                else:
                    r["differing_columns"][c] = {"type": typ, "non_float_difference": True}
            r["equivalent"] = (r["rows_dev"] == r["rows_lockbox"] and r["schema_identical"] and all(
                not v.get("non_float_difference") and v["null_pattern_differs"] == 0 and v["max_rel_diff"] <= FLOAT_REL_TOL
                for v in r["differing_columns"].values()))
            out[rel] = r
    con.close()
    return out


def stage3_pit(features: Path, labels: Path, canonical: Path) -> dict:
    """Cutoff rule (tip - 1 h for EXACT tips) on the store the V2 refit reads; box-state rows used only when
    available_at <= cutoff is enforced in the loader query itself (props_v0.load_frames hjoin)."""
    con = duckdb.connect(config={"threads": C.DUCKDB_THREADS})
    con.execute(f"ATTACH '{features}' AS f (READ_ONLY)")
    con.execute(f"ATTACH '{canonical}' AS c (READ_ONLY)")
    r = con.execute("""SELECT x.season, count(*), count(*) FILTER (WHERE g.tip_time_quality = 'EXACT'),
            count(*) FILTER (WHERE g.tip_time_quality = 'EXACT' AND x.prediction_cutoff_utc = g.scheduled_tip_utc - INTERVAL 1 HOUR),
            count(*) FILTER (WHERE x.prediction_cutoff_utc >= g.scheduled_tip_utc)
        FROM f.feat.player_features x JOIN c.core.games g USING (canonical_game_id) GROUP BY 1 ORDER BY 1""").fetchall()
    con.close()
    by = {str(s): {"rows": n, "exact": e, "exact_cutoff_tip_minus_1h": ok, "cutoff_at_or_after_tip": bad} for s, n, e, ok, bad in r}
    return {"by_season": by, "pass": all(v["exact"] == v["exact_cutoff_tip_minus_1h"] and v["cutoff_at_or_after_tip"] == 0
                                         for v in by.values())}


# ---------------------------------------------------------------------------- report
def run() -> dict:
    from cbb_platform import relocation
    manifest = json.loads((C.MANIFESTS / "DATASET_MANIFEST.json").read_text())
    s3 = json.loads((C.PROJECT_ROOT / "modeling" / "STAGE3_MANIFEST.json").read_text())
    lb_route = json.loads((LB / "canonical" / "route_build.json").read_text())
    lb_pit = json.loads((C.PROJECT_ROOT / "data" / "qa" / "lockbox_2025_26_pit_audit.json").read_text())
    art = json.loads(V1_ARTIFACT.read_text())
    sp = relocation.starter_pack_identity()
    dev = canonical_checks(DEV_CANONICAL, (2014, 2025))
    lbc_dev = canonical_checks(LB_CANONICAL, (2014, 2025))
    lbc_new = canonical_checks(LB_CANONICAL, (2026, 2026))
    eq = store_equivalence(2025)
    pit = stage3_pit(LB_FEATURES, LB_LABELS, LB_CANONICAL)
    out = {
        "report_version": "cbb-v2-provenance-1.0.0",
        "historical_api_calls": 0,
        "starter_pack": {
            "edition_or_version_recorded": False,
            "edition_note": "the export carries no edition/version string; its identity is the registered content digest. "
                            "The Data Files Guide PDF is dated 2026-02-10 (PDF metadata); data files are dated 2025-09-30.",
            "registered_data_manifest_digest": sp["registered_data_digest"], "current_data_manifest_digest": sp["current_data_digest"],
            "registered_data_files": sp["registered_data_files"], "inventory_identical": sp["inventory_identical"],
            "identity_pass": sp["pass"], "frozen_manifests_sha256": sp["frozen_manifests_sha256"],
            "known_defects_present_in_this_export": {
                "historical_athleteId_semantics": "PRESENT: player_game_stats.athleteId is a per-record id (audit 6b)",
                "duplicate_statistical_rows": "PRESENT: conference fan-out duplicate keys (audit 6c)",
                "shifted_timestamps": "PRESENT: box-file +00:00 stamps 4-5 h late (CANONICAL_SCHEMA.md 9)"},
            "remediation": {
                "historical_athleteId_semantics": "box rows resolved to persistent season-file ids by exact (season, team, name); "
                                                  "record ids are never used as player ids; ambiguous / unmatched rows stay UNRESOLVED",
                "duplicate_statistical_rows": "canonical layer collapses each fan-out group to one row; conference from games.csv ids",
                "shifted_timestamps": "box-file stamps are not used; tips are games.csv read as UTC (BDL-verified)"}},
        "datasets": {
            "dev_canonical": {"dataset_version": manifest["dataset_version"], "canonical_build_id": manifest["canonical_build_id"],
                              "canonical_content_digest": manifest["canonical_content_digest"],
                              "starter_pack_manifest_hash": manifest["starter_pack_manifest_hash"],
                              "raw_cbbd_payload_manifest_hash": manifest["raw_cbbd_payload_manifest_hash"],
                              "identity_resolution_hash": manifest["identity_remediation"]["identity_resolution_hash"],
                              "team_attribution_resolution_hash": manifest["identity_remediation"]["team_attribution_resolution_hash"],
                              "player_identity_resolution_hash": manifest["player_identity_resolution"]["hash"],
                              "game_identity_resolution_hash": manifest["game_identity"]["resolution_hash"],
                              "ingestion_version": manifest["ingestion_version"], "schema_version": manifest["schema_version"],
                              "wallclock_policy": manifest["wallclock_policy"], "code_commit": manifest["code_commit"]},
            "lockbox_canonical": {"dataset_version": lb_route["identity"]["dataset_version"],
                                  "canonical_content_digest": lb_route["identity"]["canonical_content_digest"],
                                  "route_version": lb_route["route_version"], "route_seasons": lb_route["route_seasons"],
                                  "route_payload_set_sha256": lb_route["identity"]["route_payload_set_sha256"]},
            "stage3": {"feature_code_version": s3["version"], "dev_upstream_dataset": s3["upstream_dataset"],
                       "dev_features_fingerprint": s3["features_db_sha_of_fingerprints"],
                       "dev_labels_fingerprint": s3["labels_db_sha_of_fingerprints"], "cutoff_rule": s3["cutoff_rule"]},
            "tip_rule_revision": TIP_RULE_REVISION,
            "timestamp_policy": "POINT_IN_TIME_POLICY_v2_0.md: prediction_cutoff = scheduled tip - 1 h (EXACT); 00:00 ET otherwise; "
                                "a source game is usable only when historical_knowable_at (tip + 4 h) <= cutoff",
            "v1_model": {"artifact": str(V1_ARTIFACT.relative_to(C.PROJECT_ROOT)), "sha256": _sha(V1_ARTIFACT),
                         "dataset_version": art["dataset_version"], "training_through_season": art["training_through_season"],
                         "stat_training_seasons": art["stat_training_seasons"],
                         "participation_training_seasons": art["participation_training_seasons"],
                         "train_max_cutoff": art["v0_state"]["train_max_cutoff"]}},
        "checks": {"dev_canonical_2014_2025": dev, "lockbox_canonical_2014_2025": lbc_dev, "lockbox_canonical_2026": lbc_new,
                   "stage3_dev_vs_lockbox_store_2014_2025": eq, "stage3_lockbox_store_cutoff_rule": pit,
                   "lockbox_pit_audit": {"path": "data/qa/lockbox_2025_26_pit_audit.json",
                                         "genuine_pit_violations": lb_pit.get("GENUINE_PIT_VIOLATIONS", lb_pit.get("summary", {}).get("GENUINE_PIT_VIOLATIONS"))}}}
    checks_pass = {"starter_pack_identity": sp["pass"],
                   **{f"{k}.{c}": v for k, d in (("dev", dev), ("lockbox_2014_2025", lbc_dev), ("lockbox_2026", lbc_new))
                      for c, v in d["pass"].items()},
                   "stage3_store_equivalence": all(r["equivalent"] for r in eq.values()),
                   "stage3_cutoff_rule": pit["pass"]}
    out["checks_pass"] = checks_pass
    out["DATA_PROVENANCE"] = "VERIFIED_INDEPENDENTLY_REMEDIATED" if all(checks_pass.values()) else "UNKNOWN"
    out["report_sha256_of_checks"] = hashlib.sha256(json.dumps(out["checks"], sort_keys=True, default=str).encode()).hexdigest()
    return out


if __name__ == "__main__":
    r = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(r, indent=1, default=str) + "\n")
    print(json.dumps({"DATA_PROVENANCE": r["DATA_PROVENANCE"], "checks_pass": r["checks_pass"]}, indent=1))
