"""LOCAL replay equivalence of the production input path (skipped where the frozen local stores are absent, e.g. CI).

Two real 2025-26 games are replayed as if upcoming, at their validated cutoff (tip - 1 h), from the frozen canonical
DB masked at that instant (the target games' outcomes stripped). The production path must reproduce:
  1. the lockbox Stage 3 feature store rows (player features, team features, pregame universe) EXACTLY;
  2. the Phase 1B out-of-sample store's marginal forecasts (p_play, minutes, all 7 stat means) for those games when
     scored with the same annual-refit state (F2026 V1REF) -- i.e. the label-free production loader + scorer equal the
     frozen loader + scorer that produced the validated forecasts;
  3. the frozen pregame shared environment (structural.load_team_frame labels=False + env_predict).
Two games only; no season is rescored.
"""
from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.prod import config as PC, features as PF, inputs as IN
from cbb_platform.v2 import marginal as M, phase1b as B, provenance as PV, structural as S

NEEDED = (PV.LB_FEATURES, PC.HISTORY_CANONICAL, B.OOS_PATH, M.STATE_DIR / "F2026_ANNUAL_REFIT_V1REF.json")
pytestmark = pytest.mark.skipif(not all(p.exists() for p in NEEDED), reason="frozen local stores not present")


def _eq(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    bad = []
    for c in b.columns:
        x, y = a[c], b[c]
        nx, ny = x.isna().to_numpy(), y.isna().to_numpy()
        if not (nx == ny).all():
            bad.append(c)
            continue
        if x.dtype.kind in "fiu" and y.dtype.kind in "fiu":
            if not np.allclose(x[~nx].astype(float), y[~ny].astype(float), rtol=1e-12, atol=1e-12):
                bad.append(c)
        elif not (x[~nx].astype(str).to_numpy() == y[~ny].astype(str).to_numpy()).all():
            bad.append(c)
    return bad


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    con = duckdb.connect(str(PV.LB_FEATURES), read_only=True)
    g = con.execute("""SELECT canonical_game_id, prediction_cutoff_utc FROM ex.game_examples
        WHERE season = 2026 AND prediction_cutoff_utc = (
            SELECT prediction_cutoff_utc FROM ex.game_examples WHERE season = 2026 AND scheduled_tip_utc >= '2026-02-10'
            GROUP BY 1 HAVING count(*) >= 2 ORDER BY 1 LIMIT 1)
        ORDER BY 1 LIMIT 2""").df()
    con.close()
    ids, cut = g["canonical_game_id"].tolist(), g["prediction_cutoff_utc"].iat[0]
    cfg = P.load_config()
    fr = PF.build(tmp_path_factory.mktemp("replay"), ids, 2026, cut, cfg)
    return {"ids": ids, "cut": cut, "cfg": cfg, "fr": fr}


def test_feature_rows_identical_to_lockbox_store(replay):
    ids = ", ".join(f"'{x}'" for x in replay["ids"])
    mine = duckdb.connect(str(replay["fr"]["features_db"]), read_only=True)
    lb = duckdb.connect(str(PV.LB_FEATURES), read_only=True)
    for t, key in (("feat.player_features", "player_game_example_id"), ("feat.team_features", "canonical_game_id, canonical_team_id"),
                   ("ex.player_game_examples", "player_game_example_id"), ("ex.game_examples", "canonical_game_id")):
        a = mine.execute(f"SELECT * FROM {t} WHERE canonical_game_id IN ({ids}) ORDER BY {key}").df()
        b = lb.execute(f"SELECT * FROM {t} WHERE canonical_game_id IN ({ids}) ORDER BY {key}").df()
        assert len(a) == len(b) > 0, t
        assert set(b.columns) <= set(a.columns), t
        assert _eq(a, b) == [], (t, _eq(a, b))
    prov = replay["fr"]["provenance"]["canonical"]
    assert prov["target_outcome_rows"] == 0 and prov["post_as_of_rows"] == 0


def test_marginal_forecasts_reproduce_validated_oos_store(replay):
    st = json.loads((M.STATE_DIR / "F2026_ANNUAL_REFIT_V1REF.json").read_text())
    rows = IN.marginal_rows(replay["fr"]["cand"], replay["cfg"], st["v0_state"], st["participation_state"])
    store = pd.read_parquet(B.OOS_PATH, filters=[("forecast_season", "==", 2026)])
    store = store[store["game_id"].isin(replay["ids"])]
    m = rows.merge(store, on="player_game_example_id", suffixes=("", "_store"), validate="one_to_one")
    assert len(m) == len(rows) == len(store) > 0
    for c in ["p_play", "min_mu", "min_sigma"] + [f"{s}_mu" for s in PC.BASE_STATS]:
        assert np.allclose(m[c], m[f"{c}_store"], rtol=1e-10, atol=1e-12), c
    assert (m["role_diagnostic"] == m["role_diagnostic_store"].fillna("NO_ROLE_HISTORY")).all()


def test_environment_reproduces_frozen_team_loader(replay):
    env = json.loads((PC.MODEL_DIR / "jm1_final_through_2025_26.json").read_text())["structure"]["env"]
    mine = IN.environment(replay["fr"]["team"], env)
    tf = S.load_team_frame([2026], labels=False)
    ref = S.env_predict(tf[tf["game_id"].isin(replay["ids"])], env)
    assert len(mine) == len(ref) == 4
    for r in ref.itertuples(index=False):
        assert np.allclose(mine[(r.game_id, r.team_id)], [r.pace_dev, r.own_eff_dev, r.opp_eff_dev], rtol=1e-12, atol=1e-12)
