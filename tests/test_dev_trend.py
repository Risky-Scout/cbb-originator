"""DV1 returning-player development (prod.dev_trend, promoted 2026-11-25): frozen TRAIN constants, the replay formula."""
import json

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import dev_trend as DV, engine as ENG


def test_constants_are_the_declared_train_fit():
    fit = json.loads((C.PROJECT_ROOT / "docs/audits/2026-11-25-defects/results/p1_train_fit.json").read_text())["DV1"]
    assert DV.DELTA == {s: (fit[f"{s}|1"]["delta"], fit[f"{s}|0"]["delta"]) for s in DV.DELTA}
    assert DV.W == (0.5, 0.25, 0.125) and DV.K_MINUTES == 31.0 and DV.enabled()
    assert DV.DELTA["PTS"][0] > 0.05 and abs(DV.DELTA["REB"][0]) < 0.01        # offensive development, not rebounding


def _frames():
    cand = pd.DataFrame({"player_game_example_id": ["a", "b", "c"], "cur_min": [0.0, 300.0, 100.0],
                         "h1_min": [800.0, 0.0, 600.0], "h2_min": [400.0, 0.0, np.nan], "h3_min": [0.0, 0.0, 0.0],
                         "pps_same_team": [1.0, np.nan, 0.0]})
    rows = pd.DataFrame({"player_game_example_id": ["c", "a", "b"], **{f"{s}_mu": [1.0, 2.0, 3.0] for s in DV.DELTA},
                         "min_mu": [30.0, 20.0, 8.0]})
    return rows, cand


def test_formula_and_example_id_matching():
    rows, cand = _frames()
    s = DV.prior_share(cand)
    assert np.allclose(s, [500 / (500 + 31), 0.0, 300 / (100 + 300 + 31)], rtol=0, atol=1e-15)
    out, rec = DV.apply(rows, cand)
    for st, (same, other) in DV.DELTA.items():
        exp = np.array([1.0 * (1 + other * s[2]), 2.0 * (1 + same * s[0]), 3.0])   # rows order c, a, b; b has no prior
        assert np.allclose(out[f"{st}_mu"], exp, rtol=0, atol=1e-15)
    assert out["min_mu"].equals(rows["min_mu"]) and rec["rows"] == 3


def test_disabled_is_identity_and_names_itself_in_the_engine(monkeypatch):
    assert "+DEV1#" in ENG.engine_id()
    rows, cand = _frames()
    monkeypatch.setattr(DV, "ENABLED", False)
    out, _ = DV.apply(rows, cand)
    assert out is rows and "DEV1" not in ENG.engine_id()
