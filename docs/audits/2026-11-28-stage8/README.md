# Stage 08: definitive chronological performance benchmark (2026-11-28)

**Benchmark id `CBB-S08-BENCHMARK-20261128`.** It is frozen in `results/BENCHMARK_MANIFEST.json`, which holds the identities, every source and result sha256, and the definitions.

* Governance: `docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md` §6 is the consolidated successor instrument, preserved verbatim.
* Ledger key: `stage8_chronological_benchmark_20261128`.

**What Stage 08 did not do.** It changed no predictive mathematics, refit nothing, made no provider request and wrote no production data. Stage 09 has not been started.

## 1. Lineage and deployment (authenticated 2026-11-28 01:20–01:41Z)

**Code lineage**
* Successor `7d160b1c7c5a02db834793954924c4ff240a0690` descends from 2ac9117 and 9f65e7f (`git merge-base --is-ancestor`).
* CI run 46078107204 concluded `success` on head 7d160b1 (`gh run view`).
* Checkpoint 45761b7 is on `origin/research/root-cause-20261126`.
* Ledger key `stage7_universe_bitemporal_lineage_20261127` was read.

**Production and deploy state.** Production `current` is 9f65e7f, `CBB_EXPECTED_COMMIT` is 9f65e7f and `ROLLBACK_RELEASE` is cae2a02.

The scheduled guarded deploy has **not run yet**:
* `/root/cbb-s7-deploy.state` is absent (no terminal state) and no CANCEL file exists.
* `/root/cbb-s7-deploy.sh` sha256 is 5aceb74e…dfcdf, unchanged.
* Timer `-w1` is due 2026-11-28 04:50Z and `-w2` 16:50Z.
* The staged tree is `releases/7d160b1….tmp`. No other `.tmp` exists.
* Free disk was 38.32e9 B at 01:41Z. The gate is 41.34e9 B, so it depends on the 04:45Z retention pass.

Stage 08 neither touched nor duplicated this procedure. It staged nothing and left the Droplet with no new files: the remote work was read-only `rsync`/`scp` at ionice idle.

**Source evidence**
* 370 historical forecast artifacts were mirrored read-only (`data/qa/large/stage08_mirror`, 1.6 GB).
* 343 match their recorded `pmfs_sha256`.
* The other 27 are the pre-11-16 snapshots that went through the logged lossless long→compact migration of 2026-11-16 (`ops/retention/pmf_compact_migration.jsonl`, `storage_retention.jsonl`). In each case current sha = logged `new_sha256` and logged original = the snapshot's recorded sha.
* There are 0 unexplained mismatches (`results/benchmark_sources.csv`).

**Scoring reproduces production exactly.** Recomputed model probabilities equal the frozen evaluations' `model_p` (max |Δ| = 0) for:
* 2,330 player contracts;
* 64,949 game contracts.

The de-vigged market probabilities equal the evaluations' `fair_p`, also max |Δ| = 0. The persisted PMF means equal the recomputed PMF means.

## 2. Administrative corrections

**C-S8-1. Stage 07 gate arithmetic.** The ledger's 19 Stage 07 gates are:
* **14 unqualified PASS**;
* **4 qualified PASS**:
  * HISTORICAL_INFORMATION_AVAILABILITY_VERIFIED (2026-27 retained window);
  * SILENT_MODEL_FALLBACKS_ELIMINATED (engineering);
  * HISTORICAL_FIT_LINEAGE_VERIFIED (72 classified);
  * STAGE_08_BENCHMARK_READY (engineering);
* **1 BLOCKED**: PARAMETER_DIRECTIVE_PRESERVED.

14 + 4 + 1 = 19. The handoff text's "17 unqualified, 2 qualified, 1 blocked" is 20 and was wrong. No test was rerun. The correction is recorded in the Stage 08 ledger key; the Stage 07 entry is not rewritten.

**C-S8-2. PARAMETER_DIRECTIVE_PRESERVED, formerly BLOCKED, is now resolved.** The three directives arrived inside the consolidated instrument, and its full text is in governance §6. The 55-group reconciliation is §9 below.

**C-S8-3. Stage 07 market coverage was over-counted.**
* "514 games with player-market observations" counted the per-game `GAME_FROZEN` marker records (540 games).
* The true figures are **117 games with player `MARKET_EVALUATION` records; 99 complete by the cutoff; 3,036 evaluations; 598 player-games**.
* The 3,036 / 598 figures were right.

Likewise, "349 games with game-market observations" counted marker and stale records. **285 complete games** have a game evaluation, retained quote captures and a decision game PMF.

## 3. Pre-declared evaluation definitions (fixed before any table was produced)

