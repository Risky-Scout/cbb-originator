"""GC3 component carryover (game model 1.1.0, 2026-11-25): sealed prior, bit-identical default, protected margin strength."""
import hashlib

import numpy as np

from cbb_platform.game import forecast as GF, params as GP, ratings as GR


def _r():
    rng = np.random.default_rng(0)
    return GR.Ratings(["a", "b", "c"], rng.normal(0, 5, 3), rng.normal(0, 5, 3), rng.normal(0, 3, 3), 108.0, 2.0, 68.0, {"state_id": "x"})


def test_sealed_prior_is_tp1p_on_the_gc3_construction():
    """Game model 1.2.0 (2026-11-26, ROOT_CAUSE_PROTOCOL TP1 / TP1P): margin strength = TP1P preseason roster prior;
    scoring level (off + def) and pace = the GC3 prior exactly (totals protected)."""
    import pandas as pd
    gm = GP.game_model()
    e = gm["ratings_prior"]["2027"]
    assert gm["model_version"] == "cbb-game-model-1.2.0" and gm["ratings"]["carry_level"] == 0.6 and gm["ratings"]["carry_pace"] == 0.6
    assert e["file"] == "ratings_prior_2027_tp1p.parquet" and e["replaces"].startswith("ratings_prior_2027_gc3.parquet 05f4e80a0149")
    assert hashlib.sha256((GP.ROOT / e["file"]).read_bytes()).hexdigest() == e["sha256"]
    assert GF.prior(2027).meta["state_id"] == e["state_id"]
    gc3 = pd.read_parquet(GP.ROOT / "ratings_prior_2027_gc3.parquet")
    tp = pd.read_parquet(GP.ROOT / e["file"])
    assert list(gc3.team_id) == list(tp.team_id) and np.array_equal(gc3.pace, tp.pace)
    assert np.allclose(gc3["off"] + gc3["def"], tp["off"] + tp["def"], atol=1e-9)
    assert (np.abs((tp["off"] - tp["def"]) - (gc3["off"] - gc3["def"])) > 1e-9).mean() > 0.95   # strength replaced


def test_default_carryover_is_bit_identical():
    r = _r()
    a, b = r.prior_for(0.9), r.prior_for(0.9, 1.0, 1.0)
    assert np.array_equal(a.off, b.off) and np.array_equal(a.dfn, b.dfn) and np.array_equal(a.pace, b.pace)
    assert np.array_equal(a.off, 0.9 * r.off)


def test_component_carryover_keeps_margin_strength_and_shrinks_level_and_pace():
    r = _r()
    base, gc3 = r.prior_for(1.0), r.prior_for(1.0, 0.6, 0.6)
    assert np.allclose(gc3.off - gc3.dfn, base.off - base.dfn, atol=1e-12)               # strength protected
    assert np.allclose(gc3.off + gc3.dfn, 0.6 * (base.off + base.dfn), atol=1e-12)
    assert np.allclose(gc3.pace, 0.6 * base.pace, atol=1e-12)
    h, a, n = ["a", "b", "c"], ["b", "c", "a"], [False, False, True]
    pb, pg = base.predict(h, a, n), gc3.predict(h, a, n)
    eff = lambda p: (p.exp_home - p.exp_away) * 100 / p.poss                            # noqa: E731 margin per 100 poss
    assert np.allclose(eff(pb), eff(pg), atol=1e-9)


def test_game_pmf_rows_carry_the_sealed_params_model_version():
    """2026-11-26 lineage defect: a module constant stamped cbb-game-model-1.0.0 on every live PMF row while the scorer
    ran the 1.2.0 params (abe0533360f9). The row identity must come from the sealed game_model.json only."""
    import ast, inspect
    from cbb_platform.game import model as GMod
    assert not hasattr(GMod, "MODEL_VERSION")
    tree = ast.parse(inspect.getsource(GF.forecast))
    mv = [kv for d in ast.walk(tree) if isinstance(d, ast.Dict) for kv in zip(d.keys, d.values)
          if isinstance(kv[0], ast.Constant) and kv[0].value == "model_version"]
    assert len(mv) == 1 and ast.unparse(mv[0][1]) == "gm['model_version']"
    assert GP.game_model()["model_version"] == "cbb-game-model-1.2.0"
