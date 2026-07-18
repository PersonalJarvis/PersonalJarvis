"""Integration: BrainManager.generate() short-circuit for Jarvis-Agent commands.

AD-12 + AP-OC5: status/cancel phrases must NOT trigger a force-spawn, even
when their verb hangs off the ``spawn_verbs`` allowlist ("brich" i.e. an
action-verb compound). The test ensures the pattern matcher takes effect
BEFORE the force-spawn heuristic when handlers are wired, and that the
normal path continues when no handler is set.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def _bare_manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(config=cfg, bus=EventBus(), tools={})


@pytest.mark.asyncio
async def test_status_short_circuits_to_handler() -> None:
    """A status phrase calls the handler instead of triggering a spawn."""
    manager = _bare_manager()
    calls: list[str | None] = []

    async def status_fn(mission_id: str | None) -> str:
        calls.append(mission_id)
        return "Eine Mission laeuft seit drei Minuten."  # i18n-allow

    async def cancel_fn(mission_id: str | None) -> str:
        raise AssertionError("cancel_fn must NOT run on a status intent")

    manager.set_mission_command_handlers(
        status_fn=status_fn,
        cancel_fn=cancel_fn,
    )

    result = await manager.generate("Laeuft das noch?", use_history=False)  # i18n-allow

    assert result == "Eine Mission laeuft seit drei Minuten."  # i18n-allow
    assert calls == [None]  # no mission ID in the text


@pytest.mark.asyncio
async def test_cancel_short_circuits_to_handler() -> None:
    """A cancel phrase calls the cancel handler instead of triggering a spawn."""
    manager = _bare_manager()
    calls: list[str | None] = []

    async def status_fn(mission_id: str | None) -> str:
        raise AssertionError("status_fn must NOT run on a cancel intent")

    async def cancel_fn(mission_id: str | None) -> str:
        calls.append(mission_id)
        return "Jarvis-Agent-Mission abgebrochen."

    manager.set_mission_command_handlers(
        status_fn=status_fn,
        cancel_fn=cancel_fn,
    )

    result = await manager.generate("Brich die Mission ab", use_history=False)

    assert result == "Jarvis-Agent-Mission abgebrochen."
    assert calls == [None]


@pytest.mark.asyncio
async def test_mission_id_propagated_to_handler() -> None:
    manager = _bare_manager()
    captured: list[str | None] = []

    async def status_fn(mission_id: str | None) -> str:
        captured.append(mission_id)
        return "ok"

    manager.set_mission_command_handlers(
        status_fn=status_fn,
        cancel_fn=None,
    )

    await manager.generate("Status der Mission build-foo", use_history=False)

    assert captured == ["build-foo"]


@pytest.mark.asyncio
async def test_history_appended_for_status_response() -> None:
    """The short status answer lands in the history buffer (otherwise no
    conversation memory on the next turn)."""
    manager = _bare_manager()

    async def status_fn(mission_id: str | None) -> str:
        return "Laeuft seit fuenf Minuten."  # i18n-allow

    manager.set_mission_command_handlers(
        status_fn=status_fn, cancel_fn=None,
    )

    await manager.generate("Wie weit bist du?", use_history=True)

    assert len(manager._history) == 2
    assert manager._history[0].role == "user"
    assert manager._history[0].content == "Wie weit bist du?"
    assert manager._history[1].role == "assistant"
    assert manager._history[1].content == "Laeuft seit fuenf Minuten."  # i18n-allow


@pytest.mark.asyncio
async def test_no_handler_falls_through(monkeypatch) -> None:
    """When no handlers are injected, the normal spawn path takes over
    (no crash, no empty return)."""
    manager = _bare_manager()
    spawn_called: list[str] = []

    async def fake_force_spawn(
        user_text: str, *, trace_id=None, source_layer=None
    ) -> str | None:
        spawn_called.append(user_text)
        return "spawned-result"

    # No handlers wired — the pattern does match, but falls through.
    monkeypatch.setattr(manager, "_force_spawn_worker", fake_force_spawn)

    result = await manager.generate("Brich die Mission ab", use_history=False)

    assert result == "spawned-result"
    assert spawn_called == ["Brich die Mission ab"]


@pytest.mark.asyncio
async def test_smalltalk_does_not_trigger_handler() -> None:
    """Smalltalk like 'Wie geht's?' must NOT trigger the status handler.
    Otherwise every greeting would trigger a MissionManager read."""
    manager = _bare_manager()
    triggered: list[str] = []

    async def status_fn(mission_id: str | None) -> str:
        triggered.append("status")
        return "should-not-happen"

    async def cancel_fn(mission_id: str | None) -> str:
        triggered.append("cancel")
        return "should-not-happen"

    async def fake_force_spawn(
        user_text: str, *, trace_id=None, source_layer=None
    ) -> str | None:
        return None  # smalltalk → no spawn

    manager.set_mission_command_handlers(
        status_fn=status_fn, cancel_fn=cancel_fn,
    )
    # Mock the force-spawn path; the dispatcher never even gets called
    # because the subsequent code path detects an empty provider chain
    # and returns.
    manager._force_spawn_worker = fake_force_spawn  # type: ignore[method-assign]

    await manager.generate("Hallo Jarvis", use_history=False)

    assert triggered == [], "status/cancel handler was called on smalltalk"


@pytest.mark.asyncio
async def test_status_handler_falls_through_when_only_cancel_set() -> None:
    """When the user asks for status but only cancel_fn is set, we do not
    fall back to cancel — we delegate to the normal path. (Otherwise
    false cancellations would happen.)"""
    manager = _bare_manager()
    cancel_called: list[str | None] = []

    async def cancel_fn(mission_id: str | None) -> str:
        cancel_called.append(mission_id)
        return "cancelled"

    async def fake_force_spawn(
        user_text: str, *, trace_id=None, source_layer=None
    ) -> str | None:
        return "fell-through"

    manager.set_mission_command_handlers(
        status_fn=None, cancel_fn=cancel_fn,
    )
    manager._force_spawn_worker = fake_force_spawn  # type: ignore[method-assign]

    result = await manager.generate("Status?", use_history=False)

    assert result == "fell-through"
    assert cancel_called == []
