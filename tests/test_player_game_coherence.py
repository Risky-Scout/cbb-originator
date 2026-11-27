"""Step 7 / 11 invariant (ROOT_CAUSE directive): when a material starter becomes OUT intraday, the SAME information moves
the player layer and the game layer. Player layer: the OUT player's row leaves the model inputs (prod.inputs
apply_availability) so his PMF is gone and the team's expected player points S fall (game.forecast.team_strength).
Game layer: the delta S vs the day's first snapshot enters GMod.targets, so ML, spread, home team total and game total
of the GameJointPMF all move in the coherent direction. If the player side reacts and the game side does not: FAIL."""
import pandas as pd

from cbb_platform.game import forecast as GF, markets as GM, model as GMod, params as GP


def _inputs():
    rows = [("G1", "H", f"h{i}", 1.0, mu) for i, mu in enumerate([18.0, 12.0, 9.0, 7.0, 5.0, 3.0])]
    rows += [("G1", "A", f"a{i}", 1.0, mu) for i, mu in enumerate([14.0, 12.0, 10.0, 8.0, 6.0, 4.0])]
    return pd.DataFrame(rows, columns=["game_id", "team_id", "player_id", "p_play", "PTS_mu"])


def _pmf(dh: float, da: float = 0.0):
    gm = GP.game_model()
    t = GMod.targets(74.0, 70.0, 6, GMod.VarianceParams(**gm["variance"]), GMod.AdjustParams(**gm["adjust"]), dh, da)
    return t, GF.canonical(GMod.build(t, GP.endgame_ot()))


def _production_rows(status_h0: str | None):
    """The production representation: prod.inputs.apply_availability keeps every row and flags a confirmed
    unavailable player (player_suppression), exactly as model_inputs.parquet stores it."""
    import numpy as np
    from cbb_platform.prod import inputs as IN
    r = _inputs().assign(min_mu=30.0, min_sigma=4.0, role_diagnostic="TOP5_MINUTES",
                         **{f"{s}_mu": 1.0 for s in IN.L.STATS if s != "PTS"})
    av = None if status_h0 is None else pd.DataFrame([{"game_id": "G1", "player_id": "h0", "status": status_h0,
                                                       "received_at": pd.Timestamp("2026-11-26T15:00Z")}])
    rows, _ = IN.apply_availability(r, av)
    assert np.isfinite(rows["p_play"]).all()
    return rows.drop(columns=["_scored"])


def test_flagged_out_row_leaves_team_strength_and_questionable_does_not():
    base, out, q = _production_rows(None), _production_rows("OUT"), _production_rows("QUESTIONABLE")
    assert (out.loc[out.player_id == "h0", "player_suppression"] == "CONFIRMED_UNAVAILABLE").all() and len(out) == len(base)
    s0, s1, sq = GF.team_strength(base), GF.team_strength(out), GF.team_strength(q)
    assert abs((s0[("G1", "H")] - s1[("G1", "H")]) - 18.0) < 1e-12 and s1[("G1", "A")] == s0[("G1", "A")]
    assert sq == s0                                               # questionable: the model's own p_play, unchanged


def test_starter_out_moves_player_and_game_layers_coherently():
    before = _production_rows(None)
    after = _production_rows("OUT")                             # the starter is OUT: flagged, never simulated
    s0, s1 = GF.team_strength(before), GF.team_strength(after)
    assert s1[("G1", "H")] < s0[("G1", "H")] and s1[("G1", "A")] == s0[("G1", "A")]
    dh = s1[("G1", "H")] - s0[("G1", "H")]
    t0, g0 = _pmf(0.0)
    t1, g1 = _pmf(dh)
    assert t1.exp_home < t0.exp_home and t1.exp_away == t0.exp_away
    a, b = GM.summary(g0), GM.summary(g1)
    assert b["P_HOME_WIN"] < a["P_HOME_WIN"]                 # moneyline
    assert b["MODEL_MARGIN"] < a["MODEL_MARGIN"]             # spread
    assert b["MODEL_HOME_SCORE"] < a["MODEL_HOME_SCORE"]     # home team total
    assert abs(b["MODEL_AWAY_SCORE"] - a["MODEL_AWAY_SCORE"]) < 0.05      # away team total unchanged (no away news)
    assert b["MODEL_TOTAL"] < a["MODEL_TOTAL"]               # game total


# ---------------------------------------------------------------- Stage 05: same-snapshot news-free baseline
def test_news_free_baseline_counts_news_known_at_the_first_snapshot_exactly_once():
    out, q = _production_rows("OUT"), _production_rows("QUESTIONABLE")
    s, s0 = GF.team_strength(out), GF.team_strength(out, news_free=True)
    assert abs((s0[("G1", "H")] - s[("G1", "H")]) - 18.0) < 1e-12 and s0[("G1", "A")] == s[("G1", "A")]
    assert GF.team_strength(q, news_free=True) == GF.team_strength(q)     # uncertainty: no mean shift
    base = _production_rows(None)
    assert GF.team_strength(base, news_free=True) == GF.team_strength(base)   # no news: delta 0


def test_forecast_uses_the_news_free_baseline_by_default_and_initial_on_fallback(tmp_path, monkeypatch):
    import inspect
    src = inspect.getsource(GF.forecast)
    assert "team_strength(model_inputs, news_free=True)" in src and "initial_strength(day_dir, gid)" in src
    monkeypatch.delenv(GF.BASELINE_ENV, raising=False)
    assert GF.availability_baseline() == "NEWS_FREE"
    monkeypatch.setenv(GF.BASELINE_ENV, "initial")
    assert GF.availability_baseline() == "INITIAL"
    monkeypatch.setenv(GF.BASELINE_ENV, "garbage")
    assert GF.availability_baseline() == "NEWS_FREE"
