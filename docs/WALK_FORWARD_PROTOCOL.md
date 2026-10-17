# Walk-forward protocol (Stage 3, frozen)

Machine-readable: `modeling/folds/core_walk_forward.json` (definition) and `modeling/folds/fold_manifests.json`
(per-fold counts and SHA-256 hashes of game examples, player-game examples, labels and per-table feature fingerprints).
Upstream dataset: `cbb_data_v2.2+95dd9c2fa5b7`. Seasons are end-years (2019 = 2018-19).

| fold | train | validation |
|---|---|---|
| FOLD_1 | 2013-14 .. 2017-18 | 2018-19 |
| FOLD_2 | 2013-14 .. 2018-19 | 2019-20 |
| FOLD_3 | 2013-14 .. 2019-20 | 2020-21 |
| FOLD_4 | 2013-14 .. 2020-21 | 2021-22 |
| FOLD_5 | 2013-14 .. 2021-22 | 2022-23 |
| FOLD_6 | 2013-14 .. 2022-23 | 2023-24 |
| FOLD_7 | 2013-14 .. 2023-24 | 2024-25 |

**Lockbox: 2025-26.** It is the final untouched holdout, NOT a tuning fold. No model or feature choice may be selected
from lockbox performance, and no completed 2025-26 outcome is read anywhere in the pipeline.

## Subsystem windows

| subsystem | seasons | development split |
|---|---|---|
| core player / team / game state | 2013-14 .. 2024-25 | core folds |
| shot event model (no location) | 2013-14 .. 2024-25 | core folds |
| shot location model | 2021-22 .. 2024-25 (primary high-quality x/y window) | core folds restricted to the window |
| lineup / on-court interaction | 2023-24 .. 2024-25 | train 2023-24, validate 2024-25 (`LINEUP_ROTATION_DEV`) |
| rotation / substitution | 2023-24 .. 2024-25 | train 2023-24, validate 2024-25 (`LINEUP_ROTATION_DEV`) |

Exact lineup / on-court history does not exist before 2023-24 in CBBD and is never pretended.

## Membership rule
A row belongs to a fold part by its `season` alone (deterministic). Features of every row are point-in-time (see
`CANONICAL_SCHEMA.md` and `modeling/FEATURE_REGISTRY.json`): within-season history only from games knowable before
the row's `prediction_cutoff_utc`; prior-season features only from the complete previous season inside the window.
Seasons before 2013-14 are outside the modeling window and are never used as features.
