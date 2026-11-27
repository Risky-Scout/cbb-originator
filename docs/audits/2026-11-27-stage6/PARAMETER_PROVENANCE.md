# Stage 06: parameter provenance inventory (player engine + game model)

**Scope.** The inventory covers:
* the player engine `JM1_FINAL+RA2V1[AST,FG3M,STL,REB]+PTSC1[0]+TOVT1+DEV1+CTX1+PI2+PS2`;
* the game model `cbb-game-model-1.2.0` (GC3, TP1P, γ bridge).

**Method.** The inventory was traced read-only in code at d392832, which is identical for these files to da71e34. For each fitted parameter, the training →
persistence → runtime links were followed to the exact scoring-path read. A fit artifact was never taken as proof that a parameter was
fitted. The active walk-forward store was checked on the Droplet (read-only): `ACTIVE_MODEL_VERSION cbb-jm1-2026-27-20261127`,
promoted 2026-11-27 12:20Z, with gates PASS. The context ledger meta was also checked.

**Classes:**
* **FIT-D**: empirically fitted, refit every morning by the walk-forward.
* **FIT-1**: empirically fitted once, frozen.
* **REG**: regularized estimate.
* **VAL**: validated fixed value.
* **MATH**: mathematical constant or identity.
* **OPS**: operational control.
* **SAFE**: safety control.
* **HEUR**: unsupported heuristic (hand-set, no selection evidence).
* **UNK**: unknown provenance (a documented claim with no estimator in the repo).

**Stage owners.** The recorded stages are 07, 08, 09, 11, 16 and 19 (`docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md` §2). Every other owner is given as a
**domain** with a *provisional* number: 10 rates/priors, 12 distribution shape/tails, 13 dependence, 14 calibration, 15 game ratings, 17 market comparison.
Confirm those numbers against the owner's program list.

## 1. Player engine

**Scoring order** (`prod/run.py:221-235`):
1. v0 means, minutes σ and p_play (`inputs.marginal_rows`).
2. RA2, then TOVT1.
3. `base_mu` is snapshotted.
4. DEV1, then CTX1, then PI2.
5. Availability.
6. `simulate_game`: the J2 bank. PS2 is patched into the minutes draw and PTSC1 into the mixture tables.
7. Market distributions.

**Parameter source:** the ACTIVE walk-forward version (`models/<v>/{marginal,jm1_structural,residual_dependence}.json`), else the frozen opening
artifacts (fallback F1 below).

