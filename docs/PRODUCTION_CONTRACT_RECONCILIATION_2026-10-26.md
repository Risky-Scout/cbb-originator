# Production contract reconciliation: real 2026-27 prediction, pricing and WizardOfOdds readiness (2026-10-26)

**Scope:** production infrastructure, contracts, guards, monitoring semantics and publication work.
- No model change.
- No scheduler change.
- No merge to main, no public publication, no wagering.

**Branch:** `production/cbb-2026-27-readiness`, created from `ca00d6a`. It is **local only**: this Mac has no GitHub
credentials for git, so the branch is not pushed.

**Companion documents:**
- `docs/integrations/WIZARDOFODDS_CBB_ORIGINATOR_PRODUCTION_CONTRACT_v2.md` (the new contract)
- `docs/STABILIZATION_GATE_2026-10-24.md`, `docs/REPOSITORY_MAP.md`

## 0. Commits on the production branch

| commit | content |
|---|---|
| `ca00d6a` | base: `release/cbb-v2-prospective-shadow`, the code the active scheduler runs |
| `a02cabb` | stabilization: golden production-equivalence fixture (fast-forward) |
| `382b5ae` | stabilization: map, registry, README, gitignore (fast-forward) |
| `e90ee96` | monitor market-list renames (§4) |
| `0751bc2` | 2 h schedule-freshness gate (§3) |
| `9786836` | public / internal boards, point-in-time and quote gates, fail-closed matrix, idempotent publication records, checkpoint-invariance test (§6–§11, §18–§20) |
| `26bb5c4` | production CI candidate workflow (§15) |
| `50d0fe1` | commercial-rights publication gate (§12) |
| (this commit) | this report and contract v2 |

**How the stabilization commits were integrated:** by fast-forward (`git merge --ff-only 382b5ae`). The stabilization
branch is a linear child of `ca00d6a`, so this is the safest normal method: no merge commit, no rewrite.
- No research branch was merged.
- `release/cbb-v2-prospective-shadow`, `main`, the scheduler and the ops-state branch are untouched.

## 1. Model unchanged (the hard gate)

The gate ran after every code stage (`tests/test_prod_golden_equivalence.py`). It was exact on darwin-arm64 every
time:
- frozen artifact hashes;
- model inputs;
- JM1 bank sha256;
- base-stat and combo predictions;
- pricing math.

**Diff evidence:**
- `git diff e1353e5 HEAD` over the five frozen artifacts and all predictive code (`cbb_platform/v2`, `props_v0*`,
  `props_v1_pricing`, `stage3`) has **no modified file**. The only differences are six *added* diagnostic audit
  reports that are already in the `ca00d6a` lineage.
- The branch's own changes, `382b5ae..HEAD`, touch only:
  - `prod/config.py` (two new constants);
  - `prod/side_monitor.py` and `prod/prospective_monitor.py` (renames);
  - two new modules: `prod/schedule_freshness.py` and `prod/production_board.py`;
  - tests;
  - one new CI workflow.

**Artifact hashes (sha256):**

| artifact | sha256 | status |
|---|---|---|
| marginal | `bfcef1fb…d10f` | exact |
| jm1_final | `b932fa21…28f9` | exact |
| residual_j2 | `e4254867…70f1` | exact |
| production_manifest | `7d83e9cf…cbcab` | exact |
| rollback S0 / J2 | `b41564a9…b1ff` | exact |

`prod.run verify-artifacts`: PASS.

**Focused tests:** 183 passed / 0 failed, then 25 more after the rights gate. The set:
- the CI production set;
- golden and registry;
- the new gates;
- `test_v2_jm1_final_production`, `test_props_v1_pricing`, `test_relocation`.

The full 30+ minute suite was **not** run (directive §22). It runs once, at the final cutover candidate.

## 3. Schedule freshness: `2_HOURS_TARGETED_REFRESH_FAIL_CLOSED` (IMPLEMENTED)

**Implementation:** `cbb_platform/prod/schedule_freshness.py` with the constants `PC.SCHEDULE_FRESHNESS_MAX_AGE_HOURS = 2.0`
and `PC.SCHEDULE_FRESHNESS_POLICY`.

