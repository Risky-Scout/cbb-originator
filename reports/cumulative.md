# CBB 2026-27 prospective shadow: season to date

SHADOW_ONLY. Diagnostic only: no automatic model correction, no wagering, no public deployment. Every figure carries its N; no launch threshold is implied.

## Operations

```
{
 "games_registered": 6,
 "games_by_status": {
  "PREDICTED": 6
 },
 "games_predicted": 6,
 "games_predicted_with_game_blockers": 6,
 "timing_deviation_minutes": {
  "n": 6,
  "min": 11.706526916666666,
  "median": 37.678578816666665,
  "max": 46.1327937
 },
 "quote_snapshots": 0,
 "quote_snapshots_with_bookmakers": 0,
 "games_settled": 3,
 "odds_api": {
  "live_requests": 79,
  "credits_used": 0,
  "quota_remaining_last": 4912393
 },
 "cbbd": {
  "live_requests": 222,
  "cache_hits": 71,
  "refused": 0
 }
}
```

Evidence: {"player_projections": 236, "settled_players_played": 38, "excluded_not_clean": 0, "quoted_lines": 0, "quote_side_records": 0, "settled_quote_sides": 0}

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
| PTS team_games_le_2 | 37 | 7.144 | 8.622 | -1.477 [-3.706, 0.751] (n=37, games=3) | -0.171 | 0/0 | - | - | 0/0 |
| AST team_games_le_2 | 37 | 1.226 | 1.243 | -0.018 [-0.331, 0.296] (n=37, games=3) | -0.014 | 0/0 | - | - | 0/0 |
| STL team_games_le_2 | 37 | 0.630 | 0.892 | -0.262 [-0.630, 0.106] (n=37, games=3) | -0.294 | 0/0 | - | - | 0/0 |
| TOV team_games_le_2 | 37 | 0.950 | 1.243 | -0.293 [-0.626, 0.040] (n=37, games=3) | -0.236 | 0/0 | - | - | 0/0 |
| PRA team_games_le_2 | 37 | 11.318 | 13.162 | -1.844 [-4.337, 0.648] (n=37, games=3) | -0.140 | 0/0 | - | - | 0/0 |
| PR team_games_le_2 | 37 | 10.092 | 11.919 | -1.827 [-4.051, 0.397] (n=37, games=3) | -0.153 | 0/0 | - | - | 0/0 |
| PA team_games_le_2 | 37 | 8.372 | 9.865 | -1.493 [-3.958, 0.972] (n=37, games=3) | -0.151 | 0/0 | - | - | 0/0 |
| PTS first_30_days | 37 | 7.144 | 8.622 | -1.477 [-3.706, 0.751] (n=37, games=3) | -0.171 | 0/0 | - | - | 0/0 |
| AST first_30_days | 37 | 1.226 | 1.243 | -0.018 [-0.331, 0.296] (n=37, games=3) | -0.014 | 0/0 | - | - | 0/0 |
| STL first_30_days | 37 | 0.630 | 0.892 | -0.262 [-0.630, 0.106] (n=37, games=3) | -0.294 | 0/0 | - | - | 0/0 |
| TOV first_30_days | 37 | 0.950 | 1.243 | -0.293 [-0.626, 0.040] (n=37, games=3) | -0.236 | 0/0 | - | - | 0/0 |
| PRA first_30_days | 37 | 11.318 | 13.162 | -1.844 [-4.337, 0.648] (n=37, games=3) | -0.140 | 0/0 | - | - | 0/0 |
| PR first_30_days | 37 | 10.092 | 11.919 | -1.827 [-4.051, 0.397] (n=37, games=3) | -0.153 | 0/0 | - | - | 0/0 |
| PA first_30_days | 37 | 8.372 | 9.865 | -1.493 [-3.958, 0.972] (n=37, games=3) | -0.151 | 0/0 | - | - | 0/0 |

