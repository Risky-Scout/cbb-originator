"""CI-safe unit tests of the walk-forward layer (labeled synthetic FIXTURES only; no frozen local store needed):
training eligibility rules, point-in-time state selection, the version store (atomic promotion, immutability, tamper
detection, rollback), monitoring arithmetic (Brier / log loss / no-vig market / CLV, version attribution), the
DST-correct cutoff and the CBB-only namespace.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import fixtures as FX, ledger as LG, side_monitor as SD, state as ST
from cbb_platform.walkforward import config as WC, eligibility as EL, monitor as MON, store as SO

CUT = WC.refit_cutoff(date(2026, 11, 7))                         # 2026-11-07 06:00 EST = 11:00Z


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    return FX.synthetic_history(tmp_path_factory.mktemp("h") / "history.duckdb")


def _sched(games: pd.DataFrame, status: dict) -> pd.DataFrame:
    return pd.DataFrame({"canonical_game_id": games["canonical_game_id"], "provider_game_id": range(len(games)),
                         "season": 2027, "status": [status.get(g, "final" if pd.notna(p) else "scheduled")
                                                    for g, p in zip(games["canonical_game_id"], games["home_points"])],
                         "start_time": games["scheduled_tip_utc"], "tip_time_quality": "EXACT",
                         "home_team_id": games["home_team_id"], "away_team_id": games["away_team_id"],
                         "source_received_at": CUT - pd.Timedelta(hours=2), "listed_in_latest_schedule": True,
                         "schedule_as_of": CUT - pd.Timedelta(hours=2)})


@pytest.fixture(scope="module")
def elig(hist, tmp_path_factory):
    rows, meta = FX.increment_rows(hist)
    g = rows["flagship.games"].copy()
    done = g[g["home_points"].notna()].sort_values(["scheduled_tip_utc", "canonical_game_id"])["canonical_game_id"].tolist()
    A, Bx, Cx, Dx, E, F = done
    pb, tb = rows["flagship.player_game_outcomes"].copy(), rows["flagship.team_game_outcomes"].copy()
    away_b = g.loc[g["canonical_game_id"] == Bx, "away_team_id"].iat[0]
    pb = pb[~((pb["canonical_game_id"] == Bx) & (pb["canonical_team_id"] == away_b))]           # B: half a box
    i = pb.index[pb["canonical_game_id"] == Cx][0]
    pb.loc[i, ["canonical_player_id", "identity_status"]] = [None, "UNRESOLVED_NO_MATCH"]       # C: identity
    j = pb.index[pb["canonical_game_id"] == E][0]
    pb.loc[j, "points"] = pb.loc[j, "points"] + 1                                               # E: box != score
    g.loc[g["canonical_game_id"] == Dx, ["home_points", "away_points"]] = [None, None]          # D: not final yet
    for rel in ("flagship.team_game_outcomes", "flagship.substitutions"):
        rows[rel] = rows[rel][rows[rel]["canonical_game_id"] != Dx]
    pb = pb[pb["canonical_game_id"] != Dx]
    nondi = FX._game(FX._gid("nondi-played"), 2027, FX.ROUND2_TIP, meta["teams"][0], "T-00000000fffffff1",
                     universe="DI_VS_NON_DI", hp=70, ap=50)
    canc = FX._game(FX._gid("cancelled"), 2027, FX.ROUND2_TIP, meta["teams"][1], meta["teams"][2])
    g = pd.concat([g, pd.DataFrame([nondi, canc])], ignore_index=True)
    rows.update({"flagship.games": g, "flagship.player_game_outcomes": pb, "flagship.team_game_outcomes": rows["flagship.team_game_outcomes"],
                 "metadata.prod_schedule": _sched(g, {F: "in_progress", canc["canonical_game_id"]: "canceled"})})
    root = tmp_path_factory.mktemp("st")
    ST.update(rows, CUT - pd.Timedelta(hours=2), FX.FIXTURE_PROVIDER, meta["preseason_evidence"], root,
              created_at=CUT - pd.Timedelta(hours=2))
    late = {k: v for k, v in rows.items()}
    ST.update(late | {"core.players": pd.DataFrame([{"canonical_player_id": "P-00000000000late1", "display_name": "x"}])},
              CUT + pd.Timedelta(hours=1), FX.FIXTURE_PROVIDER, meta["preseason_evidence"], root,
              created_at=CUT + pd.Timedelta(hours=1))
    st = EL.state_asof(root, CUT)
    av = EL.outcome_available_at(root, CUT)
    return {"root": root, "st": st, "el": EL.evaluate(st, CUT, av), "ids": dict(A=A, B=Bx, C=Cx, D=Dx, E=E, F=F,
            NONDI=nondi["canonical_game_id"], CANC=canc["canonical_game_id"]), "av": av}


def _dec(el, gid):
    r = el[el["game_id"] == gid].iloc[0]
    return r["decision"], r["reason"]


def test_state_asof_never_uses_a_state_created_after_the_cutoff(elig):
    vs = EL.state_versions(elig["root"])
    assert len(vs) == 2 and vs[1]["created_at"] > CUT
    assert elig["st"]["state_version"] == vs[0]["state_version"]
    assert all(pd.Timestamp(v["available_at"]) <= CUT for k, v in elig["av"].items() if k != "__scanned__")


def test_eligibility_decisions(elig):
    el, i = elig["el"], elig["ids"]
    assert _dec(el, i["A"]) == ("INCLUDED", "")
    assert _dec(el, i["B"]) == ("DEFERRED", "BOX_INCOMPLETE")
    assert _dec(el, i["C"]) == ("DEFERRED", "IDENTITY_UNRESOLVED")
    assert _dec(el, i["D"]) == ("DEFERRED", "NOT_FINAL_AT_CUTOFF")             # in progress / no final result
    assert _dec(el, i["E"]) == ("DEFERRED", "BOX_UNRECONCILED")
    assert _dec(el, i["F"]) == ("DEFERRED", "STATUS_NOT_FINAL")
    assert _dec(el, i["NONDI"]) == ("EXCLUDED", "NOT_IN_TRAINING_UNIVERSE")
    assert _dec(el, i["CANC"]) == ("EXCLUDED", "NOT_PLAYED")
    assert (el["scheduled_tip_utc"] < CUT).all()                                # upcoming games are never expected
    s = EL.summary(el)
    assert s["EXPECTED_GAMES"] == 8 and s["INCLUDED_GAMES"] == 1 and s["DEFERRED_GAMES"] == 5 and s["EXCLUDED_GAMES"] == 2
    assert s["EXCLUSION_REASON"]["DEFERRED:BOX_INCOMPLETE"] == 1


def test_outcome_availability_is_the_arrival_time_not_the_hr1_rule(elig):
    av = dict(elig["av"])
    a = elig["ids"]["A"]
    av[a] = {"available_at": (CUT + pd.Timedelta(minutes=1)).isoformat(), "state_version": "later"}
    el = EL.evaluate(elig["st"], CUT, av)
    assert el.loc[el["game_id"] == a, "knowable_at"].iat[0] <= CUT          # HR-1 says knowable ...
    assert _dec(el, a) == ("DEFERRED", "NOT_KNOWABLE_AT_CUTOFF")            # ... but it had not arrived at the cutoff


# ============================================================================ store
def _daily_copy(root: Path, day: date) -> str:
    """A daily-version-shaped copy of the opening artifacts (store mechanics only)."""
    op = SO.version_dir(root, WC.OPENING_DATE)
    vid = WC.version_id(day)
    cand = SO.new_candidate_dir(root, day)
    sha = {k: SO.write_bytes(cand / f, (op / f).read_bytes()) for k, f in SO.FILES.items()}
    man = json.loads((op / SO.MANIFEST).read_text()) | {"MODEL_VERSION": vid, "MODEL_KIND": WC.DAILY_KIND,
                                                         "TRAINING_CUTOFF": WC.refit_cutoff(day).isoformat(),
                                                         "artifacts": {k: {"file": SO.FILES[k], "sha256": v} for k, v in sha.items()}}
    SO.write_manifest(cand, man)
    SO.finalize(root, cand, day)
    return vid


def test_store_opening_promotion_rollback_and_immutability(tmp_path):
    root = tmp_path / "cbb-originator"
    r = SO.register_opening(root)
    op = WC.version_id(WC.OPENING_DATE)
    assert r["VERIFIED"] and SO.active(root)["ACTIVE_MODEL_VERSION"] == op
    assert SO.register_opening(root)["registered"] is False                     # idempotent
    v = _daily_copy(root, date(2026, 11, 2))
    SO.promote(root, v, "test")
    assert SO.active(root)["ACTIVE_MODEL_VERSION"] == v
    assert SO.rollback_pointer(root)["ROLLBACK_MODEL_VERSION"] == op
    assert not [p for p in (root / "active").iterdir() if p.name.startswith(".")]   # no partial pointer left behind
    with pytest.raises(SO.StoreError, match="immutable"):
        SO.finalize(root, SO.new_candidate_dir(root, date(2026, 11, 2)), date(2026, 11, 2))
    m = SO.load_active(root)
    assert m.identity["model_version"] == v and m.identity["model_kind"] == WC.DAILY_KIND
    SO.rollback(root, "test rollback", "architect")
    assert SO.active(root)["ACTIVE_MODEL_VERSION"] == op                        # pointer change only
    log = SO.read_log(root / "manifests" / "promotion_log.jsonl")
    assert [x["event"] for x in log] == ["PROMOTE", "PROMOTE", "ROLLBACK"]


def test_store_detects_tampering(tmp_path):
    root = tmp_path / "cbb-originator"
    SO.register_opening(root)
    v = _daily_copy(root, date(2026, 11, 3))
    f = SO.version_dir(root, date(2026, 11, 3)) / SO.FILES["residual_j2"]
    f.write_text(f.read_text().replace("J2", "J3", 1))
    with pytest.raises(SO.StoreError, match="integrity"):
        SO.verify_version(root, v)
    with pytest.raises(SO.StoreError):
        SO.promote(root, v, "must refuse")
    assert SO.active(root)["ACTIVE_MODEL_VERSION"] == WC.version_id(WC.OPENING_DATE)


def test_dst_correct_cutoff_and_namespace():
    assert WC.refit_cutoff(date(2026, 11, 1)) == pd.Timestamp("2026-11-01T11:00:00Z")     # EST (DST ended 02:00)
    assert WC.refit_cutoff(date(2026, 10, 31)) == pd.Timestamp("2026-10-31T10:00:00Z")    # EDT
    assert WC.refit_cutoff(date(2027, 3, 15)) == pd.Timestamp("2027-03-15T10:00:00Z")     # EDT again
    assert WC.STORE_ROOT.name == "cbb-originator"
    assert WC.AUTOMATIC_ARCHITECTURE_CHANGES is False and WC.AUTOMATIC_CHALLENGER_PROMOTION is False


# ============================================================================ monitoring arithmetic
def test_monitor_scores_attach_to_the_predicting_version(tmp_path):
    root = tmp_path / "cbb-originator"
    SO.ensure(root)
    pp = MON._p(root)
    cut = pd.Timestamp("2026-11-05T23:00:00Z")
    prov = pd.DataFrame([{"prediction_id": f"p{i}", "run_id": "R1", "feature_cutoff": str(cut), "model_version": "VA",
                          "training_cutoff": "TA", "game_id": f"G{i % 2}", "player_id": f"P{i}", "market": "PTS",
                          "line": 10.5, "side": "OVER", "probability": p, "p_nonpush": 1.0, "role_diagnostic": "TOP5_MINUTES",
                          "checkpoint": WC.CHECKPOINT} for i, p in enumerate((0.7, 0.4, 0.6, 0.2))])
    prov.to_parquet(pp["provenance"], index=False)
    y = [1.0, 0.0, 0.0, 0.0]
    pd.DataFrame([{"prediction_id": f"p{i}", "result": "WIN" if v else "LOSS", "side_win": v, "actual": 12.0 if v else 8.0,
                   "brier": 0.0, "log_loss": 0.0, "settled_at": "x"} for i, v in enumerate(y)]).to_parquet(pp["settled"], index=False)
    rep = MON.report(root, date(2026, 11, 6))
    b = rep["windows"]["YESTERDAY"]["binary"]
    p = np.array([0.7, 0.4, 0.6, 0.2])
    assert b["ALL"]["n"] == 4 and b["ALL"]["games"] == 2
    assert np.isclose(b["ALL"]["brier"]["value"], np.mean((p - y) ** 2))
    assert np.isclose(b["ALL"]["log_loss"]["value"], np.mean(-(np.array(y) * np.log(p) + (1 - np.array(y)) * np.log(1 - p))))
    assert np.isclose(b["ALL"]["calibration_gap"]["value"], p.mean() - np.mean(y))
    assert list(b["MODEL_VERSION"]) == ["VA"] and "PTS|TOP5_MINUTES" in b["STAT_X_ROLE"]
    assert rep["windows"]["ROLLING_7_DAY"]["binary"]["ALL"]["n"] == 4
    assert MON.report(root, date(2026, 11, 5))["windows"]["SEASON_TO_DATE"]["binary"]["ALL"]["n"] == 0   # not yet played
    assert rep["windows"]["YESTERDAY"]["market_relative"]["ALL"]["status"] == "BLOCKED_NO_QUOTES"


def test_market_no_vig_and_clv_math():
    d = pd.DataFrame({"game_id": ["G1", "G2"], "p_model": [0.6, 0.3], "p_market": [0.5, 0.4], "y": [1.0, 0.0]})
    m = MON.market_block(d)
    assert np.isclose(m["MODEL_BRIER"]["value"], ((0.4 ** 2) + (0.3 ** 2)) / 2)
    assert np.isclose(m["DELTA_BRIER"]["value"], ((0.4 ** 2 + 0.3 ** 2) - (0.5 ** 2 + 0.4 ** 2)) / 2)
    do, du = 1.87, 1.95                                                   # no-vig: (1/do) / (1/do + 1/du)
    assert np.isclose((1 / do) / (1 / do + 1 / du), du / (do + du))
    sides = pd.DataFrame([{"game_id": "G1", "player_id": "P1", "market": "PTS", "line": 10.5, "side": "OVER",
                           "sportsbook": "bk", "book_decimal_odds": 2.0}])
    assert MON.clv(sides, None)["status"] == "BLOCKED_NO_CLOSE_QUOTES"
    c = MON.clv(sides, sides.assign(closing_decimal_odds=1.8).drop(columns="book_decimal_odds"))
    assert c["status"] == "OK" and np.isclose(c["clv"]["value"], 2.0 / 1.8 - 1)
