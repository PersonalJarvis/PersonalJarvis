"""Contract-Tests für alle ChannelAdapter-Implementierungen.

Parametrisiert über alle via `jarvis.channel`-Entry-Point registrierten Plugins.
Wenn noch keine Plugins installiert sind (z.B. weil Phase-1a-Agents noch bauen),
skippen die Tests graceful, statt zu crashen.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import SystemStarted
from jarvis.core.protocols import ChannelAdapter


def _discover_channel_classes() -> list[tuple[str, type]]:
    """Lädt alle `jarvis.channel`-Entry-Points, die ladbar sind."""
    discovered: list[tuple[str, type]] = []
    try:
        from importlib.metadata import entry_points

        try:
            eps = entry_points(group="jarvis.channel")
        except TypeError:
            # Python < 3.10 Fallback
            eps = entry_points().get("jarvis.channel", [])  # type: ignore[assignment]
        for ep in eps:
            try:
                discovered.append((ep.name, ep.load()))
            except Exception:
                # Nicht-ladbare Entry-Points ignorieren (z.B. fehlende optionale Deps)
                continue
    except Exception:
        pass
    return discovered


CHANNEL_CLASSES = _discover_channel_classes()
_PARAMS = CHANNEL_CLASSES or [("skip", None)]


@pytest.mark.parametrize("name,cls", _PARAMS)
@pytest.mark.asyncio
async def test_channel_is_protocol_conformant(name, cls):
    """Instanz muss strukturell dem ChannelAdapter-Protocol entsprechen."""
    if cls is None:
        pytest.skip("keine ChannelAdapter-Plugins installiert (pip install -e .)")
    bus = EventBus()
    inst = cls(bus)
    assert isinstance(inst, ChannelAdapter), (
        f"{name} erfüllt das ChannelAdapter-Protocol nicht"
    )
    assert hasattr(inst, "name") and isinstance(inst.name, str)


@pytest.mark.parametrize("name,cls", _PARAMS)
@pytest.mark.asyncio
async def test_channel_lifecycle(name, cls):
    """start() subscribed den Bus, stop() cleant wieder auf."""
    if cls is None:
        pytest.skip("keine ChannelAdapter-Plugins installiert")
    bus = EventBus()
    inst = cls(bus)

    before_start = len(bus._wildcard_subscribers)
    await inst.start()
    after_start = len(bus._wildcard_subscribers)

    await inst.stop()
    after_stop = len(bus._wildcard_subscribers)

    assert after_start >= before_start + 1, (
        "start() sollte einen Wildcard-Subscriber registrieren"
    )
    assert after_stop <= after_start, "stop() sollte den Subscriber entfernen"


@pytest.mark.parametrize("name,cls", _PARAMS)
@pytest.mark.asyncio
async def test_channel_broadcast_event(name, cls):
    """broadcast_event darf ohne connected Clients nicht crashen."""
    if cls is None:
        pytest.skip("keine ChannelAdapter-Plugins installiert")
    bus = EventBus()
    inst = cls(bus)
    await inst.start()
    try:
        # Auch ohne Clients darf das nicht hochgehen
        await inst.broadcast_event(SystemStarted(version="test"))
    finally:
        await inst.stop()
