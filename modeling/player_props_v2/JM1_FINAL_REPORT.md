# CBB V2 — JM1_FINAL: Locked Retrospective 2025-26 Confirmation

**Decision: `PASS` — all 10 frozen gates passed.**
- JM1_FINAL becomes the selected predictive architecture, subject to architect review.
- Nothing is deployed.
- The final refit through 2025-26 has **not** been run.

| Item | Value |
|---|---|
| Postmortem commit | `ac84ff4` |
| Protocol commit | `101dc64` (`jm1_final_protocol.json`, sha256 `1735a12a8842b92057d6e57f9334a2b3218b76cc2173282cb37ce1a318c34cd9`) |
| Code since freeze | unchanged (`code_changed_since_protocol: []`) |
| Architecture matches the development JM1 arm | **TRUE**: bit-identical reproduction of the saved 2024-25 JM1 and S0 values (12 games, 240 player-games, max relative difference 0.0) |
| Confirmation season | 2025-26: **5,748 games**, 112,334 scored player-games, 20,000 draws per game |
| Chronology | structure through 2024-25; GLM seasons 2023–2025; residual basis F2024 (parameters through 2023) + F2025 (parameters through 2024); Phase 1B forecasts trained through ≤ 2025; S0 = Phase 2 J2 fit through 2024-25 |
| Nature | locked retrospective confirmation. 2025-26 results of other models were seen earlier in the project; no JM1 result on 2025-26 had been computed before this run |

## What JM1_FINAL is

JM1_FINAL is the existing JM1 development arm, frozen without changes:
- Phase 1B P(play) is unchanged.
- Each scored player's minutes in each draw are one **independent** draw from his Phase 1B conditional-minutes marginal, scaled by D / E[D] (D = shared game duration).
- That one minutes draw feeds all seven stats through the GLM: log-minutes ratio, pace, and own and opponent efficiency, with α = 1.0.
- Dispersion is refit after the random minutes. Residual J2 is refit on true out-of-sample structural residuals.
- Combos are built from the same stat draws.

It has none of the following:
- no team-minutes budget;
- no reservoir;
- no reconciliation;
- no opportunity redistribution;
- no named-player-pair parameters.

## Gates

All are strict: a gate passes only if its 95% interval bound clears the margin.

| Gate | Result | Evidence |
|---|---|---|
| C1 aggregate superiority | PASS | MARG_LOG −0.0186 [−0.0191, −0.0180]; MARG_CRPS −0.0020 [−0.0021, −0.0019] |
| C2 every stat non-inferior (log and CRPS) | PASS | no failures |
| C3 every stat × tier | PASS | no failures; worst upper bound is FG3M × BENCH at +0.0010 |
| C4 every stat × position (G / F / C) | PASS | no failures; worst upper bound is FG3M × C at +0.0015 |
| C5 minutes exposure not degraded | PASS | overall +0.0018 [+0.0016, +0.0021]; tiers +0.0047 / +0.0005 / −0.0010 |
| C6 PTS spread, bench and rotation | PASS | bench 1.391 → 1.095; rotation 1.275 → 1.126 |
| C7 joint non-inferior | PASS | ES −0.0148 [−0.0169, −0.0128]; VS_BAL −0.00069; both more than 150 Monte Carlo SE inside their margins |
| C8 both top scorers over | PASS | Brier −0.0037 [−0.0045, −0.0029]; calibration gap 0.0750 → 0.0603 |
| C9 integrity / point-in-time / chronology | PASS | 0 combo identity violations, 0 invalid matrices, 0 point-in-time violations (11,504 team rows), chronology OK |
| C10 determinism | PASS | identical hashes |

## Player stats (S0 → JM1_FINAL; Δ with 95% CI)

