# PTS RATE — CALIBRATION-GEOMETRY AUDIT — REPORT (DIAGNOSIS ONLY)

| item | value |
|---|---|
| Branch | `research/cbb-v2-pts-rate-calibration-audit` (from 734bb25) |
| Protocol | `PTS_RATE_CALIBRATION_AUDIT_PROTOCOL.md`, frozen at db54556 before any calibration quantity |
| Module | `cbb_platform/v2/pts_rate_calibration_audit.py` |
| Numbers | `pts_rate_calibration_audit.json` |
| Compute | 28 s wall, 35 CPU-s |
| Baseline | `e1353e5` unchanged |
| Public launch | HOLD |

**DECISION: CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED. RATE_COMPRESSION_CONFIRMED = FALSE.**

The frozen R0 PTS rate is **not** cross-sectionally compressed:
- Its relative spread is right, or very slightly too *wide* (b ≈ 0.97 everywhere).
- The exposure bias lives in an expected-minutes (role) signal that is **orthogonal** to the predicted rate. A global rate map cannot represent it.

## Data

The universe is PLAYED rows with `y_min > 0` in the true point-in-time OOS Phase 1B stores:

| season | rows |
|---|---|
| 2022-23 | 110,773 |
| 2023-24 | 111,052 |
| 2024-25 | 112,144 |
| 2025-26 | 111,673 |

r_R0 is recovered with `pts_rate_challenger.rate_parts` (the verbatim `props_v0._predict_means` rate):
- max |max(min_mu·r_R0, floor) − PTS_mu| = 0 in all four seasons;
- it is bit-identical to the R1 scratch rows' `r_R0` and `min_mu` for 2024-25 and 2025-26.

PIT `team_games` is identical to the stored JM1 rows.

2024-25 and 2025-26 are **not** pristine holdouts.

## Fixed reference quintiles of r_R0

Boundaries: 0.2619 / 0.3119 / 0.3576 / 0.4151.

Relative error (predicted − actual) / actual, minutes-weighted, by quintile:

| season | Q1 | Q2 | Q3 | Q4 | Q5 | compression pattern |
|---|---|---|---|---|---|---|
| 2022-23 | −2.2% | +0.1% | +0.4% | +0.9% | +1.6% | no |
| 2023-24 | −1.9% | −1.9% | −0.4% | −0.5% | +0.6% | no |
| 2024-25 | −0.3% | −0.2% | +0.7% | −0.3% | +2.5% | no |
| 2025-26 | −1.4% | −0.3% | +0.3% | +0.0% | +1.7% | no |

All quintiles are within ±2.5%. The tilt is the *opposite* of compression in every season: low predicted rates run slightly low, high predicted rates run slightly high. The reliability curve is close to the diagonal.

## NB2 offset calibration

Model: log E[PTS] = log(actual min) + a + b log r_R0, with game-clustered 95% CI.

| sample | a | b | 95% CI | N |
|---|---|---|---|---|
| REFERENCE (22-23 + 23-24) | −0.058 | 0.971 | [0.961, 0.982] | 221,825 |
| 2024-25 | −0.065 | 0.973 | [0.958, 0.988] | 112,144 |
| 2025-26 | −0.064 | 0.971 | [0.956, 0.986] | 111,673 |
| (info) 2022-23 / 2023-24 | | 0.964 / 0.978 | | |

- The slope is extremely stable (Δ ≤ 0.002).
- It is just below 1: predicted rates are marginally too dispersed, about 3% in log spread. That is not material.
- It is nowhere near the 1.05 compression threshold. Criteria 1, 2, 3 and 5 fail.

## min_mu after rate control (the actual signal)

Extension: + c·z(min_mu), with reference mean 20.68 and SD 9.19.

| sample | c | 95% CI |
|---|---|---|
| REFERENCE | +0.081 | [+0.078, +0.085] |
| 2024-25 | +0.077 | |
| 2025-26 | +0.087 | |

