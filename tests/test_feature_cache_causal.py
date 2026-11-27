"""Pregame feature cache: causal identity + invalidation (training_rows.cache_validity / feature_rows), Stage 2
2026-11-27. Before: a cached row was reused whenever <cache>/<game_id>/ existed -- a rescheduled tip, a replaced state
version, a changed history DB or changed feature semantics silently reused stale rows. Now every reuse re-checks the
typed causal key; a stale entry is superseded (kept as evidence) and rebuilt. Corrections that arrive AFTER a game's
cutoff never invalidate (no backdating). Synthetic builder: these tests exercise the cache, not Stage 3."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from cbb_platform.walkforward import training_rows as TR

U = lambda s: TR._utc(s)


def _state(root: Path, name: str, created: str):
    d = root / "versions" / name
    d.mkdir(parents=True)
    (d / "increment.duckdb").write_bytes(name.encode())
    (d / "state_manifest.json").write_text(json.dumps({"state_version": name, "created_at": created,
                                                       "source_cutoff": created}))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    sr, cr, hist = tmp_path / "state", tmp_path / "cache", tmp_path / "hist.duckdb"
    hist.write_bytes(b"history-v1")
    _state(sr, "S1", "2026-11-20T09:00:00+00:00")
    calls = []

    def build(work, gids, season, cut, cfg, h, inc, closure=False):
        calls.append({"gids": sorted(gids), "as_of": U(cut), "inc": Path(inc).parent.name, "closure": closure})
        cand = pd.DataFrame({"canonical_game_id": [g for g in gids for _ in range(2)],
                             "player_game_example_id": [f"{g}-{i}" for g in gids for i in range(2)],
                             "v": [cfg["k"]] * (2 * len(gids))})
        team = pd.DataFrame({"game_id": [g for g in gids for _ in range(2)], "team_id": ["A", "B"] * len(gids)})
        return {"cand": cand, "team": team, "features_db": None,
                "provenance": {"canonical": {"target_outcome_rows": 0, "post_as_of_rows": 0, "as_of": str(cut),
                                             **({"closure": {"version": "X"}} if closure else {})}}}
    monkeypatch.setattr(TR.PF, "build", build)
    monkeypatch.setattr(TR, "_pit_audit", lambda fdb, gids: {"violations": 0})
    monkeypatch.setattr(TR.PF, "as_of_mask_instants", lambda h, i, s, lo, hi: [])
    games = pd.DataFrame({"canonical_game_id": ["G1", "G2"],
                          "prediction_cutoff_utc": [U("2026-11-20T23:00Z"), U("2026-11-21T01:00Z")]})
    run = lambda g=games, cfg={"k": 1}, **kw: TR.feature_rows(cr, sr, g, hist, cfg, log=lambda m: None, **kw)
    return {"sr": sr, "cr": cr, "hist": hist, "calls": calls, "games": games, "run": run}


def test_cold_then_warm_cache_and_the_causal_key(env):
    a = env["run"]()
    assert [c["gids"] for c in env["calls"]] == [["G1", "G2"]] and env["calls"][0]["closure"] is True   # closure default ON
    k = a["meta"]["G1"]["causal_key"]
    assert k["version"] == TR.CAUSAL_KEY_VERSION and k["state_version"] == "S1" and len(k["key_sha256"]) == 64
    assert k["history_db_sha256"] == TR.file_sha256(env["hist"]) and k["prediction_cutoff_utc"] == "2026-11-20T23:00:00+00:00"
    assert k["build_as_of_utc"] == "2026-11-20T23:00:00+00:00"                    # group as_of = earliest cutoff
    assert a["meta"]["G2"]["causal_key"]["prediction_cutoff_utc"] == "2026-11-21T01:00:00+00:00"
    b = env["run"]()                                                              # warm: nothing rebuilt
    assert len(env["calls"]) == 1 and b["cand"].equals(a["cand"]) and b["team"].equals(a["team"])


def test_comparator_switch_restores_the_incumbent_full_build(env, monkeypatch):
    monkeypatch.setenv("CBB_FEATURE_CLOSURE", "OFF")
    env["run"]()
    assert env["calls"][-1]["closure"] is False


def test_rescheduled_tip_invalidates_and_supersedes(env):
    env["run"]()
    g = env["games"].copy()
    g.loc[g.canonical_game_id == "G2", "prediction_cutoff_utc"] = U("2026-11-21T02:30Z")
    out = env["run"](g)
    assert env["calls"][-1]["gids"] == ["G2"] and env["calls"][-1]["as_of"] == U("2026-11-21T02:30Z")
    assert out["meta"]["G2"]["prediction_cutoff_utc"] == "2026-11-21T02:30:00+00:00"
    sup = list((env["cr"] / "features" / ".superseded").iterdir())
    assert len(sup) == 1 and sup[0].name.startswith("G2.") and sup[0].name.endswith("STALE_CUTOFF")
    assert json.loads((sup[0] / "meta.json").read_text())["prediction_cutoff_utc"] == "2026-11-21T01:00:00+00:00"


def test_replaced_state_version_invalidates(env):
    env["run"]()
    _state(env["sr"], "S1b", "2026-11-20T10:00:00+00:00")       # a re-created state, as-of both cutoffs
    env["run"]()
    assert env["calls"][-1]["gids"] == ["G1", "G2"] and env["calls"][-1]["inc"] == "S1b"


def test_a_correction_after_the_cutoff_never_invalidates(env):
    env["run"]()
    _state(env["sr"], "S2", "2026-11-22T09:00:00+00:00")         # later state (e.g. a corrected box): after both cutoffs
    env["run"]()
    assert len(env["calls"]) == 1


def test_changed_feature_semantics_or_history_db_invalidate(env):
    env["run"]()
    env["run"](cfg={"k": 2})                                     # frozen config changed -> semantics hash changed
    assert len(env["calls"]) == 2
    env["hist"].write_bytes(b"history-v2")                       # history DB content changed
    env["run"](cfg={"k": 2})
    assert len(env["calls"]) == 3
    env["run"](cfg={"k": 2})
    assert len(env["calls"]) == 3


def test_legacy_rows_and_retired_states(env):
    env["run"]()
    mp = env["cr"] / "features" / "G1" / "meta.json"
    m = json.loads(mp.read_text())
    m.pop("causal_key")
    mp.write_text(json.dumps(m))                                 # a pre-key (legacy) row
    sidx = TR.StateIndex(env["sr"])
    assert TR.cache_validity(m, U("2026-11-20T23:00Z"), sidx, "x", "y") == "LEGACY_VALID"
    m2 = json.loads((env["cr"] / "features" / "G2" / "meta.json").read_text())
    m2["state_version"] = "S0_RETIRED"
    assert TR.cache_validity(m2, U("2026-11-21T01:00Z"), sidx, None, None) == "VALID_STATE_RETIRED"
    m2["status"] = "NO_STATE_AT_CUTOFF"
    assert TR.cache_validity(m2, U("2026-11-21T01:00Z"), sidx, None, None) == "STALE_STATUS"
    env["run"]()
    assert len(env["calls"]) == 1


def test_interrupted_writes_are_ignored_and_rebuilt(env):
    root = env["cr"] / "features"
    (root / ".G1.deadbeef").mkdir(parents=True)                 # a crash between tmp write and the atomic rename
    (root / ".work-0123").mkdir()
    (root / "G2").mkdir()                                       # a directory with no meta.json
    out = env["run"]()
    assert env["calls"][-1]["gids"] == ["G1", "G2"] and out["meta"]["G2"]["status"] == "BUILT"
    assert set(TR.cached_valid_games(env["cr"], env["sr"], env["games"], env["hist"], {"k": 1})) == {"G1", "G2"}


def test_warmup_todo_uses_the_same_validity(env):
    env["run"]()
    g = env["games"].copy()
    g.loc[g.canonical_game_id == "G1", "prediction_cutoff_utc"] = U("2026-11-20T22:00Z")
    assert TR.cached_valid_games(env["cr"], env["sr"], g, env["hist"], {"k": 1}) == {"G2"}


def test_closure_is_pinned_to_the_audited_stage3_sql(monkeypatch):
    """the exact-closure argument (prod.features.dependency_closure docstring) holds only for the audited Stage 3 SQL:
    a Stage 3 change must fail here (re-audit) and, at runtime, falls back to the full incumbent build"""
    from cbb_platform.prod import features as PF
    assert PF.stage3_source_sha256() == PF.CLOSURE_AUDITED_STAGE3_SHA256 and PF.closure_audited()
    monkeypatch.setattr(PF, "CLOSURE_AUDITED_STAGE3_SHA256", "0" * 64)
    assert not PF.closure_audited()
