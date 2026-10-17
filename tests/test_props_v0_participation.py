"""Player-prop v0 participation layer (cbb_platform/props_v0_participation.py): labels, lockbox, leakage, probability
range, determinism, normalized conditional distributions, unchanged v0 conditional predictions."""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from cbb_platform import lockbox, props_v0 as P, props_v0_participation as Q


@pytest.fixture(scope="module")
def data():
    df, counts = Q.load()
    cfg = P.load_config()
    frame, finals, _ = P.load_frames(cfg)
    v0_state = P.fit(frame, finals, cfg, cfg["evaluations"][-1]["train_through"])
    cand, _, _ = P.load_frames(cfg, seasons=[2022, 2023, 2024, 2025], population="candidates")
    return df, counts, Q.fit(df), cfg, frame, v0_state, cand[cand["season"] == Q.VALIDATE_SEASON]


def test_unknown_participation_is_never_treated_as_dnp(data):
    df, counts, st, *_ = data
    assert df.loc[df["participation_status"].isin(["UNKNOWN_PARTICIPATION", "NOT_ON_ROSTER"]), "y"].isna().all()
    assert (df["y"] == 0).sum() == (df["participation_status"] == "DNP_CONFIRMED").sum()
    assert (df["y"] == 1).sum() == (df["participation_status"] == "PLAYED").sum()
    tr = df[df["season"].isin(Q.TRAIN_SEASONS)]
    assert st["n_train"] == int(tr["participation_status"].isin(["PLAYED", "DNP_CONFIRMED"]).sum())
    assert counts["2024"]["UNKNOWN_PARTICIPATION"] > 0                      # they exist and were excluded, not relabeled


def test_2025_26_cannot_be_loaded():
    with pytest.raises(lockbox.LockboxViolation):
        Q.load(seasons=(2024, lockbox.LOCKBOX_SEASON))
    with pytest.raises(lockbox.LockboxViolation):
        P.load_frames(P.load_config(), seasons=[2025, lockbox.LOCKBOX_SEASON], population="candidates")
    assert Q.VALIDATE_SEASON < lockbox.LOCKBOX_SEASON and max(Q.TRAIN_SEASONS) < Q.VALIDATE_SEASON


def test_validation_rows_cannot_enter_training(data):
    df, _, st, *_ = data
    poisoned = df.copy()
    va = poisoned["season"] == Q.VALIDATE_SEASON
    poisoned.loc[va, "y"] = 1 - poisoned.loc[va, "y"]
    for c in Q.NUMERIC:
        poisoned.loc[va, c] = poisoned.loc[va, c] * 10 + 3
    assert Q.fit(poisoned)["state_hash"] == st["state_hash"]
    control = df.copy()
    idx = control.index[(control["season"] == 2024) & control["y"].notna()][:2000]
    control.loc[idx, "y"] = 1 - control.loc[idx, "y"]
    assert Q.fit(control)["state_hash"] != st["state_hash"]
    assert df.loc[df["season"] == Q.VALIDATE_SEASON, "prediction_cutoff_utc"].min() > \
        df.loc[df["season"].isin(Q.TRAIN_SEASONS), "prediction_cutoff_utc"].max()


def test_probabilities_are_within_unit_interval(data):
    df, _, st, *_ = data
    for p in (Q.predict(df, st), Q.heuristic(df, st)):                      # every candidate, UNKNOWN included
        assert np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all()


def test_outputs_are_deterministic():
    a, b = Q.run(write=False), Q.run(write=False)
    assert a["output_hash"] == b["output_hash"] and a["model_state"]["state_hash"] == b["model_state"]["state_hash"]


def test_every_conditional_stat_distribution_is_normalized(data):
    df, _, st, cfg, _, v0_state, cand = data
    res = Q.pregame_outputs(cand.iloc[:5000], df, cfg, v0_state, st)
    assert set(res["conditional_stat_distribution"]) == {"MIN", *cfg["count_targets"]}
    for name, pmf in res["conditional_stat_distribution"].items():
        assert len(pmf) == 5000 and (pmf >= 0).all() and np.abs(pmf.sum(axis=1) - 1).max() < 1e-9, name
    t = res["table"]
    assert {"participation_probability", "conditional_minutes"} <= set(t.columns)
    assert t["participation_probability"].between(0, 1).all()


def test_v0_predictions_unchanged_when_conditioning_on_appearance(data):
    df, _, st, cfg, frame, v0_state, cand = data
    scored = frame[frame["season"] == Q.VALIDATE_SEASON]
    played = cand[cand["player_game_example_id"].isin(scored["player_game_example_id"])]
    assert len(played) == len(scored)
    a = P.predict(scored, cfg, v0_state)["challenger"]
    b = Q.pregame_outputs(played, df, cfg, v0_state, st)["table"]
    for name, t in [("MIN", "min")] + list(cfg["count_targets"].items()):
        col = "conditional_minutes" if name == "MIN" else f"{name}_mean_given_play"
        assert np.array_equal(a[t], b[col].to_numpy()), name
    # the frozen v0 artifact is untouched by the participation layer
    assert hashlib.sha256((P.CONFIG_PATH.parent / "metrics.json").read_bytes()).hexdigest() == \
        "ac426477731e9744129d20265dc0c035ae025340a4007b85d2251e3190db9314"
