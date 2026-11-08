"""walkforward.fit inputs: one component group at a time (Droplet MemoryMax), frames identical to the former
all-at-once concatenation (history rows then 2026-27 rows; the team frame re-sorted), nothing retained. Synthetic."""
import numpy as np
import pandas as pd
import pytest

from cbb_platform.walkforward import config as WC, fit as WF


def _hist():
    r = np.random.default_rng(3)
    f = pd.DataFrame({"season": r.integers(2014, 2027, 50), "x": r.normal(size=50), "s": [f"p{i}" for i in range(50)]})
    team = WF._sort_team(pd.DataFrame({"season": r.integers(2014, 2027, 30), "game_id": [f"g{i % 7}" for i in range(30)],
                                       "team_id": [f"t{i}" for i in range(30)], "v": r.normal(size=30)}))
    return {"frame": f, "finals": f.iloc[:10].reset_index(drop=True), "part": f.iloc[:20].reset_index(drop=True),
            "team": team, "store": pd.DataFrame({"forecast_season": [2023, 2024], "y": [1.0, 2.0]})}


def _new():
    return {"frame": pd.DataFrame({"season": [2027, 2027], "x": [0.5, 0.25], "s": ["n1", "n2"]}),
            "finals": pd.DataFrame({"season": [2027], "x": [1.0], "s": ["n1"]}),
            "part": pd.DataFrame({"season": [2027], "x": [2.0], "s": ["n3"]}),
            "team": pd.DataFrame({"season": [2027, 2027], "game_id": ["a", "a"], "team_id": ["z", "b"], "v": [1.0, 2.0]}),
            "oos": pd.DataFrame({"forecast_season": [2027], "y": [3.0]})}


def _old_inputs(h, new):
    has_new = new is not None and len(new["frame"]) > 0
    cat = lambda k: pd.concat([h[k], new[k]], ignore_index=True) if has_new else h[k]   # noqa: E731
    return {"T": WC.PRODUCTION_SEASON if has_new else WC.OPENING_THROUGH, "frame": cat("frame"), "finals": cat("finals"),
            "part": cat("part"), "team": WF._sort_team(cat("team")) if has_new else h["team"],
            "store": pd.concat([h["store"], new["oos"]], ignore_index=True) if has_new else h["store"]}


@pytest.mark.parametrize("with_new", [True, False])
def test_component_inputs_equal_the_former_concatenation(monkeypatch, with_new):
    loads = []

    def load(cfg, g):
        loads.append(g)
        h = _hist()
        return {k: h[k] for k in WF.GROUPS[g]}
    monkeypatch.setattr(WF, "_load", load)
    monkeypatch.setattr(WF, "_HIST", {})
    new = _new() if with_new else None
    ref = _old_inputs(_hist(), new)
    x = WF.inputs({}, new)
    assert x["T"] == ref["T"]
    for k in ("frame", "finals", "part", "team", "store"):
        pd.testing.assert_frame_equal(x.pop(k), ref[k], check_exact=True)
    assert loads == ["frames", "part", "team", "store"]           # each group read once, when its component needs it
    assert x._ready == {}                                         # nothing retained after hand-over
    with pytest.raises(KeyError):
        x["frame"]


def test_history_cache_is_used_when_filled_and_never_mutated(monkeypatch):
    h = _hist()
    monkeypatch.setattr(WF, "_HIST", {**h, "source": "CONTROL"})
    monkeypatch.setattr(WF, "_load", lambda cfg, g: pytest.fail("must not reload a cached group"))
    x = WF.inputs({}, _new())
    got = x.pop("frame")
    assert len(got) == len(h["frame"]) + 2 and WF._HIST["frame"] is h["frame"] and len(WF._HIST["frame"]) == 50


def test_non_production_season_new_rows_refused():
    bad = _new()
    bad["frame"].loc[0, "season"] = 2026
    with pytest.raises(WF.WalkForwardFitError):
        WF.inputs({}, bad)
