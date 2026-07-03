"""POST/GET /api/settings/wake-word/enable-local-speech — the in-app installer
that pulls faster-whisper so ANY wake phrase works on a fresh install.

The install itself (pip) is mocked; these lock the endpoint's state machine:
- already installed  → done/already, no pip call
- fresh install      → pip runs, status flips to done
- pip failure        → status reports error with the reason
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.ui.web.settings_routes as sr


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(sr.router)
    return TestClient(app)


def _reset_state() -> None:
    sr._local_speech_install["state"] = "idle"
    sr._local_speech_install["message"] = ""


class _SyncThread:
    """Runs the install target synchronously so the test is deterministic."""

    def __init__(self, target=None, **_kw) -> None:
        self._target = target

    def start(self) -> None:
        if self._target is not None:
            self._target()


def test_already_installed_returns_done_without_pip(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setattr(sr, "_local_whisper_available", lambda: True)
    called = []
    monkeypatch.setattr(
        "jarvis.setup.dependencies.install_pip_package",
        lambda *a, **k: called.append(a) or (True, "x"),
    )

    body = _client().post("/api/settings/wake-word/enable-local-speech").json()

    assert body["state"] == "done"
    assert body["already"] is True
    assert body["available"] is True
    assert called == []  # short-circuits — never shells out to pip


def test_fresh_install_runs_pip_and_status_flips_to_done(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setattr(sr, "_local_whisper_available", lambda: False)
    packages: list[str] = []

    def fake_install(pkg, **_kw):
        packages.append(pkg)
        return True, "install reported success"

    monkeypatch.setattr("jarvis.setup.dependencies.install_pip_package", fake_install)
    monkeypatch.setattr(sr.threading, "Thread", _SyncThread)

    client = _client()
    post = client.post("/api/settings/wake-word/enable-local-speech").json()
    assert post["state"] == "running"
    assert packages == [sr._LOCAL_SPEECH_PACKAGE]

    status = client.get("/api/settings/wake-word/enable-local-speech/status").json()
    assert status["state"] == "done"
    assert "success" in status["message"]


def test_pip_failure_is_reported_as_error(monkeypatch) -> None:
    _reset_state()
    monkeypatch.setattr(sr, "_local_whisper_available", lambda: False)

    def fake_install(pkg, **_kw):
        return False, "pip exited 1: no matching wheel for this platform"

    monkeypatch.setattr("jarvis.setup.dependencies.install_pip_package", fake_install)
    monkeypatch.setattr(sr.threading, "Thread", _SyncThread)

    client = _client()
    client.post("/api/settings/wake-word/enable-local-speech")

    status = client.get("/api/settings/wake-word/enable-local-speech/status").json()
    assert status["state"] == "error"
    assert "no matching wheel" in status["message"]


def test_status_reports_available_when_present(monkeypatch) -> None:
    # Present but this process never ran the installer (installed manually or in a
    # prior run) → status is truthful without a restart.
    _reset_state()
    monkeypatch.setattr(sr, "_local_whisper_available", lambda: True)

    status = _client().get(
        "/api/settings/wake-word/enable-local-speech/status"
    ).json()

    assert status["available"] is True
    assert status["state"] == "done"
