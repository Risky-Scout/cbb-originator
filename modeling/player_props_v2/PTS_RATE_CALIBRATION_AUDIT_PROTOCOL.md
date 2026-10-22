# PTS RATE — CALIBRATION-GEOMETRY AUDIT — FROZEN PROTOCOL (DIAGNOSIS ONLY)

Architect directive 2026-10-22. Branch `research/cbb-v2-pts-rate-calibration-audit`, created from exactly 734bb25 (constrained selection result WEIGHTING_FAMILY_INFEASIBLE, which stands). That branch was pushed before this branch was created; remote SHA 734bb25.
Status: DIAGNOSTIC ONLY. Production baseline `e1353e5` is unchanged. PUBLIC LAUNCH: HOLD.
This audit builds no model, does no refit, creates no challenger, R3 or calibration transform, runs no Monte Carlo and makes no API call.

This file is committed BEFORE any calibration quantity below is computed (no quintile, regression or reliability number). After the commit, none of the items below may change.

Before this file was written, only one thing was checked: that r_R0 can be reproduced. That is a data-integrity check, not a result.

Protected file: `data/qa/game_identity_questionable_only.csv` (untracked) must keep sha256 `5ca9001c7c4e700b6ca0dd0e27c20290982d0051748ff96fa28317fa002f15b2`.

## 1. Source datasets (existing, read-only)

| forecast season | store (true point-in-time OOS Phase 1B) | state |
|---|---|---|
| 2022-23 (F2023) | `modeling/v2_oos/oos_forecasts_phase1b_v1ref_F2023.parquet` | `states/F2023_ANNUAL_REFIT_V1REF.json` |
| 2023-24 (F2024) | `modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet` | `states/F2024_ANNUAL_REFIT_V1REF.json` |
| 2024-25 (F2025) | `modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet` | `states/F2025_ANNUAL_REFIT_V1REF.json` |
| 2025-26 (F2026) | `modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet` | `states/F2026_ANNUAL_REFIT_V1REF.json` |

Other inputs:
- PIT history inputs come from `pts_rate_challenger.history(F)`: the frozen `marginal.load_frames` "candidates" SQL, run verbatim.
- PIT `team_games` = max(`pp_team_games`) per (season, game, team) from `feat.player_features`. This is the same definition as `joint_minutes_v2.reservoir_frame`; it is identical to the stored 2024-25 and 2025-26 JM1 rows.
- No OOS store, feature, draw or state is regenerated. No large file is duplicated. Outputs are compact JSON plus this audit's report.

Requirement: every store row has `training_through_season < F`. If it does not, STOP.

## 2. Conditioning (analysis universe)

A row is in the universe iff all of the following hold:
- forecast_season = F;
- `participation_status == "PLAYED"`;
- `y_PTS` is not null;
- `y_min > 0`.

There are no other filters. In particular, nothing is filtered on prediction quality, position or rate.

## 3. Predicted rate r_R0 (exact frozen R0 rate)

r_R0 = `pts_rate_challenger.rate_parts(...)["r_R0"]`. This is the verbatim `props_v0._predict_means` PTS rate:

```
(_hist(pts) + k * prior_rate_pts[pos]) / (_hist(min) + k)
```

Its inputs are the fold state F's `prior_k_minutes.pts` and `prior_rate.pts`, and the PIT current-season and history fields with the frozen recency weights.

Check: max over every candidate row of |max(min_mu · r_R0, mean_floor) − PTS_mu| ≤ 1e-9 in every season. If not, STOP as an implementation defect.

Neither JM1 mean / min_mu nor any realized quantity is used as the predicted rate.

## 4. Realized rate (retrospective diagnostic only)

r_actual = y_PTS / y_min on universe rows.

- Target-game actual minutes are used ONLY as the diagnostic exposure (offset / weights).
- They are never a production input.
- Nothing is fitted from them for later use.

## 5. Season roles

