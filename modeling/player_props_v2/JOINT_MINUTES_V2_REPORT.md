# CBB V2 — JointMinutesV2: 2024-25 Development Test

**Decision: `REJECT`.** The challenger fails one of the 12 frozen gates: G3, RESERVOIR_HEAVY. The champion is still Phase 2 J2 (S0). The 2025-26 confirmation has **not** been run.

Run details:
- **Experiment:** `cbb-v2-joint-minutes-v2`, version `cbb-v2-joint-minutes-v2-1.0.0`.
- **Branch:** `research/cbb-v2-interconnected`.
- **Protocol:** frozen and committed in `310acce` before the run. `joint_minutes_v2_protocol.json` sha256 `80d9b002a0ba3c309580b61fc6a79ad4ab73b6474225007be6efb02ab36a47d0`.
- **Code:** unchanged since the protocol was frozen (`code_changed_since_protocol: []`).
- **Evidence:** `305b0f3`.
  - `joint_minutes_v2_development.json`
  - `joint_minutes_v2_dev_parameters.json` (sha256 `008b906fb634ac757f7bb92222120e991727bc1159a098db4f7046d0eb6741cd`)
- **Data:** 2024-25 is **development** data. It was already inspected in Phase 3 and in the S2 root-cause audit, so it is not a holdout.
- **Number of runs:** exactly one full development run. 5,756 games, 112,871 scored player-games, 20,000 draws per game. No rerun and no tuning.
- **Laptop sleep:** the machine slept 03:54–10:34 during the run. The process was suspended for that time and the results are unaffected.

## 1. What was built

| Component | Implementation |
|---|---|
| **Candidate universe** | The Phase 1B candidate rows (`ex.player_game_examples`).<br>Point-in-time evidence that could add someone: 0 additional legal players.<br>Season-level roster snapshots are not point-in-time and are not used. |
| **Reservoir** | Aggregate, unmodeled rotation minutes R ≥ 0. It is never priced and has no stats.<br>Two-part model: logistic P(R > 0), plus a Poisson log-linear mean × Gamma(CV² 0.88) for the positive part.<br>Pregame team features only (team-games-so-far buckets, warm / no-history / transfer shares, log of the candidate count, returning share). Fit on 2014–2024 team-games.<br>Realized out-of-universe minutes are used only as training labels. Reservoir feature point-in-time violations: 0. |
| **Phase 1B anchor** | P(play), min_mu, min_sigma, role and cold class are all unchanged.<br>Raw minutes = clip(min_mu + σ z, 0, 60) × D / E[D].<br>No activation repair; participation is an independent Bernoulli(P(play)). |
| **Dependence** | A role-pooled three-tier latent factor (TOP5 / ROT / BENCH). No named-pair parameters.<br>Targets: pooled normal-score minutes-error correlations from out-of-sample 2023–2024. Shrunk, projected to PSD, then compensated for the effect of the constraints. |
| **Reconciliation** | m* = argmin (m − r)ᵀ W⁻¹ (m − r), subject to Σm + R = 5D, 0 ≤ m ≤ D, inactive = 0, R ≥ 0.<br>W = out-of-sample Var(minutes error) by role × warm/cold, shrunk with n0 = 500.<br>Solved exactly by KKT bisection. |
| **Duration** | Shared game duration D, including overtime. Pace does not change the clock. |
| **Stats** | One reconciled minutes draw feeds all 7 stats, and the combos come from the same draws.<br>No S3 opportunity term.<br>Conditional dispersion refit and residual J2 refit on true out-of-sample structural residuals. |
| **Arms** | **S0 (JM0):** Phase 2 J2.<br>**JM1:** sampled Phase 1B minutes with no reconciliation.<br>**JM2_A05 / JM2_A10:** the full model.<br>α\* is chosen on MARG_LOG: A05 scored 1.4350 vs A10 1.4363, so **α = 0.5**. |

