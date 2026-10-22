# PTS RATE — MODEL-ALIGNED CONSTRAINED SELECTION FROM EXISTING R2 CANDIDATES — FROZEN PROTOCOL

Architect directive 2026-10-22. Branch `research/cbb-v2-pts-rate-constrained-selection`, created from 49d1a99 (R2 final; R2_EARLY_REJECT, which stands). R2 was pushed (remote SHA 49d1a99) before this branch was created.
Status: RESEARCH. Production baseline `e1353e5` is unchanged. PUBLIC LAUNCH: HOLD. No R3, no new grid, no new formula, no calibration fallback, no refit of minutes / dispersion / J2 / environment.

This file is committed BEFORE any candidate is screened, ranked or scored under this procedure, and before the accounting check is run. After the commit, none of the items below may change.

Protected file: `data/qa/game_identity_questionable_only.csv` (untracked) must keep sha256 `5ca9001c7c4e700b6ca0dd0e27c20290982d0051748ff96fa28317fa002f15b2`.

## 1. Candidate universe (existing only)

- The candidates are every (tau, rho) record in `pts_rate_challenger_r2_fit.json` (committed 61f26f5):
  - `coarse_surface`: 861 records;
  - `refine_surface`: 441 records;
  - 1,302 records in total.
- Pairs that coincide (|Δlog tau| ≤ 1e-9 and |Δrho| ≤ 1e-9) are merged into one candidate. The count of distinct candidates is reported.
- No other tau or rho value is ever evaluated.
- The rate formula is R2's (`pts_rate_challenger_r2.r_r2`). R0 is the comparator only; it is not counted as a candidate.

## 2. Data roles

- 2022-23 + 2023-24: the historical fit and reference data that produced the R2 surface. They are not reused here.
- 2024-25: DEVELOPMENT / SELECTION.
- 2025-26: FIXED-PROCEDURE RETROSPECTIVE CONFIRMATION of exactly one locked candidate.
- Neither season is a pristine holdout: R0, R1 and R2 have all been examined on 2024-25, and R0 and R1 on 2025-26.
- No candidate is selected using 2025-26, and nothing changes after 2025-26 is inspected. The code refuses to run any 2025-26 step without a committed lock record.

## 3. Step 0 — read-only accounting (no simulation)

The rows are the 2024-25 PLAYED scored rows in the R1 scratch file `data/qa/large/v2_pts_rate/2024-25_pts_rows.parquet`, whose R0 columns are the exact frozen JM1. `min_sigma` comes from the stored source `develop_2025_rows.parquet`.

Definitions:

- `mu_JM1` = R0|mean, the frozen R0 final JM1 PTS predictive mean.
- `r0` = r_R0, the frozen Phase 1B PTS per-minute rate.
- `E_JM1_minutes` = the expectation of the JM1 scorer's minutes draw, M = clip(min_mu + min_sigma·eps, 0, 60) · D / E[D].
  - eps and D are independent streams, so E[M] = E[clip(N(min_mu, min_sigma), 0, 60)], computed in closed form.
  - The Monte Carlo factor E_b[D]/E[D] is ≈ 1 and is not included.
- `actual_minutes` = y_min; `actual_PTS` = y_PTS.

The three terms:

```
A = mu_JM1 - r0 * E_JM1_minutes
B = r0 * (E_JM1_minutes - actual_minutes)
C = r0 * actual_minutes - actual_PTS
```

Identity check: max |A + B + C − (mu_JM1 − y_PTS)| ≤ 1e-9 → PASS.

A, B and C are reported (row means) by:
- the four min_mu bands, LOW < 20 and ALL;
- the evidence buckets;
- team games ≤ 2;
- first 30 days.

This is accounting, not attribution.

Nonlinearity diagnostic (closed form, by Gauss–Hermite quadrature on eps with 64 nodes, plus the exact discrete overtime distribution of D):

```
K   = E[(max(M,1) / min_mu)^beta]
K_lin = (E[M] / min_mu)^beta
```

- beta is the frozen PTS `log_minutes_ratio` coefficient of the 2024-25 parameters.
- The ratio K / K_lin is reported by band.
- The word "Jensen" is used only if K / K_lin > 1 demonstrably accounts for the low-band excess.

**Implementation-defect hard stop.** If the identity fails, or the trace shows a reproducible defect, STOP with IMPLEMENTATION_DEFECT_FOUND = TRUE. A defect means the scorer computes something other than its documented formula, for example the anchor not being a no-op, or minutes not following clip × D/E[D].

## 4. Step 1 — JM1 marginal scoring contract and the rate-ratio scaling check (no season run)

Contract (code trace of `jm1_final.eval_game` / `structural`):

```
p(y | X) = (1/B) sum_b NB(y; exp(GRID_H round(lm_b / GRID_H)), phi_PTS)
```

- The mixture PMF is computed first; −log p(y) is taken after.
- The log score uses the mixture PMF; per-draw conditional log scores are never averaged.

Scaling questions, settled by code trace plus a tiny numerical check:

- **A.** Is the mean scaling exact?
- **B.** Is the full PMF scaling exact?
- **C.** Which approximation did the R2 screen use?

The numerical check uses the R1 exact rows, which are already computed for every 2024-25 row: R1|mean (exact) against R0|mean × mu1B_R1 / mu1B_R0.
- The discrepancy is reported (max abs, mean abs, and the effect on the stratum biases used in the constraints).
- This also validates the cheap screen's bias surrogate on a candidate with rho = 1.
- No new simulation is run for this check.

## 5. Step 2 — cheap feasibility screen (2024-25 only, every distinct candidate)