**Verification instant:**
- Per game, it is the latest instant the provider returned **the payload the current state was built from**. This
  includes `FETCHED_UNCHANGED` re-fetches, read from the CBBD request ledger.
- A verification after the checkpoint is never used.
- When the game sits on a window boundary, every window that holds it must be fresh.

**Targeted refresh:** a stale game gets one refresh through the existing production sync, with
`refresh_schedule_windows=[its month window]`. The other six windows keep their 6 h TTL, so in steady state they are
cache hits.
- The end-to-end test (`test_targeted_refresh_through_the_production_sync`) proves that only the stale game's window is
  fetched live.
- **Note:** if the routine 6-hourly sync has itself lapsed (state older than 6 h), that sync's own TTL re-fetches the
  lapsed windows. That is the existing routine-sync behaviour, not caused by this gate.

**Post-refresh checks:**
- canonical and provider id unchanged;
- home / away unchanged;
- tip unchanged and **EXACT**;
- the game is listed and not cancelled or postponed;
- the game has not started;
- the age is ≤ 2 h.

Otherwise the result is `SCHEDULE_FRESHNESS_GATE = FAIL`, with publication, pricing and actionable bet all false. An
audit record is appended to `data/prod/gates/schedule_freshness.jsonl`.

**Conflict resolution:** the policy's "2 h at T1H" now governs publication and pricing. The code's 36 h
`SCHEDULE_MAX_AGE_HOURS` stays the run-level STALE_STATE **recording** rule. It is unchanged, so nothing is weakened,
and the 2 h gate is additive.

**For the architect:**
- Publication requires an EXACT tip (`SCHEDULED_TIP_NOT_EXACT`). A DATE_ONLY or TBD tip cannot prove "not started".
- This gate is not yet wired into the scheduled tick (§16). It runs where boards are built.

**Tests:** `tests/test_prod_schedule_freshness.py` (17).

## 4. Monitor market lists (RESOLVED; diagnostics only)

| old name (module) | new name | real purpose | membership (unchanged) |
|---|---|---|---|
| `OPENING_MARKETS` (`side_monitor`) | `SIDE_MONITOR_OPENING_SEASON_MARKETS` | Over/Under side counters and warnings for quoted sides when the team has played ≤ 2 games (historical RED subgroups) | PTS, AST, STL, TOV |
| `TRANSFER_MARKETS` (`side_monitor`) | `SIDE_MONITOR_TRANSFER_IN_MARKETS` | side monitor, `cold_start_class == COLD_TRANSFER_IN` | STL, STOCKS, TOV, REB |
| `EARLY_MARKETS` (`prospective_monitor`) | `EARLY_SEASON_REPORT_MARKETS` | cumulative / daily report strata: team games ≤ 2 **and** first 30 days (projections, lines, sides) | PTS, AST, STL, TOV, PRA, PR, PA |
| `TRANSFER_MARKETS` (`prospective_monitor`) | `TRANSFER_COLD_START_REPORT_MARKETS` | report strata: COLD_TRANSFER_IN **and** COLD_NO_HISTORY | PTS, REB, STL, TOV, STOCKS |

- The lists differ on purpose: two reports with different historical motivation. They were **not** unified.
- Report output keys are unchanged.
- `tests/test_prod_monitor_semantics.py` proves:
  - memberships are byte-equal to `ca00d6a`;
  - the old names are gone;
  - the new names are distinct;
  - report keys follow the lists;
  - no forecast or pricing module references any of them (AST scan).
- The golden fixture is unchanged.

**Results:** FORECASTS_CHANGED = FALSE · PRICING_CHANGED = FALSE · MONITOR_SEMANTICS_PRESERVED = TRUE.

## 5. Contract supersession

`WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md` is unedited and kept as evidence. Its status is
**SUPERSEDED_PENDING_V2**.

v2 is `docs/integrations/WIZARDOFODDS_CBB_ORIGINATOR_PRODUCTION_CONTRACT_v2.md`. It has:
- 20,000 draws;
- exactly the 12 produced markets;
- no TS%, PF, ORB, DRB, FT, FT%, 2P, 2P% or 3P%.

v2 §11 lists every difference from v1 and why.