**Populations.** None is pooled with another.
* **A. HISTORICAL_PRODUCTION**: every genuine pregame PMF as persisted.
* **B. CORRECTED_CANDIDATE_REPLAY**: not executed, see §7.
* **C. MARKET_OVERLAP**: matched contracts.
* **D. DIAGNOSTIC_RECONSTRUCTION**: lockbox seasons, pre-11-02 games, selection-exposed dates and time-attributed engine ids, which are labelled only.

**Vintages.** One row per (game, player, stat) per vintage. They are never pooled.
* **LAST_PREGAME (primary)**: the latest PMF with `prediction_as_of < tip`. The choice does not depend on the outcome.
* **INITIAL**: the earliest INITIAL_0700 / REFIT_RETRY_0800 snapshot.
* **REFRESH_LAST**: the latest ROTOWIRE_* snapshot.
* **CUTOFF_TICK**: the latest OFFICIAL_TICK.

**Player metrics.** These score the participation-conditional PMF on played rows only (a box line with minutes > 0). The unconditional mixture (1 − p)·δ0 + p·PMF is scored separately on every forecast player-game, including DNPs and players with no box line.
* Bias = mean(forecast − actual).
* CRPS = Σₖ (F(k) − 1{y ≤ k})².
* Log score = −ln p(y), with a 1e-12 floor.
* PIT is randomized and seeded per artifact.
* Intervals are central PMF quantile intervals.

**Market comparisons**
* Market probabilities are same-book two-way no-vig, non-push conditional. Player props are void on DNP. Game markets are OT-inclusive. Pushes are excluded.
* The model is held at the **decision forecast**, the PMF the production evaluation priced. The market is read at three horizons:
  * **OPEN**: the first retained observation. The model holds later information here.
  * **DECISION**: the same observation as the model.
  * **CLOSE**: the last observation before tip. The market holds later information here.
* Primary units:
  * player: the evaluation's own (book, line) contract, one per player-stat-game;
  * game: per game × main market, the decision consensus line (the most-quoted point, fair averaged across books).
* All book-line contracts are reported as secondary.

**Inference**
* Cluster bootstrap over games, B = 1000, seed 20261128. Games nest players, stats, alternative lines, books and repeated vintages.
* Date-block bootstrap for the game-distribution bias.
* n_eff = n·var_iid / var_cluster.
* Holm over the pre-declared family: every DECISION-horizon primary category with n ≥ 30, 11 tests.
* n < 30 is reported as NOT_EVALUABLE.

**No noninferiority claim.** No margin was registered before these populations were exposed (the 11-21 / 11-22 audits examined overlapping data).

## 4. Coverage and reconciliation (cutoff 2026-11-27T20:40:23Z, state S20261127T201352Z-8eeab0c20aef)

**Games**

| unit | count | note |
|---|---|---|
| tipped by cutoff | 1,485 | DI_VS_DI 1,164, DI_VS_NON_DI 321 |
| complete | 1,443 | DI 1,134. Excluded: 25 missing result, 13 in progress, 4 postponed / cancelled |
| exact retained pregame state | 1,430 complete (DI 1,127) | 13 tipped before 2026-11-02 18:45Z |
| with a genuine pregame player PMF | 811 complete | 810 DI + 1 DI_VS_NON_DI |
| complete DI without a forecast | 324 | 300 before Droplet forecasts (11-01…11-08), 24 never slated |
| with a game PMF | 331 | all DI, from 11-19, all joint 1.0.0 (pre-C4) |
| player-market games (C) | 99 (97 settled primary contracts) | §2 C-S8-3 |
| game-market games (C) | 285 | |

**Players**
* Player box lines: **32,757** in complete games. 32,531 played and 226 are DNP lines.
* Unresolved identity: the unit is **box lines**, over all 1,443 complete games, both universes. 4,112 lines are UNRESOLVED_NO_MATCH and 3 UNRESOLVED_AMBIGUOUS, out of 32,757 box lines.
* Within the 811 forecast games, the coverage gaps are:
  * 494 played but were not forecast (suppressed in the pool);
  * 44 were unexpected participants: 32 unresolved and 12 resolved without pregame membership;
  * 6,214 were forecast but did not play.
* The Stage 07 universe table (`universe_players.parquet`) carries every player-game, and these gaps are not dropped.

**PMFs**
* The **1,014,192** pregame PMF rows split into **989,604** in complete games and 24,588 in incomplete games.
* 989,604 = **275,964** unique (game, player, stat) × an average of **3.59 vintages**, over **22,997** player-games × **12** stats.
* Vintages per player-game-stat:

| vintages | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| player-game-stats | 51,768 | 30,036 | 43,776 | 59,976 | 45,984 | 34,356 | 10,068 |

