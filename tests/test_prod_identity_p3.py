"""Production identity adaptation P-3: a transfer CBBD re-issued an athlete id to is linked back to his history-bearing
canonical player only when every deterministic check holds (prod/cbbd_feed.reissued_athlete_links), and the link
re-keys every increment relation (prod/state.apply_identity_links). 2026-11-10 Droplet: Ilija Milijasevic
(DePaul 198931 -> Wright State 233783) was scored twice in one game under two canonical ids. TEST fixtures only."""
import duckdb
import pandas as pd
import pytest

from cbb_platform.prod import cbbd_feed as F, config as PC, state as ST

H, N = "P-hist0000000001", "P-new00000000001"
ORIG, DEST = "T-origin", "T-dest"
CLS, SEASON = PC.PRESEASON_CLASS_YEAR, PC.PRODUCTION_SEASON


def _ros(pid, team, season, athlete, first="Ilija", last="Milijasevic", height=74.0, city="Belgrade"):
    return {"canonical_player_id": pid, "canonical_team_id": team, "season": season, "source_athlete_id": athlete,
            "first_name": first, "last_name": last, "player_name": f"{first} {last}", "height_inches": height,
            "hometown_city": city}


def _hist(tmp_path, rows=None, extra_players=(), extra_map=()):
    db = tmp_path / "hist.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA {s}")
    con.register("r", pd.DataFrame(rows or [_ros(H, ORIG, CLS, "198931")]))
    con.execute("CREATE TABLE flagship.rosters AS SELECT * FROM r")
    con.register("p", pd.DataFrame({"canonical_player_id": [H, *extra_players]}))
    con.execute("CREATE TABLE core.players AS SELECT * FROM p")
    con.register("m", pd.DataFrame({"provider": ["CBBD"] * (1 + len(extra_map)), "provider_player_key": ["198931", *extra_map],
                                    "canonical_player_id": [H] + [None] * len(extra_map)}))
    con.execute("CREATE TABLE metadata.player_id_map AS SELECT * FROM m")
    con.close()
    return db


def _transfers(**kw):
    t = {"source_transfer_id": "3277", "transfer_year": CLS, "first_name": "Ilija", "last_name": "Milijasevic",
         "canonical_player_id": None, "candidate_canonical_player_id": H, "origin_team_id": ORIG,
         "destination_team_id": DEST, "identity_method": "CANDIDATE_ORIGIN_ROSTER_NAME", "needs_manual_review": True}
    t.update(kw)
    return pd.DataFrame([t])


def _rosters(*extra, **kw):
    return pd.DataFrame([_ros(N, DEST, SEASON, "233783", **kw), *extra])


def test_link_when_every_check_holds(tmp_path):
    lk = F.reissued_athlete_links(_hist(tmp_path), _rosters(), _transfers())
    assert lk[["new_player_id", "hist_player_id", "source_transfer_id", "new_athlete_id", "hist_athlete_id"]].values.tolist() \
        == [[N, H, "3277", "233783", "198931"]]


@pytest.mark.parametrize("case", ["height", "hometown_null", "name_case", "dest_duplicate_name", "hist_on_current_roster",
                                  "new_id_in_history", "athlete_known", "origin_mismatch", "already_resolved",
                                  "two_portal_records"])
def test_no_link_unless_every_deterministic_check_holds(tmp_path, case):
    hist, ros, tr = _hist(tmp_path), _rosters(), _transfers()
    if case == "height":
        ros = _rosters(height=75.0)
    elif case == "hometown_null":
        ros = _rosters(city=None)
    elif case == "name_case":
        ros = _rosters(first="ILIJA")                                  # exact names only, never case-folded / fuzzy
    elif case == "dest_duplicate_name":
        ros = _rosters(_ros("P-other", DEST, SEASON, "555"))
    elif case == "hist_on_current_roster":
        ros = _rosters(_ros(H, "T-elsewhere", SEASON, "198931"))       # existing identity: never overwritten
    elif case == "new_id_in_history":
        hist = _hist(tmp_path, extra_players=(N,))
    elif case == "athlete_known":
        hist = _hist(tmp_path, extra_map=("233783",))
    elif case == "origin_mismatch":
        tr = _transfers(origin_team_id="T-somewhere")
    elif case == "already_resolved":
        tr = _transfers(canonical_player_id=H)
    elif case == "two_portal_records":
        tr = pd.concat([tr, _transfers(source_transfer_id="9999", destination_team_id="T-x")], ignore_index=True)
    assert F.reissued_athlete_links(hist, ros, tr).empty, case


