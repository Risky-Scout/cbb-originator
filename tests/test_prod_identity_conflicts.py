"""IDENTITY_CONFLICT_SUPPRESSED (prod/identity_conflicts.py): one real player with two canonical identities that cannot be
merged safely (2026-11-11: Khoi Thurmon, Old Dominion) never enters one game's modeling state twice. The suppressed
duplicate contributes zero model-input rows, zero simulation draws, zero PMFs, zero pricing rows; the primary stays
active; nothing is merged or written (a later safe merge stays possible); no other player is affected.
TEST fixtures only (temporary DBs, synthetic FIXTURE history for the end-to-end run)."""
import hashlib
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from cbb_platform.prod import (config as PC, fixtures as FX, identity_conflicts as IC, run as RN, state as ST)

A, B = "P-primary0000001", "P-dup000000000001"                        # primary (history-bearing), suppressed duplicate
DEST, ORIG = "T-dest", "T-origin"
SEASON, CLS = PC.PRODUCTION_SEASON, PC.PRESEASON_CLASS_YEAR


def _entry(a=A, b=B, team=DEST, cid="IC-TEST"):
    return {"conflict_id": cid, "status": IC.STATUS, "player_name": "Khoi Thurmon", "first_name": "Khoi",
            "last_name": "Thurmon", "team_id": team, "primary_canonical_player_id": a, "suppressed_canonical_player_id": b,
            "source_athlete_ids": {a: ["CBBD:201219"], b: ["CBBD:17440"]}, "portal_person_source_id": "46115572",
            "portal_records": [{"source_transfer_id": "3457"}], "attribute_match": {"height_inches": 73},
            "primary_selection": "test", "merge_not_performed_reason": "test", "suppression_reason": "test",
            "recorded_at": "2026-11-11T04:05:08Z", "canonical_merge_performed": False, "historical_records_preserved": True}


def _registry(tmp_path, *entries):
    p = tmp_path / "identity_conflicts.json"
    p.write_text(json.dumps({"conflicts": list(entries)}))
    return p


def _ros(pid, team, season, city="Chicago", height=73.0):
    return {"canonical_player_id": pid, "canonical_team_id": team, "season": season, "player_name": "Khoi Thurmon",
            "first_name": "Khoi", "last_name": "Thurmon", "height_inches": height, "hometown_city": city,
            "hometown_state": "IL"}


def _canonical(tmp_path, rosters=None, transfers=None):
    db = tmp_path / "canonical_asof.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA flagship; CREATE SCHEMA core")
    con.register("r", pd.DataFrame(rosters if rosters is not None else
                                   [_ros(A, ORIG, SEASON - 1), _ros(B, "T-old", SEASON - 3), _ros(B, DEST, SEASON),
                                    {**_ros("P-mate", DEST, SEASON), "player_name": "Mate One", "first_name": "Mate",
                                     "last_name": "One"}]))
    con.execute("CREATE TABLE flagship.rosters AS SELECT * FROM r")
    con.register("t", pd.DataFrame(transfers if transfers is not None else
                                   [{"transfer_year": CLS, "first_name": "Khoi", "last_name": "Thurmon",
                                     "origin_team_id": ORIG, "destination_team_id": DEST, "canonical_player_id": None,
                                     "candidate_canonical_player_id": A, "source_transfer_source_id": "46115572"}]))
    con.execute("CREATE TABLE core.transfers AS SELECT * FROM t")
    con.close()
    return db


def _cand():
    rows = [("G1", DEST, A), ("G1", DEST, B), ("G1", DEST, "P-mate"), ("G1", "T-opp", "P-opp"), ("G2", "T-x", "P-other")]
    return pd.DataFrame([{"canonical_game_id": g, "canonical_team_id": t, "canonical_player_id": p, "f": i}
                         for i, (g, t, p) in enumerate(rows)])


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------------- unit
def test_production_registry_is_khoi_only_no_merge():
    ent = IC.load()
    assert [(e["primary_canonical_player_id"], e["suppressed_canonical_player_id"], e["team_id"]) for e in ent] == \
        [("P-ba2979a21dc89cc5", "P-f998d97f4c162849", "T-f96156a09400fe48")]
    e = ent[0]
    assert e["canonical_merge_performed"] is False and e["historical_records_preserved"] is True
    assert set(e["source_athlete_ids"]) == {"P-ba2979a21dc89cc5", "P-f998d97f4c162849"}


@pytest.mark.parametrize("bad", ["missing", "merged", "same_ids"])
def test_registry_refuses_an_incomplete_or_merging_entry(tmp_path, bad):
    e = _entry()
    if bad == "missing":
        e.pop("merge_not_performed_reason")
    elif bad == "merged":
        e["canonical_merge_performed"] = True
    else:
        e["suppressed_canonical_player_id"] = A
    with pytest.raises(ValueError):
        IC.load(_registry(tmp_path, e))


