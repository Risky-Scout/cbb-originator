# JM1_FINAL high-exposure PTS bias: root-cause audit

This is a diagnosis only. The model was not changed, nothing was re-simulated, no API was called, and nothing was refit.

* **Evidence:** the same stored true-OOS cells as the directional audit, plus the Phase 1B OOS store and the frozen
  per-fold Phase 1B rate inputs (read-only).
* **Code:** `cbb_platform/prod/root_cause_audit.py`, which runs in about 12 s.
* **Output:** `jm1_final_high_exposure_root_cause.json`.
* **Sign convention:** predicted − actual, so a positive value is over-prediction.
* **Uncertainty:** game-clustered bootstrap with 2000 draws.

## 1. How the frozen PTS mean is constructed (read from code, not assumed)

**Phase 1B** (`props_v0._predict_means`) is conditional on playing. It is trained and scored on PLAYED rows only.

* `mu_1B = max(min_mu × rate, 0.01)`.
* `min_mu = clip(ridge(pregame features), 1, 45)`. The ridge penalty is α = 1e-4 × Σw, which is negligible.
* `rate = (H_pts + k·prior_pos) / (H_min + k)`:
  * H is the recency-weighted history: current season-to-date with weight 1, then the previous three seasons with
    weights 0.5, 0.25 and 0.125 (half-life one season, "fixed, not tuned").
  * The prior strength is k ≈ 30–31 minutes for PTS, toward a position-group rate of about 0.35–0.37 PTS per minute.

**JM1_FINAL** (`jm1_final.eval_game`) works per simulated draw b:

* Minutes: `M_b = clip(min_mu + min_sigma·ε_b, 0, 60) × D_b / E[D]`.
* Mean: `μ_b = mu_1B · exp(c0 + β·log(max(M_b, 1)/min_mu) + pace / own / opp environment shocks)`. For PTS,
  β = 1.30 and c0 ≈ −0.007 / −0.008.
* The anchor α = 1.0 is a no-op, so there is no pull back to `mu_1B`.
* The reported mean is the mean of the negative-binomial mixture over draws, which equals E_b[μ_b]. This is a mean
  integrated over the random minutes distribution and the shared-game environment. It is **not** simply
  `min_mu × rate`.

**What this means for the questions asked**

* The PTS mean is conditional on play: **TRUE**.
* P(play) is in the prop mean: **FALSE**. Scored rows are PLAYED, and P(play) multiplies neither the minutes draw nor
  the mean.

**Verification:** the per-row Phase 1B rate reproduces from the frozen inputs to within 1e-16 for PTS, AST and TOV.
For FG3M the gap is 0.008, on 281 rows where the 0.01 mean floor binds.

## 2. Exact decomposition

forecast − actual = **MINUTES + RATE + STRUCTURAL**, and the parts sum exactly (check error at most 1e-16).

* **Phase 1B part** (`mu_1B − y`), using the two-factor Shapley split with r̂ = mu_1B / min_mu and r = y / y_min:
  * MINUTES = (m̂ − m)(r̂ + r)/2.
  * RATE = (r̂ − r)(m̂ + m)/2.
  * On rows with y_min = 0, all of the error goes to MINUTES.
* **STRUCTURAL** = mean_JM1 − mu_1B, split sequentially into three parts:
  * INTERCEPT = mu_1B(e^c0 − 1).
  * EXPOSURE_JENSEN = mu_1B·e^c0·(J − 1), where J = E[(max(M, 1)/min_mu)^β] over the frozen minutes-draw law, computed
    by dense quadrature.
  * REMAINDER: environment shocks, duration, quantization and truncation.
* **RATE is traced exactly** through the frozen formula. Each part is multiplied by the same (m̂ + m)/2 weight, so the
  three parts sum to RATE:
  * PRIOR_SHRINKAGE = w(prior − r_hist), with w = k/(H_min + k).
  * PRIOR_SEASON_BLEND = r_hist − r_cur, the gap between the full recency-weighted history rate and the current
    season-to-date rate.
  * CURRENT_RATE_VS_REALIZED = r_cur − r.

## 3. Minutes by pregame exposure

These are PLAYED rows: conditional minutes against actual minutes.

| min_mu band | N (2024-25 / 2025-26) | pred / actual 2024-25 | signed error 2024-25 | signed error 2025-26 | median | P(act > pred) |
|---|---|---|---|---|---|---|
| [0,10) | 17,886 / 16,968 | 6.17 / 5.99 | +0.18 [+0.09, +0.26] | +0.14 [+0.05, +0.23] | +0.99 / +1.01 | 0.38 |
| [10,20) | 34,770 / 35,902 | 15.21 / 14.99 | +0.23 [+0.15, +0.30] | +0.23 [+0.16, +0.30] | +0.56 | 0.47 |
| [20,30) | 40,106 / 40,221 | 25.14 / 25.46 | −0.31 [−0.38, −0.25] | −0.36 [−0.43, −0.28] | −0.82 | 0.55 |
| [30,∞) | 20,109 / 19,243 | 32.85 / 32.58 | **+0.28 [+0.18, +0.37]** | **+0.16 [+0.06, +0.25]** | −0.55 / −0.67 | 0.54 / 0.56 |

