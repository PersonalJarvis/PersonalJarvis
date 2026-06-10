"""Instruction-skill model: SkillInvoked event, render_instructions, honest
error messages for unresolvable macro tools (AD-S1/S6).

See docs/superpowers/specs/2026-06-09-skill-system-rebuild-design.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.loader import parse_skill
from jarvis.skills.runner import SkillRunner
from jarvis.skills.schema import SkillInvoked


def _write_skill(tmp_path: Path, body: str, name: str = "demo") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    md = root / "SKILL.md"
    md.write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: Demo skill.\n"
        "config:\n"
        "  city: Berlin\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return md


def test_skill_invoked_event_frozen():
    ev = SkillInvoked(source_layer="brain.manager", skill_name="x", source="model")
    assert ev.skill_name == "x"
    assert ev.source == "model"
    with pytest.raises(Exception):
        ev.skill_name = "y"  # type: ignore[misc]


def test_render_instructions_returns_rendered_body(tmp_path):
    md = _write_skill(tmp_path, 'Hello {{ config.city }}\nTOOL: remember {"x": 1}')
    skill = parse_skill(md)
    runner = SkillRunner(registry=None, tool_registry={})
    text = runner.render_instructions(skill, args={})
    assert "Hello Berlin" in text
    assert "TOOL:" in text  # body verbatim, never executed here


def test_render_instructions_passes_args(tmp_path):
    md = _write_skill(tmp_path, "Note: {{ content }}")
    skill = parse_skill(md)
    runner = SkillRunner(registry=None, tool_registry={})
    text = runner.render_instructions(skill, args={"content": "buy milk"})
    assert "buy milk" in text


async def test_macro_run_with_unresolvable_tools_names_them(tmp_path):
    md = _write_skill(tmp_path, "TOOL: gmail-mcp/list_unread {}")
    skill = parse_skill(md)
    runner = SkillRunner(registry=None, tool_registry={})
    result = await runner.run(skill, args={})
    assert result.success is False
    assert "gmail-mcp/list_unread" in (result.error or "")
