"""The confidence gate — code decides whether gathered context is enough (C13).

The model's job ends at proposing facts and numbers; whether the errand may run
on an assumption is decided HERE, against a fixed threshold. Same reasoning as
the rest of the runner (and ``jarvis/brain/spawn_gate.py``): a model asked "are
you sure enough?" says yes, so sureness is measured by a separate scoring leg
and judged by this module, never self-declared.

Scale: 0.0–1.0 internally, following ``CriticVerdict.confidence`` and its
``LOW_CONFIDENCE_THRESHOLD`` (``jarvis/missions/critic/verdict.py``); rendered
to the user as X/10. The verdict is a frozen dataclass in the house shape of
``EvidenceVerdict`` / ``MemoryVerdict`` / ``ProgressVerdict``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .schema import ContextFact

#: An assumption at or above this may carry the errand; below it, the fact goes
#: back to one more gather round and then to the user. 0.7 is the maintainer's
#: "7 out of 10" rule, verbatim.
MIN_ACTIONABLE_CONFIDENCE: Final[float] = 0.7

#: Gather rounds in total, first look included. Bounded so "look again before
#: asking" cannot become "never ask": after this many rounds, what is still
#: uncertain is honestly the user's to answer.
MAX_GATHER_ROUNDS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class ContextVerdict:
    """The gate's decision over one scored fact ledger."""

    #: True when every decisive fact is confident enough to act on.
    proceed: bool
    #: What must be put to the user (C10) — one question per uncertain fact.
    questions: tuple[str, ...]
    #: Machine-stable slug naming why, for logs and tests.
    reason: str
    #: The decisive facts that stayed below the threshold.
    uncertain: tuple[ContextFact, ...]


def assess(facts: tuple[ContextFact, ...] | list[ContextFact]) -> ContextVerdict:
    """Judge a scored ledger.

    Only DECISIVE facts can hold an errand back: colour-of-the-website facts
    may stay vague forever without costing the user a question. An empty ledger
    proceeds — that is exactly the pre-C13 behaviour, so a gather leg that
    produced prose instead of facts degrades to the old contract rather than
    blocking the run.
    """
    uncertain = tuple(f for f in facts if f.decisive and f.confidence < MIN_ACTIONABLE_CONFIDENCE)
    if not uncertain:
        reason = "all-decisive-facts-confident" if facts else "no-facts-gathered"
        return ContextVerdict(proceed=True, questions=(), reason=reason, uncertain=())
    return ContextVerdict(
        proceed=False,
        questions=tuple(question_for(f) for f in uncertain),
        reason="uncertain-decisive-facts",
        uncertain=uncertain,
    )


def question_for(fact: ContextFact) -> str:
    """The question that settles one uncertain fact.

    Prefers the scorer's own proposal (it knows WHY it doubted); falls back to
    asking for confirmation of the statement, so a scorer that rated without
    proposing questions still yields something askable.
    """
    if fact.question.strip():
        return fact.question.strip()
    return f"Please confirm or correct: {fact.statement.strip()}"


def confidence_view(confidence: float) -> str:
    """Render 0.0–1.0 as the user-facing X/10."""
    clamped = min(max(confidence, 0.0), 1.0)
    return f"{round(clamped * 10)}/10"


__all__ = [
    "MAX_GATHER_ROUNDS",
    "MIN_ACTIONABLE_CONFIDENCE",
    "ContextVerdict",
    "assess",
    "confidence_view",
    "question_for",
]
