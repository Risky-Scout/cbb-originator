"""Stage 2U -- deterministic dataset version for the accepted Stage 2 output.

    .venv/bin/python -m cbb_platform.dataset_manifest

DATASET_VERSION = "cbb_data_v2.0+" + first 12 hex of sha256(starter pack data digest | raw CBBD payload
manifest hash | canonical content fingerprints). Identical inputs and identical canonical content give the
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


def build(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True)
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
    version = "cbb_data_v2.0+" + hashlib.sha256(f"{reg['data_manifest_digest']}|{raw_hash}|{canon_digest}".encode()).hexdigest()[:12]
    man = {"dataset_version": version, "canonical_build_id": build_id, "build_timestamp_utc": str(built_at),
           "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "cbbd_api_schema_version": CBBD_API_SCHEMA_VERSION,
           "ingestion_version": C.INGESTION_VERSION, "schema_version": C.SCHEMA_VERSION,
           "starter_pack_manifest_hash": reg["data_manifest_digest"],
           "raw_cbbd_payload_manifest_hash": raw_hash, "raw_cbbd_payloads": n_raw, "raw_cbbd_payloads_by_endpoint": raw_by,
           "canonical_content_digest": canon_digest, "canonical_tables": tables,
           "included_seasons": seasons, "event_seasons_present": event_seasons,
           "excluded_lockbox_season": lockbox.LOCKBOX_LABEL, "lockbox_touched": False,
           "code_commit": git_sha(), "unresolved_defect_counts": defects, "unresolved_identity_counts": unresolved}
    OUT.write_text(json.dumps(man, indent=1, default=str))
    return man


if __name__ == "__main__":
    m = build()
    print(m["dataset_version"], m["raw_cbbd_payloads"], m["code_commit"])
