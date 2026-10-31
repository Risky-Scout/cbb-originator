"""Reconciliation assertion semantics (architect ruling 2026-10-14): exact equality for ids, keys, row counts, integers,
strings, booleans and null masks; derived floats equal within |d| <= 1e-12 or |d| <= 1e-12 * max(|a|, |b|)."""
from __future__ import annotations

import duckdb
import pytest

from cbb_platform import cbbd_box_route_reconcile as R


def _con(ref_rows: str, rt_rows: str, cols: str = "id VARCHAR, season INTEGER, n BIGINT, flag BOOLEAN, x DOUBLE"):
    con = duckdb.connect()
    for db, rows in (("ref", ref_rows), ("rt", rt_rows)):
        con.execute(f"ATTACH ':memory:' AS {db}")
        con.execute(f"CREATE SCHEMA {db}.feat")
        con.execute(f"CREATE TABLE {db}.feat.t ({cols})")
        con.execute(f"INSERT INTO {db}.feat.t VALUES {rows}")
    return con


@pytest.mark.parametrize("a,b,equal", [
    (1.0, 1.0, True), (0.1, 0.1 + 2.8e-14, True), (1e-13, 9e-13, True),          # absolute rule
    (5000.0, 5000.0 + 1.36e-12, True),                                          # relative rule (abs > 1e-12)
    (1.0, 1.0 + 1e-9, False), (0.0, 2e-12, False),
    (None, None, True), (None, 0.0, False), (float("nan"), float("nan"), True), (float("nan"), 1.0, False)])
def test_float_equality_rule(a, b, equal):
    sql = R.float_equal_sql("a", "b")
    assert duckdb.connect().execute(f"SELECT coalesce({sql}, FALSE) FROM (SELECT ?::DOUBLE AS a, ?::DOUBLE AS b)",
                                    [a, b]).fetchone()[0] is equal


def test_e7_float_noise_passes_but_exact_columns_do_not_get_tolerance():
    con = _con("('p1', 2020, 3, TRUE, 0.1), ('p2', 2020, 4, FALSE, 7.0)",
               "('p1', 2020, 3, TRUE, 0.10000000000002), ('p2', 2020, 4, FALSE, 7.0)")
    r = R.e7_table(con, "feat.t", "WHERE season BETWEEN 2014 AND 2023")
    assert r["pass"] and not r["byte_identical"] and r["float_differences"]["x"]["outside_tolerance"] == 0
    for bad in ("('p1', 2020, 3, FALSE, 0.1), ('p2', 2020, 4, FALSE, 7.0)",     # boolean
                "('p1', 2020, 9, TRUE, 0.1), ('p2', 2020, 4, FALSE, 7.0)",      # integer
                "('p9', 2020, 3, TRUE, 0.1), ('p2', 2020, 4, FALSE, 7.0)",      # id
                "('p1', 2020, 3, TRUE, NULL), ('p2', 2020, 4, FALSE, 7.0)",     # null mask
                "('p1', 2020, 3, TRUE, 0.1)",                                   # row count
                "('p1', 2020, 3, TRUE, 0.1000001), ('p2', 2020, 4, FALSE, 7.0)"):  # float outside tolerance
        con = _con("('p1', 2020, 3, TRUE, 0.1), ('p2', 2020, 4, FALSE, 7.0)", bad)
        assert not R.e7_table(con, "feat.t", "")["pass"], bad


def test_e6_compare_table_float_tolerance_only_for_float_columns():
    con = _con("('p1', 2024, 3, TRUE, 0.1)", "('p1', 2024, 3, TRUE, 0.10000000000002)")
    r = R.compare_table(con, "feat.t", ("id",), None, exclude=(), float_tol=True)
    assert r["identical"] and r["float_columns_within_tolerance_only"] == {"x": 1}
    exact = R.compare_table(con, "feat.t", ("id",), None, exclude=())
    assert not exact["identical"] and exact["columns"]["x"]["n"] == 1


