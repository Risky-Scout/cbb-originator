# CBB 2026-27 prospective shadow: operational evidence (PRIVATE)

This orphan branch holds **operational evidence only**. It is written by `python -m cbb_platform.prod.shadow_tick`
(source: `release/cbb-v2-prospective-shadow`). It is **never merged** into any source branch or `main`.

* Model: frozen JM1_FINAL `e1353e5` (tag `cbb-v2-jm1-final-2026-27`). The artifacts are hash-verified on every tick.
* Readiness ceiling: SHADOW_ONLY. Wagering is disabled and there is no public deployment.
* Layout and rules: `cbb_platform/prod/ops_store.py` and `docs/PROSPECTIVE_SHADOW_OPERATION.md` on the source branch.
* No credentials, API keys, model stores, simulation cubes or training datasets are stored here. The binary working
  set (state + minimal provider cache) is AES-256 encrypted on the private release `cbb-2026-27-shadow-ops`.
* The files are append-only. `CHAIN.jsonl` hash-chains every sealed tick folder, so any edit is detectable
  (`ops_store.verify_chain`).
