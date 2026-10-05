# CBB Originator Model — Authoritative Project Documents

This folder is the authoritative planning and governance package for the CBB Originator Interconnected Player Prop Predictive Model.

## Frozen current decisions

- Pregame model first; in-play is a separate later model.
- CBBD is the P0 basketball backbone.
- Sharp Stats is the P0 current availability source.
- Historical injury diagnoses/status archives are P2 and do not block the model.
- Historical participation/DNP/role/lineup history is P0.
- BDL is P1 independent QA + prospective market benchmark.
- Bart Torvik is P1 non-blocking player-strength enrichment.
- Odds API is fallback benchmark infrastructure only.
- Pure originator uses no sportsbook features.
- Exact PMF is authoritative where tractable; otherwise convergence-qualified Monte Carlo is authoritative.
- No further architecture changes without an empirical gate failure.

## Implementation order

Follow `PROJECT_EXECUTION_ORDER_v1_0.md`.

## Next implementation stage

**Stage 1: Canonical schema + point-in-time contract, immediately followed by Data Platform v1.0 ingestion.**

## Implementation directive

Use `docs/STAGE_1_DATA_PLATFORM_FOUNDATION_DIRECTIVE.md` as the next Claude Code/Codex instruction.
