"""LOCAL equivalence of the walk-forward training-row path with the FROZEN loaders (skipped without the local stores).

Two real 2025-26 games are replayed through exactly the code the daily refit uses for 2026-27 games:
pregame features at their validated cutoff (prod.features.build, targets stripped), labels + season finals from an
as-of build with blank_targets=False (stage3 label SQL), assembly into the frozen loader shapes. Every frame must equal
what the frozen loaders read from the lockbox store for the same rows:
    frame  == marginal.load_frames (population "scored")       part == marginal.load_participation
    team   == structural.load_team_frame(labels=True)          finals == marginal.load_frames finals (2025-26)
    oos    == the Phase 1B V1REF OOS store rows (F2026, annual state through 2024-25)
"""
from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.prod import config as PC, features as PF
from cbb_platform.v2 import marginal as M, phase1b as B, phase3 as P3, provenance as PV, structural as S
from cbb_platform.walkforward import training_rows as TR

NEEDED = (PV.LB_FEATURES, PV.LB_LABELS, PC.HISTORY_CANONICAL, B.OOS_PATH, M.STATE_DIR / "F2026_ANNUAL_REFIT_V1REF.json")
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="frozen local stores not present")


def _eq(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    """Exact for integers / strings; floats at the replay tolerance of tests/test_prod_replay.py (rtol = atol = 1e-12:
    DuckDB window sums differ by <= 1 ulp between the per-cutoff build and the one-pass lockbox build)."""
    bad = []
    for c in b.columns:
        x, y = a[c].reset_index(drop=True), b[c].reset_index(drop=True)
        nx, ny = x.isna().to_numpy(), y.isna().to_numpy()
        if not (nx == ny).all():
            bad.append(c)
        elif x.dtype.kind == "f" or y.dtype.kind == "f":
            if not np.allclose(x[~nx].astype(float).to_numpy(), y[~ny].astype(float).to_numpy(), rtol=1e-12, atol=1e-12):
                bad.append(c)
        elif x.dtype.kind in "iub" and y.dtype.kind in "iub":
            if not np.array_equal(x[~nx].astype(np.int64).to_numpy(), y[~ny].astype(np.int64).to_numpy()):
                bad.append(c)
        elif not (x[~nx].astype(str).to_numpy() == y[~ny].astype(str).to_numpy()).all():
            bad.append(c)
    return bad


@pytest.fixture(scope="module")
def rep(tmp_path_factory):
    con = duckdb.connect(str(PV.LB_FEATURES), read_only=True)
    g = con.execute("""SELECT canonical_game_id, prediction_cutoff_utc FROM ex.game_examples
        WHERE season = 2026 AND prediction_cutoff_utc = (
            SELECT prediction_cutoff_utc FROM ex.game_examples WHERE season = 2026 AND scheduled_tip_utc >= '2026-01-20'
            GROUP BY 1 HAVING count(*) >= 2 ORDER BY 1 LIMIT 1)
        ORDER BY 1 LIMIT 2""").df()
    con.close()
    ids, cut = g["canonical_game_id"].tolist(), g["prediction_cutoff_utc"].iat[0]
    cfg = P.load_config()
    work = tmp_path_factory.mktemp("wf_rows")
    fr = PF.build(work / "pregame", ids, 2026, cut, cfg)
    lab = TR.label_rows(work / "labels", None, pd.Timestamp("2026-07-31T00:00:00Z"), ids, PC.HISTORY_CANONICAL, cfg, season=2026)
    st = json.loads((M.STATE_DIR / "F2026_ANNUAL_REFIT_V1REF.json").read_text())
    a = TR.assemble(fr["cand"], fr["team"], lab, cfg, st, 2025, season=2026)
    return {"ids": ids, "cfg": cfg, "a": a, "lab": lab}


def test_frame_equals_frozen_loader(rep):
    frame, finals = M.load_frames(rep["cfg"], range(2023, 2027))
    ref = frame[frame["player_game_example_id"].isin(rep["a"]["frame"]["player_game_example_id"])].reset_index(drop=True)
    got = rep["a"]["frame"]
    assert list(got.columns) == list(frame.columns)
    assert len(got) == len(ref) > 0
    pg = M.games_of(seasons=(2026,))
    assert set(pg[pg["player_game_example_id"].isin(ref["player_game_example_id"])]["canonical_game_id"]) == set(rep["ids"])
    full = frame[frame["player_game_example_id"].isin(pg[pg["canonical_game_id"].isin(rep["ids"])]["player_game_example_id"])]
    assert len(full) == len(got)                                  # every scored row of the two games, nothing else
    assert _eq(got, ref) == []


def test_finals_equal_frozen_loader(rep):
    _, finals = M.load_frames(rep["cfg"], range(2023, 2027))
    ref = finals[finals["season"] == 2026].reset_index(drop=True)
    got = rep["a"]["finals"]
    assert list(got.columns) == list(ref.columns) and len(got) == len(ref) > 1000
    assert _eq(got, ref) == []


def test_participation_equals_frozen_loader(rep):
    part = M.load_participation([2026])
    ref = part[part["canonical_game_id"].isin(rep["ids"])].reset_index(drop=True)
    got = rep["a"]["part"]
    assert list(got.columns) == list(ref.columns) and len(got) == len(ref) > 0
    assert _eq(got, ref) == []


def test_team_frame_equals_frozen_loader(rep):
    tf = S.load_team_frame([2026], labels=True)
    ref = tf[tf["game_id"].isin(rep["ids"])].sort_values(["season", "game_id", "team_id"]).reset_index(drop=True)
    got = rep["a"]["team"]
    assert list(got.columns) == list(ref.columns) and len(got) == len(ref) == 4
    assert _eq(got, ref) == []


def test_oos_rows_equal_validated_store(rep):
    store = P3.load_store([2026])
    ref = store[store["game_id"].isin(rep["ids"])].sort_values(["game_id", "team_id", "player_id"]).reset_index(drop=True)
    got = rep["a"]["oos"]
    assert list(got.columns) == list(ref.columns) and len(got) == len(ref) > 0
    exact = [c for c in ref.columns if not c.endswith(("_mu", "_sigma")) and c != "p_play"]
    assert _eq(got[exact], ref[exact]) == []
    for c in [c for c in ref.columns if c.endswith(("_mu", "_sigma"))] + ["p_play"]:    # replay-test tolerance
        assert np.allclose(got[c], ref[c], rtol=1e-10, atol=1e-12), c
    assert rep["a"]["coverage"]["candidates_without_label"] == 0
