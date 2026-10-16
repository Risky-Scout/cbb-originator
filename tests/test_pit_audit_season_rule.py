"""PIT audit season rule (cbb_platform/cbbd_box_route_pit_audit.season_rule): closed => no season 2026+; under the
valid LOCKBOX_OPEN 2025-26 authorization => 2026 authorized, 2027+ unauthorized. Generic predicate; every other PIT
check is untouched."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys

import pytest

from cbb_platform import config as C, cbbd_box_route_pit_audit as A, lockbox

PROBE = """
import json, os, sys
os.environ.pop("LOCKBOX_OPEN_2025_26", None)
if sys.argv[1] == "open":
    os.environ["LOCKBOX_OPEN_2025_26"] = "1"
from cbb_platform import cbbd_box_route_pit_audit as A
print(json.dumps(A.season_rule()))
"""


def _rule(mode):
    env = {k: v for k, v in os.environ.items() if k != "LOCKBOX_OPEN_2025_26"}
    return json.loads(subprocess.run([sys.executable, "-c", PROBE, mode], cwd=C.PROJECT_ROOT, env=env, capture_output=True,
                                     text=True, check=True).stdout.strip().splitlines()[-1])


def test_closed_state_refuses_2026_and_later():
    assert _rule("closed") == {"mode": "CLOSED", "unauthorized": "season >= 2026", "authorized": None}


def test_open_state_authorizes_only_2026():
    if lockbox.OPEN_DECISION_SHA256 is None:
        pytest.skip("guard closed on this commit")
    assert _rule("open") == {"mode": "LOCKBOX_OPEN_2025_26", "unauthorized": "season > 2026", "authorized": "season = 2026"}


def test_genuine_checks_unchanged():
    src = inspect.getsource(A.audit)
    for c in ("TARGET_LEAKAGE", "SAME_GAME_LEAKAGE", "FUTURE_DATA_LEAKAGE", "FUTURE_ROSTER_LEAKAGE", "LABEL_LEAKAGE", "LOOKAHEAD_BIAS"):
        assert f'"{c}"' in src
    assert "> prediction_cutoff_utc" in src and "= x.canonical_game_id" in src and ">= f.prediction_cutoff_utc" in src
