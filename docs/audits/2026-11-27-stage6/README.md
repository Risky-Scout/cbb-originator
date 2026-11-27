# Stage 06: independent probability / export mathematics and parameter provenance (2026-11-27)

The successor is built on d392832 (Stage 05, CI 46052406957; checkpoint da71e34). Production is still 9f65e7f, with rollback cae2a02.
* Governing directives: `docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md` (the Stage 06 addendum is stored verbatim).
* Parameter inventory: `PARAMETER_PROVENANCE.md`.
* Deploy: `DEPLOY_PROCEDURE.md`.

**Resource safety.** No remote computation was run in Stage 06.
* Droplet access was read-only `ssh` / `scp` of small artifacts: exports, the 11-26 frozen evaluations, 16 PMF evidence files (12 MB), actuals, the active-model and ledger meta.
* Before any access, the Droplet was checked for leftover audit units. There were none: only the scheduled quotes tick was running.
* All computation ran locally: one process, threads = 1, nice 10.
* No provider request was made. No production state was written.

## 1. Reused evidence (not repeated)

| subject | earlier evidence | why it still holds |
|---|---|---|
| Game-market P(side) from the joint PMF: spread push, totals, team totals, ML tie; settlement; raw-price and team mapping | `docs/audits/2026-11-21/GAME_REPORT.md` §1–2: 279/279 frozen evaluations reconstructed, max \|Δp\| 3.3e-16; independent re-grade identical | `game/markets.py` and `game/joint.py` have had no commits since; the forecast.py commits change only target means and lineage (re-read in Stage 06) |
| Player best-offer replay from the persisted candidate-offer sets | `docs/audits/2026-11-22/README.md`: 886/886 reconstructed exactly | selection code unchanged. Re-checked here only as part of the PMF → price chain on a new slate |
| Selection rule (max-EV kept; no rule beats the market) | 2026-11-22 audit | unchanged; actionability stays NOT_VALIDATED |

## 2. Independent verification of the player-prop mathematics and the daily exports

The tool is `scripts/s6_verify_exports.py`. It imports no `cbb_platform` module: pricing, de-vig, EV, ordering, tiers, settlement and aggregation are
first-principles code, and it has its own xlsx reader. It was run on slate **2026-11-26**:
* the Boards and Distributions of export folder 11-26;
* the Grades of 11-26 in folder 11-27;
* the 758 frozen evaluations, 1,440 priced offers and 7,488 PMFs.

The slate spans both engines (pre/post the 22:48Z PI2/PS2 deploy). Results: `results/verify_exports_2026-11-26.json`.

| requirement | checks (pass / fail) |
|---|---|
| **PMF → probabilities.** P(win), P(lose), P(push) at the exact line from the persisted PMF that the evaluation names: over = P(X > L), under = P(X < L), push only at an integer L | 1,440 / 0 (to 1e-12); line inside the support 758 / 0 |
| **Fair price.** Model p = P(side \| no push); same-book proportional de-vig; edge | 1,440 / 0 each |
| **EV.** EV = p_win (d − 1) − p_lose (a push refunds); decimal = American conversion; EV gross = net | 1,440 / 0 |
| **Best offer.** One per game × player × stat; independent order: EV, then edge (missing last), then model p, then payout, then book, line, OVER first; entry fields = best | 758 / 0; unique keys |
| **Preferred side, books at line, reliability, screen, tier.** Is Actionable = NO (unvalidated) | 758 / 0 each |
| **Point in time.** Prediction and quote strictly before the cutoff; frozen at or after it | 758 / 0 |
| **Player role** from prediction-time minutes (CORE ≥ 30 / ROTATION 20–30 / LIMITED > 0) | 758 / 0 |
| **Distributions.** One per (game, player, stat); all 12 supported stats for each of 624 players; summary rows = PMF refs (7,488); sha256 recomputed; mass = 1 and non-negative; support consistent; mean / SD / P10–P90 recomputed | all pass. Reported mean = PMF mean (max 7e-15). Max folded tail mass 1.6e-9 |
| **Combos.** PRA/PR/PA/RA/STOCKS (BANK_DRAWS) vs the sum of the base means (BANK_MIXTURE_PMF, Rao-Blackwellized) | the difference is unbiased Monte Carlo noise: mean ≈ 0 (\|mean\| ≤ 7e-4), SD 0.007–0.030, max 0.11; not a defect (domain 13 note) |
| **Settlement.** Independent WIN/LOSS/PUSH/VOID_DNP from the final box: combo actual = sum of components; 0 min / DNP = VOID_DNP; units = d − 1 / −1 / 0; settlement rows consistent across ticks | 758 / 0; 250 combo sums; consistency across ticks / 0 fail |
| **Grades reconciliation.** Each graded row traces to its frozen evaluation by content hash, with fields equal; graded rows = evaluations | 758 / 0 |
| **Grades aggregates.** By Stat (12), By Player Role (3), Stat × Role (26): N, graded, W/L/P, VOID, hit rate, Brier, log loss, units, ROI recomputed | all pass |
| **Board.** One row per game × player × stat over Full Board + Model Coverage = the 7,488 forecasts; Full Board = VALID_MARKET only; 60 % / 65 % sheets = the exact ordered subsets of tiers {60_PLUS, 65_PLUS} / {65_PLUS}; thresholds and completeness | all pass |
| **Board vs frozen evaluation.** Same PMF and same quote instant → identical | 133 / 0. Ten rows differ by contract: the Board prices the *latest* pre-cutoff quote, the frozen evaluation the *decision* quote |
| **Hash receipts.** `xlsx_sha256` of Boards / Distributions / Grades and the PMF parquet sha recomputed; receipt row counts | all pass |
| **Grades 2nd worksheet = success by stat and player role** | **FAIL in production**: in SHADOW mode the 2nd sheet is `Game Markets` (`Player Props, Game Markets, Summary, By Stat, …`) |

