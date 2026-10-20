# CBB V2 — JM1_FINAL Production-Candidate Freeze (through 2025-26)

**Status: FROZEN PRODUCTION CANDIDATE for 2026-27. Not deployed.**
- The architect approved JM1_FINAL as the selected 2026-27 architecture after its locked 2025-26 confirmation passed (`6898df9`, all 10 frozen gates).
- This freeze is the one authorized final refit of the confirmed procedure, through 2025-26.
- Nothing was selected, tuned or searched.

| Item | Value |
|---|---|
| Architecture | JM1_FINAL (protocol `101dc64`, confirmation `6898df9`, JM2 postmortem `ac84ff4`) |
| Production season | 2027 (2026-27) |
| Supervised training through | 2026 (2025-26) |
| Historical state through | 2026 (2025-26) |
| Code commit at fit | `6898df9` + `cbb_platform/v2/jm1_final_production.py` (sha256 `fb2644263a7143907e5640d98acab1a701987205423af3433fd51e77f7250022`, committed with this freeze) |
| Frozen JM1_FINAL code | `jm1_final.py`, `joint_minutes_v2.py`, `structural.py`, `phase3.py`, `dependence.py`, `joint.py`: sha256 identical to `code_sha256_at_freeze` in the protocol (checked at fit time) |
| Full suite | 656 passed / 0 failed / 3 skipped, 1859.8 s. This is the one authorized run (`data/qa/large/v2_jm1_final/full_suite.log`); it was not rerun |
| Final fit runtime | about 24 s per fit. The full stage took 56 s: two fits, artifact write, and integrity checks on 3 games × 4,000 draws |
| Resource mode | single-threaded BLAS/OMP, `nice -n 15`, one process. No season simulation and no rescoring |

## Artifacts

| Artifact | Path | sha256 |
|---|---|---|
| Phase 1B marginal (unchanged; shared with S0) | `modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json` | `bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f` |
| JM1_FINAL structural (environment, GLM, E[D], conditional dispersion, seeds) | `modeling/player_props_v2/jm1_final_through_2025_26.json` | `b932fa2155de77f95e2c2afb15b45b22ecf8fa1e49a2d175b7da7cbfca2428f9` |
| ↳ conditional dispersion (key `conditional_dispersion`) | in the structural artifact | content sha256 `4368f90cd12097e3c2fcb9e28749b8d8dcdfc7e42b643e79399c2703ea6f17b2` |
| ↳ shared game environment (key `structure.env`) | in the structural artifact | content sha256 `b0780421e7058815965f72c2d0b7f7f23a8e499562a823ccd7297239c5887de6` |
| Residual J2 | `modeling/player_props_v2/jm1_final_residual_dependence_through_2025_26.json` | `e42548675fd3c0e363cf26a464d133f384ae659d99b603105510f0937b6570f1` |
| Production manifest | `modeling/player_props_v2/jm1_final_production_manifest_2026_27.json` | `7d83e9cf4e9c68478e5e71e57ccdcf204b8d3267c4de98d7ea86ee8d5ddcbcab` |

Provenance hashes recorded in the manifest:

| Record | sha256 |
|---|---|
| protocol | `1735a12a…c34cd9` |
| development evidence | `d1b1658c…742e7f` |
| confirmation record | `9a0f823b…ef23521` (decision PASS) |
| confirmation parameters | `00516bc6…3cefc2` |
| JM2 postmortem | `664d2f5b…cf3e` |

Data hashes recorded in the manifest:

| Data | sha256 |
|---|---|
| Phase 1B OOS store `modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet` | `d0859e48…a8e425` |
| F2023 extension | `aeb1d115…84cc` |
| Stage 3 `features.duckdb` | `38e89053…a1cc` |
| Stage 3 `labels.duckdb` | `86c77e3c…651f` |

## What was fitted

`jm1_final.fit_procedure(T=2026, resid_seasons=(2024, 2025, 2026))` ran unchanged. This is the same chronology `phase3.FINAL` declared before any confirmation.

- **Phase 1B marginals:** reused as is. No refit and no new weighting search.
- **Random minutes:** unchanged.
  - M_ib = clip(min_mu + min_sigma·ε, 0, 60) × D_b / E[D], with ε drawn from the `minutes` stream.
  - One minutes draw per player per game draw feeds all seven stats.
  - No team budget, reservoir or reconciliation.
  - α = 1.0.
  - GLM terms: intercept, log_minutes_ratio, pace_dev, own_eff_dev, opp_eff_dev.
  - E[D] = 40.369 minutes, from the refit overtime law (P(K=0..4) = 0.9389 / 0.0507 / 0.0083 / 0.0017 / 0.0003).