The screen uses exactly the R2 stage-A machinery:
- **Bias:** the screen mean is R0|mean (exact JM1) × mu_1B'(cand) / mu_1B_R0.
- **Scores:** Phase 1B NB log score / CRPS with `props_v0.pmf_matrix`, phi_F2025.
- **Bootstrap:** none. Point estimates only.

A candidate is CHEAP-FEASIBLE iff ALL of the following hold. R0 values are the same rows' exact JM1 means.

- **A.** |bias(≥30)| ≤ 0.75 |bias_R0(≥30)|.
- **B.** bias(≥30) ≤ +0.15.
- **C.** |C| ≤ 0.75 |C_R0|, where C = bias[0,10) − bias[30,∞).
- **D.** |bias(ALL)| ≤ |bias_R0(ALL)| + 0.05.
- **E.** |bias(<20)| ≤ |bias_R0(<20)| + 0.05.
- **F.** |bias(first 30 days)| ≤ |bias_R0(first 30 days)| + 0.05.
- **G.** Team games ≤ 2, using the bias and score parts of the established gate G:
  - |bias| worsening ≤ 0.05;
  - Phase 1B ΔLOG ≤ +0.005;
  - Phase 1B ΔCRPS_SCALED ≤ +0.005.
  - The PIT and coverage parts need the JM1 PMF and are checked in step 4.
- **H.** Aggregate Phase 1B ΔLOG ≤ +0.002 and ΔCRPS_SCALED ≤ +0.002 (the established surrogate and margin).

Strata: the R1 / R2 definitions (`pts_rate_challenger_r2.masks`).

**If CHEAP_FEASIBLE_CANDIDATES = 0: STOP.**
- WEIGHTING_FAMILY_FEASIBLE = FALSE, and WEIGHTING_FAMILY_FINAL_DECISION = WEIGHTING_FAMILY_INFEASIBLE.
- Recommend a PTS rate calibration-geometry audit as the next architect decision.
- No JM1 scoring, no 2025-26.

## 6. Step 3 — shortlist (at most 3)

- Rank the feasible distinct candidates by the 2024-25 aggregate Phase 1B NB mean log score, ascending.
- Ties: lower Phase 1B CRPS, then the smaller Euclidean distance to rho = 1 and log tau = log 74.70.
- Take the top 3, or all of them if fewer are feasible. No manual choice.

## 7. Step 4 — actual JM1 development scoring (2024-25)

**Compute estimate first.**
- Time the exact one-pass scorer (shared exposure / environment draws; R0 + every shortlisted rate in the same pass; PTS only) on the first 40 games by sorted game_id.
- Extrapolate to all games.
- If the estimated CPU exceeds 10 minutes: STOP before running, and report the estimate and the cheapest exact alternative.

**Scorer.**
- Frozen JM1 PTS path as in R1 / R2 (`simulate_exposure`, then `log_mu_terms`, then `anchor` with alpha = 1, then `mixture_tables`, then `mixture_pmf`, then `single_metrics`).
- 20,000 draws, frozen seeds, the same draws for every arm.
- Precondition: R0 reproduces the stored rows within relative 2e-6.

**Per-candidate gates.** These are the R2 exact gates (R2 protocol section 9) A, B, C, D, E, F, G, H, J and K, unchanged:
- A includes the +0.15 overshoot cap.
- C and D: the upper bound of the aggregate ΔLOG / ΔCRPS_SCALED is ≤ +0.002 (R2 ≤ R0 within uncertainty).
- E: high-exposure scores.
- G: the full early-season gate, including mid-PIT and cov80.
- H: tails, with no override.

Survivors are the candidates that pass all of them.
- **If ACTUAL_JM1_FEASIBLE_CANDIDATES = 0:** STOP. Decision WEIGHTING_FAMILY_INFEASIBLE.

## 8. Step 5 — lock exactly one

**Selection.** Among the survivors, take the lowest actual 2024-25 JM1 PTS mean log score. Ties: lower CRPS, then closer to R0 in parameter space (smaller rho, then larger tau).

**Lock record.** `pts_rate_constrained_lock.json`, holding:
- tau and rho;
- all 2024-25 metrics;
- the rule.

It is committed BEFORE any 2025-26 computation. The lock commit SHA is reported.

## 9. Step 6 — 2025-26 confirmation (the locked candidate only)

- The same scorer and the same gates.
- **Any primary gate fails:** CONSTRAINED_CANDIDATE_REJECT, and the weighting campaign stops (decision LOCKED_CANDIDATE_REJECTED).
  - No second place, no return to the surface.

## 10. Step 7 — combos (only if 2025-26 passes)

- R2 protocol section 8 step 3 and gate I, verbatim:
  - the R1 fixed 20% subsample;
  - same draws;
  - REB / AST draws identical across arms;
  - combo bias on all rows by linearity.
- Then STOP. CANDIDATE_SURVIVES pending architect review. No refit.

## 11. Compute and discipline

- Steps 0–3 are analytic. The step 2 estimate is about 1,302 × 0.07 s ≈ 2 min.
- Step 4 runs only within the 10-minute CPU budget. Steps 6–7 each also re-estimate first, and STOP if above 10 minutes.
- Single thread (OMP / OPENBLAS / MKL / VECLIB / NUMEXPR = 1), `nice -n 15`.
- No API calls, no historical rebuild, no full-suite rerun. MARKET_SUPERIORITY = NOT_ESTABLISHED; no sportsbook data is fetched.

Outputs:
- `pts_rate_constrained_accounting.json`;
- `pts_rate_constrained_screen.json` (the per-candidate constraint table);
- `pts_rate_constrained_results.json`;
- `PTS_RATE_CONSTRAINED_SELECTION_REPORT.md`.
