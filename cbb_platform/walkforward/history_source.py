"""Historical source of the daily walk-forward lifecycle: CONTROL (the original frozen stores) or CANDIDATE (one compact,
verbatim Method-B DuckDB). Selection only: no feature value, training rule, algorithm, seed, threshold or architecture
depends on it.

    CONTROL    data/lockbox_2025_26/canonical/cbb.duckdb (scoring relations, via prod/features.compact_canonical)
               data/lockbox_2025_26/stage3/features.duckdb + labels.duckdb (marginal / participation / team loaders)
               modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet + _F2023.parquet (phase3.load_store)
    CANDIDATE  cbb_production_history_minimal.duckdb: exactly the rows / columns those readers consume, copied verbatim
               (schemas flagship / core / metadata = history_extract.RELATIONS; feat / ex / lab = the Stage 3 subset;
               oos = the two Phase 1B stores' STORE_COLS rows in file order; meta = the OOS arrow schema)

Selection: env CBB_HISTORY_SOURCE = CONTROL (default) | CANDIDATE; CBB_HISTORY_CANDIDATE = the candidate path
(default DEFAULT_CANDIDATE). A CANDIDATE is used only after its sha256 and bytes equal its manifest and the manifest's
recorded source hashes equal the frozen production manifest's data_versions (identity() raises otherwise).

The frozen readers are not edited. In CANDIDATE mode active() points them at the candidate for the duration of one
walk-forward job: provenance.LB_FEATURES / LB_LABELS (structural.load_team_frame, store.opening_through_date; the
explicit features_db / labels_db arguments of marginal.load_frames / load_participation) and phase3.load_store
(replaced by load_store_candidate: the same body over the candidate's oos tables, cast back to the parquet arrow
schema). Every patched attribute is restored on exit.

    PYTHONPATH=. .venv/bin/python -m cbb_platform.walkforward.history_source build  [--out PATH]
    PYTHONPATH=. .venv/bin/python -m cbb_platform.walkforward.history_source verify [--candidate PATH]
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa

from cbb_platform import config as C, duckdb_limits as DLM
from cbb_platform.prod import config as PC, history_extract as HX

VERSION = "cbb-production-history-minimal-1.0.0"
ENV_SOURCE, ENV_CANDIDATE = "CBB_HISTORY_SOURCE", "CBB_HISTORY_CANDIDATE"
SOURCES = ("CONTROL", "CANDIDATE")
QA_DIR = C.PROJECT_ROOT / "data" / "qa" / "footprint_minimization"
DEFAULT_CANDIDATE = QA_DIR / "cbb_production_history_minimal.duckdb"
FROZEN_MANIFEST = PC.MODEL_DIR / "jm1_final_production_manifest_2026_27.json"
LO, HI = 2014, PC.TRAINING_THROUGH            # marginal / team loaders: 2013-14 .. 2025-26
PART_SEASONS = (2024, 2025, 2026)             # marginal.load_participation (PARTICIPATION_FIRST_SEASON .. 2025-26)


class HistorySourceError(RuntimeError):
    pass


def manifest_path(candidate: Path) -> Path:
    return Path(candidate).with_name(Path(candidate).stem + "_manifest.json")


def selected() -> str:
    s = os.environ.get(ENV_SOURCE, "CONTROL").strip().upper()
    if s not in SOURCES:
        raise HistorySourceError(f"{ENV_SOURCE}={s!r}: must be one of {SOURCES}")
    return s


def candidate_path() -> Path:
    return Path(os.environ.get(ENV_CANDIDATE) or DEFAULT_CANDIDATE)


# ============================================================================ the consumed subset (traced, audit §3/§5)
def _columns() -> dict:
    from cbb_platform import props_v0 as P, props_v0_participation as Q
    from cbb_platform.v2 import phase3 as P3
    cfg = P.load_config()
    sums = sorted({c for cs in cfg["history_sums"].values() for c in cs})
    pf = (["player_game_example_id", "canonical_game_id", "canonical_player_id", "canonical_team_id", "season",
           "prediction_cutoff_utc", "is_home", "prof_last_box_position", "cold_start_class", "pp_last_game_participation",
           "pstd_sum_min", "pstd_appearances"] + [f"pstd_sum_{c}" for c in sums] + list(cfg["minutes_features"]) + list(Q.NUMERIC))
    box = ["canonical_player_id", "season", "available_at", "source_game_id", "s_min", "n_app", "last_position"] + \
        [f"s_{c}" for c in sums]
    targets = list(cfg["count_targets"].values())
    return {"player_features": list(dict.fromkeys(pf)), "player_box_state": list(dict.fromkeys(box)),
            "team_features": ["game_example_id", "canonical_game_id", "canonical_team_id", "opponent_team_id", "season",
                              "is_home", "neutral_site", "prediction_cutoff_utc", "res_state_available_at",
                              "box_state_available_at", "te_games_boxed", "te_pace_per40", "te_off_eff", "te_def_eff",
                              "tp_prev_off_eff", "tp_prev_def_eff"],
            "player_game_examples": ["player_game_example_id", "ev_prior_season_roster", "ev_transfer_preseason",
                                     "ev_recruit_preseason"],
            "game_examples": ["season", "game_date_et"],
            "player_game_labels": ["player_game_example_id", "min"] + targets +
                                  ["counting_labels_available", "participation_status", "box_row_present",
                                   "minutes_label_available"],
            "team_game_labels": ["game_example_id", "canonical_team_id", "team_points", "possessions_source", "game_minutes"],
            "oos": list(P3.STORE_COLS)}


def _sources() -> dict:
    dv = json.loads(FROZEN_MANIFEST.read_text())["data_versions"]
    return {"history_canonical": PC.HISTORY_CANONICAL, **{k: C.PROJECT_ROOT / v["path"] for k, v in dv.items()}}


def fingerprint(con, rel: str) -> dict:
    """history_extract.fingerprint (row count, columns, order-independent row-hash aggregates) + an ORDER-dependent
    aggregate (row hash combined with its scan position): equal fingerprints = the same rows in the same order."""
    fp = HX.fingerprint(con, rel)
    fp["ordered_hash_sum"] = con.execute(f"""SELECT coalesce(sum(hash(t, rn))::HUGEINT, 0)::VARCHAR
        FROM (SELECT *, row_number() OVER () AS rn FROM {rel}) t""").fetchone()[0]
    return fp


def _q(cols) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def build(out: Path = DEFAULT_CANDIDATE) -> dict:
    """Verbatim extraction (no rebuild, no ETL, no Starter Pack read). Single-threaded, fixed statement order: two builds
    of the same sources hold identical relations with identical row order (manifest `relations`, incl. the ordered
    hash). DuckDB 1.5.6 does not zero block padding (two writes of one uncompressed table differ in padding bytes and
    block checksums only), so the FILE bytes are pinned once by candidate_sha256: the candidate is immutable and
    verified by that hash, never rebuilt in place."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = _sources()
    for k, p in src.items():
        if not p.exists():
            raise HistorySourceError(f"source {k} ({p}) not present")
    cols = _columns()
    tmp = out.with_suffix(".building")
    for p in (tmp, out):
        if p.exists():
            p.unlink()
    # built and verified in memory, then written relation by relation in a fixed order, one checkpoint each (a single
    # COPY FROM DATABASE or an incremental on-disk build gave a different block layout per run; measured 2026-11-04)
    con = duckdb.connect(":memory:", config={"threads": 1, "preserve_insertion_order": True})
    con.execute("SET TimeZone = 'UTC'")
    rels, filters = {}, {}
    # ---------------------------------------------------------------- scoring relations (history_extract.RELATIONS)
    con.execute(f"ATTACH '{src['history_canonical']}' AS hist (READ_ONLY)")
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA {s}")
    for rel, where in HX.RELATIONS.items():
        con.execute(f"CREATE TABLE {rel} AS SELECT * FROM hist.{rel} WHERE {where}")
        if HX.fingerprint(con, f"hist.{rel}", where) != HX.fingerprint(con, rel):
            raise HistorySourceError(f"{rel}: copy differs from the frozen source")
        filters[rel] = where
    con.execute("DETACH hist")
    # ---------------------------------------------------------------- Stage 3 subset
    con.execute(f"ATTACH '{src['features_duckdb']}' AS s3f (READ_ONLY)")
    con.execute(f"ATTACH '{src['labels_duckdb']}' AS s3l (READ_ONLY)")
    for s in ("feat", "ex", "lab", "oos", "meta"):
        con.execute(f"CREATE SCHEMA {s}")
    sw = f"season BETWEEN {LO} AND {HI}"
    plan = [
        ("feat.player_features", f"SELECT {_q(cols['player_features'])} FROM s3f.feat.player_features WHERE {sw}", sw),
        ("feat.player_box_state", f"SELECT {_q(cols['player_box_state'])} FROM s3f.feat.player_box_state WHERE {sw}", sw),
        ("feat.team_features", f"SELECT {_q(cols['team_features'])} FROM s3f.feat.team_features WHERE {sw}", sw),
        ("ex.player_game_examples",
         f"SELECT {_q(cols['player_game_examples'])} FROM s3f.ex.player_game_examples WHERE season IN {PART_SEASONS}",
         f"season IN {PART_SEASONS}"),
        ("ex.game_examples", f"""SELECT DISTINCT season, game_date_et FROM s3f.ex.game_examples WHERE season = {HI}
             AND game_date_et = (SELECT max(game_date_et) FROM s3f.ex.game_examples WHERE season = {HI})""",
         f"season = {HI} AND game_date_et = max(game_date_et) of season {HI} (store.opening_through_date)"),
        ("lab.player_game_labels", f"""SELECT {_q(cols['player_game_labels'])} FROM s3l.lab.player_game_labels
             WHERE player_game_example_id IN (SELECT player_game_example_id FROM feat.player_features)""",
         "player_game_example_id IN feat.player_features"),
        ("lab.team_game_labels", f"""SELECT {_q(cols['team_game_labels'])} FROM s3l.lab.team_game_labels l
             WHERE (game_example_id, canonical_team_id) IN (SELECT (game_example_id, canonical_team_id) FROM feat.team_features)""",
         "(game_example_id, canonical_team_id) IN feat.team_features"),
    ]
    for rel, sql, where in plan:
        con.execute(f"CREATE TABLE {rel} AS {sql}")
        filters[rel] = where
    con.execute("DETACH s3f")
    con.execute("DETACH s3l")
    # ---------------------------------------------------------------- Phase 1B OOS (file order, STORE_COLS)
    import pyarrow.parquet as pq
    from cbb_platform.v2 import dependence as D
    oos = {"oos.phase1b_v1ref": (src["phase1b_oos_store"], f"forecast_season IN {tuple(D.OOS_SEASONS)}"),
           "oos.phase1b_v1ref_F2023": (src["phase1b_F2023_extension"], "TRUE")}
    schemas = []
    for rel, (path, where) in oos.items():
        con.execute(f"CREATE TABLE {rel} AS SELECT {_q(cols['oos'])} FROM read_parquet('{path}') WHERE {where}")
        sch = pq.read_schema(path)
        sub = pa.schema([sch.field(c) for c in cols["oos"]])
        schemas.append({"relation": rel, "arrow_schema_b64": base64.b64encode(sub.serialize().to_pybytes()).decode()})
        filters[rel] = where
    con.execute("CREATE TABLE meta.oos_arrow_schema (relation VARCHAR, arrow_schema_b64 VARCHAR)")
    con.executemany("INSERT INTO meta.oos_arrow_schema VALUES (?, ?)", [[s["relation"], s["arrow_schema_b64"]] for s in schemas])
    filters["meta.oos_arrow_schema"] = "the source parquet arrow schema of each oos relation (STORE_COLS)"
    for rel in list(HX.RELATIONS) + [r for r, *_ in plan] + list(oos) + ["meta.oos_arrow_schema"]:
        rels[rel] = {"filter": filters[rel], **fingerprint(con, rel)}
    con.execute("SET checkpoint_threshold = '1TB'")      # no intermediate auto-checkpoint: one final write at DETACH
    con.execute(f"ATTACH '{tmp}' AS outdb")
    for sch in sorted({r.split(".")[0] for r in rels}):
        con.execute(f"CREATE SCHEMA outdb.{sch}")
    con.execute("CHECKPOINT outdb")
    for rel in sorted(rels):                          # fixed order, one relation per checkpoint (deterministic layout)
        con.execute(f"CREATE TABLE outdb.{rel} AS SELECT * FROM memory.{rel}")
        con.execute("CHECKPOINT outdb")
    for rel, r in rels.items():                       # the written file holds exactly the verified relations
        if fingerprint(con, f"outdb.{rel}") != {k: v for k, v in r.items() if k != "filter"}:
            raise HistorySourceError(f"{rel}: written candidate differs from the extracted relation")
    con.execute("DETACH outdb")
    con.close()
    os.replace(tmp, out)
    man = {"version": VERSION, "format": "duckdb", "candidate": PC.rel(out), "candidate_sha256": HX.sha256_file(out),
           "candidate_bytes": out.stat().st_size,
           "sources": {k: {"path": PC.rel(p), "bytes": p.stat().st_size, "sha256": HX.sha256_file(p)} for k, p in src.items()},
           "excluded": {"modeling/v2_oos/oos_forecasts_phase1.parquet": "RESEARCH_ONLY (audit 2026-11-04)",
                        "data/lockbox_2025_26/canonical/parquet/": "EXCLUDE (audit 2026-11-04)"},
           "relations": rels, "columns": cols, "model_refit": False, "historical_rebuild": False}
    dv = json.loads(FROZEN_MANIFEST.read_text())["data_versions"]
    bad = [k for k in dv if man["sources"][k]["sha256"] != dv[k]["sha256"]]
    if bad:
        raise HistorySourceError(f"source stores differ from the frozen manifest: {bad}")
    manifest_path(out).write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return man


