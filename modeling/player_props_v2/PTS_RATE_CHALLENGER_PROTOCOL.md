# PTS RATE CHALLENGER (R1) — FROZEN PROTOCOL

Experiment: one-parameter adaptive empirical-Bayes PTS per-minute rate (architect directive 2026-10-22).
Branch: `research/cbb-v2-pts-rate-challenger`, created from 35d8705 (root-cause audit).
Status: RESEARCH. Nothing here changes `e1353e5`, a frozen artifact, a release branch, `main` or the frozen tag.

This file is committed BEFORE any R1 quantity (fit-season score, tau, evaluation) is computed.
After that commit none of the following may change:
- the formula;
- the tau bounds or grid;
- the objective;
- the periods or strata;
- the gates.

## 1. R0 (frozen JM1_FINAL, e1353e5)

Phase 1B (`props_v0._predict_means`), per candidate row with fold state `F{F}_ANNUAL_REFIT_V1REF`:

```
H_min = cur_min + sum_{j=1..3} 0.5^j h_j_min           (recency half-life 1 season, 3 prior seasons; NaN -> 0)
H_pts = cur_pts + sum_{j=1..3} 0.5^j h_j_pts
r_R0  = (H_pts + k * p_pos) / (H_min + k)              k = prior_k_minutes["pts"], p_pos = prior_rate["pts"][pos]
mu_1B = max(min_mu * r_R0, 0.01)
```

The PIT inputs come from `marginal.load_frames` ("candidates"), verbatim SQL:
- `cur_*` is the season-to-date sum of the feature row (`pstd_sum_*`) available at `prediction_cutoff_utc` (TIP_MINUS_1H);
- `h_j_*` are the player-season finals of season F-j, with `available_at <= prediction_cutoff_utc`.

JM1_FINAL (`jm1_final.eval_game`), per draw b:

```
M_b  = clip(min_mu + min_sigma eps_b, 0, 60) * D_b / E[D]
lm_b = log mu_1B + c0 + beta log(max(M_b,1)/min_mu) + pace/own/opp environment terms
```

- The anchor is alpha = 1 (exact no-op).
- `lm_b` is quantized to GRID_H.
- The PTS predictive PMF is the mixture over b of NB(exp(lm_b), phi_PTS).
- Scored rows are PLAYED; P(play) is not in the mean.

## 2. R1 (identical except the PTS rate)

The prior is the frozen R0 estimator with the current season removed:

```
Hp_min  = sum_{j=1..3} 0.5^j h_j_min                    (prior-season history only, same weights, NaN -> 0)
Hp_pts  = sum_{j=1..3} 0.5^j h_j_pts
r_prior = (Hp_pts + k * p_pos) / (Hp_min + k)          (same fold k and p_pos as R0; = p_pos when Hp_min = 0)
```

Current-season evidence is `cur_pts` and `cur_min` (the same PIT season-to-date fields R0 uses; NaN -> 0).

```
r_R1   = (cur_pts + tau * r_prior) / (cur_min + tau)
       = w r_cur + (1 - w) r_prior,   w = cur_min / (cur_min + tau)
mu_1B' = max(min_mu * r_R1, 0.01)
```

Everything else in R1 is R0, byte for byte:
- `min_mu`, `min_sigma`, P(play);
- the minutes draws, clip, duration and environment;
- c0 and beta = 1.30, the anchor, phi_PTS, the GLM;
- the other six base stats' means;
- J2, the seeds, pricing, cutoffs and the pipeline.

Algebra (no double shrinkage):
- R0 is the special case tau_i = Hp_min,i + k: a player-specific pseudo-minute weight.
- R1 replaces it with ONE global tau. The position prior enters exactly once, inside `r_prior`, and is not reapplied after the tau blend.

Zero current evidence: `cur_min = 0` implies `cur_pts = 0`, so r_R1 = tau r_prior / tau = r_prior exactly. With tau ≥ 1 there is no division hazard and no epsilon. On these rows R1 = R0 exactly.

R1 does NOT use `min_mu`, role, starter status, position-specific tau or season-specific tau. Exposure enters only through `cur_min`.

## 3. The one parameter

- NUMBER_OF_FREE_PARAMETERS = 1: `tau`, global for PTS, in pseudo-minutes.
- Bounds: tau in [1, 2000].
- Search: a deterministic log-spaced grid of 241 points, tau_i = 2000^(i/240), i = 0..240 (step factor 1.0322).
  - Selection is the argmin of the objective; on a tie the smaller i wins.
  - There is no refinement after the grid.
- If the argmin is i in {0, 1, 239, 240}: TAU_IDENTIFICATION_SUSPECT. Bounds are never enlarged.

## 4. Objective and fit data (chronology)

TAU_FIT_OBJECTIVE is the mean PTS negative log score, −log p(y), over the pooled fit rows. p is the frozen Phase 1B conditional PTS distribution of each fit fold:

```
props_v0.pmf_matrix(mu_1B'(tau), phi_fold, 120)   (NB(mean, var mu + phi mu^2); the exact frozen PMF)
```

Only mu_1B' changes; phi is frozen per fold.

