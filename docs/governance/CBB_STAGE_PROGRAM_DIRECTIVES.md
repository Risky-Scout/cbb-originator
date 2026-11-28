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
| 07 | Season-complete universe, point-in-time information integrity and predictive fit lineage | engineering complete; successor 7d160b1 staged, guarded deploy scheduled 2026-11-28 04:50Z / 16:50Z; PARAMETER_DIRECTIVE_PRESERVED resolved in Stage 08 (§6); gate tally corrected to 14 PASS + 4 qualified PASS + 1 BLOCKED | `docs/audits/2026-11-27-stage7` |
| 08 | Definitive chronological performance benchmark | engineering complete pending acceptance; benchmark CBB-S08-BENCHMARK-20261128 frozen; population B replay BLOCKED (carried to 18) | `docs/audits/2026-11-28-stage8` |
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
| 19 | Autonomous lifecycle, exports and publication optimization | not started; **mandatory storage requirements S19-STOR-1…7** (owner directive 2026-11-27) | `docs/audits/2026-11-27-stage7/ASSIGNMENT_REGISTER.md`, `docs/audits/2026-11-27-storage` |
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

**Stage 08 (2026-11-28): RESOLVED.** The consolidated successor instrument (§6) supplies the operative text of all three directives. The 55 groups are reconciled against Part III in `docs/audits/2026-11-28-stage8/results/parameter_reconciliation.csv`.

## 5. Production end-state (summary; full text at `../../CBB_RESUME_DIRECTIVE_PRODUCTION_END_STATE.md`)

Done means: the Droplet `/current` scores with the final validated engine, proven by the 14-item live proof.
* Player: RA2V1 / PTSC / TOVT1 / DEV1 / CTX1 / PI2 / PS2.
* Game: 1.2.0 / GC3 / TP1P / bridge.

A fix that exists only in research, a branch, CI or a report does not count. If any validated enhancement is shadow-only, FINAL_DEPLOY = FAIL.

## 6. CONSOLIDATED SUCCESSOR GOVERNANCE INSTRUMENT (verbatim, received 2026-11-28 01:20Z with the Stage 08 directive)

**Status.** This is the consolidated successor governance instrument. It supplies the operative requirements of the *Binding Engineering
Campaign Override*, the *Binding Mathematical Enhancement Addendum* and the *Binding Parameter Estimation, Provenance and Validation
Directive*. It supersedes the "requirements as cited" summary in §4 and resolves the Stage 07 BLOCKED status of PARAMETER_DIRECTIVE_PRESERVED.
Where §1 and this instrument differ, this instrument governs. Sections 1–5 above are retained unchanged as the historical record.
The text below is reproduced verbatim (headings demoted one level so they nest under this section).

### CBB ORIGINATOR — CONSOLIDATED BINDING GOVERNANCE AND STAGE 08 EXECUTION DIRECTIVE

### PART I — PERMANENT ENGINEERING GOVERNANCE

#### 1. Mission

Repair and optimize the existing CBB Originator into a mathematically sophisticated, empirically validated, operationally reliable, market-independent probabilistic forecasting system.

Do not rebuild the model unnecessarily.

Preserve every validated component unless evidence supports its replacement.

The ultimate objective is credible predictive superiority or noninferiority relative to appropriately matched sportsbook market probabilities across player and game prediction categories.

Do not guarantee statistical superiority where the evidence does not support it.

#### 2. Durable governing document

This consolidated directive supplies the operative requirements of the Binding Engineering Campaign Override, Binding Mathematical Enhancement Addendum and Binding Parameter Estimation, Provenance and Validation Directive.

Preserve this complete text in:

docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md

Identify it as the consolidated successor governance instrument.

Preserve previously recorded directives and evidence without unnecessary duplication.

Reconcile the 55 existing parameter groups against these requirements.

Do not repeat the completed Stage 06 inventory.

Resolve the previously blocked PARAMETER_DIRECTIVE_PRESERVED gate by verifying the consolidated governance requirements are durably present.

