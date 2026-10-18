"""V2 Phase 2 (cbb_platform/v2 latent / dependence / joint / phase2): the joint layer is reproducible, PSD by
construction and marginal-preserving; every combination market is an exact sum of the same draws; fits read only
earlier OOS seasons; 2026-27 is never read; the v1 and Phase 1B marginal artifacts are unchanged."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess

import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.v2 import dependence as D, joint as J, latent as L, phase2 as Z, provenance as PV


@pytest.fixture(scope="module")
def pop():
    p, _ = D.load_population(Z.STORE, [2024])
    games = sorted(p["game_id"].unique())[:400]
    return p[p["game_id"].isin(games)].reset_index(drop=True)


@pytest.fixture(scope="module")
def fitted(pop):
    return D.fit(pop, "MID", boot=60)


@pytest.fixture(scope="module")
def sts(fitted):
    return {c: D.structure(c, fitted if c != "J0" else None) for c in ("J0", "J1", "J2")}


def _game(pop, i=0):
    g = sorted(pop["game_id"].unique())[i]
    return pop[pop["game_id"] == g]


def _team(gp):
    return (gp["team_id"] != gp["team_id"].min()).astype(int).to_numpy()


# ---------------------------------------------------------------- artifacts unchanged
def test_v1_and_phase1b_marginal_artifacts_unchanged():
    assert hashlib.sha256(PV.V1_ARTIFACT.read_bytes()).hexdigest() == Z.V1_SHA
    assert hashlib.sha256(Z.MARGINAL_PATH.read_bytes()).hexdigest() == Z.MARGINAL_SHA
    g = Z.guards()
    assert g["V1_ARTIFACT_UNCHANGED"] and g["V2_MARGINAL_ARTIFACT_UNCHANGED"] and g["OOS_STORE_UNCHANGED"]


# ---------------------------------------------------------------- latent transforms
def test_ndtri_matches_stdlib():
    import statistics
    N = statistics.NormalDist()
    p = np.concatenate([np.linspace(1e-12, 1 - 1e-12, 4001), [1e-300, 1e-20, 0.075, 0.925]])
    assert np.abs(L.ndtri(p) - np.array([N.inv_cdf(x) for x in p])).max() < 1e-13
    assert L.ndtri(np.array([0.0, 1.0])).tolist() == [-np.inf, np.inf]


def test_first_order_deattenuation_recovers_known_latent_correlation(pop):
    """Simulate counts from a known latent correlation on real marginals; the MID and RAND estimators recover it."""
    rng = np.random.default_rng(3)
    R = np.full((7, 7), 0.25)
    R[0, 3] = R[3, 0] = 0.7
    np.fill_diagonal(R, 1.0)
    Z0 = rng.standard_normal((len(pop), 7)) @ np.linalg.cholesky(R).T
    sim = pop.copy()
    for j, s in enumerate(L.STATS):
        cdf = L.cdf_table(pop[f"{s}_mu"].to_numpy(), float(pop[f"{s}_phi"].iloc[0]))
        with np.errstate(divide="ignore"):
            thr = np.where(cdf >= 1, np.inf, L.ndtri(np.clip(cdf, 1e-300, 1)))
        sim[f"y_{s}"] = (thr < Z0[:, [j]]).sum(axis=1)
    for kind in ("MID", "RAND"):
        f = D.fit(sim, kind, boot=30)
        est = np.asarray(f["within_player"]["global"]["raw"])
        se = np.asarray(f["within_player"]["global"]["se"])
        iu = np.triu_indices(7, 1)
        assert np.all(np.abs(est[iu] - R[iu]) <= 4 * se[iu] + 0.02)        # sampling noise + 2nd-order bias


# ---------------------------------------------------------------- construction
def test_all_game_correlation_matrices_psd_unit_diagonal_and_pinned(pop, sts):
    for i in range(40):
        gp = _game(pop, i)
        groups, team = list(D.group_of(gp)), _team(gp)
        for c, st in sts.items():
            gc = D.game_correlation(st, groups, team)
            assert not gc["invalid"] and gc["min_eig"] >= D.PSD_FLOOR
            assert np.array_equal(np.diag(gc["corr"]), np.ones(len(gc["corr"]))) and gc["diag_err"] <= 1e-12
            Lc, _ = D.cholesky_unit(gc["corr"])
            assert np.allclose((Lc ** 2).sum(axis=1), 1.0, atol=1e-14)
            if c == "J0":
                assert np.array_equal(gc["corr"], np.eye(len(gc["corr"])))
            else:                                      # every player's own block is exactly its J1 matrix
                for p, g in enumerate(groups):
                    blk = gc["corr"][p * 7:(p + 1) * 7, p * 7:(p + 1) * 7]
                    assert np.allclose(blk, st.R(g), atol=1e-12)
            if c == "J1":                              # no cross-player structure
                for p in range(len(groups)):
                    off = gc["corr"][p * 7:(p + 1) * 7].copy()
                    off[:, p * 7:(p + 1) * 7] = 0
                    assert not off.any()


def test_j2_cross_blocks_follow_shared_grams(pop, sts):
    st = sts["J2"]
    gp = _game(pop, 1)
    groups, team = list(D.group_of(gp)), _team(gp)
    gc = D.game_correlation(st, groups, team)
    AB, AmB, QQ = st.A @ st.A.T + st.B @ st.B.T, st.A @ st.A.T - st.B @ st.B.T, st.Q @ st.Q.T
    i, j = np.flatnonzero(team == 0)[:2]
    k = np.flatnonzero(team == 1)[0]
    same = gc["corr"][i * 7:(i + 1) * 7, j * 7:(j + 1) * 7]
    assert np.allclose(same, AB - gc["c_q"][i] * gc["c_q"][j] * QQ, atol=1e-12)
    cross = gc["corr"][i * 7:(i + 1) * 7, k * 7:(k + 1) * 7]
    assert np.allclose(cross, gc["c_ab"][i] * gc["c_ab"][k] * AmB, atol=1e-12)


# ---------------------------------------------------------------- simulator
def test_same_seed_identical_draws(pop, sts):
    gp = _game(pop, 2)
    a, _ = J.simulate(gp, sts, sims=3000)
    b, _ = J.simulate(gp, sts, sims=3000)
    for c in sts:
        assert np.array_equal(a[c], b[c])
    other, _ = J.simulate(gp, sts, sims=3000, seed_base=J.SIM_SEED + 1)
    assert not np.array_equal(a["J2"], other["J2"])


def test_threshold_route_equals_uniform_inverse_cdf_route(pop):
    gp = _game(pop, 3)
    marg = J.game_marginals(gp)
    z = np.random.default_rng(5).standard_normal((2000, len(gp) * 7))
    x = J.counts_from_latent(z, marg["thr"])
    u = 0.5 * np.vectorize(math.erfc)(-z / math.sqrt(2))
    cdf = marg["cdf"].reshape(-1, marg["cdf"].shape[-1])
    ref = np.stack([np.searchsorted(cdf[c], u[:, c], side="left") for c in range(z.shape[1])], axis=1)
    assert np.mean(x == ref) > 0.9999                  # equal up to float ties at CDF steps (measure ~1e-16)


def test_j0_is_independent(pop, sts):
    gp = _game(pop, 4)
    d, info = J.simulate(gp, {"J0": sts["J0"]}, sims=40000)
    x = d["J0"].reshape(40000, -1).astype(float)
    keep = x.std(axis=0) > 0
    c = np.corrcoef(x[:, keep], rowvar=False)
    off = c[~np.eye(len(c), dtype=bool)]
    assert np.abs(off).max() < 5.5 / np.sqrt(40000)   # every pair consistent with zero correlation


def test_marginal_pmfs_preserved_under_j2(pop, sts):
    gp = _game(pop, 5)
    sims = 60000
    d, _ = J.simulate(gp, {"J2": sts["J2"]}, sims=sims)
    marg = J.game_marginals(gp)
    x = d["J2"]
    for p in range(len(gp)):
        for j in range(7):
            cnt = np.bincount(x[:, p, j], minlength=L.KMAX + 1)[:L.KMAX + 1]
            ex = sims * marg["pmf"][p, j]
            assert np.all(np.abs(cnt - ex) <= 5.5 * np.sqrt(ex) + 3)           # every PMF cell within MC error
    pz = J.marginal_probes(x, marg)
    assert np.abs(pz["z_mean"]).max() < 5.5


def test_combination_markets_are_exact_sums_of_the_same_draws(pop, sts):
    gp = _game(pop, 6)
    d, _ = J.simulate(gp, sts, sims=5000)
    for c, x in d.items():
        der = J.derived_markets(x)
        pts, reb, ast, stl, blk = (x[:, :, J.SI[s]].astype(np.int64) for s in ("PTS", "REB", "AST", "STL", "BLK"))
        assert np.array_equal(der["PRA"], pts + reb + ast)
        assert np.array_equal(der["PR"], pts + reb)
        assert np.array_equal(der["PA"], pts + ast)
        assert np.array_equal(der["RA"], reb + ast)
        assert np.array_equal(der["STOCKS"], stl + blk)


def test_joint_draws_carry_the_fitted_within_player_dependence(pop, sts):
    gp = _game(pop, 7)
    d, _ = J.simulate(gp, {"J0": sts["J0"], "J1": sts["J1"]}, sims=20000)
    top = int(np.argmax(gp["PTS_mu"].to_numpy()))
    r0 = np.corrcoef(d["J0"][:, top, 0], d["J0"][:, top, 3])[0, 1]
    r1 = np.corrcoef(d["J1"][:, top, 0], d["J1"][:, top, 3])[0, 1]
    assert abs(r0) < 0.04 and r1 > 0.3                 # PTS <-> FG3M


# ---------------------------------------------------------------- data discipline
def test_population_guard_refuses_other_seasons_and_store_has_no_2026_27():
    for bad in ([2027], [2025, 2027], [2023]):
        with pytest.raises(D.V2Phase2DataViolation):
            D.load_population(Z.STORE, bad)
    s = pd.read_parquet(Z.STORE, columns=["forecast_season", "training_through_season"])
    assert set(s["forecast_season"]) == {2024, 2025, 2026}
    assert (s["training_through_season"] < s["forecast_season"]).all()


def test_population_excludes_non_playing_rows_and_uses_no_p_play():
    p, acc = D.load_population(Z.STORE, [2024])
    assert set(p["participation_status"]) == {"PLAYED"}
    assert not p[[f"y_{s}" for s in L.STATS]].isna().any().any()
    assert (p.groupby("game_id")["team_id"].nunique() == 2).all()
    assert acc["p_play_used"] is False and p["p_play"].isna().all()         # 2023-24: P(play) not estimable, untouched


def test_fit_reads_only_its_population(pop, fitted):
    assert fitted["fit_seasons"] == [2024] and fitted["fit_player_games"] == len(pop)
    other = pop.copy()
    other["y_PTS"] = other["y_PTS"].sample(frac=1.0, random_state=1).to_numpy()
    assert D.fit(other, "MID", boot=60)["within_player"]["global"]["raw"] != fitted["within_player"]["global"]["raw"]


def test_multi_season_residuals_use_each_rows_own_dispersion(pop):
    p2, _ = D.load_population(Z.STORE, [2025])
    b = p2[p2["game_id"].isin(sorted(p2["game_id"].unique())[:100])].reset_index(drop=True)
    both = pd.concat([pop, b], ignore_index=True)
    assert both["FG3M_phi"].nunique() == 2                                   # store dispersion differs by forecast season
    for kind in ("MID", "RAND"):
        ra, rb, rr = D.residuals(pop, kind), D.residuals(b, kind), D.residuals(both, kind)
        for k in ("zc", "vz", "kappa"):
            assert np.array_equal(rr[k], np.vstack([ra[k], rb[k]]))


def test_recorded_stage_order_and_seasons():
    if not Z.SELECTION_PATH.exists():
        pytest.skip("selection stage not run")
    prot = json.loads(Z.PROTOCOL_PATH.read_text())
    assert prot["frozen_before_any_candidate_evaluation"] and prot["inputs"]["MARGINAL_ARTIFACT_SHA256"] == Z.MARGINAL_SHA
    sel = json.loads(Z.SELECTION_PATH.read_text())
    fit = json.loads(Z.SEL_FIT_PATH.read_text())
    assert sel["protocol_sha256"] == hashlib.sha256(Z.PROTOCOL_PATH.read_bytes()).hexdigest()
    assert fit["MID"]["fit_seasons"] == [2024] and sel["evaluation_season"] == 2025
    assert sel["season_2025_26_loaded"] is False and 2026 not in sel["seasons_loaded"]
    assert sel["SELECTED_DEPENDENCE_MODEL"] in Z.CANDIDATES
    r = subprocess.run(["git", "merge-base", "--is-ancestor", sel["protocol_commit"], "HEAD"], cwd=Z.C.PROJECT_ROOT)
    assert r.returncode == 0
    if Z.CONFIRM_PATH.exists():
        conf = json.loads(Z.CONFIRM_PATH.read_text())
        cfit = json.loads(Z.CONFIRM_FIT_PATH.read_text())
        assert cfit["MID"]["fit_seasons"] == [2024, 2025] and conf["evaluation_season"] == 2026
        assert conf["selected_dependence_model"] == sel["SELECTED_DEPENDENCE_MODEL"]
        assert conf["selection_record_sha256"] == hashlib.sha256(Z.SELECTION_PATH.read_bytes()).hexdigest()
        r = subprocess.run(["git", "merge-base", "--is-ancestor", conf["selection_record_commit"], "HEAD"],
                           cwd=Z.C.PROJECT_ROOT)
        assert r.returncode == 0
        for summ in (sel["summary"], conf["summary"]):
            for c in Z.CANDIDATES:
                assert summ["construction"][c]["INVALID_CORRELATION_MATRICES"] == 0


def test_final_dependence_artifact():
    if not Z.VERIFY_PATH.exists():
        pytest.skip("final stage not run")
    v = json.loads(Z.VERIFY_PATH.read_text())
    path = Z.C.PROJECT_ROOT / v["artifact"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == v["artifact_sha256"]
    a = json.loads(path.read_text())
    assert a["MARGINAL_ARTIFACT_SHA256"] == Z.MARGINAL_SHA and a["fit"]["seasons"] == [2024, 2025, 2026]
    assert a["SELECTED_DEPENDENCE_MODEL"] == json.loads(Z.SELECTION_PATH.read_text())["SELECTED_DEPENDENCE_MODEL"]
    st = Z.load_artifact(path)
    assert v["final_structure_check_last_200_games_2025_26"]["invalid"] == 0
    assert v["V1_ARTIFACT_UNCHANGED"] and v["V2_MARGINAL_ARTIFACT_UNCHANGED"] and not v["2026_27_OUTCOME_DATA_TOUCHED"]
    assert st.name == a["SELECTED_DEPENDENCE_MODEL"]
