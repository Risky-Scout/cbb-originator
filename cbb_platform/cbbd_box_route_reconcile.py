"""Reconciliation of the CBBD box route against the Starter Pack (docs/lockbox/CBBD_BOX_ROUTE_RECONCILIATION_SPEC.md,
frozen before evaluation). Development seasons only.

    staging(...)    S0: route-reshaped rows vs the Starter Pack staging rows, field by field, on every column the
                    canonical build reads (natural keys; the per-record Starter Pack athleteId is not compared, D1).
                    Runs on any stored CBBD box payloads (the Stage 1 smoke payloads, or full route seasons).
Every difference is listed with both values.
    full_build(out)          route canonical build + unchanged Stage 3 build of 2024 and 2025 into a scratch directory.
    ablation_build(out, ab)  DIAGNOSTIC counterfactual: the route build minus exactly the classified team-box revisions.
    full(a, b, ab)           E1-E8, every difference classified to one cause (route vs ablation vs reference), guard
                             refusal, determinism (two independent builds a, b), verdict per the 2026-10-15 directive.

    .venv/bin/python -m cbb_platform.cbbd_box_route_reconcile smoke     -> data/qa/cbbd_box_route_smoke_reconciliation.json
    .venv/bin/python -m cbb_platform.cbbd_box_route_reconcile full-build <scratch>
    .venv/bin/python -m cbb_platform.cbbd_box_route_reconcile ablation-build <scratch> <ablation>
    .venv/bin/python -m cbb_platform.cbbd_box_route_reconcile full <scratch> <scratch_b> <ablation>
                                                                    -> data/qa/cbbd_box_route_reconciliation.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import duckdb

from cbb_platform import cbbd_box_route as B, config as C, lockbox, raw_store

SPEC = C.PROJECT_ROOT / "docs" / "lockbox" / "CBBD_BOX_ROUTE_RECONCILIATION_SPEC.md"
SMOKE_OUT = C.QA / "cbbd_box_route_smoke_reconciliation.json"
MAX_REVISION_SHARE = 0.001

# columns the canonical build reads from each staging family (build_canonical.py), compared on natural keys
COMPARE = {
    "games": (("id",), ("season", "season_label", "season_type", "game_type", "tournament", "start_date", "neutral_site",
                        "conference_game", "home_team_id", "away_team_id", "home_conference_id", "away_conference_id",
                        "home_seed", "away_seed", "home_points", "away_points", "home_period_points", "away_period_points",
                        "venue_id")),
    "team_game_stats": (("gameId", "teamId"), ("opponentId", "season", "isHome", "neutralSite", "gameMinutes", "startTimeTbd",
                        "pace", "teamStats_points_total", "teamStats_fieldGoals_made", "teamStats_fieldGoals_attempted",
                        "teamStats_twoPointFieldGoals_made", "teamStats_twoPointFieldGoals_attempted",
                        "teamStats_threePointFieldGoals_made", "teamStats_threePointFieldGoals_attempted",
                        "teamStats_freeThrows_made", "teamStats_freeThrows_attempted", "teamStats_rebounds_offensive",
                        "teamStats_rebounds_defensive", "teamStats_rebounds_total", "teamStats_assists", "teamStats_steals",
                        "teamStats_blocks", "teamStats_turnovers_total", "teamStats_turnovers_teamTotal", "teamStats_fouls_total",
                        "teamStats_fouls_technical", "teamStats_fouls_flagrant", "teamStats_possessions",
                        "teamStats_points_inPaint", "teamStats_points_fastBreak", "teamStats_points_offTurnovers",
                        "teamStats_points_largestLead", "teamStats_points_byPeriod_0", "teamStats_points_byPeriod_1")),
    "player_game_stats": (("game_id", "team_id", "name"), ("opponent_id", "season", "is_home", "position", "starter",
                          "minutes", "points", "fieldGoals_made", "fieldGoals_attempted", "twoPointFieldGoals_made",
                          "twoPointFieldGoals_attempted", "threePointFieldGoals_made", "threePointFieldGoals_attempted",
                          "freeThrows_made", "freeThrows_attempted", "rebounds_offensive", "rebounds_defensive",
                          "rebounds_total", "assists", "steals", "blocks", "turnovers", "fouls", "ejected", "usage")),
    "player_season_stats": (("season", "teamId", "athleteId"), ("name", "position", "games", "starts", "minutes", "points",
                            "fieldGoals_made", "fieldGoals_attempted", "threePointFieldGoals_made", "freeThrows_made",
                            "rebounds_total", "assists", "steals", "blocks", "turnovers", "fouls", "usage")),
    "team_season_stats": (("season", "teamId"), ("games", "wins", "losses", "totalMinutes", "teamStats_points_total",
                          "teamStats_possessions", "opponentStats_points_total")),
}


def _norm(v):
    """Starter Pack text ('37.0', 'True') and route text ('37', 'True') compare by value, not spelling."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return round(f, 6)
    except (TypeError, ValueError):
        return str(v)


def _sp_rows(family: str, keys: set, key_cols) -> dict:
    if not keys:
        return {}
    pattern = C.STARTER_FAMILIES[family][0]
    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE k AS SELECT * FROM (VALUES " +
                ", ".join("(" + ", ".join(f"'{x}'" for x in k) + ")" for k in sorted(keys)) + f") t({', '.join(key_cols)})")
    cur = con.execute(f"""SELECT s.* FROM read_csv('{C.STARTER_PACK_DATA / pattern}', all_varchar=true, union_by_name=true) s
                           JOIN k USING ({', '.join(key_cols)})""")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]          # no pandas: a NULL string stays None, never NaN
    con.close()
    out = {}
    for r in rows:
        out.setdefault(tuple(r[c] for c in key_cols), []).append(r)
    return out


def _classify_names(family: str, route: dict, only_route: list, cols) -> list[dict]:
    """A route-only player-game key is a PROVIDER_NAME_REVISION when (a) among the Starter Pack rows of the same team-game
    that no route key matched, exactly one has every compared stat equal to the route row, and (b) the route name is
    the name in the raw CBBD JSON (read independently of the route code)."""
    out = []
    by_tg: dict = {}
    for k in route:
        by_tg.setdefault(k[:2], set()).add(k[2])
    stat_cols = [c for c in cols if c not in ("position",)]
    for ks in only_route:
        k = tuple(eval(ks))                                   # keys were stringified tuples of str
        sp_tg = _sp_rows(family, {k[:2]}, ("game_id", "team_id"))
        orphans = [r for r in sp_tg.get(k[:2], []) if r["name"] not in by_tg[k[:2]]]
        same = [r for r in orphans if all(_norm(r.get(c)) == _norm(route[k][0].get(c)) for c in stat_cols)]
        raw = _raw_names(route[k][0]["filename"], k[0], k[1])
        cls = "PROVIDER_NAME_REVISION" if len(same) == 1 and k[2] in raw else "UNEXPLAINED"
        out.append({"key": list(k), "class": cls, "starter_pack_name": same[0]["name"] if same else None,
                    "route_name_in_raw_cbbd_json": k[2] in raw})
    return out


def _raw_names(lineage: str, game_id: str, team_id: str) -> set:
    """Player names in the raw CBBD JSON payload for (game, team): read with json.loads, not the route reshape."""
    body = json.loads(raw_store.read_payload(C.RAW / lineage.removeprefix("cbbd:")))
    return {p.get("name") for g in body if str(g.get("gameId")) == game_id and str(g.get("teamId")) == team_id
            for p in g.get("players") or []}


def staging(rows_by_family: dict) -> dict:
    """S0 on whatever route rows are supplied (dict family -> Starter Pack-shaped rows)."""
    rep = {}
    for family, (key_cols, cols) in COMPARE.items():
        rows = rows_by_family.get(family) or []
        route = {}
        for r in rows:
            route.setdefault(tuple(r.get(c) for c in key_cols), []).append(r)
        dupes = sorted(str(k) for k, v in route.items() if len(v) > 1)
        sp = _sp_rows(family, set(route), key_cols)
        # the Starter Pack fans some rows out by conference (build_canonical collapses it); compare against the collapse
        diffs, only_route = [], sorted(str(k) for k in route if k not in sp)
        for k, rr in route.items():
            if k not in sp:
                continue
            sp_vals = {c: {_norm(x.get(c)) for x in sp[k]} for c in cols}
            for c in cols:
                v = _norm(rr[0].get(c))
                if v not in sp_vals[c]:
                    diffs.append({"key": list(k), "column": c, "route": v, "starter_pack": sorted(map(str, sp_vals[c]))})
        classified = _classify_names(family, route, only_route, cols) if family == "player_game_stats" else []
        rep[family] = {"route_rows": len(rows), "keys": len(route), "matched_keys": sum(k in sp for k in route),
                       "route_only_classified": classified,
                       "route_only_keys": only_route, "duplicate_route_keys": dupes, "differences": diffs,
                       "columns_compared": list(cols)}
    rep["unexplained"] = sum(len(v["route_only_keys"]) - sum(c["class"] == "PROVIDER_NAME_REVISION" for c in v.get("route_only_classified", []))
                             + len(v["differences"]) + len(v["duplicate_route_keys"]) for v in rep.values())
    rep["pass_exact"] = all(not v["differences"] and not v["route_only_keys"] and not v["duplicate_route_keys"]
                            for v in rep.values() if isinstance(v, dict))
    return rep


def smoke() -> dict:
    """S0 on the stored Stage 1 CBBD smoke payloads (development seasons 2024, 2025; a handful of games)."""
    by: dict[str, list] = {f: [] for f in COMPARE}
    payloads, skipped = [], []
    for ep, fam in B.ENDPOINT_FAMILY.items():
        if fam not in COMPARE:
            continue
        for path, meta in sorted(raw_store.records("cbbd"), key=lambda r: str(r[0])):
            if meta["endpoint"] != ep:
                continue
            if fam.endswith("season_stats") and ("startDateRange" in meta["params"] or "endDateRange" in meta["params"]):
                skipped.append({"endpoint": ep, "params": meta["params"], "reason": "partial-season (date-windowed) "
                                "aggregate: not comparable to a full-season Starter Pack row"})
                continue
            for s in (meta["params"].get("season"),):
                lockbox.check_season(s)
            body = json.loads(raw_store.read_payload(path))
            lockbox.check_response("cbbd", body)
            by[fam] += B.reshape(fam, body, f"cbbd:{path.relative_to(C.RAW)}")
            payloads.append({"endpoint": ep, "params": meta["params"], "sha256": meta["sha256"]})
    for fam in by:
        by[fam], _ = B.dedupe(fam, by[fam]) if fam in B.KEYS else (by[fam], 0)
    rep = staging(by)
    return {"spec": str(SPEC.relative_to(C.PROJECT_ROOT)), "spec_sha256": hashlib.sha256(SPEC.read_bytes()).hexdigest(),
            "scope": "SMOKE SAMPLE: the Stage 1 CBBD smoke payloads only (development seasons; a few teams and games). "
                     "Not a full-season validation", "payloads": payloads, "seasons": sorted({p["params"].get("season") for p in payloads}),
            "skipped_payloads": skipped, "lockbox_touched": False, "staging_reconciliation": rep}