| # | parameter (file) | value / estimator | class | T→P→R | owner |
|---|---|---|---|---|---|
| P1 | p_play logistic (`props_v0_participation.py:122-138`, `v2/marginal.py:242-265`) | IRLS, 23 inputs + missing indicators, seasons 2024..T | FIT-D | OK. Gates `_scored` only; never mixed into draws | 09 |
| P2 | participation L2 (`:39`) 1e-4·n; logit clip ±35 | | HEUR / SAFE | | 09 |
| P3 | minutes ridge β + standardization (`props_v0.py:220-226`) | weighted ridge λ = 1e-4·Σw | FIT-D | OK. Coefficient order is positional: gated at promotion, not re-checked at load | 09 |
| P4 | minutes σ by band [0,10,20,30,∞) (`props_v0.py:194-205`) | weighted RMS residual per band; a band with ≤ 50 rows takes the pooled σ | FIT-D | OK | 09 |
| P5 | minutes clip [1,45]; draw KMAX 60; `clip(min_mu+σε,0,60)·D/E[D]` | | SAFE / MATH | | 09 |
| P6 | PS2 quantile map (`prod/minutes_state.py`, `minutes_state_qmap.json` sha 304ecff…) | empirical z-quantiles F2025, bands [0,10,20,28,∞) (`r16_ps2_fit.py`) | FIT-1 | OK. Standardized with the F2025 σ; runtime σ is daily (S6) | 09 |
| P7 | PS2 band edges, tail rule (slope-1 beyond the 1 %/99 % knots) | | HEUR. The protocol says "linear, clamped" (S1) | | 09/12 |
| P8 | overtime law p_K, E[D] = 40.369 (`structural.py:186-198`) | empirical team-game frequency 2014..T | FIT-D / MATH | OK | 09 |
| P9 | recency half-life 1 season (config) | | VAL (`weighting_selection.json`, phase-1B ruling) | | 10 |
| P10 | prior_seasons_used 3, rate_prior_min_minutes 100, NB threshold 1.1, sigma buckets | | HEUR | | 10/12 |
| P11 | position prior rate (`props_v0.py:233`) | Σw·y/Σw·min | FIT-D | OK (also RA2) | 10 |
| P12 | prior strength k_t (`props_v0.py:237-242`) | Gamma–Poisson method of moments, minutes-weighted, **no recency weight** (S8) | REG, FIT-D | OK | 10 |
| P13 | mean_floor 0.01 | | SAFE | | 10 |
| P14 | RA2 α, β, γ, SCALE (`prod/rate_a2.py:39-44`) | fit F2024+F2025 (`2026-11-22-causal/results/challenger_a2_holdout.json`) | FIT-1 | OK | 10 |
| P15 | RA2 D_CLIP 1.5, D_MIN 5, Z_REF 20 | pre-declared, holdout PASS jointly, never varied | VAL (joint) | | 10 |
| P16 | TOVT1 B −0.0767, UBAR 0.721 (`prod/tov_trend.py:21-22`) | Poisson-GLM slope 2023-24 + 2024-25 (PHASE4 `t1_eval`) | FIT-1 | OK | 10 |
| P17 | DEV1 δ (7 stats × same/other team) (`prod/dev_trend.py:26-34`) | Poisson MLE TRAIN F2025 (`2026-11-25-defects/results/p1_train_fit.json`) | FIT-1 | OK | 10 |
| P18 | **DEV1 K_MINUTES 31** (`dev_trend.py:24`) | literal in `p1_build_mults.py:27`; no grid or fit; one value for all 7 stats | **HEUR** | | 10 |
| P19 | PI2 coefficients a, b, c, e (PTS, AST, FG3M, TOV) (`prod/preseason_prior.py:35-40`) | Poisson-weighted LS with a ridge N(0, .5²) on TRAIN F2025 (`r4_pi1_fit.json`) | REG, FIT-1 | OK | 10 |
| P20 | **PI2 CLIP [0.7,1.3]**: binds for every first-appearance PTS player (0.565 → 0.7); K_NO_PRIOR 31; z_d = clip(Δ,±25)/10; z_m = (clip(mpg,0,36)−20)/8 | declared in the protocol, no selection evidence | **HEUR** | | 10 |
| P21 | PI2 strength / origin table (`preseason_prior_2027.json` sha 4132a5ec…) | 2025-26 final strengths | FIT-1 (sealed) | OK | 10/16 |
| P22 | CTX1 κ_team, κ_opp (`prod/context_adj.py:35-36`) | grid on TRAIN F2025; PTS κ_opp overridden to ∞ (documented decision; the fit chose 640) | FIT-1 (grid) | OK; selected against the **pre-PS2** incumbent (see C3) | 11 |
| P23 | CTX1 ledger Y / CF sums | rebuilt each morning from point-in-time OOS rows | FIT-D | OK; **defect C3 corrected** | 11/07 |
| P24 | LEDGER_SIMS 2000; MAX_LEDGER_AGE_DAYS 2 | | OPS / SAFE | | 11/07 |
| P25 | environment priors, pace/eff coefficients, sd_pace, sd_eff, ρ_eff (`structural.py:172-193`) | OLS / residual moments 2014..T | FIT-D | OK | 15/13 |
| P26 | SHRINK_GAMES_PACE 4 / _EFF 6 | "declared, not tuned" | HEUR | | 15 |
| P27 | structural GLM (intercept, β·log(M/min_mu), env terms; β_PTS ≈ 1.30) (`structural.py:280-309`) | Poisson IRLS on Phase-1B OOS rows 2023..T given realized state | FIT-D | OK. The 2027 OOS rows come from the uncorrected opening marginal (S3) | 11 |
| P28 | GLM_RIDGE 1e-6·n, MIN_FLOOR 1 min, JM_TERMS (no opportunity term), JM1_ALPHA 1 (anchor is a no-op) | | REG / SAFE / VAL (`jm1_final_protocol.json`) | | 11 |
| P29 | conditional dispersion φ_s (`structural.py:322-334`) | unweighted method of moments on the true-OOS residual basis 2024..T | FIT-D | OK for 6 stats; PTS rows are overwritten by PTSC1 | 12 |
| P30 | **v0 dispersion φ / family** (`props_v0.py:185-191`; STL policy 0.16) | method of moments | FIT-D, **persisted but not read by the scorer** (`inputs.py:46` copies it; `jm1_final_production.py:160` reads the conditional φ only). The family still feeds P29 and gate `marginal_dispersion_families` | | 12 |
| P31 | PTSC1 PV (.11,.65,.24), PHI_N 0 (grid), NMAX 100 (`prod/pts_compound.py:25-27`) | maximum likelihood 2023-24 + 2024-25; φ_N selected on F2025 | FIT-1 / SAFE | OK | 12 |
| P32 | count KMAX 120 (`v2/latent.py:31`, tail folded); config `pmf_support_max` never read; GRID_H .005; THR_CAP ±40, z clip ±9 | | SAFE / OPS | measured: max folded tail mass on 11-26 = 1.6e-9 | 12/13 |
| P33 | J2 residual correlation R (global → role → role×position) (`v2/dependence.py:269-360`) | MID-PIT residuals (PTS under NB), soft threshold 1.96 bootstrap SE, PSD-conditioned | REG, FIT-D | OK. Estimated on NB PIT while runtime PTS is compound (S2) | 13 |
| P34 | dependence normalization δ, λ | fitted, persisted; used in estimation only | FIT-D (not a runtime parameter) | | 13 |
| P35 | SHRINK_Z, BOOTSTRAP 200, FIT_SEED, CONDITION_RATIO, floors, PIT_EPSILON | "NBA declared" | VAL (phase-2 protocol) / SAFE | | 13 |
| P36 | per-player PSD shrinks c_ab, c_q; Cholesky jitter | bisection at runtime / \|min eig\|+1e-12 | MATH / SAFE | | 13 |
| P37 | SIMS 20,000 (manifest-checked); seeds SIM_SEED 20263, SEED_BASE 20271 + blake2b | | OPS | | — |
| P38 | PMF_TOL / PSD_TOL 1e-9; DAILY_REFIT_TIME 06:00 ET; season windows | | SAFE / OPS / VAL | | 07 |

