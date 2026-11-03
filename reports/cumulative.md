# CBB 2026-27 prospective shadow: season to date

SHADOW_ONLY. Diagnostic only: no automatic model correction, no wagering, no public deployment. Every figure carries its N; no launch threshold is implied.

## Operations

```
{
 "games_registered": 121,
 "games_by_status": {
  "PREDICTED": 121
 },
 "games_predicted": 121,
 "games_predicted_with_game_blockers": 121,
 "timing_deviation_minutes": {
  "n": 121,
  "min": 11.706526916666666,
  "median": 44.14235833333333,
  "max": 46.243770049999995
 },
 "quote_snapshots": 0,
 "quote_snapshots_with_bookmakers": 0,
 "games_settled": 5,
 "odds_api": {
  "live_requests": 104,
  "credits_used": 0,
  "quota_remaining_last": 4912393
 },
 "cbbd": {
  "live_requests": 277,
  "cache_hits": 184,
  "refused": 0
 }
}
```

Evidence: {"player_projections": 4294, "settled_players_played": 57, "excluded_not_clean": 0, "quoted_lines": 0, "quote_side_records": 0, "settled_quote_sides": 0}

Warnings: none

## High-exposure PTS (min_mu >= 30)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS min_mu>=30 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING: False

Market benchmark (same lines): {}

## Early season

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS team_games_le_2 | 55 | 6.956 | 8.382 | -1.426 [-3.175, 0.324] (n=55, games=5) | -0.170 | 0/0 | - | - | 0/0 |
| AST team_games_le_2 | 55 | 1.225 | 1.436 | -0.212 [-0.694, 0.270] (n=55, games=5) | -0.147 | 0/0 | - | - | 0/0 |
| STL team_games_le_2 | 55 | 0.612 | 0.782 | -0.170 [-0.460, 0.120] (n=55, games=5) | -0.218 | 0/0 | - | - | 0/0 |
| TOV team_games_le_2 | 55 | 0.976 | 1.273 | -0.297 [-0.534, -0.060] (n=55, games=5) | -0.233 | 0/0 | - | - | 0/0 |
| PRA team_games_le_2 | 55 | 11.161 | 13.309 | -2.148 [-4.344, 0.049] (n=55, games=5) | -0.161 | 0/0 | - | - | 0/0 |
| PR team_games_le_2 | 55 | 9.937 | 11.873 | -1.936 [-3.721, -0.151] (n=55, games=5) | -0.163 | 0/0 | - | - | 0/0 |
| PA team_games_le_2 | 55 | 8.180 | 9.818 | -1.638 [-3.763, 0.487] (n=55, games=5) | -0.167 | 0/0 | - | - | 0/0 |
| PTS first_30_days | 55 | 6.956 | 8.382 | -1.426 [-3.175, 0.324] (n=55, games=5) | -0.170 | 0/0 | - | - | 0/0 |
| AST first_30_days | 55 | 1.225 | 1.436 | -0.212 [-0.694, 0.270] (n=55, games=5) | -0.147 | 0/0 | - | - | 0/0 |
| STL first_30_days | 55 | 0.612 | 0.782 | -0.170 [-0.460, 0.120] (n=55, games=5) | -0.218 | 0/0 | - | - | 0/0 |
| TOV first_30_days | 55 | 0.976 | 1.273 | -0.297 [-0.534, -0.060] (n=55, games=5) | -0.233 | 0/0 | - | - | 0/0 |
| PRA first_30_days | 55 | 11.161 | 13.309 | -2.148 [-4.344, 0.049] (n=55, games=5) | -0.161 | 0/0 | - | - | 0/0 |
| PR first_30_days | 55 | 9.937 | 11.873 | -1.936 [-3.721, -0.151] (n=55, games=5) | -0.163 | 0/0 | - | - | 0/0 |
| PA first_30_days | 55 | 8.180 | 9.818 | -1.638 [-3.763, 0.487] (n=55, games=5) | -0.167 | 0/0 | - | - | 0/0 |

## Current-season evidence buckets (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| CUR_0 | 55 | 6.956 | 8.382 | -1.426 [-3.175, 0.324] (n=55, games=5) | -0.170 | 0/0 | - | - | 0/0 |
| CUR_0_100 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_100_300 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_300PLUS | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

