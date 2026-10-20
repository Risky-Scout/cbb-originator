"""CBB V2 JM1_FINAL production layer, SHADOW mode only (no deployment, no publication, no wagering).

Model development is over: this package loads the frozen JM1_FINAL artifacts and calls the frozen code unchanged
(cbb_platform/v2/*, cbb_platform/stage3/*, cbb_platform/props_v0*.py, cbb_platform/props_v1_pricing.py). It adds only
the operational layer: season admission, incremental current-season state, point-in-time slate / universe / feature
assembly, one joint draw bank per game, pricing, quotes, strict matching, a fail-closed readiness gate, an append-only
shadow ledger, a static bundle and a local deployment dry run. See docs/SHADOW_PRODUCTION_INTEGRATION.md.
"""
