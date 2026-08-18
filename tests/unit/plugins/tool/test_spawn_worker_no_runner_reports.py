"""AU-11 — a dispatched mission with nobody to run it must still reach the user.

The tool creates the mission, finds no Kontrollierer, and used to return after
a bare ``log.warning``. By then the user had already heard the spoken
acknowledgement, so the promise stood and the answer never came: the mission
sat PENDING until the next app start quietly marked it crash-recovered.

The fix publishes the SAME completion event a finished mission publishes, with
``success=False`` and a plain-language reason, so the speech pipeline speaks it.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import JarvisAgentBackgroundCompleted
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool
from jarvis.voice.action_phrases import action_phrase


class _FakeMissionManager:
    """Accepts the dispatch and hands back an id — like the real one."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def dispatch(
        self, *, prompt: str, language: str, source_actor: str
    ) -> str:
        self.prompts.append(prompt)
        return "mission_0001"


class _FakeAnnouncer:
    async def compose(self, **kwargs: Any) -> str:
        return "On it."


def _ctx(output_language: str) -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="",
        config={"output_language": output_language},
        memory_read=None,
    )


async def _drain() -> None:
    """Let the fire-and-forget dispatch task finish."""
    await asyncio.sleep(0.05)


async def test_missing_kontrollierer_publishes_a_failed_completion() -> None:
    bus = EventBus()
    seen: list[JarvisAgentBackgroundCompleted] = []
    bus.subscribe(JarvisAgentBackgroundCompleted, seen.append)

    manager = _FakeMissionManager()
    tool = SpawnWorkerTool(
        bus=bus, manager=manager, kontrollierer=None, announcer=_FakeAnnouncer()
    )

    result = await tool.execute(
        {"utterance": "Build me a small website.", "action": ""}, _ctx("en")
    )
    await _drain()

    # The dispatch itself still happens — the UI keeps its mission card.
    assert result.success is True
    assert manager.prompts, "the mission was never dispatched"

    assert len(seen) == 1, "the dead end was reported zero or twice"
    event = seen[0]
    assert event.success is False
    assert event.utterance == "Build me a small website."
    assert event.error == action_phrase("spawn_no_runner", "en")


async def test_the_reason_is_plain_language_in_the_turn_language() -> None:
    """No ids, no exception class names — and never German on an English turn."""
    bus = EventBus()
    seen: list[JarvisAgentBackgroundCompleted] = []
    bus.subscribe(JarvisAgentBackgroundCompleted, seen.append)

    for language in ("de", "en", "es"):
        seen.clear()
        tool = SpawnWorkerTool(
            bus=bus,
            manager=_FakeMissionManager(),
            kontrollierer=None,
            announcer=_FakeAnnouncer(),
        )
        await tool.execute(
            {"utterance": "Do the thing.", "action": ""}, _ctx(language)
        )
        await _drain()

        assert len(seen) == 1
        error = seen[0].error or ""
        assert error == action_phrase("spawn_no_runner", language)
        assert "mission_" not in error
        assert "Error" not in error and "Exception" not in error
        # The pipeline caps the spoken reason at 80 characters.
        assert len(error) <= 80
