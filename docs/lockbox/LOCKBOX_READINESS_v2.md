# Lockbox readiness: CBB v1 pre-lockbox candidate (record content 3.1.0)

**NON_APPROVAL_LOCKBOX_GATES = PASS · MODEL_READY (candidate-specific) = PASS · LOCKBOX_OPEN_APPROVAL = WITHHELD ·
LOCKBOX_READY = FAIL_APPROVAL_ONLY**

**Status:** readiness record only. **The lockbox is closed and untouched.** No 2025-26 data was fetched, queried, read,
listed or inspected; no 2026-27 preseason data was used; no guard or loader change is applied. Generated
deterministically by `python -m cbb_platform.lockbox_readiness` → `data/manifests/LOCKBOX_READINESS_v2.json` (every hash
is recorded there; this page is a summary). Supersedes content 3.0.0 of this page (route FAIL, guard ruling pending).
Rulings: `ARCHITECT_RULINGS_2026-10-13_LOCKBOX_READINESS.md` (A-J) and `ARCHITECT_RULINGS_2026-10-16_ROUTE_CLOSURE.md` (K-S).

## 1. Candidate (frozen, unchanged)

* Tag `cbb-props-v1-prelockbox-2024-25-r2` → `425211071413735be8e1497105ae51830c971c12` (not moved). The original tag
  `cbb-props-v1-prelockbox-2024-25` → `6eab9dd` is preserved.
* Artifact `modeling/player_props_v1/final_prelockbox_2024_25_artifact.json`, sha256
  `96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f`. No refit, recalibration, feature, coefficient or
  STL phi (0.16) change. The readiness commit is operational/evidence only.

## 2. Gates

| gate | status | evidence |
|---|---|---|
| Candidate identity, STL evidence self-contained | PASS | artifact identical at both tags and in the working tree |
| Eligibility manifests | PASS | `modeling/eligibility/` (`ELIG_PARTICIPATION`, `ELIG_MINUTES`, `ELIG_BOX_COUNTS`, `INDEX`); deterministic regeneration and frozen-loader equality tested |
| **CBBD ingestion route** | **PASS** (accepted and closed, rulings K-O) | `data/qa/cbbd_box_route_reconciliation.json`: E1-E7 PASS; 0 boundary losses, 0 false D-I, 0 non-final games in modeling state, 0 unresolved team assignments / identities / canonical duplicates; 20 EXPLAINED_PROVIDER_DATA_REVISION team boxes, 456 EXPLAINED_PROVIDER_LABEL_REVISION rows; 0 unexplained canonical differences, 0 untraced E6 rows; deterministic A/B; FIT_CALLS 0 |
| E8 (measured / classified) | **FAIL as measured** / EXPLAINED_PROVIDER_DATA_REVISION | max gated difference 0.0020155 > frozen 1e-3 (G5 calibration-in-the-large); the ablation without the 20 accepted team boxes reproduces the reference (0.0); gate verdicts identical. Tolerance not loosened (ruling N) |
| Anti-leakage contract | PASS | `data/qa/cbbd_box_route_pit_audit.json`: PIT_VIOLATIONS 0 in every category (target, same-game, future data, future roster, label, lookahead) over 2,970,075 pool rows |
| Starter Pack content identity | PASS | 111 registered data files; digest `cc6ced32…ab83` equal; inventory equal (`.DS_Store` excluded by contract) |
| Relocation-safe resolution | PASS | rulings P and S, `cbb_platform/relocation.py`; frozen manifests unmodified; frozen development DB attached READ_ONLY, staging views rebound in memory |
| Market benchmark / energy-variogram / five PIT checkpoints | N/A | rulings C, D, E (v1 is scored at the single pregame T-1h cutoff) |
| Scorecard | PASS (frozen, committed) | `docs/lockbox/lockbox_scorecard_v2.json`, G1-G6 verbatim, thresholds unchanged |
| Fit-free lockbox runner | PASS | `data/qa/lockbox_runner_validation.json`: evaluation-B metrics reproduced exactly; FIT_CALLS 0; byte-identical reruns |
| Guard engineering review / human design ruling | PASS | v3 = v2 + `stats/team/season` (sha256 `366ba2d1…cba4e`); scoring-enable (sha256 `8f964e01…be7`) applies cleanly after v3 and enables scoring only under an authorized opening. **Design approval only** (ruling Q) |
| Guard applied | FALSE | live guard closed; no decision record |
| Environment lock | PASS | Python 3.12.13, DuckDB 1.5.6, NumPy 2.5.3, pandas 3.0.6; no drift; requirements lock equals `uv.lock` |
| Worktree cleanliness | PASS | clean apart from the ruling-I exception `data/qa/game_identity_questionable_only.csv` (untouched, untracked) |
| Data-opening plan frozen | PASS | committed |
| Tracked test suite | PASS | `data/qa/lockbox_readiness_suite.json`: 560 passed, 0 failed |
| **Candidate MODEL_READY** | **PASS** (ruling R) | every other non-approval gate passes. Means technically ready for the one-time lockbox evaluation; NOT opened, NOT approved for production, NOT deployed |
| Blueprint dataset-level A2 | OPEN (informational) | items 2, 3, 5, 6b, 7 not done; item 1 N/A for v1 (ruling E); `DATASET_MANIFEST.json` unchanged (RESEARCH_CANDIDATE) |
| Lockbox open approval | **WITHHELD** | the next step requires an explicit architect `LOCKBOX_OPEN` |
