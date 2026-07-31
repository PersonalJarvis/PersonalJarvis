"""Integration tests for the desktop-only clipboard REST fallback."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.clipboard_routes as clipboard_routes
from jarvis.ui.web.clipboard_routes import router as clipboard_router


def _client(*, native: bool) -> TestClient:
    app = FastAPI()
    app.state.native_file_actions = native
    app.include_router(clipboard_router)
    return TestClient(app)


def test_desktop_copy_writes_complete_text(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr(
        clipboard_routes,
        "write_text",
        lambda text: (copied.append(text), True)[1],
    )

    response = _client(native=True).post(
        "/api/clipboard/text",
        json={"text": "first line\nsecond line"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"copied": True}
    assert copied == ["first line\nsecond line"]


def test_headless_copy_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _unexpected(_text: str) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(clipboard_routes, "write_text", _unexpected)
    response = _client(native=False).post(
        "/api/clipboard/text",
        json={"text": "browser-owned text"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "native-clipboard-disabled"
    assert called is False


def test_unavailable_native_clipboard_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clipboard_routes, "write_text", lambda _text: False)
    response = _client(native=True).post(
        "/api/clipboard/text",
        json={"text": "copy me"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "native-clipboard-unavailable"


def test_desktop_read_returns_the_clipboard_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clipboard_routes, "read_text", lambda: "first\nsecond")

    response = _client(native=True).get("/api/clipboard/text")

    assert response.status_code == 200, response.text
    assert response.json() == {"text": "first\nsecond"}


def test_headless_read_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _unexpected() -> str | None:
        nonlocal called
        called = True
        return "leaked clipboard"

    monkeypatch.setattr(clipboard_routes, "read_text", _unexpected)
    response = _client(native=False).get("/api/clipboard/text")

    assert response.status_code == 404
    assert response.json()["detail"] == "native-clipboard-disabled"
    assert called is False


def test_unreadable_clipboard_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard_routes, "read_text", lambda: None)

    response = _client(native=True).get("/api/clipboard/text")

    assert response.status_code == 503
    assert response.json()["detail"] == "native-clipboard-unavailable"


def test_empty_clipboard_is_a_successful_empty_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty clipboard is not an error — only an unreachable one is."""
    monkeypatch.setattr(clipboard_routes, "read_text", lambda: "")

    response = _client(native=True).get("/api/clipboard/text")

    assert response.status_code == 200, response.text
    assert response.json() == {"text": ""}


def test_copy_route_declares_danger_metadata() -> None:
    schema = _client(native=True).app.openapi()
    operation = schema["paths"]["/api/clipboard/text"]["post"]
    assert operation["x-jarvis-dangerous"] is True
