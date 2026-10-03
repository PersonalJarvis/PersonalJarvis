"""The appshot gallery: every appshot and its saved edit, kept on disk."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.appshot import library
from jarvis.appshot import service as appshot_service
from jarvis.appshot.store import Appshot, get_store


def _png(width: int = 64, height: int = 48, colour=(20, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, "PNG")
    return buffer.getvalue()


def _shot(shot_id: str = "a1", *, taken_at: float = 10.0, image: bytes | None = None) -> Appshot:
    return Appshot(
        id=shot_id,
        image=image if image is not None else _png(),
        mime="image/png",
        width=64,
        height=48,
        label="active window",
        app_name="Editor",
        note="APPSHOT: evidence",
        ui_text="secret on-screen text",
        trigger="hotkey",
        taken_at=taken_at,
    )


def test_a_kept_appshot_lists_without_its_screen_text() -> None:
    assert library.save(_shot())

    [item] = library.list_items()
    assert (item.id, item.variant, item.width, item.app_name) == ("a1", "original", 64, "Editor")
    assert item.path.read_bytes() == _shot().image
    stored = "".join(p.read_text("utf-8") for p in item.path.parent.glob("*.json"))
    assert "secret on-screen text" not in stored, "the scrubbed UI text never reaches disk"


def test_an_edit_sits_right_before_its_original_and_newest_comes_first() -> None:
    library.save(_shot("old", taken_at=1.0))
    library.save(_shot("new", taken_at=2.0))
    edited = _png(32, 24, (250, 0, 0))
    library.save_edit(_shot("old", image=edited))

    rows = [(i.id, i.variant) for i in library.list_items()]
    assert rows == [("new", "original"), ("old", "edited"), ("old", "original")]
    edit = library.get_item("old", "edited")
    assert edit is not None and edit.path.read_bytes() == edited
    assert library.get_item("old", "original").has_edit


def test_an_edit_of_an_appshot_the_library_never_saw_gets_its_own_entry() -> None:
    library.save_edit(_shot("lone"))
    assert [(i.id, i.variant) for i in library.list_items()] == [("lone", "edited")]


def test_deleting_an_edit_keeps_the_original_and_deleting_the_original_removes_both() -> None:
    library.save(_shot())
    library.save_edit(_shot(image=_png(10, 10)))

    assert library.delete("a1", "edited")
    assert [(i.variant, i.has_edit) for i in library.list_items()] == [("original", False)]
    library.save_edit(_shot(image=_png(10, 10)))
    assert library.delete("a1", "original")
    assert library.list_items() == []


def test_the_library_is_capped_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "MAX_ENTRIES", 3)
    for n in range(5):
        library.save(_shot(f"s{n}", taken_at=float(n)))
    assert [i.id for i in library.list_items()] == ["s4", "s3", "s2"]


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "..", "x" * 65])
def test_an_id_that_is_a_path_fragment_is_refused(bad: str) -> None:
    assert not library.valid_id(bad)
    assert library.get_item(bad, "original") is None
    assert library.delete(bad, "original") is False


def test_a_thumbnail_is_a_small_jpeg_and_is_made_once() -> None:
    library.save(_shot(image=_png(1600, 900)))
    item = library.get_item("a1", "original")

    data, mime = library.thumbnail(item)
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as thumb:
        assert max(thumb.size) <= library.THUMB_EDGE
    assert list(item.path.parent.glob("thumb-original-*.jpg"))


async def test_take_keeps_the_appshot_only_while_the_library_is_on(monkeypatch) -> None:
    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(appshot_service, "_attach_to_card", nothing)
    for switch, expected in ((False, 0), (True, 1)):
        config = SimpleNamespace(appshot=SimpleNamespace(library=switch))
        await appshot_service._keep_in_library(_shot(f"t{int(switch)}"), config)
        assert len(library.list_items()) == expected


# -- routes ---------------------------------------------------------------------


@pytest.fixture
def client():
    from jarvis.ui.web.appshot_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = None
    get_store().clear()
    yield TestClient(app)
    get_store().clear()


def test_the_gallery_route_lists_and_serves_pictures(client) -> None:
    library.save(_shot())

    body = client.get("/api/appshot/library").json()
    assert [i["id"] for i in body["items"]] == ["a1"]
    full = client.get("/api/appshot/library/a1/image")
    assert full.status_code == 200 and full.content == _shot().image
    thumb = client.get("/api/appshot/library/a1/image?thumb=1")
    assert thumb.headers["content-type"] == "image/jpeg"
    assert client.get("/api/appshot/library/a1/image?variant=edited").status_code == 404


def test_opening_a_kept_picture_holds_it_for_the_editor(client, monkeypatch) -> None:
    from jarvis.appshot import editor_window

    async def no_window(_shot_id: str) -> bool:
        return False

    monkeypatch.setattr(editor_window, "open_editor_window", no_window)
    library.save(_shot())

    response = client.post("/api/appshot/library/a1/open", json={"variant": "original"})
    assert response.json() == {"id": "a1", "window": False}
    assert client.get("/api/appshot/latest/image?id=a1").content == _shot().image


def test_saving_an_edit_from_the_gallery_keeps_it_beside_the_original(client) -> None:
    library.save(_shot())
    edited = _png(20, 10, (0, 200, 0))

    # The hold expired: the edit route holds the kept picture again by itself.
    response = client.put("/api/appshot/latest/image?id=a1", content=edited)
    assert response.status_code == 200, response.text
    assert library.get_item("a1", "edited").path.read_bytes() == edited


def test_the_gallery_can_be_deleted(client) -> None:
    library.save(_shot("a1"))
    library.save(_shot("a2"))

    assert client.delete("/api/appshot/library/a1").status_code == 200
    assert client.delete("/api/appshot/library/a1").status_code == 404
    assert client.delete("/api/appshot/library").json() == {"ok": True, "removed": 1}
    assert client.get("/api/appshot/library").json()["items"] == []
