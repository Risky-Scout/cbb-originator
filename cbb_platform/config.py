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

import os as _os_path
# CBB_DUCKDB_PATH lets targeted tests run against a scratch copy before the single full rebuild.
DUCKDB_PATH = Path(_os_path.environ.get("CBB_DUCKDB_PATH", str(CANONICAL / "cbb.duckdb")))
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

# ---------------------------------------------------------------------------
# Machine resource cap (architect, Stage 2.1): heavy DuckDB work runs on TWO threads.
# ---------------------------------------------------------------------------
import os as _os
DUCKDB_THREADS = int(_os.environ.get("CBB_DUCKDB_THREADS", "2"))
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_v, "2")

# Stage 2.1: seasons whose game identity was audited; the trusted game relation (flagship.games) covers exactly these.
# Lockbox-scoped extension (incident docs/lockbox/LOCKBOX_INCIDENT_2025_26_TRUSTED_WINDOW.md, architect ruling
# 2026-10-16): 2025-26 joins the window ONLY under the authorized one-time opening, the condition of the scoring-enable
# patch; otherwise the window is exactly 2004-05 .. 2024-25. The same generic rules apply to every season in it.
from cbb_platform import lockbox as _lockbox
AUDITED_SEASONS = (2005, _lockbox.LOCKBOX_SEASON if _lockbox.opening_authorized() else 2025)
_FROZEN_WINDOW_SQL = "BETWEEN 2005 AND 2025"          # the window as written in schema.sql (frozen text)


def schema_sql() -> str:
    """schema.sql with its trusted-game window rendered from AUDITED_SEASONS: byte-identical to the file when closed."""
    text = SCHEMA_SQL.read_text()
    assert text.count(_FROZEN_WINDOW_SQL) == 2, "schema.sql trusted-window text changed"
    return text.replace(_FROZEN_WINDOW_SQL, f"BETWEEN {AUDITED_SEASONS[0]} AND {AUDITED_SEASONS[1]}")
