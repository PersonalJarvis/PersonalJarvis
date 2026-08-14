"""Pins for consent inside an errand — whose words the gates judge, and how
an ask-tier tool call reaches the user instead of dying in a timeout.

Two protections:

1. The tool layer's consent gates (intent_confirms_args, the spawn gate, the
   computer-use gate) judge ``user_utterance`` as if the user spoke it. Inside
   an errand that string used to be the MODEL-authored plan step — so a model
   that wrote "delete the old export folder" as its own plan step thereby
   waived the destructive-command confirmation. Now the gates only ever see
   the user's verbatim words: the goal, plus their answers.

2. A consequential (ask-tier) tool call used to block 60 s on an approval
   card nobody was attributed to, then fail as "approval-denied (timeout)"
   and read as a stall. The leg now runs with voice_confirm, so the executor
   defers the action and the composed confirmation question comes back as
   NEEDS-USER — the runner's one escape hatch, which the announcer speaks.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.errands.brain_legs import BrainLegExecutor
from jarvis.errands.runner import ErrandRunner
from jarvis.errands.schema import ErrandState
from jarvis.errands.store import ErrandStore

from .test_errand_runner import ScriptedLegs, settled

# ----------------------------------------------------------------------
# 1. The gates judge the user's words, never the model's plan step
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_working_legs_carry_the_users_words_not_the_plan_step(
    tmp_path: Path,
) -> None:
    legs = ScriptedLegs(
        plan=["delete the old export folder"],  # model-authored — must never be judged
        work=["Done. EVIDENCE: folder list is clean"],
        verdicts=[{"done": True, "proof": "folder list"}],
    )
    runner = ErrandRunner(store=ErrandStore(tmp_path / "e.db"), execute_leg=legs)
    await settled(runner, "tidy up my exports")

    work_utterances = [u for phase, u in legs.utterances if phase == "work"]
    assert work_utterances, "the working leg must have run"
    for utterance in work_utterances:
        assert utterance == "tidy up my exports"
        assert "delete the old export folder" not in utterance


@pytest.mark.asyncio
async def test_after_answers_the_gates_see_goal_plus_answers(tmp_path: Path) -> None:
    """"Yes, buy the ticket" is consent in the user's own words — it must
    reach the gates so the retried call can pass legitimately."""
    legs = ScriptedLegs(
        questions=["May I pay the 89 euros?"],
        work=["Paid. EVIDENCE: receipt R1"],
        verdicts=[{"done": True, "proof": "receipt R1"}],
    )
    store = ErrandStore(tmp_path / "e.db")
    runner = ErrandRunner(store=store, execute_leg=legs)
    opened = await runner.start("book the concert ticket")
    assert opened.state is ErrandState.NEEDS_INPUT

    await runner.provide_answers(opened.id, "yes, buy the ticket")
    await runner.join()

    work_utterances = [u for phase, u in legs.utterances if phase == "work"]
    assert work_utterances
    assert all("book the concert ticket" in u for u in work_utterances)
    assert all("yes, buy the ticket" in u for u in work_utterances)


# ----------------------------------------------------------------------
# 2. A deferred consequential action becomes the errand's question
# ----------------------------------------------------------------------


class _FakeLoop:
    """Stands in for ToolUseLoop; returns a scripted aggregate."""

    captured: dict = {}
    aggregate: SimpleNamespace = SimpleNamespace(
        text="", finish_reason="stop", executed_tool_names=set()
    )

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, messages, **kwargs):
        _FakeLoop.captured = dict(kwargs)
        return _FakeLoop.aggregate


@pytest.fixture
def fake_loop(monkeypatch: pytest.MonkeyPatch) -> type[_FakeLoop]:
    monkeypatch.setattr("jarvis.errands.brain_legs.ToolUseLoop", _FakeLoop)
    _FakeLoop.captured = {}
    return _FakeLoop


def _executor() -> BrainLegExecutor:
    return BrainLegExecutor(brain=object(), tools={"browser": object()}, executor=object())


@pytest.mark.asyncio
async def test_the_leg_passes_the_users_words_and_defers_instead_of_blocking(
    fake_loop: type[_FakeLoop],
) -> None:
    fake_loop.aggregate = SimpleNamespace(
        text="all good", finish_reason="stop", executed_tool_names={"browser"}
    )
    outcome = await _executor()(
        system_prompt="WORKING on the errand",
        instruction="click the buy button",  # model text
        with_tools=True,
        user_utterance="book the concert ticket",
    )
    assert fake_loop.captured["user_utterance"] == "book the concert ticket"
    assert fake_loop.captured["voice_confirm"] is True  # defer, never 60s-block
    assert outcome.text == "all good"


@pytest.mark.asyncio
async def test_a_judgement_leg_never_defers(fake_loop: type[_FakeLoop]) -> None:
    fake_loop.aggregate = SimpleNamespace(
        text="{}", finish_reason="stop", executed_tool_names=set()
    )
    await _executor()(
        system_prompt="you are now the VERIFIER",
        instruction="Is this errand genuinely finished?",
        with_tools=False,
    )
    assert fake_loop.captured["voice_confirm"] is False


@pytest.mark.asyncio
async def test_a_deferred_action_comes_back_as_the_users_question(
    fake_loop: type[_FakeLoop],
) -> None:
    fake_loop.aggregate = SimpleNamespace(
        text="Soll ich die 89 Euro wirklich bezahlen?",  # i18n-allow: quoted TTS
        finish_reason="voice_confirm_pending",
        executed_tool_names={"browser"},
    )
    outcome = await _executor()(
        system_prompt="WORKING on the errand",
        instruction="pay for the ticket",
        with_tools=True,
        user_utterance="book the concert ticket",
    )
    assert outcome.text.startswith("NEEDS-USER: ")
    assert "89" in outcome.text


@pytest.mark.asyncio
async def test_a_deferral_without_a_question_still_asks_something(
    fake_loop: type[_FakeLoop],
) -> None:
    """A blank question must not silently strand the errand — the marker line
    itself is what flips the run into NEEDS_INPUT."""
    fake_loop.aggregate = SimpleNamespace(
        text="", finish_reason="voice_confirm_pending", executed_tool_names=set()
    )
    outcome = await _executor()(
        system_prompt="WORKING on the errand",
        instruction="pay",
        with_tools=True,
        user_utterance="book it",
    )
    assert outcome.text.startswith("NEEDS-USER: ")
    assert len(outcome.text) > len("NEEDS-USER: ")
