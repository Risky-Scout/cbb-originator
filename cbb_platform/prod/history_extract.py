"""Compact, read-only extract of the frozen through-2025-26 canonical history: exactly the relations the production
path reads, nothing else. It lets the shadow tick run on a machine that does not hold the 8.8 GB lockbox canonical DB
(a GitHub-hosted runner). No row is changed, filtered by outcome or re-derived; the extract is a byte-for-byte subset
of the frozen relations, verified relation by relation against the source.

Readers (and the rows they take from `hist`):
    features.compact_canonical   flagship.played_games / team_game_outcomes / player_game_outcomes (seasons 2013-14..
                                 2025-26), flagship.rosters, core.transfers, core.recruits, core.team_seasons,
                                 core.teams, core.players, metadata.identity_link_quarantine (all rows),
                                 flagship.substitutions (target season only: none in history), and the empty schemas of
                                 flagship.plays / shots_trusted / on_floor_trusted
    cbbd_feed._seed / completed   core.players, metadata.player_id_map (provider CBBD), flagship.rosters (2025-26)
    sync.run / run._names         core.teams, core.players

    .venv/bin/python -m cbb_platform.prod.history_extract build  [--out data/prod/history/history_extract.duckdb]
    .venv/bin/python -m cbb_platform.prod.history_extract verify [--extract ...]   # against the frozen source
The manifest (cbb_platform/prod/history_extract_manifest.json, tracked) pins the extract's sha256 and every relation's
row count + content fingerprint. resolve() uses the frozen source when present, else the extract only if its sha256
matches the manifest; anything else raises (no guessing, no rebuild on the fly).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import duckdb

from cbb_platform.prod import config as PC

EXTRACT_VERSION = "cbb-prod-history-extract-1.0.0"
MANIFEST = Path(__file__).with_name("history_extract_manifest.json")
DEFAULT_OUT = PC.PROD_ROOT / "history" / "history_extract.duckdb"
_SEASONS = f"season BETWEEN {PC.HISTORY_FIRST_SEASON} AND {PC.TRAINING_THROUGH}"
RELATIONS = {                       # relation -> row filter (exactly what the production readers select from history)
    "flagship.played_games": _SEASONS,
    "flagship.team_game_outcomes": _SEASONS,
    "flagship.player_game_outcomes": _SEASONS,
    "flagship.rosters": "TRUE",
    "core.transfers": "TRUE",
    "core.recruits": "TRUE",
    "core.team_seasons": "TRUE",
    "core.teams": "TRUE",
    "core.players": "TRUE",
    "metadata.identity_link_quarantine": "TRUE",
    "metadata.player_id_map": "provider = 'CBBD'",
    "flagship.substitutions": "FALSE",          # schema only: compact_canonical reads the TARGET season (2026-27) only
    "flagship.plays": "FALSE",                  # schema only (no frozen JM1_FINAL input reads event streams)
    "flagship.shots_trusted": "FALSE",
    "flagship.on_floor_trusted": "FALSE",
}


class HistoryUnavailable(RuntimeError):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(con, src: str, where: str = "TRUE") -> dict:
    """Order-independent content fingerprint of a relation: row count, column (name, type) list, and two independent
    aggregates of the per-row hash (sum and xor), so equal fingerprints mean equal multisets of rows."""
    cols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
    n, s, x = con.execute(f"SELECT count(*), coalesce(sum(hash(t))::HUGEINT, 0)::VARCHAR, coalesce(bit_xor(hash(t)), 0)::VARCHAR "
                          f"FROM (SELECT * FROM {src} WHERE {where}) t").fetchone()
    return {"rows": int(n), "columns": [f"{a}:{b}" for a, b in cols], "hash_sum": s, "hash_xor": x}


def build(out: Path = DEFAULT_OUT, history_db: Path = PC.HISTORY_CANONICAL, write_manifest: bool = True) -> dict:
    out = Path(out)
    if not Path(history_db).exists():
        raise HistoryUnavailable(f"frozen history {history_db} not present")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    for p in (tmp, out):
        if p.exists():
            p.unlink()
    con = duckdb.connect(str(tmp), config={"threads": 4})
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{history_db}' AS hist (READ_ONLY)")
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA {s}")
    rels = {}
    for rel, where in RELATIONS.items():
        con.execute(f"CREATE TABLE {rel} AS SELECT * FROM hist.{rel} WHERE {where}")
        src, got = fingerprint(con, f"hist.{rel}", where), fingerprint(con, rel)
        if src != got:
            con.close()
            tmp.unlink()
            raise HistoryUnavailable(f"{rel}: extract differs from the frozen source")
        rels[rel] = {"filter": where, **got}
    con.execute("DETACH hist")
    con.execute("CHECKPOINT")
    con.close()
    os.replace(tmp, out)
    man = {"extract_version": EXTRACT_VERSION, "source": PC.rel(history_db), "source_role": "frozen, read-only, seasons <= 2025-26",
           "training_through": PC.TRAINING_THROUGH, "history_first_season": PC.HISTORY_FIRST_SEASON,
           "extract_sha256": sha256_file(out), "extract_bytes": out.stat().st_size, "relations": rels,
           "model_refit": False, "historical_rebuild": False}
    if write_manifest:
        MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    return man


def verify(extract: Path = DEFAULT_OUT, history_db: Path | None = PC.HISTORY_CANONICAL) -> dict:
    """Extract sha256 vs the manifest; relation fingerprints vs the manifest; and, when the frozen source is present,
    vs the source itself."""
    man = json.loads(MANIFEST.read_text())
    got_sha = sha256_file(extract)
    out = {"extract_sha256_matches": got_sha == man["extract_sha256"], "relations": {}}
    con = duckdb.connect(str(extract), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    src = history_db is not None and Path(history_db).exists()
    if src:
        con.execute(f"ATTACH '{history_db}' AS hist (READ_ONLY)")
    for rel, m in man["relations"].items():
        fp = fingerprint(con, rel)
        r = {"matches_manifest": fp == {k: m[k] for k in fp}}
        if src:
            r["matches_source"] = fingerprint(con, f"hist.{rel}", m["filter"]) == fp
        out["relations"][rel] = r
    con.close()
    out["PASS"] = out["extract_sha256_matches"] and all(all(v.values()) for v in out["relations"].values())
    out["checked_against_source"] = bool(src)
    return out


def resolve(extract: Path = DEFAULT_OUT, history_db: Path = PC.HISTORY_CANONICAL) -> Path:
    """The history database the production path reads: the frozen source when present; otherwise the extract, only if
    its sha256 equals the tracked manifest. Raises HistoryUnavailable otherwise."""
    if Path(history_db).exists():
        return Path(history_db)
    if not Path(extract).exists():
        raise HistoryUnavailable(f"neither the frozen history nor the extract ({extract}) is present")
    want = json.loads(MANIFEST.read_text())["extract_sha256"]
    if sha256_file(extract) != want:
        raise HistoryUnavailable("history extract sha256 differs from the tracked manifest")
    return Path(extract)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.history_extract")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    v = sub.add_parser("verify")
    v.add_argument("--extract", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    out = build(a.out) if a.cmd == "build" else verify(a.extract)
    print(json.dumps(out if a.cmd == "verify" else {k: v for k, v in out.items() if k != "relations"}, indent=1))


if __name__ == "__main__":
    main()
