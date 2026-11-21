# CBB game-market forensics (sections C, D, TEAM_MAPPING) — 2026-11-21

READ-ONLY. Nothing in the repo or on the Droplet was changed. Evidence was copied from the Droplet:
- `data/` holds the evaluations, forecasts, game_quotes and exports;
- `game/db/increment.duckdb` is the promoted state S20261121T174336Z-390f7e1386ce;
- `game/raw/` holds the raw Odds API game_odds / game_event_odds responses.

The scripts in this folder are `build_rows.py`, `team_mapping.py`, `analyze.py`, `early_season.py` and `significance.py`.

## 1. Audit reproduction
**Dataset:** `exports/2026-11-21/CBB_Game_Performance_11_21_2026.xlsx`. This is the cumulative frozen evaluations `ops/evaluations/{2026-11-19,2026-11-20}/game_market_evaluations.jsonl`, graded on promoted-state finals.

| Metric | Production evaluations | Independent re-grade |
|---|---|---|
| Graded | 245 (34 rows of 11-21 pending) | 245 |
| Model Brier | 0.24240 | 0.24240 |
| Market Brier | 0.23765 | 0.23765 |
| UNDER | 23-37, p 0.5819 | 23-37, p 0.5819 |
| OVER | 54-36 | 54-36 |
| HOME_TEAM_TOTAL | 21-29 | 21-29 |
| HOME_TEAM_TOTAL UNDER | 8-17 | 8-17 |

The independent re-grade uses my own settlement code and finals from the `flagship.games` and `team_game_outcomes` tables.

**Effective sample:** these are **50 unique games**, not 245 independent events. There are about 5 rows per game, all on one PMF.

## 2. Reconstruction and TEAM_MAPPING: PASS
- **Probability reconstruction:** for all 279 evaluations (graded and pending), I decoded the exact frozen PMF (forecast_id × game_id; pmf_zlib float32, h_min/a_min, rows × cols, renormalised).
  - The sha256 equals the evaluation's sha256 in 279/279 rows. The production decoder `pmf_of` also verified it for 57 PMFs.
  - I wrote P(side) from first principles, without importing `game.markets`. It covers SPREAD push at integers, totals and team totals, and the ML tie.
  - Max |p_frozen − p_recon| = 3.3e-16. Max |push diff| = 2e-17.
- **Finals orientation:** the home/away ids of the evaluation, the PMF row and `flagship.games` agree in 50/50 games. `team_game_outcomes.is_home` is TRUE for the home row in 50/50, and box points equal game points in 50/50.
- **Raw-response proof:** each of the 279 evaluation quotes was located in the raw Odds API response at its capture.
  - The price matches exactly in 279/279.
  - The outcome name or description names the evaluated team in 278/279.
  - The remaining row is G-9845dd6a63797ef0, the Tulane AWAY_TEAM_TOTAL UNDER 71.5. In that capture both teams had an identical UNDER 71.5 at −105. This is the shared-quote_id ambiguity fixed in 17418da: it is ambiguous only to the checker, and production resolved it from the description.
- **Swapped listings:** 5 provider events list home and away swapped relative to the canonical game, and all 5 are neutral-site. All 25 of their rows are priced to the correct canonical side.
- `team_mapping_rows.csv` holds the row-level proof.

## 3. Residuals (per game, n=50; mean, sd, t)
| Residual | Mean | sd | t |
|---|---|---|---|
| actual_home − model_home | +2.48 | 10.06 | 1.74 |
| actual_away − model_away | +1.27 | 9.78 | 0.92 |
| actual_total − model_total | +3.75 | 15.36 | 1.73 |
| actual_margin − model_margin | +1.21 | 12.55 | 0.68 |
| actual_total − **market** total | **+4.59** | 14.56 | **2.23** |
| actual_margin − market margin | +0.66 | | |
| model_total − market_total | **+0.84** | 3.53 | |