**Populations of the 11-26 slate.** These are kept distinct:

| population | rows |
|---|---|
| forecast rows | 7,488 PMFs |
| market-matched rows (graded) | 758 |
| ...of which positive-EV preferred | 408 |
| ...of which NO_POSITIVE_EV | 350 |
| reporting-screen pass | 23 |
| official eligible selections | **0** (actionability not validated) |

The graded set is therefore a market-matched population, not a selection record.

**Correction C1: the Grades second worksheet.** `Success by Stat and Role` is now the second worksheet in both layouts (`market_exports.success_sheet`). It holds:
* a POPULATIONS block: forecast rows (from the GAME_FROZEN markers), market-matched rows, positive-EV rows, screen pass, official eligible selections;
* success by stat and by player role, for the market-matched rows;
* the same two tables for the official eligible selections.

Every existing sheet is kept by name. Validation:
* The real 11-26 evaluations, regenerated with the corrected code, give By Stat / By Role blocks identical to production's sheets (CLV columns excluded locally).
* The population counts equal the independent verifier's: 7,488 / 758 / 408 / 23 / 0.
* The verifier on the corrected workbook reports `failed_checks: []` (`results/verify_exports_2026-11-26_corrected_grades.json`).

Historical Grades workbooks are not rewritten. The next export writes the new layout.

## 3. Parameter provenance (summary; full inventory in `PARAMETER_PROVENANCE.md`)

The player engine has 38 inventoried parameter groups and the game model 17.

**Daily-refit parameters, all three links verified (training → persistence → runtime).** Participation, minutes β/σ, prior rates and k, OT law,
environment, structural GLM, conditional φ, J2 correlation, and the in-season team ratings.

**Frozen fitted parameters with evidence files.** RA2, TOVT1, DEV1 δ, PI2 coefficients, CTX1 κ, PS2 map, PTSC1, endgame kernel, OT law, TP1P, league prior.

**Validated fixed.** Recency half-life, the RA2 controls, JM1 α / terms, the dependence constants, and GC3 carry 0.6.

**Unsupported heuristics.**
* DEV1 / PI2 K = 31.
* The PI2 clip [0.7, 1.3], which binds for every first-appearance PTS player.
* PI2 standardization constants.
* Environment shrink 4/6.
* Several thresholds (NB 1.1, 100 min, bands).
* The game γ = 0.3, which is self-declared "not fitted".
* The endgame design constants.

**Unknown provenance (documented claim, no estimator in the repo).**
* The game rating hyper-parameters (τ 150, λ 5/5/2/10, ρ_carry 1).
* The game variance parameters, which were also fitted on the 1.0.0 means and not refit.
* β.

**Fitted but not read at runtime.** The v0 dispersion φ (incl. the STL 0.16 policy), `pmf_support_max`, and the PTS conditional φ (overwritten by PTSC1).

**The daily refit is never validated predictively.** The gates are integrity only. The frozen corrections were fitted against the opening / annual incumbents, not the daily-refit parameters.

**Silent fallbacks.** Nine are listed (F1–F9). The most material is F1: `model_source.scoring_model` silently scores with the frozen through-2025-26
artifacts when ACTIVE is unavailable, and only `model_version` reveals it.

## 4. Demonstrated mathematical implementation errors corrected in Stage 06