## 2. Promotion gates (all 12 must pass)

| Gate | Result | Value (JM2_A05 − S0, 95% CI) | Margin |
|---|---|---|---|
| G1 minutes CRPS, overall | PASS | −0.0050 [−0.0076, −0.0024] | +0.01 |
| G2 minutes CRPS, TOP5 | PASS | −0.0038 [−0.0084, +0.0007] | +0.02 |
| G2 minutes CRPS, ROTATION_6_8 | PASS (close) | **+0.0137 [+0.0078, +0.0195]** | +0.02 |
| G2 minutes CRPS, BENCH_9PLUS | PASS | −0.0210 [−0.0258, −0.0160] | +0.02 |
| G3 EARLY_SEASON (team games ≤ 2) | PASS | +0.0036 [−0.0101, +0.0180] | +0.02 |
| G3 MONTH:NOV | PASS | −0.0049 [−0.0113, +0.0014] | +0.02 |
| **G3 RESERVOIR_HEAVY (E[R] ≥ 10)** | **FAIL** | **+0.0623 [+0.0282, +0.1028]** | +0.05 |
| G4 aggregate MARG_LOG | PASS | −0.0196 [−0.0201, −0.0191] | +0.001 |
| G5 each stat / stat × tier | PASS | 0 failures; every stat and every stat × tier improves | +0.002 / +0.005 |
| G6 Energy Score | PASS | −0.0125 [−0.0137, −0.0113] | +0.003 |
| G6 VS_BAL | PASS | −0.00092 [−0.00097, −0.00087] | +0.0001 |
| G6 Monte Carlo adequacy | PASS | upper bounds are 224 / 211 Monte Carlo standard errors inside the margin | — |
| G7 BOTH_TOP_SCORERS_OVER | PASS | Brier −0.0025 [−0.0030, −0.0021]; calibration gap 0.0698 → 0.0627 | Phase 3 A6 rule |
| G8 accounting | PASS | 0 violations in 511,945,128 team-draws; Σ_M·1 max relative error 4.1e−13 | exact |
| G9 universe / reservoir point-in-time | PASS | 0 reservoir feature violations; 0 legal players outside the universe | — |
| G10 point-in-time leakage | PASS | 0 | — |
| G11 determinism | PASS | identical hashes (2 games × 5,000 draws) | — |
| G12 2025-26 untouched | PASS | the season guard enforces 2025 (= 2024-25) as the maximum season | — |

`ALL_PASS = false`, so the decision is `REJECT`.

## 3. Why G3 failed: a design failure, not a bug

The RESERVOIR_HEAVY stratum is 272 team-games and 2,120 player-games, almost all in November (11% of November team-games). On these team-games the reservoir level is **calibrated**. This is a read-only diagnostic using 2024-25 realized out-of-universe minutes:

| Team-games | n | Predicted E[R] | Realized R | Realized P(R > 0) |
|---|---|---|---|---|
| All | 11,512 | 1.47 | 1.46 | 0.044 |
| E[R] ≥ 10 | 272 | 50.8 | 53.1 | 0.868 |
| E[R] < 10 | 11,240 | 0.28 | 0.21 | 0.024 |

The minutes loss on those rows comes from how the reservoir's uncertainty enters reconciliation:
- On heavy team-games, R is very uncertain. The realized standard deviation is about 45 minutes, and the model's q50–q90 spread is 48 → 95 minutes.
- Each R draw is subtracted from the known players' budget. So the large variance of R becomes extra spread and a downward shift in the known players' minutes.
- On heavy rows, mean minutes bias is −1.05 for JM2 vs −0.31 for S0, and MAE is 7.81 vs 7.72.
- JM1, which has no reconciliation, is neutral on the same rows (−0.0018). The loss is therefore caused by the reconciliation/reservoir coupling, not by the Phase 1B marginal.
- The model implements the frozen specification exactly. The R cap at 5D is part of the specification; it is reached in triple-overtime draws, where R_max = 275.
- No result-independent implementation bug was found, so under the protocol this is a **model-design failure**. Nothing was tuned and nothing was rerun.

