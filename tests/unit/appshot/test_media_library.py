"""Durable recordings join the gallery without copying or exposing partial files."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.appshot import library, media_library, recording
from jarvis.appshot.store import Appshot

ID = "a" * 32


@pytest.fixture
def client():
    from jarvis.ui.web.appshot_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def video(recording_id: str = ID, *, valid: bool = False) -> Path:
    path = recording.recording_dir() / f"{recording_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not valid:
        path.write_bytes(b"fake finalized recording")
        return path
    av = pytest.importorskip("av")
    with av.open(str(path), "w", format="mp4") as writer:
        stream = writer.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 160, 90, "yuv420p"
        for index in range(10):
            frame = av.VideoFrame.from_image(Image.new("RGB", (160, 90), (20, 80, 140)))
            frame.pts = index
            for packet in stream.encode(frame):
                writer.mux(packet)
        for packet in stream.encode():
            writer.mux(packet)
    return path


def screenshot(shot_id: str = "image") -> None:
    output = io.BytesIO()
    Image.new("RGB", (20, 10)).save(output, "PNG")
    library.save(Appshot(
        id=shot_id, image=output.getvalue(), mime="image/png", width=20, height=10,
        label="active window", app_name="Editor", note="", ui_text="", trigger="hotkey",
        taken_at=100.0,
    ))


def test_existing_recordings_appear_without_live_session_or_image_directory(client):
    path = video()
    assert not library.library_root().exists()
    body = client.get("/api/appshot/library").json()
    [item] = body["items"]
    assert item["id"] == f"recording_{ID}"
    assert item["mime"] == "video/mp4" and item["path"] == str(path)
    # Process-local caches are optional: reopening still finds the durable file.
    media_library._metadata.cache_clear()
    assert client.get("/api/appshot/library").json()["items"] == body["items"]


def test_gallery_includes_more_than_the_recent_ten_videos_and_sorts_with_images(client):
    screenshot()
    for index in range(12):
        path = video(f"{index:032x}")
        os.utime(path, (200 + index, 200 + index))
    items = client.get("/api/appshot/library").json()["items"]
    assert len(items) == 13
    assert items[0]["id"] == f"recording_{11:032x}"
    assert items[-1]["id"] == "image"


def test_video_metadata_poster_and_seekable_playback(client):
    path = video(valid=True)
    [item] = client.get("/api/appshot/library").json()["items"]
    assert (item["width"], item["height"]) == (160, 90)
    assert item["duration_s"] == pytest.approx(1, abs=0.15)
    url = f"/api/appshot/library/recording_{ID}/image"
    poster = client.get(f"{url}?thumb=1")
    assert poster.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(poster.content)).size == (160, 90)
    part = client.get(url, headers={"Range": "bytes=0-15"})
    assert part.status_code == 206 and part.content == path.read_bytes()[:16]
    assert part.headers["content-type"] == "video/mp4"
    assert part.headers["cache-control"] == "no-store"
    assert client.get(f"{url}?variant=edited").status_code == 404
    assert client.post(f"/api/appshot/library/recording_{ID}/open").status_code == 404


def test_bad_video_uses_a_small_poster_instead_of_returning_video_bytes(client):
    video()
    poster = client.get(f"/api/appshot/library/recording_{ID}/image?thumb=1")
    assert poster.headers["content-type"] == "image/svg+xml"
    assert poster.content.startswith(b"<svg") and len(poster.content) < 1000


def test_delete_video_never_deletes_a_screenshot_with_the_same_recorder_id(client):
    path = video()
    screenshot(ID)
    response = client.delete(f"/api/appshot/library/recording_{ID}")
    assert response.status_code == 200 and not path.exists()
    assert library.get_item(ID, "original") is not None
    assert client.delete(f"/api/appshot/library/recording_{ID}").status_code == 404


def test_clear_removes_finalized_captures_but_preserves_partial_and_foreign_files(client):
    screenshot()
    path = video()
    partial = path.with_suffix(".partial")
    partial.write_bytes(b"still recording")
    foreign = video("not-a-recorder-id")
    assert len(client.get("/api/appshot/library").json()["items"]) == 2
    body = client.delete("/api/appshot/library").json()
    assert body == {"ok": True, "removed": 2}
    assert not path.exists() and partial.exists() and foreign.exists()


def test_recording_symlinks_are_neither_listed_nor_deleted(client, tmp_path):
    target = tmp_path / "private.mp4"
    target.write_bytes(b"outside the recorder")
    link = recording.recording_dir() / f"{ID}.mp4"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("The host does not allow creating symlinks")
    assert client.get("/api/appshot/library").json()["items"] == []
    assert client.delete(f"/api/appshot/library/recording_{ID}").status_code == 404
    assert target.read_bytes() == b"outside the recorder" and link.is_symlink()
