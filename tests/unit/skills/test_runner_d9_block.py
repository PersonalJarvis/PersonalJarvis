"""D9 recursion-protection test for the skills subsystem.

A skill body that embeds ``TOOL: run-skill {...}`` must NOT trigger another
``run-skill`` invocation through the runner. The structural guard is that
``SkillRunner`` is constructed without a ``tool_registry`` reference at every
production-construction site (``jarvis/ui/desktop_app.py``,
``jarvis/skills/cli.py``, the test fixtures); the runner therefore cannot
resolve the ``run-skill`` tool name and fails-closed at
``SkillRunner._resolve_tool``.

This test pins that behaviour against regression: future refactors that wire
the Brain's tool registry into the runner would re-introduce the recursion
vector and immediately break this test.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.skills.runner import SkillRunner
from jarvis.skills.schema import (
    Skill,
    SkillFailed,
    SkillFrontmatter,
    SkillLifecycleState,
    SkillRiskPolicy,
)


class _StubRegistry:
    """SkillRunner only reads ``skill.name``/``skill.body`` — registry is unused."""


class _BusListener:
    """Captures all events for assertions."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)


def _make_recursive_skill(tmp_path: Path) -> Skill:
    body = (
        "Trying to invoke run-skill from inside a skill body.\n"
        "TOOL: run-skill {\"skill_name\": \"another\"}\n"
    )
    fm = SkillFrontmatter(
        schema_version="1",
        name="recursive_skill",
        description="attempts to call run-skill inside its body",
        risk_policy=SkillRiskPolicy(default_tier="monitor"),
    )
    return Skill(
        path=tmp_path / "recursive_skill" / "SKILL.md",
        frontmatter=fm,
        body=body,
        state=SkillLifecycleState.ACTIVE,
        body_hash="cafe",
        error=None,
    )


@pytest.mark.asyncio
async def test_skill_body_with_run_skill_call_fails_gracefully(tmp_path: Path) -> None:
    """A ``TOOL: run-skill ...`` line in the skill body must fail-closed.

    The structural separation between Brain-tool-registry and Runner-tool-
    registry means the runner cannot resolve ``run-skill``; the step is
    recorded as failed and ``SkillFailed`` is emitted.
    """
    bus = EventBus()
    listener = _BusListener()
    bus.subscribe(SkillFailed, listener)

    # Construct the runner WITHOUT a tool_registry — this is the production-
    # invariant we are pinning. If a future refactor passes the brain registry
    # here, this test must turn red and force a redesign.
    runner = SkillRunner(registry=_StubRegistry(), tool_registry=None, bus=bus)

    skill = _make_recursive_skill(tmp_path)
    result = await runner.run(skill, args={})

    # Allow the bus dispatch to settle.
    await asyncio.sleep(0)

    assert result.success is False
    assert result.error is not None
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step["tool"] == "run-skill"
    assert step["success"] is False
    assert "not found" in (step.get("error") or "").lower()

    # SkillFailed must have been emitted with a useful error message.
    assert len(listener.events) == 1
    failed = listener.events[0]
    assert isinstance(failed, SkillFailed)
    assert failed.skill_name == "recursive_skill"
    assert failed.error  # non-empty
