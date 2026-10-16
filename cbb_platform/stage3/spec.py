"""Stage 3 frozen specification: scope, folds, subsystem windows, PIT cutoff contract, missingness codes, paths.

Everything a later model stage must agree on lives here, so no other module hard-codes a season or a rule.
"""
from __future__ import annotations

from pathlib import Path

from cbb_platform import config as C, lockbox as _lockbox

STAGE3_VERSION = "stage3-features-1.0.0"
UPSTREAM_DATASET = "cbb_data_v2.4+5f19d31e524a"

# ------------------------------------------------------------------ scope (architect data-scope freeze)
# 2025-26 joins ONLY as an evaluation season, and only under the reviewed, authorized one-time opening (guard v3,
# docs/LOCKBOX_OPENING_PROTOCOL.md §2); folds, subsystem windows and every training range below are unchanged
EVALUATION_SEASONS = (_lockbox.LOCKBOX_SEASON,) if _lockbox.opening_authorized() else ()
MODEL_SEASONS = tuple(range(2014, 2026)) + EVALUATION_SEASONS     # end-year: 2013-14 .. 2024-25 (+ 2025-26 if opened)
LOCKBOX_SEASON = 2026                        # 2025-26: never read
SOURCE_SEASONS = MODEL_SEASONS               # history may come ONLY from these seasons (no silent pre-2013-14 priors)

# ------------------------------------------------------------------ core walk-forward protocol (frozen)
CORE_FOLDS = [
    {"fold": f"FOLD_{i}", "train_seasons": list(range(2014, v)), "validation_season": v}
    for i, v in enumerate(range(2019, 2026), start=1)
]
LOCKBOX = {"season": LOCKBOX_SEASON, "label": "2025-26", "role": "FINAL_UNTOUCHED_HOLDOUT_NOT_A_TUNING_FOLD"}

# ------------------------------------------------------------------ subsystem data windows
SUBSYSTEM_WINDOWS = {
    "core_player_team_game_state": {"seasons": [2014, 2025]},
    "shot_event_model_no_location": {"seasons": [2014, 2025]},
    "shot_location_model": {"seasons": [2022, 2025], "note": "primary high-quality x/y window"},
    "lineup_on_court_interaction": {"seasons": [2024, 2025], "development_train": [2024], "development_validation": [2025]},
    "rotation_substitution": {"seasons": [2024, 2025], "development_train": [2024], "development_validation": [2025]},
}
LOCATION_SEASONS = (2022, 2025)
LINEUP_SEASONS = (2024, 2025)

# ------------------------------------------------------------------ PIT cutoff contract
CUTOFF_RULE = ("prediction_cutoff_utc = scheduled_tip_utc - 1 hour (EXACT tip); start of the US/Eastern game date "
               "(00:00 ET) when the tip is not exact. A source game may contribute only when its "
               "historical_knowable_at (canonical PIT rule HR-1: tip + 4 h, or 06:00 ET next day for inexact tips) "
               "<= prediction_cutoff_utc. Since the target game is knowable only at tip + 4 h it can never contribute "
               "to its own features. CBBD wallclock is UNTRUSTED_MIXED and is never read.")
CUTOFF_OFFSET_HOURS = 1

# ------------------------------------------------------------------ semantic missingness (no global imputation)
MISSING_CODES = ("NO_PRIOR_HISTORY", "UNKNOWN_PARTICIPATION", "SOURCE_NOT_AVAILABLE", "NOT_APPLICABLE",
                 "INSUFFICIENT_SAMPLE", "IDENTITY_UNRESOLVED")

# ------------------------------------------------------------------ participation statuses (player-game examples)
PARTICIPATION_STATUSES = ("PLAYED", "DNP_CONFIRMED", "UNKNOWN_PARTICIPATION", "NOT_ON_ROSTER")

# ------------------------------------------------------------------ deterministic summaries
ROLLING_WINDOWS = (3, 5, 10)
EWM_HALF_LIFE_GAMES = 5.0                    # weight of a game k games back = 0.5 ** (k / 5)
MIN_OBS = {"team": 3, "player": 3, "event": 3, "lineup_pair": 1}

# ------------------------------------------------------------------ paths
MODELING = C.PROJECT_ROOT / "modeling"
STORE = MODELING / "store"                    # DuckDB working stores (gitignored)
FEATURES_DB = STORE / "features.duckdb"
LABELS_DB = STORE / "labels.duckdb"           # a SEPARATE database: feature code never attaches it
FEATURES_PARQUET = MODELING / "features"      # partitioned Parquet (gitignored)
LABELS_PARQUET = MODELING / "labels"          # partitioned Parquet (gitignored); schema doc tracked
FOLDS_DIR = MODELING / "folds"
REGISTRY_JSON = MODELING / "FEATURE_REGISTRY.json"
STAGE3_MANIFEST = MODELING / "STAGE3_MANIFEST.json"


def link_guard(player: str, hist_season: str, target_season: str) -> str:
    """SQL predicate: history of `player` at `hist_season` may be linked to `target_season` (no quarantined link between)."""
    return (f"NOT EXISTS (SELECT 1 FROM src.identity_link_quarantine lq WHERE lq.canonical_player_id = {player} "
            f"AND {hist_season} <= lq.from_season AND {target_season} >= lq.to_season)")


def season_sql(col: str = "season") -> str:
    return f"{col} BETWEEN {MODEL_SEASONS[0]} AND {MODEL_SEASONS[-1]}"
