"""Builtin skills meet the instruction-skill standard (AD-S7).

Every builtin: parses, pushy "Use when" trigger clause, description within the
Anthropic limit, body under 500 lines, no fictional MCP tool names, and no
legacy ``TOOL:`` macro lines (the instruction model never executes them).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.skills.loader import parse_skill

BUILTIN_ROOT = (
    Path(__file__).resolve().parents[3] / "jarvis" / "skills" / "builtin"
)
SKILL_DIRS = sorted(
    p for p in BUILTIN_ROOT.iterdir() if (p / "SKILL.md").exists()
)


@pytest.mark.parametrize("root", SKILL_DIRS, ids=lambda p: p.name)
def test_builtin_meets_instruction_standard(root: Path) -> None:
    sk = parse_skill(root / "SKILL.md")
    fm = sk.frontmatter
    assert fm is not None, f"{root.name}: parse error: {sk.error}"

    # Anthropic limit: description <= 1024 chars.
    assert len(fm.description) <= 1024, (
        f"{root.name}: description too long ({len(fm.description)} chars)"
    )

    # Pushy trigger clause — "Use when ..." in description or when_to_use.
    combined = f"{fm.description} {fm.when_to_use or ''}".lower()
    assert "use when" in combined, f"{root.name}: missing 'Use when' clause"

    # Body budget: <= 500 lines (progressive disclosure — split into
    # references/ beyond that).
    assert len(sk.body.splitlines()) <= 500, f"{root.name}: body too long"

    # No fictional MCP tool names (gmail-mcp/list_unread etc.) — RC3 of
    # "skills run but do nothing".
    assert "-mcp/" not in sk.body, f"{root.name}: fictional MCP tool reference"

    # Instruction model: no legacy TOOL: macro lines.
    assert not re.search(r"^\s*TOOL:", sk.body, re.MULTILINE), (
        f"{root.name}: legacy TOOL: macro line"
    )
