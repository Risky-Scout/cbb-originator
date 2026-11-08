"""DuckDB resource limits (cbb_platform/duckdb_limits.py): memory cap + spill change no result.

The walk-forward loaders' SQL shapes (window QUALIFY over a state table, repeated LEFT JOINs to it, an inner join to
labels, ORDER BY a unique id; a join with NO ORDER BY that the caller sorts on a unique key) are run on a synthetic
store with and without a cap small enough to force external (spilling) operators: the frames are identical (values,
dtypes, row order, row count: no truncation, no sampling). Synthetic data only; no network.
"""
import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from cbb_platform import duckdb_limits as DLM

N = 120_000


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    d = tmp_path_factory.mktemp("store")
    p = d / "s.duckdb"
    rng = np.random.default_rng(7)
    con = duckdb.connect(str(p))
    ids = rng.permutation(N)
    f = pd.DataFrame({"ex_id": ids, "player": rng.integers(0, 9000, N), "season": rng.integers(2014, 2027, N),
                      "cut": pd.Timestamp("2020-01-01") + pd.to_timedelta(rng.integers(0, 10**8, N), unit="s"),
                      **{f"x{i}": rng.normal(size=N) for i in range(24)},
                      "flag": rng.choice(["PLAYED", "DNP", None], N)})
    b = pd.DataFrame({"player": rng.integers(0, 9000, 3 * N), "season": rng.integers(2013, 2027, 3 * N),
                      "avail": pd.Timestamp("2019-06-01") + pd.to_timedelta(rng.integers(0, 10**8, 3 * N), unit="s"),
                      "src": rng.integers(0, 10**9, 3 * N), **{f"s{i}": rng.normal(size=3 * N) for i in range(8)}})
    lab = pd.DataFrame({"ex_id": ids[rng.permutation(N)], "y": rng.poisson(9, N).astype(float)})
    for name, df in (("f", f), ("b", b), ("lab", lab)):
        con.register("df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    return p


SQL = """WITH fin AS (SELECT player, season, avail, s0 + s1 AS a, s2 * 1.5 AS c, s3 FROM b
             QUALIFY row_number() OVER (PARTITION BY player, season ORDER BY avail DESC, src DESC) = 1)
    SELECT f.*, l.y, h1.a AS h1_a, h1.c AS h1_c, h2.a AS h2_a, h2.s3 AS h2_s3, (f.x0 + f.x1)::DOUBLE AS cur
    FROM f JOIN lab l USING (ex_id)
    LEFT JOIN fin h1 ON h1.player = f.player AND h1.season = f.season - 1 AND h1.avail <= f.cut
    LEFT JOIN fin h2 ON h2.player = f.player AND h2.season = f.season - 2 AND h2.avail <= f.cut
    WHERE f.season BETWEEN 2014 AND 2026 ORDER BY f.ex_id"""
UNORDERED = "SELECT f.season, f.ex_id, f.player, l.y FROM f JOIN lab l USING (ex_id)"


def _frames(store):
    con = duckdb.connect(str(store), read_only=True, config={"threads": 1})
    a = con.execute(SQL).df()
    b = con.execute(UNORDERED).df().sort_values(["season", "ex_id"], kind="mergesort").reset_index(drop=True)
    lim = con.execute("SELECT current_setting('memory_limit'), current_setting('temp_directory')").fetchone()
    con.close()
    return a, b, lim


def test_unset_is_noop(monkeypatch):
    monkeypatch.delenv(DLM.MEM_ENV, raising=False)
    monkeypatch.delenv(DLM.TMP_ENV, raising=False)
    assert DLM.install() == {} and not DLM.installed()


def test_spill_dir_inside_release_tree_refused(monkeypatch):
    monkeypatch.setenv(DLM.TMP_ENV, "/opt/sports-models/cbb-originator/current/tmp")
    with pytest.raises(DLM.DuckDBLimitError):
        DLM.settings()


def test_capped_spilling_results_identical(store, tmp_path, monkeypatch):
    ref, ref_u, _ = _frames(store)
    spill = tmp_path / "spill"
    monkeypatch.setenv(DLM.MEM_ENV, "96MB")
    monkeypatch.setenv(DLM.TMP_ENV, str(spill))
    seen = []
    try:
        assert DLM.install()["memory_limit"] == "96MB" and DLM.installed()
        orig = DLM._ORIG

        def watch(*a, **k):                                   # observe spill files while the query runs
            con = orig(*a, **k)
            seen.append(con)
            return con
        monkeypatch.setattr(DLM, "_ORIG", watch)
        cap, cap_u, lim = _frames(store)
    finally:
        DLM.uninstall()
    assert lim[1] == str(spill.resolve() / f"pid-{os.getpid()}") and ("MiB" in lim[0] or "MB" in lim[0])
    assert len(cap) == len(ref) == N and list(cap.columns) == list(ref.columns)
    assert (cap.dtypes == ref.dtypes).all()
    pd.testing.assert_frame_equal(cap, ref, check_exact=True)             # values, NaN / None semantics, order
    pd.testing.assert_frame_equal(cap_u, ref_u, check_exact=True)
    assert pd.util.hash_pandas_object(cap, index=True).sum() == pd.util.hash_pandas_object(ref, index=True).sum()


def test_caller_setting_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(DLM.MEM_ENV, "64MB")
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "t"))
    try:
        DLM.install()
        con = duckdb.connect(":memory:", config={"memory_limit": "128MB"})
        v = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        con.close()
    finally:
        DLM.uninstall()
    assert v.startswith("122") or v.startswith("128")                    # DuckDB reports MiB


