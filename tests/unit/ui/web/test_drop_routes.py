"""REST-route tests for the drag-drop intake (`POST /api/chat/drop`).

The dock / overlay POSTs dropped files (+ optional dragged text) as multipart;
the route captures them as SILENT context via ``brain.add_dropped_context`` — it
never triggers a brain turn. Mirrors the avatar-upload pattern (size cap).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.drop_routes import router as drop_router


class _FakeBrain:
    def __init__(self) -> None:
        self.dropped: list[tuple] = []

    def add_dropped_context(self, text, images=()) -> None:
        self.dropped.append((text, images))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


def _client(brain: object, bus: _FakeBus | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(drop_router)
    app.state.brain = brain
    app.state.bus = bus or _FakeBus()
    return app, TestClient(app)


def test_drop_file_captures_context_no_turn() -> None:
    brain = _FakeBrain()
    bus = _FakeBus()
    app, client = _client(brain, bus)

    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("notes.txt", b"hello from a dropped file", "text/plain"))],
        data={"thread_id": "t1"},
    )

    assert resp.status_code == 200
    assert resp.json()["dispatched"] is True
    assert len(brain.dropped) == 1
    assert "notes.txt" in brain.dropped[0][0]
    # No turn-triggering event published.
    assert bus.published == []


def test_drop_image_captured_as_context_image() -> None:
    brain = _FakeBrain()
    _app, client = _client(brain)

    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("pic.png", b"\x89PNGdata", "image/png"))],
        data={"thread_id": "t2"},
    )

    assert resp.status_code == 200
    assert len(brain.dropped) == 1
    _text, images = brain.dropped[0]
    assert len(images) == 1


def test_drop_dragged_text_only_captured() -> None:
    brain = _FakeBrain()
    _app, client = _client(brain)

    resp = client.post(
        "/api/chat/drop",
        data={"thread_id": "t3", "text": "https://example.com/page"},
    )

    assert resp.status_code == 200
    assert resp.json()["dispatched"] is True
    assert "example.com" in brain.dropped[0][0]


def test_empty_drop_captures_nothing() -> None:
    brain = _FakeBrain()
    _app, client = _client(brain)

    resp = client.post("/api/chat/drop", data={"thread_id": "t4"})

    assert resp.status_code == 200
    assert resp.json()["dispatched"] is False
    assert brain.dropped == []


def test_no_brain_returns_503() -> None:
    _app, client = _client(None)
    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("notes.txt", b"hi", "text/plain"))],
        data={"thread_id": "t1"},
    )
    assert resp.status_code == 503


def test_oversized_drop_is_rejected() -> None:
    brain = _FakeBrain()
    _app, client = _client(brain)
    huge = b"x" * (30 * 1024 * 1024)  # 30 MB > cap

    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("big.bin", huge, "application/octet-stream"))],
        data={"thread_id": "t5"},
    )

    assert resp.status_code == 413
    assert brain.dropped == []
