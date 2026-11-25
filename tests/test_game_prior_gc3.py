"""GC3 component carryover (game model 1.1.0, 2026-11-25): sealed prior, bit-identical default, protected margin strength."""
import hashlib

import numpy as np

from cbb_platform.game import forecast as GF, params as GP, ratings as GR


def _r():
    rng = np.random.default_rng(0)
    return GR.Ratings(["a", "b", "c"], rng.normal(0, 5, 3), rng.normal(0, 5, 3), rng.normal(0, 3, 3), 108.0, 2.0, 68.0, {"state_id": "x"})


def test_sealed_prior_is_the_gc3_construction():
    gm = GP.game_model()
    e = gm["ratings_prior"]["2027"]
    assert gm["model_version"] == "cbb-game-model-1.1.0" and gm["ratings"]["carry_level"] == 0.6 and gm["ratings"]["carry_pace"] == 0.6
    assert e["file"] == "ratings_prior_2027_gc3.parquet" and e["state_id"] == "TR-10eb402ea932e538"
    assert hashlib.sha256((GP.ROOT / e["file"]).read_bytes()).hexdigest() == e["sha256"]
    assert GF.prior(2027).meta["state_id"] == e["state_id"]


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
