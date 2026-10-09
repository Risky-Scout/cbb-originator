# Player-prop Challenger v0

**Version:** `props-v0-challenger-1.0.0` · **Dataset:** `cbb_data_v2.4+5f19d31e524a` · **Branch:**
`modeling/player-props-challenger-v0` (from `e691b11`, tag `pre-model-integrity-part1-v2.4`) · **Status:** research
challenger. It is not production, uses no market data, and never reads 2025-26.

Code: `cbb_platform/props_v0.py` (model), `cbb_platform/props_v0_eval.py` (evaluation). Config: `config.json`.
Metrics: `metrics.json` (`python -m cbb_platform.props_v0_eval`, about 20 s). Tests: `tests/test_props_v0.py`.

## Model

| piece | method |
|---|---|
| minutes | Weighted ridge regression, implemented as numpy normal equations because no sklearn, statsmodels or scipy is installed. Inputs: 22 approved pregame features (recent / EWM / season / prior-season minutes, start rates, participation, role rank, rotation depth, returning-minutes share, usage) + missing indicators + position and cold-start dummies + home. Clipped to [1, 45] |
| per-minute rate | (recency-weighted player sums + k × position prior rate) / (recency-weighted minutes + k). Prior rate by position group (G/F/C/unknown). k is in minutes and is estimated by method of moments (gamma prior on the rate) from training player-seasons. Training values: PTS 30, AST 60, FG3M 69, REB 72, BLK 75, TOV 175, STL 254 |
| mean | predicted minutes × rate. There is no other contextual adjustment |
| distribution | One dispersion per target, estimated on training rows. NegBin when the overdispersion ratio is > 1.10, otherwise Poisson: PTS/REB/AST/FG3M/BLK are NegBin, STL/TOV are Poisson. The tail P(Y ≥ 120) is folded into the last support point, so every pmf sums to 1 (max row error 7e-15). Minutes use a discretized normal with training residual SD by predicted-minutes band |
| baseline | Season-to-date per-appearance average, shrunk toward the population with 5 pseudo-games. Falls back to the prior season, then the population. Uses the same distribution machinery, with its own training-estimated dispersion |

**History sources (all point-in-time legal):**
- Current season: `pstd_sum_*` from `feat.player_features` (as of the cutoff).
- Seasons s-1..s-3: final sums from `feat.player_box_state`, joined only when `available_at <= prediction_cutoff_utc`.
- The label store supplies target outcomes only, never history.

**Recency rule (fixed, not tuned):** half-life of 1 season, so weights are 1 / 0.5 / 0.25 / 0.125 for the current
season and the three before it. Older seasons are dropped. The same decay weights the training rows, measured from the
last training season.

**Leakage control:** every piece of state (standardization, coefficients, priors, k, dispersion, residual SDs, baseline
means) is fit from rows with season ≤ train_through. `test_no_validation_leakage_into_model_state` multiplies the
validation labels and features (and the validation-season finals) by 10 and adds 7; the state hash is unchanged.

## Results (challenger vs rolling baseline; lower is better for MAE, log score and CRPS)

Scored population: PLAYED with a box row, so predictions are **conditional on appearing**. v0 does not model whether
a player appears.

The challenger beats the baseline on **every target, every metric, in both validation seasons**. MAE improves by
4.5–23.2 % (2023-24 / 2024-25: MIN 23.2/22.8 %, PTS 8.5/8.2 %, REB 7.8/7.4 %, AST 7.2/6.9 %, FG3M 8.4/7.8 %, STL 4.9/5.0 %, BLK 6.2/6.2 %, TOV 4.5/4.5 %), with matching gains in log
score and CRPS. Full tables are in `metrics.json`; the summary table is in the session report.

## Known weaknesses (measured, not fixed: v0 is not tuned)

1. **Low-volume over-prediction.** In the 1–4 predicted-PTS bands the challenger runs about 13 % high (2023-24: predicted
   3.02 vs observed 2.65). REB shows a similar but smaller effect.
