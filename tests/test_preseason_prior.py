"""PI2 (prod.preseason_prior): class mapping, direction of the transfer translation, no-history shrinkage, clipping,
returners untouched, sealed table integrity, engine attribution."""
import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import engine as ENG, preseason_prior as PI


def _teams():
    st = PI.table()["strength_prev"]
    ranked = sorted(st, key=st.get)
    return ranked[0], ranked[-1], st          # weakest, strongest


def _frame(rows):
    base = {"player_game_example_id": None, "canonical_player_id": None, "canonical_team_id": None, "cur_min": 0.0,
            "h1_min": 900.0, "h2_min": 0.0, "h3_min": 0.0, "pps_same_team": 1.0, "pps_min_per_app": 28.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_table_integrity_and_size():
    t = PI.table()
    assert len(t["origin_team"]) > 1000 and len(t["strength_prev"]) >= 360


def test_classes_from_prior_season_state():
    c = PI.classes(_frame([{"pps_same_team": 1.0}, {"pps_same_team": 0.0}, {"pps_same_team": np.nan}]))
    assert list(c) == ["SAME_TEAM_RETURNER", "TRANSFER", "NO_PRIOR_HISTORY"]


def test_transfer_direction_down_up_and_no_history(monkeypatch):
    weak, strong, _ = _teams()
    t = PI.table()
    monkeypatch.setitem(t["origin_team"], "P-down", strong)
    monkeypatch.setitem(t["origin_team"], "P-up", weak)
    f = _frame([{"player_game_example_id": "a", "canonical_player_id": "P-down", "canonical_team_id": weak, "pps_same_team": 0.0},
                {"player_game_example_id": "b", "canonical_player_id": "P-up", "canonical_team_id": strong, "pps_same_team": 0.0},
                {"player_game_example_id": "c", "canonical_player_id": "P-new", "canonical_team_id": weak,
                 "pps_same_team": np.nan, "h1_min": 0.0},
                {"player_game_example_id": "d", "canonical_player_id": "P-ret", "canonical_team_id": weak}])
    adj, _ = PI.adjustments(f)
    assert adj["PTS"][0] > 1.0 > adj["PTS"][1]            # down-transfer up, up-transfer down
    assert adj["PTS"][2] == pytest.approx(1 + PI.COEF["PTS"][3])    # no history, 0 current minutes: full shrink
    assert all(adj[s][3] == 1.0 for s in PI.STATS)          # returner untouched


def test_apply_clips_total_factor_and_leaves_returners(monkeypatch):
    weak, strong, _ = _teams()
    f = _frame([{"player_game_example_id": "c", "canonical_player_id": "P-new", "canonical_team_id": weak, "pps_same_team": np.nan,
                 "h1_min": 0.0},
                {"player_game_example_id": "d", "canonical_player_id": "P-ret", "canonical_team_id": weak}])
    rows = pd.DataFrame({"player_game_example_id": ["c", "d"], **{f"{s}_mu": [10.0, 10.0] for s in PI.STATS},
                         "REB_mu": [5.0, 5.0]})
    base = rows[[f"{s}_mu" for s in PI.STATS]].copy()
    out, rec = PI.apply(rows, f, base)
    assert out.loc[0, "PTS_mu"] == pytest.approx(10.0 * PI.CLIP[0])     # 1 + e_PTS = 0.565 -> clipped at 0.7
    assert (out.loc[1, [f"{s}_mu" for s in PI.STATS]] == 10.0).all()
    assert (out["REB_mu"] == 5.0).all()                                 # REB / STL / BLK never touched
    assert rec["classes"] == {"NO_PRIOR_HISTORY": 1, "SAME_TEAM_RETURNER": 1}


def test_engine_attribution_names_pi2_after_ctx1():
    e = ENG.engine_id()
    assert "+CTX1+PI2#" in e
    assert "PI2" not in ENG.pre_context_tag()
