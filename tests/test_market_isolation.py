"""Market isolation: no sportsbook field may appear in the flagship basketball-feature namespace."""
import re

from tests.conftest import scalar

# Sportsbook vocabulary. (A bare "line" token is not used: `line_number` is a snapshot row index.)
MARKET_WORDS = re.compile(r"spread|moneyline|odds|vendor|sportsbook|bookmaker|over_under|overunder|"
                          r"total_value|price|wager|\bbet|opened_at|market|vig\b|juice", re.I)
BENCHMARK_ID_OR_LINEAGE = {"bdl_odds_id", "bdl_game_id", "canonical_game_id", "source_updated_at",
                           "raw_sha256", "received_at", "processed_at"}


def test_detector_catches_every_sportsbook_column():
    """Positive control: the detector must flag every pricing column of the benchmark odds tables."""
    import duckdb
    from cbb_platform import config as C
    con = duckdb.connect()
    con.execute(C.SCHEMA_SQL.read_text())
    for table in ("benchmark.game_odds", "benchmark.opening_odds"):
        cols = [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]
        missed = [c for c in cols if c not in BENCHMARK_ID_OR_LINEAGE and not MARKET_WORDS.search(c)]
        assert missed == [], f"{table}: detector misses {missed}"


def test_flagship_namespace_has_no_market_columns(db):
    cols = db.execute("SELECT table_name, column_name FROM duckdb_columns() WHERE schema_name = 'flagship'").fetchall()
    assert cols, "flagship namespace is empty"
    offenders = [(t, c) for t, c in cols if MARKET_WORDS.search(c)]
    assert offenders == []


def test_flagship_views_never_reference_benchmark(db):
    views = db.execute("SELECT view_name, sql FROM duckdb_views() WHERE schema_name = 'flagship'").fetchall()
    assert len(views) >= 10
    assert [v for v, sql in views if "benchmark" in sql.lower()] == []


def test_flagship_contains_only_views(db):
    assert scalar(db, "SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'flagship'") == 0


def test_market_tables_live_only_in_benchmark(db):
    rows = db.execute("SELECT DISTINCT schema_name, table_name FROM duckdb_columns() WHERE schema_name IN "
                      "('core', 'fact', 'availability', 'flagship') ").fetchall()
    cols = db.execute("SELECT schema_name, table_name, column_name FROM duckdb_columns() WHERE schema_name IN "
                      "('core', 'fact', 'availability', 'flagship')").fetchall()
    assert rows
    assert [(s, t, c) for s, t, c in cols if MARKET_WORDS.search(c)] == []
    assert scalar(db, "SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'benchmark' "
                      "AND table_name IN ('game_odds', 'opening_odds')") == 2


def test_synthetic_fixtures_cannot_reach_flagship(db):
    sql = scalar(db, "SELECT sql FROM duckdb_views() WHERE schema_name = 'flagship' AND view_name = 'availability_events'")
    assert "is_synthetic_fixture" in sql
