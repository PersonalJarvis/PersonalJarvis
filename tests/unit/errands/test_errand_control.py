"""Pins for the errand control tools — the user's side of the dialog.

start_errand asks questions; before these tools existed the user's reply had
nowhere to go (``provide_answers`` had zero production callers) and "how is my
errand going" had no data source. These pins protect the four verbs: answer,
status, cancel — and the id-free targeting that makes them usable by voice.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.core.protocols import ExecutionContext
from jarvis.errands.runner import ErrandRunner
from jarvis.errands.schema import ErrandState
from jarvis.errands.store import ErrandStore
from jarvis.plugins.tool.errand_control import (
    AnswerErrandTool,
    CancelErrandTool,
    ErrandStatusTool,
)

from .test_errand_runner import ScriptedLegs


@pytest.fixture
def ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="economy, from Hamburg",
        config={},
        memory_read=None,
    )


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: ErrandRunner | None) -> None:
    monkeypatch.setattr("jarvis.plugins.tool.errand_control.get_runner", lambda: runner)


def test_reachability() -> None:
    assert "answer-errand" in ROUTER_TOOLS
    assert "errand-status" in ROUTER_TOOLS
    assert "cancel-errand" in ROUTER_TOOLS
    assert ErrandStatusTool.risk_tier == "safe"  # a read must never prompt


@pytest.mark.asyncio
async def test_an_answer_reaches_the_waiting_errand_without_an_id(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The user says "economy", not a uuid — the newest waiting errand is
    the one they were just asked about."""
    store = ErrandStore(tmp_path / "e.db")
    legs = ScriptedLegs(
        questions=["Which cabin class?"],
        work=["Booked economy. EVIDENCE: ref AA11"],
        verdicts=[{"done": True, "proof": "ref AA11"}],
    )
    runner = ErrandRunner(store=store, execute_leg=legs)
    opened = await runner.start("book a flight")
    assert opened.state is ErrandState.NEEDS_INPUT
    _patch_runner(monkeypatch, runner)

    result = await AnswerErrandTool().execute({"answers": "economy"}, ctx)
    assert result.success is True
    assert result.output["errand_id"] == opened.id
    assert "report back" in result.output["say"]

    await runner.join()
    final = await store.get(opened.id)
    assert final is not None
    assert final.state is ErrandState.COMPLETED


@pytest.mark.asyncio
async def test_answer_with_nothing_waiting_is_an_honest_no(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = ErrandRunner(store=ErrandStore(tmp_path / "e.db"), execute_leg=ScriptedLegs())
    _patch_runner(monkeypatch, runner)
    result = await AnswerErrandTool().execute({"answers": "economy"}, ctx)
    assert result.success is False
    assert "No errand is waiting" in (result.error or "")


@pytest.mark.asyncio
async def test_status_reports_the_open_question_and_the_outcome(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ErrandStore(tmp_path / "e.db")
    legs = ScriptedLegs(questions=["Which airport?"])
    runner = ErrandRunner(store=store, execute_leg=legs)
    await runner.start("book a flight")
    _patch_runner(monkeypatch, runner)

    result = await ErrandStatusTool().execute({}, ctx)
    assert result.success is True
    (entry,) = result.output["errands"]
    assert entry["state"] == "needs_input"
    assert entry["open_questions"] == ["Which airport?"]


@pytest.mark.asyncio
async def test_cancel_without_an_id_stops_the_newest_open_errand(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ErrandStore(tmp_path / "e.db")
    legs = ScriptedLegs(questions=["Which airport?"])  # stays NEEDS_INPUT = open
    runner = ErrandRunner(store=store, execute_leg=legs)
    opened = await runner.start("book a flight")
    _patch_runner(monkeypatch, runner)

    result = await CancelErrandTool().execute({}, ctx)
    assert result.success is True
    assert result.output["errand_id"] == opened.id
    final = await store.get(opened.id)
    assert final is not None
    assert final.state is ErrandState.CANCELLED


@pytest.mark.asyncio
async def test_every_verb_degrades_honestly_when_unwired(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, None)
    for tool, args in (
        (AnswerErrandTool(), {"answers": "economy"}),
        (ErrandStatusTool(), {}),
        (CancelErrandTool(), {}),
    ):
        result = await tool.execute(args, ctx)
        assert result.success is False
        assert "not" in (result.error or "").lower()