* The PMFs come from **345 artifacts**, with 5,337 unique players.
* By window: OFFICIAL_TICK 271,596; INITIAL_0700 204,660; ROTOWIRE_1000/1300/1600/1800 474,456; REFIT_RETRY_0800 38,892.

**Model and lineage labels**
* The frozen opening model (`cbb-v2-jm1-final-shadow-1.0.0`, the tick-path defect, 11-08 → 11-24) accounts for 217,620 rows. In LAST_PREGAME it is **205,320 of 275,964 rows (74%)**. This is the dominant lineage of genuine historical production.
* Engine_id is recorded on 211,392 rows and time-attributed (`prod.engine.legacy_engine`) on 778,212.
* The Stage 05 defective INITIAL rows are labelled: 2,628 PMF rows, 219 player-games in complete games.
* There are 72 AMBIGUOUS / UNVERIFIED code commits (Stage 07). They concern code attribution only. Model version and PMFs are recorded on every row.

**Engine contract.** The supported stats are exactly PTS, REB, AST, FG3M, STL, BLK, TOV, PRA, PR, PA, RA and STOCKS. All 12 are present on all 22,997 player-games.

## 5. Population A: genuine historical production (LAST_PREGAME, played rows, 16,783 player-games / 811 games per stat)

`results/player_distribution_metrics.csv` covers all vintages × model kinds, with CIs and n_eff.

| stat | forecast mean | actual | bias [95% CI] | MAE | RMSE | CRPS | log | PIT < .05 | PIT > .95 | 80% / 90% cov |
|---|---|---|---|---|---|---|---|---|---|---|
| PTS | 7.29 | 7.08 | **+0.205 [0.133, 0.270]** | 4.06 | 5.27 | 2.771 | 2.761 | **0.097** | 0.053 | 0.830 / 0.917 |
| REB | 3.01 | 2.98 | +0.035 [0.003, 0.067] | 1.69 | 2.22 | 1.154 | 1.997 | 0.057 | 0.054 | 0.889 / 0.951 |
| AST | 1.34 | 1.31 | +0.025 [0.003, 0.044] | 0.98 | 1.35 | 0.630 | 1.360 | 0.051 | 0.048 | 0.936 / 0.969 |
| FG3M | 0.77 | 0.75 | +0.024 [0.009, 0.037] | 0.70 | 1.01 | 0.427 | 1.014 | 0.051 | 0.047 | 0.946 / 0.972 |
| STL | 0.64 | 0.66 | −0.020 [−0.034, −0.007] | 0.66 | 0.88 | 0.404 | 1.029 | 0.048 | 0.055 | 0.942 / 0.969 |
| BLK | 0.32 | 0.32 | +0.002 [−0.008, 0.013] | 0.40 | 0.62 | 0.225 | 0.639 | 0.051 | 0.052 | 0.953 / 0.977 |
| TOV | 1.05 | 1.12 | **−0.069 [−0.088, −0.052]** | 0.84 | 1.12 | 0.552 | 1.310 | 0.049 | 0.059 | 0.932 / 0.964 |
| PRA | 11.64 | 11.37 | +0.265 [0.175, 0.356] | 5.31 | 6.78 | 3.657 | 3.137 | 0.080 | 0.049 | 0.817 / 0.914 |
| PR | 10.30 | 10.06 | +0.240 [0.160, 0.319] | 4.96 | 6.36 | 3.412 | 3.054 | 0.083 | 0.051 | 0.822 / 0.914 |
| PA | 8.63 | 8.40 | +0.230 [0.148, 0.307] | 4.40 | 5.68 | 3.010 | 2.885 | 0.089 | 0.050 | 0.824 / 0.917 |
| RA | 4.35 | 4.29 | +0.059 [0.021, 0.097] | 2.16 | 2.79 | 1.481 | 2.261 | 0.056 | 0.051 | 0.876 / 0.944 |
| STOCKS | 0.96 | 0.98 | −0.017 [−0.036, −0.000] | 0.83 | 1.11 | 0.536 | 1.265 | 0.051 | 0.056 | 0.934 / 0.967 |

**Distributional reading**
* Central coverage is close to nominal, and somewhat wide for the small-count stats: AST, FG3M, STL and BLK have 90% coverage of 0.97–0.98.
* **The only material tail defect is the PTS-family lower tail: 8–10% of outcomes fall below the 5% PIT.**
* The unconditional scores (`player_unconditional_metrics.csv`) give the same ranking. The PTS lower tail is 6.6% there, because the DNP mass is right in aggregate.

**Vintages.** CRPS for PTS is 2.768 at CUTOFF, 2.787 at REFRESH and 2.796 at INITIAL. Later vintages are slightly better, but the differences are an order of magnitude smaller than the subgroup effects.