TYPES_SQL = """SELECT i, 'PGE-'||i AS id, CASE WHEN i%3=0 THEN NULL ELSE 'x'||(i%5) END AS s,
  TIMESTAMPTZ '2025-01-01 00:00:00+00' + INTERVAL (i) MINUTE AS ts,
  CASE WHEN i%8=0 THEN NULL ELSE TIMESTAMPTZ '2025-01-01 00:00:00+00' + INTERVAL (i) SECOND END AS tsn,
  TIMESTAMP '2025-01-01 00:00:00' + INTERVAL (i) SECOND AS tsnaive,
  (i%7)::DOUBLE AS d, CASE WHEN i%4=0 THEN NULL ELSE (i%9)::DOUBLE END AS dn, (i%2=0) AS b, (i%7)::FLOAT AS f4,
  CASE WHEN i%5=0 THEN NULL ELSE i%2=0 END AS bn, CASE WHEN i%6=0 THEN NULL ELSE i END::BIGINT AS inn, i::INTEGER AS i32,
  CASE WHEN i%6=0 THEN NULL ELSE i END::INTEGER AS i32n, DATE '2025-01-01' + (i%30)::INTEGER AS dt,
  CASE WHEN i%9=0 THEN NULL ELSE DATE '2025-01-01' + (i%30)::INTEGER END AS dtn,
  (i%3)::SMALLINT AS sm, 'const' AS cst, CASE WHEN TRUE THEN NULL END::VARCHAR AS allnull,
  (i%11)::DECIMAL(9,2) AS dec
  FROM range(20000) t(i) ORDER BY i DESC"""


@pytest.mark.parametrize("tz", ["UTC", "America/New_York"])
def test_low_memory_fetch_equals_native_df_every_type(tmp_path, tz):
    def run():
        con = duckdb.connect(config={"threads": 1})
        con.execute(f"SET TimeZone = '{tz}'")
        a = con.execute(TYPES_SQL).df()
        b = con.execute(TYPES_SQL.replace(", (i%11)::DECIMAL(9,2) AS dec", "")).df()   # no fallback type
        con.close()
        return a, b
    ref_a, ref_b = run()
    with DLM.low_memory_fetch(tmp_path / "spool") as on:
        assert on
        got_a, got_b = run()
    pd.testing.assert_frame_equal(got_a, ref_a, check_exact=True)            # DECIMAL -> native fallback
    pd.testing.assert_frame_equal(got_b, ref_b, check_exact=True)            # every converted type
    assert (got_b.dtypes == ref_b.dtypes).all() and list(got_b.columns) == list(ref_b.columns)
    assert not list((tmp_path / "spool").glob("*"))                          # temporary files removed