# ============================================================================ full development seasons (spec §1-§6)
ROUTE_SEASONS = (2024, 2025)
FULL_OUT = C.QA / "cbbd_box_route_reconciliation.json"     # the ONE authoritative full report (lockbox_readiness.ROUTE_FULL)
LINEAGE = ("source_system", "source_file", "source_record_key", "ingestion_version", "received_at", "processed_at")
COUNTS = ("points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta", "orb", "drb", "reb", "ast",
          "stl", "blk", "tov", "pf")
CANON = {   # spec §3; None = every non-key, non-lineage column (D1/D2 excluded)
    "E1": ("core.games", ("canonical_game_id",), ("season", "season_type", "scheduled_tip_utc", "tip_time_quality",
           "game_date_et", "neutral_site", "home_team_id", "away_team_id", "home_points", "away_points",
           "home_period_points", "away_period_points", "overtime_count", "game_universe", "has_team_box_score",
           "has_player_box_score"), ""),
    "E2": ("fact.team_games", ("canonical_game_id", "canonical_team_id"), None, ""),
    "E3": ("fact.player_games", ("canonical_game_id", "canonical_team_id", "source_player_name"),
           ("canonical_player_id", "identity_status", "starter", "minutes", *COUNTS, "usage_source", "accounting_issues"), ""),
    "E4": ("fact.participation", ("canonical_game_id", "canonical_team_id", "canonical_player_id"),
           ("participation_state", "participation_evidence", "candidate_source", "starter", "minutes"),
           "canonical_player_id IS NOT NULL"),
    "E5": ("flagship.played_games", ("canonical_game_id",), (), ""),
}
STAGE3 = {"E6.labels": ("labels", "lab.player_game_labels"), "E6.features": ("features", "feat.player_features"),
          "E6.examples": ("features", "ex.player_game_examples")}
E7_SEASONS = (2014, 2023)
LIST_CAP = 2000
# Architect ruling 2026-10-14 (E7 floating-point identity): ids, keys, row counts, integers, strings, booleans and null
# masks compare EXACTLY; a derived floating-point (DOUBLE / FLOAT) Stage 3 value is equal when
# |a - b| <= FLOAT_ABS_TOL or |a - b| <= FLOAT_REL_TOL * max(|a|, |b|) (NaN == NaN). Fixed a priori, not from results.
# Canonical (E1-E5) values are source data, not derived, and stay exact.
FLOAT_ABS_TOL = 1e-12
FLOAT_REL_TOL = 1e-12
FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL")


def _types(con, db: str, table: str) -> dict:
    s, t = table.split(".")
    return dict(con.execute(f"SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = '{db}' "
                            f"AND schema_name = '{s}' AND table_name = '{t}' ORDER BY column_index").fetchall())


def float_equal_sql(a: str, b: str) -> str:
    """Null-mask exact; NaN == NaN; otherwise the absolute-or-relative 1e-12 rule."""
    return (f"(({a}) IS NULL AND ({b}) IS NULL) OR (({a}) IS NOT NULL AND ({b}) IS NOT NULL AND (({a}) = ({b}) "
            f"OR (isnan({a}) AND isnan({b})) OR (NOT isnan({a}) AND NOT isnan({b}) AND ("
            f"abs(({a}) - ({b})) <= {FLOAT_ABS_TOL} OR abs(({a}) - ({b})) <= {FLOAT_REL_TOL} * greatest(abs({a}), abs({b}))))))")


def _cols(con, db: str, table: str) -> list[str]:
    s, t = table.split(".")
    return [r[0] for r in con.execute(f"SELECT column_name FROM duckdb_columns() WHERE database_name = '{db}' "
                                      f"AND schema_name = '{s}' AND table_name = '{t}' ORDER BY column_index").fetchall()]


def compare_table(con, table: str, keys, cols, where: str = "", seasons=ROUTE_SEASONS, exclude=LINEAGE,
                  float_tol: bool = False) -> dict:
    """ref.<table> vs rt.<table> on the route seasons: key sets, duplicate keys, and every differing (key, column).
    float_tol: DOUBLE/FLOAT columns use the ruling's 1e-12 rule (derived Stage 3 values only); exact otherwise."""
    have = _cols(con, "ref", table)
    ftypes = _types(con, "ref", table) if float_tol else {}
    if cols is None:
        cols = [c for c in have if c not in keys and c not in exclude]
    cols = [c for c in cols if c in have]
    sl = ", ".join(map(str, seasons))
    filt = f"season IN ({sl})" + (f" AND {where}" if where else "")
    k = ", ".join(keys)
    out = {"table": table, "keys": list(keys), "columns_compared": list(cols)}
    for side in ("ref", "rt"):
        out[f"{side}_rows"] = con.execute(f"SELECT count(*) FROM {side}.{table} WHERE {filt}").fetchone()[0]
        out[f"{side}_duplicate_keys"] = [list(r) for r in con.execute(
            f"SELECT {k} FROM {side}.{table} WHERE {filt} GROUP BY ALL HAVING count(*) > 1 ORDER BY ALL").fetchall()]
    # a key duplicated on either side (Starter Pack double listings, D1) is compared as a multiset of compared values
    con.execute(f"""CREATE OR REPLACE TEMP TABLE dk AS SELECT DISTINCT {k} FROM (
        SELECT {k} FROM ref.{table} WHERE {filt} GROUP BY ALL HAVING count(*) > 1 UNION ALL
        SELECT {k} FROM rt.{table} WHERE {filt} GROUP BY ALL HAVING count(*) > 1)""")
    tup = f"list_sort(list(row({', '.join(cols) or 'NULL'})::VARCHAR))"
    groups = con.execute(f"""SELECT {k}, a.v, b.v FROM dk
        LEFT JOIN (SELECT {k}, {tup} AS v, count(*) AS n FROM ref.{table} WHERE {filt} GROUP BY ALL) a USING ({k})
        LEFT JOIN (SELECT {k}, {tup} AS v, count(*) AS n FROM rt.{table} WHERE {filt} GROUP BY ALL) b USING ({k})
        ORDER BY ALL""").fetchall()
    out["duplicate_key_groups"] = [{"key": list(g[:len(keys)]), "reference": g[-2], "route": g[-1],
                                    "same_distinct_values": g[-2] is not None and g[-1] is not None
                                    and sorted(set(g[-2])) == sorted(set(g[-1]))} for g in groups]
    con.execute(f"""CREATE OR REPLACE TEMP TABLE j AS SELECT {", ".join(f"coalesce(a.{c}, b.{c}) AS {c}" for c in keys)},
        a.{keys[0]} IS NULL AS only_rt, b.{keys[0]} IS NULL AS only_ref,
        {", ".join(f"a.{c}::VARCHAR AS ref_{c}, b.{c}::VARCHAR AS rt_{c}, a.{c} IS DISTINCT FROM b.{c} AS x_{c}, "
                   + (f"NOT ({float_equal_sql(f'a.{c}', f'b.{c}')}) AS d_{c}" if ftypes.get(c) in FLOAT_TYPES
                      else f"a.{c} IS DISTINCT FROM b.{c} AS d_{c}") for c in cols) or "NULL AS _"}
        FROM (SELECT * FROM ref.{table} WHERE {filt} AND ({k}) NOT IN (SELECT ({k}) FROM dk)) a
        FULL JOIN (SELECT * FROM rt.{table} WHERE {filt} AND ({k}) NOT IN (SELECT ({k}) FROM dk)) b USING ({k})""")
    for side in ("ref", "rt"):
        rows = con.execute(f"SELECT {k} FROM j WHERE only_{side} ORDER BY ALL").fetchall()
        out[f"only_{side}_keys"] = {"n": len(rows), "keys": [list(r) for r in rows[:LIST_CAP]]}
    out["columns"] = {}
    out["float_columns_within_tolerance_only"] = {}
    for c in cols:
        if ftypes.get(c) in FLOAT_TYPES:
            noise = con.execute(f"SELECT count(*) FROM j WHERE NOT only_rt AND NOT only_ref AND x_{c} AND NOT d_{c}").fetchone()[0]
            if noise:
                out["float_columns_within_tolerance_only"][c] = noise
        n = con.execute(f"SELECT count(*) FROM j WHERE NOT only_rt AND NOT only_ref AND d_{c}").fetchone()[0]
        if n:
            rows = con.execute(f"SELECT {k}, ref_{c}, rt_{c} FROM j WHERE NOT only_rt AND NOT only_ref AND d_{c} "
                               f"ORDER BY ALL LIMIT {LIST_CAP}").fetchall()
            out["columns"][c] = {"n": n, "share_of_route_rows": n / max(out["rt_rows"], 1),
                                 "rows": [{"key": list(r[:len(keys)]), "reference": r[-2], "route": r[-1]} for r in rows]}
    out["differing_values"] = sum(v["n"] for v in out["columns"].values())
    out["identical"] = not (out["differing_values"] or out["only_ref_keys"]["n"] or out["only_rt_keys"]["n"]
                            or out["rt_duplicate_keys"] or not all(g["same_distinct_values"] for g in out["duplicate_key_groups"]))
    return out


def _attach(ref: Path, rt: Path, **more: Path):
    con = duckdb.connect()
    con.execute(f"SET threads = {C.DUCKDB_THREADS}")
    for name, path in (("ref", ref), ("rt", rt), *more.items()):
        con.execute(f"ATTACH '{path}' AS {name} (READ_ONLY)")
    return con


def _season_tables(con, db: str) -> list[tuple[str, bool]]:
    return [(f"{s}.{t}", "season" in _cols(con, db, f"{s}.{t}"))
            for s, t in con.execute(f"SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = '{db}' "
                                    "AND NOT temporary ORDER BY 1, 2").fetchall()]


def e7_table(con, t: str, where: str, a: str = "ref", b: str = "rt") -> dict:
    """Byte fingerprint first; if it differs, rows are paired on every non-float column (must be a unique key on both
    sides, compared exactly, null masks of float columns included) and each float column uses the 1e-12 rule."""
    fp = {side: "{}:{}:{}".format(*con.execute(f"SELECT count(*), bit_xor(hash(r)), sum(hash(r)) FROM "
                                               f"(SELECT * FROM {db}.{t} {where}) r").fetchone())
          for side, db in (("ref", a), ("rt", b))}
    out = {**fp, "byte_identical": fp["ref"] == fp["rt"]}
    if out["byte_identical"]:
        return {**out, "pass": True}
    ty = _types(con, a, t)
    if ty != _types(con, b, t):
        return {**out, "pass": False, "failure": "column names or types differ"}
    fl = [c for c, d in ty.items() if d in FLOAT_TYPES]
    ex = [c for c in ty if c not in fl]
    keyx = f"hash(row({', '.join(ex + [f'{c} IS NULL' for c in fl])}))"
    for side, db in (("ref", a), ("rt", b)):
        n, nk = con.execute(f"SELECT count(*), count(DISTINCT {keyx}) FROM {db}.{t} {where}").fetchone()
        if n != nk:
            return {**out, "pass": False, "failure": f"{side}: non-float columns are not a unique row key ({n} rows, {nk} keys)"}
    con.execute(f"""CREATE OR REPLACE TEMP TABLE e7 AS SELECT x.k IS NULL AS only_rt, y.k IS NULL AS only_ref,
        {", ".join(f"x.{c} AS a_{c}, y.{c} AS b_{c}" for c in fl) or "NULL AS _"}
        FROM (SELECT {keyx} AS k, * FROM {a}.{t} {where}) x FULL JOIN (SELECT {keyx} AS k, * FROM {b}.{t} {where}) y USING (k)""")
    unpaired = con.execute("SELECT count(*) FILTER (WHERE only_ref), count(*) FILTER (WHERE only_rt) FROM e7").fetchone()
    cols = {}
    for c in fl:
        n_ex, n_bad, mx = con.execute(f"""SELECT count(*) FILTER (WHERE a_{c} IS DISTINCT FROM b_{c}),
            count(*) FILTER (WHERE NOT ({float_equal_sql(f'a_{c}', f'b_{c}')})),
            max(abs(a_{c} - b_{c})) FILTER (WHERE NOT isnan(a_{c}) AND NOT isnan(b_{c}))
            FROM e7 WHERE NOT only_ref AND NOT only_rt""").fetchone()
        if n_ex:
            cols[c] = {"bitwise_different": n_ex, "outside_tolerance": n_bad, "max_abs_difference": mx}
    return {**out, "exact_columns": len(ex), "float_columns": len(fl),
            "unpaired_rows": {"ref": unpaired[0], "rt": unpaired[1]}, "float_differences": cols,
            "pass": unpaired == (0, 0) and all(v["outside_tolerance"] == 0 for v in cols.values())}


