# Bitemporal checkpoint schema

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN (design only)**. These tables are NOT built yet.
The Phase A2 rebuild implements them. **Policy:** `POINT_IN_TIME_POLICY_v2_0.md`.

## 1. Time axes

| column | axis | meaning | NULL allowed |
|---|---|---|---|
| `effective_at` / `effective_to` | valid time | when the fact is true in the real world (e.g. "ruled out from 2026-11-03") | yes, when the source does not say |
| `observed_at` | valid time (provider claim) | the provider's own observation/update time (`source_updated_at` in v1.2), only when unambiguous | yes |
| `source_received_at` | transaction time | when **our** system received the bytes. It is the only factual knowability time | **only** for non-`PROSPECTIVE_EXACT` rows |
| `superseded_at` | transaction time | when a later received version replaced this one. NULL = current | yes |
| `policy_available_from` | policy time | the instant a named HR / policy rule treats the row as knowable (retrospective data) | NULL for `PROSPECTIVE_EXACT` |
| `policy_rule` | — | `HR1_TIP_PLUS_4H`, `HR1_NEXT_DAY_0600_ET`, `SEASON_OPEN_BOUNDARY`, `HR3_SEASON_COMPLETE`, … | with `policy_available_from` |
| `availability_class` | — | PIT v2.0 §3 class | **never** |
| `checkpoint_fidelity` | — | per checkpoint row (§3) | **never** |

The knowability used by the legality rule is
`coalesce(source_received_at, policy_available_from)`, **conditional on** the class being permitted (PIT v2.0 §3.1).
A NULL knowability with a non-permitted class means illegal. It never falls back to an invented default.

## 2. Tables (schema `pit`, canonical database)

```sql
-- Any mutable observation (availability, roster membership, schedule-adjacent facts, provider ratings).
CREATE TABLE pit.observation (
    observation_id        VARCHAR PRIMARY KEY,      -- sha256(provider | raw sha256 | locator)
    entity_type           VARCHAR NOT NULL,         -- PLAYER | TEAM | GAME | PLAYER_TEAM_SEASON
    entity_id             VARCHAR NOT NULL,         -- canonical id (or provider token if unresolved)
    attribute             VARCHAR NOT NULL,         -- e.g. availability_status, roster_membership
    value_raw             VARCHAR,                  -- verbatim source value
    value_normalized      VARCHAR,
    effective_at          TIMESTAMPTZ, effective_to TIMESTAMPTZ,
    observed_at           TIMESTAMPTZ,
    source_received_at    TIMESTAMPTZ,
    superseded_at         TIMESTAMPTZ,
    policy_available_from TIMESTAMPTZ, policy_rule VARCHAR,
    availability_class    VARCHAR NOT NULL,
    source_system VARCHAR NOT NULL, raw_reference VARCHAR NOT NULL, ingestion_version VARCHAR NOT NULL,
    CHECK (availability_class IN ('PROSPECTIVE_EXACT','DATE_KNOWN','PRESEASON_ASSUMED','RETROSPECTIVE_FINAL_SCHEDULE',
                                  'SOURCE_RECEIPT_UNKNOWN','UNUSABLE_FOR_HISTORICAL_PIT')),
    CHECK ((availability_class = 'PROSPECTIVE_EXACT') = (source_received_at IS NOT NULL)),
    CHECK (policy_available_from IS NULL OR policy_available_from >= TIMESTAMPTZ '2000-01-01 00:00:00+00')
);

-- Schedule versions (replaces the single retrospective row of core.game_schedule_versions for checkpoint use).
CREATE TABLE pit.schedule_version (
    canonical_game_id VARCHAR NOT NULL, version_no INTEGER NOT NULL,
    scheduled_tip_utc TIMESTAMPTZ, tip_time_quality VARCHAR NOT NULL,   -- EXACT | DATE_ONLY | TBD_FLAG
    game_date_et DATE NOT NULL,
    observed_at TIMESTAMPTZ, source_received_at TIMESTAMPTZ, superseded_at TIMESTAMPTZ,
    availability_class VARCHAR NOT NULL,      -- PROSPECTIVE_EXACT | RETROSPECTIVE_FINAL_SCHEDULE
    raw_reference VARCHAR NOT NULL,
    PRIMARY KEY (canonical_game_id, version_no)
);

-- One row per (game, checkpoint).
CREATE TABLE pit.checkpoint (
    checkpoint_id VARCHAR PRIMARY KEY,               -- sha256(game | code | anchor version)
    canonical_game_id VARCHAR NOT NULL,
    checkpoint_code VARCHAR NOT NULL,                -- T24H | T6H | T1H | T30M | T10M
    offset_minutes INTEGER NOT NULL,                 -- 1440 | 360 | 60 | 30 | 10
    anchor_version_no INTEGER NOT NULL, anchor_tip_utc TIMESTAMPTZ NOT NULL,
    cutoff_utc TIMESTAMPTZ NOT NULL,
    cutoff_rule VARCHAR NOT NULL,                    -- TIP_MINUS_OFFSET | DATE_ONLY_ET_MIDNIGHT_MINUS_OFFSET | LATE_FIRED_AT_RECEIPT
    checkpoint_fidelity VARCHAR NOT NULL,            -- PROSPECTIVE_EXACT | LATE_FIRED | RETROSPECTIVE_FINAL_SCHEDULE | DATE_ONLY_ANCHOR
    season INTEGER NOT NULL, fold_id VARCHAR,        -- all five checkpoints of a game share the fold
    UNIQUE (canonical_game_id, checkpoint_code)
);

-- Feature values materialized per checkpoint (wide tables may replace this; the columns are the contract).
CREATE TABLE pit.checkpoint_feature_value (
    checkpoint_id VARCHAR NOT NULL, entity_id VARCHAR NOT NULL, feature_name VARCHAR NOT NULL,
    value DOUBLE, missing_code VARCHAR,              -- spec.MISSING_CODES
    feature_as_of TIMESTAMPTZ,                       -- max knowability of inputs; < cutoff_utc
    min_input_class VARCHAR NOT NULL,                -- weakest-link class of all inputs
    n_inputs INTEGER NOT NULL,
    PRIMARY KEY (checkpoint_id, entity_id, feature_name)
);
```

