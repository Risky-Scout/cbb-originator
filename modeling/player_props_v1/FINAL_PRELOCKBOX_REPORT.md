# CBB v1 final pre-lockbox candidate — FREEZE: PASS

**Status:** the candidate is built and validated, and the authoritative tracked official suite is green (462 passed,
0 failed). It is frozen locally as tag `cbb-props-v1-prelockbox-2024-25`. Nothing was pushed or deployed, and 2025-26
remains closed.

**Earlier attempt:** the first freeze attempt stopped uncommitted because `tests/test_stage2.py::test_no_unrelated_project_was_modified`
found `CBB_Model_Source_Inspection.zip` in the parent workspace directory. That file was moved, not deleted, outside
Claude Code. The test then passed, and the freeze resumed with no change to the candidate.

## Final candidate

**FINAL CANDIDATE:** the existing frozen CBB v1 model + STL Negative Binomial + φ_STL = 0.16.

**ACCEPTED CHANGE:** STL distribution only.

**2025-26:** UNTOUCHED.

* **Branch:** `release/cbb-v1-prelockbox`, created from `c123a3b`.
* **Single source of truth for STL φ at runtime:** the artifact's `v0_state.dispersion.stl`. Pricing reads it through
  `Q.pregame_outputs` and `props_v0.pmf_matrix`, the existing NB implementation.
* **Build-time source:** `final_prelockbox_spec.json`. φ is not hard-coded in any runtime module.

## Accepted changes

* STL distribution only: Poisson → NB with φ = 0.16. The mean is unchanged.

## NOT ACTIVATED

* FG3M dispersion challenger
* PTS mean calibration challenger
* participation calibration challenger
* TOV mean challenger
* minutes role interaction challenger

## Development evidence

* **Non-STL targets:** for every non-STL target (MIN, PTS, REB, AST, FG3M, BLK, TOV), the final candidate equals the
  baseline exactly, PMFs are byte-identical, and the baseline reproduces `metrics.json` to within 1e-12.

| target | 2023-24 MAE | log | CRPS | 2024-25 MAE | log | CRPS |
|---|---:|---:|---:|---:|---:|---:|
| PTS | 3.848902 | 2.8390378 | 2.7025470 | 3.875251 | 2.8521504 | 2.7157599 |
| REB | 1.678609 | 2.0075611 | 1.1523132 | 1.679830 | 2.0067523 | 1.1504323 |
| AST | 0.948013 | 1.3430256 | 0.6158664 | 0.958931 | 1.3591565 | 0.6254052 |
| FG3M | 0.650456 | 0.9737922 | 0.4066373 | 0.670075 | 1.0006860 | 0.4214014 |
| STL | 0.648363 | 1.0097801 (was 1.0127575) | 0.3937593 (was 0.3943788) | 0.652906 | 1.0222833 (was 1.0249289) | 0.3993175 (was 0.3997513) |
| BLK | 0.411456 | 0.6539495 | 0.2352688 | 0.406124 | 0.6443659 | 0.2300076 |
| TOV | 0.851927 | 1.3020873 | 0.5486364 | 0.844369 | 1.2968678 | 0.5455696 |

* **STL evidence reproduced:** every metric matches the accepted challenger JSON at full precision, with a maximum
  absolute difference of **0.0**. That covers the mean, MAE, log score, CRPS, coverage, thresholds, Brier, log loss
  and diagnostics, for both seasons.
* **STL acceptance rerun:** criteria A to J all PASS.
* **2024-25 STL thresholds:**

  | line | P(over) | observed | gap | Brier | log loss |
  |---|---:|---:|---:|---:|---:|
  | 0.5 | 0.439672 | 0.433113 | +0.006559 | 0.208147 | 0.602287 |
  | 1.5 | 0.162117 | 0.162837 | −0.000720 | 0.120267 | 0.385066 |
  | 2.5 | 0.055501 | 0.055221 | +0.000281 | 0.048539 | 0.184047 |

* **Pregame identity:** on all 261,128 pregame candidates for 2024-25, every output except the STL PMF is identical.
  This holds for evaluation-B states and for base vs final artifact. The STL PMF equals `pmf_matrix(mean, 0.16)`.
* **Pricing and PMF integrity:** PASS for all 7 targets. Row sums are within 7e-15 of 1, there are no negative entries,
  and P(over) is monotone. Push semantics, half-line push = 0, the non-push probabilities, fair odds and tail support
  all behave as before.

## Integrity

* 2025-26 was untouched. A spy on every SQL query and every `check_season` call found nothing after 2024-25.
* `props_v0*.py`, the v0 config and the v0 metrics are byte-unchanged.
* The frozen refit byte-reproduces the base artifact `3d343eb9…`.

## Release-gate test suite (`OMP/OPENBLAS/MKL_NUM_THREADS=2`)

**Authoritative tracked run:**

```
.venv/bin/python -m pytest -q $(git ls-files 'tests/test_*.py') tests/test_props_v1_final_prelockbox.py
```

* **Result:** **462 passed, 0 failed**, 0 errors, 0 skipped, 0 warnings, 1468 s.
* **Stage-2 workspace test** (`tests/test_stage2.py::test_no_unrelated_project_was_modified`): PASS.
* **Untracked tests excluded on purpose:** the untracked experiment and readiness tests are not part of the release
  gate. Several of them assert that no tracked file differs from `c123a3b`, which a candidate commit necessarily
  breaks.

## Artifact

* **Path:** `modeling/player_props_v1/final_prelockbox_2024_25_artifact.json`
* **SHA-256:** `96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f`. Two independent fresh-process builds
  were byte-identical.
* **Specification fingerprint:** `3e343c1ad1e8668dc1fadade4c85a92c4bc8cbb651ff49284ac4a8a7106b8a9f`
* **Artifact fingerprint:** `191175928969a38edd431cd8410a9fd8050cf68f23a9fb1836814bb771839997`
* **Fresh-process load:** loading and scoring in a fresh process made 0 fit calls. Two runs produced a byte-identical
  scoring hash, `17d53f25…`.

## Rollback baseline

* **Commit:** `c123a3bf0ac615c6d7f864768f5e77dc18405a8d`
* **Tag:** `cbb-props-v1-refit-2024-25`

## Next step

The next step is a separate lockbox-readiness closure task. This freeze does not open 2025-26, deploy, or publish. This report makes no claim about
2025-26 performance or performance against the market.
