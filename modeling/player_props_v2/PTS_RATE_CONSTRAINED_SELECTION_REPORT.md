# PTS RATE CONSTRAINED SELECTION — REPORT

Decision: **WEIGHTING_FAMILY_INFEASIBLE**. Not one of the existing 1,296 distinct R2 (tau, rho) candidates satisfies the frozen 2024-25 cheap constraints.
- Protocol: 9efecf3, frozen before any screening.
- R2 was pushed first; remote SHA 49d1a99.
- Inputs used: the R2 fit surfaces (61f26f5) and the R1 exact 2024-25 R0 / R1 rows.
- Nothing was simulated.

## Step 0 — accounting (2024-25 PLAYED rows, R0)

Identity A + B + C = mu_JM1 − y holds: maximum error 7.1e-15, so the check PASSES.

- E_JM1_minutes is the closed-form mean of the scorer's minutes draw, clip(N(min_mu, min_sigma), 0, 60) · D / E[D].
- A = mu_JM1 − r0·E_min, B = r0·(E_min − y_min), C = r0·y_min − y_PTS.

| Stratum | N | A | B | C | Total | E_min − min_mu | mu_JM1/mu_1B | K/K_lin | A from K − K_lin |
|---|---|---|---|---|---|---|---|---|---|
| [0,10) | 17,886 | +0.279 | +0.197 | +0.224 | +0.700 | +0.43 | 1.555 | 1.153 | +0.249 |
| [10,20) | 34,770 | +0.140 | +0.088 | +0.277 | +0.505 | +0.06 | 1.038 | 1.046 | +0.210 |
| [20,30) | 40,106 | −0.011 | −0.123 | +0.030 | −0.103 | 0.00 | 1.000 | 1.015 | +0.130 |
| 30+ | 20,109 | −0.131 | +0.110 | −0.348 | −0.369 | 0.00 | 0.991 | 1.006 | +0.075 |
| <20 | 52,656 | +0.187 | +0.125 | +0.259 | +0.571 | +0.18 | 1.214 | — | +0.223 |
| team games ≤ 2 | 8,501 | +0.076 | +0.073 | −0.274 | −0.125 | — | — | — | — |
| first 30 days | 26,364 | +0.060 | +0.058 | −0.036 | +0.081 | — | — | — | — |

Evidence buckets:

| Bucket | A | B | C |
|---|---|---|---|
| 0 | +0.124 | +0.084 | −0.491 |
| (0,100] | +0.190 | +0.131 | +0.158 |
| (100,300] | +0.073 | +0.067 | +0.118 |
| 300+ | −0.025 | −0.047 | +0.013 |

Reading the table:

- **No implementation defect.** The closed form exp(c0)·K, where K = E[(max(M,1)/min_mu)^β] with β = 1.3002 (Gauss–Hermite quadrature and the exact overtime law), reproduces the scorer's mu_JM1/mu_1B by stratum: 1.560 vs 1.555 at [0,10). This confirms:
  - the anchor is a no-op;
  - minutes are drawn as clip × D/E[D];
  - the environment terms are mean-neutral.
- **The low-exposure source is the nonlinearity.** In the [0,10) band, the convexity gap K − K_lin supplies +0.249 of A's +0.279. K_lin is the same power at the mean minutes. The gap comes from the convex minutes power β > 1 applied to the floored, clipped minutes draw.
  - The two other low-band contributions are separate: minutes over-forecast (B) and a rate excess at realized minutes (C).
- **At 30+ the deficit is C, the rate itself (−0.348).** This agrees with the root-cause audit.

## Step 1 — JM1 scoring contract and rate-ratio scaling

The contract is p(y|X) = (1/B) Σ_b NB(y; exp(GRID_H·round(lm_b/GRID_H)), φ).
- `mixture_pmf` averages the probabilities first; −log is taken afterwards.
- A unit test confirms this.

**A. Is the mean scaling exact? APPROXIMATE.**
- lm_b = log mu_1B + terms that do not depend on mu_1B, and the anchor with α = 1 is a no-op. So the PRE-quantization mean E_b[exp(lm_b)] scales exactly by mu_1B'/mu_1B, unless the 0.01 floor binds.
- The reported PMF mean uses the GRID_H-quantized means and the truncated support, which makes it approximate.
- Measured on all 112,871 rows, comparing the R1 exact means with the surrogate:
  - max absolute difference 0.0011 PTS (relative 1.1e-4), mean 9.1e-5;
  - stratum bias differences ≤ 1.9e-6 PTS.

**B. Is the full PMF scaling exact? NOT_EXACT.** Per-draw quantization re-rounds, and NB with fixed φ is not a scale family on the counts.

**C. What did the screen use?** The R2 stage-A screen took the bias from the exact R0 JM1 mean × mu_1B'/mu_1B_R0 (the surrogate validated above). It took the proper scores from Phase 1B NB.

## Steps 2–3 — cheap feasibility (2024-25)

- 1,302 records, of which 1,296 are distinct (6 refinement / coarse coincidences).
- Feasible: **0**.
- Failures per constraint:

| Constraint | Candidates failing |
|---|---|
| A | 695 |
| B | 222 |
| C | 709 |
| D | 644 |
| E | 632 |
| F | 749 |
| G | 575 |
| H | 738 |

- Every candidate fails at least 2 constraints.
- R0 biases: ALL +0.164, [0,10) +0.700, 30+ −0.369, first 30 days +0.081.

The trade-off:
- 365 candidates pass the exposure constraints (A, B, C). Each of them fails aggregate bias D and first-30-days F.
  - The best on F is tau 139.9, rho 1.0: first 30 days +0.177 against a cap of 0.131, and ALL +0.275 against a cap of 0.214.
- No candidate passes the safety set D–H.
- Within this family, raising the high-exposure rate raises everyone's mean. The family moves the level, not the exposure geometry.

## Not run, per the protocol stop

- shortlist (empty);
- JM1 scoring;
- lock;
- 2025-26;
- combos.

Recommended next architect decision: a separate PTS rate calibration-geometry audit. It was not started.
