# PTS RATE CHALLENGER R2 — DAMPED ADAPTIVE EMPIRICAL-BAYES — FROZEN PROTOCOL

Experiment: two-parameter damped adaptive PTS per-minute rate (architect directive 2026-10-22, "one final pre-launch rate experiment").
Branch: `research/cbb-v2-pts-rate-challenger-r2`, created from e4b6e3d (R1 result; R1_REJECT). The R1 branch `research/cbb-v2-pts-rate-challenger` was pushed unchanged before this branch was created.
Status: RESEARCH. Nothing here changes `e1353e5`, a frozen artifact, a release branch, `main`, the Odds API code, the CBBD feed or the side monitor. PUBLIC LAUNCH: HOLD.

This file is committed BEFORE any R2 quantity (fit-season score at any (tau, rho), parameters, screen or evaluation) is computed.
After that commit none of the following may change: the formula, the prior, the bounds, the grids, the refinement rule, the objective, the periods, the strata, the screen, the gates or the stop conditions. There is no R3.

Unless stated otherwise, every definition is that of the R1 protocol (`PTS_RATE_CHALLENGER_PROTOCOL.md`, 3e9e52c) and its addendum (A1–A4), reused verbatim with "R1" read as "R2".

## 1. R0, R1, R2

- R0: frozen JM1_FINAL (e1353e5); R1 protocol section 1.
- R1: rejected one-parameter rate (tau = 74.23), for reference only.
- R2: identical to R0 except the PTS per-minute rate in Phase 1B.

## 2. R2 formula

The prior is EXACTLY the R1 prior (R1 protocol section 2; `pts_rate_challenger.rate_parts`). The R1 implementation was checked against its protocol (addendum A1, A2) and is not in violation, so it is reused unchanged:

```
Hp_min  = sum_{j=1..3} 0.5^j h_j_min                  (prior seasons only; NaN -> 0)
Hp_pts  = sum_{j=1..3} 0.5^j h_j_pts
r_prior = (Hp_pts + k p_pos) / (Hp_min + k)          (fold k and p_pos; the position prior enters exactly once)
```

Current-season evidence: n = cur_min, cur_pts (the same PIT season-to-date fields; NaN -> 0).

```
w(tau)  = n / (n + tau)
r_cur   = cur_pts / n                                 (defined only for n > 0)
r_R2    = r_prior + rho * w(tau) * (r_cur - r_prior)  for n > 0
r_R2    = r_prior                                     for n = 0   (exactly; no epsilon, no pseudo-evidence)
mu_1B'  = max(min_mu * r_R2, mean_floor)
```

Equivalently `r_R2 = (1 - rho w) r_prior + rho w r_cur`; the effective current-season weight is `rho * n/(n+tau)`, which rises monotonically in n and approaches rho (not 1).

Rows with n = 0 but cur_pts > 0 (source rows with points and 0 minutes; R1 addendum A1) have w = 0 and therefore r_R2 = r_prior exactly. This follows the directive's zero-evidence rule; R1's primary formula leaked cur_pts/tau there, R2 does not.

R1 is the special case rho = 1. R0 is not nested (it is a player-specific tau_i = Hp_min,i + k with rho = 1).

## 3. Exactly two free parameters

NUMBER_OF_FREE_PARAMETERS = 2: global `tau` (pseudo-minutes) and global `rho`. Nothing is position-, role-, band-, player-, season-, starter-, transfer- or freshman-specific.

- tau in [1, 2000]; rho in [0, 1]. Bounds are never expanded.

## 4. Fit (both parameters, once)

- Objective: mean PTS negative log score, −log p(y), under the frozen Phase 1B PMF `props_v0.pmf_matrix(mu_1B'(tau, rho), phi_fold, 120)`; no bias, exposure, compression or evaluation-season term. CRPS is computed and saved as a diagnostic only.
- Data: exactly the R1 fit rows: stored true-OOS Phase 1B rows, PLAYED with y_PTS, of 2022-23 (F2023) and 2023-24 (F2024), pooled with equal row weight. The R0 reproduction check (≤ 1e-9) must hold or the fit aborts.
- 2024-25 and 2025-26 are never used to fit; nothing is refit after seeing either.
- The fit is analytic and vectorized (Phase 1B NB). No JM1 Monte Carlo during fitting.

Deterministic search:

- **Stage 1 (coarse).** tau_i = 2000^(i/40), i = 0..40 (41 log-spaced points, tau_0 = 1, tau_40 = 2000); rho_j = j/20, j = 0..20 (21 points). 861 pairs, all evaluated.
- **Stage 2 (fixed local refinement).** Take the single best coarse pair (i*, j*) by fitting NLL (ties as below). The rectangle is bounded by the adjacent coarse values: tau in [tau_{max(i*-1,0)}, tau_{min(i*+1,40)}], rho in [rho_{max(j*-1,0)}, rho_{min(j*+1,20)}]. Evaluate a 21 × 21 grid inside it: tau log-spaced (geometric) with 21 points between the tau bounds, inclusive; rho linearly spaced with 21 points between the rho bounds, inclusive. 441 pairs. No further refinement.
- **Final pair.** The lowest fitting NLL over the union of both stages. Ties (exact float equality): prefer the larger tau, then the smaller rho.
- The full coarse and refinement surfaces (log and CRPS) are saved to `pts_rate_challenger_r2_fit.json`; chat shows only the optimum and the neighbouring sensitivity.

