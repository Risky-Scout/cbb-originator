"""Step 10 — data QA on the BUILT canonical database (real Starter Pack + real BDL smoke data).

Accounting identities are recomputed here INDEPENDENTLY of build_canonical.accounting_rules.
Source rows that violate an identity are not corrected; they must carry `accounting_issues`.
The strict identity must then hold on 100% of unflagged rows, and the flagged share must
stay under a frozen threshold (documented in CANONICAL_SCHEMA.md §7).
"""
import pytest

from tests.conftest import scalar

# Frozen implementation-level QA thresholds (CANONICAL_SCHEMA.md §7). Measured rates at freeze:
# player-games 96 / 2,368,206 (0.004%); team-games 137 / 235,612 (0.058%).
MAX_FLAGGED_SHARE = {"fact.player_games": 1e-4, "fact.team_games": 1e-3}

IDENTITIES = {
    "PTS = 2*2PM + 3*3PM + FTM": "{a}.points = 2 * {a}.two_pm + 3 * {a}.three_pm + {a}.ftm",
    "REB = ORB + DRB": "{a}.reb = {a}.orb + {a}.drb",
    "FGM = 2PM + 3PM": "{a}.fgm = {a}.two_pm + {a}.three_pm",
    "FGA = 2PA + 3PA": "{a}.fga = {a}.two_pa + {a}.three_pa",
    "makes <= attempts": "{a}.fgm <= {a}.fga AND {a}.two_pm <= {a}.two_pa AND {a}.three_pm <= {a}.three_pa AND {a}.ftm <= {a}.fta",
}
GAME_TABLES = ["fact.player_games", "fact.team_games"]
SEASON_TABLES = ["fact.player_season_stats", "fact.team_season_stats"]


@pytest.mark.parametrize("table", GAME_TABLES + SEASON_TABLES)
@pytest.mark.parametrize("name", list(IDENTITIES))
def test_identity_holds_on_every_unflagged_row(db, table, name):
    cond = IDENTITIES[name].format(a="x")
    bad = scalar(db, f"SELECT count(*) FROM {table} x WHERE x.accounting_issues IS NULL AND NOT ({cond})")
    assert bad == 0, f"{bad} unflagged rows in {table} violate {name}"


@pytest.mark.parametrize("table", GAME_TABLES + SEASON_TABLES)
def test_every_violation_is_flagged(db, table):
    any_violation = " OR ".join(f"NOT ({c.format(a='x')})" for c in IDENTITIES.values())
    tov = "tov_total" if "team" in table else "tov"
    negative = (f"least(x.points, x.fgm, x.fga, x.two_pm, x.two_pa, x.three_pm, x.three_pa, x.ftm, x.fta, "
                f"x.orb, x.drb, x.reb, x.ast, x.stl, x.blk, x.{tov}, x.pf) < 0")
    unflagged = scalar(db, f"SELECT count(*) FROM {table} x WHERE x.accounting_issues IS NULL "
                           f"AND ({any_violation} OR {negative})")
    assert unflagged == 0


@pytest.mark.parametrize("table", GAME_TABLES)
def test_flagged_share_within_frozen_threshold(db, table):
    n, flagged = db.execute(f"SELECT count(*), count(accounting_issues) FROM {table}").fetchone()
    assert flagged / n <= MAX_FLAGGED_SHARE[table], f"{flagged}/{n} flagged in {table}"


def test_no_negative_counts_on_unflagged_player_rows(db):
    assert scalar(db, """SELECT count(*) FROM fact.player_games WHERE accounting_issues IS NULL AND
        least(points, fgm, fga, two_pm, two_pa, three_pm, three_pa, ftm, fta, orb, drb, reb, ast, stl, blk, tov, pf,
              minutes) < 0""") == 0


def test_counting_columns_are_whole_numbers_in_source(db):
    """as_int() would silently round a fractional count; prove none exist in the player/team game sources."""
    for view, cols in {
        "staging.sp_player_game_stats": ["points", "minutes", "fieldGoals_made", "fieldGoals_attempted", "rebounds_total",
                                         "assists", "turnovers", "fouls", "freeThrows_made", "threePointFieldGoals_made"],
        "staging.sp_team_game_stats": ["teamStats_points_total", "teamStats_fieldGoals_made", "teamStats_rebounds_total",
                                       "teamStats_turnovers_total", "teamStats_fouls_total", "gameMinutes"],
    }.items():
        cond = " OR ".join(f"(TRY_CAST({c} AS DOUBLE) <> floor(TRY_CAST({c} AS DOUBLE)))" for c in cols)
        assert scalar(db, f"SELECT count(*) FROM {view} WHERE {cond}") == 0, view


