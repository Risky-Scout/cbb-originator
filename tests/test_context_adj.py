"""OT1 + OC2 team / opponent context (prod.context_adj, promoted 2026-11-25): TRAIN constants, point-in-time factors,
fail-safe ledger, incremental ledger update."""
import json

import numpy as np
import pandas as pd
import pytest

from cbb_platform import config as C
from cbb_platform.prod import context_adj as CX, engine as ENG


def test_constants_are_the_declared_train_fit():
    fit = json.loads((C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results/p1_train_fit.json").read_text())
    assert CX.KAPPA_TEAM == {s: fit["OT1"][s]["kappa"] for s in CX.STATS}
    assert CX.KAPPA_OPP == {s: (float("inf") if s == "PTS" else fit["OC1"][s]["kappa"]) for s in CX.STATS}   # OC2: no PTS
    assert CX.LEDGER_SIMS == 2000 and CX.enabled()


def _ledger():
    return pd.DataFrame([("g1", "2026-11-05", "A", "B", "REB", 40.0, 30.0), ("g1", "2026-11-05", "B", "A", "REB", 20.0, 30.0),
                         ("g2", "2026-11-09", "A", "C", "REB", 10.0, 30.0), ("g2", "2026-11-09", "C", "A", "REB", 30.0, 30.0)],
                        columns=CX.COLS)


def test_factors_use_only_strictly_earlier_dates_and_the_formula():
    rows = pd.DataFrame({"team_id": ["A", "C", "A"], "prediction_cutoff_utc": ["2026-11-09T23:00:00Z"] * 2 + ["2026-11-12T23:00:00Z"],
                         **{f"{s}_mu": [1.0, 1.0, 1.0] for s in CX.STATS}})
    f = CX.factors(rows, np.array(["C", "A", "B"]), _ledger())
    kt, ko = CX.KAPPA_TEAM["REB"], CX.KAPPA_OPP["REB"]
    # row 0: team A on 11-09 sees only 11-05 (A 40/30); opponent C has no earlier game
    assert f["REB"][0] == pytest.approx((40 + kt) / (30 + kt))
    # row 1: team C no earlier game; opponent A: rows of teams that FACED A before 11-09 -> B on 11-05 (20/30)
    assert f["REB"][1] == pytest.approx((20 + ko) / (30 + ko))
    # row 2 (11-12): team A both games (50/60); opponent B: players who faced B -> A on 11-05 (40/30)
    assert f["REB"][2] == pytest.approx((50 + kt) / (60 + kt) * (40 + ko) / (30 + ko))
    assert np.all(f["PTS"] == 1.0) and np.all(f["AST"] == 1.0)        # no PTS opponent factor; no AST rows in ledger


def test_missing_or_stale_ledger_is_identity_with_a_status(tmp_path):
    rows = pd.DataFrame({"team_id": ["A"], "prediction_cutoff_utc": ["2026-11-09T23:00:00Z"], **{f"{s}_mu": [2.0] for s in CX.STATS}})
    led, st = CX.load_ledger(tmp_path, 2027, "2026-11-09T12:00:00Z")
    assert led is None and st == "LEDGER_UNAVAILABLE"
    out, rec = CX.apply(rows, np.array(["B"]), led, st)
    assert out is rows and rec["applied"] is False and rec["status"] == "LEDGER_UNAVAILABLE"
    p = CX.ledger_path(tmp_path, 2027); p.parent.mkdir(parents=True)
    _ledger().to_parquet(p, index=False)
    p.with_suffix(".json").write_text(json.dumps({"updated_at": "2026-11-01T00:00:00+00:00"}))
    assert CX.load_ledger(tmp_path, 2027, "2026-11-09T12:00:00Z")[1] == "LEDGER_STALE"
    p.with_suffix(".json").write_text(json.dumps({"updated_at": "2026-11-09T06:00:00+00:00"}))
    led, st = CX.load_ledger(tmp_path, 2027, pd.Timestamp("2026-11-09T12:00:00"))
    assert st == "OK" and len(led) == 4


def test_update_ledger_is_incremental_and_rebuilds_on_a_new_incumbent(tmp_path, monkeypatch):
    calls = []
    def fake_rows(oos, frame, team, model, v0, games):
        calls.append(list(games))
        return pd.DataFrame([(g, "2026-11-05", "A", "B", "REB", 1.0, 1.0) for g in games], columns=CX.COLS)
    monkeypatch.setattr(CX, "ledger_rows", fake_rows)
    oos = pd.DataFrame({"game_id": ["g1", "g2", "g3"], "y_min": [10.0, 5.0, np.nan]})
    ts = {"frames": {"oos": oos, "frame": pd.DataFrame(), "team": pd.DataFrame()}}
    r1 = CX.update_ledger(tmp_path, 2027, ts, None, {}, "tagA", log=lambda *_: None)
    r2 = CX.update_ledger(tmp_path, 2027, ts, None, {}, "tagA", log=lambda *_: None)
    r3 = CX.update_ledger(tmp_path, 2027, ts, None, {}, "tagB", log=lambda *_: None)
    assert calls == [["g1", "g2"], ["g1", "g2"]] and r1["added_games"] == 2 and r2["added_games"] == 0
    assert r3["games"] == 2 and r3["incumbent"] == "tagB"
    assert CX.update_ledger(tmp_path, 2027, {"frames": None}, None, {}, "tagB")["status"] == "NO_FRAMES"


def test_engine_names_the_correction_and_the_ledger_incumbent(monkeypatch):
    assert "+DEV1+CTX1+" in ENG.engine_id()
    # Stage 06: the incumbent includes PS2 (simulate_game applies it inside the ledger counterfactual). a4b0cb644c60 was
    # the pre-PS2 incumbent the context kappas were selected against; the tag change forces one consistent rebuild.
    assert ENG.pre_context_tag() == "af49d60f2175"
    from cbb_platform.prod import minutes_state as MS2
    monkeypatch.setattr(MS2, "ENABLED", False)
    assert ENG.pre_context_tag() == "a4b0cb644c60"                 # without PS2: the pre-Stage-06 incumbent
    monkeypatch.setattr(MS2, "ENABLED", True)
    monkeypatch.setattr(CX, "ENABLED", False)
    assert "CTX1" not in ENG.engine_id()
