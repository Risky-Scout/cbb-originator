# Model eligibility policy

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN**

## 1. Rule

Every subsystem that is fit, calibrated or evaluated trains **only** on the rows listed in its own **eligibility manifest**.
A generic "the feature exists" flag (non-NULL column, `*_status = 'OK'`, presence in a feature table) may **never**
silently select training rows. Missing inputs are handled by the subsystem's declared missingness mechanism, never by
dropping rows ad hoc.

## 2. Required manifests (one per subsystem)

| manifest id | subsystem | expected primary window (from `spec.SUBSYSTEM_WINDOWS`, to be confirmed in the manifest) |
|---|---|---|
| `ELIG_PARTICIPATION` | active / starter / participation | 2013-14 .. 2024-25 |
| `ELIG_MINUTES` | full minutes distribution | 2013-14 .. 2024-25 |
| `ELIG_GAME_ENVIRONMENT` | pace / possessions / team strength | 2013-14 .. 2024-25 (possession-level fields need A2 possessions) |
| `ELIG_SHOT_OPPORTUNITY` | usage / shot attempts / shot family | 2013-14 .. 2024-25 (trusted PBP only) |
| `ELIG_EVENT_HEADS` | turnover, shot/foul branch, make/miss, assist, block, rebound, steal, FT | trusted PBP + accepted possessions |
| `ELIG_LOCATION` | shot location | 2021-22 .. 2024-25 (valid x/y) |
| `ELIG_LINEUP_INTERACTIONS` | lineup main effects, teammate / opponent interactions, rotation | 2023-24 .. 2024-25, `accepted` stints only |

A later subsystem (talent, calibration, joint engine) adds its own manifest before its first fit.

## 3. Manifest contents (machine-readable JSON + rendered markdown, versioned, hashed)

| field | content |
|---|---|
| `manifest_id`, `version`, `created_at`, `code_sha` | identity |
| `dataset_version` | e.g. `cbb_data_v2.4+5f19d31e524a` (must be MODEL_READY for a promotable model) |
| `feature_version` | Stage 3 / checkpoint store version and fingerprint |
| `seasons` | explicit list (end-years); lockbox 2026 never present |
| `eligible_games` | rule + count + sha256 of the sorted id list |
| `eligible_player_games` | rule + count + sha256 (where applicable) |
| `required_sources` | relations and their trust filters (e.g. `flagship.plays` excl. PBP defects) |
| `missingness_mechanism` | per input: semantic missing code (`spec.MISSING_CODES`), whether the row stays (with indicator) or is excluded, and the stated assumption (MCAR / MAR-given-X / informative) |
| `quarantine_rules` | identity quarantine (`identity_status <> 'RESOLVED'`, `metadata.identity_link_quarantine`), PBP quarantine (4,138 score-mismatch games), event team-attribution quarantine, reconstruction class |
| `identity_requirements` | e.g. "target player-game must have a canonical player id; cross-season inputs must not cross a quarantined link" |
| `pit_fidelity_requirement` | permitted availability classes per checkpoint (PIT v2.0 §3.1) |
| `sample_counts` | rows per season and fold (train / validation), events where applicable |
| `exclusions_report` | rows removed per rule per season. **Every exclusion is counted** |
| `selection_bias_reference` | link to the bias analysis when exclusions are non-random (e.g. `docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md`) |

## 4. Gates

1. A model artifact records the `manifest_id` + hash it was trained on (production contract §3).
2. A test recomputes each manifest's row hash from the frozen rule and fails on drift.
3. Training code receives the manifest's id list. It cannot filter on its own.
4. No manifest may admit rows from the lockbox season, `SOURCE_RECEIPT_UNKNOWN` / `UNUSABLE_FOR_HISTORICAL_PIT` inputs,
   or quarantined identities.
5. Changing a manifest's rule creates a new version. Results are never compared across manifest versions without
   stating it.
