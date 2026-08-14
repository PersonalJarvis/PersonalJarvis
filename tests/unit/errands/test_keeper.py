"""Pins for C14 — what was learned is kept.

The note builder is pure and strict; the runner only owes ONE promise: the
keeper is invoked when an errand ends and can never break the outcome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.errands.keeper import learnings_note
from jarvis.errands.runner import ErrandRunner, LegOutcome
from jarvis.errands.schema import ContextFact, Errand, ErrandState
from jarvis.errands.store import ErrandStore


def _fact(*, durable: bool = True, confidence: float = 0.9) -> ContextFact:
    return ContextFact(
        statement="The user prefers aisle seats",
        source="wiki-recall",
        confidence=confidence,
        decisive=True,
        durable=durable,
    )


def _finished(**changes) -> Errand:
    base = Errand(id="err-9", goal="book a flight", state=ErrandState.COMPLETED)
    return base.model_copy(update=changes)


# ----------------------------------------------------------------------
# The note builder — pure
# ----------------------------------------------------------------------


def test_durable_confident_facts_are_kept_with_their_score() -> None:
    note = learnings_note(_finished(facts=(_fact(),)))
    assert note is not None
    assert "aisle seats" in note
    assert "9/10" in note
    assert "wiki-recall" in note


def test_a_fact_too_uncertain_to_act_on_is_too_uncertain_to_write_down() -> None:
    assert learnings_note(_finished(facts=(_fact(confidence=0.5),))) is None


def test_one_off_facts_stay_out_of_the_wiki() -> None:
    """durable=False means a price or a departure time — never wiki material."""
    assert learnings_note(_finished(facts=(_fact(durable=False),))) is None


def test_user_answers_are_always_kept_verbatim() -> None:
    note = learnings_note(
        _finished(asked_questions=("Which cabin class?",), answers="economy, always")
    )
    assert note is not None
    assert "Which cabin class?" in note
    assert "economy, always" in note


def test_a_cancelled_errand_keeps_nothing() -> None:
    """Stop also means: stop touching my things."""
    note = learnings_note(
        _finished(
            state=ErrandState.CANCELLED,
            facts=(_fact(),),
            asked_questions=("Which cabin class?",),
            answers="economy",
        )
    )
    assert note is None


def test_a_still_running_errand_keeps_nothing_yet() -> None:
    assert learnings_note(_finished(state=ErrandState.RUNNING, facts=(_fact(),))) is None


# ----------------------------------------------------------------------
# The runner around the keeper
# ----------------------------------------------------------------------


class MinimalLegs:
    """One evidence-bearing step, then the verifier accepts."""

    async def __call__(
        self,
        *,
        system_prompt: str,
        instruction: str,
        with_tools: bool,
        user_utterance: str = "",
    ):
        lowered = system_prompt.lower()
        if "gathering context" in lowered:
            return LegOutcome(text="FOUND: nothing useful")
        if "deciding what you still need" in lowered:
            return LegOutcome(text=json.dumps({"questions": []}))
        if "you are planning" in lowered:
            return LegOutcome(text=json.dumps({"steps": [{"intent": "do it"}]}))
        if "the verifier" in lowered:
            return LegOutcome(text=json.dumps({"done": True, "proof": "ref K1"}))
        return LegOutcome(text="Done. EVIDENCE: ref K1", tools_used=("browser",))


@pytest.mark.asyncio
async def test_the_keeper_runs_when_an_errand_ends(tmp_path: Path) -> None:
    kept: list[Errand] = []

    async def recorder(errand: Errand) -> None:
        kept.append(errand)

    runner = ErrandRunner(
        store=ErrandStore(tmp_path / "e.db"), execute_leg=MinimalLegs(), keep_learnings=recorder
    )
    await runner.start("book a flight")
    await runner.join()
    assert len(kept) == 1
    assert kept[0].state is ErrandState.COMPLETED


@pytest.mark.asyncio
async def test_a_broken_keeper_never_breaks_the_outcome(tmp_path: Path) -> None:
    async def exploding(errand: Errand) -> None:
        raise RuntimeError("wiki on fire")

    store = ErrandStore(tmp_path / "e.db")
    runner = ErrandRunner(store=store, execute_leg=MinimalLegs(), keep_learnings=exploding)
    opened = await runner.start("book a flight")
    await runner.join()
    final = await store.get(opened.id)
    assert final is not None
    assert final.state is ErrandState.COMPLETED
