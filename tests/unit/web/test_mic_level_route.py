"""GET /api/settings/wake-word/mic-level -- live mic dBFS for the onboarding
wake step (Task 7). Never 500s; reports too_quiet / no_device honestly.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


class _PermissionPort:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def runtime_access_granted(self, _permission_id) -> bool:
        return self.allowed


@pytest.fixture(autouse=True)
def _default_to_permissionless_test_platform(monkeypatch):
    """Keep generic route tests independent of this Mac's TCC state."""
    import jarvis.ui.web.settings_routes as settings_routes

    monkeypatch.setattr(settings_routes.sys, "platform", "linux")


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


def test_macos_permission_block_never_opens_microphone(monkeypatch):
    import jarvis.speech.diagnose as d
    import jarvis.ui.web.settings_routes as settings_routes

    calls = 0

    async def forbidden_measure(duration_s=3.0):
        nonlocal calls
        calls += 1
        return -15.0

    monkeypatch.setattr(settings_routes.sys, "platform", "darwin")
    monkeypatch.setattr(d, "measure_mic_dbfs", forbidden_measure)
    app = FastAPI()
    app.state.system_permission_port = _PermissionPort(False)
    app.include_router(router)

    response = TestClient(app).get("/api/settings/wake-word/mic-level")

    assert response.status_code == 200
    assert response.json() == {
        "max_dbfs": -120.0,
        "no_device": False,
        "too_quiet": False,
        "permission_required": True,
    }
    assert calls == 0


def test_macos_wake_self_test_does_not_bypass_permission_gate(monkeypatch):
    import jarvis.speech.diagnose as d
    import jarvis.ui.web.settings_routes as settings_routes

    calls = 0

    async def forbidden_measure(duration_s=3.0):
        nonlocal calls
        calls += 1
        return -15.0

    monkeypatch.setattr(settings_routes.sys, "platform", "darwin")
    monkeypatch.setattr(d, "measure_mic_dbfs", forbidden_measure)
    app = FastAPI()
    app.state.system_permission_port = _PermissionPort(False)
    app.state.config = SimpleNamespace(
        trigger=SimpleNamespace(
            wake_word=SimpleNamespace(
                phrase="Hey Nova",
                engine="auto",
                custom_model_path="",
                fuzzy_match_ratio=0.8,
            )
        ),
        stt=SimpleNamespace(language="en"),
        ui=SimpleNamespace(language="en"),
    )
    app.include_router(router)

    response = TestClient(app).post("/api/settings/wake-word/self-test")

    assert response.status_code == 200
    assert response.json()["permission_required"] is True
    assert response.json()["no_device"] is False
    assert calls == 0