# ============================================================================ directive 2026-10-15
def test_one_authoritative_full_report_at_the_path_readiness_reads():
    from cbb_platform import config as C, lockbox_readiness as L
    assert R.FULL_OUT == C.QA / "cbbd_box_route_reconciliation.json"
    assert str(R.FULL_OUT.relative_to(C.PROJECT_ROOT)) == L.ROUTE_FULL
    import inspect
    assert "cbbd_box_route_full_reconciliation" not in inspect.getsource(R)


def _three(ref_rows, rt_rows, ab_rows, cols="id VARCHAR, season INTEGER, n BIGINT, x DOUBLE"):
    con = duckdb.connect()
    for db, rows in (("ref", ref_rows), ("rt", rt_rows), ("ab", ab_rows)):
        con.execute(f"ATTACH ':memory:' AS {db}")
        con.execute(f"CREATE TABLE {db}.t ({cols})")
        if rows:
            con.execute(f"INSERT INTO {db}.t VALUES {rows}")
    return con


def test_rowdiff_statuses_columns_float_noise_and_duplicate_multisets():
    con = _three("('a', 2024, 1, 1.0), ('b', 2024, 2, 2.0), ('c', 2025, 3, 3.0), ('d', 2025, 4, 4.0), ('d', 2025, 4, 4.0)",
                 "('a', 2024, 1, 1.0), ('b', 2024, 9, 2.0), ('c', 2025, 3, 3.0000000000000004), ('e', 2025, 5, 5.0), "
                 "('d', 2025, 4, 4.0), ('d', 2025, 4, 4.0)", "")
    r = R.rowdiff(con, "ref.t", "rt.t", ("id",), None, "d1", float_tol=True)
    assert (r["only_ref"], r["only_route"], r["different"], r["float_noise_only_rows"]) == (0, 1, 1, 1)
    assert r["columns"] == {"n": 1} and r["duplicate_keys"] == {"ref": 1, "route": 1}   # identical duplicate groups: no diff
    rows = dict(con.execute("SELECT id, status FROM d1").fetchall())
    assert rows == {"b": "DIFF", "c": "DIFF", "e": "ONLY_ROUTE"}
    con2 = _three("('d', 2025, 4, 4.0), ('d', 2025, 4, 4.0)", "('d', 2025, 4, 4.0), ('d', 2025, 7, 4.0)", "")
    assert R.rowdiff(con2, "ref.t", "rt.t", ("id",), None, "d2")["different"] == 1         # conflicting multiset


def test_attribution_uses_the_counterfactual_not_proximity():
    """A route difference that disappears when only the team-box revisions are removed is caused by them; one that
    persists is OTHER (untraced); a difference that exists only in the ablation is also untraced."""
    ref = "('a', 2024, 1, 1.0), ('b', 2024, 2, 2.0), ('c', 2024, 3, 3.0)"
    con = _three(ref, "('a', 2024, 9, 1.0), ('b', 2024, 8, 2.0), ('c', 2024, 3, 3.0)",
                 "('a', 2024, 1, 1.0), ('b', 2024, 8, 2.0), ('c', 2024, 7, 3.0)")
    R.rowdiff(con, "ref.t", "rt.t", ("id",), None, "q_rt")
    R.rowdiff(con, "ref.t", "ab.t", ("id",), None, "q_ab")
    a = R.attribute(con, "q", ("id",))
    assert a["by_cause"]["EXPLAINED_PROVIDER_DATA_REVISION"] == 1 and a["by_cause"]["OTHER"] == 1
    assert a["ablation_only_differences"] == 1 and a["untraced"] == 2
    assert dict(con.execute("SELECT id, cause FROM q_cause").fetchall()) == {"a": "EXPLAINED_PROVIDER_DATA_REVISION", "b": "OTHER"}


