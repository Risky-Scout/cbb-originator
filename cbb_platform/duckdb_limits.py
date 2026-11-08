"""Process-wide DuckDB RESOURCE limits for the shared Droplet (memory cap + disk spill). Resource settings only.

    CBB_DUCKDB_MEMORY_LIMIT   e.g. "1GB": DuckDB buffer-manager cap per connection (operators beyond it spill to disk)
    CBB_DUCKDB_HISTORY_MEMORY_LIMIT  optional, e.g. "700MB": the cap inside low_memory_fetch (frozen history loaders) and
                              for the Stage 3 feature build, where a large pandas result sits next to the engine
    CBB_DUCKDB_TEMP_DIR       spill directory; must be a CBB-owned mutable path (never the immutable release tree);
                              each process spills to its own pid-<pid>/ subdirectory, removed at exit

Why: the frozen loaders (v2.marginal.load_frames / load_participation, v2.structural.load_team_frame, ...) open their
own connections with config={"threads": ...} only, so DuckDB sizes its buffer pool from the host / cgroup memory (80 %)
and, on a read-only database, has no spill directory. Inside the 3200M walk-forward cgroup that buffer pool plus the
pandas copy of the result exceeded the ceiling (2026-11-07 Droplet acceptance: memory.high throttling, no progress).

The frozen loader modules are code-hash tracked (v2/phase3.CODE, v2/jm1_final) and are not edited. install() wraps
duckdb.connect so every connection opened afterwards gets memory_limit / temp_directory, unless the caller already set
them. Neither setting changes a query result: DuckDB external sorts / hash joins / aggregates produce the same rows;
every frozen loader that returns rows in a meaningful order has a total ORDER BY or is sorted on a unique key after the
read (tests/test_duckdb_limits.py proves byte-identical frames with and without a cap that forces spilling).
Unset (Mac, CI): install() is a no-op.

low_memory_fetch() (used ONLY around the frozen history loaders, walkforward/fit.history): measured 2026-11-07 on the
Droplet, the frame query itself runs at ~1.7 GB, but DuckDB's .df() conversion of its 1.38 M x 75 result then grows the
process past 4.8 GB (a COPY to parquet buffers the ordered result outside the cap as well). Inside the context a SELECT /
WITH query's .df() instead (1) streams the result as Arrow record batches from the capped, spilling engine (row order is
the query's), (2) builds the frame DuckDB's .df() builds (one preallocated 2D block per NumPy dtype, filled column by column while
each Arrow column is released; .df()'s own conversion plus pandas consolidation held ~3 copies) with exactly .df()'s
dtype rules (verified:
VARCHAR -> pandas str, an all-NULL VARCHAR -> object None; BOOLEAN / integers -> numpy, or the pandas nullable dtype
when the column holds a NULL; DATE -> datetime64[us]; TIMESTAMPTZ -> the session TimeZone).
Any column type outside that verified set falls back to DuckDB's own .df() for the query. tests/test_duckdb_limits.py
and the Droplet historical-input equivalence (byte-identical hist_*.parquet vs the Mac CONTROL run) prove identity.
"""
from __future__ import annotations

import atexit
import contextlib
import ctypes
import gc
import os
import re
import shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
from pandas.api.internals import create_dataframe_from_blocks

MEM_ENV, TMP_ENV = "CBB_DUCKDB_MEMORY_LIMIT", "CBB_DUCKDB_TEMP_DIR"
HIST_MEM_ENV = "CBB_DUCKDB_HISTORY_MEMORY_LIMIT"     # the history loaders + Stage 3 feature builds (large pandas results)
_SCOPE: list = []
_ORIG = duckdb.connect
_FORBIDDEN_ROOTS = ("/opt/sports-models",)


class DuckDBLimitError(RuntimeError):
    pass


