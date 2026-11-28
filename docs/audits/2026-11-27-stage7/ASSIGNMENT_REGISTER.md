# Stage 07 assignment register: every known open deficiency mapped to the authoritative 20-stage program

Stage mapping: `docs/governance/CBB_STAGE_PROGRAM_DIRECTIVES.md` §2.

**Sources:**
* every `assigned_to_*` / `open_items` / `remaining_*` key in `ROOT_CAUSE_LEDGER.json` (stages 01–06 and the 11-26 closure);
* the Stage 06 inventory `../2026-11-27-stage6/PARAMETER_PROVENANCE.md` (P1–P38, G1–G17, F1–F9, E-items);
* the Stage 07 findings in this folder.

**What changed.** Stage 06's provisional *domain* numbers are retired, and every item now has an authoritative stage.
No item is left unassigned. Items resolved in Stage 07 are marked **RESOLVED (07)**.

| # | deficiency / unsupported parameter / structural limitation | origin | stage |
|---|---|---|---|
| 1 | Replay over a later state is rule-only: late boxes (p99 8.2 h), provider revisions, later roster/portal/recruit snapshots and identity re-keys | S04, S05 | **RESOLVED (07)**: `compact_canonical` refuses a later state; replay contract |
| 2 | F1 silent frozen-model substitution | S06 | **RESOLVED (07)**: explicit DEGRADED record, run blocked, health blocker, tick identity |
| 3 | F2: CTX ledger incumbent mismatch silent; no row lineage; no as-of replay | S06 | **RESOLVED (07)**: INCUMBENT_MISMATCH status, `added_at` / `state_version` / `training_cutoff` / `incumbent` per row, `replay=True` |
| 4 | F3–F8 silent defaults (PI2 neutral defaults, design mean-imputation, env defaults, global R, game neutral / pending finals / baseline request) | S06 | **RESOLVED (07)** (visibility: per-run counters). The mathematical decisions are rows 15, 16, 22, 27 and 35 |
| 5 | F9: env flags not in lineage | S06 | **RESOLVED (07)**: `runtime_flags` in every run identity |
| 6 | `participation_keys` used event time (looser than the rule) | S07 | **RESOLVED (07)** |
| 7 | `model_source.resolve` ignored `promoted_at` (replay) | S07 | **RESOLVED (07)** (FUTURE_PROMOTION) |
| 8 | Curated statuses applied from 00:00 ET of `verified_on`: a replay with the current CSV backdates 18–43 h | S07 | **RESOLVED (07)** for replay (`replay_contract.curated_csv_at`: the CSV as deployed). The CSVs still lack an acquisition column, so replays must use the release in force |
| 9 | Game ratings persisted without the state they were fit on | S07 | **RESOLVED (07)** (`information_state` on each daily ratings state and receipt) |
| 10 | `pending_finals` returns 0 without a schedule table | S06 | **RESOLVED (07)** (visibility: `pending_finals_source`) |
| 11 | Benchmark populations: HISTORICAL_PRODUCTION vs CORRECTED_CANDIDATE_REPLAY vs MARKET_OVERLAP vs DIAGNOSTIC_RECONSTRUCTION. Includes the 268 Stage 05 defective INITIAL rows, the 204 pre-2f4a384 frozen-model ticks, and all game forecasts before C4 | S05, S06, S07 | **08** (`STAGE08_POPULATIONS.md`) |
| 12 | Selection-rule edge vs market not established; ACTIONABILITY_VALIDATED = False | 11-21 / 11-22 audits | **08** (benchmark), **18** (champion selection) |
| 13 | No jointly constrained team minutes; absent minutes not redistributed; teammate rotation effects | S04, S05 | **09** |
| 14 | Status-conditional p_play; no p_start / lineup; ACTIVE-unresponsive returner p_play; RJ Luis-type unresolved identities kept at p_play > 0 | S04, S05 | **09** |
| 15 | Minutes clip + Jensen inflation; role rank never null; PS2 tail rule / σ standardization (PS2 operates on minutes) | S06 | **09** |
| 16 | DEV1 / PI2 K = 31 (heuristic); PI2 clip [0.7, 1.3] binds for every first-appearance PTS player (now counted: `clip_bound_rows`); PI2 standardization constants; transfer strength diff defaulted to 0 | S06, S07 | **10** |
| 17 | Cold-start dispersion; recency k_t; prior-season rate projection | 11-22/11-24 | **10** |
| 18 | 4 likely duplicate canonical identities (Mayhugh, Cate, Ware, I. Brown); 10 players without CBBD roster rows; Carter / Maclin SOURCE_CONFLICT | S04 | **10** (identity linkage for priors). The conflicts stay fail-closed by design |
| 19 | Teammate usage / rate / stat means do not respond to an absence (opportunity redistribution) | S05 | **11** |
| 20 | CTX1 κ selected against the pre-PS2 incumbent → re-validate; the daily GLM is trained on uncorrected opening OOS rows (double-correction risk) | S06 | **11** |
| 21 | Environment shrink 4/6 (heuristic); NB threshold 1.1; 100-min thresholds; bands | S06 | **11** (environment / conversion) and **12** (NB threshold) |
| 22 | Design mean-imputation without an indicator (F6, now counted) | S06, S07 | **12** |
| 23 | v0 φ (incl. STL 0.16), `pmf_support_max`, PTS conditional φ fitted but unread (PTSC1 overrides); gated-but-unused dispersion family | S06 | **12** |
| 24 | PTS low-minute PMF shape; starters under-projected at prop lines | 11-21 / 11-22 | **12** |
| 25 | No predictive gate on the daily refit (integrity only); frozen corrections fitted against the opening incumbent, not the daily refit | S06 | **12** (calibration), validated in **08** |
| 26 | J2 PTS NB vs compound in the dependence copula; group → global R fallback (F7, now counted) | S06, S07 | **13** |
| 27 | Combination-prop coherence (Monte Carlo noise only; no defect) | S06 | **13** (monitor) |
| 28 | Game rating hyper-parameters τ 150, λ 5/5/2/10, ρ_carry 1: UNKNOWN provenance; GC3 carry 0.6; league prior; preseason roster uncertainty | S06 | **14** |
| 29 | GC3 seal / g1 backtest scripts not re-runnable; TP1P intermediate local-only | S06 | **14** (re-establish when re-fitting) |
| 30 | HCA; unknown teams (`gp.get(h,0)` maximum sd inflation, now visible); no opponent term; residual total bias after C4 (+0.27 overall, +1.0 on 2026-27) | S06 | **15** |
| 31 | γ = 0.3 unfitted and one-sided; β not implemented (fails closed if non-zero); confirmed-ACTIVE asymmetry in S | S05, S06 | **16** |
| 32 | Game variance parameters fitted on 1.0.0 (FINAL-basis) means, not refit after C4; endgame design constants; OT / pace-edge manifest not sealed into `params_sha256` | S06 | **17** |
| 33 | Game markets SHADOW only | release B | **17** / **18** |
| 34 | Protected-category validation (stat × role) | program | **18** |
| 35 | Export redundancy / idle-wake rebuilds; writer-lock 47 %; refresh latency (1,345 s); `availability_changes` NO_REPORT ≠ no row re-forecasts every game; selection_audit PYTHONPATH=current | S04, S05 | **19** |
| 36 | Health alert routing for the new MODEL_SOURCE_FALLBACK blocker; F-counter thresholds | S07 | **19** |
| 37 | **Disk headroom (corrected by the storage investigation):** the gate (38.5 GiB) cycles with run scratch; host growth 3.35 GB/day is ~70% non-CBB; CBB's persistent grower is the full-copy state versions. Mandatory Stage 19 requirements S19-STOR-1…7 below | S07, storage | **19** (mandatory). Activation proceeds when the gate passes (scheduled guarded deploy) |
| 38 | Consolidated activation of Stages 02–07 on the Droplet; 14-item live proof | program | activation of 7d160b1 scheduled (guarded, 2026-11-28 04:50Z / 16:50Z windows); live proof **20** |
| 39 | RotoWire commercial data authorization; WizardOfOdds deploy key (Massey, unrelated) | S04 | owner decision (external), recorded for **20** |
| 40 | Full text of the Binding Parameter Estimation, Provenance and Validation Directive, the Mathematical Enhancement Addendum and the Engineering Campaign Override not received | S06, S07 | **BLOCKED (07)**, owner to supply. The 55-group reconciliation against them is then a Stage 07 follow-up |
| 41 | Lockbox seasons ≤ 2025-26 are rule-only by nature (true acquisition unknown) | S07 | permanent limitation, **DIAGNOSTIC_RECONSTRUCTION** label in **08** |
| 42 | 2026-27 games whose pregame cutoff precedes the first retained state (2026-11-02 18:45Z), or falls in a non-retained GitHub-era promotion | S07 | permanent limitation. Counts in README §5; DIAGNOSTIC in **08** |