BOUNDARY_SOLUTION = TRUE if final tau ≤ tau_1 (1.209) or ≥ tau_39 (1653.8), or final rho ≤ 0.05 or ≥ 0.95. A boundary solution is surfaced and treated cautiously; it does not by itself reject R2. (At rho = 0, tau is unidentified; the tie rule then selects the largest tau.)

Compute: about 0.14 s per pair (R1 measured), about 3 minutes in total, single thread.

## 5. Periods

- DEVELOPMENT_EVALUATION: 2024-25.
- RETROSPECTIVE_CONFIRMATION: 2025-26.
- Neither is a pristine virgin holdout: R0 behaviour on both was examined in the directional and root-cause audits, and R1 was evaluated on both. tau and rho are frozen (fit committed) before any R2 result for either season is computed.

## 6. Stage A — cheap analytic screen, 2024-25 ONLY

Inputs: the R1 scratch file `data/qa/large/v2_pts_rate/2024-25_pts_rows.parquet` (one row per scored 2024-25 PLAYED row; its R0 columns are the exact frozen JM1 R0 PTS, 20,000 draws, reproduced to 5.95e-8 against the stored rows) and the 2024-25 Phase 1B rate inputs (`history(2025)`, F2025 state). Its sha256 is recorded. No JM1 draws.

Per row:

- Screen mean: `mean_R2 ≈ R0|mean × mu_1B'_R2 / mu_1B_R0`. This is the exact pre-quantization JM1 mean scaling when the floor does not bind (R1 protocol section 5); quantization makes it approximate.
- Screen proper scores: Phase 1B NB log score and CRPS, R2 vs R0, under `props_v0.pmf_matrix(mu_1B, phi_F2025, 120)`.

Strata: the R1 strata (section 7 below). Reported: aggregate log, CRPS, bias; the four bands; team games ≤ 2; first 30 days; evidence buckets with mean effective weight.

Stage-A STOP gate (point estimates). R2_EARLY_REJECT if ANY of:

- **SA.** |bias_R2(≥30)| > 0.75 |bias_R0(≥30)| (screen means).
- **SB.** |C_R2| > 0.75 |C_R0|, C = bias[0,10) − bias[30,∞) (screen means).
- **SC.** Aggregate Phase 1B NB mean ΔLOG (R2 − R0) > +0.002 nats.
- **SD.** Low exposure (< 20): |bias_R2| − |bias_R0| > 0.05 PTS (screen means).
- **SE.** First 30 days: |bias_R2| − |bias_R0| > 0.05 PTS (screen means).

If the screen fails: STOP. No exact draws, no 2025-26, no combos.

## 7. Strata (fixed; identical to R1)

- Exposure bands on pregame `min_mu`: [0,10), [10,20), [20,30), [30,∞). LOW = < 20; HIGH = ≥ 30.
- Early season: `team_games <= 2`; first 30 days (`directional_audit` season_day < 30).
- Current-season evidence buckets on n = `cur_min`: 0, (0,100], (100,300], > 300.
- Cold start: COLD_TRANSFER_IN, COLD_NO_HISTORY, COLD_EARLY_SEASON, WARM.

Metrics, bootstrap (game-clustered, 2000 draws, seed 20366, 95% percentile; "upper bound" = 97.5%) and PIT conventions: R1 protocol section 7. The evidence-bucket output reports the mean effective weight `rho * n/(n+tau)`.

## 8. Exact stages (only if the previous stage passes)

Exact scorer: the R1 exact frozen JM1 PTS path (`pts_rate_challenger.score_game`, the frozen `jm1_final.eval_game` call order), 20,000 draws per game, frozen seeds, common random numbers for R0 and R2, PTS only. Parameters: 2024-25 `joint_minutes_v2_dev_parameters.json`; 2025-26 `jm1_final_confirmation_parameters.json`. Precondition: R0 PTS rows (log / crps / mean) reproduce the stored JM1 rows within relative 2e-6, otherwise R2_INCONCLUSIVE.

Order (each step runs only if every primary gate of the previous step passes):

1. Exact 2024-25 PTS (gates A–H, J, K below). Any fail: R2_REJECT, STOP.
2. Exact 2025-26 PTS (same gates, same frozen tau / rho). Any fail: R2_REJECT, STOP.
3. Combos PRA / PR / PA (gate I), both seasons, sequentially: the R1 fixed 20% game subsample (int(sha256(game_id)[:8], 16) % 5 == 0; identical for R0 and R2), the R1 same-draw construction (REB / AST draws identical in both arms, only PTS differs; R0 J0 combo lines for both arms); combo mean bias on ALL rows by exact linearity with stored JM1 REB / AST means.

