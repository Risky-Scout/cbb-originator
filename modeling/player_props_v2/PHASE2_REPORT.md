# CBB Originator V2 — Phase 2: marginal-preserving joint player/stat dependence

Branch `research/cbb-v2-interconnected`. This is research only:
- v1 is untouched, and so is the Phase 1B marginal.
- No production work was done.
- 2026-27 was never read.
- Historical API calls: 0.

## 1. Result

**SELECTED_DEPENDENCE_MODEL = J2.** J2 is a unit-diagonal latent Gaussian copula over the unchanged Phase 1B marginals. It has three parts:
- within-player cross-stat dependence;
- pooled same-team dependence;
- pooled shared-game/opponent dependence.

How it was chosen and checked:
- **Selection.** J2 was selected on 2024-25 under the frozen rule, with dependence fitted on 2023-24 only.
- **Confirmation.** The one-shot 2025-26 retrospective re-fitted on 2023-24 + 2024-25 and confirmed J2. Every decision check came out the same way as in the selection.

What the results show:
- **Where the gain comes from.** Nearly all of the gain is within-player (J1 vs J0).
- **Team/game structure (J2 vs J1).** The added gain is small but consistent:
  - ES, VS_BAL and EVENT_BRIER improve in both seasons.
  - The confidence intervals exclude zero by a wide margin, and the gains are 7–13× the Monte Carlo SE.
  - No combo market gets worse.
- **Marginals.** MARGINAL_PRESERVATION = PASS for every arm in both seasons.
- **Matrices.** INVALID_CORRELATION_MATRICES = 0 in every game of every arm.

The commit order is the evidence chain:

| commit | content |
|---|---|
| cff039a | All dependence code plus `phase2_protocol.json` (sha256 1865797d…), frozen before any candidate was scored |
| 35a3877 | `phase2_selection_fit_2023_24.json` + `phase2_dependence_selection.json` → J2. 2025-26 was not loaded. |
| e4fc4de | Fix: latent residuals now use each row's own forecast dispersion (§8) |
| 4c4ff78 | `phase2_confirmation_fit_through_2024_25.json` + `phase2_retrospective_2025_26.json`. Run once; J2 confirmed. |
| ffff70a | `dependence_J2.json` (fitted on all 2023-24..2025-26 OOS pairs) + `phase2_verification.json` |

## 2. What was reused from NBA Prop Quant, and what changed for CBB

Source, read-only: `~/actions-runner-nba/_work/nba-prop-quant/nba-prop-quant`. The files read were:
- `src/nba_prop_quant/copula.py`
- `src/nba_prop_quant/research/game_latent_state/{pit,covariance,factors,simulator,validation}.py`

Nothing is imported from that repo. The patterns below were re-implemented in `cbb_platform/v2/`.