**Model kind** (DAILY_WALK_FORWARD n = 4,187 vs FROZEN n = 12,596 per stat). The two cover different dates, so the comparison is descriptive only:
* PTS CRPS 2.745 vs 2.779;
* TOV bias −0.030 vs −0.083;
* REB bias +0.119 vs +0.007.

**Participation** (22,997 player-games, played rate 0.730, mean p_play 0.730)
* Brier 0.109 against a climatology of 0.197; log loss 0.357.
* **The reliability is poor in both tails:**

| p_play bin | realized play rate | 95% CI |
|---|---|---|
| < 0.02 | 0.082 | [0.061, 0.105] |
| 0.02–0.05 | 0.128 | |
| 0.05–0.10 | 0.191 | |
| 0.10–0.20 | 0.252 | |
| 0.5–0.6 | 0.437 | |
| 0.6–0.7 | 0.497 | |
| 0.7–0.8 | 0.664 | |

* Availability-status groups other than NO_REPORT have n ≤ 47: NOT_EVALUABLE for subgroup claims.

**Minutes** (played, 16,783). Only min_mu and min_sigma are persisted. The full minutes PMF is not, so distributional minutes scoring is limited to a normal reference.
* Bias +0.01 [−0.10, 0.10]; MAE 5.15; RMSE 6.55; z sd 1.03; normal-reference 90% coverage 0.90.
* Diagnostic, realized role: starters −1.03 min, bench +0.95 min.

**Final engine** (`…+DEV1+CTX1+PI2+PS2`, genuine prospective, 11-26 22:48Z → cutoff). Only **384 player-games over 20 games**. Bias is within CI of 0 for every stat; the PTS lower tail is 5.2% and PTS CRPS 2.98 on a different slate. **This is too small for any category claim** (`final_engine_prospective_metrics.csv`).

**Games** (331 DI games, joint 1.0.0, LAST_PREGAME; `game_distribution_metrics.json`)

| target | bias | MAE | RMSE | CRPS | log | 90% cov | model sd / residual sd |
|---|---|---|---|---|---|---|---|
| home | −0.40 | 8.71 | 11.04 | 6.19 | 3.82 | 0.903 | 10.71 / 11.05 |
| away | −0.41 | 8.64 | 10.89 | 6.11 | 3.81 | 0.906 | 10.16 / 10.90 |
| total | −0.81 (date-block CI −2.9, 0.9) | 13.62 | 17.80 | 9.85 | 4.29 | 0.900 | 17.29 / 17.80 |
| **margin** | +0.01 | 10.35 | 12.81 | 7.29 | 3.96 | **0.858** | **11.70 / 12.83** |

* Moneyline: Brier 0.201 and log loss 0.572.
* Reliability is overconfident over 0.5–0.9: forecast 0.55 / 0.65 / 0.75 / 0.85 realizes 0.48 / 0.54 / 0.70 / 0.64.
* Joint structure:
  * corr(home, away): model 0.371 vs realized residual 0.316;
  * ρ(margin, total): model 0.056 vs realized 0.015.
* INITIAL (302 games) gives the same picture: margin 90% coverage 0.848.
* No DI_VS_NON_DI game has a game PMF, so that class is NOT_EVALUABLE.

## 6. Population C: matched market comparison (`results/market_comparison.csv`, `market_reliability.csv`)

The paired Δ is model − market. Negative favours the model. Rows below are DECISION, primary unit.

