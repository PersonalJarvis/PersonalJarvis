"""A question an agent asks the person, answered from a card of choices.

The open standard of chat agents: when a decision genuinely belongs to the
person, the agent asks ONE question with a few prepared answers, marks the
one it recommends, and the person taps an answer (or types their own). The
card lives in the chat timeline as two events — ``question_required`` and
``question_resolved`` — so a reopened chat replays it like any other row.

Never a stall: an unanswered question resolves itself to the recommended
answer after ``QUESTION_TIMEOUT_S``. The agent's turn, and any workflow
waiting on it, keeps moving whether or not somebody is watching. The agent
is told the answer was picked automatically, so it can say so in its reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

#: How long a question waits for the person before the recommendation wins.
QUESTION_TIMEOUT_S: Final[float] = 300.0

#: Choices on one card: fewer is no choice, more is a form.
MIN_OPTIONS: Final[int] = 2
MAX_OPTIONS: Final[int] = 4

_MAX_QUESTION: Final[int] = 600
_MAX_HEADER: Final[int] = 24
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
    header: str = ""
    recommendation_reason: str = ""

    @property
    def recommended(self) -> QuestionOption:
        return self.options[0]

    def to_payload(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "header": self.header,
            "options": [{"label": o.label, "description": o.description} for o in self.options],
            "recommended": 0,
            "recommendation_reason": self.recommendation_reason,
        }


@dataclass(frozen=True, slots=True)
class QuestionAnswer:
    """What the person (or the timeout) chose."""

    answer: str
    #: The picked option's index, or ``None`` for a typed answer or a cancel.
    option_index: int | None
    #: ``person`` | ``timeout`` | ``cancelled``.
    source: str

    @property
    def auto(self) -> bool:
        return self.source == "timeout"

    def to_payload(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "option_index": self.option_index,
            "source": self.source,
            "auto": self.auto,
        }


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse_question(args: dict[str, Any]) -> QuestionSpec:
    """Validate a tool call's arguments; raises ``ValueError`` with a fixable reason.

    ``recommended`` (an index into ``options``) moves that option to the
    front, so every consumer can rely on index 0 being the recommendation.
    """
    question = _text(args.get("question"), _MAX_QUESTION)
    if not question:
        raise ValueError("question is required")
    raw = args.get("options")
    if not isinstance(raw, list):
        raise ValueError("options must be a list of {label, description}")
    options: list[QuestionOption] = []
    seen: set[str] = set()
    for entry in raw:
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
        raise ValueError(f"give {MIN_OPTIONS} to {MAX_OPTIONS} distinct options")
    pick = args.get("recommended", 0)
    try:
        index = int(pick)
    except (TypeError, ValueError):  # a malformed pick means the first option leads
        index = 0
    if not 0 <= index < len(options):
        raise ValueError("recommended must be the index of one of the options")
    if index:
        options.insert(0, options.pop(index))
    return QuestionSpec(
        question=question,
        options=tuple(options),
        header=_text(args.get("header"), _MAX_HEADER),
        recommendation_reason=_text(args.get("recommendation_reason"), _MAX_DESCRIPTION),
    )


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
        return QuestionAnswer(spec.options[option_index].label, option_index, "person")
    if typed:
        return QuestionAnswer(typed, None, "person")
    raise ValueError("send an option or a typed answer")


def timeout_answer(spec: QuestionSpec) -> QuestionAnswer:
    return QuestionAnswer(spec.recommended.label, 0, "timeout")


__all__ = [
    "MAX_OPTIONS",
    "MIN_OPTIONS",
    "QUESTION_TIMEOUT_S",
    "QuestionAnswer",
    "QuestionOption",
    "QuestionSpec",
    "answer_from",
    "parse_question",
    "timeout_answer",
]
