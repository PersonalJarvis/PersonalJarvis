"""GET /api/settings/wake-word/mic-level -- live mic dBFS for the onboarding
wake step (Task 7). Never 500s; reports too_quiet / no_device honestly.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


@pytest.fixture
def client(monkeypatch):
    import jarvis.speech.diagnose as d

    async def fake_measure(duration_s=3.0):
        return -45.0

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_mic_level_reports_too_quiet(client):
    r = client.get("/api/settings/wake-word/mic-level")
    assert r.status_code == 200
    b = r.json()
    assert b["max_dbfs"] == -45.0
    assert b["too_quiet"] is True
    assert b["no_device"] is False


def test_mic_level_reports_no_device(monkeypatch):
    import jarvis.speech.diagnose as d

    async def fake_measure(duration_s=3.0):
        return -120.0

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/settings/wake-word/mic-level")
    assert r.status_code == 200
    b = r.json()
    assert b["no_device"] is True
    assert b["too_quiet"] is False


def test_mic_level_reports_good_level(monkeypatch):
    import jarvis.speech.diagnose as d

    async def fake_measure(duration_s=3.0):
        return -15.0

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/settings/wake-word/mic-level")
    assert r.status_code == 200
    b = r.json()
    assert b["max_dbfs"] == -15.0
    assert b["too_quiet"] is False
    assert b["no_device"] is False


def test_mic_level_never_500s_on_helper_error(monkeypatch):
    """Even if the underlying measurement blows up, the route stays honest --
    the route's defensive guard catches any exception and treats it as no device."""
    import jarvis.speech.diagnose as d

    async def fake_measure(duration_s=3.0):
        raise RuntimeError("mic measurement failed")

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.get("/api/settings/wake-word/mic-level")
    assert r.status_code == 200
    b = r.json()
    assert b["no_device"] is True
    assert b["too_quiet"] is False