| id | defect | correction | validation |
|---|---|---|---|
| C1 | Grades 2nd worksheet (export contract) | §2 | §2 |
| C3 | The CTX1 ledger incumbent tag excluded PS2 although the ledger's counterfactual simulation always applies it. The ledger was seeded 11-26 13:45Z without PS2 (1,073 games); 27 PS2 games were appended 11-27 under the same tag | `engine.pre_context_tag` includes PS2 (af49d60f2175). One consistent rebuild at the next morning walk-forward | `test_context_adj` (tag with/without PS2); rebuild cost measured 11-26 (+26 CPU-min) |
| C4 | **Game OT omission.** The targets are 40-minute expectations (ratings: per-40 pace × OT-inclusive points per possession), but the OT-inclusive final PMF was moment-matched to them, which removed the OT expectation | means matched on the regulation PMF; OT adds P(tie)·E[OT] (`cbb-game-joint-1.1.0`); fallback `CBB_GAME_MEAN_BASIS=FINAL` | see §4.1 |
| C5 | Daily game-ratings reuse ignored a prior / param change | reuse keyed on prior id + fit params | `test_daily_ratings_reuse_requires_the_same_fit_params_and_prior` |
| C6 | β loaded, silently unused | β ≠ 0 fails closed | `test_nonzero_beta_fails_closed` |
| C7 | Ratings meta recorded carry 1.0 (sealed 0.6) | params passed through (lineage only) | unchanged fit |
| C8 | OT / pace-edge manifest values unhashed | `endgame_ot_manifest_sha256` per game PMF row | test |

### 4.1 C4 validation (out of sample, no refit)

`scripts/s6_ot_mean_basis.py` replays the sealed production construction on the existing point-in-time walk-forward means of the production
configuration: TP1P, 2023-24 / 2024-25 / 2026-27, every second game. It scores both bases on the realized final scores.

The run finished normally: 6,229 games. Its process survived a MacBook sleep and network interruption, resumed, and exited 0. Nothing was rerun.
Results: `results/ot_mean_basis.json` (per-game `results/ot_mean_basis.parquet`).

| season | n | total bias (actual − model), FINAL | total bias, REGULATION | paired log-score gain (joint cell) | paired CRPS change (total) |
|---|---|---|---|---|---|
| 2023-24 | 2,860 | +1.148 | +0.386 | +0.0008 ± 0.0009 | −0.0088 ± 0.0086 |
| 2024-25 | 2,876 | +0.802 | +0.024 | −0.0000 ± 0.0009 | −0.0046 ± 0.0087 |
| 2026-27 | 493 | +1.583 | +0.997 | +0.0007 ± 0.0018 | −0.0070 ± 0.0177 |
| all | 6,229 | +1.023 | +0.267 | +0.0004 ± 0.0006 | −0.0067 ± 0.0058 |

Brier on P(home win) is unchanged in every season (the margin is unaffected).

**Default: REGULATION.**
* The construction error is demonstrated by the code trace.
* The correction removes 0.6–0.8 points of the systematic total under-projection in **every** season, in the predicted direction. The paired bias change is deterministic, not sampling noise.
* The proper scores (joint log score, total CRPS) are not worse in any season, and CRPS improves in all three. Each individual change is within about 1 SE, so no scoring gain is claimed beyond "not worse".

The remaining +0.27 overall total bias (+1.0 on 2026-27) is league/level drift. It is not OT, and it is assigned to game ratings (domain 15; see S4g).
`CBB_GAME_MEAN_BASIS=FINAL` remains the in-place fallback.


## 5. Tests

Focused local runs (threads = 1, nice 10), with the full suite left to GitHub CI:
* market / exports: 35 passed;
* game / coherence / context / PI2 / PS2 / engine / release B: 78 + 16 passed;
* every test file importing the changed modules (30 files): **398 passed, 5 skipped**.

GitHub CI run 46062370887 on 2ac9117: **success**, full regression 1,172 passed, 36 skipped. Ledger key `stage6_math_provenance_20261127`.

## 6. Assignments recorded for later stages (not started here)

* **Stage 07:** F1 frozen-model fallback receipt; `pending_finals` without a schedule table; the TP1P intermediate is local-only; the GC3 seal scripts are not re-runnable.
* **Stage 08:** the corrected-engine evaluation must label two populations separately:
  * **HISTORICAL_PRODUCTION**, the immutable rows as forecast. This includes the 268 Stage 05 defective INITIAL rows (`../2026-11-27-stage5/results/initial_gap_rows.csv`, 34 at p_play ≥ 0.5) and the pre-C3/C4 rows.
  * **CORRECTED_CANDIDATE_REPLAY**.

  The key is `code_commit` + `engine_id` + game `joint_version` / `mean_basis`. Historical forecasts are never rewritten.
* **Stage 09:** PS2 tail rule and σ standardization; minutes clip / Jensen inflation; role rank never null.
* **Stage 11:** re-validate CTX1 κ against the PS2 incumbent; daily GLM trained on uncorrected opening OOS rows (double-correction risk).
* **Stage 16:** γ unfitted and one-sided; β not implemented; no opponent term.
* **Domains without a recorded stage number** (provisional numbers in `PARAMETER_PROVENANCE.md`):
  * rates/priors: K = 31, PI2 clip, k_t recency;
  * shape/tails: stale game variance, endgame constants, unused v0 φ;
  * dependence: J2 PTS NB vs compound;
  * calibration: no predictive daily-refit gate;
  * game ratings: hyper-parameter provenance, HCA, unknown teams.
