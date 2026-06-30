"""Integration-Test: BrainManager.switch() + Voice-Intent-Detection."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import BrainProviderSwitched
from jarvis.core.protocols import BrainDelta, BrainRequest
from tests.fixtures.brain.fake_brain import FakeBrain


def _patch_two_providers(
    manager: BrainManager,
    name_a: str,
    name_b: str,
) -> tuple[FakeBrain, FakeBrain]:
    a = FakeBrain(text_response="from A")
    b = FakeBrain(text_response="from B")
    # Klasse als Attr setzen (manager._registry._classes)
    manager._registry._loaded = True
    manager._registry._classes[name_a] = type(a)
    manager._registry._classes[name_b] = type(b)
    manager._providers[name_a] = a
    manager._providers[name_b] = b
    return a, b


@pytest.mark.asyncio
async def test_manager_switch_publishes_event():
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "prov-a"
    manager = BrainManager(config=config, bus=bus, tools={})
    _patch_two_providers(manager, "prov-a", "prov-b")

    events: list = []

    async def on_switch(e: BrainProviderSwitched):
        events.append(e)

    bus.subscribe(BrainProviderSwitched, on_switch)

    await manager.switch("prov-b")
    assert manager.active_provider == "prov-b"
    assert len(events) == 1
    assert events[0].from_provider == "prov-a"
    assert events[0].to_provider == "prov-b"


@pytest.mark.asyncio
async def test_manager_switch_is_idempotent():
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "prov-a"
    manager = BrainManager(config=config, bus=bus, tools={})
    _patch_two_providers(manager, "prov-a", "prov-b")

    events: list = []
    bus.subscribe(BrainProviderSwitched, lambda e: events.append(e))

    await manager.switch("prov-a")  # no-op
    assert len(events) == 0


@pytest.mark.asyncio
async def test_voice_switch_intent_detected():
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "claude-subscription"
    manager = BrainManager(config=config, bus=bus, tools={})
    _patch_two_providers(manager, "claude-subscription", "gemini")

    # Switch now routes through app_control.apply_provider_switch (credential /
    # catalog validation + 3-layer persist).  Mock it to simulate a clean switch
    # so the test stays unit-level (no real API key required).
    async def _fake_apply(tier, provider, *, cfg, persist=True):
        if tier == "brain":
            await manager.switch(provider)
        return {"ok": True, "new_provider": provider}

    with patch("jarvis.brain.app_control.apply_provider_switch", side_effect=_fake_apply):
        result = await manager.generate("Jarvis wechsel auf gemini bitte", use_history=False)

    assert manager.active_provider == "gemini"
    # Switch now returns a spoken confirmation phrase instead of the old silent "".
    assert result  # non-empty confirmation (e.g. "Erledigt — dein Haupt-Brain…")


@pytest.mark.asyncio
async def test_alias_resolution():
    # "local" was removed from PROVIDER_ALIASES.  Use "flash" → "gemini" which
    # is still a valid alias in the current table.
    bus = EventBus()
    config = JarvisConfig()
    config.brain.primary = "openai"
    manager = BrainManager(config=config, bus=bus, tools={})
    _patch_two_providers(manager, "openai", "gemini")

    await manager.switch("flash")
    assert manager.active_provider == "gemini"
