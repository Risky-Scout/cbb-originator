"""Step 11 — measured coverage audit + unresolved-identity report.

    .venv/bin/python -m cbb_platform.coverage

Hierarchy (directive v2 §28):
    data/qa/COVERAGE_AUDIT.json            AUTHORITATIVE machine contract (measured from the built DB
                                           and the physical Starter Pack inventory)
    data/qa/STAGE_1_COVERAGE_SUMMARY.json  derived ONLY from COVERAGE_AUDIT.json
    docs/STAGE_1_COVERAGE_SUMMARY.md       rendered ONLY from STAGE_1_COVERAGE_SUMMARY.json
    data/qa/unresolved_identities.csv      every UNRESOLVED / manual-review identity map row

Nothing is estimated from documentation. Families never fetched (PBP, onFloor, substitutions,
lineups) are reported as NOT_MEASURED with the reason, never as zero coverage of a real season.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import duckdb

from cbb_platform import config as C


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(con, sql):
    return con.execute(sql).fetchone()[0]


def build_audit(con: duckdb.DuckDBPyConnection) -> dict:
    inv = json.loads((C.QA / "starter_pack_inventory.json").read_text())
    build_id = _one(con, "SELECT max(build_id) FROM metadata.data_versions")

    starter = {}
    for fam, d in inv["families"].items():
        cov = d["coverage"]
        sd = d["schema_differences"]
        starter[fam] = {
            **{k: cov[k] for k in ("seasonal", "row_count") if k in cov},
            **({k: cov[k] for k in ("first_season", "last_season", "seasons_present", "missing_seasons_within_range",
                                     "rows_by_season")} if cov.get("seasonal") else {}),
            "duplicate_candidate_key_groups": sum(f["duplicates"].get("duplicate_key_groups") or 0 for f in d["files"]),
            "schema": None if not sd else {
                "distinct_column_sets": sd["distinct_column_sets"],
                "distinct_column_orders": sd["distinct_column_orders"],
                "columns_with_type_variation": len(sd["columns_with_type_variation_across_files"]),
            },
        }

    by_season = _rows(con, """
        WITH g AS (SELECT season, count(*) games, count(*) FILTER (WHERE has_team_box_score) team_box,
                          count(*) FILTER (WHERE has_player_box_score) player_box,
                          count(*) FILTER (WHERE game_universe = 'DI_VS_DI') di_vs_di,
                          count(*) FILTER (WHERE game_universe = 'DI_VS_NON_DI') di_vs_non_di,
                          count(*) FILTER (WHERE tip_time_quality = 'EXACT') exact_tip
                   FROM core.games GROUP BY 1),
             pg AS (SELECT season, count(*) player_game_rows,
                          count(*) FILTER (WHERE identity_status = 'RESOLVED') resolved_rows,
                          count(DISTINCT canonical_player_id) unique_resolved_players,
                          count(accounting_issues) accounting_flagged_rows FROM fact.player_games GROUP BY 1),
             tg AS (SELECT season, count(*) team_game_rows, count(accounting_issues) team_accounting_flagged,
                          count(*) FILTER (WHERE player_box_reconciles) team_games_player_box_reconciles,
                          count(player_box_reconciles) team_games_with_player_box FROM fact.team_games GROUP BY 1),
             pa AS (SELECT season, count(*) FILTER (WHERE participation_state = 'PLAYED') played,
                          count(*) FILTER (WHERE participation_state = 'CONFIRMED_DNP') confirmed_dnp,
                          count(*) FILTER (WHERE participation_state = 'UNKNOWN_PARTICIPATION') unknown_participation
                   FROM fact.participation GROUP BY 1),
             ps AS (SELECT season, count(*) player_season_rows FROM fact.player_season_stats GROUP BY 1),
             pss AS (SELECT season, count(*) player_shooting_rows FROM fact.player_season_shooting GROUP BY 1),
             ts AS (SELECT season, count(*) team_season_rows, count(accounting_issues) team_season_accounting_flagged
                    FROM fact.team_season_stats GROUP BY 1),
             tss AS (SELECT season, count(*) team_shooting_rows FROM fact.team_season_shooting GROUP BY 1)
        SELECT * FROM g LEFT JOIN pg USING (season) LEFT JOIN tg USING (season) LEFT JOIN pa USING (season)
             LEFT JOIN ps USING (season) LEFT JOIN pss USING (season) LEFT JOIN ts USING (season)
             LEFT JOIN tss USING (season)
        WHERE g.season >= 2003 ORDER BY season""")

    def ident(provider):
        return _rows(con, f"""SELECT match_method, count(*) AS n, count(*) FILTER (WHERE needs_manual_review) AS manual_review
                              FROM metadata.player_id_map WHERE provider = '{provider}' GROUP BY 1 ORDER BY 1""")

    pg_total, pg_resolved = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE identity_status = 'RESOLVED') FROM fact.player_games").fetchone()
    di_unresolved = _one(con, """SELECT count(*) FROM fact.player_games p JOIN core.team_seasons ts
        ON ts.canonical_team_id = p.canonical_team_id AND ts.season = p.season
        WHERE ts.is_division_one AND p.identity_status <> 'RESOLVED'""")
    di_total = _one(con, """SELECT count(*) FROM fact.player_games p JOIN core.team_seasons ts
        ON ts.canonical_team_id = p.canonical_team_id AND ts.season = p.season WHERE ts.is_division_one""")

    def not_measured(table, reason):
        rows = _one(con, f"SELECT count(*) FROM {table}")
        return {"status": "NOT_MEASURED" if rows == 0 else "MEASURED", "rows": rows, "reason": reason}

    bdl_games, bdl_mapped = con.execute(
        "SELECT count(*), count(canonical_game_id) FROM benchmark.bdl_games").fetchone()
    tip_n, tip_ok = con.execute("""SELECT count(*), count(*) FILTER (WHERE g.scheduled_tip_utc = b.game_date_utc)
        FROM benchmark.bdl_games b JOIN core.games g USING (canonical_game_id) WHERE g.tip_time_quality = 'EXACT'""").fetchone()

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "canonical_build_id": build_id,
        "ingestion_version": C.INGESTION_VERSION,
        "schema_version": C.SCHEMA_VERSION,
        "season_convention": "CBBD season = year the season ENDS (2025 = 2024-25). BDL season = start year (+1 to join).",
        "lockbox": {"season": "2025-26 (CBBD 2026)",
                    "rows_present_in_canonical_facts": _one(con, "SELECT count(*) FROM core.games WHERE season >= 2026"),
                    "note": "Starter Pack ends at 2024-25; no lockbox data was fetched or ingested."},
        "starter_pack_physical": starter,
        "canonical_by_season": by_season,
        "identity": {
            "player_games_total": pg_total,
            "player_games_resolved": pg_resolved,
            "player_games_resolved_rate": round(pg_resolved / pg_total, 6),
            "player_games_on_d1_team_seasons": di_total,
            "player_games_unresolved_on_d1_team_seasons": di_unresolved,
            "player_identity_status_counts": dict(con.execute(
                "SELECT identity_status, count(*) FROM fact.player_games GROUP BY 1 ORDER BY 1").fetchall()),
            "cbbd_anchor_players": _one(con, "SELECT count(*) FROM core.players"),
            "starter_pack_team_season_name_keys": ident("CBBD_STARTER_PACK_TEAM_SEASON_NAME"),
            "bdl_players": ident("BDL"),
            "bdl_teams": _rows(con, """SELECT match_method, count(*) n, count(*) FILTER (WHERE needs_manual_review) manual_review
                                       FROM metadata.team_id_map WHERE provider = 'BDL' GROUP BY 1 ORDER BY 1"""),
            "bdl_games": {"fetched": bdl_games, "mapped_to_canonical": bdl_mapped},
        },
        "event_level_families": {
            "pbp": not_measured("fact.plays", "Stage 2 measured backfill; per-season detail in data/qa/cbbd_event_coverage_by_season.csv and stage2_event_coverage below"),
            "on_floor": not_measured("fact.play_on_floor", "Stage 2 measured backfill; per-season detail in data/qa/cbbd_event_coverage_by_season.csv and stage2_event_coverage below; onFloor arrives inside CBBD PBP"),
            "shots": not_measured("fact.shots", "Stage 2 measured backfill; per-season detail in data/qa/cbbd_event_coverage_by_season.csv and stage2_event_coverage below; shotInfo arrives inside CBBD PBP"),
            "substitutions": not_measured("fact.substitutions", "Stage 2 measured backfill; per-season detail in data/qa/cbbd_event_coverage_by_season.csv and stage2_event_coverage below"),
            "lineups": not_measured("fact.lineup_stats", "Stage 2 measured backfill; per-season detail in data/qa/cbbd_event_coverage_by_season.csv and stage2_event_coverage below"),
            "lineup_stints": not_measured("fact.lineup_stints", "derived in Stage 4; never manufactured"),
            "rosters": not_measured("core.rosters", "Stage 2 measured backfill; per-season detail in data/qa/cbbd_event_coverage_by_season.csv and stage2_event_coverage below; CBBD /teams/roster, one call per season"),
            "recruits": not_measured("core.recruits", "CBBD /recruiting/players, class years 2004-2024"),
            "transfers": not_measured("core.transfers", "CBBD /recruiting/portal, years 2004-2024"),
            "venue_directory": not_measured("core.venue_directory", "CBBD /venues"),
        },
        # Authoritative Stage 2 measurement (cbb_platform/event_audit.py), folded in verbatim.
        "stage2_event_coverage": (json.loads((C.QA / "cbbd_event_coverage_by_season.json").read_text())
                                  if (C.QA / "cbbd_event_coverage_by_season.json").exists() else "NOT_RUN"),
        "bdl_reconciliation": {
            "by_entity": _rows(con, """SELECT entity_type, count(*) comparisons, count(*) FILTER (WHERE values_match) matches
                                       FROM qa.reconciliation GROUP BY 1 ORDER BY 1"""),
            "mismatches": _rows(con, """SELECT entity_type, canonical_game_id, stat, cbbd_value, other_value
                                        FROM qa.reconciliation WHERE NOT values_match ORDER BY 1, 2, 3"""),
            "seasons_compared": [r[0] for r in con.execute(
                "SELECT DISTINCT cbbd_season FROM benchmark.bdl_games WHERE canonical_game_id IS NOT NULL ORDER BY 1").fetchall()],
        },
        "tip_time_external_check": {
            "description": "canonical scheduled_tip_utc (games.csv read as UTC) vs BDL independent UTC tip",
            "games_compared": tip_n, "exact_matches": tip_ok},
        "market_benchmark": {
            "game_odds_rows": _one(con, "SELECT count(*) FROM benchmark.game_odds"),
            "opening_odds_rows": _one(con, "SELECT count(*) FROM benchmark.opening_odds"),
            "note": "BDL /odds and /odds/opening returned HTTP 200 with zero rows for the 2024-25 smoke games; "
                    "2025-26 (lockbox) deliberately not queried; first prospective data expected in 2026-27.",
        },
        "availability": {
            "real_snapshot_events": _one(con, "SELECT count(*) FROM availability.events WHERE NOT is_synthetic_fixture"),
            "note": "No Sharp Stats capture supplied; adapter proven on labeled synthetic fixtures under tests/ only.",
        },
        "accounting_flags": {t: dict(con.execute(
            f"SELECT coalesce(accounting_issues, 'OK'), count(*) FROM {t} GROUP BY 1 ORDER BY 2 DESC").fetchall())
            for t in ("fact.player_games", "fact.team_games", "fact.player_season_stats", "fact.team_season_stats")},
        "qa_exceptions": dict(con.execute(
            "SELECT exception_type || ':' || severity, count(*) FROM qa.exceptions GROUP BY 1 ORDER BY 1").fetchall()),
    }


def build_summary(audit: dict) -> dict:
    """Derived strictly from COVERAGE_AUDIT.json content."""
    sp = audit["starter_pack_physical"]
    fam = lambda f: {k: sp[f].get(k) for k in ("first_season", "last_season", "missing_seasons_within_range", "row_count")}
    seasons = audit["canonical_by_season"]
    box = [s for s in seasons if s["player_box"]]
    return {
        "derived_from": "data/qa/COVERAGE_AUDIT.json",
        "canonical_build_id": audit["canonical_build_id"],
        "player_game_seasons": fam("player_game_stats"),
        "player_season_seasons": fam("player_season_stats"),
        "player_shooting_seasons": fam("player_season_shooting_stats"),
        "team_game_seasons": fam("team_game_stats"),
        "team_season_seasons": fam("team_season_stats"),
        "team_shooting_seasons": fam("team_season_shooting_stats"),
        "games_file_seasons": fam("games"),
        "seasons_in_games_without_box_scores": [s["season"] for s in seasons if not s["player_box"]],
        "player_id_coverage": {
            "resolved_rate_all_player_games": audit["identity"]["player_games_resolved_rate"],
            "d1_player_games": audit["identity"]["player_games_on_d1_team_seasons"],
            "d1_player_games_unresolved": audit["identity"]["player_games_unresolved_on_d1_team_seasons"],
        },
        "game_id_coverage": {
            "games_with_player_box_by_season": {s["season"]: f'{s["player_box"]}/{s["games"]}' for s in box},
            "bdl_games_mapped": audit["identity"]["bdl_games"],
        },
        "schema_consistency": {f: v["schema"] for f, v in sp.items() if v.get("schema")},
        "event_level_families": {k: v["status"] for k, v in audit["event_level_families"].items()},
        "bdl_reconciliation": audit["bdl_reconciliation"]["by_entity"],
        "tip_time_external_check": audit["tip_time_external_check"],
        "lockbox": audit["lockbox"],
    }


def render_markdown(summary: dict) -> str:
    L = ["# Stage 1 Coverage Summary", "",
         f"Rendered from `data/qa/STAGE_1_COVERAGE_SUMMARY.json`, which is derived from the authoritative "
         f"`data/qa/COVERAGE_AUDIT.json` (canonical build `{summary['canonical_build_id']}`). "
         "Every number is measured from physically present data. Nothing here is estimated from documentation.", "",
         "## Season coverage by family (CBBD season = year the season ends)", "",
         "| Family | First | Last | Missing in range | Rows |", "|---|---:|---:|---|---:|"]
    for key, label in [("games_file_seasons", "games (schedule/results)"), ("player_game_seasons", "player_game_stats"),
                       ("player_season_seasons", "player_season_stats"), ("player_shooting_seasons", "player_season_shooting_stats"),
                       ("team_game_seasons", "team_game_stats"), ("team_season_seasons", "team_season_stats"),
                       ("team_shooting_seasons", "team_season_shooting_stats")]:
        v = summary[key]
        L.append(f"| {label} | {v['first_season']} | {v['last_season']} | {v['missing_seasons_within_range'] or 'none'} | {v['row_count']:,} |")
    L += ["", f"Seasons present in games.csv (from 2003) with no box scores at all: {summary['seasons_in_games_without_box_scores']}.",
          "games.csv also covers 1949–2002 with schedule and results only.", "",
          "## Game ID coverage (games with a player box score / games in games.csv)", "",
          "| Season | Player box / games |", "|---:|---|"]
    L += [f"| {s} | {v} |" for s, v in summary["game_id_coverage"]["games_with_player_box_by_season"].items()]
    pid = summary["player_id_coverage"]
    L += ["", "## Player ID coverage", "",
          f"- All player-game rows resolved to a persistent canonical player: {pid['resolved_rate_all_player_games']:.4%}.",
          f"- D-I team-season player-game rows: {pid['d1_player_games']:,}; unresolved: {pid['d1_player_games_unresolved']:,}.",
          "- Resolution method: exact `(season, team, name)` match to the persistent id in player_season_stats. "
          "`player_game_stats.athleteId` is a per-record id; see the physical audit.",
          f"- BDL smoke games mapped to canonical games: {summary['game_id_coverage']['bdl_games_mapped']}.", "",
          "## Schema consistency (Starter Pack, across season files)", "",
          "| Family | Distinct column sets | Distinct column orders | Columns changing type |", "|---|---:|---:|---:|"]
    L += [f"| {f} | {v['distinct_column_sets']} | {v['distinct_column_orders']} | {v['columns_with_type_variation']} |"
          for f, v in summary["schema_consistency"].items()]
    L += ["", "## Event-level families (PBP / on-floor / substitutions / lineups)", ""]
    L += [f"- {k}: **{v}**" for k, v in summary["event_level_families"].items()]
    L += ["", "No historical PBP, on-floor, substitution, or lineup coverage is claimed. MEASURED here means only that rows "
          "from the bounded CBBD live SMOKE SAMPLE exist (no backfill). Per-game smoke metrics are in "
          "docs/CBBD_LIVE_SMOKE_RESULTS.md and must not be generalized to whole seasons.", "", "## Independent checks against BDL (2023-24 and 2024-25 smoke samples)", ""]
    L += [f"- {r['entity_type']}: {r['matches']:,} / {r['comparisons']:,} comparisons agree" for r in summary["bdl_reconciliation"]]
    t = summary["tip_time_external_check"]
    L += [f"- Tip times: {t['exact_matches']} / {t['games_compared']} canonical tip times equal BDL's independent UTC tip time.",
          "", f"## Lockbox", "", f"- {summary['lockbox']['season']}: {summary['lockbox']['rows_present_in_canonical_facts']} rows present. "
          f"{summary['lockbox']['note']}", ""]
    return "\n".join(L)


def write_unresolved_identities(con) -> int:
    rows = con.execute("""
        SELECT 'player' AS entity, provider, provider_player_key AS provider_key, season, provider_team_key AS context_team,
               match_method, match_confidence, needs_manual_review, source_context
        FROM metadata.player_id_map WHERE match_method = 'UNRESOLVED' OR needs_manual_review
        UNION ALL
        SELECT 'team', provider, provider_team_key, season, NULL, match_method, match_confidence, needs_manual_review, source_context
        FROM metadata.team_id_map WHERE match_method = 'UNRESOLVED' OR needs_manual_review
        UNION ALL
        SELECT 'game', provider, provider_game_key, NULL, NULL, match_method, match_confidence, needs_manual_review, source_context
        FROM metadata.game_id_map WHERE match_method = 'UNRESOLVED' OR needs_manual_review
        ORDER BY ALL""").fetchall()
    with open(C.QA / "unresolved_identities.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity", "provider", "provider_key", "season", "context_team", "match_method",
                    "match_confidence", "needs_manual_review", "source_context"])
        w.writerows(rows)
    return len(rows)


def run() -> dict:
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    audit = build_audit(con)
    (C.QA / "COVERAGE_AUDIT.json").write_text(json.dumps(audit, indent=1, default=str))
    # Derivatives are built by re-reading the authoritative file, never from live queries.
    summary = build_summary(json.loads((C.QA / "COVERAGE_AUDIT.json").read_text()))
    (C.QA / "STAGE_1_COVERAGE_SUMMARY.json").write_text(json.dumps(summary, indent=1, default=str))
    (C.PROJECT_ROOT / "docs" / "STAGE_1_COVERAGE_SUMMARY.md").write_text(
        render_markdown(json.loads((C.QA / "STAGE_1_COVERAGE_SUMMARY.json").read_text())))
    n = write_unresolved_identities(con)
    con.close()
    return {"unresolved_identity_rows": n, "build": audit["canonical_build_id"]}


if __name__ == "__main__":
    print(run())