def _result(scale=0.0, cal=0.0, verdict=True):
    t = {"candidate": {"log_score": 1.0 + scale, "crps": 0.5, "mae": 1.0, "rmse": 2.0},
         "rolling_baseline": {"log_score": 1.2, "crps": 0.6, "mae": 1.1, "rmse": 2.1}}
    targets = {k: t for k in ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "MIN")}
    part = {k: {"brier": 0.1, "log_loss": 0.3, "calibration_slope": 1.0, "calibration_in_the_large": cal} for k in ("candidate", "heuristic")}
    integ = {"counts": {"PTS": {"max_row_sum_error": 0.0, "min_entry": 0.0, "p_over_monotonicity_violations": 0,
                                "integer_push_max_error": 0.0, "half_line_max_push": 0.0, "invalid_fair_prices": 0}},
             "MIN": {"max_row_sum_error": 0.0, "min_entry": 0.0}}
    g = {"G1_aggregate": {"pass": verdict, "aggregate": {"candidate": {"log_score": 1.0 + scale, "crps": 0.5}}}, "verdict": "GO"}
    return {"fit_calls": 0, "result_sha256": str(scale + cal), "result": {"counts_and_minutes": {"targets": targets, "n_rows": 1},
            "participation": part, "pricing_integrity": integ, "gates": g, "row_identity_sha256": "x"}}


def test_e8_gated_metrics_use_the_frozen_1e3_tolerance():
    assert R.E8_TOL == 1e-3
    ok = R._compare_runs(_result(), _result(scale=0.0009))
    assert ok["gate_verdicts_identical"] and ok["gated_within_tolerance"] and ok["max_gated_metric_difference"] == pytest.approx(0.0009)
    bad = R._compare_runs(_result(), _result(cal=0.0039))                  # e.g. calibration-in-the-large is gated (G5)
    assert not bad["gated_within_tolerance"] and "G5.candidate.calibration_in_the_large" in bad["gated_metric_differences"]
    assert not R._compare_runs(_result(), _result(verdict=False))["gate_verdicts_identical"]


def _pg_dbs(ref_rows, rt_rows):
    """fact.player_games with the E3 columns; rows: (game, team, player_id|None, name, points)."""
    cols = ["canonical_game_id", "canonical_team_id", "season"] + list(R.E3_COLS)
    con = duckdb.connect()
    for db, rows in (("ref", ref_rows), ("rt", rt_rows), ("ab", rt_rows)):   # ablation keeps route names (removes team boxes only)
        con.execute(f"ATTACH ':memory:' AS {db}")
        con.execute(f"CREATE SCHEMA {db}.fact")
        con.execute(f"CREATE TABLE {db}.fact.player_games ({', '.join(c + (' BIGINT' if c in R.COUNTS or c in ('season', 'minutes') else ' VARCHAR') for c in cols)})")
        for g, t, pid, name, pts in rows:
            v = {c: None for c in cols}
            v.update(canonical_game_id=g, canonical_team_id=t, season=2025, canonical_player_id=pid, source_player_name=name,
                     points=pts, identity_status="RESOLVED" if pid else "UNRESOLVED_NO_MATCH", minutes=20)
            con.execute(f"INSERT INTO {db}.fact.player_games VALUES ({', '.join('?' * len(cols))})", [v[c] for c in cols])
    return con


