"""Stage 3 section 19: measured feature / label coverage by season and family.

    data/qa/STAGE3_FEATURE_COVERAGE.json   docs/STAGE3_FEATURE_COVERAGE.md
"""
from __future__ import annotations

import json

import duckdb

from cbb_platform import config as C
from cbb_platform.stage3 import registry, spec

OUT_JSON = C.QA / "STAGE3_FEATURE_COVERAGE.json"
OUT_MD = C.PROJECT_ROOT / "docs" / "STAGE3_FEATURE_COVERAGE.md"


def run(features_db, labels_db) -> dict:
    con = duckdb.connect(str(features_db), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute(f"ATTACH '{labels_db}' AS lb (READ_ONLY)")
    def rows(sql):
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    base = rows("""
    SELECT g.season, count(DISTINCT g.game_example_id) AS eligible_games,
           (SELECT count(*) FROM ex.player_game_examples p WHERE p.season = g.season) AS eligible_player_games
    FROM ex.game_examples g GROUP BY 1 ORDER BY 1""")
    lab = {r["season"]: r for r in rows("""
    SELECT season, count(*) AS label_rows,
           avg((participation_status = 'PLAYED')::INT) AS played_rate,
           avg((participation_status = 'DNP_CONFIRMED')::INT) AS dnp_confirmed_rate,
           avg((participation_status = 'UNKNOWN_PARTICIPATION')::INT) AS unknown_participation_rate,
           avg((participation_status = 'NOT_ON_ROSTER')::INT) AS not_on_roster_rate,
           avg(box_row_present::INT) AS box_row_rate,
           avg(CASE WHEN box_row_present THEN pts_identity_ok::INT END) AS pts_identity_ok_rate,
           avg(CASE WHEN box_row_present THEN reb_identity_ok::INT END) AS reb_identity_ok_rate,
           avg(CASE WHEN box_row_present THEN counting_labels_available::INT END) AS counting_labels_available_rate
    FROM lb.lab.player_game_labels GROUP BY 1""")}
    outside = {r["season"]: r for r in rows("""SELECT season, count(*) AS n FROM lb.lab.target_box_rows_outside_universe GROUP BY 1""")}
    unres = {r["season"]: r for r in rows("""SELECT season, count(*) AS n FROM lb.lab.target_box_rows_unresolved GROUP BY 1""")}
    allbox = {r["season"]: r for r in rows("""SELECT season, count(*) FILTER (WHERE box_row_present) AS n FROM lb.lab.player_game_labels GROUP BY 1""")}
    cold = {r["season"]: r for r in rows("""SELECT f.season, avg((cold_start_class <> 'WARM')::INT) AS cold_start_rate,
           avg((cold_start_class = 'COLD_NO_HISTORY')::INT) AS cold_no_history, avg((cold_start_class = 'COLD_TRANSFER_IN')::INT) AS cold_transfer_in,
           avg((cold_start_class = 'COLD_EARLY_SEASON')::INT) AS cold_early_season,
           avg(CASE WHEN l.participation_status = 'PLAYED' THEN (cold_start_class <> 'WARM')::INT END) AS cold_start_rate_among_played
           FROM feat.player_features f JOIN lb.lab.player_game_labels l USING (player_game_example_id) GROUP BY 1""")}
    fam_cov = {}
    for t in registry.FEATURE_TABLES:
        cols = [c[0] for c in con.execute(f"DESCRIBE {t}").fetchall() if c[0].endswith("_status")]
        for sc in cols:
            fam_cov[sc] = {r["season"]: r for r in rows(f"""SELECT season, count(*) AS n, avg(({sc} = 'OK')::INT) AS ok_rate,
                {', '.join(f"avg(({sc} = '{c}')::INT) AS {c.lower()}" for c in spec.MISSING_CODES)} FROM {t} GROUP BY 1""")}
    # Stage 3.1 player-pool misses (after remediation) + status of the 20 pre-remediation misses
    import csv as _csv
    miss_rows = rows("SELECT * EXCLUDE (prediction_cutoff_utc, scheduled_tip_utc) FROM lb.lab.player_pool_misses ORDER BY ALL")
    with (C.QA / "player_pool_misses_after.csv").open("w", newline="") as fh:
        # explicit Unix line endings (csv's default is \r\n, which git diff --check reports as trailing whitespace)
        w = _csv.DictWriter(fh, fieldnames=list(miss_rows[0]) if miss_rows else ["season"], lineterminator="\n")
        w.writeheader()
        w.writerows(miss_rows)
    miss_by = {}
    for m in miss_rows:
        miss_by.setdefault(m["season"], {}).setdefault(m["miss_class"], 0)
        miss_by[m["season"]][m["miss_class"]] += 1
    pre = C.QA / "player_pool_misses.csv"
    if pre.exists():
        old20 = list(_csv.DictReader(pre.open()))
        # resolve by (game source id, player): canonical player may have changed (identity repair) -> match by team/game/player id
        for r in old20:
            r["in_pool_after_stage3_1"] = bool(con.execute("""SELECT count(*) FROM ex.player_game_examples x
                JOIN lb.lab.player_game_labels l USING (player_game_example_id)
                WHERE l.box_row_present AND x.canonical_player_id = ? AND x.canonical_game_id = 'G-' || substr(sha256('CBBD|game|' || ?), 1, 16)""",
                [r["canonical_player_id"], r["game"]]).fetchone()[0])
        with pre.open("w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(old20[0]))
            w.writeheader()
            w.writerows(old20)
    per_season = []
    for b in base:
        s = b["season"]
        L = lab.get(s, {})
        per_season.append({
            **b, **{k: v for k, v in L.items() if k != "season"},
            "target_box_rows_outside_pregame_universe": outside.get(s, {}).get("n", 0),
            "identity_unresolved_box_rows": unres.get(s, {}).get("n", 0),
            "identity_unresolved_rate": (unres.get(s, {}).get("n", 0) / (unres.get(s, {}).get("n", 0) + allbox.get(s, {}).get("n", 0)))
                                        if allbox.get(s) else None,
            **{k: v for k, v in cold.get(s, {}).items() if k != "season"},
            "feature_ok_rate": {sc: d.get(s, {}).get("ok_rate") for sc, d in fam_cov.items()},
            "lineup_feature_availability": fam_cov["lu_status"].get(s, {}).get("ok_rate"),
            "shot_location_availability_team": fam_cov["loc_team_status"].get(s, {}).get("ok_rate"),
            "shot_location_availability_player": fam_cov["loc_player_status"].get(s, {}).get("ok_rate")})
    con.close()
    summary = {
        "game_examples": sum(r["eligible_games"] for r in per_season),
        "player_game_examples": sum(r["eligible_player_games"] for r in per_season),
        "label_rows": sum(r.get("label_rows", 0) for r in per_season),
        "label_rows_with_box": sum(allbox.get(r["season"], {}).get("n", 0) for r in per_season),
        "player_pool_misses_after": len(miss_rows),
        "player_pool_misses_by_class": {k: sum(1 for m in miss_rows if m["miss_class"] == k) for k in sorted({m["miss_class"] for m in miss_rows})},
        "player_pool_misses_by_season": {str(k): v for k, v in sorted(miss_by.items())},
    }
    result = {"version": spec.STAGE3_VERSION, "upstream_dataset": spec.UPSTREAM_DATASET, "summary": summary,
              "by_season": per_season, "family_missingness": {sc: {str(k): v for k, v in d.items()} for sc, d in fam_cov.items()}}
    OUT_JSON.write_text(json.dumps(result, indent=1, default=str))
    OUT_MD.write_text(render(result))
    return result


def render(r: dict) -> str:
    f = lambda v: "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
    L = ["# Stage 3 feature coverage", "",
         "Rendered from `data/qa/STAGE3_FEATURE_COVERAGE.json` by `cbb_platform/stage3/coverage.py`. Upstream dataset "
         f"`{r['upstream_dataset']}`; modeling seasons 2013-14..2024-25; lockbox 2025-26 untouched.", "",
         f"Totals: {r['summary']}", "",
         "## Universe, labels, participation, cold start", "",
         "| season | games | player-games | played | DNP confirmed | unknown | not on roster | box row | identity unresolved | outside universe | cold start | cold (played) | no history | transfer in | early season |",
         "|---|" + "---|" * 15]
    for s in r["by_season"]:
        L.append(f"| {s['season']} | {s['eligible_games']} | {s['eligible_player_games']} | {f(s.get('played_rate'))} | "
                 f"{f(s.get('dnp_confirmed_rate'))} | {f(s.get('unknown_participation_rate'))} | {f(s.get('not_on_roster_rate'))} | "
                 f"{f(s.get('box_row_rate'))} | {f(s.get('identity_unresolved_rate'))} | {s['target_box_rows_outside_pregame_universe']} | "
                 f"{f(s.get('cold_start_rate'))} | {f(s.get('cold_start_rate_among_played'))} | {f(s.get('cold_no_history'))} | "
                 f"{f(s.get('cold_transfer_in'))} | {f(s.get('cold_early_season'))} |")
    fams = list(r["by_season"][0]["feature_ok_rate"])
    L += ["", "## Feature availability (share of rows with status OK) by family", "",
          "| season | " + " | ".join(fams) + " |", "|---|" + "---|" * len(fams)]
    for s in r["by_season"]:
        L.append(f"| {s['season']} | " + " | ".join(f(s["feature_ok_rate"][k]) for k in fams) + " |")
    L += ["", "Non-OK statuses are semantic (NO_PRIOR_HISTORY, INSUFFICIENT_SAMPLE, SOURCE_NOT_AVAILABLE, ...); per-code "
          "rates are in the JSON (`family_missingness`). Nothing is imputed."]
    return "\n".join(L) + "\n"