# ============================================================================ row-level differences and their causes
# Architect directive 2026-10-15. Every route difference gets exactly ONE cause, established by a counterfactual, not by
# proximity: the ABLATION build is the route build with only the rows classified EXPLAINED_PROVIDER_DATA_REVISION (team boxes
# current CBBD supplies for games the Starter Pack snapshot has none for) removed from staging -- a diagnostic, never the
# route. A route difference that is absent from (ablation vs reference) is caused by those team-box revisions; one that
# persists is classified on its own (E3: a provider display-name revision satisfying all seven conditions) or is
# UNEXPLAINED / OTHER. A difference present only in the ablation would mean the ablation does not isolate the cause and
# is counted as unexplained. Non-final games and provider team assignments are fixed in the route (their rows equal the
# reference), so a residual difference in those games is measured, not assumed away.
CAUSES = ("NON_FINAL_GAME_REMOVAL", "PROVIDER_NAME_REVISION", "PROVIDER_TEAM_ASSIGNMENT_REPAIR",
          "PROVIDER_TEAM_ASSIGNMENT_QUARANTINE", "EXPLAINED_PROVIDER_DATA_REVISION", "EXPLAINED_PROVIDER_LABEL_REVISION",
          "FLOATING_POINT_NOISE", "OTHER")
# Architect ruling 2026-10-16: CBBD is the authoritative provider; the Starter Pack stays the development reference but
# need not equal current CBBD where CBBD carries legitimate corrected or newly supplied historical facts.
ARCHITECT_RULING = {
    "date": "2026-10-16",
    "cbbd_authority": "ACCEPTED",
    "team_boxes": "route-only CBBD team boxes of final games the Starter Pack has none for, satisfying the integrity "
                  "checks A-D, are EXPLAINED_PROVIDER_DATA_REVISION (the 10 games' matchup/date/score were verified "
                  "against official school records by the architect); they are kept, never removed to match the reference",
    "provider_labels": "a Stage 3 row differing only in a provider display-text column that no feature, label or scoring "
                       "code reads is EXPLAINED_PROVIDER_LABEL_REVISION; provider strings are not rewritten",
    "e8": "frozen tolerance unchanged; E8_FROZEN_REFERENCE_COMPARISON is reported as measured. Route closure accepts an "
          "E8 difference only when the ablation (route minus the accepted team boxes) reproduces the reference within the "
          "frozen tolerance, gate verdicts are identical, and nothing else is unexplained",
}
# provider display text carried through to research columns only. The producer modules may name the column; any other
# module (features, labels, scoring) or the frozen artifact naming it makes a difference in it unexplainable here.
PROVIDER_LABEL_COLUMNS = ("tournament",)
LABEL_PRODUCERS = ("cbb_platform/build_canonical.py", "cbb_platform/event_build.py", "cbb_platform/stage3/universe.py",
                   "cbb_platform/cbbd_box_route.py", "cbb_platform/cbbd_box_route_reconcile.py",
                   "cbb_platform/adapters/")            # provider ingestion adapters (a directory prefix)
FROZEN_ARTIFACT = "modeling/player_props_v1/final_prelockbox_2024_25_artifact.json"
FROZEN_ARTIFACT_SHA256 = "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"
SAMPLE = 50
E8_TOL = 1e-3                                         # spec §5.5 (frozen 2026-10-13): every gated metric, absolute
SEASON_FILTER = f"season IN ({', '.join(map(str, ROUTE_SEASONS))})"


def _cid(prefix: str, entity: str, provider_id) -> str:
    """build_canonical's cid macro."""
    return f"{prefix}-" + hashlib.sha256(f"CBBD|{entity}|{provider_id}".encode()).hexdigest()[:16]


def _describe(con, rel: str) -> dict:
    return {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()}


def rowdiff(con, rel_a: str, rel_b: str, keys, cols, name: str, where: str = "", float_tol: bool = False) -> dict:
    """TEMP TABLE `name`: one row per differing key of rel_a (reference) vs rel_b, with status ONLY_REF / ONLY_ROUTE / DIFF,
    the differing columns, the season, and float_noise_only (bitwise different, every float within the 1e-12 rule; kept
    so noise is counted, never treated as a difference). A key duplicated on either side compares as a multiset."""
    ty = _describe(con, rel_a)
    if _describe(con, rel_b).keys() != ty.keys():
        raise ValueError(f"{rel_a} / {rel_b}: column sets differ")
    if cols is None:
        cols = [c for c in ty if c not in keys and c not in LINEAGE]
    cols = [c for c in cols if c in ty and c not in keys]
    k = ", ".join(keys)
    filt = where or "TRUE"
    fl = [c for c in cols if float_tol and ty[c] in FLOAT_TYPES]
    differs = {c: (f"NOT ({float_equal_sql(f'x.{c}', f'y.{c}')})" if c in fl else f"x.{c} IS DISTINCT FROM y.{c}") for c in cols}
    dcols = "list_filter([" + ", ".join(f"CASE WHEN {d} THEN '{c}' END" for c, d in differs.items()) + "]::VARCHAR[], v -> v IS NOT NULL)" \
        if cols else "[]::VARCHAR[]"
    noise = " OR ".join(f"(x.{c} IS DISTINCT FROM y.{c} AND NOT ({differs[c]}))" for c in fl) or "FALSE"
    season = "coalesce(x.season, y.season)" if "season" in ty else "NULL::INTEGER"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE {name}_dk AS SELECT DISTINCT {k} FROM (
        SELECT {k} FROM {rel_a} WHERE {filt} GROUP BY ALL HAVING count(*) > 1 UNION ALL
        SELECT {k} FROM {rel_b} WHERE {filt} GROUP BY ALL HAVING count(*) > 1)""")
    tup = f"list_sort(list(row({', '.join(cols) or 'NULL'})::VARCHAR))"
    sea = "min(season)" if "season" in ty else "NULL::INTEGER"
    con.execute(f"""CREATE OR REPLACE TEMP TABLE {name} AS
        SELECT * FROM (
            SELECT {", ".join(f"coalesce(x.{c}, y.{c}) AS {c}" for c in keys)}, {season} AS _season,
                   CASE WHEN x.{keys[0]} IS NULL THEN 'ONLY_ROUTE' WHEN y.{keys[0]} IS NULL THEN 'ONLY_REF' ELSE 'DIFF' END AS status,
                   CASE WHEN x.{keys[0]} IS NULL OR y.{keys[0]} IS NULL THEN []::VARCHAR[] ELSE {dcols} END AS cols,
                   x.{keys[0]} IS NOT NULL AND y.{keys[0]} IS NOT NULL AND len({dcols}) = 0 AND ({noise}) AS float_noise_only
            FROM (SELECT * FROM {rel_a} WHERE {filt} AND ({k}) NOT IN (SELECT ({k}) FROM {name}_dk)) x
            FULL JOIN (SELECT * FROM {rel_b} WHERE {filt} AND ({k}) NOT IN (SELECT ({k}) FROM {name}_dk)) y USING ({k}))
        WHERE status <> 'DIFF' OR len(cols) > 0 OR float_noise_only
        UNION ALL BY NAME
        SELECT {k}, coalesce(xa.s, yb.s) AS _season,
               CASE WHEN xa.v IS NULL THEN 'ONLY_ROUTE' WHEN yb.v IS NULL THEN 'ONLY_REF' ELSE 'DIFF' END AS status,
               ['<duplicate-key multiset>']::VARCHAR[] AS cols, FALSE AS float_noise_only
        FROM {name}_dk LEFT JOIN (SELECT {k}, {tup} AS v, {sea} AS s FROM {rel_a} WHERE {filt} GROUP BY ALL) xa USING ({k})
                       LEFT JOIN (SELECT {k}, {tup} AS v, {sea} AS s FROM {rel_b} WHERE {filt} GROUP BY ALL) yb USING ({k})
        WHERE xa.v IS DISTINCT FROM yb.v""")
    n = dict(con.execute(f"SELECT status, count(*) FROM {name} WHERE NOT float_noise_only GROUP BY 1").fetchall())
    return {"keys": list(keys), "columns_compared": cols, "rows": {rel: con.execute(f"SELECT count(*) FROM {rel} WHERE {filt}").fetchone()[0]
                                                                   for rel in (rel_a, rel_b)},
            "only_ref": n.get("ONLY_REF", 0), "only_route": n.get("ONLY_ROUTE", 0), "different": n.get("DIFF", 0),
            "float_noise_only_rows": con.execute(f"SELECT count(*) FROM {name} WHERE float_noise_only").fetchone()[0],
            "duplicate_keys": {side: con.execute(f"SELECT count(*) FROM (SELECT {k} FROM {rel} WHERE {filt} GROUP BY ALL HAVING count(*) > 1)").fetchone()[0]
                               for side, rel in (("ref", rel_a), ("route", rel_b))},
            "columns": dict(con.execute(f"SELECT c, count(*) FROM (SELECT unnest(cols) AS c FROM {name}) GROUP BY 1 ORDER BY 2 DESC, 1").fetchall())}


def attribute(con, name: str, keys, residual_class=None) -> dict:
    """Cause of every row of route diff `{name}_rt` given ablation diff `{name}_ab` (same keys). Adds column `cause`."""
    on = " AND ".join(f"r.{c} IS NOT DISTINCT FROM a.{c}" for c in keys)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE {name}_cause AS
        SELECT r.*, CASE WHEN r.float_noise_only THEN 'FLOATING_POINT_NOISE'
                         WHEN a.status IS NULL THEN 'EXPLAINED_PROVIDER_DATA_REVISION'
                         ELSE {residual_class or "'OTHER'"} END AS cause
        FROM {name}_rt r LEFT JOIN (SELECT * FROM {name}_ab WHERE NOT float_noise_only) a ON {on}""")
    ab_only = con.execute(f"""SELECT count(*) FROM {name}_ab a WHERE NOT a.float_noise_only AND NOT EXISTS
        (SELECT 1 FROM {name}_rt r WHERE {on})""").fetchone()[0]
    return _summarize(con, name, keys, ab_only)


