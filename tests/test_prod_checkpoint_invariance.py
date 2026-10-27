"""Evidence for the proposed early-projection checkpoint policy (directive 2026-10-26 §7): on the SAME promoted state,
the production path scored at two different checkpoints before the validated TIP_MINUS_1H cutoff gives identical
projections and identical JM1 bank hashes. A prediction depends on the state, never on the clock, while slate.build's
STALE_STATE guard holds (every earlier game knowable by tip - 1 h is ingested). An earlier checkpoint is therefore a
scheduling decision, not a model change. FIXTURE data only."""
import json

import pandas as pd

from cbb_platform.prod import config as PC, fixtures as FX, run as RN, state as ST


def _score(w, hist, aso, tag):
    r = RN.daily(aso, [], availability=None, state_root=w / "state", history_db=hist, ledger_path=w / f"l_{tag}.jsonl",
                 run_root=w / f"runs_{tag}", sims=PC.SIMS, sync_log=w / "s.jsonl", odds_cache_root=w / "oc",
                 odds_collections=None, odds_ledger=w / "o.jsonl", odds_record=w / "or.json")
    recs = [json.loads(l)["body"] for l in open(w / f"l_{tag}.jsonl") if json.loads(l)["record_type"] == "PREDICTION"]
    return r, {(b["game_id"], b["player_id"], b["market"]): b["projection"] for b in recs}


def test_projection_depends_on_state_not_on_checkpoint(tmp_path):
    hist = FX.synthetic_history(tmp_path / "history.duckdb")
    rows, meta = FX.increment_rows(hist)
    ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], tmp_path / "state",
              created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    early_aso = FX.SOURCE_CUTOFF + pd.Timedelta(minutes=10)
    assert FX.UPCOMING_TIP - early_aso > pd.Timedelta(hours=9)     # far earlier than the T-60 scheduler window
    a, pa = _score(tmp_path, hist, early_aso, "early")
    b, pb = _score(tmp_path, hist, FX.AS_OF, "later")
    assert a["banks"] and pa and pa == pb
    assert {g: v["bank_sha256"] for g, v in a["banks"].items()} == {g: v["bank_sha256"] for g, v in b["banks"].items()}
    assert a["game_reasons"] == b["game_reasons"]
