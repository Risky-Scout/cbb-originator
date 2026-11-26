"""PS2 (prod.minutes_state): sealed map, monotone per band, identity under a normal map, frozen function always restored,
engine attribution."""
from math import erf, sqrt

import numpy as np
import pytest

from cbb_platform.prod import engine as ENG, minutes_state as MS2
from cbb_platform.v2 import joint_minutes_v2 as JM


def test_sealed_map_has_four_bands_of_99_knots():
    m = MS2.qmap()
    assert len(m["q"]) == 4 and all(len(q) == 99 and np.all(np.diff(q) >= 0) for q in m["q"])


def test_map_is_monotone_within_each_band():
    z = np.linspace(-4, 4, 401)[:, None].repeat(4, axis=1)
    out = MS2.map_eps(np.array([5.0, 15.0, 24.0, 32.0]), z)
    assert np.all(np.diff(out, axis=0) >= 0) and np.all(out[-1] > out[0])      # weakly monotone (integer-minute ties)


def test_normal_quantiles_give_the_identity(monkeypatch):
    u = MS2.qmap()["u"]
    inv = np.array([np.interp(x, [0.5 * (1 + erf(t / sqrt(2))) for t in np.linspace(-6, 6, 24001)], np.linspace(-6, 6, 24001)) for x in u])
    monkeypatch.setattr(MS2, "qmap", lambda: {"u": u, "edges": np.array([0.0, 10, 20, 28]), "q": [inv] * 4})
    z = np.random.default_rng(0).standard_normal((2000, 3))
    assert np.allclose(MS2.map_eps(np.array([5.0, 22.0, 30.0]), z), z, atol=0.03)       # linear-interpolation chord error between outer knots (same as the evaluated replay)


def test_context_restores_the_frozen_function_even_on_error():
    frozen = JM.raw_minutes
    with pytest.raises(RuntimeError):
        with MS2.calibrated_minutes():
            assert JM.raw_minutes is MS2.raw_minutes
            raise RuntimeError("boom")
    assert JM.raw_minutes is frozen


def test_mapped_draw_shifts_state_mass_for_starters():
    rng = np.random.default_rng(1)
    z = rng.standard_normal((20000, 1)); mu, sd = np.array([31.0]), np.array([5.0])
    dur = np.ones(20000)
    base = MS2._FROZEN_RAW_MINUTES(mu, sd, z, dur, 1.0); new = MS2.raw_minutes(mu, sd, z, dur, 1.0)
    assert (new >= 28).mean() > (base >= 28).mean()            # 28+ band median above min_mu (TRAIN)


def test_engine_names_ps2_last():
    assert ENG.engine_id().split("#")[0].endswith("+PI2+PS2")
