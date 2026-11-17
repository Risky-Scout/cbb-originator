"""Regression for workflow_dispatch script injection in cbb-shadow-tick.yml.

GitHub substitutes ${{ }} before the shell parses a run: body. Dispatch inputs are
bound to FORCE and ALLOW_REVALIDATION instead, and only the exact string true
enables each flag. Other values, including false, empty, TRUE, yes, and inert
metacharacters, must not enable the flag and must not run as shell commands.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "cbb-shadow-tick.yml"

FORCE_LINE = 'if [ "$FORCE" = "true" ]; then echo "due=true" >> "$GITHUB_OUTPUT"; fi'
EXTRA_INIT = 'extra=""'
ALLOW_LINE = 'if [ "$ALLOW_REVALIDATION" = "true" ]; then extra="--allow-revalidation"; fi'
PYTHON_LINE = (
    "python -m cbb_platform.prod.shadow_tick --ops-root ../ops --restore --persist "
    "--commit --push $extra"
)
BASE_ARGV = [
    "-m",
    "cbb_platform.prod.shadow_tick",
    "--ops-root",
    "../ops",
    "--restore",
    "--persist",
    "--commit",
    "--push",
]

CASES = [
    pytest.param("true", True, id="true"),
    pytest.param("false", False, id="false"),
    pytest.param("", False, id="empty"),
    pytest.param("true; id", False, id="true-semicolon-id"),
    pytest.param("$(id)", False, id="command-substitution-id"),
    pytest.param("true\ntrue", False, id="true-newline-true"),
    pytest.param("yes", False, id="yes"),
    pytest.param("TRUE", False, id="TRUE"),
]


def run_bodies(text: str) -> list[str]:
    lines = text.splitlines()
    bodies: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if stripped.startswith("run:"):
            rest = stripped[4:].strip()
            if rest in {"|", "|-", "|+", ">", ">-", ">+", ""}:
                block: list[str] = []
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if nxt.strip() == "":
                        block.append("")
                        i += 1
                        continue
                    nindent = len(nxt) - len(nxt.lstrip(" "))
                    if nindent <= indent:
                        break
                    block.append(nxt)
                    i += 1
                bodies.append("\n".join(block))
                continue
            bodies.append(rest)
        i += 1
    return bodies


def _stripped_lines(bodies: list[str]) -> list[str]:
    return [line.strip() for body in bodies for line in body.splitlines()]


def test_shadow_tick_run_bodies_do_not_interpolate_inputs():
    text = WORKFLOW.read_text()
    bodies = run_bodies(text)
    assert bodies, "cbb-shadow-tick.yml has no run: bodies"
    for body in bodies:
        assert "${{ inputs." not in body
        assert "${{inputs." not in body
        assert "${{ github.event." not in body
        assert "${{github.event." not in body
    lines = _stripped_lines(bodies)
    assert FORCE_LINE in lines
    assert EXTRA_INIT in lines
    assert ALLOW_LINE in lines
    assert PYTHON_LINE in lines
    assert "FORCE: ${{ inputs.force }}" in text
    assert "ALLOW_REVALIDATION: ${{ inputs.allow_revalidation }}" in text


@pytest.mark.parametrize("value,enabled", CASES)
def test_force_and_allow_revalidation_boolean_semantics(value: str, enabled: bool):
    text = WORKFLOW.read_text()
    lines = _stripped_lines(run_bodies(text))
    for required in (FORCE_LINE, EXTRA_INIT, ALLOW_LINE, PYTHON_LINE):
        assert required in lines

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bindir = root / "bin"
        bindir.mkdir()
        id_log = root / "id.log"
        argv_file = root / "argv.txt"
        github_output = root / "github_output.txt"
        github_output.write_text("")
        (bindir / "id").write_text("#!/bin/sh\nprintf '%s\\n' id >> \"$ID_LOG\"\n")
        (bindir / "python").write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGV_FILE\"\n")
        os.chmod(bindir / "id", 0o755)
        os.chmod(bindir / "python", 0o755)
        script = "\n".join([FORCE_LINE, EXTRA_INIT, ALLOW_LINE, PYTHON_LINE])
        env = {
            "PATH": str(bindir),
            "HOME": str(root),
            "FORCE": value,
            "ALLOW_REVALIDATION": value,
            "GITHUB_OUTPUT": str(github_output),
            "ID_LOG": str(id_log),
            "ARGV_FILE": str(argv_file),
        }
        proc = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        id_ran = id_log.read_text() if id_log.exists() else ""
        argv = argv_file.read_text().splitlines() if argv_file.exists() else None
        output = github_output.read_text()
        detail = f"value={value!r} rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r} id={id_ran!r} argv={argv!r} out={output!r}"
        assert proc.returncode == 0, detail
        assert id_ran == "", detail
        assert argv is not None, detail
        expected = BASE_ARGV + (["--allow-revalidation"] if enabled else [])
        assert argv == expected, detail
        assert output == ("due=true\n" if enabled else ""), detail
        assert (enabled and "--allow-revalidation" in argv) or (not enabled and "--allow-revalidation" not in argv)