- The model total is **higher** than the market, not lower.
- RMSE of total: model 15.66, market 15.13. RMSE of margin: model 12.49, market 12.47.
- The model's disagreement with the market has **no information**:
  - corr(model − market total, actual − market total) = −0.11;
  - in the totals family, the slope of (actual − line) on (model − line) is +0.13, with a game-clustered 95% CI of [−0.76, 0.87].

**By model-total bucket (per game):**

| Bucket | Games | rT (actual − model) | Market residual | Model − market | Verdict |
|---|---|---|---|---|---|
| <135 | 2 | −9.6 | | | |
| 135-145 | 15 | +10.1 (t 2.8) | +9.7 (t 2.9) | −0.4 | Outcome shock shared by the market, not a model-specific defect |
| 145-155 | 17 | +3.4 | | | |
| 155+ | 16 | −0.2 | | | No high-total problem, so no high-total fix is warranted (D) |

**By margin bucket:** no margin bias (all |t| < 1.1).

**Totals-family rows by side:**

| Side | Rows | Games | model − line | actual − line | actual − model |
|---|---|---|---|---|---|
| OVER | 90 | 42 | +2.12 | +1.99 | −0.13 (calibrated) |
| UNDER | 60 | 34 | −2.61 | +3.83 | +6.44 (t 3.9) |

On GAME_TOTAL alone, UNDER-picked games ran +7.2 over the line and OVER-picked games +3.3. The difference has Welch t 0.8, so it is not significant: both sets of games ran over the line.

## 4. Component tests
- **Variance (total):** no deficiency.
  - The PMF sd_total averages 17.34 against a realised RMS of 15.66, so z-sd is 0.90 and the PMF is slightly wide, not narrow. The chi² p-value is 0.79.
  - The PIT of the total is shifted high (mean 0.569, KS p 0.009). That is a location shift, not dispersion.
- **Variance (margin):**
  - z-sd is 1.07 here.
  - In the 2026-27 season-to-date replay (854 games) it is 1.08.
  - In history it is about 1.05-1.12 for teams with 4-5 games played. This suggests margin under-dispersion early in the season; it is PLAUSIBLE.
  - It affects SPREAD and ML, not the UNDER results.
- **rho_mt:** the PMF uses 0.056; the realised corr(rT, rM) is 0.03. OK.
- **Overtime:** handling is consistent.
  - The ratings fit OT-inclusive points on per-40 possessions, the PMF carries OT mass and is moment-matched to those targets, and settlement is OT-inclusive.
  - 4 of 50 games went to OT (2027 replay rate 4.7%, history 4-6%), which is consistent with the base rate.
  - Those 4 games had rT averaging +22.6, and the **9 UNDER rows on them went 0-9**.
  - Regulation-only UNDER rows went 23-28 against p 0.589.
- **Late-game foul / endgame kernel:** the PMF mean equals the top-down target exactly (difference 0.00) by moment matching. The kernel reshapes the distribution without moving the mean, and no mean defect is attributable to it.
- **Player lineup adjustment:** availability_delta = 0 in all 50 games (gamma × 0), so it has zero contribution. The S_home/S_away terms do not enter the mean (beta = 0).
- **Top-down team prior (ratings):**
  - My PIT replay reproduces the frozen means to 0.0002.
  - 2026-27 season to date (n=854): total bias +1.57 (t 2.6), margin −0.10. For games with 4-5 played, the total bias is +1.76 (t 1.0).
  - History 2023-24..2025-26: total bias for games with 4-5 played is −0.14 (n=1122), and +0.1 to +1.1 over full seasons.
  - So there is no structural early-season under-prediction of totals. The small league drift (+1.6) explains about 0.03 of the 0.20 UNDER gap: UNDER p moves 0.582 → 0.551 against a hit rate of 0.383.

