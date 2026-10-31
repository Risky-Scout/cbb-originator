"""Walk-forward constants. Architect directive 2026-10-31 (CBB ORIGINATOR - 2026-27 DAILY WALK-FORWARD RETRAINING):
DAILY_WALK_FORWARD_REFIT = ENABLED. The ARCHITECTURE stays frozen (JM1_FINAL); the PARAMETERS are re-estimated every
morning by the SAME approved procedure on newer legally available data. Nothing here selects, tunes or searches.

Every operational value that a daily run depends on lives here; none of them reaches a forecast except through the
frozen fitting functions it calls.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from cbb_platform.prod import config as PC

WALK_FORWARD_VERSION = "cbb-wf-daily-refit-1.0.0"
DAILY_WALK_FORWARD_REFIT = True
AUTOMATIC_ARCHITECTURE_CHANGES = False          # no predictor / family / objective / weighting / search change, ever
AUTOMATIC_CHALLENGER_PROMOTION = False
ARCHITECTURE = PC.ARCHITECTURE                  # JM1_FINAL

# ------------------------------------------------------------------ chronology
TZ = ZoneInfo("America/New_York")               # DST handled by zoneinfo (06:00 local is 11:00Z in EST, 10:00Z in EDT)
DAILY_REFIT_TIME = os.environ.get("CBB_WF_REFIT_TIME", "06:00")
PRODUCTION_SEASON = PC.PRODUCTION_SEASON        # 2026-27
OPENING_THROUGH = PC.TRAINING_THROUGH           # 2025-26: the opening-day model's supervised + historical state
OPENING_DATE = date(2026, 11, 1)                # first 2026-27 slate; scored by OPENING_DAY_BASELINE
SEASON_FIRST_DATE = date.fromisoformat(PC.PRODUCTION_SEASON_START)
SEASON_LAST_DATE = date.fromisoformat(PC.PRODUCTION_SEASON_END)
CHECKPOINT = PC.VALIDATED_PREDICTION_CUTOFF     # TIP_MINUS_1H: the only validated prediction checkpoint

# ------------------------------------------------------------------ the approved procedure's own season windows (frozen)
MARGINAL_FIRST_SEASON = 2014                    # marginal.FIRST_SEASON: stat / minutes training rows 2013-14 ..
PARTICIPATION_FIRST_SEASON = 2024               # marginal.PARTICIPATION_FIRST_SEASON
TEAM_FIRST_SEASON = 2014                        # phase3.FIRST_TEAM_SEASON: environment team-games 2013-14 ..
GLM_FIRST_SEASON = 2023                         # phase3.OOS_FIRST: GLM on Phase 1B OOS rows 2022-23 ..
RESID_FIRST_SEASON = 2024                       # residual basis = seasons 2024 .. T (SELECT (2024,), CONFIRM (2024, 2025),
                                                # FINAL (2024, 2025, 2026)): season F with structure through F-1

# ------------------------------------------------------------------ training eligibility (a game, at the daily cutoff)
TRAINING_UNIVERSE = "DI_VS_DI"                  # Stage 3 example universe (2025-26: 5,752 of 6,300 played games)
FINAL_STATUS = "final"                          # cbbd_box_route.FINAL_STATUS (normalized provider status)

# ------------------------------------------------------------------ storage (CBB-only namespace; portable to the Droplet)
STORE_ROOT = Path(os.environ.get("CBB_WF_ROOT", str(PC.PROD_ROOT / "walkforward"))) / "cbb-originator"
VERSION_PREFIX = "cbb-jm1-2026-27-"
OPENING_KIND = "OPENING_DAY_BASELINE"
DAILY_KIND = "DAILY_WALK_FORWARD"

# ------------------------------------------------------------------ resources (shared Droplet discipline)
THREAD_ENV = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
              "VECLIB_MAXIMUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
# DuckDB threads stay at the frozen default (config.DUCKDB_THREADS = 2): the frozen artifacts were fit with it


def refit_cutoff(day: date) -> pd.Timestamp:
    """The daily production training cutoff of ET date `day`, in UTC (timezone-aware; DST-correct)."""
    hh, mm = (int(x) for x in DAILY_REFIT_TIME.split(":"))
    return pd.Timestamp(datetime.combine(day, time(hh, mm), tzinfo=TZ)).tz_convert("UTC")


def version_id(day: date) -> str:
    return f"{VERSION_PREFIX}{day.strftime('%Y%m%d')}"


def threads_ok() -> dict:
    return {k: os.environ.get(k) == v for k, v in THREAD_ENV.items()}