## 6. Public projections vs private pricing (PASS / PASS)

**Implementation:** `cbb_platform/prod/production_board.py: build_boards` reads one `run.daily` output (summary,
PREDICTION ledger records, immutable state version). The test proves the ledger bytes are unchanged.

**Public board:**
- every scored (game, player, market), including the ones with no line;
- the test asserts publishable rows exist that have no sportsbook quote;
- only `PUBLIC_FIELDS` are emitted.

**Existing projection fields documented (not invented):**
- `p_play`
- `pmf_q75`
- `distribution_method`
- `projection_basis = MEAN_CONDITIONAL_ON_PLAYING`. The projection is the PMF mean conditional on playing.

**Internal board:**
- line, P(over / under / push), fair decimal and American for both sides, sportsbook, executable price, quote and
  collector timestamps, quote age;
- raw edge;
- the quote gates.

**Visibility:** `PUBLISH_PRIVATE_FIELDS = False`. Adding the private fields to the public board requires an explicit
flag (tested).

**FIXTURE data is never publishable** (tested).

## 7. Checkpoint policy

**Audit of existing timing:**

| item | finding |
|---|---|
| validated cutoff | `TIP_MINUS_1H` only (`PC.CUTOFF_VALIDATION`: "T24H/T6H/T30M/T10M never built") |
| authorized multi-checkpoint policy in the repo | **YES, surfaced:** `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md` §8–§9 names five checkpoints, **T24H, T6H, T1H, T30M, T10M**, with availability freshness bounds of 6 h, 90 min and 20 min. Only T1H was ever implemented |
| current scheduler | one score per game, at the first tick in [tip − 110 min, tip − 60 min) (`SCORE_LEAD_MINUTES = 50`; cron :12 / :42 at 12–04Z Nov–Mar) |
| what can change between checkpoints | schedule (a tip move changes the cutoff), the roster snapshot (≤ 7 d; transfers / departures change suppression), newly ingested earlier games, sportsbook quotes, and availability (**no production availability source exists**, see §23) |
| what cannot change for a fixed state | model inputs, the JM1 bank and the projections. `tests/test_prod_checkpoint_invariance.py`: the same state scored at T−9 h 20 m and T−8 h 30 m gives identical projections (600 rows) and identical bank sha256 |

**Earliest legitimate scoring point:** the first checkpoint at which `slate.build` returns SCORE with no STALE_STATE.
That means:
- the game is listed, with an EXACT tip, inside the 36 h horizon;
- for both teams, every earlier game whose result is knowable by tip − 1 h is fully ingested;
- the roster is ≤ 7 d old and the preseason evidence is complete.

The existing STALE_STATE guard enforces this. On a back-to-back tournament day, it holds the game until the previous
night's result is ingested.

**PROPOSED_PRODUCTION_CHECKPOINT_POLICY.** Architect approval is required. It is NOT enabled.

| checkpoint | when | rescoring? | why |
|---|---|---|---|
| `P24` (policy T24H) | first tick ≥ tip − 24 h at which the game is SCORE-eligible; retried each tick until eligible | yes (same frozen model, same TIP_MINUS_1H feature definition) | useful public projections a day ahead; legitimacy shown by the invariance test plus the STALE_STATE guard |
| `P6` (policy T6H) | first tick ≥ tip − 6 h | yes; it creates a new version **only if** the content changed (otherwise a `DUPLICATE_NOOP` replay) | picks up roster or schedule changes and morning lines |
| `P1` (policy T1H, current) | the existing [tip − 110, tip − 60) window | yes; the authoritative pricing checkpoint and the recorded shadow prediction | the validated cutoff |
| `T30M` / `T10M` | after the validated cutoff | **no rescoring.** These checks may only suppress or withdraw (a superseding board) on material late news | scoring after tip − 1 h is `PREDICTION_AFTER_CUTOFF` for the frozen model |

Every board is immutable and a later checkpoint is a new board. There is no post-tip publication, and projections never
wait for lines. **Cost:** CBBD calls are unchanged (scoring reads state). The Odds API is unchanged: quotes are still
acquired only in the existing window.

## 8. Point-in-time hard gate (PASS)

