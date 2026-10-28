# CBB 2026-27 prospective shadow: season to date

SHADOW_ONLY. Diagnostic only: no automatic model correction, no wagering, no public deployment. Every figure carries its N; no launch threshold is implied.

## Operations

```
{
 "games_registered": 0,
 "games_by_status": {},
 "games_predicted": 0,
 "games_predicted_with_game_blockers": 0,
 "timing_deviation_minutes": {
  "n": 0,
  "min": null,
  "median": null,
  "max": null
 },
 "quote_snapshots": 0,
 "quote_snapshots_with_bookmakers": 0,
 "games_settled": 0,
 "odds_api": {
  "live_requests": 15,
  "credits_used": 0,
  "quota_remaining_last": 4912393
 },
 "cbbd": {
  "live_requests": 117,
  "cache_hits": 47,
  "refused": 0
 }
}
```

Evidence: {"player_projections": 0, "settled_players_played": 0, "excluded_not_clean": 0, "quoted_lines": 0, "quote_side_records": 0, "settled_quote_sides": 0}

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
| CUR_0_100 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_100_300 | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| CUR_300PLUS | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

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
| C | 0 | - | - | - | - | 0/0 | - | - | 0/0 |
| non_C | 0 | - | - | - | - | 0/0 | - | - | 0/0 |

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
| PTS | 0 | - | - | 0 | - | - |
| REB | 0 | - | - | 0 | - | - |
| AST | 0 | - | - | 0 | - | - |
| FG3M | 0 | - | - | 0 | - | - |
| STL | 0 | - | - | 0 | - | - |
| BLK | 0 | - | - | 0 | - | - |
| TOV | 0 | - | - | 0 | - | - |
| PRA | 0 | - | - | 0 | - | - |
| PR | 0 | - | - | 0 | - | - |
| PA | 0 | - | - | 0 | - | - |
| RA | 0 | - | - | 0 | - | - |
| STOCKS | 0 | - | - | 0 | - | - |