## 5. Why UNDER looks overconfident (root cause)
The best offer is the max EV over books × lines × sides. So each evaluated side is the one where the model disagrees most with the market. That disagreement carries no information:
- live slope +0.13, CI [−0.76, 0.87];
- historical O/U hit rates on the model side were 50.1% and 49.8%;
- historical O/U Brier was 0.2559 and 0.2574, both worse than a 50/50 forecast.

So every side claims about +0.09 of edge (UNDER 0.095, OVER 0.088) that does not exist. On top of this, the 50-game sample carries a common over-shock: actual − market total was +4.6 (t 2.2), driven by 4 OT games plus a +3.1 regulation shock. That shock moves realised hit rates against the market symmetrically:

| Side | Hit − market fair | Model's claimed edge | Model gap (hit − p) |
|---|---|---|---|
| OVER | +0.129 | +0.088 | +0.040 |
| UNDER | −0.104 | +0.095 | −0.199 |

The UNDER gap's game-clustered CI is [−0.35, −0.05]. UNDER ≈ −0.09 (selection on uninformative disagreement) − 0.10 (sample over-shock, which the market also suffered: −0.104, CI [−0.26, +0.06]).

**Not the cause:**
- total mean bias specific to UNDER (the model total sits above the market);
- total variance (the PMF is slightly wide);
- high totals;
- the 135-145 bucket (the market missed by the same amount);
- team mapping;
- OT or foul modelling;
- the lineup term.

**GAME_UNDER_ROOT_CAUSE = PASS.** The cause is demonstrated, and it is not a GameJointPMF component defect. No OVER/UNDER correction and no high-total fix is justified.

## 6. Proven deficiency and challenger (specified, NOT evaluated, NOT deployed)
**Deficiency:** model-vs-market disagreement on totals and spreads is uninformative. The probability attached to the selected side overstates edge by about 0.09. This is known from Release B validation and is confirmed live.

| | |
|---|---|
| CURRENT | model_p = P_PMF(side) at the line; the side is the argmax EV. |
| CHALLENGER | Market-anchored mean. Before pricing, shift the PMF's total (and margin) location to close + w·(model − close), with w fitted. This is a translation; the variance model and the GameJointPMF are unchanged. |
| DEVELOPMENT | 2023-24, using the offline CBBD /lines fetch. Those lines are **not in the repo**; they must be retrieved. |
| SELECTION | 2024-25. |
| FROZEN/OOS | 2025-26 has no closes (lockbox governance). Use the prospective 2026-27 frozen live evaluations instead: at least 300 games before any decision. |
| PROSPECTIVE_EXPECTATION | Brier and log loss at or below the market's on O/U and ATS. Claimed edge shrinks toward 0. The expected w is about 0 to 0.2 (historical model-side hit ≈ 50%). |

**Secondary, PLAUSIBLE only:**
- the season-level total drift (+1.6, via the lam_league anchor of mu);
- early-season margin under-dispersion (c_m/tau_m).

Both would need the same dev/sel/frozen protocol. Neither explains UNDER.

## 7. Infrastructure finding (for the closing-quote / Board owner)
`game_quotes.as_of` keeps the latest row per (book, market, side, **point**). A line a book has withdrawn therefore stays "offered" until cutoff.
- **42.4%** of graded evaluations (and 40.9% of all evaluations) price a point that is no longer on that book's latest pre-cutoff board.
- Example: Tulane team total UNDER 71.5 from 14:10Z, while williamhill_us showed 70.5 from 15:14Z onward.
- Stale offers carry a higher claimed edge: 0.111 against 0.078 for live offers.
- **Effect:** Board EV and the Game Lines rows are not executable. Calibration grading at the stated line is still valid.
- **Fix:** restrict each (book, market_type) to its latest capture. This is a pricing-input change, so test it before deploying.

## CSVs
- `games_residuals.csv`: per game.
- `graded_rows_residuals.csv`
- `totals_family_rows.csv`
- `team_mapping_rows.csv`
- `replay_2027.csv`
- `replay_hist.csv`
- Text outputs: `analysis.txt`, `early_season.txt`, `significance.txt`.