TAU_FIT_DATA:
- Stored true-OOS Phase 1B rows, participation_status = PLAYED with y_PTS non-null, of:
  - 2022-23 (F2023, state F2023_ANNUAL_REFIT_V1REF, trained through 2021-22; `oos_forecasts_phase1b_v1ref_F2023.parquet`);
  - 2023-24 (F2024, state F2024_ANNUAL_REFIT_V1REF, trained through 2022-23; `oos_forecasts_phase1b_v1ref.parquet`).
- Rows are pooled with equal row weight.
- The R0 reproduction check (`mu_1B` from the inputs equals the stored `PTS_mu`) must hold to ≤ 1e-9 or the fit aborts.

Why Phase 1B NB rather than the JM1 mixture on the fit seasons:
- No JM1_FINAL structure exists with parameters through 2021-22 / 2022-23, and building one would be a refit (prohibited).
- The Phase 1B NB is the frozen conditional PTS distribution that exists, true-OOS, for those seasons.
- Limitation, stated: the selected tau is optimal for the Phase 1B layer. The JM1 layer (c0, beta, environment) is applied on top, unchanged.

A secondary diagnostic only (never used for selection): the mean CRPS curve on the fit rows. The full objective curve is saved.

Periods:
- DEVELOPMENT_EVALUATION: 2024-25.
- RETROSPECTIVE_CONFIRMATION: 2025-26.
- HOLDOUT_LIMITATION: both seasons were examined in the directional and root-cause audits (R0 only). Neither is a pristine holdout. tau and this protocol are frozen before any R1 result for either season is computed, so 2025-26 is a fixed-parameter RETROSPECTIVE confirmation.

Execution order:
1. Commit the protocol.
2. Fit tau.
3. Commit the fit record.
4. Evaluate 2024-25.
5. Evaluate 2025-26.
6. Decide.

## 5. Exactness of rescoring

Proof by inspection that analytic rescoring is NOT exact (`mu_R0 × r_R1 / r_R0` would be an approximation):

- (a) `structural.mixture_tables` quantizes each draw's log mean to GRID_H, `q = round(lm/GRID_H)`. Adding log(r_R1/r_R0) changes the rounding per draw.
- (b) NB(mu, phi) with fixed phi is not a scale family on counts: var = mu + phi mu², and the support is integer. The mixture PMF (log score, CRPS, PIT) is not a transform of the R0 PMF.
- (c) The 0.01 floor on mu_1B breaks proportionality where it binds.
- Only the pre-quantization mean E_b[mu_b] scales exactly (floor not binding).

Therefore the cheapest exact frozen scorer is used: the PTS path of `jm1_final.eval_game`, called with the frozen library functions in the same order with the same seeds:
- `simulate_exposure`, then `log_mu_terms`, then `anchor`, then `mixture_tables`, then `mixture_pmf`, then `phase3.single_metrics`.
- It runs with 20,000 draws per game, R0 and R1 from the SAME draws (common random numbers).

R0 recomputed this way must reproduce the stored JM1 rows for PTS log / crps / mean:
- relative tolerance 2e-6 (= jm1_final.REPRO_RTOL; stored values are float32);
- otherwise R1_INCONCLUSIVE.

Parameters:
- 2024-25: `joint_minutes_v2_dev_parameters.json` (T = 2024; the JM1 development arm, stored in `develop_2025_rows.parquet`).
- 2025-26: `jm1_final_confirmation_parameters.json` (T = 2025; `confirm_2026_rows.parquet`).
- Phase 1B fold states: F2025 / F2026 `_ANNUAL_REFIT_V1REF`.

This is a deterministic re-scoring of the frozen PTS marginal on stored true-OOS inputs. It is not a full joint season simulation:
- no REB / AST / FG3M / STL / BLK / TOV marginal and no J2 joint in stage A;
- no refit;
- no API.

Combos (stage B) use the same-draw construction exactly as `jm1_final.eval_game` / `joint_minutes_v2.joint_block`:
- correlated latents `E @ L4.T` (J2), counts from the per-stat mixture tables;
- REB and AST draws identical in both arms; PTS draws R0 vs R1.
- Combo lines are the frozen R0 J0-convolution lines for BOTH arms, so the Brier events are identical.
- Stage B runs on a fixed deterministic 20% game subsample: games with int(sha256(game_id)[:8], 16) % 5 == 0.
- Combo mean bias is reported on ALL rows, exactly: combo mean = sum of stat means, with stored JM1 REB / AST means.

## 6. Strata (fixed)

- Exposure bands on pregame `min_mu`: [0,10), [10,20), [20,30), [30,∞). LOW = < 20; HIGH = ≥ 30.
- Early season:
  - `team_games <= 2` (the stored pregame team-games feature);
  - first 30 days (the `directional_audit` season_day < 30 definition).
- Current-season evidence buckets on `cur_min` (the minutes in the R1 estimator): 0, (0,100], (100,300], > 300.
- Cold start, using the stored `cold_start_class`: COLD_TRANSFER_IN; COLD_NO_HISTORY (freshmen / cold start); COLD_EARLY_SEASON; WARM.

