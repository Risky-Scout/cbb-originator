"""Quote-tick scoring uses THE production scorer model (model_source.scoring_model), the same rule as the lifecycle
forecasts: the ACTIVE walk-forward version, else the frozen opening artifacts. 2026-11-24 defect: ticks called
run.daily without a model and scored near-tip PMFs with the frozen through-2025-26 artifacts."""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.prod import lifecycle as L, loader as LD, model_source as MS, morning_stages as MST, run as RN
from cbb_platform.prod import shadow_tick as TK
from cbb_platform.walkforward import config as WC


class _Stop(Exception):
    pass


def test_scoring_model_rule(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(LD, "load", lambda paths=None: calls.append(("FROZEN", paths)) or "frozen")
    monkeypatch.setattr(MS, "resolve", lambda src, aso, root: calls.append((src, root)) or "active")
    assert MS.scoring_model("2026-11-24T00:00Z", None, {"x": 1}) == "frozen"          # no store root: frozen only
    assert MS.scoring_model("2026-11-24T00:00Z", tmp_path) == "active"
    assert calls[-1] == ("ACTIVE_DAILY", tmp_path)

    def unavailable(*a):
        raise MS.ModelSourceUnavailable("ACTIVE_DAILY", "no ACTIVE pointer")
    monkeypatch.setattr(MS, "resolve", unavailable)
    assert MS.scoring_model("2026-11-24T00:00Z", tmp_path, {"x": 1}) == "frozen"       # fallback = lifecycle rule
    assert calls[-1] == ("FROZEN", {"x": 1})


def test_lifecycle_model_uses_the_shared_rule(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(MS, "scoring_model", lambda aso, root, paths=None: seen.append((aso, root, paths)) or "m")
    life = L.Life(model_root=tmp_path) if "model_root" in inspect.signature(L.Life).parameters else None
    if life is None:
        pytest.skip("Life has no model_root")
    assert L._model(life, pd.Timestamp("2026-11-24T00:00Z")) == "m"
    assert seen[-1][1] == Path(tmp_path)


def test_tick_scoring_passes_the_scoring_model(monkeypatch, tmp_path):
    root = tmp_path / "wf"
    ctx = TK.Ctx(ops_root=tmp_path / "ops", prod_root=tmp_path / "prod", model_root=root,
                 clock=lambda: pd.Timestamp("2026-11-24T00:00Z"))
    seen = {}
    monkeypatch.setattr(TK, "_validation_state", lambda c, now: {"passed": False, "failed": False})
    monkeypatch.setattr(TK, "step_events", lambda *a, **k: None)
    monkeypatch.setattr(MS, "scoring_model", lambda aso, r, paths=None: seen.update(aso=aso, root=r) or "ACTIVE_MODEL")

    def daily(*a, **k):
        seen["model"] = k.get("model")
        raise _Stop
    monkeypatch.setattr(RN, "daily", daily)
    due = pd.DataFrame({"canonical_game_id": ["G-1"], "cutoff": [pd.Timestamp("2026-11-24T01:00Z")],
                        "scheduled_tip_utc": [pd.Timestamp("2026-11-24T02:00Z")], "tip_time_quality": ["EXACT"]})
    with pytest.raises(_Stop):
        TK.step_score(ctx, {"score": due, "schedule": None}, {}, {}, tmp_path)
    assert seen["model"] == "ACTIVE_MODEL" and seen["root"] == root


def test_production_entry_points_set_the_store_root():
    assert "model_root=WC.STORE_ROOT" in inspect.getsource(TK.main)
    assert "model_root=_model_root(life)" in inspect.getsource(MST.run_prep)
