"""Source adapters (Step 6).

Each adapter has two halves:
  * fetch/receive  -> writes an immutable raw payload under data/raw/<provider>/ (raw_store.py)
  * normalize      -> reads every raw payload of its provider into staging/canonical tables

`load_all_raw` is called by build_canonical on every build so canonical tables are always a
pure function of (immutable Starter Pack + immutable raw payloads).
"""
from __future__ import annotations

import duckdb


def load_all_raw(con: duckdb.DuckDBPyConnection) -> None:
    from cbb_platform.adapters import availability, bdl, cbbd
    cbbd.normalize_all(con)
    bdl.normalize_all(con)
    availability.normalize_all(con)