def test_low_memory_fetch_loader_shapes_identical_with_spill(store, tmp_path, monkeypatch):
    ref, ref_u, _ = _frames(store)
    monkeypatch.setenv(DLM.MEM_ENV, "96MB")
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "spill"))
    try:
        DLM.install()
        with DLM.low_memory_fetch():
            got, got_u, _ = _frames(store)
    finally:
        DLM.uninstall()
    pd.testing.assert_frame_equal(got, ref, check_exact=True)
    pd.testing.assert_frame_equal(got_u, ref_u, check_exact=True)
    assert len(got) == N


def test_low_memory_fetch_noop_without_configuration(monkeypatch):
    monkeypatch.delenv(DLM.TMP_ENV, raising=False)
    with DLM.low_memory_fetch() as on:
        assert on is False and duckdb.connect is DLM._ORIG


def test_non_select_statements_pass_through(tmp_path):
    with DLM.low_memory_fetch(tmp_path / "s"):
        con = duckdb.connect()
        con.execute("CREATE TABLE t AS SELECT 1 AS a")
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        con.close()


def _layout(d):
    return sorted((type(b).__name__, str(b.dtype), tuple(int(x) for x in b.mgr_locs.as_array)) for b in d._mgr.blocks)


@pytest.mark.parametrize("tz", ["UTC", "America/New_York"])
def test_low_memory_fetch_same_block_grouping_as_native_df(tmp_path, tz):
    sql = TYPES_SQL.replace(", (i%11)::DECIMAL(9,2) AS dec", "")
    con = duckdb.connect(config={"threads": 1})
    con.execute(f"SET TimeZone = '{tz}'")
    ref = con.execute(sql).df()
    con.close()
    with DLM.low_memory_fetch(tmp_path / "s"):
        con = duckdb.connect(config={"threads": 1})
        con.execute(f"SET TimeZone = '{tz}'")
        got = con.execute(sql).df()
        con.close()
    pd.testing.assert_frame_equal(got, ref, check_exact=True)
    assert _layout(got) == _layout(ref)                   # same consolidated 2D blocks (same columns, same order)
    fl = [c for c in ref.columns if ref[c].dtype == np.float64]
    a, b = ref[fl].to_numpy(), got[fl].to_numpy()
    assert a.flags["F_CONTIGUOUS"] == b.flags["F_CONTIGUOUS"] and np.array_equal(a, b, equal_nan=True)


def test_dtype_mismatch_raises_never_returns_a_different_frame(monkeypatch):
    tab = pa.table({"x": pa.array([1, 2, 3], pa.int64())})
    monkeypatch.setattr(DLM, "_predicted_dtype", lambda c, tz: np.dtype("float64"))
    with pytest.raises(DLM.DuckDBLimitError):
        DLM.table_like_df(tab, "UTC")


def test_extension_columns_own_their_memory():
    """A converted column must not keep DuckDB's exported Arrow batch (all of its columns) alive."""
    t = pa.chunked_array([pa.array(["a", None]), pa.array(["bc"])])
    s = t.to_pandas()
    o = DLM._owned(s.array)
    assert o.dtype == s.dtype and pd.Series(o).equals(s) and o._pa_array.num_chunks == 1
    assert o._pa_array.chunk(0).buffers()[2].address not in {c.buffers()[2].address for c in t.chunks}
    ts = pd.Series(pd.date_range("2025-01-01", periods=3, freq="min", tz="UTC")).dt.tz_convert("America/New_York").array
    o2 = DLM._owned(ts)
    assert o2.dtype == ts.dtype and np.shares_memory(o2._ndarray, ts._ndarray) is False and (o2 == ts).all()


def test_spill_directory_is_per_process(monkeypatch, tmp_path):
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "t"))
    d = Path(DLM.settings()["temp_directory"])
    assert d.parent == (tmp_path / "t").resolve() and d.name == f"pid-{os.getpid()}" and d.is_dir()


