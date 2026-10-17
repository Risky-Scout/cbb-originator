"""V2 Phase 1 (cbb_platform/v2): the v2 loaders and fits reproduce v1 exactly when given v1's weights; the season
guard refuses 2026-27; weights are normalized; the v1 champion artifact is unchanged; the recorded stage outputs are
consistent (selection before confirmation, OOS store from earlier-trained states only)."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P, props_v0_participation as Q, props_v1_refit as R
from cbb_platform.v2 import marginal as M, provenance as PV

V1_SHA = "96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f"


@pytest.fixture(scope="module")
def cfg():
    return P.load_config()


@pytest.fixture(scope="module")
def dev(cfg):
    return M.load_frames(cfg, range(2014, 2026), features_db=PV.DEV_FEATURES, labels_db=PV.DEV_LABELS)


def test_v1_artifact_unchanged():
    assert hashlib.sha256(PV.V1_ARTIFACT.read_bytes()).hexdigest() == V1_SHA


def test_loader_reproduces_v1_loader(cfg, dev):
    f1, fi1, _ = P.load_frames(cfg)
    pd.testing.assert_frame_equal(f1, dev[0])
    pd.testing.assert_frame_equal(fi1, dev[1])
    pd.testing.assert_frame_equal(Q.load()[0], M.load_participation((2024, 2025), PV.DEV_FEATURES, PV.DEV_LABELS))


def test_v1ref_fit_reproduces_v1_states(cfg, dev):
    rec = json.loads((P.CONFIG_PATH.parent / "metrics.json").read_text())["evaluations"]
    for ev in cfg["evaluations"]:
        st = M.fit_v0(*dev, cfg, ev["train_through"], "V1REF", "TEST")
        assert st["state_hash"] == P.fit(*dev, cfg, ev["train_through"])["state_hash"] == rec[ev["name"]]["state_hash"]
    base = R.load(R.ARTIFACT_PATH)                     # v1 base refit through 2024-25 (pre STL override)
    assert M.fit_v0(*dev, cfg, 2025, "V1REF", "TEST")["state_hash"] == base["v0_state"]["state_hash"]


def test_w0_participation_reproduces_v1(cfg):
    rows = M.load_participation((2024, 2025), PV.DEV_FEATURES, PV.DEV_LABELS)
    for seasons in ((2024,), (2024, 2025)):
        assert M.fit_participation(rows, seasons, "W0", "TEST")["state_hash"] == Q.fit(rows, train_seasons=seasons)["state_hash"]
    assert M.fit_participation(rows, (2022, 2023), "W0", "TEST") is None     # no DNP labels before 2023-24


def test_season_guard_refuses_2026_27(cfg):
    for bad in ([2026, 2027], [2027], [2013, 2014]):
        with pytest.raises(M.V2SeasonViolation):
            M.check_seasons(bad)
    with pytest.raises(M.V2SeasonViolation):
        M.load_frames(cfg, [2026, 2027])
    with pytest.raises(M.V2SeasonViolation):
        M.load_participation([2027])
    import duckdb
    for p in (M.FEATURES_DB, M.LABELS_DB):         # the stores hold nothing after 2025-26
        con = duckdb.connect(str(p), read_only=True)
        tab = "feat.player_features" if p == M.FEATURES_DB else "lab.player_game_labels"
        assert con.execute(f"SELECT max(season) FROM {tab}").fetchone()[0] == 2026
        con.close()


def test_weights_normalized_and_exponential():
    s = np.array([2014] * 3 + [2020] * 5 + [2023] * 7)
    for pol, h in M.POLICIES.items():
        w = M.season_weights(s, 2023, pol)
        assert abs(w.mean() - 1) < 1e-12 and abs(w.sum() - len(s)) < 1e-9
        if h is None:
            assert np.allclose(w, 1.0) and M.n_eff(w) == pytest.approx(len(s))
        else:
            assert w[-1] / w[3] == pytest.approx(2 ** (3 / h)) and w[0] / w[-1] == pytest.approx(2 ** (-9 / h))
    assert np.allclose(M.season_weights(s, 2023, "V1REF"), P.recency_weights(2023 - s, 1.0))


def test_dev_stage_never_loaded_2025_26():
    if not M.DEV_PATH.exists():
        pytest.skip("dev stage not run")
    d = json.loads(M.DEV_PATH.read_text())
    assert d["season_2025_26_loaded"] is False and d["seasons_loaded"] == [2014, 2025]
    assert d["protocol_sha256"] == hashlib.sha256(M.PROTOCOL_PATH.read_bytes()).hexdigest()
    sel = json.loads(M.SELECTION_PATH.read_text())
    assert sel["POLICY_SELECTED_BEFORE_2025_26"] is True and sel["SELECTED_WEIGHTING_POLICY"] in M.POLICIES
    assert sel["dev_folds_sha256"] == hashlib.sha256(M.DEV_PATH.read_bytes()).hexdigest()
    for f in d["folds"]:                                  # every candidate of a fold scored the same rows
        assert len({c["targets"]["PTS"]["n"] for c in f["candidates"].values()}) == 1


def test_confirmation_after_selection():
    if not M.CONFIRM_PATH.exists():
        pytest.skip("confirmation not run")
    c = json.loads(M.CONFIRM_PATH.read_text())
    sel = json.loads(M.SELECTION_PATH.read_text())
    assert c["selected_policy"] == sel["SELECTED_WEIGHTING_POLICY"]
    assert c["selection_record_sha256"] == hashlib.sha256(M.SELECTION_PATH.read_bytes()).hexdigest()
    import subprocess
    # the selection record's commit is an ancestor of (or equal to) the commit that holds the confirmation record
    conf_commit = subprocess.run(["git", "log", "-1", "--format=%H", "--", str(M.CONFIRM_PATH)], cwd=M.C.PROJECT_ROOT,
                                 capture_output=True, text=True).stdout.strip()
    if conf_commit:
        r = subprocess.run(["git", "merge-base", "--is-ancestor", c["selection_record_commit"], conf_commit], cwd=M.C.PROJECT_ROOT)
        assert r.returncode == 0


def test_final_artifact_labels():
    if not M.ARTIFACT_PATH.exists():
        pytest.skip("final refit not run")
    a = json.loads(M.ARTIFACT_PATH.read_text())
    assert R.fingerprint(a) == a["artifact_fingerprint"]
    assert (a["SUPERVISED_TRAINING_THROUGH"], a["HISTORICAL_STATE_THROUGH"], a["PRODUCTION_SEASON"]) == (2026, 2026, 2027)
    assert a["role"].startswith("V2_CHALLENGER") and a["v0_state"]["dispersion"]["stl"]["phi"] == 0.16
    assert a["v0_state"]["train_through"] == 2026 and a["participation_state"]["train_seasons"] == [2024, 2025, 2026]
    assert a["spec_fingerprint"] == R.spec_fingerprint(P.load_config())
    assert M.ARTIFACT_PATH != PV.V1_ARTIFACT


def test_oos_store_out_of_sample():
    if not M.OOS_MANIFEST_PATH.exists():
        pytest.skip("OOS store not built")
    man = json.loads(M.OOS_MANIFEST_PATH.read_text())
    df = pd.read_parquet(M.OOS_PATH)
    assert hashlib.sha256(M.OOS_PATH.read_bytes()).hexdigest() == man["sha256"] and len(df) == man["rows"]
    assert (df["training_through_season"] < df["forecast_season"]).all()
    assert set(df["forecast_season"]) == {2024, 2025, 2026} and df["player_game_example_id"].is_unique
    assert not df["training_through_season"].eq(2026).any()            # never the final through-2025-26 model
    assert df.loc[df["forecast_season"] == 2024, "p_play"].isna().all()
    assert df.loc[df["forecast_season"] > 2024, "p_play"].between(0, 1).all()
