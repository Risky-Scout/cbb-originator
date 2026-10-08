# CBB Originator Blueprint v1.4

**Date:** 2026-10-07 · **Status:** AUTHORITATIVE · **Supersedes:** `docs/CBB_Blueprint_v1_3.docx` / `.pdf` (preserved) **only
on the topics in §1**. v1.3 stays authoritative on everything else: the pregame/in-play separation, the layered
primitive model, simulation and pricing, hard accounting, market isolation, the release criteria (§16) and the
licensing position (§15, sharpened here).

**Source of this version.** It adopts the **CBB Originator Audit and Execution Blueprint of 2026-10-06** as the basis for
the governing architecture, with architect amendments A-C (the Pre-Model Integrity Closure v1 directive, 2026-10-07).
The 2026-10-06 audit document itself is not stored in this repository. Where this file paraphrases it, the wording
comes from the architect directive. Confirming the phase definitions against the original audit is an open item.

## 1. What v1.4 changes

| topic | v1.3 position | v1.4 position |
|---|---|---|
| governing plan | Stage 0-19 + time-boxed build plan (§13) | Phases A-F (§3), order in `PROJECT_EXECUTION_ORDER_v1_1.md`, mapping in `docs/STAGE_CROSSWALK.md` |
| checkpoints | five pregame states | five checkpoints **T-24h, T-6h, T-1h, T-30m, T-10m**. Every observation carries a fidelity / availability class (§4) |
| historical timestamps | "no historical timestamp is ever invented" | unchanged and sharpened: no manufactured schedule revisions, provider receipt times or availability times. A sentinel such as `1900-01-01` is never a factual availability claim (§4) |
| availability source gate | Sharp Stats P0 | split into **G4A** (model-integration entry) and **G4B** (shadow/production reliability). G4B does not block the historical challenger program (§5) |
| production contract | built at deployment (§16) | ledger / grading / retraining / promotion / rollback / publication **contract frozen before the first model artifact exists**. Implementation stays later (§6) |
| identity evidence | (Stage 3.1) non-overlap => same player | **revoked**: a real transfer needs positive evidence, and whole-team transition clusters are presumed team-attribution defects (§7) |
| dataset status | accepted / not accepted | `RESEARCH_CANDIDATE` → `MODEL_READY` (architect ruling, recorded in a new manifest). A canonical correction always creates a successor version (§8) |
| model row selection | implicit | per-subsystem eligibility manifests. A generic "feature exists" flag never selects training rows (§9) |

## 2. Non-negotiables carried forward from v1.3 (unchanged)

Pregame first; one joint game distribution, not independent props; explicit availability → minutes → rotation →
opportunities → events; hard accounting identities; exact PMF where tractable, otherwise a convergence-qualified Monte
Carlo bank; the market never feeds the originator; the 2025-26 lockbox is untouched until its opening protocol;
2026-27 is the prospective validation season.

## 3. Phases A-F

| phase | purpose | exit gate |
|---|---|---|
| **A** pre-model integrity closure | A1 (done): governance freeze, cluster identity remediation, frozen contracts. A2: bitemporal 5-checkpoint rebuild, possessions + exact stints, eligibility manifests, PBP selection-bias audit, team-alias fix, environment lock + clean-room rebuild, off-machine baseline | dataset ruled **MODEL_READY** |
| **B** prospective availability evidence | real authorized snapshots, append-only, mapped | **G4A** before availability conditions any model. G4B evidence accrues in parallel |
| **C** historical challengers | baselines → participation/minutes → talent → environment → shot/event → location → lineup | per-subsystem out-of-sample gains on frozen folds |
| **D** engine + calibration + selection | joint engine, calibration, dependence checks, tournament, market benchmark, then one lockbox opening | frozen scorecard; lockbox result recorded, never tuned on |
| **E** shadow production | prospective 2026-27, locked predictions, grading, no publication | **G4B** + shadow stability criteria |
| **F** WizardOfOdds production | commercial-rights gate, staging contract tests, human approval, rollback | promotion decision recorded in the ledger |

