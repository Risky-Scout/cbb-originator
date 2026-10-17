"""Evidence for the lockbox-scoped trusted-window remediation (docs/lockbox/LOCKBOX_INCIDENT_2025_26_TRUSTED_WINDOW.md).

Compares the pre-remediation lockbox build with the remediated one, read-only, table by table, with the canonical
build's own order-independent content fingerprint (build_canonical.fingerprint: count, bit_xor and sum of row hashes,
processed_at excluded), split into rows of 2025-26 and all other rows. A row is 2025-26 when the table carries `season`
= 2026, or its canonical_game_id / source_canonical_game_id is a 2025-26 game of that build. Required: every non-2026
fingerprint unchanged (2005-2025 trusted content unchanged), and the trusted relations now populated for 2026.

    .venv/bin/python -m cbb_platform.lockbox_trusted_window_check <pre_db> <remediated_db> -> data/qa/lockbox_2025_26_trusted_window_remediation.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

from cbb_platform import config as C

OUT = C.QA / "lockbox_2025_26_trusted_window_remediation.json"
SEASON = 2026
VIEWS = ("flagship.games", "flagship.played_games", "metadata.trusted_game_map", "flagship.player_game_outcomes",
         "flagship.team_game_outcomes", "flagship.participation")


def _relations(con, db: str) -> list[str]:
    return [f"{s}.{t}" for s, t in con.execute(f"""SELECT schema_name, table_name FROM duckdb_tables()
        WHERE database_name = '{db}' AND table_name <> 'data_versions' ORDER BY 1, 2""").fetchall()]


def _split_fp(con, db: str, rel: str) -> dict:
    cols = [r[0] for r in con.execute(f"DESCRIBE {db}.{rel}").fetchall()]
    excl = " EXCLUDE (processed_at)" if "processed_at" in cols else ""
    games = f"(SELECT canonical_game_id FROM {db}.core.games WHERE season = {SEASON})"
    key = next((c for c in ("canonical_game_id", "source_canonical_game_id") if c in cols), None)
    is26 = f"season = {SEASON}" if "season" in cols else f"{key} IN {games}" if key else "FALSE"
    out = {"rule": is26.replace(games, "<2025-26 games>")}
    for part, cond in (("non_2026", f"NOT coalesce({is26}, FALSE)"), ("2026", f"coalesce({is26}, FALSE)")):
        n, x, s = con.execute(f"SELECT count(*), bit_xor(hash(t)), sum(hash(t)) FROM "
                              f"(SELECT *{excl} FROM {db}.{rel} WHERE {cond}) t").fetchone()
        out[part] = {"rows": n, "fingerprint": f"{n}:{x}:{s}"}
    return out


def check(pre: Path, new: Path) -> dict:
    con = duckdb.connect()
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    con.execute(f"ATTACH '{pre}' AS pre (READ_ONLY)")
    con.execute(f"ATTACH '{new}' AS new (READ_ONLY)")
    rels = sorted(set(_relations(con, "pre")) | set(_relations(con, "new"))) + list(VIEWS)
    tables, changed = {}, []
    for rel in rels:
        try:
            a, b = _split_fp(con, "pre", rel), _split_fp(con, "new", rel)
        except duckdb.CatalogException as e:
            tables[rel] = {"error": str(e)[:200]}
            changed.append(rel)
            continue
        same = a["non_2026"]["fingerprint"] == b["non_2026"]["fingerprint"]
        tables[rel] = {"rule": b["rule"], "non_2026_unchanged": same, "non_2026_rows": b["non_2026"]["rows"],
                       "rows_2026_pre": a["2026"]["rows"], "rows_2026_remediated": b["2026"]["rows"],
                       "fingerprint_non_2026": b["non_2026"]["fingerprint"]}
        if not same:
            tables[rel]["fingerprint_non_2026_pre"] = a["non_2026"]["fingerprint"]
            changed.append(rel)
    q = lambda db, sql: con.execute(sql.replace("{db}", db)).fetchone()[0]
    g26 = f"(SELECT canonical_game_id FROM {{db}}.core.games WHERE season = {SEASON})"
    counts = {db: {
        "core_games_2026": q(db, f"SELECT count(*) FROM {{db}}.core.games WHERE season = {SEASON}"),
        "core_games_2026_di_vs_di": q(db, f"SELECT count(*) FROM {{db}}.core.games WHERE season = {SEASON} AND game_universe = 'DI_VS_DI'"),
        "team_seasons_2026_di": q(db, f"SELECT count(*) FROM {{db}}.core.team_seasons WHERE season = {SEASON} AND is_division_one"),
        "game_status_resolution_2026": q(db, f"SELECT count(*) FROM {{db}}.metadata.game_status_resolution WHERE canonical_game_id IN {g26}"),
        "game_status_excluded_2026": q(db, f"SELECT count(*) FROM {{db}}.metadata.game_status_resolution WHERE canonical_game_id IN {g26} AND excluded_from_played_games"),
        "trusted_game_map_2026": q(db, f"SELECT count(*) FROM {{db}}.metadata.trusted_game_map WHERE source_canonical_game_id IN {g26}"),
        "game_alias_map_2026": q(db, f"SELECT count(*) FROM {{db}}.metadata.game_alias_map WHERE source_canonical_game_id IN {g26}"),
        "flagship_games_2026": q(db, f"SELECT count(*) FROM {{db}}.flagship.games WHERE season = {SEASON}"),
        "flagship_played_games_2026": q(db, f"SELECT count(*) FROM {{db}}.flagship.played_games WHERE season = {SEASON}"),
        "flagship_participation_2026": q(db, f"SELECT count(*) FROM {{db}}.flagship.participation WHERE canonical_game_id IN {g26}"),
        "flagship_seasons_after_2026": q(db, f"SELECT count(*) FROM {{db}}.flagship.games WHERE season > {SEASON}")}
        for db in ("pre", "new")}
    view_sql = {db: {v: con.execute(f"""SELECT sql FROM duckdb_views() WHERE database_name = '{db}'
                    AND schema_name || '.' || view_name = '{v}'""").fetchone()[0] for v in ("flagship.games", "metadata.trusted_game_map")}
                for db in ("pre", "new")}
    con.close()
    new_counts = counts["new"]
    out = {"pre_remediation_db": str(pre), "remediated_db": str(new), "season": SEASON, "method": __doc__.split("\n\n")[1],
           "relations_compared": len(rels), "relations_with_non_2026_changes": changed,
           "TRUSTED_DATA_2005_2025_CHANGED": bool(changed), "counts": counts,
           "trusted_view_window": {db: {v: ("BETWEEN 2005 AND 2026" in s, "BETWEEN 2005 AND 2025" in s) for v, s in d.items()}
                                   for db, d in view_sql.items()},
           "tables": tables}
    out["pass"] = (not changed and new_counts["trusted_game_map_2026"] > 0 and new_counts["flagship_played_games_2026"] > 0
                   and new_counts["game_status_resolution_2026"] == new_counts["core_games_2026"]
                   and new_counts["flagship_seasons_after_2026"] == 0)
    return out


if __name__ == "__main__":
    res = check(Path(sys.argv[1]), Path(sys.argv[2]))
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: res[k] for k in ("relations_compared", "relations_with_non_2026_changes", "TRUSTED_DATA_2005_2025_CHANGED",
                                          "counts", "pass")}, indent=1, default=str))
