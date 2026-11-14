"""Canonical compact PMF representation: ONE row per (snapshot, game, player, stat) with the complete ordered probability
vector, instead of one row per support point (prod.pmf_evidence long format).

Persist information, not expanded representations: the long rows are a deterministic VIEW (expand) of these rows.

Compact columns (PMF_COLUMNS):
    prediction_receipt_id, game_id, player_id, team_id, stat, prediction_as_of, model_version, mean, run_id, method,
    game_label, player_label     -- the per-PMF fields of the long format (constant within one PMF)
    support_min, support_max     -- the integer support of the vector (long outcomes support_min..support_max)
    probabilities                -- list<double>, P(outcome = support_min + i), the exact float64 values
    pmf_sha256                   -- sha256 of the canonical content: support_min, support_max, float64 bytes

Exactness: the float64 values are stored as Arrow list<double> (lossless); expand() rebuilds the long frame with the same
columns, dtypes, row order (receipt-major, outcome-ascending, i.e. the order pmf_evidence.from_run writes) and bytes.
No trimming of zero mass: the support is the long support exactly.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

PER_PMF = ["prediction_receipt_id", "game_id", "player_id", "team_id", "stat", "prediction_as_of", "model_version",
           "mean", "run_id", "method", "game_label", "player_label"]
PMF_COLUMNS = PER_PMF + ["support_min", "support_max", "probabilities", "pmf_sha256"]
LONG_COLUMNS = ["prediction_receipt_id", "game_id", "player_id", "team_id", "stat", "prediction_as_of", "model_version",
                "outcome", "probability", "mean", "run_id", "method", "game_label", "player_label"]


class PMFCompactError(RuntimeError):
    pass


def content_sha(support_min: int, probabilities: np.ndarray) -> str:
    p = np.ascontiguousarray(probabilities, dtype=np.float64)
    h = hashlib.sha256()
    h.update(np.array([support_min, support_min + len(p) - 1], dtype=np.int64).tobytes())
    h.update(p.tobytes())
    return h.hexdigest()


def compact(long: pd.DataFrame) -> pd.DataFrame:
    """Long PMF evidence -> compact rows (first-appearance receipt order, as written). Refuses a PMF whose per-PMF fields
    vary across its rows or whose outcomes are not a contiguous ascending support."""
    if not len(long):
        return pd.DataFrame(columns=PMF_COLUMNS)
    rid = long["prediction_receipt_id"].to_numpy()
    starts = np.flatnonzero(np.r_[True, rid[1:] != rid[:-1]])
    if len(starts) != len(pd.unique(rid)):
        raise PMFCompactError("a PMF's rows are not contiguous in the long frame")
    ends = np.r_[starts[1:], len(long)]
    out = long[PER_PMF].iloc[starts].reset_index(drop=True)
    outc = long["outcome"].to_numpy()
    prob = long["probability"].to_numpy(dtype=np.float64)
    lo, hi, vecs, shas = [], [], [], []
    for a, b in zip(starts, ends):
        o = outc[a:b]
        if not (np.diff(o) == 1).all():
            raise PMFCompactError(f"{rid[a]}: outcomes are not a contiguous ascending support")
        p = prob[a:b].copy()
        lo.append(int(o[0])); hi.append(int(o[-1])); vecs.append(p); shas.append(content_sha(int(o[0]), p))
    for c in PER_PMF:                                          # per-PMF fields are constant within each PMF
        v = long[c].to_numpy()
        same = (v[1:] == v[:-1]) | (pd.isna(v[1:]) & pd.isna(v[:-1]))
        bad = ~same & np.isin(np.arange(1, len(v)), starts, invert=True)
        if bad.any():
            raise PMFCompactError(f"column {c} varies within one PMF")
    out["support_min"] = np.array(lo, dtype=np.int64)
    out["support_max"] = np.array(hi, dtype=np.int64)
    out["probabilities"] = vecs
    out["pmf_sha256"] = shas
    return out[PMF_COLUMNS]


def expand(cmp: pd.DataFrame) -> pd.DataFrame:
    """Compact rows -> the exact long frame (LONG_COLUMNS, outcome int64, probability float64)."""
    if not len(cmp):
        return pd.DataFrame(columns=LONG_COLUMNS)
    vecs = [np.asarray(v, dtype=np.float64) for v in cmp["probabilities"]]
    n = np.array([len(v) for v in vecs], dtype=np.int64)
    lo = cmp["support_min"].to_numpy(dtype=np.int64)
    if ((cmp["support_max"].to_numpy(dtype=np.int64) - lo + 1) != n).any():
        raise PMFCompactError("support_min / support_max disagree with the vector length")
    rep = np.repeat(np.arange(len(cmp)), n)
    out = {c: cmp[c].to_numpy()[rep] for c in PER_PMF}
    off = np.arange(n.sum(), dtype=np.int64) - np.repeat(np.cumsum(n) - n, n)
    out["outcome"] = np.repeat(lo, n) + off
    out["probability"] = np.concatenate(vecs) if vecs else np.array([], dtype=np.float64)
    return pd.DataFrame(out)[LONG_COLUMNS]


def verify(cmp: pd.DataFrame) -> None:
    """Every stored vector matches its content hash."""
    for r in cmp[["prediction_receipt_id", "support_min", "probabilities", "pmf_sha256"]].itertuples(index=False):
        if content_sha(int(r.support_min), np.asarray(r.probabilities, dtype=np.float64)) != r.pmf_sha256:
            raise PMFCompactError(f"{r.prediction_receipt_id}: PMF content hash mismatch")