# ============================================================================ verification + identity
def verify_candidate(candidate: Path | None = None, full: bool = True) -> dict:
    """Candidate file vs its manifest (sha256 + bytes); recorded sources vs the frozen manifest's data_versions."""
    cand = Path(candidate or candidate_path())
    mp = manifest_path(cand)
    if not cand.exists() or not mp.exists():
        raise HistorySourceError(f"candidate {cand} or its manifest is absent")
    man = json.loads(mp.read_text())
    if cand.stat().st_size != man["candidate_bytes"]:
        raise HistorySourceError("candidate bytes differ from its manifest")
    if full and HX.sha256_file(cand) != man["candidate_sha256"]:
        raise HistorySourceError("candidate sha256 differs from its manifest")
    dv = json.loads(FROZEN_MANIFEST.read_text())["data_versions"]
    bad = [k for k, v in dv.items() if man["sources"].get(k, {}).get("sha256") != v["sha256"]]
    if bad:
        raise HistorySourceError(f"candidate was extracted from stores other than the frozen data_versions: {bad}")
    return man


def identity() -> dict:
    """daily.history_identity: the frozen data_versions ids. CONTROL re-checks the original stores' byte sizes;
    CANDIDATE verifies the candidate (sha256 + bytes vs manifest, sources = data_versions)."""
    dv = json.loads(FROZEN_MANIFEST.read_text())["data_versions"]
    if selected() == "CANDIDATE":
        verify_candidate()
        return {k: v["sha256"] for k, v in dv.items()}
    sizes = {k: (C.PROJECT_ROOT / v["path"]).stat().st_size == v["bytes"] for k, v in dv.items()}
    if not all(sizes.values()):
        raise RuntimeError(f"a frozen training store changed size: {sizes}")
    return {k: v["sha256"] for k, v in dv.items()}


