"""What a coding agent asks the person at the end of its turn — for every CLI.

Claude Code (and GLM, which runs its binary) can stop mid-turn and ask: its
control protocol hands Jarvis ``AskUserQuestion`` and ``ExitPlanMode``, and the
chat's cards answer them on stdin (``runner_cli``). Every other coding CLI —
Codex, Antigravity, Grok Build, OpenCode, Kimi, Cursor, DeepSeek Harness — runs
headless here, with no channel to ask on. Two end-of-turn prompts give all of
them the same two moments, without any vendor cooperation:

* **Questions.** The agent is told (:data:`ASK_PROTOCOL`, in front of every
  prompt) to end its reply with one fenced ``jarvis-ask`` block when a decision
  genuinely belongs to the person, and to stop there. When the turn finished,
  the service turns that block into the chat's ordinary question card
  (``question_required`` with ``deferred: true``). Nothing waits on it: the
  answers go back to the agent as the next message, and its vendor session
  resumes with the context it had.
* **Plan approval.** A turn that finished while the session is in plan mode
  gets a ``plan_ready`` card. *Build* switches the session to the runner's
  build mode and sends the go-ahead as the next message; *Keep planning* just
  closes the card. Claude's own ``ExitPlanMode`` still asks mid-turn; when it
  is approved the session leaves plan mode, so no second card follows.

Both cards are event-sourced. A card is open while its event has no closing
event and no later turn started — a reopened chat, or an app restart, answers
the same card the person sees.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from jarvis.agent_chat.questions import (
    PERSON,
    SKIPPED,
    QuestionAnswer,
    QuestionSpec,
    answer_from,
    parse_questions,
    recommended_answer,
)

#: The fence language of an end-of-turn question block. The frontend hides
#: these blocks from the rendered reply (``ASK_FENCE`` in
#: ``src/components/agentchat/askFence.ts``) — a parity test keeps both equal.
ASK_FENCE: Final[str] = "jarvis-ask"

#: In front of every prompt of a CLI that cannot ask mid-turn.
ASK_PROTOCOL: Final[str] = (
    "QUESTIONS FOR THE PERSON: decide everything you reasonably can yourself. "
    "Only when a decision genuinely belongs to the person (their taste, priorities, "
    "money, accounts, or an irreversible trade-off you cannot infer), end your reply "
    f"with exactly one fenced code block whose language is {ASK_FENCE}, containing "
    'JSON like {"questions": [{"question": "Which database?", "options": '
    '[{"label": "SQLite", "description": "One file, no server"}, {"label": "Postgres", '
    '"description": "A server to run"}], "recommendation_reason": "Why the first '
    'option wins"}]}. Ask 1-4 questions, each with 2-4 options, your recommendation '
    "first. Then stop and wait: the person answers in a card and their answers arrive "
    "as the next message. Never ask for permission to do what you were asked.\n\n"
)

#: What the agent is told when the person approved its plan.
PLAN_GO_AHEAD: Final[str] = (
    "The plan is approved. Implement it now, step by step, and report what you changed."
)

#: The answers to a plan card.
PLAN_BUILD: Final[str] = "build"
PLAN_KEEP: Final[str] = "keep"
PLAN_DECISIONS: Final[tuple[str, ...]] = (PLAN_BUILD, PLAN_KEEP)

_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```[ \t]*" + re.escape(ASK_FENCE) + r"[ \t]*\r?\n(?P<body>.*?)\r?\n?```",
    re.DOTALL,
)


def parse_ask_block(text: str) -> tuple[QuestionSpec, ...] | None:
    """The questions of the reply's last ``jarvis-ask`` block, or ``None``.

    A block that is not valid JSON, or whose questions do not validate, is no
    question at all: the reply still reads as text and the person answers it in
    the composer, exactly as before this card existed.
    """
    matches = list(_FENCE_RE.finditer(text or ""))
    if not matches:
        return None
    try:
        payload = json.loads(matches[-1].group("body"))
    except ValueError:  # a malformed fence is ordinary model text, not a question card
        return None
    if isinstance(payload, list):
        payload = {"questions": payload}
    if not isinstance(payload, dict):
        return None
    try:
        return parse_questions(payload)
    except ValueError:  # an invalid question block is shown as plain text instead
        return None


def turn_reply(events: Iterable[dict[str, Any]], turn_id: str) -> tuple[str, str]:
    """``(status, text)`` of a finished turn: its ``turn_finished`` status and
    every finished text block it wrote, joined. ``("", ...)`` while unfinished."""
    status = ""
    parts: list[str] = []
    for event in events:
        payload = event.get("payload") or {}
        if payload.get("turn_id") != turn_id:
            continue
        kind = event.get("kind")
        if kind == "assistant_text":
            text = str(payload.get("text") or "")
            if text.strip():
                parts.append(text)
        elif kind == "turn_finished":
            status = str(payload.get("status") or "")
    return status, "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class OpenAsk:
    """An end-of-turn question card that still waits for the person."""

    question_id: str
    turn_id: str
    specs: tuple[QuestionSpec, ...]
    answers: tuple[QuestionAnswer | None, ...]


@dataclass(frozen=True, slots=True)
class OpenPlan:
    """A plan card that still waits for the person."""

    turn_id: str
    build_mode: str


def _answers_from(raw: Any, count: int) -> list[QuestionAnswer | None]:
    rows = raw if isinstance(raw, list) else []
    out: list[QuestionAnswer | None] = []
    for index in range(count):
        row = rows[index] if index < len(rows) else None
        if not isinstance(row, dict):
            out.append(None)
            continue
        option = row.get("option_index")
        out.append(
            QuestionAnswer(
                str(row.get("answer") or ""),
                option if isinstance(option, int) else None,
                str(row.get("source") or PERSON),
            )
        )
    return out


def open_ask(events: Sequence[dict[str, Any]], question_id: str) -> OpenAsk | None:
    """The deferred card ``question_id`` if it is still open, else ``None``."""
    found: OpenAsk | None = None
    answers: list[QuestionAnswer | None] = []
    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if found is None:
            if (
                kind == "question_required"
                and payload.get("deferred")
                and payload.get("question_id") == question_id
            ):
                try:
                    specs = parse_questions({"questions": payload.get("questions") or []})
                except ValueError:  # a corrupt stored ask is treated as no open question
                    return None
                found = OpenAsk(question_id, str(payload.get("turn_id") or ""), specs, ())
                answers = [None] * len(specs)
            continue
        if kind == "turn_started":
            return None  # the person moved on; the card closed with it
        if payload.get("question_id") != question_id:
            continue
        if kind == "question_resolved":
            return None
        if kind == "question_progress":
            answers = _answers_from(payload.get("answers"), len(found.specs))
    if found is None:
        return None
    return OpenAsk(found.question_id, found.turn_id, found.specs, tuple(answers))


def open_plan(events: Sequence[dict[str, Any]], turn_id: str) -> OpenPlan | None:
    """The plan card of ``turn_id`` if it is still open, else ``None``."""
    found: OpenPlan | None = None
    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if found is None:
            if kind == "plan_ready" and payload.get("turn_id") == turn_id:
                found = OpenPlan(turn_id, str(payload.get("build_mode") or ""))
            continue
        if kind == "turn_started":
            return None
        if kind == "plan_resolved" and payload.get("turn_id") == turn_id:
            return None
    return found


def answer(
    card: OpenAsk, index: int, *, option_index: int | None, text: str | None
) -> list[QuestionAnswer | None]:
    """The card's answers with question ``index`` answered by the person.

    Raises ``ValueError`` for an answer that does not fit, ``IndexError`` for a
    question that does not exist or was already answered.
    """
    if not 0 <= index < len(card.specs) or card.answers[index] is not None:
        raise IndexError(index)
    answers = list(card.answers)
    answers[index] = answer_from(card.specs[index], option_index=option_index, text=text)
    return answers


def skipped(card: OpenAsk) -> list[QuestionAnswer]:
    """Every open question left to the agent's recommendation."""
    return [
        a if a is not None else recommended_answer(spec, SKIPPED)
        for spec, a in zip(card.specs, card.answers, strict=True)
    ]


