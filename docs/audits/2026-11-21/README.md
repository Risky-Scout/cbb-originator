# CBB Originator forensic diagnosis: 2026-11-21 (release a9a3968 live)

Diagnosis + infrastructure remediation only. **No predictive mathematics changed or deployed.** All analysis ran
read-only on copies of production state; the only Droplet action was a read-only query of the latest run's feature DB.
Details: [PLAYER_REPORT.md](PLAYER_REPORT.md) (A, B), [GAME_REPORT.md](GAME_REPORT.md) (C, D, team mapping),
[CLOSE_REPORT.md](CLOSE_REPORT.md) (E). Scripts in `scripts/`, row-level evidence in `results/`.

## Final statuses

| | |
|---|---|
| PLAYER_CALIBRATION_ROOT_CAUSE | **PASS** |
| GAME_UNDER_ROOT_CAUSE | **PASS** |
| TEAM_MAPPING | **PASS** |
| CLOSING_QUOTE_CAPTURE | **PASS** (diagnosed, fixed, tested; needs deploy + live certification) |
| GRADES_NAMING | **PASS** (fixed, tested; needs deploy) |

## PROVEN_BUGS (infrastructure / contract; fixed on `integration/cbb-audit-infra-2026-11-21`, not deployed)
1. **Player close lookup collision** (`market_exports.Observations.contracts`): the one-player de-dup `eligible()` was
   applied to the whole snapshot, so only one player per (book, line, side) survived. All 353/832 (42.4%) player
   NO_CLOSE_QUOTE rows. Recomputed on the stored quotes: 353 -> 0. Stored Performance rows are reused by hash, so the
   history needs a separately authorised rebuild.
2. **Game stale offers** (`game_quotes.as_of`): a point a book had moved off stayed offered until the cutoff, and
   max-EV preferred it. 104/245 (42.4%) frozen game evaluations priced an offer that wasn't on the book's latest
   pre-cutoff board. Fixed: latest capture per (game, book, market_type).
3. **Writer-lock exposure of close capture**: no close was lost yet (7 skipped wakes, all 12:00-12:50Z). But
   availability Phase B holds the global lock 12-20 min at 10/13/16/18 ET, and quote wakes waited up to 581 of
   600 s inside tip-60 windows. Fixed: a close-only game capture under its own narrow lock when the writer lock is
   busy, only when the normal cadence is due and a tip is <= 60 min away, so no extra API calls.
4. **Grades file name**: folder D's Grades workbook grades D-1 but was named for D. Now `CBB_Grades_<D-1>.xlsx`, and
   the receipt carries export_date / graded_slate_date. The legacy misnamed copy is retired on re-export.
5. (earlier today, 17418da) Odds API team map +70 spellings; unique team-total quote_id.

## PROVEN_CALIBRATION_DEFICIENCIES
1. **Selection on model-market disagreement (player and game, PRIMARY).** The graded side is the max-EV side
   against the market. The market is better informed, so the realized rate tracks the market in every disagreement
   bin, and the graded-side probability is inflated by about the disagreement: player +12.2pp, game ~+9pp. The
   model's standalone PMFs are calibrated: player variance ratio ~1.0, PIT ~uniform, population means unbiased; game
   total sd fine, OT / foul / mapping exact.
2. **Player per-minute rate compression (PRIOR_SHRINKAGE, contributing).** The fixed half-life-1 prior-season blend
   gives the current season only 18.8% of history minutes in November. Market-listed starters' rates come out low:
   PTS model 0.415 vs realized 0.460 per minute, AST 0.074 vs 0.089. The same mechanism appears in 3 historical
   seasons and in the pre-launch audit. It is worth ~2pp of the UNDER gap.
3. Game: a +4.6-point common over-shock vs the market in a 50-game sample (the market missed equally). Weaker leads
   to re-test: season total drift +1.6 (t 2.6); early-season margin spread ~5-12% narrow.

## INFRASTRUCTURE_DEFECTS (open)
* Game entry quotes have no staleness limit for a wholly withdrawn market (1 moneyline row, 11 h old).
* Run feature DBs are pruned after ~24 h, so exact per-run rate inputs for older runs can't be re-traced. Persisting a
  small per-run rate-component parquet would make future audits exact.
* 353 stored player Performance rows keep NO_CLOSE_QUOTE until a rebuild is authorised.

## NO_CHANGE_COMPONENTS
PMF-to-line pricing (832/832 exact, game 279/279 to 3e-16); player PMF dispersion; minutes; p_play; role
classification; game HOME/AWAY/team-total mapping; total variance; OT handling; late-game foul kernel (mean-
preserving); lineup adjustment (delta 0); GameJointPMF architecture. No high-total adjustment: 155+ residual -0.2, and
135-145 is an outcome shock shared with the market.

## Proposed mathematical changes (NONE deployed)

| | Player rate blend | Game market-anchored mean | Player market-anchored probability |
|---|---|---|---|
| CURRENT | half-life 1 prior-season blend | PMF at model total / margin | model PMF probability |
| CHALLENGER | per-stat half-life (PTS .35, AST .5, REB .5, FG3M .75) | total/margin mean -> close + w(model - close) | logit blend toward de-vigged market, weight w |
| DEVELOPMENT | F2024 | 2023-24 closing lines (offline CBBD fetch, not in repo) | live frozen evaluations, first half |
| SELECTION | F2025 (rule fixed in advance) | 2024-25 | live, second half |
| FROZEN/OOS | F2026: log loss / RPS / Brier better for all 4 stats | 2026-27 live, >= 300 games | later live window |
| PROSPECTIVE_EXPECTATION | live PTS UNDER p .644 -> .621; ~10% of the gap | Brier <= market; w ~0-0.2 | removes most of the inflation; little or no edge |
| STATUS | NOT SELECTED: low-exposure mean drifts +0.2 PTS; needs exposure-adaptive weight + JM1 structural refit | NOT EVALUATED (data) | NOT EVALUATED (no historical prop lines) |

Implication: the model has no demonstrated edge against these markets. The Board's EV / tier labels on player and game
markets overstate confidence by about the model-market disagreement until a market-anchored challenger is proven.