# --------------------------------------------------------------------------- keys and joins
def test_no_duplicate_canonical_player_game_keys(db):
    assert scalar(db, "SELECT count(*) - count(DISTINCT player_game_key) FROM fact.player_games") == 0
    assert scalar(db, """SELECT count(*) FROM (SELECT canonical_game_id, canonical_player_id FROM fact.player_games
                         WHERE canonical_player_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1)""") == 0


def test_canonical_ids_unique(db):
    for table, col in [("core.games", "canonical_game_id"), ("core.teams", "canonical_team_id"),
                       ("core.players", "canonical_player_id"), ("core.conferences", "canonical_conference_id")]:
        assert scalar(db, f"SELECT count(*) - count(DISTINCT {col}) FROM {table}") == 0, table


@pytest.mark.parametrize("child,col,parent,pcol", [
    ("fact.player_games", "canonical_game_id", "core.games", "canonical_game_id"),
    ("fact.player_games", "canonical_team_id", "core.teams", "canonical_team_id"),
    ("fact.player_games", "canonical_opponent_id", "core.teams", "canonical_team_id"),
    ("fact.player_games", "canonical_player_id", "core.players", "canonical_player_id"),
    ("fact.team_games", "canonical_game_id", "core.games", "canonical_game_id"),
    ("fact.team_games", "canonical_team_id", "core.teams", "canonical_team_id"),
    ("fact.participation", "canonical_game_id", "core.games", "canonical_game_id"),
    ("fact.participation", "canonical_player_id", "core.players", "canonical_player_id"),
    ("fact.player_season_stats", "canonical_player_id", "core.players", "canonical_player_id"),
    ("fact.player_season_stats", "canonical_team_id", "core.teams", "canonical_team_id"),
    ("fact.team_season_stats", "canonical_team_id", "core.teams", "canonical_team_id"),
    ("core.games", "home_team_id", "core.teams", "canonical_team_id"),
    ("core.games", "away_team_id", "core.teams", "canonical_team_id"),
    ("core.games", "home_conference_id", "core.conferences", "canonical_conference_id"),
    ("core.games", "canonical_venue_id", "core.venues", "canonical_venue_id"),
    ("core.team_seasons", "canonical_team_id", "core.teams", "canonical_team_id"),
    ("benchmark.bdl_games", "canonical_game_id", "core.games", "canonical_game_id"),
    ("benchmark.bdl_player_games", "canonical_player_id", "core.players", "canonical_player_id"),
])
def test_foreign_keys_resolve(db, child, col, parent, pcol):
    orphans = scalar(db, f"SELECT count(*) FROM {child} c WHERE c.{col} IS NOT NULL AND NOT EXISTS "
                         f"(SELECT 1 FROM {parent} p WHERE p.{pcol} = c.{col})")
    assert orphans == 0


def test_player_game_team_matches_game_participants(db):
    assert scalar(db, """SELECT count(*) FROM fact.player_games p JOIN core.games g USING (canonical_game_id)
                         WHERE p.canonical_team_id NOT IN (g.home_team_id, g.away_team_id)
                            OR p.canonical_opponent_id NOT IN (g.home_team_id, g.away_team_id)""") == 0


def test_unresolved_identities_are_never_given_ids(db):
    assert scalar(db, "SELECT count(*) FROM fact.player_games WHERE identity_status <> 'RESOLVED' "
                      "AND canonical_player_id IS NOT NULL") == 0
    assert scalar(db, "SELECT count(*) FROM fact.player_games WHERE identity_status = 'RESOLVED' "
                      "AND canonical_player_id IS NULL") == 0


MAP_ID_COLUMN = {"metadata.player_id_map": "canonical_player_id", "metadata.team_id_map": "canonical_team_id",
                 "metadata.game_id_map": "canonical_game_id"}


