"""Focused tests for the CBB V2 JointMinutesV2 challenger (cbb_platform/v2/joint_minutes_v2.py)."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.v2 import joint as J, joint_minutes_v2 as JM, phase3 as P3, structural as S

ENV = {"overtime": {"p": [0.94, 0.05, 0.01]}, "sd_pace": 0.07, "sd_eff": 0.14, "rho_eff": 0.19}
CF = np.array([[0.16, 0.0, -0.07], [0.0, 0.02, -0.01], [-0.07, -0.01, 0.04]])
PARAMS = {"e_dur": JM.expected_duration(ENV["overtime"]["p"]), "reservoir": {"cv2_pos": 0.9}, "tier_factor": CF}


def _game(n_per_team=12, p=0.8, res_pi=(0.01, 0.6), res_mu=(30.0, 50.0), gid="G-test"):
    n = 2 * n_per_team
    rng = np.random.default_rng(3)
    team = np.repeat([0, 1], n_per_team)
    mu = np.tile(np.concatenate([np.full(5, 30.0), np.full(3, 18.0), np.full(n_per_team - 8, 6.0)]), 2)
    tier = np.tile(np.concatenate([np.zeros(5, int), np.ones(3, int), np.full(n_per_team - 8, 2)]), 2)
    pp = np.full(n, p) if np.isscalar(p) else np.asarray(p, dtype=float)
    scored = np.zeros(n, bool)
    scored[[0, 1, 5, 9, n_per_team, n_per_team + 6]] = True
    rv = np.asarray(res_pi) * np.asarray(res_mu) ** 2 * 1.9 - (np.asarray(res_pi) * np.asarray(res_mu)) ** 2
    return JM.JMInput(gid, team, scored, pp, mu, np.full(n, 6.5), tier, rng.uniform(35, 52, n), np.zeros((2, 3)),
                      np.asarray(res_pi) * np.asarray(res_mu), np.asarray(res_pi), np.asarray(res_mu), rv)


# ---------------------------------------------------------------- chronology / protection
def test_no_2025_26_or_later_read():
    for f in (lambda: JM.check_seasons([2026]), lambda: JM.load_store([2026]), lambda: JM.reservoir_frame([2026], False)):
        with pytest.raises(JM.JMViolation):
            f()
    assert JM.DEV["eval"] == 2025 and JM.DEV["T"] == 2024 and max(JM.DEV["resid_seasons"]) <= JM.DEV["T"]


def test_old_artifacts_and_user_qa_file_unchanged():
    g = JM.guards()
    assert g["V2_PHASE1B_ARTIFACT_UNCHANGED"] and g["PHASE2_J2_ARTIFACT_UNCHANGED"] and g["V1_ARTIFACT_UNCHANGED"]
    assert all(g[f"{k}_UNCHANGED"] for k in JM.PHASE3_FROZEN)
    assert hashlib.sha256(JM.USER_QA_FILE.read_bytes()).hexdigest() == JM.USER_QA_SHA


def test_dispersion_and_residual_chronology():
    with pytest.raises(S.V2Phase3Violation):
        P3.residual_rows(2024, {"T": 2024, "terms": list(JM.JM_TERMS)})


# ---------------------------------------------------------------- candidate universe / reservoir legality
def test_candidate_universe_is_point_in_time():
    a = JM.universe_audit([2024])
    assert a["LEGAL_PIT_MEMBERSHIP_EVIDENCE_NOT_IN_UNIVERSE"] == 0
    assert all(not r["legal_evidence"] for r in a["realized_players_outside_universe_DIAGNOSTIC"])


def test_reservoir_features_contain_no_target_outcomes():
    pre = JM.reservoir_frame([2024], labels=False)
    lab = JM.reservoir_frame([2024], labels=True)
    assert "res_minutes" not in pre.columns
    pd.testing.assert_frame_equal(pre, lab.drop(columns="res_minutes"))
    assert JM.reservoir_pit_audit(pre)["violations"] == 0
    X = JM.reservoir_design(pre)
    assert X.shape[1] == len(JM.RES_FEATURES) and np.isfinite(X).all()


def test_no_realized_participant_oracle_in_simulation():
    """The game state depends only on pregame inputs: scrambling every outcome column leaves it bit-identical."""
    rows = P3.load_store([2024])
    gid = rows["game_id"].iloc[0]
    gp = rows[rows["game_id"] == gid].copy()
    gp["_scored"] = P3.scored_mask(gp)
    gp["p_play"] = 0.8
    rp = {(gid, t): {"res_mean": 1.0, "res_pi": 0.05, "res_mu_pos": 20.0, "res_var": 30.0} for t in gp["team_id"].unique()}
    wf = {"groups": {f"{r}|{c}": {"w": 40.0} for r in JM.ROLES4 for c in ("WARM", "COLD")}}
    prm = PARAMS | {"weights": wf}
    a = JM.simulate_jm(JM.jm_input(gid, gp, {}, rp, prm), ENV, prm, 400)
    gp2 = gp.copy()
    for c in ["y_min", "participation_status"] + [f"y_{s}" for s in S.STATS]:
        gp2[c] = gp2[c].sample(frac=1.0, random_state=1).to_numpy()
    b = JM.simulate_jm(JM.jm_input(gid, gp2, {}, rp, prm), ENV, prm, 400)
    assert np.array_equal(a.M, b.M) and np.array_equal(a.dur, b.dur)


def test_reservoir_is_not_a_player():
    g = _game()
    st = JM.simulate_jm(g, ENV, PARAMS, 2000)
    assert st.M.shape == (2000, int(g.scored.sum())) and st.M1.shape == st.M.shape
    assert all(set(r) >= {"raw_mean", "rec_mean"} for r in st.diag["rows"]) and len(st.diag["rows"]) == g.scored.sum()


# ---------------------------------------------------------------- participation
def test_pplay_identifiable_and_no_forced_activation():
    p = np.tile(np.concatenate([np.full(5, 0.3), np.full(7, 0.2)]), 2)      # fewer than five active is common
    g = _game(p=p)
    st = JM.simulate_jm(g, ENV, PARAMS, 20000)
    af = np.concatenate(st.diag["act_freq"])
    assert np.abs(af - p).max() < 0.02                                       # P(play) unchanged after feasibility
    assert st.checks["activation_repairs"] == 0
    assert np.mean([t["n_active_lt5_share"] for t in st.diag["teams"]]) > 0.3
    assert st.checks["team_minutes_violations"] == 0 and st.checks["reservoir_negative"] == 0


# ---------------------------------------------------------------- raw marginal, latent dependence
def test_phase1b_conditional_minutes_anchor_before_reconciliation():
    mu, sg = np.array([31.0, 17.5, 4.0]), np.array([6.6, 6.9, 5.1])
    z = np.random.default_rng(0).standard_normal((200000, 3))
    e = 40.37
    x = JM.raw_minutes(mu, sg, z, np.full(200000, e), e)
    pm = P.minutes_pmf(mu, sg, JM.KMAX)
    for i in range(3):
        h = np.bincount(np.round(x[:, i]).astype(int), minlength=JM.KMAX + 1)[: JM.KMAX + 1] / len(x)
        assert np.abs(h - pm[i]).max() < 0.004


def test_tier_factor_psd_and_unit_latent():
    bad = np.array([[0.3, 0.6, -0.5], [0.6, -0.2, 0.4], [-0.5, 0.4, 0.2]])
    A, rep = JM.project_factor(bad)
    assert rep and np.linalg.eigvalsh(A).min() > -1e-10 and (np.diag(A) >= 0).all() and (np.diag(A) <= 0.95).all()
    tier = np.array([0, 0, 1, 2, 2])
    rng = np.random.default_rng(1)
    z = JM.latent_minutes(CF, tier, np.zeros(5, int), rng.standard_normal((200000, 2, 3)), rng.standard_normal((200000, 5)))
    assert np.abs(z.std(axis=0) - 1).max() < 0.01
    c = np.corrcoef(z.T)
    assert abs(c[0, 1] - CF[0, 0]) < 0.01 and abs(c[0, 3] - CF[0, 2]) < 0.01 and abs(c[3, 4] - CF[2, 2]) < 0.01


def test_deterministic_latent_dependence_and_state():
    g = _game()
    a, b = JM.simulate_jm(g, ENV, PARAMS, 3000), JM.simulate_jm(g, ENV, PARAMS, 3000)
    assert np.array_equal(a.M, b.M) and np.array_equal(a.M1, b.M1) and np.array_equal(a.x, b.x)


# ---------------------------------------------------------------- reconciliation / accounting
def test_team_minute_conservation_bounds_and_inactive_zero():
    rng = np.random.default_rng(2)
    R, n = 5000, 15
    r = rng.uniform(0, 45, (R, n))
    act = rng.random((R, n)) < 0.55
    dur = 40 + 5 * (rng.random(R) < 0.1)
    rR = np.where(rng.random(R) < 0.3, rng.gamma(1, 25, R), 0)
    m, Rr, lam = JM.reconcile(r, rng.uniform(20, 60, n), act, dur, rR, np.full(R, 40.0))
    assert np.abs(m.sum(axis=1) + Rr - 5 * dur).max() <= 1e-9
    assert (m >= 0).all() and (m <= dur[:, None] + 1e-12).all() and (m[~act] == 0).all() and (Rr >= 0).all()
    k = act.sum(axis=1)
    assert (Rr[k < 5] >= (5 - k[k < 5]) * dur[k < 5] - 1e-9).all()


def test_reconciliation_kkt_exact():
    rng = np.random.default_rng(4)
    R, n = 3000, 12
    r, w = rng.uniform(0, 40, (R, n)), rng.uniform(20, 60, n)
    act = np.ones((R, n), bool)
    m, Rr, lam = JM.reconcile(r, w, act, np.full(R, 40.0), np.zeros(R), np.full(R, 1.0))
    free = (m > 1e-9) & (m < 40 - 1e-9)
    assert np.abs((m - r - lam[:, None] * w)[free]).max() < 1e-9


def test_reliability_weighting():
    rng = np.random.default_rng(5)
    R, n = 4000, 10
    r = rng.uniform(5, 35, (R, n))
    w = np.full(n, 40.0)
    w[0] = 4.0                                                             # established forecast: expensive to move
    m, _, _ = JM.reconcile(r, w, np.ones((R, n), bool), np.full(R, 40.0), np.zeros(R), np.full(R, 1.0))
    mv = np.abs(m - r).mean(axis=0)
    assert mv[0] < 0.2 * mv[1:].mean()


def test_shared_duration_overtime_and_pace_clock_separation():
    g = _game()
    st = JM.simulate_jm(g, ENV, PARAMS, 20000)
    assert set(np.unique(st.dur)) <= {40.0, 45.0, 50.0}
    assert abs((st.dur > 40).mean() - 0.06) < 0.01
    g2 = _game()
    g2.env = np.array([[0.3, 0.2, -0.1], [0.3, -0.1, 0.2]])                 # pace / efficiency change only the stat means
    st2 = JM.simulate_jm(g2, ENV, PARAMS, 20000)
    assert np.array_equal(st.dur, st2.dur) and np.array_equal(st.M, st2.M)
    assert st.checks["team_minutes_violations"] == 0 and st.diag["sigma1_max_rel"] < 1e-9


# ---------------------------------------------------------------- exposure / combos
def test_one_minutes_draw_feeds_all_seven_stats():
    sims, n = 50, 3
    base = np.log(np.full((n, S.NS), 5.0))
    M = np.full((sims, n), 20.0)
    glm = {"stats": {s: {"coef": {t: 0.0 for t in S.GLM_TERMS} | {"log_minutes_ratio": 1.0}} for s in S.STATS}}
    st = S.State(np.full(sims, 40.0), np.zeros((sims, 2, 3)), M, np.zeros((sims, n, 4)), np.zeros((sims, 0)), {},
                 np.zeros((n, 4)), 0.0)
    a = S.log_mu_terms(base, np.full(n, 20.0), np.zeros(n, int), st, glm, JM.JM_TERMS)
    M2 = M.copy()
    M2[7, 1] = 30.0
    st.M = M2
    b = S.log_mu_terms(base, np.full(n, 20.0), np.zeros(n, int), st, glm, JM.JM_TERMS)
    d = b - a
    assert np.allclose(d[7, 1], np.log(1.5)) and np.count_nonzero(d) == S.NS        # all 7 stats, that draw, that player


def test_same_draw_combos():
    x = np.random.default_rng(0).integers(0, 9, (40, 3, J.NS)).astype(np.int16)
    der = J.derived_markets(x)
    for c, parts in J.COMBOS.items():
        assert np.array_equal(der[c], x[:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2))


def test_normal_score_is_standard_normal_under_phase1b():
    mu, sg = np.array([25.0]), np.array([6.5])
    pm = P.minutes_pmf(mu, sg, JM.KMAX)[0]
    k = np.random.default_rng(0).choice(JM.KMAX + 1, size=200000, p=pm)
    z = JM.normal_score(k[:, None].astype(float), JM.pmf_cdf(mu, sg))[:, 0]
    assert abs(z.mean()) < 0.02 and abs(z.std() - 1) < 0.05
