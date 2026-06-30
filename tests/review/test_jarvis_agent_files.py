"""Tests fuer .claude/agents/jarvis-{worker,reviewer}.md (Phase 8.2).

Plan-Referenz: §6.2 Akzeptanzkriterium 1, §AD-2 (Reviewer read-only),
Anthropic-Doc-Footgun: ohne `tools:`-Frontmatter erbt der Subagent
das volle Toolset des Parent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
WORKER_FILE = AGENTS_DIR / "jarvis-worker.md"
REVIEWER_FILE = AGENTS_DIR / "jarvis-reviewer.md"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Trennt YAML-Frontmatter vom Markdown-Body.

    Erwartet das ueblich Pandoc-/MkDocs-/OpenClaw-Format:
        ---
        key: value
        ---
        body...
    """
    if not text.startswith("---\n"):
        raise AssertionError(f"file does not start with --- marker:\n{text[:80]!r}")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise AssertionError("no closing --- marker for frontmatter")
    fm_block = text[4:end]
    body = text[end + 5 :]
    return yaml.safe_load(fm_block) or {}, body


# ----------------------------------------------------------------------
# Existence
# ----------------------------------------------------------------------


def test_agents_dir_exists() -> None:
    assert AGENTS_DIR.is_dir(), f"{AGENTS_DIR} fehlt"


@pytest.mark.parametrize("path", [WORKER_FILE, REVIEWER_FILE])
def test_subagent_file_exists(path: Path) -> None:
    assert path.is_file(), f"{path} fehlt"


# ----------------------------------------------------------------------
# Reviewer — strict tool-allowlist (AD-2)
# ----------------------------------------------------------------------


def test_reviewer_frontmatter_strict() -> None:
    fm, body = _split_frontmatter(REVIEWER_FILE.read_text(encoding="utf-8"))

    assert fm.get("name") == "jarvis-reviewer"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert fm.get("model") == "opus"

    # AD-2: tools EXAKT "Read, Grep, Glob" — nicht mehr, nicht weniger.
    tools_raw = fm.get("tools")
    assert isinstance(tools_raw, str), (
        f"tools must be a string in YAML, got {type(tools_raw).__name__}"
    )
    parsed_tools = [t.strip() for t in tools_raw.split(",")]
    assert parsed_tools == ["Read", "Grep", "Glob"], (
        f"Reviewer tool-allowlist drift: {parsed_tools!r} "
        "— AD-2 verlangt EXAKT [Read, Grep, Glob]"
    )

    assert body.strip(), "reviewer body must contain the system-prompt"


def test_reviewer_body_contains_hard_rules() -> None:
    """Smoke-Check: Reviewer-Prompt enthaelt die kritischen Anweisungen."""
    body = REVIEWER_FILE.read_text(encoding="utf-8")
    assert "DO NOT solve the task" in body
    assert "DO NOT write code" in body
    assert "DO NOT edit files" in body
    # JSON-only Output-Constraint
    assert "JSON only" in body or "Output ONLY valid JSON" in body
    # Status-Enum sichtbar
    assert "needs_revision" in body
    # Cap-Fire-relevante Severity-Werte
    assert "critical" in body
    assert "warning" in body
    assert "suggestion" in body


# ----------------------------------------------------------------------
# Worker — full toolset
# ----------------------------------------------------------------------


def test_worker_frontmatter() -> None:
    fm, body = _split_frontmatter(WORKER_FILE.read_text(encoding="utf-8"))

    assert fm.get("name") == "jarvis-worker"
    assert isinstance(fm.get("description"), str) and fm["description"].strip()
    assert fm.get("model") == "sonnet"

    tools_raw = fm.get("tools")
    assert isinstance(tools_raw, str), "tools must be a YAML scalar string"
    parsed = {t.strip() for t in tools_raw.split(",")}
    # Plan-§6.2 worker tools: Read, Write, Edit, Bash, Grep, Glob
    assert parsed == {"Read", "Write", "Edit", "Bash", "Grep", "Glob"}, (
        f"Worker tool-allowlist drift: {parsed!r}"
    )

    assert body.strip(), "worker body must contain the system-prompt"


def test_worker_body_mentions_feedback_block() -> None:
    """Worker muss verstehen wie Feedback aus Vor-Iterationen aussieht."""
    body = WORKER_FILE.read_text(encoding="utf-8")
    # Whitespace-normalisieren, weil der Plan-Wortlaut nach
    # `Reviewer feedback from` umbricht.
    flat = " ".join(body.split())
    assert "Reviewer feedback from iteration" in flat
    assert "hard requirement" in flat
