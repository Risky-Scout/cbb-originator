# Production scoring, retraining and deployment policy v1.0

**Date:** 2026-10-07 · **Status:** AUTHORITATIVE CONTRACT, **FROZEN**. **No production implementation exists or is authorized by
this document.** It is frozen now (Blueprint v1.4 §6, amendment C) so that every model artifact created from Phase C
onward carries the right identity and provenance. Storage: `docs/PREDICTION_LEDGER_SCHEMA.md`. Publication:
`WIZARDOFODDS_CBB_ORIGINATOR_INTEGRATION_SPEC_v1_0.md`. Probability authority: `docs/PROBABILITY_ENGINE_POLICY.md` (unchanged).

## 1. Entities (each an append-only ledger table)

| entity | what it is | key |
|---|---|---|
| `information_snapshot` | the frozen input set for a run: data cutoff, dataset version, source snapshot ids, availability snapshot ids, schedule version | `snapshot_id` |
| `game_checkpoint` | (game, checkpoint code T24H..T10M) with anchor tip, cutoff and fidelity | `checkpoint_id` (same id as `pit.checkpoint`) |
| `prediction_run` | one execution: snapshot × checkpoint set × model bundle; start / end, host, code SHA, status | `run_id` |
| `model_artifact` | a trained model: version, training manifest(s), dataset / feature versions, hyper-parameters, seeds, metrics, file sha256 | `model_version` |
| `calibration_artifact` | a fitted calibration map: version, fit window, method, base model version, sha256 | `calibration_version` |
| `distribution_artifact` | the joint distribution of one game at one checkpoint: method (EXACT_PMF / MC_BANK), draw count, seed, MCSE / convergence diagnostics, storage URI, sha256 | `distribution_id` |
| `prediction` | one priced quantity: game, player (nullable for team markets), market, line, side, probability, derived fair odds, distribution reference | **immutable** `prediction_id` |
| `market_probability` | benchmark-namespace market price aligned to a checkpoint (never an originator input) | `market_obs_id` |
| `publication_event` | the act of sending predictions to WizardOfOdds: payload hash, idempotency key, response, timestamp | `publication_id` |
| `official_result_version` | a version of the canonical official outcome of a game (initial, then corrections) | (`game`, `result_version`) |
| `grade_event` | the grading of one prediction against one official result version | `grade_id` |
| `promotion_decision` | a human-approved change of the production model / calibration bundle | `decision_id` |
| `incident` | any failed gate, wrong publication, data defect or rollback, with timeline and resolution | `incident_id` |

## 2. Append-only / no-overwrite

* Ledger rows are **inserted only**. They are never updated or deleted. A correction is a new row that references the
  row it supersedes (`supersedes_id`) with a reason. Storage enforces this (database permissions / trigger, plus a hash
  chain: `row_hash = sha256(content || previous_row_hash)` per table).
* A published prediction is never edited. A changed value at a later checkpoint is a **new** `prediction_id`.
* The ledger holds no secrets and no sportsbook-derived originator inputs.

## 3. Mandatory identity / provenance on every prediction

Every `prediction` row (directly or through its run / distribution) must resolve to:
`game`, `player`, `market`, `line`, `checkpoint`, `data_cutoff_utc`, `dataset_version`, `feature_version`,
`model_version`, `calibration_version`, `distribution_id`, `probability_method` (EXACT_PMF | MC_BANK | CUSTOM_LINE_EXACT |
CUSTOM_LINE_MC), `draw_count`, `seed`, `mcse` and `convergence_status`, and `publication_timestamp` (on the publication
event). The eligibility manifest ids and hashes of every model in the bundle are recorded on `model_artifact`. A model
artifact missing any field is not promotable.

## 4. Canonical official outcome and stat corrections

* The canonical realized outcome is the **final official full-game box score including overtime** (Amendment V2 §9),
  as reported by the production source holding commercial rights (`docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md`).
* `official_result_version = 1` is the first complete final box received at least 2 h after the end of the game. Each
  later change (a stat correction) is a new version with `received_at`.
* Grading uses the **latest version received within 72 h of the game's end** (the *settlement version*). Later
  corrections are recorded and produce re-grade events flagged `post_settlement`. Headline metrics use settlement grades.
  Correction-sensitivity is reported separately.
* A forfeit, cancellation, suspended or non-final game produces no settlement. Its predictions are graded `VOID`.

## 5. Grading definitions

Let `p` be the published probability that the event occurs, and `y ∈ {0, 1}` its settled outcome.

