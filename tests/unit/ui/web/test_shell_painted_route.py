"""The shell-paint acknowledgement is valid before and after full-app handoff."""

from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


def test_full_app_accepts_shell_painted_acknowledgement() -> None:
    server = WebServer(JarvisConfig(), bus=EventBus())
    origin = "http://localhost:47821"

    with TestClient(server.app, base_url=origin) as client:
        response = client.post(
            "/api/ui/shell-painted",
            headers={"Origin": origin},
        )

    assert response.status_code == 204
    assert response.content == b""
