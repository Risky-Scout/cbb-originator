# CBB Originator V2 — Phase 1: verified data, annual refit, recency weighting

Branch `research/cbb-v2-interconnected` (from `main` eed6452, tree identical to the production baseline cc56eea).
The v1 champion (`modeling/player_props_v1/final_prelockbox_2024_25_artifact.json`, sha256 `96464d00…cb34f`) and all v1
tags are unchanged. No dependence layer, no deployment work, no 2026-27 data.

Machine-readable records (all in `modeling/player_props_v2/`): `data_provenance.json`, `phase1_protocol.json` (frozen
before any fold), `dev_folds.json`, `weighting_selection.json` (committed before 2025-26 was read),
`retrospective_2025_26.json`, `v2_marginal_challenger_through_2025_26.json`, `final_refit_record.json`,
`oos_forecast_store_manifest.json`, `states/` (every fitted candidate state). Code: `cbb_platform/v2/`. Tests:
`tests/test_v2_phase1.py`.

Full suite: 586 passed, 0 failed, 3 skipped (28 min; log data/qa/large/v2_phase1/full_suite.log). Commit order (the evidence of sequencing): 591c20e provenance + protocol → dce67c6 dev folds + selection →
20d655e 2025-26 confirmation → 76e0c90 final challenger + OOS store.

## 1. Data provenance — VERIFIED_INDEPENDENTLY_REMEDIATED (0 historical API calls)

The Starter Pack export carries no edition string. It is identified by its registered content digest `cc6ced32…`, and
the current sibling export matches that digest (111 data files, the full inventory identical). This export **does** have all
three known defects. Each one is remediated in canonical, and each fix was measured on the stores themselves:

| known defect | in this export | remediation (measured) |
|---|---|---|
| historical `athleteId` semantics | yes: `player_game_stats.athleteId` is a per-record id | resolved to persistent season-file ids by exact (season, team, name); 98.4 % of resolved ids recur across games (2014-25), 99.3 % for 2025-26; record ids never used |
| duplicate statistical rows | yes: conference fan-out | 0 duplicate player-game keys, 0 duplicate (game, player), (game, team), game ids — dev 2014-25, lockbox 2014-25 and 2025-26 |
| shifted timestamps | yes: box-file `+00:00` stamps 4-5 h late | stamps unused; player-row event time = games.csv tip read as UTC for 100 % of rows; BDL external tips equal 804/804, 0 shifted; modal tip 19:00 ET |

Datasets: dev canonical `cbb_data_v2.4+5f19d31e524a` (v1 training input); lockbox canonical `cbb_data_v3.0+86a63cb6d902`
(adds 2025-26 via CBBD route 1.2.0); Stage 3 feature code `stage3-features-1.0.0`; cutoff = tip − 1 h, source usable only
when knowable (tip + 4 h) ≤ cutoff; v1 training max cutoff 2025-04-07 19:50 ET (the 2024-25 final). **Common store:** the lockbox Stage 3 store, whose
model-read relations equal the dev store for ≤ 2024-25 in every row and column except float summation noise (max relative
difference 9.4e-16 in EWM / usage columns). One tip-rule threshold was revised on observation (the 23:00–04:00 ET share went from
< 2 % to modal hour 18–20 and < 10 %, because legitimate 8 pm PT tips gave 2.04 %). This is recorded in the report; the BDL equality test is unchanged.

## 2. Design (frozen in `phase1_protocol.json`)

Only the training cutoff and the training-row season weight change. W0 is uniform; W4 and W2 use w(s) = 2^(−(S−s)/h), normalized to mean 1 per fit.
The penalty therefore stays at alpha·n for every policy. Everything else is v1, reproduced exactly: the v2 loader returns frames identical to
`props_v0.load_frames`, V1REF state hashes equal the recorded v1 fold and artifact hashes, and W0 participation equals v1.

