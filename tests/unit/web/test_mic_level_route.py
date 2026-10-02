"""GET /api/settings/wake-word/mic-level -- live mic dBFS for the onboarding
wake step (Task 7). Never 500s; reports too_quiet / no_device honestly.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.platform.permission_service import PermissionOutcome
from jarvis.platform.permissions import PermissionId
from jarvis.ui.web.settings_routes import router
from tests.fakes.fake_permission_service import FakePermissionService


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


def _macos_app(monkeypatch, outcome: PermissionOutcome) -> tuple[FastAPI, FakePermissionService]:
    import jarvis.ui.web.settings_routes as settings_routes

    monkeypatch.setattr(settings_routes.sys, "platform", "darwin")
    gate = FakePermissionService()
    gate.script(PermissionId.MICROPHONE, outcome)
    app = FastAPI()
    app.state.permission_service = gate
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
    return app, gate


def test_macos_mic_level_never_opens_the_microphone_without_a_grant(monkeypatch):
    import jarvis.speech.diagnose as d

    calls = 0

    async def forbidden_measure(duration_s=3.0):
        nonlocal calls
        calls += 1
        return -15.0

    monkeypatch.setattr(d, "measure_mic_dbfs", forbidden_measure)
    app, gate = _macos_app(monkeypatch, PermissionOutcome.DENIED)

    response = TestClient(app).get("/api/settings/wake-word/mic-level")

    assert response.status_code == 200
    body = response.json()
    assert body["permission_required"] is True
    assert body["permission"] == "microphone"
    assert body["can_open_settings"] is True
    assert body["max_dbfs"] == -120.0
    assert body["no_device"] is False and body["too_quiet"] is False
    assert "System Settings > Privacy & Security > Microphone" in body["hint"]
    assert "Settings > Permissions" not in body["message"] + body["hint"]
    assert calls == 0
    # A GET reads the state silently: it never asks.
    assert gate.ensure_calls() == []


def test_macos_mic_level_on_an_undecided_mac_reports_permission_required_without_asking(
    monkeypatch,
):
    app, gate = _macos_app(monkeypatch, PermissionOutcome.PENDING)

    body = TestClient(app).get("/api/settings/wake-word/mic-level").json()

    assert body["permission_required"] is True
    assert body["can_open_settings"] is True
    # An app that never asked is not in the Privacy pane yet: the hint points at the
    # test button (the just-in-time ask), not at a Settings row that does not exist.
    assert body["hint"] == "Press the test button and choose Allow in the macOS dialog."
    assert "System Settings" not in body["hint"]
    assert gate.ensure_calls() == []
    assert gate.native_free()


def test_macos_mic_level_measures_once_the_microphone_is_granted(monkeypatch):
    import jarvis.speech.diagnose as d

    async def fake_measure(duration_s=3.0):
        return -15.0

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app, gate = _macos_app(monkeypatch, PermissionOutcome.GRANTED)

    body = TestClient(app).get("/api/settings/wake-word/mic-level").json()

    assert body == {
        "max_dbfs": -15.0,
        "no_device": False,
        "too_quiet": False,
        "permission_required": False,
    }
    assert gate.ensure_calls() == []


def test_macos_mic_level_reports_a_grant_lost_during_the_measurement(monkeypatch):
    import jarvis.speech.diagnose as d
    from jarvis.audio.capture import MicrophoneAccessError

    async def revoked(duration_s=3.0):
        raise MicrophoneAccessError("revoked")

    monkeypatch.setattr(d, "measure_mic_dbfs", revoked)
    app, _gate = _macos_app(monkeypatch, PermissionOutcome.GRANTED)

    body = TestClient(app).get("/api/settings/wake-word/mic-level").json()

    assert body["permission_required"] is True
    assert body["max_dbfs"] == -120.0


def test_macos_wake_self_test_asks_at_the_button_and_never_measures_without_a_grant(monkeypatch):
    import jarvis.speech.diagnose as d

    calls = 0

    async def forbidden_measure(duration_s=3.0):
        nonlocal calls
        calls += 1
        return -15.0

    monkeypatch.setattr(d, "measure_mic_dbfs", forbidden_measure)
    app, gate = _macos_app(monkeypatch, PermissionOutcome.DENIED)

    response = TestClient(app).post("/api/settings/wake-word/self-test")

    assert response.status_code == 200
    body = response.json()
    assert body["permission_required"] is True
    assert body["permission"] == "microphone"
    assert body["can_open_settings"] is True
    assert body["no_device"] is False
    assert "System Settings > Privacy & Security > Microphone" in body["hint"]
    assert "Settings > Permissions" not in body["message"] + body["hint"]
    assert calls == 0
    # The test button IS the just-in-time moment: an interactive ask, waiting for the dialog.
    (call,) = gate.ensure_calls(PermissionId.MICROPHONE)
    assert call.method == "ensure_async"
    assert (call.feature, call.interactive, call.wait_s) == ("wake_word", True, 60.0)


def test_macos_wake_self_test_measures_after_the_dialog_is_allowed(monkeypatch):
    import jarvis.speech.diagnose as d

    async def fake_measure(duration_s=3.0):
        return -20.0

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    app, gate = _macos_app(monkeypatch, PermissionOutcome.GRANTED)

    body = TestClient(app).post("/api/settings/wake-word/self-test").json()

    assert body["permission_required"] is False
    assert body["mic_ok"] is True
    assert len(gate.ensure_calls(PermissionId.MICROPHONE)) == 1


def test_non_macos_routes_never_touch_the_permission_service(monkeypatch):
    import jarvis.speech.diagnose as d
    import jarvis.ui.web.settings_routes as settings_routes

    async def fake_measure(duration_s=3.0):
        return -20.0

    monkeypatch.setattr(d, "measure_mic_dbfs", fake_measure)
    monkeypatch.setattr(settings_routes.sys, "platform", "linux")
    gate = FakePermissionService()
    app = FastAPI()
    app.state.permission_service = gate
    app.include_router(router)

    body = TestClient(app).get("/api/settings/wake-word/mic-level").json()

    assert body["permission_required"] is False
    assert gate.calls == []
