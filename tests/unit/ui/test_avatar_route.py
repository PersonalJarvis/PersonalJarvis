"""Tests for the profile-avatar endpoints (upload / serve / delete).

Contract (see jarvis/ui/web/profile_routes.py):
- POST   /api/profile/avatar   (multipart file)  → validate it is a real image
                                                    (Pillow magic-byte check, not
                                                    a trusted Content-Type), store
                                                    atomically, replace any prior
                                                    avatar; reject non-images (400)
                                                    and oversized files (413).
- GET    /api/profile/avatar                      → the stored bytes (200) with a
                                                    no-store cache header, or 404.
- DELETE /api/profile/avatar                      → remove the avatar (idempotent).
- GET    /api/profile carries ``has_avatar`` so the UI knows the initial state.

Storage is decoupled from the Curator/USER.md so the avatar works even when the
profile subsystem is in its 503 (Mock/Headless) state. It lives under
``user_data_dir()/data`` which the tests redirect via the ``LOCALAPPDATA`` env.
"""
from __future__ import annotations

import io
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.profile_routes import router


def _png_bytes(color: tuple[int, int, int] = (231, 196, 110), size: int = 4) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size: int = 4) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Redirect user_data_dir() → tmp so the avatar is written into the sandbox.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    app = FastAPI()
    app.include_router(router)
    # A profile-less app: the avatar CRUD must not depend on the Curator.
    return TestClient(app)


def test_get_avatar_404_when_none(client: TestClient) -> None:
    assert client.get("/api/profile/avatar").status_code == 404


def test_post_then_get_roundtrips_png(client: TestClient) -> None:
    data = _png_bytes()
    res = client.post(
        "/api/profile/avatar",
        files={"file": ("me.png", data, "image/png")},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["has_avatar"] is True

    got = client.get("/api/profile/avatar")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.content == data
    # Avatar is volatile → never cached, or a replace would show the old image.
    assert "no-store" in got.headers.get("cache-control", "")


def test_post_replaces_previous_avatar(client: TestClient, tmp_path: Path) -> None:
    client.post("/api/profile/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    client.post("/api/profile/avatar", files={"file": ("b.jpg", _jpeg_bytes(), "image/jpeg")})

    # Exactly one avatar file survives — no orphaned .png next to the new .jpg.
    avatar_dir = tmp_path / "Jarvis" / "data"
    survivors = sorted(p.name for p in avatar_dir.glob("profile_avatar.*"))
    assert survivors == ["profile_avatar.jpg"]

    got = client.get("/api/profile/avatar")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"


def test_post_rejects_non_image(client: TestClient) -> None:
    res = client.post(
        "/api/profile/avatar",
        # A .png filename + image content-type but the bytes are plain text:
        # Pillow must reject it on the magic bytes, not trust the header.
        files={"file": ("evil.png", b"not really an image", "image/png")},
    )
    assert res.status_code == 400
    assert res.json()["detail"]


def test_post_rejects_oversized(client: TestClient) -> None:
    # 9 MB of a "valid header" still trips the size guard before decoding.
    big = _png_bytes() + b"\x00" * (9 * 1024 * 1024)
    res = client.post(
        "/api/profile/avatar",
        files={"file": ("huge.png", big, "image/png")},
    )
    assert res.status_code == 413


def test_delete_is_idempotent_and_clears(client: TestClient) -> None:
    client.post("/api/profile/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert client.get("/api/profile/avatar").status_code == 200

    res = client.delete("/api/profile/avatar")
    assert res.status_code == 200
    assert res.json()["has_avatar"] is False
    assert client.get("/api/profile/avatar").status_code == 404

    # Deleting again is a no-op success (idempotent), not a 404.
    again = client.delete("/api/profile/avatar")
    assert again.status_code == 200
    assert again.json()["has_avatar"] is False


def test_profile_get_reports_has_avatar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/profile must surface ``has_avatar`` for the HeroBand."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    from jarvis.memory.user_profile import UserProfile

    user_md = tmp_path / "USER.md"
    user_md.write_text("---\nidentity:\n  name: Ada\n---\n\nbody\n", encoding="utf-8")
    profile = UserProfile.load(user_md)

    app = FastAPI()
    app.include_router(router)
    app.state.brain = types.SimpleNamespace(
        _user_profile=profile, _curator=None, _people=None
    )
    c = TestClient(app)

    assert c.get("/api/profile").json()["has_avatar"] is False
    c.post("/api/profile/avatar", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert c.get("/api/profile").json()["has_avatar"] is True