| category | n (clusters) | Brier model / market | ΔBrier [95% CI] | Holm p | Δlog [95% CI] | verdict |
|---|---|---|---|---|---|---|
| player ALL | 2,299 (97) | 0.2519 / 0.2468 | +0.0051 [−0.0018, 0.0129] | — | +0.013 [−0.002, 0.030] | inconclusive |
| PTS | 554 (97) | 0.2614 / 0.2478 | **+0.0135 [0.0021, 0.0270]** | 0.29 | **+0.033 [0.007, 0.062]** | inferior (unadjusted only) |
| REB | 499 | 0.2474 / 0.2461 | +0.0012 [−0.0100, 0.0132] | 1 | | inconclusive |
| AST | 398 | 0.2474 / 0.2437 | +0.0037 [−0.0089, 0.0147] | 1 | | inconclusive |
| FG3M | 372 | 0.2452 / 0.2463 | −0.0011 [−0.0135, 0.0105] | 1 | | inconclusive |
| PRA | 420 | 0.2561 / 0.2490 | +0.0071 [−0.0057, 0.0216] | 1 | | inconclusive |
| PR | 56 (28) | 0.2445 / 0.2528 | −0.0083 [−0.0441, 0.0298] | 1 | | inconclusive |
| STL, BLK, TOV, PA, RA, STOCKS | 0 | | | | | NOT_EVALUABLE (no primary contracts) |
| game ALL main | 1,415 (285) | 0.2517 / 0.2427 | **+0.0090 [0.0013, 0.0167]** | — | +0.016 [−0.001, 0.033] | inferior |
| SPREAD | 285 | 0.2685 / 0.2494 | **+0.0191 [0.0076, 0.0300]** | **0.009** | **+0.040 [0.016, 0.063]** | **inferior** |
| AWAY_TEAM_TOTAL | 285 | 0.2653 / 0.2504 | +0.0149 [0.0048, 0.0257] | 0.067 | +0.032 [0.011, 0.055] | inferior (unadjusted only) |
| GAME_TOTAL | 285 | 0.2595 / 0.2502 | +0.0092 [−0.0000, 0.0192] | 0.50 | | inconclusive |
| HOME_TEAM_TOTAL | 285 | 0.2574 / 0.2497 | +0.0077 [−0.0023, 0.0176] | 0.95 | | inconclusive |
| MONEYLINE | 275 | 0.2062 / 0.2128 | −0.0065 [−0.0228, 0.0112] | 1 | −0.027 [−0.066, 0.015] | inconclusive |

**OPEN and CLOSE**
* For players, OPEN and CLOSE match DECISION to within 0.001 Brier. Player quotes stop at about the cutoff: the last observation is at median tip − 74 min, so a true player closing line is **not retained**.
* For games, CLOSE is at median tip − 14 min and gives the same verdicts.
* The game MONEYLINE beats the *opening* market (ΔBrier −0.019 [−0.037, −0.000]), but the model holds later information there. This is not a credible advantage.

**Secondary scope (all book-line contracts)** agrees:
* player PTS +0.019 (unadjusted);
* game SPREAD +0.018 and GAME_TOTAL +0.011 inferior; ALT_* inconclusive.
* All-book MONEYLINE Δlog is −0.056 [−0.100, −0.006] but ΔBrier is inconclusive: a mixed, unadjusted signal.

**Market-relative calibration (decision, primary)**

| market | forecaster | calibration slope | ≥ 60% side: mean p → hit | ≥ 65% side: mean p → hit |
|---|---|---|---|---|
| player | model | **0.38** | n = 1,281: 0.684 → 0.585 [0.549, 0.616] | n = 798: 0.719 → 0.612 [0.572, 0.651] |
| player | market | 0.87 | | |
| game | model | **0.42** | n = 272 at ≥ 65%: 0.764 → 0.625 | |
| game | market | 1.03 | n = 141 at ≥ 65%: 0.737 → 0.759 | |

The model's departures from the market at the listed lines are about 2.5× too large. Its high-probability tiers realize well below nominal. They should not be read as evidence of advantage.

**Direction.** 1,780 of 2,299 primary contracts lean UNDER. Both directions are inconclusive: OVER +0.0044, UNDER +0.0053.

## 7. Population B: corrected candidate replay — NOT EXECUTED (BLOCKED, with the outstanding population preserved)

**Outstanding population.** It is the 1,127 complete DI_VS_DI games with an exact retained pregame state (`../2026-11-27-stage7/results/universe_games.csv`, information_state = EXACT_RETAINED_STATE).
* Historically available daily fits exist for 2026-11-01, 11-08, 11-10 and 11-12 → 11-27: 19 versions in `walkforward/.../models`.
* Games on 11-02 … 11-07, 11-09 and 11-11 would need the previous in-force model, or causally reconstructed fits.

**Feasibility, measured from production receipts rather than a new run**
* One player slate forecast costs about 14.7 CPU-min (the 11-27 morning-forecast unit) at a peak of about 2.8 GB.
* The CTX1 ledger must first be rebuilt under the PS2 incumbent (C3): about +26 CPU-min.
* Run scratch is about 0.54 GB per run.
* At one vintage per slate date, 26 dates come to about 7–8 CPU-h and ≈14 GB of transient scratch.

**Why it was not run in Stage 08**
1. On the Droplet, 14 GB of scratch would push free disk further below the 41.34e9 B deploy gate (38.3e9 free at 01:41Z) and could block the scheduled 7d160b1 activation. The 2.8 GB peaks would also compete with MemAvailable ≥ 4 GiB. The directive forbids both.
2. Locally, the replay needs a validated off-Droplet execution of `prod.run.daily` under `replay_contract`. That means copying the history DB (0.67 GB), the walk-forward store (1.1 GB), the feature cache (0.79 GB), one state version per date and the curated CSVs, and the path has never been certified on the Mac. Building and certifying that path is engineering, not benchmarking.
3. **Every 2026-27 date ≤ 11-26 is selection-exposed** for RA2 / TOVT1 / DEV1 / CTX1 / PI2 / PS2 (Stage 07 D). A final-engine replay there would be D, not B. The only clean window (after 11-26 22:48Z) is already covered by genuine prospective forecasts: the final-engine subset of A, 384 player-games.

