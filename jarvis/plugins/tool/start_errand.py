"""start_errand tool: hand a real-world job over and let it run to an outcome.

This is the door into the errand loop (docs/plans/autonomous-missions.md). The
brain calls it when the user gives an ORDER whose completion lives in the world
— book, order, arrange, cancel, sort out, get hold of — rather than a question
whose answer lives in a sentence.

Why the errand runs in the background rather than on the turn: the turn ends
when the user stops speaking, and "book me a flight" does not. The tool returns
as soon as the errand is under way (or as soon as it has questions), and the
run continues on its own — surviving a restart, because the record is durable.

Deliberately NOT gated behind explicit delegation vocabulary. ``spawn_worker``
is, and for a good reason (jarvis/brain/spawn_gate.py): the model kept starting
background CODE agents mid-conversation. This tool is the opposite case — the
maintainer's whole complaint is that an order has to be phrased as a delegation
before anything happens. The guard here is the SHAPE of the turn, stated in the
description: an order gets an errand, a question gets an answer.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.errands.schema import ErrandState
from jarvis.errands.service import get_runner

log = logging.getLogger(__name__)


class StartErrandTool:
    """Start an autonomous errand and report how it opened."""

    name: str = "start_errand"
    risk_tier: str = "monitor"
    description: str = (
        "Take on a real-world job and carry it out to completion — booking, "
        "ordering, arranging, cancelling, applying, getting something sorted. "
        "Use this when the user gives you an ORDER whose completion lives in "
        "the world rather than in your answer: 'book me a flight to Nice', "
        "'cancel my gym membership', 'order the printer cartridges', 'sort out "
        "the insurance claim'. You do NOT need the user to say 'agent', "
        "'background' or 'delegate' — the order itself is the instruction.\n\n"
        "Do NOT use it for a question ('what flights are there?'), for "
        "something you can answer or do in this turn with one tool call, or "
        "for coding work — code goes to spawn_worker.\n\n"
        "The errand looks up what it can for itself (calendar, mail, contacts, "
        "profile), asks the user any remaining questions ONCE, then works "
        "alone: it takes detours around obstacles, fetches its own confirmation "
        "codes, and finishes only when there is real proof. It survives a "
        "restart. Money is the one thing it stops for. Tell the user briefly "
        "that you are on it, and pass their goal in their own words."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "What must be true when this is finished, in the user's own "
                    "words and with every detail they gave — dates, names, "
                    "places, preferences, limits. This is the whole brief; the "
                    "errand cannot ask you afterwards what you meant."
                ),
            },
        },
        "required": ["goal"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        goal = (args.get("goal") or "").strip()
        if not goal:
            return ToolResult(
                success=False,
                output=None,
                error="No goal given — say what must be true when this is done.",
            )

        runner = get_runner()
        if runner is None:
            # Honest degradation (§3): never a blank failure, and never a
            # pretend-success that leaves the user waiting for a booking that
            # was never started.
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "Errands are not available in this session — the brain "
                    "stack is not fully wired. Do the task inline instead, and "
                    "tell the user plainly if it is too large for one turn."
                ),
            )

        # The runner detaches the loop itself and holds the task reference —
        # start() returns as soon as the errand is planned (or has questions),
        # never when it is finished. Ownership lives there so every door into
        # an errand (tool, REST, CLI, resume-at-boot) detaches identically.
        errand = await runner.start(goal, trace_id=str(ctx.trace_id))

        return ToolResult(success=True, output=_report(errand))


def _report(errand: Any) -> dict[str, Any]:
    """What the brain should tell the user right now."""
    base = {
        "errand_id": errand.id,
        "goal": errand.goal,
        "state": str(errand.state),
        "plan": [step.intent for step in errand.plan],
    }
    if errand.state is ErrandState.NEEDS_INPUT:
        base["questions"] = list(errand.open_questions)
        base["say"] = (
            "Ask the user these questions now, in their language, in one short "
            "turn. The errand starts as soon as they answer."
        )
        return base
    if errand.state is ErrandState.COMPLETED:
        base["proof"] = errand.outcome
        base["say"] = "Tell the user it is done, and name the proof."
        return base
    if errand.state is ErrandState.RUNNING:
        base["say"] = (
            "Tell the user briefly that you are on it and will report back. Do "
            "not narrate the plan step by step and do not ask anything else."
        )
        return base
    base["outcome"] = errand.outcome
    base["say"] = "Tell the user honestly where this stopped and why."
    return base


__all__ = ["StartErrandTool"]