* High-exposure players are **not** given too few minutes on the mean: their mean minutes are slightly over-predicted.
* The median player in that band plays a little more than predicted (median error −0.6), but a left tail of short
  games (blowouts, fouls) pulls the actual mean down.
* Minutes compression toward the middle is small and not monotone.

**Minutes distribution** (frozen JM1 minutes draw: stored per-row PIT and quantiles)

* **min_mu ≥ 30:**
  * mid-PIT 0.507 / 0.513;
  * P(y > q90) is only 0.035 / 0.038 against an expected 0.10, while P(y < q10) is 0.093 / 0.090;
  * 80% coverage is 0.872 against an expected 0.80;
  * the RMSE / predicted-SD ratio is 0.98 / 0.96.
  * So the upper tail is too wide, the lower tail is roughly right, and the distribution is over-dispersed on the upside.
* **min_mu < 10:**
  * mid-PIT 0.459 / 0.461;
  * P(y > q90) is 0.088 / 0.098, while P(y < q10) is only 0.034 / 0.035;
  * spread ratio 1.12 / 1.13.
  * Realized minutes are right-skewed in a way the symmetric clipped normal does not capture.
* **In aggregate**, PIT 0.498 / 0.500 and 80% coverage 0.848 / 0.844 look fine. The aggregate quality hid the
  distribution-shape problems by band, but **not** a mean compression of minutes.

## 4–6. PTS decomposition and root cause

### PTS decomposition by exposure (2024-25 | 2025-26)

| band | TOTAL | MINUTES | RATE | STRUCTURAL (int / Jensen / rem) | predicted vs actual PTS per minute |
|---|---|---|---|---|---|
| [0,10) | +0.700 \| +0.775 | −0.054 \| −0.062 | +0.343 \| +0.415 | +0.411 \| +0.422 (Jensen +0.432 \| +0.416) | +14.5% \| +18.6% |
| [10,20) | +0.505 \| +0.547 | −0.146 \| −0.161 | +0.493 \| +0.488 | +0.158 \| +0.221 | +6.0% \| +5.5% |
| [20,30) | −0.103 \| −0.065 | −0.248 \| −0.283 | +0.155 \| +0.121 | −0.011 \| +0.097 | +0.3% \| −0.1% |
| **[30,∞)** | **−0.369 \| −0.333** | **+0.056 \| +0.000** | **−0.294 \| −0.389** | **−0.131 \| +0.056** (−0.091/+0.071/−0.111 \| −0.105/+0.073/+0.088) | **−2.6% [−3.2, −2.0] \| −3.2% [−3.8, −2.6]** |
| < 20 | +0.571 \| +0.620 | −0.115 \| −0.129 | +0.442 \| +0.464 | +0.244 \| +0.285 (Jensen +0.299 \| +0.292) | +7.3% \| +7.3% |

* **High exposure (min_mu ≥ 30), shares of the total bias:**
  * Minutes: −15% (2024-25) and 0% (2025-26).
  * Rate: +80% and +117%.
  * Structural: +36% and −17%. Its sign flips between seasons, because the environment and duration remainder flips.
  * **The rate is the only component that is consistent and material in both seasons.**
* **High exposure, rate in PTS per minute:**
  * 2024-25: predicted 0.4052 against actual 0.4159, a signed error of −0.0107.
  * 2025-26: predicted 0.4149 against actual 0.4288, a signed error of −0.0139.
* **Rate trace at high exposure, in points (all 95% CIs exclude 0):**

  | Part | 2024-25 | 2025-26 |
  |---|---|---|
  | PRIOR_SHRINKAGE | −0.061 | −0.076 |
  | PRIOR_SEASON_BLEND | −0.858 | −0.951 |
  | CURRENT_RATE_VS_REALIZED | +0.626 | +0.638 |
  | Net rate | −0.294 | −0.389 |

  * The mean prior weight is only w = 0.036 / 0.039.
  * Current-season minutes are 55–59% of these players' weighted history minutes.
* **What drives the high-exposure under-forecast.** The recency-weighted history blend pulls the forecast rate about
  0.86–0.95 points below the current-season-to-date rate. The prior seasons carry 0.5 / 0.25 / 0.125 of the minutes
  weight, and for these players those seasons ran at lower per-minute rates. The current-season rate alone would
  overshoot (+0.63, which is regression to the mean). The fixed half-life-1 blend over-corrects for players at high
  pregame exposure, which leaves −0.29 / −0.39. The gamma prior toward the position mean adds a smaller −0.06 / −0.08.

### Low-exposure tail (min_mu < 20)

* The same rate mechanism holds, with the opposite sign: rate +0.442 / +0.464, or 77% / 75% of the total.
* The rate trace at < 20 in 2024-25 / 2025-26:
  * prior shrinkage +0.085 / +0.078, larger here because w ≈ 0.21 (0.34 at < 10) and the position prior sits above
    these players' rates;
  * blend −0.008 / −0.027;
  * current rate versus realized +0.365 / +0.413.
