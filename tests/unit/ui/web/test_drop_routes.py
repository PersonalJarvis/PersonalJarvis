"""REST-route tests for the drag-drop intake (`POST /api/chat/drop`).

The dock / overlay POSTs dropped files (+ optional dragged text) as multipart;
the route turns them into a proactive ``MessageSent`` brain turn via
``ingest_drop``. Mirrors the avatar-upload pattern (size cap, multipart).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain.drop_context import DROP_SOURCE_LAYER
from jarvis.core.events import MessageSent
from jarvis.ui.web.drop_routes import router as drop_router


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


class _FakeBrain:
    def __init__(self) -> None:
        self.injected: list[tuple] = []

    def inject_images_for_turn(self, trace_id, images) -> None:
        self.injected.append((trace_id, images))


def _client(bus: _FakeBus, brain: object = None) -> TestClient:
    app = FastAPI()
    app.include_router(drop_router)
    app.state.bus = bus
    app.state.brain = brain
    return TestClient(app)


def test_drop_file_dispatches_message() -> None:
    bus = _FakeBus()
    brain = _FakeBrain()
    client = _client(bus, brain)

    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("notes.txt", b"hello from a dropped file", "text/plain"))],
        data={"thread_id": "t1"},
    )

    assert resp.status_code == 200
    assert resp.json()["dispatched"] is True
    assert len(bus.published) == 1
    msg = bus.published[0]
    assert isinstance(msg, MessageSent)
    assert msg.source_layer == DROP_SOURCE_LAYER
    assert msg.thread_id == "t1"
    assert "notes.txt" in msg.text


def test_drop_image_injects_into_brain() -> None:
    bus = _FakeBus()
    brain = _FakeBrain()
    client = _client(bus, brain)

    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("pic.png", b"\x89PNGdata", "image/png"))],
        data={"thread_id": "t2"},
    )

    assert resp.status_code == 200
    assert len(brain.injected) == 1
    trace, images = brain.injected[0]
    assert trace == bus.published[0].trace_id
    assert len(images) == 1


def test_drop_dragged_text_only_dispatches() -> None:
    bus = _FakeBus()
    client = _client(bus, _FakeBrain())

    resp = client.post(
        "/api/chat/drop",
        data={"thread_id": "t3", "text": "https://example.com/page"},
    )

    assert resp.status_code == 200
    assert resp.json()["dispatched"] is True
    assert "example.com" in bus.published[0].text


def test_empty_drop_dispatches_nothing() -> None:
    bus = _FakeBus()
    client = _client(bus, _FakeBrain())

    resp = client.post("/api/chat/drop", data={"thread_id": "t4"})

    assert resp.status_code == 200
    assert resp.json()["dispatched"] is False
    assert bus.published == []


def test_oversized_drop_is_rejected() -> None:
    bus = _FakeBus()
    client = _client(bus, _FakeBrain())
    huge = b"x" * (30 * 1024 * 1024)  # 30 MB > cap

    resp = client.post(
        "/api/chat/drop",
        files=[("files", ("big.bin", huge, "application/octet-stream"))],
        data={"thread_id": "t5"},
    )

    assert resp.status_code == 413
    assert bus.published == []
