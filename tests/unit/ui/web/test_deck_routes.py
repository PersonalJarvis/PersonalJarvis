"""REST-route tests for the mission deck's pictures.

Three things must hold. The Screen-Context frame is served exactly as the
mirror holds it and disappears with it. The Computer-Use frame is addressed by
hash ONLY — a path, a filename or a short digest is refused before the
filesystem is touched, which is the whole traversal defence. And nothing is
cacheable: every image response says ``no-store``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.screen_context import last_frame as last_frame_module
from jarvis.screen_context.last_frame import LastFrameMirror
from jarvis.ui.web import deck_routes


@pytest.fixture
def mirror(monkeypatch: pytest.MonkeyPatch) -> LastFrameMirror:
    fresh = LastFrameMirror(ttl_s=120)
    monkeypatch.setattr(last_frame_module, "_mirror", fresh)
    return fresh


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deck_routes.router)
    return TestClient(app)


def test_frame_404_when_nothing_is_held(client: TestClient, mirror: LastFrameMirror):
    assert client.get("/api/deck/frame").status_code == 404
    assert client.get("/api/deck/frame/meta").json() == {"available": False, "seq": 0}


def test_frame_serves_the_held_picture_with_its_shape(client: TestClient, mirror: LastFrameMirror):
    mirror.set(
        b"\xff\xd8jpegbytes",
        mime="image/jpeg",
        width=1280,
        height=720,
        source="screen_context",
        target_label="Monitor 1",
        trace_id="abc",
    )

    meta = client.get("/api/deck/frame/meta").json()
    assert meta["available"] is True
    assert meta["seq"] == 1
    assert (meta["width"], meta["height"]) == (1280, 720)
    assert meta["target_label"] == "Monitor 1"

    res = client.get("/api/deck/frame")
    assert res.status_code == 200
    assert res.content == b"\xff\xd8jpegbytes"
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.headers["x-frame-seq"] == "1"
    assert res.headers["cache-control"] == "no-store"


def test_frame_disappears_when_the_mirror_is_cleared(client: TestClient, mirror: LastFrameMirror):
    mirror.set(b"x", mime="image/png", width=1, height=1, source="screen_context")
    assert client.get("/api/deck/frame").status_code == 200
    mirror.clear()
    assert client.get("/api/deck/frame").status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        "..%2F..%2Fetc%2Fpasswd",
        "abc",
        "A" * 64,  # upper-case hex is not the recorder's spelling
        "0" * 63,
        "0" * 65,
        "not-a-hash.jpg",
    ],
)
def test_cu_frame_refuses_anything_that_is_not_a_hash(client: TestClient, bad: str):
    res = client.get(f"/api/deck/cu-frame/{bad}")
    assert res.status_code in (400, 404)
    # The important half: a refused key never names a real file, so the
    # message is the generic one, never an OS error with a path in it. An
    # encoded slash does not even reach the handler — the router turns it
    # away first ("Not Found"), which is the same outcome one layer earlier.
    assert res.json()["detail"] in ("invalid_hash", "no_frame", "Not Found")


def test_cu_frame_serves_a_recorded_blob_by_hash(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir()
    sha = "ab" * 32
    (blob_dir / f"{sha}.jpg").write_bytes(b"\xff\xd8cuframe")
    monkeypatch.setattr(deck_routes, "CU_BLOB_DIR", blob_dir)

    res = client.get(f"/api/deck/cu-frame/{sha}")
    assert res.status_code == 200
    assert res.content == b"\xff\xd8cuframe"
    assert res.headers["content-type"].startswith("image/jpeg")
    assert res.headers["cache-control"] == "no-store"

    # A hash the recorder never wrote (or already pruned) is a plain 404.
    assert client.get(f"/api/deck/cu-frame/{'cd' * 32}").status_code == 404
