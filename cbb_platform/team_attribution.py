"""Pre-Model Integrity Closure v1 -- team-attribution and identity remediation layer (dataset v2.4).

Applied inside every canonical build, after the Stage 3.1 rules (cbb_platform/player_identity.py). Raw sources are never
touched: every rule rewrites or removes DERIVED canonical rows only, logs itself in metadata.player_identity_resolution /
metadata.team_attribution_resolution, and preserves every removed derived row verbatim in qa.identity_remediation_quarantine.
Evidence and classifications: docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md (cbb_platform/identity_cluster_audit.py).

MIRRORED_BOX_LINES (generic, reconciliation-gated). In a few Starter Pack games one team's box lines are copied onto the
opponent, so every affected player appears for BOTH teams. CBBD's season files then aggregate the copy into a one-game
season row on the opponent, which made whole rosters look like mid-season "transfers" (TCU/Washington, DePaul/Missouri
State, Bradley/Kansas State, ...). For each mirrored row the true team T is decided from positive evidence only:
    the player (unique season-file name match on one of the two game teams) has >= 1 resolved box row for T in OTHER games
    that season, none for the opponent, a season-file row on T with >= 2 games and at most a 1-game row on the opponent.
The game is repaired only if EVERY mirrored row is decided and, after keeping the true-team copy and quarantining the
copy, BOTH teams' summed player PTS / FGM / 3PM / FTM equal their team box line exactly. Otherwise every row stays
unresolved and the game is logged QUARANTINED_UNRECONCILED.
SPURIOUS_SEASON_MEMBERSHIP. A season-file row (player, team W, season) with <= 1 game, no resolved box row for W, and a
resolved box row for the OPPONENT of W in a repaired game is the season-file echo of the defect: it is quarantined from
fact.player_season_stats / fact.player_season_shooting (it otherwise made the player a "member" of W for participation).
EVENT_TEAM_NOT_IN_GAME. A game whose PBP / substitution / lineup feed names a team that did not play it (game 209587:
every event filed under Jacksonville) is excluded from the trusted event relations (qa.source_defects); events are not
re-guessed.
SOURCE_ID_COLLISION_QUARANTINE. Explicit adjudications below: provider athlete ids whose history is physically or
eligibility-impossible for one person. The impossible era is quarantined (box rows -> identity NULL; season-file, roster
and event links removed); nothing is reassigned to anyone else.
IDENTITY_LINK_QUARANTINE. Cross-season links (consecutive box seasons of one canonical player) without positive evidence
are recorded in metadata.identity_link_quarantine; Stage 3 never joins history across a quarantined link.
"""
from __future__ import annotations

import duckdb

VERSION = "pmic1-team-attribution-1.0.0"
CHECK_STATS = ("points", "fgm", "three_pm", "ftm")
# a cross-season move of >= this many players between the same two team-seasons is a whole-team-sized cluster
WHOLE_TEAM_CLUSTER_MIN = 5
LONG_GAP_SEASONS = 3

# Explicit SOURCE_ID_COLLISION adjudications (CBBD athleteId, quarantined seasons, evidence). Found by the eligibility,
# DOB-age and overlap checks of cbb_platform/identity_cluster_audit.py; evidence in docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md.
COLLISIONS = (
    ("105", (2005,), "QUARANTINE_ERA_CBBD_ATHLETE_105",
     "Marcus Watson Jr.: box rows in 2004-05 (Grambling, 21 games) and 2024-25 (Jackson State) -- a 21-season span is "
     "impossible for one college career; the 2005 era (20-season gap) is quarantined, 2025 keeps the id. Supporting only: "
     "provider DOB 2004-10-14 (provider DOB is measured unreliable, see the cluster audit)."),
    ("62580", (2006, 2007), "QUARANTINE_ERA_CBBD_ATHLETE_62580",
     "Aaron Williams (Chicago State): box seasons 2006, 2007, 2012, 2013, 2015 -- a 10-season span is impossible for one "
     "college career; the 2006-07 era (4-season gap) is quarantined, the 2012-15 era keeps the id."),
    ("68710", (2006, 2007), "QUARANTINE_ERA_CBBD_ATHLETE_68710",
     "Ryan Williams: Tulane 2006-07 and Central Arkansas 2011-14 -- a 9-season span is impossible for one college career; "
     "the Tulane era (4-season gap) is quarantined, the Central Arkansas era keeps the id."),
    ("19841", (2022, 2023, 2024), "QUARANTINE_ERA_CBBD_ATHLETE_19841",
     "DeJuan Clayton: box rows in 8 consecutive seasons (Coppin State 2017-21, Hartford 2022, California 2023, Manhattan "
     "2024) exceed any eligibility path; the 2022-24 rows (13 games, 3 teams, no portal record) cannot be tied to the "
     "Coppin State player and are quarantined. The Coppin State era (5 seasons incl. the COVID year) keeps the id."),
)

