"""Step 1 — Physical audit of the immutable CBB Starter Pack.

Everything reported here is MEASURED from the CSV bytes on disk. Nothing is taken
from headers.md, the notebooks, or API documentation.

Outputs
    data/manifests/starter_pack_sha256.json   SHA-256 of every Starter Pack file
    data/qa/starter_pack_inventory.json       full machine-readable inventory
    data/qa/starter_pack_inventory.csv        long format: one row per (file, column)
    docs/STARTER_PACK_PHYSICAL_AUDIT.md       human-readable report generated from the JSON

Run:  .venv/bin/python -m cbb_platform.audit_starter_pack
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from cbb_platform import config as C


# ---------------------------------------------------------------------------
# Hash manifest (used before AND after every run to prove immutability)
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def starter_pack_manifest() -> dict[str, dict]:
    """Return {relative_path: {sha256, size_bytes}} for every file in the Starter Pack."""
    out = {}
    for p in sorted(C.STARTER_PACK_ROOT.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(C.STARTER_PACK_ROOT))] = {
                "sha256": sha256_file(p),
                "size_bytes": p.stat().st_size,
            }
    return out


# ---------------------------------------------------------------------------
# Per-file measurements
# ---------------------------------------------------------------------------
def _q(col: str) -> str:
    """Quote a column identifier for DuckDB."""
    return '"' + col.replace('"', '""') + '"'


def _season_from_name(path: Path) -> int | None:
    return int(path.stem) if path.stem.isdigit() else None


def audit_file(con: duckdb.DuckDBPyConnection, family: str, path: Path, key_cols: list[str]) -> dict:
    rel = str(path.relative_to(C.STARTER_PACK_ROOT))
    # sample_size=-1 makes DuckDB infer each column type from ALL rows, not a sample.
    src = f"read_csv('{path}', sample_size=-1, header=true)"
    con.execute(f"CREATE OR REPLACE TEMP VIEW f AS SELECT * FROM {src}")
    described = con.execute("DESCRIBE f").fetchall()
    columns = [(r[0], r[1]) for r in described]

    n_rows = con.execute("SELECT count(*) FROM f").fetchone()[0]
    non_null = con.execute(
        "SELECT " + ", ".join(f"count({_q(c)})" for c, _ in columns) + " FROM f"
    ).fetchone()
    col_info = [
        {
            "position": i + 1,
            "name": c,
            "inferred_type": t,
            "null_count": n_rows - nn,
            "null_rate": round((n_rows - nn) / n_rows, 6) if n_rows else None,
        }
        for i, ((c, t), nn) in enumerate(zip(columns, non_null))
    ]
    names = [c for c, _ in columns]

    # Duplicate candidate keys: rows that share a key with at least one other row.
    key_present = [k for k in key_cols if k in names]
    dup = {"candidate_key": key_cols, "key_columns_present": key_present == key_cols}
    if key_present == key_cols:
        kc = ", ".join(_q(k) for k in key_cols)
        groups, rows_in_groups, excess = con.execute(
            f"""SELECT count(*), coalesce(sum(c),0), coalesce(sum(c-1),0)
                FROM (SELECT {kc}, count(*) c FROM f GROUP BY {kc} HAVING count(*) > 1)"""
        ).fetchone()
        null_keys = con.execute(
            "SELECT count(*) FROM f WHERE " + " OR ".join(f"{_q(k)} IS NULL" for k in key_cols)
        ).fetchone()[0]
        dup.update(duplicate_key_groups=groups, rows_in_duplicate_groups=int(rows_in_groups),
                   excess_duplicate_rows=int(excess), rows_with_null_key=null_keys)

    # Dates, teams, players where the family has them.
    earliest = latest = None
    date_col = C.FAMILY_DATE_COL.get(family)
    if date_col in names:
        earliest, latest = con.execute(
            f"SELECT min({_q(date_col)})::VARCHAR, max({_q(date_col)})::VARCHAR FROM f"
        ).fetchone()
    team_cols = [c for c in C.FAMILY_TEAM_COLS.get(family, []) if c in names]
    unique_teams = None
    if team_cols:
        union = " UNION ".join(f"SELECT {_q(c)} AS t FROM f" for c in team_cols)
        unique_teams = con.execute(f"SELECT count(DISTINCT t) FROM ({union})").fetchone()[0]
    player_col = C.FAMILY_PLAYER_COL.get(family)
    unique_players = None
    if player_col in names:
        unique_players = con.execute(f"SELECT count(DISTINCT {_q(player_col)}) FROM f").fetchone()[0]

    seasons_in_file = None
    if "season" in names:
        seasons_in_file = [r[0] for r in con.execute(
            "SELECT DISTINCT season FROM f WHERE season IS NOT NULL ORDER BY 1").fetchall()]

    return {
        "relative_path": rel,
        "family": family,
        "season": _season_from_name(path),
        "seasons_in_file": seasons_in_file,
        "file_size_bytes": path.stat().st_size,
        "row_count": n_rows,
        "column_count": len(columns),
        "column_names": names,
        "columns": col_info,
        "duplicates": dup,
        "earliest_date": earliest,
        "latest_date": latest,
        "date_column": date_col if date_col in names else None,
        "unique_team_count": unique_teams,
        "unique_player_count": unique_players,
    }


def schema_differences(files: list[dict]) -> dict:
    """Compare per-season files of one family by column name, order, and inferred type."""
    union: list[str] = []
    for f in files:
        for c in f["column_names"]:
            if c not in union:
                union.append(c)
    types: dict[str, dict] = {}
    for f in files:
        for col in f["columns"]:
            types.setdefault(col["name"], {})[f["relative_path"]] = col["inferred_type"]
    per_file = []
    for f in files:
        per_file.append({
            "relative_path": f["relative_path"],
            "missing_vs_union": [c for c in union if c not in f["column_names"]],
            "column_order_signature": hashlib.md5(",".join(f["column_names"]).encode()).hexdigest()[:10],
        })
    type_variation = {
        c: sorted(set(v.values())) for c, v in types.items() if len(set(v.values())) > 1
    }
    return {
        "union_column_count": len(union),
        "distinct_column_sets": len({tuple(sorted(f["column_names"])) for f in files}),
        "distinct_column_orders": len({tuple(f["column_names"]) for f in files}),
        "per_file": per_file,
        "columns_with_type_variation_across_files": type_variation,
    }


# ---------------------------------------------------------------------------
# Cross-file semantic measurements that matter for the frozen plan
# ---------------------------------------------------------------------------
def cross_checks(con: duckdb.DuckDBPyConnection) -> dict:
    d = C.STARTER_PACK_DATA
    con.execute("SET TimeZone='UTC'")
    con.execute(f"CREATE OR REPLACE TEMP VIEW g AS SELECT * FROM read_csv('{d}/games.csv', all_varchar=true)")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW tg AS SELECT * FROM read_csv('{d}/team_game_stats/*.csv',
                    all_varchar=true, union_by_name=true)""")
    con.execute(f"""CREATE OR REPLACE TEMP VIEW pg AS SELECT * FROM read_csv('{d}/player_game_stats/*.csv',
                    all_varchar=true, union_by_name=true)""")
    out: dict = {}

    # 1. games.csv start_date has no offset; box-score files carry '+00:00'. Measure how they
    #    relate. (Which one is TRUE cannot be decided from the Starter Pack alone — it is decided
    #    by independent BDL tip times in the coverage audit.)
    rel = dict(con.execute("""
        SELECT CASE WHEN t.startDate::TIMESTAMPTZ = timezone('UTC', strptime(g.start_date,'%Y-%m-%d %H:%M:%S'))
                    THEN 'box_equals_games_csv_read_as_UTC'
                    WHEN t.startDate::TIMESTAMPTZ = timezone('America/New_York', strptime(g.start_date,'%Y-%m-%d %H:%M:%S'))
                    THEN 'box_equals_games_csv_read_as_US_Eastern'
                    ELSE 'neither' END, count(*)
        FROM g JOIN (SELECT DISTINCT gameId, startDate FROM tg) t ON t.gameId = g.id GROUP BY 1""").fetchall())
    out["games_start_date_timezone"] = {
        "relationship_counts": rel,
        "offsets_observed_in_box_files": sorted({r[0] for r in con.execute("SELECT DISTINCT substr(startDate,20) FROM tg").fetchall()}),
        "interpretation": ("The two files are internally consistent only if games.csv is US/Eastern. External truth "
                           "(BDL tip times, data/qa/COVERAGE_AUDIT.json -> tip_time_external_check) shows games.csv is "
                           "naive UTC, so the box-score '+00:00' stamps are 4-5 hours late. Canonical tip times use games.csv as UTC."),
    }

    # 2. Distribution of recorded tip times (UTC clock). 00:00 UTC = 7 pm EST / 8 pm EDT.
    out["tip_time_utc_clock_top10_season_2025"] = {
        r[0]: int(r[1]) for r in con.execute(
            "SELECT substr(start_date,12,5), count(*) FROM g WHERE season = '2025' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10").fetchall()
    }
    out["start_time_tbd_flag_counts_team_game_stats"] = {
        r[0]: int(r[1]) for r in con.execute("SELECT startTimeTbd, count(*) FROM tg GROUP BY 1").fetchall()
    }
    out["games_with_date_only_start_by_season"] = {
        int(s): int(c) for s, c in con.execute(
            "SELECT season, count(*) FROM g WHERE length(start_date) <= 10 GROUP BY 1 ORDER BY season::INT").fetchall()
    }

    # 3. Season-label convention: CBBD season = calendar year in which the season ENDS.
    out["season_label_convention"] = {
        "games_rows_where_season_equals_label_end_year": con.execute(
            "SELECT count(*) FROM g WHERE season = right(season_label, 4)").fetchone()[0],
        "games_rows": con.execute("SELECT count(*) FROM g").fetchone()[0],
    }

    # 4. Which games have box scores (by season).
    out["box_score_coverage_of_games_by_season"] = [
        {"season": int(r[0]), "games": r[1], "with_team_box": r[2], "with_player_box": r[3]}
        for r in con.execute("""
            SELECT g.season, count(*), count(t.gameId), count(p.game_id)
            FROM g LEFT JOIN (SELECT DISTINCT gameId FROM tg) t ON t.gameId = g.id
                   LEFT JOIN (SELECT DISTINCT game_id FROM pg) p ON p.game_id = g.id
            GROUP BY 1 ORDER BY season::INT""").fetchall()
    ]
    out["box_score_games_missing_from_games_csv"] = {
        "team_game_stats": con.execute(
            "SELECT count(DISTINCT gameId) FROM tg WHERE gameId NOT IN (SELECT id FROM g)").fetchone()[0],
        "player_game_stats": con.execute(
            "SELECT count(DISTINCT game_id) FROM pg WHERE game_id NOT IN (SELECT id FROM g)").fetchone()[0],
    }

    # 5. Team-rows per game in team_game_stats (a valid game has exactly 2).
    out["team_game_rows_per_game"] = {
        int(k): int(v) for k, v in con.execute(
            "SELECT c, count(*) FROM (SELECT gameId, count(*) c FROM tg GROUP BY 1) GROUP BY 1 ORDER BY 1").fetchall()
    }

    # 6. Referential integrity against teams.csv / conferences.csv.
    con.execute(f"CREATE OR REPLACE TEMP VIEW t AS SELECT * FROM read_csv('{d}/teams.csv', all_varchar=true)")
    con.execute(f"CREATE OR REPLACE TEMP VIEW cf AS SELECT * FROM read_csv('{d}/conferences.csv', all_varchar=true)")
    out["referential"] = {
        "game_team_ids_not_in_teams_csv": con.execute("""
            SELECT count(DISTINCT x) FROM (SELECT home_team_id x FROM g UNION SELECT away_team_id FROM g)
            WHERE x NOT IN (SELECT id FROM t)""").fetchone()[0],
        "game_conference_ids_not_in_conferences_csv": sorted(r[0] for r in con.execute("""
            SELECT DISTINCT x FROM (SELECT home_conference_id x FROM g UNION SELECT away_conference_id FROM g)
            WHERE x IS NOT NULL AND try_cast(x AS DOUBLE)::INT::VARCHAR NOT IN (SELECT id FROM cf)""").fetchall()),
        "games_with_null_home_or_away_conference_id": con.execute(
            "SELECT count(*) FROM g WHERE home_conference_id IS NULL OR away_conference_id IS NULL").fetchone()[0],
        "player_game_team_ids_not_in_teams_csv": con.execute(
            "SELECT count(DISTINCT team_id) FROM pg WHERE team_id NOT IN (SELECT id FROM t)").fetchone()[0],
    }

    # 7. Player identity: one athleteId -> how many distinct names? (name drift / ID reuse check)
    out["player_identity"] = {
        "player_game_rows_null_athleteId": con.execute("SELECT count(*) FROM pg WHERE athleteId IS NULL").fetchone()[0],
        "athleteIds_with_multiple_names": con.execute(
            "SELECT count(*) FROM (SELECT athleteId FROM pg GROUP BY 1 HAVING count(DISTINCT name) > 1)").fetchone()[0],
        "athleteIds_on_multiple_teams_same_season": con.execute(
            "SELECT count(*) FROM (SELECT athleteId, season FROM pg GROUP BY 1,2 HAVING count(DISTINCT team_id) > 1)").fetchone()[0],
        "athleteIds_appearing_in_multiple_seasons": con.execute(
            "SELECT count(*) FROM (SELECT athleteId FROM pg GROUP BY 1 HAVING count(DISTINCT season) > 1)").fetchone()[0],
        "position_values": {r[0]: int(r[1]) for r in con.execute(
            "SELECT coalesce(position,'<NULL>'), count(*) FROM pg GROUP BY 1 ORDER BY 2 DESC").fetchall()},
    }

    # 7b. CRITICAL: is player_game_stats.athleteId a persistent PLAYER id? Test it against
    #     player_season_stats.athleteId, which demonstrably persists across seasons.
    con.execute(f"""CREATE OR REPLACE TEMP VIEW ps AS SELECT * FROM read_csv('{d}/player_season_stats/*.csv',
                    all_varchar=true, union_by_name=true)""")
    rows_2025, ids_2025 = con.execute(
        "SELECT count(*), count(DISTINCT athleteId) FROM pg WHERE season = '2025'").fetchone()
    # Composite natural key (season, team, exact name) -> persistent id. Exact string equality only.
    con.execute("""CREATE OR REPLACE TEMP TABLE name_key AS
                   SELECT season, teamId, name, count(DISTINCT athleteId) n FROM ps GROUP BY 1,2,3""")
    resolv = dict(con.execute("""
        SELECT CASE WHEN k.n IS NULL THEN 'no_season_stats_match' WHEN k.n = 1 THEN 'unique_exact_match'
                    ELSE 'ambiguous_same_name' END, count(*)
        FROM (SELECT DISTINCT season, game_id, team_id, name, athleteId FROM pg) p
        LEFT JOIN name_key k ON k.season = p.season AND k.teamId = p.team_id AND k.name = p.name
        GROUP BY 1""").fetchall())
    out["player_game_athleteId_semantics"] = {
        "finding": "player_game_stats.athleteId is a per-player-game RECORD id, not a persistent player id",
        "season_2025_rows": rows_2025,
        "season_2025_distinct_athleteId": ids_2025,
        "player_season_athleteIds_spanning_multiple_seasons": con.execute(
            "SELECT count(*) FROM (SELECT athleteId FROM ps GROUP BY 1 HAVING count(DISTINCT season) > 1)").fetchone()[0],
        "player_game_ids_that_collide_numerically_with_player_season_ids": con.execute(
            "SELECT count(DISTINCT athleteId) FROM pg WHERE athleteId IN (SELECT athleteId FROM ps)").fetchone()[0],
        "exact_season_team_name_resolution_of_distinct_player_game_records": resolv,
    }

    # 7c. What differs inside duplicate-key groups? (fan-out vs genuine duplicate games)
    def _differing_cols(view: str, keys: list[str]) -> dict:
        cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
        k = ", ".join(keys)
        con.execute(f"""CREATE OR REPLACE TEMP TABLE dupg AS SELECT * FROM {view}
                        WHERE ({k}) IN (SELECT ({k}) FROM {view} GROUP BY {k} HAVING count(*) > 1)""")
        res = {}
        for col in cols:
            m = con.execute(f"""SELECT count(*) FROM (SELECT {k} FROM dupg GROUP BY {k}
                                HAVING count(DISTINCT coalesce({_q(col)}, '<NULL>')) > 1)""").fetchone()[0]
            if m:
                res[col] = m
        return res
    out["duplicate_groups_differing_columns"] = {
        "team_game_stats (gameId, teamId)": _differing_cols("tg", ["gameId", "teamId"]),
        "player_game_stats (game_id, athleteId)": _differing_cols("pg", ["game_id", "athleteId"]),
    }

    # 8. Player-game participation semantics: what values do minutes/starter take?
    out["player_game_participation_values"] = {
        "minutes_null": con.execute("SELECT count(*) FROM pg WHERE minutes IS NULL").fetchone()[0],
        "minutes_zero": con.execute("SELECT count(*) FROM pg WHERE try_cast(minutes AS DOUBLE) = 0").fetchone()[0],
        "minutes_non_integer": con.execute(
            "SELECT count(*) FROM pg WHERE try_cast(minutes AS DOUBLE) <> floor(try_cast(minutes AS DOUBLE))").fetchone()[0],
        "starter_values": {str(r[0]): int(r[1]) for r in con.execute(
            "SELECT starter, count(*) FROM pg GROUP BY 1").fetchall()},
        "ejected_values": {str(r[0]): int(r[1]) for r in con.execute(
            "SELECT ejected, count(*) FROM pg GROUP BY 1").fetchall()},
    }
    return out


