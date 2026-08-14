"""Pins for the errand door — the tool the brain calls to hand over a job.

The behavioural claim being protected here is the one the maintainer cares
about most: an ORDER starts the work. Requiring the user to first say "agent"
or "in the background" is exactly the friction this replaces, so the absence of
that gate is pinned deliberately rather than left to chance.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.core.protocols import ExecutionContext
from jarvis.errands import service
from jarvis.errands.runner import ErrandRunner
from jarvis.errands.schema import ErrandState
from jarvis.errands.store import ErrandStore
from jarvis.missions.workers.worker_tool_broker import _FORBIDDEN_EXACT
from jarvis.plugins.tool.start_errand import StartErrandTool

from .test_errand_runner import ScriptedLegs


@pytest.fixture
def ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="book me a flight",
        config={},
        memory_read=None,
    )


@pytest.fixture(autouse=True)
def _clean_runner():
    service.reset_runner()
    yield
    service.reset_runner()


def test_tool_identity_and_reachability() -> None:
    assert StartErrandTool.name == "start_errand"
    assert StartErrandTool.risk_tier == "monitor"
    assert "start-errand" in ROUTER_TOOLS


def test_an_errand_cannot_dispatch_an_errand() -> None:
    """Same reasoning as AP-5/AP-14 for spawn tools: the runner already owns an
    unbounded loop, and a nested one inside a leg would run a second."""
    assert "start_errand" not in _FORBIDDEN_EXACT  # workers are not the issue…
    # …the guard is the leg-tool curation in the service.
    assert "start_errand" in service.LEG_TOOL_DENYLIST
    assert "start_errand" not in service.curated_leg_tools({"start_errand": object()})


def test_the_leg_arsenal_is_curated_not_the_raw_router_map() -> None:
    """An errand stays MORE capable than a mission worker (browser, shell,
    mail — finishing real jobs is the point) but three classes never enter a
    detached loop: loop-in-loop vehicles, config/self-mod, foreground UI
    remote control. The denylist is the documented difference."""
    sample = {name: object() for name in service.LEG_TOOL_DENYLIST} | {
        name: object()
        for name in (
            "browser",
            "run_shell",
            "gmail",
            "google_calendar",
            "credentials",
            "run_skill",
            "computer_use",
            "answer_errand",  # harmless: answering needs a WAITING errand
        )
    }
    curated = service.curated_leg_tools(sample)
    for banned in service.LEG_TOOL_DENYLIST:
        assert banned not in curated, f"{banned} must never reach an errand leg"
    for kept in (
        "browser",
        "run_shell",
        "gmail",
        "google_calendar",
        "credentials",
        "run_skill",
        "computer_use",
    ):
        assert kept in curated, f"{kept} is the errand's working arsenal"


def test_the_denylist_covers_the_three_classes() -> None:
    """Membership pins, so a rename in the tool layer fails loudly here."""
    assert {"start_errand", "spawn_worker"} <= service.LEG_TOOL_DENYLIST
    assert {
        "set_config_value",
        "switch-provider",
        "manage-mcp-server",
        "reveal-key-preview",
    } <= service.LEG_TOOL_DENYLIST
    assert {"navigate", "app-command", "visualize"} <= service.LEG_TOOL_DENYLIST


def test_the_description_does_not_demand_delegation_words() -> None:
    """The regression this whole wave exists to prevent.

    ``spawn_worker`` requires the user to name the vehicle. If that phrasing
    ever creeps into this description, orders stop working again and the
    product is back to needing magic words.
    """
    text = StartErrandTool.description.lower()
    assert "you do not need the user to say" in text
    for magic in ("only if the user says agent", "must say spawn", "must say delegate"):
        assert magic not in text


@pytest.mark.asyncio
async def test_missing_goal_is_refused_clearly(ctx: ExecutionContext) -> None:
    result = await StartErrandTool().execute({"goal": "   "}, ctx)
    assert result.success is False
    assert result.error


@pytest.mark.asyncio
async def test_unwired_app_degrades_honestly(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3 — never a blank failure, and never a pretend-success that leaves the
    user waiting for a booking nobody started."""
    monkeypatch.setattr("jarvis.plugins.tool.start_errand.get_runner", lambda: None)
    result = await StartErrandTool().execute({"goal": "book a flight"}, ctx)
    assert result.success is False
    assert "inline" in (result.error or "")


@pytest.mark.asyncio
async def test_questions_come_back_for_the_brain_to_ask(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legs = ScriptedLegs(questions=["Which airport?"])
    runner = ErrandRunner(store=ErrandStore(tmp_path / "e.db"), execute_leg=legs)
    monkeypatch.setattr("jarvis.plugins.tool.start_errand.get_runner", lambda: runner)

    result = await StartErrandTool().execute({"goal": "book a flight"}, ctx)
    assert result.success is True
    assert result.output["state"] == ErrandState.NEEDS_INPUT
    assert result.output["questions"] == ["Which airport?"]


@pytest.mark.asyncio
async def test_the_tool_returns_on_it_while_the_errand_keeps_working(
    ctx: ExecutionContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The turn gets "I'm on it"; the proof lands in the durable record.

    Before the detach fix the tool blocked the whole conversation turn until
    the errand finished — a spoken order froze the voice pipeline for the
    entire booking. The tool's contract is the OPENING report, never the
    outcome; the outcome travels via the errand record (and its events).
    """
    store = ErrandStore(tmp_path / "e.db")
    legs = ScriptedLegs(
        work=["Booked. EVIDENCE: ref ZZ42"],
        verdicts=[{"done": True, "proof": "ref ZZ42"}],
    )
    runner = ErrandRunner(store=store, execute_leg=legs)
    monkeypatch.setattr("jarvis.plugins.tool.start_errand.get_runner", lambda: runner)

    result = await StartErrandTool().execute({"goal": "book a flight"}, ctx)
    assert result.success is True
    assert result.output["state"] == ErrandState.RUNNING
    assert "report back" in result.output["say"]

    await runner.join()
    final = await store.get(result.output["errand_id"])
    assert final is not None
    assert final.state is ErrandState.COMPLETED
    assert "ZZ42" in final.outcome