def test_name_revision_semantics_resolved_and_unresolved_identities():
    """A display-name change with the same player id and stats is a PROVIDER_NAME_REVISION; an unresolved player (no id
    in either build) pairs only one-to-one with identical facts; a stat change or an id change is never a revision."""
    ref = [("G1", "T1", "P1", "Dom Campbell", 10), ("G1", "T1", None, "Cj Hall", 3), ("G2", "T1", "P2", "Ann", 5),
           ("G2", "T1", None, "Bo", 4)]
    rt = [("G1", "T1", "P1", "Dominick Campbell", 10), ("G1", "T1", None, "CJ Hall", 3), ("G2", "T1", "P2", "Anne", 6),
          ("G2", "T1", None, "Bob", 9)]
    con = _pg_dbs(ref, rt)
    R.canonical_differences(con, "rt", "rt", only=("E3",))
    R.canonical_differences(con, "ab", "ab", only=("E3",))
    cls = R.canonical_classification(con, only=("E3",))
    causes = dict(((r[0], r[1]), r[2]) for r in con.execute("SELECT canonical_game_id, player_key, cause FROM e3_cause").fetchall())
    assert causes[("G1", "P1")] == "PROVIDER_NAME_REVISION"
    assert causes[("G1", "UNRESOLVED_NAME:Cj Hall")] == causes[("G1", "UNRESOLVED_NAME:CJ Hall")] == "PROVIDER_NAME_REVISION"
    assert causes[("G2", "P2")] == "OTHER"                                       # name AND points changed: substantive
    assert causes[("G2", "UNRESOLVED_NAME:Bo")] == causes[("G2", "UNRESOLVED_NAME:Bob")] == "OTHER"   # facts differ
    assert cls["E3"]["by_cause"]["UNEXPLAINED"] == 3


def _label_case(ref_rows, rt_rows):
    """A row-hash Stage 3 table with a provider display-text column, compared as stage3_comparison does."""
    con = _three(ref_rows, rt_rows, rt_rows, cols="id VARCHAR, season INTEGER, tournament VARCHAR, x DOUBLE")  # persists in the ablation
    for d in ("ref", "rt", "ab"):
        con.execute(f"CREATE TEMP VIEW v_{d} AS SELECT hash(row(id, season, tournament, x IS NULL)) AS _row_key, "
                    f"hash(row(id, season, x IS NULL)) AS _lf, * FROM {d}.t")
    R.rowdiff(con, "v_ref", "v_rt", ("_row_key",), ["x", "season"], "l_rt", float_tol=True)
    R.rowdiff(con, "v_ref", "v_ab", ("_row_key",), ["x", "season"], "l_ab", float_tol=True)
    a = R.attribute(con, "l", ("_row_key",))
    return R.label_revisions(con, "l", {"ref": "v_ref", "rt": "v_rt"}, ("_row_key",), a, ["tournament"], ["x"])


# The 2026-27 production ingestion path (added 2026-10-20, after the reconciler and its sha-pinned lockbox evidence were
# recorded on 2026-10-16) carries the provider `tournament` text through UNREAD: provider-record reshape (cbbd_feed),
# dtype cast (sync), increment column contract (state) and the synthetic producers of that contract (fixtures,
# feed_fixtures). The reconciler's LABEL_PRODUCERS cannot be extended without breaking the pinned evidence
# (tests/test_lockbox_readiness.py), so the pass-through set is audited here, file by file.
PRODUCTION_PASS_THROUGH = ("cbb_platform/prod/cbbd_feed.py", "cbb_platform/prod/feed_fixtures.py",
                           "cbb_platform/prod/fixtures.py", "cbb_platform/prod/state.py", "cbb_platform/prod/sync.py")
PREDICTIVE_PATHS = ("cbb_platform/prod/features.py", "cbb_platform/prod/inputs.py", "cbb_platform/prod/run.py",
                    "cbb_platform/prod/simulate.py", "cbb_platform/prod/board.py", "cbb_platform/prod/pricing.py",
                    "cbb_platform/stage3/", "cbb_platform/v2/", "cbb_platform/walkforward/", "cbb_platform/props_v")


