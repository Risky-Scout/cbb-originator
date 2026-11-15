"""Storage retention (prod/storage_retention.py): every bounded-growth rule deletes / compresses / links ONLY its own
pattern, never a protected tree (state, walk-forward store, ticks, PMF evidence, final exports, secrets); a dry-run
report exists before any change; receipts record every change. TEST fixtures only (temporary trees)."""
import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cbb_platform.prod import ops_store as OS, pmf_evidence as PE, run_receipts as RR, storage_retention as SR

NOW = pd.Timestamp("2026-11-30T10:45:00Z")


def _age(p: Path, days: float):
    t = (NOW - pd.Timedelta(days=days)).timestamp()
    for root, dirs, files in os.walk(p) if p.is_dir() else []:
        for n in dirs + files:
            os.utime(os.path.join(root, n), (t, t))
    os.utime(p, (t, t))


def _w(p: Path, data=b"x" * 100, days=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data) if isinstance(data, bytes) else p.write_text(data)
    if days is not None:
        _age(p, days)
    return p


def _meta(folder: Path, stamp: str, key: str, params: dict, received: str, body=b'{"a":1}'):
    f = folder / f"{stamp}__{key}.json"
    _w(f, body)
    _w(Path(str(f) + ".meta.json"), json.dumps({"endpoint": "games", "params": params, "received_at": received,
                                               "file": f.name, "sha256": "s" + stamp}))
    return f


def _pmf_run(runs: Path, ops: Path, rid: str, days: float, durable_equal=True):
    d = runs / rid
    pm = [np.array([0.25, 0.5, 0.25]), np.array([0.1, 0.9])]
    mp = pd.DataFrame({"game_id": ["G1", "G1"], "player_id": ["P1", "P2"], "market": ["PTS", "REB"],
                       "team_id": ["T1", "T1"], "projection": [1.0, 0.9], "method": ["m", "m"],
                       "pmf": [a.tolist() for a in pm]})
    d.mkdir(parents=True)
    mp.to_parquet(d / "market_pmfs.parquet", index=False)
    (d / "run_summary.json").write_text(json.dumps({"run_id": rid, "predictions": 2,
                                                    "prediction_as_of": str(NOW - pd.Timedelta(days=days))}))
    ev = PE.from_run(d, rid, NOW - pd.Timedelta(days=days), "m1")
    if not durable_equal:
        ev.loc[ev.index[-1], "probability"] = 0.8
        ev.loc[ev.index[-2], "probability"] = 0.2
    p = ops / "ticks" / f"K-{rid}" / PE.FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(p, index=False)
    return d


