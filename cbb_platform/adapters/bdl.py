"""BALLDONTLIE NCAAB adapter — P1 independent QA + prospective market benchmark.

Everything this adapter produces lands in the `benchmark` schema (or qa / metadata maps).
Nothing here may ever feed flagship.* (tests/test_market_isolation.py).

Fetch (needs BDL_API_KEY in the environment; the key is never written to disk):
    .venv/bin/python -m cbb_platform.adapters.bdl smoke

Season convention: BDL `season` = the year the season STARTS (2024 = 2024-25).
CBBD/canonical `season` = the year it ENDS. So  cbbd_season = bdl_season + 1.
"""
from __future__ import annotations

import json
import re
import sys
import time

import duckdb
import requests

from cbb_platform import config as C, credentials, lockbox, raw_store, source_fetch

BASE = "https://api.balldontlie.io/ncaab/v1"
PROVIDER = "bdl"


def bdl_to_cbbd_season(bdl_season: int) -> int:
    return lockbox.canonical_season("bdl", bdl_season)     # the one canonical season normalizer


# ---------------------------------------------------------------------------
# Fetch: every page is stored verbatim and immutably in data/raw/bdl/<endpoint>/
# ---------------------------------------------------------------------------
def _default_game_season(game_id):
    """Canonical season of a BDL game id from benchmark.bdl_games (None if unknown)."""
    if not C.DUCKDB_PATH.exists():
        return None
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    try:
        return lockbox.canonical_game_season(con, "bdl")(game_id)
    finally:
        con.close()


def fetch(endpoint: str, params: dict, *, max_pages: int = 20, known_game_season=None) -> list[raw_store.RawRecord]:
    """GET `endpoint` following cursor pagination; store each page as an immutable raw payload.

    Every page goes through the global lockbox-guarded fetch path. BDL seasons are START years, so
    the guard normalizes them (BDL 2025 = 2025-26 = lockbox) before evaluating the rule.
    """
    resolver = known_game_season or _default_game_season
    lockbox.check_request(PROVIDER, endpoint, params, resolver)
    key = credentials.get("BDL_API_KEY")
    records, cursor = [], None
    for _ in range(max_pages):
        q = dict(params, per_page=100, **({"cursor": cursor} if cursor else {}))
        try:
            content, body = source_fetch.get(PROVIDER, endpoint, f"{BASE}/{endpoint}", q, {"Authorization": key},
                                             known_game_season=resolver, timeout=30)
        except source_fetch.SourceHTTPError as e:
            if e.status in (401, 403):
                raise PermissionError(str(e)) from None
            raise
        records.append(raw_store.store(PROVIDER, endpoint, content, params=q))
        cursor = body.get("meta", {}).get("next_cursor")
        if not cursor:
            break
        time.sleep(0.15)  # stay far below the 600 requests/minute limit
    return records


# ---------------------------------------------------------------------------
# Normalize: raw pages -> benchmark.* tables, identity maps, reconciliation
# ---------------------------------------------------------------------------
def _rows(con: duckdb.DuckDBPyConnection, endpoint: str) -> list[tuple[dict, dict]]:
    """[(item, meta)] for every item of every stored page of `endpoint`, oldest page first."""
    out = []
    for path, meta in raw_store.records(PROVIDER):
        if meta["endpoint"] == endpoint:
            for item in json.loads(path.read_bytes()).get("data", []):
                out.append((item, meta))
    return out


def _num(v):
    """BDL sends spread/total values as strings; prices as ints. Empty -> None."""
    if v in (None, ""):
        return None
    return float(v)


def _minutes(v):
    """BDL `min` is a string ('32' or '32:15'); keep whole minutes for comparison."""
    if v in (None, ""):
        return None
    m = re.match(r"^\s*(\d+)", str(v))
    return int(m.group(1)) if m else None


def _insert_many(con: duckdb.DuckDBPyConnection, sql: str, rows: list) -> None:
    """executemany that tolerates an empty batch (e.g. an odds page with no rows)."""
    if rows:
        con.executemany(sql, rows)