| NBA Prop Quant | CBB V2 Phase 2 | Status |
|---|---|---|
| Incumbent mid-PIT `F(y-1) + p(y)/2` → `Phi^-1` (`copula.py`) | `latent.py` MID (the primary latent residual) | reused |
| Exact randomized PIT with a keyed BLAKE2b `v` per (game, player, stat), clip eps 1e-12 (`pit.py`) | `latent.py` RAND (the randomized-PIT sensitivity check) | reused |
| Raw correlation of the PIT normals (attenuated for discrete counts) | Moments divided by `sum kappa_i kappa_j`, with `kappa = E[z Z]` computed exactly from each row's PMF (first-order Hermite de-attenuation) | **changed** |
| Standardize residuals by training mean / SD per stat | Training mean offset and realized/model variance ratio per (role, stat) | adapted |
| Ledoit-Wolf global matrix + shrunk **per-named-player** within-player matrices | Pooled hierarchy: global → role → role × position, with soft-thresholded deviations. **No player-specific matrix** (roster turnover). | **changed** |
| O(n) team-sum pair moments, same-team S / cross-team X | Same estimator, applied to de-attenuated moments | reused |
| Soft threshold at 1.96 game-clustered bootstrap SEs | Same, and also applied to within-player group deviations | reused |
| Dominating additive Gram M; A = (M+X)/2, B = (M−X)/2, Q = M−S behind a pivotal-bootstrap competition gate | Same | reused |
| Game covariance with a pinned within-player block and a single shrink c_i per player | Same pinning, but a **two-level shrink**: c_ab for A+B first, then c_q for the zero-sum Q (reason below) | **changed** |
| Rank-2 game factor, rank-1 contrast | Full-rank PSD factorizations of the thresholded 7 × 7 A and B; the thresholding is the regularizer | changed |
| Role multipliers for cross-player blocks | Not used. Cross-player blocks are pooled; role-pair effects appear only as diagnostics. | not reused |
| Cholesky + jitter | Cholesky with jitter only for rounding (recorded, never needed: 0 games) and unit-row renormalization, so diag(LLᵀ) = 1 exactly | adapted |
| Tabulated inverse CDF + `searchsorted` | Same, in latent space: `X = #{k : Phi^-1(F(k)) < z}`. The locked environment has no scipy. This equals `F^-1(Phi(z))` (tested). | adapted |
| Marginal-preservation z probes, clustered bootstrap, randomly generated joint events | z probes + PMF cell + KS + single-stat score check; game bootstrap; a **fixed, predefined** event list built from pregame mean ranks | adapted |
| — (NBA has no energy or variogram scores) | Energy score and scaled variogram score (p = 0.5), by pair type | new |
| TOV absent (no NBA marginal) | TOV included (Phase 1B has a TOV marginal) | changed |

Why the two-level shrink: a team has only 10–11 players who play, so (n−1)Q can exceed a unit-variance block. A single shared c would also shrink the game/opponent structure.

## 3. Data, population and exclusions

**Source.** Only the true-OOS Phase 1B store is used: `modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet` (sha256 d0859e48…). Every row satisfies `training_through_season < forecast_season`. Dependence is never estimated from the final marginal's in-sample residuals.

**Population.** The fit is conditional on playing:
- A row is eligible if `participation_status == PLAYED` and all 7 count labels are present.
- DNP, not-on-roster and unknown-participation rows are excluded. They are never treated as zeros.
- P(play) is not used and nothing is imputed.
- A game needs eligible rows for both teams.
- PLAYED rows with 0 recorded minutes are kept.

| population | players | player-games | games |
|---|---|---|---|
| Selection fit (2023-24) | 4,964 | 111,938 | 5,725 |
| Selection evaluation (2024-25) | 5,027 | 112,871 | 5,756 |
| Confirmation fit (2023-24 + 2024-25) | — | 224,809 | 11,481 |
| Confirmation evaluation (2025-26) | 4,965 | 112,334 | 5,748 |
| **Final fit (DEPENDENCE_ELIGIBLE, 2023-24..2025-26)** | **8,708** | **337,143** | **17,229** |

Exclusions in the final fit population (774,511 candidate rows, 17,242 games):

| reason | rows |
|---|---|
| NOT_A_PLAYING_OBSERVATION: DNP_CONFIRMED | 406,041 |
| NOT_A_PLAYING_OBSERVATION: UNKNOWN_PARTICIPATION | 27,532 |
| NOT_A_PLAYING_OBSERVATION: NOT_ON_ROSTER | 3,562 |
| PLAYED_BUT_COUNT_LABEL_MISSING | 179 |
| GAME_WITHOUT_BOTH_TEAMS_ELIGIBLE (6 games) | 54 |

Per-season breakdowns are in each record's `population` / `evaluation_population`.

## 4. Method (frozen in `phase2_protocol.json`)

**Latent residuals.**
- Primary: z = Φ⁻¹(F(y−1) + p(y)/2) (MID), centered by its exact model expectation.
- Correlations are de-attenuated by Σκᵢκⱼ, with κ = E[z·Z] computed from each row's own PMF.
- Sensitivity: randomized PIT (RAND).

**Normalization.** Per (role, stat), using training quantities only: a mean offset and a realized/model scale ratio.

**Structure.**

