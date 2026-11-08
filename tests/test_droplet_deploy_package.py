"""Droplet deployment package (deploy/droplet; docs/operations/CBB_DROPLET_DEPLOYMENT_PACKAGE.md): safe defaults,
refusals, single-writer role, exact-release identity (git-archive release + sha256 manifest), mutable paths outside the
release, no secrets, and the transfer manifest's hashes. Nothing here touches a server."""
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


def _release(tmp_path: Path, sha: str = "a" * 40) -> dict:
    """A git-archive style release: releases/<sha>/ + releases/<sha>.sha256sums, current -> releases/<sha>."""
    rel = tmp_path / "releases" / sha
    (rel / "pkg").mkdir(parents=True)
    (rel / "README").write_text("x\n")
    (rel / "pkg" / "m.py").write_text("y = 1\n")
    lines = []
    import hashlib
    for p in sorted(q for q in rel.rglob("*") if q.is_file()):
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(rel)}")
    (tmp_path / "releases" / f"{sha}.sha256sums").write_text("\n".join(lines) + "\n")
    cur = tmp_path / "current"
    cur.symlink_to(rel)
    return {"rel": rel, "current": cur, "sha": sha}


@pytest.fixture
def app(tmp_path):
    r = _release(tmp_path)
    state = tmp_path / "state"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       8131776 kB\nMemAvailable:   7340032 kB\n")
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CBB_APP_ROOT": str(r["current"]),
            "CBB_EXPECTED_COMMIT": r["sha"], "CBB_MEMINFO": str(meminfo), "CBB_STATE_ROOT": str(state),
            "CBB_HEAVY_LOCK": str(tmp_path / "heavy.lock"), "CBB_VENV_DIR": str(tmp_path / "venv"), **THREADS}
    return {**r, "env": base, "meminfo": meminfo, "tmp": tmp_path, "state": state}


def _run(app, *cmd, **extra):
    env = {**app["env"], **{k: v for k, v in extra.items() if v is not None}}
    for k in [k for k, v in extra.items() if v is None]:
        env.pop(k, None)
    return subprocess.run(["bash", str(RUN), *cmd], env=env, capture_output=True, text=True, timeout=60)


# ------------------------------------------------------------------ safe defaults
def test_env_template_defaults_are_safe_and_hold_no_secret_values():
    e = _env_example()
    assert e["ROTOWIRE_ENABLED"] == "false"
    assert e["WIZARDOFODDS_PUBLICATION"] == "disabled" and e["AUTOMATIC_WAGERING"] == "disabled"
    assert e["REAL_MONEY_ACTIONABILITY"] == "disabled"
    assert e["CBB_SCHEDULER_ROLE"] == "DISABLED" and e["CBB_EXPECTED_COMMIT"] == ""
    assert e["CBB_HISTORY_SOURCE"] in ("CONTROL", "CANDIDATE")
    assert all(e[k] == "" for k in SECRET_NAMES)


def test_code_level_safety_constants_still_hold():
    from cbb_platform.prod import model_source as MS, production_board as PB
    assert PC.WAGERING_ENABLED is False and PB.AUTOMATIC_ACTIONABLE_WAGER == "DISABLED"
    assert PC.AVAILABILITY_G4B_STATUS == "ACCUMULATING"
    assert PC.REAL_MONEY_ACTIONABILITY == "DISABLED_BY_AVAILABILITY_VALIDATION"
    assert MS.DEFAULT_SOURCE == "FROZEN_OPENING_BASELINE"


def test_preflight_passes_on_an_intact_release(app):
    r = _run(app, "preflight")
    assert r.returncode == 0, r.stderr
    assert "PREFLIGHT OK" in r.stderr and app["sha"] in r.stderr


def test_modified_release_file_is_refused(app):
    (app["rel"] / "pkg" / "m.py").write_text("y = 2\n")
    r = _run(app, "preflight")
    assert r.returncode == 3 and "differs from its manifest" in r.stderr


def test_extra_file_in_release_is_refused(app):
    (app["rel"] / "pkg" / "planted.py").write_text("import os\n")
    r = _run(app, "preflight")
    assert r.returncode == 3 and "outside its manifest" in r.stderr


def test_missing_release_manifest_is_refused(app):
    (app["rel"].parent / f"{app['sha']}.sha256sums").unlink()
    assert _run(app, "preflight").returncode == 3