@pytest.mark.parametrize("table", list(MAP_ID_COLUMN))
def test_every_non_exact_identity_has_method_confidence_and_review_flag(db, table):
    assert scalar(db, f"""SELECT count(*) FROM {table} WHERE match_method <> 'ANCHOR' AND
                          (match_confidence IS NULL OR needs_manual_review IS NULL OR source_context IS NULL)""") == 0
    # UNRESOLVED means no id and zero confidence — never an invented id.
    assert scalar(db, f"SELECT count(*) FROM {table} WHERE match_method = 'UNRESOLVED' "
                      f"AND (match_confidence <> 0 OR {MAP_ID_COLUMN[table]} IS NOT NULL)") == 0


def test_d1_unresolved_players_are_flagged_for_review(db):
    assert scalar(db, """SELECT count(*) FROM metadata.player_id_map
                         WHERE match_method = 'UNRESOLVED' AND source_context LIKE '%; D-I team-season%'
                           AND NOT needs_manual_review""") == 0


# --------------------------------------------------------------------------- dates and minutes
def test_dates_fall_inside_their_season(db):
    """CBBD season Y runs from Aug 1 of Y-1 to Jul 31 of Y (US/Eastern dates)."""
    assert scalar(db, """SELECT count(*) FROM core.games WHERE game_date_et < make_date(season - 1, 8, 1)
                         OR game_date_et > make_date(season, 7, 31)""") == 0
    assert scalar(db, "SELECT count(*) FROM core.games WHERE season NOT BETWEEN 1949 AND 2025") == 0


def test_starter_pack_contains_no_lockbox_season(db):
    """2025-26 (CBBD season 2026) is the untouched lockbox; none of it may be in canonical facts."""
    for t in ("core.games", "fact.player_games", "fact.team_games", "fact.player_season_stats"):
        assert scalar(db, f"SELECT count(*) FROM {t} WHERE season >= 2026") == 0, t


def test_minutes_reasonable(db):
    assert scalar(db, """SELECT count(*) FROM fact.team_games WHERE game_minutes IS NOT NULL
                         AND (game_minutes < 40 OR (game_minutes - 40) % 5 <> 0 OR game_minutes > 80)""") == 0
    assert scalar(db, "SELECT count(*) FROM fact.player_games WHERE accounting_issues IS NULL "
                      "AND (minutes < 0 OR minutes > 80)") == 0
    # Team player-minutes ~ 200 + 25 per OT: >= 98% of team-games with a box score within 5 minutes.
    n, ok = db.execute("""SELECT count(*), count(*) FILTER (WHERE abs(s.mins - 5 * t.game_minutes) <= 5)
        FROM fact.team_games t JOIN (SELECT canonical_game_id, canonical_team_id, sum(minutes) mins
                                     FROM fact.player_games GROUP BY 1, 2) s USING (canonical_game_id, canonical_team_id)
        """).fetchone()
    assert ok / n >= 0.98, f"{ok}/{n}"


def test_overtime_count_consistent(db):
    assert scalar(db, """SELECT count(*) FROM fact.team_games t JOIN core.games g USING (canonical_game_id)
                         WHERE t.overtime_count IS DISTINCT FROM g.overtime_count""") == 0


# --------------------------------------------------------------------------- reconciliation
def test_team_points_equal_official_game_score(db):
    assert scalar(db, """SELECT count(*) FROM fact.team_games t JOIN core.games g USING (canonical_game_id)
        WHERE t.points <> CASE WHEN t.canonical_team_id = g.home_team_id THEN g.home_points ELSE g.away_points END""") == 0


def test_player_box_reconciliation_flag_is_correct_and_high(db):
    wrong = scalar(db, """SELECT count(*) FROM fact.team_games t JOIN (SELECT canonical_game_id, canonical_team_id,
            sum(points) pts, sum(minutes) mins FROM fact.player_games GROUP BY 1, 2) s USING (canonical_game_id, canonical_team_id)
        WHERE t.player_box_reconciles IS DISTINCT FROM (s.pts = t.points AND abs(s.mins - 5 * t.game_minutes) <= 5)""")
    assert wrong == 0
    n, ok = db.execute("SELECT count(player_box_reconciles), count(*) FILTER (WHERE player_box_reconciles) "
                       "FROM fact.team_games").fetchone()
    assert ok / n >= 0.98, f"{ok}/{n}"


