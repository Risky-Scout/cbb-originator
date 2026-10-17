"""Stage 3 orchestrator (deterministic; DuckDB at 2 threads).

    .venv/bin/python -m cbb_platform.stage3.build

1. features DB (spec.FEATURES_DB): bind src (frozen v2.2 canonical DB, read-only) -> universes -> team / player /
   event / lineup state and features -> unbind (the stored DB holds no reference to the canonical DB)
2. labels DB (spec.LABELS_DB, separate file): bind src + attach the features DB read-only -> labels
3. season-partitioned Parquet exports, per-table content fingerprints
4. FEATURE_REGISTRY.json, walk-forward fold manifests, coverage audit, STAGE3_MANIFEST.json
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from cbb_platform import config as C
from cbb_platform.stage3 import event_stats, labels, lineup_stats, player_state, sources, spec, team_state, universe


def connect(path, threads: int | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    con.execute("SET enable_progress_bar = false")
    con.execute(f"SET threads = {threads or C.DUCKDB_THREADS}")
    con.execute("SET memory_limit = '12GB'")
    con.execute("SET preserve_insertion_order = true")
    tmp = spec.STORE / "duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{tmp}'")
    return con


def fingerprints(con, schemas: tuple[str, ...]) -> dict:
    out = {}
    for s, t in con.execute(f"SELECT schema_name, table_name FROM duckdb_tables() WHERE schema_name IN {schemas} "
                            "AND NOT temporary ORDER BY 1, 2").fetchall():
        n, x, sm = con.execute(f"SELECT count(*), bit_xor(hash(r)), sum(hash(r)) FROM {s}.{t} r").fetchone()
        out[f"{s}.{t}"] = {"rows": n, "fingerprint": f"{n}:{x}:{sm}"}
    return out


def build_features(db_path: Path, canonical=None) -> dict:
    tmp = Path(str(db_path) + ".building")
    if tmp.exists():
        tmp.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(tmp)
    sources.bind(con, canonical)
    universe.build(con)
    team_state.build(con)
    player_state.build(con)
    event_stats.build(con)
    lineup_stats.build(con)
    sources.unbind(con)
    fp = fingerprints(con, ("ex", "feat"))
    con.close()
    os.replace(tmp, db_path)
    return fp


def build_labels(db_path: Path, features_db: Path, canonical=None) -> dict:
    tmp = Path(str(db_path) + ".building")
    if tmp.exists():
        tmp.unlink()
    con = connect(tmp)
    sources.bind(con, canonical)
    con.execute(f"ATTACH '{features_db}' AS fx (READ_ONLY)")
    labels.build(con)
    con.execute("DETACH fx")
    sources.unbind(con)
    fp = fingerprints(con, ("lab",))
    con.close()
    os.replace(tmp, db_path)
    return fp


def export_parquet(db_path: Path, schemas: tuple[str, ...], root: Path) -> list[str]:
    con = connect(db_path)
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for s, t in con.execute(f"SELECT schema_name, table_name FROM duckdb_tables() WHERE schema_name IN {schemas} ORDER BY 1, 2").fetchall():
        out = root / t
        if out.exists():
            shutil.rmtree(out)
        cols = [c[0] for c in con.execute(f"DESCRIBE {s}.{t}").fetchall()]
        if "season" in cols:
            con.execute(f"COPY (SELECT * FROM {s}.{t} ORDER BY ALL) TO '{out}' (FORMAT parquet, PARTITION_BY (season), COMPRESSION zstd)")
        else:
            out.mkdir(parents=True)
            con.execute(f"COPY (SELECT * FROM {s}.{t} ORDER BY ALL) TO '{out / 'data.parquet'}' (FORMAT parquet, COMPRESSION zstd)")
        written.append(f"{s}.{t}")
    con.close()
    return written


def write_folds(features_db: Path, labels_db: Path) -> dict:
    con = connect(features_db)
    con.execute(f"ATTACH '{labels_db}' AS lb (READ_ONLY)")
    h = lambda sql: con.execute(f"SELECT count(*), sha256(coalesce(string_agg(k, ',' ORDER BY k), '')) FROM ({sql})").fetchone()
    seasons_of = lambda f: (f["train_seasons"], [f["validation_season"]])
    out = {"protocol": "expanding-window walk-forward; one validation season per fold; lockbox excluded", "upstream_dataset":
           spec.UPSTREAM_DATASET, "lockbox": spec.LOCKBOX, "subsystem_windows": spec.SUBSYSTEM_WINDOWS, "folds": []}
    for f in spec.CORE_FOLDS + [{"fold": "LINEUP_ROTATION_DEV", "train_seasons": [2024], "validation_season": 2025}]:
        entry = {"fold": f["fold"], "train_seasons": f["train_seasons"], "validation_season": f["validation_season"]}
        for part, seasons in zip(("train", "validation"), seasons_of(f)):
            sl = ", ".join(map(str, seasons))
            g = h(f"SELECT game_example_id AS k FROM ex.game_examples WHERE season IN ({sl})")
            p = h(f"SELECT player_game_example_id AS k FROM ex.player_game_examples WHERE season IN ({sl})")
            lab = h(f"SELECT player_game_example_id || ':' || coalesce(pts::VARCHAR, 'NULL') || ':' || coalesce(min::VARCHAR, 'NULL') || ':' "
                    f"|| participation_status AS k FROM lb.lab.player_game_labels WHERE season IN ({sl})")
            feats = {}
            for t in ("feat.team_features", "feat.player_features", "feat.team_event_features", "feat.player_shot_features",
                      "feat.player_lineup_features"):
                n, x, s = con.execute(f"SELECT count(*), bit_xor(hash(r)), sum(hash(r)) FROM (SELECT * FROM {t} WHERE season IN ({sl})) r").fetchone()
                feats[t] = {"rows": n, "fingerprint": f"{n}:{x}:{s}"}
            entry[part] = {"seasons": seasons, "game_examples": {"count": g[0], "sha256": g[1]},
                           "player_game_examples": {"count": p[0], "sha256": p[1]},
                           "player_game_labels": {"count": lab[0], "sha256": lab[1]}, "feature_sets": feats}
        out["folds"].append(entry)
    con.close()
    spec.FOLDS_DIR.mkdir(parents=True, exist_ok=True)
    (spec.FOLDS_DIR / "core_walk_forward.json").write_text(json.dumps(
        {"folds": spec.CORE_FOLDS, "lockbox": spec.LOCKBOX, "subsystem_windows": spec.SUBSYSTEM_WINDOWS,
         "rule": "The lockbox is not a tuning fold; no model or feature choice may be selected from lockbox performance."}, indent=1))
    (spec.FOLDS_DIR / "fold_manifests.json").write_text(json.dumps(out, indent=1))
    return out


def build(root: Path | None = None, canonical=None, with_reports: bool = True) -> dict:
    """root=None builds the project store; a temp root is used by the determinism test."""
    store = root or spec.STORE
    fdb, ldb = store / "features.duckdb", store / "labels.duckdb"
    t0 = datetime.now(timezone.utc)
    ffp = build_features(fdb, canonical)
    lfp = build_labels(ldb, fdb, canonical)
    result = {"features": ffp, "labels": lfp}
    if not with_reports:
        return result
    export_parquet(fdb, ("ex", "feat"), spec.FEATURES_PARQUET)
    export_parquet(ldb, ("lab",), spec.LABELS_PARQUET)
    return reports(result, t0)


def reports(result: dict | None = None, t0=None) -> dict:
    """Registry, fold manifests, coverage and the Stage 3 manifest from the built stores (no rebuild)."""
    from cbb_platform.stage3 import coverage, registry
    fdb, ldb = spec.FEATURES_DB, spec.LABELS_DB
    t0 = t0 or datetime.now(timezone.utc)
    if result is None:
        c1, c2 = connect(fdb), connect(ldb)
        result = {"features": fingerprints(c1, ("ex", "feat")), "labels": fingerprints(c2, ("lab",))}
        c1.close(), c2.close()
    ffp, lfp = result["features"], result["labels"]
    con = connect(fdb)
    reg = registry.build(con)
    con.close()
    folds = write_folds(fdb, ldb)
    cov = coverage.run(fdb, ldb)
    man = {"stage": "3", "version": spec.STAGE3_VERSION, "upstream_dataset": spec.UPSTREAM_DATASET,
           "built_at_utc": t0.isoformat(timespec="seconds"), "model_seasons": [spec.MODEL_SEASONS[0], spec.MODEL_SEASONS[-1]],
           "lockbox_season": "2025-26", "lockbox_touched": False, "cutoff_rule": spec.CUTOFF_RULE,
           "feature_registry_count": reg["feature_count"], "fingerprints": result,
           "features_db_sha_of_fingerprints": hashlib.sha256(json.dumps(ffp, sort_keys=True).encode()).hexdigest(),
           "labels_db_sha_of_fingerprints": hashlib.sha256(json.dumps(lfp, sort_keys=True).encode()).hexdigest(),
           "folds": [f["fold"] for f in folds["folds"]], "coverage_summary": cov["summary"]}
    spec.STAGE3_MANIFEST.write_text(json.dumps(man, indent=1, default=str))
    return result


if __name__ == "__main__":
    import sys
    import time
    t = time.time()
    r = reports() if sys.argv[1:] == ["reports"] else build()
    print({k: len(v) for k, v in r.items()}, f"{time.time() - t:.0f}s")