#### 3. Engineering discipline

- Repair demonstrated root causes, not superficial symptoms.
- Require regression evidence for material implementation corrections.
- Protect existing validated predictive behavior.
- Preserve historical prospective forecasts unchanged.
- Distinguish engineering completion, deployment and live validation.
- Do not conceal failures through silent fallbacks.
- Do not fabricate missing historical information.
- Do not weaken tests to obtain passing results.
- Do not use retrospective corrections to misrepresent actual historical performance.
- Do not repeatedly audit previously resolved questions.
- Complete internally resolvable defects within their assigned stages.
- Do not begin subsequent stages without authorization.

#### 4. Resource and production protection

Run one substantial computational workload at a time.

Use one numerical thread unless explicitly authorized otherwise.

Maintain the established memory and disk safety thresholds.

Do not lower those thresholds.

Use bounded execution, checkpointed computation and restart-safe processes.

Do not duplicate remote workloads following SSH or local timeout interruptions.

Do not clear host-wide caches.

Do not initiate unauthorized provider requests.

Do not modify NBA Prop Quant, Massey NCAAF/NFL, Fanatics or unrelated systems.

Protect active production and immutable historical evidence.

No external publication or wagering is authorized by this directive.

#### 5. Authoritative stage mapping

01 — Production baseline and evidence protection.

02 — Feature warm-up architectural optimization.

03 — Availability acquisition and lock contention.

04 — Roster, transfer and player identity integrity.

05 — Injury and confirmed lineup integration.

06 — Independent probability, export and settlement validation, including parameter provenance.

07 — Season-complete universe, point-in-time information integrity and predictive fit lineage.

08 — Definitive chronological performance benchmark.

09 — Participation, starting lineups and joint minutes allocation.

10 — Player development, transfers and cold-start priors.

11 — Stat-specific opportunity and conversion modeling.

12 — Marginal PMF shape, dispersion and calibration.

13 — Player dependence and combination-prop coherence.

14 — Latent team strength and preseason roster uncertainty.

15 — Offensive efficiency, defensive efficiency, pace and matchup modeling.

16 — Player-to-team bridge and replacement-player value.

17 — Joint score distributions, margin, totals and game calibration.

18 — Integrated champion selection and protected-category validation.

19 — Autonomous lifecycle, exports and publication optimization.

20 — Final production deployment and independent certification.

---

### PART II — BINDING MATHEMATICAL ENHANCEMENT REQUIREMENTS

The following five structural mechanisms are mandatory targeted engineering objectives.

Do not replace them with arbitrary hardcoded adjustments.

#### Hook A — Availability-conditioned participation

Stage 09.

Preserve the existing empirically estimated baseline participation mechanism.

Develop a statistically estimated availability-conditioned adjustment on a suitable probability or hazard scale.

Account for status category, source reliability, information age, forecast horizon, historical player role and absence context.

Use hierarchical pooling or regularization where observations are sparse.

Confirmed ineligibility or non-participation must be enforced when legitimately established.

Do not assign arbitrary participation probabilities to questionable, doubtful, probable or active players.

Do not interpret absence of an injury report as confirmed availability.

Validate using chronological Bernoulli proper scores, calibration and subgroup analysis.

#### Hook B — Joint participation and starting lineups

Stage 09.

Model team participation and starting lineups jointly.

Generate feasible team states rather than independently assigning incompatible starting probabilities.

Enforce five starters in ordinary valid game states.

Maintain explicit expected-lineup uncertainty.

Use confirmed lineups only when an authorized source supplies credible information before the relevant forecast cutoff.

Never fabricate a confirmed lineup.

Distinguish participation probability from starting probability.

#### Hook C — Joint minutes allocation

Stage 09.

Replace unconstrained independent player-minute allocation with a coherent joint rotation distribution.

For regulation, enforce the team-level minutes conservation requirement of 200 player-minutes, with individual minutes bounded by participation and game duration.

Represent overtime through an appropriate shared overtime state.

Account for injury-related absence, return from absence, rotation role, starting status and teammate replacement.