| Part | What it is | Stat-pair parameters |
|---|---|---|
| (A) Within-player | Hierarchy: global → role (TOP5_MINUTES / ROTATION_6_8 / BENCH_9PLUS) → role × position (G/F/C) | 21 per matrix |
| (B) Same-team | Pooled 7 × 7 block | 28 |
| (C) Cross-team (shared game / opponent) | Pooled 7 × 7 block | 28 |

- All deviations are soft-thresholded at 1.96 game-clustered bootstrap SEs (200 draws).
- There are no named-player or named-pair parameters.
- A conditioning rule keeps each child matrix's minimum eigenvalue at ≥ 0.5 × its parent's. When this binds, the deviation is scaled back and the repair is recorded in `psd_repairs_at_fit`. In the final fit it bound twice: BENCH_9PLUS (scale 0.82) and BENCH_9PLUS|G (scale 0.83).

**Game matrix.** Within-player blocks are pinned. Cross-player blocks come from the dominating Gram decomposition (A, B, Q) behind a competition gate, which was active (pivotal bound −0.095 < 0). The two-level shrink then runs. Every matrix is checked for:
- minimum eigenvalue;
- diagonal error;
- Cholesky.

INVALID_CORRELATION_MATRICES counts failures. There is no silent clipping.

**Candidates.** Each was evaluated with MID and RAND variants:
- J0: independence.
- J1: within-player only.
- J2: J1 + same-team + cross-team.

**Simulation.**
- One standard-normal bank per game, seeded by BLAKE2b(20263 | game_id).
- 20,000 draws per game, with common random numbers across arms.
- Counts come from latent thresholds.
- PRA, PR, PA, RA and STOCKS are exact sums of the same draws.
- No simulation cube is stored.

**Scores.**
- Energy score (ES) on the full scaled game vector. The scales are training-only.
- Scaled variogram score (p = 0.5), split into within / same-team / cross-team. VS_BAL is the mean of the three per-pair means.
- Combo log score, CRPS, and Brier at the J0-convolution quantile lines q25/q50/q75.
- 19 predefined joint-threshold event families, including tails.
- Pair diagnostics and pooled correlation calibration.

All CIs are 95% game-cluster bootstrap intervals (2,000 draws). MC SEs come from split halves and two variogram replicates.

**Decision rule (frozen).** A candidate passes vs J0 when all of these hold:
- marginal preservation;
- valid matrices;
- ES or VS_BAL improves significantly;
- no major score (ES, VS_BAL, COMBO_LOG, EVENT_BRIER) is significantly worse;
- no combo is materially degraded (> 0.002 nats or > 0.5% CRPS);
- stable (structure correlation ≥ 0.7, sign agreement ≥ 0.9);
- MC adequate (CI bound ≥ 3 MC SEs from 0).

J2 is selected over J1 only if it adds significant value vs J1.

## 5. Marginal preservation: PASS

The probes are z-tests of the simulated vs analytic quantity per player-game: mean, variance, P(X > q25/q50/q75), the PMF cell at y, and the KS distance vs the DKW bound.

For J2 in both seasons and all 7 stats, the share of player-games with |z| > 3.29 is 0.0007–0.0013, against a nominal 0.001 and a gate of 0.005. The maximum |z| is ≤ 5.4, under the 6.5 gate.

Two more checks:
- **KS.** The DKW exceedance share is ≤ 0.00027.
- **Single-stat scores.** These are identical to J0 (difference 0, as the construction requires).

The same holds for J0, J1 and the RAND arms.

## 6. Results (J0 → J1 → J2; deltas negative = better)

**2024-25 selection (fit 2023-24):**