## 7. Metrics

Per season and stratum, for R0 and R1:
- N, mean forecast, mean actual;
- signed bias (forecast − actual) and relative bias;
- MAE, RMSE;
- log score, CRPS, CRPS_SCALED (CRPS / 6.858577, the Phase 2 PTS scale);
- Brier (mean over the 5.5 / 9.5 / 14.5 half lines);
- mid-PIT;
- P(PIT < 0.25), P(PIT < 0.5), P(PIT < 0.75);
- lower miss P(PIT < 0.1), upper miss P(PIT > 0.9), tail asymmetry (upper − lower);
- 80% coverage P(0.1 < PIT < 0.9) and 90% coverage P(0.05 < PIT < 0.95).

PIT mass uses the uniform-within-interval convention (`directional_audit._p_above`). Differences are R1 − R0 per row.

Uncertainty: the game-clustered bootstrap of `directional_audit.Boot` (2000 draws, seed 20366); 95% percentile intervals of row means. "Upper bound" means the 97.5% bound.

COMPRESSION statistic (primary): C = bias[0,10) − bias[30,∞) (PTS row-mean signed bias).
- Secondary, reported only: the N-weighted least-squares slope of the four band biases on band index 0..3.

## 8. Gates (all must pass in BOTH seasons for R1_ACCEPT)

- **A. High exposure.** |bias_R1(≥30)| ≤ 0.75 |bias_R0(≥30)|.
- **B. Compression.** |C_R1| ≤ 0.75 |C_R0|.
- **C. Aggregate log.** Upper bound of mean ΔLOG ≤ +0.002 nats per PTS row (the project margin `jm1_final.G_STAT_LOG`).
- **D. Aggregate CRPS.** Upper bound of mean ΔCRPS_SCALED ≤ +0.002 (the project margin `jm1_final.G_STAT_CRPS`).
- **E. High-exposure proper scores (≥ 30).** Upper bound of ΔLOG ≤ +0.005 and of ΔCRPS_SCALED ≤ +0.005 (the project subgroup margin `jm1_final.G_STAT_GROUP_LOG`, applied to both).
- **F. Low exposure (< 20).** |bias_R1| − |bias_R0| ≤ 0.05 PTS; upper ΔLOG ≤ +0.005; upper ΔCRPS_SCALED ≤ +0.005.
- **G. Early season**, for each of team_games ≤ 2 and first 30 days:
  - upper ΔLOG ≤ +0.005 and upper ΔCRPS_SCALED ≤ +0.005;
  - |bias_R1| − |bias_R0| ≤ 0.05;
  - |midPIT_R1 − 0.5| − |midPIT_R0 − 0.5| ≤ 0.01;
  - |cov80_R1 − 0.8| − |cov80_R0 − 0.8| ≤ 0.01.
- **H. Tails**, for ALL and ≥ 30: for each of mid-PIT (0.5), P(PIT < 0.25) (0.25), P(PIT < 0.5) (0.5), P(PIT < 0.75) (0.75), lower miss (0.1), upper miss (0.1), cov80 (0.8) and cov90 (0.9):
  - |d_R1 − target| − |d_R0 − target| ≤ 0.01.
  - No proper-score override is used.
- **I. Combos PRA / PR / PA**, each:
  - stage B subsample: upper ΔLOG ≤ +0.005 nats per player-row; upper ΔCRPS ≤ 0.0025 × mean R0 CRPS; upper Δ(mean q25/q50/q75 Brier) ≤ +0.002;
  - all rows: |bias_R1| − |bias_R0| ≤ 0.05.
- **J. Numerics / cold start / transfer.**
  - r_R1 is finite and > 0 on every candidate row of all four seasons, and lies within [min(r_cur, r_prior), max(r_cur, r_prior)].
  - cur_min = 0 rows give R1 ≡ R0, bit-identical mu_1B.
  - Non-PTS Phase 1B means are bit-identical.
  - COLD_TRANSFER_IN and COLD_NO_HISTORY: upper ΔLOG ≤ +0.005.
- **Precondition.** The R0 reproduction holds (section 5). Otherwise R1_INCONCLUSIVE.

Decision:
- All of A–J pass in both seasons: R1_ACCEPT, reported as R1_ACCEPT_PENDING_JOINT_CONFIRMATION.
- Any gate fails: R1_REJECT.
- The precondition or a computation fails: R1_INCONCLUSIVE.
- No retuning, no R2, no promotion.

## 9. Compute plan

- **Fit.** About 222k rows × 241 grid points with the vectorized frozen PMF; seconds to about 2 minutes. Single thread.
- **Stage A.** About 0.05–0.08 s per game; 5,756 + about 5,700 games, so about 7 minutes per season.
- **Stage B.** About 0.25 s per game on the 20% subsample (about 1,150 games per season), so about 5 minutes per season.
- Execution: sequential, one process, `nice -n 15`, OMP / OPENBLAS / MKL / VECLIB / NUMEXPR threads = 1.
- No API calls, no historical rebuild, no full-suite rerun, no full 7-stat joint re-simulation.
