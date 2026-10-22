# PTS RATE CHALLENGER — PROTOCOL ADDENDUM (implementation note; committed with the tau fit, before any evaluation)

The frozen protocol (`PTS_RATE_CHALLENGER_PROTOCOL.md`, 3e9e52c) is unchanged.

**Nothing below changes:**
- the formula;
- the tau bounds, grid or objective;
- the periods, strata or gates' thresholds.

## A1. Data premise "cur_min = 0 implies cur_pts = 0" is false on a few source rows

The protocol (section 2) states that `cur_min = 0` implies `cur_pts = 0`, so r_R1 = r_prior exactly. Inspection of the frozen PIT inputs (`marginal.load_frames`) found that this fails on a small number of rows:

| Season | Rows | Candidate rows | Description |
|---|---|---|---|
| 2022-23 | 9 | 273,032 | season-to-date minutes 0 and 2–3 points: source box scores that recorded points with 0 minutes |
| 2023-24 | 40 | 253,767 | as above |
| 2024-25 | 0 | — | — |
| 2025-26 | 1 | — | — |

**Implementation fix:**
- The first fit run used the "equivalent form" `r_prior + w (r_cur − r_prior)` with r_cur := r_prior when cur_min = 0. On those rows that form drops `cur_pts`, so it deviated from the primary protocol formula.
- That uncommitted fit record was discarded (kept as scratch evidence, never committed).
- The code now implements the PRIMARY formula `(cur_pts + tau r_prior) / (cur_min + tau)` on every row.
- Rows with cur_min = 0 AND cur_pts = 0 take its exact value r_prior directly, avoiding the float round trip tau·r/tau.
- The fit was re-run. It selected the identical grid point: tau = 74.2307 (i = 136), objective 2.833708 vs 2.833707 under the superseded form.

**Gate J clarification:**
- The "cur_min = 0 rows give R1 ≡ R0, bit-identical mu_1B" check is evaluated on the protocol's intended ZERO-EVIDENCE rows (cur_min = 0 and cur_pts = 0).
- The 0-minute-with-points rows are reported separately (`cur_min_zero_with_points_*`). R1 there equals r_prior + cur_pts/tau, which is bounded: at most +0.04 PTS/min over r_prior on the fit seasons.
- The literal reading of the check (all cur_min = 0 rows) would fail on exactly these source-data rows, and this is reported in the results.

## A2. Point-in-time audit of the current-season evidence (pre-evaluation)

For consecutive candidate rows of a player-season (F2024, F2025), the increment of the season-to-date minutes equals:
- the PREVIOUS game's minutes on 83,880 of 101,221 rows (2023-24) and 83,727 of 102,335 rows (2024-25);
- the TARGET game's own minutes on 72 and 78 rows, which is chance coincidence, the same order as unrelated equalities.

The remaining increments are lags of the feature snapshot (box score not yet available at TIP_MINUS_1H) or multi-game gaps, both conservative. No target-game information enters `cur_pts` / `cur_min`.

## A3. Compute

The stage A + B plumbing test (12 games, neutral tau = 1e9 so that R1 ≈ R0; no R1 result produced) measured about 0.15 s per game including the 20% combo subsample. That is about 14 minutes per season, single-threaded at nice 15, slightly above the protocol's ~12-minute estimate.