NORM_NAME = ("regexp_replace(regexp_replace(lower({x}), '\\b(jr|sr|ii|iii|iv|v)\\b\\.?', '', 'g'), '[^a-z]', '', 'g')")


def norm(x: str) -> str:
    return NORM_NAME.format(x=x)


def _quarantine(con, rule_id: str, rule_type: str, table: str, where: str, key: str, game: str, team: str, player: str,
                season: str, reason: str) -> int:
    con.execute(f"""INSERT INTO qa.identity_remediation_quarantine
        SELECT '{rule_id}', '{rule_type}', '{table}', {key}, {game}, {team}, {player}, {season}, ?, to_json(t)::VARCHAR, '{VERSION}'
        FROM (SELECT * EXCLUDE (processed_at) FROM {table}) t WHERE {where}""", [reason])   # build-time column excluded: deterministic
    return con.execute(f"DELETE FROM {table} t WHERE {where}").fetchone()[0]


def _log(con, rows) -> None:
    con.executemany("INSERT INTO metadata.player_identity_resolution VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (VERSION,) for r in rows])


# ============================================================================ box layer (inside build_canonical)
def apply_box(con: duckdb.DuckDBPyConnection) -> None:
    """After player_identity.apply_box (season tables exist), before participation and derived tables."""
    _demirror(con)
    _spurious_season_membership(con)
    _collision_box(con)


def _demirror(con) -> None:
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ta_games AS
    SELECT DISTINCT p.canonical_game_id, g.season, g.home_team_id, g.away_team_id
    FROM fact.player_games p JOIN core.games g USING (canonical_game_id)
    WHERE p.identity_status = 'UNRESOLVED_SAME_PLAYER_BOTH_TEAMS';
    CREATE OR REPLACE TEMP TABLE ta_dec AS
    WITH m AS (SELECT p.player_game_key, p.canonical_game_id, p.canonical_team_id AS row_team, p.source_player_name, g.season,
                      CASE WHEN p.canonical_team_id = g.home_team_id THEN g.away_team_id ELSE g.home_team_id END AS opp_team,
                      g.home_team_id, g.away_team_id
               FROM fact.player_games p JOIN ta_games g USING (canonical_game_id)
               WHERE p.identity_status = 'UNRESOLVED_SAME_PLAYER_BOTH_TEAMS'),
    c AS (SELECT m.player_game_key, m.canonical_game_id, m.season, m.row_team, m.opp_team, m.source_player_name,
                 count(DISTINCT s.canonical_player_id) AS n_ids, min(s.canonical_player_id) AS pid
          FROM m LEFT JOIN fact.player_season_stats s ON s.season = m.season AND s.source_player_name = m.source_player_name
               AND s.canonical_team_id IN (m.home_team_id, m.away_team_id)
          GROUP BY ALL),
    e AS (SELECT c.*,
            (SELECT count(*) FROM fact.player_games x WHERE x.canonical_player_id = c.pid AND x.season = c.season
                AND x.canonical_team_id = c.row_team) AS box_row_team,
            (SELECT count(*) FROM fact.player_games x WHERE x.canonical_player_id = c.pid AND x.season = c.season
                AND x.canonical_team_id = c.opp_team) AS box_opp_team,
            coalesce((SELECT max(s.games) FROM fact.player_season_stats s WHERE s.canonical_player_id = c.pid AND s.season = c.season
                AND s.canonical_team_id = c.row_team), 0) AS sf_row_team,
            coalesce((SELECT max(s.games) FROM fact.player_season_stats s WHERE s.canonical_player_id = c.pid AND s.season = c.season
                AND s.canonical_team_id = c.opp_team), 0) AS sf_opp_team
          FROM c)
    SELECT e.*, CASE WHEN n_ids <> 1 THEN 'UNDECIDED'
                     WHEN box_row_team >= 1 AND box_opp_team = 0 AND sf_row_team >= 2 AND sf_opp_team <= 1 THEN 'KEEP'
                     WHEN box_opp_team >= 1 AND box_row_team = 0 AND sf_opp_team >= 2 AND sf_row_team <= 1 THEN 'QUARANTINE'
                     ELSE 'UNDECIDED' END AS decision
    FROM e;
    """)
    sums = ", ".join(f"sum(p.{c}) AS {c}" for c in CHECK_STATS)
    eq = " AND ".join(f"a.{c} IS NOT DISTINCT FROM t.{c}" for c in CHECK_STATS)   # same column names in fact.team_games
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE ta_after AS
    SELECT p.canonical_game_id, p.canonical_team_id, {sums}
    FROM fact.player_games p LEFT JOIN ta_dec d USING (player_game_key)
    WHERE p.canonical_game_id IN (SELECT canonical_game_id FROM ta_games) AND coalesce(d.decision, 'NOT_MIRRORED') IN ('KEEP', 'NOT_MIRRORED')
    GROUP BY 1, 2;
    CREATE OR REPLACE TEMP TABLE ta_gate AS
    SELECT g.canonical_game_id, g.season, g.home_team_id, g.away_team_id,
           (SELECT count(*) FROM ta_dec d WHERE d.canonical_game_id = g.canonical_game_id) AS n_mirrored,
           (SELECT count(*) FROM ta_dec d WHERE d.canonical_game_id = g.canonical_game_id AND d.decision = 'UNDECIDED') AS n_undecided,
           (SELECT count(*) FROM (SELECT pid FROM ta_dec d WHERE d.canonical_game_id = g.canonical_game_id
                                  GROUP BY pid HAVING count(*) FILTER (WHERE decision = 'KEEP') <> 1)) AS n_bad_pairs,
           (SELECT count(*) FROM fact.team_games t JOIN ta_after a USING (canonical_game_id, canonical_team_id)
             WHERE t.canonical_game_id = g.canonical_game_id AND {eq}) AS n_teams_reconciled,
           (SELECT t.points FROM fact.team_games t WHERE t.canonical_game_id = g.canonical_game_id AND t.canonical_team_id = g.home_team_id) AS hb,
           (SELECT a.points FROM ta_after a WHERE a.canonical_game_id = g.canonical_game_id AND a.canonical_team_id = g.home_team_id) AS hp,
           (SELECT t.points FROM fact.team_games t WHERE t.canonical_game_id = g.canonical_game_id AND t.canonical_team_id = g.away_team_id) AS ab,
           (SELECT a.points FROM ta_after a WHERE a.canonical_game_id = g.canonical_game_id AND a.canonical_team_id = g.away_team_id) AS ap
    FROM ta_games g;
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ta_ok AS SELECT canonical_game_id FROM ta_gate
    WHERE n_undecided = 0 AND n_bad_pairs = 0 AND n_teams_reconciled = 2;
    """)
    con.execute(f"""INSERT INTO metadata.player_game_team_attribution
        SELECT player_game_key, canonical_game_id, row_team, pid, decision, 'DEMIRROR_RECONCILED_GAMES', '{VERSION}'
        FROM ta_dec WHERE canonical_game_id IN (SELECT canonical_game_id FROM ta_ok) ORDER BY 1""")
    restored = con.execute("""UPDATE fact.player_games p SET canonical_player_id = d.pid, identity_status = 'RESOLVED'
        FROM ta_dec d WHERE d.player_game_key = p.player_game_key AND d.decision = 'KEEP'
          AND d.canonical_game_id IN (SELECT canonical_game_id FROM ta_ok)""").fetchone()[0]
    reason = ("MIRRORED_BOX_LINES: copy of a true-team box line filed under the opponent; true team decided from the player's "
              "other box rows and season-file games; game reconciles PTS/FGM/3PM/FTM for both teams after removal")
    moved = _quarantine(con, "DEMIRROR_RECONCILED_GAMES", "TEAM_ATTRIBUTION_REPAIR", "fact.player_games",
                        """t.player_game_key IN (SELECT player_game_key FROM ta_dec WHERE decision = 'QUARANTINE'
                           AND canonical_game_id IN (SELECT canonical_game_id FROM ta_ok))""",
                        "t.player_game_key", "t.canonical_game_id", "t.canonical_team_id", "t.canonical_player_id", "t.season", reason)
    con.execute(f"""
    INSERT INTO metadata.team_attribution_resolution
    SELECT g.canonical_game_id, g.season, 'MIRRORED_BOX_LINES',
           CASE WHEN o.canonical_game_id IS NOT NULL THEN 'REMEDIATED_RECONCILED' ELSE 'QUARANTINED_UNRECONCILED' END,
           g.n_mirrored,
           CASE WHEN o.canonical_game_id IS NOT NULL THEN (SELECT count(*) FROM ta_dec d WHERE d.canonical_game_id = g.canonical_game_id AND d.decision = 'KEEP') ELSE 0 END,
           CASE WHEN o.canonical_game_id IS NOT NULL THEN (SELECT count(*) FROM ta_dec d WHERE d.canonical_game_id = g.canonical_game_id AND d.decision = 'QUARANTINE') ELSE 0 END,
           g.hb, g.hp, g.ab, g.ap, o.canonical_game_id IS NOT NULL,
           'mirrored rows ' || g.n_mirrored || '; undecided ' || g.n_undecided || '; players without exactly one true-team copy '
               || g.n_bad_pairs || '; teams reconciling PTS/FGM/3PM/FTM after repair ' || g.n_teams_reconciled || '/2',
           '{VERSION}'
    FROM ta_gate g LEFT JOIN ta_ok o USING (canonical_game_id) ORDER BY 1;
    """)
    _log(con, [("DEMIRROR_RECONCILED_GAMES", "TEAM_REATTRIBUTION", "TEAM_ATTRIBUTION_DEFECT", None, None, None,
                "true-team copies of mirrored box lines in reconciled games", "fact.player_games", restored, reason),
               ("DEMIRROR_RECONCILED_GAMES", "QUARANTINE", "TEAM_ATTRIBUTION_DEFECT", None, None, None,
                "opponent-side copies of mirrored box lines (moved to qa.identity_remediation_quarantine)", "fact.player_games", moved, reason)])


