"""Regression test for Jarvis-Agent bootstrap-order inversion (fix 2026-05-10).

Reproduces the production scenario where the BrainManager is built BEFORE
the Mission stack is bootstrapped. Without the lazy-resolver pattern the
``spawn_worker`` tool would be permanently absent from the Brain's tool
dict, the Force-Spawn heuristic would always return False, and voice input
would never reach the Jarvis-Agent (worker dispatch).

The fix (AD-OC1): register the tool unconditionally, resolve the
MissionManager via a closure at execute-time, and let
``set_mission_manager`` make a post-hoc-bootstrapped manager visible to
the already-built Brain.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from jarvis.brain.factory import (
    _KONTROLLIERER_REF,
    _MISSION_MANAGER_REF,
    _WORKER_BOOTSTRAP_FAILED,
    build_default_brain,
    is_worker_bootstrap_failed,
    set_kontrollierer,
    set_mission_manager,
    set_worker_bootstrap_failed,
)
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext


@pytest.fixture(autouse=True)
def _clean_singleton():
    """Each test starts with a clean ``_MISSION_MANAGER_REF`` + Kontrollierer
    + bootstrap-failed sentinel."""
    saved_mgr = list(_MISSION_MANAGER_REF)
    saved_kon = list(_KONTROLLIERER_REF)
    saved_flag = _WORKER_BOOTSTRAP_FAILED[0]
    _MISSION_MANAGER_REF.clear()
    _KONTROLLIERER_REF.clear()
    _WORKER_BOOTSTRAP_FAILED[0] = False
    os.environ.pop("JARVIS_BRAIN", None)
    yield
    _MISSION_MANAGER_REF.clear()
    _MISSION_MANAGER_REF.extend(saved_mgr)
    _KONTROLLIERER_REF.clear()
    _KONTROLLIERER_REF.extend(saved_kon)
    _WORKER_BOOTSTRAP_FAILED[0] = saved_flag


def _make_ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="schreibe mir ein hello world programm",
        config={},
        memory_read=None,
    )


def test_tool_registered_without_mission_manager() -> None:
    """spawn_worker must be in the tool dict even when no MissionManager."""
    brain = build_default_brain(tier="router")
    tools = getattr(brain, "_tools", {})
    assert "spawn_worker" in tools, (
        "Lazy-resolver contract: tool must be registered unconditionally"
    )


@pytest.mark.asyncio
async def test_execute_returns_honest_failure_when_manager_none() -> None:
    """Calling execute() with no manager bootstrapped yields a clear error."""
    brain = build_default_brain(tier="router")
    tool = brain._tools["spawn_worker"]

    result = await tool.execute(
        {"utterance": "hello", "action": "test"},
        _make_ctx(),
    )
    assert result.success is False
    assert result.error is not None
    assert "Der Hintergrund-Worker ist noch nicht bereit" in result.error  # i18n-allow (matches production error text)


@pytest.mark.asyncio
async def test_post_hoc_set_mission_manager_makes_tool_functional() -> None:
    """The actual bug-reproduction: build Brain first, set manager later."""
    # Step 1 — Brain is built BEFORE any MissionManager exists.
    brain = build_default_brain(tier="router")
    tool = brain._tools["spawn_worker"]

    # Step 2 — Verify the early call fails honestly.
    early = await tool.execute({"utterance": "x", "action": "y"}, _make_ctx())
    assert early.success is False

    # Step 3 — Bootstrap path completes and registers the manager via the
    # singleton setter (mirrors server.py:1260).
    fake_manager = MagicMock()
    fake_manager.dispatch = AsyncMock(return_value=None)
    set_mission_manager(fake_manager)

    # Step 4 — Same tool instance now must dispatch through the manager.
    result = await tool.execute(
        {"utterance": "build hello world", "action": "build", "target": ""},
        _make_ctx(),
    )
    assert result.success is True
    # The dispatch happens in a background task — give the loop one tick.
    import asyncio
    await asyncio.sleep(0.05)
    assert fake_manager.dispatch.await_count == 1
    call = fake_manager.dispatch.await_args
    # main's _build_mission_prompt enriches the prompt with the interpreted
    # action ("build"), so assert containment rather than an exact raw match.
    assert "build hello world" in call.kwargs["prompt"]
    assert call.kwargs["language"] == "de"
    assert call.kwargs["source_actor"] == "hauptjarvis"


def test_force_spawn_heuristic_triggers_when_tool_present() -> None:
    """_should_force_spawn must trigger now that the tool is always there.

    Phrases that overlap with the PC-control vocabulary (``schreibe``, ``tippe``)
    are intentionally excluded — they route to dispatch_to_harness/Computer-Use
    via the bypass at manager.py:909, which is correct behaviour and unrelated
    to the lazy-resolver fix.
    """
    brain = build_default_brain(tier="router")
    # These phrases rely on the legacy verb heuristic, which only fires in
    # permissive mode (the shipped default is "strict", which requires explicit
    # spawn phrases). This test targets the lazy-resolver fix — that the tool is
    # present so the heuristic CAN trigger — so it forces permissive mode.
    brain._config.brain.routing.force_spawn_mode = "permissive"
    assert brain._should_force_spawn("baue mir eine landing page") is True
    assert brain._should_force_spawn("erstelle einen neuen branch") is True
    assert brain._should_force_spawn("implementiere den login flow") is True
    assert brain._should_force_spawn("hallo jarvis") is False


def test_init_requires_manager_or_resolver() -> None:
    """SpawnWorkerTool refuses to construct without any manager source."""
    from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool

    bus = EventBus()
    with pytest.raises(ValueError, match="manager.*manager_resolver"):
        SpawnWorkerTool(bus=bus)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_eager_manager_still_works() -> None:
    """Backwards compat: passing manager directly (test path) still works."""
    from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool

    bus = EventBus()
    fake_manager = MagicMock()
    fake_manager.dispatch = AsyncMock(return_value=None)

    tool = SpawnWorkerTool(bus=bus, manager=fake_manager)
    result = await tool.execute(
        {"utterance": "test", "action": "test"}, _make_ctx()
    )
    assert result.success is True
    import asyncio
    await asyncio.sleep(0.05)
    assert fake_manager.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_voice_path_triggers_kontrollierer_run_mission() -> None:
    """Regression: BUG-016 — voice path must trigger run_mission, not only dispatch.

    Before the fix the spawn_worker tool only called manager.dispatch(),
    leaving the mission in PENDING state forever. The user heard nothing
    because no MissionApproved/MissionFailed was ever published.

    After the fix the tool also calls kontrollierer.run_mission(mission_id)
    (mirroring the REST path in missions_routes.py:249-252).
    """
    brain = build_default_brain(tier="router")
    tool = brain._tools["spawn_worker"]

    fake_manager = MagicMock()
    fake_manager.dispatch = AsyncMock(return_value="mission-abc-123")

    fake_kontrollierer = MagicMock()
    fake_kontrollierer.run_mission = AsyncMock(return_value=None)

    set_mission_manager(fake_manager)
    set_kontrollierer(fake_kontrollierer)

    result = await tool.execute(
        {"utterance": "baue mir eine landing page", "action": "build"},
        _make_ctx(),
    )
    assert result.success is True

    import asyncio
    # Background dispatch needs a tick to complete.
    await asyncio.sleep(0.05)

    assert fake_manager.dispatch.await_count == 1
    assert fake_kontrollierer.run_mission.await_count == 1
    assert fake_kontrollierer.run_mission.await_args.args[0] == "mission-abc-123"


@pytest.mark.asyncio
async def test_voice_path_no_kontrollierer_logs_warning() -> None:
    """When the Kontrollierer isn't bootstrapped yet, dispatch still happens.

    The mission lands in PENDING and will be picked up by the next
    app-start recovery sweep. The tool must not crash, must report
    success (the dispatch itself succeeded), and must not call
    run_mission on a None resolver.
    """
    brain = build_default_brain(tier="router")
    tool = brain._tools["spawn_worker"]

    fake_manager = MagicMock()
    fake_manager.dispatch = AsyncMock(return_value="mission-xyz")
    set_mission_manager(fake_manager)
    # Kontrollierer intentionally NOT set.

    result = await tool.execute(
        {"utterance": "baue eine app", "action": "build"}, _make_ctx()
    )
    assert result.success is True

    import asyncio
    await asyncio.sleep(0.05)
    assert fake_manager.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_voice_path_kontrollierer_crash_publishes_completed_event() -> None:
    """If run_mission crashes, the tool must publish a failure event.

    The Voice-Listener subscribes to JarvisAgentBackgroundCompleted with
    success=False to give the user spoken failure feedback (e.g.
    "the mission crashed", localized). Without this publish the user would just hear silence.
    """
    from jarvis.core.events import JarvisAgentBackgroundCompleted
    from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool

    bus = EventBus()
    received: list[JarvisAgentBackgroundCompleted] = []

    def _capture(env: JarvisAgentBackgroundCompleted) -> None:
        received.append(env)

    bus.subscribe(JarvisAgentBackgroundCompleted, _capture)

    fake_manager = MagicMock()
    fake_manager.dispatch = AsyncMock(return_value="mid")

    fake_kontrollierer = MagicMock()
    fake_kontrollierer.run_mission = AsyncMock(side_effect=RuntimeError("boom"))

    tool = SpawnWorkerTool(
        bus=bus,
        manager=fake_manager,
        kontrollierer=fake_kontrollierer,
    )
    result = await tool.execute(
        {"utterance": "x", "action": "y"}, _make_ctx()
    )
    assert result.success is True

    import asyncio
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].success is False
    assert "RuntimeError" in (received[0].error or "")


# --- Phase 2A: bootstrap-failed sentinel (forensic report 2026-05-14) -------


def test_set_worker_bootstrap_failed_round_trips() -> None:
    """The factory-level singleton must round-trip through its setter/getter
    so server.py can mark a failed bootstrap and the spawn_worker tool can
    read that signal at execute-time."""
    assert is_worker_bootstrap_failed() is False
    set_worker_bootstrap_failed(True)
    assert is_worker_bootstrap_failed() is True
    set_worker_bootstrap_failed(False)
    assert is_worker_bootstrap_failed() is False


@pytest.mark.asyncio
async def test_execute_short_circuits_on_bootstrap_failed() -> None:
    """When the bootstrap-failed sentinel is set, spawn_worker must
    return the permanent-failure ack ("konnte nicht initialisiert werden")  # i18n-allow (matches production error text)
    INSTEAD of the transient "noch nicht bereit" message. The user is  # i18n-allow (matches production error text)
    otherwise told to wait for something that will never happen."""
    brain = build_default_brain(tier="router")
    tool = brain._tools["spawn_worker"]

    set_worker_bootstrap_failed(True)

    result = await tool.execute(
        {"utterance": "baue mir eine app", "action": "build"}, _make_ctx()
    )
    assert result.success is False
    assert result.error is not None
    assert "konnte nicht initialisiert werden" in result.error  # i18n-allow (matches production error text)
    # Make sure we didn't fall through to the transient-failure branch.
    assert "noch nicht bereit" not in result.error  # i18n-allow (matches production error text)
