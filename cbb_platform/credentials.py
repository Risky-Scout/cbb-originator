"""Credentials: read from the environment only, cleaned in memory, never printed or persisted.

A value pasted into a terminal can carry terminal control sequences (measured 2026-10-05: five
`ESC[B` cursor keys embedded in CBBD_API_KEY, which made CBBD answer HTTP 400). `get` strips ONLY
such control sequences and control characters. It never substitutes another variable or source,
and its error messages name the variable, never its value.
"""
from __future__ import annotations

import os
import re

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z~]|\x1b.|[\x00-\x1f\x7f]")
REGISTERED = ("CBBD_API_KEY", "BDL_API_KEY")


def get(name: str) -> str:
    raw = os.environ.get(name)
    if not raw:
        raise RuntimeError(f"BLOCKED_CREDENTIAL: {name} is not set")
    clean = _CONTROL.sub("", raw).strip()
    if not clean or not re.fullmatch(r"[\x21-\x7e]+", clean):
        raise RuntimeError(f"BLOCKED_CREDENTIAL: {name} is malformed")
    return clean


def present_values() -> list[str]:
    """Raw and cleaned values of every registered credential (for leak scans in tests only)."""
    out = []
    for name in REGISTERED:
        raw = os.environ.get(name)
        if raw and len(raw) >= 8:
            out += [raw, _CONTROL.sub("", raw).strip()]
    return list(dict.fromkeys(out))