## Transfers / cold starts

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS COLD_TRANSFER_IN | 23 | 8.144 | 9.522 | -1.377 [-5.567, 2.812] (n=23, games=5) | -0.145 | 0/0 | - | - | 0/0 |
| REB COLD_TRANSFER_IN | 23 | 3.042 | 3.348 | -0.306 [-1.244, 0.633] (n=23, games=5) | -0.091 | 0/0 | - | - | 0/0 |
| STL COLD_TRANSFER_IN | 23 | 0.645 | 0.696 | -0.051 [-0.259, 0.157] (n=23, games=5) | -0.073 | 0/0 | - | - | 0/0 |
| TOV COLD_TRANSFER_IN | 23 | 1.040 | 1.696 | -0.656 [-1.214, -0.098] (n=23, games=5) | -0.387 | 0/0 | - | - | 0/0 |
| STOCKS COLD_TRANSFER_IN | 23 | 0.931 | 1.348 | -0.417 [-0.893, 0.060] (n=23, games=5) | -0.309 | 0/0 | - | - | 0/0 |
| PTS COLD_NO_HISTORY | 10 | 4.954 | 5.700 | -0.746 [-3.140, 1.647] (n=10, games=4) | -0.131 | 0/0 | - | - | 0/0 |
| REB COLD_NO_HISTORY | 10 | 2.048 | 2.600 | -0.552 [-1.481, 0.376] (n=10, games=4) | -0.212 | 0/0 | - | - | 0/0 |
| STL COLD_NO_HISTORY | 10 | 0.456 | 0.800 | -0.344 [-0.587, -0.102] (n=10, games=4) | -0.431 | 0/0 | - | - | 0/0 |
| TOV COLD_NO_HISTORY | 10 | 0.732 | 1.000 | -0.268 [-1.116, 0.580] (n=10, games=4) | -0.268 | 0/0 | - | - | 0/0 |
| STOCKS COLD_NO_HISTORY | 10 | 0.674 | 0.900 | -0.226 [-0.601, 0.149] (n=10, games=4) | -0.251 | 0/0 | - | - | 0/0 |

## Centers (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| C | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| non_C | 55 | 6.956 | 8.382 | -1.426 [-3.175, 0.324] (n=55, games=5) | -0.170 | 0/0 | - | - | 0/0 |

## Over / Under sides

Opportunities 0; OVER favored 0, UNDER favored 0; OVER share of favored -; mean edge OVER -, UNDER -.

| side | settled | W/L/P | Brier | log loss |
|---|---|---|---|---|
| OVER | 0 | 0/0/0 | - | - |
| UNDER | 0 | 0/0/0 | - | - |

## Model vs no-vig market (identical lines)

Lines 0. Brier model -, market -, difference -. Log loss model -, market -, difference -.

## All markets

| market | N played | signed error | rel. error | settled lines | Brier | log loss |
|---|---|---|---|---|---|---|
| PTS | 55 | -1.426 [-3.175, 0.324] (n=55, games=5) | -0.170 | 0 | - | - |
| REB | 55 | -0.509 [-0.802, -0.217] (n=55, games=5) | -0.146 | 0 | - | - |
| AST | 55 | -0.212 [-0.694, 0.270] (n=55, games=5) | -0.147 | 0 | - | - |
| FG3M | 55 | -0.119 [-0.453, 0.214] (n=55, games=5) | -0.137 | 0 | - | - |
| STL | 55 | -0.170 [-0.460, 0.120] (n=55, games=5) | -0.218 | 0 | - | - |
| BLK | 55 | -0.131 [-0.262, 0.001] (n=55, games=5) | -0.288 | 0 | - | - |
| TOV | 55 | -0.297 [-0.534, -0.060] (n=55, games=5) | -0.233 | 0 | - | - |
| PRA | 55 | -2.148 [-4.344, 0.049] (n=55, games=5) | -0.161 | 0 | - | - |
| PR | 55 | -1.936 [-3.721, -0.151] (n=55, games=5) | -0.163 | 0 | - | - |
| PA | 55 | -1.638 [-3.763, 0.487] (n=55, games=5) | -0.167 | 0 | - | - |
| RA | 55 | -0.721 [-1.425, -0.018] (n=55, games=5) | -0.146 | 0 | - | - |
| STOCKS | 55 | -0.301 [-0.622, 0.021] (n=55, games=5) | -0.243 | 0 | - | - |
