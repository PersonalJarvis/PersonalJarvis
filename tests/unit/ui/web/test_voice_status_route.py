"""GET /api/voice/status reflects the live voice boot state.

WebSocket events are not persistent, so a browser tab that connects *after* the
voice pipeline finished warming up would never see the one-shot
``VoiceBootStatus(ready=True)`` event. This REST endpoint lets a late-connecting
frontend read the current state on mount. The server subscribes to
``VoiceBootStatus`` on the bus and stores ``self._voice_ready`` on the server
instance (default ``False`` at startup) — deliberately not on ``app.state``,
whose ASGI lifecycle could outrace the bus subscriber on shutdown; the endpoint
returns that stored value.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import VoiceBootStatus
from jarvis.ui.web.server import WebServer


def test_voice_status_defaults_to_false(monkeypatch) -> None:
    # Deterministic: a JARVIS_VOICE=0 in the ambient shell would otherwise seed
    # ready=True (see test_voice_status_ready_when_voice_disabled below).
    monkeypatch.delenv("JARVIS_VOICE", raising=False)
    srv = WebServer(JarvisConfig(), bus=EventBus())
    assert srv._voice_ready is False
    with TestClient(srv.app) as client:
        resp = client.get("/api/voice/status")
    assert resp.status_code == 200
    assert resp.json() == {"ready": False}


@pytest.mark.parametrize("value", ["0", "off", "false", "OFF"])
def test_voice_status_ready_when_voice_disabled(monkeypatch, value: str) -> None:
    """With the local voice stack off (JARVIS_VOICE=0 — headless / VPS /
    browser-mic-only) nothing ever warms up, so the server seeds ready=True.
    Otherwise the frontend "starting up" banner would hang forever even though
    the user can already type and use browser voice."""
    monkeypatch.setenv("JARVIS_VOICE", value)
    srv = WebServer(JarvisConfig(), bus=EventBus())
    assert srv._voice_ready is True
    with TestClient(srv.app) as client:
        resp = client.get("/api/voice/status")
    assert resp.json() == {"ready": True}


@pytest.mark.asyncio
async def test_voice_status_flips_true_after_boot_status_event() -> None:
    bus = EventBus()
    srv = WebServer(JarvisConfig(), bus=bus)

    await bus.publish(VoiceBootStatus(ready=True, detail="phase-a-done"))

    assert srv._voice_ready is True
    with TestClient(srv.app) as client:
        resp = client.get("/api/voice/status")
    assert resp.json() == {"ready": True}


@pytest.mark.asyncio
async def test_voice_status_tracks_latest_state() -> None:
    """A later ready=False (e.g. a restart) is reflected too — the endpoint is a
    live mirror, not a latch."""
    bus = EventBus()
    srv = WebServer(JarvisConfig(), bus=bus)

    await bus.publish(VoiceBootStatus(ready=True))
    assert srv._voice_ready is True
    await bus.publish(VoiceBootStatus(ready=False))
    assert srv._voice_ready is False