def test_links_are_one_to_one(tmp_path):
    hist = _hist(tmp_path, rows=[_ros(H, ORIG, CLS, "198931"), _ros("P-hist2", "T-o2", CLS, "777", first="Ana", last="B")])
    ros = _rosters(_ros("P-new2", "T-d2", SEASON, "778", first="Ana", last="B"))
    tr = pd.concat([_transfers(), _transfers(source_transfer_id="2", first_name="Ana", last_name="B",
                                             candidate_canonical_player_id="P-hist2", origin_team_id="T-o2",
                                             destination_team_id="T-d2")], ignore_index=True)
    lk = F.reissued_athlete_links(hist, ros, tr)
    assert sorted(lk["new_player_id"]) == sorted(["P-new2", N]) and len(lk) == 2 and lk["hist_player_id"].is_unique


def _increment():
    con = duckdb.connect()
    ST._empty_tables(con)
    for rel, df in {
        "flagship.rosters": pd.DataFrame([_ros(N, DEST, SEASON, "233783")]),
        "flagship.player_game_outcomes": pd.DataFrame({"canonical_game_id": ["G-1"], "canonical_team_id": [DEST],
                                                       "canonical_player_id": [N], "player_game_key": ["PG-1"], "points": [6]}),
        "flagship.substitutions": pd.DataFrame({"canonical_game_id": ["G-1", "G-1"], "canonical_team_id": [DEST, DEST],
                                                "canonical_player_id": [N, N], "sub_in_period": [1, 2],
                                                "sub_in_seconds_remaining": [1004, 1038], "source_athlete_id": ["233783"] * 2}),
        "core.players": pd.DataFrame({"canonical_player_id": [N, "P-x"], "display_name": ["Ilija Milijasevic", "X"]}),
        "core.transfers": _transfers(),
    }.items():
        ST._upsert(con, rel, df)
    return con


def _lk():
    return pd.DataFrame([{"new_player_id": N, "hist_player_id": H, "source_transfer_id": "3277"}])


def test_apply_rekeys_every_relation_once(tmp_path):
    con = _increment()
    rep = ST.apply_identity_links(con, _lk())
    assert rep["skipped_conflict"] == [] and len(rep["applied"]) == 1
    for rel in ("flagship.rosters", "flagship.player_game_outcomes", "flagship.substitutions", "core.transfers", "core.players"):
        assert con.execute(f"SELECT count(*) FROM {rel} WHERE canonical_player_id = ?", [N]).fetchone()[0] == 0, rel
    assert con.execute("SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_player_id = ?", [H]).fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM flagship.substitutions WHERE canonical_player_id = ?", [H]).fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM flagship.rosters WHERE canonical_player_id = ?", [H]).fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM core.players").fetchone()[0] == 1          # the new id's player row dropped
    assert con.execute("SELECT canonical_player_id, identity_method, needs_manual_review FROM core.transfers").fetchone() \
        == (H, "PROD_P3_REISSUED_ATHLETE_ID", False)
    assert ST.apply_identity_links(con, _lk())["applied"][0]["rows"].get("flagship.player_game_outcomes") is None  # idempotent


def test_apply_never_overwrites_a_conflicting_identity():
    con = _increment()
    ST._upsert(con, "flagship.player_game_outcomes", pd.DataFrame(
        {"canonical_game_id": ["G-1"], "canonical_team_id": [DEST], "canonical_player_id": [H], "player_game_key": ["PG-2"],
         "points": [9]}))
    rep = ST.apply_identity_links(con, _lk())
    assert rep["applied"] == [] and rep["skipped_conflict"][0]["relations"] == ["flagship.player_game_outcomes"]
    assert con.execute("SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_player_id = ?", [N]).fetchone()[0] == 1
    assert con.execute("SELECT canonical_player_id FROM core.transfers").fetchone()[0] is None


def test_history_without_the_evidence_columns_links_nothing(tmp_path):
    db = tmp_path / "bare.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA flagship")
    con.execute("CREATE TABLE flagship.rosters AS SELECT 'P-x' AS canonical_player_id, 2026 AS season")
    con.close()
    assert F.reissued_athlete_links(db, _rosters(), _transfers()).empty     # fail closed, never raises into the sync
