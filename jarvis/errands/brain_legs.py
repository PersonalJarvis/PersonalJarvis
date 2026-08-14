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
        user_utterance: str = "",
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
            # The consent gates (intent_confirms_args, the spawn gate, the
            # computer-use gate) judge this string as if the user spoke it.
            # It must therefore be the USER's words — the goal and their
            # answers — and NEVER the step intent: the intent is model text,
            # and a model that writes "delete the old folder" as its own plan
            # step must not thereby waive the destructive-command confirm.
            # Same reasoning as spawn_gate.py's "never the model's paraphrase".
            user_utterance=user_utterance,
            # Defer consequential (ask-tier) tools instead of blocking on a
            # UI approval nobody is watching: without this, an ask-tier call
            # inside a detached errand parked 60 s on an unattributed approval
            # card, then failed as "approval-denied (timeout)" and the run was
            # misreported as a stall. The deferral surfaces as a question the
            # runner can route to the user (C10/C11).
            voice_confirm=with_tools,
        )
        if getattr(aggregate, "finish_reason", "") == "voice_confirm_pending":
            # The executor stashed the consequential action and the loop
            # composed a localized confirmation question. Translate it into
            # the runner's ONE escape hatch: the errand pauses in NEEDS_INPUT,
            # the announcer speaks the question, and the user's answer returns
            # via answer_errand — in their own words, which is exactly what
            # the consent gates need to let the retried call through.
            question = (aggregate.text or "").strip() or (
                "May I go ahead with the pending consequential action?"
            )
            return LegOutcome(
                text=f"NEEDS-USER: {question}",
                tools_used=tuple(sorted(aggregate.executed_tool_names)),
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
