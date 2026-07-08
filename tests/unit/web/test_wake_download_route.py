"""POST /api/settings/wake-word/download-model triggers a non-fatal fetch."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router as settings_router


@pytest.fixture
def client(monkeypatch):
    import jarvis.speech.wake_model_fetch as wmf
    monkeypatch.setattr(wmf, "ensure_vosk_model", lambda *a, **k: object())
    monkeypatch.setattr(wmf, "vosk_model_present", lambda *a, **k: True)
    app = FastAPI()
    app.include_router(settings_router)
    return TestClient(app)


def test_download_model_ok(client):
    r = client.post("/api/settings/wake-word/download-model")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["present"] is True


def test_download_model_failure_is_non_fatal(monkeypatch):
    """A fetch failure must never 500 -- it reports ok=False with a clear message."""
    import jarvis.speech.wake_model_fetch as wmf

    monkeypatch.setattr(wmf, "ensure_vosk_model", lambda *a, **k: None)
    monkeypatch.setattr(wmf, "vosk_model_present", lambda *a, **k: False)
    app = FastAPI()
    app.include_router(settings_router)
    client = TestClient(app)

    r = client.post("/api/settings/wake-word/download-model")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["present"] is False
    assert body["message"]