def _repaired_games_sql() -> str:
    return """(SELECT canonical_game_id FROM metadata.team_attribution_resolution WHERE decision = 'REMEDIATED_RECONCILED'
               UNION SELECT cid('G','game','209587'))"""


def _spurious_season_membership(con) -> None:
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE ta_spurious AS
    SELECT DISTINCT s.canonical_player_id, s.canonical_team_id, s.season
    FROM fact.player_season_stats s
    WHERE coalesce(s.games, 0) <= 1
      AND NOT EXISTS (SELECT 1 FROM fact.player_games x WHERE x.canonical_player_id = s.canonical_player_id
                      AND x.season = s.season AND x.canonical_team_id = s.canonical_team_id)
      AND EXISTS (SELECT 1 FROM fact.player_games x WHERE x.canonical_player_id = s.canonical_player_id AND x.season = s.season
                  AND x.canonical_opponent_id = s.canonical_team_id AND x.canonical_game_id IN {_repaired_games_sql()});
    """)
    reason = ("SPURIOUS_SEASON_MEMBERSHIP: season-file echo of a repaired team-attribution defect (<= 1 game on the team, "
              "no box row for it, and the player's box row in the repaired game is on the opponent side)")
    log = []
    for table in ("fact.player_season_stats", "fact.player_season_shooting"):
        n = _quarantine(con, "SPURIOUS_SEASON_MEMBERSHIP", "QUARANTINE", table,
                        "(t.canonical_player_id, t.canonical_team_id, t.season) IN (SELECT * FROM ta_spurious)",
                        "t.source_record_key", "NULL", "t.canonical_team_id", "t.canonical_player_id", "t.season", reason)
        log.append(("SPURIOUS_SEASON_MEMBERSHIP", "QUARANTINE", "TEAM_ATTRIBUTION_DEFECT", None, None, None,
                    "one-game season-file rows on the opponent of a repaired game", table, n, reason))
    n = con.execute("""UPDATE metadata.player_id_map m SET canonical_player_id = NULL, match_method = 'TEAM_ATTRIBUTION_DEFECT',
            match_confidence = 0.0, needs_manual_review = FALSE,
            source_context = source_context || '; v2.4: key exists only because of a mirrored/misattributed box line'
        WHERE m.provider = 'CBBD_STARTER_PACK_TEAM_SEASON_NAME'
          AND (m.canonical_player_id, cid('T','team', m.provider_team_key), m.season) IN (SELECT * FROM ta_spurious)""").fetchone()[0]
    log.append(("SPURIOUS_SEASON_MEMBERSHIP", "QUARANTINE", "TEAM_ATTRIBUTION_DEFECT", None, None, None,
                "(season, team, name) keys created by the defect", "metadata.player_id_map", n, reason))
    _log(con, log)


def _collision_scope(alias_player: str, alias_season: str) -> str:
    return "(" + " OR ".join(f"({alias_player} = cid('P','athlete','{aid}') AND {alias_season} IN ({', '.join(map(str, seasons))}))"
                             for aid, seasons, _, _ in COLLISIONS) + ")"


def _collision_box(con) -> None:
    log = []
    for aid, seasons, rule, ev in COLLISIONS:
        pid = con.execute("SELECT cid('P','athlete', ?)", [aid]).fetchone()[0]
        sl = ", ".join(map(str, seasons))
        n = con.execute(f"""UPDATE fact.player_games SET canonical_player_id = NULL, identity_status = 'QUARANTINED_SOURCE_ID_COLLISION'
                            WHERE canonical_player_id = ? AND season IN ({sl})""", [pid]).fetchone()[0]
        log.append((rule, "QUARANTINE", "SOURCE_ID_COLLISION", aid, pid, None, f"seasons {sl}", "fact.player_games", n, ev))
        for table in ("fact.player_season_stats", "fact.player_season_shooting"):
            n = _quarantine(con, rule, "QUARANTINE", table, f"t.canonical_player_id = '{pid}' AND t.season IN ({sl})",
                            "t.source_record_key", "NULL", "t.canonical_team_id", "t.canonical_player_id", "t.season", ev)
            log.append((rule, "QUARANTINE", "SOURCE_ID_COLLISION", aid, pid, None, f"seasons {sl}", table, n, ev))
        n = con.execute(f"""UPDATE metadata.player_id_map SET canonical_player_id = NULL, match_method = 'SOURCE_ID_COLLISION_QUARANTINE',
                            match_confidence = 0.0, source_context = source_context || '; v2.4: {rule}'
                            WHERE provider = 'CBBD_STARTER_PACK_TEAM_SEASON_NAME' AND canonical_player_id = ? AND season IN ({sl})""",
                        [pid]).fetchone()[0]
        log.append((rule, "QUARANTINE", "SOURCE_ID_COLLISION", aid, pid, None, f"seasons {sl}", "metadata.player_id_map", n, ev))
    _log(con, log)


# ============================================================================ event layer (inside event_build)
def apply_events(con: duckdb.DuckDBPyConnection) -> None:
    """After player_identity.apply_events, before reconciliation / defects."""
    _event_team_not_in_game(con)
    _collision_events(con)
    _stale_roster_rows(con)


def _stale_roster_rows(con) -> None:
    """STALE_ROSTER_MULTI_TEAM: an athlete listed on two team rosters in one season, whose every resolved box row that
    season is for the OTHER team (>= 1 row) and none for this one. The extra row is a roster-snapshot attribution
    defect (typically an offseason transfer left on the old roster); left in place it would make him a 'previous-season
    roster returner' of a team he never played for."""
    reason = ("STALE_ROSTER_MULTI_TEAM: roster lists the athlete on two teams this season; all his resolved box rows this "
              "season are for the other team and none for this one")
    where = """(t.canonical_player_id, t.canonical_team_id, t.season) IN (
        SELECT r.canonical_player_id, r.canonical_team_id, r.season FROM core.rosters r
        WHERE (r.canonical_player_id, r.season) IN (SELECT canonical_player_id, season FROM core.rosters GROUP BY 1, 2
                                                    HAVING count(DISTINCT canonical_team_id) > 1)
          AND NOT EXISTS (SELECT 1 FROM fact.player_games x WHERE x.canonical_player_id = r.canonical_player_id
                          AND x.season = r.season AND x.canonical_team_id = r.canonical_team_id)
          AND EXISTS (SELECT 1 FROM fact.player_games x WHERE x.canonical_player_id = r.canonical_player_id
                      AND x.season = r.season AND x.canonical_team_id <> r.canonical_team_id))"""
    n = _quarantine(con, "STALE_ROSTER_MULTI_TEAM", "QUARANTINE", "core.rosters", where, "t.source_record_key", "NULL",
                    "t.canonical_team_id", "t.canonical_player_id", "t.season", reason)
    _log(con, [("STALE_ROSTER_MULTI_TEAM", "QUARANTINE", "TEAM_ATTRIBUTION_DEFECT", None, None, None,
                "roster rows contradicted by every box row of the season", "core.rosters", n, reason)])


