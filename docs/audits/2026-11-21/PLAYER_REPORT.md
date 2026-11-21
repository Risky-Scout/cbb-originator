# CBB player-market calibration forensics (sections A, B): 2026-11-21

Read-only over copies of production state (Droplet `prod/ops`: performance parquet, frozen evaluations, tick
projections / actuals / PMFs, run model_inputs, latest run's PIT feature DB queried read-only). No production write.

## A1. Reproduction
`ops/performance/cbb_market_performance.parquet` reproduces the audit exactly: 832 graded (WIN/LOSS; 10 VOID_DNP),
mean model p 0.5789, hit 0.5180; UNDER 567 (0.6401 / 0.5132), OVER 265 (0.4479 / 0.5283); PTS 162 (0.6287 / 0.4259,
ROI -20.1%); AST 0.6163 / 0.4836; PRA 0.6473 / 0.5115; 65_PLUS 31 (0.7330 / 0.5161); 60_PLUS 15 (0.6216 / 0.4000);
11-20: 232 (0.6209 / 0.4698).

## A2. PMF-to-line pricing: NO DEFECT
All 832 graded probabilities re-priced from the exact stored PMF (`pricing.price_line`): max |diff| = 0.0, max |PMF mean
- projection| = 7e-15.

## A3. The graded set is a max-EV selection against the market
Each evaluation's graded side = the best-EV offer (`market_eval.best_offer`). On the 679 two-sided rows:

| quantity | value |
|---|---|
| model p (graded side) | 0.579 |
| market de-vigged fair p (same side) | 0.502 |
| hit rate | 0.518 |
| model - market | **+0.122** |
| market - actual | -0.006 |

Hit rate tracks the MARKET in every disagreement bin (model-market 0-5pp: hit 0.464 / mkt 0.491; 5-10: 0.487 / 0.502;
10-15: 0.511 / 0.518; 15-20: 0.575 / 0.498; 20+: 0.522 / 0.498). The model's disagreement with the market carries no
usable information on these players; the calibration gap on the graded side ~= the model-market disagreement.
Side-neutral Brier (P(OVER) vs outcome), same rows: model 0.263, market 0.247.

## A4. Population calibration (not market-selected): PMFs are well calibrated
12,592 played player-games (latest pre-cutoff projection, 11-01..11-20): minutes residual +0.02 (se 0.06);
PTS -0.11, REB +0.05, AST +0.00, PRA -0.05. 5,315 played player-games with the official PMF (11-15..11-20):
variance ratio (realized MSE / PMF variance) PTS 1.008, AST 1.020, PRA 0.995, REB 1.030; 50% / 80% coverage within a
few points of nominal; PIT means 0.475-0.50. => No global MEAN_BIAS, no VARIANCE_BIAS, no PMF_CALIBRATION defect.

## A5. Proven sub-population defect: rate compression for high-exposure / market-listed starters (PRIOR_SHRINKAGE)
* Slope of actual on projection (population): PTS 1.04-1.06, AST 1.10-1.12, PRA 1.06-1.08 (se ~0.015-0.02):
  projections are compressed; TOP5 under-, BENCH over-projected (TOP5 PTS +0.33, BENCH -0.45; t 3.6 / 6.9).
* Market-listed players (164 player-games, 11-17..11-20; all their stats, so not side-selected): PTS +1.43 (se 0.51),
  AST +0.41 (se 0.14, ratio 1.19), PRA +1.72 (se 0.61); minutes +0.63 (se 0.49, ratio 1.023) -> the miss is the
  PER-MINUTE RATE (PTS x1.10, AST x1.17), not minutes (MINUTES_BIAS rejected), not p_play (DNP rows are voided).
* Rate trace for the 144 with prior-season history (PIT season-to-date + prior finals from the latest run's feature DB):

| per minute | prior season | current to date | recency blend | model 1B | model JM1 | realized |
|---|---|---|---|---|---|---|
| PTS | 0.4286 | 0.4702 | 0.4306 | 0.4242 | 0.4151 | **0.4601** |
| AST | 0.0788 | 0.0871 | 0.0783 | 0.0760 | 0.0744 | **0.0891** |
| REB | 0.1659 | 0.1734 | 0.1671 | 0.1658 | 0.1633 | 0.1497 |

  Current-season minutes are only 18.8% of the recency-weighted history (half-life 1 season: prior seasons 0.5 /
  0.25 / 0.125), so 81% of the rate comes from last season although 64% of these players improved. Residual on the
  (current - history) rate gap x min_mu: PTS slope 0.63 (se 0.18), AST 0.42 (se 0.17) -> current evidence is
  under-weighted. Same mechanism as the pre-launch audit `modeling/player_props_v2/JM1_FINAL_HIGH_EXPOSURE_ROOT_CAUSE.md`
  (PRIOR_SEASON_BLEND -0.86/-0.95 pts at min_mu >= 30), larger in November because current minutes are a small share.
* Historically reproducible (OOS v1ref store, 3 seasons): early-season (d0-14) PTS ratio 1.03 / 1.025 / 1.047, slope > 1
  in every season; WARM top quintiles +0.1..+0.5 PTS.

## A6. Classification
* PRIMARY: **OTHER_PROVEN_CAUSE = MARKET_DISAGREEMENT_SELECTION.** The graded probability is the side the model
  disagrees with the market on most; the market is better informed on market-listed players, so realized ~= market and
  the graded-side probability is inflated by ~the disagreement (+12pp). Not a pricing or PMF construction bug.
* CONTRIBUTING (proven, mechanism traced): **PRIOR_SHRINKAGE** (fixed half-life-1 prior-season blend + no
  current-season sample-size adaptation) -> per-minute rates of improving starters low -> model below the line ->
  UNDER preference (68% of graded rows UNDER; PTS/PRA UNDER proj-line -1.5 / -2.7). Sized below (B): ~2pp of the gap.
* Rejected: MEAN_BIAS (global), VARIANCE_BIAS, MINUTES_BIAS, PMF_CALIBRATION. ROLE_LAG: not separable from the
  rate blend with the evidence available (minutes / roles are right; it's the rate).
* REB: mean fine (realized rate below model), UNDER calibration fine (0.630 / 0.622): consistent with the diagnosis.

## A7. Why 11-20 deteriorated to -15.1
Not a model change: model-market disagreement was flat (11-18 +12.5, 11-19 +11.1, 11-20 +11.7pp); population
residuals on 11-20 were normal (PTS -0.15). Decomposition: -15.1 = -11.7 (disagreement selection) - 3.4 (the market
itself missed by 3.4pp that day, ~1 se at n=232). The headline swing vs 11-19 (-3.1) is composition: on 11-19, 27% of
graded rows were one-sided defensive / combo OVER props (PA, RA, STL, STOCKS, TOV; low model p, high hit) that dilute
the average; on 11-20 there were none (fair NaN 0%), so 86% of rows were UNDER on two-sided markets.

## B. Challenger (research only; NOT deployed)
| | |
|---|---|
| CURRENT | rate = (H_t + k prior_pos)/(H_min + k), H = cur + sum_j 0.5^(j/1.0) h_j (half-life 1) |
| CHALLENGER | same formula, per-stat half-life hl (one parameter); k, position prior, minutes, dispersion, PMF construction unchanged |
| DEVELOPMENT | F2024 (true OOS, state through 2022-23) |
| SELECTION | rule fixed before reading F2026: per stat, hl in {1, .75, .5, .35, .25} minimizing F2024+F2025 log loss (ALL rows) -> PTS 0.35, AST 0.5, REB 0.5, FG3M 0.75 |
| FROZEN/OOS | F2026 (2025-26, state through 2024-25): log loss PTS -0.0047, AST -0.0012, REB -0.0008, FG3M -0.0002; RPS and Brier improve in all three folds for all four; min_mu>=30 PTS log loss -0.0082/-0.0057/-0.0074 |
| PROSPECTIVE_EXPECTATION | approximate re-price of the live graded PTS/AST rows: UNDER p 0.644 -> 0.621 (PTS), 0.650 -> 0.634 (AST); Brier 0.293 -> 0.282 / 0.273 -> 0.266. Fixes ~10% of the gap; the rest is A6 primary. |

Verdict: **NOT SELECTED for deployment.** Proper scores improve in every fold, but calibration-in-the-large degrades on
low-exposure rows (PTS mean error -0.03/+0.07/+0.02 -> +0.19/+0.23/+0.21: current-season rates of small samples
overshoot); FG3M early-season log loss worsens (+0.0005). Before any promotion: exposure-adaptive weighting (weight on
current season as a function of current minutes) evaluated on the same folds, then a JM1 structural refit on the new
rate inputs (the GLM was fitted on v1ref Phase 1B rows) and a full JM1 walk-forward. The decision-layer remedy for the
primary cause (shrink model probabilities toward the de-vigged market, weight estimated walk-forward) needs historical
player-prop lines, which the repository does not have; it can only be developed on the live frozen evaluations as
they accumulate (>= several hundred per stat).

Scripts: p1..p8 (this folder), ../challenger/c1_blend.py, c2_prospective.py; results c1_blend_results.csv.