**Reporting consequence.** Question 2 is answered only by that small prospective subset. The replay remains an explicit open requirement, assigned to the Stage 18 champion-validation harness with the feasibility numbers above.

## 8. Root-cause priority matrix (evidence-ranked; descriptive associations are labelled as such)

| rank | weakness | demonstrated evidence (denominator) | cause class | stage |
|---|---|---|---|---|
| 1 | **PTS PMF lacks zero / low-count mass** | P(PTS = 0 \| play): BENCH_9PLUS forecast 0.224 vs realized 0.412 (n = 5,961); ROTATION 0.082 vs 0.178 (4,008); TOP5 0.016 vs 0.052 (6,814). PTS lower 5% PIT 9.7% (16,783); PA / PR / PRA 8–9%. REB / AST zero mass is nearly right (0.31 vs 0.35, 0.59 vs 0.66) | PMF shape / tail (PTSC1 compound, conditional φ) | **12** (P31, P29, P10) |
| 2 | **Rotation compression in PTS / PRA means** | Prediction-time role (n): BENCH +0.70 PTS [0.60, 0.79] (5,961), ROTATION +0.38 [0.24, 0.52] (4,008), TOP5 −0.33 [−0.49, −0.19] (6,814). By projected minutes the means are biased (< 20 min +0.65…+0.71, 20+ −0.35…−0.43) **while projected minutes are unbiased in those bands** (15.17 vs 14.85; 24.92 vs 25.35) | conversion / opportunity (β_PTS ≈ 1.30 Jensen, S4; daily GLM double correction, S3), **not** the minutes mean | **11** (P27), with 12 |
| 3 | **Model overconfidence against the market at listed lines** | Calibration slope 0.38 player / 0.42 game (market 0.87 / 1.03); ≥ 65% tier 0.719 → 0.612 (798); PTS inferior unadjusted (554); SPREAD inferior, Holm p 0.009 (285) | location noise relative to market information; downstream of ranks 1, 2 and 5 | 12 / 17, then 18 (selection) |
| 4 | **Participation tail miscalibration** | p_play < 0.10 realizes 8–19% (2,308 player-games); 0.5–0.7 realizes 44–50% (1,665) | participation model (Hook A); tail clipping | **09** (P1, P2) |
| 5 | **Game margin under-dispersion; ML overconfidence** | margin 90% coverage 0.858, model sd 11.70 vs residual 12.83 (331); ML 0.55–0.85 bins over-predict | variance parameters fitted on 1.0.0 means (G11 UNK); team-strength error not separable | **17** (G11), **14 / 15** (G4, G1) |
| 6 | Cold-start PTS over-projection | COLD_EARLY_SEASON +0.56 (2,761), COLD_NO_HISTORY +0.53 (114) vs WARM +0.14 (12,536) | prior strength / K = 31 / PI2 clip | **10** (P12, P18, P20) |
| 7 | TOV under-projection | −0.069 [−0.088, −0.052] overall, mostly in the frozen-model era (DAILY −0.030, CI includes 0) | lineage (frozen ticks); TOVT1 compatibility | 10 / 11 (P16) |
| 8 | Realized-starter minutes under-projection | starters −1.03 min, bench +0.95 (diagnostic only, realized labels) | rotation / joint minutes (Hooks B, C) | **09** |

**Not supported by Stage 08 evidence**
* Dependence error (Stage 13): the combo stats inherit the PTS tail, but no combo-specific defect is visible.
* Availability-bridge error (Stage 16): not separable at this sample.
* Pace error: total bias −0.81 with a date-block CI that includes 0.

## 9. Parameter reconciliation (55 groups) and estimation priorities

`results/parameter_reconciliation.csv` maps each Stage 06 group to its Part III class, materiality, Part III §5 stage, required §4 disposition and the Stage 08 evidence. The Stage 06 trace is cited, not repeated.

**Material unsupported groups still requiring disposition A–D:**
* heuristics: P2, P5, P7, P10, P18, P20, P26, G10, G13;
* unknown provenance: G4, G11;
* unused: G9 (C or A), P30 (C);
* fitted parameters that need re-validation: P1, P3, P12, P16, P22, P27, P29, P31, G1, G14.

An unfitted inventory is not completion. None of these is closed by Stage 08.