def test_verify_holds_on_the_deterministic_evidence(tmp_path):
    v = IC.verify(_canonical(tmp_path), _entry())
    assert v["verified"] and all(v[c] for c in IC.CHECKS)


@pytest.mark.parametrize("case", ["hometown", "height", "portal_origin", "portal_person", "primary_on_roster", "no_history"])
def test_verify_fails_when_any_check_fails(tmp_path, case):
    ros = [_ros(A, ORIG, SEASON - 1), _ros(B, DEST, SEASON)]
    tr = [{"transfer_year": CLS, "first_name": "Khoi", "last_name": "Thurmon", "origin_team_id": ORIG,
           "destination_team_id": DEST, "canonical_player_id": None, "candidate_canonical_player_id": A,
           "source_transfer_source_id": "46115572"}]
    if case == "hometown":
        ros[1] = _ros(B, DEST, SEASON, city="Detroit")
    elif case == "height":
        ros[1] = _ros(B, DEST, SEASON, height=75.0)
    elif case == "portal_origin":
        tr[0]["origin_team_id"] = "T-elsewhere"
    elif case == "portal_person":
        tr[0]["source_transfer_source_id"] = "999"
    elif case == "primary_on_roster":
        ros.append(_ros(A, "T-other", SEASON))
    else:
        ros = [ros[1]]
    assert not IC.verify(_canonical(tmp_path, ros, tr), _entry())["verified"], case


def test_suppressed_never_enters_the_frame_primary_stays_others_untouched(tmp_path):
    cand = _cand()
    av = pd.DataFrame([{"game_id": "G1", "player_id": B, "status": "OUT", "received_at": pd.Timestamp("2026-11-11", tz="UTC")},
                       {"game_id": "G1", "player_id": "P-mate", "status": "ACTIVE", "received_at": pd.Timestamp("2026-11-11", tz="UTC")}])
    out, av2, block, rep = IC.apply(cand, av, _canonical(tmp_path), [_entry()], "2026-11-11T23:10:00Z")
    assert B not in set(out["canonical_player_id"])                                       # (1)(2) never in the frame
    assert (out["canonical_player_id"] == A).sum() == 1 and block == {}                   # (5) primary active, priced
    others = cand[~cand["canonical_player_id"].isin([A, B])].reset_index(drop=True)
    pd.testing.assert_frame_equal(out[~out["canonical_player_id"].isin([A])].reset_index(drop=True), others)   # (7)
    assert av2.loc[av2["player_id"] == A, "status"].tolist() == ["OUT"]                   # his report follows the primary
    assert av2.loc[av2["player_id"] == "P-mate", "status"].tolist() == ["ACTIVE"] and B not in set(av2["player_id"])
    c = rep["conflicts"][0]
    assert c["canonical_merge_performed"] is False and c["games"][0]["blocks_game"] is False
    assert c["games"][0]["suppressed_rows_excluded"] == 1 and c["verification"]["verified"]


def test_unverified_evidence_fails_closed_for_this_player_only(tmp_path):
    db = _canonical(tmp_path, [_ros(A, ORIG, SEASON - 1), _ros(B, DEST, SEASON, city="Detroit")])
    out, _, block, _ = IC.apply(_cand(), None, db, [_entry()])
    assert B not in set(out["canonical_player_id"]) and block == {("G1", A): IC.UNVERIFIED}


def test_no_entry_in_the_run_is_a_no_op_and_a_later_merge_stays_possible(tmp_path):
    db = _canonical(tmp_path)
    before = _sha(db)
    merged = _cand()
    merged = merged[merged["canonical_player_id"] != B].reset_index(drop=True)           # (6) after a future safe merge
    out, av, block, rep = IC.apply(merged, None, db, [_entry()])
    pd.testing.assert_frame_equal(out, merged)
    assert block == {} and av is None
    IC.apply(_cand(), None, db, [_entry()])
    assert _sha(db) == before                                                             # nothing is ever written
    unrelated = _cand()[lambda d: d["canonical_game_id"] == "G2"].reset_index(drop=True)
    out, _, _, rep = IC.apply(unrelated, None, db, [_entry()])
    pd.testing.assert_frame_equal(out, unrelated) and rep["conflicts"] == []