# ---------------------------------------------------------------------------
# Coverage summary per family
# ---------------------------------------------------------------------------
def family_coverage(files: list[dict], family: str, con: duckdb.DuckDBPyConnection) -> dict:
    if family == "games":
        rows = con.execute(f"""SELECT season, count(*) FROM read_csv('{C.STARTER_PACK_DATA}/games.csv', all_varchar=true)
                               GROUP BY 1 ORDER BY season::INT""").fetchall()
        by_season = {int(s): n for s, n in rows}
    elif files and files[0]["season"] is not None:
        by_season = {f["season"]: f["row_count"] for f in files}
    else:
        return {"seasonal": False, "row_count": sum(f["row_count"] for f in files)}
    seasons = sorted(by_season)
    missing = [y for y in range(seasons[0], seasons[-1] + 1) if y not in by_season]
    return {
        "seasonal": True,
        "season_convention": "CBBD season = year the season ends (2025 = 2024-25)",
        "first_season": seasons[0],
        "last_season": seasons[-1],
        "seasons_present": len(seasons),
        "missing_seasons_within_range": missing,
        "row_count": sum(by_season.values()),
        "rows_by_season": by_season,
    }


def run() -> dict:
    con = duckdb.connect()
    manifest = starter_pack_manifest()
    files_by_family: dict[str, list[dict]] = {}
    for family, (pattern, key) in C.STARTER_FAMILIES.items():
        paths = sorted(C.STARTER_PACK_DATA.glob(pattern), key=lambda p: (len(p.stem), p.stem))
        files_by_family[family] = [audit_file(con, family, p, key) for p in paths]

    inventory = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ingestion_version": C.INGESTION_VERSION,
        "starter_pack_root": str(C.STARTER_PACK_ROOT),
        "starter_pack_file_count": len(manifest),
        "non_data_files": sorted(k for k in manifest if not k.startswith("data/")),
        "families": {
            fam: {
                "file_count": len(files),
                "coverage": family_coverage(files, fam, con),
                "schema_differences": schema_differences(files) if len(files) > 1 else None,
                "files": files,
            }
            for fam, files in files_by_family.items()
        },
        "cross_checks": cross_checks(con),
    }

    C.QA.mkdir(parents=True, exist_ok=True)
    C.MANIFESTS.mkdir(parents=True, exist_ok=True)
    (C.MANIFESTS / "starter_pack_sha256.json").write_text(json.dumps(
        {"starter_pack_root": str(C.STARTER_PACK_ROOT), "files": manifest}, indent=1, sort_keys=True))
    (C.QA / "starter_pack_inventory.json").write_text(json.dumps(inventory, indent=1, default=str))
    _write_inventory_csv(inventory)
    _write_markdown(inventory)
    return inventory


