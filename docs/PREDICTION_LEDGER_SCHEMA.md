# Prediction ledger schema

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE CONTRACT, **FROZEN** (not implemented). Semantics:
`PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`. The ledger is a separate database (schema `ledger`), never
attached by feature code. It is append-only: no `UPDATE` or `DELETE` grants, and every table has a hash chain.

Common columns on every table: `created_at TIMESTAMPTZ NOT NULL` (our clock, UTC), `created_by VARCHAR NOT NULL`
(service / person), `supersedes_id VARCHAR NULL`, `supersede_reason VARCHAR NULL`, `row_hash VARCHAR NOT NULL`,
`prev_row_hash VARCHAR NOT NULL` (`row_hash = sha256(canonical_json(row minus hashes) || prev_row_hash)`, per table).

```sql
CREATE TABLE ledger.information_snapshot (
    snapshot_id VARCHAR PRIMARY KEY, data_cutoff_utc TIMESTAMPTZ NOT NULL,
    dataset_version VARCHAR NOT NULL, feature_version VARCHAR NOT NULL,
    schedule_version_refs VARCHAR[] NOT NULL, availability_snapshot_ids VARCHAR[] NOT NULL,
    source_manifest_sha256 VARCHAR NOT NULL, lockbox_clear BOOLEAN NOT NULL);
CREATE TABLE ledger.game_checkpoint (
    checkpoint_id VARCHAR PRIMARY KEY, canonical_game_id VARCHAR NOT NULL, checkpoint_code VARCHAR NOT NULL,
    anchor_tip_utc TIMESTAMPTZ NOT NULL, cutoff_utc TIMESTAMPTZ NOT NULL, checkpoint_fidelity VARCHAR NOT NULL);
CREATE TABLE ledger.model_artifact (
    model_version VARCHAR PRIMARY KEY, subsystem VARCHAR NOT NULL, artifact_sha256 VARCHAR NOT NULL, storage_uri VARCHAR NOT NULL,
    dataset_version VARCHAR NOT NULL, feature_version VARCHAR NOT NULL, eligibility_manifests JSON NOT NULL,  -- [{id, version, sha256}]
    code_sha VARCHAR NOT NULL, environment_lock_sha256 VARCHAR NOT NULL, hyperparameters JSON NOT NULL, seed BIGINT NOT NULL,
    train_seasons INTEGER[] NOT NULL, validation_metrics JSON NOT NULL, lockbox_touched BOOLEAN NOT NULL CHECK (NOT lockbox_touched));
CREATE TABLE ledger.calibration_artifact (
    calibration_version VARCHAR PRIMARY KEY, base_model_versions VARCHAR[] NOT NULL, method VARCHAR NOT NULL,
    fit_window_start DATE NOT NULL, fit_window_end DATE NOT NULL, artifact_sha256 VARCHAR NOT NULL, metrics JSON NOT NULL);
CREATE TABLE ledger.prediction_run (
    run_id VARCHAR PRIMARY KEY, snapshot_id VARCHAR NOT NULL, model_bundle JSON NOT NULL,   -- {subsystem: model_version}
    calibration_version VARCHAR NOT NULL, code_sha VARCHAR NOT NULL, host VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, status VARCHAR NOT NULL, gate_results JSON NOT NULL);
CREATE TABLE ledger.distribution_artifact (
    distribution_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, checkpoint_id VARCHAR NOT NULL,
    probability_method VARCHAR NOT NULL,       -- EXACT_PMF | MC_BANK
    draw_count BIGINT, seed BIGINT, mcse JSON, convergence_status VARCHAR NOT NULL,
    accounting_checks JSON NOT NULL, storage_uri VARCHAR NOT NULL, artifact_sha256 VARCHAR NOT NULL);
CREATE TABLE ledger.prediction (
    prediction_id VARCHAR PRIMARY KEY,         -- sha256(distribution_id | player | market | line | side)  -- immutable
    distribution_id VARCHAR NOT NULL, checkpoint_id VARCHAR NOT NULL, canonical_game_id VARCHAR NOT NULL,
    canonical_player_id VARCHAR, canonical_team_id VARCHAR, market VARCHAR NOT NULL, line DOUBLE, side VARCHAR NOT NULL,
    probability DOUBLE NOT NULL CHECK (probability BETWEEN 0 AND 1), fair_decimal_odds DOUBLE,
    probability_method VARCHAR NOT NULL, mcse DOUBLE, void_on_dnp BOOLEAN NOT NULL,
    data_cutoff_utc TIMESTAMPTZ NOT NULL, dataset_version VARCHAR NOT NULL, feature_version VARCHAR NOT NULL,
    model_version VARCHAR NOT NULL, calibration_version VARCHAR NOT NULL, draw_count BIGINT, seed BIGINT);
CREATE TABLE ledger.market_probability (           -- benchmark namespace only; never an originator input
    market_obs_id VARCHAR PRIMARY KEY, canonical_game_id VARCHAR NOT NULL, checkpoint_id VARCHAR,
    book VARCHAR NOT NULL, market VARCHAR NOT NULL, line DOUBLE, side VARCHAR NOT NULL, price_american INTEGER,
    no_vig_probability DOUBLE, source_received_at TIMESTAMPTZ NOT NULL, source_updated_at TIMESTAMPTZ);
CREATE TABLE ledger.publication_event (
    publication_id VARCHAR PRIMARY KEY, idempotency_key VARCHAR NOT NULL UNIQUE, prediction_ids VARCHAR[] NOT NULL,
    payload_sha256 VARCHAR NOT NULL, contract_version VARCHAR NOT NULL, endpoint VARCHAR NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL, http_status INTEGER, response_sha256 VARCHAR, attempt INTEGER NOT NULL,
    outcome VARCHAR NOT NULL);                  -- ACCEPTED | REJECTED | RETRYING | ABANDONED
CREATE TABLE ledger.official_result_version (
    canonical_game_id VARCHAR NOT NULL, result_version INTEGER NOT NULL, source_system VARCHAR NOT NULL,
    raw_reference VARCHAR NOT NULL, received_at TIMESTAMPTZ NOT NULL, is_final BOOLEAN NOT NULL,
    box_sha256 VARCHAR NOT NULL, PRIMARY KEY (canonical_game_id, result_version));
CREATE TABLE ledger.grade_event (
    grade_id VARCHAR PRIMARY KEY, prediction_id VARCHAR NOT NULL, canonical_game_id VARCHAR NOT NULL,
    result_version INTEGER NOT NULL, is_settlement BOOLEAN NOT NULL, post_settlement BOOLEAN NOT NULL,
    outcome_class VARCHAR NOT NULL,            -- WIN | LOSS | PUSH | VOID | UNDEFINED
    realized_value DOUBLE, y SMALLINT, brier DOUBLE, log_loss DOUBLE, crps DOUBLE, graded_at TIMESTAMPTZ NOT NULL);
CREATE TABLE ledger.promotion_decision (
    decision_id VARCHAR PRIMARY KEY, decision_type VARCHAR NOT NULL,   -- PROMOTE | ROLLBACK | ENABLE_MARKET | ENABLE_SOURCE
    from_bundle JSON, to_bundle JSON NOT NULL, evidence_uri VARCHAR NOT NULL, approved_by VARCHAR[] NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL, effective_at TIMESTAMPTZ NOT NULL);
CREATE TABLE ledger.incident (
    incident_id VARCHAR PRIMARY KEY, severity VARCHAR NOT NULL, detected_at TIMESTAMPTZ NOT NULL, category VARCHAR NOT NULL,
    affected_prediction_ids VARCHAR[], affected_publication_ids VARCHAR[], description VARCHAR NOT NULL,
    resolution VARCHAR, resolved_at TIMESTAMPTZ, linked_decision_id VARCHAR);
```

## Integrity tests (to be implemented with the ledger)

1. No table accepts UPDATE / DELETE. The hash chain verifies end to end.
2. Every `prediction` resolves to a `distribution_artifact`, `prediction_run`, `information_snapshot`,
   `model_artifact` (each bundle member) and `calibration_artifact`. No field required by policy §3 is NULL (except
   `draw_count` / `seed` for EXACT_PMF).
3. `prediction.data_cutoff_utc <= game_checkpoint.cutoff_utc`, and the publication `sent_at` is before the actual tip.
4. Every settlement grade references the settlement result version (policy §4).
5. `market_probability` is never joined by any originator feature query (static check, as in `test_market_isolation.py`).