def _summarize(con, name: str, keys, ab_only: int) -> dict:
    by = dict(con.execute(f"SELECT cause, count(*) FROM {name}_cause GROUP BY 1 ORDER BY 1").fetchall())
    by_season = {str(s): dict(con.execute(f"SELECT cause, count(*) FROM {name}_cause WHERE _season = ? GROUP BY 1 ORDER BY 1", [s]).fetchall())
                 for s in ROUTE_SEASONS}
    sample = [dict(zip([*keys, "status", "cols", "cause"], r)) for r in con.execute(
        f"SELECT {', '.join(keys)}, status, cols, cause FROM {name}_cause WHERE cause NOT IN ('FLOATING_POINT_NOISE') "
        f"ORDER BY cause = 'OTHER' DESC, {', '.join(keys)} LIMIT {SAMPLE}").fetchall()]
    return {"by_cause": {c: by.get(c, 0) for c in CAUSES}, "by_season": by_season,
            "ablation_only_differences": ab_only, "untraced": by.get("OTHER", 0) + ab_only, "sample": sample}


def label_consumers(col: str) -> list[str]:
    """Files outside LABEL_PRODUCERS that name `col` as a word: every cbb_platform module and the frozen artifact."""
    import re
    pat = re.compile(rf"\b{re.escape(col)}\b")
    files = sorted(p for p in (C.PROJECT_ROOT / "cbb_platform").rglob("*.py")) + [C.PROJECT_ROOT / FROZEN_ARTIFACT]
    return [str(p.relative_to(C.PROJECT_ROOT)) for p in files
            if not str(p.relative_to(C.PROJECT_ROOT)).startswith(LABEL_PRODUCERS) and pat.search(p.read_text())]


def label_revisions(con, name: str, rels: dict, keys, attribution: dict, label_cols, float_cols) -> dict:
    """EXPLAINED_PROVIDER_LABEL_REVISION, generically: an OTHER-caused difference whose ONLY difference is in provider
    display-text columns (`label_cols`) that nothing downstream reads. DIFF rows (natural keys) qualify when their differing
    columns are all label columns; row-hash tables (key `_row_key`) pair one ONLY_REF with one ONLY_ROUTE row on the hash
    of every other non-float column (`_lf`), floats equal under the approved rule. The value mapping must be one-to-one in
    both directions (a consistent relabelling, not a fact change). Provider strings are never rewritten."""
    consumers = {c: label_consumers(c) for c in label_cols}
    out = {"columns": list(label_cols), "consumers_outside_producers": consumers, "rows": 0, "pairs": 0, "mapping": []}
    if any(consumers.values()):
        return {**attribution, "provider_label_revisions": {**out, "applied": False}}
    lab = lambda a: "struct_pack(" + ", ".join(f"{c} := {a}.{c}" for c in label_cols) + ")::VARCHAR"
    if keys == ("_row_key",):
        feq = " AND ".join(f"({float_equal_sql(f'x.{c}', f'y.{c}')})" for c in float_cols) or "TRUE"
        con.execute(f"""CREATE OR REPLACE TEMP TABLE {name}_lbl AS
            WITH o AS (SELECT _row_key, status FROM {name}_cause WHERE cause = 'OTHER' AND status <> 'DIFF'),
                 x AS (SELECT v.* FROM {rels['ref']} v JOIN o ON o.status = 'ONLY_REF' AND o._row_key = v._row_key),
                 y AS (SELECT v.* FROM {rels['rt']} v JOIN o ON o.status = 'ONLY_ROUTE' AND o._row_key = v._row_key),
                 u AS (SELECT _lf FROM x GROUP BY 1 HAVING count(*) = 1 INTERSECT SELECT _lf FROM y GROUP BY 1 HAVING count(*) = 1)
            SELECT x._row_key AS ref_key, y._row_key AS rt_key, {lab('x')} AS ref_label, {lab('y')} AS rt_label
            FROM x JOIN y USING (_lf) WHERE _lf IN (SELECT _lf FROM u) AND {feq}""")
        apply_ = f"""(status = 'ONLY_REF' AND _row_key IN (SELECT ref_key FROM {name}_lbl WHERE ok))
                  OR (status = 'ONLY_ROUTE' AND _row_key IN (SELECT rt_key FROM {name}_lbl WHERE ok))"""
    else:
        on = " AND ".join(f"x.{k} = c.{k}" for k in keys)
        only_labels = f"list_has_all([{', '.join(repr(c) for c in label_cols)}], c.cols)"
        con.execute(f"""CREATE OR REPLACE TEMP TABLE {name}_lbl AS
            SELECT {', '.join(f'c.{k}' for k in keys)}, {lab('x')} AS ref_label, {lab('y')} AS rt_label
            FROM {name}_cause c JOIN {rels['ref']} x ON {on} JOIN {rels['rt']} y ON {on.replace('x.', 'y.')}
            WHERE c.cause = 'OTHER' AND c.status = 'DIFF' AND {only_labels}""")
        apply_ = f"status = 'DIFF' AND ({', '.join(keys)}) IN (SELECT ({', '.join(keys)}) FROM {name}_lbl WHERE ok)"
    # one-to-one mapping check over the whole table (a value mapped two ways is not a relabelling)
    con.execute(f"""ALTER TABLE {name}_lbl ADD COLUMN ok BOOLEAN DEFAULT TRUE""")
    con.execute(f"""UPDATE {name}_lbl SET ok = FALSE WHERE ref_label IN (SELECT ref_label FROM {name}_lbl GROUP BY 1
        HAVING count(DISTINCT rt_label) > 1) OR rt_label IN (SELECT rt_label FROM {name}_lbl GROUP BY 1 HAVING count(DISTINCT ref_label) > 1)""")
    con.execute(f"UPDATE {name}_cause SET cause = 'EXPLAINED_PROVIDER_LABEL_REVISION' WHERE cause = 'OTHER' AND ({apply_})")
    out["mapping"] = [{"reference": a, "route": b, "rows": n, "one_to_one": bool(ok)} for a, b, n, ok in con.execute(
        f"SELECT ref_label, rt_label, count(*), bool_and(ok) FROM {name}_lbl GROUP BY 1, 2 ORDER BY 1, 2").fetchall()]
    out["pairs"] = con.execute(f"SELECT count(*) FROM {name}_lbl WHERE ok").fetchone()[0]
    out["rows"] = con.execute(f"SELECT count(*) FROM {name}_cause WHERE cause = 'EXPLAINED_PROVIDER_LABEL_REVISION'").fetchone()[0]
    return {**_summarize(con, name, keys, attribution["ablation_only_differences"]), "provider_label_revisions": {**out, "applied": True}}


def _e3_view(con, db: str) -> str:
    """Substantive player-game key: (game, team, canonical player); a row without player identity keys on its source name
    (UNRESOLVED_NAME:<name>), so the canonical ambiguity groups compare as multisets. The display name is a compared
    column, classified separately (it is not identity)."""
    con.execute(f"""CREATE OR REPLACE TEMP VIEW e3v_{db} AS SELECT coalesce(canonical_player_id, 'UNRESOLVED_NAME:' || source_player_name)
        AS player_key, * FROM {db}.fact.player_games WHERE {SEASON_FILTER}""")
    return f"e3v_{db}"


E3_COLS = ("canonical_player_id", "identity_status", "starter", "minutes", *COUNTS, "usage_source", "accounting_issues",
           "source_player_name")
CANON_DIFF = {   # spec §3 tables; E3 on the substantive key (directive §12)
    "E1": ("core.games", ("canonical_game_id",), CANON["E1"][2], ""),
    "E2": ("fact.team_games", ("canonical_game_id", "canonical_team_id"), None, ""),
    "E3": (None, ("canonical_game_id", "canonical_team_id", "player_key"), E3_COLS, ""),
    "E4": ("fact.participation", ("canonical_game_id", "canonical_team_id", "canonical_player_id"), CANON["E4"][2],
           "canonical_player_id IS NOT NULL"),
    "E5": ("flagship.played_games", ("canonical_game_id",), (), ""),
}


def canonical_differences(con, db: str = "rt", tag: str = "rt", only=None) -> dict:
    out = {}
    for e, (table, keys, cols, where) in CANON_DIFF.items():
        if only and e not in only:
            continue
        a = _e3_view(con, "ref") if e == "E3" else f"ref.{table}"
        b = _e3_view(con, db) if e == "E3" else f"{db}.{table}"
        w = SEASON_FILTER + (f" AND {where}" if where else "")
        out[e] = rowdiff(con, a, b, keys, cols, f"{e.lower()}_{tag}", w)
    return out


def canonical_classification(con, only=None) -> dict:
    """E1-E5: route diffs vs ablation diffs -> EXPLAINED_PROVIDER_DATA_REVISION / PROVIDER_NAME_REVISION (E3) / UNEXPLAINED."""
    out = {}
    for e, (_, keys, _, _) in CANON_DIFF.items():
        if only and e not in only:
            continue
        n = e.lower()
        res = "CASE WHEN r.status = 'DIFF' AND r.cols = ['source_player_name'] THEN 'PROVIDER_NAME_REVISION' ELSE 'OTHER' END" \
            if e == "E3" else None
        a = attribute(con, n, keys, res)
        if e == "E3":
            a = _pair_unresolved_name_revisions(con, a)
        a["by_cause"]["UNEXPLAINED"] = a["by_cause"].pop("OTHER")
        a["unexplained"] = a.pop("untraced")
        out[e] = a
    return out


def _pair_unresolved_name_revisions(con, a: dict) -> dict:
    """A player WITHOUT canonical identity in both builds keys on the display name, so a revised name appears as one
    ONLY_REF + one ONLY_ROUTE row. They are one PROVIDER_NAME_REVISION when, in that (game, team), exactly one such row
    exists on each side and every compared fact (identity status, starter, minutes, every count, usage, accounting) is
    equal; anything else stays unexplained."""
    facts = [c for c in E3_COLS if c != "source_player_name"]
    con.execute(f"""CREATE OR REPLACE TEMP TABLE e3_unres_pairs AS
        WITH o AS (SELECT * FROM e3_cause WHERE cause = 'OTHER' AND status IN ('ONLY_REF', 'ONLY_ROUTE')
                   AND player_key LIKE 'UNRESOLVED_NAME:%'),
             one AS (SELECT canonical_game_id, canonical_team_id FROM o GROUP BY 1, 2
                     HAVING count(*) FILTER (WHERE status = 'ONLY_REF') = 1 AND count(*) FILTER (WHERE status = 'ONLY_ROUTE') = 1)
        SELECT x.canonical_game_id, x.canonical_team_id, x.player_key AS ref_key, y.player_key AS route_key
        FROM o x JOIN o y USING (canonical_game_id, canonical_team_id) JOIN one USING (canonical_game_id, canonical_team_id)
        JOIN e3v_ref a ON a.canonical_game_id = x.canonical_game_id AND a.canonical_team_id = x.canonical_team_id AND a.player_key = x.player_key
        JOIN e3v_rt b ON b.canonical_game_id = y.canonical_game_id AND b.canonical_team_id = y.canonical_team_id AND b.player_key = y.player_key
        WHERE x.status = 'ONLY_REF' AND y.status = 'ONLY_ROUTE' AND {" AND ".join(f"a.{c} IS NOT DISTINCT FROM b.{c}" for c in facts)}""")
    con.execute("""UPDATE e3_cause c SET cause = 'PROVIDER_NAME_REVISION' FROM e3_unres_pairs p
        WHERE c.canonical_game_id = p.canonical_game_id AND c.canonical_team_id = p.canonical_team_id
          AND c.player_key IN (p.ref_key, p.route_key)""")
    by = dict(con.execute("SELECT cause, count(*) FROM e3_cause GROUP BY 1").fetchall())
    a["by_cause"] = {c: by.get(c, 0) for c in CAUSES}
    a["by_season"] = {str(s): dict(con.execute("SELECT cause, count(*) FROM e3_cause WHERE _season = ? GROUP BY 1 ORDER BY 1", [s]).fetchall())
                      for s in ROUTE_SEASONS}
    a["untraced"] = by.get("OTHER", 0) + a["ablation_only_differences"]
    a["unresolved_identity_name_revision_pairs"] = con.execute("SELECT count(*) FROM e3_unres_pairs").fetchone()[0]
    return a


