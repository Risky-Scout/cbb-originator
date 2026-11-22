# CBB causal decomposition 2026-11-22: player rate defect proven, Challenger A2 promoted for AST / FG3M / STL

Control: production `3d702b6` (JM1_FINAL daily walk-forward). Research isolated from production (local lockbox store;
Droplet reads under `ReadOnlyPaths`, writes only `audit/causal`). Player-level rows stay in gitignored
`data/qa/large/causal_20261122/`; this folder holds scripts, the frozen protocol and aggregate results.

## Evidence base (deterministic)

| dataset | rows | identity |
|---|---|---|
| D1 (`scripts/d1_build.py`): true point-in-time OOS store x lockbox pregame frame, folds F2024 / F2025 / F2026 (state through F-1) | 334,873 played player-games | every Phase 1B rate component reproduced exactly (max abs error 0.0, all 7 stats); sha256 in `d1_manifest.json` |
| LIVE 2026-27 (`scripts/e_live.py`): the production walk-forward's own 2026-27 training rows rebuilt read-only (pregame frame + opening-marginal OOS forecasts + labels, cutoff 2026-11-22 11:00Z) | 16,403 played player-games, 879 games | frame sha256 `689f26de...` = the 11-22 production model's training manifest; opening rates reproduced exactly |

Conventions: error = forecast - actual; ratio = realized / model (> 1 = under-forecast).

## Phase A: data / identity / timing

| item | evidence | status |
|---|---|---|
| quote timestamps, stale quotes, cutoff handling | first live player freeze 2026-11-22T16:01:34Z: 97 / 97 records pass (unique quote ids, parseable timestamps, 0 stale > 20 min, 0 post-cutoff / post-tip / post-freeze ELIGIBLE offers) | NOT_DEFECT |
| close capture | 97 / 97 live close lineage PASS; 11-21 44 / 44 EXACT_LINE | NOT_DEFECT |
| market line mapping, sportsbook identity, pricing | 886 / 886 frozen evaluations reproduce the stored best offer exactly; PMF-to-line pricing max diff 0.0 (11-21 A2) | NOT_DEFECT |
| result grading | grading reproduced in the 11-21 / 11-22 audits; Grades named by graded slate | NOT_DEFECT |
| game / team identity | team-map fix 17418da live; game GAME_CANDIDATE_OFFERS replay exact | NOT_DEFECT |
| training-data identity | live frame hash = production manifest | NOT_DEFECT |
| injury / lineup timestamps | not re-tested in this pass | INSUFFICIENT_EVIDENCE |

## Phase B: minutes (`scripts/a1_minutes_rates.py`)

Mean error <= 0.37 min in every role (starter +0.22, rotation -0.37, bench +0.27, low +0.05); role change, transfer,
return-from-absence <= 0.2 min; SD ratio (realized / forecast) 0.96-1.02; 50 / 80% coverage 0.50-0.56 / 0.80-0.85.
Exception: cold starts (COLD_EARLY_SEASON / NO_HISTORY / TRANSFER_IN, ~10% of rows) SD ratio 1.10-1.28, 80% coverage
0.66-0.76. The count error is NOT minutes: PTS starters -0.35 = +0.09 minutes part - 0.43 rate part.

## Phase C: per-minute rate (primary defect)

`rate = (cur + sum_j 0.5^j h_j + k prior_pos) / (cur_min + sum_j 0.5^j h_j,min + k)`, k = 30 min (PTS) / 60 (AST): the
position prior only matters at first appearances; the rate is a blend of current and prior-season rates.
* Realized / model PTS rate 1.069 when current evidence < 10% of history, 0.950 at >= 75%; AST 1.074 / 0.933.
* Where the blend leans on last season, residual rises with the role change log(min_mu / last season's min per
  appearance): PTS slope +0.122 +- 0.007; where current evidence dominates the slope vanishes (+0.001 +- 0.003).
* Low-minute players are over-forecast (PTS 0.86, AST 0.81, FG3M 0.89): the minute-weighted position prior is a
  starter-level rate.

ROOT CAUSE: prior-season history is carried forward unprojected (no development, no role-change adjustment) and the
shrinkage target has no role. This reconciles the closed 2026-10-22 campaign (re-weighting seasons only moved the level;
the residual was a role / exposure effect) and the 11-21 finding (starters under-rated).

## Challenger A -> A2 (`CHALLENGER_A_PROTOCOL.json`, committed before any fit)

One structure, 3 parameters per stat, fitted on F2024 (+F2025) only:
`G_j = exp(alpha j + beta D)` on prior-season counts, role prior `prior_pos exp(gamma z)`, z = log(min_mu / 20).
* A, F2026 holdout: log loss PTS -0.00264 +- 0.00038, AST -0.00176, FG3M -0.00087, STL -0.00032, TOV -0.00063;
  **REJECTED_AT_GATE** (AST first-appearance calibration 1.096 -> 1.311; TOV early season +0.00035).
* A2 (declared before any A2 metric): z = 0 for first appearances. F2026 second look: AST no-history 1.138, PTS max
  calibration error 0.159 -> 0.089, AST 0.186 -> 0.138.
* A2, LIVE 2026-27 (primary, never fitted on):

