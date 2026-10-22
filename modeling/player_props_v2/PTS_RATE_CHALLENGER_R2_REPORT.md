# PTS RATE CHALLENGER R2 — REPORT

Decision: **R2_EARLY_REJECT** (stage A screen, 2024-25, gate SE). Protocol 0126280 (frozen before the fit); fit 61f26f5.

## Fit (2022-23 + 2023-24, 222,902 PLAYED rows, Phase 1B NB log score)

- Search: 41 × 21 coarse grid (861 pairs). Coarse best: tau 79.08, rho 1.00. Fixed refinement 21 × 21 over tau [65.40, 95.64] × rho [0.95, 1.00].
- Final: **TAU = 74.70, RHO = 1.00. BOUNDARY_SOLUTION = TRUE** (rho on its upper bound).
- At rho = 1, R2 is R1 except on zero-minute rows (R1 tau = 74.23). The likelihood gives damping no support.
- Fit log score: R0 2.837655, R1 2.833707, R2 2.833706.
- Best coarse log by rho:

| rho | best tau | log score |
|---|---|---|
| 1.0 | 79.1 | 2.833720 |
| 0.9 | 54.1 | 2.834010 |
| 0.8 | 37.0 | 2.834848 |
| 0.7 | 30.6 | 2.836361 |
| 0.5 | 11.8 | 2.842450 |

- CRPS (diagnostic) has the same argmin.
- Numerics on the fit seasons are clean:
  - no non-finite values, no rows outside the convex hull;
  - zero-evidence rows are bit-identical;
  - every n = 0 row equals r_prior;
  - the effective weight lies in [0, rho) and is monotone across the buckets.

## Stage A screen (2024-25, analytic; 112,871 rows; 4.8 s)

| Gate | R0 | R2 | Result |
|---|---|---|---|
| SA high exposure (≥ 30) bias | −0.369 | +0.188 (−48.9% abs) | pass |
| SB compression C | 1.069 | 0.551 (−48.5%) | pass |
| SC aggregate Phase 1B ΔLOG | | −0.0045 | pass |
| SD low exposure \|bias\| change | | +0.011 | pass |
| **SE first 30 days \|bias\| change** | +0.081 | +0.284 (+0.203) | **FAIL** |

These items are not stage A gates; they are reported only:
- Aggregate bias goes from +0.164 to +0.325, and the 30+ band to +0.188. These would also fail exact gates K (aggregate bias safety) and A (the +0.15 overshoot cap).
- team_games ≤ 2 bias goes from −0.125 to +0.027.
- Evidence buckets (bias R0 → R2, mean effective weight):

| Bucket | Bias R0 → R2 | Mean effective weight |
|---|---|---|
| 0 | −0.284 → −0.284 | 0 |
| (0, 100] | +0.479 → +0.586 | 0.35 |
| (100, 300] | +0.259 → +0.412 | 0.71 |
| 300+ | −0.059 → +0.147 | 0.87 |

Not run, per the stop gate:
- the exact JM1 for 2024-25 and 2025-26;
- the combos.

## Interpretation

- Fitting tau and rho jointly by log score gives rho = 1. The fit seasons' likelihood prefers full trust in the current-season rate.
- Damping therefore cannot be identified from a proper-score objective. The R1 overshoot is not cured by a likelihood-chosen rho.
- No retune, no R3, and no promotion. Awaiting the architect.
