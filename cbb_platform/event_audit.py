"""Stage 2M + 2O -- measured event-data coverage and onFloor/substitution consistency (offline; reads the
built database and the request log).

    .venv/bin/python -m cbb_platform.event_audit

Writes (every number measured from canonical tables / the request log; nothing estimated):
    data/qa/cbbd_event_coverage_by_season.csv     one row per development season, all families
    data/qa/cbbd_pbp_quality_by_season.csv
    data/qa/cbbd_onfloor_quality_by_season.csv
    data/qa/cbbd_shot_quality_by_season.csv
    data/qa/cbbd_substitution_quality_by_season.csv
    data/qa/cbbd_lineup_quality_by_season.csv
    data/qa/cbbd_roster_quality_by_season.csv
    data/qa/onfloor_substitution_consistency_by_season.csv
    data/qa/cbbd_event_coverage_by_season.json    the same, plus identity/defect/API summaries
                                                  (folded into data/qa/COVERAGE_AUDIT.json by coverage.py)

A failed request is never counted as "no source data": games are only "tested" when the request that would
have returned them succeeded.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta

import duckdb

from cbb_platform import config as C

SEASONS = list(range(2005, 2026))
OUT_JSON = C.QA / "cbbd_event_coverage_by_season.json"
ELAPSED = """(CASE WHEN {p} <= 2 THEN ({p} - 1) * 1200 + 1200 - {s} ELSE 2400 + ({p} - 3) * 300 + 300 - {s} END)"""


def _rows(con, sql, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _write(name: str, rows: list[dict]) -> None:
    path = C.QA / name
    cols = list(rows[0].keys()) if rows else ["season"]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})


def request_log_tables(con) -> None:
    """Successful / failed requests from the append-only log (latest outcome per request)."""
    from cbb_platform.cbbd_client import REQUEST_LOG
    latest = {}
    for line in REQUEST_LOG.read_text().splitlines() if REQUEST_LOG.exists() else []:
        e = json.loads(line)
        prev = latest.get(e["request_key"])
        if prev is None or prev["status"] not in ("STORED", "ALREADY_STORED"):
            latest[e["request_key"]] = e
    rows = [(e["endpoint"], json.dumps(e["params"], sort_keys=True), e.get("season"), e["status"],
             bool(e.get("empty")), e.get("retries", 0), e.get("http_status"), e.get("purpose")) for e in latest.values()]
    con.execute("""CREATE OR REPLACE TEMP TABLE req (endpoint VARCHAR, params VARCHAR, season INTEGER, status VARCHAR,
                   empty BOOLEAN, retries INTEGER, http_status INTEGER, purpose VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO req VALUES (?,?,?,?,?,?,?,?)", rows)
    # games "tested" for PBP: a successful /plays/game for the game, or successful /plays/date for BOTH the UTC
    # tip date and the following date (/plays/date places a game on either; measured in Stage 2B)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ok_dates AS SELECT CAST(json_extract_string(params, '$.date') AS DATE) AS d
        FROM req WHERE endpoint = 'plays/date' AND status IN ('STORED', 'ALREADY_STORED');
    CREATE OR REPLACE TEMP TABLE ok_games AS SELECT CAST(json_extract_string(params, '$.gameId') AS BIGINT) AS gid, endpoint
        FROM req WHERE endpoint IN ('plays/game', 'lineups/game', 'substitutions/game') AND status IN ('STORED', 'ALREADY_STORED');
    CREATE OR REPLACE TEMP TABLE ok_sub_teams AS SELECT CAST(json_extract_string(params, '$.season') AS INTEGER) AS season,
        json_extract_string(params, '$.team') AS team FROM req WHERE endpoint = 'substitutions/team' AND status IN ('STORED', 'ALREADY_STORED');
    CREATE OR REPLACE TEMP TABLE g AS
    SELECT g.*, CAST(replace(g.source_record_key, 'id=', '') AS BIGINT) AS gid,
           CAST(coalesce(g.scheduled_tip_utc, CAST(g.game_date_et AS TIMESTAMPTZ)) AS DATE) AS utc_d,
           th.school AS home_school, ta.school AS away_school
    FROM core.games g JOIN core.teams th ON th.canonical_team_id = g.home_team_id JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
    WHERE g.season BETWEEN 2005 AND 2025;
    CREATE OR REPLACE TEMP TABLE g_tested AS
    SELECT g.canonical_game_id, g.season,
           (EXISTS (SELECT 1 FROM ok_dates WHERE d = g.utc_d) AND EXISTS (SELECT 1 FROM ok_dates WHERE d = g.utc_d + 1))
           OR EXISTS (SELECT 1 FROM ok_games o WHERE o.gid = g.gid AND o.endpoint = 'plays/game') AS pbp_tested,
           EXISTS (SELECT 1 FROM ok_games o WHERE o.gid = g.gid AND o.endpoint = 'lineups/game') AS lineup_tested,
           (EXISTS (SELECT 1 FROM ok_sub_teams t WHERE t.season = g.season AND t.team = g.home_school)
            AND EXISTS (SELECT 1 FROM ok_sub_teams t WHERE t.season = g.season AND t.team = g.away_school))
           OR EXISTS (SELECT 1 FROM ok_games o WHERE o.gid = g.gid AND o.endpoint = 'substitutions/game') AS subs_tested
    FROM g;
    """)


def pbp_quality(con) -> list[dict]:
    return _rows(con, """
    SELECT g.season, count(*) AS canonical_games, count(*) FILTER (WHERE g.game_universe = 'DI_VS_DI') AS di_vs_di_games,
           count(*) FILTER (WHERE g.has_team_box_score) AS games_with_team_box,
           count(*) FILTER (WHERE g.has_player_box_score) AS games_with_player_box,
           count(*) FILTER (WHERE t.pbp_tested) AS games_tested_pbp,
           count(*) FILTER (WHERE r.canonical_game_id IS NOT NULL) AS games_with_pbp,
           count(*) FILTER (WHERE t.pbp_tested AND r.canonical_game_id IS NULL) AS games_tested_without_pbp,
           count(*) FILTER (WHERE g.game_universe = 'DI_VS_DI' AND r.canonical_game_id IS NOT NULL) AS di_vs_di_games_with_pbp,
           coalesce(sum(r.plays), 0) AS plays,
           count(*) FILTER (WHERE r.coverage_quality IN ('RECONCILED', 'RECONCILED_WITH_SCORE_DECREASES')) AS games_score_reconciled,
           count(*) FILTER (WHERE r.coverage_quality = 'FINAL_SCORE_MISMATCH') AS games_final_score_mismatch,
           count(*) FILTER (WHERE r.coverage_quality = 'INCOMPLETE_PERIODS') AS games_incomplete_periods,
           count(*) FILTER (WHERE r.coverage_quality = 'NO_OFFICIAL_SCORE') AS games_no_official_score,
           count(*) FILTER (WHERE r.score_decreases > 0) AS games_with_score_decreases,
           count(*) FILTER (WHERE r.increment_mismatches > 0) AS games_with_increment_mismatches,
           count(*) FILTER (WHERE r.missing_terminal) AS games_missing_terminal,
           count(*) FILTER (WHERE r.clock_order_violations > 0) AS games_with_clock_order_violations,
           avg(r.period_clock_complete_share) AS period_clock_completeness
    FROM g LEFT JOIN g_tested t USING (canonical_game_id) LEFT JOIN qa.pbp_game_reconciliation r USING (canonical_game_id)
    GROUP BY 1 ORDER BY 1""")


def onfloor_quality(con) -> list[dict]:
    from cbb_platform.event_metrics import ONFLOOR_CLASSES
    cls = ", ".join(f"count(*) FILTER (WHERE on_floor_quality = '{c}') AS {c.lower()}" for c in ONFLOOR_CLASSES)
    return _rows(con, f"""
    SELECT s AS season, coalesce(n, 0) AS plays, coalesce(n_of, 0) AS plays_with_onfloor,
           coalesce(games_of, 0) AS games_with_onfloor,
           n_of / nullif(n, 0) AS onfloor_play_rate, valid_5v5 / nullif(n_of, 0) AS valid_5v5_rate,
           more_than_5 / nullif(n_of, 0) AS invalid_gt5_rate,
           (less_than_5 + missing_home + missing_away) / nullif(n_of, 0) AS invalid_lt5_rate,
           unresolved_player / nullif(n_of, 0) AS unresolved_id_rate,
           (duplicate_player + player_on_both_teams + other_invalid) / nullif(n_of, 0) AS other_invalid_rate,
           {", ".join(c.lower() for c in ONFLOOR_CLASSES)}
    FROM range(2005, 2026) r(s) LEFT JOIN (
        SELECT season, count(*) AS n, count(*) FILTER (WHERE on_floor_quality <> 'NO_ONFLOOR') AS n_of,
               count(DISTINCT canonical_game_id) FILTER (WHERE on_floor_quality <> 'NO_ONFLOOR') AS games_of, {cls}
        FROM fact.plays GROUP BY 1) p ON p.season = r.s ORDER BY 1""")


def shot_quality(con) -> list[dict]:
    return _rows(con, """
    WITH sp AS (SELECT p.season, p.canonical_game_id, p.source_play_id, s.source_play_id IS NOT NULL AS has_shot
                FROM fact.plays p LEFT JOIN fact.shots s USING (canonical_game_id, source_play_id) WHERE p.shooting_play),
    s AS (SELECT season, count(*) AS shots,
                 count(*) FILTER (WHERE shooter_source_athlete_id IS NOT NULL) AS with_shooter,
                 count(*) FILTER (WHERE shooter_player_id IS NOT NULL) AS shooter_resolved,
                 count(*) FILTER (WHERE assisted) AS assisted, count(*) FILTER (WHERE assisted AND assister_source_athlete_id IS NOT NULL) AS assisted_with_assister,
                 count(*) FILTER (WHERE assisted AND assister_player_id IS NOT NULL) AS assister_resolved,
                 count(*) FILTER (WHERE shot_range IS NOT NULL) AS with_range,
                 count(*) FILTER (WHERE shot_range <> 'free_throw') AS fg_shots,
                 count(*) FILTER (WHERE shot_range <> 'free_throw' AND location_x IS NOT NULL AND location_y IS NOT NULL) AS fg_with_xy,
                 count(*) FILTER (WHERE location_valid = FALSE) AS invalid_xy,
                 count(*) FILTER (WHERE shooter_on_team_floor IS NOT NULL) AS floor_checkable,
                 count(*) FILTER (WHERE shooter_on_team_floor) AS shooter_on_floor
          FROM fact.shots GROUP BY 1),
    rec AS (  -- field-goal attempts in shotInfo vs team box FGA, per team-game, games with PBP only
        SELECT t.season, count(*) AS team_games_compared, count(*) FILTER (WHERE x.fga = t.fga) AS fga_match,
               count(*) FILTER (WHERE x.fgm = t.fgm) AS fgm_match
        FROM (SELECT canonical_game_id, canonical_team_id, count(*) FILTER (WHERE shot_range <> 'free_throw') AS fga,
                     count(*) FILTER (WHERE shot_range <> 'free_throw' AND made) AS fgm
              FROM fact.shots WHERE canonical_team_id IS NOT NULL GROUP BY 1, 2) x
        JOIN fact.team_games t USING (canonical_game_id, canonical_team_id) GROUP BY 1)
    SELECT r.s AS season, coalesce(a.shooting_plays, 0) AS shooting_plays, coalesce(a.with_shot, 0) AS shooting_plays_with_shotinfo,
           a.with_shot / nullif(a.shooting_plays, 0) AS shotinfo_rate,
           coalesce(s.shots, 0) AS shots, s.with_shooter / nullif(s.shots, 0) AS shooter_coverage,
           s.shooter_resolved / nullif(s.shots, 0) AS shooter_resolution,
           s.assisted_with_assister / nullif(s.assisted, 0) AS assister_coverage_given_assisted,
           s.assister_resolved / nullif(s.assisted, 0) AS assister_resolution_given_assisted,
           s.with_range / nullif(s.shots, 0) AS range_coverage,
           s.fg_with_xy / nullif(s.fg_shots, 0) AS xy_coverage_field_goals, coalesce(s.invalid_xy, 0) AS invalid_coordinates,
           s.shooter_on_floor / nullif(s.floor_checkable, 0) AS shooter_on_floor_rate_when_floor_valid,
           rec.team_games_compared, rec.fga_match / nullif(rec.team_games_compared, 0) AS fga_vs_box_match_rate,
           rec.fgm_match / nullif(rec.team_games_compared, 0) AS fgm_vs_box_match_rate
    FROM range(2005, 2026) r(s)
    LEFT JOIN (SELECT season, count(*) AS shooting_plays, count(*) FILTER (WHERE has_shot) AS with_shot FROM sp GROUP BY 1) a ON a.season = r.s
    LEFT JOIN s ON s.season = r.s LEFT JOIN rec ON rec.season = r.s ORDER BY 1""")


def substitution_quality(con) -> list[dict]:
    return _rows(con, """
    WITH s AS (SELECT season, count(*) AS rows_, count(DISTINCT canonical_game_id) AS games,
                      count(*) FILTER (WHERE timing_complete) AS timing_complete,
                      count(*) FILTER (WHERE canonical_player_id IS NOT NULL) AS resolved
               FROM fact.substitutions WHERE season IS NOT NULL GROUP BY 1),
    both_teams AS (SELECT season, count(*) AS games_both_teams FROM (
        SELECT season, canonical_game_id FROM fact.substitutions WHERE season IS NOT NULL
        GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) = 2) GROUP BY 1),
    pr AS (SELECT g.season, count(*) AS events, count(*) FILTER (WHERE NOT ambiguous_flag) AS unique_pairs,
                  count(*) FILTER (WHERE pairing_method = 'AMBIGUOUS_MULTI_SWAP') AS ambiguous_multi,
                  count(*) FILTER (WHERE pairing_method = 'UNBALANCED') AS unbalanced
           FROM fact.substitution_pairs p JOIN core.games g USING (canonical_game_id) GROUP BY 1),
    t AS (SELECT season, count(*) FILTER (WHERE subs_tested) AS games_tested FROM g_tested GROUP BY 1)
    SELECT r.s AS season, coalesce(t.games_tested, 0) AS games_tested, coalesce(s.games, 0) AS games_with_substitutions,
           coalesce(b.games_both_teams, 0) AS games_with_both_teams_substitutions, coalesce(s.rows_, 0) AS substitution_rows,
           s.timing_complete / nullif(s.rows_, 0) AS timing_completeness, s.resolved / nullif(s.rows_, 0) AS id_resolution,
           coalesce(pr.events, 0) AS substitution_instants, pr.unique_pairs / nullif(pr.events, 0) AS unique_pair_rate,
           coalesce(pr.ambiguous_multi, 0) AS ambiguous_multi_swaps, coalesce(pr.unbalanced, 0) AS unbalanced_instants
    FROM range(2005, 2026) r(s) LEFT JOIN s ON s.season = r.s LEFT JOIN both_teams b ON b.season = r.s
    LEFT JOIN pr ON pr.season = r.s LEFT JOIN t ON t.season = r.s ORDER BY 1""")


def lineup_quality(con) -> list[dict]:
    return _rows(con, """
    WITH v AS (SELECT season, count(DISTINCT canonical_game_id) AS games, count(*) AS units FROM fact.lineup_stats GROUP BY 1),
    q AS (SELECT g.season, count(*) AS invalid FROM qa.lineup_units_quarantine x JOIN core.games g USING (canonical_game_id) GROUP BY 1),
    tg AS (SELECT l.season, l.canonical_game_id, l.canonical_team_id, sum(l.total_seconds) AS secs, sum(l.team_possessions) AS poss
           FROM fact.lineup_stats l GROUP BY 1, 2, 3),
    cov AS (SELECT tg.season, count(*) AS team_games,
                   avg(tg.secs / nullif(t.game_minutes * 60.0, 0)) AS mean_seconds_coverage,
                   count(*) FILTER (WHERE abs(tg.secs - t.game_minutes * 60) <= 1) AS team_games_seconds_complete,
                   avg(tg.poss / nullif(t.possessions_source, 0)) AS mean_possessions_ratio_vs_box
            FROM tg JOIN fact.team_games t USING (canonical_game_id, canonical_team_id) GROUP BY 1),
    t AS (SELECT season, count(*) FILTER (WHERE lineup_tested) AS games_tested FROM g_tested GROUP BY 1)
    SELECT r.s AS season, coalesce(t.games_tested, 0) AS games_tested, coalesce(v.games, 0) AS games_with_lineups,
           coalesce(v.units, 0) AS valid_five_player_units, coalesce(q.invalid, 0) AS invalid_units,
           cov.team_games AS team_games_with_box, cov.mean_seconds_coverage, cov.team_games_seconds_complete / nullif(cov.team_games, 0) AS seconds_complete_rate,
           cov.mean_possessions_ratio_vs_box
    FROM range(2005, 2026) r(s) LEFT JOIN v ON v.season = r.s LEFT JOIN q ON q.season = r.s
    LEFT JOIN cov ON cov.season = r.s LEFT JOIN t ON t.season = r.s ORDER BY 1""")


def roster_quality(con) -> list[dict]:
    return _rows(con, """
    WITH ro AS (SELECT DISTINCT canonical_player_id, canonical_team_id, season FROM core.rosters),
    ros AS (SELECT DISTINCT canonical_player_id, season FROM core.rosters),
    r AS (SELECT season, count(DISTINCT canonical_team_id) AS team_seasons, count(*) AS roster_rows,
                 count(DISTINCT canonical_player_id) AS athletes FROM core.rosters GROUP BY 1),
    d1 AS (SELECT ts.season, count(*) AS d1_team_seasons, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM ro WHERE ro.canonical_team_id = ts.canonical_team_id
                  AND ro.season = ts.season)) AS d1_with_roster FROM core.team_seasons ts WHERE ts.is_division_one GROUP BY 1),
    box AS (SELECT p.season, count(*) AS box_rows, count(*) FILTER (WHERE ro.canonical_player_id IS NOT NULL) AS joined
            FROM fact.player_games p LEFT JOIN ro USING (canonical_player_id, canonical_team_id, season)
            WHERE p.canonical_player_id IS NOT NULL GROUP BY 1),
    pbp AS (SELECT pp.season, count(*) AS n, count(*) FILTER (WHERE ros.canonical_player_id IS NOT NULL) AS joined FROM (
               SELECT p.season, x.canonical_player_id FROM fact.play_participants x JOIN fact.plays p USING (canonical_game_id, source_play_id)) pp
            LEFT JOIN ros USING (canonical_player_id, season) GROUP BY 1),
    flo AS (SELECT p.season, count(*) AS n, count(*) FILTER (WHERE ro.canonical_player_id IS NOT NULL) AS joined
            FROM fact.play_on_floor f JOIN fact.plays p USING (canonical_game_id, source_play_id)
            LEFT JOIN ro ON ro.canonical_player_id = f.canonical_player_id AND ro.canonical_team_id = f.canonical_team_id AND ro.season = p.season
            GROUP BY 1),
    sub AS (SELECT s.season, count(*) AS n, count(*) FILTER (WHERE ro.canonical_player_id IS NOT NULL) AS joined
            FROM fact.substitutions s LEFT JOIN ro USING (canonical_player_id, canonical_team_id, season) WHERE s.season IS NOT NULL GROUP BY 1),
    lin AS (SELECT l.season, count(*) AS n, count(*) FILTER (WHERE ro.canonical_player_id IS NOT NULL) AS joined
            FROM (SELECT season, canonical_team_id, unnest(player_ids) AS canonical_player_id FROM fact.lineup_stats) l
            LEFT JOIN ro USING (canonical_player_id, canonical_team_id, season) GROUP BY 1)
    SELECT x.s AS season, coalesce(r.team_seasons, 0) AS team_seasons_with_roster, coalesce(r.roster_rows, 0) AS roster_rows,
           coalesce(r.athletes, 0) AS roster_athletes, d1.d1_team_seasons, d1.d1_with_roster,
           box.joined / nullif(box.box_rows, 0) AS box_player_roster_join_rate,
           pbp.joined / nullif(pbp.n, 0) AS pbp_participant_roster_join_rate,
           flo.joined / nullif(flo.n, 0) AS onfloor_player_roster_join_rate,
           sub.joined / nullif(sub.n, 0) AS substitution_player_roster_join_rate,
           lin.joined / nullif(lin.n, 0) AS lineup_player_roster_join_rate
    FROM range(2005, 2026) x(s) LEFT JOIN r ON r.season = x.s LEFT JOIN d1 ON d1.season = x.s LEFT JOIN box ON box.season = x.s
    LEFT JOIN pbp ON pbp.season = x.s LEFT JOIN flo ON flo.season = x.s LEFT JOIN sub ON sub.season = x.s
    LEFT JOIN lin ON lin.season = x.s ORDER BY 1""")


def consistency(con) -> list[dict]:
    """2M: candidate floor sets from substitution stints vs CBBD onFloor, per play and side.
    Stint rule (Stage 1, cbbd_validate.floor_from_substitutions): an athlete is on the floor at elapsed t when
    t_in < t <= t_out; at a period's first instant (t = period start) t_in <= t is used instead."""
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE stints AS
    SELECT canonical_game_id, canonical_team_id, source_athlete_id, sub_in_period AS pin,
           {ELAPSED.format(p='sub_in_period', s='sub_in_seconds_remaining')} AS t_in,
           {ELAPSED.format(p='sub_out_period', s='sub_out_seconds_remaining')} AS t_out
    FROM fact.substitutions WHERE timing_complete;
    CREATE OR REPLACE TEMP TABLE sub_games AS
    SELECT canonical_game_id FROM fact.substitutions GROUP BY 1 HAVING count(DISTINCT canonical_team_id) = 2;
    CREATE OR REPLACE TEMP TABLE cp AS
    SELECT p.canonical_game_id, p.source_play_id, p.season, p.on_floor_quality,
           {ELAPSED.format(p='p.period', s='p.seconds_remaining')} AS t,
           p.seconds_remaining = CASE WHEN p.period <= 2 THEN 1200 ELSE 300 END AS period_start
    FROM fact.plays p JOIN sub_games USING (canonical_game_id)
    WHERE p.on_floor_quality <> 'NO_ONFLOOR' AND p.period IS NOT NULL AND p.seconds_remaining IS NOT NULL;
    CREATE OR REPLACE TEMP TABLE src_side AS
    SELECT f.canonical_game_id, f.source_play_id, f.canonical_team_id, list_sort(list(f.source_athlete_id)) AS s_ids,
           count(*) AS s_n, bool_or(f.canonical_player_id IS NULL) AS s_unresolved
    FROM fact.play_on_floor f JOIN cp USING (canonical_game_id, source_play_id)
    WHERE f.canonical_team_id IS NOT NULL GROUP BY 1, 2, 3;
    CREATE OR REPLACE TEMP TABLE der_side AS
    SELECT cp.canonical_game_id, cp.source_play_id, s.canonical_team_id, list_sort(list(DISTINCT s.source_athlete_id)) AS d_ids,
           count(DISTINCT s.source_athlete_id) AS d_n
    FROM cp JOIN stints s ON s.canonical_game_id = cp.canonical_game_id
     AND ((s.t_in < cp.t AND cp.t <= s.t_out) OR (cp.period_start AND s.t_in = cp.t))
    GROUP BY 1, 2, 3;
    CREATE OR REPLACE TEMP TABLE side_cmp AS
    SELECT x.canonical_game_id, x.source_play_id, x.canonical_team_id, x.s_n, x.s_unresolved, coalesce(d.d_n, 0) AS d_n,
           greatest(len(list_filter(x.s_ids, i -> NOT list_contains(coalesce(d.d_ids, []), i))),
                    len(list_filter(coalesce(d.d_ids, []), i -> NOT list_contains(x.s_ids, i)))) AS mismatch
    FROM src_side x LEFT JOIN der_side d USING (canonical_game_id, source_play_id, canonical_team_id);
    CREATE OR REPLACE TEMP TABLE play_cmp AS
    SELECT c.canonical_game_id, c.source_play_id, cp.season, max(c.mismatch) AS mismatch, count(*) AS sides,
           bool_or(c.s_n > 5) AS src_gt5, bool_or(c.s_n < 5) AS src_lt5, bool_or(c.s_unresolved) AS unresolved,
           bool_or(c.d_n < 5) AS gap, bool_or(c.d_n > 5) AS impossible
    FROM side_cmp c JOIN cp USING (canonical_game_id, source_play_id) GROUP BY 1, 2, 3;
    """)
    rows = _rows(con, """
    SELECT r.s AS season, coalesce(count(p.source_play_id), 0) AS comparable_plays,
           count(*) FILTER (WHERE p.sides = 2 AND p.mismatch = 0) AS exact_agreement,
           count(*) FILTER (WHERE p.mismatch = 1) AS one_player_mismatch,
           count(*) FILTER (WHERE p.mismatch >= 2) AS multi_player_mismatch,
           count(*) FILTER (WHERE p.src_gt5) AS source_gt5_states, count(*) FILTER (WHERE p.src_lt5) AS source_lt5_states,
           count(*) FILTER (WHERE p.unresolved) AS unresolved_identity,
           count(*) FILTER (WHERE p.gap) AS substitution_gaps, count(*) FILTER (WHERE p.impossible) AS impossible_transitions,
           count(DISTINCT p.canonical_game_id) AS games
    FROM range(2005, 2026) r(s) LEFT JOIN play_cmp p ON p.season = r.s GROUP BY 1 ORDER BY 1""")
    for r in rows:
        r["exact_agreement_rate"] = r["exact_agreement"] / r["comparable_plays"] if r["comparable_plays"] else None
    return rows


def identity_summary(con) -> dict:
    q = lambda sql: con.execute(sql).fetchone()
    out = {}
    for name, sql in {
        "pbp_participants": "SELECT count(*), count(canonical_player_id), count(DISTINCT source_athlete_id), count(DISTINCT source_athlete_id) FILTER (WHERE canonical_player_id IS NULL) FROM fact.play_participants",
        "onfloor": "SELECT count(*), count(canonical_player_id), count(DISTINCT source_athlete_id), count(DISTINCT source_athlete_id) FILTER (WHERE canonical_player_id IS NULL) FROM fact.play_on_floor",
        "shot_shooters": "SELECT count(shooter_source_athlete_id), count(shooter_player_id), count(DISTINCT shooter_source_athlete_id), count(DISTINCT shooter_source_athlete_id) FILTER (WHERE shooter_player_id IS NULL) FROM fact.shots",
        "substitutions": "SELECT count(*), count(canonical_player_id), count(DISTINCT source_athlete_id), count(DISTINCT source_athlete_id) FILTER (WHERE canonical_player_id IS NULL) FROM fact.substitutions",
        "lineups": "SELECT count(*), count(*) FILTER (WHERE x NOT LIKE 'CBBD_ATHLETE:%'), count(DISTINCT x), count(DISTINCT x) FILTER (WHERE x LIKE 'CBBD_ATHLETE:%') FROM (SELECT unnest(player_ids) x FROM fact.lineup_stats)",
    }.items():
        n, res, ids, unres = q(sql)
        out[name] = {"rows": n, "rows_resolved": res, "mapping_rate": round(res / n, 6) if n else None,
                     "distinct_athlete_ids": ids, "distinct_unresolved_athlete_ids": unres}
    # contradictions to Stage 1: one athlete id with materially different names across sources
    out["athlete_ids_with_conflicting_roster_names"] = q("""
        SELECT count(*) FROM (SELECT source_athlete_id FROM core.rosters GROUP BY 1
                              HAVING count(DISTINCT lower(regexp_replace(player_name, '[^A-Za-z]', '', 'g'))) > 1)""")[0]
    out["athlete_ids_with_conflicting_event_vs_roster_names"] = q("""
        WITH ev AS (SELECT DISTINCT source_athlete_id, lower(regexp_replace(source_name, '[^A-Za-z]', '', 'g')) AS n FROM fact.play_on_floor),
             ro AS (SELECT DISTINCT source_athlete_id, lower(regexp_replace(player_name, '[^A-Za-z]', '', 'g')) AS n FROM core.rosters)
        SELECT count(DISTINCT ev.source_athlete_id) FROM ev JOIN (SELECT DISTINCT source_athlete_id FROM ro) r USING (source_athlete_id)
        WHERE NOT EXISTS (SELECT 1 FROM ro WHERE ro.source_athlete_id = ev.source_athlete_id AND ro.n = ev.n)""")[0]
    out["onfloor_player_on_both_teams_plays"] = q("SELECT count(*) FROM fact.plays WHERE on_floor_quality = 'PLAYER_ON_BOTH_TEAMS'")[0]
    out["box_score_player_ids_equal_roster_ids_rate"] = None
    return out


def api_summary(con) -> dict:
    by = _rows(con, """SELECT endpoint, count(*) AS requests, count(*) FILTER (WHERE status IN ('STORED','ALREADY_STORED')) AS successful,
                              count(*) FILTER (WHERE status IN ('STORED','ALREADY_STORED') AND empty) AS empty_successful,
                              count(*) FILTER (WHERE status NOT IN ('STORED','ALREADY_STORED')) AS failed, sum(retries) AS retries
                       FROM req GROUP BY 1 ORDER BY 1""")
    by_season = _rows(con, """SELECT season, count(*) FILTER (WHERE status NOT IN ('STORED','ALREADY_STORED')) AS failed_requests,
                                     count(*) FILTER (WHERE empty AND status IN ('STORED','ALREADY_STORED')) AS empty_responses
                              FROM req WHERE season IS NOT NULL GROUP BY 1 ORDER BY 1""")
    return {"by_endpoint": by, "by_season": by_season}


def bdl_qa(con) -> dict:
    """2R: BDL as an independent referee, using ONLY the BDL games already held (no new BDL calls)."""
    rows = _rows(con, """
        SELECT r.coverage_quality, count(*) AS games,
               count(*) FILTER (WHERE b.home_score = r.official_home AND b.away_score = r.official_away) AS bdl_agrees_official,
               count(*) FILTER (WHERE b.home_score = r.pbp_final_home AND b.away_score = r.pbp_final_away) AS bdl_agrees_pbp
        FROM qa.pbp_game_reconciliation r JOIN benchmark.bdl_games b USING (canonical_game_id)
        WHERE b.home_score IS NOT NULL GROUP BY 1 ORDER BY 1""")
    held = con.execute("SELECT count(*), count(canonical_game_id) FROM benchmark.bdl_games").fetchone()
    return {"bdl_games_held": held[0], "bdl_games_mapped": held[1], "pbp_reconciliation_vs_bdl": rows,
            "new_bdl_calls": 0, "note": "No BDL backfill; BDL never enters flagship tables."}


def run(db_path=None) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute("SET TimeZone = 'UTC'")
    con.execute("SET enable_progress_bar = false")
    request_log_tables(con)
    tables = {"pbp": pbp_quality(con), "onfloor": onfloor_quality(con), "shots": shot_quality(con),
              "substitutions": substitution_quality(con), "lineups": lineup_quality(con), "rosters": roster_quality(con)}
    cons = consistency(con)
    defects = _rows(con, """SELECT family, reason_code, severity, excluded_from_trusted, count(*) AS records,
                                   count(DISTINCT canonical_game_id) AS games FROM qa.source_defects GROUP BY 1, 2, 3, 4 ORDER BY 1, 2""")
    defects_by_season = _rows(con, """SELECT coalesce(d.season, g.season) AS season, d.family, count(*) AS records
                                      FROM qa.source_defects d LEFT JOIN core.games g USING (canonical_game_id) GROUP BY 1, 2 ORDER BY 1, 2""")
    ident = identity_summary(con)
    api = api_summary(con)
    bdl = bdl_qa(con)
    con.close()
    names = {"pbp": "cbbd_pbp_quality_by_season.csv", "onfloor": "cbbd_onfloor_quality_by_season.csv",
             "shots": "cbbd_shot_quality_by_season.csv", "substitutions": "cbbd_substitution_quality_by_season.csv",
             "lineups": "cbbd_lineup_quality_by_season.csv", "rosters": "cbbd_roster_quality_by_season.csv"}
    for k, n in names.items():
        _write(n, tables[k])
    _write("onfloor_substitution_consistency_by_season.csv", cons)
    # combined per-season coverage row
    idx = {k: {r["season"]: r for r in v} for k, v in tables.items()}
    fail = {r["season"]: r for r in api["by_season"]}
    dseason = defaultdict(int)
    for r in defects_by_season:
        if r["season"] is not None:
            dseason[r["season"]] += r["records"]
    combined = []
    for s in SEASONS:
        p, o, sh, su, li, ro = (idx[k].get(s, {}) for k in ("pbp", "onfloor", "shots", "substitutions", "lineups", "rosters"))
        combined.append({
            "season": s, "canonical_games": p.get("canonical_games"), "games_tested_pbp": p.get("games_tested_pbp"),
            "games_with_team_box": p.get("games_with_team_box"), "games_with_player_box": p.get("games_with_player_box"),
            "games_with_pbp": p.get("games_with_pbp"), "games_tested_without_pbp": p.get("games_tested_without_pbp"),
            "plays": p.get("plays"),
            "score_reconciliation_rate": (p["games_score_reconciled"] / p["games_with_pbp"]) if p.get("games_with_pbp") else None,
            "period_clock_completeness": p.get("period_clock_completeness"),
            "plays_with_onfloor": o.get("plays_with_onfloor"), "valid_5v5_rate": o.get("valid_5v5_rate"),
            "invalid_gt5_rate": o.get("invalid_gt5_rate"), "invalid_lt5_rate": o.get("invalid_lt5_rate"),
            "unresolved_id_rate": o.get("unresolved_id_rate"),
            "shooting_plays": sh.get("shooting_plays"), "shotinfo_rate": sh.get("shotinfo_rate"),
            "shooter_resolution": sh.get("shooter_resolution"), "assister_coverage": sh.get("assister_coverage_given_assisted"),
            "xy_coverage": sh.get("xy_coverage_field_goals"),
            "games_with_substitutions": su.get("games_with_substitutions"), "substitution_rows": su.get("substitution_rows"),
            "substitution_timing_completeness": su.get("timing_completeness"), "substitution_id_resolution": su.get("id_resolution"),
            "games_with_lineups": li.get("games_with_lineups"), "valid_lineup_units": li.get("valid_five_player_units"),
            "invalid_lineup_units": li.get("invalid_units"), "lineup_seconds_coverage": li.get("mean_seconds_coverage"),
            "lineup_possessions_ratio": li.get("mean_possessions_ratio_vs_box"),
            "team_seasons_with_roster": ro.get("team_seasons_with_roster"), "box_player_roster_join_rate": ro.get("box_player_roster_join_rate"),
            "failed_requests": fail.get(s, {}).get("failed_requests", 0), "empty_responses": fail.get(s, {}).get("empty_responses", 0),
            "source_defect_records": dseason.get(s, 0)})
    _write("cbbd_event_coverage_by_season.csv", combined)
    result = {"by_season": combined, "families": tables, "onfloor_substitution_consistency": cons,
              "source_defects": defects, "source_defects_by_season": defects_by_season, "identity": ident, "api": api,
              "bdl_qa": bdl,
              "note": "Measured from the built canonical database and the request log. A failed request is never "
                      "counted as absent data; games are 'tested' only when the request that would return them succeeded."}
    OUT_JSON.write_text(json.dumps(result, indent=1, default=str))
    return result


if __name__ == "__main__":
    r = run()
    for row in r["by_season"]:
        print(row["season"], row["games_with_pbp"], row["plays"], row["plays_with_onfloor"], row["games_with_substitutions"], row["games_with_lineups"])