def test_guard_blocks_both_if_both_reach_pricing():
    d = pd.DataFrame([{"game_id": "G1", "player_id": p, "market": m, "method": "X", "player_suppression": ""}
                      for p in (A, B, "P-mate") for m in ("PTS", "REB")])
    out, hits = IC.guard(d, [_entry()])
    assert hits == [{"conflict_id": "IC-TEST", "game_id": "G1"}]
    assert set(out.loc[out["player_id"].isin([A, B]), "method"]) == {"NOT_PRICED"}
    assert set(out.loc[out["player_id"] == "P-mate", "method"]) == {"X"}
    ok, hits = IC.guard(d[d["player_id"] != B], [_entry()])
    assert hits == [] and set(ok["method"]) == {"X"}


# ---------------------------------------------------------------------------------------------------- end to end
@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    """Two FIXTURE runs of the actual production path: baseline, then with two pool players of one scored team
    registered as one conflicted player (evidence check forced true: synthetic players have distinct names)."""
    w = tmp_path_factory.mktemp("ic")
    hist = FX.synthetic_history(w / "history.duckdb")
    rows, meta = FX.increment_rows(hist)
    ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], w / "state",
              created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    common = dict(quotes_paths=FX.quotes_files(hist, meta, w / "quotes"), availability=FX.availability(meta),
                  state_root=w / "state", history_db=hist, run_root=w / "runs", sims=2000)
    base = RN.daily(FX.AS_OF, ledger_path=w / "l0.jsonl", identity_conflicts=w / "none.json", **common)
    mi = pd.read_parquet(Path(base["run_dir"]) / "model_inputs.parquet")
    gid = sorted(base["banks"])[0]
    team = sorted(mi.loc[mi["game_id"] == gid, "team_id"].unique())[0]
    a, b = sorted(mi.loc[(mi["game_id"] == gid) & (mi["team_id"] == team), "player_id"])[:2]
    reg = _registry(w, _entry(a, b, team))
    mp = pytest.MonkeyPatch()
    mp.setattr(IC, "verify", lambda db, e: {**{c: True for c in IC.CHECKS}, "verified": True})
    try:
        conf = RN.daily(FX.AS_OF, ledger_path=w / "l1.jsonl", identity_conflicts=reg, **common)
    finally:
        mp.undo()
    return base, conf, gid, a, b, w / "l1.jsonl"


def _read(s, f):
    return pd.read_parquet(Path(s["run_dir"]) / f)


def test_e2e_suppressed_contributes_zero_inputs_simulation_pmfs_pricing(e2e):
    base, conf, gid, a, b, ledger = e2e
    mi, mp = _read(conf, "model_inputs.parquet"), _read(conf, "market_pmfs.parquet")
    assert b not in set(mi["player_id"]) and b not in set(mp["player_id"])                   # (2) features (3) PMFs
    assert (mi["player_id"] == a).sum() == 1                                                  # (1)(5) one identity
    assert mp[mp["player_id"] == a].groupby("market").size().eq(1).all() and (mp["player_id"] == a).sum() == len(PC.MARKETS)
    led = [json.loads(l) for l in ledger.read_text().splitlines()]
    pred = [r["body"]["player_id"] for r in led if r.get("record_type") == "PREDICTION"]
    assert pred and b not in pred                                                             # (4) zero pricing rows
    ap = conf["identity_conflicts"]["appearances"]
    assert ap["model_inputs"]["IC-TEST"] == {a: 1, b: 0} and ap["distributions"]["IC-TEST"][b] == 0
    assert ap["priced_rows"]["IC-TEST"][b] == 0 and ap["double_count_blocked"] == []
    u = pd.read_csv(Path(conf["run_dir"]) / "player_universe.csv")
    assert set(u.loc[u["canonical_player_id"] == b, "suppression_reason"]) == {IC.STATUS}
    assert json.loads((Path(conf["run_dir"]) / "identity_conflicts.json").read_text())["conflicts"][0]["games"][0]["game_id"] == gid
    assert b in set(_read(base, "model_inputs.parquet")["player_id"])                         # baseline: he was modeled


def test_e2e_no_unrelated_game_or_player_is_affected(e2e):
    base, conf, gid, a, b, _ = e2e
    assert set(conf["banks"]) == set(base["banks"])                                          # no game blocked
    for g in base["banks"]:
        if g != gid:
            assert conf["banks"][g]["bank_sha256"] == base["banks"][g]["bank_sha256"], g
    m0 = _read(base, "model_inputs.parquet").drop(columns=["_scored"], errors="ignore")
    m1 = _read(conf, "model_inputs.parquet").drop(columns=["_scored"], errors="ignore")
    keep = lambda m: m[~m["player_id"].isin([a, b])].sort_values(["game_id", "player_id"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(keep(m0), keep(m1))                                         # every other input row identical
    assert "identity_conflicts" not in base