## 3. Checkpoint fidelity values

| value | when |
|---|---|
| `PROSPECTIVE_EXACT` | anchored on a live-received schedule version, fired on time (PIT v2.0 §4.1) |
| `LATE_FIRED` | the governing version arrived after its own cutoff; fired at receipt, never back-dated (§4.2) |
| `RETROSPECTIVE_FINAL_SCHEDULE` | historical, EXACT final tip, no version history (§5.1) |
| `DATE_ONLY_ANCHOR` | DATE_ONLY / TBD tip: anchor 00:00 ET of the game date |

## 4. Mapping of existing canonical columns (Phase A2 migration)

| current column | v2.0 interpretation |
|---|---|
| `historical_knowable_at` on game facts (HR-1) | `policy_available_from`, rule HR1_*, class `DATE_KNOWN` |
| `received_at` on Starter Pack rows | registration time of the file. It is **not** a knowability time; class per HR rule |
| `received_at` on CBBD/BDL API rows fetched in 2026 about past seasons | `SOURCE_RECEIPT_UNKNOWN` for mutable attributes; `DATE_KNOWN` for completed-game facts via HR-1 |
| `availability.events.received_at` | `source_received_at`, class `PROSPECTIVE_EXACT` |
| `core.game_schedule_versions` (`is_retrospective = TRUE`) | `pit.schedule_version` row, class `RETROSPECTIVE_FINAL_SCHEDULE` |
| Stage 3 `available_from = 1900-01-01` (preseason evidence) | `policy_available_from = SEASON_OPEN_BOUNDARY`, class `PRESEASON_ASSUMED` (nonconformance to fix) |
| `core.rosters` current-season rows, roster attributes | `SOURCE_RECEIPT_UNKNOWN` (already research-only) |
| CBBD `wallclock` | `UNUSABLE_FOR_HISTORICAL_PIT` |

## 5. Invariants (tests to be written with the build)

1. `cutoff_utc = anchor_tip_utc − offset` except `LATE_FIRED` (= receipt instant).
2. Five rows per in-scope game. All five share `season` and `fold_id`.
3. `feature_as_of < cutoff_utc` for every non-NULL value; the target game is never an input.
4. `min_input_class` is permitted for the checkpoint by PIT v2.0 §3.1.
5. No `source_received_at` on any non-prospective row. No timestamp before 2000-01-01 in any time column.
6. Rebuild determinism: identical inputs produce identical `checkpoint_id`s and value fingerprints.
7. Lockbox: no checkpoint row for season 2026 until `docs/LOCKBOX_OPENING_PROTOCOL.md` is executed.