## 4. Minutes (MIN_CRPS levels, S0 / JM1 / JM2_A05)

| Stratum | n | S0 | JM1 | JM2 | JM2 − S0 (95% CI) | Bias S0 / JM2 |
|---|---|---|---|---|---|---|
| Overall | 112,871 | 3.4461 | 3.4478 | 3.4412 | −0.0050 [−0.0076, −0.0024] | +0.12 / −0.11 |
| TOP5 | 49,741 | 3.3548 | 3.3590 | 3.3510 | −0.0038 [−0.0084, +0.0007] | −0.01 / −0.28 |
| ROTATION_6_8 | 27,502 | 3.7872 | 3.7880 | 3.8009 | +0.0137 [+0.0078, +0.0195] | −0.14 / −0.37 |
| BENCH_9PLUS | 35,628 | 3.3104 | 3.3094 | 3.2894 | −0.0210 [−0.0258, −0.0160] | +0.50 / +0.32 |
| EARLY_SEASON | 8,501 | 4.1483 | 4.1470 | 4.1519 | +0.0036 [−0.0101, +0.0180] | +0.28 / −0.10 |
| MONTH:NOV | 24,591 | 3.6975 | 3.6975 | 3.6926 | −0.0049 [−0.0113, +0.0014] | +0.21 / −0.00 |
| NEAR_BUDGET | 106,256 | 3.3917 | 3.3935 | 3.3809 | −0.0107 [−0.0128, −0.0088] | |
| RESERVOIR_HEAVY | 2,120 | 5.4971 | 5.4954 | 5.5595 | +0.0623 [+0.0282, +0.1028] | −0.31 / −1.05 |
| COLD_EARLY_SEASON | 7,758 | 3.7568 | 3.7559 | 3.7363 | −0.0205 [−0.0339, −0.0067] | |
| COLD_TRANSFER_IN | 2,810 | 3.9839 | 3.9831 | 4.0089 | +0.0250 [−0.0044, +0.0556] | |
| COLD_NO_HISTORY | 359 | 4.6290 | 4.6252 | 4.5736 | −0.0554 [−0.1240, +0.0098] | |
| Realized-incomplete (diagnostic only) | 1,650 | 5.4154 | 5.4120 | 5.4243 | +0.0090 [−0.0227, +0.0438] | |

Notes:
- **S2's catastrophe is gone.** Phase 3 S2 lost +0.95 CRPS on rows from teams with incomplete rosters; JM2 loses +0.009 on realized-incomplete rows and gains −0.0205 on cold early-season rows.
- **Coverage.** Minutes central coverage for JM2 is 0.563 / 0.838 / 0.917 at the 50 / 80 / 90% levels, vs 0.572 / 0.846 / 0.924 for S0. JM2's PIT histogram is flatter.
- **MIN_LOG is not comparable across arms and is not gated.** It is the integer-bin log score of the simulated PMF, and empty bins inflate the sampled arms.

## 5. Reconciliation distortion: |M_rec − M_raw| per player-game, in minutes

| | mean | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| All | 2.13 | 1.76 | 4.43 | 5.38 | 7.59 | 53.5 |
| TOP5 | 2.10 | 1.74 | 4.32 | 5.26 | 7.42 | 45.7 |
| ROTATION | 2.46 | 2.05 | 5.08 | 6.15 | 8.62 | 49.2 |
| BENCH | 1.92 | 1.56 | 4.07 | 4.96 | 7.02 | 53.5 |
| COLD_TRANSFER_IN | 2.26 | 1.87 | 4.68 | 5.65 | 7.80 | 49.2 |

