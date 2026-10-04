"""Jarvis X library index, save paths and the recording loop (fake encoder)."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from jarvis.jarvisx import paths
from jarvis.jarvisx.recorder import Recorder
from jarvis.jarvisx.store import Item, ItemStore


def _item(tmp_path: Path, item_id: str, *, kind: str = "image") -> Item:
    original = tmp_path / f"{item_id}.png"
    original.write_bytes(b"png")
    return Item(
        id=item_id,
        kind=kind,  # type: ignore[arg-type]
        mode="region",
        created_at="2026-09-29T14:03:22+02:00",
        width=10,
        height=20,
        duration_s=None if kind == "image" else 3.5,
        path=str(original),
        thumb_path="",
    )


def test_store_lists_newest_first_and_serializes_the_contract(tmp_path) -> None:
    store = ItemStore(tmp_path / "lib")
    store.add(_item(tmp_path, "a" * 32))
    store.add(_item(tmp_path, "b" * 32, kind="video"))

    items = store.recent()
    assert [i.id for i in items] == ["b" * 32, "a" * 32]
    payload = items[0].to_json()
    assert payload["kind"] == "video"
    assert payload["duration_s"] == 3.5
    assert payload["url"] == f"/api/jarvisx/items/{'b' * 32}/file"
    assert payload["thumb_url"] == f"/api/jarvisx/items/{'b' * 32}/thumb"
    assert payload["edited_url"] is None
    assert payload["filename"] == f"{'b' * 32}.png"


def test_edited_url_appears_only_when_the_file_exists(tmp_path) -> None:
    store = ItemStore(tmp_path / "lib")
    item = store.add(_item(tmp_path, "c" * 32))
    edited = tmp_path / "c-edited.png"
    updated = store.set_edited(item.id, str(edited))
    assert updated is not None and updated.to_json()["edited_url"] is None
    edited.write_bytes(b"png")
    assert store.get(item.id).to_json()["edited_url"] == f"/api/jarvisx/items/{'c' * 32}/edited"


def test_a_file_removed_outside_the_app_drops_out(tmp_path) -> None:
    store = ItemStore(tmp_path / "lib")
    item = store.add(_item(tmp_path, "d" * 32))
    Path(item.path).unlink()
    assert store.recent() == []
    assert store.get(item.id) is None


def test_delete_removes_every_file(tmp_path) -> None:
    store = ItemStore(tmp_path / "lib")
    item = _item(tmp_path, "e" * 32)
    thumb = store.thumb_path_for(item.id)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"jpg")
    store.add(replace(item, thumb_path=str(thumb)))
    removed = store.delete(item.id)
    assert removed is not None
    assert not Path(item.path).exists() and not thumb.exists()
    assert store.delete(item.id) is None


def test_capture_names_and_unique_paths(tmp_path) -> None:
    when = datetime(2026, 9, 29, 14, 3, 22)
    name = paths.capture_filename("image", when, "png")
    assert name == "Jarvis X 2026-09-29 at 14.03.22.png"
    assert paths.capture_filename("video", when, "mp4").startswith("Recording ")
    (tmp_path / name).write_bytes(b"x")
    assert paths.unique_path(tmp_path, name).name == "Jarvis X 2026-09-29 at 14.03.22 (2).png"
    edited = paths.edited_path_for(tmp_path / name)
    assert edited.name == "Jarvis X 2026-09-29 at 14.03.22-edited.png"


def test_unusable_save_dir_falls_back(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(paths, "default_save_dir", lambda: tmp_path / "default")
    assert paths.resolve_save_dir(str(blocker / "sub")) == tmp_path / "default"
    assert paths.resolve_save_dir("") == tmp_path / "default"


# ---------------------------------------------------------------- recorder


class FakeSource:
    def __init__(self) -> None:
        self.closed = False
        self.grabs = 0

    def grab(self) -> tuple[int, int, bytes]:
        self.grabs += 1
        return 5, 3, bytes(5 * 3 * 4)

    def close(self) -> None:
        self.closed = True


class FakeEncoder:
    name = "fake"
    extension = "mp4"

    def __init__(self) -> None:
        self.opened: tuple | None = None
        self.pts: list[int] = []
        self.closed = False

    def open(self, path: Path, width: int, height: int, fps: int) -> None:
        self.opened = (path, width, height, fps)

    def write(self, bgra: bytes, width: int, height: int, pts: int) -> None:
        self.pts.append(pts)

    def close(self) -> None:
        self.closed = True


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_recorder_lifecycle_with_a_fake_encoder(tmp_path) -> None:
    source = FakeSource()
    encoder = FakeEncoder()
    recorder = Recorder(
        source_factory=lambda: source, encoder=encoder, path=tmp_path / "v.mp4", fps=50
    )
    assert recorder.start() == ""
    assert recorder.running
    _wait_for(lambda: len(encoder.pts) >= 5)
    result = recorder.stop()

    assert not recorder.running
    assert encoder.opened == (tmp_path / "v.mp4", 5, 3, 50)
    assert encoder.closed and source.closed
    assert result.frames == len(encoder.pts) >= 5
    assert encoder.pts == sorted(set(encoder.pts)), "timestamps strictly increase"
    assert (result.width, result.height) == (4, 2), "H.264 needs even dimensions"
    assert result.first_frame is not None and result.first_frame[:2] == (5, 3)
    assert result.ok and result.duration_s > 0


def test_recorder_reports_a_source_that_cannot_start(tmp_path) -> None:
    def broken():
        raise RuntimeError("no display")

    encoder = FakeEncoder()
    recorder = Recorder(source_factory=broken, encoder=encoder, path=tmp_path / "v.mp4")
    error = recorder.start()
    assert "no display" in error
    result = recorder.stop()
    assert not result.ok and result.frames == 0
    assert encoder.opened is None


def test_an_encoder_failure_ends_the_recording_with_a_reason(tmp_path) -> None:
    class Exploding(FakeEncoder):
        def write(self, bgra, width, height, pts) -> None:
            raise OSError("disk full")

    encoder = Exploding()
    recorder = Recorder(source_factory=FakeSource, encoder=encoder, path=tmp_path / "v.mp4", fps=50)
    recorder.start()
    _wait_for(lambda: not recorder.running, timeout=3.0)
    result = recorder.stop()
    assert "disk full" in result.error
    assert encoder.closed
