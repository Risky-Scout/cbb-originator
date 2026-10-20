"""Focused tests for the JM1_FINAL production freeze (cbb_platform/v2/jm1_final_production.py)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from cbb_platform.v2 import jm1_final as F, jm1_final_production as FP, joint_minutes_v2 as JM, structural as S


def test_final_chronology_and_rollback_identity():
    assert FP.FINAL == {"T": 2026, "resid_seasons": (2024, 2025, 2026)} and FP.PRODUCTION_SEASON == 2027
    assert max(FP.FINAL["resid_seasons"]) <= FP.FINAL["T"] <= F.MAX_SEASON < FP.PRODUCTION_SEASON
    with pytest.raises(F.JM1Violation):
        F.check_seasons([FP.PRODUCTION_SEASON])
    g = F.guards()
    assert g["MARGINAL_ARTIFACT_SHA256"] == FP.ROLLBACK["MARGINAL_ARTIFACT_SHA256"]
    assert g["PHASE2_DEPENDENCE_ARTIFACT_SHA256"] == FP.ROLLBACK["PHASE2_DEPENDENCE_ARTIFACT_SHA256"]


def test_frozen_jm1_final_code_unchanged_since_protocol():
    prot = json.loads(F.PROTOCOL_PATH.read_text())
    assert F.code_state() == prot["code_sha256_at_freeze"]


def test_architecture_constants_unchanged():
    assert JM.JM1_ALPHA == 1.0 and "opportunity_deficit" not in JM.JM_TERMS
    assert JM.JM_TERMS == ("intercept", "log_minutes_ratio", "pace_dev", "own_eff_dev", "opp_eff_dev")


@pytest.mark.skipif(not FP.ARTIFACT_PATH.exists(), reason="final artifacts not frozen yet")
def test_frozen_artifacts_load_and_reference_the_phase1b_marginal():
    prod = FP.load_production()
    a = json.loads(FP.ARTIFACT_PATH.read_text())
    assert a["RESIDUAL_DEPENDENCE_ARTIFACT_SHA256"] == JM._sha(FP.RESID_DEP_PATH)
    assert a["SUPERVISED_TRAINING_THROUGH"] == 2026 and a["PRODUCTION_SEASON"] == 2027
    assert set(prod["s4"]["dispersion"]) == set(S.STATS) and prod["struct"]["T"] == 2026
    m = json.loads(FP.MANIFEST_PATH.read_text())
    assert m["artifacts"]["jm1_final_structural"]["sha256"] == JM._sha(FP.ARTIFACT_PATH)
    assert m["rollback_model"]["ROLLBACK_ARTIFACTS_UNCHANGED"] and m["deterministic_final_fit"]["IDENTICAL"]
    assert m["production_integrity"]["ALL_PASS"] and not m["DEPLOYMENT_PERFORMED"]
    assert np.isfinite([v["phi"] for v in prod["s4"]["dispersion"].values()]).all()


def test_numeric_diff_tolerates_last_bit_noise_only():
    a = {"x": 0.17849206301212597, "n": 3, "s": "J2", "v": [1.0, 2.0]}
    assert FP.numeric_diff(a, a | {"x": 0.17849206301212653})["NUMERICALLY_IDENTICAL"]
    assert not FP.numeric_diff(a, a | {"x": 0.1785})["NUMERICALLY_IDENTICAL"]
    assert not FP.numeric_diff(a, a | {"n": 4})["NUMERICALLY_IDENTICAL"]
    assert not FP.numeric_diff(a, a | {"s": "J0"})["NUMERICALLY_IDENTICAL"]
    assert not FP.numeric_diff(a, a | {"v": [1.0]})["NUMERICALLY_IDENTICAL"]
