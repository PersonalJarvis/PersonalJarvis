"""``society_ask_user``: an agent asks the person up to four short questions.

Each question offers two to four prepared answers with the recommended one
first; the person taps an answer (or types their own) and the card walks
through the series as "question 1 of 3" (jarvis/agent_chat/questions.py).
Three guarantees keep work moving and asking rare:

* When nobody answers for five minutes, the open questions take their
  recommendations, and the agent is told so. Closing the card does the same.
* At most four questions per call and two calls per turn; beyond that the
  agent gets its own recommendations back instead of another card.
* A routine never asks. Its chat is unattended by design, so the tool is not
  offered there (``society_tools``) and refuses to wait if called anyway.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from jarvis.agent_chat.questions import (
    CANCELLED,
    MAX_ASKS_PER_TURN,
    MAX_OPTIONS,
    MAX_QUESTIONS,
    MIN_OPTIONS,
    PERSON,
    QUESTION_TIMEOUT_S,
    SKIPPED,
    TIMEOUT,
    UNATTENDED,
    QuestionAnswer,
    QuestionSpec,
    TooManyQuestions,
    parse_questions,
    recommended_answer,
)
from jarvis.core.protocols import ToolResult

from .routine_runner import is_routine_session

log = logging.getLogger(__name__)

__all__ = ["ASK_USER_TOOL_NAME", "AskUserTool"]

ASK_USER_TOOL_NAME: Final[str] = "society_ask_user"
_MINUTES: Final[int] = int(QUESTION_TIMEOUT_S // 60)

#: How long one call waits for the card. CLI seats drop an MCP call after
#: about a minute (Claude Code: 60 s), so the tool hands back a "still
#: waiting" result in time and the agent polls with ``wait_for``.
WAIT_SLICE_S: Final[float] = 45.0

_QUESTION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The question, clear and specific, ending with a question mark.",
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
                        "description": "One short line: what choosing this means.",
                    },
                },
                "required": ["label"],
            },
            "description": "The answers, your recommendation FIRST.",
        },
        "recommendation_reason": {
            "type": "string",
            "description": "One sentence: why the first option beats the runner-up.",
        },
    },
    "required": ["question", "options", "recommendation_reason"],
}


class AskUserTool:
    """Ask the person a short series of questions with prepared answers."""

    name: str = ASK_USER_TOOL_NAME
    risk_tier: str = "safe"
    is_action_tool: bool = False
    description: str = (
        "Ask the user when a decision genuinely belongs to them: their taste, priorities, "
        "money, accounts, or an irreversible trade-off you cannot infer from the request, "
        "your instructions, memory or sensible defaults. Ask ONLY questions whose answer "
        "changes what you do; decide everything else yourself and say what you assumed. "
        f"Put all related questions into ONE call (at most {MAX_QUESTIONS}, fewer is "
        "better); the user answers them one after another. Each question offers "
        f"{MIN_OPTIONS}-{MAX_OPTIONS} concrete, mutually exclusive answers with your "
        "recommendation FIRST and recommendation_reason saying why it beats the "
        "runner-up. The user may also type their own answer. If nobody answers within "
        f"{_MINUTES} minutes, your recommendations are applied, so recommend the safest "
        "sensible choice. Never ask for permission to do what you were asked, never ask "
        "what you could look up, and never ask again what was already answered. "
        f"A turn can show at most {MAX_ASKS_PER_TURN} question cards. While the user has "
        "not answered yet, the call returns status 'waiting' with a question_id: then call "
        "this tool again with only wait_for set to that id, and do nothing else meanwhile."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_QUESTIONS,
                "items": _QUESTION_SCHEMA,
                "description": "The questions, most important first. Usually just one.",
            },
            "wait_for": {
                "type": "string",
                "description": (
                    "Only to keep waiting on a card you already opened: the question_id from "
                    "a 'waiting' result. Send it alone, without questions."
                ),
            },
        },
    }

    def __init__(self, runtime: Any, agent_id: str, *, session_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._session_id = session_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        wait_for = str(args.get("wait_for") or "").strip()
        if wait_for:
            return await self._keep_waiting(wait_for)
        try:
            specs = parse_questions(args)
        except ValueError as exc:
            # Bad questions go back to the agent as the tool error to fix.
            return ToolResult(success=False, output=None, error=f"invalid questions: {exc}")
        if is_routine_session(self._session_id):
            return _unattended(
                specs,
                "Routines run unattended and cannot ask the user. Your recommendations were "
                "applied; continue with them and mention the assumptions in your result.",
            )
        service = self._runtime.chat_service()
        if service is None or not callable(getattr(service, "open_questions", None)):
            return _unattended(specs, _NOBODY)
        try:
            question_id = await service.open_questions(self._session_id, specs, asker=self._asker())
        except TooManyQuestions:
            # The turn limit is reported to the agent in the returned result.
            return _unattended(
                specs,
                f"This turn already asked the user {MAX_ASKS_PER_TURN} times, so no card was "
                "shown. Your recommendations were applied: continue with them, decide any "
                "further questions yourself and list your assumptions in the reply.",
            )
        except RuntimeError as exc:
            # No running chat turn to show a card in (a background run): nobody
            # can answer, so the recommendations stand rather than a stall.
            log.info("society ask: %s cannot ask here (%s)", self._agent_id, exc)
            return _unattended(specs, _NOBODY)
        return await self._wait(service, question_id, specs)

    async def _keep_waiting(self, question_id: str) -> ToolResult:
        service = self._runtime.chat_service()
        try:
            specs = service.question_specs(self._session_id, question_id)
        except (AttributeError, KeyError):
            # Unknown question id is reported to the agent in the result.
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "No open question card with that id in this chat (it closed with the "
                    "turn). Continue with your recommendations; do not ask again."
                ),
            )
        return await self._wait(service, question_id, specs)

    async def _wait(
        self, service: Any, question_id: str, specs: tuple[QuestionSpec, ...]
    ) -> ToolResult:
        answers = await service.wait_questions(self._session_id, question_id, WAIT_SLICE_S)
        if answers is None:
            return ToolResult(
                success=True,
                output={
                    "status": "waiting",
                    "question_id": question_id,
                    "note": (
                        "The user has not finished answering yet; the card stays open and "
                        f"applies your recommendations on its own after {_MINUTES} quiet "
                        "minutes. Call society_ask_user again now with only "
                        f'{{"wait_for": "{question_id}"}} to keep waiting. Do nothing else '
                        "meanwhile and do not ask anything new."
                    ),
                },
            )
        return _result(specs, answers)

    def _asker(self) -> str:
        cached = getattr(self._runtime, "cached_agent", None)
        agent = cached(self._agent_id) if callable(cached) else None
        return str(getattr(agent, "name", "") or "")


_NOBODY: Final[str] = (
    "Nobody can answer in this run. Your recommendations were applied; continue with "
    "them and mention the assumptions in your result."
)


def _unattended(specs: tuple[QuestionSpec, ...], note: str) -> ToolResult:
    answers = [recommended_answer(spec, UNATTENDED) for spec in specs]
    result = _result(specs, answers)
    result.output["note"] = note
    return result


def _result(specs: tuple[QuestionSpec, ...], answers: list[QuestionAnswer]) -> ToolResult:
    if any(a.source == CANCELLED for a in answers):
        return ToolResult(
            success=False,
            output={"answers": None, "source": CANCELLED},
            error="The user stopped this turn before answering. Do not continue the task.",
        )
    rows: list[dict[str, Any]] = []
    for spec, answer in zip(specs, answers, strict=True):
        row: dict[str, Any] = {
            "question": spec.question,
            "answer": answer.answer,
            "source": answer.source,
        }
        if answer.option_index is not None:
            row["recommended"] = answer.option_index == 0
            description = spec.options[answer.option_index].description
            if description:
                row["option_description"] = description
        elif answer.source == PERSON:
            row["typed"] = True
        rows.append(row)
    output: dict[str, Any] = {"answers": rows}
    sources = {a.source for a in answers}
    if TIMEOUT in sources:
        output["note"] = (
            f"The user stopped answering for {_MINUTES} minutes, so your recommendations "
            "were applied to the open questions. Continue with them and briefly mention it."
        )
    elif SKIPPED in sources:
        output["note"] = (
            "The user closed the card and left the open questions to you: your "
            "recommendations were applied. Continue without asking again."
        )
    return ToolResult(success=True, output=output)
