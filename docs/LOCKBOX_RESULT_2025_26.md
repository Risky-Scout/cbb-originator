# Lockbox result: CBB props v1, 2025-26 (one-time frozen evaluation)

**LOCKBOX_DECISION = GO** · G1-G6 all PASS · FIT_CALLS = 0 · no refit, recalibration or selection.

Generated from `docs/LOCKBOX_RESULT_2025_26.json` (file sha256 `e243aa84ad312a1b8578307148ac895d94a9d17c6be6070781d2a160f87d8aef`; result_sha256 `c72f6a98c44f456234f0aca6807f63dc41db677c695bee39d6165385d569dda6`), the
single output of `cbb_platform/lockbox_runner.py` run once in a fresh process with every fit entry point disabled
(`lockbox_runner_validation.fresh_run`; `data/lockbox_2025_26/scoring_run.json`). Protocol `docs/LOCKBOX_OPENING_PROTOCOL.md` §2.5.

## Identities

| item | value |
|---|---|
| candidate | tag `cbb-props-v1-prelockbox-2024-25-r2` → `425211071413735be8e1497105ae51830c971c12` |
| artifact | `final_prelockbox_2024_25_artifact.json`, sha256 `96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f`, fingerprint `191175928969a38edd431cd8410a9fd8050cf68f23a9fb1836814bb771839997` |
| scorecard (frozen before opening) | `docs/lockbox/lockbox_scorecard_v2.json`, sha256 `27bdfbdd35b182b6eb88710d34310e9f5f71a262b850a88071b8ac085ce26ecd` |
| decision | `data/manifests/LOCKBOX_OPEN_DECISION_2025_26.json` (sha256 `819eae1fb6086514101edcca77be0027d7a56d3e4d8d7741d8068f34758e7013`), opening commit `7dbc0b569271a42c8079946e3e27e76be86703c2` |
| lockbox dataset | `cbb_data_v3.0+86a63cb6d902` (content digest `c1f8f1d6a84c3129e2de5b2a56cafa7a1e03919050a2cb77a1cc8dd1dd8624f1`; payload set `4aedc8c150d47e5afdbb7d2e3354b293031e3b0a6f391ba4a4b866cd06aa057b`), two identical builds |
| incident | `docs/lockbox/LOCKBOX_INCIDENT_2025_26_TRUSTED_WINDOW.md` (discovered before scoring; lockbox-scoped remediation) |
| PIT audit | `data/qa/lockbox_2025_26_pit_audit.json`: GENUINE_PIT_VIOLATIONS 0; UNAUTHORIZED_FUTURE_SEASON_ROWS 0 |

## Populations (2025-26)

* Counts / minutes: **112,357 scored player-games**. Exclusions (counted, never imputed): EXCLUDED_BOX_ROW_DNP_CONFIRMED_NOT_AN_OBSERVED_APPEARANCE 3, EXCLUDED_BOX_ROW_IDENTITY_UNRESOLVED 134, EXCLUDED_BOX_ROW_PLAYER_OUTSIDE_PREGAME_UNIVERSE 1,369, EXCLUDED_BOX_ROW_UNKNOWN_PARTICIPATION_NOT_AN_OBSERVED_APPEARANCE 4, EXCLUDED_PLAYED_NO_BOX_ROW_NO_OUTCOME 168, NOT_SCORED_NO_BOX_ROW_DNP_CONFIRMED 137,738, NOT_SCORED_NO_BOX_ROW_NOT_ON_ROSTER 1,164, NOT_SCORED_NO_BOX_ROW_UNKNOWN_PARTICIPATION 8,182.
* Participation: **250,266 scored candidates** (112,525 PLAYED, 137,741 DNP_CONFIRMED); 9,350 UNKNOWN / NOT_ON_ROSTER excluded.

## Frozen gates (scorecard 2.1.0, G1-G6 verbatim)

| gate | rule | candidate | baseline / heuristic | result |
|---|---|---|---|---|
| G1 aggregate | log score and CRPS < baseline | log 1.463571, CRPS 0.882388 | log 1.514262, CRPS 0.937822 | PASS |
| G2 per target | both, for >= 6 of 7 | 7 of 7 | | PASS |
| G3 minutes | MAE and RMSE < baseline | MAE 4.895715, RMSE 6.277190 | MAE 6.288510, RMSE 7.609720 | PASS |
| G4 participation | Brier and log loss < heuristic | Brier 0.054147, log loss 0.182436 | Brier 0.071541, log loss 0.257784 | PASS |
| G5 calibration | slope in [0.80, 1.25], abs(CITL) <= 0.25 | slope 1.040786, CITL -0.023117 | | PASS |
| G6 pricing integrity | row sums 1e-9, no negatives, monotone, exact push, finite prices | fair_prices_finite, monotonicity_violations_zero, no_negative_entry, push_semantics_exact, row_sums_within_1e-9 | | PASS |

### G2 detail

| target | log score (cand.) | log score (base) | CRPS (cand.) | CRPS (base) | |
|---|---|---|---|---|---|
| PTS | 2.879691 | 2.975680 | 2.778860 | 2.985716 | win |
| REB | 2.014275 | 2.088889 | 1.156564 | 1.237274 | win |
| AST | 1.379809 | 1.434424 | 0.640812 | 0.678533 | win |
| FG3M | 1.016090 | 1.061877 | 0.430172 | 0.450427 | win |
| STL | 1.021351 | 1.047428 | 0.399326 | 0.411913 | win |
| BLK | 0.645110 | 0.664482 | 0.230895 | 0.237532 | win |
| TOV | 1.288670 | 1.327056 | 0.540084 | 0.563361 | win |

## After this result

Per protocol §3 the result decides go / no-go for this frozen candidate only and may not be used to tune, re-select,
recalibrate or change the architecture. 2025-26 becomes ordinary history only for later model versions, under a separate
directive.