def settings(scope: str | None = None) -> dict:
    """scope "history" (inside low_memory_fetch / the Stage 3 feature build): CBB_DUCKDB_HISTORY_MEMORY_LIMIT when set,
    so the engine leaves room for the large pandas result held next to it; otherwise CBB_DUCKDB_MEMORY_LIMIT."""
    out = {}
    scope = scope or (_SCOPE[-1] if _SCOPE else None)
    m = (os.environ.get(MEM_ENV) or "").strip()
    if scope == "history":
        m = (os.environ.get(HIST_MEM_ENV) or "").strip() or m
    t = (os.environ.get(TMP_ENV) or "").strip()
    if m:
        out["memory_limit"] = m
    if t:
        p = Path(t).resolve()
        if any(str(p).startswith(r) for r in _FORBIDDEN_ROOTS):
            raise DuckDBLimitError(f"{TMP_ENV}={t} is inside the immutable application tree")
        p = p / f"pid-{os.getpid()}"                  # per process: DuckDB's spill file names are fixed, so two
        p.mkdir(parents=True, exist_ok=True)          # processes sharing one directory overwrite each other's files
        _cleanup_at_exit(p)
        out["temp_directory"] = str(p)
    return out


_CLEAN: set = set()


def _cleanup_at_exit(p: Path) -> None:
    if p not in _CLEAN:
        _CLEAN.add(p)
        atexit.register(shutil.rmtree, p, True)


def _connect(database=":memory:", read_only: bool = False, config: dict | None = None, **kw):
    cfg = dict(config or {})
    for k, v in settings().items():
        cfg.setdefault(k, v)
    return _ORIG(database, read_only=read_only, config=cfg, **kw)


def install() -> dict:
    """Idempotent. Returns the settings now applied to new connections ({} = nothing installed)."""
    s = settings()
    if s:
        duckdb.connect = _connect
        _patch_stage3()
    return s


def uninstall() -> None:
    duckdb.connect = _ORIG
    from cbb_platform.stage3 import build as S3B
    if getattr(S3B, "_cbb_frozen_connect", None) is not None:
        S3B.connect = S3B._cbb_frozen_connect


def _patch_stage3() -> None:
    """The frozen stage3.build.connect (used by prod.features for the 2026-27 feature build) hard-codes
    SET memory_limit = '12GB' and a spill directory inside the code tree (modeling/store/duckdb_tmp: the immutable,
    read-only release on the Droplet). With the CBB limits configured it is replaced, for this process only, by the
    same connection with every non-resource setting unchanged (TimeZone UTC, progress bar off, threads,
    preserve_insertion_order) and the CBB cap + per-process spill directory. Resource settings only: no result changes
    (module doc). stage3/build.py itself is not edited."""
    from cbb_platform import config as C
    from cbb_platform.stage3 import build as S3B
    if getattr(S3B, "_cbb_frozen_connect", None) is None:
        S3B._cbb_frozen_connect = S3B.connect

    def connect(path, threads: int | None = None):
        lim = settings("history")
        con = _ORIG(str(path))
        con.execute("SET TimeZone = 'UTC'")
        con.execute("SET enable_progress_bar = false")
        con.execute(f"SET threads = {threads or C.DUCKDB_THREADS}")
        con.execute(f"SET memory_limit = '{lim.get('memory_limit', '12GB')}'")
        con.execute("SET preserve_insertion_order = true")
        if "temp_directory" in lim:
            con.execute(f"SET temp_directory = '{lim['temp_directory']}'")
        return con
    connect.__doc__ = "stage3.build.connect with the CBB resource limits (duckdb_limits._patch_stage3)"
    S3B.connect = connect


def installed() -> bool:
    return duckdb.connect is _connect


# ============================================================================ low-memory .df() (history loaders only)
BATCH_ROWS = 65_536
_SELECT = re.compile(r"^\s*(\(\s*)*(SELECT|WITH)\b", re.IGNORECASE)
_NULLABLE = {pa.bool_(): pd.BooleanDtype(), pa.int8(): pd.Int8Dtype(), pa.int16(): pd.Int16Dtype(),
             pa.int32(): pd.Int32Dtype(), pa.int64(): pd.Int64Dtype(), pa.uint8(): pd.UInt8Dtype(),
             pa.uint16(): pd.UInt16Dtype(), pa.uint32(): pd.UInt32Dtype(), pa.uint64(): pd.UInt64Dtype()}


def _supported(t: pa.DataType) -> bool:
    return (t in _NULLABLE or pa.types.is_floating(t) or pa.types.is_string(t) or pa.types.is_large_string(t)
            or pa.types.is_date32(t) or (pa.types.is_timestamp(t) and t.unit == "us"))


