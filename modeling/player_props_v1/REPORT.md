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

## Final development refit through 2024-25 (tag `cbb-props-v1-refit-2024-25`)

Code: `cbb_platform/props_v1_refit.py`. Artifact: `refit_2024_25_artifact.json` (JSON, 15 KB). Tests:
`tests/test_props_v1_refit.py`. The frozen v1 specification is refit unchanged. The model, pricing and config files
are identical to the `cbb-props-production-candidate-v1` tag (tested). No parameter or hyperparameter was selected.

| component | training data |
|---|---|
| minutes and count rates | 1,272,278 appearances, 2013-14..2024-25. Same recency weights, now measured from 2024-25 |
| participation | 2023-24 and 2024-25 identifiable rows: 224,851 PLAYED, 268,300 DNP. Excluded: 19,346 UNKNOWN and 2,398 NOT_ON_ROSTER |

The artifact holds only what scoring needs:
- the v0 state: minutes preprocessing and coefficients, position prior rates, shrinkage k, dispersion and family per
  target, minutes residual SDs, baseline population means;
- the participation state: preprocessing and coefficients;
- the frozen config;
- versions, the training range, the coefficient order, and the spec and artifact fingerprints.

Player rates are computed at scoring time from point-in-time features, as designed. `load()` refuses an artifact
whose fingerprint, specification, coefficient order or training range does not check out.

**Reproducibility:** two refits in separate processes write byte-identical artifacts. Loading the artifact in a fresh
process, with both fit functions disabled, scores the fixed demonstration games identically to the in-memory refit.
**Bug found and fixed during the refit:** a first version serialized with sorted keys. That reordered the coefficient
dicts, which the frozen scorers read positionally, and the fresh-process test caught the mismatch. Insertion order is
now preserved, and `load()` checks the order.

**2024-25 is now training data.** Scoring it with this artifact is a functional check only, not out-of-sample evidence.
The promotion evidence remains the frozen 2023-24 / 2024-25 validation in `modeling/player_props_v0/metrics.json` and
`participation_metrics.json` (unchanged).