Execution: one process, sequential, `nice -n 15`, OMP / OPENBLAS / MKL / VECLIB / NUMEXPR threads = 1. Expected cost: about 13–16 min CPU per exact PTS season (R1 measured about 15.5 min including combos) and about 5 min per season for the combo subsample. These are the planned operations of this protocol. Any OTHER operation estimated above 10 min CPU: STOP and report first.

## 9. Gates (exact; all must pass in BOTH seasons for R2_ACCEPT)

R1 gates A–J (R1 protocol section 8) with the R1 thresholds, reused verbatim, plus the following R2 additions:

- **A. High exposure (≥ 30).** |bias_R2| ≤ 0.75 |bias_R0| AND bias_R2 ≤ +0.15 PTS (no over-forecast overshoot beyond +0.15).
- **B. Compression.** |C_R2| ≤ 0.75 |C_R0|. The four band biases are reported to show flattening vs a global shift (diagnostic).
- **C. Aggregate log.** Upper bound of mean ΔLOG ≤ +0.002 nats.
- **D. Aggregate CRPS.** Upper bound of mean ΔCRPS_SCALED ≤ +0.002.
- **E. High-exposure proper scores.** Upper ΔLOG ≤ +0.005 and upper ΔCRPS_SCALED ≤ +0.005.
- **F. Low exposure (< 20).** |bias_R2| − |bias_R0| ≤ 0.05; upper ΔLOG ≤ +0.005; upper ΔCRPS_SCALED ≤ +0.005. (The JM1 Jensen issue stays; beta = 1.30 unchanged.)
- **G. Early season** (team_games ≤ 2 and first 30 days, each): upper ΔLOG ≤ +0.005, upper ΔCRPS_SCALED ≤ +0.005, |bias| worsening ≤ 0.05, mid-PIT worsening ≤ 0.01, cov80 worsening ≤ 0.01.
- **H. Tails** (ALL and ≥ 30): for each of mid-PIT, P(PIT<0.25), P(PIT<0.5), P(PIT<0.75), lower miss, upper miss, cov80, cov90: |d_R2 − target| − |d_R0 − target| ≤ 0.01. NO proper-score override.
- **I. Combos PRA / PR / PA** (step 3): upper ΔLOG ≤ +0.005 per player-row; upper ΔCRPS ≤ 0.0025 × mean R0 CRPS; upper Δ(mean q25/q50/q75 Brier) ≤ +0.002; ALL rows |bias_R2| − |bias_R0| ≤ 0.05.
- **J. Numerics / cold start.** r_R2 finite and > 0 on every candidate row; within [min(r_cur, r_prior), max(r_cur, r_prior)] for n > 0; every n = 0 row (including n = 0 with points) has r_R2 = r_prior exactly, and on rows with n = 0 AND cur_pts = 0 mu_1B is bit-identical to R0; non-PTS Phase 1B means bit-identical; non-PTS JM1 conditional means identical (asserted per game); effective weight in [0, rho) with bucket means monotone increasing over the four evidence buckets; COLD_TRANSFER_IN and COLD_NO_HISTORY upper ΔLOG ≤ +0.005. The fit-season numerics must also hold.
- **K. Aggregate bias safety (new).** |bias_R2(ALL)| ≤ |bias_R0(ALL)| + 0.05 PTS.

Diagnostics only (not gates): the share of R1's aggregate log / CRPS improvement that R2 retains (R1 deltas from `pts_rate_challenger_eval_*.json`); secondary compression slope; per-bucket bias / log / CRPS.

## 10. Decision

- All of A–K pass in both seasons, the precondition holds and the fit numerics hold: R2_ACCEPT (reported as R2_ACCEPT_PENDING_ARCHITECT_REVIEW; no promotion, no refit of anything else).
- Stage-A screen fails: R2_EARLY_REJECT.
- Any exact primary gate fails: R2_REJECT.
- Reproduction precondition or a computation fails: R2_INCONCLUSIVE.
- In no case: retune tau or rho, change a gate, create R3, refit minutes / dispersion / environment / J2, or promote.

## 11. Outputs

- `pts_rate_challenger_r2_fit.json` (fit surfaces, optimum, numerics).
- `pts_rate_challenger_r2_screen_2024_25.json` (stage A).
- `pts_rate_challenger_r2_eval_{2024_25,2025_26}.json` (exact stages, if run), `pts_rate_challenger_r2_combos.json` (if run), `pts_rate_challenger_r2_results.json` (decision), `PTS_RATE_CHALLENGER_R2_REPORT.md`.
- Large row files: `data/qa/large/v2_pts_rate_r2/` (gitignored). No API calls; no historical rebuild; no full-suite rerun.

Push policy: the protocol and results stay local; after the decision, ask the architect before pushing.
