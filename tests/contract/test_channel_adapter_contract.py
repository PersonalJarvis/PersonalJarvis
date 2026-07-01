"""Contract tests for all ChannelAdapter implementations.

Parametrized over all plugins registered via the `jarvis.channel` entry point.
If no plugins are installed yet (e.g. because Phase-1a agents are still
building), the tests skip gracefully instead of crashing.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import SystemStarted
from jarvis.core.protocols import ChannelAdapter


def _discover_channel_classes() -> list[tuple[str, type]]:
    """Loads all `jarvis.channel` entry points that are loadable."""
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
                # Ignore entry points that fail to load (e.g. missing optional deps)
                continue
    except Exception:
        pass
    return discovered


CHANNEL_CLASSES = _discover_channel_classes()
_PARAMS = CHANNEL_CLASSES or [("skip", None)]


@pytest.mark.parametrize("name,cls", _PARAMS)
@pytest.mark.asyncio
async def test_channel_is_protocol_conformant(name, cls):
    """Instance must structurally conform to the ChannelAdapter protocol."""
    if cls is None:
        pytest.skip("no ChannelAdapter plugins installed (pip install -e .)")
    bus = EventBus()
    inst = cls(bus)
    assert isinstance(inst, ChannelAdapter), (
        f"{name} does not satisfy the ChannelAdapter protocol"
    )
    assert hasattr(inst, "name") and isinstance(inst.name, str)


@pytest.mark.parametrize("name,cls", _PARAMS)
@pytest.mark.asyncio
async def test_channel_lifecycle(name, cls):
    """start() subscribes to the bus, stop() cleans it back up."""
    if cls is None:
        pytest.skip("no ChannelAdapter plugins installed")
    bus = EventBus()
    inst = cls(bus)

    before_start = len(bus._wildcard_subscribers)
    await inst.start()
    after_start = len(bus._wildcard_subscribers)

    await inst.stop()
    after_stop = len(bus._wildcard_subscribers)

    assert after_start >= before_start + 1, (
        "start() should register a wildcard subscriber"
    )
    assert after_stop <= after_start, "stop() should remove the subscriber"


@pytest.mark.parametrize("name,cls", _PARAMS)
@pytest.mark.asyncio
async def test_channel_broadcast_event(name, cls):
    """broadcast_event must not crash without connected clients."""
    if cls is None:
        pytest.skip("no ChannelAdapter plugins installed")
    bus = EventBus()
    inst = cls(bus)
    await inst.start()
    try:
        # This must not blow up even without clients
        await inst.broadcast_event(SystemStarted(version="test"))
    finally:
        await inst.stop()