def _write_inventory_csv(inv: dict) -> None:
    fields = ["relative_path", "family", "season", "file_size_bytes", "row_count", "column_count",
              "unique_team_count", "unique_player_count", "earliest_date", "latest_date",
              "candidate_key", "duplicate_key_groups", "excess_duplicate_rows",
              "column_position", "column_name", "inferred_type", "null_count", "null_rate"]
    with open(C.QA / "starter_pack_inventory.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for fam in inv["families"].values():
            for f in fam["files"]:
                base = {k: f.get(k) for k in fields[:10]}
                base["candidate_key"] = "+".join(f["duplicates"]["candidate_key"])
                base["duplicate_key_groups"] = f["duplicates"].get("duplicate_key_groups")
                base["excess_duplicate_rows"] = f["duplicates"].get("excess_duplicate_rows")
                for col in f["columns"]:
                    w.writerow({**base, "column_position": col["position"], "column_name": col["name"],
                                "inferred_type": col["inferred_type"], "null_count": col["null_count"],
                                "null_rate": col["null_rate"]})


# ---------------------------------------------------------------------------
# Markdown report (generated from the inventory so it can never drift from it)
# ---------------------------------------------------------------------------
def _write_markdown(inv: dict) -> None:
    L: list[str] = []
    a = L.append
    a("# Starter Pack Physical Audit")
    a("")
    a(f"Generated by `python -m cbb_platform.audit_starter_pack` at {inv['generated_at_utc']} "
      f"(ingestion version `{inv['ingestion_version']}`). Every number below was measured from the CSV bytes; "
      "the machine-readable source is `data/qa/starter_pack_inventory.json` (long-form per-column detail in "
      "`data/qa/starter_pack_inventory.csv`). SHA-256 of every file: `data/manifests/starter_pack_sha256.json`.")
    a("")
    a(f"- Starter Pack root (read-only): `{inv['starter_pack_root']}`")
    a(f"- Files in Starter Pack: {inv['starter_pack_file_count']} "
      f"({len(inv['non_data_files'])} non-data files: notebooks, PDFs, headers.md, LICENSE, zip/.DS_Store).")
    a("")
    a("## 1. Tables found and measured coverage")
    a("")
    a("| Family | Files | Rows | First season | Last season | Missing seasons in range | Key | Dup key groups (total) |")
    a("|---|---:|---:|---:|---:|---|---|---:|")
    for fam, d in inv["families"].items():
        cov = d["coverage"]
        dups = sum(f["duplicates"].get("duplicate_key_groups") or 0 for f in d["files"])
        key = "+".join(d["files"][0]["duplicates"]["candidate_key"])
        if cov.get("seasonal"):
            a(f"| {fam} | {d['file_count']} | {cov['row_count']:,} | {cov['first_season']} | {cov['last_season']} | "
              f"{cov['missing_seasons_within_range'] or 'none'} | {key} | {dups} |")
        else:
            a(f"| {fam} | {d['file_count']} | {cov['row_count']:,} | n/a | n/a | n/a | {key} | {dups} |")
    a("")
    a("Season convention (measured): CBBD `season` is the year the season **ends** "
      f"({inv['cross_checks']['season_label_convention']['games_rows_where_season_equals_label_end_year']:,} of "
      f"{inv['cross_checks']['season_label_convention']['games_rows']:,} game rows have `season == right(season_label,4)`). "
      "So `2025` = 2024-25. **The Starter Pack contains no 2025-26 data** (the frozen lockbox season).")
    a("")
    a("## 2. Per-file inventory")
    a("")
    a("| File | Rows | Cols | Earliest date | Latest date | Teams | Players | Dup key groups | Null-key rows |")
    a("|---|---:|---:|---|---|---:|---:|---:|---:|")
    for d in inv["families"].values():
        for f in d["files"]:
            du = f["duplicates"]
            a(f"| {f['relative_path']} | {f['row_count']:,} | {f['column_count']} | {f['earliest_date'] or ''} | "
              f"{f['latest_date'] or ''} | {f['unique_team_count'] if f['unique_team_count'] is not None else ''} | "
              f"{f['unique_player_count'] if f['unique_player_count'] is not None else ''} | "
              f"{du.get('duplicate_key_groups', 'n/a')} | {du.get('rows_with_null_key', 'n/a')} |")
    a("")
    a("## 3. Schema changes between seasons")
    a("")
    for fam, d in inv["families"].items():
        sd = d["schema_differences"]
        if not sd:
            continue
        a(f"### {fam}")
        a(f"- Union of columns: {sd['union_column_count']}; distinct column *sets*: {sd['distinct_column_sets']}; "
          f"distinct column *orders*: {sd['distinct_column_orders']}.")
        missing = [p for p in sd["per_file"] if p["missing_vs_union"]]
        for p in missing:
            a(f"- `{p['relative_path']}` lacks: {', '.join(p['missing_vs_union'])}")
        if sd["columns_with_type_variation_across_files"]:
            tv = sd["columns_with_type_variation_across_files"]
            a(f"- {len(tv)} columns change inferred type across seasons (e.g. "
              + "; ".join(f"`{k}`: {', '.join(v)}" for k, v in list(tv.items())[:8]) + ").")
        a("")
    a("Consequence: every reader MUST select columns **by name** (DuckDB `union_by_name=true`), never by position.")
    a("")
    a("## 4. Material missingness (columns with null rate >= 5% in any file of the family)")
    a("")
    for fam, d in inv["families"].items():
        worst: dict[str, float] = {}
        for f in d["files"]:
            for c in f["columns"]:
                if c["null_rate"] is not None and c["null_rate"] >= 0.05:
                    worst[c["name"]] = max(worst.get(c["name"], 0), c["null_rate"])
        if worst:
            a(f"- **{fam}**: " + ", ".join(f"`{k}` (max {v:.1%})" for k, v in sorted(worst.items(), key=lambda kv: -kv[1])))
    a("")
    cc = inv["cross_checks"]
    a("## 5. Cross-file semantic findings")
    a("")
    tz = cc["games_start_date_timezone"]
    a(f"1. **Tip-time semantics (leakage-critical).** `games.csv.start_date` has no UTC offset; box-score files carry "
      f"`{', '.join(tz['offsets_observed_in_box_files'])}`. Measured relationship of the box stamp to games.csv: "
      f"{tz['relationship_counts']}. {tz['interpretation']}")
    a(f"2. **Recorded tip clock (UTC), 2025 top 10**: {cc['tip_time_utc_clock_top10_season_2025']}. The 00:00 peak is a "
      "7 pm EST tip, not a placeholder (confirmed against BDL). `startTimeTbd` counts in team_game_stats: "
      f"{cc['start_time_tbd_flag_counts_team_game_stats']}. Date-only start values by season: "
      f"{cc['games_with_date_only_start_by_season'] or 'none'}.")
    a("3. **Box-score coverage of games.csv by season** (games / with team box / with player box):")
    a("")
    a("| Season | Games | Team box | Player box |")
    a("|---:|---:|---:|---:|")
    for r in cc["box_score_coverage_of_games_by_season"]:
        if r["season"] >= 2003:
            a(f"| {r['season']} | {r['games']:,} | {r['with_team_box']:,} | {r['with_player_box']:,} |")
    a("")
    a(f"   Earlier seasons (1949–2002) exist in games.csv only, with no box scores. Box-score games absent from games.csv: "
      f"{cc['box_score_games_missing_from_games_csv']}.")
    a(f"4. **Team rows per game in team_game_stats**: {cc['team_game_rows_per_game']} (2 = both sides present).")
    ref = cc["referential"]
    a(f"5. **Referential integrity**: team IDs in games.csv absent from teams.csv: {ref['game_team_ids_not_in_teams_csv']}; "
      f"conference IDs absent from conferences.csv: {ref['game_conference_ids_not_in_conferences_csv']}; "
      f"games with a NULL home/away conference id: {ref['games_with_null_home_or_away_conference_id']:,} "
      "(non-D-I / unaffiliated opponents); player-game team IDs absent from teams.csv: "
      f"{ref['player_game_team_ids_not_in_teams_csv']}.")
    pi = cc["player_identity"]
    a(f"6. **Player IDs**: null `athleteId` rows: {pi['player_game_rows_null_athleteId']}; athleteIds with >1 distinct name: "
      f"{pi['athleteIds_with_multiple_names']:,}; on >1 team in one season: {pi['athleteIds_on_multiple_teams_same_season']:,}; "
      f"appearing in >1 season: {pi['athleteIds_appearing_in_multiple_seasons']:,}. Position values: {pi['position_values']}.")
    ai = cc["player_game_athleteId_semantics"]
    a(f"6b. **CRITICAL — `player_game_stats.athleteId` is not a player id.** In 2025 there are "
      f"{ai['season_2025_rows']:,} player-game rows and {ai['season_2025_distinct_athleteId']:,} distinct `athleteId` "
      "values: the field identifies the player-game *record* (a new value every game). By contrast "
      f"{ai['player_season_athleteIds_spanning_multiple_seasons']:,} `player_season_stats.athleteId` values span multiple "
      "seasons (persistent player ids). A naive id join between the two would create "
      f"{ai['player_game_ids_that_collide_numerically_with_player_season_ids']:,} coincidental false matches. "
      "Exact `(season, team_id, name)` resolution of distinct player-game records to the persistent id: "
      f"{ai['exact_season_team_name_resolution_of_distinct_player_game_records']}. "
      "This contradicts DATA_REQUIREMENTS §28 (\"persistent `athleteId`\") for this export; see CANONICAL_SCHEMA.md §Identity.")
    dg = cc["duplicate_groups_differing_columns"]
    a(f"6c. **Duplicate candidate keys are a conference fan-out, not duplicate games.** Inside every duplicate group the rows "
      f"are identical except: {dg}. The canonical layer collapses each group to one row and takes conference membership "
      "from the per-game ids in games.csv; staging keeps every raw row.")
    pp = cc["player_game_participation_values"]
    a(f"7. **Participation fields**: `minutes` NULL {pp['minutes_null']:,}, zero {pp['minutes_zero']:,}, non-integer "
      f"{pp['minutes_non_integer']:,}; `starter` values {pp['starter_values']}; `ejected` values {pp['ejected_values']}. "
      "The Starter Pack has **no roster table and no explicit DNP rows** — see the participation section of "
      "`CANONICAL_SCHEMA.md` for why that forces `UNKNOWN_PARTICIPATION` for absent players.")
    a("")
    (C.PROJECT_ROOT / "docs" / "STARTER_PACK_PHYSICAL_AUDIT.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    inv = run()
    for fam, d in inv["families"].items():
        print(f"{fam:30s} files={d['file_count']:3d} rows={d['coverage']['row_count']:>10,}")
