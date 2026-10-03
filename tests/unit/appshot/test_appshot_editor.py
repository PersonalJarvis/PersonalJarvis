"""The appshot editor's way back: an edited picture replaces the held appshot."""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.appshot.store import Appshot, AppshotStore, get_store


def _shot(shot_id: str = "s1") -> Appshot:
    return Appshot(
        id=shot_id,
        image=b"jpeg",
        mime="image/jpeg",
        width=800,
        height=600,
        label="selected area",
        app_name="Editor",
        note="APPSHOT: evidence",
        ui_text="",
        trigger="hotkey",
        taken_at=1.0,
    )


def _png(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


def test_an_edit_reaches_the_last_and_the_waiting_appshot() -> None:
    store = AppshotStore()
    store.remember(_shot(), keep_s=60)
    store.park(_shot(), ttl_s=60)

    edited = store.replace_image("s1", b"png", "image/png", 40, 30)

    assert edited is not None and edited.note == "APPSHOT: evidence", "the context note stays"
    assert store.latest().image == b"png" and store.latest().mime == "image/png"
    pending = store.take_pending()
    assert pending.image == b"png" and (pending.width, pending.height) == (40, 30)


def test_an_edit_for_a_gone_or_other_appshot_changes_nothing() -> None:
    store = AppshotStore()
    store.remember(_shot("s1"), keep_s=60)
    assert store.replace_image("other", b"png", "image/png", 1, 1) is None
    assert store.latest().image == b"jpeg"
    assert AppshotStore().replace_image("s1", b"png", "image/png", 1, 1) is None


@pytest.fixture
def client():
    from jarvis.ui.web.appshot_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = None
    store = get_store()
    store.clear()
    store.remember(_shot("s1"), keep_s=60)
    yield TestClient(app)
    store.clear()


def test_the_route_takes_a_png_and_serves_it_back(client) -> None:
    png = _png()
    response = client.put("/api/appshot/latest/image?id=s1", content=png)

    assert response.status_code == 200, response.text
    assert response.json()["appshot"]["width"] == 40
    served = client.get("/api/appshot/latest/image")
    assert served.content == png and served.headers["content-type"] == "image/png"


@pytest.mark.parametrize(
    ("body", "status"),
    [(b"", 413), (b"not an image", 400)],
)
def test_the_route_refuses_what_is_not_a_png(client, body: bytes, status: int) -> None:
    assert client.put("/api/appshot/latest/image?id=s1", content=body).status_code == status
    assert get_store().latest().image == b"jpeg"


def test_the_route_refuses_an_expired_appshot(client) -> None:
    assert client.put("/api/appshot/latest/image?id=gone", content=_png()).status_code == 404


# -- copy: the editor's PNG goes to the OS clipboard natively ----------------


def _copy_client(monkeypatch: pytest.MonkeyPatch, *, native: bool, accepted: bool = True):
    from jarvis.platform import clipboard_image
    from jarvis.ui.web.appshot_routes import router

    copied: list[bytes] = []

    def fake_write(png: bytes) -> bool:
        copied.append(png)
        return accepted

    monkeypatch.setattr(clipboard_image, "write_png", fake_write)
    app = FastAPI()
    app.include_router(router)
    app.state.native_file_actions = native
    return TestClient(app), copied


def test_copy_hands_the_png_to_the_native_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    client, copied = _copy_client(monkeypatch, native=True)
    png = _png()

    response = client.post("/api/appshot/clipboard", content=png)

    assert response.status_code == 200, response.text
    assert copied == [png]


def test_copy_is_desktop_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a browser/headless server the clipboard would be the server's."""
    client, copied = _copy_client(monkeypatch, native=False)

    assert client.post("/api/appshot/clipboard", content=_png()).status_code == 404
    assert copied == []


def test_copy_refuses_what_is_not_a_png(monkeypatch: pytest.MonkeyPatch) -> None:
    client, copied = _copy_client(monkeypatch, native=True)

    assert client.post("/api/appshot/clipboard", content=b"GIF89a....").status_code == 400
    assert client.post("/api/appshot/clipboard", content=b"").status_code == 413
    assert copied == []


def test_copy_says_when_the_os_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _copied = _copy_client(monkeypatch, native=True, accepted=False)

    response = client.post("/api/appshot/clipboard", content=_png())

    assert response.status_code == 503
    assert response.json()["detail"] == "native-clipboard-unavailable"


def test_clipboard_image_needs_a_display(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from jarvis.platform import clipboard_image

    monkeypatch.setattr(
        clipboard_image, "detect_capabilities", lambda: SimpleNamespace(display_present=False)
    )
    assert clipboard_image.write_png(_png()) is False
    assert clipboard_image.write_png(b"not a png") is False


def test_clipboard_image_dib_is_a_bitmap_without_the_file_header() -> None:
    from jarvis.platform.clipboard_image import png_to_dib

    dib = png_to_dib(_png(40, 30))
    # BITMAPINFOHEADER: its own size first, then width and height.
    assert int.from_bytes(dib[0:4], "little") == 40
    assert int.from_bytes(dib[4:8], "little") == 40
    assert int.from_bytes(dib[8:12], "little", signed=True) == 30


@pytest.mark.parametrize(
    ("wayland", "tools", "expected"),
    [
        ("wayland-0", {"wl-copy", "xclip"}, "wl-copy"),
        ("", {"wl-copy", "xclip"}, "xclip"),
        ("", {"wl-copy"}, "wl-copy"),
        ("", set(), None),
    ],
)
def test_clipboard_image_picks_the_linux_tool(
    monkeypatch: pytest.MonkeyPatch, wayland: str, tools: set[str], expected: str | None
) -> None:
    from jarvis.platform import clipboard_image

    ran: list[list[str]] = []
    monkeypatch.setenv("WAYLAND_DISPLAY", wayland)
    monkeypatch.setattr(
        clipboard_image.shutil, "which", lambda name: f"/usr/bin/{name}" if name in tools else None
    )
    monkeypatch.setattr(
        clipboard_image, "_run", lambda command, data=None: ran.append(command) or True
    )

    ok = clipboard_image._write_linux(_png())  # noqa: SLF001

    assert ok is (expected is not None)
    assert [command[0] for command in ran] == ([f"/usr/bin/{expected}"] if expected else [])


# -- drag out: a file for the native drag bridge -------------------------------


def test_drag_file_is_written_once_and_old_ones_are_swept(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import os

    from jarvis.appshot import dragfile

    monkeypatch.setattr(dragfile.tempfile, "gettempdir", lambda: str(tmp_path))
    folder = dragfile.drag_folder()
    folder.mkdir()
    stale = folder / "appshot-old.png"
    stale.write_bytes(b"x")
    os.utime(stale, (1_000.0, 1_000.0))

    first = dragfile.write_drag_file(_png(), now=1_000.0 + dragfile.MAX_AGE_S + 5)
    second = dragfile.write_drag_file(_png(), now=1_000.0 + dragfile.MAX_AGE_S + 5)

    assert first is not None and second is not None and first != second
    assert first.read_bytes() == _png()
    assert not stale.exists()


def test_drag_route_returns_a_path_on_the_desktop_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from jarvis.appshot import dragfile
    from jarvis.ui.web.appshot_routes import router

    monkeypatch.setattr(dragfile.tempfile, "gettempdir", lambda: str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    app.state.native_file_actions = True
    client = TestClient(app)

    response = client.post("/api/appshot/drag-file", content=_png())
    assert response.status_code == 200, response.text
    path = response.json()["path"]
    assert path.startswith(str(tmp_path)) and path.endswith(".png")
    assert client.post("/api/appshot/drag-file", content=b"nope").status_code == 400

    app.state.native_file_actions = False
    assert client.post("/api/appshot/drag-file", content=_png()).status_code == 404
