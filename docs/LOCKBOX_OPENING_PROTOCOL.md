# Lockbox opening protocol (2025-26)

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN**. Complements `LOCKBOX_POLICY.md` (unchanged,
still governs the closed state and its enforcement). **The lockbox is closed and untouched.** This document only defines
how it may be opened, once.

## 1. Preconditions (all required, each with recorded evidence)

1. Phase D has frozen a **single production candidate bundle**: model versions, calibration version, probability method,
   eligibility manifests, dataset version (MODEL_READY) and feature version, all hashed in the ledger
   (`ledger.model_artifact`, `ledger.calibration_artifact`).
2. The **evaluation scorecard is frozen and committed before opening**: metrics (log loss, Brier, CRPS, ECE / calibration
   slope, interval coverage, energy / variogram scores), market families, checkpoints, comparison baselines (the simple
   challengers and the market benchmark), and pass / fail thresholds. Its file sha256 is recorded.
3. The **lockbox data plan** is frozen: which provider endpoints, the season range 2025-08-01..2026-07-31, the raw
   storage path, the canonical build version. The guard change that allows exactly this plan is reviewed as a diff.
4. Two human approvals recorded as a `promotion_decision` of type `LOCKBOX_OPEN` (or an architect ruling when only one
   person is available, stated as such).
5. A clean, committed working tree; the environment lock (`docs/REPRODUCIBLE_ENVIRONMENT_POLICY.md`) recorded.

## 2. Procedure (one session, logged)

1. Create branch `lockbox/open-2025-26` from the frozen candidate commit. Tag `pre-lockbox-<date>`.
2. Apply the reviewed guard change in `cbb_platform/lockbox.py`. It allows the planned season / endpoints only, under a
   flag `LOCKBOX_OPEN_2025_26=1`, and is recorded in `LOCKBOX_POLICY.md` by a **new version** of that file. The old file is
   kept.
3. Fetch the raw data (append-only raw store), build a canonical successor dataset (e.g. `cbb_data_v3.0+…`) whose
   manifest records `lockbox_touched = true` and the opening decision id.
4. Run the frozen candidate on the frozen scorecard. **Nothing is refit, recalibrated or re-selected.**
5. Write `docs/LOCKBOX_RESULT_2025_26.md` (+ JSON) with the metrics, pass / fail per threshold, and the commit SHA.

## 3. After opening

* The lockbox result may decide **go / no-go** for the frozen candidate only. It may not be used to tune, re-select,
  recalibrate or change architecture. Any change after opening requires the 2026-27 prospective season (Phase E) for
  validation. 2025-26 thereafter is ordinary training history for **later** model versions, flagged in their manifests.
* If the procedure is interrupted, the partial data stays in the raw store, flagged. A second opening is not possible.
  The incident is recorded and the architect rules.

## 4. Never

Peeking at 2025-26 outcomes or summaries before §1 is complete; "sanity checks" on lockbox outcomes; a partial opening
for one subsystem; running more than one candidate against the lockbox.