def normalize_all(con: duckdb.DuckDBPyConnection) -> None:
    games = _rows(con, "games")
    if not games and not _rows(con, "odds") and not _rows(con, "odds/opening"):
        return
    # Latest received copy of each game wins (BDL games are QA references, not history).
    latest: dict[int, tuple[dict, dict]] = {}
    for g, meta in games:
        latest[g["id"]] = (g, meta)
    teams: dict[int, dict] = {}
    for g, _ in latest.values():
        teams[g["home_team"]["id"]] = g["home_team"]
        teams[g["visitor_team"]["id"]] = g["visitor_team"]
    for s, _ in _rows(con, "team_stats") + _rows(con, "player_stats"):
        teams[s["team"]["id"]] = s["team"]

    _map_teams(con, teams)
    _insert_many(con, "INSERT INTO benchmark.bdl_games VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,getvariable('proc'))", [
        (g["id"], g["season"], bdl_to_cbbd_season(g["season"]), g["date"], g.get("status"), g.get("postseason"),
         g["home_team"]["id"], g["visitor_team"]["id"], g.get("home_score"), g.get("away_score"),
         g.get("home_score_h1"), g.get("away_score_h1"), g.get("home_score_h2"), g.get("away_score_h2"),
         g.get("home_ot_scores"), g.get("away_ot_scores"), meta["sha256"], meta["received_at"])
        for g, meta in latest.values()])
    _map_games(con)

    # Player / team box scores: latest received copy per (game, player) / (game, team).
    ps = {(s["game"]["id"], s["player"]["id"]): (s, m) for s, m in _rows(con, "player_stats")}
    _insert_many(con, "INSERT INTO benchmark.bdl_player_games VALUES "
                    "(?,?,?,NULL,NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,getvariable('proc'))", [
        (s["game"]["id"], s["player"]["id"], s["team"]["id"], s["player"].get("first_name"),
         s["player"].get("last_name"), s.get("min"), s.get("fgm"), s.get("fga"), s.get("fg3m"), s.get("fg3a"),
         s.get("ftm"), s.get("fta"), s.get("oreb"), s.get("dreb"), s.get("reb"), s.get("ast"), s.get("stl"),
         s.get("blk"), s.get("turnover"), s.get("pf"), s.get("pts"), m["sha256"], m["received_at"])
        for s, m in ps.values()])
    ts = {(s["game"]["id"], s["team"]["id"]): (s, m) for s, m in _rows(con, "team_stats")}
    _insert_many(con, "INSERT INTO benchmark.bdl_team_games VALUES (?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,getvariable('proc'))", [
        (s["game"]["id"], s["team"]["id"], s.get("fgm"), s.get("fga"), s.get("fg3m"), s.get("fg3a"),
         s.get("ftm"), s.get("fta"), s.get("oreb"), s.get("dreb"), s.get("reb"), s.get("ast"), s.get("stl"),
         s.get("blk"), s.get("turnovers"), s.get("fouls"), m["sha256"], m["received_at"])
        for s, m in ts.values()])

    # Odds: every stored snapshot is kept (time series keyed by odds id + payload hash).
    for endpoint, table, ts_field in (("odds", "benchmark.game_odds", "updated_at"),
                                      ("odds/opening", "benchmark.opening_odds", "opened_at")):
        rows = {(o["id"], m["sha256"]): (o, m) for o, m in _rows(con, endpoint)}
        _insert_many(con, f"INSERT INTO {table} VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,getvariable('proc'))", [
            (o["id"], o["game_id"], o["vendor"], _num(o.get("spread_home_value")), o.get("spread_home_odds"),
             _num(o.get("spread_away_value")), o.get("spread_away_odds"), o.get("moneyline_home_odds"),
             o.get("moneyline_away_odds"), _num(o.get("total_value")), o.get("total_over_odds"),
             o.get("total_under_odds"), o.get(ts_field), m["sha256"], m["received_at"])
            for o, m in rows.values()])

    con.execute("""
    UPDATE benchmark.bdl_team_games t SET canonical_game_id = g.canonical_game_id
        FROM benchmark.bdl_games g WHERE g.bdl_game_id = t.bdl_game_id;
    UPDATE benchmark.bdl_team_games t SET canonical_team_id = m.canonical_team_id
        FROM metadata.team_id_map m WHERE m.provider = 'BDL' AND m.provider_team_key = CAST(t.bdl_team_id AS VARCHAR);
    UPDATE benchmark.bdl_player_games p SET canonical_game_id = g.canonical_game_id
        FROM benchmark.bdl_games g WHERE g.bdl_game_id = p.bdl_game_id;
    UPDATE benchmark.bdl_player_games p SET canonical_team_id = m.canonical_team_id
        FROM metadata.team_id_map m WHERE m.provider = 'BDL' AND m.provider_team_key = CAST(p.bdl_team_id AS VARCHAR);
    UPDATE benchmark.game_odds o SET canonical_game_id = g.canonical_game_id
        FROM benchmark.bdl_games g WHERE g.bdl_game_id = o.bdl_game_id;
    UPDATE benchmark.opening_odds o SET canonical_game_id = g.canonical_game_id
        FROM benchmark.bdl_games g WHERE g.bdl_game_id = o.bdl_game_id;
    """)
    _map_players(con)
    _reconcile(con)