**Promotion gates** (`walkforward/gates.py`) check identity, legality, determinism, PSD and normalization. **No daily refit is validated predictively.**
The frozen corrections (P14–P22) were fitted against the opening or annual incumbents, not against the daily-refit parameters they now sit on.
Owner: domain 14 (calibration), with Stage 08.

## 2. Game model

**Prediction construction.** The game PMF is analytic: it has no Monte Carlo draws.
1. Mean: a daily ridge rating (O, D, pace, μ, h, π) plus the availability term γ·ΔS.
2. SDs: a heteroscedastic sd model.
3. Shape: a discretized bivariate normal, then a frozen endgame kernel, then the OT recursion.
4. Moment matching to the targets.

| # | parameter (file) | value / estimator | class | T→P→R | owner |
|---|---|---|---|---|---|
| G1 | in-season ratings O, D, P, μ, h, π (`game/ratings.py:129-190`) | ridge on DI_VS_DI finals knowable before refit_cutoff(D); efficiency = OT-inclusive pts / OT-inclusive poss; pace = per-40 poss | FIT-D | OK. **Reuse key corrected (C5)** | 15 |
| G2 | prediction poss·eff/100 (`ratings.py:66-77`) | a **40-minute** expectation | MATH | **E1 corrected (C4)** | 15/12 |
| G3 | POSS_FTA .475; sanity 45 ≤ poss ≤ 110; weight poss/70; sum-to-zero 1e3/n | possession convention / filter / scale / identifiability | MATH / SAFE / OPS | the filter drops games silently | 15 |
| G4 | tau_days 150; λ_off 5, λ_def 5, λ_pace 2, λ_league 10; ρ_carry 1.0 (`params/game_model.json`) | claimed "coordinate search on 2023-24"; **no search code in the repo**. GC1 (ρ 0.6) and GC2 (λ 2.5) challengers FAILED (partial validation). Dataclass defaults differ (60/12/12/8/1/0.7) but are unreachable: the JSON is strict | **UNK** (documentation-only) | GM → forecast:187 | 15 |
| G5 | league prior μ 108.543, h 2.0732, π 68.050 | 2025-26 season-final chain fit (`2026-11-25-game/scripts/g7_seal_prior.py`) | FIT-1 | OK | 15 |
| G6 | carry_level / carry_pace 0.6 (GC3) | pre-declared point; gates T1–T5 PASS (`g6_gc3_eval.json`) | VAL | Consumed only through the sealed prior parquet. **Lineage label corrected (C7)** | 15 |
| G7 | TP1P preseason margin strength (364/368 teams) (`ratings_prior_2027_tp1p.parquet`) | ridge (penalty 1.0) on returning/transfer/recruit features, seasons 2016..2026 (`r9_team_prior.py`, `r14_seal_tp1p.py`); gates E1–E4 PASS | FIT-1, VAL | OK (sealed = fit to 3.6e-15). The intermediate is gitignored/local; the 2025-26 term uses a Starter-Pack stand-in; ridge 1.0 and the ±25/10 clip are hand-set | 15/07 |
| G8 | 4 teams without TP1P get the GC3 strength; an unknown team gets the league average | seal-time count logged; runtime per-row `home_known` only | fallback | | 15 |
| G9 | **β = 0** (absolute S bridge) | claimed walk-forward selection; no code; **loaded but never applied** | UNK; **guard added (C6)** | | 16 |
| G10 | **γ = 0.3** (availability bridge) | `game_model.json` says "conservative prior (not fitted)"; ΔS ≤ 0 by construction; own team only (BR1 b_opp +2.56) | **HEUR** | OK | 16 |
| G11 | variance a_m 11.23, c_m .12, k_m 0, τ_m 4; a_t 16.60, c_t .04, k_t .5, τ_t 4; ρ_mt .056 | claimed "Gaussian MLE grid on 2023-24 walk-forward residuals"; **fitting code absent**. Fitted on 1.0.0 means, not refit for 1.1.0/1.2.0. Validated on 1.0.0 (in95 .949/.947/.949); 2027 1.2.0 margin in95 .939 | UNK (validated on an earlier model) | GM → VarianceParams (strict) | 12/14/13 |
| G12 | endgame kernel 3 pace × 41 leads × 31 × 31 (`endgame_ot.npz` sha 93369a…) | empirical last-120 s increments 2014–2023, 48,470 games, shrunk w = n/(n+200) (`research/fit_endgame_ot.py`) | FIT-1 | OK (npz hash-checked) | 12 |
| G13 | T* 120 s, max lead 20, reach 30, MIN_N 200, smoothing weights | design choices | HEUR | | 12 |
| G14 | pace edges 3.2105 / 3.6053 ppm (realized-pace tertiles 2014–23) | | FIT-1 | Runtime uses the expected total; the 2027 league-mean game sits at the fast edge (S3g) | 12 |
| G15 | OT κ 1.1744, sd 3.941, ρ .2634 (3,976 OT periods) | `fit_endgame_ot.py` | FIT-1 | OK. Manifest values were unhashed: **lineage hash added (C8)** | 12 |
| G16 | MAX_OT 8; latent ±6 sd (folded); GL 5; MATCH 10 iterations / tol 2e-4; floors; STORE_FLOOR 1e-10 (≈1e-6 renormalized) | | SAFE / MATH / OPS | moment-match non-convergence is recorded but not gated | 12 |
| G17 | coherence ladders (spread ±40, totals 90..200, team totals 40..110) | | SAFE | | 14 |

