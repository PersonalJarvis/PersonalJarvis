"""AGENTS.md must stay the only agent-instructions file in the repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.ci import check_agents_md


def _repo(tmp_path: Path, monkeypatch, *files: str) -> Path:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    monkeypatch.setattr(check_agents_md, "_repo_root", lambda: tmp_path)
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("rules\n", encoding="utf-8")
    if files:
        subprocess.run(["git", "-C", str(tmp_path), "add", "--", *files], check=True)
    return tmp_path


def test_agents_md_alone_passes(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, "AGENTS.md", "jarvis/AGENTS.md")

    assert check_agents_md.main(["--quiet"]) == check_agents_md.EXIT_OK


def test_side_project_keeps_its_own_claude_md(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, "AGENTS.md", "skillbook/CLAUDE.md")

    assert check_agents_md.main(["--quiet"]) == check_agents_md.EXIT_OK


@pytest.mark.parametrize(
    "shadow",
    ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", "jarvis/CLAUDE.md"],
)
def test_tracked_claude_md_is_blocked(tmp_path, monkeypatch, capsys, shadow):
    _repo(tmp_path, monkeypatch, "AGENTS.md", shadow)

    assert check_agents_md.main(["--quiet"]) == check_agents_md.EXIT_FORBIDDEN
    assert shadow in capsys.readouterr().err


def test_untracked_claude_md_is_left_alone(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, "AGENTS.md")
    (repo / "CLAUDE.local.md").write_text("my own notes\n", encoding="utf-8")

    assert check_agents_md.main(["--quiet"]) == check_agents_md.EXIT_OK


def test_missing_agents_md_is_a_setup_error(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, "README.md")

    assert check_agents_md.main(["--quiet"]) == check_agents_md.EXIT_SETUP
