# CBB game model — Release B (SHADOW)

## What it is
One `GameJointPMF = P(H = h, A = a)` per forecast snapshot × game. Every game market (spread, total, moneyline, home/away
team totals, alternates) is computed from that single matrix (`cbb_platform/game/markets.py`).

| layer | module | rule |
|---|---|---|
| team ratings | `game/ratings.py` | Opponent-adjusted ridge on offense, defense, pace and HCA. Point in time: it uses only games knowable before D's training cutoff (all of D−1). There is ONE state per ET day, persisted at `ops/forecasts/<D>/game_ratings/`. Every later snapshot reuses it; a rebuild happens only to recover a non-fresh state. |
| prior | `game/params/ratings_prior_2027.parquet` | Season-final chain 2014..2026, carried with `rho_carry = 1.0`; sealed in `game_model.json`. |
| player term | `game/model.py` | `mu += beta·(S_today − S_base) + gamma·availability_delta`. `beta = 0` by walk-forward selection. `gamma = 0.3` is a conservative prior and is not fitted. The availability delta is the change in the model's own expected points since the INITIAL snapshot, so it is 0 at INITIAL. Player PMFs are never summed. |
| variance | `game/model.py` | `sd_m = 11.23·(1 + 0.12·e^(−n/4))`; `sd_t = 16.60·(1 + 0.04·e^(−n/4))·(E[T]/142)^0.5`; `rho_mt = 0.056`. Fit by Gaussian MLE on 2023-24 only. |
| joint | `game/joint.py` | Discretised bivariate normal in margin/total space, then an empirical endgame kernel (last 120 s), then OT recursion. Moment-matched to the targets. Zero tie mass. |
| storage | `game/forecast.py` | `ops/forecasts/<D>/<F>/game_pmfs.parquet`: compact float32 matrix (zlib), sha256-verified, lineage columns. About 2.5 MB per 57-game snapshot. |

## Validation (CBBD only; `docs/game_model/VALIDATION_CBBD_2026-11-18.json`)
Splits:

- development 2023-24 (season 2024): ratings and variance tuned
- selection 2024-25: β chosen
- frozen confirmation 2025-26: nothing tuned
- prospective 2026-27: production shadow

Prior exposure: the 2026-11-18 design prototype computed 2025-26 margin RMSE (12.69 / 12.52) as a diagnostic; no parameter was chosen on 2025-26.

| season | margin RMSE | margin CRPS | 95% cov | total RMSE | total CRPS | ML Brier | ATS hit vs close | O/U hit | market margin RMSE |
|---|---|---|---|---|---|---|---|---|---|
| 2023-24 (dev) | 11.45 | 6.43 | 0.949 | 16.83 | 9.40 | 0.183 | 50.8% | 50.1% | 11.20 |
| 2024-25 (sel) | 11.58 | 6.47 | 0.947 | 16.60 | 9.24 | 0.178 | 48.6% | 49.8% | 11.12 |
| 2025-26 (conf) | 11.69 | 6.55 | 0.949 | 17.36 | 9.73 | 0.180 | n/a: lockbox | n/a | n/a |

- Coherence: 100% of PMFs coherent in every season.
- **Conclusion:** the model is calibrated and coherent, but it is weaker than the closing line:
  - the no-vig close's moneyline Brier is 0.179 / 0.175, against the model's 0.184 / 0.180;
  - there is no ATS or O/U edge.
- Game markets therefore stay SHADOW: `IS_ACTIONABLE = FALSE`, `SELECTION_TIER = RESEARCH`, policy `GAME_MARKETS_SHADOW_RESEARCH_v1`.
- Release C needs prospective evidence that this changes, and its thresholds are chosen per market family out of sample.

The CBBD `/lines` pull for 2023-24 / 2024-25 was a one-off offline research fetch (raw responses kept outside the repo); the hash-pinned `adapters/cbbd.py` is unchanged.

Why there are no 2025-26 closes: CBBD `/lines` is not in the frozen lockbox opening plan (`lockbox.OPEN_PLAN_ENDPOINTS`). Amending that plan is a governance decision; this release does not make it.

## Production path
- **Snapshots:** `lifecycle.forecast` runs `game.forecast` right after the player PMFs, at the same instant, state and training cutoff. It is gated by `CBB_GAME_MODEL=SHADOW`, which the release units set, and it is failure-isolated.
- **Quotes:** `prod/game_quotes.py`. It has its own guard; the player-prop guard is unchanged. It runs:
  - a bulk `/odds` call for h2h, spreads and totals (3 credits per capture, every 30 min, every 10 min within 60 min of a tip);
  - an event `/odds` call for team totals and alternates (hourly, plus on near-tip captures).
- **Boards:**
  - `Player Props`: the market Full Board, one row per game × player × stat best offer.
  - `Game Lines`: one row per game × core market best offer, max EV over books × lines × sides.
  - The player-only supporting tabs follow.
- **Grades:** `Player Props`, `Game Markets` and `Summary` (separate PLAYER PROPS and GAME MARKETS blocks, split by market type).
- **Performance:** game markets live in a separate `CBB_Game_Performance_MM_DD_YYYY.xlsx`, which keeps the verified player Performance contract untouched.
- **Settlement:** OT-inclusive full game.
- **CLV statuses:** EXACT_LINE / SAME_LINE_NEAREST_CLOSE / LINE_MOVED (directional) / NO_CLOSE_QUOTE / NO_DEVIG_PAIR.
- **Health:**
  - `TEAM_RATINGS_FRESH`
  - `GAME_FORECAST_COMPLETE`: `MODEL_FAILURE: …` on a model fault
  - `GAME_MARKETS_STATE`: VALID_MARKETS / NO_MARKET_AVAILABLE / NOT_CAPTURED / CAPTURE_FAILED
  - The game layer is never part of `DAY_OPERATIONAL` while it is in SHADOW.
- **Lineage:** `game_exports.lineage` requires every game row to carry its player snapshot's run, fit, state and forecast id, one ratings state, and D's training cutoff.
- **Rollback:** set `CBB_GAME_MODEL=OFF`, or deploy the previous release. Either way the player outputs are byte-identical to Release A.
