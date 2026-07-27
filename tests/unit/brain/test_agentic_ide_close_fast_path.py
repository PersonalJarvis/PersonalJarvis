from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _Bus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _Registry:
    def __init__(self) -> None:
        self.session = SimpleNamespace(
            id="workspace",
            folder="/project",
            terminals=[
                SimpleNamespace(name="Cody", agent="codex"),
                SimpleNamespace(name="Clara", agent="claude"),
                SimpleNamespace(name="Cole", agent="codex"),
            ],
        )

    async def close_terminal(self, name: str) -> object:
        term = next(term for term in self.session.terminals if term.name == name)
        self.session.terminals.remove(term)
        return term


@pytest.mark.asyncio
async def test_close_all_codex_is_exact_and_not_model_dependent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _Registry()
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    bus = _Bus()
    manager._bus = bus  # type: ignore[assignment]
    manager._reply_language = "en"

    reply = await manager._run_agentic_ide_close_fast_path(
        "Please close all Codex terminals"
    )

    assert reply == "Closed 2 terminals: Cody and Cole."
    assert [term.name for term in registry.session.terminals] == ["Clara"]
    assert len(bus.events) == 1
    assert type(bus.events[0]).__name__ == "AgenticIdeTerminalsClosed"
    assert bus.events[0].names == ("Cody", "Cole")


@pytest.mark.asyncio
async def test_non_close_turn_stands_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _Registry()
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})

    assert await manager._run_agentic_ide_close_fast_path("Review Codex output") is None
    assert len(registry.session.terminals) == 3
