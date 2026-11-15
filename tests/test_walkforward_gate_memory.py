"""Walk-forward gate memory (2026-11-15): the gates' frozen integrity checks run in the production scorer's bounded
mixture-table scope (prod.simulate._bounded_tables), which returns byte-identical pmf / thr tables
(tests/test_prod_bounded_tables.py) and omits only cdf -- a table no gate-path reader uses. TEST fixtures only; the
exact-candidate equality (identical gates.json on the accepted 2026-11-14 candidate) is the QA gate probe."""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from cbb_platform.prod import simulate as SM
from cbb_platform.v2 import jm1_final as F, jm1_final_production as JP, structural as S
from cbb_platform.walkforward import gates as GT


def test_integrity_checks_see_the_bounded_tables_and_the_frozen_function_is_restored(monkeypatch):
    seen = []
    monkeypatch.setattr(JP, "integrity_checks", lambda prod, log: seen.append(S.mixture_tables) or {"ALL_PASS": True})
    assert GT.bounded_integrity_checks({}) == {"ALL_PASS": True}
    assert seen == [SM._scorer_tables] and S.mixture_tables is SM._FROZEN_MIXTURE_TABLES


def test_restored_even_when_the_checks_raise(monkeypatch):
    def boom(prod, log):
        raise ValueError("synthetic")
    monkeypatch.setattr(JP, "integrity_checks", boom)
    with pytest.raises(ValueError):
        GT.bounded_integrity_checks({})
    assert S.mixture_tables is SM._FROZEN_MIXTURE_TABLES


def test_no_gate_path_reader_uses_the_cdf_table():
    """score_game / integrity_checks / eval_game consume the tables only through structural.counts (thr, inv) and
    structural.mixture_pmf (pmf, inv, starts); none of them index the table dict themselves."""
    for fn in (S.counts, S.mixture_pmf, JP.score_game, JP.integrity_checks, F.eval_game):
        assert '"cdf"' not in inspect.getsource(fn) and "'cdf'" not in inspect.getsource(fn), fn.__name__
    rng = np.random.default_rng(7)
    lm = np.log(rng.uniform(0.1, 20.0, size=8 * S.NS))[None, :] + 0.4 * rng.standard_normal((2000, 8 * S.NS))
    phi = np.array([0.0, 0.12, 0.3, 0.05, 0.9, 0.0, 0.2])
    full, scorer = SM._FROZEN_MIXTURE_TABLES(lm, phi), SM._scorer_tables(lm, phi)
    z = rng.standard_normal(lm.shape)
    assert S.counts(scorer, z).tobytes() == S.counts(full, z).tobytes()
    assert S.mixture_pmf(scorer, len(lm)).tobytes() == S.mixture_pmf(full, len(lm)).tobytes()


def test_gates_call_the_bounded_checks():
    src = inspect.getsource(GT.evaluate)
    assert "bounded_integrity_checks(model.jm1)" in src and "JP.integrity_checks(" not in src