| Stat | Log score | Δ log | Δ CRPS | Δ Brier | Bias S0 → JM1 | 80% PIT coverage | Latent scale λ |
|---|---|---|---|---|---|---|---|
| PTS | 2.8796 → 2.7851 | −0.0945 [−0.0970, −0.0919] | −0.0057 | −0.0011 | +0.033 → **+0.212** | 0.692 → 0.745 | 1.225 → 1.095 |
| REB | 2.0142 → 1.9953 | −0.0189 | −0.0025 | −0.0003 | +0.118 → +0.045 | 0.776 → 0.798 | 1.051 → 1.003 |
| AST | 1.3798 → 1.3727 | −0.0071 | −0.0027 | −0.0010 | +0.010 → +0.023 | 0.794 → 0.805 | 1.017 → 0.982 |
| FG3M | 1.0160 → 1.0146 | −0.0014 [−0.0021, −0.0008] | −0.0001 (n.s.) | −0.0000 (n.s.) | −0.008 → +0.016 | 0.800 → 0.808 | 1.008 → 0.973 |
| STL | 1.0213 → 1.0199 | −0.0014 | −0.0005 | −0.0001 | +0.027 → +0.013 | 0.803 → 0.802 | 0.990 → 0.990 |
| BLK | 0.6451 → 0.6431 | −0.0020 | −0.0008 | −0.0002 | +0.016 → +0.007 | 0.801 → 0.802 | 1.006 → 1.001 |
| TOV | 1.2886 → 1.2839 | −0.0047 | −0.0020 | −0.0008 | +0.044 → −0.001 | 0.794 → 0.801 | 1.012 → 0.996 |

**PTS log score by role**

| Role | Δ log |
|---|---|
| TOP5 | −0.0097 |
| ROTATION | −0.0877 |
| BENCH | −0.2117 |

**PTS log score by position**

| Position | Δ log |
|---|---|
| G | −0.090 |
| F | −0.092 |
| C | −0.111 |

## Minutes exposure (S0 / Phase 1B → JM1_FINAL)

| Stratum | n | CRPS (Δ) | MAE | RMSE | Bias | 80% / 90% coverage |
|---|---|---|---|---|---|---|
| Overall | 112,334 | 3.4706 → 3.4725 (+0.0018) | 4.917 → 4.917 | 6.279 → 6.280 | +0.073 → +0.073 | 0.842 / 0.922 → 0.844 / 0.924 |
| TOP5 | 48,758 | 3.3517 → 3.3564 (+0.0047) | 4.695 → 4.696 | 6.075 → 6.075 | −0.113 → −0.113 | 0.853 / 0.925 → 0.857 / 0.928 |
| ROTATION | 26,775 | 3.7812 → 3.7817 (+0.0005) | 5.357 → 5.357 | 6.701 → 6.701 | −0.063 → −0.063 | 0.823 / 0.916 → 0.825 / 0.917 |
| BENCH | 36,801 | 3.4023 → 3.4013 (−0.0010) | 4.889 → 4.889 | 6.229 → 6.229 | +0.418 → +0.418 | 0.841 / 0.923 → 0.841 / 0.924 |
| Early season (≤ 2 games) | 8,516 | 4.1935 → 4.1921 (−0.0014) | 5.904 → 5.904 | 7.456 → 7.456 | +0.249 → +0.249 | 0.764 / 0.865 → 0.766 / 0.866 |
| November | 24,963 | 3.7166 → 3.7169 (+0.0003) | 5.262 → 5.262 | | +0.192 → +0.191 | |

The only differences are small, simulation-only effects: the sampled marginal and the D / E[D] scaling. Mean, MAE and RMSE are unchanged.

## Joint results

| Metric | S0 → JM1_FINAL | Δ (95% CI) |
|---|---|---|
| Energy score | 6.8173 → 6.8025 | −0.0148 [−0.0169, −0.0128] |
| VS_BAL | 0.2427 → 0.2420 | −0.0007 [−0.0008, −0.0006] |
| Joint-event Brier | 0.1299 → 0.1289 | −0.0010 |
| Joint-event log | 0.4121 → 0.4088 | −0.0033 |
| PRA | log 3.1917 → 3.1248; CRPS 3.6310 → 3.5854 | log −0.0670; CRPS −0.0456 |
| PR | log −0.0708; CRPS −0.0414 | |
| PA | log −0.0798; CRPS −0.0417 | |
| RA | log −0.0185; CRPS −0.0108 | |
| STOCKS | log −0.0025; CRPS −0.0012 | |

**Combo threshold Brier**
- At q25, every combo improves (−0.0006 to −0.0052).
- At q75, PRA/PR/PA are **worse**: +0.0009 / +0.0008 / +0.0006.
- At q50, PR is worse (+0.0005).

**Correlation calibration RMSE (slope)**

