# Production candidate v1: marginal prop pricing + slate scorer

**Branch:** `modeling/cbb-props-production-candidate-v1`, from `f9cf4df` (tag `cbb-player-props-v0-participation`).
**Frozen model:** Challenger v0 (tag `cbb-player-props-v0`) plus the participation model, unchanged. **Dataset:**
`cbb_data_v2.4+5f19d31e524a`. No sportsbook data. 2025-26 is closed.

Code: `cbb_platform/props_v1_pricing.py`. Tests: `tests/test_props_v1_pricing.py`. Sample:
`sample_predictions.csv` (`python -m cbb_platform.props_v1_pricing`).

## Pricing

For each conditional-on-playing PMF (PTS, REB, AST, FG3M, STL, BLK, TOV):
- **Integer line L:** P_OVER = P(Y > L), P_PUSH = P(Y = L), P_UNDER = P(Y < L).
- **Half line:** P_PUSH = 0.

Prices are computed from the PMF/CDF exactly, with no simulation. Fair decimal and American odds use the non-push
probabilities; the raw push probability is kept in its own column. The PMF's last point (120) holds the folded tail
P(Y ≥ 120), so lines are limited to ≤ 119.5, where every price is exact. Requesting a line outside that, or a
non-half-point line, raises an error. A probability of exactly 0 or 1 has no finite fair price and is reported as
NaN.

`participation_probability` is a separate column. It is never folded into the PMF, so the prices are for the market
conditional on the player playing. The decision to publish a prop is left to production.

## Line ladder

For each player × stat: half lines from q₀.₀₀₅ − 0.5 to q₀.₉₉₅ + 0.5, plus integer lines ≥ 1 inside the central 99 %.
In the sample this averages 13 lines per player-stat (min 3, max 107). The maximum is a high-usage player's PTS,
whose NegBin central 99 % spans 0–54. The PMF remains authoritative for any other line.

## Demonstration (2024-25, state fit through 2023-24 only; outcomes never used)

There are 2 deterministic games: the smallest canonical game ids, which are content hashes. That gives 78
candidates and 7,264 rows (2.6 MB): 6 players at ≥ 30 projected minutes, 26 at 15–30, and 36 with P(play) < 0.2. All
7 stats are covered, each with both integer and half-point lines. Reruns are byte-identical (tested). The sample was
reduced from the accepted 4-game version by changing only `SAMPLE_GAMES`; its rows are exactly that version's first
2 games.

## Runtime (2-thread limits, warm cache)

| step | time |
|---|---|
| load + fit the frozen models (once per session) | 6.6 s |
| one player (all 7 stats, full ladders) | 0.11 s |
| one game (38 candidates) | 0.12 s |
| representative slate: 2025-02-08, 154 games, 7,134 candidates, 627,024 priced lines | 0.70 s |

The only noticeable cost is the one-time model load and fit, which reads the Stage 3 stores. Per-call cost is
dominated by fixed overhead (two read-only DuckDB lookups for names and tips), not by pricing.