## 3. Silent fallbacks and hardcoded defaults

| id | where | trigger → behaviour | recorded? | owner |
|---|---|---|---|---|
| F1 | `prod/model_source.scoring_model:71-74` | any `ModelSourceUnavailable` → score with the frozen through-2025-26 artifacts (contradicts its own "no automatic fallback" docstring) | only the changed `model_version` | 07/19 |
| F2 | `prod/context_adj.py:142-166` | missing or > 2-day ledger → factor 1 (receipted); team/opponent without ledger rows → 1 | partly | 11 |
| F3 | `prod/preseason_prior.py:83-94` | missing origin/strength → z_d 0; NaN mpg → 20; NaN factor → 1.0 | no count | 10 |
| F4 | `rate_a2._col`, `dev_trend._col` | missing column → zeros; NaN `pps_same_team` → "other team" δ | no | 10 |
| F5 | `v2/phase3._game_input:216-220`; `structural.env_design/env_predict` | missing environment → zero deviation / league priors | no | 15 |
| F6 | `props_v0._design`, `props_v0_participation._design` | NaN in a feature without a `__missing` column → z = 0 | no | 09/10 |
| F7 | `dependence.Structure.R` | group unseen at fit → global R | no | 13 |
| F8 | `game/forecast.py` | missing neutral → home court applied; team not in games-played map → n = 0 (maximum sd inflation); no S → Δ 0; INITIAL baseline request without OFF → NEWS_FREE; `pending_finals` = 0 without a schedule table | per-row columns / receipt only | 15/16/07 |
| F9 | env defaults | `CBB_WF_ROOT`, `CBB_WF_REFIT_TIME` 06:00, `CBB_INITIAL_AVAILABILITY` ON, `CBB_FEATURE_CLOSURE` ON, `CBB_GAME_AVAILABILITY_BASELINE` NEWS_FREE, `CBB_GAME_MEAN_BASIS` REGULATION (new); code `ENABLED` switches in TT/DV/CX/PI/MS2 | engine_id names the active corrections, not the env values | 19 |

## 4. Demonstrated implementation errors and their disposition

