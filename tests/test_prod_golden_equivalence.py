"""Golden-output equivalence of the production scoring path (repository stabilization gate, 2026-10-24).

A small, deterministic, CI-safe replay: the labeled synthetic FIXTURE history + 2026-27 increment + fixture quotes
(cbb_platform/prod/fixtures.py; never live data) -> run.daily on the frozen e1353e5 artifacts, 20,000 draws per game,
every path a temporary root. The golden file was written ONCE from the unchanged code at ca00d6a, before any cleanup:

    tests/fixtures/golden/prod_equivalence_v1.json

It pins: frozen model identity + artifact sha256, model inputs (P(play), minutes, all 7 stat means / dispersions), the
JM1 joint bank of every scored game (sha256 of the draws), every PREDICTION record (base-stat pmf projections, combo
projections from the same draws, quoted-line probabilities / push / fair price / edge) and the priced board.

Tolerance: on the platform that wrote the golden file the comparison is exact (bank sha256 equal, floats atol 1e-12).
On another platform (e.g. the Ubuntu CI runner) BLAS / libm last-bit differences are possible, so bank sha256 is not
required and floats use the documented tolerance below. Any other difference is a model change: STOP and revert.

    .venv/bin/python -m tests.test_prod_golden_equivalence --write    (refuses to overwrite an existing golden file)
"""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import config as PC, fixtures as FX, loader as LD, run as RN, state as ST

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "prod_equivalence_v1.json"
EXACT_ATOL = 1e-12
CROSS_PLATFORM_ATOL = {"inputs": 1e-9, "pmf": 1e-9, "draws": 2e-3}   # draws: combo projections from integer bank draws
INPUT_COLS = ["p_play", "min_mu", "min_sigma"] + [f"{s}_{k}" for s in PC.BASE_STATS for k in ("mu", "phi")]
PRED_NUM = ("projection", "p_play", "probability", "push_probability", "p_nonpush", "fair_decimal", "edge", "pmf_q75")
BOARD_NUM = ("Projection", "Line", "WinChance", "PushProbability", "FairPrice", "BestOdds", "Edge")


def _platform() -> str:
    return f"{sys.platform}-{platform.machine()}"


def _num(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


def capture(work: Path) -> dict:
    """Run the production path on the fixture slate and return its comparable outputs (no run ids, no wall clock)."""
    work.mkdir(parents=True, exist_ok=True)
    hist = FX.synthetic_history(work / "history.duckdb")
    rows, meta = FX.increment_rows(hist)
    ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], work / "state",
              created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    q = FX.quotes_files(hist, meta, work / "quotes")
    r = RN.daily(FX.AS_OF, q, availability=FX.availability(meta), state_root=work / "state", history_db=hist,
                 ledger_path=work / "ledger.jsonl", run_root=work / "runs", sims=PC.SIMS, sync_log=work / "sync.jsonl",
                 odds_cache_root=work / "odds_cache", odds_collections=None, odds_ledger=work / "odds.jsonl",
                 odds_record=work / "odds_record.json")
    run_dir = next((work / "runs").iterdir())
    ident = LD.verify()
    mi = pd.read_parquet(run_dir / "model_inputs.parquet").sort_values(["game_id", "team_id", "player_id"])
    preds = []
    for line in open(work / "ledger.jsonl"):
        rec = json.loads(line)
        if rec["record_type"] != "PREDICTION":
            continue
        b = rec["body"]
        preds.append({"key": f"{b['game_id']}|{b['player_id']}|{b['market']}|{b['line']}|{b['side']}|{b['sportsbook']}",
                      "method": b["distribution_method"], "eligibility_state": b["eligibility_state"],
                      "bank_sha256": b["bank_sha256"], "model_version": b["model_version"],
                      **{k: _num(b[k]) for k in PRED_NUM}})
    board = pd.read_csv(run_dir / "board.csv").sort_values(["Game", "Player", "Bet", "Line"])
    return {
        "platform": _platform(),
        "identity": {k: ident[k] for k in ("model_version", "architecture", "jm1_final_version", "frozen_commit",
                                           "production_season", "training_through", "artifact_sha256")},
        "readiness_state": r["readiness_state"], "game_reasons": r["game_reasons"],
        "banks": {g: {"bank_sha256": v["bank_sha256"], "priced_players": v["priced_players"],
                      "corr_min_eig": v["corr_min_eig"]} for g, v in sorted(r["banks"].items())},
        "model_inputs": [{"key": f"{x.game_id}|{x.player_id}", **{c: _num(getattr(x, c)) for c in INPUT_COLS}}
                         for x in mi.itertuples(index=False)],
        "predictions": sorted(preds, key=lambda p: p["key"]),
        "board": [{"key": f"{x['Game']}|{x['Player']}|{x['Bet']}|{x['Sportsbook']}", "ModelVersion": x["ModelVersion"],
                   **{c: _num(x[c]) for c in BOARD_NUM}} for x in board.to_dict("records")],
    }