# Team names: CBBD display_name ("Michigan State Spartans") vs BDL full_name, then CBBD school vs
# BDL college. Exact string equality only; anything else stays UNRESOLVED for manual review.
def _map_teams(con: duckdb.DuckDBPyConnection, teams: dict[int, dict]) -> None:
    for tid, t in sorted(teams.items()):
        by_full = con.execute("SELECT canonical_team_id FROM core.teams WHERE display_name = ?",
                              [t.get("full_name")]).fetchall()
        by_college = con.execute("SELECT canonical_team_id FROM core.teams WHERE school = ?",
                                 [t.get("college")]).fetchall()
        if len(by_full) == 1:
            row = (by_full[0][0], "EXACT_NAME", 0.99, False, f"BDL full_name='{t.get('full_name')}' = CBBD display_name")
        elif len(by_college) == 1:
            row = (by_college[0][0], "EXACT_NAME", 0.95, False, f"BDL college='{t.get('college')}' = CBBD school")
        else:
            row = (None, "UNRESOLVED", 0.0, True,
                   f"no unique exact match for full_name='{t.get('full_name')}', college='{t.get('college')}' "
                   f"(display_name matches={len(by_full)}, school matches={len(by_college)})")
        con.execute("INSERT INTO metadata.team_id_map VALUES ('BDL', ?, ?, NULL, ?, ?, ?, ?, ?)",
                    [str(tid), row[0], row[1], row[2], row[3], row[4], C.INGESTION_VERSION])
        if row[0] is None:
            con.execute("INSERT INTO qa.exceptions VALUES ('UNRESOLVED_TEAM','WARN',?,?, 'BDL', getvariable('ver'))",
                        [f"bdl_team_id={tid}", row[4]])


def _map_games(con: duckdb.DuckDBPyConnection) -> None:
    """BDL game -> canonical game by (unordered mapped team pair, US/Eastern calendar date)."""
    con.execute("""
    CREATE OR REPLACE TEMP TABLE bdl_game_match AS
    SELECT b.bdl_game_id, list(g.canonical_game_id ORDER BY g.canonical_game_id) AS candidates
    FROM benchmark.bdl_games b
    JOIN metadata.team_id_map hm ON hm.provider = 'BDL' AND hm.provider_team_key = CAST(b.bdl_home_team_id AS VARCHAR)
    JOIN metadata.team_id_map vm ON vm.provider = 'BDL' AND vm.provider_team_key = CAST(b.bdl_visitor_team_id AS VARCHAR)
    LEFT JOIN core.games g
      ON g.game_date_et = CAST(timezone('America/New_York', b.game_date_utc) AS DATE)
     AND ((g.home_team_id = hm.canonical_team_id AND g.away_team_id = vm.canonical_team_id)
       OR (g.home_team_id = vm.canonical_team_id AND g.away_team_id = hm.canonical_team_id))
    GROUP BY 1;

    UPDATE benchmark.bdl_games b SET canonical_game_id = m.candidates[1]
    FROM bdl_game_match m WHERE m.bdl_game_id = b.bdl_game_id AND len(list_filter(m.candidates, x -> x IS NOT NULL)) = 1;

    INSERT INTO metadata.game_id_map
    SELECT 'BDL', CAST(b.bdl_game_id AS VARCHAR), b.canonical_game_id,
           CASE WHEN b.canonical_game_id IS NULL THEN 'UNRESOLVED' ELSE 'TEAMS_AND_ET_DATE' END,
           CASE WHEN b.canonical_game_id IS NULL THEN 0.0 ELSE 1.0 END,
           b.canonical_game_id IS NULL,
           'BDL season ' || b.bdl_season_start_year || ' -> CBBD season ' || b.cbbd_season, getvariable('ver')
    FROM benchmark.bdl_games b;

    INSERT INTO qa.exceptions
    SELECT 'UNRESOLVED_GAME', 'WARN', 'bdl_game_id=' || bdl_game_id,
           'no unique canonical game for mapped teams on ET date (games outside the Starter Pack seasons cannot match)',
           'BDL', getvariable('ver')
    FROM benchmark.bdl_games WHERE canonical_game_id IS NULL;
    """)


