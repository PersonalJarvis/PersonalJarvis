"""Shared Screen Context runtime ownership and live config reloads."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import ConfigReloaded
from jarvis.screen_context.turn import get_service, reset_service
from jarvis.ui.web import screen_context_routes


@pytest.fixture(autouse=True)
def _clean_service(monkeypatch, tmp_path) -> None:
    for name in tuple(os.environ):
        if name.startswith("JARVIS__"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JARVIS_CONFIG", str(tmp_path / "missing.toml"))
    reset_service()
    yield
    reset_service()


def test_rest_and_conversation_share_one_service() -> None:
    bus = EventBus()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=bus)))

    conversational = get_service(bus=bus)
    rest = screen_context_routes._get_service(request)

    assert rest is conversational


@pytest.mark.asyncio
async def test_screen_context_config_reload_discards_cached_service() -> None:
    bus = EventBus()
    original = get_service(bus=bus)

    await bus.publish(
        ConfigReloaded(changed_keys=("screen_context.enabled",))
    )

    assert get_service(bus=bus) is not original


@pytest.mark.asyncio
async def test_unrelated_config_reload_keeps_service() -> None:
    bus = EventBus()
    original = get_service(bus=bus)

    await bus.publish(ConfigReloaded(changed_keys=("brain.reply_language",)))

    assert get_service(bus=bus) is original
