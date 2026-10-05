# CBB Originator Project — Data Source and Historical Availability Amendment

**Date:** October 4, 2026  
**Status:** RECORDED AND FROZEN

## Amendment 1 — Historical injury labels downgraded

Multi-year historical injury diagnoses/status reports are no longer a P0 requirement.

They are **P2 optional enrichment**.

Historical absence-response training will use:
- actual participation/DNP
- starter status
- minutes
- substitutions
- on-floor lineups
- role/usage
- teammate redistribution
- player/team outcome changes

This is the statistically relevant mechanism for the core player-prop model.

## Amendment 2 — Sharp Stats locked as current availability source

Sharp Stats is the P0 production source for:
- player
- team
- position/class
- injury/absence reason
- structured status
- update date
- descriptive note

Our own `received_at_utc` provides exact PIT timing going forward.

Raw status and note are both preserved because conflicts can occur.

## Amendment 3 — BDL locked as P1 QA + market benchmark

BALLDONTLIE NCAAB is not the primary basketball source.

Use it for:
- independent player-game reconciliation
- independent team-game reconciliation
- score/H1/H2/OT reconciliation
- active-player QA
- prospective current/opening sportsbook benchmark with both-side prices and timestamps

BDL odds never enter the pure-originator feature path.

## Amendment 4 — Bart Torvik locked as P1 non-blocking enrichment

Bart Torvik may enrich:
- explicit DNP history
- advanced player-game rates
- game-level dunk/rim/mid shot splits
- BPM / OPM / DPM
- independent player-strength QA

Core implementation proceeds without it if acquisition is unsuitable.

## Amendment 5 — No further source shopping before implementation

The source architecture is frozen:
- CBBD = P0 basketball backbone
- Sharp Stats = P0 current availability
- BDL = P1 QA + market benchmark
- Bart Torvik = P1 optional enrichment
- Odds API = fallback market benchmark only
- historical injury archive = P2
- historical player props = P2/P1-later benchmark only

Only a measured P0 coverage/access failure may reopen the source decision.

## Next stage

Proceed immediately to:
1. `CANONICAL_SCHEMA.md`
2. `POINT_IN_TIME_POLICY.md`
3. Data Platform v1.0 ingestion
4. `COVERAGE_AUDIT.json`

No predictive model is trained until the canonical/PIT contracts exist and the initial data platform passes its readiness gate.