`production_board.point_in_time` exposes four statuses:
- `POINT_IN_TIME_LEGALITY`
- `TARGET_GAME_FEATURE_LEAKAGE_DETECTED`
- `TARGET_GAME_OUTCOME_LEAKAGE_DETECTED`: the target result, team box or player box appears in the state increment or
  in the run's as-of database
- `FUTURE_DATA_LEAKAGE_DETECTED`

Any TRUE flag means no publication, no pricing and no betting recommendation for the game. Each flag is tested.

Existing PIT logic is untouched:
- `slate` cutoff and STALE_STATE;
- `features` as-of masking;
- `state` chronology;
- `quote_freshness` POST_CUTOFF_QUOTE.

## 9. Quote quality gate (PASS)

There are eleven explicit gates (contract v2 §7), each tested to fail closed:
- exact event;
- strict player identity (matching.py, never fuzzy);
- supported market;
- valid line, side and price;
- freshness ≤ 20 min **re-checked at the checkpoint**;
- game not started;
- provider-returned quote;
- model hash;
- point in time.

There is no substitution. Each book and line row is gated independently.

## 10. Fair vs executable (PASS)

- Every internal row carries `price_kind = {fair: MODEL_FAIR_PRICE, executable: SPORTSBOOK_EXECUTABLE_PRICE}`.
- `raw_edge_ev_per_unit` is populated only when a real executable quote passed every gate. It is `null` for a stale,
  absent or ungated quote (tested).

## 11. Real-money policy

`ARCHITECT_BETTING_POLICY_REQUIRED = TRUE`. `config/betting_policy.json` does not exist. The required fields are:

| field | meaning |
|---|---|
| `eligible_sportsbook_accounts` | list of book keys / accounts |
| `bankroll` | amount and currency |
| `stake_sizing_method` | flat / Kelly / other |
| `kelly_fraction` | if Kelly |
| `max_wager` | |
| `daily_exposure_cap` | |
| `player_exposure_cap` | |
| `game_exposure_cap` | |
| `correlated_market_exposure_cap` | e.g. PTS with PRA / PR / PA on the same player |
| `min_edge_to_wager` | EV per unit |
| `approved_by`, `approved_at` | approval |

`AUTOMATIC_ACTIONABLE_WAGER = DISABLED`, and `actionable_bet_allowed` is false on every row even with a complete policy.
No bet placement is authorized.

## 12. WizardOfOdds publication mechanism audit (PENDING_SITE_CONFIRMATION)

The audit was read-only, with no network call to any Wizard host.

| mechanism | where | CBB? |
|---|---|---|
| SSH / scp, `SHA256SUMS`-verified bundle, two-rename atomic swap with `.previous`, HTTPS read-back, rollback script | `~/ncaaf-originator-2026` (`publish-wizard.yml`, `deploy_wizard.sh`, `wizard_remote_activate.sh`, `verify_wizard_public.py`). GitHub env `wizard-production`; secrets `WIZARD_SSH_{HOST,PORT,USER,PRIVATE_KEY,KNOWN_HOSTS}`. Remote `/var/www/sportsodds/tools/odds-scanner/predictions/NCAAF`, with the `wizard-deploy` account confined to that directory. No staging | **NCAAF only** |
| FTP STOR overwrite of HTML with `window.EMBEDDED_DATA` (non-atomic, plain-FTP fallback, no checksum) | `~/repos/nba-player-props-model` (`WOO_FTP_*`) | NBA only; legacy |
| FTP delete-then-STOR | `~/wnba-player-props-pmf-model` (`/predictions/WNBA`) | WNBA only |
| manual Cyberduck upload to `dev.wizardofodds.com/.../predictions/` | `~/woo_models/CyberDuck` | old NCAAB *team* dashboard; manual |
| HTTPS `POST /api/cbb/originator/v1/boards` | v1 spec only | **exists nowhere** |

**Conclusion:** no CBB destination, page, consumer, credential environment or staging path is confirmed, and
`PC.PROPOSED_PUBLIC_PATH` is unapproved. Nothing was invented.

**What was built against no specific site:**
- the transport-independent layer: envelope, idempotency, receipts, supersession, read-back reconciliation,
  no-post-tip, retry safety;
