# PTS RATE CHALLENGER (R1) — RESULT: R1_REJECT

**Records.** Protocol 3e9e52c (frozen before any R1 quantity). Fit and code 2656640. Addendum A1–A4. Machine-readable files:
- `pts_rate_challenger_fit.json`
- `pts_rate_challenger_eval_2024_25.json`
- `pts_rate_challenger_eval_2025_26.json`
- `pts_rate_challenger_results.json`

Per-row scratch evidence is in `data/qa/large/v2_pts_rate/` (gitignored).

**Design.** R1 = R0 except the PTS rate: `r_R1 = (cur_pts + tau r_prior)/(cur_min + tau)`.
- `r_prior` is the frozen R0 estimator without the current season.
- R0 is the special case tau_i = Hp_min + k.
- One global tau = **74.23 pseudo-minutes**:
  - grid index 136 of 0..240, interior;
  - fitted by mean PTS log score of the frozen Phase 1B NB on 2022-23 + 2023-24 (222,902 PLAYED rows);
  - fit-season log score 2.8377 → 2.8337;
  - the CRPS argmin is the same grid point.

**Exact scoring.** Analytic rescaling was proven inexact (protocol section 5). Both seasons were rescored with the frozen JM1_FINAL PTS path, 20,000 draws per game, using common random numbers.
- R0 reproduces the stored rows to 5.95e-8 (float32 storage).
- Combos are scored on a fixed 20% game subsample. Combo means use all rows.

## Gates (2024-25 dev / 2025-26 retrospective confirmation)

| Gate | 2024-25 | 2025-26 |
|---|---|---|
| A. ≥ 30 min abs bias reduced ≥ 25% | PASS −0.369 → +0.190 (48%) | **FAIL** −0.333 → +0.289 (13%) |
| B. compression C = bias[0,10) − bias[30+] reduced ≥ 25% | PASS 1.069 → 0.549 (49%) | PASS 1.108 → 0.512 (54%) |
| C. aggregate ΔLOG upper ≤ +0.002 | PASS −0.0040 [−0.0048, −0.0033] | PASS −0.0048 [−0.0055, −0.0041] |
| D. aggregate ΔCRPS_SCALED upper ≤ +0.002 | PASS −0.0028 (−0.71%) | PASS −0.0032 (−0.79%) |
| E. ≥ 30 ΔLOG / ΔCRPS_SCALED upper ≤ +0.005 | PASS −0.0072 / −0.0055 | PASS −0.0088 / −0.0060 |
| F. < 20 abs bias worsening ≤ 0.05; scores | PASS +0.011 | PASS +0.019 |
| G. early season (team_games ≤ 2; first 30 days) | **FAIL**: first-30-days abs bias +0.204 | **FAIL**: first-30-days abs bias +0.231, mid-PIT worsening 0.011 |
| H. tails (ALL, ≥ 30) worsening ≤ 0.01 | **FAIL**: ≥ 30 upper miss +0.018, cov80 +0.011 | **FAIL**: ALL q50 +0.012, q75 +0.014; ≥ 30 upper miss +0.019, cov80 +0.012 |
| I. PRA / PR / PA | **FAIL**: abs bias worsens +0.16 (scores improve) | **FAIL**: abs bias worsens +0.20 (scores improve) |
| J. numerics / cold start | PASS | PASS |

## What happened

R1 improves every proper score, but it over-corrects the mean.

- **High exposure flips to an over-forecast.** The ≥ 30 PTS bias goes from −0.37 / −0.33 to +0.19 / +0.29.
- **Aggregate PTS over-forecast doubles.** It goes from +0.164 / +0.212 to +0.326 / +0.409.
- **Upward shift outside the target band.** At [20,30) the bias moves from −0.10 / −0.07 to +0.06 / +0.16. The current-evidence buckets (0,100], (100,300] and 300+ all move up by about 0.11 to 0.26.
- **Low exposure is unchanged.** The low band (< 10: +0.70 / +0.77) is untouched because those players carry little current-season evidence.
- **So the compression "flattening" is mostly a level shift** of the upper bands, not removal of the low-tail over-forecast.

Mechanism, consistent with the root-cause audit:
- The season-to-date rate over-states the realized next-game rate. The root-cause audit's CURRENT_RATE_VS_REALIZED term is +0.63, which is selection and regression to the mean.
- A small global tau (74 minutes; w ≈ 0.87 beyond 300 current minutes) trusts that evidence too much for the MEAN.
- The log score still prefers it because it sharpens the rate ranking. The mean errors partly offset under the frozen JM1 structural layer.

**Other results:**
- **Evidence curve.** w = 0 / 0.35 / 0.71 / 0.87 by bucket. Zero-evidence rows are bit-identical to R0, which is sensible empirical-Bayes behavior. The bias rises in every bucket with evidence.
- **Early season.** team_games ≤ 2 improves: |bias| −0.10 / −0.14, log −0.005 / −0.009. The first 30 days over-forecast more: +0.08 → +0.29 and +0.02 → +0.25.
- **Transfers.** ΔLOG −0.003 / −0.010. Bias +0.02 → +0.17 and −0.39 → −0.20.
- **Cold / no-history rows.** Unchanged (zero evidence).
- **Combos.** PRA / PR / PA log score, CRPS (about −0.5% to −0.7%) and Brier improve. Mean bias worsens by +0.16 / +0.20 overall. At ≥ 30, PRA goes −0.44 → +0.12 and −0.32 → +0.30.
- **Other stats.** REB / AST / FG3M / STL / BLK / TOV means and conditional log means are bit-identical. Minutes, JM1 structure, dispersion and J2 are untouched.

**Decision.** R1_REJECT. Gates A (2025-26), G, H and I fail. Per the directive:
- no tau retuning;
- no R2;
- no promotion.

LOW_EXPOSURE_JENSEN_ISSUE_REMAINS = TRUE.
