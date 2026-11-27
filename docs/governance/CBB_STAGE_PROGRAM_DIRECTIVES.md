# CBB Originator stage program: governing directives (durable record)

**Purpose.** The binding directives of the 2026-11-27 stage program are preserved here, so a context reset cannot lose them.
Evidence and status for each stage live in `docs/audits/2026-11-27-*/README.md` and in the authoritative engineering checkpoint
`docs/audits/2026-11-26-rootcause/results/ROOT_CAUSE_LEDGER.json` (one key per stage).

**Precedence.** The production end-state requirement (`../../CBB_RESUME_DIRECTIVE_PRODUCTION_END_STATE.md`, one level above the repo) applies to
every stage. A stage's engineering completion is never production activation.

## 1. Standing rules (all stages)

1. **Engineering vs activation.** A stage is engineering-complete when its corrections are committed, CI is green, the consolidated successor
   is staged inert on the Droplet, and the ledger checkpoint is written. Activation (the swap of `current`) is a separate, approved
   deploy that follows the stage's `DEPLOY_PROCEDURE.md`. Only one candidate is deployed: the latest consolidated successor, which carries the
   whole validated lineage.
2. **Lineage.** Each successor is built on the previous one. The ledger keeps the complete validated lineage. A superseded, never-deployed
   staging tree is removed only after its successor is staged and hash-verified.
3. **No unrequested scope.** Do not rebuild or indiscriminately refit the model. Do not begin the next stage on your own.
4. **Reuse evidence.** Do not repeat a completed calculation without a demonstrated reason. Cite the earlier evidence instead.
5. **Historical immutability.** Historical forecasts, snapshots, frozen evaluations and exports are never rewritten. A corrected engine is
   evaluated by replay as a separate, labelled population (Stage 08).
6. **Resource safety (Stage 05 incident).** A local timeout once left a remote measurement running, and a retry then created a duplicate. The rules:
   * `timeout` around `ssh` / `systemd-run` does not stop the transient unit. Stop it explicitly.
   * Before any retry of a substantial workload, verify that the remote process has terminated.
   * Use bounded remote execution: a named transient unit, `MemoryMax`, `RuntimeMaxSec`, all thread env vars = 1, nice, `ReadOnlyPaths`.
   * Do not run redundant full local regression suites (focused tests locally; the full suite is run by GitHub CI).
   * Make no unauthorized provider (Odds API / CBBD / RotoWire) requests.
   * On the Mac: at most 2 CPU workers, 1 BLAS thread each, and heavy jobs run sequentially.
7. **Droplet hygiene.**
   * Single-quote remote ssh scripts.
   * Never run root `python` against a release without `-B`.
   * Pass the unit `Environment=` lines (`CBB_PROD_ROOT`, thread limits) to ad-hoc `systemd-run`.

## 2. Authoritative 20-stage program (Stage 07 addendum §3, 2026-11-27)

| stage | subject | status | evidence |
|---|---|---|---|
| 01 | Production baseline and evidence protection | accepted | `docs/audits/2026-11-27-baseline` |
| 02 | Feature warm-up architectural optimization | accepted | `docs/audits/2026-11-27-stage2` |
| 03 | Availability acquisition and lock contention | accepted | `docs/audits/2026-11-27-stage3` |
| 04 | Roster, transfer and player identity integrity | accepted | `docs/audits/2026-11-27-stage4` |
| 05 | Injury and confirmed lineup integration | accepted | `docs/audits/2026-11-27-stage5` |
| 06 | Independent probability, export and settlement validation, including parameter provenance | accepted | `docs/audits/2026-11-27-stage6` |
| 07 | Season-complete universe, point-in-time information integrity and predictive fit lineage | engineering complete pending acceptance; successor staged, NOT deployed (disk gate 37.95 < 38.5 GB); directive texts BLOCKED | `docs/audits/2026-11-27-stage7` |
| 08 | Definitive chronological performance benchmark | not started | |
| 09 | Participation, starting lineups and joint minutes allocation | not started | |
| 10 | Player development, transfers and cold-start priors | not started | |
| 11 | Stat-specific opportunity and conversion modeling | not started | |
| 12 | Marginal PMF shape, dispersion and calibration | not started | |
| 13 | Player dependence and combination-prop coherence | not started | |
| 14 | Latent team strength and preseason roster uncertainty | not started | |
| 15 | Offensive efficiency, defensive efficiency, pace and matchup modeling | not started | |
| 16 | Player-to-team bridge and replacement-player value | not started | |
| 17 | Joint score distributions, margin, totals and game calibration | not started | |
| 18 | Integrated champion selection and protected-category validation | not started | |
| 19 | Autonomous lifecycle, exports and publication optimization | not started | |
| 20 | Final production deployment and independent certification | not started | |

