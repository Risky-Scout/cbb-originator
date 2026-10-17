# Reproducible environment policy

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN (plan)**. This task does not replace or modify the
user's environment. It records the observed one and defines the lock and the clean-room rebuild that Phase A2 must
perform before a dataset can be ruled MODEL_READY.

## 1. Observed environment (2026-10-07, the machine that built v2.4)

| item | value |
|---|---|
| OS | macOS 26.3.1 (build 25D771280a), Darwin 25.3.0, arm64 (Apple silicon), 10 cores, 32 GiB RAM |
| Python (project venv) | CPython **3.12.13** (uv-managed), `.venv/bin/python`. The system `python3` is 3.14.5 and is **not** used |
| uv | 0.12.9 |
| DuckDB | **1.5.6** |
| direct deps (`requirements.txt`) | duckdb 1.5.6, pyarrow 25.0.1, pandas 3.0.6, requests 2.34.2, pytest 9.1.1, pytz 2026.5 |
| transitive (resolved) | certifi 2026.7.22, charset-normalizer 3.5.2, idna 3.20, iniconfig 2.3.0, numpy 2.5.3, packaging 26.3, pluggy 1.6.0, pygments 2.21.0, python-dateutil 2.9.0.post0, six 1.17.0, urllib3 2.8.0 |
| resource policy | DuckDB `threads = 2` (`config.DUCKDB_THREADS`, env `CBB_DUCKDB_THREADS`), `OMP/OPENBLAS/MKL_NUM_THREADS = 2`, event build `memory_limit = 18GB`, temp dir `data/duckdb_tmp`. Never two full pipelines at once |

## 2. The lock (Phase A2 deliverable)

1. `pyproject.toml` with the direct dependencies, and **`uv.lock`** (`uv lock --python 3.12.13`) covering every
   transitive dependency **with sha256 hashes** for macOS-arm64 and linux-x86_64 wheels. `requirements.txt` is kept as
   the human summary. `uv pip compile --generate-hashes` produces `requirements.lock.txt` for pip users.
2. `ENVIRONMENT_FINGERPRINT.json`, written by a small script into every dataset / Stage 3 manifest from then on:
   Python version and implementation, `platform.platform()`, machine, CPU count, DuckDB version,
   `duckdb_settings()` threads / memory, the sha256 of `uv.lock`, the git HEAD and the dirty flag.
3. Model artifacts record `environment_lock_sha256` (ledger schema).

## 3. Clean-room rebuild procedure

1. Fresh clone of the accepted commit into a new directory (no `.venv`, no `data/canonical`, no `modeling/store`).
2. `uv sync --frozen` (fails on any lock drift).
3. Restore inputs only: the raw store `data/raw/**` (verified against `raw_cbbd_payload_manifest_hash`), and the Starter
   Pack (verified against `starter_pack_manifest_hash`). **Nothing derived is copied.**
4. `CBB_DUCKDB_THREADS=2 .venv/bin/python -m cbb_platform.build_canonical`, then the dataset manifest, then the Stage 3
   build.
5. Compare: every canonical table fingerprint, `canonical_content_digest`, the dataset version string, every Stage 3
   feature / label fingerprint and the fold-manifest hashes must equal the accepted baseline **exactly**.
6. Run the full test suite once.
7. Record the comparison in `data/qa/clean_room_rebuild_<date>.json`.

## 4. Independent rebuild of the future accepted baseline

The accepted MODEL_READY baseline (successor of v2.4) is rebuilt by §3 on a **second machine or a clean container**
(linux-x86_64 from the lock) by someone or something other than the original build session. An exact fingerprint match
on both platforms is required. A platform-specific difference (e.g. floating-point summation in a DOUBLE fingerprint) is
investigated and either fixed (deterministic ordering, integer fingerprints) or documented as platform-scoped. It is
never ignored.

## 5. Determinism already in place

Order-independent table fingerprints (`count:bit_xor(hash):sum(hash)` excluding `processed_at`); deterministic ids
(sha256 of source keys); `ORDER BY ALL` on derived tables; a single build path; no wall-clock inputs in content.
