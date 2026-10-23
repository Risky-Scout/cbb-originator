# Historical PTS repair campaign: closeout

| item | value |
|---|---|
| Date | 2026-10-22 |
| Baseline (shadow) model | `e1353e5` (tag `cbb-v2-jm1-final-2026-27`), JM1_FINAL, frozen through 2025-26 |
| Production model change | **none**. The four frozen artifacts are unchanged. |
| Implementation defect found | **none** |
| Public launch | HOLD |

```
HISTORICAL_PTS_REPAIR_CAMPAIGN = CLOSED
NO_FURTHER_HISTORICAL_PTS_CHALLENGER_AUTHORIZED = TRUE
```

## What was found, in order

Signs are forecast − actual, so a positive value is an over-forecast. All results use stored true point-in-time
out-of-sample evidence.

1. **Directional-bias audit** (`8d324fb`, `11cf6fc`).
   * Aggregate PTS is slightly over-forecast: +0.164 in 2024-25 and +0.212 in 2025-26.
   * The bias is monotone in pregame expected minutes (`min_mu`). Below 20 minutes it is over-forecast. At 30 minutes
     or more it is **under-forecast: −0.37 / −0.33 PTS** (about −2.7% / −2.4%), flagged RED.
   * Prospective Over/Under side monitoring was added. It is diagnostic only.
2. **Root-cause audit** (`35d8705`).
   * The high-exposure deficit comes from the Phase 1B per-minute PTS **rate**, not from minutes. At 30+ minutes,
     minutes are slightly over-predicted.
   * The largest component is the fixed half-life-1 multi-season rate blend. No code bug was found.
3. **R1: one global shrinkage strength tau** (`e4b6e3d`).
   * Fitted tau = 74.23. **R1_REJECT.**
   * Proper scores improved, but the mean over-corrected: at 30+ minutes PTS bias went to +0.19 / +0.29, and the
     aggregate over-forecast doubled.
   * Gate A (2025-26), gate G (first 30 days), gate H (tails) and gate I (combo bias) failed.
4. **R2: damped adaptive rate (tau, rho)** (`49d1a99`).
   * The fit landed on the boundary: tau 74.70, rho = 1.00. The likelihood gives damping no support, so R2 is R1.
   * **R2_EARLY_REJECT.** Bias in the first 30 days worsened by 0.203 (limit 0.05), and aggregate bias went from
     +0.164 to +0.325.
5. **Constrained selection over the existing tau/rho family** (`734bb25`).
   * **0 of 1,296 candidates were feasible: WEIGHTING_FAMILY_INFEASIBLE.**
   * Every candidate that repairs the exposure bias breaks the aggregate bias or the first-30-days bias.
   * Re-weighting moves the level, not the exposure geometry. The accounting identity passed, so this is not a defect.
6. **PTS rate calibration-geometry audit** (`16cdaf9`). This was a diagnosis only.
   * Calibration slope b ≈ 0.97 in the reference seasons, in 2024-25 and in 2025-26 (CI about [0.96, 0.99]).
     **RATE_COMPRESSION_CONFIRMED = FALSE**, so **CALIBRATION_DECOMPRESSION_NOT_JUSTIFIED**.
   * The real signal is a **role / exposure** effect at a fixed rate: about +8% realized rate per SD of `min_mu`,
     orthogonal to the predicted rate.
   * At more than 300 current-season minutes the rate is calibrated (b ≈ 1.01). The residual error sits in
     low-evidence rows and early-season rows.
   * Centers run the opposite way to the global slope (b 1.04–1.08).

## Conclusion

The remaining PTS issue is **role / exposure / context dependent**. It is not a global rate-calibration problem, and
no historical re-weighting or calibration transform inside the frozen architecture fixes it without breaking other
gates.

* **No production model change** results from this campaign. e1353e5 remains the **only** shadow model.
* R1, R2, the constrained-selection code and the calibration-audit code are **never** cherry-picked into operational
  branches. The prospective shadow line (`release/cbb-v2-prospective-shadow`) descends from `35d8705` and carries no
  challenger code.
* Historical evidence (R1/R2/audit branches, reports, JSON) is kept. Nothing is deleted.

## What comes next: prospective evidence only

Future evidence comes from real 2026-27 JM1_FINAL shadow predictions at TIP_MINUS_1H, matched with real sportsbook quotes
and real settled outcomes (`cbb_platform/prod/shadow_tick.py`; see `docs/PROSPECTIVE_SHADOW_OPERATION.md`). The
monitors track exactly the strata this campaign isolated:

* high-exposure PTS (`min_mu` ≥ 30);
* early season (team games ≤ 2, first 30 days);
* current-season evidence buckets 0 / (0,100] / (100,300] / 300+;
* transfers and cold starts;
* centers;
* Over/Under side balance;
* model vs no-vig market.

## Standing rules

* **Do not reopen historical research automatically because a live warning fires.** Prospective warnings
  (e.g. `PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING`) are diagnostic labels for architect review. They never trigger a refit,
  recalibration, starter multiplier, role correction or parameter search.
* Do not rerun R1, R2, constrained selection or the calibration audit during routine shadow operation.
* A future role-aware rate architecture would be a **NEW MODEL VERSION**. It would be a separate research project and
  would need separate architect approval. It is not a continuation of this campaign.
