"""Compact canonical PMFs (prod.pmf_compact): one row per (snapshot, game, player, stat) with the full float64 vector;
the long one-row-per-outcome frame is an exact deterministic view (expand). Exactness is byte-level."""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import pmf_compact as PCX, pmf_evidence as PE


def _long(rng, n_pmfs=60):
    rows = []
    for i in range(n_pmfs):
        k = int(rng.integers(5, 135))
        p = rng.dirichlet(np.full(k, 0.3))
        if i % 7 == 0:
            p[-3:] = 0.0                                    # exact zero tail mass is kept, never trimmed
            p /= p.sum()
        if i % 11 == 0:
            p[:2] = 0.0
            p /= p.sum()
        rid = f"PR-{i:04d}"
        rows.append(pd.DataFrame({"prediction_receipt_id": rid, "game_id": f"G-{i // 13}", "player_id": f"P-{i // 4}",
                                  "team_id": f"T-{i % 2}", "stat": ["PTS", "REB", "AST", "PRA"][i % 4],
                                  "prediction_as_of": "2026-11-13T12:05:00+00:00", "model_version": "cbb-jm1-x",
                                  "engine_id": "JM1_FINAL", "outcome": np.arange(k, dtype=np.int64), "probability": p,
                                  "mean": float((p * np.arange(k)).sum()), "run_id": "R1",
                                  "method": "BANK_MIXTURE_PMF" if i % 3 else "BANK_DRAWS",
                                  "game_label": f"A @ B {i // 13}", "player_label": f"Player {i // 4}"}))
    return pd.concat(rows, ignore_index=True)[PE.COLUMNS]


def test_compact_round_trip_is_byte_exact():
    L = _long(np.random.default_rng(7))
    C = PCX.compact(L)
    assert len(C) == L["prediction_receipt_id"].nunique() and list(C.columns) == PCX.PMF_COLUMNS
    E = PCX.expand(C)
    assert E.equals(L)
    for c in L.columns:
        assert E[c].dtype == L[c].dtype, c
    assert E["probability"].to_numpy().tobytes() == L["probability"].to_numpy().tobytes()
    PCX.verify(C)


def test_round_trip_through_parquet_is_byte_exact():
    L = _long(np.random.default_rng(11))
    buf = io.BytesIO()
    PCX.compact(L).to_parquet(buf, index=False, compression="zstd")
    back = pd.read_parquet(io.BytesIO(buf.getvalue()))
    PCX.verify(back)
    assert PCX.expand(back).equals(L)


def test_derived_quantities_are_identical_after_expansion():
    L = _long(np.random.default_rng(3))
    a = {d["prediction_receipt_id"]: d["pmf"] for d in PE.distributions(L)}
    b = {d["prediction_receipt_id"]: d["pmf"] for d in PE.distributions(PCX.expand(PCX.compact(L)))}
    assert a.keys() == b.keys()
    for k in a:
        p, q = a[k], b[k]
        assert p.tobytes() == q.tobytes()
        x = np.arange(len(p))
        for line in (0.5, 3.0, 7.5, 12.0, 20.5):
            over = p[x > line].sum(); under = p[x < line].sum(); push = p[x == line].sum()
            assert (over, under, push) == (q[x > line].sum(), q[x < line].sum(), q[x == line].sum())
        mean = (p * x).sum()
        assert mean == (q * x).sum() and ((x - mean) ** 2 * p).sum() == ((x - mean) ** 2 * q).sum()
        assert np.searchsorted(np.cumsum(p), [0.1, 0.5, 0.9]).tolist() == np.searchsorted(np.cumsum(q), [0.1, 0.5, 0.9]).tolist()


def test_refuses_non_contiguous_or_varying_pmfs():
    L = _long(np.random.default_rng(5), 4)
    bad = L.copy()
    bad.loc[bad.index[2], "outcome"] = 99
    with pytest.raises(PCX.PMFCompactError):
        PCX.compact(bad)
    bad = L.copy()
    bad.loc[bad.index[1], "model_version"] = "other"
    with pytest.raises(PCX.PMFCompactError):
        PCX.compact(bad)
    C = PCX.compact(L)
    C.at[0, "pmf_sha256"] = "0" * 64
    with pytest.raises(PCX.PMFCompactError):
        PCX.verify(C)