def test_provider_label_revision_is_explained_only_as_a_consistent_text_relabelling(monkeypatch):
    # pairing semantics, isolated from the repository-wide consumer scan (tested on its own below)
    real = R.label_consumers
    monkeypatch.setattr(R, "label_consumers", lambda col: [])
    ok = _label_case("('a', 2025, 'NCAA Tournament', 1.0), ('b', 2025, 'NCAA Tournament', 2.0), ('c', 2025, NULL, 3.0)",
                     "('a', 2025, 'NCAA', 1.0), ('b', 2025, 'NCAA', 2.0), ('c', 2025, NULL, 3.0)")
    assert ok["by_cause"]["EXPLAINED_PROVIDER_LABEL_REVISION"] == 4 and ok["untraced"] == 0
    assert ok["provider_label_revisions"]["consumers_outside_producers"] == {"tournament": []}
    # a fact change alongside the label change is not a relabelling
    fact = _label_case("('a', 2025, 'NIT', 1.0)", "('a', 2025, 'National Invitation Tournament', 1.5)")
    assert fact["by_cause"]["EXPLAINED_PROVIDER_LABEL_REVISION"] == 0 and fact["untraced"] == 2
    # one reference value mapped to two route values is not one-to-one
    amb = _label_case("('a', 2025, 'CBI', 1.0), ('b', 2025, 'CBI', 2.0)", "('a', 2025, 'X', 1.0), ('b', 2025, 'Y', 2.0)")
    assert amb["by_cause"]["EXPLAINED_PROVIDER_LABEL_REVISION"] == 0 and amb["untraced"] == 4
    # a real downstream consumer still blocks the explanation (the safety rule itself is unchanged)
    monkeypatch.setattr(R, "label_consumers", lambda col: ["cbb_platform/prod/features.py"])
    blocked = _label_case("('a', 2025, 'NCAA Tournament', 1.0)", "('a', 2025, 'NCAA', 1.0)")
    assert blocked["provider_label_revisions"]["applied"] is False
    assert blocked["by_cause"].get("EXPLAINED_PROVIDER_LABEL_REVISION", 0) == 0
    monkeypatch.setattr(R, "label_consumers", real)


def test_provider_label_columns_are_read_by_no_feature_label_or_scoring_code(tmp_path):
    assert R.PROVIDER_LABEL_COLUMNS == ("tournament",)
    found = R.label_consumers("tournament")
    assert set(found) <= set(PRODUCTION_PASS_THROUGH), sorted(set(found) - set(PRODUCTION_PASS_THROUGH))
    assert not [f for f in found if f.startswith(PREDICTIVE_PATHS)]
    assert R.label_consumers("canonical_game_id")            # the scan does find consumed columns
    probe = R.C.PROJECT_ROOT / "cbb_platform" / "prod" / "_label_probe_tmp.py"   # negative control: a new reader is caught
    probe.write_text('COLUMN = "tournament"\n')
    try:
        assert "cbb_platform/prod/_label_probe_tmp.py" in R.label_consumers("tournament")
    finally:
        probe.unlink()


def test_e8_classification_under_the_architect_ruling():
    revs = [{"classification": "EXPLAINED_PROVIDER_DATA_REVISION"}] * 2
    ab_ok = {"gate_verdicts_identical": True, "gated_within_tolerance": True}
    fail = {"pass": False, "ablation_vs_reference": ab_ok}
    assert R.e8_classification({"pass": True}, 0, 0, revs) == "NONE"
    assert R.e8_classification(fail, 0, 0, revs) == "EXPLAINED_PROVIDER_DATA_REVISION"
    assert R.e8_classification(fail, 1, 0, revs) == "UNEXPLAINED"
    assert R.e8_classification(fail, 0, 1, revs) == "UNEXPLAINED"
    assert R.e8_classification(fail, 0, 0, revs + [{"classification": "UNEXPLAINED"}]) == "UNEXPLAINED"
    assert R.e8_classification({"pass": False, "ablation_vs_reference": {**ab_ok, "gated_within_tolerance": False}}, 0, 0, revs) == "UNEXPLAINED"
    assert R.e8_classification({"pass": False, "ablation_vs_reference": {**ab_ok, "gate_verdicts_identical": False}}, 0, 0, revs) == "UNEXPLAINED"
    assert R.E8_TOL == 1e-3