def arrow_column_like_df(c: pa.ChunkedArray, session_tz: str) -> pd.Series:
    """One Arrow column (as DuckDB wrote it to parquet) -> the Series DuckDB's .df() returns for that column."""
    t, n = c.type, len(c)
    if (pa.types.is_string(t) or pa.types.is_large_string(t)) and n and c.null_count == n:
        return pd.Series([None] * n, dtype=object)
    m = _NULLABLE.get(t) if c.null_count else None
    s = c.to_pandas(types_mapper=(lambda _t, m=m: m) if m is not None else None, date_as_object=False)
    if pa.types.is_date32(t):
        s = s.astype("datetime64[us]")
    elif pa.types.is_timestamp(t) and t.tz is not None:
        s = s.dt.tz_convert(session_tz)
    return s


def _predicted_dtype(c: pa.ChunkedArray, session_tz: str):
    t, n, nulls = c.type, len(c), c.null_count
    if (pa.types.is_string(t) or pa.types.is_large_string(t)):
        return np.dtype(object) if n and nulls == n else pd.StringDtype(na_value=np.nan)
    if t in _NULLABLE:
        return _NULLABLE[t] if nulls else np.dtype(t.to_pandas_dtype())
    if pa.types.is_floating(t):
        return np.dtype(t.to_pandas_dtype())
    if pa.types.is_date32(t) or (pa.types.is_timestamp(t) and t.tz is None):
        return np.dtype("datetime64[us]")
    return pd.DatetimeTZDtype("us", session_tz)


def _owned(v):
    """A deep copy of an extension array that owns its memory. Arrow -> pandas conversion is zero-copy for strings (and
    may be for timestamps / nullable values): the column would then keep DuckDB's whole exported record batch (every
    column of it) alive. Same values, same dtype; one contiguous Arrow chunk for strings."""
    if isinstance(v, pd.arrays.ArrowStringArray):       # .copy() of an Arrow-backed array is shallow
        ca = v._pa_array
        one = pa.concat_arrays(list(ca.chunks) + [pa.array([], type=ca.type)])   # always allocates new buffers
        return pd.array(pa.chunked_array([one], type=ca.type), dtype=v.dtype)
    return v.copy()                                      # DatetimeArray / masked arrays: a deep copy


def table_like_df(tab: pa.Table, session_tz: str) -> pd.DataFrame:
    """The DataFrame DuckDB's .df() builds (same values, dtypes and per-dtype block grouping: one C-ordered 2D block per
    NumPy dtype, one block per extension / tz-aware column), filled column by column from `tab` with each Arrow column
    released once copied: the peak is about one copy of the result, not three."""
    names, n = list(tab.column_names), tab.num_rows
    want = [_predicted_dtype(tab.column(c), session_tz) for c in names]
    groups: dict = {}
    for j, d in enumerate(want):
        if isinstance(d, np.dtype):
            groups.setdefault(d, []).append(j)
    arrays = {d: np.empty((len(locs), n), dtype=d) for d, locs in groups.items()}
    row = {j: (d, k) for d, locs in groups.items() for k, j in enumerate(locs)}
    ext = []
    for j, name in enumerate(names):
        s = arrow_column_like_df(tab.column(0), session_tz)
        tab = tab.drop_columns([tab.column_names[0]])           # release the converted Arrow column
        if s.dtype != want[j]:
            raise DuckDBLimitError(f"column {name}: dtype {s.dtype} differs from the predicted .df() dtype {want[j]}")
        if j in row:
            d, k = row[j]
            arrays[d][k] = s.to_numpy()
        else:
            v = _owned(s.array)
            ext.append((v.reshape(1, -1) if isinstance(s.dtype, pd.DatetimeTZDtype) else v, np.array([j])))
        del s
    del tab
    blocks = [(arrays[d], np.array(locs)) for d, locs in groups.items()] + ext
    return create_dataframe_from_blocks(blocks, index=pd.RangeIndex(n), columns=pd.Index(names))


