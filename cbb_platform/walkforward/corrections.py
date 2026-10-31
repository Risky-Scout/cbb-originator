"""BOX-LEVEL CORRECTION DETECTION between two production state versions (directive section 27).

A correction is a MATERIAL change of an official box already in a model version's training state: a player or team box
row added, removed or with a changed value. Identity and diff use ONE normalized representation, so they never
disagree and are independent of row order and storage dtype:
    null / None / NaN / NaT        -> None (all equal)
    numbers                        -> float (5 == 5.0; booleans stay booleans)
    timestamps (naive = UTC)       -> UTC ISO-8601 (the same instant in two time zones is equal)
    arrays                         -> lists of normalized values
Provenance / lineage fields (NON_MATERIAL) never count as a correction.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

BOX_KEY = {"flagship.player_game_outcomes": ("canonical_team_id", "canonical_player_id"),
           "flagship.team_game_outcomes": ("canonical_team_id",)}
NON_MATERIAL = ("player_game_key", "event_time", "historical_knowable_at", "source_system", "source_file",
                "source_record_key", "ingestion_version", "received_at", "processed_at")


def normalize(x):
    if isinstance(x, (list, tuple, np.ndarray)):
        return [normalize(v) for v in x]
    if x is None or x is pd.NaT or x is pd.NA:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, float, np.integer, np.floating)):
        return None if np.isnan(float(x)) else float(x)
    if isinstance(x, (pd.Timestamp, dt.datetime, np.datetime64)):
        t = pd.Timestamp(x)
        return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).isoformat()
    return x


def records(df: pd.DataFrame, rel: str) -> dict:
    """{key tuple: {material column: normalized value}} (row order irrelevant)."""
    key = BOX_KEY[rel]
    cols = [c for c in df.columns if c not in NON_MATERIAL and c not in key and c != "canonical_game_id"]
    out = {}
    for r in df.to_dict("records"):
        k = tuple(normalize(r[c]) for c in key)
        if k in out:
            raise ValueError(f"{rel}: duplicate box key {k}")
        out[k] = {c: normalize(r[c]) for c in cols}
    return out


def box_identity(box: dict) -> str:
    body = {rel: sorted([[list(k), v] for k, v in records(df, rel).items()], key=lambda x: json.dumps(x[0]))
            for rel, df in sorted(box.items())}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def box_diff(old: dict, new: dict) -> list[dict]:
    """Material changes: ROW_ADDED / ROW_REMOVED (one entry per row) and VALUE_CHANGED (one per field)."""
    out = []
    for rel, key in BOX_KEY.items():
        a, b = records(old[rel], rel), records(new[rel], rel)
        for k in sorted(set(a) | set(b), key=lambda t: json.dumps(t, default=str)):
            ident = dict(zip(key, k))
            if k not in a:
                out.append({"relation": rel, **ident, "change": "ROW_ADDED", "field": None, "original_value": None,
                            "corrected_value": b[k]})
            elif k not in b:
                out.append({"relation": rel, **ident, "change": "ROW_REMOVED", "field": None, "original_value": a[k],
                            "corrected_value": None})
            else:
                for c in sorted(set(a[k]) | set(b[k])):
                    x, y = a[k].get(c), b[k].get(c)
                    if x != y:
                        out.append({"relation": rel, **ident, "change": "VALUE_CHANGED", "field": c,
                                    "original_value": x, "corrected_value": y})
    return out


def read_box(increment_db: Path, game_id: str) -> dict:
    import duckdb
    con = duckdb.connect(str(increment_db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    out = {}
    for rel in BOX_KEY:
        s, t = rel.split(".")
        have = con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE schema_name = '{s}' AND table_name = '{t}'").fetchone()[0]
        out[rel] = (con.execute(f"SELECT * FROM {rel} WHERE canonical_game_id = ?", [game_id]).df() if have
                    else pd.DataFrame(columns=["canonical_game_id", *BOX_KEY[rel]]))
    con.close()
    return out
