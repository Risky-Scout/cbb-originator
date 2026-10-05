# MODEL_ARCHITECTURE_FREEZE.md
## CBB Originator Interconnected Player Prop Predictive Model
### Architecture Freeze v1.0
**Date:** October 4, 2026  
**Status:** FROZEN UNTIL EMPIRICAL GATE FAILURE

## 1. Objective

Build a market-independent, joint probabilistic NCAAB model that prices the complete player board while preserving player-player, lineup, opponent, finite-opportunity, and game-level dependencies.

Public target board:
`MIN, PTS, TS%, REB, AST, STL, BLK, TOV, PF, ORB, DRB, FT, FT%, 2P, 2P%, 3P, 3P%`

## 2. Fundamental modeling decomposition

### A. Participation and exposure
Estimate:
- active probability
- starter probability
- full minutes distribution
- feasible rotations / lineup exposure

Historical training is based primarily on actual participation, DNPs, starts, minutes, substitutions, and lineups. Multi-year injury diagnoses are not required.

### B. Dynamic player strength
Estimate time-varying latent talent separately from role:
- shooting by shot family
- free throws
- usage / shot opportunity
- creation / assists
- turnovers
- ORB / DRB
- steals
- blocks
- fouls

Use hierarchical shrinkage and explicit cold-start priors for freshmen/transfers.

### C. Game and lineup environment
Estimate:
- pace / possessions
- team offensive/defensive strength
- foul / turnover / rebounding environment
- lineup main effects
- teammate interactions
- opponent interactions
- score/context effects where appropriate

### D. Possession/event engine
Fit conditional event models for:
- turnover
- shot/foul branch
- shooter
- shot family
- make/miss
- assist
- block
- rebound
- steal
- FT sequence
- substitution/lineup transitions as needed

### E. Joint distribution engine
Generate coherent game outcomes from the fitted probabilistic system.

Rules:
- exact PMF/CDF summation is authoritative where tractable;
- otherwise use a convergence-qualified production Monte Carlo draw bank;
- the simulator propagates fitted probabilities; it does not invent feature effects;
- all players/statistics in a simulation draw belong to the same game state.

## 3. Hard accounting constraints

- `PTS = 2*2PM + 3*3PM + FTM`
- `REB = ORB + DRB`
- `FGM = 2PM + 3PM`
- `FGA = 2PA + 3PA`
- makes <= attempts
- player/team scoring reconciles
- regulation player-minutes = 200 per team
- overtime player-minutes add correctly
- steals link to opponent turnovers where applicable
- blocks link to opponent shot attempts
- assists link to eligible made field goals

## 4. Separate pregame and in-play models

Pregame is built and deployed first.

The future in-play model is separate and will reuse:
- canonical IDs/data
- latent talent
- lineup representation
- event definitions
- distribution/simulation mechanics

It will add:
- live state estimator
- sequential/Bayesian updating
- remaining-game inference
- live latency/correction controls

No in-play implementation may delay Pregame v1.

## 5. Historical injury amendment

Historical injury diagnoses/status histories are now P2 enrichment.

P0 historical availability evidence is:
- actual participation/DNP
- starter state
- minutes
- substitutions
- lineups
- teammate redistribution
- player/team performance

Current availability is P0 production information from Sharp Stats.

## 6. Data-source roles

- CBBD: P0 basketball backbone
- Sharp Stats: P0 current availability
- BDL: P1 independent QA + prospective market benchmark
- Bart Torvik: P1 non-blocking player-strength enrichment
- Odds API: fallback benchmark source
- historical injury archive: P2

## 7. Challenger requirement

The joint model must beat simpler challengers:
- rolling/rate distributions
- independent hierarchical models
- independent CatBoost/LightGBM heads
- ablations without lineup interactions / Torvik enrichment / residual factors

Complexity is retained only when untouched walk-forward results justify it.

## 8. Freeze/change-control rule

The architecture may change only because:
- a P0 data requirement proves infeasible;
- a model component fails its predefined validation gate;
- a challenger consistently outperforms it out of sample;
- a mathematical/accounting inconsistency is discovered.

New data availability alone is not sufficient reason to redesign the architecture.
