"""Point-in-time legality primitives (POINT_IN_TIME_POLICY.md HR-2, HR-3).

Stage 1 does not build features. It freezes the two legality rules that every later feature
builder must reuse, so they are tested once, here:

  * season-to-date ("as-of") aggregates are rebuilt from canonical game-level rows that were
    knowable strictly before the cutoff. The target game and every game at or after its tip are
    excluded, whatever the cutoff (a cutoff can be mis-set; the exclusion cannot);
  * a completed-season aggregate is legal only for a target in a LATER season, and only once it
    was knowable before the cutoff.

Reads only the flagship namespace.
"""
from __future__ import annotations

import duckdb

BOX_FIELDS = ("points", "fgm", "fga", "two_pm", "two_pa", "three_pm", "three_pa", "ftm", "fta",
              "orb", "drb", "reb", "ast", "stl", "blk", "tov_total", "pf")


def _target(con: duckdb.DuckDBPyConnection, target_game_id: str) -> tuple:
    row = con.execute("SELECT season, scheduled_tip_utc, game_date_et FROM flagship.games WHERE canonical_game_id = ?",
                      [target_game_id]).fetchone()
    if row is None:
        raise KeyError(target_game_id)
    return row


AS_OF_SQL = """
SELECT t.* FROM flagship.team_game_outcomes t JOIN flagship.games g USING (canonical_game_id)
WHERE t.canonical_team_id = $team AND t.season = $season
  AND t.historical_knowable_at < $cutoff                      -- knowable strictly before the cutoff
  AND t.canonical_game_id <> $target                          -- HR-2: never the target game
  AND (CASE WHEN $tip IS NOT NULL AND g.scheduled_tip_utc IS NOT NULL
            THEN g.scheduled_tip_utc < $tip                   -- strictly earlier tip
            ELSE g.game_date_et < $date_et END)               -- date-only: strictly earlier ET date
"""


def team_games_as_of(con: duckdb.DuckDBPyConnection, team_id: str, target_game_id: str, cutoff) -> list[tuple]:
    """Same-season team-game rows legal for a prediction of `target_game_id` at `cutoff`."""
    season, tip, date_et = _target(con, target_game_id)
    return con.execute(AS_OF_SQL + " ORDER BY g.scheduled_tip_utc, t.canonical_game_id", {
        "team": team_id, "season": season, "cutoff": cutoff, "target": target_game_id, "tip": tip,
        "date_et": date_et}).fetchall()


def team_season_to_date(con: duckdb.DuckDBPyConnection, team_id: str, target_game_id: str, cutoff) -> dict:
    """Season-to-date box totals for one team, with the evidence needed to audit them."""
    season, tip, date_et = _target(con, target_game_id)
    sums = ", ".join(f"sum({f}) AS {f}" for f in BOX_FIELDS)
    cur = con.execute(f"""SELECT count(*) AS games, list(canonical_game_id ORDER BY canonical_game_id) AS game_ids,
                                 max(historical_knowable_at) AS feature_as_of_time, {sums}
                          FROM ({AS_OF_SQL})""", {
        "team": team_id, "season": season, "cutoff": cutoff, "target": target_game_id, "tip": tip,
        "date_et": date_et})
    return dict(zip([d[0] for d in cur.description], cur.fetchone()))


def completed_season_aggregate_is_legal(aggregate_season: int, aggregate_knowable_at, target_season: int,
                                        cutoff) -> bool:
    """HR-3: a completed-season aggregate never describes its own season's games."""
    return aggregate_season < target_season and aggregate_knowable_at < cutoff


RESULTS_AS_OF_SQL = """
SELECT g.canonical_game_id,
       CASE WHEN g.home_team_id = $team THEN g.home_points ELSE g.away_points END AS pts_for,
       CASE WHEN g.home_team_id = $team THEN g.away_points ELSE g.home_points END AS pts_against
FROM flagship.games g                                         -- TRUSTED games: an alias never adds a result
WHERE $team IN (g.home_team_id, g.away_team_id) AND g.season = $season
  AND g.home_points IS NOT NULL AND g.away_points IS NOT NULL
  AND g.canonical_game_id <> $target
  AND (CASE WHEN $tip IS NOT NULL AND g.scheduled_tip_utc IS NOT NULL THEN g.scheduled_tip_utc < $tip
            ELSE g.game_date_et < $date_et END)
"""


def team_results_to_date(con: duckdb.DuckDBPyConnection, team_id: str, target_game_id: str) -> dict:
    """Season-to-date ALL-RESULTS record (games, wins, losses, points for/against) before a target game,
    computed only from the trusted game relation (Stage 2.1)."""
    season, tip, date_et = _target(con, target_game_id)
    return dict(zip(("games", "wins", "losses", "points_for", "points_against", "game_ids"), con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE pts_for > pts_against), count(*) FILTER (WHERE pts_for < pts_against),
               sum(pts_for), sum(pts_against), list(canonical_game_id ORDER BY canonical_game_id)
        FROM ({RESULTS_AS_OF_SQL})""", {"team": team_id, "season": season, "target": target_game_id, "tip": tip,
                                        "date_et": date_et}).fetchone()))
