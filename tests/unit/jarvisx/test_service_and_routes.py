"""Jarvis X service flows and the REST contract, with fakes for every OS seam."""

from __future__ import annotations

import asyncio
import contextlib
import io
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.core.config_writer import set_jarvisx_settings
from jarvis.jarvisx import capture
from jarvis.jarvisx import service as service_module
from jarvis.jarvisx import store as store_module
from jarvis.jarvisx.overlay.controller import Selection
from jarvis.jarvisx.service import JarvisXService

_MONITORS = [
    {"left": 0, "top": 0, "width": 200, "height": 100},
    {"left": 0, "top": 0, "width": 200, "height": 100},
]


class FakeBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [type(e).__name__ for e in self.events]


class FakeOverlay:
    def __init__(self, selection: Selection | None = None) -> None:
        self.selection = selection
        self.cards: list[dict] = []
        self.hidden_grabs = 0
        self.recording_shown: dict | None = None
        self.removed: list[str] = []

    def bind(self, loop, handler) -> None:
        self.handler = handler

    async def select_region(self, *, purpose: str):
        return self.selection

    @contextlib.contextmanager
    def cards_hidden(self):
        self.hidden_grabs += 1
        yield

    async def show_card(self, **kwargs) -> bool:
        self.cards.append(kwargs)
        return True

    async def remove_card(self, item_id: str) -> None:
        self.removed.append(item_id)

    async def show_recording(self, *, monitor, rect) -> bool:
        self.recording_shown = {"monitor": monitor, "rect": rect}
        return True

    async def hide_recording(self) -> None:
        self.recording_shown = None

    async def shutdown(self) -> None:
        return None


def _config(tmp_path: Path, **overrides) -> SimpleNamespace:
    block = {
        "enabled": True,
        "save_dir": str(tmp_path / "shots"),
        "thumbnail_persist": True,
        "thumbnail_dismiss_s": 12,
        "copy_to_clipboard": False,
        "sound": False,
        "effect": True,
    }
    block.update(overrides)
    return SimpleNamespace(jarvisx=SimpleNamespace(**block))


@pytest.fixture
def fake_screen(monkeypatch):
    grabs: list[tuple] = []

    def grab_rect(bbox):
        grabs.append(tuple(bbox))
        _l, _t, w, h = bbox
        return capture.Frame(w, h, bytes([200, 10, 10]) * (w * h), tuple(bbox))

    monkeypatch.setattr(capture, "capability", lambda: (True, ""))
    monkeypatch.setattr(capture, "permission_problem", lambda: "")
    monkeypatch.setattr(capture, "list_monitors", lambda: _MONITORS)
    monkeypatch.setattr(capture, "monitor_under_cursor", lambda monitors: monitors[1])
    monkeypatch.setattr(capture, "grab_rect", grab_rect)
    return grabs


def _service(tmp_path: Path, overlay: FakeOverlay, **config) -> tuple[JarvisXService, FakeBus]:
    bus = FakeBus()
    svc = JarvisXService(
        store=store_module.ItemStore(tmp_path / "lib"),
        overlay=overlay,
        config_loader=lambda: _config(tmp_path, **config),
    )
    svc.bind(bus=bus)
    return svc, bus


def _saved_png_size(path: str, folder: Path) -> tuple[int, int]:
    saved = Path(path)
    assert saved.parent == folder and saved.suffix == ".png"
    with Image.open(saved) as image:
        return image.size


def _is_file(path: str) -> bool:
    return Path(path).is_file()


async def test_region_capture_saves_indexes_announces_and_shows_a_card(
    tmp_path, fake_screen
) -> None:
    selection = Selection(
        screen={"x": 0, "y": 0, "w": 200, "h": 100, "dpr": 1.0}, rect=(0.25, 0.5, 0.5, 0.25)
    )
    overlay = FakeOverlay(selection)
    svc, bus = _service(tmp_path, overlay)

    result = await svc.capture("region")
    await asyncio.gather(*list(service_module._TASKS))

    assert result.ok, result.message
    assert fake_screen == [(50, 50, 100, 25)]
    assert overlay.hidden_grabs == 1, "resting cards are hidden during the grab"
    item = result.item
    assert item is not None and item.kind == "image" and item.mode == "region"
    assert (item.width, item.height) == (100, 25)
    assert _saved_png_size(item.path, tmp_path / "shots") == (100, 25)
    assert _is_file(item.thumb_path)
    assert bus.names() == ["JarvisXItemCreated"]
    assert bus.events[0].id == item.id and bus.events[0].mode == "region"
    assert overlay.cards[0]["item_id"] == item.id
    assert overlay.cards[0]["persist"] is True and overlay.cards[0]["dismiss_s"] == 12
    assert overlay.cards[0]["rect"] == [0.25, 0.5, 0.5, 0.25]