| stat | d log loss (A2 - inc) | d RPS | Brier | role calibration (low / bench / rotation / starter) inc -> A2 | decision |
|---|---|---|---|---|---|
| AST | **-0.00404 +- 0.00079** | -0.00394 | .2305 -> .2283 | .812/.985/1.083/1.091 -> 1.020/1.003/1.054/1.036 | **PROMOTED** |
| FG3M | **-0.00356 +- 0.00044** | -0.00214 | .1948 -> .1933 | .886/1.004/1.066/1.032 -> 1.058/1.015/1.037/.987 | **PROMOTED** |
| STL | **-0.00186 +- 0.00022** | -0.00115 | .2223 -> .2217 | 1.110/1.001/1.110/.991 -> 1.150/1.006/1.094/.971 | **PROMOTED** |
| PTS | +0.00134 +- 0.00152 | -0.00864 | .2453 -> .2434 | .890/.975/1.076/1.037 -> 1.021/.977/1.057/.994 | NOT promoted: live early season +0.0050, low-minute +0.0184 |
| REB | -0.00027 +- 0.00027 | -0.00105 | .2442 -> .2438 | worse at low / starter | NOT promoted (F2026 upper bound > 0) |
| BLK | -0.00023 +- 0.00008 | -0.00007 | = | ~ | NOT promoted (F2026 upper bound +9e-7) |
| TOV | +0.00275 +- 0.00045 | +0.00090 | worse | | NOT promoted |

Per-stat rule (stated with the decision): a stat failing any pre-declared criterion keeps the incumbent rate. The
gate criteria were not changed after the results.

PTS is informative: A2 repairs PTS calibration-in-the-large (starters 1.037 -> 0.994, low-minute 0.890 -> 1.021) but
log loss worsens exactly where the mean falls (low-minute, early season). The incumbent's inflated low-minute mean was
masking a PMF shape defect (low-minute PTS dispersion). That is the next target (Phase 4), with the corrected mean.

## Production (promotion)

`cbb_platform/prod/rate_a2.py`, applied in `prod/run.py` right after `inputs.marginal_rows` (scoring path only):
`mu_t = min_mu * rate_A2_t * SCALE_t` for AST / FG3M / STL, SCALE = L_A2 / L_incumbent (training-level factors) so the
JM1 structural intercept / mean anchor keep their fitted level. Daily walk-forward fit, dispersion and structural GLM
training rows unchanged (the evaluated configuration). Tests: `tests/test_rate_a2.py` (exact research formula, pinned
constants, wiring); `tests/test_prod_golden_equivalence.py` (incumbent path still byte-equal to the golden file with
rate_a2 off; with it on, only the AST / FG3M / STL means change). Downstream combos (PA, RA, PRA, STOCKS) change through
the JM1 joint draws; TOV's own mean is unchanged (its conditional mean moves only via the JM1 opportunity term).

## Root-cause matrix

| # | hypothesis | evidence / test | status | remediation |
|---|---|---|---|---|
| 1 | minutes expectation | A1: <= 0.37 min every role / transfer / return | NOT_DEFECT | - |
| 2 | minutes variance | cold starts SD ratio 1.10-1.28, 80% cov 0.66-0.76 (~10% rows); else ~1 | CONTRIBUTING_DEFECT | OPEN: cold-start minutes SD band |
| 3 | historical / current-season shrinkage | rate 1.07 vs 0.95 by evidence share; F2026 + live OOS | PRIMARY_DEFECT | A2 (AST/FG3M/STL promoted); PTS/REB/TOV open |
| 4 | per-minute rate estimation | = 3 (rate part dominates the count error) | PRIMARY_DEFECT | = 3 |
| 5 | role-change handling | slope +0.12 where the blend dominates, 0 where current dominates | PRIMARY_DEFECT (component of 3) | A2 beta |
| 6 | injury replacement | no pregame replacement flag in the frozen frame | INSUFFICIENT_EVIDENCE | needs a teammate-availability feature |
| 7 | transfer / new team | minutes +0.10; rate ratio PTS 0.992, AST 1.003 | NOT_DEFECT | - |
| 8 | opponent adjustment | not tested (Phase 3) | INSUFFICIENT_EVIDENCE | next after Phase 4 |
| 9 | PMF variance | live A2: low-minute PTS calibration fixed but log loss worse | CONTRIBUTING_DEFECT (exposed) | OPEN: PTS low-minute dispersion (Phase 4) |
| 10 | PMF tail shape | part of 9; population PIT / coverage near nominal (11-21 A4) | INSUFFICIENT_EVIDENCE | with 9 |
| 11 | dependence / copula | deferred: marginals still open (rule) | INSUFFICIENT_EVIDENCE | after marginals |
| 12 | market-selection mechanism | selected-side hit tracks the market; inflation ~ disagreement (11-21 A3, 11-22 replay) | PRIMARY_DEFECT (of the selected set) | fail-closed actionability; prospective gate V1 |
| 13 | large model-market disagreement | info slope 0.17 +- 0.13; Type 1 (starter mean, = 3) + Type 4 (selection, = 12) | CONTRIBUTING (Types 1 + 4) | A2 (Type 1, partial); gate V1 (Type 4) |
