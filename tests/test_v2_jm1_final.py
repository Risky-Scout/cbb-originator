"""Focused tests for the CBB V2 JM1_FINAL candidate (cbb_platform/v2/jm1_final.py)."""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.v2 import joint as J, jm1_final as F, joint_minutes_v2 as JM, structural as S

ENV = {"overtime": {"p": [0.94, 0.05, 0.01]}, "sd_pace": 0.07, "sd_eff": 0.14, "rho_eff": 0.19}
E_DUR = JM.expected_duration(ENV["overtime"]["p"])


def _gi(n_per_team=10, gid="G-jm1-test", mu=None):
    n = 2 * n_per_team
    team = np.repeat([0, 1], n_per_team)
    mu = np.tile(np.concatenate([np.full(5, 30.0), np.full(3, 18.0), np.full(n_per_team - 8, 6.0)]), 2) if mu is None else mu
    scored = np.zeros(n, bool)
    scored[[0, 1, 5, 9, n_per_team, n_per_team + 6]] = True
    return S.GameInput(gid, team, scored, np.full(n, 0.8), mu, np.full(n, 6.5), np.ones((n, len(S.FAM))), np.zeros((2, 3)))


# ---------------------------------------------------------------- chronology / protection
def test_never_reads_2026_27_and_confirmation_chronology():
    with pytest.raises(F.JM1Violation):
        F.check_seasons([2027])
    assert F.CONFIRM == {"T": 2025, "eval": 2026, "resid_seasons": (2024, 2025)}
    assert max(F.CONFIRM["resid_seasons"]) <= F.CONFIRM["T"] < F.CONFIRM["eval"]
    assert F.DEV == JM.DEV


def test_protected_artifacts_and_user_qa_file_unchanged():
    g = F.guards()
    assert g["V2_PHASE1B_ARTIFACT_UNCHANGED"] and g["PHASE2_J2_ARTIFACT_UNCHANGED"]
    assert all(g[f"{k}_UNCHANGED"] for k in F.JM2_FROZEN)
    assert hashlib.sha256(JM.USER_QA_FILE.read_bytes()).hexdigest() == JM.USER_QA_SHA


# ---------------------------------------------------------------- the frozen JM1 mechanism
def test_exposure_is_bit_identical_to_the_jm1_development_arm():
    gi = _gi()
    n = len(gi.team)
    jmi = JM.JMInput(gi.game_id, gi.team, gi.scored, gi.p_play, gi.min_mu, gi.min_sigma, np.zeros(n, int),
                     np.full(n, 40.0), gi.env, np.zeros(2), np.zeros(2), np.full(2, 10.0), np.ones(2))
    prm = {"e_dur": E_DUR, "reservoir": {"cv2_pos": 0.9}, "tier_factor": np.zeros((3, 3))}
    st = JM.simulate_jm(jmi, ENV, prm, 500, diagnostics=False)
    dur, x, M = F.simulate_exposure(gi, ENV, E_DUR, 500)
    assert np.array_equal(M, st.M1) and np.array_equal(dur, st.dur) and np.array_equal(x, st.x)


def test_no_team_budget_no_reconciliation_independent_players():
    gi = _gi()
    dur, _, M = F.simulate_exposure(gi, ENV, E_DUR, 4000)
    sc = np.flatnonzero(gi.scored)
    team0 = gi.team[sc] == 0
    assert M[:, team0].sum(axis=1).std() > 5.0                      # no exact team total
    mu2 = gi.min_mu.copy()
    mu2[2] = 5.0                                                    # an unscored teammate changes ...
    mu2[1] = 35.0                                                   # ... and a scored teammate changes
    _, _, M2 = F.simulate_exposure(_gi(mu=mu2), ENV, E_DUR, 4000)
    keep = [k for k, i in enumerate(sc) if i != 1]
    assert np.array_equal(M[:, keep], M2[:, keep])                   # other players' draws are untouched
    c = np.corrcoef(M[:, 0], M[:, 1])[0, 1]
    assert abs(c) < 0.06                                            # independent apart from the shared duration


def test_exposure_is_the_phase1b_conditional_marginal_scaled_by_duration():
    gi = _gi()
    dur, _, M = F.simulate_exposure(gi, ENV, E_DUR, 40000)
    assert (M >= 0).all() and (M <= JM.KMAX * dur.max() / E_DUR + 1e-9).all()
    reg = dur == S.REGULATION
    k = np.clip(np.round(M[reg, 0] * E_DUR / S.REGULATION), 0, JM.KMAX).astype(int)
    pm = P.minutes_pmf(gi.min_mu[:1], gi.min_sigma[:1], JM.KMAX)[0]
    emp = np.bincount(k, minlength=JM.KMAX + 1) / len(k)
    assert np.abs(emp - pm).max() < 0.01
    assert abs(M[:, 0].mean() - (pm @ np.arange(JM.KMAX + 1))) < 0.15