@pytest.fixture
def base(tmp_path):
    b = tmp_path / "cbb"
    prod, ops = b / "prod", b / "prod" / "ops"
    # protected trees (must survive everything)
    _w(prod / "state" / "versions" / "S1" / "increment.duckdb", days=60)
    _w(prod / "state" / "CURRENT.json", "{}", days=60)
    _w(b / "walkforward" / "cbb-originator" / "models" / "2026-11-01" / "marginal.json", "{}", days=60)
    _w(ops / "ticks" / "K0" / PE.FILE, days=60)
    _w(b / "secrets" / "cookie", days=60)
    _w(b / "exports" / "2026-11-01" / "CBB_Boards_11_01_2026.xlsx", days=60)
    _w(b / "exports" / "2026-11-01" / "boards_receipt.json", "{}", days=60)
    # incomplete runs: old one (scratch deleted, inputs kept), fresh one kept
    _w(prod / "runs" / "R-old-incomplete" / "features" / "features_asof.duckdb", days=3)
    _w(prod / "runs" / "R-old-incomplete" / "model_inputs.parquet", days=3)
    _w(prod / "runs" / "R-new-incomplete" / "features" / "features_asof.duckdb", days=0.2)
    # validation runs
    _w(prod / "odds" / "validation_runs" / "V1" / "R1" / "features" / "canonical_asof.duckdb", days=3)
    _w(prod / "odds" / "validation_runs" / "V1" / "odds_validation.json", "{}", days=3)
    _w(prod / "odds" / "validation_runs" / "V2" / "R2" / "features" / "canonical_asof.duckdb", days=1)
    # spill: dead pid (old), live pid (this process)
    _w(b / "staging" / "tmp" / "duckdb" / "pid-999999999" / "spill.tmp", days=1)
    _w(b / "staging" / "tmp" / "duckdb" / f"pid-{os.getpid()}" / "spill.tmp", days=1)
    # staging and QA
    _w(b / "staging" / "equiv" / "big.duckdb", days=20)
    _w(b / "staging" / "recent" / "x.py", days=2)
    _w(b / "staging" / "cleanup-quarantine-2026-11-14" / "x", days=30)
    _w(b / "staging" / "tmp" / "old_scratch" / "x", days=5)
    _w(b / "qa" / "old-probe" / "replay.duckdb", days=20)
    _w(b / "qa" / "accepted" / "RETAIN.json", "{}", days=40)
    _w(b / "qa" / "accepted" / "result.json", "{}", days=40)
    _w(b / "qa" / "fresh" / "x", days=3)
    # exports temp
    _w(b / "exports" / "2026-11-01" / "CBB_Distributions_11_01_2026.xlsx.tmp", days=2)
    # forecast ledgers: old (gz), recent ET day (kept)
    _w(ops / "forecasts" / "2026-11-20" / "F1" / "forecast_ledger.jsonl", '{"r":1}\n' * 500, days=10)
    _w(ops / "forecasts" / "2026-11-20" / "F1" / PE.FILE, days=10)
    _w(ops / "forecasts" / "2026-11-29" / "F2" / "forecast_ledger.jsonl", '{"r":2}\n' * 50)
    # raw cache: an old superseded payload, the newest payload (old but newest: kept), a recent superseded one
    g = prod / "cache" / "raw" / "cbbd" / "games"
    _meta(g, "20261101T000000000000Z", "k1", {"season": 2027}, "2026-11-01T00:00:00+00:00")
    _meta(g, "20261102T000000000000Z", "k1", {"season": 2027}, "2026-11-02T00:00:00+00:00")
    _meta(g, "20261103T000000000000Z", "k2", {"season": 2027, "team": "X"}, "2026-11-03T00:00:00+00:00")
    _meta(g, "20261125T000000000000Z", "k3", {"day": 1}, "2026-11-25T00:00:00+00:00")
    _meta(g, "20261126T000000000000Z", "k3", {"day": 1}, "2026-11-26T00:00:00+00:00")
    # rotowire raw
    _w(prod / "availability" / "rotowire_web" / "raw" / "2026-11-02" / "page.html", "<html>" * 50)
    _w(prod / "availability" / "rotowire_web" / "raw" / "2026-11-28" / "page.html", "<html>")
    # ledgers: canonical + prefix mirror
    _w(prod / "cache" / "cbbd_production_requests.jsonl", '{"a":1}\n{"a":2}\n')
    _w(ops / "ledgers" / "cbbd_production_requests.jsonl", '{"a":1}\n')
    _w(prod / "sync" / "sync_log.jsonl", '{"s":1}\n')
    _w(ops / "ledgers" / "sync_log.jsonl", '{"DIVERGED":1}\n')
    # run PMFs: duplicated (-> reference, whatever the age), differing (kept)
    _pmf_run(prod / "runs", ops, "R-pmf-dup", 10)
    _pmf_run(prod / "runs", ops, "R-pmf-diff", 10, durable_equal=False)
    _pmf_run(prod / "runs", ops, "R-pmf-recent", 2)
    return SR.Paths(b, prod, b / "staging" / "tmp" / "duckdb")


def _paths(p):
    return {(i["rule"], str(Path(i["path"]).relative_to(p.base))) for i in SR.plan(p, NOW)["items"]}


