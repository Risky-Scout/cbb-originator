"""The ONE production model loader. Verifies every frozen artifact before anything is scored; never fits, refits or
regenerates. Any mismatch raises ArtifactIntegrityError (readiness reason ARTIFACT_HASH_MISMATCH / WRONG_PRODUCTION_SEASON).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from cbb_platform import config as C
from cbb_platform.prod import config as PC
from cbb_platform.v2 import jm1_final_production as JP, joint as J, latent as L


# identity fields a walk-forward daily model carries (cbb_platform/walkforward/store.py) and every PREDICTION record copies;
# the frozen opening identity below has none of them, so its records are unchanged
WALK_FORWARD_IDENTITY_FIELDS = ("model_store_version", "model_kind", "training_cutoff", "training_through_date",
                                "data_cutoff", "latest_included_game", "previous_model_version", "checkpoint",
                                "model_source")


class ArtifactIntegrityError(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class FrozenModel:
    identity: dict
    marginal_cfg: dict
    v0_state: dict
    participation_state: dict
    jm1: dict                                   # jm1_final_production.load_production() form
    manifest: dict = field(repr=False, default_factory=dict)


def verify(paths: dict | None = None, expected: dict | None = None) -> dict:
    """Hash + identity checks of the four frozen artifacts. Returns the verified identity record."""
    paths = paths or {k: C.PROJECT_ROOT / v[0] for k, v in PC.ARTIFACTS.items()}
    expected = expected or {k: v[1] for k, v in PC.ARTIFACTS.items()}
    got = {}
    for k in PC.ARTIFACTS:
        p = Path(paths[k])
        if not p.exists():
            raise ArtifactIntegrityError("ARTIFACT_HASH_MISMATCH", f"{k} missing: {p}")
        got[k] = sha256_file(p)
        if got[k] != expected[k]:
            raise ArtifactIntegrityError("ARTIFACT_HASH_MISMATCH", f"{k} sha256 {got[k]} != frozen {expected[k]}")
    man = json.loads(Path(paths["production_manifest"]).read_text())
    art = json.loads(Path(paths["jm1_final"]).read_text())
    dep = json.loads(Path(paths["residual_j2"]).read_text())
    mg = json.loads(Path(paths["marginal"]).read_text())
    checks = {
        "manifest_architecture": man.get("architecture") == PC.ARCHITECTURE,
        "artifact_architecture": art.get("architecture") == PC.ARCHITECTURE,
        "manifest_production_season": man.get("production_season") == PC.PRODUCTION_SEASON,
        "artifact_production_season": art.get("PRODUCTION_SEASON") == PC.PRODUCTION_SEASON,
        "dependence_production_season": dep.get("PRODUCTION_SEASON") == PC.PRODUCTION_SEASON,
        "marginal_production_season": mg.get("PRODUCTION_SEASON") == PC.PRODUCTION_SEASON,
        "manifest_training_through": man.get("supervised_training_through") == PC.TRAINING_THROUGH,
        "artifact_training_through": art.get("SUPERVISED_TRAINING_THROUGH") == PC.TRAINING_THROUGH,
        "dependence_outcomes_through": dep.get("OUTCOME_DATA_THROUGH") == PC.TRAINING_THROUGH,
        "marginal_training_through": mg.get("SUPERVISED_TRAINING_THROUGH") == PC.TRAINING_THROUGH,
        "manifest_marginal_sha": man["artifacts"]["marginal"]["sha256"] == got["marginal"],
        "manifest_jm1_sha": man["artifacts"]["jm1_final_structural"]["sha256"] == got["jm1_final"],
        "manifest_residual_sha": man["artifacts"]["residual_dependence"]["sha256"] == got["residual_j2"],
        "artifact_residual_sha": art.get("RESIDUAL_DEPENDENCE_ARTIFACT_SHA256") == got["residual_j2"],
        "artifact_marginal_sha": art.get("MARGINAL_ARTIFACT_SHA256") == got["marginal"]
                                 and dep.get("MARGINAL_ARTIFACT_SHA256") == got["marginal"],
        "supported_stats": tuple(man.get("supported_stats", ())) == PC.BASE_STATS == tuple(L.STATS),
        "supported_combos": {k: tuple(v) for k, v in man.get("supported_combos", {}).items()} == PC.COMBOS
                            == {k: tuple(v) for k, v in J.COMBOS.items()},
        "draws_per_game": man.get("random_seed_policy", {}).get("draws_per_game") == PC.SIMS,
    }
    bad = sorted(k for k, v in checks.items() if not v)
    if bad:
        reason = "WRONG_PRODUCTION_SEASON" if any("season" in b or "through" in b for b in bad) else "ARTIFACT_HASH_MISMATCH"
        raise ArtifactIntegrityError(reason, f"identity checks failed: {bad}")
    return {"model_version": PC.PRODUCTION_VERSION, "architecture": PC.ARCHITECTURE,
            "jm1_final_version": art["version"], "frozen_commit": PC.FROZEN_COMMIT, "frozen_tag": PC.FROZEN_TAG,
            "production_season": PC.PRODUCTION_SEASON, "training_through": PC.TRAINING_THROUGH,
            "production_manifest": PC.ARTIFACTS["production_manifest"][0],
            "artifact_sha256": got, "checks": checks}


def load(paths: dict | None = None, expected: dict | None = None) -> FrozenModel:
    ident = verify(paths, expected)
    paths = paths or {k: C.PROJECT_ROOT / v[0] for k, v in PC.ARTIFACTS.items()}
    mg = json.loads(Path(paths["marginal"]).read_text())
    jm1 = JP.load_production(Path(paths["jm1_final"]), Path(paths["residual_j2"]))
    man = json.loads(Path(paths["production_manifest"]).read_text())
    return FrozenModel(identity=ident, marginal_cfg=mg["config"], v0_state=mg["v0_state"],
                       participation_state=mg["participation_state"], jm1=jm1, manifest=man)