Stage 06 assigned some items to provisional domain numbers (10 rates/priors, 12 shape/tails, 13 dependence, 14 calibration, 15 game ratings).
These are superseded. The authoritative stage for every open item is given in the Stage 07 assignment register
(`docs/audits/2026-11-27-stage7/ASSIGNMENT_REGISTER.md`).

## 3. Stage 06 binding execution addendum (verbatim, received 2026-11-27)

> # STAGE 06 — BINDING EXECUTION ADDENDUM
>
> Stages 01–05 are accepted at the engineering level.
>
> Do not repeat completed work.
>
> ## Authoritative baseline
>
> Active production: `9f65e7f9196c21a15811560d14c9974cb0bb2d7f`
>
> Rollback: `cae2a02`
>
> Validated consolidated successor: `d392832e579727088f690930f1ac87f29c7867e5`
>
> Stage 05 CI: `46052406957`
>
> Stage 05 checkpoint: `da71e34`
>
> The successor contains all accepted Stage 02–05 enhancements and is staged but not deployed.
>
> Read the existing ROOT_CAUSE_LEDGER.json and preserve the complete validated lineage.
>
> ## Stage 06 objectives
>
> Stage 06 now has two mandatory objectives:
>
> 1. Independent verification of probability mathematics, PMFs, fair prices, expected value, best-offer selection, settlement, grading and export integrity.
> 2. Comprehensive provenance verification of every material predictive parameter, pursuant to the Binding Parameter Estimation, Provenance and Validation Directive.
>
> Complete both.
>
> Reuse previously established independent game-market mathematics validation and other credible evidence.
>
> Do not repeat completed calculations without a demonstrated reason.
>
> ## Parameter audit requirements
>
> Establish which parameters are empirically fitted, validated fixed values, regularized estimates, mathematical constants, operational controls, safety controls, unsupported heuristics or unknown-provenance values.
>
> Specifically trace the actual training, persistence and runtime consumption of the fitted player and game parameters.
>
> Identify silent fallbacks and hardcoded defaults.
>
> Do not claim that the existence of a fit artifact proves every material parameter was fitted.
>
> Do not indiscriminately refit the model during Stage 06.
>
> Assign identified parameter deficiencies to their corresponding mathematical stages.
>
> Correct demonstrated mathematical implementation errors within Stage 06's authorized scope.
>
> ## Preserve the Stage 05 findings
>
> The historical investigation identified 268 player-games simulated despite already-known confirmed OUT or long-term OUT information, including 34 with playing probabilities of at least 0.5.
>
> The INITIAL and REFIT_RETRY availability-consumption defect has been corrected in d392832.
>
> Preserve original historical forecasts.
>
> Ensure the corrected-engine evaluation in Stage 08 distinguishes historical defective production forecasts from valid corrected candidate replays.
>
> ## Daily export requirements
>
> Independently verify:
>
> - Boards: one best eligible offer per game, player and stat.
> - Full Board and correctly derived 60% and 65% threshold sheets.
> - Distributions: complete supported predictive distributions.
> - Grades: immutable selection reconciliation.
> - Grades second worksheet: success by stat category and player-role category.
> - Compact immutable export hash receipts and forecast lineage.
>
> Distinguish forecast rows, market-matched rows and official eligible selections.
>
> ## Resource safety
>
> A Stage 05 local timeout left a remote measurement running, and a retry created a duplicate.
>
> Do not repeat this error.
>
> Verify remote process termination before retrying any substantial workload.
>
> Use bounded remote execution, explicit process identities, memory limits and enforced thread limits.
>
> Do not run redundant full local regression suites or unauthorized provider requests.
>
> ## Completion
>
> Preserve all governing directives in the existing durable engineering documentation so that future context resets do not lose them.
>
> Complete the independent mathematical validation and parameter-provenance inventory.
>
> Implement demonstrated Stage 06 corrections, validate them and preserve the consolidated successor.
>
> Update the authoritative engineering checkpoint.
>
> Keep engineering completion separate from production activation.
>
> Do not rebuild the model.
>
> Do not begin Stage 07 independently.

