"""Route contract tests for the system-permissions API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.platform.permissions import PermissionOperation
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.permissions_routes import router


def _snapshot() -> dict:
    return {
        "platform": "darwin",
        "supported": True,
        "headless": False,
        "app_identity": {
            "app_name": "Personal Jarvis",
            "expected_bundle_id": "com.personal-jarvis.desktop",
            "bundle_id": "com.personal-jarvis.desktop",
            "bundle_path": "/Applications/Personal Jarvis.app",
            "launched_as_bundle": True,
            "stable": True,
            "foreground": True,
        },
        "permissions": [],
        "features": {},
        "restart_required": False,
    }


class _Port:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, bool]] = []

    def snapshot(self) -> dict:
        return _snapshot()

    def request(self, permission_id, *, dry_run: bool = False):
        self.calls.append(("request", permission_id.value, dry_run))
        return PermissionOperation(
            not self.fail,
            permission_id.value,
            "request",
            not dry_run and not self.fail,
            dry_run,
            False,
            "failed" if self.fail else "requested",
            _snapshot(),
        )

    def open_settings(self, permission_id, *, dry_run: bool = False):
        self.calls.append(("open_settings", permission_id.value, dry_run))
        return PermissionOperation(
            True,
            permission_id.value,
            "open_settings",
            not dry_run,
            dry_run,
            False,
            "opened",
            _snapshot(),
        )


def _client(port: _Port) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.system_permission_port = port
    app.dependency_overrides[require_control_key_or_session] = lambda: None
    return TestClient(app)


def test_status_returns_uncached_port_snapshot() -> None:
    body = _client(_Port()).get("/api/permissions/status").json()

    assert body["platform"] == "darwin"
    assert body["app_identity"]["stable"] is True


def test_request_passes_stable_id_and_dry_run() -> None:
    port = _Port()

    response = _client(port).post("/api/permissions/screen_recording/request?dry_run=true")

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert port.calls == [("request", "screen_recording", True)]


def test_open_settings_passes_permission_id() -> None:
    port = _Port()

    response = _client(port).post("/api/permissions/microphone/open-settings")

    assert response.status_code == 200
    assert response.json()["action"] == "open_settings"
    assert port.calls == [("open_settings", "microphone", False)]


def test_failed_native_operation_is_conflict_with_full_payload() -> None:
    response = _client(_Port(fail=True)).post("/api/permissions/accessibility/request")

    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert response.json()["snapshot"]["platform"] == "darwin"


def test_unknown_permission_id_is_rejected() -> None:
    response = _client(_Port()).post("/api/permissions/camera/request")

    assert response.status_code == 422


def test_mutating_routes_are_marked_dangerous_in_openapi() -> None:
    app = FastAPI()
    app.include_router(router)

    paths = app.openapi()["paths"]

    assert paths["/api/permissions/{permission_id}/request"]["post"]["x-jarvis-dangerous"] is True
    assert (
        paths["/api/permissions/{permission_id}/open-settings"]["post"]["x-jarvis-dangerous"]
        is True
    )
