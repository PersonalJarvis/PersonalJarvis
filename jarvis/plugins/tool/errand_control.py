"""Errand control tools: answer, status, cancel — the other half of the dialog.

``start_errand`` opens the conversation; these three close it. Before them the
runner's ``provide_answers`` and ``cancel`` had zero production callers: the
errand would ask its one clarification round and the user's reply had nowhere
to go — the record sat in NEEDS_INPUT forever, silently. And "how is my errand
going" had no data source at all.

Target resolution is deliberate: the user says "economy, from Hamburg", not an
errand id. When no id is given, the tools act on the NEWEST matching errand —
the one the user was just asked about. An id parameter exists for the rare
case of several errands in flight at once.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.errands.schema import TERMINAL_STATES, Errand, ErrandState
from jarvis.errands.service import get_runner

log = logging.getLogger(__name__)

_UNWIRED = (
    "Errands are not available in this session — the brain stack is not "
    "fully wired. Tell the user plainly."
)


def _summary(errand: Errand) -> dict[str, Any]:
    """The safe, compact shape every tool result shares."""
    done_steps = sum(1 for s in errand.plan if s.done)
    return {
        "errand_id": errand.id,
        "goal": errand.goal,
        "state": str(errand.state),
        "plan_progress": f"{done_steps}/{len(errand.plan)}",
        "steps_taken": len(errand.steps),
        "open_questions": list(errand.open_questions),
        "outcome": errand.outcome,
    }


async def _newest(state_filter: Any) -> Errand | None:
    """Newest errand matching the filter, or None. Runner must exist."""
    runner = get_runner()
    if runner is None:
        return None
    for errand in await runner.store.list_recent(20):
        if state_filter(errand.state):
            return errand
    return None


class AnswerErrandTool:
    """Deliver the user's answers to the errand that is waiting on them."""

    name: str = "answer_errand"
    risk_tier: str = "monitor"
    description: str = (
        "Pass the user's answers to an errand that is waiting for input — "
        "after start_errand returned questions, or after an errand asked for "
        "something mid-run. Call it with the user's reply in their own words; "
        "the errand resumes working alone immediately. Without errand_id it "
        "answers the most recently asked errand, which is almost always "
        "right."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "answers": {
                "type": "string",
                "description": (
                    "The user's answers, complete and in their own words — "
                    "the errand cannot ask you what you meant."
                ),
            },
            "errand_id": {
                "type": "string",
                "description": "Only when several errands are waiting at once.",
            },
        },
        "required": ["answers"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        answers = (args.get("answers") or "").strip()
        if not answers:
            return ToolResult(success=False, output=None, error="No answers given.")
        runner = get_runner()
        if runner is None:
            return ToolResult(success=False, output=None, error=_UNWIRED)

        errand_id = (args.get("errand_id") or "").strip()
        if not errand_id:
            waiting = await _newest(lambda s: s is ErrandState.NEEDS_INPUT)
            if waiting is None:
                return ToolResult(
                    success=False,
                    output=None,
                    error=(
                        "No errand is waiting for input. Use errand_status to "
                        "see what is running."
                    ),
                )
            errand_id = waiting.id

        errand = await runner.provide_answers(errand_id, answers)
        if errand is None:
            return ToolResult(
                success=False, output=None, error=f"No errand found with id {errand_id}."
            )
        report = _summary(errand)
        report["say"] = (
            "Tell the user briefly that you are back on it and will report "
            "back. Do not narrate the plan."
        )
        return ToolResult(success=True, output=report)


class ErrandStatusTool:
    """Answer 'how is my errand going' from the durable records."""

    name: str = "errand_status"
    risk_tier: str = "safe"
    description: str = (
        "Look up the user's errands — running, waiting for input, and recently "
        "finished — with their goal, progress and outcome. Use it whenever the "
        "user asks how an errand or order is going, what is still open, or "
        "what happened to something they handed over."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "How many recent errands to return (default 5).",
            },
        },
        "required": [],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        runner = get_runner()
        if runner is None:
            return ToolResult(success=False, output=None, error=_UNWIRED)
        limit = max(1, min(int(args.get("limit") or 5), 20))
        errands = await runner.store.list_recent(limit)
        return ToolResult(
            success=True,
            output={
                "errands": [_summary(e) for e in errands],
                "say": (
                    "Report the states in plain language. For a waiting errand "
                    "ask its open questions now; for a finished one name the "
                    "outcome."
                ),
            },
        )


class CancelErrandTool:
    """Stop an errand on the user's word."""

    name: str = "cancel_errand"
    risk_tier: str = "monitor"
    description: str = (
        "Stop a running or waiting errand because the user said so — 'stop "
        "the booking', 'forget the order', 'cancel that'. Without errand_id "
        "it stops the most recent errand that is still open."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "errand_id": {
                "type": "string",
                "description": "Only when several errands are open at once.",
            },
        },
        "required": [],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        runner = get_runner()
        if runner is None:
            return ToolResult(success=False, output=None, error=_UNWIRED)

        errand_id = (args.get("errand_id") or "").strip()
        if not errand_id:
            open_errand = await _newest(lambda s: s not in TERMINAL_STATES)
            if open_errand is None:
                return ToolResult(
                    success=False, output=None, error="No errand is currently open."
                )
            errand_id = open_errand.id

        errand = await runner.cancel(errand_id)
        if errand is None:
            return ToolResult(
                success=False, output=None, error=f"No errand found with id {errand_id}."
            )
        report = _summary(errand)
        report["say"] = "Confirm briefly that it is stopped."
        return ToolResult(success=True, output=report)


__all__ = ["AnswerErrandTool", "CancelErrandTool", "ErrandStatusTool"]
