"""CBB v1 final pre-lockbox candidate (props_v1_refit final section): exactly one accepted change (STL Poisson -> the
existing props_v0 Negative Binomial, phi = 0.16) carried by the artifact; every other state, mean, PMF and the
participation model identical to the frozen c123a3b refit; no rejected challenger imported; loads and prices without
refitting; deterministic; reproduces the accepted STL evidence; never touches 2025-26."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys

import duckdb
import numpy as np
import pytest

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_participation as Q, props_v1_pricing as V
from cbb_platform import lockbox_state as S, props_v1_refit as R

BASE_COMMIT = "c123a3bf0ac615c6d7f864768f5e77dc18405a8d"
V0_FILES = ["cbb_platform/props_v0.py", "cbb_platform/props_v0_eval.py", "cbb_platform/props_v0_participation.py",
            "modeling/player_props_v0/config.json", "modeling/player_props_v0/metrics.json",
            "modeling/player_props_v0/participation_metrics.json"]
REJECTED = ("props_v1_fg3m_dispersion_challenger", "props_v1_pts_mean_challenger", "props_v1_tov_mean_challenger",
            "props_v1_participation_calibration_challenger", "props_v1_minutes_role_challenger")
CHALLENGERS = REJECTED + ("props_v1_stl_nb_challenger", "props_v1_lowcount_audit", "props_v1_nightly_replay")


@pytest.fixture(scope="module")
def final():
    return R.load_final()


@pytest.fixture(scope="module")
def base():
    return R.load(R.ARTIFACT_PATH)


@pytest.fixture(scope="module")
def sample(final, base):
    cand = V.load_candidates()
    cand = cand[cand["canonical_game_id"].isin(V.sample_games(cand))]
    part_rows = R.scoring_model(final)["part_rows"]
    rf = Q.pregame_outputs(cand, part_rows, final["config"], final["v0_state"], final["participation_state"])
    rb = Q.pregame_outputs(cand, part_rows, base["config"], base["v0_state"], base["participation_state"])
    return {"cand": cand, "final": rf, "base": rb}


def test_2025_26_blocked(final, tmp_path):
    with pytest.raises(lockbox.LockboxViolation):
        lockbox.check_season(lockbox.LOCKBOX_SEASON)
    with pytest.raises(lockbox.LockboxViolation):
        P.load_frames(P.load_config(), seasons=[lockbox.LOCKBOX_SEASON])
    with pytest.raises(lockbox.LockboxViolation):
        V.load_candidates(lockbox.LOCKBOX_SEASON)
    bad = dict(final, training_through_season=lockbox.LOCKBOX_SEASON)
    bad["artifact_fingerprint"] = R.fingerprint(bad)
    R.save(bad, tmp_path / "bad.json")
    with pytest.raises(lockbox.LockboxViolation):
        R.load_final(tmp_path / "bad.json")


def test_spec_has_exactly_one_accepted_change(final):
    spec = R.load_final_spec()
    assert list(spec["accepted_changes"]) == ["STL"] and spec["rejected_changes_activated"] == []
    assert spec["accepted_changes"]["STL"]["family"] == "negative_binomial" and spec["accepted_changes"]["STL"]["mean_changed"] is False
    assert spec["base_commit"] == BASE_COMMIT and spec["lockbox_touched"] is False and spec["training_through_season"] == 2025
    assert final["final_spec"] == spec and final["final_spec_fingerprint"] == R.final_spec_fingerprint(spec)
    assert list(final["distribution_overrides"]) == ["STL"]
    with pytest.raises(ValueError):
        R.apply_accepted_changes(final["v0_state"], dict(spec, rejected_changes_activated=["TOV mean"]), final["config"])


def test_rejected_challengers_not_imported_by_runtime():
    for mod in ("props_v1_refit", "props_v1_pricing", "props_v0", "props_v0_participation", "props_v0_eval"):
        tree = ast.parse((C.PROJECT_ROOT / "cbb_platform" / f"{mod}.py").read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(c in x for x in names for c in CHALLENGERS), mod
    code = ("import sys; from cbb_platform import props_v1_refit as R; R.load_final(); "
            "print(sorted(m for m in sys.modules if 'challenger' in m or 'lowcount' in m or 'nightly' in m))")
    out = subprocess.run([sys.executable, "-c", code], cwd=C.PROJECT_ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_props_v0_historical_baseline_unchanged():
    if S.current() == S.PRE_LOCKBOX_READINESS:
        assert subprocess.run(["git", "diff", "--quiet", BASE_COMMIT, "--", *V0_FILES], cwd=C.PROJECT_ROOT).returncode == 0
    else:   # unchanged through readiness d65aac9; since then only the reviewed scoring-enable window gating, byte for byte
        assert S.unchanged_since_readiness_except_reviewed_patches(BASE_COMMIT, V0_FILES)
    assert R.ARTIFACT_PATH.exists() and hashlib.sha256(R.ARTIFACT_PATH.read_bytes()).hexdigest() == \
        R.load_final_spec()["base_artifact_sha256"]


def test_stl_mean_unchanged(sample):
    assert sample["final"]["table"]["STL_mean_given_play"].equals(sample["base"]["table"]["STL_mean_given_play"])


def test_stl_family_negative_binomial_phi_exactly_016(final):
    d = final["v0_state"]["dispersion"]["stl"]
    assert d["family"] == "NEGBIN" and type(d["phi"]) is float and d["phi"] == 0.16 and repr(d["phi"]) == "0.16"
    assert final["distribution_overrides"]["STL"] == {"target": "stl", "family": "negative_binomial", "phi": 0.16,
                                                     "fitted_family": "POISSON", "fitted_phi": 0.0}


def test_existing_negative_binomial_implementation_reused(final, sample):
    K = final["config"]["pmf_support_max"]
    mu = sample["final"]["table"]["STL_mean_given_play"].to_numpy()
    assert np.array_equal(sample["final"]["conditional_stat_distribution"]["STL"], P.pmf_matrix(mu, 0.16, K))
    assert not np.array_equal(sample["final"]["conditional_stat_distribution"]["STL"], P.pmf_matrix(mu, 0.0, K))
    src = (C.PROJECT_ROOT / "cbb_platform" / "props_v1_refit.py").read_text()
    assert "lgamma" not in src and "gammaln" not in src and "1.0 / phi" not in src


def test_non_stl_families_dispersions_and_pmfs_unchanged(final, base, sample):
    for t, d in base["v0_state"]["dispersion"].items():
        if t != "stl":
            assert final["v0_state"]["dispersion"][t] == d, t
    for name, pmf in sample["base"]["conditional_stat_distribution"].items():
        if name != "STL":
            assert np.array_equal(pmf, sample["final"]["conditional_stat_distribution"][name]), name
    for col in sample["base"]["table"].columns:
        assert sample["base"]["table"][col].equals(sample["final"]["table"][col]), col


def test_participation_model_unchanged(final, base, sample):
    assert final["participation_state"] == base["participation_state"]
    assert final["coef_order"]["participation"] == base["coef_order"]["participation"]
    assert final["participation_training_seasons"] == base["participation_training_seasons"]
    assert sample["final"]["table"]["participation_probability"].equals(sample["base"]["table"]["participation_probability"])


def test_minutes_model_unchanged(final, base, sample):
    for k in ("coef", "transform", "minutes_sigma", "prior_rate", "prior_k_minutes", "baseline", "train_through",
              "n_train_rows", "train_max_cutoff", "state_hash"):
        assert final["v0_state"][k] == base["v0_state"][k], k
    assert list(final["v0_state"]["coef"]) == final["coef_order"]["v0"] == base["coef_order"]["v0"]
    assert np.array_equal(sample["final"]["conditional_stat_distribution"]["MIN"], sample["base"]["conditional_stat_distribution"]["MIN"])


def test_final_artifact_loads_and_scores_without_refit_deterministically():
    runs = [subprocess.run([sys.executable, "-c", R.FRESH_SCORE], cwd=C.PROJECT_ROOT, capture_output=True, text=True)
            for _ in range(2)]
    assert all(r.returncode == 0 for r in runs), runs[0].stderr[-2000:]
    a, b = (json.loads(r.stdout.strip().splitlines()[-1]) for r in runs)
    assert a == b and a["fit_calls"] == 0 and a["loaded"] and a["rows"] > 0 and a["stl_phi"] == 0.16
    assert a["families"] == {"pts": "NEGBIN", "reb": "NEGBIN", "ast": "NEGBIN", "three_pm": "NEGBIN", "stl": "NEGBIN",
                             "blk": "NEGBIN", "tov": "POISSON"}
    assert a["max_pmf_row_sum_error"] <= 1e-12 and a["settle_max_error"] <= 1e-12
    assert a["model_version"] == [R.load_final()["model_version"]]


def test_final_artifact_deterministic():
    art = R.build_final_artifact()
    assert R.dumps(art) == R.FINAL_ARTIFACT_PATH.read_text()
    assert R.fingerprint(art) == art["artifact_fingerprint"]


def test_pmfs_normalize_and_pricing_coherent(final, sample):
    for name, pmf in sample["final"]["conditional_stat_distribution"].items():
        assert np.abs(pmf.sum(axis=1) - 1).max() <= 1e-12 and pmf.min() >= 0, name
        if name != "MIN":
            assert R._coherence(pmf)["pass"], name
    out = R.score_final(sample["cand"], final)
    assert np.allclose(out["over_probability"] + out["under_probability"] + out["push_probability"], 1.0, atol=1e-12)
    half = (out["line"] % 1) == 0.5
    assert (out.loc[half, "push_probability"] == 0).all()
    assert (out["model_version"] == final["model_version"]).all()


def test_reproduces_accepted_stl_evidence():
    cfg = P.load_config()
    spec = R.load_final_spec()
    frame, finals, _ = P.load_frames(cfg)
    st = P.fit(frame, finals, cfg, 2024)
    fs = R.apply_accepted_changes(st, spec, cfg)
    va = frame[frame["season"] == 2025]
    y = va["y_stl"].to_numpy(dtype=float); ok = ~np.isnan(y)
    mu_b, mu_f = P.predict(va, cfg, st)["challenger"]["stl"][ok], P.predict(va, cfg, fs)["challenger"]["stl"][ok]
    assert np.array_equal(mu_b, mu_f)
    m, _ = R._target_metrics(mu_f, y[ok], "stl", fs, cfg, True)
    ch = json.loads((R.ARTIFACT_PATH.parent / "stl_nb_challenger.json").read_text())
    assert ch["decision"] == "ACCEPT" and all(ch["acceptance"].values())
    assert R._max_leaf_diff(m, ch["forward_validation"]["challenger"]) <= 1e-10
    v = json.loads(R.FINAL_VALIDATION_PATH.read_text())
    assert v["stl_accepted_evidence"]["reproduced"] and all(v["stl_accepted_evidence"]["acceptance_rerun"].values())
    assert v["candidate_artifact_sha256"] == hashlib.sha256(R.FINAL_ARTIFACT_PATH.read_bytes()).hexdigest()
    assert v["non_stl_identity"]["pass"] and v["stl_mean_identical"] and v["lockbox_2025_26_touched"] is False


def test_no_2025_26_access_while_building_loading_and_scoring(monkeypatch):
    sql, seasons = [], []
    real_connect, real_check = duckdb.connect, lockbox.check_season

    class Spy:
        def __init__(self, con):
            self._con = con

        def execute(self, q, *a, **k):
            sql.append(q)
            return self._con.execute(q, *a, **k)

        def __getattr__(self, name):
            return getattr(self._con, name)

    def check(season):
        seasons.append(int(season))
        return real_check(season)

    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: Spy(real_connect(*a, **k)))
    monkeypatch.setattr(lockbox, "check_season", check)
    art = R.build_final_artifact()
    cand = V.load_candidates()
    R.score_final(cand[cand["canonical_game_id"].isin(V.sample_games(cand))], R.load_final())
    assert sql and seasons and max(seasons) <= 2025
    bodies = [q.replace(str(C.PROJECT_ROOT), "<root>") for q in sql]     # the project path itself contains "2026"
    assert not [q for q in bodies if re.search(r"\b2026\b", q)]
    bounds = [int(b) for q in bodies for b in re.findall(r"BETWEEN\s+(\d{4})\s+AND\s+(\d{4})", q) for b in b]
    assert bounds and max(bounds) <= 2025
    assert art["training_through_season"] == 2025