| score | J0 | J1 | J2 | J2−J0 [95% CI] | J2−J1 [95% CI] |
|---|---|---|---|---|---|
| ENERGY_SCORE | 6.76784 | 6.76621 | 6.76581 | −0.00203 [−0.00215, −0.00191] | −0.00040 [−0.00046, −0.00034] |
| VARIOGRAM (VS_BAL) | 0.24179 | 0.24037 | 0.24036 | −0.00144 [−0.00147, −0.00140] | −0.000017 [−0.000024, −0.000013] |
| VS within-player | 0.22744 | — | — | −0.00426 [−0.00435, −0.00416] | ≈0 |
| VS same-team | 0.24921 | — | — | −0.000013 | −0.000013 (sig.) |
| VS cross-team | 0.24873 | — | — | −0.000040 | −0.000040 (sig.) |
| COMBO_LOG (all 5) | 2.60703 | 2.54353 | 2.54351 | −0.0635 [−0.0647, −0.0624] | −0.00002 [−0.00015, +0.00010] |
| EVENT_BRIER | 0.13310 | 0.13108 | 0.13098 | −0.00213 [−0.00228, −0.00198] | −0.00011 [−0.00012, −0.00009] |
| EVENT_LOG | 0.42430 | 0.41539 | 0.41486 | −0.00944 | −0.00053 |

**2025-26 retrospective (fit 2023-24 + 2024-25, run once):**

| score | J0 | J1 | J2 | J2−J0 [95% CI] | J2−J1 [95% CI] |
|---|---|---|---|---|---|
| ENERGY_SCORE | 6.81932 | 6.81777 | 6.81731 | −0.00201 [−0.00214, −0.00189] | −0.00047 [−0.00053, −0.00041] |
| VARIOGRAM (VS_BAL) | 0.24417 | 0.24275 | 0.24272 | −0.00144 [−0.00148, −0.00141] | −0.00002 [−0.00003, −0.00002] |
| COMBO_LOG | 2.62766 | 2.56272 | 2.56269 | −0.0650 [−0.0662, −0.0638] | −0.00003 [−0.00015, +0.00008] |
| EVENT_BRIER | 0.13194 | 0.13001 | 0.12991 | −0.00202 [−0.00219, −0.00186] | −0.00009 [−0.00011, −0.00008] |
| EVENT_LOG | 0.42103 | 0.41260 | 0.41211 | −0.00891 | −0.00049 |

**MC adequacy.** MC_ADEQUATE = TRUE in both seasons at 20,000 draws, so no 50k rerun was needed. The smallest ratio of CI bound to MC SE is 7.3, for J2−J1 VS_BAL.

**RAND sensitivity.** J2_RAND − J2 is ≤ 2e-5 on ES and VS. RAND and MID structures correlate at 0.993–0.9996, with 100% sign agreement.

**Combination markets.** J2 − J0 deltas; selection season first, then confirmation:

| combo | log score | CRPS | Brier q50 | reliability ECE J0 → J2 |
|---|---|---|---|---|
| PRA | −0.1445 / −0.1485 | −0.0531 / −0.0549 | −0.00043 / −0.00044 | 0.0123 → 0.0068 / 0.0143 → 0.0082 |
| PR | −0.1057 / −0.1079 | −0.0365 / −0.0376 | −0.00030 / −0.00033 | 0.0122 → 0.0072 / 0.0131 → 0.0081 |
| PA | −0.0434 / −0.0434 | −0.0127 / −0.0132 | −0.00023 / −0.00021 | 0.0094 → 0.0119 / 0.0109 → 0.0130 |
| RA | −0.0222 / −0.0229 | −0.0068 / −0.0070 | −0.00026 / −0.00026 | 0.0238 → 0.0217 / 0.0246 → 0.0224 |
| STOCKS | −0.0017 / −0.0022 | −0.00035 / −0.00049 | −0.00018 / −0.00019 | 0.0156 → 0.0134 / 0.0224 → 0.0201 |

All five combos improve on log score, CRPS and every Brier line in both seasons, and none degrades vs J1.

The one blemish is PA reliability ECE, which rises slightly (0.009 → 0.012) while its log score, CRPS and Brier all improve. The cause is a thin top bin: n = 640 at a predicted probability of 0.50.

**Joint threshold events.** Predefined, by pregame mean rank. Event rates are shown as selection / confirmation; ΔBrier is J2−J0 (selection CI).