def name_revisions(con) -> dict:
    """Directive §7: a display-name difference is EXPLAINED_PROVIDER_NAME_REVISION only when all seven conditions hold."""
    rows = con.execute("""SELECT d.canonical_game_id, d.canonical_team_id, d.player_key, a.source_player_name, b.source_player_name, a.season
        FROM e3_cause d JOIN e3v_ref a USING (canonical_game_id, canonical_team_id, player_key)
                        JOIN e3v_rt b USING (canonical_game_id, canonical_team_id, player_key)
        WHERE d.cause = 'PROVIDER_NAME_REVISION' AND d.status = 'DIFF'
        UNION ALL
        SELECT p.canonical_game_id, p.canonical_team_id, NULL, a.source_player_name, b.source_player_name, a.season
        FROM e3_unres_pairs p JOIN e3v_ref a ON a.canonical_game_id = p.canonical_game_id AND a.canonical_team_id = p.canonical_team_id
             AND a.player_key = p.ref_key
        JOIN e3v_rt b ON b.canonical_game_id = p.canonical_game_id AND b.canonical_team_id = p.canonical_team_id AND b.player_key = p.route_key
        ORDER BY ALL""").fetchall()
    # (1)-(3) the substantive key is equal by construction; (4) cols == [source_player_name] means every stat, the player id
    # and the identity status are equal; (5)/(6) no merge / split: name-keyed pairing never changes identity, and no route
    # player id maps to two reference ids (or back) on any shared (game, team); (7) no row lost: E3 has no ONLY_REF row
    # that is not otherwise explained.
    merge_split = con.execute("""SELECT count(*) FROM e3v_ref a JOIN e3v_rt b USING (canonical_game_id, canonical_team_id, source_player_name)
        WHERE a.canonical_player_id IS DISTINCT FROM b.canonical_player_id""").fetchone()[0]
    many = con.execute("""SELECT count(*) FROM (SELECT a.canonical_player_id FROM e3v_ref a JOIN e3v_rt b
        USING (canonical_game_id, canonical_team_id, player_key) WHERE a.canonical_player_id IS NOT NULL
        GROUP BY 1 HAVING count(DISTINCT b.canonical_player_id) <> 1)""").fetchone()[0]
    lost = con.execute("SELECT count(*) FROM e3_cause WHERE status = 'ONLY_REF' AND cause NOT IN ('EXPLAINED_PROVIDER_DATA_REVISION', 'PROVIDER_NAME_REVISION')").fetchone()[0]
    by_player = {}
    for g, t, pid, old, new, season in rows:
        p = by_player.setdefault((pid or "", old, new), {"canonical_player_id": pid, "identity": "RESOLVED" if pid else
                                                          "UNRESOLVED_IN_BOTH_BUILDS", "old_display_name": old,
                                                          "new_display_name": new, "seasons": set(), "affected_player_games": 0})
        p["seasons"].add(season)
        p["affected_player_games"] += 1
    players = [{**v, "seasons": sorted(v["seasons"])} for _, v in sorted(by_player.items())]
    raw_ok = _raw_name_check(con, rows)
    conds = {"1_player_id_unchanged": True, "2_game_unchanged": True, "3_team_unchanged": True, "4_stats_reconcile": True,
             "5_no_identity_merge": merge_split == 0 and many == 0, "6_no_identity_split": merge_split == 0 and many == 0,
             "7_no_row_lost_to_spelling": lost == 0, "route_name_in_raw_cbbd_json": raw_ok["all"]}
    return {"rows": len(rows), "distinct_resolved_players": len({r[2] for r in rows if r[2]}), "revisions": len(players),
            "unresolved_identity_rows": sum(r[2] is None for r in rows),
            "identity_changes_on_same_name": merge_split, "players_mapping_to_not_exactly_one_route_id": many,
            "unexplained_missing_rows": lost, "conditions": conds, "all_conditions_hold": all(conds.values()),
            "raw_check": {k: v for k, v in raw_ok.items() if k != "all"}, "players": players}


def _raw_index(endpoint: str) -> list[dict]:
    """Stored route payload entries for an endpoint, read from the route request log directly (not via the route code)."""
    out = {}
    for line in Path(B.DEV_LOG).read_text().splitlines():
        e = json.loads(line)
        if e.get("purpose") == B.PURPOSE and e.get("status") in ("STORED", "ALREADY_STORED") and e["endpoint"] == endpoint \
                and e.get("season") in ROUTE_SEASONS:
            out[e["request_key"]] = e
    return sorted(out.values(), key=lambda e: e["request_key"])


def _raw_name_check(con, rows) -> dict:
    """Each revised route name appears in the raw CBBD games/players JSON for that game (json.loads, no reshape)."""
    ids = dict(con.execute("""SELECT canonical_game_id, split_part(split_part(source_record_key, ';', 1), '=', 2)
        FROM rt.core.games WHERE season IN (2024, 2025)""").fetchall())
    want = {}
    for g, _, _, _, new, _ in rows:
        want.setdefault(ids.get(g), set()).add(new)
    found = {gid: set() for gid in want}
    for e in _raw_index("games/players"):
        for rec in json.loads(raw_store.read_payload(B.DEV_RAW_ROOT / e["stored_file"])):
            gid = str(rec.get("gameId"))
            if gid in want:
                found[gid] |= {p.get("name") for p in rec.get("players") or []} & want[gid]
    missing = sorted((g, n) for g in want for n in want[g] - found[g])
    return {"all": not missing, "names_checked": sum(map(len, want.values())), "missing": missing[:SAMPLE]}


def team_box_revisions(con) -> list[dict]:
    """Directive §10 A-D for every team box present in the route only, on a game the reference has: identity, final,
    internal validity, provider backfill confirmed against the raw CBBD games/teams JSON (json.loads, no reshape)."""
    rows = con.execute("""SELECT t.canonical_game_id, t.canonical_team_id, tm.school, t.season, g.game_date_et,
            split_part(split_part(g.source_record_key, ';', 1), '=', 2) AS cbbd_game_id,
            t.canonical_team_id IN (g.home_team_id, g.away_team_id) AS team_in_game,
            g.canonical_game_id IN (SELECT canonical_game_id FROM rt.flagship.played_games) AS played,
            t.points, CASE WHEN t.canonical_team_id = g.home_team_id THEN g.home_points ELSE g.away_points END AS game_points,
            t.fgm, t.fga, t.three_pm, t.three_pa, t.ftm, t.fta, t.orb, t.drb, t.reb, t.game_minutes,
            40 + 5 * (len(g.home_period_points) - 2) AS minutes_implied_by_period_line,
            t.points_by_period IS NOT DISTINCT FROM CASE WHEN t.canonical_team_id = g.home_team_id THEN g.home_period_points
                 ELSE g.away_period_points END AS periods_match_game,
            (SELECT count(*) FROM ref.fact.team_games r WHERE r.canonical_game_id = t.canonical_game_id) AS ref_team_boxes_for_game,
            rg.has_team_box_score AS ref_has_team_box_score
        FROM rt.fact.team_games t JOIN rt.core.games g USING (canonical_game_id) LEFT JOIN rt.core.teams tm USING (canonical_team_id)
        LEFT JOIN ref.core.games rg USING (canonical_game_id)
        WHERE t.season IN (2024, 2025) AND NOT EXISTS (SELECT 1 FROM ref.fact.team_games r
              WHERE r.canonical_game_id = t.canonical_game_id AND r.canonical_team_id = t.canonical_team_id)
        ORDER BY t.canonical_game_id, t.canonical_team_id""").fetchall()
    cols = ["canonical_game_id", "canonical_team_id", "team", "season", "game_date_et", "cbbd_game_id", "team_in_game", "played",
            "points", "game_points", "fgm", "fga", "three_pm", "three_pa", "ftm", "fta", "orb", "drb", "reb", "game_minutes",
            "minutes_implied_by_period_line", "periods_match_game", "ref_team_boxes_for_game", "ref_has_team_box_score"]
    recs = [dict(zip(cols, r)) for r in rows]
    raw = {}
    for e in _raw_index("games/teams"):
        for rec in json.loads(raw_store.read_payload(B.DEV_RAW_ROOT / e["stored_file"])):
            raw[(str(rec.get("gameId")), str(rec.get("teamId")))] = rec
    status = {}
    for e in _raw_index("games"):
        for rec in json.loads(raw_store.read_payload(B.DEV_RAW_ROOT / e["stored_file"])):
            status[str(rec["id"])] = rec.get("status")
    for r in recs:
        team_raw = [v for (g, t), v in raw.items() if g == r["cbbd_game_id"] and _cid("T", "team", t) == r["canonical_team_id"]]
        pts = [((v.get("teamStats") or {}).get("points") or {}).get("total") for v in team_raw]
        r["A_identity_valid"] = bool(r["team_in_game"])
        r["B_final"] = bool(r["played"]) and B.is_final_status(status.get(r["cbbd_game_id"]))
        counts = [r[c] for c in ("fgm", "fga", "three_pm", "three_pa", "ftm", "fta", "orb", "drb", "reb")]
        r["box_completeness"] = "FULL" if all(v is not None for v in counts) else \
            "POINTS_ONLY" if all(v is None for v in counts) else "PARTIAL_COUNTS"
        # internally valid: every value the record carries is consistent with the game record (points = final score,
        # period line = the game's period line, game minutes = regulation + 5 per extra period of that line; a FULL box
        # also satisfies the counting identities). A POINTS_ONLY record carries no counts; Stage 3 efficiency state
        # ignores it (team_state: fga IS NOT NULL). Its game_minutes is a fact of the completed game; Stage 3 uses it
        # only after the game (substitution-feed completeness for that game's labels / later participation state).
        r["C_internally_valid"] = r["points"] == r["game_points"] and bool(r["periods_match_game"]) and (
            r["game_minutes"] is None or r["game_minutes"] == r["minutes_implied_by_period_line"]) and (
            r["box_completeness"] == "POINTS_ONLY" or (r["box_completeness"] == "FULL"
            and r["points"] == 2 * r["fgm"] + r["three_pm"] + r["ftm"] and r["reb"] == r["orb"] + r["drb"]
            and r["fgm"] <= r["fga"] and r["three_pm"] <= r["three_pa"] and r["ftm"] <= r["fta"]))
        r["D_provider_backfill"] = r["ref_team_boxes_for_game"] == 0 and len(pts) == 1 and pts[0] == r["points"]
        r["raw_cbbd_status"] = status.get(r["cbbd_game_id"])
        r["classification"] = "EXPLAINED_PROVIDER_DATA_REVISION" if all(r[k] for k in ("A_identity_valid", "B_final", "C_internally_valid",
                                                                                  "D_provider_backfill")) else "UNEXPLAINED"
    return recs


