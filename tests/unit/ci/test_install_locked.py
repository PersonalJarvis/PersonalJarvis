"""The locked CI install: frozen export, enforced hashes, tools pinned via the lock."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.ci import install_locked

ROOT = Path(__file__).resolve().parents[3]


def test_export_never_resolves_and_leaves_the_project_out(tmp_path):
    command = install_locked.export_command("py", tmp_path / "out.txt", extras=["dev", "telephony"])
    assert command[:4] == ["py", "-m", "uv", "export"]
    assert "--frozen" in command and "--no-emit-project" in command
    assert "--locked" not in command  # the deps lane owns lock freshness
    assert command[-4:] == ["--extra", "dev", "--extra", "telephony"]
    assert "--no-hashes" not in command


def test_constraint_export_covers_every_extra_without_hashes(tmp_path):
    command = install_locked.export_command("py", tmp_path / "c.txt", all_extras=True)
    assert "--all-extras" in command and "--no-hashes" in command


def test_locked_set_requires_hashes_and_project_has_no_deps(tmp_path):
    locked = tmp_path / "locked.txt"
    first, editable = install_locked.install_commands(
        "py", locked, find_links=["--find-links", "https://example.invalid/c/"]
    )
    assert first[3:] == [
        "pip",
        "install",
        "--python",
        "py",
        "--require-hashes",
        "-r",
        str(locked),
        "--find-links",
        "https://example.invalid/c/",
    ]
    assert editable[-3:] == ["--no-deps", "-e", str(install_locked.ROOT)]


def test_tools_install_against_the_lock_as_constraints(tmp_path):
    constraints = tmp_path / "constraints.txt"
    commands = install_locked.install_commands(
        "py", tmp_path / "locked.txt", tools=["pytest"], constraints=constraints
    )
    assert commands[-1][-3:] == ["-c", str(constraints), "pytest"]
    with pytest.raises(ValueError):
        install_locked.install_commands("py", tmp_path / "locked.txt", tools=["pytest"])


def test_a_tool_outside_the_lock_is_refused():
    constraints = (
        "pytest==9.1.1\n"
        "    # via personal-jarvis\n"
        "pytest-asyncio==1.4.0 ; python_version >= '3.11'\n"
    )
    assert install_locked.unlocked_tools(["pytest", "Pytest_Asyncio"], constraints) == []
    assert install_locked.unlocked_tools(["hypothesis"], constraints) == ["hypothesis"]


def test_ci_test_jobs_install_from_the_lock():
    """No CI job that runs the test suite resolves version ranges fresh."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    for name in ("tests-linux", "tests-windows", "tests-macos", "python-fast"):
        steps = jobs[name]["steps"]
        assert any(step.get("uses") == "./.github/actions/install-locked" for step in steps), name
        for step in steps:
            assert 'pip install -e ".[' not in step.get("run", ""), name


def test_tools_only_leaves_the_locked_set_out(tmp_path):
    constraints = tmp_path / "constraints.txt"
    commands = install_locked.install_commands(
        "py", tmp_path / "locked.txt", tools=["typer"], constraints=constraints, base=False
    )
    assert len(commands) == 2
    assert "--require-hashes" not in commands[0]
    assert commands[0][-3:] == ["--no-deps", "-e", str(install_locked.ROOT)]
    assert commands[1][-3:] == ["-c", str(constraints), "typer"]
    with pytest.raises(ValueError):
        install_locked.install_commands("py", tmp_path / "locked.txt", base=False)


def test_tools_only_refuses_extras_and_an_empty_tool_list():
    with pytest.raises(SystemExit):
        install_locked.main(["--tools-only", "--extra", "dev", "--with", "pytest", "--dry-run"])
    with pytest.raises(SystemExit):
        install_locked.main(["--tools-only", "--dry-run"])


def test_light_ci_installs_are_pinned_through_the_lock():
    """The deliberately light jobs still take every version from uv.lock."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    for name in ("gates", "release-qualification", "jarvisctl"):
        steps = jobs[name]["steps"]
        light = [
            step
            for step in steps
            if step.get("uses") == "./.github/actions/install-locked"
            and step.get("with", {}).get("tools-only") == "true"
        ]
        assert light, name
        for step in steps:
            assert "pip install" not in step.get("run", ""), name