## Mandatory Stage 19 storage requirements (owner directive, 2026-11-27)

Evidence: `../2026-11-27-storage/STORAGE_ROOT_CAUSE.md`. No requirement may drop historical replay, rollback or evaluation evidence, and none may lower the disk gate.

| id | requirement | measured basis | expected effect |
|---|---|---|---|
| S19-STOR-1 | Canonical state versions in a relation-level content-addressed or append-only bitemporal store. A version becomes a manifest of relation hashes. Existing versions are migrated losslessly: each must reproduce its recorded relation sha256 through `walkforward.eligibility.state_asof` before any full copy is removed | 313 full 8–19 MB copies, 5.29 GB, 0.33–0.64 GB/day and rising with the season (≈50 MB/copy by March). `prod_schedule` changes 312/312, teams 1/312 | ≈4 GB reclaimed; state growth falls from about 0.5 to well under 0.1 GB/day |
| S19-STOR-2 | Delete run feature scratch as soon as the run's durable outputs are verified, keeping only the newest 1–2 runs that `shadow_tick.scoring_evidence` reads | ~540 MB per run, 18–24 h window, ~11 GB intraday swing | stable headroom; the gate stops cycling |
| S19-STOR-3 | Resolve the UNVERIFIED-run class: write durable PMF evidence for runs whose `market_pmfs.parquet` is incomplete, or record why the scratch must stay | 2 runs from 11-17, 1.07 GB; market_pmfs rows 19,008 / 17,988 vs predictions 30,360 / 28,860 | 1.07 GB, once their predictions are durably preserved |
| S19-STOR-4 | Compress raw provider payloads (cbbd, the-odds-api) at write, keeping the sha256 of the raw bytes | 20,026 uncompressed JSON, 0.47 GB | ≈0.4 GB; raw-cache growth falls by about 80% |
| S19-STOR-5 | Content-address tick and forecast PMFs per (game, model, inputs) so unchanged games are not rewritten every 30 min | ticks 0.34 GB, forecasts 1.0 GB, ops +0.12 GB/day | most of the ops growth |
| S19-STOR-6 | TTL or RETAIN-marker rule for `audit/`, like `qa/`; review the existing RETAIN markers | `audit/causal/ov/candidates` 0.63 GB (NO_REASON); qa 0.35 GB marked | ≤1 GB, after owner review |
| S19-STOR-7 | The disk gate in the deploy tooling is checked in bytes (38.5 GiB = 41,339,060,224) and logged, and the footprint report trends host vs CBB vs other tenants | gate unit ambiguity found in Stage 07 | no unit ambiguity |
