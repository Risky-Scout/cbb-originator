"""Distributions export: rebuilt only when its content changes, each PMF file read once, identity keys canonical.

2026-11-11 Droplet: every 10-minute quote tick re-read the slate's PMF evidence (each forecast snapshot once per game
it covers) and rewrote a byte-identical 56 MB / 1.4 M-row workbook (~266 s per tick). Two players sharing one display
name (CBBD re-issued athlete id) were summed as one key by the mass check (PMF_RECONCILIATION FAIL, mass error 1.0).
TEST fixtures only.
"""
from pathlib import Path

import pandas as pd

from cbb_platform.prod import exports as EX, pmf_evidence as PE
from tests.test_prod_exports import _game, _pmf, _run, build_tree

TIP = "2026-11-05T00:00:00+00:00"


def _tree(tmp_path, n=3, same_name=False):
    pm = {"G1": [{"game_id": "G1", "team_id": "T1", "player_id": f"P{i}", "market": "PTS", "method": "X",
                  "projection": 2.0 + i, "pmf": _pmf(2.0 + i, 30).tolist()} for i in range(n)]}
    tree = build_tree(tmp_path, [], [_game("G1", TIP)], pmfs=pm)
    if same_name:                                                   # two canonical players, one display label
        lab = tree["ops_root"] / "ticks" / "K1" / "projections.parquet"
        pd.DataFrame([{"game_id": "G1", "player_id": f"P{i}", "team_id": "T1", "player": "Same Name",
                       "game": "Away G1 @ Home"} for i in range(n)]).to_parquet(lab, index=False)
    return tree


def test_unchanged_rows_do_not_rewrite_the_workbook(tmp_path):
    tree = _tree(tmp_path)
    r1 = _run(tree)
    p = Path(r1["distributions"]["path"])
    assert r1["distributions"]["rewritten"] is True
    st = p.stat()
    sem = EX.semantic(p)
    r2 = _run(tree)
    assert r2["distributions"]["rewritten"] is False and r2["distributions"]["status"] == "WRITTEN"
    assert p.stat().st_mtime_ns == st.st_mtime_ns and p.stat().st_ino == st.st_ino and EX.semantic(p) == sem
    assert r2["distributions"]["rows"] == r1["distributions"]["rows"]


def test_changed_source_or_damaged_file_rewrites(tmp_path):
    tree = _tree(tmp_path)
    p = Path(_run(tree)["distributions"]["path"])
    p.write_bytes(p.read_bytes()[:-10])                             # damaged / replaced workbook: never trusted
    assert _run(tree)["distributions"]["rewritten"] is True
    mp = tree["runs_root"] / "R1" / "market_pmfs.parquet"
    df = pd.read_parquet(mp)
    df.loc[0, "projection"] = 9.25                                  # new evidence -> new rows -> rewritten
    df.to_parquet(mp, index=False)
    r = _run(tree)
    assert r["distributions"]["rewritten"] is True
    assert 9.25 in [row[4] for row in EX.read_xlsx(p)["Distributions"]["rows"][1:]]


def test_each_pmf_file_is_read_once_and_rows_are_unchanged(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    games = EX.slate_games(tree["ops_root"], "2026-11-04")
    base, _ = EX.distribution_rows(tree["ops_root"], tree["runs_root"], games, {}, "2026-11-04")
    # durable tick evidence covering two games, read by both
    ev = []
    for g in ("G1", "G2"):
        for i in range(2):
            pmf = _pmf(1.5 + i, 20)
            ev.append({"prediction_receipt_id": f"{g}-{i}", "game_id": g, "player_id": f"P{i}", "team_id": "T1",
                       "stat": "PTS", "prediction_as_of": "2026-11-04T23:00:00+00:00", "model_version": "TEST",
                       "mean": 1.5 + i, "run_id": "R1", "method": "X", "game_label": f"{g} label", "player_label": f"P{i}",
                       "pmf": pmf})
    games2 = pd.DataFrame([{**_game(g, TIP), "tip_utc": pd.Timestamp(TIP), "cutoff_utc": pd.Timestamp("2026-11-04T23:30:00Z"),
                            "prediction_as_of": "2026-11-04T23:00:00+00:00"} for g in ("G1", "G2")])
    reads = []
    monkeypatch.setattr(PE, "read", lambda path: reads.append(str(path)) or "DF")
    monkeypatch.setattr(PE, "distributions", lambda df: ev)
    for g in ("G1", "G2"):
        (tree["ops_root"] / "ticks" / "K1").mkdir(parents=True, exist_ok=True)
        (tree["ops_root"] / "ticks" / "K1" / PE.FILE).write_bytes(b"x")
    rows, missing = EX.distribution_rows(tree["ops_root"], tree["runs_root"], games2, {}, None)
    assert missing == [] and len(reads) == 1                        # one file, two games: parsed once
    nocache = []
    monkeypatch.setattr(EX, "_dists_of", lambda path, gid, cache: (nocache.append(1), [x for x in ev if x["game_id"] == gid])[1])
    rows2, _ = EX.distribution_rows(tree["ops_root"], tree["runs_root"], games2, {}, None)
    assert rows2 == rows and len(nocache) == 2
    assert base                                                     # the legacy (run-directory) path still exports


def test_shared_display_name_is_two_distributions_not_one_key(tmp_path):
    tree = _tree(tmp_path, n=2, same_name=True)
    rep = _run(tree)
    d = rep["distributions"]
    assert d["PMF_RECONCILIATION"] == "PASS" and d["distributions"] == 2 and d["max_abs_mass_error"] <= EX.PMF_TOL
    assert d["canonical_duplicate_keys"] == 0
    assert d["display_label_collisions"] == [[["Away G1 @ Home", "Same Name", "PTS"], [["G1", "P0", "PTS"], ["G1", "P1", "PTS"]]]]
    old_style = {}
    for r in EX.read_xlsx(Path(d["path"]))["Distributions"]["rows"][1:]:
        old_style[(r[0], r[2], r[3])] = old_style.get((r[0], r[2], r[3]), 0) + r[6]
    assert len(old_style) == 1 and abs(list(old_style.values())[0] - 2) < 1e-9   # what the label-keyed check summed


def test_mass_check_still_fails_a_bad_distribution():
    rows = [["g", "t", "p", "PTS", 1.0, 0, 0.5], ["g", "t", "p", "PTS", 1.0, 1, 0.4],
            ["g", "t", "p", "PTS", 1.0, 0, 0.5], ["g", "t", "p", "PTS", 1.0, 1, 0.5]]
    m = EX.pmf_mass_check(rows)
    assert m["distributions"] == 2 and m["PMF_RECONCILIATION"] == "FAIL" and abs(m["max_abs_mass_error"] - 0.1) < 1e-12
