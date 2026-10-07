"""Status codes at the edge of the production SPA catch-all.

The production server registers ``GET /{full_path:path}`` so client-side
routes load the SPA. That route used to partially match every path, so an
unknown ``POST /api/...`` got 405 with ``Allow: GET`` instead of 404, and a
bare ``GET /api`` got the SPA's HTML with a 200. A CLI or an older frontend
calling a removed endpoint then saw a misleading "method not allowed".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


@pytest.fixture
def client() -> TestClient:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = False  # the SPA catch-all exists only in production mode
    server = WebServer(cfg, bus=EventBus())
    # The suite-wide conftest wraps TestClient with an authenticated session.
    return TestClient(server.app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/no-such-route"),
        ("POST", "/api/no-such-route"),
        ("PUT", "/api/no-such-route/1"),
        ("DELETE", "/api/no-such-route/1"),
        ("GET", "/api"),
    ],
)
def test_unknown_api_route_is_404_json(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_real_route_with_wrong_method_is_405_naming_the_real_method(
    client: TestClient,
) -> None:
    response = client.post("/api/health")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"


def test_client_side_route_still_serves_the_spa(client: TestClient) -> None:
    response = client.get("/settings/providers")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_missing_asset_is_still_an_honest_404(client: TestClient) -> None:
    response = client.get("/brand/no-such-logo.png")

    assert response.status_code == 404