- REFERENCE / development era: 2022-23 + 2023-24 pooled.
- Later diagnostic evaluation: 2024-25 and 2025-26.
- 2024-25 and 2025-26 are NOT pristine holdouts. R0 behaviour on them has already been studied (directional audit, root cause, R1, R2, constrained selection).
- This is a diagnostic stability test, not virgin holdout validation.

## 6. Fixed predicted-rate quintiles

- Boundaries q20, q40, q60, q80 come from `numpy.quantile` (linear interpolation) of r_R0 over the pooled REFERENCE universe rows ONLY.
- Bands:
  - Q1: r < q20;
  - Q2: q20 ≤ r < q40;
  - Q3: q40 ≤ r < q60;
  - Q4: q60 ≤ r < q80;
  - Q5: r ≥ q80.
- The SAME boundaries are applied to all four seasons. They are never recomputed per season.

Each season × quintile cell reports:
- N;
- mean r_R0 (unweighted);
- mean realized PTS/min (unweighted row mean of y_PTS / y_min);
- minutes-weighted predicted rate Σ r_R0·y_min / Σ y_min;
- minutes-weighted realized rate Σ y_PTS / Σ y_min;
- signed rate error = predicted − actual;
- relative rate error = (predicted − actual) / actual;
- mean min_mu;
- mean y_min.

The PRIMARY reliability error is the minutes-weighted pair: Σ(r_R0·y_min − y_PTS) / Σ y_min. This is the exact analogue of the offset count model, and it is not dominated by 1–2-minute rows. Unweighted means are also reported.

## 7. Reliability curve and monotonicity

For each season, from the primary relative errors e1..e5 (Q1..Q5):
- inversions = the number of adjacent pairs with e(k+1) > e(k);
- gradient = e1 − e5.

The COMPRESSION PATTERN holds for a season iff ALL of:
- e1 > 0 (over-prediction at low predicted rates);
- e5 < 0 (under-prediction at high predicted rates);
- inversions ≤ 1;
- gradient ≥ 0.03.

No correction is fitted from the bands.

## 8. Primary calibration regression (count model)

NB2 count regression:

```
log E[y_PTS_i] = log(y_min_i) + a + b · log(r_R0_i)
Var = mu + phi mu^2
```

This matches the `props_v0.pmf_matrix` NB parametrization.

Estimation:
- Full maximum likelihood over (a, b, phi).
- Fisher scoring for (a, b) at fixed phi, alternated with a 1-D golden-section maximization of the profile likelihood over log phi in [log 1e-4, log 10], until max |Δcoef| < 1e-8 (at most 200 rounds).
- The NB log-likelihood in phi uses the exact identity lgamma(y+r) − lgamma(r) = Σ_{j<y} log(r+j).
- No new dependency (numpy only).

Uncertainty:
- Game-clustered sandwich covariance (cluster = game_id) for (a, b) at the ML phi.
- Bread = inverse expected information X'WX with W = mu / (1 + phi mu). NB2 mean and dispersion parameters are information-orthogonal.
- Meat = Σ_g (Σ_{i∈g} x_i s_i)(·)' with s_i = (y_i − mu_i) / (1 + phi mu_i), times the small-sample factor G/(G−1).
- 95% CI = estimate ± 1.959964 · SE.

The regression is run separately on:
- (A) REFERENCE (2022-23 + 2023-24 pooled);
- (B) 2024-25;
- (C) 2025-26.

Each run reports a, b, SE, 95% CI, phi, N, games and convergence. For information only, the same regression is also reported for 2022-23 and 2023-24 separately.

Interpretation:
- b = 1: correct relative spread.
- b > 1: the frozen rates are COMPRESSED.
- b < 1: the frozen rates are too dispersed.

## 9. min_mu after rate control

Extension:

```
log E[y_PTS] = log(y_min) + a + b log(r_R0) + c z(min_mu)
z = (min_mu − mean_ref) / sd_ref
```

- mean_ref and sd_ref are the REFERENCE universe mean and population SD of min_mu.
- The extension uses the same estimator and clustered CI. It is run on REFERENCE, 2024-25 and 2025-26.
- Effect magnitude = exp(c) − 1, the relative realized-rate change per reference SD of min_mu at fixed r_R0. The implied factor between min_mu 10 and 32 is also reported.

