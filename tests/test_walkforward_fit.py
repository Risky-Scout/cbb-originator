"""OPENING_BASELINE_REPRODUCTION: with NO 2026-27 completed-game data the daily-refit machinery reproduces the frozen
through-2025-26 production model (directive section 17). Skipped where the frozen local stores are absent (e.g. CI).

    parameters   marginal v0 / participation states, GLM, conditional dispersion, E[D] and the residual J2: BIT-IDENTICAL.
                 Shared environment (env_fit lstsq): within the frozen fit-to-fit tolerance jm1_final_production.DET_RTOL
                 (the opening freeze read its team frame without ORDER BY, so its float summation order is not
                 recoverable; the walk-forward path sorts it and is itself bit-deterministic).
    predictions  the golden production slate (tests/fixtures/golden/prod_equivalence_v1.json) scored with the
                 refit-from-scratch model equals the golden outputs at the golden test's own same-platform tolerance
                 (bank sha256 equal, floats atol 1e-12).
The baseline hashes are never updated by this test.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys

import pytest

from cbb_platform import config as C, props_v0 as P
from cbb_platform.prod import config as PC, loader as LD, run as RN
from cbb_platform.v2 import jm1_final_production as JP, phase1b as B, provenance as PV
from cbb_platform.walkforward import config as WC, daily as DL, fit as WF, store as SO
from tests import test_prod_golden_equivalence as G

NEEDED = (PV.LB_FEATURES, PV.LB_LABELS, B.OOS_PATH)
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="frozen local stores not present")
REPRO = "cbb-jm1-2026-27-19000101"


@pytest.fixture(scope="module")
def refit():
    cfg = P.load_config()
    f = WF.fit(cfg, None, lambda s: None)
    x = DL.serialize(f, cfg, {"model_version": REPRO, "training_cutoff": "NO_2026_27_DATA", "training_state": None})
    frozen = {"marg": json.loads(B.ARTIFACT_PATH.read_text()), "art": json.loads(JP.ARTIFACT_PATH.read_text()),
              "dep": json.loads(JP.RESID_DEP_PATH.read_text())}
    return {"cfg": cfg, "f": f, "x": x, "frozen": frozen}


def test_opening_chronology_and_recency(refit):
    f = refit["f"]
    assert f["T"] == WC.OPENING_THROUGH == 2026
    assert f["params"]["resid_seasons"] == [2024, 2025, 2026]
    assert f["params"]["struct"]["glm"]["seasons"] == [2023, 2024, 2025, 2026]
    w = f["weight_by_season"]
    assert w["2026"] == 1.0 and w["2025"] == 0.5 and w["2024"] == 0.25 and w["2014"] == 0.5 ** 12


def test_marginal_parameters_bit_identical(refit):
    mine, fz = WF.json_roundtrip(refit["x"]["marg"]), refit["frozen"]["marg"]
    assert mine["v0_state"] == fz["v0_state"]
    assert mine["participation_state"] == fz["participation_state"]
    assert mine["config"] == fz["config"] and mine["coef_order"] == fz["coef_order"]


def test_structural_parameters_reproduce(refit):
    mine = WF.json_roundtrip(WF.predictive_view(refit["x"]["marg"], refit["x"]["art"], refit["x"]["dep"]))
    ref = WF.predictive_view(refit["frozen"]["marg"], refit["frozen"]["art"], refit["frozen"]["dep"])
    for k in ("glm", "e_dur", "conditional_dispersion", "residual_dependence"):
        assert mine[k] == ref[k], k
    nd = JP.numeric_diff(mine["env"], ref["env"])
    assert nd["NUMERICALLY_IDENTICAL"] and nd["max_rel_diff"] <= JP.DET_RTOL, nd


def test_prediction_level_reproduction_on_the_golden_slate(refit, tmp_path, monkeypatch):
    x = refit["x"]
    cand = tmp_path / "v"
    cand.mkdir()
    sha = {k: SO.write_bytes(cand / f, x[b]) for (k, f), b in zip(SO.FILES.items(), ("mbytes", "abytes", "dbytes"))}
    frozen = json.loads((PC.MODEL_DIR / "jm1_final_production_manifest_2026_27.json").read_text())
    SO.write_manifest(cand, {"MODEL_VERSION": REPRO, "MODEL_KIND": WC.DAILY_KIND, "ARCHITECTURE": WC.ARCHITECTURE,
                             "CODE_COMMIT": "test", "TRAINING_THROUGH_SEASON": 2026, "TRAINING_CUTOFF": "NO_2026_27_DATA",
                             "TRAINING_THROUGH_DATE": None, "LATEST_INCLUDED_GAME": None, "PREVIOUS_MODEL_VERSION": None,
                             "artifacts": {k: {"file": SO.FILES[k], "sha256": v} for k, v in sha.items()},
                             "supported_stats": frozen["supported_stats"], "supported_combos": frozen["supported_combos"],
                             "random_seed_policy": frozen["random_seed_policy"]})
    model = SO.load_dir(cand, REPRO)
    monkeypatch.setattr(RN.LD, "load", lambda paths=None, expected=None: model)
    got = G.capture(tmp_path / "golden")
    gold = json.loads(G.GOLDEN.read_text())
    assert got["platform"] == gold["platform"] == f"{sys.platform}-{platform.machine()}", "exact comparison needs the golden platform"
    bad = [b for b in G.compare(got, gold) if not b.endswith((".model_version", ".ModelVersion"))]
    assert bad == []
    assert {p["model_version"] for p in got["predictions"]} == {REPRO}


def test_opening_baseline_artifacts_unchanged():
    for k, (path, sha) in PC.ARTIFACTS.items():
        assert hashlib.sha256((C.PROJECT_ROOT / path).read_bytes()).hexdigest() == sha, k
    LD.verify()
