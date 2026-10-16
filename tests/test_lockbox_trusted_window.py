"""Lockbox-scoped trusted-window extension (incident docs/lockbox/LOCKBOX_INCIDENT_2025_26_TRUSTED_WINDOW.md, architect
ruling 2026-10-16). Closed: the audited window is exactly 2005-2025 and the rendered SQL is the frozen text. Opened
(valid decision record + LOCKBOX_OPEN_2025_26=1): 2025-26 joins through the same generic SQL. 2026-27, BDL and unplanned
endpoints stay refused. No predictive file is touched."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from cbb_platform import config as C, game_identity_audit as GA, lockbox

PROBE = """
import json, os, sys
os.environ.pop("LOCKBOX_OPEN_2025_26", None)
if sys.argv[1] == "open":
    os.environ["LOCKBOX_OPEN_2025_26"] = "1"
from cbb_platform import config as C, game_identity_audit as GA, lockbox as L
def ok(f):
    try: f(); return True
    except L.LockboxViolation: return False
print(json.dumps({"window": list(C.AUDITED_SEASONS), "authorized": L.opening_authorized(),
                  "schema_frozen_text": C.schema_sql() == C.SCHEMA_SQL.read_text(),
                  "schema_2026": C.schema_sql().count("BETWEEN 2005 AND 2026"),
                  "audit_2026": "BETWEEN 2005 AND 2026" in GA.CANDIDATES_SQL,
                  "season_2026": ok(lambda: L.check_season(2026)),
                  "cbbd_2026": ok(lambda: L.check_request("cbbd", "games", {"season": 2026})),
                  "cbbd_2027": ok(lambda: L.check_request("cbbd", "games", {"season": 2027})),
                  "response_2027": ok(lambda: L.check_response("cbbd", {"season": 2027}, "games")),
                  "bdl": ok(lambda: L.check_request("bdl", "games", {"season": 2025})),
                  "unplanned": ok(lambda: L.check_request("cbbd", "plays/date", {"date": "2026-01-10"}))}))
"""


def _probe(mode: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "LOCKBOX_OPEN_2025_26"}
    p = subprocess.run([sys.executable, "-c", PROBE, mode], cwd=C.PROJECT_ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_closed_state_window_is_exactly_2005_2025_and_text_is_frozen():          # A
    closed = _probe("closed")
    assert closed["window"] == [2005, 2025] and not closed["authorized"]
    assert closed["schema_frozen_text"] and closed["schema_2026"] == 0 and not closed["audit_2026"]
    assert not closed["season_2026"] and not closed["cbbd_2026"]
    if os.environ.get("LOCKBOX_OPEN_2025_26") != "1":
        assert C.AUDITED_SEASONS == (2005, 2025) and C.schema_sql() == C.SCHEMA_SQL.read_text()
        assert "BETWEEN 2005 AND 2025" in GA.CANDIDATES_SQL


def test_open_state_admits_2026_through_the_same_generic_sql():                    # B
    if lockbox.OPEN_DECISION_SHA256 is None:
        pytest.skip("not the opening commit (guard closed)")
    opened = _probe("open")
    assert opened["authorized"] and opened["window"] == [2005, 2026]
    assert opened["schema_2026"] == 2 and opened["audit_2026"] and opened["season_2026"] and opened["cbbd_2026"]
    closed_text = C.SCHEMA_SQL.read_text()
    from cbb_platform.config import _FROZEN_WINDOW_SQL
    assert closed_text.replace(_FROZEN_WINDOW_SQL, "BETWEEN 2005 AND 2026").count("2026") >= 2   # only the window bound differs


@pytest.mark.parametrize("mode", ["closed", "open"])
def test_2027_bdl_and_unplanned_endpoints_stay_refused(mode):                      # C, D, E
    r = _probe(mode)
    assert not r["cbbd_2027"] and not r["bdl"] and not r["unplanned"]     # 2027 is refused at the request in both states
    if r["authorized"]:                    # the opened guard also refuses any 2027 value inside a response (guard v3)
        assert not r["response_2027"]


def test_trusted_views_keep_2005_2025_content_on_the_pre_remediation_build():      # F (views; tables after the rebuild)
    db = C.PROJECT_ROOT / "data" / "lockbox_2025_26" / "canonical_pre_remediation" / "cbb.duckdb"
    if not db.exists():
        pytest.skip("pre-remediation lockbox build not present")
    con = duckdb.connect()
    con.execute(f"ATTACH '{db}' AS cbb (READ_ONLY)")
    con.execute("USE cbb")
    sql = lambda hi: {v: con.execute(f"""SELECT sql FROM duckdb_views() WHERE database_name = 'cbb'
        AND schema_name || '.' || view_name = '{v}'""").fetchone()[0].split(" AS ", 1)[1].rstrip().rstrip(";").replace("2005 AND 2025", f"2005 AND {hi}")
        for v in ("flagship.games", "metadata.trusted_game_map")}
    for v, body in sql(2026).items():
        old = f"SELECT * FROM {v} WHERE " + ("season <= 2025" if v == "flagship.games" else "TRUE")
        new = f"SELECT * FROM ({body}) WHERE " + ("season <= 2025" if v == "flagship.games" else
              "source_canonical_game_id IN (SELECT canonical_game_id FROM core.games WHERE season <= 2025)")
        assert con.execute(f"SELECT count(*) FROM (({old}) EXCEPT ALL ({new}))").fetchone()[0] == 0, v
        assert con.execute(f"SELECT count(*) FROM (({new}) EXCEPT ALL ({old}))").fetchone()[0] == 0, v
    con.close()


def test_remediation_touches_no_predictive_file():                                 # G
    predictive = ["cbb_platform/props_v0.py", "cbb_platform/props_v0_eval.py", "cbb_platform/props_v0_participation.py",
                  "cbb_platform/props_v1_pricing.py", "cbb_platform/props_v1_refit.py", "cbb_platform/stage3",
                  "cbb_platform/lockbox_runner.py", "modeling/player_props_v0", "modeling/player_props_v1",
                  "modeling/eligibility", "docs/lockbox/lockbox_scorecard_v2.json"]
    opening = "7dbc0b569271a42c8079946e3e27e76be86703c2"
    assert subprocess.run(["git", "diff", "--quiet", opening, "--", *predictive], cwd=C.PROJECT_ROOT).returncode == 0
    assert subprocess.run(["git", "cat-file", "-e", opening], cwd=C.PROJECT_ROOT).returncode == 0
