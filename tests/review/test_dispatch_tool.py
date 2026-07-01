"""Tests for DispatchWithReviewTool (Phase 8.4).

Plan reference: §6.4 — tool call with a trivial task, mocked pipeline,
ToolResult format verification.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from jarvis.core.protocols import ExecutionContext
from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.state import RunState
from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
)
from jarvis.plugins.tool.dispatch_with_review import DispatchWithReviewTool

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="dispatch_with_review test",
        config={},
        memory_read=None,
    )


def _make_pipeline_with_pass(audit: ReviewAudit) -> ReviewPipeline:
    """Pipeline with mocks: worker delivers output, reviewer delivers pass."""
    async def worker_spawn(state: RunState, i: int) -> str:
        return "produced artifact"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.PASS, summary="all good", score=0.95
        )

    return ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=3,
    )


def _make_pipeline_with_cap_fire(audit: ReviewAudit) -> ReviewPipeline:
    async def worker_spawn(state: RunState, i: int) -> str:
        return f"output iter {i}"

    async def reviewer_spawn(
        state: RunState, output: str, i: int
    ) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary="not great yet",
            issues=[
                ReviewIssue(
                    severity="warning",
                    description="missing docstring on critical function",
                )
            ],
            score=0.5,
        )

    return ReviewPipeline(
        worker_spawn=worker_spawn,
        reviewer_spawn=reviewer_spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=2,
    )


# ----------------------------------------------------------------------
# Schema / Description
# ----------------------------------------------------------------------


def test_tool_schema_is_strict() -> None:
    """Plan §AD-9: strict=True, additionalProperties=false."""
    tool = DispatchWithReviewTool()
    schema = tool.schema
    assert schema["type"] == "object"
    assert schema["strict"] is True
    assert schema["additionalProperties"] is False
    assert "task" in schema["required"]


def test_tool_schema_max_iterations_bounded() -> None:
    tool = DispatchWithReviewTool()
    mi = tool.schema["properties"]["max_iterations"]
    assert mi["minimum"] == 1
    assert mi["maximum"] == 5  # AD-4 hard ceiling
    assert mi["default"] == 3


def test_tool_schema_rubric_enum_matches_plan() -> None:
    tool = DispatchWithReviewTool()
    rid = tool.schema["properties"]["rubric_id"]
    assert set(rid["enum"]) == {
        "default",
        "code_generation",
        "skill_authoring",
        "research",
    }
    assert rid["default"] == "default"


def test_tool_description_contains_selective_activation_hint() -> None:
    """Description is the only switch point for selective activation
    (Plan §AD-6) — the smalltalk negative hint must be present."""
    tool = DispatchWithReviewTool()
    desc = tool.description.lower()
    assert "user-irreversibel" in desc or "user_irreversibel" in desc  # i18n-allow: matches the German tool description under test
    assert "smalltalk" in desc
    assert "konversation" in desc  # i18n-allow: matches the German tool description under test


# ----------------------------------------------------------------------
# Execute
# ----------------------------------------------------------------------


def test_execute_success_returns_tool_result(tmp_path: Path) -> None:
    audit = ReviewAudit(path=tmp_path / "review.log")
    tool = DispatchWithReviewTool(
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=_make_pipeline_with_pass(audit),
    )

    result = asyncio.run(
        tool.execute(
            {"task": "write a python script that prints hello world", "rubric_id": "default"},
            _make_ctx(),
        )
    )

    assert result.success is True
    assert result.error is None
    assert result.output is not None
    assert result.output["outcome"] == "success"
    assert result.output["cap_fired"] is False
    assert result.output["iterations_total"] == 1
    assert result.output["final_artifact"] == "produced artifact"
    assert result.output["final_verdict"]["status"] == "pass"
    assert result.output["final_verdict"]["score"] == 0.95


def test_execute_cap_fired_returns_warnings(tmp_path: Path) -> None:
    audit = ReviewAudit(path=tmp_path / "review.log")
    tool = DispatchWithReviewTool(
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=_make_pipeline_with_cap_fire(audit),
    )

    result = asyncio.run(
        tool.execute(
            {"task": "write a function that merges two lists"},
            _make_ctx(),
        )
    )

    # Cap fire is `success=True` with warnings (Plan §AD-7: never fail-closed)
    assert result.success is True
    assert result.output is not None
    assert result.output["cap_fired"] is True
    assert result.output["outcome"] == "cap_fired"
    assert isinstance(result.output["warnings"], list)
    assert len(result.output["warnings"]) >= 1
    assert any(
        "missing docstring" in w.lower() or "not great" in w.lower()
        for w in result.output["warnings"]
    )


def test_execute_rejects_short_task(tmp_path: Path) -> None:
    """task < 20 chars is rejected before the pipeline call."""
    tool = DispatchWithReviewTool(
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
    )

    result = asyncio.run(
        tool.execute({"task": "short"}, _make_ctx())
    )
    assert result.success is False
    assert result.error and "20 zeichen" in result.error.lower()  # i18n-allow: matches the German tool error message under test


def test_execute_rejects_unknown_rubric_id(tmp_path: Path) -> None:
    tool = DispatchWithReviewTool(
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
    )
    result = asyncio.run(
        tool.execute(
            {
                "task": "write a python function that adds two numbers",
                "rubric_id": "totally_made_up",
            },
            _make_ctx(),
        )
    )
    assert result.success is False
    assert result.error is not None
    assert "rubric" in result.error.lower()


def test_execute_handles_pipeline_exception(tmp_path: Path) -> None:
    """Pipeline crash becomes ToolResult.success=False, NOT propagated."""
    audit = ReviewAudit(path=tmp_path / "review.log")

    async def crashing_worker(state: RunState, i: int) -> str:
        raise RuntimeError("simulated infrastructure failure")

    async def noop_reviewer(state: RunState, output: str, i: int) -> ReviewVerdict:
        return ReviewVerdict(
            status=ReviewStatus.PASS, summary="ok", score=1.0
        )

    pipe = ReviewPipeline(
        worker_spawn=crashing_worker,
        reviewer_spawn=noop_reviewer,
        audit=audit,
        max_iterations=1,
    )
    tool = DispatchWithReviewTool(
        runs_root=tmp_path / "runs",
        audit_log_path=tmp_path / "review.log",
        pipeline=pipe,
    )
    result = asyncio.run(
        tool.execute(
            {"task": "write a bash script that cleans the hard drive"},
            _make_ctx(),
        )
    )
    assert result.success is False
    assert result.error is not None
    assert "RuntimeError" in result.error or "infrastructure" in result.error


# ----------------------------------------------------------------------
# Entry-Point Discovery
# ----------------------------------------------------------------------


def test_entry_point_registered() -> None:
    from importlib.metadata import entry_points

    eps = list(entry_points(group="jarvis.tool"))
    names = [e.name for e in eps]
    assert "dispatch-with-review" in names, (
        f"dispatch-with-review missing from jarvis.tool entry-points: {names}"
    )


def test_router_tools_includes_dispatch_with_review() -> None:
    """Plan §AD-14 / Phase 8.4: ROUTER_TOOLS contains dispatch-with-review."""
    # NB: ``SUB_TOOLS`` was deleted in Welle 4 (the Sub-Jarvis tier no longer
    # exists — only "router" remains). The former recursion-guard assertion
    # against SUB_TOOLS is obsolete; updated 2026-06-28.
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "dispatch-with-review" in ROUTER_TOOLS