def answers_prompt(specs: Sequence[QuestionSpec], answers: Sequence[QuestionAnswer]) -> str:
    """The next message the agent receives once the card closed."""
    lines = ["Answers to your questions:"]
    for spec, picked in zip(specs, answers, strict=True):
        if picked.source == SKIPPED:
            lines.append(
                f"- {spec.question} -> left to you; go with your recommendation ({picked.answer})."
            )
        else:
            lines.append(f"- {spec.question} -> {picked.answer}")
    lines.append("Continue with these answers.")
    return "\n".join(lines)


def answers_display(specs: Sequence[QuestionSpec], answers: Sequence[QuestionAnswer]) -> str:
    """What the timeline shows as the person's message: the picks, in their words."""
    return "\n".join(
        f"{spec.question} — {picked.answer}" for spec, picked in zip(specs, answers, strict=True)
    )


__all__ = [
    "ASK_FENCE",
    "ASK_PROTOCOL",
    "PLAN_BUILD",
    "PLAN_DECISIONS",
    "PLAN_GO_AHEAD",
    "PLAN_KEEP",
    "OpenAsk",
    "OpenPlan",
    "answer",
    "answers_display",
    "answers_prompt",
    "open_ask",
    "open_plan",
    "parse_ask_block",
    "skipped",
    "turn_reply",
]