Other reconciliation effects:
- Mean minutes: raw 20.54 → reconciled 20.30. Phase 1B min_mu mean is 20.45.
- SD ratio (reconciled / raw): 0.957 overall; TOP5 0.916, ROTATION 0.978, BENCH 1.002.
- The duration bound binds for 2.1% of rows (4.6% of TOP5 rows).
- The Phase 1B conditional marginal is sampled directly. The only change is this reconciliation shift, which is quantified above.

## 6. Reservoir usage (base world, per team-game)

**Overall**
- Mean R 1.47; q50 1.16, q90 2.68, q99 7.18, max 275.
- 272 team-games (2.4%) have E[R] ≥ 10.
- Draws with fewer than 5 known players active: 0.15%.

**By month**

| Month | Mean R | Share with E[R] ≥ 10 |
|---|---|---|
| November | 6.41 | 11.1% |
| December | 0.25 | 0% |
| January–April | 0.10 | 0% |

The reservoir is essentially an early-season, cold-start mechanism. It is concentrated in teams' first games, where the tg0 coefficient is +5.4.

## 7. Stats (MARG_LOG, S0 → JM2_A05; every Δ CI excludes 0)

| Stat | S0 | JM2 | Δ LOG | Δ CRPS (scaled) |
|---|---|---|---|---|
| PTS | 2.8521 | 2.7539 | −0.0982 | −0.0064 |
| REB | 2.0067 | 1.9857 | −0.0210 | −0.0027 |
| AST | 1.3591 | 1.3520 | −0.0071 | −0.0021 |
| FG3M | 1.0007 | 0.9974 | −0.0033 | −0.0007 |
| STL | 1.0222 | 1.0202 | −0.0019 | −0.0005 |
| BLK | 0.6444 | 0.6429 | −0.0015 | −0.0005 |
| TOV | 1.2969 | 1.2928 | −0.0041 | −0.0015 |

- **By role:** stat × role improves everywhere. PTS improves by −0.0066 for TOP5, −0.0899 for ROTATION and −0.2324 for BENCH.
- **By position:** improvements everywhere, except the C × FG3M diagnostic at +0.0007 [−0.0003, +0.0016], which is not gated.
- **PTS spread ratio (latent scale λ; 1 = calibrated):**

| Stratum | S0 | JM1 | JM2 |
|---|---|---|---|
| All | 1.223 | 1.097 | 1.110 |
| TOP5 | 1.046 | 1.064 | 1.082 |
| ROTATION | 1.284 | 1.134 | 1.145 |
| BENCH | 1.393 | 1.089 | 1.106 |
| Early season | 1.328 | 1.175 | 1.180 |
| RESERVOIR_HEAVY | 1.459 | 1.287 | 1.283 |

## 8. Joint scoring (levels S0 / JM1 / JM2; Δ is JM2 − S0)

| Metric | S0 | JM1 | JM2 | Δ (95% CI) |
|---|---|---|---|---|
| Energy Score | 6.7658 | 6.7529 | 6.7533 | −0.0125 [−0.0137, −0.0113] |
| VS_BAL | 0.2404 | 0.2397 | 0.2394 | −0.00092 |
| EVENT_BRIER | 0.1310 | 0.1304 | 0.1305 | −0.0005 |
| PRA log / CRPS | 3.1664 / 3.5561 | 3.0999 / 3.5103 | 3.0960 / 3.5041 | −0.0703 / −0.0520 |
| PR log / CRPS | 3.0928 / 3.3345 | 3.0233 / 3.2927 | 3.0194 / 3.2862 | −0.0733 / −0.0483 |
| PA log / CRPS | 2.9385 / 2.9253 | 2.8601 / 2.8851 | 2.8561 / 2.8797 | −0.0824 / −0.0456 |
| RA log / CRPS | 2.2601 / 1.4704 | 2.2413 / 1.4594 | 2.2395 / 1.4588 | −0.0206 / −0.0116 |
| STOCKS log / CRPS | 1.2599 / 0.5334 | 1.2577 / 0.5325 | 1.2571 / 0.5323 | −0.0028 / −0.0011 |
| BOTH_TOP_SCORERS_OVER (observed 0.2882) | p 0.2184, Brier 0.20990 | p 0.2246, Brier 0.20676 | p 0.2255, Brier 0.20738 | Brier −0.0025 |

