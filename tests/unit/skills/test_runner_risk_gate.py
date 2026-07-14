"""Tests for SkillRunner's internal risk gate (AP-3).

A skill's ``TOOL:`` line must never bypass the same risk-tier discipline
every other tool invocation goes through. Before this fix, ``_check_risk``
was a no-op whenever no ``safety_enforcer`` was injected — which was ALWAYS
true, since none of the three production construction sites ever passed one
(``jarvis/ui/desktop_app.py``, ``jarvis/brain/factory.py``,
``jarvis/skills/cli.py``). These tests pin the fail-closed internal gate:
``block`` refuses, ``ask`` refuses (skills/cron triggers have no interactive
human to approve), ``safe``/``monitor`` still execute.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.core.config import SafetyConfig
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.skills.runner import SkillRunner
from jarvis.skills.schema import (
    Skill,
    SkillFrontmatter,
    SkillLifecycleState,
    SkillRiskPolicy,
)


class _StubRegistry:
    """SkillRunner only reads ``skill.name``/``skill.body`` — registry is unused."""


class _FakeTool:
    """A tool whose OWN static ``risk_tier`` is "safe" — the gate must still
    refuse a block/ask-declared skill call, proving the SKILL's frontmatter
    ``risk_policy`` governs the decision, not the tool's own declaration."""

    name = "fake_tool"
    schema: dict[str, Any] = {}
    description = "test tool"
    risk_tier = "safe"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls.append(args)
        return ToolResult(success=True, output="ran")


def _make_skill(tmp_path: Path, *, default_tier: str) -> Skill:
    fm = SkillFrontmatter(
        schema_version="1",
        name="gate_skill",
        description="tests the risk gate",
        risk_policy=SkillRiskPolicy(default_tier=default_tier),  # type: ignore[arg-type]
    )
    body = 'Do the thing.\nTOOL: fake_tool {"x": 1}\n'
    return Skill(
        path=tmp_path / "gate_skill" / "SKILL.md",
        frontmatter=fm,
        body=body,
        state=SkillLifecycleState.ACTIVE,
        body_hash="cafe",
        error=None,
    )


def _runner_with_default_safety(tool: Any) -> SkillRunner:
    """A SkillRunner whose internal risk gate is pinned to plain
    ``SafetyConfig()`` defaults — independent of whatever ``jarvis.toml``
    happens to be on the machine running the test."""
    runner = SkillRunner(
        registry=_StubRegistry(), tool_registry={"fake_tool": tool}, bus=None
    )
    runner._risk_evaluator_cache = RiskTierEvaluator(SafetyConfig())
    return runner


@pytest.mark.asyncio
async def test_block_tier_tool_is_refused(tmp_path: Path) -> None:
    tool = _FakeTool()
    runner = _runner_with_default_safety(tool)
    skill = _make_skill(tmp_path, default_tier="block")

    result = await runner.run(skill, args={})

    assert result.success is False
    assert tool.calls == []  # never executed
    assert "risk_tier denied" in (result.error or "")


@pytest.mark.asyncio
async def test_ask_tier_tool_is_refused_with_clear_message(tmp_path: Path) -> None:
    tool = _FakeTool()
    runner = _runner_with_default_safety(tool)
    skill = _make_skill(tmp_path, default_tier="ask")

    result = await runner.run(skill, args={})

    assert result.success is False
    assert tool.calls == []
    assert result.steps[0]["success"] is False
    assert "ask" in result.steps[0]["error"].lower()
    assert "approver" in result.steps[0]["error"].lower()


@pytest.mark.asyncio
async def test_safe_tier_tool_still_executes(tmp_path: Path) -> None:
    tool = _FakeTool()
    runner = _runner_with_default_safety(tool)
    skill = _make_skill(tmp_path, default_tier="safe")

    result = await runner.run(skill, args={})

    assert result.success is True
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_monitor_tier_tool_still_executes(tmp_path: Path) -> None:
    tool = _FakeTool()
    runner = _runner_with_default_safety(tool)
    skill = _make_skill(tmp_path, default_tier="monitor")

    result = await runner.run(skill, args={})

    assert result.success is True
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_approved_by_is_honest_risk_gate_label(tmp_path: Path) -> None:
    """The ``ExecutionContext`` passed to the tool must not fabricate an
    approval that never happened — it must name the internal risk gate."""
    seen_ctx: list[ExecutionContext] = []

    class _RecordingTool(_FakeTool):
        async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
            seen_ctx.append(ctx)
            return await super().execute(args, ctx)

    tool = _RecordingTool()
    runner = _runner_with_default_safety(tool)
    skill = _make_skill(tmp_path, default_tier="safe")

    await runner.run(skill, args={})

    assert seen_ctx
    assert seen_ctx[0].approved_by == "skill-runner:risk-gate"


@pytest.mark.asyncio
async def test_injected_safety_enforcer_still_takes_priority(tmp_path: Path) -> None:
    """Back-compat: an explicitly injected ``safety_enforcer`` overrides the
    internal gate entirely — its ``.check()`` decides."""

    class _DenyEnforcer:
        def check(self, *, tool_name: str, tier: str) -> tuple[bool, str]:
            return False, "enforcer-says-no"

    tool = _FakeTool()
    runner = SkillRunner(
        registry=_StubRegistry(),
        tool_registry={"fake_tool": tool},
        bus=None,
        safety_enforcer=_DenyEnforcer(),
    )
    skill = _make_skill(tmp_path, default_tier="safe")  # would pass the internal gate

    result = await runner.run(skill, args={})

    assert result.success is False
    assert tool.calls == []
    assert "enforcer-says-no" in (result.steps[0].get("error") or "")