MATERIAL min_mu signal (REFERENCE): |c| ≥ 0.02 AND its 95% CI excludes 0. If c is significant but |c| < 0.02, it is reported as statistically detectable but immaterial.

No correction is fitted from c.

## 10. Diagnostic strata (descriptive; same estimator)

Every stratum below is evaluated in each of: REFERENCE, 2024-25, 2025-26. For each stratum the audit reports:
- N;
- minutes-weighted predicted rate, realized rate and signed and relative error;
- the §8 slope and intercept with clustered CI, when N ≥ 2,000. Otherwise "not estimable".

Strata:
- **Exposure** (existing bands on pregame min_mu): < 20, [20, 30), ≥ 30.
- **Position** (existing `position_group` only): G, F, C, U. No new categories.
- **Current-season evidence** (existing buckets on PIT cur_min): 0, (0, 100], (100, 300], > 300.
- **Early season**:
  - team_games ≤ 2;
  - first 30 days (season_day < 30, where season_day = days since the earliest ET prediction-cutoff date among that season's universe rows);
  - remainder (season_day ≥ 30).
- **Cold start / low evidence**: cur_min = 0 and (0, 100] (the evidence strata), plus `cold_start_class`.

## 11. Decision rule (frozen; diagnostic materiality, not production gates)

Criteria:

1. REF: REFERENCE b ≥ 1.05 AND REFERENCE 95% CI lower bound > 1.
2. S25: 2024-25 b > 1.
3. S26: 2025-26 b > 1.
4. CONSISTENT: for each of 2024-25 and 2025-26, |b_season − b_ref| ≤ 0.15, OR that season's reliability curve meets the §7 COMPRESSION PATTERN.
5. CURVES: the §7 COMPRESSION PATTERN holds in ALL FOUR seasons (2022-23, 2023-24, 2024-25, 2025-26), using the fixed reference quintiles.
6. MIN_MU: REFERENCE min_mu signal is NOT material (§9).
7. POSITION: there is no major position reversal.
   - Major reversal: in REFERENCE, 2024-25 or 2025-26, a position with N ≥ 2,000 whose slope 95% CI lies entirely on the opposite side of 1 from that sample's global slope point estimate.
   - Example: global b > 1, but the position's CI upper bound < 1.
8. EARLY: there is no major early-season reversal.
   - Major reversal: in REFERENCE, 2024-25 or 2025-26, the team_games ≤ 2 or first-30-days slope 95% CI lies entirely on the opposite side of 1 from that sample's remainder-of-season slope point estimate.
   - Or: that stratum's primary signed rate error has the opposite sign to the remainder's AND its absolute relative error is ≥ 0.03.

Outcomes:
- **CALIBRATION_DECOMPRESSION_JUSTIFIED**: criteria 1–8 all hold.
- **INCONCLUSIVE**: criteria 1, 2, 3, 5, 7 and 8 hold, but 4 or 6 fails; or any required regression fails to converge.
- **CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED**: every other case (any of 1, 2, 3, 5, 7, 8 fails).
- RATE_COMPRESSION_CONFIRMED = criteria 1–5 all hold.

None of these thresholds changes after results are seen.

## 12. Hard limits

- No transform r' = exp(a)·r^b, or anything equivalent, is built, applied or scored.
- No a or b is optimized against 2024-25 or 2025-26.
- No R3/R4, tau/rho family, role multiplier, starter adjustment or minutes-band correction.

If JUSTIFIED: STOP and report. Any calibration experiment needs separate architect authorization.
If NOT JUSTIFIED: recommend STOP HISTORICAL PTS REPAIR CAMPAIGN. Keep e1353e5 as the frozen shadow baseline and continue prospective monitoring.

Compute target: minutes. Anything expected to exceed 10 CPU-minutes stops before it runs.

Tests are focused only:
- quintile construction;
- the NB regression (recovery on synthetic data);
- decision logic;
- season separation;
- no target leakage.

The full suite is not rerun.