def test_shared_duration_and_pace_never_changes_the_clock():
    gi = _gi()
    d1, x1, _ = F.simulate_exposure(gi, ENV, E_DUR, 3000)
    gi2 = _gi()
    gi2.env[:] = np.array([[0.3, 0.1, -0.1], [0.3, -0.1, 0.1]])
    d2, x2, _ = F.simulate_exposure(gi2, ENV, E_DUR, 3000)
    assert np.array_equal(d1, d2) and not np.array_equal(x1, x2)
    assert set(np.unique(d1)) <= {40.0, 45.0, 50.0}


def test_deterministic():
    gi = _gi()
    a, b = F.simulate_exposure(gi, ENV, E_DUR, 1000), F.simulate_exposure(gi, ENV, E_DUR, 1000)
    assert all(np.array_equal(u, v) for u, v in zip(a, b))


def test_one_minutes_draw_feeds_all_seven_stats_and_combos_same_draw():
    sims, n = 50, 3
    base = np.log(np.full((n, S.NS), 5.0))
    M = np.full((sims, n), 20.0)
    glm = {"stats": {s: {"coef": {t: 0.0 for t in S.GLM_TERMS} | {"log_minutes_ratio": 1.0}} for s in S.STATS}}
    st = S.State(np.full(sims, 40.0), np.zeros((sims, 2, 3)), M, np.zeros((sims, n, 4)), np.zeros((sims, 0)), {},
                 np.zeros((n, 4)), 0.0)
    a = S.log_mu_terms(base, np.full(n, 20.0), np.zeros(n, int), st, glm, JM.JM_TERMS)
    st.M = M.copy()
    st.M[7, 1] = 30.0
    d = S.log_mu_terms(base, np.full(n, 20.0), np.zeros(n, int), st, glm, JM.JM_TERMS) - a
    assert np.allclose(d[7, 1], np.log(1.5)) and np.count_nonzero(d) == S.NS
    assert "opportunity_deficit" not in JM.JM_TERMS and JM.JM1_ALPHA == 1.0
    x = np.random.default_rng(0).integers(0, 9, (40, 3, J.NS)).astype(np.int16)
    der = J.derived_markets(x)
    for c, parts in J.COMBOS.items():
        assert np.array_equal(der[c], x[:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2))


# ---------------------------------------------------------------- frozen gates
def _summ(v=-0.01, hi=-0.005, lam_c=1.1, lam_s=1.3, brier=-0.001, gap_c=0.05, gap_s=0.07):
    ci = {"value": v, "lo": v - 0.01, "hi": hi}
    strata = ["ALL"] + [f"ROLE:{r}" for r in F.TIERS] + [f"POS:{p}" for p in F.POSITIONS_GATED]
    keys = ["MARG_LOG", "MARG_CRPS_SCALED"] + [f"MARG_{m}|{s}" for m in ("LOG", "CRPS_SCALED") for s in S.STATS]
    d = {st: {k: dict(ci) for k in keys} for st in strata}
    lam = {f"ROLE:{r}": {"S0": {"PTS": {"lam": lam_s}}, "X": {"PTS": {"lam": lam_c}}} for r in F.TIERS}
    ev = {"TM_SCORER_SCORER": {"observed_rate": 0.3, "S0": {"mean_p": 0.23, "calibration_gap_obs_minus_pred": gap_s},
                               "X": {"mean_p": 0.25, "calibration_gap_obs_minus_pred": gap_c,
                                     "brier_minus_S0": {"value": brier}}}}
    return {"deltas": {"X-S0": d}, "joint_deltas": {"X-S0": {"ES": dict(ci), "VS_BAL": {"value": -1e-3, "lo": -2e-3, "hi": -9e-4}}},
            "minutes_deltas": {"X-S0": {st: {"MIN_CRPS": {"value": 0.001, "lo": 0.0, "hi": 0.002}} for st in strata}},
            "mc_se": {"X-S0|ES": 1e-4, "X-S0|VS_BAL": 1e-5}, "latent_scale_lam": lam, "events_by_family": ev}


EXTRA = {"env_pit": {"violations": 0}, "determinism": {"identical": True}, "chronology": {"OK": True}}
ACC = {"COMBO_IDENTITY_VIOLATIONS": 0, "INVALID_CORRELATION_MATRICES": {"X": 0}}


def test_gates_pass_only_when_every_bound_clears():
    assert F.gates(_summ(), "X", ACC, EXTRA)["ALL_PASS"]
    assert not F.gates(_summ(hi=0.0001), "X", ACC, EXTRA)["ALL_PASS"]             # inconclusive superiority = fail
    assert not F.gates(_summ(lam_c=1.4), "X", ACC, EXTRA)["C6_pts_spread_bench_rotation"]["PASS"]
    assert not F.gates(_summ(gap_c=0.08), "X", ACC, EXTRA)["C8_both_top_scorers_over"]["PASS"]
    assert not F.gates(_summ(), "X", ACC, EXTRA | {"chronology": {"OK": False}})["ALL_PASS"]
    s = _summ()
    s["deltas"]["X-S0"]["POS:C"]["MARG_LOG|FG3M"] = {"value": 0.003, "lo": -0.001, "hi": 0.006}
    g = F.gates(s, "X", ACC, EXTRA)
    assert g["C4_stat_x_position_non_inferior"]["failures"] == ["FG3M|C"] and not g["ALL_PASS"]
