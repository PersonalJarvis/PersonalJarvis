"""Integration: BrainManager.generate() short-circuit fuer OpenClaw-Commands.

AD-12 + AP-OC5: Status-/Cancel-Phrasen DUERFEN keinen Force-Spawn ausloesen,
auch wenn ihr Verb in der ``spawn_verbs``-Allowlist haengt ("brich" bzw.
ein Action-Verb-Kompositum). Der Test stellt sicher dass der Pattern-
Matcher VOR der Force-Spawn-Heuristik greift, wenn Handler verdrahtet
sind, und dass ohne Handler der normale Pfad weiterlaeuft.
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
    """Status-Phrase ruft den Handler statt einen Spawn anzustossen."""
    manager = _bare_manager()
    calls: list[str | None] = []

    async def status_fn(mission_id: str | None) -> str:
        calls.append(mission_id)
        return "Eine Mission laeuft seit drei Minuten."

    async def cancel_fn(mission_id: str | None) -> str:
        raise AssertionError("cancel_fn darf bei Status-Intent NICHT laufen")

    manager.set_mission_command_handlers(
        status_fn=status_fn,
        cancel_fn=cancel_fn,
    )

    result = await manager.generate("Laeuft das noch?", use_history=False)

    assert result == "Eine Mission laeuft seit drei Minuten."
    assert calls == [None]  # keine Mission-ID im Text


@pytest.mark.asyncio
async def test_cancel_short_circuits_to_handler() -> None:
    """Cancel-Phrase ruft den Cancel-Handler statt einen Spawn anzustossen."""
    manager = _bare_manager()
    calls: list[str | None] = []

    async def status_fn(mission_id: str | None) -> str:
        raise AssertionError("status_fn darf bei Cancel-Intent NICHT laufen")

    async def cancel_fn(mission_id: str | None) -> str:
        calls.append(mission_id)
        return "OpenClaw-Mission abgebrochen."

    manager.set_mission_command_handlers(
        status_fn=status_fn,
        cancel_fn=cancel_fn,
    )

    result = await manager.generate("Brich die Mission ab", use_history=False)

    assert result == "OpenClaw-Mission abgebrochen."
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
    """Die kurze Status-Antwort landet im History-Buffer (sonst kein
    Conversation-Memory beim naechsten Turn)."""
    manager = _bare_manager()

    async def status_fn(mission_id: str | None) -> str:
        return "Laeuft seit fuenf Minuten."

    manager.set_mission_command_handlers(
        status_fn=status_fn, cancel_fn=None,
    )

    await manager.generate("Wie weit bist du?", use_history=True)

    assert len(manager._history) == 2
    assert manager._history[0].role == "user"
    assert manager._history[0].content == "Wie weit bist du?"
    assert manager._history[1].role == "assistant"
    assert manager._history[1].content == "Laeuft seit fuenf Minuten."


@pytest.mark.asyncio
async def test_no_handler_falls_through(monkeypatch) -> None:
    """Wenn keine Handler injiziert sind, greift der normale Spawn-Pfad
    (kein Crash, kein leerer Return)."""
    manager = _bare_manager()
    spawn_called: list[str] = []

    async def fake_force_spawn(user_text: str, *, trace_id=None) -> str | None:
        spawn_called.append(user_text)
        return "spawned-result"

    # Keine Handler verdrahtet — Pattern matcht zwar, fall through.
    monkeypatch.setattr(manager, "_force_spawn_worker", fake_force_spawn)

    result = await manager.generate("Brich die Mission ab", use_history=False)

    assert result == "spawned-result"
    assert spawn_called == ["Brich die Mission ab"]


@pytest.mark.asyncio
async def test_smalltalk_does_not_trigger_handler() -> None:
    """Smalltalk wie 'Wie geht's?' darf den Status-Handler NICHT triggern.
    Sonst wuerde jede Begruessung einen MissionManager-Read ausloesen."""
    manager = _bare_manager()
    triggered: list[str] = []

    async def status_fn(mission_id: str | None) -> str:
        triggered.append("status")
        return "should-not-happen"

    async def cancel_fn(mission_id: str | None) -> str:
        triggered.append("cancel")
        return "should-not-happen"

    async def fake_force_spawn(user_text: str, *, trace_id=None) -> str | None:
        return None  # Smalltalk → keine Spawn

    manager.set_mission_command_handlers(
        status_fn=status_fn, cancel_fn=cancel_fn,
    )
    # Force-Spawn-Pfad mocken; Dispatcher wird gar nicht erst gerufen weil
    # der nachfolgende Code-Pfad einen leeren Provider-Chain erkennt und
    # zurueckkehrt.
    manager._force_spawn_worker = fake_force_spawn  # type: ignore[method-assign]

    await manager.generate("Hallo Jarvis", use_history=False)

    assert triggered == [], "Status-/Cancel-Handler wurde bei Smalltalk gerufen"


@pytest.mark.asyncio
async def test_status_handler_falls_through_when_only_cancel_set() -> None:
    """Wenn der User nach Status fragt, aber nur cancel_fn gesetzt ist,
    fallen wir nicht auf cancel zurueck — wir delegieren an den normalen
    Pfad. (Sonst wuerden falsche Cancellations passieren.)"""
    manager = _bare_manager()
    cancel_called: list[str | None] = []

    async def cancel_fn(mission_id: str | None) -> str:
        cancel_called.append(mission_id)
        return "cancelled"

    async def fake_force_spawn(user_text: str, *, trace_id=None) -> str | None:
        return "fell-through"

    manager.set_mission_command_handlers(
        status_fn=None, cancel_fn=cancel_fn,
    )
    manager._force_spawn_worker = fake_force_spawn  # type: ignore[method-assign]

    result = await manager.generate("Status?", use_history=False)

    assert result == "fell-through"
    assert cancel_called == []
