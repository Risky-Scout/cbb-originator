"""Production (shadow) constants. Every season, cutoff, hash and ceiling the production layer relies on lives here."""
from __future__ import annotations

from pathlib import Path

from cbb_platform import config as C
from cbb_platform.stage3 import spec as STAGE3

PRODUCTION_VERSION = "cbb-v2-jm1-final-shadow-1.0.0"

# ------------------------------------------------------------------ seasons (canonical end-year)
PRODUCTION_SEASON = 2027                 # 2026-27: the only season this layer predicts
TRAINING_THROUGH = 2026                  # 2025-26: last season with outcomes in any fitted parameter (never refit here)
HISTORY_FIRST_SEASON = STAGE3.SOURCE_SEASONS[0]   # 2013-14: the Stage 3 history window starts here (frozen)

# ------------------------------------------------------------------ frozen artifacts (immutable; verified before scoring)
MODEL_DIR = C.PROJECT_ROOT / "modeling" / "player_props_v2"
ARTIFACTS = {
    "marginal": ("modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json",
                 "bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f"),
    "jm1_final": ("modeling/player_props_v2/jm1_final_through_2025_26.json",
                  "b932fa2155de77f95e2c2afb15b45b22ecf8fa1e49a2d175b7da7cbfca2428f9"),
    "residual_j2": ("modeling/player_props_v2/jm1_final_residual_dependence_through_2025_26.json",
                    "e42548675fd3c0e363cf26a464d133f384ae659d99b603105510f0937b6570f1"),
    "production_manifest": ("modeling/player_props_v2/jm1_final_production_manifest_2026_27.json",
                            "7d83e9cf4e9c68478e5e71e57ccdcf204b8d3267c4de98d7ea86ee8d5ddcbcab"),
}
FROZEN_COMMIT = "e1353e5d0b1d653f53e4ee138c3bd9241934e32a"
FROZEN_TAG = "cbb-v2-jm1-final-2026-27"
ARCHITECTURE = "JM1_FINAL"
ROLLBACK_MODEL = "S0 = Phase 1B marginals + Phase 2 J2 (modeling/player_props_v2/dependence_J2.json); not wired for scoring"

# ------------------------------------------------------------------ prediction cutoff (validated, not invented)
# The ONLY cutoff every training row, OOS forecast and the 2025-26 confirmation were built at: Stage 3 CUTOFF_RULE
# (scheduled tip - 1 h for EXACT tips, 00:00 US/Eastern of the game date otherwise). Validated retrospectively on
# final schedules; prospective (live schedule-version) fidelity is a monitoring item, not a validated fact.
VALIDATED_PREDICTION_CUTOFF = "TIP_MINUS_1H"
VALIDATED_PREDICTION_CUTOFF_RULE = STAGE3.CUTOFF_RULE
CUTOFF_OFFSET_HOURS = STAGE3.CUTOFF_OFFSET_HOURS
CUTOFF_VALIDATION = "RETROSPECTIVE_ONLY (Stage 3 store; T24H/T6H/T30M/T10M never built)"

# ------------------------------------------------------------------ readiness / wagering
READINESS_STATES = ("BLOCKED", "SHADOW_ONLY", "PUBLIC_READY")
MAX_READINESS_STATE = "SHADOW_ONLY"      # this phase: PUBLIC_READY is never returned
WAGERING_ENABLED = False
QUOTE_MAX_AGE_SECONDS = 20 * 60          # PRODUCTION_SCORING_..._POLICY_v1_0.md: 20 min freshness at T1H
STATE_MAX_AGE_HOURS = 36                 # a state older than this (vs prediction_as_of) is STALE_STATE

# ------------------------------------------------------------------ simulation
SIMS = 20_000                            # manifest random_seed_policy.draws_per_game (JM.SIMS)

# ------------------------------------------------------------------ local paths (all gitignored)
PROD_ROOT = C.PROJECT_ROOT / "data" / "prod"
STATE_ROOT = PROD_ROOT / "state"
LEDGER_PATH = PROD_ROOT / "ledger" / "shadow_ledger.jsonl"
RUNS_ROOT = PROD_ROOT / "runs"
BUNDLE_ROOT = PROD_ROOT / "bundle"
HISTORY_CANONICAL = C.PROJECT_ROOT / "data" / "lockbox_2025_26" / "canonical" / "cbb.duckdb"   # frozen, read-only
PROPOSED_PUBLIC_PATH = "/tools/odds-scanner/predictions/basketball/ncaab/player-props/"         # NOT approved

# ------------------------------------------------------------------ 2026-27 production data feed (CBBD; production-only path)
# Separate from the research lockbox guard (cbb_platform/lockbox.py stays unchanged and keeps refusing 2026-27). These
# values are constants, not switches: the production path admits exactly this season and nothing else.
PRODUCTION_SEASON_START = "2026-08-01"   # first US/Eastern date of 2026-27 (lockbox.season_of_date boundary: Aug 1)
PRODUCTION_SEASON_END = "2027-07-31"
PRESEASON_CLASS_YEAR = PRODUCTION_SEASON - 1   # CBBD recruiting / portal `year` 2026 feeds season 2026-27
FEED_VERSION = "cbb-prod-cbbd-feed-1.0.0"
PROD_CACHE_ROOT = PROD_ROOT / "cache"                        # never data/raw: the research canonical build reads data/raw
CBBD_CACHE_RAW = PROD_CACHE_ROOT / "raw"                     # raw_store layout: raw/cbbd/<endpoint_slug>/<file>
CBBD_REQUEST_LEDGER = PROD_CACHE_ROOT / "cbbd_production_requests.jsonl"
SYNC_LOG = PROD_ROOT / "sync" / "sync_log.jsonl"
MAX_LIVE_REQUESTS_PER_STAGE = 20         # architect limit for this phase: a stage that needs more STOPs first
CBBD_QUOTA_RESERVE = 1_000               # never start a call when fewer than this remain on the account
SCHEDULE_MAX_AGE_HOURS = 36              # schedule snapshot older than this at prediction time: STALE_STATE
ROSTER_MAX_AGE_HOURS = 7 * 24            # roster snapshot older than this at prediction time: STALE_ROSTER
ODDS_PROVIDER_COVERAGE_VERIFIED = False  # NCAAB player-prop coverage of The Odds API not yet checked (no call made)

BASE_STATS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV")
COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"),
          "STOCKS": ("STL", "BLK")}
MARKETS = BASE_STATS + tuple(COMBOS)


def rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(C.PROJECT_ROOT))
