"""A roster read is factual, read-only and silent until the roster is known."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.brain import factory
from jarvis.brain.manager import BrainManager
from jarvis.brain.spawn_gate import llm_spawn_allowed
from jarvis.brain.turn_planner import TurnReason, plan_turn
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.delegate_to_agent import SocietyStatusTool
from jarvis.society import runtime as runtime_module
from jarvis.society.intent import blocks_background_spawn, is_inventory_question
from jarvis.society.lead_card import society_agent_names
from jarvis.society.runtime import SocietyRuntime
from jarvis.voice.instant_ack import plan_instant_ack


@pytest.mark.parametrize(
    "text",
    [
        "Was hast du für Agenten?",  # i18n-allow: reported voice input
        "Welche Agenten hast du?",  # i18n-allow: voice input
        "Was haben wir fuer Agenten?",  # i18n-allow: voice input
        "Was hast du eigentlich fuer Agenten?",  # i18n-allow: voice input
        "Please show my agents right now",
        "Which agents do you have access to?",
        "Who is on my team?",
        "Show my agents",
        "¿Qué agentes tienes?",  # i18n-allow: voice input
    ],
)
def test_inventory_cannot_announce_or_spawn(text):
    plan = plan_turn(text)
    assert plan.reasons == {TurnReason.SOCIETY}
    assert plan.required_capabilities == ("society_status",)
    assert plan_instant_ack(plan, text) is None
    assert not llm_spawn_allowed(text)
    manager = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    assert not manager._should_force_spawn(text)


@pytest.mark.parametrize(
    "text",
    [
        "List my agents and ask Scout to research this.",
        "Which agents do you have? Give Scout this task.",
        "Create a research agent called Scout.",
        "Which agents are used in chemistry?",
        "My travel agent booked a flight.",
    ],
)
def test_other_requests_are_not_consumed_as_inventory(text):
    assert not is_inventory_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "Create a persistent research agent called Scout.",
        "Erstelle einen Agenten fuer meine E-Mails.",  # i18n-allow: voice input
        "Crea un agente para investigar proveedores.",  # i18n-allow: voice input
        "Update my agent's responsibilities.",
    ],
)
def test_team_management_never_announces_or_spawns_a_worker(text):
    plan = plan_turn(text)
    assert TurnReason.SOCIETY in plan.reasons
    assert plan_instant_ack(plan, text) is None
    assert not llm_spawn_allowed(text)


def test_addressed_specialist_is_not_an_anonymous_worker():
    assert blocks_background_spawn("Scout, do a deep dive on this", ("Scout",))
    assert not blocks_background_spawn("Spawn a worker to help Scout", ("Scout",))
    assert not blocks_background_spawn("Research scouting opportunities", ("Scout",))


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, tool, args, *, user_utterance, config_snapshot, trace_id):
        self.calls.append(tool.name)
        return await tool.execute(
            args,
            ExecutionContext(
                trace_id=trace_id,
                user_utterance=user_utterance,
                config=config_snapshot,
                memory_read=None,
            ),
        )


async def test_first_turn_loads_saved_team_and_inventory_executes_only_status(
    tmp_path, monkeypatch
):
    saved = SocietyRuntime(tmp_path, seed_starter_team=False)
    await saved.ensure_started()
    await saved.roster.create(name="Archive", title="Research librarian")
    await saved.close()
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    monkeypatch.setattr(runtime_module, "_current", None)
    monkeypatch.setattr(factory, "_SOCIETY_FACTORY_REF", [lambda: runtime])
    assert society_agent_names() == ()
    try:
        await asyncio.gather(factory.prepare_society_context(), factory.prepare_society_context())
        assert society_agent_names() == ("Archive",)
        tool = SocietyStatusTool(runtime_resolver=lambda: runtime)
        manager = BrainManager(
            config=JarvisConfig(), bus=EventBus(), tools={"society_status": tool}
        )
        manager._reply_language = "en"
        executor = RecordingExecutor()
        manager._tool_executor = executor
        text = await manager.generate(
            "Which agents do you have?", use_history=False, trace_id=uuid4()
        )
        assert "Archive" in text
        assert executor.calls == ["society_status"]
        assert runtime.scheduler.active_runs("archive") == 0
    finally:
        await runtime.close()


async def test_inventory_with_no_tool_returns_honest_failure():
    manager = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    manager._reply_language = "en"
    result = await manager._run_society_inventory_fast_path("Who is on the team?")
    assert "cannot retrieve the agent roster" in result


async def test_unavailable_team_does_not_break_conversation(monkeypatch):
    def unavailable():
        raise OSError("storage unavailable")

    monkeypatch.setattr(runtime_module, "_current", None)
    monkeypatch.setattr(factory, "_SOCIETY_FACTORY_REF", [unavailable])
    await factory.prepare_society_context()
    assert society_agent_names() == ()


async def test_slow_team_start_is_shared_bounded_and_reaped_on_close(tmp_path, monkeypatch):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    started = 0

    async def slow_start():
        nonlocal started
        started += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "ensure_started", slow_start)
    assert not await runtime.prepare_context(timeout_s=0.01)
    assert not await runtime.prepare_context(timeout_s=0.01)
    task = runtime._context_start_task
    assert started == 1 and not task.done()
    await runtime.close()
    assert task.cancelled()


async def test_named_and_unnamed_matching_tasks_stay_with_team(tmp_path, monkeypatch):
    search = SimpleNamespace(name="search-web", description="Search the web.", risk_tier="safe")
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        brain_tools=lambda: {"search-web": search},
    )
    await runtime.ensure_started()
    try:
        await runtime.roster.create(name="Scout", focus=["core:search-web"])
        manager = BrainManager(
            config=JarvisConfig(),
            bus=EventBus(),
            tools={"spawn_worker": search},
        )
        manager._tool_executor = RecordingExecutor()
        monkeypatch.setattr(manager, "_heavy_worker_provider_viable", lambda: True)
        for text in (
            "Scout, research current suppliers in the background.",
            "Do a deep dive and research current hosting suppliers in the background.",
        ):
            assert runtime.pick_agent(text).name == "Scout"
            assert not llm_spawn_allowed(text)
            assert not manager._should_force_spawn(text)
        assert llm_spawn_allowed("Spawn a worker to research suppliers.")
    finally:
        await runtime.close()
