# CBB 2026-27 shadow: off-Mac automation activation

**Date:** 2026-10-23. **Verdict: automated prospective shadow is ACTIVE on GitHub-hosted Actions. The Mac is not
required.** This record supersedes the "NOT enabled" verdict of `SHADOW_AUTOMATION_READINESS.md`; the analysis in that
document (cadence, budgets, forecasts) still applies unchanged.

| item | value |
|---|---|
| Frozen model | JM1_FINAL `e1353e5` (tag `cbb-v2-jm1-final-2026-27`, unmoved); four artifact SHA256 verified before any remote step |
| Readiness ceiling | SHADOW_ONLY. Public launch HOLD. Wagering disabled. No model refit, no historical research |
| Code executed by the runner | `release/cbb-v2-prospective-shadow` (checked out by the workflow) |
| Workflow on main | `.github/workflows/cbb-shadow-tick.yml` only (main: `e1353e5` → `de08de5` → `b9b4d75` → `d5824da`, fast-forward, no force) |
| Evidence | private orphan branch `ops/cbb-2026-27-shadow-state` |
| Encrypted assets | private pre-release `cbb-2026-27-shadow-ops` |

## 1. Licence gate: PASS

`docs/OFF_MACHINE_DATA_LICENSE.json` status `PERMITTED`, basis `USER_ARCHITECT_ATTESTATION = PERMITTED`.

* The user/architect, as the original Starter Pack purchaser, confirmed directly that they have permission to use it
  commercially for the CBB Originator.
* They authorized encrypted private off-machine storage and GitHub-hosted processing.
* The bundled Starter Pack `LICENSE` text itself does **not** grant this; the gate relies on the attestation. No
  licensor email or external licence document is on file.
* The prior `UNCLEAR` record is kept under `superseded`.

## 2. Extract minimality: PASS

The validated extract was reused unchanged, so the 8.8 GB comparison was not rerun.

* `history_extract.duckdb`: 126,627,840 bytes, sha256 `0338b32d5228697862e599e7d545cd81718a5750fadfb51f010dbef4bbf0ca71`.
* 15 relations: 11 populated, 4 schema-only.
* The extract code and manifest are unchanged since the equivalence run
  (`docs/PROSPECTIVE_SHADOW_HISTORY_EXTRACT_EQUIVALENCE.json`, PASS).

## 3. Secrets

Repository secrets `CBBD_API_KEY`, `ODDS_API_KEY` and `CBB_OPS_ENCRYPTION_KEY` are set on `Risky-Scout/cbb-originator`.

* The API keys were piped from the local environment straight into `gh secret set`; no value was printed or written to
  a file.
* `CBB_OPS_ENCRYPTION_KEY` was generated with `openssl rand -base64 48` straight into the macOS login keychain
  (service `CBB_OPS_ENCRYPTION_KEY`, account `cbb-originator`) and from there into the secret.
* It is never in git. **Losing both copies makes every encrypted asset unrecoverable.**

## 4. Encrypted bootstrap (2026-10-23T06:53Z)

AES-256-CBC (openssl, pbkdf2 200000).

| asset | plaintext sha256 / bytes | ciphertext sha256 / bytes |
|---|---|---|
| `history-extract-0338b32d52286978.duckdb.enc` | `0338b32d…0ca71` / 126,627,840 | `d1b18826f4f3a9f1a64b8c40970994e0645826d0cf152ae5baf7892fc8ee4c23` / 126,627,872 |
| `ws-B5dc31c69.tar.enc` (working set, state `S20261023T010605Z-c4a32283e0b5`) | `f3ead39a…5c9d` / 13,629,440 | n/a |

* The history ciphertext was downloaded back and decrypted to `0338b32d…0ca71` (exact match).
* The record is in ops `markers/OFF_MACHINE_BOOTSTRAP.json` (commit `ac10588`).

## 5. Clean restore: PASS

Fresh `git clone` of the release and ops branches from GitHub into an empty directory: no Mac database, no `data/prod`,
no earlier `/tmp` state.

* Licence gate PERMITTED.
* History extract downloaded, decrypted and sha256-matched (23 s).
* Working set restored (31 files, state `S20261023T010605Z-c4a32283e0b5`).
* Artifact hash gate PASS. State loaded. Due-work discovery ran: nothing due.

## 6. Off-Mac runs (GitHub-hosted ubuntu-24.04; the Mac did not participate)