- `PendingSiteConfirmationTransport`, which always refuses;
- a private `LocalStagingTransport` for tests.

**Separate frozen gate found:** `COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md` (spec v1 §9) requires every
production source to be `PRODUCTION_APPROVED` before publication.
- The register says Starter Pack `RESEARCH_ONLY` and CBBD API `UNVERIFIED`.
- The 2026-10-23 user attestation (`docs/OFF_MACHINE_DATA_LICENSE.json`) covers the Starter-Pack-derived extract,
  including "derived predictions (WizardOfOdds.com)". The register itself was never updated, and the CBBD API tier terms
  are unreviewed.
- The publisher now refuses public transports until the register is approved (`50d0fe1`). The register was not
  edited to pass.

**WIZARDOFODDS_INPUTS_REQUIRED** (from the architect or site owner):
1. **Transport:** SSH atomic swap (the NCAAF pattern, recommended), or another mechanism. Confirm that the v1 POST API
   is retired.
2. **Destination:** the approved remote directory and public URL for CBB player props (approve or replace
   `/tools/odds-scanner/predictions/basketball/ncaab/player-props/`).
3. **Server account:** a restricted deploy account (or an extension of `wizard-deploy`) confined to the CBB parent,
   with the sticky-parent and ownership checks, and the pinned host key.
4. **GitHub environment** on `Risky-Scout/cbb-originator` (e.g. `wizard-production`, main-only) with the
   `WIZARD_SSH_*` secrets.
5. **Staging:** a staging or dev path or host for automated tests (e.g. `dev.wizardofodds.com`), or explicit
   confirmation that none exists.
6. **Page and schema:** whether a self-contained static page (our `index.html` plus JSON) is acceptable, or a required
   template, JSON schema or hub link. Also the rounding and presentation rules.
7. **Caching:** nginx / CDN cache behaviour on the path (for a valid read-back), plus rate and size limits.
8. **Content sign-off:** the public disclaimer and branding wording, and whether `p_play` and `pmf_q75` are shown.
9. **Rights:** an approved rights-register version for the CBBD API tier and the Starter Pack-derived history
   (publication of derived predictions).

## 13. Contract v2 requirements

All are defined in contract v2:
- version;
- identities;
- tip;
- checkpoint / version;
- generated_at (`checkpoint_at`);
- data cutoff;
- schedule verification;
- model identity and hashes;
- 20,000 draws;
- markets;
- projection and pricing fields;
- PIT status;
- eligibility;
- suppression reasons;
- payload hash;
- idempotency key;
- receipts;
- supersession;
- no-post-tip;
- retry;
- read-back;
- failure;
- rollback.

A correction never rewrites history: the earlier payload and receipt are kept, as the tests show.

## 14. Operational status terminology

**The model identifier stays as it is:**
- `cbb-v2-jm1-final-shadow-1.0.0` is the historical and internal model identifier inside frozen artifacts and the
  manifest. It is not edited.
- `shadow`-named modules and files stay as they are, because renaming them would be an operational risk.

**The new operational status:**
- `production_board.OPERATIONAL_MODE = "PRODUCTION"`.
- `production_readiness()` returns `READY_FOR_REAL_2026_27_PREDICTION_AND_PRICING` **only when every gate passes**.
  Otherwise it returns `NOT_READY` with the failing list (tested).
- The current answer is NOT_READY (§24).
- The existing shadow readiness ceiling (`prod/readiness.py`, SHADOW_ONLY) is unchanged because it is on the golden
  path. The production board makes its own fail-closed decision.

## 15. Production CI candidate

**New workflow:** `.github/workflows/cbb-production-ci.yml` (push to `production/**`, manual dispatch). It includes:
- the existing production steps;
- golden and registry;
- schedule freshness, monitor semantics, checkpoint invariance, and boards / contract / publication;
- `test_v2_jm1_final_production` and `test_props_v1_pricing`;
- the fixture smoke.

`cbb-shadow-ci.yml` and the active `cbb-shadow-tick.yml` are unchanged.

**Status:** NOT_YET_RUN on GitHub, because the branch is not pushed. The same test set passes locally.

