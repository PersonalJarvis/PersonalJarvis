"""Contract-Tests fuer alle ChannelAdapter-Plugins (Phase 1a).

Parametrisiert ueber ``entry_points(group="jarvis.channel")`` — jeder
registrierte Adapter muss das strukturelle Protocol erfuellen und die
Start/Stop-Bus-Subscription korrekt managen.
"""
from __future__ import annotations

import sys
from importlib.metadata import entry_points

import pytest

from jarvis.channels import ChannelAdapter
from jarvis.core.bus import EventBus


def _discover_channel_eps():
    eps = entry_points()
    if sys.version_info >= (3, 10):
        return list(eps.select(group="jarvis.channel"))
    return list(eps.get("jarvis.channel", []))  # type: ignore[attr-defined]


CHANNEL_EPS = _discover_channel_eps()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.mark.skipif(not CHANNEL_EPS, reason="no jarvis.channel entry points registered")
def test_web_channel_is_discovered() -> None:
    names = {ep.name for ep in CHANNEL_EPS}
    assert "web" in names


@pytest.mark.parametrize("ep", CHANNEL_EPS, ids=[ep.name for ep in CHANNEL_EPS])
def test_channel_adapter_protocol_conformance(ep, bus: EventBus) -> None:
    cls = ep.load()
    instance = cls(bus)
    assert isinstance(instance, ChannelAdapter), (
        f"{ep.name} does not satisfy ChannelAdapter protocol"
    )
    assert isinstance(instance.name, str) and instance.name


@pytest.mark.asyncio
@pytest.mark.parametrize("ep", CHANNEL_EPS, ids=[ep.name for ep in CHANNEL_EPS])
async def test_channel_start_subscribes_and_stop_unsubscribes(ep, bus: EventBus) -> None:
    cls = ep.load()
    instance = cls(bus)

    wildcard = bus._wildcard_subscribers  # noqa: SLF001 — intentional introspection
    before = len(wildcard)

    await instance.start()
    assert len(wildcard) == before + 1, "start() must attach a wildcard subscriber"

    await instance.stop()
    assert len(wildcard) == before, "stop() must detach the wildcard subscriber"