async def test_cancelled_selection_saves_nothing(tmp_path, fake_screen) -> None:
    svc, bus = _service(tmp_path, FakeOverlay(None))
    result = await svc.capture("region")
    assert not result.ok and result.message == "Cancelled."
    assert fake_screen == [] and bus.events == []


async def test_switched_off_refuses(tmp_path, fake_screen) -> None:
    svc, _bus = _service(tmp_path, FakeOverlay(), enabled=False)
    result = await svc.capture("fullscreen")
    assert not result.ok and "switched off" in result.message


async def test_effect_off_means_no_card(tmp_path, fake_screen) -> None:
    overlay = FakeOverlay()
    svc, _bus = _service(tmp_path, overlay, effect=False)
    result = await svc.capture("fullscreen")
    assert result.ok and fake_screen == [(0, 0, 200, 100)]
    assert overlay.cards == []


async def test_recording_lifecycle_with_a_fake_recorder(tmp_path, fake_screen, monkeypatch) -> None:
    from jarvis.jarvisx import recorder as rec

    monkeypatch.setattr(
        rec,
        "probe_encoder",
        lambda: rec.EncoderChoice(available=True, codec="fake", extension="mp4"),
    )

    class FakeRecorder:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.elapsed_s = 1.5

        def start(self) -> str:
            return ""

        def stop(self):
            self.path.write_bytes(b"video")
            return rec.RecordingResult(
                path=self.path,
                width=200,
                height=100,
                duration_s=3.2,
                frames=96,
                first_frame=(2, 1, bytes(8)),
            )

    overlay = FakeOverlay()
    svc, bus = _service(tmp_path, overlay)
    monkeypatch.setattr(svc, "_make_recorder", lambda choice, bbox, target: FakeRecorder(target))

    started = await svc.start_recording("fullscreen")
    assert started.ok, started.message
    assert svc.recording_status() == {"recording": True, "mode": "fullscreen", "elapsed_s": 1.5}
    assert overlay.recording_shown == {"monitor": [0, 0, 200, 100], "rect": None}
    assert (await svc.start_recording("region")).message == "A recording is already running."

    stopped = await svc.stop_recording()
    assert stopped.ok and stopped.item is not None
    assert stopped.item.kind == "video" and stopped.item.duration_s == 3.2
    assert overlay.recording_shown is None
    assert svc.recording_status()["recording"] is False
    assert bus.names() == [
        "JarvisXRecordingChanged",
        "JarvisXRecordingChanged",
        "JarvisXItemCreated",
    ]
    assert bus.events[1].recording is False and bus.events[1].elapsed_s == 3.2
    assert (await svc.stop_recording()).message == "No recording is running."


# ------------------------------------------------------------------ config


