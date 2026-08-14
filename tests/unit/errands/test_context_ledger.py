"""Behaviour pins for C13 — context is earned in three moves, then measured.

Same philosophy as test_errand_runner.py: the runner is driven with SCRIPTED
legs, because what is being pinned is the CONTROL FLOW — when the gate blocks,
when a second look happens, when the user is finally asked — and none of that
may depend on a model's mood. Each rule gets its hard negative: a gate that
always asks is as broken as one that never does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.errands.context_gate import (
    MIN_ACTIONABLE_CONFIDENCE,
    assess,
    confidence_view,
    question_for,
)
from jarvis.errands.runner import ErrandRunner, LegOutcome
from jarvis.errands.schema import ContextFact, Errand, ErrandState
from jarvis.errands.store import ErrandStore

# Phase markers, taken from the prompts. The scripted executor routes on these.
_CONTEXT = "GATHERING CONTEXT"
_SCORE = "the ASSESSOR"
_CLARIFY = "deciding what you still need"
_PLAN = "you are PLANNING"
_VERIFY = "you are now the VERIFIER"


def ledger(*facts: dict) -> str:
    """A gather answer: FOUND: prose plus the JSON fact ledger."""
    return "FOUND: looked things up.\n" + json.dumps({"facts": list(facts)})


def scores(*entries: dict) -> str:
    return json.dumps({"scores": list(entries)})


_HAMBURG = {
    "statement": "The user departs from Hamburg",
    "source": "calendar",
    "decisive": True,
    "durable": True,
}


class C13Legs:
    """A fake brain for the gather/score/clarify phases, recorded calls.

    ``context_rounds`` / ``score_rounds`` are consumed one per call (the last
    entry repeats), so a test can script a second look that finds more than
    the first. Work always lands evidence and the verifier accepts, so a run
    that gets past the gate finishes — the pins here are about the gate.
    """

    def __init__(
        self,
        *,
        context_rounds: list[str],
        score_rounds: list[str] | None = None,
        questions: list[str] | None = None,
    ) -> None:
        self.context_rounds = context_rounds
        self.score_rounds = score_rounds or []
        self.questions = questions or []
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        self._context_i = 0
        self._score_i = 0

    async def __call__(
        self,
        *,
        system_prompt: str,
        instruction: str,
        with_tools: bool,
        user_utterance: str = "",
    ) -> LegOutcome:
        lowered = system_prompt.lower()
        if _CONTEXT.lower() in lowered:
            self.calls.append("context")
            self.prompts.setdefault("context", []).append(system_prompt)
            text = self._take(self.context_rounds, self._context_i)
            self._context_i += 1
            return LegOutcome(text=text, tools_used=("calendar",))
        if _SCORE.lower() in lowered:
            self.calls.append("score")
            self.prompts.setdefault("score", []).append(system_prompt)
            text = self._take(self.score_rounds, self._score_i)
            self._score_i += 1
            return LegOutcome(text=text)
        if _CLARIFY.lower() in lowered:
            self.calls.append("clarify")
            self.prompts.setdefault("clarify", []).append(system_prompt)
            return LegOutcome(text=json.dumps({"questions": self.questions}))
        if _PLAN.lower() in lowered:
            self.calls.append("plan")
            return LegOutcome(text=json.dumps({"steps": [{"intent": "do the thing"}]}))
        if _VERIFY.lower() in lowered:
            self.calls.append("verify")
            return LegOutcome(text=json.dumps({"done": True, "proof": "ref OK1"}))
        self.calls.append("work")
        return LegOutcome(text="Did it. EVIDENCE: ref OK1", tools_used=("browser",))

    @staticmethod
    def _take(seq: list[str], index: int) -> str:
        if not seq:
            return ""
        return seq[min(index, len(seq) - 1)]


@pytest.fixture
def store(tmp_path: Path) -> ErrandStore:
    return ErrandStore(tmp_path / "errands.db")


async def settled(runner: ErrandRunner, goal: str) -> Errand:
    opened = await runner.start(goal)
    await runner.join()
    return await runner.store.get(opened.id) or opened


# ----------------------------------------------------------------------
# The gate itself — pure code, no model anywhere
# ----------------------------------------------------------------------


def _fact(confidence: float, *, decisive: bool = True, question: str = "") -> ContextFact:
    return ContextFact(
        statement="The user departs from Hamburg",
        source="calendar",
        confidence=confidence,
        decisive=decisive,
        question=question,
    )


def test_seven_out_of_ten_proceeds() -> None:
    """The maintainer's rule, verbatim: 7/10 is enough to act on."""
    verdict = assess((_fact(MIN_ACTIONABLE_CONFIDENCE),))
    assert verdict.proceed is True
    assert verdict.questions == ()


def test_below_seven_asks_with_the_scorers_question() -> None:
    verdict = assess((_fact(0.69, question="Which airport do you fly from?"),))
    assert verdict.proceed is False
    assert verdict.questions == ("Which airport do you fly from?",)
    assert verdict.reason == "uncertain-decisive-facts"


def test_only_decisive_facts_can_block() -> None:
    """A vague side detail must never cost the user a question."""
    verdict = assess((_fact(0.1, decisive=False),))
    assert verdict.proceed is True


def test_an_empty_ledger_proceeds_like_before_c13() -> None:
    verdict = assess(())
    assert verdict.proceed is True
    assert verdict.reason == "no-facts-gathered"