def non_final_in_modeling_state(con, staging_report: dict, stores: dict) -> dict:
    """Every excluded non-final CBBD game id, looked up in every route modeling table that carries a game id."""
    ids = [e["game_id"] for e in staging_report["final_only"]["non_final_games"]]
    if not ids:
        return {"games": 0, "rows_in_modeling_state": 0}
    gids = [_cid("G", "game", x) for x in ids]
    found = {}
    for db in ("rt", *stores):
        for t, _ in _season_tables(con, db):
            if "canonical_game_id" in _cols(con, db, t):
                n = con.execute(f"SELECT count(*) FROM {db}.{t} WHERE canonical_game_id IN (SELECT unnest(?::VARCHAR[]))", [gids]).fetchone()[0]
                if n:
                    found[f"{db}.{t}"] = n
    return {"games": len(ids), "canonical_game_ids": gids, "rows_in_modeling_state": sum(found.values()), "tables_with_rows": found}


def canonical_duplicates(con, db: str = "rt") -> dict:
    checks = {"core.games": ("canonical_game_id",), "fact.team_games": ("canonical_game_id", "canonical_team_id"),
              "fact.player_games": ("player_game_key",), "fact.participation": ("canonical_game_id", "canonical_team_id", "canonical_player_id")}
    out = {t: con.execute(f"SELECT count(*) FROM (SELECT {', '.join(k)} FROM {db}.{t} WHERE {SEASON_FILTER} "
                          f"{'AND canonical_player_id IS NOT NULL' if 'canonical_player_id' in k else ''} GROUP BY ALL HAVING count(*) > 1)").fetchone()[0]
           for t, k in checks.items()}
    out["fact.player_games(game, team, canonical_player_id)"] = con.execute(f"""SELECT count(*) FROM (SELECT canonical_game_id,
        canonical_team_id, canonical_player_id FROM {db}.fact.player_games WHERE {SEASON_FILTER} AND canonical_player_id IS NOT NULL
        GROUP BY ALL HAVING count(*) > 1)""").fetchone()[0]
    amb = con.execute(f"""SELECT canonical_game_id, canonical_team_id, source_player_name, count(*), list(identity_status ORDER BY identity_status)
        FROM {db}.fact.player_games WHERE {SEASON_FILTER} GROUP BY 1, 2, 3 HAVING count(*) > 1 ORDER BY ALL""").fetchall()
    return {"surviving_canonical_duplicates": out, "total": sum(out.values()),
            "same_name_record_groups": [{"canonical_game_id": a, "canonical_team_id": b, "name": c, "records": n, "identity_status": s}
                                        for a, b, c, n, s in amb]}


def season_summary(con, staging: dict, canon_cls: dict) -> dict:
    out = {}
    nf = staging["final_only"]["non_final_games"]
    ta = staging["team_assignment"]
    for season in ROUTE_SEASONS:
        r = {}
        for name, e, table in (("games", "E1", "core.games"), ("team_boxes", "E2", "fact.team_games"), ("player_rows", "E3", "e3v_ref")):
            rel = "e3v_ref" if e == "E3" else f"ref.{table}"
            n = dict(con.execute(f"SELECT status, count(*) FROM {e.lower()}_cause WHERE _season = ? AND cause <> 'FLOATING_POINT_NOISE' GROUP BY 1",
                                 [season]).fetchall())
            c = dict(con.execute(f"SELECT cause, count(*) FROM {e.lower()}_cause WHERE _season = ? GROUP BY 1", [season]).fetchall())
            exp = con.execute(f"SELECT count(*) FROM {rel} WHERE season = ?", [season]).fetchone()[0]
            r[name] = {"expected": exp, "matched": exp - n.get("ONLY_REF", 0), "missing": n.get("ONLY_REF", 0),
                       "extra": n.get("ONLY_ROUTE", 0), "different": n.get("DIFF", 0),
                       "explained_provider_revisions": c.get("EXPLAINED_PROVIDER_DATA_REVISION", 0) + c.get("PROVIDER_NAME_REVISION", 0),
                       "unexplained": c.get("OTHER", 0)}
            if e == "E3":
                r[name]["provider_name_revisions"] = c.get("PROVIDER_NAME_REVISION", 0)
                r[name]["different_substantive"] = con.execute("""SELECT count(*) FROM e3_cause WHERE _season = ? AND status = 'DIFF'
                    AND cause NOT IN ('PROVIDER_NAME_REVISION', 'FLOATING_POINT_NOISE')""", [season]).fetchone()[0]
        r["games"]["non_final_excluded"] = sum(str(g["season"]) == str(season) for g in nf)
        r["player_rows"]["provider_team_repairs"] = sum(str(x["season"]) == str(season) for x in ta["repairs"])
        r["player_rows"]["provider_team_quarantines"] = sum(str(x["season"]) == str(season) for x in ta["quarantines"])
        r["player_rows"]["same_name_records_left_ambiguous_by_policy"] = con.execute(f"""SELECT count(*) FROM (SELECT 1 FROM rt.fact.player_games
            WHERE season = ? GROUP BY canonical_game_id, canonical_team_id, source_player_name HAVING count(*) > 1)""", [season]).fetchone()[0]
        r["player_rows"]["unresolved_identity_defects"] = con.execute("""SELECT count(*) FROM e3_cause WHERE _season = ? AND cause = 'OTHER'
            AND (list_contains(cols, 'canonical_player_id') OR list_contains(cols, 'identity_status') OR status <> 'DIFF')""", [season]).fetchone()[0]
        r["player_rows"]["canonical_unresolved_rows"] = dict(con.execute(f"""SELECT side, count(*) FROM (
            SELECT 'reference' AS side FROM ref.fact.player_games WHERE season = {season} AND canonical_player_id IS NULL UNION ALL
            SELECT 'route' FROM rt.fact.player_games WHERE season = {season} AND canonical_player_id IS NULL) GROUP BY 1""").fetchall())
        r["di_team_season_differences"] = con.execute(f"""SELECT count(*) FROM (SELECT * FROM ref.core.team_seasons WHERE season = {season}) a
            FULL JOIN (SELECT * FROM rt.core.team_seasons WHERE season = {season}) b USING (canonical_team_id, season)
            WHERE a.is_division_one IS DISTINCT FROM b.is_division_one""").fetchone()[0]
        r["false_di_team_seasons"] = [x[0] for x in con.execute(f"""SELECT coalesce(t.school, b.canonical_team_id) FROM rt.core.team_seasons b
            LEFT JOIN ref.core.team_seasons a USING (canonical_team_id, season) LEFT JOIN ref.core.teams t USING (canonical_team_id)
            WHERE b.season = {season} AND b.is_division_one AND NOT coalesce(a.is_division_one, FALSE) ORDER BY 1""").fetchall()]
        r["canonical_game_differences"] = r["games"]["missing"] + r["games"]["extra"]
        r["canonical_player_differences"] = con.execute("""SELECT count(*) FROM e3_cause WHERE _season = ? AND cause = 'OTHER'
            AND (status <> 'DIFF' OR list_contains(cols, 'canonical_player_id'))""", [season]).fetchone()[0]
        out[str(season)] = r
    return out


def boundary_losses(con) -> dict:
    """Reference games missing from the route whose UTC start date is a request-window boundary (first/last day)."""
    edges = {d for s in ROUTE_SEASONS for ep, p in B.plan_base(s) if ep == "games" for d in (p["startDateRange"], p["endDateRange"])}
    rows = con.execute("""SELECT canonical_game_id, strftime(scheduled_tip_utc AT TIME ZONE 'UTC', '%Y-%m-%d') FROM e1_cause
        JOIN ref.core.games USING (canonical_game_id) WHERE status = 'ONLY_REF'""").fetchall()
    return {"window_edge_dates": len(edges), "reference_games_missing_from_route": len(rows),
            "boundary_date_losses": sum(d in edges for _, d in rows)}


# ============================================================================ Stage 3 (E6, E7)
E6_TABLES = {"lab.player_game_labels": ("labels", ("player_game_example_id",)),
             "feat.player_features": ("features", ("player_game_example_id",)),
             "ex.player_game_examples": ("features", ("player_game_example_id",))}


def stage3_comparison(ref_store: Path, rt_store: Path, ab_store: Path | None) -> dict:
    """E6: every column of the three spec tables (a superset of the v1-consumed columns) on the route seasons, every row
    traced through the ablation; every other Stage 3 table of the route seasons summarized the same way (paired on its
    non-float columns). E7: seasons 2014-2023 of every table (approved float rule)."""
    out, e7, other = {}, {}, {}
    for db in ("features", "labels"):
        more = {"ab": Path(ab_store) / f"{db}.duckdb"} if ab_store else {}
        con = _attach(Path(ref_store) / f"{db}.duckdb", Path(rt_store) / f"{db}.duckdb", **more)
        for t, has_season in _season_tables(con, "ref"):
            lo, hi = E7_SEASONS
            e7[t] = {"season_restricted": has_season,
                     **e7_table(con, t, f"WHERE season BETWEEN {lo} AND {hi}" if has_season else "")}
            if not has_season:
                continue
            n = "s3_" + t.replace(".", "_")
            ty = _types(con, "ref", t)
            lcols = [c for c in PROVIDER_LABEL_COLUMNS if c in ty]
            if t in E6_TABLES:
                keys, rels = E6_TABLES[t][1], {d: f"{d}.{t}" for d in ("ref", "rt", "ab")}
                cols = [c for c in ty if c not in keys]          # no lineage exclusion in Stage 3: every column is derived
            else:   # paired on every non-float column (null-safe row hash), floats under the approved rule
                ex = [c for c, d in ty.items() if d not in FLOAT_TYPES]
                keys, cols = ("_row_key",), [c for c, d in ty.items() if d in FLOAT_TYPES] + ["season"]
                rels = {}
                masks = [f'{c} IS NULL' for c in cols if c != 'season']
                lf = f", hash(row({', '.join([c for c in ex if c not in lcols] + masks)})) AS _lf" if lcols else ""
                for d in ("ref", "rt", "ab") if ab_store else ("ref", "rt"):
                    con.execute(f"CREATE OR REPLACE TEMP VIEW {n}_{d}_v AS SELECT hash(row({', '.join(ex + masks)})) "
                                f"AS _row_key{lf}, * FROM {d}.{t} WHERE {SEASON_FILTER}")
                    rels[d] = f"{n}_{d}_v"
            r = rowdiff(con, rels["ref"], rels["rt"], keys, cols, f"{n}_rt", SEASON_FILTER, float_tol=True)
            if ab_store:
                rowdiff(con, rels["ref"], rels["ab"], keys, r["columns_compared"], f"{n}_ab", SEASON_FILTER, float_tol=True)
                r["attribution"] = attribute(con, n, keys)
                if lcols:
                    r["attribution"] = label_revisions(con, n, rels, tuple(keys), r["attribution"], lcols,
                                                       [c for c in cols if c != "season" and ty.get(c) in FLOAT_TYPES])
            (out if t in E6_TABLES else other)[t] = r
        con.close()
    rows = sum(v["only_ref"] + v["only_route"] + v["different"] for v in out.values())
    untraced = sum(v.get("attribution", {}).get("untraced", rows) for v in out.values())
    by = {c: sum(v.get("attribution", {}).get("by_cause", {}).get(c, 0) for v in out.values()) for c in CAUSES}
    other_untraced = sum(v.get("attribution", {}).get("untraced", v["only_ref"] + v["only_route"] + v["different"])
                         for v in other.values())
    by_all = {c: by[c] + sum(v.get("attribution", {}).get("by_cause", {}).get(c, 0) for v in other.values()) for c in CAUSES}
    return {"E6": {"tables": out, "differing_rows": rows, "by_cause": by, "by_cause_all_stage3_tables": by_all, "untraced": untraced,
                   "feature_rows_differing": {k: out["feat.player_features"][k] for k in ("only_ref", "only_route", "different")},
                   "other_stage3_tables_untraced": other_untraced, "pass": untraced == 0 and other_untraced == 0},
            "stage3_other_tables": {"tables": other, "untraced": other_untraced},
            "E7": {"seasons": list(E7_SEASONS), "rule": {"exact": "ids, keys, row counts, integers, strings, booleans, null masks",
                                                         "float": f"|a-b| <= {FLOAT_ABS_TOL} or |a-b| <= {FLOAT_REL_TOL} * max(|a|,|b|)"},
                   "tables": e7, "pass": all(v["pass"] for v in e7.values() if v["season_restricted"])}}