## 16. Main cutover plan (READY for review; NOT executed)

**Facts:**
- `main` = `e1353e5` + three commits that only add `.github/workflows/cbb-shadow-tick.yml`.
- That file is byte-identical on the production branch.
- `e1353e5` is an ancestor of the production branch.

So a merge of the production branch into main is conflict-free.

**Step 0. Prerequisites:**
- architect approval of this report;
- the branch is pushed (requires git credentials on this Mac, or push from a credentialed machine);
- the production CI candidate is green on GitHub;
- the full suite has run once on GitHub-hosted compute with 0 failures.

**Step 1. Quiesce:**
- Choose a gap between in-season crons. The safe window is after the 09:34Z tick completes and before 12:12Z.
- Confirm no `CBB shadow tick` run is queued or in progress.
- Record the ops-state head `H0`.

**Step 2. Merge** the production branch into main with a **merge commit** (no squash, no rebase).
- The workflow on main still checks out `release/cbb-v2-prospective-shadow`, so the runtime behaviour is unchanged.
- CI on main must be green.

**Step 3. Switch the scheduler.** This is one commit on main that edits **the same** `cbb-shadow-tick.yml` in place:
- Both `ref: release/cbb-v2-prospective-shadow` lines change to `ref: ${{ github.sha }}`, so the code comes from the
  same main revision as the workflow.
- These stay exactly as they are:
  - the workflow name and file;
  - `concurrency: group: cbb-shadow-ops-state, cancel-in-progress: false`;
  - `OPS_BRANCH`;
  - the cron lines;
  - the secrets.
- No second scheduled workflow is ever added.

**Step 4. Verify the first tick:**
- `due_check`;
- the ops head advances by exactly one commit from `H0`;
- `prod.run verify-ledger` and the side-monitor chain pass;
- tick ids are unique;
- the Odds request ledger shows no second paid fetch of the same event inside its 10 min reuse window;
- the settlement registry has no duplicate `game_id`.

**Step 5. Rollback:** revert the Step 3 commit. The release branch stays frozen and untouched as the rollback source.

**How each risk is prevented:**

| risk | prevention |
|---|---|
| duplicate ticks | one workflow file, an in-place edit, `DUE.json` gating, idempotent ticks (a no-information re-run makes no call) |
| two schedulers running at once | the same file and the same concurrency group with `cancel-in-progress: false`. The cutover is a single-commit edit, never an add-then-remove |
| state-branch collisions | single writer; `git_commit_push` pushes **non-force**, so a racing writer is rejected rather than merged; same `OPS_BRANCH` |
| duplicate publications | publication is not enabled. When it is enabled, `idempotency_key` plus append-only receipts give a `DUPLICATE_NOOP` replay |
| duplicate Odds API acquisition | the request ledger and cache live in the single working set; the 10 min reuse window and 36 h prop window are unchanged |
| duplicate settlement | `settled_games()` registry: a settled game is skipped; predictions are never mutated |

**Later, a separate approval:** wire `schedule_freshness.gate` and `production_board.build_boards` into the tick's score
step, write their audit and receipts to the ops tree, and only then enable a confirmed transport.

## 17. Ops state

`OPS_STATE_MIGRATION_REQUIRED = FALSE`. The branch name `ops/cbb-2026-27-shadow-state` is cosmetic. Continuity matters
more than the name.

**Optional migration plan** (not executed):
1. Quiesce, as in §16.
2. Record `H = git rev-parse origin/ops/cbb-2026-27-shadow-state`.
3. `git push origin H:refs/heads/ops/cbb-2026-27-production-state`, then verify `ls-remote` equals `H`.
4. In one commit, change `PC.OPS_BRANCH` and the workflow `OPS_BRANCH`.
5. After the first tick on the new name, confirm its parent is `H`.
6. Keep the old branch read-only, as an archive tag `ops-shadow-final-<H>`. Never delete it before architect approval.

The release tag `cbb-2026-27-shadow-ops` (encrypted assets) stays as it is.

## 18. Fail-closed matrix (`production_board.FAIL_CLOSED_MATRIX`)