**Important context:** v1 *already* weights training rows by season (half-life 1.0, shared in config with the player-rate
history feature). W0/W4/W2 therefore replace v1's weighting. The history feature keeps its h = 1 (a feature definition). V1REF
(v1's h = 1) is carried as a reference that cannot be selected.

Participation labels (DNP_CONFIRMED) exist only from 2023-24. For F2024 (both arms) and the F2025 stale arm, participation is
**not estimable**. In F2025 annual all policies coincide (a single training season).

## 3. Development folds (2025-26 not loaded)

Aggregate = n-weighted mean over the 7 count targets (lockbox G1 definition). Δ = game-clustered CR1 mean [95 % CI].

| forecast | candidate | train ≤ | n_eff | agg log | agg CRPS | thr Brier | min MAE | min RMSE |
|---|---|---|---|---|---|---|---|---|
| 2023-24 | W0 annual | 2022-23 | 1,047,434 | 1.447638 | 0.865073 | 0.127150 | 4.8440 | 6.2283 |
| | W4 annual | | 840,750 | 1.447412 | 0.865129 | 0.127154 | 4.8476 | 6.2282 |
| | **W2 annual** | | 558,057 | 1.447239 | 0.865115 | 0.127149 | 4.8495 | 6.2281 |
| | V1REF annual (ref) | | 303,957 | 1.447033 | 0.865004 | 0.127133 | 4.8495 | 6.2276 |
| 2024-25 | W0 annual | 2023-24 | 1,159,382 | 1.455431 | 0.870114 | 0.128399 | 4.8640 | 6.2548 |
| | W4 annual | | 895,503 | 1.455111 | 0.870062 | 0.128388 | 4.8648 | 6.2537 |
| | **W2 annual** | | 577,464 | 1.454868 | 0.869935 | 0.128369 | 4.8633 | 6.2523 |
| | V1REF annual (ref) | | 317,265 | 1.454609 | 0.869699 | 0.128337 | 4.8589 | 6.2504 |

**Annual refit minus one-season-stale** (negative = annual better):

| forecast | policy | Δ agg log | Δ agg CRPS | Δ min MAE |
|---|---|---|---|---|
| 2023-24 | W0 | −0.000127 [−0.000138, −0.000116] | −0.000056 [−0.000066, −0.000046] | −0.0002 |
| | W4 | −0.000181 [−0.000197, −0.000165] | −0.000114 [−0.000128, −0.000100] | −0.0010 |
| | **W2** | −0.000218 [−0.000241, −0.000196] | −0.000186 [−0.000205, −0.000166] | −0.0025 |
| 2024-25 | W0 | −0.000219 [−0.000235, −0.000202] | −0.000188 [−0.000204, −0.000173] | −0.0019 |
| | W4 | −0.000318 [−0.000345, −0.000292] | −0.000304 [−0.000327, −0.000281] | −0.0046 |
| | **W2** | −0.000385 [−0.000422, −0.000347] | −0.000411 [−0.000442, −0.000379] | −0.0076 |

**Selection: W2 (half-life 2), decided at step 1.** Pooled Δ agg log vs W0: W4 −0.000273, W2 −0.000481 [−0.000509, −0.000453].
W0 and W4 are clearly worse than W2 (interval above 0 and positive in each fold). Per target the result is stable across
folds but split. W2 is best for PTS, AST, BLK and TOV; W0 is best for REB, FG3M and STL. Minutes MAE goes the other way: W0
is better in 2023-24 and W2 in 2024-25.

**Flag recorded before 2025-26:** W2 is the grid boundary and log score improves monotonically with a shorter half-life. **V1REF
(v1's h = 1) beats W2 in both folds:** pooled W2 − V1REF = +0.000233 log [+0.000216, +0.000249] and +0.000174 CRPS. The directive
bars testing other half-lives without architect approval, so none was tested.

## 4. 2025-26 retrospective confirmation (run once; the policy was not changed)

| candidate | train ≤ | agg log | agg CRPS | min MAE | P(play) Brier / log loss / slope / CITL / AUC |
|---|---|---|---|---|---|
| A: W2 stale | 2023-24 | 1.463844 | 0.882779 | 4.9062 | 0.05412 / 0.18233 / 1.040 / −0.050 / 0.9792 |
| **B: W2 annual** | 2024-25 | 1.463723 | 0.882586 | 4.9007 | 0.05415 / 0.18244 / 1.041 / −0.017 / 0.9792 |
| V1REF stale (ref) | 2023-24 | 1.463540 | 0.882505 | 4.9014 | 0.05412 / 0.18233 |
| V1REF annual (ref; = v1 champion) | 2024-25 | 1.463571 | 0.882388 | 4.8957 | 0.05415 / 0.18244 |

* W2 annual − stale: log −0.000121 [−0.000147, −0.000094], CRPS −0.000192 [−0.000218, −0.000166], minutes MAE −0.0055.
  **The annual-refit effect generalizes.** By target the log-score gain is in PTS, REB, AST and TOV; FG3M and STL are slightly worse.
* Participation, annual (2023-24 + 2024-25) − stale (2023-24): Brier +0.000031 [−0.000013, +0.000074], log loss +0.000118
  [+0.000006, +0.000231]. **No gain**, and slightly worse on log loss. This is the only annual-vs-stale participation comparison the labels allow.
* W2 annual − V1REF annual: log +0.000153 [+0.000125, +0.000180], CRPS +0.000199. **W2 is again worse than v1's own weighting**,
  and V1REF stale beats W2 annual on log score. In this data the half-life matters more than refit cadence.
* V1REF annual reproduces the lockbox champion result (docs/LOCKBOX_RESULT_2025_26.json) to 9e-16.

**ANNUAL_REFIT_POLICY_SUPPORTED = TRUE** under the frozen rule: annual is better on aggregate log score and CRPS in 2023-24,
2024-25 and 2025-26 for the selected policy. Caveats: the effect is small (1–4 × 10⁻⁴ nats per cell). The participation model does
not show it. Under V1REF, 2025-26 is mixed (log +0.000031 with the interval including 0; CRPS −0.000117).

## 5. Diagnostics (not gates)

* Role (pregame `role_l5_minutes_rank`): the annual and W2 gains concentrate in bench (9+) and rotation (6–8) players. In 2024-25,
  W2 annual − stale was −0.00071 (bench), −0.00046 (rotation) and −0.00011 (top 5). Top-5-minutes players barely move.
* Position: guards gain the most. The small unknown-position group (U, ~2 %) is the one group where annual refit is worse
  (+0.0005 to +0.0014).
* Calibration of W2 annual is stable across seasons. 95 % coverage: PTS 0.93, other counts ≥ 0.975, minutes 0.96. The largest
  threshold gap is PTS (about 0.04–0.045); REB mean bias is +0.08 to +0.12.

Full per-target, per-threshold, bucket and role × stat / position × stat tables are in `dev_folds.json` and
`retrospective_2025_26.json`.

## 6. Final refit and forecast store

* **V2 challenger:** `modeling/player_props_v2/v2_marginal_challenger_through_2025_26.json`, sha256
  `8cb25370be093bcd518f5a0bcbaff902fb0e3340e1ef355dd532d5d291d381f2`. W2; stat rows 2013-14..2025-26 (n 1,272,278, n_eff 595,713);
  participation 2023-24..2025-26. SUPERVISED_TRAINING_THROUGH = HISTORICAL_STATE_THROUGH = 2026, PRODUCTION_SEASON = 2027.
  It is a challenger, not promoted.
* **OOS store:** `modeling/v2_oos/oos_forecasts_phase1.parquet` (80 MB, gitignored; sha256 `d7c64f67…ffb44b` in the manifest).
  It holds 774,511 pregame candidates in 17,242 games: F2024 253,767 rows, F2025 261,128, F2026 259,616; 337,376 PLAYED. Forecasts
  come from the saved W2 annual states, each trained only on earlier seasons, and the store build fits nothing. It holds P(play)
  (NaN for F2024: not estimable), minutes (mu, sigma, discretized normal), and the 7 count marginals (family, mu, phi; PMF via
  `props_v0.pmf_matrix`). It also holds role/position fields, outcomes, versions, training-through and the weighting policy. It
  reproduces the evaluated fold log scores exactly.

## 7. Fit calls

| stage | stat (v0) fits | participation fits |
|---|---|---|
| F2024 (dev) | 8 | 0 (not estimable) |
| F2025 (dev) | 8 | 4 |
| F2026 (confirmation) | 4 | 4 |
| FINAL | 1 | 1 |
| OOS store | 0 | 0 |

Two more categories, outside the counts above. One aborted dev attempt crashed after 1 stat fit (a duplicate-column merge bug)
and wrote no output; it was fixed and the whole stage was rerun. Reproduction checks and tests refit v1 states on the dev store
(≤ 2024-25 only).

## 8. For the architect

1. The directive grid ends at its best value. v1's existing h = 1 training weight beats the selected W2 on development data,
   recorded before 2025-26, and again on 2025-26. A decision is needed on whether to approve h ≤ 2 (for example h = 1 or 1.5)
   or to adopt V1REF + annual refit as the V2 marginal base. The W2 challenger is, on this evidence, inferior to an annual
   refit of the v1 specification.
2. Annual refit helps the count/minutes model (small, consistent). It did not help participation in the one testable season.
3. The OOS store is ready for Phase 2. If Phase 2 needs more seasons of played-row history, annual states for earlier
   forecast seasons are cheap to add, but P(play) cannot exist before 2024-25.
