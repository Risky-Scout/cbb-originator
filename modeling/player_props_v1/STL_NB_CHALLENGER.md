# CBB v1 STL Poisson → Negative Binomial dispersion challenger: ACCEPT (not activated)

**Experiment only. Not activated, not committed. No model source changed.** Code:
`cbb_platform/props_v1_stl_nb_challenger.py`. Results: `stl_nb_challenger.json`. Tests:
`tests/test_props_v1_stl_nb_challenger.py`.

## Setup

* **Frozen baseline:** HEAD and tag are `c123a3b`. The artifact sha256 is `3d343eb9…`. No tracked file differs from the
  frozen commit, and 2025-26 is refused.
* **One change:** the STL conditional PMF goes from Poisson to the existing Negative Binomial (mean μ, variance
  μ + φμ²), built by the frozen `props_v0.pmf_matrix(mu, phi, kmax)`. No source change was needed and no second NB
  formula was written. The STL mean is untouched: the maximum |μ_challenger − μ_baseline| is 0.
* **Selecting φ:** on the grid 0.00 to 2.00 by 0.01 (201 points), by lowest mean log score on evaluation A (fit
  ≤ 2022-23, predict 2023-24; n = 111,948). The frame was loaded with seasons ≤ 2023-24 only. Ties within 1e-12 go to
  the smaller φ.
* **Applying φ:** φ is frozen and applied to evaluation B (fit ≤ 2023-24, predict 2024-25; n = 112,892). These are
  the same rows as the frozen evaluation, and the baseline reproduces the frozen metrics to within 1e-12.

**Selected φ_STL = 0.16.** This is interior to the grid, so it is not a boundary optimum.

## 2023-24 fit

* Means: observed 0.65784, and 0.67659 for both baseline and challenger.
* MAE: 0.64836 for both.
* Log score: 1.01276 → 1.00978 (Δ −0.00298).

## 2024-25 forward validation

| metric | baseline | challenger | Δ |
|---|---|---|---|
| mean (observed 0.67449) | 0.68448 | 0.68448 | 0 |
| MAE | 0.65291 | 0.65291 | 0 |
| log score | 1.02493 | 1.02228 | −0.00265 |
| CRPS | 0.39975 | 0.39932 | −0.00043 |
| coverage 50 / 80 / 95 (report only) | .8530 / .9445 / .9834 | .8595 / .9491 / .9868 | |

| line | observed | pred b → c | gap b → c | Brier b → c | log loss b → c |
|---|---|---|---|---|---|
| 0.5 | .4331 | .4567 → .4397 | +.0236 → +.0066 | .20854 → .20815 | .60316 → .60229 |
| 1.5 | .1628 | .1615 → .1621 | −.0014 → −.0007 | .12030 → .12027 | .38523 → .38507 |
| 2.5 | .0552 | .0488 → .0555 | −.0064 → +.0003 | .04858 → .04854 | .18492 → .18405 |

Three-line means, baseline → challenger:

* \|gap\|: .010448 → .002520
* Brier: .125805 → .125651
* log loss: .391103 → .390467

## Distribution diagnostics (observed / baseline / challenger)

There are at least 100 observed STL ≥ 4 outcomes in both seasons (1,815 and 1,912), so P(≥4) is reported.

| quantity | 2023-24 | 2024-25 |
|---|---|---|
| P(0) | .5764 / .5468 / .5636 | .5669 / .5433 / .5603 |
| P(≥1) | .4236 / .4532 / .4364 | .4331 / .4567 / .4397 |
| P(≥2) | .1590 / .1587 / .1595 | .1628 / .1615 / .1621 |
| P(≥3) | .0530 / .0476 / .0542 | .0552 / .0488 / .0555 |
| P(≥4) | .0162 / .0128 / .0178 | .0169 / .0132 / .0183 |

## Isolation and integrity

* **Other outputs:** across 261,128 pregame candidates for 2024-25, every table column is bit-identical to the
  baseline. That covers participation, minutes, and every stat mean including STL. Every PMF except STL is also
  bit-identical.
* **Challenger PMFs:** they sum to 1 within 1e-12, have no negative entries, and P(over) never rises as the line rises.
* **Determinism:** two independent runs produce byte-identical JSON.

## Acceptance

**A–J all PASS.**

**Decision: ACCEPT.** Neither tail gets worse at the 1.5 line's expense. φ = 0.16 removes most of the 0.5 defect (gap
.0236 → .0066) and essentially all of the 2.5 defect (gap −.0064 → +.0003). The 1.5 line also improves slightly, as do
both proper scores.

Even so, the challenger is **not activated and not committed**; that requires an architect decision. If it is
activated, the full test suite is mandatory before commit.

Report-only observation: zeros are still slightly under-predicted (P(0) .5603 vs .5669 observed in 2024-25), and
P(≥4) is now slightly over-predicted.
