"""Three-model isolation guard (CBB Originator / NBA Prop Quant / Massey share one Droplet).

CBB production code, deployment files and CI must never reference another project's production namespace, and CBB
deployment tooling must never run host-wide commands that could touch another project. The only allowed mention of
foreign paths is the systemd `InaccessiblePaths=` deny-list in CBB units (it makes those paths unreachable).
Documentation (docs/) is out of scope: inventories and runbooks describe the other stacks deliberately."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPED = [ROOT / "cbb_platform", ROOT / "deploy", ROOT / ".github"]
SUFFIXES = {".py", ".sh", ".service", ".timer", ".target", ".conf", ".yml", ".yaml", ".toml", ".json", ".env", ".ini", ""}

FORBIDDEN = re.compile(
    r"/srv/nba-production|/opt/nba-prop-runtime|/etc/nba-prop-quant|/var/lib/nba-prop|nbaquant|"
    r"/root/Massey Predictive Models|massey-postgres|massey_pgdata|/srv/fanatics-demo|/root/secrets\.env|"
    r"\bnba-[a-z0-9-]+\.(service|timer)\b", re.I)
BROAD = re.compile(
    r"systemctl\s+(restart|stop|start|kill|disable)\s+['\"]?\*|"
    r"\bpkill\b(?![^\n]*cbb)|\bkillall\b|"
    r"rm\s+-[rf]+\s+['\"]?/(srv|opt|var|tmp|run|etc)/?['\"]?(\s|\*|$)|"
    r"\bfind\s+/(srv|opt|tmp|var|run)?/?\s[^\n]*-delete|"
    r"\b(usermod|gpasswd|adduser)\b[^\n]*(nba|massey)", re.I)


def _files():
    for base in SCOPED:
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in SUFFIXES and p.name != Path(__file__).name:
                yield p


def _lines(p):
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return [(i + 1, ln) for i, ln in enumerate(text.splitlines())]


def test_scope_is_nonempty():
    names = {p.name for p in _files()}
    assert "cbb-originator-run" in names and "cbb-originator-quotes.service" in names


def test_no_foreign_project_paths_outside_the_unit_deny_list():
    hits = []
    for p in _files():
        for n, ln in _lines(p):
            if FORBIDDEN.search(ln) and not ln.startswith("InaccessiblePaths="):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {ln.strip()[:120]}")
    assert not hits, "cross-project reference:\n" + "\n".join(hits)


def test_deploy_tooling_has_no_host_wide_commands():
    hits = []
    for p in _files():
        for n, ln in _lines(p):
            if ln.lstrip().startswith("#"):
                continue
            if BROAD.search(ln):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {ln.strip()[:120]}")
    assert not hits, "host-wide command:\n" + "\n".join(hits)


def test_every_unit_is_cbb_scoped():
    units = sorted((ROOT / "deploy/droplet/systemd").iterdir())
    assert units
    for u in units:
        assert u.name.startswith("cbb-originator-"), u.name
        body = u.read_text()
        if u.suffix == ".service":
            assert "User=cbb-originator" in body and "EnvironmentFile=/etc/sports-models/cbb-originator.env" in body, u.name
            for d in re.findall(r"^(?:ReadWritePaths)=(.*)$", body, re.M):
                for path in d.split():
                    assert path.lstrip("-").startswith(("/var/lib/sports-models/cbb-originator", "/run/lock/sports-models")), (u.name, path)


def test_guard_catches_a_planted_reference(tmp_path):
    assert FORBIDDEN.search("ExecStart=/opt/nba-prop-runtime/venv/bin/python x")
    assert FORBIDDEN.search('DB = "massey-postgres"')
    assert BROAD.search("pkill python") and BROAD.search("rm -rf /srv/") and BROAD.search("find /tmp -name x -delete")
    assert not BROAD.search("find /var/lib/sports-models/cbb-originator/scratch -mtime +3 -delete")