Use statistically justified uncertainty and redistribution mechanisms.

A weighted bounded-simplex projection may be evaluated as a surgical implementation approach.

Its weights, raw minute distributions and redistribution mechanisms must be empirically justified.

Do not impose arbitrary injury discounts or allocate every absent player's minutes proportionally without validation.

#### Hook D — Stat-specific opportunity and conversion

Stage 11.

Separate player minutes, opportunity generation and conditional conversion.

Develop appropriate mechanisms for points, rebounds, assists, three-pointers, steals, blocks and turnovers.

Account for teammate availability, lineup composition, team pace, opponent characteristics and role changes.

Do not assume every category responds identically to an absent teammate.

Do not force all team-level stat totals to remain unchanged after an injury.

Avoid double-counting adjustments already incorporated through minutes, role or contextual features.

Estimate material coefficients and validate individual and joint effects.

#### Hook E — Replacement-adjusted player-to-team bridge

Stage 16.

Replace unsupported player-to-team availability coefficients with a statistically justified bridge.

Measure the difference between the projected available rotation and the historical roster contribution already implicit in the independent team-strength estimate.

Account for replacement-player quality.

Distinguish new availability shocks from chronic absences already incorporated into team ratings.

Investigate offensive and defensive effects separately where data support identification.

Avoid double-counting team-level information.

The existing gamma = 0.3 is not an empirically established coefficient.

Estimate and validate its replacement, or establish a defensible constrained alternative.

The unused beta parameter must be mathematically justified, implemented and validated, or eliminated.

Do not introduce unsupported player impact metrics merely to create apparent sophistication.

#### Dependence and distribution requirements

Stages 12, 13 and 17 must preserve valid marginal and joint probability distributions.

Combination props must derive from coherent shared simulation states.

Account for both conditional residual dependence and dependence induced by shared game and rotation states.

Validate dispersion and covariance against the corrected mean specification.

Avoid double-counting overtime or shared contextual uncertainty.

---

### PART III — BINDING PARAMETER ESTIMATION, PROVENANCE AND VALIDATION

#### 1. Parameter accountability

Every material predictive parameter must have an explicit mathematical role, statistical justification and production lineage.

Maintain the existing parameter inventory.

Classify each material parameter as:

- EMPIRICALLY_FITTED
- VALIDATED_FIXED_PARAMETER
- REGULARIZED_OR_PRIOR_DRIVEN
- UNFITTED_HEURISTIC
- MATHEMATICAL_CONSTANT
- OPERATIONAL_CONTROL
- SAFETY_CONTROL
- UNKNOWN_PROVENANCE

Do not confuse mathematical constants or operational controls with estimable predictive coefficients.

Do not indiscriminately refit every constant.

#### 2. Required parameter evidence

For each material predictive parameter or parameter group, record:

- Mathematical definition.
- Current value or authenticated artifact reference.
- Estimation population.
- Training cutoff.
- Information-availability constraints.
- Estimation method.
- Objective function.
- Regularization or prior.
- Effective sample size.
- Identifiability limitations.
- Estimation uncertainty or sensitivity.
- Out-of-sample validation.
- Temporal stability.
- Interactions with other components.
- Runtime consumer.
- Fallback behavior.
- Affected prediction categories.
- Responsible engineering stage.
- Final disposition.

Trace actual fitted parameters from estimation through serialization, authentication, runtime consumption and final probability distributions.

An estimator existing in the repository does not prove its output is used in production.

#### 3. Estimation standards

Use statistically appropriate likelihoods, proper scoring rules and regularized predictive objectives.

Use chronological training and validation partitions.

Prevent leakage through feature generation, standardization, imputation, prior construction, calibration and model selection.

Estimate hyperparameters using appropriate inner chronological validation.

Protect outer evaluation periods from parameter selection.

Where historical model-selection exposure already exists, label it explicitly.

Do not claim untouched out-of-sample validation on previously examined selection populations.

Use genuine prospective evidence wherever possible.