def _event_team_not_in_game(con) -> None:
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ta_ev AS
    WITH x AS (SELECT canonical_game_id, canonical_team_id, 'PBP' AS fam FROM fact.plays WHERE canonical_team_id IS NOT NULL
               UNION ALL SELECT canonical_game_id, canonical_team_id, 'SUBSTITUTION' FROM fact.substitutions
               UNION ALL SELECT canonical_game_id, canonical_team_id, 'LINEUP' FROM fact.lineup_stats
               UNION ALL SELECT canonical_game_id, canonical_team_id, 'SHOT' FROM fact.shots WHERE canonical_team_id IS NOT NULL)
    SELECT x.canonical_game_id, g.season, list(DISTINCT x.fam ORDER BY x.fam) AS fams, count(*) AS n_rows,
           list(DISTINCT x.canonical_team_id ORDER BY x.canonical_team_id) AS foreign_teams
    FROM x JOIN core.games g USING (canonical_game_id)
    WHERE x.canonical_team_id NOT IN (g.home_team_id, g.away_team_id)
    GROUP BY 1, 2;
    """)
    games = con.execute("SELECT canonical_game_id, season, fams, n_rows, foreign_teams FROM ta_ev ORDER BY 1").fetchall()
    for g, season, fams, n, teams in games:
        detail = f"event feed names team(s) {teams} that did not play this game ({n} rows in {fams}); events not re-guessed"
        for fam in ("PBP", "SUBSTITUTION", "LINEUP"):
            con.execute("INSERT INTO qa.source_defects VALUES (?, 'TEAM_ATTRIBUTION_DEFECT', 'ERROR', ?, ?, ?, ?, TRUE, ?)",
                        [fam, g, season, f"game={g}", detail, VERSION])
        con.execute("""INSERT INTO metadata.team_attribution_resolution VALUES (?, ?, 'EVENT_TEAM_NOT_IN_GAME', 'QUARANTINED_EVENTS',
                       ?, 0, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)""", [g, season, n, n, detail, VERSION])


def _collision_events(con) -> None:
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ta_gs AS SELECT canonical_game_id, season FROM core.games""")
    log = []
    for aid, seasons, rule, ev in COLLISIONS:
        pid = con.execute("SELECT cid('P','athlete', ?)", [aid]).fetchone()[0]
        sl = ", ".join(map(str, seasons))
        scope = f"canonical_game_id IN (SELECT canonical_game_id FROM ta_gs WHERE season IN ({sl}))"
        for t, pcol in (("fact.play_participants", "canonical_player_id"), ("fact.play_on_floor", "canonical_player_id"),
                        ("fact.substitutions", "canonical_player_id"), ("fact.shots", "shooter_player_id"),
                        ("fact.shots", "assister_player_id")):
            n = con.execute(f"UPDATE {t} SET {pcol} = NULL WHERE {pcol} = ? AND {scope}", [pid]).fetchone()[0]
            log.append((rule, "QUARANTINE", "SOURCE_ID_COLLISION", aid, pid, None, f"seasons {sl}", f"{t}.{pcol}", n, ev))
        for t, col in (("fact.plays", "participant_player_ids"), ("fact.plays", "on_floor_player_ids"), ("fact.lineup_stats", "player_ids")):
            con.execute(f"""UPDATE {t} SET {col} = list_transform({col}, x -> CASE WHEN x = '{pid}' THEN 'CBBD_ATHLETE:{aid}' ELSE x END)
                            WHERE list_contains({col}, '{pid}') AND {scope}""")
        n = _quarantine(con, rule, "QUARANTINE", "core.rosters", f"t.canonical_player_id = '{pid}' AND t.season IN ({sl})",
                        "t.source_record_key", "NULL", "t.canonical_team_id", "t.canonical_player_id", "t.season", ev)
        log.append((rule, "QUARANTINE", "SOURCE_ID_COLLISION", aid, pid, None, f"seasons {sl}", "core.rosters", n, ev))
    _log(con, log)