**Correlation calibration RMSE (slope), S0 → JM2:**

| Level | S0 | JM2 |
|---|---|---|
| Within player | 0.057 (1.29) | 0.011 (1.02) |
| Same team | 0.0051 (1.23) | 0.0156 (0.41) |
| Cross team | 0.0039 (1.08) | 0.0025 (0.97) |

Same-team correlation calibration gets worse, with the slope falling to 0.41.

**Event families that are not gated.** Brier is slightly worse than S0 for SP_AST_TOV, SP_PTS_REB, OP_REB_REB and OP_TOV_STL, all by ≤ +0.0007. The same-player calibration gaps widen. For example, SP_PTS_REB goes +0.034 → +0.043.

## 9. Minutes covariance: simulated vs empirical 2024-25 normal-score correlations

| Pair | Simulated | Empirical |
|---|---|---|
| TOP5–TOP5 | +0.084 | +0.056 |
| TOP5–ROT | −0.048 | −0.075 |
| TOP5–BENCH | −0.073 | −0.110 |
| ROT–ROT | −0.005 | −0.075 |
| ROT–BENCH | +0.017 | −0.038 |
| BENCH–BENCH | +0.104 | +0.072 |

- RMSE 0.044, max absolute error 0.070, signs agree on 5 of 6 pairs.
- ROT–ROT at −0.067 could not be reached in training, because the latent diagonal must be ≥ 0.
- Σ_M·1 = 0 holds to 4.1e−13 at fixed duration.

## 10. Old J2 vs residual J2 (mean |ρ|)

| Level | Old J2 | Residual J2 |
|---|---|---|
| Within player | 0.175 | 0.057 |
| Same team | 0.0102 | 0.0125 |
| Cross team | 0.0075 | 0.0064 |

Within-player dependence drops because the shared reconciled minutes now carry it. One example is PTS–REB, which falls from 0.371 to 0.080.

## 11. Integrity

| Check | Result |
|---|---|
| Accounting | 0 violations (team minutes, bounds, inactive minutes, R < 0), 0 combo identity violations, 0 invalid correlation matrices |
| Activation repairs | 0 |
| Activation vs P(play) | mean 0.43741 vs 0.43742 |
| Point-in-time | 0 violations (team features: 11,528 rows; reservoir features: 11,526 rows) |
| Determinism | identical hashes |
| Historical API calls | 0 |
| 2025-26 | not loaded |
| Frozen artifacts | Phase 1B / Phase 2 J2 / Phase 3 frozen records unchanged |
| User QA file | unchanged (sha `5ca9001c…`) and not committed |
| Tests | Focused: 18 passed.<br>Full suite, run once: 645 passed / 2 failed / 3 skipped.<br>Both failures (`test_stage2::test_no_predictive_model_artifacts`, `test_stage2_2::test_no_predictive_model_trained`) were caused by a gitignored debug scratch file, `data/qa/large/v2_jm2/debug_params_T2024.pkl`. That file was deleted afterwards and the two tests then passed. No code change. |

## 12. Disposition

- JointMinutesV2 is **not advanced** to confirmation, and Phase 2 J2 remains champion.
- The development result fixes S2's infeasible-budget catastrophe and improves every stat marginal and joint score. However, it fails the frozen RESERVOIR_HEAVY minutes gate, and its rotation-tier minutes and same-team correlation calibration are worse.
- Under the governance rules no tuning or rerun was done. Any successor needs a new protocol that discloses this 2024-25 development use.
- Awaiting architect review. **2025-26 was not run.**
