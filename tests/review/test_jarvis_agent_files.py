"""Active review definitions and runtime tool boundaries after agent retirement."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jarvis.core.review.spawns import DEFAULT_REVIEWER_TOOLS, DEFAULT_WORKER_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".agents" / "agents"
REVIEWER_FILE = AGENTS_DIR / "code-reviewer.md"


def _reviewer():
    text = REVIEWER_FILE.read_text(encoding="utf-8")
    frontmatter, body = text.removeprefix("---\n").split("\n---\n", 1)
    return yaml.safe_load(frontmatter), body


def test_agents_dir_exists() -> None:
    assert AGENTS_DIR.is_dir()


@pytest.mark.parametrize("root", [".agents", ".claude"])
def test_active_reviewer_file_exists(root: str) -> None:
    assert (REPO_ROOT / root / "agents" / "code-reviewer.md").is_file()


def test_reviewer_frontmatter_strict() -> None:
    frontmatter, body = _reviewer()
    assert frontmatter["name"] == "code-reviewer"
    assert frontmatter["description"].strip()
    assert frontmatter["role"] == "reviewer"
    assert tuple(part.strip() for part in frontmatter["tools"].split(",")) == (
        "Read", "Grep", "Glob",
    )
    assert body.strip()


def test_reviewer_body_contains_read_only_instruction() -> None:
    _, body = _reviewer()
    assert "You write NO code" in body
    assert "AGENTS.md" in body
    assert "REQUEST_CHANGES" in body and "BLOCK" in body


def test_runtime_reviewer_cannot_edit_or_execute() -> None:
    assert DEFAULT_REVIEWER_TOOLS == ("Read", "Grep", "Glob")


def test_runtime_worker_has_its_required_tools() -> None:
    assert set(DEFAULT_WORKER_TOOLS) == {"Read", "Write", "Edit", "Bash", "Grep", "Glob"}


@pytest.mark.parametrize("root", [".agents", ".claude"])
def test_retired_generic_agent_definitions_stay_removed(root: str) -> None:
    for name in ("jarvis-worker.md", "jarvis-reviewer.md"):
        assert not (REPO_ROOT / root / "agents" / name).exists()
