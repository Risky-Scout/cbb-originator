"""Stage 2U -- deterministic dataset version for the accepted Stage 2 output.

    .venv/bin/python -m cbb_platform.dataset_manifest

DATASET_VERSION = "cbb_data_v2.4+" + first 12 hex of sha256(starter pack data digest | raw CBBD payload
manifest hash | canonical content fingerprints | game identity resolution hash | game status hash | player identity
resolution hash | team-attribution resolution hash).
v2.4 = Pre-Model Integrity Closure v1 (team-attribution / cluster identity remediation, cbb_platform/team_attribution.py);
parent cbb_data_v2.3+7bef640cccde (Stage 3.1 identity repairs) <- v2.2 (played-game status) <- v2.1 (game identity) <- v2.0. Every ancestor manifest is kept verbatim as data/manifests/DATASET_MANIFEST_<version>.json. Identical inputs and identical canonical content give the
identical version; any change to raw payloads or to canonical content gives a new one. Every future model
experiment records this identifier as its upstream dataset.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone

import duckdb

from cbb_platform import config as C, lockbox, raw_store

OUT = C.MANIFESTS / "DATASET_MANIFEST.json"
PARENT_VERSION = "cbb_data_v2.3+7bef640cccde"
PARENT_MANIFEST = C.MANIFESTS / f"DATASET_MANIFEST_{PARENT_VERSION}.json"
CBBD_API_SCHEMA_VERSION = "CBBD OpenAPI v1.29.0 (reference/CBBD_OPENAPI_VERIFICATION_v1_1.md)"


def raw_payload_manifest_hash(provider: str = "cbbd") -> tuple[str, int, dict]:
    """sha256 over the sorted (relative path, original-bytes sha256) of every stored raw payload."""
    recs = raw_store.records(provider)
    lines = sorted(f"{p.relative_to(C.RAW)}|{m['sha256']}" for p, m in recs)
    by = {}
    for _, m in recs:
        b = by.setdefault(m["endpoint"], {"payloads": 0, "empty": 0, "bytes": 0})
        b["payloads"] += 1
        b["empty"] += int(m.get("size_bytes", 0) <= 2)
        b["bytes"] += m.get("size_bytes", 0)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines), dict(sorted(by.items()))


def git_sha() -> dict:
    run = lambda *a: subprocess.run(["git", *a], cwd=C.PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    return {"head": run("rev-parse", "HEAD") or None,
            "tracked_changes_at_build": bool(run("status", "--porcelain", "--untracked-files=no"))}


def identity_remediation(db_path=None) -> dict:
    """Pre-Model Integrity Closure v1: what the v2.4 team-attribution / identity layer changed (all derived rows)."""
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    q = lambda sql: con.execute(sql).fetchall()
    ta = q("SELECT * EXCLUDE (created_by_version) FROM metadata.team_attribution_resolution ORDER BY ALL")
    rows = q("SELECT * EXCLUDE (created_by_version) FROM metadata.player_game_team_attribution ORDER BY ALL")
    links = q("SELECT * EXCLUDE (created_by_version) FROM metadata.identity_link_quarantine ORDER BY ALL")
    quar = q("SELECT rule_id, table_name, canonical_player_id, canonical_game_id, season FROM qa.identity_remediation_quarantine ORDER BY ALL")
    v24 = q("""SELECT rule_id, table_name, rows_affected, provider_athlete_id, from_canonical_player_id FROM metadata.player_identity_resolution
               WHERE created_by_version LIKE 'pmic1-%' ORDER BY ALL""")
    collided = {r[4] for r in v24 if r[4]}
    players = ({r[3] for r in rows if r[3]} | {r[2] for r in quar if r[2]} | collided)
    games = ({r[0] for r in ta} | {r[3] for r in quar if r[3]}
             | {g for (g,) in q("""SELECT DISTINCT canonical_game_id FROM fact.player_games WHERE identity_status = 'QUARANTINED_SOURCE_ID_COLLISION'""")})
    event_tables = ("fact.play_participants", "fact.play_on_floor", "fact.substitutions", "fact.shots")
    out = {
        "team_attribution_resolution_hash": hashlib.sha256(json.dumps([ta, rows, links], default=str).encode()).hexdigest(),
        "identity_resolution_hash": hashlib.sha256(json.dumps(v24, default=str).encode()).hexdigest(),
        "games_by_decision": {d: n for d, n in q("SELECT decision, count(*) FROM metadata.team_attribution_resolution GROUP BY 1 ORDER BY 1")},
        "canonical_players_changed": len(players),
        "canonical_games_affected": len(games),
        "player_game_rows_restored": sum(1 for r in rows if r[4] == "KEEP"),
        "player_game_rows_quarantined_mirror_copies": sum(1 for r in rows if r[4] == "QUARANTINE"),
        "player_game_rows_quarantined_source_id_collision": q("SELECT count(*) FROM fact.player_games WHERE identity_status = 'QUARANTINED_SOURCE_ID_COLLISION'")[0][0],
        "player_season_rows_quarantined": sum(1 for r in quar if r[1] in ("fact.player_season_stats", "fact.player_season_shooting")),
        "roster_rows_quarantined": sum(1 for r in quar if r[1] == "core.rosters"),
        "event_actor_rows_remapped": sum(r[2] for r in v24 if any(r[1].startswith(t) for t in event_tables)),
        "event_games_quarantined_team_not_in_game": q("""SELECT count(*) FROM metadata.team_attribution_resolution
                                                           WHERE defect_class = 'EVENT_TEAM_NOT_IN_GAME'""")[0][0],
        "identity_links_quarantined": len(links),
        "identity_links_quarantined_by_reason": {r: n for r, n in q("SELECT reason, count(*) FROM metadata.identity_link_quarantine GROUP BY 1 ORDER BY 1")},
        "rules": sorted({r[0] for r in v24}),
    }
    con.close()
    return out


def build(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    build_id = con.execute("SELECT max(build_id) FROM metadata.data_versions").fetchone()[0]
    tables = {t: {"rows": n, "fingerprint": fp} for t, n, fp in con.execute(
        "SELECT table_name, row_count, content_fingerprint FROM metadata.data_versions WHERE build_id = ? ORDER BY 1",
        [build_id]).fetchall()}
    built_at = con.execute("SELECT max(built_at) FROM metadata.data_versions WHERE build_id = ?", [build_id]).fetchone()[0]
    defects = {f"{f}:{r}": n for f, r, n in con.execute(
        "SELECT family, reason_code, count(*) FROM qa.source_defects GROUP BY 1, 2 ORDER BY 1, 2").fetchall()}
    unresolved = {
        "player_games_unresolved": con.execute("SELECT count(*) FROM fact.player_games WHERE canonical_player_id IS NULL").fetchone()[0],
        "event_onfloor_rows_unresolved": con.execute("SELECT count(*) FROM fact.play_on_floor WHERE canonical_player_id IS NULL").fetchone()[0],
        "substitution_rows_unresolved": con.execute("SELECT count(*) FROM fact.substitutions WHERE canonical_player_id IS NULL").fetchone()[0],
        "transfers_unresolved_or_candidate": con.execute("SELECT count(*) FROM core.transfers WHERE canonical_player_id IS NULL").fetchone()[0],
        "recruits_unresolved_or_candidate": con.execute("SELECT count(*) FROM core.recruits WHERE canonical_player_id IS NULL").fetchone()[0],
    }
    event_seasons = {f: [r[0] for r in con.execute(f"SELECT DISTINCT season FROM {t} WHERE season IS NOT NULL ORDER BY 1").fetchall()]
                     for f, t in (("plays", "fact.plays"), ("substitutions", "fact.substitutions"),
                                  ("lineup_stats", "fact.lineup_stats"), ("rosters", "core.rosters"))}
    seasons = [r[0] for r in con.execute("SELECT DISTINCT season FROM core.games WHERE season BETWEEN 2005 AND 2025 ORDER BY 1").fetchall()]
    con.close()
    reg = json.loads((C.MANIFESTS / "starter_pack_registration.json").read_text())
    raw_hash, n_raw, raw_by = raw_payload_manifest_hash()
    canon_digest = hashlib.sha256(json.dumps({t: v["fingerprint"] for t, v in tables.items()}, sort_keys=True).encode()).hexdigest()
    gi = json.loads((C.QA / "game_identity_resolution.json").read_text())
    gs = json.loads((C.QA / "game_status_resolution.json").read_text())
    con2 = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    pid_rows = con2.execute("SELECT * EXCLUDE (created_by_version) FROM metadata.player_identity_resolution ORDER BY ALL").fetchall()
    # v2.4: the status table's evidence text counts player box rows, so the team-attribution repair changes it for the
    # repaired games (status / exclusion unchanged). The hash is recomputed here with the Stage 2.2 formula
    # (game_status_report.py) instead of regenerating the Stage 2.2 report documents.
    gs_hash_db = hashlib.sha256(json.dumps(con2.execute(
        "SELECT * EXCLUDE (created_by_version) FROM metadata.game_status_resolution ORDER BY canonical_game_id").fetchall(),
        default=str).encode()).hexdigest()
    gs_counts_db = dict(con2.execute("SELECT status, count(*) FROM metadata.game_status_resolution GROUP BY 1 ORDER BY 1").fetchall())
    gs_excluded_db = con2.execute("SELECT count(*) FROM metadata.game_status_resolution WHERE excluded_from_played_games").fetchone()[0]
    con2.close()
    if gs_counts_db != gs["all_status_counts"] or gs_excluded_db != gs["excluded_from_played_games"]:
        raise RuntimeError("game status counts changed vs the Stage 2.2 report: regenerate and review game status first")
    pid_hash = hashlib.sha256(json.dumps(pid_rows, default=str).encode()).hexdigest()
    remediation = identity_remediation(db_path)
    version = "cbb_data_v2.4+" + hashlib.sha256(
        f"{reg['data_manifest_digest']}|{raw_hash}|{canon_digest}|{gi['resolution_hash']}|{gs_hash_db}|{pid_hash}|"
        f"{remediation['team_attribution_resolution_hash']}".encode()).hexdigest()[:12]
    parent = json.loads(PARENT_MANIFEST.read_text())
    man = {"dataset_version": version, "parent_dataset_version": PARENT_VERSION,
           "game_identity": {"resolution_hash": gi["resolution_hash"], "map_rows": gi["map_rows"], "by_class": gi["by_class"],
                             "raw_source_games": gi["raw_source_games"], "canonical_trusted_games": gi["trusted_games"],
                             "aliases": gi["aliases"], "quarantined_corrupt": gi["quarantined_corrupt"],
                             "affected_team_seasons": gi["affected_team_seasons"],
                             "removed_duplicate_contributions": gi["removed_duplicate_contributions"]},
           "lineage": ["cbb_data_v2.0+b00e8d3dc798", "cbb_data_v2.1+292c2d2b1739", "cbb_data_v2.2+95dd9c2fa5b7",
                       "cbb_data_v2.3+7bef640cccde"],
           "status": "RESEARCH_CANDIDATE (not MODEL_READY; see docs/PRE_MODEL_INTEGRITY_CLOSURE_v1_0.md)",
           "player_identity_resolution": {"hash": pid_hash, "rules": sorted({r[0] for r in pid_rows}),
                                          "rows_rewritten": sum(r[8] for r in pid_rows)},
           "identity_remediation": remediation,
           "game_status": {"status_hash": gs_hash_db, "status_hash_stage2_2_report": gs["status_hash"],
                           "status_and_exclusions_unchanged_vs_stage2_2_report": True,
                           "trusted_games": gs["trusted_games"], "played_games": gs["played_games"],
                           "excluded_from_played_games": gs["excluded_from_played_games"], "status_counts": gs["all_status_counts"],
                           "zero_zero_by_status": gs["zero_zero_by_status"], "affected_team_seasons": gs["affected_team_seasons"],
                           "excluded_in_model_window_2014_2025": gs["excluded_in_model_window"]},
           "pre_2005_policy": "OUT_OF_MODELING_SCOPE (preserved raw; never in trusted/modeling views)",
           "modeling_game_relation": "flagship.played_games",
           "wallclock_policy": "UNTRUSTED_MIXED (raw preserved; prohibited for PIT, availability, latency, tip, live timing)",
           "fingerprints_before": {t: v["fingerprint"] for t, v in parent["canonical_tables"].items()},
           "fingerprints_changed_vs_parent": sorted(t for t, v in tables.items()
                                                    if parent["canonical_tables"].get(t, {}).get("fingerprint") != v["fingerprint"]),
           "canonical_build_id": build_id, "build_timestamp_utc": str(built_at),
           "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "cbbd_api_schema_version": CBBD_API_SCHEMA_VERSION,
           "ingestion_version": C.INGESTION_VERSION, "schema_version": C.SCHEMA_VERSION,
           "starter_pack_manifest_hash": reg["data_manifest_digest"],
           "raw_cbbd_payload_manifest_hash": raw_hash, "raw_cbbd_payloads": n_raw, "raw_cbbd_payloads_by_endpoint": raw_by,
           "canonical_content_digest": canon_digest, "canonical_tables": tables,
           "included_seasons": seasons, "event_seasons_present": event_seasons,
           "excluded_lockbox_season": lockbox.LOCKBOX_LABEL, "lockbox_touched": False,
           "lockbox_exclusion": "2025-26 never fetched, read or inspected; canonical seasons <= 2024-25 (season 2025 end-year)",
           "code_commit": git_sha(), "unresolved_defect_counts": defects, "unresolved_identity_counts": unresolved}
    OUT.write_text(json.dumps(man, indent=1, default=str))
    return man


if __name__ == "__main__":
    m = build()
    print(m["dataset_version"], m["raw_cbbd_payloads"], m["code_commit"])