def _map_players(con: duckdb.DuckDBPyConnection) -> None:
    """BDL player -> canonical player who has a box row in the SAME canonical game and team.

    Exact full-name equality first; then a documented normalization (case, punctuation,
    Jr./Sr./II/III/IV suffixes) flagged for manual review. Never name-only across teams/games.
    """
    con.execute(r"""
    CREATE OR REPLACE MACRO norm_name(n) AS
        trim(regexp_replace(regexp_replace(lower(n), '\b(jr|sr|ii|iii|iv)\b\.?', '', 'g'), '[^a-z ]', '', 'g'));

    CREATE OR REPLACE TEMP TABLE bdl_player_match AS
    SELECT b.bdl_game_id, b.bdl_player_id, b.canonical_game_id, b.canonical_team_id,
           b.player_first_name || ' ' || b.player_last_name AS bdl_name,
           list(DISTINCT p.canonical_player_id) FILTER (
               WHERE p.source_player_name = b.player_first_name || ' ' || b.player_last_name) AS exact_ids,
           list(DISTINCT p.canonical_player_id) FILTER (
               WHERE norm_name(p.source_player_name) = norm_name(b.player_first_name || ' ' || b.player_last_name)) AS norm_ids,
           -- Fallback for BDL rows with no CBBD box row (BDL lists non-participants with NULL minutes):
           -- exact name among the same team's season-stat members for that CBBD season.
           (SELECT list(DISTINCT s.canonical_player_id) FROM fact.player_season_stats s JOIN core.games g
              ON g.canonical_game_id = b.canonical_game_id
             WHERE s.canonical_team_id = b.canonical_team_id AND s.season = g.season
               AND s.source_player_name = b.player_first_name || ' ' || b.player_last_name) AS season_ids
    FROM benchmark.bdl_player_games b
    LEFT JOIN fact.player_games p ON p.canonical_game_id = b.canonical_game_id
                                  AND p.canonical_team_id = b.canonical_team_id
                                  AND p.canonical_player_id IS NOT NULL
    GROUP BY ALL;

    CREATE OR REPLACE TEMP TABLE bdl_player_result AS
    SELECT *,
        CASE WHEN len(exact_ids) = 1 THEN exact_ids[1]
             WHEN coalesce(len(exact_ids), 0) = 0 AND len(norm_ids) = 1 THEN norm_ids[1]
             WHEN coalesce(len(exact_ids), 0) = 0 AND coalesce(len(norm_ids), 0) = 0 AND len(season_ids) = 1
                  THEN season_ids[1] END AS canonical_player_id,
        CASE WHEN len(exact_ids) = 1 THEN 'EXACT_NAME_WITHIN_GAME_TEAM'
             WHEN coalesce(len(exact_ids), 0) = 0 AND len(norm_ids) = 1 THEN 'NORMALIZED_NAME_WITHIN_GAME_TEAM'
             WHEN coalesce(len(exact_ids), 0) = 0 AND coalesce(len(norm_ids), 0) = 0 AND len(season_ids) = 1
                  THEN 'EXACT_NAME_TEAM_SEASON'
             ELSE 'UNRESOLVED' END AS method
    FROM bdl_player_match;

    UPDATE benchmark.bdl_player_games b SET canonical_player_id = r.canonical_player_id
    FROM bdl_player_result r WHERE r.bdl_game_id = b.bdl_game_id AND r.bdl_player_id = b.bdl_player_id;

    INSERT INTO metadata.player_id_map
    SELECT 'BDL', CAST(bdl_player_id AS VARCHAR), canonical_player_id, NULL, canonical_team_id, method,
           CASE method WHEN 'EXACT_NAME_WITHIN_GAME_TEAM' THEN 0.98 WHEN 'EXACT_NAME_TEAM_SEASON' THEN 0.95
                       WHEN 'NORMALIZED_NAME_WITHIN_GAME_TEAM' THEN 0.85 ELSE 0.0 END,
           method NOT IN ('EXACT_NAME_WITHIN_GAME_TEAM', 'EXACT_NAME_TEAM_SEASON'),
           'bdl_game_id=' || bdl_game_id || '; bdl_name=' || bdl_name, getvariable('ver')
    FROM bdl_player_result;

    INSERT INTO qa.exceptions
    SELECT 'UNRESOLVED_PLAYER', 'WARN', 'bdl_player_id=' || bdl_player_id || ';bdl_game_id=' || bdl_game_id,
           'BDL name ' || bdl_name || ' has no unique canonical box-row match in the same game/team', 'BDL', getvariable('ver')
    FROM bdl_player_result WHERE method = 'UNRESOLVED';
    """)


