# WizardOfOdds CBB Originator integration spec v1.0

**Date:** 2026-10-07 · **Status:** AUTHORITATIVE CONTRACT, **FROZEN** (not implemented; no endpoint is called by this repository).
This is a **CBB-specific** contract written from scratch. **The NBA WizardOfOdds integration contract is not reused.** No
field, endpoint, auth scheme or semantic below is inherited from it by reference. Where the two happen to agree, that is
coincidence and must be re-verified against this document. Operations semantics:
`PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`. Storage: `docs/PREDICTION_LEDGER_SCHEMA.md`.

**Open dependency:** the WizardOfOdds side (base URL, credential issuance, rate limits) must be confirmed by the site
owner. Until then, every value marked *(confirm)* is a proposal, and Phase F cannot start.

## 1. Transport and format

* HTTPS POST, JSON (UTF-8), `Content-Type: application/json`, `contract_version: "cbb-originator-1.0"` in every body.
* Endpoints *(confirm)*: `POST /api/cbb/originator/v1/boards` (one game × one checkpoint board),
  `POST /api/cbb/originator/v1/corrections` (supersede a board), `GET /api/cbb/originator/v1/boards/{publication_id}`
  (read-back for reconciliation), `GET /api/cbb/originator/v1/health`.
* Fallback (if no API is available): signed JSON files to an agreed object-store prefix with the same body and the
  same idempotency semantics. Never CSV.

### 1.1 Board payload

```json
{
  "contract_version": "cbb-originator-1.0",
  "publication_id": "PUB-<sha256 prefix>",
  "idempotency_key": "<sha256(canonical_game_id|checkpoint_code|bundle_id|payload_sha256)>",
  "supersedes_publication_id": null,
  "game": {"canonical_game_id": "G-…", "home_team": {"id": "T-…", "name": "…"}, "away_team": {"id": "T-…", "name": "…"},
           "scheduled_tip_utc": "2026-11-10T00:00:00Z", "neutral_site": false},
  "checkpoint": {"code": "T1H", "cutoff_utc": "…", "data_cutoff_utc": "…", "fidelity": "PROSPECTIVE_EXACT"},
  "provenance": {"dataset_version": "…", "feature_version": "…", "model_bundle_id": "…", "calibration_version": "…",
                 "distribution_id": "…", "probability_method": "MC_BANK", "draw_count": 200000, "seed": 1234, "mcse_max": 0.0011},
  "markets": [
    {"prediction_id": "…", "player_id": "P-…", "player_name": "…", "team_id": "T-…", "stat": "PTS", "line": 14.5,
     "p_over": 0.5312, "p_under": 0.4688, "p_push": 0.0, "fair_decimal_over": 1.883, "fair_decimal_under": 2.133,
     "void_on_dnp": true, "status_note": null}
  ],
  "generated_at_utc": "…"
}
```

Stats allowed (Blueprint v1.3 board): `MIN, PTS, TS%, REB, AST, STL, BLK, TOV, PF, ORB, DRB, FT, FT%, 2P, 2P%, 3P, 3P%`.
A market family is publishable only after its first-enable human approval (policy §10).

## 2. Authentication *(confirm)*

A per-environment API key (staging and production keys are distinct), sent as `Authorization: Bearer <key>`, plus an
HMAC-SHA256 body signature `X-CBB-Signature: t=<unix>,v1=<hex>` over `t + "." + body`, with a 5-minute replay window.
Keys live in the secret store, never in the repository, logs or the ledger. Rotation every 90 days and on any incident.

## 3. Idempotency

`idempotency_key` is deterministic (§1.1). The receiver must treat a repeated key as the same publication (HTTP 200
with the original receipt) and never create a duplicate board. The sender never reuses a key for a different payload. A
different payload has a different `payload_sha256` and therefore a different key.

## 4. Retries

Retry only on transport errors, HTTP 429 and 5xx: exponential backoff 2 s, 4 s, 8 s, 16 s, 32 s with ±20 % jitter, at
most 5 attempts. Every attempt is a `publication_event` row. No retry on 4xx other than 429 (logged as REJECTED;
incident if unexpected). **Never publish after the actual tip.** A retry that would land after tip is abandoned
(`ABANDONED`).

## 5. Rounding

Probabilities are computed and stored at full precision in the ledger. The wire format carries probabilities rounded
half-even to **4 decimals**, and `p_over + p_under + p_push = 1` exactly after rounding: the residual is assigned to
the largest component. Fair decimal odds = `1 / p` rounded to 3 decimals (omitted when p < 0.001). Lines are sent as
given (half-points or integers). Grading uses the ledger's full-precision value.

## 6. Correction / version semantics

* A board is immutable once accepted. A correction is a **new** publication with `supersedes_publication_id` set and a
  `correction_reason`, sent to `/corrections`. The receiver shows the latest non-superseded board and keeps history.
* A later checkpoint's board is **not** a correction. It is a new publication for a new checkpoint.
* Withdrawal (a no-publish condition discovered after publication) is a correction with `markets: []` and reason
  `WITHDRAWN`, and it is logged as an incident.

## 7. Audit trail

Every request / response pair is stored as `publication_event` rows: the payload sha256, the response sha256, status and
attempt. A daily reconciliation job reads back every accepted publication (`GET …/{publication_id}`) and compares hashes.
Any mismatch is an incident.

## 8. Staging contract tests (must pass before Phase F)

1. schema validation of a golden payload and of 20 generated payloads (JSON Schema checked into the repository);
2. idempotent replay (same key twice → one board);
3. a correction supersedes and preserves history;
4. retry behaviour (simulated 503 then 200; 429 with Retry-After; 400 not retried);
5. the no-publish-after-tip guard;
6. the rounding invariant (sums to 1, half-even);
7. signature rejection (bad signature, stale timestamp);
8. read-back reconciliation round trip;
9. load: one full slate (≥ 150 games × 5 checkpoints) within the update SLA.

## 9. Commercial licensing gate

No production publication may include, or be derived from, a source whose commercial and publication rights are
unverified (`docs/COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY.md`). The provenance block lists the production
sources of the bundle. The sender refuses to publish when any listed source lacks an approved rights record.
