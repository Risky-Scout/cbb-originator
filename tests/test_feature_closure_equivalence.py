"""Exact dependency closure (prod.features.dependency_closure) vs the incumbent full build, on the labeled synthetic
2026-27 FIXTURE season over the REAL frozen canonical history (prod.fixtures; same states as test_walkforward_daily:
a corrected box (A, v1 -> v2), a final with only one team's player box (C), an in-progress game (B), a late-arriving
game, two rounds, upcoming games). Compared: every pregame feature row at every cutoff (cand + team frames, byte
sha), the PIT audit, and a full walk-forward morning + predict run with CBB_FEATURE_CLOSURE ON vs OFF (version
artifacts, feature cache, prediction PMFs). Local-store test (skips without the frozen stores, like
test_walkforward_daily)."""
from __future__ import annotations

import hashlib
import json
from datetime import date

import pandas as pd
import pytest

from cbb_platform import props_v0 as P
from cbb_platform.prod import config as PC, features as PF, fixtures as FX
from cbb_platform.walkforward import config as WC, daily as DL, eligibility as EL, store as SO, training_rows as TR

from tests import test_walkforward_daily as TWD                                # the shared fixture timeline (same states)

pytestmark = TWD.pytestmark
HIST, D4, D7 = TWD.HIST, TWD.D4, TWD.D7


@pytest.fixture(scope="module")
def states(tmp_path_factory):
    work = tmp_path_factory.mktemp("closure")
    s = TWD._states(work / "state")
    s["v5"]()
    return {"s": s, "state_root": work / "state", "work": work}


@pytest.fixture(scope="module")
def timeline(tmp_path_factory):
    """the shared timeline exactly as test_walkforward_daily runs it before predict (v5 not yet applied)"""
    work = tmp_path_factory.mktemp("closure_tl")
    s = TWD._states(work / "state")
    return {"s": s, "state_root": work / "state", "work": work}


def _frames(work, gids, cut, st, closure):
    fr = PF.build(work, gids, PC.PRODUCTION_SEASON, cut, P.load_config(), HIST, st["increment_db"], closure=closure)
    out = {"pit": TR._pit_audit(fr["features_db"], gids), "prov": fr["provenance"]["canonical"]}
    for g in gids:
        c = fr["cand"][fr["cand"]["canonical_game_id"] == g].sort_values("player_game_example_id", kind="mergesort")
        t = fr["team"][fr["team"]["game_id"] == g].sort_values("team_id", kind="mergesort")
        out[g] = (TR._sha_df(c), TR._sha_df(t), len(c), list(c.columns), [str(x) for x in c.dtypes])
    return out


def test_every_pregame_row_at_every_cutoff_is_identical(states):
    sr = states["state_root"]
    versions = EL.state_versions(sr)
    games = pd.concat([TR.game_cutoffs(v["increment_db"], FX.increment_rows(HIST)[0]["flagship.games"]["canonical_game_id"])
                       for v in versions[-1:]]).drop_duplicates("canonical_game_id")
    checked = 0
    for cut, grp in games.groupby("prediction_cutoff_utc"):
        st = EL.state_asof(sr, cut)
        if st is None:
            continue
        # the game's own cutoff, and (same result set, different elapsed time / rest) a later as_of inside the state
        for asof in (cut, cut + pd.Timedelta(minutes=30)):
            gids = sorted(grp["canonical_game_id"])
            a = _frames(states["work"] / "on", gids, asof, st, True)
            b = _frames(states["work"] / "off", gids, asof, st, False)
            assert a["pit"] == b["pit"] and a["pit"]["violations"] == 0
            assert a["prov"]["target_outcome_rows"] == b["prov"]["target_outcome_rows"] == 0
            for g in gids:
                assert a[g] == b[g], (g, asof)                          # values, row count, columns and dtypes
            assert a["prov"]["closure"]["version"] == PF.CLOSURE_VERSION
            assert a["prov"]["counts"]["flagship.player_game_outcomes"] < b["prov"]["counts"]["flagship.player_game_outcomes"]
            checked += len(gids)
    # every version: the corrected box (v2) and the late-arriving game change the information state, never the rule
    for v in versions:
        gids = sorted(FX.increment_rows(HIST)[0]["flagship.games"]["canonical_game_id"])[:4]
        cut = v["created_at"] + pd.Timedelta(hours=1)
        a, b = _frames(states["work"] / "on", gids, cut, v, True), _frames(states["work"] / "off", gids, cut, v, False)
        assert all(a[g] == b[g] for g in gids)
        checked += len(gids)
    assert checked >= 10


