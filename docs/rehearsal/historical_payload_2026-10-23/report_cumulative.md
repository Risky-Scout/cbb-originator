# CBB 2026-27 prospective shadow: season to date

SHADOW_ONLY. Diagnostic only: no automatic model correction, no wagering, no public deployment. Every figure carries its N; no launch threshold is implied.

## Operations

```
{
 "games_registered": 1,
 "games_by_status": {
  "PREDICTED": 1
 },
 "games_predicted": 1,
 "games_predicted_with_game_blockers": 0,
 "timing_deviation_minutes": {
  "n": 1,
  "min": 18.0,
  "median": 18.0,
  "max": 18.0
 },
 "quote_snapshots": 1,
 "quote_snapshots_with_bookmakers": 1,
 "games_settled": 1,
 "odds_api": {
  "live_requests": 0,
  "credits_used": 0,
  "quota_remaining_last": null
 },
 "cbbd": {
  "live_requests": 0,
  "cache_hits": 0,
  "refused": 0
 }
}
```

Evidence: {"player_projections": 44, "settled_players_played": 24, "excluded_not_clean": 0, "quoted_lines": 45, "quote_side_records": 170, "settled_quote_sides": 170}

Warnings: OVER_UNDER_CONCENTRATION_WARNING

## High-exposure PTS (min_mu >= 30)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS min_mu>=30 | 3 | 14.531 | 16.333 | -1.802 (n=3, games=1) | -0.110 | 6/6 | 0.215 (n=6, games=1) | 0.621 (n=6, games=1) | 0/11 |

PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING: False

Market benchmark (same lines): {"lines": 6, "games": 1, "model_brier": {"n": 6, "games": 1, "value": 0.21456472617852576, "se": null, "lo": null, "hi": null}, "market_brier": {"n": 6, "games": 1, "value": 0.2479275776355062, "se": null, "lo": null, "hi": null}, "brier_model_minus_market": {"n": 6, "games": 1, "value": -0.03336285145698046, "se": null, "lo": null, "hi": null}, "model_log_loss": {"n": 6, "games": 1, "value": 0.6207656841125666, "se": null, "lo": null, "hi": null}, "market_log_loss": {"n": 6, "games": 1, "value": 0.6890033304066794, "se": null, "lo": null, "hi": null}, "log_loss_model_minus_market": {"n": 6, "

## Early season

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| AST team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| STL team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| TOV team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PRA team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PR team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PA team_games_le_2 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PTS first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| AST first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| STL first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| TOV first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PRA first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PR first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PA first_30_days | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

## Current-season evidence buckets (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| CUR_0 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_0_100 | 4 | 2.077 | 1.250 | 0.827 (n=4, games=1) | 0.662 | 0/0 | - | - | 0/0 |
| CUR_100_300 | 4 | 4.002 | 2.000 | 2.002 (n=4, games=1) | 1.001 | 0/0 | - | - | 0/0 |
| CUR_300PLUS | 16 | 10.271 | 11.375 | -1.104 (n=16, games=1) | -0.097 | 14/14 | 0.221 (n=14, games=1) | 0.634 (n=14, games=1) | 4/27 |

## Transfers / cold starts

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS COLD_TRANSFER_IN | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| REB COLD_TRANSFER_IN | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| STL COLD_TRANSFER_IN | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| TOV COLD_TRANSFER_IN | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| STOCKS COLD_TRANSFER_IN | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| PTS COLD_NO_HISTORY | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| REB COLD_NO_HISTORY | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| STL COLD_NO_HISTORY | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| TOV COLD_NO_HISTORY | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| STOCKS COLD_NO_HISTORY | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

## Centers (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| C | 2 | 3.203 | 2.000 | 1.203 (n=2, games=1) | 0.601 | 0/0 | - | - | 0/0 |
| non_C | 22 | 8.284 | 8.682 | -0.398 (n=22, games=1) | -0.046 | 14/14 | 0.221 (n=14, games=1) | 0.634 (n=14, games=1) | 4/27 |

## Over / Under sides

Opportunities 85; OVER favored 23, UNDER favored 62; OVER share of favored 0.271 [0.188, 0.373] (23/85); mean edge OVER -0.141, UNDER 0.019.

| side | settled | W/L/P | Brier | log loss |
|---|---|---|---|---|
| OVER | 85 | 29/56/0 | 0.226 (n=85, games=1) | 0.643 (n=85, games=1) |
| UNDER | 85 | 56/29/0 | 0.226 (n=85, games=1) | 0.643 (n=85, games=1) |

## Model vs no-vig market (identical lines)

Lines 45. Brier model 0.226 (n=45, games=1), market 0.241 (n=45, games=1), difference -0.015 (n=45, games=1). Log loss model 0.644 (n=45, games=1), market 0.675 (n=45, games=1), difference -0.032 (n=45, games=1).

## All markets

| market | N played | signed error | rel. error | settled lines | Brier | log loss |
|---|---|---|---|---|---|---|
| PTS | 24 | -0.265 (n=24, games=1) | -0.033 | 14 | 0.221 (n=14, games=1) | 0.634 (n=14, games=1) |
| REB | 24 | 0.263 (n=24, games=1) | 0.090 | 11 | 0.251 (n=11, games=1) | 0.696 (n=11, games=1) |
| AST | 24 | 0.300 (n=24, games=1) | 0.233 | 11 | 0.225 (n=11, games=1) | 0.642 (n=11, games=1) |
| FG3M | 24 | -0.002 (n=24, games=1) | -0.002 | 9 | 0.204 (n=9, games=1) | 0.597 (n=9, games=1) |
| STL | 24 | 0.182 (n=24, games=1) | 0.363 | 0 | - | - |
| BLK | 24 | 0.085 (n=24, games=1) | 0.406 | 0 | - | - |
| TOV | 24 | 0.200 (n=24, games=1) | 0.240 | 0 | - | - |
| PRA | 24 | 0.289 (n=24, games=1) | 0.023 | 0 | - | - |
| PR | 24 | -0.013 (n=24, games=1) | -0.001 | 0 | - | - |
| PA | 24 | 0.029 (n=24, games=1) | 0.003 | 0 | - | - |
| RA | 24 | 0.562 (n=24, games=1) | 0.133 | 0 | - | - |
| STOCKS | 24 | 0.266 (n=24, games=1) | 0.375 | 0 | - | - |
