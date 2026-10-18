"""V2 Phase 3 (structural interdependence): chronology, accounting invariants, determinism, leakage guards, refits."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.v2 import joint as J, latent as L, phase3 as P3, phase3_prep as PP, structural as S

ENV = {"sd_pace": 0.07, "sd_eff": 0.14, "rho_eff": 0.2, "overtime": {"p": [0.9, 0.08, 0.02]}}


def _game(n_team=11, seed=0, p=None, rates=None, scored=None):
    rng = np.random.default_rng(seed)
    n = 2 * n_team
    team = np.repeat([0, 1], n_team)
    mm = np.tile(np.array([34, 32, 30, 28, 26, 18, 14, 10, 6, 3, 1.5][:n_team], dtype=float), 2)
    rt = rates if rates is not None else np.abs(rng.normal([0.4, 0.15, 0.08, 0.03], [0.1, 0.05, 0.04, 0.01], size=(n, 4)))
    pp = p if p is not None else np.clip(rng.uniform(0.5, 1.0, n), 0, 1)
    sc = scored if scored is not None else (pp > 0)
    return S.GameInput("G-test-%d" % seed, team, sc, pp, mm, np.full(n, 6.0), rt, np.zeros((2, 3)))


# ---------------------------------------------------------------------------- chronology / guards
def test_protocol_chronology():
    assert P3.SELECT["T"] < P3.SELECT["eval"] and max(P3.SELECT["resid_seasons"]) < P3.SELECT["eval"]
    assert P3.CONFIRM["T"] < P3.CONFIRM["eval"] and max(P3.CONFIRM["resid_seasons"]) < P3.CONFIRM["eval"]
    assert P3.SELECT["eval"] < P3.CONFIRM["eval"] == 2026 and P3.FINAL["T"] == 2026
    assert P3.SELECT["resid_seasons"] == (2024,) and P3.CONFIRM["resid_seasons"] == (2024, 2025)
    prot = json.loads(P3.PROTOCOL_PATH.read_text()) if P3.PROTOCOL_PATH.exists() else P3.protocol()
    assert prot["chronology"]["selection"]["evaluate"] == 2025
    assert prot["chronology"]["retrospective_confirmation"]["evaluate"] == 2026


def test_no_2026_27_outcomes():
    with pytest.raises(S.V2Phase3Violation):
        S.check_seasons([2026, 2027])
    with pytest.raises(S.V2Phase3Violation):
        S.load_team_frame([2027], labels=True)
    with pytest.raises(S.V2Phase3Violation):
        P3.load_store([2027])


def test_artifact_immutability():
    g = P3.guards()
    assert g["V1_ARTIFACT_UNCHANGED"] and g["V2_PHASE1B_ARTIFACT_UNCHANGED"] and g["PHASE2_J2_ARTIFACT_UNCHANGED"]
    assert g["OOS_STORE_UNCHANGED"] and g["F2023_STORE_UNCHANGED"]
    assert g["MARGINAL_ARTIFACT_SHA256"] == "bfcef1fb983b882170ab6e2e2a9857c9ecec8ff5059eaf01f048c8badf40d10f"
    assert g["PHASE2_DEPENDENCE_ARTIFACT_SHA256"] == "b41564a988f37a4c01a0dec9f8a9cb67aea69717294809b3eaa49acf4babb1ff"


def test_f2023_extension_reproduces_phase1b_rows():
    man = json.loads(PP.MANIFEST_PATH.read_text())
    assert man["row_builder_reproduces_F2024_store"]["REPRODUCED"] is True
    assert man["training_through_season"] == 2022 and man["forecast_season"] == 2023
    df = P3.load_store([2023])
    assert (df["training_through_season"] < df["forecast_season"]).all()


# ---------------------------------------------------------------------------- minutes accounting
@pytest.mark.parametrize("seed", range(5))
def test_exact_team_minutes_bounds_and_inactive_zero(seed):
    rng = np.random.default_rng(seed)
    R, n = 400, 13
    a = rng.normal(-1, 2, (R, n))
    act = rng.random((R, n)) < 0.7
    act[:, :5] = True
    act[:7, 5:] = False                                    # exactly 5 active rows
    dur = 40.0 + 5.0 * rng.integers(0, 3, R)
    M, _ = S.allocate(a, act, dur)
    assert np.abs(M.sum(axis=1) - 5 * dur).max() <= 1e-9
    assert (M >= 0).all() and (M <= dur[:, None] + 1e-9).all()
    assert (M[~act] == 0).all()
    assert np.allclose(M[:7, :5], dur[:7, None])


def test_fewer_than_five_active_is_refused_and_ensure_five_fixes():
    act = np.zeros((3, 8), bool)
    act[:, :4] = True
    with pytest.raises(S.V2Phase3Violation):
        S.solve_shift(np.zeros((3, 8)), act)
    fixed, nfix = S.ensure_five(act.copy(), np.linspace(1, 0.1, 8))
    assert nfix == 3 and (fixed.sum(axis=1) == 5).all() and fixed[:, 4].all()


def test_shared_duration_and_simulated_accounting():
    g = _game(seed=1)
    st = S.simulate_state(g, ENV, 3000)
    assert st.dur.shape == (3000,) and set(np.unique(st.dur)) <= {40.0, 45.0, 50.0}
    c = st.checks
    assert c["team_minutes_violations"] == 0 and c["minutes_bound_violations"] == 0
    assert c["inactive_minutes_nonzero"] == 0 and c["negative_share"] == 0 and c["share_sum_violations"] == 0
    # both teams of a draw share the same duration: every base-world team total is 5 * the SAME D
    for t in (0, 1):
        assert np.abs(st.base_M[:, g.team == t].sum(axis=1) - 5 * st.dur).max() <= 1e-9
    assert (st.M <= st.dur[:, None] + 1e-9).all() and (st.M > 0).all()      # scored players forced active


def test_opportunity_share_conservation():
    g = _game(seed=2)
    st = S.simulate_state(g, ENV, 2000)
    assert st.checks["negative_share"] == 0 and st.checks["share_sum_violations"] == 0   # every allocation, every family
    assert (st.shares_mean >= 0).all()
    gp = _game(seed=2, p=np.ones(22))                       # nobody uncertain: one world, shares sum to 1 exactly
    sp = S.simulate_state(gp, ENV, 2000)
    for t in (0, 1):
        assert np.allclose(sp.shares_mean[gp.team == t].sum(axis=0), 1.0, atol=1e-9)


def test_deterministic_scenario_draws():
    g = _game(seed=3)
    a, b = S.simulate_state(g, ENV, 2000), S.simulate_state(g, ENV, 2000)
    assert np.array_equal(a.M, b.M) and np.array_equal(a.dur, b.dur) and np.array_equal(a.x, b.x)
    assert np.array_equal(a.opp_deficit, b.opp_deficit)
    c = S.simulate_state(S.GameInput("G-other", *[getattr(g, f) for f in ("team", "scored", "p_play", "min_mu",
                                                                         "min_sigma", "rate", "env")]), ENV, 2000)
    assert not np.array_equal(a.M, c.M)


def test_anchor_calibration_reproduces_min_mu_when_consistent():
    n = 11
    mm = np.array([34, 32, 30, 28, 26, 18, 14, 10, 6, 3, 1.5])
    target = 5 * 40.4                                       # E[D] under ENV ~ 40.6
    p = np.full(2 * n, 0.95)
    mm2 = np.tile(mm * target / (0.95 * mm.sum()), 2)
    g = S.GameInput("G-cal", np.repeat([0, 1], n), np.ones(2 * n, bool), p, mm2, np.full(2 * n, 6.0),
                    np.full((2 * n, 4), 0.2), np.zeros((2, 3)))
    st = S.simulate_state(g, ENV, 20000)
    assert np.abs(st.M.mean(axis=0) - mm2).mean() < 0.6


# ---------------------------------------------------------------------------- availability interface
def test_availability_override_invariants():
    assert S.availability_probability("OUT", 0.97) == 0.0 and S.availability_probability("ACTIVE", 0.2) == 1.0
    assert S.availability_probability("EXPECTED_ACTIVE", 0.5) == 0.9
    assert S.availability_probability("QUESTIONABLE", 0.8, 0.4) == 0.4 and S.availability_probability(None, 0.7) == 0.7
    with pytest.raises(ValueError):
        S.availability_probability("DOUBTFUL_TYPO", 0.5)
    n = 11
    rates = np.tile(np.array([[0.4, 0.1, 0.05, 0.02]]), (2 * n, 1))
    rates[0] = [0.4, 0.05, 0.25, 0.03]                      # lead guard: playmaking
    rates[1] = [0.4, 0.35, 0.02, 0.03]                      # center: rebounding
    base_p = np.full(2 * n, 0.97)
    scored = np.ones(2 * n, bool)
    scored[[0, 1]] = False

    def run(out_idx):
        p = base_p.copy()
        if out_idx is not None:
            p[out_idx] = S.availability_probability("OUT", p[out_idx])
        g = _game(n_team=n, p=p, rates=rates, scored=scored)
        return S.simulate_state(g, ENV, 4000)
    base, guard_out, center_out = run(None), run(0), run(1)
    assert (guard_out.base_M[:, 0] == 0).all() and (center_out.base_M[:, 1] == 0).all()
    t0 = np.flatnonzero(scored)[:n - 2]                     # team-0 scored teammates
    assert guard_out.M[:, :n - 2].mean() > base.M[:, :n - 2].mean()  # teammates absorb the minutes
    pm, reb = S.FAM.index("PLAYMAKING"), S.FAM.index("REBOUNDING")
    d_g = guard_out.opp_deficit[:, :n - 2].mean(axis=(0, 1)) - base.opp_deficit[:, :n - 2].mean(axis=(0, 1))
    d_c = center_out.opp_deficit[:, :n - 2].mean(axis=(0, 1)) - base.opp_deficit[:, :n - 2].mean(axis=(0, 1))
    assert d_g[pm] > d_c[pm] and d_c[reb] > d_g[reb]        # missing guard vs center redistribute differently
    assert len(t0) == n - 2


# ---------------------------------------------------------------------------- conditional generation
def test_counts_equal_inverse_cdf_and_mixture_pmf():
    rng = np.random.default_rng(4)
    sims, d = 3000, 14
    lm = np.log(np.tile([12, 5, 3, 1.5, 0.8, 0.5, 1.4], 2))[None, :] + 0.3 * rng.standard_normal((sims, d))
    phi = np.array([0.12, 0.03, 0.05, 0.05, 0.04, 0.17, 0.0])
    tab = S.mixture_tables(lm, phi)
    z = rng.standard_normal((sims, d))
    x = S.counts(tab, z)
    import math
    u = np.array([[0.5 * (1 + math.erf(v / math.sqrt(2))) for v in row] for row in z[:200]])
    for c in range(d):
        rows = tab["inv"][:200, c]
        direct = (tab["cdf"][rows] < u[:, c][:, None]).sum(axis=1)
        mism = direct != x[:200, c]
        assert mism.mean() < 0.01                           # identical except float ties at a CDF step
    mix = S.mixture_pmf(tab, sims)
    assert np.allclose(mix.sum(axis=1), 1.0)
    for c in (0, 6):
        emp = np.bincount(x[:, c], minlength=mix.shape[1])[:mix.shape[1]] / sims
        assert np.abs(np.cumsum(emp) - np.cumsum(mix[c])).max() < 0.04


def test_same_draw_combo_identities():
    rng = np.random.default_rng(5)
    draws = rng.integers(0, 20, (100, 4, 7)).astype(np.int16)
    der = J.derived_markets(draws)
    for name, parts in J.COMBOS.items():
        assert np.array_equal(der[name], draws[:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2))


def test_conditional_dispersion_refit_recovers_phi():
    rng = np.random.default_rng(6)
    n = 200_000
    mu = rng.uniform(0.5, 15, (n, 7))
    y = np.empty_like(mu)
    phis = [0.12, 0.03, 0.05, 0.05, 0.04, 0.17, 0.0]
    for j, ph in enumerate(phis):
        y[:, j] = rng.poisson(mu[:, j] * (rng.gamma(1 / ph, ph, n) if ph > 0 else 1.0))
    fam = {s: ("POISSON" if s == "TOV" else "NEGBIN") for s in L.STATS}
    out = S.dispersion_refit(y, mu, fam)
    for j, s in enumerate(L.STATS):
        assert abs(out[s]["phi"] - phis[j]) < 0.01
    assert out["TOV"]["family"] == "POISSON" and out["TOV"]["phi"] == 0.0
    assert out["STL"]["family"] == "NEGBIN"


def test_anchor_blend():
    rng = np.random.default_rng(7)
    lm = np.log(5.0) + 0.3 * rng.standard_normal((5000, 3, 7))
    base = np.full((3, 7), np.log(4.0))
    out, m = S.anchor(lm, base, 0.5)
    assert np.allclose(np.exp(out).mean(axis=0), 0.5 * 4.0 + 0.5 * m)
    out1, _ = S.anchor(lm, base, 1.0)
    assert np.allclose(out1, lm)


# ---------------------------------------------------------------------------- leakage / fit isolation
def test_no_target_game_feature_leakage():
    tf = S.load_team_frame([2024], labels=False)
    assert not {"pts", "poss", "game_minutes"} & set(tf.columns)
    assert S.pit_audit(tf)["violations"] == 0
    tfl = S.load_team_frame([2024], labels=True)
    env = S.env_fit(S.load_team_frame([2022, 2023], labels=True))
    a = S.env_predict(tf, env).sort_values(["game_id", "team_id"]).reset_index(drop=True)
    shuffled = tfl.copy()
    shuffled[["pts", "poss", "game_minutes"]] = shuffled[["pts", "poss", "game_minutes"]].sample(frac=1, random_state=0).to_numpy()
    b = S.env_predict(shuffled, env).sort_values(["game_id", "team_id"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)                   # predictions never read target-game labels


def test_structural_oos_fit_isolation():
    s = {"T": 2024, "env": None, "glm": None, "terms": list(S.GLM_TERMS)}
    with pytest.raises(S.V2Phase3Violation):
        P3.residual_rows(2024, s)
    with pytest.raises(S.V2Phase3Violation):
        P3.residual_rows(2023, s)


def test_residual_dependence_fit_isolation_and_terms():
    assert P3.accepted_terms(["SHARED_GAME_ENVIRONMENT", "JOINT_MINUTES", "OPPORTUNITY_REDISTRIBUTION"]) == S.GLM_TERMS
    assert P3.accepted_terms(["SHARED_GAME_ENVIRONMENT", "OPPORTUNITY_REDISTRIBUTION"]) == ("pace_dev", "own_eff_dev", "opp_eff_dev")
    assert P3.accepted_terms([]) == ()
    # S4 components are fitted on the residual basis of seasons strictly before the evaluation season
    for cfg in (P3.SELECT, P3.CONFIRM):
        assert all(F < cfg["eval"] for F in cfg["resid_seasons"])
        assert all(F - 1 <= cfg["T"] for F in cfg["resid_seasons"])


def test_glm_recovers_planted_coefficients():
    rng = np.random.default_rng(8)
    n = 60_000
    X = np.column_stack([np.ones(n), rng.normal(0, 0.5, n), rng.normal(0, 0.1, n)])
    off = np.log(rng.uniform(1, 10, n))
    b = np.array([-0.02, 1.3, 0.9])
    y = rng.poisson(np.exp(off + X @ b))
    est, se = S.poisson_irls(X, y.astype(float), off)
    assert np.all(np.abs(est - b) < 4 * se + 1e-3)


def test_pmf_route_matches_phase1b_pmf():
    mu = np.array([0.4, 3.0, 12.0])
    tab = S.mixture_tables(np.log(mu)[None, :].repeat(10, 0), np.array([0.2] * 7))
    for c in range(3):
        assert np.allclose(S.mixture_pmf(tab, 10)[c], P.pmf_matrix(np.exp(np.round(np.log(mu[c:c + 1]) / S.GRID_H) * S.GRID_H), 0.2, L.KMAX)[0])