| condition | PUBLICATION | PRICING | ACTIONABLE_BET | RETRY | ALERT |
|---|---|---|---|---|---|
| artifact hash mismatch | no | no | no | no | yes |
| wrong production season | no | no | no | no | yes |
| stale schedule (2 h gate, STALE_STATE, MISSING_SCHEDULE) | no | no | no | yes (targeted refresh) | yes |
| game identity ambiguity | no | no | no | yes | yes |
| player identity ambiguity | no (row) | no (row) | no | yes | no |
| unsupported player status (OUT, not on roster, transferred) | no (row) | no (row) | no | yes | no |
| stale quote | **yes** | no | no | yes | no |
| Odds API failure / integration unvalidated | **yes** | no | no | yes | yes |
| CBBD failure | no | no | no | yes | yes |
| missing state / roster | no | no | no | yes | yes |
| failed state promotion | no | no | no | yes | yes |
| point-in-time violation | no | no | no | no | yes |
| game already started | no | no | no | no | no |
| simulation failure / non-PSD / rotation shock | no | no | no | yes | yes |
| non-normalized PMF | no | no | no | no | yes |
| publication failure | no | (internal) yes | no | yes | yes |
| publication reconciliation mismatch | no | (internal) yes | no | no | yes |
| duplicate publication with conflicting payload | no | (internal) yes | no | no | yes |
| commercial rights unverified | no (public transport) | (internal) yes | no | no | yes |
| betting policy missing | yes | yes | no | no | no |

- Every code in `prod/readiness.py` maps to a row.
- An unknown code blocks everything and alerts.
- ACTIONABLE_BET is false in every row in this phase.

## 19–20. Idempotency and staging

**Idempotency** (tested):
- `publication_id` (logical slot), `payload_sha256` and `idempotency_key` are deterministic;
- a replay gives `DUPLICATE_NOOP`;
- a changed board gives `SUPERSEDES` (v2 with lineage; v1 payload kept);
- a tampered payload is rejected;
- a transport failure leaves a receipt, and the retry succeeds;
- a read-back mismatch is detected;
- an empty or all-suppressed board is rejected;
- there is no publication after tip.

**WizardOfOdds staging:** NOT_AVAILABLE. No site staging path is confirmed. All the directive §20 tests run against the
private local staging transport, with nothing public.

## 21. Repository consolidation (no branch deleted)

| class | branches |
|---|---|
| **KEEP_ACTIVE** | `main` (scheduler; future production source), `production/cbb-2026-27-readiness` (this candidate), `release/cbb-v2-prospective-shadow` (code the scheduler runs today; becomes the frozen rollback source after cutover), `research/cbb-v2-interconnected` (frozen champion source, tag `cbb-v2-jm1-final-2026-27`) |
| **OPS_STATE_DO_NOT_TOUCH** | `ops/cbb-2026-27-shadow-state` (+ release `cbb-2026-27-shadow-ops`) |
| **ARCHIVE_WITH_TAG** (evidence, keep reachable) | `research/cbb-v2-pts-rate-challenger` (R1_REJECT), `research/cbb-v2-pts-rate-challenger-r2` (R2_EARLY_REJECT), `research/cbb-v2-pts-rate-constrained-selection` (INFEASIBLE), `research/cbb-v2-pts-rate-calibration-audit` (NOT_JUSTIFIED), `release/cbb-v1-production` (tag `cbb-props-v1-production-baseline` exists), `release/cbb-v1-prelockbox`, `lockbox/open-2025-26`, `modeling/cbb-props-production-candidate-v1`, `modeling/player-props-challenger-v0`, `modeling/player-props-v0-participation`, `research/stage3-pit-feature-store` |
| **DELETE_AFTER_ARCHIVE** (fully contained in production lineage; tag first) | `release/cbb-v2-shadow-ready`, `release/cbb-v2-live-data-feed`, `release/cbb-v2-odds-quotes`, `release/cbb-v2-directional-bias-audit`, `integration/main-shadow-tick-workflow`, `stabilization/repo-gate-2026-10-24`, `modeling/cbb-v1-nightly-refit-validation` (same SHA as the candidate-v1 branch) |

Nothing is deleted until the cutover passes and the architect approves the retirement list.

## 23. Operational blockers remaining