| family | observed | J0 p | J2 p | ΔBrier J2−J0 |
|---|---|---|---|---|
| Same player PTS & FG3M over | .310 / .305 | .169 | .273 | −0.0194 [−0.0213, −0.0177] |
| Same player PTS & REB | .292 / .285 | .205 | .258 | −0.0065 |
| Same player PTS & AST | .244 / .252 | .182 | .205 | −0.0024 |
| Same player AST & TOV | .195 / .193 | .153 | .162 | −0.0008 |
| Same player REB & BLK | .166 / .164 | .143 | .166 | −0.0006 |
| TAIL: PTS & FG3M both high | .077 / .074 | .025 | .077 | −0.0029 |
| TAIL: PTS high & FG3M low | .028 / .027 | .075 | .019 | −0.0022 |
| Teammates: creator AST & shooter FG3M | .210 / .211 | .158 | .167 | −0.0008 [−0.0009, −0.0007] |
| Teammates: creator & scorer | .266 / .264 | .195 | .200 | −0.0007 |
| Teammates: 3 scorers + creator | .147 / .149 | .092 | .099 | −0.0008 |
| Opponents: STL vs TOV | .170 / .158 | .134 | .146 | −0.0007 [−0.0009, −0.0006] |
| TAIL: opponent STL & TOV high | .028 / .027 | .018 | .022 | −0.00007 |
| Opponents: both top scorers over | .309 / .316 | .225 | .226 | −0.0001 |
| **Teammates: both top scorers over** | .288 / .294 | .223 | .218 | **+0.0007 [+0.0006, +0.0008]** |
| TAIL: teammate scorer high, other low | .039 / .037 | .043 | .046 | +0.00003 |
| REB-REB (team/opp), TAIL scorers high | — | — | — | not significant |

JOINT_THRESHOLD_RESULTS: 13 of 19 families improve significantly, 4 are neutral and 2 are worse.

The worse family is teammate scorer-scorer. J2 correctly encodes a negative teammate PTS–PTS latent correlation (−0.034; shared possessions). But the observed joint over-rate (0.29) exceeds even J0 (0.22). The gap comes from the marginals: J0 already under-predicts these joint overs, which points to over-lines and game environment, not dependence. Phase 2 is not allowed to fix that (§9).

**Pairwise correlation calibration (diagnostic; observed values centered by evaluation-season means).**

| pair type | RMSE J0 | RMSE J2 | slope J2 (sel / conf) |
|---|---|---|---|
| within-player (21 pairs) | 0.199 | 0.016 / 0.014 | 1.05 / 1.04 |
| teammates (28 pairs) | 0.013 | 0.0029 / 0.0029 | 1.05 / 1.05 |
| opponents (28 pairs) | 0.014 | 0.0023 / 0.0027 | 1.03 / 1.04 |

J1 does not move the teammate or opponent RMSE (by construction). The slopes near 1 show that the pooled cross-player structure is neither over- nor under-stated on average.

The anchor-pair diagnostics in `pair_diagnostics` are a different measure: uncentered products standardized by model SD. They carry marginal mean and variance error, so their levels are not comparable to copula correlations.

## 7. Strongest stable dependencies (final artifact; latent correlation units)

**Within-player (global).**

| pair | correlation |
|---|---|
| PTS–FG3M | 0.74 |
| PTS–REB | 0.38 |
| REB–AST | 0.23 |
| PTS–STL | 0.23 |
| PTS–AST | 0.22 |
| REB–BLK | 0.21 |
| REB–FG3M | 0.17 |
| AST–STL | 0.17 |

Role and role × position deviations add to these.

**Same team.**

| pair | correlation | note |
|---|---|---|
| AST–FG3M | +0.063 | assisted threes |
| AST–PTS | +0.036 | |
| PTS–PTS | −0.034 | shared scoring opportunity |
| PTS–FG3M | −0.028 | |
| REB–REB | −0.015 | |
| TOV–TOV | +0.019 | |

**Opponents.**

| pair | correlation | note |
|---|---|---|
| STL–TOV | +0.094 | the largest cross-player effect |
| REB–AST | −0.017 | |
| REB–PTS | −0.014 | |
| BLK–AST | −0.012 | |
| BLK–REB | +0.011 | |

