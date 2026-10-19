# CBB V2 — JointMinutesV2 Rejection Postmortem

This postmortem is **read-only**. It uses only the saved evidence from the single frozen JM2 run on 2024-25. Nothing was simulated, nothing was refit, and 2025-26 was not touched.

The full numbers are in `jm2_postmortem.json`.

## Outcome

| Field | Value |
|---|---|
| Formal decision | **`REJECT`** |
| Rejection type | **`A — CORE_MODEL_FAILURE`** |
| Bug found | **`FALSE`** |
| Joint-minutes mechanism validated | `PARTLY` |
| Final joint-minutes challenger justified | `NO` |
| Smallest correctable change | `NONE` |
| Original S2 catastrophe fixed | `PARTLY` |
| Where JM2 failed | `RECONCILIATION` (hard budget × independent Phase 1B participation) |
| Distortion of Phase 1B minutes | `MODERATE` |

Provenance:
- **Protocol:** `310acce` (`joint_minutes_v2_protocol.json`).
- **Development record:** `305b0f3`.
- **Report:** `8d89121`.
- **Selected arm:** `JM2_A05` (α = 0.5).

## Formal failure

All gates pass except one: **G3 RESERVOIR_HEAVY**.
- It covers scored player-games where the pregame expected reservoir E[R] ≥ 10.
- That is 2,120 player-games across 272 team-games.
- Minutes CRPS: S0 5.4971, JM2 5.5595.
- Δ = **+0.0623 [+0.0282, +0.1028]**, against a rule of upper bound ≤ +0.05.

## Exact reason

The heavy rows are team openers, almost all in November.

**What went wrong:**
- On these rows, hard reconciliation pushed the minutes of known players who played **down by 0.75 minutes**.
  - Bias went from −0.31 (S0) to −1.05 (JM2).
  - MAE rose by 0.088.

**Why:**
- On these teams, Phase 1B P(play) expects 8.48 known participants, but only 7.65 played.
- The 5 × D minutes budget spreads the known players' share over too many expected participants, so each player who does play is squeezed.

**What was not the cause:**
- The reservoir level was roughly calibrated: 44.8 predicted vs 52.3 realized in openers.

**Where it is worst:**
- Rows where E[R] was large but little was actually missing: 582 rows, bias −2.11, CRPS +0.130.

The same mechanism affects every stratum:
- JM2 shifts players with P(play) > 0.97 down by 0.26 minutes, even though S0 is unbiased for them (−0.03).
- Established starters (WARM TOP5): MAE +0.026, 80% coverage 0.839 → 0.815, variance ratio 0.87.
- Rotation minutes: +0.0137 CRPS.
- Candidate-surplus teams: +0.105 CRPS (not gated).

## What JM2 did achieve

**Fixed the S2 catastrophe.** On rows from teams missing ≥ 20 minutes, S2 lost +0.95 CRPS. JM2 is at +0.009 [−0.021, +0.042].

**Completeness and the reservoir:**
- The universe is unchanged (0 legal point-in-time additions): 99.28% of realized minutes, 95.6% of complete team-games.
- In aggregate the reservoir was calibrated: predicted 1.27 vs realized 1.46 minutes per team-game, and P(R > 0) 0.045 vs 0.044.
- It is too weak on the most incomplete teams: budget deficit > 60 gives 52.5 predicted vs 73.7 realized.

**Large stat and joint gains.** Every stat and every stat × tier improves:

| Metric | Change |
|---|---|
| PTS log score | −0.098 |
| Energy score | −0.0125 |
| PRA CRPS | −0.052 |
| BOTH_TOP_SCORERS_OVER | Brier −0.0025, log loss −0.0070 |

**Nearly all of those gains come from random minutes, not reconciliation.**

| Metric | JM1 − S0 (random minutes, no reconciliation) | JM2 − JM1 (what reconciliation adds) |
|---|---|---|
| PTS log score | −0.0944 | −0.0038 |
| Energy score | −0.0129 | +0.0004 (n.s.) |
| Event Brier | — | +0.0001 (worse) |
| PRA CRPS | — | −0.0063 |

**Residual J2 shrinks:**

| Level | Old J2 | Residual J2 | Reading |
|---|---|---|---|
| Within player | 0.175 | 0.057 | strong structural signal |
| Same team | 0.0102 | 0.0125 | none |

**Dependence and dispersion:**
- Tier-level minutes dependence: MIXED. RMSE 0.044 vs 0.074 under independence; ROT–ROT and ROT–BENCH are wrong.
- Dispersion refit: MIXED. PTS is still under-dispersed (latent scale 1.11); bench FG3M and AST are slightly over-dispersed.

## Bug check

None found:
- The reservoir labels add up correctly: within 1 minute on 99.7% of team-games.
- Accounting is exact.
- JM1 and JM2 share the same refit.
- Activation equals P(play), with 0 repairs.
- Determinism holds.
- The code is unchanged since the protocol freeze.

## Architect ruling (2026-10-19)

- JM2 stays **REJECTED**.
- The hard reconciliation / reservoir mechanism is **not promoted**.
- No JM3 is authorized.
- S0 / Phase 2 J2 stays champion unless a new candidate earns promotion.
- Exactly **one** candidate is authorized: **JM1_FINAL**.
  - It is random player-minutes exposure plus conditional dispersion refit plus residual J2 — the existing JM1 development arm, frozen as-is.
  - It gets one locked retrospective 2025-26 confirmation.

**Why JM1 is advanced.** JM1 already captured nearly all of JM2's predictive benefit:
- Random minutes improved player-stat marginals.
- PTS spread calibration improved strongly for bench and rotation players.
- One sampled minutes draw per player feeds all seven stats.
- Within-player residual J2 shrank substantially.
- Reconciliation added no energy-score improvement and slightly worsened joint-event Brier.
- JM2's failure came from the cross-player budget mechanism, not from random exposure.

**How post-hoc selection is guarded against.** 2024-25 is development data, so choosing JM1 on it is allowed. The protection is:
1. Freeze JM1 now.
2. No more 2024-25 tuning.
3. One 2025-26 confirmation.
