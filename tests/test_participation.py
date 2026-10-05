"""Step 7 — participation / DNP truth states (directive v2 §10, §24)."""
from tests.conftest import scalar

STATES = ("PLAYED", "CONFIRMED_DNP", "UNKNOWN_PARTICIPATION")


def test_only_the_three_truth_states_exist(db):
    assert scalar(db, f"SELECT count(*) FROM fact.participation WHERE participation_state NOT IN {STATES}") == 0


def test_missing_box_row_is_never_dnp_or_played(db):
    """A player with no box-score row is UNKNOWN — absence of a row is not evidence of a DNP."""
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE candidate_source <> 'BOX_SCORE_ROW'
                         AND participation_state <> 'UNKNOWN_PARTICIPATION'""") == 0


def test_confirmed_dnp_requires_an_explicit_roster_universe(db):
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE participation_state = 'CONFIRMED_DNP'
                         AND candidate_source <> 'ROSTER'""") == 0
    if scalar(db, "SELECT count(*) FROM core.rosters") == 0:
        assert scalar(db, "SELECT count(*) FROM fact.participation WHERE participation_state = 'CONFIRMED_DNP'") == 0


def test_played_requires_positive_evidence(db):
    assert scalar(db, """SELECT count(*) FROM fact.participation pa JOIN fact.player_games p
        ON pa.source_record_key = p.source_record_key
        WHERE pa.participation_state = 'PLAYED' AND NOT (p.minutes > 0 OR p.starter OR
              greatest(p.points, p.fga, p.fta, p.reb, p.ast, p.stl, p.blk, p.tov, p.pf) > 0)""") == 0


def test_zero_minute_no_stat_rows_stay_unknown(db):
    """Whole-minute rounding cannot separate a listed DNP from a <30-second appearance."""
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE minutes = 0 AND NOT starter
        AND participation_evidence = 'BOX_ROW_ZERO_MINUTES_NO_STATS_AMBIGUOUS'
        AND participation_state <> 'UNKNOWN_PARTICIPATION'""") == 0


def test_starter_is_orthogonal_and_null_without_box_row(db):
    assert scalar(db, "SELECT count(*) FROM fact.participation WHERE candidate_source = 'BOX_SCORE_ROW' "
                      "AND starter IS NULL") == 0
    assert scalar(db, "SELECT count(*) FROM fact.participation WHERE candidate_source <> 'BOX_SCORE_ROW' "
                      "AND (starter IS NOT NULL OR minutes IS NOT NULL)") == 0


def test_every_box_row_has_exactly_one_participation_row(db):
    assert scalar(db, "SELECT count(*) FROM fact.player_games") == scalar(
        db, "SELECT count(*) FROM fact.participation WHERE candidate_source = 'BOX_SCORE_ROW'")


def test_interruption_only_with_evidence(db):
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE interruption_flag
                         AND interruption_evidence <> 'FOULED_OUT_PF_GE_5'""") == 0
    assert scalar(db, "SELECT count(*) FROM fact.participation WHERE interruption_flag = FALSE") == 0


def test_no_medical_reason_is_inferred(db):
    cols = [r[0] for r in db.execute("SELECT column_name FROM duckdb_columns() WHERE schema_name = 'fact' "
                                     "AND table_name = 'participation'").fetchall()]
    assert not [c for c in cols if any(w in c for w in ("injury", "medical", "reason", "diagnosis"))]


def test_minutes_band_matches_minutes(db):
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE minutes IS NOT NULL AND minutes_band IS DISTINCT FROM
        CASE WHEN minutes = 0 THEN 'ZERO' WHEN minutes BETWEEN 1 AND 4 THEN 'MINIMAL' ELSE 'REGULAR' END""") == 0


def test_absence_question_is_answerable(db):
    """The later model must be able to ask: "when Player A did not participate, how were teammates'
    minutes redistributed?" This runs that query shape (no modeling) and checks both arms exist."""
    rows = db.execute("""
        WITH a AS (   -- one 2024 regular with >= 20 PLAYED games and >= 1 game without a resolved box row
            SELECT canonical_player_id, canonical_team_id FROM fact.participation WHERE season = 2024
            GROUP BY 1, 2 HAVING count(*) FILTER (WHERE participation_state = 'PLAYED') >= 20
               AND count(*) FILTER (WHERE candidate_source = 'SEASON_STATS_MEMBERSHIP') >= 1
            ORDER BY 1 LIMIT 1),
        a_games AS (
            SELECT pa.canonical_game_id, pa.participation_state = 'PLAYED' AS a_played
            FROM fact.participation pa JOIN a USING (canonical_player_id, canonical_team_id) WHERE pa.season = 2024)
        SELECT g.a_played, avg(t.minutes) AS teammate_avg_minutes, count(*) AS teammate_rows
        FROM a_games g
        JOIN fact.player_games t ON t.canonical_game_id = g.canonical_game_id
        JOIN a ON t.canonical_team_id = a.canonical_team_id AND t.canonical_player_id <> a.canonical_player_id
        GROUP BY 1""").fetchall()
    assert {r[0] for r in rows} == {True, False}
    assert all(r[2] > 0 for r in rows)


def test_zero_minute_starter_is_flagged_never_played_or_dnp(db):
    """Architect ruling (Stage 1 closure): a starter with 0 minutes is a contradictory line. It is
    FLAGGED_ZERO_MIN_STARTER / UNKNOWN_PARTICIPATION — never a PLAYED or CONFIRMED_DNP training label."""
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE candidate_source = 'BOX_SCORE_ROW'
                         AND starter AND minutes = 0 AND (participation_state <> 'UNKNOWN_PARTICIPATION'
                              OR participation_evidence NOT LIKE 'FLAGGED_ZERO_MIN_STARTER%')""") == 0
    assert scalar(db, "SELECT count(*) FROM fact.participation WHERE participation_evidence = 'BOX_ROW_STARTER_ZERO_MINUTES'") == 0
    # every 0-minute starter box line is accounted for by the flag (none silently re-labelled)
    assert scalar(db, "SELECT count(*) FROM fact.player_games WHERE starter AND minutes = 0") == scalar(
        db, "SELECT count(*) FROM fact.participation WHERE participation_evidence LIKE 'FLAGGED_ZERO_MIN_STARTER%'")


def test_flagged_states_are_excluded_from_confirmed_labels(db):
    assert scalar(db, """SELECT count(*) FROM fact.participation WHERE participation_evidence LIKE 'FLAGGED%'
                         AND participation_state IN ('PLAYED', 'CONFIRMED_DNP')""") == 0