**Stability.** Structures are stable across fits:
- 2023-24 vs 2024-25-only fits: correlation ≥ 0.995, 100% sign agreement.
- Selection fit vs confirmation fit: correlation ≥ 0.9989.

**Construction.** In 2025-26:
- c_ab = 1 in all games;
- c_q has a mean of 0.94 and a minimum of 0.51;
- the minimum game eigenvalue is 0.044;
- the maximum diagonal error is 2.2e-16;
- jitter was used in 0 games.

## 8. Code change after the protocol freeze (disclosed)

The confirmation fit was the first to span two seasons. It stopped while fitting, before any 2025-26 game was simulated or scored. The cause: the OOS store carries one dispersion per forecast season (for example, BLK phi is 0.211 / 0.232 / 0.213), and `residuals()` refused a population with more than one.

Commit e4fc4de makes each row use its own forecast's dispersion:
- The result is bit-identical on single-season populations (`test_multi_season_residuals_use_each_rows_own_dispersion`), so the committed 2023-24 selection fit is unaffected.
- The procedure is unchanged.
- `code.changed_since_protocol = ["cbb_platform/v2/dependence.py"]` is recorded in the confirmation and final records.
- The failed attempt's log is kept at `data/qa/large/v2_phase2/confirm_attempt1_failed_at_fit.log`.

## 9. Findings to note for Phase 3 (not acted on here)

**OOS PTS marginals are too narrow for non-starters.** These are the realized/model latent scale ratios (lam). REB is also wide for bench players. The other stats are ≈1.

| role | PTS lam | REB lam |
|---|---|---|
| BENCH_9PLUS | 1.39 | 1.15 |
| ROTATION_6_8 | 1.28 | 1.07 |
| TOP5_MINUTES | 1.05 | 0.97 |

Mean offsets are negative for bench PTS (−0.29 latent SD). The dependence step absorbs this only for estimating correlations; the marginals were not changed (as required).

**Joint over events are under-predicted by every arm.** Observed exceeds predicted for both-scorers-over (teammates and opponents) and most multi-player events. This points to shared game-environment / pace mean effects, which Phase 2 is forbidden to model ("no new pace/game-environment mean features"), plus the narrow marginals above.

## 10. Artifacts

| file | role |
|---|---|
| `modeling/player_props_v2/phase2_protocol.json` | frozen protocol (sha256 1865797d…) |
| `modeling/player_props_v2/phase2_selection_fit_2023_24.json` | selection fit (MID + RAND, scales, PIT comparison) |
| `modeling/player_props_v2/phase2_dependence_selection.json` | 2024-25 evaluation + decision (sha256 a543ce7d…) |
| `modeling/player_props_v2/phase2_confirmation_fit_through_2024_25.json` | confirmation fit |
| `modeling/player_props_v2/phase2_retrospective_2025_26.json` | one-shot 2025-26 confirmation |
| `modeling/player_props_v2/dependence_J2.json` | **selected dependence artifact**, sha256 b41564a988f37a4c01a0dec9f8a9cb67aea69717294809b3eaa49acf4babb1ff; references MARGINAL_ARTIFACT_SHA256 bfcef1fb… |
| `modeling/player_props_v2/phase2_verification.json` | guards + 200-game construction check (invalid 0, min eig 0.044) |
| `cbb_platform/v2/{latent,dependence,joint,phase2}.py` | code |
| `tests/test_v2_phase2.py` | 17 tests |

FULL_SUITE: 605 passed / 0 failed / 3 skipped (`data/qa/large/v2_phase2/full_suite.log`).

Only the logs are kept under `data/qa/large/v2_phase2/` (gitignored). The per-game evidence parquets were deleted after freezing.

**Tests** (`tests/test_v2_phase2.py`, 17 passed): same seed → identical draws; J0 independent; PSD / unit diagonal / pinned blocks; PMFs preserved; combo identities; threshold route = inverse-CDF route; de-attenuation; no evaluation-season outcomes in fitting; no 2026-27 read; per-row dispersion; stage order; final artifact; and v1 + Phase 1B artifacts unchanged.