| run | trigger | result |
|---|---|---|
| 39914886298 | dispatch, `force` | restore OK, hash gate PASS, nothing due. One-time `DUE.json` refresh (the pre-existing file predated `SCORE_LEAD_MINUTES = 50`) → ops `75f21c6` |
| 39915043814 | dispatch, `force`, immediate repeat | **idempotent**: `did_work=false`, `NO_CHANGE`, no commit, no upload, no API call |
| 39915309839 | dispatch (gate path) before next due | gate `due=false`, job ended in 5 s, nothing installed |
| 39916431329 | dispatch (gate path) after sync due | **work run**: CBBD routine sync, 7 live requests (cap 20), `UNCHANGED` (no new state version). Odds events listing: 0 NCAAB events. New encrypted working set `ws-K20261023T072429Z-af9a75`. Ops `fe50689` |
| 39917725158 | dispatch, `force`, immediate repeat | **idempotent**: restored the runner-written working set (33 files); no commit, no upload, no API call; 24 s job |

Second-run checks: no duplicate prediction, quote, settlement or state version; no repeated completed-game request; no
API acquisition.

**Operational fix found by run 39916431329.** The `setup-python` pip-cache restore failed with *"Egress is over the
account limit"* and then hung 10 minutes. The pip cache was removed from the workflow (`b9b4d75`); the locked install
from PyPI takes about 11 s. This is account-level Actions resource pressure; it is recorded here, not worked around by
reducing cadence.

## 7. Scheduler: ENABLED (`d5824da` on main)

```
12,42 12-23,0-4 * 11,12,1,2,3 *   # in season: every cutoff hour (34 runs/day)
12,42 12-23,0-4 1-8 4 *           # Final Four / title game
34 6,9 * 11,12,1,2,3 *            # in season, no cutoffs (2 runs/day)
34 */6 * 4-10 *                   # off season / preseason (4 runs/day)
```

* In season this is 36 runs/day with `SCORE_LEAD_MINUTES = 50`; the scientific cutoff stays TIP_MINUS_1H.
* Every record keeps the actual `target_cutoff`, `executed_at`, `prediction_as_of` and timing deviation.
* GitHub API check: workflow `CBB shadow tick` is `active`, the four cron triggers are present, and
  `workflow_dispatch` is kept.
* Until 2026-10-31 the off-season entry runs at 00:34 / 06:34 / 12:34 / 18:34 UTC. The first in-season scoring window
  opens 2026-11-01T12:40Z.

**Unchanged budgets:**
* CBBD: 20 network requests per sync stage, completed games ordered by next-game urgency.
* Odds API: 60 credits per run, with `BUDGET_EXHAUSTED` recorded per event.
* The first paid quote validation is one event only (about 12 credits) and is never bulk-retried after a FAIL.

## 8. Emergency manual recovery (only if the hosted automation is unavailable)

The scheduled workflow is the single writer, and the workflow `concurrency` group serialises GitHub runs. A manual tick
from a Mac can race it, so first **disable** the workflow:

```
gh workflow disable "CBB shadow tick" -R Risky-Scout/cbb-originator
```

**Preferred: a manual hosted run.**

```
gh workflow run cbb-shadow-tick.yml -R Risky-Scout/cbb-originator --ref main -f force=true
```

Use `-f allow_revalidation=true` only as an explicit architect action after a recorded quote-validation FAIL.

**Mac fallback, while the workflow is disabled:**

```
cd data/prod/ops && git pull --ff-only origin ops/cbb-2026-27-shadow-state && cd -
export CBB_OPS_ENCRYPTION_KEY="$(security find-generic-password -a cbb-originator -s CBB_OPS_ENCRYPTION_KEY -w)"
# CBBD_API_KEY / ODDS_API_KEY from the shell environment
.venv/bin/python -m cbb_platform.prod.shadow_tick --restore --persist --commit --push
gh workflow enable "CBB shadow tick" -R Risky-Scout/cbb-originator
```

**Recovery facts:**
* A corrupt current working set falls back to the previous archives automatically.
* A wrong key fails closed.
* A frozen-artifact hash mismatch blocks the tick (exit 2) and must never be "fixed" by regenerating artifacts.

## 9. Tests

`tests/test_prod_shadow_tick.py` and `tests/test_prod_shadow.py`: 66 passed. They cover the licence gate (blocking and
the shipped attestation), encryption-key redaction, the bootstrap key requirement, extract restore and last-good,
working-set fallback, the idempotent lifecycle, scheduler parsing (exact crons, 36 in-season runs/day, ubuntu-24.04,
release-branch checkout, no pip cache, no deploy), the cadence/lead agreement, budget exhaustion, and no public
deployment or wagering. No model-development backtest was run.

## 10. Waiting on real data (states as of activation)

* QUOTE_INTEGRATION: WAITING (no NCAAB event listed yet).
* T-60 predictions: WAITING_FOR_GAME (first 2026-11-01).
* FIRST_LIVE_COMPLETED_GAME_INGESTION: WAITING_FOR_FINAL_GAME.
* Settlement: WAITING_FOR_OUTCOME.
* Monitors and daily/cumulative reports run on every working tick, and the market benchmark waits for quotes. Warnings
  stay diagnostic only.