def test_settings_writer_round_trip_and_allowlist(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text("[ui]\nsound_effects = true\n", encoding="utf-8")
    set_jarvisx_settings({"hotkey_region": "ctrl+alt+r", "thumbnail_dismiss_s": 9}, path=path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["jarvisx"] == {"hotkey_region": "ctrl+alt+r", "thumbnail_dismiss_s": 9}
    with pytest.raises(ValueError, match="unknown jarvisx"):
        set_jarvisx_settings({"bogus": 1}, path=path)


# ------------------------------------------------------------------- REST


@pytest.fixture
def client(tmp_path, monkeypatch, fake_screen):
    from jarvis.jarvisx import recorder as rec
    from jarvis.ui.web.jarvisx_routes import router

    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        f"[jarvisx]\nsave_dir = '{(tmp_path / 'shots').as_posix()}'\n"
        "copy_to_clipboard = false\nsound = false\neffect = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_CONFIG", str(config_path))
    monkeypatch.setattr(
        rec, "probe_encoder", lambda: rec.EncoderChoice(False, detail="No encoder here.")
    )
    store = store_module.ItemStore(tmp_path / "lib")
    store_module.set_store_for_tests(store)
    svc = JarvisXService(store=store, overlay=FakeOverlay())
    service_module.set_service_for_tests(svc)
    app = FastAPI()
    app.include_router(router)
    app.state.bus = FakeBus()
    app.state.native_file_actions = False
    try:
        yield TestClient(app)
    finally:
        store_module.set_store_for_tests(None)
        service_module.set_service_for_tests(None)


def test_settings_contract_and_partial_update(client) -> None:
    body = client.get("/api/jarvisx/settings").json()
    for key in (
        "enabled",
        "hotkeys",
        "thumbnail_persist",
        "thumbnail_dismiss_s",
        "save_dir",
        "copy_to_clipboard",
        "sound",
        "effect",
        "recording_available",
        "recording_detail",
        "shortcuts",
    ):
        assert key in body
    assert body["hotkeys"]["fullscreen"] == "ctrl+shift+1"
    assert body["recording_available"] is False and body["recording_detail"] == "No encoder here."
    assert set(body["shortcuts"]) == set(body["hotkeys"])

    updated = client.put(
        "/api/jarvisx/settings",
        json={"hotkeys": {"window": "Ctrl+Alt+W"}, "thumbnail_persist": True},
    ).json()
    assert updated["hotkeys"]["window"] == "ctrl+alt+w"
    assert updated["hotkeys"]["region"] == "ctrl+shift+2"
    assert updated["thumbnail_persist"] is True


def test_invalid_or_colliding_hotkeys_are_422(client) -> None:
    clash = client.put("/api/jarvisx/settings", json={"hotkeys": {"window": "ctrl+shift+2"}})
    assert clash.status_code == 422 and "same keys" in clash.json()["detail"]
    assert client.put("/api/jarvisx/settings", json={}).status_code == 400
    relative = client.put("/api/jarvisx/settings", json={"save_dir": "shots"})
    assert relative.status_code == 422


def test_capture_library_edit_and_delete(client, tmp_path) -> None:
    taken = client.post("/api/jarvisx/capture", json={"mode": "fullscreen"}).json()
    assert taken["ok"], taken
    item = taken["item"]
    item_id = item["id"]
    assert item["url"] == f"/api/jarvisx/items/{item_id}/file"

    listing = client.get("/api/jarvisx/items?limit=10").json()["items"]
    assert [i["id"] for i in listing] == [item_id]
    assert client.get(f"/api/jarvisx/items/{item_id}").json()["width"] == 200

    original = client.get(f"/api/jarvisx/items/{item_id}/file")
    assert original.status_code == 200 and original.headers["content-type"] == "image/png"
    thumb = client.get(f"/api/jarvisx/items/{item_id}/thumb")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/jarvisx/items/{item_id}/edited").status_code == 404

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    saved = client.put(
        f"/api/jarvisx/items/{item_id}/edited",
        content=buffer.getvalue(),
        headers={"Content-Type": "image/png"},
    )
    assert saved.status_code == 200
    assert saved.json()["edited_url"] == f"/api/jarvisx/items/{item_id}/edited"
    assert (
        Path(listing[0]["path"]).with_name(Path(listing[0]["path"]).stem + "-edited.png").is_file()
    )
    assert client.get(f"/api/jarvisx/items/{item_id}/edited").content == buffer.getvalue()
    bad = client.put(
        f"/api/jarvisx/items/{item_id}/edited",
        content=b"not a png",
        headers={"Content-Type": "image/png"},
    )
    assert bad.status_code == 422

    headless = client.post(f"/api/jarvisx/items/{item_id}/copy", json={"edited": True}).json()
    assert headless["ok"] is False
    editor = client.post(f"/api/jarvisx/items/{item_id}/open-editor").json()
    assert editor["ok"] is False and editor["url"].endswith(f"item={item_id}")

    assert client.delete(f"/api/jarvisx/items/{item_id}").json()["ok"] is True
    assert client.get(f"/api/jarvisx/items/{item_id}").status_code == 404
    assert client.get("/api/jarvisx/items/not-an-id").status_code == 422


def test_record_status_and_refusal_without_encoder(client) -> None:
    assert client.get("/api/jarvisx/record/status").json() == {
        "recording": False,
        "mode": None,
        "elapsed_s": 0.0,
    }
    started = client.post("/api/jarvisx/record/start", json={"mode": "fullscreen"}).json()
    assert started["ok"] is False and started["message"] == "No encoder here."
    stopped = client.post("/api/jarvisx/record/stop").json()
    assert stopped["ok"] is False and stopped["item"] is None