def test_plan_selects_exactly_the_expected_targets(base):
    assert _paths(base) == {
        ("ORPHAN_RUN_RECEIPT", "prod/runs/R-old-incomplete/run_failure.json"),
        ("INCOMPLETE_RUN_SCRATCH", "prod/runs/R-old-incomplete/features/features_asof.duckdb"),
        ("VALIDATION_RUN_SCRATCH", "prod/odds/validation_runs/V1/R1/features/canonical_asof.duckdb"),
        ("DUCKDB_SPILL", "staging/tmp/duckdb/pid-999999999"),
        ("STAGING", "staging/equiv"),
        ("STAGING", "staging/tmp/old_scratch"),
        ("QA", "qa/old-probe"),
        ("EXPORT_TEMP", "exports/2026-11-01/CBB_Distributions_11_01_2026.xlsx.tmp"),
        ("FORECAST_LEDGER_GZIP", "prod/ops/forecasts/2026-11-20/F1/forecast_ledger.jsonl"),
        ("RAW_CACHE_SUPERSEDED", "prod/cache/raw/cbbd/games/20261101T000000000000Z__k1.json"),
        ("RAW_CACHE_SUPERSEDED", "prod/cache/raw/cbbd/games/20261101T000000000000Z__k1.json.meta.json"),
        ("ROTOWIRE_RAW_GZIP", "prod/availability/rotowire_web/raw/2026-11-02/page.html"),
        ("RUN_PMF_DUPLICATE", "prod/runs/R-pmf-dup/market_pmfs.parquet"),
        ("RUN_PMF_DUPLICATE", "prod/runs/R-pmf-recent/market_pmfs.parquet"),
        ("LEDGER_MIRROR_LINK", "prod/ops/ledgers/cbbd_production_requests.jsonl"),
    }


def test_dry_run_changes_nothing(base):
    before = sorted((str(p), p.stat().st_size) for p in base.base.rglob("*") if p.is_file())
    out = SR.run(base, apply=False, now=NOW)
    after = sorted((str(p), p.stat().st_size) for p in base.base.rglob("*") if p.is_file() and "retention" not in str(p))
    assert out["mode"] == "DRY_RUN" and out["changed"] == 0 and Path(out["report"]).exists()
    assert after == before


def test_apply_changes_only_targets_and_keeps_protected_trees(base):
    b, prod, ops = base.base, base.prod, base.ops
    led = (ops / "forecasts" / "2026-11-20" / "F1" / "forecast_ledger.jsonl").read_bytes()
    page = (prod / "availability" / "rotowire_web" / "raw" / "2026-11-02" / "page.html").read_bytes()
    out = SR.run(base, apply=True, now=NOW)
    assert out["changed"] == 15
    rec = json.loads((prod / "runs" / "R-old-incomplete" / RR.FAILURE_FILE).read_text())
    assert rec["status"] == "ORPHANED" and rec["run_id"] == "R-old-incomplete" and rec["detected_by"] == "storage_retention"
    assert RR.state(prod / "runs" / "R-old-incomplete") == "ORPHANED"
    assert not (prod / "runs" / "R-new-incomplete" / RR.FAILURE_FILE).exists()     # younger than ORPHAN_HOURS
    for p in ("prod/state/versions/S1/increment.duckdb", "prod/state/CURRENT.json", "secrets/cookie",
              "walkforward/cbb-originator/models/2026-11-01/marginal.json", f"prod/ops/ticks/K0/{PE.FILE}",
              "exports/2026-11-01/CBB_Boards_11_01_2026.xlsx", "exports/2026-11-01/boards_receipt.json",
              "prod/runs/R-old-incomplete/model_inputs.parquet", "prod/runs/R-new-incomplete/features/features_asof.duckdb",
              "prod/odds/validation_runs/V1/odds_validation.json", "prod/odds/validation_runs/V2/R2/features/canonical_asof.duckdb",
              f"staging/tmp/duckdb/pid-{os.getpid()}/spill.tmp", "staging/recent/x.py", "staging/cleanup-quarantine-2026-11-14/x",
              "qa/accepted/result.json", "qa/fresh/x", "prod/ops/forecasts/2026-11-29/F2/forecast_ledger.jsonl",
              f"prod/ops/forecasts/2026-11-20/F1/{PE.FILE}", "prod/runs/R-pmf-diff/market_pmfs.parquet",
              "prod/runs/R-pmf-recent/market_pmfs.ref.json", "prod/runs/R-pmf-dup/run_summary.json",
              "prod/runs/R-pmf-dup/market_pmfs.ref.json",
              "prod/cache/raw/cbbd/games/20261102T000000000000Z__k1.json",          # newest of its request
              "prod/cache/raw/cbbd/games/20261103T000000000000Z__k2.json",          # only payload of its request
              "prod/cache/raw/cbbd/games/20261125T000000000000Z__k3.json",          # superseded but recent
              "prod/availability/rotowire_web/raw/2026-11-28/page.html"):
        assert (b / p).exists(), p
    for p in ("prod/runs/R-old-incomplete/features/features_asof.duckdb", "staging/equiv", "qa/old-probe",
              "staging/tmp/duckdb/pid-999999999", "prod/runs/R-pmf-dup/market_pmfs.parquet",
              "prod/runs/R-pmf-recent/market_pmfs.parquet",
              "prod/cache/raw/cbbd/games/20261101T000000000000Z__k1.json",
              "prod/cache/raw/cbbd/games/20261101T000000000000Z__k1.json.meta.json",
              "exports/2026-11-01/CBB_Distributions_11_01_2026.xlsx.tmp"):
        assert not (b / p).exists(), p
    gz = ops / "forecasts" / "2026-11-20" / "F1" / "forecast_ledger.jsonl.gz"
    assert gzip.decompress(gz.read_bytes()) == led
    assert gzip.decompress((prod / "availability" / "rotowire_web" / "raw" / "2026-11-02" / "page.html.gz").read_bytes()) == page
    a, m = prod / "cache" / "cbbd_production_requests.jsonl", ops / "ledgers" / "cbbd_production_requests.jsonl"
    assert os.path.samefile(a, m) and m.read_text() == '{"a":1}\n{"a":2}\n'
    assert (ops / "ledgers" / "sync_log.jsonl").read_text() == '{"DIVERGED":1}\n'      # diverged mirror never linked
    (prod / "sync" / "sync_log.jsonl").unlink()                   # (the diverged fixture would make mirror_out raise)
    (ops / "ledgers" / "sync_log.jsonl").unlink()
    OS.mirror_out(ops, prod)                                       # the production mirror is a no-op on a link
    OS.mirror_in(ops, prod)
    assert os.path.samefile(a, m)
    rec = [json.loads(l) for l in (ops / "retention" / "storage_retention.jsonl").read_text().splitlines()]
    assert rec[-1]["summary"] and rec[-1]["changed"] == 15 and len(rec) == 16
    for rid in ("R-pmf-dup", "R-pmf-recent"):                     # the reference resolves to the same PMFs
        got = PE.read_run_pmfs(prod / "runs" / rid)
        assert [list(v) for v in got.sort_values(["player_id"])["pmf"]] == [[0.25, 0.5, 0.25], [0.1, 0.9]]
    again = SR.run(base, apply=True, now=NOW)                      # idempotent
    assert again["changed"] == 0