2. **Interval width.** PTS is slightly under-covered (80 %: 0.776, 95 %: 0.938). Low-count targets are over-covered at
   50 % (STL 0.85, BLK 0.88) because they are discrete and mostly 0/1.
3. **One dispersion per target.** Overdispersion almost certainly varies with role and minutes.
4. **No opponent / pace / team context** in the rate, and the minutes model is linear.
5. **Appearance is not modeled.** Not-scored rows in 2023-24 (2024-25): 123,350 (144,899) DNP_CONFIRMED,
   16,353 (2,987) UNKNOWN_PARTICIPATION, 2,085 (313) NOT_ON_ROSTER. A prop price needs P(appear) as well.

## Exclusions from scoring (validation seasons)

| reason | 2023-24 | 2024-25 |
|---|---|---|
| box row but DNP_CONFIRMED (not an observed appearance) | 19 | 32 |
| box row but UNKNOWN_PARTICIPATION (0-min ambiguous) | 6 | 0 |
| PLAYED but no box row (no outcome) | 6 | 1 |
| count labels unavailable (MIN still scored; counts kept NaN, never 0) | 0 | 4 |
| target box row of a player outside the pregame universe | 1,233 | 1,219 |
| target box row with unresolved identity | 10 | 29 |

## Participation layer (branch `modeling/player-props-v0-participation`; v0 frozen at tag `cbb-player-props-v0`)

Code: `cbb_platform/props_v0_participation.py`. Metrics and the end-to-end sample: `participation_metrics.json`
(`python -m cbb_platform.props_v0_participation`, about 10 s). Tests: `tests/test_props_v0_participation.py`.

**Target:** P(player appears). Labels: PLAYED = 1, DNP_CONFIRMED = 0. UNKNOWN_PARTICIPATION and NOT_ON_ROSTER (an
end-of-season roster label, not a confirmed DNP) are excluded and never relabeled. **Confirmed DNPs exist only from
2023-24**, because they require a complete substitution feed. Identifiable history through 2023-24 is therefore the
2023-24 season alone: 111,954 PLAYED and 123,369 DNP, with 16,359 UNKNOWN and 2,085 NOT_ON_ROSTER excluded. Earlier
seasons have positives only and would bias the model, so they are not used. Validation is 2024-25: 112,897 PLAYED,
144,931 DNP (2,987 UNKNOWN and 313 NOT_ON_ROSTER excluded).

**Model:** L2-regularized logistic regression, fit by numpy IRLS (penalty 1e-4·n on standardized coefficients, fixed).
23 approved pregame inputs: season and recent participation counts and rate, games since played, last-game status,
EWM, last and season minutes, start rate, prior-season minutes, minutes rank, rotation depth, roster / transfer /
recruit evidence, cold-start flags. Missing-value indicators are added on top.
**Benchmark:** (played + 2·last_played + 3·base_rate) / (played + DNP + 2·last_known + 3), counting season-to-date
identifiable games.

| 2024-25 | Brier | log loss | AUC | calibration-in-the-large | calibration slope |
|---|---|---|---|---|---|
| benchmark | 0.0712 | 0.2567 | 0.9690 | −0.202 | 1.657 |
| logistic | **0.0572** | **0.1922** | **0.9771** | −0.056 | 0.998 |

The weakest region is the middle: the 50–75 % bucket predicts 0.636 vs observed 0.545, and the 25–50 % bucket 0.375
vs 0.324.

**Wrapper:** `pregame_outputs()` returns, per pregame candidate, `participation_probability`, `conditional_minutes`
and `conditional_stat_distribution` (PTS / REB / AST / FG3M / STL / BLK / TOV pmfs plus MIN, all from the unchanged v0
state). DNP mass is never folded into the stat distributions, since a book may void the prop when the player does not
play. Conditional values for candidates with near-zero P(play) (for example departed players still in the pregame
pool) describe the case where they do appear, and should be read together with P(play).
v0 is unchanged: `metrics.json` regenerates byte-identically, and the wrapper's conditional means equal v0's on every
2024-25 appearance (tested).
