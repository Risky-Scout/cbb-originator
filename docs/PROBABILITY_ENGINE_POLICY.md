# PROBABILITY_ENGINE_POLICY.md
## CBB Originator - Distribution Authority, Pricing, and Simulation Policy
### Version 1.0 - October 4, 2026

**Status:** FROZEN ENGINEERING CONTRACT FOR IMPLEMENTATION AND VALIDATION

## 1. Purpose

This policy prevents exact probability calculations, production Monte Carlo integration, and interactive simulation from being confused or used interchangeably.

The model has one authoritative probability for a given information state and market. The method used to calculate that probability depends on whether the fitted distribution is exactly enumerable.

## 2. Distribution authority hierarchy

### Case A - Exact PMF/CDF is available

Examples include a discrete score/margin/total distribution that can be summed directly.

**Authoritative pricing method:**

`P(event) = direct sum of exact PMF/CDF mass satisfying the event condition`

Rules:

- exact PMF/CDF probabilities are the public and production source of truth;
- no finite Monte Carlo sample may replace an exact probability;
- custom spreads, totals and team totals must be computed from the exact distribution;
- simulation may be used only to verify the sampler or to provide an educational visualization;
- a low-N simulated PMF must never be displayed as an equal alternative to the exact PMF.

### Case B - Full joint distribution is not tractably enumerable

This is likely the normal case for the high-dimensional CBB interconnected player-prop generative model.

**Authoritative pricing method:**

a convergence-qualified Monte Carlo representation of the fitted probabilistic model.

Rules:

- the fitted event/rotation/lineup models determine probabilities; Monte Carlo is the numerical integration mechanism;
- the production draw bank must be generated once per information state/version and reused for pricing all supported player/game/joint queries;
- custom-line queries read from the production draw bank; they do not launch a new small simulation;
- published probability records must include `probability_method = converged_mc`, draw count, seed/reference, and Monte Carlo uncertainty/convergence metadata;
- simulation count is adaptive or otherwise large enough to satisfy frozen convergence requirements.

### Case C - Interactive Simulation Laboratory

This is a user-facing or analyst-facing sample of possible games.

Rules:

- it is never the authoritative pricing source;
- it must be clearly labeled as containing Monte Carlo sampling noise;
- it must show the number of draws;
- it must not silently overwrite or compete with the official probability;
- when an exact probability exists, display that value separately as the authoritative benchmark.

## 3. Custom-line pricing

For a home-margin random variable `M = HomeScore - AwayScore` and home spread `S`:

- home cover: `P(M + S > 0)`
- push: `P(M + S = 0)`
- away cover: `P(M + S < 0)`

For total score `T_game` and line `L`:

- over: `P(T_game > L)`
- push: `P(T_game = L)`
- under: `P(T_game < L)`

For team total `Y_team` and line `L`:

- over: `P(Y_team > L)`
- push: `P(Y_team = L)`
- under: `P(Y_team < L)`

If an exact PMF exists, these are direct sums. If the full joint model is simulation-based, these are empirical probabilities from the **production draw bank**, not a new low-N sample.

## 4. Monte Carlo uncertainty

For a binary event with estimated probability `p_hat` from `N` independent effective draws:

`MCSE ~= sqrt(p_hat * (1 - p_hat) / N)`

The implementation must freeze publication thresholds after performance benchmarking. At minimum:

- MCSE or an equivalent confidence interval must be computable for simulation-based published probabilities;
- no official probability may be published if the convergence gate fails;
- effective sample size must be used if draws are weighted or dependent;
- rare-event/tail pricing may require substantially larger `N` or variance-reduction methods.

## 5. Exact-versus-simulation validation

Whenever an exact PMF/CDF exists and a sampler is implemented from it:

1. run a fixed-seed high-N diagnostic (normally >=100,000 draws; increase when needed);
2. compare key probabilities, marginals, CDFs, totals and margins;
3. for binary probabilities, require the discrepancy to be consistent with Monte Carlo error under a frozen tolerance;
4. calculate distribution-level diagnostics such as maximum absolute CDF error and total-variation distance;
5. do not judge tiny PMF bins using raw relative percentage differences from a small sample;
6. any persistent discrepancy outside tolerance is a **BLOCKING DEFECT**, not a UI issue.

Potential defect classes include:
- support truncation;
- incorrect normalization;
- score/index mapping;
- home/away sign convention;
- push inequality error;
- overtime handling;
- rounding/line-threshold handling;
- stale/incorrect distribution version;
- sampler implementation bug.

## 6. Public presentation

### Exact/Production Pricing section

Show:
- authoritative probability;
- fair price;
- line interpretation in plain language;
- push probability where applicable;
- probability method only if useful to advanced users.

Do not force users to parse algebra to understand a spread. Example:

`Hawai'i -3.5: Hawai'i covers with a win by 4 or more.`

### Simulation Laboratory section

Show:
- simulation count;
- clear sampling-noise disclaimer;
- optional empirical outcomes/histograms;
- no language implying the interactive sample is more current or more correct than production pricing.

### Exact-vs-simulation comparison

Default location: **internal diagnostics**, not the normal public page.

If exposed publicly for educational purposes:
- exact probability must be visibly labeled authoritative;
- simulated estimate must show N and uncertainty interval;
- differences must be explained as expected sampling error when inside tolerance.

## 7. Storage requirements

Every official probability record must store:

- `prediction_id`
- `model_version`
- `data_cutoff_time`
- `distribution_version`
- `probability_method`: `exact_pmf` or `converged_mc`
- line/market definition
- `p_over`, `p_under`, `p_push` as applicable
- calibration version
- exact distribution reference, when exact
- simulation bank reference, when Monte Carlo
- simulation draw count, when Monte Carlo
- seed or deterministic simulation reference
- MCSE / interval / convergence status, when Monte Carlo
- support/truncation diagnostics where applicable

## 8. Release gates

A release is blocked if any of the following is true:

- exact pricing and custom-line pricing disagree when they use the same exact PMF;
- low-N interactive simulation is used as an official price source;
- probability method is ambiguous;
- simulation-based pricing fails its convergence/uncertainty gate;
- a high-N sampler fails to reproduce an exact benchmark within prespecified Monte Carlo tolerance;
- the public UI presents exact and noisy simulated probabilities as two peer answers without uncertainty and authority labels.

## 9. Implementation-agent rule

Claude Code or ChatGPT Codex may not choose between exact and Monte Carlo methods ad hoc. It must implement the method authorized by the mathematical model specification and this policy, produce the required diagnostics, and stop on a failed gate rather than weakening the tolerance.