def test_appending_to_the_linked_ledger_is_seen_by_both(base):
    SR.run(base, apply=True, now=NOW)
    with open(base.prod / "cache" / "cbbd_production_requests.jsonl", "a") as f:
        f.write('{"a":3}\n')
    assert (base.ops / "ledgers" / "cbbd_production_requests.jsonl").read_text().endswith('{"a":3}\n')


def test_guard_refuses_protected_and_foreign_paths(base):
    for bad, pat in ((base.prod / "state" / "versions" / "S1" / "increment.duckdb", "prod/state/*"),
                     (base.base / "walkforward" / "x", "walkforward/*"),
                     (base.ops / "ticks" / "K0" / PE.FILE, "prod/ops/ticks/*/*"),
                     (base.base.parent / "elsewhere", "*"),
                     (base.base / "exports" / "2026-11-01" / "CBB_Boards_11_01_2026.xlsx", "qa/*")):
        with pytest.raises(RuntimeError, match="refusing"):
            SR._guard(base, bad, pat)


def test_a_tampered_plan_is_refused_before_any_change(base, monkeypatch):
    real = SR.plan

    def evil(*a, **k):
        p = real(*a, **k)
        p["items"][-1]["path"] = str(base.prod / "state" / "CURRENT.json")
        return p
    monkeypatch.setattr(SR, "plan", evil)
    with pytest.raises(RuntimeError, match="refusing"):
        SR.run(base, apply=True, now=NOW)
    assert (base.prod / "state" / "CURRENT.json").exists()
    assert (base.base / "staging" / "equiv" / "big.duckdb").exists()          # nothing changed before the refusal


