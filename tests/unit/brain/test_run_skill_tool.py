"""Unit tests for the ``run-skill`` Brain-callable tool.

Plan: Skills-Brain-Integration. The tool resolves a skill by name, enforces
DRAFT/DISABLED/block-tier rejection, and delegates execution to the existing
``SkillRunner`` via the process-wide ``SkillContext``. Tests use Fakes (no
``unittest.mock``) per ``CLAUDE.md`` testing-conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.plugins.tool.run_skill import RunSkillTool
from jarvis.skills.schema import (
    Skill,
    SkillFrontmatter,
    SkillLifecycleState,
    SkillResult,
    SkillRiskPolicy,
)
from jarvis.skills.skill_context import SkillContext, set_skill_context

# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _FakeRegistry:
    """Tiny registry whose ``get`` raises ``KeyError`` like the real one."""

    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self._skills = skills or {}

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"Skill '{name}' nicht im Registry")
        return self._skills[name]


@dataclass
class _RunCall:
    skill: Skill
    args: dict[str, Any]


class _FakeRunner:
    """Records ``run`` calls and returns a scripted ``SkillResult``."""

    def __init__(self, scripted: SkillResult | None = None) -> None:
        self.calls: list[_RunCall] = []
        self.scripted = scripted or SkillResult(
            skill_name="",
            success=True,
            steps=tuple(),
            rendered_body="",
            error=None,
            duration_ms=0,
        )

    async def run(self, skill: Skill, args: dict[str, Any] | None = None) -> SkillResult:
        self.calls.append(_RunCall(skill=skill, args=args or {}))
        return self.scripted


class _ExplodingRunner:
    """Runner that raises to verify the tool catches inner exceptions."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls: list[_RunCall] = []

    async def run(self, skill: Skill, args: dict[str, Any] | None = None) -> SkillResult:
        self.calls.append(_RunCall(skill=skill, args=args or {}))
        raise self.exc


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_skill(
    name: str = "demo_skill",
    *,
    state: SkillLifecycleState = SkillLifecycleState.ACTIVE,
    default_tier: str = "monitor",
    frontmatter: bool = True,
) -> Skill:
    fm: SkillFrontmatter | None
    if frontmatter:
        fm = SkillFrontmatter(
            schema_version="1",
            name=name,
            description="fake skill",
            risk_policy=SkillRiskPolicy(default_tier=default_tier),  # type: ignore[arg-type]
        )
    else:
        fm = None
    return Skill(
        path=Path("nonexistent") / name / "SKILL.md",
        frontmatter=fm,
        body="dummy",
        state=state,
        body_hash="deadbeef",
        error=None,
    )


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="run the demo skill",
        config={},
        memory_read=None,
        approved_by="auto",
    )


@pytest.fixture(autouse=True)
def _reset_skill_context():
    """Reset the global SkillContext between tests."""
    set_skill_context(None)
    yield
    set_skill_context(None)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_skill_unknown_skill_returns_error() -> None:
    registry = _FakeRegistry()  # empty
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "missing"}, _ctx())

    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.error is not None
    assert "missing" in result.error
    assert runner.calls == []


@pytest.mark.asyncio
async def test_run_skill_rejects_draft_state() -> None:
    skill = _make_skill("draft_skill", state=SkillLifecycleState.DRAFT)
    registry = _FakeRegistry({"draft_skill": skill})
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "draft_skill"}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "DRAFT" in result.error
    assert runner.calls == [], "runner.run must NOT be called for DRAFT skills"


@pytest.mark.asyncio
async def test_run_skill_rejects_disabled_state() -> None:
    skill = _make_skill("off_skill", state=SkillLifecycleState.DISABLED)
    registry = _FakeRegistry({"off_skill": skill})
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "off_skill"}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "DISABLED" in result.error
    assert runner.calls == [], "runner.run must NOT be called for DISABLED skills"


@pytest.mark.asyncio
async def test_run_skill_happy_path() -> None:
    skill = _make_skill("hello_skill", state=SkillLifecycleState.ACTIVE)
    registry = _FakeRegistry({"hello_skill": skill})
    scripted = SkillResult(
        skill_name="hello_skill",
        success=True,
        steps=tuple(),
        rendered_body="hello",
        error=None,
        duration_ms=42,
    )
    runner = _FakeRunner(scripted=scripted)
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute(
        {"skill_name": "hello_skill", "args": {"foo": "bar"}}, _ctx()
    )

    assert result.success is True
    assert result.error is None
    assert result.output == {
        "skill_name": "hello_skill",
        "rendered_body": "hello",
        "steps_count": 0,
        "duration_ms": 42,
    }
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.skill is skill
    assert call.args == {"foo": "bar"}


@pytest.mark.asyncio
async def test_run_skill_propagates_runner_error() -> None:
    skill = _make_skill("flaky_skill", state=SkillLifecycleState.ACTIVE)
    registry = _FakeRegistry({"flaky_skill": skill})
    scripted = SkillResult(
        skill_name="flaky_skill",
        success=False,
        steps=tuple(),
        rendered_body="",
        error="oops",
        duration_ms=5,
    )
    runner = _FakeRunner(scripted=scripted)
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "flaky_skill"}, _ctx())

    assert result.success is False
    assert result.error == "oops"
    assert result.output is not None
    assert result.output["skill_name"] == "flaky_skill"


@pytest.mark.asyncio
async def test_run_skill_no_skill_context() -> None:
    set_skill_context(None)  # explicit
    tool = RunSkillTool()

    result = await tool.execute({"skill_name": "anything"}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "not initialized" in result.error.lower()


@pytest.mark.asyncio
async def test_run_skill_risk_tier_block() -> None:
    skill = _make_skill(
        "blocked_skill",
        state=SkillLifecycleState.ACTIVE,
        default_tier="block",
    )
    registry = _FakeRegistry({"blocked_skill": skill})
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({"skill_name": "blocked_skill"}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "block" in result.error.lower()
    assert runner.calls == [], "runner.run must NOT be called for block-tier skills"


@pytest.mark.asyncio
async def test_run_skill_missing_skill_name_arg() -> None:
    registry = _FakeRegistry()
    runner = _FakeRunner()
    set_skill_context(SkillContext(registry=registry, runner=runner))

    tool = RunSkillTool()
    result = await tool.execute({}, _ctx())

    assert result.success is False
    assert result.error is not None
    assert "skill_name" in result.error
    assert runner.calls == []


# ----------------------------------------------------------------------
# Surface assertions (cheap regression guards)
# ----------------------------------------------------------------------


def test_run_skill_tool_surface() -> None:
    """The tool must satisfy the basic Tool-protocol shape (name/schema/risk_tier)."""
    tool = RunSkillTool()
    assert tool.name == "run-skill"
    assert tool.risk_tier == "monitor"
    assert tool.schema["required"] == ["skill_name"]
    assert "skill_name" in tool.schema["properties"]
    # English-only description (Output Language Policy).
    assert "AVAILABLE SKILLS" in tool.description