Size of the effect:
- +8.4% realized rate per SD of min_mu at fixed r_R0;
- min_mu 32 vs 10 at the same r_R0: ×1.21 realized PTS/min.
- This is four times the 0.02 materiality bar, and stable across seasons. Criterion 6 fails.
- With c in the model, b falls to about 0.87. Given expected role, the rate is even more over-dispersed.

## Strata

Rates below are minutes-weighted, predicted vs realized. b is the within-stratum slope.

**Exposure.** The bias is band-level, and *within* every band b < 1:

| band (min_mu) | REFERENCE rel. error | 2024-25 | 2025-26 | b |
|---|---|---|---|---|
| < 20 | +6.7% | +7.3% | +7.2% | 0.83 |
| 20–30 | −0.5% | | | 0.92–0.94 |
| ≥ 30 | −3.3% | −2.6% | −3.2% | 0.90–0.93 |

The exposure geometry is NOT the rate-calibration geometry. It is a role/exposure level effect at a given rate.

**Position.**
- G, F: b 0.95–0.99.
- C: b 1.04–1.08; its 2025-26 CI [1.012, 1.140] lies on the opposite side of 1 from the global slope, which counts as a reversal under the frozen rule.
- U: b ≈ 0.85–0.88, with −6% to −7% error.
- Criterion 7 fails, on C 2025-26.

**Current-season evidence.**

| cur_min | rel. error | b |
|---|---|---|
| 0 | −7% to −10% (under) | ≈ 0.85–0.87 |
| (0, 100] | +2% to +4% (over) | ≈ 0.83 |
| (100, 300] | +1% to +2% | ≈ 0.95–0.97 |
| > 300 | ≈ 0 | ≈ 1.01 |

- With substantial evidence the rate is essentially perfectly calibrated, in both level and spread.
- All residual mis-spread sits in low-evidence rows, and it is *over-dispersion* (b < 1), not compression.
- That explains why tau/rho re-weighting could not fix the exposure bias: the well-evidenced rate is already right.

**Early season.**

| stratum | b | rel. error |
|---|---|---|
| team games ≤ 2 | ≈ 0.79 | −4% to −6% (under) |
| first 30 days | 0.83–0.86 | |
| remainder | ≈ 1.01 | +0.5% to +1.4% (over) |

- The level sign reverses relative to the remainder in all three samples. Criterion 8 fails, in 6 of 6 checks.
- This is the same opening-season under-forecast seen in the directional audit.

**Cold start.**
- COLD_EARLY_SEASON: b 0.77–0.84, −3% error.
- COLD_TRANSFER_IN: b 0.66–0.76, −2% to −7% error.
- COLD_NO_HISTORY: N < 2,000, not estimable.

## Cold-start implication

Any global map r' = exp(a)·r^b would necessarily change every cur_min = 0 and low-evidence prediction, because the r_R0 inputs there are pure prior/history blends.

Those groups have opposite-signed level errors:
- cur_min = 0: under;
- (0, 100]: over.

Their spread errors also differ from the well-evidenced rows (b ≈ 0.85 vs ≈ 1.01). A single global intercept + slope cannot correct them coherently, and would distort the cur_min > 300 rows, which are currently calibrated.

## Decision criteria (frozen §11)

| # | criterion | result |
|---|---|---|
| 1 | REF | FAIL |
| 2 | S25 | FAIL |
| 3 | S26 | FAIL |
| 4 | CONSISTENT | pass (slopes agree, but at 0.97) |
| 5 | CURVES | FAIL |
| 6 | MIN_MU | FAIL |
| 7 | POSITION | FAIL |
| 8 | EARLY | FAIL |

→ **CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED**

## Caveat

The regression conditions on target-game actual minutes as exposure (diagnostic only). Part of the min_mu signal can reflect *which* minutes are played: low-role players' minutes skew to low-leverage or garbage time. It is not purely a per-minute skill miss.

Either way, it is a role/exposure effect, not rate-spread compression, and nothing here is fitted for production.

## Recommendation

STOP the historical PTS repair campaign. Keep `e1353e5` as the frozen shadow baseline, and continue prospective side/calibration monitoring on real 2026-27 quotes and outcomes.

No calibration transform, challenger or R3 was built.