## 4. Historical checkpoint fidelity (amendment A)

All five checkpoints stay required. Historical sources rarely know *when* something was known. Every observation and
every checkpoint value therefore carries a class (`POINT_IN_TIME_POLICY_v2_0.md` §3):
`PROSPECTIVE_EXACT`, `DATE_KNOWN`, `PRESEASON_ASSUMED`, `RETROSPECTIVE_FINAL_SCHEDULE`, `SOURCE_RECEIPT_UNKNOWN` and
`UNUSABLE_FOR_HISTORICAL_PIT`. A field may enter a historical checkpoint feature only when its class permits that
checkpoint (matrix in the PIT policy). Missing fidelity is represented, never fabricated. When the true history does
not exist, the historical T-24h..T-10m values for a field may legitimately be identical, and they are labeled as such.

## 5. Availability gates (amendment B)

* **G4A — model-integration entry:** at least one real authorized snapshot; raw bytes preserved append-only; exact
  `received_at`; raw status and note preserved; team/player mapping measured; schema accepted; proof of no
  target-outcome leakage. Needed before availability may condition any model.
* **G4B — shadow/production reliability:** >= 14 consecutive operating days **or** >= 250 material status rows,
  whichever is larger; >= 99 % team mapping; >= 97 % player mapping after review rules; append-only snapshots; an
  update-to-affected-game inference rehearsal <= 60 s; no structural retraining on the hot path. Needed before
  shadow / production, **not** before Phase C.
Detail: `docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md`.

## 6. Production contract timing (amendment C)

`PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, `docs/PREDICTION_LEDGER_SCHEMA.md` and
`WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md` are frozen **now**. Every model artifact created from Phase C on
records the identity/provenance fields those contracts require: dataset version, feature version, model version,
calibration version, seeds, draw counts and MCSE. Implementation of the production system remains Phase E/F.

## 7. Identity evidence rule

1. Chronology (non-overlapping dates) is never positive identity evidence.
2. A real transfer requires positive evidence: an independent portal record (normalized name + origin + destination +
   year), or provider roster continuity on both team-seasons outside any whole-team-sized cluster, which is admitted
   at MEDIUM confidence only.
3. A cluster of >= 5 identities moving between the same two team labels at the same boundary is presumed a
   team-attribution defect until evidence shows otherwise.
4. Repairs are non-destructive. Raw bytes are never edited. Derived rows are corrected through explicit rule layers,
   removed derived rows are preserved verbatim, and anything unresolved is quarantined from player-level modeling.
Implementation and evidence: `cbb_platform/team_attribution.py`, `cbb_platform/identity_cluster_audit.py`,
`docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md`.

## 8. Dataset status

`RESEARCH_CANDIDATE` datasets may feed research diagnostics but no model artifact intended for promotion.
`MODEL_READY` is granted only by the architect after Phase A2. Every canonical correction creates a successor
(`cbb_data_v2.N+<fingerprint>`) with its parent recorded, and ancestor manifests stay byte-identical. Current:
`cbb_data_v2.4+5f19d31e524a` (parent `cbb_data_v2.3+7bef640cccde`), RESEARCH_CANDIDATE.

## 9. Eligibility

Each subsystem trains only on rows listed in its eligibility manifest (`docs/MODEL_ELIGIBILITY_POLICY.md`). The manifest
covers seasons, games, player-games, required sources, missingness mechanism, quarantine, identity and PIT-fidelity
requirements, and counts.

## 10. Commercial data rights

The CBB Starter Pack is research-only unless commercial rights are separately obtained. No WizardOfOdds production
deployment may depend on a source whose commercial and publication rights are unverified. This is a deployment gate,
not a reason to discard private research (`docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md`).
