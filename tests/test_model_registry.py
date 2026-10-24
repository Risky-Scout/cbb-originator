"""modeling/MODEL_REGISTRY.json mirrors the runtime source of truth (cbb_platform/prod/config.py) and the files on disk.
Read-only: nothing here writes an artifact."""
from __future__ import annotations

import json

from cbb_platform import config as C
from cbb_platform.prod import config as PC, loader as LD

REGISTRY = C.PROJECT_ROOT / "modeling" / "MODEL_REGISTRY.json"


def _reg() -> dict:
    return json.loads(REGISTRY.read_text())


def test_champion_matches_runtime_config_and_disk():
    ch = _reg()["current_champion"]
    assert ch["champion_name"] == ch["architecture"] == PC.ARCHITECTURE
    assert ch["model_version"] == PC.PRODUCTION_VERSION and ch["git_commit"] == PC.FROZEN_COMMIT
    assert ch["git_tag"] == PC.FROZEN_TAG and ch["draws_per_game"] == PC.SIMS
    assert tuple(ch["supported_stats"]) == PC.BASE_STATS
    assert {k: tuple(v) for k, v in ch["supported_combos"].items()} == PC.COMBOS
    assert {k: (v["path"], v["sha256"]) for k, v in ch["artifacts"].items()} == PC.ARTIFACTS
    for v in ch["artifacts"].values():
        assert LD.sha256_file(C.PROJECT_ROOT / v["path"]) == v["sha256"]


def test_rollback_and_research_artifacts_are_distinct_from_production():
    reg = _reg()
    prod_paths = {v[0] for v in PC.ARTIFACTS.values()}
    rb = reg["current_champion"]["rollback_artifact"]
    assert rb["wired_for_scoring"] is False
    assert LD.sha256_file(C.PROJECT_ROOT / rb["dependence"]["path"]) == rb["dependence"]["sha256"]
    for path, v in reg["research_artifacts"].items():
        if path.startswith("_") or "sha256" not in v:
            continue
        assert path not in prod_paths
        assert LD.sha256_file(C.PROJECT_ROOT / path) == v["sha256"]
    for b in reg["superseded_production_baselines"]:
        assert b["artifact"]["path"] not in prod_paths
        assert LD.sha256_file(C.PROJECT_ROOT / b["artifact"]["path"]) == b["artifact"]["sha256"]
