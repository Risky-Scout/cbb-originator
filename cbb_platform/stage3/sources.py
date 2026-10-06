"""The ONLY place Stage 3 touches the canonical database.

`bind(con)` attaches the frozen canonical DB read-only and exposes a narrow `src` schema of views. Every feature
module reads `src.*` only, so (a) only trusted flagship relations and outcome-free context tables are reachable,
(b) the modeling source window (2013-14 .. 2024-25) is enforced once, here, (c) the label store and every market-data
relation is unreachable from feature code, and (d) synthetic tests can bind their own `src` tables.

    src.played_games   trusted PLAYED contests (flagship.played_games)              -- history + targets
    src.schedule       trusted schedule incl. unplayed records (flagship.games)      -- context only, never form
    src.team_games     team box rows of played games                                  -- history
    src.player_games   player box rows of played games                                -- history
    src.plays / src.shots / src.on_floor / src.substitutions   trusted event relations -- history
    src.rosters, src.teams, src.team_seasons, src.recruits, src.transfers             -- outcome-free context
"""
from __future__ import annotations

from cbb_platform import config as C
from cbb_platform.stage3 import spec

S = spec.season_sql


def bind(con, canonical_path=None) -> None:
    path = str(canonical_path or C.DUCKDB_PATH)
    con.execute(f"ATTACH '{path}' AS cdb (READ_ONLY)")
    w = S("season")
    con.execute(f"""
    CREATE SCHEMA IF NOT EXISTS src;
    -- Stage 3 identity guard: a canonical player whose box rows sit on two teams with INTERLEAVING game dates in one
    -- season (two people merged by the upstream name-based box mapping) is IDENTITY_UNRESOLVED for that season.
    -- The frozen v2.2 data is not changed; the rows are masked here and listed in src.identity_conflicts.
    CREATE OR REPLACE VIEW src.identity_conflicts AS
    WITH p AS (SELECT p.canonical_player_id, p.season, p.canonical_team_id, g.scheduled_tip_utc AS tip
               FROM cdb.flagship.player_game_outcomes p JOIN cdb.flagship.played_games g USING (canonical_game_id)
               WHERE p.canonical_player_id IS NOT NULL AND {S("g.season")}),
         t AS (SELECT canonical_player_id, season, canonical_team_id, min(tip) AS a, max(tip) AS b FROM p GROUP BY 1, 2, 3)
    SELECT DISTINCT x.canonical_player_id, x.season, 'INTERLEAVING_TEAMS_IN_SEASON' AS reason
    FROM t x JOIN t y USING (canonical_player_id, season)
    WHERE x.canonical_team_id < y.canonical_team_id AND x.a <= y.b AND y.a <= x.b;
    -- historical_knowable_at: canonical PIT rule HR-1 (build_canonical.KNOWABLE_SQL), exposed for result-only streams
    CREATE OR REPLACE VIEW src.played_games AS SELECT *,
        CASE WHEN tip_time_quality = 'EXACT' THEN scheduled_tip_utc + INTERVAL 4 HOUR
             ELSE timezone('America/New_York', CAST(game_date_et + INTERVAL 1 DAY AS TIMESTAMP) + INTERVAL 6 HOUR) END
            AS historical_knowable_at
        FROM cdb.flagship.played_games WHERE {w};
    CREATE OR REPLACE VIEW src.schedule AS SELECT * FROM cdb.flagship.games WHERE {w};
    CREATE OR REPLACE VIEW src.team_games AS SELECT t.* FROM cdb.flagship.team_game_outcomes t
        JOIN cdb.flagship.played_games g USING (canonical_game_id) WHERE {S("g.season")};
    CREATE OR REPLACE VIEW src.player_games AS
    SELECT p.* REPLACE (CASE WHEN c.canonical_player_id IS NULL THEN p.canonical_player_id END AS canonical_player_id,
                        CASE WHEN c.canonical_player_id IS NULL THEN p.identity_status ELSE 'UNRESOLVED_STAGE3_IDENTITY_CONFLICT' END
                            AS identity_status)
    FROM cdb.flagship.player_game_outcomes p JOIN cdb.flagship.played_games g USING (canonical_game_id)
    LEFT JOIN src.identity_conflicts c ON c.canonical_player_id = p.canonical_player_id AND c.season = g.season
    WHERE {S("g.season")};
    CREATE OR REPLACE VIEW src.plays AS SELECT p.* FROM cdb.flagship.plays p
        JOIN cdb.flagship.played_games g USING (canonical_game_id) WHERE {S("g.season")};
    -- Stage 3 attribution guard: a shooter / assister keeps player attribution only when the player belongs to the
    -- shooting team that season (roster or box row) and is not an identity conflict; otherwise the shot still counts
    -- for the TEAM but for no player (source misattribution, measured at <= 0.1% of shots per season).
    CREATE OR REPLACE VIEW src.team_members AS
    SELECT canonical_player_id, canonical_team_id, season FROM cdb.flagship.rosters WHERE {w}
    UNION SELECT canonical_player_id, canonical_team_id, season FROM cdb.flagship.player_game_outcomes
          WHERE canonical_player_id IS NOT NULL AND {w};
    CREATE OR REPLACE VIEW src.shots AS
    SELECT s.* REPLACE (
        CASE WHEN EXISTS (SELECT 1 FROM src.team_members m WHERE m.canonical_player_id = s.shooter_player_id
                          AND m.canonical_team_id = s.canonical_team_id AND m.season = s.season)
              AND NOT EXISTS (SELECT 1 FROM src.identity_conflicts c WHERE c.canonical_player_id = s.shooter_player_id
                              AND c.season = s.season) THEN s.shooter_player_id END AS shooter_player_id,
        CASE WHEN EXISTS (SELECT 1 FROM src.team_members m WHERE m.canonical_player_id = s.assister_player_id
                          AND m.canonical_team_id = s.canonical_team_id AND m.season = s.season)
              AND NOT EXISTS (SELECT 1 FROM src.identity_conflicts c WHERE c.canonical_player_id = s.assister_player_id
                              AND c.season = s.season) THEN s.assister_player_id END AS assister_player_id)
    FROM cdb.flagship.shots_trusted s JOIN cdb.flagship.played_games g USING (canonical_game_id) WHERE {S("g.season")};
    CREATE OR REPLACE VIEW src.on_floor AS SELECT f.* FROM cdb.flagship.on_floor_trusted f
        JOIN cdb.flagship.played_games g USING (canonical_game_id) WHERE {S("g.season")};
    CREATE OR REPLACE VIEW src.substitutions AS SELECT s.* FROM cdb.flagship.substitutions s
        JOIN cdb.flagship.played_games g USING (canonical_game_id) WHERE {S("g.season")};
    CREATE OR REPLACE VIEW src.rosters AS SELECT r.* FROM cdb.flagship.rosters r
        WHERE {S("r.season")} AND NOT EXISTS (SELECT 1 FROM src.identity_conflicts c
                                         WHERE c.canonical_player_id = r.canonical_player_id AND c.season = r.season);
    CREATE OR REPLACE VIEW src.teams AS SELECT canonical_team_id, school FROM cdb.core.teams;
    CREATE OR REPLACE VIEW src.team_seasons AS SELECT * FROM cdb.core.team_seasons WHERE {w};
    CREATE OR REPLACE VIEW src.recruits AS SELECT * FROM cdb.core.recruits;
    CREATE OR REPLACE VIEW src.transfers AS SELECT * FROM cdb.core.transfers;
    """)


def unbind(con) -> None:
    con.execute("DROP SCHEMA IF EXISTS src CASCADE")
    con.execute("DETACH cdb")
