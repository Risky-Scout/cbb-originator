# PBP quarantine selection-bias analysis plan

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN (plan only)**. The analysis is not run here. No
event model is trained.

## 1. Population

The **4,138** games whose PBP final score does not match the official score (`qa.source_defects`, family `PBP`, reason
`FINAL_SCORE_MISMATCH`, excluded from `flagship.plays` and every PBP-derived trusted view) are compared with the included
games. Universe: `flagship.played_games` seasons 2013-14 .. 2024-25, split by:

* **excluded** — PBP present, FINAL_SCORE_MISMATCH (also counted separately: SCORE_DECREASE, INCOMPLETE_PERIODS);
* **included** — PBP present and trusted;
* **no PBP** — no PBP payload (reported separately; not part of the quarantine).

## 2. Comparisons (included vs excluded)

| dimension | measure |
|---|---|
| season | exclusion rate per season |
| conference | rate per conference of each team and per conference matchup class (in-conf / non-conf) |
| team | rate per team-season; flag teams whose exclusion rate is > 3 × the season rate |
| pace | official possessions (team box `possessions_source`) distribution: mean, quantiles, KS distance |
| score | total points distribution |
| margin | absolute final margin distribution; close-game (≤ 5) share |
| overtime | OT share |
| neutral site | neutral share |
| tournament state | conference tournament / NCAA / NIT / CBI / other postseason share (`season_type`, `tournament`) |
| provider / era | where identifiable: CBBD source era (plays/date vs plays/game payload family, payload year), PBP-coverage era |

## 3. Statistics

* Per dimension: rate difference with a 95 % binomial interval; standardized mean difference (SMD) for continuous
  measures; KS statistic.
* A logistic **propensity** model of exclusion on all dimensions jointly. It is descriptive only, never used to impute.
* Pre-declared materiality thresholds: |SMD| ≥ 0.10 on pace / score / margin, or a 2-percentage-point rate difference
  in tournament / OT / neutral state, is **material**. These are frozen here, before the analysis.

## 4. Consequences (decided before seeing results)

1. If nothing is material, event / possession models use included games only, and their eligibility manifests cite
   this analysis.
2. If something is material, the affected eligibility manifests must either (a) reweight by the inverse exclusion
   propensity in evaluation reporting, or (b) restrict claims to the included population, stated in the manifest. No
   event sequence is ever imputed from box totals.
3. Box-level (non-event) models are unaffected. They use box rows of every played game.

## 5. Outputs

`data/qa/pbp_quarantine_selection_bias.json` + rendered markdown; the propensity-model coefficients; the frozen
thresholds; the commit SHA. Lockbox excluded.
