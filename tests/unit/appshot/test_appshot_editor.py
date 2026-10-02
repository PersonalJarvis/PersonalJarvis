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