def test_cli_runs_scratch_then_storage(base, capsys, monkeypatch):
    from cbb_platform.prod import retention as RT
    monkeypatch.delenv("CBB_DUCKDB_TEMP_DIR", raising=False)
    assert RT.main(["--runs-root", str(base.prod / "runs"), "--ops-root", str(base.ops)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_scratch"]["mode"] == "DRY_RUN" and out["storage"]["mode"] == "DRY_RUN"
    assert Path(out["storage"]["report"]).exists() and out["storage"]["changed"] == 0


def test_run_json_gzip_round_trip_and_only_completed_runs(tmp_path):
    b = tmp_path / "cbb"
    prod = b / "prod"
    old = datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()
    for rid, done in (("R-done", True), ("R-failed", False)):
        d = prod / "runs" / rid
        _w(d / "bundle" / "predictions.json", json.dumps({"rid": rid, "x": list(range(500))}))
        _w(d / "game_diagnostics.json", json.dumps([{"game_id": "G1", "rid": rid}]))
        if done:
            _w(d / "run_summary.json", json.dumps({"run_id": rid}))
        for f in d.rglob("*"):
            os.utime(f, (old, old))
    P = SR.Paths(b, prod, b / "staging" / "tmp" / "duckdb")
    want = {p: p.read_bytes() for p in (prod / "runs" / "R-done").rglob("*.json") if p.name != "run_summary.json"}
    out = SR.run(P, apply=True, now=NOW)
    assert out["by_rule"]["RUN_JSON_GZIP"]["items"] == 2
    for p, data in want.items():
        assert not p.exists() and gzip.decompress(p.with_name(p.name + ".gz").read_bytes()) == data
    assert (prod / "runs" / "R-failed" / "game_diagnostics.json").exists()          # not a completed run: untouched
    assert (prod / "runs" / "R-done" / "run_summary.json").exists()


def test_model_audit_and_footprint_are_report_only(tmp_path):
    b = tmp_path / "cbb"
    W = b / "walkforward" / "cbb-originator"
    for day, complete in (("2026-11-13", True), ("2026-11-14", True), ("2026-11-15", True), ("2026-11-16", False)):
        _w(W / "models" / day / "marginal.json", json.dumps({"d": day}))
        if complete:
            _w(W / "models" / day / "MANIFEST.sha256", day)
    _w(W / "models" / "2026-11-13" / "same.bin", "identical")
    _w(W / "models" / "2026-11-14" / "same.bin", "identical")
    _w(W / "active" / "ACTIVE_MODEL.json", json.dumps({"ACTIVE_MODEL_VERSION": "cbb-jm1-2026-27-20261115"}))
    _w(W / "rollback" / "ROLLBACK_MODEL.json", json.dumps({"ROLLBACK_MODEL_VERSION": "cbb-jm1-2026-27-20261114"}))
    _w(b / "prod" / "ops" / "forecasts" / "index.jsonl", json.dumps({"model_version": "cbb-jm1-2026-27-20261113"}) + "\n")
    _w(b / "qa" / "big" / "x.bin", "0" * 10)
    P = SR.Paths(b, b / "prod", b / "staging" / "tmp" / "duckdb")
    before = sorted(str(p) for p in b.rglob("*"))
    m = SR.model_audit(P)
    assert {x["dir"]: x["class"] for x in m["models"]} == {"2026-11-13": "REFERENCED_BY_RETAINED_FORECAST",
                                                          "2026-11-14": "ROLLBACK", "2026-11-15": "ACTIVE",
                                                          "2026-11-16": "INCOMPLETE"}
    assert ["models/2026-11-13/same.bin", "models/2026-11-14/same.bin"] in m["duplicate_payload_files"]
    fp = SR.footprint(P)
    assert fp["files"] == len([p for p in b.rglob("*") if p.is_file()]) and fp["areas"]["walkforward"] > 0
    import cbb_platform.prod.storage_retention as mod
    old = mod.LARGE_DIR_BYTES
    try:
        mod.LARGE_DIR_BYTES = 5
        fp = SR.footprint(P)
        assert "qa/big" in fp["large_dirs_without_reason"]                  # no RETAIN.json -> flagged
        _w(b / "qa" / "big" / "RETAIN.json", "{}")
        assert "qa/big" not in SR.footprint(P)["large_dirs_without_reason"]
    finally:
        mod.LARGE_DIR_BYTES = old
    (b / "qa" / "big" / "RETAIN.json").unlink()
    assert sorted(str(p) for p in b.rglob("*")) == before                    # nothing changed