#### 4. Material unsupported parameters

Every material unsupported parameter must ultimately receive one of these dispositions:

A. EMPIRICALLY_ESTIMATED_AND_VALIDATED.

B. EXISTING_VALUE_INDEPENDENTLY_VALIDATED.

C. MATHEMATICALLY_REDUNDANT_AND_ELIMINATED.

D. STATISTICALLY_CONSTRAINED_WITH_JUSTIFIED_PRIOR_AND_UNCERTAINTY.

An unfitted parameter inventory is not completion.

A favorable in-sample fit is not validation.

#### 5. Explicit mandatory optimization assignments

Stage 09:

- Availability-conditioned participation.
- Starting-lineup probabilities.
- Joint minutes distribution.
- Return-from-absence effects.
- Rotation replacement.
- Minutes clipping and dispersion.

Stage 10:

- DEV1 and PI2 K = 31.
- PI2 clipping range [0.7, 1.3].
- PI2 standardization constants.
- Player-development and transfer parameters.
- Cold-start prior strength.
- Prior recency.

Stage 11:

- Environment shrinkage.
- Stat-specific opportunity and conversion.
- CTX1 validation under PS2.
- Daily GLM and frozen-correction compatibility.
- Potential double-correction.
- Remaining F2–F9 predictive fallback mechanisms as applicable.

Stage 12:

- Unused dispersion parameters.
- Conditional dispersion.
- PMF shape and tail parameters.
- PTSC1 interaction.
- Calibration stability.

Stage 13:

- Residual dependence.
- Shared-state dependence.
- Combination-prop coherence.
- Dependence regularization.

Stage 14:

- Team-rating hyperparameter provenance and estimation.
- Preseason priors.
- Carry and regression strength.
- Home-court effects.
- Roster uncertainty.

Stage 15:

- Offensive and defensive efficiency.
- Pace.
- Opponent adjustments.
- Environment shrinkage.
- Current-season scoring-level drift.

Stage 16:

- Unfitted gamma.
- Unused beta.
- Replacement-player value.
- Offensive and defensive bridge effects.

Stage 17:

- Game variance parameters fitted against older means.
- Score covariance.
- Endgame design parameters.
- Overtime distribution and calibration.
- Joint score-distribution accuracy.

Stages 18–19:

- Daily-refit predictive validation.
- Frozen-correction compatibility.
- Integrated champion selection.
- Protected-category non-regression.
- Sustainable model promotion and monitoring.

Resolve any additional material deficiencies identified in the existing parameter inventory.

#### 6. Predictive fit promotion

Integrity checks alone are insufficient to establish predictive fitness.

Develop a computationally sustainable promotion framework using appropriate rolling predictive evaluation, causal validation, stability diagnostics and monitoring.

Do not require an expensive complete historical replay every morning.

Do not silently substitute the frozen opening model.

Any authorized fallback must identify the actual artifact, cutoff, age, reason, degraded status, actionability and recovery policy.

---

### PART IV — STAGE 08: DEFINITIVE CHRONOLOGICAL PERFORMANCE BENCHMARK

#### 1. Starting lineage

Validated Stage 07 successor:

7d160b1c7c5a02db834793954924c4ff240a0690

CI run:

46078107204

Reported CI:

1,186 passed, 36 skipped.

Focused regression:

200 passed, 2 skipped.

Engineering checkpoint:

45761b7

Ledger key:

stage7_universe_bitemporal_lineage_20261127

Read the existing Stage 07 evidence and ROOT_CAUSE_LEDGER.json.

Authenticate the starting lineage.

Do not modify the validated predictive mathematics during this stage.

#### 2. Deployment separation

A bounded remote deployment procedure already exists for 7d160b1.

Do not duplicate, restart or silently replace that procedure.

Authenticate its current state and receipt.

Stage 08 offline evaluation may proceed independently of production activation.

Do not compete with active production workloads.

Do not stage a successor that invalidates the scheduled deployment unexpectedly.

Do not merge or promote an unvalidated Stage 08 predictive change.

