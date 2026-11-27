# Stage 03 regression (successor candidate, local, 2026-11-27)

One run: `nice -n 10 pytest -q -p no:cacheprovider tests -rs` with one BLAS/OMP thread. That is the whole `tests/` tree, a superset of
the Stage 02 contract. Wall time 3 259 s (54 min). Log: `local_full_regression.log`.

| Scope | Collected | Passed | Skipped | Failed |
|---|---|---|---|---|
| Stage 02 contract (the 29 legacy research/data-pipeline files excluded, as in the Stage 02 run) | 1 175 (= 1 167 + 8 new) | **1 168** | 7 (flock(1) absent on macOS ×5, guard closed, local store absent) | **0** |
| Excluded legacy files (also run) | 599 | 596 | 2 (lockbox guards) | 1 |
| Total | 1 774 | 1 764 | 9 | 1 |

The single failure is `tests/test_stage2.py::test_no_unrelated_project_was_modified`. It is a filesystem snapshot of the
*parent* folder, and a user document there (`SSH root.docx`, size 13 571 → 13 827, mtime 2026-11-18) changed. It is
code-independent, unrelated to this change, and outside the Stage 02 contract scope. It was left as is: user files are not touched.

Stage 02 protections included and passing: `test_feature_cache_causal.py` (10), `test_feature_closure_equivalence.py`
(2, store-backed, ran), `test_walkforward_daily_warmup.py`, `test_closure_is_pinned_to_the_audited_stage3_sql`, the
morning-equivalence and walk-forward suites. The flock(1) skips run on CI (ubuntu).

Deviation: the run included the legacy DuckDB-backed files (peak RSS ≈ 8.2 GB, DuckDB threads up to ~190% CPU) beyond
the contract scope. There was no memory pressure on the 32 GB host, and no second suite was started.
