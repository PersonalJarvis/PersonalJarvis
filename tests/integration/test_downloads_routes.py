"""Integration tests for /api/downloads (save-to-Downloads) routes.

The desktop shell saves files via this backend route because pywebview silently
drops browser downloads. These tests pin: desktop-gating (404 when disabled),
filename hardening, collision avoidance, base64 decode, and the size cap.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.downloads_routes as downloads_routes
from jarvis.ui.web.downloads_routes import router as downloads_router


def _client(native: bool, home: Path) -> TestClient:
    app = FastAPI()
    app.state.native_file_actions = native
    app.include_router(downloads_router)
    client = TestClient(app)
    # Both Path.home() (in the route) resolve to the temp home.
    return client


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_capabilities_reflects_flag(home: Path) -> None:
    client = _client(native=True, home=home)
    body = client.get("/api/downloads/capabilities").json()
    assert body["native_file_actions"] is True
    assert body["platform"] in ("win32", "darwin", "linux")

    client_off = _client(native=False, home=home)
    assert (
        client_off.get("/api/downloads/capabilities").json()["native_file_actions"]
        is False
    )


def test_save_writes_to_downloads(home: Path) -> None:
    client = _client(native=True, home=home)
    res = client.post(
        "/api/downloads/save",
        json={"filename": "note.txt", "content_b64": _b64("hällo".encode())},  # i18n-allow
    )
    assert res.status_code == 200, res.text
    data = res.json()
    target = home / "Downloads" / "note.txt"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hällo"  # i18n-allow
    assert data["saved_path"] == str(target)
    assert data["filename"] == "note.txt"
    assert data["bytes_written"] == len("hällo".encode())  # i18n-allow


def test_save_disabled_returns_404(home: Path) -> None:
    client = _client(native=False, home=home)
    res = client.post(
        "/api/downloads/save",
        json={"filename": "x.txt", "content_b64": _b64(b"data")},
    )
    assert res.status_code == 404
    assert not (home / "Downloads").exists()


def test_save_sanitizes_path_traversal(home: Path) -> None:
    client = _client(native=True, home=home)
    res = client.post(
        "/api/downloads/save",
        json={
            "filename": "../../etc/pa:ss<wd>.txt",
            "content_b64": _b64(b"x"),
        },
    )
    assert res.status_code == 200, res.text
    saved = Path(res.json()["saved_path"])
    # Stays inside Downloads, directory parts + illegal chars stripped.
    assert saved.parent == home / "Downloads"
    assert saved.name == "pa_ss_wd_.txt"


def test_save_avoids_collision(home: Path) -> None:
    client = _client(native=True, home=home)
    first = client.post(
        "/api/downloads/save",
        json={"filename": "dup.txt", "content_b64": _b64(b"one")},
    ).json()
    second = client.post(
        "/api/downloads/save",
        json={"filename": "dup.txt", "content_b64": _b64(b"two")},
    ).json()
    assert Path(first["saved_path"]).name == "dup.txt"
    assert Path(second["saved_path"]).name == "dup-1.txt"
    assert (home / "Downloads" / "dup.txt").read_bytes() == b"one"
    assert (home / "Downloads" / "dup-1.txt").read_bytes() == b"two"


def test_save_rejects_invalid_base64(home: Path) -> None:
    client = _client(native=True, home=home)
    res = client.post(
        "/api/downloads/save",
        json={"filename": "x.txt", "content_b64": "not!base64!!"},
    )
    assert res.status_code == 400


def test_save_rejects_oversized(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloads_routes, "_MAX_BYTES", 8)
    client = _client(native=True, home=home)
    res = client.post(
        "/api/downloads/save",
        json={"filename": "big.bin", "content_b64": _b64(b"x" * 16)},
    )
    assert res.status_code == 413
