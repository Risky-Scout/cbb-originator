# Stage crosswalk: frozen Stage 0-19 order, implemented stages and Phases A-F

**Version:** 1.0 · **Date:** 2026-10-07 · **Authority:** `docs/ARCHITECTURE_PRECEDENCE.md` §2 (#3)

The original order is `docs/PROJECT_EXECUTION_ORDER_v1_0.md` (frozen 2026-10-04, preserved). The implemented work used
its own stage names. Those names are commits on `main` / `research/stage3-pit-feature-store`:

| implemented stage | commit(s) | dataset produced |
|---|---|---|
| Stage 1 (data platform foundation) | `40872e3` | Stage 1 accepted baseline |
| Stage 2 (CBBD event backfill, coverage audit, windows) | `236da4e`, `154a24c`, `a5d584c` | `cbb_data_v2.0+b00e8d3dc798` |
| Stage 2.1 (canonical game identity resolution) | `c2c1b1e`, `f0547bd` | `cbb_data_v2.1+292c2d2b1739` |
| Stage 2.2 (played-game status, pre-2005 ruling) | `73a7dec`, `09de3bd` | `cbb_data_v2.2+95dd9c2fa5b7` |
| Stage 3 (PIT feature store, label store, folds) | `e559242` | features on v2.2 |
| Stage 3.1 (player identity repairs, PIT player pool) | `956ccdd` | `cbb_data_v2.3+7bef640cccde` (RESEARCH_CANDIDATE) |
| Pre-Model Integrity Closure v1 Part 1 (this) | this commit | `cbb_data_v2.4+5f19d31e524a` (RESEARCH_CANDIDATE) |

New phases (defined in `CBB_Blueprint_v1_4.md` §3, ordered in `PROJECT_EXECUTION_ORDER_v1_1.md`):
**A** pre-model integrity closure (Part 1 governance + identity + contracts; Part 2 bitemporal rebuild, possession/stint
reconstruction, eligibility manifests, reproducible baseline) ·
**B** prospective availability evidence (G4A) ·
**C** historical challenger-model program ·
**D** joint engine, calibration, tournament, market benchmark, lockbox opening ·
**E** shadow production (G4B) ·
**F** WizardOfOdds production, then in-play as a separate project.

Status vocabulary: **COMPLETE** · **PARTIAL** · **DEFERRED** (deliberately postponed, with a reason) · **SUPERSEDED**
(replaced by a newer deliverable) · **NOT_STARTED**.

## Crosswalk by original deliverable

| orig. stage | deliverable | implemented in | status | new phase | evidence / gap |
|---|---|---|---|---|---|
| 0 | frozen documents (architecture, sources, matrix, data requirements, probability engine) | Stage 0 package | COMPLETE | — | preserved; precedence in `docs/ARCHITECTURE_PRECEDENCE.md` |
| 0 | gate: P0 families sourced; no blocking architecture question | Stage 0 | COMPLETE | — | |
| 1 | `CANONICAL_SCHEMA.md`, DDL, ID-map and lineage contracts | Stage 1 | COMPLETE | — | `cbb_platform/schema.sql` |
| 1 | `POINT_IN_TIME_POLICY.md` | Stage 1 (v1.2) | SUPERSEDED (in part) | A1 | v1.2 legality rules HR-1..HR-7 stand; checkpoint set / fidelity classes / schedule AS-OF superseded by `POINT_IN_TIME_POLICY_v2_0.md` |
| 1 | gate: no market field reaches originator views | Stage 1 | COMPLETE | — | `tests/test_market_isolation.py` |
| 2.1-2.7 | CBBD games, teams, rosters, box, PBP, substitutions, lineups, season summaries, recruiting, transfers, venues | Stage 1 + Stage 2 | COMPLETE | — | raw manifest `b055525a…`; 18,569 payloads |
| 2.8 | BDL games / player / team stats for QA | Stage 1 | PARTIAL | A2 | 816 games / 130 player-games ingested; not a full independent reconciliation |
| 2.9 | BDL odds / opening odds into benchmark namespace | Stage 1 | PARTIAL | D | tables and isolation exist; 0 odds rows held |
| 2.10 | Sharp Stats adapter + append-only snapshots | Stage 1 / 2Q | PARTIAL | B | adapter + append-only tests on SYNTHETIC fixtures; **no real snapshot** (G4A open) |
| 2.11 | Bart Torvik enrichment | — | DEFERRED | C (optional) | non-blocking by Amendment 4; no adapter |
| 2 | gate: idempotent, primary joins, no raw overwrite | Stage 1/2 | COMPLETE | — | |
| — | game identity resolution (duplicates / aliases / corrupt) | Stage 2.1 | COMPLETE | A (integrity) | not in the frozen order; `docs/GAME_IDENTITY_RESOLUTION.md` |
| — | played-game status; pre-2005 out of scope | Stage 2.2 | COMPLETE | A (integrity) | `docs/GAME_STATUS_RESOLUTION.md` |
| — | player identity repairs (collisions) | Stage 3.1 → **reopened** | SUPERSEDED | A1 | Stage 3.1 chronology rule revoked; cluster audit + v2.4 remediation (`docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md`) |
| — | team identity alias (LIU Brooklyn / Long Island University, 2 Carver College ids) | — | NOT_STARTED | A2 | classified CANONICAL_MAPPING_ERROR in the cluster audit; merge deferred (changes game universe) |
| 3 | `COVERAGE_AUDIT.json` by season / source | Stage 2 | **COMPLETE** | — | **coverage auditing exists**: `data/qa/COVERAGE_AUDIT.json`, per-family quality CSVs |
| 3 | ID join rates, CBBD vs BDL reconciliation | Stage 2 | PARTIAL | A2 | join rates complete; BDL reconciliation limited to the held sample |
| 3 | PBP / onFloor / shot location / substitution / lineup coverage | Stage 2 | COMPLETE | — | `data/qa/cbbd_*_quality_by_season.csv` |
| 3 | stint reconstruction feasibility | Stage 2 | PARTIAL | A2 | onFloor/substitution consistency measured; no stint build attempted |
| 3 | Sharp Stats current-season mapping | — | NOT_STARTED | B | needs a real snapshot (G4A) |
| 3 | Torvik mapping | — | DEFERRED | C | optional source |
| 3 | market benchmark coverage | — | NOT_STARTED | D | no odds held |
| 3 | PIT timestamp completeness | Stage 1/2 | COMPLETE | — | every fact row has `historical_knowable_at`; wallclock UNTRUSTED_MIXED |
| 3 | gate: training window per subsystem | Stage 2T + Stage 3 | PARTIAL | A2 | windows proposed / frozen in `spec.SUBSYSTEM_WINDOWS`; eligibility manifests now required (`docs/MODEL_ELIGIBILITY_POLICY.md`) |
| 4 | canonical possessions | — | **NOT_STARTED** | A2 | **exact possession reconstruction is not complete**: `fact.possessions` has 0 rows. Policy frozen: `docs/POSSESSION_AND_LINEUP_RECONSTRUCTION_POLICY.md` |
| 4 | paired substitutions | Stage 2 | PARTIAL | A2 | `fact.substitution_pairs` (517,979 rows; 2013-14, 2023-24, 2024-25 only) |
| 4 | exact lineup stints | — | **NOT_STARTED** | A2 | **exact chronological lineup stints are not complete**: `fact.lineup_stints` has 0 rows. Stage 3 lineup features use per-play attribution (2023-24+), not stints |
| 4 | reconstruction quality metrics + accounting reconciliation | Stage 2 (PBP reconciliation only) | PARTIAL | A2 | `qa.pbp_game_reconciliation`; 4,138 FINAL_SCORE_MISMATCH games quarantined (bias plan: `docs/PBP_QUARANTINE_SELECTION_BIAS_PLAN.md`) |
| 5 | strictly lagged feature store | Stage 3 / 3.1 | **PARTIAL** | A2 | **PIT feature infrastructure exists** (220 registered features, leakage tests, separate label store) but at ONE checkpoint (T-1h / ET date start), with a `1900-01-01` preseason sentinel that PIT v2.0 forbids; five-checkpoint bitemporal rebuild pending |
| 5 | role / minutes history, DNP / absence-response inputs | Stage 3 | PARTIAL | A2/C | participation history exists; absence-response redistribution features not built |
| 5 | lineup exposure | Stage 3 | PARTIAL | A2 | 2023-24 .. 2024-25 per-play attribution only |
| 5 | recruiting / transfer priors | Stage 3 | COMPLETE | — | `prof_*` features |
| 5 | gate: automated leakage tests | Stage 3 | COMPLETE | — | `tests/test_stage3.py`, `tests/test_stage3_1.py` |
| — | walk-forward folds + lockbox exclusion | Stage 3 | COMPLETE | — | `modeling/folds/*.json` (rebuilt on v2.4) |
| 6 | challenger baselines (rolling, hierarchical, CatBoost/LightGBM) | — | **NOT_STARTED** | C | **challenger models are not started** |
| 7 | dynamic player talent | — | NOT_STARTED | C | |
| 8 | availability / minutes / rotation | — | NOT_STARTED | C (+B for current conditioning) | |
| 9 | game environment | — | NOT_STARTED | C | |
| 10 | lineup / interaction layer | — | NOT_STARTED | C | |
| 11 | possession / event models | — | NOT_STARTED | C | needs A2 possessions |
| 12 | joint distribution engine | — | NOT_STARTED | D | |
| 13 | calibration + dependence validation | — | **NOT_STARTED** | D | **calibration is not started** |
| 14 | full challenger tournament | — | NOT_STARTED | D | |
| 15 | sportsbook benchmark | — | NOT_STARTED | D | |
| 16 | production pipeline + readiness gates | — | NOT_STARTED (contract COMPLETE) | contract A1, build E/F | contract frozen now: `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, `docs/PREDICTION_LEDGER_SCHEMA.md` |
| 17 | shadow production | — | **NOT_STARTED** | E | **shadow / production is not started** |
| 18 | deploy Pregame v1 | — | NOT_STARTED | F | WizardOfOdds contract frozen: `WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md` |
| 19 | in-play project | — | DEFERRED | after F | separate project after Pregame v1 is stable |

## What this crosswalk asserts (tested by `tests/test_pre_model_closure.py::test_governance_documents_exist_and_point_to_the_new_versions` for existence; the statuses are the record)

* Coverage auditing **exists** (frozen Stage 3).
* PIT feature infrastructure **exists** (frozen Stage 5, PARTIAL: one checkpoint).
* Exact possession reconstruction is **not complete** (frozen Stage 4: NOT_STARTED).
* Exact chronological lineup stints are **not complete** (frozen Stage 4: NOT_STARTED).
* Challenger models are **not started** (frozen Stage 6+).
* Calibration is **not started** (frozen Stage 13).
* Shadow / production is **not started** (frozen Stages 16-18; contracts only).
