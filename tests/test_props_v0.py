"""Player-prop Challenger v0 (cbb_platform/props_v0.py, props_v0_eval.py): lockbox, chronology, leakage, distributions,
determinism, missing outcomes."""
from __future__ import annotations

import json
import math

import duckdb
import numpy as np
import pytest

from cbb_platform import lockbox, props_v0 as P, props_v0_eval as E
from cbb_platform.stage3 import spec


@pytest.fixture(scope="module")
def data():
    cfg = P.load_config()
    frame, finals, excl = P.load_frames(cfg)
    return cfg, frame, finals, excl


def test_no_2025_26_rows_accessed(data):
    cfg, frame, finals, excl = data
    assert frame["season"].max() <= lockbox.LAST_DEVELOPMENT_SEASON and finals["season"].max() <= lockbox.LAST_DEVELOPMENT_SEASON
    assert set(frame["season"].unique()) <= set(spec.MODEL_SEASONS) and max(map(int, excl)) <= lockbox.LAST_DEVELOPMENT_SEASON
    assert all(ev["validate"] < lockbox.LOCKBOX_SEASON for ev in cfg["evaluations"])
    with pytest.raises(lockbox.LockboxViolation):
        P.load_frames(cfg, seasons=[2024, lockbox.LOCKBOX_SEASON])


def test_chronological_train_validation_separation(data):
    cfg, frame, finals, _ = data
    for ev in cfg["evaluations"]:
        assert ev["train_through"] < ev["validate"]
        st = P.fit(frame, finals, cfg, ev["train_through"])
        assert st["n_train_rows"] == int((frame["season"] <= ev["train_through"]).sum())
        va = frame[frame["season"] == ev["validate"]]
        assert va["prediction_cutoff_utc"].min() > frame.loc[frame["season"] <= ev["train_through"], "prediction_cutoff_utc"].max()
    # prior-season history is only used once it was knowable before the target cutoff
    assert (frame["h1_min"].notna() & (frame["season"] == spec.MODEL_SEASONS[0])).sum() == 0


def test_no_validation_leakage_into_model_state(data):
    cfg, frame, finals, _ = data
    T = cfg["evaluations"][0]["train_through"]
    base = P.fit(frame, finals, cfg, T)["state_hash"]
    poisoned, pfin = frame.copy(), finals.copy()
    later = poisoned["season"] > T
    for c in [c for c in poisoned.columns if c.startswith("y_")] + cfg["minutes_features"] + ["cur_min", "cur_pts", "h1_pts"]:
        poisoned.loc[later, c] = poisoned.loc[later, c] * 10 + 7          # validation labels AND features
    pfin.loc[pfin["season"] > T, ["min", "pts", "reb"]] = 1e6
    assert P.fit(poisoned, pfin, cfg, T)["state_hash"] == base
    control = frame.copy()                                                   # the hash does react to training rows
    control.loc[control.index[control["season"] == T][:1000], "y_pts"] += 5
    assert P.fit(control, finals, cfg, T)["state_hash"] != base


def test_distribution_probabilities_normalize():
    for mu in (0.01, 0.3, 1.0, 3.5, 8.0, 15.0, 30.0):
        for phi in (0.0, 0.1, 0.25, 0.35):
            row = P.pmf_matrix(np.array([mu]), phi, 120)[0]
            assert abs(row.sum() - 1) < 1e-6 and (row >= 0).all(), (mu, phi)
            if mu <= 15:                                       # tail beyond the support is negligible here
                assert abs((row * np.arange(121)).sum() - mu) < 1e-5 * max(mu, 1)
    p = P.pmf_matrix(np.array([2.7]), 0.0, 10)[0]
    exact = [math.exp(-2.7) * 2.7 ** k / math.factorial(k) for k in range(10)]
    assert all(abs(p[k] - exact[k]) < 1e-12 for k in range(10))
    assert abs(p[10] - (1 - sum(exact))) < 1e-12                # last point = P(Y >= kmax)
    m = P.minutes_pmf(np.array([0.5, 18.0, 39.0]), np.array([5.0, 7.0, 5.5]), 60)
    assert np.allclose(m.sum(axis=1), 1.0) and (m >= 0).all()


def test_means_are_finite_and_nonnegative(data):
    cfg, frame, finals, _ = data
    ev = cfg["evaluations"][-1]
    st = P.fit(frame, finals, cfg, ev["train_through"])
    pr = P.predict(frame[frame["season"] == ev["validate"]], cfg, st)
    for model in pr.values():
        for k, v in model.items():
            assert np.isfinite(v).all() and (v >= 0).all(), k


def test_deterministic_rerun(data):
    cfg, frame, finals, _ = data
    ev = cfg["evaluations"][0]
    a, ha = E.evaluate(frame, finals, cfg, ev)
    b, hb = E.evaluate(frame, finals, cfg, ev)
    assert ha == hb and a["state_hash"] == b["state_hash"]
    assert json.dumps(a["targets"], sort_keys=True) == json.dumps(b["targets"], sort_keys=True)


def test_missing_outcomes_are_not_converted_to_zero(data):
    cfg, frame, finals, excl = data
    con = duckdb.connect(str(spec.LABELS_DB), read_only=True)
    n_played_box = con.execute(f"""SELECT count(*) FROM lab.player_game_labels WHERE season BETWEEN 2014 AND 2025
        AND participation_status = 'PLAYED' AND box_row_present AND minutes_label_available""").fetchone()[0]
    no_counts = con.execute(f"""SELECT count(*) FROM lab.player_game_labels WHERE season BETWEEN 2014 AND 2025
        AND participation_status = 'PLAYED' AND box_row_present AND minutes_label_available AND NOT counting_labels_available""").fetchone()[0]
    con.close()
    assert len(frame) == n_played_box                          # only observed appearances; no DNP / UNKNOWN row fabricated
    assert frame["y_min"].notna().all()
    for t in cfg["count_targets"].values():
        assert frame[f"y_{t}"].isna().sum() == no_counts > 0   # unavailable count labels stay NaN, never 0
    # scoring drops NaN outcomes instead of treating them as zero
    y = np.array([2.0, np.nan, 0.0]); mu = np.array([1.0, 1.0, 1.0]); w = np.ones(3)
    assert P._dispersion(y, mu, w, 1.1)["overdispersion_ratio"] == pytest.approx(((1.0) + 1.0) / 2.0)
    seasons = {int(s) for s in excl}
    assert all(any(k.startswith("EXCLUDED_BOX_ROW_") or k.startswith("NOT_SCORED_") for k in excl[s]) for s in seasons)