def test_prod_root_inside_the_release_is_refused(app):
    r = _run(app, "preflight", CBB_PROD_ROOT=str(app["rel"] / "data" / "prod"))
    assert r.returncode == 3 and "inside the application tree" in r.stderr


def test_git_checkout_mode_still_verifies_identity(tmp_path, app):
    repo = tmp_path / "gitapp"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)  # noqa: E731
    g("init", "-q")
    (repo / "README").write_text("x\n")
    g("add", "README")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    sha = g("rev-parse", "HEAD").stdout.strip()
    assert _run(app, "preflight", CBB_APP_ROOT=str(repo), CBB_EXPECTED_COMMIT=sha).returncode == 0
    (repo / "README").write_text("changed\n")
    assert _run(app, "preflight", CBB_APP_ROOT=str(repo), CBB_EXPECTED_COMMIT=sha).returncode == 3


@pytest.mark.parametrize("extra", [
    {"ROTOWIRE_ENABLED": "true"},
    {"ROTOWIRE_ENABLED": "yes"},
    {"WIZARDOFODDS_PUBLICATION": "enabled"},
    {"AUTOMATIC_WAGERING": "enabled"},
    {"REAL_MONEY_ACTIONABILITY": "enabled"},
    {"CBB_MODEL_SOURCE": "SOMETHING_ELSE"},
    {"OMP_NUM_THREADS": "4"},
    {"MKL_NUM_THREADS": None},
    {"CBB_DUCKDB_THREADS": "2"},
    {"CBB_EXPECTED_COMMIT": "0" * 40},
    {"CBB_EXPECTED_COMMIT": None},
])
def test_unsafe_or_unverified_configuration_is_refused(app, extra):
    for cmd in (["preflight"], ["lifecycle-morning"], ["refit-retry"], ["tick"], ["lifecycle-refresh", "10:00"]):
        r = _run(app, *cmd, CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1", **extra)
        assert r.returncode == 3, (cmd, extra, r.stderr)
        assert "REFUSED" in r.stderr


def test_no_writer_role_means_nothing_runs(app):
    for cmd in (["lifecycle-morning"], ["refit-retry"], ["tick"], ["lifecycle-refresh", "13:00"]):
        r = _run(app, *cmd)                                  # CBB_SCHEDULER_ROLE unset -> DISABLED
        assert r.returncode == 4 and "not the designated" in r.stderr
        assert _run(app, *cmd, CBB_SCHEDULER_ROLE="SECONDARY").returncode == 4


def test_dry_run_paths_are_outside_the_release(app):
    m = _run(app, "lifecycle-morning", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1")
    assert m.returncode == 0, m.stderr
    s = m.stderr
    assert "cbb_platform.prod.lifecycle" in s and " morning" in s
    assert f"--prod-root {app['state']}/prod" in s and f"--ops-root {app['state']}/prod/ops" in s
    assert f"{app['state']}/locks/cbb-originator.writer.lock" in s
    assert str(app["rel"]) not in s.split("run:", 1)[1].replace(f"{app['tmp']}/venv", "")
    t = _run(app, "tick", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1")
    assert t.returncode == 0 and "cbb_platform.prod.shadow_tick" in t.stderr and "--restore" not in t.stderr


@needs_flock
def test_writer_lock_busy_fails_closed_with_tempfail(app):
    lockd = app["state"] / "locks"
    lockd.mkdir(parents=True)
    holder = subprocess.Popen(["flock", str(lockd / "cbb-originator.writer.lock"), "sleep", "20"])
    try:
        import time
        time.sleep(0.5)
        r = _run(app, "refit-retry", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_WRITER_LOCK_WAIT_SEC="1")
        assert r.returncode == 75 and "writer lock busy" in r.stderr
    finally:
        holder.kill()
        holder.wait()


def test_prod_root_env_relocates_every_mutable_production_path(tmp_path):
    """No mutable production path may stay under the immutable release (CBB_PROD_ROOT / CBB_WF_ROOT / CBB_EXPORT_ROOT)."""
    code = ("import json; from cbb_platform.prod import config as PC, exports as EX, history_extract as HX, lifecycle as L;"
            "from cbb_platform.walkforward import config as WC; from cbb_platform import ops_persistence as OP;"
            "p={k: str(v) for k, v in vars(PC).items() if k.isupper() and hasattr(v, 'is_absolute') and 'ROOT' in k or k in"
            "('LEDGER_PATH','SYNC_LOG','CBBD_REQUEST_LEDGER','ODDS_REQUEST_LEDGER','ROTOWIRE_REQUEST_LEDGER','ODDS_VALIDATION_RECORD',"
            "'ODDS_COLLECTIONS','CBBD_CACHE_RAW','ODDS_CACHE_RAW','ROTOWIRE_CACHE_RAW','ROTOWIRE_MAP_ROOT')};"
            "p.update(EXPORT=str(EX.EXPORT_ROOT), RUNS=str(EX.RUNS_ROOT), HX_OUT=str(HX.DEFAULT_OUT), WF=str(WC.STORE_ROOT),"
            "LIFE_EXPORTS=str(L.Life().exports), LIFE_OPS=str(L.Life().ops_root), LIFE_STATE=str(L.Life().state_root));"
            "print(json.dumps(p))")
    env = {**os.environ, "CBB_PROD_ROOT": str(tmp_path / "prod"), "CBB_WF_ROOT": str(tmp_path / "wf"),
           "CBB_EXPORT_ROOT": str(tmp_path / "exports"), "PYTHONPATH": str(C.PROJECT_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([os.environ.get("PYTHON", __import__("sys").executable), "-c", code], env=env,
                         capture_output=True, text=True, check=True, cwd=str(tmp_path)).stdout
    paths = json.loads(out)
    assert len(paths) > 15
    for k, v in paths.items():
        assert Path(v).is_relative_to(tmp_path), (k, v)
        assert not Path(v).is_relative_to(C.PROJECT_ROOT), (k, v)
    assert paths["EXPORT"] == str(tmp_path / "exports") and paths["LIFE_EXPORTS"] == str(tmp_path / "exports")
    assert paths["WF"] == str(tmp_path / "wf" / "cbb-originator")


# ------------------------------------------------------------------ systemd units
HEAVY_UNITS = ("cbb-originator-morning.service", "cbb-originator-refit-retry.service")
LIGHT_UNITS = ("cbb-originator-availability@.service", "cbb-originator-quotes.service")


def _mem(s: str, key: str) -> int:
    v = re.search(rf"^{key}=(\d+)M$", s, re.M).group(1)
    return int(v)


def test_units_are_isolated_and_hold_no_secrets():
    assert {u.name for u in UNITS} == {*HEAVY_UNITS, *LIGHT_UNITS, "cbb-originator-morning.timer",
                                       "cbb-originator-refit-retry.timer", "cbb-originator-quotes.timer",
                                       *(f"cbb-originator-availability-{t}.timer" for t in ("1000", "1300", "1600", "1800"))}
    for u in UNITS:
        s = u.read_text()
        assert u.name.startswith("cbb-originator-")
        for name in SECRET_NAMES:
            assert not re.search(rf"^\s*Environment=.*\b{name}=", s, re.M), (u.name, name)
        assert not re.search(r"(?i)massey|nfl|ncaaf|wizard_deploy|/home/|/Users/", s), u.name
    for u in (D / "systemd").glob("*.service"):
        s = u.read_text()
        lines = s.splitlines()
        for line in ("Type=oneshot", "User=cbb-originator", "Group=cbb-originator", "Restart=no",
                     "SupplementaryGroups=sports-models-heavy",
                     "EnvironmentFile=/etc/sports-models/cbb-originator.env",
                     "WorkingDirectory=/opt/sports-models/cbb-originator/current",
                     "Environment=CBB_APP_ROOT=/opt/sports-models/cbb-originator/current",
                     "Environment=CBB_PROD_ROOT=/var/lib/sports-models/cbb-originator/prod",
                     "Environment=CBB_WF_ROOT=/var/lib/sports-models/cbb-originator/walkforward",
                     "Environment=CBB_EXPORT_ROOT=/var/lib/sports-models/cbb-originator/exports",
                     "Environment=CBB_HEAVY_LOCK=/run/lock/sports-models/heavy.lock",
                     "Environment=CBB_DUCKDB_THREADS=1",
                     "ProtectSystem=strict", "PrivateTmp=yes", "NoNewPrivileges=yes", "StandardOutput=journal",
                     "MemorySwapMax=0", "ReadOnlyPaths=/opt/sports-models/cbb-originator",
                     "ReadWritePaths=/var/lib/sports-models/cbb-originator /run/lock/sports-models"):
            assert line in lines, (u.name, line)
        assert "ExecStart=/opt/sports-models/cbb-originator/current/deploy/droplet/bin/cbb-originator-run " in s
        inacc = [ln for ln in lines if ln.startswith("InaccessiblePaths=")]
        assert inacc and all(p in inacc[0] for p in ("/opt/nba-prop-runtime", "/srv/nba-production"))
        active = [ln for ln in lines if ln.strip() and not ln.startswith("#") and not ln.startswith("InaccessiblePaths=")]
        assert not [ln for ln in active if re.search(r"(?i)nba|--restore|publish|wager", ln)], u.name
        for k in THREADS:
            assert f"{k}=1" in s
        assert _mem(s, "MemoryHigh") < _mem(s, "MemoryMax")
    for n in HEAVY_UNITS:
        s = (D / "systemd" / n).read_text()
        assert _mem(s, "MemoryHigh") == 2800 and _mem(s, "MemoryMax") == 3200        # 8 GB host: 4 GiB reserve kept
    for n in LIGHT_UNITS:
        assert _mem((D / "systemd" / n).read_text(), "MemoryMax") < 3200


def test_timers_are_dst_correct_and_match_the_approved_schedule():
    cal = {p.name: [ln for ln in p.read_text().splitlines() if ln.startswith("OnCalendar=")]
           for p in (D / "systemd").glob("*.timer")}
    assert cal == {"cbb-originator-morning.timer": ["OnCalendar=*-*-* 07:00:00 America/New_York"],
                   "cbb-originator-refit-retry.timer": ["OnCalendar=*-*-* 08:00:00 America/New_York"],
                   "cbb-originator-quotes.timer": ["OnCalendar=*-*-* *:00/10:00"],
                   **{f"cbb-originator-availability-{t.replace(':', '')}.timer": [f"OnCalendar=*-*-* {t}:00 America/New_York"]
                      for t in ("10:00", "13:00", "16:00", "18:00")}}
    for p in (D / "systemd").glob("*.timer"):
        assert "Persistent=false" in p.read_text()
    for t in ("1000", "1300", "1600", "1800"):
        hh = f"{t[:2]}:{t[2:]}"
        assert f"Unit=cbb-originator-availability@{hh}.service" in (D / "systemd" / f"cbb-originator-availability-{t}.timer").read_text()


def test_shared_heavy_lock_tmpfiles_entry():
    s = (D / "tmpfiles" / "sports-models-heavy.conf").read_text().splitlines()
    assert "d /run/lock/sports-models 0770 root sports-models-heavy -" in s
    assert "f /run/lock/sports-models/heavy.lock 0660 root sports-models-heavy -" in s


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


def _fake_venv(app, due: str) -> Path:
    """A stand-in interpreter that records each invocation (argv) and answers the cheap due check."""
    log = app["tmp"] / "calls.log"
    py = app["tmp"] / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(f"""#!/bin/bash
echo "$*" >> {log}
case "$1" in *due_check.py) echo "due={due}"; echo "reason=test" ;; esac
exit 0
""")
    py.chmod(0o755)
    return log


@needs_flock
@pytest.mark.parametrize("due", ["false", "true"])
def test_tick_runs_tiered_quote_refresh_every_wake(app, due):
    log = _fake_venv(app, due)
    r = _run(app, "tick", CBB_SCHEDULER_ROLE="PRIMARY_WRITER")
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert "due_check.py" in calls[0]
    shadow = [c for c in calls if "cbb_platform.prod.shadow_tick" in c]
    quotes = [c for c in calls if "cbb_platform.prod.lifecycle" in c and c.endswith(" quotes")]
    assert len(quotes) == 1 and len(shadow) == (1 if due == "true" else 0)
    assert calls[-1] == quotes[0]                                       # the refresh runs after the official tick


def test_duckdb_spill_dir_must_be_cbb_state(app):
    r = _run(app, "preflight", CBB_DUCKDB_TEMP_DIR="/opt/sports-models/cbb-originator/current/tmp")
    assert r.returncode == 3 and "CBB_DUCKDB_TEMP_DIR" in r.stderr
    d = _run(app, "tick", CBB_SCHEDULER_ROLE="PRIMARY_WRITER", CBB_DRY_RUN="1")
    assert "lifecycle quotes" in d.stderr and f"spill {app['state']}/staging/tmp/duckdb" in d.stderr
