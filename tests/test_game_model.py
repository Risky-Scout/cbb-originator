"""Release B game layer: GameJointPMF math, market derivation from the ONE joint PMF, PIT ratings, sealed params."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from cbb_platform.game import joint as J, markets as GM, model as GMod, params as GP, ratings as GR


@pytest.fixture(scope="module")
def eot():
    return GP.endgame_ot()


@pytest.fixture(scope="module")
def vp():
    return GMod.VarianceParams(**GP.game_model()["variance"])


@pytest.fixture(scope="module")
def pmf(eot, vp):
    return GMod.build(GMod.targets(74.0, 68.0, 10, vp), eot)


# ----------------------------------------------------------------------------- joint PMF
def test_pmf_valid_zero_tie_mass_and_moments_match_targets(pmf, vp):
    v = pmf.validate()
    assert v["VALID"] and v["NEGATIVE_CELLS"] == 0 and v["TIE_MASS"] == 0.0 and abs(v["PMF_SUM"] - 1) < 1e-9
    mo = pmf.moments()
    sm, st = vp.sd(142.0, 10)
    assert abs(mo["exp_home"] - 74) < 0.01 and abs(mo["exp_away"] - 68) < 0.01
    assert abs(mo["sd_margin"] - sm) < 0.02 and abs(mo["sd_total"] - st) < 0.02


def test_pmf_is_deterministic(eot, vp):
    t = GMod.targets(70.5, 71.2, 3, vp)
    assert GMod.build(t, eot).sha256() == GMod.build(t, eot).sha256()


def test_overtime_resolves_every_tie(eot):
    t = J.Targets(70.0, 70.0, 9.0, 15.0, 0.0)
    g = J.build(t, eot.kernel(140), eot.ot)
    assert g.tie_mass() == 0.0
    assert g.meta["ot_residual_tie_split"] < 1e-6


@pytest.mark.parametrize("h,a,n", [(55, 50, 0), (90, 85, 25), (68, 80, 6), (72, 72, 2)])
def test_extreme_and_even_games_stay_coherent(eot, vp, h, a, n):
    g = GMod.build(GMod.targets(h, a, n, vp), eot)
    c = GM.coherence(g)
    assert c["COHERENT"], c


# ----------------------------------------------------------------------------- markets from the same PMF
def test_markets_all_derive_from_one_pmf(pmf):
    d = GM.Dist(pmf)
    H, A = np.meshgrid(np.arange(pmf.p.shape[0]) + pmf.h_min, np.arange(pmf.p.shape[1]) + pmf.a_min, indexing="ij")
    P = pmf.p
    # spread home -5.5, total 141.5 over, ML, team totals -- recomputed by brute force over the joint cells
    assert GM.side_probs(d, "SPREAD", "HOME", -5.5)[0] == pytest.approx(P[(H - A) > 5.5].sum(), abs=1e-12)
    assert GM.side_probs(d, "GAME_TOTAL", "OVER", 141.5)[0] == pytest.approx(P[(H + A) > 141.5].sum(), abs=1e-12)
    assert GM.side_probs(d, "MONEYLINE", "HOME", None)[0] == pytest.approx(P[H > A].sum(), abs=1e-12)
    assert GM.side_probs(d, "HOME_TEAM_TOTAL", "UNDER", 73.5)[0] == pytest.approx(P[H < 73.5].sum(), abs=1e-12)
    assert GM.side_probs(d, "ALT_AWAY_TEAM_TOTAL", "OVER", 70.0)[2] == pytest.approx(P[A == 70].sum(), abs=1e-12)


def test_integer_lines_push_and_complements(pmf):
    d = GM.Dist(pmf)
    hw, hl, hp = GM.side_probs(d, "SPREAD", "HOME", -6.0)
    aw, al, ap = GM.side_probs(d, "SPREAD", "AWAY", 6.0)
    assert hp == ap > 0 and hw == pytest.approx(al) and aw == pytest.approx(hl)
    hw2, _, hp2 = GM.side_probs(d, "SPREAD", "HOME", -5.5)
    assert hp2 == 0 and hw2 > hw
    ml = GM.side_probs(d, "MONEYLINE", "HOME", None)[0] + GM.side_probs(d, "MONEYLINE", "AWAY", None)[0]
    assert ml == pytest.approx(1.0, abs=1e-12)


def test_best_offer_is_max_ev_and_game_markets_are_never_actionable(pmf):
    q = pd.DataFrame([
        dict(game_id="G", book="a", market_type="SPREAD", side="HOME", point=-5.5, decimal=1.91),
        dict(game_id="G", book="a", market_type="SPREAD", side="AWAY", point=5.5, decimal=1.91),
        dict(game_id="G", book="b", market_type="SPREAD", side="HOME", point=-6.5, decimal=2.05),
        dict(game_id="G", book="b", market_type="SPREAD", side="AWAY", point=6.5, decimal=1.80),
        dict(game_id="G", book="a", market_type="ALT_SPREAD", side="HOME", point=-1.5, decimal=1.5),
    ])
    c = GM.candidates(pmf, q)
    b = GM.best_by_market(c)
    assert set(b) == {"SPREAD"}                                   # alternates excluded unless asked
    sp = [x for x in c if x["market_type"] == "SPREAD"]
    assert b["SPREAD"]["ev"] == max(x["ev"] for x in sp)
    assert all(x["paired"] for x in sp) and b["SPREAD"]["fair_p"] is not None
    dec = GM.decision(b["SPREAD"])
    assert dec["IS_ACTIONABLE"] is False and dec["SELECTION_TIER"] == "RESEARCH"
    assert "ALT_SPREAD" in GM.best_by_market(c, alternates=True)


def test_ev_uses_push_refund(pmf):
    d = GM.Dist(pmf)
    w, l, u = GM.side_probs(d, "SPREAD", "HOME", -6.0)
    q = pd.DataFrame([dict(game_id="G", book="a", market_type="SPREAD", side="HOME", point=-6.0, decimal=1.95)])
    c = GM.candidates(pmf, q)[0]
    assert c["ev"] == pytest.approx(w * 0.95 - l) and c["model_p"] == pytest.approx(w / (1 - u))
    assert c["fair_p"] is None and c["edge"] is None                 # unpaired -> no de-vig


# ----------------------------------------------------------------------------- ratings: point in time
def _toy_games():
    rows = []
    t0 = pd.Timestamp("2025-11-03 23:00", tz="UTC")
    teams = [f"T{i}" for i in range(6)]
    k = 0
    for d in range(20):
        for i in range(3):
            h, a = teams[(d + i) % 6], teams[(d + i + 3) % 6]
            hp = 75 + 3 * (int(h[1]) - int(a[1]))
            tip = t0 + pd.Timedelta(days=d)
            rows.append(dict(game_id=f"G{k}", season=2026, game_date_et=pd.Timestamp((tip - pd.Timedelta(hours=5)).date()),
                             tip_utc=tip, home=h, away=a, neutral=False, conference_game=False, home_pts=hp, away_pts=70,
                             ot=0, home_conf="c", away_conf="c", poss=68.0, poss_game=68.0,
                             knowable_at=tip + pd.Timedelta(hours=4)))
            k += 1
    return pd.DataFrame(rows)


def test_ratings_use_only_games_knowable_before_the_cutoff():
    g = _toy_games()
    cut = pd.Timestamp("2025-11-10 11:00", tz="UTC")
    r = GR.fit(g, cut, None, GR.RatingParams())
    assert r.meta["games"] == int((g.knowable_at < cut).sum())
    assert pd.Timestamp(r.meta["max_knowable_at"]) < cut
    # a future game's result changes nothing
    g2 = g.copy()
    g2.loc[g2.knowable_at >= cut, "home_pts"] += 40
    assert GR.fit(g2, cut, None, GR.RatingParams()).state_id() == r.state_id()


def test_ratings_recover_strength_order_and_are_deterministic():
    g = _toy_games()
    cut = pd.Timestamp("2025-12-01", tz="UTC")
    r1, r2 = GR.fit(g, cut, None, GR.RatingParams()), GR.fit(g, cut, None, GR.RatingParams())
    assert r1.state_id() == r2.state_id()
    off = dict(zip(r1.teams, r1.off))
    assert off["T5"] > off["T0"]
    pr = r1.predict(["T5"], ["T0"], [False])
    assert pr.exp_home.iloc[0] > pr.exp_away.iloc[0]
    unk = r1.predict(["NEW"], ["T0"], [True])
    assert not unk.home_known.iloc[0]


# ----------------------------------------------------------------------------- sealed params
def test_params_refuse_a_tampered_artifact(tmp_path):
    src = GP.ROOT
    for f in ("endgame_ot.npz", "endgame_ot.json", "game_model.json"):
        (tmp_path / f).write_bytes((src / f).read_bytes())
    m = json.loads((tmp_path / "game_model.json").read_text())
    m["variance"]["a_m"] = 9.0
    (tmp_path / "game_model.json").write_text(json.dumps(m))
    with pytest.raises(GP.ParamsIntegrityError):
        GP.game_model.__wrapped__(str(tmp_path)) if hasattr(GP.game_model, "__wrapped__") else GP.game_model(str(tmp_path))
    with open(tmp_path / "endgame_ot.npz", "ab") as f:
        f.write(b"x")
    with pytest.raises(GP.ParamsIntegrityError):
        GP.endgame_ot.__wrapped__(str(tmp_path)) if hasattr(GP.endgame_ot, "__wrapped__") else GP.endgame_ot(str(tmp_path))


def test_game_model_params_were_not_selected_on_the_confirmation_season():
    m = GP.game_model()
    assert m["splits"] == {"development": 2024, "selection": 2025, "confirmation": 2026, "prospective": 2027}
    assert "2025-26" in m["prior_exposure"] and m["adjust"]["beta"] == 0.0