def test_fallback_question_names_the_statement() -> None:
    assert "Hamburg" in question_for(_fact(0.2))


def test_confidence_is_shown_as_x_out_of_ten() -> None:
    assert confidence_view(0.83) == "8/10"
    assert confidence_view(1.7) == "10/10"
    assert confidence_view(-0.4) == "0/10"


# ----------------------------------------------------------------------
# The runner around the gate
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_uncertain_fact_reaches_the_user_even_when_clarify_says_nothing(
    store: ErrandStore,
) -> None:
    """The model proposes, the code decides: an empty clarify answer cannot
    veto a measured uncertainty."""
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=[
            scores({"index": 0, "confidence": 0.3, "question": "Which airport, really?"})
        ],
        questions=[],
    )
    errand = await ErrandRunner(store=store, execute_leg=legs).start("book a flight")
    assert errand.state is ErrandState.NEEDS_INPUT
    assert "Which airport, really?" in errand.open_questions
    assert "Which airport, really?" in errand.asked_questions  # kept for C14


@pytest.mark.asyncio
async def test_confident_facts_start_work_without_interruption(store: ErrandStore) -> None:
    """The hard negative: a gate that still asks at 9/10 removes the point."""
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=[scores({"index": 0, "confidence": 0.9})],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is ErrandState.COMPLETED
    assert "clarify" in legs.calls  # the head-only round still happened
    assert errand.facts[0].confidence == 0.9
    assert errand.facts[0].decisive is True


@pytest.mark.asyncio
async def test_a_second_look_happens_before_the_user_is_asked(store: ErrandStore) -> None:
    """C13's core promise: look again before asking."""
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG), ledger(_HAMBURG)],
        score_rounds=[
            scores({"index": 0, "confidence": 0.4, "question": "Which airport?"}),
            scores({"index": 0, "confidence": 0.9}),
        ],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert legs.calls.count("context") == 2
    assert errand.state is ErrandState.COMPLETED
    # The second look was pointed at what stayed uncertain.
    assert "STILL TOO UNCERTAIN" in legs.prompts["context"][1]
    assert "Hamburg" in legs.prompts["context"][1]


@pytest.mark.asyncio
async def test_regather_stops_when_another_look_changes_nothing(store: ErrandStore) -> None:
    """Bounded honesty: when a second look moves nothing, asking is the honest
    move — not a third identical look, and never a silent run on a guess."""
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=[scores({"index": 0, "confidence": 0.4, "question": "Which airport?"})],
    )
    errand = await ErrandRunner(store=store, execute_leg=legs).start("book a flight")
    assert legs.calls.count("context") == 2  # one look, one re-look, then stop
    assert errand.state is ErrandState.NEEDS_INPUT
    assert "Which airport?" in errand.open_questions


@pytest.mark.asyncio
async def test_scorer_garbage_reads_as_uncertain(store: ErrandStore) -> None:
    """The safe direction: an unrated decisive fact must ask, never run."""
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=["Looks great, ship it!"],
    )
    errand = await ErrandRunner(store=store, execute_leg=legs).start("book a flight")
    assert errand.state is ErrandState.NEEDS_INPUT
    assert any("Hamburg" in q for q in errand.open_questions)


@pytest.mark.asyncio
async def test_prose_only_gather_behaves_like_before_c13(store: ErrandStore) -> None:
    """Back-compat pin: a gather leg that returns no ledger degrades to the
    pre-C13 contract — prose recorded, no scoring, no gate block."""
    legs = C13Legs(context_rounds=["FOUND: the user flies from Hamburg"])
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is ErrandState.COMPLETED
    assert "score" not in legs.calls
    assert "Hamburg" in errand.gathered_context


@pytest.mark.asyncio
async def test_the_source_inventory_reaches_the_gather_prompt(store: ErrandStore) -> None:
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=[scores({"index": 0, "confidence": 0.9})],
    )

    async def sources() -> str:
        return "- WhatsApp (installed app)\n- the wiki"

    await settled(
        ErrandRunner(store=store, execute_leg=legs, context_sources=sources), "book a flight"
    )
    assert "WhatsApp" in legs.prompts["context"][0]
    assert "WHERE CONTEXT CAN LIVE" in legs.prompts["context"][0]
    # The triage duty and the explicit-mention free pass ride with the list.
    assert "triage" in legs.prompts["context"][0]
    assert "free pass" in legs.prompts["context"][0]


@pytest.mark.asyncio
async def test_a_failing_inventory_never_breaks_a_start(store: ErrandStore) -> None:
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=[scores({"index": 0, "confidence": 0.9})],
    )

    async def broken() -> str:
        raise RuntimeError("inventory exploded")

    errand = await settled(
        ErrandRunner(store=store, execute_leg=legs, context_sources=broken), "book a flight"
    )
    assert errand.state is ErrandState.COMPLETED


@pytest.mark.asyncio
async def test_the_ledger_json_is_kept_out_of_the_prose_record(store: ErrandStore) -> None:
    """Cosmetic but pinned: facts live structured on the record, so the prose
    view must not repeat them as raw JSON in every later prompt."""
    legs = C13Legs(
        context_rounds=[ledger(_HAMBURG)],
        score_rounds=[scores({"index": 0, "confidence": 0.9})],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert '{"facts"' not in errand.gathered_context