def _reconcile(con: duckdb.DuckDBPyConnection) -> None:
    """CBBD vs BDL: disagreements are recorded, never used to overwrite CBBD."""
    con.execute("""
    INSERT INTO qa.reconciliation
    SELECT 'GAME_SCORE', b.canonical_game_id, NULL, NULL, s.stat, s.cbbd, s.bdl, s.cbbd IS NOT DISTINCT FROM s.bdl, 'BDL'
    FROM benchmark.bdl_games b JOIN core.games g USING (canonical_game_id),
    LATERAL (SELECT * FROM (VALUES
        -- BDL home/visitor may be oriented differently from CBBD at neutral sites; compare as a pair.
        ('higher_score', greatest(g.home_points, g.away_points), greatest(b.home_score, b.away_score)),
        ('lower_score',  least(g.home_points, g.away_points),    least(b.home_score, b.away_score))) v(stat, cbbd, bdl)) s;

    -- Measured BDL semantics: for a player WITH minutes, a NULL counting stat means zero
    -- (e.g. 1 minute, points NULL vs CBBD 0). Only for comparison; benchmark rows keep NULL.
    INSERT INTO qa.reconciliation
    SELECT 'PLAYER_GAME', b.canonical_game_id, b.canonical_team_id, b.canonical_player_id, s.stat, s.cbbd, s.bdl,
           s.cbbd IS NOT DISTINCT FROM s.bdl, 'BDL'
    FROM benchmark.bdl_player_games b
    JOIN fact.player_games p ON p.canonical_game_id = b.canonical_game_id AND p.canonical_player_id = b.canonical_player_id,
    LATERAL (SELECT * FROM (VALUES
        ('points', p.points, coalesce(b.points, 0)), ('fgm', p.fgm, coalesce(b.fgm, 0)), ('fga', p.fga, coalesce(b.fga, 0)),
        ('three_pm', p.three_pm, coalesce(b.three_pm, 0)), ('three_pa', p.three_pa, coalesce(b.three_pa, 0)),
        ('ftm', p.ftm, coalesce(b.ftm, 0)), ('fta', p.fta, coalesce(b.fta, 0)), ('orb', p.orb, coalesce(b.orb, 0)),
        ('drb', p.drb, coalesce(b.drb, 0)), ('reb', p.reb, coalesce(b.reb, 0)), ('ast', p.ast, coalesce(b.ast, 0)),
        ('stl', p.stl, coalesce(b.stl, 0)), ('blk', p.blk, coalesce(b.blk, 0)),
        ('tov', p.tov, coalesce(b.tov, 0)), ('pf', p.pf, coalesce(b.pf, 0)),
        ('minutes', p.minutes, TRY_CAST(regexp_extract(b.minutes_raw, '^\\s*(\\d+)', 1) AS INTEGER))
    ) v(stat, cbbd, bdl)) s
    WHERE b.minutes_raw IS NOT NULL;

    INSERT INTO qa.reconciliation
    SELECT 'TEAM_GAME', b.canonical_game_id, b.canonical_team_id, NULL, s.stat, s.cbbd, s.bdl,
           s.cbbd IS NOT DISTINCT FROM s.bdl, 'BDL'
    FROM benchmark.bdl_team_games b
    JOIN fact.team_games t ON t.canonical_game_id = b.canonical_game_id AND t.canonical_team_id = b.canonical_team_id,
    LATERAL (SELECT * FROM (VALUES
        -- same measured NULL-means-zero convention as player rows
        ('fgm', t.fgm, coalesce(b.fgm, 0)), ('fga', t.fga, coalesce(b.fga, 0)),
        ('three_pm', t.three_pm, coalesce(b.three_pm, 0)), ('three_pa', t.three_pa, coalesce(b.three_pa, 0)),
        ('ftm', t.ftm, coalesce(b.ftm, 0)), ('fta', t.fta, coalesce(b.fta, 0)), ('orb', t.orb, coalesce(b.orb, 0)),
        ('drb', t.drb, coalesce(b.drb, 0)), ('reb', t.reb, coalesce(b.reb, 0)), ('ast', t.ast, coalesce(b.ast, 0)),
        ('stl', t.stl, coalesce(b.stl, 0)), ('blk', t.blk, coalesce(b.blk, 0)),
        ('tov_total', t.tov_total, coalesce(b.tov, 0)), ('pf', t.pf, coalesce(b.pf, 0))
    ) v(stat, cbbd, bdl)) s;

    INSERT INTO qa.exceptions
    SELECT 'BDL_MISMATCH', 'WARN',
           entity_type || ':' || canonical_game_id || ':' || coalesce(canonical_team_id, '') || ':' ||
           coalesce(canonical_player_id, '') || ':' || stat,
           'cbbd=' || coalesce(cbbd_value::VARCHAR, 'NULL') || ' bdl=' || coalesce(other_value::VARCHAR, 'NULL'),
           'BDL', getvariable('ver')
    FROM qa.reconciliation WHERE NOT values_match AND other_source = 'BDL';
    """)


