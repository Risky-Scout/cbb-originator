# Stage 08 benchmark populations (prepared in Stage 07; Stage 08 is not started)

**Frozen evaluation contract**
* Evaluation cutoff: **2026-11-27T20:40:23Z**.
* Evaluation information state: `S20261127T201352Z-8eeab0c20aef`, the latest state version with created_at and source_cutoff ≤ the cutoff (source_cutoff 20:10:28Z).
* Universe: every 2026-27 game with scheduled tip ≤ the cutoff (`results/universe_games.csv`, one row per game; `results/universe_players.parquet`, one row per game × player).
* A game is **COMPLETE** when it has a result, player box and team box in the evaluation state.
* A later Stage 08 cutoff is a new contract and must be recorded the same way.

None of the four populations may be combined into a single performance claim.

## A. HISTORICAL_PRODUCTION: immutable forecasts as originally generated

| member | count | source |
|---|---|---|
| pregame player PMF rows (prediction_as_of < tip) | 1,014,192 (0 invalid) | every `ops/forecasts/*/*/pmfs.parquet` and `ops/ticks/*/pmfs.parquet` |
| games with a prospective forecast | 833; 810 of 1,134 complete DI_VS_DI | `universe_games.csv` |
| forecast player-games in complete games | 22,997, all with all 12 stats | `universe_players.parquet` |

Labels Stage 08 must keep on these rows (never rewrite them):
* `model_version` / fit kind. 218,640 rows come from the **frozen opening model**: 204 ticks before the 2f4a384 tick-path fix (2026-11-08 → 2026-11-24 04:10Z). They are labelled FROZEN_OPENING_BASELINE and are not daily-refit forecasts.
* **Stage 05 defective INITIAL rows**: 268 player-games, 34 with p_play ≥ 0.5 (`../2026-11-27-stage5/results/initial_gap_rows.csv`).
* **Pre-C4 game forecasts**: every persisted game PMF row so far. These are joint 1.0.0 with FINAL mean basis, and the files carry no `joint_version` column (Stage 06 adds it).
* **Pre-C3 player forecasts**: the CTX1 ledger had a mixed incumbent tag (Stage 06 C3).
* Lineage per artifact is in `results/historical_lineage.csv`:
  * code commit recorded on 591 artifacts and derived from the tick release chronology on 257;
  * AMBIGUOUS on 50, where a deploy fell between ticks; UNVERIFIED on 22.
  * engine_id is recorded on 140 and otherwise time-attributed by `prod.engine.legacy_engine`.

## B. CORRECTED_CANDIDATE_REPLAY: corrected incumbent on historically legitimate information

* **Eligible:** complete games whose pregame cutoff (tip − 1 h) has an **exact retained information state**. That is **1,430 of 1,443 complete games** in total, and **1,127 of 1,134 complete DI_VS_DI**. The other 7 tipped before the first retained version, 2026-11-02 18:45Z. Counts: `universe_summary.json`.
* **Information contract:** `cbb_platform/prod/replay_contract.py`.
  * Use the state as-of T; a later state is refused.
  * RotoWire events count only where collected_at < T.
  * Curated CSVs are taken as deployed at T.
  * The CTX ledger is read with `replay=True`.
  * The model must satisfy training_cutoff ≤ T and promoted_at ≤ T.
* **Key:** code_commit + engine_id + joint_version / mean_basis + state_version + model_version + `runtime_flags`.
* Replays are written to a separate store. They never overwrite A.

## C. MARKET_OVERLAP: matched event, line, price, timestamp, settlement

| member | count |
|---|---|
| frozen player market evaluations | 3,036 rows, 598 player-games, 514 games |
| frozen game market evaluations | 349 games |

These evaluations are a sub-population of A, priced at the decision observation. Stage 06 verified the evaluation mathematics independently.

## D. DIAGNOSTIC_RECONSTRUCTION: incomplete provenance or prior selection exposure

* 7 complete DI games whose pregame cutoff precedes the first retained state.
* All lockbox seasons ≤ 2025-26. They are rule-only; true acquisition times are unknown.
* **Selection exposure.** Every 2026-27 game through 2026-11-26 informed at least one promoted correction:
  * RA2 (11-22), TOVT1 (11-24), DEV1 / CTX1 (11-25), PI2 / PS2 (11-26);
  * the 11-21 / 11-22 selection audits.

  A replay of the corrected engine on those dates is in-sample for those components. A clean chronological holdout starts with games after the last promotion date of the evaluated engine.
* Curated-status rows whose replay availability is bounded by deploy times derived from the first production run on each commit (`results/curated_provenance.csv`). That bound is conservative, not exact.

## Missing and excluded (complete DI_VS_DI games without a prospective forecast: 324)

| reason | games |
|---|---|
| BEFORE_DROPLET_PRODUCTION_FORECASTS (tips 11-01 … 11-08; GitHub-era shadow forecasts are not retained as PMFs) | 300 |
| NEVER_SLATED_PREGAME (no pregame run receipt names the game. Tip dates: 11-10 ×15, 11-14 ×7, 11-18 ×2, 11-12, 11-15, 11-24. That is 27 games of all kinds, 24 of them DI_VS_DI) | 24 |

Pre-cutoff games that are not COMPLETE (42) are excluded from every population, with their reason:
* MISSING_RESULT: 25
* IN_PROGRESS_AT_CUTOFF: 13
* POSTPONED_OR_CANCELLED: 4