def stage3_identity(store_a: Path, store_b: Path) -> dict:
    """Determinism: every Stage 3 table, every season, build A vs build B under the approved float rule."""
    res = {}
    for db in ("features", "labels"):
        con = _attach(Path(store_a) / f"{db}.duckdb", Path(store_b) / f"{db}.duckdb")
        for t, _ in _season_tables(con, "ref"):
            res[f"{db}:{t}"] = e7_table(con, t, "")
        con.close()
    return {"tables": len(res), "byte_identical": sum(v["byte_identical"] for v in res.values()),
            "failures": {k: v for k, v in res.items() if not v["pass"]}, "pass": all(v["pass"] for v in res.values())}


# ============================================================================ E8
def _flatten_metrics(o, pre=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _flatten_metrics(v, f"{pre}.{k}" if pre else str(k))
    elif isinstance(o, bool) or not isinstance(o, (int, float)):
        return
    else:
        yield pre, float(o)


def gated_metrics(res: dict) -> dict:
    """The values G1-G6 read (lockbox_runner.gates), by name."""
    from cbb_platform.lockbox_runner import TARGETS
    tg, out = res["counts_and_minutes"]["targets"], {}
    for t in TARGETS:
        for kind in ("candidate", "rolling_baseline"):
            for m in ("log_score", "crps"):
                out[f"G1/G2.{t}.{kind}.{m}"] = tg[t][kind].get(m)
    for kind, v in res["gates"]["G1_aggregate"]["aggregate"].items():
        for m, x in v.items():
            out[f"G1.aggregate.{kind}.{m}"] = x
    for kind in ("candidate", "rolling_baseline"):
        for m in ("mae", "rmse"):
            out[f"G3.MIN.{kind}.{m}"] = tg["MIN"][kind].get(m)
    p = res["participation"]
    for kind in ("candidate", "heuristic"):
        for m in ("brier", "log_loss"):
            out[f"G4.{kind}.{m}"] = p[kind].get(m)
    for m in ("calibration_slope", "calibration_in_the_large"):
        out[f"G5.candidate.{m}"] = p["candidate"].get(m)
    pi = res["pricing_integrity"]
    for t, v in pi["counts"].items():
        for m in ("max_row_sum_error", "min_entry", "p_over_monotonicity_violations", "integer_push_max_error",
                  "half_line_max_push", "invalid_fair_prices"):
            out[f"G6.{t}.{m}"] = v.get(m)
    for m in ("max_row_sum_error", "min_entry"):
        out[f"G6.MIN.{m}"] = pi["MIN"].get(m)
    return out


def _score(art: Path, store: Path, work: Path, tag: str) -> dict:
    from cbb_platform import lockbox_runner_validation as V
    run = V.fresh_run(art, Path(store) / "features.duckdb", Path(store) / "labels.duckdb", V.DEV_SEASON, Path(work) / f"e8_{tag}.json")
    return {**run, "result": json.loads((Path(work) / f"e8_{tag}.json").read_text())}


def _compare_runs(a: dict, b: dict) -> dict:
    verdict = lambda r: {k: (v["pass"] if isinstance(v, dict) else v) for k, v in r["gates"].items()}
    ga, gb = gated_metrics(a["result"]), gated_metrics(b["result"])
    gd = {k: abs(ga[k] - gb[k]) for k in ga if ga[k] is not None and gb.get(k) is not None and ga[k] != gb[k]}
    fa = dict(_flatten_metrics({k: a["result"][k] for k in ("counts_and_minutes", "participation", "pricing_integrity")}))
    fb = dict(_flatten_metrics({k: b["result"][k] for k in ("counts_and_minutes", "participation", "pricing_integrity")}))
    alld = {k: abs(fa[k] - fb[k]) for k in fa.keys() & fb.keys() if fa[k] != fb[k] and not (fa[k] != fa[k] and fb[k] != fb[k])}
    return {"fit_calls": {"reference": a["fit_calls"], "other": b["fit_calls"]},
            "result_sha256": {"reference": a["result_sha256"], "other": b["result_sha256"]},
            "row_identity_equal": a["result"]["row_identity_sha256"] == b["result"]["row_identity_sha256"],
            "gates": {"reference": verdict(a["result"]), "other": verdict(b["result"])},
            "gate_verdicts_identical": verdict(a["result"]) == verdict(b["result"]),
            "gated_metric_values": {k: {"reference": ga[k], "other": gb.get(k)} for k in sorted(ga)},
            "gated_metric_differences": dict(sorted(gd.items(), key=lambda kv: -kv[1])),
            "max_gated_metric_difference": max(gd.values(), default=0.0),
            "gated_within_tolerance": all(v <= E8_TOL for v in gd.values()) and len(ga) == len(gb),
            "all_metric_differences_top": dict(sorted(alld.items(), key=lambda kv: -kv[1])[:40]),
            "max_any_metric_difference": max(alld.values(), default=0.0),
            "metric_keys_only_one_side": sorted(fa.keys() ^ fb.keys())}


def end_to_end(ref_store: Path, rt_store: Path, work: Path, ab_store: Path | None = None, rt_store_b: Path | None = None) -> dict:
    """E8: the fit-free runner on 2024-25, evaluation-B artifact (fit on the reference store through 2023-24), scored
    against each Stage 3 store in a fresh process; gated metrics compared at the frozen 1e-3 (spec §5.5)."""
    from cbb_platform import lockbox_runner_validation as V, props_v1_refit as R
    art = Path(work) / "evaluation_B_artifact.json"
    R.save(V.evaluation_artifact(), art)
    ref = _score(art, ref_store, work, "reference")
    rt = _score(art, rt_store, work, "route")
    out = {"artifact_sha256": hashlib.sha256(art.read_bytes()).hexdigest(), "season": V.DEV_SEASON, "tolerance": E8_TOL,
           "route_vs_reference": _compare_runs(ref, rt)}
    out["pass"] = out["route_vs_reference"]["gate_verdicts_identical"] and out["route_vs_reference"]["gated_within_tolerance"]
    if ab_store:
        out["ablation_vs_reference"] = _compare_runs(ref, _score(art, ab_store, work, "ablation"))
    if rt_store_b:
        b = _score(art, rt_store_b, work, "route_b")
        out["route_a_vs_route_b"] = {"result_sha256_equal": rt["result_sha256"] == b["result_sha256"], "fit_calls": b["fit_calls"]}
    return out


def e8_classification(e8: dict, unexplained_canonical: int, untraced_stage3: int, revs: list[dict]) -> str:
    """Architect ruling 2026-10-16. The frozen comparison is reported as measured; its difference is
    EXPLAINED_PROVIDER_DATA_REVISION only if the ablation (route minus exactly the accepted team-box revisions)
    reproduces the reference within the frozen tolerance with identical gate verdicts, every team-box revision passed
    A-D, and nothing else (canonical or Stage 3) is unexplained. NONE when the frozen comparison passes."""
    if e8["pass"]:
        return "NONE"
    ab = e8.get("ablation_vs_reference") or {}
    explained = (bool(revs) and all(r["classification"] == "EXPLAINED_PROVIDER_DATA_REVISION" for r in revs)
                 and ab.get("gate_verdicts_identical") is True and ab.get("gated_within_tolerance") is True
                 and unexplained_canonical == 0 and untraced_stage3 == 0)
    return "EXPLAINED_PROVIDER_DATA_REVISION" if explained else "UNEXPLAINED"


def guard_refusal() -> dict:
    """§5.6: under the current guard 2025-26 is refused by plan and fetch before any client (network) exists."""
    calls, out = [], {}
    original = B._client
    B._client = lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError("client created"))
    try:
        for name, fn in (("plan_base", lambda: B.plan_base(lockbox.LOCKBOX_SEASON)),
                         ("fetch", lambda: B.fetch(lockbox.LOCKBOX_SEASON))):
            try:
                fn()
                out[name] = "NOT_REFUSED"
            except lockbox.LockboxViolation as e:
                out[name] = f"REFUSED: {e}"
    finally:
        B._client = original
    return {"season": lockbox.LOCKBOX_SEASON, **out, "clients_created": len(calls),
            "pass": not calls and all(v.startswith("REFUSED") for v in out.values())}


def determinism() -> dict:
    """§6: two staging passes over the same payloads give byte-identical rows."""
    h = []
    for _ in range(2):
        rows, rep = B.staged_rows(ROUTE_SEASONS)
        h.append(hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())
    return {"staging_rows_sha256": h, "byte_identical": h[0] == h[1], "payload_set_sha256": rep["payload_set_sha256"],
            "staging_report": rep}


# ============================================================================ builds
def full_build(out: Path) -> dict:
    """Route canonical build (scratch) + the unchanged Stage 3 build on it (scratch). Never writes the dev dataset."""
    from cbb_platform.stage3 import build as S3
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    r = B.build(ROUTE_SEASONS, out / "cbb.duckdb", out / "parquet")
    r["identity"] = B.successor_version(r)
    (out / "route_build.json").write_text(json.dumps(r, indent=1, sort_keys=True) + "\n")
    s3 = S3.build(root=out / "stage3", canonical=out / "cbb.duckdb", with_reports=False)
    (out / "route_stage3.json").write_text(json.dumps(s3, indent=1, sort_keys=True) + "\n")
    return {"identity": r["identity"], "staging": r["staging"]}