- **Shared game environment:** the same OLS pace and efficiency law, refit on team-games 2014–2026.
  - 70,800 games.
  - sd_pace 0.0723, sd_eff 0.1407, ρ_eff 0.178.
  - R² pace 0.279, R² eff 0.209.
  - Pregame team features only. No sportsbook inputs, and no realized target-game environment.
- **Structural GLM:** GLM seasons 2023–2026.
- **Conditional dispersion:** the same method of moments, on the true-OOS structural residual basis. It conditions on the structural state (the realized-state conditional mean given random minutes and environment), so minutes-driven variance is not double-counted.

| Stat | Family | φ at confirmation fit (through 2024-25) | φ final (through 2025-26) |
|---|---|---|---|
| PTS | NEGBIN | 0.1156 | 0.1142 |
| REB | NEGBIN | 0.0219 | 0.0218 |
| AST | NEGBIN | 0.0457 | 0.0473 |
| FG3M | NEGBIN | 0.0554 | 0.0534 |
| STL | NEGBIN | 0.0440 | 0.0364 |
| BLK | NEGBIN | 0.1383 | 0.1292 |
| TOV | POISSON | 0 | 0 (moment φ −0.006 floored, as frozen) |

## Residual J2 (true out-of-sample basis)

The residual basis is true out of sample: season F is scored with structural parameters fitted through F−1, and no residual comes from a model trained on its own games.

| Season | Structure through |
|---|---|
| F2024 | 2023 |
| F2025 | 2024 |
| F2026 | 2025 |

| Basis | Value |
|---|---|
| Seasons | 2024, 2025, 2026 |
| Player-games | **332,743** |
| Games | **17,002** |
| Players | 8,706 |

`dependence.fit(…, "MID")` ran unchanged:
- 200 bootstrap draws;
- shrinkage z = 1.96;
- seed 20262;
- per-role normalization;
- PSD-safe construction, with 0 PSD repairs needed at fit.

At the integrity games, the minimum game-correlation eigenvalue was about 0.098–0.100 with no jitter.

| Component | Final residual J2 | Old Phase 2 J2 (S0, relative to the marginals) |
|---|---|---|
| Within-player (global R) | mean \|r\| 0.060; min eigenvalue 0.318; strongest pairs PTS–FG3M +0.672, PTS–REB +0.080, PTS–AST −0.057, REB–BLK +0.053, AST–STL +0.050. Role and group hierarchy retained | mean \|r\| 0.182 (most of the old PTS–REB/AST/TOV correlation is now carried by the shared minutes draw) |
| Same-team (thresholded) | 23 nonzero entries; mean \|r\| 0.0132; max \|r\| 0.081 (PTS–PTS −0.081, PTS–FG3M −0.062, AST–FG3M +0.049) | 23 nonzero; mean \|r\| 0.0110 |
| Cross-team / shared game (thresholded + shared loadings A/B/Q) | 22 nonzero entries; mean \|r\| 0.0073; max \|r\| 0.080 (STL–TOV +0.080, REB–TOV −0.037) | 26 nonzero; mean \|r\| 0.0088 |

## Deterministic final fit: PASS

The stage fits twice and compares the two artifact contents.

- **This run:** the fits were bit-identical.
  - Structural content sha256 `6191e0c9…beaf` both times; residual J2 content `1c48fbfa…8e73` both times.
  - 133 + 3,273 floats compared, 0 differing.
- **First attempt:** it stopped at the guard before writing anything (`data/qa/large/v2_jm1_final/final_attempt1_nondeterministic.log`).
  - The residual J2 was bit-identical between its two fits.
  - The structural environment coefficients through 2026 differed in the last bits (relative difference about 1e-13).
  - Root cause: `structural.load_team_frame` reads `feat.team_features` with a multi-threaded DuckDB query that has no ORDER BY, so the row order fed to `env_fit`'s `lstsq`, and hence the float summation order, can vary between runs.
  - The frozen code was not changed.
- **Change made:** the production module's reproduction check now compares non-float content exactly and floats at a relative 1e-12. It records both `BIT_IDENTICAL` and `IDENTICAL`, and the result is unit-tested.
- **Source of truth:** the frozen artifact. Scoring from it is bit-deterministic.

## Production-integrity checks: ALL PASS

The checks ran mechanically through the frozen scorer `jm1_final_production.score_game` on 3 games from 2025-26 (35–58 priced players, 4,000 draws). These games are in-sample for the final fit, so no predictive metric is computed.