| id | defect | evidence | disposition |
|---|---|---|---|
| **C3** | The CTX1 counterfactual ledger's incumbent tag excluded PS2, but `simulate_game` (used by `context_adj.ledger_rows`) always installs PS2. | The ledger was seeded 11-26 13:45Z (1,073 games, before PS2 went live at 22:48Z). The 11-27 morning appended 27 games under the same tag `a4b0cb644c60`, so the ledger mixes two incumbents. | **Corrected**: `engine.pre_context_tag` includes PS2 (new tag `af49d60f2175`). The next morning walk-forward rebuilds the whole ledger once with a consistent incumbent: about +26 min CPU, as at the 11-26 seeding. CTX1 κ were selected against the pre-PS2 incumbent → Stage 11 re-validation. |
| **C4** (E1) | Game expected scores are 40-minute expectations, but the OT-inclusive final PMF was moment-matched to them, so the OT expectation was removed from every total and team total. | Code trace (ratings: per-40 pace × OT-inclusive per-possession efficiency). Walk-forward data, all three seasons: final − target exceeds per-40 − target by 1.10–1.25 points, which is P(OT) × OT points per OT game. | **Corrected**: the means are matched on the regulation PMF and OT adds P(tie)·E[OT] (joint 1.1.0). `CBB_GAME_MEAN_BASIS=FINAL` is the in-place fallback. Validation: `results/ot_mean_basis.json` (README §4). |
| **C5** (E2) | Daily game ratings were reused under a changed prior or changed rating params (key = cutoff + FRESH only). | code trace | **Corrected**: reuse also requires the same prior id and the same fit params; otherwise `REBUILT_PRIOR_OR_PARAMS_CHANGED`. |
| **C6** (E3) | β is loaded and silently ignored. | code trace | **Guarded**: a nonzero β fails closed (not implemented). |
| **C7** (E4) | The persisted ratings meta recorded carry 1.0 while the sealed prior was built with 0.6. | code trace | **Corrected** (lineage only; the fit is unchanged). |
| **C8** (E6) | OT κ/sd/ρ, the pace edges, T* and max lead are read from an unhashed manifest. | code trace | **Lineage hash added**: `endgame_ot_manifest_sha256` on every game PMF row. Sealing a hash into `game_model.json` would change `params_sha256`; that is left to the owner of domain 12. |
| E2p | Fitted-but-unread provenance: the v0 φ (incl. STL 0.16), `pmf_support_max` and the PTS conditional φ are overwritten by PTSC1. | code trace | No numerical effect. Recorded; domain 12 should stop presenting them as model state, and gate `marginal_dispersion_families` gates an unused family. |
| E5g | The GC3 seal / g1 backtest scripts are not re-runnable as committed (monkeypatched `prior_for` arity). The 1.0.0 prior seal, the rating-param search, the variance MLE grid and the β selection have no code. | code trace | Documentation-only provenance (G4, G9, G11 = UNK). Domain 15/12. |

## 5. Suspicions (not demonstrated; assigned)

* **S1 (09/12).** PS2 tail rule vs the protocol text. PS1 doc 48 min vs code 60.
* **S2 (13).** J2 correlation estimated on PTS NB PIT, used with compound PTS margins.
* **S3 (10/14).** The daily GLM intercept is trained on uncorrected opening OOS rows, with TOVT/DEV1/PI2/CTX1 multiplied on top: a double-correction risk, clearest for TOV.
* **S4 (09/11).** Minutes clip plus Jensen's inequality with β > 1 inflates low-minute means (PTS PMF/marginal 1.22 at min_mu < 10; consistent with `cbb-bank-not-team-score`).
* **S5 (10).** PI2's clip excludes RA2/TOVT; NO_PRIOR classification interacts with DEV1 "other".
* **S6 (09).** PS2 is standardized with the F2025 σ but runtime uses the daily σ; band edges 28 vs 30.
* **S7 (13/09).** `role_l5_minutes_rank` is never null, so a zero-history player can be TOP5.
* **S8 (10).** k_t ignores recency.
* **S1g (12/14).** Variance params are stale relative to 1.2.0 (margin in95 .939, early-season z-sd 1.05–1.12).
* **S2g (16).** γ is one-sided and own-team only.
* **S3g (12).** The pace-bucket edge sits at the league mean.
* **S4g (15).** λ_league total drift.
* **S5g (15).** Single league HCA; a missing neutral flag applies it.
* **S7g (12/07).** Moment-match non-convergence and `pending_finals` are ungated.
* **S8g (17).** NaN `market_last_update` in game candidates.
* **S9g (15/07).** TP1P intermediate is local only.
