"""prod.simulate.bounded_mixture_tables (memory fix for the 2026-11-13 morning forecast OOM) returns EXACTLY the frozen
structural.mixture_tables result -- same keys, dtypes, shapes and bytes -- and the production forecast is bit-identical
with and without the substitution. The frozen model files are untouched (their hash pins live in
tests/test_v2_jm1_final_production.py and tests/test_prod_shadow.py)."""
from __future__ import annotations

import contextlib
import json

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import (feed_fixtures as FF, fixtures as FX, lifecycle as L, odds_fixtures as OX,
                               pmf_evidence as PE, shadow_tick as TK, simulate as SM)
from cbb_platform.v2 import structural as S
from tests.test_prod_shadow_tick import Clock, _ctx


def _lm(rng, sims, players, spread):
    base = np.log(rng.uniform(0.05, 25.0, size=players * S.NS))
    return base[None, :] + spread * rng.standard_normal((sims, players * S.NS))


def _same(a: dict, b: dict):
    assert set(a) == set(b)
    for k in a:
        assert a[k].dtype == b[k].dtype and a[k].shape == b[k].shape, k
        assert a[k].tobytes() == b[k].tobytes(), k


@pytest.mark.parametrize("players,block,spread", [(26, 2, 0.35), (13, 2, 0.8), (9, 4, 0.05), (2, 2, 0.3), (7, 1, 1.5)])
def test_bounded_tables_are_byte_identical_to_the_frozen_tables(players, block, spread):
    rng = np.random.default_rng(players * 1000 + block)
    lm = _lm(rng, 3000, players, spread)
    lm[:5, 0] = np.log(400.0)                                  # extreme means: cdf saturates -> THR_CAP rows
    phi = np.array([0.0, 0.12, 0.3, 0.05, 0.9, 0.0, 0.2])     # Poisson and NB stats
    frozen = SM._FROZEN_MIXTURE_TABLES(lm, phi)
    _same(SM.bounded_mixture_tables(lm, phi, block_players=block), frozen)
    z = rng.standard_normal(lm.shape)
    bounded = SM.bounded_mixture_tables(lm, phi, block_players=block)
    assert S.counts(bounded, z).tobytes() == S.counts(frozen, z).tobytes()
    assert S.mixture_pmf(bounded, len(lm)).tobytes() == S.mixture_pmf(frozen, len(lm)).tobytes()
    scorer = SM._scorer_tables(lm, phi)                        # the scorer's substitute: every table it reads
    assert set(scorer) == {"inv", "starts", "pmf", "thr"}
    _same(scorer, {k: frozen[k] for k in scorer})


def test_odd_width_lm_falls_back_to_one_frozen_block():
    rng = np.random.default_rng(3)
    lm = rng.normal(1.0, 0.4, size=(500, 10))
    phi = np.full(7, 0.2)
    _same(SM.bounded_mixture_tables(lm, phi), SM._FROZEN_MIXTURE_TABLES(lm, phi))


def test_substitution_is_scoped_to_the_scorer_call():
    assert S.mixture_tables is SM._FROZEN_MIXTURE_TABLES
    with SM._bounded_tables():
        assert S.mixture_tables is SM._scorer_tables
    assert S.mixture_tables is SM._FROZEN_MIXTURE_TABLES
    with pytest.raises(RuntimeError), SM._bounded_tables():
        raise RuntimeError("scorer failure")
    assert S.mixture_tables is SM._FROZEN_MIXTURE_TABLES


def test_forecast_with_bounded_tables_is_bit_identical_to_the_frozen_tables(tmp_path, monkeypatch):
    hist = FX.synthetic_history(tmp_path / "history.duckdb")
    clock = Clock("2026-11-01T12:00Z")
    cbbd = FF.FakeCBBD(FF.payloads("before"))
    ctx = _ctx(tmp_path, hist, clock, cbbd, OX.FakeOdds([]))
    TK.tick(ctx)
    cbbd.bodies = FF.payloads("after")
    clock.t = pd.Timestamp("2026-11-05T06:00Z")
    TK.tick(ctx)
    clock.t = pd.Timestamp("2026-11-10T12:00Z")
    out = {}
    for arm in ("bounded", "frozen"):
        if arm == "frozen":
            monkeypatch.setattr(SM, "_bounded_tables", contextlib.nullcontext)
        life = L.Life(ops_root=tmp_path / arm / "ops", prod_root=tmp_path / "prod", history_db=hist,
                      model_root=tmp_path / "no_wf_store", clock=clock, sims=2000)
        r = L.forecast(life, L.INITIAL)
        assert r["status"] == "FORECAST", r
        out[arm] = life.forecasts(L.et_date(clock.t).isoformat(), r["snapshot_id"])
    key = ["game_id", "player_id", "stat", "outcome"]
    pa, pb = (PE.read(out[k] / PE.FILE).sort_values(key).reset_index(drop=True) for k in ("bounded", "frozen"))
    assert len(pa) > 0 and len(pa) == len(pb)
    for c in key:
        assert pa[c].tolist() == pb[c].tolist(), c
    assert pa["probability"].to_numpy().tobytes() == pb["probability"].to_numpy().tobytes()
    assert pa["mean"].to_numpy().tobytes() == pb["mean"].to_numpy().tobytes()
    ja, jb = (json.loads((out[k] / "snapshot.json").read_text()) for k in ("bounded", "frozen"))
    assert ja["players_forecast"] == jb["players_forecast"] and ja["games_forecast"] == jb["games_forecast"]
