"""Player correction PS2: state-calibrated minutes draw (docs/audits/2026-11-26-rootcause/ROOT_CAUSE_PROTOCOL.json PS1 /
PS2). Promoted 2026-11-26.

Root cause (PS1, no fit): the bank's minutes draw clip(min_mu + min_sigma eps, 0, KMAX), eps ~ N(0, 1), read as a state
mixture OUT / LIMITED <10 / BENCH 10-20 / ROTATION 20-28 / STARTER 28+, under-weights the LIMITED and STARTER states given
playing (live: LIMITED .196 vs .222 observed, STARTER .186 vs .242, BENCH .346 vs .285).

Fix: the same 'minutes' eps stream, monotonically re-mapped per min_mu band: eps' = Q_b(Phi(eps)), Q_b = empirical
quantile function (99 knots, linear; unit slope beyond the outer knots) of the standardized realized minutes residuals
z = (y_min - min_mu) / min_sigma of played rows, TRAIN 2024-25 only (minutes_state_qmap.json, sealed). Common random
numbers, the copula, the conditional stat model and P(play) are untouched; only the state masses move.
Evaluation (common random numbers vs the PI2 engine): stacked log loss 2025-26 -0.00090 [-0.00097, -0.00081], live -0.00076
[-0.00100, -0.00054], every stat improves in both periods; live graded prop events log loss 0.7034 vs 0.7047.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from functools import lru_cache
from math import erf, sqrt
from pathlib import Path

import numpy as np

from cbb_platform.v2 import joint_minutes_v2 as JM

ID = "CBB_PLAYER_CORRECTION_PS2_STATE_CALIBRATED_MINUTES_V1"
QMAP = Path(__file__).resolve().parent / "minutes_state_qmap.json"
QMAP_SHA256 = "304ecff46ae597747ff2bf992b414609bb747ed1b9c24d51835aea143db721f5"
Z_KNOT = 2.3263478740408408          # Phi^-1(0.99) = -Phi^-1(0.01)
ENABLED = True
_FROZEN_RAW_MINUTES = JM.raw_minutes
_phi = np.vectorize(lambda z: 0.5 * (1.0 + erf(z / sqrt(2.0))))


class QmapIntegrityError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(ENABLED)


@lru_cache(maxsize=1)
def qmap() -> dict:
    raw = QMAP.read_bytes()
    if hashlib.sha256(raw).hexdigest() != QMAP_SHA256:
        raise QmapIntegrityError(f"{QMAP.name}: sha256 mismatch")
    m = json.loads(raw)
    return {"u": np.asarray(m["u"], float), "edges": np.asarray(m["edges"], float),
            "q": [np.asarray(b["q"], float) for b in m["bands"]]}


def map_eps(min_mu: np.ndarray, z: np.ndarray) -> np.ndarray:
    """eps' per (draw, player) column; the band of each column from its min_mu."""
    m = qmap()
    b = np.clip(np.searchsorted(m["edges"], np.asarray(min_mu, float), side="right") - 1, 0, len(m["q"]) - 1)
    u = _phi(z)
    out = np.empty_like(z, dtype=float)
    for k, q in enumerate(m["q"]):
        c = b == k
        if not c.any():
            continue
        zz = z[:, c]
        v = np.interp(u[:, c], m["u"], q)
        v = np.where(zz < -Z_KNOT, q[0] + (zz + Z_KNOT), v)
        v = np.where(zz > Z_KNOT, q[-1] + (zz - Z_KNOT), v)
        out[:, c] = v
    return out


def raw_minutes(min_mu, min_sigma, z, dur, e_dur) -> np.ndarray:
    """joint_minutes_v2.raw_minutes with the state-calibrated eps (the frozen function does the rest)."""
    return _FROZEN_RAW_MINUTES(min_mu, min_sigma, map_eps(min_mu, np.asarray(z, float)), dur, e_dur)


@contextmanager
def calibrated_minutes():
    """Install the PS2 draw for one scoring call (identity when disabled); always restores the frozen function."""
    if not enabled():
        yield
        return
    JM.raw_minutes = raw_minutes
    try:
        yield
    finally:
        JM.raw_minutes = _FROZEN_RAW_MINUTES