# ============================================================================ link layer (end of build_canonical)
LINKS_SQL = f"""
WITH s AS (SELECT canonical_player_id, season, min(canonical_team_id) AS team_id, any_value(source_player_name) AS name
           FROM fact.player_games WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2),
l AS (SELECT canonical_player_id, lag(season) OVER w AS from_season, season AS to_season, lag(team_id) OVER w AS from_team_id,
             team_id AS to_team_id, name
      FROM s WINDOW w AS (PARTITION BY canonical_player_id ORDER BY season)),
e AS (SELECT l.*, to_season - from_season AS gap, from_team_id <> to_team_id AS cross_team,
             EXISTS (SELECT 1 FROM core.transfers x WHERE x.origin_team_id = l.from_team_id AND x.destination_team_id = l.to_team_id
                     AND x.transfer_year BETWEEN l.from_season AND l.to_season - 1
                     AND {norm("x.first_name || ' ' || x.last_name")} = {norm("l.name")}) AS portal_record,
             EXISTS (SELECT 1 FROM core.rosters r WHERE r.canonical_player_id = l.canonical_player_id AND r.canonical_team_id = l.from_team_id
                     AND r.season = l.from_season) AS roster_from,
             EXISTS (SELECT 1 FROM core.rosters r WHERE r.canonical_player_id = l.canonical_player_id AND r.canonical_team_id = l.to_team_id
                     AND r.season = l.to_season) AS roster_to
      FROM l WHERE from_season IS NOT NULL),
c AS (SELECT e.*, count(*) FILTER (WHERE cross_team) OVER (PARTITION BY from_team_id, from_season, to_team_id, to_season) AS cluster_size FROM e)
SELECT * FROM c
"""


