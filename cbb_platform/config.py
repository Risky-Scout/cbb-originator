"""Paths, versions, and Starter Pack file families shared by every Stage 1 module.

Everything that a human might need to change to re-point the platform lives here,
so that no other module hard-codes a path.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The CBB Starter Pack is an IMMUTABLE, read-only upstream mount. Code in this
# package only ever opens it for reading. It is never copied into data/raw
# (that would duplicate ~780 MB for no lineage benefit); instead its SHA-256
# manifest is recorded in data/manifests/ and every canonical row carries the
# source file path it came from.
STARTER_PACK_ROOT = PROJECT_ROOT.parent / "CBB Starter Pack" / "starter_pack"
STARTER_PACK_DATA = STARTER_PACK_ROOT / "data"

DATA = PROJECT_ROOT / "data"
RAW = DATA / "raw"              # immutable external payloads we fetched/received
STAGING = DATA / "staging"      # lossless provider-shaped Parquet (all source columns)
CANONICAL = DATA / "canonical"  # provider-independent Parquet + the DuckDB file
MANIFESTS = DATA / "manifests"  # hashes, source manifest, data versions
QA = DATA / "qa"                # audit/coverage/QA artifacts

DUCKDB_PATH = CANONICAL / "cbb.duckdb"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")

# ---------------------------------------------------------------------------
# Versions (bump when transformation semantics change)
# ---------------------------------------------------------------------------
INGESTION_VERSION = "stage1-1.0.0"
SCHEMA_VERSION = "canonical-1.0.0"

# ---------------------------------------------------------------------------
# Starter Pack physical families and their candidate keys.
# Keys are *candidates* to be measured by the audit, not assumed to be unique.
# ---------------------------------------------------------------------------
STARTER_FAMILIES = {
    # family: (glob relative to STARTER_PACK_DATA, candidate key columns)
    "games": ("games.csv", ["id"]),
    "teams": ("teams.csv", ["id"]),
    "conferences": ("conferences.csv", ["id"]),
    "player_game_stats": ("player_game_stats/*.csv", ["game_id", "athleteId"]),
    "player_season_stats": ("player_season_stats/*.csv", ["season", "teamId", "athleteId"]),
    "player_season_shooting_stats": ("player_season_shooting_stats/*.csv", ["season", "teamId", "athleteId"]),
    "team_game_stats": ("team_game_stats/*.csv", ["gameId", "teamId"]),
    "team_season_stats": ("team_season_stats/*.csv", ["season", "teamId"]),
    "team_season_shooting_stats": ("team_season_shooting_stats/*.csv", ["season", "teamId"]),
}

# Columns that identify teams / players / dates in each family (used by the audit).
FAMILY_TEAM_COLS = {
    "games": ["home_team_id", "away_team_id"],
    "teams": ["id"],
    "player_game_stats": ["team_id"],
    "player_season_stats": ["teamId"],
    "player_season_shooting_stats": ["teamId"],
    "team_game_stats": ["teamId"],
    "team_season_stats": ["teamId"],
    "team_season_shooting_stats": ["teamId"],
}
FAMILY_PLAYER_COL = {
    "player_game_stats": "athleteId",
    "player_season_stats": "athleteId",
    "player_season_shooting_stats": "athleteId",
}
FAMILY_DATE_COL = {
    "games": "start_date",            # naive US/Eastern wall time (measured, see audit doc)
    "player_game_stats": "start_date",  # explicit UTC offset
    "team_game_stats": "startDate",     # explicit UTC offset
}