# ---------------------------------------------------------------------------
# Bounded smoke fetch (Step 9). Seasons <= 2024-25 only: 2025-26 is the untouched lockbox.
# ---------------------------------------------------------------------------
SMOKE_DATES = ["2025-03-22"]          # UTC date filter (BDL semantics); 2024-25 NCAA tournament
SMOKE_GAME_LIMIT = 4                  # games carried into player/team/odds fetches


def smoke() -> dict:
    report: dict = {}
    games = fetch("games", {"dates[]": SMOKE_DATES})
    items = [g for r in games for g in json.loads(r.path.read_bytes())["data"]]
    finals = sorted((g for g in items if g.get("status_state") == "final"), key=lambda g: g["id"])[:SMOKE_GAME_LIMIT]
    ids = [g["id"] for g in finals]
    report["games_fetched"] = len(items)
    report["smoke_game_ids"] = ids
    report["player_stats_pages"] = len(fetch("player_stats", {"game_ids[]": ids}))
    report["team_stats_pages"] = len(fetch("team_stats", {"game_ids[]": ids}))
    for ep in ("odds", "odds/opening"):
        try:
            report[f"{ep}_pages"] = len(fetch(ep, {"game_ids": ids}))
        except PermissionError as exc:   # GOAT-tier endpoints may be unavailable on this key
            report[f"{ep}_pages"] = f"BLOCKED_TIER: {exc}"
    report["players_active_pages"] = len(fetch("players/active", {"team_ids[]": [finals[0]["home_team"]["id"]]},
                                               max_pages=2))
    return report


if __name__ == "__main__":
    if sys.argv[1:] == ["smoke"]:
        print(json.dumps(smoke(), indent=1))
    else:
        print(__doc__)