#### 3. Resolve administrative governance discrepancies

Preserve this consolidated directive.

Reconcile the Stage 07 acceptance-gate arithmetic against the authoritative ledger.

The reported 17 unqualified passes, two qualified passes and one blocked gate do not reconcile to 19.

Correct the reporting count without rerunning completed tests.

#### 4. Freeze the benchmark population

Use the Stage 07 cutoff:

2026-11-27T20:40:23Z

The primary benchmark must preserve this fixed evaluation cutoff.

Account for all reported populations:

- 1,485 tipped games.
- 1,443 completed games.
- 1,134 completed D-I versus D-I games.
- 1,430 games with reportedly reconstructable information states.
- 810 genuine prospective D-I game forecasts.
- 300 games predating Droplet forecasting.
- 24 games never slated.
- 32,757 player box lines.
- 22,997 forecast player-games.
- 1,014,192 recorded pregame PMFs.
- 514 games with player-market observations.
- 349 games with game-market observations.
- 72 ambiguous or unverifiable historical artifacts.
- 4,112 unresolved player identities or identity records.

Reconcile definitions, intersections and exclusions.

Do not assume these populations are directly comparable or nested.

Explain the PMF count relative to unique player-game-stat combinations and forecast vintages.

Determine the exact denominator and unit of the unresolved identity population.

Do not omit difficult games or unexpected players.

#### 5. Preserve distinct evaluation populations

A. HISTORICAL_PRODUCTION:

Genuine immutable forecasts as originally generated.

Include historical defective forecasts with their actual lineage and defect labels.

B. CORRECTED_CANDIDATE_REPLAY:

The frozen Stage 07 architecture applied only to legitimate historical information.

Use historically available fit artifacts or causally reconstructed fits under explicit reconstruction rules.

Distinguish exact retained states from conservative reconstructions.

C. MARKET_OVERLAP:

Matched predictions and sportsbook observations with comparable line, side, quote timestamp, settlement and forecast horizon.

D. DIAGNOSTIC_RECONSTRUCTION:

Incomplete information provenance, timing-rule-only historical states, ambiguous lineage or prior model-selection exposure.

Do not combine these populations into an undifferentiated performance result.

#### 6. Chronological replay integrity

For every corrected replay, authenticate:

- Forecast cutoff.
- Information-state cutoff.
- Actual acquisition availability.
- Historical roster membership.
- Availability event chronology.
- Fitted artifact identity.
- Training cutoff.
- Model-promotion availability.
- Feature-state identity.
- Configuration and parameter lineage.
- Forecast vintage.

Do not use a model fitted or promoted using future outcomes.

The mere existence of a retained database state does not establish a valid replay.

Historical lockbox seasons that can only be positioned through a timing rule must remain explicitly qualified.

#### 7. Forecast-vintage discipline

Evaluate comparable horizons independently.

At minimum distinguish, where supported:

- Initial daily forecast.
- Intraday refresh forecast.
- Official pregame cutoff forecast.
- Earliest available market observation.
- Same-time market observation.
- Closing market observation.

Do not select the best historical forecast vintage after observing the outcome.

Do not count repeated PMFs for the same realized outcome as independent observations.

Report unique events, players, player-games, market contracts and forecast vintages.

#### 8. Complete player-distribution evaluation

Evaluate every supported player-stat category, not merely sportsbook-listed props.

For each category report, where appropriate:

- Actual and forecast means.
- Signed bias.
- MAE and RMSE.
- Discrete CRPS.
- Predictive log score.
- Randomized PIT or appropriate discrete calibration diagnostics.
- Prediction interval coverage.
- Interval width.
- Tail calibration.
- Conditional and unconditional performance.

Account for DNP outcomes and participation separately.

Do not conflate a participation-conditioned prop PMF with an unconditional player-production distribution.

Evaluate:

- Participation probabilities.
- Minutes distributions.
- Starting and role classification where credible historical labels exist.
- PTS.
- REB.
- AST.
- FG3M.
- STL.
- BLK.
- TOV.
- PRA.
- PR.
- PA.
- RA.
- STOCKS.

