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


def test_starter_out_moves_player_and_game_layers_coherently():
    before = _inputs()
    after = before[before.player_id != "h0"]                    # the starter is OUT: removed from model inputs
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
