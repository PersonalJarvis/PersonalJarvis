"""BrainLegExecutor — runs one errand phase against the real brain.

The seam between ``ErrandRunner`` (which owns the control flow) and the brain
stack. It exists so the runner's decisions are testable with scripted legs:
everything about *when* to keep going, *when* something is proven and *when* to
give up lives in the runner and is pinned by tests without a model; everything
about *how* to do one step lives here.

Per-leg budgets, and why they do not contradict C1:

C1 says an errand has no step limit. That is a statement about the ERRAND, not
about one call to a model. A working leg still needs a ceiling, because a
single leg that never returns is not progress — it is a wedge, and the outer
loop cannot measure a leg that has not ended. So a leg is bounded, the errand
is not: when a leg hits its ceiling it ends, the runner records it, the stall
detector judges it on evidence like any other leg, and the run continues.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from jarvis.brain.iteration_budget import IterationBudget
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainMessage

from .runner import LegOutcome

log = logging.getLogger(__name__)

#: Tool rounds inside ONE working leg. Generous — a booking page is several
#: reads and clicks and there is no reason to pay a full outer round for each —
#: but finite, so a wedged leg surfaces instead of hanging the errand.
WORKING_LEG_ROUNDS: int = 25

#: A judgement leg (plan, verify, re-check, clarify) has no tools and needs
#: exactly one answer.
JUDGEMENT_LEG_ROUNDS: int = 1


@dataclass
class BrainLegExecutor:
    """Executes errand phases through the standard tool-use loop."""

    brain: Any
    tools: dict[str, Any]
    executor: Any
    max_tokens: int = 4096

    async def __call__(
        self,
        *,
        system_prompt: str,
        instruction: str,
        with_tools: bool,
    ) -> LegOutcome:
        # A judgement leg gets NO tools at all. This is structural rather than
        # advisory: a verifier holding a browser tool goes and looks something
        # up, and a verdict that did more work is no longer a verdict on the
        # work — it is a new leg pretending to be a judgement.
        tools = dict(self.tools) if with_tools else {}
        loop = ToolUseLoop(
            self.brain,
            tools,
            self.executor,
            system_prompt=system_prompt,
            budget=IterationBudget(
                max_turns=WORKING_LEG_ROUNDS if with_tools else JUDGEMENT_LEG_ROUNDS
            ),
            max_tokens=self.max_tokens,
        )
        aggregate = await loop.run(
            [BrainMessage(role="user", content=instruction)],
            user_utterance=instruction,
        )
        return LegOutcome(
            text=aggregate.text or "",
            # ``executed_tool_names`` is what ACTUALLY ran, not what the model
            # asked for. The distinction matters here more than anywhere: a
            # blocked or hallucinated tool call must never look like progress
            # to the stall detector.
            tools_used=tuple(sorted(aggregate.executed_tool_names)),
        )


__all__ = ["JUDGEMENT_LEG_ROUNDS", "WORKING_LEG_ROUNDS", "BrainLegExecutor"]
