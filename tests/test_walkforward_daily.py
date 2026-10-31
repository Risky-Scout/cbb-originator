"""END-TO-END CHRONOLOGY of the daily walk-forward refit (directive section 33). LOCAL: needs the frozen stores.

A labeled synthetic FIXTURE 2026-27 season (prod.fixtures: real teams / players of the frozen canonical history,
synthetic boxes) arrives as immutable production state versions, exactly as the live feed would deliver it:

    v0 2026-11-02 12:00Z  schedule + preseason evidence only
    v1 2026-11-04 09:00Z  round 1: A final + complete; B still in progress (no result); C final but half a box
    v2 2026-11-07 09:00Z  B and C complete, A CORRECTED (one rebound), round 2 (3 games) final + complete
    v3 2026-11-07 12:00Z  LATE game result arrives AFTER the 2026-11-07 06:00 ET cutoff (HR-1 knowable before it)
    v4 2026-11-10 15:05Z  idempotent re-sync (state pointer freshness for the prediction run)
    v5 2026-11-11 09:00Z  upcoming games U1, U2 final

    morning 11-04 -> PASS (A only)      rerun -> idempotent          morning 11-07 -> PASS (6 games; A corrected)
    morning 11-08 -> REFUSED (forced repeat-fit nondeterminism)     predict 11-10 16:00Z with the ACTIVE version
    morning 11-11 -> settles the 11-10 predictions FIRST (attributed to their version), then PASS     rerun -> idempotent
    a fresh store rebuilding 11-04 from the same inputs -> byte-identical artifacts
"""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform.prod import config as PC, fixtures as FX, ledger as LG, side_monitor as SD, state as ST
from cbb_platform.v2 import phase1b as B, provenance as PV
from cbb_platform.walkforward import config as WC, daily as DL, eligibility as EL, monitor as MON, store as SO

HIST = PC.HISTORY_CANONICAL
NEEDED = (PV.LB_FEATURES, PV.LB_LABELS, HIST, B.OOS_PATH)
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="frozen local stores not present")
LATE_TIP = pd.Timestamp("2026-11-06T20:00:00Z")
D4, D7, D8, D11 = date(2026, 11, 4), date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 11)


def _rosters():
    pk = FX.pick(HIST)
    out = {}
    for team in pk["teams"]:
        p = pk["players"][pk["players"]["team"] == team].head(10).copy()
        p["w"], p["r"], p["pos"] = (p["mins"] / p["gp"]).clip(lower=2.0), 1.0, p["pos"].fillna("G")
        out[team] = p.reset_index(drop=True)
    return out


def _result(rng, roster, gid, tip, home, away) -> dict:
    hr, ht, hs = FX._box(rng, gid, 2027, tip, home, away, True, roster[home])
    ar, at, as_ = FX._box(rng, gid, 2027, tip, away, home, False, roster[away])
    for t in (ht, at):     # the live CBBD box carries teamStats_possessions (build_canonical); prod.fixtures._box does not
        t["possessions_source"] = float(t["fga"] - t["orb"] + t["tov_total"] + 0.475 * t["fta"])
    return {"game": FX._game(gid, 2027, tip, home, away, hp=ht["points"], ap=at["points"]),
            "team": pd.DataFrame([ht, at]), "player": pd.DataFrame(hr + ar), "subs": pd.DataFrame(hs + as_)}


