"""Droplet deployment package (deploy/droplet; docs/operations/CBB_DROPLET_DEPLOYMENT_PACKAGE.md): safe defaults,
refusals, single-writer role, exact-commit identity, no secrets, and the transfer manifest's hashes. Nothing here
touches a server. The wrapper runs against a throwaway git repository with a fake /proc/meminfo."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cbb_platform import config as C
from cbb_platform.prod import config as PC

D = C.PROJECT_ROOT / "deploy" / "droplet"
RUN = D / "bin" / "cbb-originator-run"
UNITS = sorted((D / "systemd").glob("*.service")) + sorted((D / "systemd").glob("*.timer"))
SECRET_NAMES = ("CBBD_API_KEY", "ODDS_API_KEY", "CBB_OPS_ENCRYPTION_KEY", "GH_TOKEN", "ROTOWIRE_API_KEY")
THREADS = {k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                            "NUMEXPR_NUM_THREADS")}
needs_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="flock(1) not installed (util-linux; present on the Droplet and CI)")


def _env_example() -> dict:
    out = {}
    for line in (D / "env" / "cbb-originator.env.example").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


@pytest.fixture
def app(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)  # noqa: E731
    g("init", "-q")
    (repo / "README").write_text("x\n")
    g("add", "README")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    sha = g("rev-parse", "HEAD").stdout.strip()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       33554432 kB\nMemAvailable:   20971520 kB\n")     # 20 GB available
    lock = tmp_path / "lock"
    lock.mkdir()
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CBB_APP_DIR": str(repo), "CBB_EXPECTED_COMMIT": sha,
            "CBB_MEMINFO": str(meminfo), "CBB_LOCK_DIR": str(lock), "CBB_VENV_DIR": str(tmp_path / "venv"), **THREADS}
    return {"repo": repo, "sha": sha, "env": base, "meminfo": meminfo, "tmp": tmp_path}


def _run(app, cmd, **extra):
    env = {**app["env"], **{k: v for k, v in extra.items() if v is not None}}
    for k in [k for k, v in extra.items() if v is None]:
        env.pop(k, None)
    return subprocess.run(["bash", str(RUN), cmd], env=env, capture_output=True, text=True, timeout=60)


# ------------------------------------------------------------------ safe defaults
def test_env_template_defaults_are_safe_and_hold_no_secret_values():
    e = _env_example()
    assert e["CBB_MODEL_SOURCE"] == "FROZEN_OPENING_BASELINE"
    assert e["ROTOWIRE_ENABLED"] == "false" and e["ROTOWIRE_RIGHTS_STATUS"] == "UNVERIFIED"
    assert e["WIZARDOFODDS_PUBLICATION"] == "disabled" and e["AUTOMATIC_WAGERING"] == "disabled"
    assert e["REAL_MONEY_ACTIONABILITY"] == "disabled"
    assert e["CBB_SCHEDULER_ROLE"] == "DISABLED" and e["CBB_EXPECTED_COMMIT"] == ""
    assert e["CBB_MIN_MEMAVAILABLE_GB"] == "13"
    assert all(e[k] == "" for k in SECRET_NAMES)


def test_code_level_safety_constants_still_hold():
    from cbb_platform.prod import model_source as MS, production_board as PB
    assert PC.WAGERING_ENABLED is False and PB.AUTOMATIC_ACTIONABLE_WAGER == "DISABLED"
    assert PC.AVAILABILITY_G4B_STATUS == "ACCUMULATING"
    assert PC.REAL_MONEY_ACTIONABILITY == "DISABLED_BY_AVAILABILITY_VALIDATION"
    assert MS.DEFAULT_SOURCE == "FROZEN_OPENING_BASELINE"


def test_preflight_passes_on_defaults(app):
    r = _run(app, "preflight")
    assert r.returncode == 0, r.stderr
    assert "PREFLIGHT OK" in r.stderr


@pytest.mark.parametrize("extra", [
    {"ROTOWIRE_ENABLED": "true"},
    {"ROTOWIRE_ENABLED": "true", "ROTOWIRE_RIGHTS_STATUS": "PRODUCTION_APPROVED"},     # not wired: still refused
    {"ROTOWIRE_ENABLED": "yes"},
    {"WIZARDOFODDS_PUBLICATION": "enabled"},
    {"AUTOMATIC_WAGERING": "enabled"},
    {"REAL_MONEY_ACTIONABILITY": "enabled"},
    {"CBB_MODEL_SOURCE": "ACTIVE_DAILY"},
    {"OMP_NUM_THREADS": "4"},
    {"MKL_NUM_THREADS": None},
    {"CBB_EXPECTED_COMMIT": "0" * 40},
    {"CBB_EXPECTED_COMMIT": None},
])
def test_unsafe_or_unverified_configuration_is_refused(app, extra):
    for cmd in ("preflight", "morning", "tick"):
        r = _run(app, cmd, CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1", **extra)
        assert r.returncode == 3, (cmd, extra, r.stderr)
        assert "REFUSED" in r.stderr


def test_modified_checkout_is_refused(app):
    (app["repo"] / "README").write_text("changed\n")
    assert _run(app, "preflight").returncode == 3


def test_no_writer_role_means_nothing_runs(app):
    for cmd in ("morning", "tick"):
        r = _run(app, cmd)                                  # CBB_SCHEDULER_ROLE unset -> DISABLED
        assert r.returncode == 4 and "not the designated" in r.stderr
        assert _run(app, cmd, CBB_SCHEDULER_ROLE="SECONDARY").returncode == 4


def test_dry_run_shows_lock_order_and_commands(app):
    m = _run(app, "morning", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1")
    assert m.returncode == 0, m.stderr
    s = m.stderr
    assert s.index("sports-model-heavy.lock") < s.index("MemAvailable") < s.index("cbb-originator.writer.lock")
    assert "cbb_platform.walkforward.daily morning" in s
    t = _run(app, "tick", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1")
    assert t.returncode == 0, t.stderr
    assert "cbb_platform.prod.shadow_tick" in t.stderr and "--restore" not in t.stderr
    assert "sports-model-heavy.lock" not in t.stderr      # light job: never takes the shared heavy lock


# ------------------------------------------------------------------ lock + memory + exit-code mapping (needs flock)
def _fake_python(app, status: str):
    py = app["tmp"] / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/usr/bin/env bash\nif [ \"$1\" = \"-m\" ]; then echo 'log line'; "
                  f"printf '{{\\n \"DAILY_REFIT_STATUS\": \"{status}\"\\n}}\\n'; exit 0; fi\nexec python3 \"$@\"\n")
    py.chmod(0o755)


@needs_flock
def test_memory_gate_fails_closed_with_tempfail(app):
    app["meminfo"].write_text("MemTotal:  8388608 kB\nMemAvailable:  6291456 kB\n")      # 6 GB < 13 GB
    _fake_python(app, "PASS")
    r = _run(app, "morning", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_MEM_WAIT_SEC="0")
    assert r.returncode == 75 and "FAIL (RESOURCE)" in r.stderr


@needs_flock
def test_heavy_lock_busy_fails_closed_with_tempfail(app):
    _fake_python(app, "PASS")
    lockf = Path(app["env"]["CBB_LOCK_DIR"]) / "sports-model-heavy.lock"
    holder = subprocess.Popen(["flock", str(lockf), "sleep", "20"])
    try:
        import time
        time.sleep(0.5)
        r = _run(app, "morning", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_HEAVY_LOCK_WAIT_SEC="1")
        assert r.returncode == 75 and "heavy-job lock busy" in r.stderr
    finally:
        holder.kill()
        holder.wait()


@needs_flock
@pytest.mark.parametrize("status,rc", [("PASS", 0), ("NO_NEW_DATA", 0), ("FAIL", 1), ("NOT_RUN", 1)])
def test_refit_status_maps_to_exit_code(app, status, rc):
    _fake_python(app, status)
    r = _run(app, "morning", CBB_SCHEDULER_ROLE="PRIMARY_WRITER")
    assert r.returncode == rc, r.stderr
    assert f"DAILY_REFIT_STATUS = {status}" in r.stderr


# ------------------------------------------------------------------ systemd templates
def test_units_are_isolated_and_hold_no_secrets():
    assert {u.name for u in UNITS} == {"cbb-originator-morning.service", "cbb-originator-morning.timer",
                                       "cbb-originator-tick.service", "cbb-originator-tick.timer"}
    for u in UNITS:
        s = u.read_text()
        for name in SECRET_NAMES:
            assert not re.search(rf"^\s*Environment=.*\b{name}=", s, re.M), (u.name, name)
        assert not re.search(r"(?i)massey|nba|nfl|ncaaf|wizard_deploy|/home/|/Users/", s.replace("WizardOfOdds", "")), u.name
    for u in (D / "systemd").glob("*.service"):
        s = u.read_text()
        for line in ("Type=oneshot", "User=cbb-originator", "Group=cbb-originator", "Restart=no",
                     "EnvironmentFile=/etc/sports-models/cbb-originator.env", "WorkingDirectory=/opt/sports-models/cbb-originator/app",
                     "ProtectSystem=strict", "NoNewPrivileges=yes", "StandardOutput=journal", "MemorySwapMax=0",
                     "ReadWritePaths=/var/lib/sports-models/cbb-originator /run/lock/sports-models"):
            assert line in s.splitlines(), (u.name, line)
        assert "ExecStart=/opt/sports-models/cbb-originator/app/deploy/droplet/bin/cbb-originator-run " in s
        for k in THREADS:
            assert f"{k}=1" in s
        directives = [ln for ln in s.splitlines() if ln.strip() and not ln.startswith("#")]
        assert not [ln for ln in directives if re.search(r"(?i)--restore|publish|wager|deploy_dryrun", ln)], u.name
    m = (D / "systemd" / "cbb-originator-morning.service").read_text()
    assert "MemoryHigh=12G" in m and "MemoryMax=14G" in m


def test_timers_are_dst_correct_and_match_the_approved_cadence():
    m = (D / "systemd" / "cbb-originator-morning.timer").read_text()
    assert "OnCalendar=*-*-* 06:05:00 America/New_York" in m and "Persistent=false" in m
    t = [ln for ln in (D / "systemd" / "cbb-originator-tick.timer").read_text().splitlines() if ln.startswith("OnCalendar=")]
    assert t == ["OnCalendar=*-11,12,01,02,03-* 00..04,12..23:12,42:00 UTC", "OnCalendar=*-04-01..08 00..04,12..23:12,42:00 UTC",
                 "OnCalendar=*-11,12,01,02,03-* 06,09:34:00 UTC", "OnCalendar=*-04..10-* 00,06,12,18:34:00 UTC"]
    wf = (C.PROJECT_ROOT / ".github" / "workflows" / "cbb-shadow-tick.yml")
    if wf.exists():                                         # the scheduled copy lives on main
        for cron in ("12,42 12-23,0-4 * 11,12,1,2,3 *", "12,42 12-23,0-4 1-8 4 *", "34 6,9 * 11,12,1,2,3 *", "34 */6 * 4-10 *"):
            assert cron in wf.read_text()


# ------------------------------------------------------------------ transfer manifest
def test_transfer_manifest_hashes_match_the_frozen_pins():
    man = json.loads((D / "STATE_TRANSFER_MANIFEST.json").read_text())
    by = {e["name"]: e for e in man["entries"]}
    for k, (path, sha) in PC.ARTIFACTS.items():
        e = by[f"frozen_jm1_{k}"]
        assert e["sha256"] == sha and e["destination"].endswith("/" + Path(path).name)
        assert e["bytes"] == (C.PROJECT_ROOT / e["destination"].split("/app/", 1)[1]).stat().st_size
    dv = json.loads((PC.MODEL_DIR / "jm1_final_production_manifest_2026_27.json").read_text())["data_versions"]
    for name, key in (("stage3_features", "features_duckdb"), ("stage3_labels", "labels_duckdb"),
                      ("oos_phase1b", "phase1b_oos_store"), ("oos_phase1b_F2023", "phase1b_F2023_extension")):
        assert by[name]["sha256"] == dv[key]["sha256"] and by[name]["bytes"] == dv[key]["bytes"]
        assert by[name]["required"].startswith("REQUIRED") and "NOT COVERED" in by[name]["licence_scope"]
    import hashlib
    g = C.PROJECT_ROOT / "tests" / "fixtures" / "golden" / "prod_equivalence_v1.json"
    assert by["golden_fixture"]["sha256"] == hashlib.sha256(g.read_bytes()).hexdigest()
    assert by["history_canonical_lockbox"]["bytes"] == 9455546368 and by["history_canonical_lockbox"]["required"].startswith("REQUIRED")
    assert by["rotowire_event_store"]["required"].startswith("NOT_REQUIRED")
    assert "data/prod/state" in by["ops_working_set"]["notes"] and "release" in by["ops_working_set"]["source"]
    assert not re.search(r"(?i)(api[_-]?key|token|password)\s*[:=]\s*\S{8,}", json.dumps(man))