def test_player_turnovers_reconcile_to_team_minus_team_turnovers(db):
    """Measured source semantics: team tov_total includes team turnovers (teamTotal)."""
    n, ok = db.execute("""SELECT count(*), count(*) FILTER (WHERE s.tov = t.tov_total - t.team_turnovers)
        FROM fact.team_games t JOIN (SELECT canonical_game_id, canonical_team_id, sum(tov) tov FROM fact.player_games
        GROUP BY 1, 2) s USING (canonical_game_id, canonical_team_id) WHERE t.player_box_reconciles""").fetchone()
    assert ok / n >= 0.99, f"{ok}/{n}"


def test_bdl_disagreements_reported_not_overwritten(db):
    if not scalar(db, "SELECT count(*) FROM qa.reconciliation"):
        pytest.skip("no BDL smoke data in this build")
    missing = scalar(db, """SELECT count(*) FROM qa.reconciliation r WHERE NOT r.values_match AND NOT EXISTS (
        SELECT 1 FROM qa.exceptions e WHERE e.exception_type = 'BDL_MISMATCH'
          AND e.entity_key = r.entity_type || ':' || r.canonical_game_id || ':' || coalesce(r.canonical_team_id, '')
                             || ':' || coalesce(r.canonical_player_id, '') || ':' || r.stat)""")
    assert missing == 0
    # CBBD values in reconciliation are the canonical values (nothing was overwritten by BDL).
    assert scalar(db, """SELECT count(*) FROM qa.reconciliation r JOIN fact.player_games p
        ON p.canonical_game_id = r.canonical_game_id AND p.canonical_player_id = r.canonical_player_id
        WHERE r.entity_type = 'PLAYER_GAME' AND r.stat = 'points' AND r.cbbd_value <> p.points""") == 0


def test_tip_times_match_independent_bdl_times(db):
    """Leakage guard: canonical tip time (games.csv read as UTC) must equal BDL's independent UTC tip."""
    n, bad = db.execute("""SELECT count(*), count(*) FILTER (WHERE g.scheduled_tip_utc <> b.game_date_utc)
        FROM benchmark.bdl_games b JOIN core.games g USING (canonical_game_id)
        WHERE g.tip_time_quality = 'EXACT'""").fetchone()
    if n == 0:
        pytest.skip("no BDL games in this build")
    assert bad == 0, f"{bad}/{n} tip times disagree with BDL"


# --------------------------------------------------------------------------- lineage and PIT
LINEAGE = ("source_system", "source_record_key", "ingestion_version", "received_at", "processed_at")


def _tables_with(db, column):
    return [f"{s}.{t}" for s, t in db.execute(
        "SELECT schema_name, table_name FROM duckdb_columns() WHERE column_name = ? "
        "AND schema_name IN ('core','fact') ", [column]).fetchall()]


def test_lineage_present_on_every_canonical_row(db):
    for table in _tables_with(db, "source_record_key"):
        cond = " OR ".join(f"{c} IS NULL" for c in LINEAGE)
        assert scalar(db, f"SELECT count(*) FROM {table} WHERE {cond}") == 0, table


def test_pit_timestamps_valid(db):
    for table in _tables_with(db, "historical_knowable_at"):
        assert scalar(db, f"SELECT count(*) FROM {table} WHERE historical_knowable_at IS NULL") == 0, table
    for table in ("fact.player_games", "fact.team_games", "fact.participation"):
        # an outcome can never be knowable before its game starts
        assert scalar(db, f"SELECT count(*) FROM {table} WHERE event_time IS NOT NULL "
                          "AND historical_knowable_at <= event_time") == 0, table
    # end-of-season aggregates: knowable no earlier than the last game of their season
    for table in ("fact.player_season_stats", "fact.team_season_stats", "fact.player_season_shooting",
                  "fact.team_season_shooting"):
        assert scalar(db, f"""SELECT count(*) FROM {table} s JOIN (SELECT season, max(historical_knowable_at) k
                              FROM fact.team_games GROUP BY 1) g USING (season)
                              WHERE s.historical_knowable_at < g.k""") == 0, table
    assert scalar(db, "SELECT count(*) FROM fact.player_games WHERE processed_at < received_at") == 0


def test_schedule_versions_are_marked_retrospective(db):
    """Starter Pack schedules were not observed prospectively; they may not pose as authentic snapshots."""
    assert scalar(db, "SELECT count(*) FROM core.game_schedule_versions WHERE source_system = 'CBBD_STARTER_PACK' "
                      "AND (NOT is_retrospective OR observed_at IS NOT NULL)") == 0
