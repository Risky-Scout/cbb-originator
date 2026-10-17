# 2025-26 lockbox data-opening plan (PLAN ONLY — NOT EXECUTED)

**Version:** 3.0 · **Date:** 2026-10-13 · **Status:** COMPLETE TEXT, **NOT YET FROZEN.** Three items block freezing
(§9). Nothing in this plan was run. No 2025-26 data was fetched, queried, read, listed or inspected while preparing it.
Supersedes v2.0 (its content is preserved in the external archive manifest). Architect rulings:
`docs/lockbox/ARCHITECT_RULINGS_2026-10-13_LOCKBOX_READINESS.md`.

## 1. Identities this plan binds

| item | value |
|---|---|
| authoritative candidate | tag `cbb-props-v1-prelockbox-2024-25-r2` → commit `425211071413735be8e1497105ae51830c971c12` (evidence-closure successor of tag `cbb-props-v1-prelockbox-2024-25` → `6eab9ddc667942b271bdbc50cccc3c6a9d02843c`, preserved; the two differ only by the tracked STL evidence files) |
| candidate artifact | `modeling/player_props_v1/final_prelockbox_2024_25_artifact.json`, sha256 `96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f`; spec fingerprint `3e343c1a…a9f`; artifact fingerprint `19117592…997` |
| scorecard | `docs/lockbox/lockbox_scorecard_v2.json` (content 2.1.0), sha256 `27bdfbdd35b182b6eb88710d34310e9f5f71a262b850a88071b8ac085ce26ecd` |
| eligibility manifests | `ELIG_PARTICIPATION` `d5229c31b4e5712898a7876b2c772da94df3a7b9267f019760fbbfde8322da52`; `ELIG_MINUTES` `7631762badf71bac0b12dfbff1bca3e57ccb58c5dd2ae13e50e7a6e237d122a5`; `ELIG_BOX_COUNTS` `3fda535cffdcfc6c55d331381b80abe88cc12e4e7b1aa3e7e828bbb08d49ff9b`; index `1ea13628…8db3` (`modeling/eligibility/`) |
| development dataset | `cbb_data_v2.4+5f19d31e524a` (manifest sha256 `e0d1e20f…6b864847`), status **RESEARCH_CANDIDATE — not MODEL_READY** (§9) |
| environment | `uv.lock` sha256 `cab2bcfb…e36b`; environment fingerprint `6e7270e899fb8d8a90f6ba075d7783841fd22a52dcdd2284a1041ccf30122def` |
| guard change | `docs/lockbox/lockbox_open_2025_26_guard_v3.patch`, sha256 `366ba2d1d1e2aa88491cf0370e05695f020f436a5c83e818bb237a54641cba4e` (= reviewed v2 `c1ffec87…044c` + `stats/team/season`) |
| opening-time loader change | `docs/lockbox/lockbox_open_2025_26_scoring_enable.patch`, sha256 `8f964e01062937fde8ff44a7c8af595d3659884ff223771bced2e6d32aff2be7` (applied with v3; inert unless the opening is authorized) |
| ingestion route | `cbb_platform/cbbd_box_route.py` (CBBD only, ruling G); reconciliation criteria `docs/lockbox/CBBD_BOX_ROUTE_RECONCILIATION_SPEC.md` (sha256 `0727835e…9955`, frozen before evaluation) |
| fit-free runner | `cbb_platform/lockbox_runner.py`; development reproduction `data/qa/lockbox_runner_validation.json` (eval-B metrics reproduced exactly, 0 fit calls) |

## 2. Scope (protocol §1.3)

* **Season:** 2025-26: CBBD `season=2026`; US/Eastern dates 2025-08-01..2026-07-31.
* **Provider:** CBBD only, through `cbb_platform/source_fetch.py::get` (the single guarded path).
* **Endpoints (= `OPEN_PLAN_ENDPOINTS` of guard v3):** box route `games`, `games/teams`, `games/players`,
  `stats/player/season`, `stats/team/season`; context `substitutions/team`, `teams/roster`, `recruiting/players`,
  `recruiting/portal` (class / portal year 2025 → target season 2026). Reference `teams`, `conferences` are
  guard-exempt.
* **Excluded:** odds / lines / any market endpoint, BDL, Torvik, Sharp Stats, play-by-play, lineups, shots, season
  shooting stats. v1 reads none of them.
* **Raw destination:** the append-only main raw store `data/raw/cbbd/<endpoint>/`, logged in
  `data/manifests/cbbd_request_log.jsonl` (purposes `cbbd_box_route` and `cbbd_box_route_context`).
* **Canonical output:** a successor dataset in `data/lockbox_2025_26/canonical/` (never `data/canonical/`). Its identity
  is `cbbd_box_route.successor_version` (§4). `cbb_data_v2.4+5f19d31e524a` and its manifest are never rewritten.
* **Stage 3 output:** `data/lockbox_2025_26/stage3/` (never `modeling/store`, `modeling/features`, `modeling/labels`,
  `modeling/folds` or `STAGE3_MANIFEST.json`).

## 3. Commands (in order, once authorized; none were run)

