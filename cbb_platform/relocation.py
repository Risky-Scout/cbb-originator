"""Relocation-safe resolution of frozen absolute paths (architect directive 2026-10-16, readiness closure §2).

The repository moved, with its registered sibling Starter Pack, from HISTORICAL_PROJECT_PARENT to its current parent.
Frozen evidence keeps the historical absolute paths exactly as recorded (starter_pack_registration.json,
starter_pack_sha256.json, the workspace snapshots, and the persistent Starter Pack staging views inside the frozen
development DuckDB). A historical path is never an artifact identity: a recorded location resolves to the current one
ONLY for this one known relocation, and ONLY after the registered content is proven identical (file inventory and the
registered data digest). Nothing here writes a manifest or the development database.

    starter_pack_identity()          registered Starter Pack content == current Starter Pack content
    resolve(path)                    historical recorded path -> current path (the known relocation only)
    connect_canonical_relocated()    read-only canonical DB connection whose Starter Pack staging views read the
                                     verified current Starter Pack (TEMPORARY overlay; the database file is untouched)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from cbb_platform import config as C

HISTORICAL_PROJECT_PARENT = Path("/Users/josephshackelford/CBB Predictive Model 2026")    # before 2026-10-15
REGISTRATION = C.MANIFESTS / "starter_pack_registration.json"
FILE_MANIFEST = C.MANIFESTS / "starter_pack_sha256.json"


class RelocationError(RuntimeError):
    pass


def relocated() -> bool:
    """True only for the known move: this project was HISTORICAL_PROJECT_PARENT/<name> and is no longer there."""
    return C.PROJECT_ROOT.parent != HISTORICAL_PROJECT_PARENT and not (HISTORICAL_PROJECT_PARENT / C.PROJECT_ROOT.name).exists()


def resolve(recorded) -> Path:
    """A recorded absolute path under HISTORICAL_PROJECT_PARENT -> the same relative path under the current parent.
    Any other path (or no relocation) is returned unchanged; a path is never rewritten in the evidence itself."""
    p = Path(recorded)
    if relocated() and p.is_relative_to(HISTORICAL_PROJECT_PARENT):
        return C.PROJECT_ROOT.parent / p.relative_to(HISTORICAL_PROJECT_PARENT)
    return p


def starter_pack_identity() -> dict:
    """The registration contract (build_canonical.register_starter_pack): digest of every data/ file except Finder
    .DS_Store metadata; plus the full file inventory of starter_pack_sha256.json under the same exclusion."""
    from cbb_platform.audit_starter_pack import starter_pack_manifest
    from cbb_platform.build_canonical import _manifest_digest
    reg, inv = json.loads(REGISTRATION.read_text()), json.loads(FILE_MANIFEST.read_text())
    current = starter_pack_manifest()
    data_only = {k: v for k, v in current.items() if k.startswith("data/") and not k.endswith(".DS_Store")}
    no_ds = lambda m: {k: v for k, v in m.items() if not k.endswith(".DS_Store")}
    recorded_roots = {reg["starter_pack_root"], inv["starter_pack_root"]}
    out = {"registered_root": reg["starter_pack_root"], "current_root": str(C.STARTER_PACK_ROOT),
           "resolved_root": str(resolve(reg["starter_pack_root"])),
           "registered_data_files": reg["data_file_count"], "current_data_files": len(data_only),
           "registered_data_digest": reg["data_manifest_digest"], "current_data_digest": _manifest_digest(data_only),
           "inventory_files": len(no_ds(inv["files"])), "inventory_identical": no_ds(current) == no_ds(inv["files"]),
           "excluded": ".DS_Store (Finder metadata; excluded by the registration contract)",
           "frozen_manifests_sha256": {str(p.relative_to(C.PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in (REGISTRATION, FILE_MANIFEST)}}
    out["pass"] = (len(recorded_roots) == 1 and Path(out["resolved_root"]) == C.STARTER_PACK_ROOT
                   and out["registered_data_files"] == out["current_data_files"]
                   and out["registered_data_digest"] == out["current_data_digest"] and out["inventory_identical"])
    return out


def connect_canonical_relocated(path: Path | None = None, identity: dict | None = None) -> duckdb.DuckDBPyConnection:
    """A session over the frozen canonical DB attached READ_ONLY, in which every persistent staging view that reads the
    registered (historical) Starter Pack root is shadowed by an in-memory view with the identical definition bound to the
    current root. Refuses unless the Starter Pack content identity is proven. The view text is otherwise unchanged
    (the filename lineage strips the root, so `filename` values are identical)."""
    ident = identity or starter_pack_identity()
    if not ident["pass"]:
        raise RelocationError(f"Starter Pack content identity not proven; refusing to rebind: {ident}")
    old, new = ident["registered_root"], str(C.STARTER_PACK_ROOT)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    con.execute(f"ATTACH '{path or C.DUCKDB_PATH}' AS cbb (READ_ONLY)")
    con.execute("USE cbb")
    views = con.execute("""SELECT view_name, sql FROM duckdb_views()
        WHERE database_name = 'cbb' AND schema_name = 'staging' AND NOT internal ORDER BY 1""").fetchall()
    con.execute("ATTACH ':memory:' AS reloc")
    con.execute("CREATE SCHEMA reloc.staging")
    for name, sql in views:
        if old == new or old not in sql:
            continue
        body = sql.replace(old, new)
        head = f"CREATE VIEW staging.{name} AS "
        if not body.startswith(head):
            raise RelocationError(f"unexpected view definition for staging.{name}")
        con.execute(f"CREATE VIEW reloc.staging.{name} AS {body[len(head):]}")
    con.execute("SET search_path = 'cbb.main,reloc.staging'")      # staging.<view> -> the rebound overlay first
    return con


def bound_views(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT view_name FROM duckdb_views() WHERE database_name = 'reloc' ORDER BY 1").fetchall()]