**Highest-priority estimation targets (Question 9):**
1. P31 / P29: PTS zero and low-count mass, conditional dispersion (Stage 12).
2. P27: daily structural GLM low-minute conversion, the β_PTS Jensen term (Stage 11).
3. G11: game variance on the corrected means (Stage 17).
4. P1 / P2: participation tails (Stage 09).
5. P3 / P5: joint minutes and clipping (Stage 09).
6. P12 / P18 / P20: cold-start prior strength, K = 31 and the PI2 clip (Stage 10).
7. G4 / G1: rating hyperparameters (Stage 14 / 15).
8. G10 / G9: bridge (Stage 16).

## 10. The frozen benchmark that Stages 09–17 must improve on (Question 10)

The manifest is `results/BENCHMARK_MANIFEST.json`. Every later stage reports its candidate against these exact numbers, on the same population definitions, with the same paired cluster bootstrap.
* **Player (A, LAST_PREGAME, played, 16,783 player-games per stat)**:
  * CRPS PTS 2.771, REB 1.154, AST 0.630, FG3M 0.427, STL 0.404, BLK 0.225, TOV 0.552, PRA 3.657, PR 3.412, PA 3.010, RA 1.481, STOCKS 0.536;
  * log scores in §5;
  * PTS lower-tail PIT 0.097;
  * role-band PTS biases +0.70 / +0.38 / −0.33.
* **Participation:** Brier 0.1091 and log loss 0.3568 (22,997).
* **Minutes:** MAE 5.153 and RMSE 6.547.
* **Game (331):**
  * CRPS: margin 7.291, total 9.845, home 6.194, away 6.106;
  * margin 90% coverage 0.858;
  * ML Brier 0.201.
* **Market (C, DECISION):**
  * player ΔBrier +0.0051 (2,299), PTS +0.0135;
  * game main ΔBrier +0.0090 (1,415), SPREAD +0.0191;
  * calibration slopes 0.38 / 0.42.

**Protection rules for later stages**
1. **No later stage may select or tune parameters on these populations and then report them as out-of-sample.** Any parameter estimated in Stages 09–17 uses training data with cutoff ≤ its fit date. It is then validated on games after its declaration (prospective) or on lockbox seasons (labelled D).
2. **Protected prospective window.** Every complete game after the 7d160b1 activation through the Stage 18 decision date is reserved for Stage 18 champion selection. It is not read by Stages 09–17 for selection.
3. **Noninferiority margins for Stage 18** must be declared in the governance record before that window is scored.

## 11. Answers to the required questions

1. **Genuine historical production.** It is a well-calibrated marginal forecaster in aggregate, with near-nominal central coverage. It has two material defects: a PTS-family zero / low-tail deficit and role-dependent PTS bias. Participation is mis-calibrated in its tails. Most of the history (74% of primary rows) came from the frozen opening model through the tick-path defect, and it is labelled as such.
2. **Corrected incumbent under legitimate chronological reconstruction.** **Not established.** The replay was not executed (§7). The 384-player-game final-engine prospective subset shows no significant bias and a PTS lower tail of 5.2%, which is insufficient for any claim.
3. **Player categories credibly outperforming the market:** **none.**
4. **Game categories credibly outperforming the market:** **none.** The opening-market moneyline result is not credible, because of the information asymmetry.
5. **Inferior:** game SPREAD (Holm-significant). Inferior unadjusted only: player PTS, game AWAY_TEAM_TOTAL and game main markets overall.
6. **Inconclusive:** player ALL, REB, AST, FG3M, PRA and PR; game GAME_TOTAL, HOME_TEAM_TOTAL, MONEYLINE and all ALT_* markets. **NOT_EVALUABLE:** STL, BLK, TOV, PA, RA and STOCKS markets (no contracts); DI_VS_NON_DI games; availability-status subgroups.
7. **Principal weaknesses by stat and role:**
   * PTS / PA / PR / PRA zero mass and lower tail, strongest for bench and rotation players;
   * PTS over-projection for bench / rotation and under-projection for top-5 roles;
   * TOV under-projection (frozen era);
   * cold-start over-projection.
8. **Evidence for each repair:** §8. Each row lists its denominator. Ranks 1, 2, 4 and 5 are directly measured on prediction-time groups. Rank 3 is a market-relative measurement. Rank 8 uses realized labels and is diagnostic only.
9. **Highest-priority parameters:** §9.
10. **Benchmark:** §10.

## 12. Acceptance gates