def _close(a, b, atol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= atol


def compare(got: dict, gold: dict) -> list[str]:
    same = got["platform"] == gold["platform"]
    tol = {k: EXACT_ATOL for k in CROSS_PLATFORM_ATOL} if same else CROSS_PLATFORM_ATOL
    bad = []
    for k in ("identity", "readiness_state", "game_reasons"):
        if got[k] != gold[k]:
            bad.append(k)
    if sorted(got["banks"]) != sorted(gold["banks"]):
        bad.append("banks: scored game set")
    for g, v in gold["banks"].items():
        w = got["banks"].get(g, {})
        if w.get("priced_players") != v["priced_players"] or not _close(w.get("corr_min_eig"), v["corr_min_eig"], tol["inputs"]):
            bad.append(f"banks[{g}]")
        if same and w.get("bank_sha256") != v["bank_sha256"]:
            bad.append(f"banks[{g}].bank_sha256")
    for part, cols, kind in (("model_inputs", INPUT_COLS, "inputs"), ("board", BOARD_NUM, None),
                             ("predictions", PRED_NUM, None)):
        gk, wk = [r["key"] for r in gold[part]], [r["key"] for r in got[part]]
        if gk != wk:
            bad.append(f"{part}: key set / order")
            continue
        for gr, wr in zip(gold[part], got[part]):
            for c in cols:
                t = tol[kind] if kind else tol["draws" if wr.get("method") == "BANK_DRAWS" or part == "board" else "pmf"]
                if not _close(wr[c], gr[c], t):
                    bad.append(f"{part}[{gr['key']}].{c}: {wr[c]} != {gr[c]}")
            for c in ("method", "eligibility_state", "model_version", "ModelVersion"):
                if c in gr and wr[c] != gr[c]:
                    bad.append(f"{part}[{gr['key']}].{c}")
            if same and "bank_sha256" in gr and wr["bank_sha256"] != gr["bank_sha256"]:
                bad.append(f"{part}[{gr['key']}].bank_sha256")
    return bad


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    return capture(tmp_path_factory.mktemp("golden"))


def test_golden_file_present_and_pinned_to_the_frozen_champion():
    gold = json.loads(GOLDEN.read_text())
    assert gold["identity"]["artifact_sha256"] == {k: v[1] for k, v in PC.ARTIFACTS.items()}
    assert gold["identity"]["frozen_commit"] == PC.FROZEN_COMMIT and gold["identity"]["model_version"] == PC.PRODUCTION_VERSION
    assert len(gold["banks"]) == 2 and len(gold["predictions"]) > 500 and len(gold["board"]) > 0
    assert {p["method"] for p in gold["predictions"]} >= {"BANK_MIXTURE_PMF", "BANK_DRAWS"}   # base stats + combos


def test_production_outputs_equal_the_golden_file(captured):
    bad = compare(captured, json.loads(GOLDEN.read_text()))
    assert not bad, f"{len(bad)} differences (a model change; STOP and revert): {bad[:20]}"


def test_capture_is_deterministic(captured, tmp_path):
    assert compare(capture(tmp_path / "again"), captured) == []


if __name__ == "__main__":
    if "--write" not in sys.argv:
        sys.exit("usage: python -m tests.test_prod_golden_equivalence --write")
    if GOLDEN.exists():
        sys.exit(f"refusing to overwrite {GOLDEN}: the golden file is written once, from unchanged code")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = capture(Path(d))
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}: {len(out['predictions'])} predictions, {len(out['board'])} board rows, banks {list(out['banks'])}")
