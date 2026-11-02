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
 "games_predicted_with_game_blockers": 1,
 "timing_deviation_minutes": {
  "n": 1,
  "min": 46.1327937,
  "median": 46.1327937,
  "max": 46.1327937
 },
 "quote_snapshots": 0,
 "quote_snapshots_with_bookmakers": 0,
 "games_settled": 1,
 "odds_api": {
  "live_requests": 63,
  "credits_used": 0,
  "quota_remaining_last": 4912393
 },
 "cbbd": {
  "live_requests": 203,
  "cache_hits": 65,
  "refused": 0
 }
}
```

Evidence: {"player_projections": 28, "settled_players_played": 15, "excluded_not_clean": 0, "quoted_lines": 0, "quote_side_records": 0, "settled_quote_sides": 0}

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
| PTS team_games_le_2 | 15 | 7.851 | 8.467 | -0.616 (n=15, games=1) | -0.073 | 0/0 | - | - | 0/0 |
| AST team_games_le_2 | 15 | 1.312 | 1.400 | -0.088 (n=15, games=1) | -0.063 | 0/0 | - | - | 0/0 |
| STL team_games_le_2 | 15 | 0.599 | 1.133 | -0.534 (n=15, games=1) | -0.471 | 0/0 | - | - | 0/0 |
| TOV team_games_le_2 | 15 | 0.961 | 1.533 | -0.573 (n=15, games=1) | -0.373 | 0/0 | - | - | 0/0 |
| PRA team_games_le_2 | 15 | 12.225 | 13.533 | -1.308 (n=15, games=1) | -0.097 | 0/0 | - | - | 0/0 |
| PR team_games_le_2 | 15 | 10.915 | 12.133 | -1.219 (n=15, games=1) | -0.100 | 0/0 | - | - | 0/0 |
| PA team_games_le_2 | 15 | 9.158 | 9.867 | -0.709 (n=15, games=1) | -0.072 | 0/0 | - | - | 0/0 |
| PTS first_30_days | 15 | 7.851 | 8.467 | -0.616 (n=15, games=1) | -0.073 | 0/0 | - | - | 0/0 |
| AST first_30_days | 15 | 1.312 | 1.400 | -0.088 (n=15, games=1) | -0.063 | 0/0 | - | - | 0/0 |
| STL first_30_days | 15 | 0.599 | 1.133 | -0.534 (n=15, games=1) | -0.471 | 0/0 | - | - | 0/0 |
| TOV first_30_days | 15 | 0.961 | 1.533 | -0.573 (n=15, games=1) | -0.373 | 0/0 | - | - | 0/0 |
| PRA first_30_days | 15 | 12.225 | 13.533 | -1.308 (n=15, games=1) | -0.097 | 0/0 | - | - | 0/0 |
| PR first_30_days | 15 | 10.915 | 12.133 | -1.219 (n=15, games=1) | -0.100 | 0/0 | - | - | 0/0 |
| PA first_30_days | 15 | 9.158 | 9.867 | -0.709 (n=15, games=1) | -0.072 | 0/0 | - | - | 0/0 |

## Current-season evidence buckets (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| CUR_0 | 15 | 7.851 | 8.467 | -0.616 (n=15, games=1) | -0.073 | 0/0 | - | - | 0/0 |
| CUR_0_100 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_100_300 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_300PLUS | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

## Transfers / cold starts

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| PTS COLD_TRANSFER_IN | 8 | 9.386 | 8.000 | 1.386 (n=8, games=1) | 0.173 | 0/0 | - | - | 0/0 |
| REB COLD_TRANSFER_IN | 8 | 3.507 | 3.750 | -0.243 (n=8, games=1) | -0.065 | 0/0 | - | - | 0/0 |
| STL COLD_TRANSFER_IN | 8 | 0.646 | 0.875 | -0.229 (n=8, games=1) | -0.262 | 0/0 | - | - | 0/0 |
| TOV COLD_TRANSFER_IN | 8 | 1.081 | 2.000 | -0.919 (n=8, games=1) | -0.459 | 0/0 | - | - | 0/0 |
| STOCKS COLD_TRANSFER_IN | 8 | 0.929 | 1.250 | -0.321 (n=8, games=1) | -0.257 | 0/0 | - | - | 0/0 |
| PTS COLD_NO_HISTORY | 3 | 5.337 | 4.667 | 0.670 (n=3, games=1) | 0.144 | 0/0 | - | - | 0/0 |
| REB COLD_NO_HISTORY | 3 | 2.140 | 2.333 | -0.194 (n=3, games=1) | -0.083 | 0/0 | - | - | 0/0 |
| STL COLD_NO_HISTORY | 3 | 0.473 | 1.000 | -0.527 (n=3, games=1) | -0.527 | 0/0 | - | - | 0/0 |
| TOV COLD_NO_HISTORY | 3 | 0.763 | 1.667 | -0.904 (n=3, games=1) | -0.542 | 0/0 | - | - | 0/0 |
| STOCKS COLD_NO_HISTORY | 3 | 0.698 | 1.000 | -0.302 (n=3, games=1) | -0.302 | 0/0 | - | - | 0/0 |

## Centers (PTS)

| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | quoted-line Brier | quoted-line log loss | Over/Under favored |
|---|---|---|---|---|---|---|---|---|---|
| C | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| non_C | 15 | 7.851 | 8.467 | -0.616 (n=15, games=1) | -0.073 | 0/0 | - | - | 0/0 |

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
| PTS | 15 | -0.616 (n=15, games=1) | -0.073 | 0 | - | - |
| REB | 15 | -0.598 (n=15, games=1) | -0.163 | 0 | - | - |
| AST | 15 | -0.088 (n=15, games=1) | -0.063 | 0 | - | - |
| FG3M | 15 | -0.388 (n=15, games=1) | -0.306 | 0 | - | - |
| STL | 15 | -0.534 (n=15, games=1) | -0.471 | 0 | - | - |
| BLK | 15 | -0.061 (n=15, games=1) | -0.183 | 0 | - | - |
| TOV | 15 | -0.573 (n=15, games=1) | -0.373 | 0 | - | - |
| PRA | 15 | -1.308 (n=15, games=1) | -0.097 | 0 | - | - |
| PR | 15 | -1.219 (n=15, games=1) | -0.100 | 0 | - | - |
| PA | 15 | -0.709 (n=15, games=1) | -0.072 | 0 | - | - |
| RA | 15 | -0.690 (n=15, games=1) | -0.136 | 0 | - | - |
| STOCKS | 15 | -0.596 (n=15, games=1) | -0.406 | 0 | - | - |