1. **WizardOfOdds transport and destination:** PENDING_SITE_CONFIRMATION (§12, inputs 1–8).
2. **Commercial rights register:** Starter Pack is `RESEARCH_ONLY` and the CBBD API is `UNVERIFIED`. The publisher
   refuses public transports (§12, input 9).
3. **Availability source:** none in production. The tick passes no availability, so every player is `NO_REPORT`.
   - The frozen policy §8 makes a missing or stale availability snapshot a **no-publish condition**.
   - G4A/G4B are open (`docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md`).
   - An architect ruling is needed: acquire a source, or rule how NO_REPORT may be published.
4. **Quote integration:** not yet validated on a real 2026-27 event (`QUOTE_INTEGRATION_UNVALIDATED`; blocks pricing
   only).
5. **Betting policy:** not supplied (§11).
6. **Checkpoint policy:** the early-checkpoint proposal (§7) awaits architect approval.
7. **Tick integration:** the freshness gate, the boards and receipts are not yet wired into the scheduled tick (§16).
8. **Git credentials:** the branch is unpushed and the production CI candidate has not run on GitHub. The full suite
   has not run once.
9. **Main cutover:** not executed (§16).

## 24. Return values

```
PRODUCTION_BRANCH: production/cbb-2026-27-readiness @ <final commit of this report; see git log>
STABILIZATION_COMMITS_INTEGRATED: TRUE (fast-forward a02cabb, 382b5ae)
STABILIZATION_GATE: PASS
GOLDEN_EQUIVALENCE: PASS
FROZEN_MODEL_HASH_GATE: PASS
MODEL_CHANGED: FALSE
SCHEDULE_FRESHNESS_POLICY: 2_HOURS_TARGETED_REFRESH_FAIL_CLOSED
SCHEDULE_FRESHNESS_IMPLEMENTATION: PASS (gate module + tests; not yet wired into the scheduled tick)
MONITOR_LIST_CONFLICT: RESOLVED
MONITOR_CHANGE_AFFECTS_FORECASTS: FALSE
ACTUAL_DRAW_COUNT: 20000
SUPPORTED_PRODUCTION_MARKETS: PTS, REB, AST, FG3M, STL, BLK, TOV, PRA, PR, PA, RA, STOCKS
OLD_WIZARD_CONTRACT_STATUS: SUPERSEDED_PENDING_V2
PUBLIC_PROJECTION_BOARD_CONTRACT: PASS
INTERNAL_PRICING_BOARD_CONTRACT: PASS
EARLY_PROJECTION_CHECKPOINT_POLICY: current = single T-60 (one score per game in [tip-110m, tip-60m)); PROPOSED = P24 / P6 / P1 rescoring (same frozen model, TIP_MINUS_1H features, new version only on changed content) + T30M / T10M suppress-only; awaiting architect approval
POINT_IN_TIME_HARD_GATE: PASS
QUOTE_QUALITY_GATE: PASS
FAIR_VS_EXECUTABLE_PRICE_SEPARATION: PASS
ARCHITECT_BETTING_POLICY_REQUIRED: TRUE
WIZARDOFODDS_PUBLICATION_MECHANISM: PENDING_SITE_CONFIRMATION
WIZARDOFODDS_INPUTS_REQUIRED: §12 items 1-9
PUBLICATION_CODE_IMPLEMENTED: FALSE (transport-independent layer implemented and tested; no confirmed transport exists)
PUBLICATION_STAGING_TESTED: NOT_AVAILABLE (site staging); local private staging transport tested
PRODUCTION_CI_CANDIDATE: NOT_YET_RUN (branch not pushed; identical test set passes locally)
MAIN_CUTOVER_PLAN: READY
OPS_STATE_MIGRATION_REQUIRED: FALSE
OPERATIONAL_BLOCKERS_REMAINING: §23 items 1-9
CBB_ORIGINATOR_PRODUCTION_READINESS: NOT_READY
WIZARDOFODDS_PRODUCTION_PUBLICATION: PENDING_SITE_CONFIRMATION
REAL_MONEY_DECISION_SUPPORT: BLOCKED_BY_ARCHITECT_POLICY
```
