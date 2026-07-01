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


# --- reveal / open (native "show in folder" + "open" for a saved file) -------


def _make_download(home: Path, name: str = "voice-session.md") -> Path:
    downloads = home / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    f = downloads / name
    f.write_text("# transcript", encoding="utf-8")
    return f


@pytest.mark.parametrize("route", ["reveal", "open"])
def test_reveal_open_disabled_returns_404(home: Path, route: str) -> None:
    client = _client(native=False, home=home)
    res = client.post(f"/api/downloads/{route}", json={"path": str(_make_download(home))})
    assert res.status_code == 404


@pytest.mark.parametrize("route", ["reveal", "open"])
def test_reveal_open_rejects_outside_downloads(home: Path, route: str) -> None:
    client = _client(native=True, home=home)
    _make_download(home)  # ensure Downloads exists
    outside = home / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    res = client.post(f"/api/downloads/{route}", json={"path": str(outside)})
    assert res.status_code == 403


@pytest.mark.parametrize("route", ["reveal", "open"])
def test_reveal_open_missing_file_returns_404(home: Path, route: str) -> None:
    client = _client(native=True, home=home)
    (home / "Downloads").mkdir(parents=True, exist_ok=True)
    gone = home / "Downloads" / "gone.md"
    res = client.post(f"/api/downloads/{route}", json={"path": str(gone)})
    assert res.status_code == 404


def test_reveal_invokes_helper_with_resolved_path(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.platform.open_path as open_path

    calls: list[Path] = []
    monkeypatch.setattr(
        open_path, "reveal_in_folder", lambda p: (calls.append(p), True)[1]
    )
    client = _client(native=True, home=home)
    f = _make_download(home)
    res = client.post("/api/downloads/reveal", json={"path": str(f)})
    assert res.status_code == 200, res.text
    assert res.json() == {"revealed": True}
    assert calls and calls[0] == f.resolve()


def test_open_invokes_helper_and_reports_failure(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.platform.open_path as open_path

    calls: list[Path] = []
    monkeypatch.setattr(open_path, "open_file", lambda p: (calls.append(p), False)[1])
    client = _client(native=True, home=home)
    f = _make_download(home)
    res = client.post("/api/downloads/open", json={"path": str(f)})
    assert res.status_code == 200, res.text
    assert res.json() == {"opened": False}
    assert calls and calls[0] == f.resolve()