def _digest(root, day):
    vd = SO.version_dir(root, day)
    files = {f: hashlib.sha256((vd / f).read_bytes()).hexdigest() for f in SO.FILES.values()}
    feats = {}
    for d in sorted((root / "training" / "features").iterdir()):
        if (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            feats[d.name] = (m["cand_sha256"], m["team_sha256"], m["prediction_cutoff_utc"], m["state_version"])
    return files, feats


def test_morning_fit_and_predictions_identical_closure_on_vs_off(timeline, monkeypatch):
    states = timeline
    sr, out = states["state_root"], {}
    for mode in ("OFF", "ON"):
        monkeypatch.setenv("CBB_FEATURE_CLOSURE", mode)
        root = states["work"] / f"wf_{mode}" / "cbb-originator"
        r = DL.morning(D7, root, sr, HIST, now=TWD._now(D7), log=lambda m: None)
        assert r["DAILY_MODEL_PROMOTION"] == "PROMOTED", r.get("gates")
        q = FX.quotes_files(HIST, states["s"]["meta"], states["work"] / f"quotes_{mode}")
        p = DL.predict(FX.AS_OF, root, q, state_root=sr, history_db=HIST, sims=PC.SIMS,
                       sync_log=states["work"] / f"sync_{mode}.jsonl", odds_cache_root=states["work"] / f"odds_{mode}",
                       odds_collections=None, odds_ledger=states["work"] / f"odds_{mode}.jsonl",
                       odds_record=states["work"] / f"odds_record_{mode}.json")
        assert p["predictions"] > 0, p
        files, feats = _digest(root, D7)
        pmfs = {x: pd.read_parquet(root / "predictions" / x) for x in ("pmfs.parquet", "provenance.parquet")}
        out[mode] = {"files": files, "feats": feats, "pmfs": pmfs, "pred": p}
    assert out["ON"]["files"] == out["OFF"]["files"]                    # fit inputs + artifacts byte-identical
    assert out["ON"]["feats"] == out["OFF"]["feats"] and len(out["ON"]["feats"]) > 0
    # player PMFs: every column except the run identity
    a, b = out["ON"]["pmfs"]["pmfs.parquet"], out["OFF"]["pmfs"]["pmfs.parquet"]
    assert len(a) > 0 and list(a.columns) == list(b.columns)
    assert [c for c in a.columns if not a[c].equals(b[c])] == ["run_id"]
    # derived probabilities / provenance: prediction_id = H(run_id, key) and the production_manifest hash covers the
    # version manifest's CREATED_AT wall clock -- both run identity; every model artifact hash and value is identical
    a, b = out["ON"]["pmfs"]["provenance.parquet"], out["OFF"]["pmfs"]["provenance.parquet"]
    assert len(a) == len(b) > 0
    assert [c for c in a.columns if not a[c].equals(b[c])] == ["prediction_id", "run_id", "artifact_sha256"]
    strip = lambda s: {k: v for k, v in json.loads(s).items() if k != "production_manifest"}
    assert [strip(x) for x in a["artifact_sha256"]] == [strip(x) for x in b["artifact_sha256"]]
    # training inputs: the assembled training frame / finals / OOS frames (sha), labels, eligibility, gates
    tm = {m: json.loads((states["work"] / f"wf_{m}" / "cbb-originator" / "models/2026-11-07/training_manifest.json").read_text())
          for m in ("ON", "OFF")}
    assert tm["ON"]["frames_sha256"] == tm["OFF"]["frames_sha256"] and tm["ON"]["coverage"] == tm["OFF"]["coverage"]
    for f in ("labels.parquet", "eligibility.parquet", "gates.json"):
        assert ((states["work"] / "wf_ON" / "cbb-originator" / "models/2026-11-07" / f).read_bytes()
                == (states["work"] / "wf_OFF" / "cbb-originator" / "models/2026-11-07" / f).read_bytes()), f
    # the manifests differ only by wall clock and by the closure build provenance (how the rows were built)
    for rel in ("models/2026-11-07/version_manifest.json", "models/2026-11-07/training_manifest.json"):
        x, y = (json.loads((states["work"] / f"wf_{m}" / "cbb-originator" / rel).read_text()) for m in ("ON", "OFF"))
        assert _diff_paths(x, y) <= {"CREATED_AT"} | {f"feature_builds.{g}.build.closure" for g in x.get("feature_builds", {})}, rel


def _diff_paths(a, b, pre=""):
    if isinstance(a, dict) and isinstance(b, dict):
        return set().union(*[_diff_paths(a.get(k), b.get(k), f"{pre}.{k}".lstrip(".")) for k in set(a) | set(b)])
    return set() if a == b else {pre}
