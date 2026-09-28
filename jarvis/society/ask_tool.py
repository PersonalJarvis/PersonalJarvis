"""``society_ask_user``: an agent asks the person one multiple-choice question.

The agent offers two to four prepared answers and marks the one it
recommends; the person taps one in the chat card or types their own
(jarvis/agent_chat/questions.py). Two guarantees keep work moving:

* After five minutes without an answer the recommendation is chosen
  automatically, and the agent is told so.
* A routine never asks. Its chat is unattended by design, so the tool is not
  offered there (``society_tools``) and refuses to wait if called anyway —
  the agent proceeds on its own recommendation.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from jarvis.agent_chat.questions import (
    MAX_OPTIONS,
    MIN_OPTIONS,
    QUESTION_TIMEOUT_S,
    QuestionAnswer,
    QuestionSpec,
    parse_question,
    timeout_answer,
)
from jarvis.core.protocols import ToolResult

from .routine_runner import is_routine_session

log = logging.getLogger(__name__)

__all__ = ["ASK_USER_TOOL_NAME", "AskUserTool"]

ASK_USER_TOOL_NAME: Final[str] = "society_ask_user"
_MINUTES: Final[int] = int(QUESTION_TIMEOUT_S // 60)


class AskUserTool:
    """Ask the person one question with prepared answers; wait for the pick."""

    name: str = ASK_USER_TOOL_NAME
    risk_tier: str = "safe"
    is_action_tool: bool = False
    description: str = (
        "Ask the user ONE question when a decision genuinely belongs to them and you "
        "cannot infer it from the request, your instructions, memory or sensible "
        f"defaults. Offer {MIN_OPTIONS}-{MAX_OPTIONS} concrete, mutually exclusive answers "
        "and recommend exactly one: put your recommendation FIRST and say in "
        "recommendation_reason why it beats the runner-up. The user taps an answer or "
        "types their own. If nobody answers within "
        f"{_MINUTES} minutes, your recommendation is chosen automatically, so recommend "
        "the safest sensible choice. Do not ask for permission to do what you were "
        "asked, and do not ask several questions in a row — decide the rest yourself."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question, clear and specific, ending with a question mark.",
            },
            "header": {
                "type": "string",
                "description": "Very short topic label for the card (max 24 chars).",
            },
            "options": {
                "type": "array",
                "minItems": MIN_OPTIONS,
                "maxItems": MAX_OPTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "1-5 word answer."},
                        "description": {
                            "type": "string",
                            "description": "What choosing this means, trade-offs included.",
                        },
                    },
                    "required": ["label"],
                },
                "description": "The answers, your recommendation first.",
            },
            "recommendation_reason": {
                "type": "string",
                "description": "One sentence: why the first option beats the runner-up.",
            },
        },
        "required": ["question", "options", "recommendation_reason"],
    }

    def __init__(self, runtime: Any, agent_id: str, *, session_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._session_id = session_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        try:
            spec = parse_question(args)
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=f"invalid question: {exc}")
        if is_routine_session(self._session_id):
            return _result(spec, timeout_answer(spec), reason="routine")
        service = self._runtime.chat_service()
        if service is None or not callable(getattr(service, "ask_question", None)):
            return _result(spec, timeout_answer(spec), reason="unattended")
        try:
            answer = await service.ask_question(self._session_id, spec)
        except RuntimeError as exc:
            # No running chat turn to show a card in (a background run): nobody
            # can answer, so the recommendation stands rather than a stall.
            log.info("society ask: %s cannot ask here (%s)", self._agent_id, exc)
            return _result(spec, timeout_answer(spec), reason="unattended")
        return _result(spec, answer)


def _result(spec: QuestionSpec, answer: QuestionAnswer, *, reason: str = "") -> ToolResult:
    if answer.source == "cancelled":
        return ToolResult(
            success=False,
            output={"answer": None, "source": "cancelled"},
            error="The user stopped this turn before answering. Do not continue the task.",
        )
    output: dict[str, Any] = {
        "question": spec.question,
        "answer": answer.answer,
        "source": answer.source,
    }
    if answer.option_index is not None:
        option = spec.options[answer.option_index]
        output["option_description"] = option.description
        output["recommended"] = answer.option_index == 0
    if reason == "routine":
        output["note"] = (
            "Routines run unattended and cannot ask the user. Your recommendation was "
            "applied; continue with it and mention the assumption in your result."
        )
        output["source"] = "unattended"
    elif reason == "unattended":
        output["note"] = (
            "Nobody can answer in this run. Your recommendation was applied; continue "
            "with it and mention the assumption in your result."
        )
        output["source"] = "unattended"
    elif answer.auto:
        output["note"] = (
            f"The user did not answer within {_MINUTES} minutes, so your recommendation "
            "was chosen automatically. Continue with it and briefly mention that you "
            "went with your recommendation."
        )
    elif answer.option_index is None:
        output["note"] = "The user typed their own answer instead of picking an option."
    return ToolResult(success=True, output=output)
