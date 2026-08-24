"""Plugin grants match by PREFIX everywhere a task's allowlist is applied.

A template grants ``github`` while the live tools are ``github/list_issues``,
``github/create_issue``, … Exact matching in ``BrainManager._select_task_tools``
left such a task running with ZERO tools (it then "could not access GitHub"
at 09:00). The brain's allowlist and the unattended-approval bridge must
apply one and the same rule: :func:`jarvis.tasks.templates.grant_matches`.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import ActionApprovalRequired, ActionApproved
from jarvis.core.protocols import ToolResult
from jarvis.tasks.approval_bridge import TaskAutoApprover


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.schema: dict[str, Any] = {}


class _NullExecutor:
    async def execute(self, *a: Any, **kw: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


def _manager(names: list[str]) -> BrainManager:
    return BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={n: _FakeTool(n) for n in names},
        tool_executor=_NullExecutor(),  # type: ignore[arg-type]
    )


LIVE = ["gmail", "github/list_issues", "github/create_issue", "linear/list_issues",
        "github_search"]


def test_prefix_grant_covers_namespaced_tools() -> None:
    sel = _manager(LIVE)._select_task_tools(("github",))
    assert set(sel) == {"github/list_issues", "github/create_issue"}


def test_prefix_grant_does_not_cover_lookalike_names() -> None:
    sel = _manager(LIVE)._select_task_tools(("github",))
    assert "github_search" not in sel
    assert "linear/list_issues" not in sel


def test_exact_grant_still_works_and_mixes_with_prefix() -> None:
    sel = _manager(LIVE)._select_task_tools(("gmail", "linear"))
    assert set(sel) == {"gmail", "linear/list_issues"}


def test_empty_allowlist_yields_no_tools() -> None:
    assert _manager(LIVE)._select_task_tools(()) == {}


async def test_approver_pre_authorizes_by_prefix() -> None:
    bus = EventBus()
    approved: list[ActionApproved] = []

    async def _on(ev: ActionApproved) -> None:
        approved.append(ev)

    bus.subscribe(ActionApproved, _on)
    approver = TaskAutoApprover(bus)
    trace = uuid4()
    approver.arm(trace, ("github",), approved_by="scheduled-task:t1")

    await bus.publish(ActionApprovalRequired(
        trace_id=trace, tool_name="github/create_issue", risk_tier="ask",
    ))
    await bus.publish(ActionApprovalRequired(
        trace_id=trace, tool_name="github_search", risk_tier="ask",
    ))
    await bus.publish(ActionApprovalRequired(
        trace_id=uuid4(), tool_name="github/create_issue", risk_tier="ask",
    ))
    assert [e.tool_name for e in approved] == ["github/create_issue"]
    assert approved[0].approved_by == "scheduled-task:t1"
