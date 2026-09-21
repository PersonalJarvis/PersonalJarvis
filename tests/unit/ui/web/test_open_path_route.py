"""POST /api/settings/open-path opens a chat file link on the desktop."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(native: bool) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.native_file_actions = native
    return TestClient(app)


def test_open_path_calls_open_file(tmp_path: Path) -> None:
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"video")
    with patch("jarvis.platform.open_path.open_file", return_value=True) as opened:
        response = _client(True).post("/api/settings/open-path", json={"path": str(target)})
    assert response.status_code == 200
    assert response.json() == {"opened": True}
    opened.assert_called_once_with(target.resolve())


def test_open_path_is_absent_without_native_file_actions(tmp_path: Path) -> None:
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"video")
    with patch("jarvis.platform.open_path.open_file") as opened:
        response = _client(False).post("/api/settings/open-path", json={"path": str(target)})
    assert response.status_code == 404
    opened.assert_not_called()


def test_open_path_refuses_a_program(tmp_path: Path) -> None:
    target = tmp_path / "run.exe"
    target.write_bytes(b"nope")
    with patch("jarvis.platform.open_path.open_file") as opened:
        response = _client(True).post("/api/settings/open-path", json={"path": str(target)})
    assert response.status_code == 400
    assert response.json()["detail"] == "refused-program"
    opened.assert_not_called()


def test_open_path_reports_a_missing_file(tmp_path: Path) -> None:
    response = _client(True).post(
        "/api/settings/open-path", json={"path": str(tmp_path / "gone.mp4")}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "not-found"