| Level | S0 | JM1_FINAL |
|---|---|---|
| Within player | 0.0526 (1.26) | 0.0138 (0.96) |
| Cross team | 0.0042 (1.09) | 0.0023 (0.99) |
| Same team | 0.0051 (1.23) | **0.0161 (0.37)** |

**Joint tails.** All 6 tail families have Brier ≤ S0.

**Event families with worse Brier than S0**
- OP_REB_REB: +0.0004 [+0.0000, +0.0009]; calibration gap −0.007 → +0.011.
- SP_AST_TOV: +0.0003 [+0.0001, +0.0006].

## Both top scorers over (team-games n = 11,493)

| Arm | Predicted | Calibration gap | Brier | Log loss |
|---|---|---|---|---|
| Observed rate | 0.2937 | | | |
| S0 | 0.2186 | 0.0750 | 0.21304 | 0.62078 |
| JM1_FINAL | 0.2333 | 0.0603 | 0.20932 | 0.61093 |

The log loss change is Δ −0.0099 [−0.0121, −0.0078]. Result: **improved**; it repeats the 2024-25 result.

## Old Phase 2 J2 vs JM1_FINAL residual J2 (mean |ρ|)

| Level | Old J2 | Residual J2 |
|---|---|---|
| Within player | 0.181 | 0.059 |
| Same team | 0.0106 | 0.0129 |
| Cross team | 0.0081 | 0.0070 |

**Within-player pairs (old → residual)**

| Pair | Old | Residual |
|---|---|---|
| PTS–REB | 0.375 | 0.080 |
| PTS–AST | 0.212 | −0.056 |
| REB–AST | 0.230 | 0.040 |
| REB–BLK | 0.208 | 0.052 |
| PTS–TOV | 0.143 | 0.015 |
| PTS–FG3M | 0.737 | 0.672 |

Explicit player-minutes uncertainty again explains about two-thirds of within-player dependence. It explains none of the teammate dependence.

## Protected-category regressions (none breaches a gate)

| Category | Metric | Δ (95% CI) | n | Class |
|---|---|---|---|---|
| PTS mean, all | bias | +0.033 → +0.212 points | 112,334 | SMALL_BUT_CLEAR (mean over-forecast) |
| BENCH PTS | mean MSE (scaled) | +0.0073 [+0.0066, +0.0081] | 36,801 | SMALL_BUT_CLEAR |
| BENCH FG3M | CRPS / Brier | +0.0007 / +0.0003 | 36,801 | SMALL_BUT_CLEAR |
| PRA / PR / PA | threshold Brier at q75 (overs region) | +0.0009 / +0.0008 / +0.0006 | 5,748 games | SMALL_BUT_CLEAR |
| Same-team pairs | correlation calibration | RMSE 0.0051 → 0.0161; slope 0.37 | — | MATERIAL (diagnostic, not gated) |
| OP_REB_REB, SP_AST_TOV | event Brier | +0.0004 / +0.0003 | | SMALL_BUT_CLEAR |
| TOP5 minutes | CRPS | +0.0047 [+0.0043, +0.0051] | 48,758 | simulation-only (MAE and RMSE unchanged) |

## Development evidence (2024-25, descriptive)

The JM1 arm scored as follows on the saved 2024-25 run:

| Metric | Δ vs S0 |
|---|---|
| Aggregate MARG_LOG | −0.0185 |
| PTS log | −0.0944 |
| Energy score | −0.0129 |
| PRA CRPS | −0.0457 |
| Both top scorers over, Brier | −0.0031 |
| Minutes CRPS | +0.0017 |

PTS λ for bench / rotation / TOP5 went from 1.393 / 1.284 / 1.046 to 1.089 / 1.134 / 1.064.

Applied to 2024-25, the gate preview passed all of C1–C10. It is recorded in `jm1_final_development_evidence.json`. That was selection evidence, not confirmation.

## What happens next

- **On approval:** the selected JM1_FINAL procedure is refit **through the end of 2025-26** for 2026-27 production. That refit is a separate operation and was not run here.
- **Two items for architect review before production:**
  - the PTS mean over-forecast (+0.21 points; anchor α = 1.0 with log-minutes coefficient 1.30);
  - the PRA/PR/PA q75 Brier regressions.
- **Integrity:** 0 historical API calls; the user QA file is unchanged.