Reconcile all supported stat categories against the actual engine contract.

Include unexpected participants and missing forecasts in coverage reporting.

#### 9. Complete game-distribution evaluation

Evaluate:

- Home score.
- Away score.
- Game total.
- Margin of victory.
- Moneyline probability.
- Spread probabilities.
- Team totals.
- Alternate spreads and totals where comparable observations exist.

Report:

- Signed bias.
- MAE.
- RMSE.
- CRPS.
- Log score.
- Brier score for relevant binary contracts.
- Calibration.
- Tail performance.
- Joint distribution diagnostics.
- Relevant covariance and correlation diagnostics.

Distinguish FBS/FCS terminology from CBB eligibility classifications. Do not import unrelated football assumptions.

Evaluate D-I versus D-I and other supported game classes separately.

#### 10. Independent market comparison

For every comparable player and game market, calculate both model and de-vigged market probabilities under identical settlement conventions.

Use matched line, side, event, player, book and quote timing.

Report opening, contemporaneous and closing comparisons separately.

Do not treat closing odds as information available to an earlier originator forecast.

Calculate paired differences in:

- Brier score.
- Log loss.
- Calibration measures where appropriate.

Use additional proper scores only where the market supplies sufficient distributional information.

Do not infer a complete market PMF from a single binary quote without explicitly justified assumptions.

Keep market-independent predictive accuracy separate from market-relative probability accuracy.

#### 11. Role and category analysis

For each player-stat category, break down performance by sufficiently supported groups:

- Starter versus nonstarter where actual or prediction-time labels are appropriately defined.
- Prediction-time role category.
- Projected minutes.
- Actual minutes for diagnostic analysis only.
- Participation probability.
- Availability status.
- Return-from-absence context.
- Early-season versus established history.
- Transfer and cold-start status.
- Forecast horizon.
- Conference or competition level.
- Over versus under.

Report unsupported or empty categories as NOT_EVALUABLE.

Do not use realized player role to claim prospective subgroup performance.

Distinguish diagnostic conditioning on actual outcomes from information available before prediction.

#### 12. Probability calibration

Produce reliability tables with sample sizes and uncertainty intervals.

Examine:

- Overall calibration.
- Calibration-in-the-large.
- Calibration slope where statistically appropriate.
- Overconfidence and underconfidence.
- High-probability tails.
- 60% and 65% reporting tiers.
- Role and stat interactions.
- Market-relative calibration.

Use appropriate regularization or pooling for sparse subgroup diagnostics.

Do not interpret a small high-probability sample as reliable evidence of sustained predictive advantage.

#### 13. Statistical inference

Use paired comparisons on identical observations.

Account for dependence among:

- Players in the same game.
- Multiple stats for the same player.
- Repeated games for the same teams.
- Alternative lines.
- Repeated forecast vintages.
- Shared market events.

Use defensible clustered or blocked uncertainty estimation.

Report effective sample sizes and confidence intervals.

Control or explicitly account for multiplicity when examining numerous categories.

Do not claim statistical superiority from a negative point estimate alone.

Do not claim noninferiority merely because a difference is statistically insignificant.

Any formal noninferiority margin must be justified and specified before evaluating the comparison.

#### 14. Root-cause diagnosis

For each materially weak prediction category, distinguish:

- Mean bias.
- Dispersion error.
- Tail error.
- Participation error.
- Minutes error.
- Rotation error.
- Role classification error.
- Opportunity-rate error.
- Conversion-rate error.
- Dependence error.
- Team-strength error.
- Pace error.
- Availability-bridge error.
- Parameter-estimation instability.
- Data coverage or identity limitations.
- Information horizon differences.

Use existing evidence and targeted diagnostics.

Do not infer causality solely from descriptive subgroup associations.

Rank candidate root causes by demonstrated evidence, affected sample size, materiality and expected relevance to the assigned engineering stage.

#### 15. Protect the incumbent benchmark