def history_db() -> Path:
    """The scoring-relation source the walk-forward feature / label builds read (compact_canonical)."""
    return candidate_path() if selected() == "CANDIDATE" else PC.HISTORY_CANONICAL


def stage3_paths() -> tuple[Path, Path]:
    from cbb_platform.v2 import provenance as PV
    if selected() == "CANDIDATE":
        return candidate_path(), candidate_path()
    return PV.LB_FEATURES, PV.LB_LABELS


def load_store_candidate(seasons) -> pd.DataFrame:
    """phase3.load_store, the same body, reading the candidate's oos tables (file order) and casting each part back to
    the source parquet's arrow schema before to_pandas (the frame pd.read_parquet would return)."""
    from cbb_platform.v2 import dependence as D, phase3 as P3, structural as S
    seasons = S.check_seasons(seasons)
    con = duckdb.connect(str(candidate_path()), read_only=True, config={"threads": 1})
    con.execute("SET TimeZone = 'UTC'")
    sch = {r: pa.ipc.read_schema(pa.py_buffer(base64.b64decode(b)))
           for r, b in con.execute("SELECT relation, arrow_schema_b64 FROM meta.oos_arrow_schema").fetchall()}

    def part(rel: str, where: str) -> pd.DataFrame:
        t = con.execute(f"SELECT {_q(P3.STORE_COLS)} FROM {rel} WHERE {where}").to_arrow_table().cast(sch[rel])
        df = t.to_pandas(self_destruct=True)         # same frame; the Arrow buffers are released while converting
        del t
        for c in [c for c in df.columns if isinstance(df[c].array, pd.arrays.ArrowStringArray)]:
            df[c] = DLM._owned(df[c].array)          # zero-copy strings would keep DuckDB's whole batches alive
        DLM.release()
        return df
    parts = []
    main = [s for s in seasons if s in D.OOS_SEASONS]
    if main:
        parts.append(part("oos.phase1b_v1ref", f"forecast_season IN ({', '.join(map(str, main))})"))
    if P3.OOS_FIRST in seasons:
        parts.append(part("oos.phase1b_v1ref_F2023", "TRUE"))
    con.close()
    if set(seasons) - set(main) - {P3.OOS_FIRST}:
        raise S.V2Phase3Violation(f"no Phase 1B OOS forecasts for {seasons}")
    df = pd.concat(parts, ignore_index=True)
    if not (df["training_through_season"] < df["forecast_season"]).all():
        raise S.V2Phase3Violation("a forecast row was not out of sample")
    if not set(df["forecast_season"]) <= set(seasons):
        raise S.V2Phase3Violation("store filter leaked another season")
    df["role_diagnostic"] = df["role_diagnostic"].fillna("NO_ROLE_HISTORY")
    df["position_group"] = df["position_group"].fillna("U")
    return df


@contextlib.contextmanager
def active():
    """Point the frozen walk-forward readers at the selected source for one job (no-op for CONTROL)."""
    if selected() == "CONTROL":
        yield "CONTROL"
        return
    verify_candidate()
    from cbb_platform.v2 import phase3 as P3, provenance as PV
    cand = candidate_path()
    saved = (PV.LB_FEATURES, PV.LB_LABELS, P3.load_store)
    PV.LB_FEATURES, PV.LB_LABELS, P3.load_store = cand, cand, load_store_candidate
    try:
        yield "CANDIDATE"
    finally:
        PV.LB_FEATURES, PV.LB_LABELS, P3.load_store = saved


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.walkforward.history_source")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, default=DEFAULT_CANDIDATE)
    v = sub.add_parser("verify")
    v.add_argument("--candidate", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        m = build(a.out)
        print(json.dumps({k: m[k] for k in ("candidate", "candidate_sha256", "candidate_bytes")}, indent=1))
    else:
        m = verify_candidate(a.candidate)
        print(json.dumps({"PASS": True, "candidate_sha256": m["candidate_sha256"]}, indent=1))


if __name__ == "__main__":
    main()
