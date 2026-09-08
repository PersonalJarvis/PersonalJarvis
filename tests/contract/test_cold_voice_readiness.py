"""The REST/WS readiness promises match the desktop capability on every OS."""

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import VoiceBootStatus
from jarvis.ui.web.schema import event_to_ws_envelope
from jarvis.ui.web.server import WebServer


@pytest.mark.asyncio
async def test_ready_transport_matches_actual_voice_capability(monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE", raising=False)
    bus = EventBus()
    server = WebServer(JarvisConfig(), bus=bus)
    client = TestClient(server.app)
    for detail, ready, expected in [
        ("warmup_start", False, False),
        ("watchdog_timeout", True, False),
        ("voice_unavailable", True, False),
        ("listening", True, True),
        ("warmup_start", False, False),
    ]:
        event = VoiceBootStatus(ready=ready, detail=detail)
        await bus.publish(event)
        assert event_to_ws_envelope(event)["payload"]["ready"] is expected
        assert client.get("/api/voice/status").json()["ready"] is expected
        assert event.voice_usable is expected