def _blank(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()
    g[["home_points", "away_points"]] = None
    g[["has_team_box_score", "has_player_box_score"]] = False
    return g


def _rel(games, parts) -> dict:
    return {"flagship.games": pd.DataFrame(games),
            "flagship.team_game_outcomes": pd.concat([p["team"] for p in parts], ignore_index=True),
            "flagship.player_game_outcomes": pd.concat([p["player"] for p in parts], ignore_index=True),
            "flagship.substitutions": pd.concat([p["subs"] for p in parts], ignore_index=True)}


def _states(state_root: Path) -> dict:
    rows, meta = FX.increment_rows(HIST)
    g = rows["flagship.games"]
    rng, ros = np.random.default_rng(20271), _rosters()
    r1 = sorted(g.loc[g["scheduled_tip_utc"] == FX.ROUND1_TIP, "canonical_game_id"])
    r2 = sorted(g.loc[g["scheduled_tip_utc"] == FX.ROUND2_TIP, "canonical_game_id"])
    A, Bg, Cg = r1
    t = meta["teams"]
    late = FX._gid("2027|late|1")
    teams = g.set_index("canonical_game_id")[["home_team_id", "away_team_id", "scheduled_tip_utc"]]
    res = {gid: _result(rng, ros, gid, teams.loc[gid, "scheduled_tip_utc"], teams.loc[gid, "home_team_id"],
                        teams.loc[gid, "away_team_id"]) for gid in r1 + r2}
    res[late] = _result(rng, ros, late, LATE_TIP, t[1], t[4])
    for u in ("U1", "U2"):
        gid = meta["upcoming"][u]
        res[gid] = _result(rng, ros, gid, teams.loc[gid, "scheduled_tip_utc"], teams.loc[gid, "home_team_id"],
                           teams.loc[gid, "away_team_id"])
    core = {k: v for k, v in rows.items() if k.startswith("core.")}
    pe = meta["preseason_evidence"]
    sched = _blank(pd.concat([g, pd.DataFrame([FX._game(late, 2027, LATE_TIP, t[1], t[4])])], ignore_index=True))
    up = lambda r, at, src=None: ST.update(r, src or at, FX.FIXTURE_PROVIDER, pe, state_root, created_at=at)
    up({"flagship.games": sched, **core}, pd.Timestamp("2026-11-02T12:00:00Z"))
    # v1: A complete; C final with only the home team's player box; B in progress
    c_half = dict(res[Cg])
    c_half["player"] = res[Cg]["player"][res[Cg]["player"]["canonical_team_id"] == teams.loc[Cg, "home_team_id"]]
    up(_rel([res[A]["game"], res[Cg]["game"]], [res[A], c_half]), pd.Timestamp("2026-11-04T09:00:00Z"))
    # v2: B, the rest of C, round 2; A corrected (+1 offensive rebound for its first player, team totals kept consistent)
    a_fix = copy.deepcopy(res[A])
    i = a_fix["player"].index[0]
    a_fix["player"].loc[i, ["orb", "reb"]] = a_fix["player"].loc[i, ["orb", "reb"]] + 1
    tm = a_fix["team"]["canonical_team_id"] == a_fix["player"].loc[i, "canonical_team_id"]
    a_fix["team"].loc[tm, ["orb", "reb"]] = a_fix["team"].loc[tm, ["orb", "reb"]] + 1
    up(_rel([res[x]["game"] for x in [Bg, Cg] + r2] + [a_fix["game"]], [res[Bg], res[Cg], a_fix] + [res[x] for x in r2]),
       pd.Timestamp("2026-11-07T09:00:00Z"))
    up(_rel([res[late]["game"]], [res[late]]), pd.Timestamp("2026-11-07T12:00:00Z"))
    up({}, pd.Timestamp("2026-11-10T15:05:00Z"), pd.Timestamp("2026-11-10T15:00:00Z"))
    v5 = [meta["upcoming"]["U1"], meta["upcoming"]["U2"]]
    return {"meta": meta, "A": A, "B": Bg, "C": Cg, "r2": r2, "late": late, "U": v5, "a_player": a_fix["player"].loc[i],
            "v5": lambda: up(_rel([res[x]["game"] for x in v5], [res[x] for x in v5]), pd.Timestamp("2026-11-11T09:00:00Z"))}


def _now(day: date) -> pd.Timestamp:
    return WC.refit_cutoff(day) + pd.Timedelta(minutes=30)


@pytest.fixture(scope="module")
def tl(tmp_path_factory, monkeypatch_module):
    work = tmp_path_factory.mktemp("wf_e2e")
    state_root, root = work / "state", work / "cbb-originator"
    s = _states(state_root)
    log = lambda m: None
    out = {"s": s, "root": root, "state_root": state_root, "work": work}
    out["d4"] = DL.morning(D4, root, state_root, HIST, now=_now(D4), log=log)
    snap4 = {f: (SO.version_dir(root, D4) / f).read_bytes() for f in list(SO.FILES.values()) + ["labels.parquet"]}
    out["d4_rerun"] = DL.morning(D4, root, state_root, HIST, now=_now(D4) + pd.Timedelta(minutes=5), log=log)
    out["d7"] = DL.morning(D7, root, state_root, HIST, now=_now(D7), log=log)
    # 11-08: the candidate's repeat fit is made to differ -> DETERMINISTIC_REPEAT_FIT fails -> REFUSED
    real_fit, calls = DL.WF.fit, {"n": 0}

    def flaky(cfg, fr, log=print):
        calls["n"] += 1
        if calls["n"] == 1:
            calls["f"] = real_fit(cfg, fr, log)
            return calls["f"]
        f = copy.deepcopy(calls["f"])
        k = next(iter(f["v0"]["coef"]))
        f["v0"]["coef"][k] += 1e-9
        return f
    monkeypatch_module.setattr(DL.WF, "fit", flaky)
    out["d8"] = DL.morning(D8, root, state_root, HIST, now=_now(D8), log=log)
    monkeypatch_module.setattr(DL.WF, "fit", real_fit)
    # 11-10 16:00Z: predictions with the ACTIVE version (fixture quotes)
    q = FX.quotes_files(HIST, s["meta"], work / "quotes")
    out["pred"] = DL.predict(FX.AS_OF, root, q, state_root=state_root, history_db=HIST, sims=PC.SIMS,
                             sync_log=work / "sync.jsonl", odds_cache_root=work / "odds_cache", odds_collections=None,
                             odds_ledger=work / "odds.jsonl", odds_record=work / "odds_record.json")
    s["v5"]()
    out["d11"] = DL.morning(D11, root, state_root, HIST, now=_now(D11), log=log)
    rep = (root / "monitoring" / "reports" / f"{D11.isoformat()}.json").read_bytes()
    nset = len(pd.read_parquet(root / "monitoring" / "settlements.parquet"))
    out["d11_rerun"] = DL.morning(D11, root, state_root, HIST, now=_now(D11) + pd.Timedelta(minutes=5), log=log)
    out["idem"] = {"report_identical": rep == (root / "monitoring" / "reports" / f"{D11.isoformat()}.json").read_bytes(),
                   "settlements_before": nset, "settlements_after": len(pd.read_parquet(root / "monitoring" / "settlements.parquet")),
                   "d4_files_unchanged": all(snap4[f] == (SO.version_dir(root, D4) / f).read_bytes() for f in snap4)}
    # a fresh store, same state, same day -> same artifacts
    root2 = work / "fresh" / "cbb-originator"
    out["d4_fresh"] = DL.morning(D4, root2, state_root, HIST, now=_now(D4), log=log)
    out["root2"] = root2
    return out


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _man(root, day):
    return json.loads((SO.version_dir(root, day) / SO.MANIFEST).read_text())


def _el(root, day):
    return pd.read_parquet(SO.version_dir(root, day) / "eligibility.parquet").set_index("game_id")


def test_first_refit_includes_the_completed_game_the_next_morning(tl):
    r, s = tl["d4"], tl["s"]
    assert r["DAILY_REFIT_STATUS"] == "PASS" and r["DAILY_MODEL_PROMOTION"] == "PROMOTED", r.get("gates")
    m = _man(tl["root"], D4)
    assert m["MODEL_VERSION"] == "cbb-jm1-2026-27-20261104" and m["PREVIOUS_MODEL_VERSION"] == "cbb-jm1-2026-27-20261101"
    assert m["N_INCLUDED_2026_27_GAMES"] == 1 and m["LATEST_INCLUDED_GAME"]["game_id"] == s["A"]
    assert m["N_INCLUDED_2026_27_PLAYER_GAMES"] > 0 and m["TRAINING_THROUGH_SEASON"] == 2027
    assert m["recency"]["weight_by_season"]["2027"] == 1.0 and m["recency"]["weight_by_season"]["2026"] == 0.5
    assert m["fit"]["resid_seasons"] == [2024, 2025, 2026, 2027] and m["fit"]["env_seasons"][-1] == 2027
    assert m["deterministic_refit"]["BIT_IDENTICAL"]
    for k in ("MODEL_VERSION", "CREATED_AT", "TRAINING_CUTOFF", "TRAINING_THROUGH_DATE", "LATEST_INCLUDED_GAME",
              "N_INCLUDED_2026_27_GAMES", "N_INCLUDED_2026_27_PLAYER_GAMES", "FEATURE_STATE_HASH", "MARGINAL_ARTIFACT_HASH",
              "STRUCTURAL_ARTIFACT_HASH", "DISPERSION_HASH", "DEPENDENCE_HASH", "CODE_COMMIT", "CONFIG_HASH",
              "TRAINING_DATA_HASH", "PREVIOUS_MODEL_VERSION"):
        assert m.get(k) is not None, k
    g = json.loads((SO.version_dir(tl["root"], D4) / "gates.json").read_text())
    assert g["ALL_PASS"] and g["POINT_IN_TIME_LEGALITY"] == "PASS"
    assert not (g["FUTURE_DATA_LEAKAGE_DETECTED"] or g["TARGET_GAME_FEATURE_LEAKAGE_DETECTED"]
                or g["TARGET_GAME_OUTCOME_LEAKAGE_DETECTED"])


def test_in_progress_and_incomplete_box_games_are_deferred_then_enter(tl):
    s, e4, e7 = tl["s"], _el(tl["root"], D4), _el(tl["root"], D7)
    assert tuple(e4.loc[s["B"], ["decision", "reason"]]) == ("DEFERRED", "NOT_FINAL_AT_CUTOFF")
    assert tuple(e4.loc[s["C"], ["decision", "reason"]]) == ("DEFERRED", "BOX_INCOMPLETE")
    assert set(e4.index[e4["decision"] == "INCLUDED"]) == {s["A"]}
    assert set(e7.index[e7["decision"] == "INCLUDED"]) == {s["A"], s["B"], s["C"], *s["r2"]}
    assert tl["d7"]["eligibility"]["NEWLY_INCLUDED_GAMES"] == 5


def test_future_and_late_arriving_outcomes_never_enter_training(tl):
    s, m7, e7 = tl["s"], _man(tl["root"], D7), _el(tl["root"], D7)
    late = e7.loc[s["late"]]
    assert late["decision"] == "DEFERRED" and late["reason"] == "NOT_FINAL_AT_CUTOFF"   # result arrived 1 h after the cutoff
    assert pd.Timestamp(late["knowable_at"]) <= WC.refit_cutoff(D7)                    # although HR-1 says knowable
    v = {x["state_version"]: x for x in EL.state_versions(tl["state_root"])}
    assert v[m7["STATE_VERSION"]]["created_at"] <= WC.refit_cutoff(D7)
    assert (e7["scheduled_tip_utc"] < WC.refit_cutoff(D7)).all()
    tm = json.loads((SO.version_dir(tl["root"], D7) / "training_manifest.json").read_text())
    for gid, b in tm["feature_builds"].items():               # every pregame row built at its own cutoff, from older state
        assert pd.Timestamp(b["build"]["as_of"]) == pd.Timestamp(b["prediction_cutoff_utc"])
        assert pd.Timestamp(b["state_created_at"]) <= pd.Timestamp(b["prediction_cutoff_utc"])
        assert b["build"]["target_outcome_rows"] == 0 and b["pit_audit"]["violations"] == 0
    assert _el(tl["root"], D11).loc[s["late"], "decision"] == "INCLUDED"


def test_corrected_box_enters_only_the_next_version(tl):
    s, root = tl["s"], tl["root"]
    corr = SO.read_log(root / "manifests" / "corrections.jsonl")
    assert len(corr) == 1 and corr[0]["game_id"] == s["A"]
    c = corr[0]
    assert c["FIRST_MODEL_VERSION_USING_CORRECTION"] == "cbb-jm1-2026-27-20261107"
    assert c["CORRECTION_RECEIVED_AT"] == "2026-11-07T09:00:00+00:00"
    ch = {(x["relation"], x.get("canonical_player_id"), x["field"]): x for x in c["changes"]}
    pid = s["a_player"]["canonical_player_id"]
    for rel, who in (("flagship.player_game_outcomes", pid), ("flagship.team_game_outcomes", None)):
        for f in ("orb", "reb"):
            x = ch[(rel, who, f)]
            assert x["corrected_value"] == x["original_value"] + 1
    assert {(x["relation"], x["field"]) for x in c["changes"]} == {(r, f) for r in ("flagship.player_game_outcomes",
                                                                    "flagship.team_game_outcomes") for f in ("orb", "reb")}
    m4 = _man(root, D4)                                         # the old version is untouched and keeps its old state
    assert m4["STATE_VERSION"].startswith("S20261104T090000Z") and tl["idem"]["d4_files_unchanged"]


def test_failed_candidate_leaves_the_active_model_unchanged(tl):
    r = tl["d8"]
    assert r["DAILY_REFIT_STATUS"] == "FAIL" and r["DAILY_MODEL_PROMOTION"] == "REFUSED"
    assert r["gates"]["failed"] == ["DETERMINISTIC_REPEAT_FIT"]
    assert r["ACTIVE_MODEL_VERSION"] == "cbb-jm1-2026-27-20261107"
    assert not SO.version_dir(tl["root"], D8).exists() and Path(r["refused_candidate"]).exists()


def test_atomic_promotion_and_rollback(tl):
    root = tl["root"]
    assert tl["d11"]["DAILY_MODEL_PROMOTION"] == "PROMOTED" and tl["d11"]["ACTIVE_MODEL_VERSION"] == "cbb-jm1-2026-27-20261111"
    assert SO.rollback_pointer(root)["ROLLBACK_MODEL_VERSION"] == "cbb-jm1-2026-27-20261107"
    assert [d.name for d in (root / "candidates").iterdir() if d.name.startswith(".candidate")] == []
    assert SO.versions(root) == [f"cbb-jm1-2026-27-2026{d}" for d in ("1101", "1104", "1107", "1111")]
    for v in SO.versions(root):
        assert SO.verify_version(root, v)["VERIFIED"]
    prev = SO.load_version(root, "cbb-jm1-2026-27-20261107")                        # rollback-capable
    assert prev.identity["model_kind"] == WC.DAILY_KIND
    rb = SO.rollback(root, "e2e test", "test")
    assert rb["ACTIVE_MODEL_VERSION"] == "cbb-jm1-2026-27-20261107"
    SO.promote(root, "cbb-jm1-2026-27-20261111", "restore after rollback test")


def test_predictions_carry_model_version_and_training_cutoff(tl):
    r = tl["pred"]
    assert r["predictions"] > 0
    preds = [p for p in LG.predictions(tl["root"] / "predictions" / "ledger.jsonl") if p["run_id"] == r["run_id"]]
    assert {p["model_version"] for p in preds} == {"cbb-jm1-2026-27-20261107"}
    assert {p["training_cutoff"] for p in preds} == {WC.refit_cutoff(D7).isoformat()}
    assert all(p["checkpoint"] == "TIP_MINUS_1H" and p["feature_cutoff"] and p["data_cutoff"] for p in preds)
    prov = pd.read_parquet(tl["root"] / "predictions" / "provenance.parquet")
    assert set(prov["model_version"]) == {"cbb-jm1-2026-27-20261107"} and prov["role_diagnostic"].notna().all()
    assert len(pd.read_parquet(tl["root"] / "predictions" / "pmfs.parquet")) > 0


def test_monitoring_is_attached_to_the_predicting_version(tl):
    r = tl["d11"]
    assert r["settlement"]["settled"] > 0                                            # scored BEFORE the 11-11 refit
    rep = json.loads((tl["root"] / "monitoring" / "reports" / f"{D11.isoformat()}.json").read_text())
    b = rep["windows"]["YESTERDAY"]["binary"]
    assert b["ALL"]["n"] > 0 and set(b["MODEL_VERSION"]) == {"cbb-jm1-2026-27-20261107"}
    assert all(k in b for k in ("STAT", "ROLE", "STAT_X_ROLE", "LINE", "LINE_RANGE", "CHECKPOINT"))
    assert b["ALL"]["brier"]["value"] is not None and b["ALL"]["log_loss"]["value"] is not None
    d = rep["windows"]["SEASON_TO_DATE"]["distribution"]
    assert d["ALL"]["n"] > 0 and set(d["MODEL_VERSION"]) == {"cbb-jm1-2026-27-20261107"}


def test_market_comparison_uses_the_exact_matched_line_and_timestamp(tl):
    root = tl["root"]
    st = pd.read_parquet(root / "monitoring" / "settlements.parquet")
    m = MON.market_frame(root, st)
    assert len(m) > 0
    q = SD.load(root / "predictions" / "side_monitor.jsonl")
    for r in m.itertuples(index=False):
        o = q[(q["run_id"] == r.run_id) & (q["game_id"] == r.game_id) & (q["player_id"] == r.player_id)
              & (q["market"] == r.market) & (q["line"] == r.line) & (q["sportsbook"] == r.sportsbook)
              & (q["quote_available_at"] == r.quote_available_at)]
        assert set(o["side"]) == {"OVER", "UNDER"}
        do, du = (o.loc[o["side"] == s_, "book_decimal_odds"].iat[0] for s_ in ("OVER", "UNDER"))
        assert np.isclose(r.p_market, (1 / do) / (1 / do + 1 / du))
    rep = json.loads((root / "monitoring" / "reports" / f"{D11.isoformat()}.json").read_text())
    mk = rep["windows"]["YESTERDAY"]["market_relative"]
    assert mk["ALL"]["n"] > 0 and "DELTA_BRIER" in mk["ALL"] and "DELTA_LOG_LOSS" in mk["ALL"] and mk["SPORTSBOOK"]


def test_rerun_is_idempotent(tl):
    a, b = tl["d4"], tl["d4_rerun"]
    assert b["DAILY_MODEL_PROMOTION"] in ("NOT_REQUIRED", "IDEMPOTENT_NO_CHANGE") and b["ACTIVE_MODEL_VERSION"] == a["ACTIVE_MODEL_VERSION"]
    c = tl["d11_rerun"]
    assert c["DAILY_MODEL_PROMOTION"] in ("NOT_REQUIRED", "IDEMPOTENT_NO_CHANGE") and c["settlement"]["settled"] == 0
    assert tl["idem"]["report_identical"] and tl["idem"]["settlements_before"] == tl["idem"]["settlements_after"]
    runs = SO.read_log(tl["root"] / "manifests" / "runs.jsonl")
    assert len({r["run_id"] for r in runs}) == len(runs)                              # no duplicated run record


def test_same_inputs_produce_byte_identical_artifacts(tl):
    assert tl["d4_fresh"]["DAILY_MODEL_PROMOTION"] == "PROMOTED"
    for f in SO.FILES.values():
        assert (SO.version_dir(tl["root"], D4) / f).read_bytes() == (SO.version_dir(tl["root2"], D4) / f).read_bytes(), f


def test_other_sports_models_untouched(tl):
    assert tl["root"].name == "cbb-originator"
    changed = subprocess.run(["git", "diff", "--name-only", "production/cbb-2026-27-readiness"], cwd=C.PROJECT_ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=C.PROJECT_ROOT,
                               capture_output=True, text=True, check=True).stdout.split()
    for p in changed + untracked:
        assert p.startswith(("cbb_platform/", "tests/", "docs/")) or p in (
            "data/qa/game_identity_questionable_only.csv", ".github/workflows/cbb-production-ci.yml"), p
        assert not any(x in p.lower() for x in ("massey", "nba", "nfl", "ncaaf")), p