class _Result:
    def __init__(self, conn: "_Conn", sql: str):
        self._conn, self._sql, self._real = conn, sql, None

    def _materialize(self):
        if self._real is None:
            self._real = self._conn._con.execute(self._sql)
        return self._real

    def df(self) -> pd.DataFrame:
        con = self._conn._con
        # read BEFORE the stream opens: any other statement on this connection would end an open stream early
        tz = con.execute("SELECT current_setting('TimeZone')").fetchone()[0]
        reader = con.execute(self._sql).to_arrow_reader(BATCH_ROWS)    # streamed from the capped, spilling engine
        if not all(_supported(f.type) for f in reader.schema) or len(set(reader.schema.names)) != len(reader.schema.names):
            reader.close()
            return con.execute(self._sql).df()           # an unverified type / duplicate names: DuckDB's own conversion
        tab = reader.read_all()                          # compact Arrow batches; nothing else runs until drained
        del reader
        release()
        out = table_like_df(tab, tz)
        del tab
        release()
        return out

    def __getattr__(self, k):
        return getattr(self._materialize(), k)


class _Conn:
    def __init__(self, con, spool: Path):
        self._con, self._spool = con, spool

    def execute(self, sql, *a, **k):
        if a or k or not isinstance(sql, str) or not _SELECT.match(sql):
            self._con.execute(sql, *a, **k)
            return self
        return _Result(self, sql)

    def __getattr__(self, k):
        return getattr(self._con, k)


@contextlib.contextmanager
def history_scope():
    """Connections opened inside get the history cap (settings scope "history"); nothing else changes."""
    _SCOPE.append("history")
    try:
        yield
    finally:
        _SCOPE.pop()


@contextlib.contextmanager
def low_memory_fetch(spool_dir: Path | str | None = None):
    """Inside: every new connection's SELECT / WITH .df() is streamed and converted column by column (module doc).
    Active only when spool_dir or CBB_DUCKDB_TEMP_DIR is configured (the Droplet); otherwise a no-op."""
    base = spool_dir or (os.environ.get(TMP_ENV) and Path(os.environ[TMP_ENV]) / "spool")
    if not base:
        yield False
        return
    spool = Path(base).resolve()
    if any(str(spool).startswith(r) for r in _FORBIDDEN_ROOTS):
        raise DuckDBLimitError(f"spool {spool} is inside the immutable application tree")
    prev = duckdb.connect

    def connect(*a, **k):
        return _Conn(prev(*a, **k), spool)
    duckdb.connect = connect
    _SCOPE.append("history")
    try:
        yield True
    finally:
        _SCOPE.pop()
        duckdb.connect = prev
        release()


SPILL_MIN_BYTES = 64 * 2**20


def spill_frame(df: pd.DataFrame) -> pd.DataFrame:
    """The same DataFrame (same columns, index, dtypes, values and block layout) with each large NumPy block held in a
    memory-mapped file of this process's CBB spill directory instead of anonymous memory: its pages are file-backed and
    reclaimable by the kernel under the cgroup ceiling, while a frozen fit copies what it needs (e.g. marginal.fit_v0's
    frame[mask].copy()) into ordinary memory. The file is unlinked at once (the mapping keeps it until the frame is
    freed). No-op unless CBB_DUCKDB_TEMP_DIR is configured (Droplet) or the frame is small."""
    tmp = settings().get("temp_directory")
    blocks = df._mgr.blocks
    if not tmp or sum(b.values.nbytes for b in blocks if isinstance(b.values, np.ndarray)) < SPILL_MIN_BYTES:
        return df
    out = []
    for i, b in enumerate(blocks):
        v = b.values
        if isinstance(v, np.ndarray) and v.dtype != object and v.nbytes >= 2**20:
            path = Path(tmp) / f"frame-{id(df)}-{i}.npy"
            mm = np.lib.format.open_memmap(path, mode="w+", dtype=v.dtype, shape=v.shape,
                                           fortran_order=bool(v.flags["F_CONTIGUOUS"] and not v.flags["C_CONTIGUOUS"]))
            mm[...] = v
            mm.flush()
            path.unlink()
            v = mm.view(np.ndarray)                  # a plain ndarray (as before); the mapping is its base
        out.append((v, b.mgr_locs.as_array))
    res = create_dataframe_from_blocks(out, index=df.index, columns=df.columns)
    if not res.dtypes.equals(df.dtypes):
        raise DuckDBLimitError("spilled frame dtypes differ")
    return res


def release() -> None:
    """Return freed memory to the OS (Arrow pool + glibc arenas). No effect on any value."""
    gc.collect()
    with contextlib.suppress(Exception):
        pa.default_memory_pool().release_unused()
    with contextlib.suppress(Exception):
        ctypes.CDLL("libc.so.6").malloc_trim(0)
