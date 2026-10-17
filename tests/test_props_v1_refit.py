"""Production candidate v1 final development refit (cbb_platform/props_v1_refit.py): training range, lockbox, frozen
specification, serialization, deterministic refit, loaded-artifact scoring, pricing exactness, separate P(play)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_participation as Q, props_v1_pricing as V
from cbb_platform import lockbox_state as S, props_v1_refit as R

FROZEN_TAG = "cbb-props-production-candidate-v1"
FROZEN_FILES = ["cbb_platform/props_v0.py", "cbb_platform/props_v0_participation.py", "cbb_platform/props_v1_pricing.py",
                "modeling/player_props_v0/config.json"]


@pytest.fixture(scope="module")
def art():
    return R.fit_artifact()


def test_training_stops_at_2024_25(art):
    assert art["training_through_season"] == R.TRAIN_THROUGH == lockbox.LAST_DEVELOPMENT_SEASON
    assert art["stat_training_seasons"] == [2014, 2025] and art["participation_training_seasons"] == [2024, 2025]
    assert pd.Timestamp(art["v0_state"]["train_max_cutoff"]) < pd.Timestamp(str(lockbox.LOCKBOX_START), tz="US/Eastern")
    pr = art["participation_rows"]
    assert art["participation_state"]["n_train"] == sum(r["played"] + r["dnp"] for r in pr.values())
    assert all(r["unknown_excluded"] > 0 for r in pr.values())                 # excluded, not relabeled


def test_2025_26_cannot_be_accessed(art, tmp_path):
    with pytest.raises(lockbox.LockboxViolation):
        Q.load(seasons=(2025, lockbox.LOCKBOX_SEASON))
    with pytest.raises(lockbox.LockboxViolation):
        P.load_frames(art["config"], seasons=[2025, lockbox.LOCKBOX_SEASON])
    bad = dict(art, training_through_season=lockbox.LOCKBOX_SEASON)
    bad["artifact_fingerprint"] = R.fingerprint(bad)
    R.save(bad, tmp_path / "bad.json")
    with pytest.raises(lockbox.LockboxViolation):
        R.load(tmp_path / "bad.json")


def test_frozen_specification_is_unchanged(art):
    if S.current() == S.PRE_LOCKBOX_READINESS:
        diff = subprocess.run(["git", "diff", "--quiet", FROZEN_TAG, "--", *FROZEN_FILES], cwd=C.PROJECT_ROOT)
        assert diff.returncode == 0, "frozen v1 model / pricing / config files differ from the tag"
    else:   # post-lockbox: equal to the tag at readiness d65aac9; since then only the reviewed scoring-enable window gating
        assert S.unchanged_since_readiness_except_reviewed_patches(FROZEN_TAG, FROZEN_FILES), \
            "frozen v1 model / pricing / config files differ from the tag beyond the reviewed lockbox patches"
    frozen_cfg = json.loads(subprocess.run(["git", "show", f"{FROZEN_TAG}:modeling/player_props_v0/config.json"],
                                           cwd=C.PROJECT_ROOT, capture_output=True, check=True).stdout)
    assert art["config"] == frozen_cfg and art["spec_fingerprint"] == R.spec_fingerprint(frozen_cfg)


def test_artifact_serialization_round_trips_exactly(art, tmp_path):
    R.save(art, tmp_path / "a.json")
    back = R.load(tmp_path / "a.json")
    assert back == art and R.dumps(back) == (tmp_path / "a.json").read_text()
    assert R.ARTIFACT_PATH.read_text() == R.dumps(art)                         # committed artifact = this refit
    shuffled = json.loads(R.dumps(art))                                        # a key-sorting tool would misalign
    shuffled["v0_state"]["coef"] = dict(sorted(shuffled["v0_state"]["coef"].items()))
    (tmp_path / "s.json").write_text(json.dumps(shuffled))
    with pytest.raises(ValueError):
        R.load(tmp_path / "s.json")


def test_two_independent_refits_produce_identical_fingerprints(art):
    again = R.fit_artifact()
    assert again["artifact_fingerprint"] == art["artifact_fingerprint"] and R.dumps(again) == R.dumps(art)


_FRESH = """
import hashlib, sys
from cbb_platform import props_v0 as P, props_v0_participation as Q, props_v1_refit as R, props_v1_pricing as V
def _no_fit(*a, **k):
    raise RuntimeError("retraining attempted")
P.fit = Q.fit = _no_fit
out = R.score_demo(R.load())
print(hashlib.sha256(out.to_csv(index=False, lineterminator="\\n", float_format="%.6f").encode()).hexdigest(), len(out))
"""


def test_loaded_artifact_scores_without_retraining_and_matches(art):
    res = subprocess.run([sys.executable, "-c", _FRESH], cwd=C.PROJECT_ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]
    fresh_hash, n = res.stdout.split()[-2:]
    mem = R.score_demo(art)
    assert int(n) == len(mem) > 0
    assert hashlib.sha256(mem.to_csv(index=False, lineterminator="\n", float_format="%.6f").encode()).hexdigest() == fresh_hash


def test_pricing_exact_and_participation_separate(art):
    model = R.scoring_model(R.load())
    cand = V.load_candidates()
    cand = cand[cand["canonical_game_id"].isin(V.sample_games(cand))]
    res = Q.pregame_outputs(cand, model["part_rows"], model["cfg"], model["v0"], model["part"])
    out = V.score(cand, model)
    k = np.arange(model["cfg"]["pmf_support_max"] + 1)
    row = {pid: i for i, pid in enumerate(res["table"]["canonical_player_id"])}
    for stat, pmf in res["conditional_stat_distribution"].items():
        assert np.abs(pmf.sum(axis=1) - 1).max() < 1e-9, stat                  # conditional PMF, no DNP mass
        if stat == "MIN":
            continue
        o = out[out["stat"] == stat]
        p = pmf[o["canonical_player_id"].map(row).to_numpy()]
        line = o["line"].to_numpy()[:, None]
        assert np.allclose((p * (k > line)).sum(axis=1), o["over_probability"], atol=1e-12), stat
        assert np.allclose((p * (k == line)).sum(axis=1), o["push_probability"], atol=1e-12), stat
    assert np.allclose(out["over_probability"] + out["under_probability"] + out["push_probability"], 1.0)
    pp = out.drop_duplicates("canonical_player_id").set_index("canonical_player_id")["participation_probability"]
    assert np.array_equal(pp.loc[res["table"]["canonical_player_id"]].to_numpy(),
                          res["table"]["participation_probability"].to_numpy())
