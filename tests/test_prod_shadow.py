"""Focused tests of the JM1_FINAL shadow production layer (CI-safe: synthetic FIXTURE history; no network, no local
frozen stores needed). The real-history replay equivalence lives in tests/test_prod_replay.py."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform.prod import (board as BO, bundle as BD, config as PC, deploy_dryrun as DR, features as PF,
                               fixtures as FX, inputs as IN, ledger as LG, loader as LD, matching as MT, monitoring as MO,
                               pricing as PX, quotes as QT, readiness as RD, run as RN, simulate as SM, slate as SL,
                               smoke as SMK, state as ST)
from cbb_platform.v2 import dependence as D, jm1_final as F, jm1_final_production as JP, joint as J

PROD_SRC = Path(PC.__file__).parent


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    return FX.synthetic_history(tmp_path_factory.mktemp("hist") / "history.duckdb")


@pytest.fixture(scope="module")
def smoke_run(hist, tmp_path_factory):
    work = tmp_path_factory.mktemp("smoke") / "smoke_ci"
    return SMK.smoke(work, hist, sims=4000), work


@pytest.fixture(scope="module")
def model():
    return LD.load()


# ============================================================================ season admission / training cutoff
def test_2027_season_admission(model):
    i = model.identity
    assert (i["production_season"], i["training_through"]) == (2027, 2026) == (PC.PRODUCTION_SEASON, PC.TRAINING_THROUGH)
    assert i["architecture"] == "JM1_FINAL" and all(i["checks"].values())


def test_increment_only_admits_2026_27(tmp_path, hist):
    rows, meta = FX.increment_rows(hist)
    bad = {k: v.copy() for k, v in rows.items()}
    bad["flagship.games"].loc[0, "season"] = 2026                      # tries to modify frozen history
    with pytest.raises(ST.StateUpdateFailed, match="outside season"):
        ST.update(bad, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], tmp_path / "s1",
                  created_at=FX.SOURCE_CUTOFF)
    bad = {k: v.copy() for k, v in rows.items()}
    bad["core.recruits"].loc[0, "recruit_year"] = 2027                 # a class that feeds 2027-28
    with pytest.raises(ST.StateUpdateFailed, match="recruiting class 2026"):
        ST.update(bad, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], tmp_path / "s2",
                  created_at=FX.SOURCE_CUTOFF)


def _artifact_copies(tmp_path):
    paths = {}
    for k, (rel, _) in PC.ARTIFACTS.items():
        p = tmp_path / Path(rel).name
        shutil.copyfile(C.PROJECT_ROOT / rel, p)
        paths[k] = p
    return paths


def test_training_cutoff_protection(tmp_path):
    paths = _artifact_copies(tmp_path)
    a = json.loads(paths["jm1_final"].read_text())
    a["SUPERVISED_TRAINING_THROUGH"] = 2027                            # claims training on the production season
    paths["jm1_final"].write_text(json.dumps(a))
    exp = {k: v[1] for k, v in PC.ARTIFACTS.items()} | {"jm1_final": LD.sha256_file(paths["jm1_final"])}
    with pytest.raises(LD.ArtifactIntegrityError) as e:
        LD.verify(paths, exp)
    assert e.value.reason == "WRONG_PRODUCTION_SEASON"


def test_artifact_hash_enforcement_and_wrong_hash_rejection(tmp_path, smoke_run):
    assert LD.verify()["artifact_sha256"] == {k: v[1] for k, v in PC.ARTIFACTS.items()}
    paths = _artifact_copies(tmp_path)
    paths["residual_j2"].write_bytes(paths["residual_j2"].read_bytes() + b" ")
    with pytest.raises(LD.ArtifactIntegrityError) as e:
        LD.verify(paths)
    assert e.value.reason == "ARTIFACT_HASH_MISMATCH"
    _, work = smoke_run
    out = RN.daily(FX.AS_OF, (), state_root=work / "state", ledger_path=tmp_path / "l.jsonl", run_root=tmp_path / "r",
                   artifact_paths=paths)
    assert out["readiness_state"] == "BLOCKED" and out["run_reasons"] == ["ARTIFACT_HASH_MISMATCH"]
    assert out["predictions"] == 0 and not (tmp_path / "l.jsonl").exists()


def test_frozen_predictive_code_unchanged():
    prot = json.loads(F.PROTOCOL_PATH.read_text())["code_sha256_at_freeze"]
    assert F.code_state() == {k: prot[k] for k in F.code_state()}
    man = json.loads((PC.MODEL_DIR / "jm1_final_production_manifest_2026_27.json").read_text())
    p = C.PROJECT_ROOT / "cbb_platform" / "v2" / "jm1_final_production.py"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == man["provenance"]["code_sha256"]["cbb_platform/v2/jm1_final_production.py"]


# ============================================================================ point in time / leakage / universe
def test_point_in_time_chronology(tmp_path, hist):
    rows, meta = FX.increment_rows(hist)
    early = FX.ROUND2_TIP + pd.Timedelta(hours=1)                      # round 2 results not yet knowable
    with pytest.raises(ST.StateUpdateFailed, match="FUTURE_LEAKAGE"):
        ST.update(rows, early, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], tmp_path / "s", created_at=early)
    assert ST.current(tmp_path / "s") is None
    cut, rule = SL.feature_cutoff(FX.UPCOMING_TIP, "EXACT", "2026-11-10")
    assert (cut, rule) == (FX.UPCOMING_TIP - pd.Timedelta(hours=1), "TIP_MINUS_1H")
    cut2, rule2 = SL.feature_cutoff(pd.NaT, "DATE_ONLY", "2026-11-10")
    assert rule2 == "ET_GAME_DATE_START" and cut2 == pd.Timestamp("2026-11-10 05:00:00Z")


def _hist_target(hist):
    con = duckdb.connect(str(hist), read_only=True)
    g = con.execute("""SELECT canonical_game_id, scheduled_tip_utc FROM flagship.played_games WHERE season = 2026
                       ORDER BY scheduled_tip_utc DESC, 1 LIMIT 1""").fetchone()
    con.close()
    return g[0], pd.Timestamp(g[1]) - pd.Timedelta(hours=1)


def test_target_game_outcomes_cannot_reach_features(tmp_path, hist):
    gid, cut = _hist_target(hist)
    cfg = LD.load().marginal_cfg
    a = PF.build(tmp_path / "a", [gid], 2026, cut, cfg, history_db=hist)
    tampered = tmp_path / "tampered.duckdb"
    shutil.copyfile(hist, tampered)
    con = duckdb.connect(str(tampered))
    con.execute(f"UPDATE flagship.player_game_outcomes SET points = points + 40, minutes = 40 WHERE canonical_game_id = '{gid}'")
    con.execute(f"UPDATE flagship.played_games SET home_points = 200 WHERE canonical_game_id = '{gid}'")
    con.close()
    b = PF.build(tmp_path / "b", [gid], 2026, cut, cfg, history_db=tampered)
    assert a["provenance"]["canonical"]["target_outcome_rows"] == 0
    pd.testing.assert_frame_equal(a["cand"], b["cand"])
    pd.testing.assert_frame_equal(a["team"], b["team"])


def test_pregame_candidate_legality(tmp_path, hist):
    gid, cut = _hist_target(hist)
    tampered = tmp_path / "late_player.duckdb"
    shutil.copyfile(hist, tampered)
    con = duckdb.connect(str(tampered))
    ghost = "P-ffffffffffffffff"                                       # appears ONLY in the target game's box
    con.execute(f"""INSERT INTO flagship.player_game_outcomes BY NAME
        SELECT * REPLACE ('{ghost}' AS canonical_player_id) FROM flagship.player_game_outcomes
        WHERE canonical_game_id = '{gid}' LIMIT 1""")
    con.close()
    fr = PF.build(tmp_path / "f", [gid], 2026, cut, LD.load().marginal_cfg, history_db=tampered)
    assert ghost not in set(fr["cand"]["canonical_player_id"])
    assert set(fr["cand"]["pool_evidence"]) <= {"PRIOR_APPEARANCE_CONFIRMED", "PRIOR_SEASON_RETURNER",
                                                "PRIOR_SEASON_ROSTER_RETURNER", "TRANSFER_PRESEASON_EVIDENCE",
                                                "RECRUIT_PRESEASON_EVIDENCE"}
    assert (pd.to_datetime(fr["cand"]["prediction_cutoff_utc"], utc=True) == cut).all()


def test_cold_start_behavior(smoke_run):
    s, work = smoke_run
    mi = pd.read_parquet(Path(s["run_A"]["run_dir"]) / "model_inputs.parquet")
    rec = mi[mi["player_id"] == s["fixture_meta"]["recruit"]]
    assert len(rec) == 1 and rec["cold_start_class"].iat[0] == "COLD_NO_HISTORY"
    assert rec["pool_evidence"].iat[0] == "RECRUIT_PRESEASON_EVIDENCE"
    assert np.isfinite(rec[["p_play", "min_mu", "PTS_mu"]].to_numpy()).all()
    tr = mi[mi["player_id"] == s["fixture_meta"]["transfer"]]
    assert len(tr) == 1 and tr["cold_start_class"].iat[0] == "COLD_TRANSFER_IN"


# ============================================================================ identity
def test_ambiguous_identity_suppression():
    teams = pd.DataFrame({"canonical_team_id": ["T-1", "T-2", "T-3"], "school": ["North", "South", "South"],
                          "mascot": ["Owls", "Cats", "Dogs"], "display_name": ["North Owls", "South Cats", "South Dogs"]})
    tidx = MT.team_index(teams, None)
    assert MT.resolve_team("South", tidx) == (None, "AMBIGUOUS")
    assert MT.resolve_team("North Owls", tidx) == ("T-1", "EXACT_NORMALIZED")
    assert MT.resolve_team("Nrth Owls", tidx) == (None, "UNMATCHED")             # never fuzzy
    cands = pd.DataFrame({"game_id": ["G-1"] * 3, "team_id": ["T-1", "T-1", "T-2"], "player_id": ["P-1", "P-2", "P-3"],
                          "display_name": ["Chris Smith", "Chris Smith Jr.", "Jon Doe"]})
    q = pd.DataFrame({"quote_status": ["VALID"] * 3, "canonical_game_id": ["G-1"] * 3, "suppression_reason": [""] * 3,
                      "provider_player_name": ["Chris Smith", "John Doe", "Jon Doe"]})
    out = MT.match_players(q, cands, None)
    assert out["suppression_reason"].tolist() == ["AMBIGUOUS_PLAYER", "UNMATCHED_PLAYER", ""]
    assert out["canonical_player_id"].tolist() == [None, None, "P-3"]
    pm = pd.DataFrame({"provider_player_name": ["Chris Smith"], "canonical_team_id": ["T-1"], "canonical_player_id": ["P-2"]})
    assert MT.match_players(q, cands, pm)["canonical_player_id"].iat[0] == "P-2"   # explicit map resolves


def test_strict_game_matching_and_orientation():
    slate = pd.DataFrame({"canonical_game_id": ["G-1", "G-2"], "home_team_id": ["T-1", "T-3"], "away_team_id": ["T-2", "T-4"],
                          "start_time": pd.to_datetime(["2026-11-11T00:30Z", "2026-11-11T00:30Z"]), "neutral_site": [False, True]})
    teams = pd.DataFrame({"canonical_team_id": ["T-1", "T-2", "T-3", "T-4"], "school": ["A", "B", "C", "D"],
                          "mascot": ["x", "y", "z", "w"], "display_name": ["A x", "B y", "C z", "D w"]})
    tidx = MT.team_index(teams, None)
    t = pd.Timestamp("2026-11-11T00:30Z")
    q = pd.DataFrame({"provider_event_id": ["e1", "e2", "e3", "e4"], "provider_home_team": ["A x", "B y", "D w", "A x"],
                      "provider_away_team": ["B y", "A x", "C z", "B y"],
                      "provider_commence_time": [t, t, t, t + pd.Timedelta(hours=8)], "quote_status": ["VALID"] * 4,
                      "suppression_reason": [""] * 4})
    out = MT.match_games(q, slate, tidx)
    assert out["canonical_game_id"].tolist() == ["G-1", None, "G-2", None]          # swapped only at a neutral site
    assert out["suppression_reason"].tolist() == ["", "UNMATCHED_GAME", "", "UNMATCHED_GAME"]


# ============================================================================ one bank / combos / pmf / push / psd / seeds
def test_one_joint_draw_bank_per_game(hist, tmp_path, monkeypatch):
    calls = []
    real = JP.score_game

    def counting(*a, **k):
        calls.append(a[1])
        return real(*a, **k)
    monkeypatch.setattr(JP, "score_game", counting)
    s = SMK.smoke(tmp_path / "one_bank", hist, sims=2000)
    games = list(s["run_A"]["banks"])
    assert sorted(calls) == sorted(games * 2)                                  # one bank per game per run (2 runs)
    preds = LG.predictions(tmp_path / "one_bank" / "smoke_ledger.jsonl")
    for g in games:
        shas = {p["bank_sha256"] for p in preds if p["game_id"] == g and p["run_id"] == s["run_A"]["run_id"]}
        assert shas == {s["run_A"]["banks"][g]["bank_sha256"]}                 # every market from that one bank


def _bank(hist, tmp_path, sims=3000):
    gid, cut = _hist_target(hist)
    m = LD.load()
    fr = PF.build(tmp_path / "bank", [gid], 2026, cut, m.marginal_cfg, history_db=hist)
    rows = IN.marginal_rows(fr["cand"], m.marginal_cfg, m.v0_state, m.participation_state)
    rows, _ = IN.apply_availability(rows, None)
    env = IN.environment(fr["team"], m.jm1["struct"]["env"])
    return m, gid, rows, env, SM.simulate_game(m.jm1, gid, rows, env, sims)


def test_same_draw_combos_pmf_normalization_and_determinism(hist, tmp_path):
    m, gid, rows, env, bank = _bank(hist, tmp_path)
    assert SM.same_draw_check(bank)
    d = bank["draws"]
    der = J.derived_markets(d)
    si = J.SI
    assert np.array_equal(der["PRA"], d[:, :, si["PTS"]] + d[:, :, si["REB"]] + d[:, :, si["AST"]])
    assert np.array_equal(der["STOCKS"], d[:, :, si["STL"]] + d[:, :, si["BLK"]])
    md = SM.market_distributions(bank)
    assert set(md["market"]) == set(PC.MARKETS)
    assert all(abs(p.sum() - 1) < 1e-9 and (p >= 0).all() for p in md["pmf"])
    again = SM.simulate_game(m.jm1, gid, rows, env, 3000)
    assert again["bank_sha256"] == bank["bank_sha256"]                         # deterministic seeds
    assert bank["seeds"]["count_latents"] == D.game_seed(J.SIM_SEED, gid)


def test_psd_validation_blocks(hist, tmp_path, monkeypatch):
    m, gid, rows, env, _ = _bank(hist, tmp_path, 500)
    real = D.game_correlation
    monkeypatch.setattr(D, "game_correlation", lambda *a, **k: {**real(*a, **k), "invalid": True, "min_eig": -0.5})
    with pytest.raises(SM.ScoringFailure) as e:
        SM.simulate_game(m.jm1, gid, rows, env, 500)
    assert e.value.reason == "NON_PSD_DEPENDENCE"


def test_push_handling_and_pricing():
    pmf = np.array([0.1, 0.2, 0.3, 0.25, 0.15])
    p = PX.price_line(pmf, 2.0)
    assert np.isclose(p["p_push"], 0.3) and np.isclose(p["p_over"], 0.4) and np.isclose(p["p_under"], 0.3)
    assert np.isclose(p["p_over"] + p["p_under"] + p["p_push"], 1.0)
    assert np.isclose(p["fair_over_decimal"], 1 / (0.4 / 0.7))                 # fair price conditional on no push
    h = PX.price_line(pmf, 2.5)
    assert h["p_push"] == 0.0 and np.isclose(h["p_over"], 0.4)
    sv = PX.side_view(p, "OVER")
    assert np.isclose(PX.expected_value(sv["win"], sv["lose"], 2.0), 0.4 * 1.0 - 0.3)   # push refunds the stake
    with pytest.raises(PX.InvalidProbabilities) as e:
        PX.price_line(np.array([0.5, 0.4]), 0.5)
    assert e.value.reason == "PMF_NOT_NORMALIZED"
    assert PX.price_line(pmf, 9.0)["p_under"] == pytest.approx(1.0)            # line beyond support


# ============================================================================ quotes
def test_quote_normalization_and_chronology():
    ev = {"id": "e1", "home_team": "A x", "away_team": "B y", "commence_time": "2026-11-11T00:30:00Z", "bookmakers": [
        {"key": "bk", "last_update": "2026-11-10T15:50:00Z", "markets": [
            {"key": "player_points", "last_update": "2026-11-10T15:50:00Z", "outcomes": [
                {"name": "Over", "description": "Jon Doe", "price": -110, "point": 14.5},
                {"name": "Under", "description": "Jon Doe", "price": +120, "point": 14.5}]},
            {"key": "player_points_rebounds_assists_alternate", "last_update": "2026-11-10T15:50:00Z", "outcomes": [
                {"name": "Over", "description": "Jon Doe", "price": +200, "point": 30.5}]},
            {"key": "player_first_basket", "outcomes": [{"name": "Jon Doe", "price": +900}]},
            {"key": "player_rebounds", "last_update": "2026-11-10T17:00:00Z", "outcomes": [
                {"name": "Over", "description": "Jon Doe", "price": -110, "point": 4.5}]}]}]}
    q = QT.normalize_event(ev, "2026-11-10T15:55:00Z")
    assert set(q.columns) >= {"provider", "sportsbook", "provider_event_id", "canonical_game_id", "provider_player_name",
                              "canonical_player_id", "stat", "line", "side", "decimal_odds", "quote_available_at",
                              "collector_received_at", "market_id"}
    assert np.isclose(q["decimal_odds"].iat[0], 1 + 100 / 110) and np.isclose(q["decimal_odds"].iat[1], 2.2)
    assert q["stat"].iat[2] == "PRA" and bool(q["is_alternate"].iat[2])
    assert q["suppression_reason"].tolist() == ["", "", "", "UNSUPPORTED_MARKET", "INVALID_QUOTE"]   # last: after receipt
    tips = {"G-1": pd.Timestamp("2026-11-11T00:30Z")}
    v = q.iloc[:2].assign(canonical_game_id="G-1")
    v.loc[v.index[1], "quote_available_at"] = pd.Timestamp("2026-11-10T15:00Z")
    f = BO.quote_freshness(v, pd.Timestamp("2026-11-10T16:00Z"), tips)
    assert f["suppression_reason"].tolist() == ["", "STALE_QUOTE"]
    f2 = BO.quote_freshness(v, pd.Timestamp("2026-11-10T15:52Z"), tips)
    assert f2["suppression_reason"].iat[0] == "POST_CUTOFF_QUOTE"              # collected after the prediction


def test_smoke_quote_outcomes(smoke_run):
    s, _ = smoke_run
    got = {(r["quote_status"], r["suppression_reason"]) for r in s["run_A"]["quotes"]["by_status"]}
    assert {("SUPPRESSED", "UNMATCHED_GAME"), ("SUPPRESSED", "UNMATCHED_PLAYER"), ("SUPPRESSED", "STALE_QUOTE"),
            ("SUPPRESSED", "POST_CUTOFF_QUOTE"), ("INVALID", "UNSUPPORTED_MARKET"), ("INVALID", "INVALID_QUOTE"),
            ("VALID", "")} <= got


# ============================================================================ availability / readiness
def test_unsupported_rotation_shock(smoke_run):
    s, _ = smoke_run
    up = s["fixture_meta"]["upcoming"]
    gr = s["run_A"]["game_reasons"]
    assert "UNSUPPORTED_ROTATION_SHOCK" in gr[up["U2"]]
    assert gr[up["U1"]] == []                                                  # bench OUT: player suppressed only
    assert "AMBIGUOUS_GAME" in gr[up["U3"]] and "AMBIGUOUS_GAME" in gr[up["U3b"]]
    assert "NON_DI_GAME" in gr[up["NONDI"]]
    mi = pd.read_parquet(Path(s["run_A"]["run_dir"]) / "model_inputs.parquet")
    assert set(mi.loc[mi["availability_status"] == "OUT", "player_suppression"]) == {"CONFIRMED_UNAVAILABLE"}


def test_no_redistribution_to_teammates(hist, tmp_path):
    _, gid, rows, _, _ = _bank(hist, tmp_path, 200)
    top = rows.sort_values("min_mu").iloc[-1]
    av = pd.DataFrame([{"game_id": gid, "player_id": top["player_id"], "status": "OUT", "received_at": FX.SOURCE_CUTOFF}])
    after, flags = IN.apply_availability(rows, av)
    before, _ = IN.apply_availability(rows, None)
    mates = after["player_id"] != top["player_id"]
    for c in ["p_play", "min_mu", "min_sigma"] + [f"{s}_mu" for s in PC.BASE_STATS]:
        assert np.array_equal(after.loc[mates, c].to_numpy(), before.loc[mates, c].to_numpy())
    assert "UNSUPPORTED_ROTATION_SHOCK" in flags[gid]["reasons"]


def test_readiness_fail_closed_and_shadow_only_ceiling():
    assert RD.evaluate()["readiness_state"] == "SHADOW_ONLY"
    for r in RD.REQUIRED_CODES:
        assert RD.evaluate([r])["readiness_state"] == "BLOCKED", r
        assert RD.evaluate((), [r])["readiness_state"] == "BLOCKED", r
        assert RD.evaluate((), (), [r])["readiness_state"] == "BLOCKED", r
    assert RD.evaluate((), (), ["SOMETHING_NEW"])["readiness_state"] == "BLOCKED"      # unknown -> blocked
    assert RD.cap("PUBLIC_READY") == "SHADOW_ONLY" and RD.cap("nonsense") == "BLOCKED"
    assert PC.MAX_READINESS_STATE == "SHADOW_ONLY" and PC.WAGERING_ENABLED is False
    assert all(not RD.evaluate()["wager_eligible"] for _ in range(2))


def test_cutoff_unresolved_blocks(monkeypatch):
    monkeypatch.setattr(PC, "VALIDATED_PREDICTION_CUTOFF", "UNRESOLVED")
    assert RD.evaluate()["readiness_state"] == "BLOCKED" and "CUTOFF_UNRESOLVED" in RD.evaluate()["reasons"]


def test_smoke_never_public_ready(smoke_run):
    s, _ = smoke_run
    preds = LG.predictions(Path(s["run_A"]["run_dir"]).parents[1] / "smoke_ledger.jsonl")
    assert {p["readiness_state"] for p in preds} <= {"BLOCKED", "SHADOW_ONLY"}
    assert s["run_A"]["readiness_state"] == "SHADOW_ONLY" and s["run_A"]["wagering_enabled"] is False
    assert all(p["data_label"].startswith("FIXTURE") for p in preds)


# ============================================================================ state / slate
def test_failed_update_keeps_last_known_good(tmp_path, hist):
    rows, meta = FX.increment_rows(hist)
    root = tmp_path / "state"
    good = ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], root,
                     created_at=FX.SOURCE_CUTOFF)
    ptr = (root / "CURRENT.json").read_text()
    bad = {"flagship.player_game_outcomes": rows["flagship.player_game_outcomes"].head(1).assign(canonical_player_id="BAD-ID")}
    with pytest.raises(ST.StateUpdateFailed):
        ST.update(bad, FX.SOURCE_CUTOFF + pd.Timedelta(hours=1), FX.FIXTURE_PROVIDER, meta["preseason_evidence"], root,
                  created_at=FX.SOURCE_CUTOFF + pd.Timedelta(hours=1))
    assert (root / "CURRENT.json").read_text() == ptr and ST.current(root)["manifest"]["state_version"] == good["state_version"]
    assert not list((root / "versions").glob(".building-*"))
    assert RN._last_failure_after(root, json.loads(ptr)["promoted_at"])
    assert ST.staleness(good, FX.SOURCE_CUTOFF + pd.Timedelta(hours=PC.STATE_MAX_AGE_HOURS + 1))["stale"]
    for k in ("state_version", "created_at", "source_cutoff", "last_completed_game", "provider", "input_hashes"):
        assert k in good


def test_slate_pending_prior_result_and_late_prediction(tmp_path, hist):
    rows, meta = FX.increment_rows(hist)
    g = rows["flagship.games"].copy()
    r2 = g["scheduled_tip_utc"] == FX.ROUND2_TIP
    g.loc[r2, ["home_points", "away_points"]] = None                           # round 2 results never ingested
    keep = {k: v for k, v in rows.items() if k not in ("flagship.team_game_outcomes", "flagship.player_game_outcomes",
                                                       "flagship.substitutions")}
    keep["flagship.games"] = g
    root = tmp_path / "s"
    ST.update(keep, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], root, created_at=FX.SOURCE_CUTOFF)
    sl = SL.build(ST.current(root)["increment_db"], FX.AS_OF, FX.SOURCE_CUTOFF)
    u1 = sl.set_index("canonical_game_id").loc[meta["upcoming"]["U1"]]
    assert "STALE_STATE" in u1["suppression_reasons"]
    late = SL.build(ST.current(root)["increment_db"], FX.UPCOMING_TIP - pd.Timedelta(minutes=20), FX.SOURCE_CUTOFF)
    assert all("PREDICTION_AFTER_CUTOFF" in r for r in late["suppression_reasons"])


# ============================================================================ ledger / bundle / rollback / monitoring
def _pred(i):
    return {k: None for k in LG.PREDICTION_FIELDS} | {"prediction_id": f"p{i}", "run_id": "r", "market": "PTS",
                                                       "projection": 10.0, "line": 9.5, "pmf_q75": 12}


def test_append_only_ledger(tmp_path):
    p = tmp_path / "ledger.jsonl"
    LG.append([{"record_type": "RUN", "body": {"run_id": "r"}}, {"record_type": "PREDICTION", "body": _pred(1)}], p)
    LG.append([{"record_type": "SETTLEMENT", "body": {"prediction_id": "p1", "actual": 12, "brier": 0.2}}], p)
    assert LG.verify(p)["CHAIN_VALID"] and LG.verify(p)["settlements"] == 1
    with pytest.raises(LG.LedgerViolation, match="immutable"):
        LG.append([{"record_type": "PREDICTION", "body": _pred(1)}], p)
    with pytest.raises(LG.LedgerViolation, match="prediction-time"):
        LG.append([{"record_type": "SETTLEMENT", "body": {"prediction_id": "p1", "projection": 99}}], p)
    with pytest.raises(LG.LedgerViolation, match="unknown prediction"):
        LG.append([{"record_type": "SETTLEMENT", "body": {"prediction_id": "nope"}}], p)
    lines = p.read_text().splitlines()
    tampered = tmp_path / "t.jsonl"
    tampered.write_text("\n".join([lines[0], lines[1].replace('"projection":10.0', '"projection":11.0'), lines[2]]) + "\n")
    with pytest.raises(LG.LedgerViolation, match="row_hash"):
        LG.verify(tampered)
    dropped = tmp_path / "d.jsonl"
    dropped.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(LG.LedgerViolation, match="chain"):
        LG.verify(dropped)


def test_static_bundle_sha_validation(smoke_run, tmp_path):
    s, work = smoke_run
    good = work / "bundle_A"
    assert BD.verify(good)["BUNDLE_VALID"]
    t = tmp_path / "b1"
    shutil.copytree(good, t)
    (t / "predictions.csv").write_text((t / "predictions.csv").read_text() + "x\n")
    with pytest.raises(BD.BundleChecksumFailure, match="checksum"):
        BD.verify(t)
    t2 = tmp_path / "b2"
    shutil.copytree(good, t2)
    (t2 / "extra.parquet").write_text("dev data")
    with pytest.raises(BD.BundleChecksumFailure):
        BD.verify(t2)
    man = json.loads((good / "DEPLOYMENT_MANIFEST.json").read_text())
    assert man["artifact_sha256"] == {k: v[1] for k, v in PC.ARTIFACTS.items()}
    assert man["wagering_enabled"] is False and man["public_deployment_approved"] is False
    assert "FIXTURE" in man["data_label"]
    html = (good / "index.html").read_text()
    assert "SHADOW" in html and "NOT FOR WAGERING" in html


def test_rollback_dry_run(smoke_run, tmp_path):
    s, work = smoke_run
    assert s["rollback_dry_run"]["ROLLBACK_DRY_RUN"] == "PASS" and s["rollback_dry_run"]["network_used"] is False
    with pytest.raises(DR.DryRunRefused):
        DR.dry_run(work / "bundle_A", work / "bundle_B", C.PROJECT_ROOT / "not_a_sandbox")
    assert DR.rollback(tmp_path / "empty")["rolled_back"] is False


def test_monitoring_is_read_only(smoke_run):
    s, work = smoke_run
    led = work / "smoke_ledger.jsonl"
    before = led.read_bytes()
    rep = MO.report(led)
    assert led.read_bytes() == before
    assert rep["A_PTS_MEAN_BIAS"]["reference"]["jm1_final"] == 0.212 and rep["A_PTS_MEAN_BIAS"]["reference"]["s0"] == 0.033
    assert rep["C_SAME_TEAM_CORRELATION"]["reference"]["jm1_final_rmse"] == 0.0161
    assert rep["policy"].startswith("monitoring only")


def test_no_network_or_deploy_code_in_production_layer():
    """No deploy / shell / raw-socket code anywhere; HTTP only in the four production acquisition modules (CBBD GETs,
    The Odds API GETs, RotoWire API availability GETs, RotoWire subscriber-web availability GETs)."""
    never = re.compile(r"^\s*(import|from)\s+(urllib|http\.client|socket|paramiko|ftplib|smtplib|subprocess)\b", re.M)
    http = re.compile(r"^\s*(import|from)\s+requests\b", re.M)
    for f in PROD_SRC.glob("*.py"):
        text = f.read_text()
        assert not never.search(text), f.name
        assert not http.search(text) or f.name in ("cbbd_acquire.py", "odds_acquire.py", "rotowire_acquire.py",
                                                   "rotowire_web_acquire.py"), f.name
    for name in ("cbbd_acquire.py", "odds_acquire.py", "rotowire_acquire.py", "rotowire_web_acquire.py"):
        acq = (PROD_SRC / name).read_text()
        assert ".post(" not in acq and ".put(" not in acq and ".delete(" not in acq
    assert 'BASE = "https://api.the-odds-api.com"' in (PROD_SRC / "odds_acquire.py").read_text()
    assert 'BASE = "https://api.rotowire.com"' in (PROD_SRC / "rotowire_acquire.py").read_text()
    web = (PROD_SRC / "rotowire_web_acquire.py").read_text()
    assert 'SITE = "https://www.rotowire.com"' in web and "api.rotowire.com" not in web   # never poses as the API
    acq = (PROD_SRC / "cbbd_acquire.py").read_text()
    assert 'BASE = CBBD_ADAPTER.BASE' in acq
