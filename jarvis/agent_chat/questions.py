"""Questions an agent asks the person, answered from a card of choices.

The open standard of chat agents: when a decision genuinely belongs to the
person, the agent asks with a few prepared answers per question, marks the
one it recommends, and the person taps an answer (or types their own). One
call may carry a short series of related questions; the card walks through
them as "question 1 of 3". The card lives in the chat timeline as events —
``question_required``, one ``question_progress`` per answered step and
``question_resolved`` — so a reopened chat replays it like any other row.

Never a stall: when nobody answers for ``QUESTION_TIMEOUT_S`` the remaining
questions resolve to their recommendations. Every answer restarts that
window, so a person working through the series is never cut off. Closing the
card does the same at once ("let the agent decide"). The agent is told which
answers were picked for it, so it can say so in its reply.

Asking is rationed: at most ``MAX_QUESTIONS`` per call and ``MAX_ASKS_PER_TURN``
calls per turn. An agent that keeps asking gets its own recommendations back
instead of another card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

#: How long the card waits for the NEXT answer before the recommendations win.
QUESTION_TIMEOUT_S: Final[float] = 300.0

#: Choices on one question: fewer is no choice, more is a form.
MIN_OPTIONS: Final[int] = 2
MAX_OPTIONS: Final[int] = 4

#: Questions in one call — a short series, never a questionnaire.
MAX_QUESTIONS: Final[int] = 4

#: Cards per agent turn. A third ask in one turn gets the recommendations.
MAX_ASKS_PER_TURN: Final[int] = 2

#: Answer sources.
PERSON: Final[str] = "person"
TIMEOUT: Final[str] = "timeout"
SKIPPED: Final[str] = "skipped"
CANCELLED: Final[str] = "cancelled"
#: Applied without a card (a routine, a background run, the per-turn limit).
UNATTENDED: Final[str] = "unattended"

_MAX_QUESTION: Final[int] = 600
_MAX_LABEL: Final[int] = 80
_MAX_DESCRIPTION: Final[int] = 300
_MAX_ANSWER: Final[int] = 2000


@dataclass(frozen=True, slots=True)
class QuestionOption:
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    """One validated question: the recommended option is always index 0."""

    question: str
    options: tuple[QuestionOption, ...]
    recommendation_reason: str = ""

    @property
    def recommended(self) -> QuestionOption:
        return self.options[0]

    def to_payload(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "options": [{"label": o.label, "description": o.description} for o in self.options],
            "recommended": 0,
            "recommendation_reason": self.recommendation_reason,
        }


@dataclass(frozen=True, slots=True)
class QuestionAnswer:
    """What the person (or the timeout, a skip, a cancel) chose for one question."""

    answer: str
    #: The picked option's index, or ``None`` for a typed answer or a cancel.
    option_index: int | None
    #: ``person`` | ``timeout`` | ``skipped`` | ``cancelled`` | ``unattended``.
    source: str

    @property
    def by_person(self) -> bool:
        return self.source == PERSON

    def to_payload(self) -> dict[str, Any]:
        return {"answer": self.answer, "option_index": self.option_index, "source": self.source}


class TooManyQuestions(RuntimeError):
    """This turn already showed ``MAX_ASKS_PER_TURN`` question cards."""


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_one(raw: dict[str, Any]) -> QuestionSpec:
    question = _text(raw.get("question"), _MAX_QUESTION)
    if not question:
        raise ValueError("every question needs its question text")
    entries = raw.get("options")
    if not isinstance(entries, list):
        raise ValueError("options must be a list of {label, description}")
    options: list[QuestionOption] = []
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            label, description = _text(entry, _MAX_LABEL), ""
        elif isinstance(entry, dict):
            label = _text(entry.get("label"), _MAX_LABEL)
            description = _text(entry.get("description"), _MAX_DESCRIPTION)
        else:
            continue
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        options.append(QuestionOption(label, description))
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"give each question {MIN_OPTIONS} to {MAX_OPTIONS} distinct options")
    try:
        index = int(raw.get("recommended", 0))
    except (TypeError, ValueError):  # a malformed pick means the first option leads
        index = 0
    if not 0 <= index < len(options):
        raise ValueError("recommended must be the index of one of the options")
    if index:
        options.insert(0, options.pop(index))
    return QuestionSpec(
        question=question,
        options=tuple(options),
        recommendation_reason=_text(raw.get("recommendation_reason"), _MAX_DESCRIPTION),
    )


def parse_questions(args: dict[str, Any]) -> tuple[QuestionSpec, ...]:
    """Validate a tool call's arguments; raises ``ValueError`` with a fixable reason.

    Takes ``questions: [...]`` (the series) or a single question's fields at
    the top level. ``recommended`` moves that option to the front, so every
    consumer can rely on index 0 being the recommendation.
    """
    raw = args.get("questions")
    items: list[Any] = raw if isinstance(raw, list) else [args]
    if not items:
        raise ValueError("ask at least one question")
    if len(items) > MAX_QUESTIONS:
        raise ValueError(
            f"ask at most {MAX_QUESTIONS} questions at once; keep only the ones whose "
            "answer changes what you do and decide the rest yourself"
        )
    specs = tuple(_parse_one(item) for item in items if isinstance(item, dict))
    if len(specs) != len(items):
        raise ValueError("every question must be an object")
    if len({s.question.casefold() for s in specs}) != len(specs):
        raise ValueError("do not ask the same question twice")
    return specs


def answer_from(
    spec: QuestionSpec, *, option_index: int | None, text: str | None
) -> QuestionAnswer:
    """The person's pick as an answer; raises ``ValueError`` for neither/both/out of range."""
    typed = (text or "").strip()[:_MAX_ANSWER]
    if option_index is not None and typed:
        raise ValueError("send either an option or a typed answer, not both")
    if option_index is not None:
        if not 0 <= option_index < len(spec.options):
            raise ValueError("no such option")
        return QuestionAnswer(spec.options[option_index].label, option_index, PERSON)
    if typed:
        return QuestionAnswer(typed, None, PERSON)
    raise ValueError("send an option or a typed answer")


def recommended_answer(spec: QuestionSpec, source: str) -> QuestionAnswer:
    return QuestionAnswer(spec.recommended.label, 0, source)


__all__ = [
    "CANCELLED",
    "MAX_ASKS_PER_TURN",
    "MAX_OPTIONS",
    "MAX_QUESTIONS",
    "MIN_OPTIONS",
    "PERSON",
    "QUESTION_TIMEOUT_S",
    "SKIPPED",
    "TIMEOUT",
    "UNATTENDED",
    "QuestionAnswer",
    "QuestionOption",
    "QuestionSpec",
    "TooManyQuestions",
    "answer_from",
    "parse_questions",
    "recommended_answer",
]
