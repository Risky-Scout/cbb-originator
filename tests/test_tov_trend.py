"""T1 TOV within-season trend (prod.tov_trend, promoted 2026-11-24): frozen constants, the replay formula, TOV only."""
import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import engine as ENG, tov_trend as TT


def test_constants_are_the_declared_fit():
    assert TT.B == pytest.approx(-0.07673824529357458, abs=0) and TT.UBAR == pytest.approx(0.7213194232871556, abs=0)
    assert TT.enabled()


def test_season_clock_matches_the_evaluated_replay():
    cut = np.array(["2026-11-01T16:00:00Z", "2026-11-24T23:30:00Z", "2027-01-01T04:30:00Z", "2027-03-15T17:00:00Z"])
    u = TT.season_u(cut)
    # replay (p5_replay tov_trend arm): ET-naive cutoff minus Nov 1 of the season, whole days / 100
    assert u.tolist() == [0.0, 0.23, 0.6, 1.34]                     # 01-01 04:30Z is 12-31 23:30 ET -> day 60
    assert np.allclose(TT.multiplier(cut), np.exp(TT.B * (u - TT.UBAR)), rtol=0, atol=0)
    assert TT.multiplier(cut)[0] > 1.05 > 1.0 > TT.multiplier(cut)[-1]  # more turnovers early in the season


def _frames():
    cand = pd.DataFrame({"player_game_example_id": ["a", "b", "c"],
                         "prediction_cutoff_utc": ["2026-11-24T16:00:00Z", "2026-11-24T16:00:00Z", "2027-02-20T16:00:00Z"]})
    rows = pd.DataFrame({"player_game_example_id": ["c", "a", "b"], "TOV_mu": [1.0, 2.0, 0.5], "PTS_mu": [10.0, 5.0, 3.0],
                         "min_mu": [30.0, 20.0, 8.0]})
    return rows, cand


def test_only_tov_mean_changes_and_rows_are_matched_by_example_id():
    rows, cand = _frames()
    out, rec = TT.apply(rows, cand)
    m = TT.multiplier(cand.set_index("player_game_example_id").loc[["c", "a", "b"], "prediction_cutoff_utc"].to_numpy())
    assert np.allclose(out["TOV_mu"], rows["TOV_mu"] * m, rtol=0, atol=0)
    assert out.drop(columns="TOV_mu").equals(rows.drop(columns="TOV_mu")) and rec["rows"] == 3


def test_disabled_is_identity_and_names_itself_in_the_engine(monkeypatch):
    assert "+TOVT1#" in ENG.engine_id()
    rows, cand = _frames()
    monkeypatch.setattr(TT, "ENABLED", False)
    out, _ = TT.apply(rows, cand)
    assert out is rows and "TOVT1" not in ENG.engine_id()