```
# 0. one session; branch from the authoritative candidate (protocol §2.1)
git switch -c lockbox/open-2025-26 425211071413735be8e1497105ae51830c971c12
git tag -a pre-lockbox-<date> -m "pre-lockbox <date>"
# 1. reviewed opening change + decision record + LOCKBOX_POLICY.md new version (protocol §2.2), one commit:
git apply docs/lockbox/lockbox_open_2025_26_guard_v3.patch
git apply docs/lockbox/lockbox_open_2025_26_scoring_enable.patch
#    write data/manifests/LOCKBOX_OPEN_DECISION_2025_26.json (decision_type LOCKBOX_OPEN, lockbox_open true, approvers,
#    citing the identities of §1), set lockbox.OPEN_DECISION_SHA256 to its sha256, commit
.venv/bin/python -m pytest -q tests/test_guard_v2_review.py   # synthetic guard checks on the opening branch
export LOCKBOX_OPEN_2025_26=1 CBB_DUCKDB_THREADS=2 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
# 2. fetch (box route, then context families) into the main raw store
.venv/bin/python -m cbb_platform.cbbd_box_route fetch 2026 --raw-root data/raw --log data/manifests/cbbd_request_log.jsonl
.venv/bin/python -m cbb_platform.cbbd_box_route fetch-context 2026 --raw-root data/raw --log data/manifests/cbbd_request_log.jsonl
# 3. successor canonical build, twice (deterministic dataset version, §4)
.venv/bin/python -m cbb_platform.cbbd_box_route build 2026 --out data/lockbox_2025_26/canonical --raw-root data/raw --log data/manifests/cbbd_request_log.jsonl
.venv/bin/python -m cbb_platform.cbbd_box_route build 2026 --out data/lockbox_2025_26/canonical_rebuild --raw-root data/raw --log data/manifests/cbbd_request_log.jsonl
# 4. successor Stage 3 (the frozen pipeline; separate root; no reports overwrite the development records)
.venv/bin/python -c "from pathlib import Path; from cbb_platform.stage3 import build; build.build(root=Path('data/lockbox_2025_26/stage3'), canonical=Path('data/lockbox_2025_26/canonical/cbb.duckdb'), with_reports=False)"
# 5. score the frozen artifact on the frozen scorecard (fit-free)
.venv/bin/python -m cbb_platform.lockbox_runner --artifact modeling/player_props_v1/final_prelockbox_2024_25_artifact.json \
    --expected-artifact-sha256 96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f \
    --features-db data/lockbox_2025_26/stage3/features.duckdb --labels-db data/lockbox_2025_26/stage3/labels.duckdb \
    --season 2026 --out docs/LOCKBOX_RESULT_2025_26.json
# 6. docs/LOCKBOX_RESULT_2025_26.md from the JSON (protocol §2.5): metrics, G1-G6 pass/fail, commit SHA
```

Under the CURRENT (closed) guard, steps 2, 3 and 5 each fail with `LockboxViolation` before any credential read,
network call or file read (`tests/test_cbbd_box_route.py`, `tests/test_lockbox_runner.py`).

## 4. Deterministic dataset version

`cbbd_box_route build` writes `route_build.json` with `identity = successor_version(...)`:
`cbb_data_v3.0+` + the first 12 hex of sha256(`route payload-set sha256` | `canonical content digest`). The canonical
content digest is the dataset-manifest formula, sha256 over every table's order-independent fingerprint. The two builds
of step 3 must give identical identities and table fingerprints. A difference stops the session, and the incident is
recorded (protocol §3). The successor record must carry `lockbox_touched = true` and the decision id.

## 5. Point-in-time protections (unchanged code paths)

* Features are joined as-of `available_at <= prediction_cutoff_utc` (scheduled tip − 1 h, the T-1h checkpoint).
* Labels are never features. Season-to-date history resets each season.
* No 2025-26 row is ever used for fitting. The artifact is loaded, not refit (runner: 0 fit calls). The scoring-enable
  patch admits 2025-26 rows only into the loaders and the Stage 3 window. Folds, subsystem windows and every training
  range are unchanged, and any season after 2025-26 is still refused.

## 6. Prohibitions

* **No refitting, recalibration, re-selection or tuning** of anything after opening (protocol §2.4, §3). The runner
  has no fit path.
* **No post-open change** to the scorecard, the gates, the thresholds or their interpretation. The verdict is the
  evaluator's mechanical output (scorecard `pass_fail_semantics`).
* No second candidate, no partial opening, no "sanity checks" on outcomes before step 5 (protocol §4).

## 7. Tests (run on the opening branch before step 2, and again after step 5)

```
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
.venv/bin/python -m pytest -q $(git ls-files 'tests/test_*.py')
```

## 8. Rollback / close

Protocol §3 applies: partial raw data stays in the raw store, flagged; a second opening is not possible; the incident
is recorded and the architect rules. Code: `git switch release/cbb-v1-prelockbox`. Every commit except the opening
commit has `OPEN_DECISION_SHA256 = None` and no applied patch, so the guard is closed there. Reverting the two patches
restores the closed guard byte-identically (`tests/test_guard_v2_review.py`). The development dataset and its Stage 3
stores are never written, so nothing needs restoring.

## 9. Why this plan is not frozen yet

1. **CBBD route:** full-season development validation (2023-24, 2024-25) under the frozen reconciliation spec has not
   run, because the CBBD credential is not available in the preparing session. Smoke-scale reconciliation passed with
   0 unexplained differences (`data/qa/cbbd_box_route_smoke_reconciliation.json`).
2. **Human design ruling:** the v3 guard diff and the scoring-enable diff differ from the reviewed v2 design (ruling H).
3. **MODEL_READY:** the development dataset is RESEARCH_CANDIDATE (Phase A2 items outstanding; ruling A).

Once all three are closed and this file is committed, its sha256 belongs in the readiness record and the LOCKBOX_OPEN
decision.