def test_stage3_connect_gets_the_cbb_limits_and_same_other_settings(monkeypatch, tmp_path):
    from cbb_platform.stage3 import build as S3B
    q = ("SELECT name, value FROM duckdb_settings() WHERE name IN ('TimeZone', 'enable_progress_bar', 'threads', "
         "'memory_limit', 'preserve_insertion_order', 'temp_directory') ORDER BY name")
    frozen = S3B.connect
    ref = dict(frozen(tmp_path / "a.duckdb").execute(q).fetchall())
    monkeypatch.setenv(DLM.MEM_ENV, "256MB")
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "spill"))
    try:
        DLM.install()
        assert S3B.connect is not frozen
        got = dict(S3B.connect(tmp_path / "b.duckdb").execute(q).fetchall())
    finally:
        DLM.uninstall()
    assert S3B.connect is frozen
    for k in ("TimeZone", "enable_progress_bar", "threads", "preserve_insertion_order"):
        assert got[k] == ref[k], k
    assert got["memory_limit"].startswith(("244", "256")) and ref["memory_limit"] != got["memory_limit"]
    assert got["temp_directory"] == str((tmp_path / "spill").resolve() / f"pid-{os.getpid()}")


def test_stage3_connect_untouched_without_configuration(monkeypatch):
    from cbb_platform.stage3 import build as S3B
    monkeypatch.delenv(DLM.MEM_ENV, raising=False)
    monkeypatch.delenv(DLM.TMP_ENV, raising=False)
    before = S3B.connect
    DLM.install()
    assert S3B.connect is before


def test_spill_frame_identical_values_dtypes_and_layout(monkeypatch, tmp_path):
    n = 300_000
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"a": rng.normal(size=n), "s": pd.array([f"x{i % 7}" for i in range(n)], dtype="str"),
                       "b": rng.normal(size=n), "i": rng.integers(0, 9, n).astype("int32"),
                       "t": pd.date_range("2025-01-01", periods=n, freq="s", tz="America/New_York"), "c": rng.normal(size=n)})
    df = df.copy()                                            # consolidated blocks, as a loader / concat returns
    monkeypatch.setattr(DLM, "SPILL_MIN_BYTES", 1)
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "t"))
    got = DLM.spill_frame(df)
    pd.testing.assert_frame_equal(got, df, check_exact=True)
    assert _layout(got) == _layout(df)
    fl = [b for b in got._mgr.blocks if str(b.dtype) == "float64"][0]
    assert type(fl.values) is np.ndarray and isinstance(fl.values.base, np.memmap)
    assert not [p for p in (tmp_path / "t").rglob("frame-*")]           # unlinked; mapping keeps the data
    tr = got[got["i"] <= 8].copy()                                       # the frozen fit's access pattern
    tr["_w"] = 1.0
    pd.testing.assert_frame_equal(tr.drop(columns="_w"), df, check_exact=True)
    a = got[["a", "b", "c"]].to_numpy()
    assert np.array_equal(a, df[["a", "b", "c"]].to_numpy())


def test_spill_frame_noop_without_configuration(monkeypatch):
    monkeypatch.delenv(DLM.TMP_ENV, raising=False)
    df = pd.DataFrame({"a": np.arange(10.0)})
    assert DLM.spill_frame(df) is df


def test_history_scope_cap(monkeypatch, tmp_path):
    from cbb_platform.stage3 import build as S3B
    monkeypatch.setenv(DLM.MEM_ENV, "512MB")
    monkeypatch.setenv(DLM.HIST_MEM_ENV, "128MB")
    monkeypatch.setenv(DLM.TMP_ENV, str(tmp_path / "t"))
    q = "SELECT current_setting('memory_limit')"
    try:
        DLM.install()
        general = duckdb.connect().execute(q).fetchone()[0]
        with DLM.low_memory_fetch():
            con = duckdb.connect()
            hist = con.execute(q).fetchone()[0]
            con.close()
        after = duckdb.connect().execute(q).fetchone()[0]
        s3 = S3B.connect(tmp_path / "s3.duckdb").execute(q).fetchone()[0]
    finally:
        DLM.uninstall()
    assert general.startswith(("488", "512")) and after == general
    assert hist.startswith(("122", "128")) and s3.startswith(("122", "128"))