def link_reason_sql() -> str:
    """The quarantine reason of a link row of LINKS_SQL (NULL = positive evidence present)."""
    return f"""CASE WHEN gap >= {LONG_GAP_SEASONS} AND NOT portal_record THEN 'LONG_GAP_NO_INDEPENDENT_EVIDENCE'
                    WHEN cross_team AND cluster_size >= {WHOLE_TEAM_CLUSTER_MIN} AND NOT portal_record THEN 'WHOLE_TEAM_CLUSTER_NO_INDEPENDENT_EVIDENCE'
                    WHEN cross_team AND NOT portal_record AND NOT (roster_from AND roster_to) THEN 'CROSS_TEAM_NO_PROVIDER_ROSTER_CONTINUITY'
               END"""


def apply_links(con: duckdb.DuckDBPyConnection) -> int:
    """After all box / event / roster / transfer facts exist (end of build_canonical)."""
    con.execute(f"""INSERT INTO metadata.identity_link_quarantine
        SELECT canonical_player_id, from_season, to_season, from_team_id, to_team_id, reason, '{VERSION}'
        FROM (SELECT *, {link_reason_sql()} AS reason FROM ({LINKS_SQL})) WHERE reason IS NOT NULL ORDER BY ALL""")
    return con.execute("SELECT count(*) FROM metadata.identity_link_quarantine").fetchone()[0]