| quantity | definition |
|---|---|
| over / under on line `L` (half-point) | `y = 1[X > L]` for OVER |
| integer line `L` | `y = 1[X > L]`; `X = L` is a **PUSH** |
| Brier | `(p − y)^2`, mean over graded rows |
| log loss | `−[y ln p + (1−y) ln(1−p)]`, with `p` clipped to `[1e-6, 1 − 1e-6]`. Clipping is reported, never silent |
| CRPS (count statistic X, predictive CDF F) | `Σ_{k=k_min}^{k_max} (F(k) − 1[x ≤ k])^2` over the integer support (exact for discrete distributions), computed from the distribution artifact |
| calibration | reliability by decile and by market, ECE with equal-mass bins, calibration slope / intercept |
| percentages (FT%, 2P%, 3P%, TS%) | graded only when the denominator is > 0 in the official box; otherwise **UNDEFINED** |

Outcome classes:

| class | when | in metrics |
|---|---|---|
| `WIN` / `LOSS` | settled 1 / 0 | yes |
| `PUSH` | integer line hit exactly | excluded from Brier / log loss, counted and reported |
| `VOID` | game not final, player DNP on a market declared void-on-DNP, cancelled / forfeited game | excluded, counted |
| `UNDEFINED` | percentage with zero denominator; player not on the official box | excluded, counted |

A player who does not appear on the official box is `VOID` for counting-stat markets **only** if the market contract says
void-on-DNP. Otherwise `X = 0` and the row is graded. The contract per market is fixed in the publication spec.

## 6. Retraining and recalibration cadence (candidate generation, not automatic promotion)

| artifact | candidate cadence | trigger |
|---|---|---|
| structural models | once per season (preseason), plus mid-season (after ~ 40 % of games) | scheduled; or a monitored drift breach (§8) |
| calibration maps | weekly candidate during the season | scheduled; candidate fit only on graded settlement data strictly before the fit date |
| hot path (live update) | **no structural retraining**; conditioning on new availability / schedule only (≤ 60 s update SLA) | availability or schedule change |

A candidate never replaces production by itself. It enters shadow evaluation (§7) and needs a promotion decision.

## 7. Promotion rules

A candidate bundle may be promoted only if **all** hold:

1. built from a **MODEL_READY** dataset with complete provenance (§3) and passing eligibility manifests;
2. evaluated on frozen walk-forward folds and on ≥ 2 weeks or ≥ 500 graded shadow predictions (whichever is larger)
   on the same games and checkpoints as production;
3. non-inferior on log loss (paired difference, one-sided 95 % bound ≤ +0.002 nats per prediction), and no
   market-family ECE worse by > 0.01;
4. accounting / coherence gates pass on every distribution artifact (hard identities, 200 player-minutes, MCSE within
   policy);
5. a written human approval (`promotion_decision.approved_by`, two distinct people when available) with the evidence
   attached.

## 8. Monitoring, rollback and no-publish conditions

**Rollback** to the last promoted bundle (a recorded `promotion_decision` of type ROLLBACK) when any of these occur:
* a publication-time gate failure rate > 2 % of games in a day;
* a rolling 7-day log loss worse than the previous bundle's shadow by > 0.01 nats (paired), or a calibration slope
  outside [0.85, 1.15];
* an accounting / coherence violation in any published distribution;
* an incident ruling.
Rollback is a pointer change to an existing artifact. No retraining happens during a rollback.

**No-publish conditions** (the game, or the affected market, is withheld, and an incident is logged when unexpected):
missing or stale availability snapshot (older than the checkpoint's freshness bound: 6 h for T24H, 90 min for T6H,
20 min for T1H / T30M / T10M); availability source failing G4B; schedule version older than 2 h at T1H or later;
unmapped team or player in the projected rotation; MCSE above policy; convergence failure; dataset / model / calibration
version not promoted; the commercial-rights gate not satisfied for any source used; lockbox flag; game outside the
D-I vs D-I flagship universe.

## 9. Shadow-production requirements (Phase E)

Predictions are produced, timestamped and **locked before tip** at all five checkpoints, written to the ledger, graded
after settlement, and **never published**. Shadow must run ≥ 14 consecutive operating days with ≥ 99 % of eligible games
priced at T1H, a zero-incident final week, and G4B satisfied, before Phase F promotion can be requested.

## 10. Human approval requirements

Human approval is required for: every promotion and rollback (§7, §8); opening the lockbox
(`docs/LOCKBOX_OPENING_PROTOCOL.md`); any change to this contract, the ledger schema or the publication spec (new
version); enabling publication of a market family for the first time; enabling a new production data source (rights
gate).