def ablation_build(route_out: Path, out: Path) -> dict:
    """DIAGNOSTIC ONLY (never the route): the route build with exactly the team boxes classified as provider revisions on
    the route build (team_box_revisions: route-only team boxes of games the reference has) removed from staging."""
    from cbb_platform.stage3 import build as S3
    con = _attach(C.DUCKDB_PATH, Path(route_out) / "cbb.duckdb")
    revs = team_box_revisions(con)
    con.close()
    drop = {(r["cbbd_game_id"], r["canonical_game_id"]) for r in revs}
    cbbd_games = {g for g, _ in drop}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    original = B.staged_rows

    def ablated(*a, **k):
        rows, rep = original(*a, **k)
        before = len(rows["team_game_stats"])
        rows["team_game_stats"] = [t for t in rows["team_game_stats"] if t["gameId"] not in cbbd_games]
        rep["ablation_team_box_rows_removed"] = before - len(rows["team_game_stats"])
        return rows, rep
    B.staged_rows = ablated
    try:
        r = B.build(ROUTE_SEASONS, out / "cbb.duckdb", out / "parquet")
    finally:
        B.staged_rows = original
    (out / "ablation_build.json").write_text(json.dumps({"removed_cbbd_games": sorted(cbbd_games, key=int),
                                                         "staging": r["staging"]}, indent=1, sort_keys=True) + "\n")
    S3.build(root=out / "stage3", canonical=out / "cbb.duckdb", with_reports=False)
    return {"removed_cbbd_games": sorted(cbbd_games, key=int), "team_box_rows_removed": r["staging"]["ablation_team_box_rows_removed"]}


# ============================================================================ the report
def _same_rows(con, a: str, b: str) -> bool:
    return con.execute(f"SELECT (SELECT count(*) FROM (SELECT * FROM {a} EXCEPT ALL SELECT * FROM {b})) + "
                       f"(SELECT count(*) FROM (SELECT * FROM {b} EXCEPT ALL SELECT * FROM {a}))").fetchone()[0] == 0


def full(out: Path, out_b: Path, ab: Path) -> dict:
    """Directive 2026-10-15 over the full development route seasons. Requires full_build(out), full_build(out_b) (an
    independent second build from the same payloads) and ablation_build(out, ab)."""
    from cbb_platform.stage3 import spec
    out, out_b, ab = Path(out), Path(out_b), Path(ab)
    build_a = json.loads((out / "route_build.json").read_text())
    build_b = json.loads((out_b / "route_build.json").read_text())
    staging = build_a["staging"]
    con = _attach(C.DUCKDB_PATH, out / "cbb.duckdb", ab=ab / "cbb.duckdb", rtb=out_b / "cbb.duckdb")
    canon_rt = canonical_differences(con, "rt", "rt")
    canon_ab = canonical_differences(con, "ab", "ab")
    cls = canonical_classification(con)
    names = name_revisions(con)
    revs = team_box_revisions(con)
    canon_b = canonical_differences(con, "rtb", "rtb")
    det_canon = {"route_a_identity": build_a["identity"], "route_b_identity": build_b["identity"],
                 "canonical_content_identical": build_a["identity"] == build_b["identity"],
                 "reconciliation_rows_identical": all(_same_rows(con, f"{e.lower()}_rt", f"{e.lower()}_rtb") for e in canon_rt)}
    dups = canonical_duplicates(con)
    seasons = season_summary(con, staging, cls)
    bnd = boundary_losses(con)
    con.close()
    s3 = stage3_comparison(spec.STORE, out / "stage3", ab / "stage3")
    con = _attach(C.DUCKDB_PATH, out / "cbb.duckdb", feat=out / "stage3" / "features.duckdb", lab=out / "stage3" / "labels.duckdb")
    nonfinal = non_final_in_modeling_state(con, staging, ("feat", "lab"))
    con.close()
    e8 = end_to_end(spec.STORE, out / "stage3", out, ab / "stage3", out_b / "stage3")
    det = {"staging": determinism(), **det_canon, "stage3_route_a_vs_b": stage3_identity(out / "stage3", out_b / "stage3"),
           "fit_free_scoring_identical": e8["route_a_vs_route_b"]["result_sha256_equal"]}
    det["pass"] = (det["staging"]["byte_identical"] and det["canonical_content_identical"] and det["reconciliation_rows_identical"]
                   and det["stage3_route_a_vs_b"]["pass"] and det["fit_free_scoring_identical"])
    unexplained_canon = sum(v["unexplained"] for v in cls.values()) + sum(r["classification"] != "EXPLAINED_PROVIDER_DATA_REVISION" for r in revs) \
        + (0 if names["all_conditions_hold"] else names["rows"])
    fit_calls = [e8["route_vs_reference"]["fit_calls"]["reference"], e8["route_vs_reference"]["fit_calls"]["other"],
                 e8["route_a_vs_route_b"]["fit_calls"], e8["ablation_vs_reference"]["fit_calls"]["other"]]
    e = {k: cls[k]["unexplained"] == 0 for k in cls}
    e["E2"] = e["E2"] and all(r["classification"] == "EXPLAINED_PROVIDER_DATA_REVISION" for r in revs)
    e["E3"] = e["E3"] and names["all_conditions_hold"] and dups["total"] == 0
    verdict = {
        "BOUNDARY_DATE_LOSSES": bnd["boundary_date_losses"],
        "FALSE_DI_CLASSIFICATIONS": sum(len(seasons[s]["false_di_team_seasons"]) for s in seasons),
        "DI_CLASSIFICATION_DIFFERENCES": sum(seasons[s]["di_team_season_differences"] for s in seasons),
        "NON_FINAL_GAMES_EXCLUDED": staging["final_only"]["non_final_games_excluded"],
        "NON_FINAL_GAMES_IN_MODELING_STATE": nonfinal["rows_in_modeling_state"],
        "UNRESOLVED_PLAYER_IDENTITIES": sum(seasons[s]["player_rows"]["unresolved_identity_defects"] for s in seasons),
        "PROVIDER_TEAM_ASSIGNMENT": {k: staging["team_assignment"][k] for k in ("repaired", "quarantined", "unresolved")},
        "CBBD_AUTHORITY": ARCHITECT_RULING["cbbd_authority"],
        "UNRESOLVED_TEAM_ASSIGNMENTS": staging["team_assignment"]["unresolved"],
        "SURVIVING_CANONICAL_DUPLICATES": dups["total"],
        "EXPLAINED_PROVIDER_DATA_REVISIONS": sum(r["classification"] == "EXPLAINED_PROVIDER_DATA_REVISION" for r in revs),
        "EXPLAINED_PROVIDER_LABEL_REVISIONS": s3["E6"]["by_cause_all_stage3_tables"]["EXPLAINED_PROVIDER_LABEL_REVISION"],
        "UNEXPLAINED_CANONICAL_DIFFERENCES": unexplained_canon,
        "UNTRACED_E6_FEATURE_ROWS": s3["E6"]["untraced"],
        **{f"{k}": "PASS" if v else "FAIL" for k, v in e.items()},
        "E6": "PASS" if s3["E6"]["pass"] else "FAIL", "E7": "PASS" if s3["E7"]["pass"] else "FAIL",
        "E8": "PASS" if e8["pass"] else "FAIL",
        "E8_FROZEN_REFERENCE_COMPARISON": "PASS" if e8["pass"] else "FAIL",
        "E8_DIFFERENCE_CLASSIFICATION": e8_classification(e8, unexplained_canon, s3["E6"]["untraced"] + s3["E6"]["other_stage3_tables_untraced"], revs),
        "E8_ABLATION_MAX_GATED_METRIC_DIFFERENCE": e8["ablation_vs_reference"]["max_gated_metric_difference"],
        "E8_GATE_VERDICTS_IDENTICAL": "PASS" if e8["route_vs_reference"]["gate_verdicts_identical"] else "FAIL",
        "E8_MAX_GATED_METRIC_DIFFERENCE": e8["route_vs_reference"]["max_gated_metric_difference"],
        "DETERMINISTIC": "PASS" if det["pass"] else "FAIL",
        "FIT_CALLS": sum(fit_calls), "GUARD_REFUSES_2025_26": guard_refusal()["pass"], "2025_26_TOUCHED": False,
        "PREDICTIVE_SPEC_CHANGED": hashlib.sha256((C.PROJECT_ROOT / FROZEN_ARTIFACT).read_bytes()).hexdigest() != FROZEN_ARTIFACT_SHA256,
    }
    e8_ok = verdict["E8"] == "PASS" or (verdict["E8_DIFFERENCE_CLASSIFICATION"] == "EXPLAINED_PROVIDER_DATA_REVISION"
                                        and verdict["E8_GATE_VERDICTS_IDENTICAL"] == "PASS")
    verdict["CBBD_INGESTION_ROUTE"] = "PASS" if (
        verdict["BOUNDARY_DATE_LOSSES"] == 0 and verdict["FALSE_DI_CLASSIFICATIONS"] == 0
        and verdict["NON_FINAL_GAMES_IN_MODELING_STATE"] == 0 and verdict["UNRESOLVED_PLAYER_IDENTITIES"] == 0
        and verdict["SURVIVING_CANONICAL_DUPLICATES"] == 0 and verdict["UNEXPLAINED_CANONICAL_DIFFERENCES"] == 0
        and verdict["UNRESOLVED_TEAM_ASSIGNMENTS"] == 0
        and verdict["UNTRACED_E6_FEATURE_ROWS"] == 0 and all(verdict[k] == "PASS" for k in ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "DETERMINISTIC"))
        and e8_ok and verdict["FIT_CALLS"] == 0 and not verdict["PREDICTIVE_SPEC_CHANGED"] and verdict["GUARD_REFUSES_2025_26"]) else "FAIL"
    return {"spec": str(SPEC.relative_to(C.PROJECT_ROOT)), "spec_sha256": hashlib.sha256(SPEC.read_bytes()).hexdigest(),
            "directive": "architect 2026-10-15: final CBBD ingestion route correction and development validation; "
                         "architect ruling 2026-10-16: CBBD authority accepted, route closure",
            "architect_ruling": ARCHITECT_RULING,
            "route_version": B.ROUTE_VERSION, "route_module_sha256": hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest(),
            "route_seasons": list(ROUTE_SEASONS), "lockbox_touched": False, "route_build": build_a["identity"],
            "verdict": verdict, "pass": verdict["CBBD_INGESTION_ROUTE"] == "PASS",
            "final_only": {**staging["final_only"], "modeling_state_check": nonfinal},
            "team_assignment": staging["team_assignment"], "duplicates": {**dups, "route_dedupe_collapsed": {
                k: v for k, v in staging.items() if k.endswith("_duplicates_collapsed")}, "same_name_records_staged": staging["same_name_records"]},
            "boundary": bnd, "seasons": seasons, "name_revisions": names, "team_box_revisions": revs,
            "canonical": {"route_vs_reference": canon_rt, "ablation_vs_reference": canon_ab, "classification": cls},
            "stage3": s3, "end_to_end": e8, "determinism": det, "guard": guard_refusal()}


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["full-build"]:
        print(json.dumps(full_build(Path(sys.argv[2])), indent=1, default=str))
    elif cmd == ["ablation-build"]:
        print(json.dumps(ablation_build(Path(sys.argv[2]), Path(sys.argv[3])), indent=1, default=str))
    elif cmd == ["full"]:
        res = full(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        FULL_OUT.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
        print(json.dumps({"written": str(FULL_OUT), "verdict": res["verdict"]}, indent=1, default=str))
    elif sys.argv[1:] == ["smoke"]:
        out = smoke()
        SMOKE_OUT.write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
        r = out["staging_reconciliation"]
        print(json.dumps({f: {k: (len(v) if isinstance(v, list) else v) for k, v in d.items() if k != "columns_compared"}
                          for f, d in r.items() if isinstance(d, dict)}, indent=1), r["pass_exact"])
    else:
        raise SystemExit("usage: smoke | full-build <dir> | ablation-build <route_dir> <dir> | full <dir_a> <dir_b> <ablation_dir>")