## 3a. Stage 07 binding execution addendum (received 2026-11-27)

The text is verbatim. Two exceptions: the §3 stage list is not repeated here, because it is recorded in §2, and three long bullet lists were collapsed into single sentences.

> # STAGE 07 — BINDING EXECUTION ADDENDUM
> ## SEASON-COMPLETE UNIVERSE, BITEMPORAL INFORMATION INTEGRITY AND FIT LINEAGE
>
> **Priority: P0 — Mandatory prerequisite to predictive parameter optimization**
>
> Stages 01–06 have been accepted at the engineering level.
>
> Do not repeat completed work.
>
> This stage must permanently establish the historical information integrity and predictive artifact lineage necessary for credible parameter estimation, model validation and market-performance comparisons.
>
> This is NOT authorization to rebuild the CBB Originator.
>
> ## 1. AUTHORITATIVE STARTING LINEAGE
>
> Reported active production: `9f65e7f9196c21a15811560d14c9974cb0bb2d7f`
> Reported rollback: `cae2a02`
> Validated Stage 06 successor: `2ac9117e25a92fcc090f2a5da4584a5248213e90`
> Stage 06 CI: `46062370887`
> Reported CI results: 1,172 passed. 36 skipped.
> Stage 06 checkpoint: `d5a7dac`
> Authoritative evidence: `docs/audits/2026-11-27-stage6/`
> Parameter inventory: `docs/audits/2026-11-27-stage6/PARAMETER_PROVENANCE.md`
> Existing governance: `docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md`
>
> Read the existing ROOT_CAUSE_LEDGER.json.
>
> Authenticate current identities before beginning.
>
> Preserve the complete Stage 02–06 validated lineage.
>
> ## 2. PRESERVE THE FULL GOVERNING DIRECTIVES
>
> The complete Binding Parameter Estimation, Provenance and Validation Directive has now been supplied.
>
> Preserve its complete text in the existing authoritative governance documentation.
>
> Also preserve the Binding Mathematical Enhancement Addendum and Binding Engineering Campaign Override.
>
> Do not create redundant governance documents.
>
> Reconcile the existing 55 parameter groups against the complete parameter directive.
>
> Identify only material omissions or differences.
>
> Do not repeat the entire Stage 06 parameter audit.
>
> ## 3. COMPLETE AND AUTHORITATIVE 20-STAGE PROGRAM
>
> Record the following exact stage mapping in the governance documentation.
>
> [The 20-stage mapping is recorded verbatim in §2 of this document.]
>
> Assign every remaining known defect, unsupported parameter and structural limitation to its appropriate stage.
>
> No material deficiency may remain unassigned.
>
> Do not begin the later stages independently.
>
> ## 4. ESTABLISH BITEMPORAL INFORMATION INTEGRITY
>
> The Stage 04 audit established that box-score data may arrive considerably later than the tip-plus-four-hour eligibility convention.
>
> Reported delays beyond the assumed availability time:
>
> - Median: 2.7 hours.
> - 99th percentile: 8.2 hours.
> - Maximum: 28.9 hours.
>
> A historical replay applying an earlier cutoff to a later database state may therefore admit information that the model had not actually acquired.
>
> Correct this exposure.
>
> Distinguish:
>
> - Event occurrence time.
> - Provider source publication time.
> - Actual acquisition time.
> - Canonical ingestion time.
> - Provider revision time.
> - Effective validity period.
> - Forecast cutoff.
> - Training cutoff.
> - State creation time.
>
> Do not substitute provider source time for actual acquisition time.
>
> The source timestamp describes event chronology. Actual acquisition establishes when the information became available to the model.
>
> Historical replay must respect both.
>
> Where applicable, define the earliest legitimate information availability as the maximum of the relevant structural eligibility time, source publication time and actual acquisition time.
>
> Provider corrections must become available only when their revised contents were legitimately knowable.
>
> ## 5. RECOVER HISTORICAL INFORMATION PROVENANCE
>
> Use the existing retained evidence:
>
> - Canonical state versions.
> - Sync records.
> - Request ledgers.
> - Archived payload identities.
> - Content hashes.
> - Box-score ingestion records.
> - Availability event logs.
> - Forecast receipts.
> - Training manifests.
> - Feature manifests.
> - Effective-dated roster records.
>
> Establish first-observed availability of each relevant observation and material revision wherever credible retained evidence permits.
>
> Prefer existing immutable state versions over retrospective filtering of a later state.
>
> Where precise historical timestamps cannot be recovered, use a defensible conservative availability bound or classify the observation as unverifiable.
>
> Do not fabricate per-row timestamps.
>
> Do not infer historical availability solely from a final database row's event date.
>
> Quantify the historically recoverable and unrecoverable populations.
>
> ## 6. IMPLEMENT PERMANENT REPLAY PROTECTIONS
>
> Inspect every relevant historical replay and walk-forward entry point.
>
> Ensure that:
>
> - Future box scores cannot enter historical features.
> - Late-arriving box scores cannot enter earlier forecasts.
> - Revised outcomes cannot enter forecasts predating the revision.
> - Later roster information cannot alter earlier team membership.
> - Availability events collected after the cutoff are excluded.
> - Historical curated statuses are applied only after their legitimate verification time.
> - Training normalization, imputation and calibration remain causal.
> - Cached feature identities incorporate the relevant causal information state.
> - Original prospective forecasts remain immutable.
>
> Add focused regression fixtures for the demonstrated failure modes.
>
> Require old-path failure and corrected-path success wherever practical.
>
> Do not weaken existing point-in-time guards.
>
> ## 7. ELIMINATE SILENT FITTED-MODEL SUBSTITUTION
>
> Stage 06 identified nine silent fallback mechanisms, including the material F1 fallback from the intended active model to the frozen through-2025-26 opening model.
>
> Do not merely document them again.
>
> For each identified fallback, establish:
>
> - Exact runtime activation condition.
> - Intended predictive artifact.
> - Actual substituted artifact.
> - Applicable training cutoff.
> - Historical activation evidence where recoverable.
> - Affected forecast populations.
> - Existing visibility in health and forecast receipts.
> - Existing actionability implications.
>
> Distinguish potential code-path exposure from confirmed historical activation.
>
> Permanently eliminate silent substitution.
>
> An explicitly approved last-known-good fallback may remain where necessary for operational resilience.
>
> Such a fallback must record:
>
> - Actual model and artifact identity.
> - Actual training cutoff.
> - Artifact age.
> - Expected model identity.
> - Exact fallback reason.
> - Explicit degraded status.
> - Appropriate actionability policy.
> - Recovery requirements.
>
> Do not label a frozen opening-model prediction as a current daily-refit prediction.
>
> If a fallback requires a later structural modeling decision, implement its visibility and lineage protection now and assign the remaining mathematical decision to the appropriate stage.
>
> ## 8. AUTHENTICATE HISTORICAL FIT LINEAGE
>
> For historical production forecasts and corrected candidate replays, establish:
>
> - Actual code commit.
> - Player engine identity.
> - Game model identity.
> - Fit identity.
> - Fitted artifact hashes.
> - Training information cutoff.
> - Preseason prior identity.
> - Feature-state identity.
> - Availability-state identity.
> - Joint score distribution version.
> - Game mean-basis setting.
> - Relevant configuration flags.
> - Fallback status.
>
> Preserve the Stage 06 attribution corrections.
>
> Do not rewrite immutable historical prediction receipts.
>
> Maintain explicit, auditable attribution corrections where original labels were inaccurate.
>
> ## 9. RECONCILE THE COMPLETE 2026–27 SEASON UNIVERSE
>
> Freeze the exact evaluation cutoff and eligibility contract.
>
> Account for every eligible completed game.
>
> Report:
>
> - Total eligible completed games.
> - Valid completed outcomes.
> - Missing or incomplete results.
> - Valid player box scores.
> - Genuine prospective forecasts.
> - Games with reconstructable historical information states.
> - Games without credible replay information.
> - Games with market observations.
> - Missing forecasts.
> - All exclusion reasons.
>
> For the player universe, reconcile:
>
> - Historically valid roster membership.
> - Pregame predictive player pool.
> - Actual participants.
> - DNP players.
> - Unexpected participants.
> - Forecast players.
> - Missing player forecasts.
> - Supported stat categories.
> - Valid PMFs.
> - Missing PMFs.
> - Market-overlap observations.
>
> Do not restrict the complete predictive evaluation universe to sportsbook-listed players.
>
> Do not silently exclude difficult games or players.
>
> ## 10. PROTECT THE STAGE 08 BENCHMARK
>
> Prepare the authoritative evaluation populations for Stage 08.
>
> Separate at minimum:
>
> A. HISTORICAL_PRODUCTION — actual immutable forecasts as originally generated.
>
> B. CORRECTED_CANDIDATE_REPLAY — the corrected incumbent architecture applied to historically legitimate information.
>
> C. MARKET_OVERLAP — matched event, line, price, timestamp and settlement observations.
>
> D. DIAGNOSTIC_RECONSTRUCTION — populations with incomplete historical provenance or prior model-selection exposure.
>
> Do not combine these into an undifferentiated performance claim.
>
> Preserve identification of the 268 historically defective INITIAL player-games from Stage 05.
>
> Preserve identification of forecasts predating the overtime mean-basis correction.
>
> Do not retrospectively rewrite original predictions to make the historical performance appear better.
>
> Do not fit new predictive parameters against the final evaluation population.
>
> ## 11. PARAMETER-ESTIMATION PREREQUISITE
>
> This stage establishes the legitimate information foundation for all subsequent predictive parameter fitting.
>
> Do not begin fitting the Stage 09–17 structural candidates before this foundation is established.
>
> Specifically protect the estimation of: availability-conditioned participation; starting-lineup probabilities; joint rotation and minutes distributions; return-from-absence effects; player development and transfer priors; opportunity redistribution; conditional production rates; marginal dispersion; residual dependence; team-strength parameters; offensive and defensive efficiency; pace; player-to-team availability effects; replacement-player value; game score variance and covariance.
>
> Historical information integrity is a prerequisite, not an optional statistical refinement.
>
> ## 12. PRODUCTION ACTIVATION MUST NOT BE FORGOTTEN
>
> The accepted Stage 02–06 enhancements remain staged rather than active in production.
>
> This is now a material operational concern.
>
> Before declaring the Stage 07 handoff complete, establish the exact current deployment status and the next objectively safe consolidated activation opportunity.
>
> If the validated candidate can be deployed under the existing authorized procedure without compromising production, complete that deployment and verify preflight, integrity and rollback.
>
> Do not lower resource thresholds.
>
> Do not disable timers simply to manufacture an idle window.
>
> Do not bypass locks or commercial data-rights restrictions.
>
> Do not launch another indefinite deployment watcher.
>
> If safe activation remains blocked, record the precise blocker and preserve the consolidated validated successor.
>
> Do not prevent unrelated safe offline engineering while awaiting genuine production evidence.
>
> Remember that engineering acceptance does not establish production activation.
>
> ## 13. RESOURCE AND EXECUTION DISCIPLINE
>
> Use retained artifacts and existing state versions.
>
> Do not initiate redundant full-season reconstructions merely to discover information provenance.
>
> Do not rerun the entire local regression suite unnecessarily.
>
> Use focused local tests and required GitHub CI.
>
> Run one substantial computational workload at a time.
>
> Enforce numerical thread and memory limits.
>
> Verify remote processes have terminated before retrying timed-out commands.
>
> Do not initiate unauthorized provider requests.
>
> Do not perform host-wide operating-system changes.
>
> Protect NBA Prop Quant, Massey NCAAF/NFL, Fanatics and all unrelated production systems.
>
> ## 14. ACCEPTANCE GATES
>
> Report each independently: COMPLETE_STAGE_MAPPING_PRESERVED, PARAMETER_DIRECTIVE_PRESERVED, SEASON_GAME_UNIVERSE_RECONCILED, SEASON_PLAYER_UNIVERSE_RECONCILED, HISTORICAL_INFORMATION_AVAILABILITY_VERIFIED, PROVIDER_REVISION_PROTECTION_VERIFIED, AVAILABILITY_EVENT_CHRONOLOGY_VERIFIED, HISTORICAL_ROSTER_INTEGRITY_VERIFIED, REPLAY_LEAKAGE_DEFECTS_REPAIRED, CAUSAL_FEATURE_CACHE_PROTECTED, SILENT_MODEL_FALLBACKS_ELIMINATED, HISTORICAL_FIT_LINEAGE_VERIFIED, ORIGINAL_PROSPECTIVE_FORECASTS_PRESERVED, CORRECTED_REPLAY_POPULATION_DEFINED, MARKET_OVERLAP_POPULATION_DEFINED, MISSING_INFORMATION_LIMITATIONS_QUANTIFIED, STAGE_08_BENCHMARK_READY, RESOURCE_SAFETY_VERIFIED, SUCCESSOR_LINEAGE_PRESERVED.
>
> Use PASS, FAIL, BLOCKED or UNVERIFIED.
>
> Do not declare unavailable historical evidence reconstructed.
>
> Do not confuse a conservative reconstructed information state with an exact historical state.
>
> ## 15. COMPLETION REPORT
>
> Return a concise, evidence-supported report covering: exact defects discovered; permanent implementation corrections; historical information provenance recovered; historical observations that remain unverifiable; exact game and player population counts; historical fallback activation findings; disposition of all nine fallback mechanisms; point-in-time regression evidence; corrected replay readiness; parameter-estimation readiness; focused regression and CI results; consolidated successor identity; production activation status; updated authoritative engineering checkpoint.
>
> Do not stop after documenting an internally repairable defect.
>
> Do not rebuild the model.
>
> Do not begin Stage 08 independently.
>
> Complete every internally resolvable Stage 07 requirement efficiently and correctly.
>
> BEGIN STAGE 07 NOW.