* A second, **structural** mechanism appears only in the low tail: EXPOSURE_JENSEN of +0.299 / +0.292 (43–46% of the
  total; +0.43 at < 10).
  * The frozen minutes draw is a symmetric clipped normal with the floor max(M, 1), raised to the power β = 1.30.
  * At min_mu < 10 this gives J = 1.57 / 1.49. The same functional evaluated on **realized** minutes gives 1.30 / 1.24.
  * At ≥ 30, J = 1.005 against 0.996–1.000 realized, so this is not a factor for high exposure.
* Minutes contribute a small negative amount at < 20 (−0.12 / −0.13).

**Compression pattern confirmed: TRUE, and it lives in the per-minute rate.** The predicted rate relative to actual
runs monotone with pregame exposure: +14.5–18.6% at < 10, +5.5–6.0% at 10–20, about 0 at 20–30, and −2.6% / −3.2% at
30+. Minutes are not compressed in the mean. The low tail is also inflated by the structural Jensen term.

## 8. Cross-check by stat (min_mu ≥ 30, 2024-25 | 2025-26)

| Stat | Total | Minutes | Rate (relative) | Structural |
|---|---|---|---|---|
| AST | −0.112 \| −0.073 | +0.023 \| +0.014 | −0.091 \| −0.095 (−3.2% \| −3.3%) | −0.044 \| +0.008 |
| FG3M | −0.075 \| −0.029 | +0.005 \| −0.001 | −0.052 \| −0.037 (−3.8% \| −2.9%) | −0.027 \| +0.009 |
| TOV | −0.071 \| −0.054 | +0.030 \| +0.027 | −0.032 \| −0.018 (−1.0% \| −0.1%, not significant) | **−0.070 \| −0.063** (environment / duration remainder −0.046 / −0.043) |

* **AST and FG3M:** the same pattern as PTS. The rate is under at high exposure (the blend dominates: AST −0.20, FG3M
  −0.11 to −0.12) and over at low exposure, and minutes are ≈ 0 or positive.
* **TOV is different.** Its rate error at high exposure is small. Most of its bias is structural: the environment and
  duration remainder (the TOV GLM has pace +1.33 and own efficiency −0.68).
* **Overall:** this points to rate compression shared by the scoring and creation stats, **not** to a shared minutes
  compression.

## 10. Frozen components that can compress toward the population (all actually present)

| Component | Present | Explains the monotone pattern? | Evidence |
|---|---|---|---|
| Minutes ridge (α = 1e-4 × Σw) and clip [1, 45] | yes | **No.** High-exposure minutes are over-predicted on the mean; the penalty is negligible. | §3 |
| Rate gamma prior toward the position-group rate (k ≈ 31 PTS) | yes | **Partly.** It pushes the low tail up (+0.08, w ≈ 0.21–0.34) and the high tail down slightly (−0.06 / −0.08, w ≈ 0.04). | rate trace |
| Recency-weighted multi-season rate history (half-life 1, 3 prior seasons, fixed) | yes | **Yes; the main high-exposure driver.** The blend sits about 0.9 points below the current-season rate for 30+ players. Because the current-season rate alone overshoots (+0.63), the miss comes from the blend's fixed weights. | rate trace |
| Exposure-invariant rate: Phase 1B rate does not depend on min_mu; the GLM β applies only to log(M/min_mu) deviations | yes | **Consistent with the pattern.** The predicted rate is a history average that ignores how per-minute production rises with role. β = 1.30 > 1 shows per-minute output rising with minutes, but only around min_mu. | §4–6 |
| Structural GLM β = 1.30 with a symmetric clipped-normal minutes draw and floor 1 (anchor α = 1, so not re-anchored) | yes | **The low tail only:** +0.29 to +0.43 PTS. Simulated J exceeds realized J at min_mu < 10 (1.57 vs 1.30). | §4–6 |
| Mean floor 0.01 | yes | No (binds on 0 PTS rows). | checks |
| Environment and duration terms | yes | Not monotone. They flip sign between seasons for PTS; for TOV they are the main high-exposure component. | §8 |

## Conclusion (for architect review; nothing repaired)

* **Root cause of the high-exposure PTS under-forecast:** the Phase 1B per-minute RATE, not minutes.
* **The structural mechanism:** the frozen rate is a fixed recency-weighted blend of current and prior seasons
  (half-life one season), shrunk lightly toward a position prior, and it does not depend on the player's pregame
  exposure. High-exposure players typically produce more per minute now than in their prior-season history. The fixed
  blend under-weights that level, and the gamma prior adds a little more.
* **The low-exposure over-forecast:** the same rate compression (with a larger prior weight), plus a second,
  structural inflation. The β = 1.30 minutes elasticity is applied over a symmetric clipped-normal minutes draw that is
  more convex than realized minutes at small min_mu.
* **No code defect was found.** Every component reproduces its documented formula exactly. These are specification
  choices (fixed weights, an exposure-invariant rate, and the shape of the minutes-draw law) interacting with
  exposure.