Freeze the benchmark architecture and evaluation definitions.

Record exact model identities, artifact hashes, population manifests, metrics and statistical methodology.

Subsequent Stages 09–17 must evaluate improvements against this frozen benchmark.

Do not optimize parameters during Stage 08.

Do not repeatedly tune later models against a purported untouched holdout.

Establish protected evaluation periods and prospective validation procedures for later stages.

#### 16. Resource-efficient execution

Reuse validated historical artifacts wherever possible.

Do not reconstruct every intraday forecast vintage unnecessarily.

Run one substantial computation at a time.

Use checkpointed chronological processing.

Perform a bounded feasibility pilot before any expensive season replay.

Quantify projected CPU, RAM, disk and elapsed resource requirements.

If a complete replay cannot safely run, preserve the exact outstanding population and report the blocker.

Do not substitute an unrepresentative sample and call it season-complete.

No unauthorized provider calls.

No production data modification.

#### 17. Required benchmark deliverables

Produce concise human-readable reports and machine-readable tables containing:

- Season game coverage.
- Season player coverage.
- Forecast-vintage reconciliation.
- Historical information limitations.
- Player-category performance.
- Game-category performance.
- Role-by-stat performance.
- Calibration diagnostics.
- Matched market comparisons.
- Clustered statistical uncertainty.
- Root-cause priority matrix.
- Parameter-estimation priorities.
- Protected benchmark manifest.

Every material result must include its actual denominator.

#### 18. Required final questions

Answer explicitly:

1. How well does the genuine historical production model perform?

2. How well does the corrected incumbent perform under legitimate chronological reconstruction?

3. Which player categories credibly outperform the matched market?

4. Which game categories credibly outperform the matched market?

5. Which categories remain inferior?

6. Which categories remain statistically inconclusive?

7. What are the principal predictive weaknesses by stat and player role?

8. What evidence supports each proposed structural repair?

9. Which unfitted or poorly validated parameters deserve the highest priority?

10. What exact benchmark must Stage 09 and subsequent stages improve upon?

#### 19. Acceptance gates

Report PASS, FAIL, BLOCKED or UNVERIFIED for:

- CONSOLIDATED_GOVERNANCE_PRESERVED
- PARAMETER_INVENTORY_RECONCILED
- BENCHMARK_LINEAGE_AUTHENTICATED
- COMPLETE_GAME_UNIVERSE_ACCOUNTED_FOR
- COMPLETE_PLAYER_UNIVERSE_ACCOUNTED_FOR
- FORECAST_VINTAGES_RECONCILED
- PROSPECTIVE_AND_REPLAY_POPULATIONS_SEPARATED
- HISTORICAL_INFORMATION_LIMITATIONS_QUANTIFIED
- MARKET_COMPARISON_MATCHING_VERIFIED
- PLAYER_DISTRIBUTION_METRICS_COMPLETE
- GAME_DISTRIBUTION_METRICS_COMPLETE
- ROLE_STAT_ANALYSIS_COMPLETE
- CALIBRATION_ANALYSIS_COMPLETE
- DEPENDENCE_AWARE_INFERENCE_COMPLETE
- ROOT_CAUSE_PRIORITIES_EVIDENCE_SUPPORTED
- PARAMETER_ESTIMATION_PRIORITIES_ESTABLISHED
- BENCHMARK_MANIFEST_FROZEN
- RESOURCE_SAFETY_VERIFIED
- STAGE_09_READY

Do not award PASS to unsupported evidence.

Do not obscure limitations in an aggregate completion percentage.

#### 20. Final handoff

Return the complete Stage 08 engineering report.

Include the definitive performance tables, exact sample definitions, unresolved blockers, benchmark identities and prioritized Stage 09–17 optimization requirements.

Preserve the authoritative ledger and governance records.

Report the separately scheduled production deployment status.

Do not begin Stage 09 independently.

Do not rebuild the model.

Do not refit the predictive architecture during this benchmark.

BEGIN STAGE 08 NOW.