| gate | status | basis |
|---|---|---|
| CONSOLIDATED_GOVERNANCE_PRESERVED | **PASS** | governance §6 holds the full verbatim text; §4 / Stage 07 BLOCKED resolved (C-S8-2) |
| PARAMETER_INVENTORY_RECONCILED | **PASS** | 55 / 55 groups mapped to Part III classes, stages and dispositions; dispositions themselves remain open by design (Stages 09–17) |
| BENCHMARK_LINEAGE_AUTHENTICATED | **PASS** | §1: ancestry, CI, checkpoint, 370 / 370 sources hash-authenticated, exact reproduction of 67,279 frozen model / market probabilities |
| COMPLETE_GAME_UNIVERSE_ACCOUNTED_FOR | **PASS** | §4; 1,485 games each in one class; market counts corrected (C-S8-3) |
| COMPLETE_PLAYER_UNIVERSE_ACCOUNTED_FOR | **PASS** | §4; 32,757 box lines; 4,115 unresolved lines counted with their unit; 494 / 44 / 6,214 coverage gaps kept |
| FORECAST_VINTAGES_RECONCILED | **PASS** | 989,604 = 275,964 × 3.59; 4 vintages evaluated separately; no outcome-dependent choice |
| PROSPECTIVE_AND_REPLAY_POPULATIONS_SEPARATED | **PASS** | A, C and D reported separately; B not executed and never imputed |
| HISTORICAL_INFORMATION_LIMITATIONS_QUANTIFIED | **PASS** | frozen-model share, time-attributed engines, Stage 05 defects, pre-C4 game PMFs, no player closing line, minutes PMF not persisted |
| MARKET_COMPARISON_MATCHING_VERIFIED | **PASS** | same book, line, side and event; recomputation equals the frozen evaluation exactly; horizons separated; DNP void and pushes excluded |
| PLAYER_DISTRIBUTION_METRICS_COMPLETE | **PASS (qualified)** | 12 stats × 4 vintages, all metrics; minutes scored on two persisted moments only (no minutes PMF persisted); starting labels exist only as realized (diagnostic) |
| GAME_DISTRIBUTION_METRICS_COMPLETE | **PASS (qualified)** | home / away / total / margin / ML / spreads / team totals / alts; DI_VS_NON_DI NOT_EVALUABLE (no game PMFs) |
| ROLE_STAT_ANALYSIS_COMPLETE | **PASS** | `role_stat_metrics.csv`: 11 grouping variables × 12 stats, basis labelled, n < 30 NOT_EVALUABLE |
| CALIBRATION_ANALYSIS_COMPLETE | **PASS** | PIT deciles and tails, participation and ML reliability with CIs, market reliability, CITL, slope, 60% / 65% tiers |
| DEPENDENCE_AWARE_INFERENCE_COMPLETE | **PASS** | game-cluster bootstrap (nests players, stats, lines, books, vintages), date-block sensitivity, n_eff, Holm. Team-level two-way clustering not applied (documented) |
| ROOT_CAUSE_PRIORITIES_EVIDENCE_SUPPORTED | **PASS** | §8, each with its denominator; diagnostic rows labelled |
| PARAMETER_ESTIMATION_PRIORITIES_ESTABLISHED | **PASS** | §9 |
| BENCHMARK_MANIFEST_FROZEN | **PASS** | `results/BENCHMARK_MANIFEST.json` (sources, scripts, results and intermediates hashed) |
| RESOURCE_SAFETY_VERIFIED | **PASS** | §13 |
| STAGE_09_READY | **PASS (qualified)** | the benchmark and protection rules are frozen. Population B is BLOCKED and carried to Stage 18. The 7d160b1 activation is pending its scheduled window |

**Tally.** 16 unqualified PASS + 3 qualified PASS (PLAYER_DISTRIBUTION_METRICS_COMPLETE, GAME_DISTRIBUTION_METRICS_COMPLETE, STAGE_09_READY) = 19 gates, with 0 FAIL and 0 BLOCKED. Two open items are stated outright rather than folded into a percentage:
* Population B is a BLOCKED deliverable (§7). Question 2 is therefore answered as *not established*.
* The parameter dispositions remain open by design.

## 13. Resource safety

**Remote**
* Only read-only `rsync` / `scp` (`ionice -c3 nice 19`) and `ls` / `du` / `cat` / `systemctl list-timers`.
* No unit was created, no file was written on the Droplet, and no provider call was made.

**Local**
* One process at a time with every thread variable = 1 and nice 10:
  * extraction: 13 s wall, 1.38 GB peak RSS;
  * market: 135 s;
  * analysis: about 15 s per run.
* Checkpointed: there is one output per source artifact.

**Mirror.** The mirror (1.6 GB) and the intermediates (66 MB) live in gitignored `data/qa/large/stage08_{mirror,work}`.

**Rerun:**
```
python s8_extract.py MIRROR WORK && python s8_market.py MIRROR WORK && python s8_analyze.py WORK results
python s8_parameters.py results && python s8_manifest.py MIRROR WORK results
```
