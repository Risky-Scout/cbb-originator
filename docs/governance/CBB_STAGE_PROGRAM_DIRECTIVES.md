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

## 2. Stage register (names as recorded in the durable evidence)

| stage | subject | status | evidence |
|---|---|---|---|
| 01 | baseline (receipts, release integrity, gates) | accepted | `docs/audits/2026-11-27-baseline` |
| 02 | feature warm-up | accepted | `docs/audits/2026-11-27-stage2` |
| 03 | availability sync / quote wake | accepted | `docs/audits/2026-11-27-stage3` |
| 04 | roster / transfer / identity integrity | accepted | `docs/audits/2026-11-27-stage4` |
| 05 | injury / availability / confirmed-lineup mathematics | accepted (engineering) | `docs/audits/2026-11-27-stage5` |
| 06 | independent probability / export mathematics + parameter provenance | this addendum | `docs/audits/2026-11-27-stage6` |
| 07 | historical information availability (bitemporal replay) | not started | assigned items in the ledger |
| 08 | corrected-engine evaluation (historical defective forecasts vs valid corrected replays) | not started | |
| 09 | joint participation and minutes | not started | |
| 11 | opportunity redistribution / conditional production | not started | |
| 16 | player-to-team translation (γ, β) | not started | |
| 19 | operations (export redundancy, writer-lock time, refresh latency) | not started | |

Stages 10, 12–15, 17, 18 and 20 have not had their subject recorded in any durable document in this repository. Stage 06 therefore names a
deficiency's **mathematical domain** and leaves the stage number open when the domain does not match a recorded stage. The owner's program
list should be added here when it is next issued.

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

## 4. Binding Parameter Estimation, Provenance and Validation Directive (requirements as cited)

The full text of this directive was not delivered to the Stage 06 session. Its requirements, as the Stage 06 addendum cites them, are:

* Classify every material predictive parameter as one of: EMPIRICALLY_FITTED, VALIDATED_FIXED, REGULARIZED_ESTIMATE, MATHEMATICAL_CONSTANT,
  OPERATIONAL_CONTROL, SAFETY_CONTROL, UNSUPPORTED_HEURISTIC or UNKNOWN_PROVENANCE.
* For fitted parameters, trace three links: training (the estimator and its data window), persistence (the artifact) and runtime consumption (the scoring-path read).
* Identify silent fallbacks and hardcoded defaults.
* A fit artifact's existence is not proof that each parameter was fitted.
* No indiscriminate refit. Each deficiency is assigned to its mathematical stage.

When the full text is supplied, insert it here verbatim. The Stage 06 inventory then needs a re-check against any additional requirement.

## 5. Production end-state (summary; full text at `../../CBB_RESUME_DIRECTIVE_PRODUCTION_END_STATE.md`)

Done means: the Droplet `/current` scores with the final validated engine, proven by the 14-item live proof.
* Player: RA2V1 / PTSC / TOVT1 / DEV1 / CTX1 / PI2 / PS2.
* Game: 1.2.0 / GC3 / TP1P / bridge.

A fix that exists only in research, a branch, CI or a report does not count. If any validated enhancement is shadow-only, FINAL_DEPLOY = FAIL.