## 4. Binding Parameter Estimation, Provenance and Validation Directive (requirements as cited)

The full text of this directive was not delivered to the Stage 06 session. Its requirements, as the Stage 06 addendum cites them, are:

* Classify every material predictive parameter as one of: EMPIRICALLY_FITTED, VALIDATED_FIXED, REGULARIZED_ESTIMATE, MATHEMATICAL_CONSTANT,
  OPERATIONAL_CONTROL, SAFETY_CONTROL, UNSUPPORTED_HEURISTIC or UNKNOWN_PROVENANCE.
* For fitted parameters, trace three links: training (the estimator and its data window), persistence (the artifact) and runtime consumption (the scoring-path read).
* Identify silent fallbacks and hardcoded defaults.
* A fit artifact's existence is not proof that each parameter was fitted.
* No indiscriminate refit. Each deficiency is assigned to its mathematical stage.

When the full text is supplied, insert it here verbatim. The Stage 06 inventory then needs a re-check against any additional requirement.

**Stage 07 (2026-11-27).** The Stage 07 addendum states that this directive, the *Binding Mathematical Enhancement Addendum* and the *Binding Engineering Campaign Override* "have now been supplied". However, none of their text was included in the Stage 07 message, or in any file in the repo, the Droplet, or the memory. They cannot be preserved verbatim, and the 55 groups cannot be reconciled against text that was not received. Status: **BLOCKED pending receipt**.

## 5. Production end-state (summary; full text at `../../CBB_RESUME_DIRECTIVE_PRODUCTION_END_STATE.md`)

Done means: the Droplet `/current` scores with the final validated engine, proven by the 14-item live proof.
* Player: RA2V1 / PTSC / TOVT1 / DEV1 / CTX1 / PI2 / PS2.
* Game: 1.2.0 / GC3 / TP1P / bridge.

A fix that exists only in research, a branch, CI or a report does not count. If any validated enhancement is shadow-only, FINAL_DEPLOY = FAIL.