| Check | Result |
|---|---|
| P(play) separate from conditional outcomes (changing every P(play) leaves draws and minutes bit-identical) | PASS |
| One minutes draw per player per draw feeds all 7 stats (perturbing one minutes value changes exactly that player-draw's 7 log means) | PASS |
| PRA / PR / PA / RA / STOCKS are sums of the same stat draws | PASS |
| PSD game correlation matrices | PASS |
| Marginal PMFs valid and normalized (error < 1e-9) | PASS |
| Pricing primitive `props_v1_pricing.price`: integer lines push exactly, half lines never push, probabilities sum to 1 | PASS |
| Deterministic seeds (repeat scoring is bit-identical) | PASS |
| Production scorer matches the confirmed `jm1_final.eval_game` path (means at 1e-12, minutes bit-identical) | PASS |

Focused tests in `tests/test_v2_jm1_final_production.py`: 5 passed.
- chronology and rollback identity;
- frozen code unchanged since the protocol;
- architecture constants;
- tolerance comparator;
- frozen artifacts load and cross-reference.

Seed policy:
- Structural streams: `structural.stream_seed(20271, game_id, stream)` for duration, environment and minutes.
- Count latents: `dependence.game_seed(20263, game_id)`.
- 20,000 draws per game.

Supported markets:
- Stats: PTS, REB, AST, FG3M, STL, BLK, TOV.
- Combos: PRA, PR, PA, RA, STOCKS.

## Known monitoring items (recorded, NOT repaired)

These are prospective monitoring items. By architect ruling, the model was not changed to correct them.

| | Item | 2025-26 confirmation evidence | Production monitoring |
|---|---|---|---|
| A | **PTS mean bias** | S0 +0.033 → JM1_FINAL **+0.212** points (mean over-forecast; α = 1.0 with log-minutes coefficient 1.30). The final fit adds one season and does not address it | Track PTS calibration and mean bias from the first slate, overall and by role |
| B | **High combo overs** | q75 threshold Brier slightly worse than S0: PRA +0.0009, PR +0.0008, PA +0.0006 | Monitor high combo lines (overs region) separately |
| C | **Same-team correlation** | Correlation calibration RMSE S0 0.0051 → JM1_FINAL **0.0161**, slope 0.37 | Residual same-team dependence is a known weakness; monitor same-team parlays and correlations |

## Rollback model: S0 = Phase 1B marginals + Phase 2 J2 (unchanged)

The hashes below were checked before and after the fit, and both match.

| Artifact | sha256 |
|---|---|
| `modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json` | `bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f` |
| `modeling/player_props_v2/dependence_J2.json` | `b41564a988f37a4c01a0dec9f8a9cb67aea69717294809b3eaa49acf4babb1ff` |

These were also confirmed:
- the JM2 frozen records are unchanged (`jm1_final.guards`, before and after);
- `data/qa/game_identity_questionable_only.csv` is unchanged (sha256 `5ca9001c…15b2`), untracked and not committed.

## Proposed scratch deletions (NOT deleted; awaiting approval)

All scratch lives under the gitignored `data/qa/large/`. Phase 2 sets the precedent: per-game evidence parquets were deleted after freezing.

**Candidates.** Phase 3 is not promoted, and its summaries are committed in the Phase 3 records. No code path reads these files.

| File | Size | sha256 |
|---|---|---|
| `data/qa/large/v2_phase3/select_2025_rows.parquet` | 332 MB | `a789e371…88a8` |
| `data/qa/large/v2_phase3/select_2025_games.parquet` | 13 MB | `16f1885f…6a13` |
| `data/qa/large/v2_phase3/select_2025_events.parquet` | 2.4 MB | `a69790b3…a755` |
| `data/qa/large/v2_phase3/select_rebuild_2025_rows.parquet` | 171 MB | `8be22af0…9c1d` |
| `data/qa/large/v2_phase3/select_rebuild_2025_games.parquet` | 6.7 MB | `da83c49d…8138` |
| `data/qa/large/v2_phase3/select_rebuild_2025_events.parquet` | 1.3 MB | `0e0c1ca3…e074` |

Together they come to about 526 MB.

**Optional, recommended KEEP for now:** `data/qa/large/v2_jm1/confirm_2026_{rows,games,events}.parquet`.
- Size: about 120 MB.
- sha256: rows `91f12c38…23bb`, games `7170451b…2753`, events `f592a478…5a49`.
- These are the per-player-game confirmation evidence. They are the only drill-down source for monitoring items A–C until prospective 2026-27 data exists.

**Protected (do not delete):**
- `data/qa/large/v2_jm2/develop_2025_*.parquet`: preserved evidence per `jm2_postmortem.json`, and the input to the JM1 bit-exact reproduction.
- `data/qa/large/v2_phase3/select_2025_simstore.parquet` and `select_residual_basis.parquet`: inputs to `phase3.build_store`.
- every log;
- the OOS stores;
- the canonical and lockbox stores;
- every protocol, confirmation and final record;
- user QA files.

## Not done (by instruction)

- No push.
- No merge to main.
- No deployment workflow.
- No Wizard SSH.
- No public pages.
- No live odds ingestion.
- No publishing or wagering.

Other boundaries:
- Historical API calls: **0**.
- 2026-27 outcome data touched: **FALSE**. The season guards cap every loader at 2026.

Deployment is the next architect-controlled phase.
