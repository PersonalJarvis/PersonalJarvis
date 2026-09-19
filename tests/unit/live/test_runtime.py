"""Browser media startup reports a safe failure and releases its watcher."""

import asyncio
import logging

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import BrowserVoiceRequested
from jarvis.live import runtime


@pytest.mark.asyncio
async def test_unattached_media_reports_timeout_and_releases_watcher(monkeypatch, caplog):
    monkeypatch.setattr(runtime, "_active", {})
    monkeypatch.setattr(runtime, "_watchers", set())
    bus = EventBus()
    actions = []
    bus.subscribe(BrowserVoiceRequested, lambda event: actions.append(event.action))

    with caplog.at_level(logging.WARNING, logger="jarvis.live.runtime"):
        result = await runtime.run_browser_call(bus, asyncio.Event(), timeout_s=0.01)

    assert result == "error"
    assert actions == ["start"]
    assert not runtime._watchers
    assert caplog.messages == [
        "Browser voice media did not attach before its startup deadline"
    ]
    assert caplog.records[0].exc_info is None


@pytest.mark.asyncio
async def test_user_hangup_during_startup_is_not_reported_as_timeout(monkeypatch, caplog):
    monkeypatch.setattr(runtime, "_active", {})
    monkeypatch.setattr(runtime, "_watchers", set())
    bus = EventBus()
    actions = []
    hangup = asyncio.Event()
    hangup.set()
    bus.subscribe(BrowserVoiceRequested, lambda event: actions.append(event.action))

    with caplog.at_level(logging.WARNING, logger="jarvis.live.runtime"):
        result = await runtime.run_browser_call(bus, hangup, timeout_s=0.01)

    assert result == "hotkey"
    assert actions == ["start", "stop"]
    assert not runtime._watchers
    assert not caplog.records