def test_empty_frames():
    assert PCX.compact(pd.DataFrame(columns=PE.COLUMNS)).empty
    assert list(PCX.expand(pd.DataFrame(columns=PCX.PMF_COLUMNS)).columns) == PCX.LONG_COLUMNS


def _deref_case(tmp_path, tamper=None, drop=False):
    from cbb_platform.prod import pmf_evidence as PE
    d = tmp_path / "R1"
    d.mkdir()
    pm = [np.array([0.25, 0.5, 0.25]), np.array([0.1, 0.9])]
    pd.DataFrame({"game_id": ["G1", "G1"], "player_id": ["P1", "P2"], "market": ["PTS", "REB"], "team_id": ["T", "T"],
                  "projection": [1.0, 0.9], "method": ["m", "m"], "pmf": [a.tolist() for a in pm]}
                 ).to_parquet(d / PE.RUN_FILE, index=False)
    ev = PE.from_run(d, "R1", "2026-11-04T18:00:00Z", "M")
    if tamper:
        ev.loc[ev.index[-1], "probability"], ev.loc[ev.index[-2], "probability"] = 0.8, 0.2
    if drop:
        ev = ev[ev["player_id"] == "P1"]
    PE.write(tmp_path / "tick" / PE.FILE, ev)
    return PE, d, tmp_path / "tick" / PE.FILE


def test_run_copy_becomes_a_reference_only_when_bit_identical(tmp_path):
    PE, d, durable = _deref_case(tmp_path)
    before = pd.read_parquet(d / PE.RUN_FILE)
    r = PE.dereference_run(d, "R1", durable)
    assert r["status"] == "REFERENCED" and not (d / PE.RUN_FILE).exists() and (d / PE.RUN_REF).exists()
    after = PE.read_run_pmfs(d)
    assert [np.asarray(v).tobytes() for v in after["pmf"]] == [np.asarray(v, dtype=float).tobytes() for v in before["pmf"]]
    assert PE.dereference_run(d, "R1", durable)["status"] == "ABSENT"          # idempotent


@pytest.mark.parametrize("kw", [{"tamper": True}, {"drop": True}])
def test_run_copy_kept_when_durable_differs_or_is_partial(tmp_path, kw):
    PE, d, durable = _deref_case(tmp_path, **kw)
    r = PE.dereference_run(d, "R1", durable)
    assert r["status"] == "KEPT" and (d / PE.RUN_FILE).exists() and not (d / PE.RUN_REF).exists()


def test_files_written_before_engine_attribution_read_with_the_exact_legacy_engine(tmp_path):
    """A durable PMF file from before engine_id existed: attributed from prediction_as_of (prod.engine)."""
    from cbb_platform.prod import engine as ENG
    L = _long(np.random.default_rng(3), 8).drop(columns="engine_id")
    L.loc[L.index[L["prediction_receipt_id"] >= "PR-0004"], "prediction_as_of"] = "2026-11-22T22:10:10.060092+00:00"
    p = tmp_path / "pmfs.parquet"
    L.to_parquet(p, index=False)                                   # a legacy LONG file
    back = PE.read(p)
    assert list(back.columns) == PE.COLUMNS
    old = back[back["prediction_receipt_id"] < "PR-0004"]["engine_id"].unique().tolist()
    new = back[back["prediction_receipt_id"] >= "PR-0004"]["engine_id"].unique().tolist()
    assert old == ["JM1_FINAL"] and new == [ENG.ENGINE_HISTORY[1][1]]
    c = tmp_path / "compact.parquet"                                # a legacy COMPACT file
    PCX.compact(back).drop(columns="engine_id").to_parquet(c, index=False)
    assert PE.read(c).equals(back)
