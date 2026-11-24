"""prod.rate_a2 (Challenger A2, promoted 2026-11-22 for AST / FG3M / STL, REB 2026-11-24): exact formula, pinned constants, wiring."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import rate_a2 as RA2

RES = C.PROJECT_ROOT / "docs" / "audits" / "2026-11-22-causal" / "results"
STATE = {"prior_k_minutes": {"ast": 60.0, "three_pm": 70.0, "stl": 255.0, "reb": 45.0},
         "prior_rate": {"ast": {"G": 0.08, "F": 0.045, "C": 0.044, "U": 0.07},
                        "three_pm": {"G": 0.06, "F": 0.04, "C": 0.01, "U": 0.05},
                        "stl": {"G": 0.04, "F": 0.03, "C": 0.025, "U": 0.035},
                        "reb": {"G": 0.12, "F": 0.2, "C": 0.25, "U": 0.15}}}


def _cand():
    rng = np.random.default_rng(7)
    n = 40
    d = pd.DataFrame({"player_game_example_id": [f"E{i}" for i in range(n)], "pos": rng.choice(list("GFCU"), n),
                      "cur_min": rng.uniform(0, 300, n), "h1_min": rng.uniform(0, 900, n), "h2_min": rng.uniform(0, 900, n),
                      "h3_min": rng.uniform(0, 900, n), "h1_n_app": rng.integers(0, 30, n).astype(float)})
    for t in ("ast", "three_pm", "stl", "reb"):
        for c in ("cur", "h1", "h2", "h3"):
            d[f"{c}_{t}"] = d[f"{c}_min" if c != "cur" else "cur_min"] * rng.uniform(0.01, 0.1, n)
    d.loc[0, ["cur_min", "h1_min", "h2_min", "h3_min", "cur_ast", "h1_ast", "h2_ast", "h3_ast"]] = 0.0   # first appearance
    d.loc[1, "h1_n_app"] = np.nan
    return d


def _reference(d, mm, s):
    """The research formula (docs/audits/2026-11-22-causal/scripts/e_live.py), written independently."""
    t, al, be, ga, sc = RA2.PARAMS[s]
    W = np.array([0.5, 0.25, 0.125])
    Hm = d.cur_min.fillna(0).to_numpy() + sum(w * d[f"h{j}_min"].fillna(0).to_numpy() for j, w in zip((1, 2, 3), W))
    mpa = d.h1_min.to_numpy() / np.maximum(d.h1_n_app.fillna(0).to_numpy(), 1)
    D = np.where(d.h1_n_app.fillna(0).to_numpy() >= 5, np.clip(np.log(np.maximum(mm, 1) / np.maximum(mpa, 1)), -1.5, 1.5), 0.0)
    z = np.where(Hm > 0, np.log(np.maximum(mm, 1) / 20.0), 0.0)
    h = np.column_stack([d[f"h{j}_{t}"].fillna(0).to_numpy() for j in (1, 2, 3)])
    k, prior = STATE["prior_k_minutes"][t], d.pos.map(STATE["prior_rate"][t]).to_numpy()
    r = (d[f"cur_{t}"].fillna(0).to_numpy() + np.exp(be * D) * (h * (W * np.exp(al * np.arange(1, 4)))).sum(1)
         + k * prior * np.exp(ga * z)) / (Hm + k)
    return np.maximum(mm * r * sc, 0.01)


def test_apply_reproduces_the_research_formula_and_touches_only_its_stats():
    d = _cand()
    mm = np.linspace(2, 38, len(d))
    rows = pd.DataFrame({"player_game_example_id": d.player_game_example_id[::-1].to_numpy(), "min_mu": mm[::-1],
                         **{f"{s}_mu": np.full(len(d), 1.5) for s in ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV")}})
    out, rec = RA2.apply(rows, d, {"mean_floor": 0.01}, STATE)
    for s in RA2.PARAMS:
        ref = pd.Series(_reference(d, mm, s), index=d.player_game_example_id)
        assert np.allclose(out[f"{s}_mu"].to_numpy(), ref.loc[out.player_game_example_id].to_numpy(), rtol=0, atol=1e-12)
    for s in RA2.NOT_ADJUSTED:
        assert (out[f"{s}_mu"] == 1.5).all()
    assert rec["id"] == RA2.ID and set(rec["mean_ratio"]) == {"AST", "FG3M", "STL", "REB"}
    assert list(out.player_game_example_id) == list(rows.player_game_example_id)        # row order preserved


def test_first_appearance_uses_the_incumbent_position_prior():
    d = _cand().iloc[:1]
    r = RA2.rates(d, np.array([35.0]), STATE)["AST"]
    assert np.isclose(r[0], STATE["prior_rate"]["ast"][d.pos.iloc[0]])                # z = 0, no history


def test_constants_are_the_frozen_f2024_f2025_fit():
    f = json.loads((RES / "challenger_a2_holdout.json").read_text())
    live = json.loads((RES / "challenger_a2_live_2026_27.json").read_text())
    assert f["train"] == [2024, 2025] and f["eval"] == 2026
    for s, (_, al, be, ga, sc) in RA2.PARAMS.items():
        a = f["stats"][s]["fit"]["A_full"]
        assert (al, be, ga) == (a["alpha"], a["beta"], a["gamma"])
        assert sc == live["stats"][s]["LA"] / live["stats"][s]["L0"]
    assert set(RA2.PARAMS) | set(RA2.NOT_ADJUSTED) == {"PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"}


def test_scoring_path_applies_it_right_after_marginal_rows():
    src = (C.PROJECT_ROOT / "cbb_platform" / "prod" / "run.py").read_text()
    i = src.index("rows = IN.marginal_rows(cand")
    j = src.index("RA2.apply(rows, cand, model.marginal_cfg, model.v0_state)")
    assert i < j < src.index("rows, aflags = IN.apply_availability(rows, availability)")
    tr = (C.PROJECT_ROOT / "cbb_platform" / "walkforward" / "training_rows.py").read_text()
    assert "rate_a2" not in tr and "RA2" not in tr                                    # training rows unchanged
