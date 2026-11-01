"""Production candidate v1 pricing + slate scorer (cbb_platform/props_v1_pricing.py): exact push-aware pricing, odds
conversions, monotonicity, separation of P(play), unchanged frozen model outputs, lockbox, byte-identical sample."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_participation as Q, props_v1_pricing as V

RNG = np.random.default_rng(20261009)


def _pmfs():
    mus = np.array([0.05, 0.4, 1.3, 3.0, 7.5, 14.0, 27.0])
    return np.vstack([P.pmf_matrix(mus, phi, 120) for phi in (0.0, 0.12, 0.25)])


# These tests require large gitignored historical stores and run fully in the authoritative local validation environment.
# GitHub CI retains frozen-artifact hash, golden-equivalence and fixture-backed production tests.
# Explicit existence checks only: a store that exists but is corrupt or fails a check still fails the test.
def _require_local_stores(*paths):
    missing = [str(Path(p).relative_to(C.PROJECT_ROOT)) for p in paths if not Path(p).exists()]
    if missing:
        pytest.skip(f"local-only gitignored store absent: {', '.join(missing)}")


@pytest.fixture(scope="module")
def scored():
    _require_local_stores(P.spec.FEATURES_DB, P.spec.LABELS_DB)
    model = V.load_model()
    cand = V.load_candidates()
    games = V.sample_games(cand)
    return model, cand[cand["canonical_game_id"].isin(games)], V.score(cand[cand["canonical_game_id"].isin(games)], model)


def test_probabilities_sum_to_one_and_half_lines_never_push():
    pmf = _pmfs()
    for line in (0.5, 1.0, 2.5, 3.0, 7.0, 18.5, 30.0, 119.5):
        r = V.price(pmf, line)
        assert np.allclose(r["over_probability"] + r["under_probability"] + r["push_probability"], 1.0, atol=1e-12)
        if line % 1:
            assert np.all(r["push_probability"] == 0)
        nonpush = r["over_probability_nonpush"] + r["under_probability_nonpush"]
        assert np.allclose(nonpush[np.isfinite(nonpush)], 1.0)


def test_integer_lines_preserve_push_and_pricing_is_exact_against_the_pmf():
    pmf = _pmfs()
    k = np.arange(pmf.shape[1])
    for line in (0.0, 1.0, 4.0, 4.5, 12.0, 12.5, 40.0):
        r = V.price(pmf, line)
        assert np.allclose(r["over_probability"], (pmf * (k > line)).sum(axis=1), atol=1e-14)
        assert np.allclose(r["under_probability"], (pmf * (k < line)).sum(axis=1), atol=1e-14)
        assert np.allclose(r["push_probability"], (pmf * (k == line)).sum(axis=1), atol=1e-14)
    assert np.all(V.price(pmf, 4.0)["push_probability"] > 0)                 # push kept, not discarded
    with pytest.raises(ValueError):
        V.price(pmf, 120.0)                                                  # beyond the exact support (tail point)
    with pytest.raises(ValueError):
        V.price(pmf, 3.25)


def test_fair_odds_invert_and_conversions_round_trip():
    p = RNG.uniform(0.01, 0.99, 5000)
    d = V.prob_to_decimal(p)
    assert np.allclose(1 / d, p)
    a = V.decimal_to_american(d)
    assert np.allclose(V.american_to_decimal(a), d, rtol=1e-12)
    assert np.allclose(V.decimal_to_american(V.american_to_decimal(np.array([-250.0, -110.0, 100.0, 145.0]))),
                       [-250.0, -110.0, 100.0, 145.0])
    assert V.decimal_to_american(np.array([2.0]))[0] == 100.0 and V.decimal_to_american(np.array([1.5]))[0] == -200.0
    r = V.price(_pmfs(), 2.0)
    ok = np.isfinite(r["fair_over_decimal"])
    assert np.allclose(1 / r["fair_over_decimal"][ok], r["over_probability_nonpush"][ok])


def test_raising_the_line_never_increases_over_and_lowering_never_increases_under():
    pmf = _pmfs()
    lines = np.arange(0, 60.5, 0.5)
    over = np.stack([V.price(pmf, l)["over_probability"] for l in lines], axis=1)
    under = np.stack([V.price(pmf, l)["under_probability"] for l in lines], axis=1)
    assert np.all(np.diff(over, axis=1) <= 1e-15) and np.all(np.diff(under, axis=1) >= -1e-15)


def test_participation_probability_stays_separate_from_the_pmf(scored):
    model, cand, out = scored
    res = Q.pregame_outputs(cand, model["part_rows"], model["cfg"], model["v0"], model["part"])
    for stat, pmf in res["conditional_stat_distribution"].items():
        assert np.allclose(pmf.sum(axis=1), 1.0)                            # conditional PMF: no DNP mass folded in
    pts = out[out["stat"] == "PTS"].drop_duplicates("canonical_player_id")
    assert pts["participation_probability"].between(0, 1).all() and (pts["participation_probability"] < 0.2).any()
    # every PTS price is reproduced from the conditional PMF alone: P(play) never enters the price
    pmf = res["conditional_stat_distribution"]["PTS"]
    row = {pid: i for i, pid in enumerate(res["table"]["canonical_player_id"])}
    o = out[out["stat"] == "PTS"]
    idx = o["canonical_player_id"].map(row).to_numpy()
    again = V.price(pmf[idx], o["line"].to_numpy())
    for k in ("over_probability", "under_probability", "push_probability"):
        assert np.array_equal(again[k], o[k].to_numpy()), k
    low = out[out["participation_probability"] < 0.05]
    assert np.allclose(low["over_probability"] + low["under_probability"] + low["push_probability"], 1.0)


def test_frozen_model_outputs_unchanged(scored):
    model, cand, out = scored
    means = P.predict(cand, model["cfg"], model["v0"])["challenger"]
    pp = Q.predict(cand.merge(model["part_rows"][["player_game_example_id"] +
                                                  [c for c in Q.NUMERIC + Q.FLAGS if c not in cand.columns]],
                              on="player_game_example_id"), model["part"])
    ref = pd.DataFrame({"canonical_player_id": cand["canonical_player_id"].to_numpy(), "pp": pp,
                        "min": means["min"], "pts": means["pts"]})
    got = out[out["stat"] == "PTS"].drop_duplicates("canonical_player_id").merge(ref, on="canonical_player_id")
    assert np.allclose(got["participation_probability"], got["pp"], atol=0) and np.allclose(got["mean"], got["pts"], atol=0)
    assert np.allclose(got["conditional_minutes"], got["min"], atol=0)
    tags = {"metrics.json": "ac426477731e9744129d20265dc0c035ae025340a4007b85d2251e3190db9314",
            "participation_metrics.json": "0138968e2c7b6dcc15e9c040ce19e79299c97bd4fd740b8202b985b02f98cbac"}
    for f, h in tags.items():
        assert hashlib.sha256((P.CONFIG_PATH.parent / f).read_bytes()).hexdigest() == h, f


def test_2025_26_remains_blocked():
    with pytest.raises(lockbox.LockboxViolation):
        V.load_candidates(lockbox.LOCKBOX_SEASON)
    with pytest.raises(ValueError):
        V.load_model(train_through=lockbox.LAST_DEVELOPMENT_SEASON)          # only the frozen 2023-24 state


def test_deterministic_rerun_is_byte_identical(scored, tmp_path):
    model, cand, out = scored
    V.write_csv(V.score(cand, model), tmp_path / "a.csv")
    V.write_csv(V.score(cand, model), tmp_path / "b.csv")
    a, b = (tmp_path / "a.csv").read_bytes(), (tmp_path / "b.csv").read_bytes()
    assert a == b and a == V.SAMPLE_PATH.read_bytes()                       # also equals the committed sample
    assert b"\r" not in a
