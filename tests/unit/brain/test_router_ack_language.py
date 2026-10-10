"""The router's tool ack speaks the turn's resolved output language.

The ack used a private de/en heuristic with a German default, so a Portuguese
or Spanish turn heard a German tool acknowledgement. It now resolves through
``RouterBrain._output_locale`` (the one output-language resolver).
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import RouterBrain
from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested


@pytest.mark.asyncio
async def test_tool_ack_uses_the_resolved_output_language(monkeypatch) -> None:
    router = RouterBrain.__new__(RouterBrain)
    bus = EventBus()
    router._bus = bus
    captured: list[AnnouncementRequested] = []

    async def _on(event: AnnouncementRequested) -> None:
        captured.append(event)

    bus.subscribe(AnnouncementRequested, _on)
    monkeypatch.setattr(RouterBrain, "_output_locale", lambda self, utterance: "pt")

    emit = router._build_ack_emitter("procura o tempo em Lisboa")  # i18n-allow: PT fixture
    assert emit is not None
    await emit("search_web", {"query": "tempo Lisboa"})

    assert captured, "the search tool must be acknowledged"
    assert captured[0].language == "pt"