## Current-season evidence buckets (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| CUR_0 | 37 | 7.144 | 8.622 | -1.477 [-3.706, 0.751] (n=37, games=3) | -0.171 | 0/0 | - | - | 0/0 |
| CUR_0_100 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_100_300 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_300PLUS | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

## Transfers / cold starts

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS COLD_TRANSFER_IN | 18 | 8.269 | 9.278 | -1.009 [-6.255, 4.237] (n=18, games=3) | -0.109 | 0/0 | - | - | 0/0 |
| REB COLD_TRANSFER_IN | 18 | 3.186 | 3.111 | 0.075 [-0.915, 1.066] (n=18, games=3) | 0.024 | 0/0 | - | - | 0/0 |
| STL COLD_TRANSFER_IN | 18 | 0.654 | 0.722 | -0.069 [-0.326, 0.189] (n=18, games=3) | -0.095 | 0/0 | - | - | 0/0 |
| TOV COLD_TRANSFER_IN | 18 | 1.041 | 1.722 | -0.681 [-1.133, -0.230] (n=18, games=3) | -0.396 | 0/0 | - | - | 0/0 |
| STOCKS COLD_TRANSFER_IN | 18 | 0.956 | 1.278 | -0.322 [-0.487, -0.156] (n=18, games=3) | -0.252 | 0/0 | - | - | 0/0 |
| PTS COLD_NO_HISTORY | 7 | 4.990 | 6.143 | -1.153 [-4.214, 1.909] (n=7, games=2) | -0.188 | 0/0 | - | - | 0/0 |
| REB COLD_NO_HISTORY | 7 | 2.076 | 2.571 | -0.495 [-1.002, 0.011] (n=7, games=2) | -0.193 | 0/0 | - | - | 0/0 |
| STL COLD_NO_HISTORY | 7 | 0.462 | 0.857 | -0.395 [-0.617, -0.174] (n=7, games=2) | -0.461 | 0/0 | - | - | 0/0 |
| TOV COLD_NO_HISTORY | 7 | 0.751 | 0.857 | -0.106 [-1.446, 1.234] (n=7, games=2) | -0.124 | 0/0 | - | - | 0/0 |
| STOCKS COLD_NO_HISTORY | 7 | 0.683 | 0.857 | -0.174 [-0.389, 0.041] (n=7, games=2) | -0.203 | 0/0 | - | - | 0/0 |

## Centers (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| C | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| non_C | 37 | 7.144 | 8.622 | -1.477 [-3.706, 0.751] (n=37, games=3) | -0.171 | 0/0 | - | - | 0/0 |

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
| PTS | 37 | -1.477 [-3.706, 0.751] (n=37, games=3) | -0.171 | 0 | - | - |
| REB | 37 | -0.352 [-0.673, -0.032] (n=37, games=3) | -0.107 | 0 | - | - |
| AST | 37 | -0.018 [-0.331, 0.296] (n=37, games=3) | -0.014 | 0 | - | - |
| FG3M | 37 | -0.217 [-0.560, 0.125] (n=37, games=3) | -0.206 | 0 | - | - |
| STL | 37 | -0.262 [-0.630, 0.106] (n=37, games=3) | -0.294 | 0 | - | - |
| BLK | 37 | -0.124 [-0.206, -0.041] (n=37, games=3) | -0.305 | 0 | - | - |
| TOV | 37 | -0.293 [-0.626, 0.040] (n=37, games=3) | -0.236 | 0 | - | - |
| PRA | 37 | -1.844 [-4.337, 0.648] (n=37, games=3) | -0.140 | 0 | - | - |
| PR | 37 | -1.827 [-4.051, 0.397] (n=37, games=3) | -0.153 | 0 | - | - |
| PA | 37 | -1.493 [-3.958, 0.972] (n=37, games=3) | -0.151 | 0 | - | - |
| RA | 37 | -0.369 [-0.948, 0.211] (n=37, games=3) | -0.081 | 0 | - | - |
| STOCKS | 37 | -0.385 [-0.709, -0.060] (n=37, games=3) | -0.297 | 0 | - | - |
